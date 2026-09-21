"""Project-local MEP settings and Codex proposals share the same revision gate."""
import copy
import json
import sys
import types

import pytest
from tkinter import ttk
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


def test_review_count_drops_the_runner_copy_and_groups_one_message_per_line():
    # blender_runner 가 게이트 경고를 diagnostics 에 복사한다 — 같은 건이 두 번 세어지면 안 된다.
    from mep_setup_ui import blender_review_status
    warn = {'id': 'V007', 'severity': 'warn', 'message': 'needs_review 2'}
    summary, detail = blender_review_status({'source_verification': {'warnings': 1, 'findings': [warn]},
        'diagnostics': [dict(warn, code='V007')] + [{'code': 'reference_not_solid', 'eid': f'o:{i}',
        'message': 'Opening retained'} for i in range(16)]})
    assert '17건' in summary
    assert detail == ['V007: needs_review 2', 'reference_not_solid: Opening retained (16건)']


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


def test_hidden_tk_defaults_tab_loads_and_forms_mep_declarations(workflow):
    """'프로젝트 기본값' 소탭 — 선언 없으면 빈 채로, 채우면 defaults.mep 모양으로, 숫자 아닌 온도는 거절."""
    import tkinter as tk
    from mep_setup_ui import MepSetupDialog
    session, profile = workflow
    root = tk.Tk()
    root.withdraw()
    inventory = {'source_sha256': profile['source_sha256'], 'scale_to_mm': 1,
                 'bounds_mm': [0, 0, 1000, 1000], 'layers': [],
                 'regions': [{'id': 'layout_1', 'bounds_mm': [0, 0, 1000, 1000]}]}
    dialog = None
    try:
        dialog = MepSetupDialog(root, session, inventory, lambda _s: None)
        dialog.win.withdraw()
        root.update_idletasks()
        assert dialog._form_mep_defaults() == {}
        dialog._load_defaults({'mep': {'pipe': {'material': '강관', 'service': 'domestic_cold',
                                                 'insulation': {'grade': '나', 'humid': True, 'fluid_temp_c': 10}}}})
        assert dialog.defaults_vars['pipe']['material'].get() == '강관'
        assert dialog.defaults_vars['pipe']['service'].get() == 'domestic_cold'
        assert dialog.defaults_vars['pipe']['grade'].get() == '나'
        assert dialog.defaults_vars['pipe']['humid'].get() is True
        assert dialog.defaults_vars['pipe']['fluid_temp_c'].get() == '10'
        assert dialog._form_mep_defaults() == {'pipe': {'material': '강관', 'service': 'domestic_cold',
                                                         'insulation': {'grade': '나', 'humid': True, 'fluid_temp_c': 10.0}}}
        dialog.defaults_vars['duct']['grade'].set('가')
        dialog.defaults_vars['duct']['fluid_temp_c'].set('오')
        with pytest.raises(ValueError):
            dialog._form_mep_defaults()
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


def test_hidden_tk_main_window_shows_four_controls_and_keeps_every_tool(workflow):
    """첫 화면은 넷뿐이다 — 나머지는 접혀 있을 뿐 하나도 지우지 않았다."""
    import tkinter as tk
    from mep_gui import App
    root = tk.Tk()
    root.withdraw()
    try:
        app = App(root)
        bar = root.winfo_children()[0]
        assert [child.cget('text') for child in bar.winfo_children()] == [
            '열기…', '설비 도면 설정', '납품 ▾', '그 밖의 도구 ▾']
        assert not app.tools.winfo_manager()                 # 기본 접힘
        app.v_tools.set(True); app._toggle_tools(); root.update_idletasks()
        assert app.tools.winfo_manager()
        # 접힌 도구는 전부 살아 있다(기능 손실 0).
        for name in ('_do_scan', '_do_diag', '_do_review_csv', '_do_build', '_do_blender_build',
                     '_do_boq', '_apply_review', '_pick_edits', '_do_add_source', '_show_checklist'):
            assert callable(getattr(app, name)), name
        assert app._open_after_parse is False
        # 진행 중 비활성화는 접힌 프레임 안까지 닿아야 한다(한 겹 더 깊다).
        app._set_buttons('disabled')
        buried = [w for w in app.tools.winfo_children() for w in w.winfo_children()
                  if isinstance(w, ttk.Button)]
        assert buried and all('disabled' in w.state() for w in buried)
    finally:
        root.destroy()


