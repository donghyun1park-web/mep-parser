"""Backward-compatible geometry.v2 contract and readiness validation.

The existing ``elements`` layout is intentionally retained so the screening
engine and FreeCAD BIM builder can continue to consume the same file.  Version
2 adds stable identities, source provenance and explicit semantic review data
needed by the body-fitted CFD pipeline.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
from typing import Any

from heat_source_contract import (
    HeatSourceContractError,
    assert_unique_positive_source_ids,
    normalize_confirmed_heat_source,
)


SCHEMA_VERSION = 2
CONTRACT = "geometry.v2"
CATEGORIES = (
    "wall", "column", "slab", "zone", "opening",
    "pipe", "duct", "tray", "equipment",
)
_TERMINAL_RE = re.compile(
    r"DIFF|DIFFUSER|GRILLE|REGISTER|SUPPLY|EXHAUST|SA[-_ ]|EA[-_ ]|디퓨저|그릴|급기구|배기구",
    re.IGNORECASE,
)
_SUPPLY_RE = re.compile(r"SUPPLY|SA(?:[-_ ]|$)|급기", re.IGNORECASE)
_EXHAUST_RE = re.compile(r"EXHAUST|EA(?:[-_ ]|$)|RETURN|RA(?:[-_ ]|$)|배기|환기", re.IGNORECASE)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _digest(prefix: str, value: Any, length: int = 16) -> str:
    raw = _canonical(value).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(raw).hexdigest()[:length]}"


def _source_handles(rec: dict[str, Any]) -> list[str]:
    def valid_handle_tokens(value: Any) -> list[str]:
        """Keep only scalar CAD tokens; JSON objects are never handles."""
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            return [str(value)]
        if isinstance(value, (list, tuple, set, frozenset)):
            return [str(item) for item in value
                    if isinstance(item, (str, int)) and not isinstance(item, bool)]
        return []

    handles = valid_handle_tokens(rec.get("source_handles"))
    handle = rec.get("source_handle")
    if isinstance(handle, (str, int)) and not isinstance(handle, bool):
        handles = list(handles) + [str(handle)]
    source_ref = rec.get("source_ref") or {}
    if not isinstance(source_ref, dict):
        source_ref = {}
    handles += valid_handle_tokens(source_ref.get("handles"))
    ref_handle = source_ref.get("handle")
    if isinstance(ref_handle, (str, int)) and not isinstance(ref_handle, bool):
        handles.append(str(ref_handle))
    return sorted({str(item) for item in handles if str(item).strip()})


def _geometry_identity(rec: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "kind", "closed", "points", "center", "radius", "centerline",
        "z_base", "elevation", "width_detected", "diameter", "width_mm",
        "height_mm",
    )
    return {key: rec[key] for key in keys if key in rec}


def _source_ref(rec: dict[str, Any]) -> dict[str, Any]:
    existing = copy.deepcopy(rec.get("source_ref") or {})
    handles = _source_handles(rec)
    layer = (existing.get("layer") or rec.get("source_layer")
             or rec.get("layer") or "")
    block_name = (existing.get("block_name") or rec.get("source_block_name")
                  or rec.get("block_name") or "")
    entity_type = (existing.get("entity_type")
                   or rec.get("source_entity_type") or "")
    existing.update({
        "handle": handles[0] if len(handles) == 1 else None,
        "handles": handles,
        "layer": layer,
        "block_name": block_name,
        "entity_type": entity_type,
    })
    return existing


def _infer_semantic(category: str, rec: dict[str, Any],
                    params: dict[str, Any] | None = None) -> dict[str, Any]:
    semantic = copy.deepcopy(rec.get("semantic") or {})
    if category == "zone":
        semantic.setdefault("kind", "space")
        semantic.setdefault("boundary", "closed" if rec.get("closed") else "open")
        wall_height = (params or {}).get("wall", {}).get("height")
        if wall_height not in (None, ""):
            semantic.setdefault("ceiling_height_mm", wall_height)
            semantic.setdefault("height_source", "project_default")
    elif category == "equipment":
        haystack = " ".join(str(rec.get(key) or "") for key in
                            ("block_name", "source_block_name", "layer", "source_layer"))
        if _TERMINAL_RE.search(haystack):
            semantic.setdefault("kind", "air_terminal")
            if rec.get("kind") == "circle" and rec.get("radius") is not None:
                semantic.setdefault("diameter_mm", 2.0 * float(rec["radius"]))
            elif rec.get("kind") == "polyline" and rec.get("points"):
                xs = [float(point[0]) for point in rec["points"]]
                ys = [float(point[1]) for point in rec["points"]]
                semantic.setdefault("width_mm", max(xs) - min(xs))
                semantic.setdefault("height_mm", max(ys) - min(ys))
            if _SUPPLY_RE.search(haystack):
                semantic.setdefault("role", "supply")
                semantic.setdefault("role_source", "name_inference")
            elif _EXHAUST_RE.search(haystack):
                semantic.setdefault("role", "exhaust")
                semantic.setdefault("role_source", "name_inference")
            else:
                semantic.setdefault("role", "unresolved")
        else:
            semantic.setdefault("kind", "equipment")
            semantic.setdefault("role", "solid")
            semantic.setdefault("role_source", "category_default")
    elif category == "opening":
        semantic.setdefault("kind", "architectural_opening")
        semantic.setdefault("cfd_boundary", False)
    elif category in ("wall", "column", "slab"):
        semantic.setdefault("kind", "solid_boundary")
    elif category in ("pipe", "duct", "tray"):
        semantic.setdefault("kind", category)
    return semantic


def _element_identity(category: str, rec: dict[str, Any]) -> tuple[str, str]:
    ref = rec["source_ref"]
    geometry = _geometry_identity(rec)
    if ref.get("handles"):
        basis = {
            "category": category,
            "handles": ref["handles"],
            "layer": ref.get("layer", ""),
            "block_name": ref.get("block_name", ""),
            "geometry": geometry,
        }
        return _digest(category, basis), "source_derived"
    basis = {
        "category": category,
        "layer": ref.get("layer", ""),
        "block_name": ref.get("block_name", ""),
        "geometry": geometry,
    }
    return _digest(category, basis), "geometry_derived"


def _level_id(z: float) -> str:
    return _digest("level", {"z_mm": round(float(z), 6)}, 12)


def migrate_geometry(data: dict[str, Any], source_path: str | None = None) -> dict[str, Any]:
    """Return an additive, deterministic geometry.v2 representation."""
    out = copy.deepcopy(data)
    out["schema_version"] = SCHEMA_VERSION
    out["contract"] = CONTRACT
    out["source"] = source_path or out.get("source") or ""
    out["units"] = "mm"
    scale = float(out.get("scale_applied", 1.0) or 1.0)
    out.setdefault("scale_applied", scale)
    existing_source_units = out.get("source_units") or {}
    source_insunits = out.get("source_insunits", existing_source_units.get("insunits"))
    source_units = copy.deepcopy(existing_source_units)
    source_units.setdefault("insunits", source_insunits)
    source_units.setdefault("millimetres_per_source_unit", scale)
    source_units.setdefault("normalized_length_unit", "mm")
    source_units.setdefault("assumed", bool(source_insunits in (None, 0)))
    out["source_units"] = source_units
    out["coordinate_system"] = {
        "axis_convention": "XY_Z_UP",
        "origin_mm": [0.0, 0.0, 0.0],
        "rotation_deg": 0.0,
        "millimetres_to_metres": 0.001,
    }

    floors = list(out.get("floors") or [{"z": 0.0, "label": "Level_1"}])
    levels = []
    for index, floor in enumerate(floors):
        z = float(floor.get("z", 0.0) or 0.0)
        levels.append({
            "id": _level_id(z),
            "label": floor.get("label") or f"Level_{index + 1}",
            "elevation_mm": z,
        })
    out["levels"] = levels

    elements = out.setdefault("elements", {})
    used_ids: set[str] = set()
    for category in CATEGORIES:
        records = elements.setdefault(category, [])
        for rec in records:
            rec["category"] = category
            rec["source_ref"] = _source_ref(rec)
            proposed, stability = _element_identity(category, rec)
            element_id = str(rec.get("id") or proposed)
            if element_id in used_ids:
                collision_basis = {
                    "base": proposed,
                    "duplicate": sum(1 for value in used_ids if value.startswith(proposed)),
                }
                element_id = f"{proposed}_{_digest('dup', collision_basis, 8).split('_', 1)[1]}"
                stability = "geometry_derived_duplicate"
            used_ids.add(element_id)
            rec["id"] = element_id
            rec["id_stability"] = stability
            rec["confirmed"] = bool(rec.get("confirmed", False))
            rec["confirmation_state"] = "confirmed" if rec["confirmed"] else "unconfirmed"
            rec["semantic"] = _infer_semantic(category, rec, out.get("params"))
            z = float(rec.get("z_base", rec.get("elevation", 0.0)) or 0.0)
            rec["level_id"] = min(levels, key=lambda level: abs(level["elevation_mm"] - z))["id"]

    zone_ids = [rec["id"] for rec in elements.get("zone", [])]
    for category, records in elements.items():
        if category == "zone":
            continue
        for rec in records:
            zone_index = rec.get("zone")
            if isinstance(zone_index, int) and 0 <= zone_index < len(zone_ids):
                rec["space_id"] = zone_ids[zone_index]

    out["review"] = build_review(out)
    return out


def _issue(code: str, message: str, severity: str = "error",
           element_id: str | None = None, field: str | None = None) -> dict[str, Any]:
    issue = {"code": code, "severity": severity, "message": message}
    if element_id:
        issue["element_id"] = element_id
    if field:
        issue["field"] = field
    return issue


def validate_geometry_v2(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate the core v2 contract without requiring jsonschema."""
    issues: list[dict[str, Any]] = []
    if data.get("schema_version") != SCHEMA_VERSION:
        issues.append(_issue("SCHEMA_VERSION", "schema_version must be 2"))
    if data.get("contract") != CONTRACT:
        issues.append(_issue("CONTRACT", "contract must be geometry.v2"))
    if data.get("units") != "mm":
        issues.append(_issue("UNITS", "normalized geometry units must be mm"))
    if not isinstance(data.get("coordinate_system"), dict):
        issues.append(_issue("COORDINATE_SYSTEM", "coordinate_system is required"))
    elements = data.get("elements")
    if not isinstance(elements, dict):
        return issues + [_issue("ELEMENTS", "elements must be an object")]
    seen: set[str] = set()
    for category, records in elements.items():
        if not isinstance(records, list):
            issues.append(_issue("ELEMENT_LIST", f"elements.{category} must be an array"))
            continue
        for rec in records:
            eid = rec.get("id")
            if not eid:
                issues.append(_issue("ELEMENT_ID", "element id is required"))
            elif eid in seen:
                issues.append(_issue("DUPLICATE_ID", f"duplicate element id: {eid}", element_id=eid))
            else:
                seen.add(eid)
            if rec.get("category") != category:
                issues.append(_issue("ELEMENT_CATEGORY", "category does not match its collection",
                                     element_id=eid))
            if not isinstance(rec.get("source_ref"), dict):
                issues.append(_issue("SOURCE_REF", "source_ref is required", element_id=eid))
            if not isinstance(rec.get("confirmed"), bool):
                issues.append(_issue("CONFIRMED", "confirmed must be boolean", element_id=eid))
    return issues


