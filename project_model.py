"""Fail-closed immutable Design -> Scenario -> Run project model.

Geometry/terminal shape belongs to a Design revision.  Operating values belong
to a Scenario revision.  A Run identity binds exact immutable revisions and a
solver profile without modifying legacy CFD case directories.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


_MODULE_ROOT = Path(__file__).resolve().parent
_STORE = "_project_model"
_FORBIDDEN_SCENARIO_KEYS = {
    "geometry", "role", "normal", "size", "width", "height", "diameter",
    "diameter_m", "center", "points", "host_surface", "host_element_id",
    "mesh_patch_name", "source_element_id",
}


class ProjectModelError(ValueError):
    """Raised when an immutable project-model contract cannot be satisfied."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(
        value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False,
    ) + "\n").encode("utf-8")


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest_value(value: Any) -> str:
    return _digest_bytes(_canonical(value))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_json(path: Path, *, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectModelError(code, f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProjectModelError(code, f"expected a JSON object: {path}")
    return value


def _schema(name: str) -> Draft202012Validator:
    value = json.loads((_MODULE_ROOT / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(value)
    return Draft202012Validator(value)


def _schema_errors(name: str, value: Any) -> list[str]:
    return [error.message for error in sorted(
        _schema(name).iter_errors(value), key=lambda item: list(item.path),
    )]


def _inside(root: Path, candidate: Path, *, code: str) -> Path:
    root = root.resolve()
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ProjectModelError(code, f"path escapes project root: {candidate}") from exc
    return candidate


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _resolve_ref(root: Path, relative: Any, *, code: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ProjectModelError(code, "artifact reference path is missing")
    relative_path = Path(relative)
    if relative_path.is_absolute() or "\\" in relative or any(
        part in {".", ".."} for part in relative_path.parts
    ):
        raise ProjectModelError(code, f"unsafe artifact reference: {relative}")
    return _inside(root, root / relative_path, code=code)


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() == payload:
            return
        raise ProjectModelError("IMMUTABLE_COLLISION", f"refusing to overwrite {path}")
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _artifact_sha(path: Path) -> str:
    try:
        return _digest_bytes(path.read_bytes())
    except OSError as exc:
        raise ProjectModelError("ARTIFACT_READ_FAILED", f"cannot read {path}: {exc}") from exc


def _revision_sha(record: dict[str, Any], field: str) -> str:
    payload = {key: value for key, value in record.items() if key != field}
    return _digest_value(payload)


def _validate_geometry(path: Path) -> dict[str, Any]:
    geometry = _load_json(path, code="GEOMETRY_INVALID")
    errors = _schema_errors("geometry.v2.schema.json", geometry)
    if errors:
        raise ProjectModelError("GEOMETRY_SCHEMA_INVALID", "; ".join(errors[:5]))
    from geometry_v2 import validate_for_body_fitted

    review = geometry.get("review")
    blockers = validate_for_body_fitted(geometry)
    if (not isinstance(review, dict) or review.get("ready") is not True
            or review.get("blocking") is True or review.get("blocker_count") != 0
            or blockers):
        raise ProjectModelError("GEOMETRY_REVIEW_NOT_READY", str(blockers or review))
    return geometry


def _copy_geometry(root: Path, source: Path) -> tuple[Path, str, str]:
    source = _inside(root, source, code="GEOMETRY_PATH_ESCAPE")
    geometry = _validate_geometry(source)
    identity_sha = _digest_value(geometry)
    payload = _json_bytes(geometry)
    artifact_sha = _digest_bytes(payload)
    target = root / _STORE / "geometry" / f"{artifact_sha}.geometry.v2.json"
    _atomic_write(target, payload)
    return target, artifact_sha, identity_sha


def _find_root_from_artifact(path: Path) -> Path:
    resolved = path.resolve()
    for ancestor in (resolved.parent, *resolved.parents):
        if ancestor.name == _STORE:
            return ancestor.parent
    raise ProjectModelError("PROJECT_ROOT_NOT_FOUND", f"not under {_STORE}: {path}")


def _find_root_for_design(design_id: str, geometry_path: Path) -> Path:
    for root in (geometry_path.resolve().parent, *geometry_path.resolve().parents):
        if (root / _STORE / "designs" / design_id).is_dir():
            return root
    raise ProjectModelError("DESIGN_NOT_FOUND", f"cannot locate {design_id}")


def _revision_files(directory: Path, suffix: str) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(directory.glob(f"*.{suffix}.json"))


def _latest_revision(directory: Path, suffix: str) -> Path | None:
    files = _revision_files(directory, suffix)
    return files[-1] if files else None


def _response(path: Path, record: dict[str, Any], root: Path) -> dict[str, Any]:
    result = copy.deepcopy(record)
    result["path"] = str(path.resolve())
    result["artifact_sha256"] = _artifact_sha(path)
    result["project_root"] = str(root.resolve())
    return result


def create_design(
    projects_root: str | Path,
    *,
    geometry_path: str | Path,
    name: str,
    created_by: str,
) -> dict[str, Any]:
    root = Path(projects_root).resolve()
    geometry_target, geometry_sha, geometry_identity_sha = _copy_geometry(
        root, Path(geometry_path),
    )
    design_id = f"design-{geometry_identity_sha[:24]}"
    revisions = root / _STORE / "designs" / design_id / "revisions"
    parent = _latest_revision(revisions, "design.v1")
    revision_number = len(_revision_files(revisions, "design.v1")) + 1
    record: dict[str, Any] = {
        "schema_version": 1,
        "contract": "design.v1",
        "design_id": design_id,
        "revision_number": revision_number,
        "name": name,
        "created_at": _now(),
        "revision_author": created_by,
        "revision_reason": "initial reviewed geometry",
        "geometry": {
            "path": _relative(root, geometry_target),
            "sha256": geometry_sha,
            "contract": "geometry.v2",
        },
        "parent_revision": None if parent is None else {
            "path": _relative(root, parent), "sha256": _artifact_sha(parent),
        },
    }
    record["revision_sha256"] = _revision_sha(record, "revision_sha256")
    errors = _schema_errors("design.v1.schema.json", record)
    if errors:
        raise ProjectModelError("DESIGN_SCHEMA_INVALID", "; ".join(errors))
    target = revisions / f"{revision_number:04d}-{record['revision_sha256']}.design.v1.json"
    _atomic_write(target, _json_bytes(record))
    return _response(target, record, root)


def revise_design(
    design_id: str,
    *,
    geometry_path: str | Path,
    reason: str,
    revised_by: str,
) -> dict[str, Any]:
    geometry_source = Path(geometry_path)
    root = _find_root_for_design(design_id, geometry_source)
    geometry_target, geometry_sha, _ = _copy_geometry(root, geometry_source)
    revisions = root / _STORE / "designs" / design_id / "revisions"
    parent = _latest_revision(revisions, "design.v1")
    if parent is None:
        raise ProjectModelError("DESIGN_NOT_FOUND", design_id)
    previous = _load_json(parent, code="DESIGN_INVALID")
    revision_number = int(previous["revision_number"]) + 1
    record: dict[str, Any] = {
        "schema_version": 1,
        "contract": "design.v1",
        "design_id": design_id,
        "revision_number": revision_number,
        "name": previous["name"],
        "created_at": _now(),
        "revision_author": revised_by,
        "revision_reason": reason,
        "geometry": {
            "path": _relative(root, geometry_target),
            "sha256": geometry_sha,
            "contract": "geometry.v2",
        },
        "parent_revision": {
            "path": _relative(root, parent), "sha256": _artifact_sha(parent),
        },
    }
    record["revision_sha256"] = _revision_sha(record, "revision_sha256")
    errors = _schema_errors("design.v1.schema.json", record)
    if errors:
        raise ProjectModelError("DESIGN_SCHEMA_INVALID", "; ".join(errors))
    target = revisions / f"{revision_number:04d}-{record['revision_sha256']}.design.v1.json"
    _atomic_write(target, _json_bytes(record))
    return _response(target, record, root)


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            key in _FORBIDDEN_SCENARIO_KEYS or _contains_forbidden_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_forbidden_key(child) for child in value)
    return False


def classify_scenario_variation(base: Any, candidate: Any) -> str:
    """Classify operating-only changes separately from geometry/shape changes."""
    if _contains_forbidden_key(candidate):
        return "DESIGN_REVISION_REQUIRED"
    return "SCENARIO_REVISION"


_SCENARIO_DIFF_METADATA = {
    "airflow_cmh": (
        "CMH", "terminal flow and room air-change distribution",
    ),
    "supply_temperature_k": (
        "K", "supply-air thermal driving force",
    ),
    "convective_power_w": (
        "W", "internal convective heat load",
    ),
    "people_count": (
        "people", "occupancy load and occupied-period assumption",
    ),
    "outdoor_temperature_k": (
        "K", "external thermal boundary assumption",
    ),
    "duration_s": (
        "s", "simulated operating period",
    ),
    "background_cell_m": (
        "m", "mesh resolution intent and computational cost",
    ),
    "preset": (
        None, "mesh resolution intent and computational cost",
    ),
    "profile_name": (
        None, "solver physics profile selection",
    ),
    "profile_scope": (
        None, "solver physics profile applicability",
    ),
}


def _scenario_conditions(value: Any) -> Any:
    if isinstance(value, dict) and "operating_conditions" in value:
        return value["operating_conditions"]
    return value


_DIFF_MISSING = object()


def _identity_equal(baseline: Any, candidate: Any) -> bool:
    if baseline is _DIFF_MISSING or candidate is _DIFF_MISSING:
        return baseline is candidate
    return _canonical(baseline) == _canonical(candidate)


def _display_diff_value(value: Any) -> Any:
    if value is _DIFF_MISSING:
        return "<missing>"
    if isinstance(value, float):
        rounded = round(value, 6)
        return 0.0 if rounded == 0 else rounded
    if isinstance(value, dict):
        return {key: _display_diff_value(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_display_diff_value(child) for child in value]
    return copy.deepcopy(value)


def _diff_metadata(path: str) -> tuple[str | None, str]:
    field = path.rsplit(".", 1)[-1]
    if field == "input_authority" or ".input_authority." in path:
        return None, "input provenance and review authority"
    return _SCENARIO_DIFF_METADATA.get(
        field, (None, "scenario operating condition"),
    )


def _semantic_rows(path: str, baseline: Any, candidate: Any) -> list[dict[str, Any]]:
    if _identity_equal(baseline, candidate):
        return []
    if path.endswith(".terminals") and isinstance(baseline, list) and isinstance(candidate, list):
        base_by_id = {
            row.get("terminal_id"): row for row in baseline
            if isinstance(row, dict) and isinstance(row.get("terminal_id"), str)
        }
        candidate_by_id = {
            row.get("terminal_id"): row for row in candidate
            if isinstance(row, dict) and isinstance(row.get("terminal_id"), str)
        }
        if len(base_by_id) == len(baseline) and len(candidate_by_id) == len(candidate):
            rows: list[dict[str, Any]] = []
            for identity in sorted(set(base_by_id) | set(candidate_by_id)):
                rows.extend(_semantic_rows(
                    f"{path}[{identity}]",
                    base_by_id.get(identity, _DIFF_MISSING),
                    candidate_by_id.get(identity, _DIFF_MISSING),
                ))
            return rows
    if path.endswith(".heat_sources") and isinstance(baseline, list) and isinstance(candidate, list):
        base_by_id = {
            row.get("source_id"): row for row in baseline
            if isinstance(row, dict) and isinstance(row.get("source_id"), str)
        }
        candidate_by_id = {
            row.get("source_id"): row for row in candidate
            if isinstance(row, dict) and isinstance(row.get("source_id"), str)
        }
        if len(base_by_id) == len(baseline) and len(candidate_by_id) == len(candidate):
            rows = []
            for identity in sorted(set(base_by_id) | set(candidate_by_id)):
                rows.extend(_semantic_rows(
                    f"{path}[{identity}]",
                    base_by_id.get(identity, _DIFF_MISSING),
                    candidate_by_id.get(identity, _DIFF_MISSING),
                ))
            return rows
    baseline_mapping = baseline if isinstance(baseline, dict) else (
        {} if (baseline is None or baseline is _DIFF_MISSING)
        and isinstance(candidate, dict) else None
    )
    candidate_mapping = candidate if isinstance(candidate, dict) else (
        {} if (candidate is None or candidate is _DIFF_MISSING)
        and isinstance(baseline, dict) else None
    )
    if baseline_mapping is not None and candidate_mapping is not None and (
        baseline_mapping or candidate_mapping
    ):
        rows = []
        for key in sorted(set(baseline_mapping) | set(candidate_mapping)):
            child_path = f"{path}.{key}" if path else key
            rows.extend(_semantic_rows(
                child_path,
                baseline_mapping.get(key, _DIFF_MISSING),
                candidate_mapping.get(key, _DIFF_MISSING),
            ))
        return rows
    if isinstance(baseline, list) and isinstance(candidate, list):
        rows = []
        for index in range(max(len(baseline), len(candidate))):
            base_value = baseline[index] if index < len(baseline) else _DIFF_MISSING
            candidate_value = candidate[index] if index < len(candidate) else _DIFF_MISSING
            rows.extend(_semantic_rows(f"{path}[{index}]", base_value, candidate_value))
        return rows
    unit, effect = _diff_metadata(path)
    return [{
        "path": path,
        "baseline": _display_diff_value(baseline),
        "candidate": _display_diff_value(candidate),
        "unit": unit,
        "engineering_effect": effect,
        "requires_review": True,
    }]


def scenario_diff(baseline: dict, candidate: dict) -> list[dict]:
    """Return a stable-ID semantic diff of two Scenario operating conditions.

    Equality is checked on exact values first.  Rounding is presentation-only
    and therefore cannot alter Scenario content identity.
    """
    return _semantic_rows(
        "operating_conditions",
        _scenario_conditions(copy.deepcopy(baseline)),
        _scenario_conditions(copy.deepcopy(candidate)),
    )


def create_scenario(
    design_revision: str | Path,
    *,
    name: str,
    operating_conditions: dict[str, Any],
    purpose: str,
) -> dict[str, Any]:
    if _contains_forbidden_key(operating_conditions):
        raise ProjectModelError(
            "SCENARIO_GEOMETRY_MUTATION",
            "geometry, terminal role/normal/size and patch shape require a Design revision",
        )
    design_path = Path(design_revision).resolve()
    root = _find_root_from_artifact(design_path)
    design_errors = validate_design_revision(design_path, projects_root=root)
    if design_errors:
        raise ProjectModelError("DESIGN_REVISION_INVALID", str(design_errors))
    design = _load_json(design_path, code="DESIGN_INVALID")
    identity_payload = {
        "design_id": design["design_id"],
        "design_revision_sha256": design["revision_sha256"],
        "operating_conditions": operating_conditions,
        "purpose": purpose,
    }
    scenario_id = f"scenario-{_digest_value(identity_payload)[:24]}"
    revisions = root / _STORE / "scenarios" / scenario_id / "revisions"
    parent = _latest_revision(revisions, "scenario.v1")
    revision_number = len(_revision_files(revisions, "scenario.v1")) + 1
    record: dict[str, Any] = {
        "schema_version": 1,
        "contract": "scenario.v1",
        "scenario_id": scenario_id,
        "revision_number": revision_number,
        "name": name,
        "purpose": purpose,
        "created_at": _now(),
        "design": {
            "path": _relative(root, design_path),
            "sha256": _artifact_sha(design_path),
            "design_id": design["design_id"],
            "revision_sha256": design["revision_sha256"],
        },
        "operating_conditions": copy.deepcopy(operating_conditions),
        "parent_revision": None if parent is None else {
            "path": _relative(root, parent), "sha256": _artifact_sha(parent),
        },
    }
    record["revision_sha256"] = _revision_sha(record, "revision_sha256")
    errors = _schema_errors("scenario.v1.schema.json", record)
    if errors:
        raise ProjectModelError("SCENARIO_SCHEMA_INVALID", "; ".join(errors))
    target = revisions / f"{revision_number:04d}-{record['revision_sha256']}.scenario.v1.json"
    _atomic_write(target, _json_bytes(record))
    return _response(target, record, root)


def create_case_identity(
    design_revision: str | Path,
    scenario_revision: str | Path,
    *,
    run_id: str,
    solver_profile: str,
    parent_run_id: str | None = None,
) -> dict[str, Any]:
    design_path = Path(design_revision).resolve()
    scenario_path = Path(scenario_revision).resolve()
    root = _find_root_from_artifact(design_path)
    if _find_root_from_artifact(scenario_path) != root:
        raise ProjectModelError("PROJECT_ROOT_MISMATCH", "design and scenario use different roots")
    design_errors = validate_design_revision(design_path, projects_root=root)
    scenario_errors = validate_scenario_revision(scenario_path, projects_root=root)
    if design_errors:
        raise ProjectModelError("DESIGN_REVISION_INVALID", str(design_errors))
    if scenario_errors:
        raise ProjectModelError("SCENARIO_REVISION_INVALID", str(scenario_errors))
    design = _load_json(design_path, code="DESIGN_INVALID")
    scenario = _load_json(scenario_path, code="SCENARIO_INVALID")
    if scenario["design"]["revision_sha256"] != design["revision_sha256"]:
        raise ProjectModelError("SCENARIO_DESIGN_MISMATCH", "scenario binds another design revision")
    identity_payload = {
        "requested_run_id": run_id,
        "design_revision_sha256": design["revision_sha256"],
        "scenario_revision_sha256": scenario["revision_sha256"],
        "solver_profile": solver_profile,
        "parent_run_id": parent_run_id,
    }
    content_run_id = f"run-{_digest_value(identity_payload)[:24]}"
    record: dict[str, Any] = {
        "schema_version": 1,
        "contract": "case_identity.v1",
        "run_id": content_run_id,
        "requested_run_id": run_id,
        "created_at": _now(),
        "design": {
            "path": _relative(root, design_path),
            "sha256": _artifact_sha(design_path),
            "revision_sha256": design["revision_sha256"],
            "design_id": design["design_id"],
        },
        "scenario": {
            "path": _relative(root, scenario_path),
            "sha256": _artifact_sha(scenario_path),
            "revision_sha256": scenario["revision_sha256"],
            "scenario_id": scenario["scenario_id"],
        },
        "solver_profile": solver_profile,
        "parent_run_id": parent_run_id,
    }
    record["identity_sha256"] = _revision_sha(record, "identity_sha256")
    errors = _schema_errors("case_identity.v1.schema.json", record)
    if errors:
        raise ProjectModelError("CASE_IDENTITY_SCHEMA_INVALID", "; ".join(errors))
    target = root / _STORE / "runs" / f"{content_run_id}.case_identity.v1.json"
    _atomic_write(target, _json_bytes(record))
    return _response(target, record, root)


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _validate_revision_document(
    path: Path,
    root: Path,
    *,
    schema_name: str,
    contract: str,
    hash_field: str,
    invalid_code: str,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    try:
        path = _inside(root, path, code="ARTIFACT_PATH_ESCAPE")
        value = _load_json(path, code=invalid_code)
    except ProjectModelError as exc:
        return None, [_issue(exc.code, str(exc))]
    issues = [_issue(invalid_code, message) for message in _schema_errors(schema_name, value)]
    if value.get("contract") == contract and value.get(hash_field) != _revision_sha(value, hash_field):
        issues.append(_issue(f"{contract.upper().replace('.', '_')}_HASH_MISMATCH", "self hash changed"))
    return value, issues


def validate_design_revision(
    path: str | Path, *, projects_root: str | Path,
) -> list[dict[str, str]]:
    root = Path(projects_root).resolve()
    value, issues = _validate_revision_document(
        Path(path), root, schema_name="design.v1.schema.json", contract="design.v1",
        hash_field="revision_sha256", invalid_code="DESIGN_REVISION_INVALID",
    )
    if value is None or issues:
        return issues
    try:
        geometry_path = _resolve_ref(root, value["geometry"]["path"], code="GEOMETRY_PATH_ESCAPE")
        if _artifact_sha(geometry_path) != value["geometry"]["sha256"]:
            issues.append(_issue("GEOMETRY_REVISION_CHANGED", "geometry bytes changed"))
        else:
            _validate_geometry(geometry_path)
    except ProjectModelError as exc:
        issues.append(_issue(exc.code, str(exc)))
    return issues


def validate_scenario_revision(
    path: str | Path, *, projects_root: str | Path,
) -> list[dict[str, str]]:
    root = Path(projects_root).resolve()
    value, issues = _validate_revision_document(
        Path(path), root, schema_name="scenario.v1.schema.json", contract="scenario.v1",
        hash_field="revision_sha256", invalid_code="SCENARIO_REVISION_INVALID",
    )
    if value is None or issues:
        return issues
    try:
        design_path = _resolve_ref(root, value["design"]["path"], code="DESIGN_PATH_ESCAPE")
        if _artifact_sha(design_path) != value["design"]["sha256"]:
            issues.append(_issue("DESIGN_REVISION_CHANGED", "design bytes changed"))
        else:
            design = _load_json(design_path, code="DESIGN_REVISION_INVALID")
            if design.get("revision_sha256") != value["design"]["revision_sha256"]:
                issues.append(_issue("DESIGN_REVISION_CHANGED", "design revision changed"))
            else:
                issues.extend(validate_design_revision(design_path, projects_root=root))
    except ProjectModelError as exc:
        issues.append(_issue(exc.code, str(exc)))
    return issues


def validate_case_identity(
    path: str | Path, *, projects_root: str | Path,
) -> list[dict[str, str]]:
    root = Path(projects_root).resolve()
    value, issues = _validate_revision_document(
        Path(path), root, schema_name="case_identity.v1.schema.json",
        contract="case_identity.v1", hash_field="identity_sha256",
        invalid_code="CASE_IDENTITY_INVALID",
    )
    if value is None or issues:
        return issues
    for label, validator in (
        ("DESIGN", validate_design_revision), ("SCENARIO", validate_scenario_revision),
    ):
        reference = value[label.lower()]
        try:
            target = _resolve_ref(root, reference["path"], code=f"{label}_PATH_ESCAPE")
            if _artifact_sha(target) != reference["sha256"]:
                issues.append(_issue(f"{label}_REVISION_CHANGED", f"{label.lower()} bytes changed"))
                continue
            target_value = _load_json(target, code=f"{label}_REVISION_INVALID")
            if target_value.get("revision_sha256") != reference["revision_sha256"]:
                issues.append(_issue(f"{label}_REVISION_CHANGED", f"{label.lower()} revision changed"))
                continue
            issues.extend(validator(target, projects_root=root))
        except ProjectModelError as exc:
            issues.append(_issue(exc.code, str(exc)))
    return issues


def _case_location(case_dir: str | Path, root: Path) -> tuple[Path, str, str]:
    case = _inside(root, Path(case_dir), code="LEGACY_CASE_PATH_ESCAPE")
    if not case.is_dir():
        raise ProjectModelError("LEGACY_CASE_NOT_FOUND", str(case))
    relative = _relative(root, case)
    if relative == _STORE or relative.startswith(f"{_STORE}/"):
        raise ProjectModelError("LEGACY_CASE_PATH_INVALID", relative)
    legacy_id = f"legacy-{_digest_value({'case_path': relative})[:24]}"
    return case, relative, legacy_id


def _case_inventory(case: Path) -> tuple[list[dict[str, Any]], str]:
    rows: list[dict[str, Any]] = []
    for path in sorted(case.rglob("*")):
        if not path.is_file():
            continue
        resolved = _inside(case, path, code="LEGACY_CASE_FILE_ESCAPE")
        payload = resolved.read_bytes()
        rows.append({
            "path": resolved.relative_to(case).as_posix(),
            "size": len(payload),
            "sha256": _digest_bytes(payload),
        })
    return rows, _digest_value(rows)


def _legacy_directory(root: Path, legacy_id: str) -> Path:
    return root / _STORE / "legacy_cases" / legacy_id


def import_legacy_case(
    case_dir: str | Path, *, projects_root: str | Path,
) -> dict[str, Any]:
    """Inventory a legacy case in metadata storage without touching the case."""
    root = Path(projects_root).resolve()
    case, relative, legacy_id = _case_location(case_dir, root)
    inventory, inventory_sha = _case_inventory(case)
    target = (
        _legacy_directory(root, legacy_id) / "snapshots"
        / f"{inventory_sha}.legacy_case.v1.json"
    )
    if target.exists():
        record = _load_json(target, code="LEGACY_SIDECAR_INVALID")
    else:
        record = {
            "schema_version": 1,
            "contract": "legacy_case.v1",
            "legacy_case_id": legacy_id,
            "created_at": _now(),
            "case_path": relative,
            "inventory_sha256": inventory_sha,
            "inventory": inventory,
            "status": "legacy_unlinked",
            "scenario_comparison_eligible": False,
            "design_citation_eligible": False,
        }
        _atomic_write(target, _json_bytes(record))
    result = copy.deepcopy(record)
    result["sidecar_path"] = str(target.resolve())
    return result


def _case_link_path(root: Path, legacy_id: str) -> Path:
    return _legacy_directory(root, legacy_id) / "run_identity_link.v1.json"


def link_run_identity(
    case_dir: str | Path, identity_path: str | Path,
) -> dict[str, Any]:
    """Bind a case to one frozen Run identity using an external sidecar."""
    identity = Path(identity_path).resolve()
    root = _find_root_from_artifact(identity)
    case, relative, legacy_id = _case_location(case_dir, root)
    identity_issues = validate_case_identity(identity, projects_root=root)
    if identity_issues:
        raise ProjectModelError("RUN_IDENTITY_INVALID", str(identity_issues))
    identity_value = _load_json(identity, code="RUN_IDENTITY_INVALID")
    _, inventory_sha = _case_inventory(case)
    target = _case_link_path(root, legacy_id)
    if target.exists():
        existing = _load_json(target, code="RUN_IDENTITY_LINK_INVALID")
        if (
            existing.get("case_identity_path") == _relative(root, identity)
            and existing.get("case_identity_sha256") == _artifact_sha(identity)
        ):
            result = copy.deepcopy(existing)
            result["sidecar_path"] = str(target.resolve())
            return result
        raise ProjectModelError(
            "CASE_ALREADY_LINKED", "an immutable case link already exists",
        )
    record = {
        "schema_version": 1,
        "contract": "run_identity_link.v1",
        "legacy_case_id": legacy_id,
        "case_path": relative,
        "linked_at": _now(),
        "linked_case_inventory_sha256": inventory_sha,
        "case_identity_path": _relative(root, identity),
        "case_identity_sha256": _artifact_sha(identity),
        "design_revision_sha256": identity_value["design"]["revision_sha256"],
        "scenario_revision_sha256": identity_value["scenario"]["revision_sha256"],
        "case_identity_status": "LINKED",
    }
    _atomic_write(target, _json_bytes(record))
    result = copy.deepcopy(record)
    result["sidecar_path"] = str(target.resolve())
    return result


def validate_run_identity(
    case_dir: str | Path, *, projects_root: str | Path,
) -> list[dict[str, str]]:
    """Validate the frozen identity currently linked to a case."""
    root = Path(projects_root).resolve()
    try:
        _, relative, legacy_id = _case_location(case_dir, root)
        link_path = _case_link_path(root, legacy_id)
        if not link_path.is_file():
            return [_issue("RUN_IDENTITY_NOT_LINKED", "case has no Run identity link")]
        link = _load_json(link_path, code="RUN_IDENTITY_LINK_INVALID")
        if (
            link.get("contract") != "run_identity_link.v1"
            or link.get("case_path") != relative
        ):
            return [_issue("RUN_IDENTITY_CHANGED", "case link metadata changed")]
        identity = _resolve_ref(
            root, link.get("case_identity_path"), code="RUN_IDENTITY_PATH_ESCAPE",
        )
        if _artifact_sha(identity) != link.get("case_identity_sha256"):
            return [_issue("RUN_IDENTITY_CHANGED", "Run identity bytes changed")]
        identity_value = _load_json(identity, code="RUN_IDENTITY_INVALID")
        if (
            identity_value.get("design", {}).get("revision_sha256")
            != link.get("design_revision_sha256")
            or identity_value.get("scenario", {}).get("revision_sha256")
            != link.get("scenario_revision_sha256")
            or validate_case_identity(identity, projects_root=root)
        ):
            return [_issue("RUN_IDENTITY_CHANGED", "Run identity references changed")]
        return validate_case_identity_lifecycle(identity, projects_root=root)
    except ProjectModelError as exc:
        return [_issue("RUN_IDENTITY_CHANGED", str(exc))]


def validate_case_identity_lifecycle(
    identity_path: str | Path, *, projects_root: str | Path,
) -> list[dict[str, str]]:
    """Report a valid frozen Run whose Design now has a newer revision."""
    root = Path(projects_root).resolve()
    try:
        identity = _inside(
            root, Path(identity_path), code="RUN_IDENTITY_PATH_ESCAPE",
        )
        value = _load_json(identity, code="RUN_IDENTITY_INVALID")
        design_id = value["design"]["design_id"]
        latest_path = _latest_revision(
            root / _STORE / "designs" / design_id / "revisions", "design.v1",
        )
        if latest_path is None:
            return [_issue("RUN_IDENTITY_CHANGED", "linked Design no longer exists")]
        latest = _load_json(latest_path, code="DESIGN_REVISION_INVALID")
        if latest.get("revision_sha256") != value["design"].get("revision_sha256"):
            return [_issue(
                "SUPERSEDED_DESIGN_REVISION",
                "a newer immutable Design revision exists",
            )]
        return []
    except (KeyError, TypeError, ProjectModelError) as exc:
        return [_issue("RUN_IDENTITY_CHANGED", str(exc))]


def case_identity_summary(
    case_dir: str | Path, *, projects_root: str | Path,
) -> dict[str, Any]:
    """Return display-safe identity eligibility without modifying the case."""
    issues = validate_run_identity(case_dir, projects_root=projects_root)
    if not issues:
        return {
            "case_identity_status": "LINKED",
            "scenario_comparison_eligible": True,
            "design_citation_eligible": True,
        }
    code = issues[0]["code"]
    status = "legacy_unlinked" if code == "RUN_IDENTITY_NOT_LINKED" else code
    return {
        "case_identity_status": status,
        "scenario_comparison_eligible": False,
        "design_citation_eligible": False,
        "case_identity_issues": issues,
    }
