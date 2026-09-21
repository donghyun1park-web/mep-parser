"""간섭 검토 목록 — 구조체와 설비가 겹치는 자리를 **위치·부재·조치**로 한 줄씩 낸다.

FreeCAD `check_clashes` 는 객체 이름과 부피만 준다(`Wall_21 ↔ Duct_14 · 244803 mm³`). 현장 담당자에게는 어디를
보라는 말이 없고 검토 목록에도 안 뜬다. 여기서는 평면 교차 + 높이 범위(2.5D)로 교차 한 곳마다 한 줄을 만든다 —
벽·기둥·슬래브·보는 수직 압출, 설비는 `route_points` 구간마다 높이 범위를 가진 띠라서 수직·경사 구간도 구간별로
맞는다. 폭·높이·경로는 빌더와 같은 규약(`width_of`·`z_range`·`mep_section`·`route_points`·`beam_rings`)만 쓴다.

조치 구분은 형상이 말해 주는 것으로만 가른다(레이어 이름을 추측하지 않는다). 문·창 개구부 안을 지나는 것은
간섭이 아니므로 목록에서 빼고 `through_openings` 로 센다. 형상을 못 만든 레코드도 `skipped_*` 로 센다."""
import hashlib
import math

from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union
from shapely.strtree import STRtree

import construction_rules as CR
import geom_contract as GC

STRUCT_CATS = ("wall", "column", "slab", "beam")
ROUTE_CATS = ("pipe", "duct", "tray")
THIN_WALL_MM = 50.0       # 이보다 얇은 '벽' 은 면선·마감선 오결합일 가능성이 크다(실측: 폭 10mm 칸막이 40개)
FLOOR_EMBED_MM = 300.0    # 벽 바닥에서 이 높이 안에서 끝나는 설비는 바닥 매립(난방 코일 z 70~86)
MIN_AREA_MM2 = 1.0
MIN_Z_MM = 0.5

SLEEVE_MARGIN_MM = 20.0   # 슬리브 외곽에서 이 안이면 그 슬리브 자리로 본다(중심선·외곽선 제도 오차)
# 파서가 **위치나 두께를 확신하지 못하는** 벽 — 그 위에서 센 간섭은 그만큼만 믿을 수 있다.
# `needs_review` 전체를 쓰지 않는다: 실무 프로젝트는 건축 레이어 분류 확인으로 벽이 통째로 검토 대상이라
# (실측: 통합 모델 24건 전부 `project_architecture_classification`) 그걸로 세면 매번 24/24 가 되어 아무
# 말도 안 하는 것과 같다. 여기서 보는 것은 **기하의 불확실성**이다.
UNCERTAIN_PAIRINGS = ("single_offset", "manual")   # 축선 자체가 추정이거나 사람이 그린 것

ACTIONS = {
    "slab_penetration": "슬래브 관통 — 슬리브·방수 확인",
    "structure_penetration": "기둥·보 관통 — 경로 변경 검토",
    "wall_penetration": "벽 관통 — 슬리브·개구 확인",
    "sleeve_provided": "슬리브 자리 관통 — 도면의 슬리브 규격·위치와 대조",
    "sleeve_undersized": "슬리브 자리 관통 — 도면 슬리브가 외경+40mm 미만(KCS 31 20 15 2.2.20)",
    "under_wall": "바닥 매립 설비가 벽 아래를 지남 — 문 하부 경로·벽 선시공 여부 확인",
    "suspect_thin_wall": "벽 두께가 비정상(면선 오결합 의심) — 도면 확인",
}
LABELS = {"slab_penetration": "슬래브 관통", "structure_penetration": "기둥·보 관통", "wall_penetration": "벽 관통",
          "sleeve_provided": "슬리브 관통", "sleeve_undersized": "슬리브 규격 부족",
          "under_wall": "벽 하부 통과", "suspect_thin_wall": "두께 의심 벽"}


