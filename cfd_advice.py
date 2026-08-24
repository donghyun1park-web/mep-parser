# -*- coding: utf-8 -*-
"""해석 결과 → 설계 개선 추천(결정론) + AI(Claude) 입력용 다이제스트.

설계 원칙 — 무엇을 규칙으로 하고 무엇을 AI에 맡기는가
------------------------------------------------------
규칙(이 모듈): "숫자가 답인 것". 필요풍량, 허용온도 초과, 기하 간격, 격자 해상도.
    이런 건 계산식이 정해져 있고 재현돼야 하며, 틀리면 설계가 틀린다.
    → LLM 에 맡기면 안 된다(환각 시 풍량이 바뀐다).
AI(다이제스트): "판단이 답인 것". 디퓨저를 어디로 옮길지, 대안 비교, 설계 의도 해석,
    서술형 보고. 규칙으로 못 박기 어렵고 맥락이 필요하다.

가장 중요한 제약: **미수렴 결과로는 설계 추천을 하지 않는다.**
2026-08 사고(에너지폐합 158%, 온도가 사실상 초기값)에서 보듯, 틀린 결과 위에
추천을 얹으면 틀린 설계를 자동화해서 배포하게 된다. citable=False 면 이 모듈은
"재실행"만 권고하고 설계 항목은 봉인한다.
"""

import json
import math

from cfd_status_catalog import PURPOSE_PROFILES

RHO_CP = 1206.0          # ρ0·cp [J/(m³·K)] — cfd_export 와 동일 기준

# 추천 우선순위
P_BLOCK = "차단"          # 이걸 해결하기 전엔 결과를 못 쓴다
P_HIGH = "높음"
P_MED = "보통"
P_INFO = "참고"

# 토출면 풍속 상한 [m/s]. 초과 시 소음·드래프트 우려(일반 확산기 실무 범위 2~4).
FACE_VELOCITY_WARN = 4.0
# 급기-배기 최단거리 하한 [m]. 이보다 가까우면 급기가 실을 돌지 못하고
# 곧바로 배기로 빨려드는 단락류(short-circuit) 위험.
SHORT_CIRCUIT_MIN_M = 2.0
# 목표 실온 후보 — 용도가 지정 안 됐을 때 표로 제시(추측하지 않는다)
DEFAULT_TARGET_TEMPS_C = (24.0, 26.0, 28.0, 40.0)

GROUP_LABELS = {
    "evidence": "증적·검토",
    "input": "입력 조건",
    "model": "해석 모델",
    "field": "현장 검증",
}
_CATEGORY_GROUPS = {
    "해석 신뢰도": "evidence",
    "풍량": "input",
    "디퓨저 사양": "input",
    "급배기 배치": "model",
    "개구부 모델링": "model",
    "격자": "model",
    "발열 모델": "model",
    "급배기 균형": "model",
    "현장 검증": "field",
}


def _patch_center(p):
    r = p.get("rect_snap") or p.get("rect_req")
    if not r or len(r) < 4:
        return None
    return ((r[0] + r[2]) / 2.0, (r[1] + r[3]) / 2.0)


def _supply_terminals_for_review(meta, supply_patches):
    """Return one area/flow row per physical terminal, not per CFD child patch."""
    preflight = (meta or {}).get("opening_preflight") or {}
    if (preflight.get("contract") == "opening_preflight.v2"
            and isinstance(preflight.get("terminals"), list)):
        rows = []
        for terminal in preflight["terminals"]:
            if terminal.get("role") != "supply":
                continue
            rows.append({
                "name": terminal.get("parent_name") or terminal.get("opening_id"),
                "requested_area_m2": terminal.get("requested_area_m2"),
                "snapped_area_m2": terminal.get("snapped_area_m2"),
                "design_cmh": terminal.get("design_cmh"),
                "applied_normal_cmh": terminal.get("applied_normal_cmh"),
                "requested_rect": terminal.get("requested_rect"),
                "snapped_rect": terminal.get("snapped_rect"),
            })
        return rows

    # Legacy metadata had one row per OpenFOAM patch. Retain it as a fallback
    # so historical reports remain readable, but new 4-way cases always carry
    # opening_preflight.v2.
    rows = []
    for patch in supply_patches:
        rect = patch.get("rect_req") or []
        requested_area = None
        if len(rect) == 4:
            requested_area = (rect[2] - rect[0]) * (rect[3] - rect[1])
        rows.append({
            "name": patch.get("name"),
            "requested_area_m2": requested_area,
            "snapped_area_m2": patch.get("area"),
            "design_cmh": patch.get("design_cmh") or patch.get("cmh_req") or patch.get("cmh"),
            "applied_normal_cmh": patch.get("cmh"),
            "requested_rect": patch.get("rect_req"),
            "snapped_rect": patch.get("rect_snap"),
        })
    return rows


