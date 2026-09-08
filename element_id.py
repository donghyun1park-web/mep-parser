"""
element_id.py  —  라운드트립 핵심 (드롭인 가능, 자립 모듈)

설계 원칙:
  EID(요소 식별자)는 '원본 DXF 좌표'에서만 파생한다.
  - 배열 인덱스 ❌ (앞에 요소 하나 추가되면 전부 밀림 = 현재 문제)
  - 파생 결과(centerline/width_detected) ❌ (파라미터 바꾸면 같이 변함)
  - 원본 raw 좌표 ✅ (같은 파일 재파싱 시 불변. 파라미터 무관)

핵심 결과:
  벽 폭을 200→150으로 바꿔 중심선이 이동해도, EID는 그대로다.
  → 사용자가 그 벽에 건 수정(override/카테고리/삭제)이 재파싱 후에도 살아남는다.
  EID가 바뀌는 유일한 경우 = grouping이 실제로 바뀔 때(두 벽이 병합/분할).
  이건 '정말 다른 요소가 됐다'는 뜻이라 ID가 바뀌는 게 옳다.
"""
import hashlib
import json
import copy
import math
from collections import Counter

import geom_contract as GC

GRID_MM = 1.0  # 좌표 양자화 격자 (식별용; 1mm면 충분)


# ── 1) 원본 raw 엔티티 시그니처 (파일에서 읽자마자 1회 부여) ──────────
def _q(v):
    return int(round(v / GRID_MM))


def raw_entity_sig(rec):
    """raw DXF 엔티티(정규화 레코드) → 결정론적 짧은 시그니처.
    재파싱 시 같은 파일이면 항상 동일. 방향(역순)·시점 차이를 정규화."""
    kind = rec["kind"]
    if kind == "circle":
        c = rec["center"]
        payload = ("C", _q(c[0]), _q(c[1]), _q(rec["radius"]))
    else:  # polyline / line
        pts = [(_q(p[0]), _q(p[1])) for p in rec["points"]]
        # 역순 동일 요소로 취급 → 사전순 더 작은 쪽 채택
        rev = list(reversed(pts))
        canon = min(pts, rev)
        payload = ("P", int(rec.get("closed", False)), tuple(canon))
    h = hashlib.sha1(repr(payload).encode()).hexdigest()[:10]
    return h


# ── 2) 요소 EID = 기여한 원본 엔티티 시그니처들의 결정론적 해시 ──────
def element_eid(geom_prefix, source_sigs):
    """source_sigs: 이 요소를 구성한 raw 엔티티 시그니처 리스트.
    벽=페어링된 2선의 sig 2개, 슬래브=자기 폴리라인 sig 1개 등.
    정렬해서 해시 → 입력 순서 무관, grouping 동일하면 EID 동일."""
    key = "|".join(sorted(source_sigs))
    h = hashlib.sha1(key.encode()).hexdigest()[:8]
    return f"{geom_prefix}:{h}"


