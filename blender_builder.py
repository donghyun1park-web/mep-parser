"""Deterministic geometry.json -> portable payload -> editable Blender review model.

The preparation stage runs in the application's Python. The native stage uses
only Blender's bundled Python and never interprets DXF, profile text, or AI code.
"""
import hashlib
import json
import math
import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))


def _number(value, label, positive=False):
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError(f"Invalid finite {'positive ' if positive else ''}{label}: {value}")
    return number


def _xy(points):
    return [[_number(p[0], 'x'), _number(p[1], 'y')] for p in points]


def _mesh(poly, z0, z1, origin):
    from shapely import constrained_delaunay_triangles
    from shapely.geometry.polygon import orient
    from blender_verify import validate_mesh_payload
    if poly.is_empty or not poly.is_valid or poly.area <= 0 or z1 <= z0:
        raise ValueError('Invalid or empty footprint/height; no automatic geometry repair')
    parts = [poly] if poly.geom_type == 'Polygon' else list(poly.geoms)
    vertices, faces = [], []
    for part in parts:
        if part.geom_type != 'Polygon':
            raise ValueError('Non-polygon footprint')
        part = orient(part, sign=1)
        rings = [list(part.exterior.coords)[:-1]] + [list(r.coords)[:-1] for r in part.interiors]
        flat = [p for ring in rings for p in ring]
        lookup = {tuple(p): i for i, p in enumerate(flat)}
        offset, n = len(vertices), len(flat)
        vertices += [[(x-origin[0])/1000, (y-origin[1])/1000, (z-origin[2])/1000]
                     for z in (z0, z1) for x, y in flat]
        for triangle in constrained_delaunay_triangles(part).geoms:
            ids = [lookup[tuple(p)] + offset for p in list(orient(triangle, sign=1).exterior.coords)[:-1]]
            faces.extend([list(reversed(ids)), [i+n for i in ids]])
        ring_offset = offset
        for ring in rings:
            for i in range(len(ring)):
                a, b = ring_offset+i, ring_offset+(i+1) % len(ring)
                faces.append([a, b, b+n, a+n])
            ring_offset += len(ring)
    result = {'kind': 'mesh', 'vertices_m': vertices, 'faces': faces,
              'expected_volume_m3': poly.area * (z1-z0) / 1e9}
    validate_mesh_payload(result)
    return result


