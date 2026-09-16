"""Source-bound, portable DXF MEP profiles and parser integration."""
import copy
import hashlib
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ezdxf
import pytest
import dxf_parser as dp


def source(tmp_path, units=4):
    d = ezdxf.new(); d.units = units
    d.layers.new('COIL', dxfattribs={'color': 3})
    d.modelspace().add_line((0, 0), (100, 0), dxfattribs={'layer': 'COIL'})
    p = tmp_path / 'plan.dxf'; d.saveas(p)
    return d, p


def profile(path, **extra):
    p = {'version': 1, 'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
         'layers': [{'pattern': '^COIL$', 'category': 'pipe', 'system': 'heating',
                     'representation': 'centerline', 'diameter_mm': 15.9,
                     'nominal_size': '15A', 'material': 'PB', 'dimension_basis': 'assumed',
                     'placement': 'foam_top'}],
         'levels': {'structural_slab_top_mm': 0, 'floor_to_floor_mm': 2800, 'slab_thickness_mm': 200},
         'floor_layers': [{'role': 'impact_insulation', 'thickness_mm': 30},
                          {'role': 'foamed_concrete', 'thickness_mm': 40},
                          {'role': 'screed', 'thickness_mm': 40}]}
    p.update(extra); return p


def test_source_hash_binding_rejects_changed_input(tmp_path):
    from mep_profile import validate_profile
    _, path = source(tmp_path)
    p = profile(path)
    assert validate_profile(p, p['source_sha256'])['version'] == 1
    with pytest.raises(ValueError, match='hash|SHA|source'):
        validate_profile(p, 'a' * 64)


def test_unknown_units_require_explicit_scale(tmp_path):
    from mep_profile import inspect_mep_source
    _, path = source(tmp_path, 0)
    inv = inspect_mep_source(path)
    assert inv['scale_to_mm'] is None
    with pytest.raises(ValueError, match='unit|단위'):
        dp.parse(str(path), [], block_rules=[], mep_profile=profile(path))
    result = dp.parse(str(path), [], block_rules=[], mep_profile=profile(path, unit_scale_to_mm=10))
    assert result['elements']['pipe'][0]['points'][-1] == [1000, 0]


def test_inventory_explicit_units_enable_region_selection_for_unitless_dxf(tmp_path):
    from mep_profile import inspect_mep_source
    _, path = source(tmp_path, 0)
    inv = inspect_mep_source(path, unit_scale_to_mm=10)
    assert inv['scale_to_mm'] == 10
    assert inv['layers'][0]['bounds_mm'] == [0, 0, 1000, 0]
    assert inv['regions'] and inv['regions'][0]['bounds_mm'][2] == 1000


def test_nonuniform_circular_block_is_diagnosed_instead_of_treated_as_round(tmp_path):
    d = ezdxf.new(); d.units = 4
    block = d.blocks.new('CURVED'); block.add_arc((0, 0), 100, 0, 180)
    d.modelspace().add_blockref('CURVED', (0, 0), dxfattribs={'layer': 'COIL', 'xscale': 2, 'yscale': 1})
    path = tmp_path / 'scaled.dxf'; d.saveas(path)
    r = dp.parse(str(path), [], block_rules=[], mep_profile=profile(path))
    assert not r['elements']['pipe']
    assert r['source_coverage']['omitted'] == 1
    assert any(i['code'] == 'NONUNIFORM_CURVE_TRANSFORM' for i in r['mep_diagnostics']['issues'])


def test_uniform_plan_scale_is_supported_when_z_scale_is_one(tmp_path):
    d = ezdxf.new(); d.units = 4
    block = d.blocks.new('CURVED'); block.add_arc((0, 0), 100, 0, 180)
    d.modelspace().add_blockref('CURVED', (0, 0), dxfattribs={'layer': 'COIL', 'xscale': 2, 'yscale': 2, 'zscale': 1})
    path = tmp_path / 'uniform.dxf'; d.saveas(path)
    r = dp.parse(str(path), [], block_rules=[], mep_profile=profile(path))
    assert len(r['elements']['pipe']) == 1
    assert r['elements']['pipe'][0]['source_length_mm'] == pytest.approx(628.3185307179587)


