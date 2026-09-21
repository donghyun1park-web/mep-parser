# -*- coding: utf-8 -*-
"""시공기준 규칙 표 — geometry.json 을 시공기준(KCS/KDS/NFTC) 과 대조한다.

CLAUDE.md '규칙 표 모듈' 참조. FreeCAD·shapely 의존 없음(단위테스트 가능).

원칙: **표에 없는 숫자는 코드에 없다.** `RULES` 의 모든 행은 출처(standard·clause·url·
jurisdiction·source_kind)와 검증 판정(verdict·retrieved)을 가진다. `verdict != "confirmed"`
인 규칙은 `BACKLOG` 에만 있고 `review()`/`supports()` 가 적용하지 않는다.

평면도에는 구배·유향·보온·지지·재질·호칭지름(DN)이 없다 — 전부 **선언**이 필요하다.
선언이 없으면 규칙은 건너뛰고 `receipt.skipped`/`declarations_missing` 으로 센다.
외경→DN 역산, 계통 이름→용도 추정, 카테고리→재질 추정은 하지 않는다(KS D 3507 은
`BACKLOG` 의 `plausible` 항목일 뿐 — 실제 규격표는 원문 유료라 확인하지 못했다).

경위는 docs/decisions/construction-rules.md.
"""
import math
import re

import geom_contract as GC

RETRIEVED = "2026-09-17"

# layer_map.csv opts `service=` · MEP 프로필 `service` 가 받는 값. 계통 이름(SA/RA 등)은
# 해석하지 않는다 — 자유 문자열로 사람이 붙인 이름과 용도는 다른 것이다.
SERVICES = (
    'drain', 'vent', 'storm', 'domestic_cold', 'domestic_hot', 'chilled_hot_water',
    'cooling_water', 'steam', 'sprinkler_branch', 'sprinkler_main',
    'hydrant_branch', 'hydrant_riser', 'supply_air', 'return_air', 'exhaust_air', 'smoke_control',
)

# 자유 문자열 재질 선언 → 정규 키(소문자 매칭). 미매칭은 skip 사유에 원문을 싣는다.
_MATERIAL_ALIASES = {
    '강관': 'steel', 'steel': 'steel', 'sgp': 'steel', 'carbon steel': 'steel',
    '동관': 'copper', 'copper': 'copper',
    '스테인리스강관': 'stainless', 'sts': 'stainless', 'stainless': 'stainless', 'stainless steel': 'stainless',
    '경질염화비닐관': 'pvc', 'pvc': 'pvc', 'upvc': 'pvc',
    '아연도금강판': 'galvanized_sheet', '아연도금강판제': 'galvanized_sheet',
    'galvanized': 'galvanized_sheet', 'galvanized_sheet': 'galvanized_sheet', 'galvanized steel': 'galvanized_sheet',
}


def material_key(text):
    """자유 문자열 재질 선언 → 정규 키. 못 알아들으면 None — 원문은 호출측이 skip 사유에 싣는다."""
    return _MATERIAL_ALIASES.get(str(text or '').strip().lower())


def _material_of(rec):
    """레코드의 재질 선언 — `overrides.material`(layer_map `opts: material=` 또는 편집)을 먼저
    본다: 최상위 `material` 은 프로젝트 기본값(project_default)일 수 있고, 기본값이 나중에 온
    편집을 가리면 안 된다(freecad_builder.set_ifc_props 와 같은 순서). MEP 프로필은 행이 선언한
    값을 최상위·overrides 양쪽에 싣는다(_assign)."""
    return (rec.get('overrides') or {}).get('material') or rec.get('material')


_DN_RE = re.compile(r'(\d+(?:\.\d+)?)\s*(?:A|mm)?\s*$', re.I)


def parse_dn(nominal_size):
    """`DN100`·`100A`·`PB15A`·`100mm` → 호칭지름(숫자, mm). 못 읽으면 None.

    외경(diameter)을 DN 으로 역산하지 않는다 — DN100 강관의 실제 외경은 114.3mm 다
    (KS D 3507, `BACKLOG` 의 plausible 항목). 호칭은 선언에서만 온다."""
    if nominal_size is None:
        return None
    m = _DN_RE.search(str(nominal_size).strip())
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _band(rows, dn):
    """(상한 mm 포함, 값) 오름차순 목록에서 dn 이 속하는 값. 마지막 항목 상한 None = 그 이상 전부."""
    for hi, value in rows:
        if hi is None or dn <= hi:
            return value
    return None


def _count_turns_over(points, deg):
    """3D 점열에서 연속 두 구간의 방향 전환각이 `deg` 를 **넘는**(초과, 이상 아님) 꼭짓점 수."""
    limit = math.cos(math.radians(deg))
    n = 0
    for i in range(1, len(points) - 1):
        a = [points[i][k] - points[i - 1][k] for k in range(3)]
        b = [points[i + 1][k] - points[i][k] for k in range(3)]
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        if na < 1e-9 or nb < 1e-9:
            continue
        cos_ang = max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b)) / (na * nb)))
        if cos_ang < limit:
            n += 1
    return n


