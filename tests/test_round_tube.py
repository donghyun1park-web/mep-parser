# -*- coding: utf-8 -*-
"""꺾이거나 휜 원형 관은 사각 덕트와 같은 마이터 링을 정다각형으로 꿰맨 관이다.

FreeCAD `makePipeShell` 스윕은 수직으로 꺾인 입상관을 IFC 비다양체(V107)로, 원호 덕트는 IFC 단계
멈춤으로 만들었다. 평면 면만의 관은 닫힌 메시라 IFC 가 그대로 받는다 — 링·부피를 좌표로 잰다.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import geom_contract as GC  # noqa: E402


def _polygon_area(r, n):
    return 0.5 * n * r * r * math.sin(2 * math.pi / n)


def test_bent_round_riser_is_a_closed_polygon_tube_with_exact_mitre_volume():
    route = [[0.0, 0.0, 100.0], [1000.0, 0.0, 100.0], [1000.0, 0.0, 1100.0]]          # 수평 → 수직
    (rings,) = GC.rect_parts(route, 20.0, 20.0, 0.0, sides=GC.ROUND_SIDES)
    assert [len(r) for r in rings] == [GC.ROUND_SIDES] * 3
    assert all(abs(math.dist(p, route[0]) - 10.0) < 1e-9 for p in rings[0])        # 끝 링은 원 위에
    verts, faces = GC.rect_sweep_mesh(rings)
    assert len(faces) == 2 * GC.ROUND_SIDES + 2
    edges = {}
    for f in faces:
        for a, b in zip(f, f[1:] + f[:1]):
            edges[(a, b)] = edges.get((a, b), 0) + 1
    assert all(edges.get((b, a)) == 1 for (a, b) in edges)                          # 닫힌 2-다양체, 방향 일관
    volume = GC._signed_volume(verts, faces)
    assert volume == pytest.approx(2000.0 * _polygon_area(10.0, GC.ROUND_SIDES), rel=1e-9)


def test_rect_rings_are_unchanged_by_the_profile_generalisation():
    route = [[0.0, 0.0, 2400.0], [2000.0, 0.0, 2400.0], [3000.0, 1000.0, 2700.0]]
    assert GC.rect_parts(route, 204.0, 60.0, 0.3) == GC.rect_parts(route, 204.0, 60.0, 0.3, sides=None)
    (rings,) = GC.rect_parts(route, 204.0, 60.0)
    w, h = GC.section_axes([1.0, 0.0, 0.0])
    assert rings[0][0] == pytest.approx([0.0 - 102.0 * w[0] - 30.0 * h[0], -102.0 * w[1] - 30.0 * h[1],
                                         2400.0 - 102.0 * w[2] - 30.0 * h[2]])      # 첫 모서리 (−w, −h) 그대로
