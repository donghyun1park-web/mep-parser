# -*- coding: utf-8 -*-
"""geometry.json ↔ Pascal(pascalorg/editor) 씬 그래프 — **벽만**(0단계 다리).

Pascal 은 벽을 `start`/`end` 축선 + `thickness`/`height` 로 갖는다. 우리와 같은
모형이지만 단위가 **미터**고, 고저는 부재가 아니라 **레벨**이 들고 있다.
그래서 이 다리가 하는 일은 셋뿐이다:

1. mm ↔ m  — 환산식은 `geom_contract` 에만 있다(여기서 /1000 을 쓰지 않는다).
2. z_base(부재별 절대 고저) ↔ 레벨 스택(`level`·`baseElevation`·`height`).
3. Pascal 이 표현할 수 없는 것을 **세어서 보고**한다 — 조용히 버리지 않는다.

★ 좌표는 metadata 에 넣지 않는다. 넣으면 왕복이 통과해도 그건 컨테이너가
  통과한 것이지 변환이 통과한 게 아니다. metadata 는 출처(eid·layer·검토사유)만.

Pascal 쪽 계약(읽고 고정한 값, `packages/core/src`):
  base.ts        id = `<prefix>_<16 chars of [0-9a-z]>`, parentId nullable, metadata record
  wall.ts        start/end: [x, y] (레벨 좌표), thickness/height: 미터(optional)
  level.ts       level(서수) · baseElevation(m, **누적 위에 더하는 오프셋**) · height(m)
  storey.ts      baseY_i = (직전 baseY + 직전 height) + baseElevation_i, level 오름차순
  ifc-converter  site → building → level → wall, parentId 와 부모 children **양쪽** 기록
"""

import hashlib
import math

import geom_contract as GC

# packages/core/src/systems/wall/wall-footprint.ts
PASCAL_DEFAULT_WALL_THICKNESS_M = 0.1
PASCAL_DEFAULT_WALL_HEIGHT_M = 2.5

# 닫힌 폴리선 벽을 직사각형으로 인정할 허용치(mm·도)
RECT_TOL_MM = 1.0
RECT_TOL_DEG = 1.0

# metadata 에 싣는 출처 필드 — **치수·좌표는 없다**
_PROVENANCE = ("eid", "eid_v1", "layer", "pairing", "confidence", "needs_review",
               "review_reason", "width_detected", "overrides", "zone",
               "source_signatures", "handle", "subtype", "source")


def _nid(prefix, *parts):
    """결정론적 노드 id. Pascal 의 nanoid 와 같은 형식(16자 [0-9a-z])이되 난수가
    아니다 — 같은 도면을 두 번 변환하면 같은 씬이어야 diff 가 의미를 갖는다."""
    h = hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return "%s_%s" % (prefix, h[:16])


def _node(nid, ntype, parent, **extra):
    n = {"object": "node", "id": nid, "type": ntype, "parentId": parent,
         "visible": True, "metadata": {}}
    n.update(extra)
    return n


# ── 닫힌 폴리선 벽 ↔ 축선 ───────────────────────────────────────────────────
# 직사각형 footprint 는 Pascal 벽과 **같은 형상**이다(축선 ± 두께/2). 그래서
# 왕복이 무손실이다. 사다리꼴 등은 Pascal WallNode 로 표현할 방법이 없다.
def _rect_axis(pts):
    """닫힌 폴리선이 직사각형이면 (centerline, thickness_mm), 아니면 None."""
    p = [list(q) for q in pts]
    if len(p) > 1 and math.dist(p[0], p[-1]) <= 1e-6:
        p = p[:-1]
    if len(p) != 4:
        return None
    side = [math.dist(p[i], p[(i + 1) % 4]) for i in range(4)]
    if abs(side[0] - side[2]) > RECT_TOL_MM or abs(side[1] - side[3]) > RECT_TOL_MM:
        return None
    for i in range(4):
        a, b, c = p[i - 1], p[i], p[(i + 1) % 4]
        v1 = (a[0] - b[0], a[1] - b[1])
        v2 = (c[0] - b[0], c[1] - b[1])
        n1, n2 = math.hypot(*v1), math.hypot(*v2)
        if n1 == 0 or n2 == 0:
            return None
        cosang = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
        if abs(math.degrees(math.acos(cosang)) - 90.0) > RECT_TOL_DEG:
            return None
    # 짧은 변 두 개의 중점을 잇는 것이 축선, 짧은 변 길이가 두께
    if side[0] <= side[1]:
        a, b, t = (p[0], p[1]), (p[2], p[3]), side[0]
    else:
        a, b, t = (p[1], p[2]), (p[3], p[0]), side[1]

    def mid(q):
        return [(q[0][0] + q[1][0]) / 2.0, (q[0][1] + q[1][1]) / 2.0]

    return [mid(a), mid(b)], t


