"""Deterministic, source-preserving MEP curves and endpoint topology (millimetres)."""
from collections import defaultdict
import copy
import hashlib
import json
import math

from ezdxf.math import arc_angle_span_deg


def _xyz(point, scale):
    return [float(point[i]) * scale for i in range(3)]


def _signature(value):
    def norm(v):
        if isinstance(v, float):
            return round(v, 9)
        if isinstance(v, dict):
            return {k: norm(x) for k, x in v.items()}
        if isinstance(v, (tuple, list)):
            return [norm(x) for x in v]
        return v
    return hashlib.sha256(json.dumps(norm(value), sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()[:24]


def extract_curve(entity, scale=1.0, chord_error_mm=.25, source_ref=None, max_points=100000):
    """Evaluate the actual curve; retain analytic primitives independently of sampling.

    LINE/ARC and bulge polylines have exact source lengths. General SPLINE/ELLIPSE
    lengths remain explicitly approximate. Nonplanar routes require a 3D contract
    and are rejected instead of silently flattening their elevation.
    """
    if not math.isfinite(scale) or scale <= 0 or not math.isfinite(chord_error_mm) or chord_error_mm <= 0:
        raise ValueError('Curve scale and chord error must be positive and finite')
    kind = entity.dxftype()
    points = []
    exact = None
    closed = False
    spec = {'type': kind}

    def add(p):
        if len(points) >= max_points:
            raise ValueError('Curve point budget exceeded; requested tolerance was not met')
        q = _xyz(p, scale)
        if not all(math.isfinite(v) for v in q):
            raise ValueError('Nonfinite curve coordinate')
        points.append(q)

    if kind == 'LINE':
        a, b = entity.dxf.start, entity.dxf.end
        add(a); add(b)
        exact = (b - a).magnitude * scale
        spec.update(start_mm=_xyz(a, scale), end_mm=_xyz(b, scale))
    elif kind in ('ARC', 'CIRCLE'):
        r = float(entity.dxf.radius) * scale
        if r <= 0:
            raise ValueError('Nonpositive circular radius')
        start = float(entity.dxf.start_angle) if kind == 'ARC' else 0.0
        end = float(entity.dxf.end_angle) if kind == 'ARC' else 360.0
        span = math.radians(arc_angle_span_deg(start, end))
        if kind == 'CIRCLE':
            span = math.tau; closed = True
        if span <= 0:
            raise ValueError('Zero circular span')
        angle = 2 * math.acos(max(-1., 1 - min(chord_error_mm / r, 1.)))
        count = max(1, math.ceil(span / angle))
        if count + 1 > max_points:
            raise ValueError('Curve point budget exceeded; requested tolerance was not met')
        angles = [start + math.degrees(span) * i / count for i in range(count + 1)]
        for p in entity.vertices(angles):
            add(p)
        exact = r * span
        spec.update(center_ocs_mm=_xyz(entity.dxf.center, scale), radius_mm=r,
                    extrusion=list(entity.dxf.extrusion), start_angle_deg=start,
                    end_angle_deg=end, span_radians=span)
    elif kind in ('LWPOLYLINE', 'POLYLINE'):
        if kind == 'POLYLINE' and not (entity.is_2d_polyline or entity.is_3d_polyline):
            raise ValueError('Polygon mesh/polyface is not a MEP centerline')
        if kind == 'POLYLINE' and entity.is_3d_polyline:
            for v in entity.vertices:
                add(v.dxf.location)
            closed = bool(entity.is_closed)
            if closed and points and math.dist(points[0], points[-1]) > 1e-9:
                points.append(list(points[0]))
            exact = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
            spec['vertices_wcs_mm'] = copy.deepcopy(points)
        else:
            closed = bool(entity.closed if kind == 'LWPOLYLINE' else entity.is_closed)
            vertices = list(entity.vertices_in_wcs()) if kind == 'LWPOLYLINE' else list(entity.points_in_wcs())
            specs = []; exact = 0.0
            for i, part in enumerate(entity.virtual_entities()):
                sub = extract_curve(part, scale, chord_error_mm, max_points=max_points)
                subpoints = [[p[0], p[1], sub['source_elevation_mm']] for p in sub['points']]
                target = _xyz(vertices[i], scale)
                reverse = math.dist(subpoints[-1], target) < math.dist(subpoints[0], target)
                if reverse:
                    subpoints.reverse()
                if points and math.dist(points[-1], subpoints[0]) > 1e-6:
                    raise ValueError('Polyline primitive endpoints are inconsistent')
                points.extend(subpoints[1:] if points else subpoints)
                if len(points) > max_points:
                    raise ValueError('Curve point budget exceeded; requested tolerance was not met')
                exact += sub['source_length_mm']
                specs.append({'reverse': reverse, 'primitive': sub['source_geometry'][0]})
            spec.update(parts=specs, closed=closed)
    elif kind in ('SPLINE', 'ELLIPSE'):
        for p in entity.flattening(chord_error_mm / scale, segments=8):
            add(p)
        closed = bool(entity.closed) if kind == 'SPLINE' else (
            len(points) > 1 and math.dist(points[0], points[-1]) < 1e-6)
        if kind == 'SPLINE':
            spec.update(degree=int(entity.dxf.degree), control_points_mm=[_xyz(p, scale) for p in entity.control_points],
                        fit_points_mm=[_xyz(p, scale) for p in entity.fit_points], knots=list(map(float, entity.knots)),
                        weights=list(map(float, entity.weights)), closed=closed)
        else:
            spec.update(center_mm=_xyz(entity.dxf.center, scale), major_axis_mm=_xyz(entity.dxf.major_axis, scale),
                        ratio=float(entity.dxf.ratio), start_param=float(entity.dxf.start_param),
                        end_param=float(entity.dxf.end_param), extrusion=list(entity.dxf.extrusion))
    else:
        raise ValueError('Unsupported MEP curve: ' + kind)
    if len(points) < 2:
        raise ValueError('Curve has fewer than two points')
    zvals = [p[2] for p in points]
    if max(zvals) - min(zvals) > 1e-5:
        raise ValueError('Nonplanar source route requires explicit 3D route support')
    ref = copy.deepcopy(source_ref or {'handle': str(entity.dxf.get('handle', '') or ''),
                                      'type': kind, 'insert_path': []})
    ident = {k: v for k, v in ref.items() if k != 'source_sha256'}
    sample_length = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
    return {'kind': 'polyline', 'closed': closed, 'points': [p[:2] for p in points],
            'source_refs': [ref], 'source_geometry': [spec],
            'source_length_mm': exact, 'sampled_length_mm': sample_length,
            'length_basis': 'analytic' if exact is not None else 'evaluated_curve_approximation',
            'source_elevation_mm': points[0][2], 'curve_chord_error_mm': chord_error_mm,
            '_sigs': [_signature({'source': ident, 'geometry': spec})]}


def compatibility_key(record):
    """Attributes which cannot be discarded when combining routes."""
    fields = ('layer', 'category', 'system', 'region_id', 'level', 'floor_id', 'elevation',
              'diameter', 'width_mm', 'height_mm', 'nominal_size', 'material', 'placement',
              'dimension_basis', 'overrides', 'geometry_mode', '_parse_opts', '_profile_rule')
    return json.dumps({k: record[k] for k in fields if k in record}, sort_keys=True, default=str)


def join_paths(records, endpoint_tolerance_mm=.001, gap_review_mm=10):
    """Join only coincident endpoints of compatible paths, stopping at branches.

    Interior intersections do not create nodes. Nearby, noncoincident endpoints
    are reported but never joined. The returned records retain all source refs.
    """
    if endpoint_tolerance_mm <= 0 or gap_review_mm < endpoint_tolerance_mm:
        raise ValueError('Invalid endpoint/gap tolerance')
    buckets = defaultdict(list)
    for rec in records:
        buckets[compatibility_key(rec)].append(rec)
    output = []; report = {'branchpoints': [], 'gaps': [], 'endpoints': [], 'source_paths': len(records)}
    for group_key in sorted(buckets):
        rows = sorted(buckets[group_key], key=lambda r: (tuple(r['points'][0]), tuple(r['points'][-1]),
                                                         tuple(r.get('_sigs', []))))
        nodes = []; cells = defaultdict(list); adjacency = defaultdict(list); ends = []
        tol = endpoint_tolerance_mm

        def node(point):
            cell = (math.floor(point[0] / tol), math.floor(point[1] / tol))
            candidates = []
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    candidates.extend(cells[(cell[0] + dx, cell[1] + dy)])
            hit = [i for i in candidates if math.dist(point, nodes[i]) <= tol]
            if hit:
                return min(hit)
            idx = len(nodes); nodes.append(list(point)); cells[cell].append(idx)
            return idx

        for i, rec in enumerate(rows):
            a, b = node(rec['points'][0]), node(rec['points'][-1])
            ends.append((a, b)); adjacency[a].append(i); adjacency[b].append(i)
        for n, links in adjacency.items():
            if len(links) > 2:
                report['branchpoints'].append({'point_mm': nodes[n], 'degree': len(links),
                                              'source_refs': [s for i in links for s in rows[i].get('source_refs', [])]})
        used = set()

        def walk(first, start_node):
            route = []; at = start_node; edge = first
            while edge not in used:
                used.add(edge)
                a, b = ends[edge]; rev = at == b
                route.append((edge, rev)); at = a if rev else b
                if len(adjacency[at]) != 2:
                    break
                candidates = [e for e in adjacency[at] if e not in used]
                if not candidates:
                    break
                edge = candidates[0]
            rec = copy.deepcopy(rows[route[0][0]])
            pts = []; refs = []; specs = []; sigs = []; exact = 0.; approximate = False
            for idx, rev in route:
                r = rows[idx]; rp = list(reversed(r['points'])) if rev else r['points']
                pts.extend(copy.deepcopy(rp[1:] if pts else rp))
                for s in r.get('source_refs', []):
                    sr = copy.deepcopy(s); sr['reverse'] = bool(sr.get('reverse', False)) ^ rev; refs.append(sr)
                specs.extend(copy.deepcopy(r.get('source_geometry', []))); sigs.extend(r.get('_sigs', []))
                if r.get('source_length_mm') is None:
                    approximate = True
                else:
                    exact += r['source_length_mm']
            rec.update(points=pts, closed=math.dist(pts[0], pts[-1]) <= tol, source_refs=refs,
                       source_geometry=specs, _sigs=sigs, joined_from=len(route),
                       source_length_mm=None if approximate else exact,
                       sampled_length_mm=sum(math.dist(a, b) for a, b in zip(pts, pts[1:])),
                       length_basis='evaluated_curve_approximation' if approximate else 'analytic')
            rec['path_topology'] = 'closed' if rec['closed'] else 'open'
            output.append(rec)

        for n in sorted(adjacency, key=lambda k: tuple(nodes[k])):
            if len(adjacency[n]) == 2:
                continue
            for edge in adjacency[n]:
                if edge not in used:
                    walk(edge, n)
        for edge in range(len(rows)):
            if edge not in used:
                walk(edge, ends[edge][0])
        terminals = [n for n in adjacency if len(adjacency[n]) == 1]
        report['endpoints'].extend({'point_mm': nodes[n], 'source_refs': rows[adjacency[n][0]].get('source_refs', [])}
                                   for n in terminals)
        near_cells = defaultdict(list)
        for n in sorted(terminals):
            cell = (math.floor(nodes[n][0] / gap_review_mm), math.floor(nodes[n][1] / gap_review_mm))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for other in near_cells[(cell[0] + dx, cell[1] + dy)]:
                        distance = math.dist(nodes[n], nodes[other])
                        if tol < distance <= gap_review_mm:
                            report['gaps'].append({'endpoints_mm': [nodes[other], nodes[n]],
                                                   'distance_mm': distance, 'repair_applied': False,
                                                   'source_refs': rows[adjacency[other][0]].get('source_refs', []) + rows[adjacency[n][0]].get('source_refs', [])})
            near_cells[cell].append(n)
    report['connected_paths'] = len(output)
    return output, report
