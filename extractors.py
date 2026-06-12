import ezdxf
import math
import os
import time
from shapely.geometry import LineString, Point, box
from shapely.strtree import STRtree
from shapely.ops import linemerge
import yaml
from collections import defaultdict

from mep_macro.geometry import pair_rect, dv

# ── File-based debug logger ──────────────────────────────────────
_LOG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug_extractors.log")

def _dlog(msg):
    """Append a timestamped message to the debug log file."""
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass

def _dlog_reset():
    """Clear the debug log file."""
    try:
        with open(_LOG_PATH, "w", encoding="utf-8") as f:
            f.write(f"=== Extractor Debug Log  {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
    except Exception:
        pass


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

def pre_merge_lines(lines, angle_tol=1.0, dist_tol=2.0, gap_tol=200.0):
    t0 = time.time()
    _dlog(f"pre_merge_lines START: {len(lines)} lines")
    
    merged = []
    used = set()
    n = len(lines)
    
    straight_indices = []
    straight_lines = []
    for i in range(n):
        if len(lines[i]) != 2:
            merged.append(lines[i])
            used.add(i)
        else:
            straight_indices.append(i)
            straight_lines.append(LineString(lines[i]))
            
    if not straight_lines:
        _dlog(f"pre_merge_lines END (no straight lines): {time.time()-t0:.3f}s")
        return merged
        
    tree = STRtree(straight_lines)
    
    for idx, i in enumerate(straight_indices):
        if i in used:
            continue
            
        p1, p2 = lines[i]
        ang1 = get_angle(p1, p2)
        
        current_cluster = [lines[i]]
        used.add(i)
        
        iterations = 0
        while iterations < 100:  # Safety limit
            iterations += 1
            added = False
            minx = min(p1[0], p2[0]) - gap_tol
            miny = min(p1[1], p2[1]) - gap_tol
            maxx = max(p1[0], p2[0]) + gap_tol
            maxy = max(p1[1], p2[1]) + gap_tol
            query_geom = box(minx, miny, maxx, maxy)
            nearby_idx_list = tree.query(query_geom)
            
            matched_in_this_loop = []
            for j_idx in nearby_idx_list:
                j = straight_indices[j_idx]
                if j in used:
                    continue
                q1, q2 = lines[j]
                ang2 = get_angle(q1, q2)
                
                ang_diff = min(abs(ang1 - ang2), 180 - abs(ang1 - ang2))
                if ang_diff > angle_tol:
                    continue
                    
                d1 = proj_dist(p1, p2, q1)
                d2 = proj_dist(p1, p2, q2)
                
                if d1 <= dist_tol and d2 <= dist_tol:
                    ux, uy = dv(p1, p2)
                    t_p1 = 0.0
                    t_p2 = (p2[0]-p1[0])*ux + (p2[1]-p1[1])*uy
                    t_q1 = (q1[0]-p1[0])*ux + (q1[1]-p1[1])*uy
                    t_q2 = (q2[0]-p1[0])*ux + (q2[1]-p1[1])*uy
                    
                    min_p, max_p = min(t_p1, t_p2), max(t_p1, t_p2)
                    min_q, max_q = min(t_q1, t_q2), max(t_q1, t_q2)
                    
                    if (min_q <= max_p + gap_tol) and (min_p <= max_q + gap_tol):
                        matched_in_this_loop.append(lines[j])
                        used.add(j)
                        added = True
                        
            if added:
                current_cluster.extend(matched_in_this_loop)
                ux, uy = dv(p1, p2)
                all_pts = []
                for c_line in current_cluster:
                    all_pts.extend(c_line)
                t_vals = [((pt[0]-p1[0])*ux + (pt[1]-p1[1])*uy, pt) for pt in all_pts]
                t_vals.sort(key=lambda x: x[0])
                p1, p2 = t_vals[0][1], t_vals[-1][1]
            else:
                break
                
        merged.append((p1, p2))
        
    _dlog(f"pre_merge_lines END: {len(lines)} -> {len(merged)} in {time.time()-t0:.3f}s")
    return merged

def post_merge_centerlines(centerlines, angle_tol=2.0, gap_tol=1500.0):
    """Post-merge collinear centerlines using STRtree for O(n log n) performance."""
    t0 = time.time()
    n = len(centerlines)
    _dlog(f"post_merge_centerlines START: {n} centerlines")
    
    if not centerlines:
        return []
    
    # Build spatial index for fast neighbour lookup
    geom_list = [c['centerline'] for c in centerlines]
    tree = STRtree(geom_list)
        
    merged_results = []
    used = set()
    
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
        
        # Query nearby centerlines using spatial index + gap_tol buffer
        bounds = current_ls.bounds
        search_box = box(
            bounds[0] - gap_tol, bounds[1] - gap_tol,
            bounds[2] + gap_tol, bounds[3] + gap_tol
        )
        nearby = tree.query(search_box)
        
        for j in nearby:
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
                            current_ls = merged
                            p1, p2 = current_ls.coords[0], current_ls.coords[-1]
                            used.add(j)
                        elif merged.geom_type == 'MultiLineString':
                            pts = list(current_ls.coords) + list(ls2.coords)
                            ux, uy = dv(p1, p2)
                            pts.sort(key=lambda pt: (pt[0]-p1[0])*ux + (pt[1]-p1[1])*uy)
                            current_ls = LineString([pts[0], pts[-1]])
                            p1, p2 = current_ls.coords[0], current_ls.coords[-1]
                            used.add(j)
                    except Exception:
                        pass
        
        merged_results.append({
            'centerline': current_ls,
            'thickness': current_thick,
            'height': c1['height'],
            'label': c1['label']
        })
        
    _dlog(f"post_merge_centerlines END: {n} -> {len(merged_results)} in {time.time()-t0:.3f}s")
    return merged_results

class WallExtractor:
    def __init__(self, config_path):
        self.wall_mappings = {}
        self._load_config(config_path)

    def _load_config(self, config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
                
                # 지원 포맷 1: 계층형 (layers -> wall -> [종류] -> patterns)
                if data and 'layers' in data and 'wall' in data['layers']:
                    for wtype, props in data['layers']['wall'].items():
                        patterns = props.get('patterns', [])
                        for pat in patterns:
                            # 와일드카드 일단 제거해서 정확한 이름으로 취급
                            clean_name = pat.replace('*', '').strip()
                            if clean_name:
                                self.wall_mappings[clean_name] = {
                                    'layer': clean_name,
                                    'default_thickness': props.get('default_thickness', 200),
                                    'height': props.get('height', 3000),
                                    'label': f"Wall_{wtype}"
                                }
                # 지원 포맷 2: 플랫 배열형 (mappings)
                elif data and 'mappings' in data:
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

    def extract_from_raw_lines(self, raw_lines, props):
        """Extracts walls directly from a list of raw 2D line segments."""
        _dlog_reset()
        _dlog(f"extract_from_raw_lines START: {len(raw_lines)} raw lines")
        
        if not raw_lines:
            return []
            
        print(f"\n[Viewport Extraction]")
        # 1. Pre-merge
        pre_merged_lines = pre_merge_lines(raw_lines)
        print(f"  [Pre-merge] 원본 선분 {len(raw_lines)}개 -> 병합 후 {len(pre_merged_lines)}개")
        _dlog(f"Pre-merge done: {len(raw_lines)} -> {len(pre_merged_lines)}")
        
        # 2. Extract pairs (Pass 1 & Pass 2 with Statistical Filtering)
        centerlines = self._extract_centerlines(pre_merged_lines, props)
        _dlog(f"Centerline extraction done: {len(centerlines)} centerlines")
        
        # 3. Post-merge
        post_merged = post_merge_centerlines(centerlines)
        if centerlines:
            print(f"  [Post-merge] 중심선 {len(centerlines)}개 -> 병합 후 {len(post_merged)}개")
        
        _dlog(f"extract_from_raw_lines END: {len(post_merged)} final walls")
        return post_merged

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
        t0 = time.time()
        _dlog(f"_extract_centerlines START: {len(lines)} lines")
        
        shapely_lines = []
        original_segments = []
        min_line_len = 200  # Skip hatch/noise fragments shorter than 200mm
        
        for line in lines:
            if len(line) == 2:
                p1, p2 = line[0], line[1]
                ls = LineString([p1, p2])
                original_segments.append((p1, p2))
            else:
                ls = LineString(line)
                original_segments.append(line)
            if ls.length >= min_line_len:
                shapely_lines.append(ls)
            else:
                # Keep indices in sync: add a None placeholder
                # No — just track separately
                pass

        # Re-sync: shapely_lines and original_segments must have same length
        # Rebuild with only those passing length filter
        shapely_lines = []
        original_segments = []
        for line in lines:
            if len(line) == 2:
                p1, p2 = line[0], line[1]
                ls = LineString([p1, p2])
                seg = (p1, p2)
            else:
                ls = LineString(line)
                seg = line
            if ls.length >= min_line_len:
                shapely_lines.append(ls)
                original_segments.append(seg)

        if not shapely_lines:
            _dlog("_extract_centerlines END: no lines passed length filter")
            return []

        _dlog(f"  {len(shapely_lines)} lines passed length filter (>= {min_line_len}mm)")

        # Precompute length, angle, and unit vector for each line
        line_lengths = []
        line_uvs = []
        for ls, seg in zip(shapely_lines, original_segments):
            length = ls.length
            p1, p2 = seg[0], seg[-1]
            ux, uy = dv(p1, p2)
            line_lengths.append(length)
            line_uvs.append((ux, uy))

        tree = STRtree(shapely_lines)
        
        default_thickness = props.get('default_thickness', 200)
        max_search_dist = default_thickness * 3.0

        # PASS 1: Collect all valid pair thicknesses for statistical filtering
        all_thicknesses = []
        pair_candidates = []
        
        n_searched = 0
        n_dot_skipped = 0
        n_pair_skipped = 0
        
        for i in range(len(shapely_lines)):
            # Primary wall boundary line must be at least 250mm long
            if line_lengths[i] < 250:
                continue
            
            n_searched += 1
            line1 = shapely_lines[i]
            seg1 = original_segments[i]
            ux1, uy1 = line_uvs[i]
            
            bounds = line1.bounds
            query_geom = box(
                bounds[0] - max_search_dist, bounds[1] - max_search_dist,
                bounds[2] + max_search_dist, bounds[3] + max_search_dist
            )
            nearby_indices = tree.query(query_geom)
            
            best_match = None
            best_dist = float('inf')
            best_pts = None

            for j in nearby_indices:
                if j <= i:
                    continue
                
                ux2, uy2 = line_uvs[j]
                
                # 1. Fast parallel check (dot product)
                dot = abs(ux1 * ux2 + uy1 * uy2)
                if dot < 0.985:
                    n_dot_skipped += 1
                    continue
                
                # 2. Fast vector-math overlap check
                seg2 = original_segments[j]
                rect_pts = pair_rect(seg1, seg2, pair_min=20, pair_max=max_search_dist, ovl_min=0.2)
                if not rect_pts:
                    n_pair_skipped += 1
                    continue
                
                # 3. Final shapely distance
                line2 = shapely_lines[j]
                dist = line1.distance(line2)
                if dist > max_search_dist or dist < 20: 
                    continue
                
                if dist < best_dist:
                    best_dist = dist
                    best_match = j
                    best_pts = rect_pts

            if best_match is not None:
                pair_candidates.append({
                    'i': i, 'j': best_match, 'dist': best_dist, 'pts': best_pts
                })
                if line_lengths[i] >= 500 or line_lengths[best_match] >= 500:
                    all_thicknesses.append(best_dist)

        _dlog(f"  PASS1 done: searched={n_searched}, dot_skip={n_dot_skipped}, pair_skip={n_pair_skipped}, candidates={len(pair_candidates)} in {time.time()-t0:.3f}s")

        # Statistical Filtering: DBSCAN 1D
        valid_clusters = custom_dbscan_1d(all_thicknesses, eps=30.0, min_samples=1)
        
        if not valid_clusters:
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
            
            cl = LineString([mid_start, mid_end])
            # Skip centerlines shorter than wall thickness (causes FreeCAD max() error)
            if cl.length < cand['dist']:
                outlier_count += 1
                continue
            
            results.append({
                'centerline': cl,
                'thickness': cand['dist'],
                'height': props.get('height', 3000),
                'label': props.get('label', 'Wall')
            })
            used.add(cand['i'])
            used.add(cand['j'])
            
        print(f"  [Filter] 버려진 이상치 짝: {outlier_count}개")
        
        # Single Line Fallback 비활성화:
        # 1697개 선분 중 짝이 안 맞은 583개가 전부 벽으로 생성되어 FreeCAD를 멈추게 함.
        # 실제 벽체는 평행선 쌍으로만 추출 (paired walls).
        # 단일 선은 대부분 해치선, 구조 주석, 또는 짝이 누락된 노이즈임.
        single_count = 0

        _dlog(f"  PASS2 done: paired={len(results)}, single_fallback={single_count} (disabled), outliers={outlier_count}, total={len(results)} in {time.time()-t0:.3f}s")
        return results
