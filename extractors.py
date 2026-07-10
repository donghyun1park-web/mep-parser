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


def detect_columns_from_segments(segments, min_area=40000.0, max_area=2500000.0,
                                 snap=120.0, min_segs=2, min_aspect=0.25):
    """미페어링(벽으로 안 묶인) 짧은 세그먼트들을 클러스터링 → 기둥 footprint(bbox) 산출.

    원리: 평면도의 기둥/샤프트는 짧은 선 2~N개가 박스(또는 ㄷ브래킷/X마크)를 이룬다.
    끝점이 가까운(snap mm) 세그먼트끼리 union-find 로 한 덩어리로 묶고, 덩어리의
    bounding box 가 '기둥 크기·정사각형에 가까움'이면 기둥으로 채택.
      - min_area~max_area: 기둥 면적 범위(기본 0.04~2.5 m²). 방(큼)·노이즈(작음) 배제.
      - min_aspect: 단변/장변 비. 너무 길쭉하면(코리도·벽 잔재) 배제.
      - min_segs: 덩어리 최소 세그먼트 수(2=L코너 이상). 외톨이 스텁(문선)은 제외.
    정사각 기둥은 이미 양면 페어링→ColBase 폴백으로 잡히므로, 이 함수는 그 외(브래킷/
    비정형) 기둥 보강용. 입력은 '미페어링 세그먼트'라 벽과 중복되지 않는다.
    반환: [[(x,y)x4], ...] 각 기둥의 사각 footprint(반시계). O(N log N), 결정론."""
    n = len(segments)
    if n == 0:
        return []
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)  # 작은 인덱스 root → 결정론

    # 끝점 (x, y, seg_index) x-정렬 슬라이딩 윈도우로 근접 끝점 union
    pts = []
    for i, (a, b) in enumerate(segments):
        pts.append((float(a[0]), float(a[1]), i))
        pts.append((float(b[0]), float(b[1]), i))
    pts.sort()
    snap2 = snap * snap
    for idx in range(len(pts)):
        x0, y0, i0 = pts[idx]
        j = idx + 1
        while j < len(pts) and pts[j][0] - x0 <= snap:
            x1, y1, i1 = pts[j]
            if i0 != i1 and (x1 - x0) ** 2 + (y1 - y0) ** 2 <= snap2:
                union(i0, i1)
            j += 1

    clusters = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)

    cols = []
    for root in sorted(clusters):
        members = clusters[root]
        if len(members) < min_segs:
            continue
        xs, ys = [], []
        for m in members:
            for p in segments[m]:
                xs.append(float(p[0])); ys.append(float(p[1]))
        minx, miny, maxx, maxy = min(xs), min(ys), max(xs), max(ys)
        w, h = maxx - minx, maxy - miny
        area = w * h
        if not (min_area <= area <= max_area):
            continue
        if max(w, h) <= 0 or min(w, h) / max(w, h) < min_aspect:
            continue
        cols.append([(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)])
    return cols


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
    ux, uy = dv(a, b)
    # The normal vector is (-uy, ux)
    # The orthogonal distance is the dot product of vector a->p with the normal vector
    dist = abs((p[0]-a[0])*(-uy) + (p[1]-a[1])*ux)
    return dist

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

