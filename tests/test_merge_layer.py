# -*- coding: utf-8 -*-
"""collinear 재병합이 레코드를 새로 만들면서 잃던 필드.

실측: single_offset 158개 중 115개의 layer 가 빈 문자열이었다. "이 벽 어느
레이어냐" 를 물을 수 없으니 오분류 진단이 막혔고, seg_length 가 갱신되지 않아
병합 허용치를 총연장으로 판단할 수도 없었다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dxf_parser as dp


def _wall(x0, x1, layer="A-CON"):
    return {"kind": "polyline", "closed": False, "layer": layer,
            "points": [[x0, 0], [x1, 0]], "centerline": [[x0, 0], [x1, 0]],
            "width_detected": 200.0, "pairing": "paired", "confidence": 0.9,
            "seg_length": float(x1 - x0), "z_base": 0.0}


def test_merged_wall_keeps_its_layer():
    out = dp.merge_collinear_walls([_wall(0, 1000), _wall(1010, 3000)], {})
    assert len(out) == 1, out
    assert out[0]["layer"] == "A-CON", out[0]


def test_merged_wall_recomputes_seg_length():
    """갱신 안 하면 총연장이 병합 전 값으로 남아 허용치 비교가 무의미해진다."""
    out = dp.merge_collinear_walls([_wall(0, 1000), _wall(1010, 3000)], {})
    assert out[0]["seg_length"] == 3000.0, out[0]["seg_length"]


def test_merged_wall_keeps_its_material():
    """재질은 overrides 를 타고 빌더로 간다 — 병합이 overrides 를 버리면 사라진다.
    형상은 멀쩡하고 물량·내화 산정만 조용히 틀리는, 제일 오래 사는 종류의 오류다."""
    a, b = _wall(0, 1000), _wall(1010, 3000)
    for w in (a, b):
        w["overrides"] = {"width": 200.0, "material": "콘크리트"}
    out = dp.merge_collinear_walls([a, b], {})
    assert len(out) == 1, out
    assert out[0]["overrides"].get("material") == "콘크리트", out[0].get("overrides")


def test_unmerged_walls_are_untouched():
    """갭이 크면 병합하지 않는다 — 그때도 원본 필드는 그대로여야 한다."""
    out = dp.merge_collinear_walls([_wall(0, 1000), _wall(9000, 10000)], {})
    assert len(out) == 2
    assert all(w["layer"] == "A-CON" for w in out)
