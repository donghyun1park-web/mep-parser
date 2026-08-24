"""Audit limited-beta and product-readiness evidence without self-certification.

The audit is intentionally conservative: missing or malformed evidence remains
BLOCKED, and bundled sample drawings are never counted as actual-site DXF UAT.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
import math
from pathlib import Path
import statistics
import tempfile
import os

import field_acceptance
import cfd_gci_job
import install_acceptance
import uat_acceptance


FIELD_CONTRACT = field_acceptance.CONTRACT
INSTALL_CONTRACT = install_acceptance.CONTRACT
UAT_CONTRACT = uat_acceptance.CONTRACT
RELEASE_CONTRACT = "release_readiness.v1"
G2_CONTRACT = "grid_convergence.v3"
G2_SCHEMA_VERSION = 3
G2_ENGINE = "body_fitted_thermal_mesh_uncertainty_lsr"
_G2_CASE_ARTIFACTS = {
    "run_manifest_sha256": "run_manifest.json",
    "result_manifest_sha256": "result_manifest.json",
    "mesh_manifest_sha256": "mesh_manifest.json",
    "thermal_input_sha256": "thermal_input.json",
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _duration_ko(seconds):
    """Format a positive runtime estimate for nontechnical Korean users."""
    try:
        value = float(seconds)
    except (TypeError, ValueError, OverflowError):
        return ""
    if not math.isfinite(value) or value < 0:
        return ""
    minutes = max(1, math.ceil(value / 60.0))
    hours, minutes = divmod(minutes, 60)
    if hours and minutes:
        return f"{hours}시간 {minutes}분"
    if hours:
        return f"{hours}시간"
    return f"{minutes}분"


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (OSError, ValueError):
        return False


def _atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                                     dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _check(check_id, label, passed, detail, evidence=None):
    return {
        "id": check_id,
        "label": label,
        "status": "PASS" if passed else "BLOCKED",
        "detail": detail,
        "evidence": evidence or [],
    }


def _valid_sha256(value):
    return (isinstance(value, str) and len(value) == 64
            and all(character in "0123456789abcdef"
                    for character in value.lower()))


def _g2_case_traceability(case, projects_root):
    """Return a case identity only when its claimed evidence is current.

    This is deliberately an artifact-integrity check, not a second execution
    of the result-citation or mesh/GCI numerical gates.
    """
    if not isinstance(case, dict):
        return None
    name = case.get("name")
    raw_path = case.get("path")
    provenance = case.get("provenance")
    if (not isinstance(name, str) or not name.strip()
            or not isinstance(raw_path, str) or not raw_path.strip()
            or not isinstance(provenance, dict)):
        return None
    case_path = Path(raw_path).expanduser()
    if not case_path.is_absolute() or not case_path.is_dir():
        return None
    try:
        resolved = case_path.resolve()
    except OSError:
        return None
    if not _inside(resolved, projects_root):
        return None
    for hash_key, filename in _G2_CASE_ARTIFACTS.items():
        expected = provenance.get(hash_key)
        if not _valid_sha256(expected):
            return None
        try:
            actual = _sha256(resolved / filename)
        except OSError:
            return None
        if actual.lower() != expected.lower():
            return None
    return (name.strip().casefold(), str(resolved).casefold())


def _g2_release_manifest_passes(row, projects_root):
    """Validate the minimum independently traceable G2 release contract."""
    if not isinstance(row, dict):
        return False
    if (row.get("schema_version") != G2_SCHEMA_VERSION
            or row.get("contract") != G2_CONTRACT
            or row.get("engine") != G2_ENGINE
            or not isinstance(row.get("created_at"), str)
            or not row.get("created_at").strip()
            or row.get("status") != "PASS"
            or row.get("design_ready") is not True
            or not isinstance(row.get("errors"), list)
            or row.get("errors")):
        return False
    comparison = row.get("comparison")
    metrics = row.get("metrics")
    cases = row.get("cases")
    if (not isinstance(comparison, dict)
            or not isinstance(metrics, list) or not metrics
            or not all(isinstance(item, dict) and item.get("status") == "PASS"
                       for item in metrics)
            or not isinstance(cases, list) or len(cases) < 4):
        return False
    grid_count = comparison.get("grid_count")
    if (not isinstance(grid_count, int) or isinstance(grid_count, bool)
            or grid_count < 4 or grid_count != len(cases)):
        return False
    try:
        minimum_flow_through = float(
            comparison.get("minimum_flow_through_fraction")
        )
        maximum_window_drift = float(comparison.get("maximum_window_drift_pct"))
    except (TypeError, ValueError, OverflowError):
        return False
    if (not math.isfinite(minimum_flow_through)
            or not math.isfinite(maximum_window_drift)
            or minimum_flow_through < 3.0
            or maximum_window_drift > 2.0):
        return False
    evidence = [_g2_case_traceability(case, projects_root) for case in cases]
    if any(item is None for item in evidence):
        return False
    names = [item[0] for item in evidence]
    paths = [item[1] for item in evidence]
    return len(set(names)) == len(names) and len(set(paths)) == len(paths)


def _environment_check(projects_root):
    path = Path(projects_root) / "capability_manifest.json"
    try:
        row = _read(path)
        openfoam = row.get("openfoam") or {}
        missing = []
        if not row.get("body_fitted_runtime_ready"):
            missing.append("실제 형상 런타임")
        if not (row.get("body_fitted_engine_ready")
                or openfoam.get("thermal_detailed_ready")):
            missing.append("OpenFOAM v2606 상세 열유동 프로필")
        if not openfoam.get("body_fitted_ready"):
            missing.append("OpenFOAM 메시 도구")
        if not (row.get("freecad") or {}).get("ok"):
            missing.append("FreeCAD/OCC")
        acceptance = row.get("acceptance") or {}
        if (not acceptance.get("ok")
                or acceptance.get("openfoam_profile")
                != openfoam.get("compatible_profile")):
            missing.append("격리 환경 수용시험")
        passed = not missing
        detail = ("OpenFOAM·FreeCAD·격리 환경 수용시험 통과" if passed else
                  "환경 다시 검사 필요: " + ", ".join(missing))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        passed, detail = False, f"환경 증거를 읽지 못했습니다: {exc}"
    return _check("environment", "설치 환경", passed, detail,
                  [str(path.resolve())] if path.is_file() else [])


def _g2_check(projects_root):
    benchmark = (Path(projects_root).parent / "cfd_benchmarks" /
                 "g2_thermal" / "geometry.json")
    benchmark_hash = _sha256(benchmark) if benchmark.is_file() else ""
    candidates = []
    for path in (Path(projects_root) / "_body_gci").glob("*/grid_convergence.json"):
        try:
            row = _read(path)
            job = _read(path.parent / "gci_job.json")
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if not isinstance(row, dict) or not isinstance(job, dict):
            continue
        job_input = job.get("input")
        if not isinstance(job_input, dict):
            continue
        geometry_path = Path(str(job_input.get("geometry_path") or ""))
        if (row.get("contract") != G2_CONTRACT
                or not benchmark_hash
                or job_input.get("gci_contract") != G2_CONTRACT
                or str(job_input.get("geometry_sha256") or "").lower()
                != benchmark_hash
                or geometry_path.resolve() != benchmark.resolve()):
            continue
        passed = _g2_release_manifest_passes(row, projects_root)
        candidates.append((str(row.get("created_at") or ""), passed, path, row))
    passed_rows = [item for item in candidates if item[1]]
    if passed_rows:
        _, _, path, _ = max(passed_rows, key=lambda item: item[0])
        return _check("g2_v3", "G2 4격자 검증", True,
                      "3.0 교환시간·정상성·메시 불확실성 gate 통과",
                      [str(path.resolve())])
    active = []
    for path in (Path(projects_root) / "_body_gci").glob("*/gci_job.json"):
        try:
            job = _read(path)
            job_input = job.get("input") or {}
            geometry_path = Path(str(job_input.get("geometry_path") or ""))
            owner = cfd_gci_job.active_run_lock(projects_root, path.parent.name)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if (job.get("status") != "running" or not owner or not benchmark_hash
                or str(job_input.get("gci_contract") or "") != "grid_convergence.v3"
                or str(job_input.get("geometry_sha256") or "").lower()
                != benchmark_hash
                or geometry_path.resolve() != benchmark.resolve()):
            continue
        levels = job.get("levels") or []
        level = next((row for row in levels if row.get("status") == "running"), None)
        if level is None:
            level = next((row for row in levels if row.get("status") != "PASS"), None)
        if level is None:
            continue
        target = float((job_input.get("thermal_settings") or {}).get(
            "thermal_minimum_flow_through_fraction") or 3.0)
        fraction = float(level.get("flow_through_fraction") or 0.0)
        live = cfd_gci_job.bounded_live_progress(job, projects_root)
        active.append((
            str(job.get("updated_at") or ""), path, str(level.get("name") or "격자"),
            fraction, target, live,
        ))
    if active:
        _, path, level_name, fraction, target, live = max(
            active, key=lambda item: item[0]
        )
        if live:
            live_fraction = float(
                live.get("estimated_flow_through_fraction") or fraction
            )
            progress_detail = (
                f"예상 {live_fraction:.2f} / {target:.2f} FTT"
                f" (저장 {fraction:.2f}, 다음 {float(live['next_checkpoint_time_s']):.3f}초)"
            )
            remaining = _duration_ko(
                live.get("estimated_remaining_runtime_seconds")
            )
            if remaining:
                progress_detail += f" · 남은 실제시간 약 {remaining}"
        else:
            progress_detail = f"{fraction:.2f} / {target:.2f} FTT"
        return _check(
            "g2_v3", "G2 4격자 검증", False,
            f"{level_name} 계산 실행 중 · {progress_detail}; 완료 후 자동 재감사",
            [str(path.resolve())],
        )
    if candidates:
        _, _, path, row = max(candidates, key=lambda item: item[0])
        return _check("g2_v3", "G2 4격자 검증", False,
                      f"최신 v3 결과가 {row.get('status') or '미완료'}입니다.",
                      [str(path.resolve())])
    return _check("g2_v3", "G2 4격자 검증", False,
                  "grid_convergence.v3 실제 결과가 없습니다.")


def _field_check(projects_root, evidence_root):
    accepted, variations, reasons, signatures = [], [], [], set()
    for path in sorted((Path(evidence_root) / "field_dxf").glob("*.json")):
        try:
            verification = field_acceptance.validate_evidence(path, projects_root)
            row = verification.get("manifest") or {}
            signature = str((row.get("variation") or {}).get("signature") or "")
            valid = bool(verification.get("ok") and signature
                         and signature not in signatures)
            if valid:
                signatures.add(signature)
                accepted.append(str(path.resolve()))
                variations.append(row.get("variation") or {})
            else:
                reason = verification.get("error") or "duplicate drawing signature"
                reasons.append(f"{path.name} ({reason})")
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            reasons.append(path.name)
    diversity_counts = {
        "단위": len({str(row.get("unit") or row.get("insunits") or "")
                    for row in variations}),
        "원점": len({str(row.get("origin") or row.get("extmin") or "")
                    for row in variations}),
        "회전": len({str(row.get("rotation") or row.get("insert_rotations_deg") or "")
                    for row in variations}),
        "레이어": len({str(row.get("layers_signature") or row.get("layers") or "")
                      for row in variations}),
    }
    missing_diversity = [label for label, count in diversity_counts.items()
                         if count < 2]
    passed = len(accepted) >= 3 and not missing_diversity
    if passed:
        detail = f"서로 다른 실제 현장 DXF {len(accepted)}건과 단위·원점·회전·레이어 다양성 통과"
    else:
        detail = f"유효한 실제 현장 DXF 증거 {len(accepted)}/3건"
        diversity_progress = ", ".join(
            f"{label} {min(count, 2)}/2"
            for label, count in diversity_counts.items()
        )
        detail += "; 다양성 " + diversity_progress
        if missing_diversity:
            detail += "; 더 필요한 차이: " + ", ".join(missing_diversity)
        if reasons:
            detail += f"; 미인정: {', '.join(reasons)}"
    return _check("field_dxf", "실제 현장 DXF", passed, detail, accepted)


def _install_check(projects_root, evidence_root):
    accepted = []
    for path in sorted((Path(evidence_root) / "install_recovery").glob("*.json")):
        try:
            verification = install_acceptance.validate_evidence(
                path, projects_root
            )
            if verification.get("ok"):
                accepted.append(str(path.resolve()))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    return _check(
        "install_recovery", "설치·복구 수용시험", bool(accepted),
        ("클린 설치·의존성 복구·중단 작업 재개 통과" if accepted else
         "세 가지 설치·복구 시나리오의 실제 수용 증거가 없습니다."),
        accepted[-1:],
    )


def _uat_check(evidence_root):
    sessions, participant_ids, rejected = [], set(), []
    for path in sorted((Path(evidence_root) / "uat").glob("*.json")):
        try:
            verification = uat_acceptance.validate_evidence(
                path, Path(evidence_root).parent
            )
            row = verification.get("manifest") or {}
            participant_id = str(row.get("participant_id") or "").casefold()
            if verification.get("ok") and participant_id not in participant_ids:
                participant_ids.add(participant_id)
                sessions.append((path, row))
            else:
                rejected.append(path.name)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            rejected.append(path.name)
    completed = sum(row.get("first_project_completed") is True for _, row in sessions)
    rate = completed / len(sessions) if sessions else 0.0
    median = statistics.median(row["setup_minutes"] for _, row in sessions) if sessions else None
    fatals = sum(int(row.get("fatal_usability_errors") or 0) for _, row in sessions)
    passed = len(sessions) >= 3 and rate >= 0.9 and median <= 15.0 and fatals == 0
    detail = (f"{len(sessions)}명, 완료율 {rate * 100:.0f}%, 설정 중앙값 "
              f"{median:.1f}분, 치명 오류 {fatals}건" if sessions else
              "관찰자가 작업별로 기록한 기계설비 담당자 UAT가 없습니다.")
    if rejected:
        detail += f"; 무효 {len(rejected)}건"
    return _check("mechanical_uat", "기계설비 담당자 UAT", passed, detail,
                  [str(path.resolve()) for path, _ in sessions])


def build_release_audit(projects_root, output_path=None):
    projects_root = Path(projects_root).expanduser().resolve()
    evidence_root = projects_root / "_release_evidence"
    checks = [
        _environment_check(projects_root),
        _g2_check(projects_root),
        _field_check(projects_root, evidence_root),
        _install_check(projects_root, evidence_root),
        _uat_check(evidence_root),
    ]
    limited_beta = all(item["status"] == "PASS" for item in checks[:4])
    product_ready = limited_beta and checks[4]["status"] == "PASS"
    manifest = {
        "schema_version": 1,
        "contract": RELEASE_CONTRACT,
        "created_at": _now(),
        "status": "PASS" if product_ready else "BLOCKED",
        "limited_beta_ready": limited_beta,
        "product_ready": product_ready,
        "checks": checks,
        "next_actions": [item["detail"] for item in checks
                         if item["status"] != "PASS"],
    }
    output_path = Path(output_path or evidence_root / "release_readiness.json")
    _atomic_json(output_path, manifest)
    return {"ok": True, "manifest": manifest,
            "manifest_path": str(output_path.resolve())}


def generate_html(manifest, path):
    rows = "".join(
        f"<tr><td>{html.escape(item['label'])}</td><td class='{item['status'].lower()}'>"
        f"{item['status']}</td><td>{html.escape(item['detail'])}</td></tr>"
        for item in manifest["checks"]
    )
    document = f"""<!doctype html><html lang='ko'><meta charset='utf-8'>