def _terminals_for_layout(meta, patches, role):
    """Return one location per physical terminal for layout checks."""
    preflight = (meta or {}).get("opening_preflight") or {}
    if (preflight.get("contract") == "opening_preflight.v2"
            and isinstance(preflight.get("terminals"), list)):
        rows = []
        for terminal in preflight["terminals"]:
            if terminal.get("role") != role:
                continue
            rect = terminal.get("snapped_rect") or terminal.get("requested_rect")
            if not isinstance(rect, (list, tuple)) or len(rect) != 4:
                continue
            try:
                center = ((float(rect[0]) + float(rect[2])) / 2.0,
                          (float(rect[1]) + float(rect[3])) / 2.0)
            except (TypeError, ValueError):
                continue
            rows.append({
                "name": terminal.get("parent_name") or terminal.get("opening_id"),
                "wall": terminal.get("wall"),
                "center": center,
            })
        return rows
    rows = []
    for patch in patches:
        if patch.get("role") != role:
            continue
        center = _patch_center(patch)
        if center:
            rows.append({"name": patch.get("name"), "wall": patch.get("wall"),
                         "center": center})
    return rows


def required_airflow_cmh(power_w, T_supply_C, target_room_C):
    """목표 실온을 유지하는 데 필요한 최소 급기풍량 [CMH].
    Q = ρcp·V̇·(T_room − T_sup) 를 V̇ 에 대해 푼 것. 단열벽 가정."""
    dT = float(target_room_C) - float(T_supply_C)
    if dT <= 0:
        return None                      # 급기가 목표보다 뜨거우면 냉방 불가
    return power_w / (RHO_CP * dT) * 3600.0


