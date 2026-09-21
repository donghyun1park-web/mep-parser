# -*- coding: utf-8 -*-
"""프로젝트 기본값(`source.options.defaults`) — 레이어·프로필 선언이 없을 때만 채우는 선언.

카테고리 추정이 아니다: 사람이 프로젝트를 열 때 한 번 적은 값이고 `declaration_basis` 로
자기보고한다. 레이어맵 opts·MEP 프로필 행의 선언이 있으면 기본값은 손대지 않는다.
"""
import hashlib
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ezdxf
import pytest
import dxf_parser as dp


def _dxf(tmp_path, layers):
    """layers: {layer명: [(x0,y0),(x1,y1)]} — 각 레이어에 선 하나."""
    d = ezdxf.new(); d.header['$INSUNITS'] = 4
    for name, pts in layers.items():
        d.layers.new(name)
        d.modelspace().add_line(pts[0], pts[1], dxfattribs={'layer': name})
    p = tmp_path / 'plan.dxf'; d.saveas(p)
    return str(p)


def _rules_csv(tmp_path, rows):
    p = tmp_path / 'layer_map.csv'
    p.write_text('\n'.join(['pattern,category,width,height,thickness,opts'] + rows), encoding='utf-8')
    return dp.load_layer_map(str(p))


# ── 건축: layer_map material= 이 있으면 기본값이 손대지 않는다 ──────────────
def test_architecture_default_fills_only_walls_without_a_declared_material(tmp_path):
    path = _dxf(tmp_path, {'A-CON': [(0, 0), (5000, 0)], 'A-STEEL': [(0, 1000), (5000, 1000)]})
    rules = _rules_csv(tmp_path, ['^A-CON$,wall,200,2800,,material=콘크리트', '^A-STEEL$,wall,150,2800,,'])
    defaults = {'architecture': {'wall': {'material': '기본재질'}}}
    d = dp.parse(path, rules, block_rules=[], defaults=defaults)
    by_layer = {w['layer']: w for w in d['elements']['wall']}
    assert by_layer['A-CON']['overrides']['material'] == '콘크리트'
    assert 'declaration_basis' not in by_layer['A-CON']            # 선언이 있으면 기본값은 안 건드린다
    assert by_layer['A-STEEL']['material'] == '기본재질'
    assert by_layer['A-STEEL']['declaration_basis'] == {'material': 'project_default'}
    assert d['defaults_effective']['source'] == defaults
    assert d['defaults_effective']['applied'] == {'material': 1}


def test_no_defaults_means_no_defaults_effective_key(tmp_path):
    path = _dxf(tmp_path, {'A-CON': [(0, 0), (5000, 0)]})
    rules = _rules_csv(tmp_path, ['^A-CON$,wall,200,2800,,'])
    d = dp.parse(path, rules, block_rules=[])
    assert 'defaults_effective' not in d


# ── 설비: 평면 layer_map 경로(프로필 없음) ──────────────────────────────────
def test_plain_layer_map_mep_gets_defaults_for_nominal_and_service(tmp_path):
    path = _dxf(tmp_path, {'M-PIPE-A': [(0, 0), (5000, 0)], 'M-PIPE-B': [(0, 1000), (5000, 1000)]})
    rules = _rules_csv(tmp_path, [
        '^M-PIPE-A$,pipe,100,,,nominal=DN100;service=domestic_cold;material=강관',
        '^M-PIPE-B$,pipe,100,,,'])
    defaults = {'mep': {'pipe': {'material': '강관', 'nominal_size': 'DN50', 'service': 'drain'}}}
    d = dp.parse(path, rules, block_rules=[], defaults=defaults)
    by_layer = {p['layer']: p for p in d['elements']['pipe']}
    assert (by_layer['M-PIPE-A']['nominal_size'], by_layer['M-PIPE-A']['service']) == ('DN100', 'domestic_cold')
    assert by_layer['M-PIPE-A']['overrides']['material'] == '강관'
    assert 'declaration_basis' not in by_layer['M-PIPE-A']          # 셋 다 선언이 있으면 기본값은 안 건드린다
    assert (by_layer['M-PIPE-B']['nominal_size'], by_layer['M-PIPE-B']['service']) == ('DN50', 'drain')
    assert by_layer['M-PIPE-B']['material'] == '강관'
    assert by_layer['M-PIPE-B']['declaration_basis'] == {
        'material': 'project_default', 'nominal_size': 'project_default', 'service': 'project_default'}
    assert d['defaults_effective']['applied'] == {'material': 1, 'nominal_size': 1, 'service': 1}


