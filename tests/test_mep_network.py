"""설비 연결성 — 조각·끊긴 끝·이음 후보. 가까울 뿐인 끝은 후보가 아니고, 모델은 바뀌지 않는다."""
import copy

import geom_contract as GC
from mep_network import analyze


def _duct(eid, pts, system="SA", w=110, h=54, elevation=2573, **extra):
    return dict({"eid": eid, "kind": "polyline", "points": pts, "elevation": elevation,
                 "width_mm": w, "height_mm": h, "system": system}, **extra)


def _geom(**elements):
    return {"contract": GC.contract_block(), "params": {}, "elements": elements}


def test_elbow_and_straight_gaps_are_candidates_but_ends_that_are_only_close_are_not():
    g = _geom(duct=[_duct("d:a", [[0, 0], [1000, 0]]), _duct("d:b", [[1150, 150], [1150, 1200]]),
                    _duct("d:c", [[0, 3000], [1000, 3000]]), _duct("d:d", [[1200, 3000], [2000, 3000]]),
                    _duct("d:e", [[0, 6000], [1000, 6000]]), _duct("d:f", [[0, 6150], [1000, 6150]], system="RA")])
    before = copy.deepcopy(g)
    net = analyze(g)
    assert g == before                                                     # 모델을 바꾸지 않는다
    assert sorted((c["kind"], tuple(c["eids"]), c["gap_mm"]) for c in net["candidates"]) == [
        ("elbow", ("d:a", "d:b"), 212.1), ("straight", ("d:c", "d:d"), 200.0)]
    elbow = next(c for c in net["candidates"] if c["kind"] == "elbow")
    assert elbow["points"] == [[1000.0, 0.0], [1150.0, 0.0], [1150.0, 150.0]]   # 모서리를 지나는 점선
    s = net["summary"]
    assert (s["runs"], s["groups"], s["groups_with_candidates"], s["open_ends"], s["conflicts"]) == (6, 6, 4, 12, 0)
    assert analyze(g)["candidates"][0]["id"] == net["candidates"][0]["id"]      # 다시 돌려도 같은 id


def test_drawn_joints_close_ends_a_branch_is_a_tee_and_ends_at_equipment_are_terminal():
    g = _geom(duct=[_duct("d:g", [[0, 0], [1000, 0]], joints=[{"id": "j:1", "port": "end"}]),
                    _duct("d:h", [[1000, 0], [2000, 0]], joints=[{"id": "j:1", "port": "start"}]),
                    _duct("d:i", [[500, 250], [500, 1000]])],
              equipment=[{"eid": "e:1", "kind": "polyline", "closed": True, "z_base": 2300,
                          "points": [[2050, -200], [2450, -200], [2450, 200], [2050, 200]]}])
    net = analyze(g)
    assert {(e["eid"], e["port"]): e["status"] for e in net["open_ends"]} == {
        ("d:g", "start"): "open", ("d:h", "end"): "terminal", ("d:i", "start"): "tee", ("d:i", "end"): "open"}
    (tee,) = net["candidates"]
    assert (tee["kind"], tee["eids"], tee["ports"], tee["gap_mm"], tee["points"]) == (
        "tee", ["d:i", "d:g"], ["start", "tap"], 250.0, [[500.0, 250.0], [500.0, 0.0]])
    assert (net["summary"]["groups"], net["summary"]["groups_with_candidates"]) == (2, 1)


def test_other_system_meeting_is_a_conflict_and_each_end_takes_only_its_nearest_partner():
    g = _geom(duct=[_duct("d:sa", [[0, 0], [1000, 0]]), _duct("d:ra", [[1200, 0], [2000, 0]], system="RA"),
                    _duct("d:a", [[0, 5000], [1000, 5000]]),
                    _duct("d:near", [[1100, 5000], [1900, 5000]], w=204, h=60, elevation=2570),
                    _duct("d:far", [[1000, 5200], [1000, 6000]])])
    net = analyze(g)
    (conflict,) = net["conflicts"]
    assert (conflict["kind"], conflict["systems"]) == ("straight", ["SA", "RA"])
    (cand,) = net["candidates"]                                            # d:far 의 엘보(200mm)는 더 가까운 직선에 밀린다
    assert cand["eids"] == ["d:a", "d:near"] and cand["size_change"] and cand["sizes"] == ["110×54", "204×60"]
    assert {(e["eid"], e["port"]): e["status"] for e in net["open_ends"]}[("d:far", "start")] == "open"


def test_project_state_and_editor_review_carry_connection_candidates(tmp_path):
    import ezdxf
    from project_server import ProjectSession
    from project_store import ProjectStore
    doc = ezdxf.new(units=4)
    for a, b in (((0, 0), (1000, 0)), ((1200, 0), (2000, 0))):
        doc.modelspace().add_line(a, b, dxfattribs={"layer": "DUCT"})
    doc.saveas(tmp_path / "mep.dxf")
    session = ProjectSession(ProjectStore(tmp_path / "unit.mep").create([{"id": "main", "path": str(tmp_path / "mep.dxf")}]))
    net = session.state()["geometry"]["mep_connectivity"]
    (cand,) = net["candidates"]
    assert cand["kind"] == "straight" and net["summary"]["groups_with_candidates"] == 1
    assert any(i["category"] == "mep_gap" and i["eids"] == cand["eids"] for i in session.pascal_review()["items"])
