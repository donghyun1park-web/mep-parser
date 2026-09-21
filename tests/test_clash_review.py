"""간섭 검토 목록 — 한 교차가 한 줄, 위치·부재·조치가 붙는다."""
from pathlib import Path
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
                                 "through_openings_assumed": 0, "assumed_basis": 0, "on_uncertain_walls": 0,
                                 "skipped_structures": 0, "skipped_routes": 0, "envelope_basis": 0}
    assert item["basis"] == "declared" and item["assumed"] == []      # 선언 두께·높이 + 도면 z


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


def test_ceiling_and_floor_slabs_come_from_the_level_declaration_only_when_it_exists():
    """평면도에 슬래브 몸체가 없어 천장 쪽 간섭은 판정 대상 밖이었다 — 선언이 있으면 그 두 장만 세운다."""
    riser = {"eid": "p:r", "kind": "polyline", "points": [[2000, 2000], [2000, 2000]], "elevation": 1500,
             "diameter": 100, "path3d": {"segments": [{"type": "line", "start": [2000, 2000, -1000],
                                                       "end": [2000, 2000, 1400]}]}}
    tight = {"eid": "d:tight", "kind": "polyline", "points": [[0, 500], [4000, 500]], "elevation": 2500,
             "width_mm": 200, "height_mm": 200, "system": "SA"}          # 상단 2600 = 천장 슬래브 밑면
    g = _geom(pipe=[riser], duct=[tight])
    assert find_clashes(g)["items"] == []                                 # 선언이 없으면 아무것도 세우지 않는다
    g["mep_profile"] = {"levels": {"structural_slab_top_mm": 0.0, "floor_to_floor_mm": 2800.0,
                                   "slab_thickness_mm": 200.0},
                        "region": {"id": "unit", "bounds_mm": [0, 0, 5000, 5000]}}
    items = find_clashes(g)["items"]
    # 밑면에 딱 붙은 덕트는 항목이 아니다(접촉은 간섭이 아니다). 슬래브를 뚫은 입상관만 뜬다.
    assert [(c["mep"]["eid"], c["kind"], c["struct"]["eid"]) for c in items] == [("p:r", "slab_penetration", None)]
    # 입상관은 z 500~2900 이라 천장 슬래브(2600~2800)를 지난다. 바닥(−200~0)에는 닿지 않는다.
    assert "층 높이 선언" in items[0]["struct"]["layer"] and items[0]["struct"]["synthetic"] == "천장"
    assert "천장 슬래브" in items[0]["action"] and "합성" in items[0]["action"]


def test_a_penetration_at_a_drawn_sleeve_is_told_apart_from_one_without():
    """도면이 '여기는 뚫어 뒀다' 고 말한 자리와 아닌 자리는 조치가 다르다."""
    sleeve = {"eid": "e:s", "kind": "polyline", "closed": True, "role": "sleeve", "z_base": 0,
              "points": [[2400, 40], [2600, 40], [2600, 160], [2400, 160]], "overrides": {"height": 200}}
    duct = {"eid": "d:in", "kind": "polyline", "points": [[2500, -1500], [2500, 1700]], "elevation": 2400,
            "width_mm": 400, "height_mm": 300, "system": "RA"}
    away = {"eid": "d:out", "kind": "polyline", "points": [[4000, -1500], [4000, 1700]], "elevation": 2400,
            "width_mm": 400, "height_mm": 300, "system": "RA"}
    g = _geom(wall=[_wall("w:1", [[0, 100], [5000, 100]])], equipment=[sleeve], duct=[duct, away])
    kinds = {c["mep"]["eid"]: c["kind"] for c in find_clashes(g)["items"]}
    assert kinds == {"d:in": "sleeve_provided", "d:out": "wall_penetration"}