# ── 규칙 표 — verdict=confirmed 만. 미러(source_kind=mirror)에서 읽은 것도 사용자 확인대로 기본 적용한다 ──
RULES = [
    {"id": "duct-aspect-ratio", "hook": "check", "categories": ("duct",),
     "standard": "KCS 31 20 20 덕트설비공사", "clause": "3.2.1(2)② 표 3.2-2 주3",
     "url": "https://nogada.kwoody01.com/423", "jurisdiction": "KR", "source_kind": "mirror",
     "verdict": "confirmed", "retrieved": RETRIEVED,
     "correction": "공판공법·CB덕트 한정 — 모든 제작 공법에 일반화하지 않는다",
     "needs": (), "max_ratio": 4.0},

    {"id": "pipe-support-spacing-horizontal", "hook": "boq", "categories": ("pipe",),
     "standard": "KCS 31 20 15 배관설비공사(2025)", "clause": "3.4 지지 및 고정 표 3.4-1",
     "url": "https://nogada.kwoody01.com/422", "jurisdiction": "KR", "source_kind": "primary",
     "verdict": "confirmed", "retrieved": RETRIEVED, "correction": None,
     "needs": ("dn", "material"),
     "bands": {
         "steel":      [(20, 1.8), (40, 2.0), (80, 3.0), (150, 4.0), (None, 5.0)],
         "copper":     [(20, 1.0), (40, 1.5), (50, 2.0), (100, 2.5), (None, 3.0)],
         "stainless":  [(20, 1.0), (40, 1.5), (50, 2.0), (100, 2.5), (None, 3.0)],
         "pvc":        [(16, 0.75), (40, 1.0), (50, 1.2), (125, 1.5), (None, 2.0)],
     }},

    {"id": "duct-hanger-spacing", "hook": "boq", "categories": ("duct",),
     "standard": "KCS 31 20 20 덕트설비공사", "clause": "3.2 표 3.2-20(장방형)·표 3.2-21(스파이럴)",
     "url": "https://nogada.kwoody01.com/423", "jurisdiction": "KR", "source_kind": "mirror",
     "verdict": "confirmed", "retrieved": RETRIEVED,
     "correction": "아연도금강판제 한정 — 스테인리스(표 3.2-28)·PVC(표 3.2-36)는 별도 표(미수록)",
     "needs": ("material",), "rect_spacing_m": 3.68, "round_max_dn": 1250.0, "round_spacing_m": 3.0},

    {"id": "drain-cleanout-spacing", "hook": "boq", "categories": ("pipe",),
     "standard": "KCS 31 30 25 배수통기설비공사", "clause": "3.3.1 청소구 설치 개소",
     "url": "https://nogada.kwoody01.com/447", "jurisdiction": "KR", "source_kind": "mirror",
     "verdict": "confirmed", "retrieved": RETRIEVED,
     "correction": "'45도를 넘는' 방향전환부만 — 정확히 45도는 해당 없음",
     "needs": ("dn", "service"), "bands": [(100, 15.0), (None, 30.0)], "turn_deg": 45.0},

    {"id": "drain-slope-by-diameter", "hook": "info", "categories": ("pipe",),
     "standard": "KDS 31 30 25 배수통기설비 설계기준 / KCS 31 30 25",
     "clause": "4.1 표 4.1-1 / 3.10.1(20) 표 3.10-1",
     "url": "https://nogada.kwoody01.com/768", "jurisdiction": "KR", "source_kind": "mirror",
     "verdict": "confirmed", "retrieved": RETRIEVED, "correction": None,
     "needs": ("dn", "service"),
     "bands": [(65, (0.02, "1/50")), (150, (0.01, "1/100")), (None, (0.005, "1/200"))]},

    {"id": "hydrant-pipe-diameter-minimums", "hook": "check", "categories": ("pipe",),
     "standard": "NFTC 102 옥내소화전설비의 화재안전기술기준", "clause": "2.3.5",
     "url": "https://www.ulex.co.kr/%EB%B2%95%EB%A5%A0/2100000216245-83615-%EC%98%A5%EB%82%B4%EC%86%8C%ED%99%94%EC%A0%84",
     "jurisdiction": "KR", "source_kind": "primary", "verdict": "confirmed", "retrieved": RETRIEVED,
     "correction": "호스릴형(25/32mm)·연결송수관 겸용(100/65mm)은 별도 규정 — 일반 가지·수직관만",
     "needs": ("dn", "service"), "min_dn": {"hydrant_branch": 40.0, "hydrant_riser": 50.0}},

    # ── Phase 2 — 간섭·지지 확장 ──────────────────────────────────────────
    {"id": "sleeve-diameter-from-pipe-and-insulation", "hook": "clash", "categories": ("pipe", "equipment", "wall", "slab"),
     "standard": "KCS 31 20 15 배관설비공사", "clause": "2.2.20 슬리브",
     "url": "https://nogada.kwoody01.com/422", "jurisdiction": "KR", "source_kind": "mirror",
     "verdict": "confirmed", "retrieved": RETRIEVED,
     "correction": "'40mm 정도' — 규격 선정 여유값이다. 보온 배관은 보온피복 외경 기준(보온 선언 시 Phase 4 `mep_envelope` 가 반영)",
     "needs": (), "margin_mm": 40.0},

    {"id": "pipe-support-vertical", "hook": "boq", "categories": ("pipe",),
     "standard": "KCS 31 20 15 배관설비공사", "clause": "3.4 지지 및 고정 표 3.4-1(수직관)",
     "url": "https://nogada.kwoody01.com/422", "jurisdiction": "KR", "source_kind": "mirror",
     "verdict": "confirmed", "retrieved": RETRIEVED,
     "correction": "주철관은 직관 1개당 1개소·이형관 2~3개당 중간 1개소로 다르다 — 이 표는 그 구분 없이 "
                  "'각 층 1개소 이상'만 적용한다(주철관 별도 규칙은 미수록)",
     "needs": ("dn",)},

    {"id": "sprinkler-hanger-spacing", "hook": "boq", "categories": ("pipe",),
     "standard": "NFTC 103 스프링클러설비의 화재안전기술기준", "clause": "2.5.13",
     "url": "https://www.ulex.co.kr/%EB%B2%95%EB%A5%A0/2100000238132-83616-%EC%8A%A4%ED%94%84%EB%A7%81%ED%81%B4%EB%9F%AC",
     "jurisdiction": "KR", "source_kind": "primary", "verdict": "confirmed", "retrieved": RETRIEVED,
     "correction": "가지배관은 헤드 설치지점 사이마다 1개 이상(3.5m 초과분은 구간마다 추가) — 여기서는 길이÷3.5 로 하한만 추정한다",
     "needs": ("service",), "spacing_m": {"sprinkler_branch": 3.5, "sprinkler_main": 4.5}},

    {"id": "sleeve-protrusion-wet-floor", "hook": "info", "categories": ("equipment", "slab"),
     "standard": "KCS 31 20 15 배관설비공사", "clause": "3.6.1 슬리브",
     "url": "https://nogada.kwoody01.com/422", "jurisdiction": "KR", "source_kind": "mirror",
     "verdict": "confirmed", "retrieved": RETRIEVED,
     "correction": "물 세척 바닥은 강관 슬리브를 마감면 위 30mm 이상 올린다 — geometry.json 에 '물 세척 바닥' "
                  "선언 표면이 없어 적용 코드는 없다(문구만, 사람이 대조한다)",
     "needs": ()},

    # ── Phase 3 — 보온 두께(BOQ) ──────────────────────────────────────────
    {"id": "insulation-thickness-cold-water", "hook": "boq", "categories": ("pipe",),
     "standard": "KCS 31 20 05 보온공사", "clause": "2.5 표 2.5-1(일반)·표 2.5-2(다습, 등급 나만 확인)",
     "url": "https://nogada.kwoody01.com/420", "jurisdiction": "KR", "source_kind": "mirror",
     "verdict": "confirmed", "retrieved": RETRIEVED,
     "correction": "급수·배수관의 결로 방지용 — 냉수(4~15°C) HVAC 배관과는 다른 표(chilled-water 쪽)",
     "needs": ("dn", "grade")},

    {"id": "insulation-thickness-hot-water", "hook": "boq", "categories": ("pipe",),
     "standard": "KCS 31 20 05 보온공사",
     "clause": "2.5 표 2.5-3(≤90°C)·표 2.5-4(91~120°C, 등급 나만 확인)·표 2.5-5(121~220°C, 전등급 동일)",
     "url": "https://nogada.kwoody01.com/420", "jurisdiction": "KR", "source_kind": "mirror",
     "verdict": "confirmed", "retrieved": RETRIEVED,
     "correction": "≤90°C 는 등급 가·나만 확인 — 다·라는 표에 없어 적용하지 않는다",
     "needs": ("dn", "grade", "fluid_temp_c")},

    {"id": "insulation-thickness-chilled-water", "hook": "boq", "categories": ("pipe",),
     "standard": "KCS 31 20 05 보온공사",
     "clause": "2.5 표 2.5-6~2.5-9(냉수·냉온수, 등급 나·라만 확인)",
     "url": "https://nogada.kwoody01.com/420", "jurisdiction": "KR", "source_kind": "mirror",
     "verdict": "confirmed", "retrieved": RETRIEVED,
     "correction": "4~6°C·6~15°C 온도대별 표가 다르다 — 등급 가·다는 표에 없어 적용하지 않는다",
     "needs": ("dn", "grade", "fluid_temp_c")},

    {"id": "duct-insulation-thickness", "hook": "boq", "categories": ("duct",),
     "standard": "KCS 31 20 05 보온공사", "clause": "2.4 표 2.4-1~2.4-4",
     "url": "https://nogada.kwoody01.com/420", "jurisdiction": "KR", "source_kind": "mirror",
     "verdict": "confirmed", "retrieved": RETRIEVED,
     "correction": "일반 공조덕트는 크기·노출 여부와 무관하게 등급만으로 정해진다. 제연덕트(service="
                  "smoke_control)는 전등급 25mm 로 별도. 주방 배기덕트(화기 50mm/일반 25mm)는 이 두 값이 "
                  "아니라 별도 조항(2.4(6))이라 미수록",
     "needs": ("grade",)},

    {"id": "insulation-thickness-definition-and-tolerance", "hook": "info", "categories": ("pipe", "duct"),
     "standard": "KCS 31 20 05 보온공사", "clause": "2.2 보온두께의 공통사항",
     "url": "https://nogada.kwoody01.com/420", "jurisdiction": "KR", "source_kind": "mirror",
     "verdict": "confirmed", "retrieved": RETRIEVED,
     "correction": "표기된 두께는 보온재만의 두께(외장재·보조재 제외)이며 **최소 기준**이다. "
                  "시공 허용오차는 +3/−2mm(부분적)",
     "needs": ()},
]
_BY_ID = {r["id"]: r for r in RULES}

