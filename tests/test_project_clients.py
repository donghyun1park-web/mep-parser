import json
import unittest
from pathlib import Path
import pytest
from test_project_server import session


def test_mcp_rejects_index_without_revision_and_writes_canonical_eid(tmp_path):
    try:
        import mep_mcp_server as mcp  # 선택 의존성(mcp SDK) — 없으면 건너뛴다
    except (ImportError, SystemExit) as exc:   # 이 모듈은 의존성이 없으면 sys.exit(1)
        raise unittest.SkipTest(f'mcp SDK 없음 — MCP 클라이언트 검사 미실행 ({exc})')
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
    saved = json.loads(Path(app.geom_path).read_text(encoding='utf-8'))    # 프로젝트는 UTF-8 로 쓴다(간섭 조치 문구 등 한글)
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
    # 납품 IFC 경로(`ifc_builder`)는 `.FCStd` 를 내지 않는다 — 영수증이 선언한 산출물만 센다.
    # 없는 'fcstd' 를 세면 성공한 빌드가 '검증 안 됨' 으로 보인다.
    ifc=tmp_path/'out.ifc'
    ifc.write_bytes(b'an ifc file')
    only_ifc={'provenance':provenance,'artifacts':{'ifc':{'status':'verified','path':str(ifc),'sha256':av.file_hash(str(ifc)),'provenance':provenance}}}
    report.write_text(json.dumps(only_ifc))
    result=project_server.verified_artifacts(report,geometry,'run-new')
    assert list(result)==['ifc'] and result['ifc']['status']=='verified'


def test_mcp_apply_layer_rule_inserts_above_existing_rules_not_at_the_end(tmp_path, monkeypatch):
    """끝에 붙이면 'COL|기둥' 같은 넓은 규칙에 가려져 아무 일도 안 난다 — 헤더 바로 아래에 넣는다.
    저장소의 실제 layer_map.csv 는 건드리지 않는다(임시 사본으로 리디렉션)."""
    try:
        import mep_mcp_server as mcp
    except (ImportError, SystemExit) as exc:
        raise unittest.SkipTest(f'mcp SDK 없음 — MCP 클라이언트 검사 미실행 ({exc})')
    csv_path = tmp_path / 'layer_map.csv'
    csv_path.write_text('pattern,category,width,height,thickness,opts\nCOL|기둥,column,,,,\n', encoding='utf-8')
    monkeypatch.setattr(mcp, 'LAYER_MAP', str(csv_path))
    msg = mcp.apply_layer_rule('배수판_벽체|배수판', 'ignore')
    assert 'Added rule' in msg
    lines = csv_path.read_text(encoding='utf-8').splitlines()
    assert lines[1].startswith('배수판_벽체|배수판,ignore')   # 헤더 바로 아래, COL 규칙보다 위
    assert lines[2].startswith('COL|기둥')
