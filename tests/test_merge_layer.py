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


# ── 병합 가드: 합치면 한쪽이 조용히 사라지는 속성 ──────────────────────────
# 병합기는 합친 뒤 한쪽 레코드의 값을 그대로 쓴다 — 아래 속성이 서로 다른데도
# 합치면 그중 하나가 없어진다. 형상은 멀쩡하고 검사도 통과한다.
# (pascalorg/editor 의 wallsCanMerge 가 같은 이유로 height·material 을 본다.)
def _ov(x0, x1, z=0.0, **ov):
    r = _wall(x0, x1)
    r["z_base"] = z
    if ov:
        r["overrides"] = ov
    return r


def test_walls_on_different_storeys_do_not_merge():
    """★ 위층 벽이 아래층 벽에 흡수되어 통째로 사라진다.
    한 DXF 안에 여러 표고가 있으면([4b] 가 지원하는 경우) 바로 도달한다."""
    out = dp.merge_collinear_walls([_ov(0, 3000, z=0.0), _ov(0, 3000, z=4200.0)], {})
    assert len(out) == 2, out
    assert sorted(w["z_base"] for w in out) == [0.0, 4200.0]
    # 끝-끝으로 이어지는 경우도 마찬가지
    out = dp.merge_collinear_walls([_ov(0, 1000, z=0.0), _ov(1010, 3000, z=4200.0)], {})
    assert len(out) == 2, out


def test_walls_of_different_height_do_not_merge():
    """4000mm 파라펫이 2800mm 벽에 붙으면 파라펫이 2800 이 된다.
    layer_map 은 패턴별로 height 를 주므로 정상 사용에서 바로 나온다."""
    out = dp.merge_collinear_walls(
        [_ov(0, 1000, height=2800.0), _ov(1010, 3000, height=4000.0)], {})
    assert sorted(w["overrides"]["height"] for w in out) == [2800.0, 4000.0], out


def test_walls_of_different_material_do_not_merge():
    """조적벽이 콘크리트벽에 붙으면 물량·내화가 조용히 틀린다."""
    out = dp.merge_collinear_walls(
        [_ov(0, 1000, material="콘크리트"), _ov(1010, 3000, material="조적")], {})
    assert sorted(w["overrides"]["material"] for w in out) == ["조적", "콘크리트"], out


def test_walls_of_different_declared_width_do_not_merge():
    """overrides 는 '빌더가 쓸 치수' 다 — 다르면 다른 벽이다.
    height·material 만 열거하면 width·thickness 가 이 구멍으로 새므로 통째로 본다."""
    out = dp.merge_collinear_walls(
        [_ov(0, 1000, width=200.0), _ov(1010, 3000, width=300.0)], {})
    assert sorted(w["overrides"]["width"] for w in out) == [200.0, 300.0], out


def test_same_storey_within_tolerance_still_merges():
    """가드가 정상 병합까지 막으면 [4.0] 의 목적을 잃는다.
    z 는 층 감지와 **같은 허용치**(FLOOR_TOL_MM)로 양자화한다."""
    out = dp.merge_collinear_walls([_ov(0, 1000, z=0.0), _ov(1010, 3000, z=50.0)], {})
    assert len(out) == 1 and out[0]["seg_length"] == 3000.0, out


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


def test_identical_closed_polygons_on_two_layers_are_deduped():
    """같은 벽이 두 레이어에 그려진다(실측: A-CON ∥ 상부골조 36쌍, 좌표까지 동일).

    열린 벽은 면선 페어링이 이 중복을 흡수하지만 닫힌 폴리곤은 그 경로를 안 탄다.
    그대로 두면 IFC 에 같은 벽이 두 개 쌓이고 물량이 두 배가 된다."""
    pts = [[0, 0], [600, 0], [600, 600], [0, 600]]
    def box(layer):
        return {"kind": "polyline", "closed": True, "layer": layer,
                "points": [list(p) for p in pts], "z_base": 0.0}
    out = dp.detect_wall_pairs([box("A-CON"), box("상부골조")], {})
    assert len(out) == 1, f"중복이 남았다: {[w.get('layer') for w in out]}"
    assert dp.CLOSED_WALL_DUPS == {"상부골조": 1}, dp.CLOSED_WALL_DUPS


