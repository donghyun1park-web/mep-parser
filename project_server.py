"""Single canonical edit flow shared by the desktop GUI, MCP and loopback preview."""
import contextlib
import copy
import io
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from project_store import ProjectStore, RevisionConflict, atomic_bytes, atomic_json, validate_edits


class ProjectSession:
    def __init__(self, store):
        self.store = store
        self._mutex = threading.RLock()
        self._cached_state = None
        self._cached_drawing = None

    def _parse_source(self, source, edits):
        import dxf_parser as parser
        rules = parser.load_layer_map(source['layer_map']) if source.get('layer_map') else parser.DEFAULT_LAYER_RULES
        block = parser.load_layer_map(source['block_map']) if source.get('block_map') else parser.DEFAULT_BLOCK_RULES
        options = dict(source.get('options') or {})
        if source.get('height') is not None:
            options['level_height'] = source['height']
        if source.get('schedule'):
            from schedule_io import load_schedule_xlsx
            options['ext_schedule'] = load_schedule_xlsx(source['schedule'])
        with contextlib.redirect_stdout(io.StringIO()):
            return parser.parse(source['path'], rules, block, edits=edits, **options)

    def _parse(self, manifest):
        sources = manifest['sources']
        if self.store.is_plain_source(manifest):
            data = self._parse_source(sources[0], manifest['edits_by_floor']['main'])
        else:
            from stack_build import build_stack
            levels = []
            for source in sources:
                level = dict(source, source=source['path'], edits=manifest['edits_by_floor'].get(source['id'], {}))
                level['options'] = dict(source.get('options') or {})
                if source.get('schedule'):
                    from schedule_io import load_schedule_xlsx
                    level['options']['ext_schedule'] = load_schedule_xlsx(source['schedule'])
                levels.append(level)
            with contextlib.redirect_stdout(io.StringIO()):
                data = build_stack({'project':manifest['project_id'], 'levels':levels})
        edits = self.store.flat_edits(manifest)
        from element_id import suggest_relink
        report = data.setdefault('edits_report', {})
        report['relink_suggestions'] = suggest_relink(report.get('orphaned', []), edits, data.get('elements', {}))
        data['project'] = {'project_id':manifest['project_id'], 'revision':manifest['revision'],
                           'folder':str(self.store.folder), 'changed_inputs':self.store.changed_inputs(manifest)}
        data['project_edits'] = edits
        return data

    def state(self):
        with self._mutex:
            manifest = self.store.refresh_inputs()
            if self._cached_state and self._cached_state['revision'] == manifest['revision']:
                return copy.deepcopy(self._cached_state)
            geometry = self._parse(manifest)
            current = self.store.refresh_inputs()
            if current['revision'] != manifest['revision']:
                raise RevisionConflict(current['revision'])
            self._cached_state = {'project_id':manifest['project_id'], 'revision':manifest['revision'],
                    'edits':self.store.flat_edits(manifest), 'geometry':geometry}
            return copy.deepcopy(self._cached_state)

    def preview_state(self):
        """Attach original linework to a consistent view without polluting build input."""
        from source_drawing import build_source_drawing
        with self._mutex:
            state = self.state()
            manifest = self.store.read()
            self.store.check_revision(manifest, state['revision'], state['project_id'])
            geometry = state['geometry']
            # Counts and edited heights do not affect source linework. The cache
            # key keeps only the actual source file state and resolved placement.
            key = json.dumps({'sources': manifest['sources'], 'placements': [
                {k: level.get(k) for k in ('id', 'z', 'offset', 'label')}
                for level in geometry.get('stack', {}).get('levels', [])]}, sort_keys=True)
            if self._cached_drawing and self._cached_drawing[0] == key:
                drawing = self._cached_drawing[1]
            else:
                try:
                    drawing = build_source_drawing(manifest['sources'], geometry)
                except Exception as exc:
                    # Source linework is an optional view. A display decoder
                    # error must not suppress the canonical review/edit screen.
                    drawing = {'schema_version': 1, 'units': 'mm', 'status': 'unavailable',
                               'floors': [], 'warnings': ['원본 도면 표시 실패: ' + type(exc).__name__]}
            current = self.store.refresh_inputs()
            self.store.check_revision(current, state['revision'], state['project_id'])
            self._cached_drawing = (key, drawing)
            geometry['source_drawing'] = copy.deepcopy(drawing)
            return state

    def export_geometry(self, path=None):
        # Each build gets an immutable revision snapshot; a later browser save cannot alter it.
        state = self.state()
        if path is None:
            path = self.store.folder / 'builds' / f"geometry-r{state['revision']}-{secrets.token_hex(4)}.json"
        atomic_json(path, state['geometry'])
        return str(path), state

    def _capture(self, flat, manifest):
        from element_id import capture_edit, review_signature
        local = self.store.local_edits(flat, manifest)
        for source in manifest['sources']:
            sid = source['id']
            existing = manifest['edits_by_floor'].get(sid, {})
            baseline = self._parse_source(source, existing)
            def unique_records(data):
                records, duplicate = {}, set()
                for category, recs in data['elements'].items():
                    for rec in recs:
                        eid = rec['eid']
                        if eid in records:
                            duplicate.add(eid)
                        records[eid] = (category, rec)
                return {eid:value for eid,value in records.items() if eid not in duplicate}, duplicate
            records, ambiguous = unique_records(baseline)
            acknowledgements = set()
            for eid, edit in local.get(sid, {}).items():
                previous = existing.get(eid, {})
                explicit = edit.pop('acknowledge', False)
                if eid in ambiguous and (edit != previous or explicit):
                    raise ValueError('Ambiguous EID cannot be edited or acknowledged: ' + eid)
                if previous.get('_source'):
                    edit['_source'] = copy.deepcopy(previous['_source'])
                else:
                    edit.pop('_source', None)
                if explicit:
                    acknowledgements.add(eid)
                    edit.pop('review_resolved', None)
                    edit.pop('_review_signature', None)
                elif previous.get('_review_signature'):
                    edit['_review_signature'] = previous['_review_signature']
                else:
                    edit.pop('_review_signature', None)
                    edit.pop('review_resolved', None)
                if eid in records:
                    category, rec = records[eid]
                    local[sid][eid] = capture_edit(rec, edit, category, baseline.get('params'))
            if acknowledgements:
                proposed = self._parse_source(source, local.get(sid, {}))
                current, duplicates = unique_records(proposed)
                for eid in acknowledgements:
                    if eid in duplicates or eid not in current:
                        raise ValueError('Acknowledgement target is missing or ambiguous: ' + eid)
                    category, rec = current[eid]
                    edit = local[sid][eid]
                    edit['review_resolved'] = True
                    edit['_review_signature'] = review_signature(category, rec, proposed.get('params'))
                    local[sid][eid] = capture_edit(rec, edit, category, proposed.get('params'))
        enriched = copy.deepcopy(manifest)
        enriched['edits_by_floor'] = local
        return self.store.flat_edits(enriched)

    def save(self, edits, expected_revision, project_id, decisions=None, dry_run=False):
        validate_edits(edits)
        with self._mutex:
            manifest = self.store.refresh_inputs()
            self.store.check_revision(manifest, expected_revision, project_id)
            captured = self._capture(copy.deepcopy(edits), manifest)
            proposed = copy.deepcopy(manifest)
            proposed['edits_by_floor'] = self.store.local_edits(captured, manifest)
            try:
                self._parse(proposed)
            except Exception as exc:
                raise ValueError('Proposed edits cannot be parsed; project was not changed: ' + str(exc)) from exc
            if dry_run:
                return {'dry_run': True, 'project_id': project_id, 'revision': expected_revision}
            self.store.check_revision(self.store.refresh_inputs(), expected_revision, project_id)
            self.store.replace_edits(captured, expected_revision, project_id, decisions=decisions)
            return self.state()

    def import_legacy(self, path, expected_revision, project_id):
        raw = Path(path).read_bytes()
        payload = json.loads(raw)
        if isinstance(payload, dict) and 'project_id' in payload:
            if payload['project_id'] != project_id:
                raise ValueError('Import belongs to a different project')
            payload = payload.get('edits')
        validate_edits(payload)
        with self._mutex:
            state = self.state()
            self.store.check_revision(state, expected_revision, project_id)
            edits = state['edits']
            edits.update(payload)
            # Keep the exact import before committing, including on a later CAS conflict.
            atomic_bytes(self.store.folder / 'imports' / (secrets.token_hex(16) + '.json'), raw)
            return self.save(edits, expected_revision, project_id,
                             [{'action': 'import', 'revision': expected_revision}])

    def edit(self, eid, patch, expected_revision, project_id):
        state = self.state()
        self.store.check_revision(state, expected_revision, project_id)
        present = {r['eid'] for rs in state['geometry']['elements'].values() for r in rs}
        if eid not in present and eid not in state['edits']:
            raise ValueError('EID does not exist in this project')
        edit = state['edits'].setdefault(eid, {})
        for key, value in patch.items():
            if key == 'overrides':
                edit.setdefault(key, {}).update(value)
            else:
                edit[key] = value
        return self.save(state['edits'], expected_revision, project_id)

    def discard(self, eid, expected_revision, project_id):
        state = self.state()
        self.store.check_revision(state, expected_revision, project_id)
        if eid not in state['edits']:
            raise ValueError('Edit does not exist')
        del state['edits'][eid]
        return self.save(state['edits'], expected_revision, project_id,
                         [{'action':'discard','eid':eid,'revision':expected_revision}])

    def relink(self, orphan, target, expected_revision, project_id):
        state = self.state()
        self.store.check_revision(state, expected_revision, project_id)
        if orphan not in state['geometry']['edits_report'].get('orphaned', []):
            raise ValueError('Source edit is not an orphan')
        eligible = next((row['candidates'] for row in state['geometry']['edits_report'].get('relink_suggestions', [])
                         if row['orphan'] == orphan), [])
        if target not in eligible:
            raise ValueError('Target is not an eligible same-floor/category candidate')
        current = {r['eid'] for rs in state['geometry']['elements'].values() for r in rs}
        if target not in current or target in state['edits']:
            raise ValueError('Target does not exist or already has an edit')
        edit = state['edits'].pop(orphan)
        for key in ('_source', '_at', '_review_signature', 'review_resolved'):
            edit.pop(key, None)
        state['edits'][target] = edit
        return self.save(state['edits'], expected_revision, project_id,
                         [{'action':'relink','orphan':orphan,'target':target,'revision':expected_revision}])

    def defer(self, eid, expected_revision, project_id):
        state = self.state()
        self.store.check_revision(state, expected_revision, project_id)
        if eid not in state['geometry']['edits_report'].get('orphaned', []):
            raise ValueError('Edit is not an orphan')
        return self.save(state['edits'], expected_revision, project_id,
                         [{'action': 'defer', 'eid': eid, 'revision': expected_revision}])

    def serve(self):
        return ProjectServer(self)

    # ── Pascal 편집 화면 ──────────────────────────────────────────────────
    # 스냅샷 조회와 변경 적용이 **여기 한 곳**이다 — HTTP(`/pascal/*`)와 MCP 가
    # 같은 함수를 부른다. 저장은 전체 JSON 되돌리기가 아니라 원본과 대조한 변경
    # 명령이고(`pascal_bridge.scene_to_edits`), 기존 save 의 revision·검증·잠금을 탄다.
    def pascal_snapshot(self):
        from pascal_bridge import scene_sha256, to_pascal_scene
        state = self.state()
        scene, report = to_pascal_scene(state['geometry'])
        return {'project_id': state['project_id'], 'revision': state['revision'],
                'snapshot_sha256': scene_sha256(scene), 'scene': scene, 'report': report}

    def pascal_apply(self, scene, expected_revision, project_id, snapshot_sha256, op_id,
                     dry_run=False):
        from pascal_bridge import merge_edits, scene_sha256, scene_to_edits, to_pascal_scene
        if not isinstance(op_id, str) or not 0 < len(op_id) <= 128:
            raise ValueError('op_id must be a nonempty string of at most 128 characters')
        if not isinstance(scene, dict) or not isinstance(scene.get('nodes'), dict):
            raise ValueError('scene must be a Pascal scene graph with nodes')
        with self._mutex:
            manifest = self.store.refresh_inputs()
            if manifest['project_id'] != project_id:
                raise ValueError('Wrong project_id')
            # 같은 작업 ID 는 두 번 적용하지 않는다. 재시도·이중 클릭이 수정을 두 번
            # 쌓거나, 둘째 요청이 낡은 revision 으로 409 를 내며 첫 요청의 성공을
            # 가리지 않게 — 중복은 revision 검사보다 **먼저** 본다.
            if any(d.get('action') == 'pascal_apply' and d.get('op_id') == op_id
                   for d in manifest.get('decisions') or []):
                return {'duplicate': True, 'op_id': op_id, 'state': self.state()}
            self.store.check_revision(manifest, expected_revision, project_id)
            state = self.state()
            current, _ = to_pascal_scene(state['geometry'])
            if scene_sha256(current) != snapshot_sha256:
                raise SnapshotConflict(manifest['revision'])
            commands, report = scene_to_edits(state['geometry'], scene)
            summary = {k: report[k] for k in ('deleted', 'moved', 'overrides', 'added')}
            if not commands:
                return {'applied': False, 'reason': 'no_changes', 'summary': summary,
                        'op_id': op_id, 'state': state}
            decision = {'action': 'pascal_apply', 'op_id': op_id, 'revision': expected_revision,
                        'snapshot_sha256': snapshot_sha256, 'summary': summary}
            result = self.save(merge_edits(state['edits'], commands), expected_revision,
                               project_id, [decision], dry_run=dry_run)
            return {'applied': not dry_run, 'dry_run': bool(dry_run), 'op_id': op_id,
                    'commands': commands, 'summary': summary, 'state': result}


