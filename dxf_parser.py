"""
dxf_parser.py  v2  —  결정론적 코어 + 과거 프로젝트 자산 흡수
DXF → 정규화 geometry.json (FreeCAD 빌더와의 계약)

v2 신규 (과거 프로젝트에서 가져온 패턴):
  [MEP 물량산출 계획]  layer_map.csv 기반 매핑 테이블 (하드코딩 규칙 → 외부 CSV)
  [MEP 물량산출 계획]  shapely 점-다각형 존 판정 (요소 → 방/구역 귀속)
  [MEP Phase 1]        --scan 인벤토리 모드 (빌드 전 통계로 도면 검증)
  [ATA cad_parser]     SPLINE 추출 + 호 근사 정비

사용:
  python3 dxf_parser.py plan.dxf --scan                 # 인벤토리만
  python3 dxf_parser.py plan.dxf -m layer_map.csv -o geometry.json
"""
import argparse
import copy
import csv
import difflib
import hashlib
import json
import math
import os
import re
import sys

# Windows 한글 출력 크래시 방지
if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

import ezdxf

try:
    from shapely.geometry import Point, Polygon
    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False

# CSV 없을 때 폴백 규칙 (정규식, 카테고리, 파라미터)
DEFAULT_LAYER_RULES = [
    (r"WALL|벽|CON", "wall", {}),
    (r"COL|기둥|Block_C", "column", {}),
    (r"SLAB|FLOOR|바닥|슬래브|STAIR|계단", "slab", {}),
    (r"ZONE|ROOM|실|구역", "zone", {}),
    (r"DOOR|WIND|문|창|OPEN", "opening", {}),
    (r"CEN|중심", "wall", {}),  # 중심선도 벽체로 간주하는 경우
    # [Phase 2.7] MEP — 데이터 추출만(3D 빌드 후속). 중심선 + 치수.
    (r"PIPE|배관|PIPING", "pipe", {}),
    (r"DUCT|덕트", "duct", {}),
    (r"TRAY|트레이|CABLETRAY", "tray", {}),
]
# CSV 없을 때 폴백 블록 규칙 (블록명 정규식 → 카테고리)
DEFAULT_BLOCK_RULES = [
    (r"COL|기둥|PILLAR", "column", {}),
    (r"DOOR|문", "opening", {}),
    (r"WIND|창", "opening", {}),
    # [Phase 2.7] MEP 장비는 블록 참조가 일반적
    (r"PUMP|AHU|FAN|PANEL|VAV|FCU|장비|펌프|분전반", "equipment", {}),
]
# [Phase 2.7] MEP 카테고리 (벽 평행선 검출 제외, z·치수 주석 부착 대상)
MEP_CATEGORIES = ("pipe", "duct", "tray", "equipment")
DEFAULT_PARAMS = {
    "wall": {"width": 200.0, "height": 2800.0},
    "column": {"height": 3000.0},
    "slab": {"thickness": 200.0},
}
ARC_SEG_PER_RAD = 8.0

# ── [Phase 1] 평행선 벽 검출 튜닝 상수 ───────────────────────
WALL_ANGLE_TOL_DEG = 5.0      # 평행 판정 허용 사이각(도)
WALL_PAIR_MIN_MM = 1.0        # 벽 두께 최소. 1mm → 밀착 철골(A-STEEL, <10mm)도 페어링.
                               # 이전 50mm 로 182개 절반이 필터링됨.
WALL_PAIR_MAX_MM = 500.0      # 벽 두께 최대(이보다 멀면 무관한 선)
WALL_PAIR_OVERLAP_RATIO = 0.3 # 투영 겹침 최소 비율. 0.5→0.3: 세그먼트 길이 불일치로
                               # 페어링 실패하던 88개 중 상당수 구제.

# ── [Phase 4.0] collinear 재병합 튜닝 상수 ───────────────────
COLLINEAR_ANGLE_TOL_DEG = 2.0  # 같은 직선 판정 사이각(도) — 벽 짝보다 빡빡
COLLINEAR_DIST_TOL_MM = 10.0   # 같은 직선 판정 수직오프셋 허용(mm)
COLLINEAR_GAP_TOL_MM = 500.0   # 끝-끝 간격 이 이하면 한 벽으로 연쇄 병합(mm)
                                # 실무 도면: T/십자 교차점 틈 = 벽두께(100~400mm)
                                # 문 개구부 ≥800mm 이므로 500mm 는 안전
CORNER_SNAP_TOL_MM = 50.0      # 끝점 이 거리 이내면 centroid로 스냅(mm)


# ── [MEP 물량산출] 외부 CSV 매핑 테이블 로드 ──────────────────
def load_layer_map(csv_path):
    """layer_map.csv → 규칙 리스트. 파라미터(width/height/thickness)도 함께."""
    rules = []
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(filter(lambda l: not l.startswith("# "), f)):
            attrs = {}
            for k in ("width", "height", "thickness"):
                v = (row.get(k) or "").strip()
                if v:
                    attrs[k] = float(v)
            rules.append((row["pattern"], row["category"], attrs))
    return rules


def classify(layer_name, rules):
    for pattern, category, attrs in rules:
        if re.search(pattern, layer_name, re.IGNORECASE):
            return category, attrs
    return None, {}


# ── 엔티티 → 점열 (ATA 추출 로직 정비·통합) ──────────────────
def arc_to_points(cx, cy, r, a0, a1):
    a0r, a1r = math.radians(a0), math.radians(a1)
    if a1r < a0r:
        a1r += 2 * math.pi
    n = max(2, int((a1r - a0r) * ARC_SEG_PER_RAD))
    return [(cx + r * math.cos(a0r + (a1r - a0r) * i / n),
             cy + r * math.sin(a0r + (a1r - a0r) * i / n)) for i in range(n + 1)]


def lwpolyline_points(e):
    pts, vertices, closed = [], list(e.get_points("xyb")), e.closed
    n = len(vertices)
    for i in range(n):
        x, y, bulge = vertices[i]
        pts.append((x, y))
        if bulge and (i < n - 1 or closed):
            x2, y2, _ = vertices[(i + 1) % n]
            pts.extend(_bulge_points(x, y, x2, y2, bulge)[1:-1])
    return pts, closed


def _bulge_points(x1, y1, x2, y2, bulge):
    chord = math.hypot(x2 - x1, y2 - y1)
    if chord == 0 or bulge == 0:
        return [(x1, y1), (x2, y2)]
    sagitta = bulge * chord / 2.0
    r = ((chord / 2.0) ** 2 + sagitta ** 2) / (2 * abs(sagitta))
    theta = 4 * math.atan(abs(bulge))
    n = max(2, int(theta * ARC_SEG_PER_RAD))
    mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    dx, dy = (x2 - x1) / chord, (y2 - y1) / chord
    h = math.sqrt(max(r * r - (chord / 2.0) ** 2, 0.0))
    sign = 1 if bulge > 0 else -1
    ccx, ccy = mx - sign * h * dy, my + sign * h * dx
    a0, a1 = math.atan2(y1 - ccy, x1 - ccx), math.atan2(y2 - ccy, x2 - ccx)
    if bulge > 0 and a1 < a0:
        a1 += 2 * math.pi
    if bulge < 0 and a1 > a0:
        a1 -= 2 * math.pi
    return [(ccx + r * math.cos(a0 + (a1 - a0) * i / n),
             ccy + r * math.sin(a0 + (a1 - a0) * i / n)) for i in range(n + 1)]


def entity_to_record(e, scale):
    """DXF 엔티티 → 정규화 레코드 (polyline/circle). 미지원이면 None."""
    t = e.dxftype()
    layer = getattr(e.dxf, "layer", "")
    if t == "LINE":
        s, d = e.dxf.start, e.dxf.end
        return {"kind": "polyline", "closed": False, "layer": layer,
                "points": [[s.x * scale, s.y * scale], [d.x * scale, d.y * scale]]}
    if t == "LWPOLYLINE":
        pts, closed = lwpolyline_points(e)
        return {"kind": "polyline", "closed": closed, "layer": layer,
                "points": [[p[0] * scale, p[1] * scale] for p in pts]}
    if t == "POLYLINE":
        pts = [[v.dxf.location.x * scale, v.dxf.location.y * scale] for v in e.vertices]
        return {"kind": "polyline", "closed": e.is_closed, "layer": layer, "points": pts}
    if t == "CIRCLE":
        c = e.dxf.center
        return {"kind": "circle", "center": [c.x * scale, c.y * scale],
                "radius": e.dxf.radius * scale, "layer": layer}
    if t == "ARC":
        c = e.dxf.center
        pts = arc_to_points(c.x, c.y, e.dxf.radius, e.dxf.start_angle, e.dxf.end_angle)
        # arc 식별자 보존: 문 스윙 호 판별(classify_geometry)에 사용
        return {"kind": "polyline", "closed": False, "layer": layer, "from_arc": True,
                "arc_radius": e.dxf.radius * scale,
                "points": [[p[0] * scale, p[1] * scale] for p in pts]}
    if t == "SPLINE":
        pts = [[p[0] * scale, p[1] * scale] for p in e.control_points]
        return ({"kind": "polyline", "closed": e.closed, "layer": layer,
                 "points": pts} if pts else None)
    return None


def _centroid(rec):
    if rec["kind"] == "circle":
        return rec["center"]
    pts = rec["points"]
    return [sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)]


# ── [Phase 2.7] MEP 추출 보조 ────────────────────────────────
def _entity_elevation(e, scale):
    """엔티티의 Z 고저(mm). MEP 라우팅은 고저가 중요 → 2D 폴리라인 z 보존."""
    t = e.dxftype()
    try:
        if t == "LINE":
            z = e.dxf.start.z
        elif t == "LWPOLYLINE":
            z = e.dxf.elevation
        elif t == "POLYLINE":
            z = e.vertices[0].dxf.location.z
        elif t in ("CIRCLE", "ARC"):
            z = e.dxf.center.z
        else:
            z = 0.0
    except Exception:
        z = 0.0
    return round(float(z) * scale, 3)


def annotate_mep(rec, cat, attrs, elevation):
    """MEP 레코드에 고저·치수 자기서술 필드 부착(데이터만, 3D 빌드는 후속).
    pipe: diameter / duct·tray: width_mm·height_mm. 모두 layer_map width/height 에서."""
    rec["elevation"] = elevation
    if cat == "pipe":
        rec["diameter"] = attrs.get("width")        # 배관 외경(mm)
    elif cat in ("duct", "tray"):
        rec["width_mm"] = attrs.get("width")
        rec["height_mm"] = attrs.get("height")
    return rec