def _rect_from_axis(centerline, thickness_mm):
    """축선 + 두께 → 닫힌 직사각형 4점. `_rect_axis` 의 역."""
    (x0, y0), (x1, y1) = centerline[0], centerline[-1]
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length == 0:
        return None
    nx = -dy / length * thickness_mm / 2.0
    ny = dx / length * thickness_mm / 2.0
    return [[x0 + nx, y0 + ny], [x1 + nx, y1 + ny],
            [x1 - nx, y1 - ny], [x0 - nx, y0 - ny]]


# ── 레벨 스택 (storey.ts 의 계산을 그대로 뒤집는다) ─────────────────────────
def _levels_from_z(zs_mm, heights_mm):
    """오름차순 z(mm) → [(ordinal, baseElevation_m, height_m)].

    Pascal: baseY_i = (baseY_{i-1} + height_{i-1}) + baseElevation_i.
    층고를 층간 거리로 주면 baseElevation 은 맨 아래층만 남는다."""
    out = []
    prev_top = 0.0
    for i, z in enumerate(zs_mm):
        h = (zs_mm[i + 1] - z) if i + 1 < len(zs_mm) else heights_mm[i]
        out.append((i, GC.mm_to_m(z - prev_top), GC.mm_to_m(h)))
        prev_top = z + h
    return out


def _z_of_levels(levels):
    """`_levels_from_z` 의 역 — storey.ts `getLevelElevations` 와 같은 누적식."""
    zs = []
    cum = 0.0
    for lv in sorted(levels, key=lambda n: n.get("level", 0)):
        base = cum + GC.m_to_mm(lv.get("baseElevation", 0.0))
        zs.append(base)
        h = lv.get("height")
        cum = base + GC.m_to_mm(h if h is not None else PASCAL_DEFAULT_WALL_HEIGHT_M)
    return zs


