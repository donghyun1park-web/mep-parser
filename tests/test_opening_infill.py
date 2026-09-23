# -*- coding: utf-8 -*-
"""창·문 자리에서 끊겨 그린 벽의 **창 아래 벽**과 **창·문 위 벽(인방)**을 채운다.

평면도는 창 높이에서 자른 단면이라 창 자리에 벽 선이 없다. 실측(단위세대 평면도): 창·문 15곳이 전부 벽이
끊긴 자리였고 3D 외곽이 바닥부터 천장까지 뚫려 보였다. 채운 벽은 **층 바닥보다 떠서 시작**한다(인방 z_base 2100) —
벽 바닥 높이로 층을 고르던 곳(V001 · 빌더 · Pascal 레벨 · IFC 층 재검사)이 가짜 층을 만들거나 빌드를 막지 않게
`floor_z` 로 층을 판정한다.
"""
import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ezdxf
import dxf_parser as P
import geom_contract as GC
import verify

ROOT = Path(__file__).resolve().parents[1]


def _plan(tmp_path, continuous=False):
    """벽 두 장(A-WALL, 200mm)과 끊김 두 곳: 창 W-1200 (x 3000~4200), 문 D-900 (x 6000~6900).
    블록 기준점은 실무 도면처럼 **창·문의 끝**이다(로컬 X -폭~0). continuous 면 창 자리 벽이 이어진다."""
    doc = ezdxf.new(units=4)
    w = doc.blocks.new("W-1200")
    for y in (-100, 0, 100):
        w.add_line((-1200, y), (0, y))
    d = doc.blocks.new("D-900")
    d.add_line((-900, 0), (0, 0))
    d.add_line((-900, 0), (-900, -900))
    ms = doc.modelspace()
    spans = [(0, 9000)] if continuous else [(0, 3000), (4200, 6000), (6900, 9000)]
    for x0, x1 in spans:
        for y in (0, 200):
            ms.add_line((x0, y), (x1, y), dxfattribs={"layer": "A-WALL"})
    ms.add_blockref("W-1200", (4200, 100), dxfattribs={"layer": "A-WIN"})
    if not continuous:
        ms.add_blockref("D-900", (6900, 100), dxfattribs={"layer": "A-DOOR"})
    p = tmp_path / ("cont.dxf" if continuous else "plan.dxf")
    doc.saveas(p)
    return str(p)


def _parse(path, **kw):
    with contextlib.redirect_stdout(io.StringIO()):
        return P.parse(path, P.load_layer_map(str(ROOT / "layer_map.csv")),
                       P.load_layer_map(str(ROOT / "block_map.csv")), **kw)


def _infill(g):
    return {(w["infill_of"], w["infill_part"]): w for w in g["elements"]["wall"] if w.get("source") == "opening_infill"}


def _span(w):
    (x1, y1), (x2, y2) = w["centerline"]
    return sorted((x1, x2)), (y1, y2)


def test_block_openings_sit_at_the_gap_center_not_at_the_insertion_end(tmp_path):
    g = _parse(_plan(tmp_path))
    ops = {o["mark"]: o for o in g["elements"]["opening"]}
    assert ops["W-1200"]["center"] == [3600.0, 100.0] and ops["W-1200"]["anchor"] == [4200.0, 100.0]
    assert ops["D-900"]["center"] == [6450.0, 100.0]
    # EID 는 삽입점 기준 그대로 — 중심을 옮겨도 저장된 수정이 이어진다
    import element_id as E
    assert ops["W-1200"]["source_signatures"] == [E.raw_entity_sig({"kind": "circle", "center": [4200.0, 100.0], "radius": 600.0})]


