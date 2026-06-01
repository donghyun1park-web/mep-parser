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
if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

import FreeCAD as App
import Part
import Draft
import Arch


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
    d = params.get("wall", {})
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
        ov = el.get("overrides", {})
        height = float(ov.get("height", d.get("height", 2800.0)))
        z_base = float(el.get("z_base", 0.0))
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

    # ── ② 열린 폴리선: Part.makePolygon → Arch.makeWall (안정 우선) ──────────────
    # Draft.make_wire 는 setSkipRecompute 환경에서 shape 미계산 → Arch.makeWall 실패.
    # Part::Feature 는 Shape 직접 할당 → recompute 없이도 유효 → 안정적 저장 보장.
    for i, el in enumerate(walls):
        if el.get("closed") or el.get("pairing") == "closed":
            continue
        if el["kind"] != "polyline":
            continue
        baseline = el.get("centerline") or el.get("points", [])
        if len(baseline) < 2:
            continue
        ov = el.get("overrides", {})
        width  = float(el.get("width_detected")
                       or ov.get("width", d.get("width", 200.0)))
        height = float(ov.get("height", d.get("height", 2800.0)))
        z_base = float(el.get("z_base", 0.0))
        try:
            base = make_wire(baseline, False, doc=doc, label=f"WallAxis_{i}")
            if not base:
                raise ValueError("make_wire returned None")
            wall = Arch.makeWall(base, width=width, height=height)
            if not wall:
                raise ValueError("Arch.makeWall returned None")
            wall.Label = f"Wall_{i}"
            wall.Placement.Base.z = z_base
            wall.addProperty("App::PropertyString", "DxfId", "Metadata", "")
            wall.DxfId = el.get("handle") or f"WALL_{i}"
            objs.append(wall)
            src_els.append(el)
            idx_map[i] = wall
        except Exception as e:
            print(f"[warn] Wall_{i} 생성 실패: {e}")
    return objs, idx_map, src_els


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


def build_columns(doc, columns, params):
    d = params.get("column", {})
    objs = []
    src_els = []
    for i, el in enumerate(columns):
        height = float(el.get("overrides", {}).get("height", d.get("height", 3000.0)))
        if el["kind"] == "circle":
            base = Draft.makeCircle(el["radius"], placement=App.Placement(
                vec(el["center"]), App.Rotation()))
        elif el["kind"] == "polyline" and el.get("closed"):
            base = make_wire(el["points"], True)
        else:
            continue
        base.Label = f"ColBase_{i}"
        col = Arch.makeStructure(base, height=height)
        col.IfcType = "Column"
        col.Label = f"Column_{i}"
        col.Placement.Base.z = float(el.get("z_base", 0.0))  # [4b]
        
        # [라운드트립 기반] DXF Handle 주입
        col.addProperty("App::PropertyString", "DxfId", "Metadata", "Original DXF Handle")
        col.DxfId = el.get("handle") or f"COLUMN_{i}"
        
        objs.append(col)
        src_els.append(el)
    return objs, src_els


def build_slabs(doc, slabs, params):
    d = params.get("slab", {})
    objs = []
    src_els = []
    for i, el in enumerate(slabs):
        if el["kind"] != "polyline" or not el.get("closed"):
            continue
        thk = float(el.get("overrides", {}).get("thickness", d.get("thickness", 200.0)))
        base = make_wire(el["points"], True)
        base.Label = f"SlabBase_{i}"
        # 슬래브는 바닥(-thk) 방향으로 두께. 여기선 +Z 로 두고 배치만 내림.
        slab = Arch.makeStructure(base, height=thk)
        slab.IfcType = "Slab"
        slab.Label = f"Slab_{i}"
        z_b = float(el.get("z_base", 0.0))
        slab.Placement.Base.z = z_b - thk  # [4b] 층 Z + 슬래브 하향 오프셋
        
        # [라운드트립 기반] DXF Handle 주입
        slab.addProperty("App::PropertyString", "DxfId", "Metadata", "Original DXF Handle")
        slab.DxfId = el.get("handle") or f"SLAB_{i}"
        
        objs.append(slab)
        src_els.append(el)
    return objs, src_els


