"""MEP curve fidelity and topology regressions, using real ezdxf entities."""
import math
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ezdxf
import pytest
import dxf_parser as dp


def test_legacy_join_keeps_different_elevations_and_sections_separate():
    rows = [dict(layer='P', points=[[0, 0], [100, 0]], elevation=70, diameter=15.9),
            dict(layer='P', points=[[100, 20], [200, 20]], elevation=2700, diameter=100)]
    joined, _ = dp.join_mep_runs(rows, 25)
    assert len(joined) == 2


def test_legacy_join_keeps_both_sides_of_explicit_gap():
    rows = [dict(layer='P', points=[[0, 0], [100, 0]]),
            dict(layer='P', points=[[100, 20], [200, 20]])]
    joined, gap = dp.join_mep_runs(rows, 25)
    assert joined[0]['points'] == [[0, 0], [100, 0], [100, 20], [200, 20]]
    assert gap == 20


def test_parser_spline_follows_curve_instead_of_control_polygon():
    d = ezdxf.new()
    e = d.modelspace().add_open_spline([(0, 0), (0, 100), (100, 100), (100, 0)], degree=3)
    r = dp.entity_to_record(e, 1)
    assert len(r['points']) > 4
    assert max(p[1] for p in r['points']) == pytest.approx(75, abs=.5)


def test_arc_exact_length_and_signature_do_not_depend_on_tessellation():
    from mep_paths import extract_curve
    e = ezdxf.new().modelspace().add_arc((10, 20), 100, 0, 180)
    a = extract_curve(e, 1, .25)
    b = extract_curve(e, 1, 2)
    assert a['source_length_mm'] == pytest.approx(100 * math.pi)
    assert a['_sigs'] == b['_sigs']
    assert len(a['points']) > len(b['points'])
    assert a['sampled_length_mm'] < a['source_length_mm']
    assert a['points'][0] == pytest.approx([110, 20])
    assert a['points'][-1] == pytest.approx([-90, 20])


@pytest.mark.parametrize('bulge', [1, -1, 2, -2])
def test_bulge_direction_and_major_arc_preserve_vertices(bulge):
    from mep_paths import extract_curve
    e = ezdxf.new().modelspace().add_lwpolyline([(0, 0, bulge), (100, 0, 0)], format='xyb')
    r = extract_curve(e, 1, .1)
    assert r['points'][0] == pytest.approx([0, 0], abs=1e-8)
    assert r['points'][-1] == pytest.approx([100, 0], abs=1e-8)
    assert (min(p[1] for p in r['points']) < -49) if bulge > 0 else (max(p[1] for p in r['points']) > 49)
    radius = 50 if abs(bulge) == 1 else 62.5
    assert r['source_length_mm'] == pytest.approx(radius * 4 * math.atan(abs(bulge)))


def test_curve_tolerance_is_in_mm_for_metre_input():
    from mep_paths import extract_curve
    e = ezdxf.new().modelspace().add_arc((0, 0), 1, 0, 180)
    r = extract_curve(e, 1000, .25)
    worst = max(1000 - math.hypot((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                for a, b in zip(r['points'], r['points'][1:]))
    assert worst <= .25 + 1e-8


def test_mixed_curve_chain_joins_endpoints_only_and_keeps_gap():
    from mep_paths import extract_curve, join_paths
    m = ezdxf.new().modelspace()
    entities = [m.add_line((0, 0), (100, 0)), m.add_arc((100, 50), 50, 270, 90),
                m.add_line((100, 100), (0, 100)), m.add_line((-4.2, 100), (-50, 100))]
    rows = [dict(extract_curve(e, 1, .25), layer='P', elevation=70, diameter=15.9) for e in entities]
    paths, report = join_paths(rows, .001, 10)
    assert len(paths) == 2
    assert sum(len(p['source_refs']) for p in paths) == 4
    assert sum(p['source_length_mm'] for p in paths) == pytest.approx(245.8 + 50 * math.pi)
    assert any(g['distance_mm'] == pytest.approx(4.2) for g in report['gaps'])
    assert not any(g['repair_applied'] for g in report['gaps'])


def test_t_branch_is_not_arbitrarily_chained_and_crossing_is_not_a_joint():
    from mep_paths import join_paths
    rows = [dict(points=p, layer='P', source_length_mm=100, sampled_length_mm=100)
            for p in [[[0, 0], [100, 0]], [[100, 0], [200, 0]], [[100, 0], [100, 100]]]]
    paths, report = join_paths(rows, .001, 10)
    assert len(paths) == 3
    assert len(report['branchpoints']) == 1
    paths, report = join_paths([dict(points=[[0, 50], [100, 50]], layer='P'),
                                dict(points=[[50, 0], [50, 100]], layer='P')], .001, 10)
    assert len(paths) == 2
    assert not report['branchpoints']


def test_point_budget_failure_is_explicit_not_a_false_tolerance_claim():
    from mep_paths import extract_curve
    e = ezdxf.new().modelspace().add_arc((0, 0), 100000, 0, 180)
    with pytest.raises(ValueError, match='budget'):
        extract_curve(e, 1, .001, max_points=10)