def _uncertain_wall(category, rec):
    """이 벽의 위치·두께를 파서가 확신하는가. 확신하지 못하면 그 위에서 센 간섭도 그만큼만 믿는다."""
    if category != "wall":
        return False
    return (rec.get("pairing") in UNCERTAIN_PAIRINGS
            or rec.get("review_reason") in GC.UNTRUSTED_WIDTH_REASONS)


def _prisms(geometry):
    params, out, skipped = geometry.get("params"), [], 0
    for cat in STRUCT_CATS:
        for rec in (geometry.get("elements") or {}).get(cat) or []:
            try:
                z0, z1 = GC.z_range(cat, rec, params)
                width = None
                if cat == "wall":
                    width = GC.width_of(rec, params, "wall")
                    if rec.get("closed") and len(rec.get("points") or []) >= 3:
                        polys = [Polygon([p[:2] for p in rec["points"]])]
                    else:
                        axis = [p[:2] for p in (rec.get("centerline") or rec.get("points") or [])]
                        polys = [LineString(axis).buffer(width / 2, cap_style=2, join_style=2)]
                elif cat == "beam":
                    polys = [Polygon([p[:2] for p in ring]) for ring in GC.beam_rings(rec, params) if len(ring) >= 3]
                elif rec.get("kind") == "circle":
                    polys = [Point(rec["center"][:2]).buffer(float(rec["radius"]))]
                else:
                    polys = [Polygon([p[:2] for p in rec.get("points") or []])]
                poly = unary_union([p.buffer(0) for p in polys])
            except Exception:
                poly = None
            if poly is None or poly.is_empty or not z1 > z0:
                skipped += 1
                continue
            out.append({"rec": rec, "category": cat, "poly": poly, "z": (z0, z1), "width": width,
                        "basis": GC.height_basis(cat, rec, params)})
    return out, skipped


def _voids(geometry):
    """문·창 개구부의 빈 공간(평면 사각형 × 문턱~문틀 높이). 빌더 `build_openings` 와 같은 사각형이다."""
    out = []
    for o in (geometry.get("elements") or {}).get("opening") or []:
        if not (o.get("host_dir") and o.get("center") and o.get("width") and o.get("height")):
            continue
        dx, dy = o["host_dir"][:2]
        n = math.hypot(dx, dy)
        if n < 1e-9:
            continue
        dx, dy = dx / n, dy / n
        cx, cy = o["center"][:2]
        hw, hd = float(o["width"]) / 2, float(o.get("host_width") or 200) / 2 + 20
        ring = [(cx + dx * a - dy * b, cy + dy * a + dx * b) for a, b in ((-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd))]
        lo = GC.base_z("opening", o) + float(o.get("sill") or 0)
        # 이 개구부의 높이·문턱이 가정이면, '개구부 안을 지나니 간섭 아님' 이라는 판정도 그 가정에 기댄다.
        out.append((Polygon(ring), lo, lo + float(o["height"]),
                    bool(set(o.get("dims_assumed") or ()) & {"height", "sill"})))
    return out


def _level_slabs(geometry):
    """층 높이 선언에서 **바닥·천장 슬래브**를 합성한다 → `_prisms` 와 같은 모양의 항목들.

    ★ 평면도에는 슬래브·보 몸체가 없다. 그래서 '덕트 상단 = 슬래브 밑면' 같은 설치 조건은 선언일 뿐
      아무도 검사하지 않았고, 천장 쪽 간섭은 **판정 대상 밖**이었다. 프로필의 층 높이·슬래브 두께가
      있으면 그 두 장만 간섭 계산에 세운다 — **모델·IFC·물량에는 넣지 않는다**(도면에 없는 부재다).
      선언이 없으면 아무것도 만들지 않는다(추정하지 않는다).
    """
    sources = []
    profile = geometry.get("mep_profile") or {}
    if profile.get("levels"):
        sources.append((profile["levels"], profile.get("region"), None))
    for level in (geometry.get("stack") or {}).get("levels") or []:
        if level.get("levels"):
            sources.append((level["levels"], level.get("region"), level.get("id")))
    out, seen = [], set()
    for levels, region, level_id in sources:
        thickness = levels.get("slab_thickness_mm")
        floor_to_floor = levels.get("floor_to_floor_mm")
        if not thickness or not floor_to_floor:
            continue
        top = float(levels.get("structural_slab_top_mm") or 0.0)
        bounds = (region or {}).get("bounds_mm") or _plan_bounds(geometry)
        if not bounds:
            continue
        key = (round(top, 1), round(float(thickness), 1), round(float(floor_to_floor), 1), tuple(round(v, 1) for v in bounds))
        if key in seen:
            continue                      # 같은 층을 원본마다 다시 세우지 않는다
        seen.add(key)
        poly = box(*bounds)
        for role, slab_top in (("바닥", top), ("천장", top + float(floor_to_floor))):
            out.append({"rec": {"eid": None, "layer": "(층 높이 선언)", "level": level_id},
                        "category": "slab", "poly": poly, "z": (slab_top - float(thickness), slab_top),
                        "width": None, "synthetic": role,
                        "basis": {"z": "declared", "assumed": []}})
    return out