def test_a_sloped_drain_meets_the_slab_where_the_slope_puts_it():
    """구배 선언(`GC.sloped_path3d`)이 붙은 경로는 구간마다 실제 높이로 대조된다 — 평평하게 뒀으면
    안 걸렸을 슬래브를, 저점이 실제로 떨어지는 자리에서 잡아낸다."""
    slab = {"eid": "s:1", "kind": "polyline", "closed": True, "z_base": 2400, "overrides": {"thickness": 100},
            "points": [[1800, -500], [2200, -500], [2200, 500], [1800, 500]]}
    flat = _pipe("p:flat", [[0, 0], [2000, 0]], elevation=2600, diameter=100)
    g_flat = _geom(slab=[slab], pipe=[flat])
    assert find_clashes(g_flat)["items"] == []                     # 평평하면 슬래브 z 범위를 안 건드린다

    sloped = dict(flat, eid="p:sloped",
                  path3d={"segments": GC.sloped_path3d([[0, 0], [2000, 0]], 0.15, "start")})
    g_sloped = _geom(slab=[slab], pipe=[sloped])
    (item,) = find_clashes(g_sloped)["items"]
    assert item["kind"] == "slab_penetration" and item["at"][0] > 1000            # 저점(끝 쪽)에서 걸린다


def test_an_insulated_pipe_widens_the_clash_band_only_when_the_profile_opts_in():
    """보온 외피(`rec['insulation_mm']`)는 프로필 `rules.insulation_envelope: true` 일 때만 반영한다 —
    기본은 나관 치수. 켜면 간섭 수가 늘 수 있다(opt-in 이라 사용자가 결정한다)."""
    wall = _wall("w:1", [[0, 1000], [5000, 1000]])
    duct = {"eid": "d:1", "kind": "polyline", "points": [[0, 1150], [2000, 1150]], "elevation": 2400,
            "width_mm": 50.0, "height_mm": 50.0, "insulation_mm": 40.0}   # 나관 반폭 25 < 150mm 간격, 외피 반폭 65 >= 간격
    g = _geom(wall=[wall], duct=[duct])
    bare = find_clashes(g)
    assert bare["items"] == [] and bare["summary"]["envelope_basis"] == 0
    g_insulated = dict(g, mep_profile={"rules": {"insulation_envelope": True}})
    insulated = find_clashes(g_insulated)
    (item,) = insulated["items"]
    assert item["kind"] == "wall_penetration" and item["mep"]["envelope"] == "insulated"
    assert insulated["summary"]["envelope_basis"] == 1


def test_a_drawn_sleeve_smaller_than_pipe_od_plus_40_is_its_own_row_and_names_the_clause():
    """KCS 31 20 15 2.2.20 — 슬리브는 배관 외경 + 40mm 정도. 모자라면 슬리브가 있어도 '규격 부족'이다."""
    pipe = {"eid": "p:in", "kind": "polyline", "points": [[2500, -1500], [2500, 1700]],
            "elevation": 2400, "diameter": 100.0}
    small = {"eid": "e:small", "kind": "polyline", "closed": True, "role": "sleeve", "z_base": 2200,
             "points": [[2445, 40], [2555, 40], [2555, 160], [2445, 160]], "overrides": {"height": 400}}  # 110mm < 140mm
    ok = {"eid": "e:ok", "kind": "polyline", "closed": True, "role": "sleeve", "z_base": 2200,
          "points": [[4425, 25], [4575, 25], [4575, 175], [4425, 175]], "overrides": {"height": 400}}      # 150×150
    away = {"eid": "p:ok", "kind": "polyline", "points": [[4500, -1500], [4500, 1700]],
            "elevation": 2400, "diameter": 100.0}
    g = _geom(wall=[_wall("w:1", [[0, 100], [5000, 100]])], equipment=[small, ok], pipe=[pipe, away])
    by_eid = {c["mep"]["eid"]: c for c in find_clashes(g)["items"]}
    small_item = by_eid["p:in"]
    assert small_item["kind"] == "sleeve_undersized"
    assert small_item["rule"] == {"id": "sleeve-diameter-from-pipe-and-insulation", "clause": "KCS 31 20 15 2.2.20",
                                  "required_mm": 140.0, "measured_mm": 110.0}
    assert by_eid["p:ok"]["kind"] == "sleeve_provided" and "rule" not in by_eid["p:ok"]
    from clash_review import to_rows
    row = next(r for r in to_rows({"items": [small_item]}) if r["mep_eid"] == "p:in")
    assert (row["rule_id"], row["required_mm"], row["measured_mm"]) == (
        "sleeve-diameter-from-pipe-and-insulation", 140.0, 110.0)


