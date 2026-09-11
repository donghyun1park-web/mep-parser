"""Project-local MEP settings and Codex proposals share the same revision gate."""
import copy
import json
import sys
import types

import pytest
from project_store import ProjectStore, RevisionConflict, fingerprint
from project_server import ProjectSession


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    source = tmp_path / 'plan.dxf'
    source.write_bytes(b'fixture drawing')
    store = ProjectStore(tmp_path / 'plan.mep').create([{'id': 'main', 'path': str(source)}])
    session = ProjectSession(store)
    def validate(profile, source_sha256=None):
        if profile.get('source_sha256') != source_sha256:
            raise ValueError('source hash mismatch')
        if profile.get('invalid'):
            raise ValueError('invalid profile')
        return copy.deepcopy(profile)
    monkeypatch.setitem(sys.modules, 'mep_profile', types.SimpleNamespace(validate_profile=validate))
    def parse(manifest):
        options = manifest['sources'][0]['options']
        if options.get('mep_profile', {}).get('parse_failure'):
            raise ValueError('candidate parse failed')
        return {'elements': {}, 'mep_profile': options.get('mep_profile'), 'project': {
            'project_id': manifest['project_id'], 'revision': manifest['revision']}}
    monkeypatch.setattr(session, '_parse', parse)
    return session, {'version': 1, 'source_sha256': fingerprint(source), 'layers': []}


def test_profile_configure_is_revisioned_and_reopenable(workflow):
    session, profile = workflow
    first = session.store.read()
    state = session.configure_source(profile, 0, first['project_id'])
    assert state['revision'] == 1
    assert ProjectStore(session.store.folder).read()['sources'][0]['options']['mep_profile'] == profile
    with pytest.raises(RevisionConflict):
        session.configure_source(profile, 0, first['project_id'])


def test_invalid_candidate_does_not_modify_manifest_or_backup(workflow):
    session, profile = workflow
    first = session.store.path.read_bytes()
    with pytest.raises(ValueError, match='candidate'):
        session.configure_source(dict(profile, parse_failure=True), 0, session.store.read()['project_id'])
    assert session.store.path.read_bytes() == first
    assert not session.store.backup.exists()


def test_proposal_cannot_change_geometry_or_acknowledge_reviews(workflow):
    session, profile = workflow
    first = session.store.read()
    proposal = session.propose_mep_profile(profile, 0, first['project_id'], reason='Layer names inspected')
    assert session.store.read() == first
    assert session.state()['geometry']['mep_profile'] is None
    assert session.mep_proposals()[0]['proposal_id'] == proposal['proposal_id']
    state = session.apply_mep_proposal(proposal['proposal_id'], 0, first['project_id'])
    assert state['revision'] == 1
    assert state['edits'] == {}
    assert session.mep_proposals() == []
    assert session.store.read()['decisions'][-1]['action'] == 'apply_mep_profile'


def test_proposal_rejects_changed_revision_and_source(workflow):
    session, profile = workflow
    first = session.store.read()
    proposal = session.propose_mep_profile(profile, 0, first['project_id'])
    session.configure_source(profile, 0, first['project_id'])
    with pytest.raises(RevisionConflict):
        session.apply_mep_proposal(proposal['proposal_id'], 1, first['project_id'])


def test_proposal_id_cannot_escape_project(workflow):
    session, _ = workflow
    with pytest.raises(ValueError, match='proposal'):
        session.apply_mep_proposal('../outside', 0, session.store.read()['project_id'])


def test_mep_gui_properties_use_category_dimensions():
    from mep_setup_ui import mep_property_overrides
    assert mep_property_overrides('pipe', '15.9', '') == {'diameter': 15.9}
    assert mep_property_overrides('duct', '204', '60') == {'width_mm': 204., 'height_mm': 60.}
    assert mep_property_overrides('wall', '200', '2600') == {'width': 200., 'height': 2600.}
    with pytest.raises(ValueError):
        mep_property_overrides('pipe', 'nan', '')


