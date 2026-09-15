"""간섭 검토 목록 — 한 교차가 한 줄, 위치·부재·조치가 붙는다."""
import geom_contract as GC
from clash_review import find_clashes


def _wall(eid, pts, width=200, height=2600, z=0):
    return {"eid": eid, "kind": "polyline", "closed": False, "points": pts, "centerline": pts,
            "overrides": {"width": width, "height": height}, "z_base": z, "layer": "A-WALL"}


def _geom(**elements):
    return {"contract": GC.contract_block(), "params": {"wall": {"width": 200, "height": 2600}},
            "floors": [{"z": 0, "label": "L1"}], "elements": elements}


def _pipe(eid, pts, elevation=78, diameter=15.9):
    return {"eid": eid, "kind": "polyline", "points": pts, "elevation": elevation, "diameter": diameter, "system": "heating"}


def test_duct_through_a_wall_is_one_located_penetration():
    duct = {"eid": "d:1", "kind": "polyline", "points": [[2500, -1500], [2500, 1700]], "elevation": 2400,
            "width_mm": 400, "height_mm": 300, "system": "SA"}
    g = _geom(wall=[_wall("w:1", [[0, 100], [5000, 100]])], duct=[duct])
    result = find_clashes(g)
    (item,) = result["items"]
    assert item["kind"] == "wall_penetration" and item["struct"]["eid"] == "w:1" and item["mep"]["eid"] == "d:1"
    assert item["at"] == [2500.0, 100.0] and item["z"] == [2250.0, 2550.0] and item["crossing_mm"] == 200.0
    assert item["mep"]["size"] == "400×300" and item["mep"]["system"] == "SA"
    assert find_clashes(g)["items"][0]["id"] == item["id"]                 # 다시 돌려도 같은 id
    assert result["summary"] == {"total": 1, "by_kind": {"wall_penetration": 1}, "through_openings": 0,
                                 "skipped_structures": 0, "skipped_routes": 0}


def test_floor_pipe_under_a_wall_and_a_thin_wall_are_told_apart():
    g = _geom(wall=[_wall("w:rc", [[0, 100], [5000, 100]]), _wall("w:sliver", [[0, 3000], [5000, 3000]], width=10)],
              pipe=[_pipe("p:1", [[1000, -500], [1000, 3500]])])
    kinds = {c["struct"]["eid"]: c["kind"] for c in find_clashes(g)["items"]}
    assert kinds == {"w:rc": "under_wall", "w:sliver": "suspect_thin_wall"}


def test_crossing_the_same_wall_twice_is_two_rows():
    g = _geom(wall=[_wall("w:1", [[0, 100], [5000, 100]])],
              pipe=[_pipe("p:u", [[500, -500], [500, 700], [1500, 700], [1500, -500]])])
    items = find_clashes(g)["items"]
    assert [c["at"][0] for c in items] == [500.0, 1500.0]


def test_a_pipe_through_a_door_opening_is_not_a_clash_but_is_counted():
    door = {"eid": "o:1", "kind": "circle", "center": [1000, 100], "radius": 450, "width": 900, "height": 2100,
            "sill": 0, "host_dir": [1, 0], "host_width": 200, "z_base": 0}
    g = _geom(wall=[_wall("w:1", [[0, 100], [5000, 100]])], opening=[door], pipe=[_pipe("p:1", [[1000, -500], [1000, 700]])])
    result = find_clashes(g)
    assert result["items"] == [] and result["summary"]["through_openings"] == 1


def test_a_riser_through_a_slab_and_no_clash_above_the_wall():
    slab = {"eid": "s:1", "kind": "polyline", "closed": True, "points": [[0, 0], [4000, 0], [4000, 4000], [0, 4000]],
            "z_base": 3000, "overrides": {"thickness": 200}}
    riser = {"eid": "p:r", "kind": "polyline", "points": [[2000, 2000], [2000, 2000]], "elevation": 2000, "diameter": 100,
             "path3d": {"segments": [{"type": "line", "start": [2000, 2000, 0], "end": [2000, 2000, 1500]}]}}
    high = {"eid": "d:high", "kind": "polyline", "points": [[2500, -1500], [2500, 1700]], "elevation": 2700,
            "width_mm": 400, "height_mm": 100}
    g = _geom(wall=[_wall("w:1", [[0, 100], [5000, 100]])], slab=[slab], pipe=[riser], duct=[high])
    items = find_clashes(g)["items"]
    assert [(c["kind"], c["mep"]["eid"]) for c in items] == [("slab_penetration", "p:r")]


def test_project_state_carries_the_clash_list_for_overlaid_drawings(tmp_path):
    import ezdxf
    from project_server import ProjectSession
    from project_store import ProjectStore
    paths = []
    for name, duct in (("arch.dxf", False), ("mep.dxf", True)):
        doc = ezdxf.new(units=4)
        for y in (0, 200):
            doc.modelspace().add_line((0, y), (5000, y), dxfattribs={"layer": "WALL"})
        if duct:
            doc.modelspace().add_line((2500, -1500), (2500, 1700), dxfattribs={"layer": "DUCT"})
        doc.saveas(tmp_path / name)
        paths.append(str(tmp_path / name))
    session = ProjectSession(ProjectStore(tmp_path / "unit.mep").create([{"id": "main", "path": paths[0]}]))
    first = session.state()
    state = session.add_source(paths[1], first["revision"], first["project_id"])
    review = state["geometry"]["clash_review"]
    (item,) = review["items"]
    assert item["struct"]["eid"].startswith("main:") and item["mep"]["eid"].startswith("src2:")
    assert any(i["category"] == "clash" and i["eid"] == item["mep"]["eid"] for i in session.pascal_review()["items"])