def recommendations(meta, metrics, parsed=None, trust=None, forecast=None,
                    health=None, *, case_health=None):
    """결과 → 설계 추천 목록.

    반환: [{priority, category, finding, action, basis}] — basis 는 판단 근거(수식·수치).
    """
    m = metrics or {}
    cfg = meta.get("config", {})
    patches = meta.get("patches") or []
    recs = []
    authoritative = case_health if case_health is not None else health
    authoritative_valid = (
        isinstance(authoritative, dict)
        and authoritative.get("contract") == "case_health.v1"
        and authoritative.get("citation_status") in {
            "SCREENING_ONLY", "NOT_EVALUATED", "CITATION_BLOCKED",
            "DESIGN_CITABLE",
        }
        and isinstance(authoritative.get("checks"), dict)
    )
    if authoritative is not None:
        citable = (
            authoritative_valid
            and authoritative.get("citation_status") == "DESIGN_CITABLE"
        )
    else:
        citable = True if trust is None else trust.get("citable", True)

    # ── 0. 신뢰 게이트 ────────────────────────────────────────────────
    if not citable and authoritative is not None:
        design = (
            authoritative.get("checks", {}).get("design_ready", {})
            if authoritative_valid else {}
        )
        errors = (authoritative.get("errors") or []) if authoritative_valid else []
        reason_codes = [
            str(row.get("code")) for row in errors
            if isinstance(row, dict) and row.get("code")
        ]
        evidence = (authoritative.get("evidence") or {}) if authoritative_valid else {}
        recs.append({
            "priority": P_BLOCK,
            "category": "해석 신뢰도",
            "finding": (
                f"Case Health {authoritative.get('citation_status')}: "
                f"{design.get('impact') or '권위 있는 Case Health 형식을 확인할 수 없습니다.'}"
                if authoritative_valid else
                "권위 있는 Case Health 형식을 확인할 수 없어 설계 판단을 봉인했습니다."
            ),
            "action": (
                " ".join(str(item) for item in (design.get("next_actions") or []))
                or "Case Evidence와 Case Health를 다시 생성·검증하십시오."
            ),
            "basis": (
                "Case Health 사유 " + (", ".join(reason_codes) or "없음")
                + " · evidence " + str(evidence.get("sha256") or "확인 불가")
            ),
        })
    elif not citable:
        need_txt = ""
        if forecast:
            need = {f: i["iters_to_target"] for f, i in forecast.items()
                    if i.get("iters_to_target")}
            if need:
                worst = max(need, key=lambda f: need[f])
                need_txt = (f" 잔차 감쇠율 기준 약 {need[worst]:,}회 추가 필요"
                            f"(병목 필드 {worst}).")
        recs.append({
            "priority": P_BLOCK,
            "category": "해석 신뢰도",
            "finding": "결과가 수렴/에너지수지 기준을 통과하지 못했습니다. "
                       + " ".join(trust.get("reasons") or []),
            "action": "초기장을 평형온도로 두고 반복수를 늘려 재실행하십시오." + need_txt
                      + " 재실행 전에는 아래 설계 판단을 내리지 마십시오.",
            "basis": f"에너지 폐합율 {m.get('closure_pct', float('nan')):.0f}% "
                     "(정상상태·단열벽이면 100%)",
        })

    if authoritative_valid:
        profile = PURPOSE_PROFILES.get(authoritative.get("purpose")) or {}
        field = authoritative.get("checks", {}).get("field_calibrated") or {}
        if (
            "field_calibrated" in (profile.get("required_checks") or ())
            and field.get("status") != "PASS"
        ):
            recs.append({
                "priority": P_BLOCK,
                "category": "현장 검증",
                "finding": (
                    f"현장 보정 상태 {field.get('status') or 'NOT_EVALUATED'}. "
                    f"{field.get('impact') or ''}"
                ).strip(),
                "action": " ".join(
                    str(item) for item in (field.get("next_actions") or [])
                ) or "현장 측정 및 TAB 증적을 완료하십시오.",
                "basis": (
                    "Case Health field_calibrated · 사유 "
                    + (", ".join(str(item) for item in (field.get("reason_codes") or [])) or "없음")
                    + " · 증적 "
                    + (", ".join(str(item) for item in (field.get("evidence_refs") or [])) or "없음")
                ),
            })

    # ── 1. 풍량 적정성 (수렴 여부와 무관하게 입력값만으로 판정 가능) ──
    power_w = (m.get("heat_kw") or 0) * 1000.0
    T_sup = m.get("T_supply_C")
    design_cmh = m.get("supply_cmh")
    if power_w and T_sup is not None and design_cmh:
        target = (cfg.get("design") or {}).get("target_room_T_C")
        if target:
            need = required_airflow_cmh(power_w, T_sup, target)
            if need:
                ratio = design_cmh / need
                if ratio < 1.0:
                    recs.append({
                        "priority": P_HIGH, "category": "풍량",
                        "finding": f"목표 실온 {target:g} °C 유지에 필요한 풍량"
                                   f" {need:,.0f} CMH 대비 설계 {design_cmh:,.0f} CMH"
                                   f" ({ratio*100:.0f}%) — 부족합니다.",
                        "action": f"급기풍량을 최소 {need:,.0f} CMH 이상으로 증대하거나,"
                                  f" 급기온도를 낮추거나, 발열량을 줄이십시오.",
                        "basis": "V̇ = Q / (ρcp·ΔT), ΔT = 목표실온 − 급기온",
                    })
                else:
                    recs.append({
                        "priority": P_INFO, "category": "풍량",
                        "finding": f"목표 {target:g} °C 기준 필요 {need:,.0f} CMH,"
                                   f" 설계 {design_cmh:,.0f} CMH — 여유 {(ratio-1)*100:.0f}%.",
                        "action": "풍량은 충분합니다. 여유가 과다하면 팬동력 절감 검토 여지가 있습니다.",
                        "basis": "V̇ = Q / (ρcp·ΔT)",
                    })
        else:
            rows = []
            for t in DEFAULT_TARGET_TEMPS_C:
                need = required_airflow_cmh(power_w, T_sup, t)
                if need:
                    rows.append(f"{t:g} °C → {need:,.0f} CMH ({design_cmh/need*100:.0f}%)")
            if rows:
                recs.append({
                    "priority": P_MED, "category": "풍량",
                    "finding": f"목표 실온이 지정되지 않아 판정을 보류했습니다."
                               f" 설계 급기 {design_cmh:,.0f} CMH 기준 목표별 필요풍량: "
                               + " · ".join(rows),
                    "action": "실 용도에 맞는 목표 실온을 정해 판정하십시오"
                              " (예: 전기실=장비 허용주위온도 40 °C, 사무·로비=26 °C 내외)."
                              " cfg['design']['target_room_T_C'] 지정 시 자동 판정합니다.",
                    "basis": "V̇ = Q / (ρcp·ΔT) — 괄호는 설계/필요 비율(100% 미만이면 부족)",
                })

    # ── 2. 급배기 배치: 단락류 위험 ────────────────────────────────────
    sup = [p for p in patches if p.get("role") == "supply"]
    exh = [p for p in patches if p.get("role") == "exhaust"]
    supply_layout = _terminals_for_layout(meta, sup, "supply")
    exhaust_layout = _terminals_for_layout(meta, exh, "exhaust")
    pairs = []
    for s in supply_layout:
        cs = s["center"]
        best = None
        for e in exhaust_layout:
            ce = e["center"]
            d = math.hypot(cs[0] - ce[0], cs[1] - ce[1])
            same_wall = s.get("wall") == e.get("wall")
            if best is None or d < best[0]:
                best = (d, e.get("name"), same_wall)
        if best:
            pairs.append((best[0], s.get("name"), best[1], best[2]))
    if pairs:
        pairs.sort()
        dmin, sname, ename, same_wall = pairs[0]
        if dmin < SHORT_CIRCUIT_MIN_M and same_wall:
            recs.append({
                "priority": P_HIGH, "category": "급배기 배치",
                "finding": f"급기 {sname} 와 배기 {ename} 가 같은 면에서 {dmin:.2f} m 로"
                           f" 인접 — 급기가 실을 돌지 않고 배기로 직행하는"
                           f" 단락류(short-circuit) 위험이 있습니다.",
                "action": f"두 개구부 간격을 최소 {SHORT_CIRCUIT_MIN_M:g} m 이상으로 벌리거나,"
                          " 배기를 반대편/저층부로 옮겨 급기가 실을 통과하도록 하십시오.",
                "basis": f"급기 {len(supply_layout)}개소의 최근접 배기 거리 최소 {dmin:.2f} m",
            })
        else:
            recs.append({
                "priority": P_INFO, "category": "급배기 배치",
                "finding": f"급기-배기 최근접 거리 최소 {dmin:.2f} m"
                           f" (평균 {sum(p[0] for p in pairs)/len(pairs):.2f} m) —"
                           " 기하상 명백한 단락류 배치는 아닙니다.",
                "action": "실제 단락류 여부는 수렴된 결과의 기류선·배기온도로 확인하십시오"
                          " (배기온도가 실 평균보다 낮으면 단락류 징후).",
                "basis": f"개구부 중심 간 수평거리, 기준 {SHORT_CIRCUIT_MIN_M:g} m",
            })

    # ── 3. 개구부 격자 스냅 왜곡 ──────────────────────────────────────
    # 개구부는 격자선에 맞춰 스냅되므로 실면적이 설계면적과 달라진다. 풍량은 유지되니
    # 면적이 줄면 토출속도가 그 역수만큼 뛴다 — 이건 설계값이 아니라 모델 인공물이다.
    # (실측: sup9 가 0.0441→0.0225 m²(51%)로 잘려 2.74→5.48 m/s. 리포트의
    #  '최대 유속 5.38 m/s'는 유동 현상이 아니라 이 급기구 자체였다.)
    terminals = _supply_terminals_for_review(meta, sup)
    distorted = []
    for terminal in terminals:
        a_req = terminal.get("requested_area_m2")
        a_snap = terminal.get("snapped_area_m2")
        try:
            a_req = float(a_req)
            a_snap = float(a_snap)
        except (TypeError, ValueError):
            continue
        if a_req <= 0 or a_snap <= 0:
            continue
        ratio = a_snap / a_req
        if abs(ratio - 1.0) > 0.15:
            distorted.append((ratio, terminal.get("name"), a_req, a_snap))
    if distorted:
        distorted.sort()
        r, nm, a_req, a_snap = distorted[0]
        recs.append({
            "priority": P_HIGH, "category": "개구부 모델링",
            "finding": f"{len(distorted)}개 급기구의 실제 모델 면적이 설계면적과 다릅니다"
                       f" (최악 {nm}: {a_req:.4f} → {a_snap:.4f} m², {r*100:.0f}%)."
                       f" 풍량은 유지되므로 토출속도가 약 {1/r:.1f}배로 부풀려집니다 —"
                       f" 설계 특성이 아니라 **격자 스냅 인공물**입니다.",
            "action": "셀 크기를 개구부 치수의 약수로 맞추거나 더 조밀하게 하여 스냅 왜곡을"
                      " 없애십시오. 그 전까지 '최대 유속'과 해당 급기구 주변 기류는"
                      " 인용하지 마십시오.",
            "basis": "스냅면적/설계면적, 허용 ±15%. 토출속도 = 풍량/면적 이므로 왜곡이 그대로 속도에 전이",
        })

    # ── 3b. 토출 풍속(설계값 기준) ────────────────────────────────────
    fast = []
    for terminal in terminals:
        try:
            area = float(terminal.get("requested_area_m2"))
            cmh = float(terminal.get("design_cmh"))
        except (TypeError, ValueError):
            continue
        if area <= 0 or cmh <= 0:
            continue
        # This is one physical diffuser's design face velocity.  A 4-way
        # diffuser must not be divided into four child patches here: doing so
        # silently quarters both its flow and its design area.
        v = cmh / 3600.0 / area
        if v > FACE_VELOCITY_WARN:
            fast.append((v, terminal.get("name")))
    if fast:
        fast.sort(reverse=True)
        recs.append({
            "priority": P_MED, "category": "디퓨저 사양",
            "finding": f"{len(fast)}개 급기구의 **설계** 토출면 풍속이"
                       f" {FACE_VELOCITY_WARN:g} m/s 를 초과합니다"
                       f"(최대 {fast[0][0]:.2f} m/s @ {fast[0][1]}).",
            "action": "디퓨저 유효면적을 키우거나(개수 증대·규격 상향) 개소당 풍량을 낮춰"
                      " 소음·드래프트를 줄이십시오. 필요 면적 = 풍량/(3600×목표풍속).",
            "basis": f"설계면적 기준 풍속 = 풍량/면적, 실무 권장 2~{FACE_VELOCITY_WARN:g} m/s",
        })

    # ── 4. 격자 해상도 (제트 관련 결과의 신뢰성) ───────────────────────
    try:
        import cfd_export
        dr = cfd_export.diffuser_resolution(cfg, patches)
    except Exception:
        dr = None
    if dr and dr["under"]:
        w = dr["worst"]
        recs.append({
            "priority": P_HIGH if citable else P_MED, "category": "격자",
            "finding": f"{len(dr['under'])}/{dr['n_total']}개 개구부가 한 변"
                       f" {dr['min_cells']:g}셀 미만입니다"
                       f" (최소 {w['name']} ≈ {w['cells_per_side']:.1f}셀).",
            "action": f"제트 거동을 보려면 셀을 {dr['recommended_cell_m']:.3f} m 이하로 줄이거나"
                      " 실디퓨저 면적을 반영하십시오. 현 결과의 제트 도달거리·최대유속은"
                      " 인용하지 마십시오(실온·에너지수지는 영향 작음).",
            "basis": f"등가 한 변 = √면적, 현재 셀 {dr['cell_m']:g} m",
        })

    # ── 5. 핫스팟 모델링 여부 ─────────────────────────────────────────
    if meta.get("heat", {}).get("via") != "obstacles":
        n_eq = (meta.get("from_geometry") or {}).get("equipment")
        if n_eq:
            recs.append({
                "priority": P_MED, "category": "발열 모델",
                "finding": f"도면에서 장비 {n_eq}대가 감지됐으나 발열은 바닥층 균질"
                           " 체적발열원으로 단순화돼 있습니다 — 장비 국부 과열은 계산에 없습니다.",
                "action": "반(盤) 단위 과열 검토가 목적이면 장비별 위치·개별 발열량을 입력해"
                          " 재해석하십시오. 현 결과의 '최고 온도'를 핫스팟으로 인용하지 마십시오.",
                "basis": f"heat.via={meta.get('heat', {}).get('via') or 'floor-zone'}",
            })

    # ── 6. 질량수지 ───────────────────────────────────────────────────
    if citable and m.get("mass_err_pct") is not None and abs(m["mass_err_pct"]) > 5.0:
        recs.append({
            "priority": P_HIGH, "category": "급배기 균형",
            "finding": f"배기 순유량이 설계 급기 대비 {m['mass_err_pct']:+.1f}% 어긋납니다.",
            "action": "급배기 풍량 설정과 개구부 면적을 재확인하십시오"
                      " (실 압력 밸런스·문틈 유입이 의도된 것인지 포함).",
            "basis": "(배기 − 급기)/급기, 허용 ±5%",
        })

    for row in recs:
        group = _CATEGORY_GROUPS.get(row["category"], "model")
        row["group"] = group
        row["group_label"] = GROUP_LABELS[group]
    order = {P_BLOCK: 0, P_HIGH: 1, P_MED: 2, P_INFO: 3}
    recs.sort(key=lambda r: (
        order.get(r["priority"], 9), 0 if r["group"] == "evidence" else 1
    ))
    return recs