def test_window_gets_a_sill_wall_and_a_lintel_and_a_door_gets_a_lintel(tmp_path):
    g = _parse(_plan(tmp_path))
    ops = {o["mark"]: o for o in g["elements"]["opening"]}
    inf = _infill(g)
    win, door = ops["W-1200"]["eid"], ops["D-900"]["eid"]
    assert set(inf) == {(win, "below"), (win, "above"), (door, "above")}
    below, above, lintel = inf[(win, "below")], inf[(win, "above")], inf[(door, "above")]
    assert GC.z_range("wall", below, g["params"]) == (0.0, 900.0)
    assert GC.z_range("wall", above, g["params"]) == (2100.0, 2800.0)
    assert GC.z_range("wall", lintel, g["params"]) == (2100.0, 2800.0)
    assert _span(below) == ([3000.0, 4200.0], (100.0, 100.0)) and _span(lintel)[0] == [6000.0, 6900.0]
    assert all(w["floor_z"] == 0.0 and w["width_detected"] == 200.0 and w["overrides"]["height"] > 0 and "height" not in w
               for w in inf.values())
    # 평면도에 없는 창호 치수로 정한 높이다 — 그렇게 남는다
    assert below["dims_assumed"] == ["height"] and above["dims_assumed"] == ["z_base", "height"]
    assert g["opening_infill"] == {"walls": 3, "openings": 2, "assumed": 3, "skipped": {}}
    assert ops["W-1200"]["host_dir"] == [1.0, 0.0]     # 옆 벽 방향 — 상자·문짝이 X 축 가정으로 그려지지 않게
    assert len({w["eid"] for w in inf.values()}) == 3
    assert sorted(w["eid"] for w in _infill(_parse(_plan(tmp_path))).values()) == sorted(w["eid"] for w in inf.values())


def test_raised_walls_do_not_make_floors_and_pass_the_floor_gate(tmp_path):
    g = _parse(_plan(tmp_path))
    assert [f["z"] for f in g["floors"]] == [0.0]
    ids = {f.id for f in verify.verify_geometry(g).findings}
    assert "V001" not in ids and "V002" not in ids


def test_a_continuous_wall_is_cut_by_the_builder_and_needs_no_infill(tmp_path):
    g = _parse(_plan(tmp_path, continuous=True))
    assert _infill(g) == {} and "opening_infill" not in g
    assert g["elements"]["opening"][0]["wall_indices"]


def test_schedule_dimensions_drive_the_infill_and_clear_the_assumption(tmp_path):
    sched = [{"mark": "W-1200", "subtype": "window", "width": 1200.0, "height": 2000.0, "sill": 300.0, "count": 1},
             {"mark": "D-900", "subtype": "door", "width": 900.0, "height": 2400.0, "sill": 0.0, "count": 1}]
    g = _parse(_plan(tmp_path), ext_schedule=sched)
    by = {(w["infill_part"], round(w["centerline"][0][0])): w for w in _infill(g).values()}
    assert GC.z_range("wall", by[("below", 3000)], g["params"]) == (0.0, 300.0)
    assert GC.z_range("wall", by[("above", 3000)], g["params"]) == (2300.0, 2800.0)
    assert GC.z_range("wall", by[("above", 6000)], g["params"]) == (2400.0, 2800.0)
    assert not any(w.get("dims_assumed") for w in by.values()) and g["opening_infill"]["assumed"] == 0


def test_edits_on_infill_walls_survive_a_reparse_and_are_not_orphans(tmp_path):
    path = _plan(tmp_path)
    g0 = _parse(path)
    ops = {o["mark"]: o["eid"] for o in g0["elements"]["opening"]}
    inf0 = _infill(g0)
    above, below = inf0[(ops["W-1200"], "above")]["eid"], inf0[(ops["W-1200"], "below")]["eid"]
    g = _parse(path, edits={above: {"overrides": {"height": 400.0}}, below: {"deleted": True}})
    inf = _infill(g)
    assert (ops["W-1200"], "below") not in inf
    assert GC.z_range("wall", inf[(ops["W-1200"], "above")], g["params"]) == (2100.0, 2500.0)
    rep = g["edits_report"]
    assert rep["orphaned"] == [] and {above, below} <= set(rep["applied"])
    assert below not in rep["current_eids"]


def test_stacked_levels_move_the_floor_anchor_and_prefix_the_opening_link(tmp_path):
    import stack_build as SB
    with contextlib.redirect_stdout(io.StringIO()):
        g = SB.build_stack({"levels": [{"id": "L2", "source": _plan(tmp_path), "z": 3000, "height": 3200}]})
    inf = [w for w in g["elements"]["wall"] if w.get("source") == "opening_infill"]
    opening_eids = {o["eid"] for o in g["elements"]["opening"]}
    assert inf and all(w["floor_z"] == 3000.0 and w["infill_of"] in opening_eids for w in inf)
    lintel = next(w for w in inf if w["infill_part"] == "above" and w["centerline"][0][0] == 6000.0)
    assert GC.z_range("wall", lintel, g["params"]) == (5100.0, 6200.0)     # 층고 3200 의 윗면까지
    assert "V001" not in {f.id for f in verify.verify_geometry(g).findings}


