"""Fail-closed single-PC working-validation evidence contract.

Only Task 1 validators live here.  Later tasks may register a code-owned,
check-specific recomputation validator for their own raw artifacts; until then
their check is intentionally BLOCKED.  Check documents and a solver/PASS JSON
are never a substitute for an implemented validator.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

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
TASK_ONE_ARTIFACTS = {
    "code_baseline": "_working_validation/evidence/vv_baseline.json",
    "filesystem_io": "_working_validation/evidence/io_acceptance.json",
}
_FUTURE_CHECKS = frozenset(CHECK_IDS[2:])
_MAX_SOURCE_AGE = timedelta(days=7)
_HEX = re.compile(r"^[0-9a-f]{64}$")
_RFC3339_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_CACHE_PARTS = frozenset({".cache", "cache", ".pytest_cache", "__pycache__", "tmp", "temp", ".tmp"})


@dataclass(frozen=True)
class CheckResult:
    """Code-created result consumed by the pure working-state transition."""

    check_id: str
    status: str
    blockers: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.check_id, "status": self.status, "blockers": list(self.blockers)}


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


def _safe_path(projects_root: Path, candidate: Path, output: Path | None) -> str | None:
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


def _fixed_artifact(projects_root: Path, relative: str, output: Path | None) -> tuple[Path | None, str | None]:
    candidate = (projects_root / relative).resolve()
    if output is not None and candidate == output.resolve():
        return None, "OUTPUT_ALIAS"
    try:
        candidate.relative_to(projects_root)
    except ValueError:
        return None, "SOURCE_OUTSIDE_PROJECTS_ROOT"
    if not candidate.is_file():
        return None, "AUTHORITATIVE_ARTIFACT_MISSING"
    return candidate, None


def _git_head() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent,
            capture_output=True, check=True, text=True, encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return completed.stdout.strip()


def _evaluate_code_baseline(projects_root: Path, output: Path | None) -> tuple[CheckResult, dict[str, str]]:
    path, error = _fixed_artifact(projects_root, TASK_ONE_ARTIFACTS["code_baseline"], output)
    if error:
        return CheckResult("code_baseline", "BLOCKED", (error,)), {}
    assert path is not None
    baseline = _read_json(path)
    if baseline is None or baseline.get("contract") != "vv_baseline.v1":
        return CheckResult("code_baseline", "BLOCKED", ("BASELINE_CONTRACT_INVALID",)), {}
    if not _fresh(baseline.get("created_at")):
        return CheckResult("code_baseline", "BLOCKED", ("BASELINE_STALE_OR_TIMESTAMP_INVALID",)), {}
    executable = baseline.get("python_executable")
    executable_path = Path(executable) if isinstance(executable, str) and executable else None
    try:
        from vv_baseline import _installed_distribution_snapshot
        expected_distributions = _installed_distribution_snapshot()
    except Exception:
        expected_distributions = ""
    if (executable_path is None or not executable_path.is_file()
            or baseline.get("python_executable_sha256") != _sha256_file(executable_path)
            or baseline.get("python_version") != sys.version
            or baseline.get("python_architecture") != platform.architecture()[0]
            or baseline.get("installed_distribution_snapshot_sha256") != expected_distributions
            or baseline.get("git_head") != _git_head()
            or baseline.get("projects_root") != str(projects_root)):
        return CheckResult("code_baseline", "BLOCKED", ("BASELINE_RECOMPUTATION_MISMATCH",)), {}
    return CheckResult("code_baseline", "PASS"), {str(path): _sha256_file(path)}


def _io_schema() -> dict[str, Any] | None:
    return _read_json(Path(__file__).with_name("io_acceptance.v1.schema.json"))


def _canonical_io(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in {"created_at", "projects_root"}}


def _io_evidence_hashes(projects_root: Path, raw: dict[str, Any], output: Path | None, report: Path) -> tuple[dict[str, str], str | None]:
    hashes = {str(report): _sha256_file(report)}
    inventory_relative = raw.get("inventory_path")
    if not isinstance(inventory_relative, str):
        return {}, "IO_ACCEPTANCE_RECOMPUTATION_MISMATCH"
    inventory = (projects_root / inventory_relative).resolve()
    error = _safe_path(projects_root, inventory, output)
    if error or not inventory.is_file() or raw.get("inventory_sha256") != _sha256_file(inventory):
        return {}, error or "IO_ACCEPTANCE_RECOMPUTATION_MISMATCH"
    hashes[str(inventory)] = _sha256_file(inventory)
    probes = raw.get("artifact_probes")
    if not isinstance(probes, list):
        return {}, "IO_ACCEPTANCE_RECOMPUTATION_MISMATCH"
    for probe in probes:
        if not isinstance(probe, dict) or probe.get("status") != "PASS" or probe.get("read") is not True:
            return {}, "IO_ACCEPTANCE_RECOMPUTATION_MISMATCH"
        candidate_value = probe.get("path")
        if not isinstance(candidate_value, str):
            return {}, "IO_ACCEPTANCE_RECOMPUTATION_MISMATCH"
        candidate = Path(candidate_value).resolve()
        error = _safe_path(projects_root, candidate, output)
        if error or not candidate.is_file() or probe.get("sha256") != _sha256_file(candidate):
            return {}, error or "IO_ACCEPTANCE_RECOMPUTATION_MISMATCH"
        hashes[str(candidate)] = _sha256_file(candidate)
    return hashes, None


def _evaluate_filesystem_io(projects_root: Path, output: Path | None) -> tuple[CheckResult, dict[str, str]]:
    report, error = _fixed_artifact(projects_root, TASK_ONE_ARTIFACTS["filesystem_io"], output)
    if error:
        return CheckResult("filesystem_io", "BLOCKED", (error,)), {}
    assert report is not None
    raw = _read_json(report)
    schema = _io_schema()
    if raw is None or schema is None:
        return CheckResult("filesystem_io", "BLOCKED", ("IO_ACCEPTANCE_MALFORMED",)), {}
    schema_errors = list(Draft202012Validator(schema).iter_errors(raw))
    if schema_errors:
        return CheckResult("filesystem_io", "BLOCKED", ("IO_ACCEPTANCE_SCHEMA_INVALID",)), {}
    if not _fresh(raw.get("created_at")):
        return CheckResult("filesystem_io", "BLOCKED", ("IO_ACCEPTANCE_STALE_OR_TIMESTAMP_INVALID",)), {}
    # Recompute the actual root probes and selected recovered artifact hashes;
    # the saved PASS field is not proof by itself.
    from scripts.io_acceptance import run_io_acceptance
    recomputed = run_io_acceptance(projects_root)
    if recomputed.get("status") != "PASS" or _canonical_io(raw) != _canonical_io(recomputed):
        return CheckResult("filesystem_io", "BLOCKED", ("IO_ACCEPTANCE_RECOMPUTATION_MISMATCH",)), {}
    hashes, evidence_error = _io_evidence_hashes(projects_root, raw, output, report)
    if evidence_error:
        return CheckResult("filesystem_io", "BLOCKED", (evidence_error,)), {}
    return CheckResult("filesystem_io", "PASS"), hashes


def _future_not_implemented(check_id: str) -> tuple[CheckResult, dict[str, str]]:
    """Task 2–5 must replace this with a code-owned semantic recomputation."""
    return CheckResult(check_id, "BLOCKED", (f"{check_id.upper()}_VALIDATOR_NOT_IMPLEMENTED",)), {}


def _external_results(projects_root: Path, output: Path | None) -> tuple[list[CheckResult], dict[str, str]]:
    results: list[CheckResult] = []
    evidence: dict[str, str] = {}
    for check_id in CHECK_IDS:
        if check_id == "code_baseline":
            result, hashes = _evaluate_code_baseline(projects_root, output)
        elif check_id == "filesystem_io":
            result, hashes = _evaluate_filesystem_io(projects_root, output)
        else:
            result, hashes = _future_not_implemented(check_id)
        results.append(result)
        evidence.update(hashes)
    return results, dict(sorted(evidence.items()))


def _derive_working_validation_state(results: Iterable[CheckResult]) -> dict[str, Any]:
    """Pure exact-eight state machine; it never reads filesystem evidence."""
    rows = list(results)
    if [row.check_id for row in rows] != list(CHECK_IDS):
        raise ValueError("CHECK_RESULTS_NOT_EXACT_ORDERED_UNIQUE")
    if any(row.status not in {"PASS", "FAIL", "BLOCKED", "NOT_EVALUATED"} for row in rows):
        raise ValueError("CHECK_RESULT_STATUS_INVALID")
    checks = [row.as_dict() for row in rows]
    first_six_pass = all(row.status == "PASS" for row in rows[:6])
    all_pass = all(row.status == "PASS" for row in rows)
    status = "NUMERICAL_SPOTCHECK_PASS_SINGLE_PC" if all_pass else "WORKING_SINGLE_PC" if first_six_pass else "BLOCKED"
    return {
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
    }


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
    results, evidence_sha256 = _external_results(projects_root, output)
    state = _derive_working_validation_state(results)
    return {
        "contract": "working_validation.v1",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "scope": "single_pc_serial_current_user",
        **state,
        "evidence_sha256": evidence_sha256,
    }


def evaluate_working_validation(projects_root: Path) -> dict:
    """Recompute Task 1 evidence; future check ownership remains fail-closed."""
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
