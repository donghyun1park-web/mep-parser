# -*- coding: utf-8 -*-
"""geometry.json 기하 계약(contract) — z 기준면 규약이 존재하는 **유일한 장소**.

이 저장소에서 z 기준면 규약은 과거 여러 곳(freecad_builder / preview / 4D 애니메이션 /
struct_review)에 각자 구현돼 있었고, 그중 preview 가 슬래브를 '하단' 기준으로
해석해 보/슬래브가 한 두께 떠 보이는 버그가 났다. 더 나쁜 것은 그 증상을
"원인 미상"으로 두고 z_base 에 +thickness 를 더하는 보정을 데이터에 적용한 것이다.
보정값이 JSON 에 남아 다른 소비자를 연쇄 오염시켰다.

따라서 규약은 여기에만 둔다. 소비자는 `z_range()` 만 호출한다.
FreeCAD·shapely 의존 없음(순수 표준 라이브러리) — 그래야 단위테스트가 가능하다.

    from geom_contract import z_range, ccw
    z0, z1 = z_range("slab", rec, params)
"""

import bisect
import hashlib
import math

# v3: MEP 경로가 `path3d`(직선·원호·스플라인 구간, 점마다 높이)를 갖는다. mm·Z-up 은
# 그대로다. v2 파일은 **좌표를 하나도 옮기지 않고** 읽힌다 — `path3d` 가 없으면 경로는
# (평면 points, elevation) 의 직선 구간이다(`path3d_segments`).
SCHEMA_VERSION = 3
COMPATIBLE_VERSIONS = (2, 3)

# ── z 기준면(datum) ────────────────────────────────────────────────────────
#   bottom : z_base 가 아랫면. 위로 height 만큼 올라간다.
#   top    : z_base 가 윗면. 아래로 thickness 만큼 내려간다.  ← 슬래브/보만
#   axis   : elevation 이 단면 중심축. 위아래로 절반씩.        ← 배관/덕트/트레이
#
#   슬래브가 'top' 인 것은 실수가 아니라 도면 규약이다. 구조도면은 슬래브 상단(FL)과
#   벽 하단을 준다. 슬래브를 'bottom' 으로 강제하면 사람이 입력하는 모든 수치에서
#   두께를 미리 빼야 하고, 그게 바로 +thickness 보정이 태어난 경위다.
Z_DATUM = {
    "wall":      "bottom",
    "column":    "bottom",
    "zone":      "bottom",
    "opening":   "bottom",
    "slab":      "top",
    "beam":      "top",
    "pipe":      "axis",
    "duct":      "axis",
    "tray":      "axis",
    "equipment": "bottom",
}

# 카테고리별 치수 기본값. params 로 덮어쓸 수 있다.
DEFAULT_DIMS = {
    "wall":      {"width": 200.0, "height": 2800.0},
    "column":    {"width": 400.0, "height": 3000.0},
    "zone":      {"height": 2800.0},
    "opening":   {"height": 2100.0},
    "slab":      {"thickness": 200.0},
    "beam":      {"width": 300.0, "thickness": 600.0},
    "pipe":      {"diameter": 100.0},
    "duct":      {"width_mm": 400.0, "height_mm": 300.0},
    "tray":      {"width_mm": 300.0, "height_mm": 100.0},
    "equipment": {"height": 1000.0},
}

# z_base 가 아니라 elevation 키를 쓰는 카테고리(MEP). 의미가 달라 개명하지 않는다.
_ELEV_CATS = ("pipe", "duct", "tray", "equipment")


class ContractError(ValueError):
    """계약 위반 — 알 수 없는 카테고리 등."""


# ── 기본 접근자 ────────────────────────────────────────────────────────────
def _dim(category, key, rec=None, params=None):
    """치수 해석 우선순위: rec.overrides > rec 최상위 > params[cat] > DEFAULT_DIMS."""
    if rec is not None:
        ov = rec.get("overrides") or {}
        if ov.get(key) is not None:
            return float(ov[key])
        if rec.get(key) is not None:
            return float(rec[key])
    if params:
        p = params.get(category) or {}
        if p.get(key) is not None:
            return float(p[key])
    return float(DEFAULT_DIMS.get(category, {}).get(key, 0.0))


def datum_of(category):
    if category not in Z_DATUM:
        raise ContractError(f"알 수 없는 카테고리: {category!r}")
    return Z_DATUM[category]


def floor_has_level(floor, level):
    """`floors[]` 한 항목이 레코드의 `level`(원본 id)을 담는가 — 빌더·빌드 전 검사·IFC 재검사가 같이 쓴다.

    같은 층에 겹친 공종 도면(건축 + 난방 + 환기)은 한 층이 원본 여러 개를 `sources` 로 묶는다
    (stack_build). 층 이름만 보면 원본 id 가 달라 그 층의 벽·설비가 전부 고아가 된다."""
    return level in (floor.get("id"), floor.get("label"), floor.get("storey")) or level in (floor.get("sources") or ())


def base_z(category, rec):
    """해당 카테고리가 쓰는 기준 z 값. MEP 는 elevation, 나머지는 z_base."""
    key = "elevation" if category in _ELEV_CATS else "z_base"
    value = (rec.get('overrides') or {}).get(key, rec.get(key, 0.0))
    return float(value if value is not None else 0.0)


def floor_z(category, rec):
    """이 부재가 **어느 층에 속하는가**를 정하는 높이. 보통은 `base_z` 와 같다.

    창 위 벽(인방)처럼 층 바닥보다 높이 떠서 시작하는 벽은 `z_base` 가 형상의 아랫면(예: 2100)이라, 그 값으로
    층을 고르면 2.1m 짜리 가짜 층이 생기거나(파서 층 감지·Pascal 레벨) 어느 층에도 안 들어가 빌드가 막힌다
    (V001 · 빌더 `_at_floor` · IFC 층 재검사). 그런 레코드는 `floor_z` 에 옆 벽의 층 높이를 적는다.
    ★ 형상은 여전히 `z_range` 로만 읽는다. 층 판정만 이 함수로 한다."""
    value = rec.get("floor_z")
    return float(value) if value is not None else base_z(category, rec)


def _positive(value, field):
    if isinstance(value, bool):
        raise ContractError(f'{field}: boolean is not a dimension')
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ContractError(f'{field}: numeric dimension required') from None
    if not math.isfinite(value) or value <= 0:
        raise ContractError(f'{field}: finite positive dimension required')
    return value


# 단면 치수의 정식 키와 GUI 별칭. **소비자가 다시 쓰지 말 것** — 되돌릴 때
# 어떤 키를 지워야 선언이 갱신되는지도 이 표가 정한다(`pascal_bridge`).
MEP_DIM_ALIASES = {
    'pipe': {'diameter': ('diameter', 'outside_diameter_mm', 'diameter_mm', 'width')},
    'duct': {'width_mm': ('width_mm', 'width'), 'height_mm': ('height_mm', 'height')},
    'tray': {'width_mm': ('width_mm', 'width'), 'height_mm': ('height_mm', 'height')},
}
# 원형 덕트(v3). `width` 별칭은 두지 않는다 — 사각 덕트의 폭 선언이 모양을 바꾸는
# 순간 지름으로 읽히면 조용히 다른 크기가 된다.
ROUND_DIM_ALIASES = {'diameter': ('diameter', 'outside_diameter_mm', 'diameter_mm')}
SECTION_SHAPES = ('round', 'rect')


def section_shape(category, rec):
    """단면 모양. 배관은 원형뿐이고, 덕트·트레이는 선언이 없으면 사각(v2 와 같다)."""
    shape = (rec.get('overrides') or {}).get('section_shape', rec.get('section_shape'))
    if shape is None:
        return 'round' if category == 'pipe' else 'rect'
    if shape not in SECTION_SHAPES or (category == 'pipe' and shape != 'round'):
        raise ContractError(f'{category}: unsupported section shape {shape!r}')
    return shape