# ── 설비: MEP 프로필 경로(_assign) ──────────────────────────────────────────
def _profile_source(tmp_path):
    d = ezdxf.new(); d.units = 4
    d.layers.new('COIL')
    d.modelspace().add_line((0, 0), (1000, 0), dxfattribs={'layer': 'COIL'})
    p = tmp_path / 'plan.dxf'; d.saveas(p)
    return p


def _profile(path, **row_extra):
    row = {'pattern': '^COIL$', 'category': 'pipe', 'system': 'heating',
           'representation': 'centerline', 'diameter_mm': 15.9,
           'dimension_basis': 'assumed', 'placement': 'foam_top'}
    row.update(row_extra)
    return {'version': 1, 'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'layers': [row],
            'levels': {'structural_slab_top_mm': 0, 'floor_to_floor_mm': 2800, 'slab_thickness_mm': 200},
            'floor_layers': [{'role': 'impact_insulation', 'thickness_mm': 30},
                             {'role': 'foamed_concrete', 'thickness_mm': 40},
                             {'role': 'screed', 'thickness_mm': 40}]}


def test_mep_profile_row_declaration_beats_the_project_default(tmp_path):
    path = _profile_source(tmp_path)
    prof = _profile(path, material='PB', nominal_size='15A', service='domestic_hot')
    defaults = {'mep': {'pipe': {'material': '기본재질', 'nominal_size': 'DN9999', 'service': 'drain'}}}
    d = dp.parse(str(path), [], block_rules=[], mep_profile=prof, defaults=defaults)
    rec = d['elements']['pipe'][0]
    assert (rec['material'], rec['nominal_size'], rec['service']) == ('PB', '15A', 'domestic_hot')
    assert 'declaration_basis' not in rec
    assert d['defaults_effective']['applied'] == {}


def test_mep_profile_row_without_declarations_takes_the_project_default(tmp_path):
    path = _profile_source(tmp_path)
    prof = _profile(path)   # material/nominal_size/service 미선언
    defaults = {'mep': {'pipe': {'material': '기본재질', 'nominal_size': 'DN50', 'service': 'drain'}}}
    d = dp.parse(str(path), [], block_rules=[], mep_profile=prof, defaults=defaults)
    rec = d['elements']['pipe'][0]
    assert (rec['material'], rec['nominal_size'], rec['service']) == ('기본재질', 'DN50', 'drain')
    assert rec['declaration_basis'] == {
        'material': 'project_default', 'nominal_size': 'project_default', 'service': 'project_default'}
    # 프로필 경로는 overrides.material 도 같이 실을 이유가 없다(row 선언만 그렇게 한다) — 편집이 이길 자리를 지킨다.
    assert 'material' not in (rec.get('overrides') or {})
    assert d['defaults_effective']['applied'] == {'material': 1, 'nominal_size': 1, 'service': 1}


def test_mep_profile_insulation_default_is_applied_only_when_undeclared(tmp_path):
    path = _profile_source(tmp_path)
    defaults = {'mep': {'pipe': {'insulation': {'grade': '가', 'fluid_temp_c': 20}}}}
    d = dp.parse(str(path), [], block_rules=[], mep_profile=_profile(path), defaults=defaults)
    rec = d['elements']['pipe'][0]
    assert rec['insulation_decl'] == {'grade': '가', 'fluid_temp_c': 20}
    assert rec['declaration_basis'] == {'insulation': 'project_default'}
    d2 = dp.parse(str(path), [], block_rules=[],
                  mep_profile=_profile(path, insulation={'grade': '나'}), defaults=defaults)
    rec2 = d2['elements']['pipe'][0]
    assert rec2['insulation_decl'] == {'grade': '나'}
    assert 'declaration_basis' not in rec2


