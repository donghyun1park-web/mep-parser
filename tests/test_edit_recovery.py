"""Corrections keep their target, context and floor across a fresh parse."""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import element_id as E
import stack_build as SB


def wall(eid='w:a', x=0, y=0, **kw):
    r = {'eid': eid, 'kind': 'polyline', 'closed': False,
         'points': [[x, y], [x + 1000, y]],
         'centerline': [[x, y], [x + 1000, y]],
         'z_base': 0, 'width_detected': 200,
         'pairing': 'paired', 'layer': 'A-WALL'}
    r.update(kw)
    return r


def test_relink_ranks_nearest_same_floor_and_category_not_input_order():
    old = {'L1:w:old': {'_at': [0, 0], '_source': dict(wall(), category='wall', level='L1')}}
    elements = {'wall': [wall('L1:w:far', x=9000, level='L1'),
                          wall('L2:w:other', level='L2'), wall('L1:w:near', level='L1')],
                'column': [dict(wall('L1:c:col'), level='L1')]}
    result = E.suggest_relink(list(old), old, elements)
    assert result[0]['candidates'] == ['L1:w:near', 'L1:w:far']
    assert result[0]['details'][0]['distance_mm'] == 0


def test_missing_location_and_ambiguous_candidates_are_not_guessed():
    old = {'w:old': {'overrides': {'width': 300}}}
    result = E.suggest_relink(list(old), old, {'wall': [wall()]})
    assert result[0]['candidates'] == []
    old['w:old']['_at'] = [0, 0]
    result = E.suggest_relink(list(old), old, {'wall': [wall(), wall()]})
    assert result[0]['candidates'] == []


def test_relink_ties_are_deterministic_and_manual_wall_prefix_is_wall():
    old = {'wm:old': {'_at': [0, 0], '_source': dict(wall(), category='wall')}}
    elements = {'wall': [wall('w:b'), wall('w:a')]}
    a = E.suggest_relink(list(old), old, elements)[0]['candidates']
    elements['wall'].reverse()
    assert a == ['w:a', 'w:b'] == E.suggest_relink(list(old), old, elements)[0]['candidates']


def test_added_record_overrides_survive_and_sidecar_is_not_mutated():
    edits = {'wm:a': {'added': True, 'category': 'wall', 'record': wall('wm:a'),
                       'overrides': {'width': 350}}}
    before = copy.deepcopy(edits)
    els = {'wall': []}
    E.apply_edits(els, edits)
    assert els['wall'][0]['overrides']['width'] == 350
    els['wall'][0]['points'][0][0] = 999
    assert edits == before


def test_added_eid_cannot_overwrite_existing_dxf_record():
    els = {'wall': [wall()]}
    report = E.apply_edits(els, {'w:a': {'added': True, 'category': 'wall', 'record': wall(x=500)}})
    assert report['orphaned'] == ['w:a']
    assert els['wall'][0]['points'][0] == [0, 0]


def test_acknowledgement_is_bound_to_geometry_dimensions_and_reasons():
    rec = wall(needs_review=True, review_reason='single_offset')
    edit = E.capture_edit(rec, {'review_resolved': True}, 'wall')
    els = {'wall': [copy.deepcopy(rec)]}
    E.apply_edits(els, {'w:a': edit})
    E.finalize_reviews(els)
    assert not els['wall'][0]['needs_review']
    for change in ({'overrides': {'height': 3200}}, {'review_reason': 'thin_pair'},
                   {'centerline': [[0, 0], [1200, 0]]}):
        els = {'wall': [dict(copy.deepcopy(rec), **change)]}
        E.apply_edits(els, {'w:a': edit})
        E.finalize_reviews(els)
        assert els['wall'][0]['needs_review'] and els['wall'][0]['review_ack_stale']


def test_legacy_acknowledgement_requires_explicit_reconfirmation():
    els = {'wall': [wall(needs_review=True, review_reason='thin_pair')]}
    E.apply_edits(els, {'w:a': {'review_resolved': True}})
    E.finalize_reviews(els)
    assert els['wall'][0]['needs_review']
    assert not els['wall'][0]['review_resolved']


def test_property_edit_does_not_acknowledge_unrelated_problem():
    rec = wall(needs_review=True, review_reason='single_offset')
    edit = E.capture_edit(rec, {'overrides': {'width': 250}}, 'wall')
    els = {'wall': [rec]}
    E.apply_edits(els, {'w:a': edit})
    E.finalize_reviews(els)
    assert els['wall'][0]['needs_review']
    assert not edit.get('review_resolved')


def test_floor_local_roundtrip_moves_record_source_and_anchor_once():
    levels = [{'id': 'L1', 'z': 0, 'offset': [0, 0]},
              {'id': 'L2', 'z': 3000, 'offset': [5000, -2000]}]
    r = wall('L2:wm:a', x=5100, y=-1800, z_base=3000, level='L2')
    edits = {'L2:wm:a': {'added': True, 'category': 'wall', 'record': r,
                          '_at': [5100, -1800], '_source': dict(r, category='wall')}}
    original = copy.deepcopy(edits)
    local = SB.edits_to_local(edits, levels)
    e = local['L2']['wm:a']
    assert e['record']['points'][0] == [100, 200]
    assert e['record']['z_base'] == 0 and e['_source']['points'][0] == [100, 200]
    assert e['_at'] == [100, 200]
    assert SB.edits_to_world(local, levels) == original
    assert edits == original