def mep_dimensions(category, rec, params=None):
    """Physical outside dimensions; canonical keys win over legacy GUI aliases.

    A nominal designation (e.g. PB15A) never becomes an outside diameter.
    Legacy records may use defaults; explicitly unknown profile dimensions may not.
    Round ducts/trays return ``{'diameter': ...}``; rectangular ones width/height.
    """
    if category not in MEP_DIM_ALIASES:
        raise ContractError('Not a linear MEP category: ' + str(category))
    if rec.get('dimension_status') == 'unknown':
        raise ContractError('MEP outside dimensions are unresolved')
    aliases = MEP_DIM_ALIASES[category]
    defaults = DEFAULT_DIMS[category]
    if category != 'pipe' and section_shape(category, rec) == 'round':
        aliases, defaults = ROUND_DIM_ALIASES, {}      # 원형 덕트 지름은 기본값으로 때우지 않는다
    sources = [rec.get('overrides') or {}, rec, (params or {}).get(category) or {}, defaults]
    result = {}
    for key, names in aliases.items():
        if key == 'width_mm' and rec.get('geometry_mode') == 'footprint':
            continue  # The actual polygon defines plan width; no nominal width is invented.
        value = next((source[name] for source in sources for name in names if source.get(name) is not None), None)
        result[key] = _positive(value, category + '.' + key)
    return result


def mep_elevation(placement, section_height_mm, levels, floor_layers, explicit_center_mm=None):
    """Resolve a project placement rule into the existing MEP axis datum, in mm."""
    height = _positive(section_height_mm, 'section_height_mm')
    levels = levels or {}
    base = levels.get('structural_slab_top_mm', 0.0)
    try:
        base = float(base)
    except (TypeError, ValueError):
        raise ContractError('Structural slab top must be finite') from None
    if not math.isfinite(base):
        raise ContractError('Structural slab top must be finite')
    if placement in ('source', 'center'):
        if isinstance(explicit_center_mm, bool):
            raise ContractError('An explicit center elevation is required')
        try:
            center = float(explicit_center_mm)
        except (ValueError, TypeError):
            raise ContractError('An explicit center elevation is required') from None
        if not math.isfinite(center):
            raise ContractError('Center elevation must be finite')
        return center
    if placement == 'slab_soffit':
        storey = _positive(levels.get('floor_to_floor_mm'), 'floor_to_floor_mm')
        slab = _positive(levels.get('slab_thickness_mm'), 'slab_thickness_mm')
        if slab >= storey or height > storey - slab:
            raise ContractError('Section does not fit below the upper slab')
        return base + storey - slab - height / 2
    if placement == 'foam_top':
        layers = floor_layers or []
        if sum(row.get('role') == 'foamed_concrete' for row in layers) != 1:
            raise ContractError('Exactly one foamed_concrete layer is required')
        z = base
        for row in layers:
            z += _positive(row.get('thickness_mm'), 'floor_layer.thickness_mm')
            if row.get('role') == 'foamed_concrete':
                return z + height / 2
    raise ContractError('Unknown MEP placement: ' + str(placement))


def thickness_of(rec, params=None, category="slab"):
    return _dim(category, "thickness", rec, params)


def height_of(rec, params=None, category="wall"):
    return _dim(category, "height", rec, params)


# 파서가 스스로 '잘못 잰 값' 으로 표시한 사유. 이런 실측은 쓰지 않는다.
UNTRUSTED_WIDTH_REASONS = ("thin_pair",)


def width_of(rec, params=None, category="wall"):
    """평면상 폭. 우선순위: layer_map 선언 → **믿을 수 있는** 실측 → params 기본값.

    ★ 적어 준 값이 이긴다(stack 레벨 height 와 같은 규약). 레이어 기본값 대신
      실측을 쓰고 싶으면 layer_map 의 width 를 **비운다** — 그러면 여기로 떨어진다.
      어긋나는 경우는 파서가 `width_conflicts` 로 보고한다.

    ★ 실측이라고 다 믿지 않는다. 파서가 `review_reason="thin_pair"` 로 표시한 것은
      벽면 옆 마감선과 짝지어 나온 값이라 두께가 아니다(실측: A-CON 중앙값 250mm
      옆에 50mm 21개). 종전에는 선언값 200mm 이 이걸 우연히 가려 주고 있었으므로,
      선언을 비우는 순간 50mm 벽이 세워진다 — 그 함정을 여기서 막는다.
    """
    if category == "wall":
        ov = (rec.get("overrides") or {}).get("width")
        if ov is not None:
            return float(ov)
        wd = rec.get("width_detected")
        if wd and rec.get("review_reason") not in UNTRUSTED_WIDTH_REASONS:
            return float(wd)
    return _dim(category, "width", rec, params)


def _dim_source(category, key, rec, params):
    """그 치수가 어디서 왔는가 — `_dim` 의 우선순위와 **같은 순서**로 본다."""
    if (rec.get("overrides") or {}).get(key) is not None:
        return "overrides"
    if rec.get(key) is not None:
        return "record"
    if ((params or {}).get(category) or {}).get(key) is not None:
        return "params"
    return "default"


_HEIGHT_KEYS = {"wall": ("height",), "column": ("height",), "zone": ("height",), "equipment": ("height",),
                "opening": ("height", "sill"), "slab": ("thickness",), "beam": ("thickness",)}
# 평면도에서 온 z — 0 은 값이 아니라 값이 없다는 뜻이다(`height_basis` 의 `plan_z`).
PLAN_Z_EPS_MM = 1e-6


def is_plan_zero(value):
    try:
        return abs(float(value)) <= PLAN_Z_EPS_MM
    except (TypeError, ValueError):
        return False


def height_basis(category, rec, params=None):
    """이 부재의 높이가 어디서 왔는가 — `{"z": "declared"|"source"|"assumed", "assumed": [키…]}`.

    ★ **평면도에는 높이가 없다.** 간섭·연결 판정은 대부분 선언값이나 레이어 기본값에 기대는데, 종전에는
      그 사실이 판정 결과 어디에도 안 실려 **가정으로 나온 줄과 도면이 말해 준 줄이 똑같아 보였다**
      (실측: 지하3층 개구부 26개의 높이·문턱이 26개 전부 가정값, 난방관 지름은 프로필의 `assumed`).
      고치지 않고 **말하기만** 한다 — 높이를 추정해 바꾸는 것은 같은 실패를 한 겹 더 쌓는 일이다.

    판정: 기준 z 는 `overrides`·`elevation_source`(declared/profile) → declared · 도면 z → source · 없음 →
    assumed. 치수는 `dims_assumed`·`dimension_basis` 가 가정이라 말했거나 `params`/기본값으로 떨어지면 가정이다.
    하나라도 가정이면 그 부재의 `z` 는 assumed 다.

    ★ **평면도의 z 0 은 근거가 아니다.** 평면도는 설비를 전부 z 0 에 그린다 — 0 은 "도면이 높이를 말해
      줬다" 가 아니라 **정보가 없다**는 뜻이다. 그걸 `source` 로 세면 바닥에 깔린 덕트가 선언한 줄과
      똑같아 보인다(실측: 환기 덕트 45개가 전부 z 0 이던 때 간섭 2건이 나왔고, 제 높이로 올리자 0건이
      됐다 — 형상은 양쪽 다 멀쩡했다). 그래서 기준 z 가 0 이면 `assumed` 에 `plan_z` 를 넣는다."""
    assumed = []
    overrides = rec.get("overrides") or {}
    if category in _ELEV_CATS:
        source = rec.get("elevation_source")
        if overrides.get("elevation") is not None or source in ("declared", "profile"):
            datum = "declared"
        elif source == "source" or rec.get("elevation") is not None:
            raw = rec.get("source_elevation_mm")
            raw = rec.get("elevation") if raw is None else raw
            if is_plan_zero(raw):
                datum = "assumed"
                assumed.append("plan_z")
            else:
                datum = "source"
        else:
            datum = "assumed"
            assumed.append("elevation")
    elif overrides.get("z_base") is not None:
        datum = "declared"
    elif rec.get("z_base") is not None:
        datum = "source"
    else:
        datum, _ = "assumed", assumed.append("z_base")
    keys = _HEIGHT_KEYS.get(category)
    if keys is None:
        try:
            keys = tuple(mep_dimensions(category, rec, params))
        except ContractError:
            keys = ()
            assumed.append("section")
    declared_assumed = set(rec.get("dims_assumed") or ())
    for key in keys:
        if key in declared_assumed or _dim_source(category, key, rec, params) in ("params", "default"):
            assumed.append(key)
    return {"z": "assumed" if assumed else datum, "assumed": sorted(set(assumed))}


