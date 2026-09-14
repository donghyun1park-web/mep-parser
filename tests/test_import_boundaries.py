"""Public synthetic regressions for source-preserving DXF import boundaries."""
import math

import ezdxf
import pytest
from shapely.geometry import Polygon

import dxf_parser as dp
from mep_paths import extract_curve
from mep_profile import inspect_mep_source
from verify import verify_geometry


@pytest.mark.parametrize('normal, expected_x, expected_z', [
    ((0, 0, 1), 100, 25), ((0, 0, -1), -100, -25),
])
@pytest.mark.parametrize('bulge', [1, -1])
def test_classic_polyline_preserves_bulge_ocs_elevation(normal, expected_x, expected_z, bulge):
    doc = ezdxf.new()
    e = doc.modelspace().add_polyline2d([(0, 0), (100, 0)],
        dxfattribs={'elevation': (0, 0, 25), 'extrusion': normal})
    e.vertices[0].dxf.bulge = bulge
    rec = extract_curve(e)
    assert rec['points'][0] == pytest.approx([0, 0])
    assert rec['points'][-1] == pytest.approx([expected_x, 0])
    assert rec['source_elevation_mm'] == expected_z
    assert rec['source_length_mm'] == pytest.approx(50 * math.pi)
    assert rec['path3d_basis'] == 'analytic_source'
    assert rec['source_refs'][0]['handle'] == e.dxf.handle


def test_repeated_vertex_does_not_shift_polyline_primitive_direction():
    e = ezdxf.new().modelspace().add_polyline2d([(0, 0), (0, 0), (100, 0), (100, 100)])
    rec = extract_curve(e)
    assert rec['points'] == [[0, 0], [100, 0], [100, 100]]
    assert rec['source_length_mm'] == 200


def test_inventory_keeps_good_layers_when_mesh_is_not_a_centerline(tmp_path):
    doc = ezdxf.new(); doc.units = 4
    m = doc.modelspace()
    m.add_polyline2d([(0, 0), (20, 0), (20, 10)], close=True, dxfattribs={'layer': 'VALVE'})
    m.add_line((100, 0), (500, 0), dxfattribs={'layer': 'SUPPLY'})
    m.add_polymesh((2, 2), dxfattribs={'layer': 'MESH'})
    path = tmp_path / 'drawing.dxf'; doc.saveas(path)
    result = inspect_mep_source(path)
    rows = {r['name']: r for r in result['layers']}
    assert rows['VALVE']['bounds_mm'] == [0, 0, 20, 10]
    assert rows['SUPPLY']['bounds_mm'] == [100, 0, 500, 0]
    assert any(i['code'] == 'SOURCE_CURVE' and i['source_refs'][0]['type'] == 'POLYLINE'
               for i in result['issues'])


def outline(msp, pts, z=0):
    for a, b in zip(pts, pts[1:] + pts[:1]):
        msp.add_line((*a, z), (*b, z), dxfattribs={'layer': 'STRUCT_COLUMN'})


def parse_columns(doc, tmp_path):
    doc.units = 4
    path = tmp_path / 'drawing.dxf'; doc.saveas(path)
    return dp.parse(str(path), [('STRUCT_COLUMN', 'column', {})], block_rules=[], use_ai=False)


def test_separate_columns_on_one_layer_do_not_fill_space_between(tmp_path):
    doc = ezdxf.new(); m = doc.modelspace()
    outline(m, [(0, 0), (600, 0), (600, 400), (0, 400)])
    outline(m, [(5000, 0), (5600, 0), (5600, 400), (5000, 400)])
    m.add_line((0, 0), (600, 400), dxfattribs={'layer': 'STRUCT_COLUMN'})
    m.add_line((600, 0), (0, 400), dxfattribs={'layer': 'STRUCT_COLUMN'})
    data = parse_columns(doc, tmp_path)
    cols = data['elements']['column']
    assert len(cols) == 2
    assert sorted(Polygon(c['points']).area for c in cols) == [240000, 240000]
    assert sorted(len(c['source_signatures']) for c in cols) == [4, 6]
    assert len({c['eid'] for c in cols}) == 2
    assert all(c['needs_review'] for c in cols)


