import os
from unittest.mock import MagicMock, patch
import pytest
from test_builder import FB


def test_pipe_uses_reviewed_outer_diameter_and_axis():
    """`Arch.makePipe`(곡면)는 안 쓴다 — 평면 직선도 마이터 링(`_rect_solid`)으로 짓는다
    (V107: 곡면 있는 IFC 형상을 `ifcopenshell` 이 재검증 때 다시 삼각분할하면 이음매가 하나도
    안 맞아 메시 전체가 비다양체가 됐다 — 최소 재현은 `docs/decisions/mep-geometry.md`).
    overrides 가 지름·높이를 여전히 이긴다는 계약만 여기서 잠근다."""
    rec = {'points': [[0, 0], [1000, 0]], 'diameter': 100,
           'elevation': 0, 'overrides': {'width': 15.9, 'elevation': 77.95}}
    with patch.object(FB, 'set_ifc_props'), patch.object(FB, '_rect_solid') as rect_solid:
        rect_solid.return_value = MagicMock(isValid=lambda: True, Volume=1.0)
        FB.build_mep(MagicMock(), {'pipe': [rec]})
        route, width, height = rect_solid.call_args.args[:3]
        assert width == height == 15.9
        assert all(p[2] == 77.95 for p in route)
        assert rect_solid.call_args.kwargs['sides'] == FB.GC.ROUND_SIDES


def test_outline_duct_extrudes_area_with_holes_instead_of_perimeter_sweep():
    rec = {'points': [[0,0],[1000,0],[1000,200],[0,200]], 'closed': True,
           'centerline': [[0,100],[1000,100]],
           'holes': [[[100,50],[200,50],[200,150],[100,150]]],
           'geometry_mode': 'footprint', 'width_mm':200, 'height_mm':60, 'elevation':2570}
    shape=MagicMock()
    shape.Volume=190000*60
    with patch.object(FB, '_footprint_solid', return_value=shape) as extrude, patch.object(FB, '_rect_solid') as sweep, patch.object(FB, 'set_ifc_props'):
        FB.build_mep(MagicMock(), {'duct': [rec]})
        sweep.assert_not_called()
        assert extrude.call_args.args[1:] == (2540, 60)
        assert FB.MEP_VOLUME['expected_mm3'] == pytest.approx(190000*60)


def test_closed_duct_centerline_includes_the_last_segment():
    rec = {'points': [[0,0],[1000,0],[1000,1000],[0,1000]], 'closed':True,
           'geometry_mode':'centerline','width_mm':100,'height_mm':60}
    shape=MagicMock()
    shape.Volume=4000*100*60
    with patch.object(FB, '_rect_solid', return_value=shape) as sweep, patch.object(FB, 'set_ifc_props'):
        FB.build_mep(MagicMock(), {'duct':[rec]})
        # 계약 v3: 사각 관은 절대 3D 경로(닫힌 경로는 첫 점으로 되돌아온다)를 받는다.
        assert sweep.call_args.args[0] == [p + [0.0] for p in rec['points'] + [rec['points'][0]]]
        assert FB.MEP_VOLUME['expected_mm3'] == 4000*100*60


def test_equipment_uses_reviewed_height():
    rec={'points':[[0,0],[500,0],[500,400],[0,400]], 'closed':True,
         'elevation':120,'overrides':{'height':730}}
    with patch.object(FB, '_equip_solid', return_value=MagicMock()) as extrude, patch.object(FB, 'set_ifc_props'):
        FB.build_mep(MagicMock(), {'equipment':[rec]})
        assert extrude.call_args.args[1:] == (120,730)


