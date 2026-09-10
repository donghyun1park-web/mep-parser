# -*- coding: utf-8 -*-
"""geometry.json ↔ Pascal(pascalorg/editor) 씬 그래프.

Pascal 은 우리와 같은 모형을 쓴다 — 축선/폴리곤 + 치수. 다른 것이 셋이고,
**셋 다 틀려도 모델은 열린다**(그래서 좌표로 잰다):

1. 단위 — 우리 mm, Pascal **m**(덕트·배관 지름만 **인치**).
   환산은 `geom_contract` 에만 있다. 여기서 `/1000`·`/25.4` 를 쓰지 않는다.
2. 고저 — 우리는 부재의 `z_base`/`elevation`, Pascal 은 **레벨**의 스택
   (`level`·`baseElevation`·`height`). MEP 의 고저는 층이 아니라 **레벨 위 높이**다.
3. 축 — Pascal 은 **Y-up**. 평면은 (첫째, 셋째) 성분이고 둘째가 높이다
   (`column.position[2]`·`duct.path[2]` 가 평면 좌표라는 것을 소비자 코드로 확인).

★ 좌표는 metadata 에 넣지 않는다. 넣으면 왕복이 통과해도 컨테이너가 통과한
  것이지 변환이 통과한 게 아니다. metadata 는 출처(eid·layer·검토사유)만.

Pascal 쪽 계약(읽고 고정한 값, `packages/`):
  base.ts        id = `<prefix>_<16 chars of [0-9a-z]>`, parentId nullable, metadata record
  wall.ts        start/end: [x, z] 평면(레벨 좌표), thickness/height: m
  column.ts      position: [x, **y=하단**, z], rotation: 요(yaw), width/depth/height/radius: m
  slab.ts        polygon: [x, z], elevation = **상단**(레벨면 위 m), thickness 는 아래로
  zone.ts        polygon: [x, z], name 필수, ceilingHeight: m
  duct-segment   path: [x, **y=바닥 위 높이**, z] m, shape, width/height/diameter: **인치**
  pipe-segment   path 동일, diameter: 인치(1.25~8), system: waste|vent
  level.ts       level(서수) · baseElevation(m, **누적 위에 더하는 오프셋**) · height(m)
  storey.ts      baseY_i = (직전 baseY + 직전 height) + baseElevation_i, level 오름차순
  ifc-converter  site → building → level → 부재. parentId 와 부모 children **양쪽** 기록
"""

import hashlib
import math

import geom_contract as GC

# packages/core/src/systems/wall/wall-footprint.ts
PASCAL_DEFAULT_WALL_THICKNESS_M = 0.1
PASCAL_DEFAULT_WALL_HEIGHT_M = 2.5

# 직사각형으로 인정할 허용치(mm·도)
RECT_TOL_MM = 1.0
RECT_TOL_DEG = 1.0

# 레벨을 **정의**하는 카테고리. MEP 는 여기 없다 — 덕트의 elevation 2590mm 는
# 2.59m 짜리 층이 아니라 바닥 위 2.59m 다.
LEVEL_CATS = ("wall", "column", "slab", "zone")
MEP_CATS = ("duct", "pipe")

# Pascal 에 대응 노드가 **없는** 카테고리. 조용히 버리지 않고 세어서 보고한다.
# (beam 은 Pascal 자신의 IFC 임포터도 "Pascal has no `beam` node type yet" 이라며 건너뛴다.)
NO_PASCAL_NODE = {
    "beam": "no_beam_node_in_pascal",
    "tray": "no_cable_tray_node_in_pascal",
    # HvacEquipmentNode 는 furnace|air-handler|condenser **캐비닛**이고 치수가
    # 0.3~2m 로 묶여 있다(실측 지하3층: 장비 6개 전부 2.1~2.6m 로 초과).
    # 우리 equipment 는 임의 footprint 라 담을 그릇이 아니다.
    "equipment": "hvac_cabinet_only",
    # 개구부는 DoorNode/WindowNode 로 갈 자리가 **있다** — 벽 자식이고 좌표가
    # 벽 로컬(시작점부터의 거리)이라 이 단계에서 안 했을 뿐이다.
    "opening": "needs_wall_local_frame",
}

