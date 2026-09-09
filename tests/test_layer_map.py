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


def test_default_rules_and_repo_csv_agree_on_open_layers():
    """`-m` 유무로 판정이 달라지면 그게 다음 버그다.

    버리는 것과 살리는 것이 이름으로 갈린다: 'OPEN'(공백부 X 표시선)·
    'DEFPOINT'(치수 정의점)는 버리고, 'A-OPENING'(진짜 개구부)은 살린다.
    'A-DEFPOINT-XX' 는 남의 레이어라 삼키지 않는다(미매핑으로 드러나야 한다).
    두 경로가 같은 답을 내야 한다."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    csv_rules = dp.load_layer_map(os.path.join(root, "layer_map.csv"))
    for layer, want in (("OPEN", "ignore"), ("A-OPENING", "opening"),
                        ("OPENING", "opening"), ("DOOR-1", "opening"),
                        ("DEFPOINT", "ignore"), ("Defpoints", "ignore"),
                        ("A-DEFPOINT-XX", None)):
        got_csv = dp.classify(layer, csv_rules, set())[0]
        got_def = dp.classify(layer, dp.DEFAULT_LAYER_RULES, set())[0]
        assert got_csv == want, f"layer_map.csv: {layer} -> {got_csv}"
        assert got_def == want, f"DEFAULT_LAYER_RULES: {layer} -> {got_def}"


# ── opts 컬럼 (레이어별 튜닝) ──────────────────────────────────────────────
def test_opts_parsed():
    r = dp.load_layer_map(write_csv(
        "pattern,category,width,height,thickness,opts\n"
        "AU_GIRDER,beam,,,,pair_max=1800;from=dim;schedule=BEAM_SCHEDULE\n"))
    o = r[0][2]["_opts"]
    assert o == {"pair_max": 1800.0, "from": "dim", "schedule": "BEAM_SCHEDULE"}


def test_opts_column_is_optional():
    """opts 없는 기존 5컬럼 CSV 가 그대로 동작해야 한다(하위호환)."""
    r = dp.load_layer_map(write_csv(HEAD + "WALL,wall,200,2800,\n"))
    assert r[0][2] == {"width": 200.0, "height": 2800.0}
    assert "_opts" not in r[0][2]


def test_unknown_opt_key_raises():
    """오타난 허용치를 조용히 무시하면 안 된다."""
    try:
        dp.load_layer_map(write_csv(
            "pattern,category,width,height,thickness,opts\nX,wall,,,,pairmax=1800\n"))
    except dp.LayerMapError as e:
        assert "pairmax" in str(e)
        return
    raise AssertionError("오타 opts 키가 통과했다")


def test_non_numeric_opt_raises():
    try:
        dp.load_layer_map(write_csv(
            "pattern,category,width,height,thickness,opts\nX,wall,,,,pair_max=넓게\n"))
    except dp.LayerMapError:
        return
    raise AssertionError("숫자 아닌 opts 값이 통과했다")


def test_opts_do_not_leak_into_overrides():
    """_opts 는 파서 전용. 빌더가 읽는 overrides 로 새면 안 된다."""
    attrs = {"width": 200.0, "_opts": {"pair_max": 1800.0}}
    assert dp._pub_attrs(attrs) == {"width": 200.0}


def test_material_reaches_the_builder_through_overrides():
    """★ 재질만은 예외로 overrides 를 탄다 — 빌더가 읽어야 하기 때문이다.

    최상위 필드로 만들면 벽의 병합·페어링·체이닝을 지나며 조용히 사라진다
    (그 경로가 보존하는 것은 overrides 다). 나머지 opts 는 파서 전용 그대로다."""
    HEAD6 = "pattern,category,width,height,thickness,opts\n"
    r = dp.load_layer_map(write_csv(
        HEAD6 + "WALL,wall,200,2800,,material=콘크리트;pair_max=600\n"))
    attrs = r[0][2]
    assert attrs["_opts"]["material"] == "콘크리트"
    pub = dp._pub_attrs(attrs)
    assert pub == {"width": 200.0, "height": 2800.0, "material": "콘크리트"}, pub


def test_material_is_never_guessed_from_category():
    """적힌 것만 붙는다. wall→콘크리트 추정은 조적벽에서 바로 틀린다."""
    r = dp.load_layer_map(write_csv(HEAD + "WALL,wall,200,2800,\n"))
    assert "material" not in dp._pub_attrs(r[0][2])


def test_pair_bounds_takes_min_across_layers():
    """교차 레이어 쌍은 min — 양쪽 모두 허용해야 넓힌다.
    안 그러면 느슨한 보 레이어가 옆 벽선을 빨아들인다."""
    loose = {"opts": {"pair_max": 2500.0}}
    tight = {"opts": {}}
    lo, hi = dp._pair_bounds(loose, tight)
    assert hi == dp.WALL_PAIR_MAX_MM, f"교차 쌍이 느슨한 쪽을 따라갔다: {hi}"
    lo2, hi2 = dp._pair_bounds(loose, loose)
    assert hi2 == 2500.0, "같은 레이어끼리인데 안 넓어졌다"


# ── from=dim (DIMENSION 을 부재로) ─────────────────────────────────────────
class _FakeDim:
    """DIMENSION 최소 스텁 — ezdxf 없이 핸들러 로직만 검증."""
    class _P:
        def __init__(s, x, y): s.x, s.y = x, y

    class _D:
        pass

    def __init__(self, text, p2=(0, 0), p3=(1000, 0), dimtype=32, layer="G"):
        self.dxf = self._D()
        self.dxf.text = text
        self.dxf.dimtype = dimtype
        self.dxf.layer = layer
        self.dxf.defpoint2 = self._P(*p2)
        self.dxf.defpoint3 = self._P(*p3)
        self.dxf.defpoint = self._P(p3[0], p3[1] + 1900)   # 치수선 위치(측정점 아님)

    def dxftype(self):
        return "DIMENSION"


def test_dim_member_extracted_from_defpoint2_3():
    """defpoint(10)는 치수선 위치이지 측정점이 아니다 — defpoint2(13)→defpoint3(14)."""
    r = dp.entity_to_record(_FakeDim("RAG11B", (100, 200), (5100, 200)), 1.0, {"from": "dim"})
    assert r is not None
    assert r["points"] == [[100, 200], [5100, 200]], r["points"]
    assert r["member_name"] == "RAG11B" and r["source"] == "dimension"


def test_dim_ignored_without_optin():
    """opts 에 from=dim 이 없으면 DIMENSION 은 예전처럼 무시된다(다른 도면 영향 없음)."""
    assert dp.entity_to_record(_FakeDim("RAG11B"), 1.0, None) is None
    assert dp.entity_to_record(_FakeDim("RAG11B"), 1.0, {}) is None


def test_real_dimensions_are_not_members():
    """빈 텍스트/'<>'/순수 숫자는 진짜 치수선 — 부재로 만들면 안 된다."""
    for t in ("", "<>", "15,000", " 3600 ", "2,400x1,200", "1.5"):
        assert dp.entity_to_record(_FakeDim(t), 1.0, {"from": "dim"}) is None, f"{t!r} 가 부재가 됐다"


def test_non_linear_dimtype_rejected():
    """각도·반경 치수는 부재 축선이 아니다."""
    assert dp.entity_to_record(_FakeDim("X1", dimtype=2), 1.0, {"from": "dim"}) is None


def test_zero_length_dim_rejected():
    assert dp.entity_to_record(_FakeDim("X1", (0, 0), (0, 0)), 1.0, {"from": "dim"}) is None


def test_member_re_narrows_selection():
    """일반 가드로는 부재명과 상세도 기호를 구분 못 한다 — member_re 로 좁힌다."""
    o = {"from": "dim", "member_re": r"^R[AS][BG]"}
    assert dp.entity_to_record(_FakeDim("RAB1D"), 1.0, o) is not None
    assert dp.entity_to_record(_FakeDim("Hu1"), 1.0, o) is None
    assert dp.entity_to_record(_FakeDim("Lt"), 1.0, o) is None


# ── 헤더/컬럼 정합 ─────────────────────────────────────────────────────────
def test_extra_field_without_header_column_is_rejected():
    """배포된 layer_map.csv 헤더가 5컬럼이라 opts 를 적어도 조용히 버려졌다.

    DictReader 는 헤더보다 많은 필드를 None 키에 담고 넘어간다 — opts 설계가
    막으려던 바로 그 '조용한 무시'가 파일 헤더 쪽에 남아 있었다.
    """
    p = write_csv("pattern,category,width,height,thickness\n"
                  "^A-CON$,wall,200,2800,,pair_min=100\n")
    try:
        dp.load_layer_map(p)
    except dp.LayerMapError as e:
        assert "헤더" in str(e), e
        return
    raise AssertionError("헤더에 없는 컬럼이 조용히 통과했다")


def test_opts_reach_the_rule_when_the_header_has_the_column():
    p = write_csv("pattern,category,width,height,thickness,opts\n"
                  "^A-CON$,wall,200,2800,,pair_min=100;pair_max=600\n")
    _pat, _cat, attrs = dp.load_layer_map(p)[0]
    assert attrs["_opts"] == {"pair_min": 100.0, "pair_max": 600.0}


def test_shipped_layer_map_has_the_opts_column():
    """저장소가 배포하는 파일이 그 함정에 다시 빠지지 않게 고정한다."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "layer_map.csv"), encoding="utf-8") as f:
        header = f.readline().strip()
    assert header.endswith(",opts"), header


