# -*- coding: utf-8 -*-
"""EID 라운드트립 — 설계의 핵심 주장 2개.

  주장1: 벽 폭 200→150(중심선 이동) 후에도 EID 불변 → 사용자 수정이 살아남는다.
  주장2: 원본 선이 빠져 grouping 이 바뀌면 EID 도 바뀌고, 그때 수정은 조용히
         사라지는 대신 '고아' 로 정직하게 보고된다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from element_id import raw_entity_sig, element_eid, apply_edits, suggest_relink

# 원본 DXF 의 raw 세그먼트(불변). 벽1 = 평행 2선, 벽2 = 평행 2선.
RAW = {
    "wall1_faceA": {"kind": "polyline", "closed": False, "points": [[0, 0], [5000, 0]]},
    "wall1_faceB": {"kind": "polyline", "closed": False, "points": [[0, 200], [5000, 200]]},
    "wall2_faceA": {"kind": "polyline", "closed": False, "points": [[0, 4000], [3000, 4000]]},
    "wall2_faceB": {"kind": "polyline", "closed": False, "points": [[0, 4200], [3000, 4200]]},
}


def parse(raw, wall_width):
    """파서 모사: raw 2선을 페어링 → 벽 1개. 중심선은 width 에 따라 달라지는 파생물.
    EID 는 '원본 두 선의 sig' 에서만 계산 → width 와 무관."""
    elements = {"wall": []}
    for a, b in (("wall1_faceA", "wall1_faceB"), ("wall2_faceA", "wall2_faceB")):
        if a not in raw or b not in raw:
            continue
        ya, yb = raw[a]["points"][0][1], raw[b]["points"][0][1]
        x0, x1 = raw[a]["points"][0][0], raw[a]["points"][1][0]
        sigs = [raw_entity_sig(raw[a]), raw_entity_sig(raw[b])]
        elements["wall"].append({
            "eid": element_eid("w", sigs),          # ← 원본 좌표 기반, width 무관
            "kind": "polyline",
            "centerline": [[x0, (ya + yb) / 2.0], [x1, (ya + yb) / 2.0]],
            "width_detected": yb - ya,
            "render_width": wall_width,             # 사용자가 바꾸는 값
            "needs_review": False,
        })
    return elements


def _edits():
    eid = parse(RAW, 200)["wall"][0]["eid"]
    return eid, {eid: {"overrides": {"width": 150}, "review_resolved": True}}


def test_eid_survives_a_width_change():
    eid, edits = _edits()
    re_parsed = parse(RAW, 150)                      # 폭이 달라져 중심선이 이동해도
    assert re_parsed["wall"][0]["eid"] == eid
    report = apply_edits(re_parsed, edits)
    assert re_parsed["wall"][0]["overrides"] == {"width": 150}
    assert not report["orphaned"]


def test_grouping_change_orphans_the_edit_instead_of_dropping_it():
    """벽1 의 한 면이 사라져 페어링이 깨지면 EID 가 바뀐다.
    그때 수정을 조용히 버리지 않고 '검토 필요' 로 띄우는 것이 계약이다."""
    eid, edits = _edits()
    changed = parse({k: v for k, v in RAW.items() if k != "wall1_faceB"}, 200)
    report = apply_edits(changed, edits)
    assert eid in report["orphaned"]
    assert suggest_relink(report["orphaned"], edits, changed), "재연결 후보를 제안하지 않았다"


# ── 한 면선 쌍이 N개로 잘릴 때 (실측: 벽 682개 중 357개가 이 상태였다) ──────
def _split_records(n, sigs):
    """같은 면선 쌍에서 나온 N개 세그먼트. `_sigs` 는 전부 같고 구간만 다르다."""
    import dxf_parser as dp
    out = []
    for i in range(n):
        pts = [[i * 1000, 0], [(i + 1) * 1000, 0]]
        out.append({"eid": element_eid("w", sigs + [dp._span_sig(pts)]),
                    "kind": "polyline", "points": pts, "needs_review": False})
    return {"wall": out}


def test_split_segments_get_distinct_eids():
    """★ 이게 깨지면 사용자가 A 를 고쳤는데 B 가 바뀐다.

    긴 면선 한 쌍이 여러 세그먼트로 잘리면 `_sigs` 가 전부 같아 EID 도 같아졌다.
    실측(지하3층): 벽 682개 / 고유 EID 419 — 357개(52%)가 다른 벽과 ID 를 공유했고,
    `apply_edits` 의 dict 에서 마지막 하나만 남아 임의의 벽에 적용됐다."""
    sigs = [raw_entity_sig(RAW["wall1_faceA"]), raw_entity_sig(RAW["wall1_faceB"])]
    eids = [r["eid"] for r in _split_records(3, sigs)["wall"]]
    assert len(set(eids)) == 3, f"3분할인데 고유 EID {len(set(eids))}개: {eids}"


def test_span_does_not_move_with_declared_width():
    """구간은 **원본 면선 좌표**에서만 나온다 — 선언 폭이 바뀌어도 그대로여야 한다.
    (실측으로도 확인: 폭 200→400 재파싱에서 벽 EID 672개 전부 유지, 사라짐 0.)"""
    import dxf_parser as dp
    assert dp._span_sig([[0, 0], [1000, 0]]) == dp._span_sig([[0, 0], [1000, 0]])
    assert dp._span_sig([[0, 0], [1000, 0]]) == dp._span_sig([[1000, 0], [0, 0]])  # 방향 무관
    assert dp._span_sig([[0, 0], [1000, 0]]) != dp._span_sig([[1000, 0], [2000, 0]])


def test_ambiguous_eid_is_reported_not_silently_applied():
    """좌표까지 같은 진짜 중복 벽은 ID 를 공유하는 게 옳다. 다만 그때 수정을
    **아무 쪽에나 걸면 안 된다** — 종전에는 dict 라 마지막 것이 조용히 이겼다."""
    rec = {"eid": "w:dead", "kind": "polyline", "points": [[0, 0], [1, 0]]}
    els = {"wall": [dict(rec), dict(rec)]}
    rep = apply_edits(els, {"w:dead": {"overrides": {"width": 999}}})
    assert rep["ambiguous"] == ["w:dead"], rep
    assert rep["applied"] == [] and rep["orphaned"] == ["w:dead"], rep
    assert all("overrides" not in r for r in els["wall"]), "모호한데 적용해 버렸다"


def test_old_edits_are_carried_over_through_eid_v1():
    """EID 공식이 바뀌어도 **모호하지 않았던** 옛 수정은 이어받는다.
    모호했던 것(옛 ID 를 여러 벽이 공유)은 어느 벽 것인지 파일에 없으므로 고아다."""
    els = {"wall": [
        {"eid": "w:new1", "eid_v1": "w:old1", "kind": "polyline", "points": [[0, 0], [1, 0]]},
        {"eid": "w:new2", "eid_v1": "w:dupe", "kind": "polyline", "points": [[2, 0], [3, 0]]},
        {"eid": "w:new3", "eid_v1": "w:dupe", "kind": "polyline", "points": [[4, 0], [5, 0]]},
    ]}
    rep = apply_edits(els, {"w:old1": {"overrides": {"width": 150}},
                            "w:dupe": {"overrides": {"width": 150}}})
    assert rep["migrated"] == ["w:old1"], rep
    assert rep["orphaned"] == ["w:dupe"], rep
    assert els["wall"][0]["overrides"] == {"width": 150}
    assert all("overrides" not in r for r in els["wall"][1:]), "모호한 옛 ID 를 적용했다"