# ── 단위 ────────────────────────────────────────────────────────────────────
# geometry.json 은 **mm**(스키마 `"units": "mm"`), Pascal 씬 그래프는 **m** 이다.
# 나누기 1000 을 여기저기 쓰기 시작하면 어느 한 곳이 빠졌을 때 1000배 틀린 모델이
# 조용히 나간다 — z 규약과 같은 이유로 변환도 이 파일에만 둔다.
MM_PER_M = 1000.0
# Pascal 의 덕트·배관 지름은 **인치**다(미국 관행). 여기 말고 다른 데서 25.4 를 쓰지 말 것.
MM_PER_IN = 25.4


def mm_to_m(v):
    """mm 스칼라/좌표열 → m. 리스트는 구조를 유지한 채 재귀."""
    if isinstance(v, (list, tuple)):
        return [mm_to_m(x) for x in v]
    return None if v is None else float(v) / MM_PER_M


def m_to_mm(v):
    """m 스칼라/좌표열 → mm."""
    if isinstance(v, (list, tuple)):
        return [m_to_mm(x) for x in v]
    return None if v is None else float(v) * MM_PER_M


def mm_to_in(v):
    """mm → 인치."""
    return None if v is None else float(v) / MM_PER_IN


def in_to_mm(v):
    """인치 → mm."""
    return None if v is None else float(v) * MM_PER_IN


# ── 핵심: 모든 소비자가 호출하는 단 하나의 함수 ─────────────────────────────
def z_range(category, rec, params=None):
    """(z0, z1) = (아랫면, 윗면). 카테고리 규약을 여기서만 해석한다."""
    d = datum_of(category)
    z = base_z(category, rec)

    if d == "bottom":
        h = _dim(category, "height", rec, params)
        return (z, z + h)

    if d == "top":
        t = _dim(category, "thickness", rec, params)
        return (z - t, z)

    # axis — 단면 중심이 elevation. 3D 경로(v3)는 점마다 높이가 달라 전체 범위를 준다.
    dims = mep_dimensions(category, rec, params)
    half = dims['diameter' if 'diameter' in dims else 'height_mm'] / 2.0
    lo, hi = path3d_dz_range(rec)
    return (z + lo - half, z + hi + half)


def z_range_for(data, category, rec):
    """geometry.json 전체를 받아 params 를 자동으로 꺼내 쓰는 편의 함수."""
    return z_range(category, rec, data.get("params"))


# ── MEP 경로(계약 v3) ─────────────────────────────────────────────────────
# 배관·덕트·트레이의 **현재 형상**은 `path3d.segments` 다. 화면·빌더가 쓰는 샘플 점은
# 여기서 파생된다(`sample_segments`) — 원호를 작은 직선으로 바꿔 저장하지 않는다.
#
#   {"type": "line",   "start": [x,y,dz], "end": [x,y,dz]}
#   {"type": "arc",    "start": [..], "end": [..], "center": [..], "normal": [nx,ny,nz]}
#        start→end 는 normal 둘레 **반시계**(오른손). 전원(全圓)은 start == end.
#   {"type": "spline", "degree": p, "control_points": [[x,y,dz],..], "knots": [..],
#                      "weights": [..]}   ← 없으면 전부 1(비유리)
#
# ★ z 는 부재 `elevation`(중심축)에서의 **상대값**이다. 그래서 설치 높이 규칙(프로필
#   placement · `top:` 선언 · overrides.elevation)이 여전히 한 손잡이이고, 경로 전체를
#   위아래로 옮긴 편집은 v2 처럼 elevation 선언 하나로 남는다. DXF 평면도에서 온 경로는
#   전부 dz = 0 이다. 모든 소비자는 `route_points()` 로 절대 좌표를 받는다.
ROUTE_CATS = ("pipe", "duct", "tray")
ROUTE_SEGMENT_TYPES = ("line", "arc", "spline")
ROUTE_CHORD_MM = 0.5            # 빌더 기본 현오차(mm). 화면은 더 거칠게 써도 된다.
_EPS = 1e-9
_UP = (0.0, 0.0, 1.0)


def _p3(p):
    return [float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else 0.0]


def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _add(a, b):
    return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]