class SnapshotConflict(RevisionConflict):
    """편집 화면이 받은 씬과 지금 저장소가 만드는 씬이 다르다. revision 은 같아도
    파서·다리·설정이 바뀌면 생긴다 — 409 로 돌려보내 다시 불러오게 한다."""

    def __str__(self):
        return 'Pascal scene snapshot is stale; reload it before applying'


class ProjectServer:
    def __init__(self, session):
        self.session = session
        self.token = secrets.token_urlsafe(32)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, code, data, content_type='application/json'):
                payload = (json.dumps(data, ensure_ascii=False, allow_nan=False) if content_type == 'application/json' else data).encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', content_type+'; charset=utf-8')
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('Content-Security-Policy', "frame-ancestors 'none'")
                self.end_headers()
                self.wfile.write(payload)

            def authorized(self, preview=False):
                if self.headers.get('Host') != urlsplit(owner.base_url).netloc:
                    self.reply(403, {'error':'Untrusted Host'})
                    return False
                origin = self.headers.get('Origin')
                if origin and origin != owner.base_url:
                    self.reply(403, {'error':'Origin not allowed'})
                    return False
                token = self.headers.get('Authorization', '').removeprefix('Bearer ')
                if preview:
                    token = parse_qs(urlsplit(self.path).query).get('token', [token])[0]
                if not secrets.compare_digest(token, owner.token):
                    self.reply(401, {'error':'Authentication required'})
                    return False
                return True

            def do_GET(self):
                path = urlsplit(self.path).path
                if path not in ('/state','/preview','/pascal/snapshot'):
                    self.reply(404, {'error':'Unknown endpoint'})
                    return
                if not self.authorized(preview=path == '/preview'):
                    return
                try:
                    if path == '/pascal/snapshot':
                        self.reply(200, session.pascal_snapshot())
                        return
                    state = session.state() if path == '/state' else session.preview_state()
                    if path == '/state':
                        self.reply(200, state)
                    else:
                        import preview
                        geometry = state['geometry']
                        geometry['project_runtime'] = dict(base_url=owner.base_url, token=owner.token,
                                                           project_id=state['project_id'], revision=state['revision'])
                        self.reply(200, preview.build_html(geometry), 'text/html')
                except RevisionConflict as exc:
                    self.reply(409, {'error':str(exc),'current_revision':exc.current_revision})
                except Exception as exc:
                    self.reply(500, {'error':str(exc)})

            def do_POST(self):
                path = urlsplit(self.path).path
                if path not in ('/edits', '/discard', '/relink', '/defer', '/pascal/apply'):
                    self.reply(404, {'error':'Unknown endpoint'})
                    return
                if not self.authorized():
                    return
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if size <= 0 or size > 8*1024*1024:
                        raise ValueError('Request body size is invalid')
                    if self.headers.get_content_type() != 'application/json':
                        raise ValueError('Content-Type must be application/json')
                    body = json.loads(self.rfile.read(size))
                    common = (body['expected_revision'],body['project_id'])
                    if path == '/edits':
                        result = session.save(body['edits'], *common)
                    elif path == '/discard':
                        result = session.discard(body['eid'], *common)
                    elif path == '/defer':
                        result = session.defer(body['eid'], *common)
                    elif path == '/pascal/apply':
                        result = session.pascal_apply(body['scene'], *common, body['snapshot_sha256'],
                                                      body['op_id'], bool(body.get('dry_run', False)))
                    else:
                        result = session.relink(body['orphan'],body['target'], *common)
                    self.reply(200, result)
                except RevisionConflict as exc:
                    self.reply(409, {'error':str(exc),'current_revision':exc.current_revision})
                except (ValueError, KeyError, TypeError) as exc:
                    self.reply(400, {'error':str(exc)})
                except Exception as exc:
                    self.reply(500, {'error':str(exc)})

        self.httpd = ThreadingHTTPServer(('127.0.0.1',0), Handler)
        self.httpd.daemon_threads = True
        self.base_url = f'http://127.0.0.1:{self.httpd.server_port}'
        self.url = self.base_url+'/preview?token='+self.token
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def open_source_project(dxf_path, folder=None, layer_map=None, block_map=None, options=None, schedule=None):
    dxf_path = str(Path(dxf_path).resolve())
    store = ProjectStore(folder or Path(dxf_path).with_suffix('.mep'))
    if not store.path.exists():
        source = dict(id='main', path=dxf_path, layer_map=layer_map, block_map=block_map, options=options or {}, schedule=schedule)
        store.create([source])
    elif store.read()['sources'][0]['path'] != dxf_path:
        raise ValueError('Project belongs to a different source drawing')
    return ProjectSession(store)


