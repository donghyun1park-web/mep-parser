# -*- coding: utf-8 -*-
"""개구부 조각 걸러내기.

문 하나는 문짝선 + 스윙호 + 철물로 여러 엔티티가 되는데, 레이어 규칙이
`DOOR|WIND|문|창 → opening` 이면 그 조각이 **각각** 개구부 레코드가 된다.
실측(지하3층 A-DOOR): 288개 중 244개가 0.1~12.5mm 짜리 조각이었고, 그것들이
벽 링크율을 24% 로 끌어내리고 25mm 짜리 구멍을 벽에 뚫고 있었다.
(진짜 문 18개는 블록 INSERT 안에 있어 explode 경로로 정상 처리된다.)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dxf_parser as dp


def _circle(r, x=0.0, y=0.0, layer="A-DOOR"):
    return {"kind": "circle", "center": [x, y], "radius": r, "layer": layer}


def _poly(w, h, layer="A-DOOR"):
    return {"kind": "polyline", "closed": False, "layer": layer,
            "points": [[0, 0], [w, 0], [w, h], [0, h]]}


def test_tiny_fragments_are_dropped_and_counted():
    """조용히 버리지 않는다 — 레이어별로 세어서 돌려준다."""
    els = {"opening": [_circle(0.15), _circle(6.0), _circle(450.0), _poly(900, 40)]}
    dropped = dp.drop_tiny_openings(els)
    assert len(els["opening"]) == 2, [dp._opening_extent(o) for o in els["opening"]]
    assert dropped == {"A-DOOR": 2}, dropped


def test_pipe_sleeves_survive():
    """MEP 도면의 배관 슬리브(50~200mm)는 진짜 개구부다 — 자르면 안 된다."""
    els = {"opening": [_circle(40.0), _circle(75.0)]}      # 지름 80mm, 150mm
    assert dp.drop_tiny_openings(els) == {}, "슬리브를 버렸다"
    assert len(els["opening"]) == 2


def test_extent_uses_diameter_for_circles_and_bbox_for_polylines():
    assert dp._opening_extent(_circle(450.0)) == 900.0
    assert dp._opening_extent(_poly(900, 40)) == 900.0
    assert dp._opening_extent({"kind": "polyline", "points": [[0, 0]]}) == 0.0


def test_threshold_is_overridable():
    els = {"opening": [_circle(40.0)]}
    dp.drop_tiny_openings(els, min_size=200.0)
    assert els["opening"] == []


def test_nothing_dropped_returns_empty_report():
    els = {"opening": [_circle(450.0)]}
    assert dp.drop_tiny_openings(els) == {}
    assert len(els["opening"]) == 1


# ── 실측 치수와 가정 치수를 구분한다 ──────────────────────────────────────
def test_measured_width_is_not_marked_assumed():
    """반지름이 실제 크기면 width 는 도면에서 잰 값이다."""
    els = {"opening": [_circle(450.0)]}
    dp.link_openings_to_walls(els, {})
    op = els["opening"][0]
    assert op["width"] == 900.0
    assert "width" not in (op.get("dims_assumed") or [])


def test_height_and_sill_are_always_assumed_from_a_plan():
    """★ 평면도는 창의 높이·문턱을 **보여주지 않는다**(입면도/창호일람표에 있다).

    종전에는 기본값 1200/900 을 넣고 실측과 구분 없이 내보냈다 — 실측 도면
    288개가 전부 같은 1200/900 이었고 아무도 그게 가정인 줄 몰랐다."""
    els = {"opening": [_circle(450.0)]}
    dp.link_openings_to_walls(els, {})
    assumed = els["opening"][0].get("dims_assumed") or []
    assert "height" in assumed and "sill" in assumed, assumed


def test_supplied_dimensions_are_left_alone():
    """창호일람표나 사용자가 준 값은 가정으로 표시하지 않는다."""
    op = _circle(450.0)
    op.update({"height": 2400.0, "sill": 0.0, "subtype": "door"})
    els = {"opening": [op]}
    dp.link_openings_to_walls(els, {})
    assert els["opening"][0]["height"] == 2400.0
    assert not (els["opening"][0].get("dims_assumed") or [])


def test_zero_radius_marks_width_assumed_too():
    els = {"opening": [{"kind": "circle", "center": [0, 0], "radius": 0.4}]}
    dp.link_openings_to_walls(els, {})
    assert "width" in (els["opening"][0].get("dims_assumed") or [])