# ── [Phase 2] BLOCK(INSERT) 처리 ─────────────────────────────
def _box_record(cx, cy, w, h, rot_deg):
    """중심(cx,cy)·크기 w×h·회전 rot_deg 사각형 closed polyline."""
    hw, hh = w / 2.0, h / 2.0
    corners = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
    a = math.radians(rot_deg)
    ca, sa = math.cos(a), math.sin(a)
    pts = [[round(cx + x * ca - y * sa, 3), round(cy + x * sa + y * ca, 3)]
           for x, y in corners]
    return {"kind": "polyline", "closed": True, "points": pts}


def insert_to_records(insert, scale, category, attrs):
    """INSERT → geometry 레코드 리스트.
    1) virtual_entities()로 블록 내부 형상을 실좌표 explode → entity_to_record.
    2) column: closed polyline/circle만 채택. 없으면 width 박스 마커 폴백.
       opening: circle 우선. 없으면 width 지름 원 마커.
       기타: explode 레코드 그대로."""
    exploded = []
    try:
        for ve in insert.virtual_entities():
            r = entity_to_record(ve, scale)
            if r:
                exploded.append(r)
    except Exception:
        pass

    c = insert.dxf.insert
    cx, cy = c.x * scale, c.y * scale
    rot = float(insert.dxf.get("rotation", 0.0) or 0.0)
    size = attrs.get("width")  # 평면 크기(정사각 근사). None이면 기본값.

    if category == "column":
        solid = [r for r in exploded
                 if r["kind"] == "circle" or (r["kind"] == "polyline" and r.get("closed"))]
        if solid:
            return solid
        return [_box_record(cx, cy, (size or 400.0) * scale,
                            (size or 400.0) * scale, rot)]
    if category == "opening":
        circ = [r for r in exploded if r["kind"] == "circle"]
        if circ:
            return circ
        return [{"kind": "circle", "center": [round(cx, 3), round(cy, 3)],
                 "radius": round((size or 900.0) * scale / 2.0, 3)}]
    if category == "slab":
        # 계단코어 등: 닫힌 폴리라인(윤곽선)만 슬래브로. 내부 LINE들(A-STAIR 등) 제외.
        closed = [r for r in exploded
                  if r["kind"] == "polyline" and r.get("closed") and len(r["points"]) >= 3]
        return closed  # 없어도 OK(마커 불필요)
    if category == "equipment":
        # 장비는 위치 1개만 필요 — 블록 내부 detail 선 전부 채택하면 폭발(엘리베이터 690선).
        # 닫힌 윤곽 1개 우선, 없으면 size 박스 마커 1개로 폴백.
        closed = [r for r in exploded
                  if r["kind"] == "polyline" and r.get("closed") and len(r["points"]) >= 3]
        if closed:
            # 가장 큰 윤곽(bbox 면적) 1개만 = 장비 외형
            def _area(r):
                xs = [p[0] for p in r["points"]]; ys = [p[1] for p in r["points"]]
                return (max(xs) - min(xs)) * (max(ys) - min(ys))
            return [max(closed, key=_area)]
        return [_box_record(cx, cy, (size or 800.0) * scale,
                            (size or 800.0) * scale, rot)]
    # wall/zone 등: 열린 선도 포함
    return [r for r in exploded if r["kind"] == "polyline"]


# ── [Phase 1] 평행선 쌍 → 벽 중심선+두께 검출 ────────────────
def _seg_dir(p1, p2):
    """세그먼트 단위 방향벡터 (ux, uy) 와 길이. 길이 0이면 (0,0,0)."""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    ln = math.hypot(dx, dy)
    if ln == 0:
        return 0.0, 0.0, 0.0
    return dx / ln, dy / ln, ln


def _angle_deg(u1, u2):
    """두 단위벡터 사이각(도). 평행/반평행(0°/180°) 모두 0에 수렴."""
    dot = max(-1.0, min(1.0, abs(u1[0] * u2[0] + u1[1] * u2[1])))
    return math.degrees(math.acos(dot))


_JOIN_LINE_SNAP = 5.0  # 끝점 공유 판정 거리(mm). LINE 연결 pre-join용.

def join_connected_lines(wall_records):
    """같은 레이어의 2점 LINE 레코드들 중 끝점이 이어지는 것을 다중점 폴리라인으로 병합.
    detect_wall_pairs 전에 실행. 개별 LINE export된 DXF의 벽 파편화 방지.
    LWPOLYLINE / closed / 다중점 레코드는 변경 없이 통과."""
    from collections import defaultdict

    def ptkey(p):
        t = _JOIN_LINE_SNAP
        return (round(p[0] / t), round(p[1] / t))

    two_pt = {i for i, r in enumerate(wall_records)
              if len(r.get("points", [])) == 2 and not r.get("closed")}
    keep   = [i for i in range(len(wall_records)) if i not in two_pt]

    # 레이어별 끝점 맵
    layer_ep = defaultdict(lambda: defaultdict(list))
    for i in two_pt:
        r = wall_records[i]
        ly = r.get("layer", "")
        pts = r["points"]
        layer_ep[ly][ptkey(pts[0])].append((i, "start"))
        layer_ep[ly][ptkey(pts[1])].append((i, "end"))

    visited = set()
    joined = []

    for ly, ep_map in layer_ep.items():
        for start in [i for i in two_pt if wall_records[i].get("layer","") == ly]:
            if start in visited:
                continue
            visited.add(start)
            pts0 = wall_records[start]["points"]
            chain_pts = [list(pts0[0]), list(pts0[1])]
            chain_seen = {ptkey(chain_pts[0]), ptkey(chain_pts[1])}

            # tail 연장
            while True:
                cands = [x for x in ep_map.get(ptkey(chain_pts[-1]), [])
                         if x[0] not in visited]
                if not cands:
                    break
                j, end = cands[0]
                nxt = wall_records[j]["points"]
                new_pt = list(nxt[1]) if end == "start" else list(nxt[0])
                if ptkey(new_pt) in chain_seen:
                    break  # 루프 방지
                visited.add(j)
                chain_pts.append(new_pt)
                chain_seen.add(ptkey(new_pt))

            # head 연장
            while True:
                cands = [x for x in ep_map.get(ptkey(chain_pts[0]), [])
                         if x[0] not in visited]
                if not cands:
                    break
                j, end = cands[0]
                nxt = wall_records[j]["points"]
                new_pt = list(nxt[0]) if end == "end" else list(nxt[1])
                if ptkey(new_pt) in chain_seen:
                    break
                visited.add(j)
                chain_pts.insert(0, new_pt)
                chain_seen.add(ptkey(new_pt))

            nr = copy.deepcopy(wall_records[start])
            nr["points"] = chain_pts
            nr["closed"] = False
            joined.append(nr)

    # keep_idx 레코드(LWPOLYLINE 등) + 새 joined 레코드
    return [copy.deepcopy(wall_records[i]) for i in keep] + joined


def _wall_segments(wall_records):
    """벽 폴리라인 레코드들을 직선 세그먼트 단위로 분해."""
    segs = []
    for idx, rec in enumerate(wall_records):
        if rec.get("kind") != "polyline":
            continue
        pts = rec.get("points", [])
        if len(pts) < 2:
            continue
        n = len(pts)
        edges = list(range(n - 1))
        pairs = [(pts[i], pts[i + 1]) for i in edges]
        if rec.get("closed") and n > 2:
            pairs.append((pts[-1], pts[0]))
        for a, b in pairs:
            ux, uy, ln = _seg_dir(a, b)
            if ln == 0:
                continue
            segs.append({"p1": a, "p2": b, "dir": (ux, uy), "len": ln,
                         "src": idx, "layer": rec.get("layer", ""),
                         "overrides": rec.get("overrides", {}),
                         "z_base": rec.get("z_base", 0.0)})
    return segs


_SEG_MERGE_GAP_MM = 50.0  # 끊긴 선 복원용 작은 갭(문 개구부 800mm+ 는 안 이음)

def _merge_collinear_segments(segs, gap_tol=None):
    """같은 직선(각도+수직오프셋) 위 끝-끝 가까운 세그먼트를 1개로 병합.
    페어링 전에 호출 → 단편화(끊긴 선)된 면선을 복원해 그리디 매칭 어긋남 방지.
    gap_tol 작게(50mm) → 제도 오차/끊김만 잇고 개구부는 보존. 결정론."""
    if not segs:
        return segs
    if gap_tol is None:
        gap_tol = _SEG_MERGE_GAP_MM
    buckets = {}
    for s in segs:
        d = _get_normalized_direction(s["p1"], s["p2"])
        ang = math.degrees(math.atan2(d[1], d[0]))          # [0,180)
        off = -d[1] * s["p1"][0] + d[0] * s["p1"][1]         # 원점→직선 부호거리
        key = (round(ang / COLLINEAR_ANGLE_TOL_DEG),
               round(off / COLLINEAR_DIST_TOL_MM),
               s.get("layer", ""))                           # 같은 레이어끼리만
        buckets.setdefault(key, []).append(s)
    out = []
    for key in sorted(buckets, key=lambda k: (k[0], k[1], str(k[2]))):
        b = buckets[key]
        dref = _get_normalized_direction(b[0]["p1"], b[0]["p2"])
        b.sort(key=lambda s: min(s["p1"][0]*dref[0]+s["p1"][1]*dref[1],
                                 s["p2"][0]*dref[0]+s["p2"][1]*dref[1]))
        cur = dict(b[0])
        for nxt in b[1:]:
            pts = [cur["p1"], cur["p2"], nxt["p1"], nxt["p2"]]
            tproj = sorted(p[0]*dref[0]+p[1]*dref[1] for p in pts)
            l1 = cur["len"]; l2 = nxt["len"]
            gap = (tproj[-1] - tproj[0]) - (l1 + l2)
            if gap <= gap_tol:
                pts.sort(key=lambda p: p[0]*dref[0]+p[1]*dref[1])
                a, bb = list(pts[0]), list(pts[-1])
                ux, uy, ln = _seg_dir(a, bb)
                cur = {"p1": a, "p2": bb, "dir": (ux, uy), "len": ln,
                       "src": cur.get("src"), "layer": cur.get("layer", ""),
                       "overrides": cur.get("overrides") or nxt.get("overrides", {}),
                       "z_base": cur.get("z_base", 0.0)}
            else:
                out.append(cur)
                cur = dict(nxt)
        out.append(cur)
    return out


