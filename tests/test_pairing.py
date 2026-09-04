# -*- coding: utf-8 -*-
"""면선 페어링 우선순위 — 같은 레이어 쌍이 먼저 구간을 가져간다.

실무 도면은 같은 벽을 건축(A-WALL)과 구조(A-CON)가 각자 그리고 두 표현이
50mm 안팎으로 어긋난다. 거리만으로 정렬하면 그 레이어 간 오프셋이 제일 가까워서
구간을 선점하고 진짜 두께 쌍을 막는다(실측: 50mm 55개가 450mm 41개를 가로챔).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dxf_parser as dp


def _seg(y, layer, x0=0.0, x1=5000.0):
    return {"p1": [x0, y], "p2": [x1, y], "dir": (1.0, 0.0), "len": x1 - x0,
            "src": 0, "layer": layer, "sigs": [], "overrides": {}, "opts": {},
            "z_base": 0.0}


def test_same_layer_pair_wins_over_a_closer_cross_layer_pair():
    """A-CON 두 면(0, 200)이 진짜 벽. A-WALL 이 50mm 옆에 같은 벽을 또 그렸다."""
    segs = [_seg(0.0, "A-CON"), _seg(200.0, "A-CON"), _seg(50.0, "A-WALL")]
    pairs, _ = dp._find_wall_pairs(segs)
    widths = sorted(round(p[2]) for p in pairs)
    assert 200 in widths, widths
    # 200mm 짝이 먼저 구간을 점유했는지 — 첫 쌍이 그것이어야 한다
    assert round(pairs[0][2]) == 200, [round(p[2]) for p in pairs]


def test_cross_layer_pair_is_demoted_not_forbidden():
    """한쪽 면만 다른 레이어에 그린 도면도 있다. 같은 레이어 짝이 없으면 채택한다."""
    segs = [_seg(0.0, "A-CON"), _seg(200.0, "상부골조")]
    pairs, matched = dp._find_wall_pairs(segs)
    assert pairs and round(pairs[0][2]) == 200, [round(p[2]) for p in pairs]
    assert matched, "짝이 있는데 single 로 떨어졌다"


def test_ordering_is_deterministic():
    segs = [_seg(0.0, "A-CON"), _seg(200.0, "A-CON"),
            _seg(50.0, "A-WALL"), _seg(250.0, "A-WALL")]
    first = [(p[0], p[1], round(p[2], 3)) for p in dp._find_wall_pairs(segs)[0]]
    for _ in range(3):
        assert [(p[0], p[1], round(p[2], 3))
                for p in dp._find_wall_pairs(segs)[0]] == first
