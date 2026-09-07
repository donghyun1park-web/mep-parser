# -*- coding: utf-8 -*-
"""실측 두께 vs layer_map 선언 두께 교차검증.

layer_map 의 `width` 는 **레이어 하나에 한 번 적는 기본값**이고 `width_detected`
는 그 벽에서 실제로 잰 값이다. `geom_contract.width_of` 는 선언값을 먼저 쓰므로,
둘이 어긋나면 실측값이 조용히 버려진다.

실측(지하3층 건축평면): 벽 280개가 200mm 로 선언됐는데 도면에서는 250·300·400·
450mm 로 재졌다. 전부 200mm 로 세워지고 있었고 아무도 그 말을 듣지 못했다.

여기서 고치는 것은 '어느 쪽을 쓰는가' 가 아니라 '말을 하는가' 다 — 적어 준 값이
이기는 것은 stack 레벨 height 와 같은 규약이므로 유지한다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dxf_parser as dp
import geom_contract as GC


def _wall(detected, declared, pairing="paired", reason=None):
    r = {"kind": "polyline", "closed": False, "layer": "A-CON",
         "points": [[0, 0], [3000, 0]], "centerline": [[0, 0], [3000, 0]],
         "width_detected": detected, "pairing": pairing, "z_base": 0.0,
         "overrides": {"width": declared, "height": 2800.0}}
    if reason:
        r["needs_review"], r["review_reason"] = True, reason
    return r


def _conflicts(walls):
    """parse() 안의 교차검증 블록과 같은 판정을 레코드 목록에 적용한다."""
    out = []
    for w in walls:
        if w.get("pairing") != "paired" or w.get("review_reason") == "thin_pair":
            continue
        d, o = w.get("width_detected"), (w.get("overrides") or {}).get("width")
        if not d or o is None:
            continue
        if abs(float(d) - float(o)) > max(dp.WIDTH_CONFLICT_ABS_MM,
                                          float(o) * dp.WIDTH_CONFLICT_REL):
            out.append(w)
    return out


def test_declared_width_still_wins_the_build():
    """★ 이 테스트가 깨지면 산출물의 벽 두께가 통째로 바뀐 것이다.
    실측을 쓰도록 뒤집는 것은 의도적인 결정이어야지 부작용이면 안 된다."""
    assert GC.width_of(_wall(450.0, 200.0), None, "wall") == 200.0
    # 선언이 없을 때만 실측이 쓰인다
    assert GC.width_of({"width_detected": 450.0}, None, "wall") == 450.0


def test_real_disagreement_is_reported():
    """200 선언 / 450 실측 = 도면에 레이어 기본값과 다른 두께의 벽이 있다는 뜻."""
    assert len(_conflicts([_wall(450.0, 200.0)])) == 1


def test_small_difference_is_not_reported():
    """도면 오차·마감선 두께까지 불일치로 세면 경고가 무의미해진다."""
    assert _conflicts([_wall(210.0, 200.0)]) == []      # 절대 20mm 이내
    assert _conflicts([_wall(1150.0, 1000.0)]) == []    # 상대 15% 이내


def test_untrustworthy_measurements_are_excluded():
    """single_offset 은 중심선 자체가 추정이라 두께도 추정이고,
    thin_pair 는 이미 '잘못 잰 값' 으로 걸러진 것이다 — 두 번 보고하지 않는다."""
    assert _conflicts([_wall(450.0, 200.0, pairing="single_offset")]) == []
    assert _conflicts([_wall(50.0, 200.0, reason="thin_pair")]) == []
