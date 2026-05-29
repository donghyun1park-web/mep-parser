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


def make_wire(points, closed):
    pts = []
    for p in points:
        v = vec(p)
        if not pts or (pts[-1] - v).Length > 1.0:
            pts.append(v)
    if closed and len(pts) > 1 and (pts[-1] - pts[0]).Length <= 1.0:
        pts.pop()
    if len(pts) < 2:
        return None
    try:
        w = Draft.makeWire(pts, closed=closed, face=False)
        return w
    except Exception:
        return None


def build_walls(doc, walls, params):
    d = params.get("wall", {})
    objs = []
    src_els = []  # 각 obj 에 대응하는 원본 element (층 그룹핑용)
    idx_map = {}  # element index → wall obj (opening void 연결용)
    
    # 에러 요소 저장용 그룹 (빌더 크래시 방지 및 시각화)
    error_group = None

    for i, el in enumerate(walls):
        # baseline: 검출된 중심선 우선, 없으면 원본 points
        baseline = el.get("centerline") or el.get("points")
        if el["kind"] != "polyline" or not baseline or len(baseline) < 2:
            continue
        ov = el.get("overrides", {})  # CSV 요소별 오버라이드 우선
        # width: 검출 두께 > CSV 오버라이드 > params 기본
        width = float(el.get("width_detected")
                      or ov.get("width", d.get("width", 200.0)))
        height = float(ov.get("height", d.get("height", 2800.0)))
        
        # 라운드트립용 핸들(ID)이 파싱단계에서 부여된 경우 가져오기
        dxf_id = el.get("handle") or f"WALL_{i}"

        try:
            base = make_wire(baseline, el.get("closed", False))
            if not base:
                raise ValueError("make_wire returned None")
            base.Label = f"WallAxis_{i}"
            wall = Arch.makeWall(base, width=width, height=height)
            if not wall:
                raise ValueError("Arch.makeWall returned None")
                
            wall.Label = f"Wall_{i}"
            wall.Placement.Base.z = float(el.get("z_base", 0.0))  # [4b] 층 Z 오프셋
            
            # [라운드트립 기반] DXF Handle 주입
            wall.addProperty("App::PropertyString", "DxfId", "Metadata", "Original DXF Handle")
            wall.DxfId = dxf_id

            objs.append(wall)
            src_els.append(el)
            idx_map[i] = wall
        except Exception as e:
            print(f"[warn] Wall_{i} 생성 실패: {e}")
            # 에러 발생 시 단순 선(빨간색)으로 시각적 롤백 (Graceful Degradation)
            try:
                if not error_group:
                    error_group = doc.addObject("App::DocumentObjectGroup", "Error_Elements")
                pts = [vec(p, float(el.get("z_base", 0.0))) for p in baseline]
                if len(pts) >= 2:
                    err_line = Draft.makeWire(pts, closed=el.get("closed", False), face=False)
                    err_line.Label = f"Error_Wall_{i}"
                    err_line.ViewObject.LineColor = (1.0, 0.0, 0.0, 0.0) # 빨간색
                    err_line.ViewObject.LineWidth = 3.0
                    error_group.addObject(err_line)
            except Exception as ex:
                pass
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
    """구조·MEP 솔리드 페어별 shape.common() 볼륨 > vol_tol → clash 목록 반환.
    O(S×M): S=구조 객체 수, M=MEP 객체 수. 실무 도면에서도 수백개 수준이므로 충분."""
    clashes = []
    for so in struct_objs:
        s_shape = getattr(so, "Shape", None)
        if s_shape is None or not s_shape.isValid():
            continue
        for mo in mep_objs:
            m_shape = getattr(mo, "Shape", None)
            if m_shape is None or not m_shape.isValid():
                continue
            try:
                common = s_shape.common(m_shape)
                if common.Volume > vol_tol:
                    clashes.append({"struct": so.Label, "mep": mo.Label,
                                    "volume_mm3": round(common.Volume, 1)})
            except Exception:
                pass  # 형상 오류는 무시(이미 warn 출력됨)
    return clashes