def test_floor_conversion_rejects_unknown_or_missing_floor():
    for eid in ('L9:w:a', 'w:a'):
        try:
            SB.edits_to_local({eid: {'deleted': True}}, [{'id': 'L1', 'z': 0}])
        except ValueError:
            continue
        raise AssertionError('Unknown floor edit was assigned to another floor')


def test_opening_links_to_actual_polyline_segment_and_its_own_floor():
    import dxf_parser as dp
    lower = wall('w:low', centerline=[[0, 0], [1000, 0], [1000, 2000]])
    upper = copy.deepcopy(lower)
    upper.update(eid='w:high', z_base=4000)
    opening = {'kind': 'circle', 'center': [1000, 1400], 'radius': 300,
               'z_base': 4000, 'width': 600, 'height': 2000, 'sill': 0}
    elements = {'wall': [lower, upper], 'opening': [opening]}
    dp.link_openings_to_walls(elements, {})
    assert opening['wall_indices'] == [1]
    assert opening['host_dir'] == [0, 1]


def test_opening_relink_clears_deleted_host_and_uses_declared_width():
    import dxf_parser as dp
    w = wall(overrides={'width': 400})
    op = {'kind': 'circle', 'center': [500, 350], 'radius': 200,
          'width': 400, 'height': 2000, 'sill': 0}
    data = {'wall': [w], 'opening': [op]}
    dp.link_openings_to_walls(data, {})
    assert op['wall_indices'] == [0] and op['host_width'] == 400
    data['wall'] = []
    dp.link_openings_to_walls(data, {})
    assert op['wall_indices'] == [] and 'host_dir' not in op


def test_parser_validates_acknowledgement_after_final_diagnostics():
    import contextlib
    import io
    import dxf_parser as dp
    sample = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'sample_plan.dxf')
    with contextlib.redirect_stdout(io.StringIO()):
        base = dp.parse(sample, dp.DEFAULT_LAYER_RULES)
    rec = base['elements']['wall'][0]
    edit = E.capture_edit(rec, {'review_resolved': True}, 'wall', base['params'])
    with contextlib.redirect_stdout(io.StringIO()):
        fresh = dp.parse(sample, dp.DEFAULT_LAYER_RULES, edits={rec['eid']: edit})
    found = next(r for r in fresh['elements']['wall'] if r['eid'] == rec['eid'])
    assert found['review_resolved'] and not found.get('review_ack_stale')
    edit['_review_signature'] = 'obsolete-context'
    with contextlib.redirect_stdout(io.StringIO()):
        fresh = dp.parse(sample, dp.DEFAULT_LAYER_RULES, edits={rec['eid']: edit})
    found = next(r for r in fresh['elements']['wall'] if r['eid'] == rec['eid'])
    assert found['needs_review'] and not found['review_resolved']


def test_manual_gap_and_overlap_diagnostics_do_not_move_coordinates():
    import edit_review
    a = wall('wm:a', _edited=True)
    b = wall('w:b', x=1020)
    c = wall('w:c', x=500)
    elements = {'wall': [a, b, c]}
    before = copy.deepcopy(elements)
    edit_review.annotate_edit_diagnostics(elements, {})
    codes = {r['code'] for r in a['edit_diagnostics']}
    assert codes == {'endpoint_gap', 'wall_overlap'}
    assert a['needs_review']
    assert [r['points'] for r in elements['wall']] == [r['points'] for r in before['wall']]


def test_review_signature_ignores_numeric_json_representation():
    a = wall()
    b = copy.deepcopy(a)
    b['points'] = [[float(x), float(y)] for x, y in b['points']]
    b['centerline'] = [[float(x), float(y)] for x, y in b['centerline']]
    assert E.review_signature('wall', a) == E.review_signature('wall', b)


def test_level_height_is_applied_before_explicit_user_height_and_ack():
    import contextlib
    import io
    import dxf_parser as dp
    root = os.path.dirname(os.path.dirname(__file__))
    sample = os.path.join(root, 'sample_plan.dxf')
    with contextlib.redirect_stdout(io.StringIO()):
        base = dp.parse(sample, dp.DEFAULT_LAYER_RULES, level_height=4000)
    rec = base['elements']['wall'][0]
    assert rec['overrides']['height'] == 4000
    edit = E.capture_edit(rec, {'overrides': {'height': 3200}, 'review_resolved': True}, 'wall', base['params'])
    with contextlib.redirect_stdout(io.StringIO()):
        fresh = SB.build_stack({'levels': [{'id': 'L1', 'source': sample, 'z': 3000,
                                           'height': 4000, 'edits': {rec['eid']: edit}}]})
    found = next(r for r in fresh['elements']['wall'] if r['eid'] == 'L1:' + rec['eid'])
    assert found['overrides']['height'] == 3200
    assert found['review_resolved']
