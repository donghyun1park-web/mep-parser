import importlib.util
import json
import urllib.request
import urllib.error
import pytest
import ezdxf
from project_store import ProjectStore, RevisionConflict


def session(tmp_path):
    assert importlib.util.find_spec('project_server'), 'loopback project service missing'
    from project_server import ProjectSession
    dxf = tmp_path / 'drawing.dxf'
    doc = ezdxf.new(units=4)
    doc.layers.new('WALL')
    for y in (0,200):
        doc.modelspace().add_line((0,y),(5000,y), dxfattribs={'layer':'WALL'})
    doc.saveas(dxf)
    store = ProjectStore(tmp_path / 'drawing.mep').create(sources=[{'id':'main','path':str(dxf)}])
    return ProjectSession(store)


def test_http_save_reopen_and_token_origin_guards(tmp_path):
    sess = session(tmp_path)
    with sess.serve() as server:
        def request(path, payload=None, token=True, origin=None):
            headers = {'Authorization':'Bearer '+server.token} if token else {}
            if origin:
                headers['Origin'] = origin
            if payload is not None:
                headers['Content-Type']='application/json'
            req = urllib.request.Request(server.base_url+path, data=None if payload is None else json.dumps(payload).encode(), headers=headers)
            with urllib.request.urlopen(req) as r:
                return json.load(r)
        state = request('/state')
        eid = state['geometry']['elements']['wall'][0]['eid']
        body = {'project_id':state['project_id'],'expected_revision':0,'edits':{eid:{'overrides':{'height':3210}}}}
        saved = request('/edits',body)
        assert saved['revision'] == 1
        assert saved['geometry']['elements']['wall'][0]['overrides']['height'] == 3210
        assert '_source' in saved['edits'][eid]
        for path, payload, token, origin, code in [('/state',None,False,None,401),('/edits',body,True,'https://evil.example',403),('/edits',body,True,None,409),('/../../secret',None,True,None,404)]:
            with pytest.raises(urllib.error.HTTPError) as error:
                request(path,payload,token,origin)
            assert error.value.code == code
        from project_server import ProjectSession
        reopened = ProjectSession(ProjectStore(sess.store.folder)).state()
        assert reopened['revision'] == 1
        assert reopened['geometry']['elements']['wall'][0]['overrides']['height'] == 3210


def _thin_pair_session(tmp_path):
    """A-CON 레이어: 250mm 로 깨끗이 짝지어진 벽 8개 + **경쟁하는 세 선**(0·50·250mm) 자리 2곳.
    가까운 50mm 쪽이 먼저 짝을 먹어(그리디) thin_pair 로 걸린다 — `pair_min` 을 250 의 1/3(83mm)
    로 주면 50mm 후보가 범위 밖으로 밀려 250mm 쪽이 대신 짝지어진다(walls.md 의 실측과 같은 모양).
    layer_map 은 **프로젝트 폴더 안**의 사본이라 apply_layer_suggestion 이 쓸 수 있다."""
    import ezdxf
    from project_server import ProjectSession
    dxf = tmp_path / 'drawing.dxf'
    doc = ezdxf.new()
    doc.header['$INSUNITS'] = 4
    msp = doc.modelspace()
    for i in range(8):
        y0 = i * 2000
        msp.add_line((0, y0), (5000, y0), dxfattribs={'layer': 'A-CON'})
        msp.add_line((0, y0 + 250), (5000, y0 + 250), dxfattribs={'layer': 'A-CON'})
    for i in range(2):
        y0 = 20000 + i * 2000
        msp.add_line((0, y0), (5000, y0), dxfattribs={'layer': 'A-CON'})
        msp.add_line((0, y0 + 50), (5000, y0 + 50), dxfattribs={'layer': 'A-CON'})
        msp.add_line((0, y0 + 250), (5000, y0 + 250), dxfattribs={'layer': 'A-CON'})
    doc.saveas(dxf)
    dxf_path = str(dxf)
    layer_map = tmp_path / 'layer_map.csv'
    layer_map.write_text('\n'.join(['pattern,category,width,height,thickness,opts', '^A-CON$,wall,,2800,,']), encoding='utf-8')
    store = ProjectStore(tmp_path / 'proj.mep').create(sources=[{'id': 'main', 'path': dxf_path, 'layer_map': str(layer_map)}])
    return ProjectSession(store), str(layer_map)