# 보온 두께 표 — 검증(재독)에서 확인한 등급·온도대·다습 조합만 있다. 나머지 조합은 규칙이 있어도
# 값을 반환하지 않는다(추정하지 않는다). key = (상한 DN mm 또는 None, 두께 mm).
_DUCT_INSULATION_MM = {"가": 20.0, "나": 25.0, "다": 30.0, "라": 35.0}          # 표 2.4-1~4, 크기 무관
_DUCT_INSULATION_SMOKE_MM = 25.0                                               # 제연덕트, 전 등급 동일

_PIPE_INSULATION = {
    "cold_general":       {"가": [(80, 20), (None, 35)], "나": [(80, 25), (None, 40)],
                           "다": [(80, 30), (None, 45)], "라": [(80, 35), (None, 50)]},
    "cold_humid":         {"나": [(25, 25), (300, 40), (None, 50)]},
    "hot_90":             {"가": [(40, 20), (125, 35), (None, 45)], "나": [(40, 25), (125, 40), (None, 50)]},
    "hot_120":            {"나": [(40, 40), (125, 50), (None, 80)]},
    "hot_220":            {g: [(25, 40), (65, 50), (300, 80), (None, 100)] for g in ("가", "나", "다", "라")},
    "chilled_6_15":       {"나": [(25, 25), (None, 40)]},
    "chilled_4_6":        {"나": [(25, 30), (None, 45)]},
    "chilled_4_6_humid":  {"나": [(32, 40), (100, 50), (None, 75)], "라": [(32, 50), (100, 65), (None, 100)]},
}