# ★ Pascal 스키마의 **수치 범위**. zod 가 거부하면 그 노드는 씬에 안 올라온다 —
#   범위를 넘긴 부재를 그냥 내보내면 "변환했다" 고 말해 놓고 Pascal 에서는
#   사라진다. 넘기면 내보내지 않고 `out_of_pascal_range` 로 센다.
#   (덕트 폭 하한 4인치 = 101.6mm — 실측 환기평면에 100mm 덕트가 있다.)
_RANGE = {
    ("duct-segment", "width"): (4.0, 60.0),
    ("duct-segment", "height"): (3.0, 40.0),
    ("duct-segment", "diameter"): (2.0, 48.0),
    ("pipe-segment", "diameter"): (1.25, 8.0),
    ("zone", "ceilingHeight"): (0.1, None),
    ("column", "height"): (1e-9, None),
    ("column", "width"): (1e-9, None),
    ("column", "depth"): (1e-9, None),
    ("column", "radius"): (1e-9, None),
}


def _out_of_range(node):
    """스키마 범위를 벗어난 첫 필드 (이름, 값, lo, hi). 없으면 None."""
    for (ntype, field), (lo, hi) in _RANGE.items():
        if node.get("type") != ntype or field not in node:
            continue
        v = node[field]
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            return field, v, lo, hi
    return None


# metadata 에 싣는 출처 필드 — **치수·좌표는 없다**
_PROVENANCE = ("eid", "eid_v1", "layer", "pairing", "confidence", "needs_review",
               "review_reason", "width_detected", "overrides", "zone",
               "source_signatures", "handle", "subtype", "source", "member_name",
               "section", "schedule_match", "elevation_source")


def _mep_dims(cat, rec, params):
    """MEP 단면 치수. 규약은 `geom_contract` 가 갖는다 — GUI 별칭 키(`width`/
    `height`/`diameter_mm`)와 '치수 미해소' 판정이 거기 있다. 여기서 `rec["width_mm"]`
    만 읽으면 별칭만 있는 레코드가 **조용히 기본값 400mm 덕트**가 된다.

    ponytail: `mep_dimensions` 가 아직 없는 트리를 위한 폴백. 그 함수가 들어오면 지운다.
    """
    fn = getattr(GC, "mep_dimensions", None)
    if fn is not None:
        return fn(cat, rec, params)          # ContractError 는 호출자가 잡는다
    keys = ("diameter",) if cat == "pipe" else ("width_mm", "height_mm")
    return {k: float(rec.get(k) or GC.DEFAULT_DIMS[cat][k]) for k in keys}


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


def _meta(rec, **extra):
    m = {k: rec[k] for k in _PROVENANCE if k in rec}
    m.update(extra)
    return m


# ── 닫힌 폴리선 ↔ 축선 / 상자 ──────────────────────────────────────────────
def _corners(pts):
    """닫힌 폴리선의 꼭짓점 4개(중복 끝점 제거). 직사각형이 아니면 None."""
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
    return p, side


def _rect_axis(pts):
    """닫힌 직사각형 → (centerline, thickness_mm). 짧은 변 중점을 잇는 것이 축선."""
    got = _corners(pts)
    if got is None:
        return None
    p, side = got
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


def _rect_box(pts):
    """닫힌 직사각형 → (cx, cy, width_mm, depth_mm, rotation_rad). 기둥용.

    Pascal 의 기둥 평면은 로컬 +X 를 **rotation 만큼 CCW** 돌린 것이다
    (`column/floorplan.ts` 가 `rotatePlanVector(v, -rotation)` 을 쓰는데 그 함수는
    시계방향 회전이라 부호가 두 번 뒤집힌다). 여기와 `_box_corners` 가 그 한 쌍이다."""
    got = _corners(pts)
    if got is None:
        return None
    p, side = got
    cx = sum(q[0] for q in p) / 4.0
    cy = sum(q[1] for q in p) / 4.0
    ux = (p[1][0] - p[0][0]) / side[0], (p[1][1] - p[0][1]) / side[0]
    return cx, cy, side[0], side[1], math.atan2(ux[1], ux[0])


