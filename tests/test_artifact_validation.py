"""Artifact gates use the exported file, not success wrappers or entity counts."""
import copy
import json
from pathlib import Path
import sys
import unittest

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import geom_contract as GC
import verify


def geometry():
    return {"contract": GC.contract_block(), "project": {"project_id": "p", "revision": 7},
            "elements": {"wall": [{"eid": "wall:one", "kind": "polyline", "closed": False,
                "points": [[0, 0], [3000, 0]], "width_detected": 80,
                "overrides": {"width": 200, "height": 3000}}]}}


def _ifc_builder():
    """ifcopenshell 은 선택 의존성이다(Bonsai 안에 들어 있다). 없는 PC 에서 이 테스트가
    **에러로** 죽으면 `build_exe.bat` 의 게이트가 통째로 막혀 .exe 를 못 만든다 —
    실측: 깨끗한 Python 3.11 에서 17건이 그렇게 터져 빌드가 중단됐다. `test_ifc4d.py`
    와 같은 규약으로 건너뛴다(pytest 도 run_all.py 도 SkipTest 를 skip 으로 센다).
    `ifc_builder` 는 의존성이 없으면 `sys.exit(1)` 하므로 SystemExit 도 함께 받는다."""
    try:
        import ifc_builder
    except (ImportError, SystemExit) as exc:
        raise unittest.SkipTest(f'ifcopenshell 없음 — IFC 산출물 검사 미실행 ({exc})')
    return ifc_builder


def test_missing_stats_and_unbuilt_cannot_pass():
    assert verify.verify_build(geometry(), {}).failed
    assert verify.verify_build(geometry(), {"unbuilt": {"beam": {"count": 1}}}).failed


def test_circle_column_is_valid_preflight_geometry():
    data = geometry()
    data["elements"] = {"column": [{"eid": "col:c", "kind": "circle", "center": [0, 0], "radius": 200}]}
    assert not verify.verify_geometry(data).failed


def test_simple_ifc_rejects_nonempty_unsupported_category_before_writing(tmp_path):
    ifc_builder = _ifc_builder()
    data = geometry()
    data["elements"]["pipe"] = [{"eid": "pipe:p", "points": [[0, 0], [100, 0]]}]
    src = tmp_path / "g.json"
    src.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "out.ifc"
    receipt = tmp_path / "out.build.json"
    receipt.write_text(json.dumps({"status": "verified", "provenance": {"run_id": "old"}}), encoding="utf-8")
    with pytest.raises(ValueError, match="FreeCAD"):
        ifc_builder.build(str(src), str(out))
    assert not out.exists()
    failed = json.loads(receipt.read_text(encoding="utf-8"))
    assert failed["status"] == "failed"
    assert failed["provenance"]["run_id"] != "old"
    assert failed["artifacts"]["ifc"]["path"] is None


def test_simple_export_properties_mapping_and_contract_dimensions(tmp_path):
    ifc_builder = _ifc_builder()
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.util.element
    data = geometry()
    data["elements"]["wall"][0]["points"].append([3000, 2000])
    src = tmp_path / "g.json"
    src.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "out.ifc"
    stats = ifc_builder.build(str(src), str(out))
    assert stats["artifacts"]["ifc"]["status"] == "verified"
    model = ifcopenshell.open(str(out))
    walls = model.by_type("IfcWall")
    assert len(stats["eid_map"]["wall:one"]) == 2
    for wall in walls:
        p = ifcopenshell.util.element.get_pset(wall, "Pset_MEPParser")
        assert p["EID"] == "wall:one"
        assert json.loads(p["SourceEIDs"]) == ["wall:one"]
        assert p["RunId"] == stats["provenance"]["run_id"]
        assert p["InputSHA256"] == stats["provenance"]["input_sha256"]
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    shape = ifcopenshell.geom.create_shape(settings, walls[0])
    ys = shape.geometry.verts[1::3]
    assert min(ys) == pytest.approx(-0.1)
    assert max(ys) == pytest.approx(0.1)


def test_exported_ifc_tampering_is_rejected(tmp_path):
    ifc_builder = _ifc_builder()
    import ifcopenshell
    import ifcopenshell.util.element
    data = geometry()
    src = tmp_path / "g.json"
    src.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "out.ifc"
    stats = ifc_builder.build(str(src), str(out))
    assert not verify.verify_build(data, stats, str(out), stage="post_export").failed
    model = ifcopenshell.open(str(out))
    wall = model.by_type("IfcWall")[0]
    wall.Representation = None
    model.write(str(out))
    assert verify.verify_build(data, stats, str(out), stage="post_export").failed


def test_post_export_requires_actual_file_even_if_stats_claim_success(tmp_path):
    assert verify.verify_build(geometry(), {"verify": {"status": "ok"}},
                               str(tmp_path / "missing.ifc"), stage="post_export").failed


def test_simple_circle_and_slab_follow_contract(tmp_path):
    ifc_builder = _ifc_builder()
    import ifcopenshell
    import ifcopenshell.geom
    data = geometry()
    data["elements"] = {
        "column": [{"eid": "col:c", "kind": "circle", "center": [1000, 2000], "radius": 200,
                    "height": 3200}],
        "slab": [{"eid": "slab:s", "kind": "polyline", "closed": True,
                  "points": [[0, 0], [3000, 0], [3000, 3000], [0, 3000]],
                  "z_base": 0, "thickness": 350}]}
    src = tmp_path / "g.json"
    src.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "out.ifc"
    ifc_builder.build(str(src), str(out))
    model = ifcopenshell.open(str(out))
    assert len(model.by_type("IfcCircleProfileDef")) == 1
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    shape = ifcopenshell.geom.create_shape(settings, model.by_type("IfcSlab")[0])
    assert min(shape.geometry.verts[2::3]) == pytest.approx(-0.35)
    assert max(shape.geometry.verts[2::3]) == pytest.approx(0)


