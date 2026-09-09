# -*- coding: utf-8 -*-
"""entity_to_record 의 엔티티 타입 커버리지.

미지원 타입은 조용히 사라지지 않고 'unhandled' 경고로 나오지만, 경고는 셋 중
가장 약한 신호다 — 실측(아파트 단위세대 환기평면)에서 플렉시블 덕트가 ELLIPSE
216개로 그려져 있었고 194개가 경고만 남긴 채 모델에서 빠졌다.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ezdxf

import dxf_parser as dp


def _msp():
    return ezdxf.new("R2010").modelspace()


def test_ellipse_arc_becomes_a_polyline_on_the_curve():
    """제어점이 아니라 **곡선 위의 점**이어야 한다 — 덕트 축선이 되기 때문."""
    msp = _msp()
    e = msp.add_ellipse(center=(1000, 500), major_axis=(400, 0), ratio=0.5,
                        start_param=0.0, end_param=math.pi)
    rec = dp.entity_to_record(e, 1.0)
    assert rec and rec["kind"] == "polyline", rec
    pts = rec["points"]
    assert len(pts) >= 3, pts
    # 반타원: 시작 (1400,500) → 끝 (600,500), 꼭대기는 y=500+400*0.5=700
    assert math.dist(pts[0], [1400, 500]) < 1.0, pts[0]
    assert math.dist(pts[-1], [600, 500]) < 1.0, pts[-1]
    assert max(p[1] for p in pts) > 690, "곡선 위가 아니라 현(弦)을 따라간다"
    # 모든 점이 타원 위에 있다(활꼴 허용치 안).
    for x, y in pts:
        assert abs(((x - 1000) / 400.0) ** 2 + ((y - 500) / 200.0) ** 2 - 1.0) < 0.05, (x, y)
    assert not rec["closed"]


def test_full_ellipse_is_closed():
    msp = _msp()
    e = msp.add_ellipse(center=(0, 0), major_axis=(300, 0), ratio=1.0,
                        start_param=0.0, end_param=2 * math.pi)
    rec = dp.entity_to_record(e, 1.0)
    assert rec and rec["closed"], rec


def test_ellipse_scale_is_applied():
    msp = _msp()
    e = msp.add_ellipse(center=(1, 0), major_axis=(1, 0), ratio=1.0,
                        start_param=0.0, end_param=math.pi)
    rec = dp.entity_to_record(e, 1000.0)          # m 도면 → mm
    assert math.dist(rec["points"][0], [2000, 0]) < 1.0, rec["points"][0]
