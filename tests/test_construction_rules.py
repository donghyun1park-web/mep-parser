# -*- coding: utf-8 -*-
"""시공기준 규칙(construction_rules.py) — 표에 없는 숫자는 코드에 없다.

`verdict=confirmed` 만 적용한다. 선언(DN·재질·용도)이 없으면 건너뛰고 그 사실을 센다 —
외경으로 호칭지름을 역산하지 않고, 계통 이름을 용도로 추정하지 않는다. 형상은 바꾸지 않는다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import construction_rules as CR


def _pipe(eid, pts, **kw):
    rec = {"eid": eid, "kind": "polyline", "points": [list(p) for p in pts], "elevation": 2600.0,
           "diameter": 20.0}
    rec.update(kw)
    return rec


def _duct(eid, pts, **kw):
    rec = {"eid": eid, "kind": "polyline", "points": [list(p) for p in pts], "elevation": 2600.0,
           "width_mm": 200.0, "height_mm": 100.0}
    rec.update(kw)
    return rec


def test_every_rule_row_carries_standard_clause_url_verdict_and_retrieved_date():
    for rule in CR.RULES:
        assert rule["verdict"] == "confirmed", rule["id"]     # 적용 표에는 confirmed 만 있다
        for key in ("standard", "clause", "url", "jurisdiction", "source_kind", "verdict", "retrieved"):
            assert rule.get(key), f"{rule['id']} missing {key}"
    ids = {r["id"] for r in CR.RULES}
    assert ids.isdisjoint({r["id"] for r in CR.BACKLOG})      # 적용 표와 백로그는 겹치지 않는다
    for rule in CR.BACKLOG:
        assert rule["verdict"] != "confirmed" or rule["id"] == "duct-fitting-dimensions"


def test_only_confirmed_rules_apply_and_the_receipt_lists_every_skipped_reason():
    """선언이 하나도 없는 배관 — 규칙이 적용되지 못한 이유가 영수증에 남는다."""
    geo = {"params": {}, "elements": {"pipe": [_pipe("p1", [(0, 0), (5000, 0)])]}}
    out = CR.review(geo)
    assert out["receipt"]["applied"] == []                    # 값을 얻은 규칙이 없다
    assert any(k.startswith("pipe-support-spacing-horizontal:") for k in out["receipt"]["skipped"])
    assert out["receipt"]["declarations_missing"] == {"dn": 1, "material": 1, "service": 1}


def test_receipt_counts_defaulted_declarations_separately_from_missing():
    """기본값이 채운 선언은 declarations_missing 에서 빠지고 declarations_defaulted 로 옮겨간다 —
    선언 공백이라는 사실 자체는 계속 보인다(0 이 아니라 다른 칸에)."""
    geo = {"params": {}, "elements": {"pipe": [
        _pipe("p1", [(0, 0), (5000, 0)]),                                         # 선언 전혀 없음
        _pipe("p2", [(0, 1000), (5000, 1000)], nominal_size="DN50", service="drain",
              material="강관", declaration_basis={"material": "project_default",
              "nominal_size": "project_default", "service": "project_default"}),   # 기본값이 다 채움
    ]}}
    out = CR.review(geo)
    assert out["receipt"]["declarations_missing"] == {"dn": 1, "material": 1, "service": 1}
    assert out["receipt"]["declarations_defaulted"] == {"dn": 1, "material": 1, "service": 1}


def test_material_of_reads_overrides_before_the_top_level_field():
    """overrides.material(편집·layer_map 선언)이 최상위 material(프로필 선언 또는 프로젝트
    기본값)보다 이긴다 — 기본값이 나중에 온 편집을 가리면 안 된다."""
    assert CR._material_of({"material": "기본재질", "overrides": {"material": "PB"}}) == "PB"
    assert CR._material_of({"material": "기본재질"}) == "기본재질"
    assert CR._material_of({}) is None


def test_a_flat_duct_over_one_to_four_is_a_violation_and_a_square_duct_is_silent():
    geo = {"params": {}, "elements": {"duct": [
        _duct("flat", [(0, 0), (2000, 0)], width_mm=440.0, height_mm=100.0),      # 4.4 > 4
        _duct("square", [(0, 1000), (2000, 1000)], width_mm=300.0, height_mm=300.0),  # 1.0
    ]}}
    out = CR.review(geo)
    violations = [i for i in out["items"] if i["kind"] == "violation"]
    assert [(v["rule"], v["eid"]) for v in violations] == [("duct-aspect-ratio", "flat")]
    assert violations[0]["values"]["ratio"] == 4.4


def test_hanger_counts_use_declared_material_and_nominal_size_and_never_the_outside_diameter():
    """DN80 강관의 실제 외경은 89.1mm(KS D 3507) — 외경을 호칭으로 잘못 읽으면 '80 초과'
    밴드(4.0m)가 걸린다. 선언한 DN80 그대로 3.0m 이어야 한다."""
    rec = _pipe("p1", [(0, 0), (30000, 0)], nominal_size="DN80", material="강관", diameter=89.1)
    rows = CR.supports({"params": {}, "elements": {"pipe": [rec]}})
    assert len(rows) == 1
    row = rows[0]
    assert row[3] == "DN80" and row[5] == 3.0
    assert row[6] == 10                                       # ceil(30.0 / 3.0)


def test_a_drain_run_reports_the_fall_it_needs_and_says_the_slope_is_undeclared():
    rec = _pipe("d1", [(0, 0), (10000, 0)], nominal_size="DN100", service="drain")
    out = CR.review({"params": {}, "elements": {"pipe": [rec]}})
    (info,) = [i for i in out["items"] if i["rule"] == "drain-slope-by-diameter"]
    assert info["kind"] == "info"
    assert info["values"]["slope"] == "1/100"
    assert info["values"]["required_fall_mm"] == 100.0        # 10000mm × 1/100
    assert info["values"]["slope_basis"] == "undeclared"


def test_a_hydrant_branch_under_dn40_is_a_violation_and_dn40_is_not():
    geo = {"params": {}, "elements": {"pipe": [
        _pipe("under", [(0, 0), (1000, 0)], nominal_size="32A", service="hydrant_branch"),
        _pipe("ok", [(0, 0), (1000, 0)], nominal_size="40A", service="hydrant_branch"),
    ]}}
    out = CR.review(geo)
    violations = {i["eid"] for i in out["items"] if i["kind"] == "violation"}
    assert violations == {"under"}


def test_cleanouts_count_the_head_the_length_bands_and_turns_over_45_degrees():
    """DN100 이하 15m 간격. 40m 직선 = 기점 1 + ⌊40/15⌋ = 3. 20m+20m 직각 꺾임 하나 더하면 4."""
    straight = _pipe("s1", [(0, 0), (40000, 0)], nominal_size="DN100", service="drain")
    bent = _pipe("b1", [(0, 0), (20000, 0), (20000, 20000)], nominal_size="DN100", service="drain")
    rows = CR.supports({"params": {}, "elements": {"pipe": [straight, bent]}})
    assert [r[6] for r in rows] == [3, 4]


def test_risers_take_one_support_per_storey_only_when_storeys_are_declared():
    """수직관은 표 3.4-1 대로 '각 층 1개소' — 길이가 아니라 층 수로 센다."""
    riser = _pipe("r1", [(0, 0), (1000, 0)], nominal_size="DN50",
                  path3d={"segments": [{"type": "line", "start": [0, 0, 0], "end": [1000, 0, 0]},
                                       {"type": "line", "start": [1000, 0, 0], "end": [1000, 0, 8400]}]})
    geo_floors = {"params": {}, "floors": [{"z": 0}, {"z": 2800}, {"z": 5600}, {"z": 8400}],
                 "elements": {"pipe": [riser]}}
    rows = CR.supports(geo_floors)
    assert len(rows) == 1 and rows[0][6] == 3 and "pipe-support-vertical" in rows[0][-1]

    geo_no_info = {"params": {}, "elements": {"pipe": [riser]}}
    out = CR.review(geo_no_info)
    assert CR.supports(geo_no_info) == []
    assert "pipe-support-vertical:no_storey_height" in out["receipt"]["skipped"]

    geo_profile = {"params": {}, "mep_profile": {"levels": {"floor_to_floor_mm": 2800}},
                  "elements": {"pipe": [riser]}}
    assert CR.supports(geo_profile)[0][6] == 3                 # ceil(8400/2800) = 3


def test_insulation_thickness_comes_from_declared_grade_temperature_and_dn_only():
    """같은 등급·온도·DN 이면 재질·계통 이름이 달라도 같은 두께 — 표를 읽는 키는 그 셋뿐이다."""
    rec_a = {"nominal_size": "DN50", "service": "domestic_hot", "material": "steel", "system": "난방"}
    rec_b = {"nominal_size": "DN50", "service": "domestic_hot", "material": "PB", "system": "온수"}
    decl = {"grade": "가", "fluid_temp_c": 80}
    assert CR.insulation_mm("pipe", rec_a, decl) == CR.insulation_mm("pipe", rec_b, decl) == (35.0, "hot_90")
    assert CR.insulation_mm("pipe", rec_a, {"grade": "나", "fluid_temp_c": 80}) == (40.0, "hot_90")  # 등급이 값을 바꾼다
    # 확인 안 된 등급(다·라, ≤90°C)은 표에 없다 — 값을 지어내지 않고 건너뛴다
    assert CR.insulation_mm("pipe", rec_a, {"grade": "다", "fluid_temp_c": 80}) == (None, "grade_not_in_table")


def test_a_humid_location_takes_the_humid_table():
    """DN50 등급 나: 일반(표 2.5-1) 25mm, 다습(표 2.5-2) 40mm — 다른 표를 조회한다."""
    rec = {"nominal_size": "DN50", "service": "domestic_cold"}
    assert CR.insulation_mm("pipe", rec, {"grade": "나"}) == (25.0, "cold_general")
    assert CR.insulation_mm("pipe", rec, {"grade": "나", "humid": True}) == (40.0, "cold_humid")


def test_sprinkler_hangers_follow_the_declared_pipe_class():
    branch = _pipe("br1", [(0, 0), (10000, 0)], service="sprinkler_branch")
    main = _pipe("mn1", [(0, 0), (9000, 0)], service="sprinkler_main")
    rows = CR.supports({"params": {}, "elements": {"pipe": [branch, main]}})
    by_eid = {}
    for row, rec in zip(rows, (branch, main)):
        by_eid[rec["eid"]] = row
    assert by_eid["br1"][5] == 3.5 and by_eid["br1"][6] == 3        # ceil(10/3.5)
    assert by_eid["mn1"][5] == 4.5 and by_eid["mn1"][6] == 2        # ceil(9/4.5)
