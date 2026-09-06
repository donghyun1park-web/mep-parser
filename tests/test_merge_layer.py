# -*- coding: utf-8 -*-
"""collinear 재병합이 레코드를 새로 만들면서 잃던 필드.

실측: single_offset 158개 중 115개의 layer 가 빈 문자열이었다. "이 벽 어느
레이어냐" 를 물을 수 없으니 오분류 진단이 막혔고, seg_length 가 갱신되지 않아
병합 허용치를 총연장으로 판단할 수도 없었다.
"""
import math
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


def _closed_box(x0, y0, s=600.0):
    pts = [[x0, y0], [x0 + s, y0], [x0 + s, y0 + s], [x0, y0 + s]]
    return {"kind": "polyline", "closed": True, "layer": "A-CON",
            "points": pts, "centerline": pts, "width_detected": None,
            "pairing": "closed", "confidence": 0.7, "z_base": 0.0}


def test_closed_polygons_are_not_flattened_by_the_merger():
    """★ 벽 72개가 IFC 에서 증발한 원인.

    이 병합기는 레코드를 `centerline[0] → centerline[-1]` **한 세그먼트로만** 본다.
    폴리곤에 그 짓을 하면 닫힘변 하나짜리 2점 레코드가 되고, 빌더의 닫힘 분기
    (≥3점 필요)와 열림 분기(pairing=="closed" 제외) 사이로 조용히 사라진다.
    형상오류 0, 검사 전부 통과, 산출물에만 없다."""
    boxes = [_closed_box(0, 0), _closed_box(0, 700), _closed_box(700, 0)]
    out = dp.merge_collinear_walls(boxes, {})
    assert len(out) == 3, f"닫힌 폴리선이 병합됐다: {len(out)}"
    for w in out:
        assert len(w["points"]) == 4, f"폴리곤이 {len(w['points'])}점으로 납작해졌다"
        assert w.get("closed") is True and w.get("pairing") == "closed"


def test_open_walls_still_merge_next_to_closed_ones():
    """닫힘 제외가 열린 벽의 병합까지 막아 버리면 원래 목적을 잃는다."""
    out = dp.merge_collinear_walls(
        [_wall(0, 1000), _closed_box(5000, 5000), _wall(1010, 3000)], {})
    assert len(out) == 2, out
    assert sum(1 for w in out if w.get("pairing") == "closed") == 1
    assert any(w.get("seg_length") == 3000.0 for w in out), "열린 벽이 안 붙었다"


# ── 코너 스냅 ──────────────────────────────────────────────────────────────
def _seg(a, b):
    return {"kind": "polyline", "closed": False, "layer": "A-CON",
            "points": [a, b], "centerline": [a, b],
            "pairing": "paired", "width_detected": 200.0, "z_base": 0.0}


def test_snap_does_not_annihilate_a_wall_shorter_than_the_tolerance():
    """★ 벽 길이 < snap_tol 이면 **양 끝점이 서로 tol 안**이라 한 클러스터로 묶여
    둘 다 같은 centroid 가 된다 → 길이 0 → 빌더의 make_wire 에서 조용히 탈락.
    실측(지하3층 A-CON): 50mm 벽 1개가 정확히 이렇게 IFC 에서 사라졌다."""
    out = dp.snap_wall_corners([_seg([0, 0], [40, 0]),
                                _seg([1000, 0], [2000, 0])], snap_tol=50.0)
    a, b = out[0]["centerline"][0], out[0]["centerline"][-1]
    assert math.dist(a, b) == 40.0, f"짧은 벽이 길이 {math.dist(a, b)} 로 뭉개졌다"
    assert out[0]["points"] == [[0, 0], [40, 0]], out[0]["points"]


def test_normal_corner_snap_still_works():
    """가드가 정상 T자 스냅까지 막으면 [4.1] 의 목적을 잃는다."""
    out = dp.snap_wall_corners([_seg([0, 0], [1000, 0]),
                                _seg([1005, 3], [1005, 1000])], snap_tol=50.0)
    assert out[0]["centerline"][-1] == out[1]["centerline"][0], out
    assert out[0]["centerline"][-1] == [1002.5, 1.5], out[0]["centerline"]


def test_unmerged_walls_are_untouched():
    """갭이 크면 병합하지 않는다 — 그때도 원본 필드는 그대로여야 한다."""
    out = dp.merge_collinear_walls([_wall(0, 1000), _wall(9000, 10000)], {})
    assert len(out) == 2
    assert all(w["layer"] == "A-CON" for w in out)
