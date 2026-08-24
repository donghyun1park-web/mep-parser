"""
cfd_report.py — OpenFOAM 해석 결과 리포트 (도면→CFD 파이프라인)

Phase 1a: 잔차 로그 파서 + 수렴 그래프 ("측정 먼저" 하니스).
Phase 2 에서 지표 표 + 단면 컨투어 + 자립 HTML 로 확장.

사용:
  python cfd_report.py <solver.log>            # 로그 → 잔차 그래프 PNG
  python cfd_report.py <case_dir>              # case/log.* 자동 탐색
  python cfd_report.py <log> -o residuals.png

설계: 눈으로 로그를 읽어 "수렴한 것 같다"고 판단하지 않는다. 이 파서가 iteration 별 잔차·
continuity·rho·bounding(불안정 신호)·크래시를 수치로 뽑아, 안정화 시도 전/후를 객관 비교한다.
외부 의존성 없음(stdlib + matplotlib, 이미 프로젝트에서 사용).
"""
import argparse
import html
import json
import os
from pathlib import Path
import re
import sys

import cfd_convergence_spec
import cfd_case_health
import cfd_review
from cfd_status_catalog import CASE_HEALTH_CHECKS, status_descriptor

def generate_gci_report(study_dir, out_html=None):
    """Generate a self-contained Korean mesh-uncertainty report."""
    study_dir = os.path.abspath(study_dir)
    try:
        with open(os.path.join(study_dir, "grid_convergence.json"), encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"GCI 결과를 읽지 못했습니다: {exc}"}

    def number(value, digits=3):
        return "산출 불가" if value is None else f"{float(value):.{digits}f}"

    is_v2 = manifest.get("contract") == "grid_convergence.v2"
    is_v3 = manifest.get("contract") == "grid_convergence.v3"
    uses_time_window = is_v2 or is_v3
    case_rows = "".join(
        "<tr><td>{}</td><td>{:,}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            html.escape(str(row.get("name", ""))), int(row.get("cell_count") or 0),
            number(row.get("effective_grid_width_m"), 5), number(row.get("time_s"), 3),
            int((row.get("time_window") or {}).get("snapshot_count") or 0)
            if uses_time_window else "—",
        ) for row in manifest.get("cases") or []
    )
    metric_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td>"
        "<td class='{}'>{}</td></tr>".format(
            html.escape(str(row.get("label", ""))) + " (" +
            html.escape(str(row.get("unit", ""))) + ")",
            number(row.get("fine")), number(row.get("medium")),
            number(row.get("coarse")),
            "산출 불가" if (row.get("uncertainty_fine_pct") if is_v3
                            else row.get("gci_fine_pct")) is None
            else number(row.get("uncertainty_fine_pct") if is_v3
                        else row.get("gci_fine_pct"), 2) + "%",
            (number(row.get("window_drift_pct"), 2) + "%"
             if is_v3 else "—"),
            "pass" if row.get("status") == "PASS" else "fail",
            html.escape(str(row.get("status", ""))),
        ) for row in manifest.get("metrics") or []
    )
    status = str(manifest.get("status", "UNKNOWN"))
    comparison = manifest.get("comparison") or {}
    heat_sources = comparison.get("heat_source_contract") or []

    def source_reference(row):
        """Render only immutable CAD reference fields recorded by the case."""
        provenance = row.get("provenance")
        reference_kind = row.get("source_reference_kind")
        if isinstance(provenance, dict):
            reference_kind = reference_kind or provenance.get("source_reference_kind")
        if reference_kind == "manual_input":
            return "사용자 입력"
        ref = row.get("source_ref")
        if not isinstance(ref, dict):
            return "확인 불가"
        labels = (("handle", "handle"), ("source_handle", "handle"),
                  ("layer", "layer"), ("block_name", "block"))
        parts = []
        seen = set()
        for key, label in labels:
            value = ref.get(key)
            text = str(value).strip() if value is not None else ""
            token = (label, text)
            if text and token not in seen:
                parts.append(f"{label}:{text}")
                seen.add(token)
        return html.escape(" · ".join(parts) or "확인 불가")

    def source_review_status(row):
        """Make a preserved DXF-derived user override visible in the report."""
        provenance = row.get("provenance")
        override = row.get("override_of_dxf") is True
        if isinstance(provenance, dict):
            override = override or provenance.get("override_of_dxf") is True
        return "DXF 원본 + 사용자 변경" if override else "사용자 확인"

    heat_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(row.get('source_label') or row.get('name') or '장비'))}</td>"
        f"<td>{html.escape(str(row.get('source_id') or ', '.join(str(item) for item in (row.get('source_element_ids') or [])) or '확인 불가'))}</td>"
        f"<td>{source_reference(row)}</td>"
        f"<td>{source_review_status(row)}</td>"
        f"<td>{number(row.get('power_kw'), 3)} kW</td>"
        f"<td>{number(row.get('requested_convective_power_w', row.get('convective_power_w')), 1)} W</td>"
        f"<td>{number(row.get('applied_convective_power_w', row.get('convective_power_w')), 1)} W</td>"
        f"<td>{html.escape(str(row.get('evidence') or '근거 미기록'))}</td>"
        "</tr>"
        for row in heat_sources
    )
    heat_contract_html = ""
    if heat_sources:
        heat_contract_html = (
            "<h2>검증 열원 계약</h2>"
            "<p><small>아래 장비 원본 ID·열원 근거·실제 적용 대류열이 동일한 사례만 "
            "메시 불확실성 비교에 사용했습니다.</small></p>"
            "<table><tr><th>장비</th><th>원본 ID</th><th>입력 출처</th><th>검토 상태</th><th>입력</th>"
            "<th>요청 대류</th><th>CFD 대류 적용</th><th>근거</th></tr>"
            + heat_rows + "</table>"
        )
    status_class = "pass" if status == "PASS" else "fail"
    method_note = (
        "v3는 최소 4개 격자에 Eça–Hoekstra(2014) 최소제곱근 절차를 적용합니다. "
        "비정렬 격자 산포가 있으면 Richardson GCI로 오인하지 않고 적합 오차, "
        "표준편차와 안전계수를 합친 95% 메시 불확실성으로 판정합니다. DOI "
        "10.1016/j.jcp.2014.01.006. 마지막 시간창의 각 지표 변화도 2% 이하인지 "
        "별도 정상성 gate로 확인합니다."
        if is_v3 else
        "v2는 최소 1.0 유동 교환시간을 계산하고 마지막 0.1 유동 교환시간의 "
        "T/U/V를 셀 체적으로 가중한 뒤 시간 적분합니다. 전역 최고온도는 열원 "
        "모서리 국부 극값 진단으로만 남기며 GCI gate에 사용하지 않습니다."
        if is_v2 else
        "온도는 절대온도가 아니라 기준온도 대비 상승량으로 비교합니다. v1은 "
        "최종 한 시점의 셀 개수 기준 통계입니다. 과도 URANS 설계 확정에는 "
        "체적가중 시간창을 사용하는 v2를 권장합니다."
    )
    ratios = comparison.get("refinement_ratios_fine_to_coarse") or [
        comparison.get("refinement_ratio_medium_to_fine"),
        comparison.get("refinement_ratio_coarse_to_medium"),
    ]
    ratio_text = " / ".join(number(value) for value in ratios if value is not None)
    grid_count = int(comparison.get("grid_count") or len(manifest.get("cases") or []))
    measure_name = "세분 메시 불확실성" if is_v3 else "세분 GCI"
    limit_value = (manifest.get("uncertainty_limit_pct") if is_v3
                   else manifest.get("gci_limit_pct"))
    body = f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'><title>MEP CFD Studio 메시 독립성 보고서</title>
