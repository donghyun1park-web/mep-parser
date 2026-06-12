import FreeCAD as App
import FreeCADGui as Gui
import Arch
import Part

def find_objects(name_str):
    """
    Finds and returns objects matching the name/label.
    If name_str is '전체' or 'all', returns all Wall, Column, Slab, Opening, Merged objects.
    """
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

def get_lines_from_fc_layer(doc, layer_name, only_visible=True):
    """
    Extracts raw 2D line segments from FreeCAD objects matching the layer_name.
    Returns: list of ((x1, y1), (x2, y2)) and the list of source objects.
    """
    target_objs = []
    
    # 1. 1순위: 그룹 이름이 정확히 layer_name과 일치하는 경우
    for obj in doc.Objects:
        if (obj.Label == layer_name or obj.Name == layer_name.replace("-", "_")) and hasattr(obj, "Group"):
            # 그룹 내의 객체들 중 3D 벽체/기둥이 아닌 것만 수집
            for child in obj.Group:
                if not any(child.Name.startswith(prefix) for prefix in ["Wall_", "Col_", "Center_", "BaseSolid_"]):
                    target_objs.append(child)
            break
            
    # 2. 2순위: 단일 객체의 이름이 정확히 일치하거나, layer_name을 포함하는 2D 객체들
    if not target_objs:
        for obj in doc.Objects:
            # 이미 생성된 3D 객체는 무조건 스킵
            if any(obj.Name.startswith(prefix) for prefix in ["Wall_", "Col_", "Center_", "BaseSolid_"]):
                continue
                
            # 이름이 정확히 일치하거나, 느슨한 포함 관계 허용
            if layer_name == obj.Label or layer_name.replace("-", "_") == obj.Name or layer_name in obj.Label:
                if getattr(obj, "Shape", None) and obj.Shape.Edges:
                    target_objs.append(obj)
                    
    segs = []
    for obj in target_objs:
        if only_visible and hasattr(obj, "ViewObject") and obj.ViewObject and not obj.ViewObject.Visibility:
            continue
            
        sh = getattr(obj, "Shape", None)
        if not sh:
            continue
            
        for ed in sh.Edges:
            if ed.Length < 50: # Filter out tiny hatch lines and noise
                continue
                
            is_line = False
            if hasattr(ed, "Curve") and ed.Curve:
                if "Line" in getattr(ed.Curve, "TypeId", ""):
                    is_line = True
            
            if is_line:
                vs = ed.Vertexes
                if len(vs) >= 2:
                    pts = [vs[0].Point, vs[-1].Point]
                else:
                    continue
            else:
                try:
                    num_pts = max(2, int(ed.Length / 100.0) + 1)
                    pts = ed.discretize(Number=num_pts)
                except Exception:
                    vs = ed.Vertexes
                    if len(vs) >= 2:
                        pts = [vs[0].Point, vs[-1].Point]
                    else:
                        continue
                        
            if is_line:
                a = (round(pts[0].x, 1), round(pts[0].y, 1))
                b = (round(pts[-1].x, 1), round(pts[-1].y, 1))
                if a != b:
                    segs.append((a, b))
            else:
                curve_pts = tuple((round(p.x, 1), round(p.y, 1)) for p in pts)
                # Remove consecutive duplicates
                clean_pts = []
                for p in curve_pts:
                    if not clean_pts or clean_pts[-1] != p:
                        clean_pts.append(p)
                if len(clean_pts) > 1:
                    segs.append(tuple(clean_pts))
                    
    return segs, target_objs

def hide_base_solids(doc):
    """
    Hides internal Part::Feature base solids and Draft wires to prevent overlapping and selection issues.
    Ensures Arch objects are visible.
    """
    if Gui.ActiveDocument:
        for obj in doc.Objects:
            if "BaseSolid_" in obj.Name or "WallAxis_" in obj.Name or "ColBase_" in obj.Name:
                if hasattr(obj, "ViewObject") and obj.ViewObject:
                    obj.ViewObject.Visibility = False
            elif obj.Name.startswith("Wall_") or obj.Name.startswith("Col_"):
                if hasattr(obj, "ViewObject") and obj.ViewObject:
                    obj.ViewObject.Visibility = True

def safe_recompute(doc):
    """
    Safely triggers document recompute.
    """
    try:
        doc.recompute()
    except Exception:
        pass

