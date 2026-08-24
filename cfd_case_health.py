"""Case Health projection and citation decision over current Case Evidence."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from jsonschema import Draft202012Validator

import cfd_evidence
import cfd_review
from cfd_status_catalog import (
    CASE_HEALTH_CHECKS,
    CITATION_DECISION_TABLE,
    CITATION_DECISION_TABLE_VERSION,
    CITATION_STATUSES,
    EVIDENCE_CHECKS,
    PURPOSE_PROFILES,
    STATUS_CATALOG,
    status_descriptor,
)


CONTRACT = "case_health.v1"
_STATUS_PRECEDENCE = ("FAIL", "BLOCKED", "NOT_EVALUATED", "PASS")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _schema() -> tuple[dict, Draft202012Validator]:
    path = Path(__file__).resolve().with_name("case_health.v1.schema.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload, Draft202012Validator(payload)


def _assert_catalog_contract(schema: dict) -> None:
    properties = schema.get("properties") or {}
    schema_version = (properties.get("citation_decision_table_version") or {}).get("const")
    schema_table = (properties.get("citation_decision_table") or {}).get("const")
    required_checks = (((schema.get("$defs") or {}).get("checks") or {}).get("required"))
    if (
        schema_version != CITATION_DECISION_TABLE_VERSION
        or schema_table != list(CITATION_DECISION_TABLE)
        or required_checks != list(CASE_HEALTH_CHECKS)
    ):
        raise RuntimeError("CITATION_DECISION_TABLE_MISMATCH")


def _unique(values):
    return list(dict.fromkeys(values))


def _descriptor_for(status: str, reasons: list[str]) -> dict[str, str]:
    for code in reasons:
        if code in STATUS_CATALOG:
            return status_descriptor(code)
    fallback = {
        "PASS": "DESIGN_CITABLE",
        "NOT_EVALUATED": "EVIDENCE_STATUS_NOT_EVALUATED",
        "BLOCKED": "CITATION_BLOCKED",
        "FAIL": "CITATION_BLOCKED",
    }[status]
    return status_descriptor(fallback)


def _health_check(status: str, reasons: list[str], refs: list[str]) -> dict:
    reasons = _unique(reasons)
    refs = _unique(refs)
    descriptor = _descriptor_for(status, reasons)
    return {
        "status": status,
        "reason_codes": reasons,
        "evidence_refs": refs,
        "impact": descriptor["impact"],
        "next_actions": [descriptor["next_action"]],
    }


def _design_status(source_checks: list[dict]) -> str:
    statuses = {row["status"] for row in source_checks}
    return next(status for status in _STATUS_PRECEDENCE if status in statuses)


def _decision(
    evidence: dict,
    evidence_errors: list[dict],
    source_by_id: dict[str, dict],
    review_state: dict,
) -> tuple[str, str, list[dict]]:
    purpose = evidence["purpose"]
    profile = PURPOSE_PROFILES[purpose]
    required = [source_by_id[check_id] for check_id in profile["required_checks"]]
    review_errors = review_state["errors"] if profile["review_required"] else []
    if evidence_errors or review_errors:
        return (
            "CITATION_BLOCKED",
            "CITATION_EVIDENCE_OR_REVIEW_INVALID",
            [*evidence_errors, *review_errors],
        )
    if any(row["status"] in {"FAIL", "BLOCKED"} for row in required):
        return "CITATION_BLOCKED", "REQUIRED_CHECK_FAILED_OR_BLOCKED", []
    if profile["review_required"] and review_state["status"] == "REJECTED":
        return "CITATION_BLOCKED", "REVIEW_REJECTED", []
    if any(row["status"] == "NOT_EVALUATED" for row in required):
        return "NOT_EVALUATED", "REQUIRED_CHECK_NOT_EVALUATED", []
    if purpose == "benchmark":
        return "NOT_EVALUATED", "BENCHMARK_NOT_DESIGN_CITABLE", []
    if purpose == "screening" or "legacy_case_ref" in evidence:
        return "SCREENING_ONLY", "SCREENING_ONLY", []
    if review_state["status"] == "APPROVED":
        return "DESIGN_CITABLE", "DESIGN_CITABLE", []
    return "NOT_EVALUATED", "REVIEW_REQUIRED", []


def _atomic_json(path: Path, payload: dict) -> None:
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def review_summary(evidence_path: Path, *, projects_root: Path) -> dict:
    state = cfd_review.current_review_state(
        evidence_path, projects_root=projects_root
    )
    summary = {"status": state["status"]}
    if state.get("review_id"):
        summary["review_id"] = state["review_id"]
    return summary


def build_case_health(evidence_path: Path, *, projects_root: Path) -> dict:
    """Revalidate Case Evidence, project health, and publish the current snapshot."""
    schema, validator = _schema()
    _assert_catalog_contract(schema)
    root, evidence_file, evidence, digest, relative = cfd_review.resolve_evidence_target(
        evidence_path, projects_root=projects_root
    )
    evidence_errors = cfd_evidence.validate_case_evidence(
        evidence_file, projects_root=root
    )
    source_rows = evidence["checks"]
    source_by_id = {row["id"]: row for row in source_rows}
    if tuple(source_by_id) != EVIDENCE_CHECKS:
        raise ValueError("case evidence checks do not match the Task-1 catalog")
    checks: dict[str, dict] = {}
    for check_id in EVIDENCE_CHECKS:
        source = source_by_id[check_id]
        checks[check_id] = _health_check(
            source["status"], source["reason_codes"], source["evidence_refs"]
        )
    design_status = _design_status(source_rows)
    design_reasons = _unique(
        reason for row in source_rows for reason in row["reason_codes"]
    )
    design_refs = _unique(
        ref for row in source_rows for ref in row["evidence_refs"]
    )
    checks["design_ready"] = _health_check(
        design_status, design_reasons, design_refs
    )

    profile = PURPOSE_PROFILES[evidence["purpose"]]
    review_state = (
        cfd_review.current_review_state(evidence_file, projects_root=root)
        if profile["review_required"]
        else {"status": "MISSING", "review_id": None, "errors": []}
    )
    citation_status, decision_reason, related_errors = _decision(
        evidence, evidence_errors, source_by_id, review_state
    )
    if citation_status not in CITATION_STATUSES:
        raise RuntimeError("CITATION_DECISION_TABLE_MISMATCH")
    errors = [{"code": decision_reason}]
    for error in related_errors:
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            row = {"code": error["code"]}
            if isinstance(error.get("detail"), str) and error["detail"]:
                row["detail"] = error["detail"]
            if isinstance(error.get("evidence_ref"), str) and error["evidence_ref"]:
                row["evidence_ref"] = error["evidence_ref"]
            if row not in errors:
                errors.append(row)
    health = {
        "contract": CONTRACT,
        "schema_version": 1,
        "created_at": _now(),
        "purpose": evidence["purpose"],
        "evidence": {
            "contract": "case_evidence.v1",
            "path": relative,
            "sha256": digest,
        },
        "checks": checks,
        "status": design_status,
        "citation_status": citation_status,
        "citation_decision_table_version": CITATION_DECISION_TABLE_VERSION,
        "citation_decision_table": list(CITATION_DECISION_TABLE),
        "errors": errors,
    }
    identity_key = "case_identity" if "case_identity" in evidence else "legacy_case_ref"
    health[identity_key] = evidence[identity_key]
    schema_errors = list(validator.iter_errors(health))
    if schema_errors:
        raise RuntimeError(f"CASE_HEALTH_SCHEMA_MISMATCH: {schema_errors[0].message}")
    output = evidence_file.parent / "case_health.v1.json"
    if output.exists() and output.is_symlink():
        raise ValueError("case health output is unsafe")
    _atomic_json(output, health)
    return health
