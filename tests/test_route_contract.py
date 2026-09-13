# -*- coding: utf-8 -*-
"""계약 v3 — MEP 경로(`path3d`)와 단면. 모든 빌더·다리가 쓰는 한 벌의 기하.

위험은 셋이다: v2 파일이 조용히 움직이는 것, 원호·스플라인이 샘플로 뭉개지는 것,
빌더마다 사각 단면의 방향이 달라지는 것. 각각을 좌표로 잰다.
"""
import math

import pytest

import geom_contract as GC


def _close(a, b, tol=1e-9):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def _arc(r=1000.0, sweep=math.pi / 2, z=0.0):
    return {"type": "arc", "start": [r, 0.0, z], "end": [r * math.cos(sweep), r * math.sin(sweep), z],
            "center": [0.0, 0.0, z], "normal": [0.0, 0.0, 1.0]}


def _quarter_circle_nurbs(r=1000.0):
    s = math.sqrt(0.5)
    return {"type": "spline", "degree": 2, "control_points": [[r, 0, 0], [r, r, 0], [0, r, 0]],
            "knots": [0, 0, 0, 1, 1, 1], "weights": [1, s, 1]}


def test_v2_route_is_read_without_moving_anything():
    rec = {"points": [[0, 0], [3000, 0], [3000, 2000]], "elevation": 2600.0}
    assert GC.path3d_segments(rec)[0] == {"type": "line", "start": [0.0, 0.0, 0.0], "end": [3000.0, 0.0, 0.0]}
    assert GC.route_points("duct", rec) == [[0, 0, 2600.0], [3000, 0, 2600.0], [3000, 2000, 2600.0]]
    assert GC.is_planar_polyline_route(rec)
    assert GC.route_length(rec) == 5000.0
    assert GC.check_contract({"contract": {"version": 2}}) == []
    assert GC.check_contract({"contract": {"version": 3}}) == []


def test_closed_v2_route_closes_like_the_builders_did():
    rec = {"points": [[0, 0], [1000, 0], [1000, 1000]], "closed": True, "elevation": 0}
    assert GC.route_length(rec) == pytest.approx(2000 + math.sqrt(2) * 1000)


def test_arc_samples_stay_within_the_chord_error_and_length_is_analytic():
    seg = _arc()
    pts = GC.sample_segments([seg], chord_error_mm=0.5)
    assert _close(pts[0], [1000, 0, 0]) and _close(pts[-1], [0, 1000, 0], 1e-9)
    for a, b in zip(pts, pts[1:]):
        mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]
        assert 1000 - math.hypot(*mid) <= 0.5 + 1e-9          # 현오차 한도
    assert GC.segment_length(seg) == pytest.approx(math.pi / 2 * 1000, abs=1e-9)


def test_full_circle_arc_has_start_equal_end():
    seg = {"type": "arc", "start": [500, 0, 0], "end": [500, 0, 0], "center": [0, 0, 0],
           "normal": [0, 0, 1]}
    assert GC.segment_length(seg) == pytest.approx(2 * math.pi * 500)


def test_arc_normal_decides_the_direction():
    ccw = _arc()
    cw = dict(ccw, normal=[0, 0, -1])                      # 같은 두 점, 반대쪽 큰 원호
    assert GC.segment_length(cw) == pytest.approx(1.5 * math.pi * 1000)


def test_rational_spline_is_evaluated_exactly_not_its_control_polygon():
    pts = GC.sample_segments([_quarter_circle_nurbs()], chord_error_mm=0.25)
    assert all(abs(math.hypot(p[0], p[1]) - 1000) < 1e-6 for p in pts)   # 점이 원 위에
    assert GC.segment_length(_quarter_circle_nurbs()) == pytest.approx(math.pi / 2 * 1000, rel=1e-6)
    assert GC.route_length_basis({"path3d": {"segments": [_quarter_circle_nurbs()]}}) == \
        "evaluated_curve_approximation"


@pytest.mark.parametrize("seg", [_arc(), _quarter_circle_nurbs(),
                                 {"type": "line", "start": [0, 0, 0], "end": [5, 6, 7]}])
def test_reversal_keeps_the_shape(seg):
    fwd = GC.sample_segments([seg], 0.5)
    back = GC.sample_segments(GC.reverse_segments([seg]), 0.5)
    assert _close(fwd[0], back[-1], 1e-6) and _close(fwd[-1], back[0], 1e-6)
    twice = GC.sample_segments(GC.reverse_segments(GC.reverse_segments([seg])), 0.5)
    assert len(twice) == len(fwd) and all(_close(p, q, 1e-9) for p, q in zip(twice, fwd))
    assert sum(math.dist(a, b) for a, b in zip(back, back[1:])) == pytest.approx(
        sum(math.dist(a, b) for a, b in zip(fwd, fwd[1:])), rel=1e-3)


def test_translation_moves_arcs_without_resampling_them():
    moved = GC.translate_segments([_arc()], [100, -50, 20])
    assert moved[0]["center"] == [100.0, -50.0, 20.0]
    assert GC.segment_length(moved[0]) == pytest.approx(GC.segment_length(_arc()))