def _pair_geometry(sa, sb):
    """평행 후보 두 세그먼트 → (수직거리, 겹침길이, 중선[[],[]]) 또는 None."""
    o = sa["p1"]
    ux, uy = sa["dir"]
    # 축(u) 투영 스칼라
    def t(p):
        return (p[0] - o[0]) * ux + (p[1] - o[1]) * uy
    ta1, ta2 = t(sa["p1"]), t(sa["p2"])
    tb1, tb2 = t(sb["p1"]), t(sb["p2"])
    t_lo = max(min(ta1, ta2), min(tb1, tb2))
    t_hi = min(max(ta1, ta2), max(tb1, tb2))
    overlap = t_hi - t_lo
    if overlap <= 0:
        return None
    # sb 한 점의 sa선상 수직 오프셋(평행선이므로 일정)
    foot_t = tb1
    foot = (o[0] + foot_t * ux, o[1] + foot_t * uy)
    off = (sb["p1"][0] - foot[0], sb["p1"][1] - foot[1])
    perp = math.hypot(off[0], off[1])
    if not (WALL_PAIR_MIN_MM <= perp <= WALL_PAIR_MAX_MM):
        return None
    half = (off[0] * 0.5, off[1] * 0.5)
    c_lo = (o[0] + t_lo * ux + half[0], o[1] + t_lo * uy + half[1])
    c_hi = (o[0] + t_hi * ux + half[0], o[1] + t_hi * uy + half[1])
    return perp, overlap, [[round(c_lo[0], 3), round(c_lo[1], 3)],
                           [round(c_hi[0], 3), round(c_hi[1], 3)]]


def _find_wall_pairs(segs):
    """그리디 매칭: (i, j, perp, overlap, centerline) 확정쌍 리스트 + 매칭된 인덱스 집합."""
    candidates = []
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            if _angle_deg(segs[i]["dir"], segs[j]["dir"]) > WALL_ANGLE_TOL_DEG:
                continue
            geom = _pair_geometry(segs[i], segs[j])
            if geom is None:
                continue
            perp, overlap, center = geom
            min_len = min(segs[i]["len"], segs[j]["len"])
            if min_len == 0 or overlap < WALL_PAIR_OVERLAP_RATIO * min_len:
                continue
            candidates.append((perp, overlap, i, j, center, min_len))
    candidates.sort(key=lambda c: c[0])  # 가까운 쌍 우선
    matched, pairs = set(), []
    for perp, overlap, i, j, center, min_len in candidates:
        if i in matched or j in matched:
            continue
        matched.add(i); matched.add(j)
        conf = min(1.0, 0.7 + 0.3 * min(1.0, overlap / min_len))
        pairs.append((i, j, perp, center, round(conf, 3)))
    return pairs, matched


def detect_wall_pairs(wall_records, params):
    """벽 레코드 → 평행선 쌍은 중심선+두께로 병합, 단독선은 중심선 벽으로.
    v1: 세그먼트 단위 출력(직선 1개 = 벽 레코드 1개).
    닫힌 폴리선(closed=True): 세그먼트 분해·짝맺기 건너뜀 → pairing='closed' 로 원본 통과.
      FreeCAD builder 에서 solid extrusion(기둥 형태) 으로 처리."""
    # ① 닫힌 폴리선은 세그먼트 분해 대상에서 제외 ─ 교차 벽 파편화 방지
    #    단, npts<3 인 퇴화 closed(예: 2점 폴리라인)는 solid extrude 불가 →
    #    open_recs 로 보내 일반 면선 페어링 경로를 태운다.
    closed_recs = [r for r in wall_records
                   if r.get("closed", False) and len(r.get("points", [])) >= 3]
    open_recs   = [r for r in wall_records
                   if not (r.get("closed", False) and len(r.get("points", [])) >= 3)]

    segs = _merge_collinear_segments(_wall_segments(open_recs))
    if not segs and not closed_recs:
        return wall_records

    pairs, matched = _find_wall_pairs(segs) if segs else ([], set())
    out = []

    for i, j, perp, center, conf in pairs:
        ov = segs[i]["overrides"] or segs[j]["overrides"]
        zb = segs[i].get("z_base", 0.0)
        seg_len = math.hypot(center[1][0]-center[0][0], center[1][1]-center[0][1])
        out.append({"kind": "polyline", "closed": False,
                    "points": [list(segs[i]["p1"]), list(segs[i]["p2"])],
                    "centerline": center,
                    "width_detected": round(perp, 3),
                    "confidence": conf, "pairing": "paired",
                    "needs_review": False, "z_base": zb,
                    "layer": segs[i].get("layer", ""),
                    "seg_length": round(seg_len, 1),
                    **({"overrides": ov} if ov else {})})
    for k, s in enumerate(segs):
        if k in matched:
            continue
        seg_len = s["len"]
        out.append({"kind": "polyline", "closed": False,
                    "points": [list(s["p1"]), list(s["p2"])],
                    "centerline": [list(s["p1"]), list(s["p2"])],
                    "width_detected": None,
                    "confidence": 0.5, "pairing": "single",
                    "needs_review": True, "z_base": s.get("z_base", 0.0),
                    "layer": s.get("layer", ""),
                    "seg_length": round(seg_len, 1),
                    **({"overrides": s["overrides"]} if s["overrides"] else {})})

    # ② 닫힌 폴리선: 원본 레코드 그대로 추가 (pairing="closed" 마킹만)
    for r in closed_recs:
        nr = copy.deepcopy(r)
        nr["pairing"] = "closed"
        nr.setdefault("confidence", 0.7)
        nr.setdefault("needs_review", False)
        out.append(nr)

    return out


def repair_single_walls(wall_records, params):
    """[외곽선→센터선 정합] single(짝 못 찾은 면선) 2차 처리.
    ① collinear 병합 이후 길어진 single 면선끼리 2차 페어링 → paired 승격(정확한 두께).
    ② 잔여 면선: 법선방향 width/2 offset 으로 centerline 생성(벽 중심쪽). align=Center 통일.
    → 모든 비-closed 벽이 centerline 기준이 되어 builder 의 centerline/면선 혼합 버그 제거."""
    singles = [r for r in wall_records if r.get("pairing") == "single"]
    others  = [r for r in wall_records if r.get("pairing") != "single"]
    if not singles:
        return wall_records

    # 로컬 질량 기준점: 신뢰 가능한 others(paired 등) centerline 중점 모음
    mass_pts = []
    for r in others:
        cl = r.get("centerline") or r.get("points", [])
        if len(cl) >= 2:
            mass_pts.append(((cl[0][0] + cl[-1][0]) / 2.0,
                             (cl[0][1] + cl[-1][1]) / 2.0))
    if mass_pts:
        gx = sum(p[0] for p in mass_pts) / len(mass_pts)
        gy = sum(p[1] for p in mass_pts) / len(mass_pts)
    else:
        gx = gy = 0.0

    # face-style 판정: paired 벽이 충분히 존재 = 벽을 두 면선으로 그린 도면.
    #   paired 가 거의 없으면(centerline-style: 벽=중심선 1개) offset 하면 오히려 어긋남
    #   → 그 경우 잔여 single 은 centerline 그대로 둔다.
    n_others_paired = sum(1 for r in others if r.get("pairing") == "paired")

    # ① 2차 페어링 (single 면선만 대상, collinear 병합 후 = 단편화 복원)
    segs = _merge_collinear_segments(_wall_segments(singles))
    pairs, matched = _find_wall_pairs(segs) if segs else ([], set())
    out = list(others)
    for i, j, perp, center, conf in pairs:
        ov = segs[i]["overrides"] or segs[j]["overrides"]
        seg_len = math.hypot(center[1][0] - center[0][0], center[1][1] - center[0][1])
        out.append({"kind": "polyline", "closed": False,
                    "points": [list(segs[i]["p1"]), list(segs[i]["p2"])],
                    "centerline": center,
                    "width_detected": round(perp, 3),
                    "confidence": round(conf, 3), "pairing": "paired",
                    "needs_review": False, "z_base": segs[i].get("z_base", 0.0),
                    "layer": segs[i].get("layer", ""),
                    "seg_length": round(seg_len, 1),
                    **({"overrides": ov} if ov else {})})

    # face-style 여부: 기존 paired + 2차 페어링으로 생긴 paired 합으로 판단
    face_style = (n_others_paired + len(pairs)) > 0
    pw = float(params.get("wall", {}).get("width", 200.0))

    # 중복 제거용: 모든 paired centerline(기존 others + 방금 2차로 만든 것) 수집
    paired_cls = []
    for r in out:
        if r.get("pairing") == "paired":
            cl = r.get("centerline") or r.get("points", [])
            if len(cl) >= 2:
                paired_cls.append((cl[0], cl[-1], float(r.get("width_detected") or pw)))

    def _near_paired(mx, my):
        """면선 중점이 이미 만들어진 paired 벽의 면(width/2+여유) 위에 있나."""
        for (a, b, w) in paired_cls:
            dd = _pt_to_seg_dist(mx, my, a[0], a[1], b[0], b[1])
            if dd <= w / 2.0 + 30.0:
                return True
        return False

    # ② 잔여 면선 처리
    n_dropped = 0
    for k, s in enumerate(segs):
        if k in matched:
            continue
        ov = s.get("overrides") or {}
        w = float(ov.get("width", pw))
        mx0 = (s["p1"][0] + s["p2"][0]) / 2.0
        my0 = (s["p1"][1] + s["p2"][1]) / 2.0
        # 이미 paired 벽 면 위 = 중복 면선(벽은 이미 생성됨) → 버림
        if face_style and _near_paired(mx0, my0):
            n_dropped += 1
            continue
        if not face_style:
            # centerline-style 도면(벽=중심선): offset 없이 그대로 centerline 유지
            out.append({"kind": "polyline", "closed": False,
                        "points": [list(s["p1"]), list(s["p2"])],
                        "centerline": [list(s["p1"]), list(s["p2"])],
                        "width_detected": None,
                        "confidence": 0.5, "pairing": "single",
                        "needs_review": True, "z_base": s.get("z_base", 0.0),
                        "layer": s.get("layer", ""),
                        "seg_length": round(s["len"], 1),
                        **({"overrides": ov} if ov else {})})
            continue
        ux, uy = s["dir"]
        nx, ny = -uy, ux  # 법선
        mx = (s["p1"][0] + s["p2"][0]) / 2.0
        my = (s["p1"][1] + s["p2"][1]) / 2.0
        # 가장 가까운 others 중점(로컬 질량), 없으면 전역 centroid
        tx, ty = gx, gy
        best_d = None
        for (px, py) in mass_pts:
            dd = (px - mx) ** 2 + (py - my) ** 2
            if best_d is None or dd < best_d:
                best_d, tx, ty = dd, px, py
        # +n / -n 중 목표점에 가까운 쪽으로 offset
        d_plus = (mx + nx * w / 2 - tx) ** 2 + (my + ny * w / 2 - ty) ** 2
        d_minus = (mx - nx * w / 2 - tx) ** 2 + (my - ny * w / 2 - ty) ** 2
        sgn = 1.0 if d_plus <= d_minus else -1.0
        ox, oy = nx * sgn * w / 2.0, ny * sgn * w / 2.0
        cl = [[round(s["p1"][0] + ox, 3), round(s["p1"][1] + oy, 3)],
              [round(s["p2"][0] + ox, 3), round(s["p2"][1] + oy, 3)]]
        out.append({"kind": "polyline", "closed": False,
                    "points": [list(s["p1"]), list(s["p2"])],
                    "centerline": cl,
                    "width_detected": round(w, 3),
                    "confidence": 0.4, "pairing": "single_offset",
                    "needs_review": True, "z_base": s.get("z_base", 0.0),
                    "layer": s.get("layer", ""),
                    "seg_length": round(s["len"], 1),
                    **({"overrides": ov} if ov else {})})
    return out


