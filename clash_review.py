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

import geom_contract as GC

STRUCT_CATS = ("wall", "column", "slab", "beam")
ROUTE_CATS = ("pipe", "duct", "tray")
THIN_WALL_MM = 50.0       # 이보다 얇은 '벽' 은 면선·마감선 오결합일 가능성이 크다(실측: 폭 10mm 칸막이 40개)
FLOOR_EMBED_MM = 300.0    # 벽 바닥에서 이 높이 안에서 끝나는 설비는 바닥 매립(난방 코일 z 70~86)
MIN_AREA_MM2 = 1.0
MIN_Z_MM = 0.5

ACTIONS = {
    "slab_penetration": "슬래브 관통 — 슬리브·방수 확인",
    "structure_penetration": "기둥·보 관통 — 경로 변경 검토",
    "wall_penetration": "벽 관통 — 슬리브·개구 확인",
    "under_wall": "바닥 매립 설비가 벽 아래를 지남 — 문 하부 경로·벽 선시공 여부 확인",
    "suspect_thin_wall": "벽 두께가 비정상(면선 오결합 의심) — 도면 확인",
}
LABELS = {"slab_penetration": "슬래브 관통", "structure_penetration": "기둥·보 관통", "wall_penetration": "벽 관통",
          "under_wall": "벽 하부 통과", "suspect_thin_wall": "두께 의심 벽"}


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


def _bands(cat, rec, params):
    """설비 경로 → 구간별 (평면 띠, 축선, 아래 z, 위 z). 원형은 폭 = 지름."""
    sec = GC.mep_section(cat, rec, params)
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
    return bands, size


def _kind(prism, z1):
    if prism["category"] == "slab":
        return "slab_penetration"
    if prism["category"] in ("column", "beam"):
        return "structure_penetration"
    if prism["width"] is not None and prism["width"] < THIN_WALL_MM:
        return "suspect_thin_wall"
    if z1 - prism["z"][0] <= FLOOR_EMBED_MM:
        return "under_wall"
    return "wall_penetration"


def find_clashes(geometry):
    """geometry.json dict → {"items": [...], "summary": {...}}. 입력을 바꾸지 않는다."""
    params = geometry.get("params")
    prisms, skipped_structures = _prisms(geometry)
    voids = _voids(geometry)
    tree = STRtree([p["poly"] for p in prisms]) if prisms else None
    items, through, through_assumed, skipped_routes = [], 0, 0, 0
    for cat in ROUTE_CATS:
        for rec in (geometry.get("elements") or {}).get(cat) or []:
            try:
                bands, size = _bands(cat, rec, params)
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
                    struct = prism["rec"]
                    assumed = sorted(set(prism["basis"]["assumed"] + mep_basis["assumed"]))
                    key = "|".join([str(struct.get("eid")), str(rec.get("eid")), "%.0f" % at.x, "%.0f" % at.y])
                    items.append({
                        "id": "clash:" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12],
                        "kind": kind, "action": ACTIONS[kind],
                        "at": [round(at.x, 1), round(at.y, 1)], "z": [round(z0, 1), round(z1, 1)],
                        "crossing_mm": round(sum(p[1] for p in members), 1), "area_mm2": round(part.area, 1),
                        "level": rec.get("level") or struct.get("level"),
                        # 이 줄의 높이가 도면에서 온 것인지 가정인지 — 판정과 **같이** 보여야 한다.
                        "basis": "assumed" if assumed else "declared", "assumed": assumed,
                        "struct": {"eid": struct.get("eid"), "category": prism["category"], "layer": struct.get("layer"),
                                   "width_mm": None if prism["width"] is None else round(prism["width"], 1),
                                   "z_basis": prism["basis"]["z"]},
                        "mep": {"eid": rec.get("eid"), "category": cat, "size": size, "layer": rec.get("layer"),
                                "system": rec.get("system") or (rec.get("overrides") or {}).get("system"),
                                "z_basis": mep_basis["z"]},
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
                        "skipped_structures": skipped_structures, "skipped_routes": skipped_routes},
            "method": "2.5D plan intersection with z ranges (geom_contract widths, elevations, routes)"}