<style>body{{font-family:Segoe UI,Malgun Gothic,sans-serif;background:#f4f7fa;color:#1d2b36;margin:0}}main{{max-width:920px;margin:24px auto;background:#fff;padding:28px;border-radius:14px}}h1{{color:#244f73;margin-top:0}}table{{width:100%;border-collapse:collapse;margin:12px 0 24px}}th,td{{border-bottom:1px solid #dce5ec;padding:9px;text-align:right}}th:first-child,td:first-child{{text-align:left}}.pass{{color:#207245;font-weight:700}}.fail{{color:#a92c2c;font-weight:700}}.note{{background:#fff6d8;border-left:5px solid #d39b00;padding:12px;line-height:1.55}}small{{color:#64727c}}</style></head><body><main>
<h1>{grid_count}수준 메시 불확실성 보고서</h1><p>종합 판정 <b class='{status_class}'>{html.escape(status)}</b> · 허용 기준 {number(limit_value, 1)}%</p>
<p><small>유효 격자폭 h=(공기 체적/셀 수)^(1/3), 세분비 {ratio_text}, 비교 물리시간 {number(comparison.get('physical_time_s'))} s</small></p>
<h2>비교 결과</h2><table><tr><th>지표</th><th>세분</th><th>중간</th><th>거친</th><th>{measure_name}</th><th>시간창 변화</th><th>판정</th></tr>{metric_rows}</table>
<h2>입력 사례</h2><table><tr><th>사례</th><th>셀 수</th><th>유효 격자폭 (m)</th><th>물리시간 (s)</th><th>시간창 스냅샷</th></tr>{case_rows}</table>
{heat_contract_html}
<p class='note'>{html.escape(method_note)}</p>
</main></body></html>"""
    out_html = out_html or os.path.join(study_dir, "gci_report.html")
    try:
        os.makedirs(os.path.dirname(os.path.abspath(out_html)), exist_ok=True)
        temporary = out_html + ".tmp"
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
        os.replace(temporary, out_html)
    except OSError as exc:
        return {"ok": False, "error": f"GCI 보고서를 저장하지 못했습니다: {exc}"}
    return {"ok": True, "path": os.path.abspath(out_html), "status": status}


def generate_body_fitted_report(case_dir, out_html=None, *, projects_root=None):
    """Generate a compact self-contained report from VTU result artifacts."""
    case_dir = os.path.abspath(case_dir)
    try:
        with open(os.path.join(case_dir, "result_manifest.json"), encoding="utf-8") as f:
            result_manifest = json.load(f)
        with open(os.path.join(case_dir, result_manifest["summary_path"]), encoding="utf-8") as f:
            summary = json.load(f)
        with open(os.path.join(case_dir, "run_manifest.json"), encoding="utf-8") as f:
            run = json.load(f)
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"body-fitted 결과를 읽지 못했습니다: {exc}"}
    try:
        with open(os.path.join(case_dir, "thermal_input.json"), encoding="utf-8") as f:
            thermal_input = json.load(f)
    except (OSError, ValueError, json.JSONDecodeError):
        thermal_input = {}
    # Retain the old gate as diagnostic provenance only. Case Health is the
    # sole citation authority for this report.
    try:
        import cfd_result_gate
        result_gate = cfd_result_gate.evaluate_body_fitted_case(case_dir)
    except Exception as exc:  # A report must remain readable for incomplete cases.
        result_gate = {
            "citation_status": "NOT_EVALUATED",
            "status": "NOT_EVALUATED",
            "citable": False,
            "blockers": ["result_gate"],
            "reasons": [f"결과 게이트를 확인하지 못했습니다: {exc}"],
        }
    evidence_path = Path(case_dir) / "case_evidence.v1.json"
    case_health = None
    review_summary = {
        "status": "NOT_AVAILABLE",
        "reason_codes": ["CASE_EVIDENCE_NOT_FOUND"],
    }
    review_binding = None
    health_failure = "CASE_EVIDENCE_NOT_FOUND"
    if projects_root is not None and evidence_path.is_file():
        try:
            with cfd_review.review_state_lock(
                evidence_path, projects_root=Path(projects_root)
            ):
                case_health = cfd_case_health.build_case_health(
                    evidence_path, projects_root=Path(projects_root)
                )
                review_summary = cfd_case_health.review_summary(
                    evidence_path, projects_root=Path(projects_root)
                )
                if (
                    case_health.get("citation_status") == "DESIGN_CITABLE"
                    and review_summary.get("status") == "APPROVED"
                    and review_summary.get("review_id")
                ):
                    review_path = (
                        evidence_path.parent / "_reviews" /
                        f"{review_summary['review_id']}.case_review.v1.json"
                    )
                    if cfd_review.validate_review(
                        review_path, projects_root=Path(projects_root)
                    ):
                        raise ValueError("current review binding is invalid")
                    candidate = json.loads(review_path.read_text(encoding="utf-8"))
                    if (
                        candidate.get("review_id") != review_summary.get("review_id")
                        or (candidate.get("target") or {}).get("sha256")
                        != (case_health.get("evidence") or {}).get("sha256")
                    ):
                        raise ValueError("current review binding changed")
                    review_binding = candidate
            health_failure = ""
        except Exception:
            case_health = None
            review_summary = {
                "status": "INVALID",
                "reason_codes": ["CITATION_EVIDENCE_OR_REVIEW_INVALID"],
            }
            review_binding = None
            health_failure = "CITATION_EVIDENCE_OR_REVIEW_INVALID"
    progress = run.get("thermal_progress") or {}
    energy = progress.get("energy_balance") or {}
    temperature = summary.get("temperature") or {}
    velocity = summary.get("velocity") or {}
    hot = temperature.get("hottest_cell") or {}
    peak = velocity.get("peak_cell") or {}
    slices = result_manifest.get("slices") or []
    warnings = run.get("warnings") or []
    estimated = progress.get("estimated_remaining_runtime_seconds")

    def number(value, digits=3, suffix=""):
        if value is None:
            return "확인 불가"
        try:
            return f"{float(value):.{digits}f}{suffix}"
        except (TypeError, ValueError):
            return "확인 불가"

    def percentage(value, digits=1):
        try:
            return number(float(value) * 100, digits, "%")
        except (TypeError, ValueError):
            return number(None, digits, "%")

    def source_reference(source):
        """Show immutable DXF identity only when the thermal input retained it."""
        provenance = source.get("provenance")
        reference_kind = source.get("source_reference_kind")
        if isinstance(provenance, dict):
            reference_kind = reference_kind or provenance.get("source_reference_kind")
        if reference_kind == "manual_input":
            return "사용자 입력"
        ref = source.get("source_ref")
        if not isinstance(ref, dict):
            return "확인 불가"
        labels = (("handle", "handle"), ("source_handle", "handle"),
                  ("layer", "layer"), ("block_name", "block"))
        parts = []
        seen = set()
        for key, label in labels:
            value = ref.get(key)
            text = str(value).strip() if value is not None else ""
            token = (label, text)
            if text and token not in seen:
                parts.append(f"{label}:{text}")
                seen.add(token)
        return html.escape(" · ".join(parts) or "확인 불가")

    def source_review_status(source):
        """Make a preserved DXF-derived user override visible in the report."""
        provenance = source.get("provenance")
        override = source.get("override_of_dxf") is True
        if isinstance(provenance, dict):
            override = override or provenance.get("override_of_dxf") is True
        return "DXF 원본 + 사용자 변경" if override else "사용자 확인"

    def coordinate(row):
        values = row.get("centre_m") or []
        return ", ".join(f"{float(value):.3f}" for value in values) if values else "확인 불가"

    heat_contract = dict(thermal_input.get("heat") or {})
    heat_sources = list(thermal_input.get("heat_sources")
                        or heat_contract.get("sources") or [])
    requested_heat = heat_contract.get("requested_convective_power_w")
    if requested_heat is None:
        requested_heat = heat_contract.get("applied_convective_power_w")
    applied_heat = heat_contract.get("applied_convective_power_w")
    deferred_heat = heat_contract.get("deferred_convective_power_w", 0.0)
    source_heat_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(source.get('source_label') or source.get('name') or '장비'))}</td>"
        f"<td>{html.escape(str(source.get('source_id') or ', '.join(str(item) for item in (source.get('source_element_ids') or [])) or '확인 불가'))}</td>"
        f"<td>{source_reference(source)}</td>"
        f"<td>{source_review_status(source)}</td>"
        f"<td>{number(source.get('power_kw'), 3, ' kW')}</td>"
        f"<td>{number(source.get('convective_fraction'), 3)}</td>"
        f"<td>{percentage(source.get('radiative_fraction'))}</td>"
        f"<td>{number(source.get('requested_convective_power_w', source.get('convective_power_w')), 1, ' W')}</td>"
        f"<td>{number(source.get('applied_convective_power_w', source.get('convective_power_w')), 1, ' W')}</td>"
        f"<td>{number(source.get('deferred_convective_power_w', 0.0), 1, ' W')}</td>"
        f"<td>{html.escape(str(source.get('evidence') or '근거 미기록'))}</td>"
        "</tr>"
        for source in heat_sources
    )
    heat_contract_html = ""
    if heat_sources or heat_contract:
        scale = heat_contract.get("application_scale")
        scale_note = ""
        try:
            if scale is not None and abs(float(scale) - 1.0) > 1e-12:
                scale_note = (
                    "<p class='warn'><b>열원 스케일 단계:</b> 현재 CFD에는 요청 대류열의 "
                    f"{float(scale):.3g}배만 적용되었습니다. 이 결과는 설계 확정용이 아닙니다.</p>"
                )
        except (TypeError, ValueError):
            scale_note = "<p class='warn'>열원 스케일 값을 해석할 수 없어 설계 확정에 사용할 수 없습니다.</p>"
        heat_contract_html = f"""<h2>확정 장비 열원 계약</h2>
<p>입력 열량 {number(heat_contract.get('input_power_w'), 1, ' W')} · 요청 대류 {number(requested_heat, 1, ' W')} · CFD 대류 적용 {number(applied_heat, 1, ' W')} · 보류 대류 {number(deferred_heat, 1, ' W')} · 미모델 복사 {number(heat_contract.get('excluded_radiative_power_w'), 1, ' W')}</p>
<table><tr><th>장비</th><th>원본 ID</th><th>입력 출처</th><th>검토 상태</th><th>입력</th><th>대류비</th><th>복사비</th><th>요청 대류</th><th>CFD 대류 적용</th><th>보류 대류</th><th>근거</th></tr>{source_heat_rows or '<tr><td colspan="11">장비별 열원 목록이 없습니다.</td></tr>'}</table>
{scale_note}<small>현재 body-fitted 경로는 대류분만 공기영역에 적용합니다. 복사분은 별도 복사 검증 전까지 미모델입니다.</small>"""

    slice_rows = "".join(
        f"<tr><td>{html.escape(str(item.get('axis', '')).upper())}</td>"
        f"<td>{number(item.get('target_m'), 3, ' m')}</td>"
        f"<td>{int(item.get('sample_count') or 0):,}</td></tr>"
        for item in slices
    )
    warning_rows = "".join(f"<li>{html.escape(str(item))}</li>" for item in warnings)
    citation_status = (
        str(case_health.get("citation_status") or "NOT_EVALUATED")
        if isinstance(case_health, dict) else "NOT_EVALUATED"
    )
    if citation_status == "DESIGN_CITABLE" and not review_binding:
        citation_status = "CITATION_BLOCKED"
        health_failure = "CITATION_EVIDENCE_OR_REVIEW_INVALID"
    reason_codes = [
        str(item.get("code")) for item in ((case_health or {}).get("errors") or [])
        if isinstance(item, dict) and item.get("code")
    ]
    if health_failure and health_failure not in reason_codes:
        reason_codes.insert(0, health_failure)
    descriptor_code = reason_codes[0] if reason_codes else citation_status
    try:
        citation_descriptor = status_descriptor(descriptor_code)
    except ValueError:
        citation_descriptor = status_descriptor(
            "DESIGN_CITABLE" if citation_status == "DESIGN_CITABLE"
            else "CITATION_BLOCKED"
        )
    citation_class = citation_status.lower().replace("_", "-")

    checks = (case_health or {}).get("checks") or {}
    if checks:
        evidence_rows = "".join(
            "<tr>"
            f"<td><code>{html.escape(check_id)}</code></td>"
            f"<td>{html.escape(str((checks.get(check_id) or {}).get('status') or 'NOT_EVALUATED'))}</td>"
            f"<td>{html.escape(str((checks.get(check_id) or {}).get('impact') or ''))}</td>"
            f"<td>{html.escape(' · '.join(str(item) for item in ((checks.get(check_id) or {}).get('next_actions') or [])))}</td>"
            f"<td>{html.escape(', '.join(str(item) for item in ((checks.get(check_id) or {}).get('reason_codes') or [])) or '없음')}</td>"
            f"<td>{html.escape(', '.join(str(item) for item in ((checks.get(check_id) or {}).get('evidence_refs') or [])) or '없음')}</td>"
            "</tr>"
            for check_id in CASE_HEALTH_CHECKS
        )
    else:
        missing_descriptor = status_descriptor("CASE_EVIDENCE_NOT_FOUND")
        evidence_rows = "".join(
            "<tr>"
            f"<td><code>{html.escape(check_id)}</code></td>"
            "<td>NOT_EVALUATED</td>"
            f"<td>{html.escape(missing_descriptor['impact'])}</td>"
            f"<td>{html.escape(missing_descriptor['next_action'])}</td>"
            "<td>CASE_EVIDENCE_NOT_FOUND</td><td>없음</td></tr>"
            for check_id in CASE_HEALTH_CHECKS
        )

    binding_html = ""
    if citation_status == "DESIGN_CITABLE" and review_binding:
        identity = (
            case_health.get("case_identity")
            or case_health.get("legacy_case_ref") or {}
        )
        identity_value = identity.get("path") or identity.get("case_id") or "확인 불가"
        target = review_binding.get("target") or {}
        binding_html = (
            "<p><b>검증 범위:</b> "
            f"{html.escape(str(case_health.get('purpose') or ''))} · "
            f"{html.escape(', '.join(CASE_HEALTH_CHECKS))}</p>"
            "<p><b>Evidence ID:</b> "
            f"{html.escape(str(identity_value))} · "
            f"<b>Reviewer:</b> {html.escape(str(review_binding.get('reviewer') or ''))} · "
            f"<b>Review ID:</b> {html.escape(str(review_binding.get('review_id') or ''))}</p>"
            "<details class='evidence-detail'><summary>근거 보기</summary>"
            f"<p>Evidence: <code>{html.escape(str((case_health.get('evidence') or {}).get('path') or ''))}</code>"
            f" · SHA-256 <code>{html.escape(str((case_health.get('evidence') or {}).get('sha256') or ''))}</code></p>"
            f"<p>Target: <code>{html.escape(str(target.get('path') or ''))}</code>"
            f" · SHA-256 <code>{html.escape(str(target.get('sha256') or ''))}</code></p>"
            f"<p>Review reason: {html.escape(str(review_binding.get('reason') or ''))}</p></details>"
        )

    if citation_status == "SCREENING_ONLY":
        citation_banner = "<div class='citation-banner screening-only first-content'><b>초기안 비교용 · 설계 인용 불가</b></div>"
    elif citation_status == "DESIGN_CITABLE" and review_binding:
        citation_banner = (
            "<div class='citation-banner design-citable first-content'><b>"
            "설계 검토 인용 가능 · DESIGN_CITABLE</b>"
            f"{binding_html}</div>"
        )
    else:
        citation_banner = (
            f"<div class='citation-banner {html.escape(citation_class)} first-content'><b>"
            f"설계 인용 불가 · {html.escape(citation_status)}</b>"
            f"<p>{html.escape(citation_descriptor['impact'])}</p>"
            f"<p>다음 조치: {html.escape(citation_descriptor['next_action'])}</p>"
            f"<p>사유 코드: {html.escape(', '.join(reason_codes) or 'CASE_EVIDENCE_NOT_FOUND')}</p></div>"
        )
    gate_blockers = list(result_gate.get("blockers") or [])
    gate_reasons = list(result_gate.get("reasons") or [])
    gate_details = "".join(
        f"<li><code>{html.escape(str(item))}</code></li>" for item in gate_blockers
    ) or "<li>없음</li>"
    gate_reasons_html = "".join(
        f"<li>{html.escape(str(item))}</li>" for item in gate_reasons
    ) or "<li>없음</li>"

    numerical_quality = run.get("numerical_quality")
    numerical_quality_html = ""
    if isinstance(numerical_quality, dict):
        quality_blockers = " · ".join(str(item) for item in (numerical_quality.get("blockers") or [])) or "없음"
        courant = numerical_quality.get("courant") if isinstance(numerical_quality.get("courant"), dict) else {}
        wall_treatment = (numerical_quality.get("wall_treatment")
                          if isinstance(numerical_quality.get("wall_treatment"), dict) else {})
        flux_balance = (numerical_quality.get("flux_balance")
                        if isinstance(numerical_quality.get("flux_balance"), dict) else {})
        wall_ratio = wall_treatment.get("acceptable_area_ratio")
        flux_ratio = flux_balance.get("imbalance_ratio")
        numerical_quality_html = f"""<h2>수치 품질 근거</h2>
<table><tr><th>프로파일</th><th>상태</th><th>피크 Courant / 게이트</th><th>y+ 벽 처리 적용 면적</th><th>단말 phi 불평형</th></tr>
<tr><td><code>{html.escape(str(numerical_quality.get('profile') or '확인 불가'))}</code></td>
<td><b>{html.escape(str(numerical_quality.get('status') or '확인 불가'))}</b></td>
<td>{number(courant.get('peak_maximum'), 3)} / {number(courant.get('gate'), 3)}</td>
<td>{percentage(wall_ratio, 1)}</td>
<td>{percentage(flux_ratio, 3)}</td></tr></table>
<p><b>수치 품질 blocker:</b> {html.escape(quality_blockers)}</p>"""
    body = f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>MEP CFD Studio 상세 열·부력 결과</title>
<style>
body{{font-family:Segoe UI,Malgun Gothic,sans-serif;margin:0;background:#f4f7fa;color:#1e2b36}}
main{{max-width:980px;margin:24px auto;background:white;padding:28px;border-radius:14px;box-shadow:0 4px 20px #18334d18}}
h1{{margin-top:0;color:#244f73}} .warn{{background:#fff6d8;border-left:5px solid #d59b00;padding:12px}} .pass{{background:#e9f7ef;border-left:5px solid #207245;padding:12px}} .fail{{background:#fff0f0;border-left:5px solid #a92c2c;padding:12px}}
.citation-banner{{margin:0 0 18px;padding:14px;border-left:6px solid #778895;border-radius:8px;background:#f2f5f7}}.citation-banner.design-citable{{background:#e9f7ef;border-left-color:#207245}}.citation-banner.screening-only,.citation-banner.not-evaluated{{background:#fff6d8;border-left-color:#d59b00}}.citation-banner.citation-blocked{{background:#fff0f0;border-left-color:#a92c2c}}.evidence-detail{{margin:10px 0;padding:8px 10px;border:1px solid #dce5ec;border-radius:8px;background:#f8fafb}}.evidence-detail summary{{cursor:pointer;font-weight:700;color:#244f73}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:18px 0}}
.card{{border:1px solid #dce5ec;border-radius:10px;padding:14px}} .value{{font-size:1.45rem;font-weight:700}}
table{{width:100%;border-collapse:collapse;margin:12px 0}} th,td{{border-bottom:1px solid #e2e8ed;padding:9px;text-align:left}}
small{{color:#5d6a73}} code{{background:#eef3f6;padding:2px 5px;border-radius:4px}}
@media print{{main{{margin:0;box-shadow:none}}.first-content{{break-inside:avoid;page-break-after:avoid;margin-top:0}}}}
</style></head><body><main>
{citation_banner}
<h1>상세 열·부력 결과 요약</h1>
<h2>Case Evidence 검사표</h2>
<table><tr><th>검사</th><th>상태</th><th>영향</th><th>다음 조치</th><th>사유 코드</th><th>증적 참조</th></tr>{evidence_rows}</table>
<details class='evidence-detail'><summary>근거 보기 · 레거시 결과 게이트 및 수치 진단</summary>
<p><b>레거시 결과 게이트:</b> {html.escape(str(result_gate.get('citation_status') or 'NOT_EVALUATED'))} · Case Health 인용 판단에 사용하지 않음</p>
<p><b>레거시 차단 항목</b></p><ul>{gate_details}</ul>
<p><b>레거시 판정 사유</b></p><ul>{gate_reasons_html}</ul>
{numerical_quality_html}</details>
<p>상태 <b>{html.escape(str(run.get('status', 'UNKNOWN')))}</b> · 물리시간 {number(summary.get('time_s'), 3, ' s')} · 셀 {int(summary.get('cell_count') or 0):,}</p>
<div class='grid'>
 <div class='card'>최저온도<div class='value'>{number(temperature.get('minimum'), 3, ' K')}</div></div>
 <div class='card'>최고온도<div class='value'>{number(temperature.get('maximum'), 3, ' K')}</div></div>
 <div class='card'>평균속도<div class='value'>{number(velocity.get('mean_speed'), 3, ' m/s')}</div></div>
 <div class='card'>최고속도<div class='value'>{number(velocity.get('maximum_speed'), 3, ' m/s')}</div></div>
</div>
{heat_contract_html}
<h2>주요 위치</h2>
<table><tr><th>항목</th><th>값</th><th>셀 중심좌표 x,y,z (m)</th></tr>
<tr><td>최고온도 셀</td><td>{number(hot.get('temperature_k'), 3, ' K')}</td><td>{coordinate(hot)}</td></tr>
<tr><td>최고속도 셀</td><td>{number(peak.get('speed_m_s'), 3, ' m/s')}</td><td>{coordinate(peak)}</td></tr></table>
<h2>계산 진행률</h2>
<table><tr><th>누적 물리시간</th><th>필요 물리시간</th><th>유동 교환시간 확보율</th><th>예상 남은 실제시간</th></tr>
<tr><td>{number(progress.get('completed_duration_s'), 2, ' s')}</td><td>{number(progress.get('required_duration_s'), 2, ' s')}</td>
<td>{number((progress.get('flow_through_fraction') or 0)*100, 2, '%')}</td><td>{number(None if estimated is None else estimated/60, 1, ' min')}</td></tr></table>
<h2>과도 에너지 폐합</h2>
<p>누적 투입열과 <b>실내 축열 + 누적 배기열</b>을 비교합니다. 초기 가열 중에는 배기 회수율만으로 수렴을 판정하지 않습니다.</p>
<table><tr><th>누적 투입열</th><th>실내 축열</th><th>누적 배기열</th><th>과도 폐합률</th></tr>
<tr><td>{number(energy.get('input_energy_j'), 1, ' J')}</td>
<td>{number(energy.get('stored_sensible_energy_j'), 1, ' J')}</td>
<td>{number(energy.get('cumulative_exhaust_energy_j'), 1, ' J')}</td>
<td>{number(None if energy.get('transient_closure_ratio') is None else energy.get('transient_closure_ratio')*100, 2, '%')}</td></tr></table>
<small>계산 방법: <code>{html.escape(str(energy.get('method') or '확인 불가'))}</code> · 배기 이력 완전성: {'완전' if energy.get('history_complete') else '불완전'}</small>
<h2>좌표 기반 중앙 단면</h2>
<table><tr><th>축</th><th>목표 좌표</th><th>표본 셀</th></tr>{slice_rows}</table>
<small>단면은 대표 셀 길이 절반 폭 안의 cell 중심을 좌표로 선택합니다. 통계는 <code>{html.escape(str(summary.get('aggregation')))}</code>이며 체적가중 평균이 아닙니다.</small>
<h2>남은 진단</h2><ul>{warning_rows or '<li>없음</li>'}</ul>
</main></body></html>"""
    out_html = out_html or os.path.join(case_dir, "body_fitted_report.html")
    try:
        with open(out_html, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "report_path": out_html, "summary": summary}

# ── 로그 파싱 정규식 (표준 OpenFOAM SIMPLE/PIMPLE 로그) ──────────────────────
_RE_TIME = re.compile(r"^Time = ([\d.eE+-]+)\s*$")
_RE_RESID = re.compile(
    r"Solving for (\w+),\s*Initial residual = ([\d.eE+-]+),\s*Final residual = ([\d.eE+-]+)")
_RE_CONT = re.compile(
    r"time step continuity errors : sum local = ([\d.eE+-]+), global = ([\d.eE+-]+)")
_RE_RHO = re.compile(r"rho min/max\s*:\s*([\d.eE+-]+)\s+([\d.eE+-]+)")
_RE_BOUND = re.compile(r"bounding (\w+), min: ([\d.eE+-]+) max: ([\d.eE+-]+)")
# 실제 크래시 시그니처만 (시작 배너 "trapFpe: ... trapping enabled (FOAM_SIGFPE)" 는 정상 → 제외)
_RE_CRASH = re.compile(r"sigFpe::sigHandler|error::printStack|\(core dumped\)|"
                       r"Foam::fatalError|#\d+\s+Foam::")


def _f(s):
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def parse_log(text):
    """OpenFOAM 솔버 로그 텍스트 → 구조화 결과.
    반환: {
      iters: [time,...],                 # 각 Time = N (SIMPLE 반복)
      residuals: {field: [initial,...]}, # iteration별 초기잔차
      continuity_local: [...], continuity_global: [...],
      rho_min: [...], rho_max: [...],    # (압축성 솔버만; Boussinesq 는 빔)
      bounding: [(time, field, min, max), ...],  # 불안정 신호
      crashed: bool, n_iters: int
    }"""
    iters = []
    residuals = {}
    cont_local, cont_global = [], []
    rho_min, rho_max = [], []
    bounding = []
    crashed = False
    cur_time = None
    seen_fields_this_step = set()

    for line in text.splitlines():
        m = _RE_TIME.match(line)
        if m:
            cur_time = _f(m.group(1))
            iters.append(cur_time)
            seen_fields_this_step = set()
            continue
        if _RE_CRASH.search(line):
            crashed = True
            continue
        m = _RE_RESID.search(line)
        if m and cur_time is not None:
            field, init = m.group(1), _f(m.group(2))
            # 한 스텝에서 같은 필드는 첫(초기) 잔차만 (nCorrectors 대비)
            if field in seen_fields_this_step:
                continue
            seen_fields_this_step.add(field)
            residuals.setdefault(field, {})[cur_time] = init
            continue
        m = _RE_CONT.search(line)
        if m and cur_time is not None:
            cont_local.append((cur_time, _f(m.group(1))))
            cont_global.append((cur_time, _f(m.group(2))))
            continue
        m = _RE_RHO.search(line)
        if m and cur_time is not None:
            rho_min.append((cur_time, _f(m.group(1))))
            rho_max.append((cur_time, _f(m.group(2))))
            continue
        m = _RE_BOUND.search(line)
        if m:
            bounding.append((cur_time, m.group(1), _f(m.group(2)), _f(m.group(3))))

    # 필드별 시계열을 iter 순서로 정렬된 리스트로
    resid_series = {}
    for field, d in residuals.items():
        resid_series[field] = [d.get(t) for t in iters]

    return {
        "iters": iters,
        "residuals": resid_series,
        "continuity_local": cont_local,
        "continuity_global": cont_global,
        "rho_min": rho_min,
        "rho_max": rho_max,
        "bounding": bounding,
        "crashed": crashed,
        "n_iters": len(iters),
    }


# 단일 정의는 cfd_convergence_spec 을 참고 — 여기서는 재정의하지 않고 그대로 가져온다.
CONVERGENCE_TARGETS = cfd_convergence_spec.FORECAST_TARGET_RESIDUALS


def residual_decay_forecast(parsed, targets=None, tail_frac=0.35, min_points=20):
    """잔차 시계열의 로그선형 감쇠율 → 목표 잔차까지 남은 반복수 추정.

    왜 필요한가: 잔차 그래프만 봐서는 "아직 내려가는 중"인지 "정체"인지 구분이 안 되고,
    남은 계산량은 더더욱 모른다. 실측 사고에서 T 잔차는 1e-3 에서 반복당 0.1% 씩만
    감소 중이었다 — 1e-5 까지 약 4,600회가 더 필요한 상태였는데 리포트는 그냥
    '계산완료'로 표시됐다. 감쇠율을 회귀로 뽑으면 "몇 회 더" 를 숫자로 말할 수 있다.

    반환: {field: {last, target, rate_per_iter, iters_to_target}}
          iters_to_target=0 은 이미 도달, None 은 정체/발산(반복만으로는 도달 불가).
    """
    import math
    targets = targets or CONVERGENCE_TARGETS
    out = {}
    for field, target in targets.items():
        ser = [v for v in (parsed.get("residuals", {}).get(field) or [])
               if v is not None and v > 0]
        if len(ser) < min_points:
            continue
        tail = ser[-max(min_points, int(len(ser) * tail_frac)):]
        n = len(tail)
        xs = list(range(n))
        ys = [math.log(v) for v in tail]
        mx, my = sum(xs) / n, sum(ys) / n
        den = sum((x - mx) ** 2 for x in xs)
        if den <= 0:
            continue
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
        last = tail[-1]
        info = {"last": last, "target": target, "rate_per_iter": slope}
        if last <= target:
            info["iters_to_target"] = 0
        elif slope < -1e-12:
            info["iters_to_target"] = int(math.ceil(math.log(last / target) / (-slope)))
        else:
            info["iters_to_target"] = None
        out[field] = info
    return out


def diagnose(parsed):
    """파싱 결과 → 사람이 읽는 진단 요약 문자열 목록."""
    out = []
    n = parsed["n_iters"]
    out.append(f"반복(iteration): {n}")
    if parsed["crashed"]:
        out.append("★ 크래시(sigFpe/발산) 감지 — 솔버가 도중 종료됨.")
    # 마지막 초기잔차
    for field in ("Ux", "Uy", "Uz", "p_rgh", "h", "k", "omega", "epsilon"):
        ser = parsed["residuals"].get(field)
        if ser:
            vals = [v for v in ser if v is not None]
            if vals:
                out.append(f"  {field:7s} 초기잔차: 시작 {vals[0]:.2e} → 끝 {vals[-1]:.2e}"
                           + ("  (하강)" if vals[-1] < vals[0] else "  (미하강/발산)"))
    # rho 음수(압축성 발산 신호)
    if parsed["rho_min"]:
        mn = min(v for _, v in parsed["rho_min"] if v is not None)
        if mn < 0:
            out.append(f"★ 음의 밀도 감지(rho min={mn:.2f}) — 압축성 솔버 발산의 전형. "
                       "Boussinesq(비압축) 전환 권장.")
    if parsed["bounding"]:
        flds = sorted({b[1] for b in parsed["bounding"]})
        out.append(f"  bounding 경고 {len(parsed['bounding'])}회 (필드: {', '.join(flds)}) — 국소 불안정.")
    # 유량수지(continuity global)
    if parsed["continuity_global"]:
        last = parsed["continuity_global"][-1][1]
        out.append(f"  최종 continuity(global): {last:.2e}" + ("  (양호)" if abs(last) < 1e-3 else "  (큼)"))
    return out


def plot_residuals(parsed, out_png, title="OpenFOAM convergence (initial residuals)"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    iters = parsed["iters"]
    if not iters:
        return None
    fig, ax = plt.subplots(figsize=(11, 6))
    order = ["Ux", "Uy", "Uz", "p_rgh", "h", "T", "k", "omega", "epsilon"]
    plotted = 0
    for field in order:
        ser = parsed["residuals"].get(field)
        if not ser:
            continue
        xs = [t for t, v in zip(iters, ser) if v is not None and v > 0]
        ys = [v for v in ser if v is not None and v > 0]
        if len(ys) >= 2:
            ax.semilogy(xs, ys, label=field, lw=1.3)
            plotted += 1
    ax.set_xlabel("iteration")
    ax.set_ylabel("initial residual (log)")
    ax.set_title(title + (f"  —  ★크래시" if parsed["crashed"] else ""))
    ax.grid(True, which="both", alpha=0.3)
    if plotted:
        ax.legend(fontsize=9, ncol=2)
    # 크래시/bounding 표시
    if parsed["crashed"] and iters:
        ax.axvline(iters[-1], color="red", ls="--", alpha=0.6)
        ax.text(iters[-1], ax.get_ylim()[1], " crash", color="red", va="top", fontsize=9)
    fig.tight_layout()
    fig.savefig(out_png, dpi=110)
    plt.close(fig)
    return out_png


# ── 결과 필드 판독 (함수객체 우회: ascii 필드 직접 파싱) ────────────────────
# 이 OpenFOAM v1912 apt 빌드는 함수객체(인라인·postProcess)가 SHA1 버그로 깨졌다.
# 대신 writeFormat=ascii + 균일 구조격자(blockMesh 단일 hex)라는 사실을 이용해,
# 최종 time 디렉토리의 필드를 직접 읽고 셀 인덱스(i + nx*j + nx*ny*k)로 좌표를 복원한다.

def read_field(path):
    """OpenFOAM ascii volScalar/volVectorField 의 internalField 판독.
    반환: ('scalar', [float,...]) | ('vector', [(x,y,z),...])
          | ('uniform_scalar', v) | ('uniform_vector', (x,y,z)) | None"""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as f:
        txt = f.read()
    m = re.search(r"internalField\s+(uniform|nonuniform)", txt)
    if not m:
        return None
    if m.group(1) == "uniform":
        rest = txt[m.end():].lstrip()
        if rest.startswith("("):
            vec = tuple(float(x) for x in re.match(r"\(([^)]*)\)", rest).group(1).split())
            return ("uniform_vector", vec)
        val = float(re.match(r"[-\d.eE+]+", rest).group(0))
        return ("uniform_scalar", val)
    cm = re.search(r"List<(scalar|vector)>\s*\n?\s*(\d+)\s*\n?\s*\(", txt)
    if not cm:
        return None
    is_vec = cm.group(1) == "vector"
    start = cm.end()
    end = txt.index("\n)", start)
    body = txt[start:end]
    if is_vec:
        data = [tuple(float(x) for x in v.split())
                for v in re.findall(r"\(([^)]+)\)", body)]
        return ("vector", data)
    return ("scalar", [float(x) for x in body.split()])


def _as_array(field, ncells):
    """read_field 결과 → numpy 배열(uniform 은 상수 확장). scalar:(N,) vector:(N,3)."""
    import numpy as np
    if field is None:
        return None
    kind, data = field
    if kind == "scalar":
        return np.asarray(data, float)
    if kind == "vector":
        return np.asarray(data, float)
    if kind == "uniform_scalar":
        return np.full(ncells, float(data))
    if kind == "uniform_vector":
        return np.tile(np.asarray(data, float), (ncells, 1))
    return None


def find_latest_time(case_dir):
    """case 디렉토리의 최종(0 아님) time 디렉토리 경로. 없으면 None."""
    times = []
    for name in os.listdir(case_dir):
        full = os.path.join(case_dir, name)
        if os.path.isdir(full) and re.fullmatch(r"\d+(\.\d+)?", name):
            times.append((float(name), full))
    times = [t for t in times if t[0] > 0]
    if not times:
        return None
    return max(times, key=lambda t: t[0])[1]


def read_patch_field(path, patch):
    """time-dir 필드파일의 boundaryField[patch] 값 판독 → [float,...] 또는 상수.
    inletOutlet/calculated 등도 'value' 리스트를 씀. 없으면 None."""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as f:
        txt = f.read()
    # boundaryField 안의 해당 patch 블록만 잘라내기(중괄호 균형)
    m = re.search(r"\b" + re.escape(patch) + r"\s*\{", txt)
    if not m:
        return None
    i = m.end(); depth = 1
    while i < len(txt) and depth:
        if txt[i] == "{":
            depth += 1
        elif txt[i] == "}":
            depth -= 1
        i += 1
    seg = txt[m.end():i - 1]
    mv = re.search(r"value\s+nonuniform\s+List<scalar>\s*\n?\s*(\d+)\s*\n?\s*\(", seg)
    if mv:
        s = mv.end()
        e = seg.find("\n)", s)
        if e == -1:                       # 소형 패치는 한 줄 인라인 `N(a b c)` 포맷
            e = seg.index(")", s)
        return [float(x) for x in seg[s:e].split()]
    mu = re.search(r"value\s+uniform\s+([-\d.eE]+)", seg)
    if mu:
        return ("uniform", float(mu.group(1)))
    return None


def energy_closure(case_dir, meta):
    """발열 kW(체적발열원) 케이스의 에너지 폐합 검증.
    정상상태 + 단열벽이면 주입열 P = 유량가중 배기 엔탈피유출 ρcp·Σ(phi·(T-Tref)).
    잔차가 아니라 이 폐합율(≈100%)이 발열 케이스의 진짜 수렴/신뢰 지표.
    v2: meta['patches'](급배기구 모드)면 배기 패치 전부 합산 + 질량수지 추가.
    반환: {closure_pct, outlet_dT, power_w, vdot[, mass_err_pct]} 또는 None."""
    heat = meta.get("heat", {})
    if heat.get("mode") != "volume":
        return None
    tdir = find_latest_time(case_dir)
    if not tdir:
        return None
    Tref = float(meta["config"].get("inlet", {}).get("T", 293))
    exh_names = ["outlet"]
    patches = meta.get("patches")
    if patches:
        exh_names = [p["name"] for p in patches if p["role"] == "exhaust"]
        sups = [p for p in patches if p["role"] == "supply"]
        if sups:
            Tref = float(sups[0].get("T") or Tref)   # 기준 = 급기온도

    # 회수된 모든 time 스냅샷(최근 3개)에 대해 폐합 계산 → 평균.
    # 이유(실측): 4way 등 제트 충돌 유동은 진동 정상상태(limit cycle) — 단일 스냅샷
    # 폐합이 87~104% 로 요동해 수렴을 오판. 반복 평균이 올바른 판정.
    tdirs = []
    for name in os.listdir(case_dir):
        full = os.path.join(case_dir, name)
        if os.path.isdir(full) and re.fullmatch(r"\d+(\.\d+)?", name) and float(name) > 0:
            tdirs.append((float(name), full))
    tdirs = [d for _, d in sorted(tdirs)][-3:]   # 최근 3개만(과거 과도기 스냅샷 배제)
    power_w = float(heat.get("power_w", 0))
    su = power_w / 1206.0                             # 주입 Su (ρ0·cp=1206)
    samples = []                                      # (closure, vdot, outlet_dT)
    for td in tdirs:
        vdot = enth = 0.0
        got = False
        for nm in exh_names:
            phi = read_patch_field(os.path.join(td, "phi"), nm)
            T = read_patch_field(os.path.join(td, "T"), nm)
            if not isinstance(phi, list) or not isinstance(T, list) or len(phi) != len(T):
                continue
            got = True
            vdot += sum(phi)                                     # 순 배기유량(m³/s)
            enth += sum(p * (t - Tref) for p, t in zip(phi, T))  # Σ phi·ΔT (K·m³/s)
        if got and su:
            samples.append((enth / su * 100.0, vdot, (enth / vdot if vdot else None)))
    if not samples:
        return None
    clos = [s[0] for s in samples]
    closure = sum(clos) / len(clos)
    osc = (max(clos) - min(clos)) / 2 if len(clos) > 1 else 0.0
    vdot = samples[-1][1]
    dts = [s[2] for s in samples if s[2] is not None]
    out = {"closure_pct": closure, "closure_osc": round(osc, 1),
           "closure_n": len(samples),
           "outlet_dT": (sum(dts) / len(dts) if dts else None),
           "power_w": power_w, "vdot": vdot}
    if patches:
        qin = sum((p.get("cmh") or 0) for p in patches if p["role"] == "supply") / 3600.0
        if qin > 0:
            out["mass_err_pct"] = (vdot - qin) / qin * 100.0   # 배기순유량 vs 급기설계유량
    return out


def solid_mask(meta):
    """V3a 실형상: meta config(room_polygon/obstacles) → (nz,ny,nx) 고체 bool 마스크.
    저장하지 않고 결정론 재계산(파일이 진실). 실형상 아니면 None."""
    cfg = meta.get("config", {})
    if not (cfg.get("room_polygon") or cfg.get("obstacles")):
        return None
    import numpy as np
    import cfd_export
    labels = cfd_export.solid_labels(cfg, meta["mesh"])
    nx, ny, nz = meta["mesh"]["nx"], meta["mesh"]["ny"], meta["mesh"]["nz"]
    m = np.zeros(nx * ny * nz, dtype=bool)
    if labels["solid"]:
        m[np.asarray(labels["solid"], dtype=int)] = True
    return m.reshape(nz, ny, nx)


def field_metrics(case_dir, meta):
    """최종 time 디렉토리 → T/U 통계 + 유량·환기 지표(가정값 명시).
    V3a: 고체 셀(방 밖·장애물)은 통계에서 제외(유체만)."""
    import numpy as np
    tdir = find_latest_time(case_dir)
    if not tdir:
        return None
    n = meta["mesh"]["cells"]
    T = _as_array(read_field(os.path.join(tdir, "T")), n)
    U = _as_array(read_field(os.path.join(tdir, "U")), n)
    room = meta["config"]["room"]
    L, W, H = room["L"], room["W"], room["H"]
    inlet = meta["config"].get("inlet", {})
    Tsup_K = float(inlet.get("T", 293))
    smask = solid_mask(meta)
    fluid = None if smask is None else ~smask.reshape(-1)
    nfluid = int(fluid.sum()) if fluid is not None else n
    cell_vol = (L / meta["mesh"]["nx"]) * (W / meta["mesh"]["ny"]) * (H / meta["mesh"]["nz"])
    out = {
        "time_dir": os.path.basename(tdir),
        "T_supply_C": Tsup_K - 273.15,
        "room_volume": round(nfluid * cell_vol, 1) if fluid is not None else L * W * H,
        "solid_n": (n - nfluid) if fluid is not None else 0,
    }
    if T is not None:
        Tc = (T - 273.15) if fluid is None else (T - 273.15)[fluid[:len(T)]]
        out.update(T_avg_C=float(Tc.mean()), T_max_C=float(Tc.max()),
                   T_min_C=float(Tc.min()), dT_rise=float(Tc.mean() - (Tsup_K - 273.15)))
    if U is not None and U.ndim == 2:
        mag = np.linalg.norm(U, axis=1)
        if fluid is not None:
            mag = mag[fluid[:len(mag)]]
        out.update(U_max=float(mag.max()), U_avg=float(mag.mean()))
    # 급기 풍량: fixedValue inlet BC(정확) × 벽 면적. 최소모델은 '벽 전체' → 비현실적일 수 있어 명시.
    roles = meta.get("roles", {})
    wall = inlet.get("wall")
    area = {"x0": W * H, "xL": W * H, "y0": L * H, "yW": L * H,
            "floor": L * W, "ceiling": L * W}.get(wall)
    Uvec = inlet.get("U", [0, 0, 0])
    Umag = float(np.linalg.norm(Uvec)) if Uvec else 0.0
    vol_eff = out["room_volume"]
    if area and Umag > 0:
        Q = Umag * area              # m3/s
        out["supply_area"] = area
        out["supply_U"] = Umag
        out["supply_cmh"] = Q * 3600.0
        out["ach"] = (Q * 3600.0) / vol_eff
        out["supply_full_wall"] = (wall in ("x0", "xL", "y0", "yW", "floor", "ceiling"))
    # v2 급배기구 모드: 설계 풍량은 패치 정의에서 정확히(스냅 실면적 반영)
    patches = meta.get("patches")
    if patches:
        sup_cmh = sum((p.get("cmh") or 0) for p in patches if p["role"] == "supply")
        if sup_cmh:
            out["supply_cmh"] = sup_cmh
            out["ach"] = sup_cmh / vol_eff
            out["supply_full_wall"] = False
        out["n_supply"] = len({p.get("parent_name") or p["name"].split("_q")[0]
                               for p in patches if p["role"] == "supply"})
        out["n_exhaust"] = len({p.get("parent_name") or p["name"].split("_q")[0]
                                for p in patches if p["role"] == "exhaust"})
    # 발열 kW 케이스: 에너지 폐합 검증(신뢰 지표)
    ec = energy_closure(case_dir, meta)
    if ec:
        out["heat_kw"] = ec["power_w"] / 1000.0
        out["closure_pct"] = ec["closure_pct"]
        out["closure_osc"] = ec.get("closure_osc")
        out["outlet_dT"] = ec["outlet_dT"]
        out["mass_err_pct"] = ec.get("mass_err_pct")
    # 해석적 평형온도(에너지수지 해) — CFD 결과의 독립 교차검증 기준.
    # CFD 배기온도가 이 값에서 크게 벗어나면 미수렴이거나 발열·유량 설정 오류다.
    try:
        import cfd_export
        T_eq_K, eq_info = cfd_export.equilibrium_temperature(
            meta.get("config", {}), meta.get("patches"))
        if T_eq_K is not None:
            out["T_eq_C"] = T_eq_K - 273.15
            out["T_eq_dT_K"] = eq_info.get("delta_T_K")
            out["flush_time_s"] = eq_info.get("flush_time_s")
            if ec and ec.get("outlet_dT") is not None:
                # CFD 가 낸 배기 상승온도 vs 이론 상승온도
                out["T_eq_gap_K"] = ec["outlet_dT"] - eq_info["delta_T_K"]
    except Exception:
        pass          # 교차검증은 부가정보 — 실패해도 본 리포트는 나와야 한다
    return out


def _cell_grid(field_arr, meta):
    """평탄 셀배열 → (nz, ny, nx) 재배열 + 셀중심 좌표축. blockMesh 단일 hex 가정."""
    import numpy as np
    nx, ny, nz = meta["mesh"]["nx"], meta["mesh"]["ny"], meta["mesh"]["nz"]
    room = meta["config"]["room"]
    L, W, H = room["L"], room["W"], room["H"]
    g = np.asarray(field_arr[:nx * ny * nz]).reshape(nz, ny, nx)  # [k,j,i]
    xc = (np.arange(nx) + 0.5) * L / nx
    yc = (np.arange(ny) + 0.5) * W / ny
    zc = (np.arange(nz) + 0.5) * H / nz
    return g, xc, yc, zc


def plot_sections(case_dir, meta, out_png, z_target=1.5):
    """수평(z=z_target)·수직(길이방향 중앙) 온도 단면 2매 + 수직면 기류벡터."""
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    tdir = find_latest_time(case_dir)
    if not tdir:
        return None
    n = meta["mesh"]["cells"]
    T = _as_array(read_field(os.path.join(tdir, "T")), n)
    U = _as_array(read_field(os.path.join(tdir, "U")), n)
    if T is None:
        return None
    Tg, xc, yc, zc = _cell_grid(T - 273.15, meta)
    nz, ny, nx = Tg.shape
    room = meta["config"]["room"]
    L, W, H = room["L"], room["W"], room["H"]
    kz = int(np.clip(round(z_target / H * nz - 0.5), 0, nz - 1))
    jy = ny // 2

    # V3a 실형상: 고체(방 밖·장애물) 회색 마스킹 — 색범위·통계는 유체만
    smask = solid_mask(meta)
    if smask is not None:
        Tg = np.ma.masked_array(Tg, mask=smask)
    vmin, vmax = float(Tg.min()), float(Tg.max())
    if vmax - vmin < 0.5:
        vmax = vmin + 0.5
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.2))
    for ax in (ax1, ax2):
        ax.set_facecolor("#b8bcc0")   # 마스크 영역 = 회색 바탕

    # (1) 수평면 z=z_target : 평면 온도분포
    c1 = ax1.contourf(xc, yc, Tg[kz], levels=24, cmap="turbo", vmin=vmin, vmax=vmax)
    ax1.set_title(f"Horizontal plane  z = {zc[kz]:.2f} m  (temperature)")
    ax1.set_xlabel("x [m]"); ax1.set_ylabel("y [m]"); ax1.set_aspect("equal")
    fig.colorbar(c1, ax=ax1, label="T [C]", shrink=0.85)

    # (2) 수직면 y=mid : 성층 + 기류(inlet->outlet, 부력순환)
    Tv = Tg[:, jy, :]                      # (nz, nx)
    c2 = ax2.contourf(xc, zc, Tv, levels=24, cmap="turbo", vmin=vmin, vmax=vmax)
    if U is not None and U.ndim == 2:
        Ug = U[:nx * ny * nz].reshape(nz, ny, nx, 3)
        Ux = Ug[:, jy, :, 0]; Uz = Ug[:, jy, :, 2]
        s = max(1, nx // 20)
        ax2.quiver(xc[::s], zc[::max(1, nz // 12)],
                   Ux[::max(1, nz // 12), ::s], Uz[::max(1, nz // 12), ::s],
                   color="white", scale=None, width=0.003, alpha=0.8)
    ax2.set_title(f"Vertical plane  y = {yc[jy]:.2f} m  (temp + airflow)")
    ax2.set_xlabel("x [m]"); ax2.set_ylabel("height z [m]"); ax2.set_aspect("equal")
    fig.colorbar(c2, ax=ax2, label="T [C]", shrink=0.85)

    fig.tight_layout()
    fig.savefig(out_png, dpi=120)
    plt.close(fig)
    return out_png


def find_log(path):
    """case 디렉토리면 log.*Foam 최신 파일 탐색, 파일이면 그대로."""
    if os.path.isfile(path):
        return path
    if os.path.isdir(path):
        import glob
        cands = glob.glob(os.path.join(path, "log.*Foam")) + \
            glob.glob(os.path.join(path, "log.*Simple*")) + \
            glob.glob(os.path.join(path, "log.*Pimple*"))
        cands = [c for c in cands if "checkMesh" not in c and "blockMesh" not in c]
        if cands:
            return max(cands, key=os.path.getmtime)
    return None


def _read_solver_log(log_path):
    """Read a solver log through one testable, permission-aware seam."""
    with open(log_path, encoding="utf-8", errors="replace") as handle:
        return handle.read()


def _load_opening_boundary_verification(case_dir):
    """Load optional result-side opening evidence without mutating input meta."""
    path = os.path.join(case_dir, "opening_boundary_verification.v1.json")
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return payload if payload.get("contract") == "opening_boundary_verification.v1" else None


# 에너지 폐합 허용범위 [%]. 정상상태·단열벽이면 넣은 열 = 나간 열이므로 100 이어야 한다.
CLOSURE_OK = (90.0, 110.0)      # 이 안이면 양호
CLOSURE_HARD = (75.0, 125.0)    # 이 밖은 물리적으로 성립 불가 → 결과 인용 차단


def _legacy_result_trust(parsed, metrics):
    """결과를 '설계 근거로 인용해도 되는가'로 판정. dict 반환.

    잔차만 보면 "계산은 끝났다"가 되지만, 정상상태·단열벽 케이스에서 에너지 폐합율이
    100%에서 크게 벗어났다는 것은 넣은 열과 나간 열이 안 맞는다는 뜻이고, 그 상태의
    온도 수치는 물리적 의미가 없다. 실측 사고에서 폐합 158%(= 나간 열이 넣은 열의
    1.6배, 불가능)였는데 리포트는 노란 '미수렴' 배지만 달고 평균온도 26.7 °C 를
    그대로 실었다 — 그 값은 사실상 초기장 300 K 였다.

    반환 키:
      badge/color  — 기존 배지(하위호환)
      citable      — False 면 온도·ΔT 를 인용하면 안 된다
      reasons      — 인용 불가 사유(사람이 읽는 문장)
    """
    # Keep the old badge wording, but never let this compatibility adapter own
    # a third copy of the screening threshold contract.  The authoritative
    # limits live in cfd_convergence_spec and are exposed by the result gate.
    import cfd_result_gate

    m = metrics or {}
    cont_ok = parsed["continuity_global"] and abs(parsed["continuity_global"][-1][1]) < 1e-3
    thresholds = cfd_result_gate.RESIDUAL_LIMITS
    checked = []
    for field, limit in thresholds.items():
        values = [v for v in (parsed.get("residuals", {}).get(field) or []) if v is not None]
        if values:
            checked.append(values[-1] <= limit)
    residual_ok = bool(checked) and all(checked)
    clo = m.get("closure_pct")
    reasons = []
    if parsed.get("crashed"):
        return {"badge": "발산/크래시", "color": "#c0392b", "citable": False,
                "reasons": ["솔버가 발산/크래시로 중단됨 — 결과 없음."]}
    if clo is not None:
        osc = m.get("closure_osc") or 0
        mass = m.get("mass_err_pct")
        mass_ok = mass is None or abs(mass) <= 5.0
        closure_ok = CLOSURE_OK[0] <= clo <= CLOSURE_OK[1]
        closure_hard_fail = not (CLOSURE_HARD[0] <= clo <= CLOSURE_HARD[1])
        osc_ok = osc <= 10.0
        tag = f"{clo:.0f}%" + (f"±{osc:.0f}" if osc >= 5 else "")
        if closure_hard_fail:
            # 넣은 열과 나간 열이 25% 넘게 어긋남 = 정상상태 에너지수지 위반.
            # 대개 초기장이 아직 안 빠졌거나(반복 부족) 발열/유량 설정 오류다.
            cause = ("배기로 나가는 열이 주입 열보다 많다 — 초기장 잔열이 아직 배출 중"
                     "(반복 부족)이거나 초기온도가 평형보다 높게 설정됨"
                     if clo > 100 else
                     "주입 열이 배기로 안 나온다 — 아직 실내에 축열 중(반복 부족)이거나 누설")
            reasons.append(
                f"에너지 폐합율 {clo:.0f}% — 정상상태에서는 100%여야 한다. {cause}."
                " 이 상태의 온도·ΔT 는 설계 근거로 인용할 수 없다.")
            return {"badge": f"결과 인용 불가(폐합 {tag})", "color": "#c0392b",
                    "citable": False, "reasons": reasons}
        if closure_ok and cont_ok and mass_ok and osc_ok and residual_ok:
            return {"badge": f"수렴·폐합 양호({tag})", "color": "#1e8449",
                    "citable": True, "reasons": []}
        if closure_ok:
            if not residual_ok:
                reasons.append("주요 잔차가 목표 미달 — 추가 반복 권장.")
            if not cont_ok:
                reasons.append("연속방정식 누적오차 큼.")
            if not mass_ok:
                reasons.append(f"급배기 질량수지 오차 {mass:.1f}%.")
            if not osc_ok:
                reasons.append(f"폐합율이 ±{osc:.0f}% 진동 — 정상상태 미도달 가능.")
            return {"badge": f"계산완료·추가확인(폐합 {tag})", "color": "#b9770e",
                    "citable": True, "reasons": reasons}
        reasons.append(f"에너지 폐합율 {clo:.0f}% — 허용범위"
                       f"({CLOSURE_OK[0]:.0f}~{CLOSURE_OK[1]:.0f}%) 밖. 반복 부족이 가장 흔한 원인.")
        return {"badge": f"미수렴(에너지폐합 {tag})", "color": "#b9770e",
                "citable": False, "reasons": reasons}
    if cont_ok and residual_ok:
        return {"badge": "수렴(양호)", "color": "#1e8449", "citable": True, "reasons": []}
    return {"badge": "부분수렴/확인필요", "color": "#b9770e", "citable": False,
            "reasons": ["연속방정식 또는 잔차가 목표 미달 — 결과 신뢰도 확인 필요."]}


def _energy_balance_required(meta):
    """Return whether this case injects a non-zero volumetric heat load.

    A volume source is the only legacy/screening heat model for which the
    energy-closure calculation is a required result check.  Prefer the
    exported case metadata; fall back to the input config for older cases
    that predate the ``heat`` metadata block.
    """
    def positive(value):
        try:
            return float(value) > 0.0
        except (TypeError, ValueError):
            return False

    heat = meta.get("heat") if isinstance(meta, dict) else None
    if isinstance(heat, dict):
        return heat.get("mode") == "volume" and positive(heat.get("power_w"))

    config = (meta or {}).get("config") or {}
    config_heat = config.get("heat") or {}
    return positive(config_heat.get("power_w")) or positive(config_heat.get("power_kw"))


def result_trust(parsed, metrics, model_quality=None, energy_required=False,
                 opening_preflight=None, opening_verification=None):
    """Return the legacy badge plus the shared ``result_trust.v1`` contract.

    ``badge`` and ``color`` remain stable for existing reports.  The added
    contract fields make a warning unambiguously non-citable and keep the
    structured-grid screening path separate from body-fitted design review.
    """
    import cfd_result_gate

    legacy = _legacy_result_trust(parsed, metrics)
    gate = cfd_result_gate.evaluate_screening_result(
        parsed,
        metrics,
        model_quality=model_quality,
        energy_required=energy_required,
        opening_preflight=opening_preflight,
        opening_verification=opening_verification,
    )
    # The legacy helper keeps familiar wording for trustworthy screening
    # results, but it intentionally tolerated missing fields for historical
    # reports.  A current result gate fails closed on missing evidence; do not
    # present its non-citable state with a green legacy badge.
    if (gate["status"] in ("NOT_EVALUATED", "FAIL")
            and legacy["citable"]):
        if gate["status"] == "FAIL":
            legacy["badge"], legacy["color"] = "결과 인용 불가", "#c0392b"
        elif gate["status"] == "NOT_EVALUATED":
            legacy["badge"], legacy["color"] = "결과 평가 불가", "#b9770e"
        else:
            legacy["badge"], legacy["color"] = "결과 추가 확인 필요", "#b9770e"
        if gate["reasons"]:
            legacy["reasons"] = list(gate["reasons"])
    legacy.update({
        "contract": gate["contract"],
        "status": gate["status"],
        "run_status": gate["run_status"],
        "convergence_status": gate["convergence_status"],
        "design_ready": gate["design_ready"],
        "citation_status": gate["citation_status"],
        "citable": gate["citable"],
        "blockers": gate["blockers"],
        "evidence": gate["evidence"],
    })
    if not legacy["reasons"]:
        legacy["reasons"] = gate["reasons"]
    return legacy


def convergence_badge(parsed, metrics):
    """하위호환 래퍼 — (배지문구, 색). 판정 본체는 result_trust()."""
    t = result_trust(parsed, metrics)
    return (t["badge"], t["color"])


def _opening_preflight_summary(preflight):
    """Return dashboard-safe opening evidence without changing CFD policy.

    ``supply_u`` remains the area-weighted velocity actually applied to
    snapped OpenFOAM patches.  When physical terminals are available, expose
    the corresponding design-face velocity separately so a coarse mesh never
    makes a snapped value look like a catalogue/design value.
    """
    unavailable = {
        "opening_preflight_status": "NOT_AVAILABLE",
        "opening_resolution_ok": None,
        "jet_metrics_citable": None,
        "opening_terminal_count": 0,
        "opening_warning_count": 0,
        "design_supply_u": None,
        "snapped_supply_u": None,
    }
    if not isinstance(preflight, dict) or preflight.get("contract") != "opening_preflight.v2":
        return unavailable

    terminals = preflight.get("terminals")
    terminals = terminals if isinstance(terminals, list) else []
    warnings = preflight.get("warnings")
    warnings = warnings if isinstance(warnings, list) else []

    def aggregate_face_velocity(area_key, flow_key):
        area_total = 0.0
        flow_total = 0.0
        for terminal in terminals:
            if not isinstance(terminal, dict) or terminal.get("role") != "supply":
                continue
            try:
                area = float(terminal.get(area_key))
                flow = float(terminal.get(flow_key))
            except (TypeError, ValueError):
                continue
            if area > 0.0 and flow >= 0.0:
                area_total += area
                flow_total += flow
        if area_total <= 0.0:
            return None
        return round(flow_total / 3600.0 / area_total, 6)

    terminal_count = preflight.get("terminal_count")
    if not isinstance(terminal_count, int) or terminal_count < 0:
        terminal_count = len(terminals)
    return {
        "opening_preflight_status": "AVAILABLE",
        "opening_resolution_ok": bool(preflight.get("opening_resolution_ok")),
        "jet_metrics_citable": bool(preflight.get("jet_metrics_citable")),
        "opening_terminal_count": terminal_count,
        "opening_warning_count": len(warnings),
        "design_supply_u": aggregate_face_velocity("requested_area_m2", "design_cmh"),
        "snapped_supply_u": aggregate_face_velocity("snapped_area_m2", "applied_normal_cmh"),
    }


def _case_summary_uncached(case_dir):
    """케이스 폴더 → 대시보드 행 dict. 실행 전(meta만)·실행 후·리포트 유무 모두 처리.
    meta 없으면 None(케이스 아님)."""
    import glob as _glob
    import math
    meta = _load_meta(case_dir)
    if not meta:
        return None
    opening_verification = _load_opening_boundary_verification(case_dir)
    cfg = meta.get("config", {})
    room = cfg.get("room", {})
    heat = meta.get("heat", {})
    # 개구부 모드에서는 cfg.inlet.U가 비어 있고 실제 급기속도는 patches에 있다.
    # 패치 면적으로 가중한 속도 크기를 표시하면 분할된 4-way 패치와 격자 스냅을
    # 모두 반영하면서, 기존 벽 전체 급기 케이스의 표시도 그대로 유지할 수 있다.
    weighted_speed = 0.0
    supply_area = 0.0
    for patch in meta.get("patches") or []:
        if patch.get("role") != "supply":
            continue
        try:
            area = float(patch.get("area") or 0.0)
            vector = patch.get("U")
            if vector:
                speed = math.sqrt(sum(float(value) ** 2 for value in vector))
            elif area > 0 and patch.get("cmh") is not None:
                speed = float(patch["cmh"]) / (3600.0 * area)
            else:
                continue
        except (TypeError, ValueError):
            continue
        if area > 0:
            weighted_speed += speed * area
            supply_area += area

    if supply_area > 0:
        supply_u = weighted_speed / supply_area
    else:
        Uvec = cfg.get("inlet", {}).get("U", [0, 0, 0])
        supply_u = math.sqrt(sum(float(v) ** 2 for v in Uvec)) if Uvec else 0.0
    if heat.get("mode") == "volume":
        heat_label = f"{heat.get('power_w', 0) / 1000.0:g} kW"
    elif heat.get("mode") == "surface":
        heat_label = f"바닥 {heat.get('floor_T', '?')}K"
    else:
        heat_label = "—"
    opening_summary = _opening_preflight_summary(meta.get("opening_preflight"))
    out = {
        "dir": os.path.basename(os.path.abspath(case_dir)),
        "name": cfg.get("name") or os.path.basename(case_dir),
        "room": f"{room.get('L','?')}×{room.get('W','?')}×{room.get('H','?')}",
        "cells": meta.get("mesh", {}).get("cells"),
        "heat_label": heat_label,
        "heat_kw": (heat.get("power_w", 0) / 1000.0) if heat.get("mode") == "volume" else None,
        "supply_u": round(supply_u, 3),
        "endTime": cfg.get("endTime"),
        "mtime": os.path.getmtime(os.path.join(case_dir, "cfd_case_meta.json")),
        "status": "created",
        "badge": "미실행", "badge_color": "#7f8c8d",
        "result_contract": "result_trust.v1",
        "result_status": "NOT_EVALUATED",
        "run_status": "NOT_EVALUATED",
        "convergence_status": "NOT_EVALUATED",
        "design_ready": False,
        "citation_status": "NOT_EVALUATED",
        "citable": False,
        "blockers": ["solver_not_run"],
        "T_avg_C": None, "T_max_C": None, "dT_rise": None,
        "closure_pct": None, "outlet_dT": None, "n_iters": None,
        "gci": meta.get("gci"),
        "report": None,
        "from_geometry": bool(meta.get("from_geometry")),
        "opening_verification_status": (
            opening_verification.get("status") if opening_verification else "NOT_AVAILABLE"
        ),
        **opening_summary,
    }
    logp = find_log(case_dir)
    if logp:
        out["status"] = "ran"
        try:
            parsed = parse_log(_read_solver_log(logp))
        except OSError:
            out.update({
                "status": "unreadable",
                "badge": "솔버 로그 접근 불가",
                "badge_color": "#c0392b",
                "result_status": "NOT_EVALUATED",
                "run_status": "NOT_EVALUATED",
                "convergence_status": "NOT_EVALUATED",
                "design_ready": False,
                "citation_status": "NOT_EVALUATED",
                "citable": False,
                "blockers": ["solver_log_unreadable"],
            })
            return out
        metrics = None
        try:
            metrics = field_metrics(case_dir, meta)
        except Exception:
            pass
        trust = result_trust(
            parsed,
            metrics,
            model_quality=meta.get("model_quality"),
            energy_required=_energy_balance_required(meta),
            opening_preflight=meta.get("opening_preflight"),
            opening_verification=opening_verification,
        )
        out["badge"], out["badge_color"] = trust["badge"], trust["color"]
        out.update({
            "result_contract": trust["contract"],
            "result_status": trust["status"],
            "run_status": trust["run_status"],
            "convergence_status": trust["convergence_status"],
            "design_ready": trust["design_ready"],
            "citation_status": trust["citation_status"],
            "citable": trust["citable"],
            "blockers": trust["blockers"],
        })
        out["n_iters"] = parsed["n_iters"]
        if metrics:
            for k in ("T_avg_C", "T_max_C", "dT_rise", "closure_pct", "outlet_dT",
                      "supply_cmh", "ach", "U_max", "mass_err_pct", "n_supply", "n_exhaust"):
                out[k] = metrics.get(k)
    reps = _glob.glob(os.path.join(case_dir, "cfd_report_*.html"))
    if reps:
        out["report"] = os.path.basename(max(reps, key=os.path.getmtime))
        if out["status"] == "ran":
            out["status"] = "reported"
    return out


def _latest_case_report(case_dir):
    import glob as _glob

    reports = _glob.glob(os.path.join(case_dir, "cfd_report_*.html"))
    return max(reports, key=os.path.getmtime) if reports else None


def case_summary(case_dir):
    """Return one case summary, using a validated on-disk cache when fresh.

    The cache belongs here rather than in the Studio scanner so CLI and GUI
    callers use the same freshness rules.  A result calculated while solver
    files are changing is returned to the caller but never published.
    """
    import cfd_case_cache

    def fingerprint():
        return cfd_case_cache.summary_fingerprint(
            case_dir,
            log_path=find_log(case_dir),
            report_path=_latest_case_report(case_dir),
        )

    try:
        with cfd_case_cache.case_lock(case_dir):
            before = fingerprint()
            cached = cfd_case_cache.load(case_dir, before)
            # A solver can append a log or write a field after the cache read.
            # Never serve the cached result unless the inputs still match.
            if cached is not None and before == fingerprint():
                return cached
            summary = _case_summary_uncached(case_dir)
            # A permission/transient I/O hold must be retried next time.  It
            # is not a result of this input state and must never become a
            # sticky dashboard cache entry after access is repaired.
            if (summary is not None and summary.get("status") != "unreadable"
                    and before == fingerprint()):
                try:
                    cfd_case_cache.publish(case_dir, before, summary)
                except OSError:
                    pass
            return summary
    except OSError:
        return _case_summary_uncached(case_dir)


def _b64(png_path):
    import base64
    with open(png_path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def _fmt(v, unit="", nd=1):
    if v is None:
        return "—"
    return f"{v:,.{nd}f}{unit}"


def build_html_report(case_dir, meta, parsed, resid_png, sect_png, metrics,
                      out_html, *, case_health=None):
    """자립 HTML 해석 리포트(보고서 첨부 품질). preview.py 계열 스타일."""
    import datetime
    import html as _html
    cfg = meta.get("config", {})
    name = cfg.get("name", os.path.basename(case_dir))
    room = cfg.get("room", {})
    diag = diagnose(parsed)
    m = metrics or {}
    opening_verification = _load_opening_boundary_verification(case_dir)
    trust = result_trust(
        parsed,
        metrics,
        model_quality=meta.get("model_quality"),
        energy_required=_energy_balance_required(meta),
        opening_preflight=meta.get("opening_preflight"),
        opening_verification=opening_verification,
    )
    badge, bcol = trust["badge"], trust["color"]
    citable = trust.get("citable", True)

    # 부제는 케이스 실제 내용에서 유도한다(과거엔 "전기실 발열·환기"가 하드코딩돼
    # 로비 EHP 케이스에도 그대로 붙어 제목과 어긋났다).
    _scope = []
    if m.get("heat_kw") is not None:
        _scope.append(f"발열 {m['heat_kw']:g} kW")
    if m.get("supply_cmh"):
        _scope.append(f"급기 {m['supply_cmh']:,.0f} CMH")
    if m.get("ach"):
        _scope.append(f"{m['ach']:.2f} ACH")
    scope_label = " · ".join(_scope + ["정상상태 부력유동 해석"])

    # 인용 불가 판정이면 최상단에 정지 배너를 띄우고, 남은 반복수까지 숫자로 제시한다.
    trust_banner = ""
    if not citable:
        items = list(trust.get("reasons") or [])
        fc = residual_decay_forecast(parsed)
        need = {f: i["iters_to_target"] for f, i in fc.items()
                if i["iters_to_target"]}
        if need:
            worst = max(need, key=lambda f: need[f])
            items.append(
                f"현재 잔차 감쇠율 기준 목표 도달까지 <b>약 {need[worst]:,}회</b> 추가 반복 필요"
                f"(최다 필요 필드 {worst}, 현재 {parsed['n_iters']:,}회 수행)."
                + (" 정체 필드: " + ", ".join(f for f, i in fc.items()
                                             if i["iters_to_target"] is None)
                   if any(i["iters_to_target"] is None for i in fc.values()) else ""))
        if m.get("T_eq_C") is not None:
            items.append(
                f"에너지수지 해(단열벽 정상상태)로는 실내 평형온도가 <b>{m['T_eq_C']:.1f} °C</b> 여야 한다"
                " — 이 값과 CFD 온도의 차이가 미수렴 정도를 나타낸다.")
        trust_banner = ('<div class="stop"><b>✗ 이 결과는 설계 근거로 인용할 수 없습니다.</b><ul>'
                        + "".join(f"<li>{x}</li>" for x in items) + "</ul></div>")
    # 해석조건(입력 가정) 행
    assum = []
    fg = meta.get("from_geometry")
    if fg:
        src = cfg.get("_note", "").split("·")[1].strip() if "·" in cfg.get("_note", "") else fg.get("source", "")
        prov = f"도면 자동추출 ({fg.get('source','?')})"
        if fg.get("equipment"):
            prov += f" · 장비 {fg['equipment']}개 감지(바닥발열로 단순화)"
        if fg.get("openings_by_wall"):
            prov += f" · 경계 개구부 {fg['openings_by_wall']}"
        assum.append(("치수 출처", prov))
    assum.append(("실 치수", f"{room.get('L','?')} × {room.get('W','?')} × {room.get('H','?')} m (L×W×H)"))
    assum.append(("체적", _fmt(m.get("room_volume"), " m³", 1)))
    assum.append(("격자", f"{meta['mesh']['nx']}×{meta['mesh']['ny']}×{meta['mesh']['nz']} = {meta['mesh']['cells']:,} cells (셀 {cfg.get('mesh',{}).get('cell','?')} m)"))
    assum.append(("솔버", "buoyantBoussinesqSimpleFoam (정상상태·부력·비압축 Boussinesq)"))
    patches = meta.get("patches")
    quality = meta.get("model_quality") or {}
    if quality:
        assum.append(("형상 모델", "균일격자 다공성 셀 근사(예비 스크리닝·확정설계용 아님)"))
    if patches:
        # v2 급배기구 목록 (스냅된 실면적·실풍량)
        seen = {}
        for p in patches:
            base = p.get("parent_name") or p["name"].split("_q")[0]
            e = seen.setdefault(base, {"role": p["role"], "type": p.get("type"),
                                       "wall": p["wall"], "area": 0.0, "cmh": 0.0,
                                       "target_cmh": p.get("design_cmh")})
            e["area"] += p.get("area") or 0
            e["cmh"] += p.get("cmh") or 0
        rows_txt = " · ".join(
            (f"{k}[{v['type']},{v['wall']}] {v['area']:.2f}㎡ {v['cmh']:.0f}CMH" if v["role"] == "supply"
             else f"{k}[배기,{v['wall']}] {v['area']:.2f}㎡"
                  + (f" 설계목표 {v['target_cmh']:.0f}CMH(압력출구·실제유량은 결과 phi 확인)"
                     if v.get("target_cmh") is not None else "(압력출구·실제유량은 결과 phi 확인)"))
            for k, v in seen.items())
        assum.append(("급배기구", rows_txt))
        assum.append(("총 급기(설계)", f"{_fmt(m.get('supply_cmh'),' CMH',0)} · {_fmt(m.get('ach'),' ACH',1)}"))
        preflight = meta.get("opening_preflight") or {}
        if preflight.get("contract") == "opening_preflight.v2":
            terminal_rows = preflight.get("terminals") or []
            jet_ok = bool(preflight.get("jet_metrics_citable"))
            pressure_targets = [row for row in terminal_rows
                                if row.get("flow_control") == "pressure_outlet"]
            note = ("부모 단말 기준 스냅 면적·정상속도 사전검증. "
                    + ("제트/최대유속 지표 사용 가능" if jet_ok else
                       "제트·최대유속은 설계 판단에 사용하지 않음(개구부 해상도/사분면 균형 확인 필요)"))
            if pressure_targets:
                note += "; 배기 설계 CMH는 압력출구 목표값이며 실제 배기량은 결과 phi로 확인"
            assum.append(("개구부 사전검증", note))
        opening_result = _load_opening_boundary_verification(case_dir)
        if opening_result:
            terminal_checks = opening_result.get("terminals") or []
            area_warn = sum(1 for row in terminal_checks if row.get("area_status") == "WARN")
            flow_warn = sum(1 for row in terminal_checks if row.get("flow_status") == "WARN")
            actual_note = (f"{opening_result.get('status')} — 실제 boundary 면적/phi 유량 검증 "
                           f"(면적 경고 {area_warn}, 유량 경고 {flow_warn})")
            if opening_result.get("status") in ("PARTIAL", "NOT_AVAILABLE"):
                actual_note += "; polyMesh 또는 phi 미회수 항목은 재검증 필요"
            assum.append(("개구부 결과검증", actual_note))
    elif m.get("supply_cmh"):
        fw = " ※최소모델: 벽면 전체를 급기로 단순화 → 풍량·ACH 비현실적, 급배기구(openings) 모드 권장" if m.get("supply_full_wall") else ""
        assum.append(("급기(가정)", f"{_fmt(m.get('supply_U'),' m/s',3)} × {_fmt(m.get('supply_area'),' m²',1)} = {_fmt(m.get('supply_cmh'),' CMH',0)} · {_fmt(m.get('ach'),' ACH',1)}{fw}"))
    assum.append(("급기온도(가정)", _fmt(m.get("T_supply_C"), " °C", 1)))
    # 벽 열경계는 결과 해석의 전제다. 단열이 아니면 벽으로 열이 드나들어 에너지수지가
    # 급배기만으로 닫히지 않으므로, 폐합율 판정의 의미도 달라진다 → 명시적으로 공개.
    assum.append(("벽 열경계", "단열(zeroGradient) — 벽·바닥·천장 통과 열손실 0 가정. "
                              "실제 구조체 열손실이 있으면 실온은 이보다 낮아진다."))
    assum.append(("초기장 온도", (f"{m['T_eq_C']:.1f} °C (에너지수지 평형해로 초기화 — "
                                "초기장 배출 과도기를 없애 수렴 가속)")
                  if m.get("T_eq_C") is not None else "설정값"))
    heat = cfg.get("heat", {})
    # 발열이 '바닥층 균질 체적원'이면 장비 위치별 국부 과열은 계산에 들어있지 않다.
    # 그런데 결과표의 '최고 온도'는 마치 핫스팟인 것처럼 읽힌다 — 실측 사고에서
    # 평균 26.7 / 최고 27.2 (편차 0.5 K)를 보고 "핫스팟 없음"으로 오독될 뻔했다.
    # 실제 의미는 "핫스팟을 모델에 안 넣었다" 이므로 리포트가 먼저 말해야 한다.
    hotspot_resolved = meta.get("heat", {}).get("via") == "obstacles"
    if m.get("heat_kw") is not None:
        if hotspot_resolved:
            eqz = meta.get("equip_zones") or []
            heat_meta = meta.get("heat") or {}
            input_w = float(heat_meta.get("input_power_w") or heat_meta.get("power_w") or 0.0)
            applied_w = float(heat_meta.get("applied_convective_power_w")
                              or heat_meta.get("power_w") or 0.0)
            excluded_w = float(heat_meta.get("excluded_radiative_power_w") or 0.0)
            source_rows = []
            for source in eqz:
                label = _html.escape(str(source.get("source_label")
                                             or source.get("source_id") or "장비"))
                sid = _html.escape(str(source.get("source_id") or ""))
                evidence = _html.escape(str(source.get("evidence") or "근거 미기록"))
                source_rows.append(
                    f"{label}{f'[{sid}]' if sid else ''}: "
                    f"입력 {float(source.get('input_power_w') or 0) / 1000:g} kW, "
                    f"대류 적용 {float(source.get('convective_power_w') or 0) / 1000:g} kW, "
                    f"근거 {evidence}"
                )
            assum.append((
                "발열(입력·적용)",
                f"장비 {len(eqz)}대 위치별 다공성-voxel 체적원 — "
                f"입력 {_fmt(input_w / 1000,' kW',1)}, "
                f"CFD 대류 주입 {_fmt(applied_w / 1000,' kW',1)}, "
                f"미모델 복사 {_fmt(excluded_w / 1000,' kW',1)}. "
                + "<br>".join(source_rows)
                + "<br><span style=\"color:#7a5c00\">복사분은 현재 legacy 스크리닝에 적용되지 않습니다.</span>",
            ))
        else:
            n_eq = (meta.get("from_geometry") or {}).get("equipment")
            detected = f"도면에서 장비 {n_eq}대가 감지됐으나 " if n_eq else ""
            assum.append(("발열(입력)",
                          f"{_fmt(m.get('heat_kw'),' kW',1)} — 바닥층 <b>균질</b> 체적 발열원"
                          f"(계산서 총발열 직결). {detected}위치·개별 발열량은 반영되지 않았습니다 → "
                          f"<b>반(盤) 단위 국부 과열(핫스팟) 판정 불가</b>. "
                          f"핫스팟 검토가 목적이면 장비별 위치·발열량을 입력해야 합니다."))
    elif heat.get("floor_T") is not None:
        assum.append(("발열(가정)", f"바닥 {heat.get('floor_T','?')}K 고정온도 = 장비 총발열 단순화 (실발열량 아님)"))

    # 결과 지표 행. 인용 불가 판정이면 온도 계열 수치마다 표식을 붙인다
    # (실측 사고: 폐합 158% 인데도 평균온도 26.7 °C 가 그대로 인용됐다 — 초기값이었다).
    res = []
    warn = "" if citable else ' <b style="color:#c0392b">✗ 인용 금지</b>'
    res.append(("평균 온도", _fmt(m.get("T_avg_C"), " °C", 1) + warn))
    res.append(("최고 온도(핫스팟)" if hotspot_resolved else "최고 온도(장내 최대·핫스팟 아님)",
                _fmt(m.get("T_max_C"), " °C", 1) + warn
                + ("" if hotspot_resolved else
                   ' <span style="color:#7a5c00">— 균질 발열원이라 장비 국부 과열은 미포함</span>')))
    res.append(("최저 온도", _fmt(m.get("T_min_C"), " °C", 1) + warn))
    res.append(("급기 대비 상승 ΔT", _fmt(m.get("dT_rise"), " K", 1) + warn))
    opening_preflight = meta.get("opening_preflight") or {}
    jet_metrics_citable = (
        opening_preflight.get("jet_metrics_citable")
        if opening_preflight.get("contract") == "opening_preflight.v2" else True
    )
    if patches and not jet_metrics_citable:
        res.append(("최대 유속", "설계 판단 불가 — 개구부 해상도 또는 4-way 사분면 균형을 개선한 뒤 재생성"))
    else:
        res.append(("최대 유속", _fmt(m.get("U_max"), " m/s", 3)))
    # 해석적 교차검증: 단열벽·정상상태면 배기온도는 T_sup + Q/(ρ·cp·V̇) 여야 한다.
    # CFD 값이 이 값과 크게 다르면 미수렴이거나 발열·유량 설정이 잘못된 것이다.
    if m.get("T_eq_C") is not None:
        cross = f"{m['T_eq_C']:.1f} °C"
        if m.get("T_eq_gap_K") is not None:
            gap = m["T_eq_gap_K"]
            ok = abs(gap) <= 1.0
            cross += (f" &nbsp;(CFD 배기온도와 차 {gap:+.1f} K"
                      f" &nbsp;<b>{'✓ 일치' if ok else '✗ 불일치 — 미수렴/설정오류'}</b>)")
        res.append(("이론 평형온도 (에너지수지 해)", cross))
    if m.get("closure_pct") is not None:
        cv = m["closure_pct"]
        osc = m.get("closure_osc") or 0
        mark = "✓ 에너지수지 양호" if 90 <= cv <= 110 else "✗ 에너지수지 불량 — 추가 수렴 필요"
        oscnote = f" ±{osc:.0f} (진동 유동 — 최근 스냅샷 평균)" if osc >= 5 else ""
        res.append(("에너지 폐합율 (주입열=배기열)", f"{cv:.0f}%{oscnote} &nbsp;<b>{mark}</b>"))
        res.append(("배기 온도상승(유량가중)", _fmt(m.get("outlet_dT"), " K", 2)))
    if m.get("mass_err_pct") is not None:
        mv = m["mass_err_pct"]
        res.append(("질량수지 (배기−급기)/급기", f"{mv:+.1f}%"
                    + (" &nbsp;<b>✓</b>" if abs(mv) < 2 else " &nbsp;<b>✗ 확인 필요</b>")))
    res.append(("반복(iteration)", f"{parsed['n_iters']}"))
    if parsed["continuity_global"]:
        res.append(("최종 연속방정식 오차(global)", f"{parsed['continuity_global'][-1][1]:.2e}"))

    def rows(pairs):
        return "\n".join(
            f'<tr><th>{k}</th><td>{v}</td></tr>' for k, v in pairs)

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    diag_html = "<br>".join(x.replace("★", "⚠") for x in diag)
    model_warn = (f'<div class="warn">⚠ <b>형상 모델 한계:</b> {quality.get("warning")}</div>'
                  if quality.get("warning") else "")
    # 급배기구가 격자에 뭉개지면 제트 도달거리·최대유속을 신뢰할 수 없다.
    # (에너지수지는 유량으로 정해지므로 온도 평균은 영향이 작지만, 국소 기류는 못 쓴다.)
    try:
        import cfd_export as _cx
        _dr = _cx.diffuser_resolution(cfg, patches)
    except Exception:
        _dr = None
    if _dr and _dr["under"]:
        w = _dr["worst"]
        model_warn += (
            f'<div class="warn">⚠ <b>급배기구 격자 해상도 부족:</b> '
            f'{len(_dr["under"])}/{_dr["n_total"]}개 개구부가 한 변 {_dr["min_cells"]:g}셀 미만입니다'
            f'(최소 <code>{w["name"]}</code> {w["area_m2"]:.3f} m² = 한 변 {w["side_m"]:.2f} m '
            f'≈ <b>{w["cells_per_side"]:.1f}셀</b>, 현재 셀 {_dr["cell_m"]:g} m). '
            f'토출 제트가 격자에 뭉개져 <b>제트 도달거리·확산·최대유속</b>은 신뢰할 수 없습니다. '
            f'해상하려면 셀 ≤ <b>{_dr["recommended_cell_m"]:.3f} m</b>(또는 실디퓨저 면적 반영). '
            f'실온·에너지수지는 유량으로 결정되므로 영향이 상대적으로 작습니다.</div>')
    # 설계 개선 추천(결정론) — 미수렴이면 엔진이 스스로 설계 항목을 봉인한다.
    if _dr and _dr["under"]:
        directional = _dr["worst"]
        if (directional.get("quadrant_resolution_ok") is False or
                directional.get("quadrant_balance_ok") is False):
            child_cells = directional.get("child_min_cells_per_side")
            detail = (f" 4-way child minimum {child_cells:.1f} cells."
                      if isinstance(child_cells, (int, float)) else "")
            model_warn += (
                '<div class="warn"><b>4-way directional jet limitation:</b> '
                'The parent terminal flow is conserved, but child quadrants are '
                'not balanced/resolved. Do not use maximum velocity or throw for '
                'design decisions.' + detail + '</div>'
            )
    try:
        import cfd_advice
        _forecast = residual_decay_forecast(parsed)
        advice_health = case_health if isinstance(case_health, dict) else {}
        recs = cfd_advice.recommendations(
            meta, m, parsed, trust, _forecast, case_health=advice_health
        )
    except Exception:
        cfd_advice, recs, _forecast = None, [], {}
    _pcolor = {"차단": "#c0392b", "높음": "#b9770e", "보통": "#2874a6", "참고": "#7f8c8d"}

    def _md_b(s):
        """추천 문구는 마크다운(**강조**)이 원본 — AI 다이제스트와 HTML 이 같은 문자열을
        쓰도록 하고, HTML 쪽에서만 태그로 바꾼다."""
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", str(s))

    if recs:
        rec_html = "".join(
            f'<tr><td style="white-space:nowrap"><b style="color:{_pcolor.get(r["priority"], "#333")}">'
            f'{r["priority"]}</b><br><small>{r["category"]}</small></td>'
            f'<td>{_md_b(r["finding"])}<br><b>→ {_md_b(r["action"])}</b>'
            f'<br><small style="color:#777">근거: {_md_b(r["basis"])}</small></td></tr>'
            for r in recs)
        rec_html = ('<table><tr><th style="width:14%">우선순위</th>'
                    '<th>지적사항 / 조치</th></tr>' + rec_html + "</table>")
    else:
        rec_html = "<p>(추천 없음)</p>"
    sect_img = f'<img src="{_b64(sect_png)}" alt="단면">' if sect_png and os.path.exists(sect_png) else "<p>(단면 없음 — 결과 필드 미기록)</p>"
    resid_img = f'<img src="{_b64(resid_png)}" alt="수렴">' if resid_png and os.path.exists(resid_png) else ""

    html = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CFD 해석 리포트 — {name}</title>
<style>
 :root{{--fg:#1a1a1a;--muted:#666;--line:#e2e2e2;--bg:#fff;--card:#fafafa;--accent:#2c5f8a}}
 *{{box-sizing:border-box}} body{{font-family:'Malgun Gothic','Segoe UI',sans-serif;margin:0;color:var(--fg);background:#f0f2f5}}
 .page{{max-width:960px;margin:24px auto;background:var(--bg);padding:36px 44px;box-shadow:0 1px 8px rgba(0,0,0,.08);border-radius:10px}}
 h1{{font-size:24px;margin:0 0 4px}} .sub{{color:var(--muted);font-size:13px;margin-bottom:18px}}
 .badge{{display:inline-block;color:#fff;background:{bcol};padding:3px 12px;border-radius:14px;font-size:13px;font-weight:600;vertical-align:middle;margin-left:8px}}
 h2{{font-size:16px;margin:26px 0 10px;padding-bottom:6px;border-bottom:2px solid var(--accent);color:var(--accent)}}
 table{{width:100%;border-collapse:collapse;font-size:14px}}
 th,td{{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}}
 th{{width:34%;background:var(--card);font-weight:600;color:#333}}
 .warn{{background:#fff8e1;border:1px solid #f0d060;border-radius:8px;padding:10px 14px;font-size:13px;color:#7a5c00;margin:10px 0}}
 .stop{{background:#fdecea;border:2px solid #c0392b;border-radius:8px;padding:12px 16px;font-size:13.5px;color:#7b1d13;margin:12px 0;line-height:1.7}}
 .stop ul{{margin:8px 0 0 18px;padding:0}} .stop li{{margin:3px 0}}
 .diag{{background:var(--card);border-left:4px solid var(--accent);padding:12px 16px;font-size:13.5px;line-height:1.7;border-radius:4px}}
 img{{max-width:100%;height:auto;border:1px solid var(--line);border-radius:6px;margin-top:8px}}
 .foot{{color:var(--muted);font-size:12px;margin-top:28px;border-top:1px solid var(--line);padding-top:12px}}
 code{{background:#eee;padding:1px 6px;border-radius:4px;font-size:12px}}
</style></head><body><div class="page">
 <h1>CFD 해석 리포트 — {name} <span class="badge">{badge}</span></h1>
 <div class="sub">{scope_label} · 생성 {now} · case <code>{os.path.basename(case_dir)}</code></div>
 {trust_banner}
 <div class="warn">⚠ <b>본 리포트의 풍량·발열·온도는 설계 <u>가정값</u>이며 확정 설계값이 아닙니다.</b>
  실디퓨저 면적·장비별 실발열량·급기조건을 반영하면 수치가 달라집니다. 방법론·경향 검토용입니다.</div>
 {model_warn}

 <h2>1. 수렴성 판정</h2>
 <div class="diag">{diag_html}</div>
 {resid_img}

 <h2>2. 해석 조건 (입력 가정)</h2>
 <table>{rows(assum)}</table>

 <h2>3. 결과 지표</h2>
 <table>{rows(res)}</table>

 <h2>4. 온도·기류 단면</h2>
 {sect_img}
 <div class="sub">좌: 수평면(작업/장비 높이) 온도분포 — 핫스팟 위치. 우: 길이방향 수직면 — 바닥 발열에 의한
  온도 성층과 급기→배기 기류(흰 화살표). 색 범례 단위 °C.</div>

 <h2>5. 설계 개선 추천</h2>
 {rec_html}
 <div class="sub">위 항목은 입력값·기하·에너지수지로부터 <b>규칙 기반 계산</b>된 것입니다(추정 아님).
  배치 재설계·대안 비교 같은 판단이 필요하면 함께 저장된
  <code>cfd_ai_digest_*.md</code> 를 Claude 등 AI 에 붙여넣으십시오 — HTML 리포트는
  이미지가 포함돼 용량이 크므로 그 다이제스트가 AI 입력용입니다.</div>

 <div class="foot">생성 도구: cfd_report.py (도면→CFD 파이프라인) · OpenFOAM {meta.get('_of','v1912')} ·
  재현: <code>python cfd_export.py &lt;config&gt; -o &lt;case&gt; &amp;&amp; python cfd_run.py &lt;case&gt; &amp;&amp; python cfd_report.py &lt;case&gt;</code></div>
</div></body></html>"""
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    # AI 검토용 다이제스트 — HTML 은 base64 이미지 때문에 수백 KB 라 붙여넣기 부적합.
    if cfd_advice is not None:
        try:
            base = os.path.splitext(out_html)[0]
            with open(base + "_ai_digest.md", "w", encoding="utf-8") as f:
                f.write(cfd_advice.ai_digest(
                    meta, m, parsed, trust, _forecast, recs,
                    case_health=advice_health,
                ))
            with open(base + "_ai_digest.json", "w", encoding="utf-8") as f:
                f.write(cfd_advice.digest_payload(
                    meta, m, parsed, trust, _forecast, recs,
                    case_health=advice_health,
                ))
        except Exception:
            pass          # 다이제스트 실패가 본 리포트를 막으면 안 된다
    return out_html


def _load_meta(case_dir):
    import json
    p = os.path.join(case_dir, "cfd_case_meta.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


def _current_case_health_for_report(case_dir, projects_root):
    """Recompute one safe Case Health projection or return a sealed sentinel."""
    if projects_root is None:
        return {}
    evidence_path = Path(case_dir) / "case_evidence.v1.json"
    if not evidence_path.is_file():
        return {}
    try:
        with cfd_review.review_state_lock(
            evidence_path, projects_root=Path(projects_root)
        ):
            return cfd_case_health.build_case_health(
                evidence_path, projects_root=Path(projects_root)
            )
    except Exception:
        return {}


def generate_report(case_dir, out_html=None, quiet=True, *, projects_root=None):
    """케이스 디렉토리 → HTML 리포트 생성(그래프·지표·단면 포함).
    CLI(main)와 스튜디오(cfd_studio)가 공용. 반환: (out_html, metrics) 또는 로그 없으면 예외."""
    logpath = find_log(case_dir)
    if not logpath or not os.path.exists(logpath):
        raise FileNotFoundError(f"솔버 로그 없음: {case_dir}")
    with open(logpath, encoding="utf-8", errors="replace") as f:
        parsed = parse_log(f.read())
    meta = _load_meta(case_dir)
    if not meta:
        raise FileNotFoundError(f"cfd_case_meta.json 없음: {case_dir}")

    def note(msg):
        if not quiet:
            print(msg, file=sys.stderr)

    resid_png = os.path.join(case_dir, "_residuals.png")
    try:
        plot_residuals(parsed, resid_png)
    except Exception as e:
        note(f"수렴 그래프 스킵: {e}")
        resid_png = None
    metrics = None
    try:
        metrics = field_metrics(case_dir, meta)
    except Exception as e:
        note(f"지표 계산 스킵: {e}")
    sect_png = os.path.join(case_dir, "_sections.png")
    try:
        if not plot_sections(case_dir, meta, sect_png):
            sect_png = None
    except Exception as e:
        note(f"단면 스킵: {e}")
        sect_png = None
    out_html = out_html or os.path.join(
        case_dir, f"cfd_report_{meta.get('config', {}).get('name', 'case')}.html")
    case_health = _current_case_health_for_report(case_dir, projects_root)
    build_html_report(
        case_dir, meta, parsed, resid_png, sect_png, metrics, out_html,
        case_health=case_health,
    )
    return out_html, metrics


def main():
    ap = argparse.ArgumentParser(description="OpenFOAM 결과 → 수렴 그래프·지표·단면 HTML 리포트")
    ap.add_argument("input", help="솔버 로그 파일 또는 case 디렉토리")
    ap.add_argument("-o", "--out", default=None, help="출력(로그모드=PNG, 케이스모드=HTML) 경로")
    args = ap.parse_args()

    logpath = find_log(args.input)
    if not logpath or not os.path.exists(logpath):
        print(f"로그를 찾을 수 없음: {args.input}", file=sys.stderr)
        sys.exit(1)
    with open(logpath, encoding="utf-8", errors="replace") as f:
        parsed = parse_log(f.read())

    print(f"로그: {logpath}")
    for line in diagnose(parsed):
        print(line)

    # 케이스 디렉토리 + meta 있으면 전체 HTML 리포트
    case_dir = args.input if os.path.isdir(args.input) else os.path.dirname(logpath)
    if _load_meta(case_dir):
        out_html, metrics = generate_report(case_dir, out_html=args.out, quiet=False)
        if metrics:
            print(f"  평균 {_fmt(metrics.get('T_avg_C'),'°C')} · 최고 {_fmt(metrics.get('T_max_C'),'°C')} · "
                  f"ΔT {_fmt(metrics.get('dT_rise'),'K')} · 최대유속 {_fmt(metrics.get('U_max'),'m/s',3)}")
        print(f"HTML 리포트 -> {out_html}")
    else:
        resid_png = os.path.join(case_dir, "_residuals.png")
        try:
            plot_residuals(parsed, resid_png)
        except Exception as e:
            print(f"수렴 그래프 스킵: {e}", file=sys.stderr)
            resid_png = None
        out = args.out or (os.path.splitext(logpath)[0] + "_residuals.png")
        if resid_png and resid_png != out and os.path.exists(resid_png):
            import shutil
            shutil.copy(resid_png, out)
        print(f"수렴 그래프 -> {out}  (meta 없음 → 로그 전용 모드)")


if __name__ == "__main__":
    main()