# ── [Phase 4.0] collinear 벽 재병합 ─────────────────────────
# detect_wall_pairs 는 세그먼트 단위(직선 1개=벽 1개)로 출력한다.
# 한 벽이 여러 LINE/세그먼트로 쪼개졌으면 BIM 객체도 쪼개진다 → 같은 직선 위
# 끝-끝이 가까운 세그먼트를 하나로 합친다.
# 범위 주의: '같은 직선' 연쇄만 병합한다. 직각 코너 틈(사각방=벽4개)은
#   여기서 안 메운다 — 코너 스냅/miter 는 별도(코너 정비).
# 성능: 세그먼트를 '직선 키'(정규화 방향 + 원점 수직오프셋 + 두께 + pairing)로
#   버킷팅 → 버킷 내 1D 사영 정렬 후 인접 갭<tol 단일 패스 연쇄. union-find 불필요,
#   O(N log N). 결정론 유지(키·좌표 정렬, tie-break는 사영 t).
def _get_normalized_direction(p1, p2):
    """단위 방향벡터, 상반원 정규화(반대 방향도 같은 키)."""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    n = math.hypot(dx, dy)
    if n == 0:
        return (0.0, 0.0)
    dx, dy = dx / n, dy / n
    if dy < 0 or (dy == 0 and dx < 0):  # 상반원으로 뒤집기
        dx, dy = -dx, -dy
    return (dx, dy)


def _point_to_line_distance(pt, a, d):
    """점 pt 와 점 a·단위방향 d 가 정의하는 무한직선의 수직거리."""
    nx, ny = -d[1], d[0]
    return abs((pt[0] - a[0]) * nx + (pt[1] - a[1]) * ny)


def _check_collinear_connectable(seg1, seg2):
    """두 세그먼트가 같은 직선(각도·수직오프셋 tol) + 1D 갭<tol 인가."""
    d1 = _get_normalized_direction(seg1["c1"], seg1["c2"])
    d2 = _get_normalized_direction(seg2["c1"], seg2["c2"])
    if _angle_deg(d1, d2) > COLLINEAR_ANGLE_TOL_DEG:
        return False
    if _point_to_line_distance(seg2["c1"], seg1["c1"], d1) > COLLINEAR_DIST_TOL_MM:
        return False
    ts = sorted(p[0] * d1[0] + p[1] * d1[1]
                for p in (seg1["c1"], seg1["c2"], seg2["c1"], seg2["c2"]))
    l1 = math.dist(seg1["c1"], seg1["c2"])
    l2 = math.dist(seg2["c1"], seg2["c2"])
    gap = (ts[-1] - ts[0]) - (l1 + l2)  # 음수=겹침
    return gap <= COLLINEAR_GAP_TOL_MM


def _merge_two_segments(seg1, seg2):
    """같은 직선 두 레코드 → 사영 양 끝점으로 합치고 overrides 보존."""
    d = _get_normalized_direction(seg1["c1"], seg1["c2"])
    pts = [seg1["c1"], seg1["c2"], seg2["c1"], seg2["c2"]]
    pts.sort(key=lambda p: p[0] * d[0] + p[1] * d[1])
    a, b = list(pts[0]), list(pts[-1])
    rec1, rec2 = seg1["rec"], seg2["rec"]
    ov = rec1.get("overrides") or rec2.get("overrides")  # 치수 보존(빌더 손실 방지)
    merged = {"kind": "polyline", "closed": False,
              "points": [a, b], "centerline": [a, b],
              "width_detected": rec1.get("width_detected"),
              "confidence": min(rec1.get("confidence", 0.5),
                                rec2.get("confidence", 0.5)),
              "pairing": rec1.get("pairing", "single"),
              "needs_review": bool(rec1.get("needs_review") or rec2.get("needs_review")),
              "z_base": rec1.get("z_base", 0.0),  # [4b]
              **({"overrides": ov} if ov else {})}
    return {"c1": a, "c2": b, "rec": merged}


def merge_collinear_walls(wall_records, params):
    """같은 직선 위 끝-끝이 가까운 벽 세그먼트를 한 벽으로 재병합(O(N log N))."""
    if not wall_records:
        return wall_records
    items = []
    for rec in wall_records:
        cl = rec.get("centerline") or rec.get("points")
        if not cl or len(cl) < 2:
            return wall_records  # 비정형 → 보수적으로 원본 유지
        items.append({"c1": cl[0], "c2": cl[-1], "rec": rec})
    # 버킷 키: 방향(각도) + 원점 수직오프셋 + 두께 + pairing (모두 양자화)
    buckets = {}
    for it in items:
        d = _get_normalized_direction(it["c1"], it["c2"])
        ang = math.degrees(math.atan2(d[1], d[0]))            # [0,180)
        off = -d[1] * it["c1"][0] + d[0] * it["c1"][1]        # 원점→직선 부호거리
        w = it["rec"].get("width_detected")
        key = (round(ang / COLLINEAR_ANGLE_TOL_DEG),
               round(off / COLLINEAR_DIST_TOL_MM),
               round(w, 1) if w is not None else None,
               it["rec"].get("pairing", "single"))
        buckets.setdefault(key, []).append(it)
    out = []
    for key in sorted(buckets, key=lambda k: (k[0], k[1], k[3], k[2] or -1)):
        bucket = buckets[key]
        dref = _get_normalized_direction(bucket[0]["c1"], bucket[0]["c2"])
        bucket.sort(key=lambda it: min(it["c1"][0] * dref[0] + it["c1"][1] * dref[1],
                                       it["c2"][0] * dref[0] + it["c2"][1] * dref[1]))
        cur = bucket[0]
        for nxt in bucket[1:]:
            if _check_collinear_connectable(cur, nxt):
                cur = _merge_two_segments(cur, nxt)
            else:
                out.append(cur["rec"])
                cur = nxt
        out.append(cur["rec"])
    return out


# ── [Phase 4.1] 코너 스냅 ────────────────────────────────────
# [4] boolean void(Part.makeCut)는 벽 끝점이 정확히 일치해야 solid 교차 연산이 성공한다.
# detect_wall_pairs + merge_collinear_walls 이후에도 끝점이 수 mm 어긋나면 void 뚫기 실패.
# 알고리즘: 끝점 목록을 x 정렬 후 슬라이딩 윈도우 → euclidean dist < snap_tol 쌍 union-find
# → 클러스터 centroid 로 일괄 치환. O(N log N), 결정론(정렬+작은인덱스-root union-find).
# 범위: 같은 직선 아닌 코너(T자, ㄱ자 접점) 스냅. collinear 재병합과 독립적으로 동작.
def snap_wall_corners(wall_records, snap_tol=None):
    """벽 끝점 CORNER_SNAP_TOL_MM 이내 클러스터 → centroid 스냅.
    centerline·points 양쪽 동기화. deepcopy 로 원본 불변."""
    if snap_tol is None:
        snap_tol = CORNER_SNAP_TOL_MM
    if not wall_records:
        return wall_records

    # [x, y, rec_idx, ep_idx(0=c1,1=c2)]
    eps = []
    for i, rec in enumerate(wall_records):
        cl = rec.get("centerline") or rec.get("points", [])
        if len(cl) >= 2:
            eps.append([float(cl[0][0]),  float(cl[0][1]),  i, 0])
            eps.append([float(cl[-1][0]), float(cl[-1][1]), i, 1])

    if not eps:
        return wall_records

    # union-find (작은 인덱스가 root → 결정론)
    parent = list(range(len(eps)))

    def _find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def _union(a, b):
        ra, rb = _find(a), _find(b)
        if ra == rb:
            return
        if ra < rb:
            parent[rb] = ra
        else:
            parent[ra] = rb

    # x 정렬 후 슬라이딩 윈도우
    order = sorted(range(len(eps)), key=lambda k: (eps[k][0], eps[k][1]))
    lo = 0
    for hi, i in enumerate(order):
        while eps[order[lo]][0] < eps[i][0] - snap_tol:
            lo += 1
        for j_idx in range(lo, hi):
            j = order[j_idx]
            if abs(eps[j][0] - eps[i][0]) > snap_tol:
                continue
            if abs(eps[j][1] - eps[i][1]) > snap_tol:
                continue
            dx = eps[i][0] - eps[j][0]
            dy = eps[i][1] - eps[j][1]
            if dx * dx + dy * dy <= snap_tol * snap_tol:
                _union(i, j)

    # 클러스터별 centroid
    clusters: dict = {}
    for k in range(len(eps)):
        r = _find(k)
        clusters.setdefault(r, []).append(k)

    new_pos = {}
    n_snapped = 0
    for members in clusters.values():
        if len(members) < 2:
            continue
        cx = round(sum(eps[m][0] for m in members) / len(members), 3)
        cy = round(sum(eps[m][1] for m in members) / len(members), 3)
        for m in members:
            new_pos[m] = [cx, cy]
        n_snapped += 1

    if not new_pos:
        return wall_records

    out = copy.deepcopy(wall_records)
    for k, ep in enumerate(eps):
        if k not in new_pos:
            continue
        ri, ei = ep[2], ep[3]
        rec = out[ri]
        for field in ("centerline", "points"):
            lst = rec.get(field)
            if not lst or len(lst) < 2:
                continue
            if ei == 0:
                lst[0] = new_pos[k]
            else:
                lst[-1] = new_pos[k]
    return out


