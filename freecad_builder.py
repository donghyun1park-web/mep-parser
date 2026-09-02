"""
freecad_builder.py  —  Phase 3+5
geometry.json 을 읽어 FreeCAD Arch(BIM) 객체를 생성하고
.FCStd 와 .ifc 로 내보낸다.

★ 반드시 FreeCAD 의 파이썬(freecadcmd)으로 실행한다 (일반 python3 아님):
    freecadcmd freecad_builder.py geometry.json out_model
    -> out_model.FCStd, out_model.ifc 생성

설계:
- 벽은 Arch.makeWall(baseline, width, height) 로 생성 -> 조인트/IFC 매핑이 견고.
- 기둥/슬래브는 닫힌 윤곽 -> 면 -> Arch.makeStructure 로 솔리드화.
- 결정론적. LLM 호출 없음. (LLM 은 실패 케이스 보조용으로만 차후 연결)
"""
import json
import os
import sys

# Windows 한글 출력 크래시 방지
if hasattr(sys.stdout, 'encoding') and sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

import FreeCAD as App
import Part
import Draft
import Arch

# z 기준면 규약은 geom_contract 에만 존재한다. 여기서 재구현하지 말 것.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import geom_contract as GC


def vec(p, z=0.0):
    return App.Vector(p[0], p[1], z)


def make_wire(points, closed, doc=None, label="_wall_base"):
    """Part.makePolygon → Part::Feature 베이스라인 생성.
    개별 직선 세그먼트(2점) 용. self-intersecting 없음."""
    pts = []
    for p in points:
        v = vec(p)
        if not pts or (pts[-1] - v).Length > 1.0:
            pts.append(v)
    if closed and len(pts) > 1 and (pts[-1] - pts[0]).Length <= 1.0:
        pts.pop()
    if len(pts) < 2:
        return None
    # 세그먼트 길이 최소값 확인 (너무 짧은 세그먼트 → Part.makePolygon 실패)
    total_len = sum((pts[i+1]-pts[i]).Length for i in range(len(pts)-1))
    if total_len < 1.0:
        return None
    try:
        poly_pts = pts + [pts[0]] if closed else pts
        wire_shape = Part.makePolygon(poly_pts)
        d = doc or App.ActiveDocument
        feat = d.addObject("Part::Feature", label)
        feat.Shape = wire_shape
        return feat
    except Exception:
        return None


def make_draft_wire(points, closed, doc=None, label="_wall_wire"):
    """Draft.make_wire 로 베이스라인 생성.
    다중점(3점 이상) 꺾인 벽 전용 — FreeCAD가 코너 Miter 자동 처리.
    실패 시 None 반환(호출자가 Part 폴백 처리)."""
    pts = []
    for p in points:
        v = vec(p)
        if not pts or (pts[-1] - v).Length > 1.0:
            pts.append(v)
    if len(pts) < 2:
        return None
    try:
        # FreeCAD 0.19+ snake_case API 시도, 구버전은 camelCase 폴백
        if hasattr(Draft, "make_wire"):
            w = Draft.make_wire(pts, closed=closed, face=False)
        else:
            w = Draft.makeWire(pts, closed=closed, face=False)
        if w:
            w.Label = label
        return w
    except Exception:
        return None


# ── 벽 체이닝: snap_wall_corners로 정렬된 끝점 기준 연결 ──────────────────────
# 목적: 연속 세그먼트 → 하나의 Draft Wire → Arch.makeWall 1개 → 코너 Miter 자동
# 핵심: snap_wall_corners(50mm tol) 이후 실제 연결된 끝점은 동일 좌표.
#       1mm 판정으로 오연결(T접합 근처) 방지.
_CHAIN_SNAP = 1.0   # 끝점 연결 판정 거리(mm) — snap 후 동일 좌표이므로 1mm 충분

def _chain_wall_segments(walls):
    """열린(non-closed) 벽 레코드들을 끝점 연결로 체이닝.
    Returns: list of (representative_el, chained_centerline_pts, member_indices)
      - representative_el : 체인의 첫 레코드(width/height 참조용)
      - chained_centerline_pts : 이어붙인 다중점 리스트
      - member_indices : 체인에 속한 원본 인덱스 리스트
    분리된 벽(연결 없음)은 단독 체인으로 반환.
    닫힌(pairing="closed") 레코드는 체이닝 대상 제외 — 별도 처리."""
    import math

    def cl(el):
        return el.get("centerline") or el.get("points", [])

    def pt_key(p):
        return (round(p[0] / _CHAIN_SNAP), round(p[1] / _CHAIN_SNAP))

    # 열린 벽만 체이닝 대상
    open_idx = [i for i, w in enumerate(walls)
                if not (w.get("closed") or w.get("pairing") == "closed")
                and len(cl(w)) >= 2]

    # 각 끝점 → (wall_idx, 'start'|'end') 매핑
    from collections import defaultdict
    ep_map = defaultdict(list)
    for i in open_idx:
        pts = cl(walls[i])
        ep_map[pt_key(pts[0])].append((i, "start"))
        ep_map[pt_key(pts[-1])].append((i, "end"))

    visited = set()
    chains = []

    for start in open_idx:
        if start in visited:
            continue
        visited.add(start)
        pts0 = cl(walls[start])
        chain_pts = list(pts0)
        chain_ids = [start]

        # 루프 감지용: 체인의 모든 점을 key set으로 관리
        chain_pt_keys = {pt_key(p) for p in chain_pts}

        # 앞쪽(tail) 연장
        while True:
            tail = chain_pts[-1]
            candidates = [x for x in ep_map.get(pt_key(tail), [])
                          if x[0] not in visited]
            if not candidates:
                break
            j, end = candidates[0]
            nxt = cl(walls[j])
            # 추가될 새 점들 중 이미 체인에 있는 점이 있으면 루프 → 중단
            new_pts = nxt[1:] if end == "start" else list(reversed(nxt))[1:]
            if any(pt_key(p) in chain_pt_keys for p in new_pts):
                break
            visited.add(j)
            chain_ids.append(j)
            chain_pts.extend(new_pts)
            chain_pt_keys.update(pt_key(p) for p in new_pts)

        # 뒤쪽(head) 연장
        while True:
            head = chain_pts[0]
            candidates = [x for x in ep_map.get(pt_key(head), [])
                          if x[0] not in visited]
            if not candidates:
                break
            j, end = candidates[0]
            nxt = cl(walls[j])
            new_pts = list(nxt) if end == "end" else list(reversed(nxt))
            new_pts = new_pts[:-1]  # 마지막 점(=head)은 이미 체인에 있음
            if any(pt_key(p) in chain_pt_keys for p in new_pts):
                break
            visited.add(j)
            chain_ids.insert(0, j)
            chain_pts = new_pts + chain_pts
            chain_pt_keys.update(pt_key(p) for p in new_pts)

        chains.append((walls[start], chain_pts, chain_ids))

    return chains