def _plan_bounds(geometry):
    xs, ys = [], []
    for recs in (geometry.get("elements") or {}).values():
        for rec in recs:
            for p in (rec.get("points") or []):
                xs.append(float(p[0])); ys.append(float(p[1]))
    return [min(xs), min(ys), max(xs), max(ys)] if xs else None


def _sleeves(geometry):
    """슬리브 자리(프로필 `role: sleeve`)의 평면 + 실측 규격. **부재가 아니라 판정 근거**다 — 도면이
    "여기는 뚫어 뒀다"고 말한 자리라 같은 관통이라도 조치가 다르다(슬리브 없는 관통은 새로 뚫어야
    한다). `size_mm` 은 그려진 슬리브의 최소 변(원은 지름) — `sleeve-diameter-from-pipe-and-insulation`
    이 이 값을 배관 외경+40mm 와 대조한다(`clash_review.find_clashes`)."""
    out = []
    for rec in (geometry.get("elements") or {}).get("equipment") or []:
        if (rec.get("role") or (rec.get("overrides") or {}).get("role")) != "sleeve":
            continue
        pts = [p[:2] for p in rec.get("points") or []]
        if len(pts) >= 3:
            poly = Polygon(pts).buffer(0)
            if not poly.is_empty:
                mrr = poly.minimum_rotated_rectangle.exterior.coords
                size_mm = min(math.dist(mrr[i], mrr[i + 1]) for i in range(len(mrr) - 1))
                out.append({"poly": poly.buffer(SLEEVE_MARGIN_MM), "size_mm": size_mm})
    return out


def _bands(cat, rec, params, use_envelope=False):
    """설비 경로 → 구간별 (평면 띠, 축선, 아래 z, 위 z) + (규격 문자열, 보온 외피 반영 여부).
    원형은 폭 = 지름. `use_envelope` 는 프로필 `rules.insulation_envelope: true` 일 때만 참이고,
    그때만 `rec['insulation_mm']`(있으면) 만큼 띠가 넓어진다 — 슬리브 대조(`required_sleeve_mm`)와
    같은 `geom_contract.mep_envelope` 를 쓴다."""
    sec = GC.mep_envelope(cat, rec, params) if use_envelope else GC.mep_section(cat, rec, params)
    envelope = bool(sec.get("insulated"))
    if sec.get("diameter"):
        half_w = half_h = float(sec["diameter"]) / 2
        size = "Ø%g" % sec["diameter"]
    else:
        half_w, half_h = float(sec["width_mm"]) / 2, float(sec["height_mm"]) / 2
        size = "%g×%g" % (sec["width_mm"], sec["height_mm"])
    pts = GC.route_points(cat, rec)
    bands = []
    for a, b in zip(pts, pts[1:]):
        if math.dist(a[:2], b[:2]) < 1e-6:                   # 수직 구간 — 단면이 평면에 그대로 찍힌다
            r = max(half_w, half_h)
            band, axis = box(a[0] - r, a[1] - r, a[0] + r, a[1] + r), None
        else:
            axis = LineString([a[:2], b[:2]])
            band = axis.buffer(half_w, cap_style=2, join_style=2)
        bands.append((band, axis, min(a[2], b[2]) - half_h, max(a[2], b[2]) + half_h))
    return bands, size, envelope


