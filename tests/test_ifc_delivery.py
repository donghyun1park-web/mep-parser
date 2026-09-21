# -*- coding: utf-8 -*-
"""납품 IFC 는 `ifc_builder`(ifcopenshell 직접)가 낸다 — 모든 카테고리·개구부·설비·이음.

종전에 이 모듈은 벽·기둥·슬래브만 짓고 나머지는 "FreeCAD 가 필요하다"며 거부했다. 실측으로 그 전제가
깨졌다(`docs/decisions/ifc-builder.md`): 개구부는 절삭이 아니라 **선언**(`IfcOpeningElement`)이고
`ifcopenshell.geom.create_shape` — 우리 재검사가 쓰는 바로 그 함수 — 가 구멍을 정확히 뺀다.

여기 테스트는 전부 **실제 파일을 다시 열어** 본다. 빌더가 성공했다고 말하는 것은 증거가 아니다.
"""
import json
import os
import sys
import unittest
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import geom_contract as GC


def _ifc_builder():
    """ifcopenshell 은 선택 의존성이다 — 없는 PC 에서 **에러로** 죽으면 `build_exe.bat` 게이트가
    통째로 막힌다(`tests/test_ifc4d.py` 와 같은 규약). `ifc_builder` 는 없으면 `sys.exit(1)` 한다."""
    try:
        import ifc_builder
    except (ImportError, SystemExit) as exc:
        raise unittest.SkipTest(f"ifcopenshell 없음 — 납품 IFC 검사 미실행 ({exc})")
    return ifc_builder


def _geometry():
    """한 층에 모든 카테고리 하나씩 — 벽·기둥(각·원)·슬래브·보·zone·개구부(문·창)·배관·덕트·트레이·장비."""
    g = {
        "source": "t.dxf", "units": "mm", "scale_applied": 1.0,
        "contract": GC.contract_block(),
        "params": {"wall": {"width": 200, "height": 2800}, "column": {"height": 3000},
                   "slab": {"thickness": 200}},
        "floors": [{"z": 0.0, "label": "Level_1"}],
        "elements": {
            "wall": [
                {"eid": "wall:a", "kind": "polyline", "layer": "A-WALL", "centerline": [[0, 0], [5000, 0]],
                 "overrides": {"width": 200, "height": 2800}, "material": "콘크리트"},
                {"eid": "wall:b", "kind": "polyline", "layer": "A-WALL", "centerline": [[5000, 0], [5000, 4000]],
                 "overrides": {"width": 200, "height": 2800}},
                {"eid": "wall:c", "kind": "polyline", "closed": True, "pairing": "closed", "layer": "A-WALL",
                 "points": [[8000, 0], [8600, 0], [8600, 600], [8000, 600]], "overrides": {"height": 2800}},
            ],
            "column": [
                {"eid": "col:a", "kind": "polyline", "closed": True, "layer": "A-COLS",
                 "points": [[0, 3000], [600, 3000], [600, 3600], [0, 3600]], "overrides": {"height": 3000}},
                {"eid": "col:b", "kind": "circle", "center": [2000, 3300], "radius": 250,
                 "layer": "A-COLS", "overrides": {"height": 3000}},
            ],
            "slab": [{"eid": "slab:a", "kind": "polyline", "closed": True, "layer": "A-SLAB",
                      "points": [[0, 0], [9000, 0], [9000, 4000], [0, 4000]],
                      "overrides": {"thickness": 200}, "z_base": 0}],
            "beam": [{"eid": "beam:a", "kind": "polyline", "layer": "AU_BEAM",
                      "points": [[0, 2000], [5000, 2000]], "member_name": "G1",
                      "overrides": {"width": 400, "thickness": 600}, "z_base": 0}],
            "zone": [{"eid": "zone:a", "kind": "polyline", "closed": True, "layer": "A-ZONE",
                      "points": [[200, 200], [4800, 200], [4800, 3800], [200, 3800]]}],
            "opening": [
                {"eid": "op:d", "kind": "circle", "center": [1500, 0], "radius": 450,
                 "width": 900, "height": 2100, "sill": 0, "layer": "A-DOOR",
                 "subtype": "door", "wall_indices": [0]},
                {"eid": "op:w", "kind": "circle", "center": [3500, 0], "radius": 600,
                 "width": 1200, "height": 1200, "sill": 900, "layer": "A-WIND",
                 "subtype": "window", "wall_indices": [0]},
            ],
            "pipe": [
                {"eid": "pipe:a", "kind": "polyline", "layer": "M-PIPE",
                 "points": [[500, 500], [4500, 500], [4500, 3500]], "elevation": 2400,
                 "diameter": 100, "system": "CWS", "nominal_size": "DN100", "material": "강관"},
                {"eid": "pipe:b", "kind": "polyline", "layer": "M-PIPE",
                 "points": [[4500, 3500], [7000, 3500]], "elevation": 2400,
                 "diameter": 100, "system": "CWS", "nominal_size": "DN100", "material": "강관"},
            ],
            "duct": [{"eid": "duct:a", "kind": "polyline", "layer": "M-DUCT",
                      "points": [[500, 1500], [4500, 1500]], "elevation": 2500,
                      "width_mm": 400, "height_mm": 300, "system": "SA"}],
            "tray": [{"eid": "tray:a", "kind": "polyline", "layer": "E-TRAY",
                      "points": [[500, 2500], [4500, 2500]], "elevation": 2600,
                      "width_mm": 300, "height_mm": 100}],
            "equipment": [{"eid": "eq:a", "kind": "polyline", "closed": True, "layer": "M-EQUIP",
                           "points": [[6000, 2000], [7000, 2000], [7000, 3000], [6000, 3000]],
                           "elevation": 0, "height": 800, "role": "equipment"}],
        },
    }
    GC.assign_joints(g["elements"])
    return g