def _box_corners(cx, cy, width_mm, depth_mm, rot):
    """`_rect_box` 의 역."""
    c, s = math.cos(rot), math.sin(rot)
    hw, hd = width_mm / 2.0, depth_mm / 2.0
    out = []
    for lx, ly in ((-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd)):
        out.append([cx + lx * c - ly * s, cy + lx * s + ly * c])
    return out


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


def _level_for(z, levels):
    """그 고저를 담을 레벨. 구조 부재는 정확히 맞고, MEP 는 **아래 가장 가까운** 층."""
    pick = levels[0]
    for lz, lid in levels:
        if lz <= z + 1e-6:
            pick = (lz, lid)
    return pick


# ── 정변환 ────────────────────────────────────────────────────────────────
def to_pascal_scene(geometry, name=None):
    """geometry.json(dict) → ({nodes, rootNodeIds}, report)."""
    params = geometry.get("params") or {}
    el = geometry.get("elements") or {}
    src = name or geometry.get("source") or "mep"

    report = {"counts_in": {c: len(v) for c, v in sorted(el.items()) if v},
              "counts_out": {}, "unconvertible": [], "levels": 0,
              "split_multi_segment": 0, "closed_as_axis": 0}

    def bad(cat, rec, reason, **extra):
        item = {"category": cat, "eid": rec.get("eid"), "layer": rec.get("layer"),
                "reason": reason}
        item.update(extra)
        report["unconvertible"].append(item)

    # 레벨은 **구조** 카테고리의 z 로만 만든다
    zs = sorted({round(GC.base_z(c, r), 6)
                 for c in LEVEL_CATS for r in (el.get(c) or [])}) or [0.0]
    heights = []
    for z in zs:
        hs = [GC.z_range(c, r, params)[1] - GC.z_range(c, r, params)[0]
              for c in ("wall", "column") for r in (el.get(c) or [])
              if abs(GC.base_z(c, r) - z) < 1e-6]
        heights.append(max(hs) if hs else GC.m_to_mm(PASCAL_DEFAULT_WALL_HEIGHT_M))

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

    levels = []
    for (ordinal, base_m, h_m), z in zip(_levels_from_z(zs, heights), zs):
        lid = _nid("level", src, ordinal)
        label = next((f.get("label") for f in geometry.get("floors") or []
                      if abs(float(f.get("z", 0.0)) - z) < 1e-6), None)
        nodes[lid] = _node(lid, "level", bld_id,
                           name=label or "Level_%d" % (ordinal + 1),
                           children=[], level=ordinal, baseElevation=base_m, height=h_m)
        nodes[bld_id]["children"].append(lid)
        levels.append((z, lid))
    report["levels"] = len(levels)

    def add(nid, node, lid, cat, rec):
        bounds = _out_of_range(node)
        if bounds is not None:
            field, v, lo, hi = bounds
            bad(cat, rec, "out_of_pascal_range", field=field,
                value=round(v, 4), min=lo, max=hi)
            return
        nodes[nid] = node
        nodes[lid]["children"].append(nid)
        report["counts_out"][cat] = report["counts_out"].get(cat, 0) + 1

    # ── 대응 노드가 없는 카테고리는 여기서 전부 보고하고 끝낸다 ──────────────
    for cat, reason in NO_PASCAL_NODE.items():
        for rec in el.get(cat) or []:
            bad(cat, rec, reason)

    # ── 벽 ────────────────────────────────────────────────────────────────
    for i, w in enumerate(el.get("wall") or []):
        eid = w.get("eid") or "wall:%d" % i
        lz, lid = _level_for(GC.base_z("wall", w), levels)
        axis = w.get("centerline") or w.get("points") or []
        thickness = GC.width_of(w, params, "wall")
        closed = bool(w.get("closed") or w.get("pairing") == "closed")
        if closed:
            got = _rect_axis(w.get("points") or axis)
            if got is None:
                bad("wall", w, "closed_polygon_not_rectangular",
                    points=len(w.get("points") or []))
                continue
            axis, thickness = got
            report["closed_as_axis"] += 1
        if len(axis) < 2:
            bad("wall", w, "fewer_than_2_points")
            continue
        if len(axis) > 2:
            report["split_multi_segment"] += 1
        for s in range(len(axis) - 1):
            wid = _nid("wall", eid, s)
            node = _node(
                wid, "wall", lid, name=str(w.get("layer") or "wall"), children=[],
                start=[GC.mm_to_m(axis[s][0]), GC.mm_to_m(axis[s][1])],
                end=[GC.mm_to_m(axis[s + 1][0]), GC.mm_to_m(axis[s + 1][1])],
                thickness=GC.mm_to_m(thickness),
                height=GC.mm_to_m(GC.height_of(w, params, "wall")),
                frontSide="unknown", backSide="unknown")
            node["metadata"]["mep"] = _meta(w, seg=s, segs=len(axis) - 1, closed=closed)
            add(wid, node, lid, "wall", w)

    # ── 기둥 ──────────────────────────────────────────────────────────────
    for i, c in enumerate(el.get("column") or []):
        eid = c.get("eid") or "col:%d" % i
        z0, z1 = GC.z_range("column", c, params)
        lz, lid = _level_for(GC.base_z("column", c), levels)
        cid = _nid("column", eid)
        if c.get("kind") == "circle":
            cx, cy, r = c["center"][0], c["center"][1], float(c["radius"])
            shape, dims = "round", {"radius": GC.mm_to_m(r),
                                    "width": GC.mm_to_m(r * 2), "depth": GC.mm_to_m(r * 2)}
            rot = 0.0
        else:
            got = _rect_box(c.get("points") or [])
            if got is None:
                bad("column", c, "closed_polygon_not_rectangular",
                    points=len(c.get("points") or []))
                continue
            cx, cy, wmm, dmm, rot = got
            shape = "rectangular"
            dims = {"width": GC.mm_to_m(wmm), "depth": GC.mm_to_m(dmm),
                    "radius": GC.mm_to_m(max(wmm, dmm) / 2.0)}
        node = _node(
            cid, "column", lid, name=str(c.get("layer") or "column"),
            position=[GC.mm_to_m(cx), GC.mm_to_m(z0 - lz), GC.mm_to_m(cy)],
            rotation=rot, crossSection=shape, height=GC.mm_to_m(z1 - z0),
            # ★ 기본값은 **장식용**이다(round-rings 주춧돌 + 기둥머리 + 목이 잘록한
            #   샤프트). 구조 기둥에 그대로 두면 그리스 신전이 선다 — Pascal 자신의
            #   IFC 임포터도 같은 자리에서 장식을 벗긴다.
            style="plain", shaftProfile="straight", shaftStartScale=1,
            shaftEndScale=1, shaftSegmentCount=1, baseStyle="none",
            capitalStyle="none", baseHeight=0, capitalHeight=0, **dims)
        node["metadata"]["mep"] = _meta(c, kind=c.get("kind", "polyline"))
        add(cid, node, lid, "column", c)

    # ── 슬래브 ────────────────────────────────────────────────────────────
    for i, s in enumerate(el.get("slab") or []):
        eid = s.get("eid") or "slab:%d" % i
        pts = s.get("points") or []
        if len(pts) < 3:
            bad("slab", s, "fewer_than_3_points")
            continue
        top = GC.base_z("slab", s)                   # 슬래브의 z_base 는 **상단**
        lz, lid = _level_for(top, levels)
        sid = _nid("slab", eid)
        node = _node(sid, "slab", lid, name=str(s.get("layer") or "slab"),
                     polygon=[[GC.mm_to_m(p[0]), GC.mm_to_m(p[1])] for p in pts],
                     holes=[], elevation=GC.mm_to_m(top - lz),
                     thickness=GC.mm_to_m(GC.thickness_of(s, params, "slab")))
        node["metadata"]["mep"] = _meta(s)
        add(sid, node, lid, "slab", s)

    # ── zone ──────────────────────────────────────────────────────────────
    for i, zrec in enumerate(el.get("zone") or []):
        eid = zrec.get("eid") or "zone:%d" % i
        pts = zrec.get("points") or []
        if len(pts) < 3:
            bad("zone", zrec, "fewer_than_3_points")
            continue
        # zone 의 기준면은 하단이라 z0 가 곧 레벨면이다 — ZoneNode 에 고저
        # 필드가 없어도 잃을 것이 없다(다른 z 의 zone 은 제 레벨을 갖는다).
        z0, z1 = GC.z_range("zone", zrec, params)
        lz, lid = _level_for(GC.base_z("zone", zrec), levels)
        zid = _nid("zone", eid)
        node = _node(zid, "zone", lid, name=str(zrec.get("layer") or "Zone %d" % (i + 1)),
                     polygon=[[GC.mm_to_m(p[0]), GC.mm_to_m(p[1])] for p in pts],
                     ceilingHeight=GC.mm_to_m(z1 - z0), autoFromWalls=False,
                     boundaryWallIds=[], spaceRole="generic")
        node["metadata"]["mep"] = _meta(zrec)
        add(zid, node, lid, "zone", zrec)

    # ── MEP(덕트·배관) ────────────────────────────────────────────────────
    #   path 는 [x, **높이**, y] — Y-up 이라 평면 y 가 셋째로 간다.
    for cat in MEP_CATS:
        for i, m in enumerate(el.get(cat) or []):
            eid = m.get("eid") or "%s:%d" % (cat, i)
            pts = m.get("points") or []
            if len(pts) < 2:
                bad(cat, m, "fewer_than_2_points")
                continue
            elev = GC.base_z(cat, m)                 # MEP 의 기준면은 **중심축**
            lz, lid = _level_for(elev, levels)
            y = GC.mm_to_m(elev - lz)
            path = [[GC.mm_to_m(p[0]), y, GC.mm_to_m(p[1])] for p in pts]
            try:
                dims = _mep_dims(cat, m, params)
            except GC.ContractError as exc:
                bad(cat, m, "mep_dimensions_unresolved", detail=str(exc))
                continue
            need = ("diameter",) if cat == "pipe" else ("width_mm", "height_mm")
            if any(dims.get(k) is None for k in need):
                # footprint 모드처럼 공칭 단면이 없는 레코드 — 세그먼트로 못 만든다
                bad(cat, m, "mep_dimensions_unresolved", detail="missing " + ",".join(need))
                continue
            mid_ = _nid("duct-segment" if cat == "duct" else "pipe-segment", eid)
            if cat == "duct":
                node = _node(mid_, "duct-segment", lid,
                             name=str(m.get("layer") or cat), children=[], path=path,
                             shape="rect", width=GC.mm_to_in(dims["width_mm"]),
                             height=GC.mm_to_in(dims["height_mm"]))
            else:
                node = _node(mid_, "pipe-segment", lid,
                             name=str(m.get("layer") or cat), children=[], path=path,
                             diameter=GC.mm_to_in(dims["diameter"]))
            node["metadata"]["mep"] = _meta(m)
            add(mid_, node, lid, cat, m)

    return {"nodes": nodes, "rootNodeIds": [site_id]}, report


