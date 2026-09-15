"""한 층에 공종 도면 여러 장 — 건축은 한 원본에서만, 설비는 각 도면에서, 층은 하나.

설비 도면은 건축 배경(XREF)을 물고 있어 그대로 합치면 벽이 도면 수만큼 겹치고, 층을 원본마다
만들면 같은 높이에 층이 둘 서서 벽·덕트가 서로 다른 층으로 갈린다(간섭을 못 본다)."""
import json

import ezdxf
import pytest

import geom_contract as GC
import stack_build as SB
import verify as V
from project_store import ProjectStore, RevisionConflict


def _drawing(path, extra_layer=None, dx=0.0):
    doc = ezdxf.new(units=4)
    for y in (0, 200):
        doc.modelspace().add_line((dx, y), (dx + 5000, y), dxfattribs={"layer": "WALL"})
    doc.modelspace().add_line((dx + 1000, -1500), (dx + 1000, 1700), dxfattribs={"layer": "PIPE"})
    if extra_layer:
        doc.modelspace().add_line((dx + 2500, -1500), (dx + 2500, 1700), dxfattribs={"layer": extra_layer})
    doc.saveas(path)
    return str(path)


def _spec(arch, mep, **mep_level):
    return {"levels": [{"id": "A", "source": arch, "z": 0, "floor": "L1"},
                       dict({"id": "B", "source": mep, "z": 0, "floor": "L1", "categories": ["duct"]}, **mep_level)]}


def test_sources_on_one_floor_share_one_storey_and_keep_only_their_categories(tmp_path):
    arch, mep = _drawing(tmp_path / "arch.dxf"), _drawing(tmp_path / "mep.dxf", "DUCT")
    g = SB.build_stack(_spec(arch, mep))
    assert g["floors"] == [{"z": 0.0, "label": "L1", "id": "L1", "sources": ["A", "B"]}]
    el = g["elements"]
    assert el["wall"] and {w["level"] for w in el["wall"]} == {"A"}          # 벽은 건축 원본에서만
    assert {p["level"] for p in el["pipe"]} == {"A"}                        # 설비 도면의 배관은 담지 않았다
    assert el["duct"] and all(d["eid"].startswith("B:") for d in el["duct"])
    level_b = g["stack"]["levels"][1]
    assert level_b["floor"] == "L1" and level_b["excluded"]["wall"] == len(el["wall"])
    report = V.verify_geometry(g).to_dict()
    assert not [f for f in report["findings"] if f["id"] in ("V001", "V002")], report


def test_same_floor_sources_must_share_z_and_openings_need_their_walls(tmp_path):
    arch, mep = _drawing(tmp_path / "arch.dxf"), _drawing(tmp_path / "mep.dxf", "DUCT")
    with pytest.raises(SB.StackError, match="z"):
        SB.build_stack(_spec(arch, mep, z=3000))
    with pytest.raises(SB.StackError, match="개구부"):
        SB.build_stack(_spec(arch, mep, categories=["opening", "duct"]))
    with pytest.raises(SB.StackError, match="categories"):
        SB.build_stack(_spec(arch, mep, categories=["ductt"]))


def test_a_same_floor_drawing_that_does_not_overlap_is_reported(tmp_path):
    arch, far = _drawing(tmp_path / "arch.dxf"), _drawing(tmp_path / "far.dxf", "DUCT", dx=100000)
    g = SB.build_stack(_spec(arch, far))
    assert any("겹치지 않는다" in w for w in g["warnings"]), g["warnings"]


def test_floor_membership_is_by_name_or_by_source():
    floor = {"z": 0, "label": "L1", "id": "L1", "sources": ["A", "B"]}
    assert GC.floor_has_level(floor, "L1") and GC.floor_has_level(floor, "B")
    assert not GC.floor_has_level(floor, "C") and not GC.floor_has_level({"label": "L1"}, "B")


def test_adding_a_drawing_keeps_edits_uses_mep_only_and_guards_revision(tmp_path):
    from project_server import ProjectSession
    arch, mep = _drawing(tmp_path / "arch.dxf"), _drawing(tmp_path / "mep.dxf", "DUCT")
    session = ProjectSession(ProjectStore(tmp_path / "unit.mep").create([{"id": "main", "path": arch}]))
    first = session.state()
    walls = first["geometry"]["elements"]["wall"]
    saved = session.edit(walls[0]["eid"], {"overrides": {"height": 3210}}, 0, first["project_id"])

    with pytest.raises(ValueError):
        session.add_source(tmp_path / "missing.dxf", saved["revision"], first["project_id"])
    assert session.store.read()["revision"] == saved["revision"]            # 실패한 후보는 프로젝트를 안 바꾼다
    with pytest.raises(RevisionConflict):
        session.add_source(mep, 0, first["project_id"])

    added = session.add_source(mep, saved["revision"], first["project_id"])
    g = added["geometry"]
    assert added["revision"] == saved["revision"] + 1
    assert g["floors"] == [{"z": 0.0, "label": "Level_1", "id": "main", "sources": ["main", "src2"]}]
    assert len(g["elements"]["wall"]) == len(walls)                          # 벽이 도면 수만큼 겹치지 않는다
    edited = next(w for w in g["elements"]["wall"] if w["eid"] == "main:" + walls[0]["eid"])
    assert edited["overrides"]["height"] == 3210                             # 기존 수정이 그대로 붙는다
    assert g["elements"]["duct"] and all(d["eid"].startswith("src2:") for d in g["elements"]["duct"])
    assert {p["level"] for p in g["elements"]["pipe"]} == {"main", "src2"}    # 설비는 두 도면 모두에서 담는다
    reopened = ProjectSession(ProjectStore(session.store.folder)).state()
    assert reopened["revision"] == added["revision"] and json.dumps(reopened["geometry"]["floors"]) == json.dumps(g["floors"])