def _pipe_insulation_table(service, temp_c, humid):
    """service·온도·다습 → `_PIPE_INSULATION` 의 표 id. 온도가 없으면(냉온수인데 미선언) None."""
    if service == "domestic_cold":
        return "cold_humid" if humid else "cold_general"
    if temp_c is None:
        return None
    if temp_c <= 6:
        return "chilled_4_6_humid" if humid else "chilled_4_6"
    if temp_c <= 15:
        return "chilled_6_15"                        # 다습 표 미확인
    if temp_c <= 90:
        return "hot_90"
    if temp_c <= 120:
        return "hot_120"
    if temp_c <= 220:
        return "hot_220"
    return None


def insulation_mm(category, rec, decl):
    """선언(`decl = {'grade','humid','fluid_temp_c'}`) + 레코드(DN·용도) → (두께mm, 표 id) 또는
    (None, 미적용 사유). 확인 못 한 등급·온도 조합은 값을 만들지 않는다 — 표에 없으면 코드에도 없다."""
    grade = (decl or {}).get("grade")
    if grade not in ("가", "나", "다", "라"):
        return None, "grade_undeclared"
    if category == "duct":
        if rec.get("service") == "smoke_control":
            return _DUCT_INSULATION_SMOKE_MM, "duct-insulation-thickness"
        return _DUCT_INSULATION_MM.get(grade), "duct-insulation-thickness"
    if category != "pipe":
        return None, "category_not_covered"
    table_id = _pipe_insulation_table(rec.get("service"), (decl or {}).get("fluid_temp_c"), bool((decl or {}).get("humid")))
    if table_id is None:
        return None, "no_temp_or_service_table"
    bands = _PIPE_INSULATION.get(table_id, {}).get(grade)
    if bands is None:
        return None, "grade_not_in_table"
    dn = parse_dn(rec.get("nominal_size"))
    if dn is None:
        return None, "dn_undeclared"
    return _band(bands, dn), table_id