def ai_digest(meta, metrics, parsed=None, trust=None, forecast=None, recs=None,
              health=None, *, case_health=None):
    """Claude 등 LLM 에 붙여넣을 압축 다이제스트(마크다운).

    HTML 리포트는 base64 이미지 때문에 수백 KB 라 붙여넣기에 부적합하다.
    여기서는 판단에 필요한 사실만 남기고, LLM 이 무엇을 하면 되고 무엇을 하면
    안 되는지(수치 재계산 금지, 미수렴 결과로 설계 결론 금지)를 명시한다.
    """
    m = metrics or {}
    cfg = meta.get("config", {})
    room = cfg.get("room", {})
    patches = meta.get("patches") or []
    authoritative = case_health if case_health is not None else health
    authoritative_valid = (
        isinstance(authoritative, dict)
        and authoritative.get("contract") == "case_health.v1"
        and authoritative.get("citation_status") in {
            "SCREENING_ONLY", "NOT_EVALUATED", "CITATION_BLOCKED",
            "DESIGN_CITABLE",
        }
    )
    if authoritative is None:
        citable = True if trust is None else trust.get("citable", True)
        citation_status = None
    else:
        citable = (
            authoritative_valid
            and authoritative.get("citation_status") == "DESIGN_CITABLE"
        )
        citation_status = (
            authoritative.get("citation_status")
            if authoritative_valid else "NOT_EVALUATED"
        )
    L = []

    L.append("# CFD 해석 다이제스트 (AI 검토 입력용)")
    L.append("")
    L.append("## 이 문서를 받은 AI 에게")
    L.append("- 아래 **사실(Facts)** 만 근거로 삼으십시오. 없는 값을 추정해 채우지 마십시오.")
    L.append("- 풍량·온도 같은 설계 수치는 이미 아래 '결정론적 추천'에서 계산돼 있습니다. "
             "다시 계산하지 말고, 그 결과를 **해석·우선순위화·대안 제시**에 쓰십시오.")
    if not citable:
        L.append("- ⚠ **이 해석은 수렴/에너지수지 기준 미통과입니다. "
                 "온도·유속 수치로 설계 결론을 내리지 마십시오.** "
                 "재실행 계획과 입력조건 검토까지만 답하십시오.")
    L.append("- 요청: (1) 우선순위별 개선안, (2) 각 안의 근거와 부작용, "
             "(3) 재해석이 필요하면 어떤 조건으로 돌릴지.")
    L.append("")

    L.append("## Facts — 대상")
    _vol = m.get("room_volume")
    L.append(f"- 실 치수: {room.get('L')} × {room.get('W')} × {room.get('H')} m "
             f"(체적 {_vol:,.1f} m³)" if isinstance(_vol, (int, float))
             else f"- 실 치수: {room.get('L')} × {room.get('W')} × {room.get('H')} m")
    L.append(f"- 발열: {m.get('heat_kw', '?')} kW "
             f"({'장비별 위치 반영' if meta.get('heat', {}).get('via') == 'obstacles' else '바닥층 균질 체적발열 — 핫스팟 미반영'})")
    n_eq = (meta.get("from_geometry") or {}).get("equipment")
    if n_eq:
        L.append(f"- 도면 감지 장비: {n_eq}대 (개별 발열량·위치는 미입력)")
    L.append(f"- 급기: {m.get('supply_cmh', '?')} CMH, {m.get('ach', 0):.2f} ACH, "
             f"급기온 {m.get('T_supply_C', '?')} °C")
    sup = [p for p in patches if p.get("role") == "supply"]
    exh = [p for p in patches if p.get("role") == "exhaust"]
    if sup:
        walls = sorted({p.get("wall") for p in sup})
        wallse = sorted({p.get("wall") for p in exh})
        L.append(f"- 급기구 {len(sup)}개소({', '.join(walls)}), "
                 f"배기구 {len(exh)}개소({', '.join(wallse)})")
        areas = [p.get("area") for p in sup if p.get("area")]
        if areas:
            L.append(f"- 급기구 유효면적: 개당 {min(areas):.3f}~{max(areas):.3f} m²")
    L.append("- 벽 열경계: 단열(zeroGradient) — 구조체 열손실 0 가정")
    L.append("")

    L.append("## Facts — 해석 결과")
    if citation_status:
        L.append(f"- Case Health 인용 상태: **{citation_status}**")
    L.append(f"- 신뢰 판정: **{(trust or {}).get('badge', '?')}** "
             f"(설계 인용 가능 = {'예' if citable else '**아니오**'})")
    if m.get("closure_pct") is not None:
        L.append(f"- 에너지 폐합율: {m['closure_pct']:.0f}% (정상상태·단열벽이면 100%)")
    if m.get("T_eq_C") is not None:
        L.append(f"- 이론 평형 실온(에너지수지 해): {m['T_eq_C']:.1f} °C")
    for key, label, unit in (("T_avg_C", "CFD 평균 온도", "°C"),
                             ("T_max_C", "CFD 최고 온도", "°C"),
                             ("dT_rise", "급기 대비 상승", "K"),
                             ("U_max", "최대 유속", "m/s")):
        if m.get(key) is not None:
            mark = "" if citable else "  ← 미수렴, 인용 금지"
            L.append(f"- {label}: {m[key]:.2f} {unit}{mark}")
    if parsed:
        L.append(f"- 반복 수행: {parsed.get('n_iters', '?')}회")
    if forecast:
        need = {f: i["iters_to_target"] for f, i in forecast.items()
                if i.get("iters_to_target")}
        if need:
            worst = max(need, key=lambda f: need[f])
            L.append(f"- 잔차 목표 도달 추정: 약 {need[worst]:,}회 추가 필요(병목 {worst})")
    L.append("")

    L.append("## 결정론적 추천 (계산 완료 — 재계산 불필요)")
    for i, r in enumerate(recs or [], 1):
        L.append(f"{i}. [{r['priority']}] **{r['category']}** — {r['finding']}")
        L.append(f"   - 조치: {r['action']}")
        L.append(f"   - 근거: {r['basis']}")
    if not recs:
        L.append("(없음)")
    L.append("")
    L.append("## 모델 한계 (판단 시 반드시 고려)")
    L.append("- 정상상태 RANS(부력 Boussinesq). 과도현상·일간 변동 없음.")
    L.append("- 벽 단열 가정 — 실제 구조체 열손실이 있으면 실온은 이보다 낮게 나온다.")
    L.append("- 복사 열전달 미포함.")
    L.append("- 균일 직교격자 + 개구부 격자 스냅 — 실디퓨저 형상(날개·확산각) 미반영.")
    return "\n".join(L)