def _kind(prism, z1):
    if prism["category"] == "slab":
        return "slab_penetration"
    if prism["category"] in ("column", "beam"):
        return "structure_penetration"
    if prism["width"] is not None and prism["width"] < THIN_WALL_MM:
        return "suspect_thin_wall"
    rec = prism.get("rec") or {}
    # '벽 밑을 지나는 바닥 매립 배관' 은 **바닥에 선 벽**에서만 말이 된다 — 창 위 벽(인방)의 아랫면은 바닥이 아니다
    if z1 - prism["z"][0] <= FLOOR_EMBED_MM and GC.base_z("wall", rec) - GC.floor_z("wall", rec) < 1.0:
        return "under_wall"
    return "wall_penetration"


def find_clashes(geometry):
    """geometry.json dict → {"items": [...], "summary": {...}}. 입력을 바꾸지 않는다."""
    params = geometry.get("params")
    # opt-in: 켜지면 간섭 폭이 보온 외피만큼 넓어져 간섭 수가 늘 수 있다(사용자 확인 사항 — 기본 꺼짐).
    use_envelope = bool(((geometry.get("mep_profile") or {}).get("rules") or {}).get("insulation_envelope"))
    prisms, skipped_structures = _prisms(geometry)
    prisms += _level_slabs(geometry)       # 도면에 몸체가 없는 바닥·천장 — 간섭 계산에만 선다
    voids = _voids(geometry)
    sleeves = _sleeves(geometry)
    tree = STRtree([p["poly"] for p in prisms]) if prisms else None
    items, through, through_assumed, skipped_routes = [], 0, 0, 0
    for cat in ROUTE_CATS:
        for rec in (geometry.get("elements") or {}).get(cat) or []:
            try:
                bands, size, envelope = _bands(cat, rec, params, use_envelope)
            except Exception:
                skipped_routes += 1
                continue
            mep_basis = GC.height_basis(cat, rec, params)
            hits = {}
            for band, axis, lo, hi in bands:
                for k in (tree.query(band) if tree is not None else ()):
                    prism = prisms[int(k)]
                    if min(hi, prism["z"][1]) - max(lo, prism["z"][0]) <= MIN_Z_MM:
                        continue
                    piece = band.intersection(prism["poly"])
                    if piece.area <= MIN_AREA_MM2:
                        continue
                    inside = axis.intersection(prism["poly"]).length if axis is not None else 0.0
                    hits.setdefault(int(k), []).append((piece, inside, max(lo, prism["z"][0]), min(hi, prism["z"][1]), hi))
            for k, pieces in hits.items():
                prism = prisms[k]
                merged = unary_union([p[0] for p in pieces])
                for part in getattr(merged, "geoms", [merged]):   # 한 부재를 두 번 지나면 두 줄이다
                    members = [p for p in pieces if p[0].intersects(part)]
                    z0, z1 = min(p[2] for p in members), max(p[3] for p in members)
                    at = part.representative_point() if not part.centroid.within(part.buffer(1)) else part.centroid
                    inside = [v_assumed for v, v_lo, v_hi, v_assumed in voids
                              if v.contains(at) and v_lo <= z0 and z1 <= v_hi]
                    if inside:
                        through += 1
                        through_assumed += any(inside)
                        continue
                    kind = _kind(prism, max(p[4] for p in members))
                    rule_info = None
                    if kind == "wall_penetration":
                        matched = next((s for s in sleeves if s["poly"].contains(at)), None)
                        if matched is not None:
                            required = CR.required_sleeve_mm(cat, rec, params)
                            if required is not None and matched["size_mm"] < required:
                                kind = "sleeve_undersized"
                                rule_info = {"id": "sleeve-diameter-from-pipe-and-insulation",
                                            "clause": "KCS 31 20 15 2.2.20",
                                            "required_mm": round(required, 1),
                                            "measured_mm": round(matched["size_mm"], 1)}
                            else:
                                kind = "sleeve_provided"
                    struct = prism["rec"]
                    assumed = sorted(set(prism["basis"]["assumed"] + mep_basis["assumed"]))
                    action = ACTIONS[kind]
                    if prism.get("synthetic"):
                        action = f"{prism['synthetic']} 슬래브(층 높이 선언에서 합성) 관통 — 설치 높이 확인"
                    key = "|".join([str(struct.get("eid")), str(rec.get("eid")), "%.0f" % at.x, "%.0f" % at.y])
                    items.append({
                        "id": "clash:" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12],
                        "kind": kind, "action": action,
                        "at": [round(at.x, 1), round(at.y, 1)], "z": [round(z0, 1), round(z1, 1)],
                        "crossing_mm": round(sum(p[1] for p in members), 1), "area_mm2": round(part.area, 1),
                        "level": rec.get("level") or struct.get("level"),
                        **({"rule": rule_info} if rule_info else {}),
                        # 이 줄의 높이가 도면에서 온 것인지 가정인지 — 판정과 **같이** 보여야 한다.
                        "basis": "assumed" if assumed else "declared", "assumed": assumed,
                        # 벽이 간섭 품질의 바닥이다 — 그 줄이 선 벽을 파서가 어떻게 잡았는지 같이 싣는다
                        # (`single_offset` 은 축선 자체가 추정이라 위치도 두께도 추정이다).
                        "struct": dict({"eid": struct.get("eid"), "category": prism["category"],
                                        "layer": struct.get("layer"),
                                        "width_mm": None if prism["width"] is None else round(prism["width"], 1),
                                        "z_basis": prism["basis"]["z"], "synthetic": prism.get("synthetic")},
                                       **{k: v for k, v in (
                                           ("pairing", struct.get("pairing")),
                                           ("review_reason", struct.get("review_reason")),
                                           ("uncertain", _uncertain_wall(prism["category"], struct) or None))
                                          if v}),
                        "mep": dict({"eid": rec.get("eid"), "category": cat, "size": size, "layer": rec.get("layer"),
                                    "system": rec.get("system") or (rec.get("overrides") or {}).get("system"),
                                    "z_basis": mep_basis["z"]},
                                   **({"envelope": "insulated"} if envelope else {})),
                    })
    order = list(ACTIONS)
    items.sort(key=lambda c: (order.index(c["kind"]), c["at"][1], c["at"][0], c["id"]))
    by_kind = {}
    for c in items:
        by_kind[c["kind"]] = by_kind.get(c["kind"], 0) + 1
    return {"items": items,
            "summary": {"total": len(items), "by_kind": by_kind, "through_openings": through,
                        "through_openings_assumed": through_assumed,
                        "assumed_basis": sum(1 for c in items if c["basis"] == "assumed"),
                        "on_uncertain_walls": sum(1 for c in items if c["struct"].get("uncertain")),
                        "skipped_structures": skipped_structures, "skipped_routes": skipped_routes,
                        "envelope_basis": sum(1 for c in items if c["mep"].get("envelope"))},
            "method": "2.5D plan intersection with z ranges (geom_contract widths, elevations, routes)"}