def test_relative_z_is_added_to_elevation_and_widens_the_z_range():
    riser = {"path3d": {"segments": [
        {"type": "line", "start": [0, 0, 0], "end": [2000, 0, 0]},
        {"type": "line", "start": [2000, 0, 0], "end": [2000, 0, 1500]}]},
        "points": [[0, 0], [2000, 0]], "elevation": 1000.0, "diameter": 50.0}
    pts = GC.route_points("pipe", riser)
    assert pts[0] == [0, 0, 1000.0] and pts[-1] == [2000, 0, 2500.0]
    assert GC.z_range("pipe", riser) == (975.0, 2525.0)
    assert GC.route_length(riser) == 3500.0                   # 수직 구간도 길이다
    assert not GC.is_planar_polyline_route(riser)


def test_discontinuous_path3d_is_reported():
    rec = {"path3d": {"segments": [{"type": "line", "start": [0, 0, 0], "end": [1, 0, 0]},
                                   {"type": "line", "start": [2, 0, 0], "end": [3, 0, 0]}]}}
    assert GC.path3d_problems(rec) and "segment 1" in GC.path3d_problems(rec)[0]
    assert GC.path3d_problems({"points": [[0, 0], [1, 0]]}) == []


# ── 단면 ────────────────────────────────────────────────────────────────────
def test_round_duct_uses_a_diameter_and_never_a_default():
    rec = {"section_shape": "round", "diameter": 125.0, "elevation": 2500}
    assert GC.mep_dimensions("duct", rec) == {"diameter": 125.0}
    assert GC.z_range("duct", rec) == (2437.5, 2562.5)
    with pytest.raises(GC.ContractError):
        GC.mep_dimensions("duct", {"section_shape": "round"})
    assert GC.mep_dimensions("duct", {"width_mm": 110, "height_mm": 54}) == {"width_mm": 110.0, "height_mm": 54.0}
    with pytest.raises(GC.ContractError):
        GC.section_shape("pipe", {"section_shape": "rect"})
    sec = GC.mep_section("duct", {"width_mm": 204, "height_mm": 60, "overrides": {"section_roll": 0.5}})
    assert sec == {"width_mm": 204.0, "height_mm": 60.0, "shape": "rect", "roll": 0.5}


def _pascal_rect_section_axes(d, roll):
    """Pascal `rectSectionAxes`(Y-up) 를 그대로 옮긴 참조 구현."""
    def cross(a, b):
        return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
    n = math.sqrt(sum(c * c for c in d)); d = [c / n for c in d]
    x = cross([0, 1, 0], d)
    if sum(c * c for c in x) < 1e-8:
        x = [1, 0, 0]
    n = math.sqrt(sum(c * c for c in x)); x = [c / n for c in x]
    z = cross(x, d)
    c, s = math.cos(roll), math.sin(roll)
    return ([x[i] * c + z[i] * s for i in range(3)], [-x[i] * s + z[i] * c for i in range(3)])


@pytest.mark.parametrize("d", [(1, 0, 0), (0, 1, 0), (1, 2, 0), (1, 1, 1), (0, 0, 1), (0, 0, -1), (0.3, -0.2, 0.9)])
@pytest.mark.parametrize("roll", [0.0, 0.7, -2.1])
def test_section_axes_match_pascal_under_the_bridge_axis_swap(d, roll):
    """다리는 (x,y,z) → Pascal (x,z,y). 같은 roll 값이 두 화면에서 같은 단면이어야 한다."""
    swap = lambda v: [v[0], v[2], v[1]]
    pw, ph = _pascal_rect_section_axes(swap(d), roll)
    w, h = GC.section_axes(d, roll)
    assert _close(w, swap(pw), 1e-12) and _close(h, swap(ph), 1e-12)


def _edges_twice(faces):
    count = {}
    for f in faces:
        for a, b in zip(f, f[1:] + f[:1]):
            count[(a, b)] = count.get((a, b), 0) + 1
    return all(count.get((b, a)) == 1 for (a, b) in count) and all(v == 1 for v in count.values())


@pytest.mark.parametrize("pts", [
    [[0, 0, 0], [3000, 0, 0]],                                   # 직관
    [[0, 0, 0], [3000, 0, 0], [3000, 2000, 0]],                  # 수평 90°
    [[0, 0, 0], [3000, 0, 0], [3000, 0, 1500]],                  # 입상
    [[0, 0, 0], [2000, 0, 0], [3000, 1000, 500], [3000, 3000, 500]],   # 경사 섞임
])
def test_rect_rings_make_a_closed_tube_whose_volume_is_axis_length_times_area(pts):
    w, h = 204.0, 60.0
    rings = GC.rect_rings(pts, w, h)
    verts, faces = GC.rect_sweep_mesh(rings)
    assert _edges_twice(faces)                                   # 닫힌 다양체
    length = sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))
    assert GC._signed_volume(verts, faces) == pytest.approx(length * w * h, rel=1e-9)