def test_pascal_keeps_one_level_reports_raised_walls_and_an_untouched_scene_saves_nothing(tmp_path):
    import pascal_bridge as PB
    g = _parse(_plan(tmp_path))
    scene, rep = PB.to_pascal_scene(g)
    levels = [n for n in scene["nodes"].values() if n.get("type") == "level"]
    assert len(levels) == 1
    raised = [u for u in rep["unconvertible"] if u["reason"] == "wall_base_above_level"]
    assert len(raised) == 2                   # 인방 둘. 창 아래 벽은 바닥에서 서므로 그대로 나간다
    edits, _ = PB.scene_to_edits(g, scene)
    assert edits == {}


def test_a_pipe_just_above_a_lintel_bottom_is_a_wall_penetration_not_under_a_wall():
    import clash_review as CR
    lintel = {"z_base": 2100.0, "floor_z": 0.0, "overrides": {"height": 700.0}}
    prism = {"category": "wall", "rec": lintel, "z": (2100.0, 2800.0), "width": 200.0}
    assert CR._kind(prism, 2150.0) == "wall_penetration"
    floor_wall = {"z_base": 0.0, "overrides": {"height": 2800.0}}
    assert CR._kind(dict(prism, rec=floor_wall, z=(0.0, 2800.0)), 50.0) == "under_wall"


def test_quantities_use_the_infill_height_not_the_storey_height(tmp_path):
    import boq_export as BQ
    g = _parse(_plan(tmp_path))
    total = BQ.aggregate(g)
    full = _parse(_plan(tmp_path, continuous=True))
    # 끊긴 벽 + 채운 벽의 벽면 면적 = 이어진 벽 면적 − 창·문 면적(1200×1200 + 900×2100)
    def area(data):
        return sum(GC.height_of(w, data["params"], "wall") * __import__("math").dist(*w["centerline"])
                   for w in data["elements"]["wall"])
    assert abs(area(g) - (area(full) - 1200 * 1200 - 900 * 2100)) < 1.0
    rows = {r[0]: r for r in total["벽"][1]}
    # 벽 행은 도면에 그려진 벽 길이 6.9m·층고 2.8m 그대로, 채운 벽은 따로 — 면적 합이 창·문을 뺀 벽면과 같다
    assert rows["T200"][1:5] == [3, 6.9, 2.8, 19.3]
    assert rows["T200 창·문 위아래"][1:5] == [3, 3.3, "", round(2.55e6 / 1e6, 1)]   # 1.08 + 0.84 + 0.63 ㎡


def test_door_jamb_pieces_touching_the_door_edge_are_still_fragments(tmp_path):
    """블록 중심을 문 끝에서 실제 중심으로 옮기자 문설주 조각(150mm)이 반 폭 밖 27mm 에 서서 다시 개구부가 됐다."""
    doc = ezdxf.new(units=4)
    doc.blocks.new("PD-750").add_line((-750, 0), (0, 0))
    ms = doc.modelspace()
    for y in (0, 200):
        ms.add_line((0, y), (2000, y), dxfattribs={"layer": "A-WALL"})
        ms.add_line((2750, y), (5000, y), dxfattribs={"layer": "A-WALL"})
    ms.add_blockref("PD-750", (2750, 100), dxfattribs={"layer": "A-DOOR"})
    ms.add_lwpolyline([(2775, 25), (2775, 175)], dxfattribs={"layer": "A-DOOR"})   # 문 끝 바깥 25mm 의 문설주 선
    p = tmp_path / "jamb.dxf"
    doc.saveas(p)
    g = _parse(str(p))
    assert [o.get("mark") for o in g["elements"]["opening"]] == ["PD-750"]