def test_hidden_tk_delivery_menu_builds_ifc_directly_and_freecad_moves_to_the_tools_frame(workflow):
    """납품 IFC 는 `ifc_builder`(ifcopenshell 직접)가 낸다 — FreeCAD 설치가 없어도 눌린다.
    FreeCAD·Blender 는 지운 게 아니라 접은 '그 밖의 도구' 로 내렸다(핸들러도 살아 있다)."""
    import tkinter as tk
    from mep_gui import App
    root = tk.Tk()
    root.withdraw()
    try:
        app = App(root)
        labels = [app.export_menu.entrycget(i, 'label') for i in range(app.export_menu.index('end') + 1)]
        assert labels == ['검토 목록 CSV', '물량 Excel', '창호일람 Excel 내보내기', '납품 검증 빌드 (IFC)']
        assert 'FreeCAD' not in ' '.join(labels) and 'Blender' not in ' '.join(labels)
        # FreeCAD 가 없는 PC 에서도 납품 항목은 눌려야 한다 — 그게 이 전환의 요점이다.
        assert app.export_menu.entrycget(3, 'state') != 'disabled'
        app.v_tools.set(True); app._toggle_tools(); root.update_idletasks()
        tool_texts = [w.cget('text') for w in app.tools.winfo_children() for w in w.winfo_children()
                      if isinstance(w, (ttk.Button, ttk.Checkbutton))]
        assert any('FreeCAD' in t for t in tool_texts) and any('Blender' in t for t in tool_texts)
        assert callable(app._do_build) and callable(app._do_blender_build)
    finally:
        root.destroy()


def test_hidden_tk_setup_dialog_hides_handle_and_proposal_tabs_until_expert(workflow):
    """현장에서 쓸 일이 없는 핸들·정규식과 제안 검토는 숨긴다. 위젯은 그대로 살아 있다."""
    import tkinter as tk
    from mep_setup_ui import MepSetupDialog
    session, profile = workflow
    root = tk.Tk()
    root.withdraw()
    dialog = None
    try:
        dialog = MepSetupDialog(root, session, {'source_sha256': profile['source_sha256'],
            'scale_to_mm': 1, 'bounds_mm': None, 'layers': [], 'regions': []}, lambda _: None)
        dialog.win.withdraw()
        root.update_idletasks()
        shown = lambda: [dialog.tabs.tab(t, 'text') for t in dialog.tabs.tabs()
                         if dialog.tabs.tab(t, 'state') == 'normal']
        assert shown() == ['영역·레이어', '건축 분류', '프로젝트 기본값', '높이·바닥 구성']
        assert 'source_handles' in dialog.rule_vars and 'role' in dialog.rule_vars  # 숨겨도 변수는 산다
        dialog.v_expert.set(True); dialog._toggle_expert(); root.update_idletasks()
        assert shown() == ['영역·레이어', '설비 원본 필터', '건축 분류', '프로젝트 기본값', '높이·바닥 구성', 'Codex 제안 검토']
    finally:
        if dialog:
            dialog.win.destroy()
        root.destroy()