def validate_for_body_fitted(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Return semantic blockers for body_fitted_airflow.

    These issues do not prevent the existing ``screening_voxel`` workflow.
    """
    issues = list(validate_geometry_v2(data))
    if (data.get("unit_review") or {}).get("required"):
        issues.append(_issue(
            "SOURCE_UNIT_CONFIRMATION_REQUIRED",
            "Confirm the DXF source unit before body-fitted CFD.",
            field="unit_review.required",
        ))
    elements = data.get("elements") or {}
    zones = elements.get("zone") or []
    closed = [zone for zone in zones if zone.get("closed") and len(zone.get("points") or []) >= 3]
    if not zones:
        issues.append(_issue("SPACE_MISSING", "A closed room/zone is required for body-fitted CFD."))
    elif not closed:
        issues.append(_issue("SPACE_NOT_CLOSED", "No closed room/zone boundary was found."))
    elif not any(zone.get("confirmed") for zone in closed):
        issues.append(_issue("SPACE_CONFIRMATION_REQUIRED",
                             "Confirm the room/zone to use as the air volume."))
    for zone in zones:
        if not zone.get("closed"):
            issues.append(_issue("SPACE_BOUNDARY_OPEN", "The room boundary is open.",
                                 element_id=zone.get("id"), field="closed"))
        try:
            ceiling_height = float((zone.get("semantic") or {}).get("ceiling_height_mm", 0) or 0)
        except (TypeError, ValueError):
            ceiling_height = 0
        if ceiling_height <= 0:
            issues.append(_issue("SPACE_HEIGHT_REQUIRED", "Enter the room ceiling height.",
                                 element_id=zone.get("id"),
                                 field="semantic.ceiling_height_mm"))

    canonical_heat_sources: list[tuple[str | None, dict[str, Any]]] = []
    for rec in elements.get("equipment") or []:
        semantic = rec.get("semantic") or {}
        kind = semantic.get("kind")
        eid = rec.get("id")
        if kind == "air_terminal":
            if semantic.get("role") not in ("supply", "exhaust"):
                issues.append(_issue("TERMINAL_ROLE_REQUIRED",
                                     "Choose supply or exhaust for this air terminal.",
                                     element_id=eid, field="semantic.role"))
            try:
                airflow = float(semantic.get("airflow_cmh", 0.0) or 0.0)
            except (TypeError, ValueError):
                airflow = 0.0
            if airflow <= 0:
                issues.append(_issue("TERMINAL_AIRFLOW_REQUIRED",
                                     "Enter a positive airflow in CMH.", element_id=eid,
                                     field="semantic.airflow_cmh"))
            if not semantic.get("host_surface"):
                issues.append(_issue("TERMINAL_HOST_REQUIRED",
                                     "Select the wall or ceiling that hosts this terminal.",
                                     element_id=eid, field="semantic.host_surface"))
            host = str(semantic.get("host_surface") or "").lower().replace("wall:", "")
            if host in ("x0", "xl", "y0", "yw"):
                try:
                    elevation = float(semantic.get("center_z_mm", 0) or 0)
                except (TypeError, ValueError):
                    elevation = 0
                if elevation <= 0:
                    issues.append(_issue("TERMINAL_ELEVATION_REQUIRED",
                                         "Enter the wall terminal centre elevation.",
                                         element_id=eid, field="semantic.center_z_mm"))
            normal = semantic.get("normal")
            try:
                valid_normal = (isinstance(normal, list) and len(normal) == 3
                                and math.sqrt(sum(float(v) ** 2 for v in normal)) > 0)
            except (TypeError, ValueError):
                valid_normal = False
            if not valid_normal:
                issues.append(_issue("TERMINAL_NORMAL_REQUIRED",
                                     "Confirm the terminal discharge direction.", element_id=eid,
                                     field="semantic.normal"))
            if not rec.get("confirmed"):
                issues.append(_issue("TERMINAL_CONFIRMATION_REQUIRED",
                                     "Confirm this air terminal before meshing.",
                                     element_id=eid, field="confirmed"))
        else:
            if not rec.get("confirmed"):
                issues.append(_issue("EQUIPMENT_CONFIRMATION_REQUIRED",
                                     "Confirm whether this equipment is a solid or heat source.",
                                     element_id=eid, field="confirmed"))
            role = semantic.get("role")
            if role == "heat_source":
                source_type = str(
                    semantic.get("source_type") or ""
                ).strip().casefold()
                if source_type != "user_confirmed":
                    issues.append(_issue(
                        "EQUIPMENT_HEAT_SOURCE_CONFIRMATION_REQUIRED",
                        "A body-fitted equipment load must explicitly be reviewed as user_confirmed before CFD.",
                        element_id=eid,
                        field="semantic.source_type",
                    ))
                try:
                    input_power = semantic.get("input_power_w")
                    power_w = (float(input_power) if input_power is not None
                               else float(semantic.get("power_kw", 0) or 0) * 1000.0)
                except (TypeError, ValueError):
                    power_w = 0
                if power_w <= 0:
                    issues.append(_issue("EQUIPMENT_POWER_REQUIRED",
                                         "Enter the equipment heat output in kW.",
                                         element_id=eid,
                                         field=("semantic.input_power_w"
                                                if "input_power_w" in semantic
                                                else "semantic.power_kw")))
                try:
                    convective_fraction = float(semantic.get("convective_fraction"))
                except (TypeError, ValueError):
                    convective_fraction = 0
                if not 0 < convective_fraction <= 1:
                    issues.append(_issue(
                        "EQUIPMENT_CONVECTIVE_FRACTION_REQUIRED",
                        "Enter and confirm a convective heat fraction between 0 and 1.",
                        element_id=eid, field="semantic.convective_fraction",
                    ))
                if not str(semantic.get("evidence") or "").strip():
                    issues.append(_issue(
                        "EQUIPMENT_HEAT_EVIDENCE_REQUIRED",
                        "Record the source for the equipment heat output before CFD.",
                        element_id=eid, field="semantic.evidence",
                    ))
                # Keep the reviewed geometry in the same W-based heat split
                # contract consumed by both CFD adapters.  Older inputs may
                # still carry ``power_kw`` only; the normalizer performs that
                # one compatibility conversion and derives radiation only
                # when the user did not explicitly supply a split.
                try:
                    canonical_source = normalize_confirmed_heat_source({
                        **semantic,
                        "source_id": eid,
                        "source_label": ((rec.get("source_ref") or {}).get("block_name")
                                         or (rec.get("source_ref") or {}).get("layer")
                                         or eid),
                        "source_type": semantic.get("source_type"),
                        "source_ref": rec.get("source_ref"),
                        "override_of_dxf": semantic.get("override_of_dxf"),
                    })
                except HeatSourceContractError as exc:
                    message = str(exc)
                    if "must sum to 1" in message:
                        issues.append(_issue(
                            "EQUIPMENT_HEAT_FRACTION_SUM_REQUIRED",
                            "Convective and radiative heat fractions must sum to 1.",
                            element_id=eid,
                            field="semantic.radiative_fraction",
                        ))
                    elif "radiative_fraction" in message:
                        issues.append(_issue(
                            "EQUIPMENT_RADIATIVE_FRACTION_REQUIRED",
                            "Enter a radiative heat fraction between 0 and 1.",
                            element_id=eid,
                            field="semantic.radiative_fraction",
                        ))
                    elif "input power" in message:
                        issues.append(_issue(
                            "EQUIPMENT_HEAT_CONTRACT_INVALID",
                            message,
                            element_id=eid,
                            field="semantic.input_power_w",
                        ))
                    else:
                        issues.append(_issue(
                            "EQUIPMENT_HEAT_CONTRACT_INVALID",
                            message,
                            element_id=eid,
                            field="semantic.source_type",
                        ))
                else:
                    # ``setdefault`` preserves original user fields while
                    # recording a canonical explicit W-based split in each
                    # reviewed geometry.v2 artifact.
                    semantic.setdefault("source_type", canonical_source["source_type"])
                    semantic.setdefault("input_power_w", canonical_source["input_power_w"])
                    semantic.setdefault("radiative_fraction",
                                        canonical_source["radiative_fraction"])
                    semantic.setdefault("convective_power_w",
                                        canonical_source["convective_power_w"])
                    semantic.setdefault("radiative_power_w",
                                        canonical_source["radiative_power_w"])
                    # Body-fitted heat sources are design-review inputs, not
                    # legacy screening assumptions.  The shared normalizer
                    # still supports ``legacy_manual_input`` for V3a, but it
                    # cannot promote a body-fitted source.
                    if canonical_source["source_type"] == "user_confirmed":
                        canonical_heat_sources.append((eid, canonical_source))
            if role in ("solid", "heat_source"):
                try:
                    height_mm = float(semantic.get("height_mm", 0) or 0)
                except (TypeError, ValueError):
                    height_mm = 0
                if height_mm <= 0:
                    issues.append(_issue("EQUIPMENT_HEIGHT_REQUIRED",
                                         "Enter the actual equipment height.",
                                         element_id=eid, field="semantic.height_mm"))
    try:
        assert_unique_positive_source_ids(
            [source for _, source in canonical_heat_sources]
        )
    except HeatSourceContractError as exc:
        # The individual source records remain listed above; one explicit
        # global issue prevents a duplicated reviewed equipment ID from
        # injecting heat twice through separate geometry elements.
        issues.append(_issue(
            "EQUIPMENT_HEAT_SOURCE_ID_DUPLICATE",
            str(exc),
            field="elements.equipment",
        ))
    return issues


def build_review(data: dict[str, Any]) -> dict[str, Any]:
    issues = validate_for_body_fitted(data)
    element_index = {
        rec.get("id"): rec
        for category, records in (data.get("elements") or {}).items()
        for rec in records
        if rec.get("id")
    }
    for item in issues:
        rec = element_index.get(item.get("element_id"))
        if not rec:
            continue
        ref = rec.get("source_ref") or {}
        item["element_category"] = rec.get("category")
        item["source_label"] = (ref.get("block_name") or ref.get("layer")
                                or rec.get("id"))
    blockers = [item for item in issues if item.get("severity") == "error"]
    warnings = [item for item in issues if item.get("severity") == "warning"]
    return {
        "engine": "body_fitted_airflow",
        "ready": not blockers,
        "blocking": bool(blockers),
        "blocker_count": len(blockers),
        "warning_count": len(warnings),
        "items": issues,
        "screening_voxel_allowed": True,
    }


def semantic_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Compare user-significant fields by stable element id."""
    def by_id(value: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {rec["id"]: rec for records in (value.get("elements") or {}).values()
                for rec in records if rec.get("id")}

    left, right = by_id(before), by_id(after)
    changed = []
    keys = ("confirmed", "confirmation_state", "level_id", "space_id", "semantic")
    for eid in sorted(left.keys() & right.keys()):
        delta = {key: {"before": left[eid].get(key), "after": right[eid].get(key)}
                 for key in keys if left[eid].get(key) != right[eid].get(key)}
        if delta:
            changed.append({"id": eid, "changes": delta})
    return {
        "added": sorted(right.keys() - left.keys()),
        "removed": sorted(left.keys() - right.keys()),
        "changed": changed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate and validate geometry.v2")
    parser.add_argument("input")
    parser.add_argument("-o", "--output")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    with open(args.input, encoding="utf-8") as handle:
        source = json.load(handle)
    migrated = migrate_geometry(source, source_path=source.get("source") or args.input)
    issues = validate_geometry_v2(migrated)
    if args.output and not args.validate_only:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(migrated, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"valid": not issues, "issues": issues,
                      "review": migrated["review"]}, ensure_ascii=False, indent=2))
    return 0 if not issues else 2


if __name__ == "__main__":
    raise SystemExit(main())
