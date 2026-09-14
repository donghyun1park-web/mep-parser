# -*- coding: utf-8 -*-
"""이음은 도면이 실제로 이어 그린 곳에만 생긴다 — 가까움으로 잇지 않는다.

틈·교차·다른 높이를 이음으로 치면 SA 와 RA 가 한 계통이 되고 피팅 물량이 부푼다. 이음은
레코드가 id 로 들고 다니므로, 편집 뒤에 끊긴 이음은 고치지 않고 검사가 드러낸다.
"""
import contextlib
import copy
import io
import os

import pytest

import geom_contract as GC

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(eid, pts, elev=2600.0, **extra):
    rec = {"eid": eid, "kind": "polyline", "points": [list(p) for p in pts], "elevation": elev,
           "diameter": 20.0}
    rec.update(extra)
    return rec


def _refs(rec):
    return [(j["id"], j["port"], j.get("at_mm")) for j in rec.get("joints") or []]


def _elements():
    duct = _run("L", [(0, 0), (0, -1000)], width_mm=200.0, height_mm=100.0)   # 배관 A 시작과 같은 점, 카테고리가 다르다
    duct.pop("diameter")
    return {"pipe": [
        _run("A", [(0, 0), (1000, 0)]),                     # 줄기
        _run("B", [(400, 0), (400, 800)]),                  # 줄기 안쪽에서 나온 가지 → A 는 tap
        _run("C", [(0, 2000), (1000, 2000)]),
        _run("D", [(1000, 2000), (1000, 3000)]),            # C 끝과 같은 점
        _run("E", [(1000, 2000), (2000, 2000)]),            # 셋째 → 티
        _run("F", [(3000, -500), (3000, 500)]),             # 안쪽끼리 교차 — 이음이 아니다
        _run("G", [(2500, 0), (3500, 0)]),
        _run("H", [(5000, 0), (6000, 0)]),
        _run("I", [(6000.5, 0), (7000, 0)]),                # 0.5mm 틈 — 잇지 않는다
        _run("J", [(8000, 0), (9000, 0)]),
        _run("K", [(9000, 0), (9000, 1000)], elev=2800.0),  # 평면으로는 같은 점, 높이가 다르다
    ], "duct": [duct]}


def test_only_drawn_connections_become_joints():
    el = _elements()
    assert GC.assign_joints(el) == {"joints": 2, "taps": 1, "by_degree": {"3": 2}}
    by = {r["eid"]: r for r in el["pipe"]}
    (a,), (b,) = _refs(by["A"]), _refs(by["B"])
    assert a[0] == b[0] and a[1:] == ("tap", 400.0) and b[1:] == ("start", None)
    tee = [_refs(by[k]) for k in "CDE"]
    assert len({r[0][0] for r in tee}) == 1 and [r[0][1] for r in tee] == ["end", "start", "start"]
    assert all("joints" not in by[k] for k in "FGHIJK") and "joints" not in el["duct"][0]
    assert GC.joint_problems(el) == []
    again = copy.deepcopy(el)
    GC.assign_joints(again)
    assert again == el                                      # 다시 파싱해도 같은 id


def test_edits_that_break_a_joint_are_reported_not_repaired():
    el = _elements()
    GC.assign_joints(el)
    by = {r["eid"]: r for r in el["pipe"]}
    el["pipe"].remove(by["B"])                              # 가지를 지웠다
    by["D"]["points"][0] = [1000, 2100]                     # 옮긴 경로가 이음 id 를 그대로 들고 있다
    problems = {p["problem"]: p for p in GC.joint_problems(el)}
    assert set(problems) == {"single_member", "members_apart"}
    assert problems["single_member"]["eids"] == ["A"]
    assert problems["members_apart"]["distance_mm"] == pytest.approx(100.0)
    assert sorted(problems["members_apart"]["eids"]) == ["C", "D", "E"]


