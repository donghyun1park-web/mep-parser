"""
cfd_studio.py — MEP CFD Studio: 대시보드 통합 단일 프로그램

CLI 4개(cfd_export/run/report/gridstudy) 체인을 브라우저 하나로 통합:
  더블클릭(run_cfd.bat) → 브라우저 → 대시보드(전 케이스 집계) → 새 해석 마법사 →
  실행 모니터 → 리포트/결과 뷰어.

설계 원칙(계획서):
- stdlib http.server 만(의존성 0), 127.0.0.1 바인딩, 자립 HTML(외부 CDN 없음).
- 파일이 진실: 프로젝트 루트(기본 cfd_projects/) 직속 폴더 중 cfd_case_meta.json 있는
  것이 케이스. 서버가 죽어도 재스캔으로 복구.
- 엔진 재사용: cfd_export.build_case/cfg_from_geometry · cfd_run.run_case ·
  cfd_report.case_summary/build (판정·지표는 CLI와 동일 코드 = 불일치 없음).

사용:
  python cfd_studio.py                 # cfd_projects/ 루트, 브라우저 자동
  python cfd_studio.py --root <경로> --port 8090 --no-browser
"""
import argparse
from datetime import datetime, timezone
import glob
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sys
import threading
import uuid
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote, parse_qs, quote

import cfd_export
import cfd_gci
import cfd_gci_job
import cfd_mesh
import cfd_physics
import cfd_occ
import cfd_report
import cfd_result_gate
import cfd_case_health
import cfd_review
import field_acceptance
import field_pipeline_job
import release_audit
import uat_acceptance
from cfd_capabilities import diagnose_freecad
from cfd_run import (
    diagnose_openfoam, record_runtime_capability, run_case,
    run_mpi_runtime_smoke, run_until_closed,
)
from geometry_v2 import migrate_geometry, validate_for_body_fitted
from heat_source_contract import HeatSourceContractError, source_reference_kind

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except (AttributeError, OSError):
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "cfd_projects")   # main()에서 확정

_SAFE_NAME = re.compile(r"^[\w가-힣.\- ]+$")
MAX_DXF_UPLOAD = 100 * 1024 * 1024


# ── 케이스 스캔 ───────────────────────────────────────────────────────────────

def safe_case_dir(name):
    """케이스 폴더명 검증 + ROOT 밖 접근 차단. 유효하면 절대경로, 아니면 None."""
    if not name or not _SAFE_NAME.match(name) or ".." in name:
        return None
    full = os.path.realpath(os.path.join(ROOT, name))
    if not full.startswith(os.path.realpath(ROOT) + os.sep):
        return None
    return full if os.path.isdir(full) else None


def _body_solver_case(name):
    """Resolve one body-fitted solver result without allowing path traversal."""
    if not name or not _SAFE_NAME.match(name) or ".." in name:
        return None
    root = Path(ROOT, "_body_solver").resolve()
    target = cfd_review.safe_project_directory(
        Path(ROOT, "_body_solver", name), projects_root=Path(ROOT)
    )
    if target is None:
        return None
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target if target.is_dir() else None


def _body_result_file(case, relative_path):
    """Resolve one manifest-declared artifact confined to its solver case."""
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("result artifact path is missing")
    path = (case / relative_path).resolve()
    try:
        path.relative_to(case)
    except ValueError as exc:
        raise ValueError("result artifact path is outside its solver case") from exc
    if not path.is_file():
        raise OSError(f"result artifact is missing: {relative_path}")
    return path


def body_result_payload(case_name):
    """Load a coordinate-based summary and its three bounded slice datasets."""
    case = _body_solver_case(case_name)
    if case is None:
        return {"ok": False, "error": "상세 결과 케이스가 없습니다."}
    try:
        manifest = json.loads((case / "result_manifest.json").read_text(encoding="utf-8"))
        run_manifest = json.loads((case / "run_manifest.json").read_text(encoding="utf-8"))
        summary_path = _body_result_file(case, manifest["summary_path"])
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        slices = {}
        for item in manifest.get("slices") or []:
            path = _body_result_file(case, item["path"])
            slices[item["axis"]] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"상세 결과를 읽지 못했습니다: {exc}"}
    evidence_path = case / "case_evidence.v1.json"
    case_health = None
    review_summary = {
        "status": "NOT_AVAILABLE",
        "reason_codes": ["CASE_EVIDENCE_NOT_FOUND"],
    }
    if evidence_path.is_file():
        try:
            with cfd_review.review_state_lock(
                evidence_path, projects_root=Path(ROOT)
            ):
                case_health = cfd_case_health.build_case_health(
                    evidence_path, projects_root=Path(ROOT)
                )
                review_summary = cfd_case_health.review_summary(
                    evidence_path, projects_root=Path(ROOT)
                )
        except Exception:
            # A broken evidence chain must not hide a readable legacy result.
            case_health = None
            review_summary = {
                "status": "INVALID",
                "reason_codes": ["CITATION_EVIDENCE_OR_REVIEW_INVALID"],
            }
    return {
        "ok": True, "case": case.name, "manifest": manifest,
        "run_manifest": run_manifest,
        "result_gate": cfd_result_gate.evaluate_body_fitted_case(case),
        "design_job": field_design_job_status(case.name),
        "summary": summary, "slices": slices,
        "case_health": case_health, "review_summary": review_summary,
    }


def scan_body_gci_cases():
    """List body-fitted thermal results and explain why any are ineligible."""
    root = Path(ROOT, "_body_solver")
    rows = []
    if root.is_dir():
        for case in sorted(root.iterdir(), key=lambda path: path.name.lower()):
            if not case.is_dir() or not (case / "result_manifest.json").is_file():
                continue
            try:
                item = cfd_gci.load_time_window_case(case)
                try:
                    cfd_gci.load_time_window_case(
                        case, minimum_flow_through_fraction=3.0
                    )
                    v3_eligible = True
                    v3_reason = ""
                except (cfd_gci.GCIInputError, OSError, ValueError) as exc:
                    v3_eligible = False
                    v3_reason = str(exc)
                rows.append({
                    "name": item["name"], "eligible": True,
                    "contract": ("grid_convergence.v3_ready" if v3_eligible
                                 else "grid_convergence.v2_ready"),
                    "v3_eligible": v3_eligible,
                    "v3_reason": v3_reason,
                    "cell_count": item["cell_count"], "time_s": item["time_s"],
                    "effective_grid_width_m": item["effective_grid_width_m"],
                    "metrics": item["metrics"], "time_window": item["time_window"],
                })
            except (cfd_gci.GCIInputError, OSError, ValueError) as exc:
                try:
                    legacy = cfd_gci.load_body_fitted_case(case)
                except (cfd_gci.GCIInputError, OSError, ValueError):
                    legacy = None
                rows.append({
                    "name": case.name, "eligible": False,
                    "v3_eligible": False,
                    "legacy_eligible": legacy is not None,
                    "reason": "v2 시간창 기준 미충족: " + str(exc),
                })
    return {"ok": True, "cases": rows}


def release_readiness_payload():
    """Rebuild the evidence-backed release gate for the current project root."""
    return release_audit.build_release_audit(ROOT)


def _file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _find_child_manifest(root, filename, expected_hash):
    try:
        candidates = Path(root).glob(f"*/{filename}")
        for path in candidates:
            try:
                if _file_sha256(path) == expected_hash:
                    return path
            except OSError:
                continue
    except OSError:
        pass
    return None


def _field_case_chain(case_name):
    """Resolve a completed thermal result back to its immutable DXF chain."""
    solver = _body_solver_case(case_name)
    if solver is None:
        return {"ok": False, "error": "프로젝트의 상세 열해석 결과를 선택하세요."}
    try:
        run = json.loads((solver / "run_manifest.json").read_text(encoding="utf-8"))
        if (run.get("engine") != "body_fitted_buoyant_urans"
                or run.get("status") != "PASS"
                or run.get("design_ready") is not True
                or not (solver / "result_manifest.json").is_file()):
            return {"ok": False, "error": "PASS 상태로 완료된 상세 열해석 결과가 아닙니다."}
        result_gate = cfd_result_gate.evaluate_body_fitted_case(
            solver, gci_root=Path(ROOT, "_body_gci")
        )
        if not (
            result_gate.get("status") == "PASS"
            and result_gate.get("design_ready") is True
            and result_gate.get("citation_status") == "DESIGN_CITABLE"
            and result_gate.get("citable") is True
        ):
            return {
                "ok": False,
                "error": "설계 검토 인용 가능 상태가 아닙니다. 수치 품질·결과 artifact·GCI 증거를 확인하세요.",
                "result_gate": result_gate,
            }
        mesh_hash = _file_sha256(solver / "mesh_manifest.json")
        mesh_manifest_path = _find_child_manifest(
            Path(ROOT, "_body_mesh"), "mesh_manifest.json", mesh_hash
        )
        if mesh_manifest_path is None:
            raise LookupError("mesh manifest")
        mesh_case = mesh_manifest_path.parent
        surface_hash = _file_sha256(mesh_case / "surface_manifest.json")
        surface_manifest_path = _find_child_manifest(
            Path(ROOT, "_occ_geometry"), "surface_manifest.json", surface_hash
        )
        if surface_manifest_path is None:
            raise LookupError("surface manifest")
        surface_dir = surface_manifest_path.parent
        surface = json.loads((surface_dir / "surface_manifest.json").read_text(encoding="utf-8"))
        geometry = Path(str((surface.get("source") or {}).get("geometry_path") or ""))
        if not geometry.is_absolute():
            geometry = (surface_dir / geometry).resolve()
        geometry.resolve().relative_to(Path(ROOT).resolve())
        geometry_row = json.loads(geometry.read_text(encoding="utf-8"))
        source_value = geometry_row.get("source")
        if isinstance(source_value, dict):
            source_value = (source_value.get("path") or source_value.get("file")
                            or source_value.get("name") or "")
        source = Path(str(source_value or ""))
        if not source.is_absolute():
            source = (geometry.parent / source).resolve()
        source.resolve().relative_to(Path(ROOT).resolve())
        if not source.is_file() or source.suffix.lower() != ".dxf":
            raise ValueError("source DXF missing")
    except LookupError:
        return {"ok": False, "error": "메시 또는 OCC 원본 체인을 찾지 못했습니다."}
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"원본 DXF 체인을 확인하지 못했습니다: {exc}"}
    return {
        "ok": True, "solver_case": str(solver), "mesh_case": str(mesh_case),
        "surface_dir": str(surface_dir), "geometry": str(geometry.resolve()),
        "source_dxf": str(source.resolve()),
    }


def field_evidence_candidates():
    """List completed field-result candidates for a one-click evidence check."""
    rows = []
    registered_hashes = set()
    evidence_root = Path(ROOT, "_release_evidence", "field_dxf")
    for evidence_path in (sorted(evidence_root.glob("*.json"))
                          if evidence_root.is_dir() else []):
        verification = field_acceptance.validate_evidence(evidence_path, ROOT)
        if verification.get("ok"):
            source_hash = str(
                (verification.get("manifest") or {}).get("source_sha256") or ""
            ).lower()
            if source_hash:
                registered_hashes.add(source_hash)
    root = Path(ROOT, "_body_solver")
    if root.is_dir():
        for case in sorted(root.iterdir(), key=lambda path: path.name.lower()):
            if not case.is_dir():
                continue
            chain = _field_case_chain(case.name)
            if chain.get("ok"):
                source_name = Path(chain["source_dxf"]).name
                source_hash = _file_sha256(chain["source_dxf"]).lower()
                is_sample = field_acceptance.is_bundled_sample_drawing(
                    chain["source_dxf"], ROOT
                )
                already_registered = source_hash in registered_hashes
                row = {"case": case.name,
                       "eligible": not is_sample and not already_registered,
                       "source": source_name}
                if is_sample:
                    row["reason"] = "프로그램에 포함된 샘플은 실제 현장 증거로 등록할 수 없습니다."
                elif already_registered:
                    row["reason"] = "이미 검증 등록된 현장 도면입니다."
                rows.append(row)
    return {"ok": True, "cases": rows}


def record_field_evidence(case_name, actual_site_drawing=False):
    """Verify and register a real drawing without asking users for file paths."""
    if actual_site_drawing is not True:
        return {"ok": False, "error": "실제 현장 도면임을 확인해야 등록할 수 있습니다."}
    chain = _field_case_chain(case_name)
    if not chain.get("ok"):
        return chain
    return field_acceptance.build_field_acceptance(
        chain["source_dxf"], chain["geometry"], chain["surface_dir"],
        chain["mesh_case"], chain["solver_case"], ROOT,
        actual_site_drawing=True,
    )


def _uat_draft_path(token):
    if not re.fullmatch(r"[0-9a-f]{32}", str(token or "")):
        return None
    root = Path(ROOT, "_release_evidence", "uat_drafts").resolve()
    return root / f"{token}.json"


def _write_uat_draft(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def uat_field_evidence_candidates():
    rows = []
    root = Path(ROOT, "_release_evidence", "field_dxf")
    for path in sorted(root.glob("*.json")) if root.is_dir() else []:
        verified = field_acceptance.validate_evidence(path, ROOT)
        if not verified.get("ok"):
            continue
        source = Path(str((verified["manifest"].get("source_dxf_path") or ""))).name
        rows.append({"id": path.name, "source": source})
    return {"ok": True, "cases": rows}


def _completed_uat_participant_ids():
    ids = set()
    root = Path(ROOT, "_release_evidence", "uat")
    for path in sorted(root.glob("*.json")) if root.is_dir() else []:
        verification = uat_acceptance.validate_evidence(path, ROOT)
        if not verification.get("ok"):
            continue
        participant_id = str(
            (verification.get("manifest") or {}).get("participant_id") or ""
        ).strip().casefold()
        if participant_id:
            ids.add(participant_id)
    return ids


def _active_uat_draft(participant_id):
    """Return the newest readable draft for a participant, if one exists."""
    wanted = str(participant_id or "").strip().casefold()
    root = Path(ROOT, "_release_evidence", "uat_drafts")
    matches = []
    for path in root.glob("*.json") if root.is_dir() else []:
        try:
            draft = json.loads(path.read_text(encoding="utf-8"))
            if (str(draft.get("participant_id") or "").strip().casefold()
                    != wanted):
                continue
            if _uat_draft_path(draft.get("token")) != path.resolve():
                continue
            matches.append((str(draft.get("started_at") or ""), path, draft))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return max(matches, default=None, key=lambda row: row[0])


def _start_uat_session_unlocked(participant_id, observed_by, field_evidence_id):
    participant_id = str(participant_id or "").strip()
    observed_by = str(observed_by or "").strip()
    if not participant_id or not observed_by:
        return {"ok": False, "error": "참가자 코드와 관찰자 이름을 입력하세요."}
    if participant_id.casefold() == observed_by.casefold():
        return {"ok": False, "error": "참가자와 다른 관찰자가 기록해야 합니다."}
    if participant_id.casefold() in _completed_uat_participant_ids():
        return {
            "ok": False,
            "error": "이미 완료된 참가자 코드입니다. 다른 담당자의 고유 코드를 입력하세요.",
        }
    field_root = Path(ROOT, "_release_evidence", "field_dxf").resolve()
    field_path = (field_root / Path(str(field_evidence_id or "")).name).resolve()
    try:
        field_path.relative_to(field_root)
    except ValueError:
        return {"ok": False, "error": "현장 도면 증거가 올바르지 않습니다."}
    if not field_acceptance.validate_evidence(field_path, ROOT).get("ok"):
        return {"ok": False, "error": "유효한 현장 도면 검증을 먼저 등록하세요."}
    active = _active_uat_draft(participant_id)
    if active:
        _, active_path, draft = active
        same_observer = (
            str(draft.get("observed_by") or "").strip().casefold()
            == observed_by.casefold()
        )
        same_field = Path(str(draft.get("field_evidence_path") or "")).resolve() == field_path
        if not same_observer or not same_field:
            return {
                "ok": False,
                "error": "이 참가자의 진행 중 시험이 있습니다. 기존 관찰자와 도면으로 이어서 진행하거나 먼저 취소하세요.",
            }
        resumed = uat_session_status(draft.get("token"))
        if resumed.get("ok"):
            return {**resumed, "resumed": True, "token": active_path.stem}
    token = uuid.uuid4().hex
    now = _utc_now()
    draft = {
        "contract": "mechanical_uat_draft.v1", "token": token,
        "participant_id": participant_id, "observed_by": observed_by,
        "field_evidence_path": str(field_path), "started_at": now,
        "task_started_at": now, "task_index": 0, "tasks": [],
    }
    _write_uat_draft(_uat_draft_path(token), draft)
    return {"ok": True, "token": token, "task": uat_acceptance.TASKS[0],
            "task_index": 0, "task_count": len(uat_acceptance.TASKS)}


def start_uat_session(participant_id, observed_by, field_evidence_id):
    """Start or recover one participant draft without creating duplicates."""
    with UAT_LOCK:
        return _start_uat_session_unlocked(
            participant_id, observed_by, field_evidence_id
        )


def uat_session_status(token):
    path = _uat_draft_path(token)
    if path is None or not path.is_file():
        return {"ok": False, "error": "진행 중인 사용자 시험을 찾지 못했습니다."}
    try:
        draft = json.loads(path.read_text(encoding="utf-8"))
        index = int(draft.get("task_index") or 0)
        done = index >= len(uat_acceptance.TASKS)
        return {
            "ok": True, "token": str(token), "done": done,
            "task_index": index, "task_count": len(uat_acceptance.TASKS),
            "task": None if done else uat_acceptance.TASKS[index],
            "participant_id": draft.get("participant_id"),
            "observed_by": draft.get("observed_by"),
        }
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"사용자 시험 기록을 읽지 못했습니다: {exc}"}


def _cancel_uat_session_unlocked(token):
    """Delete only an unfinished UAT draft so a mistaken session can restart."""
    path = _uat_draft_path(token)
    if path is None or not path.is_file():
        return {"ok": False, "error": "진행 중인 사용자 시험을 찾지 못했습니다."}
    try:
        path.unlink()
        return {"ok": True}
    except OSError as exc:
        return {"ok": False, "error": f"사용자 시험을 취소하지 못했습니다: {exc}"}


def cancel_uat_session(token):
    """Serialise cancellation with task writes and final evidence creation."""
    with UAT_LOCK:
        return _cancel_uat_session_unlocked(token)


def _record_uat_task_unlocked(token, status, assistance_count=0, notes=""):
    path = _uat_draft_path(token)
    if path is None or not path.is_file():
        return {"ok": False, "error": "진행 중인 사용자 시험을 찾지 못했습니다."}
    try:
        draft = json.loads(path.read_text(encoding="utf-8"))
        index = int(draft.get("task_index") or 0)
        status = str(status or "").upper()
        assistance_count = int(assistance_count or 0)
        if index >= len(uat_acceptance.TASKS):
            return {"ok": False, "error": "모든 작업 기록이 끝났습니다."}
        if status not in ("PASS", "FAIL") or assistance_count < 0:
            return {"ok": False, "error": "작업 결과 또는 도움 횟수가 올바르지 않습니다."}
        now = _utc_now()
        draft["tasks"].append({
            "id": uat_acceptance.TASKS[index], "status": status,
            "started_at": draft["task_started_at"], "completed_at": now,
            "assistance_count": assistance_count,
            "notes": str(notes or "").strip(),
        })
        draft["task_index"] = index + 1
        draft["task_started_at"] = now
        _write_uat_draft(path, draft)
        done = draft["task_index"] >= len(uat_acceptance.TASKS)
        return {
            "ok": True, "done": done, "task_index": draft["task_index"],
            "task_count": len(uat_acceptance.TASKS),
            "task": None if done else uat_acceptance.TASKS[draft["task_index"]],
        }
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"사용자 시험 기록을 저장하지 못했습니다: {exc}"}


def record_uat_task(token, status, assistance_count=0, notes="",
                    expected_task=""):
    """Serialise task writes and reject a repeated click for an old task."""
    with UAT_LOCK:
        path = _uat_draft_path(token)
        if path is None or not path.is_file():
            return {"ok": False, "error": "진행 중인 사용자 시험을 찾지 못했습니다."}
        try:
            draft = json.loads(path.read_text(encoding="utf-8"))
            index = int(draft.get("task_index") or 0)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return {"ok": False, "error": f"사용자 시험 기록을 읽지 못했습니다: {exc}"}
        current_task = (
            uat_acceptance.TASKS[index]
            if 0 <= index < len(uat_acceptance.TASKS) else ""
        )
        if expected_task and str(expected_task) != current_task:
            return {
                "ok": False,
                "error": "이미 저장된 작업입니다. 현재 작업 화면을 다시 확인하세요.",
                "task": current_task or None,
                "task_index": index,
            }
        return _record_uat_task_unlocked(
            token, status, assistance_count, notes
        )


def _finish_uat_session_unlocked(token, critical_incidents=None):
    path = _uat_draft_path(token)
    if path is None or not path.is_file():
        return {"ok": False, "error": "진행 중인 사용자 시험을 찾지 못했습니다."}
    try:
        draft = json.loads(path.read_text(encoding="utf-8"))
        if int(draft.get("task_index") or 0) != len(uat_acceptance.TASKS):
            return {"ok": False, "error": "필수 작업 6개를 먼저 모두 기록하세요."}
        result = uat_acceptance.build_uat_session(
            draft.get("participant_id"), draft.get("observed_by"),
            draft.get("started_at"), _utc_now(), draft.get("tasks"),
            critical_incidents or [], draft.get("field_evidence_path"), ROOT,
        )
        if result.get("ok"):
            path.unlink(missing_ok=True)
        return result
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"사용자 시험을 완료하지 못했습니다: {exc}"}


def finish_uat_session(token, critical_incidents=None):
    """Create at most one final record while excluding task/cancel races."""
    with UAT_LOCK:
        return _finish_uat_session_unlocked(token, critical_incidents)


def build_body_fitted_gci(case_names):
    """Create a project-local thermal mesh-uncertainty manifest and report."""
    if not isinstance(case_names, list) or len(case_names) not in (3, 4):
        return {"ok": False, "error": "비교할 열·부력 결과를 3개 또는 4개 선택해 주세요."}
    cases = [_body_solver_case(str(name)) for name in case_names]
    if any(case is None for case in cases):
        return {"ok": False, "error": "프로젝트 안의 열·부력 결과만 비교할 수 있습니다."}
    if len({str(case) for case in cases}) != len(cases):
        return {"ok": False, "error": "서로 다른 결과를 선택해 주세요."}
    token = hashlib.sha256(
        "\n".join(sorted(case.name for case in cases)).encode("utf-8")
    ).hexdigest()[:12]
    study = Path(ROOT, "_body_gci", f"gci-{token}")
    result = cfd_gci.build_grid_convergence(
        cases, study / "grid_convergence.json",
        contract=("grid_convergence.v3" if len(cases) == 4
                  else "grid_convergence.v2"),
    )
    if not result.get("ok"):
        return result
    report = cfd_report.generate_gci_report(study)
    if not report.get("ok"):
        return {"ok": False, "error": report.get("error") or "GCI 보고서 생성 실패"}
    result.update({
        "study": study.name,
        "report_url": "/body-gci-report/" + quote(study.name),
        "report": report,
    })
    return result


def scan_cases():
    """루트 직속 케이스 폴더 → case_summary 목록(최신순)."""
    cases = []
    if os.path.isdir(ROOT):
        for d in sorted(os.listdir(ROOT)):
            full = os.path.join(ROOT, d)
            if not os.path.isdir(full):
                continue
            if not os.path.exists(os.path.join(full, "cfd_case_meta.json")):
                continue
            try:
                s = cfd_report.case_summary(full)
            except Exception as e:
                s = {"dir": d, "name": d, "badge": f"요약 실패: {e}", "badge_color": "#c0392b",
                     "status": "error", "mtime": 0}
            if s:
                s["gci_pct"] = (s.get("gci") or {}).get("gci_pct")
                cases.append(s)
    cases.sort(key=lambda c: c.get("mtime") or 0, reverse=True)
    return {"root": ROOT, "cases": cases}


# ── 실행 큐 (동시 1개 — WSL 경합·결정론 보장) ────────────────────────────────

RUN = {"active": None, "queue": [], "history": {}, "worker": False}
RUN_LOCK = threading.Lock()
UAT_LOCK = threading.Lock()
OPENFOAM_OK = None   # main()에서 1회 체크(1초 status 폴링마다 wsl 프로세스를 띄우지 않음)
OPENFOAM_CAPABILITIES = {}
FREECAD_CAPABILITIES = {}
ENVIRONMENT_ACCEPTANCE = {}
ENVIRONMENT_LOCK = threading.Lock()
ACCEPTANCE_JOB = "__environment_acceptance__"
ACCEPTANCE_DISPLAY_NAME = "환경 수용 테스트"
MPI_SMOKE_JOB = "__mpi_runtime_smoke__"
MPI_SMOKE_DISPLAY_NAME = "안전 제한 MPI 병렬 재점검"
FIELD_DESIGN_FLOW_FRACTION = 3.0
FIELD_DESIGN_MAX_RUNS = 500


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _manifest_path():
    return os.path.join(ROOT, "capability_manifest.json")


def _runtime_capability_path():
    return os.path.join(ROOT, "_release_evidence", "runtime_capability.v1.json")


_MPI_IDENTITY_FIELDS = (
    "distro", "kernel", "mpirun_path", "mpirun_version",
    "ompi_info_version", "effective_cpu_count",
)
_MPI_REQUIRED_COMMANDS = ("mpirun", "decomposePar", "reconstructPar")


def _normalise_mpi_identity(value):
    try:
        raw = dict(value or {})
    except (TypeError, ValueError):
        return {}
    try:
        cpu = int(raw.get("effective_cpu_count"))
    except (TypeError, ValueError):
        cpu = None
    return {
        "distro": str(raw.get("distro") or ""),
        "kernel": str(raw.get("kernel") or ""),
        "mpirun_path": str(raw.get("mpirun_path") or ""),
        "mpirun_version": str(raw.get("mpirun_version") or ""),
        "ompi_info_version": str(raw.get("ompi_info_version") or ""),
        "effective_cpu_count": cpu if cpu and cpu > 0 else None,
    }


def _mpi_identity_complete(identity):
    return bool(identity) and all(
        identity.get(field) not in (None, "") for field in _MPI_IDENTITY_FIELDS
    )


def _resolve_mpi_smoke_artifact(raw_path):
    """Resolve only a project-local smoke artifact, never an arbitrary path."""
    text = str(raw_path or "").strip()
    if not text:
        return None
    evidence_root = (Path(ROOT) / "_release_evidence").resolve()
    supplied = Path(text).expanduser()
    candidates = [supplied] if supplied.is_absolute() else [
        Path.cwd() / supplied,
        Path(ROOT) / supplied,
        evidence_root / supplied.name,
    ]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            resolved.relative_to(evidence_root)
        except (OSError, ValueError):
            continue
        if resolved.is_file():
            return resolved
    return None


def _current_mpi_identity(openfoam):
    payload = dict(openfoam or {})
    commands = dict(payload.get("commands") or {})
    return {
        "distro": str(payload.get("distro") or ""),
        "kernel": str(payload.get("kernel") or ""),
        "mpirun_path": str(commands.get("mpirun") or ""),
        "mpirun_version": str(payload.get("mpi_version") or ""),
        "ompi_info_version": str(payload.get("ompi_info_version") or ""),
        "effective_cpu_count": payload.get("effective_cpu_count"),
    }


def _mpi_identity_mismatches(expected, actual):
    expected = _normalise_mpi_identity(expected)
    actual = _normalise_mpi_identity(actual)
    if not _mpi_identity_complete(expected) or not _mpi_identity_complete(actual):
        return list(_MPI_IDENTITY_FIELDS)
    return [field for field in _MPI_IDENTITY_FIELDS
            if expected[field] != actual[field]]


def _not_run_mpi_evidence(evidence, reason_code):
    result = dict(evidence or {})
    result.update(
        status="NOT_RUN", reason_code=reason_code,
        parallel_runtime_ready=False,
    )
    return result


def _load_mpi_runtime_capability():
    """Load the separate MPI execution proof without trusting static probes."""
    path = _runtime_capability_path()
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, TypeError):
        return None
    if payload.get("contract") != "runtime_capability.v1":
        return None
    mpi = dict(payload.get("mpi") or {})
    status = str(mpi.get("execution_smoke") or "NOT_RUN").upper()
    if status not in ("NOT_RUN", "PASS", "BLOCKED"):
        status = "NOT_RUN"
    evidence = {
        "status": status,
        "recorded_status": status,
        "reason_code": str(mpi.get("reason_code") or ""),
        "artifact_path": str(mpi.get("artifact_path") or ""),
        "artifact_sha256": str(mpi.get("artifact_sha256") or ""),
        "identity": _normalise_mpi_identity(mpi.get("smoke_identity")),
        "mpi_tools": dict(mpi.get("tools") or {}),
        "mpi_version": str(mpi.get("version") or ""),
        "cpu_count": (dict(payload.get("cpu") or {}).get("effective_logical_count")),
        "distro": str((dict(payload.get("openfoam") or {})).get("distro") or ""),
        "parallel_runtime_ready": bool(payload.get("parallel_runtime_ready")),
        "path": path,
    }
    if status != "PASS":
        return evidence
    artifact = _resolve_mpi_smoke_artifact(evidence["artifact_path"])
    if artifact is None:
        return _not_run_mpi_evidence(evidence, "MPI_SMOKE_ARTIFACT_MISSING")
    expected_hash = str(evidence["artifact_sha256"] or "").lower()
    try:
        actual_hash = _file_sha256(artifact)
        smoke = json.loads(artifact.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return _not_run_mpi_evidence(evidence, "MPI_SMOKE_ARTIFACT_INVALID")
    if len(expected_hash) != 64 or actual_hash != expected_hash:
        return _not_run_mpi_evidence(evidence, "MPI_SMOKE_ARTIFACT_HASH_MISMATCH")
    if (smoke.get("contract") != "mpi_runtime_smoke.v1"
            or str(smoke.get("status") or "").upper() != "PASS"):
        return _not_run_mpi_evidence(evidence, "MPI_SMOKE_ARTIFACT_CONTRACT_INVALID")
    if list(smoke.get("requested_ranks") or []) != [2, 4]:
        return _not_run_mpi_evidence(evidence, "MPI_SMOKE_RANK_SCOPE_INVALID")
    if dict(smoke.get("environment_overrides") or {}):
        return _not_run_mpi_evidence(evidence, "MPI_SMOKE_OVERRIDE_NOT_BASELINE")
    artifact_identity = _normalise_mpi_identity(smoke.get("identity"))
    if not _mpi_identity_complete(artifact_identity):
        return _not_run_mpi_evidence(evidence, "MPI_SMOKE_IDENTITY_UNAVAILABLE")
    if not _mpi_identity_complete(evidence["identity"]):
        return _not_run_mpi_evidence(evidence, "MPI_SMOKE_IDENTITY_UNAVAILABLE")
    if _mpi_identity_mismatches(evidence["identity"], artifact_identity):
        return _not_run_mpi_evidence(evidence, "MPI_SMOKE_ARTIFACT_IDENTITY_MISMATCH")
    return evidence


def _apply_mpi_runtime_capability(openfoam):
    """Make the GUI fail closed until an actual rank-spawn smoke passes."""
    merged = dict(openfoam or {})
    evidence = _load_mpi_runtime_capability()
    if evidence is None:
        evidence = {
            "status": "NOT_RUN", "reason_code": "MPI_SMOKE_NOT_RUN",
            "artifact_path": "", "artifact_sha256": "", "path": "",
            "parallel_runtime_ready": False,
        }
    if evidence["status"] == "PASS":
        mismatches = _mpi_identity_mismatches(
            evidence.get("identity"), _current_mpi_identity(merged)
        )
        if mismatches:
            evidence = _not_run_mpi_evidence(
                evidence, "MPI_SMOKE_RUNTIME_MISMATCH:" + ",".join(mismatches)
            )
        expected_tools = dict(evidence.get("mpi_tools") or {})
        current_tools = dict(merged.get("commands") or {})
        changed_tools = [name for name in _MPI_REQUIRED_COMMANDS
                         if not expected_tools.get(name)
                         or expected_tools.get(name) != current_tools.get(name)]
        if evidence["status"] == "PASS" and changed_tools:
            evidence = _not_run_mpi_evidence(
                evidence, "MPI_SMOKE_STATIC_ENV_MISMATCH:" + ",".join(changed_tools)
            )
        if (evidence["status"] == "PASS"
                and (str(evidence.get("mpi_version") or "")
                     != str(merged.get("mpi_version") or ""))):
            evidence = _not_run_mpi_evidence(
                evidence, "MPI_SMOKE_STATIC_ENV_MISMATCH:mpi_version"
            )
        if (evidence["status"] == "PASS"
                and (str(evidence.get("distro") or "")
                     != str(merged.get("distro") or ""))):
            evidence = _not_run_mpi_evidence(
                evidence, "MPI_SMOKE_STATIC_ENV_MISMATCH:distro"
            )
    # `parallel_ready` used to mean only command discovery + CPU count.  Never
    # surface that as usable MPI once a runtime evidence contract exists.
    parallel_ready = bool(
        merged.get("ok") and merged.get("parallel_ready")
        and evidence["status"] == "PASS" and evidence["parallel_runtime_ready"]
    )
    merged["parallel_ready"] = parallel_ready
    merged["parallel_runtime_ready"] = parallel_ready
    merged["mpi_execution_smoke"] = evidence["status"]
    merged["mpi_runtime_reason_code"] = evidence["reason_code"]
    merged["mpi_runtime_evidence"] = evidence
    return merged


def _load_saved_acceptance():
    try:
        with open(_manifest_path(), encoding="utf-8") as f:
            saved = json.load(f)
        value = saved.get("acceptance")
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _publish_environment_manifest(openfoam, freecad, acceptance):
    """Atomically persist environment facts. Caller holds ENVIRONMENT_LOCK."""
    manifest_path = _manifest_path()
    tmp_path = manifest_path + f".{uuid.uuid4().hex}.tmp"
    body_runtime_ready = bool(
        openfoam.get("body_fitted_ready") and freecad.get("ok")
    )
    body_engine_ready = bool(
        openfoam.get("thermal_detailed_ready") and freecad.get("ok")
    )
    payload = {
        "schema_version": 2,
        "application": "MEP CFD Studio",
        "engine": "screening_voxel+body_fitted_thermal",
        "body_fitted_runtime_ready": body_runtime_ready,
        "body_fitted_engine_ready": body_engine_ready,
        "openfoam": openfoam,
        "freecad": freecad,
        "acceptance": acceptance,
    }
    try:
        os.makedirs(ROOT, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_path, manifest_path)
        return manifest_path, ""
    except OSError as exc:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        return "", f"환경 진단 기록 저장 실패: {exc}"


def refresh_environment_capabilities():
    """Probe OpenFOAM and FreeCAD, then publish one project-local manifest."""
    global OPENFOAM_OK, OPENFOAM_CAPABILITIES, FREECAD_CAPABILITIES
    global ENVIRONMENT_ACCEPTANCE
    with ENVIRONMENT_LOCK:
        openfoam = _apply_mpi_runtime_capability(diagnose_openfoam())
        freecad = diagnose_freecad()
        if not ENVIRONMENT_ACCEPTANCE:
            ENVIRONMENT_ACCEPTANCE = _load_saved_acceptance()
        accepted_profile = str(
            ENVIRONMENT_ACCEPTANCE.get("openfoam_profile") or ""
        )
        current_profile = str(openfoam.get("compatible_profile") or "")
        if (ENVIRONMENT_ACCEPTANCE.get("ok") is True
                and accepted_profile != current_profile):
            ENVIRONMENT_ACCEPTANCE = dict(
                ENVIRONMENT_ACCEPTANCE,
                status="stale",
                ok=False,
                error=("OpenFOAM 실행 프로필이 바뀌었습니다. 현재 환경에서 실제 "
                       "계산 테스트를 다시 실행해 주세요."),
                previous_openfoam_profile=accepted_profile,
            )
        manifest_path, manifest_error = _publish_environment_manifest(
            openfoam, freecad, ENVIRONMENT_ACCEPTANCE
        )
        if manifest_path:
            openfoam = dict(openfoam, manifest_path=manifest_path)
            freecad = dict(freecad, manifest_path=manifest_path)
        else:
            openfoam = dict(openfoam, manifest_path="",
                            manifest_error=manifest_error)
            freecad = dict(freecad, manifest_path="",
                           manifest_error=manifest_error)
        OPENFOAM_CAPABILITIES = openfoam
        FREECAD_CAPABILITIES = freecad
        OPENFOAM_OK = bool(openfoam.get("ok"))
        return {"openfoam": dict(openfoam), "freecad": dict(freecad)}


def refresh_openfoam_capabilities():
    """Backward-compatible wrapper returning only the OpenFOAM section."""
    return refresh_environment_capabilities()["openfoam"]


def refresh_openfoam_runtime_evidence():
    """Refresh MPI proof without waiting for an unrelated FreeCAD probe."""
    global OPENFOAM_OK, OPENFOAM_CAPABILITIES
    with ENVIRONMENT_LOCK:
        openfoam = _apply_mpi_runtime_capability(diagnose_openfoam())
        freecad = dict(FREECAD_CAPABILITIES)
        manifest_path, manifest_error = _publish_environment_manifest(
            openfoam, freecad, ENVIRONMENT_ACCEPTANCE
        )
        if manifest_path:
            openfoam = dict(openfoam, manifest_path=manifest_path)
        else:
            openfoam = dict(openfoam, manifest_path="",
                            manifest_error=manifest_error)
        OPENFOAM_CAPABILITIES = openfoam
        OPENFOAM_OK = bool(openfoam.get("ok"))
        return dict(openfoam)


def _set_acceptance_state(**values):
    global ENVIRONMENT_ACCEPTANCE
    with ENVIRONMENT_LOCK:
        ENVIRONMENT_ACCEPTANCE = dict(ENVIRONMENT_ACCEPTANCE, **values)
        _publish_environment_manifest(
            OPENFOAM_CAPABILITIES, FREECAD_CAPABILITIES, ENVIRONMENT_ACCEPTANCE
        )
        return dict(ENVIRONMENT_ACCEPTANCE)


def _solver_busy_payload(owner):
    owner = dict(owner or {})
    pid = owner.get("pid", "unknown")
    return {
        "ok": False,
        "code": "CFD_SOLVER_BUSY",
        "error": ("다른 CFD 작업이 OpenFOAM을 사용 중입니다. 완료 후 다시 실행하세요. "
                  f"PID {pid}"),
        "lock": owner,
    }


def _claim_solver_slot():
    """Claim the project-wide OpenFOAM slot without exposing lock internals."""
    token, owner = cfd_gci_job.acquire_solver_lock(ROOT)
    return (token, None) if token is not None else (None, _solver_busy_payload(owner))


def _enqueue(name, kind):
    """실행/격자검증 작업 예약. 문제 있으면 오류 문자열, 정상이면 None."""
    if not OPENFOAM_OK:
        return ("WSL OpenFOAM 이 없습니다 — WSL 에서 `sudo apt-get install openfoam` "
                "설치 후 스튜디오를 재시작하세요.")
    if not safe_case_dir(name):
        return "케이스 없음"
    with RUN_LOCK:
        if RUN["active"] and RUN["active"]["name"] == name:
            return "이미 실행 중"
        if any(q["name"] == name for q in RUN["queue"]):
            return "이미 대기열에 있음"
        RUN["queue"].append({"name": name, "kind": kind})
        RUN["history"].pop(name, None)
        if not RUN["worker"]:
            RUN["worker"] = True
            threading.Thread(target=_run_worker, daemon=True).start()
    return None


def enqueue_run(name):
    return _enqueue(name, "run")


def enqueue_grid(name):
    return _enqueue(name, "grid")


def enqueue_environment_acceptance():
    """Queue one isolated end-to-end CFD acceptance run."""
    if not OPENFOAM_OK:
        summary = OPENFOAM_CAPABILITIES.get("summary") or "OpenFOAM 계산 환경이 준비되지 않았습니다."
        fix = OPENFOAM_CAPABILITIES.get("fix") or "먼저 환경 다시 검사를 실행하세요."
        return f"{summary} {fix}"
    start_worker = False
    with RUN_LOCK:
        if RUN["active"] and RUN["active"].get("kind") == "acceptance":
            return "환경 수용 테스트가 이미 실행 중입니다."
        if any(q.get("kind") == "acceptance" for q in RUN["queue"]):
            return "환경 수용 테스트가 이미 대기 중입니다."
        RUN["queue"].append({"name": ACCEPTANCE_JOB, "kind": "acceptance"})
        RUN["history"].pop(ACCEPTANCE_JOB, None)
        if not RUN["worker"]:
            RUN["worker"] = True
            start_worker = True
    _set_acceptance_state(status="queued", ok=None, error="",
                          queued_at=_utc_now(), finished_at="")
    if start_worker:
        threading.Thread(target=_run_worker, daemon=True).start()
    return None


def enqueue_mpi_runtime_smoke():
    """Queue a fixed, safe MPI rank-spawn recheck without user shell input."""
    if not OPENFOAM_OK:
        summary = OPENFOAM_CAPABILITIES.get("summary") or "OpenFOAM 계산 환경이 준비되지 않았습니다."
        fix = OPENFOAM_CAPABILITIES.get("fix") or "먼저 환경 다시 검사를 실행하세요."
        return f"{summary} {fix}"
    owner = cfd_gci_job.active_solver_lock(ROOT)
    if owner is not None:
        return ("다른 CFD 작업이 OpenFOAM을 사용 중입니다. 완료 후 MPI 병렬 재점검을 실행하세요. "
                f"PID {owner.get('pid', 'unknown')}")
    start_worker = False
    with RUN_LOCK:
        if RUN["active"] and RUN["active"].get("kind") == "mpi_smoke":
            return "MPI 병렬 재점검이 이미 실행 중입니다."
        if any(row.get("kind") == "mpi_smoke" for row in RUN["queue"]):
            return "MPI 병렬 재점검이 이미 대기 중입니다."
        RUN["queue"].append({"name": MPI_SMOKE_JOB, "kind": "mpi_smoke"})
        RUN["history"].pop(MPI_SMOKE_JOB, None)
        if not RUN["worker"]:
            RUN["worker"] = True
            start_worker = True
    if start_worker:
        threading.Thread(target=_run_worker, daemon=True).start()
    return None


def _queue_body_gci_study(study_id):
    """Queue one persistent GCI study on the shared OpenFOAM worker."""
    owner = cfd_gci_job.active_run_lock(ROOT, study_id)
    if owner is not None:
        return f"격자 독립성 작업이 이미 실행 중입니다. PID {owner.get('pid', 'unknown')}"
    start_worker = False
    with RUN_LOCK:
        if (RUN["active"] and RUN["active"].get("job_name") == study_id):
            return "이 메시 독립성 작업은 이미 실행 중입니다."
        if any(row.get("name") == study_id for row in RUN["queue"]):
            return "이 메시 독립성 작업은 이미 대기 중입니다."
        RUN["queue"].append({"name": study_id, "kind": "body_gci"})
        RUN["history"].pop(study_id, None)
        if not RUN["worker"]:
            RUN["worker"] = True
            start_worker = True
    if start_worker:
        threading.Thread(target=_run_worker, daemon=True).start()
    return None


def _queue_field_pipeline_job(job_id):
    """Queue one persistent field pipeline on the shared OpenFOAM worker."""
    owner = field_pipeline_job.active_run_lock(ROOT, job_id)
    if owner is not None:
        return f"현장 자동 해석이 이미 실행 중입니다. PID {owner.get('pid', 'unknown')}"
    start_worker = False
    with RUN_LOCK:
        if (RUN["active"] and RUN["active"].get("job_name") == job_id):
            return "같은 현장 자동 해석이 이미 실행 중입니다."
        if any(row.get("name") == job_id for row in RUN["queue"]):
            return "같은 현장 자동 해석이 이미 대기 중입니다."
        RUN["queue"].append({"name": job_id, "kind": "field_pipeline"})
        RUN["history"].pop(job_id, None)
        if not RUN["worker"]:
            RUN["worker"] = True
            start_worker = True
    if start_worker:
        threading.Thread(target=_run_worker, daemon=True).start()
    return None


def _review_body_gci_geometry_data(migrated):
    issues = list(validate_for_body_fitted(migrated))
    elements = migrated.get("elements") or {}
    zones = [
        item for item in elements.get("zone") or []
        if item.get("closed") and item.get("confirmed")
    ]
    equipment = elements.get("equipment") or []
    supplies = [
        item for item in equipment
        if (item.get("semantic") or {}).get("kind") == "air_terminal"
        and (item.get("semantic") or {}).get("role") == "supply"
    ]
    exhausts = [
        item for item in equipment
        if (item.get("semantic") or {}).get("kind") == "air_terminal"
        and (item.get("semantic") or {}).get("role") == "exhaust"
    ]
    heat_sources = [
        item for item in equipment
        if (item.get("semantic") or {}).get("role") == "heat_source"
    ]
    if len(zones) > 1:
        issues.append({
            "code": "SINGLE_ZONE_REQUIRED", "severity": "error",
            "message": "Select exactly one confirmed room for the GCI study.",
        })
    if not supplies:
        issues.append({
            "code": "SUPPLY_MISSING", "severity": "error",
            "message": "At least one supply terminal is required for thermal GCI.",
        })
    if not exhausts:
        issues.append({
            "code": "EXHAUST_MISSING", "severity": "error",
            "message": "At least one exhaust terminal is required for thermal GCI.",
        })
    if not heat_sources:
        issues.append({
            "code": "HEAT_SOURCE_MISSING", "severity": "error",
            "message": "At least one heat source is required for thermal GCI.",
        })
    try:
        supply_cmh = sum(
            float((item.get("semantic") or {}).get("airflow_cmh") or 0.0)
            for item in supplies
        )
        exhaust_cmh = sum(
            float((item.get("semantic") or {}).get("airflow_cmh") or 0.0)
            for item in exhausts
        )
    except (TypeError, ValueError):
        supply_cmh = exhaust_cmh = 0.0
    reference = max(supply_cmh, exhaust_cmh)
    if reference > 0 and abs(supply_cmh - exhaust_cmh) / reference > 0.01:
        issues.append({
            "code": "TERMINAL_FLOW_IMBALANCE", "severity": "error",
            "message": "Supply and exhaust design airflows must balance within 1%.",
        })
    return migrated, issues


def _review_body_gci_geometry(path):
    path = Path(path).expanduser().resolve()
    source = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(source, dict):
        raise ValueError("geometry.json 최상위 값은 객체여야 합니다.")
    migrated = migrate_geometry(
        source,
        source_path=str(source.get("source") or path),
    )
    _, issues = _review_body_gci_geometry_data(migrated)
    return source, issues


def _geometry_source_path(geometry_path, geometry):
    source_value = (geometry or {}).get("source")
    if isinstance(source_value, dict):
        source_value = (
            source_value.get("path") or source_value.get("file")
            or source_value.get("name") or ""
        )
    source_path = Path(str(source_value or ""))
    if not source_path.is_absolute():
        source_path = Path(geometry_path).resolve().parent / source_path
    return source_path.resolve()


_BODY_GCI_ISSUE_MESSAGES = {
    "SPACE_MISSING": "해석할 방 또는 구역이 없습니다.",
    "SPACE_NOT_CLOSED": "방 경계가 닫혀 있지 않습니다.",
    "SPACE_CONFIRMATION_REQUIRED": "해석할 방을 아직 확인하지 않았습니다.",
    "SPACE_BOUNDARY_OPEN": "방 경계에 열린 부분이 있습니다.",
    "SPACE_HEIGHT_REQUIRED": "방 천장 높이가 필요합니다.",
    "TERMINAL_ROLE_REQUIRED": "급기 또는 배기 역할을 선택해야 합니다.",
    "TERMINAL_AIRFLOW_REQUIRED": "급·배기 풍량(CMH)을 입력해야 합니다.",
    "TERMINAL_HOST_REQUIRED": "급·배기구가 붙은 벽 또는 천장을 선택해야 합니다.",
    "TERMINAL_ELEVATION_REQUIRED": "벽 급·배기구의 중심 높이가 필요합니다.",
    "TERMINAL_NORMAL_REQUIRED": "급·배기 방향을 확인해야 합니다.",
    "TERMINAL_CONFIRMATION_REQUIRED": "급·배기구 확인이 필요합니다.",
    "EQUIPMENT_CONFIRMATION_REQUIRED": "장비의 고체 또는 발열원 역할을 확인해야 합니다.",
    "EQUIPMENT_POWER_REQUIRED": "발열 장비의 열량(kW)이 필요합니다.",
    "EQUIPMENT_CONVECTIVE_FRACTION_REQUIRED": "발열 장비의 대류열 비율을 확인해야 합니다.",
    "EQUIPMENT_HEAT_EVIDENCE_REQUIRED": "발열 장비 kW의 계산서·장비표 등 근거를 기록해야 합니다.",
    "EQUIPMENT_HEIGHT_REQUIRED": "장비의 실제 높이가 필요합니다.",
    "SINGLE_ZONE_REQUIRED": "GCI 계산에는 확인된 방을 하나만 선택해야 합니다.",
    "SUPPLY_MISSING": "열유동 계산에 사용할 급기구가 없습니다.",
    "EXHAUST_MISSING": "열유동 계산에 사용할 배기구가 없습니다.",
    "HEAT_SOURCE_MISSING": "열유동 계산에 사용할 발열원이 없습니다.",
    "TERMINAL_FLOW_IMBALANCE": "총 급기풍량과 총 배기풍량의 차이가 1%를 초과합니다.",
}


def _friendly_body_gci_issues(issues):
    rows = []
    for issue in issues:
        code = str(issue.get("code") or "UNKNOWN")
        message = _BODY_GCI_ISSUE_MESSAGES.get(code)
        if message is None:
            message = "도면 변환 정보가 오래되었거나 불완전합니다."
        rows.append({
            **issue,
            "user_message": message,
            "action": (
                "새 해석 화면의 3D 의미 확인 단계에서 해당 항목을 확인한 뒤 저장하세요."
                if code in _BODY_GCI_ISSUE_MESSAGES else
                "원본 DXF를 다시 불러와 자동 변환과 의미 확인을 다시 진행하세요."
            ),
        })
    return rows


def body_gci_geometry_candidates():
    """List project geometries by friendly label so users never need a path."""
    root = Path(ROOT).expanduser().resolve()
    paths = set()
    if root.is_dir():
        for path in root.rglob("*.geometry.json"):
            if path.is_file():
                paths.add(path.resolve())
    for study in cfd_gci_job.list_studies(root):
        geometry = Path(str((study.get("input") or {}).get("geometry_path") or ""))
        if geometry.is_file():
            paths.add(geometry.resolve())

    rows = []
    for path in paths:
        try:
            source, issues = _review_body_gci_geometry(path)
            modified = path.stat().st_mtime
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        source_value = source.get("source")
        if isinstance(source_value, dict):
            source_value = (
                source_value.get("path") or source_value.get("file")
                or source_value.get("name") or ""
            )
        source_text = str(source_value or "")
        source_name = Path(source_text).name if source_text else ""
        is_g2 = "g2_thermal" in str(path).replace("\\", "/").lower()
        is_sample = (
            not is_g2
            and field_acceptance.is_bundled_sample_drawing(
                _geometry_source_path(path, source), root
            )
        )
        label = (
            "G2 열유동 기준방" if is_g2 else
            source_name or path.name.removesuffix(".geometry.json")
        )
        rows.append({
            "id": hashlib.sha256(
                os.path.normcase(str(path)).encode("utf-8")
            ).hexdigest()[:16],
            "label": label,
            "kind": "benchmark" if is_g2 else "project",
            "ready": not issues,
            "field_eligible": not is_g2 and not is_sample,
            "field_reason": (
                "프로그램에 포함된 샘플은 현장 실증 계산에 사용할 수 없습니다."
                if is_sample else ""
            ),
            "issue_count": len(issues),
            "issues": _friendly_body_gci_issues(issues[:5]),
            "path": str(path),
            "modified_at": datetime.fromtimestamp(
                modified, tz=timezone.utc
            ).isoformat(),
        })
    rows.sort(
        key=lambda row: (
            row["ready"], row["kind"] == "benchmark", row["modified_at"]
        ),
        reverse=True,
    )
    return {"ok": True, "geometries": rows[:100]}


def start_body_gci_selection(geometry_id="", geometry_path="", settings=None):
    """Resolve a UI selection token, retaining path input only as an advanced fallback."""
    if geometry_id:
        candidate = next(
            (
                row for row in body_gci_geometry_candidates()["geometries"]
                if row["id"] == geometry_id
            ),
            None,
        )
        if candidate is None:
            return {"ok": False, "error": "선택한 도면을 다시 찾을 수 없습니다. 목록을 새로고침하세요."}
        if not candidate["ready"]:
            return {
                "ok": False,
                "error": "3D/CFD 의미 확인이 끝난 도면을 선택해 주세요.",
                "issues": candidate["issues"],
            }
        geometry_path = candidate["path"]
    if not str(geometry_path or "").strip():
        return {"ok": False, "error": "검증할 도면을 선택해 주세요."}
    try:
        _, issues = _review_body_gci_geometry(geometry_path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"선택한 도면을 읽지 못했습니다: {exc}"}
    if issues:
        return {
            "ok": False,
            "error": "3D/CFD 의미 확인이 끝난 도면을 선택해 주세요.",
            "issues": _friendly_body_gci_issues(issues[:5]),
        }
    return start_body_gci_job(geometry_path, settings=settings)


def field_pipeline_jobs_payload():
    """List persistent field jobs with queue and cross-process state."""
    with RUN_LOCK:
        queued = {row.get("name") for row in RUN["queue"]
                  if row.get("kind") == "field_pipeline"}
        active = (RUN["active"].get("job_name")
                  if RUN["active"] and RUN["active"].get("kind") == "field_pipeline"
                  else None)
    rows = []
    for saved in field_pipeline_job.list_jobs(ROOT):
        row = dict(saved)
        if field_pipeline_job.is_terminal_status(row.get("status")):
            row = field_pipeline_job.review_terminal_job_citation(ROOT, row)
        row["level"] = dict(saved.get("level") or {})
        owner = field_pipeline_job.active_run_lock(ROOT, row.get("job"))
        row["runtime_state"] = (
            "running" if row.get("job") == active or owner is not None else
            "queued" if row.get("job") in queued else "idle"
        )
        if owner is not None:
            row["run_lock"] = {"pid": owner.get("pid"),
                               "started_at": owner.get("started_at")}
            row["persisted_status"] = row.get("status")
            row["status"] = "running"
        _add_field_pipeline_live_estimate(row)
        if (field_pipeline_job.is_terminal_status(row.get("status"))
                and row.get("result_case")):
            case_name = Path(row["result_case"]).name
            row["results_url"] = "/body-results/" + quote(case_name)
            row["report_url"] = "/body-report/" + quote(case_name)
        rows.append(row)
    return {"ok": True, "jobs": rows}


def _add_field_pipeline_live_estimate(row):
    """Reuse the bounded GCI estimator for a single design level."""
    level = row.get("level")
    if not isinstance(level, dict):
        return
    shadow = {
        "runtime_state": row.get("runtime_state"),
        "stage": row.get("stage"),
        "updated_at": row.get("updated_at"),
        "levels": [level],
    }
    _add_body_gci_live_estimate(shadow)
    if shadow.get("live_progress"):
        row["live_progress"] = shadow["live_progress"]


def start_field_pipeline_selection(geometry_id="", geometry_path="", settings=None):
    """Resolve a confirmed drawing and start its one-click 3 FTT pipeline."""
    runtime = OPENFOAM_CAPABILITIES or diagnose_openfoam()
    if not runtime.get("thermal_detailed_ready"):
        return {"ok": False, "error": "현장 자동 해석에는 검증된 OpenFOAM v2606 환경이 필요합니다."}
    freecad = FREECAD_CAPABILITIES or diagnose_freecad()
    if not freecad.get("ok"):
        return {"ok": False, "error": "현장 자동 해석에는 검증된 FreeCAD/OCC 환경이 필요합니다."}
    if geometry_id:
        candidate = next((row for row in body_gci_geometry_candidates()["geometries"]
                          if row["id"] == geometry_id), None)
        if candidate is None:
            return {"ok": False, "error": "선택한 도면을 다시 찾을 수 없습니다. 목록을 새로 고치세요."}
        if candidate.get("kind") != "project":
            return {"ok": False, "error": "현장 자동 해석에는 프로젝트로 불러온 DXF를 선택하세요."}
        if not candidate.get("field_eligible", True):
            return {"ok": False, "error": candidate.get("field_reason")
                    or "실제 현장 도면을 선택하세요."}
        if not candidate.get("ready"):
            return {"ok": False, "error": "3D/CFD 입력 확인이 끝난 도면을 선택하세요.",
                    "issues": candidate.get("issues") or []}
        geometry_path = candidate["path"]
    if not str(geometry_path or "").strip():
        return {"ok": False, "error": "자동 해석할 도면을 선택하세요."}
    try:
        source, issues = _review_body_gci_geometry(geometry_path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"선택한 도면을 읽지 못했습니다: {exc}"}
    if issues:
        return {"ok": False, "error": "3D/CFD 입력 확인이 끝나지 않았습니다.",
                "issues": _friendly_body_gci_issues(issues[:5])}
    if field_acceptance.is_bundled_sample_drawing(
            _geometry_source_path(geometry_path, source), ROOT):
        return {
            "ok": False,
            "error": "프로그램에 포함된 샘플은 현장 실증 계산에 사용할 수 없습니다.",
        }

    # Detached GCI calculations own WSL independently of the Studio worker.
    # Do not start a second heavy solver against the same workstation.
    running_gci = next((row.get("study") for row in cfd_gci_job.list_studies(ROOT)
                        if cfd_gci_job.active_run_lock(ROOT, row.get("study"))), None)
    if running_gci:
        return {"ok": False, "error":
                f"메시 검증 작업 {running_gci}이 실행 중입니다. 완료 후 현장 해석을 시작하세요."}
    created = field_pipeline_job.create_job(ROOT, geometry_path, settings=settings)
    if not created.get("ok"):
        return created
    manifest = created["manifest"]
    if field_pipeline_job.is_terminal_status(manifest.get("status")):
        manifest = field_pipeline_job.review_terminal_job_citation(ROOT, manifest)
        case_name = Path(manifest.get("result_case") or "").name
        return {**created, "queued": False,
                "manifest": manifest,
                "status": manifest.get("status"),
                "citation_status": manifest.get("citation_status"),
                "citation_blockers": list(manifest.get("citation_blockers") or []),
                "results_url": "/body-results/" + quote(case_name)}
    error = _queue_field_pipeline_job(created["job"])
    return ({"ok": False, "error": error, "job": created["job"]} if error else
            {**created, "queued": True})


def resume_field_pipeline_job(job_id):
    manifest = field_pipeline_job.load_job(ROOT, job_id)
    if manifest is None:
        return {"ok": False, "error": "현장 자동 해석 작업을 찾을 수 없습니다."}
    if field_pipeline_job.is_terminal_status(manifest.get("status")):
        manifest = field_pipeline_job.review_terminal_job_citation(ROOT, manifest)
        case_name = Path(manifest.get("result_case") or "").name
        return {"ok": True, "job": job_id, "queued": False,
                "manifest": manifest,
                "status": manifest.get("status"),
                "citation_status": manifest.get("citation_status"),
                "citation_blockers": list(manifest.get("citation_blockers") or []),
                "results_url": "/body-results/" + quote(case_name)}
    error = _queue_field_pipeline_job(job_id)
    return ({"ok": False, "error": error, "job": job_id} if error else
            {"ok": True, "job": job_id, "queued": True, "manifest": manifest})


def confirm_body_gci_geometry(geometry_path, zone_index, height_m,
                              terminals, obstacles, bbox=None, unit_confirmed=False):
    """Save a reviewed single-zone thermal geometry without altering the source."""
    root = Path(ROOT).expanduser().resolve()
    source_path = Path(str(geometry_path or "")).expanduser().resolve()
    try:
        source_path.relative_to(root)
    except ValueError:
        return {
            "ok": False,
            "error": "프로젝트 밖의 파일은 직접 수정하지 않습니다. DXF를 먼저 불러오세요.",
        }
    if not source_path.is_file():
        return {"ok": False, "error": "확인할 geometry.json 파일이 없습니다."}
    try:
        source = json.loads(source_path.read_text(encoding="utf-8"))
        geometry = migrate_geometry(
            source, source_path=str(source.get("source") or source_path)
        )
        height_mm = float(height_m) * 1000.0
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"도면 확인 입력을 읽지 못했습니다: {exc}"}
    unit_review = dict(geometry.get("unit_review") or {})
    if unit_review.get("required"):
        if unit_confirmed is not True:
            return {
                "ok": False,
                "error": "DXF 헤더와 실제 좌표 단위가 다릅니다. 화면에서 mm 단위를 먼저 확인해 주세요.",
            }
        unit_review.update({
            "required": False, "resolved": True,
            "resolution": "user_confirmed_mm",
            "resolved_at": _utc_now(),
        })
        geometry["unit_review"] = unit_review
        geometry.setdefault("source_units", {})["selection_source"] = "user_confirmed_mm"
    zones = (geometry.get("elements") or {}).get("zone") or []
    if zone_index not in (None, ""):
        try:
            zone_index = int(zone_index)
        except (ValueError, TypeError):
            return {"ok": False, "error": "해석할 방 번호를 확인해 주세요."}
    elif bbox is not None:
        try:
            values = ([float(value) for value in bbox.split(",")]
                      if isinstance(bbox, str) else [float(value) for value in bbox])
        except (ValueError, TypeError):
            return {"ok": False, "error": "추정 구역 범위는 x0,y0,x1,y1 네 숫자여야 합니다."}
        if len(values) != 4 or not (values[0] < values[2] and values[1] < values[3]):
            return {"ok": False, "error": "추정 구역 범위와 순서를 확인해 주세요."}
        candidates = geometry.get("zone_candidates") or []
        matched = next((candidate for candidate in candidates
                        if len(candidate.get("bbox_mm") or []) == 4
                        and all(abs(float(a) - float(b)) <= 1.0
                                for a, b in zip(values, candidate["bbox_mm"]))), None)
        if not matched:
            return {"ok": False, "error": "파서가 제안한 구역을 선택한 뒤 확인해 주세요."}
        x0c, y0c, x1c, y1c = values
        zones.append({
            "kind": "polyline", "closed": True,
            "points": [[x0c, y0c], [x1c, y0c], [x1c, y1c], [x0c, y1c], [x0c, y0c]],
            "layer": "USER_CONFIRMED_BBOX", "z_base": 0.0, "confirmed": False,
            "source_ref": {
                "layer": "USER_CONFIRMED_BBOX",
                "entity_type": "UI_CONFIRMED_ZONE_CANDIDATE",
                "candidate_layer": matched.get("source_layer"),
            },
            "semantic": {"kind": "space", "boundary": "closed"},
        })
        zone_index = len(zones) - 1
        geometry.setdefault("elements", {})["zone"] = zones
    else:
        return {"ok": False, "error": "해석할 방 또는 추정 구역을 선택해 주세요."}
    if not (0 <= zone_index < len(zones)):
        return {"ok": False, "error": "해석할 방을 하나 선택해 주세요."}
    if not 100.0 <= height_mm <= 50000.0:
        return {"ok": False, "error": "천장 높이는 0.1~50 m 범위로 입력해 주세요."}
    extent = cfd_export._xy_extent([zones[zone_index]])
    if not extent:
        return {"ok": False, "error": "선택한 방의 닫힌 경계를 읽을 수 없습니다."}
    x0, y0, x1, y1 = [float(value) for value in extent]
    room_l, room_w = (x1 - x0) / 1000.0, (y1 - y0) / 1000.0
    terminals = list(terminals or [])
    obstacles = list(obstacles or [])
    if not 2 <= len(terminals) <= 100:
        return {"ok": False, "error": "급기구와 배기구를 합쳐 2~100개 입력해 주세요."}
    if len(obstacles) > 200:
        return {"ok": False, "error": "장비와 장애물은 최대 200개까지 확인할 수 있습니다."}

    # Drawing-derived terminals must retain the migrated DXF element identity.
    # The browser is allowed to edit CFD settings, but must not be able to
    # substitute a different CAD handle/layer for a detected terminal.
    drawing_terminal_sources = {}
    drawing_obstacle_sources = {}
    ambiguous_drawing_source_ids = set()

    def drawing_source_id(source_equipment):
        source_ref = dict(source_equipment.get("source_ref") or {})
        return str(
            source_equipment.get("id") or source_ref.get("handle") or ""
        ).strip()

    def is_explicit_manual_source_ref(source_ref):
        """Return true only for the server-owned manual-input provenance shape.

        A prior reviewed geometry may contain a UI_INPUT row.  It is not a CAD
        object even if an old or tampered file happens to carry a ``handle``;
        letting that row into a DXF lookup map would turn browser data into
        false drawing provenance on the next confirmation.
        """
        if not isinstance(source_ref, dict):
            return False
        entity_type = str(source_ref.get("entity_type") or "").strip().upper()
        return entity_type in {"UI_INPUT", "LEGACY_UI_INPUT"}

    for source_equipment in (geometry.get("elements") or {}).get("equipment") or []:
        source_id = drawing_source_id(source_equipment)
        semantic = cfd_export._equipment_semantics(source_equipment)
        try:
            is_dxf_source = (
                source_reference_kind(
                    source_equipment.get("source_ref"),
                    source_id,
                    override_of_dxf=semantic.get("override_of_dxf") is True,
                ) == "dxf"
            )
        except HeatSourceContractError:
            is_dxf_source = False
        # A saved UI_INPUT row may have an ID too.  Only a handle-bearing CAD
        # reference is a drawing candidate; otherwise re-opening a confirmed
        # manual heat input must not recast it as a DXF override.
        if (source_id and is_dxf_source
                and not is_explicit_manual_source_ref(
                    source_equipment.get("source_ref"))):
            source_key = source_id.casefold()
            if source_key in ambiguous_drawing_source_ids:
                continue
            existing_terminal = drawing_terminal_sources.get(source_key)
            existing_obstacle = drawing_obstacle_sources.get(source_key)
            if ((existing_terminal is not None and
                 existing_terminal is not source_equipment)
                    or (existing_obstacle is not None and
                        existing_obstacle is not source_equipment)):
                # A case-insensitive browser lookup cannot safely decide
                # which original CAD object the user meant.  Do not let the
                # last imported record silently replace the first.
                ambiguous_drawing_source_ids.add(source_key)
                drawing_terminal_sources.pop(source_key, None)
                drawing_obstacle_sources.pop(source_key, None)
                continue
            if semantic.get("kind") == "air_terminal":
                drawing_terminal_sources[source_key] = source_equipment
            else:
                drawing_obstacle_sources[source_key] = source_equipment

    reserved_source_ids = (
        set(drawing_terminal_sources)
        | set(drawing_obstacle_sources)
        | set(ambiguous_drawing_source_ids)
    )
    assigned_manual_source_ids = set()

    def allocate_manual_source_identity(kind, index):
        """Create a deterministic, collision-safe server-owned UI identity."""
        base_source_id = f"manual_{kind}_{index}"
        source_id = base_source_id
        suffix = 2
        while source_id.casefold() in (
                reserved_source_ids | assigned_manual_source_ids):
            source_id = f"{base_source_id}_{suffix}"
            suffix += 1
        source_key = source_id.casefold()
        assigned_manual_source_ids.add(source_key)
        source_label = source_id.upper()
        return source_id, source_label, {
            "layer": "USER_CONFIRMED",
            "block_name": source_label,
            "entity_type": "UI_INPUT",
            "source_id": source_id,
        }

    drawing_terminal_defaults = {
        str(candidate.get("source_id") or "").strip().casefold(): candidate
        for candidate in cfd_export.diffusers_from_geometry(geometry, zone=zone_index)
        if str(candidate.get("source_id") or "").strip()
    }

    def terminal_matches_dxf(row, candidate):
        """True only while the reviewed CFD terminal still matches DXF data."""
        if candidate.get("requires_role_review") or candidate.get("role") == "unresolved":
            return False
        for field, expected in (
                ("role", candidate.get("role")),
                ("type", candidate.get("type")),
                ("wall", candidate.get("host_surface"))):
            if expected not in (None, "", "unresolved") and str(row.get(field) or "") != str(expected):
                return False
        for field in ("cx", "cy", "w", "h"):
            try:
                if abs(float(row.get(field)) - float(candidate.get(field))) > 1e-6:
                    return False
            except (TypeError, ValueError):
                return False
        expected_airflow = candidate.get("airflow_cmh")
        if expected_airflow not in (None, ""):
            try:
                if abs(float(row.get("cmh")) - float(expected_airflow)) > 1e-6:
                    return False
            except (TypeError, ValueError):
                return False
        return True

    outward = {
        "ceiling": [0.0, 0.0, 1.0], "floor": [0.0, 0.0, -1.0],
        "x0": [-1.0, 0.0, 0.0], "xL": [1.0, 0.0, 0.0],
        "y0": [0.0, -1.0, 0.0], "yW": [0.0, 1.0, 0.0],
    }
    terminal_records = []
    seen_dxf_source_ids = set()
    supply_cmh = exhaust_cmh = 0.0
    for index, row in enumerate(terminals, 1):
        try:
            role = str(row.get("role") or "")
            host = str(row.get("wall") or row.get("host_surface") or "")
            cx, cy = float(row.get("cx")), float(row.get("cy"))
            width, height = float(row.get("w")), float(row.get("h"))
            airflow = float(row.get("cmh"))
        except (ValueError, TypeError, AttributeError):
            return {"ok": False, "error": f"{index}번 급·배기구의 숫자 입력을 확인해 주세요."}
        if role not in ("supply", "exhaust") or host not in outward:
            return {"ok": False, "error": f"{index}번 급·배기구의 역할과 설치 면을 확인해 주세요."}
        if width <= 0 or height <= 0 or airflow <= 0:
            return {"ok": False, "error": f"{index}번 급·배기구의 크기와 풍량은 0보다 커야 합니다."}
        if host in ("ceiling", "floor"):
            if not (0 <= cx <= room_l and 0 <= cy <= room_w):
                return {"ok": False, "error": f"{index}번 천장/바닥 급·배기구가 방 범위를 벗어났습니다."}
            center = [x0 + cx * 1000.0, y0 + cy * 1000.0]
            center_z = height_mm if host == "ceiling" else 0.0
        elif host in ("x0", "xL"):
            if not (0 <= cx <= room_w and 0 < cy < height_mm / 1000.0):
                return {"ok": False, "error": f"{index}번 벽 급·배기구의 위치 또는 높이가 범위를 벗어났습니다."}
            center = [x0 if host == "x0" else x1, y0 + cx * 1000.0]
            center_z = cy * 1000.0
        else:
            if not (0 <= cx <= room_l and 0 < cy < height_mm / 1000.0):
                return {"ok": False, "error": f"{index}번 벽 급·배기구의 위치 또는 높이가 범위를 벗어났습니다."}
            center = [x0 + cx * 1000.0, y0 if host == "y0" else y1]
            center_z = cy * 1000.0
        normal = outward[host]
        if role == "supply":
            normal = [-value for value in normal]
            supply_cmh += airflow
        else:
            exhaust_cmh += airflow
        source_id = str(row.get("source_id") or "").strip()
        source_type = str(row.get("source_type") or "").strip()
        source_key = source_id.casefold()
        if source_key and source_key in ambiguous_drawing_source_ids:
            return {
                "ok": False,
                "error": f"{index}번 급·배기구의 DXF source_id '{source_id}'가 대소문자 기준으로 모호합니다.",
            }
        source_equipment = drawing_terminal_sources.get(source_key)
        if source_key and source_key in drawing_obstacle_sources:
            return {
                "ok": False,
                "error": f"{index}번 급·배기구의 source_id는 DXF 장애물 원본입니다.",
            }
        claims_drawing_origin = (
            source_type.casefold() == "dxf_detected"
            or row.get("override_of_dxf") is True
        )
        if claims_drawing_origin and source_equipment is None:
            return {
                "ok": False,
                "error": f"{index}번 급·배기구의 DXF 원본 식별자를 확인할 수 없습니다.",
            }
        # Original drawing membership is authoritative.  Browser flags may
        # describe review state, but cannot turn a DXF item into manual input.
        drawing_derived = source_equipment is not None
        drawing_override = bool(row.get("override_of_dxf")) and drawing_derived
        source_label = str(row.get("source_label") or "").strip()
        if drawing_derived:
            if source_key in seen_dxf_source_ids:
                return {
                    "ok": False,
                    "error": f"{index}번 급·배기구의 DXF source_id '{source_id}'가 중복되었습니다.",
                }
            seen_dxf_source_ids.add(source_key)
            source_id = drawing_source_id(source_equipment)
            source_ref = dict(source_equipment.get("source_ref") or {})
            source_label = str(
                source_ref.get("block_name") or source_ref.get("layer")
                or source_equipment.get("block_name") or source_equipment.get("layer")
                or source_id
            )
            dxf_values_unchanged = (
                not drawing_override and terminal_matches_dxf(
                    row, drawing_terminal_defaults.get(source_key, {})
                )
            )
        else:
            # The browser is not an authority for manual source identities or
            # CAD references.  Replace every submitted manual terminal ID/ref
            # with a server-owned UI_INPUT record.
            source_id, source_label, source_ref = allocate_manual_source_identity(
                "terminal", index
            )
        terminal_record = {
            "kind": "circle", "center": center,
            "radius": min(width, height) * 500.0,
            "z_base": 0.0, "zone": zone_index, "confirmed": True,
            "source_ref": source_ref,
            "semantic": {
                "kind": "air_terminal", "role": role,
                "airflow_cmh": airflow, "host_surface": host,
                "normal": normal, "center_z_mm": center_z,
                "width_mm": width * 1000.0, "height_mm": height * 1000.0,
                "terminal_type": str(row.get("type") or "grille"),
            },
        }
        if drawing_derived:
            terminal_record["id"] = source_id
            terminal_record["source_label"] = source_label
            if dxf_values_unchanged:
                terminal_record["semantic"]["source_type"] = "dxf_detected"
            else:
                terminal_record["semantic"].update({
                    "source_type": "user_confirmed",
                    "override_of_dxf": True,
                })
        else:
            terminal_record["id"] = source_id
            terminal_record["source_label"] = source_label
            terminal_record["semantic"]["source_type"] = "user_confirmed"
        terminal_records.append(terminal_record)
    reference = max(supply_cmh, exhaust_cmh)
    if supply_cmh <= 0 or exhaust_cmh <= 0:
        return {"ok": False, "error": "급기구와 배기구의 설계풍량을 모두 입력해 주세요."}
    if abs(supply_cmh - exhaust_cmh) / reference > 0.01:
        return {
            "ok": False,
            "error": f"총 급기 {supply_cmh:.1f} CMH와 총 배기 {exhaust_cmh:.1f} CMH의 차이를 1% 이내로 맞춰 주세요.",
        }

    equipment_records = []
    heat_source_count = 0
    for index, row in enumerate(obstacles, 1):
        if str(row.get("kind") or "equipment") == "column":
            continue
        try:
            ox0, oy0 = float(row.get("x0")), float(row.get("y0"))
            ox1, oy1 = float(row.get("x1")), float(row.get("y1"))
            item_height = float(row.get("h"))
            power_kw = float(row.get("kw") or 0.0)
        except (ValueError, TypeError, AttributeError):
            return {"ok": False, "error": f"{index}번 장비의 좌표·높이·발열량을 확인해 주세요."}
        if not (0 <= ox0 < ox1 <= room_l and 0 <= oy0 < oy1 <= room_w):
            return {"ok": False, "error": f"{index}번 장비가 선택한 방 범위를 벗어났습니다."}
        if not 0 < item_height <= height_mm / 1000.0 or power_kw < 0:
            return {"ok": False, "error": f"{index}번 장비의 높이 또는 발열량을 확인해 주세요."}
        role = "heat_source" if power_kw > 0 else "solid"
        if role == "heat_source":
            try:
                convective_fraction = float(row.get("convective_fraction"))
            except (TypeError, ValueError):
                return {
                    "ok": False,
                    "error": f"{index}번 발열 장비의 대류분율(0~1)을 확인해 주세요.",
                }
            evidence = str(row.get("evidence") or "").strip()
            if not 0 < convective_fraction <= 1:
                return {
                    "ok": False,
                    "error": f"{index}번 발열 장비의 대류분율은 0보다 크고 1 이하여야 합니다.",
                }
            if not evidence:
                return {
                    "ok": False,
                    "error": f"{index}번 발열 장비의 kW 근거를 입력해 주세요.",
                }
            heat_source_count += 1
        source_id = str(row.get("source_id") or "").strip()
        source_label = str(row.get("source_label") or "").strip()
        source_type = str(row.get("source_type") or "").strip()
        source_key = source_id.casefold()
        if source_key and source_key in ambiguous_drawing_source_ids:
            return {
                "ok": False,
                "error": f"{index}번 장애물의 DXF source_id '{source_id}'가 대소문자 기준으로 모호합니다.",
            }
        source_equipment = drawing_obstacle_sources.get(source_key)
        if source_key and source_key in drawing_terminal_sources:
            return {
                "ok": False,
                "error": f"{index}번 장애물의 source_id는 DXF 급·배기구 원본입니다.",
            }
        claims_drawing_origin = (
            source_type.casefold() == "dxf_detected"
            or row.get("override_of_dxf") is True
        )
        if claims_drawing_origin and source_equipment is None:
            return {
                "ok": False,
                "error": f"{index}번 장애물의 DXF 원본 식별자를 확인할 수 없습니다.",
            }
        drawing_derived = source_equipment is not None
        if drawing_derived:
            if source_key in seen_dxf_source_ids:
                return {
                    "ok": False,
                    "error": f"{index}번 장애물의 DXF source_id '{source_id}'가 중복되었습니다.",
                }
            seen_dxf_source_ids.add(source_key)
            source_id = drawing_source_id(source_equipment)
            source_ref = dict(source_equipment.get("source_ref") or {})
            source_label = str(
                source_ref.get("block_name") or source_ref.get("layer")
                or source_equipment.get("block_name") or source_equipment.get("layer")
                or source_id
            )
        elif role == "heat_source":
            # A manual heat source is a user input, not a browser-provided CAD
            # identity.  Regenerate its ID/label/reference so it cannot be
            # mistaken for an original DXF object when the geometry is reopened.
            source_id, source_label, source_ref = allocate_manual_source_identity(
                "heat", index
            )
        else:
            # The browser cannot designate a manual load as a CAD object.
            # Make an explicit server-owned manual provenance record instead.
            source_ref = {
                "layer": "USER_CONFIRMED",
                "block_name": source_label or f"EQUIPMENT_{index}",
                "entity_type": "UI_INPUT",
            }
            if source_id:
                source_ref["source_id"] = source_id
        record = {
            "kind": "polyline", "closed": True,
            "points": [
                [x0 + ox0 * 1000.0, y0 + oy0 * 1000.0],
                [x0 + ox1 * 1000.0, y0 + oy0 * 1000.0],
                [x0 + ox1 * 1000.0, y0 + oy1 * 1000.0],
                [x0 + ox0 * 1000.0, y0 + oy1 * 1000.0],
            ],
            "z_base": 0.0, "zone": zone_index, "confirmed": True,
            "source_ref": source_ref,
            "semantic": {
                "kind": "equipment", "role": role,
                "height_mm": item_height * 1000.0,
                **({
                    "power_kw": power_kw,
                    "convective_fraction": convective_fraction,
                    "evidence": evidence,
                    # A DXF item becomes a heat source only after the reviewer
                    # supplies kW/split/evidence.  Keep the original DXF ref,
                    # but record the thermal input as user-confirmed so an
                    # unreviewed drawing detection cannot reach an exporter.
                    "source_type": "user_confirmed",
                    **({"override_of_dxf": True} if drawing_derived else {}),
                } if role == "heat_source" else {}),
            },
        }
        if source_id:
            record["id"] = source_id
        if source_label:
            record["source_label"] = source_label
        equipment_records.append(record)
    if heat_source_count == 0:
        return {"ok": False, "error": "발열량(kW)이 입력된 장비를 하나 이상 확인해 주세요."}

    for index, zone in enumerate(zones):
        zone["confirmed"] = index == zone_index
        if index == zone_index:
            semantic = dict(zone.get("semantic") or {})
            semantic.update({
                "kind": "space", "boundary": "closed",
                "ceiling_height_mm": height_mm, "height_source": "user_confirmed",
            })
            zone["semantic"] = semantic
    geometry.setdefault("params", {}).setdefault("wall", {})["height"] = height_mm
    geometry["elements"]["equipment"] = terminal_records + equipment_records
    geometry["confirmation"] = {
        "confirmed_at": _utc_now(),
        "source_geometry_path": str(source_path),
        "method": "studio_semantic_confirmation_v1",
        "zone_index": zone_index,
    }
    confirmed = migrate_geometry(geometry, source_path=geometry.get("source") or str(source_path))
    _, gci_issues = _review_body_gci_geometry_data(confirmed)
    if gci_issues:
        return {
            "ok": False, "error": "확정 입력의 GCI 준비 검사를 통과하지 못했습니다.",
            "issues": _friendly_body_gci_issues(gci_issues[:10]),
        }
    name = source_path.name
    suffix = ".geometry.json"
    stem = name[:-len(suffix)] if name.endswith(suffix) else source_path.stem
    if not stem.endswith(".confirmed"):
        stem += ".confirmed"
    output = source_path.with_name(stem + suffix)
    temporary = output.with_name(output.name + f".{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(confirmed, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, output)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return {
        "ok": True, "geometry": str(output.resolve()),
        "source_geometry": str(source_path), "gci_ready": True,
        "zone_index": zone_index,
        "inspection": inspect_geometry(output, zone=zone_index),
    }


def start_body_gci_job(geometry_path, settings=None):
    """Create a deterministic three-grid job and enqueue unfinished work."""
    runtime = OPENFOAM_CAPABILITIES or diagnose_openfoam()
    if not runtime.get("thermal_detailed_ready"):
        return {"ok": False, "error": "GCI 자동 계산에는 검증된 OpenFOAM v2606 환경이 필요합니다."}
    freecad = FREECAD_CAPABILITIES or diagnose_freecad()
    if not freecad.get("ok"):
        return {"ok": False, "error": "GCI 자동 계산에는 검증된 FreeCAD/OCC 환경이 필요합니다."}
    created = cfd_gci_job.create_study(ROOT, geometry_path, settings=settings)
    if not created.get("ok"):
        return created
    manifest = created["manifest"]
    if manifest.get("status") == "complete":
        return {**created, "queued": False,
                "report_url": "/body-gci-report/" + quote(created["study"])}
    error = _queue_body_gci_study(created["study"])
    if error:
        return {"ok": False, "error": error, "study": created["study"]}
    return {**created, "queued": True}


def resume_body_gci_job(study_id):
    """Resume a persisted job after a failure or application restart."""
    manifest = cfd_gci_job.load_study(ROOT, study_id)
    if manifest is None:
        return {"ok": False, "error": "GCI 자동 작업을 찾을 수 없습니다."}
    if manifest.get("status") == "complete":
        return {"ok": True, "study": study_id, "queued": False,
                "manifest": manifest,
                "report_url": "/body-gci-report/" + quote(study_id)}
    error = _queue_body_gci_study(study_id)
    return ({"ok": False, "error": error, "study": study_id} if error else
            {"ok": True, "study": study_id, "queued": True, "manifest": manifest})


def _add_body_gci_live_estimate(row):
    """Add a bounded between-checkpoint estimate for a running thermal level."""
    if row.get("runtime_state") != "running":
        return
    live = cfd_gci_job.bounded_live_progress(row, ROOT)
    if live is None:
        return
    level = next(
        (item for item in row.get("levels") or []
         if item.get("name") == live["level"]),
        None,
    )
    if level is None:
        return
    row["live_progress"] = live
    level["estimated_live_time_s"] = live["estimated_time_s"]
    level["estimated_flow_through_fraction"] = (
        live["estimated_flow_through_fraction"]
    )
    level["next_checkpoint_time_s"] = live["next_checkpoint_time_s"]


def body_gci_jobs_payload():
    queued = set()
    active = None
    with RUN_LOCK:
        queued = {row.get("name") for row in RUN["queue"]
                  if row.get("kind") == "body_gci"}
        if RUN["active"] and RUN["active"].get("kind") == "body_gci":
            active = RUN["active"].get("job_name")
    rows = []
    for manifest in cfd_gci_job.list_studies(ROOT):
        row = dict(manifest)
        row["levels"] = [dict(item) for item in manifest.get("levels") or []]
        owner = cfd_gci_job.active_run_lock(ROOT, row.get("study"))
        row["runtime_state"] = (
            "running" if row.get("study") == active or owner is not None else
            "queued" if row.get("study") in queued else "idle"
        )
        if owner is not None:
            row["run_lock"] = {
                "pid": owner.get("pid"),
                "started_at": owner.get("started_at"),
            }
            # A detached runner can still own the live calculation while the
            # last persisted attempt says FAIL (for example after a UI or
            # monitoring process was interrupted).  Show the live owner as the
            # authoritative current state without discarding audit history.
            row["persisted_status"] = row.get("status")
            row["status"] = "running"
            if row.get("error"):
                row["persisted_error"] = row["error"]
                row["error"] = ""
        _add_body_gci_live_estimate(row)
        if row.get("status") == "complete":
            row["report_url"] = "/body-gci-report/" + quote(row["study"])
        rows.append(row)
    return {"ok": True, "jobs": rows}


def _field_design_job_path(case_name):
    case = _body_solver_case(case_name)
    return case / "field_design_job.json" if case is not None else None


def _write_field_design_job(path, payload):
    temporary = Path(path).with_name(Path(path).name + f".{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def field_design_job_status(case_name):
    path = _field_design_job_path(case_name)
    manifest = {}
    if path is not None and path.is_file():
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            manifest = {"status": "FAIL", "error": "자동 계산 기록을 읽지 못했습니다."}
    runtime_state = "idle"
    with RUN_LOCK:
        if (RUN["active"] and RUN["active"].get("kind") == "field_thermal"
                and RUN["active"].get("job_name") == case_name):
            runtime_state = "running"
        elif any(row.get("kind") == "field_thermal" and row.get("name") == case_name
                 for row in RUN["queue"]):
            runtime_state = "queued"
    return {"ok": True, "case": case_name, "runtime_state": runtime_state,
            "manifest": manifest}


def field_design_status_payload(case_name):
    case = _body_solver_case(case_name)
    if case is None:
        return {"ok": False, "error": "상세 열해석 결과를 찾지 못했습니다."}
    try:
        run = json.loads((case / "run_manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"열해석 상태를 읽지 못했습니다: {exc}"}
    progress = run.get("thermal_progress") or {}
    return {
        **field_design_job_status(case_name),
        "run_status": run.get("status"), "design_ready": run.get("design_ready") is True,
        "latest_time_s": progress.get("latest_time_s"),
        "flow_through_fraction": float(progress.get("flow_through_fraction") or 0.0),
        "target_flow_through_fraction": FIELD_DESIGN_FLOW_FRACTION,
        "estimated_remaining_runtime_seconds": progress.get(
            "estimated_remaining_runtime_seconds"
        ),
    }


def enqueue_field_design_run(case_name):
    runtime = OPENFOAM_CAPABILITIES or diagnose_openfoam()
    if not runtime.get("thermal_detailed_ready"):
        return {"ok": False, "error": "3.0 교환시간 자동 계산에는 OpenFOAM v2606이 필요합니다."}
    case = _body_solver_case(case_name)
    if case is None:
        return {"ok": False, "error": "프로젝트의 상세 열해석 결과를 선택하세요."}
    try:
        run = json.loads((case / "run_manifest.json").read_text(encoding="utf-8"))
        if run.get("engine") != "body_fitted_buoyant_urans" or run.get("status") == "FAIL":
            return {"ok": False, "error": "이어 계산할 수 있는 열·부력 결과가 아닙니다."}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"열해석 기록을 읽지 못했습니다: {exc}"}
    start_worker = False
    with RUN_LOCK:
        if (RUN["active"] and RUN["active"].get("kind") == "field_thermal"
                and RUN["active"].get("job_name") == case_name):
            return {"ok": False, "error": "이미 자동 계산 중입니다."}
        if any(row.get("kind") == "field_thermal" and row.get("name") == case_name
               for row in RUN["queue"]):
            return {"ok": False, "error": "이미 자동 계산 대기 중입니다."}
        RUN["queue"].append({"name": case_name, "kind": "field_thermal"})
        RUN["history"].pop(case_name, None)
        if not RUN["worker"]:
            RUN["worker"] = True
            start_worker = True
    if start_worker:
        threading.Thread(target=_run_worker, daemon=True).start()
    return {"ok": True, "queued": True, "case": case_name}


def _do_mpi_runtime_smoke(act):
    """Run a fixed, cross-process-locked MPI recheck and publish its evidence."""
    token, owner = cfd_gci_job.acquire_solver_lock(ROOT)
    if token is None:
        return False, ("다른 CFD 작업이 OpenFOAM을 사용 중입니다. "
                       f"PID {owner.get('pid', 'unknown')}"), {}
    try:
        act["step"] = "MPI rank-spawn 안전 재점검"
        act["lines"].append("[MPI] -np 2, -np 4 hostname을 안전 제한으로 확인합니다.")
        del act["lines"][:-15]
        output_path = os.path.join(
            ROOT, "_release_evidence", "mpi_runtime_smoke.v1.json"
        )
        result = run_mpi_runtime_smoke(
            output_path,
            distro=(OPENFOAM_CAPABILITIES.get("distro") or None),
            timeout_seconds=10, cleanup_grace_seconds=3, ranks=(2, 4),
        )
        smoke = {
            "status": result.get("status"),
            "reason_code": result.get("reason_code"),
            "artifact_path": result.get("artifact_path"),
            "artifact_sha256": result.get("artifact_sha256"),
            "identity": result.get("identity"),
        }
        record_runtime_capability(_runtime_capability_path(), mpi_smoke=smoke)
        refresh_openfoam_runtime_evidence()
        status = str(result.get("status") or "BLOCKED")
        reason = str(result.get("reason_code") or "")
        act["step"] = "MPI 병렬 재점검 완료"
        act["lines"].append(
            f"[MPI] {status}" + (f" ({reason})" if reason else "")
        )
        del act["lines"][:-15]
        # BLOCKED is an honest diagnostic outcome, not a worker crash.  The
        # refreshed capability controls the UI and keeps calculations serial.
        return True, None, {
            "status": status, "reason_code": reason,
            "artifact_path": result.get("artifact_path") or "",
            "artifact_sha256": result.get("artifact_sha256") or "",
        }
    except Exception as exc:
        return False, f"MPI 병렬 재점검 오류: {type(exc).__name__}: {exc}", {}
    finally:
        cfd_gci_job.release_solver_lock(ROOT, token)


def _run_worker():
    """대기열을 하나씩 처리해 WSL 계산이 서로 경합하지 않게 한다."""
    while True:
        with RUN_LOCK:
            if not RUN["queue"]:
                RUN["worker"] = False
                return
            job = RUN["queue"].pop(0)
            name, kind = job["name"], job["kind"]
            if kind == "acceptance":
                case_dir = os.path.join(ROOT, "_system", "environment_acceptance")
                end_t = 100
                display_name = ACCEPTANCE_DISPLAY_NAME
            elif kind == "mpi_smoke":
                case_dir = os.path.join(ROOT, "_release_evidence")
                end_t = None
                display_name = MPI_SMOKE_DISPLAY_NAME
            elif kind == "body_gci":
                case_dir = os.path.join(ROOT, "_body_gci", name)
                end_t = None
                display_name = "4수준 메시 불확실성 자동 계산"
            elif kind == "field_thermal":
                case_dir = str(_body_solver_case(name) or "")
                end_t = None
                display_name = "현장 결과 3.0 교환시간 자동 계산"
            elif kind == "field_pipeline":
                case_dir = os.path.join(ROOT, "_field_jobs", name)
                end_t = None
                display_name = "현장 DXF 3D·CFD 자동 해석"
            else:
                case_dir = safe_case_dir(name)
                end_t = None
                display_name = name
                try:
                    with open(os.path.join(case_dir, "cfd_case_meta.json"), encoding="utf-8") as f:
                        end_t = json.load(f).get("config", {}).get("endTime")
                except Exception:
                    pass
            RUN["active"] = {"name": display_name, "job_name": name, "kind": kind,
                             "step": "준비", "time": 0.0,
                             "endTime": end_t, "lines": []}
        act = RUN["active"]
        details = {}
        if kind == "acceptance":
            _set_acceptance_state(status="running", ok=None, error="",
                                  started_at=_utc_now(), finished_at="")
        try:
            if kind == "grid":
                err = _do_gridstudy(name, case_dir, act)
                ok = err is None
            elif kind == "body_gci":
                ok, err, details = _do_body_gci(name, act)
            elif kind == "field_thermal":
                ok, err, details = _do_field_design_run(name, act)
            elif kind == "field_pipeline":
                ok, err, details = _do_field_pipeline(name, act)
            elif kind == "acceptance":
                ok, err, details = _do_environment_acceptance(case_dir, act)
            elif kind == "mpi_smoke":
                ok, err, details = _do_mpi_runtime_smoke(act)
            else:
                ok, err = _do_run(name, case_dir, act)
        except Exception as e:
            ok, err = False, f"{type(e).__name__}: {e}"
        with RUN_LOCK:
            RUN["history"][name] = {"ok": ok, "error": err, "kind": kind,
                                     **details}
            RUN["active"] = None
        if kind == "acceptance":
            _set_acceptance_state(status="passed" if ok else "failed", ok=ok,
                                  error=err or "", finished_at=_utc_now(), **details)
        else:
            FIELD_CACHE.pop(name, None)   # 결과 갱신 → 뷰어 캐시 무효화


def _do_body_gci(study_id, act):
    def callback(payload):
        act["step"] = payload.get("stage") or "GCI"
        act["levels"] = payload.get("levels") or []
        message = payload.get("message") or act["step"]
        act["lines"].append(str(message))
        del act["lines"][:-15]

    result = cfd_gci_job.run_study(ROOT, study_id, callback=callback)
    details = {
        "study": study_id,
        "gate_status": (result.get("manifest") or {}).get("gate_status"),
    }
    if result.get("ok"):
        details["report_url"] = "/body-gci-report/" + quote(study_id)
    return bool(result.get("ok")), result.get("error"), details


def _do_field_pipeline(job_id, act):
    def callback(payload):
        act["step"] = payload.get("stage") or "field_pipeline"
        act["level"] = payload.get("level") or {}
        message = payload.get("message") or act["step"]
        act["lines"].append(str(message))
        del act["lines"][:-15]

    result = field_pipeline_job.run_job(ROOT, job_id, callback=callback)
    manifest = result.get("manifest") or {}
    details = {"job": job_id, "case": result.get("case") or ""}
    if result.get("ok") and result.get("case"):
        details["results_url"] = "/body-results/" + quote(result["case"])
        details["report_url"] = "/body-report/" + quote(result["case"])
    elif manifest.get("level"):
        details["flow_through_fraction"] = (
            manifest["level"].get("flow_through_fraction")
        )
    return bool(result.get("ok")), result.get("error"), details


def _do_field_design_run(case_name, act):
    case = _body_solver_case(case_name)
    if case is None:
        return False, "상세 열해석 결과를 찾지 못했습니다.", {}
    job_path = case / "field_design_job.json"
    previous = {}
    if job_path.is_file():
        try:
            previous = json.loads(job_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            previous = {}
    resume_history = list(previous.get("resume_history") or [])
    if previous.get("status") in ("running", "FAIL"):
        resume_history.append({
            "resumed_at": _utc_now(), "previous_status": previous.get("status"),
            "checkpoint_time_s": previous.get("latest_time_s"),
            "flow_through_fraction": previous.get("flow_through_fraction"),
        })
    job = {
        "schema_version": 1, "contract": "field_design_job.v1",
        "case": case_name, "status": "running", "stage": "checking",
        "started_at": previous.get("started_at") or _utc_now(),
        "updated_at": _utc_now(), "attempts": int(previous.get("attempts") or 0) + 1,
        "target_flow_through_fraction": FIELD_DESIGN_FLOW_FRACTION,
        "latest_time_s": previous.get("latest_time_s"),
        "flow_through_fraction": previous.get("flow_through_fraction", 0.0),
        "resume_history": resume_history, "error": "",
    }

    def publish(stage, message=""):
        job["stage"] = stage
        job["updated_at"] = _utc_now()
        _write_field_design_job(job_path, job)
        act["step"] = stage
        if message:
            act["lines"].append(message)
            del act["lines"][:-15]

    def callback(line):
        text = str(line)
        if text.startswith("Time = "):
            try:
                act["time"] = float(text.split("=", 1)[1])
            except ValueError:
                pass
        act["lines"].append(text)
        del act["lines"][:-15]

    try:
        publish("checking", "현재 열해석 체크포인트 확인")
        for _ in range(FIELD_DESIGN_MAX_RUNS):
            run = json.loads((case / "run_manifest.json").read_text(encoding="utf-8"))
            progress = run.get("thermal_progress") or {}
            latest = float(progress.get("latest_time_s") or 0.0)
            flow_time = float(progress.get("flow_through_time_s") or 0.0)
            fraction = float(progress.get("flow_through_fraction") or 0.0)
            job.update(latest_time_s=latest, flow_through_fraction=fraction)
            if (run.get("status") == "PASS" and run.get("design_ready") is True
                    and fraction + 1e-12 >= FIELD_DESIGN_FLOW_FRACTION
                    and (case / "result_manifest.json").is_file()):
                job.update(status="complete", stage="complete", completed_at=_utc_now())
                publish("complete", "3.0 교환시간 설계 검토 계산 완료")
                report = cfd_report.generate_body_fitted_report(
                    case, projects_root=ROOT
                )
                return True, None, {
                    "case": case_name, "flow_through_fraction": fraction,
                    "report_url": "/body-report/" + quote(case_name)
                    if report.get("ok") else "",
                }
            if flow_time <= 0:
                raise RuntimeError("유동 교환시간을 계산하지 못했습니다.")
            remaining = max(0.0, flow_time * FIELD_DESIGN_FLOW_FRACTION - latest)
            recommended = float(progress.get("recommended_next_duration_s") or 0.0)
            if recommended <= 1e-9:
                recommended = min(remaining, 20.0)
            duration = min(remaining, recommended)
            if duration <= 1e-9:
                raise RuntimeError("목표 시간에 도달했지만 설계 검토 gate가 PASS가 아닙니다.")
            token, busy = _claim_solver_slot()
            if token is None:
                job.update(status="BLOCKED", stage="blocked", error=busy["error"])
                publish("blocked", busy["error"])
                return False, busy["error"], {
                    "case": case_name,
                    "flow_through_fraction": job.get("flow_through_fraction"),
                    "code": busy["code"], "lock": busy["lock"],
                }
            job["next_duration_s"] = duration
            publish("thermal_continue", f"{latest:.3f}s에서 {duration:.3f}s 이어 계산")
            try:
                result = cfd_physics.run_buoyant_continuation(
                    case,
                    settings={
                        "thermal_duration_s": duration,
                        "thermal_minimum_flow_through_fraction": FIELD_DESIGN_FLOW_FRACTION,
                    },
                    progress_cb=callback,
                )
            finally:
                cfd_gci_job.release_solver_lock(ROOT, token)
            if not result.get("ok"):
                raise RuntimeError(result.get("error") or "열해석 이어 계산 실패")
        raise RuntimeError("안전 반복 상한 안에 3.0 교환시간에 도달하지 못했습니다.")
    except Exception as exc:
        job.update(status="FAIL", stage="failed", error=str(exc), failed_at=_utc_now())
        publish("failed", str(exc))
        return False, str(exc), {"case": case_name,
                                 "flow_through_fraction": job.get("flow_through_fraction")}


def _do_run(name, case_dir, act):
    def cb(line):
        if line.startswith("Time = "):
            act["step"] = "solver"
            try:
                act["time"] = float(line.split("=", 1)[1])
            except ValueError:
                pass
        elif line.startswith("=== blockMesh"):
            act["step"] = "blockMesh"
        elif line.startswith("=== checkMesh"):
            act["step"] = "checkMesh"
        elif "Mesh OK" in line:
            act["mesh_ok"] = True
        elif line.startswith("=== solver"):
            act["step"] = "solver"
        act["lines"].append(line)
        del act["lines"][:-15]

    # 폐합이 통과할 때까지 자동으로 이어 돌린다. 한 번만 돌리고 멈추면
    # "계산은 끝났는데 물리적으로는 미완"인 결과가 그대로 리포트에 실린다.
    token, busy = _claim_solver_slot()
    if token is None:
        return False, busy["error"]
    try:
        r = run_until_closed(case_dir, progress_cb=cb)
    finally:
        cfd_gci_job.release_solver_lock(ROOT, token)
    err = r.get("error")
    if r["ok"]:
        act["step"] = "리포트 생성"
        try:
            cfd_report.generate_report(case_dir)
        except Exception as e:
            err = f"리포트 생성 실패: {e}"
    return (r["ok"] and not err), err


def _do_environment_acceptance(case_dir, act):
    """Generate and run a tiny deterministic case, then verify recovered output."""
    cfg = {
        "name": "environment_acceptance",
        "_note": "MEP CFD Studio 자동 환경 수용 테스트 — 설계 결과로 사용하지 않음",
        "room": {"L": 2.0, "W": 2.0, "H": 2.0},
        "mesh": {"cell": 0.5},
        "g": [0, 0, -9.81],
        "inlet": {"wall": "x0", "U": [0.3, 0, 0], "T": 293.15,
                  "_desc": "환경 테스트 기준 급기"},
        "outlet": {"wall": "xL", "_desc": "환경 테스트 기준 배기"},
        "heat": {"power_kw": 0.5, "_desc": "환경 테스트 기준 발열"},
        "init": {"T": 295.15},
        "endTime": 100,
    }
    act["step"] = "기준 케이스 생성"
    act["lines"].append("=== 64셀 환경 기준 케이스 생성 ===")
    cfd_export.build_case(cfg, case_dir)

    ok, err = _do_run(ACCEPTANCE_JOB, case_dir, act)
    details = {
        "case_dir": case_dir,
        "cells": 64,
        "openfoam_profile": str(
            OPENFOAM_CAPABILITIES.get("compatible_profile") or ""
        ),
        "openfoam_version": str(OPENFOAM_CAPABILITIES.get("version") or ""),
        "openfoam_distro": str(OPENFOAM_CAPABILITIES.get("distro") or ""),
    }
    if not ok:
        return False, err or "기준 케이스 계산에 실패했습니다.", details

    logs = glob.glob(os.path.join(case_dir, "log.*Foam"))
    reports = glob.glob(os.path.join(case_dir, "cfd_report_*.html"))
    times = []
    for name in os.listdir(case_dir):
        path = os.path.join(case_dir, name)
        try:
            value = float(name)
        except ValueError:
            continue
        if value > 0 and os.path.isdir(path):
            times.append(value)
    latest_time = max(times) if times else None
    mesh_ok = bool(act.get("mesh_ok"))
    details.update({
        "mesh_ok": mesh_ok,
        "latest_time": latest_time,
        "solver_log": logs[0] if logs else "",
        "report_path": reports[0] if reports else "",
    })
    missing = []
    if not mesh_ok:
        missing.append("Mesh OK 확인")
    if latest_time is None:
        missing.append("결과 time 폴더")
    if not logs:
        missing.append("solver 로그")
    if not reports:
        missing.append("HTML 보고서")
    if missing:
        return False, "수용 테스트 결과 누락: " + ", ".join(missing), details
    act["step"] = "수용 테스트 통과"
    act["lines"].append(
        f"=== PASS · Mesh OK · latest time {latest_time:g} · HTML 보고서 생성 ==="
    )
    return True, None, details


def _do_gridstudy(name, case_dir, act):
    """격자 독립성 검증: 케이스 셀 c 기준 [1.5c, c, c/1.5] 3케이스를 <case>/_grid/ 에
    실행(cfd_gridstudy.run_one 재사용) → GCI 를 케이스 meta['gci'] 에 병합.
    반환: 오류 문자열 또는 None."""
    import cfd_gridstudy
    meta_path = os.path.join(case_dir, "cfd_case_meta.json")
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    cfg = meta.get("config", {})
    c = float(cfg.get("mesh", {}).get("cell", 0.3))
    cells = [round(c * 1.5, 3), c, round(c / 1.5, 3)]   # 성긴 → 세밀
    gdir = os.path.join(case_dir, "_grid")
    os.makedirs(gdir, exist_ok=True)
    results = []
    for i, cell in enumerate(cells):
        act["step"] = f"격자검증 {i + 1}/3 · 셀 {cell} m"
        act["time"] = 0.0
        act["lines"].append(f"[격자 {i + 1}/3] 셀 {cell} m 실행...")
        token, busy = _claim_solver_slot()
        if token is None:
            return busy["error"]
        try:
            results.append(cfd_gridstudy.run_one(
                cfg, cell, os.path.join(gdir, f"c{i}"), cfg.get("endTime")
            ))
        finally:
            cfd_gci_job.release_solver_lock(ROOT, token)
    key = "T_max_C"
    vals = [r["metrics"].get(key) for r in results]
    gci = {"key": key, "cells": cells,
           "values": [round(v, 3) if isinstance(v, float) else v for v in vals],
           "ncells": [r["cells"] for r in results]}
    err = None
    if all(v is not None for v in vals):
        res = cfd_gridstudy.solve_order(vals[2], vals[1], vals[0],
                                        cells[1] / cells[2], cells[0] / cells[1])
        if res and res[0] == "비단조":
            gci["verdict"] = "비단조 — 격자 미독립(더 세밀 필요)"
        elif res:
            p, fext, g21 = res
            gci.update(p=round(p, 2), extrapolated=round(fext, 3), gci_pct=round(g21, 2),
                       verdict=("신뢰(≤5%)" if g21 <= 5 else "격자오차 큼(>5%)"))
        else:
            gci["verdict"] = "격자간 변화 0 — 완전 독립"
            gci["gci_pct"] = 0.0
    else:
        gci["verdict"] = "지표 누락(하위 실행 실패)"
        err = "격자검증: 일부 격자 실행 실패(케이스 _grid/ 로그 확인)"
    meta["gci"] = gci
    with open(meta_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    act["lines"].append(f"격자검증 완료: {gci.get('verdict')}"
                        + (f" GCI={gci.get('gci_pct')}%" if gci.get("gci_pct") is not None else ""))
    return err


def run_status():
    with RUN_LOCK:
        act = None
        if RUN["active"]:
            a = RUN["active"]
            act = {"name": a["name"], "step": a["step"], "time": a["time"],
                   "endTime": a["endTime"], "kind": a.get("kind"),
                   "lines": list(a["lines"])}
        queue = [
            (ACCEPTANCE_DISPLAY_NAME if q["kind"] == "acceptance" else
             q["name"] + (" (격자검증)" if q["kind"] == "grid" else ""))
            for q in RUN["queue"]
        ]
        history = {k: dict(v) for k, v in RUN["history"].items()
                   if k != ACCEPTANCE_JOB}
        mpi_smoke_state = (
            "running" if (RUN["active"] and RUN["active"].get("kind") == "mpi_smoke")
            else "queued" if any(row.get("kind") == "mpi_smoke" for row in RUN["queue"])
            else "idle"
        )
        return {"openfoam": bool(OPENFOAM_OK),
                "environment": dict(OPENFOAM_CAPABILITIES),
                "freecad": dict(FREECAD_CAPABILITIES),
                "acceptance": dict(ENVIRONMENT_ACCEPTANCE),
                "mpi_smoke": {"state": mpi_smoke_state},
                "active": act, "queue": queue, "history": history}


# ── 결과 필드 캐시 + 단면 슬라이스 (2D/3D 뷰어 공용 API) ─────────────────────
# 함수객체가 깨진 환경이므로(SHA1 버그) 최종 time 의 ascii 필드를 직접 파싱해
# (nz,ny,nx) 구조격자로 캐시하고, 요청된 절단면만 JSON 으로 반환한다(수 KB).

FIELD_CACHE = {}
FIELD_LOCK = threading.Lock()


def _load_fields(name):
    """케이스 결과 필드 → 구조격자 배열 캐시(mtime 무효화)."""
    d = safe_case_dir(name)
    if not d:
        return None
    tdir = cfd_report.find_latest_time(d)
    if not tdir or not os.path.exists(os.path.join(tdir, "T")):
        return None
    meta = cfd_report._load_meta(d)
    if not meta:
        return None
    mt = os.path.getmtime(os.path.join(tdir, "T"))
    with FIELD_LOCK:
        c = FIELD_CACHE.get(name)
        if c and c["mtime"] == mt:
            return c
    import numpy as np
    n = meta["mesh"]["cells"]
    T = cfd_report._as_array(cfd_report.read_field(os.path.join(tdir, "T")), n)
    if T is None:
        return None
    Tg, xc, yc, zc = cfd_report._cell_grid(T - 273.15, meta)
    smask = cfd_report.solid_mask(meta)          # V3a 실형상: (nz,ny,nx) bool 또는 None
    if smask is not None:
        Tm = np.ma.masked_array(Tg, mask=smask)
        tmin, tmax = float(Tm.min()), float(Tm.max())
    else:
        tmin, tmax = float(Tg.min()), float(Tg.max())
    entry = {"meta": meta, "mtime": mt, "T": Tg, "smask": smask,
             "xc": xc, "yc": yc, "zc": zc,
             "Tmin": tmin, "Tmax": tmax}
    U = cfd_report._as_array(cfd_report.read_field(os.path.join(tdir, "U")), n)
    if U is not None and getattr(U, "ndim", 1) == 2:
        nx, ny, nz = meta["mesh"]["nx"], meta["mesh"]["ny"], meta["mesh"]["nz"]
        Ug = U[:nx * ny * nz].reshape(nz, ny, nx, 3)
        entry["Ux"], entry["Uy"], entry["Uz"] = Ug[..., 0], Ug[..., 1], Ug[..., 2]
        Umag = np.linalg.norm(Ug, axis=3)
        entry["Umag"] = Umag
        entry["Umax"] = float(Umag.max())
    with FIELD_LOCK:
        FIELD_CACHE[name] = entry
    return entry


def _model_payload(meta):
    """실행 전·후 공용 3D 모델 정보. 결과 필드가 없어도 생성된 CFD 형상을 보여준다."""
    cfg = meta.get("config", {})
    room = cfg.get("room", {})
    roles = meta.get("roles", {})
    out = {
        "name": cfg.get("name"),
        "room": room,
        "inlet": next((k for k, v in roles.items() if v == "inlet"), None),
        "outlet": next((k for k, v in roles.items() if v == "outlet"), None),
        "openings": [],
    }
    if meta.get("model_quality"):
        out["model_quality"] = meta["model_quality"]
    if meta.get("opening_preflight"):
        out["opening_preflight"] = meta["opening_preflight"]
    # 급배기구 모드는 roles 가 모두 wall 이므로 실제 패치 사각형을 별도로 전달한다.
    for p in (meta.get("patches") or []):
        rect = p.get("rect_snap")
        wall = p.get("wall")
        if not rect or len(rect) != 4 or wall not in ("x0", "xL", "y0", "yW", "floor", "ceiling"):
            continue
        out["openings"].append({
            "name": p.get("name"), "role": p.get("role"), "type": p.get("type"),
            "wall": wall, "rect": rect, "cmh": p.get("cmh"),
            "parent_name": p.get("parent_name"), "opening_id": p.get("opening_id"),
            "design_cmh": p.get("design_cmh"), "flow_control": p.get("flow_control"),
        })

    # V3a 실형상 윤곽(뷰어 오버레이용): 방 폴리곤 + 장애물 footprint/높이
    outlines = {}
    if cfg.get("room_polygon"):
        outlines["room"] = cfg["room_polygon"]
    obs = []
    for o in (cfg.get("obstacles") or []):
        if o.get("footprint"):
            poly = o["footprint"]
            xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
            bb = [min(xs), min(ys), max(xs), max(ys)]
        else:
            bb = o["bbox"]
            poly = [[bb[0], bb[1]], [bb[2], bb[1]], [bb[2], bb[3]], [bb[0], bb[3]]]
        H = room.get("H", 3.0)
        obs.append({"kind": o.get("kind", "equipment"), "poly": poly, "bbox": bb,
                    "h": float(o.get("h", H if o.get("kind") == "column" else 2.0)),
                    "kw": o.get("kw")})
    if obs:
        outlines["obstacles"] = obs
    if outlines:
        out["outlines"] = outlines
    return out


def model_info(name):
    """케이스 생성 직후부터 사용할 수 있는 3D 계산 모델 정보."""
    d = safe_case_dir(name)
    if not d:
        return {"error": "케이스 없음"}
    meta = cfd_report._load_meta(d)
    if not meta:
        return {"error": "계산 모델 정보가 없습니다"}
    out = _model_payload(meta)
    try:
        verification = cfd_report._load_opening_boundary_verification(d)
    except Exception:
        verification = None
    if verification:
        out["opening_verification"] = verification
    mesh = meta.get("mesh", {})
    out["mesh"] = {k: mesh.get(k) for k in ("nx", "ny", "nz", "cells")}
    return out


def field_info(name):
    e = _load_fields(name)
    if not e:
        return {"error": "결과 필드 없음 — 아직 실행 전이거나 회수 실패"}
    m = e["meta"]["mesh"]
    out = _model_payload(e["meta"])
    out.update({"nx": m["nx"], "ny": m["ny"], "nz": m["nz"],
                "Tmin": round(e["Tmin"], 2), "Tmax": round(e["Tmax"], 2),
                "Umax": round(e.get("Umax", 0.0), 3), "hasU": "Umag" in e})
    return out


def field_slice(name, field, axis, idx, want_vec):
    """절단면 1장: {hx,hy,w,h,pos,data[[..]],(vx,vy)}. data 는 화면행=hy축."""
    e = _load_fields(name)
    if not e:
        return {"error": "결과 필드 없음"}
    key = "Umag" if field == "U" else "T"
    if key not in e:
        return {"error": f"{field} 필드 없음"}
    g = e[key]
    nz, ny, nx = g.shape
    room = e["meta"]["config"].get("room", {})
    L, W, H = room.get("L", 1), room.get("W", 1), room.get("H", 1)
    axis = axis if axis in ("x", "y", "z") else "z"
    lim = {"z": nz, "y": ny, "x": nx}[axis]
    idx = max(0, min(int(idx), lim - 1))
    has_u = "Ux" in e
    sm = e.get("smask")
    mslice = None
    if axis == "z":
        data = g[idx]                       # (ny, nx) — 행=y, 열=x
        pos, hx, hy, w, h = e["zc"][idx], "x", "y", L, W
        vec = (e["Ux"][idx], e["Uy"][idx]) if has_u else None
        if sm is not None:
            mslice = sm[idx]
    elif axis == "y":
        data = g[:, idx, :]                 # (nz, nx) — 행=z, 열=x
        pos, hx, hy, w, h = e["yc"][idx], "x", "z", L, H
        vec = (e["Ux"][:, idx, :], e["Uz"][:, idx, :]) if has_u else None
        if sm is not None:
            mslice = sm[:, idx, :]
    else:
        data = g[:, :, idx]                 # (nz, ny) — 행=z, 열=y
        pos, hx, hy, w, h = e["xc"][idx], "y", "z", W, H
        vec = (e["Uy"][:, :, idx], e["Uz"][:, :, idx]) if has_u else None
        if sm is not None:
            mslice = sm[:, :, idx]
    out = {"axis": axis, "idx": idx, "pos": round(float(pos), 3),
           "hx": hx, "hy": hy, "w": w, "h": h,
           "data": [[round(float(v), 3) for v in row] for row in data]}
    if mslice is not None:
        out["mask"] = [[1 if v else 0 for v in row] for row in mslice]
    if want_vec and vec is not None:
        out["vx"] = [[round(float(v), 4) for v in row] for row in vec[0]]
        out["vy"] = [[round(float(v), 4) for v in row] for row in vec[1]]
    return out


# ── 마법사 백엔드: 도면 미리보기 · 케이스 생성 · 삭제 ────────────────────────

def inspect_geometry(path, zone=None, bbox=None):
    """geometry.json 미리보기: zone 목록·전체범위·(zone/bbox 선택 시) 개구부 벽 힌트."""
    path = os.path.abspath(os.path.expanduser(path or ""))
    if not os.path.isfile(path):
        return {"error": f"파일 없음: {path}"}
    try:
        with open(path, encoding="utf-8") as f:
            geom = json.load(f)
    except Exception as e:
        return {"error": f"geometry.json 파싱 실패: {e}"}
    geom = migrate_geometry(geom, source_path=geom.get("source") or path)
    el = geom.get("elements", {})
    zones = []
    for i, z in enumerate(el.get("zone", [])):
        ext = cfd_export._xy_extent([z])
        if ext:
            zones.append({"i": i, "L": round((ext[2] - ext[0]) / 1000, 2),
                          "W": round((ext[3] - ext[1]) / 1000, 2)})
    out = {"zones": zones,
           "schema_version": geom.get("schema_version"),
           "contract": geom.get("contract"),
           "openings": len(el.get("opening", [])),
           "equipment": len(el.get("equipment", [])),
           "walls": len(el.get("wall", [])),
           "height_m": round(geom.get("params", {}).get("wall", {}).get("height", 2800.0) / 1000.0, 2),
           "height_confirmed": any(
               bool(zone.get("confirmed"))
               and (zone.get("semantic") or {}).get("height_source") == "user_confirmed"
               for zone in el.get("zone", [])
           ),
           "parser_warnings": list(geom.get("warnings") or []),
           "body_fitted_blocked": bool(geom.get("review", {}).get("blocking")),
           "review_blocker_count": int(geom.get("review", {}).get("blocker_count", 0)),
           "review_warning_count": int(geom.get("review", {}).get("warning_count", 0)),
           "review_items": list(geom.get("review", {}).get("items") or []),
           "source_dxf": geom.get("source"),
           "source_units": dict(geom.get("source_units") or {}),
           "unit_detection": dict(geom.get("unit_detection") or {}),
           "unit_review": dict(geom.get("unit_review") or {}),
           "zone_candidates": list(geom.get("zone_candidates") or [])}
    ext_all = cfd_export._xy_extent(el.get("wall", []))
    if ext_all:
        out["wall_extent_mm"] = [round(v, 1) for v in ext_all]
    if zone is not None or bbox is not None:
        try:
            cfg, info = cfd_export.cfg_from_geometry(geom, zone=zone, bbox=bbox,
                                                     height=out["height_m"])
            out["room"] = cfg["room"]
            out["openings_by_wall"] = info["openings_by_wall"]
            out["warnings"] = info["warnings"]
            out["diffusers"] = cfd_export.diffusers_from_geometry(geom, zone=zone, bbox=bbox)
            shape = cfd_export.obstacles_from_geometry(geom, zone=zone, bbox=bbox)
            out["room_polygon"] = shape["room_polygon"]
            out["obstacles"] = shape["obstacles"]
        except SystemExit as e:
            out["error"] = str(e)
    return out


def _safe_upload_stem(filename):
    """브라우저가 보낸 파일명에서 경로·제어문자를 제거한 짧은 저장용 stem."""
    raw = os.path.basename((filename or "").replace("\\", "/"))
    stem, ext = os.path.splitext(raw)
    stem = re.sub(r"[^\w가-힣.\- ]+", "_", stem).strip(" ._-")
    return (stem[:80] or "drawing"), ext.lower()


def _parse_uploaded_dxf(dxf_path, unit_override="auto"):
    """업로드 DXF를 프로젝트 매핑 규칙으로 geometry.json 변환하고 미리보기한다."""
    import dxf_parser as parser

    def mapping(name, fallback):
        # --root에 프로젝트 전용 매핑을 둔 경우 우선하고, 없으면 프로그램 기본 매핑 사용.
        for base in (ROOT, HERE):
            p = os.path.join(base, name)
            if os.path.isfile(p):
                return parser.load_layer_map(p), p
        return fallback, None

    rules, layer_path = mapping("layer_map.csv", parser.DEFAULT_LAYER_RULES)
    block_rules, block_path = mapping("block_map.csv", parser.DEFAULT_BLOCK_RULES)
    data = parser.parse(dxf_path, rules, block_rules, unit_override=unit_override)
    geom_path = os.path.splitext(dxf_path)[0] + ".geometry.json"
    temporary = geom_path + f".{uuid.uuid4().hex}.part"
    with open(temporary, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temporary, geom_path)
    return {
        "ok": True,
        "dxf": dxf_path,
        "geometry": geom_path,
        "inspect": inspect_geometry(geom_path),
        "maps": {"layer": layer_path, "block": block_path},
    }


def build_occ_geometry(geometry_path):
    """Build one validated OCC air volume under the project-local output root."""
    geometry_path = os.path.abspath(os.path.expanduser(geometry_path or ""))
    if not os.path.isfile(geometry_path):
        return {"ok": False, "error": f"geometry.json 파일이 없습니다: {geometry_path}"}
    stem = re.sub(r"[^\w가-힣.\- ]+", "_", os.path.splitext(os.path.basename(geometry_path))[0])
    path_key = hashlib.sha256(os.path.normcase(geometry_path).encode("utf-8")).hexdigest()[:10]
    output_dir = os.path.join(ROOT, "_occ_geometry", f"{stem[:60]}-{path_key}")
    result = cfd_occ.run_occ_job(geometry_path, output_dir)
    if not result.get("ok"):
        return result
    manifest = result.get("manifest") or {}
    air = manifest.get("air_volume") or {}
    return {
        "ok": True,
        "output": result.get("output"),
        "manifest_path": result.get("manifest_path"),
        "volume_m3": air.get("volume_m3"),
        "region_count": len(manifest.get("regions") or []),
        "location_in_mesh": air.get("location_in_mesh"),
        "topology": manifest.get("topology"),
        "surface_hash": manifest.get("surface_hash"),
    }


def build_body_fitted_mesh(geometry_path, settings=None):
    """Build OCC geometry, generate P3A case, run Allmesh and return its gate."""
    try:
        resolved_settings = cfd_mesh.resolve_settings(settings)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    occ = build_occ_geometry(geometry_path)
    if not occ.get("ok"):
        return occ
    preset = resolved_settings["preset"]
    mesh_dir = os.path.join(
        ROOT, "_body_mesh", os.path.basename(occ["output"]) + "-" + preset
    )
    built = cfd_mesh.build_mesh_case(
        occ["output"], mesh_dir, settings=resolved_settings
    )
    if not built.get("ok"):
        return built
    token, busy = _claim_solver_slot()
    if token is None:
        return busy
    try:
        result = cfd_mesh.run_mesh_case(mesh_dir)
    finally:
        cfd_gci_job.release_solver_lock(ROOT, token)
    result["estimate"] = built.get("estimate")
    result["preset"] = preset
    return result


def run_body_fitted_isothermal(mesh_case_path, settings=None):
    """Run P4 only from a project-local, previously accepted body-fitted mesh."""
    allowed = Path(ROOT, "_body_mesh").resolve()
    try:
        mesh_case = Path(mesh_case_path or "").expanduser().resolve()
        mesh_case.relative_to(allowed)
    except (OSError, ValueError):
        return {"ok": False, "error": "프로젝트의 검증된 상세 메시만 계산할 수 있습니다."}
    try:
        mesh_manifest = json.loads(
            (mesh_case / "mesh_manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return {"ok": False, "error": "메시 품질 결과를 읽지 못했습니다."}
    if mesh_manifest.get("status") != "PASS" or mesh_manifest.get("profile") != "detailed":
        return {"ok": False, "error": "PASS 상태의 안정 상세 메시가 필요합니다."}
    solver_dir = Path(ROOT, "_body_solver", mesh_case.name + "-isothermal")
    built = cfd_physics.build_isothermal_case(mesh_case, solver_dir, settings=settings)
    if not built.get("ok"):
        return built
    token, busy = _claim_solver_slot()
    if token is None:
        return busy
    try:
        result = cfd_physics.run_isothermal_case(solver_dir)
    finally:
        cfd_gci_job.release_solver_lock(ROOT, token)
    result["physics_input"] = built.get("physics_input")
    return result


def run_body_fitted_thermal(mesh_case_path, initial_case_path, settings=None):
    """Run the validated 0.05 s detailed thermal-buoyancy screening gate."""
    runtime = diagnose_openfoam()
    if not runtime.get("thermal_detailed_ready"):
        return {
            "ok": False,
            "error": "상세 열·부력 계산에는 검증된 OpenFOAM v2606 프로필이 필요합니다.",
        }
    mesh_root = Path(ROOT, "_body_mesh").resolve()
    solver_root = Path(ROOT, "_body_solver").resolve()
    try:
        mesh_case = Path(mesh_case_path or "").expanduser().resolve()
        initial_case = Path(initial_case_path or "").expanduser().resolve()
        mesh_case.relative_to(mesh_root)
        initial_case.relative_to(solver_root)
    except (OSError, ValueError):
        return {"ok": False, "error": "프로젝트의 상세 메시와 등온 결과만 사용할 수 있습니다."}
    try:
        mesh_manifest = json.loads(
            (mesh_case / "mesh_manifest.json").read_text(encoding="utf-8")
        )
        initial_manifest = json.loads(
            (initial_case / "run_manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return {"ok": False, "error": "상세 메시 또는 등온 결과 manifest를 읽지 못했습니다."}
    if (mesh_manifest.get("status") != "PASS"
            or mesh_manifest.get("profile") != "detailed"):
        return {"ok": False, "error": "PASS 상태의 안정 상세 메시가 필요합니다."}
    if (initial_manifest.get("status") == "FAIL"
            or initial_manifest.get("engine") not in (
                "body_fitted_isothermal_rans", "body_fitted_isothermal_urans"
            )):
        return {"ok": False, "error": "먼저 등온 유동 계산을 완료해야 합니다."}

    thermal_settings = {
        "thermal_duration_s": 0.05,
        "thermal_initial_delta_t_s": 0.0001,
        "thermal_max_delta_t_s": 0.0005,
        "thermal_write_interval_s": 0.01,
    }
    if settings:
        thermal_settings.update(settings)
    solver_dir = solver_root / (mesh_case.name + "-thermal-screening")
    built = cfd_physics.build_buoyant_case(
        mesh_case, solver_dir, settings=thermal_settings,
        initial_case_dir=initial_case,
    )
    if not built.get("ok"):
        return built
    token, busy = _claim_solver_slot()
    if token is None:
        return busy
    try:
        result = cfd_physics.run_buoyant_case(solver_dir)
    finally:
        cfd_gci_job.release_solver_lock(ROOT, token)
    result["thermal_input"] = built.get("thermal_input")
    result["screening_only"] = True
    report = cfd_report.generate_body_fitted_report(
        solver_dir, projects_root=ROOT
    )
    if report.get("ok"):
        result["report_url"] = "/body-report/" + quote(solver_dir.name)
    if (solver_dir / "result_manifest.json").is_file():
        result["results_url"] = "/body-results/" + quote(solver_dir.name)
    return result


def continue_body_fitted_thermal(solver_case_path, settings=None):
    """Continue a project-local thermal screening case from its latest time."""
    runtime = diagnose_openfoam()
    if not runtime.get("thermal_detailed_ready"):
        return {
            "ok": False,
            "error": "열·부력 이어 계산에는 검증된 OpenFOAM v2606 프로필이 필요합니다.",
        }
    allowed = Path(ROOT, "_body_solver").resolve()
    try:
        solver_case = Path(solver_case_path or "").expanduser().resolve()
        solver_case.relative_to(allowed)
    except (OSError, ValueError):
        return {"ok": False, "error": "프로젝트의 열·부력 결과만 이어 계산할 수 있습니다."}
    if not (solver_case / "thermal_input.json").is_file():
        return {"ok": False, "error": "열·부력 입력 manifest가 없습니다."}
    token, busy = _claim_solver_slot()
    if token is None:
        return busy
    try:
        result = cfd_physics.run_buoyant_continuation(
            solver_case, settings=settings
        )
    finally:
        cfd_gci_job.release_solver_lock(ROOT, token)
    report = cfd_report.generate_body_fitted_report(
        solver_case, projects_root=ROOT
    )
    if report.get("ok"):
        result["report_url"] = "/body-report/" + quote(solver_case.name)
    if (solver_case / "result_manifest.json").is_file():
        result["results_url"] = "/body-results/" + quote(solver_case.name)
    return result


def run_body_fitted_transient(solver_case_path, settings=None):
    """Continue a project-local steady WARN case with bounded pimpleFoam."""
    allowed = Path(ROOT, "_body_solver").resolve()
    try:
        solver_case = Path(solver_case_path or "").expanduser().resolve()
        solver_case.relative_to(allowed)
    except (OSError, ValueError):
        return {"ok": False, "error": "프로젝트의 정상상태 계산 결과만 이어서 진단할 수 있습니다."}
    try:
        manifest = json.loads(
            (solver_case / "run_manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return {"ok": False, "error": "정상상태 계산 결과를 읽지 못했습니다."}
    if (manifest.get("status") == "FAIL"
            or ("ITERATION_LIMIT" not in (manifest.get("warnings") or [])
                and manifest.get("engine") != "body_fitted_isothermal_urans")):
        return {"ok": False, "error": "정상상태 수렴 미달 결과에서만 시간변동 진단을 시작합니다."}
    token, busy = _claim_solver_slot()
    if token is None:
        return busy
    try:
        return cfd_physics.run_transient_diagnostic(solver_case, settings=settings)
    finally:
        cfd_gci_job.release_solver_lock(ROOT, token)


_INFLOW = {"x0": (1, 0, 0), "xL": (-1, 0, 0), "y0": (0, 1, 0), "yW": (0, -1, 0)}


def create_case(p):
    """마법사 폼(JSON) → 케이스 생성. 반환 {ok, dir} 또는 {error}."""
    name = (p.get("name") or "").strip()
    if not name or not _SAFE_NAME.match(name):
        return {"error": "케이스명은 한글/영문/숫자/공백/._- 만 가능"}
    out_dir = os.path.join(ROOT, name)
    if os.path.exists(out_dir):
        return {"error": f"이미 존재하는 케이스: {name} (다른 이름 또는 기존 삭제 후)"}
    try:
        supply = p.get("supply", "x0")
        exhaust = p.get("exhaust", "xL")
        vent_openings = p.get("openings") or None   # v2: 급배기구 목록(디퓨저/그릴)
        if not vent_openings and supply == exhaust:
            return {"error": "급기 벽과 배기 벽이 같습니다"}
        supply_u = float(p.get("supply_u", 0.3))
        supply_T = float(p.get("supply_T_C", 20.0)) + 273.15
        power_kw = p.get("power_kw")
        power_kw = float(power_kw) if power_kw not in (None, "") else None
        cell = float(p.get("cell", 0.3))
        endtime = int(p.get("endtime", 400))
        if supply_u <= 0:
            return {"error": "급기 유속은 0보다 커야 합니다"}
        if power_kw is not None and power_kw < 0:
            return {"error": "발열량은 0 이상이어야 합니다"}
        if cell <= 0:
            return {"error": "격자 셀 크기는 0보다 커야 합니다"}
        if endtime < 1:
            return {"error": "최대 반복은 1 이상이어야 합니다"}
        info = None
        if p.get("mode") == "geometry":
            with open(os.path.expanduser(p.get("geometry") or ""), encoding="utf-8") as f:
                geom = json.load(f)
            if ((geom.get("unit_review") or {}).get("required")
                    and p.get("unit_confirmed") is not True):
                return {"error": "DXF 헤더와 실제 좌표 단위가 다릅니다. 화면에서 mm 단위를 먼저 확인해 주세요."}
            zone = p.get("zone")
            zone = int(zone) if zone not in (None, "") else None
            bbox = p.get("bbox") or None
            if isinstance(bbox, str) and bbox.strip():
                bbox = [float(x) for x in bbox.split(",")]
                if len(bbox) != 4:
                    return {"error": "bbox는 x0,y0,x1,y1 네 숫자로 입력하세요"}
            elif not bbox:
                bbox = None
            height = p.get("height")
            height = float(height) if height not in (None, "") else None
            if height is not None and height <= 0:
                return {"error": "층고는 0보다 커야 합니다"}
            cfg, info = cfd_export.cfg_from_geometry(
                geom, zone=zone, bbox=bbox, height=height, cell=cell,
                supply=supply, exhaust=exhaust, supply_u=supply_u, supply_T=supply_T,
                power_kw=power_kw, endTime=endtime, name=name)
        else:
            L, W, H = float(p["L"]), float(p["W"]), float(p["H"])
            if min(L, W, H) <= 0:
                return {"error": "방의 길이·폭·높이는 모두 0보다 커야 합니다"}
            d = _INFLOW.get(supply, (1, 0, 0))
            cfg = {
                "name": name,
                "_note": "스튜디오 직접 입력 · 급배기·발열=가정값(리포트 명시)",
                "room": {"L": L, "W": W, "H": H},
                "mesh": {"cell": cell},
                "g": [0, 0, -9.81],
                "inlet": {"wall": supply,
                          "U": [supply_u * d[0], supply_u * d[1], supply_u * d[2]],
                          "T": supply_T, "_desc": f"급기(가정) — {supply} 벽"},
                "outlet": {"wall": exhaust, "_desc": f"배기(가정) — {exhaust} 벽"},
                "heat": ({"power_kw": power_kw,
                          "_desc": f"장비 총발열(가정) {power_kw} kW = 바닥층 체적발열원"}
                         if power_kw is not None else
                         {"wall": "floor", "floor_T": 313.0,
                          "_desc": "발열 바닥(가정) = 장비 총발열 단순화"}),
                "init": {"T": 300},
                "endTime": endtime,
            }
        if vent_openings:
            # 급배기구 모드: 벽 전체 inlet/outlet 제거, openings 로 대체
            cfg["openings"] = []
            for o in vent_openings:
                row = {"role": o.get("role", "supply"), "type": o.get("type", "grille"),
                       "wall": o.get("wall", "ceiling"),
                       "cx": float(o["cx"]), "cy": float(o["cy"]),
                       "w": float(o["w"]), "h": float(o["h"])}
                if o.get("cmh") not in (None, ""):
                    # Exhaust CMH is retained as a design target.  The
                    # generated pressure outlet does not impose it; the
                    # post-run phi check is the actual-flow evidence.
                    row["cmh"] = float(o["cmh"])
                if o.get("opening_id") not in (None, ""):
                    row["opening_id"] = str(o["opening_id"])
                if o.get("source_id") not in (None, ""):
                    row["source_id"] = str(o["source_id"])
                if o.get("source_label") not in (None, ""):
                    row["source_label"] = str(o["source_label"])
                if o.get("source_type") not in (None, ""):
                    row["source_type"] = str(o["source_type"])
                if isinstance(o.get("source_ref"), dict):
                    row["source_ref"] = dict(o["source_ref"])
                if o.get("override_of_dxf") is True:
                    row["override_of_dxf"] = True
                if row["role"] == "supply":
                    row["cmh"] = float(o["cmh"])
                    row["T"] = float(o.get("T_C", p.get("supply_T_C", 20.0))) + 273.15
                cfg["openings"].append(row)
            cfg["inlet"] = {"T": supply_T}    # Tref(폐합 기준)만 유지
            cfg.pop("outlet", None)
            if cfg.get("heat", {}).get("floor_T") is not None:
                cfg["heat"] = ({"power_kw": power_kw} if power_kw is not None else {})
            # V3a 실형상: 방 폴리곤 + 장애물(기둥·장비, 장비별 kw)
            if p.get("room_polygon"):
                cfg["room_polygon"] = [[float(q[0]), float(q[1])] for q in p["room_polygon"]]
            obs_rows = p.get("obstacles") or []
            if obs_rows:
                cfg["obstacles"] = []
                seen_dxf_obstacle_ids = set()
                for o in obs_rows:
                    row = {"kind": o.get("kind", "equipment"),
                           "bbox": [float(o["x0"]), float(o["y0"]),
                                    float(o["x1"]), float(o["y1"])]}
                    if o.get("h") not in (None, ""):
                        row["h"] = float(o["h"])
                    if o.get("kw") not in (None, ""):
                        row["kw"] = float(o["kw"])
                    if o.get("convective_fraction") not in (None, ""):
                        row["convective_fraction"] = float(
                            o["convective_fraction"]
                        )
                    source_type = str(o.get("source_type") or "").strip()
                    source_id = str(o.get("source_id") or "").strip()
                    positive_heat_input = float(row.get("kw") or 0.0) > 0.0
                    if source_type.lower() == "dxf_detected":
                        if source_id and source_id in seen_dxf_obstacle_ids:
                            return {"error": f"DXF 장애물 source_id '{source_id}'가 중복되었습니다."}
                        if source_id:
                            seen_dxf_obstacle_ids.add(source_id)
                        if positive_heat_input:
                            return {
                                "error": "DXF에서 검출한 장비는 kW·대류비·근거를 검토해 사용자 확인 열원으로 전환한 뒤 사용하세요."
                            }
                    if (positive_heat_input
                            and source_type.lower() in ("", "legacy_manual_input")
                            and cfd_export._legacy_obstacle_cad_identity_path(o)):
                        return {
                            "error": "CAD/DXF 식별자가 있는 장비는 source_type 없이 "
                                     "legacy 수동 열원으로 사용할 수 없습니다. 검토 후 "
                                     "user_confirmed로 지정하세요."
                        }
                    for key in ("source_id", "source_label", "evidence", "source_type"):
                        if o.get(key) not in (None, ""):
                            row[key] = str(o[key])
                    if isinstance(o.get("source_ref"), dict):
                        row["source_ref"] = dict(o["source_ref"])
                    if o.get("override_of_dxf") is True:
                        row["override_of_dxf"] = True
                    if float(row.get("kw") or 0.0) > 0:
                        try:
                            fraction = float(row.get("convective_fraction"))
                        except (TypeError, ValueError):
                            return {
                                "error": "발열 장비마다 대류비(0~1)를 확인해 주세요."
                            }
                        if not 0 < fraction <= 1:
                            return {
                                "error": "발열 장비의 대류비는 0보다 크고 1 이하여야 합니다."
                            }
                        if not str(row.get("evidence") or "").strip():
                            return {
                                "error": "발열 장비마다 kW 근거를 입력해 주세요."
                            }
                    cfg["obstacles"].append(row)
                obs_kw = sum(r.get("kw") or 0 for r in cfg["obstacles"])
                if obs_kw > 0:
                    if power_kw is not None:
                        return {"error": "발열은 장애물별 kw 또는 총발열 kW 중 하나만 입력하세요"}
                    cfg["heat"] = {}
        cfd_export.build_case(cfg, out_dir)
        if info:
            meta_path = os.path.join(out_dir, "cfd_case_meta.json")
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            meta["from_geometry"] = {k: v for k, v in info.items() if k != "src_polygon"}
            with open(meta_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        return {"ok": True, "dir": name}
    except SystemExit as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def delete_case(name):
    d = safe_case_dir(name)
    if not d:
        return {"error": "케이스 없음"}
    with RUN_LOCK:
        if RUN["active"] and RUN["active"]["name"] == name:
            return {"error": "실행 중인 케이스 — 완료 후 삭제하세요"}
        RUN["queue"] = [q for q in RUN["queue"] if q.get("name") != name]
    shutil.rmtree(d)
    return {"ok": True}


# ── HTTP 핸들러 ───────────────────────────────────────────────────────────────

_CTYPES = {".html": "text/html; charset=utf-8", ".png": "image/png",
           ".json": "application/json; charset=utf-8", ".js": "text/javascript; charset=utf-8",
           ".csv": "text/csv; charset=utf-8"}


def _local_post_allowed(host, origin=None, fetch_site=None):
    """Reject cross-site POSTs to the loopback-only project server.

    Binding to 127.0.0.1 prevents remote TCP access, but without an Origin/
    Sec-Fetch-Site check an unrelated website open in the user's browser could
    still submit a delete/run request to localhost (classic local-service CSRF).
    Origin-less CLI/smoke requests remain available when Host is local.
    """
    host = (host or "").strip().lower()
    if not re.fullmatch(r"(?:127\.0\.0\.1|localhost)(?::\d{1,5})?", host):
        return False
    if (fetch_site or "").strip().lower() in ("cross-site", "same-site"):
        return False
    if origin:
        return origin.strip().rstrip("/").lower() == f"http://{host}"
    return True


class StudioHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):   # 콘솔 도배 방지
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False),
                   "application/json; charset=utf-8")

    def do_GET(self):
        try:
            self._route_get()
        except (ConnectionAbortedError, BrokenPipeError):
            pass
        except Exception as e:
            try:
                self._json({"error": str(e)}, 500)
            except Exception:
                pass

    def _route_get(self):
        u = urlparse(self.path)
        path = unquote(u.path)
        if path == "/":
            return self._send(200, PAGE_DASH)
        if path == "/new":
            return self._send(200, PAGE_NEW)
        if path == "/field-run":
            return self._send(200, PAGE_FIELD_RUN)
        if path == "/body-gci":
            return self._send(200, PAGE_BODY_GCI)
        if path == "/release-readiness":
            return self._send(200, PAGE_RELEASE_READINESS)
        if path == "/uat-session":
            return self._send(200, PAGE_UAT_SESSION)
        m = re.match(r"^/body-results/([^/]+)$", path)
        if m:
            case = _body_solver_case(m.group(1))
            if case is None:
                return self._send(404, "<meta charset='utf-8'>상세 결과가 없습니다.")
            page = PAGE_BODY_RESULTS.replace(
                "__CASE_JSON__", json.dumps(case.name, ensure_ascii=False).replace("</", "<\\/")
            )
            return self._send(200, page)
        if path == "/api/cases":
            return self._json(scan_cases())
        if path == "/api/body-gci-cases":
            return self._json(scan_body_gci_cases())
        if path == "/api/body-gci-geometries":
            return self._json(body_gci_geometry_candidates())
        if path == "/api/body-gci-jobs":
            return self._json(body_gci_jobs_payload())
        if path == "/api/field-pipeline-jobs":
            return self._json(field_pipeline_jobs_payload())
        if path == "/api/release-readiness":
            return self._json(release_readiness_payload())
        if path == "/api/field-evidence-candidates":
            return self._json(field_evidence_candidates())
        if path == "/api/uat/field-evidence":
            return self._json(uat_field_evidence_candidates())
        if path == "/api/uat/session":
            result = uat_session_status(parse_qs(u.query).get("token", [""])[0])
            return self._json(result, 200 if result.get("ok") else 404)
        m = re.match(r"^/api/body-results/([^/]+)$", path)
        if m:
            result = body_result_payload(m.group(1))
            return self._json(result, 200 if result.get("ok") else 404)
        m = re.match(r"^/api/case-health/([^/]+)$", path)
        if m:
            case_name = m.group(1)
            case = _body_solver_case(case_name)
            if case is None:
                return self._json({
                    "ok": False,
                    "code": "CASE_EVIDENCE_NOT_FOUND",
                    "case": case_name,
                }, 404)
            evidence_path = case / "case_evidence.v1.json"
            if not evidence_path.is_file():
                return self._json({
                    "ok": False,
                    "code": "CASE_EVIDENCE_NOT_FOUND",
                    "case": case.name,
                }, 404)
            health = cfd_case_health.build_case_health(
                evidence_path, projects_root=Path(ROOT)
            )
            return self._json(health)
        m = re.match(r"^/api/field-design-status/([^/]+)$", path)
        if m:
            result = field_design_status_payload(m.group(1))
            return self._json(result, 200 if result.get("ok") else 404)
        if path == "/api/status":
            return self._json(run_status())
        m = re.match(r"^/api/modelinfo/([^/]+)$", path)
        if m:
            return self._json(model_info(m.group(1)))
        m = re.match(r"^/api/fieldinfo/([^/]+)$", path)
        if m:
            return self._json(field_info(m.group(1)))
        m = re.match(r"^/api/slice/([^/]+)$", path)
        if m:
            q = parse_qs(u.query)
            return self._json(field_slice(
                m.group(1),
                q.get("field", ["T"])[0],
                q.get("axis", ["z"])[0],
                q.get("idx", ["0"])[0],
                q.get("vec", ["0"])[0] == "1"))
        m = re.match(r"^/case/([^/]+)/report$", path)
        if m:
            return self._serve_report(m.group(1))
        m = re.match(r"^/body-report/([^/]+)$", path)
        if m:
            return self._serve_body_report(m.group(1))
        m = re.match(r"^/body-gci-report/([^/]+)$", path)
        if m:
            return self._serve_body_gci_report(m.group(1))
        m = re.match(r"^/case/([^/]+)/file/([^/]+)$", path)
        if m:
            return self._serve_file(m.group(1), m.group(2))
        m = re.match(r"^/vendor/([\w.\-]+)$", path)
        if m:
            full = os.path.join(HERE, "vendor", m.group(1))
            if os.path.isfile(full):
                with open(full, "rb") as f:
                    return self._send(200, f.read(), "text/javascript; charset=utf-8")
            return self._send(404, "not found")
        self._send(404, "not found")

    def do_POST(self):
        try:
            if not _local_post_allowed(self.headers.get("Host"),
                                       self.headers.get("Origin"),
                                       self.headers.get("Sec-Fetch-Site")):
                return self._json({"error": "다른 웹사이트에서 보낸 요청은 허용하지 않습니다."}, 403)
            u = urlparse(self.path)
            path = unquote(u.path)
            if path == "/api/import-dxf":
                return self._handle_import_dxf(u)
            if path == "/api/case-review":
                try:
                    ln = int(self.headers.get("Content-Length") or 0)
                    body = self.rfile.read(ln).decode("utf-8") if ln else "{}"
                    p = json.loads(body or "{}")
                except (TypeError, ValueError, UnicodeError, json.JSONDecodeError):
                    return self._json({
                        "ok": False, "code": "INVALID_REQUEST_BODY"
                    }, 400)
                return self._handle_case_review(p)
            ln = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(ln).decode("utf-8") if ln else "{}"
            p = json.loads(body or "{}")
            if path == "/api/environment/refresh":
                return self._json(refresh_environment_capabilities())
            if path == "/api/environment/acceptance":
                err = enqueue_environment_acceptance()
                return self._json({"error": err} if err else {"ok": True})
            if path == "/api/environment/mpi-smoke":
                err = enqueue_mpi_runtime_smoke()
                return self._json({"error": err} if err else {"ok": True})
            if path == "/api/register-field-evidence":
                result = record_field_evidence(
                    p.get("case", ""), p.get("actual_site_drawing") is True
                )
                return self._json(result, 200 if result.get("ok") else 400)
            if path == "/api/uat/start":
                result = start_uat_session(
                    p.get("participant_id"), p.get("observed_by"),
                    p.get("field_evidence_id"),
                )
                return self._json(result, 200 if result.get("ok") else 400)
            if path == "/api/uat/task":
                result = record_uat_task(
                    p.get("token"), p.get("status"),
                    p.get("assistance_count", 0), p.get("notes", ""),
                    p.get("task", ""),
                )
                return self._json(result, 200 if result.get("ok") else 400)
            if path == "/api/uat/finish":
                result = finish_uat_session(
                    p.get("token"), p.get("critical_incidents") or [],
                )
                return self._json(result, 200 if result.get("ok") else 400)
            if path == "/api/uat/cancel":
                result = cancel_uat_session(p.get("token"))
                return self._json(result, 200 if result.get("ok") else 400)
            if path == "/api/inspect":
                zone = p.get("zone")
                zone = int(zone) if zone not in (None, "") else None
                bbox = p.get("bbox") or None
                if isinstance(bbox, str) and bbox.strip():
                    bbox = [float(x) for x in bbox.split(",")]
                    if len(bbox) != 4:
                        return self._json({"error": "bbox는 x0,y0,x1,y1 네 숫자로 입력하세요"}, 400)
                elif not bbox:
                    bbox = None
                return self._json(inspect_geometry(p.get("geometry", ""), zone=zone, bbox=bbox))
            if path == "/api/build-occ":
                result = build_occ_geometry(p.get("geometry", ""))
                return self._json(result, 200 if result.get("ok") else 400)
            if path == "/api/build-body-mesh":
                result = build_body_fitted_mesh(p.get("geometry", ""), p.get("settings"))
                return self._json(result, 200 if result.get("ok") else 400)
            if path == "/api/run-body-isothermal":
                result = run_body_fitted_isothermal(p.get("mesh_case", ""), p.get("settings"))
                return self._json(result, 200 if result.get("ok") else 400)
            if path == "/api/run-body-thermal":
                result = run_body_fitted_thermal(
                    p.get("mesh_case", ""), p.get("initial_case", ""), p.get("settings")
                )
                return self._json(result, 200 if result.get("ok") else 400)
            if path == "/api/continue-body-thermal":
                result = continue_body_fitted_thermal(
                    p.get("solver_case", ""), p.get("settings")
                )
                return self._json(result, 200 if result.get("ok") else 400)
            if path == "/api/start-field-design-run":
                result = enqueue_field_design_run(p.get("case", ""))
                return self._json(result, 200 if result.get("ok") else 400)
            if path == "/api/start-field-pipeline-job":
                result = start_field_pipeline_selection(
                    p.get("geometry_id", ""), p.get("geometry", ""), p.get("settings")
                )
                return self._json(result, 200 if result.get("ok") else 400)
            if path == "/api/resume-field-pipeline-job":
                result = resume_field_pipeline_job(p.get("job", ""))
                return self._json(result, 200 if result.get("ok") else 400)
            if path == "/api/body-gci":
                result = build_body_fitted_gci(p.get("cases"))
                return self._json(result, 200 if result.get("ok") else 400)
            if path == "/api/start-body-gci-job":
                result = start_body_gci_selection(
                    p.get("geometry_id", ""), p.get("geometry", ""), p.get("settings")
                )
                return self._json(result, 200 if result.get("ok") else 400)
            if path == "/api/confirm-body-geometry":
                result = confirm_body_gci_geometry(
                    p.get("geometry", ""), p.get("zone"), p.get("height_m"),
                    p.get("terminals"), p.get("obstacles"), p.get("bbox"),
                    p.get("unit_confirmed") is True,
                )
                return self._json(result, 200 if result.get("ok") else 400)
            if path == "/api/resume-body-gci-job":
                result = resume_body_gci_job(p.get("study", ""))
                return self._json(result, 200 if result.get("ok") else 400)
            if path == "/api/run-body-transient":
                result = run_body_fitted_transient(p.get("solver_case", ""), p.get("settings"))
                return self._json(result, 200 if result.get("ok") else 400)
            if path == "/api/create":
                r = create_case(p)
                if r.get("ok") and p.get("run_now"):
                    r["run_error"] = enqueue_run(r["dir"])
                return self._json(r)
            m = re.match(r"^/api/run/([^/]+)$", path)
            if m:
                err = enqueue_run(m.group(1))
                return self._json({"error": err} if err else {"ok": True})
            m = re.match(r"^/api/grid/([^/]+)$", path)
            if m:
                err = enqueue_grid(m.group(1))
                return self._json({"error": err} if err else {"ok": True})
            m = re.match(r"^/api/delete/([^/]+)$", path)
            if m:
                return self._json(delete_case(m.group(1)))
            self._send(404, "not found")
        except (ConnectionAbortedError, BrokenPipeError):
            pass
        except Exception as e:
            try:
                self._json({"error": str(e)}, 500)
            except Exception:
                pass

    def _handle_case_review(self, payload):
        required = {
            "case", "reviewer_id", "decision", "reason", "target_sha256",
        }
        optional = {"supersedes_review_ids"}
        if not isinstance(payload, dict) or set(payload) - required - optional:
            return self._json({
                "ok": False, "code": "INVALID_REQUEST_BODY"
            }, 400)
        if not required.issubset(payload):
            return self._json({
                "ok": False, "code": "INVALID_REQUEST_BODY"
            }, 400)
        case_name = payload.get("case")
        reviewer_id = payload.get("reviewer_id")
        decision = payload.get("decision")
        reason = payload.get("reason")
        target_sha256 = payload.get("target_sha256")
        supersedes = payload.get("supersedes_review_ids", [])
        valid_shape = (
            isinstance(case_name, str)
            and bool(case_name)
            and bool(_SAFE_NAME.fullmatch(case_name))
            and ".." not in case_name
            and isinstance(reviewer_id, str)
            and bool(reviewer_id.strip())
            and decision in {"APPROVED", "REJECTED"}
            and isinstance(reason, str)
            and bool(reason.strip())
            and isinstance(target_sha256, str)
            and bool(cfd_review.SHA256_PATTERN.fullmatch(target_sha256))
            and isinstance(supersedes, list)
            and all(isinstance(item, str) for item in supersedes)
            and len(supersedes) == len(set(supersedes))
        )
        if not valid_shape:
            return self._json({
                "ok": False, "code": "INVALID_REQUEST_BODY"
            }, 400)
        case = _body_solver_case(case_name)
        if case is None:
            return self._json({
                "ok": False,
                "code": "CASE_EVIDENCE_NOT_FOUND",
                "case": case_name,
            }, 404)
        evidence_path = case / "case_evidence.v1.json"
        if not evidence_path.is_file():
            return self._json({
                "ok": False,
                "code": "CASE_EVIDENCE_NOT_FOUND",
                "case": case.name,
            }, 404)
        try:
            review = cfd_review.create_review(
                evidence_path,
                projects_root=Path(ROOT),
                expected_target_sha256=target_sha256,
                reviewer_id=reviewer_id,
                decision=decision,
                reason=reason,
                supersedes_review_ids=supersedes,
            )
            with cfd_review.review_state_lock(
                evidence_path, projects_root=Path(ROOT)
            ):
                health = cfd_case_health.build_case_health(
                    evidence_path, projects_root=Path(ROOT)
                )
                summary = cfd_case_health.review_summary(
                    evidence_path, projects_root=Path(ROOT)
                )
        except ValueError as exc:
            code = str(exc)
            if "REVIEW_TARGET_CHANGED" in code or "review target" in code:
                return self._json({
                    "ok": False,
                    "code": "REVIEW_TARGET_CHANGED",
                    "case": case.name,
                }, 409)
            return self._json({
                "ok": False, "code": code or "INVALID_REQUEST_BODY"
            }, 400)
        return self._json({
            "ok": True,
            "review": review,
            "review_summary": summary,
            "case_health": health,
        }, 201)

    def _handle_import_dxf(self, u):
        """raw POST DXF 업로드(최대 100 MiB) → 자동 파싱 → geometry.json."""
        q = parse_qs(u.query)
        filename = q.get("filename", [""])[0]
        unit_override = q.get("unit", ["auto"])[0].strip().lower()
        if unit_override not in ("auto", "header", "mm"):
            return self._json({"error": "도면 단위는 자동 감지, mm, CAD 헤더 중에서 선택하세요."}, 400)
        stem, ext = _safe_upload_stem(filename)
        if ext == ".dwg":
            return self._json({
                "error": "DWG는 직접 읽을 수 없습니다. AutoCAD에서 [다른 이름으로 저장] → "
                         "AutoCAD 2010 이상 DXF(ASCII, 단위 mm)로 변환한 뒤 선택하세요."
            }, 415)
        if ext != ".dxf":
            return self._json({"error": "DXF 파일(.dxf)만 선택할 수 있습니다."}, 415)
        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._json({"error": "파일 크기 정보를 확인할 수 없습니다."}, 400)
        if size <= 0:
            return self._json({"error": "선택한 DXF 파일이 비어 있습니다."}, 400)
        if size > MAX_DXF_UPLOAD:
            return self._json({"error": "DXF 파일은 최대 100MB까지 업로드할 수 있습니다."}, 413)

        import_dir = os.path.realpath(os.path.join(ROOT, "_imports"))
        root_real = os.path.realpath(ROOT)
        if not import_dir.startswith(root_real + os.sep):
            return self._json({"error": "업로드 저장 경로가 안전하지 않습니다."}, 400)
        os.makedirs(import_dir, exist_ok=True)
        token = uuid.uuid4().hex[:8]
        final_path = os.path.join(import_dir, f"{stem}_{token}.dxf")
        part_path = final_path + ".part"
        remaining = size
        try:
            with open(part_path, "wb") as f:
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("업로드가 중간에 끊겼습니다")
                    f.write(chunk)
                    remaining -= len(chunk)
            os.replace(part_path, final_path)
        except Exception as e:
            try:
                if os.path.exists(part_path):
                    os.remove(part_path)
            except OSError:
                pass
            return self._json({"error": f"DXF 저장 실패: {e}"}, 400)

        try:
            return self._json(_parse_uploaded_dxf(final_path, unit_override=unit_override))
        except (Exception, SystemExit) as e:
            return self._json({
                "error": f"DXF 자동 변환 실패: {type(e).__name__}: {e}",
                "dxf": final_path,
                "help": "도면 단위가 mm인지, 외부참조가 바인딩됐는지, DXF가 손상되지 않았는지 확인하세요."
            }, 422)

    def _serve_report(self, case_name):
        d = safe_case_dir(case_name)
        if not d:
            return self._send(404, "케이스 없음")
        reps = glob.glob(os.path.join(d, "cfd_report_*.html"))
        if not reps:
            return self._send(404, "<meta charset='utf-8'>리포트가 아직 없습니다 — 먼저 실행하세요.")
        with open(max(reps, key=os.path.getmtime), encoding="utf-8") as f:
            self._send(200, f.read())

    def _serve_body_report(self, case_name):
        if not case_name or not _SAFE_NAME.match(case_name) or ".." in case_name:
            return self._send(404, "결과 없음")
        root = Path(ROOT, "_body_solver").resolve()
        report = (root / case_name / "body_fitted_report.html").resolve()
        try:
            report.relative_to(root)
        except ValueError:
            return self._send(404, "결과 없음")
        if not report.is_file():
            return self._send(404, "<meta charset='utf-8'>상세 결과 리포트가 없습니다.")
        with open(report, encoding="utf-8") as handle:
            self._send(200, handle.read())

    def _serve_body_gci_report(self, study_name):
        if not study_name or not re.fullmatch(r"gci-[0-9a-f]{12}", study_name):
            return self._send(404, "not found")
        root = Path(ROOT, "_body_gci").resolve()
        report = (root / study_name / "gci_report.html").resolve()
        try:
            report.relative_to(root)
        except ValueError:
            return self._send(404, "not found")
        if not report.is_file():
            return self._send(404, "<meta charset='utf-8'>GCI 보고서가 없습니다.")
        return self._send(200, report.read_text(encoding="utf-8"))

    def _serve_file(self, case_name, fname):
        d = safe_case_dir(case_name)
        if not d or not _SAFE_NAME.match(fname):
            return self._send(404, "not found")
        full = os.path.realpath(os.path.join(d, fname))
        if not full.startswith(d + os.sep) or not os.path.isfile(full):
            return self._send(404, "not found")
        ext = os.path.splitext(fname)[1].lower()
        with open(full, "rb") as f:
            self._send(200, f.read(), _CTYPES.get(ext, "text/plain; charset=utf-8"))


# ── 비정형 body-fitted 단면 결과 페이지 ─────────────────────────────────────

PAGE_FIELD_RUN = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>현장 도면 자동 해석</title>
<style>:root{--navy:#16324f;--blue:#1976d2;--green:#207245;--red:#b3261e;--line:#dce5ec;--muted:#627484}*{box-sizing:border-box}body{margin:0;background:#f4f7f9;color:#243746;font:16px/1.55 Segoe UI,Malgun Gothic,sans-serif}main{max-width:980px;margin:28px auto;padding:0 18px}.panel{background:#fff;border:1px solid var(--line);border-radius:14px;padding:24px;margin:16px 0;box-shadow:0 4px 18px #17324f10}h1,h2{color:var(--navy);margin-top:0}.back{color:var(--blue);text-decoration:none}.steps{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:18px 0}.step{padding:11px 7px;text-align:center;border-radius:9px;background:#edf3f7;color:#526675;font-size:14px}.step b{display:block;color:var(--navy)}select,button{font:inherit;border-radius:9px;padding:11px 14px}select{width:min(100%,640px);border:1px solid var(--line);background:#fff}button{border:0;background:var(--blue);color:#fff;cursor:pointer;font-weight:700}button:disabled{opacity:.5;cursor:default}.hint{color:var(--muted)}.reason{margin:9px 0;padding:10px 12px;border-left:4px solid #e6a23c;background:#fff8e8}.job{border-top:1px solid var(--line);padding:16px 0}.job:first-child{border-top:0}.pass{color:var(--green);font-weight:700}.fail{color:var(--red);font-weight:700}.bar{height:8px;background:#e5edf2;border-radius:8px;overflow:hidden;margin:8px 0}.fill{height:100%;background:var(--blue)}@media(max-width:700px){.steps{grid-template-columns:1fr}.panel{padding:18px}}</style></head><body><main>
<a class="back" href="/">← 첫 화면</a><section class="panel"><h1>현장 도면 자동 해석</h1><p>확인이 끝난 DXF 하나를 선택하면 3D 공기영역, 상세 메시, 등온 초기장, 열·부력 해석과 최소 3.0 유동 교환시간까지 자동으로 진행합니다. 창을 닫아도 작업 기록은 남고 중단 지점부터 재개할 수 있습니다.</p><div class="steps"><div class="step"><b>1</b>3D 공기영역</div><div class="step"><b>2</b>상세 메시</div><div class="step"><b>3</b>등온 초기장</div><div class="step"><b>4</b>열·부력</div><div class="step"><b>5</b>3.0 FTT 결과</div></div></section>
<section class="panel"><h2>도면 선택</h2><select id="geometry"><option value="">도면을 찾는 중입니다...</option></select> <button id="start" type="button">자동 해석 시작</button><p id="message" class="hint"></p><div id="geometryHelp" class="hint"></div><p class="hint">도면이 보이지 않으면 <a class="back" href="/new">새 DXF 불러오기</a>에서 방·급기·배기·발열 장비를 먼저 확인하세요.</p></section>
<section class="panel"><h2>자동 작업</h2><div id="jobs"><p class="hint">작업 기록을 불러오는 중입니다.</p></div></section>
<script>const el=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),f=(v,n=2)=>v==null?'—':Number(v).toFixed(n),rt=s=>{const n=Number(s);if(!Number.isFinite(n)||n<0)return'';const m=Math.max(1,Math.ceil(n/60)),h=Math.floor(m/60),r=m%60;return h?`${h}시간${r?' '+r+'분':''}`:`${m}분`},eta=s=>{const n=Number(s);return Number.isFinite(n)&&n>=0?new Date(Date.now()+n*1000).toLocaleString('ko-KR',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}):''};const requested=new URLSearchParams(location.search).get('geometry')||'';
async function loadGeometry(){try{const r=await fetch('/api/body-gci-geometries'),j=await r.json(),rows=(j.geometries||[]).filter(x=>x.kind==='project');geometry.innerHTML=rows.length?'<option value="">확인이 끝난 현장 도면을 선택하세요</option>'+rows.map(x=>{const eligible=x.ready&&x.field_eligible!==false,label=eligible?'준비 완료':x.field_reason||'확인 필요';return `<option value="${esc(x.id)}" ${eligible?'':'disabled'}>${esc(x.label)} · ${esc(label)}</option>`}).join(''):'<option value="">불러온 현장 DXF가 없습니다</option>';const blocked=rows.filter(x=>!x.ready||x.field_eligible===false).slice(0,5);geometryHelp.innerHTML=blocked.length?'<b>사용할 수 없는 도면</b>'+blocked.map(x=>{const reason=x.field_reason||((x.issues||[])[0]||{}).user_message||'3D/CFD 입력 확인이 필요합니다.',action=x.ready?'':` <a class="back" href="/new?geometry=${encodeURIComponent(x.path)}">이 도면 확인하기 →</a>`;return `<div class="reason">${esc(x.label)}: ${esc(reason)}${action}</div>`}).join(''):'';const match=rows.find(x=>x.ready&&x.field_eligible!==false&&x.path===requested);if(match)geometry.value=match.id}catch(e){message.textContent='도면 목록을 불러오지 못했습니다: '+e.message}}
function stageLabel(s){const x=String(s||'');if(x==='occ')return '3D 공기영역';if(x.includes('mesh'))return '상세 메시';if(x.includes('isothermal'))return '등온 초기장';if(x.includes('thermal'))return '열·부력 계산';if(x==='complete')return '완료';return x||'대기'}
async function loadJobsLegacy(){try{const r=await fetch('/api/field-pipeline-jobs'),j=await r.json(),rows=j.jobs||[];jobs.innerHTML=rows.length?rows.map(x=>{const l=x.level||{},p=x.live_progress||null,ftt=p?p.estimated_flow_through_fraction:Number(l.flow_through_fraction||0),pct=Math.min(100,Math.max(0,ftt/3*100)),state=x.runtime_state==='running'?'실행 중':x.runtime_state==='queued'?'대기 중':x.status==='complete'?'완료':x.status,remain=p?rt(p.estimated_remaining_runtime_seconds):'',estimateLabel=p&&p.estimate_basis==='initial_stability_scaled'?'보수 추정':'예상',progress=p?`${estimateLabel} ${f(ftt)} / 3.00 FTT · 다음 저장 ${f(p.next_checkpoint_time_s)}초${remain?' · 남은 실제시간 약 '+remain+' · 완료 예상 '+eta(p.estimated_remaining_runtime_seconds):''}`:`${f(ftt)} / 3.00 FTT`,heartbeat=l.live_message?' · '+esc(String(l.live_message).slice(-100)):'';return `<div class="job"><b>${esc(PathName((x.input||{}).source_dxf_path)||x.job)}</b> · <span class="${x.status==='complete'?'pass':x.status==='FAIL'?'fail':''}">${esc(state)}</span><div class="hint" title="${esc(l.live_message||'')}">현재 단계: ${esc(stageLabel(x.stage))} · 메시 셀 ${l.cell_count==null?'—':Number(l.cell_count).toLocaleString()} · ${progress}${heartbeat}</div><div class="bar"><div class="fill" style="width:${pct}%"></div></div>${x.error?`<p class="fail">${esc(x.error)}</p>`:''}<p>${x.results_url?`<a class="back" href="${x.results_url}">결과 보기 →</a> · <a class="back" href="${x.report_url}" target="_blank">보고서</a>`:''}${x.runtime_state==='idle'&&x.status!=='complete'?`<button type="button" onclick="resume('${esc(x.job)}')">중단 지점부터 재개</button>`:''}</p></div>`}).join(''):'<p class="hint">아직 자동 작업이 없습니다.</p>'}catch(e){jobs.textContent=e.message}}
function PathName(p){return String(p||'').split(/[\\/]/).pop()}
start.onclick=async()=>{if(!geometry.value){message.textContent='확인이 끝난 도면을 선택하세요.';return}if(!confirm('이 도면을 3.0 유동 교환시간까지 자동 해석할까요?'))return;start.disabled=true;message.textContent='자동 작업을 만들고 있습니다.';try{const r=await fetch('/api/start-field-pipeline-job',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({geometry_id:geometry.value})}),j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||'작업 생성 실패');message.textContent=j.queued?'자동 해석을 시작했습니다. 창을 닫아도 계속됩니다.':'이미 완료된 결과가 있습니다.';await loadJobs()}catch(e){message.textContent=e.message}finally{start.disabled=false}};
async function resume(job){message.textContent='재개 요청 중...';const r=await fetch('/api/resume-field-pipeline-job',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job})}),j=await r.json();message.textContent=j.ok?'중단 지점부터 다시 시작합니다.':j.error;loadJobs()}
const fieldPipelineTerminal=s=>s==='complete'||s==='analysis_complete_not_citable';
async function loadJobs(){try{const r=await fetch('/api/field-pipeline-jobs'),j=await r.json(),rows=j.jobs||[];jobs.innerHTML=rows.length?rows.map(x=>{const l=x.level||{},p=x.live_progress||null,ftt=p?p.estimated_flow_through_fraction:Number(l.flow_through_fraction||0),pct=Math.min(100,Math.max(0,ftt/3*100)),preliminary=x.status==='analysis_complete_not_citable',state=x.runtime_state==='running'?'실행 중':x.runtime_state==='queued'?'대기 중':preliminary?'해석 완료 · 설계 인용 보류':x.status==='complete'?'설계 검토 인용 가능':x.status,remain=p?rt(p.estimated_remaining_runtime_seconds):'',estimateLabel=p&&p.estimate_basis==='initial_stability_scaled'?'보수 추정':'예상',progress=p?`${estimateLabel} ${f(ftt)} / 3.00 FTT · 다음 저장 ${f(p.next_checkpoint_time_s)}초${remain?' · 남은 실제시간 약 '+remain+' · 완료 예상 '+eta(p.estimated_remaining_runtime_seconds):''}`:`${f(ftt)} / 3.00 FTT`,heartbeat=l.live_message?' · '+esc(String(l.live_message).slice(-100)):'',blockers=Array.isArray(x.citation_blockers)?x.citation_blockers:[],citation=preliminary?`<p class="reason">3.0 FTT 해석은 완료되었습니다. <b>설계 인용 보류</b> · 결과 판정 ${esc(x.citation_status||'NOT_EVALUATED')}${blockers.length?' · 확인 항목: '+esc(blockers.join(', ')):''}</p>`:'',links=x.results_url?`<a class="back" href="${x.results_url}">결과 보기 →</a>${x.report_url?` · <a class="back" href="${x.report_url}" target="_blank">보고서</a>`:''}`:'';return `<div class="job"><b>${esc(PathName((x.input||{}).source_dxf_path)||x.job)}</b> · <span class="${x.status==='complete'?'pass':x.status==='FAIL'?'fail':''}">${esc(state)}</span><div class="hint" title="${esc(l.live_message||'')}">현재 단계: ${esc(stageLabel(x.stage))} · 메시 셀 ${l.cell_count==null?'—':Number(l.cell_count).toLocaleString()} · ${progress}${heartbeat}</div><div class="bar"><div class="fill" style="width:${pct}%"></div></div>${citation}${x.error?`<p class="fail">${esc(x.error)}</p>`:''}<p>${links}${x.runtime_state==='idle'&&!fieldPipelineTerminal(x.status)?`<button type="button" onclick="resume('${esc(x.job)}')">중단 지점부터 재개</button>`:''}</p></div>`}).join(''):'<p class="hint">아직 자동 작업이 없습니다.</p>'}catch(e){jobs.textContent=e.message}}
start.onclick=async()=>{if(!geometry.value){message.textContent='확인이 끝난 도면을 선택하세요.';return}if(!confirm('이 도면을 3.0 유동 교환시간까지 자동 해석할까요?'))return;start.disabled=true;message.textContent='자동 작업을 만들고 있습니다.';try{const r=await fetch('/api/start-field-pipeline-job',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({geometry_id:geometry.value})}),j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||'작업 생성 실패');message.textContent=j.queued?'자동 해석을 시작했습니다. 창을 닫아도 계속됩니다.':j.status==='analysis_complete_not_citable'?'3.0 FTT 해석은 완료되었습니다. 설계 인용 증거 확인이 필요합니다.':'이미 설계 검토 인용 가능한 결과가 있습니다.';await loadJobs()}catch(e){message.textContent=e.message}finally{start.disabled=false}};
resume=async job=>{message.textContent='재개 요청 중...';const r=await fetch('/api/resume-field-pipeline-job',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({job})}),j=await r.json();message.textContent=j.ok?(j.queued?'중단 지점부터 다시 시작합니다.':j.status==='analysis_complete_not_citable'?'3.0 FTT 해석은 완료되었습니다. 설계 인용 증거 확인이 필요합니다.':'이미 설계 검토 인용 가능한 결과입니다.'):j.error;loadJobs()};
loadGeometry();loadJobs();setInterval(loadJobs,3000);</script></main></body></html>"""


PAGE_BODY_GCI = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MEP CFD Studio · 메시 독립성</title>
<style>
:root{--ink:#1d2b36;--muted:#64727c;--line:#d9e3ea;--accent:#245f8e;--ok:#207245;--bad:#a92c2c}
*{box-sizing:border-box}body{margin:0;background:#f4f7fa;color:var(--ink);font-family:Segoe UI,Malgun Gothic,sans-serif}
main{max-width:980px;margin:22px auto;padding:0 16px}header,.panel{background:#fff;border:1px solid var(--line);border-radius:12px;padding:18px;margin-bottom:14px}
h1{font-size:1.5rem;margin:5px 0 0;color:#244f73}.sub{color:var(--muted);line-height:1.55}.back{color:var(--accent);text-decoration:none}
.case{display:grid;grid-template-columns:34px 1fr auto;gap:10px;align-items:center;padding:12px;border-bottom:1px solid #edf1f4}.case:last-child{border:0}
.case small{display:block;color:var(--muted);margin-top:3px}.case.bad{opacity:.65}.reason{color:var(--bad)!important;font-size:.82rem}
button{font:inherit;border:0;border-radius:8px;padding:10px 16px;background:var(--accent);color:#fff;cursor:pointer}button:disabled{opacity:.45;cursor:not-allowed}
#status{margin-left:10px;color:var(--muted)}table{width:100%;border-collapse:collapse;margin-top:14px}th,td{padding:9px;border-bottom:1px solid var(--line);text-align:right}th:first-child,td:first-child{text-align:left}
.pass{color:var(--ok);font-weight:700}.fail{color:var(--bad);font-weight:700}.notice{padding:10px 12px;background:#fff6d8;border-left:4px solid #d39b00;font-size:.88rem}
.pathrow{display:flex;gap:8px;flex-wrap:wrap}.pathrow input,.pathrow select{flex:1;min-width:280px;font:inherit;border:1px solid #b9c8d3;border-radius:8px;padding:9px;background:#fff}.advanced{margin-top:10px;color:var(--muted)}
.job{border-top:1px solid var(--line);padding:12px 0}.job:first-child{margin-top:12px}.levels{display:flex;gap:7px;flex-wrap:wrap;margin-top:7px}.level{background:#eef3f6;border-radius:12px;padding:4px 9px;font-size:.8rem}.level.PASS{background:#e4f4e9;color:var(--ok)}.level.FAIL{background:#fdeaea;color:var(--bad)}
</style></head><body><main>
<header><a class="back" href="/">← 대시보드</a><h1>메시 불확실성 확인</h1>
<p class="sub">v3는 동일한 CAD·유량·열원·부력 조건에서 최소 3.0 유동 교환시간을 계산하고, 최소 4개 격자의 마지막 0.1 유동 교환시간을 체적가중·시간평균합니다. 비정렬 격자의 산포는 Eça–Hoekstra 최소제곱법으로 평가하며 세 지표 불확실성 5% 이하와 시간창 변화 2% 이하를 모두 만족해야 PASS입니다.</p></header>
<section class="panel"><h2>자동 4수준 계산</h2><p class="sub">확인이 끝난 도면을 선택하면 매우 거친·거친·중간·세분 메시와 해석을 순서대로 실행합니다. 호환되는 기존 결과는 검증 후 재사용하며 앱을 다시 시작해도 완료 단계부터 재개합니다.</p>
<div class="pathrow"><select id="autoGeometrySelect" aria-label="검증할 도면 선택"><option value="">도면 목록을 불러오는 중…</option></select><button id="autoStart">자동 작업 시작</button></div><div id="autoGeometryHelp" class="sub"></div>
<details class="advanced"><summary>고급: 기존 geometry.json 경로 직접 입력</summary><div class="pathrow"><input id="autoGeometry" type="text" placeholder="C:\\...\\drawing.geometry.json" aria-label="기존 geometry.json 경로"></div></details><p id="autoStatus" class="sub"></p><div id="jobs"></div></section>
<section class="panel"><h2>완료된 결과 직접 비교</h2><div id="cases">결과를 찾는 중입니다.</div><p><button id="run" disabled>선택 결과 비교</button><span id="status"></span></p></section>
<section class="panel" id="result" hidden></section>
<p class="notice">절대온도 대신 기준온도 대비 상승량을 비교합니다. 전역 최고온도는 열원 모서리 진단으로만 표시합니다. FAIL이면 격자 세분뿐 아니라 시간창 통계의 단조성과 충분한 계산시간도 함께 확인해야 합니다.</p>
</main><script>
const box=document.getElementById('cases'),run=document.getElementById('run'),statusEl=document.getElementById('status'),result=document.getElementById('result'),jobs=document.getElementById('jobs'),autoGeometry=document.getElementById('autoGeometry'),autoGeometrySelect=document.getElementById('autoGeometrySelect'),autoGeometryHelp=document.getElementById('autoGeometryHelp'),autoStatus=document.getElementById('autoStatus');
const f=(v,n=3)=>Number(v).toFixed(n), esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),rt=s=>{const n=Number(s);if(!Number.isFinite(n)||n<0)return'';const m=Math.max(1,Math.ceil(n/60)),h=Math.floor(m/60),r=m%60;return h?`${h}시간${r?' '+r+'분':''}`:`${m}분`},eta=s=>{const n=Number(s);return Number.isFinite(n)&&n>=0?new Date(Date.now()+n*1000).toLocaleString('ko-KR',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'}):''};
const requestedGeometry=new URLSearchParams(location.search).get('geometry')||'';autoGeometry.value=requestedGeometry;
async function loadGeometryChoices(){try{const r=await fetch('/api/body-gci-geometries'),j=await r.json(),items=j.geometries||[];autoGeometrySelect.innerHTML=items.map(x=>`<option value="${esc(x.id)}" ${x.ready?'':'disabled'}>${esc(x.label)} · ${x.kind==='benchmark'?'검증 기준방':x.ready?'3D/CFD 확인 완료':'확인 필요 ('+x.issue_count+')'}</option>`).join('')||'<option value="">확인이 끝난 도면이 없습니다</option>';const blocked=items.filter(x=>!x.ready).slice(0,5);autoGeometryHelp.innerHTML=blocked.length?'<b>확인이 필요한 도면</b>'+blocked.map(x=>{const issues=(x.issues||[]).slice(0,3),messages=issues.map(i=>i.user_message||'도면 의미 확인이 필요합니다.').join(' · '),action=(issues[0]||{}).action||'';return `<div class="reason">${esc(x.label)}: ${esc(messages)} ${esc(action)} <a class="back" href="/new?geometry=${encodeURIComponent(x.path)}">이 도면 확인하기</a></div>`}).join(''):'';const match=items.find(x=>x.path===requestedGeometry&&x.ready);if(match)autoGeometrySelect.value=match.id;else{const first=items.find(x=>x.ready);autoGeometrySelect.value=first?first.id:''}}catch(e){autoGeometrySelect.innerHTML='<option value="">도면 목록을 불러오지 못했습니다</option>';autoStatus.textContent=e.message}}
function jobLevels(job){return (job.levels||[]).map(x=>{const e=x.resource_estimate||{},live=x.estimated_live_time_s,heartbeat=x.live_message?' · '+esc(String(x.live_message).slice(-80)):'';return `<span class="level ${x.status}" title="${esc(x.live_message||'')}">${esc(x.name)} · ${esc(x.status)} · ${esc(x.stage)}${x.cell_count?' · '+Number(x.cell_count).toLocaleString()+' cells':e.estimated_cells?' · 안전상한 '+Number(e.estimated_cells).toLocaleString()+' cells / '+f(e.estimated_ram_gb,1)+' GB':''}${live!=null?' · 예상 '+f(live)+' s / '+f(x.estimated_flow_through_fraction,2)+' FTT':x.latest_time_s!=null?' · '+f(x.latest_time_s)+' s':''}${heartbeat}</span>`}).join('')}
async function loadJobs(){try{const r=await fetch('/api/body-gci-jobs'),j=await r.json();jobs.innerHTML=(j.jobs||[]).map(x=>{const p=x.live_progress,remain=p?rt(p.estimated_remaining_runtime_seconds):'',estimateLabel=p&&p.estimate_basis==='initial_stability_scaled'?'보수 추정':'예상',progress=p?`<div class="sub">${esc(p.level)} ${estimateLabel} 진행 ${f(p.estimated_time_s)} / ${f(p.target_time_s)}초 · ${f(p.estimated_flow_through_fraction,2)} / 3.00 FTT · 다음 저장 ${f(p.next_checkpoint_time_s)}초${remain?' · 남은 실제시간 약 '+remain+' · 완료 예상 '+eta(p.estimated_remaining_runtime_seconds):''}</div>`:'';return `<div class="job"><b>${esc(x.study)}</b> · <span class="${x.gate_status==='PASS'?'pass':x.status==='FAIL'||x.gate_status==='FAIL'?'fail':''}">${esc(x.runtime_state==='running'?'실행 중':x.runtime_state==='queued'?'대기 중':x.status)}</span>${progress}<div class="levels">${jobLevels(x)}</div>${x.error?`<div class="reason">${esc(x.error)}</div>`:''}<div>${x.report_url?`<a class="back" target="_blank" href="${x.report_url}">메시 불확실성 보고서 열기</a>`:''}${x.runtime_state==='idle'&&x.status!=='complete'?` <button type="button" onclick="resumeJob('${x.study}')">완료 단계부터 재개</button>`:''}</div></div>`}).join('')||'<p class="sub">아직 자동 작업이 없습니다.</p>'}catch(e){jobs.textContent=e.message}}
async function resumeJob(study){autoStatus.textContent='재개 요청 중…';const r=await fetch('/api/resume-body-gci-job',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({study})}),j=await r.json();autoStatus.textContent=j.ok?'작업을 대기열에 넣었습니다.':(j.error||'재개 실패');loadJobs()}
document.getElementById('autoStart').onclick=async()=>{const geometry_id=autoGeometrySelect.value,geometry=autoGeometry.value.trim();if(!geometry_id&&!geometry){autoStatus.textContent='검증할 도면을 선택해 주세요.';return}if(!confirm('4개 메시를 최소 3.0 유동 교환시간까지 순차 계산합니다. 기존 호환 결과는 재사용합니다. 시작할까요?'))return;autoStatus.textContent='자동 작업 생성 중…';const r=await fetch('/api/start-body-gci-job',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({geometry_id,geometry})}),j=await r.json();autoStatus.textContent=j.ok?(j.queued?'자동 작업이 대기열에 추가되었습니다.':'이미 완료된 작업입니다.'):(j.error||'작업 생성 실패');loadJobs()};
loadGeometryChoices();loadJobs();setInterval(loadJobs,3000);
function chosen(){return [...document.querySelectorAll('input[name=case]:checked')].map(x=>x.value)}
function sync(){const selected=[...document.querySelectorAll('input[name=case]:checked')],n=selected.length,v3ok=selected.every(x=>x.dataset.v3==='true');run.disabled=n!==3&&(n!==4||!v3ok);statusEl.textContent=n===4&&!v3ok?'4수준 비교는 모두 3.0 유동 교환시간을 완료해야 합니다.':`${n}개 선택 · 4개 권장`}
fetch('/api/body-gci-cases').then(r=>r.json()).then(j=>{if(!j.cases.length){box.textContent='아직 비교 가능한 상세 열·부력 결과가 없습니다.';return}
 box.innerHTML=j.cases.map(c=>`<label class="case ${c.eligible?'':'bad'}"><input type="checkbox" name="case" value="${esc(c.name)}" data-v3="${c.v3_eligible===true}" ${c.eligible?'':'disabled'}><span><b>${esc(c.name)}</b>${c.eligible?`<small>${Number(c.cell_count).toLocaleString()} cells · h=${f(c.effective_grid_width_m,4)} m · ${f(c.time_s)} s · ${c.v3_eligible?'v3 준비 완료':'v2만 가능(3.0 교환시간 미완료)'}</small>`:`<small class="reason">${esc(c.reason)}</small>`}</span><a class="back" href="/body-results/${encodeURIComponent(c.name)}" target="_blank">결과 보기</a></label>`).join('');
 document.querySelectorAll('input[name=case]').forEach(x=>x.onchange=sync);sync()}).catch(e=>box.textContent=e.message);
run.onclick=()=>{run.disabled=true;statusEl.textContent='메시 불확실성 계산 중…';result.hidden=true;fetch('/api/body-gci',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cases:chosen()})}).then(async r=>{const j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||'계산 실패');return j}).then(j=>{const m=j.manifest,cls=m.status.toLowerCase(),ratios=m.comparison.refinement_ratios_fine_to_coarse||[m.comparison.refinement_ratio_medium_to_fine,m.comparison.refinement_ratio_coarse_to_medium],v3=m.contract==='grid_convergence.v3';result.hidden=false;result.innerHTML=`<h2 class="${cls}">메시 불확실성 ${m.status}</h2><p>세분비 ${ratios.map(x=>f(x)).join(' / ')} · 물리시간 ${f(m.comparison.physical_time_s)} s</p><table><tr><th>지표</th><th>세분</th><th>중간</th><th>거친</th>${v3?'<th>매우 거친</th>':''}<th>${v3?'불확실성':'GCI'}</th><th>시간창 변화</th><th>판정</th></tr>${m.metrics.map(x=>`<tr><td>${esc(x.label)} (${esc(x.unit)})</td><td>${f(x.fine)}</td><td>${f(x.medium)}</td><td>${f(x.coarse)}</td>${v3?`<td>${f((x.grid_values||[])[3])}</td>`:''}<td>${(v3?x.uncertainty_fine_pct:x.gci_fine_pct)==null?'산출 불가':f(v3?x.uncertainty_fine_pct:x.gci_fine_pct,2)+'%'}</td><td>${v3?f(x.window_drift_pct,2)+'%':'—'}</td><td class="${x.status.toLowerCase()}">${x.status}</td></tr>`).join('')}</table><p><a class="back" target="_blank" href="${j.report_url}">자립형 상세 보고서 열기 →</a></p>`;statusEl.textContent='완료'}).catch(e=>statusEl.textContent=e.message).finally(()=>sync())};
</script></body></html>"""


PAGE_RELEASE_READINESS = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MEP CFD Studio · 출시 준비</title><style>
:root{--ink:#1d2b36;--muted:#64727c;--line:#d9e3ea;--accent:#245f8e;--ok:#207245;--bad:#a92c2c}
*{box-sizing:border-box}body{margin:0;background:#f4f7fa;color:var(--ink);font-family:Segoe UI,Malgun Gothic,sans-serif}
main{max-width:980px;margin:22px auto;padding:0 16px}header,.panel{background:#fff;border:1px solid var(--line);border-radius:12px;padding:18px;margin-bottom:14px}
h1{font-size:1.5rem;margin:5px 0}.sub{color:var(--muted);line-height:1.55}.back{color:var(--accent);text-decoration:none}
table{width:100%;border-collapse:collapse}th,td{padding:11px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
.pass{color:var(--ok);font-weight:700}.blocked{color:var(--bad);font-weight:700}.badge{display:inline-block;padding:5px 9px;border-radius:12px;background:#edf2f5}
button,select{font:inherit;border-radius:8px;padding:9px 14px}button{border:0;background:var(--accent);color:#fff;cursor:pointer}select{border:1px solid var(--line);background:#fff;min-width:300px}.field-tools{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.confirm{display:flex;gap:7px;align-items:center}.msg{color:var(--muted)}
</style></head><body><main><header><a class="back" href="/">← 대시보드</a><h1>출시 준비 현황</h1>
<p class="sub">샘플이나 자체 선언을 통과 증거로 사용하지 않습니다. 실제 G2 검증, 현장 DXF 3건, 설치·복구 수용시험, 관찰자 기록 UAT를 파일과 해시로 확인합니다.</p></header>
<section class="panel"><div id="summary">검사 중입니다.</div><p><button onclick="load()">다시 검사</button></p>
<table><thead><tr><th>항목</th><th>상태</th><th>현재 근거·다음 조치</th></tr></thead><tbody id="checks"></tbody></table></section>
<section class="panel"><h2>현장 도면 해석</h2><p class="sub">확인이 끝난 DXF를 선택하면 3D 공기영역부터 3.0 유동 교환시간 결과까지 한 번에 계산합니다.</p><a class="back" href="/field-run">현장 도면 자동 해석 시작하기 →</a></section>
<section class="panel"><h2>현장 도면 검증 등록</h2>
<p class="sub">경로를 직접 입력할 필요가 없습니다. 완료된 상세 열해석을 선택하면 원본 DXF부터 결과까지 파일과 해시를 다시 검사합니다. 같은 원본은 한 번만 등록되며, 3건 전체에서 도면 단위·원점·블록 회전·레이어 구성이 각각 2종 이상이어야 합니다. 위 표의 현재/필요 수를 먼저 확인하세요.</p>
<div class="field-tools"><select id="fieldCase"><option value="">검증 가능한 결과를 찾는 중...</option></select>
<label class="confirm"><input id="actualSite" type="checkbox"> 실제 현장에서 받은 도면입니다</label>
<button id="registerField" type="button">검사하고 등록</button></div><p id="fieldMessage" class="msg"></p></section>
<section class="panel"><h2>기계설비 담당자 사용자 시험</h2><p class="sub">현장 도면 검증을 등록한 뒤, 참가자와 다른 관찰자가 필수 작업 6개를 화면에서 순서대로 기록합니다.</p><a class="back" href="/uat-session">관찰 시험 시작하기 →</a></section>
</main><script>
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function load(){summary.textContent='검사 중입니다.';checks.innerHTML='';try{const r=await fetch('/api/release-readiness'),j=await r.json(),m=j.manifest||{};summary.innerHTML=`<span class="badge">제한적 베타 ${m.limited_beta_ready?'PASS':'BLOCKED'}</span> <span class="badge">제품 준비 ${m.product_ready?'PASS':'BLOCKED'}</span>`;checks.innerHTML=(m.checks||[]).map(x=>`<tr><td>${esc(x.label)}</td><td class="${x.status.toLowerCase()}">${esc(x.status)}</td><td>${esc(x.detail)}</td></tr>`).join('')}catch(e){summary.textContent='검사 실패: '+e.message}}
async function loadFieldCases(){try{const r=await fetch('/api/field-evidence-candidates'),j=await r.json(),rows=j.cases||[],eligible=rows.filter(x=>x.eligible);fieldCase.innerHTML=rows.length?'<option value="">완료 결과를 선택하세요</option>'+rows.map(x=>`<option value="${esc(x.case)}" ${x.eligible?'':'disabled'}>${esc(x.source)} · ${esc(x.case)}${x.reason?' · '+esc(x.reason):''}</option>`).join(''):'<option value="">아직 등록 가능한 완료 결과가 없습니다</option>';if(!eligible.length&&rows.length)fieldMessage.textContent='완료 결과가 모두 등록됐거나 실제 현장 증거 기준을 충족하지 않습니다.'}catch(e){fieldMessage.textContent=e.message}}
registerField.onclick=async()=>{if(!fieldCase.value){fieldMessage.textContent='완료된 상세 열해석 결과를 선택하세요.';return}if(!actualSite.checked){fieldMessage.textContent='실제 현장 도면인지 확인해 주세요.';return}registerField.disabled=true;fieldMessage.textContent='원본과 계산 산출물을 다시 검사하고 있습니다.';try{const r=await fetch('/api/register-field-evidence',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({case:fieldCase.value,actual_site_drawing:true})}),j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||(j.manifest&&j.manifest.errors||[]).join(', ')||'검증 실패');actualSite.checked=false;await load();await loadFieldCases();fieldMessage.textContent='현장 도면 검증 증거를 등록했습니다.'}catch(e){fieldMessage.textContent='등록하지 못했습니다: '+e.message}finally{registerField.disabled=false}};
load();loadFieldCases();
</script></body></html>"""


PAGE_UAT_SESSION = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>MEP CFD Studio · 사용자 시험</title><style>
:root{--ink:#1d2b36;--muted:#64727c;--line:#d9e3ea;--accent:#245f8e;--ok:#207245;--bad:#a92c2c}*{box-sizing:border-box}body{margin:0;background:#f4f7fa;color:var(--ink);font-family:Segoe UI,Malgun Gothic,sans-serif}main{max-width:780px;margin:24px auto;padding:0 16px}.panel{background:#fff;border:1px solid var(--line);border-radius:12px;padding:20px;margin-bottom:14px}h1{font-size:1.5rem;margin:8px 0}.sub,.msg{color:var(--muted);line-height:1.55}.back{color:var(--accent);text-decoration:none}.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}label{display:grid;gap:5px;color:var(--muted)}input,select,textarea,button{font:inherit;border-radius:8px;padding:10px}input,select,textarea{border:1px solid var(--line);background:#fff;color:var(--ink)}textarea{width:100%;min-height:78px}button{border:0;background:var(--accent);color:#fff;cursor:pointer}.fail{background:var(--bad)}.pass{background:var(--ok)}.cancel{background:#687781}.actions{display:flex;gap:10px;flex-wrap:wrap}.task{font-size:1.25rem;padding:18px;background:#edf5fa;border-radius:10px;margin:12px 0}.hidden{display:none}@media(max-width:620px){.grid{grid-template-columns:1fr}}
</style></head><body><main><a class="back" href="/release-readiness">← 출시 준비</a><section class="panel"><h1>기계설비 담당자 관찰 시험</h1><p class="sub">참가자는 평소처럼 프로그램을 사용하고, 관찰자는 각 작업의 성공 여부와 도움 횟수만 기록합니다. 설정 시간은 서버가 자동으로 계산합니다. 참가자 실명 대신 구분 가능한 코드를 사용하세요.</p></section>
<section id="setup" class="panel"><div class="grid"><label>참가자 코드<input id="participant" placeholder="예: 설비담당-A"></label><label>관찰자 이름<input id="observer" placeholder="예: 홍길동"></label></div><p><label>사용할 검증 도면<select id="fieldEvidence"><option value="">불러오는 중...</option></select></label></p><button id="start" type="button">시험 시작</button><p id="setupMsg" class="msg"></p></section>
<section id="runner" class="panel hidden"><div id="progress" class="sub"></div><div id="taskText" class="task"></div><div class="grid"><label>도움 횟수<input id="assistance" type="number" min="0" value="0"></label><label>관찰 메모<input id="taskNotes" placeholder="막힌 지점이나 질문"></label></div><p class="actions"><button class="pass" type="button" onclick="saveTask('PASS')">도움 없이/도움 후 성공</button><button class="fail" type="button" onclick="saveTask('FAIL')">완료하지 못함</button><button class="cancel" type="button" onclick="cancelSession()">시험 취소</button></p><p id="taskMsg" class="msg"></p></section>
<section id="finishBox" class="panel hidden"><h2>치명 문제 기록</h2><p class="sub">데이터 손실, 잘못된 결과를 정상으로 오인하게 하는 문제, 더 진행할 수 없는 문제만 치명 오류로 기록합니다.</p><div class="grid"><label>문제 수준<select id="incidentSeverity"><option value="">문제 없음</option><option value="fatal">치명</option><option value="major">중요</option><option value="minor">경미</option></select></label><label>문제 코드<input id="incidentCode" placeholder="예: RESULT_MISREAD"></label></div><p><label>문제 설명<textarea id="incidentNotes"></textarea></label></p><p class="actions"><button id="finish" type="button">관찰 기록 완료</button><button class="cancel" type="button" onclick="cancelSession()">시험 취소</button></p><p id="finishMsg" class="msg"></p></section>
</main><script>
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const labels={launch_application:'프로그램을 실행하고 대시보드를 연다',import_dxf:'DXF 도면을 선택해 불러온다',confirm_geometry:'자동 인식된 방·급배기구·장비를 확인한다',configure_conditions:'방 높이·풍량·발열 조건을 입력한다',run_or_open_result:'해석을 실행하거나 완료 결과를 연다',interpret_report:'온도·유속 결과와 경고를 설명한다'};let token='',current='',saving=false,finishing=false;
async function loadEvidence(){try{const r=await fetch('/api/uat/field-evidence'),j=await r.json(),rows=j.cases||[];fieldEvidence.innerHTML=rows.length?'<option value="">검증 도면 선택</option>'+rows.map(x=>`<option value="${esc(x.id)}">${esc(x.source)}</option>`).join(''):'<option value="">먼저 현장 도면 검증을 등록하세요</option>'}catch(e){setupMsg.textContent=e.message}}
function showTask(j){current=j.task;progress.textContent=`작업 ${j.task_index+1} / ${j.task_count}`;taskText.textContent=labels[current]||current;assistance.value=0;taskNotes.value=''}
start.onclick=async()=>{setupMsg.textContent='';try{const r=await fetch('/api/uat/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({participant_id:participant.value,observed_by:observer.value,field_evidence_id:fieldEvidence.value})}),j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||'시작 실패');token=j.token;localStorage.setItem('mepUatToken',token);setup.classList.add('hidden');if(j.done)finishBox.classList.remove('hidden');else{runner.classList.remove('hidden');showTask(j);if(j.resumed)taskMsg.textContent='진행 중이던 관찰 기록을 복구했습니다.'}}catch(e){setupMsg.textContent=e.message}};
async function saveTask(status){if(saving)return;saving=true;taskMsg.textContent='저장 중...';try{const r=await fetch('/api/uat/task',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token,status,task:current,assistance_count:Number(assistance.value||0),notes:taskNotes.value})}),j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||'저장 실패');if(j.done){runner.classList.add('hidden');finishBox.classList.remove('hidden')}else showTask(j);taskMsg.textContent=''}catch(e){taskMsg.textContent=e.message}finally{saving=false}}
async function cancelSession(){if(saving||finishing){taskMsg.textContent='저장 또는 완료 처리가 끝난 뒤 다시 눌러 주세요.';finishMsg.textContent='저장 또는 완료 처리가 끝난 뒤 다시 눌러 주세요.';return}if(!confirm('진행 중인 관찰 기록을 지우고 처음부터 다시 시작할까요?'))return;taskMsg.textContent='취소 중...';finishMsg.textContent='취소 중...';try{const r=await fetch('/api/uat/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token})}),j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||'취소 실패');localStorage.removeItem('mepUatToken');location.reload()}catch(e){taskMsg.textContent=e.message;finishMsg.textContent=e.message}}
finish.onclick=async()=>{if(finishing)return;finishing=true;finish.disabled=true;finishMsg.textContent='검증 기록을 만드는 중...';const incidents=incidentSeverity.value?[{severity:incidentSeverity.value,code:incidentCode.value||'UNSPECIFIED',notes:incidentNotes.value}]:[];try{const r=await fetch('/api/uat/finish',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token,critical_incidents:incidents})}),j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||(j.manifest&&j.manifest.errors||[]).join(', ')||'완료 실패');localStorage.removeItem('mepUatToken');finishMsg.innerHTML=`기록 완료: <b>${j.manifest.status}</b> · 설정 ${j.manifest.setup_minutes}분 · <a class="back" href="/release-readiness">출시 준비로 돌아가기</a>`}catch(e){finishMsg.textContent=e.message;finishing=false;finish.disabled=false}};
async function resume(){const saved=localStorage.getItem('mepUatToken');if(!saved)return;try{const r=await fetch('/api/uat/session?token='+encodeURIComponent(saved)),j=await r.json();if(!r.ok||!j.ok)throw Error();token=saved;setup.classList.add('hidden');if(j.done)finishBox.classList.remove('hidden');else{runner.classList.remove('hidden');showTask(j)}}catch(e){localStorage.removeItem('mepUatToken')}}loadEvidence();resume();
</script></body></html>"""


PAGE_BODY_RESULTS = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MEP CFD Studio · 상세 단면 결과</title>
<style>
:root{--ink:#1d2b36;--muted:#65727c;--line:#d9e3ea;--accent:#245f8e;--bg:#f4f7fa}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:Segoe UI,Malgun Gothic,sans-serif}
main{max-width:1180px;margin:18px auto;padding:0 16px}.top{display:flex;gap:14px;align-items:end;justify-content:space-between;flex-wrap:wrap}
h1{font-size:1.45rem;margin:0;color:#244f73}.sub{color:var(--muted);margin:4px 0 0}.controls{display:flex;gap:8px;align-items:end;flex-wrap:wrap}
label{font-size:.82rem;color:var(--muted);display:grid;gap:4px}select,button,a.btn{font:inherit;border:1px solid #b9c8d3;border-radius:7px;padding:7px 10px;background:#fff;color:var(--ink)}
button{cursor:pointer}button.active{background:var(--accent);color:#fff;border-color:var(--accent)}a.btn{text-decoration:none}.panel{margin-top:14px;background:#fff;border:1px solid var(--line);border-radius:12px;padding:12px}
canvas{display:block;width:100%;height:620px;border-radius:8px;background:#f9fbfc;touch-action:none}.legend{display:flex;align-items:center;gap:8px;margin-top:9px;color:var(--muted);font-size:.84rem}
.bar{height:12px;flex:1;max-width:340px;border-radius:8px;background:linear-gradient(90deg,#2864c7,#34b4c4,#eac33b,#d84336)}
 #readout{min-height:26px;margin-top:8px;font-size:.9rem}.notice{margin-top:12px;padding:10px 12px;background:#fff6d8;border-left:4px solid #d39b00;font-size:.88rem}.screening-watermark{margin:0 0 12px;padding:10px 14px;border:2px solid #d39b00;border-radius:8px;background:#fff6d8;color:#704f00;font-weight:800;text-align:center}.screening-watermark[hidden]{display:none}
.case-health{margin-top:14px;border:1px solid var(--line);border-left:6px solid #778895;border-radius:12px;padding:14px;background:#fff}.case-health h2{font-size:1.05rem;margin:0 0 8px}.case-health p{margin:5px 0;line-height:1.5}.case-health.design-citable{background:#e8f6ed;border-left-color:#207245}.case-health.screening-only,.case-health.not-evaluated{background:#fff6d8;border-left-color:#d39b00}.case-health.citation-blocked,.case-health.missing{background:#fff0ee;border-left-color:#b64032}.case-health .status{font-weight:700}details.evidence{margin-top:10px;border:1px solid var(--line);border-radius:9px;padding:9px 11px;background:#f8fafb}details.evidence summary{cursor:pointer;color:var(--accent);font-weight:600}details.evidence pre{white-space:pre-wrap;word-break:break-word;font-size:.76rem;color:#52636f}
@media(max-width:700px){canvas{height:470px}.top{align-items:stretch}.controls{width:100%}}@media print{.screening-watermark{display:block;position:relative;break-inside:avoid;page-break-after:avoid}}
</style></head><body><main>
<div id="screeningWatermark" class="screening-watermark" hidden>초기안 비교용 · 설계 인용 불가</div>
<div class="top"><div><h1>상세 열·부력 단면 결과</h1><p class="sub" id="meta">결과를 불러오는 중…</p></div>
 <div class="controls">
 <button id="designRun" type="button">3.0 교환시간까지 자동 계산</button>
 <button id="view2" class="active" type="button">2D 단면</button><button id="view3" type="button">3D 세 단면</button>
 <label>축<select id="axis"><option value="x">X 중앙</option><option value="y">Y 중앙</option><option value="z" selected>Z 중앙</option></select></label>
 <label>표시값<select id="field"><option value="T">온도 (K)</option><option value="speed">속도 (m/s)</option></select></label>
</div></div>
<section id="caseHealth" class="case-health missing"><h2>Case Health</h2>
<p class="status" id="healthStatus">증적 상태를 확인하는 중입니다.</p>
<p id="healthImpact"></p><p id="healthBlockers"></p><p id="healthActions"></p></section>
<details id="evidenceDetails" class="evidence"><summary>근거 보기</summary><pre id="rawEvidence"></pre></details>
<div class="panel"><canvas id="plot" role="img" aria-label="비정형 CFD 단면 결과"></canvas>
<div class="legend"><span id="lo">-</span><span class="bar" aria-hidden="true"></span><span id="hi">-</span><span id="unit"></span></div>
<div id="readout" aria-live="polite">셀 위에 마우스를 올리면 좌표와 값을 확인할 수 있습니다.</div></div>
<div class="notice">좌표 기반 cell 중심 표본입니다. 계산 유동 교환시간을 채우기 전에는 설계 확정 근거로 사용하지 마세요.</div>
</main><script>
const CASE=__CASE_JSON__, canvas=document.getElementById('plot'), ctx=canvas.getContext('2d');
const axisEl=document.getElementById('axis'),fieldEl=document.getElementById('field'),readout=document.getElementById('readout');
let payload=null,mode='2d',marks=[],baseMeta='',designComplete=false,fttComplete=false,legacyResultGate={};
const val=s=>fieldEl.value==='T'?Number(s.temperature_k):Number(s.speed_m_s);
const unit=()=>fieldEl.value==='T'?'K':'m/s';
function color(v,lo,hi){const t=hi>lo?Math.max(0,Math.min(1,(v-lo)/(hi-lo))):.5;const h=220*(1-t);return `hsl(${h} 68% 50%)`}
function size(){const d=Math.min(devicePixelRatio||1,2),r=canvas.getBoundingClientRect();canvas.width=Math.max(320,Math.round(r.width*d));canvas.height=Math.round(r.height*d);ctx.setTransform(d,0,0,d,0,0);return {w:r.width,h:r.height}}
function ranges(samples){const vs=samples.map(val).filter(Number.isFinite);return {lo:Math.min(...vs),hi:Math.max(...vs)}}
function legend(r){document.getElementById('lo').textContent=r.lo.toFixed(fieldEl.value==='T'?2:3);document.getElementById('hi').textContent=r.hi.toFixed(fieldEl.value==='T'?2:3);document.getElementById('unit').textContent=unit()}
function axesFor(a){return a==='x'?[1,2]:a==='y'?[0,2]:[0,1]}
function draw2(){const {w,h}=size(),slice=payload.slices[axisEl.value],samples=slice.samples||[],r=ranges(samples),pad=42,axes=axesFor(axisEl.value);legend(r);marks=[];ctx.clearRect(0,0,w,h);
 const xs=samples.map(s=>s.centre_m[axes[0]]),ys=samples.map(s=>s.centre_m[axes[1]]),xmin=Math.min(...xs),xmax=Math.max(...xs),ymin=Math.min(...ys),ymax=Math.max(...ys);
 ctx.strokeStyle='#aab9c4';ctx.lineWidth=1;ctx.strokeRect(pad,pad,w-pad*1.5,h-pad*1.7);ctx.fillStyle='#52636f';ctx.font='12px Segoe UI';ctx.fillText(['x','y','z'][axes[0]]+' (m)',w/2,h-10);ctx.save();ctx.translate(13,h/2);ctx.rotate(-Math.PI/2);ctx.fillText(['x','y','z'][axes[1]]+' (m)',0,0);ctx.restore();
 samples.forEach(s=>{const x=pad+(s.centre_m[axes[0]]-xmin)/Math.max(xmax-xmin,1e-9)*(w-pad*2),y=h-pad*1.2-(s.centre_m[axes[1]]-ymin)/Math.max(ymax-ymin,1e-9)*(h-pad*2.2);ctx.fillStyle=color(val(s),r.lo,r.hi);ctx.fillRect(x-3,y-3,6,6);marks.push({x,y,s})});
 ctx.fillStyle='#52636f';ctx.fillText(`${axisEl.value.toUpperCase()}=${Number(slice.target_m).toFixed(3)} m · ${samples.length.toLocaleString()} cells`,pad,20)}
function draw3(){const {w,h}=size(),samples=Object.values(payload.slices).flatMap(s=>s.samples||[]),r=ranges(samples);legend(r);marks=[];ctx.clearRect(0,0,w,h);const b=payload.summary.bounds_m,min=b.minimum,max=b.maximum,c=[0,1,2].map(i=>(min[i]+max[i])/2),span=Math.max(max[0]-min[0],max[1]-min[1],max[2]-min[2]),scale=Math.min(w*.72,h*.66)/Math.max(span,1e-9);
 const proj=p=>({x:w/2+((p[0]-c[0])-(p[1]-c[1]))*scale*.58,y:h*.58-(p[2]-c[2])*scale+((p[0]-c[0])+(p[1]-c[1]))*scale*.25});
 const corners=[];for(const x of [min[0],max[0]])for(const y of [min[1],max[1]])for(const z of [min[2],max[2]])corners.push([x,y,z]);const edge=[[0,1],[0,2],[0,4],[1,3],[1,5],[2,3],[2,6],[3,7],[4,5],[4,6],[5,7],[6,7]];ctx.strokeStyle='#aab9c4';edge.forEach(e=>{const a=proj(corners[e[0]]),b=proj(corners[e[1]]);ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke()});
 samples.sort((a,b)=>(a.centre_m[0]+a.centre_m[1]+a.centre_m[2])-(b.centre_m[0]+b.centre_m[1]+b.centre_m[2])).forEach(s=>{const p=proj(s.centre_m);ctx.fillStyle=color(val(s),r.lo,r.hi);ctx.globalAlpha=.62;ctx.fillRect(p.x-2,p.y-2,4,4);marks.push({x:p.x,y:p.y,s})});ctx.globalAlpha=1;ctx.fillStyle='#52636f';ctx.font='12px Segoe UI';ctx.fillText(`X/Y/Z 중앙 단면 · ${samples.length.toLocaleString()} samples`,20,22)}
function draw(){if(!payload)return;mode==='2d'?draw2():draw3()}
function renderCaseHealth(health,review){const node=document.getElementById('caseHealth'),status=health&&health.citation_status||'NOT_AVAILABLE',approved=review&&review.status==='APPROVED';document.getElementById('screeningWatermark').hidden=status!=='SCREENING_ONLY';const classes={DESIGN_CITABLE:'case-health design-citable',SCREENING_ONLY:'case-health screening-only',NOT_EVALUATED:'case-health not-evaluated',CITATION_BLOCKED:'case-health citation-blocked',NOT_AVAILABLE:'case-health missing'};node.className=(status==='DESIGN_CITABLE'&&!approved)?classes.NOT_EVALUATED:(classes[status]||classes.NOT_AVAILABLE);if(!health){healthStatus.textContent='CASE_EVIDENCE_NOT_FOUND · 설계 인용 불가';healthImpact.textContent='현재 Case Evidence를 찾을 수 없어 결과의 사용 범위를 판단할 수 없습니다.';healthBlockers.textContent='차단 사유: CASE_EVIDENCE_NOT_FOUND';healthActions.textContent='다음 조치: 현재 케이스의 Case Evidence를 다시 생성하세요.';return}const design=health.checks.design_ready,errors=Array.isArray(health.errors)?health.errors:[],codes=errors.map(x=>x.code).filter(Boolean);healthStatus.textContent=`${status} · 원본 검사 ${health.status||design.status||'NOT_EVALUATED'} · 목적 ${health.purpose||'확인 불가'}`;healthImpact.textContent='사용 범위: '+(health.checks.design_ready.impact||'현재 증적 상태를 확인하세요.');healthBlockers.textContent='차단 사유: '+(codes.join(', ')||'없음');healthActions.textContent='다음 조치: '+((health.checks.design_ready.next_actions||[]).join(' · ')||'현재 증적과 검토 기록을 함께 보관하세요.')}
function renderResultGate(gate){legacyResultGate=gate||{}}
function renderEvidence(j){rawEvidence.textContent=JSON.stringify({evidence:j.case_health&&j.case_health.evidence,review_summary:j.review_summary,legacy_result_gate:legacyResultGate,numerical_quality:j.run_manifest&&j.run_manifest.numerical_quality,thermal_progress:j.run_manifest&&j.run_manifest.thermal_progress},null,2)}
 function selectMode(next){mode=next;document.getElementById('view2').classList.toggle('active',next==='2d');document.getElementById('view3').classList.toggle('active',next==='3d');axisEl.disabled=next==='3d';draw()}
document.getElementById('view2').onclick=()=>selectMode('2d');document.getElementById('view3').onclick=()=>selectMode('3d');axisEl.onchange=draw;fieldEl.onchange=draw;window.addEventListener('resize',draw);
canvas.addEventListener('pointermove',e=>{if(!marks.length)return;const r=canvas.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;let best=null,d=144;for(const m of marks){const q=(m.x-x)**2+(m.y-y)**2;if(q<d){best=m;d=q}}if(!best)return;const s=best.s,p=s.centre_m;readout.textContent=`좌표 (${p.map(v=>Number(v).toFixed(3)).join(', ')}) m · 온도 ${Number(s.temperature_k).toFixed(3)} K · 속도 ${Number(s.speed_m_s).toFixed(3)} m/s`});
function renderDesign(j){const f=Number(j.flow_through_fraction||0),state=j.runtime_state||'idle';fttComplete=j.design_ready&&f>=3;designComplete=fttComplete&&payload&&payload.case_health&&payload.case_health.citation_status==='DESIGN_CITABLE'&&payload.review_summary&&payload.review_summary.status==='APPROVED';designRun.disabled=state==='queued'||state==='running';designRun.textContent=designComplete?'설계 검토 인용 가능 · 결과 새로고침':fttComplete?'3.0 FTT 계산 완료 · 설계 인용 전 증거 확인 필요':state==='running'?`자동 계산 중 · ${f.toFixed(2)} / 3.00 FTT`:state==='queued'?'자동 계산 대기 중':`3.0 교환시간까지 자동 계산 · ${f.toFixed(2)} FTT`;document.getElementById('meta').textContent=baseMeta+(baseMeta?' · ':'')+`유동 교환시간 ${f.toFixed(2)} / 3.00`}
async function pollDesign(){try{const r=await fetch('/api/field-design-status/'+encodeURIComponent(CASE)),j=await r.json();if(j.ok)renderDesign(j)}catch(e){}}
designRun.onclick=async()=>{if(designComplete||fttComplete){location.reload();return}if(!confirm('현재 체크포인트에서 최소 3.0 유동 교환시간까지 자동으로 이어 계산합니다. 계산 중 창을 닫아도 작업은 계속됩니다. 시작할까요?'))return;designRun.disabled=true;try{const r=await fetch('/api/start-field-design-run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({case:CASE})}),j=await r.json();if(!r.ok||!j.ok)throw Error(j.error||'자동 계산 시작 실패');pollDesign()}catch(e){readout.textContent=e.message;designRun.disabled=false}};
fetch('/api/body-results/'+encodeURIComponent(CASE)).then(r=>r.json()).then(j=>{if(!j.ok)throw Error(j.error||'결과 없음');payload=j;const s=j.summary;baseMeta=`${CASE} · ${Number(s.time_s).toFixed(3)} s · ${Number(s.cell_count).toLocaleString()} cells`;document.getElementById('meta').textContent=baseMeta;renderCaseHealth(j.case_health,j.review_summary);renderResultGate(j.result_gate);renderEvidence(j);draw();pollDesign()}).catch(e=>{document.getElementById('meta').textContent=e.message;readout.textContent='결과를 표시하지 못했습니다.'});setInterval(pollDesign,5000);
</script></body></html>"""


# ── 대시보드 페이지 (자립 HTML/JS, 리포트와 같은 시각 언어) ──────────────────

PAGE_DASH = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MEP CFD Studio</title>
<style>
 :root{--accent:#2c5f8a;--line:#e2e2e2;--muted:#666}
 *{box-sizing:border-box}
 body{font-family:'Malgun Gothic','Segoe UI',sans-serif;margin:0;background:#f0f2f5;color:#1a1a1a}
 .wrap{max-width:1280px;margin:18px auto;padding:0 16px}
 .hdr{display:flex;align-items:center;gap:14px;background:#fff;padding:14px 20px;border-radius:10px;box-shadow:0 1px 6px rgba(0,0,0,.07)}
 .hdr h1{font-size:19px;margin:0;color:var(--accent);white-space:nowrap}
 .hdr .root{color:var(--muted);font-size:12px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .btn{background:var(--accent);color:#fff;border:none;border-radius:8px;padding:9px 16px;font-size:14px;cursor:pointer;text-decoration:none;display:inline-block;white-space:nowrap}
 .btn.sec{background:#fff;color:var(--accent);border:1px solid var(--accent)}
 .cards{display:flex;gap:12px;margin:14px 0;flex-wrap:wrap}
 .card{flex:1;min-width:140px;background:#fff;border-radius:10px;padding:13px 18px;box-shadow:0 1px 6px rgba(0,0,0,.07)}
 .card .n{font-size:26px;font-weight:700}
 .card .l{color:var(--muted);font-size:12.5px}
 .tblwrap{background:#fff;border-radius:10px;box-shadow:0 1px 6px rgba(0,0,0,.07);overflow-x:auto}
 table{width:100%;border-collapse:collapse;font-size:13.5px;min-width:1230px}
 th,td{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
 th{background:#fafafa;cursor:pointer;user-select:none;font-weight:600}
 th:hover{color:var(--accent)}
 th .arr{font-size:10px;color:var(--accent)}
 td.num,th.num{text-align:right}
 .badge{display:inline-block;color:#fff;border-radius:12px;padding:2px 10px;font-size:12px;font-weight:600}
 tr.rowwarn td{background:#fdecea}
 tr.rowopening td{background:#fff8e7}
 .opening-review{display:block;white-space:normal;line-height:1.4;min-width:205px}
 .opening-review.warn{color:#8a5a00;font-weight:600}
 .opening-review small{display:block;color:#735015;font-weight:400;margin-top:3px}
 .opening-review.ok{color:#207245;font-weight:600}
 .fbar{margin:0 0 10px;font-size:13px;color:#555;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
 .fbar label{display:inline-flex;gap:5px;align-items:center;cursor:pointer;user-select:none}
 .fbar .hid{color:#c0392b;font-weight:600}
 .empty{background:#fff;border-radius:10px;padding:56px 20px;text-align:center;color:var(--muted);box-shadow:0 1px 6px rgba(0,0,0,.07)}
 .empty .steps{font-size:15px;margin:14px 0 22px}
 a.rep{color:var(--accent);font-weight:600;text-decoration:none}
 a.rep:hover{text-decoration:underline}
 a.del{color:#c0392b}
 .foot{color:var(--muted);font-size:11.5px;margin:14px 2px}
 .strip{background:#eaf2f8;border:1px solid #aed6f1;border-radius:10px;padding:10px 16px;margin:0 0 12px;font-size:13.5px}
 .strip.err{background:#fdecea;border-color:#f5b7b1;color:#922b21}
 .strip.ok{background:#eaf7ee;border-color:#a9dfbf;color:#196f3d}
 .strip.warn{background:#fef9e7;border-color:#f7dc6f;color:#7d6608}
 .envrow{display:flex;align-items:center;gap:10px;justify-content:space-between;flex-wrap:wrap}
 .mini{background:#fff;border:1px solid currentColor;color:inherit;border-radius:7px;padding:5px 10px;cursor:pointer;font-size:12px}
 .strip .bar{background:#d6eaf8;border-radius:6px;height:9px;margin:7px 0;overflow:hidden}
 .strip .fill{background:var(--accent);height:100%;transition:width .5s}
 .strip pre{background:#1e2a33;color:#d5e8f5;border-radius:6px;padding:8px 10px;font-size:11.5px;max-height:220px;overflow:auto;white-space:pre-wrap}
 .strip summary{cursor:pointer;color:var(--accent);font-size:12.5px}
 .ov{position:fixed;inset:0;background:rgba(20,30,40,.5);display:none;align-items:center;justify-content:center;z-index:60}
 .ovbox{background:#fff;border-radius:12px;padding:16px 20px;max-width:94vw;max-height:92vh;overflow:auto;box-shadow:0 6px 30px rgba(0,0,0,.25)}
 .ovhdr{display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap}
 .ovhdr b{color:var(--accent);font-size:15px}
 .ovhdr select,.ovhdr input[type=range]{font-size:13px}
 .ovhdr .x{margin-left:auto;background:none;border:none;font-size:18px;cursor:pointer;color:#666}
 #vwcv{border:1px solid var(--line);border-radius:6px;cursor:crosshair}
 #vwread{font-size:13px;color:#333;margin-top:8px;min-height:18px}
 .cmpbar{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);background:var(--accent);color:#fff;
  border-radius:24px;padding:10px 22px;box-shadow:0 4px 16px rgba(0,0,0,.3);display:none;z-index:55;font-size:14px}
 .cmpbar button{background:#fff;color:var(--accent);border:none;border-radius:14px;padding:5px 14px;margin-left:12px;cursor:pointer;font-weight:600}
 #cmptbl{border-collapse:collapse;font-size:13.5px;min-width:480px}
 #cmptbl th,#cmptbl td{padding:8px 14px;border-bottom:1px solid var(--line);text-align:right}
 #cmptbl th:first-child,#cmptbl td:first-child{text-align:left;background:#fafafa;font-weight:600}
 .best{color:#1e8449;font-weight:700}
</style></head><body><div class="wrap">
 <div class="hdr">
  <h1>MEP CFD Studio</h1>
 <div class="root" id="root">…</div>
 <button class="btn sec" onclick="load()">새로고침</button>
 <a class="btn sec" href="/field-run">현장 자동 해석</a>
 <a class="btn sec" href="/body-gci">메시 독립성</a>
 <a class="btn sec" href="/release-readiness">출시 준비</a>
 <a class="btn" href="/new">＋ 새 해석</a>
 </div>
 <div class="cards" id="cards"></div>
 <div id="filterbar"></div>
 <div id="strip"></div>
 <div id="main"></div>
 <div class="foot">도면→OpenFOAM CFD 파이프라인 · 지표: 평균/최고 온도, 급기 대비 ΔT,
  에너지 폐합율(주입열=배기열, 90~110% 정상), GCI(격자 오차, ≤5% 신뢰)</div>
</div>

<div id="vwov" class="ov" onclick="if(event.target===this)vwClose()">
 <div class="ovbox">
  <div class="ovhdr">
   <b id="vwtitle"></b>
   <select id="vwmode" onchange="vwMode()"><option value="2d">2D 단면</option><option value="3d">3D 컷플레인</option></select>
   <select id="vwfield" onchange="fieldCh()"><option value="T">온도</option><option value="U">유속</option></select>
   <select id="vwaxis" onchange="vwAxis()"><option value="z">수평면(Z)</option><option value="y">수직면(Y)</option><option value="x">수직면(X)</option></select>
   <input type="range" id="vwidx" style="width:170px" oninput="vwFetch()">
   <span id="vwpos" style="font-size:13px;color:#444;min-width:88px"></span>
   <label style="font-size:13px" id="vwveclb"><input type="checkbox" id="vwvec" onchange="vwFetch()"> 기류 화살표</label>
   <button class="x" onclick="vwClose()">✕</button>
  </div>
  <div id="vw2d" style="display:flex;gap:12px;align-items:flex-start">
   <canvas id="vwcv" width="640" height="430" onmousemove="vwHover(event)" onmouseleave="vwread.textContent=''"></canvas>
   <canvas id="vwcb" width="52" height="430"></canvas>
  </div>
  <div id="vw3d" style="display:none">
   <div style="display:flex;gap:12px;align-items:flex-start">
     <canvas id="cv3d" width="640" height="430" style="border:1px solid var(--line);border-radius:6px"></canvas>
     <canvas id="vwcb3" width="52" height="430"></canvas>
    </div>
    <div style="font-size:13px;margin-top:8px">
     <span id="vwcuts">절단면 X <input type="range" id="s3x" style="width:140px" oninput="upd3&&upd3('x',this.value)">
      Y <input type="range" id="s3y" style="width:140px" oninput="upd3&&upd3('y',this.value)">
      Z(높이) <input type="range" id="s3z" style="width:140px" oninput="upd3&&upd3('z',this.value)"></span>
     <span id="vwhint" style="color:#666">· 드래그=회전 · 휠=줌 · 파랑=급기 · 빨강=배기</span>
   </div>
  </div>
  <div id="vwread"></div>
 </div>
</div>

<div id="cmpov" class="ov" onclick="if(event.target===this)cmpov.style.display='none'">
 <div class="ovbox"><div class="ovhdr"><b>케이스 비교</b><button class="x" onclick="cmpov.style.display='none'">✕</button></div>
  <div id="cmpbody"></div></div>
</div>
<div id="cmpbar" class="cmpbar"><span id="cmpn"></span><button onclick="openCompare()">선택 비교 →</button></div>

<script>
let CASES=[], KEY='mtime', ASC=false;
const SEL=new Set();
function hesc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
const COLS=[
 {k:'_sel',t:''},
 {k:'name',t:'케이스명'},
 {k:'room',t:'방 L×W×H (m)'},
 {k:'cells',t:'셀',num:1},
 {k:'heat_label',t:'발열'},
 {k:'supply_u',t:'CFD 적용 급기 m/s',num:1,dec:2},
 {k:'design_supply_u',t:'설계 면적 급기 m/s',num:1,dec:2},
 {k:'opening_review',t:'개구부 사전검증'},
 {k:'T_avg_C',t:'평균T ℃',num:1,dec:1},
 {k:'T_max_C',t:'최고T ℃',num:1,dec:1},
 {k:'dT_rise',t:'ΔT K',num:1,dec:1},
 {k:'closure_pct',t:'폐합 %',num:1,dec:0},
 {k:'gci_pct',t:'GCI %',num:1,dec:1},
 {k:'badge',t:'상태'},
 {k:'mtime',t:'날짜',num:1},
 {k:'_act',t:'동작'}
];
function fmt(c,v){
 if(v===null||v===undefined||v==='')return '—';
 if(c.k==='mtime'){const d=new Date(v*1000);return (d.getMonth()+1)+'/'+d.getDate()+' '+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');}
 if(c.k==='cells')return v.toLocaleString();
 if(c.dec!==undefined&&typeof v==='number')return v.toFixed(c.dec);
 return v;
}
// 인용 불가(에너지수지 미통과) 케이스 판별. 배지 문구가 바뀌어도 따라가도록
// 폐합율까지 같이 본다 — 예전엔 정규식이 옛 배지명만 알아서 '경고 0'으로 오표시됐다.
function legacyBadgeBlocked(c){
 const b=c.badge||'';
 if(/인용 불가|^미수렴|^발산/.test(b))return true;
 return c.closure_pct!=null&&(c.closure_pct<90||c.closure_pct>110);
}
// 상태 열 숨김은 보기 편의일 뿐이다. 케이스(행)는 그대로 두고 열만 감추며,
// 요약 카드의 '경고' 개수는 항상 전체를 세어 인용 불가 건이 잊히지 않게 한다.
let HIDESTATUS=localStorage.getItem('hideStatusCol')==='1';
function toggleStatusCol(v){HIDESTATUS=v;localStorage.setItem('hideStatusCol',v?'1':'0');render();}
function visibleCols(){return COLS.filter(c=>!(HIDESTATUS&&c.k==='badge'));}
function resultTrust(c){
 const status=c.citation_status||'NOT_EVALUATED',citable=c.citable===true,blockers=Array.isArray(c.blockers)?c.blockers:[];
 if(c.status==='created')return {status:'NOT_EVALUATED',citable:false,blockers:[],label:'미실행',color:'#7f8c8d'};
 if(status==='DESIGN_CITABLE'&&citable)return {status,citable,blockers,label:'설계 검토 인용 가능',color:'#207245'};
 if(status==='SCREENING_ONLY'&&citable)return {status,citable,blockers,label:'스크리닝 전용',color:'#b7791f'};
 return {status,citable:false,blockers,label:status==='FAIL'?'해석 실패':'결과 평가 보류',color:'#c0392b'};
}
function openingReview(c){
 if(c.opening_preflight_status!=='AVAILABLE')return null;
 const warningCount=Math.max(0,Number(c.opening_warning_count)||0);
 const unresolved=c.opening_resolution_ok!==true||c.jet_metrics_citable!==true;
 if(unresolved){
  const count=warningCount?`개구부 ${warningCount}개 `:'';
  return {warn:true,label:'제트/최대 유속 설계 판단 보류',
   detail:`${count}스냅 면적 기준 해상도를 개선해야 합니다. 열·에너지 스크리닝은 가능하지만 제트 도달거리와 최대 유속은 설계 판정에 사용하지 않습니다.`};
 }
 return {warn:false,label:'제트/최대 유속 검토 가능',detail:'설계 면적과 CFD 적용 면적의 사전검증을 통과했습니다.'};
}
function isBlocked(c){const trust=resultTrust(c);return c.status!=='created'&&(!trust.citable||trust.blockers.some(x=>x!=='screening_engine'));}
function cards(){
 const n=CASES.length;
 const design=CASES.filter(c=>resultTrust(c).status==='DESIGN_CITABLE'&&resultTrust(c).citable).length;
 const screening=CASES.filter(c=>resultTrust(c).status==='SCREENING_ONLY'&&resultTrust(c).citable).length;
 const warn=CASES.filter(isBlocked).length;
 const idle=CASES.filter(c=>c.status==='created').length;
 const openingWarn=CASES.filter(c=>{const review=openingReview(c);return review&&review.warn;}).length;
 document.getElementById('cards').innerHTML=
  card(n,'케이스','#2c5f8a')+card(design,'설계 인용 가능','#207245')+card(screening,'스크리닝 전용','#b7791f')+card(openingWarn,'개구부 검토','#b7791f')+card(warn,'결과 검토 필요','#c0392b')+card(idle,'미실행','#7f8c8d');
 const nb=warn;
 document.getElementById('filterbar').innerHTML=
  `<div class="fbar"><label><input type="checkbox" ${HIDESTATUS?'checked':''}
    onchange="toggleStatusCol(this.checked)"> 상태 열 숨기기</label>`
  + (HIDESTATUS&&nb?`<span class="hid">상태 열을 감췄습니다 — 검토 필요 ${nb}건은 그대로 있습니다(리포트에서 확인).</span>`:'')
  + `</div>`;
}
function card(n,l,col){return `<div class="card"><div class="n" style="color:${col}">${n}</div><div class="l">${l}</div></div>`}
function sortBy(k){ if(KEY===k)ASC=!ASC; else {KEY=k;ASC=(k==='name'||k==='room');} render(); }
function render(){
 cards();
 const main=document.getElementById('main');
 if(!CASES.length){
  main.innerHTML=`<div class="empty"><h3>아직 케이스가 없습니다</h3>
   <div class="steps">① ＋ 새 해석 (방·발열·급기 입력) → ② 실행 → ③ 리포트·결과 확인</div>
   <a class="btn" href="/new">＋ 새 해석 시작</a></div>`;
  return;
 }
 const arr=[...CASES].sort((a,b)=>{
  let x=a[KEY],y=b[KEY];
  if(x==null&&y==null)return 0; if(x==null)return 1; if(y==null)return -1;
  if(typeof x==='string')x=x.toLowerCase(),y=(y+'').toLowerCase();
  return (x<y?-1:x>y?1:0)*(ASC?1:-1);
 });
 const cols=visibleCols();
 let h='<div class="tblwrap"><table><thead><tr>';
 for(const c of cols){
  if(c.k==='_sel'){h+='<th></th>';continue;}
  const arrow=(KEY===c.k)?`<span class="arr">${ASC?'▲':'▼'}</span>`:'';
  h+=`<th class="${c.num?'num':''}" onclick="sortBy('${c.k}')">${c.t}${arrow}</th>`;
 }
 h+='</tr></thead><tbody>';
 for(const r of arr){
  const trust=resultTrust(r),warn=isBlocked(r),opening=openingReview(r);
  h+=`<tr class="${warn?'rowwarn':(opening&&opening.warn?'rowopening':'')}">`;
  for(const c of cols){
   if(c.k==='_sel'){
    h+=`<td><input type="checkbox" ${SEL.has(r.dir)?'checked':''} onchange="selCh('${encodeURIComponent(r.dir)}',this.checked)"></td>`;continue;
   }
   if(c.k==='badge'){h+=`<td><span class="badge" style="background:${trust.color}">${hesc(trust.label)}</span>${r.badge?`<small> ${hesc(r.badge)}</small>`:''}</td>`;continue;}
   if(c.k==='opening_review'){
    if(!opening){h+='<td>—</td>';continue;}
    h+=`<td><span class="opening-review ${opening.warn?'warn':'ok'}">${opening.warn?'⚠ ':''}${hesc(opening.label)}<small>${hesc(opening.detail)}</small></span></td>`;continue;
   }
    if(c.k==='_act'){
     const d=encodeURIComponent(r.dir);
     let a=[];
     a.push(`<a class="rep" href="#" onclick="openModel('${d}');return false">3D 모델</a>`);
     if(r.report)a.push(`<a class="rep" target="_blank" href="/case/${d}/report">리포트</a>`);
    if(r.status!=='created')a.push(`<a class="rep" href="#" onclick="openViewer('${d}');return false">결과</a>`);
    a.push(`<a class="rep" href="#" onclick="runCase('${d}');return false">${r.status==='created'?'실행':'재실행'}</a>`);
    if(r.status!=='created')a.push(`<a class="rep" href="#" title="격자 독립성 검증(3격자 배치 실행 → GCI)" onclick="gridCase('${d}');return false">격자</a>`);
    a.push(`<a class="rep del" href="#" onclick="delCase('${d}',this);return false">삭제</a>`);
    h+=`<td>${a.join(' · ')}</td>`;continue;
   }
   h+=`<td class="${c.num?'num':''}">${fmt(c,r[c.k])}</td>`;
  }
  h+='</tr>';
 }
 h+='</tbody></table></div>';
 main.innerHTML=h;
 cmpBar();
}
function selCh(d,on){
 const name=decodeURIComponent(d);
 if(on)SEL.add(name); else SEL.delete(name);
 cmpBar();
}
function cmpBar(){
 const bar=document.getElementById('cmpbar');
 const n=[...SEL].filter(s=>CASES.some(c=>c.dir===s)).length;
 bar.style.display=n>=2?'':'none';
 document.getElementById('cmpn').textContent=n+'개 선택됨';
}
const CMPROWS=[
 ['room','방 (m)',v=>v],['cells','셀',v=>v?v.toLocaleString():'—'],
 ['heat_label','발열',v=>v],['supply_u','대표 급기 m/s',v=>v],
 ['T_avg_C','평균T ℃',v=>v!=null?v.toFixed(1):'—'],
 ['T_max_C','최고T ℃',v=>v!=null?v.toFixed(1):'—','min'],
 ['dT_rise','ΔT K',v=>v!=null?v.toFixed(1):'—'],
 ['outlet_dT','배기 ΔT K',v=>v!=null?v.toFixed(2):'—'],
 ['closure_pct','폐합 %',v=>v!=null?v.toFixed(0)+'%':'—'],
 ['mass_err_pct','질량수지 %',v=>v!=null?(v>0?'+':'')+v.toFixed(1)+'%':'—'],
 ['n_supply','급기구 수',v=>v!=null?v:'—'],
 ['gci_pct','GCI %',v=>v!=null?v.toFixed(1)+'%':'—'],
 ['badge','상태',v=>v||'—'],
];
function openCompare(){
 const sel=CASES.filter(c=>SEL.has(c.dir)).slice(0,4);
 if(sel.length<2){alert('2개 이상 선택하세요');return}
 let h='<table id="cmptbl"><tr><th></th>'+sel.map(c=>`<th>${c.dir}</th>`).join('')+'</tr>';
 for(const [k,label,f,best] of CMPROWS){
  let bi=-1;
  if(best==='min'){
   let bv=Infinity;
   sel.forEach((c,i)=>{if(c[k]!=null&&c[k]<bv){bv=c[k];bi=i;}});
  }
  h+=`<tr><th>${label}</th>`+sel.map((c,i)=>`<td class="${i===bi?'best':''}">${f(c[k])}${i===bi?' ★':''}</td>`).join('')+'</tr>';
 }
 h+='<tr><th>리포트</th>'+sel.map(c=>`<td>${c.report?`<a class="rep" target="_blank" href="/case/${encodeURIComponent(c.dir)}/report">열기</a>`:'—'}</td>`).join('')+'</tr></table>';
 document.getElementById('cmpbody').innerHTML=h;
 document.getElementById('cmpov').style.display='flex';
}
// ── 결과 뷰어 (2D 단면: 슬라이더·호버·기류 화살표 / 3D 컷플레인은 모듈에서) ──
var VW=null, SL=null;   // var = window 프로퍼티(3D 모듈 스크립트가 접근)
const CSTOPS=[[0,[48,18,59]],[0.25,[40,187,236]],[0.5,[164,252,60]],[0.75,[251,126,33]],[1,[122,4,3]]];
function cmap(t){
 t=Math.max(0,Math.min(1,t));
 for(let i=1;i<CSTOPS.length;i++){
  if(t<=CSTOPS[i][0]){
   const [t0,c0]=CSTOPS[i-1],[t1,c1]=CSTOPS[i],f=(t-t0)/(t1-t0);
   return `rgb(${c0.map((v,k)=>Math.round(v+(c1[k]-v)*f)).join(',')})`;
  }
 }
 return 'rgb(122,4,3)';
}
async function openViewer(d){
 const name=decodeURIComponent(d);
 const r=await fetch('/api/fieldinfo/'+d);const j=await r.json();
 if(j.error){alert(j.error);return}
 VW={case:d,name,info:j,axis:'z',modelOnly:false};
 document.getElementById('vwtitle').textContent=name+' — 결과 뷰어';
 document.getElementById('vwread').textContent='';
 document.getElementById('vwfield').value='T';
 document.getElementById('vwaxis').value='z';
 document.getElementById('vwvec').checked=false;
 document.getElementById('vwmode').value='2d';
 vwMode();
 vwAxis();
 document.getElementById('vwov').style.display='flex';
}
async function openModel(d){
 const name=decodeURIComponent(d);
 const r=await fetch('/api/modelinfo/'+d);const j=await r.json();
 if(j.error){alert(j.error);return}
 VW={case:d,name,info:j,axis:'z',modelOnly:true};
 document.getElementById('vwtitle').textContent=name+' — 실행 전 3D 계산 모델';
 const q=j.model_quality&&j.model_quality.warning;
 const pf=j.opening_preflight||{}, terms=pf.terminals||[], ov=j.opening_verification||{};
 const pfnote=terms.length
  ?` · 단말 ${terms.length}개 사전검증: 제트/최대유속 ${pf.jet_metrics_citable?'사용 가능':'설계 판단 불가'}${(pf.result_required||[]).length?' · 배기 실제유량은 계산 후 확인':''}${ov.status?` · 경계면/phi 검증 ${ov.status}`:''}`:'';
 document.getElementById('vwread').textContent=q
  ?'⚠ '+q+'  ·  파랑=급기, 빨강=배기, 보라=장비, 갈색=기둥'
  :'이 형상이 실제 계산에 사용됩니다. 파랑=급기, 빨강=배기, 보라=장비, 갈색=기둥';
 if(pfnote)document.getElementById('vwread').textContent+=pfnote;
 document.getElementById('vwmode').value='3d';
 document.getElementById('vwov').style.display='flex';
 vwMode();
}
function vwClose(){document.getElementById('vwov').style.display='none';VW=null;}
function vwMode(){
 const modelOnly=!!(VW&&VW.modelOnly);
 const modeSel=document.getElementById('vwmode'),fieldSel=document.getElementById('vwfield');
 const m=modelOnly?'3d':modeSel.value;
 modeSel.style.display=modelOnly?'none':'';
 fieldSel.style.display=modelOnly?'none':'';
 document.getElementById('vw2d').style.display=m==='2d'?'flex':'none';
 document.getElementById('vw3d').style.display=m==='3d'?'':'none';
 for(const id of ['vwaxis','vwidx','vwpos','vwveclb'])
  document.getElementById(id).style.display=(m==='3d'||modelOnly)?'none':'';
 document.getElementById('vwcuts').style.display=modelOnly?'none':'';
 document.getElementById('vwcb3').style.display=modelOnly?'none':'';
 if(m==='3d'&&VW){
  if(!modelOnly){
   for(const [id,n] of [['s3x',VW.info.nx],['s3y',VW.info.ny],['s3z',VW.info.nz]]){
    const s=document.getElementById(id);s.min=0;s.max=n-1;s.value=Math.round(n/2);
   }
  }
  if(window.init3D)init3D(); else document.getElementById('vwread').textContent='3D 모듈 로딩 중… 잠시 후 다시';
 }
}
function fieldCh(){
 vwFetch();
 if(document.getElementById('vwmode').value==='3d'&&window.init3D)init3D();
}
function vwAxis(){
 if(!VW)return;
 VW.axis=document.getElementById('vwaxis').value;
 const n={z:VW.info.nz,y:VW.info.ny,x:VW.info.nx}[VW.axis];
 const s=document.getElementById('vwidx');
 s.min=0;s.max=n-1;s.value=Math.round(n/2);
 vwFetch();
}
async function vwFetch(){
 if(!VW)return;
 const f=document.getElementById('vwfield').value;
 const vec=document.getElementById('vwvec').checked?1:0;
 const idx=document.getElementById('vwidx').value;
 const r=await fetch(`/api/slice/${VW.case}?field=${f}&axis=${VW.axis}&idx=${idx}&vec=${vec}`);
 SL=await r.json();
 if(SL.error){document.getElementById('vwread').textContent='⚠ '+SL.error;return}
 const axisName={z:'높이 z',y:'y',x:'x'}[VW.axis];
 document.getElementById('vwpos').textContent=`${axisName} = ${SL.pos} m`;
 vwDraw();
}
function vwRange(){
 const f=document.getElementById('vwfield').value;
 return f==='T'?[VW.info.Tmin,VW.info.Tmax]:[0,VW.info.Umax];
}
function vwDraw(){
 const cv=document.getElementById('vwcv'),ctx=cv.getContext('2d');
 const d=SL.data,ny=d.length,nx=d[0].length;
 const scale=Math.min(660/SL.w,430/SL.h);
 cv.width=Math.max(220,Math.round(SL.w*scale));
 cv.height=Math.max(160,Math.round(SL.h*scale));
 const cw=cv.width/nx,chh=cv.height/ny;
 const [mn,mx]=vwRange(),rg=(mx-mn)||1;
 for(let j=0;j<ny;j++)for(let i=0;i<nx;i++){
  ctx.fillStyle=(SL.mask&&SL.mask[j][i])?'#b8bcc0':cmap((d[j][i]-mn)/rg);
  ctx.fillRect(i*cw,cv.height-(j+1)*chh,cw+0.7,chh+0.7);
 }
 // 실형상 윤곽(수평면에서만: 방 폴리곤 + 장애물 footprint)
 if(SL.axis==='z'&&VW.info.outlines){
  const sx=cv.width/SL.w, sy=cv.height/SL.h;
  const drawPoly=(poly,col,wd)=>{
   ctx.strokeStyle=col;ctx.lineWidth=wd;ctx.beginPath();
   poly.forEach((p,n)=>{const X=p[0]*sx,Y=cv.height-p[1]*sy;n?ctx.lineTo(X,Y):ctx.moveTo(X,Y);});
   ctx.closePath();ctx.stroke();
  };
  if(VW.info.outlines.room)drawPoly(VW.info.outlines.room,'#1a2733',2);
  for(const o of (VW.info.outlines.obstacles||[]))
   drawPoly(o.poly,o.kind==='column'?'#5d4037':'#8e44ad',1.6);
 }
 if(SL.vx&&document.getElementById('vwvec').checked){
  ctx.strokeStyle='rgba(255,255,255,.85)';ctx.fillStyle='rgba(255,255,255,.85)';ctx.lineWidth=1.1;
  const st=Math.max(1,Math.round(nx/20)),um=VW.info.Umax||1,len=Math.min(cw,chh)*2.1;
  for(let j=0;j<ny;j+=st)for(let i=0;i<nx;i+=st){
   const vx=SL.vx[j][i]/um*len,vy=SL.vy[j][i]/um*len;
   if(Math.abs(vx)<0.5&&Math.abs(vy)<0.5)continue;
   const x0=(i+0.5)*cw,y0=cv.height-(j+0.5)*chh;
   const x1=x0+vx,y1=y0-vy;
   ctx.beginPath();ctx.moveTo(x0,y0);ctx.lineTo(x1,y1);ctx.stroke();
   const a=Math.atan2(y1-y0,x1-x0);
   ctx.beginPath();ctx.moveTo(x1,y1);
   ctx.lineTo(x1-4*Math.cos(a-0.45),y1-4*Math.sin(a-0.45));
   ctx.lineTo(x1-4*Math.cos(a+0.45),y1-4*Math.sin(a+0.45));
   ctx.fill();
  }
 }
 // 컬러바
 const cb=document.getElementById('vwcb'),c2=cb.getContext('2d');
 cb.height=cv.height;
 c2.clearRect(0,0,cb.width,cb.height);
 for(let y=0;y<cb.height;y++){
  c2.fillStyle=cmap(1-y/cb.height);
  c2.fillRect(0,y,20,1);
 }
 c2.fillStyle='#333';c2.font='11px sans-serif';
 const unit=document.getElementById('vwfield').value==='T'?'℃':'m/s';
 c2.fillText(mx.toFixed(1),23,10);
 c2.fillText(((mn+mx)/2).toFixed(1),23,cb.height/2+4);
 c2.fillText(mn.toFixed(1),23,cb.height-2);
 c2.fillText(unit,23,cb.height/2+18);
}
function vwHover(ev){
 if(!SL||!SL.data)return;
 const cv=document.getElementById('vwcv'),rect=cv.getBoundingClientRect();
 const d=SL.data,ny=d.length,nx=d[0].length;
 const i=Math.min(nx-1,Math.max(0,Math.floor((ev.clientX-rect.left)/rect.width*nx)));
 const j=Math.min(ny-1,Math.max(0,ny-1-Math.floor((ev.clientY-rect.top)/rect.height*ny)));
 const px=((i+0.5)*SL.w/nx).toFixed(2),py=((j+0.5)*SL.h/ny).toFixed(2);
 const unit=document.getElementById('vwfield').value==='T'?'℃':'m/s';
 const val=(SL.mask&&SL.mask[j][i])?'고체(벽/장애물)':`${d[j][i]} ${unit}`;
 document.getElementById('vwread').textContent=`${SL.hx}=${px} m, ${SL.hy}=${py} m  →  ${val}`;
}
async function gridCase(d){
 const name=decodeURIComponent(d);
 if(!confirm(name+' 격자 독립성 검증을 실행할까요? (셀 크기 3종을 배치 실행 — 수 분 소요, GCI 배지가 표에 추가됩니다)'))return;
 const r=await fetch('/api/grid/'+d,{method:'POST'});
 const j=await r.json();
 if(j.error)alert(j.error);
}
async function runCase(d){
 const r=await fetch('/api/run/'+d,{method:'POST'});
 const j=await r.json();
 if(j.error)alert(j.error);
 pollNow=true;
}
async function delCase(d){
 const name=decodeURIComponent(d);
 if(!confirm(name+' 케이스 폴더를 삭제할까요? (되돌릴 수 없음)'))return;
 const r=await fetch('/api/delete/'+d,{method:'POST'});
 const j=await r.json();
 if(j.error)alert(j.error); else load();
}
function acceptanceHtml(a){
 if(!a||!Object.keys(a).length)return `<div style="margin-top:7px">실제 계산 수용 테스트: 아직 실행하지 않음</div>`;
 if(a.status==='queued')return `<div style="margin-top:7px">⏳ 실제 계산 수용 테스트가 대기 중입니다.</div>`;
 if(a.status==='running')return `<div style="margin-top:7px">▶ 실제 계산 수용 테스트를 실행하고 있습니다.</div>`;
 if(a.status==='passed'){
  const when=a.finished_at?new Date(a.finished_at).toLocaleString():'';
  return `<div style="margin-top:7px">✅ 실제 계산 통과 · Mesh OK · 64셀 · latest time ${hesc(a.latest_time)}${when?' · '+hesc(when):''}</div>`;
 }
 if(a.status==='failed')return `<div style="margin-top:7px;color:#922b21">❌ 실제 계산 실패: ${hesc(a.error||'원인 미확인')}</div>`;
 return '';
}
function environmentHtml(e,f,a,mpiJob){
 if(!e||!Object.keys(e).length){
  return `<div class="strip err">⚠ 계산 환경을 아직 확인하지 못했습니다. 스튜디오를 다시 시작하세요.</div>`;
 }
 const bodyReady=!!(e.thermal_detailed_ready&&f&&f.ok);
 const cls=e.ok?(bodyReady?'ok':'warn'):'err';
 const icon=e.ok?'✅':'⚠';
 const distro=hesc(e.distro||'WSL 배포판 없음');
 const version=hesc(e.version||e.package_version||'버전 미확인');
 const body=bodyReady?' · 실제 형상 열유동 엔진 준비':' · 실제 형상 열유동 환경 일부 미확인';
 const missing=[...(e.missing_runtime_commands||[]),...(e.missing_body_fitted_commands||[])];
 const tools=Object.entries(e.commands||{}).map(([k,v])=>`${hesc(k)} ${v?'✓':'✕'}`).join(' · ');
 const reason=String(e.reason_code||'').trim();
 const diagnostic=reason?`<div style="margin-top:6px">진단 코드: <code>${hesc(reason)}</code></div>`:'';
 const safety=reason==='WSL_ACCESS_DENIED'
  ?`<div style="margin-top:6px">안전을 위해 OpenFOAM 실행과 MPI 병렬 재점검을 시작하지 않았습니다.</div>`:'';
 const help=e.fix?`<div style="margin-top:6px">해결 방법: ${hesc(e.fix)}</div>`:'';
 const miss=missing.length?`<div style="margin-top:6px">누락: ${hesc(missing.join(', '))}</div>`:'';
 const mpi=e.mpi_runtime_evidence||{};
 const mpiStatus=String(mpi.status||e.mpi_execution_smoke||'NOT_RUN');
 const mpiReason=mpi.reason_code||e.mpi_runtime_reason_code||'';
 const mpiPath=mpi.path||'';
 const mpiBusy=!!(mpiJob&&(mpiJob.state==='queued'||mpiJob.state==='running'));
 const mpiLine=mpiStatus==='PASS'
   ?`<div style="margin-top:6px;color:#176b3a">MPI 병렬 스모크: PASS — 검증된 rank 범위에서만 사용 가능</div>`
   :mpiStatus==='BLOCKED'
     ?`<div style="margin-top:6px;color:#922b21">MPI 병렬: 차단 (${hesc(mpiReason||'rank spawn 검증 실패')}) — 현재는 직렬 해석만 사용합니다.</div>`
     :`<div style="margin-top:6px;color:#805d00">MPI 병렬 스모크: 미실행 — 현재는 직렬 해석만 사용합니다.</div>`;
 const mpiEvidence=mpiPath?`<div style="margin-top:4px;color:#555">MPI 증거: ${hesc(mpiPath)}</div>`:'';
 const fc=f||{};
 const fcVersion=fc.ok?`FreeCAD ${hesc(fc.freecad_version||'?')} · OCC ${hesc(fc.occ_version||'?')}`:
   hesc(fc.summary||'FreeCAD 환경 미확인');
 const fcPath=fc.executable?`<div style="margin-top:5px;color:#555">${hesc(fc.executable)}</div>`:'';
 const fcHelp=(!fc.ok&&fc.fix)?`<div style="margin-top:6px">FreeCAD 해결 방법: ${hesc(fc.fix)}</div>`:'';
 const manifest=e.manifest_path?`<div style="margin-top:5px;color:#555">진단 기록: ${hesc(e.manifest_path)}</div>`:'';
 const busy=a&&(a.status==='queued'||a.status==='running');
 return `<div class="strip ${cls}"><div class="envrow"><span>${icon} <b>${hesc(e.summary||'환경 진단')}</b> · ${distro} · OpenFOAM ${version}${body}</span>`+
   `<span style="display:flex;gap:7px"><button class="mini" onclick="refreshEnv(this)">환경 다시 검사</button>`+
   `<button class="mini" ${(!e.ok||busy)?'disabled':''} onclick="runAcceptance(this)">실제 계산 테스트</button>`+
   `<button class="mini" ${(!e.ok||mpiBusy)?'disabled':''} onclick="runMpiSmoke(this)">${mpiBusy?'MPI 재점검 중':'MPI 병렬 재점검'}</button></span></div>`+
   `${diagnostic}${safety}${help}${miss}${mpiLine}${mpiEvidence}${fcHelp}${acceptanceHtml(a)}`+
   `<details><summary>설치 상세 보기</summary><div style="margin-top:7px">${tools||'OpenFOAM 도구 정보 없음'}</div>`+
   `<div style="margin-top:7px">${fcVersion}</div>${fcPath}${manifest}</details></div>`;
}
async function refreshEnv(btn){
 const old=btn.textContent;btn.disabled=true;btn.textContent='검사 중…';
 try{
  const r=await fetch('/api/environment/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
  const j=await r.json();
  if(!r.ok||j.error)throw new Error(j.error||'환경 검사 실패');
  location.reload();
 }catch(e){alert(e.message);btn.disabled=false;btn.textContent=old;}
}
async function runAcceptance(btn){
 const old=btn.textContent;btn.disabled=true;btn.textContent='테스트 예약 중…';
 try{
  const r=await fetch('/api/environment/acceptance',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
  const j=await r.json();
  if(!r.ok||j.error)throw new Error(j.error||'수용 테스트 예약 실패');
 }catch(e){alert(e.message);btn.disabled=false;btn.textContent=old;}
}
async function runMpiSmoke(btn){
 const old=btn.textContent;btn.disabled=true;btn.textContent='MPI 재점검 예약 중…';
 try{
  const r=await fetch('/api/environment/mpi-smoke',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
  const j=await r.json();
  if(!r.ok||j.error)throw new Error(j.error||'MPI 병렬 재점검 예약 실패');
 }catch(e){alert(e.message);btn.disabled=false;btn.textContent=old;}
}
let WAS_ACTIVE=false, pollNow=false;
async function pollStatus(){
 try{
  const r=await fetch('/api/status');const s=await r.json();
  const el=document.getElementById('strip');
  let h='';
  if(s.active){
   WAS_ACTIVE=true;
   const a=s.active;
   const pct=(a.endTime&&a.time)?Math.min(100,Math.round(a.time/a.endTime*100)):0;
   h+=`<div class="strip">▶ 실행중: <b>${a.name}</b> · ${a.step}`+
      (a.time?` · Time ${a.time}/${a.endTime||'?'} (${pct}%)`:'')+
      (s.queue.length?` · 대기 ${s.queue.length}건`:'')+
      `<div class="bar"><div class="fill" style="width:${pct}%"></div></div>`+
      `<details><summary>진행 로그</summary><pre>${(a.lines||[]).join('\\n')}</pre></details></div>`;
  } else {
   if(s.queue.length)h+=`<div class="strip">⏳ 대기열 ${s.queue.length}건…</div>`;
   if(WAS_ACTIVE){WAS_ACTIVE=false;load();}
   for(const [k,v] of Object.entries(s.history||{})){
    if(v&&v.error)h+=`<div class="strip err">⚠ ${k}: ${v.error}</div>`;
   }
  }
  h+=environmentHtml(s.environment,s.freecad,s.acceptance,s.mpi_smoke);
  el.innerHTML=h;
 }catch(e){}
 setTimeout(pollStatus,1000);
}
async function load(){
 const r=await fetch('/api/cases');const j=await r.json();
 CASES=j.cases||[];
 document.getElementById('root').textContent='프로젝트: '+j.root;
 render();
}
load();
pollStatus();
</script>
<script type="importmap">{"imports":{"three":"/vendor/three.module.js"}}</script>
<script type="module">
// ── 3D 컷플레인 뷰어 (three.js — preview.py 와 같은 벤더 파일, 오프라인) ──
// 좌표 매핑: CFD(x,y,z; z=높이) → three(X,Z,Y; Y=up). 텍스처는 2D 뷰어와 같은
// /api/slice + cmap 을 그대로 사용 → 값·색이 2D 와 정의상 일치.
import * as THREE from 'three';
import { OrbitControls } from '/vendor/OrbitControls.js';
let R=null;
function mat(c,op){return new THREE.MeshBasicMaterial({color:c,transparent:true,opacity:op,side:THREE.DoubleSide});}
function sliceCanvas(sl){
 const d=sl.data,ny=d.length,nx=d[0].length;
 const c=document.createElement('canvas');c.width=Math.max(64,nx*6);c.height=Math.max(64,ny*6);
 const ctx=c.getContext('2d');
 const [mn,mx]=vwRange(),rg=(mx-mn)||1;
 const cw=c.width/nx,ch=c.height/ny;
 for(let j=0;j<ny;j++)for(let i=0;i<nx;i++){
  ctx.fillStyle=(sl.mask&&sl.mask[j][i])?'#b8bcc0':cmap((d[j][i]-mn)/rg);
  ctx.fillRect(i*cw,c.height-(j+1)*ch,cw+0.7,ch+0.7);
 }
 return c;
}
async function setPlane(axis,idx){
 try{
  const f=document.getElementById('vwfield').value;
  const r=await fetch(`/api/slice/${VW.case}?field=${f}&axis=${axis}&idx=${idx}`);
  const sl=await r.json(); if(sl.error){document.getElementById('vwread').textContent='⚠ '+sl.error;return;}
  const {L,W,H}=VW.info.room;
  let mesh=R.planes[axis];
  if(!mesh){
   let g;
   if(axis==='z'){g=new THREE.PlaneGeometry(L,W);}      // 수평면
   else if(axis==='y'){g=new THREE.PlaneGeometry(L,H);} // x–z 면
   else{g=new THREE.PlaneGeometry(W,H);}                // y–z 면
   mesh=new THREE.Mesh(g,new THREE.MeshBasicMaterial({side:THREE.DoubleSide}));
   if(axis==='z')mesh.rotation.x=Math.PI/2;    // 국소+y → 세계+Z(CFD y)
   if(axis==='x')mesh.rotation.y=-Math.PI/2;   // 국소+x → 세계+Z(CFD y)
   R.planes[axis]=mesh;R.scene.add(mesh);
  }
  if(mesh.material.map)mesh.material.map.dispose();
  mesh.material.map=new THREE.CanvasTexture(sliceCanvas(sl));
  mesh.material.needsUpdate=true;
  if(axis==='z')mesh.position.set(L/2,sl.pos,W/2);
  else if(axis==='y')mesh.position.set(L/2,H/2,sl.pos);
  else mesh.position.set(sl.pos,H/2,W/2);
  R.pos[axis]=sl.pos;
  R.renderer.render(R.scene,R.camera);   // RAF 와 무관하게 즉시 1프레임(확실성)
 }catch(e){
  document.getElementById('vwread').textContent='⚠ 3D: '+e.message;
 }
}
window.upd3=function(axis,idx){setPlane(axis,idx);};
window._dbg3=function(){return R?{raf:!!R.raf,children:R.scene.children.length,
 planes:Object.keys(R.planes),pos:R.pos,zY:R.planes.z?R.planes.z.position.y:null}:null;};
window._rot3=function(rad){ // 검증용: OrbitControls 와 동일한 궤도 회전 경로
 if(!R)return null;
 const t=R.controls.target,p=R.camera.position;
 const dx=p.x-t.x,dz=p.z-t.z,r0=Math.hypot(dx,dz),a=Math.atan2(dz,dx)+rad;
 p.set(t.x+r0*Math.cos(a),p.y,t.z+r0*Math.sin(a));
 R.camera.lookAt(t);R.renderer.render(R.scene,R.camera);
 return p.toArray().map(v=>+v.toFixed(2));
};
window.init3D=async function(){
 if(!VW)return;
 const cv=document.getElementById('cv3d');
 if(!R){
  const renderer=new THREE.WebGLRenderer({canvas:cv,antialias:true,preserveDrawingBuffer:true});
  renderer.setSize(640,430,false);
  R={renderer,scene:new THREE.Scene(),camera:new THREE.PerspectiveCamera(45,640/430,0.01,2000),
     controls:null,planes:{},pos:{},raf:null};
  R.scene.background=new THREE.Color(0xf4f6f8);
  R.controls=new OrbitControls(R.camera,cv);
 }
 R.scene.clear(); R.planes={};
 const {L,W,H}=VW.info.room;
 const edges=new THREE.LineSegments(
  new THREE.EdgesGeometry(new THREE.BoxGeometry(L,H,W)),
  new THREE.LineBasicMaterial({color:0x2c5f8a}));
 edges.position.set(L/2,H/2,W/2);
 R.scene.add(edges);
 const face=(wall,color)=>{
  let p;
  if(wall==='x0'||wall==='xL'){p=new THREE.Mesh(new THREE.PlaneGeometry(W,H),mat(color,0.16));
   p.rotation.y=-Math.PI/2;p.position.set(wall==='x0'?0:L,H/2,W/2);}
  else if(wall==='y0'||wall==='yW'){p=new THREE.Mesh(new THREE.PlaneGeometry(L,H),mat(color,0.16));
   p.position.set(L/2,H/2,wall==='y0'?0:W);}
  else return;
  R.scene.add(p);
 };
 if(VW.info.inlet)face(VW.info.inlet,0x2980b9);
 if(VW.info.outlet)face(VW.info.outlet,0xc0392b);
 // 급배기구 모드: 스냅된 실제 계산 패치를 벽/천장/바닥 위에 표시.
 const opening=(o)=>{
  if(!o.rect||o.rect.length!==4)return;
  const [u0,v0,u1,v1]=o.rect.map(Number),du=u1-u0,dv=v1-v0;
  if(!(du>0&&dv>0))return;
  const col=o.role==='supply'?0x2980b9:0xc0392b;
  const p=new THREE.Mesh(new THREE.PlaneGeometry(du,dv),mat(col,0.72));
  const eps=0.003;
  if(o.wall==='ceiling'||o.wall==='floor'){
   p.rotation.x=Math.PI/2;
   p.position.set((u0+u1)/2,o.wall==='ceiling'?H+eps:-eps,(v0+v1)/2);
  }else if(o.wall==='x0'||o.wall==='xL'){
   p.rotation.y=-Math.PI/2;
   p.position.set(o.wall==='x0'?-eps:L+eps,(v0+v1)/2,(u0+u1)/2);
  }else if(o.wall==='y0'||o.wall==='yW'){
   p.position.set((u0+u1)/2,(v0+v1)/2,o.wall==='y0'?-eps:W+eps);
  }else return;
  R.scene.add(p);
 };
 for(const o of (VW.info.openings||[]))opening(o);
 // V3a 실형상: 장애물 wireframe 박스 + 방 폴리곤 라인(바닥·천장)
 const ol=VW.info.outlines||{};
 for(const o of (ol.obstacles||[])){
  const bb=o.bbox, bw=bb[2]-bb[0], bd=bb[3]-bb[1];
  const eg=new THREE.LineSegments(
   new THREE.EdgesGeometry(new THREE.BoxGeometry(bw,o.h,bd)),
   new THREE.LineBasicMaterial({color:o.kind==='column'?0x5d4037:0x8e44ad}));
  eg.position.set((bb[0]+bb[2])/2, o.h/2, (bb[1]+bb[3])/2);
  R.scene.add(eg);
 }
 if(ol.room){
  for(const yy of [0,H]){
   const pts=ol.room.map(p=>new THREE.Vector3(p[0],yy,p[1]));
   pts.push(pts[0].clone());
   R.scene.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
    new THREE.LineBasicMaterial({color:0x1a2733})));
  }
 }
 R.camera.position.set(L*1.45,H*1.7,W*1.55);
 R.controls.target.set(L/2,H/2,W/2);R.controls.update();
 if(!VW.modelOnly){
  for(const [ax,id] of [['x','s3x'],['y','s3y'],['z','s3z']])
   await setPlane(ax,document.getElementById(id).value);
  // 컬러바(2D와 동일 색범위)
  const cb=document.getElementById('vwcb3'),c2=cb.getContext('2d');
  const [mn,mx]=vwRange();
  c2.clearRect(0,0,cb.width,cb.height);
  for(let y=0;y<cb.height;y++){c2.fillStyle=cmap(1-y/cb.height);c2.fillRect(0,y,20,1);}
  c2.fillStyle='#333';c2.font='11px sans-serif';
  const unit=document.getElementById('vwfield').value==='T'?'℃':'m/s';
  c2.fillText(mx.toFixed(1),23,10);c2.fillText(mn.toFixed(1),23,cb.height-2);
  c2.fillText(unit,23,cb.height/2+4);
 }
 R.renderer.render(R.scene,R.camera);
 if(!R.raf)anim();
};
function anim(){
 if(!R)return;
 const vis=document.getElementById('vw3d').style.display!=='none'
        && document.getElementById('vwov').style.display!=='none';
 if(!vis){R.raf=null;return;}
 R.raf=requestAnimationFrame(anim);
 R.controls.update();
 R.renderer.render(R.scene,R.camera);
}
if(window.VW&&document.getElementById('vwov').style.display!=='none'&&document.getElementById('vwmode').value==='3d')
 window.init3D();
</script></body></html>"""


# ── 새 해석 마법사 페이지 ────────────────────────────────────────────────────

PAGE_NEW = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>새 해석 — MEP CFD Studio</title>
<style>
 :root{--accent:#2c5f8a;--line:#e2e2e2;--muted:#666}
 *{box-sizing:border-box}
 body{font-family:'Malgun Gothic','Segoe UI',sans-serif;margin:0;background:#f0f2f5;color:#1a1a1a}
 .wrap{max-width:860px;margin:18px auto;padding:0 16px}
 .hdr{display:flex;align-items:center;gap:14px;background:#fff;padding:14px 20px;border-radius:10px;box-shadow:0 1px 6px rgba(0,0,0,.07)}
 .hdr h1{font-size:19px;margin:0;color:var(--accent)}
 .btn{background:var(--accent);color:#fff;border:none;border-radius:8px;padding:9px 16px;font-size:14px;cursor:pointer;text-decoration:none;display:inline-block}
 .btn.sec{background:#fff;color:var(--accent);border:1px solid var(--accent)}
 .panel{background:#fff;border-radius:10px;box-shadow:0 1px 6px rgba(0,0,0,.07);padding:16px 22px;margin:14px 0}
 h2{font-size:15px;color:var(--accent);border-bottom:2px solid var(--accent);padding-bottom:6px;margin:2px 0 14px}
 label{margin-right:18px}
 input[type=number],input[type=text]{border:1px solid #ccc;border-radius:6px;padding:6px 8px;font-size:14px;width:90px}
 input.wide{width:430px} input.mid{width:220px}
 select{border:1px solid #ccc;border-radius:6px;padding:6px;font-size:14px}
 .row{margin:9px 0;line-height:2.1}
 .hint{background:#eaf2f8;border-radius:8px;padding:8px 12px;font-size:12.5px;color:#1a5276;margin:8px 0}
 .warn{color:#b9770e;font-size:12.5px;font-weight:600}
 .prevbox{background:#fbf9f3;border:1px solid #e0d9c2;border-radius:8px;padding:12px 16px;font-size:13.5px;line-height:1.9;margin-bottom:12px}
 .err{color:#c0392b;font-weight:600;margin-top:10px}
 .req{color:#c0392b}
 .optbl{border-collapse:collapse;font-size:13px;margin:6px 0}
 .optbl th,.optbl td{border:1px solid var(--line);padding:4px 7px;text-align:center}
 .optbl input{width:56px;padding:4px 5px}
 .optbl select{font-size:12.5px;padding:4px}
 .uploadbox{border:2px dashed #8bb6d8;border-radius:12px;background:#f5faff;padding:22px;text-align:center;margin:10px 0}
 .uploadbox.drag{background:#e4f2fb;border-color:var(--accent)}
 .uploadbtn{display:inline-block;background:var(--accent);color:#fff;border:0;border-radius:10px;padding:13px 24px;font-size:16px;font-weight:700;cursor:pointer;margin:0 0 8px}
 .uploadsub{display:block;color:var(--muted);font-size:12.5px}
 .uploadstatus{min-height:24px;margin-top:9px;font-size:13px;font-weight:600;color:#1a5276}
 .hardwarn{background:#fff1f0;border:2px solid #c0392b;border-radius:8px;color:#922b21;padding:10px 12px;margin-top:8px;font-weight:700}
 details.advanced{margin:10px 0;color:var(--muted);font-size:13px}
 details.advanced summary{cursor:pointer;color:var(--accent);font-weight:600}
 button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{outline:3px solid #f4b942;outline-offset:2px}
</style></head><body><div class="wrap">
 <div class="hdr"><h1>＋ 새 해석</h1><div style="flex:1"></div><a class="btn sec" href="/">← 대시보드</a></div>

  <div class="panel"><h2>STEP 1 · 방 정보</h2>
   <div class="row">
    <label><input type="radio" name="mode" value="geometry" checked onchange="modeCh()"> DXF 도면에서 자동 추출</label>
    <label><input type="radio" name="mode" value="manual" onchange="modeCh()"> 치수 직접 입력(간이)</label>
   </div>
   <div id="sec_manual" style="display:none">
    <div class="row">L <input id="L" type="number" min="0.1" step="0.1" value="11.0" required oninput="preview()"> ×
     W <input id="W" type="number" min="0.1" step="0.1" value="9.0" required oninput="preview()"> ×
     H <input id="H" type="number" min="0.1" step="0.1" value="5.4" required oninput="preview()"> m</div>
   </div>
   <div id="sec_geom">
    <div id="dropbox" class="uploadbox" ondragover="dragDxf(event,true)" ondragleave="dragDxf(event,false)" ondrop="dropDxf(event)">
     <button type="button" class="uploadbtn" onclick="el('dxffile').click()">📐 DXF 도면 선택 · 자동 변환</button>
     <input id="dxffile" type="file" accept=".dxf,.dwg" hidden onchange="uploadDxf(this.files[0])">
     <label class="uploadsub">도면 단위
      <select id="dxfunit" aria-label="DXF 도면 단위"><option value="auto" selected>자동 감지(권장)</option><option value="mm">mm로 읽기</option><option value="header">CAD 헤더 사용</option></select>
     </label>
     <span class="uploadsub">DXF 최대 100MB · 도면은 프로젝트에 안전하게 복사되고 geometry.json으로 자동 변환됩니다.</span>
     <div id="uploadstatus" class="uploadstatus" role="status" aria-live="polite"></div>
    </div>
    <details class="advanced"><summary>고급: 기존 geometry.json 경로 직접 입력</summary>
     <div class="row">경로 <input id="gpath" type="text" class="wide" placeholder="C:\\...\\geometry.json" aria-label="기존 geometry.json 경로">
      <button class="btn sec" onclick="inspect()">경로 불러오기</button></div>
    </details>
    <div id="ginfo" class="hint" style="display:none" role="status" aria-live="polite"></div>
    <div class="row">해석 구역 <select id="zone" onchange="selCh()" aria-label="해석할 도면 구역"><option value="">도면 전체 범위(bbox)</option></select>
     &nbsp;범위(mm) <input id="bbox" type="text" class="mid" placeholder="x0,y0,x1,y1" onchange="selCh()" aria-label="도면 해석 범위 네 좌표">
     &nbsp;층고 <input id="height" type="number" min="0.1" step="0.1" value="3.0" required oninput="preview()"> m</div>
    <div id="ohint" class="hint" style="display:none" role="status" aria-live="polite"></div>
  </div>
 </div>

 <div class="panel"><h2>STEP 2 · 해석 조건</h2>
   <div class="row">발열(계산서 총발열) <input id="kw" type="number" min="0" step="0.5" placeholder="예: 10" oninput="preview()"> kW
   <span class="req">★권장</span> <span style="color:var(--muted);font-size:12px">— 비우면 구식 바닥 40°C 고정온도 모드</span></div>
  <div class="row">급배기 방식:
   <label><input type="radio" name="vmode" value="wall" checked onchange="vmodeCh()"> 벽 전체(간이 스크리닝)</label>
   <label><input type="radio" name="vmode" value="open" onchange="vmodeCh()"> 급배기구 지정(디퓨저/그릴) <span class="req">★배열 검토용</span></label>
  </div>
  <div id="sec_vwall">
   <div class="row">급기 벽 <select id="supply" onchange="preview()"></select>
    &nbsp;배기 벽 <select id="exhaust" onchange="preview()"></select>
    <span style="color:var(--muted);font-size:12px">(x0=서, xL=동, y0=남, yW=북 벽 — 도면 좌표 기준)</span></div>
    <div class="row">급기 유속 <input id="su" type="number" min="0.01" step="0.01" value="0.3" required oninput="preview()"> m/s
     <span id="suwarn" class="warn" role="status" aria-live="polite"></span></div>
  </div>
  <div id="sec_vopen" style="display:none">
   <table class="optbl" id="optbl"><thead><tr>
    <th>역할</th><th>타입</th><th>벽</th><th>cx(m)</th><th>cy(m)</th><th>w(m)</th><th>h(m)</th><th>설계 CMH</th><th>출처</th><th></th>
   </tr></thead><tbody></tbody></table>
   <div class="row">
    <button class="btn sec" onclick="opAdd('supply')">＋ 급기구</button>
    <button class="btn sec" onclick="opAdd('exhaust')">＋ 배기구</button>
     <button class="btn sec" id="btnDiff" style="display:none" onclick="opFromDrawing()">📐 도면 디퓨저 불러오기 <span id="ndiff"></span></button>
     <button class="btn sec" id="btnRoleConfirm" style="display:none" onclick="confirmTerminalRoles()">추천 급기/리턴 역할 검토 완료</button>
    <span class="hint" style="display:inline-block;padding:4px 10px">좌표축: ceiling/floor=(x,y) · x0/xL=(y,z) · y0/yW=(x,z) · round=원형 하향 단일패치 근사 · 4way=천장 4방향 취출</span>
   </div>
   <div class="row" style="margin-top:12px;font-weight:600;font-size:13.5px">실형상(V3) — 방 폴리곤·장애물
    <label id="polylb" style="display:none;font-weight:400;margin-left:10px">
     <input type="checkbox" id="usepoly" checked onchange="preview()"> 방 실형상(zone 폴리곤) 사용</label>
   </div>
   <table class="optbl" id="obtbl"><thead><tr>
    <th>종류</th><th>x0(m)</th><th>y0</th><th>x1</th><th>y1</th><th>h(m)</th><th>발열 kW</th><th>대류비</th><th>kW 근거</th><th>DXF 출처</th><th></th>
   </tr></thead><tbody></tbody></table>
   <div class="row">
    <button class="btn sec" onclick="obAdd()">＋ 장애물</button>
    <button class="btn sec" id="btnObs" style="display:none" onclick="obFromDrawing()">📐 도면 기둥·장비 불러오기 <span id="nobs"></span></button>
    <span class="hint" style="display:inline-block;padding:4px 10px">발열 장비는 kW·대류비·근거를 모두 확인하세요. 대류분만 CFD에 주입되고 복사분은 미모델로 기록됩니다.</span>
   </div>
   <div class="hardwarn">⚠ 현재 V3는 실제 고체 벽 메시가 아니라 고저항 다공성 셀 근사입니다. 초기 배치 비교용이며 최종 설계 판정용이 아닙니다.</div>
    <div id="opwarn" class="warn" role="status" aria-live="polite"></div>
   </div>
   <div class="row">급기 온도 <input id="st" type="number" min="-50" max="100" step="1" value="20" required oninput="preview()"> °C
    &nbsp;· 격자 셀 <input id="cell" type="number" min="0.05" max="5" step="0.05" value="0.3" required oninput="preview()"> m
    &nbsp;· 최대 반복 <input id="iters" type="number" min="50" step="50" value="400" required oninput="preview()"></div>
 </div>

  <div class="panel"><h2>STEP 3 · 확인</h2>
    <div id="preview" class="prevbox" role="status" aria-live="polite">DXF 도면을 선택하세요.</div>
    <div class="hint"><b>정밀 3D/CFD를 사용할 때</b> 방·급배기·발열 장비 입력을 먼저 확정 저장하세요. 원본 도면은 바뀌지 않습니다.<br>
     <button id="confirmgeom" type="button" class="btn sec" onclick="confirmBodyGeometry()">정밀 3D 입력 확인·저장</button>
     <span id="confirmmsg" role="status" aria-live="polite"></span></div>
    <div class="row">케이스명 <input id="name" type="text" class="mid" maxlength="80" required placeholder="전기실_B1_10kW"></div>
   <button id="createbtn" class="btn" onclick="create(false)">생성</button>
   <button id="runbtn" class="btn" onclick="create(true)">생성＋즉시 실행</button>
   <span id="msg" class="err" role="alert" aria-live="assertive"></span>
 </div>
</div>
<script>
let GDIMS=null, OHINT={}, GDIFF=[], GPOLY=null, GOBS=[], OPENING_MODE_RECOMMENDATION='';
let UNITREVIEW_REQUIRED=false, UNITCONFIRMED=true;
const WALLS=['x0','xL','y0','yW'];
function v(id){return document.getElementById(id).value}
function el(id){return document.getElementById(id)}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function mode(){return document.querySelector('input[name=mode]:checked').value}
function modeCh(){
 el('sec_manual').style.display=mode()==='manual'?'':'none';
 el('sec_geom').style.display=mode()==='geometry'?'':'none';
 preview();
}
function wallOpts(){
 for(const id of ['supply','exhaust']){
  const cur=v(id)|| (id==='supply'?'x0':'xL');
  el(id).innerHTML=WALLS.map(w=>{
   const star=OHINT[w]?` ★개구부${OHINT[w]}`:'';
   return `<option value="${w}" ${w===cur?'selected':''}>${w}${star}</option>`;
  }).join('');
 }
}
function dragDxf(ev,on){ev.preventDefault();el('dropbox').classList.toggle('drag',on)}
function dropDxf(ev){
 ev.preventDefault();el('dropbox').classList.remove('drag');
 const file=ev.dataTransfer&&ev.dataTransfer.files&&ev.dataTransfer.files[0];
 if(file)uploadDxf(file);
}
async function uploadDxf(file){
 if(!file)return;
 const st=el('uploadstatus'),ext=(file.name.split('.').pop()||'').toLowerCase();
 st.style.color='#1a5276';
 if(ext==='dwg'){
  st.style.color='#922b21';
  st.textContent='⚠ DWG는 직접 읽을 수 없습니다. AutoCAD의 [다른 이름으로 저장]에서 AutoCAD 2010 이상 DXF(ASCII, 단위 mm)로 변환한 뒤 선택하세요.';
  el('dxffile').value='';return;
 }
 if(ext!=='dxf'){st.style.color='#922b21';st.textContent='⚠ DXF 파일(.dxf)만 선택하세요.';return;}
 if(file.size>100*1024*1024){st.style.color='#922b21';st.textContent='⚠ DXF 파일은 최대 100MB까지 가능합니다.';return;}
 st.textContent=`${file.name} 업로드 및 도면 자동 인식 중… 잠시 기다려 주세요.`;
 try{
   const r=await fetch('/api/import-dxf?filename='+encodeURIComponent(file.name)+'&unit='+encodeURIComponent(v('dxfunit')) ,{
   method:'POST',headers:{'Content-Type':'application/dxf'},body:file});
  const j=await r.json();
  if(!r.ok||j.error)throw new Error(j.error+(j.help?' — '+j.help:''));
  el('gpath').value=j.geometry;
  st.style.color='#1e8449';
  st.textContent=`✓ ${file.name} 자동 변환 완료 — 아래 인식 결과와 해석 구역을 확인하세요.`;
  await applyInspection(j.inspect||{});
 }catch(e){
  st.style.color='#922b21';st.textContent='⚠ '+e.message;
 }finally{el('dxffile').value='';}
}
async function inspect(){
 if(!v('gpath').trim()){el('ginfo').style.display='';el('ginfo').textContent='⚠ geometry.json 경로를 입력하세요.';return}
 try{
  const r=await fetch('/api/inspect',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({geometry:v('gpath')})});
  const j=await r.json();
  if(j.error)throw new Error(j.error);
  await applyInspection(j);
 }catch(e){el('ginfo').style.display='';el('ginfo').textContent='⚠ '+e.message;}
}
async function applyInspection(j){
 GDIMS=null;OHINT={};GDIFF=[];GPOLY=null;GOBS=[];
 UNITREVIEW_REQUIRED=!!(j.unit_review&&j.unit_review.required);
 UNITCONFIRMED=!UNITREVIEW_REQUIRED;
 const zones=j.zones||[], candidates=j.zone_candidates||[];
 el('ginfo').style.display='';
 let info=`<b>자동 인식:</b> 벽 ${j.walls||0} · 개구부 ${j.openings||0} · 장비 ${j.equipment||0} · 구역 ${zones.length}개`+
  (j.wall_extent_mm?` · 도면범위 x ${j.wall_extent_mm[0]}~${j.wall_extent_mm[2]}, y ${j.wall_extent_mm[1]}~${j.wall_extent_mm[3]} mm`:'');
 if(j.unit_detection&&j.unit_detection.auto_corrected)
  info+='<div class="warn"><b>단위 자동 보정 후보:</b> CAD 헤더는 inch였지만 실제 좌표를 mm로 읽었습니다. 아래 로비 크기가 맞는지 확인해야 계산할 수 있습니다. '+
   '<button id="btnUnitConfirm" type="button" class="btn sec" onclick="confirmUnitMm()">이 도면은 mm가 맞음</button></div>';
 if(j.parser_warnings&&j.parser_warnings.length)
  info+='<div class="warn">'+j.parser_warnings.map(w=>'⚠ '+esc(w)).join('<br>')+'</div>';
 if(j.contract==='geometry.v2')
  info+=`<div><b>geometry.v2:</b> 원본 CAD 추적 ID와 CFD 의미 검사를 적용했습니다. `+
   `<button id="btnOcc" type="button" onclick="buildOcc()" ${j.body_fitted_blocked?'disabled':''}>실제 3D 공기영역 만들기</button></div>`;
 if(j.body_fitted_blocked){
  const items=(j.review_items||[]).slice(0,5);
  info+=`<div class="hardwarn"><b>정밀 3D CFD 준비 확인 ${j.review_blocker_count||0}건</b><br>`+
   items.map(x=>'• ['+esc(x.source_label||x.element_id||x.code)+'] '+esc(x.message||x.code)).join('<br>')+
   ((j.review_items||[]).length>5?'<br>• 그 외 항목이 있습니다.':'')+
   '<br>빠른 검토 해석은 계속 사용할 수 있습니다.</div>';
 }
 el('zone').innerHTML='<option value="">도면 전체 범위(bbox)</option>'+
  zones.map(z=>`<option value="${z.i}">구역 ${z.i+1} · ${z.L}×${z.W} m</option>`).join('');
 if(j.height_confirmed&&j.height_m)el('height').value=j.height_m;
 if(zones.length){
  el('bbox').value='';el('zone').value=zones[0].i;
  info+='<div>✓ 첫 번째 폐합 구역을 자동 선택했습니다. 다른 방이면 ‘해석 구역’을 바꾸세요.</div>';
 }else if(candidates.length&&candidates[0].bbox_mm&&candidates[0].bbox_mm.length===4){
  const c=candidates[0];el('zone').value='';el('bbox').value=c.bbox_mm.join(',');
  info+=`<div class="hardwarn">⚠ 닫힌 방 경계는 없지만 <b>${esc(c.source_layer)} 레이어의 ${c.length_m}×${c.width_m} m (${c.area_m2}㎡)</b> 범위를 로비 후보로 찾았습니다. 실제 로비가 맞는지 확인하세요. 2D 도면만으로 층고는 알 수 없으므로 실제 높이(예: 두 개층 로비 10m)를 직접 입력한 뒤 [정밀 3D 입력 확인·저장]을 누르세요.</div>`;
 }else if(j.wall_extent_mm&&j.wall_extent_mm.length===4){
  el('zone').value='';el('bbox').value=j.wall_extent_mm.join(',');
  info+='<div class="hardwarn">⚠ 닫힌 방 구역을 찾지 못했습니다. 전체 벽 범위가 여러 방·복도를 포함할 수 있으므로 직접 범위를 확인하세요.</div>';
 }else{
  info+='<div class="hardwarn">⚠ 방 구역과 벽 범위를 찾지 못했습니다. 도면 레이어 매핑을 확인하세요.</div>';
 }
 el('ginfo').innerHTML=info;
 if(zones.length||candidates.length||(j.wall_extent_mm&&j.wall_extent_mm.length===4))await selCh(); else preview();
}
function confirmUnitMm(){
 const text=GDIMS?`${GDIMS.L}×${GDIMS.W} m`:(v('bbox').trim()||'표시된 범위');
 if(!window.confirm(`CAD 헤더는 inch이지만 실제 도면은 mm로 작성된 것으로 판단했습니다. 해석 구역 ${text}가 실제 크기와 맞습니까?`))return;
 UNITCONFIRMED=true;
 const btn=el('btnUnitConfirm');if(btn){btn.disabled=true;btn.textContent='✓ mm 단위 확인 완료';}
 preview();
}
async function buildOcc(){
 const btn=el('btnOcc');if(btn){btn.disabled=true;btn.textContent='3D 형상 생성 중…';}
 try{
  const r=await fetch('/api/build-occ',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({geometry:v('gpath')})});
  const j=await r.json();if(!r.ok||!j.ok)throw new Error(j.error||'3D 형상을 만들지 못했습니다.');
  const m=Number(j.volume_m3||0).toFixed(3);
  el('ohint').style.display='';
  el('ohint').innerHTML=`✅ 실제 3D 공기영역 생성 완료 · 체적 ${m} m³ · 표면 ${j.region_count||0}개<br>`+
   `형상 검사: watertight ${j.topology&&j.topology.watertight?'통과':'확인 필요'} `+
   `<button id="btnBodyMeshQuick" type="button" onclick="buildBodyMesh('quick')">빠른 실제형상 메시</button> `+
   `<button id="btnBodyMeshDetailed" type="button" onclick="buildBodyMesh('detailed')">안정 상세 메시</button> `+
   `<button type="button" onclick="openFieldRun()">현장 3.0 FTT 자동 해석</button> `+
   `<button type="button" onclick="openBodyGci()">4수준 메시 불확실성 계산</button>`;
  if(btn)btn.textContent='실제 3D 공기영역 다시 만들기';
 }catch(e){
  el('ohint').style.display='';el('ohint').textContent='⚠ '+e.message;
  if(btn)btn.textContent='실제 3D 공기영역 만들기';
 }finally{if(btn)btn.disabled=false;}
}
let LAST_BODY_MESH='', LAST_BODY_SOLVER='', LAST_THERMAL_SOLVER='', LAST_TRANSIENT_NEXT=0, LAST_TRANSIENT_ESTIMATE=0;
function openBodyGci(){location.href='/body-gci?geometry='+encodeURIComponent(v('gpath').trim())}
function openFieldRun(){location.href='/field-run?geometry='+encodeURIComponent(v('gpath').trim())}
async function buildBodyMesh(preset='quick'){
 const quick=el('btnBodyMeshQuick'), detailed=el('btnBodyMeshDetailed');
 [quick,detailed].forEach(b=>{if(b)b.disabled=true;});
 const label=preset==='detailed'?'상세 메시':'빠른 메시';
 const active=preset==='detailed'?detailed:quick;if(active)active.textContent=label+' 생성 중…';
 try{
  const r=await fetch('/api/build-body-mesh',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({geometry:v('gpath'),settings:{preset:preset}})});
  const j=await r.json();if(!r.ok||!j.ok)throw new Error(j.error||'실제형상 메시 gate에 실패했습니다.');
  const m=j.manifest||{}, q=m.mesh||{}, e=j.estimate||{};
  LAST_BODY_MESH=j.case||'';
  el('ohint').innerHTML=`✅ ${label} 통과 · 셀 ${q.cells||0}개 · 체적오차 `+
   `${((m.mesh_volume_error_ratio||0)*100).toFixed(3)}% · 최대 non-orthogonality ${q.max_non_orthogonality||0}`;
  if((m.layer||{}).enabled){
   el('ohint').innerHTML+=`<br>경계층 적용률 ${(((m.layer||{}).coverage_ratio||0)*100).toFixed(1)}%`+
    ` · y+ 목표 ${m.y_plus.target_min}~${m.y_plus.target_max} (계산 후 확인)`;
  }
  if(m.profile==='detailed')el('ohint').innerHTML+=
   `<br><button type="button" onclick="runBodyIsothermal()">등온 유동 계산 시작</button>`;
  if((m.warnings||[]).length){
   const concave=(m.strict_diagnostics||{}).concave_cells||0;
   el('ohint').innerHTML+=`<br>ℹ 상세 형상 진단: concave cell ${concave}개 `+
    '(OpenFOAM solver 품질 기준은 통과했습니다.)';
  }
 }catch(e){
  el('ohint').style.display='';el('ohint').innerHTML='⚠ 메시 생성 중단: '+esc(e.message)+
   '<br>계산은 시작되지 않았습니다. 메시 진단 결과를 확인하세요.';
 }finally{
  if(quick){quick.disabled=false;quick.textContent='빠른 실제형상 메시';}
  if(detailed){detailed.disabled=false;detailed.textContent='안정 상세 메시';}
 }
}
async function runBodyIsothermal(){
 el('ohint').style.display='';
 el('ohint').innerHTML='등온 유동 계산 중… 이 기준 모델은 몇 분 정도 걸릴 수 있습니다.';
 try{
  const r=await fetch('/api/run-body-isothermal',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({mesh_case:LAST_BODY_MESH})});
  const j=await r.json();if(!r.ok||!j.ok)throw new Error(j.error||'등온 유동 gate에 실패했습니다.');
  LAST_BODY_SOLVER=j.case||'';
  const m=j.manifest||{}, s=m.solver||{}, y=m.y_plus||{};
  const ratio=y.area_ratio_in_target==null?'측정 불가':(y.area_ratio_in_target*100).toFixed(1)+'%';
  const wallRatio=y.wall_treatment_acceptable_area_ratio==null?'측정 불가':
   (y.wall_treatment_acceptable_area_ratio*100).toFixed(1)+'%';
  el('ohint').innerHTML=`${m.status==='PASS'?'✅':'⚠'} 등온 유동 계산 완료 · 반복 ${s.iterations||0}회`+
   ` · continuity ${Math.abs((s.continuity||{}).global||0).toExponential(2)}`+
   `<br>로그층 y+ 목표면적 ${ratio} · 벽처리 유효면적 ${wallRatio}`+
   `<br>수렴 판정 ${esc(s.convergence_mode||'미달')} · ${m.design_ready?'설계 검토 가능':'추가 보정 필요'}`;
  const warningText={
   ITERATION_LIMIT:'최대 반복 안에 정상상태 수렴 기준을 충족하지 못했습니다.',
   WALL_TREATMENT_COVERAGE:'벽면 y+ 전이영역이 너무 넓습니다.',
   YPLUS_FIELD_MISSING:'벽면 y+를 계산하지 못했습니다.'
  };
  if((m.warnings||[]).length)el('ohint').innerHTML+='<br>진단: '+
   m.warnings.map(code=>esc(warningText[code]||code)).join('<br>');
   if((m.warnings||[]).includes('ITERATION_LIMIT')){
    el('ohint').innerHTML+='<br><button type="button" onclick="runBodyTransient()">시간변동 진단 계속</button>';
   }
   if(m.status!=='FAIL')el('ohint').innerHTML+=
    '<br><button type="button" onclick="runBodyThermal()">열·부력 안정성 시험 (0.05초)</button>';
 }catch(e){
  el('ohint').innerHTML='⚠ 등온 유동 계산 중단: '+esc(e.message)+
   '<br>기존 메시 결과는 보존되며 계산 결과를 설계 판단에 사용하지 않습니다.';
 }
}
async function runBodyThermal(){
 el('ohint').style.display='';
 el('ohint').innerHTML='열·부력 안정성 시험 계산 중입니다. 이 결과는 장시간 설계 결과가 아닙니다.';
 try{
  const r=await fetch('/api/run-body-thermal',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({mesh_case:LAST_BODY_MESH,initial_case:LAST_BODY_SOLVER})});
  const j=await r.json();if(!r.ok||!j.ok)throw new Error(j.error||'열·부력 안정성 gate 실패');
   LAST_THERMAL_SOLVER=j.case||'';
   const m=j.manifest||{}, s=m.solver||{}, t=m.thermal||{}, p=m.thermal_progress||{};
    const q=(j.result_artifacts||{}).summary||{}, qt=q.temperature||{}, qu=q.velocity||{}, eb=p.energy_balance||{};
  const co=((s.courant||{}).maximum==null?'확인 불가':Number(s.courant.maximum).toFixed(4));
  const tmin=(t.minimum_k==null?'확인 불가':Number(t.minimum_k).toFixed(3)+' K');
  const tmax=(t.maximum_k==null?'확인 불가':Number(t.maximum_k).toFixed(3)+' K');
   el('ohint').innerHTML=`${m.status==='PASS'?'✅':'⚠️'} 열·부력 안정성 시험 완료`+
    `<br>최대 Courant ${co} · 온도 ${tmin} ~ ${tmax}`+
     `<br>유동 교환시간 확보 ${(Number(p.flow_through_fraction||0)*100).toFixed(2)}%`+
     (eb.transient_closure_ratio==null?'':`<br>과도 에너지 폐합 ${(Number(eb.transient_closure_ratio)*100).toFixed(2)}% · 실내 축열 ${Number(eb.stored_sensible_energy_j||0).toFixed(1)} J`)+
    `<br>예상 남은 실제시간 ${p.estimated_remaining_runtime_seconds==null?'확인 불가':Math.ceil(Number(p.estimated_remaining_runtime_seconds)/60)+'분'}`+
    (q.cell_count?`<br>VTK ${Number(q.cell_count).toLocaleString()}셀 · 평균속도 ${Number(qu.mean_speed||0).toFixed(3)} m/s`: '')+
    '<br><b>0.05초 수치 안정성 시험이며 최종 설계 판정용 결과가 아닙니다.</b>'+
    '<br><button type="button" onclick="continueBodyThermal()">가속 5초 추가 계산</button>'+
    (j.results_url?` <a class="rep" target="_blank" href="${j.results_url}">2D·3D 단면 보기</a>`:'')+
    (j.report_url?` <a class="rep" target="_blank" href="${j.report_url}">상세 결과 리포트</a>`:'');
  }catch(e){el('ohint').innerHTML='❌ 열·부력 안정성 시험 중단: '+esc(e.message);}
 }
async function continueBodyThermal(){
 el('ohint').innerHTML='마지막 열·부력 결과에서 가속 5초 추가 계산 중입니다. 약 2분 정도 걸릴 수 있습니다.';
 try{
  const r=await fetch('/api/continue-body-thermal',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({solver_case:LAST_THERMAL_SOLVER,settings:{thermal_duration_s:5.0}})});
  const j=await r.json();if(!r.ok||!j.ok)throw new Error(j.error||'열·부력 이어 계산 실패');
  const m=j.manifest||{}, p=m.thermal_progress||{}, t=m.thermal||{}, s=m.solver||{};
   const q=(j.result_artifacts||{}).summary||{}, qt=q.temperature||{}, qu=q.velocity||{}, eb=p.energy_balance||{};
  const estimate=p.estimated_remaining_runtime_seconds==null?'확인 불가':
   Math.ceil(Number(p.estimated_remaining_runtime_seconds)/60)+'분';
  el('ohint').innerHTML=`${m.status==='FAIL'?'❌':'⚠️'} 열·부력 이어 계산 완료`+
   `<br>물리시간 ${Number(p.latest_time_s||0).toFixed(2)}초 · 유동 교환시간 확보 ${(Number(p.flow_through_fraction||0)*100).toFixed(2)}%`+
    `<br>최대 Courant ${Number((s.courant||{}).maximum||0).toFixed(4)} · 최고온도 ${Number(t.maximum_k||0).toFixed(3)} K`+
    (eb.transient_closure_ratio==null?'':`<br>과도 에너지 폐합 ${(Number(eb.transient_closure_ratio)*100).toFixed(2)}% · 실내 축열 ${Number(eb.stored_sensible_energy_j||0).toFixed(1)} J · 누적 배기열 ${Number(eb.cumulative_exhaust_energy_j||0).toFixed(1)} J`)+
   (q.cell_count?`<br>VTK 최고온도 ${Number(qt.maximum||0).toFixed(3)} K · 최고속도 ${Number(qu.maximum_speed||0).toFixed(3)} m/s`: '')+
   `<br>예상 남은 실제시간 ${estimate}`+
   (p.interactive_budget_exceeded?'<br><b>예상시간이 1시간을 넘습니다. 자동 장시간 실행은 차단됩니다.</b>':'')+
   '<br><button type="button" onclick="continueBodyThermal()">가속 5초 추가 계산</button>'+
   (j.results_url?` <a class="rep" target="_blank" href="${j.results_url}">2D·3D 단면 보기</a>`:'')+
   (j.report_url?` <a class="rep" target="_blank" href="${j.report_url}">상세 결과 리포트</a>`:'');
 }catch(e){el('ohint').innerHTML='❌ 열·부력 이어 계산 중단: '+esc(e.message);}
}
async function runBodyTransient(longRun=false){
 let settings=null;
 if(longRun){
  const minutes=Math.max(1,Math.ceil((LAST_TRANSIENT_ESTIMATE||0)/60));
  if(!confirm(`설계 검토 기준까지 약 ${minutes}분이 예상됩니다. 계산을 계속할까요?`))return;
  settings={transient_duration_s:LAST_TRANSIENT_NEXT};
 }
 el('ohint').style.display='';
 el('ohint').innerHTML='시간변동 진단 중… 정상상태 마지막 결과에서 이어서 계산합니다.';
 try{
  const r=await fetch('/api/run-body-transient',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({solver_case:LAST_BODY_SOLVER,settings})});
  const j=await r.json();if(!r.ok||!j.ok)throw new Error(j.error||'시간변동 진단에 실패했습니다.');
  const m=j.manifest||{}, s=m.solver||{}, w=m.transient_window||{}, p=m.transient_progress||{};
  const fraction=((p.flow_through_fraction||w.flow_through_fraction||0)*100).toFixed(1)+'%';
  const span=w.mean_speed_relative_span==null?'측정 불가':
   (w.mean_speed_relative_span*100).toFixed(1)+'%';
  LAST_TRANSIENT_NEXT=p.recommended_next_duration_s||0;
  LAST_TRANSIENT_ESTIMATE=p.estimated_remaining_runtime_seconds||0;
  const estimate=LAST_TRANSIENT_ESTIMATE>0?Math.max(1,Math.ceil(LAST_TRANSIENT_ESTIMATE/60))+'분':'계산 완료';
  const numerics=p.numerics||{};
  const solverRate=p.last_solver_runtime_per_simulated_second;
  const overhead=p.last_fixed_runtime_overhead_seconds;
  const costDetail=solverRate==null?'':
   `<br>성능 기준 ${esc(numerics.profile||'기본')} · OpenFOAM ${Number(solverRate).toFixed(1)}초/물리초`+
   ` · 준비·회수 ${Number(overhead||0).toFixed(1)}초`;
  el('ohint').innerHTML=`${m.status==='PASS'?'✅':'⚠'} 시간변동 진단 완료`+
   ` · 최대 Courant ${((s.courant||{}).maximum||0).toFixed(2)}`+
   `<br>평균유속 변동폭 ${span} · 유동 교환시간 확보 ${fraction}`+
   `<br>누적 ${Number(p.completed_duration_s||0).toFixed(1)}초 / 필요 ${Number(p.required_duration_s||0).toFixed(1)}초`+
   ` · 예상 남은 실제시간 ${estimate}`+
   costDetail+
   `<br>${m.design_ready?'설계 검토 가능':'아직 진단용 · 설계 검토에는 더 긴 계산 필요'}`;
  const warningText={
   TRANSIENT_WINDOW_MISSING:'비교할 결과 시점이 부족합니다.',
   TRANSIENT_WINDOW_UNSTABLE:'평균 유속이 아직 안정되지 않았습니다.',
   TRANSIENT_WINDOW_TOO_SHORT:'설계 판정에 필요한 유동 교환시간이 부족합니다.',
   TRANSIENT_RUNTIME_BUDGET:'예상 계산시간이 기본 1시간 한도를 넘습니다. 메시·solver 최적화가 필요합니다.',
   WALL_TREATMENT_COVERAGE:'벽면 y+ 전이영역이 너무 넓습니다.'
  };
  if((m.warnings||[]).length)el('ohint').innerHTML+='<br>진단: '+
   m.warnings.map(code=>esc(warningText[code]||code)).join('<br>');
  if((m.warnings||[]).includes('TRANSIENT_WINDOW_TOO_SHORT')&&LAST_TRANSIENT_NEXT>0&&
     !(p.interactive_budget_exceeded)){
   el('ohint').innerHTML+='<br><button type="button" onclick="runBodyTransient(true)">장시간 평균 계산 계속</button>';
  }else if(p.interactive_budget_exceeded){
   el('ohint').innerHTML+='<br>장시간 자동 실행이 차단되었습니다. 빠른 메시 또는 성능 최적화 후 다시 계산하세요.';
  }else if((m.warnings||[]).includes('TRANSIENT_WINDOW_UNSTABLE')){
   LAST_TRANSIENT_NEXT=10;
   el('ohint').innerHTML+='<br><button type="button" onclick="runBodyTransient(true)">안정화 계산 10초 계속</button>';
  }
 }catch(e){
  el('ohint').innerHTML='⚠ 시간변동 진단 중단: '+esc(e.message)+
   '<br>기존 정상상태 결과와 메시 결과는 보존됩니다.';
 }
}
async function selCh(){
 const body={geometry:v('gpath')};
 if(v('zone')!=='')body.zone=v('zone'); else if(v('bbox').trim())body.bbox=v('bbox');
 else {GDIMS=null;preview();return}
 let j;
 try{
  const r=await fetch('/api/inspect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  j=await r.json();if(!r.ok||j.error)throw new Error(j.error||'도면 범위를 읽지 못했습니다.');
 }catch(e){el('ohint').style.display='';el('ohint').textContent='⚠ '+e.message;GDIMS=null;preview();return}
 GDIMS=j.room||null; OHINT=j.openings_by_wall||{}; GDIFF=j.diffusers||[];
 GPOLY=j.room_polygon||null; GOBS=j.obstacles||[];
 let t='';
 if(Object.keys(OHINT).length)t+='💡 경계 개구부(급/배기 후보): '+Object.entries(OHINT).map(([w,n])=>`${w}벽 ${n}개`).join(' · ');
 if(GDIFF.length)t+=(t?'<br>':'')+`📐 도면 장비블록(디퓨저 후보) ${GDIFF.length}개 감지 — 급배기구 지정 모드에서 [도면 디퓨저 불러오기]`;
 if(GPOLY)t+=(t?'<br>':'')+`⬠ zone 실형상 폴리곤 ${GPOLY.length}점 — 급배기구 모드에서 "방 실형상 사용" 가능`;
 if(GOBS.length)t+=(t?'<br>':'')+`▣ 기둥·장비 ${GOBS.length}개 — [도면 기둥·장비 불러오기]로 장애물 배치`;
 if(j.warnings&&j.warnings.length)t+=(t?'<br>':'')+j.warnings.map(w=>'⚠ '+esc(w)).join('<br>');
 el('ohint').style.display=t?'':'none'; el('ohint').innerHTML=t;
 el('btnDiff').style.display=GDIFF.length?'':'none';
 el('ndiff').textContent=GDIFF.length?`(${GDIFF.length})`:'';
 el('btnObs').style.display=GOBS.length?'':'none';
 el('nobs').textContent=GOBS.length?`(${GOBS.length})`:'';
 el('polylb').style.display=GPOLY?'':'none';
 wallOpts(); preview();
}
// ── 장애물 편집기 (V3a 실형상) ──
let OBROWS=[];
function obAdd(){OBROWS.push({kind:'equipment',x0:'',y0:'',x1:'',y1:'',h:2.0,kw:'',convective_fraction:'',evidence:'',source_id:'',source_label:'',source_ref:{},source_type:'user_confirmed'});obRender();}
function obDel(i){OBROWS.splice(i,1);obRender();}
function obSet(i,k,val){OBROWS[i][k]=val;preview();}
function obRender(){
 const tb=document.querySelector('#obtbl tbody');
 tb.innerHTML=OBROWS.map((r,i)=>{
  const num=(k,w)=>{const fraction=k==='convective_fraction';return `<input type="number" min="${k==='h'?0.01:0}" ${fraction?'max="1" step="0.01"':'step="0.1"'} style="width:${w||56}px" value="${esc(r[k]??'')}" oninput="obSet(${i},'${k}',this.value)">`};
  const text=(k,w)=>`<input type="text" style="width:${w||118}px" value="${esc(r[k]??'')}" oninput="obSet(${i},'${k}',this.value)">`;
  const sel=`<select onchange="obSet(${i},'kind',this.value)"><option ${r.kind==='column'?'selected':''}>column</option><option ${r.kind==='equipment'?'selected':''}>equipment</option></select>`;
  const source=r.source_type==='dxf_detected'
   ?`<small title="DXF 원본 항목">DXF 원본<br><code>${esc(r.source_label||r.source_id)}</code></small>`
   :r.override_of_dxf
    ?`<small title="DXF 원본을 보존하고 사용자 입력을 기록합니다">DXF 원본 + 사용자 변경<br><code>${esc(r.source_label||r.source_id)}</code></small>`
    :'<small>수동 입력</small>';
  return `<tr><td>${sel}</td><td>${num('x0')}</td><td>${num('y0')}</td><td>${num('x1')}</td><td>${num('y1')}</td>`+
   `<td>${num('h',48)}</td><td>${r.kind==='equipment'?num('kw',56):'—'}</td><td>${r.kind==='equipment'?num('convective_fraction',56):'—'}</td><td>${r.kind==='equipment'?text('evidence',132):'—'}</td>`+
   `<td>${source}</td><td><a class="rep" style="color:#c0392b" href="#" onclick="obDel(${i});return false">✕</a></td></tr>`;
 }).join('');
 preview();
}
function obFromDrawing(){
 if(!GOBS.length){alert('도면에서 감지된 기둥·장비가 없습니다 — STEP 1에서 도면·zone/bbox 먼저');return}
 for(const o of GOBS){
  OBROWS.push({kind:o.kind,x0:o.bbox[0],y0:o.bbox[1],x1:o.bbox[2],y1:o.bbox[3],
   h:o.h||(o.kind==='column'?'':2.0),kw:o.kw??'',convective_fraction:o.convective_fraction??'',
   evidence:o.evidence||'',source_id:o.source_id||'',source_label:o.source_label||o.name||'',
   source_ref:o.source_ref||{},source_type:o.source_type||(o.source_id?'dxf_detected':'user_confirmed'),
   override_of_dxf:o.override_of_dxf===true});
 }
 obRender();
 alert(GOBS.length+'개 장애물을 불러왔습니다. 표의 DXF 출처를 실제 도면과 대조한 뒤, 발열 장비에는 kW·대류비·근거를 확인하세요.');
}
function opFromDrawing(){
 if(!GDIFF.length){alert('불러올 도면 장비블록이 없습니다 — STEP 1에서 도면·zone/bbox 먼저');return}
 if(OPROWS.length&&OPROWS.every(r=>r.cx===''&&r.cy===''))OPROWS=[];
 for(const d of GDIFF){
  const role=(d.role==='supply'||d.role==='exhaust')?d.role:(d.suggested_role||'supply');
  const type=d.type||(role==='supply'?'4way':'grille');
 OPROWS.push({role:role,type:type,wall:d.host_surface||'ceiling',cx:d.cx,cy:d.cy,
   w:Math.min(1.2,d.w),h:Math.min(1.2,d.h),cmh:d.airflow_cmh||d.cmh||'',
   source_id:d.source_id||'',source_label:d.source_label||d.name||'',
   source_ref:{...(d.source_ref||{})},source_type:d.source_type||'',
   override_of_dxf:d.override_of_dxf===true,
   dxf_defaults:d.source_id?{role:d.role,type:d.type,wall:d.host_surface||'ceiling',
    cx:d.cx,cy:d.cy,w:Math.min(1.2,d.w),h:Math.min(1.2,d.h),
    cmh:d.airflow_cmh||d.cmh||null}:null,
   needsReview:!!d.requires_role_review,
   roleConfidence:Number(d.role_suggestion_confidence||0),
   roleSuggestionSource:d.role_suggestion_source||''});
 }
 if(!OPROWS.some(r=>r.role==='exhaust'))opAdd('exhaust');
 opRender();
 el('btnRoleConfirm').style.display=OPROWS.some(r=>r.needsReview)?'':'none';
 const ns=OPROWS.filter(r=>r.role==='supply').length,ne=OPROWS.filter(r=>r.role==='exhaust').length;
 alert(`${GDIFF.length}개 원형 말단 위치를 불러왔습니다. 급기 ${ns}개·리턴 ${ne}개는 SA/RA 문자와 거리만으로 추천한 값입니다. 표의 색상과 신뢰도를 보고 실제 덕트 연결을 확인한 뒤 각 말단의 CMH를 입력하세요.`);
}
function confirmTerminalRoles(){
 if(!OPROWS.length)return;
 const review=OPROWS.filter(r=>r.needsReview),ns=OPROWS.filter(r=>r.role==='supply').length,
  ne=OPROWS.filter(r=>r.role==='exhaust').length,low=review.filter(r=>Number(r.roleConfidence||0)<0.5).length;
 if(!window.confirm(`현재 표는 급기 ${ns}개·리턴 ${ne}개입니다. 이 중 ${low}개는 낮은 신뢰도의 위치 기반 추천입니다. 실제 SA/RA 덕트 연결과 일치하는지 도면에서 확인했습니까?`))return;
 for(const row of OPROWS){
  if(row.needsReview&&row.source_type==='dxf_detected'){
   row.source_type='user_confirmed';row.override_of_dxf=true;
  }
  row.needsReview=false;
 }
 el('btnRoleConfirm').style.display='none';opRender();
 alert('현재 표의 급기/리턴 역할을 검토 완료로 표시했습니다.');
}
function dims(){
 if(mode()==='manual')return {L:+v('L'),W:+v('W'),H:+v('H')};
 if(!GDIMS)return null;
 return {L:GDIMS.L,W:GDIMS.W,H:+v('height')};
}
// ── 급배기구 편집기 (v2 openings) ──
let OPROWS=[];
function vmode(){return document.querySelector('input[name=vmode]:checked').value}
function vmodeCh(){
 el('sec_vwall').style.display=vmode()==='wall'?'':'none';
 el('sec_vopen').style.display=vmode()==='open'?'':'none';
 if(vmode()==='open'){
  if(!OPROWS.length){opAdd('supply');opAdd('exhaust');}
  const recommendations=[];
  if(v('cell')===el('cell').defaultValue){
   el('cell').value=0.15;
   recommendations.push('급배기구 권장 기본값으로 격자 셀을 0.15 m로 적용했습니다');
  }else if(+v('cell')>0.15){
   recommendations.push(`입력한 격자 셀 ${v('cell')} m를 유지합니다 (급배기구는 0.15 m 이하 권장)`);
  }
  if(v('iters')===el('iters').defaultValue){
   el('iters').value=4000;
   recommendations.push('급배기구 권장 기본값으로 최대 반복을 4000회로 적용했습니다');
  }else if(+v('iters')<4000){
   recommendations.push(`입력한 최대 반복 ${v('iters')}회를 유지합니다 (급배기구는 4000~8000회 권장)`);
  }
  OPENING_MODE_RECOMMENDATION=recommendations.join(' · ');
 }else{
  OPENING_MODE_RECOMMENDATION='';
 }
 preview();
}
function opAdd(role){
 OPROWS.push(role==='supply'
  ?{role:'supply',type:'4way',wall:'ceiling',cx:'',cy:'',w:0.6,h:0.6,cmh:400}
  :{role:'exhaust',type:'grille',wall:'xL',cx:'',cy:'',w:0.5,h:0.5,cmh:400});
 opRender();
}
function opDel(i){OPROWS.splice(i,1);opRender();}
function opMatchesDxf(r){
 const d=r.dxf_defaults;if(!d)return true;
 for(const k of ['role','type','wall']){
  if(d[k]!==null&&d[k]!==undefined&&d[k]!==''&&d[k]!=='unresolved'&&String(r[k]??'')!==String(d[k]))return false;
 }
 for(const k of ['cx','cy','w','h','cmh']){
  if(d[k]===null||d[k]===undefined||d[k]==='')continue;
  if(Math.abs(Number(r[k])-Number(d[k]))>1e-6)return false;
 }
 return true;
}
function opSet(i,k,val){
 OPROWS[i][k]=val;
 const becameOverride=OPROWS[i].source_type==='dxf_detected'&&!opMatchesDxf(OPROWS[i]);
 if(becameOverride){
  OPROWS[i].source_type='user_confirmed';OPROWS[i].override_of_dxf=true;
 }
 if(k==='role'){
  OPROWS[i].needsReview=false;OPROWS[i].roleConfidence=1;OPROWS[i].roleSuggestionSource='user_edit';
  el('btnRoleConfirm').style.display=OPROWS.some(r=>r.needsReview)?'':'none';opRender();
 }else if(becameOverride)opRender();else preview();
}
function opRender(){
 const tb=document.querySelector('#optbl tbody');
 tb.innerHTML=OPROWS.map((r,i)=>{
  const sel=(k,opts)=>`<select onchange="opSet(${i},'${k}',this.value)">`+opts.map(o=>`<option ${o===r[k]?'selected':''}>${o}</option>`).join('')+`</select>`;
  const num=(k,w)=>{const step=k==='cmh'?1:0.001;return `<input type="number" min="${k==='cmh'?1:(k==='w'||k==='h'?0.01:0)}" step="${step}" required style="width:${w||56}px" value="${r[k]}" aria-label="${k}" oninput="opSet(${i},'${k}',this.value)">`;};
  const confidence=r.needsReview?`<div style="font-size:11px;color:#7d6608">추천 ${Math.round(Number(r.roleConfidence||0)*100)}%</div>`:'';
  const rowBg=r.needsReview?(r.role==='supply'?'#eef7ff':'#fff1f0'):'';
  const source=!r.source_id?'<small>수동 입력</small>'
   :(r.source_type==='dxf_detected'
    ?`<small title="DXF 원본 항목">DXF 원본<br><code>${esc(r.source_label||r.source_id)}</code></small>`
    :r.override_of_dxf
     ?`<small title="DXF 원본을 보존하고 사용자 입력을 기록합니다">DXF 원본 + 사용자 변경<br><code>${esc(r.source_label||r.source_id)}</code></small>`
     :`<small>사용자 확인<br><code>${esc(r.source_label||r.source_id)}</code></small>`);
  return `<tr style="background:${rowBg}"><td>${sel('role',['supply','exhaust'])}${confidence}</td>`+
   `<td>${r.role==='supply'?sel('type',['round','4way','down','grille']):'—'}</td>`+
   `<td>${sel('wall',['ceiling','x0','xL','y0','yW','floor'])}</td>`+
   `<td>${num('cx')}</td><td>${num('cy')}</td><td>${num('w')}</td><td>${num('h')}</td>`+
   `<td>${num('cmh',66)}</td>`+
   `<td>${source}</td>`+
   `<td><a class="rep" style="color:#c0392b" href="#" onclick="opDel(${i});return false">✕</a></td></tr>`;
 }).join('');
 preview();
}
function opValid(){
 if(OPROWS.some(r=>r.needsReview))return '도면의 SA/RA 추천 역할을 확인한 뒤 [추천 급기/리턴 역할 검토 완료]를 누르세요';
 const sups=OPROWS.filter(r=>r.role==='supply'), exhs=OPROWS.filter(r=>r.role==='exhaust');
 if(!sups.length)return '급기구가 없습니다';
 if(!exhs.length)return '배기구가 최소 1개 필요합니다(압력출구)';
 for(const r of OPROWS){
   if(r.cx===''||r.cy===''||+r.cx<0||+r.cy<0||!(+r.w>0)||!(+r.h>0))return '급배기구 좌표(cx,cy)는 0 이상, 크기(w,h)는 0보다 크게 입력하세요';
   if(!(+r.cmh>0))return '모든 급기구와 배기구의 CMH(계산서 풍량)를 입력하세요';
  }
  const supply=sups.reduce((sum,r)=>sum+(+r.cmh||0),0),exhaust=exhs.reduce((sum,r)=>sum+(+r.cmh||0),0),reference=Math.max(supply,exhaust);
  if(reference>0&&Math.abs(supply-exhaust)/reference>.01)return `설계 목표 확인: 총 급기 ${supply.toFixed(1)} CMH와 총 배기 목표 ${exhaust.toFixed(1)} CMH를 1% 이내로 맞추세요. 배기는 압력출구이므로 실제 유량은 계산 후 phi로 검증합니다.`;
  return '';
 }
function preview(){
 const d=dims(), pv=el('preview');
 const kw=parseFloat(v('kw'));
 if(!d||!d.L||!d.W||!d.H){pv.textContent=mode()==='geometry'?'DXF 도면을 선택하고 자동 인식된 해석 구역을 확인하세요.':'방의 길이·폭·높이를 입력하세요.';return}
 if(!(+v('cell')>0)){pv.textContent='격자 셀 크기를 0보다 크게 입력하세요.';return}
 const vol=d.L*d.W*d.H;
 let Q=0, head='';
 if(vmode()==='open'){
  el('suwarn').textContent='';
  let warn=opValid();
  if(OPENING_MODE_RECOMMENDATION){
   warn=warn?`${warn} · ${OPENING_MODE_RECOMMENDATION}`:OPENING_MODE_RECOMMENDATION;
  }
  const obkw=OBROWS.reduce((s,r)=>s+(+r.kw||0),0);
  if(obkw>0&&kw)warn=warn||'발열은 장애물 kW 또는 총발열 kW 중 하나만';
  el('opwarn').textContent=warn?('⚠ '+warn):'';
  const cmh=OPROWS.filter(r=>r.role==='supply').reduce((s,r)=>s+(+r.cmh||0),0);
  const exhaustCmh=OPROWS.filter(r=>r.role==='exhaust').reduce((s,r)=>s+(+r.cmh||0),0);
  Q=cmh/3600;
  const ns=OPROWS.filter(r=>r.role==='supply').length, ne=OPROWS.filter(r=>r.role==='exhaust').length;
  head=`급기구 ${ns}개 Σ<b>${cmh.toLocaleString()} CMH</b> · 배기구 ${ne}개 설계목표 Σ<b>${exhaustCmh.toLocaleString()} CMH</b> · ACH ${(cmh/vol).toFixed(1)}<br><span style="color:#666;font-size:12px">배기 CMH는 압력출구 설계 목표입니다. 실제 배기량은 해석 완료 후 phi로 확인합니다.</span>`;
  if(OBROWS.length||GPOLY&&el('usepoly').checked){
   head+=`<br>실형상: ${GPOLY&&el('usepoly').checked?'방 폴리곤 ✓':''} 장애물 ${OBROWS.length}개`
     +(obkw>0?` (발열 Σ${obkw} kW — 장비 위치별)`:'');
  }
  const nx=Math.round(d.L/ +v('cell')),ny=Math.round(d.W/ +v('cell')),nz=Math.round(d.H/ +v('cell'));
  head+=`<br>격자 ${nx}×${ny}×${nz} = ${(nx*ny*nz).toLocaleString()} 셀 <span style="color:#666;font-size:12px">(급배기구 케이스는 반복 4000~8000 권장 — 현실 풍량은 열 수렴이 느림)</span>`;
 } else {
  const u=+v('su');
  el('suwarn').textContent=(u&&u<0.1)?'⚠ 약유동: 에너지폐합이 안 닫혀 미수렴 위험 — 0.3 이상 권장':'';
  const sup=v('supply')||'x0';
  const A=(sup==='x0'||sup==='xL')?d.W*d.H:d.L*d.H;
  Q=u*A;
  const cmh=Q*3600;
  head=`풍량 = ${u} m/s × ${A.toFixed(1)} m² = <b>${cmh.toLocaleString(undefined,{maximumFractionDigits:0})} CMH</b> · ACH ${(cmh/vol).toFixed(1)}`;
 }
 let t=`방 ${d.L}×${d.W}×${d.H} m — 체적 ${vol.toFixed(0)} m³<br>`+head;
 const kweff=kw||(vmode()==='open'?OBROWS.reduce((s,r)=>s+(+r.kw||0),0):0);
 if(kweff&&Q>0)t+=`<br>예상 배기 ΔT = Q/(ρc·V̇) = ${kweff}kW/(1206×${Q.toFixed(3)}) = <b>${(kweff*1000/(1206*Q)).toFixed(2)} K</b>
  <span style="color:#666;font-size:12px">— 실행 후 CFD 배기 ΔT·에너지폐합이 이 손계산과 맞아야 정상</span>`;
 else if(!kweff)t+=`<br><span class="warn">발열 미입력(총 kW 또는 장비 kw) — 계산서 대조(에너지폐합 검증)가 불가합니다.</span>`;
 pv.innerHTML=t;
}
function checkInput(id){
 const x=el(id);
 if(!x.checkValidity()){x.reportValidity();el('msg').textContent='표시된 입력값의 범위와 필수 항목을 확인하세요.';return false}
 return true;
}
function validateCore(){
 const ids=['name','st','cell','iters'];
 if(mode()==='manual')ids.push('L','W','H'); else ids.push('height');
 if(vmode()==='wall')ids.push('su');
 for(const id of ids)if(!checkInput(id))return false;
 if(v('kw')!==''&&!checkInput('kw'))return false;
 if(mode()==='geometry'&&(!v('gpath').trim()||!GDIMS)){
  el('msg').textContent='DXF 도면을 선택하고 자동 인식된 해석 구역을 확인하세요.';return false;
 }
 if(mode()==='geometry'&&UNITREVIEW_REQUIRED&&!UNITCONFIRMED){
  el('msg').textContent='DXF 단위 확인이 필요합니다. 자동 인식 결과의 [이 도면은 mm가 맞음] 버튼을 먼저 누르세요.';return false;
 }
 if(vmode()==='open'){
  for(const x of document.querySelectorAll('#sec_vopen input[required]')){
   if(!x.checkValidity()){x.reportValidity();el('msg').textContent='급배기구 좌표·크기·풍량을 확인하세요.';return false;}
  }
 }
 return true;
}
async function confirmBodyGeometry(){
 const msg=el('confirmmsg'),btn=el('confirmgeom');msg.textContent='';
 if(mode()!=='geometry'||!v('gpath').trim()){msg.textContent='⚠ 먼저 DXF 도면을 불러오세요.';return}
 if(v('zone')===''&&!v('bbox').trim()){msg.textContent='⚠ 닫힌 방 또는 파서가 찾은 추정 구역을 선택하세요.';return}
 if(UNITREVIEW_REQUIRED&&!UNITCONFIRMED){msg.textContent='⚠ DXF 단위를 먼저 확인하세요. 자동 인식 결과에서 [이 도면은 mm가 맞음]을 누르세요.';return}
 if(vmode()!=='open'){msg.textContent='⚠ 급·배기구 지정 방식을 선택하세요.';return}
 if(!checkInput('height'))return;
 const warn=opValid();if(warn){msg.textContent='⚠ '+warn;return}
 const obstacles=OBROWS.filter(r=>r.x0!==''&&r.y0!==''&&r.x1!==''&&r.y1!==''&&r.h!=='');
 const incompleteHeat=obstacles.find(r=>r.kind==='equipment'&&Number(r.kw)>0&&(!(Number(r.convective_fraction)>0&&Number(r.convective_fraction)<=1)||!String(r.evidence||'').trim()));
 if(incompleteHeat){msg.textContent='⚠ 발열 장비마다 대류비(0~1)와 kW 근거를 입력하세요.';return}
 if(!obstacles.some(r=>r.kind==='equipment'&&+r.kw>0)){
  msg.textContent='⚠ 발열량(kW)이 입력된 장비를 하나 이상 추가하세요.';return;
 }
 const selectedZone=v('zone')===''?null:Number(v('zone'));btn.disabled=true;btn.textContent='확인본 저장 중…';
 try{
  const r=await fetch('/api/confirm-body-geometry',{method:'POST',headers:{'Content-Type':'application/json'},
   body:JSON.stringify({geometry:v('gpath'),zone:selectedZone,bbox:v('bbox'),height_m:v('height'),terminals:OPROWS,obstacles,unit_confirmed:UNITCONFIRMED})});
  const j=await r.json();if(!r.ok||!j.ok)throw new Error(j.error||'도면 확인본을 저장하지 못했습니다.');
  el('gpath').value=j.geometry;await applyInspection(j.inspection||{});
  el('zone').value=String(j.zone_index);await selCh();
  msg.innerHTML='✓ 원본은 보존하고 3D/CFD 확인본을 저장했습니다. '+
   `<a class="btn" href="/field-run?geometry=${encodeURIComponent(j.geometry)}">현장 자동 해석 시작 →</a> `+
   `<a class="btn secondary" href="/body-gci?geometry=${encodeURIComponent(j.geometry)}">메시 불확실성 검증</a>`;
 }catch(e){msg.textContent='⚠ '+e.message}
 finally{btn.disabled=false;btn.textContent='정밀 3D 입력 확인·저장'}
}
function createBusy(on){for(const id of ['createbtn','runbtn'])el(id).disabled=on;}
async function create(runNow){
 el('msg').textContent='';
 if(!validateCore())return;
 const p={mode:mode(),name:v('name'),power_kw:v('kw'),supply:v('supply'),exhaust:v('exhaust'),
  supply_u:v('su'),supply_T_C:v('st'),cell:v('cell'),endtime:v('iters'),run_now:runNow};
 if(mode()==='manual'){p.L=v('L');p.W=v('W');p.H=v('H');}
 else{p.geometry=v('gpath');p.zone=v('zone');p.bbox=v('bbox');p.height=v('height');p.unit_confirmed=UNITCONFIRMED;}
 if(vmode()==='open'){
  const warn=opValid();
  if(warn){el('msg').textContent=warn;return}
  p.openings=OPROWS;
  const obs=OBROWS.filter(r=>r.x0!==''&&r.y0!==''&&r.x1!==''&&r.y1!=='');
  const incompleteHeat=obs.find(r=>r.kind==='equipment'&&Number(r.kw)>0&&(!(Number(r.convective_fraction)>0&&Number(r.convective_fraction)<=1)||!String(r.evidence||'').trim()));
  if(incompleteHeat){el('msg').textContent='발열 장비마다 대류비(0~1)와 kW 근거를 입력하세요.';return}
  if(obs.length)p.obstacles=obs;
  if(GPOLY&&el('usepoly').checked&&mode()==='geometry')p.room_polygon=GPOLY;
 }
 createBusy(true);el('msg').textContent='계산 모델을 생성하는 중입니다…';
 try{
  const r=await fetch('/api/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});
  const j=await r.json();
  if(!r.ok||j.error)throw new Error(j.error||'생성에 실패했습니다.');
  if(j.run_error)alert('생성됨. 실행 실패: '+j.run_error);
  location.href='/';
 }catch(e){el('msg').textContent='⚠ '+e.message;createBusy(false);}
}
async function loadRequestedGeometry(){
 const requested=new URLSearchParams(location.search).get('geometry')||'';
 if(!requested)return;
 el('gpath').value=requested;
 const openMode=document.querySelector('input[name=vmode][value=open]');
 if(openMode){openMode.checked=true;vmodeCh()}
 el('uploadstatus').textContent='메시 불확실성 계산에 필요한 3D/CFD 입력을 확인하세요.';
 await inspect();
}
wallOpts(); el('exhaust').value='xL'; preview(); loadRequestedGeometry();
</script></body></html>"""


# ── 기동 ─────────────────────────────────────────────────────────────────────

def find_port(prefer):
    """지정 포트가 사용 중이면 +1 씩 20개까지 시도, 0이면 OS 임의."""
    if prefer == 0:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p
    for p in range(prefer, prefer + 20):
        try:
            s = socket.socket()
            s.bind(("127.0.0.1", p))
            s.close()
            return p
        except OSError:
            continue
    raise SystemExit(f"빈 포트를 못 찾음({prefer}~{prefer+19})")


def main():
    global ROOT
    ap = argparse.ArgumentParser(description="MEP CFD Studio — 대시보드 통합 프로그램")
    ap.add_argument("--root", default=os.path.join(HERE, "cfd_projects"),
                    help="프로젝트 루트(케이스 폴더 모음, 기본 cfd_projects/)")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    ROOT = os.path.abspath(args.root)
    os.makedirs(ROOT, exist_ok=True)
    environment = refresh_environment_capabilities()
    capabilities = environment["openfoam"]
    freecad = environment["freecad"]
    port = find_port(args.port)
    url = f"http://127.0.0.1:{port}"
    httpd = ThreadingHTTPServer(("127.0.0.1", port), StudioHandler)
    print(f"MEP CFD Studio: {url}")
    print(f"  프로젝트 루트: {ROOT}")
    if capabilities.get("ok"):
        print(f"  OpenFOAM(WSL): OK — {capabilities.get('distro')} "
              f"{capabilities.get('version') or capabilities.get('package_version')}")
    else:
        print(f"  OpenFOAM(WSL): {capabilities.get('summary')} — 실행 기능 비활성")
    if freecad.get("ok"):
        print(f"  FreeCAD(Windows): OK — {freecad.get('freecad_version')} "
              f"/ OCC {freecad.get('occ_version')}")
    else:
        print(f"  FreeCAD(Windows): {freecad.get('summary')} — 실제 형상 기능 비활성")
    print("  종료: Ctrl+C")
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료.")


if __name__ == "__main__":
    main()
