"""Units must agree across inventory, persisted model and original-DXF overlay."""
import math

import ezdxf
import pytest

import dxf_parser as dp
from mep_profile import inspect_mep_source
from project_server import ProjectSession
from project_store import ProjectStore, RevisionConflict, fingerprint
from source_drawing import build_source_drawing


def drawing(tmp_path, units=1):
    doc = ezdxf.new(units=units)
    doc.modelspace().add_line((0, 0), (100, 0), dxfattribs={'layer': 'SUPPLY'})
    doc.modelspace().add_text('110x54', dxfattribs={'layer': 'NOTE'})
    path = tmp_path / 'drawing.dxf'
    doc.saveas(path)
    return path


def session_for(tmp_path, path, options=None):
    return ProjectSession(ProjectStore(tmp_path / 'project.mep').create([
        {'id': 'main', 'path': str(path), 'options': options or {}}]))


@pytest.mark.parametrize('units, expected', [(1, 2540), (2, 30480), (5, 1000), (6, 100000)])
def test_header_scale_matches_inventory_model_and_overlay(tmp_path, units, expected):
    path = drawing(tmp_path, units)
    inventory = inspect_mep_source(path)
    geometry = dp.parse(str(path), [], block_rules=[])
    floor = build_source_drawing([{'path': str(path)}], geometry)['floors'][0]
    assert inventory['bounds_mm'][2] == pytest.approx(expected)
    assert geometry['scale_applied'] * 100 == pytest.approx(expected)
    assert floor['primitives'][0]['points'][1] == pytest.approx([expected, 0])


def test_header_override_keeps_evidence_and_does_not_claim_annotation_proves_units(tmp_path):
    path = drawing(tmp_path)
    inv = inspect_mep_source(path, unit_scale_to_mm=1)
    review = inv.get('unit_review', {})
    assert review.get('header_scale_to_mm') == pytest.approx(25.4)
    assert review.get('effective_scale_to_mm') == 1
    assert review.get('header_differs') is True
    assert review.get('basis') == 'explicit'
    assert inv['unit_evidence']['annotations'][0]['text'] == '110x54'
    measurement = inv['unit_evidence']['measurements'][0]
    assert measurement['raw_length'] == 100
    assert measurement['length_mm'] == 100
    assert measurement['source_refs'][0]['handle']


def test_unitless_legacy_preview_is_explicitly_assumed(tmp_path):
    path = drawing(tmp_path, 0)
    g = dp.parse(str(path), [], block_rules=[])
    assert g['scale_applied'] == 1
    assert g.get('unit_review', {}).get('basis') == 'legacy_assumed_mm'
    assert any('unit' in w.lower() for w in g['warnings'])


def test_architecture_units_save_reopen_reparse_and_reject_stale_request(tmp_path):
    path = drawing(tmp_path)
    session = session_for(tmp_path, path)
    state = session.state()
    configure = getattr(session, 'configure_units', None)
    assert callable(configure), 'Architectural drawings need units without a MEP mapping'
    changed = configure(1, state['revision'], state['project_id'], source_sha256=fingerprint(path))
    assert changed['revision'] == 1
    reopened = ProjectSession(ProjectStore(session.store.folder))
    assert reopened.state()['geometry']['scale_applied'] == 1
    floor = reopened.preview_state()['geometry']['source_drawing']['floors'][0]
    assert floor['primitives'][0]['points'][1] == [100, 0]
    assert reopened.store.read()['decisions'][-1]['action'] == 'set_source_units'
    with pytest.raises(RevisionConflict):
        configure(10, 0, state['project_id'], source_sha256=fingerprint(path))
    assert reopened.state()['revision'] == 1


def test_existing_profile_override_controls_source_overlay(tmp_path):
    path = drawing(tmp_path, 6)
    source = {'path': str(path), 'options': {'mep_profile': {'unit_scale_to_mm': 1}}}
    floor = build_source_drawing([source], {})['floors'][0]
    assert floor['primitives'][0]['points'][1] == [100, 0]