# ── 정변환 ────────────────────────────────────────────────────────────────
def to_pascal_scene(geometry, name=None):
    """geometry.json(dict) → ({nodes, rootNodeIds}, report). 벽만."""
    params = geometry.get("params") or {}
    walls = (geometry.get("elements") or {}).get("wall") or []
    src = name or geometry.get("source") or "mep"

    report = {"walls_in": len(walls), "walls_out": 0, "unconvertible": [],
              "levels": 0, "split_multi_segment": 0, "closed_as_axis": 0}

    # 벽을 z_base 별로 묶는다 — Pascal 의 고저는 부재가 아니라 레벨이 든다
    by_z = {}
    for i, w in enumerate(walls):
        by_z.setdefault(round(GC.base_z("wall", w), 6), []).append((i, w))
    zs = sorted(by_z)
    heights = [max([GC.height_of(w, params, "wall") for _i, w in by_z[z]] or
                   [GC.m_to_mm(PASCAL_DEFAULT_WALL_HEIGHT_M)]) for z in zs]

    nodes = {}
    site_id = _nid("site", src)
    bld_id = _nid("building", src)
    nodes[site_id] = _node(
        site_id, "site", None, name=str(src), children=[bld_id],
        polygon={"type": "polygon",
                 "points": [[-15, -15], [15, -15], [15, 15], [-15, 15]]})
    # 우리 쪽 파일 머리(단위·params·source)는 Pascal 에 자리가 없다. 되돌릴 때
    # 필요하므로 site 에 싣되, **좌표는 아니다**.
    nodes[site_id]["metadata"]["mep"] = {
        "units": geometry.get("units", "mm"),
        "source": geometry.get("source"),
        "params": params,
    }
    nodes[bld_id] = _node(bld_id, "building", site_id, name=str(src), children=[],
                          position=[0, 0, 0], rotation=[0, 0, 0])

    for (ordinal, base_m, h_m), z in zip(_levels_from_z(zs, heights), zs):
        lid = _nid("level", src, ordinal)
        label = next((f.get("label") for f in geometry.get("floors") or []
                      if abs(float(f.get("z", 0.0)) - z) < 1e-6), None)
        nodes[lid] = _node(lid, "level", bld_id,
                           name=label or "Level_%d" % (ordinal + 1),
                           children=[], level=ordinal, baseElevation=base_m, height=h_m)
        nodes[bld_id]["children"].append(lid)

        for i, w in by_z[z]:
            eid = w.get("eid") or "idx:%d" % i
            axis = w.get("centerline") or w.get("points") or []
            thickness = GC.width_of(w, params, "wall")
            closed = bool(w.get("closed") or w.get("pairing") == "closed")
            if closed:
                got = _rect_axis(w.get("points") or axis)
                if got is None:
                    report["unconvertible"].append(
                        {"eid": eid, "layer": w.get("layer"),
                         "reason": "closed_polygon_not_rectangular",
                         "points": len(w.get("points") or [])})
                    continue
                axis, thickness = got
                report["closed_as_axis"] += 1
            if len(axis) < 2:
                report["unconvertible"].append(
                    {"eid": eid, "layer": w.get("layer"), "reason": "fewer_than_2_points"})
                continue
            if len(axis) > 2:
                report["split_multi_segment"] += 1
            for s in range(len(axis) - 1):
                wid = _nid("wall", eid, s)
                meta = {k: w[k] for k in _PROVENANCE if k in w}
                meta["seg"] = s
                meta["segs"] = len(axis) - 1
                meta["closed"] = closed
                nodes[wid] = _node(
                    wid, "wall", lid, name=str(w.get("layer") or "wall"), children=[],
                    start=[GC.mm_to_m(axis[s][0]), GC.mm_to_m(axis[s][1])],
                    end=[GC.mm_to_m(axis[s + 1][0]), GC.mm_to_m(axis[s + 1][1])],
                    thickness=GC.mm_to_m(thickness),
                    height=GC.mm_to_m(GC.height_of(w, params, "wall")),
                    frontSide="unknown", backSide="unknown")
                nodes[wid]["metadata"]["mep"] = meta
                nodes[lid]["children"].append(wid)
                report["walls_out"] += 1

    report["levels"] = len(zs)
    return {"nodes": nodes, "rootNodeIds": [site_id]}, report


