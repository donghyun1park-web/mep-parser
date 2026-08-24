"""Restartable body-fitted thermal mesh-uncertainty orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import uuid

import cfd_gci
import cfd_mesh
import cfd_occ
import cfd_physics
import cfd_power
import cfd_report


DEFAULT_THREE_GRID_LEVELS = (
    ("coarse", 0.350),
    ("medium", 0.243),
    ("fine", 0.169),
)
DEFAULT_FOUR_GRID_LEVELS = (
    ("very_coarse", 0.504),
    *DEFAULT_THREE_GRID_LEVELS,
)
MAX_CONTINUATION_RUNS = 200


def _now():
    return datetime.now(timezone.utc).isoformat()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def bounded_live_progress(job, projects_root, now=None):
    """Estimate progress between saved thermal checkpoints without overrunning one."""
    stage = str((job or {}).get("stage") or "")
    if not stage.endswith(":thermal_continue"):
        return None
    level_name = stage.split(":", 1)[0]
    level = next(
        (item for item in (job.get("levels") or [])
         if item.get("name") == level_name),
        None,
    )
    if level is None or not level.get("thermal_case"):
        return None
    projects = Path(projects_root).expanduser().resolve()
    case = Path(str(level["thermal_case"])).expanduser().resolve()
    try:
        case.relative_to((projects / "_body_solver").resolve())
    except (OSError, ValueError):
        return None
    try:
        progress = (_read(case / "run_manifest.json").get("thermal_progress")
                    or {})
        awaiting_sample = (
            progress.get("estimate_status") == "awaiting_continuation_sample"
        )
        checkpoint = max(
            float(level.get("latest_time_s") or 0.0),
            float(progress.get("latest_time_s") or 0.0),
        )
        rate = float(
            progress.get(
                "checkpoint_rate_seconds_per_simulated_second"
                if awaiting_sample
                else "last_solver_runtime_per_simulated_second"
            ) or 0.0
        )
        flow_time = float(progress.get("flow_through_time_s") or 0.0)
        required = float(progress.get("required_duration_s") or 0.0)
        duration = float(progress.get("recommended_next_duration_s") or 0.0)
        updated = datetime.fromisoformat(str(
            level.get("stage_started_at") or job.get("updated_at") or ""
        ))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        elapsed = max(0.0, (current - updated).total_seconds())
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if checkpoint <= 0 or rate <= 0 or flow_time <= 0 or required <= 0 or duration <= 0:
        return None
    try:
        fixed = max(
            0.0,
            float(progress.get("last_fixed_runtime_overhead_seconds") or 0.0),
        )
    except (ValueError, TypeError):
        fixed = 0.0
    solver_elapsed = max(0.0, elapsed - min(60.0, fixed * 0.5))
    next_checkpoint = min(required, checkpoint + duration)
    estimated_time = min(next_checkpoint, checkpoint + solver_elapsed / rate)
    remaining = progress.get("estimated_remaining_runtime_seconds")
    try:
        estimated_remaining = (
            max(0.0, float(remaining) - elapsed)
            if remaining is not None else None
        )
    except (ValueError, TypeError):
        estimated_remaining = None
    return {
        "level": level_name,
        "checkpoint_time_s": checkpoint,
        "estimated_time_s": estimated_time,
        "estimated_flow_through_fraction": estimated_time / flow_time,
        "next_checkpoint_time_s": next_checkpoint,
        "target_time_s": required,
        "estimated_remaining_runtime_seconds": estimated_remaining,
        "estimate_basis": (
            "initial_stability_scaled" if awaiting_sample
            else "measured_continuation"
        ),
        "as_of": current.isoformat(),
        "is_estimate": True,
    }


def _atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", delete=False,
            dir=path.parent, prefix="." + path.name + ".", suffix=".tmp") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def _safe_stem(path):
    stem = re.sub(r"[^\w가-힣.\- ]+", "_", Path(path).stem).strip(" ._-")
    return stem[:48] or "drawing"


def _job_path(root, study_id):
    return Path(root).expanduser().resolve() / "_body_gci" / study_id / "gci_job.json"


def _pid_is_alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) is not a portable existence probe on Windows.  Query
        # a minimal process handle instead; access denied also proves that the
        # PID exists but belongs to a process we cannot inspect.
        import ctypes

        process_query_limited_information = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(
            process_query_limited_information, False, pid
        )
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return ctypes.get_last_error() == 5
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _acquire_run_lock(job_path, lock_name="gci_job.lock"):
    """Atomically claim one study across Studio, CLI, and recovery processes."""
    lock_path = Path(job_path).with_name(lock_name)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    payload = {
        "schema_version": 1,
        "contract": "gci_job_lock.v1",
        "pid": os.getpid(),
        "started_at": _now(),
        "token": token,
    }
    for _ in range(2):
        try:
            descriptor = os.open(
                lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
        except FileExistsError:
            try:
                owner = _read(lock_path)
            except (OSError, ValueError, json.JSONDecodeError):
                owner = {}
            if _pid_is_alive(owner.get("pid")):
                return None, owner
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                return None, owner
            continue
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        return token, payload
    return None, {}


def _release_run_lock(job_path, token, lock_name="gci_job.lock"):
    lock_path = Path(job_path).with_name(lock_name)
    try:
        owner = _read(lock_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return
    if owner.get("token") != token:
        return
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


def acquire_job_lock(job_path, lock_name="gci_job.lock"):
    """Public cross-process lock used by other persistent CFD job types."""
    return _acquire_run_lock(job_path, lock_name=lock_name)


def release_job_lock(job_path, token, lock_name="gci_job.lock"):
    """Release a lock previously returned by :func:`acquire_job_lock`."""
    _release_run_lock(job_path, token, lock_name=lock_name)


def _solver_lock_manifest(root):
    return Path(root).expanduser().resolve() / "_system" / "cfd_solver_job.json"


def acquire_solver_lock(root):
    """Claim the workstation-wide OpenFOAM slot across every job type."""
    return _acquire_run_lock(
        _solver_lock_manifest(root), lock_name="cfd_solver.lock"
    )


def release_solver_lock(root, token):
    _release_run_lock(
        _solver_lock_manifest(root), token, lock_name="cfd_solver.lock"
    )


def active_solver_lock(root):
    return active_job_lock(
        _solver_lock_manifest(root), lock_name="cfd_solver.lock"
    )


def load_study(root, study_id):
    if not re.fullmatch(r"gci-[0-9a-f]{12}", str(study_id or "")):
        return None
    path = _job_path(root, study_id)
    try:
        return _read(path) if path.is_file() else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def active_run_lock(root, study_id):
    """Return a live cross-process run owner, or None for absent/stale locks."""
    if not re.fullmatch(r"gci-[0-9a-f]{12}", str(study_id or "")):
        return None
    return active_job_lock(_job_path(root, study_id))


def active_job_lock(job_path, lock_name="gci_job.lock"):
    """Return the live owner of any persistent CFD job manifest."""
    lock_path = Path(job_path).with_name(lock_name)
    try:
        owner = _read(lock_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return owner if _pid_is_alive(owner.get("pid")) else None


def create_study(root, geometry_path, settings=None):
    """Create or load a deterministic project-local GCI automation job."""
    root = Path(root).expanduser().resolve()
    geometry = Path(geometry_path or "").expanduser().resolve()
    if not geometry.is_file():
        return {"ok": False, "error": f"geometry.json 파일이 없습니다: {geometry}"}
    try:
        json.loads(geometry.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"geometry.json을 읽지 못했습니다: {exc}"}

    requested = dict(settings or {})
    gci_contract = str(requested.get("gci_contract") or "grid_convergence.v3")
    if gci_contract not in (
            "grid_convergence.v1", "grid_convergence.v2", "grid_convergence.v3"):
        return {"ok": False, "error": f"지원하지 않는 GCI 계약입니다: {gci_contract}"}
    defaults = (DEFAULT_FOUR_GRID_LEVELS if gci_contract == "grid_convergence.v3"
                else DEFAULT_THREE_GRID_LEVELS)
    widths = requested.get("mesh_widths_m") or [row[1] for row in defaults]
    try:
        widths = [float(value) for value in widths]
    except (TypeError, ValueError):
        return {"ok": False, "error": "메시 크기는 숫자여야 합니다."}
    required_count = 4 if gci_contract == "grid_convergence.v3" else 3
    if len(widths) != required_count or not all(value > 0 for value in widths):
        return {"ok": False, "error": f"양수인 메시 크기 {required_count}개가 필요합니다."}
    widths = sorted(widths, reverse=True)
    if min(left / right for left, right in zip(widths, widths[1:])) < 1.1:
        return {"ok": False, "error": "명목 메시 세분비는 각 단계에서 1.10 이상이어야 합니다."}

    thermal_settings = dict(requested.get("thermal_settings") or {})
    if gci_contract in ("grid_convergence.v2", "grid_convergence.v3"):
        thermal_settings.setdefault(
            "thermal_minimum_flow_through_fraction",
            (cfd_gci.V3_MINIMUM_FLOW_THROUGH_FRACTION
             if gci_contract == "grid_convergence.v3"
             else cfd_gci.V2_MINIMUM_FLOW_THROUGH_FRACTION),
        )
        thermal_settings.setdefault("thermal_max_single_run_s", 20.0)
        # Override older seeded cases that persisted the former 0.1 s output
        # interval. Recovery keeps only representative snapshots per chunk,
        # so 2 s preserves the analysis window while avoiding disposable time
        # directory I/O.
        thermal_settings.setdefault("thermal_continuation_write_interval_s", 2.0)
    level_mesh_settings = requested.get("level_mesh_settings")
    if level_mesh_settings is None:
        # The coarsest global grid cannot represent the small round terminals
        # within the 5% patch-area gate at terminal level 2. Preserve boundary
        # geometry with one extra terminal-only level while leaving the global
        # grid family and all other local-refinement rules unchanged.
        level_mesh_settings = ({"very_coarse": {"terminal_level": 3}}
                               if gci_contract == "grid_convergence.v3" else {})
    if not isinstance(level_mesh_settings, dict) or any(
            not isinstance(value, dict) for value in level_mesh_settings.values()):
        return {"ok": False, "error": "수준별 메시 설정은 객체여야 합니다."}
    job_input = {
        "geometry_path": str(geometry),
        "geometry_sha256": _sha256(geometry),
        "gci_contract": gci_contract,
        "mesh_widths_m": widths,
        "mesh_settings": dict(requested.get("mesh_settings") or {}),
        "level_mesh_settings": level_mesh_settings,
        "isothermal_settings": dict(requested.get("isothermal_settings") or {}),
        "thermal_settings": thermal_settings,
    }
    study_id = "gci-" + _canonical_hash(job_input)[:12]
    path = _job_path(root, study_id)
    if path.is_file():
        existing = load_study(root, study_id)
        if existing is not None:
            return {"ok": True, "study": study_id, "manifest": existing,
                    "manifest_path": str(path), "existing": True}

    levels = []
    for (name, _), width in zip(defaults, widths):
        levels.append({
            "name": name, "background_cell_m": width,
            "status": "pending", "stage": "pending", "error": "",
            "mesh_case": "", "isothermal_case": "", "thermal_case": "",
            "cell_count": None, "latest_time_s": None,
            "flow_through_fraction": 0.0,
        })
    manifest = {
        "schema_version": 1,
        "contract": "gci_job.v1",
        "engine": "body_fitted_thermal_gci_job",
        "created_at": _now(), "updated_at": _now(),
        "study": study_id, "status": "queued", "stage": "queued",
        "attempts": 0, "error": "", "gate_status": None,
        "attempt_history": [], "resume_history": [],
        "last_attempt_elapsed_s": None,
        "total_elapsed_s": 0.0,
        "input": job_input, "occ_output": "", "levels": levels,
        "grid_convergence_path": "", "report_path": "",
    }
    _atomic_json(path, manifest)
    return {"ok": True, "study": study_id, "manifest": manifest,
            "manifest_path": str(path), "existing": False}


def list_studies(root):
    studies = []
    base = Path(root).expanduser().resolve() / "_body_gci"
    if base.is_dir():
        for path in base.glob("gci-*/gci_job.json"):
            try:
                manifest = _read(path)
                studies.append(manifest)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
    studies.sort(key=lambda row: row.get("updated_at") or "", reverse=True)
    return studies


def _valid_manifest(case, filename, predicate):
    path = Path(case) / filename
    try:
        value = _read(path)
        return value if predicate(value) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _has_positive_time(case):
    for path in Path(case).iterdir() if Path(case).is_dir() else ():
        if not path.is_dir():
            continue
        try:
            if float(path.name) > 0:
                return True
        except ValueError:
            continue
    return False


def _raw_thermal_analysis_complete(thermal_case, thermal_manifest,
                                   target_flow_fraction):
    """Return whether a finished thermal case is safe to expose as screening.

    This deliberately does not require ``design_ready``.  That flag includes
    numerical-quality and evidence gates that decide whether a result may be
    cited for design, whereas the field workflow must still be able to show a
    physically completed WARN run and hold it for that later review.
    """
    if not (Path(thermal_case) / "result_manifest.json").is_file():
        return False
    if str((thermal_manifest or {}).get("status") or "") not in ("PASS", "WARN"):
        return False
    if list((thermal_manifest or {}).get("errors") or []):
        return False
    solver = (thermal_manifest or {}).get("solver") or {}
    if isinstance(solver, dict) and solver.get("fatal"):
        return False
    progress = (thermal_manifest or {}).get("thermal_progress") or {}
    try:
        flow_fraction = float(progress.get("flow_through_fraction"))
    except (TypeError, ValueError):
        return False
    return (math.isfinite(flow_fraction)
            and flow_fraction + 1e-12 >= float(target_flow_fraction))


def _target_flow_through_fraction(job_input, default=0.25):
    """Return the finite completion target persisted with one job input."""
    value = ((job_input or {}).get("thermal_settings") or {}).get(
        "thermal_minimum_flow_through_fraction", default
    )
    try:
        value = float(value)
    except (TypeError, ValueError):
        return float(default)
    return value if math.isfinite(value) and value > 0 else float(default)


def _compatible_reuse_input(source, target):
    """Return true when two studies differ only in grid contract and widths."""
    keys = (
        "geometry_sha256", "mesh_settings", "isothermal_settings",
        "thermal_settings",
    )
    return all(source.get(key) == target.get(key) for key in keys)


def _compatible_seed_input(source, target):
    """Allow a shorter compatible thermal result to seed a longer v2 run."""
    if any(source.get(key) != target.get(key) for key in (
            "geometry_sha256", "mesh_settings", "isothermal_settings")):
        return False
    ignored = {
        "thermal_duration_s", "thermal_initial_delta_t_s",
        "thermal_max_delta_t_s", "thermal_write_interval_s",
        "thermal_minimum_flow_through_fraction", "thermal_max_single_run_s",
        "thermal_continuation_write_interval_s",
    }
    source_thermal = {
        key: value for key, value in (source.get("thermal_settings") or {}).items()
        if key not in ignored
    }
    target_thermal = {
        key: value for key, value in (target.get("thermal_settings") or {}).items()
        if key not in ignored
    }
    return source_thermal == target_thermal


def _effective_level_mesh_settings(job_input, level):
    """Return mesh controls that affect one level, excluding its width/name."""
    settings = dict(job_input.get("mesh_settings") or {})
    overrides = job_input.get("level_mesh_settings") or {}
    settings.update(overrides.get(str(level.get("name") or ""), {}) or {})
    return settings


def _validate_completed_level(level, *, target_flow_fraction=0.25):
    """Validate saved final artifacts and normalize the level checkpoint."""
    thermal_case = Path(level.get("thermal_case") or "")
    if not thermal_case.is_dir():
        return None
    try:
        run = _read(thermal_case / "run_manifest.json")
        if not _raw_thermal_analysis_complete(
                thermal_case, run, target_flow_fraction):
            return None
        loaded = cfd_gci.load_body_fitted_case(thermal_case)
    except (OSError, ValueError, TypeError, cfd_gci.GCIInputError):
        return None
    progress = run.get("thermal_progress") or {}
    raw_status = (
        "PASS" if (run.get("status") == "PASS" and run.get("design_ready"))
        else "WARN"
    )
    level.update(
        status=raw_status, stage="complete", error="",
        cell_count=int(loaded["cell_count"]),
        latest_time_s=progress.get("latest_time_s", loaded.get("time_s")),
        flow_through_fraction=float(progress.get("flow_through_fraction") or 0.0),
    )
    return thermal_case


def validate_completed_design_level(level, *, target_flow_fraction=3.0):
    """Public validator shared by single-grid field automation."""
    return _validate_completed_level(
        level, target_flow_fraction=target_flow_fraction
    )


def _reuse_compatible_level(root, manifest, level):
    """Reuse a validated width shared with an earlier compatible study."""
    target_fraction = _target_flow_through_fraction(manifest.get("input"))
    for candidate in list_studies(root):
        if candidate.get("study") == manifest.get("study"):
            continue
        if not _compatible_reuse_input(candidate.get("input") or {}, manifest["input"]):
            continue
        for saved in candidate.get("levels") or []:
            try:
                same_width = math.isclose(
                    float(saved.get("background_cell_m")),
                    float(level.get("background_cell_m")),
                    rel_tol=0.0,
                    abs_tol=1e-12,
                )
            except (TypeError, ValueError):
                same_width = False
            if not same_width:
                continue
            if _effective_level_mesh_settings(
                    candidate.get("input") or {}, saved
            ) != _effective_level_mesh_settings(manifest["input"], level):
                continue
            trial = dict(level)
            trial.update({
                key: saved.get(key) for key in (
                    "mesh_case", "isothermal_case", "thermal_case",
                    "cell_count", "latest_time_s", "flow_through_fraction",
                )
            })
            thermal_case = _validate_completed_level(
                trial, target_flow_fraction=target_fraction
            )
            if thermal_case is None:
                continue
            level.update(trial)
            level["reused_from_study"] = candidate.get("study")
            return thermal_case
    return None


def _seed_compatible_level(root, manifest, level):
    """Find a shorter validated result that can be cloned and continued."""
    target_fraction = _target_flow_through_fraction(manifest.get("input"))
    best = None
    for candidate in list_studies(root):
        if candidate.get("study") == manifest.get("study"):
            continue
        if not _compatible_seed_input(candidate.get("input") or {}, manifest["input"]):
            continue
        for saved in candidate.get("levels") or []:
            try:
                same_width = math.isclose(
                    float(saved.get("background_cell_m")),
                    float(level.get("background_cell_m")),
                    rel_tol=0.0, abs_tol=1e-12,
                )
            except (TypeError, ValueError):
                same_width = False
            if not same_width:
                continue
            if _effective_level_mesh_settings(
                    candidate.get("input") or {}, saved
            ) != _effective_level_mesh_settings(manifest["input"], level):
                continue
            trial = dict(saved)
            source_target = _target_flow_through_fraction(candidate.get("input"))
            thermal_case = _validate_completed_level(
                trial, target_flow_fraction=source_target
            )
            if thermal_case is None:
                continue
            source_fraction = float(trial.get("flow_through_fraction") or 0.0)
            if source_fraction + 1e-12 >= target_fraction:
                continue
            if best is None or source_fraction > best[0]:
                best = (source_fraction, candidate, trial, thermal_case)
    if best is None:
        return False
    _, candidate, saved, thermal_case = best
    level.update(
        mesh_case=saved.get("mesh_case") or "",
        isothermal_case=saved.get("isothermal_case") or "",
        seed_thermal_case=str(thermal_case),
        seeded_from_study=candidate.get("study"),
        seeded_flow_through_fraction=float(saved.get("flow_through_fraction") or 0.0),
    )
    return True


def _publish(job_path, manifest, callback=None, message=None):
    manifest["updated_at"] = _now()
    _atomic_json(job_path, manifest)
    if callback:
        payload = {
            "study": manifest["study"], "stage": manifest["stage"],
            "message": message or manifest["stage"],
        }
        if "levels" in manifest:
            payload["levels"] = manifest["levels"]
        if "level" in manifest:
            payload["level"] = manifest["level"]
        callback(payload)


def _set_level(manifest, level, stage, status="running", error=""):
    manifest["stage"] = f"{level['name']}:{stage}"
    level.pop("live_message", None)
    level.pop("live_updated_at", None)
    level.update(
        stage=stage, status=status, error=error, stage_started_at=_now()
    )


def _line_callback(callback, manifest, level, stage, job_path=None,
                   heartbeat_interval_s=5.0):
    last_heartbeat = [0.0]

    def emit(line):
        raw_message = str(line).strip()[-500:]
        time_match = re.fullmatch(r"Time\s*=\s*(\S+)", raw_message)
        if time_match and stage == "isothermal_run":
            live_message = f"등온 계산 반복 {time_match.group(1)}"
        elif time_match and stage.startswith("thermal_"):
            live_message = f"열·부력 물리시간 {time_match.group(1)}초"
        else:
            live_message = raw_message
        current = time.monotonic()
        if (job_path is not None
                and current - last_heartbeat[0] >= heartbeat_interval_s):
            level["live_message"] = live_message
            level["live_updated_at"] = _now()
            try:
                # A missing heartbeat must never abort a healthy solver.
                _publish(job_path, manifest)
            except OSError:
                pass
            else:
                last_heartbeat[0] = current
        if callback:
            payload = {
                "study": manifest["study"],
                "stage": f"{level['name']}:{stage}",
                "message": raw_message,
            }
            if "levels" in manifest:
                payload["levels"] = manifest["levels"]
            if "level" in manifest:
                payload["level"] = manifest["level"]
            callback(payload)
    return emit


def run_thermal_design_level(root, occ_output, manifest, level, job_path,
                             callback=None, case_prefix=None):
    """Run or resume one detailed mesh-to-thermal design level.

    GCI studies and single-grid field jobs share this exact validated stage
    chain so the easier UI cannot silently bypass mesh or solver gates.
    """
    token = manifest["study"].split("-", 1)[-1]
    base_name = case_prefix or (
        f"{Path(occ_output).name}-gci-{token}-{level['name']}"
    )
    mesh_case = Path(level.get("mesh_case") or (root / "_body_mesh" / base_name))
    iso_case = Path(level.get("isothermal_case") or (
        root / "_body_solver" / (base_name + "-isothermal")
    ))
    thermal_case = root / "_body_solver" / (base_name + "-thermal")
    level.update(mesh_case=str(mesh_case), isothermal_case=str(iso_case),
                 thermal_case=str(thermal_case))

    mesh_manifest = _valid_manifest(
        mesh_case, "mesh_manifest.json", lambda row: (
            row.get("status") == "PASS"
            and (mesh_case / "constant" / "polyMesh").is_dir()
        )
    )
    if mesh_manifest is None:
        _set_level(manifest, level, "mesh_build")
        _publish(job_path, manifest, callback, f"{level['name']} 메시 입력 생성")
        mesh_settings = dict(manifest["input"].get("mesh_settings") or {})
        mesh_settings.update(
            (manifest["input"].get("level_mesh_settings") or {}).get(
                level["name"], {}
            )
        )
        mesh_settings.update({
            "preset": "detailed",
            "background_cell_m": level["background_cell_m"],
        })
        built = cfd_mesh.build_mesh_case(occ_output, mesh_case, settings=mesh_settings)
        if not built.get("ok"):
            raise RuntimeError(built.get("error") or f"{level['name']} 메시 입력 생성 실패")
        _set_level(manifest, level, "mesh_run")
        _publish(job_path, manifest, callback, f"{level['name']} 메시 계산")
        result = cfd_mesh.run_mesh_case(
            mesh_case, progress_cb=_line_callback(
                callback, manifest, level, "mesh_run", job_path
            )
        )
        if not result.get("ok"):
            raise RuntimeError(result.get("error") or f"{level['name']} 메시 gate 실패")
        mesh_manifest = result.get("manifest") or _read(mesh_case / "mesh_manifest.json")
    level["cell_count"] = int((mesh_manifest.get("mesh") or {}).get("cells") or 0)
    _publish(job_path, manifest, callback, f"{level['name']} 메시 PASS")

    iso_manifest = _valid_manifest(
        iso_case, "run_manifest.json", lambda row: (
            row.get("status") != "FAIL" and _has_positive_time(iso_case)
        )
    )
    if iso_manifest is None:
        _set_level(manifest, level, "isothermal_build")
        _publish(job_path, manifest, callback, f"{level['name']} 등온 초기장 생성")
        built = cfd_physics.build_isothermal_case(
            mesh_case, iso_case,
            settings=manifest["input"].get("isothermal_settings") or None,
        )
        if not built.get("ok"):
            raise RuntimeError(built.get("error") or f"{level['name']} 등온 입력 생성 실패")
        _set_level(manifest, level, "isothermal_run")
        _publish(job_path, manifest, callback, f"{level['name']} 등온 계산")
        result = cfd_physics.run_isothermal_case(
            iso_case, progress_cb=_line_callback(
                callback, manifest, level, "isothermal_run", job_path
            )
        )
        if not result.get("ok"):
            raise RuntimeError(result.get("error") or f"{level['name']} 등온 gate 실패")
        iso_manifest = result.get("manifest") or _read(iso_case / "run_manifest.json")
    _publish(job_path, manifest, callback, f"{level['name']} 등온 초기장 준비")

    thermal_settings = {
        "thermal_duration_s": 0.05,
        "thermal_initial_delta_t_s": 0.0001,
        "thermal_max_delta_t_s": 0.0005,
        "thermal_write_interval_s": 0.01,
    }
    thermal_settings.update(manifest["input"].get("thermal_settings") or {})
    target_flow_fraction = float(thermal_settings.get(
        "thermal_minimum_flow_through_fraction", 0.25
    ))

    seed_case = Path(level.get("seed_thermal_case") or "")
    if (not thermal_case.exists() and seed_case.is_dir()
            and (seed_case / "run_manifest.json").is_file()):
        _set_level(manifest, level, "thermal_seed")
        _publish(
            job_path, manifest, callback,
            f"{level['name']} 이전 열 결과 복제 후 이어 계산",
        )
        shutil.copytree(seed_case, thermal_case)
        thermal_input_path = thermal_case / "thermal_input.json"
        thermal_input = _read(thermal_input_path)
        thermal_input.setdefault("settings", {}).update(
            manifest["input"].get("thermal_settings") or {}
        )
        _atomic_json(thermal_input_path, thermal_input)

    thermal_manifest = _valid_manifest(
        thermal_case, "run_manifest.json", lambda row: row.get("status") != "FAIL"
    )
    if thermal_manifest is None:
        _set_level(manifest, level, "thermal_build")
        _publish(job_path, manifest, callback, f"{level['name']} 열·부력 입력 생성")
        built = cfd_physics.build_buoyant_case(
            mesh_case, thermal_case, settings=thermal_settings,
            initial_case_dir=iso_case,
        )
        if not built.get("ok"):
            raise RuntimeError(built.get("error") or f"{level['name']} 열·부력 입력 생성 실패")
        _set_level(manifest, level, "thermal_initial")
        _publish(job_path, manifest, callback, f"{level['name']} 열 안정성 계산")
        result = cfd_physics.run_buoyant_case(
            thermal_case,
            progress_cb=_line_callback(
                callback, manifest, level, "thermal_initial", job_path
            ),
        )
        if not result.get("ok"):
            raise RuntimeError(result.get("error") or f"{level['name']} 열 안정성 gate 실패")
        thermal_manifest = result.get("manifest") or _read(thermal_case / "run_manifest.json")

    for _ in range(MAX_CONTINUATION_RUNS):
        progress = thermal_manifest.get("thermal_progress") or {}
        level["latest_time_s"] = progress.get("latest_time_s")
        level["flow_through_fraction"] = float(progress.get("flow_through_fraction") or 0.0)
        if _raw_thermal_analysis_complete(
                thermal_case, thermal_manifest, target_flow_fraction):
            break
        flow_time = float(progress.get("flow_through_time_s") or 0.0)
        latest_time = float(progress.get("latest_time_s") or 0.0)
        target_remaining = max(0.0, flow_time * target_flow_fraction - latest_time)
        remaining = max(
            float(progress.get("remaining_duration_s") or 0.0), target_remaining
        )
        recommended = float(progress.get("recommended_next_duration_s") or 0.0)
        if recommended <= 0 and remaining > 1e-9:
            recommended = min(remaining, 5.0)
        if remaining <= 1e-9 or recommended <= 0:
            warnings = ", ".join(thermal_manifest.get("warnings") or [])
            raise RuntimeError(
                f"{level['name']} 목표시간 도달 후에도 설계 gate 미통과: {warnings or '원인 미상'}"
            )
        duration = min(remaining, recommended)
        _set_level(manifest, level, "thermal_continue")
        _publish(
            job_path, manifest, callback,
            f"{level['name']} 열 계산 {level['latest_time_s'] or 0:.3f}s, 다음 {duration:.3f}s",
        )
        continuation_settings = dict(manifest["input"].get("thermal_settings") or {})
        continuation_settings["thermal_duration_s"] = duration
        result = cfd_physics.run_buoyant_continuation(
            thermal_case, settings=continuation_settings,
            progress_cb=_line_callback(
                callback, manifest, level, "thermal_continue", job_path
            ),
        )
        if not result.get("ok"):
            raise RuntimeError(result.get("error") or f"{level['name']} 열 이어 계산 실패")
        thermal_manifest = result.get("manifest") or _read(thermal_case / "run_manifest.json")
    else:
        raise RuntimeError(f"{level['name']} 열 이어 계산 횟수가 안전 한도를 넘었습니다.")

    progress = thermal_manifest.get("thermal_progress") or {}
    raw_status = (
        "PASS" if (thermal_manifest.get("status") == "PASS"
                   and thermal_manifest.get("design_ready"))
        else "WARN"
    )
    level.update(
        status=raw_status, stage="complete", error="",
        latest_time_s=progress.get("latest_time_s"),
        flow_through_fraction=float(progress.get("flow_through_fraction") or 0.0),
    )
    cfd_report.generate_body_fitted_report(thermal_case)
    completion_label = (
        "설계 열 결과 PASS" if thermal_manifest.get("design_ready")
        else "열 해석 완료 - 설계 인용 검토 보류"
    )
    _publish(job_path, manifest, callback, f"{level['name']} {completion_label}")
    return thermal_case


def _run_study_unlocked(root, study_id, callback=None):
    """Run or resume every unfinished stage and publish the final GCI gate."""
    root = Path(root).expanduser().resolve()
    job_path = _job_path(root, study_id)
    manifest = load_study(root, study_id)
    if manifest is None:
        return {"ok": False, "error": "GCI 자동 작업을 찾을 수 없습니다."}
    attempt_started = _now()
    started = time.monotonic()
    previous_status = str(manifest.get("status") or "")
    previous_attempts = int(manifest.get("attempts") or 0)
    if previous_attempts > 0 and previous_status in ("running", "FAIL"):
        resume_history = list(manifest.get("resume_history") or [])
        resume_history.append({
            "resumed_at": attempt_started,
            "previous_status": previous_status,
            "previous_stage": str(manifest.get("stage") or ""),
            "previous_attempt": previous_attempts,
            "completed_levels": [
                row.get("name") for row in manifest.get("levels") or []
                if row.get("status") == "PASS" and row.get("stage") == "complete"
            ],
            "checkpoint_times_s": {
                str(row.get("name")): row.get("latest_time_s")
                for row in manifest.get("levels") or []
                if row.get("latest_time_s") is not None
            },
        })
        manifest["resume_history"] = resume_history
    manifest.update(status="running", stage="starting", error="",
                    attempts=int(manifest.get("attempts") or 0) + 1,
                    attempt_started_at=attempt_started)
    _publish(job_path, manifest, callback, "GCI 자동 작업 시작")
    try:
        geometry = Path(manifest["input"]["geometry_path"])
        if _sha256(geometry) != manifest["input"]["geometry_sha256"]:
            raise RuntimeError("작업 생성 후 geometry.json 내용이 변경되었습니다.")
        token = manifest["study"][4:]
        occ_output = Path(root, "_occ_geometry",
                          f"{_safe_stem(geometry)}-gci-{token}")
        if not cfd_occ.inspect_occ_output(occ_output).get("ok"):
            manifest["stage"] = "occ"
            _publish(job_path, manifest, callback, "공기영역 3D 형상 생성")
            result = cfd_occ.run_occ_job(geometry, occ_output)
            if not result.get("ok"):
                raise RuntimeError(result.get("error") or "OCC 공기영역 생성 실패")
        inspected = cfd_occ.inspect_occ_output(occ_output)
        if not inspected.get("ok"):
            raise RuntimeError(inspected.get("error") or "OCC 산출물 검증 실패")
        manifest["occ_output"] = str(occ_output)
        for level in manifest["levels"]:
            mesh_settings = dict(manifest["input"].get("mesh_settings") or {})
            mesh_settings.update(
                (manifest["input"].get("level_mesh_settings") or {}).get(
                    level["name"], {}
                )
            )
            mesh_settings.update({
                "preset": "detailed",
                "background_cell_m": level["background_cell_m"],
            })
            estimate = cfd_mesh.estimate_resources(
                inspected["manifest"], settings=mesh_settings
            )
            level["resource_estimate"] = {
                key: estimate.get(key) for key in (
                    "background_cells", "estimated_cells",
                    "estimated_ram_gb", "estimated_disk_gb",
                )
            }
        _publish(job_path, manifest, callback, "공기영역 3D 형상 준비")

        target_flow_fraction = _target_flow_through_fraction(manifest.get("input"))
        thermal_cases = []
        for level in manifest["levels"]:
            completed = _validate_completed_level(
                level, target_flow_fraction=target_flow_fraction
            )
            if completed is not None:
                thermal_cases.append(completed)
                _publish(job_path, manifest, callback,
                         f"{level['name']} 저장 결과 검증 및 재사용")
                continue
            reused = _reuse_compatible_level(root, manifest, level)
            if reused is not None:
                thermal_cases.append(reused)
                _publish(
                    job_path, manifest, callback,
                    f"{level['name']} 이전 작업 {level['reused_from_study']} 결과 재사용",
                )
                continue
            if _seed_compatible_level(root, manifest, level):
                _publish(
                    job_path, manifest, callback,
                    f"{level['name']} 이전 작업 {level['seeded_from_study']}에서 "
                    "이어 계산 준비",
                )
            try:
                thermal_cases.append(run_thermal_design_level(
                    root, occ_output, manifest, level, job_path, callback
                ))
            except Exception as exc:
                level.update(status="FAIL", error=str(exc))
                raise

        manifest["stage"] = "gci"
        _publish(job_path, manifest, callback,
                 f"{len(thermal_cases)}수준 메시 불확실성 계산")
        gci_contract = manifest["input"].get(
            "gci_contract", "grid_convergence.v1"
        )
        result = cfd_gci.build_grid_convergence(
            thermal_cases, job_path.parent / "grid_convergence.json",
            contract=gci_contract,
        )
        if not result.get("ok"):
            raise RuntimeError(result.get("error") or "GCI 계산 입력 검증 실패")
        report = cfd_report.generate_gci_report(job_path.parent)
        if not report.get("ok"):
            raise RuntimeError(report.get("error") or "GCI 보고서 생성 실패")
        gate_status = result["manifest"]["status"]
        elapsed = round(time.monotonic() - started, 3)
        history = list(manifest.get("attempt_history") or [])
        history.append({
            "attempt": manifest["attempts"], "started_at": attempt_started,
            "finished_at": _now(), "elapsed_s": elapsed, "status": "complete",
        })
        manifest.update(
            status="complete", stage="complete", gate_status=gate_status,
            error="", grid_convergence_path=str(job_path.parent / "grid_convergence.json"),
            report_path=report["path"], completed_at=_now(),
            last_attempt_elapsed_s=elapsed,
            total_elapsed_s=round(float(manifest.get("total_elapsed_s") or 0.0) + elapsed, 3),
            attempt_history=history,
        )
        _publish(job_path, manifest, callback, f"메시 독립성 {gate_status}")
        return {"ok": True, "study": study_id, "manifest": manifest,
                "gate": result["manifest"], "report_path": report["path"]}
    except Exception as exc:
        elapsed = round(time.monotonic() - started, 3)
        history = list(manifest.get("attempt_history") or [])
        history.append({
            "attempt": manifest["attempts"], "started_at": attempt_started,
            "finished_at": _now(), "elapsed_s": elapsed, "status": "FAIL",
            "error": str(exc),
        })
        manifest.update(
            status="FAIL", error=str(exc), failed_at=_now(),
            last_attempt_elapsed_s=elapsed,
            total_elapsed_s=round(float(manifest.get("total_elapsed_s") or 0.0) + elapsed, 3),
            attempt_history=history,
        )
        _publish(job_path, manifest, callback, f"자동 작업 중단: {exc}")
        return {"ok": False, "error": str(exc), "study": study_id,
                "manifest": manifest}


def _active_field_job_lock(root):
    """Find a live field runner created before the shared solver lock."""
    base = Path(root).expanduser().resolve() / "_field_jobs"
    if not base.is_dir():
        return None, None
    for path in base.glob("field-*/field_pipeline_job.json"):
        owner = active_job_lock(path)
        if owner is not None:
            return path.parent.name, owner
    return None, None


def _active_other_gci_lock(root, study_id):
    """Find a live older GCI runner that does not own the shared lock yet."""
    for row in list_studies(root):
        candidate = row.get("study")
        if candidate == study_id:
            continue
        owner = active_run_lock(root, candidate)
        if owner is not None:
            return candidate, owner
    return None, None


def run_study(root, study_id, callback=None):
    """Run one study while preventing cross-process duplicate execution."""
    root = Path(root).expanduser().resolve()
    job_path = _job_path(root, study_id)
    if load_study(root, study_id) is None:
        return {"ok": False, "error": "GCI 자동 작업을 찾을 수 없습니다."}
    other_study, other_owner = _active_other_gci_lock(root, study_id)
    if other_owner is not None:
        return {
            "ok": False, "code": "CFD_SOLVER_BUSY",
            "error": (f"메시 검증 작업 {other_study}이 OpenFOAM을 사용 중입니다. "
                      f"PID {other_owner.get('pid', 'unknown')}"),
            "study": study_id, "lock": other_owner,
        }
    field_job, field_owner = _active_field_job_lock(root)
    if field_owner is not None:
        return {
            "ok": False, "code": "CFD_SOLVER_BUSY",
            "error": (f"현장 자동 해석 {field_job}이 OpenFOAM을 사용 중입니다. "
                      f"PID {field_owner.get('pid', 'unknown')}"),
            "study": study_id, "lock": field_owner,
        }
    token, owner = _acquire_run_lock(job_path)
    if token is None:
        return {
            "ok": False,
            "code": "GCI_JOB_ALREADY_RUNNING",
            "error": f"GCI 작업이 이미 실행 중입니다. PID {owner.get('pid', 'unknown')}",
            "study": study_id,
            "lock": owner,
        }
    solver_token, solver_owner = acquire_solver_lock(root)
    if solver_token is None:
        _release_run_lock(job_path, token)
        return {
            "ok": False, "code": "CFD_SOLVER_BUSY",
            "error": ("다른 CFD 작업이 OpenFOAM을 사용 중입니다. "
                      f"PID {solver_owner.get('pid', 'unknown')}"),
            "study": study_id, "lock": solver_owner,
        }
    try:
        with cfd_power.keep_system_awake():
            return _run_study_unlocked(root, study_id, callback=callback)
    finally:
        release_solver_lock(root, solver_token)
        _release_run_lock(job_path, token)
