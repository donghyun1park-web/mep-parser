"""Fail-closed HVAC template loading and reviewed Scenario draft creation."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


_MODULE_ROOT = Path(__file__).resolve().parent
_USER_AUTHORITY = re.compile(r"^user_confirmed:\S+$")
_APPROVED_SOURCE_LABEL = re.compile(r"^approved_source:\S+$")
_COMFORT_KEYS = (
    "relative_humidity_pct", "metabolic_rate_met", "clothing_clo",
)


class HVACConfigError(ValueError):
    """Raised when a template or a user-supplied physical value is unsafe."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def _load_object(path: Path, *, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HVACConfigError(code, f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise HVACConfigError(code, f"expected JSON object: {path}")
    return value


def _schema_validator(name: str) -> Draft202012Validator:
    schema = _load_object(_MODULE_ROOT / name, code="SCHEMA_INVALID")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def load_hvac_template(template_path: Path) -> dict:
    """Load a declarative template that cannot contain physical defaults."""
    template = _load_object(Path(template_path), code="TEMPLATE_READ_FAILED")
    errors = sorted(
        _schema_validator("hvac_template.v1.schema.json").iter_errors(template),
        key=lambda item: list(item.path),
    )
    if errors:
        raise HVACConfigError(
            "TEMPLATE_SCHEMA_INVALID",
            "; ".join(error.message for error in errors[:5]),
        )
    return copy.deepcopy(template)