CSV_COLUMNS = ("id", "kind", "label", "action", "level", "x_mm", "y_mm", "z0_mm", "z1_mm", "crossing_mm",
               "struct_eid", "struct_category", "struct_layer", "struct_width_mm",
               "struct_pairing", "struct_review_reason",
               "mep_eid", "mep_category", "mep_system", "mep_size", "basis", "assumed",
               "rule_id", "required_mm", "measured_mm")


def to_rows(review):
    """간섭 목록 → 표 한 장. 열은 고정이고 없는 값은 빈칸이다(합성 슬래브는 EID 가 없다)."""
    rows = []
    for item in (review or {}).get("items") or []:
        struct, mep = item.get("struct") or {}, item.get("mep") or {}
        at, z = item.get("at") or [None, None], item.get("z") or [None, None]
        rows.append({
            "id": item.get("id"), "kind": item.get("kind"), "label": LABELS.get(item.get("kind"), item.get("kind")),
            "action": item.get("action"), "level": item.get("level"),
            "x_mm": at[0], "y_mm": at[1], "z0_mm": z[0], "z1_mm": z[1], "crossing_mm": item.get("crossing_mm"),
            "struct_eid": struct.get("eid"), "struct_category": struct.get("category"),
            # 합성 슬래브는 도면에 없는 부재라 EID 가 없다 — 빈칸 대신 출처를 적는다.
            "struct_layer": struct.get("layer") or struct.get("synthetic"),
            "struct_width_mm": struct.get("width_mm"),
            "struct_pairing": struct.get("pairing"), "struct_review_reason": struct.get("review_reason"),
            "mep_eid": mep.get("eid"), "mep_category": mep.get("category"), "mep_system": mep.get("system"),
            "mep_size": mep.get("size"), "basis": item.get("basis"),
            "assumed": " ".join(item.get("assumed") or ()),
            "rule_id": (item.get("rule") or {}).get("id"),
            "required_mm": (item.get("rule") or {}).get("required_mm"),
            "measured_mm": (item.get("rule") or {}).get("measured_mm"),
        })
    return rows