def main():
    # freecadcmd 는 argv 의 .json/파일명을 '열 문서'로 오인하므로 환경변수를 우선 사용.
    #   권장:  MEP_GEOMETRY=geometry.json MEP_OUT=out_model freecadcmd freecad_builder.py
    #   호환:  freecadcmd freecad_builder.py geometry.json out_model
    geom_path = os.environ.get("MEP_GEOMETRY") or (sys.argv[1] if len(sys.argv) > 1 else None)
    out_base = os.environ.get("MEP_OUT") or (sys.argv[2] if len(sys.argv) > 2 else "out_model")
    if not geom_path:
        print("usage: MEP_GEOMETRY=geometry.json MEP_OUT=out freecadcmd freecad_builder.py")
        sys.exit(1)

    with open(geom_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    params = data.get("params", {})
    el = data["elements"]

    doc = App.newDocument("BIM")

    walls, wall_idx_map, wall_src = build_walls(doc, el.get("wall", []), params)
    cols,  col_src              = build_columns(doc, el.get("column", []), params)
    slabs, slab_src             = build_slabs(doc, el.get("slab", []), params)
    spaces, space_src           = build_spaces(doc, el.get("zone", []), params)  # [4c]
    mep_objs = build_mep(doc, el)                                                # [5a]

    # [4b] 층별 Arch.makeFloor 생성. floors 없으면 단층 폴백.
    # _at_floor: src_els는 build_* 가 실제 빌드한 원본 element 리스트(skip 제외)
    # → zip(objs, src_els) 가 항상 1:1 대응 보장
    _FLOOR_TOL = 100.0
    floors_info = data.get("floors") or [{"z": 0.0, "label": "Level_1"}]

    def _at_floor(obj_list, src_list, fz):
        """src element 의 z_base ≈ fz 인 obj 필터링."""
        return [obj for obj, el_r in zip(obj_list, src_list)
                if abs(float(el_r.get("z_base", 0.0)) - fz) < _FLOOR_TOL]

    floor_containers = []
    for fi, finfo in enumerate(floors_info):
        fz = float(finfo.get("z", 0.0))
        flabel = finfo.get("label", f"Level_{fi+1}")
        fw  = _at_floor(walls,  wall_src,  fz)
        fc  = _at_floor(cols,   col_src,   fz)
        fs  = _at_floor(slabs,  slab_src,  fz)
        fsp = _at_floor(spaces, space_src, fz)
        fl = Arch.makeFloor(fw + fc + fs + fsp)
        fl.Label = flabel
        fl.Placement.Base.z = fz
        floor_containers.append(fl)

    building = Arch.makeBuilding(floor_containers)
    building.Label = "Building"

    # ★ recompute 1회: Arch 객체 shape 확정 후 opening void 적용.
    #   void 이후 recompute 금지(Arch 파라메트릭 재계산이 shape 덮어씀).
    doc.recompute()

    # [Phase 4a] opening void (문/창 위치에 원통 절단)
    n_voids = apply_opening_voids(wall_idx_map, el.get("opening", []), params)

    # [Phase 5b] 구조 vs MEP 간섭(clash) 검사
    struct_objs = walls + cols + slabs
    clashes = check_clashes(struct_objs, mep_objs)
    if clashes:
        print(f"  [CLASH] 간섭 {len(clashes)}건:")
        for c in clashes:
            print(f"    {c['struct']} ↔ {c['mep']}  {c['volume_mm3']:.0f} mm³")
    else:
        print("  [CLASH] 간섭 없음")

    fcstd = f"{out_base}.FCStd"
    ifc = f"{out_base}.ifc"
    doc.saveAs(os.path.abspath(fcstd))

    # IFC 내보내기. FreeCAD 버전별 익스포터 경로가 달라 폴백 체인으로 시도.
    #   1.1+: importers.exportIFC  /  구버전: exportIFC, importIFC
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
        _exporter.export([building], os.path.abspath(ifc))
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