def _mul(a, s):
    return [a[0] * s, a[1] * s, a[2] * s]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _norm(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    n = _norm(a)
    if n < _EPS:
        raise ContractError("zero-length direction")
    return [a[0] / n, a[1] / n, a[2] / n]


def path3d_segments(rec):
    """현재 경로의 해석 구간(상대 z, mm).

    `path3d` 가 없으면 v2 평면 경로(points)의 직선 구간이다 — **무이동 변환**.
    닫힌 경로는 마지막 점에서 첫 점으로 닫는다(빌더가 하던 그대로)."""
    p3 = rec.get("path3d")
    if p3:
        segs = p3.get("segments") if isinstance(p3, dict) else None
        if not segs:
            raise ContractError("path3d.segments is required")
        for s in segs:
            if s.get("type") not in ROUTE_SEGMENT_TYPES:
                raise ContractError(f"unsupported path3d segment {s.get('type')!r}")
        return segs
    pts = rec.get("centerline") or rec.get("points") or []
    if rec.get("closed") and len(pts) > 2 and math.dist(pts[0][:2], pts[-1][:2]) > _EPS:
        pts = list(pts) + [pts[0]]
    return [{"type": "line", "start": [float(a[0]), float(a[1]), 0.0],
             "end": [float(b[0]), float(b[1]), 0.0]}
            for a, b in zip(pts, pts[1:]) if math.dist(a[:2], b[:2]) > _EPS]


def _arc_frame(s):
    """원호 → (중심, u, v, 반지름, 회전각). u 는 중심→시작, v = normal × u."""
    c, a, b = _p3(s["center"]), _p3(s["start"]), _p3(s["end"])
    n = _unit(_p3(s["normal"]))
    ua, ub = _sub(a, c), _sub(b, c)
    r = _norm(ua)
    tol = max(1e-6, r * 1e-6)
    if r < _EPS or abs(_norm(ub) - r) > tol:
        raise ContractError("arc start/end do not lie on one circle")
    if abs(_dot(ua, n)) > tol or abs(_dot(ub, n)) > tol:
        raise ContractError("arc points are not in the plane of its normal")
    u = _mul(ua, 1.0 / r)
    v = _cross(n, u)
    theta = math.atan2(_dot(ub, v), _dot(ub, u))
    if theta <= 1e-12:
        theta += 2.0 * math.pi                  # start == end → 전원
    return c, u, v, r, theta


def _nurbs(s):
    p = int(s["degree"])
    ctrl = [_p3(q) for q in s["control_points"]]
    knots = [float(k) for k in s["knots"]]
    w = [float(x) for x in (s.get("weights") or [1.0] * len(ctrl))]
    if p < 1 or len(ctrl) <= p or len(knots) != len(ctrl) + p + 1 or len(w) != len(ctrl):
        raise ContractError("inconsistent spline degree/control points/knots/weights")
    if any(not math.isfinite(x) or x <= 0 for x in w):
        raise ContractError("spline weights must be positive")
    if any(b < a for a, b in zip(knots, knots[1:])):
        raise ContractError("spline knots must be non-decreasing")
    return p, ctrl, knots, w


def _nurbs_point(p, ctrl, knots, w, t):
    """de Boor(동차 좌표) — 유리 B-스플라인 한 점."""
    n = len(ctrl) - 1
    k = min(max(bisect.bisect_right(knots, t) - 1, p), n)
    d = [[ctrl[j][0] * w[j], ctrl[j][1] * w[j], ctrl[j][2] * w[j], w[j]] for j in range(k - p, k + 1)]
    for r in range(1, p + 1):
        for j in range(p, r - 1, -1):
            i = j + k - p
            den = knots[i + p - r + 1] - knots[i]
            al = 0.0 if den == 0 else (t - knots[i]) / den
            d[j] = [(1 - al) * d[j - 1][m] + al * d[j][m] for m in range(4)]
    x, y, z, ww = d[p]
    return [x / ww, y / ww, z / ww]


def _sample_spline(s, chord):
    p, ctrl, knots, w = _nurbs(s)
    t0, t1 = knots[p], knots[len(ctrl)]
    spans = sorted({k for k in knots if t0 <= k <= t1})
    out = [_nurbs_point(p, ctrl, knots, w, t0)]

    def refine(ta, pa, tb, pb, depth):
        tm = (ta + tb) / 2.0
        pm = _nurbs_point(p, ctrl, knots, w, tm)
        ab = _sub(pb, pa)
        L = _norm(ab)
        dev = _norm(_sub(pm, pa)) if L < _EPS else _norm(_cross(_sub(pm, pa), ab)) / L
        if depth < 18 and dev > chord:
            refine(ta, pa, tm, pm, depth + 1)
            refine(tm, pm, tb, pb, depth + 1)
        else:
            out.append(pb)

    for a, b in zip(spans, spans[1:]):
        # 매듭 구간마다 4등분에서 시작한다 — 한 번의 중점 검사로 굴곡을 놓치지 않게.
        ts = [a + (b - a) * i / 4.0 for i in range(5)]
        ps = [_nurbs_point(p, ctrl, knots, w, t) for t in ts]
        for i in range(4):
            refine(ts[i], ps[i], ts[i + 1], ps[i + 1], 0)
    return out


def _sample_one(s, chord):
    t = s["type"]
    if t == "line":
        return [_p3(s["start"]), _p3(s["end"])]
    if t == "arc":
        c, u, v, r, theta = _arc_frame(s)
        step = 2.0 * math.acos(max(-1.0, 1.0 - min(chord / r, 1.0)))
        n = max(1, math.ceil(theta / step))
        return [_add(c, _add(_mul(u, r * math.cos(theta * i / n)), _mul(v, r * math.sin(theta * i / n))))
                for i in range(n + 1)]
    return _sample_spline(s, chord)


def sample_segments(segments, chord_error_mm=ROUTE_CHORD_MM, dz=0.0):
    """구간 → 샘플 점 [[x,y,z],..](연속 중복 제거). `dz` 를 z 에 더한다."""
    if not (chord_error_mm > 0):
        raise ContractError("chord error must be positive")
    out = []
    for s in segments:
        for p in _sample_one(s, chord_error_mm):
            q = [p[0], p[1], p[2] + dz]
            if not out or math.dist(out[-1], q) > _EPS:
                out.append(q)
    return out


def route_points(category, rec, chord_error_mm=ROUTE_CHORD_MM):
    """MEP 경로의 **절대** 3D 샘플 점(mm). 모든 빌더·다리가 이것을 쓴다."""
    return sample_segments(path3d_segments(rec), chord_error_mm, base_z(category, rec))


def path3d_dz_range(rec):
    """경로 상대 높이 범위 (min, max). v2 평면 경로는 (0, 0)."""
    if not rec.get("path3d"):
        return (0.0, 0.0)
    zs = [p[2] for p in sample_segments(path3d_segments(rec))]
    return (min(zs), max(zs)) if zs else (0.0, 0.0)


def is_planar_polyline_route(rec):
    """직선 구간만 있고 모두 dz = 0 인가 — v2 빌더 경로를 그대로 써도 되는 경우."""
    return all(s["type"] == "line" and abs(_p3(s["start"])[2]) <= _EPS and abs(_p3(s["end"])[2]) <= _EPS
               for s in path3d_segments(rec))


def segment_length(s):
    """구간 길이(mm). 직선·원호는 해석값, 스플라인은 0.001mm 현오차 샘플의 길이.

    ★ 꺾은선은 곡선보다 늘 짧다(상대오차 ≈ 현오차/3R). 0.01mm 로는 R1000 에서
      1.6e-6 이 모자랐다 — 원본 길이를 소수 여섯째 자리까지 대조하는 검사가 있다."""
    if s["type"] == "line":
        return math.dist(_p3(s["start"]), _p3(s["end"]))
    if s["type"] == "arc":
        _c, _u, _v, r, theta = _arc_frame(s)
        return r * theta
    pts = _sample_spline(s, 0.001)
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


def route_length(rec):
    """현재 모델 길이(mm, 수직 구간 포함). 원본 길이는 `source_length_mm` 가 따로 들고 있다."""
    return sum(segment_length(s) for s in path3d_segments(rec))


def route_length_basis(rec):
    return ("evaluated_curve_approximation"
            if any(s["type"] == "spline" for s in path3d_segments(rec)) else "analytic")


# ── MEP 이음(joint) — 연결은 **명시적 증거**로만 만든다 ────────────────────────
# 도면이 실제로 이어 그린 곳만 이음이다: 경로 끝이 다른 경로 끝과 **같은 점**(엘보·레듀서·티·
# 크로스)이거나, 다른 경로의 안쪽 **위에** 놓인 곳(가지 — 줄기는 `tap`). 가까운 끝(틈)·교차·
# 다른 높이·다른 카테고리는 이음이 아니다 — 가까운 것을 잇는 일은 선언(`connect_gap=`)으로만
# 한다(SA 와 RA 끝이 스치는 자리를 이으면 두 계통이 하나가 된다). 레코드가
# `joints: [{id, port, at_mm?}]` 를 들고, 같은 id 를 든 레코드끼리가 한 이음이다 — 구성원이 id 를
# 들고 다니므로 이동·분할로 EID 가 바뀌어도 끊어지지 않고, 끊긴 이음은 `joint_problems` 가 말한다.
JOINT_PORTS = ("start", "end", "tap")
JOINT_COINCIDENT_MM = 0.001     # '같은 점' — 파서가 이음을 **만드는** 허용치(틈을 메우는 거리가 아니다)
JOINT_TOL_MM = 1.0              # 선언된 이음의 구성원이 아직 만나는가 — 편집 뒤 **검사** 허용치


def _joint_id(category, point):
    key = "|".join([category] + ["%.3f" % (round(float(v), 3) + 0.0) for v in point])
    return "j:" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _polyline_nearest(pts, q):
    """(거리, 시작부터의 길이) — 꺾은선 위에서 q 에 가장 가까운 점."""
    best, run = (math.inf, 0.0), 0.0
    for a, b in zip(pts, pts[1:]):
        ab = _sub(b, a)
        length = _norm(ab)
        t = 0.0 if length <= _EPS else max(0.0, min(1.0, _dot(_sub(q, a), ab) / (length * length)))
        d = math.dist(_add(a, _mul(ab, t)), q)
        if d < best[0]:
            best = (d, run + t * length)
        run += length
    return best


def _polyline_at(pts, s):
    """꺾은선 시작에서 길이 s 인 점(길이를 넘으면 끝점)."""
    for a, b in zip(pts, pts[1:]):
        length = math.dist(a, b)
        if s <= length:
            return _add(a, _mul(_sub(b, a), s / length if length > _EPS else 0.0))
        s -= length
    return list(pts[-1])


def route_port_point(category, rec, port, at_mm=None):
    """이음 구성원의 절대 3D 위치(mm) — 경로 시작·끝, 또는 `route_points` 를 따라 `at_mm` 인 점."""
    pts = route_points(category, rec)
    if port == "start":
        return list(pts[0])
    if port == "end":
        return list(pts[-1])
    return _polyline_at(pts, float(at_mm))


def assign_joints(elements, tol_mm=JOINT_COINCIDENT_MM):
    """배관·덕트·트레이에 이음(`joints`)을 쓰고 요약 {joints, taps, by_degree} 를 돌려준다.

    카테고리마다 따로 본다. 끝끼리 `tol_mm` 안이면 한 무리이고, 그 점이 다른 경로의 안쪽(끝에서
    `tol_mm` 넘게 떨어진 곳) 위에 있으면 그 경로가 `tap`(`at_mm` = `route_points` 를 따른 길이)으로
    든다. 구성원이 둘 이상일 때만 이음이다. id 는 이음 점에서 나오므로 같은 도면을 다시 파싱하면
    같다. **파싱 직후 레코드에만** 쓴다 — 있던 `joints` 는 지우고 다시 쓴다."""
    if not tol_mm > 0:
        raise ContractError("joint tolerance must be positive")
    summary = {"joints": 0, "taps": 0, "by_degree": {}}
    for cat in ROUTE_CATS:
        routes = []
        for rec in elements.get(cat) or []:
            rec.pop("joints", None)
            if rec.get("geometry_mode") == "footprint":
                continue
            try:
                pts = route_points(cat, rec)
            except (ContractError, KeyError, TypeError, ValueError):
                continue                          # 끊긴 경로는 V010 몫이다
            if len(pts) >= 2:
                box = ([min(p[k] for p in pts) for k in range(3)], [max(p[k] for p in pts) for k in range(3)])
                routes.append((rec, pts, sum(math.dist(a, b) for a, b in zip(pts, pts[1:])), box))
        cells, groups = {}, []
        for i, (rec, pts, _length, _box) in enumerate(routes):
            if rec.get("closed"):
                continue
            for port, p in (("start", pts[0]), ("end", pts[-1])):
                cell = tuple(math.floor(v / tol_mm) for v in p)
                near = [groups[k] for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)
                        for k in cells.get((cell[0] + dx, cell[1] + dy, cell[2] + dz), ())
                        if math.dist(groups[k][0][3], p) <= tol_mm]
                if near:
                    near[0].append((i, port, None, p))
                else:
                    cells.setdefault(cell, []).append(len(groups))
                    groups.append([(i, port, None, p)])
        # ponytail: 가지 판정은 무리 × 경로(상자 선별 뒤 꺾은선) — 도면당 수백~수천 경로면 충분하다.
        for group in groups:
            point, ended = group[0][3], {m[0] for m in group}
            members = list(group)
            for j, (_rec, pts, length, (lo, hi)) in enumerate(routes):
                if j in ended or any(point[k] < lo[k] - tol_mm or point[k] > hi[k] + tol_mm for k in range(3)):
                    continue
                d, s = _polyline_nearest(pts, point)
                if d <= tol_mm and tol_mm < s < length - tol_mm:
                    members.append((j, "tap", s, point))
            if len(members) < 2:
                continue
            jid = _joint_id(cat, point)
            for idx, port, s, _p in members:
                ref = {"id": jid, "port": port}
                if port == "tap":
                    ref["at_mm"] = round(s, 3)
                routes[idx][0].setdefault("joints", []).append(ref)
            degree = str(sum(2 if m[1] == "tap" else 1 for m in members))
            summary["joints"] += 1
            summary["taps"] += sum(1 for m in members if m[1] == "tap")
            summary["by_degree"][degree] = summary["by_degree"].get(degree, 0) + 1
    return summary


