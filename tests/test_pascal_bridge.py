# -*- coding: utf-8 -*-
"""geometry.json ↔ Pascal 씬 왕복.

이 다리의 위험은 형상이 아니라 **단위**(mm↔m, 덕트는 인치)와 **축**(Pascal 은
Y-up 이라 평면 y 가 셋째로 간다)과 **고저**(우리는 부재의 z, Pascal 은 레벨 스택)다.
셋 다 틀려도 모델은 열린다 — 1000배 작은 건물도, 90도 돌아간 덕트도, 한 층
내려앉은 벽도. 그래서 좌표로 잰다.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import geom_contract as GC
import pascal_bridge as PB

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _wall(eid, a, b, width=200.0, z=0.0):
    return {"kind": "polyline", "closed": False, "points": [a, b], "centerline": [a, b],
            "width_detected": width, "confidence": 1.0, "pairing": "paired",
            "needs_review": False, "z_base": z, "layer": "A-WALL", "eid": eid,
            "overrides": {"height": 2800.0}}


def _geom(elements, floors=None):
    return {"source": "t.dxf", "units": "mm",
            "params": {"wall": {"width": 200.0, "height": 2800.0},
                       "column": {"width": 400.0, "height": 3000.0},
                       "slab": {"thickness": 200.0}, "zone": {"height": 2800.0}},
            "elements": elements, "floors": floors or [{"z": 0.0, "label": "Level_1"}]}


def _nodes_of(scene, ntype):
    return [n for n in scene["nodes"].values() if n["type"] == ntype]


# ── 벽(0단계에서 만든 것 — 계약이 안 깨졌는지) ─────────────────────────────
def test_millimeters_become_meters_and_come_back():
    """3000mm 벽은 Pascal 에서 3.0 이고, 돌아오면 다시 3000 이다."""
    g = _geom({"wall": [_wall("w:1", [0.0, 0.0], [3000.0, 0.0])]})
    scene, rep = PB.to_pascal_scene(g)
    w = _nodes_of(scene, "wall")[0]
    assert w["start"] == [0.0, 0.0] and w["end"] == [3.0, 0.0]
    assert w["thickness"] == 0.2 and w["height"] == 2.8
    assert rep["counts_out"] == {"wall": 1} and rep["unconvertible"] == []

    back, _ = PB.from_pascal_scene(scene)
    r = back["elements"]["wall"][0]
    assert r["centerline"] == [[0.0, 0.0], [3000.0, 0.0]]
    assert r["eid"] == "w:1" and r["z_base"] == 0.0


def test_scene_hierarchy_is_wired_both_ways():
    """Pascal 은 parentId 와 부모 children 을 **둘 다** 본다 — 한쪽만 채우면 안 열린다."""
    scene, _ = PB.to_pascal_scene(_geom({"wall": [_wall("w:1", [0.0, 0.0], [1000.0, 0.0])]}))
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
    g = _geom({"wall": [_wall("w:1", [0.0, 0.0], [1000.0, 0.0], z=0.0),
                        _wall("w:2", [0.0, 0.0], [1000.0, 0.0], z=4200.0)]},
              floors=[{"z": 0.0, "label": "B1"}, {"z": 4200.0, "label": "1F"}])
    scene, rep = PB.to_pascal_scene(g)
    assert rep["levels"] == 2
    lv = sorted(_nodes_of(scene, "level"), key=lambda n: n["level"])
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
    scene, rep = PB.to_pascal_scene(_geom({"wall": [w]}))
    assert rep["closed_as_axis"] == 1
    node = _nodes_of(scene, "wall")[0]
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
    scene, rep = PB.to_pascal_scene(_geom({"wall": [w]}))
    assert _nodes_of(scene, "wall") == []
    assert rep["counts_out"] == {}
    assert [u["reason"] for u in rep["unconvertible"]] == ["closed_polygon_not_rectangular"]


def test_multi_segment_wall_splits_and_rejoins():
    """Pascal 벽은 한 구간뿐이라 꺾인 축선은 쪼개진다. 돌아올 때 다시 이어야 한다."""
    w = _wall("w:m", [0.0, 0.0], [1000.0, 0.0])
    w["centerline"] = [[0.0, 0.0], [1000.0, 0.0], [1000.0, 2000.0]]
    scene, rep = PB.to_pascal_scene(_geom({"wall": [w]}))
    assert rep["split_multi_segment"] == 1 and rep["counts_out"]["wall"] == 2

    back, _ = PB.from_pascal_scene(scene)
    assert len(back["elements"]["wall"]) == 1
    assert back["elements"]["wall"][0]["centerline"] == \
        [[0.0, 0.0], [1000.0, 0.0], [1000.0, 2000.0]]


def test_thickness_edited_in_pascal_comes_back_as_a_declaration():
    """Pascal 에서 두께를 고치면 `overrides` 로 돌아온다 — 적어 준 값이 이긴다는
    `geom_contract.width_of` 규약과 같은 자리."""
    scene, _ = PB.to_pascal_scene(_geom({"wall": [_wall("w:1", [0.0, 0.0], [3000.0, 0.0])]}))
    _nodes_of(scene, "wall")[0]["thickness"] = 0.45
    back, rep = PB.from_pascal_scene(scene)
    assert rep["edited"] == {"wall_thickness": 1}
    assert back["elements"]["wall"][0]["overrides"]["width"] == 450.0


def test_moving_a_level_in_pascal_is_not_hidden_by_a_stale_override():
    """`geom_contract.base_z` 는 선언(`overrides`)을 먼저 본다. 되돌릴 때 최상위
    `z_base` 만 쓰면 Pascal 에서 층을 옮긴 것이 **조용히 사라진다** — 형상은
    멀쩡하고 벽만 딴 층에 선다. 어느 쪽을 읽든 같은 값이어야 한다."""
    up = _wall("w:1", [0.0, 0.0], [3000.0, 0.0])
    up["overrides"] = {"z_base": 4200.0, "height": 2800.0}
    low = _wall("w:2", [0.0, 0.0], [3000.0, 0.0])
    scene, rep = PB.to_pascal_scene(_geom({"wall": [up, low]}))
    assert rep["levels"] == 2

    lv = sorted(_nodes_of(scene, "level"), key=lambda n: n["level"])
    moved = next(n for n in _nodes_of(scene, "wall")
                 if n["metadata"]["mep"]["eid"] == "w:1")
    lv[1]["children"].remove(moved["id"])          # 사용자가 아래층으로 끌어내렸다
    lv[0]["children"].append(moved["id"])
    moved["parentId"] = lv[0]["id"]

    back, _ = PB.from_pascal_scene(scene)
    r = next(w for w in back["elements"]["wall"] if w["eid"] == "w:1")
    assert r["z_base"] == 0.0
    assert r["overrides"]["z_base"] == 0.0         # 선언도 함께 따라와야 한다
    assert GC.base_z("wall", r) == 0.0


def test_ids_are_deterministic():
    """같은 입력은 같은 씬이어야 diff 가 의미를 갖는다(Pascal 의 id 는 난수다)."""
    g = _geom({"wall": [_wall("w:1", [0.0, 0.0], [3000.0, 0.0])]})
    a, _ = PB.to_pascal_scene(g)
    b, _ = PB.to_pascal_scene(g)
    assert sorted(a["nodes"]) == sorted(b["nodes"])
    assert all(nid.split("_")[1].isalnum() and len(nid.split("_")[1]) == 16
               for nid in a["nodes"])


# ── 1단계: 기둥·슬래브·zone·MEP ───────────────────────────────────────────
def test_column_keeps_its_rectangle_and_its_angle():
    """기둥은 폴리곤이 아니라 중심+치수+회전이다. 30° 돌아간 기둥으로 회전 규약을
    고정한다 — 축에 나란한 기둥만 시험하면 부호가 틀려도 안 걸린다."""
    cx, cy, rot = 1000.0, 2000.0, math.radians(30)
    c, s = math.cos(rot), math.sin(rot)
    ring = [[cx + lx * c - ly * s, cy + lx * s + ly * c]
            for lx, ly in ((-300.0, -200.0), (300.0, -200.0), (300.0, 200.0), (-300.0, 200.0))]
    rec = {"kind": "polyline", "closed": True, "points": ring, "z_base": 0.0,
           "layer": "A-CON", "eid": "c:1", "overrides": {"height": 3000.0}}
    scene, rep = PB.to_pascal_scene(_geom({"column": [rec]}))
    node = _nodes_of(scene, "column")[0]
    assert rep["counts_out"] == {"column": 1}
    assert abs(node["rotation"] - rot) < 1e-9
    assert node["crossSection"] == "rectangular"
    assert (round(node["width"], 9), round(node["depth"], 9)) == (0.6, 0.4)
    assert [round(v, 9) for v in node["position"]] == [1.0, 0.0, 2.0]   # [x, 높이, 평면 y]
    assert node["height"] == 3.0

    back, _ = PB.from_pascal_scene(scene)
    r = back["elements"]["column"][0]
    got = sorted((round(p[0], 6), round(p[1], 6)) for p in r["points"])
    want = sorted((round(p[0], 6), round(p[1], 6)) for p in ring)
    assert max(math.dist(a, b) for a, b in zip(got, want)) < 1e-6


def test_round_column_stays_round():
    """원형 기둥은 `crossSection: round` + radius 다 — 실무 도면(지하3층)에는 하나도
    없어서 실측만으로는 이 갈래가 안 돌아 본다."""
    rec = {"kind": "circle", "center": [1000.0, 2000.0], "radius": 250.0,
           "z_base": 0.0, "eid": "c:r", "overrides": {"height": 3000.0}}
    scene, _ = PB.to_pascal_scene(_geom({"column": [rec]}))
    n = _nodes_of(scene, "column")[0]
    assert n["crossSection"] == "round" and n["radius"] == 0.25
    assert n["position"] == [1.0, 0.0, 2.0]

    back, _ = PB.from_pascal_scene(scene)
    r = back["elements"]["column"][0]
    assert r["kind"] == "circle" and r["center"] == [1000.0, 2000.0] and r["radius"] == 250.0


def test_column_ornament_is_stripped():
    """ColumnNode 기본값은 **장식용**이다(주춧돌·기둥머리·목이 잘록한 샤프트).
    그대로 두면 구조 기둥 자리에 그리스 신전이 선다."""
    rec = {"kind": "polyline", "closed": True, "z_base": 0.0, "eid": "c:1",
           "points": [[0.0, 0.0], [600.0, 0.0], [600.0, 400.0], [0.0, 400.0]]}
    scene, _ = PB.to_pascal_scene(_geom({"column": [rec]}))
    n = _nodes_of(scene, "column")[0]
    assert n["style"] == "plain" and n["shaftProfile"] == "straight"
    assert n["baseStyle"] == "none" and n["capitalStyle"] == "none"
    assert n["baseHeight"] == 0 and n["capitalHeight"] == 0
    assert n["shaftStartScale"] == 1 and n["shaftEndScale"] == 1


def test_slab_elevation_is_the_top_face():
    """슬래브의 z 는 **상단**이고 두께는 아래로 자란다 — 우리 규약과 Pascal 이 같다.
    하단으로 읽으면 한 두께만큼 뜬다(preview 가 실제로 그랬다)."""
    rec = {"kind": "polyline", "closed": True, "z_base": 4200.0, "eid": "s:1",
           "points": [[0.0, 0.0], [5000.0, 0.0], [5000.0, 4000.0], [0.0, 4000.0]],
           "overrides": {"thickness": 210.0}}
    scene, _ = PB.to_pascal_scene(_geom({"slab": [rec]}))
    n = _nodes_of(scene, "slab")[0]
    assert n["elevation"] == 0.0 and n["thickness"] == 0.21   # 레벨면(=4200)이 상단
    assert n["polygon"][1] == [5.0, 0.0]

    back, _ = PB.from_pascal_scene(scene)
    r = back["elements"]["slab"][0]
    assert r["z_base"] == 4200.0
    assert GC.thickness_of(r, {}, "slab") == 210.0


def test_duct_is_inches_and_the_plan_y_goes_third():
    """Pascal 은 Y-up 이다 — path 의 둘째가 높이, 셋째가 평면 y. 뒤바꾸면 덕트가
    바닥에 눕는다. 단면은 **인치**다(200mm = 7.874in)."""
    rec = {"kind": "polyline", "closed": False, "eid": "d:1", "layer": "SA",
           "points": [[0.0, 0.0], [3000.0, 0.0], [3000.0, 2000.0]],
           "elevation": 2590.0, "width_mm": 200.0, "height_mm": 200.0}
    scene, rep = PB.to_pascal_scene(_geom({"duct": [rec]}))
    n = _nodes_of(scene, "duct-segment")[0]
    assert rep["counts_out"] == {"duct": 1}
    assert n["path"] == [[0.0, 2.59, 0.0], [3.0, 2.59, 0.0], [3.0, 2.59, 2.0]]
    assert n["shape"] == "rect"
    assert abs(n["width"] - 200.0 / 25.4) < 1e-9

    back, _ = PB.from_pascal_scene(scene)
    r = back["elements"]["duct"][0]
    assert r["points"] == [[0.0, 0.0], [3000.0, 0.0], [3000.0, 2000.0]]
    assert r["elevation"] == 2590.0
    assert abs(r["width_mm"] - 200.0) < 1e-9


def test_mep_elevation_is_a_height_not_a_level():
    """덕트의 elevation 2590mm 는 2.59m 짜리 **층**이 아니라 바닥 위 높이다.
    레벨로 세면 건물에 없는 층이 생기고 벽이 그리로 딸려 간다."""
    g = _geom({"wall": [_wall("w:1", [0.0, 0.0], [3000.0, 0.0])],
               "duct": [{"kind": "polyline", "points": [[0.0, 0.0], [3000.0, 0.0]],
                         "elevation": 2590.0, "width_mm": 200.0, "height_mm": 200.0,
                         "eid": "d:1"}]})
    scene, rep = PB.to_pascal_scene(g)
    assert rep["levels"] == 1
    assert _nodes_of(scene, "duct-segment")[0]["path"][0][1] == 2.59


def test_a_riser_keeps_its_heights():
    """수직·경사 구간은 점마다 높이가 다르다. `elevation` 한 값으로 담으면 입상관이
    천장에 눕는다 — `path3d`(계약 v3)가 있으면 점마다 싣고 그대로 되돌린다.
    (오늘의 `mep_paths.extract_curve` 는 비평면 경로를 평탄화하는 대신 **거부**하므로
     이 갈래는 계약이 확장될 때를 위한 것이다.)"""
    riser = {"kind": "polyline", "points": [[0.0, 0.0], [3000.0, 0.0], [3000.0, 0.0]],
             "eid": "p:r", "elevation": 2600.0, "diameter": 100.0,
             "path3d": [[0.0, 0.0, 2600.0], [3000.0, 0.0, 2600.0], [3000.0, 0.0, 500.0]]}
    scene, rep = PB.to_pascal_scene(_geom({"pipe": [riser]}))
    node = _nodes_of(scene, "pipe-segment")[0]
    assert [round(p[1], 6) for p in node["path"]] == [2.6, 2.6, 0.5]
    assert rep["path3d"] == 1

    back, _ = PB.from_pascal_scene(scene)
    r = back["elements"]["pipe"][0]
    assert r["path3d"] == riser["path3d"] and r["elevation"] == 2600.0


def test_a_flat_run_does_not_grow_a_path3d():
    """평면 경로에 3D 키를 만들어 붙이면 계약이 없는 필드가 데이터에 번진다."""
    flat = {"kind": "polyline", "points": [[0.0, 0.0], [3000.0, 0.0]], "eid": "p:f",
            "elevation": 2600.0, "diameter": 100.0}
    scene, rep = PB.to_pascal_scene(_geom({"pipe": [flat]}))
    back, _ = PB.from_pascal_scene(scene)
    assert "path3d" not in back["elements"]["pipe"][0]
    assert rep.get("path3d") is None


def test_pipe_diameter_is_inches():
    rec = {"kind": "polyline", "points": [[0.0, 0.0], [2000.0, 0.0]],
           "elevation": 2600.0, "diameter": 100.0, "eid": "p:1"}
    scene, _ = PB.to_pascal_scene(_geom({"pipe": [rec]}))
    n = _nodes_of(scene, "pipe-segment")[0]
    assert abs(n["diameter"] - 100.0 / 25.4) < 1e-9
    back, _ = PB.from_pascal_scene(scene)
    assert abs(back["elements"]["pipe"][0]["diameter"] - 100.0) < 1e-9


def test_mep_dimensions_come_from_the_contract_not_from_here():
    """단면 치수 규약은 `geom_contract` 것이다 — GUI 별칭 키(`width`/`height`)와
    '치수 미해소' 판정이 거기 있다. 여기서 `width_mm` 만 읽으면 별칭만 있는 덕트가
    **조용히 기본값 400mm** 로 나간다(형상은 멀쩡해서 아무 검사에도 안 걸린다)."""
    alias = {"kind": "polyline", "points": [[0.0, 0.0], [3000.0, 0.0]], "eid": "d:a",
             "elevation": 2590.0, "width": 500.0, "height": 300.0}
    scene, _ = PB.to_pascal_scene(_geom({"duct": [alias]}))
    n = _nodes_of(scene, "duct-segment")[0]
    assert abs(GC.in_to_mm(n["width"]) - 500.0) < 1e-9
    assert abs(GC.in_to_mm(n["height"]) - 300.0) < 1e-9


def test_mep_with_unresolved_dimensions_is_reported():
    """치수를 모르는 레코드에 기본값을 씌워 내보내지 않는다 — 물량이 조용히 틀린다."""
    if not hasattr(GC, "mep_dimensions"):
        raise __import__("unittest").SkipTest("geom_contract.mep_dimensions 없음")
    unk = {"kind": "polyline", "points": [[0.0, 0.0], [3000.0, 0.0]], "eid": "d:u",
           "elevation": 2590.0, "dimension_status": "unknown"}
    scene, rep = PB.to_pascal_scene(_geom({"duct": [unk]}))
    assert _nodes_of(scene, "duct-segment") == []
    assert rep["unconvertible"][0]["reason"] == "mep_dimensions_unresolved"


def test_zone_becomes_a_room_polygon():
    rec = {"kind": "polyline", "closed": True, "z_base": 0.0, "eid": "z:1",
           "layer": "A-ZONE",
           "points": [[0.0, 0.0], [4000.0, 0.0], [4000.0, 3000.0], [0.0, 3000.0]]}
    scene, _ = PB.to_pascal_scene(_geom({"zone": [rec]}))
    n = _nodes_of(scene, "zone")[0]
    assert n["name"] == "A-ZONE" and n["ceilingHeight"] == 2.8
    back, _ = PB.from_pascal_scene(scene)
    assert back["elements"]["zone"][0]["points"][2] == [4000.0, 3000.0]


def _opening(eid, center, width=900.0, height=1200.0, sill=900.0, hosts=(0,), sub=None):
    return {"kind": "circle", "center": list(center), "radius": width / 2.0,
            "z_base": 0.0, "eid": eid, "wall_indices": list(hosts), "subtype": sub,
            "width": width, "height": height, "sill": sill, "layer": "A-DOOR"}


def test_opening_lands_on_its_wall_in_wall_local_coordinates():
    """Pascal 의 문·창은 **벽의 자식**이고 위치가 벽 로컬이다 —
    [시작점부터의 거리, 바닥 위 중심 높이, 벽 중심면에서의 오프셋]."""
    w = _wall("w:1", [0.0, 0.0], [4000.0, 0.0])
    g = _geom({"wall": [w], "opening": [_opening("o:1", (1500.0, 0.0))]})
    scene, rep = PB.to_pascal_scene(g)
    assert rep["counts_out"]["opening"] == 1
    node = _nodes_of(scene, "window")[0]
    wall = _nodes_of(scene, "wall")[0]
    assert node["parentId"] == wall["id"] and node["wallId"] == wall["id"]
    assert node["id"] in wall["children"]          # parentId 와 children 양쪽
    assert node["position"] == [1.5, 1.5, 0.0]     # 1500mm 지점, 중심높이 900+1200/2
    assert node["width"] == 0.9 and node["height"] == 1.2

    back, _ = PB.from_pascal_scene(scene)
    r = back["elements"]["opening"][0]
    assert r["center"] == [1500.0, 0.0] and r["sill"] == 900.0
    assert r["wall_indices"] == [0] and r["radius"] == 450.0


def test_opening_without_a_subtype_is_a_frameless_cutout():
    """문인지 창인지 모르면 **추측하지 않는다.** Pascal 의 `openingKind:'opening'`
    이 정확히 '틀 없는 구멍' 이다(실측 지하3층: 개구부 34개 전부 subtype 없음)."""
    g = _geom({"wall": [_wall("w:1", [0.0, 0.0], [4000.0, 0.0])],
               "opening": [_opening("o:1", (1500.0, 0.0)),
                           _opening("o:2", (2500.0, 0.0), sub="door", sill=0.0),
                           _opening("o:3", (3500.0, 0.0), sub="window")]})
    scene, _ = PB.to_pascal_scene(g)
    kinds = {n["metadata"]["mep"]["eid"]: (n["type"], n.get("openingKind"))
             for n in scene["nodes"].values() if n["type"] in ("door", "window")}
    assert kinds["o:1"] == ("window", "opening")
    assert kinds["o:2"] == ("door", None)
    assert kinds["o:3"] == ("window", "window")

    back, _ = PB.from_pascal_scene(scene)
    got = {o["eid"]: o["subtype"] for o in back["elements"]["opening"]}
    assert got == {"o:1": None, "o:2": "door", "o:3": "window"}


def test_opening_keeps_its_offset_and_its_overshoot():
    """개구부 중심은 벽 축선 위에 있지 않고(실측 중앙 100mm 벗어남), 실무 도면은
    문을 벽 마구리 **밖**에 걸쳐 그린다(실측 29개 중 11개, 최대 610mm).
    둘 다 버리면 그만큼 조용히 옮겨진다."""
    w = _wall("w:1", [0.0, 0.0], [2000.0, 0.0])
    off = _opening("o:off", (1000.0, 125.0))          # 축선에서 125mm 옆
    past = _opening("o:past", (2400.0, 0.0))          # 벽 끝에서 400mm 밖
    scene, rep = PB.to_pascal_scene(_geom({"wall": [w], "opening": [off, past]}))
    assert rep["opening_past_wall_end"] == 1
    pos = {n["metadata"]["mep"]["eid"]: n["position"] for n in _nodes_of(scene, "window")}
    assert pos["o:off"][2] == 0.125                   # 셋째 성분 = 중심면 오프셋
    assert pos["o:past"][0] == 2.4                    # 구간 밖이라도 자르지 않는다

    back, _ = PB.from_pascal_scene(scene)
    got = {o["eid"]: o["center"] for o in back["elements"]["opening"]}
    assert got["o:off"] == [1000.0, 125.0] and got["o:past"] == [2400.0, 0.0]


def test_opening_with_no_host_wall_is_reported():
    """붙을 벽이 없으면 벽 자식이 될 수 없다 — 세어서 보고한다
    (실측 지하3층 5개는 제도자가 문 자리에서 벽을 끊어 그린 경우)."""
    op = _opening("o:1", (1500.0, 0.0), hosts=())
    op["no_host_reason"] = "wall_open_at_this_span"
    scene, rep = PB.to_pascal_scene(_geom({"wall": [_wall("w:1", [0.0, 0.0], [4000.0, 0.0])],
                                           "opening": [op]}))
    assert _nodes_of(scene, "window") == []
    u = rep["unconvertible"][0]
    assert u["reason"] == "no_host_wall" and u["detail"] == "wall_open_at_this_span"


def test_categories_pascal_cannot_hold_are_counted():
    """보·케이블트레이는 Pascal 에 노드 자체가 없고(그쪽 IFC 임포터도 보를 건너뛴다),
    장비는 0.3~2m 짜리 HVAC 캐비닛뿐이며, 개구부는 벽 로컬 좌표가 필요하다.
    넷 다 **세어서** 보고한다 — 조용히 빠지면 물량이 말없이 줄어든다."""
    g = _geom({"beam": [{"kind": "polyline", "points": [[0, 0], [1000, 0]], "eid": "b:1"}],
               "tray": [{"kind": "polyline", "points": [[0, 0], [1000, 0]],
                         "elevation": 3000.0, "eid": "t:1"}],
               "equipment": [{"kind": "polyline", "closed": True, "eid": "e:1",
                              "points": [[0, 0], [800, 0], [800, 800], [0, 800]]}],
               "opening": [{"kind": "circle", "center": [500, 0], "radius": 450.0,
                            "eid": "o:1", "wall_indices": []}]})
    scene, rep = PB.to_pascal_scene(g)
    assert rep["counts_out"] == {}
    assert {u["category"]: u["reason"] for u in rep["unconvertible"]} == {
        "beam": "no_beam_node_in_pascal",
        "tray": "no_cable_tray_node_in_pascal",
        "equipment": "hvac_cabinet_only",
        "opening": "no_host_wall"}


def test_a_duct_outside_pascals_range_is_never_clamped_into_a_native_node():
    """범위를 넘긴 값을 **깎아서** 내보내면 Pascal 에는 다른 크기의 덕트가 선다.
    native 노드로는 내보내지 않고(그러면 zod 가 거부해 씬에서 사라진다) 치수를
    그대로 실은 foreign 노드로 보낸다."""
    rec = {"kind": "polyline", "points": [[0.0, 0.0], [1000.0, 0.0]], "eid": "d:1",
           "elevation": 2590.0, "width_mm": 100.0, "height_mm": 150.0}
    scene, rep = PB.to_pascal_scene(_geom({"duct": [rec]}))
    assert _nodes_of(scene, "duct-segment") == []          # 깎아서 native 로 보내지 않는다
    node = _nodes_of(scene, PB.FOREIGN_PREFIX + "duct")[0]
    assert node["section"]["width_mm"] == 100.0            # 하한 101.6mm 로 올리지 않는다
    assert node["outOfPascalRange"]["field"] == "width"
    assert rep["foreign"] == {"duct": 1}


def test_a_column_outside_pascals_range_is_still_unconvertible():
    """구조 노드에는 foreign 대안을 두지 않는다 — zod 가 거부하면 그대로 보고한다."""
    rec = {"kind": "polyline", "closed": True, "z_base": 0.0, "eid": "c:0",
           "points": [[0.0, 0.0], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]],
           "overrides": {"height": 0.0}}
    scene, rep = PB.to_pascal_scene(_geom({"column": [rec]}))
    assert _nodes_of(scene, "column") == []
    assert rep["unconvertible"][0]["category"] == "column"


# ── 실제 파싱 결과 ────────────────────────────────────────────────────────
def _axis(rec, cat):
    if rec.get("kind") == "circle":
        return [tuple(rec["center"])]
    if rec.get("closed") or cat in ("column", "slab", "zone"):
        # 닫힌 형상은 축선/상자로 접었다 펴므로 시작 꼭짓점이 회전할 수 있다.
        return sorted((round(p[0], 6), round(p[1], 6)) for p in rec["points"])
    return [tuple(p) for p in (rec.get("centerline") or rec["points"])]


def test_deleting_a_middle_segment_does_not_invent_a_diagonal():
    """Pascal 에서 꺾인 벽의 가운데 구간을 지우면 남은 조각은 **떨어져 있다.**
    그대로 이으면 도면에 없던 대각선 벽이 생긴다. 끊긴 자리에서 나누고,
    갈라져 나온 조각은 저장소 규약대로 수동(`wm:`) 레코드가 된다."""
    w = _wall("w:1", [0.0, 0.0], [3000.0, 0.0])
    w["centerline"] = [[0.0, 0.0], [3000.0, 0.0], [3000.0, 3000.0], [6000.0, 3000.0]]
    scene, _ = PB.to_pascal_scene(_geom({"wall": [w]}))
    segs = sorted(_nodes_of(scene, "wall"), key=lambda n: n["metadata"]["mep"]["seg"])
    mid = segs[1]
    scene["nodes"][mid["parentId"]]["children"].remove(mid["id"])
    del scene["nodes"][mid["id"]]

    back, rep = PB.from_pascal_scene(scene)
    axes = sorted((r["centerline"] for r in back["elements"]["wall"]), key=lambda a: a[0])
    assert axes == [[[0.0, 0.0], [3000.0, 0.0]], [[3000.0, 3000.0], [6000.0, 3000.0]]]
    assert rep["wall_split_by_deletion"] == 1
    eids = {r["eid"] for r in back["elements"]["wall"]}
    assert "w:1" in eids and any(e.startswith("wm:") for e in eids)   # EID 중복 없이


def test_opening_on_a_later_wall_segment_survives():
    """개구부는 자기가 붙은 **구간**의 축선으로 위치를 되살려야 한다. 대표 조각만
    보면 다구간 벽의 둘째 구간에 붙은 개구부가 호스트를 못 찾고 사라진다."""
    w = _wall("w:1", [0.0, 0.0], [3000.0, 0.0])
    w["centerline"] = [[0.0, 0.0], [3000.0, 0.0], [3000.0, 3000.0]]
    op = _opening("o:1", (3000.0, 1500.0))
    scene, rep = PB.to_pascal_scene(_geom({"wall": [w], "opening": [op]}))
    assert rep["counts_out"]["opening"] == 1

    back, _ = PB.from_pascal_scene(scene)
    got = back["elements"]["opening"][0]
    assert math.dist(got["center"], [3000.0, 1500.0]) < 1e-6


def test_opening_whose_host_wall_vanished_is_counted():
    """호스트가 사라지면 개구부를 만들 수 없다 — 그래도 **조용히 버리지 않는다.**"""
    scene, _ = PB.to_pascal_scene(_geom({"wall": [_wall("w:1", [0.0, 0.0], [4000.0, 0.0])],
                                         "opening": [_opening("o:1", (1500.0, 0.0))]}))
    wall = _nodes_of(scene, "wall")[0]
    del scene["nodes"][wall["id"]]
    back, rep = PB.from_pascal_scene(scene)
    assert back["elements"].get("opening", []) == []
    assert rep["dropped"]["opening_host_missing"] == 1


def test_mep_section_edited_in_pascal_beats_the_old_declaration():
    """`mep_dimensions` 는 `overrides` 를 먼저 본다. 최상위만 갱신하면 Pascal 에서
    100→150 으로 키운 것이 조용히 100 으로 남는다 — 별칭 키 전부에 적용해야 한다."""
    for alias in ("diameter", "width", "outside_diameter_mm"):
        rec = {"kind": "polyline", "points": [[0.0, 0.0], [3000.0, 0.0]], "eid": "p:1",
               "elevation": 2600.0, "diameter": 100.0, "overrides": {alias: 100.0}}
        scene, _ = PB.to_pascal_scene(_geom({"pipe": [rec]}))
        _nodes_of(scene, "pipe-segment")[0]["diameter"] = GC.mm_to_in(150.0)
        back, _ = PB.from_pascal_scene(scene)
        r = back["elements"]["pipe"][0]
        assert GC.mep_dimensions("pipe", r, {})["diameter"] == 150.0, alias


def test_real_mep_sizes_survive_as_foreign_nodes():
    """Pascal 의 기본 배관·덕트는 미국 주택 규격이라(배관 1.25~8인치, 덕트 높이
    3인치 하한) **우리 PB 15.9mm 와 높이 54mm 덕트가 하나도 안 들어간다.**
    버리는 대신 씬 스키마가 통째로 보존하는 foreign 노드로 내보낸다 — 치수는
    범위에 맞춰 깎지 않고 **mm 그대로**."""
    pb = {"kind": "polyline", "points": [[0.0, 0.0], [3000.0, 0.0]], "eid": "p:pb",
          "elevation": 77.95, "diameter": 15.9, "nominal_size": "15A",
          "material": "PB", "system": "공급", "source_length_mm": 3000.0,
          "length_basis": "analytic"}
    duct = {"kind": "polyline", "points": [[0.0, 0.0], [3000.0, 0.0]], "eid": "d:1",
            "elevation": 2590.0, "width_mm": 110.0, "height_mm": 54.0, "system": "SA"}
    scene, rep = PB.to_pascal_scene(_geom({"pipe": [pb], "duct": [duct]}))
    assert rep["counts_out"] == {"pipe": 1, "duct": 1}
    assert rep["foreign"] == {"pipe": 1, "duct": 1} and rep["unconvertible"] == []
    node = next(n for n in scene["nodes"].values() if n["type"] == PB.FOREIGN_PREFIX + "pipe")
    assert node["section"]["diameter"] == 15.9 and node["section"]["units"] == "mm"
    assert node["nativeNodeType"] == "pipe-segment"

    back, _ = PB.from_pascal_scene(scene)
    r = back["elements"]["pipe"][0]
    assert GC.mep_dimensions("pipe", r, {})["diameter"] == 15.9
    assert (r["material"], r["nominal_size"], r["system"]) == ("PB", "15A", "공급")
    assert r["source_length_mm"] == 3000.0 and r["length_basis"] == "analytic"
    d = back["elements"]["duct"][0]
    assert (d["width_mm"], d["height_mm"]) == (110.0, 54.0)


def test_sample_drawings_round_trip_without_moving():
    """합성 케이스가 못 보는 것 — 수백 개 좌표, 실측 두께, 카테고리 혼재."""
    import contextlib
    import io as _io

    import dxf_parser as dp
    rules = dp.load_layer_map(os.path.join(ROOT, "layer_map.csv"))
    seen = set()
    for fn in ("sample_plan.dxf", "sample_walls.dxf", "sample_mep.dxf"):
        path = os.path.join(ROOT, fn)
        if not os.path.exists(path):
            continue
        with contextlib.redirect_stdout(_io.StringIO()):
            g = dp.parse(path, rules, block_rules=[])
        scene, rep = PB.to_pascal_scene(g)
        back, _ = PB.from_pascal_scene(scene)
        seen.update(rep["counts_out"])
        for cat, recs in g["elements"].items():
            by_eid = {r.get("eid"): r for r in back["elements"].get(cat) or []}
            # ★ 되돌아오지 않은 레코드를 **건너뛰지 않는다.** 종전엔 `continue` 라
            #   조용히 사라진 부재가 검사를 그냥 통과했다(실측: 다구간 벽 둘째
            #   구간의 개구부가 항상 사라지고 있었는데 아무 검사도 안 걸렸다).
            reported = {u.get("eid") for u in rep["unconvertible"]}
            for r in recs:
                if r.get("eid") not in by_eid:
                    assert r.get("eid") in reported, (fn, cat, r.get("eid"), "말없이 사라짐")
                    continue
                b = by_eid[r.get("eid")]
                a1, a2 = _axis(r, cat), _axis(b, cat)
                assert len(a1) == len(a2), (fn, cat, r.get("eid"))
                assert max(math.dist(p, q) for p, q in zip(a1, a2)) < 1e-6, (fn, cat)
                if "z_base" in r:
                    assert abs(r["z_base"] - b["z_base"]) < 1e-9, (fn, cat)
                if "elevation" in r and cat in PB.MEP_CATS:
                    assert abs(r["elevation"] - b["elevation"]) < 1e-9, (fn, cat)
    assert {"wall", "column", "slab"} <= seen, seen