def post_merge_centerlines(centerlines, angle_tol=2.0, gap_tol=300.0):
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
                            # Flatten to 2 points to remove tiny kinks that cause FreeCAD offset spikes to infinity
                            current_ls = LineString([merged.coords[0], merged.coords[-1]])
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
    
    # --- Curve Reconstruction: Join Polylines ---
    def join_polylines(lines_data, gap_tol=200.0):
        # lines_data: list of dicts with 'centerline', 'thickness', etc.
        groups = {}
        for c in lines_data:
            k = (round(c['thickness']), round(c['height']), c['label'])
            if k not in groups: groups[k] = []
            groups[k].append(c)
            
        joined_results = []
        for k, group in groups.items():
            lines = [c['centerline'] for c in group]
            used_lines = set()
            for i, l1 in enumerate(lines):
                if i in used_lines: continue
                used_lines.add(i)
                current_coords = list(l1.coords)
                
                while True:
                    found = False
                    for j, l2 in enumerate(lines):
                        if j in used_lines: continue
                        c2 = list(l2.coords)
                        if math.hypot(c2[0][0]-current_coords[-1][0], c2[0][1]-current_coords[-1][1]) <= gap_tol:
                            current_coords.extend(c2[1:])
                            used_lines.add(j)
                            found = True; break
                        elif math.hypot(c2[-1][0]-current_coords[-1][0], c2[-1][1]-current_coords[-1][1]) <= gap_tol:
                            current_coords.extend(c2[-2::-1])
                            used_lines.add(j)
                            found = True; break
                        elif math.hypot(c2[-1][0]-current_coords[0][0], c2[-1][1]-current_coords[0][1]) <= gap_tol:
                            current_coords = c2[:-1] + current_coords
                            used_lines.add(j)
                            found = True; break
                        elif math.hypot(c2[0][0]-current_coords[0][0], c2[0][1]-current_coords[0][1]) <= gap_tol:
                            current_coords = c2[:0:-1] + current_coords
                            used_lines.add(j)
                            found = True; break
                    if not found:
                        break
                
                # 1. U-turn prevention
                if len(current_coords) > 2:
                    clean_coords = [current_coords[0]]
                    for idx in range(1, len(current_coords)-1):
                        p_prev = clean_coords[-1]
                        p_curr = current_coords[idx]
                        p_next = current_coords[idx+1]
                        if p_prev == p_curr or p_curr == p_next: continue
                        u1 = dv(p_prev, p_curr)
                        u2 = dv(p_curr, p_next)
                        dot = max(-1.0, min(1.0, u1[0]*u2[0] + u1[1]*u2[1]))
                        if math.degrees(math.acos(dot)) <= 170.0:
                            clean_coords.append(p_curr)
                    clean_coords.append(current_coords[-1])
                else:
                    clean_coords = current_coords
                    
                # 2. Shapely Simplify (Douglas-Peucker)
                # Removes noisy straight line wobbles while PRESERVING large curves!
                # Also makes segments longer, preventing OpenCascade offset failures in Arch.makeWall
                if len(clean_coords) > 1:
                    final_ls = LineString(clean_coords).simplify(5.0, preserve_topology=True)
                else:
                    final_ls = LineString(clean_coords)
                    
                joined_results.append({
                    'centerline': final_ls,
                    'thickness': k[0],
                    'height': k[1],
                    'label': k[2]
                })
        return joined_results

    final_results = join_polylines(merged_results)
    _dlog(f"join_polylines END: {len(merged_results)} -> {len(final_results)}")
    return final_results