def _build(tmp_path, data=None, name="out"):
    ifc_builder = _ifc_builder()
    src = tmp_path / f"{name}.json"
    src.write_text(json.dumps(data or _geometry(), ensure_ascii=False), encoding="utf-8")
    out = tmp_path / f"{name}.ifc"
    return ifc_builder.build(str(src), str(out)), str(out)


def _classes(path):
    import ifcopenshell
    from collections import Counter
    model = ifcopenshell.open(path)
    return Counter(p.is_a() for p in model.by_type("IfcProduct"))


def test_every_category_reaches_the_ifc_and_the_artifact_verifier_passes(tmp_path):
    stats, out = _build(tmp_path)
    assert stats["status"] == "verified"
    assert stats["skip"] == 0, stats.get("unbuilt")
    got = _classes(out)
    # 설비는 계통별 IFC 클래스로 — 종전엔 전부 IfcBuildingElementProxy 였다.
    for ifc_class, n in (("IfcWall", 3), ("IfcColumn", 2), ("IfcSlab", 1), ("IfcBeam", 1),
                         ("IfcSpace", 1), ("IfcPipeSegment", 2), ("IfcDuctSegment", 1),
                         ("IfcCableCarrierSegment", 1), ("IfcDistributionElement", 1),
                         ("IfcDoor", 1), ("IfcWindow", 1), ("IfcOpeningElement", 2)):
        assert got[ifc_class] == n, (ifc_class, got)
    assert stats["materials"] == {"콘크리트": 1, "강관": 2}   # 적힌 것만 — 카테고리 추정 없다


def test_an_opening_is_a_declared_void_and_the_shape_really_loses_that_volume(tmp_path):
    """개구부는 절삭이 아니라 선언이다. 증거는 재검사가 쓰는 `create_shape` 의 부피 하나뿐이다."""
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.util.element

    stats, out = _build(tmp_path)
    cut = {c["host_name"] for o in stats["opening_results"] for c in o["cuts"]}
    assert len(cut) == 1 and all(not o["failed_hosts"] for o in stats["opening_results"])
    model = ifcopenshell.open(out)
    wall = next(w for w in model.by_type("IfcWall")
                if ifcopenshell.util.element.get_pset(w, "Pset_MEPParser")["EID"] == "wall:a")
    assert len(wall.HasOpenings) == 2
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    shape = ifcopenshell.geom.create_shape(settings, wall)
    verts, faces = shape.geometry.verts, shape.geometry.faces
    volume = 0.0
    for i in range(0, len(faces), 3):
        a, b, c = (verts[3 * j:3 * j + 3] for j in faces[i:i + 3])
        volume += (a[0] * (b[1] * c[2] - b[2] * c[1]) + a[1] * (b[2] * c[0] - b[0] * c[2])
                   + a[2] * (b[0] * c[1] - b[1] * c[0]))
    solid = 5000 * 200 * 2800
    void = 900 * 200 * 2100 + 1200 * 200 * 1200
    assert abs(volume / 6) * 1e9 == pytest.approx(solid - void, rel=1e-6)