def mep_joints(elements):
    """이음 id → 구성원 [(카테고리, 레코드, port, at_mm)] — 레코드의 `joints` 에서 모은 파생값."""
    out = {}
    for cat in ROUTE_CATS:
        for rec in elements.get(cat) or []:
            for ref in rec.get("joints") or []:
                out.setdefault(str(ref.get("id") or ""), []).append(
                    (cat, rec, ref.get("port"), ref.get("at_mm")))
    return out


def joint_problems(elements, tol_mm=JOINT_TOL_MM):
    """선언된 이음의 문제 [{joint, problem, eids[, distance_mm]}] — 보고만 하고 고치지 않는다.

    `single_member` 상대가 지워졌다 · `members_apart` 이동·편집으로 구성원이 더는 만나지 않는다 ·
    `bad_member` id·포트가 틀렸거나 경로·가지 위치를 풀 수 없다."""
    problems = []
    for jid, members in sorted(mep_joints(elements).items()):
        item = {"joint": jid, "eids": [rec.get("eid") for _c, rec, _p, _a in members]}
        points = []
        try:
            for cat, rec, port, at in members:
                if not jid or port not in JOINT_PORTS or (port == "tap") != (at is not None):
                    raise ValueError("bad joint reference")
                points.append(route_port_point(cat, rec, port, at))
                if port == "tap" and not 0.0 <= float(at) <= route_length(rec) + tol_mm:
                    raise ValueError("tap outside route")
        except (ContractError, KeyError, TypeError, ValueError):
            problems.append(dict(item, problem="bad_member"))
            continue
        if len(members) < 2:
            problems.append(dict(item, problem="single_member"))
            continue
        # 사람이 확정한 이음(`basis: "bridged"`)은 **떨어져 있는 것이 정상**이다 — 도면이 피팅 자리에서 끊어
        # 그린 틈을 사람이 잇겠다고 선언한 것이므로, 선언한 틈(`gap_mm`)까지는 벌어짐으로 보고하지 않는다.
        allowance = tol_mm
        for _cat, rec, _port, _at in members:
            for ref in rec.get("joints") or []:
                if str(ref.get("id")) == jid and ref.get("basis") == "bridged":
                    allowance = max(allowance, float(ref.get("gap_mm") or 0.0) + tol_mm)
        spread = max(math.dist(a, b) for a in points for b in points)
        if spread > allowance:
            problems.append(dict(item, problem="members_apart", distance_mm=round(spread, 3)))
    return problems


def reverse_segments(segments):
    """경로 방향 뒤집기 — 원호는 법선을, 스플라인은 매듭을 함께 뒤집는다."""
    out = []
    for s in reversed(segments):
        q = dict(s)
        if s["type"] in ("line", "arc"):
            q["start"], q["end"] = s["end"], s["start"]
            if s["type"] == "arc":
                q["normal"] = [-float(c) for c in s["normal"]]
        else:
            k = [float(x) for x in s["knots"]]
            c = k[0] + k[-1]
            q["knots"] = [c - x for x in reversed(k)]
            q["control_points"] = list(reversed(s["control_points"]))
            if s.get("weights"):
                q["weights"] = list(reversed(s["weights"]))
        out.append(q)
    return out


