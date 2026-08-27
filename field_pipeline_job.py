"""Restartable one-click field-DXF to 3 FTT design simulation pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from contextlib import nullcontext
from pathlib import Path
import re
import time

import cfd_gci_job
import cfd_case_health
import cfd_evidence
import cfd_mesh
import cfd_occ
import cfd_power
import cfd_result_gate
import cfd_review
import cfd_validation_anchor
import field_acceptance
import project_model
from geometry_v2 import migrate_geometry, validate_for_body_fitted
from jsonschema import Draft202012Validator


CONTRACT_V1 = "field_pipeline_job.v1"
CONTRACT_V2 = "field_pipeline_job.v2"
CONTRACT = CONTRACT_V1
DEFAULT_BACKGROUND_CELL_M = 0.35
TARGET_FLOW_THROUGH_FRACTION = 3.0
ANALYSIS_COMPLETE_NOT_CITABLE = "analysis_complete_not_citable"
TERMINAL_STATUSES = frozenset(("complete", ANALYSIS_COMPLETE_NOT_CITABLE))


def _now():
    return datetime.now(timezone.utc).isoformat()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _normalise_job_document(value):
    if not isinstance(value, dict):
        return None
    version = value.get("schema_version")
    contract = value.get("contract")
    if version == 1 and contract == CONTRACT_V1:
        result = dict(value)
        result.setdefault("case_identity_status", "NOT_LINKED")
        return result
    if version != 2 or contract != CONTRACT_V2:
        return None
    schema = _read(Path(__file__).resolve().parent / "field_pipeline_job.v2.schema.json")
    if list(Draft202012Validator(schema).iter_errors(value)):
        return None
    return dict(value)


def is_terminal_status(status):
    """Return whether a persisted job has finished its raw analysis chain."""
    return status in TERMINAL_STATUSES


_HEALTH_SNAPSHOT_KEYS = (
    "case_evidence_path", "case_evidence_sha256",
    "case_health_path", "case_health_sha256", "review_summary",
)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_authoritative_case_binding(manifest):
    """Revalidate a field job's role document against its exact solver case."""
    manifest = manifest if isinstance(manifest, dict) else {}
    reference = manifest.get("validation_anchor")
    solver_case = str(manifest.get("authoritative_solver_case") or "").strip()
    if reference is None and not solver_case:
        return []
    if not isinstance(reference, dict) or not solver_case:
        return [{
            "code": "FIELD_AUTHORITY_BINDING_INCOMPLETE",
            "message": "validation_anchor and authoritative_solver_case are both required",
        }]
    try:
        current = cfd_validation_anchor.anchor_reference(
            reference.get("path"), expected_case=solver_case,
            expected_role="field_authority",
        )
    except (OSError, cfd_validation_anchor.ValidationAnchorError) as exc:
        code = (
            "FIELD_AUTHORITY_CASE_MISMATCH"
            if "ANCHOR_CASE_MISMATCH" in str(exc)
            else "FIELD_AUTHORITY_ANCHOR_INVALID"
        )
        return [{"code": code, "message": str(exc)}]
    if current != reference:
        return [{
            "code": "FIELD_AUTHORITY_ANCHOR_CHANGED",
            "message": "current anchor reference differs from the field manifest",
        }]
    if manifest.get("authoritative_case_sha256") != reference.get("binding_sha256"):
        return [{
            "code": "FIELD_AUTHORITY_BINDING_HASH_MISMATCH",
            "message": "authoritative_case_sha256 must equal the live anchor binding hash",
        }]
    if not str(manifest.get("validation_study_id") or "").strip() or not str(
        manifest.get("authority_reason") or ""
    ).strip():
        return [{
            "code": "FIELD_AUTHORITY_METADATA_INCOMPLETE",
            "message": "validation_study_id and authority_reason are required",
        }]
    input_payload = manifest.get("input")
    if isinstance(input_payload, dict):
        expected_authority = {
            key: manifest.get(key) for key in (
                "validation_anchor", "authoritative_solver_case",
                "authoritative_case_sha256", "validation_study_id",
                "authority_reason",
            )
        }
        if input_payload.get("validation_authority") != expected_authority:
            return [{
                "code": "FIELD_AUTHORITY_INPUT_SNAPSHOT_MISMATCH",
                "message": "field input authority snapshot differs from top-level authority",
            }]
    result_case = str(manifest.get("result_case") or "").strip()
    if result_case and (
        Path(result_case).expanduser().resolve(strict=False)
        != Path(solver_case).expanduser().resolve(strict=False)
    ):
        return [{
            "code": "FIELD_RESULT_CASE_NOT_AUTHORITATIVE",
            "message": "terminal field result is not the anchored authoritative solver case",
        }]
    return []