def build_spaces(doc, zones, params):
    """zone 닫힌 폴리라인 → Arch.makeSpace 방 객체. IFC Space 태깅."""
    d = params.get("wall", {})
    room_h = float(d.get("height", 2800.0))
    objs = []
    src_els = []
    for i, el in enumerate(zones):
        if el["kind"] != "polyline" or not el.get("closed"):
            continue
        pts = el["points"]
        if len(pts) < 3:
            continue
        try:
            z_b = float(el.get("z_base", 0.0))
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

    def _at_floor(obj_list, src_list, fz):
        return [obj for obj, el_r in zip(obj_list, src_list)
                if abs(float(el_r.get("z_base", 0.0)) - fz) < _FLOOR_TOL]

    floor_containers = []
    for fi, finfo in enumerate(floors_info):
        fz    = float(finfo.get("z", 0.0))
        flbl  = finfo.get("label", f"Level_{fi+1}")
        fw  = _at_floor(walls,  wall_src,  fz)
        fc  = _at_floor(cols,   col_src,   fz)
        fs  = _at_floor(slabs,  slab_src,  fz)
        fsp = _at_floor(spaces, space_src, fz)
        try:
            fl = Arch.makeFloor(fw + fc + fs + fsp)
            fl.Label = flbl
            fl.Placement.Base.z = fz
            floor_containers.append(fl)
            print(f"  {flbl}: walls={len(fw)} cols={len(fc)}")
        except Exception as _fe:
            print(f"  [warn] makeFloor 실패({flbl}): {_fe}")
    try:
        building = Arch.makeBuilding(floor_containers)
        building.Label = "Building"
        doc.recompute()
    except Exception as _be:
        print(f"  [warn] makeBuilding/recompute: {_be}")

    print("[7/8] Opening void + clash 검사")
    n_voids = apply_opening_voids(wall_idx_map, el.get("opening", []), params)
    print(f"  void 적용: {n_voids}개")
    struct_objs = walls + cols + slabs
    clashes = check_clashes(struct_objs, mep_objs)
    if clashes:
        print(f"  [CLASH] 간섭 {len(clashes)}건")
    else:
        print("  [CLASH] 간섭 없음")

    fcstd = f"{out_base}.FCStd"
    ifc   = f"{out_base}.ifc"

    # ── saveAs: ASCII 임시경로 저장 → GUI Python이 최종경로로 이동 ────────────
    # FreeCAD C++ saveAs 는 한글/공백 경로에서 조용히 실패하거나 빈 파일 생성.
    # 해결책: builder는 항상 ASCII 경로인 스크립트 디렉토리에 저장하고,
    #         stdout 으로 임시경로를 알려준다 → GUI가 shutil.move 로 이동.
    print("[8/8] 저장")
    import shutil as _shutil
    _HERE_B = os.path.dirname(os.path.abspath(__file__))
    _tmp_fcstd = os.path.join(_HERE_B, "_mep_tmp_out.FCStd")
    _tmp_ifc   = os.path.join(_HERE_B, "_mep_tmp_out.ifc")
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
        # GUI 가 이 마커를 읽어 파일을 이동시킴
        print(f"FCSTD_TMP:{_tmp_fcstd}")
        print(f"FCSTD_DST:{os.path.abspath(fcstd)}")
    else:
        print(f"[ERROR] FCStd 저장 실패 — 파일 없음: {_tmp_fcstd}")

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
    try:
        if _exporter is None:
            raise ImportError("IFC exporter 모듈을 찾지 못함")
        _exporter.export([building], _tmp_ifc)
        if os.path.exists(_tmp_ifc):
            print(f"IFC_TMP:{_tmp_ifc}")
            print(f"IFC_DST:{os.path.abspath(ifc)}")
    except Exception as e:
        print("[warn] IFC export 실패:", e)

    n_err = sum(1 for o in doc.Objects if getattr(o, "Shape", None)
                and not o.Shape.isValid())
    print(f"빌드 완료: floors={len(floor_containers)} walls={len(walls)}"
          f" columns={len(cols)} slabs={len(slabs)} spaces={len(spaces)}"
          f" mep={len(mep_objs)}"
          + (f" openings_void={n_voids}" if n_voids else "")
          + (f" clashes={len(clashes)}" if clashes else ""))
    print(f"  -> {fcstd}")
    print(f"  -> {ifc}")
    if n_err:
        print(f"  [warn] 형상 검증 실패 객체 {n_err}개 (자가수정 루프 대상)")


# freecadcmd 는 __name__ 을 모듈명("freecad_builder")으로 설정하므로 둘 다 허용.
if __name__ in ("__main__", "freecad_builder"):
    main()