def test_each_row_says_whether_the_heights_it_used_were_declared_or_assumed():
    """평면도에는 높이가 없다 — 가정으로 나온 줄이 도면이 말해 준 줄과 같아 보이면 안 된다."""
    door = {"eid": "o:1", "kind": "circle", "center": [1000, 100], "radius": 450, "width": 900, "height": 2100,
            "sill": 0, "host_dir": [1, 0], "host_width": 200, "z_base": 0, "dims_assumed": ["height", "sill"]}
    plain = {"eid": "w:p", "kind": "polyline", "closed": False, "points": [[0, 3000], [5000, 3000]],
             "centerline": [[0, 3000], [5000, 3000]], "z_base": 0, "layer": "A-WALL"}   # 높이가 params 기본값이다
    g = _geom(wall=[_wall("w:1", [[0, 100], [5000, 100]]), plain], opening=[door],
              pipe=[_pipe("p:1", [[1000, -500], [1000, 700]]), _pipe("p:2", [[2000, 2500], [2000, 3500]])])
    result = find_clashes(g)
    (row,) = result["items"]
    assert (row["struct"]["eid"], row["basis"], row["assumed"]) == ("w:p", "assumed", ["height"])
    assert (row["struct"]["z_basis"], row["mep"]["z_basis"]) == ("assumed", "source")
    # 문 안을 지나 뺀 한 건은 그 문의 높이·문턱이 가정이라는 사실과 함께 센다.
    summary = result["summary"]
    assert (summary["through_openings"], summary["through_openings_assumed"], summary["assumed_basis"]) == (1, 1, 1)


def test_an_edited_pipe_elevation_reports_declared_not_source():
    """인스펙터에서 편집한 elevation 은 overrides 에 실린다 — height_basis 가 이미 'declared' 로 안다."""
    edited_pipe = {"eid": "p:1", "kind": "polyline", "points": [[1000, -500], [1000, 700]],
                   "elevation": 78, "overrides": {"elevation": 100}, "diameter": 15.9, "system": "heating"}
    g = _geom(wall=[_wall("w:1", [[0, 100], [5000, 100]])], pipe=[edited_pipe])
    result = find_clashes(g)
    (row,) = result["items"]
    assert row["mep"]["z_basis"] == "declared"


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


def test_a_plan_z_of_zero_is_not_evidence_of_an_installation_height():
    """평면도는 설비를 전부 z 0 에 그린다 — 0 은 '도면이 말해 줬다' 가 아니라 정보가 없다는 뜻이다."""
    flat = {"eid": "d:flat", "kind": "polyline", "points": [[2500, -1500], [2500, 1700]], "elevation": 0.0,
            "source_elevation_mm": 0.0, "elevation_source": "source",
            "width_mm": 400, "height_mm": 300, "system": "SA"}
    g = _geom(wall=[_wall("w:1", [[0, 100], [5000, 100]])], duct=[flat])
    (row,) = find_clashes(g)["items"]
    assert (row["basis"], row["mep"]["z_basis"]) == ("assumed", "assumed") and "plan_z" in row["assumed"]
    assert find_clashes(g)["summary"]["assumed_basis"] == 1
    # 같은 덕트를 프로필이 선언하면(설치 높이 규칙) 그 줄은 다시 선언 근거가 된다.
    declared = dict(flat, elevation=2400.0, elevation_source="profile")
    g2 = _geom(wall=[_wall("w:1", [[0, 100], [5000, 100]])], duct=[declared])
    (row2,) = find_clashes(g2)["items"]
    assert (row2["basis"], row2["assumed"], row2["mep"]["z_basis"]) == ("declared", [], "declared")