def test_units_change_keeps_same_source_region_and_physical_section(tmp_path):
    path = drawing(tmp_path)
    profile = {'version': 1, 'source_sha256': fingerprint(path), 'unit_scale_to_mm': 25.4,
        'region': {'id': 'selected', 'bounds_mm': [-25.4, -25.4, 2565.4, 25.4]},
        'layers': [{'pattern': '^SUPPLY$', 'category': 'duct', 'system': 'SA',
                    'representation': 'centerline', 'width_mm': 110, 'height_mm': 54,
                    'placement': 'center', 'center_elevation_mm': 2573, 'dimension_basis': 'user'}]}
    session = session_for(tmp_path, path, {'mep_profile': profile})
    initial = session.state()
    configure = getattr(session, 'configure_units', None)
    assert callable(configure)
    changed = configure(1, 0, initial['project_id'], source_sha256=fingerprint(path))
    saved = session.store.read()['sources'][0]['options']['mep_profile']
    assert saved['unit_scale_to_mm'] == 1
    assert saved['region']['bounds_mm'] == pytest.approx([-1, -1, 101, 1])
    run = changed['geometry']['elements']['duct'][0]
    assert run['points'][-1] == [100, 0]
    assert run['width_mm'] == 110 and run['height_mm'] == 54
    floor = session.preview_state()['geometry']['source_drawing']['floors'][0]
    assert floor['primitives'][0]['points'][-1] == [100, 0]


@pytest.mark.parametrize('value', [0, -1, math.inf, math.nan, True])
def test_invalid_units_never_write_project(tmp_path, value):
    path = drawing(tmp_path)
    session = session_for(tmp_path, path)
    original = session.store.path.read_bytes()
    configure = getattr(session, 'configure_units', None)
    assert callable(configure)
    with pytest.raises(ValueError):
        configure(value, 0, session.store.read()['project_id'], source_sha256=fingerprint(path))
    assert session.store.path.read_bytes() == original


def test_unit_review_cannot_apply_to_a_different_source_hash(tmp_path):
    path = drawing(tmp_path)
    session = session_for(tmp_path, path)
    configure = getattr(session, 'configure_units', None)
    assert callable(configure)
    with pytest.raises(ValueError, match='hash|SHA|source'):
        configure(1, 0, session.store.read()['project_id'], source_sha256='0' * 64)
    assert session.store.read()['revision'] == 0


def test_units_http_route_uses_authentication_and_revision_gate(tmp_path):
    import json
    import urllib.error
    import urllib.request
    path = drawing(tmp_path)
    session = session_for(tmp_path, path)
    state = session.state()
    body = dict(unit_scale_to_mm=1, expected_revision=0, project_id=state['project_id'],
                source_id='main', source_sha256=fingerprint(path))
    with session.serve() as server:
        def send(token):
            req = urllib.request.Request(server.base_url + '/source-units',
                data=json.dumps(body).encode(), headers={'Content-Type': 'application/json',
                                                        'Authorization': 'Bearer ' + token})
            return urllib.request.urlopen(req)
        with pytest.raises(urllib.error.HTTPError) as denied:
            send('wrong-token')
        assert denied.value.code == 401
        with send(server.token) as response:
            assert json.load(response)['geometry']['scale_applied'] == 1
        with pytest.raises(urllib.error.HTTPError) as stale:
            send(server.token)
        assert stale.value.code == 409


def test_hidden_unit_dialog_compares_reference_and_saves_real_project(tmp_path):
    import importlib.util
    assert importlib.util.find_spec('drawing_units_ui') is not None, 'Unit review must be available in the desktop UI'
    import tkinter as tk
    import time
    from drawing_units_ui import UnitSetupDialog
    path = drawing(tmp_path)
    session = session_for(tmp_path, path)
    root = tk.Tk(); root.withdraw()
    saved = []
    dialog = UnitSetupDialog(root, session, inspect_mep_source(path), saved.append)
    dialog.win.withdraw()
    try:
        assert 'inch' in dialog.summary.get()
        assert '2540' in dialog.comparison.get()
        dialog.reference_raw.set('100')
        dialog.reference_mm.set('100')
        dialog.compare_button.invoke()
        assert float(dialog.scale.get()) == 1
        assert '100' in dialog.comparison.get()
        dialog.save_button.invoke()
        deadline = time.monotonic() + 10
        while not saved and time.monotonic() < deadline:
            root.update(); time.sleep(.01)
        assert len(saved) == 1
        reopened = ProjectSession(ProjectStore(session.store.folder))
        assert reopened.state()['geometry']['scale_applied'] == 1
        assert reopened.preview_state()['geometry']['source_drawing']['floors'][0]['primitives'][0]['points'][1] == [100, 0]
    finally:
        dialog.win.destroy(); root.destroy()