def translate_segments(segments, delta):
    """구간 평행이동 — 모양(원호·스플라인)을 그대로 두고 옮긴다."""
    d = _p3(delta)

    def mv(p):
        p = _p3(p)
        return [p[0] + d[0], p[1] + d[1], p[2] + d[2]]

    out = []
    for s in segments:
        q = dict(s)
        for key in ("start", "end", "center"):
            if key in q:
                q[key] = mv(q[key])
        if "control_points" in q:
            q["control_points"] = [mv(p) for p in q["control_points"]]
        out.append(q)
    return out


def polyline_segments(points):
    """3D 점열 → 직선 구간(편집으로 모양이 바뀐 경로)."""
    pts = [_p3(p) for p in points]
    return [{"type": "line", "start": a, "end": b}
            for a, b in zip(pts, pts[1:]) if math.dist(a, b) > _EPS]


def path3d_problems(rec, tol_mm=1e-6):
    """path3d 가 정의대로인가. 빈 목록이면 정상. 구간끼리 끊겨 있으면 그 자리를 말한다."""
    if not rec.get("path3d"):
        return []
    try:
        segs = path3d_segments(rec)
        ends = []
        for s in segs:
            pts = _sample_one(s, 1.0)
            ends.append((pts[0], pts[-1]))
    except (ContractError, KeyError, TypeError, ValueError) as exc:
        return [str(exc)]
    return [f"segment {i} starts {math.dist(ends[i - 1][1], ends[i][0]):.6f} mm from segment {i - 1}"
            for i in range(1, len(ends)) if math.dist(ends[i - 1][1], ends[i][0]) > tol_mm]


# ── 단면 ────────────────────────────────────────────────────────────────────
def mep_section(category, rec, params=None):
    """단면 한 벌 — 모양 · 치수(mm) · roll(라디안). 빌더·다리가 모두 이것을 쓴다."""
    dims = mep_dimensions(category, rec, params)
    ov = rec.get("overrides") or {}
    roll = ov.get("section_roll", rec.get("section_roll", 0.0))
    return dict(dims, shape=section_shape(category, rec), roll=float(roll or 0.0))


def section_axes(direction, roll=0.0):
    """사각 단면의 (폭 축, 높이 축).

    roll 0 에서 폭은 수평이고 연직 구간은 world X 로 떨어진다. 다리가 (x,y,z) → (x,z,−y)
    (Pascal Y-up·북쪽 −Z — 거울상이 아닌 회전)로 옮기면 플러그인 `section.ts` 와 **같은 roll 값이
    같은 단면**이다(tests/test_pascal_host.py 가 좌표로 대조한다)."""
    d = _unit(_p3(direction))
    w0 = _cross(d, _UP)
    w0 = [1.0, 0.0, 0.0] if _norm(w0) < 1e-4 else _unit(w0)
    h0 = _unit(_cross(d, w0))
    c, s = math.cos(roll), math.sin(roll)
    return (_add(_mul(w0, c), _mul(h0, s)), _add(_mul(w0, -s), _mul(h0, c)))


def _rotate_min(v, a, b):
    """a → b 최소 회전을 v 에 적용(로드리게스)."""
    k = _cross(a, b)
    s, c = _norm(k), _dot(a, b)
    if s < 1e-12:
        if c > 0:
            return list(v)
        raise ContractError("route reverses direction (180 degree turn)")
    k = _mul(k, 1.0 / s)
    return _add(_add(_mul(v, c), _mul(_cross(k, v), s)), _mul(k, _dot(k, v) * (1.0 - c)))


def _dedup3(points, tol=1e-6):
    pts = []
    for p in points:
        q = _p3(p)
        if not pts or math.dist(pts[-1], q) > tol:
            pts.append(q)
    return pts


ROUND_SIDES = 24     # 원형 관을 평면 면으로 만들 때의 변 수 — IFC 에서 닫힌 메시로 남는다(부피는 원의 98.9%)


