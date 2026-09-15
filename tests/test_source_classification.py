"""Site-specific role and source filters must not turn annotation symbols into MEP."""
import copy
import hashlib
import math

import ezdxf
import pytest

import dxf_parser as dp
import geom_contract as GC
from mep_profile import validate_profile
from project_server import ProjectSession
from project_store import ProjectStore


def save(tmp_path, doc):
    path = tmp_path / 'source.dxf'
    doc.saveas(path)
    return path


def profile(path, layers=None, **extra):
    return dict(version=1, source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                unit_scale_to_mm=1, layers=layers or [],
                levels={'floor_to_floor_mm': 2800, 'slab_thickness_mm': 200}, **extra)


def duct(**extra):
    return dict(pattern='^AIR$', category='duct', system='SA', representation='centerline',
                placement='slab_soffit', dimension_basis='annotation', **extra)


def parse(path, p):
    return dp.parse(str(path), dp.DEFAULT_LAYER_RULES, block_rules=[], mep_profile=p)


def test_round_and_rectangular_sections_survive_project_save_reparse(tmp_path):
    doc = ezdxf.new(units=4)
    lines = [doc.modelspace().add_line((0, y), (1000, y), dxfattribs={'layer': 'AIR'}) for y in (0, 500, 1000, 1500)]
    path = save(tmp_path, doc)
    rules = [duct(section_shape='rect', width_mm=110, height_mm=54),
             duct(section_shape='rect', width_mm=204, height_mm=60),
             duct(section_shape='round', diameter_mm=100), duct(section_shape='round', diameter_mm=125)]
    for rule, line in zip(rules, lines):
        rule['source_handles'] = [line.dxf.handle]
    session = ProjectSession(ProjectStore(tmp_path / 'project').create([{'id': 'main', 'path': str(path)}]))
    state = session.state()
    state = session.configure_source(profile(path, rules), state['revision'], state['project_id'])
    reopened = ProjectSession(ProjectStore(session.store.folder)).state()
    records = sorted(reopened['geometry']['elements']['duct'], key=lambda r: r['points'][0][1])
    assert len(records) == 4
    assert [GC.mep_dimensions('duct', r) for r in records] == [
        {'width_mm': 110, 'height_mm': 54}, {'width_mm': 204, 'height_mm': 60},
        {'diameter': 100}, {'diameter': 125}]
    assert [GC.z_range('duct', r)[1] for r in records] == [2600] * 4
    assert {r['source_refs'][0]['handle'] for r in records} == {e.dxf.handle for e in lines}


def test_terminal_body_filter_excludes_inner_rings_and_direction_symbol(tmp_path):
    doc = ezdxf.new(units=4)
    body = doc.blocks.new('BODY')
    outer = body.add_circle((0, 0), 79)
    body.add_circle((0, 0), 50)
    arrow = doc.blocks.new('DIRECTION')
    arrow.add_circle((0, 0), 240)
    for x in (0, 1000):
        doc.modelspace().add_blockref('BODY', (x, 0), dxfattribs={'layer': 'DEVICE'})
        doc.modelspace().add_blockref('DIRECTION', (x, 0), dxfattribs={'layer': 'DEVICE'})
    path = save(tmp_path, doc)
    rule = dict(pattern='^DEVICE$', category='equipment', role='terminal', system='SA',
                representation='outline', block_pattern='^BODY$', entity_types=['CIRCLE'],
                source_handles=[outer.dxf.handle], height_mm=80, placement='slab_soffit', dimension_basis='assumed')
    result = parse(path, profile(path, [rule]))
    terminals = result['elements']['equipment']
    assert len(terminals) == 2
    assert all(r['role'] == 'terminal' and r['needs_review'] for r in terminals)
    assert all(GC.z_range('equipment', r) == (2520, 2600) for r in terminals)
    assert result['source_coverage']['selected'] == result['source_coverage']['represented'] == 2
    assert all(len(r['source_refs']) == 1 and r['source_refs'][0]['handle'] == outer.dxf.handle for r in terminals)
    assert len({r['eid'] for r in terminals}) == 2
    assert all(max(p[0] for p in r['points']) - min(p[0] for p in r['points']) == pytest.approx(158, abs=.5) for r in terminals)


def test_exact_source_ref_selects_one_of_repeated_block_instances(tmp_path):
    doc = ezdxf.new(units=4)
    block = doc.blocks.new('SEGMENT')
    line = block.add_line((0, 0), (100, 0))
    first = doc.modelspace().add_blockref('SEGMENT', (0, 0), dxfattribs={'layer': 'AIR'})
    doc.modelspace().add_blockref('SEGMENT', (1000, 0), dxfattribs={'layer': 'AIR'})
    path = save(tmp_path, doc)
    ref = {'handle': line.dxf.handle, 'type': 'LINE', 'insert_path': [
        {'handle': first.dxf.handle, 'block': 'SEGMENT', 'array_index': 0}]}
    result = parse(path, profile(path, [duct(width_mm=110, height_mm=54, source_refs=[ref])]))
    records = result['elements']['duct']
    assert len(records) == 1
    assert records[0]['points'] == [[0, 0], [100, 0]]
    assert result['source_coverage']['selected'] == 1