def test_applying_a_thin_pair_suggestion_fixes_the_pairing_and_writes_under_the_header(tmp_path):
    sess, layer_map = _thin_pair_session(tmp_path)
    state = sess.state()
    assert state['geometry']['thin_pairs'] == {'A-CON': 2}
    (suggestion,) = [s for s in state['geometry']['suggestions_apply'] if s['code'] == 'thin_pair']
    saved = sess.apply_layer_suggestion(suggestion, state['revision'], state['project_id'])
    assert saved['revision'] == state['revision'] + 1
    assert saved['geometry']['tolerances_effective']['per_layer']['^A-CON$'] == {'pair_min': float(suggestion['opts']['pair_min'])}
    assert 'thin_pairs' not in saved['geometry']
    lines = open(layer_map, encoding='utf-8').read().splitlines()
    assert lines[0].startswith('pattern,') and lines[1].startswith('^A-CON$')  # 헤더 바로 아래
    assert sess.store.read()['decisions'][-1]['action'] == 'apply_layer_rule'


def test_applying_the_same_suggestion_twice_does_not_duplicate_the_row(tmp_path):
    sess, layer_map = _thin_pair_session(tmp_path)
    state = sess.state()
    (suggestion,) = [s for s in state['geometry']['suggestions_apply'] if s['code'] == 'thin_pair']
    once = sess.apply_layer_suggestion(suggestion, state['revision'], state['project_id'])
    rows_after_first = open(layer_map, encoding='utf-8').read().splitlines()
    twice = sess.apply_layer_suggestion(suggestion, once['revision'], once['project_id'])
    assert open(layer_map, encoding='utf-8').read().splitlines() == rows_after_first
    assert twice['revision'] == once['revision']            # 바뀐 게 없으니 revision 도 그대로


def test_apply_layer_suggestion_rejects_a_stale_revision(tmp_path):
    sess, _ = _thin_pair_session(tmp_path)
    state = sess.state()
    (suggestion,) = [s for s in state['geometry']['suggestions_apply'] if s['code'] == 'thin_pair']
    sess.apply_layer_suggestion(suggestion, state['revision'], state['project_id'])
    with pytest.raises(RevisionConflict):
        sess.apply_layer_suggestion(suggestion, state['revision'], state['project_id'])


def test_apply_layer_suggestion_rolls_back_an_unknown_opts_key(tmp_path):
    sess, layer_map = _thin_pair_session(tmp_path)
    state = sess.state()
    before = open(layer_map, encoding='utf-8').read()
    bad = {'code': 'thin_pair', 'row_layer': 'A-CON', 'pattern': '^A-CON$',
           'op': 'set_opts', 'category': 'wall', 'opts': {'no_such_key': 1}}
    with pytest.raises(ValueError):
        sess.apply_layer_suggestion(bad, state['revision'], state['project_id'])
    assert open(layer_map, encoding='utf-8').read() == before
    assert sess.store.read()['revision'] == state['revision']


def test_apply_layer_suggestion_rejects_a_source_with_no_project_local_csv(tmp_path):
    from project_server import ProjectSession
    dxf = tmp_path / 'drawing.dxf'
    import ezdxf
    ezdxf.new().saveas(dxf)
    store = ProjectStore(tmp_path / 'proj.mep').create(sources=[{'id': 'main', 'path': str(dxf)}])
    sess = ProjectSession(store)
    state = sess.state()
    suggestion = {'code': 'thin_pair', 'row_layer': 'A-CON', 'pattern': '^A-CON$',
                  'op': 'set_opts', 'category': 'wall', 'opts': {'pair_min': 67}}
    with pytest.raises(ValueError, match='로컬'):
        sess.apply_layer_suggestion(suggestion, state['revision'], state['project_id'])


def test_apply_layer_suggestion_refuses_an_mep_profile_source(tmp_path, monkeypatch):
    sess, _ = _thin_pair_session(tmp_path)
    state = sess.state()
    manifest = sess.store.read()
    manifest['sources'][0]['options'] = {'mep_profile': {'version': 1}}
    from project_store import atomic_json
    atomic_json(sess.store.path, manifest)
    suggestion = {'code': 'thin_pair', 'row_layer': 'A-CON', 'pattern': '^A-CON$',
                  'op': 'set_opts', 'category': 'wall', 'opts': {'pair_min': 67}}
    with pytest.raises(ValueError, match='설비 프로필'):
        sess.apply_layer_suggestion(suggestion, sess.store.read()['revision'], state['project_id'])