# ── 3) 수정(edits) 사이드카 적용 = 라운드트립 본체 ──────────────────
def apply_edits(elements, edits):
    """fresh 파싱 결과(elements)에 저장된 edits를 EID로 재적용.
    elements: {category: [rec, ...]}, 각 rec에 rec['eid'] 존재 가정.
    edits: {eid: {"overrides":{...}, "category":..., "deleted":bool, "review_resolved":bool}}
       또는 신규 추가: {eid: {"added": true, "category": <cat>, "record": {...}}}
         (preview.py 반자동 창호 배치 — DXF에 없는 사용자 생성 요소).
    반환: report(적용/고아/추가/현재EID목록)."""
    if not isinstance(edits, dict):
        raise ValueError('수정 목록은 EID 객체여야 합니다')
    present, ambiguous, legacy = {}, set(), {}
    for cat, items in elements.items():
        for rec in items:
            eid = rec["eid"]
            if eid in present:
                # 같은 EID 를 두 레코드가 쓴다 = 어느 쪽을 고칠지 알 수 없다.
                # 종전에는 마지막 것이 조용히 이겼고, 그래서 사용자가 A 를 고치면
                # B 가 바뀌었다. 이제는 적용하지 않고 보고한다.
                ambiguous.add(eid)
            present[eid] = (cat, rec)
            v1 = rec.get("eid_v1")
            if v1 and v1 != eid:
                # 옛 공식으로 만든 ID → 구 edits.json 이어받기. 둘 이상이 같은 옛
                # ID 를 가리키면 그 수정이 어느 벽 것이었는지 파일에 없다 — 포기한다.
                legacy[v1] = None if v1 in legacy else (cat, rec)

    applied, orphans, added, migrated = [], [], [], []
    move_ops = []
    for eid, edit in edits.items():
        if not isinstance(edit, dict):
            raise ValueError(f'{eid}: 수정은 객체여야 합니다')
        if edit.get('category') and edit['category'] not in GC.Z_DATUM:
            raise ValueError(f'{eid}: 알 수 없는 카테고리 {edit["category"]}')
        if eid in ambiguous:
            orphans.append(eid)
            continue
        # 신규 추가 요소: DXF 재파싱과 무관하게 elements 에 주입(멱등 — 이미 있으면 스킵).
        if edit.get("added"):
            if eid in present:
                old_cat, previous = present[eid]
                if not str(previous.get('source', '')).startswith('manual'):
                    orphans.append(eid)
                    continue
                elements[old_cat].remove(previous)
            cat = edit.get("category", "opening")
            rec = copy.deepcopy(edit.get("record", {}))
            rec["eid"] = eid
            rec.setdefault('source', 'manual_edit')
            elements.setdefault(cat, []).append(rec)
            present[eid] = (cat, rec)
            added.append(eid)
        elif eid in present:
            cat, rec = present[eid]
        elif legacy.get(eid):
            cat, rec = legacy[eid]
            migrated.append(eid)
        else:
            orphans.append(eid)
            continue
        if edit.get("overrides"):
            rec.setdefault("overrides", {}).update(copy.deepcopy(edit["overrides"]))
        if edit.get("review_resolved"):
            rec["review_resolved"] = True
            rec['_review_signature'] = edit.get('_review_signature')
        elif 'review_resolved' in edit:
            rec['review_resolved'] = False
        rec['_edited'] = True
        if edit.get("deleted"):
            rec["_deleted"] = True
        new_cat = edit.get("category")
        if new_cat and new_cat != cat:
            move_ops.append((cat, rec, new_cat))
        if eid not in added:
            applied.append(eid)

    # 카테고리 이동 (순회 후 일괄)
    for old_cat, rec, new_cat in move_ops:
        elements[old_cat].remove(rec)
        elements.setdefault(new_cat, []).append(rec)

    # 삭제 표시 제거
    for cat in list(elements):
        elements[cat] = [r for r in elements[cat] if not r.get("_deleted")]

    return {
        "applied": sorted(applied),
        "orphaned": sorted(orphans),
        "added": sorted(added),
        "migrated": sorted(migrated),
        "ambiguous": sorted(ambiguous),
        "current_eids": sorted(r['eid'] for items in elements.values() for r in items),
    }


# ── 4) 고아 edit 재연결 제안 (자동적용 ❌, 제안만 — 프로젝트 철학 준수) ─
def suggest_relink(orphan_eids, edits, elements, max_suggest=5):
    """Same-floor/category candidates ranked by distance, direction and overlap.

    The score is a navigation aid, never permission to apply an edit. Legacy
    records without a location cannot support a geometric suggestion.
    """
    cur = [(cat, rec) for cat, items in elements.items() for rec in items]
    counts = Counter(rec.get('eid') for _, rec in cur)
    out = []
    for oe in sorted(orphan_eids):
        edit = edits[oe]
        source = edit.get('_source') or {}
        points = _points(source)
        anchor = edit.get('_at') or source.get('center') or (points[0] if points else None)
        row = {'orphan': oe, 'candidates': [], 'details': [], 'edit': copy.deepcopy(edit)}
        out.append(row)
        if not anchor or len(anchor) < 2:
            row['reason'] = 'missing_source_location'
            continue
        cat = source.get('category') or category_from_eid(oe)
        floor = _floor(oe, source)
        ranked = []
        for current_cat, rec in cur:
            eid = rec.get('eid')
            if not eid or counts[eid] != 1 or current_cat != cat or _floor(eid, rec) != floor:
                continue
            if not floor and any(k in source for k in ('z_base', 'elevation')):
                if abs(GC.base_z(cat, source) - GC.base_z(cat, rec)) > 100:
                    continue
            cp = _points(rec)
            if not cp and rec.get('center'):
                cp = [rec['center']]
            if not cp:
                continue
            distance = min((_point_segment_distance(anchor, a, b) for a, b in zip(cp, cp[1:])),
                           default=math.dist(anchor[:2], cp[0][:2]))
            angle, overlap = _direction_overlap(points, cp)
            ranked.append((round(distance, 6), round(angle, 6), -round(overlap, 6), eid))
        for distance, angle, neg_overlap, eid in sorted(ranked)[:max(0, min(5, max_suggest))]:
            row['candidates'].append(eid)
            row['details'].append({'eid': eid, 'distance_mm': round(distance, 3),
                                   'angle_deg': round(angle, 3), 'overlap_mm': round(-neg_overlap, 3)})
        if not ranked:
            row['reason'] = 'no_unambiguous_candidate'
    return out