# 백로그 — verdict != confirmed, 적용 코드 없음. 표에만 남겨 다음 사람이 다시 조사하지 않게 한다.
BACKLOG = [
    {"id": "ks-d-3507-outside-diameters", "verdict": "plausible",
     "standard": "KS D 3507 배관용 탄소강관", "why": "원문 유료(standard.go.kr) — 확인 후 선언 DN·외경 불일치 경고로만, DN 역산에는 쓰지 않는다"},
    {"id": "qto-structural-conventions", "verdict": "n/a",
     "standard": "조달청 물량산출 기준 / 국토부 표준품셈(미확인)",
     "why": "1차 출처를 못 찾았다 — 벽 상단=보 하단·기둥=슬래브까지 등. 적용 비용 6곳(resolver·stack_build._shift·pascal_bridge·boq·V004·height_basis)"},
    {"id": "rc-beam-hole-limits", "verdict": "unverified",
     "standard": "KDS 14 20 해설(국내 미확인) — 참고: 日 MLIT 고시, ACI 318",
     "why": "국내 출처 확보 전 수치를 쓰지 않는다"},
    {"id": "room-clear-height", "verdict": "unverified",
     "standard": "건축법 시행령 등(law.go.kr 원문 미확인)",
     "why": "원문 확인 후 zone × 보 하단 × 설비 띠 하단 예산 검사로"},
    {"id": "seismic-bracing-mep", "verdict": "unverified",
     "standard": "소방청 고시(내진 버팀대·이격·정착)", "why": "고시 원문 확인 전"},
    {"id": "fire-damper-and-compartment", "verdict": "refuted",
     "standard": "건축물의 피난·방화구조 등의 기준에 관한 규칙 제14조",
     "why": "2017 개정으로 종전 '철판 1.5mm' 수치가 성능기준(비차열 1시간·KS F 2822)으로 대체됨. zone 에 방화구획 선언 표면이 없다 — 별도 설계"},
    {"id": "standard-labour-output-duration", "verdict": "unverified",
     "standard": "표준품셈(2025)", "why": "ifc_4d.py 옆 duration_estimate 스키마만 예약, 수치는 원문 재독 후"},
    {"id": "duct-fitting-dimensions", "verdict": "confirmed",
     "standard": "KCS 31 20 20 / KDS 31 25 30(엘보 곡률비 1.5 R/W)",
     "why": "피팅 치수표를 쓰지 않는다는 기록(docs/decisions/mep-geometry.md '이음 몸체')과 충돌 — 재론하지 않는다"},
]


def drain_slope_ratio(rec):
    """`drain-slope-by-diameter` 표에서 이 배관의 선언 DN 에 맞는 구배(rise/run, 예: 0.01 = 1/100).
    `mep_profile._assign` 의 `slope: {ratio: "rule:drain-slope-by-diameter"}` 선언이 부르는 곳 —
    geom_contract 는 이 모듈을 import 할 수 없어(순환 참조) 값을 여기서 미리 풀어 건넨다. DN 선언이
    없으면 None(형상을 바꾸지 않는다)."""
    dn = parse_dn(rec.get("nominal_size"))
    if dn is None:
        return None
    ratio, _label = _band(_BY_ID["drain-slope-by-diameter"]["bands"], dn)
    return ratio


def required_sleeve_mm(category, rec, params=None):
    """KCS 31 20 15 2.2.20 — 슬리브 지름은 배관 외경(비보온) + 40mm '정도'. 배관만 대상이다(2.2.20 은
    배관 슬리브 조항 — 덕트·트레이 관통은 이 조항 밖). 보온 외피는 `rec['insulation_mm']` 가 있으면
    반영한다(Phase 4). 못 재면 None — clash_review 는 그러면 대조하지 않는다."""
    if category != "pipe":
        return None
    try:
        dims = GC.mep_envelope(category, rec, params)
    except (GC.ContractError, KeyError, TypeError, ValueError):
        return None
    diameter = dims.get("diameter")
    if diameter is None:
        return None
    return diameter + _BY_ID["sleeve-diameter-from-pipe-and-insulation"]["margin_mm"]


def _storeys_spanned(rec, geometry):
    """수직관이 지나는 층 수 — `floors[]` 선언이 있으면 그 z 목록으로 센다(최소 1). 없으면 프로필
    `mep_profile.levels.floor_to_floor_mm` 로 추정(⌈수직 연장 ÷ 층고⌉). 수직 구간이 없으면(순수 평면
    경로) None. 층 정보 자체가 없으면 -1(구분용 sentinel — 호출측이 `no_storey_height` 로 센다)."""
    try:
        zs = [p[2] for p in GC.route_points("pipe", rec)]
    except (GC.ContractError, KeyError, TypeError, ValueError):
        return None
    z0, z1 = min(zs), max(zs)
    if z1 - z0 < 1.0:
        return None
    floors = geometry.get("floors") or []
    if floors:
        fz = [float(f.get("z", 0.0)) for f in floors]
        return max(1, sum(1 for z in fz if z0 - 1.0 <= z <= z1 + 1.0))
    f2f = ((geometry.get("mep_profile") or {}).get("levels") or {}).get("floor_to_floor_mm")
    if f2f:
        return max(1, math.ceil((z1 - z0) / float(f2f)))
    return -1


