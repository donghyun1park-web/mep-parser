# -*- coding: utf-8 -*-
"""layer_map.csv 로딩/분류 위생 테스트.

실제로 물렸던 함정을 고정한다:
  · '배수판_벽체' 가 '벽' 부분문자열 때문에 'WALL|벽|CON' 에 선점되어
    ignore 규칙이 한 번도 작동하지 않았고, 배수판이 200mm 벽으로 모델링됐다.
  · 주석 필터가 '# '(샵+공백)라서 '#chk_...' 가 살아있는 규칙으로 파싱됐다.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dxf_parser as dp


def write_csv(text):
    p = os.path.join(tempfile.gettempdir(), "_lm_test.csv")
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


HEAD = "pattern,category,width,height,thickness\n"


# ── 주석 처리 ──────────────────────────────────────────────────────────────
def test_hash_comment_is_skipped_without_space():
    """'# ' 가 아니라 '#' 만으로도 걸러져야 한다."""
    r = dp.load_layer_map(write_csv(HEAD + "#chk_U_250212,column,,,\nWALL,wall,200,2800,\n"))
    assert len(r) == 1 and r[0][0] == "WALL", f"주석이 규칙으로 살아남았다: {r}"


def test_indented_comment_is_skipped():
    r = dp.load_layer_map(write_csv(HEAD + "   # 들여쓴 주석,column,,,\nWALL,wall,,,\n"))
    assert len(r) == 1


def test_blank_pattern_is_skipped():
    r = dp.load_layer_map(write_csv(HEAD + ",wall,,,\nWALL,wall,,,\n"))
    assert len(r) == 1


# ── 유효성 검증 ────────────────────────────────────────────────────────────
def test_unknown_category_raises():
    """오타가 조용히 새 버킷을 만드는 것을 막는다."""
    try:
        dp.load_layer_map(write_csv(HEAD + "FOO,walll,,,\n"))
    except dp.LayerMapError as e:
        assert "walll" in str(e)
        return
    raise AssertionError("오타 카테고리가 통과했다")


def test_bad_regex_raises():
    try:
        dp.load_layer_map(write_csv(HEAD + "A[unclosed,wall,,,\n"))
    except dp.LayerMapError:
        return
    raise AssertionError("깨진 정규식이 통과했다")


def test_non_numeric_dimension_raises():
    try:
        dp.load_layer_map(write_csv(HEAD + "WALL,wall,이백,,\n"))
    except dp.LayerMapError:
        return
    raise AssertionError("숫자 아닌 치수가 통과했다")


def test_ignore_is_valid_category():
    r = dp.load_layer_map(write_csv(HEAD + "배수판,ignore,,,\n"))
    assert r[0][1] == "ignore"


def test_beam_is_valid_category():
    r = dp.load_layer_map(write_csv(HEAD + "00-보,beam,,,\n"))
    assert r[0][1] == "beam"


# ── 그림자 규칙 탐지 ───────────────────────────────────────────────────────
def test_shadowed_rule_detected():
    """배수판 사고 그대로 재현: 넓은 규칙이 위, 제외 규칙이 아래."""
    rules = dp.load_layer_map(write_csv(
        HEAD + "WALL|벽|CON,wall,200,2800,\n배수판_벽체|배수판,ignore,,,\n"))
    hits = set()
    cat, _ = dp.classify("배수판_벽체", rules, hits)
    assert cat == "wall", "재현 실패 — 이 순서면 배수판이 벽으로 잡혀야 한다"
    sh = dp.shadowed_rules(rules, hits, {"배수판_벽체"})
    assert sh and sh[0]["category"] == "ignore", f"가려진 규칙을 못 잡았다: {sh}"
    assert sh[0]["shadowed_by"][0]["by_category"] == "wall"


def test_correct_order_has_no_shadow():
    """제외 규칙을 위로 올리면 경고가 사라져야 한다(오탐 방지)."""
    rules = dp.load_layer_map(write_csv(
        HEAD + "배수판_벽체|배수판,ignore,,,\nWALL|벽|CON,wall,200,2800,\n"))
    hits = set()
    assert dp.classify("배수판_벽체", rules, hits)[0] == "ignore"
    dp.classify("WALL-1", rules, hits)
    assert dp.shadowed_rules(rules, hits, {"배수판_벽체", "WALL-1"}) == []


def test_unused_rule_for_absent_layer_is_not_reported():
    """그 도면에 없는 레이어의 규칙까지 경고하면 노이즈가 된다."""
    rules = dp.load_layer_map(write_csv(HEAD + "WALL,wall,,,\nDUCT,duct,,,\n"))
    hits = set()
    dp.classify("WALL-1", rules, hits)
    assert dp.shadowed_rules(rules, hits, {"WALL-1"}) == []


# ── 실제 저장소 CSV ────────────────────────────────────────────────────────
def test_repo_layer_map_loads_and_has_no_shadow():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rules = dp.load_layer_map(os.path.join(root, "layer_map.csv"))
    assert rules, "layer_map.csv 가 비었다"
    hits = set()
    layers = ["배수판_벽체", "배수판", "A-CEN", "A-HAT", "WALL-1", "S_RC-CON",
              "COL-1", "SLAB-1", "DOOR-1", "PIPE-1", "DUCT-1", "TRAY-1",
              "A-ELE", "A-INSUL", "A-STEEL", "OPEN"]
    got = {L: dp.classify(L, rules, hits)[0] for L in layers}
    assert got["배수판_벽체"] == "ignore", f"배수판 회귀! {got['배수판_벽체']}"
    assert got["WALL-1"] == "wall" and got["S_RC-CON"] == "wall"
    sh = dp.shadowed_rules(rules, hits, set(layers))
    assert sh == [], f"저장소 layer_map.csv 에 가려진 규칙이 있다: {sh}"