def category_from_eid(eid):
    prefix = str(eid).split(':')[-2] if ':' in str(eid) else ''
    return {'w': 'wall', 'wm': 'wall', 'c': 'column', 's': 'slab', 'b': 'beam',
            'z': 'zone', 'o': 'opening', 'om': 'opening', 'p': 'pipe',
            'd': 'duct', 't': 'tray', 'e': 'equipment'}.get(prefix)


def _floor(eid, rec):
    return str(rec.get('level') or (str(eid).rsplit(':', 2)[0] if str(eid).count(':') >= 2 else ''))


def _points(rec):
    return rec.get('centerline') or rec.get('points') or []


def _point_segment_distance(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    ll = dx * dx + dy * dy
    t = max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / ll)) if ll else 0
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _direction_overlap(a, b):
    if len(a) < 2 or len(b) < 2:
        return 0, 0
    u = (a[-1][0] - a[0][0], a[-1][1] - a[0][1])
    v = (b[-1][0] - b[0][0], b[-1][1] - b[0][1])
    lu, lv = math.hypot(*u), math.hypot(*v)
    if not lu or not lv:
        return 0, 0
    angle = math.degrees(math.acos(min(1, abs((u[0] * v[0] + u[1] * v[1]) / (lu * lv)))))
    projections = [((p[0] - a[0][0]) * u[0] + (p[1] - a[0][1]) * u[1]) / lu for p in b]
    return angle, max(0, min(lu, max(projections)) - max(0, min(projections)))


def source_snapshot(rec, category):
    keys = ('eid', 'level', 'kind', 'closed', 'points', 'centerline', 'center', 'radius',
            'z_base', 'elevation', 'layer', 'width_detected', 'pairing', 'overrides',
            'width', 'height', 'thickness', 'diameter', 'width_mm', 'height_mm', 'sill',
            'subtype', 'section', 'review_reason', 'dims_assumed', 'schedule_match')
    return dict({k: copy.deepcopy(rec[k]) for k in keys if k in rec}, category=category)


def review_signature(category, rec, params=None):
    """Hash reviewed meaning; exclude identifiers, timestamps and acknowledgement flags."""
    snap = source_snapshot(rec, category)
    for key in ('eid', 'level', 'overrides'):
        snap.pop(key, None)
    if snap.get('review_reason') in ('review_changed', 'legacy_review_unverified'):
        snap.pop('review_reason', None)
    snap['effective_z'] = GC.z_range(category, rec, params)
    snap['effective_width'] = GC.width_of(rec, params, category)
    snap['effective_overrides'] = rec.get('overrides') or {}
    snap['diagnostics'] = rec.get('edit_diagnostics') or []
    def canonical(value):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return round(float(value), 6)
        if isinstance(value, dict):
            return {k: canonical(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [canonical(v) for v in value]
        return value
    raw = json.dumps(canonical(snap), ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def capture_edit(rec, edit, category, params=None):
    """Capture the old target once, and bind an explicit acknowledgement to its context."""
    out = copy.deepcopy(edit)
    out.setdefault('_source', source_snapshot(rec, category))
    points = _points(rec)
    at = rec.get('center') or (points[0] if points else None)
    if at:
        out.setdefault('_at', copy.deepcopy(at[:2]))
    if out.get('review_resolved') and not out.get('_review_signature'):
        effective = copy.deepcopy(out.get('record') if out.get('added') else rec)
        effective.setdefault('overrides', {}).update(out.get('overrides') or {})
        out['_review_signature'] = review_signature(out.get('category') or category, effective, params)
    return out


def finalize_reviews(elements, params=None):
    """Run after every diagnostic; an old yes must not hide a new condition."""
    stale = []
    for cat, records in elements.items():
        for rec in records:
            if not rec.get('review_resolved'):
                continue
            sig = rec.get('_review_signature')
            if sig and sig == review_signature(cat, rec, params):
                rec['needs_review'] = False
                rec.pop('review_ack_stale', None)
            else:
                rec['review_resolved'] = False
                rec['review_ack_stale'] = True
                rec['needs_review'] = True
                rec.setdefault('review_reason', 'review_changed' if sig else 'legacy_review_unverified')
                stale.append(rec['eid'])
    return stale
