"""Launch exactly the requested checkout, bypassing FreeCAD macro import paths."""
import json
import os
import subprocess
import tempfile
import uuid
import artifact_validation as AV


def _record_runtime(geometry_path, out_base, builder_path, run_id, exit_code, stdout, stderr, failure=None):
    path = os.path.abspath(out_base + ".build.json")
    stats = {}
    try:
        with open(path, encoding="utf-8") as stream:
            existing = json.load(stream)
        if existing.get("provenance", {}).get("run_id") == run_id:
            stats = existing
    except (OSError, ValueError):
        pass
    if not stats:
        try:
            with open(geometry_path, encoding="utf-8") as stream:
                prov = AV.provenance(json.load(stream))
        except (OSError, ValueError):
            prov = {}
        prov.update({"run_id": run_id, "builder_path": builder_path,
                     "builder_sha256": AV.file_hash(builder_path)})
        stats = {"schema_version": 2, "provenance": prov, "status": "failed",
                 "runtime_errors": ["Builder did not produce a current-run receipt"],
                 "artifacts": {k: {"status": "failed", "path": None, "provenance": prov} for k in ("fcstd", "ifc")}}
    decode = lambda value: value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value or "")
    stats["runtime"] = {**stats.get("runtime", {}), "process_exit_code": exit_code,
                        "stdout": decode(stdout), "stderr": decode(stderr)}
    if failure:
        stats.setdefault("runtime_errors", []).append(failure)
        stats["status"] = "failed"
    if exit_code not in (None, 0) or failure:
        stats["status"] = "failed"
        for artifact in stats.get("artifacts", {}).values():
            error = "; ".join(str(value) for value in (artifact.get("error"), failure or f"FreeCAD process exited {exit_code}") if value)
            artifact.update({"status": "failed", "path": None,
                             "error": error})
    temporary = path + "." + run_id + ".tmp"
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(stats, stream, ensure_ascii=False, indent=2)
    os.replace(temporary, path)


def run_build(freecadcmd, geometry_path, out_base, builder_path, timeout=900):
    builder_path = os.path.abspath(builder_path)
    parent = os.path.dirname(os.path.abspath(out_base))
    os.makedirs(parent, exist_ok=True)
    # FreeCAD C++ filename handling still needs ASCII paths on some Windows builds.
    temp_parent = parent if parent.isascii() else None
    with tempfile.TemporaryDirectory(prefix="mep_launch_", dir=temp_parent) as temp:
        wrapper = os.path.join(temp, "launch_" + uuid.uuid4().hex + ".py")
        with open(wrapper, "w", encoding="utf-8") as stream:
            stream.write("import sys, runpy\n")
            stream.write("sys.path.insert(0, " + repr(os.path.dirname(builder_path)) + ")\n")
            stream.write("for name in ('freecad_builder', 'verify', 'artifact_validation', 'geom_contract'):\n    sys.modules.pop(name, None)\n")
            stream.write("runpy.run_path(" + repr(builder_path) + ", run_name='__main__')\n")
        run_id = uuid.uuid4().hex
        env = dict(os.environ, MEP_GEOMETRY=os.path.abspath(geometry_path),
                   MEP_OUT=os.path.abspath(out_base), MEP_BUILD_RUN_ID=run_id)
        try:
            result = subprocess.run([str(freecadcmd), wrapper], cwd=os.path.dirname(builder_path),
                                    env=env, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            _record_runtime(geometry_path, out_base, builder_path, run_id, None,
                            exc.stdout, exc.stderr, "FreeCAD runtime timeout")
            exc.run_id = run_id
            raise
        except OSError as exc:
            _record_runtime(geometry_path, out_base, builder_path, run_id, None, b"", str(exc), str(exc))
            raise
        _record_runtime(geometry_path, out_base, builder_path, run_id, result.returncode,
                        result.stdout, result.stderr)
    result.run_id = run_id
    return result