# ── 역변환 ────────────────────────────────────────────────────────────────
def from_pascal_scene(scene):
    """Pascal 씬 → (geometry dict, report).

    Pascal 에서 사람이 고친 치수는 **선언으로 돌아온다**(`overrides`) —
    적어 준 값이 이긴다는 `geom_contract` 규약과 같은 자리."""
    nodes = scene.get("nodes") or {}
    site = next((n for n in nodes.values() if n.get("type") == "site"), None)
    head = ((site or {}).get("metadata") or {}).get("mep") or {}
    params = head.get("params") or {}

    ordered = sorted((n for n in nodes.values() if n.get("type") == "level"),
                     key=lambda n: n.get("level", 0))
    z_of = {lv["id"]: z for lv, z in zip(ordered, _z_of_levels(ordered))}

    report = {"counts_out": {}, "edited": {}, "points_from_axis": 0}

    def out(cat, rec):
        elements.setdefault(cat, []).append(rec)
        report["counts_out"][cat] = report["counts_out"].get(cat, 0) + 1

    def set_base(rec, key, value):
        """해소한 고저를 기록한다. `overrides` 에 같은 키가 남아 있으면 **함께**
        갱신한다 — `geom_contract.base_z` 가 선언(overrides)을 먼저 보므로,
        최상위만 쓰면 Pascal 에서 층을 옮긴 것이 조용히 사라진다(형상은 멀쩡하고
        벽만 딴 층에 선다). 어느 쪽을 읽든 같은 값이어야 한다."""
        rec[key] = value
        if key in rec.get("overrides", {}):
            rec["overrides"][key] = value

    def edited(what):
        report["edited"][what] = report["edited"].get(what, 0) + 1

    def base(n):
        """metadata 의 출처 + 되살릴 수 없는 필드를 뺀 레코드 뼈대."""
        meta = (n.get("metadata") or {}).get("mep") or {}
        rec = {k: meta[k] for k in _PROVENANCE if k in meta}
        rec["overrides"] = dict(rec.get("overrides") or {})
        return rec, meta

    elements = {}

    # ── 벽: 같은 eid 의 조각들을 순서대로 다시 잇는다(정변환 split 의 역) ──
    groups = {}
    for n in nodes.values():
        if n.get("type") != "wall":
            continue
        meta = (n.get("metadata") or {}).get("mep") or {}
        groups.setdefault((n.get("parentId"), meta.get("eid") or "#" + n["id"]),
                          []).append((meta.get("seg", 0), n["id"], n))

    for (lid, _key), segs in sorted(groups.items()):
        segs.sort()
        first = segs[0][2]
        rec, meta = base(first)
        axis = [[GC.m_to_mm(first["start"][0]), GC.m_to_mm(first["start"][1])]]
        for _s, _i, n in segs:
            axis.append([GC.m_to_mm(n["end"][0]), GC.m_to_mm(n["end"][1])])
        rec["kind"] = "polyline"
        rec["closed"] = bool(meta.get("closed"))
        set_base(rec, "z_base", z_of.get(lid, 0.0))

        t_mm = GC.m_to_mm(first.get("thickness", PASCAL_DEFAULT_WALL_THICKNESS_M))
        h_mm = GC.m_to_mm(first.get("height", PASCAL_DEFAULT_WALL_HEIGHT_M))
        # 닫힌 벽의 두께는 직사각형의 짧은 변이라 `width_of` 가 아니라 폴리곤이
        # 들고 있다 — `overrides` 에 적으면 원본에 없던 선언이 생긴다.
        if not rec["closed"] and abs(t_mm - GC.width_of(rec, params, "wall")) > 0.5:
            rec["overrides"]["width"] = t_mm
            edited("wall_thickness")
        if abs(h_mm - GC.height_of(rec, params, "wall")) > 0.5:
            rec["overrides"]["height"] = h_mm
            edited("wall_height")

        if rec["closed"]:
            ring = _rect_from_axis(axis, t_mm)
            if ring is None:
                continue
            # 축선 방향에 따라 감김이 뒤집힌다 — 계약대로 CCW 로 정규화한다.
            rec["points"] = GC.ccw(ring)
        else:
            rec["centerline"] = axis
            # 원래 `points` 는 페어링에 쓴 **원본 면선 한 줄**이라 축선에서
            # 되살릴 수 없다(어느 쪽 면인지가 없다). 축선으로 채우고 센다.
            rec["points"] = [list(p) for p in axis]
            report["points_from_axis"] += 1
        out("wall", rec)

    # ── 나머지 노드 ───────────────────────────────────────────────────────
    for nid in sorted(nodes):
        n = nodes[nid]
        t = n.get("type")
        if t in ("site", "building", "level", "wall"):
            continue
        rec, meta = base(n)
        lz = z_of.get(n.get("parentId"), 0.0)

        if t == "column":
            pos = n.get("position") or [0, 0, 0]
            z0 = lz + GC.m_to_mm(pos[1])
            h_mm = GC.m_to_mm(n.get("height", PASCAL_DEFAULT_WALL_HEIGHT_M))
            set_base(rec, "z_base", z0)
            if meta.get("kind") == "circle":
                rec["kind"] = "circle"
                rec["center"] = [GC.m_to_mm(pos[0]), GC.m_to_mm(pos[2])]
                rec["radius"] = GC.m_to_mm(n.get("radius", 0.22))
            else:
                rec["kind"] = "polyline"
                rec["closed"] = True
                rec["points"] = GC.ccw(_box_corners(
                    GC.m_to_mm(pos[0]), GC.m_to_mm(pos[2]),
                    GC.m_to_mm(n.get("width", 0.44)), GC.m_to_mm(n.get("depth", 0.44)),
                    n.get("rotation", 0.0)))
            if abs(h_mm - GC.height_of(rec, params, "column")) > 0.5:
                rec["overrides"]["height"] = h_mm
                edited("column_height")
            out("column", rec)

        elif t == "slab":
            rec["kind"] = "polyline"
            rec["closed"] = True
            rec["points"] = [[GC.m_to_mm(p[0]), GC.m_to_mm(p[1])]
                             for p in n.get("polygon") or []]
            set_base(rec, "z_base", lz + GC.m_to_mm(n.get("elevation", 0.0)))
            th = GC.m_to_mm(n.get("thickness", 0.05))
            if abs(th - GC.thickness_of(rec, params, "slab")) > 0.5:
                rec["overrides"]["thickness"] = th
                edited("slab_thickness")
            out("slab", rec)

        elif t == "zone":
            rec["kind"] = "polyline"
            rec["closed"] = True
            rec["points"] = [[GC.m_to_mm(p[0]), GC.m_to_mm(p[1])]
                             for p in n.get("polygon") or []]
            set_base(rec, "z_base", lz)
            ch = GC.m_to_mm(n.get("ceilingHeight", 2.7))
            if abs(ch - GC.height_of(rec, params, "zone")) > 0.5:
                rec["overrides"]["height"] = ch
                edited("zone_height")
            out("zone", rec)

        elif t in ("duct-segment", "pipe-segment"):
            cat = "duct" if t == "duct-segment" else "pipe"
            path = n.get("path") or []
            rec["kind"] = "polyline"
            rec["closed"] = False
            rec["points"] = [[GC.m_to_mm(p[0]), GC.m_to_mm(p[2])] for p in path]
            set_base(rec, "elevation", lz + GC.m_to_mm(path[0][1] if path else 0.0))
            if cat == "duct":
                rec["width_mm"] = GC.in_to_mm(n.get("width", 14))
                rec["height_mm"] = GC.in_to_mm(n.get("height", 8))
            else:
                rec["diameter"] = GC.in_to_mm(n.get("diameter", 2))
            out(cat, rec)

    geometry = {
        "source": head.get("source"),
        "units": head.get("units", "mm"),
        "scale_applied": 1.0,
        "params": params,
        "elements": elements,
        "floors": [{"z": z_of[lv["id"]], "label": lv.get("name") or "Level_%d" % (i + 1)}
                   for i, lv in enumerate(ordered)],
        "contract": GC.contract_block(),
    }
    return geometry, report


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="geometry.json ↔ Pascal 씬 그래프")
    ap.add_argument("input")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--back", action="store_true", help="Pascal 씬 → geometry.json")
    a = ap.parse_args()

    data = json.load(open(a.input, encoding="utf-8"))
    res, rep = (from_pascal_scene(data) if a.back else to_pascal_scene(data))
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