<title>MEP CFD Studio 출시 준비 감사</title><style>
body{{font-family:Segoe UI,Malgun Gothic,sans-serif;max-width:980px;margin:32px auto;color:#243746}}
table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid #d9e3ea;text-align:left}}
.pass{{color:#207245;font-weight:700}}.blocked{{color:#a92c2c;font-weight:700}}
</style><h1>MEP CFD Studio 출시 준비 감사</h1>
<p>제한적 베타: <b>{str(manifest['limited_beta_ready']).upper()}</b> · 제품 준비: <b>{str(manifest['product_ready']).upper()}</b></p>
<table><tr><th>검증 항목</th><th>상태</th><th>근거</th></tr>{rows}</table>
<p>샘플 도면, 자체 선언, 누락된 해시는 실제 현장/UAT 증거로 인정하지 않습니다.</p></html>"""
    Path(path).write_text(document, encoding="utf-8", newline="\n")
    return str(Path(path).resolve())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projects-root", default="cfd_projects")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    result = build_release_audit(args.projects_root, args.output)
    report = Path(result["manifest_path"]).with_suffix(".html")
    generate_html(result["manifest"], report)
    print(json.dumps({**result, "report_path": str(report.resolve())},
                     ensure_ascii=False, indent=2))
    return 0 if result["manifest"]["product_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
