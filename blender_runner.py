"""Hidden, resource-gated Blender subprocesses with current-run verification."""
import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from blender_builder import prepare_payload
from blender_verify import file_hash, validate_payload, verify_glb

HERE = Path(__file__).resolve().parent


def find_blender(configured=None):
    if configured is not None:
        candidate = Path(configured)
        return str(candidate.resolve()) if candidate.is_file() else None
    path = shutil.which('blender')
    if path: return path
    if os.name == 'nt':
        root = Path(os.environ.get('ProgramFiles', r'C:\Program Files'))/'Blender Foundation'
        preferred = root/'Blender 4.2'/'blender.exe'
        if preferred.is_file(): return str(preferred)
        available = sorted(root.glob('Blender */blender.exe'), reverse=True) if root.is_dir() else []
        if available: return str(available[0])
    return None


def resource_snapshot(out_dir, payload):
    available = None
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        class Memory(ctypes.Structure):
            _fields_ = [('length',wintypes.DWORD),('load',wintypes.DWORD)] + [(name,ctypes.c_ulonglong) for name in
                ('total','available','totalpage','availablepage','totalvirt','availablevirt','extended')]
        state = Memory(); state.length = ctypes.sizeof(state)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)): available = state.available
    else:
        try: available = os.sysconf('SC_AVPHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
        except (ValueError, OSError, AttributeError): pass
    vertices = sum(len(o.get('vertices_m',[])) + len(o.get('points_m',[]))*24 for o in payload['objects'])
    required = max(1024**3, 512*1024**2 + vertices*320)
    disk = shutil.disk_usage(out_dir).free
    return {'available_ram_bytes': available, 'required_ram_bytes_estimate': required,
        'free_disk_bytes': disk, 'required_disk_bytes_estimate': max(256*1024**2, vertices*120),
        'estimate_basis': '1 GiB minimum, plus geometry-scaled working estimate; no process termination'}


def validate_receipt(receipt, run_id, input_sha256, output_root):
    """Reject stale/forged markers, missing files, bad hashes and wrong GLB data."""
    root = Path(output_root).resolve()
    if receipt.get('schema') != 'mep.blender_verification.v1' or receipt.get('status') != 'verified':
        raise ValueError('Native verification schema/status missing')
    if receipt.get('run_id') != run_id or receipt.get('input_sha256') != input_sha256 or receipt.get('native_reopened') is not True:
        raise ValueError('Native verification provenance/reopen mismatch')
    objects, ids = receipt.get('native_objects') or {}, receipt.get('verified_eids') or []
    if not objects or set(objects) != set(ids) or len(ids) != len(set(ids)):
        raise ValueError('Native source EID coverage missing')
    if receipt.get('mesh_checks',0) + receipt.get('curve_checks',0) != len(ids):
        raise ValueError('Native geometry checks incomplete')
    paths = {}
    for kind in ('blend','glb'):
        row = (receipt.get('artifacts') or {}).get(kind) or {}
        path = Path(row.get('path') or '').resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size < 20:
            raise ValueError(f'Missing or out-of-root artifact: {kind}')
        if row.get('sha256') != file_hash(path):
            raise ValueError(f'Artifact hash mismatch: {kind}')
        paths[kind] = path
    with paths['blend'].open('rb') as stream:
        if stream.read(7) != b'BLENDER': raise ValueError('Saved artifact is not an uncompressed Blender file')
    glb_ids = (receipt.get('glb') or {}).get('eids') or []
    if not glb_ids or not set(glb_ids) <= set(objects): raise ValueError('Native GLB source coverage missing')
    result = verify_glb(paths['glb'], {eid: objects[eid] for eid in glb_ids})
    if result['sha256'] != receipt['artifacts']['glb']['sha256']: raise ValueError('Rechecked GLB hash mismatch')
    return result


def build_blender(geometry_path, out_dir, blender_path=None, timeout=900):
    """Return a verified/blocked/failed JSON receipt; never launch a visible window.

    Every run has its own directory. Previous exports are preserved and cannot
    satisfy a failed current run. GUI callers should show diagnostics and use
    artifact paths only when status is ``verified``.
    """
    run_id = uuid.uuid4().hex
    geometry_path, out_dir = Path(geometry_path).resolve(), Path(out_dir).resolve()
    receipt = {'schema': 'mep.blender_run.v1', 'status': 'failed', 'run_id': run_id,
        'input_sha256': None, 'artifacts': {}, 'diagnostics': [], 'errors': [], 'verification': None}
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        run_dir = out_dir/('run_'+run_id); run_dir.mkdir()
        receipt['run_dir'] = str(run_dir)
        raw = geometry_path.read_bytes()
        receipt['input_sha256'] = hashlib.sha256(raw).hexdigest()
        receipt['input_path'] = str(geometry_path)
        snapshot = run_dir/'input_geometry.json'; snapshot.write_bytes(raw)
        receipt['input_snapshot'] = str(snapshot)
        data = json.loads(raw.decode('utf-8-sig'))
        from verify import verify_geometry
        gate = verify_geometry(data)
        receipt['source_verification'] = gate.to_dict()
        if gate.failed:
            receipt['errors'].append('Source geometry/review gate failed; resolve findings before export.')
            return _save_receipt(receipt, out_dir)
        payload = prepare_payload(data)
        validate_payload(payload)
        receipt['diagnostics'] = payload['diagnostics'] + [
            {'code': f.id, 'severity': f.severity, 'message': f.message, 'detail': f.payload}
            for f in gate.warns]
        executable = find_blender(blender_path)
        if not executable:
            receipt['status'] = 'blocked'; receipt['errors'].append('Blender executable not found; select Blender 4.2 or later.')
            return _save_receipt(receipt, out_dir)
        resources = resource_snapshot(run_dir, payload); receipt['resources'] = resources
        if resources['available_ram_bytes'] is None or resources['available_ram_bytes'] < resources['required_ram_bytes_estimate'] or resources['free_disk_bytes'] < resources['required_disk_bytes_estimate']:
            receipt['status'] = 'blocked'; receipt['errors'].append('Native build not run: resource measurement/available RAM or disk below working estimate.')
            return _save_receipt(receipt, out_dir)
        payload_path = run_dir/'blender_payload.json'
        payload_path.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
        receipt['payload_path'] = str(payload_path); receipt['payload_sha256'] = file_hash(payload_path)
        started = time.monotonic(); receipt['runtime'] = []
        for script in ('blender_builder.py','blender_verify.py'):
            command = [executable,'--background','--factory-startup','--python-exit-code','1',
                '--python',str(HERE/script),'--',str(payload_path),str(run_dir),run_id,receipt['input_sha256']]
            remaining = max(1, timeout-(time.monotonic()-started))
            completed = subprocess.run(command, cwd=str(HERE), capture_output=True, timeout=remaining,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            log = run_dir/(Path(script).stem+'.log')
            log.write_bytes(completed.stdout+b'\n'+completed.stderr)
            receipt['runtime'].append({'script':script,'script_sha256':file_hash(HERE/script),
                'exit_code': completed.returncode,'log_path':str(log)})
            if completed.returncode:
                raise ValueError(f'{script} exited {completed.returncode}; see {log}')
        native_path = run_dir/'native_verification.json'
        report = json.loads(native_path.read_text(encoding='utf-8'))
        if report.get('payload_sha256') != receipt['payload_sha256']:
            raise ValueError('Native payload hash mismatch')
        if report.get('verified_eids') != sorted(o['eid'] for o in payload['objects']):
            raise ValueError('Native report source coverage differs from this payload')
        if (report.get('glb') or {}).get('eids') != sorted(o['eid'] for o in payload['objects'] if not o.get('covered_only')):
            raise ValueError('Native GLB source coverage differs from exposed payload')
        receipt['host_glb_recheck'] = validate_receipt(report, run_id, receipt['input_sha256'], run_dir)
        if file_hash(geometry_path) != receipt['input_sha256']:
            raise ValueError('Source geometry changed during export')
        if file_hash(snapshot) != receipt['input_sha256']:
            raise ValueError('Immutable input snapshot changed during export')
        receipt['verification'] = report
        receipt['artifacts'] = report['artifacts']; receipt['status'] = 'verified'
    except subprocess.TimeoutExpired:
        receipt['errors'].append('Native Blender build/verification timed out; output is unverified.')
    except Exception as exc:
        receipt['errors'].append(f'{type(exc).__name__}: {exc}')
    return _save_receipt(receipt, out_dir)


def _save_receipt(receipt, out_dir):
    path = Path(out_dir)/'blender_build.json'; receipt['receipt_path'] = str(path)
    if receipt['status'] != 'verified': receipt['artifacts'] = {}
    temporary = path.with_suffix('.'+receipt['run_id']+'.tmp')
    temporary.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    os.replace(temporary, path)
    return receipt
