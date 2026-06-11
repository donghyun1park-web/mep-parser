import ezdxf
import math
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree
from shapely.ops import linemerge
import yaml
from collections import defaultdict

from mep_macro.geometry import pair_rect, dv

def custom_dbscan_1d(data, eps, min_samples):
    """Simple 1D DBSCAN algorithm for finding representative thickness clusters."""
    if not data:
        return []
    sorted_data = sorted(data)
    clusters = []
    current_cluster = [sorted_data[0]]
    
    for val in sorted_data[1:]:
        if val - current_cluster[-1] <= eps:
            current_cluster.append(val)
        else:
            if len(current_cluster) >= min_samples:
                clusters.append(current_cluster)
            current_cluster = [val]
            
    if len(current_cluster) >= min_samples:
        clusters.append(current_cluster)
        
    # Return average of each valid cluster
    return [sum(c)/len(c) for c in clusters]

def get_angle(p1, p2):
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    return math.degrees(math.atan2(dy, dx)) % 180.0

def proj_dist(a, b, p):
    """Orthogonal distance from line a-b to point p"""
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    L2 = dx*dx + dy*dy
    if L2 == 0:
        return math.hypot(p[0]-a[0], p[1]-a[1])
    t = ((p[0] - a[0])*dx + (p[1] - a[1])*dy) / L2
    t = max(0, min(1, t))
    proj_x = a[0] + t*dx
    proj_y = a[1] + t*dy
    return math.hypot(p[0]-proj_x, p[1]-proj_y)

def pre_merge_lines(lines, angle_tol=1.0, dist_tol=20.0, gap_tol=50.0):
    """Pre-merge collinear segments."""
    merged = []
    used = set()
    
    n = len(lines)
    for i in range(n):
        if i in used:
            continue
        p1, p2 = lines[i]
        ang1 = get_angle(p1, p2)
        
        current_cluster = [lines[i]]
        used.add(i)
        
        for j in range(n):
            if j in used:
                continue
            q1, q2 = lines[j]
            ang2 = get_angle(q1, q2)
            
            ang_diff = min(abs(ang1 - ang2), 180 - abs(ang1 - ang2))
            if ang_diff > angle_tol:
                continue
                
            # Check if q1 or q2 is close to the line p1-p2
            d1 = proj_dist(p1, p2, q1)
            d2 = proj_dist(p1, p2, q2)
            
            if d1 <= dist_tol and d2 <= dist_tol:
                # Check overlap or gap
                # Project all points to the axis p1-p2
                ux, uy = dv(p1, p2)
                def t_val(pt):
                    return (pt[0]-p1[0])*ux + (pt[1]-p1[1])*uy
                
                t_p1, t_p2 = t_val(p1), t_val(p2)
                t_q1, t_q2 = t_val(q1), t_val(q2)
                
                min_p, max_p = min(t_p1, t_p2), max(t_p1, t_p2)
                min_q, max_q = min(t_q1, t_q2), max(t_q1, t_q2)
                
                if (min_q <= max_p + gap_tol) and (min_p <= max_q + gap_tol):
                    current_cluster.append(lines[j])
                    used.add(j)
                    
                    # Update line bounds
                    all_pts = []
                    for c_line in current_cluster:
                        all_pts.extend(c_line)
                    t_vals = [(pt, t_val(pt)) for pt in all_pts]
                    t_vals.sort(key=lambda x: x[1])
                    p1, p2 = t_vals[0][0], t_vals[-1][0]
                    
        merged.append((p1, p2))
        
    return merged