# ── 안전장치 ────────────────────────────────────────────────────────────────
def test_defaults_do_not_change_review_signature(tmp_path):
    """기본값은 review_signature 가 보는 필드(source_snapshot)에 없다 — 확인한 부재가
    기본값 도입만으로 '검토 필요' 로 되돌아가면 안 된다."""
    from element_id import review_signature
    path = _dxf(tmp_path, {'A-STEEL': [(0, 0), (5000, 0)]})
    rules = _rules_csv(tmp_path, ['^A-STEEL$,wall,150,2800,,'])
    without = dp.parse(path, rules, block_rules=[])['elements']['wall'][0]
    with_defaults = dp.parse(path, rules, block_rules=[],
                              defaults={'architecture': {'wall': {'material': '기본재질'}}})['elements']['wall'][0]
    assert with_defaults.get('declaration_basis')                  # 기본값은 실제로 적용됐다
    assert review_signature('wall', without, {}) == review_signature('wall', with_defaults, {})


def test_source_options_defaults_reach_stack_build_without_typeerror(tmp_path):
    """source.options 의 모든 키가 parse(**options) 로 풀린다 — defaults 도 다른 옵션과 같은 문이다."""
    import stack_build as SB
    path = _dxf(tmp_path, {'A-STEEL': [(0, 0), (5000, 0)]})
    rules_csv = tmp_path / 'layer_map.csv'
    rules_csv.write_text('pattern,category,width,height,thickness,opts\n^A-STEEL$,wall,150,2800,,\n', encoding='utf-8')
    g = SB.build_stack({'levels': [{'id': 'L1', 'source': path, 'layer_map': str(rules_csv), 'z': 0,
                                     'options': {'defaults': {'architecture': {'wall': {'material': '기본재질'}}}}}]})
    assert g['elements']['wall'][0]['material'] == '기본재질'


def demo():
    tmp = Path(tempfile.mkdtemp())
    test_architecture_default_fills_only_walls_without_a_declared_material(tmp)
    print("ok")


if __name__ == "__main__":
    demo()


# ── validate_defaults — 오타는 저장 시점에 거절한다 ─────────────────────────
def test_validate_defaults_rejects_unknown_category_and_service_and_bad_insulation():
    from mep_profile import validate_defaults
    with pytest.raises(ValueError, match='category'):
        validate_defaults({'mep': {'no_such_cat': {'material': 'PB'}}})
    with pytest.raises(ValueError, match='service'):
        validate_defaults({'mep': {'pipe': {'service': 'heating'}}})   # system 과 혼동한 흔한 오타
    with pytest.raises(ValueError, match='grade'):
        validate_defaults({'mep': {'pipe': {'insulation': {'grade': 'X'}}}})
    with pytest.raises(ValueError, match='category'):
        validate_defaults({'architecture': {'no_such_cat': {'material': '콘크리트'}}})
    with pytest.raises(ValueError, match='non-empty'):
        validate_defaults({'architecture': {'wall': {'material': ''}}})


def test_validate_defaults_accepts_the_documented_schema_and_strips_empty_categories():
    from mep_profile import validate_defaults
    out = validate_defaults({
        'mep': {'pipe': {'material': ' 강관 ', 'nominal_size': 'DN100', 'service': 'domestic_cold',
                         'insulation': {'grade': '나', 'humid': False, 'fluid_temp_c': 20}},
                'duct': {}},   # 빈 카테고리는 결과에서 빠진다
        'architecture': {'wall': {'material': '콘크리트'}}})
    assert out['mep']['pipe']['material'] == '강관'                # 앞뒤 공백은 정리한다
    assert 'duct' not in out['mep']
    assert out['architecture'] == {'wall': {'material': '콘크리트'}}
    assert validate_defaults({}) == {}