# ── 호 근사 오차 ───────────────────────────────────────────────────────────
def test_arc_chord_error_is_bounded_in_mm_not_degrees():
    """고정 각도 밀도는 반지름이 커질수록 조용히 더 틀린다.

    실측: r=13,040 곡선벽의 두께 500mm 가 476mm 로 측정됐다(사지타 27mm).
    오차를 mm 로 묶으면 반지름과 무관하게 일정하다."""
    import math
    for r in (500.0, 6000.0, 13040.0, 50000.0):
        span = math.radians(103.5)
        n = dp._arc_segments(r, span)
        sagitta = r * (1 - math.cos((span / n) / 2))
        assert sagitta <= dp.ARC_CHORD_TOL_MM + 1e-6 or n == dp.ARC_MAX_SEGS, \
            f"r={r}: n={n} 사지타={sagitta:.2f}mm"


def test_arc_segments_never_explodes_or_degenerates():
    import math
    assert dp._arc_segments(1e9, math.pi) == dp.ARC_MAX_SEGS      # 상한
    assert dp._arc_segments(0.0, 0.01) >= 2                       # 하한
    assert dp._arc_segments(100.0, math.radians(5)) >= 2


# ── centerline= (환기평면도: 덕트가 외곽선 2줄 + 중심선 1줄) ────────────────
class _E:
    """색·선종류만 보는 최소 더미 — ezdxf 없이 판정기만 고정한다."""
    def __init__(self, color=256, linetype="BYLAYER", t="LINE"):
        self._d = {"color": color, "linetype": linetype}
        self._t = t
    def dxftype(self):
        return self._t
    @property
    def dxf(self):
        return self
    def get(self, k, default=None):
        return self._d.get(k, default)


