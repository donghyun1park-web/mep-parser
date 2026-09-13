"""Independent mesh, saved Blender and GLB checks; no existence-only success."""
import collections
import hashlib
import json
import math
import struct
import sys
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

TOL_M = 0.00005  # 0.05 mm: float32 export, after local-origin normalization.
METADATA_KEYS = ('eid', 'source_eid', 'category', 'source_length_mm', 'sampled_length_mm',
    'needs_review', 'dimension_basis', 'system', 'layer', 'source_layers', 'source_layer',
    'region_id', 'layout_id', 'material', 'circuit_id', 'nominal_size', 'assumptions')


def require(ok, message):
    if not ok: raise ValueError(message)


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_mesh_payload(spec):
    vertices, faces = spec['vertices_m'], spec['faces']
    require(vertices and faces, 'Empty mesh payload')
    require(all(len(p) == 3 and all(math.isfinite(v) for v in p) for p in vertices), 'Mesh positions must be finite')
    edges, directions, volume = collections.Counter(), collections.Counter(), 0.0
    for face in faces:
        require(len(face) >= 3 and len(set(face)) == len(face), 'Degenerate mesh face')
        require(all(isinstance(i, int) and 0 <= i < len(vertices) for i in face), 'Mesh index out of bounds')
        for a, b in zip(face, face[1:]+face[:1]):
            edges[tuple(sorted((a,b)))] += 1; directions[(a,b)] += 1
        a = vertices[face[0]]
        for k in range(1, len(face)-1):
            b, c = vertices[face[k]], vertices[face[k+1]]
            volume += (a[0]*(b[1]*c[2]-b[2]*c[1]) + a[1]*(b[2]*c[0]-b[0]*c[2]) + a[2]*(b[0]*c[1]-b[1]*c[0]))/6
    require(all(n == 2 for n in edges.values()), 'Mesh is not closed')
    require(all(directions[(a,b)] == directions[(b,a)] for a,b in directions), 'Mesh winding is inconsistent')
    expected = spec['expected_volume_m3']
    require(math.isfinite(expected) and expected > 0, 'Invalid expected volume')
    require(volume > 0 and math.isclose(volume, expected, rel_tol=1e-7, abs_tol=1e-10), 'Mesh volume does not match source footprint')
    return {'volume_m3': volume, 'vertices': len(vertices), 'faces': len(faces)}


def validate_payload(data):
    require(data.get('schema') == 'mep.blender_payload.v1' and data.get('units') == 'metres', 'Payload schema/units mismatch')
    require(len(data.get('origin_mm', [])) == 3 and all(math.isfinite(v) for v in data['origin_mm']), 'Invalid origin')
    require(data.get('objects'), 'Empty payload')
    ids = [o['eid'] for o in data['objects']]
    require(len(set(ids)) == len(ids), 'Duplicate payload EID')
    for obj in data['objects']:
        require(obj['source_eid'] in data['source_eids'], 'Model object has no source EID')
        z = obj['z_bounds_mm']
        require(len(z) == 2 and all(math.isfinite(v) for v in z) and z[1] > z[0], 'Invalid Z bounds')
        if obj['kind'] == 'mesh':
            validate_mesh_payload(obj)
        elif obj['kind'] == 'curve':
            pts = obj['points_m']; radius = obj['radius_m']
            require(len(pts) >= 2 and all(len(p) == 3 and all(math.isfinite(v) for v in p) for p in pts), 'Invalid curve positions')
            require(math.isfinite(radius) and radius > 0, 'Invalid curve radius')
            # 계약 v3 경로는 점마다 높이가 다르다 — 가장 낮은/높은 점 ± 반지름이 원본 범위다.
            zs = [p[2]*1000 for p in pts]
            require(abs(min(zs)-radius*1000-z[0]) < 1e-6 and abs(max(zs)+radius*1000-z[1]) < 1e-6, 'Curve Z/radius contradict source bounds')
        else:
            raise ValueError('Unsupported payload object kind')


