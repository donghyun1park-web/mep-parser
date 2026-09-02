# -*- coding: utf-8 -*-
"""geometry.json 기하 계약(contract) — z 기준면 규약이 존재하는 **유일한 장소**.

이 저장소에서 z 기준면 규약은 과거 4곳(freecad_builder / preview / anim_4d /
struct_review)에 각자 구현돼 있었고, 그중 preview 가 슬래브를 '하단' 기준으로
해석해 보/슬래브가 한 두께 떠 보이는 버그가 났다. 더 나쁜 것은 그 증상을
"원인 미상"으로 두고 z_base 에 +thickness 를 더하는 보정을 데이터에 적용한 것이다.
보정값이 JSON 에 남아 다른 소비자를 연쇄 오염시켰다.

따라서 규약은 여기에만 둔다. 소비자는 `z_range()` 만 호출한다.
FreeCAD·shapely 의존 없음(순수 표준 라이브러리) — 그래야 단위테스트가 가능하다.

    from geom_contract import z_range, ccw
    z0, z1 = z_range("slab", rec, params)
"""

SCHEMA_VERSION = 2

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


def base_z(category, rec):
    """해당 카테고리가 쓰는 기준 z 값. MEP 는 elevation, 나머지는 z_base."""
    key = "elevation" if category in _ELEV_CATS else "z_base"
    return float(rec.get(key, 0.0) or 0.0)


def thickness_of(rec, params=None, category="slab"):
    return _dim(category, "thickness", rec, params)


def height_of(rec, params=None, category="wall"):
    return _dim(category, "height", rec, params)


def width_of(rec, params=None, category="wall"):
    """평면상 폭. 벽은 width_detected 를 우선 신뢰한다(실측값)."""
    if category == "wall":
        ov = (rec.get("overrides") or {}).get("width")
        if ov is not None:
            return float(ov)
        wd = rec.get("width_detected")
        if wd:
            return float(wd)
    return _dim(category, "width", rec, params)


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

    # axis — 단면 중심이 elevation
    if category == "pipe":
        half = _dim("pipe", "diameter", rec, params) / 2.0
    else:  # duct / tray
        half = _dim(category, "height_mm", rec, params) / 2.0
    return (z - half, z + half)


def z_range_for(data, category, rec):
    """geometry.json 전체를 받아 params 를 자동으로 꺼내 쓰는 편의 함수."""
    return z_range(category, rec, data.get("params"))


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
        if v != SCHEMA_VERSION:
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
        "function gcZRange(cat, rec, params){\n"
        "  const d = Z_DATUM[cat];\n"
        "  const z = +((ELEV_CATS.indexOf(cat)>=0 ? rec.elevation : rec.z_base) || 0);\n"
        "  if (d === 'bottom') return [z, z + gcDim(cat,'height',rec,params)];\n"
        "  if (d === 'top')    return [z - gcDim(cat,'thickness',rec,params), z];\n"
        "  const half = (cat === 'pipe') ? gcDim('pipe','diameter',rec,params)/2\n"
        "                                : gcDim(cat,'height_mm',rec,params)/2;\n"
        "  return [z - half, z + half];\n"
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
