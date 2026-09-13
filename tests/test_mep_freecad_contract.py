from unittest.mock import MagicMock, patch
import pytest
from test_builder import FB


def test_pipe_uses_reviewed_outer_diameter_and_axis():
    rec = {'points': [[0, 0], [1000, 0]], 'diameter': 100,
           'elevation': 0, 'overrides': {'width': 15.9, 'elevation': 77.95}}
    axis = MagicMock()
    with patch.object(FB, 'make_wire', return_value=axis), patch.object(FB, 'set_ifc_props'):
        FB.build_mep(MagicMock(), {'pipe': [rec]})
        assert FB.Arch.makePipe.call_args.kwargs['diameter'] == 15.9
        assert axis.Placement.Base.z == 77.95


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