def _matmul(a, b):
    return [[sum(a[i][k]*b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _local_matrix(node):
    if 'matrix' in node:
        a = node['matrix']; require(len(a) == 16 and all(math.isfinite(x) for x in a), 'Invalid GLB matrix')
        return [[a[c*4+r] for c in range(4)] for r in range(4)]
    t, q, s = node.get('translation', [0,0,0]), node.get('rotation', [0,0,0,1]), node.get('scale', [1,1,1])
    require(len(t) == 3 and len(q) == 4 and len(s) == 3 and all(math.isfinite(v) for v in t+q+s), 'Invalid GLB transform')
    x,y,z,w = q
    require(abs(sum(v*v for v in q)-1) < 1e-4, 'Invalid GLB quaternion')
    r = [[1-2*y*y-2*z*z,2*x*y-2*z*w,2*x*z+2*y*w],
         [2*x*y+2*z*w,1-2*x*x-2*z*z,2*y*z-2*x*w],
         [2*x*z-2*y*w,2*y*z+2*x*w,1-2*x*x-2*y*y]]
    return [[r[i][j]*s[j] for j in range(3)]+[t[i]] for i in range(3)]+[[0,0,0,1]]


def _bounds(points):
    require(points, 'No evaluated points')
    return [[min(p[i] for p in points) for i in range(3)], [max(p[i] for p in points) for i in range(3)]]


def _refs(value):
    return json.loads(value) if isinstance(value, str) else value or []


def verify_glb(path, expected):
    """Read actual embedded vertices and node transforms, not accessor min/max."""
    blob = Path(path).read_bytes()
    require(len(blob) >= 20, 'GLB too short')
    magic, version, size = struct.unpack_from('<4sII', blob)
    require(magic == b'glTF' and version == 2 and size == len(blob), 'Invalid GLB header')
    offset, chunks = 12, []
    while offset < len(blob):
        require(offset+8 <= len(blob), 'Truncated GLB chunk')
        length, kind = struct.unpack_from('<II', blob, offset); offset += 8
        require(length % 4 == 0 and offset+length <= len(blob), 'Invalid GLB chunk bounds')
        chunks.append((kind, blob[offset:offset+length])); offset += length
    require(len(chunks) == 2 and chunks[0][0] == 0x4E4F534A and chunks[1][0] == 0x004E4942, 'Expected embedded JSON and BIN')
    doc, binary = json.loads(chunks[0][1].rstrip(b' \x00')), chunks[1][1]
    require(doc.get('asset', {}).get('version') == '2.0', 'Invalid glTF asset')
    require(len(doc.get('scenes', [])) == 1 and doc.get('scene') == 0, 'GLB must contain one active scene')
    require(not doc.get('animations') and not doc.get('skins'), 'Unsupported animated/deformed GLB')
    nodes, meshes = doc.get('nodes', []), doc.get('meshes', [])
    views, accessors, buffers = doc.get('bufferViews', []), doc.get('accessors', []), doc.get('buffers', [])
    require(len(buffers) == 1 and 'uri' not in buffers[0] and 0 <= len(binary)-buffers[0]['byteLength'] <= 3, 'Invalid embedded buffer')
    parents = {}
    for i, node in enumerate(nodes):
        for child in node.get('children', []):
            require(isinstance(child, int) and 0 <= child < len(nodes) and child not in parents, 'Invalid node hierarchy')
            parents[child] = i
    roots = doc['scenes'][0].get('nodes', [])
    require(all(i not in parents for i in roots), 'Active scene root has a parent')
    reachable, todo = set(), list(roots)
    while todo:
        i = todo.pop(); require(isinstance(i, int) and 0 <= i < len(nodes), 'Invalid scene node')
        if i not in reachable: reachable.add(i); todo.extend(nodes[i].get('children', []))
    geometry = {i for i,n in enumerate(nodes) if 'mesh' in n}
    require(geometry and geometry <= reachable, 'Orphan geometry outside active scene')
    used = {nodes[i]['mesh'] for i in geometry}
    require(used == set(range(len(meshes))), 'Orphan or invalid mesh index')
    by_eid = {}
    for i in geometry:
        eid = nodes[i].get('extras', {}).get('eid')
        require(eid in expected and eid not in by_eid, 'GLB EID missing, duplicate, or unexpected')
        by_eid[eid] = i
    require(set(by_eid) == set(expected), 'GLB source EID set differs from active model')
    matrices = {}
    def matrix(i, trail=()):
        require(i not in trail, 'Cyclic GLB hierarchy')
        if i not in matrices:
            local = _local_matrix(nodes[i])
            matrices[i] = _matmul(matrix(parents[i], trail+(i,)), local) if i in parents else local
        return matrices[i]
    vertices_read, measurements = 0, {}
    for eid, index in by_eid.items():
        node = nodes[index]
        require(_refs(node.get('extras', {}).get('source_refs')) == _refs(expected[eid].get('source_refs')), 'GLB source references differ')
        if expected[eid].get('input_sha256'):
            require(node.get('extras', {}).get('input_sha256') == expected[eid]['input_sha256'], 'GLB input provenance differs')
        for key, value in expected[eid].get('metadata', {}).items():
            require(node.get('extras', {}).get(key) == value, f'GLB source metadata differs: {eid}.{key}')
        mesh, points, transform = meshes[node['mesh']], [], matrix(index)
        require(not node.get('skin') and not node.get('weights') and not mesh.get('weights'), 'GLB source deformation unsupported')
        require(mesh.get('primitives'), 'GLB mesh has no primitives')
        for primitive in mesh['primitives']:
            require(not primitive.get('targets') and not primitive.get('extensions'), 'Unsupported GLB geometry extension')
            require(primitive.get('mode', 4) == 4, 'GLB review mesh must contain triangles')
            ai = primitive.get('attributes', {}).get('POSITION')
            require(isinstance(ai, int) and 0 <= ai < len(accessors), 'Invalid POSITION accessor')
            acc = accessors[ai]
            require(acc.get('componentType') == 5126 and acc.get('type') == 'VEC3' and acc.get('count',0) > 0 and 'sparse' not in acc, 'Unsupported POSITION encoding')
            vi = acc.get('bufferView', -1); require(0 <= vi < len(views), 'Invalid POSITION bufferView')
            view = views[vi]; stride = view.get('byteStride',12); local = acc.get('byteOffset',0)
            start = view.get('byteOffset',0)+local
            require(view.get('buffer',0) == 0 and stride >= 12 and local >= 0 and start >= 0
                and local+stride*(acc['count']-1)+12 <= view['byteLength']
                and view.get('byteOffset',0)+view['byteLength'] <= buffers[0]['byteLength'], 'POSITION exceeds BIN bounds')
            positions = [struct.unpack_from('<fff', binary, start+j*stride) for j in range(acc['count'])]
            require(all(math.isfinite(v) for p in positions for v in p), 'GLB POSITION must be finite')
            if 'indices' in primitive:
                index_id = primitive['indices']
                require(isinstance(index_id, int) and 0 <= index_id < len(accessors), 'Invalid triangle index accessor')
                ia = accessors[index_id]
                encodings = {5121: ('B', 1), 5123: ('H', 2), 5125: ('I', 4)}
                require(ia.get('componentType') in encodings and ia.get('type') == 'SCALAR' and 'sparse' not in ia
                    and ia.get('count', 0) > 0 and ia['count'] % 3 == 0, 'Invalid triangle index encoding/count')
                iv = ia.get('bufferView', -1); require(0 <= iv < len(views), 'Invalid triangle index view')
                view_index = views[iv]; encoding, step = encodings[ia['componentType']]
                ioffset = ia.get('byteOffset', 0); istart = view_index.get('byteOffset', 0)+ioffset
                require(view_index.get('buffer', 0) == 0 and ioffset >= 0 and istart >= 0
                    and ioffset+ia['count']*step <= view_index['byteLength']
                    and view_index.get('byteOffset', 0)+view_index['byteLength'] <= buffers[0]['byteLength'], 'Triangle index data exceeds BIN')
                indices = struct.unpack_from('<'+encoding*ia['count'], binary, istart)
                require(all(i < acc['count'] for i in indices), 'Triangle index exceeds POSITION count')
            else:
                require(acc['count'] % 3 == 0, 'Non-indexed triangle vertex count invalid')
            points += [[sum(transform[r][c]*p[c] for c in range(3))+transform[r][3] for r in range(3)] for p in positions]
            vertices_read += len(positions)
        actual = _bounds(points)
        # glTF Y-up: [x,y,z] = Blender [x,z,-y].
        low, high = expected[eid]['bounds_m']
        desired = [[low[0], low[2], -high[1]], [high[0], high[2], -low[1]]]
        require(all(abs(actual[k][j]-desired[k][j]) <= TOL_M for k in (0,1) for j in range(3)), f'GLB actual bounds differ: {eid}')
        measurements[eid] = actual
    return {'status': 'verified', 'scene_count': 1, 'eids': sorted(by_eid),
        'position_vertices_read': vertices_read, 'actual_bounds_gltf_m': measurements, 'sha256': file_hash(path)}


def verify_native(payload_path, out_dir, run_id, input_sha256):
    import bpy
    payload_path, out_dir = Path(payload_path), Path(out_dir)
    data = json.loads(payload_path.read_text(encoding='utf-8')); validate_payload(data)
    blend = out_dir/'model.blend'; before = file_hash(blend)
    bpy.ops.wm.open_mainfile(filepath=str(blend))
    expected_ids = {o['eid'] for o in data['objects']}
    found = [o for o in bpy.data.objects if o.get('eid')]
    require(len(found) == len(expected_ids) and {o['eid'] for o in found} == expected_ids, 'Saved Blender full EID set differs')
    actual, mesh_checks, curve_checks = {}, 0, 0
    for spec in data['objects']:
        ob = next(o for o in found if o['eid'] == spec['eid'])
        require(_refs(ob.get('source_refs')) == spec.get('source_refs', []), 'Saved source references differ')
        require(ob.get('input_sha256') == input_sha256, 'Saved object input hash differs')
        metadata = {}
        for key in METADATA_KEYS:
            value = spec.get(key)
            if value is not None:
                stored = value if isinstance(value, (str, bool, int, float)) else json.dumps(value, ensure_ascii=False)
                require(ob.get(key) == stored, f'Saved source metadata differs: {spec["eid"]}.{key}')
                metadata[key] = stored
        scene = bpy.data.scenes['MEP_COVERED']; bpy.context.window.scene = scene
        require(ob.name in scene.objects, 'Saved model object absent from covered scene')
        if spec['kind'] == 'curve':
            require(ob.type == 'CURVE' and len(ob.data.splines) == 1 and ob.data.splines[0].type == 'POLY', 'Saved source path is not editable polyline')
            require(not ob.modifiers and ob.data.bevel_mode == 'ROUND' and ob.data.use_fill_caps and ob.data.bevel_resolution == 4, 'Saved curve profile differs')
            require(abs(ob.data.bevel_depth-spec['radius_m']) < 1e-8, 'Saved curve diameter differs')
            points = [list(ob.matrix_world @ p.co.to_3d()) for p in ob.data.splines[0].points]
            require(len(points) == len(spec['points_m']) and all(math.dist(a,b) <= TOL_M for a,b in zip(points,spec['points_m'])), 'Saved source curve coordinates differ')
            curve_checks += 1
        else:
            require(ob.type == 'MESH' and not ob.modifiers, 'Saved mesh kind/modifiers differ')
            points = [list(ob.matrix_world @ p.co) for p in ob.data.vertices]
            require(len(points) == len(spec['vertices_m']) and all(math.dist(a,b) <= TOL_M for a,b in zip(points,spec['vertices_m'])), 'Saved source mesh vertices differ')
            require([list(f.vertices) for f in ob.data.polygons] == spec['faces'], 'Saved mesh faces differ')
            mesh_checks += 1
        evaluated = ob.evaluated_get(bpy.context.evaluated_depsgraph_get()); mesh = evaluated.to_mesh()
        try:
            require(mesh and len(mesh.vertices) > 0 and len(mesh.polygons) > 0, 'Saved evaluated mesh empty')
            positions = [list(evaluated.matrix_world @ v.co) for v in mesh.vertices]
            require(all(math.isfinite(v) for p in positions for v in p), 'Saved evaluated geometry is not finite')
            bounds = _bounds(positions)
        finally: evaluated.to_mesh_clear()
        require(abs(bounds[0][2]*1000-spec['z_bounds_mm'][0]) <= TOL_M*1000 and abs(bounds[1][2]*1000-spec['z_bounds_mm'][1]) <= TOL_M*1000, 'Saved actual Z bounds differ')
        actual[spec['eid']] = {'bounds_m': bounds, 'source_refs': spec.get('source_refs', []),
            'input_sha256': input_sha256, 'metadata': metadata}
    active = bpy.data.scenes['MEP_EXPOSED']
    require(active.get('run_id') == run_id and active.get('input_sha256') == input_sha256 and active.get('payload_sha256') == file_hash(payload_path), 'Saved scene provenance differs')
    require({o.get('eid') for o in active.objects if o.get('eid')} == {s['eid'] for s in data['objects'] if not s.get('covered_only')}, 'Saved exposed scene membership differs')
    expected_glb = {s['eid']: actual[s['eid']] for s in data['objects'] if not s.get('covered_only')}
    glb = verify_glb(out_dir/'model.glb', expected_glb)
    require(before == file_hash(blend), 'Verifier changed saved blend file')
    report = {'schema': 'mep.blender_verification.v1', 'status': 'verified', 'run_id': run_id,
        'input_sha256': input_sha256, 'payload_sha256': file_hash(payload_path), 'native_reopened': True,
        'verified_eids': sorted(actual), 'native_objects': actual, 'mesh_checks': mesh_checks, 'curve_checks': curve_checks,
        'glb': glb, 'artifacts': {'blend': {'path': str(blend), 'sha256': before},
            'glb': {'path': str(out_dir/'model.glb'), 'sha256': glb['sha256']}},
        'scope': 'source_geometry_and_artifact_integrity; design_and_physics_not_evaluated'}
    (out_dir/'native_verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    args = sys.argv[sys.argv.index('--')+1:]
    verify_native(*args)
