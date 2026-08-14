"""Fail-closed single-PC working-validation evidence contract.

The registry below is deliberately code-owned.  It is not a user-editable
``sources.json``: each check can only inspect its fixed, named artifact and
the check-specific facts it must contain.  This is a non-adversarial local
filesystem model.  It detects missing, stale, malformed, substituted, and
wrapper-only evidence, but does not claim to resist a user who rewrites the
application code and every referenced local artifact.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator


CHECK_IDS = (
    "code_baseline",
    "filesystem_io",
    "serial_environment",
    "working_room_e2e",
    "real_dxf_screening",
    "restart_integrity",
    "exact_heat_verification",
    "limited_numerical_spotchecks",
)
WORKING_CHECK_IDS = CHECK_IDS[:6]
_MAX_SOURCE_AGE = timedelta(days=7)
_HEX = re.compile(r"^[0-9a-f]{64}$")
_RFC3339_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_CACHE_PARTS = frozenset({".cache", "cache", ".pytest_cache", "__pycache__", "tmp", "temp", ".tmp"})


# Fixed internal working-validation paths are the authoritative artifact
# registry.  Later task stages must
# produce these actual, check-specific contracts before the real projects root
# can advance from BLOCKED.
AUTHORITATIVE_ARTIFACTS: dict[str, tuple[str, str]] = {
    "code_baseline": (
        "_working_validation/evidence/code_baseline.json",
        "working_validation.code_baseline.v1",
    ),
    "filesystem_io": (
        "_working_validation/evidence/filesystem_io.json",
        "working_validation.filesystem_io.v1",
    ),
    "serial_environment": ("capability_manifest.json", "runtime_capability.v1"),
    "working_room_e2e": (
        "_working_validation/evidence/working_room_e2e.json",
        "working_validation.working_room_e2e.v1",
    ),
    "real_dxf_screening": (
        "_working_validation/evidence/real_dxf_screening.json",
        "working_validation.real_dxf_screening.v1",
    ),
    "restart_integrity": (
        "_working_validation/evidence/restart_integrity.json",
        "working_validation.restart_integrity.v1",
    ),
    "exact_heat_verification": (
        "_working_validation/evidence/exact_heat_verification.json",
        "working_validation.exact_heat_verification.v1",
    ),
    "limited_numerical_spotchecks": (
        "_working_validation/evidence/limited_numerical_spotchecks.json",
        "working_validation.limited_numerical_spotchecks.v1",
    ),
}

# Check documents cannot nominate alternative sources.  The only intentionally
# dynamic list is the I/O authoritative-case inventory, whose fixed path and
# explicit linkage contract are validated by ``scripts.io_acceptance``.
_FIXED_LINKS: dict[str, dict[str, tuple[str, str | None]]] = {
    "code_baseline": {
        "baseline": ("_working_validation/evidence/vv_baseline.json", "vv_baseline.v1"),
    },
    "filesystem_io": {
        "io_acceptance": ("_working_validation/evidence/io_acceptance.json", "io_acceptance.v1"),
    },
    "working_room_e2e": {
        "mesh_manifest.json": ("_body_solver/room-001/mesh_manifest.json", None),
        "run_manifest.json": ("_body_solver/room-001/run_manifest.json", None),
        "result_manifest.json": ("_body_solver/room-001/result_manifest.json", None),
        "log.checkMesh": ("_body_solver/room-001/log.checkMesh", None),
        "log.buoyantBoussinesqPimpleFoam": ("_body_solver/room-001/log.buoyantBoussinesqPimpleFoam", None),
        "12/T": ("_body_solver/room-001/12/T", None),
        "12/U": ("_body_solver/room-001/12/U", None),
        "12/phi": ("_body_solver/room-001/12/phi", None),
        "12/V": ("_body_solver/room-001/12/V", None),
        "results/room-001.vtu": ("_body_solver/room-001/results/room-001.vtu", None),
        "reports/room-001.html": ("_body_solver/room-001/reports/room-001.html", None),
    },
    "real_dxf_screening": {
        "source_dxf": ("_field_jobs/room-001.dxf", None),
        "geometry": ("_field_jobs/room-001.geometry.json", "geometry.v2"),
    },
    "restart_integrity": {
        "restart_input": ("_body_solver/room-001/thermal_restart_input.json", "thermal_restart_input.v1"),
        "run_manifest": ("_body_solver/room-001/run_manifest.json", "run_manifest.v1"),
    },
    "exact_heat_verification": {
        "result_manifest": ("_body_solver/room-001/result_manifest.json", "result_manifest.v1"),
        "heat_report": ("_working_validation/evidence/exact_heat_report.json", "exact_heat_report.v1"),
    },
    "limited_numerical_spotchecks": {
        "result_manifest": ("_body_solver/room-001/result_manifest.json", "result_manifest.v1"),
        "spotcheck_report": ("_working_validation/evidence/limited_numerical_spotchecks_report.json", "limited_numerical_spotchecks_report.v1"),
    },
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_hex(value: object) -> bool:
    return isinstance(value, str) and bool(_HEX.fullmatch(value))


def _utc_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not _RFC3339_UTC.fullmatch(value):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _fresh(value: object) -> bool:
    stamp = _utc_timestamp(value)
    now = datetime.now(timezone.utc)
    return bool(stamp and stamp <= now + timedelta(minutes=5) and now - stamp <= _MAX_SOURCE_AGE)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _blocked_path(projects_root: Path, candidate: Path, output: Path | None) -> str | None:
    try:
        relative = candidate.resolve().relative_to(projects_root)
    except ValueError:
        return "SOURCE_OUTSIDE_PROJECTS_ROOT"
    parts = [part.casefold() for part in relative.parts]
    if any(part in _CACHE_PARTS or part.endswith(".tmp") for part in parts):
        return "SOURCE_CACHE_OR_TEMP_FORBIDDEN"
    name = candidate.name.casefold()
    if (("working_validation" in name or "working-validation" in name)
            and candidate.suffix.casefold() in {".json", ".html", ".htm"}):
        return "SOURCE_PUBLISHED_WORKING_VALIDATION_FORBIDDEN"
    if output is not None and candidate.resolve() == output.resolve():
        return "OUTPUT_ALIAS"
    return None


def _resolve_fixed(projects_root: Path, relative: str, output: Path | None) -> tuple[Path | None, str | None]:
    candidate = (projects_root / relative).resolve()
    # Registry entries are structural and known safe; only an explicit output
    # alias can make one unsafe.
    if output is not None and candidate == output.resolve():
        return None, "OUTPUT_ALIAS"
    try:
        candidate.relative_to(projects_root)
    except ValueError:
        return None, "SOURCE_OUTSIDE_PROJECTS_ROOT"
    return candidate, None


def _link(
    projects_root: Path, value: object, *, expected_contract: str | None = None,
    output: Path | None = None, allowed_prefix: str | None = None,
) -> tuple[dict[str, Any] | None, Path | None, dict[str, str], list[str]]:
    if not isinstance(value, dict) or set(value) - {"path", "sha256", "contract"}:
        return None, None, {}, ["LINK_INVALID"]
    if not isinstance(value.get("path"), str) or not _is_hex(value.get("sha256")):
        return None, None, {}, ["LINK_IDENTITY_INVALID"]
    if expected_contract is not None and value.get("contract") != expected_contract:
        return None, None, {}, ["LINK_CONTRACT_INVALID"]
    candidate = (projects_root / value["path"]).resolve()
    blocked = _blocked_path(projects_root, candidate, output)
    if blocked:
        return None, None, {}, [blocked]
    if allowed_prefix is not None:
        prefix = (projects_root / allowed_prefix).resolve()
        try:
            candidate.relative_to(prefix)
        except ValueError:
            return None, None, {}, ["LINK_CASE_SCOPE_INVALID"]
    if not candidate.is_file():
        return None, None, {}, ["LINK_MISSING"]
    actual = _sha256_file(candidate)
    if actual != value["sha256"]:
        return None, None, {}, ["LINK_REHASH_MISMATCH"]
    document = _read_json(candidate)
    if expected_contract is not None and (document is None or document.get("contract") != expected_contract):
        return None, None, {}, ["LINK_SOURCE_CONTRACT_INVALID"]
    return document, candidate, {str(candidate): actual}, []


def _fixed_link(
    projects_root: Path, document: dict[str, Any], key: str, *, check_id: str,
    output: Path | None = None, allowed_prefix: str | None = None,
) -> tuple[dict[str, Any] | None, Path | None, dict[str, str], list[str]]:
    expected_relative, expected_contract = _FIXED_LINKS[check_id][key]
    value = document.get(key)
    if not isinstance(value, dict) or value.get("path") != expected_relative:
        return None, None, {}, ["LINK_FIXED_PATH_INVALID"]
    return _link(
        projects_root, value, expected_contract=expected_contract, output=output,
        allowed_prefix=allowed_prefix,
    )


def _common_document(document: dict[str, Any], check_id: str, contract: str) -> list[str]:
    blockers: list[str] = []
    if document.get("contract") != contract:
        blockers.append("CONTRACT_INVALID")
    if document.get("check_id") != check_id:
        blockers.append("CHECK_ID_INVALID")
    if document.get("target_identity") != "single_pc_serial_current_user":
        blockers.append("TARGET_IDENTITY_INVALID")
    if not _fresh(document.get("created_at")):
        blockers.append("SOURCE_STALE_OR_TIMESTAMP_INVALID")
    return blockers


def _validate_code_baseline(projects_root: Path, document: dict[str, Any], output: Path | None) -> tuple[list[str], dict[str, str]]:
    blockers = _common_document(document, "code_baseline", AUTHORITATIVE_ARTIFACTS["code_baseline"][1])
    baseline, _, hashes, link_blockers = _fixed_link(
        projects_root, document, "baseline", check_id="code_baseline", output=output,
    )
    blockers.extend(link_blockers)
    if baseline is None:
        return blockers, hashes
    executable = baseline.get("python_executable")
    executable_path = Path(executable) if isinstance(executable, str) and executable else None
    if (executable_path is None or not executable_path.is_file()
            or baseline.get("python_executable_sha256") != _sha256_file(executable_path)
            or baseline.get("python_version") != sys.version
            or baseline.get("python_architecture") != platform.architecture()[0]
            or not _is_hex(baseline.get("installed_distribution_snapshot_sha256"))
            or not isinstance(baseline.get("git_head"), str) or not baseline.get("git_head")):
        blockers.append("BASELINE_RUNTIME_IDENTITY_INVALID")
    return blockers, hashes


def _validate_filesystem_io(projects_root: Path, document: dict[str, Any], output: Path | None) -> tuple[list[str], dict[str, str]]:
    blockers = _common_document(document, "filesystem_io", AUTHORITATIVE_ARTIFACTS["filesystem_io"][1])
    acceptance, _, hashes, link_blockers = _fixed_link(
        projects_root, document, "io_acceptance", check_id="filesystem_io", output=output,
    )
    blockers.extend(link_blockers)
    if acceptance is None:
        return blockers, hashes
    inventory_path = acceptance.get("inventory_path")
    inventory_sha = acceptance.get("inventory_sha256")
    inventory = (projects_root / inventory_path).resolve() if isinstance(inventory_path, str) else None
    if (acceptance.get("status") != "PASS" or not _is_hex(inventory_sha) or inventory is None
            or _blocked_path(projects_root, inventory, output) or not inventory.is_file()
            or _sha256_file(inventory) != inventory_sha):
        blockers.append("IO_ACCEPTANCE_SEMANTICS_INVALID")
    probes = acceptance.get("artifact_probes")
    if not isinstance(probes, list) or not probes or any(
        not isinstance(row, dict) or row.get("status") != "PASS" or row.get("read") is not True
        or not _is_hex(row.get("sha256")) for row in probes
    ):
        blockers.append("IO_ARTIFACT_PROBES_INVALID")
    if inventory is not None and inventory.is_file() and _is_hex(inventory_sha) and _sha256_file(inventory) == inventory_sha:
        hashes[str(inventory)] = inventory_sha
    return blockers, hashes


def _validate_serial_environment(projects_root: Path, document: dict[str, Any], output: Path | None) -> tuple[list[str], dict[str, str]]:
    del projects_root, output
    blockers: list[str] = []
    if document.get("contract") != AUTHORITATIVE_ARTIFACTS["serial_environment"][1]:
        blockers.append("CONTRACT_INVALID")
    if not _fresh(document.get("created_at")):
        blockers.append("SOURCE_STALE_OR_TIMESTAMP_INVALID")
    cpu = document.get("cpu") if isinstance(document.get("cpu"), dict) else {}
    baseline = document.get("serial_baseline") if isinstance(document.get("serial_baseline"), dict) else {}
    if (document.get("serial_runtime_ready") is not True or document.get("serial_only") is not True
            or cpu.get("effective_logical_count") != 1 or baseline.get("status") != "PASS"
            or not _is_hex(baseline.get("solver_log_sha256"))):
        blockers.append("SERIAL_ENVIRONMENT_SEMANTICS_INVALID")
    return blockers, {}


_CASE_ARTIFACTS = frozenset({
    "mesh_manifest.json", "run_manifest.json", "result_manifest.json", "log.checkMesh",
    "log.buoyantBoussinesqPimpleFoam", "12/T", "12/U", "12/phi", "12/V",
    "results/room-001.vtu", "reports/room-001.html",
})


def _validate_working_room(projects_root: Path, document: dict[str, Any], output: Path | None) -> tuple[list[str], dict[str, str]]:
    blockers = _common_document(document, "working_room_e2e", AUTHORITATIVE_ARTIFACTS["working_room_e2e"][1])
    case_path = document.get("case_path")
    if document.get("case_id") != "room-001" or case_path != "_body_solver/room-001":
        return blockers + ["CASE_IDENTITY_INVALID"], {}
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != _CASE_ARTIFACTS:
        return blockers + ["CASE_ARTIFACT_SET_INVALID"], {}
    hashes: dict[str, str] = {}
    loaded: dict[str, dict[str, Any] | None] = {}
    for name in sorted(_CASE_ARTIFACTS):
        holder = {name: artifacts[name]}
        parsed, _, item_hashes, link_blockers = _fixed_link(
            projects_root, holder, name, check_id="working_room_e2e", output=output, allowed_prefix=case_path,
        )
        blockers.extend(link_blockers)
        hashes.update(item_hashes)
        loaded[name] = parsed
    mesh = loaded.get("mesh_manifest.json") or {}
    run = loaded.get("run_manifest.json") or {}
    result = loaded.get("result_manifest.json") or {}
    if (mesh.get("contract") != "mesh_manifest.v1" or mesh.get("status") != "PASS"
            or run.get("contract") != "run_manifest.v1" or run.get("status") != "PASS"
            or run.get("requested_ranks") != 1 or result.get("contract") != "result_manifest.v1"):
        blockers.append("CASE_MANIFEST_SEMANTICS_INVALID")
    vtu = artifacts.get("results/room-001.vtu", {})
    html = artifacts.get("reports/room-001.html", {})
    if (not isinstance(result.get("source"), dict) or result["source"].get("sha256") != vtu.get("sha256")
            or not isinstance(result.get("html"), dict) or result["html"].get("sha256") != html.get("sha256")
            or result.get("mesh_manifest_sha256") != artifacts["mesh_manifest.json"].get("sha256")
            or result.get("run_manifest_sha256") != artifacts["run_manifest.json"].get("sha256")):
        blockers.append("CASE_RESULT_LINKAGE_INVALID")
    return blockers, hashes


def _validate_real_dxf(projects_root: Path, document: dict[str, Any], output: Path | None) -> tuple[list[str], dict[str, str]]:
    blockers = _common_document(document, "real_dxf_screening", AUTHORITATIVE_ARTIFACTS["real_dxf_screening"][1])
    dxf, _, hashes, dxf_blockers = _fixed_link(projects_root, document, "source_dxf", check_id="real_dxf_screening", output=output)
    geometry, _, geometry_hashes, geometry_blockers = _fixed_link(
        projects_root, document, "geometry", check_id="real_dxf_screening", output=output,
    )
    blockers.extend(dxf_blockers + geometry_blockers)
    hashes.update(geometry_hashes)
    if (dxf is not None or geometry is None or document.get("source_is_real") is not True
            or document.get("screening_status") != "PASS"
            or geometry.get("source_dxf_sha256") != document.get("source_dxf", {}).get("sha256")):
        # A raw DXF is not JSON, so _link returns None after successful hash
        # verification; that is expected and the hash is retained below.
        if not (not dxf_blockers and geometry is not None and document.get("source_is_real") is True
                and document.get("screening_status") == "PASS"
                and geometry.get("source_dxf_sha256") == document.get("source_dxf", {}).get("sha256")):
            blockers.append("DXF_SCREENING_SEMANTICS_INVALID")
    source = document.get("source_dxf")
    if isinstance(source, dict) and isinstance(source.get("path"), str):
        source_path = (projects_root / source["path"]).resolve()
        if source_path.is_file() and not _blocked_path(projects_root, source_path, output):
            hashes[str(source_path)] = _sha256_file(source_path)
    return blockers, hashes


def _validate_restart(projects_root: Path, document: dict[str, Any], output: Path | None) -> tuple[list[str], dict[str, str]]:
    blockers = _common_document(document, "restart_integrity", AUTHORITATIVE_ARTIFACTS["restart_integrity"][1])
    restart, _, hashes, restart_blockers = _fixed_link(
        projects_root, document, "restart_input", check_id="restart_integrity", output=output,
    )
    run, _, run_hashes, run_blockers = _fixed_link(
        projects_root, document, "run_manifest", check_id="restart_integrity", output=output,
    )
    blockers.extend(restart_blockers + run_blockers)
    hashes.update(run_hashes)
    if (restart is None or run is None or not _is_hex(restart.get("restart_fingerprint"))
            or run.get("requested_ranks") != 1 or document.get("restart_status") != "PASS"):
        blockers.append("RESTART_INTEGRITY_SEMANTICS_INVALID")
    return blockers, hashes


def _validate_exact_heat(projects_root: Path, document: dict[str, Any], output: Path | None) -> tuple[list[str], dict[str, str]]:
    blockers = _common_document(document, "exact_heat_verification", AUTHORITATIVE_ARTIFACTS["exact_heat_verification"][1])
    result, _, hashes, result_blockers = _fixed_link(projects_root, document, "result_manifest", check_id="exact_heat_verification", output=output)
    report, _, report_hashes, report_blockers = _fixed_link(projects_root, document, "heat_report", check_id="exact_heat_verification", output=output)
    blockers.extend(result_blockers + report_blockers)
    hashes.update(report_hashes)
    try:
        valid = (result is not None and report is not None and report.get("status") == "PASS"
                 and float(document.get("max_relative_error")) <= float(document.get("tolerance"))
                 and report.get("max_relative_error") == document.get("max_relative_error")
                 and report.get("tolerance") == document.get("tolerance"))
    except (TypeError, ValueError):
        valid = False
    if not valid:
        blockers.append("EXACT_HEAT_SEMANTICS_INVALID")
    return blockers, hashes


def _validate_spotchecks(projects_root: Path, document: dict[str, Any], output: Path | None) -> tuple[list[str], dict[str, str]]:
    blockers = _common_document(document, "limited_numerical_spotchecks", AUTHORITATIVE_ARTIFACTS["limited_numerical_spotchecks"][1])
    result, _, hashes, result_blockers = _fixed_link(projects_root, document, "result_manifest", check_id="limited_numerical_spotchecks", output=output)
    report, _, report_hashes, report_blockers = _fixed_link(projects_root, document, "spotcheck_report", check_id="limited_numerical_spotchecks", output=output)
    blockers.extend(result_blockers + report_blockers)
    hashes.update(report_hashes)
    if (result is None or report is None or report.get("status") != "PASS"
            or not isinstance(document.get("qoi_count"), int) or document["qoi_count"] < 1
            or report.get("qoi_count") != document.get("qoi_count")):
        blockers.append("NUMERICAL_SPOTCHECK_SEMANTICS_INVALID")
    return blockers, hashes


_VALIDATORS: dict[str, Callable[[Path, dict[str, Any], Path | None], tuple[list[str], dict[str, str]]]] = {
    "code_baseline": _validate_code_baseline,
    "filesystem_io": _validate_filesystem_io,
    "serial_environment": _validate_serial_environment,
    "working_room_e2e": _validate_working_room,
    "real_dxf_screening": _validate_real_dxf,
    "restart_integrity": _validate_restart,
    "exact_heat_verification": _validate_exact_heat,
    "limited_numerical_spotchecks": _validate_spotchecks,
}


def _evaluate_check(projects_root: Path, check_id: str, output: Path | None) -> tuple[dict[str, Any], dict[str, str]]:
    relative, expected_contract = AUTHORITATIVE_ARTIFACTS[check_id]
    path, path_blocker = _resolve_fixed(projects_root, relative, output)
    if path_blocker:
        return {"id": check_id, "status": "BLOCKED", "blockers": [path_blocker]}, {}
    if path is None or not path.is_file():
        return {"id": check_id, "status": "BLOCKED", "blockers": ["AUTHORITATIVE_ARTIFACT_MISSING"]}, {}
    document = _read_json(path)
    if document is None:
        return {"id": check_id, "status": "BLOCKED", "blockers": ["AUTHORITATIVE_ARTIFACT_MALFORMED"]}, {}
    blockers, hashes = _VALIDATORS[check_id](projects_root, document, output)
    if document.get("contract") != expected_contract:
        blockers.append("AUTHORITATIVE_CONTRACT_INVALID")
    if not blockers:
        hashes[str(path)] = _sha256_file(path)
    return {"id": check_id, "status": "PASS" if not blockers else "BLOCKED", "blockers": sorted(set(blockers))}, hashes if not blockers else {}


def _schema_path() -> Path:
    return Path(__file__).with_name("working_validation.v1.schema.json")


def validate_working_validation_payload(payload: object) -> list[str]:
    """Return fail-closed schema and truth-table errors for one emitted manifest."""
    try:
        schema = json.loads(_schema_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ["SCHEMA_UNAVAILABLE"]
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda error: list(error.path))
    blockers = ["SCHEMA_INVALID:" + error.message for error in errors]
    if not isinstance(payload, dict):
        return blockers or ["PAYLOAD_NOT_OBJECT"]
    checks = payload.get("checks")
    if not isinstance(checks, list) or [row.get("id") if isinstance(row, dict) else None for row in checks] != list(CHECK_IDS):
        blockers.append("CHECK_IDS_NOT_EXACT_ORDERED_UNIQUE")
        return blockers
    first_six = checks[:6]
    all_pass = all(row.get("status") == "PASS" for row in checks)
    first_six_pass = all(row.get("status") == "PASS" for row in first_six)
    expected_status = "NUMERICAL_SPOTCHECK_PASS_SINGLE_PC" if all_pass else "WORKING_SINGLE_PC" if first_six_pass else "BLOCKED"
    if payload.get("status") != expected_status:
        blockers.append("STATUS_TRUTH_TABLE_INVALID")
    if payload.get("working_ready_on_target") is not first_six_pass:
        blockers.append("WORKING_BOOLEAN_TRUTH_TABLE_INVALID")
    if payload.get("limited_numerical_spotchecks_pass_on_target") is not all_pass:
        blockers.append("SPOTCHECK_BOOLEAN_TRUTH_TABLE_INVALID")
    expected_blockers = [row for row in first_six if row.get("status") != "PASS"]
    expected_numerical = [row for row in checks[6:] if row.get("status") != "PASS"]
    if payload.get("blockers") != expected_blockers:
        blockers.append("BLOCKERS_TRUTH_TABLE_INVALID")
    if payload.get("numerical_blockers") != expected_numerical:
        blockers.append("NUMERICAL_BLOCKERS_TRUTH_TABLE_INVALID")
    if payload.get("verification_scope") != ["single_pc_serial_current_user"]:
        blockers.append("VERIFICATION_SCOPE_INVALID")
    if payload.get("limitations") != ["not_design_citable", "not_release_ready"]:
        blockers.append("LIMITATIONS_INVALID")
    return blockers


def _build_payload(projects_root: Path, output: Path | None = None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    evidence_sha256: dict[str, str] = {}
    for check_id in CHECK_IDS:
        row, hashes = _evaluate_check(projects_root, check_id, output)
        checks.append(row)
        evidence_sha256.update(hashes)
    first_six_pass = all(row["status"] == "PASS" for row in checks[:6])
    all_pass = all(row["status"] == "PASS" for row in checks)
    status = "NUMERICAL_SPOTCHECK_PASS_SINGLE_PC" if all_pass else "WORKING_SINGLE_PC" if first_six_pass else "BLOCKED"
    return {
        "contract": "working_validation.v1",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "scope": "single_pc_serial_current_user",
        "status": status,
        "working_ready_on_target": first_six_pass,
        "limited_numerical_spotchecks_pass_on_target": all_pass,
        "design_citable": False,
        "release_ready": False,
        "checks": checks,
        "blockers": [row for row in checks[:6] if row["status"] != "PASS"],
        "numerical_blockers": [row for row in checks[6:] if row["status"] != "PASS"],
        "verification_scope": ["single_pc_serial_current_user"],
        "limitations": ["not_design_citable", "not_release_ready"],
        "evidence_sha256": dict(sorted(evidence_sha256.items())),
    }


def evaluate_working_validation(projects_root: Path) -> dict:
    """Recompute evidence and return WORKING, NUMERICAL_SPOTCHECK_PASS, or BLOCKED."""
    payload = _build_payload(Path(projects_root).resolve())
    blockers = validate_working_validation_payload(payload)
    if blockers:
        raise ValueError("WORKING_VALIDATION_EMISSION_INVALID:" + ";".join(blockers))
    return payload


def write_working_validation(projects_root: Path, output: Path) -> dict:
    """Atomically write the recomputed single-PC manifest."""
    output = Path(output).resolve()
    payload = _build_payload(Path(projects_root).resolve(), output)
    if any("OUTPUT_ALIAS" in blocker for row in payload["checks"] for blocker in row["blockers"]):
        raise ValueError("WORKING_VALIDATION_OUTPUT_ALIAS")
    payload["output_path"] = str(output)
    blockers = validate_working_validation_payload(payload)
    if blockers:
        raise ValueError("WORKING_VALIDATION_EMISSION_INVALID:" + ";".join(blockers))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    return payload


def compare_working_validation_runs(first: Path, second: Path) -> dict:
    """Compare all declared semantics except created_at and output path."""
    first_payload = _read_json(Path(first))
    second_payload = _read_json(Path(second))
    first_errors = validate_working_validation_payload(first_payload)
    second_errors = validate_working_validation_payload(second_payload)
    if first_errors or second_errors:
        raise ValueError("WORKING_VALIDATION_RUN_INVALID:" + ";".join(first_errors + second_errors))
    assert first_payload is not None and second_payload is not None
    canonical_first = {key: value for key, value in first_payload.items() if key not in {"created_at", "output_path"}}
    canonical_second = {key: value for key, value in second_payload.items() if key not in {"created_at", "output_path"}}
    differences = sorted(key for key in set(canonical_first) | set(canonical_second) if canonical_first.get(key) != canonical_second.get(key))
    return {"equal": not differences, "differences": differences}