def test_profile_cannot_bind_pre_read_geometry_to_post_read_file_hash(tmp_path, monkeypatch):
    d, path = source(tmp_path)
    before = path.read_bytes()
    d.modelspace().add_line((200, 0), (300, 0), dxfattribs={'layer': 'COIL'}); d.saveas(path)
    after = path.read_bytes(); bound_to_after = profile(path)
    path.write_bytes(before)
    real_readfile = ezdxf.readfile

    def read_then_replace(filename, *args, **kwargs):
        doc = real_readfile(filename, *args, **kwargs)
        Path(filename).write_bytes(after)
        return doc

    monkeypatch.setattr(ezdxf, 'readfile', read_then_replace)
    with pytest.raises(ValueError, match='hash|SHA|source|Source'):
        dp.parse(str(path), [], block_rules=[], mep_profile=bound_to_after)


def test_profile_assigns_foam_top_and_keeps_nominal_separate(tmp_path):
    _, path = source(tmp_path)
    result = dp.parse(str(path), [], block_rules=[], mep_profile=profile(path))
    p = result['elements']['pipe'][0]
    assert p['diameter'] == 15.9 and p['nominal_size'] == '15A'
    assert p['elevation'] == pytest.approx(77.95)
    assert p['needs_review'] and p['review_reason']
    assert p['source_refs'][0]['handle']
    assert p['source_length_mm'] == 100
    assert result['mep_diagnostics']['source_coverage']['represented'] == 1


def test_mep_only_profile_declares_nonzero_structural_floor_datum(tmp_path):
    _, path = source(tmp_path)
    p = profile(path); p['levels']['structural_slab_top_mm'] = 10000
    r = dp.parse(str(path), [], block_rules=[], mep_profile=p)
    assert r['elements']['pipe'][0]['elevation'] == pytest.approx(10077.95)
    assert r['floors'][0]['z'] == 10000


def test_eid_and_saved_correction_survive_curve_refinement(tmp_path):
    d, path = source(tmp_path)
    d.modelspace().add_arc((100, 50), 50, 270, 90, dxfattribs={'layer': 'COIL'}); d.saveas(path)
    first = dp.parse(str(path), [], block_rules=[], mep_profile=profile(path, curve_chord_error_mm=2))
    eid = first['elements']['pipe'][0]['eid']
    second = dp.parse(str(path), [], block_rules=[], mep_profile=profile(path, curve_chord_error_mm=.1),
                      edits={eid: {'overrides': {'diameter': 18}}})
    assert second['elements']['pipe'][0]['eid'] == eid
    assert second['elements']['pipe'][0]['overrides']['diameter'] == 18
    assert second['edits_report']['applied']


def test_region_omits_outside_and_reports_crossing_without_clipping(tmp_path):
    d, path = source(tmp_path)
    d.modelspace().add_line((500, 0), (600, 0), dxfattribs={'layer': 'COIL'})
    d.modelspace().add_line((90, 5), (200, 5), dxfattribs={'layer': 'COIL'}); d.saveas(path)
    r = dp.parse(str(path), [], block_rules=[], mep_profile=profile(path, region={'id': 'A', 'bounds_mm': [-10, -10, 110, 10]}))
    assert len(r['elements']['pipe']) == 1
    assert r['elements']['pipe'][0]['points'] == [[0, 0], [100, 0]]
    assert any(i['code'] == 'REGION_CROSSING' for i in r['mep_diagnostics']['issues'])
    assert r['mep_diagnostics']['source_coverage']['omitted'] == 1


def test_nested_insert_layer_zero_and_byblock_color_keep_instance_identity(tmp_path):
    d = ezdxf.new(); d.units = 4
    d.layers.new('COIL', dxfattribs={'color': 3})
    b = d.blocks.new('ONE'); b.add_line((0, 0), (100, 0), dxfattribs={'color': 0})
    outer = d.blocks.new('NEST'); outer.add_blockref('ONE', (0, 0), dxfattribs={'color': 0})
    d.modelspace().add_blockref('NEST', (0, 0), dxfattribs={'layer': 'COIL', 'color': 1})
    d.modelspace().add_blockref('NEST', (1000, 0), dxfattribs={'layer': 'COIL', 'color': 1})
    path = tmp_path / 'block.dxf'; d.saveas(path)
    p = profile(path); p['layers'][0]['color'] = 1
    r = dp.parse(str(path), [], block_rules=[], mep_profile=p)
    rows = r['elements']['pipe']
    assert len(rows) == 2
    assert rows[0]['eid'] != rows[1]['eid']
    assert all(len(row['source_refs'][0]['insert_path']) == 2 for row in rows)
    assert sorted(row['points'][0][0] for row in rows) == [0, 1000]


