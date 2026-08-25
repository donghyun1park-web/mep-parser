"""Windows-side runtime capability probes for MEP CFD Studio.

OpenFOAM lives in WSL and is diagnosed by :mod:`cfd_run`.  FreeCAD stays on
Windows and is launched as an isolated, headless process.  Keeping this module
free of FreeCAD imports lets the normal application Python inspect the runtime
without depending on FreeCAD's embedded Python.
"""

from __future__ import annotations

from datetime import datetime, timezone
import glob
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid


FREECAD_EXE_ENV = "MEP_CFD_FREECADCMD"
SUPPORTED_FREECAD = "1.1.1"
SUPPORTED_OCC = "7.8.1"
_PROBE_MARKER = "MEP_CFD_FREECAD_CAPABILITY:"
_STAGE_MARKER = "MEP_CFD_FREECAD_STAGE:"
RUNTIME_CAPABILITY_CONTRACT = "runtime_capability.v1"
MPI_COMMANDS = ("mpirun", "decomposePar", "reconstructPar")
MPI_SMOKE_IDENTITY_FIELDS = (
    "distro", "kernel", "mpirun_path", "mpirun_version",
    "ompi_info_version", "effective_cpu_count",
)


def _positive_int_or_none(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _finite_or_none(value):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _sha256_or_empty(value):
    value = str(value or "").lower()
    return value if re.fullmatch(r"[0-9a-f]{64}", value) else ""


def _mpi_smoke_identity(value):
    """Keep only the exact WSL/Open MPI identity used by a smoke artifact."""
    try:
        raw = dict(value or {})
    except (TypeError, ValueError):
        return {}
    identity = {
        "distro": str(raw.get("distro") or ""),
        "kernel": str(raw.get("kernel") or ""),
        "mpirun_path": str(raw.get("mpirun_path") or ""),
        "mpirun_version": str(raw.get("mpirun_version") or ""),
        "ompi_info_version": str(raw.get("ompi_info_version") or ""),
        "effective_cpu_count": _positive_int_or_none(raw.get("effective_cpu_count")),
    }
    return identity if any(identity.values()) else {}


def _mpi_smoke_identity_complete(identity):
    return bool(identity) and all(
        identity.get(field) not in (None, "")
        for field in MPI_SMOKE_IDENTITY_FIELDS
    )


def build_runtime_capability(openfoam, baseline=None, *, created_at=None,
                             mpi_smoke=None):
    """Build an honest WSL/OpenFOAM runtime capability evidence payload.

    A serial runtime can be ready even if MPI is not installed. MPI command
    discovery is distinct from an actual MPI execution smoke test, which starts
    as NOT_RUN until a controlled benchmark records it.
    """
    openfoam = dict(openfoam or {})
    commands = dict(openfoam.get("commands") or {})
    mpi_tools = {name: str(commands.get(name) or "") for name in MPI_COMMANDS}
    missing_mpi = [name for name in MPI_COMMANDS if not mpi_tools[name]]
    cpu_count = _positive_int_or_none(openfoam.get("effective_cpu_count"))
    baseline = dict(baseline or {})
    baseline_status = str(baseline.get("status") or "NOT_RUN")
    if baseline_status not in ("NOT_RUN", "PARTIAL", "PASS", "FAIL"):
        baseline_status = "NOT_RUN"
    smoke = dict(mpi_smoke or {})
    execution_smoke = str(
        smoke.get("status") or openfoam.get("mpi_execution_smoke") or "NOT_RUN"
    ).upper()
    # Earlier probes exposed FAIL, while the explicit smoke contract uses
    # BLOCKED for a failed or unsafe rank spawn.  Preserve the important
    # distinction from NOT_RUN rather than erasing field evidence.
    if execution_smoke == "FAIL":
        execution_smoke = "BLOCKED"
    if execution_smoke not in ("NOT_RUN", "PASS", "BLOCKED"):
        execution_smoke = "NOT_RUN"
    smoke_reason = str(smoke.get("reason_code") or "")
    smoke_artifact_path = str(smoke.get("artifact_path") or "")
    smoke_artifact_sha256 = _sha256_or_empty(smoke.get("artifact_sha256"))
    smoke_identity = _mpi_smoke_identity(smoke.get("identity"))
    smoke_proof_complete = bool(
        execution_smoke == "PASS" and smoke_artifact_path
        and smoke_artifact_sha256 and _mpi_smoke_identity_complete(smoke_identity)
    )
    static_parallel_ready = bool(openfoam.get("parallel_ready"))
    non_mpi_commands = {
        name: str(value or "")
        for name, value in commands.items()
        if name not in MPI_COMMANDS
    }
    return {
        "schema_version": 1,
        "contract": RUNTIME_CAPABILITY_CONTRACT,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "serial_runtime_ready": bool(openfoam.get("ok")),
        "parallel_runtime_ready": bool(
            static_parallel_ready and smoke_proof_complete
        ),
        "openfoam": {
            "status": str(openfoam.get("status") or ""),
            "distro": str(openfoam.get("distro") or ""),
            "kernel": str(openfoam.get("kernel") or ""),
            "version": str(openfoam.get("version") or ""),
            "package_version": str(openfoam.get("package_version") or ""),
            "compatible_profile": str(openfoam.get("compatible_profile") or ""),
            "solvers": non_mpi_commands,
        },
        "mpi": {
            "tools": mpi_tools,
            "missing": missing_mpi,
            "version": str(openfoam.get("mpi_version") or ""),
            "tools_available": not missing_mpi,
            "static_prerequisites_ready": static_parallel_ready,
            "execution_smoke": execution_smoke,
            "reason_code": smoke_reason,
            "artifact_path": smoke_artifact_path,
            "artifact_sha256": smoke_artifact_sha256,
            "smoke_identity": smoke_identity,
        },
        "cpu": {
            "effective_logical_count": cpu_count,
            "source": str(openfoam.get("effective_cpu_source") or "WSL nproc"),
        },
        "serial_baseline": {
            "status": baseline_status,
            "runner_wall_seconds": _finite_or_none(baseline.get("runner_wall_seconds")),
            "solver_clock_seconds": _finite_or_none(baseline.get("solver_clock_seconds")),
            "peak_rss_kib": _positive_int_or_none(baseline.get("peak_rss_kib")),
            "case_input_sha256": _sha256_or_empty(baseline.get("case_input_sha256")),
            "solver_log_sha256": _sha256_or_empty(baseline.get("solver_log_sha256")),
        },
    }


def write_runtime_capability(path, payload):
    """Atomically write a runtime_capability.v1 payload and return its path."""
    target = os.path.abspath(os.fspath(path))
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=".runtime_capability.", suffix=".tmp", dir=os.path.dirname(target) or ".",
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return target


_FREECAD_PROBE = r'''
import json
import sys

result = {"modules": {}, "smoke": {}}
try:
    import FreeCAD as App
    result["modules"]["FreeCAD"] = True
    import Part
    result["modules"]["Part"] = True
    import Draft
    result["modules"]["Draft"] = True
    import Arch
    result["modules"]["Arch"] = True
    import Mesh
    result["modules"]["Mesh"] = True
    import MeshPart
    result["modules"]["MeshPart"] = True
    import BOPTools.SplitAPI
    result["modules"]["BOPTools.SplitAPI"] = True

    version = [str(x) for x in App.Version()]
    result["version_parts"] = version
    result["freecad_version"] = ".".join(version[:3])
    result["revision"] = version[3] if len(version) > 3 else ""
    result["python_version"] = sys.version.split()[0]
    result["occ_version"] = str(getattr(Part, "OCC_VERSION", ""))

    room = Part.makeBox(10000.0, 8000.0, 3000.0)
    column = Part.makeBox(
        500.0, 500.0, 3000.0, App.Vector(1000.0, 1000.0, 0.0)
    )
    cut = room.cut(column)
    solids = list(cut.Solids)
    solid = solids[0] if len(solids) == 1 else cut
    vertices, facets = solid.tessellate(100.0)
    expected_mm3 = 239250000000.0
    volume_mm3 = float(solid.Volume)
    rel_error = abs(volume_mm3 - expected_mm3) / expected_mm3
    result["smoke"] = {
        "shape_type": str(cut.ShapeType),
        "valid": bool(cut.isValid()),
        "solid_count": len(solids),
        "closed": bool(solid.isClosed()) if hasattr(solid, "isClosed") else None,
        "volume_mm3": volume_mm3,
        "expected_volume_mm3": expected_mm3,
        "relative_volume_error": rel_error,
        "face_count": len(solid.Faces),
        "tessellation_vertices": len(vertices),
        "tessellation_facets": len(facets),
    }
    result["smoke"]["ok"] = bool(
        result["smoke"]["valid"]
        and result["smoke"]["solid_count"] == 1
        and result["smoke"]["closed"] is not False
        and result["smoke"]["volume_mm3"] > 0
        and result["smoke"]["relative_volume_error"] <= 1e-9
        and result["smoke"]["tessellation_facets"] > 0
    )
except Exception as exc:
    result["exception"] = "%s: %s" % (type(exc).__name__, exc)
    result["smoke"]["ok"] = False

print("MEP_CFD_FREECAD_CAPABILITY:" + json.dumps(result, ensure_ascii=True))
'''


_FREECAD_STAGE_SCRIPTS = {
    "imports": r'''
import json
import sys
result = {"stage": "imports", "ok": False, "modules": {}}
try:
    import FreeCAD as App
    result["modules"]["FreeCAD"] = True
    import Part
    result["modules"]["Part"] = True
    import Draft
    result["modules"]["Draft"] = True
    import Arch
    result["modules"]["Arch"] = True
    import Mesh
    result["modules"]["Mesh"] = True
    import MeshPart
    result["modules"]["MeshPart"] = True
    import BOPTools.SplitAPI
    result["modules"]["BOPTools.SplitAPI"] = True
    version = [str(value) for value in App.Version()]
    result.update({
        "ok": True,
        "freecad_version": ".".join(version[:3]),
        "revision": version[3] if len(version) > 3 else "",
        "python_version": sys.version.split()[0],
        "occ_version": str(getattr(Part, "OCC_VERSION", "")),
    })
except Exception as exc:
    result["exception"] = "%s: %s" % (type(exc).__name__, exc)
print("MEP_CFD_FREECAD_STAGE:" + json.dumps(result, ensure_ascii=True))
''',
    "boolean": r'''
import json
result = {"stage": "boolean", "ok": False}
try:
    import FreeCAD as App
    import Part
    room = Part.makeBox(10000.0, 8000.0, 3000.0)
    column = Part.makeBox(500.0, 500.0, 3000.0, App.Vector(1000.0, 1000.0, 0.0))
    cut = room.cut(column)
    solids = list(cut.Solids)
    solid = solids[0] if len(solids) == 1 else cut
    expected = 239250000000.0
    volume = float(solid.Volume)
    rel_error = abs(volume - expected) / expected
    result.update({
        "valid": bool(cut.isValid()), "solid_count": len(solids),
        "volume_mm3": volume, "relative_volume_error": rel_error,
    })
    result["ok"] = bool(result["valid"] and len(solids) == 1 and volume > 0 and rel_error <= 1e-9)
except Exception as exc:
    result["exception"] = "%s: %s" % (type(exc).__name__, exc)
print("MEP_CFD_FREECAD_STAGE:" + json.dumps(result, ensure_ascii=True))
''',
    "tessellation": r'''
import json
result = {"stage": "tessellation", "ok": False}
try:
    import Part
    shape = Part.makeBox(1000.0, 1000.0, 1000.0)
    vertices, facets = shape.tessellate(100.0)
    result.update({"vertices": len(vertices), "facets": len(facets)})
    result["ok"] = bool(vertices and facets)
except Exception as exc:
    result["exception"] = "%s: %s" % (type(exc).__name__, exc)
print("MEP_CFD_FREECAD_STAGE:" + json.dumps(result, ensure_ascii=True))
''',
}


def _version_key(path):
    """Sort standard installs by the version embedded in their parent path."""
    parent = os.path.basename(os.path.dirname(os.path.dirname(path)))
    return tuple(int(n) for n in re.findall(r"\d+", parent)) or (0,)


def _candidate_paths():
    """Yield auto-discovered FreeCADCmd candidates in precedence order."""
    for command in ("FreeCADCmd.exe", "freecadcmd.exe", "FreeCADCmd", "freecadcmd"):
        found = shutil.which(command)
        if found:
            yield found, "path"

    roots = []
    for key in ("ProgramFiles", "ProgramFiles(x86)"):
        if os.environ.get(key):
            roots.append(os.environ[key])
    roots.extend([r"C:\Program Files", r"C:\Program Files (x86)"])
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(os.path.join(local, "Programs"))

    standard = []
    for root in roots:
        standard.extend(glob.glob(os.path.join(root, "FreeCAD*", "bin", "FreeCADCmd.exe")))
        standard.extend(glob.glob(os.path.join(root, "FreeCAD*", "bin", "freecadcmd.exe")))
    for path in sorted(standard, key=lambda value: (_version_key(value), value), reverse=True):
        yield path, "standard"


def select_freecadcmd(explicit=None):
    """Return ``(absolute_path, selection)`` or ``("", "missing")``."""
    requested = []
    if explicit:
        requested.append((explicit, "explicit", "explicit_missing"))
    else:
        configured = os.environ.get(FREECAD_EXE_ENV, "").strip()
        if configured:
            requested.append((configured, "configured", "configured_missing"))
    for candidate, selection, missing_selection in requested:
        path = os.path.abspath(os.path.expandvars(os.path.expanduser(candidate)))
        if os.path.isfile(path):
            return os.path.realpath(path), selection
        return "", missing_selection

    seen = set()
    for candidate, selection in _candidate_paths():
        path = os.path.abspath(os.path.expandvars(os.path.expanduser(candidate)))
        if not os.path.isfile(path):
            continue
        path = os.path.realpath(path)
        key = os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        return path, selection
    return "", "missing"


def find_freecadcmd(explicit=None):
    """Compatibility helper used by the legacy MEP GUI."""
    return select_freecadcmd(explicit)[0] or None


def freecad_headless_command(executable, script_path, job_dir):
    """Build an isolated FreeCADCmd command for a single job."""
    os.makedirs(job_dir, exist_ok=True)
    return [
        os.path.abspath(executable),
        "--console",
        "--safe-mode",
        "-u", os.path.join(job_dir, "user.cfg"),
        "-s", os.path.join(job_dir, "system.cfg"),
        "--log-file", os.path.join(job_dir, "freecad.log"),
        os.path.abspath(script_path),
    ]


def _file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stage_rows():
    return [
        {"id": stage, "status": "NOT_RUN", "reason_code": "", "details": {}}
        for stage in ("discovery", "imports", "boolean", "tessellation")
    ]


def _stage_payload(stdout, expected_stage):
    payload = None
    for line in (stdout or "").splitlines():
        if line.startswith(_STAGE_MARKER):
            try:
                candidate = json.loads(line[len(_STAGE_MARKER):])
            except (TypeError, ValueError):
                candidate = None
            if isinstance(candidate, dict):
                payload = candidate
    if payload is None or payload.get("stage") != expected_stage:
        return None
    return payload


def _stage_payload_invariants(stage_name, payload):
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        return False
    if stage_name == "imports":
        return True
    if stage_name == "boolean":
        volume = payload.get("volume_mm3")
        declared_error = payload.get("relative_volume_error")
        if (
            payload.get("valid") is not True
            or payload.get("solid_count") != 1
            or isinstance(volume, bool)
            or not isinstance(volume, (int, float))
            or not math.isfinite(float(volume))
            or float(volume) <= 0
            or isinstance(declared_error, bool)
            or not isinstance(declared_error, (int, float))
            or not math.isfinite(float(declared_error))
        ):
            return False
        expected = 239250000000.0
        recomputed_error = abs(float(volume) - expected) / expected
        return bool(
            math.isclose(
                float(declared_error), recomputed_error, rel_tol=0.0, abs_tol=1e-15
            )
            and recomputed_error <= 1e-9
        )
    if stage_name == "tessellation":
        vertices = payload.get("vertices")
        facets = payload.get("facets")
        return bool(
            isinstance(vertices, int) and not isinstance(vertices, bool) and vertices > 0
            and isinstance(facets, int) and not isinstance(facets, bool) and facets > 0
        )
    return False


def diagnose_freecad_stages(executable: Path, *, per_stage_timeout_s: float) -> dict:
    """Run bounded FreeCAD discovery/import/Boolean/tessellation diagnostics.

    Each runtime operation uses a fresh, isolated ``FreeCADCmd`` process.  The
    result names the first failed stage and never promotes an unavailable or
    unsupported runtime to ready.
    """
    try:
        timeout = float(per_stage_timeout_s)
    except (TypeError, ValueError):
        timeout = 0.0
    if timeout <= 0 or not math.isfinite(timeout):
        raise ValueError("FREECAD_STAGE_TIMEOUT_INVALID")

    checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    stages = _stage_rows()
    requested = os.fspath(executable) if executable is not None else None
    requested_path = (
        os.path.abspath(os.path.expandvars(os.path.expanduser(requested)))
        if requested else ""
    )
    if requested_path and os.path.isfile(requested_path):
        path, selection = os.path.realpath(requested_path), "explicit"
    else:
        path, selection = "", "explicit_missing"
    result = {
        "schema_version": 1,
        "contract": "freecad_staged_diagnostics.v1",
        "checked_at": checked_at,
        "run_id": uuid.uuid4().hex,
        "ok": False,
        "status": "missing",
        "failed_stage": "discovery",
        "summary": "지정한 FreeCADCmd 실행 파일을 찾지 못했습니다.",
        "fix": (
            "FreeCAD 1.1.1을 설치하고 MEP_CFD_FREECADCMD에 "
            "FreeCADCmd.exe 절대경로를 지정하세요."
        ),
        "selection": selection,
        "executable": path,
        "executable_sha256": "",
        "freecad_version": "",
        "revision": "",
        "python_version": "",
        "occ_version": "",
        "compatible_profile": "",
        "stages": stages,
    }
    if not path:
        stages[0].update(status="BLOCKED", reason_code="FREECAD_EXECUTABLE_MISSING")
        return result

    try:
        is_python = os.path.samefile(path, sys.executable)
    except OSError:
        is_python = False
    if Path(path).name.casefold() not in {"freecadcmd", "freecadcmd.exe"} or is_python:
        stages[0].update(
            status="BLOCKED", reason_code="FREECAD_EXECUTABLE_IDENTITY_INVALID"
        )
        result.update(
            status="identity_invalid",
            summary="지정한 실행 파일이 정규 FreeCADCmd 실행 파일이 아닙니다.",
            fix="FreeCAD 설치의 bin/FreeCADCmd.exe 절대경로를 지정하세요.",
        )
        return result

    try:
        executable_sha256 = _file_sha256(path)
    except OSError as exc:
        stages[0].update(
            status="BLOCKED", reason_code="FREECAD_EXECUTABLE_UNREADABLE",
            details={"error": str(exc)},
        )
        result.update(
            status="unreadable", summary="FreeCADCmd 실행 파일을 읽지 못했습니다.",
            fix="실행 파일 권한과 보안 프로그램 차단 여부를 확인하세요.",
        )
        return result
    stages[0].update(status="PASS", details={"selection": selection})
    result.update(executable=path, executable_sha256=executable_sha256)

    stage_indexes = {"imports": 1, "boolean": 2, "tessellation": 3}
    with tempfile.TemporaryDirectory(prefix="mep_cfd_freecad_stages_") as root:
        for stage_name in ("imports", "boolean", "tessellation"):
            row = stages[stage_indexes[stage_name]]
            job_dir = os.path.join(root, stage_name)
            os.makedirs(job_dir, exist_ok=True)
            script_path = os.path.join(job_dir, f"{stage_name}.py")
            with open(script_path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(_FREECAD_STAGE_SCRIPTS[stage_name])
            command = freecad_headless_command(path, script_path, job_dir)
            try:
                proc = subprocess.run(
                    command,
                    cwd=job_dir,
                    env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except subprocess.TimeoutExpired:
                code = f"FREECAD_{stage_name.upper()}_TIMEOUT"
                row.update(status="BLOCKED", reason_code=code)
                result.update(
                    status="timeout", failed_stage=stage_name,
                    summary=f"FreeCAD {stage_name} 진단이 제한시간을 초과했습니다.",
                    fix="FreeCAD 프로세스를 종료한 뒤 해당 단계 진단을 다시 실행하세요.",
                )
                return result
            except OSError as exc:
                row.update(
                    status="BLOCKED", reason_code="FREECAD_STAGE_LAUNCH_FAILED",
                    details={"error": str(exc)},
                )
                result.update(
                    status="launch_failed", failed_stage=stage_name,
                    summary=f"FreeCAD {stage_name} 진단을 실행하지 못했습니다.",
                    fix="실행 파일 권한과 보안 프로그램 차단 여부를 확인하세요.",
                )
                return result

            payload = _stage_payload(proc.stdout, stage_name)
            if proc.returncode != 0 or not _stage_payload_invariants(stage_name, payload):
                row.update(
                    status="BLOCKED", reason_code=f"FREECAD_{stage_name.upper()}_FAILED",
                    details={
                        "returncode": proc.returncode,
                        "exception": str((payload or {}).get("exception") or ""),
                    },
                )
                result.update(
                    status="stage_failed", failed_stage=stage_name,
                    summary=f"FreeCAD {stage_name} 진단이 실패했습니다.",
                    fix="FreeCAD 1.1.1 설치를 복구하고 해당 단계 진단을 다시 실행하세요.",
                )
                return result

            if stage_name == "imports":
                required = (
                    "FreeCAD", "Part", "Draft", "Arch", "Mesh", "MeshPart",
                    "BOPTools.SplitAPI",
                )
                modules = payload.get("modules") if isinstance(payload.get("modules"), dict) else {}
                supported = (
                    payload.get("freecad_version") == SUPPORTED_FREECAD
                    and str(payload.get("occ_version") or "").startswith(SUPPORTED_OCC)
                    and all(modules.get(name) is True for name in required)
                )
                result.update(
                    freecad_version=str(payload.get("freecad_version") or ""),
                    revision=str(payload.get("revision") or ""),
                    python_version=str(payload.get("python_version") or ""),
                    occ_version=str(payload.get("occ_version") or ""),
                )
                if not supported:
                    row.update(status="BLOCKED", reason_code="FREECAD_UNSUPPORTED_PROFILE", details=payload)
                    result.update(
                        status="unsupported_version", failed_stage="imports",
                        summary="FreeCAD 또는 OCC 버전/필수 모듈이 검증 프로필과 다릅니다.",
                        fix=(
                            f"FreeCAD {SUPPORTED_FREECAD} / OCC {SUPPORTED_OCC} 설치를 "
                            "사용하고 다시 검사하세요."
                        ),
                    )
                    return result
            row.update(status="PASS", details=payload)

    result.update(
        ok=True, status="ready", failed_stage=None,
        summary="FreeCAD 단계별 형상 환경이 준비되었습니다.", fix="",
        compatible_profile="freecad-1.1.1-occ-7.8.1",
    )
    return result


def _error_detail(proc, log_path):
    parts = []
    if proc is not None:
        if proc.returncode:
            parts.append(f"exit={proc.returncode}")
        if proc.stderr:
            parts.append(proc.stderr.strip()[-2000:])
        if proc.stdout and _PROBE_MARKER not in proc.stdout:
            parts.append(proc.stdout.strip()[-1000:])
    try:
        with open(log_path, encoding="utf-8", errors="replace") as handle:
            log_tail = handle.read()[-2000:].strip()
        if log_tail:
            parts.append(log_tail)
    except OSError:
        pass
    return "\n".join(part for part in parts if part)[:4000]


def diagnose_freecad(executable=None, timeout=60):
    """Probe the pinned Windows FreeCAD runtime and run an OCC Boolean smoke."""
    checked_at = datetime.now(timezone.utc).isoformat()
    path, selection = select_freecadcmd(executable)
    base = {
        "schema_version": 1,
        "checked_at": checked_at,
        "ok": False,
        "status": "missing",
        "summary": "FreeCADCmd를 찾지 못했습니다.",
        "fix": (
            "FreeCAD 1.1.1을 설치하거나 환경변수 "
            f"`{FREECAD_EXE_ENV}`에 FreeCADCmd.exe 경로를 지정하세요."
        ),
        "selection": selection,
        "executable": path,
        "freecad_version": "",
        "revision": "",
        "python_version": "",
        "occ_version": "",
        "compatible_profile": "",
        "modules": {},
        "smoke": {},
        "error_detail": "",
    }
    if not path:
        if selection in ("explicit_missing", "configured_missing"):
            base["summary"] = "지정한 FreeCADCmd 경로에 실행 파일이 없습니다."
            base["fix"] = (
                f"`{FREECAD_EXE_ENV}` 값 또는 지정 경로를 올바른 "
                "FreeCADCmd.exe 절대경로로 수정하세요."
            )
        return base

    proc = None
    with tempfile.TemporaryDirectory(prefix="mep_cfd_freecad_probe_") as job_dir:
        script_path = os.path.join(job_dir, "probe.py")
        with open(script_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(_FREECAD_PROBE)
        command = freecad_headless_command(path, script_path, job_dir)
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.run(
                command,
                cwd=job_dir,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=creationflags,
            )
        except subprocess.TimeoutExpired as exc:
            return dict(
                base,
                status="timeout",
                summary="FreeCAD headless 진단이 제한시간을 초과했습니다.",
                fix="FreeCAD 프로세스를 종료한 뒤 환경 검사를 다시 실행하세요.",
                error_detail=str(exc),
            )
        except OSError as exc:
            return dict(
                base,
                status="launch_failed",
                summary="FreeCADCmd를 실행하지 못했습니다.",
                fix="실행 파일 권한과 보안 프로그램 차단 여부를 확인하세요.",
                error_detail=str(exc),
            )

        payload = None
        for line in (proc.stdout or "").splitlines():
            if line.startswith(_PROBE_MARKER):
                try:
                    payload = json.loads(line[len(_PROBE_MARKER):])
                except (ValueError, TypeError):
                    payload = None
        error_detail = (
            _error_detail(proc, os.path.join(job_dir, "freecad.log"))
            if proc.returncode != 0 or not isinstance(payload, dict)
            else str(payload.get("exception") or "")
        )

    if proc.returncode != 0 or not isinstance(payload, dict):
        return dict(
            base,
            status="probe_failed",
            summary="FreeCAD가 실행됐지만 진단 결과를 만들지 못했습니다.",
            fix="FreeCAD 설치를 복구한 뒤 환경 검사를 다시 실행하세요.",
            error_detail=error_detail or "capability marker missing",
        )

    freecad_version = str(payload.get("freecad_version") or "")
    occ_version = str(payload.get("occ_version") or "")
    modules = payload.get("modules") if isinstance(payload.get("modules"), dict) else {}
    smoke = payload.get("smoke") if isinstance(payload.get("smoke"), dict) else {}
    required_modules = (
        "FreeCAD", "Part", "Draft", "Arch", "Mesh", "MeshPart", "BOPTools.SplitAPI"
    )
    missing_modules = [name for name in required_modules if not modules.get(name)]
    supported = (
        freecad_version == SUPPORTED_FREECAD and occ_version.startswith(SUPPORTED_OCC)
    )
    common = dict(
        base,
        freecad_version=freecad_version,
        revision=str(payload.get("revision") or ""),
        python_version=str(payload.get("python_version") or ""),
        occ_version=occ_version,
        modules=modules,
        smoke=smoke,
        error_detail=error_detail or str(payload.get("exception") or ""),
    )

    if missing_modules or not smoke.get("ok"):
        reason = (
            "필수 모듈 누락: " + ", ".join(missing_modules)
            if missing_modules else "OCC Boolean/tessellation 스모크 실패"
        )
        return dict(
            common,
            status="smoke_failed",
            summary=f"FreeCAD 형상 기능을 사용할 수 없습니다. {reason}",
            fix="FreeCAD 1.1.1 설치를 복구하고 환경 검사를 다시 실행하세요.",
        )
    if not supported:
        shown = f"FreeCAD {freecad_version or '?'} / OCC {occ_version or '?'}"
        return dict(
            common,
            status="unsupported_version",
            summary=f"{shown}은 아직 검증된 프로필이 아닙니다.",
            fix=(
                f"현재 검증 기준은 FreeCAD {SUPPORTED_FREECAD} / OCC "
                f"{SUPPORTED_OCC}입니다. 골든 케이스를 통과한 뒤 지원 프로필을 추가하세요."
            ),
        )
    return dict(
        common,
        ok=True,
        status="ready",
        summary="FreeCAD headless 형상 환경이 준비되었습니다.",
        fix="",
        compatible_profile="freecad-1.1.1-occ-7.8.1",
    )
