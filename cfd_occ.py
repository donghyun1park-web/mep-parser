"""Normal-Python controller for the isolated FreeCAD OCC worker."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile

from cfd_capabilities import find_freecadcmd, freecad_headless_command
from geometry_v2 import migrate_geometry, validate_geometry_v2


HERE = Path(__file__).resolve().parent
WORKER = HERE / "cfd_occ_worker.py"


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_occ_output(output_dir):
    output = Path(output_dir).expanduser().resolve()
    manifest_path = output / "surface_manifest.json"
    if not manifest_path.is_file():
        return {"ok": False, "error": f"surface_manifest.json이 없습니다: {output}"}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"surface manifest를 읽지 못했습니다: {exc}"}
    required = ("air_volume_regions.stl", "air_volume.brep", "air_volume.FCStd")
    missing = [name for name in required if not (output / name).is_file()]
    air = manifest.get("air_volume") or {}
    topology = manifest.get("topology") or {}
    outputs = manifest.get("outputs") or {}
    hash_errors = []
    for filename, key in (("air_volume_regions.stl", "stl_sha256"),
                          ("air_volume.brep", "brep_sha256")):
        path = output / filename
        expected = outputs.get(key)
        if path.is_file() and expected and _sha256_file(path) != expected:
            hash_errors.append(filename)
    ok = (
        manifest.get("contract") == "surface_manifest.v1"
        and air.get("valid") is True
        and air.get("solid_count") == 1
        and topology.get("watertight") is True
        and topology.get("open_edges") == 0
        and topology.get("non_manifold_edges") == 0
        and topology.get("duplicate_triangles") == 0
        and float(air.get("area_error_ratio", 1.0)) <= 0.001
        and not missing
        and not hash_errors
    )
    return {
        "ok": ok,
        "error": ("필수 OCC 출력 누락: " + ", ".join(missing) if missing else
                  "OCC 출력 해시 불일치: " + ", ".join(hash_errors) if hash_errors else ""),
        "output": str(output),
        "manifest_path": str(manifest_path),
        "manifest": manifest,
        "missing": missing,
        "hash_errors": hash_errors,
    }


def _prepare_geometry(path):
    source_path = Path(path).expanduser().resolve()
    if not source_path.is_file():
        return None, f"geometry 파일이 없습니다: {source_path}"
    try:
        source = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"geometry 파일을 읽지 못했습니다: {exc}"
    geometry = migrate_geometry(source, source_path=source.get("source") or str(source_path))
    issues = validate_geometry_v2(geometry)
    if issues:
        return None, "geometry.v2 계약 오류: " + "; ".join(item["message"] for item in issues[:5])
    review = geometry.get("review") or {}
    if review.get("blocking"):
        return None, "정밀 3D CFD 필수 확인이 남아 있습니다: " + "; ".join(
            item.get("message", item.get("code", "")) for item in review.get("items", [])[:5]
        )
    geometry["occ_source_path"] = str(source_path)
    return geometry, ""


def run_occ_job(geometry_path, output_dir, executable=None, timeout=300):
    """Build and atomically publish one body-fitted OCC geometry job."""
    geometry, error = _prepare_geometry(geometry_path)
    if error:
        return {"ok": False, "error": error}
    freecad = executable or find_freecadcmd()
    if not freecad:
        return {"ok": False, "error": "FreeCADCmd를 찾지 못했습니다. 환경 다시 검사를 실행하세요."}
    output = Path(output_dir).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        with tempfile.TemporaryDirectory(prefix=".occ-process-", dir=output.parent) as tmp:
            process_dir = Path(tmp)
            normalized_path = process_dir / "geometry.v2.json"
            normalized_path.write_text(
                json.dumps(geometry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            env = dict(
                os.environ,
                MEP_CFD_GEOMETRY=str(normalized_path),
                MEP_CFD_OCC_OUTPUT=str(output),
                PYTHONIOENCODING="utf-8",
            )
            command = freecad_headless_command(freecad, WORKER, process_dir)
            proc = subprocess.run(
                command,
                cwd=HERE,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=creationflags,
            )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"FreeCAD OCC 작업이 {timeout}초를 초과했습니다."}
    except OSError as exc:
        return {"ok": False, "error": f"FreeCAD OCC 작업을 시작하지 못했습니다: {exc}"}

    if proc.returncode != 0:
        detail = ((proc.stdout or "")[-4000:] + "\n" + (proc.stderr or "")[-2000:]).strip()
        return {"ok": False, "error": "FreeCAD OCC 작업 실패", "detail": detail,
                "returncode": proc.returncode}
    result = inspect_occ_output(output)
    result["stdout"] = (proc.stdout or "")[-4000:]
    if not result["ok"] and not result.get("error"):
        result["error"] = "OCC 출력 검증에 실패했습니다."
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build body-fitted CFD OCC geometry")
    parser.add_argument("geometry")
    parser.add_argument("output")
    parser.add_argument("--freecad")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    value = run_occ_job(args.geometry, args.output, executable=args.freecad, timeout=args.timeout)
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if value.get("ok") else 2)