def _review_lock_for_case(root, solver_case):
    root = Path(root).expanduser().resolve()
    if not str(solver_case or "").strip():
        return nullcontext()
    case = Path(solver_case).expanduser()
    if not case.is_absolute():
        case = root / case
    case = cfd_review.safe_project_directory(case, projects_root=root)
    if case is None:
        return nullcontext()
    return cfd_review.review_state_lock(
        case / "case_evidence.v1.json", projects_root=root
    )


def _current_health_snapshot_locked(root, solver_case):
    """Build and validate current evidence/health; caller holds review lock."""
    root = Path(root).expanduser().resolve()
    case = Path(solver_case).expanduser()
    if not case.is_absolute():
        case = root / case
    try:
        case = case.resolve(strict=True)
        case.relative_to(root)
        cfd_evidence.build_case_evidence(case, projects_root=root)
        evidence_path = case / "case_evidence.v1.json"
        evidence_errors = cfd_evidence.validate_case_evidence(
            evidence_path, projects_root=root
        )
        if evidence_errors:
            raise ValueError("current Case Evidence failed revalidation")
        health = cfd_case_health.build_case_health(
            evidence_path, projects_root=root
        )
        health_path = case / "case_health.v1.json"
        evidence_errors = cfd_evidence.validate_case_evidence(
            evidence_path, projects_root=root
        )
        if evidence_errors:
            raise ValueError("Case Evidence changed after health publication")
        evidence_bytes = evidence_path.read_bytes()
        evidence_hash = hashlib.sha256(evidence_bytes).hexdigest()
        health_bytes = health_path.read_bytes()
        health_on_disk = json.loads(health_bytes.decode("utf-8"))
        health_hash = hashlib.sha256(health_bytes).hexdigest()
        if health_on_disk != health:
            raise ValueError("published Case Health differs from returned health")
        evidence_link = (
            health_on_disk.get("evidence")
            if isinstance(health_on_disk.get("evidence"), dict)
            else {}
        )
        if (
            evidence_link.get("path") != evidence_path.relative_to(root).as_posix()
            or evidence_link.get("sha256") != evidence_hash
            or _sha256(evidence_path) != evidence_hash
        ):
            raise ValueError("Case Health is not bound to current Case Evidence")
        citation_status = health_on_disk.get("citation_status")
        if citation_status not in {
            "SCREENING_ONLY", "NOT_EVALUATED", "CITATION_BLOCKED", "DESIGN_CITABLE",
        }:
            raise ValueError("current Case Health has an invalid citation status")
        blockers = [
            item["code"] for item in health_on_disk.get("errors") or []
            if isinstance(item, dict) and isinstance(item.get("code"), str)
        ]
        if citation_status == "DESIGN_CITABLE":
            blockers = []
        review = cfd_case_health.review_summary(
            evidence_path, projects_root=root
        )
        if citation_status == "DESIGN_CITABLE" and review.get("status") != "APPROVED":
            raise ValueError("review state changed after health publication")
        final_evidence_errors = cfd_evidence.validate_case_evidence(
            evidence_path, projects_root=root
        )
        if (
            final_evidence_errors
            or evidence_path.read_bytes() != evidence_bytes
            or health_path.read_bytes() != health_bytes
        ):
            raise ValueError("evidence or health changed during snapshot assembly")
        return {
            "citation_status": citation_status,
            "citation_blockers": list(dict.fromkeys(blockers)),
            "case_evidence_path": evidence_path.relative_to(root).as_posix(),
            "case_evidence_sha256": evidence_hash,
            "case_health_path": health_path.relative_to(root).as_posix(),
            "case_health_sha256": health_hash,
            "review_summary": review,
        }
    except Exception:
        return {
            "citation_status": "CITATION_BLOCKED",
            "citation_blockers": ["CASE_EVIDENCE_NOT_FOUND"],
            "review_summary": {"status": "INVALID"},
        }