def test_relink_keeps_infill_edits_on_infill_walls_and_wall_edits_on_walls(tmp_path):
    """재연결 후보는 채움 ↔ 채움, 벽 ↔ 벽. 스냅숏에는 `source` 가 없어 종전 필터는 채움 후보를 전부 버렸다."""
    import element_id as E
    g = _parse(_plan(tmp_path))
    walls = g["elements"]["wall"]
    lintel = next(w for w in walls if w.get("infill_part") == "above")
    host = next(w for w in walls if w.get("source") != "opening_infill" and w["centerline"][0][0] in (0.0, 3000.0))
    edits = {"w:gone-infill": E.capture_edit(lintel, {"overrides": {"height": 500.0}}, "wall", g["params"]),
             "w:gone-wall": E.capture_edit(host, {"overrides": {"width": 250.0}}, "wall", g["params"])}
    rows = {r["orphan"]: r for r in E.suggest_relink(sorted(edits), edits, g["elements"])}
    infill_eids = {w["eid"] for w in walls if w.get("source") == "opening_infill"}
    assert rows["w:gone-infill"]["candidates"] and set(rows["w:gone-infill"]["candidates"]) <= infill_eids
    assert rows["w:gone-wall"]["candidates"] and not set(rows["w:gone-wall"]["candidates"]) & infill_eids


def test_closed_polygon_walls_are_never_the_reference_so_no_infill_lands_on_a_wall_face(tmp_path):
    doc = ezdxf.new(units=4)
    doc.blocks.new("W-1200").add_line((-1200, 0), (0, 0))
    ms = doc.modelspace()
    for x0, x1 in ((0, 3000), (4200, 7000)):
        ms.add_lwpolyline([(x0, 0), (x1, 0), (x1, 200), (x0, 200)], close=True, dxfattribs={"layer": "A-WALL"})
    ms.add_blockref("W-1200", (4200, 100), dxfattribs={"layer": "A-WIN"})
    p = tmp_path / "closed.dxf"
    doc.saveas(p)
    g = _parse(str(p))
    assert all(w.get("pairing") == "closed" for w in g["elements"]["wall"])
    assert _infill(g) == {}      # 면 위에 반쯤 걸친 벽을 세우느니 만들지 않는다


def test_a_swing_door_drawn_with_its_leaf_along_local_x_is_centred_on_the_wall(tmp_path):
    """스윙 문은 로컬 X·Y 범위가 둘 다 문 폭이다. 문짝(폭 길이의 직선)이 로컬 X 에만 있으면 열린 쪽은 Y 다."""
    doc = ezdxf.new(units=4)
    d = doc.blocks.new("D-900")
    d.add_line((0, 0), (900, 0))
    d.add_arc((0, 0), 900, 0, 90)
    ms = doc.modelspace()
    for y in (0, 200):
        ms.add_line((0, y), (5000, y), dxfattribs={"layer": "A-WALL"})
    ms.add_blockref("D-900", (1900, 100), dxfattribs={"layer": "A-DOOR", "rotation": 90})
    p = tmp_path / "swing.dxf"
    doc.saveas(p)
    op = _parse(str(p))["elements"]["opening"][0]
    assert op["center"] == [1450.0, 100.0] and op["wall_indices"]


def test_a_window_overlapping_a_door_does_not_get_a_sill_wall_inside_the_door(tmp_path):
    doc = ezdxf.new(units=4)
    doc.blocks.new("W-2000").add_line((-2000, 0), (0, 0))
    doc.blocks.new("D-900").add_line((-900, 0), (0, 0))
    ms = doc.modelspace()
    for x0, x1 in ((0, 3000), (5000, 8000)):
        for y in (0, 200):
            ms.add_line((x0, y), (x1, y), dxfattribs={"layer": "A-WALL"})
    ms.add_blockref("W-2000", (5000, 100), dxfattribs={"layer": "A-WIN"})     # 창 3000~5000
    ms.add_blockref("D-900", (3900, 100), dxfattribs={"layer": "A-DOOR"})     # 문 3000~3900 — 창과 겹친다
    p = tmp_path / "overlap.dxf"
    doc.saveas(p)
    g = _parse(str(p))
    below = [w for w in _infill(g).values() if w["infill_part"] == "below"]
    assert [_span(w)[0] for w in below] == [[3900.0, 5000.0]]


