"""Immutable, fail-closed authority binding for numerical validation studies."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

import cfd_numerical_sensitivity_job as sensitivity_job
from geometry_v2 import migrate_geometry


CONTRACT = "validation_anchor.v1"
ROLES = {"gci_fine", "temporal_fine", "field_authority"}
ARTIFACT_ROLES = (
    "geometry",
    "surface_manifest",
    "mesh_manifest",
    "thermal_input",
    "run_manifest",
    "result_manifest",
    "result_source",
    "occupied_selector",
)


class ValidationAnchorError(ValueError):
    def __init__(self, code, detail=""):
        self.code = str(code)
        super().__init__(self.code + (f": {detail}" if detail else ""))


def _canonical_sha256(value):
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path, code):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationAnchorError(code, str(path)) from exc
    if not isinstance(payload, dict):
        raise ValidationAnchorError(code, str(path))
    return payload


def _resolve_reference(base, value, default, code):
    raw = value if value not in (None, "") else default
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = Path(base) / path
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise ValidationAnchorError(code, str(path)) from exc


def _require_recorded_hash(document, path, candidates, code, *, accepted_hashes=None):
    expected = None
    for candidate in candidates:
        cursor = document
        for key in candidate:
            cursor = cursor.get(key) if isinstance(cursor, dict) else None
        if cursor not in (None, ""):
            expected = cursor
            break
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValidationAnchorError(code, "recorded SHA-256 is missing")
    actuals = {_file_sha256(path)}
    actuals.update(str(value).lower() for value in (accepted_hashes or []))
    if expected.lower() not in actuals:
        raise ValidationAnchorError(code, str(path))


def _occ_normalised_geometry_sha256(path):
    """Reproduce the transient geometry.v2 bytes hashed by the OCC worker."""
    source = _read_json(path, "ANCHOR_GEOMETRY_INVALID")
    geometry = migrate_geometry(
        source, source_path=source.get("source") or str(Path(path).resolve()),
    )
    geometry["occ_source_path"] = str(Path(path).resolve())
    payload = (json.dumps(geometry, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _artifact(role, path):
    path = Path(path).resolve(strict=True)
    return {"role": role, "path": str(path), "sha256": _file_sha256(path)}


def _case_documents(case_dir, selector_path):
    case = Path(case_dir).expanduser().resolve(strict=True)
    if not case.is_dir():
        raise ValidationAnchorError("ANCHOR_CASE_INVALID", str(case))
    surface_path = case / "surface_manifest.json"
    mesh_path = case / "mesh_manifest.json"
    thermal_path = case / "thermal_input.json"
    run_path = case / "run_manifest.json"
    result_path = case / "result_manifest.json"
    surface = _read_json(surface_path, "ANCHOR_SURFACE_INVALID")
    mesh = _read_json(mesh_path, "ANCHOR_MESH_INVALID")
    thermal = _read_json(thermal_path, "ANCHOR_THERMAL_INVALID")
    run = _read_json(run_path, "ANCHOR_RUN_INVALID")
    result = _read_json(result_path, "ANCHOR_RESULT_INVALID")

    surface_source = surface.get("source") if isinstance(surface.get("source"), dict) else {}
    surface_input = surface.get("input") if isinstance(surface.get("input"), dict) else {}
    geometry_path = _resolve_reference(
        surface_path.parent,
        surface_source.get("geometry_path") or surface_input.get("geometry_path"),
        "geometry.json",
        "ANCHOR_GEOMETRY_INVALID",
    )
    _require_recorded_hash(
        surface,
        geometry_path,
        (("source", "geometry_sha256"), ("input", "geometry_sha256")),
        "ANCHOR_GEOMETRY_LINK_MISMATCH",
        accepted_hashes=(_occ_normalised_geometry_sha256(geometry_path),),
    )
    mesh_input = mesh.get("input") if isinstance(mesh.get("input"), dict) else {}
    _require_recorded_hash(
        mesh,
        surface_path,
        (("input", "surface_manifest_sha256"),),
        "ANCHOR_SURFACE_LINK_MISMATCH",
    )
    _require_recorded_hash(
        thermal,
        mesh_path,
        (("mesh_manifest_sha256",), ("input", "mesh_manifest_sha256")),
        "ANCHOR_MESH_LINK_MISMATCH",
    )
    _require_recorded_hash(
        run,
        thermal_path,
        (("input", "thermal_input_sha256"),),
        "ANCHOR_RUN_THERMAL_LINK_MISMATCH",
    )
    if mesh_input and run.get("input", {}).get("mesh_manifest_sha256"):
        _require_recorded_hash(
            run,
            mesh_path,
            (("input", "mesh_manifest_sha256"),),
            "ANCHOR_RUN_MESH_LINK_MISMATCH",
        )
    for path, candidates, code in (
        (run_path, (("run_manifest_sha256",),), "ANCHOR_RESULT_RUN_LINK_MISMATCH"),
        (mesh_path, (("mesh_manifest_sha256",),), "ANCHOR_RESULT_MESH_LINK_MISMATCH"),
        (thermal_path, (("thermal_input_sha256",),), "ANCHOR_RESULT_THERMAL_LINK_MISMATCH"),
    ):
        _require_recorded_hash(result, path, candidates, code)

    result_source = result.get("source") if isinstance(result.get("source"), dict) else {}
    source_path = _resolve_reference(
        case, result_source.get("path"), None, "ANCHOR_RESULT_SOURCE_INVALID"
    )
    _require_recorded_hash(
        result,
        source_path,
        (("source", "sha256"),),
        "ANCHOR_RESULT_SOURCE_LINK_MISMATCH",
    )
    selector_path = Path(selector_path).expanduser().resolve(strict=True)
    selector = _read_json(selector_path, "ANCHOR_SELECTOR_INVALID")
    try:
        normalised_selector = sensitivity_job._validate_stored_selector(selector)
    except sensitivity_job.NumericalSensitivityJobInputError as exc:
        raise ValidationAnchorError("ANCHOR_SELECTOR_INVALID", str(exc)) from exc

    artifacts = [
        _artifact("geometry", geometry_path),
        _artifact("surface_manifest", surface_path),
        _artifact("mesh_manifest", mesh_path),
        _artifact("thermal_input", thermal_path),
        _artifact("run_manifest", run_path),
        _artifact("result_manifest", result_path),
        _artifact("result_source", source_path),
        _artifact("occupied_selector", selector_path),
    ]
    physical_settings = {
        "settings": thermal.get("settings") if isinstance(thermal.get("settings"), dict) else {},
        "numerics": thermal.get("numerics") if isinstance(thermal.get("numerics"), dict) else {},
        "terminals": thermal.get("terminals") if isinstance(thermal.get("terminals"), list) else [],
        "heat_sources": (
            thermal.get("heat_sources")
            if isinstance(thermal.get("heat_sources"), list) else []
        ),
    }
    solver = run.get("solver") if isinstance(run.get("solver"), dict) else {}
    solver_identity = {
        "engine": run.get("engine"),
        "application": solver.get("application"),
        "openfoam_version": solver.get("openfoam_version"),
        "numerical_profile": (run.get("effective_numerics") or {}).get("profile"),
        "convection_order": (run.get("effective_numerics") or {}).get("convection_order"),
    }
    return {
        "case": case,
        "artifacts": artifacts,
        "physical_settings": physical_settings,
        "physical_settings_sha256": _canonical_sha256(physical_settings),
        "solver_identity": solver_identity,
        "selector_sha256": normalised_selector["selector_sha256"],
    }


def _binding_payload(payload):
    return {
        "case_path": payload["case_path"],
        "artifacts": payload["artifacts"],
        "physical_settings_sha256": payload["physical_settings_sha256"],
        "solver_identity": payload["solver_identity"],
        "selector_sha256": payload["selector_sha256"],
    }


def _atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n", delete=False,
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp",
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def create_validation_anchor(case_dir, *, selector_path, role, output_path):
    """Create one role-bound document whose identity comes from current bytes."""
    if role not in ROLES:
        raise ValidationAnchorError("ANCHOR_ROLE_INVALID", str(role))
    output = Path(output_path).expanduser().resolve(strict=False)
    if output.exists():
        existing = _read_json(output, "ANCHOR_OUTPUT_IMMUTABLE")
        existing_issues = validate_validation_anchor(
            output, expected_case=case_dir,
        )
        if not existing_issues and existing.get("role") == role:
            return {
                "anchor_id": existing["anchor_id"],
                "path": str(output),
                "sha256": _file_sha256(output),
                "existing": True,
            }
        raise ValidationAnchorError("ANCHOR_OUTPUT_IMMUTABLE", str(output))
    documents = _case_documents(case_dir, selector_path)
    payload = {
        "schema_version": 1,
        "contract": CONTRACT,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "BOUND_NOT_CITABLE",
        "role": role,
        "case_path": str(documents["case"]),
        "artifacts": documents["artifacts"],
        "physical_settings": documents["physical_settings"],
        "physical_settings_sha256": documents["physical_settings_sha256"],
        "solver_identity": documents["solver_identity"],
        "selector_sha256": documents["selector_sha256"],
    }
    payload["binding_sha256"] = _canonical_sha256(_binding_payload(payload))
    payload["anchor_id"] = "anchor-" + payload["binding_sha256"][:16]
    _atomic_json(output, payload)
    return {
        "anchor_id": payload["anchor_id"],
        "path": str(output),
        "sha256": _file_sha256(output),
        "existing": False,
    }


def validate_validation_anchor(anchor_path, *, expected_case=None):
    """Re-read every bound byte; never trust the document's status field."""
    issues = []
    anchor_path = Path(anchor_path).expanduser().resolve(strict=False)
    try:
        payload = _read_json(anchor_path, "ANCHOR_DOCUMENT_INVALID")
    except ValidationAnchorError as exc:
        return [{"code": exc.code, "message": str(exc)}]
    if payload.get("contract") != CONTRACT or payload.get("schema_version") != 1:
        issues.append({"code": "ANCHOR_CONTRACT_INVALID", "message": str(anchor_path)})
    if payload.get("role") not in ROLES:
        issues.append({"code": "ANCHOR_ROLE_INVALID", "message": str(payload.get("role"))})
    case_path = Path(str(payload.get("case_path") or "")).expanduser().resolve(strict=False)
    if expected_case is not None:
        expected = Path(expected_case).expanduser().resolve(strict=False)
        if case_path != expected:
            issues.append({
                "code": "ANCHOR_CASE_MISMATCH",
                "message": f"expected {expected}; anchored {case_path}",
            })
    rows = payload.get("artifacts")
    if not isinstance(rows, list) or [row.get("role") for row in rows if isinstance(row, dict)] != list(ARTIFACT_ROLES):
        issues.append({"code": "ANCHOR_ARTIFACT_SET_INVALID", "message": "artifact roles"})
        rows = rows if isinstance(rows, list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        path = Path(str(row.get("path") or "")).expanduser().resolve(strict=False)
        try:
            actual = _file_sha256(path)
        except OSError:
            issues.append({"code": "ANCHOR_ARTIFACT_MISSING", "message": str(path)})
            continue
        if actual != row.get("sha256"):
            issues.append({
                "code": "ANCHOR_ARTIFACT_HASH_MISMATCH",
                "message": f"{row.get('role')}: {path}",
            })
    try:
        if payload.get("binding_sha256") != _canonical_sha256(_binding_payload(payload)):
            issues.append({"code": "ANCHOR_BINDING_HASH_MISMATCH", "message": str(anchor_path)})
        expected_id = "anchor-" + str(payload.get("binding_sha256") or "")[:16]
        if payload.get("anchor_id") != expected_id:
            issues.append({"code": "ANCHOR_ID_MISMATCH", "message": str(anchor_path)})
        if payload.get("physical_settings_sha256") != _canonical_sha256(payload.get("physical_settings")):
            issues.append({"code": "ANCHOR_PHYSICAL_SETTINGS_MISMATCH", "message": str(anchor_path)})
    except (KeyError, TypeError, ValueError):
        issues.append({"code": "ANCHOR_BINDING_INVALID", "message": str(anchor_path)})
    selector_row = next(
        (row for row in rows if isinstance(row, dict) and row.get("role") == "occupied_selector"),
        None,
    )
    if selector_row is not None:
        try:
            selector = _read_json(selector_row["path"], "ANCHOR_SELECTOR_INVALID")
            normalised = sensitivity_job._validate_stored_selector(selector)
            if normalised["selector_sha256"] != payload.get("selector_sha256"):
                issues.append({"code": "ANCHOR_SELECTOR_HASH_MISMATCH", "message": str(anchor_path)})
        except (ValidationAnchorError, sensitivity_job.NumericalSensitivityJobInputError) as exc:
            issues.append({"code": "ANCHOR_SELECTOR_INVALID", "message": str(exc)})
    deduplicated = []
    seen = set()
    for issue in issues:
        key = (issue["code"], issue["message"])
        if key not in seen:
            seen.add(key)
            deduplicated.append(issue)
    return deduplicated


def anchor_reference(anchor_path, *, expected_case=None, expected_role=None):
    """Return a pinned consumer reference only after live validation."""
    path = Path(anchor_path).expanduser().resolve(strict=True)
    issues = validate_validation_anchor(path, expected_case=expected_case)
    payload = _read_json(path, "ANCHOR_DOCUMENT_INVALID")
    if expected_role is not None and payload.get("role") != expected_role:
        issues.append({
            "code": "ANCHOR_ROLE_MISMATCH",
            "message": f"expected {expected_role}; anchored {payload.get('role')}",
        })
    if issues:
        raise ValidationAnchorError(issues[0]["code"], issues[0]["message"])
    return {
        "anchor_id": payload["anchor_id"],
        "role": payload["role"],
        "path": str(path),
        "sha256": _file_sha256(path),
        "binding_sha256": payload["binding_sha256"],
    }