def digest_payload(meta, metrics, parsed=None, trust=None, forecast=None,
                   recs=None, health=None, *, case_health=None):
    """기계 판독용 JSON payload — API 연동·자동화용."""
    authoritative = case_health if case_health is not None else health
    authoritative_valid = (
        isinstance(authoritative, dict)
        and authoritative.get("contract") == "case_health.v1"
        and authoritative.get("citation_status") in {
            "SCREENING_ONLY", "NOT_EVALUATED", "CITATION_BLOCKED",
            "DESIGN_CITABLE",
        }
    )
    citable = (
        authoritative_valid
        and authoritative.get("citation_status") == "DESIGN_CITABLE"
        if authoritative is not None else
        (True if trust is None else trust.get("citable", True))
    )
    payload = {
        "citable": citable,
        "badge": (trust or {}).get("badge"),
        "metrics": {k: v for k, v in (metrics or {}).items()
                    if isinstance(v, (int, float, str)) or v is None},
        "n_iters": (parsed or {}).get("n_iters"),
        "recommendations": recs or [],
    }
    if authoritative is not None:
        payload["citation_status"] = (
            authoritative.get("citation_status")
            if authoritative_valid else "NOT_EVALUATED"
        )
    return json.dumps(payload, ensure_ascii=False, indent=2)