def _current_health_snapshot(root, solver_case):
    with _review_lock_for_case(root, solver_case):
        return _current_health_snapshot_locked(root, solver_case)


def review_terminal_job_citation(root, manifest):
    """Return the current citation truth for a finished field analysis.

    Older job manifests may have been written before the result gate existed.
    Re-read the immutable CFD artifacts before presenting their ``complete``
    state so a stale manifest cannot be mistaken for a design-citable result.
    """
    reviewed = dict(manifest or {})
    if not is_terminal_status(reviewed.get("status")):
        return reviewed
    authority_issues = validate_authoritative_case_binding(reviewed)
    if authority_issues:
        reviewed.update(
            status=ANALYSIS_COMPLETE_NOT_CITABLE,
            citation_status="CITATION_BLOCKED",
            citation_blockers=[item["code"] for item in authority_issues],
            review_summary={"status": "INVALID"},
        )
        return reviewed
    for key in (*_HEALTH_SNAPSHOT_KEYS, "citation_reasons", "citation_gate"):
        reviewed.pop(key, None)
    solver_case = str(reviewed.get("result_case") or "").strip()
    snapshot = (
        _current_health_snapshot(root, solver_case)
        if solver_case
        else {
            "citation_status": "CITATION_BLOCKED",
            "citation_blockers": ["CASE_EVIDENCE_NOT_FOUND"],
            "review_summary": {"status": "MISSING"},
        }
    )
    reviewed.update(
        status=("complete" if snapshot["citation_status"] == "DESIGN_CITABLE"
                else ANALYSIS_COMPLETE_NOT_CITABLE),
        **snapshot,
    )
    return reviewed


def _job_path(root, job_id):
    return Path(root).expanduser().resolve() / "_field_jobs" / job_id / "field_pipeline_job.json"


def _source_path(geometry, geometry_path):
    source = geometry.get("source")
    if isinstance(source, dict):
        source = source.get("path") or source.get("file") or source.get("name")
    source = Path(str(source or ""))
    if not source.is_absolute():
        source = Path(geometry_path).parent / source
    return source.resolve()


def _semantic_issues(geometry):
    issues = list(validate_for_body_fitted(geometry))
    elements = geometry.get("elements") or {}
    zones = [row for row in elements.get("zone") or []
             if row.get("closed") and row.get("confirmed")]
    equipment = elements.get("equipment") or []
    supplies = [row for row in equipment
                if (row.get("semantic") or {}).get("kind") == "air_terminal"
                and (row.get("semantic") or {}).get("role") == "supply"]
    exhausts = [row for row in equipment
                if (row.get("semantic") or {}).get("kind") == "air_terminal"
                and (row.get("semantic") or {}).get("role") == "exhaust"]
    heat_sources = [row for row in equipment
                    if (row.get("semantic") or {}).get("role") == "heat_source"]
    if len(zones) != 1:
        issues.append({"code": "SINGLE_ZONE_REQUIRED"})
    if not supplies:
        issues.append({"code": "SUPPLY_MISSING"})
    if not exhausts:
        issues.append({"code": "EXHAUST_MISSING"})
    if not heat_sources:
        issues.append({"code": "HEAT_SOURCE_MISSING"})
    try:
        supply = sum(float((row.get("semantic") or {}).get("airflow_cmh") or 0)
                     for row in supplies)
        exhaust = sum(float((row.get("semantic") or {}).get("airflow_cmh") or 0)
                      for row in exhausts)
        reference = max(supply, exhaust)
        if reference <= 0 or abs(supply - exhaust) / reference > 0.01:
            issues.append({"code": "TERMINAL_FLOW_IMBALANCE"})
    except (TypeError, ValueError):
        issues.append({"code": "TERMINAL_FLOW_IMBALANCE"})
    return issues


def load_job(root, job_id):
    if not re.fullmatch(r"field-[0-9a-f]{12}", str(job_id or "")):
        return None
    path = _job_path(root, job_id)
    try:
        return _normalise_job_document(_read(path)) if path.is_file() else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def list_jobs(root):
    base = Path(root).expanduser().resolve() / "_field_jobs"
    rows = []
    if base.is_dir():
        for path in base.glob("field-*/field_pipeline_job.json"):
            try:
                row = _normalise_job_document(_read(path))
                if row is not None:
                    identity_issues = validate_job_identity(root, row)
                    if identity_issues:
                        row["case_identity_status"] = identity_issues[0]["code"]
                    rows.append(row)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
    rows.sort(key=lambda row: row.get("updated_at") or "", reverse=True)
    return rows