def test_a_stacked_floor_cuts_its_own_wall_and_not_the_one_below(tmp_path):
    """`wall_indices` 는 그 층 안에서의 위치다. 안 밀어 주면 위층 문이 아래층 벽을 뚫는다 —
    형상은 멀쩡하고 검사도 통과하므로 이 테스트가 유일한 신호다."""
    ifc_builder = _ifc_builder()
    floors = []
    for i, label in enumerate(("B1", "1F")):
        data = _geometry()
        data.pop("floors")
        src = tmp_path / f"{label}.json"
        src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        floors.append({"geometry": str(src), "storey": label, "z": i * 3000.0})
    out = str(tmp_path / "stack.ifc")
    stats = ifc_builder.build_multi(floors, out)[-1]
    assert stats["status"] == "verified"
    for result in stats["opening_results"]:
        level = result["eid"].split(":")[0]
        assert result["cuts"], result
        for cut in result["cuts"]:
            assert cut["host_name"].startswith(level + ":"), (result["eid"], cut["host_name"])
    assert _classes(out)["IfcOpeningElement"] == 4
    assert stats["fittings"]["built"] == 2        # 이음 id 도 층마다 따로 — 안 그러면 0 이 된다
    # 벽타입은 모델 하나에 하나 — 층마다 캐시를 새로 주면 같은 `WALL-200` 이 층 수만큼 생긴다.
    import ifcopenshell
    model = ifcopenshell.open(out)
    names = [t.Name for t in model.by_type("IfcWallType")]
    assert len(names) == len(set(names)), names


def test_a_confirmed_joint_becomes_a_fitting_body_of_the_kind_the_quantity_table_counts(tmp_path):
    import ifcopenshell
    import ifcopenshell.util.element

    stats, out = _build(tmp_path)
    assert stats["fittings"]["built"] == 1 and stats["fittings"]["by_kind"] == {"pipe:elbow": 1}
    model = ifcopenshell.open(out)
    fitting = model.by_type("IfcPipeFitting")[0]
    pset = ifcopenshell.util.element.get_pset(fitting, "Pset_MEPParser")
    assert pset["FittingKind"] == "elbow"
    assert json.loads(pset["SourceEIDs"]) == ["pipe:a", "pipe:b"]


def test_a_declared_wall_material_survives_the_wall_connection_the_gui_turns_on(tmp_path):
    """GUI 의 '벽 접합' 은 **기본이 켜짐**이고, 프로젝트 기본값은 벽 재질을 묻는다. 둘이 만나면
    종전에는 빌드가 통째로 죽었다 — 평범한 `IfcMaterial` 이 `create_2pt_wall` 의 레이어셋을
    덮어써 벽 Body 가 아예 안 생기고 `Representation is NULL` 이 났다."""
    import ifcopenshell
    import ifcopenshell.util.element

    ifc_builder = _ifc_builder()
    src = tmp_path / "connected.json"
    src.write_text(json.dumps(_geometry(), ensure_ascii=False), encoding="utf-8")
    out = str(tmp_path / "connected.ifc")
    stats = ifc_builder.build(str(src), out, connect=True)
    assert stats["status"] == "verified"
    assert stats["materials"]["콘크리트"] == 1
    model = ifcopenshell.open(out)
    wall = next(w for w in model.by_type("IfcWall")
                if ifcopenshell.util.element.get_pset(w, "Pset_MEPParser")["EID"] == "wall:a")
    usage = ifcopenshell.util.element.get_material(wall)
    assert usage.is_a("IfcMaterialLayerSetUsage")
    assert [layer.Material.Name for layer in usage.ForLayerSet.MaterialLayers] == ["콘크리트"]