def test_outline_keeps_hole_and_has_no_fake_centerline_length(tmp_path):
    d = ezdxf.new(); d.units = 4
    for points in [[(0, 0), (1000, 0), (1000, 1000), (0, 1000)],
                   [(200, 200), (800, 200), (800, 800), (200, 800)]]:
        d.modelspace().add_lwpolyline(points, close=True, dxfattribs={'layer': 'DUCT'})
    path = tmp_path / 'outline.dxf'; d.saveas(path)
    p = profile(path); p['layers'] = [{'pattern': '^DUCT$', 'category': 'duct', 'system': 'SA',
        'representation': 'outline', 'height_mm': 60, 'dimension_basis': 'user', 'placement': 'slab_soffit'}]
    r = dp.parse(str(path), [], block_rules=[], mep_profile=p)
    ducts = r['elements']['duct']
    assert len(ducts) == 1 and ducts[0]['geometry_mode'] == 'footprint'
    assert len(ducts[0]['holes']) == 1
    assert 'source_length_mm' not in ducts[0]
    assert ducts[0]['elevation'] == 2570


def test_same_system_outline_can_close_across_explicit_layer_color_rules(tmp_path):
    d = ezdxf.new(); d.units = 4
    m = d.modelspace()
    for a, b in [((0, 0), (100, 0)), ((100, 0), (100, 50))]:
        m.add_line(a, b, dxfattribs={'layer': 'OUTLINE', 'color': 3})
    for a, b in [((100, 50), (0, 50)), ((0, 50), (0, 0))]:
        m.add_line(a, b, dxfattribs={'layer': 'FITTING', 'color': 5})
    path = tmp_path / 'combined.dxf'; d.saveas(path)
    p = profile(path)
    p['layers'] = [dict(pattern='^' + layer + '$', color=color, category='duct', system='SA',
                         representation='outline', height_mm=60, dimension_basis='user', placement='slab_soffit')
                   for layer, color in [('OUTLINE', 3), ('FITTING', 5)]]
    r = dp.parse(str(path), [], block_rules=[], mep_profile=p)
    assert len(r['elements']['duct']) == 1
    assert r['elements']['duct'][0]['footprint_area_mm2'] == 5000
    assert r['source_coverage']['represented'] == 4 and r['source_coverage']['omitted'] == 0


def test_outline_uses_declared_coincidence_tolerance_but_retains_original_primitive(tmp_path):
    d = ezdxf.new(); d.units = 4
    m = d.modelspace()
    for a, b in [((0, 0), (100, 0)), ((100.0001, 0), (100, 50)),
                 ((100, 50), (0, 50)), ((0, 50), (0, 0))]:
        m.add_line(a, b, dxfattribs={'layer': 'DUCT'})
    path = tmp_path / 'tolerance.dxf'; d.saveas(path)
    p = profile(path); p['layers'] = [dict(pattern='^DUCT$', category='duct', system='SA',
        representation='outline', height_mm=60, dimension_basis='user', placement='slab_soffit')]
    r = dp.parse(str(path), [], block_rules=[], mep_profile=p)
    assert len(r['elements']['duct']) == 1
    rec = r['elements']['duct'][0]
    assert rec['boundary_max_adjustment_mm'] == pytest.approx(.0001)
    assert any(s.get('start_mm', [None])[0] == 100.0001 for s in rec['source_geometry'])


