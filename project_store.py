"""Durable, revisioned project manifest. Geometry files are disposable derived outputs."""
import copy
import hashlib
import json
import os
from pathlib import Path
import time
import uuid
from contextlib import contextmanager


class RevisionConflict(ValueError):
    def __init__(self, current_revision):
        self.current_revision = current_revision
        super().__init__(f'Revision conflict: current revision is {current_revision}; reload before saving')


class ProjectCorrupt(ValueError):
    pass


def fingerprint(path):
    if not path:
        return None
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def atomic_bytes(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with open(temporary, 'xb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name != 'nt':
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_json(path, data):
    atomic_bytes(path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8'))


def validate_edits(edits):
    if not isinstance(edits, dict):
        raise ValueError('edits must be an EID-keyed object')
    json.dumps(edits, allow_nan=False)
    for eid, edit in edits.items():
        if not isinstance(eid, str) or not eid or not isinstance(edit, dict):
            raise ValueError('Each edit needs a nonempty EID and object value')
        if edit.get('category') and edit['category'] not in ('wall','column','slab','beam','zone','opening','pipe','duct','tray','equipment','ignore'):
            raise ValueError('Unknown edit category')
        if 'overrides' in edit and not isinstance(edit['overrides'], dict):
            raise ValueError('overrides must be an object')
        for key, value in (edit.get('overrides') or {}).items():
            if key in ('width','height','thickness','diameter','width_mm','height_mm'):
                if type(value) not in (int,float) or value <= 0:
                    raise ValueError(f'{key} must be a positive number')
        for field in ('added', 'deleted', 'review_resolved', 'acknowledge'):
            if field in edit and not isinstance(edit[field], bool):
                raise ValueError(f'{field} must be boolean')
        if edit.get('added') and not isinstance(edit.get('record'), dict):
            raise ValueError('added edits require a record')


class ProjectStore:
    def __init__(self, folder):
        self.folder = Path(folder).resolve()
        self.path = self.folder / 'project.json'
        self.backup = self.folder / 'project.previous.json'

    @contextmanager
    def locked(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        with open(self.folder / '.project.lock', 'a+b') as stream:
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b'0')
                stream.flush()
            deadline = time.monotonic() + 15
            while True:
                try:
                    stream.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Project is busy in another window or process')
                    time.sleep(0.02)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == 'nt':
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def create(self, sources, options=None):
        with self.locked():
            if self.path.exists():
                raise FileExistsError(self.path)
            normalized = copy.deepcopy(sources)
            if not normalized:
                raise ValueError('At least one source is required')
            ids = set()
            for source in normalized:
                if source.get('align'):
                    raise ValueError('Unresolved align is not allowed in a durable project; resolve alignment and save explicit offsets first')
                sid = source.setdefault('id', 'main')
                if not isinstance(sid, str) or not sid or ':' in sid or sid in ids:
                    raise ValueError('Source IDs must be unique nonempty strings')
                ids.add(sid)
                source.setdefault('options', {})
                source.setdefault('z', 0)
                source.setdefault('offset', [0, 0])
                source['fingerprints'] = {}
                for key in ('path', 'layer_map', 'block_map', 'schedule'):
                    if source.get(key):
                        source[key] = str(Path(source[key]).resolve())
                        source['fingerprints'][key] = fingerprint(source[key])
            state = dict(schema_version=1, project_id=str(uuid.uuid4()), revision=0,
                         sources=normalized, options=options or {}, edits_by_floor={sid:{} for sid in ids}, decisions=[])
            atomic_json(self.path, state)
        return self

    @staticmethod
    def _validate_manifest(data):
        if data['schema_version'] != 1 or type(data['revision']) is not int or data['revision'] < 0:
            raise ValueError('unsupported schema or revision')
        uuid.UUID(data['project_id'])
        if not isinstance(data['sources'], list) or not data['sources'] or not isinstance(data['edits_by_floor'], dict):
            raise ValueError('invalid sources or edits')
        source_ids = set()
        for source in data['sources']:
            if source.get('align'):
                raise ValueError('Unresolved align: resolve alignment and save explicit offsets first')
            sid=source['id']
            if not isinstance(sid,str) or not sid or ':' in sid or sid in source_ids or not isinstance(source['path'],str):
                raise ValueError('invalid source identity')
            source_ids.add(sid)
            if not isinstance(source.get('options',{}),dict) or not isinstance(source.get('fingerprints',{}),dict):
                raise ValueError('invalid source options or fingerprints')
        if not set(data['edits_by_floor']).issubset(source_ids):
            raise ValueError('edits refer to an unknown floor')
        for edits in data['edits_by_floor'].values():
            validate_edits(edits)
        json.dumps(data, allow_nan=False)
        return data

    def read(self):
        try:
            return self._validate_manifest(json.loads(self.path.read_text(encoding='utf-8')))
        except FileNotFoundError:
            raise
        except (ValueError, KeyError, TypeError) as exc:
            raise ProjectCorrupt(f'Project manifest is corrupt; explicitly recover project.previous.json: {exc}') from exc

    @staticmethod
    def is_plain_source(state):
        sources=state['sources']
        return (len(sources)==1 and sources[0]['id']=='main' and not sources[0].get('z')
                and not any(sources[0].get('offset') or [0,0]))

    @staticmethod
    def flat_edits(state):
        if ProjectStore.is_plain_source(state):
            return copy.deepcopy(state['edits_by_floor']['main'])
        from stack_build import edits_to_world
        return edits_to_world(state['edits_by_floor'], state['sources'])

    @staticmethod
    def local_edits(flat, state):
        validate_edits(flat)
        if ProjectStore.is_plain_source(state):
            return {'main':copy.deepcopy(flat)}
        from stack_build import edits_to_local
        return edits_to_local(flat, state['sources'])

    def check_revision(self, state, expected_revision, project_id):
        if state['project_id'] != project_id:
            raise ValueError('Wrong project_id')
        if type(expected_revision) is not int or state['revision'] != expected_revision:
            raise RevisionConflict(state['revision'])

    def _commit(self, before, after):
        after['revision'] = before['revision'] + 1
        self._validate_manifest(after)
        atomic_json(self.backup, before)
        atomic_json(self.path, after)
        return after

    def replace_edits(self, edits, expected_revision, project_id, decisions=None):
        with self.locked():
            before = self.read()
            self.check_revision(before, expected_revision, project_id)
            after = copy.deepcopy(before)
            after['edits_by_floor'] = self.local_edits(edits, before)
            if decisions:
                after['decisions'].extend(decisions)
            return self._commit(before, after)

    def update_sources(self, sources, options, expected_revision, project_id):
        with self.locked():
            before = self.read()
            self.check_revision(before, expected_revision, project_id)
            if [s['id'] for s in sources] != [s['id'] for s in before['sources']]:
                raise ValueError('Changing source identities requires a new project')
            after = copy.deepcopy(before)
            after['sources'], after['options'] = copy.deepcopy(sources), copy.deepcopy(options)
            return self._commit(before, after)

    def refresh_inputs(self):
        """Source/map changes invalidate browser CAS just like an explicit project edit."""
        with self.locked():
            before = self.read()
            after = copy.deepcopy(before)
            changed = False
            for source in after['sources']:
                observed = {}
                for key in ('path', 'layer_map', 'block_map', 'schedule'):
                    if source.get(key):
                        try:
                            observed[key] = fingerprint(source[key])
                        except OSError:
                            observed[key] = None
                if observed != source.get('observed_fingerprints', source.get('fingerprints', {})):
                    changed = True
                    source['observed_fingerprints'] = observed
            return self._commit(before, after) if changed else before

    def changed_inputs(self, state=None):
        state = state or self.read()
        changed = []
        for source in state['sources']:
            for key, expected in source.get('fingerprints', {}).items():
                try:
                    actual = fingerprint(source.get(key))
                except OSError:
                    actual = None
                if actual != expected:
                    changed.append(source['id'] + ':' + key)
        return changed

    def import_legacy(self, path, expected_revision, project_id):
        raw = Path(path).read_bytes()
        payload = json.loads(raw)
        if isinstance(payload, dict) and 'project_id' in payload:
            if payload['project_id'] != project_id:
                raise ValueError('Import belongs to a different project')
            payload = payload.get('edits')
        validate_edits(payload)
        with self.locked():
            before = self.read()
            self.check_revision(before, expected_revision, project_id)
            after = copy.deepcopy(before)
            merged = self.flat_edits(before)
            merged.update(payload)
            after['edits_by_floor'] = self.local_edits(merged, before)
            atomic_bytes(self.folder / 'imports' / (uuid.uuid4().hex + '.json'), raw)
            return self._commit(before, after)

    def recover_backup(self):
        with self.locked():
            backup = self._validate_manifest(json.loads(self.backup.read_text(encoding='utf-8')))
            try:
                current_revision = self.read()['revision']
            except ProjectCorrupt:
                current_revision = backup['revision'] + 1
            if self.path.exists():
                atomic_bytes(self.folder / ('project.corrupt.' + uuid.uuid4().hex + '.json'), self.path.read_bytes())
            backup['revision'] = max(current_revision, backup['revision']) + 1
            atomic_json(self.path, backup)
            return self.read()