def test_two_walls_of_one_thickness_keep_their_own_declared_materials(tmp_path):
    """벽타입(레이어셋)은 벽 여러 장이 공유한다 — 두께만으로 캐시하면 조적 벽이 콘크리트로 납품된다.
    형상은 멀쩡해서 어떤 검사에도 안 걸린다."""
    import ifcopenshell
    import ifcopenshell.util.element

    ifc_builder = _ifc_builder()
    data = _geometry()
    data["elements"]["wall"][1]["material"] = "조적"       # 0번과 같은 200mm
    src = tmp_path / "mixed.json"
    src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out = str(tmp_path / "mixed.ifc")
    stats = ifc_builder.build(str(src), out, connect=True)
    assert stats["status"] == "verified"
    assert stats["materials"]["콘크리트"] == 1 and stats["materials"]["조적"] == 1
    model = ifcopenshell.open(out)
    got = {}
    for wall in model.by_type("IfcWall"):
        usage = ifcopenshell.util.element.get_material(wall)
        if usage is not None and usage.is_a("IfcMaterialLayerSetUsage"):
            eid = ifcopenshell.util.element.get_pset(wall, "Pset_MEPParser")["EID"]
            got[eid] = [layer.Material.Name if layer.Material else None
                        for layer in usage.ForLayerSet.MaterialLayers]
    assert got == {"wall:a": ["콘크리트"], "wall:b": ["조적"]}


def test_an_undeclared_wall_material_stays_empty_instead_of_becoming_concrete(tmp_path):
    """`material=` 은 적힌 것만 붙는다 — 종전 `_wall_type` 은 전부 'Concrete' 로 하드코딩했다."""
    import ifcopenshell
    import ifcopenshell.util.element

    ifc_builder = _ifc_builder()
    data = _geometry()
    data["elements"]["wall"][0].pop("material")
    src = tmp_path / "bare.json"
    src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out = str(tmp_path / "bare.ifc")
    assert ifc_builder.build(str(src), out, connect=True)["status"] == "verified"
    model = ifcopenshell.open(out)
    assert not [m for m in model.by_type("IfcMaterial") if m.Name == "Concrete"]
    wall = next(w for w in model.by_type("IfcWall")
                if ifcopenshell.util.element.get_pset(w, "Pset_MEPParser")["EID"] == "wall:a")
    usage = ifcopenshell.util.element.get_material(wall)
    assert [layer.Material for layer in usage.ForLayerSet.MaterialLayers] == [None]


def test_a_record_that_could_not_be_built_blocks_the_file_and_the_receipt_names_it(tmp_path):
    """못 만든 레코드 하나가 납품을 막는 건 옳다 — 다만 **수만** 세면 어느 부재를 고칠지 알 수 없다."""
    ifc_builder = _ifc_builder()
    data = _geometry()
    data["elements"]["zone"][0]["closed"] = False        # 닫히지 않은 zone 은 세울 수 없다
    src = tmp_path / "broken.json"
    src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "broken.ifc"
    with pytest.raises(ValueError, match="V106"):
        ifc_builder.build(str(src), str(out))
    assert not out.exists()
    receipt = json.loads((tmp_path / "broken.build.json").read_text(encoding="utf-8"))
    assert receipt["unbuilt"]["zone"]["count"] == 1
    detail = receipt["unbuilt"]["zone"]["detail"][0]
    assert detail["eid"] == "zone:a" and detail["layer"] == "A-ZONE"
    assert detail["reason"] == "not_a_closed_polyline"


def _one_wall(**opening):
    """벽 하나(5m, 두께·높이는 params 에서) + 선택적 개구부 하나. 리뷰 재현용 최소 모델."""
    data = {"source": "t.dxf", "units": "mm", "scale_applied": 1.0, "contract": GC.contract_block(),
            "params": {"wall": {"width": 200, "height": 2800}},
            "floors": [{"z": 0.0, "label": "Level_1"}],
            "elements": {"wall": [{"eid": "wall:a", "kind": "polyline", "layer": "A-WALL",
                                   "centerline": [[0, 0], [5000, 0]]}]}}
    if opening:
        op = {"eid": "op:x", "kind": "circle", "center": [2500, 0], "radius": 450,
              "layer": "A-OPEN", "wall_indices": [0], "host_width": 200}
        op.update(opening)
        data["elements"]["opening"] = [op]
    return data