def test_connected_simple_wall_is_centered(tmp_path):
    ifc_builder = _ifc_builder()
    import ifcopenshell
    import ifcopenshell.geom
    src, out = tmp_path / "g.json", tmp_path / "out.ifc"
    src.write_text(json.dumps(geometry()), encoding="utf-8")
    ifc_builder.build(str(src), str(out), connect=True)
    model = ifcopenshell.open(str(out))
    settings = ifcopenshell.geom.settings()
    settings.set(settings.USE_WORLD_COORDS, True)
    shape = ifcopenshell.geom.create_shape(settings, model.by_type("IfcWall")[0])
    assert min(shape.geometry.verts[1::3]) == pytest.approx(-0.1)
    assert max(shape.geometry.verts[1::3]) == pytest.approx(0.1)


def test_runner_records_startup_failure_and_rejects_old_receipt(tmp_path):
    from freecad_runner import run_build
    builder = tmp_path / "builder.py"
    builder.write_text("import sys\nprint('startup')\nprint('fatal import', file=sys.stderr)\nraise RuntimeError('broken')\n", encoding="utf-8")
    geom = tmp_path / "g.json"
    geom.write_text(json.dumps(geometry()), encoding="utf-8")
    receipt = tmp_path / "out.build.json"
    receipt.write_text(json.dumps({"provenance": {"run_id": "old"}, "status": "verified",
                                  "artifacts": {"ifc": {"status": "verified", "path": "old.ifc"}}}), encoding="utf-8")
    result = run_build(sys.executable, str(geom), str(tmp_path / "out"), str(builder))
    stats = json.loads(receipt.read_text(encoding="utf-8"))
    assert stats["provenance"]["run_id"] == result.run_id
    assert stats["status"] == "failed"
    assert stats["artifacts"]["ifc"]["path"] is None
    assert "fatal import" in stats["runtime"]["stderr"]


@pytest.mark.parametrize("key", ["ReviewReason", "Layer", "Level", "ProjectId", "Revision"])
def test_qa_property_tampering_is_rejected(tmp_path, key):
    ifc_builder = _ifc_builder()
    import ifcopenshell
    import ifcopenshell.api.pset
    import ifcopenshell.util.element
    data = geometry()
    src, out = tmp_path / "g.json", tmp_path / "out.ifc"
    src.write_text(json.dumps(data), encoding="utf-8")
    stats = ifc_builder.build(str(src), str(out))
    model = ifcopenshell.open(str(out))
    pset = ifcopenshell.util.element.get_pset(model.by_type("IfcWall")[0], "Pset_MEPParser")
    ifcopenshell.api.pset.edit_pset(model, pset=model.by_id(pset["id"]), properties={key: "wrong"})
    model.write(str(out))
    report = verify.verify_build(data, stats, str(out))
    assert report.failed
    assert any(key in finding.message for finding in report.errors)


def test_same_elevation_storeys_use_explicit_level_and_validate_name(tmp_path):
    ifc_builder = _ifc_builder()
    import ifcopenshell
    data = geometry()
    data["floors"] = [{"label": "East", "z": 0}, {"label": "West", "z": 0}]
    data["elements"]["wall"][0]["level"] = "East"
    src, out = tmp_path / "g.json", tmp_path / "out.ifc"
    src.write_text(json.dumps(data), encoding="utf-8")
    stats = ifc_builder.build(str(src), str(out))
    model = ifcopenshell.open(str(out))
    wall = model.by_type("IfcWall")[0]
    wall.ContainedInStructure[0].RelatingStructure = next(s for s in model.by_type("IfcBuildingStorey") if s.Name == "West")
    model.write(str(out))
    assert verify.verify_build(data, stats, str(out)).failed


@pytest.mark.parametrize("key", ["orphaned", "ambiguous", "conflicts"])
def test_unresolved_edits_block_preflight_and_simple_ifc(tmp_path, key):
    ifc_builder = _ifc_builder()
    data = geometry()
    data["edits_report"] = {key: ["wall:unapplied"]}
    assert verify.verify_geometry(data).failed
    src, out = tmp_path / "g.json", tmp_path / "out.ifc"
    src.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="preflight"):
        ifc_builder.build(str(src), str(out))
    assert not out.exists()


def test_equal_volume_translated_ifc_is_rejected(tmp_path):
    ifc_builder = _ifc_builder()
    import ifcopenshell
    data = geometry()
    src, out = tmp_path / "g.json", tmp_path / "out.ifc"
    src.write_text(json.dumps(data), encoding="utf-8")
    stats = ifc_builder.build(str(src), str(out))
    model = ifcopenshell.open(str(out))
    wall = model.by_type("IfcWall")[0]
    wall.ObjectPlacement.RelativePlacement.Location.Coordinates = (1., 0., 0.)
    model.write(str(out))
    report = verify.verify_build(data, stats, str(out))
    assert report.failed
    assert any("bounds" in item.message for item in report.errors)
