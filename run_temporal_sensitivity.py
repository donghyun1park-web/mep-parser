"""Fail-closed temporal sensitivity runner boundary."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import tempfile

import cfd_gci_job
import cfd_numerical_sensitivity_job as sensitivity_job
import cfd_numerics
import cfd_post
import cfd_physics
import cfd_power
import cfd_report
import cfd_temporal_sensitivity as temporal
import run_numerical_sensitivity as scheme_execution


EXECUTION_CONTRACT = "temporal_sensitivity_execution.v1"
EXECUTION_FILENAME = "temporal_sensitivity_execution.v1.json"
PREPARATION_FILENAME = "temporal_sensitivity_preparation.v1.json"
TARGET_FLOW_THROUGH_FRACTION = 3.0
MAX_CONTINUATION_RUNS = 200
_FOAM_STATEMENT = r"(?m)^(\s*{key}\s+)[^;]+;"


class TemporalSensitivityExecutionError(ValueError):
    pass


def _error(code, detail=None):
    message = str(code)
    if detail:
        message = f"{message}: {detail}"
    raise TemporalSensitivityExecutionError(message)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path):
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        _error("TEMPORAL_ARTIFACT_HASH_FAILED", str(error))
    return digest.hexdigest()


def _load_json(path, code):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as error:
        _error(code, str(error))
    if not isinstance(value, dict):
        _error(code)
    return value


def _replace_foam_statement(text, key, value):
    pattern = re.compile(_FOAM_STATEMENT.format(key=re.escape(key)))
    updated, count = pattern.subn(rf"\g<1>{value};", text)
    if count != 1:
        _error("TEMPORAL_CONTROL_DICT_INVALID", key)
    return updated


def _configure_fixed_delta_t(case, delta_t_s):
    control_path = Path(case) / "system" / "controlDict"
    try:
        control = control_path.read_text(encoding="utf-8")
        control = _replace_foam_statement(control, "adjustTimeStep", "no")
        control = _replace_foam_statement(control, "deltaT", f"{delta_t_s:.12g}")
        control = _replace_foam_statement(control, "maxDeltaT", f"{delta_t_s:.12g}")
        control = _replace_foam_statement(control, "maxCo", "1")
        control_path.write_text(control, encoding="utf-8", newline="\n")
    except (OSError, UnicodeDecodeError) as error:
        _error("TEMPORAL_CONTROL_DICT_INVALID", str(error))
    thermal_path = Path(case) / "thermal_input.json"
    if thermal_path.is_file():
        thermal_input = _load_json(thermal_path, "TEMPORAL_THERMAL_INPUT_INVALID")
        settings = thermal_input.get("settings")
        if not isinstance(settings, dict):
            _error("TEMPORAL_THERMAL_INPUT_INVALID")
        settings["thermal_initial_delta_t_s"] = delta_t_s
        settings["thermal_max_delta_t_s"] = delta_t_s
        settings["thermal_continuation_max_delta_t_s"] = delta_t_s
        settings["thermal_max_co"] = 1.0
        settings["thermal_max_courant_gate"] = 1.0
        _write_json(thermal_path, thermal_input)


def _verify_fixed_step_configuration(case, expected_delta_t_s):
    """Reread the executed controlDict and reject controller intervention."""
    control_path = Path(case) / "system" / "controlDict"
    try:
        control = control_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        _error("TEMPORAL_FIXED_CONTROLLER_INVALID", str(error))

    def value(key):
        matches = re.findall(
            rf"(?m)^\s*{re.escape(key)}\s+([^;]+);", control
        )
        if len(matches) != 1:
            _error("TEMPORAL_FIXED_CONTROLLER_INVALID", key)
        return matches[0].strip()

    if value("adjustTimeStep").casefold() != "no":
        _error("TEMPORAL_FIXED_CONTROLLER_INVALID", "adjustTimeStep")
    try:
        delta_t = float(value("deltaT"))
        max_delta_t = float(value("maxDeltaT"))
        max_co = float(value("maxCo"))
    except ValueError as error:
        _error("TEMPORAL_FIXED_CONTROLLER_INVALID", str(error))
    if (
        not all(math.isfinite(item) for item in (delta_t, max_delta_t, max_co))
        or not math.isclose(
            delta_t, expected_delta_t_s, rel_tol=0.0, abs_tol=1e-12
        )
        or not math.isclose(
            max_delta_t, expected_delta_t_s, rel_tol=0.0, abs_tol=1e-12
        )
        or not math.isclose(max_co, temporal.MAX_CO, rel_tol=0.0, abs_tol=1e-12)
    ):
        _error("TEMPORAL_FIXED_CONTROLLER_INVALID")


def _input_entries(case):
    case = Path(case)
    excluded = {"system/controlDict", "Allrun"}
    entries = []
    for path in sorted(case.rglob("*"), key=lambda item: item.relative_to(case).as_posix()):
        relative = path.relative_to(case).as_posix()
        if path.is_symlink():
            _error("TEMPORAL_CHILD_LINK_FORBIDDEN", relative)
        if path.is_file() and relative not in excluded:
            entries.append({"path": relative, "sha256": _sha256_file(path)})
    if not entries:
        _error("TEMPORAL_CHILD_INPUTS_MISSING", str(case))
    return entries


def _normalised_shared_projection(case):
    case = Path(case)
    control_path = case / "system" / "controlDict"
    try:
        control = control_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        _error("TEMPORAL_CONTROL_DICT_INVALID", str(error))
    for key in ("deltaT", "maxDeltaT"):
        control = _replace_foam_statement(control, key, "<fixed-delta-t>")
    thermal_path = case / "thermal_input.json"
    thermal_input = _load_json(thermal_path, "TEMPORAL_THERMAL_INPUT_INVALID")
    settings = thermal_input.get("settings")
    if not isinstance(settings, dict):
        _error("TEMPORAL_THERMAL_INPUT_INVALID")
    thermal_input = json.loads(json.dumps(thermal_input))
    settings = thermal_input["settings"]
    for key in (
        "thermal_initial_delta_t_s", "thermal_max_delta_t_s",
        "thermal_continuation_max_delta_t_s",
    ):
        settings.pop(key, None)
    invariant_entries = [
        entry for entry in _input_entries(case)
        if entry["path"] != "thermal_input.json"
    ]
    return temporal._canonical_sha256({
        "control_without_delta_t": control.replace("\r\n", "\n"),
        "thermal_without_delta_t": thermal_input,
        "invariant_entries": invariant_entries,
    })


def _rehash_input_entries(case, entries):
    if not isinstance(entries, list) or not entries:
        _error("TEMPORAL_IMMUTABLE_INPUT_SNAPSHOT_INVALID")
    for entry in entries:
        if not isinstance(entry, dict):
            _error("TEMPORAL_IMMUTABLE_INPUT_SNAPSHOT_INVALID")
        relative = entry.get("path")
        if (not isinstance(relative, str) or not relative
                or "\\" in relative or Path(relative).is_absolute()
                or ".." in Path(relative).parts):
            _error("TEMPORAL_IMMUTABLE_INPUT_SNAPSHOT_INVALID")
        path = (Path(case) / relative).resolve(strict=False)
        try:
            path.relative_to(Path(case).resolve(strict=True))
        except ValueError:
            _error("TEMPORAL_IMMUTABLE_INPUT_SNAPSHOT_INVALID")
        if path.is_symlink() or not path.is_file():
            _error("TEMPORAL_IMMUTABLE_INPUT_MISSING", relative)
        if _sha256_file(path) != entry.get("sha256"):
            _error("TEMPORAL_IMMUTABLE_INPUT_HASH_MISMATCH", relative)


def _prepare_temporal_children(study, manifest):
    preparation_path = Path(study) / PREPARATION_FILENAME
    if preparation_path.is_file():
        preparation = _load_json(
            preparation_path, "TEMPORAL_PREPARATION_INVALID")
        if preparation.get("manifest_sha256") != manifest.get("manifest_sha256"):
            _error("TEMPORAL_PREPARATION_MANIFEST_MISMATCH")
        for child in preparation.get("children") or []:
            case = Path(study) / child.get("case_child", "")
            if not case.is_dir() or temporal._tree_sha256(case) != child.get(
                    "input_tree_sha256"):
                _error("TEMPORAL_CHILD_INPUT_HASH_MISMATCH", str(case))
        return preparation
    if any((Path(study) / child["case_child"]).exists()
           for child in manifest["children"]):
        _error("TEMPORAL_UNTRACKED_CHILD_EXISTS")
    seed = Path(manifest["seed_case_path"]).expanduser().resolve(strict=False)
    if temporal._tree_sha256(seed) != manifest["seed_tree_sha256"]:
        _error("TEMPORAL_SEED_HASH_MISMATCH")
    staging = Path(tempfile.mkdtemp(prefix=".temporal-prepare-", dir=study))
    prepared_children = []
    try:
        for child in manifest["children"]:
            target = staging / child["case_child"]
            shutil.copytree(seed, target)
            _configure_fixed_delta_t(target, float(child["delta_t_s"]))
            prepared_children.append({
                "case_child": child["case_child"],
                "delta_t_s": child["delta_t_s"],
                "input_tree_sha256": temporal._tree_sha256(target),
                "control_dict_sha256": _sha256_file(
                    target / "system" / "controlDict"),
                "shared_input_projection_sha256": _normalised_shared_projection(
                    target),
                "immutable_input_entries": _input_entries(target),
                "mesh_manifest_sha256": _sha256_file(
                    target / "mesh_manifest.json")
                if (target / "mesh_manifest.json").is_file() else None,
            })
        projections = {
            child["shared_input_projection_sha256"] for child in prepared_children
        }
        if len(projections) != 1:
            _error("TEMPORAL_EXTRA_INPUT_VARIATION")
        for child in prepared_children:
            (staging / child["case_child"]).replace(
                Path(study) / child["case_child"])
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    preparation = {
        "contract": "temporal_sensitivity_preparation.v1",
        "status": "PENDING_SOLVER_EVIDENCE",
        "manifest_sha256": manifest["manifest_sha256"],
        "seed_tree_sha256": manifest["seed_tree_sha256"],
        "children": prepared_children,
        "created_at": _now(),
    }
    _write_json(preparation_path, preparation)
    return preparation


def _flow_fraction(run):
    try:
        value = float(run["thermal_progress"]["flow_through_fraction"])
    except (KeyError, TypeError, ValueError):
        _error("TEMPORAL_FLOW_THROUGH_EVIDENCE_MISSING")
    if not math.isfinite(value) or value < 0:
        _error("TEMPORAL_FLOW_THROUGH_EVIDENCE_INVALID")
    return value


def _run_case_to_target(case, delta_t_s, target, progress_cb=None):
    result = cfd_physics.run_buoyant_case(case, progress_cb=progress_cb)
    if not isinstance(result, dict) or result.get("ok") is not True:
        _error("TEMPORAL_SOLVER_RUN_FAILED")
    run = result.get("manifest") or _load_json(
        Path(case) / "run_manifest.json", "TEMPORAL_RUN_MANIFEST_MISSING")
    for _ in range(MAX_CONTINUATION_RUNS):
        if _flow_fraction(run) + 1e-12 >= target:
            return result
        progress = run.get("thermal_progress") or {}
        try:
            flow_time = float(progress["flow_through_time_s"])
            latest = float(progress["latest_time_s"])
            recommended = float(progress.get("recommended_next_duration_s") or 0.0)
        except (KeyError, TypeError, ValueError):
            _error("TEMPORAL_CONTINUATION_EVIDENCE_INVALID")
        remaining = max(0.0, flow_time * target - latest)
        duration = min(remaining, recommended if recommended > 0 else 5.0)
        if not math.isfinite(duration) or duration <= 0:
            _error("TEMPORAL_CONTINUATION_EVIDENCE_INVALID")
        result = cfd_physics.run_buoyant_continuation(
            case,
            settings={
                "thermal_duration_s": duration,
                "thermal_minimum_flow_through_fraction": target,
                "thermal_initial_delta_t_s": delta_t_s,
                "thermal_max_delta_t_s": delta_t_s,
                "thermal_continuation_max_delta_t_s": delta_t_s,
            },
            progress_cb=progress_cb,
        )
        if not isinstance(result, dict) or result.get("ok") is not True:
            _error("TEMPORAL_SOLVER_CONTINUATION_FAILED")
        run = result.get("manifest") or _load_json(
            Path(case) / "run_manifest.json", "TEMPORAL_RUN_MANIFEST_MISSING")
    _error("TEMPORAL_CONTINUATION_LIMIT_EXCEEDED")


def _completed_temporal_side(case, child):
    run_path = Path(case) / "run_manifest.json"
    result_path = Path(case) / "result_manifest.json"
    if not run_path.is_file() or not result_path.is_file():
        _error("TEMPORAL_SOLVER_EVIDENCE_INCOMPLETE", str(case))
    run = _load_json(run_path, "TEMPORAL_RUN_MANIFEST_INVALID")
    if _flow_fraction(run) + 1e-12 < TARGET_FLOW_THROUGH_FRACTION:
        _error("TEMPORAL_MINIMUM_FLOW_THROUGH_NOT_REACHED", str(case))
    logs = sorted(
        path for path in Path(case).glob("**/*")
        if path.is_file() and path.name.casefold().startswith("log")
    )
    history = temporal.verify_fixed_delta_t_history(logs, child["delta_t_s"])
    if not history["valid"]:
        _error("TEMPORAL_ACTUAL_DELTA_T_HISTORY_INVALID", ",".join(history["blockers"]))
    log_entries = [
        {"path": path.relative_to(case).as_posix(), "sha256": _sha256_file(path)}
        for path in logs
    ]
    return {
        "status": "COMPLETE",
        "case_child": child["case_child"],
        "delta_t_s": child["delta_t_s"],
        "run_manifest_sha256": _sha256_file(run_path),
        "result_manifest_sha256": _sha256_file(result_path),
        "solver_logs": log_entries,
        "solver_log_tree_sha256": temporal._canonical_sha256(log_entries),
        "flow_through_fraction": _flow_fraction(run),
        "time_history": history,
        "completed_at": _now(),
    }


def _load_manifest(study_dir):
    study = Path(study_dir).expanduser().resolve(strict=False)
    path = study / "temporal_sensitivity.v1.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as error:
        raise TemporalSensitivityExecutionError("TEMPORAL_MANIFEST_READ_FAILED") from error
    validation = temporal.validate_temporal_manifest(manifest)
    if not validation["valid"]:
        raise TemporalSensitivityExecutionError(
            "TEMPORAL_MANIFEST_INVALID:" + ",".join(validation["blockers"])
        )
    return study, manifest


def run_temporal_study(study_dir: Path, progress_cb=None, *, execute=False) -> dict:
    study, manifest = _load_manifest(study_dir)
    if progress_cb:
        progress_cb({"stage": "validated_frozen_inputs", "study_root": str(study)})
    if not execute:
        return {
            "contract": EXECUTION_CONTRACT,
            "status": "PENDING_SOLVER_EVIDENCE",
            "valid": False,
            "study_root": str(study),
            "manifest_sha256": manifest["manifest_sha256"],
            "blockers": ["SOLVER_EXECUTION_PENDING", "WSL_OR_OPENFOAM_REQUIRED"],
        }
    if manifest.get("anchor_fine_case"):
        _error("TEMPORAL_EXTERNAL_FINE_ANCHOR_REQUIRES_P5_3")
    preparation = _prepare_temporal_children(study, manifest)
    checkpoint_path = study / EXECUTION_FILENAME
    if checkpoint_path.exists():
        _error("TEMPORAL_EXECUTION_ALREADY_STARTED", str(checkpoint_path))
    checkpoint = {
        "contract": EXECUTION_CONTRACT,
        "status": "RUNNING",
        "valid": False,
        "manifest_sha256": manifest["manifest_sha256"],
        "preparation_sha256": _sha256_file(study / PREPARATION_FILENAME),
        "target_flow_through_fraction": TARGET_FLOW_THROUGH_FRACTION,
        "started_at": _now(),
        "blockers": ["SOLVER_RUNS_INCOMPLETE"],
    }
    for child in manifest["children"]:
        checkpoint[child["case_child"]] = {"status": "PENDING"}
    _write_json(checkpoint_path, checkpoint)
    token, owner = cfd_gci_job.acquire_solver_lock(study.parent)
    if token is None:
        try:
            checkpoint_path.unlink()
        except OSError:
            pass
        _error("CFD_SOLVER_BUSY", f"PID {(owner or {}).get('pid', 'unknown')}")
    try:
        with cfd_power.keep_system_awake():
            for child in manifest["children"]:
                name = child["case_child"]
                case = study / name
                prepared = next(
                    item for item in preparation["children"]
                    if item["case_child"] == name
                )
                if temporal._tree_sha256(case) != prepared["input_tree_sha256"]:
                    _error("TEMPORAL_CHILD_INPUT_HASH_MISMATCH", name)
                checkpoint[name] = {
                    "status": "RUNNING",
                    "case_child": name,
                    "delta_t_s": child["delta_t_s"],
                    "started_at": _now(),
                }
                _write_json(checkpoint_path, checkpoint)
                if progress_cb:
                    progress_cb({"stage": f"{name}_running", "case": str(case)})
                _run_case_to_target(
                    case, child["delta_t_s"], TARGET_FLOW_THROUGH_FRACTION,
                    progress_cb=progress_cb,
                )
                checkpoint[name] = _completed_temporal_side(case, child)
                _write_json(checkpoint_path, checkpoint)
    except Exception as error:
        checkpoint["status"] = "FAIL"
        checkpoint["failed_at"] = _now()
        checkpoint["blockers"] = [str(error).split(":", 1)[0]]
        _write_json(checkpoint_path, checkpoint)
        raise
    finally:
        cfd_gci_job.release_solver_lock(study.parent, token)
    checkpoint["status"] = "SOLVER_RUNS_COMPLETE"
    checkpoint["completed_at"] = _now()
    checkpoint["blockers"] = ["INDEPENDENT_VERIFICATION_REQUIRED"]
    _write_json(checkpoint_path, checkpoint)
    if progress_cb:
        progress_cb({"stage": "solver_runs_complete", "status": checkpoint["status"]})
    return {**checkpoint, "study_root": str(study)}


def _verify_selector(manifest):
    selector = manifest.get("selector")
    if not isinstance(selector, dict):
        _error("TEMPORAL_CONFIRMED_SELECTOR_REQUIRED")
    try:
        selector = sensitivity_job.require_confirmed_occupied_volume_band({
            key: value for key, value in selector.items()
            if key != "selector_sha256"
        })
    except sensitivity_job.NumericalSensitivityJobInputError as error:
        _error("TEMPORAL_CONFIRMED_SELECTOR_REQUIRED", str(error))
    for name in ("geometry_ref", "zone_ref"):
        reference = selector[name]
        path = Path(reference["path"]).expanduser().resolve(strict=False)
        if path.is_symlink() or not path.is_file():
            _error("TEMPORAL_SELECTOR_EVIDENCE_MISSING", name)
        if _sha256_file(path) != reference["sha256"]:
            _error("TEMPORAL_SELECTOR_EVIDENCE_HASH_MISMATCH", name)
    if selector != manifest.get("selector"):
        _error("TEMPORAL_SELECTOR_HASH_MISMATCH")
    return selector


def _verify_base_numerical_gates(run, case):
    evidence = scheme_execution._solver_evidence_from_run(run)
    if evidence["ended"] is not True or evidence["fatal_error"] is True:
        _error("TEMPORAL_SOLVER_END_STATE_FAILED", str(case))
    try:
        peak_co = float(evidence["peak_courant"])
        co_limit = min(float(evidence["courant_limit"]), 1.0)
        continuity = abs(float(evidence["continuity"]["global"]))
        continuity_limit = float(evidence["continuity"]["limit"])
        phi_imbalance = abs(float(evidence["phi_balance"]["imbalance_ratio"]))
        phi_limit = float(evidence["phi_balance"]["limit"])
    except (KeyError, TypeError, ValueError):
        _error("TEMPORAL_SOLVER_EVIDENCE_INVALID", str(case))
    if (not all(math.isfinite(value) for value in (
            peak_co, co_limit, continuity, continuity_limit,
            phi_imbalance, phi_limit)) or peak_co > co_limit):
        _error("TEMPORAL_COURANT_LIMIT_FAILED", str(case))
    if continuity > continuity_limit:
        _error("TEMPORAL_CONTINUITY_LIMIT_FAILED", str(case))
    if evidence["phi_balance"]["available"] is not True or phi_imbalance > phi_limit:
        _error("TEMPORAL_PHI_BALANCE_FAILED", str(case))
    for field, row in evidence["residuals"].items():
        if (float(row["final"]) > float(row["limit"])
                or float(row["tail_maximum"]) > float(row["limit"])):
            _error("TEMPORAL_RESIDUAL_LIMIT_FAILED", field)
    if evidence["energy_closure_basis"] != (
            "solver_positive_phi_and_owner_cell_temperature"):
        _error("TEMPORAL_ENERGY_BASIS_INVALID", str(case))
    return evidence


def _json_payload_sha256(value):
    encoded = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def verify_temporal_study(study_dir: Path, current_case: Path, *, publish=True) -> dict:
    """Rehash all raw evidence and recompute the three-level temporal result."""
    study, manifest = _load_manifest(study_dir)
    expected = (study / "fine_dt_0p01").resolve(strict=False)
    if manifest.get("anchor_fine_case"):
        expected = Path(manifest["anchor_fine_case"]).expanduser().resolve(strict=False)
    current = Path(current_case).expanduser().resolve(strict=False)
    preflight_blockers = []
    if current != expected:
        preflight_blockers.append("TEMPORAL_CURRENT_CASE_NOT_FINE_CHILD")
    try:
        seed_hash = temporal._tree_sha256(manifest["seed_case_path"])
    except (KeyError, temporal.TemporalSensitivityInputError):
        preflight_blockers.append("TEMPORAL_SEED_HASH_MISMATCH")
    else:
        if seed_hash != manifest.get("seed_tree_sha256"):
            preflight_blockers.append("TEMPORAL_SEED_HASH_MISMATCH")
    if not isinstance(manifest.get("selector"), dict):
        preflight_blockers.append("TEMPORAL_CONFIRMED_SELECTOR_REQUIRED")
    if preflight_blockers:
        return {
            "contract": EXECUTION_CONTRACT,
            "status": "NOT_EVALUATED",
            "valid": False,
            "study_root": str(study),
            "current_case": str(current),
            "manifest_sha256": manifest["manifest_sha256"],
            "blockers": list(dict.fromkeys(preflight_blockers)),
        }
    selector = _verify_selector(manifest)
    preparation_path = study / PREPARATION_FILENAME
    checkpoint_path = study / EXECUTION_FILENAME
    if not preparation_path.is_file() or not checkpoint_path.is_file():
        return {
            "contract": EXECUTION_CONTRACT,
            "status": "NOT_EVALUATED",
            "valid": False,
            "study_root": str(study),
            "current_case": str(current),
            "manifest_sha256": manifest["manifest_sha256"],
            "blockers": ["TEMPORAL_SOLVER_EVIDENCE_MISSING"],
        }
    preparation = _load_json(preparation_path, "TEMPORAL_PREPARATION_INVALID")
    checkpoint = _load_json(checkpoint_path, "TEMPORAL_EXECUTION_CHECKPOINT_INVALID")
    if (preparation.get("manifest_sha256") != manifest["manifest_sha256"]
            or checkpoint.get("contract") != EXECUTION_CONTRACT
            or checkpoint.get("status") != "SOLVER_RUNS_COMPLETE"
            or checkpoint.get("manifest_sha256") != manifest["manifest_sha256"]
            or checkpoint.get("preparation_sha256") != _sha256_file(preparation_path)
            or checkpoint.get("target_flow_through_fraction")
            != TARGET_FLOW_THROUGH_FRACTION):
        _error("TEMPORAL_EXECUTION_CHECKPOINT_INVALID")
    prepared_by_name = {
        item["case_child"]: item for item in preparation.get("children") or []
        if isinstance(item, dict) and isinstance(item.get("case_child"), str)
    }
    if set(prepared_by_name) != {item["case_child"] for item in manifest["children"]}:
        _error("TEMPORAL_PREPARATION_INVALID")
    projections = {
        item.get("shared_input_projection_sha256")
        for item in prepared_by_name.values()
    }
    if len(projections) != 1 or None in projections:
        _error("TEMPORAL_EXTRA_INPUT_VARIATION")
    children_result = []
    qois = []
    windows = []
    profiles = set()
    mesh_hashes = set()
    for child in manifest["children"]:
        name = child["case_child"]
        case = study / name
        prepared = prepared_by_name[name]
        side = checkpoint.get(name)
        if (not isinstance(side, dict) or side.get("status") != "COMPLETE"
                or side.get("case_child") != name
                or side.get("delta_t_s") != child["delta_t_s"]):
            _error("TEMPORAL_EXECUTION_SIDE_INCOMPLETE", name)
        _verify_fixed_step_configuration(case, child["delta_t_s"])
        _rehash_input_entries(case, prepared.get("immutable_input_entries"))
        logs = []
        for reference in side.get("solver_logs") or []:
            if not isinstance(reference, dict):
                _error("TEMPORAL_SOLVER_LOG_EVIDENCE_INVALID", name)
            relative = reference.get("path")
            path = (case / str(relative)).resolve(strict=False)
            try:
                path.relative_to(case.resolve(strict=True))
            except ValueError:
                _error("TEMPORAL_SOLVER_LOG_EVIDENCE_INVALID", name)
            if path.is_symlink() or not path.is_file() or _sha256_file(path) != reference.get(
                    "sha256"):
                _error("TEMPORAL_SOLVER_LOG_HASH_MISMATCH", name)
            logs.append(path)
        actual_history = temporal.verify_fixed_delta_t_history(
            logs, child["delta_t_s"])
        if not actual_history["valid"] or actual_history != side.get("time_history"):
            _error("TEMPORAL_ACTUAL_DELTA_T_HISTORY_INVALID", name)
        pseudo_pair = {"shared_input": {
            "mesh_sha256": prepared.get("mesh_manifest_sha256")
        }}
        evidence = scheme_execution._verify_case_result_artifacts(
            case, pseudo_pair, side)
        run = evidence.pop("run")
        profile = (run.get("effective_numerics") or {}).get("profile")
        if not isinstance(profile, str):
            _error("TEMPORAL_EFFECTIVE_PROFILE_MISSING", name)
        profiles.add(profile)
        mesh_hashes.add(evidence["mesh_manifest_sha256"])
        scheme_execution._verify_effective_numerics_files(case, run, profile)
        solver_evidence = _verify_base_numerical_gates(run, case)
        progress = run.get("thermal_progress")
        try:
            flow_time = float(progress["flow_through_time_s"])
            latest = float(progress["latest_time_s"])
            fraction = float(progress["flow_through_fraction"])
            checkpoint_fraction = float(side["flow_through_fraction"])
        except (KeyError, TypeError, ValueError):
            _error("TEMPORAL_FLOW_THROUGH_EVIDENCE_INVALID", name)
        if (not all(math.isfinite(value) for value in (
                flow_time, latest, fraction, checkpoint_fraction))
                or flow_time <= 0 or fraction + 1e-12 < TARGET_FLOW_THROUGH_FRACTION
                or not math.isclose(
                    fraction, checkpoint_fraction, rel_tol=1e-9, abs_tol=1e-9)):
            _error("TEMPORAL_MINIMUM_FLOW_THROUGH_NOT_REACHED", name)
        window_start = latest - 0.1 * flow_time
        window = {
            "start_s": window_start,
            "end_s": latest,
            "flow_through_fraction_start": fraction - 0.1,
            "flow_through_fraction_end": fraction,
        }
        occupied = cfd_post.compute_time_weighted_occupied_volume_qois(
            sorted(case.glob("VTK/**/internal.vtu")), selector,
            floor_elevation_m=scheme_execution._occupied_floor_elevation(run, case),
            window_start_s=window_start, window_end_s=latest,
            minimum_samples=5,
        )
        exhaust = cfd_post.read_time_weighted_exhaust_temperature_rise_from_case(
            case, window_start_s=window_start, window_end_s=latest,
            minimum_samples=5,
        )
        qoi = {
            "occupied_zone_mean_temperature_k": occupied[
                "occupied_zone_mean_temperature_k"],
            "occupied_zone_mean_speed_m_s": occupied[
                "occupied_zone_mean_speed_m_s"],
            "exhaust_temperature_rise_k": exhaust["exhaust_temperature_rise_k"],
        }
        windows.append(window)
        qois.append(qoi)
        children_result.append({
            "case_child": name,
            "delta_t_s": child["delta_t_s"],
            "case_path": str(case.resolve(strict=False)),
            "run_hash": evidence["run_manifest_sha256"],
            "result_hash": evidence["result_manifest_sha256"],
            "mesh_hash": evidence["mesh_manifest_sha256"],
            "solver_log_tree_sha256": evidence["solver_log_tree_sha256"],
            "time_history": actual_history,
            "window": window,
            "qois": qoi,
            "solver_evidence": solver_evidence,
        })
    if len(profiles) != 1:
        _error("TEMPORAL_SCHEME_MISMATCH")
    if len(mesh_hashes) != 1:
        _error("TEMPORAL_MESH_MISMATCH")
    first_window = windows[0]
    if any(
        not math.isclose(window[key], first_window[key], rel_tol=1e-9, abs_tol=1e-9)
        for window in windows[1:]
        for key in ("start_s", "end_s", "flow_through_fraction_end")
    ):
        _error("TEMPORAL_COMMON_FINAL_WINDOW_MISMATCH")
    qoi_configuration = {
        "occupied_zone_mean_temperature_k": (1.0, 0.5),
        "occupied_zone_mean_speed_m_s": (0.1, 0.05),
        "exhaust_temperature_rise_k": (1.0, 0.5),
    }
    convergence = []
    for name, (floor, absolute_limit) in qoi_configuration.items():
        convergence.append(temporal.calculate_temporal_richardson(
            name,
            coarse=qois[0][name], medium=qois[1][name], fine=qois[2][name],
            fixed_delta_t_s=manifest["fixed_delta_t_s"],
            near_zero_floor=floor, relative_limit_pct=5.0,
            absolute_limit=absolute_limit,
        ))
    statuses = {item["status"] for item in convergence}
    status = "PASS" if statuses == {"PASS"} else (
        "NOT_EVALUATED" if "NOT_EVALUATED" in statuses else "FAIL"
    )
    blockers = list(dict.fromkeys(
        blocker for item in convergence for blocker in item.get("blockers") or []
    ))
    result = {
        "contract": temporal.CONTRACT,
        "status": status,
        "study_root": str(study),
        "current_case": str(current),
        "fixed_delta_t_s": manifest["fixed_delta_t_s"],
        "controller": manifest["controller"],
        "manifest_sha256": manifest["manifest_sha256"],
        "preparation_sha256": _sha256_file(preparation_path),
        "execution_checkpoint_sha256": _sha256_file(checkpoint_path),
        "children": children_result,
        "qoi_convergence": convergence,
        "blockers": blockers,
    }
    if "validation_anchor" in manifest:
        result["validation_anchor"] = manifest["validation_anchor"]
    if status != "PASS":
        return {**result, "valid": False}
    plot_path = study / "temporal_sensitivity_convergence.v1.svg"
    plot_text = cfd_report.render_temporal_sensitivity_svg(convergence)
    plot_sha256 = hashlib.sha256(plot_text.encode("utf-8")).hexdigest()
    evidence = {
        "contract": "temporal_sensitivity_verification_evidence.v1",
        "study_root": str(study),
        "manifest_sha256": manifest["manifest_sha256"],
        "preparation_sha256": result["preparation_sha256"],
        "execution_checkpoint_sha256": result["execution_checkpoint_sha256"],
        "children": children_result,
        "plot": {"path": plot_path.name, "sha256": plot_sha256},
    }
    evidence_path = study / "temporal_sensitivity_verification.v1.json"
    evidence_sha256 = _json_payload_sha256(evidence)
    result["verification"] = {
        "contract": "temporal_sensitivity_verification.v1",
        "verifier": "run_temporal_sensitivity.verify_temporal_study",
        "raw_artifacts_rehashed": True,
        "study_root": str(study),
        "evidence_path": evidence_path.name,
        "evidence_sha256": evidence_sha256,
        "plot_path": plot_path.name,
        "plot_sha256": plot_sha256,
    }
    if publish:
        cfd_report.write_temporal_sensitivity_plot(convergence, plot_path)
        if _sha256_file(plot_path) != plot_sha256:
            _error("TEMPORAL_CONVERGENCE_PLOT_WRITE_MISMATCH")
        _write_json(evidence_path, evidence)
        if _sha256_file(evidence_path) != evidence_sha256:
            _error("TEMPORAL_VERIFICATION_EVIDENCE_WRITE_MISMATCH")
        _write_json(study / "temporal_sensitivity_result.v1.json", result)
        _write_json(current / "temporal_sensitivity.json", result)
    elif not plot_path.is_file() or _sha256_file(plot_path) != plot_sha256:
        _error("TEMPORAL_CONVERGENCE_PLOT_HASH_MISMATCH")
    return {**result, "valid": True}


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study_dir", nargs="?", type=Path)
    parser.add_argument("--case-seed", "--mesh-case", dest="case_seed", type=Path)
    parser.add_argument("--study-root", type=Path)
    parser.add_argument(
        "--delta-t", "--fixed-delta-t", dest="fixed_delta_t",
        nargs=3, type=float, default=[0.04, 0.02, 0.01],
    )
    parser.add_argument("--courant-ceiling", type=float, default=1.0)
    parser.add_argument("--selector", "--selector-json", dest="selector", type=Path)
    parser.add_argument("--anchor-fine-case", type=Path)
    parser.add_argument("--validation-anchor", type=Path)
    parser.add_argument("--current-case", type=Path)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.case_seed is not None or args.study_root is not None:
            if args.case_seed is None or args.study_root is None:
                raise TemporalSensitivityExecutionError("TEMPORAL_PREPARE_ARGUMENTS_REQUIRED")
            if args.selector is None:
                raise TemporalSensitivityExecutionError("TEMPORAL_SELECTOR_REQUIRED")
            if not math.isclose(
                    args.courant_ceiling, temporal.MAX_CO,
                    rel_tol=0.0, abs_tol=1e-12):
                raise TemporalSensitivityExecutionError(
                    "TEMPORAL_COURANT_CEILING_INVALID"
                )
            manifest = temporal.create_temporal_study(
                args.case_seed,
                args.fixed_delta_t,
                args.anchor_fine_case,
                validation_anchor_path=args.validation_anchor,
                selector=_load_json(args.selector, "TEMPORAL_SELECTOR_INVALID"),
            )
            study = Path(args.study_root).expanduser().resolve(strict=False)
            if study.exists():
                raise TemporalSensitivityExecutionError("TEMPORAL_TARGET_EXISTS")
            study.mkdir(parents=True)
            _write_json(study / "temporal_sensitivity.v1.json", manifest)
            result = manifest
        else:
            if args.study_dir is None:
                raise TemporalSensitivityExecutionError("TEMPORAL_STUDY_REQUIRED")
            if args.verify:
                if args.current_case is None:
                    raise TemporalSensitivityExecutionError("TEMPORAL_CURRENT_CASE_REQUIRED")
                result = verify_temporal_study(args.study_dir, args.current_case)
            else:
                result = run_temporal_study(args.study_dir, execute=args.execute)
        if args.output:
            _write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result.get("status") == "PASS" else 2
    except (TemporalSensitivityExecutionError, temporal.TemporalSensitivityInputError) as error:
        result = {"contract": EXECUTION_CONTRACT, "status": "NOT_EVALUATED",
                  "valid": False, "blockers": [str(error).split(":", 1)[0]],
                  "error": str(error)}
        if args.output:
            _write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
