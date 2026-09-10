# -*- coding: utf-8 -*-
"""geometry.json ↔ Pascal 씬 왕복 — 0단계 다리.

이 다리의 유일한 위험은 **단위**(우리 mm, Pascal m)와 **고저**(우리는 부재의
z_base, Pascal 은 레벨 스택)다. 둘 다 틀려도 형상은 멀쩡해 보이므로 —
1000배 작은 건물도, 한 층 내려앉은 벽도 열리기는 열린다 — 좌표로 잰다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pascal_bridge as PB


def _wall(eid, a, b, width=200.0, z=0.0):
    return {"kind": "polyline", "closed": False, "points": [a, b], "centerline": [a, b],
            "width_detected": width, "confidence": 1.0, "pairing": "paired",
            "needs_review": False, "z_base": z, "layer": "A-WALL", "eid": eid,
            "overrides": {"height": 2800.0}}


def _geom(walls, floors=None):
    return {"source": "t.dxf", "units": "mm", "params": {"wall": {"width": 200.0, "height": 2800.0}},
            "elements": {"wall": walls}, "floors": floors or [{"z": 0.0, "label": "Level_1"}]}


def _walls_of(scene):
    return [n for n in scene["nodes"].values() if n["type"] == "wall"]


def test_millimeters_become_meters_and_come_back():
    """3000mm 벽은 Pascal 에서 3.0 이고, 돌아오면 다시 3000 이다."""
    g = _geom([_wall("w:1", [0.0, 0.0], [3000.0, 0.0])])
    scene, rep = PB.to_pascal_scene(g)
    w = _walls_of(scene)[0]
    assert w["start"] == [0.0, 0.0] and w["end"] == [3.0, 0.0]
    assert w["thickness"] == 0.2 and w["height"] == 2.8
    assert rep["walls_out"] == 1 and rep["unconvertible"] == []

    back, _ = PB.from_pascal_scene(scene)
    r = back["elements"]["wall"][0]
    assert r["centerline"] == [[0.0, 0.0], [3000.0, 0.0]]
    assert r["eid"] == "w:1" and r["z_base"] == 0.0


def test_scene_hierarchy_is_wired_both_ways():
    """Pascal 은 parentId 와 부모 children 을 **둘 다** 본다 — 한쪽만 채우면 안 열린다."""
    scene, _ = PB.to_pascal_scene(_geom([_wall("w:1", [0.0, 0.0], [1000.0, 0.0])]))
    nodes = scene["nodes"]
    assert len(scene["rootNodeIds"]) == 1
    site = nodes[scene["rootNodeIds"][0]]
    assert site["type"] == "site" and site["parentId"] is None
    for nid, n in nodes.items():
        assert n["id"] == nid and n["object"] == "node"
        if n["parentId"] is not None:
            assert nid in nodes[n["parentId"]]["children"], nid


def test_level_stack_restores_absolute_z():
    """Pascal 의 baseElevation 은 절대 고저가 아니라 **누적 위에 더하는 오프셋**이다.
    storey.ts 를 그대로 뒤집지 않으면 위층이 조용히 내려앉는다."""
    g = _geom([_wall("w:1", [0.0, 0.0], [1000.0, 0.0], z=0.0),
               _wall("w:2", [0.0, 0.0], [1000.0, 0.0], z=4200.0)],
              floors=[{"z": 0.0, "label": "B1"}, {"z": 4200.0, "label": "1F"}])
    scene, rep = PB.to_pascal_scene(g)
    assert rep["levels"] == 2
    lv = sorted((n for n in scene["nodes"].values() if n["type"] == "level"),
                key=lambda n: n["level"])
    assert [n["name"] for n in lv] == ["B1", "1F"]
    assert lv[0]["baseElevation"] == 0.0 and lv[0]["height"] == 4.2

    back, _ = PB.from_pascal_scene(scene)
    assert sorted(w["z_base"] for w in back["elements"]["wall"]) == [0.0, 4200.0]
    assert [f["z"] for f in back["floors"]] == [0.0, 4200.0]


def test_rectangular_closed_wall_survives_as_an_axis():
    """닫힌 직사각형 벽은 Pascal 벽과 같은 형상이다 — 축선 ± 두께/2 로 무손실."""
    ring = [[0.0, 0.0], [2000.0, 0.0], [2000.0, 600.0], [0.0, 600.0]]
    w = {"kind": "polyline", "closed": True, "points": ring, "confidence": 1.0,
         "pairing": "closed", "needs_review": False, "z_base": 0.0,
         "layer": "A-CON", "eid": "w:c", "overrides": {"height": 2800.0}}
    scene, rep = PB.to_pascal_scene(_geom([w]))
    assert rep["closed_as_axis"] == 1
    node = _walls_of(scene)[0]
    assert node["thickness"] == 0.6                      # 짧은 변이 두께
    assert sorted([node["start"], node["end"]]) == [[0.0, 0.3], [2.0, 0.3]]

    back, _ = PB.from_pascal_scene(scene)
    r = back["elements"]["wall"][0]
    assert r["closed"] is True
    assert sorted(map(tuple, r["points"])) == sorted(map(tuple, ring))


def test_non_rectangular_closed_wall_is_reported_not_dropped():
    """사다리꼴은 Pascal WallNode 로 표현할 방법이 없다. 세어서 보고한다 —
    조용히 빠지면 벽이 사라진 줄 아무도 모른다(실측 지하3층: 38개 중 7개)."""
    w = {"kind": "polyline", "closed": True, "confidence": 1.0, "pairing": "closed",
         "points": [[0.0, 0.0], [2000.0, 0.0], [2000.0, 600.0], [0.0, 700.0]],
         "needs_review": False, "z_base": 0.0, "layer": "상부골조", "eid": "w:t"}
    scene, rep = PB.to_pascal_scene(_geom([w]))
    assert _walls_of(scene) == []
    assert rep["walls_out"] == 0
    assert [u["reason"] for u in rep["unconvertible"]] == ["closed_polygon_not_rectangular"]


def test_multi_segment_wall_splits_and_rejoins():
    """Pascal 벽은 한 구간뿐이라 꺾인 축선은 쪼개진다. 돌아올 때 다시 이어야 한다."""
    w = _wall("w:m", [0.0, 0.0], [1000.0, 0.0])
    w["centerline"] = [[0.0, 0.0], [1000.0, 0.0], [1000.0, 2000.0]]
    scene, rep = PB.to_pascal_scene(_geom([w]))
    assert rep["split_multi_segment"] == 1 and rep["walls_out"] == 2

    back, _ = PB.from_pascal_scene(scene)
    assert len(back["elements"]["wall"]) == 1
    assert back["elements"]["wall"][0]["centerline"] == \
        [[0.0, 0.0], [1000.0, 0.0], [1000.0, 2000.0]]


def test_thickness_edited_in_pascal_comes_back_as_a_declaration():
    """Pascal 에서 두께를 고치면 `overrides` 로 돌아온다 — 적어 준 값이 이긴다는
    `geom_contract.width_of` 규약과 같은 자리."""
    scene, _ = PB.to_pascal_scene(_geom([_wall("w:1", [0.0, 0.0], [3000.0, 0.0])]))
    _walls_of(scene)[0]["thickness"] = 0.45
    back, rep = PB.from_pascal_scene(scene)
    assert rep["edited_thickness"] == 1
    assert back["elements"]["wall"][0]["overrides"]["width"] == 450.0


def test_ids_are_deterministic():
    """같은 입력은 같은 씬이어야 diff 가 의미를 갖는다(Pascal 의 id 는 난수다)."""
    g = _geom([_wall("w:1", [0.0, 0.0], [3000.0, 0.0])])
    a, _ = PB.to_pascal_scene(g)
    b, _ = PB.to_pascal_scene(g)
    assert sorted(a["nodes"]) == sorted(b["nodes"])
    assert all(nid.split("_")[1].isalnum() and len(nid.split("_")[1]) == 16
               for nid in a["nodes"])


def test_sample_drawing_round_trips_without_moving():
    """실제 파싱 결과로 왕복 — 합성 케이스가 못 보는 것(수백 개 좌표, 실측 두께)."""
    import math

    import dxf_parser as dp
    import geom_contract as GC
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dxf = os.path.join(root, "sample_walls.dxf")
    if not os.path.exists(dxf):
        raise __import__("unittest").SkipTest("sample_walls.dxf 없음")

    import contextlib
    import io as _io
    rules = dp.load_layer_map(os.path.join(root, "layer_map.csv"))
    with contextlib.redirect_stdout(_io.StringIO()):
        g = dp.parse(dxf, rules, block_rules=[])
    scene, rep = PB.to_pascal_scene(g)
    back, rep2 = PB.from_pascal_scene(scene)
    assert rep["walls_in"] == len(g["elements"]["wall"])
    assert rep2["walls_out"] == rep["walls_in"] - len(rep["unconvertible"])

    by_eid = {w["eid"]: w for w in back["elements"]["wall"]}
    for w in g["elements"]["wall"]:
        b = by_eid.get(w["eid"])
        if b is None:
            continue
        if w.get("closed"):
            # 닫힌 벽은 축선으로 접었다 펴므로 시작 꼭짓점이 회전할 수 있다.
            # 같은 사각형이면 같은 면이다 — 꼭짓점 집합으로 대조한다.
            a1 = sorted((round(x, 6), round(y, 6)) for x, y in w["points"][:4])
            a2 = sorted((round(x, 6), round(y, 6)) for x, y in b["points"][:4])
            assert GC.signed_area(b["points"]) > 0, w["eid"]     # 감김은 CCW 로
        else:
            a1 = w.get("centerline") or w["points"]
            a2 = b.get("centerline") or b["points"]
        assert len(a1) == len(a2)
        assert max(math.dist(p, q) for p, q in zip(a1, a2)) < 1e-6
        assert abs(w.get("z_base", 0.0) - b["z_base"]) < 1e-9