# ── 역변환 ────────────────────────────────────────────────────────────────
def from_pascal_scene(scene):
    """Pascal 씬 → (geometry dict, report). 벽만.

    Pascal 에서 사람이 고친 두께·높이는 **선언으로 돌아온다**(`overrides`) —
    적어 준 값이 이긴다는 `geom_contract.width_of` 규약과 같다."""
    nodes = scene.get("nodes") or {}
    site = next((n for n in nodes.values() if n.get("type") == "site"), None)
    head = ((site or {}).get("metadata") or {}).get("mep") or {}
    params = head.get("params") or {}

    levels = [n for n in nodes.values() if n.get("type") == "level"]
    ordered = sorted(levels, key=lambda n: n.get("level", 0))
    z_of = {lv["id"]: z for lv, z in zip(ordered, _z_of_levels(levels))}

    report = {"walls_in": 0, "walls_out": 0, "edited_thickness": 0,
              "edited_height": 0, "points_from_axis": 0}

    # 같은 eid 의 조각들을 순서대로 다시 잇는다(정변환의 split 의 역)
    groups = {}
    for n in nodes.values():
        if n.get("type") != "wall":
            continue
        report["walls_in"] += 1
        meta = (n.get("metadata") or {}).get("mep") or {}
        key = (n.get("parentId"), meta.get("eid") or "#" + n["id"])
        groups.setdefault(key, []).append((meta.get("seg", 0), n["id"], n))

    out = []
    for (lid, _key), segs in sorted(groups.items()):
        segs.sort()
        first = segs[0][2]
        meta = (first.get("metadata") or {}).get("mep") or {}
        axis = [[GC.m_to_mm(first["start"][0]), GC.m_to_mm(first["start"][1])]]
        for _s, _i, n in segs:
            axis.append([GC.m_to_mm(n["end"][0]), GC.m_to_mm(n["end"][1])])

        rec = {k: meta[k] for k in _PROVENANCE if k in meta}
        rec["kind"] = "polyline"
        rec["closed"] = bool(meta.get("closed"))
        rec["z_base"] = z_of.get(lid, 0.0)
        rec["overrides"] = dict(rec.get("overrides") or {})

        # Pascal 쪽 값이 우리 규칙이 뽑을 값과 다르면 = 사람이 고친 것 → 선언으로 승격
        t_mm = GC.m_to_mm(first.get("thickness", PASCAL_DEFAULT_WALL_THICKNESS_M))
        h_mm = GC.m_to_mm(first.get("height", PASCAL_DEFAULT_WALL_HEIGHT_M))
        # 닫힌 벽의 두께는 직사각형의 짧은 변이라 `width_of` 가 아니라 폴리곤이
        # 들고 있다 — `overrides` 에 적으면 원본에 없던 선언이 생긴다. 아래에서
        # 폴리곤을 다시 만들 때 t_mm 을 그대로 쓴다.
        if not rec["closed"] and abs(t_mm - GC.width_of(rec, params, "wall")) > 0.5:
            rec["overrides"]["width"] = t_mm
            report["edited_thickness"] += 1
        if abs(h_mm - GC.height_of(rec, params, "wall")) > 0.5:
            rec["overrides"]["height"] = h_mm
            report["edited_height"] += 1

        if rec["closed"]:
            ring = _rect_from_axis(axis, t_mm)
            if ring is None:
                continue
            # 축선의 방향에 따라 감김이 뒤집힌다 — 계약대로 CCW 로 정규화한다.
            # (꼭짓점 순서는 회전할 수 있다. 같은 사각형이면 같은 면이다.)
            rec["points"] = GC.ccw(ring)
            rec["pairing"] = rec.get("pairing") or "closed"
        else:
            rec["centerline"] = axis
            # 원래 `points` 는 페어링에 쓰인 **원본 면선 한 줄**이라 축선에서
            # 되살릴 수 없다(어느 쪽 면인지가 없다). 축선으로 채우고 센다.
            rec["points"] = [list(p) for p in axis]
            report["points_from_axis"] += 1
        out.append(rec)
        report["walls_out"] += 1

    geometry = {
        "source": head.get("source"),
        "units": head.get("units", "mm"),
        "scale_applied": 1.0,
        "params": params,
        "elements": {"wall": out},
        "floors": [{"z": z_of[lv["id"]], "label": lv.get("name") or "Level_%d" % (i + 1)}
                   for i, lv in enumerate(ordered)],
        "contract": GC.contract_block(),
    }
    return geometry, report


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="geometry.json ↔ Pascal 씬 그래프(벽)")
    ap.add_argument("input")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--back", action="store_true", help="Pascal 씬 → geometry.json")
    a = ap.parse_args()

    data = json.load(open(a.input, encoding="utf-8"))
    res, rep = (from_pascal_scene(data) if a.back else to_pascal_scene(data))
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
