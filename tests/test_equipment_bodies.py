"""장비 본체 제안 — 겹친 기호에서 어느 면이 본체인지 **제안**한다. 고르는 것은 사람이다."""
import hashlib
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ezdxf
import pytest
import dxf_parser as dp
from mep_profile import measure_equipment_bodies, split_rule_by_equipment_bodies


RULE = {'pattern': '^DIFF$', 'category': 'equipment', 'system': 'SA', 'representation': 'outline',
        'height_mm': 100.0, 'role': 'terminal', 'dimension_basis': 'user', 'placement': 'center',
        'center_elevation_mm': 2500.0}


def _profile(path, layers):
    return {'version': 1, 'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'layers': layers, 'levels': {'structural_slab_top_mm': 0}, 'floor_layers': []}


def _diffuser_drawing(tmp_path, radii=(79, 50, 48), positions=((0, 0), (3000, 0))):
    """블록 하나에 동심원 여러 겹 — 실무 디퓨저 기호가 그렇다(실측: 한 블록에 지름 158·101·97)."""
    doc = ezdxf.new(units=4)
    doc.layers.new('DIFF')
    block = doc.blocks.new(name='apt-diff')
    for r in radii:
        block.add_circle((0, 0), r, dxfattribs={'layer': 'DIFF'})
    for at in positions:
        doc.modelspace().add_blockref('apt-diff', at, dxfattribs={'layer': 'DIFF'})
    path = tmp_path / 'diff.dxf'
    doc.saveas(path)
    return path


def test_the_outer_face_of_a_layered_symbol_is_suggested_as_the_one_body(tmp_path):
    path = _diffuser_drawing(tmp_path)
    result = measure_equipment_bodies(path, RULE)
    assert len(result['instances']) == 2 and result['ambiguous_instances'] == []
    faces = result['instances'][0]['faces']
    assert len(faces) == 3 and sorted(f['area_mm2'] for f in faces)[-1] == faces[result['instances'][0]['body_face']]['area_mm2']
    # 블록 안쪽 핸들은 인스턴스가 달라도 같다 — 핸들 하나가 위치마다 한 겹만 남긴다.
    assert 'source_handles' in result['suggestion'] and result['suggestion']['instances'] == 2
    assert len(result['suggestion']['source_handles']) == 1


def test_the_suggested_rule_leaves_one_body_per_position_and_no_overlap_warning(tmp_path):
    path = _diffuser_drawing(tmp_path)
    before = dp.parse(str(path), [], block_rules=[], mep_profile=_profile(path, [RULE]))
    assert len(before['elements']['equipment']) == 6          # 2곳 × 3겹 — 물량이 세 배로 부푼다
    assert sum(i['code'] == 'EQUIPMENT_OVERLAP' for i in before['mep_diagnostics']['issues'])

    rule, status = split_rule_by_equipment_bodies(RULE, measure_equipment_bodies(path, RULE))
    assert status == 'suggested'
    after = dp.parse(str(path), [], block_rules=[], mep_profile=_profile(path, [rule]))
    assert len(after['elements']['equipment']) == 2
    assert not any(i['code'] == 'EQUIPMENT_OVERLAP' for i in after['mep_diagnostics']['issues'])


def test_a_symbol_whose_open_outline_encloses_another_is_left_for_the_user(tmp_path):
    """열린 선이 한 인스턴스 안에서 겹겹으로 면을 이루면 저장이 막힌다 — 그 자리를 이 도구가 짚어 준다."""
    doc = ezdxf.new(units=4)
    doc.layers.new('EQ')
    block = doc.blocks.new(name='unit')
    for x0, y0, x1, y1 in ((0, 0, 600, 400), (100, 100, 500, 300)):
        for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
            block.add_line(a, b, dxfattribs={'layer': 'EQ'})      # 닫힌 폴리선이 아니라 네 토막
    doc.modelspace().add_blockref('unit', (0, 0), dxfattribs={'layer': 'EQ'})
    path = tmp_path / 'unit.dxf'; doc.saveas(path)
    rule = dict(RULE, pattern='^EQ$', role='equipment')
    result = measure_equipment_bodies(path, rule)
    assert len(result['ambiguous_instances']) == 1
    assert result['ambiguous_instances'][0]['reason'] == 'outline_has_hole'
    assert result['suggestion'] is None
    assert split_rule_by_equipment_bodies(rule, result) == (None, 'ambiguous_instances')
    # 저장은 여전히 막힌다 — 이 도구는 진단이지 우회로가 아니다.
    with pytest.raises(ValueError, match='Ambiguous equipment outline'):
        dp.parse(str(path), [], block_rules=[], mep_profile=_profile(path, [rule]))


def test_symbols_drawn_directly_rather_than_in_a_block_are_suggested_by_exact_source(tmp_path):
    """직접 그린 기호는 인스턴스마다 핸들이 다르다 — 핸들 하나로는 못 고른다."""
    doc = ezdxf.new(units=4)
    doc.layers.new('DIFF')
    for x in (0, 3000):
        for r in (79, 50):
            doc.modelspace().add_circle((x, 0), r, dxfattribs={'layer': 'DIFF'})
    path = tmp_path / 'direct.dxf'; doc.saveas(path)
    result = measure_equipment_bodies(path, RULE)
    assert len(result['instances']) == 1                   # 블록이 아니면 인스턴스 하나다
    # 네 원이 한 인스턴스에 있고 서로를 덮지 않는다 — 코드가 고를 근거가 없다.
    assert result['suggestion'] is None and result['ambiguous_instances'][0]['reason'] == 'no_single_covering_face'


def test_a_symbol_that_already_makes_one_body_needs_no_filter(tmp_path):
    """면이 이미 하나면 좁힐 것이 없다 — 없는 제안을 만들어 규칙을 흔들지 않는다."""
    path = _diffuser_drawing(tmp_path, radii=(79,))
    result = measure_equipment_bodies(path, RULE)
    assert result['already_single_body'] and result['suggestion'] is None
    assert split_rule_by_equipment_bodies(RULE, result) == (None, 'already_single_body')


def test_sources_the_pipeline_keeps_apart_by_height_are_kept_apart_here_too(tmp_path):
    """실측: 슬리브 사각형과 그 안의 X 표시가 z 로 9.45e-6mm 어긋나 파이프라인에서 다른 묶음이 된다 —
    그래서 사각형이 X 에 사분되지 않는다. 여기서 높이를 반올림하면 도구만 없는 문제를 보고한다."""
    doc = ezdxf.new(units=4)
    doc.layers.new('SLV')
    ms = doc.modelspace()
    box = [(0, 0), (125, 0), (125, 200), (0, 200)]
    for a, b in zip(box, box[1:] + box[:1]):
        ms.add_line((a[0], a[1], 1000.0), (b[0], b[1], 1000.0), dxfattribs={'layer': 'SLV'})
    for a, b in (((0, 0), (125, 200)), ((125, 0), (0, 200))):     # 안쪽 X 표시 — z 가 미세하게 다르다
        ms.add_line((a[0], a[1], 1000.0000000094), (b[0], b[1], 1000.0000000094), dxfattribs={'layer': 'SLV'})
    path = tmp_path / 'sleeve.dxf'; doc.saveas(path)
    rule = dict(RULE, pattern='^SLV$', role='sleeve', placement='source')
    result = measure_equipment_bodies(path, rule)
    areas = [f['area_mm2'] for i in result['instances'] for f in i['faces']]
    assert 25000.0 in areas and 6250.0 not in areas      # 사각형 한 장이지 사분면 넷이 아니다
    assert result['already_single_body']