def prepare_payload(data):
    """Prepare source-bound curves/closed meshes in metres; never mutate data."""
    import geom_contract as GC
    from shapely.geometry import Polygon, LineString, Point
    if data.get('units') != 'mm':
        raise ValueError('geometry.json must declare normalized units=mm')
    params, elements = data.get('params') or {}, data.get('elements') or {}
    diagnostics, objects, seen, coords = [], [], set(), []
    layouts = {rec['layout_id'] for recs in elements.values() for rec in recs if rec.get('layout_id') is not None}
    if len(layouts) > 1:
        raise ValueError('Multiple source layouts cannot share one export; select a layout first')
    for cat, recs in elements.items():
        for rec in recs:
            eid = rec.get('eid')
            if not isinstance(eid, str) or not eid or eid in seen:
                raise ValueError(f'Missing or duplicate EID: {eid}')
            seen.add(eid)
            coords.extend(_xy(rec.get('points') or rec.get('centerline') or []))
            if rec.get('center') is not None:
                coords.extend(_xy([rec['center']]))
    if not coords:
        raise ValueError('No source coordinates to model')
    origin = [(min(p[i] for p in coords)+max(p[i] for p in coords))/2 for i in (0, 1)] + [0.0]
    profile = data.get('mep_profile') or {}
    source_slabs = []
    for cat, recs in elements.items():
        for rec in recs:
            eid = rec['eid']
            if cat in ('zone', 'opening'):
                diagnostics.append({'code': 'reference_not_solid', 'eid': eid, 'category': cat,
                    'message': 'Zone/opening is retained in input; no unverified Boolean opening is cut.'})
                continue
            if cat not in GC.Z_DATUM:
                raise ValueError(f'Unsupported category: {cat}')
            z0, z1 = [_number(v, 'z') for v in GC.z_range(cat, rec, params)]
            if z1 <= z0:
                raise ValueError(f'Non-positive height for {eid}')
            points = _xy(rec.get('centerline') or rec.get('points') or [])
            base = {'eid': eid, 'source_eid': eid, 'category': cat,
                'source_refs': rec.get('source_refs') or rec.get('source_handles') or [],
                'source_length_mm': rec.get('source_length_mm'),
                'sampled_length_mm': rec.get('sampled_length_mm'),
                'needs_review': bool(rec.get('needs_review')), 'review_reason': rec.get('review_reason'),
                'dimension_basis': rec.get('dimension_basis'), 'z_bounds_mm': [z0, z1],
                'source_points_mm': points, 'covered_only': False}
            for key in ('system', 'circuit_id', 'source_layer', 'source_layers', 'layer', 'region_id',
                        'layout_id', 'assumptions', 'nominal_size'):
                if key in rec: base[key] = rec[key]
            material = (rec.get('overrides') or {}).get('material', rec.get('material'))
            if material is not None: base['material'] = material
            if cat in ('pipe', 'duct', 'tray'):
                dims = GC.mep_dimensions(cat, rec, params)
                for key, value in dims.items():
                    _number(value, key, True)
                declared = rec.get('overrides') or {}
                for key in dims:
                    alias = {'width_mm': 'width', 'height_mm': 'height'}.get(key, key)
                    if key not in declared and alias not in declared and rec.get(key) is None and (params.get(cat) or {}).get(key) is None:
                        diagnostics.append({'code': 'dimension_default_used', 'eid': eid, 'field': key,
                            'value': dims[key], 'message': 'Existing contract default; specification is not confirmed.'})
                base['dimensions_mm'] = dims
            if cat == 'pipe':
                if len(points) < 2 or sum(math.dist(a, b) for a, b in zip(points, points[1:])) <= 0:
                    raise ValueError(f'Empty pipe path: {eid}')
                if rec.get('closed') and points[-1] != points[0]:
                    points = points + [points[0]]
                base.update(kind='curve', radius_m=dims['diameter']/2000,
                    points_m=[[(p[0]-origin[0])/1000, (p[1]-origin[1])/1000, (z0+z1)/2000] for p in points])
                objects.append(base)
                continue
            if rec.get('kind') == 'circle':
                center = _xy([rec['center']])[0]
                radius = _number(rec['radius'], 'circle radius', True)
                poly = Point(center).buffer(radius, quad_segs=32)
                diagnostics.append({'code': 'circle_tessellated', 'eid': eid, 'segments': 128})
            elif rec.get('geometry_mode') == 'footprint' or (cat not in ('duct', 'tray') and rec.get('closed')):
                poly = Polygon(_xy(rec.get('points') or []), [_xy(r) for r in rec.get('holes') or []])
            else:
                if len(points) < 2:
                    raise ValueError(f'No usable path/footprint: {eid}')
                if cat in ('duct', 'tray') and rec.get('closed') and points[-1] != points[0]:
                    points = points + [points[0]]
                width = dims['width_mm'] if cat in ('duct', 'tray') else GC.width_of(rec, params, cat)
                _number(width, 'width', True)
                poly = LineString(points).buffer(width/2, cap_style=2, join_style=2, mitre_limit=5)
                base['sweep_width_mm'] = width
                base['geometry_method'] = 'continuous_plan_mitre_sweep'
            base.update(_mesh(poly, z0, z1, origin))
            objects.append(base)
            if cat == 'slab' and rec.get('closed'):
                source_slabs.append((rec, poly, z1))
    layers = profile.get('floor_layers') or []
    if not source_slabs:
        diagnostics.append({'code': 'floor_footprint_not_available',
            'message': 'No closed source slab footprint. No floor or building was invented from MEP bounds.'})
    elif layers:
        for rec, poly, slab_top in source_slabs:
            z = slab_top
            for index, layer in enumerate(layers):
                thickness = _number(layer['thickness_mm'], 'floor layer thickness', True)
                role = layer.get('role') or layer.get('name') or f'layer_{index+1}'
                name = layer.get('name') or role
                obj = {'eid': f"{rec['eid']}:floor:{index}:{role}", 'source_eid': rec['eid'],
                    'category': 'floor_layer', 'name': name, 'floor_role': role,
                    'source_refs': rec.get('source_refs') or rec.get('source_handles') or [],
                    'z_bounds_mm': [z, z+thickness], 'covered_only': role == 'screed' or index == len(layers)-1,
                    'dimension_basis': layer.get('dimension_basis', profile.get('assumptions')),
                    'needs_review': True}
                obj.update(_mesh(poly, z, z+thickness, origin)); objects.append(obj); z += thickness
        diagnostics.append({'code': 'floor_layers_review_envelopes',
            'message': 'Layers follow source slab footprints. Wet-area details and pipe-volume subtraction are not evaluated.'})
    if not objects:
        raise ValueError('No modelable source objects')
    return {'schema': 'mep.blender_payload.v1', 'origin_mm': origin, 'objects': objects,
        'source_eids': sorted(seen), 'diagnostics': diagnostics, 'mep_profile': profile,
        'units': 'metres', 'source_units': 'mm', 'scope': 'drawing_based_review_not_design_validation'}


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def native_build(payload_path, out_dir, run_id, input_sha256):
    """Called only inside Blender. Host dependencies are deliberately not imported."""
    import bpy
    from mathutils import Vector
    payload_path, out_dir = Path(payload_path), Path(out_dir)
    data = json.loads(payload_path.read_text(encoding='utf-8'))
    from blender_verify import validate_payload, METADATA_KEYS
    validate_payload(data)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    exposed = bpy.context.scene; exposed.name = 'MEP_EXPOSED'
    covered = bpy.data.scenes.new('MEP_COVERED')
    palette = {'pipe': (.92,.27,.10,1), 'duct': (.05,.39,.72,1), 'tray': (.52,.32,.72,1),
        'wall': (.78,.77,.71,1), 'slab': (.48,.49,.51,1), 'floor_layer': (.71,.67,.49,1),
        'column': (.65,.66,.66,1), 'beam': (.66,.68,.69,1), 'equipment': (.12,.55,.47,1)}
    materials = {}
    for cat, color in palette.items():
        mat = bpy.data.materials.new(cat); mat.diffuse_color = color; mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get('Principled BSDF'); bsdf.inputs['Base Color'].default_value = color
        bsdf.inputs['Roughness'].default_value = .56; materials[cat] = mat
    for spec in data['objects']:
        name = spec['eid']
        if spec['kind'] == 'curve':
            item = bpy.data.curves.new(name, 'CURVE'); item.dimensions = '3D'
            item.bevel_depth = spec['radius_m']; item.bevel_resolution = 4; item.use_fill_caps = True
            spline = item.splines.new('POLY'); spline.points.add(len(spec['points_m'])-1)
            for point, xyz in zip(spline.points, spec['points_m']): point.co = (*xyz, 1)
        else:
            item = bpy.data.meshes.new(name)
            item.from_pydata(spec['vertices_m'], [], spec['faces']); item.update()
        ob = bpy.data.objects.new(name, item); ob.data.materials.append(materials[spec['category']])
        for key in METADATA_KEYS:
            value = spec.get(key)
            if value is not None: ob[key] = value if isinstance(value, (str, bool, int, float)) else json.dumps(value, ensure_ascii=False)
        ob['source_refs'] = json.dumps(spec.get('source_refs') or [], ensure_ascii=False, sort_keys=True)
        ob['input_sha256'] = input_sha256
        ob['z_bounds_mm'] = spec['z_bounds_mm']
        covered.collection.objects.link(ob)
        if not spec.get('covered_only'): exposed.collection.objects.link(ob)
    for scene in (exposed, covered):
        scene.unit_settings.system = 'METRIC'; scene.unit_settings.scale_length = 1
        scene.unit_settings.length_unit = 'MILLIMETERS'
        scene['input_sha256'] = input_sha256; scene['payload_sha256'] = _hash(payload_path)
        scene['run_id'] = run_id; scene['origin_mm'] = data['origin_mm']
        scene['scope'] = data['scope']
        scene.world = bpy.data.worlds.new(scene.name+'_World'); scene.world.color = (.7,.7,.7)
    # Camera framing uses actual built geometry; it does not create physical envelopes.
    bounds = [Vector(p) for s in data['objects'] for p in s.get('vertices_m', s.get('points_m', []))]
    lo = Vector([min(p[i] for p in bounds) for i in range(3)])
    hi = Vector([max(p[i] for p in bounds) for i in range(3)])
    target = (lo+hi)/2; extent = max((hi-lo).length, 1)
    for scene in (exposed, covered):
        cam_data = bpy.data.cameras.new(scene.name+'_Camera'); cam = bpy.data.objects.new(cam_data.name, cam_data)
        scene.collection.objects.link(cam); cam.location = target+Vector((.7,-1,1))*extent
        cam.rotation_euler = (target-cam.location).to_track_quat('-Z','Y').to_euler()
        cam_data.type = 'ORTHO'; cam_data.ortho_scale = extent*1.15; cam_data.clip_end = extent*10
        scene.camera = cam
        for screen in bpy.data.screens:
            for area in screen.areas:
                if area.type == 'VIEW_3D':
                    area.spaces.active.region_3d.view_distance = extent
                    area.spaces.active.region_3d.view_location = target
                    area.spaces.active.clip_end = extent*20
    bpy.context.window.scene = exposed
    bpy.ops.wm.save_as_mainfile(filepath=str(out_dir/'model.blend'))
    bpy.ops.object.select_all(action='DESELECT')
    for ob in exposed.objects:
        if ob.get('eid'): ob.select_set(True)
    bpy.ops.export_scene.gltf(filepath=str(out_dir/'model.glb'), export_format='GLB',
        use_selection=True, use_active_scene=True, export_extras=True, export_cameras=False, export_lights=False)
    return {'blend': str(out_dir/'model.blend'), 'glb': str(out_dir/'model.glb')}


if __name__ == '__main__':
    args = sys.argv[sys.argv.index('--')+1:]
    native_build(*args)