def _profile(width, height, sides=None):
    """단면 윤곽 (u, v) — 사각은 네 모서리(종전 순서 그대로), `sides` 면 내접 정다각형."""
    hw, hh = 0.5 * float(width), 0.5 * float(height)
    if not sides:
        return [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    return [(hw * math.cos(2 * math.pi * k / sides), hh * math.sin(2 * math.pi * k / sides))
            for k in range(int(sides))]


def _ring_points(p, w, h, profile):
    return [_add(p, _add(_mul(w, u), _mul(h, v))) for u, v in profile]


def _rect_ring_list(pts, width, height, roll, closed, sides=None):
    """(내부) 한 조각의 링. 끝은 직각, 꺾인 점은 마이터, closed 면 고리(첫 링을 끝에 한 번 더)."""
    n = len(pts)
    dirs = [_unit(_sub(pts[(i + 1) % n], pts[i])) for i in range(n if closed else n - 1)]
    profile = _profile(width, height, sides)

    def mitre(p, a, b, w, h, along):
        # (w, h) 는 `along`(a 또는 b)에 수직인 단면 — 그 방향으로 이등분면에 투영한다.
        m = _add(a, b)
        if _norm(m) < 1e-9:
            raise ContractError("route reverses direction (180 degree turn)")
        nrm = _unit(m)
        return [_sub(c, _mul(along, _dot(_sub(c, p), nrm) / _dot(along, nrm)))
                for c in _ring_points(p, w, h, profile)]

    w0, h0 = section_axes(dirs[0], roll)
    w, h = w0, h0
    rings = [mitre(pts[0], dirs[-1], dirs[0], w, h, dirs[0]) if closed else _ring_points(pts[0], w, h, profile)]
    for i in range(1, n if closed else n - 1):
        a, b = dirs[i - 1], dirs[i]
        rings.append(mitre(pts[i], a, b, w, h, a))
        w, h = _rotate_min(w, a, b), _rotate_min(h, a, b)
    if closed:
        w, h = _rotate_min(w, dirs[-1], dirs[0]), _rotate_min(h, dirs[-1], dirs[0])
        if math.dist(w, w0) > 1e-6 or math.dist(h, h0) > 1e-6:
            # 비평면 고리는 한 바퀴 돌면 단면이 비틀린 채 돌아온다 — 억지로 잇지 않는다.
            raise ContractError("closed rectangular route twists around its loop")
        rings.append([list(p) for p in rings[0]])
    else:
        rings.append(_ring_points(pts[-1], w, h, profile))
    return rings


def rect_rings(points, width, height, roll=0.0, sides=None):
    """사각 단면을 경로 따라 옮긴 링들(각 4점, mm — `sides` 면 원형 관의 정다각형). 끝은 직각, 꺾인 점은 마이터.

    ★ 단면 방향은 첫 구간의 `section_axes` 를 **최소 회전으로 전달**한다 — 구간마다
      새로 정하면 연직 구간에서 world X 로 떨어져 비틀리고, 그러면 꺾인 점의 두
      단면이 맞지 않아 틈이 난다. 수평 경로에서는 구간마다 정한 것과 같다.
    닫힌 경로(첫 점 == 끝 점)는 모든 꼭짓점을 마이터로 잇는다 — 끝에 뚜껑 둘을 겹쳐
    두면 한 자리에 반대 방향 면이 생긴다. 닫혔다는 표시로 첫 링을 끝에 한 번 더 넣는다.
    짧은 급꺾임에서는 관이 뒤집힐 수 있다 — 빌더는 `rect_parts` 를 쓴다."""
    pts = _dedup3(points)
    if len(pts) < 2:
        return []
    closed = len(pts) > 3 and math.dist(pts[0], pts[-1]) <= 1e-6
    return _rect_ring_list(pts[:-1] if closed else pts, width, height, roll, closed, sides)


def inverted_segments(rings, points, closed=None):
    """옆 모서리 길이가 0 이하가 되는 구간 번호 — 양 끝 마이터 면이 단면 안에서 교차했다."""
    pts = _dedup3(points)
    if closed is None:
        closed = len(pts) > 3 and math.dist(pts[0], pts[-1]) <= 1e-6
    body = pts[:-1] if closed else pts
    n = len(body)
    bad = []
    for i in range(n if closed else n - 1):
        d = _unit(_sub(body[(i + 1) % n], body[i]))
        if any(_dot(_sub(rings[i + 1][k], rings[i][k]), d) <= 1e-6 for k in range(len(rings[i]))):
            bad.append(i)
    return bad


def rect_parts(points, width, height, roll=0.0, sides=None):
    """사각 관을 **뒤집히지 않는 조각들**로 — [링 목록, ...]. 빌더는 이것을 쓴다.

    ★ 짧은 구간에서 급하게 꺾이면 양 끝 마이터 면이 단면 안에서 교차해 관이 뒤집힌다
      (실측: 폭 200mm 덕트가 188mm 구간에서 118°). 그 구간은 앞뒤를 **직각으로 끊은
      독립 토막**으로 만든다 — 그 자리는 실제로 직관이 아니라 피팅이다. 토막을 넘어서도
      단면 방향은 최소 회전으로 이어진다. 조각 길이의 합 = 경로 길이라 기대 부피
      (길이 × 단면적)는 그대로다."""
    pts = _dedup3(points)
    if len(pts) < 2:
        return []
    whole = rect_rings(pts, width, height, roll, sides)
    if not inverted_segments(whole, pts):
        return [whole]
    # 닫힌 고리도 첫 점에서 끊어 열린 꺾은선으로 조각낸다(드문 경우).
    open_rings = _rect_ring_list(pts, width, height, roll, False, sides)
    bad = set(inverted_segments(open_rings, pts, closed=False))
    dirs = [_unit(_sub(b, a)) for a, b in zip(pts, pts[1:])]
    frames = [section_axes(dirs[0], roll)]
    for a, b in zip(dirs, dirs[1:]):
        w, h = frames[-1]
        frames.append((_rotate_min(w, a, b), _rotate_min(h, a, b)))
    profile = _profile(width, height, sides)
    parts, cur = [], None
    for i in range(len(dirs)):
        if i in bad:
            if cur is not None:
                cur.append(_ring_points(pts[i], *frames[i - 1], profile))
                parts.append(cur)
                cur = None
            parts.append([_ring_points(pts[i], *frames[i], profile),
                          _ring_points(pts[i + 1], *frames[i], profile)])
            continue
        if cur is None:
            cur = [_ring_points(pts[i], *frames[i], profile)]
        if i == len(dirs) - 1 or (i + 1) in bad:
            cur.append(_ring_points(pts[i + 1], *frames[i], profile))
            parts.append(cur)
            cur = None
        else:
            cur.append(open_rings[i + 1])          # 마이터 링은 한 조각 계산과 같다
    return parts


def _signed_volume(verts, faces):
    vol = 0.0
    for f in faces:
        a = verts[f[0]]
        for i in range(1, len(f) - 1):
            vol += _dot(a, _cross(verts[f[i]], verts[f[i + 1]])) / 6.0
    return vol


def rect_sweep_mesh(rings):
    """링 → 닫힌 관 메시(verts, faces — 옆면은 사각형, 뚜껑은 링 다각형). 면 방향은 바깥쪽.

    `rect_rings` 가 닫힌 경로로 표시한 링(첫 링 == 끝 링)은 고리로 잇고 뚜껑을 달지 않는다."""
    closed = len(rings) > 2 and all(math.dist(p, q) <= 1e-9 for p, q in zip(rings[0], rings[-1]))
    body = rings[:-1] if closed else rings
    m, n = len(body), len(body[0])
    verts = [list(p) for ring in body for p in ring]
    faces = []
    for i in range(m if closed else m - 1):
        o, q = n * i, n * ((i + 1) % m)
        for k in range(n):
            k2 = (k + 1) % n
            faces.append([o + k, o + k2, q + k2, q + k])
    if not closed:
        last = n * (m - 1)
        faces.append(list(reversed(range(n))))
        faces.append([last + k for k in range(n)])
    if _signed_volume(verts, faces) < 0:
        faces = [list(reversed(f)) for f in faces]
    return verts, faces


# ── 폴리곤 감김 정규화 ─────────────────────────────────────────────────────
def signed_area(pts):
    """양수면 CCW(면 법선 +Z), 음수면 CW."""
    a = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i][0], pts[i][1]
        x2, y2 = pts[(i + 1) % n][0], pts[(i + 1) % n][1]
        a += x1 * y2 - x2 * y1
    return a / 2.0


def beam_footprint(a, b, width, min_len=1.0):
    """축선 한 구간 + 폭 → 평면 사각 footprint(축 중심, CCW). 너무 짧으면 None.

    보는 '축선 + 단면' 으로 온다(`from=dim` + 일람표). 축선을 솔리드로 만드는
    규칙이 빌더·preview·물량산출에 각자 구현되면 D6 이 그대로 재현되므로
    여기에 한 번만 둔다. JS 쪽은 js_constants() 로 같은 식을 주입받는다.
    """
    ax, ay, bx, by = float(a[0]), float(a[1]), float(b[0]), float(b[1])
    dx, dy = bx - ax, by - ay
    L = (dx * dx + dy * dy) ** 0.5
    if L < min_len or width <= 0:
        return None
    nx, ny = -dy / L * width / 2.0, dx / L * width / 2.0
    return ccw([[ax + nx, ay + ny], [bx + nx, by + ny],
                [bx - nx, by - ny], [ax - nx, ay - ny]])


def beam_rings(rec, params=None):
    """보 레코드 → 압출할 평면 링 목록. 닫힌 외곽선은 그대로, 축선은 구간별 사각형."""
    if rec.get("closed"):
        return [rec.get("points") or []]
    w = width_of(rec, params, "beam")
    pts = rec.get("centerline") or rec.get("points") or []
    out = []
    for a, b in zip(pts, pts[1:]):
        fp = beam_footprint(a, b, w)
        if fp:
            out.append(fp)
    return out


def poly_area(pts):
    """닫힌 폴리곤 면적(신발끈, 부호 없음). 단위 변환은 호출측 몫.

    boq_export·ifc_builder·struct_review 가 각자 같은 신발끈을 들고 있었다.
    부호를 잘못 다루면 면적이 음수로 나오는 종류의 실수라 한 곳에 둔다.
    """
    return abs(signed_area(pts)) if len(pts) >= 3 else 0.0


def ccw(pts):
    """닫힌 폴리곤을 CCW(법선 +Z)로 정규화. 이미 CCW면 그대로 반환.

    Arch.makeStructure 가 면 법선 방향으로 압출하므로, CW 폴리곤은 -Z 로 압출돼
    결과가 두께만큼 아래로 밀린다(실측: 슬래브 356개 중 CW 352개 전부 밀림).
    """
    if not pts or len(pts) < 3:
        return pts
    return pts if signed_area(pts) > 0 else list(reversed(pts))


# ── 계약 블록 / 검사 ───────────────────────────────────────────────────────
def contract_block():
    """parse() 가 geometry.json 에 심는 자기기술(self-describing) 블록."""
    return {"version": SCHEMA_VERSION, "z_datum": dict(Z_DATUM)}


def check_contract(data):
    """geometry.json 의 계약 정합성 점검. 문제 문자열 리스트 반환(빈 리스트면 정상)."""
    issues = []
    c = data.get("contract")
    if c:
        v = c.get("version")
        if v not in COMPATIBLE_VERSIONS:
            issues.append(f"계약 버전 불일치: 파일 {v} vs 코드 {SCHEMA_VERSION}")
        for cat, dat in (c.get("z_datum") or {}).items():
            if Z_DATUM.get(cat) != dat:
                issues.append(f"z 기준면 불일치: {cat} 파일={dat} 코드={Z_DATUM.get(cat)}")
    for cat, recs in (data.get("elements") or {}).items():
        if not recs:
            continue
        if cat not in Z_DATUM:
            issues.append(f"알 수 없는 카테고리 버킷: {cat!r} ({len(recs)}개)")
    return issues