def test_an_edited_elevation_that_crosses_a_floor_boundary_moves_the_ifc_storey_too():
    """편집한 elevation 이 층 경계를 넘으면 형상만 옮기고 IFC storey 는 옛 층에 남던 버그.

    (`_story_owner`/`_at_floor`/이음 레코드가 rec['elevation'] 을 원값으로 읽어 overrides 를 무시했다.)
    """
    import json
    import tempfile
    import geom_contract as GC
    from test_builder import _build, _skip_if_no_freecad
    _skip_if_no_freecad()
    g = {"source": "t.dxf", "units": "mm", "params": {}, "contract": GC.contract_block(),
         "floors": [{"z": 0.0, "label": "Level_1"}, {"z": 3000.0, "label": "Level_2"}],
         "elements": {"pipe": [
             {"eid": "p:ground", "kind": "polyline", "points": [[0, 5000], [1000, 5000]],
              "elevation": 0.0, "diameter": 20.0},                                   # Level_1 을 비우지 않는다
             {"eid": "p:moved", "kind": "polyline", "points": [[0, 0], [1000, 0]],
              "elevation": 0.0, "diameter": 20.0, "overrides": {"elevation": 3200.0}}]}}
    directory = tempfile.mkdtemp(prefix="mep_floor_elev_")
    geom = os.path.join(directory, "geometry.json")
    with open(geom, "w", encoding="utf-8") as stream:
        json.dump(g, stream)
    _log, st = _build(geom, os.path.join(directory, "out"))
    assert st["status"] == "verified", st
    assert st["built"]["floors"] == 2 and st["built"]["mep"] == 2
    import ifcopenshell
    import ifcopenshell.util.element
    model = ifcopenshell.open(st["artifacts"]["ifc"]["path"])
    by_eid = {ifcopenshell.util.element.get_pset(p, "Pset_MEPParser")["EID"]: p
              for p in model.by_type("IfcPipeSegment")}
    storey_of = lambda eid: by_eid[eid].ContainedInStructure[0].RelatingStructure.Name
    assert storey_of("p:ground") == "Level_1"
    assert storey_of("p:moved") == "Level_2"        # elevation 3200 > 두 층 z 를 다 지나 위층에 든다


def test_a_single_straight_round_pipe_builds_a_manifold_ifc_shape():
    """★ V107 최소 재현이 다시 나오면 여기서 잡는다.

    한때 "평면 직선 하나뿐인 배관은 안전하다"고 보고 그 경우만 `Arch.makePipe`(곡면)를 썼는데,
    실제로는 그 경우가 V107(비다양체 메시)이었다 — 기울임·이음·fitting 없이, 배관 딱 1개만으로도
    재현된다. mock 으로는 못 잠근다(`ifcopenshell` 이 실제 IFC 를 다시 삼각분할해야 드러난다) — 반드시
    실제 freecadcmd 빌드 + IFC 재검사로 확인한다. 경위: docs/decisions/mep-geometry.md '원형 단면'."""
    import json
    import tempfile
    import geom_contract as GC
    from test_builder import _build, _skip_if_no_freecad
    _skip_if_no_freecad()
    g = {"source": "t.dxf", "units": "mm", "params": {}, "contract": GC.contract_block(),
         "elements": {"pipe": [{"eid": "p:1", "kind": "polyline",
                                 "points": [[0, 0], [1000, 0]], "elevation": 500.0, "diameter": 20.0}]}}
    directory = tempfile.mkdtemp(prefix="mep_round_pipe_")
    geom = os.path.join(directory, "geometry.json")
    with open(geom, "w", encoding="utf-8") as stream:
        json.dump(g, stream)
    _log, st = _build(geom, os.path.join(directory, "out"))
    assert st["status"] == "verified", st.get("verify_ifc") or st


def test_declaration_basis_reaches_the_ifc_pset():
    """프로젝트 기본값이 채운 필드는 Pset_MEPParser.DeclarationBasis 로 나간다."""
    obj = MagicMock()
    rec = {"eid": "p:1", "material": "PB", "declaration_basis": {"material": "project_default"}}
    with patch.object(FB, "PROVENANCE", {}):
        FB.set_ifc_props(obj, rec)
    assert obj.IfcProperties["DeclarationBasis"] == 'Pset_MEPParser;;IfcText;;{"material": "project_default"}'


def test_no_declaration_basis_is_still_a_present_empty_object():
    """뷰어에서 항상 같은 속성 이름을 기대할 수 있게 — Assumptions 와 같은 규약(빈 값도 낸다)."""
    obj = MagicMock()
    with patch.object(FB, "PROVENANCE", {}):
        FB.set_ifc_props(obj, {"eid": "p:1"})
    assert obj.IfcProperties["DeclarationBasis"] == 'Pset_MEPParser;;IfcText;;{}'
