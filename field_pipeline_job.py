"""Restartable one-click field-DXF to 3 FTT design simulation pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time

import cfd_gci_job
import cfd_mesh
import cfd_occ
import cfd_power
import cfd_result_gate
import field_acceptance
from geometry_v2 import migrate_geometry, validate_for_body_fitted


CONTRACT = "field_pipeline_job.v1"
DEFAULT_BACKGROUND_CELL_M = 0.35
TARGET_FLOW_THROUGH_FRACTION = 3.0
ANALYSIS_COMPLETE_NOT_CITABLE = "analysis_complete_not_citable"
TERMINAL_STATUSES = frozenset(("complete", ANALYSIS_COMPLETE_NOT_CITABLE))


def _now():
    return datetime.now(timezone.utc).isoformat()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def is_terminal_status(status):
    """Return whether a persisted job has finished its raw analysis chain."""
    return status in TERMINAL_STATUSES


def _citation_gate(root, solver_case):
    """Evaluate citation readiness without treating a gate problem as a solve failure."""
    try:
        gate = cfd_result_gate.evaluate_body_fitted_case(
            solver_case, gci_root=Path(root) / "_body_gci"
        )
    except Exception as exc:  # fail closed; the raw CFD result remains viewable
        return {
            "contract": cfd_result_gate.CONTRACT,
            "status": "NOT_EVALUATED",
            "design_ready": False,
            "citation_status": "NOT_EVALUATED",
            "citable": False,
            "blockers": ["result_gate_error"],
            "reasons": [f"결과 인용 가능성 판정을 완료하지 못했습니다: {exc}"],
        }
    if not isinstance(gate, dict):
        return {
            "contract": cfd_result_gate.CONTRACT,
            "status": "NOT_EVALUATED",
            "design_ready": False,
            "citation_status": "NOT_EVALUATED",
            "citable": False,
            "blockers": ["result_gate_invalid"],
            "reasons": ["결과 인용 가능성 판정 형식이 올바르지 않습니다."],
        }
    return gate


def _is_design_citable(gate):
    return (
        gate.get("status") == "PASS"
        and gate.get("design_ready") is True
        and gate.get("citation_status") == "DESIGN_CITABLE"
        and gate.get("citable") is True
    )


def review_terminal_job_citation(root, manifest):
    """Return the current citation truth for a finished field analysis.

    Older job manifests may have been written before the result gate existed.
    Re-read the immutable CFD artifacts before presenting their ``complete``
    state so a stale manifest cannot be mistaken for a design-citable result.
    """
    reviewed = dict(manifest or {})
    if not is_terminal_status(reviewed.get("status")):
        return reviewed
    solver_case = str(reviewed.get("result_case") or "").strip()
    if solver_case:
        gate = _citation_gate(root, solver_case)
    else:
        gate = {
            "contract": cfd_result_gate.CONTRACT,
            "status": "NOT_EVALUATED",
            "design_ready": False,
            "citation_status": "NOT_EVALUATED",
            "citable": False,
            "blockers": ["result_case_missing"],
            "reasons": ["완료된 현장 해석의 결과 케이스를 찾지 못했습니다."],
        }
    reviewed.update(
        status="complete" if _is_design_citable(gate) else ANALYSIS_COMPLETE_NOT_CITABLE,
        citation_status=gate.get("citation_status") or "NOT_EVALUATED",
        citation_blockers=list(gate.get("blockers") or []),
        citation_reasons=list(gate.get("reasons") or []),
        citation_gate=gate,
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
        return _read(path) if path.is_file() else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def list_jobs(root):
    base = Path(root).expanduser().resolve() / "_field_jobs"
    rows = []
    if base.is_dir():
        for path in base.glob("field-*/field_pipeline_job.json"):
            try:
                row = _read(path)
                if row.get("contract") == CONTRACT:
                    rows.append(row)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
    rows.sort(key=lambda row: row.get("updated_at") or "", reverse=True)
    return rows


def active_run_lock(root, job_id):
    if load_job(root, job_id) is None:
        return None
    return cfd_gci_job.active_job_lock(_job_path(root, job_id))


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
    job_id = "field-" + cfd_gci_job._canonical_hash(job_input)[:12]
    path = _job_path(root, job_id)
    existing = load_job(root, job_id)
    if existing is not None:
        return {"ok": True, "job": job_id, "manifest": existing,
                "manifest_path": str(path), "existing": True}
    manifest = {
        "schema_version": 1, "contract": CONTRACT,
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
        "citation_reasons": [], "citation_gate": None,
    }
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
        citation_gate = _citation_gate(root, completed)
        design_citable = _is_design_citable(citation_gate)
        final_status = "complete" if design_citable else ANALYSIS_COMPLETE_NOT_CITABLE
        elapsed = round(time.monotonic() - started, 3)
        attempts = list(manifest.get("attempt_history") or [])
        attempts.append({"attempt": manifest["attempts"], "started_at": started_at,
                         "finished_at": _now(), "elapsed_s": elapsed,
                         "status": final_status})
        manifest.update(
            status=final_status, stage="complete", error="",
            result_case=str(completed),
            report_path=str(Path(completed) / "body_fitted_report.html"),
            citation_status=citation_gate.get("citation_status") or "NOT_EVALUATED",
            citation_blockers=list(citation_gate.get("blockers") or []),
            citation_reasons=list(citation_gate.get("reasons") or []),
            citation_gate=citation_gate,
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
    if is_terminal_status(existing.get("status")):
        reviewed = review_terminal_job_citation(root, existing)
        return {
            "ok": True, "job": job_id, "manifest": reviewed,
            "case": Path(str(reviewed.get("result_case") or "")).name,
            "already_complete": True,
        }
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
