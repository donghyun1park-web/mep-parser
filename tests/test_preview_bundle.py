"""Production HTML must carry data safely and run without a development server."""
import hashlib
import json
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
import preview


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.scripts, self.external, self.current = {}, [], None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'link') and (attrs.get('src') or attrs.get('href')):
            self.external.append(attrs.get('src') or attrs.get('href'))
        if tag == 'script':
            self.current = attrs.get('id', '')
            self.scripts[self.current] = ''

    def handle_data(self, data):
        if self.current is not None:
            self.scripts[self.current] += data

    def handle_endtag(self, tag):
        if tag == 'script':
            self.current = None


def test_offline_bundle_and_untrusted_data_are_separate():
    marker = '</script><script id="injected">throw 42</script>'
    data = {'source': marker, 'elements': {}, 'project_runtime': {'revision': 7},
            'source_drawing': {'status': 'unavailable', 'floors': [], 'warnings': [marker]}}
    document = Document(preview.build_html(data))
    assert not document.external
    assert 'injected' not in document.scripts
    payload = json.loads(document.scripts['mep-data'])
    assert payload['source'] == marker
    assert payload['source_drawing'] == data['source_drawing']
    assert payload['project_runtime']['revision'] == 7
    assert len(document.scripts['mep-app']) > 100_000
    assert 'gcZRange' in document.scripts['mep-contract']


def test_missing_production_asset_fails_with_build_instruction(tmp_path, monkeypatch):
    monkeypatch.setattr(preview, '_asset_dir', lambda: tmp_path)
    with pytest.raises(RuntimeError, match='npm.*build'):
        preview.build_html({'elements': {}})


def test_committed_bundle_matches_manifest():
    built = Path(preview._asset_dir())
    manifest = json.loads((built / 'manifest.json').read_text(encoding='utf-8'))
    assert manifest['tool'] == 'vite'
    for name, digest in manifest['assets'].items():
        assert hashlib.sha256((built / name).read_bytes()).hexdigest() == digest, name
    root = Path(preview.__file__).parent
    assert manifest['source_hash'] == 'sha256-lf'
    for name, digest in manifest['sources'].items():
        content = (root / name).read_bytes().replace(b'\r\n', b'\n')
        assert hashlib.sha256(content).hexdigest() == digest, f'{name}: run npm run build'


def test_review_view_javascript_behaviors():
    node = shutil.which('node')
    assert node, 'Node.js is required for frontend behavior tests'
    root = Path(preview.__file__).parent
    result = subprocess.run([node, '--test', str(root / 'tests' / 'preview_review.test.mjs'),
                             str(root / 'tests' / 'preview_coordinates.test.mjs')],
                            cwd=root, capture_output=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