def test_miter_ring_is_the_same_from_both_legs():
    """꺾인 점의 단면을 들어오는 쪽·나가는 쪽에서 각각 구해도 같아야 틈이 안 난다."""
    pts = [[0, 0, 0], [2000, 0, 0], [2000, 0, 1500]]
    rings = GC.rect_rings(pts, 200, 100, 0.3)
    b = GC._unit(GC._sub(pts[2], pts[1]))
    end = rings[-1]                                              # 나가는 쪽 단면(끝점)
    back = [GC._sub(c, GC._mul(b, 1500)) for c in end]           # 꼭짓점 평면으로 되돌림
    a = GC._unit(GC._sub(pts[1], pts[0]))
    n = GC._unit(GC._add(a, b))
    from_out = [GC._sub(c, GC._mul(b, GC._dot(GC._sub(c, pts[1]), n) / GC._dot(b, n))) for c in back]
    assert all(_close(p, q, 1e-9) for p, q in zip(rings[1], from_out))


def test_a_u_turn_is_refused_rather_than_folded():
    with pytest.raises(GC.ContractError):
        GC.rect_rings([[0, 0, 0], [1000, 0, 0], [0, 0, 0]], 100, 100)


def test_closed_rect_loop_is_mitred_all_round_without_caps():
    """닫힌 사각 경로 끝에 뚜껑 둘을 겹쳐 두면 한 자리에 반대 방향 면이 생긴다."""
    square = [[0, 0, 0], [1000, 0, 0], [1000, 1000, 0], [0, 1000, 0], [0, 0, 0]]
    verts, faces = GC.rect_sweep_mesh(GC.rect_rings(square, 100, 60))
    assert len(verts) == 16 and len(faces) == 16 and _edges_twice(faces)
    assert GC._signed_volume(verts, faces) == pytest.approx(4000 * 100 * 60, rel=1e-9)


def test_native_builder_accessors_give_the_analytic_curve():
    seg = _arc()
    assert GC.arc_sweep(seg) == pytest.approx(math.pi / 2)
    assert _close(GC.arc_point(seg, 0.5), [1000 * math.cos(math.pi / 4), 1000 * math.sin(math.pi / 4), 0], 1e-9)
    degree, poles, knots, weights = GC.nurbs_definition(_quarter_circle_nurbs())
    assert degree == 2 and len(poles) == 3 and weights[1] == pytest.approx(math.sqrt(0.5))


@pytest.mark.parametrize("seg, want", [
    ({"type": "line", "start": [0, 0, 0], "end": [0, 0, 5]}, [0, 0, 1]),
    (_arc(), [0, 1, 0]),                              # (1000,0) 에서 반시계로 출발 → +y
    (_quarter_circle_nurbs(), [0, 1, 0]),             # 첫 제어 다각형 방향 = 접선
])
def test_start_tangent_is_the_curve_tangent_not_the_chord(seg, want):
    """`Arch.makePipe` 는 현(chord)에 수직으로 단면을 놓아 곡선 시작 관을 찌그러뜨렸다."""
    assert _close(GC.start_tangent([seg]), want, 1e-6)


def _sharp_route():
    """실측 사고 형상: 폭 200mm 덕트가 188mm 구간 양 끝에서 118° 씩 꺾인다."""
    t1 = math.radians(118)
    p2 = [1000 + 188 * math.cos(t1), 188 * math.sin(t1), 0]
    t2 = 2 * t1
    return [[0, 0, 0], [1000, 0, 0], p2, [p2[0] + 1000 * math.cos(t2), p2[1] + 1000 * math.sin(t2), 0]]


def test_short_sharp_bend_turns_a_single_tube_inside_out():
    route = _sharp_route()
    assert GC.inverted_segments(GC.rect_rings(route, 200, 100), route) == [1]


def test_rect_parts_cut_the_inverted_segment_into_a_straight_piece():
    route = _sharp_route()
    parts = GC.rect_parts(route, 200, 100)
    assert len(parts) == 3
    total = 0.0
    for part in parts:
        verts, faces = GC.rect_sweep_mesh(part)
        assert _edges_twice(faces)
        for a, b in zip(part, part[1:]):
            axis = GC._unit(GC._sub([sum(p[i] for p in b) / 4 for i in range(3)],
                                    [sum(p[i] for p in a) / 4 for i in range(3)]))
            assert all(GC._dot(GC._sub(b[k], a[k]), axis) > 0 for k in range(4))   # 옆면이 뒤집히지 않았다
        total += GC._signed_volume(verts, faces)
    length = sum(math.dist(a, b) for a, b in zip(route, route[1:]))
    assert total == pytest.approx(length * 200 * 100, rel=1e-9)
    gentle = [[0, 0, 0], [1000, 0, 0], [1000, 800, 0]]
    assert GC.rect_parts(gentle, 200, 100) == [GC.rect_rings(gentle, 200, 100)]     # 멀쩡하면 한 조각