def post_merge_centerlines(centerlines, angle_tol=2.0, gap_tol=1500.0):
    """Post-merge collinear centerlines, preventing over-merge."""
    if not centerlines:
        return []
        
    merged_results = []
    used = set()
    n = len(centerlines)
    
    for i in range(n):
        if i in used:
            continue
            
        c1 = centerlines[i]
        ls1 = c1['centerline']
        if len(ls1.coords) < 2:
            merged_results.append(c1)
            used.add(i)
            continue
            
        p1, p2 = ls1.coords[0], ls1.coords[-1]
        ang1 = get_angle(p1, p2)
        
        current_ls = ls1
        current_thick = c1['thickness']
        used.add(i)
        
        merged_count = 0
        for j in range(n):
            if j in used:
                continue
            c2 = centerlines[j]
            # Only merge if same thickness group (within 20mm)
            if abs(c2['thickness'] - current_thick) > 20:
                continue
                
            ls2 = c2['centerline']
            if len(ls2.coords) < 2:
                continue
                
            q1, q2 = ls2.coords[0], ls2.coords[-1]
            ang2 = get_angle(q1, q2)
            ang_diff = min(abs(ang1 - ang2), 180 - abs(ang1 - ang2))
            
            if ang_diff > angle_tol:
                continue
                
            # Check gap
            d1 = proj_dist(p1, p2, q1)
            d2 = proj_dist(p1, p2, q2)
            
            if d1 <= 50.0 and d2 <= 50.0:
                # Same axis. Check distance.
                dist = current_ls.distance(ls2)
                if dist <= gap_tol:
                    # Valid to merge
                    try:
                        merged = linemerge([current_ls, ls2])
                        if merged.geom_type == 'LineString':
                            # Successful merge
                            current_ls = merged
                            p1, p2 = current_ls.coords[0], current_ls.coords[-1]
                            used.add(j)
                            merged_count += 1
                        elif merged.geom_type == 'MultiLineString':
                            # gap was actually there, linemerge makes it MultiLineString.
                            # So we force connect it by taking extremes.
                            pts = list(current_ls.coords) + list(ls2.coords)
                            ux, uy = dv(p1, p2)
                            def t_val(pt):
                                return (pt[0]-p1[0])*ux + (pt[1]-p1[1])*uy
                            pts.sort(key=t_val)
                            current_ls = LineString([pts[0], pts[-1]])
                            p1, p2 = current_ls.coords[0], current_ls.coords[-1]
                            used.add(j)
                            merged_count += 1
                    except Exception:
                        pass
        
        merged_results.append({
            'centerline': current_ls,
            'thickness': current_thick,
            'height': c1['height'],
            'label': c1['label']
        })
        
    return merged_results