def active_run_lock(root, job_id):
    if load_job(root, job_id) is None:
        return None
    return cfd_gci_job.active_job_lock(_job_path(root, job_id))


def _identity_fields(root, requested):
    raw_path = str(requested.get("case_identity_path") or "").strip()
    if not raw_path:
        return None
    root = Path(root).resolve()
    identity_path = Path(raw_path).expanduser()
    if not identity_path.is_absolute():
        identity_path = root / identity_path
    try:
        identity_path = identity_path.resolve(strict=True)
        identity_path.relative_to(root)
    except (OSError, ValueError) as exc:
        raise project_model.ProjectModelError(
            "RUN_IDENTITY_PATH_ESCAPE", str(identity_path),
        ) from exc
    issues = project_model.validate_case_identity(identity_path, projects_root=root)
    if issues:
        raise project_model.ProjectModelError("RUN_IDENTITY_INVALID", str(issues))
    identity = _read(identity_path)
    return {
        "case_identity_path": identity_path.relative_to(root).as_posix(),
        "case_identity_sha256": _sha256(identity_path),
        "design_revision_sha256": identity["design"]["revision_sha256"],
        "scenario_revision_sha256": identity["scenario"]["revision_sha256"],
        "case_identity_status": "LINKED",
    }


def validate_job_identity(root, manifest):
    if manifest.get("contract") == CONTRACT_V1:
        return []
    if manifest.get("contract") != CONTRACT_V2:
        return [{"code": "RUN_IDENTITY_CHANGED", "message": "unknown job contract"}]
    root = Path(root).resolve()
    try:
        identity_path = root / manifest["case_identity_path"]
        identity_path = identity_path.resolve(strict=True)
        identity_path.relative_to(root)
        if _sha256(identity_path) != manifest.get("case_identity_sha256"):
            raise ValueError("case identity bytes changed")
        identity = _read(identity_path)
        if (
            identity.get("design", {}).get("revision_sha256")
            != manifest.get("design_revision_sha256")
            or identity.get("scenario", {}).get("revision_sha256")
            != manifest.get("scenario_revision_sha256")
            or project_model.validate_case_identity(identity_path, projects_root=root)
        ):
            raise ValueError("case identity references changed")
        return project_model.validate_case_identity_lifecycle(
            identity_path, projects_root=root,
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return [{"code": "RUN_IDENTITY_CHANGED", "message": str(exc)}]


def create_job(root, geometry_path, settings=None):
    """Create or load a deterministic field pipeline job."""
    root = Path(root).expanduser().resolve()
    geometry_path = Path(str(geometry_path or "")).expanduser().resolve()
    try:
        geometry_path.relative_to(root)
    except (OSError, ValueError):
        return {"ok": False, "error": "프로젝트 안으로 불러온 도면만 자동 해석할 수 있습니다."}
    if not geometry_path.is_file():
        return {"ok": False, "error": "확정된 geometry.json 파일을 찾을 수 없습니다."}
    try:
        raw = _read(geometry_path)
        if not isinstance(raw, dict):
            raise ValueError("geometry.json 최상위 값은 객체여야 합니다.")
        geometry = migrate_geometry(raw, source_path=str(raw.get("source") or geometry_path))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"도면 변환 정보를 읽지 못했습니다: {exc}"}
    issues = _semantic_issues(geometry)
    if issues:
        return {"ok": False, "error": "3D/CFD 입력 확인이 끝나지 않았습니다.",
                "issues": issues[:10]}
    source_dxf = _source_path(raw, geometry_path)
    try:
        source_dxf.relative_to(root)
    except (OSError, ValueError):
        return {"ok": False, "error": "원본 DXF가 프로젝트 안에 보존되어 있지 않습니다. DXF를 다시 불러오세요."}
    if source_dxf.suffix.lower() != ".dxf" or not source_dxf.is_file():
        return {"ok": False, "error": "원본 DXF 파일을 찾을 수 없습니다. DXF를 다시 불러오세요."}
    if field_acceptance.is_bundled_sample_drawing(source_dxf, root):
        return {
            "ok": False,
            "error": "프로그램에 포함된 샘플은 현장 실증 계산에 사용할 수 없습니다.",
        }

    requested = dict(settings or {})
    try:
        identity_fields = _identity_fields(root, requested)
    except project_model.ProjectModelError as exc:
        return {"ok": False, "code": exc.code, "error": str(exc)}
    authority_fields = None
    authority_values = {
        "validation_anchor_path": str(requested.get("validation_anchor_path") or "").strip(),
        "authoritative_solver_case": str(requested.get("authoritative_solver_case") or "").strip(),
        "validation_study_id": str(requested.get("validation_study_id") or "").strip(),
        "authority_reason": str(requested.get("authority_reason") or "").strip(),
    }
    if any(authority_values.values()):
        if not all(authority_values.values()):
            return {
                "ok": False,
                "code": "FIELD_AUTHORITY_BINDING_INCOMPLETE",
                "error": "Validation Anchor authority fields must be supplied together.",
            }
        authoritative_case = Path(
            authority_values["authoritative_solver_case"]
        ).expanduser().resolve(strict=False)
        try:
            authoritative_case.relative_to(root)
            anchor_reference = cfd_validation_anchor.anchor_reference(
                authority_values["validation_anchor_path"],
                expected_case=authoritative_case,
                expected_role="field_authority",
            )
        except (OSError, ValueError, cfd_validation_anchor.ValidationAnchorError) as exc:
            return {
                "ok": False,
                "code": "FIELD_AUTHORITY_ANCHOR_INVALID",
                "error": str(exc),
            }
        authority_fields = {
            "validation_anchor": anchor_reference,
            "authoritative_solver_case": str(authoritative_case),
            "authoritative_case_sha256": anchor_reference["binding_sha256"],
            "validation_study_id": authority_values["validation_study_id"],
            "authority_reason": authority_values["authority_reason"],
        }
    try:
        width = float(requested.get("background_cell_m", DEFAULT_BACKGROUND_CELL_M))
    except (TypeError, ValueError):
        return {"ok": False, "error": "메시 크기는 숫자여야 합니다."}
    if not 0.05 <= width <= 2.0:
        return {"ok": False, "error": "메시 크기는 0.05~2.0 m 범위여야 합니다."}
    mesh_settings = dict(requested.get("mesh_settings") or {})
    mesh_settings.update({"preset": "detailed", "background_cell_m": width})
    thermal_settings = dict(requested.get("thermal_settings") or {})
    thermal_settings["thermal_minimum_flow_through_fraction"] = TARGET_FLOW_THROUGH_FRACTION
    thermal_settings.setdefault("thermal_max_single_run_s", 20.0)
    thermal_settings.setdefault("thermal_continuation_write_interval_s", 2.0)
    job_input = {
        "geometry_path": str(geometry_path),
        "geometry_sha256": cfd_gci_job._sha256(geometry_path),
        "source_dxf_path": str(source_dxf),
        "source_dxf_sha256": cfd_gci_job._sha256(source_dxf),
        "background_cell_m": width,
        "mesh_settings": mesh_settings,
        "isothermal_settings": dict(requested.get("isothermal_settings") or {}),
        "thermal_settings": thermal_settings,
    }
    if identity_fields is not None:
        job_input["run_identity"] = {
            key: identity_fields[key] for key in (
                "case_identity_path", "case_identity_sha256",
                "design_revision_sha256", "scenario_revision_sha256",
            )
        }
    if authority_fields is not None:
        job_input["validation_authority"] = dict(authority_fields)
    job_id = "field-" + cfd_gci_job._canonical_hash(job_input)[:12]
    path = _job_path(root, job_id)
    existing = load_job(root, job_id)
    if existing is not None:
        return {"ok": True, "job": job_id, "manifest": existing,
                "manifest_path": str(path), "existing": True}
    manifest = {
        "schema_version": 2 if identity_fields is not None else 1,
        "contract": CONTRACT_V2 if identity_fields is not None else CONTRACT_V1,
        "engine": "body_fitted_field_pipeline",
        "created_at": _now(), "updated_at": _now(),
        "job": job_id, "study": job_id,
        "status": "queued", "stage": "queued", "attempts": 0,
        "error": "", "attempt_history": [], "resume_history": [],
        "total_elapsed_s": 0.0, "input": job_input, "occ_output": "",
        "level": {
            "name": "design", "background_cell_m": width,
            "status": "pending", "stage": "pending", "error": "",
            "mesh_case": "", "isothermal_case": "", "thermal_case": "",
            "cell_count": None, "latest_time_s": None,
            "flow_through_fraction": 0.0,
        },
        "result_case": "", "report_path": "",
        "citation_status": "NOT_EVALUATED", "citation_blockers": [],
        "review_summary": {"status": "MISSING"},
    }
    manifest.update(identity_fields or {"case_identity_status": "NOT_LINKED"})
    manifest.update(authority_fields or {})
    cfd_gci_job._atomic_json(path, manifest)
    return {"ok": True, "job": job_id, "manifest": manifest,
            "manifest_path": str(path), "existing": False}