@pytest.mark.parametrize('category, width', [('column', 400), ('opening', 900), ('equipment', 800)])
def test_fallback_block_dimensions_are_mm_not_source_units(category, width):
    doc = ezdxf.new(units=1)
    doc.blocks.new('SYMBOL').add_line((0, 0), (1, 1))
    insert = doc.modelspace().add_blockref('SYMBOL', (2, 3))
    record = dp.insert_to_records(insert, 25.4, category, {'width': width})[0]
    if category == 'opening':
        assert record['center'] == pytest.approx([50.8, 76.2])
        assert record['radius'] * 2 == width
    else:
        xs = [p[0] for p in record['points']]
        assert max(xs) - min(xs) == pytest.approx(width)


def test_stdio_source_unit_review_and_apply_share_project_revision(tmp_path):
    import asyncio
    import json
    import os
    import sys
    from pathlib import Path
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    path = drawing(tmp_path)
    output = tmp_path / 'model.json'
    root = Path(__file__).resolve().parents[1]
    async def check():
        params = StdioServerParameters(command=sys.executable, args=[str(root / 'mep_mcp_server.py')],
            cwd=str(root), env={**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                names = {t.name for t in (await client.list_tools()).tools}
                assert {'get_source_units', 'set_source_units'} <= names
                async def call(name, args):
                    result = await client.call_tool(name, args)
                    assert not result.isError
                    return '\n'.join(block.text for block in result.content if block.type == 'text')
                await call('parse_dxf', {'dxf_path': str(path), 'json_out_path': str(output),
                                       'use_ai': False, 'use_vision': False})
                before = json.loads(await call('get_source_units', {'json_path': str(output)}))
                assert before['inventory']['scale_to_mm'] == pytest.approx(25.4)
                args = {'json_path': str(output), 'unit_scale_to_mm': 1,
                        'expected_revision': before['revision'], 'project_id': before['project_id'],
                        'source_sha256': before['inventory']['source_sha256']}
                refused = json.loads(await call('set_source_units', args))
                assert refused['error'] == 'user_review_required'
                result = json.loads(await call('set_source_units', dict(args, reviewed_by_user=True)))
                assert result['revision'] == before['revision'] + 1
                after = json.loads(await call('get_source_units', {'json_path': str(output)}))
                assert after['inventory']['scale_to_mm'] == 1
                stale = json.loads(await call('set_source_units', dict(args, reviewed_by_user=True)))
                assert stale['error'] == 'revision_conflict'
    asyncio.run(asyncio.wait_for(check(), 60))


@pytest.mark.parametrize('via_profile', [False, True])
def test_unit_change_cannot_transfer_an_edit_to_a_different_source_at_old_coordinates(tmp_path, via_profile):
    doc = ezdxf.new(units=4)
    doc.modelspace().add_circle((100, 100), 10, dxfattribs={'layer': 'A-COLS'})
    doc.modelspace().add_circle((1000, 1000), 100, dxfattribs={'layer': 'A-COLS'})
    doc.modelspace().add_line((0, 0), (100, 0), dxfattribs={'layer': 'SUPPLY'})
    path = tmp_path / 'colliding.dxf'; doc.saveas(path)
    session = session_for(tmp_path, path)
    state = session.state()
    old = next(r for r in state['geometry']['elements']['column'] if r['radius'] == 100)
    state = session.edit(old['eid'], {'overrides': {'height': 4321}, 'review_resolved': True},
                         0, state['project_id'])
    if via_profile:
        profile = {'version': 1, 'source_sha256': fingerprint(path), 'unit_scale_to_mm': 10,
            'layers': [{'pattern': '^SUPPLY$', 'category': 'pipe', 'system': 'test',
                       'representation': 'centerline', 'diameter_mm': 20,
                       'placement': 'source', 'dimension_basis': 'user'}]}
        changed = session.configure_source(profile, 1, state['project_id'])
    else:
        changed = session.configure_units(10, 1, state['project_id'], source_sha256=fingerprint(path))
    columns = changed['geometry']['elements']['column']
    assert not any(r.get('overrides', {}).get('height') == 4321 for r in columns)
    assert not any(r.get('review_resolved') for r in columns)
    orphans = changed['geometry']['edits_report']['orphaned']
    assert len(orphans) == 1
    assert changed['edits'][orphans[0]]['overrides']['height'] == 4321
    reopened = ProjectSession(ProjectStore(session.store.folder)).state()
    assert reopened['geometry']['edits_report']['orphaned'] == orphans


@pytest.mark.parametrize('kind', ['ARC', 'LWPOLYLINE', 'ELLIPSE'])
@pytest.mark.parametrize('scale,radius', [(25.4, 1000), (1000, 1)])
def test_curve_tessellation_error_is_measured_in_mm(kind, scale, radius):
    doc = ezdxf.new(); m = doc.modelspace()
    if kind == 'ARC':
        e = m.add_arc((0, 0), radius, 0, 90)
    elif kind == 'LWPOLYLINE':
        e = m.add_lwpolyline([(radius, 0, math.tan(math.pi / 8)), (0, radius, 0)], format='xyb')
    else:
        e = m.add_ellipse((0, 0), major_axis=(radius, 0), ratio=1, start_param=0, end_param=math.pi / 2)
    points = dp.entity_to_record(e, scale)['points']
    errors = [radius * scale - math.hypot((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
              for a, b in zip(points, points[1:])]
    assert max(errors) <= 5.00001


def test_legacy_project_keeps_old_unit_policy_until_explicit_revisioned_change(tmp_path):
    from project_store import atomic_json
    doc = ezdxf.new(units=1)
    doc.modelspace().add_circle((100, 100), 10, dxfattribs={'layer': 'A-COLS'})
    doc.modelspace().add_circle((2540, 2540), 254, dxfattribs={'layer': 'A-COLS'})
    path = tmp_path / 'legacy.dxf'; doc.saveas(path)
    session = session_for(tmp_path, path, {'unit_scale_to_mm': 1})
    state = session.state()
    old = next(r for r in state['geometry']['elements']['column'] if r['radius'] == 254)
    session.edit(old['eid'], {'overrides': {'height': 4321}, 'review_resolved': True}, 0, state['project_id'])
    # Recreate a pre-unit-review manifest: old parser used scale 1 for inch headers.
    legacy = session.store.read()
    legacy['sources'][0]['options'].pop('unit_scale_to_mm')
    legacy['sources'][0].pop('unit_policy', None)
    atomic_json(session.store.path, legacy)
    reopened = ProjectSession(ProjectStore(session.store.folder))
    state = reopened.preview_state()
    assert state['revision'] == 1
    assert state['geometry']['scale_applied'] == 1
    assert state['geometry']['unit_review']['basis'] == 'legacy_header_policy'
    assert reopened.source_units()['inventory']['scale_to_mm'] == 1
    assert state['geometry']['source_drawing']['floors'][0]['bbox'][2] == 2794
    target = next(r for r in state['geometry']['elements']['column'] if r['radius'] == 254)
    assert target['overrides']['height'] == 4321
    changed = reopened.configure_units(25.4, 1, state['project_id'], source_sha256=fingerprint(path))
    assert changed['revision'] == 2
    assert len(changed['geometry']['edits_report']['orphaned']) == 1
    assert not any(r.get('overrides', {}).get('height') == 4321 for r in changed['geometry']['elements']['column'])


def test_exact_inch_selection_does_not_orphan_unchanged_header_edits(tmp_path):
    doc = ezdxf.new(units=1)
    doc.modelspace().add_circle((10, 10), 5, dxfattribs={'layer': 'A-COLS'})
    path = tmp_path / 'inch.dxf'; doc.saveas(path)
    session = session_for(tmp_path, path)
    state = session.state()
    column = state['geometry']['elements']['column'][0]
    state = session.edit(column['eid'], {'overrides': {'height': 4321}}, 0, state['project_id'])
    changed = session.configure_units(25.4, 1, state['project_id'], source_sha256=fingerprint(path))
    assert changed['geometry']['edits_report']['orphaned'] == []
    assert changed['geometry']['elements']['column'][0]['overrides']['height'] == 4321