def test_save_parses_once_and_the_returned_revision_is_immediately_usable(tmp_path, monkeypatch):
    """save() 는 제안을 검증하려 한 번 파싱한다 — 커밋 뒤 state() 가 또 파싱하면 안 된다."""
    sess = session(tmp_path)
    state = sess.state()
    eid = state['geometry']['elements']['wall'][0]['eid']
    calls = []
    orig = sess.__class__._parse
    def counted(self, manifest):
        calls.append(1)
        return orig(self, manifest)
    monkeypatch.setattr(sess.__class__, '_parse', counted)
    saved = sess.save({eid: {'overrides': {'height': 3100}}}, 0, state['project_id'])
    assert len(calls) == 1
    assert saved['revision'] == 1
    assert saved['geometry']['project']['revision'] == 1          # 다음 저장의 expected_revision 근거
    assert saved['geometry']['elements']['wall'][0]['overrides']['height'] == 3100
    # 되돌려준 revision 이 바로 다음 저장에 쓰인다 — 캐시가 낡은 값이면 여기서 409 가 난다
    again = sess.save({eid: {'overrides': {'height': 3200}}}, saved['revision'], state['project_id'])
    assert again['revision'] == 2


def test_source_change_reports_orphans_and_explicit_discard(tmp_path):
    sess = session(tmp_path)
    first = sess.state()
    eid = first['geometry']['elements']['wall'][0]['eid']
    sess.save({eid:{'overrides':{'width':180}}},0,first['project_id'])
    doc = ezdxf.readfile(sess.store.read()['sources'][0]['path'])
    for ent in doc.modelspace():
        ent.dxf.end = (6000,ent.dxf.end.y,0)
    doc.saveas(sess.store.read()['sources'][0]['path'])
    latest = sess.state()
    assert eid in latest['geometry']['edits_report']['orphaned']
    assert latest['geometry']['project']['changed_inputs']
    assert latest['revision'] == 2
    deferred = sess.defer(eid, latest['revision'], first['project_id'])
    assert eid in deferred['geometry']['edits_report']['orphaned']
    assert sess.store.read()['decisions'][-1]['action'] == 'defer'
    result = sess.discard(eid,deferred['revision'],first['project_id'])
    assert result['revision'] == 4
    assert eid not in result['edits']
    assert sess.store.read()['decisions'][-1]['action'] == 'discard'

def test_acknowledgement_binds_to_proposed_edits_and_old_source(tmp_path):
    sess = session(tmp_path)
    state = sess.state()
    eid = state['geometry']['elements']['wall'][0]['eid']
    saved = sess.edit(eid, {'overrides':{'width':190},'acknowledge':True},0,state['project_id'])
    rec = saved['geometry']['elements']['wall'][0]
    from element_id import review_signature
    assert saved['edits'][eid]['_review_signature'] == review_signature('wall',rec,saved['geometry']['params'])
    assert not rec.get('needs_review')
    source = saved['edits'][eid]['_source']
    resaved = sess.edit(eid, {'overrides':{'width':195}},1,state['project_id'])
    assert resaved['edits'][eid]['_source'] == source
    assert resaved['edits'][eid]['_review_signature'] == saved['edits'][eid]['_review_signature']

def test_pipe_elevation_override_saves_reopens_and_moves_the_z_range(tmp_path):
    """overrides.elevation 은 이미 geom_contract.base_z 가 읽는다 — 저장·재열기 왕복만 확인한다."""
    from project_server import ProjectSession
    dxf = tmp_path / 'drawing.dxf'
    doc = ezdxf.new(units=4)
    doc.layers.new('MEP-PIPE')
    doc.modelspace().add_lwpolyline([(0, 0), (1000, 0)], dxfattribs={'layer': 'MEP-PIPE'})
    doc.saveas(dxf)
    layer_map = tmp_path / 'layer_map.csv'
    layer_map.write_text('\n'.join(['pattern,category,width,height,thickness,opts', '^MEP-PIPE$,pipe,,,,']), encoding='utf-8')
    store = ProjectStore(tmp_path / 'proj.mep').create(sources=[{'id':'main','path':str(dxf),'layer_map':str(layer_map)}])
    sess = ProjectSession(store)
    state = sess.state()
    eid = state['geometry']['elements']['pipe'][0]['eid']
    saved = sess.edit(eid, {'overrides': {'elevation': 2600}}, 0, state['project_id'])
    rec = saved['geometry']['elements']['pipe'][0]
    import geom_contract as GC
    assert GC.base_z('pipe', rec) == 2600
    assert rec['elevation'] != 2600, '도면값(최상위 elevation)은 그대로 — 편집은 overrides 에만 산다'
    reopened = ProjectSession(ProjectStore(sess.store.folder)).state()
    changed = reopened['geometry']['elements']['pipe'][0]
    assert changed['overrides']['elevation'] == 2600
    cleared = sess.edit(eid, {'overrides': {'elevation': None}}, saved['revision'], state['project_id'])
    assert 'elevation' not in (cleared['geometry']['elements']['pipe'][0].get('overrides') or {})


