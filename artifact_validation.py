"""Current-run receipts and independent IFC round-trip validation."""
import hashlib
import json
import math
import os
import uuid

import geom_contract as GC

MODELED = tuple(GC.Z_DATUM)


def input_hash(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def provenance(data):
    project = data.get("project") or {}
    return {"run_id": uuid.uuid4().hex, "input_sha256": input_hash(data),
            "project_id": project.get("project_id"), "revision": project.get("revision")}


def records(data):
    for cat in MODELED:
        for index, original in enumerate((data.get("elements") or {}).get(cat) or []):
            rec = dict(original)
            # Legacy geometry files can still be built without mutating the input file.
            if not rec.get("eid"):
                rec["eid"] = cat + ":legacy:" + input_hash({"record": original, "index": index})[:20]
            yield cat, index, rec


def prepare_records(data):
    for cat, index, rec in records(data):
        data["elements"][cat][index] = rec


def source_eids(rec):
    return sorted(set(rec.get("_artifact_source_eids") or [rec["eid"]]))


def qa_values(rec, prov):
    return {"EID": rec["eid"], "SourceEIDs": json.dumps(source_eids(rec), ensure_ascii=False),
            "NeedsReview": bool(rec.get("needs_review")),
            "ReviewReason": str(rec.get("review_reason") or rec.get("schedule_match") or ""),
            "Layer": str(rec.get("layer") or ""), "Level": str(rec.get("level") or ""),
            "RunId": prov["run_id"], "InputSHA256": prov["input_sha256"],
            "ProjectId": str(prov.get("project_id") or ""),
            "Revision": str(prov.get("revision") if prov.get("revision") is not None else "")}


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_receipt(path, prov, status="verified"):
    return {"status": status, "path": os.path.abspath(path), "sha256": file_hash(path),
            "size": os.path.getsize(path), "provenance": dict(prov)}


def mesh_bounds(verts):
    return [min(verts[i::3])*1000 for i in range(3)] + [max(verts[i::3])*1000 for i in range(3)]


def ifc_product_bounds(product):
    import ifcopenshell.geom
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    shape = ifcopenshell.geom.create_shape(settings, product)
    return mesh_bounds(shape.geometry.verts)


def inspect_ifc(data, stats, path):
    """Return errors and EID->GlobalId lists, reading geometry and relations from disk.

    No counts threshold: chained members share a GlobalId and split members have
    several GlobalIds. Every modeled record must be covered exactly by its own
    declared memberships; expected_products binds each split piece to its sources.
    """
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.util.element
    import ifcopenshell.util.unit

    errors, mapping = [], {}
    model = ifcopenshell.open(path)
    prov = stats.get("provenance") or {}
    expected = {r["eid"]: (c, r) for c, _, r in records(data) if c != "opening"}
    if len(expected) != sum(1 for c, _, _ in records(data) if c != "opening"):
        errors.append("Duplicate input EIDs")
    leaf_eids = {opening["eid"] for opening in stats.get("opening_results") or [] if opening.get("leaf_built")}
    expected.update({rec["eid"]: (cat, rec) for cat, _, rec in records(data) if cat == "opening" and rec["eid"] in leaf_eids})
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    scale = ifcopenshell.util.unit.calculate_unit_scale(model)
    products = []
    expected_products = {p["name"]: p for p in stats.get("expected_products") or []}
    stats["ifc_validation"] = {"volume_relative_tolerance": 0.005, "volume_absolute_tolerance_mm3": 1.0,
                               "bounds_tolerance_mm": 1.0,
                               "geometry_engine": "ifcopenshell", "shape_count": 0, "volumes": []}
    for product in model.by_type("IfcProduct"):
        pset = ifcopenshell.util.element.get_pset(product, "Pset_MEPParser")
        if not pset:
            if product.is_a("IfcElement") and not product.is_a("IfcOpeningElement"):
                errors.append(f"{product.GlobalId}: missing Pset_MEPParser")
            continue
        gid = product.GlobalId
        try:
            members = json.loads(pset["SourceEIDs"])
            if not isinstance(members, list) or not members or any(not isinstance(x, str) for x in members):
                raise ValueError("SourceEIDs must be a nonempty JSON string list")
            if pset.get("EID") not in members:
                raise ValueError("representative EID is not in SourceEIDs")
            props = next(rel.RelatingPropertyDefinition for rel in product.IsDefinedBy
                         if rel.is_a("IfcRelDefinesByProperties") and rel.RelatingPropertyDefinition.Name == "Pset_MEPParser")
            for prop in props.HasProperties:
                if prop.Name in ("EID", "SourceEIDs") and not prop.NominalValue.is_a("IfcText"):
                    errors.append(f"{gid}: {prop.Name} must be IfcText")
        except Exception as exc:
            errors.append(f"{gid}: invalid identity properties: {exc}")
            continue
        for key, val in (("RunId", prov.get("run_id")), ("InputSHA256", prov.get("input_sha256"))):
            if not val or pset.get(key) != val:
                errors.append(f"{gid}: stale or missing {key}")
        if "NeedsReview" not in pset:
            errors.append(f"{gid}: missing NeedsReview")
        elif bool(pset["NeedsReview"]) != any(expected[eid][1].get("needs_review") for eid in members if eid in expected):
            errors.append(f"{gid}: NeedsReview differs from source members")
        declared_qa = expected_products.get(product.Name, {}).get("qa")
        if declared_qa is None:
            representative = expected.get(pset.get("EID"))
            if representative:
                rec = dict(representative[1], _artifact_source_eids=members,
                           needs_review=any(expected[eid][1].get("needs_review") for eid in members if eid in expected))
                declared_qa = qa_values(rec, prov)
        for key, value in (declared_qa or {}).items():
            if pset.get(key, "") != value:
                errors.append(f"{gid}: QA property {key} differs from expected source/run value")
        products.append({"name": product.Name, "global_id": gid, "source_eids": sorted(members)})
        for eid in members:
            if eid not in expected:
                errors.append(f"{gid}: unknown source EID {eid}")
            mapping.setdefault(eid, []).append(gid)
        try:
            shape = ifcopenshell.geom.create_shape(settings, product)
            verts = shape.geometry.verts
            faces = shape.geometry.faces
            if not verts or not faces or not all(math.isfinite(v) for v in verts):
                raise ValueError("empty or nonfinite mesh")
            bounds = mesh_bounds(verts)
            expected_bounds = expected_products.get(product.Name, {}).get("bbox_mm")
            if expected_bounds is None or len(expected_bounds) != 6 or any(abs(a-b) > 1.0 for a, b in zip(bounds, expected_bounds)):
                raise ValueError(f"world bounds {bounds} differ from expected {expected_bounds} mm")
            # Closed solid tessellation: every undirected edge occurs twice.
            edges = {}
            for i in range(0, len(faces), 3):
                tri = faces[i:i+3]
                for a, b in zip(tri, tri[1:] + tri[:1]):
                    key = tuple(sorted((a, b)))
                    edges[key] = edges.get(key, 0) + 1
            if any(n != 2 for n in edges.values()):
                raise ValueError("non-closed or non-manifold mesh")
            expected_volume = expected_products.get(product.Name, {}).get("volume_mm3")
            if expected_volume is not None:
                volume = 0.0
                for i in range(0, len(faces), 3):
                    a, b, c = [verts[3*j:3*j+3] for j in faces[i:i+3]]
                    volume += a[0]*(b[1]*c[2]-b[2]*c[1]) + a[1]*(b[2]*c[0]-b[0]*c[2]) + a[2]*(b[0]*c[1]-b[1]*c[0])
                volume = abs(volume / 6) * 1e9
                removed = [cut["removed_volume_mm3"] for opening in stats.get("opening_results") or []
                           for cut in opening.get("cuts") or [] if cut.get("host_name") == product.Name]
                tolerance = max(1.0, min([expected_volume] + removed) * 0.005)
                if expected_volume <= 0 or abs(volume-expected_volume) > tolerance:
                    raise ValueError(f"exported volume {volume:g} differs from built/cut volume {expected_volume:g} mm3")
                stats["ifc_validation"]["volumes"].append({"global_id": gid, "name": product.Name,
                    "built_mm3": expected_volume, "exported_mm3": volume, "tolerance_mm3": tolerance})
            stats["ifc_validation"]["shape_count"] += 1
        except Exception as exc:
            errors.append(f"{gid}: shape verification failed: {exc}")
        containers = [rel.RelatingStructure for rel in getattr(product, "ContainedInStructure", ())]
        if product.is_a("IfcSpace"):
            containers += [rel.RelatingObject for rel in getattr(product, "Decomposes", ())]
        containers = [c for c in containers if c.is_a("IfcBuildingStorey")]
        if len(containers) != 1:
            errors.append(f"{gid}: expected exactly one storey, got {len(containers)}")
        elif data.get("floors"):
            for eid in members:
                if eid not in expected:
                    continue
                cat, rec = expected[eid]
                levels = data["floors"]
                z = GC.base_z(cat, rec)
                if rec.get("level"):
                    matches = [f for f in levels if rec["level"] in (f.get("id"), f.get("label"), f.get("storey"))]
                    wanted = matches[0] if len(matches) == 1 else None
                elif cat in ("pipe", "duct", "tray", "equipment"):
                    below = [f for f in levels if float(f.get("z", 0)) <= z + 100]
                    wanted = max(below, key=lambda f: f.get("z", 0)) if below else min(levels, key=lambda f: abs(f.get("z", 0)-z))
                else:
                    matches = [f for f in levels if abs(float(f.get("z", 0))-z) < 100]
                    wanted = matches[0] if len(matches) == 1 else None
                actual_z = float(containers[0].Elevation or 0) * scale * 1000
                expected_name = (wanted or {}).get("label") or (wanted or {}).get("storey")
                if wanted is None or abs(actual_z-float(wanted.get("z", 0))) > 1 or (expected_name and containers[0].Name != expected_name):
                    errors.append(f"{gid}: wrong storey for {eid}")
    for eid in sorted(expected.keys() - mapping.keys()):
        errors.append(f"Missing exported EID: {eid}")
    manifest = stats.get("expected_products")
    if manifest is not None:
        actual = {p["name"]: p["source_eids"] for p in products}
        wanted = {p["name"]: sorted(p["source_eids"]) for p in manifest}
        if actual != wanted or len(actual) != len(products):
            errors.append("Exported product/source membership differs from build manifest")
    for opening in stats.get("opening_results") or []:
        # Openings split three ways, exactly as V106 does. An opening the parser
        # never attached to a wall is a drawing/link quality question, not a
        # failed export -- reporting it here failed the whole building, and a
        # partial miss (some hosts cut, some not) is V106's warning to raise.
        if not (opening.get("requested_hosts") or []):
            continue
        hosts = opening.get("cut_host_eids") or []
        if not hosts:
            errors.append(f"Opening {opening.get('eid')}: host cut failed")
            continue
        gids = sorted({gid for eid in hosts for gid in mapping.get(eid, [])})
        if not gids:
            errors.append(f"Opening {opening.get('eid')}: no exported host")
        mapping[opening.get("eid")] = sorted(set(mapping.get(opening.get("eid"), []) + gids))
    # Every input opening must appear in the build receipt. Appearing in it with
    # no host is a link question that V106 reports; being absent altogether means
    # the builder dropped the opening without saying so.
    reported = {o.get("eid") for o in (stats.get("opening_results") or [])}
    for _, _, opening in (r for r in records(data) if r[0] == "opening"):
        if opening["eid"] not in reported:
            errors.append(f"Opening {opening['eid']}: missing cut receipt")
    return errors, {k: sorted(set(v)) for k, v in mapping.items()}