def test_concave_column_retains_empty_corner(tmp_path):
    doc = ezdxf.new()
    outline(doc.modelspace(), [(0, 0), (600, 0), (600, 100), (100, 100), (100, 400), (0, 400)])
    c = parse_columns(doc, tmp_path)['elements']['column'][0]
    assert Polygon(c['points']).area == 90000
    assert len(c['source_signatures']) == 6


@pytest.mark.parametrize('nested', [False, True])
def test_missing_or_nested_boundary_is_not_invented_or_filled(tmp_path, nested):
    doc = ezdxf.new(); m = doc.modelspace()
    if nested:
        outline(m, [(0, 0), (1000, 0), (1000, 1000), (0, 1000)])
        outline(m, [(200, 200), (800, 200), (800, 800), (200, 800)])
        expected = 8
    else:
        for a, b in [((0, 0), (600, 0)), ((600, 0), (600, 400)), ((600, 400), (0, 400))]:
            m.add_line(a, b, dxfattribs={'layer': 'STRUCT_COLUMN'})
        expected = 3
    data = parse_columns(doc, tmp_path)
    cols = data['elements']['column']
    assert not any(c.get('closed') for c in cols)
    assert sum(len(c['source_signatures']) for c in cols) == expected
    assert all(c['needs_review'] for c in cols)
    assert verify_geometry(data).errors


def test_column_grouping_keeps_different_heights_separate(tmp_path):
    doc = ezdxf.new(); m = doc.modelspace()
    pts = [(0, 0), (600, 0), (600, 400), (0, 400)]
    outline(m, pts, 0); outline(m, pts, 3000)
    cols = parse_columns(doc, tmp_path)['elements']['column']
    assert len(cols) == 2
    assert sorted(c['z_base'] for c in cols) == [0, 3000]
    assert len({c['eid'] for c in cols}) == 2
    lower = next(c for c in cols if c['z_base'] == 0)
    edited = dp.parse(str(tmp_path/'drawing.dxf'), [('STRUCT_COLUMN', 'column', {})],
                      block_rules=[], edits={lower['eid']: {'overrides': {'height': 2900}}})
    assert [c.get('overrides', {}).get('height') for c in sorted(edited['elements']['column'], key=lambda c:c['z_base'])] == [2900, None]


def test_nested_boundary_with_spokes_cannot_be_unioned_into_solid(tmp_path):
    doc = ezdxf.new(); m = doc.modelspace()
    outline(m, [(0, 0), (600, 0), (600, 400), (0, 400)])
    outline(m, [(200, 100), (400, 100), (400, 300), (200, 300)])
    m.add_line((0, 0), (200, 100), dxfattribs={'layer':'STRUCT_COLUMN'})
    m.add_line((600, 400), (400, 300), dxfattribs={'layer':'STRUCT_COLUMN'})
    data = parse_columns(doc, tmp_path)
    assert not any(c.get('closed') for c in data['elements']['column'])
    assert sum(len(c['source_signatures']) for c in data['elements']['column']) == 10
    assert verify_geometry(data).errors


def test_duplicate_outline_edge_does_not_hide_a_valid_column(tmp_path):
    doc = ezdxf.new(); m = doc.modelspace()
    outline(m, [(0, 0), (600, 0), (600, 400), (0, 400)])
    m.add_line((600, 0), (0, 0), dxfattribs={'layer':'STRUCT_COLUMN'})
    data = parse_columns(doc, tmp_path)
    cols = data['elements']['column']
    assert len(cols) == 1 and cols[0]['closed']
    assert Polygon(cols[0]['points']).area == 240000
    assert len(cols[0]['source_signatures']) == 5


def test_closed_column_keeps_existing_identity(tmp_path):
    doc = ezdxf.new()
    doc.modelspace().add_lwpolyline([(0, 0), (500, 0), (500, 500), (0, 500)],
                                  close=True, dxfattribs={'layer': 'STRUCT_COLUMN'})
    first = parse_columns(doc, tmp_path)['elements']['column'][0]
    outline(doc.modelspace(), [(2000, 0), (2600, 0), (2600, 400), (2000, 400)])
    second = next(c for c in parse_columns(doc, tmp_path)['elements']['column']
                  if c['points'] == first['points'])
    assert first['eid'] == second['eid']
    assert not first.get('needs_review')
