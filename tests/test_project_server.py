import importlib.util
import json
import urllib.request
import urllib.error
import pytest
import ezdxf
from project_store import ProjectStore


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