def test_new_manual_element_explicit_acknowledgement_has_fingerprint(tmp_path):
    sess=session(tmp_path)
    first=sess.state()
    edits={'user:opening-1':{'added':True,'category':'opening','acknowledge':True,
        'record':{'kind':'polyline','points':[[1000,0],[2000,0],[2000,200],[1000,200]],'closed':True,
                  'subtype':'door','height':2100,'sill':0}}}
    result=sess.save(edits,0,first['project_id'])
    assert result['edits']['user:opening-1'].get('_review_signature'), 'explicit new element review must use final parse diagnostics'

def test_two_floor_project_reopens_local_edits_and_acknowledgements(tmp_path):
    original=session(tmp_path)
    source=original.store.read()['sources'][0]['path']
    from project_server import ProjectSession
    sources=[{'id':'1F','path':source,'height':2800,'z':0,'offset':[0,0]},
             {'id':'2F','path':source,'height':3200,'z':4000,'offset':[100,300]}]
    sess=ProjectSession(ProjectStore(tmp_path/'stack.mep').create(sources))
    first=sess.state()
    wall=next(r for r in first['geometry']['elements']['wall'] if r['level']=='2F')
    saved=sess.edit(wall['eid'],{'overrides':{'height':3350},'acknowledge':True},0,first['project_id'])
    local_eid=wall['eid'].removeprefix('2F:')
    stored=sess.store.read()['edits_by_floor']['2F'][local_eid]
    assert stored['overrides']['height']==3350
    assert stored['_source'].get('level') in (None,'')
    reopened=ProjectSession(ProjectStore(sess.store.folder)).state()
    changed=next(r for r in reopened['geometry']['elements']['wall'] if r['eid']==wall['eid'])
    assert changed['overrides']['height']==3350
    assert changed['z_base']==4000
    assert not changed.get('needs_review')
    untouched=next(r for r in reopened['geometry']['elements']['wall'] if r['level']=='1F')
    assert untouched['overrides']['height']==2800


def test_invalid_candidate_never_replaces_current_or_previous_project(tmp_path):
    sess = session(tmp_path)
    first = sess.state()
    eid = first['geometry']['elements']['wall'][0]['eid']
    saved = sess.edit(eid, {'overrides': {'height': 2900}}, 0, first['project_id'])
    before = sess.store.path.read_bytes()
    backup = sess.store.backup.read_bytes()
    edits = dict(saved['edits'])
    edits['wm:bad'] = {'added': True, 'category': 'wall',
                       'record': {'kind': 'polyline', 'points': [[0], [1000]], 'closed': False}}
    with pytest.raises(ValueError):
        sess.save(edits, saved['revision'], first['project_id'])
    assert sess.store.path.read_bytes() == before
    assert sess.store.backup.read_bytes() == backup
    from project_server import ProjectSession
    assert ProjectSession(sess.store).state()['revision'] == saved['revision']
    imported = tmp_path / 'bad-edits.json'
    imported.write_text(json.dumps(edits))
    with pytest.raises(ValueError):
        sess.import_legacy(imported, saved['revision'], first['project_id'])
    assert sess.store.path.read_bytes() == before
    assert sess.store.backup.read_bytes() == backup


def test_relink_rejects_other_floor_and_other_category(tmp_path):
    original = session(tmp_path)
    source = original.store.read()['sources'][0]['path']
    doc = ezdxf.readfile(source)
    doc.layers.new('COLUMN')
    doc.modelspace().add_circle((1000, 1000), 200, dxfattribs={'layer': 'COLUMN'})
    doc.saveas(source)
    from project_server import ProjectSession
    sess = ProjectSession(ProjectStore(tmp_path / 'stack.mep').create([
        {'id': 'L1', 'path': source, 'z': 0}, {'id': 'L2', 'path': source, 'z': 3000}]))
    first = sess.state()
    eid = next(r['eid'] for r in first['geometry']['elements']['wall'] if r['level'] == 'L1')
    sess.edit(eid, {'overrides': {'height': 2222}}, 0, first['project_id'])
    doc = ezdxf.readfile(source)
    for ent in doc.modelspace().query('LINE'):
        ent.dxf.end = (6000, ent.dxf.end.y, 0)
    doc.saveas(source)
    latest = sess.state()
    elements = latest['geometry']['elements']
    bad_targets = [next(r['eid'] for r in elements['wall'] if r['level'] == 'L2'),
                   next(r['eid'] for r in elements['column'] if r['level'] == 'L1')]
    for target in bad_targets:
        with pytest.raises(ValueError, match='eligible'):
            sess.relink(eid, target, latest['revision'], first['project_id'])
    assert sess.store.read()['revision'] == latest['revision']
    target = next(r['eid'] for r in elements['wall'] if r['level'] == 'L1')
    relinked = sess.relink(eid, target, latest['revision'], first['project_id'])
    assert relinked['edits'][target]['overrides']['height'] == 2222