def test_drawing_kind_is_guessed_from_layer_names_only(tmp_path):
    """추측은 대화의 기본 선택을 채울 뿐이다. 설비 도면을 그냥 해석하면 배경이 기둥이 된다."""
    import ezdxf
    from mep_gui import guess_drawing_kind

    def drawing(name, layers):
        doc = ezdxf.new()
        for layer in layers:
            doc.layers.new(layer)
        path = tmp_path / name
        doc.saveas(path)
        return str(path)

    assert guess_drawing_kind(drawing('arch.dxf', ['A-WALL', 'A-COL', 'A-SLAB'])) == '건축'
    assert guess_drawing_kind(drawing('mep.dxf', ['M-DUCT', 'A-WALL'])) == '설비'   # 설비 이름이 이긴다
    # 벽·기둥 이름이 하나도 없으면 해석할 건축이 없다 — 실무 환기 도면이 그렇다(배경은 XREF).
    assert guess_drawing_kind(drawing('vent.dxf', ['SA', 'RA', 'BACK'])) == '설비'
    assert guess_drawing_kind(str(tmp_path / 'missing.dxf')) == '건축'              # 못 읽으면 종전 경로


def test_saving_an_added_pipe_and_moving_a_jointed_one_updates_connectivity_live(tmp_path):
    """평면 탭이 만드는 두 가지 편집을 프로젝트 저장에 태운다.

    (1) 새로 그린(added) 배관은 저장 즉시 열린 끝으로 mep_connectivity 에 잡힌다.
    (2) 이음(joints)이 있던 배관을 delete+add(이동)로 옮기면 assign_joints 를 다시
        돌리지 않으므로(파싱 직후에만 돈다) 짝은 옛 joint id 를 들고 있다가 상대를
        잃는다 — verify_geometry 가 joint_single_member 로 알린다(현재 동작 기록).
    """
    import ezdxf
    import geom_contract as GC
    import verify

    dxf = tmp_path / 'drawing.dxf'
    doc = ezdxf.new(units=4)
    doc.layers.new('MEP-PIPE')
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (1000, 0)], dxfattribs={'layer': 'MEP-PIPE'})     # p:A
    msp.add_lwpolyline([(1000, 0), (2000, 0)], dxfattribs={'layer': 'MEP-PIPE'})  # p:B, A 와 맞닿는다
    doc.saveas(dxf)
    layer_map = tmp_path / 'layer_map.csv'
    layer_map.write_text('\n'.join(['pattern,category,width,height,thickness,opts', '^MEP-PIPE$,pipe,,,,']), encoding='utf-8')
    store = ProjectStore(tmp_path / 'proj.mep').create(sources=[{'id': 'main', 'path': str(dxf), 'layer_map': str(layer_map)}])
    sess = ProjectSession(store)
    state = sess.state()
    pipes = sorted(state['geometry']['elements']['pipe'], key=lambda r: r['points'][0][0])
    a, b = pipes
    assert a.get('joints') and b.get('joints'), '두 배관이 맞닿는 자리에서 이음이 안 잡혔다'
    assert a['joints'][0]['id'] == b['joints'][0]['id']

    moved_a_eid = 'main:wm:moved-a'
    edits = {
        a['eid']: {'deleted': True},
        moved_a_eid: {'added': True, 'category': 'pipe', 'record': {
            'kind': 'polyline', 'closed': False, 'points': [[0, 500], [1000, 500]],
            'centerline': [[0, 500], [1000, 500]], 'pairing': 'manual', 'layer': '(수동)',
            'confidence': 1, 'needs_review': False, 'source': 'manual_preview',
            'overrides': {'diameter': a.get('diameter', 100.0)}, 'eid': moved_a_eid,
            'level': 'main', 'elevation': a['elevation'], 'system': a.get('system')}},
    }
    saved = sess.save(edits, state['revision'], state['project_id'])

    # (1) 열린 끝: 옮긴 배관 둘 + 짝을 잃은 b 도 이제 열린 끝이다.
    open_ends = saved['geometry']['mep_connectivity']['open_ends']
    assert len(open_ends) >= 2, open_ends

    # (2) 이음 문제: b 가 여전히 들고 있는 joint id 의 상대가 사라졌다.
    report = verify.verify_geometry(saved['geometry'])
    joint_findings = [f for f in report.errors + report.warns if f.id == 'V012']
    reasons = {item.get('reason') for f in joint_findings for item in f.payload.get('sample', [])}
    assert 'joint_single_member' in reasons, joint_findings