def session_from_geometry(json_path):
    data = json.loads(Path(json_path).read_text(encoding='utf-8'))
    meta = data.get('project') or {}
    if not meta.get('folder'):
        raise ValueError('Geometry has no canonical project; run parse_dxf first')
    session = ProjectSession(ProjectStore(meta['folder']))
    if session.store.read()['project_id'] != meta.get('project_id'):
        raise ValueError('Geometry belongs to a different project')
    return session

def verified_artifacts(report_path, geometry, expected_run_id):
    """Validate current-run receipts and actual bytes before clients claim artifact success."""
    import artifact_validation as av
    from project_store import fingerprint
    expected = dict(run_id=expected_run_id, input_sha256=av.input_hash(geometry),
                    project_id=geometry.get('project', {}).get('project_id'),
                    revision=geometry.get('project', {}).get('revision'))
    try:
        report = json.loads(Path(report_path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        return {kind:{'status':'failed','error':f'Build receipt unavailable: {exc}'} for kind in ('fcstd','ifc')}
    result = {}
    for kind in ('fcstd','ifc'):
        receipt = copy.deepcopy(report.get('artifacts', {}).get(kind) or {'status':'failed'})
        proof = receipt.get('provenance') or {}
        try:
            if not expected_run_id or any(proof.get(k) != v for k,v in expected.items()):
                raise ValueError('Artifact receipt does not match this build run and project revision')
            if receipt.get('status') == 'verified':
                if not receipt.get('path') or not receipt.get('sha256') or fingerprint(receipt['path']) != receipt['sha256']:
                    raise ValueError('Artifact is missing or bytes differ from the receipt')
            elif receipt.get('status') not in ('failed','diagnostic_nonverified'):
                raise ValueError('Artifact status is not verified')
        except (OSError, ValueError) as exc:
            receipt['status']='failed'
            receipt['error']=str(exc)
        result[kind] = receipt
    return result