# ── [Phase 4a] opening → 벽 연결 ────────────────────────────
# geometry.json 파싱 시 각 opening에 wall_indices 태깅.
# builder(freecad_builder.py)가 Part.makeCut boolean void 적용할 때 사용.
def _pt_to_seg_dist(px, py, x1, y1, x2, y2):
    """점(px,py) → 선분(x1,y1)-(x2,y2) 최소 euclidean 거리."""
    dx, dy = x2 - x1, y2 - y1
    ln = dx * dx + dy * dy
    if ln == 0:
        return math.hypot(px - x1, py - y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / ln))
    return math.hypot(x1 + t * dx - px, y1 + t * dy - py)


def link_openings_to_walls(elements, params):
    """각 opening이 교차하는 벽 index 목록을 opening["wall_indices"]에 기록.
    판정: opening 중심→벽 중심선 수직거리 < opening 반경 + 벽두께/2 + 10mm 여유."""
    openings = elements.get("opening", [])
    walls = elements.get("wall", [])
    # 스키마 기본값(벽 유무와 무관하게 항상 설정 → build_openings 일관성)
    for op in openings:
        op.setdefault("wall_indices", [])
        if not op.get("center"):
            try:
                cen = _centroid(op)
                op["center"] = [round(cen[0], 3), round(cen[1], 3)]
            except Exception:
                op["center"] = [0, 0]
        if not op.get("radius"):
            pts = op.get("points", [])
            if len(pts) >= 2:
                x0, y0, x1, y1 = _bbox(pts)
                op["radius"] = round(max(x1 - x0, y1 - y0) / 2.0, 1)
            else:
                op["radius"] = 50.0
        op.setdefault("subtype", None)
        if "width" not in op or op.get("width") is None:
            r0 = float(op.get("radius", 50.0))
            op["width"] = round(r0 * 2, 1) if r0 > 1 else (
                900.0 if op.get("subtype") == "door" else 1200.0)
        if op.get("height") is None:
            op["height"] = 2100.0 if op.get("subtype") == "door" else 1200.0
        if op.get("sill") is None:
            op["sill"] = 0.0 if op.get("subtype") == "door" else 900.0
    if not openings or not walls:
        return
    default_w = float(params.get("wall", {}).get("width", 200.0))
    for op in openings:
        c = op.get("center") or [0, 0]
        cx, cy = float(c[0]), float(c[1])
        r = float(op.get("radius", 50.0))
        indices = []
        nearest = (1e18, None, default_w)  # (거리, 벽방향단위벡터, 벽두께)
        for i, wall in enumerate(walls):
            cl = wall.get("centerline") or wall.get("points", [])
            if len(cl) < 2:
                continue
            ww = float(wall.get("width_detected")
                       or wall.get("overrides", {}).get("width", default_w))
            dist = _pt_to_seg_dist(cx, cy,
                                   float(cl[0][0]), float(cl[0][1]),
                                   float(cl[-1][0]), float(cl[-1][1]))
            if dist < r + ww * 0.5 + 10.0:
                indices.append(i)
                if dist < nearest[0]:
                    ux, uy, _ln = _seg_dir(cl[0], cl[-1])
                    nearest = (dist, (ux, uy), ww)
        op["wall_indices"] = sorted(indices)
        # host 벽 배향(문/창 사각 void 방향 산출용)
        if nearest[1] is not None:
            op["host_dir"] = [round(nearest[1][0], 5), round(nearest[1][1], 5)]
            op["host_width"] = round(nearest[2], 1)


