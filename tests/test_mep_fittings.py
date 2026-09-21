# -*- coding: utf-8 -*-
"""이음 몸체 — 이음(`joints`)이 IFC 의 피팅이 된다. 형식은 물량표와 같은 함수, 크기는 도면의 틈과 단면.

설비 도면은 피팅 자리에서 중심선을 끊어 그린다. 사람이 그 틈을 이음으로 확정해도 형상은 그대로라 3D 는 끊긴
관이었고, IFC 에는 피팅이 하나도 없어 뷰어 물량과 물량표가 갈렸다.
"""
import json
import math
import os
import tempfile

import geom_contract as GC
import mep_network as MN

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pipe(eid, pts, **kw):
    rec = {"eid": eid, "kind": "polyline", "points": [list(p) for p in pts], "elevation": 2600.0, "diameter": 20.0}
    rec.update(kw)
    return rec


def _duct(eid, pts, **kw):
    rec = {"eid": eid, "kind": "polyline", "points": [list(p) for p in pts], "elevation": 2600.0,
           "width_mm": 200.0, "height_mm": 100.0}
    rec.update(kw)
    return rec


def _geometry():
    """도면이 이어 그린 이음(배관) + 사람이 확정한 틈 이음(덕트 엘보 · 원형 가지 티)."""
    g = {"source": "t.dxf", "units": "mm", "params": {}, "contract": GC.contract_block(),
         "floors": [{"z": 0.0, "label": "Level_1"}], "elements": {"pipe": [
             _pipe("p:A", [(0, 0), (1000, 0)]), _pipe("p:B", [(400, 0), (400, 800)]),              # 티
             _pipe("p:C", [(0, 2000), (1000, 2000)]), _pipe("p:D", [(1000, 2000), (1000, 3000)]),  # 엘보
             _pipe("p:E", [(3000, 0), (4000, 0)]), _pipe("p:F", [(4000, 0), (5000, 0)]),            # 곧게 끊어 그린 선
             _pipe("p:G", [(6000, 0), (7000, 0)]), _pipe("p:H", [(7000, 0), (8000, 0)], diameter=15.9),  # 레듀서
         ], "duct": [
             _duct("d:A", [(0, 5000), (1000, 5000)]), _duct("d:B", [(1150, 5150), (1150, 6000)]),    # 212mm 틈 엘보
             _duct("d:C", [(0, 7000), (2000, 7000)]),
             {"eid": "d:D", "kind": "polyline", "points": [[800, 7250], [800, 8000]], "elevation": 2600.0,
              "section_shape": "round", "diameter": 100.0},                                          # 250mm 틈 티
         ]}}
    GC.assign_joints(g["elements"])
    found = MN.analyze(g)["candidates"]
    assert sorted(c["kind"] for c in found) == ["elbow", "tee"], found
    assert not MN.apply_bridges(g, [{"id": c["id"]} for c in found], found)["orphaned"]
    return g


def _bounds(fitting):
    pts = [p for part in fitting["parts"] for ring in part for p in ring]
    return [min(p[k] for p in pts) for k in range(3)], [max(p[k] for p in pts) for k in range(3)]


def test_bodies_fill_the_corner_reach_the_confirmed_gap_and_match_the_quantity_table():
    import boq_export
    g = _geometry()
    out = GC.joint_fittings(g["elements"])
    by = {tuple(f["eids"]): f for f in out["fittings"]}
    assert {k: f["kind"] for k, f in by.items()} == {
        ("p:A", "p:B"): "tee", ("p:C", "p:D"): "elbow", ("p:G", "p:H"): "reducer",
        ("d:A", "d:B"): "elbow", ("d:C", "d:D"): "tee"}
    assert out["straight"] == 1 and out["skipped"] == []                   # 곧게 끊어 그린 같은 규격은 피팅이 아니다

    # 도면이 이은 엘보: 모서리 (1000, 2000) 의 바깥 쐐기까지 덮는다(뒤로 반폭 10, 앞으로 반폭 10 + 반지름 10).
    lo, hi = _bounds(by[("p:C", "p:D")])
    assert [round(v, 6) for v in lo[:2] + hi[:2]] == [980.0, 1990.0, 1010.0, 2020.0]
    # 확정한 엘보: 축선이 만나는 (1150, 5000) 이 중심이고, 몸체는 두 끝(1000, 5000)·(1150, 5150)을 넘어 닿는다.
    elbow = by[("d:A", "d:B")]
    assert [round(v, 6) for v in elbow["center"]] == [1150.0, 5000.0, 2600.0]
    lo, hi = _bounds(elbow)
    assert lo[0] <= 1000 and hi[1] >= 5150 and (lo[2], hi[2]) == (2550.0, 2650.0)
    # 확정한 티: 원형 가지 몸체가 줄기 축선 안쪽에서 가지 끝(y 7250)까지.
    lo, hi = _bounds(by[("d:C", "d:D")])
    assert [round(v, 6) for v in (lo[1], hi[1])] == [6900.0, 7250.0]

    for fitting in out["fittings"]:                                       # 토막마다 닫힌 셸 — IFC 재검사가 요구한다
        for part in fitting["parts"]:
            verts, faces = GC.rect_sweep_mesh(part)
            edges = {}
            for f in faces:
                for a, b in zip(f, f[1:] + f[:1]):
                    edges[(a, b)] = edges.get((a, b), 0) + 1
            assert all(edges.get((b, a)) == 1 for (a, b) in edges) and GC._signed_volume(verts, faces) > 0

    rows = boq_export.aggregate(g)["MEP 이음"][1]                          # 물량표와 모델의 피팅 수가 같다
    assert sum(r[3] for r in rows) == len(out["fittings"])
    assert ["덕트", "엘보", "200x100", 1] in rows and ["덕트", "티", "200x100/Ø100", 1] in rows


def test_a_broken_joint_gets_no_body_and_says_why():
    g = _geometry()
    g["elements"]["pipe"] = [r for r in g["elements"]["pipe"] if r["eid"] != "p:B"]     # 티의 가지를 지웠다
    out = GC.joint_fittings(g["elements"])
    assert [(s["reason"], s["eids"]) for s in out["skipped"]] == [("single_member", ["p:A"])]
    assert ("p:A",) not in {tuple(f["eids"]) for f in out["fittings"]}


def test_fittings_reach_the_ifc_as_fitting_classes_and_pass_the_reverification():
    from test_builder import _build, _skip_if_no_freecad
    _skip_if_no_freecad()
    directory = tempfile.mkdtemp(prefix="mep_fittings_")
    geom = os.path.join(directory, "geometry.json")
    with open(geom, "w", encoding="utf-8") as stream:
        json.dump(_geometry(), stream)
    _log, st = _build(geom, os.path.join(directory, "out"))
    assert st["artifacts"]["ifc"]["status"] == "verified", st.get("verify_ifc")
    assert st["fittings"]["built"] == 5 and st["fittings"]["skipped"] == [], st["fittings"]
    assert st["built"]["mep"] == 12 and st["unbuilt"] == {}                # 경로 부재 수는 그대로다
    with open(st["artifacts"]["ifc"]["path"], encoding="utf-8", errors="ignore") as stream:
        ifc = stream.read()
    assert ifc.count("IFCPIPEFITTING(") == 3 and ifc.count("IFCDUCTFITTING(") == 2
    assert "'FittingKind'" in ifc
    assert all(math.isfinite(v["exported_mm3"]) for v in st["ifc_validation"]["volumes"])
