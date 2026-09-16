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
    # 높이 범위가 겹치는지도 보는 판정이라 그 높이의 근거를 함께 싣는다(도면 z + 선언 단면 → 가정 없음).
    assert elbow["z_basis"] == "declared" and "assumed" not in elbow
    assert {e["z_basis"] for e in net["open_ends"]} == {"source"}
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


def test_an_end_at_a_sleeve_is_not_open_and_is_told_apart_from_a_terminal():
    """슬리브를 지나 모델 밖으로 나가는 끝과 단말에서 끝나는 끝은 둘 다 '열림' 이 아니다 — 조치가 다르다."""
    g = _geom(duct=[_duct("d:out", [[0, 0], [1000, 0]]), _duct("d:term", [[0, 2000], [1000, 2000]]),
                    _duct("d:past", [[1000, 4000], [2400, 4000]])],
              equipment=[{"eid": "e:s", "kind": "polyline", "closed": True, "role": "sleeve", "z_base": 0,
                          "points": [[1010, -60], [1120, -60], [1120, 60], [1010, 60]]},
                         {"eid": "e:t", "kind": "polyline", "closed": True, "role": "terminal", "z_base": 0,
                          "points": [[1150, 1900], [1450, 1900], [1450, 2100], [1150, 2100]]},
                         {"eid": "e:past", "kind": "polyline", "closed": True, "role": "terminal", "z_base": 0,
                          "points": [[1500, 4120], [1800, 4120], [1800, 4300], [1500, 4300]]}])
    status = {(e["eid"], e["port"]): e["status"] for e in analyze(g)["open_ends"]}
    assert status[("d:out", "end")] == "sleeve" and status[("d:term", "end")] == "terminal"
    assert status[("d:out", "start")] == "open" and status[("d:term", "start")] == "open"
    # 경로 **옆** 120mm 에 단말이 있어도 그쪽을 향해 끝난 것이 아니면 단말이 아니다(거리만 보면 삼킨다).
    assert status[("d:past", "start")] == "open" and status[("d:past", "end")] == "open"


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


def test_a_confirmed_candidate_becomes_a_bridged_joint_and_a_vanished_one_is_reported():
    from mep_network import apply_bridges
    g = _geom(duct=[_duct("d:c", [[0, 3000], [1000, 3000]]), _duct("d:d", [[1200, 3000], [2000, 3000]])])
    net = analyze(g)
    (cand,) = net["candidates"]
    report = apply_bridges(g, [{"id": cand["id"]}, {"id": "gap:deadbeef12"}], net["candidates"])
    assert report["orphaned"] == [{"id": "gap:deadbeef12", "reason": "candidate_gone"}]
    # 확정하면 후보 목록에서 빠지므로, 화면이 '확정한 이음' 을 보여 주려면 위치·부재가 함께 와야 한다.
    (done,) = report["applied"]
    assert (done["id"], done["kind"], done["eids"], done["gap_mm"]) == (cand["id"], "straight", ["d:c", "d:d"], 200.0)
    assert done["points"] == cand["points"]
    refs = [d["joints"][0] for d in g["elements"]["duct"]]
    assert {r["id"] for r in refs} == {"j:" + cand["id"].split(":")[1]}
    assert [r["port"] for r in refs] == ["end", "start"]
    assert all(r["basis"] == "bridged" and r["gap_mm"] == 200.0 for r in refs)
    # 선언한 틈만큼 떨어진 것은 '구성원이 떨어졌다' 가 아니다 — 사람이 잇겠다고 declare 한 자리다.
    assert GC.joint_problems(g["elements"]) == []
    assert analyze(g)["summary"]["groups"] == 1                     # 확정 뒤에는 한 계통으로 센다


def _duct_project(tmp_path):
    import ezdxf
    from project_server import ProjectSession
    from project_store import ProjectStore
    doc = ezdxf.new(units=4)
    for a, b in (((0, 0), (1000, 0)), ((1200, 0), (2000, 0))):
        doc.modelspace().add_line(a, b, dxfattribs={"layer": "DUCT"})
    doc.saveas(tmp_path / "mep.dxf")
    return ProjectSession(ProjectStore(tmp_path / "unit.mep").create([{"id": "main", "path": str(tmp_path / "mep.dxf")}]))


def test_project_state_and_editor_review_carry_connection_candidates(tmp_path):
    session = _duct_project(tmp_path)
    net = session.state()["geometry"]["mep_connectivity"]
    (cand,) = net["candidates"]
    assert cand["kind"] == "straight" and net["summary"]["groups_with_candidates"] == 1
    assert any(i["category"] == "mep_gap" and i["eids"] == cand["eids"] for i in session.pascal_review()["items"])


def test_the_preview_route_confirms_a_candidate_with_the_project_token(tmp_path):
    import json as _json
    import urllib.request
    session = _duct_project(tmp_path)
    with session.serve() as server:
        def request(path, payload=None):
            headers = {"Authorization": "Bearer " + server.token}
            if payload is not None:
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(server.base_url + path, headers=headers,
                                         data=None if payload is None else _json.dumps(payload).encode())
            with urllib.request.urlopen(req) as response:
                return _json.load(response)
        state = request("/state")
        (cand,) = state["geometry"]["mep_connectivity"]["candidates"]
        saved = request("/bridges", {"project_id": state["project_id"], "expected_revision": state["revision"],
                                     "candidate_id": cand["id"], "confirmed": True})
    assert saved["revision"] == state["revision"] + 1
    assert [b["id"] for b in saved["geometry"]["mep_connectivity"]["bridges"]["applied"]] == [cand["id"]]
    assert all(any(j.get("basis") == "bridged" for j in d.get("joints") or [])
               for d in saved["geometry"]["elements"]["duct"])


def test_a_confirmation_is_stored_by_candidate_id_and_survives_reopening(tmp_path):
    from project_server import ProjectSession
    from project_store import ProjectStore
    session = _duct_project(tmp_path)
    state = session.state()
    (cand,) = state["geometry"]["mep_connectivity"]["candidates"]
    after = session.confirm_bridge(cand["id"], state["revision"], state["project_id"])
    assert [b["id"] for b in after["geometry"]["mep_connectivity"]["bridges"]["applied"]] == [cand["id"]]
    assert all(any(j.get("basis") == "bridged" for j in d.get("joints") or [])
               for d in after["geometry"]["elements"]["duct"])
    reopened = ProjectSession(ProjectStore(tmp_path / "unit.mep")).state()      # 종료 → 재열기
    net = reopened["geometry"]["mep_connectivity"]
    assert [b["id"] for b in net["bridges"]["applied"]] == [cand["id"]] and net["summary"]["groups"] == 1
    assert net["candidates"] == []                                             # 확정한 자리는 후보에서 빠진다
    undone = session.confirm_bridge(cand["id"], reopened["revision"], reopened["project_id"], confirmed=False)
    assert undone["geometry"]["mep_connectivity"]["bridges"]["applied"] == []
    assert undone["geometry"]["mep_connectivity"]["summary"]["groups"] == 2
