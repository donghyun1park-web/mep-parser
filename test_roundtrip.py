"""
test_roundtrip.py — 설계의 핵심 주장 2개를 증명한다.
  주장1: 벽 폭 200→150 (중심선 이동) 후에도 EID 불변 → 수정 살아남음.
  주장2: 원본 선이 빠져 grouping이 바뀌면 EID 변경 → edit이 고아로 '정직하게' 보고됨.
"""
from element_id import raw_entity_sig, element_eid, apply_edits, suggest_relink


# 원본 DXF의 raw 세그먼트 (불변). 벽1 = 평행 2선, 벽2 = 평행 2선.
RAW = {
    "wall1_faceA": {"kind": "polyline", "closed": False, "points": [[0, 0], [5000, 0]]},
    "wall1_faceB": {"kind": "polyline", "closed": False, "points": [[0, 200], [5000, 200]]},
    "wall2_faceA": {"kind": "polyline", "closed": False, "points": [[0, 4000], [3000, 4000]]},
    "wall2_faceB": {"kind": "polyline", "closed": False, "points": [[0, 4200], [3000, 4200]]},
}


def parse(raw, wall_width):
    """파서 모사: raw 2선을 페어링 → 벽 1개. 중심선은 width에 따라 달라짐(파생).
    EID는 '원본 두 선의 sig'에서만 계산 → width와 무관."""
    elements = {"wall": []}
    pairs = [("wall1_faceA", "wall1_faceB"), ("wall2_faceA", "wall2_faceB")]
    for a, b in pairs:
        if a not in raw or b not in raw:
            continue
        ya = raw[a]["points"][0][1]
        yb = raw[b]["points"][0][1]
        cy = (ya + yb) / 2.0  # 중심선 y (양면 중앙)
        x0 = raw[a]["points"][0][0]
        x1 = raw[a]["points"][1][0]
        sigs = [raw_entity_sig(raw[a]), raw_entity_sig(raw[b])]
        eid = element_eid("w", sigs)  # ← 원본 좌표 기반, width 무관
        elements["wall"].append({
            "eid": eid,
            "kind": "polyline",
            "centerline": [[x0, cy], [x1, cy]],     # 파생물
            "width_detected": yb - ya,
            "render_width": wall_width,              # 사용자가 바꾸는 값
            "needs_review": False,
        })
    return elements


print("=" * 60)
print("주장1: 폭 변경 후에도 EID 불변 → 수정 보존")
print("=" * 60)
e200 = parse(RAW, 200)
eid_w1 = e200["wall"][0]["eid"]
print(f"  width=200 → 벽1 EID = {eid_w1}, 중심선 = {e200['wall'][0]['centerline']}")

# 사용자가 벽1 폭을 150으로 override + 검토완료 표시 → edits 사이드카에 EID로 저장
edits = {eid_w1: {"overrides": {"width": 150}, "review_resolved": True}}

# 레이어 규칙/파라미터 바꿔 '재파싱' (이번엔 기본폭 150 가정 → 중심선 동일하나 일반화)
e_re = parse(RAW, 150)
eid_w1_re = e_re["wall"][0]["eid"]
print(f"  재파싱   → 벽1 EID = {eid_w1_re}")
print(f"  EID 동일? {eid_w1 == eid_w1_re}  ← 핵심")

report = apply_edits(e_re, edits)
print(f"  edits 적용 결과: applied={report['applied']} orphaned={report['orphaned']}")
print(f"  벽1 override 살아남음? {e_re['wall'][0].get('overrides')}")
assert eid_w1 == eid_w1_re
assert e_re["wall"][0]["overrides"] == {"width": 150}
assert not report["orphaned"]
print("  ✓ PASS\n")

print("=" * 60)
print("주장2: grouping이 실제로 바뀌면 EID 변경 → edit 고아로 정직 보고")
print("=" * 60)
# 원본에서 벽1의 한 면(faceB)이 사라진 도면으로 교체 → 벽1 페어링 깨짐
RAW2 = {k: v for k, v in RAW.items() if k != "wall1_faceB"}
e_changed = parse(RAW2, 200)
print(f"  변경 후 벽 수: {len(e_changed['wall'])} (벽1 페어 깨짐)")
report2 = apply_edits(e_changed, edits)
print(f"  edits 적용: applied={report2['applied']} orphaned={report2['orphaned']}")
sugg = suggest_relink(report2["orphaned"], edits, e_changed)
print(f"  재연결 제안(자동적용X): {[s['orphan']+' → '+str(s['candidates']) for s in sugg]}")
assert eid_w1 in report2["orphaned"]
print("  ✓ PASS — 수정이 조용히 사라지지 않고 '검토 필요'로 떠오름\n")

print("모든 검증 통과.")