class WallExtractor:
    def __init__(self, config_path):
        self.wall_mappings = {}
        self.last_unpaired_segments = []  # [(p1, p2), ...] 마지막 추출에서 짝을 못 찾고 버려진 원본 선분
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
        all_unpaired = []

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
            all_unpaired.extend(self.last_unpaired_segments)

            # 3. Post-merge
            post_merged = post_merge_centerlines(centerlines)
            if centerlines:
                print(f"  [Post-merge] 중심선 {len(centerlines)}개 -> 병합 후 {len(post_merged)}개")

            if self.last_unpaired_segments:
                print(f"  [경고] '{layer_name}' 레이어에서 짝을 못 찾아 벽으로 생성되지 않은 선분 "
                      f"{len(self.last_unpaired_segments)}개")

            results.extend(post_merged)

        self.last_unpaired_segments = all_unpaired
        return results

    def extract_from_raw_lines(self, raw_lines, props):
        """Extracts walls directly from a list of raw 2D line segments."""
        _dlog_reset()
        _dlog(f"extract_from_raw_lines START: {len(raw_lines)} raw lines")
        self.last_unpaired_segments = []

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

        if self.last_unpaired_segments:
            print(f"  [경고] 짝을 찾지 못해 벽으로 생성되지 않은 선분 {len(self.last_unpaired_segments)}개 "
                  f"(200mm 이상, 단일선/페어링 실패)")

        _dlog(f"extract_from_raw_lines END: {len(post_merged)} final walls, "
              f"{len(self.last_unpaired_segments)} unpaired segments dropped")
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
        min_line_len = 50  # Skip hatch/noise fragments shorter than 50mm
        
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
        max_search_dist = default_thickness * 3.0 + 50.0

        # PASS 1: Collect all valid pair thicknesses for statistical filtering
        all_thicknesses = []
        pair_candidates = []
        
        n_searched = 0
        n_dot_skipped = 0
        n_pair_skipped = 0
        
        for i in range(len(shapely_lines)):
            # Primary wall boundary line must be at least 50mm long (was 250, but curves are 100mm)
            if line_lengths[i] < 50:
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
                rect_pts = pair_rect(seg1, seg2, pair_min=5, pair_max=max_search_dist, ovl_min=0.2)
                if not rect_pts:
                    n_pair_skipped += 1
                    continue
                
                # 3. Final shapely distance
                line2 = shapely_lines[j]
                dist = line1.distance(line2)
                if dist > max_search_dist or dist < 5: 
                    continue
                
                # 겹침 길이(두 선이 나란히 겹치는 구간) = 진짜 벽면 판별의 핵심 지표.
                # rect_pts = [p1, p2, q2, q1]; p1->p2 가 벽 축 방향 겹침 길이.
                pp1, pp2 = rect_pts[0], rect_pts[1]
                ov_len = math.hypot(pp2[0] - pp1[0], pp2[1] - pp1[1])
                pair_candidates.append({
                    'i': i, 'j': j, 'dist': dist, 'pts': rect_pts, 'ov': ov_len
                })
                if dist >= 40: # 해치선(주로 30mm 이하 간격) 배제를 위해 최소 두께 40mm 이상만 통계에 포함
                    all_thicknesses.append(dist)

        _dlog(f"  PASS1 done: searched={n_searched}, dot_skip={n_dot_skipped}, pair_skip={n_pair_skipped}, candidates={len(pair_candidates)} in {time.time()-t0:.3f}s")

        # Statistical Filtering: Histogram Peak Finder
        # 500mm 이상 긴 선분들로 파악한 "진짜 벽체 두께" 피크값들만 추출
        valid_clusters = []
        if all_thicknesses:
            bins = {}
            for d in all_thicknesses:
                b = round(d, -1) # 10mm 단위로 반올림
                bins[b] = bins.get(b, 0) + 1
            # 도면 내에 존재하는 모든 두께(빈도 1 이상)를 유효 군집으로 인정하여
            # 소수의 기둥, 옹벽, 변형벽 등이 Outlier로 오판되어 누락되는 현상 방지
            for b in bins.keys():
                valid_clusters.append(float(b))
        
        if not valid_clusters:
            valid_clusters = [default_thickness]
            print(f"  [Filter] 군집 형성 실패. 기본 두께 {default_thickness}mm 적용.")
        else:
            print(f"  [Filter] 감지된 대표 두께 군집: {[round(c, 1) for c in valid_clusters]}mm")

        # PASS 2: Accept pairs that are within 30mm of any valid cluster.
        # 그리디 매칭 + 이미 짝지어진 선 재사용 금지(같은 선이 여러 벽에 중복 사용되는 것 방지).
        # 정렬 기준 = 겹침 길이(ov) 내림차순.  ★중요★
        #   - 한 선이 가까운 선(예 200mm)과 먼 진짜 반대면(예 450mm) 둘 다와 짝지을 수 있을 때,
        #     '거리 오름차순'으로 처리하면 항상 가까운 쪽을 먼저 집어 200mm '너무 얇은' 벽을
        #     만들고 진짜 면을 놓친다(지하3층 A-CON 에서 벽이 한쪽 노란선에만 붙고 반대 노란선엔
        #     안 닿는 어긋남으로 나타남).
        #   - 진짜 벽 양면은 벽 길이만큼 길게 나란히 '겹치고', 직각벽 끝선 같은 가짜 짝은 짧게
        #     겹친다 → 겹침 긴 순서로 처리하면 진짜 면 쌍이 먼저 선을 차지해 올바른 두께가 나온다.
        #   - 동률이면 가까운 두께 우선(거리 오름차순)으로 안정적 결정.
        pair_candidates.sort(key=lambda c: (-c['ov'], c['dist']))
        used = set()
        results = []
        outlier_count = 0
        reused_count = 0

        for cand in pair_candidates:
            if cand['i'] in used or cand['j'] in used:
                _dlog(f"REJECTED line already paired: i={cand['i']} j={cand['j']} dist={cand['dist']:.1f}")
                reused_count += 1
                continue

            is_valid = False
            for cluster_val in valid_clusters:
                if abs(cand['dist'] - cluster_val) <= 15.0:
                    is_valid = True
                    break
                    
            if not is_valid:
                _dlog(f"REJECTED thickness outlier: dist={cand['dist']:.1f}")
                outlier_count += 1
                continue
                
            p1, p2, q2, q1 = cand['pts']
            mid_start = ((p1[0] + q1[0])/2.0, (p1[1] + q1[1])/2.0)
            mid_end = ((p2[0] + q2[0])/2.0, (p2[1] + q2[1])/2.0)
            cl = LineString([mid_start, mid_end])
            
            # 1:N 페어링 시 발생하는 다중 레이어 벽 중복 방지 로직
            # 완전히 일치하거나 평행하고 세로로 겹치는 경우 중복 생성 방지
            is_dup = False
            for res in results:
                e_cl = res['centerline']
                u1 = dv(cl.coords[0], cl.coords[-1])
                u2 = dv(e_cl.coords[0], e_cl.coords[-1])
                # 평행한지 확인
                if abs(u1[0]*u2[0] + u1[1]*u2[1]) > 0.985:
                    # 엄격한 중복 체크: 30mm 이내로 거의 동일선상에 있을 때만 중복으로 간주
                    if cl.distance(e_cl) < 30:
                        # 중심축(u1)에 투영하여 겹치는 구간 확인
                        t_e1 = (e_cl.coords[0][0] - cl.coords[0][0])*u1[0] + (e_cl.coords[0][1] - cl.coords[0][1])*u1[1]
                        t_e2 = (e_cl.coords[-1][0] - cl.coords[0][0])*u1[0] + (e_cl.coords[-1][1] - cl.coords[0][1])*u1[1]
                        min_e, max_e = min(t_e1, t_e2), max(t_e1, t_e2)
                        
                        t_c1 = 0
                        t_c2 = cl.length
                        
                        overlap = max(0, min(max_e, t_c2) - max(min_e, t_c1))
                        # 겹치는 구간이 80% 이상일 때만 진짜 중복 다중레이어 벽체로 판정 (파이프 샤프트 등 보존)
                        if overlap > 0.8 * cl.length:
                            is_dup = True
                            break
            
            if is_dup:
                _dlog(f"REJECTED duplicate overlap: len={cl.length:.1f}, dist_to_ecl={cl.distance(e_cl):.1f}")
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
            
        print(f"  [Filter] 버려진 이상치 짝: {outlier_count}개, 중복선 짝: {reused_count}개")
        
        # Single Line Fallback 비활성화:
        # 1697개 선분 중 짝이 안 맞은 583개가 전부 벽으로 생성되어 FreeCAD를 멈추게 함.
        # 실제 벽체는 평행선 쌍으로만 추출 (paired walls).
        # 단일 선은 대부분 해치선, 구조 주석, 또는 짝이 누락된 노이즈임.
        # -> 벽으로 만들지는 않지만, 사용자가 "DXF엔 있는데 안 생긴 벽"을 알아챌 수 있게
        #    어떤 원본 선분이 짝을 못 찾아 버려졌는지는 별도로 보존해 둔다.
        single_count = 0
        unpaired_min_len = 200.0  # 해치/주석 노이즈(50~200mm)는 알림에서 제외, 벽일 가능성 있는 길이만 보고
        self.last_unpaired_segments = [
            original_segments[k] for k in range(len(shapely_lines))
            if k not in used and line_lengths[k] >= unpaired_min_len
        ]

        _dlog(f"  PASS2 done: paired={len(results)}, single_fallback={single_count} (disabled), "
              f"outliers={outlier_count}, reused_skipped={reused_count}, "
              f"unpaired_unused={len(self.last_unpaired_segments)} "
              f"(>= {unpaired_min_len}mm), total={len(results)} in {time.time()-t0:.3f}s")
        return results
