"""Whole-model bounds must include MEP when testing for runaway native geometry."""
import pytest

from verify import verify_build


@pytest.mark.parametrize('native_max_x, expected_error', [(1108, False), (10000, True)])
def test_native_bounds_compare_against_architecture_and_current_3d_mep(native_max_x, expected_error):
    data = {'elements': {'column': [{'kind': 'polyline', 'closed': True,
                'points': [[0, 0], [600, 0], [600, 400], [0, 400]]}],
            'pipe': [{'kind': 'polyline', 'diameter': 15.9, 'elevation': 70,
                'points': [[0, 0], [1, 0]],  # Old display cache must not define the envelope.
                'path3d': {'segments': [{'type': 'line', 'start': [0, -1000, 0],
                                       'end': [1100, -1000, 0]}]}}]}}
    stats = {'intent': {}, 'built': {}, 'floor_orphans': 0, 'floor_dups': 0,
             'invalid_shapes': 0, 'bbox': [-8, -1008, 0, native_max_x, 400, 2800]}
    report = verify_build(data, stats, stage='catalog')
    assert any(f.id == 'V104' for f in report.findings) is expected_error


def test_circular_column_is_part_of_mixed_input_envelope():
    data = {'elements': {'column': [{'kind': 'circle', 'center': [0, 0], 'radius': 1000}],
                        'pipe': [{'points': [[0, 0], [10, 0]], 'diameter': 10}]}}
    stats = {'intent': {}, 'built': {}, 'floor_orphans': 0, 'floor_dups': 0,
             'invalid_shapes': 0, 'bbox': [-1000, -1000, 0, 1000, 1000, 3000]}
    assert not any(f.id == 'V104' for f in verify_build(data, stats, stage='catalog').findings)


def test_outline_uses_authoritative_footprint_not_stale_centerline():
    data = {'elements': {'column': [{'kind': 'polyline', 'closed': True,
                'points': [[0, 0], [100, 0], [100, 100], [0, 100]]}],
        'duct': [{'geometry_mode': 'footprint', 'height_mm': 100,
        'points': [[0, 0], [100, 0], [100, 100], [0, 100]],
        'centerline': [[0, 0], [10000, 0]],
        'path3d': {'segments': [{'type': 'line', 'start': [0, 0, 0], 'end': [10000, 0, 0]}]}}]}}
    stats = {'intent': {}, 'built': {}, 'floor_orphans': 0, 'floor_dups': 0,
             'invalid_shapes': 0, 'bbox': [0, 0, 0, 5000, 100, 100]}
    assert any(f.id == 'V104' for f in verify_build(data, stats, stage='catalog').findings)
