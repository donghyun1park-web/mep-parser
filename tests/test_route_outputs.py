# -*- coding: utf-8 -*-
"""계약 v3 경로를 소비자가 **같은 경로·같은 단면**으로 쓰는가 — Blender 준비·물량·빌드 게이트.

입상관이 천장에 눕거나, 원호가 꺾은선이 되거나, 평면 v2 경로의 출력이 조용히 바뀌면
모델은 열리지만 틀린다. 좌표·길이·게이트 판정으로 잰다.
"""
import copy
import json
import math

import pytest

import boq_export
import geom_contract as GC
import verify

pytest.importorskip("shapely")
import blender_builder  # noqa: E402
import blender_verify  # noqa: E402


def _data(**elements):
    return {"units": "mm", "params": {}, "elements": copy.deepcopy(elements),
            "contract": GC.contract_block()}


RISER = {"eid": "p:riser", "points": [[0, 0], [1000, 0]], "elevation": 100.0, "diameter": 20.0,
         "path3d": {"segments": [{"type": "line", "start": [0, 0, 0], "end": [1000, 0, 0]},
                                 {"type": "line", "start": [1000, 0, 0], "end": [1000, 0, 1000]}]}}
ROUND = {"eid": "d:round", "section_shape": "round", "diameter": 125.0, "elevation": 2500.0,
         "points": [[0, 0], [500, 500]],
         "path3d": {"segments": [{"type": "arc", "start": [0, 0, 0], "end": [500, 500, 0],
                                  "center": [0, 500, 0], "normal": [0, 0, 1]}]}}
SLOPED = {"eid": "d:sloped", "width_mm": 204.0, "height_mm": 60.0, "elevation": 2400.0,
          "points": [[0, 0], [2000, 0], [3000, 1000]],
          "path3d": {"segments": [{"type": "line", "start": [0, 0, 0], "end": [2000, 0, 0]},
                                  {"type": "line", "start": [2000, 0, 0], "end": [3000, 1000, 300]}]}}
FLAT = {"eid": "d:flat", "width_mm": 110.0, "height_mm": 54.0, "elevation": 2600.0,
        "points": [[0, 3000], [2000, 3000]]}


def test_blender_payload_follows_the_route_and_section():
    payload = blender_builder.prepare_payload(_data(pipe=[RISER], duct=[ROUND, SLOPED, FLAT]))
    blender_verify.validate_payload(payload)
    objs = {o["eid"]: o for o in payload["objects"]}
    ox, oy = payload["origin_mm"][:2]

    riser = objs["p:riser"]
    assert riser["kind"] == "curve" and riser["radius_m"] == pytest.approx(0.01)
    zs = [p[2] for p in riser["points_m"]]
    assert min(zs) == pytest.approx(0.1) and max(zs) == pytest.approx(1.1)      # 입상관이 눕지 않는다
    assert riser["z_bounds_mm"] == pytest.approx([90.0, 1110.0])

    rnd = objs["d:round"]
    assert rnd["kind"] == "curve" and rnd["radius_m"] == pytest.approx(0.0625)   # 원형 덕트 = 원형 bevel
    assert all(abs(math.hypot(p[0] * 1000 + ox, p[1] * 1000 + oy - 500) - 500) < 1e-6
               for p in rnd["points_m"])                                          # 원호가 원 위에 남는다

    sloped = objs["d:sloped"]
    assert sloped["kind"] == "mesh" and sloped["geometry_method"] == "route_mitre_sweep_3d"
    rings = GC.rect_rings(GC.route_points("duct", SLOPED), 204.0, 60.0)
    got = [[v[0] * 1000 + ox, v[1] * 1000 + oy, v[2] * 1000] for v in sloped["vertices_m"]]
    want = [p for ring in rings for p in ring]
    assert len(got) == len(want) and all(math.dist(p, q) < 1e-6 for p, q in zip(got, want))  # 공통 링 그대로
    assert objs["d:flat"]["geometry_method"] == "continuous_plan_mitre_sweep"   # v2 경로는 종전 그대로


