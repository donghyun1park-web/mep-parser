"""Read-only diagnostics around manual edits. Never snap or change geometry."""
import geom_contract as GC
from shapely.geometry import LineString, Point

GAP_TOL_MM = 50.0


def annotate_edit_diagnostics(elements, params=None):
    walls = elements.get('wall', [])
    shapes = []
    for r in walls:
        pts = r.get('centerline') or r.get('points') or []
        shapes.append(LineString(pts) if len(pts) >= 2 else None)
    for i, rec in enumerate(walls):
        if not rec.get('_edited'):
            continue
        findings = []
        line = shapes[i]
        if line is not None and not rec.get('closed'):
            z0, z1 = GC.z_range('wall', rec, params)
            for j, other in enumerate(walls):
                if i == j or shapes[j] is None:
                    continue
                a, b = GC.z_range('wall', other, params)
                if min(z1, b) <= max(z0, a):
                    continue
                if rec.get('level') and other.get('level') and rec['level'] != other['level']:
                    continue
                length = line.intersection(shapes[j]).length
                if length > 1:
                    findings.append({'code': 'wall_overlap', 'other': other.get('eid'),
                                     'length_mm': round(length, 3)})
                for endpoint, p in enumerate((line.coords[0], line.coords[-1])):
                    distance = Point(p).distance(shapes[j])
                    if 0.001 < distance <= GAP_TOL_MM:
                        findings.append({'code': 'endpoint_gap', 'other': other.get('eid'),
                                         'endpoint': endpoint, 'distance_mm': round(distance, 3)})
        _assign(rec, findings)
    for op in elements.get('opening', []):
        if not op.get('_edited'):
            continue
        findings = [] if op.get('wall_indices') else [{'code': 'opening_host_missing'}]
        _assign(op, findings)


def _assign(rec, findings):
    rec['edit_diagnostics'] = sorted(findings, key=lambda f: (f['code'], str(f.get('other')), f.get('endpoint', 0)))
    if findings:
        rec['needs_review'] = True
        rec.setdefault('review_reason', findings[0]['code'])