def get_polygon_centerline_and_width(poly):
    """
    Given a shapely Polygon, returns (centerline_pts, width) if it is a simple rectangular wall,
    or None if it's too complex.
    """
    import math
    coords = list(poly.exterior.coords)[:-1]
    if len(coords) != 4:
        # Try to simplify to remove collinear points
        simplified = poly.simplify(5.0)
        coords = list(simplified.exterior.coords)[:-1]
        if len(coords) != 4:
            return None
            
    # Calculate edge lengths
    dists = []
    for i in range(4):
        p1 = coords[i]
        p2 = coords[(i + 1) % 4]
        dists.append((math.dist(p1, p2), i, (i + 1) % 4))
        
    side0_len = dists[0][0]
    side1_len = dists[1][0]
    
    if side0_len < side1_len:
        width = side0_len
        m1 = ((coords[0][0] + coords[1][0]) / 2.0, (coords[0][1] + coords[1][1]) / 2.0)
        m2 = ((coords[2][0] + coords[3][0]) / 2.0, (coords[2][1] + coords[3][1]) / 2.0)
    else:
        width = side1_len
        m1 = ((coords[1][0] + coords[2][0]) / 2.0, (coords[1][1] + coords[2][1]) / 2.0)
        m2 = ((coords[3][0] + coords[0][0]) / 2.0, (coords[3][1] + coords[0][1]) / 2.0)
        
    return ([m1, m2], width)