def test_planar_line_route_with_path3d_builds_exactly_like_v2():
    v3 = dict(FLAT, eid="d:v3", path3d={"segments": GC.path3d_segments(FLAT)})
    payload = blender_builder.prepare_payload(_data(duct=[FLAT, v3]))
    a, b = [o for o in payload["objects"] if o["eid"] in ("d:flat", "d:v3")]
    assert a["vertices_m"] == b["vertices_m"] and a["faces"] == b["faces"]


def test_rect_duct_width_is_horizontal_in_every_direction():
    """폭은 수평이다 — 방향에 따라 폭·높이가 뒤바뀌어도 부피는 같아 어떤 검사에도 안 걸린다
    (FreeCAD 종전 스윕이 실제로 그랬다: 400×100 덕트가 x 방향이면 높이 방향 크기 400)."""
    for end in ([2000, 0, 0], [0, 2000, 0], [-1500, 900, 0]):
        rec = {"eid": "d:dir", "width_mm": 400.0, "height_mm": 100.0, "elevation": 1000.0,
               "points": [[0, 0], end[:2]],
               "path3d": {"segments": [{"type": "line", "start": [0, 0, 0], "end": end},
                                       {"type": "line", "start": end, "end": [end[0], end[1], 10]}]}}
        mesh = blender_builder.prepare_payload(_data(duct=[rec]))["objects"][0]
        zs = [v[2] * 1000 for v in mesh["vertices_m"][:4]]                       # 시작 단면
        assert max(zs) - min(zs) == pytest.approx(100.0, abs=1e-6)


def test_short_sharp_bend_still_builds_a_closed_mesh():
    """폭 200mm 덕트가 188mm 구간에서 118° 씩 꺾여도(실측 사고 형상) 닫힌 메시 · 부피 일치."""
    t1 = math.radians(118)
    p2 = [1000 + 188 * math.cos(t1), 188 * math.sin(t1), 0]
    p3 = [p2[0] + 1000 * math.cos(2 * t1), p2[1] + 1000 * math.sin(2 * t1), 10]
    rec = {"eid": "d:sharp", "width_mm": 200.0, "height_mm": 100.0, "elevation": 2400.0,
           "points": [[0, 0], [1000, 0], p2[:2], p3[:2]],
           "path3d": {"segments": GC.polyline_segments([[0, 0, 0], [1000, 0, 0], p2, p3])}}
    payload = blender_builder.prepare_payload(_data(duct=[rec]))
    blender_verify.validate_payload(payload)
    assert payload["objects"][0]["geometry_method"] == "route_mitre_sweep_3d"


def test_boq_counts_vertical_runs_and_round_ducts():
    header, rows, total = boq_export.aggregate(_data(pipe=[RISER], duct=[ROUND, FLAT]))["MEP"]
    by = {(r[0], r[1]): r for r in rows}
    assert by[("배관", "20")][3] == pytest.approx(2.0)                           # 수평 1m + 입상 1m
    assert by[("덕트", "Ø125")][3] == pytest.approx(math.pi / 2 * 500 / 1000, abs=1e-3)
    assert by[("덕트", "110x54")][3] == pytest.approx(2.0)


def test_broken_path3d_is_stopped_by_the_build_gate():
    broken = dict(SLOPED, eid="d:broken", path3d={"segments": [
        {"type": "line", "start": [0, 0, 0], "end": [1000, 0, 0]},
        {"type": "line", "start": [1500, 0, 0], "end": [2000, 0, 0]}]})
    rep = verify.verify_geometry(_data(duct=[broken]))
    v010 = [f for f in rep.findings if f.id == "V010"]
    assert v010 and v010[0].severity == "error", rep.text()
    assert "path3d" in json.dumps(v010[0].payload, ensure_ascii=False, default=str)
