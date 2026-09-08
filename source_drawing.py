"""Bounded, read-only DXF linework for the original-drawing preview."""
from collections import Counter
from pathlib import Path
import math

import ezdxf

from dxf_parser import entity_to_record
from element_id import raw_entity_sig


MAX_PRIMITIVES = 50_000
MAX_POINTS = 500_000
MAX_INSERT_DEPTH = 8
MAX_ENTITIES = 100_000


class _BudgetExceeded(Exception):
    pass


def _finite(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError("Non-finite source coordinate")
    return value


def _round_point(p):
    return [round(_finite(p[0]), 3), round(_finite(p[1]), 3)]


def _record(entity, scale, point_budget=MAX_POINTS):
    """Return sampled source geometry; SPLINE deliberately bypasses parser control points."""
    if entity.dxftype() == "SPLINE":
        points = []
        for p in entity.flattening(0.5 / scale, segments=8):
            if len(points) >= point_budget:
                raise _BudgetExceeded()
            points.append(_round_point((p.x * scale, p.y * scale)))
        if len(points) < 2:
            return None
        return {"kind": "polyline", "closed": bool(entity.closed),
                "layer": getattr(entity.dxf, "layer", ""), "points": points}
    kind = entity.dxftype()
    # These parser shortcuts are unsuitable for an original-drawing backdrop.
    if kind == "POLYLINE":
        if not (entity.is_2d_polyline or entity.is_3d_polyline):
            return None
        if len(entity.vertices) > point_budget:
            raise _BudgetExceeded()
        if any(v.dxf.get("bulge", 0) for v in entity.vertices):
            return None
    if kind == "LWPOLYLINE":
        # Parser bulge sampling uses at most 128 segments per source vertex.
        if len(entity) * 129 > point_budget:
            raise _BudgetExceeded()
    if kind == "CIRCLE":
        normal = entity.dxf.extrusion
        if abs(normal.x) > 1e-9 or abs(normal.y) > 1e-9:
            return None
    rec = entity_to_record(entity, scale)
    if not rec:
        return None
    sigs = rec.pop("_sigs", None)
    rec["signature"] = sigs[0] if sigs else raw_entity_sig(rec)
    rec.pop("from_arc", None)
    rec.pop("arc_radius", None)
    if rec["kind"] == "polyline":
        rec["points"] = [_round_point(p) for p in rec["points"]]
    else:
        rec["center"] = _round_point(rec["center"])
        rec["radius"] = round(_finite(rec["radius"]), 3)
    if (len(rec.get("points", ())) or 2) > point_budget:
        raise _BudgetExceeded()
    return rec


def _entities(entity, depth=0, work=None, omitted=None):
    """Expand INSERT/MINSERT with ezdxf transforms and a hard recursion ceiling."""
    work = work if work is not None else [MAX_ENTITIES]
    omitted = omitted if omitted is not None else Counter()
    work[0] -= 1
    if work[0] < 0:
        raise _BudgetExceeded()
    if entity.dxftype() != "INSERT":
        yield entity
        return
    if depth >= MAX_INSERT_DEPTH:
        yield "INSERT_DEPTH"
        return
    try:
        inserts = entity.multi_insert() if int(getattr(entity, "mcount", 1) or 1) > 1 else [entity]
        for insert in inserts:
            work[0] -= 1
            if work[0] < 0:
                raise _BudgetExceeded()
            seen = False
            def skipped(e, reason):
                omitted[e.dxftype() + "_TRANSFORM"] += 1
                work[0] -= 1
                if work[0] < 0:
                    raise _BudgetExceeded()
            for virtual in insert.virtual_entities(skipped_entity_callback=skipped):
                seen = True
                yield from _entities(virtual, depth + 1, work, omitted)
            if not seen:
                omitted["INSERT_EMPTY"] += 1
    except _BudgetExceeded:
        raise
    except Exception:
        yield "INSERT"


def _bbox(primitives):
    coordinates = []
    for primitive in primitives:
        if primitive["kind"] == "circle":
            x, y = primitive["center"]
            r = primitive["radius"]
            coordinates.extend(((x - r, y - r), (x + r, y + r)))
        else:
            coordinates.extend(primitive["points"])
    if not coordinates:
        return None
    xs, ys = zip(*coordinates)
    return [round(min(xs), 3), round(min(ys), 3), round(max(xs), 3), round(max(ys), 3)]


def _floor(source, level):
    source_id = str(source.get("id", "main"))
    name = Path(str(source.get("path", ""))).name or source_id
    z = _finite(level.get("z", source.get("z", 0.0)) or 0.0)
    offset = level.get("offset", source.get("offset", [0, 0])) or [0, 0]
    dx, dy = _finite(offset[0]), _finite(offset[1])
    floor = {"id": source_id, "label": str(level.get("label", source.get("label", source_id))), "z": z,
             "offset": [dx, dy], "name": name, "primitives": [], "bbox": None,
             "warnings": [], "omitted": {}, "status": "unavailable"}
    try:
        doc = ezdxf.readfile(str(source["path"]))
    except Exception as exc:
        floor["warnings"].append(f"Could not read {name}: {type(exc).__name__}")
        return floor

    scale = 1000.0 if doc.header.get("$INSUNITS", 0) == 6 else 1.0
    omitted = Counter()
    point_count = 0
    primitive_index = 0
    work = [MAX_ENTITIES]
    try:
        for top in doc.modelspace():
            for entity in _entities(top, work=work, omitted=omitted):
                if len(floor["primitives"]) >= MAX_PRIMITIVES or point_count >= MAX_POINTS:
                    raise _BudgetExceeded()
                if isinstance(entity, str):
                    omitted[entity] += 1
                    continue
                try:
                    rec = _record(entity, scale, MAX_POINTS - point_count)
                    if rec is None:
                        omitted[entity.dxftype()] += 1
                        continue
                    count = len(rec.get("points", ())) or 2
                    if rec["kind"] == "polyline":
                        rec["points"] = [_round_point((p[0] + dx, p[1] + dy)) for p in rec["points"]]
                        rec["closed"] = bool(rec.get("closed", False))
                    else:
                        rec["center"] = _round_point((rec["center"][0] + dx, rec["center"][1] + dy))
                        rec["closed"] = True
                    rec["id"] = f"{source_id}:{primitive_index}"
                    primitive_index += 1
                    point_count += count
                    floor["primitives"].append(rec)
                except _BudgetExceeded:
                    raise
                except Exception:
                    omitted[entity.dxftype() + "_ERROR"] += 1
    except _BudgetExceeded:
        omitted["TRUNCATED"] += 1
    except Exception as exc:
        omitted["SOURCE_ERROR"] += 1
        floor["warnings"].append(f"Source extraction stopped: {type(exc).__name__}")

    floor["omitted"] = dict(sorted(omitted.items()))
    if omitted.get("TRUNCATED"):
        floor["warnings"].append(
            f"Preview limit reached ({MAX_PRIMITIVES} primitives/{MAX_POINTS} points); "
            "remaining source content is uncounted")
    if omitted and not floor["primitives"]:
        floor["warnings"].append("No supported source geometry was available")
    floor["bbox"] = _bbox(floor["primitives"])
    floor["status"] = "available" if floor["primitives"] and not omitted else "partial"
    return floor


def build_source_drawing(sources, geometry):
    """Build sanitized per-source 2D linework using resolved stack offsets when present."""
    levels = {str(level.get("id")): level
              for level in (geometry or {}).get("stack", {}).get("levels", [])}
    floors = []
    for source in sources or []:
        try:
            floors.append(_floor(source, levels.get(str(source.get("id", "main")), {})))
        except Exception as exc:
            floors.append({"id": str(source.get("id", "main")),
                           "label": str(source.get("label", source.get("id", "main"))),
                           "name": Path(str(source.get("path", ""))).name,
                           "z": 0, "offset": [0, 0], "primitives": [], "bbox": None,
                           "status": "unavailable", "omitted": {},
                           "warnings": [f"Source preview unavailable: {type(exc).__name__}"]})
    statuses = {floor["status"] for floor in floors}
    if floors and statuses == {"available"}:
        status = "available"
    elif not floors or statuses == {"unavailable"}:
        status = "unavailable"
    else:
        status = "partial"
    warnings = [f"[{floor['id']}] {warning}"
                for floor in floors for warning in floor["warnings"]]
    return {"schema_version": 1, "units": "mm", "status": status,
            "floors": floors, "warnings": warnings}
