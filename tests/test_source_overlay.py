# -*- coding: utf-8 -*-
"""원본 DXF 밑그림 — 편집 화면에서 원본 선이 벽과 **같은 자리·같은 방향**으로 겹쳐야 한다.

밑그림이 뒤집히거나 밀리면 사람은 부재가 틀린 줄 알고 멀쩡한 벽을 옮긴다. SVG 는 북쪽이 위이고,
guide 노드는 층 bbox 에 맞춰 놓이며, 되돌리기는 guide 를 부재로 읽지 않는다.
"""
import os
import re
import sys

import ezdxf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import geom_contract as GC  # noqa: E402
import pascal_bridge as PB  # noqa: E402
from source_drawing import drawing_svg  # noqa: E402


def test_svg_is_north_up_and_sized_to_the_floor_bbox():
    floor = {"bbox": [0.0, 0.0, 1000.0, 500.0], "primitives": [
        {"kind": "polyline", "closed": False, "points": [[0.0, 0.0], [0.0, 500.0]]},
        {"kind": "circle", "center": [1000.0, 500.0], "radius": 50.0}]}
    svg = drawing_svg(floor, px=1000)
    assert re.search(r'width="1000" height="500" viewBox="0 -500 1000 500"', svg)
    assert 'points="0,0 0,-500"' in svg                      # 북쪽(+y)이 SVG 위쪽(−)
    assert '<circle cx="1000" cy="-500" r="50"/>' in svg
    assert drawing_svg({"bbox": None, "primitives": []}) is None


def _geo():
    wall = {"kind": "polyline", "closed": False, "eid": "w:1", "layer": "A-WALL", "z_base": 0.0,
            "points": [[0.0, 0.0], [4000.0, 0.0]], "centerline": [[0.0, 0.0], [4000.0, 0.0]],
            "overrides": {"width": 200.0, "height": 2800.0}}
    return {"source": "t.dxf", "units": "mm", "params": {}, "elements": {"wall": [wall]},
            "contract": GC.contract_block()}


def test_guide_sits_on_the_floor_bbox_and_is_not_read_back_as_a_member():
    geo = _geo()
    scene, _ = PB.to_pascal_scene(geo)
    drawing = {"floors": [{"id": "main", "label": "1F", "z": 0.0, "bbox": [0.0, -100.0, 4000.0, 1900.0]}]}
    assert PB.add_source_guides(scene, drawing, lambda f: "/api/mep/source?floor=" + f["id"]) == 1
    (guide,) = [n for n in scene["nodes"].values() if n["type"] == "guide"]
    assert guide["url"] == "/api/mep/source?floor=main" and guide["scale"] == 0.4     # 가로 4m = 10m × 0.4
    assert guide["position"] == [2.0, 0.0, -0.9]                                       # 중심 (2000, 900) → z = −0.9
    level = next(n for n in scene["nodes"].values() if n["type"] == "level")
    assert guide["parentId"] == level["id"] and guide["id"] in level["children"]
    back, rev = PB.from_pascal_scene(scene)
    assert rev["dropped"] == {} and [r["eid"] for r in back["elements"]["wall"]] == ["w:1"]
    assert PB.scene_to_edits(geo, scene)[0] == {}


def test_snapshot_carries_the_guide_but_saves_still_match_its_fingerprint(tmp_path):
    from project_server import ProjectSession
    from project_store import ProjectStore
    dxf = tmp_path / "drawing.dxf"
    doc = ezdxf.new(units=4)
    doc.layers.new("WALL")
    for y in (0, 200):
        doc.modelspace().add_line((0, y), (5000, y), dxfattribs={"layer": "WALL"})
    doc.saveas(dxf)
    sess = ProjectSession(ProjectStore(tmp_path / "drawing.mep").create(sources=[{"id": "main", "path": str(dxf)}]))
    snap = sess.pascal_snapshot()
    guides = [n for n in snap["scene"]["nodes"].values() if n["type"] == "guide"]
    assert [g["url"] for g in guides] == ["/api/mep/source?floor=main"] and snap["report"]["guides"] == 1
    assert sess.pascal_source_svg("main").startswith("<svg")
    wall = next(n for n in snap["scene"]["nodes"].values() if n["type"] == "wall")
    wall["thickness"] = 0.45
    done = sess.pascal_apply(snap["scene"], snap["revision"], snap["project_id"], snap["snapshot_sha256"], "op-g")
    assert done["applied"] is True                           # guide 를 실은 씬으로 저장해도 409 가 아니다