def test_centerline_color_keeps_only_the_marked_line():
    """외곽선을 안 거르면 한 덕트가 3중으로 계상된다(실측 179개 → 45개)."""
    o = {"centerline": "color:1"}
    assert dp._is_not_centerline(_E(color=1), o) is False        # 중심선
    assert dp._is_not_centerline(_E(color=256), o) is True       # BYLAYER = 외곽선
    assert dp._is_not_centerline(_E(color=0), o) is True         # BYBLOCK = 외곽선
    assert dp._is_not_centerline(_E(color=1), {}) is False       # 옵션 없으면 안 거른다


def test_centerline_linetype_form():
    o = {"centerline": "linetype:CENTER"}
    assert dp._is_not_centerline(_E(linetype="CENTER"), o) is False
    assert dp._is_not_centerline(_E(linetype="center"), o) is False   # 대소문자 무시
    assert dp._is_not_centerline(_E(linetype="BYLAYER"), o) is True


def test_centerline_bad_spec_fails_at_load_not_silently():
    """오타난 판정 기준을 조용히 안 먹으면 외곽선이 그대로 부재가 된다."""
    for bad in ("colour=1", "1", "colour:1"):
        try:
            dp._parse_opts(f"centerline={bad}")
        except dp.LayerMapError:
            continue
        raise AssertionError(f"centerline={bad!r} 가 통과했다")
    assert dp._parse_opts("centerline=color:1") == {"centerline": "color:1"}