def test_the_list_becomes_one_table_excel_can_open(tmp_path):
    """현장에 보낼 표를 일회성 스크립트로 만들지 않는다 — 열은 고정이고 없는 값은 빈칸이다."""
    import csv as _csv
    from clash_review import CSV_COLUMNS, to_rows, write_csv
    duct = {"eid": "d:1", "kind": "polyline", "points": [[2500, -1500], [2500, 1700]], "elevation": 2400,
            "width_mm": 400, "height_mm": 300, "system": "SA", "layer": "M-SA"}
    g = _geom(wall=[_wall("w:1", [[0, 100], [5000, 100]])], duct=[duct])
    review = find_clashes(g)
    (row,) = to_rows(review)
    assert row["kind"] == "wall_penetration" and row["label"] == "벽 관통"
    assert (row["x_mm"], row["y_mm"], row["struct_eid"], row["mep_eid"]) == (2500.0, 100.0, "w:1", "d:1")
    assert row["mep_size"] == "400×300" and row["basis"] == "declared" and row["assumed"] == ""
    path, count = write_csv(tmp_path / "clash.csv", to_rows(review), CSV_COLUMNS)
    with open(path, encoding="utf-8-sig", newline="") as handle:
        table = list(_csv.reader(handle))
    assert count == 1 and table[0] == list(CSV_COLUMNS) and len(table) == 2
    assert open(path, "rb").read(3) == b"\xef\xbb\xbf"          # Excel 이 바로 연다


def test_a_synthetic_slab_row_says_where_it_came_from_instead_of_an_empty_cell(tmp_path):
    from clash_review import to_rows
    riser = {"eid": "p:r", "kind": "polyline", "points": [[2000, 2000], [2000, 2000]], "elevation": 1500,
             "diameter": 100, "path3d": {"segments": [{"type": "line", "start": [2000, 2000, -1000],
                                                       "end": [2000, 2000, 1400]}]}}
    g = _geom(pipe=[riser])
    g["mep_profile"] = {"levels": {"structural_slab_top_mm": 0.0, "floor_to_floor_mm": 2800.0,
                                   "slab_thickness_mm": 200.0},
                        "region": {"id": "unit", "bounds_mm": [0, 0, 5000, 5000]}}
    (row,) = to_rows(find_clashes(g))
    assert row["struct_eid"] is None and "층 높이 선언" in row["struct_layer"]


def test_the_command_line_writes_both_tables(tmp_path):
    import json as _json
    import subprocess
    import sys
    duct = {"eid": "d:1", "kind": "polyline", "points": [[2500, -1500], [2500, 1700]], "elevation": 2400,
            "width_mm": 400, "height_mm": 300, "system": "SA"}
    g = _geom(wall=[_wall("w:1", [[0, 100], [5000, 100]])], duct=[duct])
    source = tmp_path / "geometry.json"
    source.write_text(_json.dumps(g), encoding="utf-8")
    done = subprocess.run([sys.executable, "clash_review.py", str(source), "--connectivity"],
                          capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[1]))
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "geometry_clash.csv").exists() and (tmp_path / "geometry_connectivity.csv").exists()


def test_a_clash_row_says_how_the_parser_found_the_wall_it_stands_on():
    """벽이 간섭 품질의 바닥이다 — `single_offset` 은 축선 자체가 추정이라 위치도 두께도 추정이다."""
    guessed = dict(_wall("w:g", [[0, 100], [5000, 100]]), pairing="single_offset",
                   review_reason="single_offset", needs_review=True)
    g = _geom(wall=[guessed, _wall("w:ok", [[0, 3000], [5000, 3000]])],
              pipe=[_pipe("p:1", [[1000, -500], [1000, 700]]), _pipe("p:2", [[2000, 2500], [2000, 3500]])])
    result = find_clashes(g)
    by_eid = {c["struct"]["eid"]: c["struct"] for c in result["items"]}
    assert by_eid["w:g"]["pairing"] == "single_offset" and by_eid["w:g"]["uncertain"] is True
    assert "pairing" not in by_eid["w:ok"] and "uncertain" not in by_eid["w:ok"]   # 없는 키는 안 만든다
    assert result["summary"]["on_uncertain_walls"] == 1
    # 건축 레이어 분류 확인만 걸린 벽은 기하가 불확실한 것이 아니다 — 그걸로 세면 매번 전건이 된다.
    classified = dict(_wall("w:c", [[0, 6000], [5000, 6000]]), pairing="paired",
                      review_reason="project_architecture_classification", needs_review=True)
    g2 = _geom(wall=[classified], pipe=[_pipe("p:3", [[1000, 5500], [1000, 6500]])])
    assert find_clashes(g2)["summary"]["on_uncertain_walls"] == 0
