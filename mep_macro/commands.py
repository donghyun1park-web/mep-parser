import os
import sys
import shlex
import FreeCAD as App
import FreeCADGui as Gui
import Arch
import Part
import Draft

# Ensure the parent directory is in sys.path
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from .freecad_utils import (
    find_objects,
    hide_base_solids,
    safe_recompute,
    build_polygonized_layer,
    execute_fallback_build
)

def process_cli_command(ui_instance, cmd_text):
    """
    Routes command from the CLI input panel.
    - ui_instance: instance of LocalCLIMacro dock widget.
    - cmd_text: raw user input command.
    """
    log = ui_instance.log
    
    # Handle pending build confirm/cancel state machine
    if ui_instance.pending_build:
        cmd_lower = cmd_text.strip().lower()
        if cmd_lower in ("예", "y", "yes"):
            log("✅ 열린 선을 닫고 3D 빌드를 시작합니다...")
            execute_fallback_build(App.ActiveDocument, ui_instance.pending_build, auto_close=True, logger=log)
        elif cmd_lower in ("아니오", "n", "no"):
            log("❌ 닫기 작업을 취소하고, 기존 닫힌 선들만 빌드합니다...")
            execute_fallback_build(App.ActiveDocument, ui_instance.pending_build, auto_close=False, logger=log)
        else:
            log("입력이 올바르지 않습니다. '예' 또는 '아니오'로 대답해주세요.")
            return
        ui_instance.pending_build = None
        return

    parts = cmd_text.split()
    if not parts:
        return
    cmd = parts[0].lower()

    if cmd == "도움말":
        log("사용 가능한 명령어:\n"
            " - 빌드 [레이어명] [객체타입(벽/기둥)] [높이]\n"
            " - 합치기 [번호1] [번호2]\n"
            " - 자르기(또는 맞추기) [잘릴벽] [기준기둥]\n"
            " - 높이 [수치] [번호 또는 전체]\n"
            " - 두께 [수치] [번호 또는 전체]\n"
            " - 정렬 [왼쪽/오른쪽/가운데] [번호 또는 전체]\n"
            " - 투명도 [0~100 수치] [번호 또는 전체]\n"
            " - 지우기 [번호 또는 전체]\n"
            " - 편집모드 [번호 또는 전체] (마우스 드래그 연장 가능하게 전환)\n"
            " - 연장 [수치] [이름]\n"
            " - 색상 [빨강/파랑/초록/하늘/회색/주황/노랑 등] [이름 또는 전체]\n"
            " - 슬라브(또는 바닥) [두께(선택)]\n"
            " - 층추가 [\"DXF경로\"] [높이] [층이름]")

    elif cmd in ("합치기", "merge"):
        if len(parts) < 3:
            log("사용법: 합치기 [벽번호1] [벽번호2]")
            return
        w1_str, w2_str = parts[1], parts[2]
        w1_list = find_objects(w1_str)
        w2_list = find_objects(w2_str)
        if not w1_list:
            log(f"객체 '{w1_str}' 찾을 수 없음.")
            return
        if not w2_list:
            log(f"객체 '{w2_str}' 찾을 수 없음.")
            return
        w1, w2 = w1_list[0], w2_list[0]
        Arch.addComponents(w2, w1)
        w1.Label = f"Merged_{w1_str}_{w2_str}"
        if hasattr(w2, "ViewObject") and w2.ViewObject:
            w2.ViewObject.Visibility = False
        log(f"✅ 성공: '{w1_str}'과 '{w2_str}' 병합됨.")

    elif cmd in ("자르기", "cut", "맞추기"):
        if len(parts) < 3:
            log("사용법: 자르기 [잘릴벽] [기준기둥/벽]")
            return
        w1_str, w2_str = parts[1], parts[2]
        w1_list = find_objects(w1_str)
        w2_list = find_objects(w2_str)
        if not w1_list:
            log(f"객체 '{w1_str}' 찾을 수 없음.")
            return
        if not w2_list:
            log(f"객체 '{w2_str}' 찾을 수 없음.")
            return
        w1, w2 = w1_list[0], w2_list[0]
        Arch.makeCut(w1, w2)
        if hasattr(w2, "ViewObject") and w2.ViewObject:
            w2.ViewObject.Visibility = True
        log(f"✅ 성공: '{w1_str}'이 '{w2_str}'에 맞춰 잘렸습니다. (독립된 객체 유지)")

    elif cmd in ("높이", "height"):
        if len(parts) < 3:
            log("사용법: 높이 [수치] [번호 또는 전체]")
            return
        try:
            val = float(parts[1])
        except ValueError:
            log("높이 값은 숫자여야 합니다.")
            return
        target = parts[2]
        objs = find_objects(target)
        count = 0
        for obj in objs:
            if hasattr(obj, "Height"):
                obj.Height = val
                count += 1
        log(f"✅ 성공: {count}개 객체의 높이를 {val}로 변경.")

    elif cmd in ("두께", "thickness", "width"):
        if len(parts) < 3:
            log("사용법: 두께 [수치] [번호 또는 전체]")
            return
        try:
            val = float(parts[1])
        except ValueError:
            log("두께 값은 숫자여야 합니다.")
            return
        target = parts[2]
        objs = find_objects(target)
        count = 0
        for obj in objs:
            if hasattr(obj, "Width"):
                obj.Width = val
                count += 1
        log(f"✅ 성공: {count}개 객체의 두께를 {val}로 변경.")

    elif cmd in ("정렬", "align"):
        if len(parts) < 3:
            log("사용법: 정렬 [왼쪽/오른쪽/가운데] [번호 또는 전체]")
            return
        align_str = parts[1].lower()
        target = parts[2]
        
        align_val = "Center"
        if align_str in ("왼쪽", "left"):
            align_val = "Left"
        elif align_str in ("오른쪽", "right"):
            align_val = "Right"
        elif align_str in ("가운데", "center"):
            align_val = "Center"
        else:
            log("정렬 방식은 '왼쪽', '오른쪽', '가운데' 중 하나여야 합니다.")
            return
            
        objs = find_objects(target)
        count = 0
        for obj in objs:
            if hasattr(obj, "Align"):
                obj.Align = align_val
                count += 1
        log(f"✅ 성공: {count}개 객체를 {align_str}({align_val}) 정렬로 변경.")

    elif cmd in ("투명도", "transparency", "alpha"):
        if len(parts) < 3:
            log("사용법: 투명도 [0~100 수치] [번호 또는 전체]")
            return
        try:
            val = int(parts[1])
        except ValueError:
            log("투명도 수치는 숫자여야 합니다.")
            return
        val = max(0, min(100, val))
        target = parts[2]
        objs = find_objects(target)
        count = 0
        for obj in objs:
            if hasattr(obj, "ViewObject") and obj.ViewObject:
                obj.ViewObject.Transparency = val
                count += 1
        log(f"✅ 성공: {count}개 객체의 투명도를 {val}로 변경.")

    elif cmd in ("지우기", "delete"):
        if len(parts) < 2:
            log("사용법: 지우기 [번호 또는 전체]")
            return
        target = parts[1]
        objs = find_objects(target)
        doc = App.ActiveDocument
        for obj in objs:
            doc.removeObject(obj.Name)
        log(f"✅ 성공: {len(objs)}개 객체 삭제됨.")

    elif cmd in ("편집모드", "editable"):
        if len(parts) < 2:
            log("사용법: 편집모드 [벽번호 또는 전체]")
            return
        target = parts[1]
        objs = find_objects(target)
        count = 0
        doc = App.ActiveDocument
        for obj in objs:
            base = getattr(obj, "Base", None)
            if base and base.TypeId == "Part::Feature":
                shape = base.Shape
                if hasattr(shape, "Vertexes"):
                    pts = []
                    # Find bottom horizontal edges (Z coordinate close to min_z) to avoid vertical lines
                    bottom_edges = []
                    if shape.Edges:
                        min_z = min(v.Point.z for v in shape.Vertexes)
                        for edge in shape.Edges:
                            v1 = edge.valueAt(edge.FirstParameter)
                            v2 = edge.valueAt(edge.LastParameter)
                            if abs(v1.z - min_z) < 10.0 and abs(v2.z - min_z) < 10.0:
                                bottom_edges.append(edge)
                        
                        if bottom_edges:
                            # Use the longest horizontal edge at the bottom as the wall centerline axis
                            longest_edge = max(bottom_edges, key=lambda e: e.Length)
                            pts = [longest_edge.valueAt(longest_edge.FirstParameter), longest_edge.valueAt(longest_edge.LastParameter)]
                            
                    if not pts and shape.Edges:
                        edge = shape.Edges[0]
                        pts = [edge.valueAt(edge.FirstParameter), edge.valueAt(edge.LastParameter)]
                    elif not pts:
                        pts = [v.Point for v in shape.Vertexes]
                        
                    if len(pts) >= 2:
                        draft_wire = Draft.make_wire(pts, closed=False, face=False)
                        draft_wire.Label = base.Label + "_Draft"
                        obj.Base = draft_wire
                        doc.removeObject(base.Name)
                        count += 1
        log(f"✅ 성공: {count}개 객체의 뼈대가 마우스 편집 가능하게 변환되었습니다. 벽을 더블클릭해보세요!")

    elif cmd in ("연장", "extend"):
        if len(parts) < 3:
            log("사용법: 연장 [수치] [벽이름]")
            return
        try:
            val = float(parts[1])
        except ValueError:
            log("거리는 숫자로 입력해야 합니다.")
            return
        target = parts[2]
        objs = find_objects(target)
        count = 0
        for obj in objs:
            base = getattr(obj, "Base", None)
            if not base:
                continue
            if hasattr(base, "Points") and len(base.Points) >= 2:
                pts = base.Points
                p1, p2 = pts[0], pts[-1]
                vec = p2 - p1
                if vec.Length > 0.001:
                    vec.normalize()
                    pts[-1] = p2 + vec * val
                    base.Points = pts
                    count += 1
            elif hasattr(base, "Shape") and hasattr(base.Shape, "Vertexes") and len(base.Shape.Vertexes) >= 2:
                pts = [v.Point for v in base.Shape.Vertexes]
                p1, p2 = pts[0], pts[-1]
                vec = p2 - p1
                if vec.Length > 0.001:
                    vec.normalize()
                    pts[-1] = p2 + vec * val
                    new_wire = Part.makePolygon(pts)
                    base.Shape = new_wire
                    count += 1
        log(f"✅ 성공: {count}개 객체의 끝을 {val}만큼 연장했습니다.")

    elif cmd in ("색상", "color"):
        if len(parts) < 3:
            log("사용법: 색상 [빨강/파랑/초록/하늘/회색/주황/노랑 등] [이름 또는 전체]")
            return
        color_str = parts[1].lower()
        target = parts[2]
        
        colors = {
            "빨강": (1.0, 0.0, 0.0), "red": (1.0, 0.0, 0.0),
            "파랑": (0.0, 0.0, 1.0), "blue": (0.0, 0.0, 1.0),
            "초록": (0.0, 1.0, 0.0), "green": (0.0, 1.0, 0.0),
            "하늘": (0.5, 0.8, 1.0), "하늘색": (0.5, 0.8, 1.0), "skyblue": (0.5, 0.8, 1.0),
            "노랑": (1.0, 1.0, 0.0), "yellow": (1.0, 1.0, 0.0),
            "회색": (0.6, 0.6, 0.6), "gray": (0.6, 0.6, 0.6),
            "주황": (1.0, 0.5, 0.0), "orange": (1.0, 0.5, 0.0)
        }
        
        rgb = colors.get(color_str, (0.8, 0.8, 0.8))
        objs = find_objects(target)
        count = 0
        for obj in objs:
            if hasattr(obj, "ViewObject") and obj.ViewObject:
                obj.ViewObject.ShapeColor = rgb
                count += 1
        log(f"✅ 성공: {count}개 객체의 색상을 {color_str}로 변경했습니다.")

    elif cmd in ("슬라브", "바닥", "slab"):
        thickness = 300.0
        if len(parts) >= 2:
            try:
                thickness = float(parts[1])
            except ValueError:
                pass
        
        doc = App.ActiveDocument
        
        try:
            import shapely.geometry as sg
            from shapely.ops import unary_union
        except ImportError:
            log("❌ shapely가 필요합니다. 'pip install shapely'를 실행해주세요.")
            return
            
        polys = []
        z_list = []
        for obj in doc.Objects:
            if "Slab" in obj.Name or "Slab" in obj.Label:
                continue
            if obj.Name.startswith("Wall") or obj.Name.startswith("Col") or "Wall" in obj.Label or "Column" in obj.Label:
                if hasattr(obj, "Shape") and obj.Shape:
                    pts = []
                    for v in obj.Shape.Vertexes:
                        pts.append((v.Point.x, v.Point.y))
                        z_list.append(v.Point.z)
                    if len(pts) >= 3:
                        mp = sg.MultiPoint(pts)
                        hull = mp.convex_hull
                        if hull.geom_type == 'Polygon':
                            polys.append(hull)
                            
        if not polys:
            log("기준이 될 벽체나 기둥이 없습니다.")
            return
            
        z_min = min(z_list) if z_list else 0.0
        
        log("⏳ 건물 외벽선을 분석하여 슬라브 경계를 계산 중...")
        
        try:
            # Buffer by 600mm to close door/window gaps (up to 1200mm)
            buffered = [p.buffer(600.0, cap_style=2, join_style=2) for p in polys]
            merged = unary_union(buffered)
            
            # Select the largest polygon if there are multiple detached buildings
            if merged.geom_type == 'Polygon':
                outer_poly = merged
            elif merged.geom_type == 'MultiPolygon':
                outer_poly = max(merged.geoms, key=lambda p: p.area)
            else:
                outer_poly = None
                
            if not outer_poly:
                log("❌ 외벽 윤곽선 추출 실패.")
                return
                
            # Create a filled polygon from the exterior boundary of the merged walls (closes inner rooms)
            filled_poly = sg.Polygon(outer_poly.exterior)
            # Erode back by 600mm to get the exact outer wall face line
            slab_poly = filled_poly.buffer(-600.0, cap_style=2, join_style=2)
            
            # If it split, pick the largest
            if slab_poly.geom_type == 'MultiPolygon':
                slab_poly = max(slab_poly.geoms, key=lambda p: p.area)
                
            # Add a small slab margin (100mm) outside the walls
            slab_poly = slab_poly.buffer(100.0, cap_style=2, join_style=2)
            
            coords = list(slab_poly.exterior.coords)
            p_vecs = [App.Vector(x, y, z_min) for x, y in coords]
            
            # Create Draft Wire
            rect = Draft.make_wire(p_vecs, closed=True, face=False)
            rect.Label = "Slab_Base"
            
            # Create Arch Structure
            slab = Arch.makeStructure(rect, height=thickness)
            slab.Label = "Main_Slab"
            slab.IfcType = "Slab"
            slab.Normal = App.Vector(0, 0, 1) # Extrude upwards
            slab.Placement.Base.z = z_min - thickness
            
            # Hide the base wire
            if hasattr(rect, "ViewObject") and rect.ViewObject:
                rect.ViewObject.Visibility = False
                
            log(f"✅ 성공: 건물 외벽선을 따라 설계된 두께 {thickness}mm 슬라브가 생성되었습니다. (레벨: {z_min}mm)")
        except Exception as e:
            log(f"❌ 슬라브 경계 생성 중 오류 발생: {e}")
            import traceback
            log(traceback.format_exc())

    elif cmd in ("층추가", "add_floor"):
        try:
            args = shlex.split(cmd_text)
        except Exception:
            args = cmd_text.split()
            
        if len(args) < 4:
            log("사용법: 층추가 [\"DXF경로\"] [높이] [층이름]\n예: 층추가 \"C:\\도면\\지하3층.dxf\" 3500 B3")
            return
            
        dxf_path = args[1]
        try:
            z_offset = float(args[2])
        except ValueError:
            log("높이 값은 숫자여야 합니다.")
            return
        prefix = args[3]
        
        if not os.path.exists(dxf_path):
            log(f"❌ 파일이 존재하지 않습니다: {dxf_path}")
            return
            
        log(f"⏳ [{prefix}] 층 추가를 시작합니다. 경로: {dxf_path}, 높이: {z_offset}")
        
        # In-Memory DXF Parsing and Direct FreeCAD BIM building via Extractors
        try:
            import extractors
            CONFIG_PATH = os.path.join(HERE, "layers_config.yaml")
            wall_extractor = extractors.WallExtractor(CONFIG_PATH)
            
            log("⏳ 1. 중심선 추출 엔진 가동 중 (Phase 1 & 2 적용)...")
            wall_data = wall_extractor.extract_from_dxf(dxf_path)
            
            log(f"✅ 2. 파싱 완료! {len(wall_data)}개의 파라메트릭 벽체 3D 모델 생성 시작...")
            doc = App.ActiveDocument
            
            count = 0
            for idx, w in enumerate(wall_data):
                coords = list(w['centerline'].coords)
                if len(coords) < 2:
                    continue

                points = [App.Vector(x, y, z_offset) for x, y in coords]
                wire = Draft.make_wire(points, closed=False, face=False)
                wire_label = f"Center_{w['label']}_{prefix}_{idx}"
                wire.Label = wire_label

                wall = Arch.makeWall(wire, length=0, width=w['thickness'], 
                                     height=w['height'], align="Center")
                wall.Label = f"{prefix}_{w['label']}_{idx}"
                count += 1
                
            safe_recompute(doc)
            Gui.updateGui()
            log(f"✅ {prefix} 층 {count}개의 스마트 벽체 추가가 성공적으로 완료되었습니다!")
        except Exception as e:
            log(f"❌ 층추가 실패: {e}")
            import traceback
            log(traceback.format_exc())

    elif cmd in ("빌드", "build"):
        if len(parts) < 4:
            log("사용법: 빌드 [레이어명] [벽/기둥] [높이]\n예: 빌드 A-Wall 벽 3000")
            return
        layer_name = parts[1]
        obj_type = parts[2]
        try:
            height = float(parts[3])
        except ValueError:
            log("높이는 숫자여야 합니다.")
            return
            
        thick_cap = None
        if len(parts) >= 5:
            try:
                thick_cap = float(parts[4])
            except ValueError:
                pass
                
        build_polygonized_layer(App.ActiveDocument, layer_name, obj_type, height, thick_cap, logger=log)

    else:
        log(f"알 수 없는 명령어: {cmd}")