def test_explicit_missing_source_filter_cannot_silently_report_complete(tmp_path):
    doc = ezdxf.new(units=4)
    line = doc.modelspace().add_line((0, 0), (100, 0), dxfattribs={'layer': 'AIR'})
    path = save(tmp_path, doc)
    with pytest.raises(ValueError, match='source|Source|handle'):
        parse(path, profile(path, [duct(width_mm=110, height_mm=54, source_handles=[line.dxf.handle, 'FFFF'])]))


def test_conflicting_filters_do_not_silently_choose_first_dimensions(tmp_path):
    doc = ezdxf.new(units=4)
    doc.modelspace().add_line((0, 0), (100, 0), dxfattribs={'layer': 'AIR'})
    path = save(tmp_path, doc)
    with pytest.raises(ValueError, match='overlap|ambiguous|Ambiguous'):
        parse(path, profile(path, [duct(width_mm=110, height_mm=54), duct(width_mm=204, height_mm=60)]))


def test_project_architecture_mapping_reclassifies_only_selected_layer(tmp_path):
    doc = ezdxf.new(units=4)
    m = doc.modelspace()
    for a, b in [((0, 0), (3000, 0)), ((0, 200), (3000, 200))]:
        m.add_line(a, b, dxfattribs={'layer': 'FRAME-COL'})
    m.add_lwpolyline([(4000, 0), (4400, 0), (4400, 400), (4000, 400)], close=True, dxfattribs={'layer': 'A-COL'})
    path = save(tmp_path, doc)
    global_rules = copy.deepcopy(dp.DEFAULT_LAYER_RULES)
    p = profile(path, architecture_layers=[{'pattern': '^FRAME-COL$', 'category': 'wall', 'height_mm': 2600}])
    result = parse(path, p)
    assert len(result['elements']['column']) == 1
    assert result['elements']['wall']
    assert all(r.get('needs_review') for r in result['elements']['wall'])
    assert all(GC.z_range('wall', r)[1] == 2600 for r in result['elements']['wall'])
    assert dp.DEFAULT_LAYER_RULES == global_rules
    assert len(dp.parse(str(path), dp.DEFAULT_LAYER_RULES, block_rules=[])['elements']['column']) == 3


@pytest.mark.parametrize('field,value', [('source_handles', []), ('source_handles', ['']),
    ('entity_types', 'LINE'), ('block_pattern', '['), ('source_refs', [{'handle': 'AB'}])])
def test_bad_source_filters_are_rejected_before_parse(tmp_path, field, value):
    doc = ezdxf.new(units=4)
    path = save(tmp_path, doc)
    with pytest.raises(ValueError):
        validate_profile(profile(path, [duct(width_mm=110, height_mm=54, **{field: value})]))


def test_gui_edits_preserve_exact_instance_filters_and_architectural_rules(tmp_path):
    import tkinter as tk
    from mep_setup_ui import MepSetupDialog
    from mep_profile import inspect_mep_source
    doc = ezdxf.new(units=4)
    line = doc.modelspace().add_line((0, 0), (100, 0), dxfattribs={'layer': 'AIR'})
    path = save(tmp_path, doc)
    ref = {'handle': line.dxf.handle, 'insert_path': []}
    p = profile(path, [duct(width_mm=110, height_mm=54, source_refs=[ref], source_handles=[line.dxf.handle])],
                architecture_layers=[{'pattern': '^FRAME$', 'category': 'wall', 'height_mm': 2600}])
    session = ProjectSession(ProjectStore(tmp_path / 'project').create([{'id': 'main', 'path': str(path)}]))
    root = tk.Tk(); root.withdraw()
    try:
        dialog = MepSetupDialog(root, session, inspect_mep_source(path, 1), lambda _: None)
        dialog._load_profile(p)
        dialog.map_tree.selection_set('0'); dialog._select_mapping(None)
        dialog.rule_vars['width_mm'].set('204')
        dialog._update_mapping()
        result = dialog._form_profile()
        assert result['layers'][0]['source_refs'] == [ref]
        assert result['layers'][0]['source_handles'] == [line.dxf.handle]
        assert result['layers'][0]['width_mm'] == 204
        assert result['architecture_layers'] == p['architecture_layers']
    finally:
        root.destroy()