def build_walls(doc, walls, params):
    objs = []
    src_els = []
    idx_map = {}
    error_group = None

    # ── ① 닫힌 폴리선(pairing="closed"): solid extrusion ────────────────────────
    for i, el in enumerate(walls):
        if not (el.get("closed", False) or el.get("pairing") == "closed"):
            continue
        if el["kind"] != "polyline":
            continue
        baseline = el.get("centerline") or el.get("points", [])
        if len(baseline) < 3:
            continue
        z_base, _z1 = GC.z_range("wall", el, params)
        height = _z1 - z_base
        dxf_id = el.get("handle") or f"CLOSEDWALL_{i}"
        try:
            pts_3d = [App.Vector(p[0], p[1], z_base) for p in baseline]
            if (pts_3d[-1] - pts_3d[0]).Length > 1.0:
                pts_3d.append(pts_3d[0])
            wire = Part.makePolygon(pts_3d)
            face = Part.Face(wire)
            solid = face.extrude(App.Vector(0, 0, height))
            feat = doc.addObject("Part::Feature", f"ClosedWall_{i}")
            feat.Shape = solid
            struct = Arch.makeStructure(feat)
            struct.Label = f"ClosedWall_{i}"
            struct.addProperty("App::PropertyString", "DxfId", "Metadata", "")
            struct.DxfId = dxf_id
            objs.append(struct)
            src_els.append(el)
            idx_map[i] = struct
        except Exception as e:
            print(f"[warn] ClosedWall_{i} 생성 실패: {e}")

    # ── ② 열린 폴리선: 체이닝으로 묶어서 생성 (사용자 요청: 잘게 나누지 말고 하나의 벽체로) ───────────────────────────
    # repair_single_walls 로 모든 비-closed 벽이 centerline 기준이 되었으므로 체이닝을 복원합니다.
    open_walls = []
    open_wall_global_indices = []
    for i, el in enumerate(walls):
        if el.get("closed") or el.get("pairing") == "closed":
            continue
        if el.get("kind") != "polyline":
            continue
        open_walls.append(el)
        open_wall_global_indices.append(i)
        
    chains = _chain_wall_segments(open_walls)

    n_folds = 0
    for c_idx, (base_el, chain_pts, chain_ids) in enumerate(chains):
        if len(chain_pts) < 2:
            continue
        width = GC.width_of(base_el, params, "wall")
        z_base, _z1 = GC.z_range("wall", base_el, params)
        height = _z1 - z_base
        # [실측] 겹치는 동일선상 벽이 체이닝되면 baseline 이 180° 되꺾여(A→B→A방향)
        # Arch.makeWall(align="Center") 의 오프셋이 폭주 → 좌표 1e7 규모의 깨진 솔리드 생성
        # (isValid()=True 라 탐지도 안 됨). 되꺾임 지점에서 잘라 각각 별도 벽으로 만든다.
        subchains = _split_folded_chain(chain_pts)
        if len(subchains) > 1:
            n_folds += 1
        for s_idx, sub_pts in enumerate(subchains):
            if len(sub_pts) < 2:
                continue
            label = f"Wall_{c_idx}" if len(subchains) == 1 else f"Wall_{c_idx}_{s_idx}"
            try:
                base = make_wire(sub_pts, False, doc=doc, label=f"WallAxis_{label[5:]}")
                if not base:
                    continue
                wall = Arch.makeWall(base, width=width, height=height, align="Center")
                if not wall:
                    continue
                wall.Label = label
                wall.Placement.Base.z = z_base
                wall.addProperty("App::PropertyString", "DxfId", "Metadata", "")
                wall.DxfId = base_el.get("handle") or f"WALL_CHAIN_{c_idx}"
                objs.append(wall)
                src_els.append(base_el)
                if s_idx == 0:
                    for cid in chain_ids:
                        global_i = open_wall_global_indices[cid]
                        idx_map[global_i] = wall
            except Exception as e:
                print(f"[warn] {label} 체인 생성 실패: {e}")
    if n_folds:
        print(f"  [fix] 되꺾인 벽 체인 {n_folds}건 분할 (깨진 형상 방지)")

    return objs, idx_map, src_els