def build_polygonized_layer(doc, layer_name, obj_type, height, thick_cap=None, logger=None):
    """
    Parses active layout edges of a layer, polygonizes them using Shapely,
    fills corners, merges overlapping geometries, extrudes, and creates Arch walls/structures.
    """
    def log(text):
        if logger:
            logger(text)
        else:
            print(text)

    try:
        from shapely.geometry import LineString, Polygon
        from shapely.ops import polygonize_full, unary_union
        import shapely
    except ImportError:
        log("❌ shapely 가 없습니다. FreeCAD python 에 'pip install shapely' 필요.")
        return

    # 1) 레이어 객체 수집
    lines = []
    for obj in doc.Objects:
        if (layer_name in obj.Label or layer_name in obj.Name) and hasattr(obj, "Group"):
            lines = list(obj.Group)
            break
    if not lines:
        for obj in doc.Objects:
            if (layer_name in obj.Label or layer_name in obj.Name):
                if getattr(obj, "Shape", None) and obj.Shape.Edges:
                    lines.append(obj)
    if not lines:
        log(f"레이어 '{layer_name}'에 해당하는 객체를 찾을 수 없습니다.")
        return

    # Find the Z coordinate of the lines to preserve floor levels
    z_base = 0.0
    for ln in lines:
        sh = getattr(ln, "Shape", None)
        if sh and sh.Vertexes:
            z_base = sh.Vertexes[0].Point.z
            break

    # 2) 모든 edge -> shapely LineString
    segs = []
    for ln in lines:
        if hasattr(ln, "ViewObject") and ln.ViewObject and not ln.ViewObject.Visibility:
            # 숨김 처리된 선은 제외
            continue
            
        sh = getattr(ln, "Shape", None)
        if not sh:
            continue
        for ed in sh.Edges:
            is_line = False
            if hasattr(ed, "Curve") and ed.Curve:
                if getattr(ed.Curve, "TypeId", "") == "Part::GeomLine":
                    is_line = True
            
            if is_line:
                vs = ed.Vertexes
                if len(vs) >= 2:
                    pts = [vs[0].Point, vs[-1].Point]
                else:
                    continue
            else:
                try:
                    num_pts = max(2, int(ed.Length / 100.0) + 1)
                    pts = ed.discretize(Number=num_pts)
                except Exception:
                    vs = ed.Vertexes
                    if len(vs) >= 2:
                        pts = [vs[0].Point, vs[-1].Point]
                    else:
                        continue
                        
            for i in range(len(pts) - 1):
                a = (round(pts[i].x, 1), round(pts[i].y, 1))
                b = (round(pts[i+1].x, 1), round(pts[i+1].y, 1))
                if a != b:
                    segs.append(LineString([a, b]))
                    
    if not segs:
        log("edge 가 없거나 모두 숨김 처리되어 있습니다.")
        return
        
    log(f"레이어 '{layer_name}': 객체 {len(lines)}개 / edge {len(segs)}개 → 면추출 중...")

    # 3) 교차점 자동분할(noding) + 닫힌 면 추출 및 PAIRFACE 병행
    try:
        from .geometry import pair_rect
        import math
        
        snapped_geom = shapely.set_precision(unary_union(segs), grid_size=1.0)
        polys, dangles, cuts, invalids = polygonize_full(snapped_geom)
        
        valid_regions = []
        unclosed_segs = []
        
        def add_to_unclosed(geom_coords):
            c = list(geom_coords)
            for idx in range(len(c) - 1):
                unclosed_segs.append((c[idx], c[idx+1]))

        is_wall = obj_type in ("벽", "wall")
        if thick_cap is None:
            thick_cap = 800.0   # 벽 두께 상한(mm)

        for poly in polys.geoms:
            area = poly.area
            per = poly.length
            if per <= 0 or area < 5000:
                add_to_unclosed(poly.exterior.coords)
                for interior in poly.interiors:
                    add_to_unclosed(interior.coords)
                continue
            
            thick = 2.0 * area / per
            if is_wall and thick > thick_cap:
                add_to_unclosed(poly.exterior.coords)
                for interior in poly.interiors:
                    add_to_unclosed(interior.coords)
                continue
            elif not is_wall and area > 1500000:
                add_to_unclosed(poly.exterior.coords)
                for interior in poly.interiors:
                    add_to_unclosed(interior.coords)
                continue
                
            valid_regions.append(poly)

        for collection in [dangles, cuts, invalids]:
            for geom in collection.geoms:
                if geom.is_empty:
                    continue
                if geom.geom_type == 'LineString':
                    add_to_unclosed(geom.coords)
                elif geom.geom_type == 'MultiLineString':
                    for subgeom in geom.geoms:
                        add_to_unclosed(subgeom.coords)
                        
        cands = []
        for i in range(len(unclosed_segs)):
            for j in range(i + 1, len(unclosed_segs)):
                r = pair_rect(unclosed_segs[i], unclosed_segs[j])
                if r:
                    perp = math.dist(r[0], r[3])
                    cands.append((perp, i, j, r))
        cands.sort()
        
        pair_count = 0
        for perp, i, j, rect in cands:
            valid_regions.append(Polygon(rect))
            pair_count += 1
            
        original_poly_count = len(valid_regions) - pair_count
        
        # Merge overlapping regions
        merged_geom = unary_union(valid_regions)
        final_regions = []
        if merged_geom.geom_type == 'Polygon':
            final_regions.append(merged_geom)
        elif merged_geom.geom_type == 'MultiPolygon':
            final_regions.extend(list(merged_geom.geoms))
        elif merged_geom.geom_type == 'GeometryCollection':
            for g in merged_geom.geoms:
                if g.geom_type == 'Polygon':
                    final_regions.append(g)
                elif g.geom_type == 'MultiPolygon':
                    final_regions.extend(list(g.geoms))
                    
        valid_regions = final_regions
        log(f"  닫힌 영역 {original_poly_count}개 추출 (1mm 스냅 적용), PAIRFACE 평행쌍 {pair_count}개 복구 (전체 {len(valid_regions)}개 덩어리로 병합됨).")
    except Exception as e:
        import traceback
        log(f"❌ polygonize/PAIRFACE 실패:\n{traceback.format_exc()}")
        return

    count = 0
    skipped = 0
    import Draft
    
    # 4) Remove duplicate/highly overlapping regions (unary_union can make OBB decomposition complex,
    # so we instead process individual unique regions to keep them rectangular and editable)
    unique_regions = []
    for poly in valid_regions:
        is_duplicate = False
        for upoly in unique_regions:
            try:
                inter_area = poly.intersection(upoly).area
                if inter_area > 0.8 * poly.area:
                    is_duplicate = True
                    break
            except Exception:
                pass
        if not is_duplicate:
            unique_regions.append(poly)
            
    for poly in unique_regions:
        try:
            if is_wall:
                # 1. Try to create a fully parametric, editable wall based on 2D centerline Draft.Wire
                centerline_info = get_polygon_centerline_and_width(poly)
                if centerline_info:
                    pts, w = centerline_info
                    p1 = App.Vector(pts[0][0], pts[0][1], z_base)
                    p2 = App.Vector(pts[1][0], pts[1][1], z_base)
                    
                    # Create parametric centerline wire
                    base_wire = Draft.make_wire([p1, p2], closed=False, face=False)
                    base_wire.Label = f"WallAxis_{layer_name}_{count}"
                    
                    # Create parametric Arch Wall
                    bim = Arch.makeWall(base_wire, width=w, height=height, align="Center")
                    bim.Label = f"Wall_{layer_name}_{count}"
                    
                    count += 1
                    continue
            else:
                # 2. Try to create a parametric column based on a 2D closed Draft.Wire footprint
                ext_vecs = [App.Vector(x, y, z_base) for x, y in poly.exterior.coords]
                if len(ext_vecs) >= 4:
                    base_wire = Draft.make_wire(ext_vecs, closed=True, face=True)
                    base_wire.Label = f"ColBase_{layer_name}_{count}"
                    
                    # Create parametric Arch Structure
                    bim = Arch.makeStructure(base_wire, height=height)
                    bim.Label = f"Col_{layer_name}_{count}"
                    bim.IfcType = "Column"
                    bim.Normal = App.Vector(0, 0, 1)
                    
                    count += 1
                    continue

            # 3. Fallback: non-parametric 3D solid base (Part::Feature)
            wires = []
            ext_vecs = [App.Vector(x, y, z_base) for x, y in poly.exterior.coords]
            if len(ext_vecs) < 4:
                continue
            wires.append(Part.makePolygon(ext_vecs))
            
            for interior in poly.interiors:
                int_vecs = [App.Vector(x, y, z_base) for x, y in interior.coords]
                if len(int_vecs) >= 4:
                    wires.append(Part.makePolygon(int_vecs))
                    
            face = Part.Face(wires)
            if not face.isValid() or face.Area < 1000.0:
                skipped += 1
                continue
                
            solid = face.extrude(App.Vector(0, 0, height))
            
            feat_name = f"BaseSolid_{layer_name}_{count}".replace("-", "_")
            feat = doc.addObject("Part::Feature", feat_name)
            feat.Shape = solid
            
            if is_wall:
                bim = Arch.makeWall(feat)
                bim.Label = f"Wall_{layer_name}_{count}"
            else:
                bim = Arch.makeStructure(feat)
                bim.Label = f"Col_{layer_name}_{count}"
                bim.IfcType = "Column"
                bim.Normal = App.Vector(0, 0, 1)
            
            count += 1
        except Exception as e:
            log(f"  영역 빌드 실패 (폴백 시도): {e}")
            try:
                wires = []
                ext_vecs = [App.Vector(x, y, z_base) for x, y in poly.exterior.coords]
                if len(ext_vecs) < 4:
                    continue
                wires.append(Part.makePolygon(ext_vecs))
                
                for interior in poly.interiors:
                    int_vecs = [App.Vector(x, y, z_base) for x, y in interior.coords]
                    if len(int_vecs) >= 4:
                        wires.append(Part.makePolygon(int_vecs))
                        
                face = Part.Face(wires)
                if not face.isValid() or face.Area < 1000.0:
                    skipped += 1
                    continue
                    
                solid = face.extrude(App.Vector(0, 0, height))
                
                feat_name = f"BaseSolid_{layer_name}_{count}".replace("-", "_")
                feat = doc.addObject("Part::Feature", feat_name)
                feat.Shape = solid
                
                if is_wall:
                    bim = Arch.makeWall(feat)
                    bim.Label = f"Wall_{layer_name}_{count}"
                else:
                    bim = Arch.makeStructure(feat)
                    bim.Label = f"Col_{layer_name}_{count}"
                    bim.IfcType = "Column"
                    bim.Normal = App.Vector(0, 0, 1)
                
                count += 1
            except Exception as fe:
                log(f"  폴백 빌드 실패: {fe}")

    # 원본 2D 선 숨기기
    for ln in lines:
        if getattr(ln, "ViewObject", None):
            ln.ViewObject.Visibility = False

    safe_recompute(doc)
    hide_base_solids(doc)
    Gui.updateGui()
    
    log(f"✅ 빌드 완료! {obj_type} {count}개 생성 (방/대형영역 {skipped}개 제외).")