class WallExtractor:
    def __init__(self, config_path):
        self.wall_mappings = {}
        self._load_config(config_path)

    def _load_config(self, config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
                if data and 'mappings' in data:
                    for mapping in data['mappings']:
                        if mapping.get('type') == 'Wall':
                            self.wall_mappings[mapping['layer']] = mapping
        except Exception as e:
            print(f"Error loading config {config_path}: {e}")

    def extract_from_dxf(self, dxf_path):
        """Extracts walls from a DXF file based on the config."""
        try:
            doc = ezdxf.readfile(dxf_path)
            msp = doc.modelspace()
        except Exception as e:
            print(f"Failed to read DXF: {e}")
            return []

        results = []
        
        for layer_name, props in self.wall_mappings.items():
            print(f"\n[Layer: {layer_name}]")
            raw_lines = self._get_lines_from_layer(msp, layer_name)
            if not raw_lines:
                print("  -> 선분 없음.")
                continue
            
            # 1. Pre-merge
            pre_merged_lines = pre_merge_lines(raw_lines)
            print(f"  [Pre-merge] 원본 선분 {len(raw_lines)}개 -> 병합 후 {len(pre_merged_lines)}개")
            
            # 2. Extract pairs (Pass 1 & Pass 2 with Statistical Filtering)
            centerlines = self._extract_centerlines(pre_merged_lines, props)
            
            # 3. Post-merge
            post_merged = post_merge_centerlines(centerlines)
            if centerlines:
                print(f"  [Post-merge] 중심선 {len(centerlines)}개 -> 병합 후 {len(post_merged)}개")
            
            results.extend(post_merged)
            
        return results

    def _get_lines_from_layer(self, msp, layer_name):
        lines = []
        for entity in msp.query(f'LINE LWPOLYLINE[layer=="{layer_name}"]'):
            if entity.dxftype() == 'LINE':
                s, e = entity.dxf.start, entity.dxf.end
                lines.append(((s.x, s.y), (e.x, e.y)))
            elif entity.dxftype() == 'LWPOLYLINE':
                pts = [(p[0], p[1]) for p in entity]
                for i in range(len(pts) - 1):
                    lines.append((pts[i], pts[i+1]))
        return lines

    def _extract_centerlines(self, lines, props):
        shapely_lines = []
        original_segments = []
        min_line_len = 50
        
        for line in lines:
            p1, p2 = line[0], line[1]
            ls = LineString([p1, p2])
            if ls.length >= min_line_len:
                shapely_lines.append(ls)
                original_segments.append((p1, p2))

        if not shapely_lines:
            return []

        tree = STRtree(shapely_lines)
        
        default_thickness = props.get('default_thickness', 200)
        max_search_dist = default_thickness * 3.0

        # PASS 1: Collect all valid pair thicknesses for statistical filtering
        all_thicknesses = []
        pair_candidates = []
        
        for i, (line1, seg1) in enumerate(zip(shapely_lines, original_segments)):
            query_geom = line1.buffer(max_search_dist)
            nearby_indices = tree.query(query_geom)
            
            best_match = None
            best_dist = float('inf')
            best_pts = None

            for j in nearby_indices:
                if j <= i:
                    continue
                
                line2 = shapely_lines[j]
                seg2 = original_segments[j]
                
                dist = line1.distance(line2)
                if dist > max_search_dist or dist < 20: 
                    continue
                
                rect_pts = pair_rect(seg1, seg2, pair_min=20, pair_max=max_search_dist, ovl_min=0.2)
                if rect_pts:
                    if dist < best_dist:
                        best_dist = dist
                        best_match = j
                        best_pts = rect_pts

            if best_match is not None:
                all_thicknesses.append(best_dist)
                pair_candidates.append({
                    'i': i, 'j': best_match, 'dist': best_dist, 'pts': best_pts
                })

        # Statistical Filtering: DBSCAN 1D
        valid_clusters = custom_dbscan_1d(all_thicknesses, eps=30.0, min_samples=2)
        
        if not valid_clusters:
            # Fallback to default thickness if no clusters found
            valid_clusters = [default_thickness]
            print(f"  [Filter] 군집 형성 실패. 기본 두께 {default_thickness}mm 적용.")
        else:
            print(f"  [Filter] 감지된 대표 두께 군집: {[round(c, 1) for c in valid_clusters]}mm")

        # PASS 2: Accept pairs that are within 30mm of any valid cluster
        used = set()
        results = []
        outlier_count = 0
        
        for cand in pair_candidates:
            if cand['i'] in used or cand['j'] in used:
                continue
                
            is_valid = False
            for cluster_val in valid_clusters:
                if abs(cand['dist'] - cluster_val) <= 30.0:
                    is_valid = True
                    break
                    
            if not is_valid:
                outlier_count += 1
                continue
                
            p1, p2, q2, q1 = cand['pts']
            mid_start = ((p1[0] + q1[0])/2.0, (p1[1] + q1[1])/2.0)
            mid_end = ((p2[0] + q2[0])/2.0, (p2[1] + q2[1])/2.0)
            
            results.append({
                'centerline': LineString([mid_start, mid_end]),
                'thickness': cand['dist'],
                'height': props.get('height', 3000),
                'label': props.get('label', 'Wall')
            })
            used.add(cand['i'])
            used.add(cand['j'])
            
        print(f"  [Filter] 버려진 이상치 짝: {outlier_count}개")
        return results