def _publish(path, manifest, callback=None, message=""):
    manifest["updated_at"] = _now()
    cfd_gci_job._atomic_json(path, manifest)
    if callback:
        callback({"job": manifest["job"], "stage": manifest["stage"],
                  "message": message or manifest["stage"],
                  "level": manifest["level"]})


def _complete_from_authoritative_case(root, path, manifest, callback=None):
    """Publish a field job from its anchored fine case without a second solve."""
    root = Path(root).expanduser().resolve()
    completed = Path(manifest["authoritative_solver_case"]).expanduser().resolve(strict=True)
    completed.relative_to(root)
    started_at = _now()
    started = time.monotonic()
    manifest = dict(manifest)
    manifest.update(
        status="running", stage="authoritative_case_revalidation", error="",
        attempts=int(manifest.get("attempts") or 0) + 1,
        attempt_started_at=started_at,
    )
    _publish(path, manifest, callback, "Validation Anchor 권위 케이스 재검증")
    if manifest.get("contract") == CONTRACT_V2:
        link = project_model.link_run_identity(
            completed, root / manifest["case_identity_path"],
        )
        manifest["case_identity_status"] = link["case_identity_status"]
    snapshot = _current_health_snapshot(root, completed)
    final_status = (
        "complete" if snapshot["citation_status"] == "DESIGN_CITABLE"
        else ANALYSIS_COMPLETE_NOT_CITABLE
    )
    elapsed = round(time.monotonic() - started, 3)
    attempts = list(manifest.get("attempt_history") or [])
    attempts.append({
        "attempt": manifest["attempts"], "started_at": started_at,
        "finished_at": _now(), "elapsed_s": elapsed,
        "status": final_status, "mode": "authoritative_case_reuse",
    })
    level = dict(manifest.get("level") or {})
    level.update(
        status="PASS" if final_status == "complete" else "WARN",
        stage="complete", error="", thermal_case=str(completed),
    )
    manifest.update(
        status=final_status, stage="complete", error="", level=level,
        result_case=str(completed),
        report_path=str(completed / "body_fitted_report.html"),
        **snapshot,
        completed_at=_now(), attempt_history=attempts,
        last_attempt_elapsed_s=elapsed,
        total_elapsed_s=round(float(manifest.get("total_elapsed_s") or 0) + elapsed, 3),
    )
    _publish(path, manifest, callback, "Validation Anchor 권위 케이스 연결 완료")
    return {
        "ok": True, "job": manifest["job"], "manifest": manifest,
        "case": completed.name, "authoritative_case_reused": True,
    }