def test_duplicate_geometry_is_dropped_for_any_category():
    """기둥·슬래브는 벽과 달리 페어링·병합을 안 거쳐 중복을 흡수할 기회가 없다.
    실측: 같은 기둥 블록이 같은 자리에 두 번 들어가 IfcColumn 93 중 13개가 유령."""
    def col(x, layer="A-CON"):
        return {"kind": "polyline", "closed": True, "layer": layer, "z_base": 0.0,
                "points": [[x, 0], [x + 400, 0], [x + 400, 400], [x, 400]]}
    kept, dropped = dp.drop_duplicate_geometry([col(0), col(0), col(9000)])
    assert len(kept) == 2, kept
    assert dropped == {"A-CON": 1}, dropped


def test_duplicate_geometry_keeps_different_storeys_apart():
    """다층 조립에서 같은 x·y 의 위층 기둥은 **다른 기둥**이다 — z 를 무시하면 층이 사라진다."""
    def col(z):
        return {"kind": "polyline", "closed": True, "layer": "A-CON", "z_base": z,
                "points": [[0, 0], [400, 0], [400, 400], [0, 400]]}
    kept, dropped = dp.drop_duplicate_geometry([col(0.0), col(4200.0)])
    assert len(kept) == 2 and dropped == {}, (kept, dropped)


def test_duplicate_geometry_respects_detected_thickness():
    """★ 축선이 같아도 두께가 다르면 같은 부재가 아니다.
    실측: 같은 벽을 A-CON 은 450mm, 상부골조는 400mm 로 그렸다 — 어느 쪽이 맞는지는
    도면을 봐야 알고, 여기서 조용히 하나를 고를 문제가 아니다."""
    def w(width, layer):
        return {"kind": "polyline", "closed": False, "layer": layer, "z_base": 0.0,
                "points": [[0, 0], [3000, 0]], "width_detected": width}
    kept, _ = dp.drop_duplicate_geometry([w(450.0, "A-CON"), w(400.0, "상부골조")])
    assert len(kept) == 2, "두께가 다른데 같은 것으로 뭉갰다"
    kept2, _ = dp.drop_duplicate_geometry([w(450.0, "A-CON"), w(450.0, "상부골조")])
    assert len(kept2) == 1


def test_different_closed_polygons_are_both_kept():
    """기하가 다르면 남긴다 — '같은 레이어 쌍' 이 아니라 **좌표**로 판단한다."""
    def box(x):
        return {"kind": "polyline", "closed": True, "layer": "A-CON", "z_base": 0.0,
                "points": [[x, 0], [x + 600, 0], [x + 600, 600], [x, 600]]}
    out = dp.detect_wall_pairs([box(0), box(5000)], {})
    assert len(out) == 2, out


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


def test_review_flags_always_carry_a_reason():
    """★ '검토하라' 면서 이유를 안 알려주면 검토를 못 한다.

    실측(지하3층): needs_review 180개 중 **154개가 사유 없음**이었다(전부
    single_offset). Bonsai 에서 NeedsReview=True 로 걸러도 ReviewReason 이 빈
    문자열이라 무엇을 볼지 알 수 없었다. preview 의 REASON 표가 이 코드를 사람
    문장으로 바꿔 주므로, 코드가 비면 그 자리가 통째로 빈다."""
    segs = [_ov(0, 3000), _ov(0, 3000, z=0.0)]
    segs[1]["points"] = segs[1]["centerline"] = [[0, 250], [3000, 250]]
    out = dp.detect_wall_pairs(segs + [_ov(9000, 12000)], {})
    flagged = [w for w in out if w.get("needs_review")]
    assert flagged, "이 합성 케이스는 검토 대상이 나와야 한다"
    missing = [w.get("pairing") for w in flagged if not w.get("review_reason")]
    assert not missing, f"사유 없이 검토 플래그만 켠 pairing: {missing}"
