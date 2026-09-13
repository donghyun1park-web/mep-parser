# -*- coding: utf-8 -*-
"""원본 곡선 → 계약 v3 `path3d`. 샘플 점과 **같은 곡선·같은 방향**이어야 한다.

해석 구간이 샘플과 어긋나면 빌더가 그리는 경로와 화면·물량이 보는 경로가 달라진다.
끝점·길이·방향을 원본 엔티티 종류별로 잰다.
"""
import math

import ezdxf
import pytest
from ezdxf.math import Vec3

import geom_contract as GC
from mep_paths import extract_curve, join_paths


def _msp():
    return ezdxf.new().modelspace()


def _line(msp):
    return msp.add_line((0, 0), (3000, 400))


def _arc(msp):
    return msp.add_arc((100, 50), 300, 20, 250)


def _arc_flipped(msp):                         # CAD 에서 거울 복사한 원호(돌출 −Z)
    return msp.add_arc((100, 50), 300, 20, 250, dxfattribs={"extrusion": (0, 0, -1)})


def _circle(msp):
    return msp.add_circle((10, 20), 150)


def _bulge(msp):
    return msp.add_lwpolyline([(0, 0, 0.5), (1000, 0, 0), (1000, 1000, -1.0), (0, 1000)], format="xyb")


def _spline(msp):
    return msp.add_spline(fit_points=[(0, 0), (1000, 500), (2000, 0), (3000, 800)])


def _ellipse(msp):
    return msp.add_ellipse((0, 0), major_axis=(2000, 0), ratio=0.4, start_param=0.3, end_param=2.5)


def _elevated_line(msp):                        # 원본 z 는 상대 0 으로 담긴다
    return msp.add_line((0, 0, 2400), (1000, 0, 2400))


@pytest.mark.parametrize("make", [_line, _arc, _arc_flipped, _circle, _bulge, _spline, _ellipse, _elevated_line])
@pytest.mark.parametrize("scale", [1.0, 25.4])
def test_path3d_is_the_sampled_curve(make, scale):
    rec = extract_curve(make(_msp()), scale, 0.5)
    assert rec["path3d_basis"] == "analytic_source"
    segs = rec["path3d"]["segments"]
    assert all(abs(p[2]) < 1e-9 for p in GC.sample_segments(segs, 0.5))      # 평면 원본 = 상대 z 0
    dense = GC.sample_segments(segs, 0.01, rec["source_elevation_mm"])
    assert math.dist(dense[0][:2], rec["points"][0]) < 1e-6
    assert math.dist(dense[-1][:2], rec["points"][-1]) < 1e-6
    # 원본 샘플 점은 해석 곡선 **위**에 있어야 한다. 조밀 꺾은선(현오차 0.01)까지의
    # 점-선분 거리로 잰다 — 조밀 '점'까지 재면 반지름이 클 때 점 간격이 오차로 섞인다.
    def seg_dist(p, a, b):
        ab = (b[0] - a[0], b[1] - a[1])
        L2 = ab[0] ** 2 + ab[1] ** 2
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * ab[0] + (p[1] - a[1]) * ab[1]) / L2))
        return math.dist(p, (a[0] + t * ab[0], a[1] + t * ab[1]))

    for p in rec["points"][:: max(1, len(rec["points"]) // 25)]:
        assert min(seg_dist(p, a, b) for a, b in zip(dense, dense[1:])) < 0.02
    if rec["source_length_mm"] is not None:                                   # 해석 길이
        assert GC.route_length(rec) == pytest.approx(rec["source_length_mm"], rel=1e-9, abs=1e-9)
    else:                                                                      # 근사 길이
        assert GC.route_length(rec) == pytest.approx(rec["sampled_length_mm"], rel=2e-3)


def test_eids_do_not_change_because_of_path3d():
    """식별(_sigs)은 원본 사양에서만 나온다 — v3 필드가 EID 를 흔들면 수정이 고아가 된다."""
    rec = extract_curve(_arc(_msp()), 1.0, 0.5)
    again = extract_curve(_arc(_msp()), 1.0, 0.5)
    assert rec["_sigs"] == again["_sigs"]
    assert "path3d" not in rec["source_geometry"][0]


def test_joined_path_reverses_the_segments_of_reversed_sources():
    msp = _msp()
    a = extract_curve(msp.add_line((0, 0), (1000, 0)), 1.0, 0.5)
    b = extract_curve(msp.add_arc((1000, 500), 500, 270, 360), 1.0, 0.5)        # (1000,0) → (1500,500)
    c = extract_curve(msp.add_line((1500, 2000), (1500, 500)), 1.0, 0.5)       # 거꾸로 그린 선
    for r in (a, b, c):
        r["layer"] = "SA"
    joined, report = join_paths([c, a, b], 0.001, 10)
    assert len(joined) == 1 and report["connected_paths"] == 1
    rec = joined[0]
    assert GC.path3d_problems(rec) == []                                        # 끊김 없음
    dense = GC.sample_segments(rec["path3d"]["segments"], 0.01)
    assert math.dist(dense[0][:2], rec["points"][0]) < 1e-6
    assert math.dist(dense[-1][:2], rec["points"][-1]) < 1e-6
    assert GC.route_length(rec) == pytest.approx(rec["source_length_mm"], rel=1e-12)
    assert [s["type"] for s in rec["path3d"]["segments"]] in (["line", "arc", "line"],)


def test_mismatched_analytic_route_falls_back_to_evaluated_points_and_says_so(monkeypatch):
    import mep_paths
    real = mep_paths._spline_segment

    def broken(bspline, scale):
        seg = real(bspline, scale)
        seg["control_points"] = [[p[0] + 5, p[1], p[2]] for p in seg["control_points"]]
        return seg

    monkeypatch.setattr(mep_paths, "_spline_segment", broken)
    rec = extract_curve(_spline(_msp()), 1.0, 0.5)
    assert rec["path3d_basis"] == "evaluated_points"
    assert all(s["type"] == "line" for s in rec["path3d"]["segments"])