def test_partial_outline_source_is_not_claimed_fully_covered(tmp_path):
    d = ezdxf.new(); d.units = 4
    for a, b in [((0, 0), (200, 0)), ((100, 0), (100, 50)),
                 ((100, 50), (0, 50)), ((0, 50), (0, 0))]:
        d.modelspace().add_line(a, b, dxfattribs={'layer': 'DUCT'})
    path = tmp_path / 'partial.dxf'; d.saveas(path)
    p = profile(path); p['layers'] = [dict(pattern='^DUCT$', category='duct', system='SA',
        representation='outline', height_mm=60, dimension_basis='user', placement='slab_soffit')]
    r = dp.parse(str(path), [], block_rules=[], mep_profile=p)
    assert len(r['elements']['duct']) == 1
    assert not r['source_coverage']['complete']
    assert any(i['code'] == 'OUTLINE_PARTIAL' for i in r['mep_diagnostics']['issues'])


def test_legacy_gap_option_does_not_leak_to_other_layers(tmp_path):
    d = ezdxf.new(); d.units = 4
    for layer, x in [('P1', 0), ('P2', 1000)]:
        d.modelspace().add_line((x, 0), (x + 100, 0), dxfattribs={'layer': layer})
        d.modelspace().add_line((x + 120, 0), (x + 200, 0), dxfattribs={'layer': layer})
    path = tmp_path / 'gap.dxf'; d.saveas(path)
    rules = [('^P1$', 'pipe', {'width': 15.9, '_opts': {'connect_gap': 25}}),
             ('^P2$', 'pipe', {'width': 15.9})]
    r = dp.parse(str(path), rules, block_rules=[])
    assert len(r['elements']['pipe']) == 3


def _codes(result):
    return {i['code'] for i in result['mep_diagnostics']['issues']}


def test_a_plan_z_used_as_an_installation_height_is_reported(tmp_path):
    """평면도는 설비를 z 0 에 그린다 — 그 0 을 설치 높이로 쓰면 설비가 바닥에 깔린 채 조용히 나간다."""
    _, path = source(tmp_path)
    rule = {'pattern': '^COIL$', 'category': 'pipe', 'system': 'heating', 'representation': 'centerline',
            'diameter_mm': 15.9, 'dimension_basis': 'user', 'placement': 'source'}
    flat = dp.parse(str(path), [], block_rules=[], mep_profile=profile(path, layers=[rule]))
    assert 'PLAN_Z_AS_ELEVATION' in _codes(flat)
    assert flat['elements']['pipe'][0]['elevation'] == 0
    # 설치 높이를 선언하면 사라진다 — 이 진단이 요구하는 조치가 그것이다.
    declared = dp.parse(str(path), [], block_rules=[], mep_profile=profile(
        path, layers=[dict(rule, placement='slab_soffit')]))
    assert 'PLAN_Z_AS_ELEVATION' not in _codes(declared)


def test_a_drawing_z_outside_the_declared_storey_is_not_an_installation_height(tmp_path):
    """실측(단위세대 환기): 슬리브 2개의 원본 z 가 12,357mm·24,715mm 였다 — 한 층 세대의 높이가 아니다."""
    d = ezdxf.new(); d.units = 4
    d.layers.new('COIL', dxfattribs={'color': 3})
    d.modelspace().add_line((0, 0, 12357.8), (100, 0, 12357.8), dxfattribs={'layer': 'COIL'})
    path = tmp_path / 'high.dxf'; d.saveas(path)
    rule = {'pattern': '^COIL$', 'category': 'pipe', 'system': 'heating', 'representation': 'centerline',
            'diameter_mm': 15.9, 'dimension_basis': 'user', 'placement': 'source'}
    result = dp.parse(str(path), [], block_rules=[], mep_profile=profile(path, layers=[rule]))
    (issue,) = [i for i in result['mep_diagnostics']['issues'] if i['code'] == 'SOURCE_Z_OUTSIDE_STOREY']
    assert issue['storey_mm'] == [-200.0, 2800.0] and issue['source_elevations_mm'] == [12357.8]
    assert 'PLAN_Z_AS_ELEVATION' not in _codes(result)     # z 0 과는 다른 문제다
    # 선언한 층 높이가 없으면 잴 기준이 없다 — 아무 말도 하지 않는다(추정하지 않는다).
    quiet = profile(path, layers=[rule]); quiet['levels'] = {'structural_slab_top_mm': 0}
    assert 'SOURCE_Z_OUTSIDE_STOREY' not in _codes(dp.parse(str(path), [], block_rules=[], mep_profile=quiet))