def test_verified_files_still_show_source_review_findings():
    from mep_setup_ui import blender_review_status
    summary, detail = blender_review_status({'status': 'verified', 'source_verification': {
        'warnings': 1, 'findings': [{'id': 'V012', 'severity': 'warn', 'message': 'Selected source omitted: 2'}]},
        'diagnostics': [{'code': 'floor_review', 'message': 'Wet-area details not evaluated'}]})
    assert '2건' in summary
    assert detail == ['V012: Selected source omitted: 2', 'floor_review: Wet-area details not evaluated']


def test_hidden_tk_profile_form_preserves_custom_region_and_site_values(workflow):
    import tkinter as tk
    from mep_setup_ui import MepSetupDialog
    session, profile = workflow
    root = tk.Tk()
    root.withdraw()
    inventory = {'source_sha256': profile['source_sha256'], 'scale_to_mm': 1,
                 'bounds_mm': [0, 0, 1000, 1000], 'layers': [{'name': 'COIL', 'count': 2,
                 'entity_types': {'LINE': 1, 'ARC': 1}, 'colors': [1]}],
                 'regions': [{'id': 'layout_1', 'bounds_mm': [0, 0, 1000, 1000]}]}
    saved = []
    dialog = None
    try:
        dialog = MepSetupDialog(root, session, inventory, saved.append)
        dialog.win.withdraw()
        root.update_idletasks()
        assert len(dialog.layer_tree.get_children()) == 1
        assert dialog.level_vars['floor_to_floor_mm'].get() == ''
        assert dialog.level_vars['impact_insulation'].get() == ''
        rule = {'pattern': '^COIL$', 'category': 'pipe', 'system': 'heating', 'diameter_mm': 15.9,
                'placement': 'source', 'representation': 'centerline', 'dimension_basis': 'user'}
        # Same id is not enough: a smaller explicit region must never widen on roundtrip.
        custom = dict(profile, layers=[rule], region={'id': 'layout_1', 'bounds_mm': [50, 60, 700, 800]})
        dialog._load_profile(custom)
        candidate = dialog._form_profile()
        assert candidate['region'] == custom['region']
        assert candidate['floor_layers'] == []
        assert candidate['levels'] == {'structural_slab_top_mm': 0.}
        dialog.level_vars['unit_scale_to_mm'].set('10')
        with pytest.raises(ValueError, match='단위'):
            dialog._form_profile()
        dialog._rescan_done(dict(inventory, scale_to_mm=10, regions=[]))
        with pytest.raises(ValueError, match='영역'):
            dialog._form_profile()
        dialog.region_select.current(0)
        assert dialog._form_profile()['unit_scale_to_mm'] == 10
        dialog._saved({'revision': 1, 'geometry': {}})
        assert saved == [{'revision': 1, 'geometry': {}}]
    finally:
        if dialog:
            dialog.win.destroy()
        root.destroy()


def test_hidden_tk_proposals_only_show_active_source(workflow):
    import tkinter as tk
    from mep_setup_ui import MepSetupDialog
    session, profile = workflow
    manifest = session.store.read()
    first = session.propose_mep_profile(profile, 0, manifest['project_id'])
    foreign = dict(first, proposal_id='f' * 32, source_id='other')
    from project_store import atomic_json
    atomic_json(session.store.folder / 'proposals' / ('f' * 32 + '.json'), foreign)
    root = tk.Tk()
    root.withdraw()
    dialog = None
    try:
        dialog = MepSetupDialog(root, session, {'source_sha256': profile['source_sha256'],
            'scale_to_mm': 1, 'bounds_mm': None, 'layers': [], 'regions': []}, lambda _: None)
        dialog.win.withdraw()
        assert len(dialog.proposals) == 1
        assert dialog.proposals[0]['source_id'] == 'main'
        dialog.proposal_list.selection_set(0)
        dialog._load_proposal()
        assert dialog.proposal['proposal_id'] == first['proposal_id']
    finally:
        if dialog:
            dialog.win.destroy()
        root.destroy()
