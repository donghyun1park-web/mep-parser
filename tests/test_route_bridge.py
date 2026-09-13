# -*- coding: utf-8 -*-
"""계약 v3 경로가 Pascal 을 오가도 눕거나 모양을 잃지 않는가.

입상관이 한 높이로 실리거나, 돌아온 경로가 계약에 없는 형식으로 저장되면 다음 빌드
게이트(V010)에서 막힌다. 왕복·변경 명령을 좌표로 잰다.
"""
import copy

import pytest

import geom_contract as GC
import pascal_bridge as PB


def _geo(**elements):
    return {"source": "t.dxf", "units": "mm", "params": {}, "elements": copy.deepcopy(elements),
            "contract": GC.contract_block()}


RISER = {"eid": "p:riser", "layer": "HW", "diameter": 20.0, "elevation": 100.0,
         "points": [[0, 0], [1000, 0]],
         "path3d": {"segments": [{"type": "line", "start": [0, 0, 0], "end": [1000, 0, 0]},
                                 {"type": "line", "start": [1000, 0, 0], "end": [1000, 0, 1000]}]}}


def _node(scene, eid):
    return next(n for n in scene["nodes"].values()
                if ((n.get("metadata") or {}).get("mep") or {}).get("eid") == eid)


def test_riser_goes_to_pascal_with_per_point_heights_and_comes_back_as_v3():
    geo = _geo(pipe=[RISER])
    scene, report = PB.to_pascal_scene(geo)
    ys = [p[1] for p in _node(scene, "p:riser")["path"]]
    assert round(max(ys) - min(ys), 9) == 1.0                            # 1m 입상이 Pascal 에서도 1m
    assert report.get("path3d") == 1
    back, _rev = PB.from_pascal_scene(scene)
    rec = back["elements"]["pipe"][0]
    assert isinstance(rec["path3d"], dict) and GC.path3d_problems(rec) == []
    zs = [p[2] for p in GC.route_points("pipe", rec)]
    assert min(zs) == 100.0 and max(zs) == 1100.0                       # 눕지 않았다
    assert PB.scene_to_edits(geo, scene)[0] == {}                        # 손대지 않으면 명령 0


def test_raising_the_whole_riser_is_an_elevation_declaration_not_a_new_route():
    geo = _geo(pipe=[RISER])
    scene, _ = PB.to_pascal_scene(geo)
    for p in _node(scene, "p:riser")["path"]:
        p[1] += 0.05
    edits, report = PB.scene_to_edits(geo, scene)
    assert list(edits) == ["p:riser"] and list(edits["p:riser"]) == ["overrides"]      # EID 유지
    assert edits["p:riser"]["overrides"] == {"elevation": pytest.approx(150.0, abs=1e-9)}


def test_raising_one_point_becomes_a_new_v3_route_with_relative_heights():
    geo = _geo(pipe=[RISER])
    scene, _ = PB.to_pascal_scene(geo)
    _node(scene, "p:riser")["path"][-1][1] += 0.5
    edits, report = PB.scene_to_edits(geo, scene)
    assert edits["p:riser"] == {"deleted": True} and report["moved"] == 1
    (new_eid, cmd), = [(k, v) for k, v in edits.items() if v.get("added")]
    rec = cmd["record"]
    assert GC.path3d_problems(rec) == [] and all(s["type"] == "line" for s in rec["path3d"]["segments"])
    zs = [p[2] for p in GC.route_points("pipe", rec)]
    assert min(zs) == 100.0 and max(zs) == 1600.0


def test_planar_arc_route_is_untouched_by_a_round_trip():
    arc = {"type": "arc", "start": [0, 0, 0], "end": [500, 500, 0], "center": [0, 500, 0], "normal": [0, 0, 1]}
    samples = GC.sample_segments([arc], 0.5)
    duct = {"eid": "d:arc", "layer": "SA", "width_mm": 204.0, "height_mm": 60.0, "elevation": 2500.0,
            "points": [[p[0], p[1]] for p in samples], "path3d": {"segments": [arc]}}
    geo = _geo(duct=[duct])
    scene, report = PB.to_pascal_scene(geo)
    assert not report.get("path3d")                                      # 평면 경로는 종전 그대로
    assert PB.scene_to_edits(geo, scene)[0] == {}