def _row(label, service, material, size, length_m, spacing_m, count, rule, basis=None):
    note = f"{rule['id']} · {rule['clause']}"
    if (basis or {}).get("material"):
        note += " · 재질=프로젝트 기본값"           # 카테고리 추정이 아니라 사람이 적은 선언이다
    return [label, service or "", material or "", size, round(length_m, 3), spacing_m, count, note]


_INSULATION_RULE_OF_TABLE = {
    "cold_general": "insulation-thickness-cold-water", "cold_humid": "insulation-thickness-cold-water",
    "hot_90": "insulation-thickness-hot-water", "hot_120": "insulation-thickness-hot-water",
    "hot_220": "insulation-thickness-hot-water",
    "chilled_6_15": "insulation-thickness-chilled-water", "chilled_4_6": "insulation-thickness-chilled-water",
    "chilled_4_6_humid": "insulation-thickness-chilled-water",
    "duct-insulation-thickness": "duct-insulation-thickness",
}


def _size_display(cat, rec, params):
    try:
        sec = GC.mep_section(cat, rec, params)
    except (GC.ContractError, KeyError, TypeError, ValueError):
        return ""
    if "diameter" in sec:
        return "Ø%g" % sec["diameter"]
    return "%gx%g" % (sec["width_mm"], sec["height_mm"])