def _wall_volume(path):
    import ifcopenshell
    import ifcopenshell.geom
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    wall = ifcopenshell.open(path).by_type("IfcWall")[0]
    shape = ifcopenshell.geom.create_shape(settings, wall)
    verts, faces = shape.geometry.verts, shape.geometry.faces
    volume = 0.0
    for i in range(0, len(faces), 3):
        a, b, c = (verts[3 * j:3 * j + 3] for j in faces[i:i + 3])
        volume += (a[0] * (b[1] * c[2] - b[2] * c[1]) + a[1] * (b[2] * c[0] - b[0] * c[2])
                   + a[2] * (b[0] * c[1] - b[1] * c[0]))
    return abs(volume / 6) * 1e9


def test_a_stacked_build_keeps_each_floors_measured_wall_thickness_and_wall_height(tmp_path):
    """층 조립이 치수를 층마다 얼릴 때 두 가지를 잃었다. ① `_dim` 이 params 를 실측(`width_detected`)
    위에 두어 250mm 벽이 200mm 로 섰다 ② 합친 모델에 params 가 없어 4m 벽의 일반 개구부가 2.9m 에서
    멈췄다. 둘 다 검사가 같은 틀린 수치로 계산해 `verified` 로 나갔다."""
    ifc_builder = _ifc_builder()
    data = _one_wall(width=900)                       # subtype 없음 = 벽 높이 전체를 뚫는 개구부
    data.pop("floors")
    data["params"]["wall"]["height"] = 4000
    data["elements"]["wall"][0]["width_detected"] = 250.0
    src = tmp_path / "f.json"
    src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out = str(tmp_path / "multi.ifc")
    stats = ifc_builder.build_multi([{"geometry": str(src), "storey": "1F", "z": 0}], out)[0]
    assert stats["status"] == "verified"
    removed = sum(c["removed_volume_mm3"] for o in stats["opening_results"] for c in o["cuts"])
    assert removed == pytest.approx(900 * 250 * 4000)          # 실측 두께 × 그 층의 벽 높이 전체
    assert _wall_volume(out) == pytest.approx(5000 * 250 * 4000 - 900 * 250 * 4000, rel=1e-6)


def test_an_edited_opening_is_cut_where_the_preview_draws_it(tmp_path):
    """수동 편집은 `overrides` 로 온다(`element_id.apply_edits`). 종전 빌더는 최상위 값만 읽어,
    미리보기에서 2.4m 로 고친 창이 납품 IFC 에서는 1.2m 로 뚫렸다."""
    ifc_builder = _ifc_builder()
    data = _one_wall(subtype="window", width=1200, height=1200, sill=900,
                     overrides={"height": 2400.0, "sill": 0.0})
    src = tmp_path / "edited.json"
    src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out = str(tmp_path / "edited.ifc")
    stats = ifc_builder.build(str(src), out)
    assert stats["status"] == "verified"
    assert _wall_volume(out) == pytest.approx(5000 * 200 * 2800 - 1200 * 200 * 2400, rel=1e-6)


def test_an_opening_that_would_empty_its_wall_is_refused_by_name(tmp_path):
    """벽 조각 전체를 덮는 개구부를 선언하면 ifcopenshell 은 빈 불리언을 조용히 버려 벽이 안 뚫린 채
    나가고, 재검사는 "부피가 0 과 다르다" 는 엉뚱한 말을 했다. FreeCAD 경로처럼 이름을 적고 막는다."""
    ifc_builder = _ifc_builder()
    data = _one_wall(width=6000)                      # 5m 벽보다 넓은 일반 개구부
    src = tmp_path / "consumed.json"
    src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "consumed.ifc"
    with pytest.raises(ValueError, match="Opening cut nothing"):
        ifc_builder.build(str(src), str(out))
    assert not out.exists()
    receipt = json.loads((tmp_path / "consumed.build.json").read_text(encoding="utf-8"))
    assert receipt["runtime_errors"]                  # 트레이스백이 아니라 영수증에 사유가 남는다
    failed = receipt["opening_results"][0]["failed_hosts"]
    assert failed and failed[0]["error"] == "cut would consume the whole host"