def test_broken_joints_surface_as_connection_review_items():
    import verify
    el = _elements()
    GC.assign_joints(el)
    el["pipe"] = [r for r in el["pipe"] if r["eid"] != "B"]
    data = {"source": "t.dxf", "units": "mm", "params": {}, "elements": el,
            "contract": GC.contract_block(), "floors": [{"z": 0.0, "label": "Level_1"}]}
    (v012,) = [f for f in verify.verify_geometry(data).findings if f.id == "V012"]
    assert [s["reason"] for s in v012.payload["sample"]] == ["joint_single_member"]


def test_boq_counts_tees_elbows_and_reducers_but_not_straight_splits():
    import boq_export
    elements = {"pipe": [
        _run("A", [(0, 0), (1000, 0)]), _run("B", [(400, 0), (400, 800)]),                     # 티
        _run("C", [(0, 2000), (1000, 2000)]), _run("D", [(1000, 2000), (1000, 3000)]),         # 엘보
        _run("E", [(3000, 0), (4000, 0)]), _run("F", [(4000, 0), (5000, 0)]),                   # 곧게 끊어 그린 선
        _run("G", [(6000, 0), (7000, 0)]), _run("H", [(7000, 0), (8000, 0)], diameter=15.9),   # 레듀서
    ]}
    GC.assign_joints(elements)
    rows = boq_export.aggregate({"params": {}, "elements": elements})["MEP 이음"][1]
    assert rows == [["배관", "레듀서", "15.9/20", 1], ["배관", "엘보", "20", 1], ["배관", "티", "20", 1]]


def test_joints_ride_the_pascal_round_trip_and_unread_pascal_nodes_are_counted():
    import pascal_bridge as PB
    el = _elements()
    GC.assign_joints(el)
    geo = {"source": "t.dxf", "units": "mm", "params": {}, "elements": el, "contract": GC.contract_block()}
    scene, _ = PB.to_pascal_scene(geo)
    lid = next(n["id"] for n in scene["nodes"].values() if n["type"] == "level")
    scene["nodes"]["duct-fitting_user0001"] = {"object": "node", "id": "duct-fitting_user0001",
                                               "type": "duct-fitting", "parentId": lid, "visible": True,
                                               "metadata": {}}
    back, rev = PB.from_pascal_scene(scene)
    assert ({r["eid"]: r.get("joints") for r in back["elements"]["pipe"]}
            == {r["eid"]: r.get("joints") for r in el["pipe"]})
    assert rev["dropped"] == {"pascal_node:duct-fitting": 1}          # 피팅은 아직 저장되지 않는다 — 센다
    assert PB.scene_to_edits(geo, scene)[0] == {}


def test_sample_mep_branch_pipe_is_a_drawn_tee():
    import dxf_parser as dp
    with contextlib.redirect_stdout(io.StringIO()):
        g = dp.parse(os.path.join(ROOT, "sample_mep.dxf"),
                     dp.load_layer_map(os.path.join(ROOT, "layer_map.csv")),
                     dp.load_layer_map(os.path.join(ROOT, "block_map.csv")))
    assert g["mep_joints"] == {"joints": 1, "taps": 1, "by_degree": {"3": 1}}
    refs = [(j["port"], j.get("at_mm")) for r in g["elements"]["pipe"] for j in r.get("joints") or []]
    assert len(refs) == 2 and ("tap", 5000.0) in refs
    assert all("joints" not in r for cat in ("duct", "tray") for r in g["elements"][cat])


def test_stacked_floors_keep_their_own_joint_ids():
    """이음 id 는 이음 점 좌표에서 나온다 — 같은 DXF 를 두 층에 쓰면 층 접두가 없을 때 두 층의 티가 한 이음이 된다."""
    import stack_build as SB
    spec = {"levels": [{"id": lid, "source": "sample_mep.dxf", "z": z,
                        "layer_map": "layer_map.csv", "block_map": "block_map.csv"}
                       for lid, z in (("1F", 0.0), ("2F", 3000.0))]}
    with contextlib.redirect_stdout(io.StringIO()):
        g = SB.build_stack(spec, base_dir=ROOT)
    ids = {j["id"] for r in g["elements"]["pipe"] for j in r.get("joints") or []}
    assert sorted(i.split(":")[0] for i in ids) == ["1F", "2F"] and all(i.count(":") == 2 for i in ids)
    assert GC.joint_problems(g["elements"]) == []
