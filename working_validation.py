"""Fail-closed single-PC working-validation evidence contract.

Each of the fixed eight checks is evaluated by a code-owned validator against
one authoritative manifest and its raw artifacts.  Check documents and a
solver/PASS JSON are never a substitute for semantic recomputation.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import stat
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
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
CODE_OWNED_ARTIFACTS = {
    "serial_environment": "_working_validation/local_usability_acceptance.json",
    "working_room_e2e": (
        "_working_validation/working-room-v1/working_room_acceptance.json"
    ),
    "real_dxf_screening": (
        "_working_validation/sgi-screening-v1/sgi_screening_acceptance.json"
    ),
    "restart_integrity": (
        "_working_validation/sgi-screening-v1/sgi_screening_acceptance.json"
    ),
    "exact_heat_verification": (
        "_working_validation/heat-box-v1/verification_manifest.json"
    ),
    "limited_numerical_spotchecks": (
        "_working_validation/numerical-spotcheck-v1/numerical_spotcheck.json"
    ),
}
_MAX_SOURCE_AGE = timedelta(days=7)
_HEX = re.compile(r"^[0-9a-f]{64}$")
_RFC3339_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_CACHE_PARTS = frozenset({".cache", "cache", ".pytest_cache", "__pycache__", "tmp", "temp", ".tmp"})
_SERIAL_AUTHORITY_ROOTS = (
    "_working_validation/local_usability_acceptance.json",
    "_working_validation/runtime_capability.v1.json",
    "_system/environment_acceptance",
)
_PROTECTED_PRODUCER_ROOTS = (
    "_working_validation/evidence",
    "_working_validation/evaluations",
    "_working_validation/working-room-v1",
    "_working_validation/sgi-screening-v1",
    "_working_validation/heat-box-v1",
    "_working_validation/numerical-spotcheck-v1",
    "_imports",
    "_field_jobs",
    "_body_mesh",
    "_body_solver",
    "_body_gci",
)
_WINDOWS_RESERVED_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
})


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
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("DUPLICATE_JSON_KEY")
            value[key] = item
        return value

    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
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
    return CheckResult("code_baseline", "PASS"), {
        path.relative_to(projects_root).as_posix(): _sha256_file(path)
    }


def _io_schema() -> dict[str, Any] | None:
    return _read_json(Path(__file__).with_name("io_acceptance.v1.schema.json"))


def _canonical_io(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in {"created_at", "projects_root"}}


def _io_evidence_hashes(projects_root: Path, raw: dict[str, Any], output: Path | None, report: Path) -> tuple[dict[str, str], str | None]:
    hashes = {report.relative_to(projects_root).as_posix(): _sha256_file(report)}
    inventory_relative = raw.get("inventory_path")
    if not isinstance(inventory_relative, str):
        return {}, "IO_ACCEPTANCE_RECOMPUTATION_MISMATCH"
    inventory = (projects_root / inventory_relative).resolve()
    error = _safe_path(projects_root, inventory, output)
    if error or not inventory.is_file() or raw.get("inventory_sha256") != _sha256_file(inventory):
        return {}, error or "IO_ACCEPTANCE_RECOMPUTATION_MISMATCH"
    hashes[inventory.relative_to(projects_root).as_posix()] = _sha256_file(inventory)
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
        hashes[candidate.relative_to(projects_root).as_posix()] = _sha256_file(candidate)
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


def _normalize_code_owned_result(
    check_id: str, value: object,
) -> tuple[CheckResult, dict[str, str]]:
    invalid = f"{check_id.upper()}_VALIDATOR_RESULT_INVALID"
    if not isinstance(value, dict):
        return CheckResult(check_id, "BLOCKED", (invalid,)), {}
    status = value.get("status")
    blockers = value.get("blockers")
    hashes = value.get("evidence_sha256")
    reported_check = value.get("check_id")
    valid_blockers = (
        isinstance(blockers, list)
        and all(isinstance(item, str) and item for item in blockers)
        and len(blockers) == len(set(blockers))
    )
    valid_hashes = (
        isinstance(hashes, dict)
        and all(
            isinstance(path, str) and path and _is_hex(digest)
            for path, digest in hashes.items()
        )
    )
    if (
        not isinstance(status, str)
        or status not in {"PASS", "FAIL", "BLOCKED"}
        or not valid_blockers
        or not valid_hashes
        or (reported_check is not None and reported_check != check_id)
        or (status == "PASS" and (blockers or not hashes))
        or (status != "PASS" and not blockers)
    ):
        return CheckResult(check_id, "BLOCKED", (invalid,)), {}
    return CheckResult(check_id, status, tuple(blockers)), dict(sorted(hashes.items()))


def _evaluate_code_owned(
    check_id: str, projects_root: Path, output: Path | None,
) -> tuple[CheckResult, dict[str, str]]:
    manifest = projects_root.joinpath(
        *PurePosixPath(CODE_OWNED_ARTIFACTS[check_id]).parts
    )
    try:
        if check_id == "serial_environment":
            from scripts.local_usability_acceptance import (
                validate_local_usability_acceptance,
            )

            value = validate_local_usability_acceptance(manifest, projects_root)
        elif check_id == "working_room_e2e":
            from cfd_working_room import validate_working_room

            value = validate_working_room(manifest, projects_root, output)
        elif check_id == "real_dxf_screening":
            from cfd_working_room import validate_sgi_screening_acceptance

            value = validate_sgi_screening_acceptance(
                manifest, projects_root, output
            )
        elif check_id == "restart_integrity":
            from cfd_working_room import validate_restart_integrity

            value = validate_restart_integrity(manifest, projects_root, output)
        elif check_id == "exact_heat_verification":
            from cfd_verification import validate_heat_box_manifest

            value = validate_heat_box_manifest(manifest, projects_root, output)
        else:
            from cfd_numerical_spotcheck import (
                validate_numerical_spotcheck_manifest,
            )

            value = validate_numerical_spotcheck_manifest(
                manifest, projects_root, output
            )
        return _normalize_code_owned_result(check_id, value)
    except Exception:
        code = f"{check_id.upper()}_VALIDATOR_EXCEPTION"
        return CheckResult(check_id, "BLOCKED", (code,)), {}


def _canonical_evidence_ref(value: object) -> PurePosixPath | None:
    """Accept only one canonical, safe, project-relative POSIX evidence key."""

    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or ":" in value
        or value.startswith("/")
        or "//" in value
    ):
        return None
    relative = PurePosixPath(value)
    if relative.is_absolute() or relative.as_posix() != value:
        return None
    folded = [part.casefold() for part in relative.parts]
    if (
        not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
        or any(
            part in _CACHE_PARTS
            or part.endswith(".tmp")
            or part == "latest"
            or part.startswith("latest.")
            for part in folded
        )
    ):
        return None
    name = relative.name.casefold()
    if (
        ("working_validation" in name or "working-validation" in name)
        and PurePosixPath(name).suffix in {".json", ".html", ".htm"}
    ):
        return None
    return relative


def _evidence_path_case_is_exact(
    projects_root: Path,
    relative: PurePosixPath,
) -> bool:
    current = projects_root
    for part in relative.parts:
        try:
            names = frozenset(entry.name for entry in os.scandir(current))
        except OSError:
            return False
        if part not in names:
            return False
        current = current / part
    return True


def _reparse_free_evidence_path(projects_root: Path, relative: PurePosixPath) -> bool:
    current = projects_root
    for part in relative.parts:
        current = current / part
        try:
            metadata = current.lstat()
        except OSError:
            return False
        if current.is_symlink() or (
            getattr(metadata, "st_file_attributes", 0) & 0x400
        ):
            return False
    return True


def _evidence_identity(path: Path) -> tuple[int, int, int, int] | None:
    try:
        metadata = path.stat()
    except OSError:
        return None
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(stat.S_IFMT(metadata.st_mode)),
        int(metadata.st_nlink),
    )


def _evidence_digest(check_id: str, path: Path) -> str | None:
    if path.is_file():
        try:
            return _sha256_file(path)
        except OSError:
            return None
    if path.is_dir() and check_id == "limited_numerical_spotchecks":
        try:
            from cfd_numerical_spotcheck import _snapshot_directory_tree

            snapshot = _snapshot_directory_tree(path)
        except Exception:
            return None
        return snapshot[0] if snapshot is not None else None
    return None


def _capture_evidence(
    projects_root: Path,
    check_id: str,
    path_text: str,
    expected_digest: str,
) -> tuple[dict[str, Any] | None, str | None]:
    relative = _canonical_evidence_ref(path_text)
    if relative is None:
        return None, "EVIDENCE_PATH_INVALID"
    lexical = projects_root.joinpath(*relative.parts)
    if (
        not _evidence_path_case_is_exact(projects_root, relative)
        or not _reparse_free_evidence_path(projects_root, relative)
    ):
        return None, "EVIDENCE_PATH_INVALID"
    try:
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(projects_root)
    except (OSError, RuntimeError, ValueError):
        return None, "EVIDENCE_PATH_INVALID"
    identity = _evidence_identity(resolved)
    actual_digest = _evidence_digest(check_id, resolved)
    if identity is None or actual_digest is None:
        return None, "EVIDENCE_PATH_INVALID"
    if resolved.is_file() and identity[3] != 1:
        return None, "EVIDENCE_ALIAS_CONFLICT"
    if actual_digest != expected_digest:
        return None, "EVIDENCE_HASH_MISMATCH_AT_AGGREGATION"
    return {
        "path": resolved,
        "digest": expected_digest,
        "identity": identity,
        "is_directory": resolved.is_dir(),
        "owners": {check_id},
    }, None


def _block_check(
    results: list[CheckResult], index_by_check: dict[str, int],
    check_id: str, blocker: str,
) -> None:
    index = index_by_check[check_id]
    current = results[index]
    blockers = tuple(dict.fromkeys((*current.blockers, blocker)))
    results[index] = CheckResult(check_id, "BLOCKED", blockers)


def _external_results(projects_root: Path, output: Path | None) -> tuple[list[CheckResult], dict[str, str]]:
    results: list[CheckResult] = []
    index_by_check: dict[str, int] = {}
    records: dict[str, dict[str, Any]] = {}
    for check_id in CHECK_IDS:
        if check_id == "code_baseline":
            result, hashes = _evaluate_code_baseline(projects_root, output)
        elif check_id == "filesystem_io":
            result, hashes = _evaluate_filesystem_io(projects_root, output)
        else:
            result, hashes = _evaluate_code_owned(check_id, projects_root, output)
        results.append(result)
        index_by_check[check_id] = len(results) - 1
        if result.status == "PASS" and not hashes:
            _block_check(results, index_by_check, check_id, "EVIDENCE_MISSING")
        for path_text, digest in hashes.items():
            prior = records.get(path_text)
            if prior is not None and prior["digest"] != digest:
                prior["owners"].add(check_id)
                prior.setdefault("reported_digests", {prior["digest"]}).add(
                    digest
                )
                continue
            captured, error = _capture_evidence(
                projects_root, check_id, path_text, digest
            )
            if error is not None:
                _block_check(results, index_by_check, check_id, error)
                continue
            assert captured is not None
            prior = records.get(path_text)
            if prior is None:
                records[path_text] = captured
            else:
                prior["owners"].add(check_id)
                prior.setdefault("reported_digests", {prior["digest"]}).add(
                    captured["digest"]
                )
                prior.setdefault("reported_identities", {prior["identity"]}).add(
                    captured["identity"]
                )

    invalid_paths: set[str] = set()
    for path_text, record in records.items():
        if (
            len(record.get("reported_digests", {record["digest"]})) != 1
            or len(record.get("reported_identities", {record["identity"]})) != 1
        ):
            invalid_paths.add(path_text)
            for owner in record["owners"]:
                _block_check(
                    results, index_by_check, owner, "EVIDENCE_HASH_CONFLICT"
                )

    paths_by_identity: dict[tuple[int, int, int, int], list[str]] = {}
    for path_text, record in records.items():
        paths_by_identity.setdefault(record["identity"], []).append(path_text)
    for aliases in paths_by_identity.values():
        if len(aliases) < 2:
            continue
        invalid_paths.update(aliases)
        for path_text in aliases:
            for owner in records[path_text]["owners"]:
                _block_check(
                    results, index_by_check, owner, "EVIDENCE_ALIAS_CONFLICT"
                )

    evidence: dict[str, str] = {}
    for path_text, record in records.items():
        if path_text in invalid_paths:
            continue
        owners = record["owners"]
        owner_for_digest = (
            "limited_numerical_spotchecks"
            if record["is_directory"]
            else next(iter(owners))
        )
        current_identity = _evidence_identity(record["path"])
        current_digest = _evidence_digest(owner_for_digest, record["path"])
        if (
            not _evidence_path_case_is_exact(
                projects_root, PurePosixPath(path_text)
            )
            or not _reparse_free_evidence_path(
                projects_root, PurePosixPath(path_text)
            )
            or current_identity != record["identity"]
            or current_digest != record["digest"]
        ):
            for owner in owners:
                _block_check(
                    results, index_by_check, owner,
                    "EVIDENCE_CHANGED_DURING_AGGREGATION",
                )
            continue
        evidence[path_text] = record["digest"]
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
    for row in checks:
        row_blockers = row.get("blockers")
        valid_row_blockers = (
            isinstance(row_blockers, list)
            and all(isinstance(item, str) and item for item in row_blockers)
            and len(row_blockers) == len(set(row_blockers))
        )
        if (
            not valid_row_blockers
            or (row.get("status") == "PASS" and row_blockers)
            or (row.get("status") != "PASS" and not row_blockers)
        ):
            blockers.append("CHECK_BLOCKER_INVARIANT_INVALID")
            break
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
    evidence = payload.get("evidence_sha256")
    if isinstance(evidence, dict) and any(
        _canonical_evidence_ref(path_text) is None
        for path_text in evidence
    ):
        blockers.append("EVIDENCE_PATH_INVALID")
    if (
        isinstance(evidence, dict)
        and not evidence
        and any(row.get("status") == "PASS" for row in checks)
    ):
        blockers.append("EVIDENCE_MISSING")
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
    """Recompute all eight fixed checks without running external producers."""
    payload = _build_payload(Path(projects_root).resolve())
    blockers = validate_working_validation_payload(payload)
    if blockers:
        raise ValueError("WORKING_VALIDATION_EMISSION_INVALID:" + ";".join(blockers))
    return payload


def _same_location(first: Path, second: Path) -> bool:
    try:
        if first.resolve(strict=False) == second.resolve(strict=False):
            return True
        return first.exists() and second.exists() and os.path.samefile(first, second)
    except OSError:
        return False


def _paths_overlap(first: Path, second: Path) -> bool:
    try:
        left = first.resolve(strict=False)
        right = second.resolve(strict=False)
    except (OSError, RuntimeError):
        return True
    if left == right or left in right.parents or right in left.parents:
        return True
    return _same_location(first, second)


def _output_lexical_blocker(
    projects_root: Path, output: Path,
) -> tuple[Path | None, str | None]:
    raw = Path(output)
    if any(part in {".", ".."} for part in raw.parts):
        return None, "OUTPUT_PATH_INVALID"
    try:
        lexical = Path(os.path.abspath(os.fspath(raw)))
        relative = lexical.relative_to(projects_root)
    except (OSError, TypeError, ValueError):
        return None, "OUTPUT_OUTSIDE_PROJECTS_ROOT"
    if not relative.parts:
        return None, "OUTPUT_PATH_INVALID"
    folded = [part.casefold() for part in relative.parts]
    if any(
        part in _CACHE_PARTS or part.endswith(".tmp")
        for part in folded
    ):
        return None, "OUTPUT_CACHE_OR_TEMP_FORBIDDEN"
    for part in relative.parts:
        if (
            not part
            or ":" in part
            or any(character in '<>"|?*' for character in part)
            or any(ord(character) < 32 for character in part)
            or part.endswith((" ", "."))
            or part.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES
        ):
            return None, "OUTPUT_PATH_INVALID"

    current = projects_root
    for index, part in enumerate(relative.parts):
        try:
            names = frozenset(entry.name for entry in os.scandir(current))
        except FileNotFoundError:
            break
        except OSError:
            return None, "OUTPUT_PATH_INVALID"
        if part not in names:
            if any(name.casefold() == part.casefold() for name in names):
                return None, "OUTPUT_PATH_INVALID"
            break
        current = current / part
        try:
            metadata = current.lstat()
        except OSError:
            return None, "OUTPUT_PATH_INVALID"
        if current.is_symlink() or (
            getattr(metadata, "st_file_attributes", 0) & 0x400
        ):
            return None, "OUTPUT_PATH_INVALID"
        is_leaf = index == len(relative.parts) - 1
        if (is_leaf and not stat.S_ISREG(metadata.st_mode)) or (
            not is_leaf and not stat.S_ISDIR(metadata.st_mode)
        ):
            return None, "OUTPUT_PATH_INVALID"
    return lexical, None


def _output_authority_blocker(
    projects_root: Path,
    output: Path,
    evidence_sha256: dict[str, str] | None = None,
) -> str | None:
    lexical, lexical_error = _output_lexical_blocker(projects_root, output)
    if lexical_error:
        return lexical_error
    assert lexical is not None
    try:
        lexical.resolve(strict=False).relative_to(projects_root)
    except (OSError, RuntimeError, ValueError):
        return "OUTPUT_OUTSIDE_PROJECTS_ROOT"
    fixed = {
        *TASK_ONE_ARTIFACTS.values(),
        *CODE_OWNED_ARTIFACTS.values(),
        *_SERIAL_AUTHORITY_ROOTS,
        *_PROTECTED_PRODUCER_ROOTS,
    }
    authorities = [
        projects_root.joinpath(*PurePosixPath(relative).parts)
        for relative in fixed
    ]
    for path_text in (evidence_sha256 or {}):
        relative = _canonical_evidence_ref(path_text)
        if relative is None:
            return "EVIDENCE_PATH_INVALID"
        candidate = projects_root.joinpath(*relative.parts)
        authorities.append(candidate)
    if any(_paths_overlap(lexical, authority) for authority in authorities):
        return "OUTPUT_ALIAS"
    return None


def _unsafe_output_blocker(blocker: str) -> bool:
    upper = blocker.upper()
    if upper == "SOURCE_SELF_OUTPUT_FORBIDDEN":
        return True
    return "OUTPUT" in upper and any(
        marker in upper
        for marker in (
            "ALIAS", "INVALID", "FORBIDDEN", "LOCATION", "PATH",
            "REPARSE", "UNWRITABLE",
        )
    )


def write_working_validation(projects_root: Path, output: Path) -> dict:
    """Atomically write the recomputed single-PC manifest."""
    projects_root = Path(projects_root).resolve()
    raw_output = Path(output)
    output_blocker = _output_authority_blocker(projects_root, raw_output)
    if output_blocker:
        raise ValueError("WORKING_VALIDATION_" + output_blocker)
    output = Path(os.path.abspath(os.fspath(raw_output)))
    payload = _build_payload(projects_root, output)
    unsafe = [
        blocker
        for row in payload["checks"]
        for blocker in row["blockers"]
        if _unsafe_output_blocker(blocker)
    ]
    if unsafe:
        raise ValueError(
            "WORKING_VALIDATION_UNSAFE_OUTPUT:" + ";".join(dict.fromkeys(unsafe))
        )
    output_blocker = _output_authority_blocker(
        projects_root, output, payload["evidence_sha256"]
    )
    if output_blocker:
        raise ValueError("WORKING_VALIDATION_" + output_blocker)
    payload["output_path"] = str(output)
    blockers = validate_working_validation_payload(payload)
    if blockers:
        raise ValueError("WORKING_VALIDATION_EMISSION_INVALID:" + ";".join(blockers))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return payload


def compare_working_validation_runs(first: Path, second: Path) -> dict:
    """Compare all declared semantics except created_at and output path."""
    try:
        first_path = Path(first).resolve(strict=True)
        second_path = Path(second).resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError("WORKING_VALIDATION_RUN_INVALID:RUN_PATH_INVALID")
    if _same_location(first_path, second_path):
        raise ValueError("WORKING_VALIDATION_RUN_IDENTITY_NOT_INDEPENDENT")
    first_payload = _read_json(first_path)
    second_payload = _read_json(second_path)
    first_errors = validate_working_validation_payload(first_payload)
    second_errors = validate_working_validation_payload(second_payload)
    if first_errors or second_errors:
        raise ValueError("WORKING_VALIDATION_RUN_INVALID:" + ";".join(first_errors + second_errors))
    assert first_payload is not None and second_payload is not None
    if (
        first_payload.get("output_path") != str(first_path)
        or second_payload.get("output_path") != str(second_path)
    ):
        raise ValueError("WORKING_VALIDATION_RUN_OUTPUT_PATH_MISMATCH")
    canonical_first = {key: value for key, value in first_payload.items() if key not in {"created_at", "output_path"}}
    canonical_second = {key: value for key, value in second_payload.items() if key not in {"created_at", "output_path"}}
    differences = sorted(key for key in set(canonical_first) | set(canonical_second) if canonical_first.get(key) != canonical_second.get(key))
    return {"equal": not differences, "differences": differences}
