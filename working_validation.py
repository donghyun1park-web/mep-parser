"""Fail-closed single-PC working-validation evidence contract."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


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
_SOURCES_RELATIVE = Path("_release_evidence") / "working_validation" / "sources.json"
_MAX_SOURCE_AGE = timedelta(days=7)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def _safe_source(projects_root: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    candidate = (projects_root / value).resolve()
    try:
        candidate.relative_to(projects_root)
    except ValueError:
        return None
    name = candidate.name.lower()
    if (name.startswith("working_validation-run") or name.startswith("working-validation")
            or name.endswith(".tmp") or "__pycache__" in candidate.parts):
        return None
    return candidate


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _source_check(projects_root: Path, check_id: str, entry: object) -> tuple[str, list[str], dict[str, str]]:
    if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
        return "BLOCKED", ["SOURCE_DESCRIPTOR_INVALID"], {}
    source = _safe_source(projects_root, entry.get("path"))
    expected = entry.get("sha256")
    if source is None or not source.is_file():
        return "BLOCKED", ["SOURCE_MISSING"], {}
    if not isinstance(expected, str) or len(expected) != 64:
        return "BLOCKED", ["SOURCE_HASH_INVALID"], {}
    actual = _sha256_file(source)
    if actual != expected:
        return "BLOCKED", ["SOURCE_REHASH_MISMATCH"], {}
    recovered = _read_json(source)
    if recovered is None:
        return "BLOCKED", ["SOURCE_MALFORMED"], {}
    created_at = _utc_timestamp(recovered.get("created_at"))
    now = datetime.now(timezone.utc)
    if created_at is None or created_at > now + timedelta(minutes=5) or now - created_at > _MAX_SOURCE_AGE:
        return "BLOCKED", ["SOURCE_STALE"], {}
    if (recovered.get("contract") != "recovered_evidence.v1"
            or recovered.get("artifact") != check_id or "status" in recovered):
        return "BLOCKED", ["SOURCE_NOT_RECOVERED_ARTIFACT"], {}
    return "PASS", [], {str(source): actual}


def evaluate_working_validation(projects_root: Path) -> dict:
    """Recompute evidence and return WORKING, NUMERICAL_SPOTCHECK_PASS, or BLOCKED."""
    projects_root = Path(projects_root).resolve()
    source_manifest = _read_json(projects_root / _SOURCES_RELATIVE)
    source_checks = source_manifest.get("checks") if isinstance(source_manifest, dict) else None
    source_valid = (isinstance(source_manifest, dict)
                    and source_manifest.get("contract") == "working_validation.sources.v1"
                    and isinstance(source_checks, dict)
                    and set(source_checks).issubset(set(CHECK_IDS)))
    checks: list[dict[str, Any]] = []
    evidence_sha256: dict[str, str] = {}
    for check_id in CHECK_IDS:
        if not source_valid or check_id not in source_checks:
            status, blockers, hashes = "BLOCKED", ["SOURCE_EVIDENCE_MISSING"], {}
        else:
            status, blockers, hashes = _source_check(projects_root, check_id, source_checks[check_id])
        checks.append({"id": check_id, "status": status, "blockers": blockers})
        evidence_sha256.update(hashes)
    first_six_pass = all(row["status"] == "PASS" for row in checks[:6])
    all_pass = all(row["status"] == "PASS" for row in checks)
    if all_pass:
        status = "NUMERICAL_SPOTCHECK_PASS_SINGLE_PC"
    elif first_six_pass:
        status = "WORKING_SINGLE_PC"
    else:
        status = "BLOCKED"
    blockers = [row for row in checks[:6] if row["status"] != "PASS"]
    numerical_blockers = [row for row in checks[6:] if row["status"] != "PASS"]
    return {
        "contract": "working_validation.v1",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "scope": "single_pc_serial_current_user",
        "status": status,
        "working_ready_on_target": first_six_pass,
        "limited_numerical_spotchecks_pass_on_target": all_pass,
        "design_citable": False,
        "release_ready": False,
        "checks": checks,
        "blockers": blockers,
        "numerical_blockers": numerical_blockers,
        "verification_scope": ["single_pc_serial_current_user"],
        "limitations": ["not_design_citable", "not_release_ready"],
        "evidence_sha256": dict(sorted(evidence_sha256.items())),
    }


def write_working_validation(projects_root: Path, output: Path) -> dict:
    """Atomically write the recomputed single-PC manifest."""
    payload = evaluate_working_validation(projects_root)
    output = Path(output).resolve()
    payload["output_path"] = str(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    return payload


def compare_working_validation_runs(first: Path, second: Path) -> dict:
    """Compare canonical status/check/evidence hashes, excluding created_at and output path."""
    first_payload = _read_json(Path(first))
    second_payload = _read_json(Path(second))
    if first_payload is None or second_payload is None:
        raise ValueError("WORKING_VALIDATION_RUN_MALFORMED")
    keys = ("contract", "scope", "status", "working_ready_on_target",
            "limited_numerical_spotchecks_pass_on_target", "design_citable",
            "release_ready", "checks", "blockers", "numerical_blockers", "evidence_sha256")
    differences = [key for key in keys if first_payload.get(key) != second_payload.get(key)]
    return {"equal": not differences, "differences": differences}