# ── [Phase 3] 레이어명 비의존 기하 분류기 + 미매핑 fuzzy 제안 ──
def _bbox(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def classify_geometry(rec):
    """레이어명 무시, 기하만으로 (category, confidence, reason, subtype).
    subtype: 'door'|'window'|None. 모르면 (None, 0, '', None).
    하위호환: 호출부는 4-튜플 언팩 또는 [:3] 슬라이스로 사용."""
    k = rec.get("kind")
    # ── 문 스윙 호: ARC 유래 열린 폴리 → opening/door ──────────────────────────
    if rec.get("from_arc"):
        ar = float(rec.get("arc_radius", 0.0))
        if 300.0 <= ar <= 1500.0:  # 표준 문폭 범위(반경=문폭)
            return ("opening", 0.7, f"문 스윙 호 r={ar:.0f}", "door")
    if k == "circle":
        r = rec.get("radius", 0.0)
        if r <= 1000:
            return ("column", 0.5, f"소형 원 r={r:.0f} (기둥/개구부 모호)", None)
        return (None, 0.0, "", None)
    pts = rec.get("points", [])
    if len(pts) < 2:
        return (None, 0.0, "", None)
    x0, y0, x1, y1 = _bbox(pts)
    w, h = x1 - x0, y1 - y0
    if rec.get("closed"):
        area = w * h  # bbox 근사
        aspect = (min(w, h) / max(w, h)) if max(w, h) > 0 else 0.0
        if len(pts) <= 6 and aspect >= 0.7 and 40_000 <= area <= 1_000_000:
            return ("column", 0.85, f"소형 정사각 닫힘폴리 {w:.0f}x{h:.0f}", None)
        # 얇고 긴 닫힘 박스 = 창(개구부): 폭 넓고 깊이 얇음
        long_side, short_side = max(w, h), min(w, h)
        if (len(pts) <= 6 and 600 <= long_side <= 3000 and short_side <= 400
                and aspect <= 0.4):
            return ("opening", 0.6, f"얇은 박스 {w:.0f}x{h:.0f} (창 추정)", "window")
        if area >= 10_000_000:
            return ("slab", 0.7, f"대형 닫힘폴리 {w:.0f}x{h:.0f} (슬래브/존)", None)
        return ("zone", 0.4, f"중형 닫힘폴리 {w:.0f}x{h:.0f}", None)
    return ("wall", 0.45, "열린 선/폴리 (벽/배관 중심선 모호)", None)


def fuzzy_layer_suggestion(layer, rules):
    """레이어명 vs 규칙 패턴 토큰 difflib 최고 유사도 → (score, token, category)."""
    best = (0.0, None, None)
    up = (layer or "").upper()
    for pattern, cat, _ in rules:
        for tok in re.split(r"[|]", pattern):
            tok = tok.strip()
            if not tok:
                continue
            s = difflib.SequenceMatcher(None, up, tok.upper()).ratio()
            if s > best[0]:
                best = (s, tok, cat)
    return best


def build_suggestions(unmapped_recs, rules, kind="layer"):
    """미매핑 레이어/블록별: 기하 투표 + 이름 fuzzy → 비강제 제안(사람/AI 검토용).
    kind: 'layer'|'block' — 제안에 source 표기. classify_geometry 4-튜플 사용."""
    out = []
    for name, recs in sorted(unmapped_recs.items()):
        votes = {}            # category → [conf,...]
        subtype_votes = {}    # subtype  → count
        for r in recs:
            cat, conf, _reason, subtype = classify_geometry(r)
            if cat:
                votes.setdefault(cat, []).append(conf)
            if subtype:
                subtype_votes[subtype] = subtype_votes.get(subtype, 0) + 1
        geom_cat, geom_conf, geom_reason = None, 0.0, ""
        if votes:
            geom_cat = max(votes, key=lambda c: (len(votes[c]), sum(votes[c])))
            geom_conf = round(sum(votes[geom_cat]) / len(votes[geom_cat]), 2)
            geom_reason = next(
                (classify_geometry(r)[2] for r in recs
                 if classify_geometry(r)[0] == geom_cat), "")
        geom_subtype = (max(subtype_votes, key=subtype_votes.get)
                        if subtype_votes else None)
        score, tok, name_cat = fuzzy_layer_suggestion(name, rules)
        out.append({"layer": name, "count": len(recs), "source": kind,
                    "geom_guess": geom_cat, "geom_confidence": geom_conf,
                    "geom_reason": geom_reason, "geom_subtype": geom_subtype,
                    "name_guess": name_cat if score >= 0.5 else None,
                    "name_match": tok if score >= 0.5 else None,
                    "name_score": round(score, 2)})
    return out


# ── [MEP 물량산출] shapely 존 귀속 ───────────────────────────
def assign_zones(elements, zones):
    """각 요소의 무게중심이 어느 zone 폴리곤에 들어가는지 태깅."""
    if not HAS_SHAPELY or not zones:
        return
    polys = []
    for i, z in enumerate(zones):
        if z["kind"] == "polyline" and len(z["points"]) >= 3:
            polys.append((i, Polygon([(p[0], p[1]) for p in z["points"]])))
    for cat, items in elements.items():
        if cat == "zone":
            continue
        for rec in items:
            cx, cy = _centroid(rec)
            pt = Point(cx, cy)
            rec["zone"] = next((i for i, poly in polys if poly.contains(pt)), None)


def parse(dxf_path, rules, block_rules=DEFAULT_BLOCK_RULES, params=DEFAULT_PARAMS,
          use_ai=False, use_vision=False, api_key=None, ai_threshold=0.8):
    """DXF → geometry.json dict.
    use_ai: 텍스트 LLM 분류 + 고신뢰 자동적용. use_vision: Vision 폴백.
    ai_threshold: best_classification confidence 이 값 초과면 자동 카테고리 적용."""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    scale = 1000.0 if doc.header.get("$INSUNITS", 0) == 6 else 1.0
    result = {"source": dxf_path, "units": "mm", "scale_applied": scale, "params": params,
              "elements": {"wall": [], "column": [], "slab": [], "zone": [], "opening": [],
                           "pipe": [], "duct": [], "tray": [], "equipment": []},
              "warnings": []}
    unmapped, unmapped_blocks, n_inserts, unmapped_recs = {}, {}, 0, {}
    unmapped_block_recs = {}      # 블록명 → explode 기하 샘플(제안 통계용)
    unmapped_block_entities = {}  # 블록명 → INSERT 엔티티(AI 자동적용 재추출용)
    for e in msp:
        # [Phase 2] INSERT: 블록명 분류 → explode/마커. 레이어 폴백.
        if e.dxftype() == "INSERT":
            n_inserts += 1
            bname = e.dxf.get("name", "") or ""
            cat, attrs = classify(bname, block_rules) if block_rules else (None, {})
            if cat is None:
                cat, attrs = classify(e.dxf.layer, rules)  # 레이어 폴백
            if cat is None:
                unmapped_blocks[bname] = unmapped_blocks.get(bname, 0) + 1
                unmapped_block_entities.setdefault(bname, []).append(e)
                # explode 기하 샘플 수집(블록당 최대 30) — 제안 통계용
                bk = unmapped_block_recs.setdefault(bname, [])
                if len(bk) < 30:
                    try:
                        for _r in insert_to_records(e, scale, "wall", {}):
                            bk.append(_r)
                    except Exception:
                        pass
                continue
            elev = _entity_elevation(e, scale)
            for rec in insert_to_records(e, scale, cat, attrs):
                if attrs:
                    rec["overrides"] = attrs
                if cat in MEP_CATEGORIES:
                    annotate_mep(rec, cat, attrs, elev)
                else:
                    rec["z_base"] = elev  # [4b] 층 분리용 Z 기준
                result["elements"].setdefault(cat, []).append(rec)
            continue
        cat, attrs = classify(e.dxf.layer, rules)
        if cat is None:
            unmapped[e.dxf.layer] = unmapped.get(e.dxf.layer, 0) + 1
            bucket = unmapped_recs.setdefault(e.dxf.layer, [])
            # 전체 보관(AI 자동적용 재라우팅용). 제안 통계는 build_suggestions가 샘플링.
            try:
                sr = entity_to_record(e, scale)
                if sr:
                    sr["z_base"] = _entity_elevation(e, scale)
                    bucket.append(sr)
            except Exception:
                pass
            continue
        rec = entity_to_record(e, scale)
        if rec is None:
            result["warnings"].append(f"unhandled {e.dxftype()} @ {e.dxf.layer}")
            continue
        if attrs:
            rec["overrides"] = attrs
        elev = _entity_elevation(e, scale)
        if cat in MEP_CATEGORIES:
            annotate_mep(rec, cat, attrs, elev)
        else:
            rec["z_base"] = elev  # [4b] 층 분리용 Z 기준(단층=0.0)
        result["elements"].setdefault(cat, []).append(rec)

    # ── [Phase B] AI 분류 + 고신뢰 자동적용 (wall 후처리 전에 요소 합류) ─────────
    # 미매핑 레이어/블록 → 제안 생성 → (옵션)LLM/Vision → confidence>임계 자동 카테고리.
    # 자동적용된 wall/opening 도 아래 후처리(pairing/merge/snap/link)를 거치게 됨.
    _layer_sug = build_suggestions(unmapped_recs, rules, kind="layer") if unmapped else []
    _block_sug = (build_suggestions(unmapped_block_recs, block_rules, kind="block")
                  if unmapped_blocks else [])
    suggestions = _layer_sug + _block_sug
    if suggestions and (use_ai or use_vision):
        _ai_cache = _ai_cache_load(dxf_path)
        if use_ai:
            llm_tiebreak_suggestions(suggestions, api_key=api_key, cache=_ai_cache)
        if use_vision:
            try:
                import vision_classify as _vc
                _vc.vision_fallback(dxf_path, suggestions, unmapped_recs,
                                    api_key=api_key, cache=_ai_cache)
            except Exception as _ve:
                result["warnings"].append(f"Vision 폴백 스킵: {_ve}")
        _ai_cache_save(dxf_path, _ai_cache)
        apply_ai_classifications(result, suggestions,
                                 unmapped_recs, unmapped_block_entities,
                                 scale, threshold=ai_threshold)
    result["suggestions"] = suggestions

    # [Phase 1-pre] 개별 LINE 연결: 끝점 공유 2점 레코드 → 다중점 폴리라인 병합
    _n_raw = len(result["elements"]["wall"])
    result["elements"]["wall"] = join_connected_lines(result["elements"]["wall"])
    _n_joined = len(result["elements"]["wall"])
    if _n_raw != _n_joined:
        print(f"  [join] LINE 연결: {_n_raw}개 → {_n_joined}개 레코드")
        
    # --- [Column Bounding Box Grouping] ---
    # FreeCAD DXF export groups 6 lines (4 outline + 2 X-lines) into one unique layer (e.g. Block_C_600X835)
    # Group these lines by layer, and convert them to a single closed bounding box.
    col_by_layer = {}
    new_cols = []
    for c in result["elements"]["column"]:
        if c.get("closed") or c.get("kind") == "circle":
            new_cols.append(c)
        else:
            layer = c.get("layer", "")
            if layer:
                col_by_layer.setdefault(layer, []).append(c)
            else:
                new_cols.append(c)
                
    for layer, recs in col_by_layer.items():
        all_pts = []
        for r in recs:
            all_pts.extend(r.get("points", []))
        if not all_pts:
            continue
            
        try:
            from shapely.geometry import MultiPoint
            hull = MultiPoint(all_pts).convex_hull
            if hull.geom_type == 'Polygon':
                coords = list(hull.exterior.coords)
                pts = [[c[0], c[1]] for c in coords]
            else:
                pts = None
        except ImportError:
            pts = None
            
        if not pts:
            # Fallback to AABB if shapely fails or shape is invalid
            min_x = min(p[0] for p in all_pts)
            max_x = max(p[0] for p in all_pts)
            min_y = min(p[1] for p in all_pts)
            max_y = max(p[1] for p in all_pts)
            pts = [[min_x, min_y], [max_x, min_y], [max_x, max_y], [min_x, max_y], [min_x, min_y]]
            
        # Get width estimate
        xs = [p[0] for p in pts]
        min_x, max_x = min(xs), max(xs)
        
        merged = {
            "kind": "polyline",
            "closed": True,
            "points": pts,
            "centerline": pts,
            "width_detected": max_x - min_x,
            "confidence": 1.0,
            "pairing": "closed",
            "layer": layer,
            "z_base": recs[0].get("z_base", 0.0),
            "overrides": recs[0].get("overrides", {})
        }
        new_cols.append(merged)

    
    result["elements"]["column"] = new_cols
    # --------------------------------------

    # [Phase 1] 평행선 쌍 → 벽 중심선+두께 (zone 귀속 전에 재구성)
    result["elements"]["wall"] = detect_wall_pairs(result["elements"]["wall"], params)
    # [Phase 4.0] 같은 직선 위 쪼개진 세그먼트 재병합(코너 틈은 제외)
    _n_before = len(result["elements"]["wall"])
    result["elements"]["wall"] = merge_collinear_walls(result["elements"]["wall"], params)
    result["wall_merge"] = {"before": _n_before, "after": len(result["elements"]["wall"])}
    # [외곽선→센터선] single 2차 페어링 + 잔여 offset → 모든 비-closed 벽 centerline 통일
    _n_single_pre = sum(1 for w in result["elements"]["wall"] if w.get("pairing") == "single")
    result["elements"]["wall"] = repair_single_walls(result["elements"]["wall"], params)
    _n_repaired_pair = sum(1 for w in result["elements"]["wall"] if w.get("pairing") == "paired")
    _n_offset = sum(1 for w in result["elements"]["wall"] if w.get("pairing") == "single_offset")
    if _n_single_pre:
        print(f"  [single 정합] single {_n_single_pre}개 → 2차페어링 후 "
              f"paired합={_n_repaired_pair}, offset={_n_offset}")
    # [Phase 4.1] 코너 스냅: 끝점 불일치 → [4] boolean void 실패 방지
    _walls_pre_snap = result["elements"]["wall"]
    result["elements"]["wall"] = snap_wall_corners(result["elements"]["wall"])
    _snapped = sum(
        1 for a, b in zip(_walls_pre_snap, result["elements"]["wall"])
        if a.get("centerline") != b.get("centerline"))
    result["wall_merge"]["snapped_corners"] = _snapped
    # [Phase 4a] opening → 교차 벽 연결 (builder boolean void 전처리)
    link_openings_to_walls(result["elements"], params)
    paired = sum(1 for w in result["elements"]["wall"] if w.get("pairing") == "paired")
    single = sum(1 for w in result["elements"]["wall"] if w.get("pairing") == "single")
    offset = sum(1 for w in result["elements"]["wall"] if w.get("pairing") == "single_offset")
    result["wall_pairing"] = {"paired": paired, "single": single, "single_offset": offset}
    result["blocks"] = {"inserts": n_inserts, "unmapped": sum(unmapped_blocks.values())}
    result["mep"] = {c: len(result["elements"].get(c, [])) for c in MEP_CATEGORIES}
    # [Phase 4b] 층 감지: structural z_base 값 수집 → 100mm tol 양자화 → floors 목록
    _FLOOR_TOL = 100.0
    _z_vals = set()
    for _cat in ("wall", "column", "slab", "zone"):
        for _el in result["elements"].get(_cat, []):
            _z = _el.get("z_base", 0.0)
            _z_vals.add(round(_z / _FLOOR_TOL) * _FLOOR_TOL)
    if not _z_vals:
        _z_vals = {0.0}
    result["floors"] = [{"z": float(z), "label": f"Level_{i+1}"}
                        for i, z in enumerate(sorted(_z_vals))]
    assign_zones(result["elements"], result["elements"].get("zone", []))
    # 미매핑 로그 (suggestions 는 위 [Phase B] 에서 이미 result["suggestions"] 설정)
    # AI 자동적용으로 해소된 항목은 suggestion 에 applied=True 표기됨.
    _remain_layers = {k: v for k, v in unmapped.items()
                      if not _is_applied(result.get("suggestions", []), k, "layer")}
    _remain_blocks = {k: v for k, v in unmapped_blocks.items()
                      if not _is_applied(result.get("suggestions", []), k, "block")}
    if _remain_layers:
        result["warnings"].append("미매핑 레이어: " +
                                  ", ".join(f"{k}({v})" for k, v in _remain_layers.items()))
    if _remain_blocks:
        result["warnings"].append("미매핑 블록: " +
                                  ", ".join(f"{k}({v})" for k, v in _remain_blocks.items()))

    # ── 짧은 벽 클러스터 감지: 계단/장식선 오분류 경고 ─────────────────
    # 같은 레이어에 짧은 벽(< 400mm)이 5개 이상 → 계단/비구조선 의심
    _STAIR_LEN_THRESHOLD = 400.0
    _STAIR_COUNT_THRESHOLD = 5
    _short_by_layer: dict = {}
    for _w in result["elements"]["wall"]:
        _ln = _w.get("seg_length", 0.0)
        _ly = _w.get("layer", "")
        if _ln > 0 and _ln < _STAIR_LEN_THRESHOLD:
            _short_by_layer[_ly] = _short_by_layer.get(_ly, 0) + 1
    for _ly, _cnt in _short_by_layer.items():
        if _cnt >= _STAIR_COUNT_THRESHOLD:
            result["warnings"].append(
                f"[계단 의심] 레이어 '{_ly}': 짧은 벽 {_cnt}개 (<{int(_STAIR_LEN_THRESHOLD)}mm). "
                "계단/장식선이면 layer_map.csv 에서 카테고리를 'slab' 으로 변경하세요.")

    return result


# ── [MEP Phase 1] 인벤토리 스캐너 ────────────────────────────
def scan(dxf_path):
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    layers, types, xs, ys, poly_recs, blocks = {}, {}, [], [], [], {}
    for e in msp:
        layers[e.dxf.layer] = layers.get(e.dxf.layer, 0) + 1
        types[e.dxftype()] = types.get(e.dxftype(), 0) + 1
        if e.dxftype() == "INSERT":
            bn = e.dxf.get("name", "") or "?"
            blocks[bn] = blocks.get(bn, 0) + 1
        try:
            rec = entity_to_record(e, 1.0)
            if rec and rec["kind"] == "polyline":
                poly_recs.append(rec)
                for p in rec["points"]:
                    xs.append(p[0]); ys.append(p[1])
        except Exception:
            pass
    print(f"[인벤토리] {dxf_path}")
    print(f"  단위코드 $INSUNITS={doc.header.get('$INSUNITS', 0)}")
    if xs:
        print(f"  좌표범위 X[{min(xs):.0f}~{max(xs):.0f}] Y[{min(ys):.0f}~{max(ys):.0f}]")
    print("  레이어별 객체 수:")
    for k, v in sorted(layers.items(), key=lambda x: -x[1]):
        print(f"    {k:20s} {v}")
    print("  엔티티 타입별:")
    for k, v in sorted(types.items(), key=lambda x: -x[1]):
        print(f"    {k:20s} {v}")
    if blocks:
        print("  블록(INSERT) 참조별:")
        for k, v in sorted(blocks.items(), key=lambda x: -x[1]):
            print(f"    {k:20s} {v}")
    # [Phase 1] 평행선 쌍 후보 개략 추정(전체 폴리라인 대상, O(n²) 가드)
    segs = _wall_segments(poly_recs)
    if len(segs) <= 2000:
        pairs, _ = _find_wall_pairs(segs)
        print(f"  평행선 쌍 후보: {len(pairs)}개 (세그먼트 {len(segs)}개 중)")
    else:
        print(f"  평행선 쌍 후보: 생략(세그먼트 {len(segs)}개 > 2000)")


# ── [Phase 6a] LLM tie-break (모호 레이어 분류 보조) ─────────
# 원칙: LLM은 geometry.json의 category 값만 제안. FreeCAD 코드 생성 절대 금지.
#       자동매핑 없음 — 사용자가 제안을 검토해 layer_map.csv 에 직접 추가.
# 의존: anthropic SDK (선택). 없으면 graceful fallback(제안 없음).
# 트리거: geom_confidence < 0.7 AND name_score < 0.6 (둘 다 모호한 항목만 호출).
# API key: 환경변수 ANTHROPIC_API_KEY.
_VALID_CATEGORIES = ("wall", "column", "slab", "zone", "opening",
                     "pipe", "duct", "tray", "equipment")
_LLM_SYSTEM = (
    "You are a BIM/MEP layer/block classifier for architectural DXF drawings. "
    "Given a layer or block name and geometry statistics, suggest the most likely "
    "building element category, and for openings whether it is a door or a window. "
    "Reply ONLY with valid JSON: "
    "{\"category\": \"<one of: wall column slab zone opening pipe duct tray equipment>\", "
    "\"subtype\": \"<door|window|null>\", "
    "\"reason\": \"<one concise sentence in Korean>\", "
    "\"confidence\": <0.0-1.0>}. "
    "subtype is only meaningful when category is opening (door/window); otherwise null. "
    "NEVER generate FreeCAD code or geometry coordinates. Classification only."
)


def _llm_one(name, count, geom_guess, geom_conf, name_guess, name_score,
             api_key, source="layer", geom_stats=None, geom_subtype=None):
    """LLM 1회 호출 → (category, subtype, reason, confidence) or None."""
    try:
        import anthropic  # optional dep
    except ImportError:
        return None
    stat_line = f"기하 통계: {geom_stats}\n" if geom_stats else ""
    sub_line = f"기하 추정 서브타입: {geom_subtype}\n" if geom_subtype else ""
    prompt = (
        f"{'블록명' if source == 'block' else '레이어명'}: \"{name}\" (엔티티 {count}개)\n"
        f"기하 추정: {geom_guess or '불명'} (신뢰도 {geom_conf:.2f})\n"
        f"{sub_line}{stat_line}"
        f"이름 유사: {name_guess or '불명'} (유사도 {name_score:.2f})\n"
        f"가능한 카테고리: {', '.join(_VALID_CATEGORIES)}\n"
        "위 정보를 바탕으로 가장 적합한 카테고리(개구부면 door/window)를 JSON으로 답하세요."
    )
    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=160,
            system=_LLM_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        import re as _re
        raw = _re.sub(r"```[a-z]*\n?", "", raw).strip().rstrip("`").strip()
        parsed = json.loads(raw)
        cat = parsed.get("category", "").lower()
        if cat not in _VALID_CATEGORIES:
            return None
        subtype = parsed.get("subtype")
        if isinstance(subtype, str):
            subtype = subtype.lower().strip()
            if subtype not in ("door", "window"):
                subtype = None
        else:
            subtype = None
        return (cat, subtype,
                str(parsed.get("reason", ""))[:120],
                float(parsed.get("confidence", 0.5)))
    except Exception:
        return None


def _geom_stats_str(sug):
    """제안에 담긴 기하 추정 요약 → LLM feature 문자열."""
    bits = []
    if sug.get("geom_reason"):
        bits.append(sug["geom_reason"])
    return "; ".join(bits) if bits else None


def llm_tiebreak_suggestions(suggestions, api_key=None, cache=None):
    """모호(기하·이름 둘 다 낮은 신뢰도) 제안에만 LLM 호출 → llm_guess 필드 추가.
    레이어·블록 제안 모두 처리. cache: {sig_key: {...}} 재현성 캐시(있으면 우선)."""
    if not api_key:
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return  # API key 없으면 조용히 스킵
    if cache is None:
        cache = {}
    for s in suggestions:
        geom_conf = float(s.get("geom_confidence") or 0.0)
        name_score = float(s.get("name_score") or 0.0)
        if geom_conf >= 0.7 or name_score >= 0.6:
            continue  # 이미 충분히 자명 → LLM 불필요
        key = _sig_key("llm", s)
        cached = cache.get(key)
        if cached:
            result = (cached["cat"], cached.get("subtype"),
                      cached.get("reason", ""), cached.get("conf", 0.5))
        else:
            result = _llm_one(
                s["layer"], s["count"],
                s.get("geom_guess"), geom_conf,
                s.get("name_guess"), name_score,
                api_key, source=s.get("source", "layer"),
                geom_stats=_geom_stats_str(s),
                geom_subtype=s.get("geom_subtype"))
            if result:
                cache[key] = {"cat": result[0], "subtype": result[1],
                              "reason": result[2], "conf": result[3]}
        if result:
            cat, subtype, reason, conf = result
            s["llm_guess"] = cat
            s["llm_subtype"] = subtype
            s["llm_reason"] = reason
            s["llm_confidence"] = round(conf, 2)


# ── [Phase B] AI 분류 자동적용 + 재현성 캐시 ────────────────────────────────
def _sig_key(prefix, sug):
    """제안의 시그니처 해시 키(캐시용). 이름+개수+기하추정 기반 → 동일 도면 동일 키."""
    raw = f"{prefix}|{sug.get('source')}|{sug.get('layer')}|{sug.get('count')}|" \
          f"{sug.get('geom_guess')}|{sug.get('geom_subtype')}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def _ai_cache_path(dxf_path):
    return os.path.splitext(dxf_path)[0] + ".ai_cache.json"


def _ai_cache_load(dxf_path):
    p = _ai_cache_path(dxf_path)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _ai_cache_save(dxf_path, cache):
    if not cache:
        return
    try:
        with open(_ai_cache_path(dxf_path), "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _is_applied(suggestions, name, source):
    """해당 레이어/블록 제안이 AI 자동적용됐는지."""
    for s in suggestions:
        if s.get("layer") == name and s.get("source") == source and s.get("applied"):
            return True
    return False


def apply_ai_classifications(result, suggestions, unmapped_recs,
                             unmapped_block_entities, scale, threshold=0.8):
    """confidence>threshold 제안 → 해당 미매핑 레코드를 elements[cat]로 자동 이동.
    레이어: unmapped_recs[name] 의 보관 레코드 사용.
    블록: unmapped_block_entities[name] 엔티티를 결정 카테고리로 재추출.
    threshold 이하: needs_review=True 태깅(GUI 검토)."""
    n_applied = 0
    for s in suggestions:
        best = best_classification(s)
        if not best:
            continue
        s["final_guess"] = best["category"]
        s["final_subtype"] = best.get("subtype")
        s["final_confidence"] = best["confidence"]
        s["decided_by"] = best["decided_by"]
        if best["confidence"] <= threshold:
            s["needs_review"] = True
            continue
        cat = best["category"]
        subtype = best.get("subtype")
        name = s["layer"]
        recs = []
        if s.get("source") == "block":
            for e in unmapped_block_entities.get(name, []):
                try:
                    elev = _entity_elevation(e, scale)
                    for rec in insert_to_records(e, scale, cat, {}):
                        rec.setdefault("z_base", elev)
                        recs.append(rec)
                except Exception:
                    pass
        else:
            recs = unmapped_recs.get(name, [])
        for rec in recs:
            if cat in MEP_CATEGORIES:
                annotate_mep(rec, cat, {}, rec.get("z_base", 0.0))
            if subtype and cat == "opening":
                rec["subtype"] = subtype
                # opening 은 중심/반경 필요 → 폴리/원 → 중심 산출
                cen = _centroid(rec)
                rec.setdefault("center", [round(cen[0], 3), round(cen[1], 3)])
                rec.setdefault("radius", 50.0)
            rec["ai_applied"] = True
            result["elements"].setdefault(cat, []).append(rec)
        if recs:
            s["applied"] = True
            s["applied_count"] = len(recs)
            n_applied += 1
            result["warnings"].append(
                f"[AI 자동적용] {s.get('source')} '{name}' → {cat}"
                + (f"/{subtype}" if subtype else "")
                + f" ({best['confidence']:.2f}, {best['decided_by']}, {len(recs)}개)")
    if n_applied:
        result["warnings"].append(f"[AI] 자동적용 {n_applied}개 레이어/블록")
    return n_applied


def best_classification(sug):
    """제안에서 최종 카테고리/서브타입/신뢰도/근거 결정.
    우선순위: 이름 일치(>=0.6) > LLM > 기하. 4가지 신호 종합."""
    name_score = float(sug.get("name_score") or 0.0)
    geom_conf = float(sug.get("geom_confidence") or 0.0)
    llm_conf = float(sug.get("llm_confidence") or 0.0)
    vision_conf = float(sug.get("vision_confidence") or 0.0)
    cands = []  # (priority, conf, cat, subtype, reason, source)
    if sug.get("name_guess") and name_score >= 0.6:
        cands.append((3, name_score, sug["name_guess"], None,
                      f"이름 유사 {sug.get('name_match')}", "name"))
    if sug.get("vision_guess"):
        cands.append((2, vision_conf, sug["vision_guess"], sug.get("vision_subtype"),
                      sug.get("vision_reason", "Vision"), "vision"))
    if sug.get("llm_guess"):
        cands.append((2, llm_conf, sug["llm_guess"], sug.get("llm_subtype"),
                      sug.get("llm_reason", "LLM"), "llm"))
    if sug.get("geom_guess"):
        cands.append((1, geom_conf, sug["geom_guess"], sug.get("geom_subtype"),
                      sug.get("geom_reason", "기하"), "geom"))
    if not cands:
        return None
    # 우선순위 → 신뢰도 순
    cands.sort(key=lambda c: (c[0], c[1]), reverse=True)
    _pri, conf, cat, subtype, reason, src = cands[0]
    return {"category": cat, "subtype": subtype, "confidence": round(conf, 2),
            "reason": reason, "decided_by": src}


DWG_DXF_CHECKLIST = (
    "========================================================\n"
    "  DWG -> DXF 내보내기 체크리스트  (MEP Parser 전용)\n"
    "========================================================\n"
    "\n"
    "[1] 저장 형식\n"
    "  [ ] AutoCAD DXF 형식으로 저장 (*.dxf)\n"
    "  [ ] 버전: AutoCAD 2010 (R18) 이상 권장 (ezdxf 호환)\n"
    "  [ ] ASCII DXF 사용 (Binary DXF 지양)\n"
    "\n"
    "[2] 단위 설정  <- 가장 흔한 실수\n"
    "  [ ] $INSUNITS 를 반드시 확인:\n"
    "        4 = mm  (권장)\n"
    "        6 = m   (파서가 x1000 자동 보정)\n"
    "        0 = 무단위 (보정 없음 -> 크기 이상)\n"
    "  [ ] AutoCAD: '도면 단위' 대화상자에서 삽입 단위 = 밀리미터 설정\n"
    "\n"
    "[3] 레이어 보존\n"
    "  [ ] 레이어 이름 한글/특수문자 최소화 (ezdxf 인코딩 이슈 방지)\n"
    "  [ ] 레이어 동결/잠금 해제 후 저장 (동결 레이어 엔티티 누락)\n"
    "  [ ] 레이어 0 에 실제 요소 없도록 (분류 불가)\n"
    "\n"
    "[4] 엔티티 유형\n"
    "  [ ] 포함 확인: LINE, LWPOLYLINE, POLYLINE, CIRCLE, ARC, INSERT\n"
    "  [ ] XREF(외부참조) -> 바인딩(Bind) 후 저장, 또는 별도 DXF로 분리\n"
    "  [ ] SOLID/3DFACE 등 솔리드 엔티티는 파서가 무시함(경고로 표시)\n"
    "  [ ] MTEXT/TEXT -> 파서 무시(정상). 치수선도 무시.\n"
    "\n"
    "[5] 블록(INSERT) 처리\n"
    "  [ ] 기둥/문/창/장비를 블록으로 사용한 경우 block_map.csv 에 등록\n"
    "  [ ] EXPLODE 하지 말 것 - 블록 정보 유지해야 분류 정확\n"
    "  [ ] 동적 블록(Dynamic Block) -> 정적 블록으로 변환 권장\n"
    "\n"
    "[6] 좌표계\n"
    "  [ ] WCS(World Coordinate System) 기준으로 저장\n"
    "  [ ] UCS가 돌아가 있으면 WCS 로 전환 후 저장\n"
    "  [ ] 기준점(Origin) 확인: 너무 먼 좌표(e.g. 위경도) -> 파서 경고\n"
    "\n"
    "[7] 저장 전 점검\n"
    "  [ ] AUDIT 명령 실행 -> 오류 수정\n"
    "  [ ] PURGE 실행 -> 미사용 레이어/블록 정리\n"
    "  [ ] 저장 후 MEP Parser --scan 으로 레이어 목록 확인\n"
    "\n"
    "[8] 변환 검증 (MEP Parser)\n"
    "  [ ] python dxf_parser.py plan.dxf --scan\n"
    "        -> 레이어 목록 / 좌표범위 / 블록 목록 확인\n"
    "  [ ] 레이어 이름이 layer_map.csv 패턴과 매칭되는지 확인\n"
    "  [ ] 경고(미매핑 레이어) -> layer_map.csv 에 패턴 추가\n"
    "\n"
    "========================================================\n"
)


def print_checklist():
    """DWG→DXF 내보내기 체크리스트 출력."""
    print(DWG_DXF_CHECKLIST)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dxf", nargs="?", default=None,
                    help="DXF 도면 경로 (--checklist 시 생략 가능)")
    ap.add_argument("-m", "--map", default=None, help="layer_map.csv (없으면 기본규칙)")
    ap.add_argument("-b", "--blockmap", default=None, help="block_map.csv (없으면 기본규칙)")
    ap.add_argument("-o", "--out", default="geometry.json")
    ap.add_argument("--scan", action="store_true", help="인벤토리 통계만 출력")
    ap.add_argument("--checklist", action="store_true",
                    help="DWG->DXF 내보내기 체크리스트 출력 후 종료")
    ap.add_argument("--llm", action="store_true",
                    help="텍스트 AI 분류 + 고신뢰 자동적용 (ANTHROPIC_API_KEY 필요)")
    ap.add_argument("--vision", action="store_true",
                    help="Vision 폴백: DXF 렌더 → Claude Vision 영역 분류 (실험적)")
    ap.add_argument("--ai-threshold", type=float, default=0.8,
                    help="AI 자동적용 신뢰도 임계값 (기본 0.8)")
    ap.add_argument("--auto-map", action="store_true",
                    help="신뢰도 높은(0.8이상) 제안을 layer_map.csv에 자동 추가")
    args = ap.parse_args()

    if args.checklist:
        print_checklist()
        return

    if not args.dxf:
        ap.error("DXF 파일을 지정하세요 (또는 --checklist 사용)")

    if args.scan:
        scan(args.dxf)
        return

    rules = load_layer_map(args.map) if args.map else DEFAULT_LAYER_RULES
    block_rules = load_layer_map(args.blockmap) if args.blockmap else DEFAULT_BLOCK_RULES
    # use_ai/use_vision: parse() 내부에서 분류·자동적용(요소 합류 후 wall 후처리 보장)
    data = parse(args.dxf, rules, block_rules,
                 use_ai=args.llm, use_vision=args.vision,
                 ai_threshold=args.ai_threshold)

    if args.auto_map and args.map and data.get("suggestions"):
        appended_count = 0
        with open(args.map, "a", encoding="utf-8") as f:
            for s in data["suggestions"]:
                # LLM 또는 기하/이름 추론 중 신뢰도 높은 카테고리 채택
                best_cat = s.get("llm_guess") or s.get("geom_guess") or s.get("name_guess")
                conf = float(s.get("llm_confidence") or s.get("geom_confidence") or s.get("name_score") or 0.0)
                if best_cat and conf >= 0.8:
                    f.write(f"\n{s['layer']},{best_cat},,,")
                    appended_count += 1
        if appended_count > 0:
            print(f"  [Auto-Map] {appended_count}개 레이어를 {args.map} 파일에 자동 추가했습니다.")
            
        # LLM 결과 반영 후 json 재저장(아래 dump 에서 처리)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    el = data["elements"]
    zoned = sum(1 for c in ("wall", "column") for r in el[c] if r.get("zone") is not None)
    wp = data.get("wall_pairing", {})
    print(f"파싱 완료 -> {args.out}  (shapely={'on' if HAS_SHAPELY else 'off'})")
    print(f"  walls={len(el['wall'])} columns={len(el['column'])} "
          f"slabs={len(el['slab'])} zones={len(el['zone'])} openings={len(el['opening'])}")
    print(f"  벽 쌍 검출: paired={wp.get('paired', 0)} single={wp.get('single', 0)}")
    bk = data.get("blocks", {})
    print(f"  블록(INSERT): {bk.get('inserts', 0)}개 (미매핑 {bk.get('unmapped', 0)}개)")
    mep = data.get("mep", {})
    if any(mep.values()):
        print(f"  MEP 추출(데이터만): pipe={mep.get('pipe',0)} duct={mep.get('duct',0)} "
              f"tray={mep.get('tray',0)} equipment={mep.get('equipment',0)}")
    print(f"  존 귀속된 요소: {zoned}")
    for w in data["warnings"]:
        print("  [warn]", w)
    for s in data.get("suggestions", []):
        g = f"기하={s['geom_guess']}({s['geom_confidence']})" if s.get('geom_guess') else "기하=?"
        nm = (f"이름~{s['name_match']}->{s['name_guess']}({s['name_score']})"
              if s.get('name_guess') else "이름=?")
        llm = (f" [LLM->{s['llm_guess']}({s['llm_confidence']}) {s['llm_reason']}]"
               if s.get("llm_guess") else "")
        print(f"  [제안] '{s['layer']}'x{s['count']}: {g} {nm}{llm}")


if __name__ == "__main__":
    main()