def _finite_number(value: Any, *, path: str, minimum: float | None = None,
                   exclusive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HVACConfigError("PHYSICS_VALUE_INVALID", f"{path} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise HVACConfigError("PHYSICS_VALUE_INVALID", f"{path} must be finite")
    if minimum is not None and (number <= minimum if exclusive else number < minimum):
        relation = ">" if exclusive else ">="
        raise HVACConfigError(
            "PHYSICS_VALUE_INVALID", f"{path} must be {relation} {minimum}",
        )
    return number


def _parameter_rules(template: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rules: dict[str, dict[str, Any]] = {}
    for row in template["allowed_parameters"]:
        key = row["key"]
        if key in rules:
            raise HVACConfigError(
                "TEMPLATE_SCHEMA_INVALID", f"duplicate allowed parameter: {key}",
            )
        rules[key] = row["validation"]
    return rules


def _parameter_number(rules: dict[str, dict[str, Any]], key: str, value: Any,
                      *, path: str) -> float:
    if key not in rules:
        raise HVACConfigError(
            "PARAMETER_NOT_ALLOWED", f"template does not allow {key}",
        )
    number = _finite_number(value, path=path)
    validation = rules[key]
    if "exclusive_minimum" in validation and number <= validation["exclusive_minimum"]:
        raise HVACConfigError(
            "PHYSICS_VALUE_INVALID",
            f"{path} must be > {validation['exclusive_minimum']}",
        )
    if "minimum" in validation and number < validation["minimum"]:
        raise HVACConfigError(
            "PHYSICS_VALUE_INVALID", f"{path} must be >= {validation['minimum']}",
        )
    if "maximum" in validation and number > validation["maximum"]:
        raise HVACConfigError(
            "PHYSICS_VALUE_INVALID", f"{path} must be <= {validation['maximum']}",
        )
    return number


def _authority(value: Any, *, path: str) -> str:
    if isinstance(value, str) and _APPROVED_SOURCE_LABEL.fullmatch(value) is not None:
        raise HVACConfigError(
            "APPROVED_SOURCE_UNVERIFIED",
            f"{path} references an approved source without a verified approval artifact",
        )
    if not isinstance(value, str) or _USER_AUTHORITY.fullmatch(value) is None:
        raise HVACConfigError(
            "UNAPPROVED_PHYSICS_VALUE",
            f"{path} needs user_confirmed:<ref>; approved sources need a verified artifact",
        )
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_reviewed_geometry(geometry: dict[str, Any]) -> None:
    review = geometry.get("review")
    if (not isinstance(review, dict)
            or review.get("ready") is not True
            or review.get("blocking") is True
            or review.get("blocker_count") != 0):
        raise HVACConfigError(
            "DESIGN_REVIEW_NOT_READY",
            "geometry review must be ready with zero blockers",
        )


def _raw_geometry_issues(geometry: dict[str, Any]) -> list[dict[str, Any]]:
    errors = sorted(
        _schema_validator("geometry.v2.schema.json").iter_errors(geometry),
        key=lambda item: list(item.path),
    )
    if errors:
        raise HVACConfigError(
            "DESIGN_SCHEMA_INVALID",
            "; ".join(error.message for error in errors[:5]),
        )
    from geometry_v2 import validate_for_body_fitted

    return validate_for_body_fitted(geometry)


def _geometry_from_design(
    design: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(design, dict):
        raise HVACConfigError("DESIGN_INVALID", "design must be an object")
    if design.get("contract") == "geometry.v2":
        _assert_reviewed_geometry(design)
        geometry = copy.deepcopy(design)
        return geometry, {
            "design_id": None,
            "revision_sha256": None,
            "geometry_contract": "geometry.v2",
        }, _raw_geometry_issues(geometry)
    if design.get("contract") != "design.v1":
        raise HVACConfigError("DESIGN_INVALID", "expected design.v1 or geometry.v2")
    artifact_value = design.get("path")
    if not isinstance(artifact_value, str) or not artifact_value:
        raise HVACConfigError("DESIGN_INVALID", "Design artifact path is required")
    resolved_artifact = Path(artifact_value).resolve()
    project_root = design.get("project_root")
    if not isinstance(project_root, str) or not project_root:
        store_ancestor = next(
            (parent for parent in resolved_artifact.parents if parent.name == "_project_model"),
            None,
        )
        if store_ancestor is None:
            raise HVACConfigError("DESIGN_INVALID", "cannot locate project root")
        root = store_ancestor.parent
    else:
        root = Path(project_root).resolve()
    try:
        resolved_artifact.relative_to(root)
    except ValueError as exc:
        raise HVACConfigError("DESIGN_INVALID", "Design artifact escapes project") from exc
    from project_model import validate_design_revision

    design_issues = validate_design_revision(resolved_artifact, projects_root=root)
    if design_issues:
        raise HVACConfigError("DESIGN_INVALID", str(design_issues))
    authoritative_design = _load_object(resolved_artifact, code="DESIGN_INVALID")
    geometry_ref = authoritative_design.get("geometry")
    if not isinstance(geometry_ref, dict):
        raise HVACConfigError("DESIGN_INVALID", "geometry reference is missing")
    relative = geometry_ref.get("path")
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or "\\" in relative:
        raise HVACConfigError("DESIGN_INVALID", "unsafe geometry reference")
    geometry_path = (root / relative).resolve()
    try:
        geometry_path.relative_to(root)
    except ValueError as exc:
        raise HVACConfigError("DESIGN_INVALID", "geometry reference escapes project") from exc
    try:
        actual_sha = _sha256(geometry_path)
    except OSError as exc:
        raise HVACConfigError("DESIGN_INVALID", f"cannot read geometry: {exc}") from exc
    if actual_sha != geometry_ref.get("sha256"):
        raise HVACConfigError("DESIGN_INVALID", "geometry artifact hash changed")
    geometry = _load_object(geometry_path, code="DESIGN_INVALID")
    if geometry.get("contract") != "geometry.v2":
        raise HVACConfigError("DESIGN_INVALID", "referenced geometry is not geometry.v2")
    _assert_reviewed_geometry(geometry)
    return geometry, {
        "design_id": authoritative_design.get("design_id"),
        "revision_sha256": authoritative_design.get("revision_sha256"),
        "geometry_contract": "geometry.v2",
    }, []


def _design_sources(geometry: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    elements = geometry.get("elements")
    if not isinstance(elements, dict):
        raise HVACConfigError("DESIGN_INVALID", "geometry elements are missing")
    terminals: list[dict[str, Any]] = []
    heat_sources: list[dict[str, Any]] = []
    for rows in elements.values():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("semantic"), dict):
                continue
            role = row["semantic"].get("role")
            if role in {"supply", "exhaust"}:
                terminals.append(row)
            elif role == "heat_source":
                heat_sources.append(row)
    return terminals, heat_sources


def _blocker(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "message": message, **details}


def _indexed(rows: Any, key: str, *, duplicate_code: str,
             blockers: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(rows, list):
        raise HVACConfigError("USER_VALUES_INVALID", f"{key} collection must be a list")
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get(key), str) or not row[key]:
            raise HVACConfigError("USER_VALUES_INVALID", f"every row needs {key}")
        identity = row[key]
        if identity in result:
            blockers.append(_blocker(duplicate_code, f"duplicate {key}: {identity}", **{key: identity}))
        else:
            result[identity] = row
    return result


def _validate_comfort_inputs(
    value: Any, rules: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise HVACConfigError("USER_VALUES_INVALID", "comfort_inputs must be an object")
    result: dict[str, Any] = {}
    for key, record in value.items():
        if key not in _COMFORT_KEYS or not isinstance(record, dict):
            raise HVACConfigError("USER_VALUES_INVALID", f"unsupported comfort input: {key}")
        number = _parameter_number(
            rules, f"comfort.{key}", record.get("value"),
            path=f"comfort_inputs.{key}",
        )
        authority = _authority(record.get("authority"), path=f"comfort_inputs.{key}")
        result[key] = {"value": number, "authority": authority}
    return result


def apply_hvac_template(template: dict, design: dict, *, user_values: dict) -> dict:
    """Create a reviewable Scenario draft using stable Design element IDs."""
    errors = sorted(
        _schema_validator("hvac_template.v1.schema.json").iter_errors(template),
        key=lambda item: list(item.path),
    )
    if errors:
        raise HVACConfigError("TEMPLATE_SCHEMA_INVALID", errors[0].message)
    if not isinstance(user_values, dict):
        raise HVACConfigError("USER_VALUES_INVALID", "user_values must be an object")
    parameter_rules = _parameter_rules(template)

    geometry, design_ref, geometry_issues = _geometry_from_design(design)
    design_terminals, design_heat = _design_sources(geometry)
    blockers: list[dict[str, Any]] = []
    if design_ref["revision_sha256"] is None:
        blockers.append(_blocker(
            "DESIGN_REVISION_REQUIRED",
            "raw geometry is preview-only; publish a reviewed immutable Design revision",
        ))

    terminal_by_id: dict[str, dict[str, Any]] = {}
    role_counts = {"supply": 0, "exhaust": 0}
    for row in design_terminals:
        terminal_id = row.get("id")
        role = row["semantic"].get("role")
        if not isinstance(terminal_id, str) or not terminal_id:
            blockers.append(_blocker("MISSING_TERMINAL_ID", "terminal has no stable element ID"))
            continue
        if row.get("id_stability") not in {"source_derived", "geometry_derived"}:
            blockers.append(_blocker(
                "UNSTABLE_TERMINAL_ID",
                f"terminal ID requires geometry collision review: {terminal_id}",
                terminal_id=terminal_id,
            ))
        if row.get("confirmed") is not True or row.get("confirmation_state") != "confirmed":
            blockers.append(_blocker(
                "UNCONFIRMED_TERMINAL",
                f"terminal is not confirmed in reviewed geometry: {terminal_id}",
                terminal_id=terminal_id,
            ))
        role_counts[role] += 1
        if terminal_id in terminal_by_id:
            blockers.append(_blocker(
                "DUPLICATE_TERMINAL_ID", f"duplicate terminal ID: {terminal_id}",
                terminal_id=terminal_id,
            ))
        else:
            terminal_by_id[terminal_id] = row
    for role in template["required_terminal_roles"]:
        if role_counts.get(role, 0) == 0:
            blockers.append(_blocker(
                "MISSING_REQUIRED_TERMINAL_ROLE", f"Design has no {role} terminal", role=role,
            ))

    input_terminals = _indexed(
        user_values.get("terminals", []), "terminal_id",
        duplicate_code="DUPLICATE_TERMINAL_INPUT", blockers=blockers,
    )
    for terminal_id in sorted(terminal_by_id):
        if terminal_id not in input_terminals:
            blockers.append(_blocker(
                "MISSING_TERMINAL_INPUT", f"no operating value for {terminal_id}",
                terminal_id=terminal_id,
            ))
    for terminal_id in sorted(input_terminals):
        if terminal_id not in terminal_by_id:
            blockers.append(_blocker(
                "UNKNOWN_TERMINAL_ID", f"input does not map to Design terminal {terminal_id}",
                terminal_id=terminal_id,
            ))

    authority_map: dict[str, str] = {}
    terminal_rows: list[dict[str, Any]] = []
    supply_total = 0.0
    exhaust_total = 0.0
    for terminal_id in sorted(set(terminal_by_id) & set(input_terminals)):
        source = input_terminals[terminal_id]
        role = terminal_by_id[terminal_id]["semantic"]["role"]
        authority = _authority(source.get("authority"), path=f"terminals[{terminal_id}].airflow_cmh")
        airflow = _parameter_number(
            parameter_rules, "terminal.airflow_cmh", source.get("airflow_cmh"),
            path=f"terminals[{terminal_id}].airflow_cmh",
        )
        output = {"terminal_id": terminal_id, "airflow_cmh": airflow}
        authority_map[f"terminals[{terminal_id}].airflow_cmh"] = authority
        if role == "supply":
            supply_total += airflow
            supply_temperature = _parameter_number(
                parameter_rules, "terminal.supply_temperature_k",
                source.get("supply_temperature_k"),
                path=f"terminals[{terminal_id}].supply_temperature_k",
            )
            output["supply_temperature_k"] = supply_temperature
            authority_map[f"terminals[{terminal_id}].supply_temperature_k"] = authority
        else:
            exhaust_total += airflow
        terminal_rows.append(output)

    if supply_total > 0 and exhaust_total > 0:
        denominator = max(supply_total, exhaust_total)
        imbalance = abs(supply_total - exhaust_total) / denominator
        tolerance = float(template["validation"]["airflow_balance_tolerance_fraction"])
        if imbalance > tolerance:
            blockers.append(_blocker(
                "AIRFLOW_IMBALANCE", "supply and exhaust totals exceed template tolerance",
                supply_cmh=supply_total, exhaust_cmh=exhaust_total,
                imbalance_fraction=imbalance, tolerance_fraction=tolerance,
            ))

    heat_by_id: dict[str, dict[str, Any]] = {}
    for row in design_heat:
        source_id = row.get("id")
        if isinstance(source_id, str) and source_id:
            if row.get("id_stability") not in {"source_derived", "geometry_derived"}:
                blockers.append(_blocker(
                    "UNSTABLE_HEAT_SOURCE_ID",
                    f"heat source ID requires geometry collision review: {source_id}",
                    source_id=source_id,
                ))
            if row.get("confirmed") is not True or row.get("confirmation_state") != "confirmed":
                blockers.append(_blocker(
                    "UNCONFIRMED_HEAT_SOURCE",
                    f"heat source is not confirmed in reviewed geometry: {source_id}",
                    source_id=source_id,
                ))
            if source_id in heat_by_id:
                blockers.append(_blocker(
                    "DUPLICATE_HEAT_SOURCE_ID", f"duplicate heat source ID: {source_id}",
                    source_id=source_id,
                ))
            else:
                heat_by_id[source_id] = row
    relevant_ids = [row.get("id") for row in (*design_terminals, *design_heat)]
    for issue in geometry_issues:
        issue_code = issue.get("code")
        issue_id = issue.get("element_id")
        handled = (
            issue_code == "DUPLICATE_ID" and relevant_ids.count(issue_id) > 1
        ) or (
            issue_code == "TERMINAL_CONFIRMATION_REQUIRED"
            and issue_id in terminal_by_id
        ) or (
            issue_code == "HEAT_SOURCE_CONFIRMATION_REQUIRED"
            and issue_id in heat_by_id
        )
        if not handled:
            blockers.append(_blocker(
                "DESIGN_REVIEW_BLOCKER",
                str(issue.get("message") or issue_code or "geometry validation issue"),
                geometry_issue=copy.deepcopy(issue),
            ))
    input_heat = _indexed(
        user_values.get("heat_sources", []), "source_id",
        duplicate_code="DUPLICATE_HEAT_SOURCE_INPUT", blockers=blockers,
    )
    for source_id in sorted(heat_by_id):
        if source_id not in input_heat:
            blockers.append(_blocker(
                "MISSING_HEAT_SOURCE_INPUT", f"no operating value for {source_id}",
                source_id=source_id,
            ))
    for source_id in sorted(input_heat):
        if source_id not in heat_by_id:
            blockers.append(_blocker(
                "UNKNOWN_HEAT_SOURCE_ID", f"input does not map to Design heat source {source_id}",
                source_id=source_id,
            ))
    heat_rows: list[dict[str, Any]] = []
    for source_id in sorted(set(heat_by_id) & set(input_heat)):
        source = input_heat[source_id]
        authority = _authority(
            source.get("authority"), path=f"heat_sources[{source_id}].convective_power_w",
        )
        power = _parameter_number(
            parameter_rules, "heat_source.convective_power_w",
            source.get("convective_power_w"),
            path=f"heat_sources[{source_id}].convective_power_w",
        )
        heat_rows.append({
            "source_id": source_id, "convective_power_w": power, "authority": authority,
        })
        authority_map[f"heat_sources[{source_id}].convective_power_w"] = authority

    occupancy = user_values.get("occupancy")
    occupancy_output = None
    if occupancy is not None:
        if not isinstance(occupancy, dict):
            raise HVACConfigError("USER_VALUES_INVALID", "occupancy must be null or object")
        occupancy_authority = _authority(
            occupancy.get("authority"), path="occupancy.people_count",
        )
        people = _parameter_number(
            parameter_rules, "occupancy.people_count", occupancy.get("people_count"),
            path="occupancy.people_count",
        )
        if not people.is_integer():
            raise HVACConfigError("PHYSICS_VALUE_INVALID", "people_count must be an integer")
        schedule = occupancy.get("schedule_name")
        if not isinstance(schedule, str) or not schedule:
            raise HVACConfigError("USER_VALUES_INVALID", "occupancy.schedule_name is required")
        occupancy_output = {"people_count": int(people), "schedule_name": schedule}
        authority_map["occupancy.people_count"] = occupancy_authority

    weather = user_values.get("weather")
    weather_output = None
    if weather is not None:
        if not isinstance(weather, dict):
            raise HVACConfigError("USER_VALUES_INVALID", "weather must be null or object")
        weather_authority = _authority(
            weather.get("authority"), path="weather.outdoor_temperature_k",
        )
        outdoor_temperature = _parameter_number(
            parameter_rules, "weather.outdoor_temperature_k",
            weather.get("outdoor_temperature_k"), path="weather.outdoor_temperature_k",
        )
        weather_output = {
            "outdoor_temperature_k": outdoor_temperature,
            "authority": weather_authority,
        }
        authority_map["weather.outdoor_temperature_k"] = weather_authority

    period = user_values.get("operating_period")
    mesh = user_values.get("mesh_intent")
    if not isinstance(period, dict) or not isinstance(mesh, dict):
        raise HVACConfigError("USER_VALUES_INVALID", "operating_period and mesh_intent are required")
    duration = _parameter_number(
        parameter_rules, "operating_period.duration_s", period.get("duration_s"),
        path="operating_period.duration_s",
    )
    preset = mesh.get("preset")
    if preset not in {"quick", "detailed"}:
        raise HVACConfigError("USER_VALUES_INVALID", "mesh_intent.preset must be quick or detailed")
    cell_size = _parameter_number(
        parameter_rules, "mesh.background_cell_m", mesh.get("background_cell_m"),
        path="mesh_intent.background_cell_m",
    )
    comfort = _validate_comfort_inputs(
        user_values.get("comfort_inputs"), parameter_rules,
    )

    ready = not blockers
    operating_conditions = None
    if ready:
        profile = template["physics_profile"]
        operating_conditions = {
            "terminals": terminal_rows,
            "heat_sources": heat_rows,
            "occupancy": occupancy_output,
            "weather": weather_output,
            "operating_period": {"duration_s": duration},
            "mesh_intent": {"preset": preset, "background_cell_m": cell_size},
            "physics_intent": {
                "profile_name": profile["name"],
                "profile_scope": profile["scope"],
            },
            "input_authority": dict(sorted(authority_map.items())),
        }
    return {
        "contract": "hvac_template_application.v1",
        "template_id": template["template_id"],
        "design": design_ref,
        "ready": ready,
        "blockers": blockers,
        "terminal_mapping": [
            {"terminal_id": terminal_id, "role": terminal_by_id[terminal_id]["semantic"]["role"]}
            for terminal_id in sorted(terminal_by_id)
        ],
        "physics_profile": copy.deepcopy(template["physics_profile"]),
        "operating_conditions": operating_conditions,
        "deferred_inputs": {
            "comfort_status": "NOT_EVALUATED",
            "comfort": comfort,
        },
    }