def test_architecture_only_profile_filters_legacy_mep_outside_region(tmp_path):
    doc = ezdxf.new(units=4)
    doc.modelspace().add_line((10000, 0), (11000, 0), dxfattribs={'layer': 'PIPE'})
    path = save(tmp_path, doc)
    p = profile(path, architecture_layers=[{'pattern': '^FRAME$', 'category': 'wall'}],
                region={'id': 'unit', 'bounds_mm': [-100, -100, 500, 500]})
    result = parse(path, p)
    assert result['elements']['pipe'] == []
    assert result['mep_diagnostics']['scope']['excluded_architecture']['pipe'] == 1


def test_insert_architecture_override_remains_reviewable_on_child_layer(tmp_path):
    doc = ezdxf.new(units=4)
    block = doc.blocks.new('FRAME_COLUMN')
    block.add_lwpolyline([(0, 0), (400, 0), (400, 400), (0, 400)], close=True)
    doc.modelspace().add_blockref('FRAME_COLUMN', (0, 0), dxfattribs={'layer': 'FRAME'})
    path = save(tmp_path, doc)
    p = profile(path, architecture_layers=[{'pattern': '^FRAME$', 'category': 'column', 'height_mm': 2600}])
    r = parse(path, p)['elements']['column'][0]
    assert GC.z_range('column', r) == (0, 2600)
    assert r['needs_review']
    assert r['review_reason'] == 'project_architecture_classification'


def test_overlapping_equipment_symbols_keep_two_bodies_and_distinct_ids(tmp_path):
    doc = ezdxf.new(units=4)
    for x in (0, 100):
        doc.modelspace().add_circle((x, 0), 79, dxfattribs={'layer': 'DEVICE'})
    path = save(tmp_path, doc)
    row = dict(pattern='^DEVICE$', category='equipment', role='terminal', system='SA',
               representation='outline', height_mm=80, placement='slab_soffit', dimension_basis='assumed')
    result = parse(path, profile(path, [row]))
    bodies = result['elements']['equipment']
    assert len(bodies) == 2
    assert len({r['eid'] for r in bodies}) == 2
    assert all(len(r['source_refs']) == 1 for r in bodies)
    assert any(i['code'] == 'EQUIPMENT_OVERLAP' for i in result['mep_diagnostics']['issues'])


def test_paired_insert_wall_retains_project_classification_review(tmp_path):
    doc = ezdxf.new(units=4)
    block = doc.blocks.new('WALL_BODY')
    block.add_line((0, 0), (3000, 0))
    block.add_line((0, 200), (3000, 200))
    doc.modelspace().add_blockref('WALL_BODY', (0, 0), dxfattribs={'layer': 'FRAME'})
    path = save(tmp_path, doc)
    p = profile(path, architecture_layers=[{'pattern': '^FRAME$', 'category': 'wall', 'height_mm': 2600}])
    walls = parse(path, p)['elements']['wall']
    assert walls and all(r['needs_review'] for r in walls)
    assert all(GC.z_range('wall', r) == (0, 2600) for r in walls)


def test_equipment_overlap_across_rules_respects_vertical_separation(tmp_path):
    doc = ezdxf.new(units=4)
    items = [doc.modelspace().add_circle((x, 0), 79, dxfattribs={'layer': 'DEVICE'}) for x in (0, 100, 100)]
    path = save(tmp_path, doc)
    rules = [dict(pattern='^DEVICE$', category='equipment', role='terminal', system=str(i),
        source_handles=[item.dxf.handle], representation='outline', height_mm=80, placement='center',
        center_elevation_mm=2560 if i < 2 else 2000, dimension_basis='assumed') for i, item in enumerate(items)]
    result = parse(path, profile(path, rules))
    overlaps = [r for r in result['mep_diagnostics']['issues'] if r['code'] == 'EQUIPMENT_OVERLAP']
    assert len(overlaps) == 1
    assert {r['handle'] for r in overlaps[0]['source_refs']} == {i.dxf.handle for i in items[:2]}


def test_wall_pair_keeps_review_from_nonrepresentative_source_layer(tmp_path):
    doc = ezdxf.new(units=4)
    doc.modelspace().add_line((0, 0), (3000, 0), dxfattribs={'layer': 'A-WALL'})
    doc.modelspace().add_line((0, 200), (3000, 200), dxfattribs={'layer': 'Z-FRAME'})
    path = save(tmp_path, doc)
    p = profile(path, architecture_layers=[{'pattern': '^Z-FRAME$', 'category': 'wall', 'height_mm': 2600}])
    result = dp.parse(str(path), [('^A-WALL$', 'wall', {'height': 2600})], block_rules=[], mep_profile=p)
    walls = result['elements']['wall']
    assert len(walls) == 1 and walls[0]['layer'] == 'A-WALL'
    assert walls[0].get('needs_review')
    assert walls[0]['review_reason'] == 'project_architecture_classification'
