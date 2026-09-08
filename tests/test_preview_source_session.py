"""Source drawing is a revision-consistent view, never a build/edit input."""
import json
import urllib.request

import ezdxf
import pytest

from project_store import ProjectStore, RevisionConflict
from project_server import ProjectSession
from test_preview_bundle import Document


def make_session(tmp_path):
    path = tmp_path / 'plan.dxf'
    doc = ezdxf.new(units=4)
    doc.layers.new('WALL')
    for y in (0, 200):
        doc.modelspace().add_line((0, y), (5000, y), dxfattribs={'layer': 'WALL'})
    doc.saveas(path)
    return ProjectSession(ProjectStore(tmp_path / 'plan.mep').create(sources=[{'id': 'main', 'path': str(path)}]))


def test_original_source_is_view_only_and_cached_across_property_edit(tmp_path, monkeypatch):
    import source_drawing
    sess = make_session(tmp_path)
    build = source_drawing.build_source_drawing
    calls = []
    def record(*args, **kwargs):
        calls.append(1)
        return build(*args, **kwargs)
    monkeypatch.setattr(source_drawing, 'build_source_drawing', record)
    first = sess.preview_state()
    source = first['geometry']['source_drawing']
    assert source['floors'][0]['primitives']
    assert first['revision'] == 0
    assert 'source_drawing' not in sess.state()['geometry']
    wall = first['geometry']['elements']['wall'][0]
    sess.save({wall['eid']: {'overrides': {'height': 3210}}}, 0, first['project_id'])
    second = sess.preview_state()
    assert second['geometry']['source_drawing'] == source
    assert len(calls) == 1
    path, _ = sess.export_geometry()
    assert 'source_drawing' not in json.loads(open(path, encoding='utf-8').read())
    assert second['revision'] == 1


def test_input_changed_while_reading_background_does_not_mix_revisions(tmp_path, monkeypatch):
    import source_drawing
    sess = make_session(tmp_path)
    build = source_drawing.build_source_drawing
    def mutate(sources, geometry):
        result = build(sources, geometry)
        path = sources[0]['path']
        doc = ezdxf.readfile(path)
        doc.modelspace().add_line((50, 50), (600, 50), dxfattribs={'layer': 'WALL'})
        doc.saveas(path)
        return result
    monkeypatch.setattr(source_drawing, 'build_source_drawing', mutate)
    with pytest.raises(RevisionConflict):
        sess.preview_state()


def test_preview_http_carries_original_with_existing_token_guard(tmp_path):
    sess = make_session(tmp_path)
    with sess.serve() as server:
        with urllib.request.urlopen(server.base_url + '/preview?token=' + server.token) as response:
            html = response.read().decode('utf-8')
        payload = json.loads(Document(html).scripts['mep-data'])
        assert payload['source_drawing']['floors'][0]['primitives']
        assert payload['project_runtime']['revision'] == 0


def test_optional_backdrop_failure_keeps_canonical_3d_view(tmp_path, monkeypatch):
    import source_drawing
    sess = make_session(tmp_path)
    def broken(*args):
        raise ValueError('decoder problem containing an arbitrary private path')
    monkeypatch.setattr(source_drawing, 'build_source_drawing', broken)
    state = sess.preview_state()
    assert state['geometry']['elements']['wall']
    assert state['geometry']['source_drawing']['status'] == 'unavailable'
    assert 'private path' not in json.dumps(state['geometry']['source_drawing'])