def _evaluate(geometry):
    """모든 규칙을 한 번 평가한다 — `review()`/`supports()` 가 나눠 쓴다.

    반환 (items, rows, receipt). items 는 V013(위반)·정보 항목, rows 는 BOQ '설비 지지·청소구'.
    receipt 는 `tolerances_effective` 와 같은 자리 — 무엇을 적용했고 무엇을 왜 건너뛰었는지."""
    els = geometry.get("elements") or {}
    params = geometry.get("params")
    items, rows, insulation_rows = [], [], []
    hits, skipped = {}, {}

    def hit(rid):
        hits[rid] = hits.get(rid, 0) + 1

    def skip(rid, reason):
        key = f"{rid}:{reason}"
        skipped[key] = skipped.get(key, 0) + 1

    # 선언 현황 — 규칙 적용 여부와 무관하게, 도면이 얼마나 선언돼 있는지의 정직한 집계.
    missing = {"dn": 0, "material": 0, "service": 0}
    for rec in els.get("pipe") or []:
        if parse_dn(rec.get("nominal_size")) is None:
            missing["dn"] += 1
        if _material_of(rec) is None:
            missing["material"] += 1
        if not rec.get("service"):
            missing["service"] += 1
    for rec in els.get("duct") or []:
        if _material_of(rec) is None:
            missing["material"] += 1
        if not rec.get("service"):
            missing["service"] += 1
    # 기본값이 채운 선언 — missing 과 짝이다. 기본값이 채워도 "선언 공백" 이라는 사실은 계속 보인다
    # (declarations_missing 의 의미는 그대로 두고, 여기서 defaulted 로 따로 센다).
    defaulted = {"dn": 0, "material": 0, "service": 0}
    for rec in els.get("pipe") or []:
        basis = rec.get("declaration_basis") or {}
        if "nominal_size" in basis:
            defaulted["dn"] += 1
        if "material" in basis:
            defaulted["material"] += 1
        if "service" in basis:
            defaulted["service"] += 1
    for rec in els.get("duct") or []:
        basis = rec.get("declaration_basis") or {}
        if "material" in basis:
            defaulted["material"] += 1
        if "service" in basis:
            defaulted["service"] += 1

    # duct-aspect-ratio — check
    rule = _BY_ID["duct-aspect-ratio"]
    for rec in els.get("duct") or []:
        if rec.get("geometry_mode") == "footprint":
            continue
        try:
            sec = GC.mep_section("duct", rec, params)
        except (GC.ContractError, KeyError, TypeError, ValueError):
            continue
        if sec["shape"] != "rect":
            continue
        ratio = max(sec["width_mm"], sec["height_mm"]) / min(sec["width_mm"], sec["height_mm"])
        hit(rule["id"])
        if ratio > rule["max_ratio"]:
            items.append({"eid": rec.get("eid"), "rule": rule["id"], "kind": "violation",
                          "values": {"ratio": round(ratio, 2), "max_ratio": rule["max_ratio"]},
                          "standard": rule["standard"], "clause": rule["clause"]})

    # hydrant-pipe-diameter-minimums — check
    rule = _BY_ID["hydrant-pipe-diameter-minimums"]
    for rec in els.get("pipe") or []:
        min_dn = rule["min_dn"].get(rec.get("service"))
        if min_dn is None:
            continue
        dn = parse_dn(rec.get("nominal_size"))
        if dn is None:
            skip(rule["id"], "dn_undeclared")
            continue
        hit(rule["id"])
        if dn < min_dn:
            items.append({"eid": rec.get("eid"), "rule": rule["id"], "kind": "violation",
                          "values": {"dn": dn, "min_dn": min_dn, "service": rec.get("service")},
                          "standard": rule["standard"], "clause": rule["clause"]})

    # pipe-support-spacing-horizontal — boq
    rule = _BY_ID["pipe-support-spacing-horizontal"]
    for rec in els.get("pipe") or []:
        dn = parse_dn(rec.get("nominal_size"))
        mat_raw = _material_of(rec)
        mat = material_key(mat_raw)
        if dn is None:
            skip(rule["id"], "dn_undeclared")
            continue
        if mat is None:
            skip(rule["id"], "material_undeclared" if mat_raw is None else "material_not_recognized")
            continue
        spacing = _band(rule["bands"][mat], dn)
        if GC.path3d_dz_range(rec) != (0.0, 0.0):
            skip(rule["id"], "vertical_run")   # 수직관은 표 3.4-1 의 다른 행 — 백로그
            continue
        try:
            length_m = GC.route_length(rec) / 1000.0
        except (GC.ContractError, KeyError, TypeError, ValueError):
            continue
        if length_m <= 0:
            continue
        hit(rule["id"])
        count = max(1, math.ceil(length_m / spacing))
        rows.append(_row("배관", rec.get("service"), mat_raw, f"DN{dn:g}", length_m, spacing, count, rule, rec.get("declaration_basis")))

    # duct-hanger-spacing — boq (원형은 물리 지름이 곧 호칭이라 별도 선언이 필요 없다)
    rule = _BY_ID["duct-hanger-spacing"]
    for rec in els.get("duct") or []:
        if rec.get("geometry_mode") == "footprint":
            continue
        mat_raw = _material_of(rec)
        if material_key(mat_raw) != "galvanized_sheet":
            skip(rule["id"], "material_undeclared" if mat_raw is None else "material_not_galvanized")
            continue
        try:
            sec = GC.mep_section("duct", rec, params)
            length_m = GC.route_length(rec) / 1000.0
        except (GC.ContractError, KeyError, TypeError, ValueError):
            continue
        if length_m <= 0:
            continue
        if sec["shape"] == "round":
            if sec["diameter"] > rule["round_max_dn"]:
                skip(rule["id"], "dn_out_of_band")
                continue
            spacing, size = rule["round_spacing_m"], f"Ø{sec['diameter']:g}"
        else:
            spacing, size = rule["rect_spacing_m"], "장방형"
        hit(rule["id"])
        count = max(1, math.ceil(length_m / spacing))
        rows.append(_row("덕트", rec.get("service"), mat_raw, size, length_m, spacing, count, rule, rec.get("declaration_basis")))

    # drain-cleanout-spacing — boq
    rule = _BY_ID["drain-cleanout-spacing"]
    for rec in els.get("pipe") or []:
        if rec.get("service") != "drain":
            continue
        dn = parse_dn(rec.get("nominal_size"))
        if dn is None:
            skip(rule["id"], "dn_undeclared")
            continue
        spacing = _band(rule["bands"], dn)
        try:
            pts = GC.route_points("pipe", rec)
            length_m = GC.route_length(rec) / 1000.0
        except (GC.ContractError, KeyError, TypeError, ValueError):
            continue
        if length_m <= 0:
            continue
        hit(rule["id"])
        turns = _count_turns_over(pts, rule["turn_deg"])
        count = 1 + math.floor(length_m / spacing) + turns   # 기점 1 + 간격마다 + 45°초과 꺾임마다
        rows.append(_row("배관", "drain", None, f"DN{dn:g}", length_m, spacing, count, rule))

    # drain-slope-by-diameter — info (형상은 바꾸지 않는다 — 필요 낙차만 말한다)
    rule = _BY_ID["drain-slope-by-diameter"]
    for rec in els.get("pipe") or []:
        if rec.get("service") != "drain":
            continue
        dn = parse_dn(rec.get("nominal_size"))
        if dn is None:
            skip(rule["id"], "dn_undeclared")
            continue
        ratio, label = _band(rule["bands"], dn)
        try:
            length_mm = GC.route_length(rec)
        except (GC.ContractError, KeyError, TypeError, ValueError):
            continue
        hit(rule["id"])
        items.append({"eid": rec.get("eid"), "rule": rule["id"], "kind": "info",
                      "values": {"dn": dn, "slope": label, "route_length_mm": round(length_mm, 1),
                                "required_fall_mm": round(length_mm * ratio, 1), "slope_basis": "undeclared"},
                      "standard": rule["standard"], "clause": rule["clause"]})

    # pipe-support-vertical — boq (수직 구간만. 수평은 pipe-support-spacing-horizontal 이 이미 처리)
    rule = _BY_ID["pipe-support-vertical"]
    for rec in els.get("pipe") or []:
        dn = parse_dn(rec.get("nominal_size"))
        if dn is None:
            continue                          # 수평 규칙이 이미 dn_undeclared 를 센다 — 이중으로 안 센다
        count = _storeys_spanned(rec, geometry)
        if count is None:
            continue                          # 수직 구간이 없다 — 이 규칙 대상이 아니다
        if count == -1:
            skip(rule["id"], "no_storey_height")
            continue
        hit(rule["id"])
        zs = [p[2] for p in GC.route_points("pipe", rec)]
        length_m = (max(zs) - min(zs)) / 1000.0
        rows.append(_row("배관", rec.get("service"), _material_of(rec), f"DN{dn:g}", length_m, "", count, rule, rec.get("declaration_basis")))

    # sprinkler-hanger-spacing — boq
    rule = _BY_ID["sprinkler-hanger-spacing"]
    for rec in els.get("pipe") or []:
        spacing = rule["spacing_m"].get(rec.get("service"))
        if spacing is None:
            continue
        try:
            length_m = GC.route_length(rec) / 1000.0
        except (GC.ContractError, KeyError, TypeError, ValueError):
            continue
        if length_m <= 0:
            continue
        hit(rule["id"])
        dn = parse_dn(rec.get("nominal_size"))
        count = max(1, math.ceil(length_m / spacing))
        rows.append(_row("배관", rec.get("service"), _material_of(rec),
                         f"DN{dn:g}" if dn is not None else "", length_m, spacing, count, rule,
                         rec.get("declaration_basis")))

    # 보온 — mep_profile._assign 이 파싱 시점에 이미 계산해 박아 둔 값(rec['insulation_mm']/
    # ['insulation_table'])을 그대로 읽는다. 여기서 다시 계산하지 않는다(단일 계산 지점).
    for cat, label in (("pipe", "배관"), ("duct", "덕트")):
        for rec in els.get(cat) or []:
            mm = rec.get("insulation_mm")
            table = rec.get("insulation_table")
            if not mm or table not in _INSULATION_RULE_OF_TABLE:
                continue
            rule = _BY_ID[_INSULATION_RULE_OF_TABLE[table]]
            try:
                length_m = GC.route_length(rec) / 1000.0
            except (GC.ContractError, KeyError, TypeError, ValueError):
                continue
            if length_m <= 0:
                continue
            hit(rule["id"])
            note = f"{rule['id']} · {rule['clause']}(최소값, 보온재만)"
            if (rec.get("declaration_basis") or {}).get("insulation"):
                note += " · 등급=프로젝트 기본값"
            insulation_rows.append([label, rec.get("service") or "", _size_display(cat, rec, params),
                                    mm, round(length_m, 3), note])

    receipt = {
        "version": 1,
        "applied": [{"id": rid, "standard": _BY_ID[rid]["standard"], "clause": _BY_ID[rid]["clause"],
                     "url": _BY_ID[rid]["url"], "verdict": _BY_ID[rid]["verdict"], "hits": n}
                    for rid, n in sorted(hits.items())],
        "skipped": skipped,
        "declarations_missing": missing,
        "declarations_defaulted": defaulted,
    }
    return items, rows, insulation_rows, receipt


