import importlib.util
import json
import concurrent.futures
from pathlib import Path
import pytest


def store_class():
    assert importlib.util.find_spec('project_store'), 'durable project store is missing'
    from project_store import ProjectStore
    return ProjectStore


def create(tmp_path):
    source = tmp_path / 'drawing.dxf'
    source.write_text('source-v1')
    return store_class()(tmp_path / 'drawing.mep').create(sources=[{'id':'main','path':str(source)}])


def test_save_reopen_and_compare_swap(tmp_path):
    store = create(tmp_path)
    first = store.read()
    state = store.replace_edits({'L:1':{'overrides':{'width':220}}}, first['revision'], first['project_id'])
    assert state['revision'] == 1
    assert store_class()(store.folder).read()['edits_by_floor']['main']['L:1']['overrides']['width'] == 220
    from project_store import RevisionConflict
    with pytest.raises(RevisionConflict):
        store.replace_edits({}, 0, first['project_id'])
    assert store.read() == state


def test_concurrent_writers_exactly_one_succeeds(tmp_path):
    store = create(tmp_path)
    state = store.read()
    def write(n):
        from project_store import RevisionConflict
        try:
            return store_class()(store.folder).replace_edits({'L:1':{'overrides':{'width':n+1}}},0,state['project_id'])['revision']
        except RevisionConflict:
            return 'conflict'
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        outcomes = list(pool.map(write, range(5)))
    assert outcomes.count(1) == 1
    assert outcomes.count('conflict') == 4


def test_corruption_requires_explicit_backup_recovery(tmp_path):
    store = create(tmp_path)
    initial = store.read()
    store.replace_edits({'L:1':{'deleted':True}},0,initial['project_id'])
    store.path.write_text('{broken')
    from project_store import ProjectCorrupt
    with pytest.raises(ProjectCorrupt):
        store.read()
    recovered = store.recover_backup()
    assert recovered['revision'] > 1
    assert recovered['edits_by_floor']['main'] == {}
    assert list(store.folder.glob('project.corrupt.*.json'))


def test_import_preserves_original_and_rejects_project_mismatch(tmp_path):
    store = create(tmp_path)
    state = store.read()
    legacy = tmp_path / 'edits.json'
    raw = '{"L:1": {"overrides": {"width": 180}}}'
    legacy.write_text(raw)
    store.import_legacy(legacy, 0, state['project_id'])
    assert legacy.read_text() == raw
    assert next((store.folder / 'imports').iterdir()).read_text() == raw
    legacy.write_text(json.dumps({'project_id':'different','edits':{}}))
    with pytest.raises(ValueError, match='project'):
        store.import_legacy(legacy,1,state['project_id'])


def test_source_changes_detected_and_invalid_edit_rejected(tmp_path):
    store = create(tmp_path)
    state = store.read()
    Path(state['sources'][0]['path']).write_text('source-v2')
    assert store.changed_inputs() == ['main:path']
    with pytest.raises(ValueError):
        store.replace_edits({'L:1':{'overrides':{'width':float('nan')}}},0,state['project_id'])
    assert store.read()['revision'] == 0

def test_changed_source_invalidates_stale_client_revision(tmp_path):
    store = create(tmp_path)
    first = store.read()
    Path(first['sources'][0]['path']).write_text('updated-input')
    assert hasattr(store, 'refresh_inputs'), 'input changes must invalidate stale clients'
    latest = store.refresh_inputs()
    assert latest['revision'] == 1
    assert store.refresh_inputs()['revision'] == 1
    from project_store import RevisionConflict
    with pytest.raises(RevisionConflict):
        store.replace_edits({'L:1':{'deleted':True}},0,first['project_id'])


def test_interrupted_atomic_replace_keeps_previous_good_manifest(tmp_path, monkeypatch):
    store = create(tmp_path)
    first = store.read()
    import project_store
    replace = project_store.os.replace
    def interrupted(src, dst):
        if Path(dst) == store.path:
            raise OSError('simulated power interruption before manifest replacement')
        return replace(src,dst)
    monkeypatch.setattr(project_store.os, 'replace', interrupted)
    with pytest.raises(OSError, match='interruption'):
        store.replace_edits({'L:1':{'deleted':True}},0,first['project_id'])
    assert store.read() == first
    assert json.loads(store.backup.read_text()) == first
    assert not list(store.folder.glob('*.tmp'))


def test_separate_process_writers_cannot_lose_updates(tmp_path):
    store = create(tmp_path)
    first = store.read()
    import subprocess
    import sys
    code = "from project_store import *; import sys; s=ProjectStore(sys.argv[1]);\ntry: s.replace_edits({'L:1':{'deleted':True}},0,sys.argv[2]); print('saved')\nexcept RevisionConflict: print('conflict')"
    processes = [subprocess.Popen([sys.executable,'-c',code,str(store.folder),first['project_id']], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(3)]
    outputs = [p.communicate(timeout=30) for p in processes]
    assert all(p.returncode == 0 for p in processes), outputs
    assert sum(out.strip() == 'saved' for out,err in outputs) == 1
    assert sum(out.strip() == 'conflict' for out,err in outputs) == 2

def test_shifted_single_main_uses_world_namespace_and_local_storage(tmp_path):
    store = create(tmp_path)
    first = store.read()
    sources=first['sources']
    sources[0]['offset']=[120,250]
    sources[0]['z']=3000
    store.update_sources(sources,{},0,first['project_id'])
    state=store.replace_edits({'main:user:1':{'added':True,'category':'opening',
        'record':{'kind':'polyline','points':[[1120,250],[1320,250]],'z_base':3000}}},1,first['project_id'])
    assert 'user:1' in state['edits_by_floor']['main']
    assert state['edits_by_floor']['main']['user:1']['record']['points'][0] == [1000,0]
    assert state['edits_by_floor']['main']['user:1']['record']['z_base'] == 0

def test_invalid_backup_never_replaces_corrupt_original(tmp_path):
    store=create(tmp_path)
    first=store.read()
    store.replace_edits({},0,first['project_id'])
    store.path.write_text('{original-corrupt')
    backup=json.loads(store.backup.read_text())
    backup['project_id']='not-a-project-id'
    store.backup.write_text(json.dumps(backup))
    from project_store import ProjectCorrupt
    with pytest.raises((ValueError,ProjectCorrupt)):
        store.recover_backup()
    assert store.path.read_text() == '{original-corrupt'


def test_edit_validation_rejects_unknown_category_and_negative_dimensions(tmp_path):
    store=create(tmp_path)
    state=store.read()
    for edit in ({'category':'system'}, {'overrides':{'width':-1}}, {'overrides':{'height':'hello'}}):
        with pytest.raises(ValueError):
            store.replace_edits({'L:1':edit},0,state['project_id'])
    assert store.read()['revision'] == 0

def test_unresolved_stack_alignment_is_rejected_without_manifest(tmp_path):
    source=tmp_path/'floor.dxf'; source.write_text('source')
    store=store_class()(tmp_path/'stack.mep')
    with pytest.raises(ValueError, match='align'):
        store.create([{'id':'1F','path':str(source)}, {'id':'2F','path':str(source),'align':'1F'}])
    assert not store.path.exists()
