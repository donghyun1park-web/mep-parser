"""Source-bound site profiles for deterministic ventilation and floor-heating DXF."""
from collections import Counter, defaultdict
from pathlib import Path
import copy
import hashlib
import math
import re

import ezdxf
from ezdxf import units
import geom_contract as GC
from mep_paths import extract_curve, join_paths, _signature

CURVES = {'LINE', 'ARC', 'CIRCLE', 'LWPOLYLINE', 'POLYLINE', 'SPLINE', 'ELLIPSE'}
MAX_ENTITIES = 100000


def _number(value, label, positive=False):
    if isinstance(value, bool):
        raise ValueError(label + ': boolean is not a number')
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(label + ': numeric value required') from None
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(label + ': finite ' + ('positive ' if positive else '') + 'number required')
    return value


def scale_to_mm(doc, explicit=None):
    if explicit is not None:
        return _number(explicit, 'unit_scale_to_mm', True)
    code = int(doc.header.get('$INSUNITS', 0))
    if code == 0:
        return None
    try:
        return float(units.conversion_factor(code, units.MM))
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def validate_profile(profile, source_sha256=None):
    """Validate without changing the caller's data. Hash mismatch requires rebinding."""
    if not isinstance(profile, dict) or profile.get('version') != 1:
        raise ValueError('MEP profile version 1 is required')
    out = copy.deepcopy(profile)
    source_hash = out.get('source_sha256', '')
    if not isinstance(source_hash, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', source_hash):
        raise ValueError('A source_sha256 binding is required')
    out['source_sha256'] = source_hash.lower()
    if source_sha256 and source_hash.lower() != source_sha256.lower():
        raise ValueError('Source SHA256 hash changed; review and rebind the MEP profile')
    if out.get('unit_scale_to_mm') is not None:
        out['unit_scale_to_mm'] = _number(out['unit_scale_to_mm'], 'unit_scale_to_mm', True)
    for key, default in [('curve_chord_error_mm', .25), ('endpoint_tolerance_mm', .001), ('gap_review_mm', 10)]:
        out[key] = _number(out.get(key, default), key, True)
    if out['endpoint_tolerance_mm'] > .1:
        raise ValueError('endpoint_tolerance_mm must not exceed 0.1 mm; larger gaps need review')
    if out['gap_review_mm'] < out['endpoint_tolerance_mm']:
        raise ValueError('gap_review_mm must be at least endpoint_tolerance_mm')
    region = out.get('region')
    if region is not None:
        if not isinstance(region, dict) or not isinstance(region.get('id'), str) or not region['id'].strip():
            raise ValueError('Region id is required')
        bounds = region.get('bounds_mm')
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
            raise ValueError('Region bounds_mm must be [xmin,ymin,xmax,ymax]')
        bounds = [_number(v, 'region.bounds_mm') for v in bounds]
        if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
            raise ValueError('Region bounds must have positive area')
        region['bounds_mm'] = bounds
    levels = out.setdefault('levels', {})
    if not isinstance(levels, dict):
        raise ValueError('levels must be an object')
    levels.setdefault('structural_slab_top_mm', 0.)
    for key in ('structural_slab_top_mm', 'floor_to_floor_mm', 'slab_thickness_mm'):
        if key in levels:
            levels[key] = _number(levels[key], key, key != 'structural_slab_top_mm')
    layers = out.setdefault('floor_layers', [])
    if not isinstance(layers, list):
        raise ValueError('floor_layers must be a list')
    seen = set()
    for row in layers:
        role = row.get('role')
        if role not in ('impact_insulation', 'foamed_concrete', 'screed') or role in seen:
            raise ValueError('Floor layer roles must be supported and unique')
        seen.add(role)
        row['thickness_mm'] = _number(row.get('thickness_mm'), 'floor layer thickness', True)
    rules = out.get('layers')
    if not isinstance(rules, list) or not rules:
        raise ValueError('At least one MEP layer mapping is required')
    for row in rules:
        if not isinstance(row, dict) or not isinstance(row.get('pattern'), str) or not row['pattern']:
            raise ValueError('Layer pattern is required')
        try:
            re.compile(row['pattern'])
        except re.error as exc:
            raise ValueError('Invalid layer pattern: ' + str(exc)) from None
        cat = row.get('category')
        if cat not in ('pipe', 'duct', 'tray'):
            raise ValueError('MEP profile category must be pipe, duct or tray')
        if not isinstance(row.get('system'), str) or not row['system'].strip():
            raise ValueError('A MEP system name is required')
        row.setdefault('representation', 'centerline')
        if row['representation'] not in ('centerline', 'outline') or (row['representation'] == 'outline' and cat == 'pipe'):
            raise ValueError('Outline representation is available for duct/tray only')
        row.setdefault('dimension_basis', 'assumed')
        if row['dimension_basis'] not in ('user', 'assumed', 'annotation'):
            raise ValueError('Unsupported dimension_basis')
        row.setdefault('placement', 'source')
        if row['placement'] not in ('source', 'center', 'slab_soffit', 'foam_top'):
            raise ValueError('Unsupported placement')
        for key in ('diameter_mm', 'width_mm', 'height_mm'):
            if row.get(key) is not None:
                row[key] = _number(row[key], key, True)
        required = ('diameter_mm',) if cat == 'pipe' else (('height_mm',) if row['representation'] == 'outline' else ('width_mm', 'height_mm'))
        if any(row.get(k) is None for k in required):
            raise ValueError('Physical outside dimensions required: ' + ', '.join(required))
        if row.get('color') is not None:
            color = row['color']
            if isinstance(color, bool) or not isinstance(color, int) or not 1 <= color <= 255:
                raise ValueError('color must be an effective ACI integer from 1 to 255')
        if row.get('linetype') is not None and (not isinstance(row['linetype'], str) or not row['linetype'].strip()):
            raise ValueError('linetype must be a nonempty name')
        if row['placement'] == 'center':
            row['center_elevation_mm'] = _number(row.get('center_elevation_mm'), 'center_elevation_mm')
        height = row['diameter_mm'] if cat == 'pipe' else row['height_mm']
        GC.mep_elevation(row['placement'], height, levels, layers,
                         row.get('center_elevation_mm', 0.))
    return out


def _source_entity(entity):
    current = entity
    for _ in range(10):
        source = current.source_of_copy
        if source is None:
            return current
        current = source
    return current


def _expanded(doc, issues):
    """Transformed INSERT/MINSERT leaves plus effective display attributes and identity."""
    budget = [MAX_ENTITIES]

    def visit(entity, path=(), inherited=None, depth=0):
        budget[0] -= 1
        if budget[0] < 0:
            raise ValueError('DXF entity budget exceeded')
        inherited = inherited or {'layer': '0', 'color': 7, 'linetype': 'CONTINUOUS'}
        raw_layer = str(entity.dxf.get('layer', '0'))
        layer = inherited['layer'] if raw_layer == '0' and path else raw_layer
        table = doc.layers.get(layer) if layer in doc.layers else None
        color = int(entity.dxf.get('color', 256))
        if color == 0:
            color = inherited['color']
        elif color == 256:
            color = abs(int(table.dxf.color)) if table is not None else 7
        linetype = str(entity.dxf.get('linetype', 'BYLAYER')).upper()
        if linetype == 'BYBLOCK':
            linetype = inherited['linetype']
        elif linetype == 'BYLAYER':
            linetype = str(table.dxf.linetype).upper() if table is not None else 'CONTINUOUS'
        nonuniform = bool(inherited.get('_nonuniform', False))
        if entity.dxftype() == 'INSERT':
            scales = [abs(float(entity.dxf.get(k, 1))) for k in ('xscale', 'yscale')]
            nonuniform = nonuniform or max(scales) - min(scales) > 1e-9
        effective = {'layer': layer, 'color': color, 'linetype': linetype, '_nonuniform': nonuniform}
        source = _source_entity(entity)
        ref = {'handle': str(source.dxf.get('handle', '') or ''), 'type': source.dxftype(),
               'insert_path': copy.deepcopy(list(path))}
        if entity.dxftype() != 'INSERT':
            if (source.dxftype() in ('ARC', 'CIRCLE') and entity.dxftype() != source.dxftype()) or (nonuniform and entity.dxftype() in ('ARC', 'CIRCLE', 'ELLIPSE', 'SPLINE')):
                issues.append({'code': 'NONUNIFORM_CURVE_TRANSFORM', 'severity': 'warning',
                               'reason': 'Nonuniformly scaled circular source requires review', 'source_refs': [ref],
                               'layer': layer})
                yield None, ref, effective
            else:
                yield entity, ref, effective
            return
        if depth >= 8:
            issues.append({'code': 'INSERT_DEPTH', 'severity': 'warning', 'reason': 'Nested INSERT depth exceeds 8',
                           'source_refs': [ref], 'layer': layer})
            yield None, ref, effective
            return
        instances = list(entity.multi_insert()) if int(getattr(entity, 'mcount', 1) or 1) > 1 else [entity]
        for index, instance in enumerate(instances):
            part = {'handle': ref['handle'], 'block': str(entity.dxf.name), 'array_index': index}
            child_path = tuple(path) + (part,)
            skipped = []
            try:
                for child in instance.virtual_entities(skipped_entity_callback=lambda e, why: skipped.append((e, why))):
                    yield from visit(child, child_path, effective, depth + 1)
                for child, reason in skipped:
                    child_ref = {'handle': str(child.dxf.get('handle', '') or ''), 'type': child.dxftype(),
                                 'insert_path': list(child_path)}
                    issues.append({'code': 'INSERT_TRANSFORM', 'severity': 'warning', 'reason': str(reason),
                                   'source_refs': [child_ref], 'layer': layer})
                    yield None, child_ref, effective
            except (ValueError, TypeError, ezdxf.DXFError) as exc:
                issues.append({'code': 'INSERT_TRANSFORM', 'severity': 'warning', 'reason': str(exc),
                               'source_refs': [ref], 'layer': layer})
                yield None, ref, effective
    for entity in doc.modelspace():
        yield from visit(entity)


def _bounds(points):
    if not points:
        return None
    return [min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points)]