def execute_fallback_build(doc, state, auto_close, logger=None):
    """
    Fallback loop builder if needed.
    """
    def log(text):
        if logger:
            logger(text)
        else:
            print(text)
            
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
                w.append(w[0]) # Auto close
                final_loops.append(w)
                
    count = 0
    for loop in final_loops:
        try:
            poly = Part.makePolygon(loop)
            face = Part.Face(poly)
            solid = face.extrude(App.Vector(0, 0, h))
            
            feat_name = f"BaseSolid_{layer_name}_{count}".replace("-", "_")
            feat = doc.addObject("Part::Feature", feat_name)
            feat.Shape = solid
            
            if obj_type in ("벽", "wall"):
                bim_obj = Arch.makeWall(feat)
                bim_obj.Label = f"Wall_{layer_name}_{count}"
            else:
                bim_obj = Arch.makeStructure(feat)
                bim_obj.Label = f"Col_{layer_name}_{count}"
                
            count += 1
        except Exception as e:
            log(f"도형 생성 실패 (루프 점 개수 {len(loop)}): {e}")
            
    for line in lines:
        if hasattr(line, "ViewObject") and line.ViewObject:
            line.ViewObject.Visibility = False
            
    log(f"✅ 빌드 완료! {count}개의 {obj_type}이(가) 성공적으로 생성되었습니다.")
    safe_recompute(doc)
    hide_base_solids(doc)
    Gui.updateGui()
