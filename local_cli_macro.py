import FreeCAD as App
import FreeCADGui as Gui
import Arch
import Part

# PySide 호환성 처리 (FreeCAD 0.20+ / 1.0+)
try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    try:
        from PySide2 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide import QtCore, QtGui
        QtWidgets = QtGui

class LocalCLIMacro(QtWidgets.QDockWidget):
    def __init__(self):
        super().__init__("Antigravity Command Line")
        self.setAllowedAreas(QtCore.Qt.BottomDockWidgetArea | QtCore.Qt.RightDockWidgetArea)
        
        self.widget = QtWidgets.QWidget()
        self.layout = QtWidgets.QVBoxLayout()
        self.widget.setLayout(self.layout)
        
        self.output_log = QtWidgets.QTextEdit()
        self.output_log.setReadOnly(True)
        # 어두운 테마나 보기 편하게 스타일 적용
        self.output_log.setStyleSheet("background-color: #1e1e1e; color: #00ff00; font-family: Consolas, monospace;")
        self.layout.addWidget(self.output_log)
        
        self.input_line = QtWidgets.QLineEdit()
        self.input_line.setPlaceholderText("명령어를 입력하세요 (예: 합치기 623 465, 높이 3000 전체, 두께 200 623)")
        self.input_line.setStyleSheet("font-size: 14px; padding: 5px;")
        self.input_line.returnPressed.connect(self.on_enter)
        self.layout.addWidget(self.input_line)
        
        self.setWidget(self.widget)
        self.pending_build = None
        self.log("⚡ CLI 준비 완료. '도움말'을 입력해보세요.")

    def log(self, text):
        self.output_log.append(text)
        self.output_log.verticalScrollBar().setValue(self.output_log.verticalScrollBar().maximum())

    def find_objects(self, name_str):
        doc = App.ActiveDocument
        if not doc:
            return []
        if name_str.lower() in ("전체", "all"):
            return [obj for obj in doc.Objects if any(k in obj.Name or k in obj.Label for k in ["Wall", "Column", "Slab", "Opening", "Merged"])]
            
        found = []
        for obj in doc.Objects:
            if name_str in obj.Name or name_str in obj.Label:
                found.append(obj)
        return found

    def on_enter(self):
        cmd_text = self.input_line.text().strip()
        if not cmd_text:
            return
        
        self.input_line.clear()
        self.log(f"\n> {cmd_text}")
        
        try:
            self.process_command(cmd_text)
            if App.ActiveDocument:
                App.ActiveDocument.recompute()
                Gui.updateGui()
        except Exception as e:
            self.log(f"[에러] {str(e)}")

    def process_command(self, cmd_text):
        if self.pending_build:
            if cmd_text.lower() in ("예", "y", "yes"):
                self.log("✅ 열린 선을 닫고 3D 빌드를 시작합니다...")
                self._execute_build(self.pending_build, auto_close=True)
            elif cmd_text.lower() in ("아니오", "n", "no"):
                self.log("❌ 닫기 작업을 취소하고, 기존 닫힌 선들만 빌드합니다...")
                self._execute_build(self.pending_build, auto_close=False)
            else:
                self.log("입력이 올바르지 않습니다. '예' 또는 '아니오'로 대답해주세요.")
                return
            self.pending_build = None
            return
            
        parts = cmd_text.split()
        if not parts: return
        cmd = parts[0].lower()
        
        if cmd == "도움말":
            self.log("사용 가능한 명령어:\n - 빌드 [레이어명] [객체타입(벽/기둥)] [높이]\n - 합치기 [번호1] [번호2]\n - 자르기(또는 맞추기) [잘릴벽] [기준기둥]\n - 높이 [수치] [번호 또는 전체]\n - 두께 [수치] [번호 또는 전체]\n - 정렬 [왼쪽/오른쪽/가운데] [번호 또는 전체]\n - 투명도 [0~100 수치] [번호 또는 전체]\n - 지우기 [번호 또는 전체]\n - 편집모드 [번호 또는 전체] (마우스 드래그 연장 가능하게 전환)")
            
        elif cmd in ("합치기", "merge"):
            if len(parts) < 3:
                self.log("사용법: 합치기 [벽번호1] [벽번호2]")
                return
            w1_str, w2_str = parts[1], parts[2]
            w1_list = self.find_objects(w1_str)
            w2_list = self.find_objects(w2_str)
            if not w1_list:
                self.log(f"객체 '{w1_str}' 찾을 수 없음.")
                return
            if not w2_list:
                self.log(f"객체 '{w2_str}' 찾을 수 없음.")
                return
            w1, w2 = w1_list[0], w2_list[0]
            Arch.addComponents(w2, w1)
            w1.Label = f"Merged_{w1_str}_{w2_str}"
            if hasattr(w2, "ViewObject") and w2.ViewObject:
                w2.ViewObject.Visibility = False
            self.log(f"✅ 성공: '{w1_str}'과 '{w2_str}' 병합됨.")
            
        elif cmd in ("자르기", "cut", "맞추기"):
            if len(parts) < 3:
                self.log("사용법: 자르기 [잘릴벽] [기준기둥/벽]")
                return
            w1_str, w2_str = parts[1], parts[2]
            w1_list = self.find_objects(w1_str)
            w2_list = self.find_objects(w2_str)
            if not w1_list:
                self.log(f"객체 '{w1_str}' 찾을 수 없음.")
                return
            if not w2_list:
                self.log(f"객체 '{w2_str}' 찾을 수 없음.")
                return
            w1, w2 = w1_list[0], w2_list[0]
            
            import Arch
            # w1에서 w2의 형태만큼 빼기(Subtractions)
            Arch.makeCut(w1, w2)
            
            # 잘라내는 기준 객체(w2)가 투명해지거나 숨겨질 수 있으므로 다시 보이게 함
            if hasattr(w2, "ViewObject") and w2.ViewObject:
                w2.ViewObject.Visibility = True
                
            self.log(f"✅ 성공: '{w1_str}'이 '{w2_str}'에 맞춰 잘렸습니다. (독립된 객체 유지)")
            
        elif cmd in ("높이", "height"):
            if len(parts) < 3:
                self.log("사용법: 높이 [수치] [번호 또는 전체]")
                return
            val = float(parts[1])
            target = parts[2]
            objs = self.find_objects(target)
            count = 0
            for obj in objs:
                if hasattr(obj, "Height"):
                    obj.Height = val
                    count += 1
            self.log(f"✅ 성공: {count}개 객체의 높이를 {val}로 변경.")
            
        elif cmd in ("두께", "thickness", "width"):
            if len(parts) < 3:
                self.log("사용법: 두께 [수치] [번호 또는 전체]")
                return
            val = float(parts[1])
            target = parts[2]
            objs = self.find_objects(target)
            count = 0
            for obj in objs:
                if hasattr(obj, "Width"):
                    obj.Width = val
                    count += 1
            self.log(f"✅ 성공: {count}개 객체의 두께를 {val}로 변경.")
            
        elif cmd in ("정렬", "align"):
            if len(parts) < 3:
                self.log("사용법: 정렬 [왼쪽/오른쪽/가운데] [번호 또는 전체]")
                return
            align_str = parts[1].lower()
            target = parts[2]
            
            # FreeCAD Arch Wall Align properties: "Center", "Left", "Right"
            align_val = "Center"
            if align_str in ("왼쪽", "left"): align_val = "Left"
            elif align_str in ("오른쪽", "right"): align_val = "Right"
            elif align_str in ("가운데", "center"): align_val = "Center"
            else:
                self.log(f"정렬 방식은 '왼쪽', '오른쪽', '가운데' 중 하나여야 합니다.")
                return
                
            objs = self.find_objects(target)
            count = 0
            for obj in objs:
                if hasattr(obj, "Align"):
                    obj.Align = align_val
                    count += 1
            self.log(f"✅ 성공: {count}개 객체를 {align_str}({align_val}) 정렬로 변경.")
            
        elif cmd in ("투명도", "transparency", "alpha"):
            if len(parts) < 3:
                self.log("사용법: 투명도 [0~100 수치] [번호 또는 전체]")
                return
            try:
                val = int(parts[1])
            except ValueError:
                self.log("투명도 수치는 숫자여야 합니다.")
                return
            if val < 0: val = 0
            if val > 100: val = 100
            target = parts[2]
            objs = self.find_objects(target)
            count = 0
            for obj in objs:
                if hasattr(obj, "ViewObject") and obj.ViewObject:
                    obj.ViewObject.Transparency = val
                    count += 1
            self.log(f"✅ 성공: {count}개 객체의 투명도를 {val}로 변경.")
            
        elif cmd in ("지우기", "delete"):
            if len(parts) < 2:
                self.log("사용법: 지우기 [번호 또는 전체]")
                return
            target = parts[1]
            objs = self.find_objects(target)
            doc = App.ActiveDocument
            for obj in objs:
                doc.removeObject(obj.Name)
            self.log(f"✅ 성공: {len(objs)}개 객체 삭제됨.")
            
        elif cmd in ("편집모드", "editable"):
            if len(parts) < 2:
                self.log("사용법: 편집모드 [벽번호 또는 전체]")
                return
            target = parts[1]
            objs = self.find_objects(target)
            count = 0
            doc = App.ActiveDocument
            for obj in objs:
                base = getattr(obj, "Base", None)
                if base and base.TypeId == "Part::Feature":
                    shape = base.Shape
                    if hasattr(shape, "Vertexes"):
                        import Draft
                        # Sort vertices by curve parameter if it's an edge, but simple vertex order usually works for straight lines
                        pts = []
                        if shape.Edges:
                            from FreeCAD import Vector
                            edge = shape.Edges[0]
                            pts = [edge.valueAt(edge.FirstParameter), edge.valueAt(edge.LastParameter)]
                        else:
                            pts = [v.Point for v in shape.Vertexes]
                            
                        if len(pts) >= 2:
                            draft_wire = Draft.make_wire(pts, closed=False, face=False)
                            draft_wire.Label = base.Label + "_Draft"
                            obj.Base = draft_wire
                            doc.removeObject(base.Name)
                            count += 1
            self.log(f"✅ 성공: {count}개 객체의 뼈대가 마우스 편집 가능하게 변환되었습니다. 벽을 더블클릭해보세요!")
            
        elif cmd in ("연장", "extend"):
            if len(parts) < 3:
                self.log("사용법: 연장 [수치] [벽이름]")
                return
            try:
                val = float(parts[1])
            except ValueError:
                self.log("거리는 숫자로 입력해야 합니다.")
                return
            target = parts[2]
            objs = self.find_objects(target)
            count = 0
            for obj in objs:
                base = getattr(obj, "Base", None)
                if not base: continue
                if hasattr(base, "Points") and len(base.Points) >= 2:
                    pts = base.Points
                    p1 = pts[0]
                    p2 = pts[-1]
                    vec = p2 - p1
                    if vec.Length > 0.001:
                        vec.normalize()
                        # 끝점을 방향대로 val만큼 연장
                        pts[-1] = p2 + vec * val
                        base.Points = pts
                        count += 1
                elif hasattr(base, "Shape") and hasattr(base.Shape, "Vertexes") and len(base.Shape.Vertexes) >= 2:
                    # 편집모드가 안 된 벽체의 경우
                    import Part
                    pts = [v.Point for v in base.Shape.Vertexes]
                    p1, p2 = pts[0], pts[-1]
                    vec = p2 - p1
                    if vec.Length > 0.001:
                        vec.normalize()
                        pts[-1] = p2 + vec * val
                        new_wire = Part.makePolygon(pts)
                        base.Shape = new_wire
                        count += 1
            self.log(f"✅ 성공: {count}개 객체의 끝을 {val}만큼 연장했습니다.")
            
        elif cmd in ("색상", "color"):
            if len(parts) < 3:
                self.log("사용법: 색상 [빨강/파랑/초록/하늘/회색/원래대로 등] [벽이름 또는 전체]")
                return
            color_str = parts[1].lower()
            target = parts[2]
            
            # 색상 매핑 (r, g, b) 0.0 ~ 1.0
            colors = {
                "빨강": (1.0, 0.0, 0.0), "red": (1.0, 0.0, 0.0),
                "파랑": (0.0, 0.0, 1.0), "blue": (0.0, 0.0, 1.0),
                "초록": (0.0, 1.0, 0.0), "green": (0.0, 1.0, 0.0),
                "하늘": (0.5, 0.8, 1.0), "하늘색": (0.5, 0.8, 1.0), "skyblue": (0.5, 0.8, 1.0),
                "노랑": (1.0, 1.0, 0.0), "yellow": (1.0, 1.0, 0.0),
                "회색": (0.6, 0.6, 0.6), "gray": (0.6, 0.6, 0.6),
                "주황": (1.0, 0.5, 0.0), "orange": (1.0, 0.5, 0.0)
            }
            
            rgb = colors.get(color_str, (0.8, 0.8, 0.8)) # 기본 밝은 회색(흰색)
            
            objs = self.find_objects(target)
            count = 0
            for obj in objs:
                if hasattr(obj, "ViewObject") and obj.ViewObject:
                    obj.ViewObject.ShapeColor = rgb
                    count += 1
            self.log(f"✅ 성공: {count}개 객체의 색상을 {color_str}로 변경했습니다.")
            
        elif cmd in ("슬라브", "바닥", "slab"):
            thickness = 300.0
            if len(parts) >= 2:
                try:
                    thickness = float(parts[1])
                except ValueError:
                    pass
            
            doc = App.ActiveDocument
            xmin, ymin, xmax, ymax = None, None, None, None
            # 전체 객체의 외곽(Bounding Box)을 계산
            for obj in doc.Objects:
                if obj.Name.startswith("Wall") or obj.Name.startswith("Column"):
                    if hasattr(obj, "Shape") and obj.Shape and hasattr(obj.Shape, "BoundBox"):
                        bb = obj.Shape.BoundBox
                        if xmin is None or bb.XMin < xmin: xmin = bb.XMin
                        if ymin is None or bb.YMin < ymin: ymin = bb.YMin
                        if xmax is None or bb.XMax > xmax: xmax = bb.XMax
                        if ymax is None or bb.YMax > ymax: ymax = bb.YMax
            
            if xmin is None:
                self.log("기준이 될 벽체나 기둥이 없습니다.")
                return
                
            # 여백 500mm 추가
            margin = 500.0
            xmin -= margin
            ymin -= margin
            xmax += margin
            ymax += margin
            
            import Draft, Arch, FreeCAD
            # 직사각형 바닥 뼈대 생성
            p1 = FreeCAD.Vector(xmin, ymin, 0)
            p2 = FreeCAD.Vector(xmax, ymin, 0)
            p3 = FreeCAD.Vector(xmax, ymax, 0)
            p4 = FreeCAD.Vector(xmin, ymax, 0)
            rect = Draft.make_wire([p1, p2, p3, p4], closed=True, face=False)
            rect.Label = "Slab_Base"
            
            # 구조물(슬라브) 생성
            slab = Arch.makeStructure(rect, height=thickness)
            slab.Label = "Main_Slab"
            # Z축으로 두께만큼 내려서 벽체 아래에 깔리게 함
            slab.Placement.Base.z = -thickness
            
            self.log(f"✅ 성공: 전체 도면 크기에 맞춘 두께 {thickness}mm 슬라브가 생성되었습니다.")
            
        elif cmd in ("층추가", "add_floor"):
            import shlex
            try:
                args = shlex.split(cmd_text)
            except Exception:
                args = cmd_text.split()
                
            if len(args) < 4:
                self.log("사용법: 층추가 [\"DXF경로\"] [높이] [층이름]\n예: 층추가 \"C:\\도면\\지하3층.dxf\" 3500 B3")
                return
                
            dxf_path = args[1]
            try:
                z_offset = float(args[2])
            except ValueError:
                self.log("높이 값은 숫자여야 합니다.")
                return
            prefix = args[3]
            
            self.log(f"⏳ [{prefix}] 층 추가를 시작합니다. 경로: {dxf_path}, 높이: {z_offset}")
            
            import sys
            old_argv = sys.argv.copy()
            try:
                import make_json
                sys.argv = ["make_json.py", dxf_path]
                make_json.main()
                self.log("✅ 도면 파싱 완료! 3D 모델 생성을 시작합니다...")
                
                import build_live_walls_2
                import importlib
                sys.argv = ["build_live_walls_2.py", str(z_offset), prefix]
                importlib.reload(build_live_walls_2)
                
                self.log(f"✅ {prefix} 층 추가가 완료되었습니다!")
            except Exception as e:
                self.log(f"❌ 층추가 실패: {e}")
                import traceback
                self.log(traceback.format_exc())
            finally:
                sys.argv = old_argv
                
        elif cmd in ("빌드", "build"):
            if len(parts) < 4:
                self.log("사용법: 빌드 [레이어명] [벽/기둥] [높이]\n예: 빌드 A-Wall 벽 3000")
                return
            layer_name = parts[1]
            obj_type = parts[2]
            try:
                height = float(parts[3])
            except ValueError:
                self.log("높이는 숫자여야 합니다.")
                return
            # 선택: 4번째 인자 = 두께상한(mm). 없으면 기본.
            thick_cap = None
            if len(parts) >= 5:
                try: thick_cap = float(parts[4])
                except ValueError: pass
            self._build_polygonize(layer_name, obj_type, height, thick_cap)

        else:
            self.log(f"알 수 없는 명령어: {cmd}")

    def _build_polygonize(self, layer_name, obj_type, height, thick_cap=None):
        """평면그래프 면추출(polygonize) 기반 외곽선 3D 빌드.
        - FreeCAD 에 import 된 레이어의 모든 edge 수집 → shapely 로 교차점 자동분할(noding)
          → 닫힌 면 추출 → 두께/면적 필터 → Part.Face.extrude → Arch 객체.
        - 그리디 체이닝의 분기점 실패·강제닫기 실패를 제거. 두께 추정 불필요(면이 실제 두께)."""
        doc = App.ActiveDocument
        if not doc:
            self.log("활성화된 문서가 없습니다.")
            return
        try:
            from shapely.geometry import LineString
            from shapely.ops import polygonize, unary_union
        except ImportError:
            self.log("❌ shapely 가 없습니다. FreeCAD python 에 'pip install shapely' 필요.")
            return

        # 1) 레이어 객체 수집 (그룹 또는 라벨 매칭)
        lines = []
        for obj in doc.Objects:
            if (layer_name in obj.Label or layer_name in obj.Name) and hasattr(obj, "Group"):
                lines = list(obj.Group); break
        if not lines:
            for obj in doc.Objects:
                if (layer_name in obj.Label or layer_name in obj.Name):
                    if getattr(obj, "Shape", None) and obj.Shape.Edges:
                        lines.append(obj)
        if not lines:
            self.log(f"레이어 '{layer_name}'에 해당하는 객체를 찾을 수 없습니다.")
            return

        # 2) 모든 edge → shapely LineString (좌표 1mm 반올림 → 노드 정합)
        segs = []
        for ln in lines:
            sh = getattr(ln, "Shape", None)
            if not sh:
                continue
            for ed in sh.Edges:
                vs = ed.Vertexes
                if len(vs) >= 2:
                    a = (round(vs[0].Point.x, 1), round(vs[0].Point.y, 1))
                    b = (round(vs[-1].Point.x, 1), round(vs[-1].Point.y, 1))
                    if a != b:
                        segs.append(LineString([a, b]))
        if not segs:
            self.log("edge 가 없습니다.")
            return
        self.log(f"레이어 '{layer_name}': 객체 {len(lines)}개 / edge {len(segs)}개 → 면추출 중...")

        # 3) 교차점 자동분할(noding) + 닫힌 면 추출
        try:
            regions = list(polygonize(unary_union(segs)))
        except Exception as e:
            self.log(f"❌ polygonize 실패: {e}")
            return
        self.log(f"  닫힌 영역 {len(regions)}개 추출.")

        # 4) 필터 (벽: 얇은 단면 / 기둥: 소형 영역)
        is_wall = obj_type in ("벽", "wall")
        if thick_cap is None:
            thick_cap = 800.0   # 벽 두께 상한(mm). 이보다 두꺼운 면 = 방/외부 → 제외
        count = 0
        skipped = 0
        for poly in regions:
            area = poly.area
            per = poly.length
            if per <= 0 or area < 5000:   # 노이즈(<50cm²) 제외
                continue
            thick = 2.0 * area / per       # 얇은 직사각형의 두께 근사
            if is_wall:
                if thick > thick_cap:      # 방/큰 영역 제외
                    skipped += 1; continue
            else:  # 기둥: 소형 영역만(1.5m² 이하)
                if area > 1_500_000:
                    skipped += 1; continue
            try:
                vecs = [App.Vector(x, y, 0.0) for x, y in poly.exterior.coords]
                solid = Part.Face(Part.makePolygon(vecs)).extrude(App.Vector(0, 0, height))
                if not (solid.isValid() and solid.Volume > 0):
                    continue
                feat = doc.addObject("Part::Feature", f"{obj_type}Solid_{count}")
                feat.Shape = solid
                if is_wall:
                    bim = Arch.makeWall(feat); bim.Label = f"Wall_{layer_name}_{count}"
                else:
                    bim = Arch.makeStructure(feat); bim.Label = f"Col_{layer_name}_{count}"
                count += 1
            except Exception as e:
                self.log(f"  영역 빌드 실패(점 {len(list(poly.exterior.coords))}): {e}")

        # 5) 원본 2D 숨김
        for ln in lines:
            if getattr(ln, "ViewObject", None):
                ln.ViewObject.Visibility = False
        doc.recompute()
        self.log(f"✅ 빌드 완료! {obj_type} {count}개 생성 (방/대형영역 {skipped}개 제외).")

    def _execute_build(self, state, auto_close):
        doc = App.ActiveDocument
        import Part, Arch
        
        closed = state["closed_wires"]
        open_w = state["open_wires"]
        h = state["height"]
        obj_type = state["type"]
        lines = state["lines"]
        layer_name = state["layer"]
        
        final_loops = closed.copy()
        if auto_close:
            for w in open_w:
                if len(w) >= 2:
                    w.append(w[0]) # Auto close loop
                    final_loops.append(w)
                    
        count = 0
        for loop in final_loops:
            try:
                poly = Part.makePolygon(loop)
                face = Part.Face(poly)
                solid = face.extrude(App.Vector(0, 0, h))
                
                feat = doc.addObject("Part::Feature", f"{obj_type}Solid_{count}")
                feat.Shape = solid
                
                if obj_type in ("벽", "wall"):
                    bim_obj = Arch.makeWall(feat)
                    bim_obj.Label = f"Wall_{layer_name}_{count}"
                else:
                    bim_obj = Arch.makeStructure(feat)
                    bim_obj.Label = f"Col_{layer_name}_{count}"
                    
                count += 1
            except Exception as e:
                self.log(f"도형 생성 실패 (루프 점 개수 {len(loop)}): {e}")
                
        for line in lines:
            if hasattr(line, "ViewObject") and line.ViewObject:
                line.ViewObject.Visibility = False
                
        self.log(f"✅ 빌드 완료! {count}개의 {obj_type}이(가) 성공적으로 생성되었습니다.")
        doc.recompute()

def install_cli_macro():
    mw = Gui.getMainWindow()
    # 기존 도킹 패널 제거
    for dw in mw.findChildren(QtWidgets.QDockWidget):
        if dw.windowTitle() == "Antigravity Command Line":
            mw.removeDockWidget(dw)
            dw.deleteLater()

    # 새 패널 추가
    cli_macro = LocalCLIMacro()
    mw.addDockWidget(QtCore.Qt.BottomDockWidgetArea, cli_macro)
    cli_macro.show()

if __name__ == "__main__":
    install_cli_macro()