def _union_bounds(a, b):
    if a is None:
        return list(b) if b is not None else None
    if b is None:
        return list(a)
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def _region_candidates(boxes):
    """Whitespace-separated candidates only, never an automatic plan selection."""
    if not boxes:
        return []
    groups = [boxes]
    for axis in (0, 1):
        split = []
        for group in groups:
            ordered = sorted(group, key=lambda b: b[axis]); chunks = []; chunk = []
            high = None
            sizes = sorted(max(b[2] - b[0], b[3] - b[1]) for b in group)
            threshold = max(2000., sizes[len(sizes) // 2] * 4)
            for box in ordered:
                if high is not None and box[axis] - high > threshold:
                    chunks.append(chunk); chunk = []; high = None
                chunk.append(box); high = max(high if high is not None else box[axis + 2], box[axis + 2])
            chunks.append(chunk); split.extend(chunks)
        groups = split
    result = []
    for i, group in enumerate(groups):
        bb = None
        for box in group:
            bb = _union_bounds(bb, box)
        if bb[0] == bb[2]:
            bb[0] -= 1; bb[2] += 1
        if bb[1] == bb[3]:
            bb[1] -= 1; bb[3] += 1
        result.append({'id': 'region_' + str(i + 1), 'label': 'Drawing region ' + str(i + 1),
                       'bounds_mm': bb, 'entity_count': len(group), 'selection_status': 'candidate_only'})
    return result


def inspect_mep_source(dxf_path, unit_scale_to_mm=None):
    path = Path(dxf_path); source_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    doc = ezdxf.readfile(path); scale = scale_to_mm(doc, unit_scale_to_mm); issues = []; layers = {}; boxes = []
    for entity, ref, appearance in _expanded(doc, issues):
        name = appearance['layer']
        row = layers.setdefault(name, {'name': name, 'count': 0, 'entity_types': Counter(),
                                       'colors': set(), 'linetypes': set(), 'bounds_mm': None})
        row['count'] += 1; row['entity_types'][ref['type']] += 1
        row['colors'].add(appearance['color']); row['linetypes'].add(appearance['linetype'])
        if scale is not None and entity is not None and entity.dxftype() in CURVES:
            try:
                rec = extract_curve(entity, scale, 2., ref, max_points=10000)
                bb = _bounds(rec['points']); boxes.append(bb)
                row['bounds_mm'] = _union_bounds(row['bounds_mm'], bb)
            except ValueError as exc:
                issues.append({'code': 'SOURCE_CURVE', 'severity': 'warning', 'reason': str(exc), 'source_refs': [ref]})
    all_bounds = None
    for row in layers.values():
        row['entity_types'] = dict(row['entity_types']); row['colors'] = sorted(row['colors']); row['linetypes'] = sorted(row['linetypes'])
        all_bounds = _union_bounds(all_bounds, row['bounds_mm'])
    warnings = ['Unknown DXF units; an explicit unit_scale_to_mm is required'] if scale is None else []
    return {'source': str(path.resolve()), 'source_sha256': source_hash, 'INSUNITS': int(doc.units),
            'insunits': int(doc.units), 'scale_to_mm': scale, 'bounds_mm': all_bounds,
            'layers': [layers[k] for k in sorted(layers)], 'regions': _region_candidates(boxes),
            'warnings': warnings, 'issues': issues, 'scope': 'Read-only inventory; regions and layer roles require selection'}


def _region_status(bounds, region):
    if region is None or bounds is None:
        return 'inside'
    x0, y0, x1, y1 = region['bounds_mm']; a, b, c, d = bounds
    if c < x0 or a > x1 or d < y0 or b > y1:
        return 'outside'
    if a >= x0 - 1e-7 and c <= x1 + 1e-7 and b >= y0 - 1e-7 and d <= y1 + 1e-7:
        return 'inside'
    return 'crossing'


def _assign(record, row, index, profile):
    rec = copy.deepcopy(record); cat = row['category']
    rec.update(category=cat, system=row['system'], material=row.get('material', ''),
               nominal_size=row.get('nominal_size', ''), dimension_basis=row['dimension_basis'],
               dimension_status='assumed' if row['dimension_basis'] == 'assumed' else 'specified',
               placement=row['placement'], _profile_rule=index,
               region_id=(profile.get('region') or {}).get('id', 'whole_drawing'))
    if cat == 'pipe':
        rec['diameter'] = row['diameter_mm']; height = rec['diameter']
    else:
        if row.get('width_mm') is not None:
            rec['width_mm'] = row['width_mm']
        rec['height_mm'] = row['height_mm']; height = rec['height_mm']
    rec['elevation'] = GC.mep_elevation(row['placement'], height, profile['levels'], profile['floor_layers'],
                                      rec.get('source_elevation_mm', 0.) if row['placement'] == 'source' else row.get('center_elevation_mm'))
    rec['elevation_source'] = 'source' if row['placement'] == 'source' else 'profile'
    if row.get('material'):
        rec.setdefault('overrides', {})['material'] = row['material']
    if row['dimension_basis'] == 'assumed':
        rec['needs_review'] = True; rec['review_reason'] = 'mep_dimensions_assumed'
        rec['dims_assumed'] = ['diameter'] if cat == 'pipe' else ['width_mm', 'height_mm']
    return rec


def _outlines(records, issues, endpoint_tolerance_mm):
    from shapely.geometry import LineString, Polygon
    from shapely.ops import polygonize, unary_union
    from shapely.strtree import STRtree
    nodes = []; cells = defaultdict(list); deviations = []
    tolerance = endpoint_tolerance_mm

    def coincident(point):
        key = (math.floor(point[0] / tolerance), math.floor(point[1] / tolerance))
        hits = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                hits.extend(i for i in cells[(key[0] + dx, key[1] + dy)] if math.dist(point, nodes[i]) <= tolerance)
        if hits:
            target = nodes[min(hits)]
            deviations.append(math.dist(point, target))
            return list(target)
        cells[key].append(len(nodes)); nodes.append(list(point))
        return list(point)

    lines = []
    for record in records:
        points = copy.deepcopy(record['points'])
        points[0] = coincident(points[0]); points[-1] = coincident(points[-1])
        lines.append(LineString(points))
    polygons = list(polygonize(unary_union(lines)))
    # polygonize returns both an annulus and the face enclosed by its inner ring.
    # Treat explicitly nested outlines as holes rather than filling them twice.
    holes = [Polygon(ring) for p in polygons for ring in p.interiors]
    polygons = [p for p in polygons if not any(h.covers(p) for h in holes)]
    output = []; covered = set(); tree = STRtree(lines)
    for polygon in polygons:
        indices = sorted(int(i) for i in tree.query(polygon.boundary)
                         if polygon.boundary.intersection(lines[int(i)]).length > 1e-7)
        if not indices:
            continue
        covered.update(indices); rec = copy.deepcopy(records[indices[0]])
        rec.update(points=[list(p) for p in polygon.exterior.coords[:-1]],
                   holes=[[list(p) for p in ring.coords[:-1]] for ring in polygon.interiors],
                   closed=True, geometry_mode='footprint',
                   source_refs=[s for i in indices for s in records[i]['source_refs']],
                   source_geometry=[s for i in indices for s in records[i]['source_geometry']],
                   _sigs=[s for i in indices for s in records[i]['_sigs']])
        rec['source_layers'] = sorted({records[i]['layer'] for i in indices})
        rec['profile_rule_indices'] = sorted({records[i]['_profile_rule'] for i in indices})
        rec['boundary_join_tolerance_mm'] = tolerance
        rec['boundary_max_adjustment_mm'] = max(deviations, default=0.)
        # 외곽선 레코드는 경로가 아니라 면이다 — 첫 경계선의 길이·경로를 물려받지 않는다.
        for key in ('source_length_mm', 'sampled_length_mm', 'length_basis', 'path3d', 'path3d_basis'):
            rec.pop(key, None)
        rec['footprint_area_mm2'] = polygon.area
        output.append(rec)
    boundary = unary_union([polygon.boundary for polygon in polygons]).buffer(tolerance)
    partial = set()
    for i in covered:
        missing_length = lines[i].difference(boundary).length
        if missing_length > tolerance:
            partial.add(i)
            issues.append({'code': 'OUTLINE_PARTIAL', 'severity': 'warning',
                           'reason': 'Only part of the original outline bounds a closed face',
                           'unrepresented_length_mm': missing_length, 'source_refs': records[i]['source_refs']})
    for i, r in enumerate(records):
        if i not in covered:
            issues.append({'code': 'OUTLINE_OPEN', 'severity': 'warning',
                           'reason': 'Selected outline does not bound a closed face; no geometry invented',
                           'source_refs': r['source_refs']})
    return output, len(records) - len(covered) + len(partial)


def apply_mep_profile(doc, result, profile):
    source_hash = hashlib.sha256(Path(result['source']).read_bytes()).hexdigest()
    profile = validate_profile(profile, source_hash)
    scale = scale_to_mm(doc, profile.get('unit_scale_to_mm'))
    if scale is None:
        raise ValueError('Unknown DXF units; provide unit_scale_to_mm')
    issues = []; grouped = defaultdict(list); selected = represented = omitted = outside = 0
    rules = [(re.compile(r['pattern'], re.I), r) for r in profile['layers']]
    for entity, ref, appearance in _expanded(doc, issues):
        match = next(((i, row) for i, (pattern, row) in enumerate(rules)
                      if pattern.search(appearance['layer']) and
                      (row.get('color') is None or row['color'] == appearance['color']) and
                      (row.get('linetype') is None or row['linetype'].upper() == appearance['linetype'])), None)
        if match is None:
            continue
        index, row = match
        ref['source_sha256'] = source_hash
        if entity is None:
            selected += 1; omitted += 1; continue
        # Text, dimensions and symbols in a mapped line layer are explicit omissions.
        try:
            rec = extract_curve(entity, scale, profile['curve_chord_error_mm'], ref)
        except (ValueError, ezdxf.DXFError) as exc:
            selected += 1; omitted += 1
            issues.append({'code': 'UNSUPPORTED_SOURCE', 'severity': 'warning', 'reason': str(exc), 'source_refs': [ref]})
            continue
        state = _region_status(_bounds(rec['points']), profile.get('region'))
        if state == 'outside':
            outside += 1; continue
        selected += 1
        if state == 'crossing':
            omitted += 1
            issues.append({'code': 'REGION_CROSSING', 'severity': 'warning',
                           'reason': 'Source crosses selected region; omitted without clipping', 'source_refs': [ref],
                           'bounds_mm': _bounds(rec['points'])})
            continue
        rec['layer'] = appearance['layer']
        grouped[index].append(_assign(rec, row, index, profile))
    elements = result['elements']
    for category in ('pipe', 'duct', 'tray'):
        elements[category] = []
    topology = {'branchpoints': [], 'gaps': [], 'endpoints': [], 'source_paths': 0, 'connected_paths': 0}
    build_groups = []
    outline_groups = {}
    for index, records in grouped.items():
        if profile['layers'][index]['representation'] != 'outline':
            build_groups.append((index, records))
            continue
        for rec in records:
            key = tuple(rec.get(k) for k in ('category', 'system', 'height_mm', 'elevation',
                                             'material', 'placement', 'dimension_basis', 'region_id'))
            if key not in outline_groups:
                outline_groups[key] = (index, [])
            outline_groups[key][1].append(rec)
    build_groups.extend(outline_groups.values())
    for index, records in build_groups:
        row = profile['layers'][index]
        if row['representation'] == 'outline':
            built, lost = _outlines(records, issues, profile['endpoint_tolerance_mm']); omitted += lost; represented += len(records) - lost
        else:
            built, report = join_paths(records, profile['endpoint_tolerance_mm'], profile['gap_review_mm'])
            represented += len(records)
            for key in topology:
                if isinstance(topology[key], list):
                    topology[key].extend(report[key])
                else:
                    topology[key] += report[key]
            for rec in built:
                rec['geometry_mode'] = 'centerline'
                refs = {(_signature({k: v for k, v in s.items() if k != 'reverse'})) for s in rec['source_refs']}
                if any(refs.intersection(_signature({k: v for k, v in s.items() if k != 'reverse'}) for s in gap['source_refs']) for gap in report['gaps']):
                    rec['needs_review'] = True; rec['review_reason'] = 'mep_source_gap'
        elements[row['category']].extend(built)
    # Apply the same selected region to legacy architectural results; never clip.
    excluded_categories = Counter()
    for category, records in list(elements.items()):
        if category in ('pipe', 'duct', 'tray'):
            continue
        keep = []
        for rec in records:
            points = rec.get('points') or []
            if rec.get('kind') == 'circle':
                x, y = rec['center']; radius = rec['radius']; points = [[x - radius, y - radius], [x + radius, y + radius]]
            state = _region_status(_bounds(points), profile.get('region'))
            if state == 'inside':
                keep.append(rec)
            else:
                excluded_categories[category] += 1
                if state == 'crossing':
                    issues.append({'code': 'REGION_CROSSING', 'severity': 'warning',
                                   'reason': 'Architectural element crosses region; omitted without clipping',
                                   'category': category, 'layer': rec.get('layer'), 'bounds_mm': _bounds(points)})
        elements[category] = keep
    for gap in topology['gaps']:
        issues.append(dict(gap, code='SOURCE_GAP', severity='warning', reason='Nearby source endpoints remain disconnected'))
    for branch in topology['branchpoints']:
        issues.append(dict(branch, code='SOURCE_BRANCH', severity='warning', reason='Branch preserved as separate paths; no arbitrary circuit route'))
    coverage = {'selected': selected, 'represented': represented, 'omitted': omitted,
                'outside_region': outside, 'complete': omitted == 0, 'basis': 'source entity instances'}
    scope = {'region': profile.get('region'), 'excluded_architecture': dict(excluded_categories),
             'coordinates': 'WCS millimetres; analytic source retained; curve and coincidence tolerances reported',
             'source_gap_policy': 'report_only_no_repair', 'design_approved': False}
    result['mep_profile'] = profile
    result['mep_diagnostics'] = {'issues': issues, 'paths': topology, 'gaps': topology['gaps'],
                                 'source_coverage': coverage, 'scope': scope}
    result['source_coverage'] = coverage
    result['scale_applied'] = scale
    if omitted:
        result.setdefault('warnings', []).append(f'MEP selected source omitted: {omitted}; see mep_diagnostics')
    return result
