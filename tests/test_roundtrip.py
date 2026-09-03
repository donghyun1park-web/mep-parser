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