def review(geometry):
    """geometry.json dict → {items, summary, receipt}. 위반(`kind=violation`)은 verify.py 의
    V013 이 읽는다. 형상·레코드는 건드리지 않는다."""
    items, _rows, _ins, receipt = _evaluate(geometry)
    violations = sum(1 for i in items if i["kind"] == "violation")
    return {"items": items, "summary": {"violations": violations, "info": len(items) - violations},
           "receipt": receipt}


def supports(geometry):
    """geometry.json dict → BOQ '설비 지지·청소구' 행. 개수는 **하한 추정**(길이÷간격, 올림)이다."""
    _items, rows, _ins, _receipt = _evaluate(geometry)
    return rows


def insulation(geometry):
    """geometry.json dict → BOQ 'MEP 보온' 행 — `mep_profile._assign` 이 프로필의 보온 선언(등급·
    다습·유온)을 이미 조회해 `rec['insulation_mm']` 로 박아 둔 것을 그대로 옮긴다. layer_map.csv 로만
    파싱한 도면은 보온 선언 표면이 없어(프로필 전용) 늘 빈 목록이다."""
    _items, _rows, ins, _receipt = _evaluate(geometry)
    return ins


CSV_COLUMNS = ("구분", "용도", "재질", "규격(DN)", "길이(m)", "간격(m)", "개수(하한)", "근거")


def to_rows(support_rows):
    """`supports()` 결과 → `clash_review.write_csv` 가 받는 dict 행(CSV 는 이 규약을 공유한다)."""
    return [dict(zip(CSV_COLUMNS, r)) for r in support_rows]
