"""칸막이 보드선 · 중복 선 · 문 기호 조각 — 형상은 멀쩡해 보이지만 물량과 간섭을 부풀리는 것들."""
import contextlib
import hashlib
import io
import os

import ezdxf

import dxf_parser as dp
from mep_profile import validate_profile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _wall(eid, y, width, layer="Parti"):
    pts = [[0, y], [3000, y]]
    return {"eid": eid, "kind": "polyline", "points": pts, "centerline": pts, "pairing": "paired",
            "width_detected": width, "layer": layer, "z_base": 0.0}


def test_nested_same_layer_pairs_collapse_to_the_outer_faces():
    walls = [_wall("w:80", 50, 80), _wall("w:100", 50, 100), _wall("w:90", 45, 90), _wall("w:dup", 50, 100),
             _wall("w:other", 50, 80, layer="A-WALL"), _wall("w:blob", 50, 440)]
    kept, dropped = dp.collapse_nested_wall_pairs(walls, {"wall": {"width": 200, "height": 2800}})
    # 두께 차가 큰 440mm 덩어리는 고르지 않고, 다른 레이어는 건드리지 않는다
    assert [w["eid"] for w in kept] == ["w:100", "w:other", "w:blob"]
    assert dropped == {"Parti": 3}


def test_profile_wall_rows_default_to_a_50mm_pairing_floor():
    rows = validate_profile({"version": 1, "source_sha256": "0" * 64, "architecture_layers": [
        {"pattern": "Parti$", "category": "wall"}, {"pattern": "A-COL$", "category": "wall", "pair_min_mm": 30},
        {"pattern": "A-CEN$", "category": "ignore"}]})["architecture_layers"]
    assert [r.get("pair_min_mm") for r in rows] == [50.0, 30.0, None]


def test_a_partition_drawn_with_board_lines_becomes_one_wall(tmp_path):
    path = tmp_path / "parti.dxf"
    doc = ezdxf.new(units=4)
    for y in (0, 10, 90, 100):
        doc.modelspace().add_line((0, y), (4000, y), dxfattribs={"layer": "Parti"})
    doc.saveas(path)
    profile = {"version": 1, "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
               "architecture_layers": [{"pattern": "^Parti$", "category": "wall", "height_mm": 2600}]}
    with contextlib.redirect_stdout(io.StringIO()):
        g = dp.parse(str(path), list(dp.DEFAULT_LAYER_RULES), dp.DEFAULT_BLOCK_RULES, mep_profile=profile)
    widths = [round(w["width_detected"]) for w in g["elements"]["wall"]]
    # 한 벽이다. 두께는 그리디 페어링이 고른 짝의 것(여기선 보드선-면선 90) — 바깥면 우선 페어링은 없다
    assert len(widths) == 1 and 90 <= widths[0] <= 100, widths
    assert g["nested_pairs_collapsed"]["Parti"] >= 1                     # 안쪽 짝을 버렸다고 말한다


def test_door_symbol_fragments_and_duplicates_are_dropped_but_sleeves_stay():
    door = {"kind": "circle", "center": [1000, 100], "radius": 450, "layer": "", "z_base": 0}
    leaf = {"kind": "polyline", "points": [[1030, 50], [1180, 50], [1180, 90], [1030, 90]], "closed": True,
            "layer": "A-DOOR", "z_base": 0}
    sleeve = {"kind": "circle", "center": [1200, 100], "radius": 50, "layer": "SLEEVE", "z_base": 0}
    elements = {"opening": [door, dict(door), leaf, dict(leaf), sleeve]}
    dropped = dp.drop_opening_fragments(elements)
    assert elements["opening"] == [door, sleeve]
    assert dropped == {"(블록)": 1, "A-DOOR": 2}


def test_door_and_window_blocks_take_their_width_from_the_name(tmp_path):
    path = tmp_path / "blocks.dxf"
    doc = ezdxf.new(units=4)
    names = ("PD-750", "XREF_UNIT expand$0$W-1800")
    for name in names:
        doc.blocks.new(name=name).add_line((0, 0), (100, 0))
    for i, name in enumerate(names):
        doc.modelspace().add_blockref(name, (1000 + 4000 * i, 0), dxfattribs={"layer": "0"})
    doc.saveas(path)
    blocks = dp.load_layer_map(os.path.join(ROOT, "block_map.csv"))
    with contextlib.redirect_stdout(io.StringIO()):
        g = dp.parse(str(path), list(dp.DEFAULT_LAYER_RULES), blocks)
    assert sorted(round(2 * o["radius"]) for o in g["elements"]["opening"]) == [750, 1800]
