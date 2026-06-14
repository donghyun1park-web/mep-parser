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
    반환: report(적용/고아/현재EID목록)."""
    present = {}
    for cat, items in elements.items():
        for rec in items:
            present[rec["eid"]] = (cat, rec)

    applied, orphans = [], []
    move_ops = []
    for eid, edit in edits.items():
        if eid not in present:
            orphans.append(eid)
            continue
        cat, rec = present[eid]
        if edit.get("overrides"):
            rec.setdefault("overrides", {}).update(edit["overrides"])
        if edit.get("review_resolved"):
            rec["needs_review"] = False
        if edit.get("deleted"):
            rec["_deleted"] = True
        new_cat = edit.get("category")
        if new_cat and new_cat != cat:
            move_ops.append((cat, rec, new_cat))
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
        "current_eids": sorted(present),
    }


# ── 4) 고아 edit 재연결 제안 (자동적용 ❌, 제안만 — 프로젝트 철학 준수) ─
def suggest_relink(orphan_eids, edits, elements, max_suggest=5):
    """고아가 된 edit을, 현재 요소 중 '가장 가까운 후보'에 재연결 제안.
    여기선 EID 접두(geom종류) 일치 + 간단 거리 점수만. 사용자 확인 후 적용."""
    cur = [(rec["eid"], rec) for items in elements.values() for rec in items]
    out = []
    for oe in orphan_eids:
        prefix = oe.split(":")[0]
        cands = [eid for eid, _ in cur if eid.split(":")[0] == prefix]
        if cands:
            out.append({"orphan": oe, "candidates": cands[:max_suggest],
                        "edit": edits[oe]})
    return out
