import copy
import math
import pytest
from verify import verify_geometry
from boq_export import aggregate


def model(record):
    return {'units':'mm','elements':{'pipe':[record]}}


@pytest.mark.parametrize('rec',[
    {'points':[[0,0],[float('nan'),10]],'diameter':16},
    {'points':[[0,0],[100,0]],'diameter':-16},
    {'points':[[0,0],[100,0]],'diameter':16,'elevation':float('inf')},
    {'points':[[0,0],[0,0]],'diameter':16},
    {'points':[[0,0],[100,0]],'dimension_status':'unknown'},
])
def test_invalid_mep_cannot_pass_structural_only_gate(rec):
    report=verify_geometry(model(rec))
    assert report.failed
    assert any(f.id=='V010' for f in report.findings)


def test_mep_gap_and_assumptions_are_reviewable_not_implicitly_approved():
    data=model({'points':[[0,0],[100,0]],'diameter':15.9,'needs_review':True,'review_reason':'source_gap'})
    data['mep_diagnostics']={'issues':[{'code':'source_gap','severity':'warning','reason':'4mm gap retained'}]}
    report=verify_geometry(data)
    assert not report.failed
    assert any(f.id=='V012' for f in report.warns)


def test_profile_scoped_export_rejects_outside_geometry_and_no_source_refs():
    data=model({'points':[[0,0],[1200,0]],'diameter':16,'eid':'p:example'})
    data['mep_profile']={'version':1,'region':{'id':'A','bounds_mm':[0,0,1000,1000]},'layers':[{'category':'pipe'}]}
    report=verify_geometry(data)
    assert report.failed
    assert any(f.id=='V011' for f in report.errors)


def test_boq_uses_exact_curve_length_and_keeps_actual_od():
    data=model({'points':[[0,0],[1000,0]],'diameter':15.9,'nominal_size':'15A',
                'source_length_mm':1500,'sampled_length_mm':1000,'source_refs':[{'handle':'1'}]})
    rows=aggregate(data)['MEP'][1]
    assert rows[0][1]=='15.9'
    assert rows[0][3]==1.5


def test_coverage_omissions_are_visible_even_without_issue_rows():
    data=model({'points':[[0,0],[100,0]],'diameter':16})
    data['mep_diagnostics']={'source_coverage':{'selected':3,'represented':1,'omitted':2,'complete':False}}
    report=verify_geometry(data)
    assert any(f.id=='V012' for f in report.warns)


def test_inconsistent_coverage_cannot_pass_export():
    data=model({'points':[[0,0],[100,0]],'diameter':16})
    data['mep_diagnostics']={'source_coverage':{'selected':3,'represented':3,'omitted':1,'complete':True}}
    assert any(f.id=='V011' for f in verify_geometry(data).errors)


def test_outline_duct_perimeter_is_not_reported_as_centerline_length():
    data={'elements':{'duct':[{'kind':'polyline','geometry_mode':'footprint','closed':True,
      'points':[[0,0],[1000,0],[1000,200],[0,200]],'width_mm':200,'height_mm':60}]}}
    assert aggregate(data)['MEP'][1]==[]
    assert aggregate(data)['MEP 외곽'][1][0][-1]==pytest.approx(.2)