def _run_unlocked(root, job_id, callback=None):
    root = Path(root).expanduser().resolve()
    path = _job_path(root, job_id)
    manifest = load_job(root, job_id)
    if manifest is None:
        return {"ok": False, "error": "현장 자동 해석 작업을 찾을 수 없습니다."}
    started = time.monotonic()
    started_at = _now()
    previous_status = manifest.get("status")
    if int(manifest.get("attempts") or 0) and previous_status in ("running", "FAIL"):
        history = list(manifest.get("resume_history") or [])
        history.append({
            "resumed_at": started_at, "previous_status": previous_status,
            "previous_stage": manifest.get("stage"),
            "previous_attempt": manifest.get("attempts"),
            "checkpoint_time_s": manifest["level"].get("latest_time_s"),
            "flow_through_fraction": manifest["level"].get("flow_through_fraction"),
        })
        manifest["resume_history"] = history
    manifest.update(status="running", stage="starting", error="",
                    attempts=int(manifest.get("attempts") or 0) + 1,
                    attempt_started_at=started_at)
    _publish(path, manifest, callback, "현장 도면 자동 해석 시작")
    try:
        geometry_path = Path(manifest["input"]["geometry_path"])
        source_dxf = Path(manifest["input"]["source_dxf_path"])
        if cfd_gci_job._sha256(geometry_path) != manifest["input"]["geometry_sha256"]:
            raise RuntimeError("작업 생성 후 도면 확인 정보가 변경되었습니다.")
        if cfd_gci_job._sha256(source_dxf) != manifest["input"]["source_dxf_sha256"]:
            raise RuntimeError("작업 생성 후 원본 DXF가 변경되었습니다.")
        token = job_id.split("-", 1)[1]
        occ_output = root / "_occ_geometry" / f"{cfd_gci_job._safe_stem(geometry_path)}-field-{token}"
        if not cfd_occ.inspect_occ_output(occ_output).get("ok"):
            manifest["stage"] = "occ"
            _publish(path, manifest, callback, "3D 공기영역 생성")
            result = cfd_occ.run_occ_job(geometry_path, occ_output)
            if not result.get("ok"):
                raise RuntimeError(result.get("error") or "3D 공기영역 생성 실패")
        inspected = cfd_occ.inspect_occ_output(occ_output)
        if not inspected.get("ok"):
            raise RuntimeError(inspected.get("error") or "3D 공기영역 검증 실패")
        manifest["occ_output"] = str(occ_output)
        estimate = cfd_mesh.estimate_resources(
            inspected["manifest"], settings=manifest["input"]["mesh_settings"]
        )
        manifest["level"]["resource_estimate"] = {
            key: estimate.get(key) for key in (
                "background_cells", "estimated_cells", "estimated_ram_gb",
                "estimated_disk_gb",
            )
        }
        _publish(path, manifest, callback, "3D 공기영역 준비 완료")

        target_flow_fraction = float((
            manifest["input"].get("thermal_settings") or {}
        ).get(
            "thermal_minimum_flow_through_fraction",
            TARGET_FLOW_THROUGH_FRACTION,
        ))
        completed = cfd_gci_job.validate_completed_design_level(
            manifest["level"], target_flow_fraction=target_flow_fraction
        )
        if completed is None:
            completed = cfd_gci_job.run_thermal_design_level(
                root, occ_output, manifest, manifest["level"], path, callback,
                case_prefix=f"{cfd_gci_job._safe_stem(geometry_path)}-field-{token}-design",
            )
        progress = _read(Path(completed) / "run_manifest.json").get("thermal_progress") or {}
        raw_status = manifest["level"].get("status")
        if raw_status not in ("PASS", "WARN"):
            raw_status = "WARN"
        manifest["level"].update(
            status=raw_status, stage="complete", error="",
            latest_time_s=progress.get("latest_time_s"),
            flow_through_fraction=float(progress.get("flow_through_fraction") or 0),
        )
        if manifest.get("contract") == CONTRACT_V2:
            link = project_model.link_run_identity(
                completed, root / manifest["case_identity_path"],
            )
            manifest["case_identity_status"] = link["case_identity_status"]
        snapshot = _current_health_snapshot(root, completed)
        final_status = (
            "complete" if snapshot["citation_status"] == "DESIGN_CITABLE"
            else ANALYSIS_COMPLETE_NOT_CITABLE
        )
        elapsed = round(time.monotonic() - started, 3)
        attempts = list(manifest.get("attempt_history") or [])
        attempts.append({"attempt": manifest["attempts"], "started_at": started_at,
                         "finished_at": _now(), "elapsed_s": elapsed,
                         "status": final_status})
        manifest.update(
            status=final_status, stage="complete", error="",
            result_case=str(completed),
            report_path=str(Path(completed) / "body_fitted_report.html"),
            **snapshot,
            completed_at=_now(), attempt_history=attempts,
            last_attempt_elapsed_s=elapsed,
            total_elapsed_s=round(float(manifest.get("total_elapsed_s") or 0) + elapsed, 3),
        )
        _publish(path, manifest, callback, "3.0 FTT 현장 설계 해석 완료")
        return {"ok": True, "job": job_id, "manifest": manifest,
                "case": Path(completed).name}
    except Exception as exc:
        elapsed = round(time.monotonic() - started, 3)
        attempts = list(manifest.get("attempt_history") or [])
        attempts.append({"attempt": manifest["attempts"], "started_at": started_at,
                         "finished_at": _now(), "elapsed_s": elapsed,
                         "status": "FAIL", "error": str(exc)})
        manifest.update(status="FAIL", error=str(exc), failed_at=_now(),
                        attempt_history=attempts, last_attempt_elapsed_s=elapsed,
                        total_elapsed_s=round(float(manifest.get("total_elapsed_s") or 0) + elapsed, 3))
        manifest["level"].update(status="FAIL", error=str(exc))
        _publish(path, manifest, callback, f"자동 해석 중단: {exc}")
        return {"ok": False, "job": job_id, "error": str(exc),
                "manifest": manifest}