# ── preview.py 용 JS 상수 주입 ─────────────────────────────────────────────
def js_constants():
    """preview.py 는 JS 문자열이라 import 가 불가능하다. 규약을 재구현하지 않도록
    이 블록을 주입한다 — D6(preview 가 슬래브를 하단으로 해석) 재발 방지의 핵심."""
    import json as _json
    return (
        "// geom_contract.py 에서 자동 주입 — 이 파일에서 규약을 다시 구현하지 말 것\n"
        f"const Z_DATUM = {_json.dumps(Z_DATUM)};\n"
        f"const DEFAULT_DIMS = {_json.dumps(DEFAULT_DIMS)};\n"
        f"const CONTRACT_VERSION = {SCHEMA_VERSION};\n"
        "const ELEV_CATS = ['pipe','duct','tray','equipment'];\n"
        "function gcDim(cat, key, rec, params){\n"
        "  const ov = (rec && rec.overrides) || {};\n"
        "  if (ov[key] != null) return +ov[key];\n"
        "  if (rec && rec[key] != null) return +rec[key];\n"
        "  if (params && params[cat] && params[cat][key] != null) return +params[cat][key];\n"
        "  return +(((DEFAULT_DIMS[cat]||{})[key]) || 0);\n"
        "}\n"
        f"const MEP_DIM_ALIASES = {_json.dumps(MEP_DIM_ALIASES)};\n"
        f"const ROUND_DIM_ALIASES = {_json.dumps(ROUND_DIM_ALIASES)};\n"
        "function gcSectionShape(cat, rec){\n"
        "  const s = (rec.overrides||{}).section_shape ?? rec.section_shape;\n"
        "  return s == null ? (cat==='pipe'?'round':'rect') : s;\n"
        "}\n"
        "function gcMepDimensions(cat, rec, params){\n"
        "  if (!MEP_DIM_ALIASES[cat] || rec.dimension_status === 'unknown') throw new Error('Unresolved MEP dimensions');\n"
        "  const round = cat !== 'pipe' && gcSectionShape(cat, rec) === 'round';\n"
        "  const aliases = round ? ROUND_DIM_ALIASES : MEP_DIM_ALIASES[cat];\n"
        "  const sources=[rec.overrides||{},rec,(params||{})[cat]||{},round?{}:DEFAULT_DIMS[cat]], out={};\n"
        "  for(const [key,names] of Object.entries(aliases)){\n"
        "    if(key==='width_mm' && rec.geometry_mode==='footprint') continue;\n"
        "    let value; outer: for(const source of sources){ for(const name of names){ if(source[name]!=null){value=source[name];break outer;} } }\n"
        "    if(typeof value==='boolean' || !Number.isFinite(+value) || +value<=0) throw new Error('Invalid MEP dimension: '+key);\n"
        "    out[key]=+value;\n"
        "  } return out;\n"
        "}\n"
        "function gcZRange(cat, rec, params){\n"
        "  const d = Z_DATUM[cat];\n"
        "  const key=ELEV_CATS.includes(cat)?'elevation':'z_base';\n"
        "  const z=+((rec.overrides||{})[key] ?? rec[key] ?? 0);\n"
        "  if (d === 'bottom') return [z, z + gcDim(cat,'height',rec,params)];\n"
        "  if (d === 'top')    return [z - gcDim(cat,'thickness',rec,params), z];\n"
        "  const dims=gcMepDimensions(cat,rec,params);\n"
        "  const half=(dims.diameter!=null?dims.diameter:dims.height_mm)/2;\n"
        "  return [z - half, z + half];\n"
        "}\n"
        # 폭 규약을 JS 가 다시 구현하지 않게 주입한다. 종전엔 preview 가 두 곳에서
        # 서로 다른 순서로 재구현해, thin_pair 벽이 **화면 50mm / 빌드 200mm** 였다.
        f"const UNTRUSTED_WIDTH_REASONS = {_json.dumps(list(UNTRUSTED_WIDTH_REASONS))};\n"
        "function gcWidthOf(rec, params, cat){\n"
        "  cat = cat || 'wall';\n"
        "  if (cat === 'wall'){\n"
        "    const ov = (rec.overrides || {}).width;\n"
        "    if (ov != null) return +ov;\n"
        "    const wd = rec.width_detected;\n"
        "    if (wd && UNTRUSTED_WIDTH_REASONS.indexOf(rec.review_reason) < 0) return +wd;\n"
        "  }\n"
        "  return gcDim(cat, 'width', rec, params);\n"
        "}\n"
        "function gcSignedArea(p){let a=0;for(let i=0;i<p.length;i++){const q=p[(i+1)%p.length];\n"
        "  a += p[i][0]*q[1] - q[0]*p[i][1];} return a/2;}\n"
        "function gcCcw(p){ return (p.length<3 || gcSignedArea(p)>0) ? p : p.slice().reverse(); }\n"
        "function gcBeamRings(rec, params){\n"
        "  if (rec.closed) return [rec.points || []];\n"
        "  const w = gcDim('beam','width',rec,params);\n"
        "  const pts = rec.centerline || rec.points || [];\n"
        "  const out = [];\n"
        "  for (let i=0;i+1<pts.length;i++){\n"
        "    const a=pts[i], b=pts[i+1];\n"
        "    const dx=b[0]-a[0], dy=b[1]-a[1], L=Math.hypot(dx,dy);\n"
        "    if (L < 1 || w <= 0) continue;\n"
        "    const nx=-dy/L*w/2, ny=dx/L*w/2;\n"
        "    out.push(gcCcw([[a[0]+nx,a[1]+ny],[b[0]+nx,b[1]+ny],\n"
        "                    [b[0]-nx,b[1]-ny],[a[0]-nx,a[1]-ny]]));\n"
        "  }\n"
        "  return out;\n"
        "}\n"
    )


if __name__ == "__main__":
    # 자가점검: 각 카테고리 규약이 의도대로 동작하는지
    P = {"wall": {"height": 2800.0}, "slab": {"thickness": 200.0}}
    cases = [
        ("wall",   {"z_base": 6050, "overrides": {"height": 8500}}, (6050, 14550)),
        ("column", {"z_base": 2500, "overrides": {"height": 3550}}, (2500, 6050)),
        ("slab",   {"z_base": 6050, "overrides": {"thickness": 200}}, (5850, 6050)),
        ("beam",   {"z_base": 6050, "overrides": {"thickness": 1200}}, (4850, 6050)),
        ("pipe",   {"elevation": 2600, "diameter": 100}, (2550, 2650)),
        ("duct",   {"elevation": 2800, "height_mm": 300}, (2650, 2950)),
        ("equipment", {"elevation": 0}, (0, 1000)),
    ]
    bad = 0
    for cat, rec, want in cases:
        got = z_range(cat, rec, P)
        ok = abs(got[0] - want[0]) < 1e-6 and abs(got[1] - want[1]) < 1e-6
        bad += 0 if ok else 1
        print(("  OK  " if ok else " FAIL ") + f"{cat:10s} {got} (기대 {want})")
    sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert signed_area(sq) > 0 and ccw(sq) == sq, "CCW 판정 오류"
    assert ccw(list(reversed(sq))) == sq, "CW→CCW 반전 오류"
    print("  OK   ccw() 정규화")
    print("PASS" if bad == 0 else f"FAIL {bad}건")
    raise SystemExit(1 if bad else 0)