def _split_folded_chain(pts, cos_tol=-0.99):
    """벽 baseline 이 180° 되꺾이는 지점에서 분할.

    겹치는 동일선상 벽 세그먼트가 체이닝되면 A→B→A 형태가 되어
    Arch.makeWall 의 중심선 오프셋이 발산한다. 연속 방향벡터의
    코사인이 cos_tol 이하(≈172° 이상 꺾임)면 그 지점에서 자른다.
    """
    import math as _m

    def _dir(a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = _m.hypot(dx, dy)
        return (dx / L, dy / L) if L > 1e-9 else None

    out, cur = [], [pts[0]]
    prev = None
    for a, b in zip(pts, pts[1:]):
        v = _dir(a, b)
        if v is None:
            continue          # 길이 0 세그먼트는 건너뜀
        if prev is not None and (prev[0] * v[0] + prev[1] * v[1]) <= cos_tol:
            out.append(cur)   # 되꺾임 → 여기서 끊음
            cur = [a]
        cur.append(b)
        prev = v
    out.append(cur)
    return [c for c in out if len(c) >= 2]


def apply_opening_voids(idx_map, openings, params):
    """doc.recompute() 이후 호출. opening["wall_indices"] 벽에 원통 절단 적용.
    [Phase 4a]: Part.makeCylinder → wall_obj.Shape.cut() → 비파라메트릭 덮어쓰기.
    이후 doc.recompute() 를 호출하면 덮어씌워지므로 saveAs() 직전에 실행할 것.
    v1: 원통(radius=opening.radius, height=wall_height+여유). 사각형 개구부는 v2 예정."""
    if not openings:
        return 0
    d = params.get("wall", {})
    wall_h = float(d.get("height", 2800.0))
    margin = 100.0
    n_ok = 0
    for op in openings:
        r = float(op.get("radius", 50.0))
        if r <= 0:
            continue
        c = op.get("center") or [0, 0]
        cx, cy = float(c[0]), float(c[1])
        # 원통 cutter: 개구부 반경, 벽 높이+위아래 여유, z=-margin 기준점
        cutter = Part.makeCylinder(
            r, wall_h + margin * 2,
            App.Vector(cx, cy, -margin),
            App.Vector(0, 0, 1))
        for wi in op.get("wall_indices", []):
            if wi not in idx_map:
                continue
            wall_obj = idx_map[wi]
            try:
                cut = wall_obj.Shape.cut(cutter)
                if cut.isValid():
                    wall_obj.Shape = cut
                    n_ok += 1
                else:
                    print(f"[warn] opening void 형상 오류: Wall_{wi}")
            except Exception as e:
                print(f"[warn] opening void 실패 Wall_{wi}: {e}")
    return n_ok


def _opening_axes(op):
    """opening 의 host 벽 방향(u)·법선(n) 단위벡터. host_dir 없으면 X축 가정."""
    hd = op.get("host_dir")
    if hd and (abs(hd[0]) + abs(hd[1])) > 1e-6:
        import math as _m
        ln = _m.hypot(hd[0], hd[1])
        ux, uy = hd[0] / ln, hd[1] / ln
    else:
        ux, uy = 1.0, 0.0
    return (ux, uy), (-uy, ux)  # (along-wall, normal)


def _opening_solids(op, params):
    """opening 스펙 → (cutter_shape, leaf_shape|None, subtype). 좌표·배향 계산을 한 곳에 모아
    build_openings(다건)와 cut_window_into_wall(라이브 단건)이 공유. doc 미접근(순수 Part)."""
    d = params.get("wall", {})
    wall_h = float(d.get("height", 2800.0))
    margin = 100.0
    c = op.get("center") or [0, 0]
    cx, cy = float(c[0]), float(c[1])
    subtype = op.get("subtype")
    width = float(op.get("width") or (float(op.get("radius", 450.0)) * 2))
    depth = float(op.get("host_width") or d.get("width", 200.0)) + margin
    z_base = float(op.get("z_base", 0.0))
    if subtype == "window":
        sill = float(op.get("sill", 900.0)) + z_base
        oh = float(op.get("height", 1200.0))
    elif subtype == "door":
        sill = z_base
        oh = float(op.get("height", 2100.0))
    else:
        sill = z_base - margin
        oh = wall_h + margin * 2
    (ux, uy), (nx, ny) = _opening_axes(op)
    hw, hd_ = width / 2.0, depth / 2.0
    corners = [
        App.Vector(cx - ux * hw - nx * hd_, cy - uy * hw - ny * hd_, sill),
        App.Vector(cx + ux * hw - nx * hd_, cy + uy * hw - ny * hd_, sill),
        App.Vector(cx + ux * hw + nx * hd_, cy + uy * hw + ny * hd_, sill),
        App.Vector(cx - ux * hw + nx * hd_, cy - uy * hw + ny * hd_, sill),
    ]
    cutter = Part.Face(Part.makePolygon(corners + [corners[0]])).extrude(App.Vector(0, 0, oh))

    leaf = None
    if subtype in ("door", "window"):
        lh = 40.0 / 2.0  # 판 두께/2
        lc = [
            App.Vector(cx - ux * hw - nx * lh, cy - uy * hw - ny * lh, sill),
            App.Vector(cx + ux * hw - nx * lh, cy + uy * hw - ny * lh, sill),
            App.Vector(cx + ux * hw + nx * lh, cy + uy * hw + ny * lh, sill),
            App.Vector(cx - ux * hw + nx * lh, cy - uy * hw + ny * lh, sill),
        ]
        leaf = Part.Face(Part.makePolygon(lc + [lc[0]])).extrude(App.Vector(0, 0, oh))
    return cutter, leaf, subtype


def _add_leaf_feature(doc, leaf_shape, subtype, tag):
    """창틀/문짝 Part::Feature 추가 + IfcType 태깅. 라벨 반환."""
    feat = doc.addObject("Part::Feature",
                         f"{'Door' if subtype == 'door' else 'Window'}_{tag}")
    feat.Shape = leaf_shape
    try:
        arch_obj = Arch.makeEquipment(feat) if hasattr(Arch, "makeEquipment") else feat
    except Exception:
        arch_obj = feat
    target = arch_obj if arch_obj is not None else feat
    try:
        target.Label = f"{'Door' if subtype == 'door' else 'Window'}_{tag}"
        if hasattr(target, "IfcType"):
            target.IfcType = "Door" if subtype == "door" else "Window"
    except Exception:
        pass
    return target.Label


def cut_window_into_wall(doc, wall_obj, op, params, tag="live", add_leaf=True):
    """라이브 단건 삽입: 한 벽 객체에 opening void + (옵션)창틀/문짝 패널.
    freecad_live_addon.cmd_add_window_to_wall 이 호출. _opening_solids 로 build_openings 와 기하 공유.
    ★ 라이브 영속성: Arch Wall 의 .Shape 직접 덮어쓰기는 다음 recompute 에 사라지므로,
      파라메트릭 Part::Cut(Base=벽, Tool=cutter) 로 만들어 recompute 후에도 유지되게 한다.
    returns {"void": bool, "leaf": label|None, "cut_obj": label|None}."""
    cutter_shape, leaf, subtype = _opening_solids(op, params)
    out = {"void": False, "leaf": None, "cut_obj": None}
    try:
        cutter_feat = doc.addObject("Part::Feature", f"WinCutter_{tag}")
        cutter_feat.Shape = cutter_shape
        if hasattr(cutter_feat, "ViewObject") and cutter_feat.ViewObject:
            cutter_feat.ViewObject.Visibility = False
        cut = doc.addObject("Part::Cut", f"WallCut_{tag}")
        cut.Base = wall_obj
        cut.Tool = cutter_feat
        out["void"] = True
        out["cut_obj"] = cut.Label
    except Exception as e:
        print(f"[warn] cut_window_into_wall void(Part::Cut) 실패: {e}")
    if add_leaf and leaf is not None:
        try:
            out["leaf"] = _add_leaf_feature(doc, leaf, subtype, tag)
        except Exception as e:
            print(f"[warn] cut_window_into_wall leaf 실패: {e}")
    return out


def build_openings(doc, openings, wall_idx_map, params):
    """[Phase D] 문/창 3D. 사각형 void 로 벽 cut + 문짝/창틀 솔리드(IfcDoor/Window).
    - subtype='door'  : 바닥~height 개구, 얇은 문짝 판.
    - subtype='window': sill~sill+height 개구, 창틀+유리 판.
    - subtype 없음     : 사각 void 만(기존 동작 보강). radius 없을 때 원통 폴백.
    recompute 이후·saveAs 이전 호출(비파라메트릭 cut). returns (n_void, n_leaf).
    좌표·솔리드 계산은 _opening_solids 로 cut_window_into_wall(라이브 단건)과 공유."""
    if not openings:
        return (0, 0)
    n_void = 0
    n_leaf = 0
    log = ""
    for oi, op in enumerate(openings):
        try:
            cutter, leaf, subtype = _opening_solids(op, params)
        except Exception as e:
            print(f"[warn] opening_{oi} cutter 실패: {e}")
            continue

        # 벽 cut (개구부가 교차하는 모든 벽)
        for wi in op.get("wall_indices", []):
            wobj = wall_idx_map.get(wi)
            if wobj is None:
                continue
            try:
                cut = wobj.Shape.cut(cutter)
                if cut.isValid():
                    wobj.Shape = cut
                    n_void += 1
                else:
                    log += f"[warn] opening void 형상 오류: Wall_{wi}\n"
            except Exception as e:
                log += f"[warn] opening void 실패 Wall_{wi}: {e}\n"

        # 문짝/창틀 솔리드 (개구부당 1회) — IfcType 태깅
        if leaf is not None:
            try:
                _add_leaf_feature(doc, leaf, subtype, oi)
                n_leaf += 1
            except Exception as e:
                log += f"[warn] {subtype}_{oi} leaf 실패: {e}\n"
    if log:
        print(log, end="")
    return (n_void, n_leaf)


def build_columns(doc, columns, params):
    objs = []
    src_els = []
    for i, el in enumerate(columns):
        z0, z1 = GC.z_range("column", el, params)
        height = z1 - z0
        if el["kind"] == "circle":
            base = Draft.makeCircle(el["radius"], placement=App.Placement(
                vec(el["center"]), App.Rotation()))
        elif el["kind"] == "polyline" and el.get("closed"):
            base = make_wire(GC.ccw(el["points"]), True)
        else:
            continue
        base.Label = f"ColBase_{i}"
        col = Arch.makeStructure(base, height=height)
        col.IfcType = "Column"
        col.Label = f"Column_{i}"
        col.Normal = App.Vector(0, 0, 1)  # 감김 무관하게 +Z 압출(슬래브는 GC.ccw 로 동일 효과)
        col.Placement.Base.z = z0
        
        # [라운드트립 기반] DXF Handle 주입
        col.addProperty("App::PropertyString", "DxfId", "Metadata", "Original DXF Handle")
        col.DxfId = el.get("handle") or f"COLUMN_{i}"
        
        objs.append(col)
        src_els.append(el)
    return objs, src_els


def build_slabs(doc, slabs, params):
    objs = []
    src_els = []
    for i, el in enumerate(slabs):
        if el["kind"] != "polyline" or not el.get("closed"):
            continue
        cat = "beam" if (el.get("overrides", {}).get("ifc_type") == "Beam") else "slab"
        z0, z1 = GC.z_range(cat, el, params)
        thk = z1 - z0
        # [실측·확정] Arch.makeStructure 는 닫힌 와이어를 면으로 만들어 '면 법선' 방향으로 압출한다.
        # 법선은 폴리곤 감김 방향(winding)이 결정 → CW 면 -Z 로 압출되어 결과가 두께만큼 더 내려간다
        # (356개 중 CW 352개가 전부 z_base-thk 로 밀림, CCW 4개만 정상 — 상관계수 1.0 으로 확인).
        # 입력 감김에 좌우되지 않도록 항상 CCW(법선 +Z)로 정규화한다.
        base = make_wire(GC.ccw(el["points"]), True)
        base.Label = f"SlabBase_{i}"
        # 슬래브는 바닥(-thk) 방향으로 두께. 여기선 +Z 로 두고 배치만 내림.
        slab = Arch.makeStructure(base, height=thk)
        # [실측] 가늘고 긴(보 형태) 폴리곤에 IfcType="Slab" 강제 시 FreeCAD IFC exporter가
        # 조용히(에러 없이) 해당 오브젝트를 통째로 누락시킴(실측: 30/30 사례 재현·확정).
        # overrides.ifc_type 로 명시적 지정 가능(기본값은 기존 그대로 "Slab" — 하위호환).
        slab.IfcType = el.get("overrides", {}).get("ifc_type", "Slab")
        slab.Label = f"Slab_{i}"
        slab.Placement.Base.z = z0  # 기준면 해석은 geom_contract 가 단독 담당
        
        # [라운드트립 기반] DXF Handle 주입
        slab.addProperty("App::PropertyString", "DxfId", "Metadata", "Original DXF Handle")
        slab.DxfId = el.get("handle") or f"SLAB_{i}"
        
        objs.append(slab)
        src_els.append(el)
    return objs, src_els


def build_spaces(doc, zones, params):
    """zone 닫힌 폴리라인 → Arch.makeSpace 방 객체. IFC Space 태깅."""
    objs = []
    src_els = []
    for i, el in enumerate(zones):
        if el["kind"] != "polyline" or not el.get("closed"):
            continue
        pts = el["points"]
        if len(pts) < 3:
            continue
        try:
            # zone 은 벽 높이를 기본값으로 쓴다(방 높이). 규약은 geom_contract 단독.
            z_b, _z1 = GC.z_range("zone", el, params)
            room_h = _z1 - z_b
            if room_h <= 0:
                room_h = GC.height_of(el, params, "wall")
            pts_3d = [App.Vector(float(p[0]), float(p[1]), z_b) for p in pts]
            pts_3d.append(pts_3d[0])  # 닫기
            wire = Part.makePolygon(pts_3d)
            face = Part.Face(wire)
            solid = face.extrude(App.Vector(0, 0, room_h))
            feat = doc.addObject("Part::Feature", f"SpaceShape_{i}")
            feat.Shape = solid
            feat.Label = f"SpaceShape_{i}"
            space = Arch.makeSpace([feat])
            space.Label = f"Space_{i}"
            space.IfcType = "Space"
            objs.append(space)
            src_els.append(el)
        except Exception as e:
            print(f"[warn] Space_{i} 생성 실패: {e}")
    return objs, src_els


# ── [Phase 5a] MEP 3D 솔리드 ────────────────────────────────
def _pipe_solid(pts, radius, elev):
    """다점 중심선 → 원기둥 세그먼트 fuse. 모두 z=elev."""
    shapes = []
    for k in range(len(pts) - 1):
        p1 = App.Vector(float(pts[k][0]),   float(pts[k][1]),   elev)
        p2 = App.Vector(float(pts[k+1][0]), float(pts[k+1][1]), elev)
        seg = p2 - p1
        ln = seg.Length
        if ln < 1.0:
            continue
        cyl = Part.makeCylinder(radius, ln, p1, seg.normalize())
        shapes.append(cyl)
    if not shapes:
        return None
    result = shapes[0]
    for s in shapes[1:]:
        result = result.fuse(s)
    return result


def _rect_solid(pts, width, height, elev):
    """다점 중심선 → 사각단면(width×height) 세그먼트 fuse. duct/tray 용."""
    w2, h2 = width / 2.0, height / 2.0
    # 단면: Z축 방향 정렬 기준 사각형(XY 평면), 이후 각 세그먼트 방향으로 회전
    rect_pts = [App.Vector(-w2, -h2, 0), App.Vector(w2, -h2, 0),
                App.Vector(w2,  h2, 0), App.Vector(-w2,  h2, 0),
                App.Vector(-w2, -h2, 0)]
    rect_wire = Part.makePolygon(rect_pts)
    rect_face = Part.Face(rect_wire)
    shapes = []
    for k in range(len(pts) - 1):
        p1 = App.Vector(float(pts[k][0]),   float(pts[k][1]),   elev)
        p2 = App.Vector(float(pts[k+1][0]), float(pts[k+1][1]), elev)
        seg = p2 - p1
        ln = seg.Length
        if ln < 1.0:
            continue
        # Z→seg 방향 회전
        try:
            rot = App.Rotation(App.Vector(0, 0, 1), seg.normalize())
        except Exception:
            rot = App.Rotation()
        mat = App.Placement(p1, rot).Matrix
        face_rot = rect_face.transformed(mat)
        solid = face_rot.extrude(seg)
        shapes.append(solid)
    if not shapes:
        return None
    result = shapes[0]
    for s in shapes[1:]:
        result = result.fuse(s)
    return result


def _equip_solid(pts, elev, default_h=1000.0):
    """장비: 닫힌 폴리라인 footprint → extrude 솔리드."""
    pts_3d = [App.Vector(float(p[0]), float(p[1]), elev) for p in pts]
    pts_3d.append(pts_3d[0])
    wire = Part.makePolygon(pts_3d)
    face = Part.Face(wire)
    return face.extrude(App.Vector(0, 0, default_h))


def build_mep(doc, mep_elements):
    """MEP 중심선 → 3D 솔리드 Part::Feature. 구조 빌드와 분리(clash 검사용)."""
    objs = []
    for cat in ("pipe", "duct", "tray", "equipment"):
        for i, el in enumerate(mep_elements.get(cat, [])):
            elev = float(el.get("elevation", 0.0))
            pts = el.get("points", [])
            if len(pts) < 2:
                continue
            try:
                if cat == "pipe":
                    r = float(el.get("diameter") or 100.0) / 2.0
                    shape = _pipe_solid(pts, r, elev)
                elif cat in ("duct", "tray"):
                    w = float(el.get("width_mm") or 400.0)
                    h = float(el.get("height_mm") or (300.0 if cat == "duct" else 100.0))
                    shape = _rect_solid(pts, w, h, elev)
                elif cat == "equipment":
                    if not el.get("closed") or len(pts) < 3:
                        continue
                    shape = _equip_solid(pts, elev)
                else:
                    continue
                if shape is None or not shape.isValid():
                    print(f"[warn] MEP {cat}_{i} 형상 오류")
                    continue
                feat = doc.addObject("Part::Feature", f"{cat.capitalize()}_{i}")
                feat.Shape = shape
                feat.Label = f"{cat.capitalize()}_{i}"
                objs.append(feat)
            except Exception as e:
                print(f"[warn] MEP {cat}_{i}: {e}")
    return objs


# ── [Phase 5b] 구조 vs MEP 간섭(clash) 검사 ─────────────────
def check_clashes(struct_objs, mep_objs, vol_tol=1.0):
    """구조·MEP 솔리드 페어별 clash 목록 반환.
    AABB Broad-phase 먼저 → 겹침 후보만 shape.common() 수행 → O(S×M) 최악에서
    실제로는 O(겹침 후보)로 대폭 축소."""
    clashes = []
    if not mep_objs:
        return clashes

    # Broad-phase: BoundBox 교차 여부 확인 (cheap)
    def _bb(obj):
        try:
            s = getattr(obj, "Shape", None)
            if s is None or not s.isValid():
                return None
            bb = s.BoundBox
            # isValid() 없는 FreeCAD 버전 대비 hasattr 확인
            if hasattr(bb, "isValid") and not bb.isValid():
                return None
            if bb.XLength <= 0 and bb.YLength <= 0 and bb.ZLength <= 0:
                return None  # 퇴화 bounding box
            return bb
        except Exception:
            return None

    for so in struct_objs:
        sbb = _bb(so)
        if sbb is None:
            continue
        for mo in mep_objs:
            mbb = _bb(mo)
            if mbb is None:
                continue
            # AABB 겹침 확인 (Intersect)
            try:
                if not sbb.intersected(mbb):
                    continue
            except Exception:
                continue
            try:
                s_shape = so.Shape
                m_shape = mo.Shape
                common = s_shape.common(m_shape)
                if common.Volume > vol_tol:
                    clashes.append({"struct": so.Label, "mep": mo.Label,
                                    "volume_mm3": round(common.Volume, 1)})
            except Exception:
                pass
    return clashes


def main():
    import traceback as _tb
    try:
        _main_impl()
    except Exception as _fatal:
        print("[FATAL] 빌드 중 예외 발생:")
        _tb.print_exc()
        sys.exit(1)


def _main_impl():
    geom_path = os.environ.get("MEP_GEOMETRY") or (sys.argv[1] if len(sys.argv) > 1 else None)
    out_base  = os.environ.get("MEP_OUT")      or (sys.argv[2] if len(sys.argv) > 2 else "out_model")
    if not geom_path:
        print("usage: MEP_GEOMETRY=geometry.json MEP_OUT=out freecadcmd freecad_builder.py")
        sys.exit(1)

    print(f"[1/8] JSON 로드: {geom_path}")
    with open(geom_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    params = data.get("params", {})
    el = data["elements"]
    print(f"  walls={len(el.get('wall',[]))} cols={len(el.get('column',[]))}"
          f" slabs={len(el.get('slab',[]))} openings={len(el.get('opening',[]))}")

    import time as _time

    print("[2/8] 문서 생성")
    doc = App.newDocument("BIM")
    # Part::Feature.Shape = wire_shape 는 shape 직접 할당 → 개별 recompute 가 빠름.
    # setSkipRecompute 는 오히려 배치 recompute 비용 증가 → 사용 안 함.

    _t0 = _time.time()
    print("[3/8] 벽체 빌드")
    walls, wall_idx_map, wall_src = build_walls(doc, el.get("wall", []), params)
    print(f"  → {len(walls)}개 벽체 ({_time.time()-_t0:.1f}s)")

    print("[4/8] 기둥/슬래브/공간/MEP 빌드")
    _t1 = _time.time()
    cols,   col_src   = build_columns(doc, el.get("column", []), params)
    slabs,  slab_src  = build_slabs(doc, el.get("slab", []), params)
    spaces, space_src = build_spaces(doc, el.get("zone", []), params)
    mep_objs          = build_mep(doc, el)
    print(f"  → cols={len(cols)} slabs={len(slabs)} spaces={len(spaces)} mep={len(mep_objs)} ({_time.time()-_t1:.1f}s)")

    print("[5/8] recompute")
    _t2 = _time.time()
    try:
        doc.recompute()
        print(f"  → {len(doc.Objects)}개 객체 ({_time.time()-_t2:.1f}s)")
    except Exception as _re:
        print(f"  [warn] recompute 오류: {_re}")

    print("[6/8] 층 컨테이너 생성")
    _FLOOR_TOL  = 100.0
    floors_info = data.get("floors") or [{"z": 0.0, "label": "Level_1"}]
    _fz_list = [float(f.get("z", 0.0)) for f in floors_info]

    # ★ 어느 Floor 에도 못 들어간 객체는 Arch.makeBuilding 트리 밖에 남아
    #   export([building]) 에서 조용히 빠진다(보 135개가 IfcBeam:0 이 된 원인).
    #   두 Floor 에 동시 매칭되면 중복 삽입된다. 둘 다 세어서 게이트로 넘긴다.
    _hits = {}          # id(obj) -> 매칭된 층 인덱스 목록
    _meta = {}          # id(obj) -> (라벨, z_base)

    def _at_floor(obj_list, src_list, fz, fi):
        out = []
        for obj, el_r in zip(obj_list, src_list):
            zb = float(el_r.get("z_base", el_r.get("elevation", 0.0)) or 0.0)
            _meta.setdefault(id(obj), (getattr(obj, "Label", "?"), zb))
            _hits.setdefault(id(obj), [])
            if abs(zb - fz) < _FLOOR_TOL:
                _hits[id(obj)].append(fi)
                out.append(obj)
        return out

    def _in_story(obj_list, src_list, fi):
        """MEP 전용 배정. 배관 elevation(예: 2600)이 층 z 와 '일치' 할 리 없다 —
        층 안에서 도는 설비이므로 elevation 을 포함하는 층(가장 큰 z <= elev)에 넣는다.
        정확 매칭을 요구하면 MEP 는 영원히 고아가 되어 IFC 에서 빠진다."""
        out = []
        for obj, el_r in zip(obj_list, src_list):
            elev = float(el_r.get("elevation", 0.0) or 0.0)
            _meta.setdefault(id(obj), (getattr(obj, "Label", "?"), elev))
            _hits.setdefault(id(obj), [])
            below = [k for k, z in enumerate(_fz_list) if z <= elev + _FLOOR_TOL]
            owner = max(below, key=lambda k: _fz_list[k]) if below else \
                min(range(len(_fz_list)), key=lambda k: abs(_fz_list[k] - elev))
            if owner == fi:
                _hits[id(obj)].append(fi)
                out.append(obj)
        return out

    # MEP 도 그룹핑 대상에 넣는다 — 지금까지 src_els 가 없어 구조적으로 제외돼
    # IFC 에서 항상 누락됐다. elevation 을 z_base 자리에 넣어 동일하게 다룬다.
    mep_src = []
    for _cat in ("pipe", "duct", "tray", "equipment"):
        for _r in el.get(_cat, []):
            mep_src.append(_r)
    if len(mep_src) != len(mep_objs):
        mep_src = [{"elevation": 0.0}] * len(mep_objs)   # 개수 불일치 시 안전 폴백

    floor_containers = []
    for fi, finfo in enumerate(floors_info):
        fz    = float(finfo.get("z", 0.0))
        flbl  = finfo.get("label", f"Level_{fi+1}")
        fw  = _at_floor(walls,   wall_src,  fz, fi)
        fc  = _at_floor(cols,    col_src,   fz, fi)
        fs  = _at_floor(slabs,   slab_src,  fz, fi)
        fsp = _at_floor(spaces,  space_src, fz, fi)
        fm  = _in_story(mep_objs, mep_src, fi)
        try:
            fl = Arch.makeFloor(fw + fc + fs + fsp + fm)
            fl.Label = flbl
            fl.Placement.Base.z = fz
            floor_containers.append(fl)
            print(f"  {flbl}: walls={len(fw)} cols={len(fc)} slabs={len(fs)}"
                  + (f" mep={len(fm)}" if fm else ""))
        except Exception as _fe:
            print(f"  [warn] makeFloor 실패({flbl}): {_fe}")

    _orphans = [_meta[k] for k, v in _hits.items() if not v]
    _dups    = [_meta[k] for k, v in _hits.items() if len(v) > 1]
    if _orphans:
        print(f"  [!] 어느 층에도 속하지 않은 객체 {len(_orphans)}개 — IFC 에서 누락된다")
        print(f"      floors z = {[round(z) for z in _fz_list]}")
        for lbl, zb in _orphans[:5]:
            print(f"      {lbl}  z_base={zb:.0f}")
    if _dups:
        print(f"  [!] 두 층에 중복 삽입된 객체 {len(_dups)}개")
    try:
        building = Arch.makeBuilding(floor_containers)
        building.Label = "Building"
        doc.recompute()
    except Exception as _be:
        print(f"  [warn] makeBuilding/recompute: {_be}")

    print("[7/8] 문/창 3D (사각형 void + 문짝/창틀) + clash 검사")
    n_voids, n_leaf = build_openings(doc, el.get("opening", []), wall_idx_map, params)
    print(f"  개구부 void={n_voids}개, 문짝/창틀={n_leaf}개")
    struct_objs = walls + cols + slabs
    clashes = check_clashes(struct_objs, mep_objs)
    if clashes:
        print(f"  [CLASH] 간섭 {len(clashes)}건")
    else:
        print("  [CLASH] 간섭 없음")

    fcstd = f"{out_base}.FCStd"
    ifc   = f"{out_base}.ifc"

    # ── 보조 형상 숨김 ────────────────────────────────────────────────────────
    # Arch 객체의 Base(벽 축선·슬래브 윤곽)는 GUI 워크벤치에서 자동으로 숨겨지지만
    # freecadcmd(headless)에는 ViewObject 가 없어 Visibility=true 로 저장된다.
    # → GUI 로 열면 Z=0 평면에 축선 수백 개가 함께 보임. App 레벨 Visibility 로 숨긴다.
    _n_hidden = 0
    for _o in doc.Objects:
        _lbl = getattr(_o, "Label", "")
        if _lbl.startswith(("WallAxis", "SlabBase", "ColBase", "SpaceShape", "_wall_")):
            try:
                _o.Visibility = False
                _n_hidden += 1
            except Exception:
                pass
    if _n_hidden:
        print(f"  보조 형상 {_n_hidden}개 숨김(축선/베이스)")

    # ── 게이트 준비: 형상검증은 저장 '전' 에 한다 ────────────────────────────
    # 종전에는 saveAs/IFC export 이후에 세고 경고만 했다 — 깨진 형상이 이미
    # 디스크에 쓰인 뒤였고 아무도 그 경고에 반응하지 않았다.
    try:
        n_err = sum(1 for o in doc.Objects if _shape_ok(o) is False)
    except Exception:
        n_err = 0

    _bbox = None
    try:
        import FreeCAD as _A
        _bb = None
        for _o in doc.Objects:
            _s = getattr(_o, "Shape", None)
            if _s is None or _s.isNull():
                continue
            b = _s.BoundBox
            if b.XLength > 1e9:
                continue
            _bb = b if _bb is None else _bb.united(b)
        if _bb is not None:
            _bbox = [_bb.XMin, _bb.YMin, _bb.ZMin, _bb.XMax, _bb.YMax, _bb.ZMax]
    except Exception:
        pass

    # intent 는 원본 카테고리 수가 아니라 **실제 부여된 IfcType** 으로 센다.
    # 닫힌 폴리선 벽은 Arch.makeStructure 라 IfcWall 이 아니고, 보는 slab 버킷에
    # 있지만 IfcType=Beam 이다. 원본 수로 세면 정상 빌드가 불일치로 걸린다.
    _by_ifctype = {}
    for _o in (walls + cols + slabs):
        _t = str(getattr(_o, "IfcType", "") or "").strip().lower().replace(" ", "")
        if _t:
            _by_ifctype[_t] = _by_ifctype.get(_t, 0) + 1
    build_stats = {
        "intent": {"wall": _by_ifctype.get("wall", 0),
                   "column": _by_ifctype.get("column", 0),
                   "slab": _by_ifctype.get("slab", 0),
                   "beam": _by_ifctype.get("beam", 0)},
        "ifctype_counts": _by_ifctype,
        "built": {"walls": len(walls), "columns": len(cols), "slabs": len(slabs),
                  "spaces": len(spaces), "mep": len(mep_objs),
                  "floors": len(floor_containers)},
        "floor_orphans": len(_orphans), "floor_dups": len(_dups),
        "floor_orphan_detail": [{"label": l, "z_base": z} for l, z in _orphans[:20]],
        "invalid_shapes": n_err,
        "bbox": _bbox,
        "openings_void": n_voids, "opening_leaves": n_leaf,
        "clashes": [{"struct": c.get("struct"), "mep": c.get("mep"),
                     "volume_mm3": c.get("volume_mm3")} for c in (clashes or [])],
    }

    # ── 게이트 ①: 저장 전 검사 ───────────────────────────────────────────────
    # 실패하면 마커를 출력하지 않는다. GUI(mep_gui._build_done)와 MCP(build_freecad)는
    # 둘 다 FCSTD_DST 마커가 있어야만 파일을 옮기므로, 마커를 withhold 하는 것만으로
    # 소비자 코드 변경 없이 fail-closed 가 된다.
    _allow = os.environ.get("MEP_ALLOW_ERRORS", "").strip() not in ("", "0", "false")
    _rep = None
    try:
        import verify as _V
        _rep = _V.verify_build(data, build_stats, None)
    except Exception as _ve:
        print(f"  [warn] 검증 모듈 로드 실패(게이트 미작동): {_ve}")

    print("[8/8] 저장")
    _HERE_B = os.path.dirname(os.path.abspath(__file__))
    _stats_path = os.path.abspath(f"{out_base}.build.json")
    if _rep is not None and _rep.failed and not _allow:
        build_stats["verify"] = _rep.to_dict()
        _write_json(_stats_path, build_stats)
        print("  [게이트] 저장 전 검사 실패 — 산출물을 내보내지 않는다:")
        print(_rep.text())
        print(f"BUILD_FAILED:{_stats_path}", flush=True)
        print("  (검사를 무시하고 강제 저장하려면 MEP_ALLOW_ERRORS=1)")
        sys.exit(2)
    if _rep is not None and _rep.failed and _allow:
        print("  [게이트] 검사 실패했으나 MEP_ALLOW_ERRORS=1 로 강제 진행:")
        print(_rep.text())
        build_stats["verify_status"] = "failed_override"

    # ── saveAs: ASCII 임시경로 저장 → 호출자가 최종경로로 이동 ────────────────
    # FreeCAD C++ saveAs 는 한글/공백 경로에서 조용히 실패하거나 빈 파일 생성.
    # 임시파일명에 pid+시각을 넣어 동시 빌드 충돌을 막는다.
    _tag = f"{os.getpid()}.{int(_time.time())}"
    _tmp_fcstd = os.path.join(_HERE_B, f"_mep_tmp_out.{_tag}.FCStd")
    _tmp_ifc   = os.path.join(_HERE_B, f"_mep_tmp_out.{_tag}.ifc")
    print(f"  saveAs → {_tmp_fcstd}")
    _saved_fcstd = False
    try:
        doc.saveAs(_tmp_fcstd)
        _saved_fcstd = os.path.exists(_tmp_fcstd) and os.path.getsize(_tmp_fcstd) > 0
        print(f"  파일 크기: {os.path.getsize(_tmp_fcstd) if _saved_fcstd else 0} bytes")
    except Exception as _se:
        print(f"[ERROR] saveAs 실패: {_se}")
        import traceback as _tb2; _tb2.print_exc()

    if _saved_fcstd:
        print(f"FCSTD_TMP:{_tmp_fcstd}", flush=True)
        print(f"FCSTD_DST:{os.path.abspath(fcstd)}", flush=True)
    else:
        print(f"[ERROR] FCStd 저장 실패 — 파일 없음: {_tmp_fcstd}", flush=True)

    # IFC 내보내기 (임시 ASCII 경로 → 이동)
    _exporter = None
    for _imp in ("importers.exportIFC", "exportIFC", "importIFC"):
        try:
            mod = __import__(_imp, fromlist=["export"])
            if hasattr(mod, "export"):
                _exporter = mod
                break
        except Exception:
            continue
    _ifc_ok = False
    try:
        if _exporter is None:
            raise ImportError("IFC exporter 모듈을 찾지 못함")
        _exporter.export([building], _tmp_ifc)
        _ifc_ok = os.path.exists(_tmp_ifc)
    except Exception as e:
        print("[warn] IFC export 실패:", e, flush=True)

    # ── 게이트 ②: IFC 는 별도 게이팅 ────────────────────────────────────────
    # 전부-아니면-전무보다 낫다. 멀쩡한 FCStd 는 남기고 IFC 만 사유와 함께 보류한다.
    if _ifc_ok:
        _rep2 = None
        try:
            import verify as _V2
            _rep2 = _V2.verify_build(data, build_stats, _tmp_ifc)
        except Exception:
            pass
        if _rep2 is not None and _rep2.failed and not _allow:
            build_stats["verify_ifc"] = _rep2.to_dict()
            print("  [게이트] IFC 검사 실패 — IFC 를 내보내지 않는다:")
            print(_rep2.text())
            print(f"IFC_FAILED:{_stats_path}", flush=True)
        else:
            if _rep2 is not None:
                build_stats["verify_ifc"] = _rep2.to_dict()
                if _rep2.failed:
                    print("  [게이트] IFC 검사 실패했으나 MEP_ALLOW_ERRORS=1 로 진행")
            print(f"IFC_TMP:{_tmp_ifc}", flush=True)
            print(f"IFC_DST:{os.path.abspath(ifc)}", flush=True)

    if _rep is not None:
        build_stats.setdefault("verify", _rep.to_dict())
    _write_json(_stats_path, build_stats)

    print(f"빌드 완료: floors={len(floor_containers)} walls={len(walls)}"
          f" columns={len(cols)} slabs={len(slabs)} spaces={len(spaces)}"
          f" mep={len(mep_objs)}"
          + (f" openings_void={n_voids}" if n_voids else "")
          + (f" clashes={len(clashes)}" if clashes else ""))
    print(f"  -> {fcstd}")
    print(f"  -> {ifc}")
    print(f"  -> {_stats_path}")
    if n_err:
        print(f"  [warn] 형상 검증 실패 객체 {n_err}개")


def _write_json(path, obj):
    try:
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"  [warn] build.json 저장 실패: {e}")


def _shape_ok(o):
    """Shape 유효성 검사. 예외 발생 시 None 반환."""
    try:
        s = getattr(o, "Shape", None)
        if s is None:
            return None
        return s.isValid()
    except Exception:
        return None


# main() 실행 조건:
#   - python freecad_builder.py 직접 실행 (__name__=="__main__")
#   - freecadcmd freecad_builder.py (이때 __name__=="freecad_builder", MEP_GEOMETRY 환경변수 설정됨)
# 라이브 애드온이 `import freecad_builder` 할 때(MEP_GEOMETRY 없음)는 main() 실행 안 됨.
if __name__ == "__main__" or os.environ.get("MEP_GEOMETRY"):
    main()