def run_job(root, job_id, callback=None):
    root = Path(root).expanduser().resolve()
    path = _job_path(root, job_id)
    existing = load_job(root, job_id)
    if existing is None:
        return {"ok": False, "error": "현장 자동 해석 작업을 찾을 수 없습니다."}
    authority_issues = validate_authoritative_case_binding(existing)
    if authority_issues:
        return {
            "ok": False,
            "code": authority_issues[0]["code"],
            "error": "Validation Anchor와 authoritative solver case가 일치하지 않습니다.",
            "issues": authority_issues,
            "job": job_id,
            "manifest": existing,
        }
    identity_issues = validate_job_identity(root, existing)
    if identity_issues:
        issue_code = identity_issues[0]["code"]
        return {
            "ok": False,
            "code": issue_code,
            "error": "Design/Scenario/Run identity가 변경되어 재개할 수 없습니다.",
            "issues": identity_issues,
            "job": job_id,
            "manifest": existing,
        }
    if is_terminal_status(existing.get("status")):
        with _review_lock_for_case(root, existing.get("result_case")):
            reviewed = review_terminal_job_citation(root, existing)
            _publish(path, reviewed)
        return {
            "ok": True, "job": job_id, "manifest": reviewed,
            "case": Path(str(reviewed.get("result_case") or "")).name,
            "already_complete": True,
        }
    if str(existing.get("authoritative_solver_case") or "").strip():
        token, owner = cfd_gci_job.acquire_job_lock(path)
        if token is None:
            return {
                "ok": False, "code": "FIELD_JOB_ALREADY_RUNNING",
                "error": (
                    "현장 권위 케이스 재검증이 이미 실행 중입니다. "
                    f"PID {owner.get('pid', 'unknown')}"
                ),
                "job": job_id, "lock": owner,
            }
        try:
            return _complete_from_authoritative_case(
                root, path, existing, callback=callback,
            )
        finally:
            cfd_gci_job.release_job_lock(path, token)
    running_gci = None
    for row in cfd_gci_job.list_studies(root):
        busy_owner = cfd_gci_job.active_run_lock(root, row.get("study"))
        if busy_owner is not None:
            running_gci = (row.get("study"), busy_owner)
            break
    if running_gci is not None:
        study, busy_owner = running_gci
        return {"ok": False, "code": "CFD_SOLVER_BUSY",
                "error": (f"메시 검증 작업 {study}이 OpenFOAM을 사용 중입니다. "
                          f"PID {busy_owner.get('pid', 'unknown')}"),
                "job": job_id, "lock": busy_owner}
    token, owner = cfd_gci_job.acquire_job_lock(path)
    if token is None:
        return {"ok": False, "code": "FIELD_JOB_ALREADY_RUNNING",
                "error": f"현장 자동 해석이 이미 실행 중입니다. PID {owner.get('pid', 'unknown')}",
                "job": job_id, "lock": owner}
    solver_token, solver_owner = cfd_gci_job.acquire_solver_lock(root)
    if solver_token is None:
        cfd_gci_job.release_job_lock(path, token)
        return {"ok": False, "code": "CFD_SOLVER_BUSY",
                "error": ("다른 CFD 작업이 OpenFOAM을 사용 중입니다. "
                          f"PID {solver_owner.get('pid', 'unknown')}"),
                "job": job_id, "lock": solver_owner}
    try:
        with cfd_power.keep_system_awake():
            return _run_unlocked(root, job_id, callback=callback)
    finally:
        cfd_gci_job.release_solver_lock(root, solver_token)
        cfd_gci_job.release_job_lock(path, token)
