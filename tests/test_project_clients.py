import json
from pathlib import Path
import pytest
from test_project_server import session


def test_mcp_rejects_index_without_revision_and_writes_canonical_eid(tmp_path):
    import mep_mcp_server as mcp
    sess = session(tmp_path)
    path, first = sess.export_geometry(tmp_path / 'geometry.json')
    eid = first['geometry']['elements']['wall'][0]['eid']
    assert 'revision' in mcp.update_geometry_overrides('wall', 0, {'height':3550}, path).lower()
    assert sess.store.read()['revision'] == 0
    result = mcp.update_geometry_overrides('wall', -1, {'height':3550}, path, eid=eid, expected_revision=0)
    assert json.loads(result)['revision'] == 1
    assert sess.store.read()['edits_by_floor']['main'][eid]['overrides']['height'] == 3550
    assert sess.state()['geometry']['elements']['wall'][0]['overrides']['height'] == 3550


def test_gui_save_refreshes_from_canonical_project(tmp_path):
    from mep_gui import App
    sess = session(tmp_path)
    first = sess.state()
    eid = first['geometry']['elements']['wall'][0]['eid']
    app = App.__new__(App)
    app.project_session = sess
    app.geom_path = str(tmp_path / 'geometry.json')
    app.data = first['geometry']
    sess.edit(eid, {'overrides':{'height':4110}},0,first['project_id'])
    app._save()
    saved = json.loads(Path(app.geom_path).read_text())
    assert saved['project']['revision'] == 1
    assert saved['elements']['wall'][0]['overrides']['height'] == 4110

def test_artifact_claim_requires_current_receipt_and_actual_hash(tmp_path):
    import project_server
    import artifact_validation as av
    assert hasattr(project_server,'verified_artifacts'), 'client artifact receipts must be verified'
    geometry={'project':{'project_id':'p','revision':4},'elements':{}}
    artifact=tmp_path/'out.FCStd'
    artifact.write_bytes(b'fresh artifact')
    provenance={'run_id':'run-new','input_sha256':av.input_hash(geometry),'project_id':'p','revision':4}
    receipt={'provenance':provenance,'artifacts':{'fcstd':{'status':'verified','path':str(artifact),'sha256':av.file_hash(str(artifact)),'provenance':provenance}}}
    report=tmp_path/'out.build.json'
    report.write_text(json.dumps(receipt))
    assert project_server.verified_artifacts(report,geometry,'run-new')['fcstd']['status']=='verified'
    assert project_server.verified_artifacts(report,geometry,'run-old')['fcstd']['status']=='failed'
    artifact.write_bytes(b'corrupted')
    assert project_server.verified_artifacts(report,geometry,'run-new')['fcstd']['status']=='failed'
