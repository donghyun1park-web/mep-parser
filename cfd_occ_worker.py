"""FreeCADCmd worker for body-fitted CFD air-volume and named surfaces.

Run only through FreeCADCmd.  The normal Python process passes paths via:

    MEP_CFD_GEOMETRY=<geometry.v2.json>
    MEP_CFD_OCC_OUTPUT=<published output directory>

All geometry is modelled in millimetres.  STL vertices are converted to metres
exactly once while serialising the final multi-region ASCII STL.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sys
import uuid

import FreeCAD as App
import Part

from heat_source_contract import (
    HeatSourceContractError,
    assert_unique_positive_source_ids,
    normalize_confirmed_heat_source,
)


GEOMETRY_ENV = "MEP_CFD_GEOMETRY"
OUTPUT_ENV = "MEP_CFD_OCC_OUTPUT"
DEFLECTION_MM = 2.0
BOOLEAN_TOL_MM = 0.01
AREA_TOL_MM2 = 0.01
MM_TO_M = 0.001


class GeometryError(RuntimeError):
    pass


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clean_points(points):
    cleaned = []
    for point in points or []:
        current = [float(point[0]), float(point[1])]
        if not cleaned or math.dist(cleaned[-1], current) > BOOLEAN_TOL_MM:
            cleaned.append(current)
    if len(cleaned) > 2 and math.dist(cleaned[0], cleaned[-1]) <= BOOLEAN_TOL_MM:
        cleaned.pop()
    return cleaned


def _face_from_xy(points, z_mm):
    points = _clean_points(points)
    if len(points) < 3:
        raise GeometryError("Closed polygon requires at least three distinct points.")
    vectors = [App.Vector(point[0], point[1], z_mm) for point in points]
    wire = Part.makePolygon(vectors + [vectors[0]])
    face = Part.Face(wire)
    if face.isNull() or not face.isValid() or face.Area <= AREA_TOL_MM2:
        raise GeometryError("Polygon does not form a valid OCC face.")
    return face


def _record_height(rec, default_height=None):
    semantic = rec.get("semantic") or {}
    overrides = rec.get("overrides") or {}
    for value in (semantic.get("height_mm"), overrides.get("height"),
                  rec.get("height_mm"), default_height):
        if value not in (None, ""):
            height = float(value)
            if height > 0:
                return height
    raise GeometryError("Element %s has no positive height." % (rec.get("id") or "?"))


def _solid_from_record(rec, height_mm, z_mm=None):
    z_base = float(rec.get("z_base", rec.get("elevation", 0.0)) if z_mm is None else z_mm)
    if rec.get("kind") == "circle":
        center = rec.get("center") or []
        radius = float(rec.get("radius", 0.0) or 0.0)
        if len(center) < 2 or radius <= 0:
            raise GeometryError("Invalid circle for element %s." % (rec.get("id") or "?"))
        solid = Part.makeCylinder(radius, height_mm,
                                  App.Vector(float(center[0]), float(center[1]), z_base))
    elif rec.get("kind") == "polyline" and rec.get("closed"):
        solid = _face_from_xy(rec.get("points"), z_base).extrude(App.Vector(0, 0, height_mm))
    else:
        raise GeometryError("Element %s needs a closed footprint." % (rec.get("id") or "?"))
    if solid.isNull() or not solid.isValid() or solid.Volume <= 0:
        raise GeometryError("Element %s did not produce a valid solid." % (rec.get("id") or "?"))
    return solid


def _select_space(data):
    zones = [item for item in (data.get("elements") or {}).get("zone", [])
             if item.get("closed") and item.get("confirmed")]
    if len(zones) != 1:
        raise GeometryError("Exactly one confirmed closed zone is required; found %d." % len(zones))
    zone = zones[0]
    semantic = zone.get("semantic") or {}
    height = float(semantic.get("ceiling_height_mm", 0.0) or 0.0)
    if height <= 0:
        raise GeometryError("Confirmed zone requires semantic.ceiling_height_mm.")
    z_base = float(zone.get("z_base", 0.0) or 0.0)
    room = _solid_from_record(zone, height, z_base)
    solids = list(room.Solids)
    if len(solids) != 1:
        raise GeometryError("Zone extrusion must create exactly one solid.")
    return zone, room, z_base, z_base + height


def _obstacle_records(data, space_id):
    elements = data.get("elements") or {}
    for rec in elements.get("column") or []:
        if rec.get("space_id") and rec.get("space_id") != space_id:
            continue
        yield "column", rec
    for rec in elements.get("equipment") or []:
        if rec.get("space_id") and rec.get("space_id") != space_id:
            continue
        semantic = rec.get("semantic") or {}
        if semantic.get("kind") == "air_terminal":
            continue
        if not rec.get("confirmed"):
            raise GeometryError("Equipment %s is not confirmed." % (rec.get("id") or "?"))
        if semantic.get("role") not in ("solid", "heat_source"):
            raise GeometryError("Equipment %s must be solid or heat_source." % (rec.get("id") or "?"))
        yield "equipment", rec


def _build_air_volume(data, room, room_z0, room_z1, space_id):
    params = data.get("params") or {}
    obstacle_shapes = []
    cut_shapes = []
    for category, rec in _obstacle_records(data, space_id):
        default = ((params.get("column") or {}).get("height")
                   if category == "column" else None)
        height = _record_height(rec, default)
        shape = _solid_from_record(rec, height)
        common = room.common(shape)
        if common.isNull() or common.Volume <= BOOLEAN_TOL_MM ** 3:
            raise GeometryError("Obstacle %s is outside the confirmed room." % (rec.get("id") or "?"))
        if not common.isValid():
            raise GeometryError("Obstacle %s intersection is invalid." % (rec.get("id") or "?"))
        cut_shapes.append(common)
        semantic = rec.get("semantic") or {}
        role = semantic.get("role", category)
        heat_contract = {}
        if role == "heat_source":
            source_ref = dict(rec.get("source_ref") or {})
            try:
                heat_contract = normalize_confirmed_heat_source({
                    **semantic,
                    "source_id": rec.get("id"),
                    "source_label": (source_ref.get("block_name")
                                     or source_ref.get("layer")
                                     or rec.get("id")),
                    "source_type": semantic.get("source_type"),
                    "source_ref": source_ref,
                })
            except HeatSourceContractError as exc:
                raise GeometryError(
                    "Heat-source contract is invalid for %s: %s"
                    % (rec.get("id") or "?", exc)
                ) from exc
            if heat_contract["source_type"] != "user_confirmed":
                raise GeometryError(
                    "Heat-source %s must be user_confirmed before body-fitted CFD."
                    % (rec.get("id") or "?")
                )
            # This is not a different source type: it tells downstream
            # reviewers that a confirmed value/position overrode the original
            # DXF candidate while retaining that candidate's source_ref.
            if semantic.get("override_of_dxf") is True:
                heat_contract["override_of_dxf"] = True
        obstacle_shapes.append({
            "category": category,
            "element_id": rec.get("id"),
            "role": role,
            "heat_contract": heat_contract,
            "shape": common,
        })

    try:
        assert_unique_positive_source_ids([
            item["heat_contract"] for item in obstacle_shapes
            if item.get("heat_contract", {}).get("input_power_w", 0) > 0
        ])
    except HeatSourceContractError as exc:
        raise GeometryError("Heat-source identity contract is invalid: %s" % exc) from exc

    cutter = None
    if cut_shapes:
        cutter = cut_shapes[0]
        for shape in cut_shapes[1:]:
            cutter = cutter.fuse(shape)
        if not cutter.isValid():
            raise GeometryError("Fused obstacle shape is invalid.")
    air = room.cut(cutter) if cutter is not None else room
    if air.isNull() or not air.isValid() or air.Volume <= 0:
        raise GeometryError("Air-volume Boolean result is invalid.")
    solids = list(air.Solids)
    if len(solids) != 1:
        raise GeometryError("Air volume must contain exactly one solid; found %d." % len(solids))
    return air, obstacle_shapes


def _terminal_records(data, space_id):
    for rec in (data.get("elements") or {}).get("equipment") or []:
        if rec.get("space_id") and rec.get("space_id") != space_id:
            continue
        semantic = rec.get("semantic") or {}
        if semantic.get("kind") == "air_terminal":
            yield rec


def _centroid_2d(rec):
    if rec.get("kind") == "circle":
        return [float(rec["center"][0]), float(rec["center"][1])]
    points = _clean_points(rec.get("points"))
    if not points:
        raise GeometryError("Terminal %s has no 2D location." % (rec.get("id") or "?"))
    return [sum(item[0] for item in points) / len(points),
            sum(item[1] for item in points) / len(points)]


def _terminal_size(rec):
    semantic = rec.get("semantic") or {}
    diameter = semantic.get("diameter_mm")
    if diameter not in (None, ""):
        diameter = float(diameter)
        if diameter > 0:
            return diameter, diameter, True
    width = semantic.get("width_mm")
    height = semantic.get("height_mm")
    if width not in (None, "") and height not in (None, ""):
        width, height = float(width), float(height)
        if width > 0 and height > 0:
            return width, height, False
    if rec.get("kind") == "circle" and float(rec.get("radius", 0) or 0) > 0:
        diameter = 2.0 * float(rec["radius"])
        return diameter, diameter, True
    points = _clean_points(rec.get("points"))
    if points:
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        width, height = max(xs) - min(xs), max(ys) - min(ys)
        if width > 0 and height > 0:
            return width, height, False
    raise GeometryError("Terminal %s has no positive size." % (rec.get("id") or "?"))


def _circle_face(center, radius, normal):
    edge = Part.makeCircle(radius, center, normal)
    return Part.Face(Part.Wire([edge]))


def _rect_face(center, width, height, u_axis, v_axis):
    u = u_axis.multiply(width / 2.0)
    v = v_axis.multiply(height / 2.0)
    points = [center - u - v, center + u - v, center + u + v, center - u + v]
    return Part.Face(Part.makePolygon(points + [points[0]]))


def _terminal_face(rec, room_z0, room_z1):
    semantic = rec.get("semantic") or {}
    host = str(semantic.get("host_surface") or "").lower().replace("wall:", "")
    xy = _centroid_2d(rec)
    width, height, circular = _terminal_size(rec)
    if host in ("ceiling", "floor"):
        z = room_z1 if host == "ceiling" else room_z0
        if rec.get("kind") == "polyline" and rec.get("closed") and not circular:
            return _face_from_xy(rec.get("points"), z), host
        center = App.Vector(xy[0], xy[1], z)
        if circular:
            return _circle_face(center, width / 2.0, App.Vector(0, 0, 1)), host
        return _rect_face(center, width, height, App.Vector(1, 0, 0), App.Vector(0, 1, 0)), host

    elevation = semantic.get("center_z_mm", rec.get("elevation"))
    if elevation in (None, ""):
        raise GeometryError("Wall terminal %s requires semantic.center_z_mm." % (rec.get("id") or "?"))
    z = float(elevation)
    if not (room_z0 < z < room_z1):
        raise GeometryError("Wall terminal %s elevation is outside the room." % (rec.get("id") or "?"))
    if host in ("x0", "xl"):
        center = App.Vector(xy[0], xy[1], z)
        normal = App.Vector(1, 0, 0)
        u_axis = App.Vector(0, 1, 0)
    elif host in ("y0", "yw"):
        center = App.Vector(xy[0], xy[1], z)
        normal = App.Vector(0, 1, 0)
        u_axis = App.Vector(1, 0, 0)
    else:
        raise GeometryError("Unsupported terminal host_surface: %s" % host)
    if circular:
        return _circle_face(center, width / 2.0, normal), host
    return _rect_face(center, width, height, u_axis, App.Vector(0, 0, 1)), host


def _faces(shape):
    return [face for face in shape.Faces if face.Area > AREA_TOL_MM2]


def _belongs_to_obstacle(face, obstacles):
    center = face.CenterOfMass
    for obstacle in obstacles:
        try:
            if obstacle["shape"].isInside(center, BOOLEAN_TOL_MM, True):
                return obstacle
        except Exception:
            pass
    return None


def _partition_boundary(data, air, obstacles, room_z0, room_z1, space_id):
    regions = {"wall": {"role": "wall", "element_ids": [], "faces": []}}
    for obstacle in obstacles:
        name = "equipment_%s" % obstacle["element_id"]
        heat_contract = dict(obstacle.get("heat_contract") or {})
        regions[name] = {
            "role": obstacle["role"],
            "element_ids": [obstacle["element_id"]],
            "power_kw": heat_contract.get("power_kw"),
            "input_power_w": heat_contract.get("input_power_w"),
            "convective_fraction": heat_contract.get("convective_fraction"),
            "radiative_fraction": heat_contract.get("radiative_fraction"),
            "convective_power_w": heat_contract.get("convective_power_w"),
            "radiative_power_w": heat_contract.get("radiative_power_w"),
            "excluded_radiative_power_w": heat_contract.get(
                "excluded_radiative_power_w"
            ),
            # Keep the reviewed DXF/source identity alongside the numerical
            # heat split.  The resulting surface manifest is the hand-off to
            # meshing, thermal physics, and GCI, so reducing this to a
            # generated ``equipment_<id>`` patch name would break the CAD
            # traceability promised at the confirmation screen.
            "source_id": heat_contract.get("source_id"),
            "source_label": heat_contract.get("source_label"),
            "source_ref": heat_contract.get("source_ref"),
            "override_of_dxf": heat_contract.get("override_of_dxf"),
            "evidence": heat_contract.get("evidence"),
            "source_type": heat_contract.get("source_type"),
            "faces": [],
        }
    for face in air.Faces:
        obstacle = _belongs_to_obstacle(face, obstacles)
        if obstacle:
            regions["equipment_%s" % obstacle["element_id"]]["faces"].append(face)
        else:
            regions["wall"]["faces"].append(face)

    for terminal in sorted(_terminal_records(data, space_id), key=lambda rec: rec.get("id", "")):
        semantic = terminal.get("semantic") or {}
        role = semantic.get("role")
        if role not in ("supply", "exhaust") or not terminal.get("confirmed"):
            raise GeometryError("Terminal %s is not fully confirmed." % (terminal.get("id") or "?"))
        target, host = _terminal_face(terminal, room_z0, room_z1)
        expected_area = target.Area
        patch_faces = []
        remaining_wall = []
        for wall_face in regions["wall"]["faces"]:
            common = wall_face.common(target)
            if common.isNull() or common.Area <= AREA_TOL_MM2:
                remaining_wall.append(wall_face)
                continue
            patch_faces.extend(_faces(common))
            remainder = wall_face.cut(common)
            remaining_wall.extend(_faces(remainder))
        actual_area = sum(face.Area for face in patch_faces)
        if actual_area <= AREA_TOL_MM2:
            raise GeometryError("Terminal %s does not intersect the room boundary." % terminal.get("id"))
        if abs(actual_area - expected_area) / expected_area > 0.02:
            raise GeometryError("Terminal %s is clipped or overlaps another patch." % terminal.get("id"))
        regions["wall"]["faces"] = remaining_wall
        name = "%s_%s" % (role, terminal["id"])
        regions[name] = {
            "role": role,
            "host_surface": host,
            "element_ids": [terminal["id"]],
            "airflow_cmh": float(semantic["airflow_cmh"]),
            "design_normal": [float(value) for value in semantic.get("normal", [])],
            "expected_area_mm2": expected_area,
            "faces": patch_faces,
        }
    return regions


def _triangle_normal(a, b, c):
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length <= 1e-15:
        raise GeometryError("Degenerate tessellation triangle.")
    return nx / length, ny / length, nz / length


def _triangulate_face(face):
    vertices, facets = face.tessellate(DEFLECTION_MM)
    triangles = []
    for indices in facets:
        points = []
        for index in indices:
            vertex = vertices[index]
            points.append((float(vertex.x) * MM_TO_M,
                           float(vertex.y) * MM_TO_M,
                           float(vertex.z) * MM_TO_M))
        if len(points) == 3:
            _triangle_normal(*points)
            triangles.append(tuple(points))
    return triangles


def _triangle_key(triangle):
    return tuple(sorted(tuple(round(value, 9) for value in point) for point in triangle))


def _region_triangles(regions):
    output = {}
    for name in sorted(regions):
        triangles = []
        for face in regions[name]["faces"]:
            triangles.extend(_triangulate_face(face))
        triangles.sort(key=_triangle_key)
        if not triangles:
            raise GeometryError("Region %s has no triangles." % name)
        output[name] = triangles
    return output


def _edge_diagnostics(region_triangles):
    counts = {}
    duplicate_triangles = 0
    seen_triangles = set()
    for triangles in region_triangles.values():
        for triangle in triangles:
            tri_key = _triangle_key(triangle)
            if tri_key in seen_triangles:
                duplicate_triangles += 1
            seen_triangles.add(tri_key)
            points = [tuple(round(value, 8) for value in point) for point in triangle]
            for a, b in ((points[0], points[1]), (points[1], points[2]), (points[2], points[0])):
                key = tuple(sorted((a, b)))
                counts[key] = counts.get(key, 0) + 1
    open_edges = sum(1 for count in counts.values() if count == 1)
    non_manifold_edges = sum(1 for count in counts.values() if count > 2)
    return {
        "open_edges": open_edges,
        "non_manifold_edges": non_manifold_edges,
        "duplicate_triangles": duplicate_triangles,
        "watertight": open_edges == 0 and non_manifold_edges == 0,
    }


def _normalised_hash(triangles):
    canonical = []
    for triangle in triangles:
        canonical.append(";".join(
            ",".join("%.9f" % value for value in point)
            for point in sorted(triangle)
        ))
    return hashlib.sha256("\n".join(sorted(canonical)).encode("ascii")).hexdigest()


def _write_ascii_stl(path, region_triangles):
    with open(path, "w", encoding="ascii", newline="\n") as handle:
        for name in sorted(region_triangles):
            handle.write("solid %s\n" % name)
            for triangle in region_triangles[name]:
                normal = _triangle_normal(*triangle)
                handle.write("  facet normal %.9g %.9g %.9g\n" % normal)
                handle.write("    outer loop\n")
                for point in triangle:
                    handle.write("      vertex %.9g %.9g %.9g\n" % point)
                handle.write("    endloop\n  endfacet\n")
            handle.write("endsolid %s\n" % name)


def _aabb(triangles):
    points = [point for triangle in triangles for point in triangle]
    return {
        "min_m": [min(point[axis] for point in points) for axis in range(3)],
        "max_m": [max(point[axis] for point in points) for axis in range(3)],
    }


def _representative_normal(triangles):
    weighted = [0.0, 0.0, 0.0]
    for triangle in triangles:
        normal = _triangle_normal(*triangle)
        a, b, c = triangle
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        area2 = math.sqrt((uy*vz-uz*vy)**2 + (uz*vx-ux*vz)**2 + (ux*vy-uy*vx)**2)
        for axis in range(3):
            weighted[axis] += normal[axis] * area2
    length = math.sqrt(sum(value * value for value in weighted))
    if length <= 1e-12:
        return None
    return [round(value / length, 9) for value in weighted]


def _location_in_mesh(air):
    box = air.BoundBox
    fractions = (0.5, 0.35, 0.65, 0.2, 0.8)
    boundary = Part.makeCompound(list(air.Faces))
    candidates = []
    for fx in fractions:
        for fy in fractions:
            for fz in fractions:
                point = App.Vector(box.XMin + box.XLength * fx,
                                   box.YMin + box.YLength * fy,
                                   box.ZMin + box.ZLength * fz)
                if not air.isInside(point, BOOLEAN_TOL_MM, False):
                    continue
                distance = boundary.distToShape(Part.Vertex(point))[0]
                if distance > BOOLEAN_TOL_MM:
                    candidates.append((distance, point))
    if not candidates:
        raise GeometryError("Could not find a locationInMesh point inside the air solid.")
    candidates.sort(key=lambda item: (-item[0], item[1].x, item[1].y, item[1].z))
    distance, point = candidates[0]
    return {
        "point_m": [point.x * MM_TO_M, point.y * MM_TO_M, point.z * MM_TO_M],
        "boundary_clearance_m": distance * MM_TO_M,
        "verified_by_occ_isInside": True,
    }


def _tool_versions():
    version = App.Version()
    return {
        "freecad": ".".join(str(value) for value in version[:3]),
        "freecad_revision": str(version[3]) if len(version) > 3 else "",
        "occ": str(Part.OCC_VERSION),
        "python": sys.version.split()[0],
    }


def _region_manifest(regions, region_triangles):
    result = []
    for name in sorted(regions):
        faces = regions[name]["faces"]
        triangles = region_triangles[name]
        item = {
            "name": name,
            "role": regions[name]["role"],
            "source_element_ids": regions[name].get("element_ids", []),
            "area_m2": sum(face.Area for face in faces) * 1e-6,
            "representative_normal": _representative_normal(triangles),
            "aabb": _aabb(triangles),
            "triangle_count": len(triangles),
            "normalized_triangle_hash": _normalised_hash(triangles),
        }
        if regions[name].get("host_surface"):
            item["host_surface"] = regions[name]["host_surface"]
        if regions[name].get("airflow_cmh") is not None:
            item["airflow_cmh"] = regions[name]["airflow_cmh"]
        if regions[name].get("power_kw") is not None:
            item["power_kw"] = float(regions[name]["power_kw"])
        if regions[name].get("input_power_w") is not None:
            item["input_power_w"] = float(regions[name]["input_power_w"])
        if regions[name].get("convective_fraction") is not None:
            item["convective_fraction"] = float(
                regions[name]["convective_fraction"]
            )
        if regions[name].get("radiative_fraction") is not None:
            item["radiative_fraction"] = float(
                regions[name]["radiative_fraction"]
            )
        if regions[name].get("convective_power_w") is not None:
            item["convective_power_w"] = float(
                regions[name]["convective_power_w"]
            )
        if regions[name].get("radiative_power_w") is not None:
            item["radiative_power_w"] = float(
                regions[name]["radiative_power_w"]
            )
        if regions[name].get("excluded_radiative_power_w") is not None:
            item["excluded_radiative_power_w"] = float(
                regions[name]["excluded_radiative_power_w"]
            )
        if regions[name].get("source_id") not in (None, ""):
            item["source_id"] = str(regions[name]["source_id"])
        if regions[name].get("source_label") not in (None, ""):
            item["source_label"] = str(regions[name]["source_label"])
        if regions[name].get("source_ref") not in (None, ""):
            item["source_ref"] = regions[name]["source_ref"]
        if regions[name].get("override_of_dxf") is True:
            item["override_of_dxf"] = True
        if regions[name].get("evidence") not in (None, ""):
            item["evidence"] = str(regions[name]["evidence"])
        if regions[name].get("source_type") not in (None, ""):
            item["source_type"] = str(regions[name]["source_type"])
        if regions[name].get("design_normal"):
            item["design_normal"] = regions[name]["design_normal"]
        if regions[name].get("expected_area_mm2"):
            item["expected_area_m2"] = regions[name]["expected_area_mm2"] * 1e-6
            item["area_error_ratio"] = abs(
                item["area_m2"] - item["expected_area_m2"]
            ) / item["expected_area_m2"]
        result.append(item)
    return result


def _write_document(path, air, regions):
    doc = App.newDocument("MEP_CFD_AirVolume")
    air_obj = doc.addObject("Part::Feature", "AirVolume")
    air_obj.Label = "Validated air volume"
    air_obj.Shape = air
    for name in sorted(regions):
        obj = doc.addObject("Part::Feature", "Surface_%s" % name.replace("-", "_"))
        obj.Label = name
        obj.Shape = Part.makeCompound(regions[name]["faces"])
    doc.recompute()
    doc.saveAs(path)
    App.closeDocument(doc.Name)


def _publish(staging, output_dir):
    output_dir = os.path.abspath(output_dir)
    backup = output_dir + ".backup." + uuid.uuid4().hex
    if os.path.exists(output_dir):
        os.replace(output_dir, backup)
    try:
        os.replace(staging, output_dir)
    except Exception:
        if os.path.exists(backup):
            os.replace(backup, output_dir)
        raise
    if os.path.exists(backup):
        shutil.rmtree(backup, ignore_errors=True)


def build(geometry_path, output_dir):
    geometry_path = os.path.abspath(geometry_path)
    output_dir = os.path.abspath(output_dir)
    with open(geometry_path, encoding="utf-8") as handle:
        data = json.load(handle)
    if data.get("contract") != "geometry.v2" or data.get("schema_version") != 2:
        raise GeometryError("cfd_occ_worker requires geometry.v2 input.")
    if (data.get("review") or {}).get("blocking"):
        raise GeometryError("geometry.v2 has unresolved body-fitted review blockers.")

    parent = os.path.dirname(output_dir)
    os.makedirs(parent, exist_ok=True)
    staging = output_dir + ".staging." + uuid.uuid4().hex
    os.makedirs(staging)
    try:
        zone, room, room_z0, room_z1 = _select_space(data)
        air, obstacles = _build_air_volume(data, room, room_z0, room_z1, zone.get("id"))
        regions = _partition_boundary(data, air, obstacles, room_z0, room_z1, zone.get("id"))
        region_triangles = _region_triangles(regions)
        topology = _edge_diagnostics(region_triangles)
        if not topology["watertight"] or topology["duplicate_triangles"]:
            raise GeometryError("Tessellated boundary is not watertight/manifold: %s" % topology)

        boundary_area = air.Area * 1e-6
        region_area = sum(face.Area for value in regions.values() for face in value["faces"]) * 1e-6
        area_error = abs(region_area - boundary_area) / boundary_area
        if area_error > 0.001:
            raise GeometryError("Named surface area does not match the air boundary.")

        stl_path = os.path.join(staging, "air_volume_regions.stl")
        _write_ascii_stl(stl_path, region_triangles)
        brep_path = os.path.join(staging, "air_volume.brep")
        air.exportBrep(brep_path)
        fcstd_path = os.path.join(staging, "air_volume.FCStd")
        _write_document(fcstd_path, air, regions)

        manifest = {
            "schema_version": 1,
            "contract": "surface_manifest.v1",
            "engine": "body_fitted_airflow",
            "source": {
                "geometry_path": data.get("occ_source_path") or geometry_path,
                "geometry_sha256": _sha256_file(geometry_path),
                "geometry_contract": data.get("contract"),
                "space_element_id": zone.get("id"),
            },
            "tools": _tool_versions(),
            "transform": {
                "occ_units": "mm",
                "stl_units": "m",
                "scale": MM_TO_M,
                "origin_mm": (data.get("coordinate_system") or {}).get("origin_mm", [0, 0, 0]),
                "rotation_deg": (data.get("coordinate_system") or {}).get("rotation_deg", 0.0),
                "inverse": {"scale": 1000.0, "rotation_deg": 0.0, "translation_mm": [0, 0, 0]},
            },
            "tessellation": {
                "algorithm": "TopoShape.tessellate",
                "linear_deflection_mm": DEFLECTION_MM,
            },
            "air_volume": {
                "valid": bool(air.isValid()),
                "solid_count": len(air.Solids),
                "volume_m3": air.Volume * 1e-9,
                "boundary_area_m2": boundary_area,
                "region_area_m2": region_area,
                "area_error_ratio": area_error,
                "obstacle_count": len(obstacles),
                "location_in_mesh": _location_in_mesh(air),
            },
            "regions": _region_manifest(regions, region_triangles),
            "topology": topology,
            "outputs": {
                "multi_region_stl": "air_volume_regions.stl",
                "brep": "air_volume.brep",
                "freecad_document": "air_volume.FCStd",
            },
        }
        manifest["surface_hash"] = _normalised_hash(
            [triangle for name in sorted(region_triangles) for triangle in region_triangles[name]]
        )
        manifest_path = os.path.join(staging, "surface_manifest.json")
        with open(manifest_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        manifest["outputs"]["stl_sha256"] = _sha256_file(stl_path)
        manifest["outputs"]["brep_sha256"] = _sha256_file(brep_path)
        with open(manifest_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        _publish(staging, output_dir)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main():
    geometry_path = os.environ.get(GEOMETRY_ENV)
    output_dir = os.environ.get(OUTPUT_ENV)
    if not geometry_path or not output_dir:
        raise SystemExit("MEP_CFD_GEOMETRY and MEP_CFD_OCC_OUTPUT are required")
    manifest = build(geometry_path, output_dir)
    print("MEP_CFD_OCC_RESULT:" + json.dumps({
        "ok": True,
        "output": os.path.abspath(output_dir),
        "volume_m3": manifest["air_volume"]["volume_m3"],
        "regions": len(manifest["regions"]),
        "surface_hash": manifest["surface_hash"],
    }, ensure_ascii=False, sort_keys=True))


if os.environ.get(GEOMETRY_ENV) and os.environ.get(OUTPUT_ENV):
    main()
