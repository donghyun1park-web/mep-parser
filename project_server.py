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

from project_store import ProjectStore, RevisionConflict, atomic_bytes, atomic_json, validate_edits, fingerprint


class ProjectSession:
    def __init__(self, store):
        self.store = store
        self._mutex = threading.RLock()
        self._cached_state = None
        self._cached_drawing = None

    def _parse_source(self, source, edits):
        import dxf_parser as parser
        from drawing_units import uses_legacy_units
        rules = parser.load_layer_map(source['layer_map']) if source.get('layer_map') else parser.DEFAULT_LAYER_RULES
        block = parser.load_layer_map(source['block_map']) if source.get('block_map') else parser.DEFAULT_BLOCK_RULES
        options = dict(source.get('options') or {})
        options['legacy_units'] = uses_legacy_units(source)
        if source.get('height') is not None:
            options['level_height'] = source['height']
        if source.get('schedule'):
            from schedule_io import load_schedule
            options['ext_schedule'] = load_schedule(source['schedule'])
        with contextlib.redirect_stdout(io.StringIO()):
            return parser.parse(source['path'], rules, block, edits=edits, **options)

    def _parse(self, manifest):
        sources = manifest['sources']
        if self.store.is_plain_source(manifest):
            data = self._parse_source(sources[0], manifest['edits_by_floor']['main'])
        else:
            from stack_build import build_stack
            from drawing_units import uses_legacy_units
            levels = []
            for source in sources:
                level = dict(source, source=source['path'], edits=manifest['edits_by_floor'].get(source['id'], {}))
                level['options'] = dict(source.get('options') or {})
                level['options']['legacy_units'] = uses_legacy_units(source)
                if source.get('schedule'):
                    from schedule_io import load_schedule
                    level['options']['ext_schedule'] = load_schedule(source['schedule'])
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
        try:
            from clash_review import find_clashes
            data['clash_review'] = find_clashes(data)
        except Exception as exc:
            # 간섭 목록은 검토 보조다 — 실패해도 모델·수정은 막지 않되, 못 만든 사실은 남긴다.
            data['clash_review'] = {'items': [], 'summary': {'total': 0, 'error': f'{type(exc).__name__}: {exc}'}}
        try:
            from mep_network import analyze, apply_bridges
            net = analyze(data)
            # 확정한 이음을 기록하고 **그 뒤 상태**를 다시 센다 — 확정한 자리가 화면에 계속 '끊긴 끝'·'후보' 로
            # 남으면 사람이 같은 자리를 두 번 확인한다.
            report = apply_bridges(data, manifest.get('bridges') or [], net.get('candidates'))
            if report['applied']:
                net = analyze(data)
            net['bridges'] = report
            data['mep_connectivity'] = net
        except Exception as exc:
            data['mep_connectivity'] = {'candidates': [], 'conflicts': [], 'open_ends': [],
                                        'bridges': {'applied': [], 'orphaned': []},
                                        'summary': {'error': f'{type(exc).__name__}: {exc}'}}
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

    def _profile_source(self, manifest, source_id):
        source = next((item for item in manifest['sources'] if item['id'] == source_id), None)
        if source is None:
            raise ValueError('Unknown source identity')
        return source

    @staticmethod
    def _hold_unit_edits(manifest, source_id, old_scale, new_scale):
        """Preserve edits as orphans when geometry-based IDs can collide across scales."""
        import math
        # ezdxf's inch conversion differs from 25.4 by floating-point rounding.
        if old_scale is not None and new_scale is not None and math.isclose(old_scale, new_scale, rel_tol=1e-9):
            return {}
        edits = manifest['edits_by_floor'].get(source_id, {})
        held, remapped = {}, {}
        for eid, edit in edits.items():
            if edit.get('added'):
                edit['review_resolved'] = False
                edit.pop('_review_signature', None)
                held[eid] = edit
            elif eid.partition(':')[2].startswith('unit-r') and edit.get('_unit_source_eid'):
                held[eid] = edit
            else:
                prefix, _, suffix = eid.partition(':')
                key = f'{prefix}:unit-r{manifest["revision"] + 1}-{suffix}'
                while key in edits or key in held:
                    key += '-' + secrets.token_hex(4)
                edit['_unit_source_eid'] = eid
                held[key] = edit
                remapped[eid] = key
        manifest['edits_by_floor'][source_id] = held
        return remapped

    def configure_source(self, profile, expected_revision, project_id, source_id='main', proposal_id=None):
        """Validate and parse a candidate before atomically saving project-local MEP settings."""
        from mep_profile import validate_profile
        with self._mutex:
            manifest = self.store.refresh_inputs()
            self.store.check_revision(manifest, expected_revision, project_id)
            proposed = copy.deepcopy(manifest)
            source = self._profile_source(proposed, source_id)
            from drawing_units import option_scale, unit_review, uses_legacy_units
            previous_explicit = option_scale(source.get('options') or {})
            candidate = copy.deepcopy(profile)
            options = source.setdefault('options', {})
            if candidate.get('unit_scale_to_mm') is None and options.get('unit_scale_to_mm') is not None:
                candidate['unit_scale_to_mm'] = options['unit_scale_to_mm']
            normalized = validate_profile(candidate, source_sha256=fingerprint(source['path']))
            held_edits = {}
            if previous_explicit != normalized.get('unit_scale_to_mm') or uses_legacy_units(source):
                import ezdxf
                doc = ezdxf.readfile(source['path'])
                before_scale = unit_review(doc, previous_explicit, legacy=True,
                    legacy_header=uses_legacy_units(source))['effective_scale_to_mm']
                after_scale = unit_review(doc, normalized.get('unit_scale_to_mm'))['effective_scale_to_mm']
                held_edits = self._hold_unit_edits(proposed, source_id, before_scale, after_scale)
            options.pop('unit_scale_to_mm', None)
            options['mep_profile'] = normalized
            try:
                self._parse(proposed)
            except Exception as exc:
                raise ValueError('MEP candidate cannot be parsed; project was not changed: ' + str(exc)) from exc
            self.store.check_revision(self.store.refresh_inputs(), expected_revision, project_id)
            decision = {'action': 'apply_mep_profile', 'source_id': source_id,
                        'revision': expected_revision, 'source_sha256': normalized['source_sha256'],
                        'unit_edits_held': held_edits}
            if proposal_id:
                decision['proposal_id'] = proposal_id
            self.store.update_sources(proposed['sources'], proposed['options'], expected_revision,
                                      project_id, decisions=[decision], edits_by_floor=proposed['edits_by_floor'])
            return self.state()

    MEP_CATEGORIES = ('pipe', 'duct', 'tray', 'equipment')

    def add_source(self, path, expected_revision, project_id, categories=MEP_CATEGORIES, source_id=None):
        """같은 층에 공종 도면을 겹친다 — 기준(첫) 원본의 층·z·offset·매핑을 물려받고, 담을 종류는 `categories`.

        설비 도면도 건축 배경을 물고 있어 전부 담으면 벽이 도면 수만큼 겹친다 — 기본은 설비 종류만.
        후보를 먼저 파싱해 보고(설정 저장과 같은 규칙) 실패하면 프로젝트를 바꾸지 않는다."""
        with self._mutex:
            manifest = self.store.refresh_inputs()
            self.store.check_revision(manifest, expected_revision, project_id)
            base = manifest['sources'][0]
            ids = {s['id'] for s in manifest['sources']}
            sid = source_id or next('src%d' % n for n in range(2, len(ids) + 3) if 'src%d' % n not in ids)
            base_label = 'Level_1' if self.store.is_plain_source(manifest) else None
            source = dict(id=sid, path=str(path), floor=base.get('floor') or base['id'], z=base.get('z', 0),
                          offset=list(base.get('offset') or [0, 0]), categories=list(categories),
                          layer_map=base.get('layer_map'), block_map=base.get('block_map'), options={})
            proposed = copy.deepcopy(manifest)
            if base_label:
                proposed['sources'][0].setdefault('label', base_label)
            proposed['edits_by_floor'][sid] = {}
            try:
                proposed['sources'].append(self.store._normalize_source(source, ids))
                self._parse(proposed)
            except Exception as exc:
                raise ValueError('도면을 추가할 수 없습니다(프로젝트는 바뀌지 않았습니다): ' + str(exc)) from exc
            self.store.add_source(source, expected_revision, project_id, base_label=base_label,
                                  decisions=[{'action': 'add_source', 'source_id': sid, 'revision': expected_revision,
                                              'categories': list(categories)}])
            return self.state()

    def configure_units(self, scale, expected_revision, project_id, source_id='main', *, source_sha256):
        """Save reviewed source units without requiring a MEP mapping or losing edits."""
        import ezdxf
        from drawing_units import positive_scale, option_scale, unit_review, uses_legacy_units
        scale = positive_scale(scale)
        with self._mutex:
            manifest = self.store.refresh_inputs()
            self.store.check_revision(manifest, expected_revision, project_id)
            proposed = copy.deepcopy(manifest)
            source = self._profile_source(proposed, source_id)
            actual_hash = fingerprint(source['path'])
            if source_sha256 != actual_hash:
                raise ValueError('Source SHA256 hash changed; review source units again')
            options = source.setdefault('options', {})
            doc = ezdxf.readfile(source['path'])
            before = unit_review(doc, option_scale(options), legacy=options.get('mep_profile') is None,
                                 legacy_header=uses_legacy_units(source))
            profile = options.get('mep_profile')
            factor = None
            if profile is not None:
                if profile.get('region'):
                    old_scale = before['effective_scale_to_mm']
                    if old_scale is None:
                        raise ValueError('Previous region units unknown; review the MEP region first')
                    factor = scale / old_scale
                    # Preserve the exact source-space selection; physical sections/heights
                    # and user edits already in mm must not be multiplied.
                    profile['region']['bounds_mm'] = [v * factor for v in profile['region']['bounds_mm']]
                profile['unit_scale_to_mm'] = scale
                options.pop('unit_scale_to_mm', None)
            else:
                options['unit_scale_to_mm'] = scale
            held_edits = self._hold_unit_edits(proposed, source_id, before['effective_scale_to_mm'], scale)
            self._parse(proposed)
            self.store.check_revision(self.store.refresh_inputs(), expected_revision, project_id)
            decision = {'action': 'set_source_units', 'source_id': source_id, 'source_sha256': actual_hash,
                        'previous_scale_to_mm': before['effective_scale_to_mm'], 'unit_scale_to_mm': scale,
                        'region_scale_factor': factor, 'unit_edits_held': held_edits}
            self.store.update_sources(proposed['sources'], proposed['options'], expected_revision,
                                      project_id, decisions=[decision], edits_by_floor=proposed['edits_by_floor'])
            return self.state()

    def source_units(self, source_id='main'):
        from drawing_units import option_scale, uses_legacy_units
        from mep_profile import inspect_mep_source
        with self._mutex:
            manifest = self.store.refresh_inputs()
            source = self._profile_source(manifest, source_id)
            inventory = inspect_mep_source(source['path'], option_scale(source.get('options') or {}),
                                           legacy_units=uses_legacy_units(source))
            self.store.check_revision(self.store.refresh_inputs(), manifest['revision'], manifest['project_id'])
            return {'project_id': manifest['project_id'], 'revision': manifest['revision'],
                    'source_id': source_id, 'inventory': inventory}

    def measure_outline_widths(self, rule, source_id='main', region_bounds_mm=None):
        """설비 규칙의 중심선마다 외곽선 폭을 잰다(읽기 전용). 영역을 안 주면 저장된 프로필의 영역."""
        from drawing_units import option_scale, uses_legacy_units
        from mep_profile import measure_outline_widths
        with self._mutex:
            manifest = self.store.refresh_inputs()
            source = self._profile_source(manifest, source_id)
            options = source.get('options') or {}
            region = region_bounds_mm or ((options.get('mep_profile') or {}).get('region') or {}).get('bounds_mm')
            result = measure_outline_widths(source['path'], rule, option_scale(options),
                                            legacy_units=uses_legacy_units(source), region=region)
            return dict(result, project_id=manifest['project_id'], revision=manifest['revision'], source_id=source_id)

    def measure_equipment_bodies(self, rule, source_id='main', region_bounds_mm=None):
        """장비 규칙이 고르는 기호마다 닫힌 면을 세어 본체 후보를 제안한다(읽기 전용). 고르는 것은 사람이다."""
        from drawing_units import option_scale, uses_legacy_units
        from mep_profile import measure_equipment_bodies
        with self._mutex:
            manifest = self.store.refresh_inputs()
            source = self._profile_source(manifest, source_id)
            options = source.get('options') or {}
            region = region_bounds_mm or ((options.get('mep_profile') or {}).get('region') or {}).get('bounds_mm')
            result = measure_equipment_bodies(source['path'], rule, option_scale(options),
                                              legacy_units=uses_legacy_units(source), region=region)
            return dict(result, project_id=manifest['project_id'], revision=manifest['revision'], source_id=source_id)

    def propose_mep_profile(self, profile, expected_revision, project_id, source_id='main', reason=''):
        """Save a reviewable proposal. Never changes a profile, geometry or review acknowledgement."""
        from mep_profile import validate_profile
        with self._mutex:
            manifest = self.store.refresh_inputs()
            self.store.check_revision(manifest, expected_revision, project_id)
            source = self._profile_source(manifest, source_id)
            normalized = validate_profile(profile, source_sha256=fingerprint(source['path']))
            proposal = {'schema_version': 1, 'proposal_id': secrets.token_hex(16),
                        'project_id': project_id, 'base_revision': expected_revision,
                        'source_id': source_id, 'source_sha256': normalized['source_sha256'],
                        'profile': normalized, 'reason': str(reason)[:8000]}
            self.store.check_revision(self.store.refresh_inputs(), expected_revision, project_id)
            atomic_json(self.store.folder / 'proposals' / (proposal['proposal_id'] + '.json'), proposal)
            return proposal

    def mep_proposals(self):
        manifest = self.store.refresh_inputs()
        applied = {item.get('proposal_id') for item in manifest.get('decisions', [])}
        proposals = []
        for path in sorted((self.store.folder / 'proposals').glob('*.json')):
            try:
                item = json.loads(path.read_text(encoding='utf-8'))
                if item['project_id'] != manifest['project_id'] or item['proposal_id'] in applied:
                    continue
                item['stale'] = item['base_revision'] != manifest['revision']
                proposals.append(item)
            except (ValueError, KeyError, OSError):
                continue
        return proposals

    def apply_mep_proposal(self, proposal_id, expected_revision, project_id):
        if not isinstance(proposal_id, str) or len(proposal_id) != 32 or any(c not in '0123456789abcdef' for c in proposal_id):
            raise ValueError('Invalid proposal identity')
        proposal = json.loads((self.store.folder / 'proposals' / (proposal_id + '.json')).read_text(encoding='utf-8'))
        manifest = self.store.refresh_inputs()
        self.store.check_revision(manifest, expected_revision, project_id)
        self.store.check_revision(manifest, proposal['base_revision'], proposal['project_id'])
        return self.configure_source(proposal['profile'], expected_revision, project_id,
                                     proposal['source_id'], proposal_id=proposal_id)

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

    def confirm_bridge(self, candidate_id, expected_revision, project_id, confirmed=True, reason=''):
        """이음 후보 하나를 사람이 확정(또는 취소)한다. 형상은 바뀌지 않고 `joints` 에만 기록된다."""
        return self.confirm_bridges([candidate_id], expected_revision, project_id, confirmed, reason)

    def confirm_bridges(self, candidate_ids, expected_revision, project_id, confirmed=True, reason=''):
        """확정(취소)을 **한 번에** 처리한다 — revision 하나, 결정 기록 하나.

        후보 26건을 버튼 26번·revision 26번으로 밟게 하지 않는다. 대신 **전부 아니면 아무것도** 다 —
        모르는 후보가 하나라도 섞이면 통째로 거부한다(부분 적용은 무엇이 들어갔는지 아무도 모르게 만든다)."""
        ids = [str(c) for c in candidate_ids]
        if not ids or len(set(ids)) != len(ids):
            raise ValueError('candidate_ids must be a nonempty list of distinct ids')
        with self._mutex:
            manifest = self.store.refresh_inputs()
            self.store.check_revision(manifest, expected_revision, project_id)
            if confirmed:
                live = {c['id'] for c in ((self.state()['geometry'].get('mep_connectivity') or {}).get('candidates') or [])}
                unknown = [i for i in ids if i not in live]
                if unknown:
                    raise ValueError('Unknown connection candidate: ' + ', '.join(unknown))
            kept = [b for b in (manifest.get('bridges') or []) if b.get('id') not in set(ids)]
            if confirmed:
                kept.extend({'id': i, 'reason': str(reason)[:500]} for i in ids)
            self.store.set_bridges(kept, expected_revision, project_id,
                                   decisions=[{'action': 'confirm_bridges' if confirmed else 'unconfirm_bridges',
                                               'candidate_ids': ids, 'revision': expected_revision}])
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
        from urllib.parse import quote
        from pascal_bridge import add_source_guides, scene_sha256, to_pascal_scene
        state = self.preview_state()                  # 원본 선까지(캐시된다) — guide 밑그림용
        drawing = state['geometry'].pop('source_drawing', None)
        scene, report = to_pascal_scene(state['geometry'])
        # ★ 지문은 guide 를 싣기 **전**에 잰다. 적용(`pascal_apply`)이 원본 선 없이 같은 식으로 다시 재므로,
        #   guide 까지 넣고 재면 저장할 때마다 409 가 난다. guide 는 되돌리기가 건너뛰는 밑그림이다.
        sha = scene_sha256(scene)
        report['guides'] = add_source_guides(
            scene, drawing, lambda floor: '/api/mep/source?floor=' + quote(str(floor.get('id')), safe=''))
        return {'project_id': state['project_id'], 'revision': state['revision'],
                'snapshot_sha256': sha, 'scene': scene, 'report': report}

    def pascal_source_svg(self, floor_id):
        """한 층의 원본 선 SVG(북쪽이 위). 층이 없거나 그릴 선이 없으면 KeyError."""
        from source_drawing import drawing_svg
        drawing = self.preview_state()['geometry'].get('source_drawing') or {}
        floor = next((f for f in drawing.get('floors') or [] if str(f.get('id')) == str(floor_id)), None)
        svg = drawing_svg(floor) if floor else None
        if svg is None:
            raise KeyError('no source drawing for floor %r' % floor_id)
        return svg

    def pascal_review(self):
        """편집 화면의 검토 목록 — 사람이 봐야 할 부재(검토 사유 · 붙일 벽이 없는 개구부 · 끊긴 이음)와
        Pascal 로 못 보낸 부재. 조치할 것이 없는 사유(문 자리에서 끊어 그린 벽)는 싣지 않는다."""
        import geom_contract as GC
        from pascal_bridge import to_pascal_scene
        state = self.state()
        elements = state['geometry'].get('elements') or {}
        items = []
        for cat, recs in elements.items():
            for rec in recs:
                reason = (rec.get('review_reason') or 'needs_review') if rec.get('needs_review') else None
                if cat == 'opening' and rec.get('no_host_reason') in (
                        'no_wall_on_this_line', 'no_wall_at_this_level', 'no_walls_to_check'):
                    reason = reason or 'opening_' + rec['no_host_reason']
                if reason:
                    items.append({'category': cat, 'eid': rec.get('eid'), 'layer': rec.get('layer'),
                                  'reason': reason})
        for problem in GC.joint_problems(elements):
            items.append({'category': 'joint', 'eid': (problem['eids'] or [None])[0],
                          'eids': problem['eids'], 'reason': 'joint_' + problem['problem']})
        for clash in (state['geometry'].get('clash_review') or {}).get('items', []):
            items.append({'category': 'clash', 'eid': clash['mep']['eid'], 'layer': clash['struct'].get('layer'),
                          # 합성 슬래브는 부재가 아니라 EID 가 없다 — 목록에서 고를 수 있는 것만 싣는다.
                          'eids': [e for e in (clash['struct']['eid'], clash['mep']['eid']) if e],
                          'reason': clash['action'],
                          'at': clash['at'], 'z': clash['z'], 'z_basis': clash.get('basis')})
        from mep_network import KIND_LABELS
        net = state['geometry'].get('mep_connectivity') or {}
        for gap in net.get('candidates', []) + [dict(c, conflict=True) for c in net.get('conflicts', [])]:
            kind = KIND_LABELS.get(gap['kind'], gap['kind'])
            items.append({'category': 'mep_gap', 'eid': gap['eids'][0], 'eids': gap['eids'], 'at': gap['points'][0],
                          'z_basis': gap.get('z_basis'),
                          'reason': (f"다른 계통 끝이 {kind}형으로 맞닿음({' ↔ '.join(map(str, gap['systems']))}) — 도면 확인"
                                     if gap.get('conflict') else f"{kind} 이음 후보 · 틈 {gap['gap_mm']:.0f}mm (확정 아님)")})
        _scene, report = to_pascal_scene(state['geometry'])
        # 평면도에는 높이가 없다 — 목록 전체가 가정 위에 서 있다는 사실은 줄마다의 표시로는 안 보인다.
        clash_summary = (state['geometry'].get('clash_review') or {}).get('summary') or {}
        net_summary = net.get('summary') or {}
        return {'project_id': state['project_id'], 'revision': state['revision'], 'items': items,
                'summary': {'clash_total': clash_summary.get('total', 0),
                            'clash_assumed': clash_summary.get('assumed_basis', 0),
                            'gap_total': net_summary.get('candidates', 0),
                            'gap_assumed': (net_summary.get('assumed_basis') or {}).get('candidates', 0)},
                'unconvertible': report['unconvertible']}

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
                return {'duplicate': True, 'op_id': op_id, 'state': self.state(),
                        'snapshot': self.pascal_snapshot()}
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
            out = {'applied': not dry_run, 'dry_run': bool(dry_run), 'op_id': op_id,
                   'commands': commands, 'summary': summary, 'state': result}
            if not dry_run:
                # 편집 화면은 이 씬으로 **갈아 끼워야** 다음 저장이 맞는다. 이동한 부재는
                # 새 EID·새 노드 id 를 받으므로, 화면에 남은 옛 노드로 다시 저장하면
                # 방금 만든 복사본을 지우고 옛것을 되살리는 명령이 나온다.
                out['snapshot'] = self.pascal_snapshot()
            return out


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
                if path not in ('/state','/preview','/pascal/snapshot','/pascal/source.svg','/pascal/review','/source-units'):
                    self.reply(404, {'error':'Unknown endpoint'})
                    return
                if not self.authorized(preview=path == '/preview'):
                    return
                try:
                    if path == '/source-units':
                        source_id = parse_qs(urlsplit(self.path).query).get('source_id', ['main'])[0]
                        self.reply(200, session.source_units(source_id))
                        return
                    if path == '/pascal/snapshot':
                        self.reply(200, session.pascal_snapshot())
                        return
                    if path == '/pascal/review':
                        self.reply(200, session.pascal_review())
                        return
                    if path == '/pascal/source.svg':
                        floor = parse_qs(urlsplit(self.path).query).get('floor', [''])[0]
                        try:
                            svg = session.pascal_source_svg(floor)
                        except KeyError as exc:
                            self.reply(404, {'error': str(exc)})
                            return
                        self.reply(200, svg, 'image/svg+xml')
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
                if path not in ('/edits', '/discard', '/relink', '/defer', '/pascal/apply', '/source-units', '/bridges'):
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
                    elif path == '/source-units':
                        result = session.configure_units(body['unit_scale_to_mm'], *common,
                            body.get('source_id', 'main'), source_sha256=body['source_sha256'])
                    elif path == '/bridges':
                        # 설비 이음 후보 확정·취소 — 형상은 안 바뀌고 `joints` 에만 기록된다.
                        # 여럿은 `candidate_ids` 로 한 번에(revision 하나). 둘 다 오면 무엇을 저장할지 모른다.
                        if ('candidate_id' in body) == ('candidate_ids' in body):
                            raise ValueError('Send exactly one of candidate_id or candidate_ids')
                        ids = body.get('candidate_ids') or [body.get('candidate_id')]
                        if not isinstance(ids, list):
                            raise ValueError('candidate_ids must be a list')
                        result = session.confirm_bridges(ids, *common, bool(body.get('confirmed', True)))
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
