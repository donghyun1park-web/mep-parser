# -*- coding: utf-8 -*-
"""편집 화면의 검토 목록 — 사람이 봐야 할 것만, 조치할 것이 없는 사유는 빼고.

목록이 넘치면 아무도 안 보고, 빠지면 벽을 지워 연결이 풀린 개구부나 끊긴 이음이 조용히 남는다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import geom_contract as GC  # noqa: E402


def test_review_lists_flags_unlinked_openings_and_broken_joints_only(tmp_path, monkeypatch):
    from project_server import ProjectSession
    wall = {"kind": "polyline", "closed": False, "eid": "w:1", "layer": "A-WALL", "z_base": 0.0,
            "points": [[0.0, 0.0], [4000.0, 0.0]], "centerline": [[0.0, 0.0], [4000.0, 0.0]],
            "needs_review": True, "review_reason": "single_offset", "overrides": {"height": 2800.0}}
    unlinked = {"kind": "circle", "eid": "o:1", "center": [9000.0, 0.0], "radius": 450.0, "z_base": 0.0,
                "width": 900.0, "height": 2100.0, "wall_indices": [], "no_host_reason": "no_wall_on_this_line"}
    jamb = dict(unlinked, eid="o:2", no_host_reason="wall_open_at_this_span")      # 조치할 것 없음 — 빼야 한다
    pipes = [{"eid": "p:a", "kind": "polyline", "points": [[0, 0], [1000, 0]], "elevation": 2600.0, "diameter": 20.0},
             {"eid": "p:b", "kind": "polyline", "points": [[1000, 0], [1000, 1000]], "elevation": 2600.0, "diameter": 20.0}]
    elements = {"wall": [wall], "opening": [unlinked, jamb], "pipe": pipes}
    GC.assign_joints(elements)
    elements["pipe"] = pipes[:1]                                                    # 상대를 지웠다
    geometry = {"source": "t.dxf", "units": "mm", "params": {}, "elements": elements,
                "contract": GC.contract_block(),
                "construction_rules": {"items": [
                    {"eid": "d:1", "rule": "duct-aspect-ratio", "kind": "violation",
                     "standard": "KCS 31 20 20", "clause": "3.2.1(2)②", "values": {"ratio": 4.4}},
                    {"eid": "p:x", "rule": "drain-slope-by-diameter", "kind": "info",
                     "standard": "KDS 31 30 25", "clause": "4.1"}]}}  # info 는 목록에 안 나간다
    sess = ProjectSession.__new__(ProjectSession)
    monkeypatch.setattr(sess, "state", lambda: {"project_id": "p", "revision": 3, "geometry": geometry})
    got = sess.pascal_review()
    assert got["revision"] == 3
    assert [(i["category"], i["eid"], i["reason"]) for i in got["items"]] == [
        ("wall", "w:1", "single_offset"), ("opening", "o:1", "opening_no_wall_on_this_line"),
        ("joint", "p:a", "joint_single_member"),
        ("rule", "d:1", "KCS 31 20 20 3.2.1(2)② 위반")]
    assert {u["eid"] for u in got["unconvertible"]} == {"o:1", "o:2"}               # 벽 없는 개구부는 못 보낸다