def write_csv(path, rows, columns):
    """표 한 장을 CSV 로. `utf-8-sig` 라 Excel 이 바로 연다(물량 CSV 와 같은 규약)."""
    import csv
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for row in rows:
            writer.writerow(["" if row.get(key) is None else row.get(key) for key in columns])
    return str(path), len(rows)


def main(argv=None):
    """간섭·연결 목록을 CSV 로 낸다 — 일상 검토는 여기서 끝난다(FreeCAD 빌드는 납품 검증용).

    `python clash_review.py geometry.json -o clash.csv [--connectivity conn.csv]`"""
    import argparse
    import json
    parser = argparse.ArgumentParser(description="간섭 검토 목록 → CSV")
    parser.add_argument("geometry", help="geometry.json")
    parser.add_argument("-o", "--out", help="간섭 목록 CSV (기본: <geometry>_clash.csv)")
    parser.add_argument("--connectivity", nargs="?", const=True,
                        help="설비 연결 목록도 CSV 로 (기본: <geometry>_connectivity.csv)")
    parser.add_argument("--rules", nargs="?", const=True,
                        help="시공기준 지지·청소구 목록도 CSV 로 (기본: <geometry>_rules.csv)")
    args = parser.parse_args(argv)
    with open(args.geometry, encoding="utf-8") as handle:
        data = json.load(handle)
    stem = args.geometry.rsplit(".", 1)[0]
    review = data.get("clash_review") or find_clashes(data)
    path, count = write_csv(args.out or stem + "_clash.csv", to_rows(review), CSV_COLUMNS)
    print("간섭 %d건 -> %s" % (count, path))
    summary = review.get("summary") or {}
    if summary.get("assumed_basis"):
        print("  가정 높이에 기댄 줄 %d건 — 설비 설정에서 설치 높이·규격을 선언하면 줄어듭니다"
              % summary["assumed_basis"])
    if args.connectivity:
        import mep_network
        net = data.get("mep_connectivity") or mep_network.analyze(data)
        out = args.connectivity if isinstance(args.connectivity, str) else stem + "_connectivity.csv"
        path, count = write_csv(out, mep_network.to_rows(net), mep_network.CSV_COLUMNS)
        print("연결 목록 %d행 -> %s" % (count, path))
    if args.rules:
        import construction_rules
        out = args.rules if isinstance(args.rules, str) else stem + "_rules.csv"
        support_rows = construction_rules.supports(data)
        path, count = write_csv(out, construction_rules.to_rows(support_rows), construction_rules.CSV_COLUMNS)
        print("시공기준 지지·청소구 %d행 -> %s" % (count, path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