# ── ProjectSession.configure_defaults — 서버 저장 왕복 ──────────────────────
def test_configure_defaults_saves_reopens_and_applies_on_next_parse(tmp_path):
    from project_store import ProjectStore
    from project_server import ProjectSession
    path = _dxf(tmp_path, {'A-STEEL': [(0, 0), (5000, 0)]})
    layer_map = tmp_path / 'layer_map.csv'
    layer_map.write_text('pattern,category,width,height,thickness,opts\n^A-STEEL$,wall,150,2800,,\n', encoding='utf-8')
    store = ProjectStore(tmp_path / 'proj.mep').create(sources=[{'id': 'main', 'path': path, 'layer_map': str(layer_map)}])
    sess = ProjectSession(store)
    state = sess.state()
    assert 'material' not in state['geometry']['elements']['wall'][0]
    saved = sess.configure_defaults({'architecture': {'wall': {'material': '기본재질'}}},
                                     state['revision'], state['project_id'])
    assert saved['revision'] == state['revision'] + 1
    assert saved['geometry']['elements']['wall'][0]['material'] == '기본재질'
    assert sess.store.read()['decisions'][-1]['action'] == 'configure_defaults'
    reopened = ProjectSession(ProjectStore(sess.store.folder)).state()
    assert reopened['geometry']['elements']['wall'][0]['material'] == '기본재질'


def test_open_source_project_height_sets_level_height_only_at_creation(tmp_path):
    """`mep_gui` 의 '열 때 층고 질문' → `open_source_project(height=...)` → `source['height']` →
    기존 `level_height` 경로(파서가 이미 하던 일). 재파싱(같은 도면 재사용)에서는 새 height 를 안 받는다
    — GUI 의 `_pending_open_defaults` 가 한 번 쓰면 비우는 것과 같은 계약이다."""
    from project_server import open_source_project
    path = _dxf(tmp_path, {'A-STEEL': [(0, 0), (5000, 0)]})
    layer_map = tmp_path / 'layer_map.csv'
    layer_map.write_text('pattern,category,width,height,thickness,opts\n^A-STEEL$,wall,150,2800,,\n', encoding='utf-8')
    sess = open_source_project(path, layer_map=str(layer_map), height=3000.0)
    wall = sess.state()['geometry']['elements']['wall'][0]
    assert wall['overrides']['height'] == 3000.0
    assert sess.store.read()['sources'][0]['height'] == 3000.0
    # 같은 도면으로 다시 열면(프로젝트가 이미 있다) height 인자는 무시된다 — 생성 시점 전용
    again = open_source_project(path, layer_map=str(layer_map), height=9000.0)
    assert again.store.read()['sources'][0].get('height') == 3000.0


def test_configure_defaults_rejects_a_typo_before_touching_the_project(tmp_path):
    from project_store import ProjectStore
    from project_server import ProjectSession
    path = _dxf(tmp_path, {'A-STEEL': [(0, 0), (5000, 0)]})
    store = ProjectStore(tmp_path / 'proj.mep').create(sources=[{'id': 'main', 'path': path}])
    sess = ProjectSession(store)
    state = sess.state()
    with pytest.raises(ValueError):
        sess.configure_defaults({'mep': {'pipe': {'service': 'heating'}}}, state['revision'], state['project_id'])
    assert sess.store.read()['revision'] == state['revision']


def test_http_defaults_endpoint_round_trips(tmp_path):
    import json
    import urllib.request
    from project_store import ProjectStore
    from project_server import ProjectSession
    path = _dxf(tmp_path, {'A-STEEL': [(0, 0), (5000, 0)]})
    layer_map = tmp_path / 'layer_map.csv'
    layer_map.write_text('pattern,category,width,height,thickness,opts\n^A-STEEL$,wall,150,2800,,\n', encoding='utf-8')
    store = ProjectStore(tmp_path / 'proj.mep').create(sources=[{'id': 'main', 'path': path, 'layer_map': str(layer_map)}])
    sess = ProjectSession(store)
    with sess.serve() as server:
        state_req = urllib.request.Request(server.base_url + '/state',
            headers={'Authorization': 'Bearer ' + server.token})
        with urllib.request.urlopen(state_req) as r:
            state = json.load(r)
        body = {'project_id': state['project_id'], 'expected_revision': state['revision'],
                'defaults': {'architecture': {'wall': {'material': '기본재질'}}}}
        req = urllib.request.Request(server.base_url + '/defaults', data=json.dumps(body).encode(),
            headers={'Authorization': 'Bearer ' + server.token, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(req) as r:
            saved = json.load(r)
        assert saved['geometry']['elements']['wall'][0]['material'] == '기본재질'