def test_multi_storey_ifc_moves_the_floor_anchor_with_each_storey(tmp_path):
    try:
        import ifcopenshell  # noqa: F401
        import ifc_builder as IB
    except (ImportError, SystemExit):
        import unittest
        raise unittest.SkipTest("ifcopenshell 없음")
    import json
    geom = {"units": "mm", "params": {"wall": {"width": 200, "height": 2800}}, "floors": [{"z": 0.0, "label": "Level_1"}],
            "elements": {"wall": [
                {"kind": "polyline", "closed": False, "points": [[0, 0], [3000, 0]], "centerline": [[0, 0], [3000, 0]],
                 "width_detected": 200.0, "z_base": 0.0, "eid": "w:a"},
                {"kind": "polyline", "closed": False, "points": [[3000, 0], [4200, 0]], "centerline": [[3000, 0], [4200, 0]],
                 "width_detected": 200.0, "z_base": 2100.0, "floor_z": 0.0, "overrides": {"height": 700.0},
                 "pairing": "infill", "source": "opening_infill", "eid": "w:lintel"}]}}
    p = tmp_path / "g.json"
    p.write_text(json.dumps(geom), encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()):
        IB.build_multi([{"geometry": str(p), "storey": "1F", "z": 0}, {"geometry": str(p), "storey": "2F", "z": 2800}],
                       str(tmp_path / "out.ifc"), qto=False)
    m = ifcopenshell.open(str(tmp_path / "out.ifc"))
    by_storey = {s.Name: sorted(e.Name or "" for rel in s.ContainsElements for e in rel.RelatedElements)
                 for s in m.by_type("IfcBuildingStorey")}
    assert all(len(v) == 2 for v in by_storey.values()), by_storey


# ── 종류 선언(layer_map opts subtype=) ─────────────────────────────────────
def _unmarked_plan(tmp_path):
    """부호가 없는 실무 도면: 문은 '안방 도어 평면' 블록(기준점이 가운데), 창은 A-WIN 레이어의 **선**(블록 아님)."""
    doc = ezdxf.new(units=4)
    d = doc.blocks.new("안방 도어 평면")
    d.add_line((-450, 0), (450, 0))
    d.add_line((-450, 0), (-450, -900))
    ms = doc.modelspace()
    for x0, x1 in [(0, 3000), (4200, 6000), (6900, 9000)]:
        for y in (0, 200):
            ms.add_line((x0, y), (x1, y), dxfattribs={"layer": "A-WALL"})
    ms.add_lwpolyline([(3000, 0), (4200, 0), (4200, 200), (3000, 200)], close=True, dxfattribs={"layer": "A-WIN"})
    ms.add_blockref("안방 도어 평면", (6450, 100), dxfattribs={"layer": "A-DOOR"})
    p = tmp_path / "unmarked.dxf"
    doc.saveas(p)
    return str(p)


def _map(tmp_path, declare):
    rows = ["pattern,category,width,height,thickness,opts", "A-WALL$,wall,,,,",
            "A-DOOR$,opening,,,," + ("subtype=door" if declare else ""),
            "A-WIN$,opening,,,," + ("subtype=window" if declare else "")]
    p = tmp_path / ("declared.csv" if declare else "plain.csv")
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return P.load_layer_map(str(p))


def test_declared_subtype_gives_unmarked_openings_their_sill_wall_and_lintel(tmp_path):
    """★ 블록 이름에 부호(D-900·W-1200)가 없고 창을 선으로 그린 도면은 개구부가 **전부 종류 미상**이었다 —
    채움은 종류를 알 때만 서므로 창·문 자리가 바닥부터 천장까지 뚫려 보였고, 빌더도 층 전체 높이로 뚫었다
    (실측: 종합평면도 기준층 개구부 136개 전부). layer_map 의 `subtype=` 선언으로 레이어가 종류를 준다 — 추정이 아니라
    적힌 것이다. 선언이 없으면 종전대로 종류 미상이다."""
    plan = _unmarked_plan(tmp_path)
    with contextlib.redirect_stdout(io.StringIO()):
        g = P.parse(plan, _map(tmp_path, True), P.load_layer_map(str(ROOT / "block_map.csv")))
        plain = P.parse(plan, _map(tmp_path, False), P.load_layer_map(str(ROOT / "block_map.csv")))
    subs = sorted(o.get("subtype") or "" for o in g["elements"]["opening"])
    assert subs == ["door", "window"], subs
    assert {o.get("subtype") for o in plain["elements"]["opening"]} == {None}
    parts = sorted((o["subtype"], w["infill_part"]) for w in g["elements"]["wall"] if w.get("source") == "opening_infill"
                   for o in g["elements"]["opening"] if o["eid"] == w["infill_of"])
    assert parts == [("door", "above"), ("window", "above"), ("window", "below")], parts
    assert not [w for w in plain["elements"]["wall"] if w.get("source") == "opening_infill"]