def test_a_footprint_tray_stands_on_the_tray_section_not_the_duct_one(tmp_path):
    ifc_builder = _ifc_builder()
    data = _one_wall()
    data["params"]["tray"] = {"height_mm": 100.0}
    data["elements"]["tray"] = [{"eid": "tray:fp", "kind": "polyline", "closed": True, "layer": "E-TRAY",
                                 "geometry_mode": "footprint", "elevation": 2000.0,
                                 "points": [[0, 1000], [4000, 1000], [4000, 1300], [0, 1300]]}]
    stats, _out = _build(tmp_path, data, "fptray")
    assert stats["status"] == "verified"
    tray = next(e for e in stats["expected_products"] if "tray:fp" in e["source_eids"])
    assert tray["volume_mm3"] == pytest.approx(4000 * 300 * 100)
    assert tray["bbox_mm"][2] == pytest.approx(1950) and tray["bbox_mm"][5] == pytest.approx(2050)


def test_the_receipt_carries_the_model_bbox_so_the_runaway_solid_check_runs(tmp_path):
    """V104(폭주 솔리드)는 영수증의 `bbox` 가 있어야 돈다. 종전 빌더는 `None` 으로 박아 꺼 두었다."""
    import verify
    stats, out = _build(tmp_path)
    assert len(stats["bbox"]) == 6 and stats["bbox"][3] - stats["bbox"][0] > 8000
    forged = dict(stats, bbox=[b * 10 for b in stats["bbox"]])
    assert "V104" in {f.id for f in verify.verify_build(_geometry(), forged, out, stage="post_export").findings}


def test_a_footprint_duct_is_left_out_of_the_mep_ratio_but_its_volume_is_still_checked(tmp_path):
    """외곽선 부재는 기대·실제 부피를 같은 식으로 셈해 퇴화 비율(`mep_volume`)에 넣으면 늘 1 이고,
    축선 부재의 종잇장 사고를 합계로 가린다. 대신 제품마다 V107 이 IFC 에서 부피를 잰다 — 그걸 잠근다."""
    import artifact_validation as AV
    data = _one_wall()
    data["elements"]["duct"] = [{"eid": "duct:fp", "kind": "polyline", "closed": True, "layer": "M-DUCT",
                                 "geometry_mode": "footprint", "elevation": 2400.0, "height_mm": 150.0,
                                 "points": [[0, 1000], [4000, 1000], [4000, 1600], [0, 1600]]}]
    stats, out = _build(tmp_path, data, "fpduct")
    assert stats["mep_volume"] == {"expected_mm3": 0.0, "built_mm3": 0.0}
    duct = next(e for e in stats["expected_products"] if "duct:fp" in e["source_eids"])
    duct["volume_mm3"] *= 0.5                         # 반쪽짜리 덕트를 약속했다고 치면
    errors, _mapping = AV.inspect_ifc(data, stats, out)
    assert any("volume" in e for e in errors), errors


def test_round_sections_ship_as_flat_faced_polygons_so_the_re_reader_sees_a_manifold(tmp_path):
    """`Arch.makePipe`(곡면)가 V107 비다양체를 낸 그 자리다 — 여기는 정다각형 마이터 관만 나간다."""
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.util.element

    _stats, out = _build(tmp_path)
    model = ifcopenshell.open(out)
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    for pipe in model.by_type("IfcPipeSegment"):
        shape = ifcopenshell.geom.create_shape(settings, pipe)
        faces = shape.geometry.faces
        edges = {}
        for i in range(0, len(faces), 3):
            tri = faces[i:i + 3]
            for a, b in zip(tri, tri[1:] + tri[:1]):
                key = tuple(sorted((a, b)))
                edges[key] = edges.get(key, 0) + 1
        bad = [k for k, n in edges.items() if n != 2]
        eid = ifcopenshell.util.element.get_pset(pipe, "Pset_MEPParser")["EID"]
        assert not bad, f"{eid}: 변 {len(bad)}개가 두 면에 안 물렸다"
    assert not model.by_type("IfcSweptDiskSolid")      # 곡면 경로 솔리드가 새로 들어오면 여기서 걸린다
