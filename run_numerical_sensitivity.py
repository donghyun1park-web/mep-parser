"""Fail-closed serial execution and verification for scheme sensitivity."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import tempfile

import cfd_gci_job
import cfd_numerical_sensitivity_job as sensitivity_job
import cfd_numerical_sensitivity_runner as preparation
import cfd_numerics
import cfd_post
import cfd_physics
import cfd_power


EXECUTION_CONTRACT = "serial_numerical_sensitivity_execution.v1"
EXECUTION_FILENAME = "serial_sensitivity_execution.v1.json"
TARGET_FLOW_THROUGH_FRACTION = 3.0
MAX_CONTINUATION_RUNS = 200


class NumericalSensitivityExecutionError(ValueError):
    """Raised when a sensitivity execution request is unsafe or malformed."""


def _error(code, detail=None):
    message = str(code)
    if detail:
        message = f"{message}: {detail}"
    raise NumericalSensitivityExecutionError(message)


def _load_object(path, code):
    path = Path(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError) as error:
        _error(code, str(error))
    if not isinstance(value, dict):
        _error(code)
    return value


def _now():
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path):
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        _error("SENSITIVITY_ARTIFACT_HASH_FAILED", str(error))
    return digest.hexdigest()


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", delete=False,
                dir=path.parent, prefix="." + path.name + ".", suffix=".tmp") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            temporary = Path(handle.name)
        temporary.replace(path)
    except (OSError, TypeError, ValueError) as error:
        _error("SENSITIVITY_CHECKPOINT_WRITE_FAILED", str(error))


def _safe_child_file(case, relative, code):
    if (not isinstance(relative, str) or not relative or "\\" in relative
            or Path(relative).is_absolute() or ".." in Path(relative).parts):
        _error(code, str(relative))
    case = Path(case).resolve(strict=True)
    candidate = (case / relative).resolve(strict=False)
    try:
        candidate.relative_to(case)
    except ValueError:
        _error(code, str(relative))
    if candidate.is_symlink() or not candidate.is_file():
        _error(code, str(relative))
    return candidate


def _validate_prerun_case(case, side):
    """Rehash the frozen seed and reject every completed/restarted run."""
    case = Path(case)
    for child in case.iterdir():
        if child.is_dir() and child.name.casefold().startswith("processor"):
            _error("SENSITIVITY_PROCESSOR_DIRECTORIES_FORBIDDEN", str(child))
        if child.is_dir():
            try:
                if float(child.name) > 0:
                    _error("SENSITIVITY_EXISTING_RUN_FORBIDDEN", str(child))
            except ValueError:
                pass
    for name in (
        "run_manifest.json", "result_manifest.json", "thermal_progress.json",
        "thermal_restart_input.json",
    ):
        if (case / name).exists():
            _error("SENSITIVITY_EXISTING_RUN_FORBIDDEN", name)
    snapshot = _load_object(
        case / "case_seed_snapshot.v1.json", "SENSITIVITY_CASE_SEED_SNAPSHOT_MISSING"
    )
    supplied_hash = snapshot.get("case_seed_snapshot_sha256")
    payload = dict(snapshot)
    payload.pop("case_seed_snapshot_sha256", None)
    if (supplied_hash != side.get("case_seed_snapshot_sha256")
            or sensitivity_job.canonical_sha256(payload) != supplied_hash):
        _error("SENSITIVITY_CASE_SEED_SNAPSHOT_HASH_MISMATCH")
    entries = snapshot.get("entries")
    if not isinstance(entries, list) or not entries:
        _error("SENSITIVITY_CASE_SEED_ENTRIES_MISSING")
    for entry in entries:
        if not isinstance(entry, dict):
            _error("SENSITIVITY_CASE_SEED_ENTRY_INVALID")
        path = _safe_child_file(
            case, entry.get("path"), "SENSITIVITY_CASE_SEED_ENTRY_INVALID"
        )
        if _sha256_file(path) != entry.get("sha256"):
            _error("SENSITIVITY_CASE_SEED_ENTRY_HASH_MISMATCH", entry.get("path"))
    return supplied_hash


def _flow_fraction(manifest):
    try:
        value = float((manifest.get("thermal_progress") or {})[
            "flow_through_fraction"
        ])
    except (KeyError, TypeError, ValueError):
        _error("SENSITIVITY_FLOW_THROUGH_EVIDENCE_MISSING")
    if not math.isfinite(value) or value < 0:
        _error("SENSITIVITY_FLOW_THROUGH_EVIDENCE_INVALID")
    return value


def _run_case_to_target(case, target, progress_cb=None):
    """Run a fresh child and continue it until the fixed FTT target."""
    result = cfd_physics.run_buoyant_case(case, progress_cb=progress_cb)
    if not isinstance(result, dict) or result.get("ok") is not True:
        _error(
            "SENSITIVITY_SOLVER_RUN_FAILED",
            result.get("error") if isinstance(result, dict) else None,
        )
    manifest = result.get("manifest") or _load_object(
        Path(case) / "run_manifest.json", "SENSITIVITY_RUN_MANIFEST_MISSING"
    )
    for _ in range(MAX_CONTINUATION_RUNS):
        fraction = _flow_fraction(manifest)
        if fraction + 1e-12 >= target:
            return result
        progress = manifest.get("thermal_progress") or {}
        try:
            flow_time = float(progress["flow_through_time_s"])
            latest = float(progress["latest_time_s"])
            recommended = float(progress.get("recommended_next_duration_s") or 0.0)
        except (KeyError, TypeError, ValueError):
            _error("SENSITIVITY_CONTINUATION_EVIDENCE_INVALID")
        remaining = max(0.0, flow_time * target - latest)
        duration = min(remaining, recommended if recommended > 0 else 5.0)
        if not math.isfinite(duration) or duration <= 0:
            _error("SENSITIVITY_CONTINUATION_EVIDENCE_INVALID")
        result = cfd_physics.run_buoyant_continuation(
            case,
            settings={
                "thermal_duration_s": duration,
                "thermal_minimum_flow_through_fraction": target,
            },
            progress_cb=progress_cb,
        )
        if not isinstance(result, dict) or result.get("ok") is not True:
            _error(
                "SENSITIVITY_SOLVER_CONTINUATION_FAILED",
                result.get("error") if isinstance(result, dict) else None,
            )
        manifest = result.get("manifest") or _load_object(
            Path(case) / "run_manifest.json", "SENSITIVITY_RUN_MANIFEST_MISSING"
        )
    _error("SENSITIVITY_CONTINUATION_LIMIT_EXCEEDED")


def _completed_side(case, side):
    run_path = Path(case) / "run_manifest.json"
    result_path = Path(case) / "result_manifest.json"
    if not run_path.is_file() or not result_path.is_file():
        _error("SENSITIVITY_SOLVER_EVIDENCE_INCOMPLETE", str(case))
    run = _load_object(run_path, "SENSITIVITY_RUN_MANIFEST_INVALID")
    if _flow_fraction(run) + 1e-12 < TARGET_FLOW_THROUGH_FRACTION:
        _error("SENSITIVITY_MINIMUM_FLOW_THROUGH_NOT_REACHED", str(case))
    profile = (run.get("effective_numerics") or {}).get("profile")
    if profile != side.get("profile"):
        _error("SENSITIVITY_EFFECTIVE_PROFILE_MISMATCH", str(case))
    logs = sorted(
        path for path in Path(case).glob("**/*")
        if path.is_file() and path.name.casefold().startswith("log")
    )
    if not logs:
        _error("SENSITIVITY_SOLVER_LOG_MISSING", str(case))
    log_entries = [
        {"path": path.relative_to(case).as_posix(), "sha256": _sha256_file(path)}
        for path in logs
    ]
    return {
        "status": "COMPLETE",
        "profile": side["profile"],
        "case_child": side["case_child"],
        "case_seed_snapshot_sha256": side["case_seed_snapshot_sha256"],
        "run_manifest_sha256": _sha256_file(run_path),
        "result_manifest_sha256": _sha256_file(result_path),
        "solver_log_tree_sha256": sensitivity_job.canonical_sha256(log_entries),
        "solver_logs": log_entries,
        "flow_through_fraction": _flow_fraction(run),
        "completed_at": _now(),
    }


def _load_study(study_dir):
    study = Path(study_dir).expanduser().resolve(strict=False)
    if not study.is_dir():
        _error("SENSITIVITY_STUDY_NOT_FOUND", str(study))
    pair = _load_object(
        study / "frozen_pair_manifest.json",
        "SENSITIVITY_PAIR_MANIFEST_MISSING",
    )
    job = _load_object(
        study / "cfd_numerical_sensitivity_job.v1.json",
        "SENSITIVITY_JOB_MANIFEST_MISSING",
    )
    preparation_manifest = _load_object(
        study / "serial_sensitivity_preparation.v1.json",
        "SENSITIVITY_PREPARATION_MANIFEST_MISSING",
    )
    pair_validation = sensitivity_job.validate_frozen_pair_manifest(pair)
    if not pair_validation.get("valid"):
        _error(
            "SENSITIVITY_PAIR_MANIFEST_INVALID",
            ",".join(pair_validation.get("blockers") or ["unknown"]),
        )
    job_validation = sensitivity_job.validate_cfd_numerical_sensitivity_job_manifest(
        job, trusted_pair_manifest=pair
    )
    if not job_validation.get("structurally_valid"):
        _error(
            "SENSITIVITY_JOB_MANIFEST_INVALID",
            ",".join(job_validation.get("blockers") or ["unknown"]),
        )
    if preparation_manifest.get("status") != "PENDING_SOLVER_EVIDENCE":
        _error("SENSITIVITY_PREPARATION_STATUS_INVALID")
    return study, pair, job, preparation_manifest


def _expected_variant(study, pair):
    child = pair["variant"]["case_child"]
    expected = (study / child).resolve(strict=False)
    if not expected.is_dir():
        _error("SENSITIVITY_VARIANT_CASE_MISSING", str(expected))
    return expected


def _check_variant_case(study, pair, variant_case):
    expected = _expected_variant(study, pair)
    if variant_case is not None:
        supplied = Path(variant_case).expanduser().resolve(strict=False)
        if supplied != expected:
            _error("SENSITIVITY_VARIANT_CASE_MISMATCH", str(supplied))
    try:
        processor_dirs = [
            child for child in expected.iterdir()
            if child.is_dir() and child.name.casefold().startswith("processor")
        ]
    except OSError as error:
        _error("SENSITIVITY_VARIANT_CASE_UNREADABLE", str(error))
    if processor_dirs:
        _error("SENSITIVITY_PROCESSOR_DIRECTORIES_FORBIDDEN", str(processor_dirs[0]))
    return expected


def run_serial_sensitivity_pair(study_dir: Path, variant_case: Path | None = None,
                                progress_cb=None, *, execute=True) -> dict:
    """Run the frozen first-/second-order children once, strictly in order.

    Completion only publishes post-run evidence hashes.  A separate call to
    :func:`verify_serial_sensitivity_pair` must reread those files and
    recompute the comparison before any ``numerical_sensitivity.v1`` PASS can
    exist.
    """
    study, pair, job, _ = _load_study(study_dir)
    expected_variant = _check_variant_case(study, pair, variant_case)
    if progress_cb is not None:
        progress_cb({"stage": "validated_frozen_inputs", "study_root": str(study)})
    if not execute:
        result = {
            "contract": EXECUTION_CONTRACT,
            "status": "PENDING_SOLVER_EVIDENCE",
            "valid": False,
            "study_root": str(study),
            "variant_case": str(expected_variant),
            "job_manifest_sha256": job.get("job_manifest_sha256"),
            "blockers": ["SOLVER_EXECUTION_PENDING"],
        }
        if progress_cb is not None:
            progress_cb({"stage": "pending_solver_evidence", "status": result["status"]})
        return result

    checkpoint_path = study / EXECUTION_FILENAME
    if checkpoint_path.exists():
        _error("SENSITIVITY_EXECUTION_ALREADY_STARTED", str(checkpoint_path))
    cases = {
        role: study / pair[role]["case_child"] for role in ("baseline", "variant")
    }
    for role, case in cases.items():
        _validate_prerun_case(case, pair[role])

    checkpoint = {
        "contract": EXECUTION_CONTRACT,
        "status": "RUNNING",
        "valid": False,
        "job_id": pair["job_id"],
        "pair_manifest_sha256": pair["manifest_sha256"],
        "job_manifest_sha256": job["job_manifest_sha256"],
        "target_flow_through_fraction": TARGET_FLOW_THROUGH_FRACTION,
        "started_at": _now(),
        "baseline": {"status": "PENDING"},
        "variant": {"status": "PENDING"},
        "blockers": ["SOLVER_RUNS_INCOMPLETE"],
    }
    _atomic_json(checkpoint_path, checkpoint)
    solver_root = study.parent
    token, owner = cfd_gci_job.acquire_solver_lock(solver_root)
    if token is None:
        try:
            checkpoint_path.unlink()
        except OSError:
            pass
        _error("CFD_SOLVER_BUSY", f"PID {(owner or {}).get('pid', 'unknown')}")
    try:
        with cfd_power.keep_system_awake():
            for role in ("baseline", "variant"):
                checkpoint[role] = {
                    "status": "RUNNING",
                    "profile": pair[role]["profile"],
                    "case_child": pair[role]["case_child"],
                    "started_at": _now(),
                }
                checkpoint["blockers"] = ["SOLVER_RUNS_INCOMPLETE"]
                _atomic_json(checkpoint_path, checkpoint)
                if progress_cb is not None:
                    progress_cb({"stage": f"{role}_running", "case": str(cases[role])})
                _run_case_to_target(
                    cases[role], TARGET_FLOW_THROUGH_FRACTION,
                    progress_cb=progress_cb,
                )
                checkpoint[role] = _completed_side(cases[role], pair[role])
                _atomic_json(checkpoint_path, checkpoint)
    except Exception as error:
        checkpoint["status"] = "FAIL"
        checkpoint["failed_at"] = _now()
        checkpoint["blockers"] = [str(error).split(":", 1)[0]]
        _atomic_json(checkpoint_path, checkpoint)
        raise
    finally:
        cfd_gci_job.release_solver_lock(solver_root, token)

    if (checkpoint["baseline"]["run_manifest_sha256"]
            == checkpoint["variant"]["run_manifest_sha256"]):
        checkpoint["status"] = "FAIL"
        checkpoint["blockers"] = ["SENSITIVITY_RUN_HASH_NOT_DISTINCT"]
        _atomic_json(checkpoint_path, checkpoint)
        _error("SENSITIVITY_RUN_HASH_NOT_DISTINCT")
    checkpoint["status"] = "SOLVER_RUNS_COMPLETE"
    checkpoint["completed_at"] = _now()
    checkpoint["blockers"] = ["INDEPENDENT_VERIFICATION_REQUIRED"]
    _atomic_json(checkpoint_path, checkpoint)
    result = {
        **checkpoint,
        "study_root": str(study),
        "variant_case": str(expected_variant),
    }
    if progress_cb is not None:
        progress_cb({"stage": "solver_runs_complete", "status": result["status"]})
    return result


def verify_serial_sensitivity_pair(study_dir: Path, current_case: Path, *,
                                   publish=True) -> dict:
    """Rehash raw paired evidence, recompute QoIs, and publish PASS atomically."""
    study, pair, job, _ = _load_study(study_dir)
    expected_variant = _check_variant_case(study, pair, current_case)
    try:
        selector = sensitivity_job.require_confirmed_occupied_volume_band(
            {key: value for key, value in pair["selector"].items()
             if key != "selector_sha256"}
        )
    except sensitivity_job.NumericalSensitivityJobInputError as error:
        _error("SENSITIVITY_CONFIRMED_SELECTOR_REQUIRED", str(error))
    _verify_selector_evidence(selector)

    checkpoint_path = study / EXECUTION_FILENAME
    if not checkpoint_path.is_file():
        return {
            "contract": EXECUTION_CONTRACT,
            "status": "NOT_EVALUATED",
            "valid": False,
            "study_root": str(study),
            "current_case": str(expected_variant),
            "job_manifest_sha256": job.get("job_manifest_sha256"),
            "blockers": ["SOLVER_EVIDENCE_MISSING"],
        }
    checkpoint = _load_object(
        checkpoint_path, "SENSITIVITY_EXECUTION_CHECKPOINT_MISSING")
    if (checkpoint.get("contract") != EXECUTION_CONTRACT
            or checkpoint.get("status") != "SOLVER_RUNS_COMPLETE"
            or checkpoint.get("job_id") != pair["job_id"]
            or checkpoint.get("pair_manifest_sha256") != pair["manifest_sha256"]
            or checkpoint.get("job_manifest_sha256") != job["job_manifest_sha256"]
            or checkpoint.get("target_flow_through_fraction") != TARGET_FLOW_THROUGH_FRACTION):
        _error("SENSITIVITY_EXECUTION_CHECKPOINT_INVALID")

    cases = {role: study / pair[role]["case_child"] for role in ("baseline", "variant")}
    sides = {}
    qois = {}
    windows = {}
    for role in ("baseline", "variant"):
        case = cases[role]
        _rehash_physical_tree(case, pair["shared_input"]["physical_tree"])
        side_checkpoint = checkpoint.get(role)
        if (not isinstance(side_checkpoint, dict)
                or side_checkpoint.get("status") != "COMPLETE"
                or side_checkpoint.get("profile") != pair[role]["profile"]
                or side_checkpoint.get("case_child") != pair[role]["case_child"]
                or side_checkpoint.get("case_seed_snapshot_sha256") != (
                    pair[role]["case_seed_snapshot_sha256"])):
            _error("SENSITIVITY_EXECUTION_SIDE_INCOMPLETE", role)
        result_evidence = _verify_case_result_artifacts(
            case, pair, side_checkpoint)
        run = result_evidence["run"]
        _verify_effective_numerics_files(case, run, pair[role]["profile"])
        progress = run.get("thermal_progress")
        if not isinstance(progress, dict):
            _error("SENSITIVITY_FLOW_THROUGH_EVIDENCE_MISSING", role)
        try:
            flow_time = float(progress["flow_through_time_s"])
            latest = float(progress["latest_time_s"])
        except (KeyError, TypeError, ValueError):
            _error("SENSITIVITY_FLOW_THROUGH_EVIDENCE_INVALID", role)
        if (not math.isfinite(flow_time) or flow_time <= 0
                or not math.isfinite(latest)
                or _flow_fraction(run) + 1e-12 < TARGET_FLOW_THROUGH_FRACTION):
            _error("SENSITIVITY_MINIMUM_FLOW_THROUGH_NOT_REACHED", role)
        try:
            checkpoint_fraction = float(side_checkpoint["flow_through_fraction"])
        except (KeyError, TypeError, ValueError):
            _error("SENSITIVITY_CHECKPOINT_FLOW_THROUGH_INVALID", role)
        if (not math.isfinite(checkpoint_fraction)
                or not math.isclose(
                    checkpoint_fraction, _flow_fraction(run),
                    rel_tol=1e-9, abs_tol=1e-9)):
            _error("SENSITIVITY_CHECKPOINT_FLOW_THROUGH_MISMATCH", role)
        window_start = latest - 0.1 * flow_time
        windows[role] = {
            "start_s": window_start,
            "end_s": latest,
            "flow_through_fraction_start": _flow_fraction(run) - 0.1,
            "flow_through_fraction_end": _flow_fraction(run),
        }
        vtu_paths = sorted(case.glob("VTK/**/internal.vtu"))
        occupied = cfd_post.compute_time_weighted_occupied_volume_qois(
            vtu_paths, selector,
            floor_elevation_m=_occupied_floor_elevation(run, case),
            window_start_s=window_start, window_end_s=latest,
            minimum_samples=5,
        )
        exhaust = cfd_post.read_time_weighted_exhaust_temperature_rise_from_case(
            case, window_start_s=window_start, window_end_s=latest,
            minimum_samples=5,
        )
        qois[role] = {
            "occupied_zone_mean_temperature_k": occupied[
                "occupied_zone_mean_temperature_k"],
            "occupied_zone_mean_speed_m_s": occupied[
                "occupied_zone_mean_speed_m_s"],
            "exhaust_temperature_rise_k": exhaust["exhaust_temperature_rise_k"],
        }
        sides[role] = {
            "profile": pair[role]["profile"],
            "run_hash": result_evidence["run_manifest_sha256"],
            "mesh_hash": pair[role]["mesh_sha256"],
            "physical_input_hash": pair[role]["physical_input_sha256"],
            "solver_evidence": _solver_evidence_from_run(run),
        }
        result_evidence.pop("run")
        sides[role + "_verification"] = result_evidence

    if sides["baseline"]["run_hash"] == sides["variant"]["run_hash"]:
        _error("SENSITIVITY_RUN_HASH_NOT_DISTINCT")
    if not math.isclose(
            windows["baseline"]["end_s"] - windows["baseline"]["start_s"],
            windows["variant"]["end_s"] - windows["variant"]["start_s"],
            rel_tol=1e-9, abs_tol=1e-9):
        _error("SENSITIVITY_FINAL_WINDOW_MISMATCH")
    if not math.isclose(
            windows["baseline"]["flow_through_fraction_end"],
            windows["variant"]["flow_through_fraction_end"],
            rel_tol=1e-9, abs_tol=1e-9):
        _error("SENSITIVITY_FINAL_WINDOW_FRACTION_MISMATCH")
    limits = {
        item["name"]: item["limit"] for item in job["qoi_plan"]["definitions"]
    }
    comparisons = []
    for name in cfd_numerics.REQUIRED_SENSITIVITY_QOIS:
        baseline_value = float(qois["baseline"][name])
        variant_value = float(qois["variant"][name])
        difference = abs(variant_value - baseline_value)
        if not all(math.isfinite(value) for value in (
                baseline_value, variant_value, difference, limits[name])):
            _error("SENSITIVITY_QOI_VALUE_INVALID", name)
        if difference > limits[name]:
            _error("SENSITIVITY_QOI_LIMIT_FAILED", name)
        comparisons.append({
            "name": name,
            "baseline": baseline_value,
            "variant": variant_value,
            "absolute_difference": difference,
            "limit": limits[name],
            "passed": True,
        })

    verification_evidence = {
        "contract": "numerical_sensitivity_verification_evidence.v1",
        "created_at": checkpoint.get("completed_at"),
        "study_root": str(study),
        "pair_manifest_sha256": pair["manifest_sha256"],
        "job_manifest_sha256": job["job_manifest_sha256"],
        "execution_checkpoint_sha256": _sha256_file(checkpoint_path),
        "windows": windows,
        "baseline": sides.pop("baseline_verification"),
        "variant": sides.pop("variant_verification"),
    }
    evidence_path = study / "numerical_sensitivity_verification.v1.json"
    evidence_sha256 = _json_payload_sha256(verification_evidence)
    if publish:
        _atomic_json(evidence_path, verification_evidence)
        if _sha256_file(evidence_path) != evidence_sha256:
            _error("SENSITIVITY_VERIFICATION_EVIDENCE_WRITE_MISMATCH")
    verification = {
        "contract": "numerical_sensitivity_verification.v1",
        "verifier": "run_numerical_sensitivity.verify_serial_sensitivity_pair",
        "raw_artifacts_rehashed": True,
        "study_root": str(study),
        "current_case_child": pair["variant"]["case_child"],
        "evidence_path": evidence_path.name,
        "evidence_sha256": evidence_sha256,
    }
    final = {
        "contract": cfd_numerics.SENSITIVITY_CONTRACT,
        "status": "PASS",
        "provenance": {
            "explicit_job": True,
            "source": "cfd_numerical_sensitivity_job",
            "job_id": pair["job_id"],
        },
        "baseline": sides["baseline"],
        "variant": sides["variant"],
        "allowed_variation": job["allowed_variation"],
        "qoi_comparisons": comparisons,
        "verification": verification,
    }
    if "validation_anchor" in job:
        final["validation_anchor"] = job["validation_anchor"]
    validation = cfd_numerics.validate_numerical_sensitivity(final)
    if not validation["valid"]:
        _error("SENSITIVITY_FINAL_ARTIFACT_INVALID", ",".join(validation["blockers"]))
    if publish:
        _atomic_json(study / "numerical_sensitivity.v1.json", final)
        # This case-local copy is a discovery pointer only.  Consumers must
        # rerun the central verifier using verification.study_root; the job
        # contract explicitly keeps case-local files non-authoritative.
        _atomic_json(expected_variant / "numerical_sensitivity.json", final)
    return {
        **final,
        "valid": True,
        "study_root": str(study),
        "current_case": str(expected_variant),
        "blockers": [],
    }


def _json_payload_sha256(value):
    try:
        encoded = (
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        _error("SENSITIVITY_VERIFICATION_EVIDENCE_INVALID", str(error))
    return hashlib.sha256(encoded).hexdigest()


def _verify_selector_evidence(selector):
    for name in ("geometry_ref", "zone_ref"):
        reference = selector[name]
        path = Path(reference["path"]).expanduser().resolve(strict=False)
        if path.is_symlink() or not path.is_file():
            _error("SENSITIVITY_SELECTOR_EVIDENCE_MISSING", name)
        if _sha256_file(path) != reference["sha256"]:
            _error("SENSITIVITY_SELECTOR_EVIDENCE_HASH_MISMATCH", name)


def _rehash_physical_tree(case, physical_tree):
    entries = physical_tree.get("entries") if isinstance(physical_tree, dict) else None
    if not isinstance(entries, list) or not entries:
        _error("SENSITIVITY_PHYSICAL_TREE_INVALID")
    for entry in entries:
        try:
            path = preparation._safe_case_path(
                case, entry.get("path"), require_directory=(
                    entry.get("path") == "constant/polyMesh"))
            digest = preparation._hash_regular_tree(path)
        except preparation.NumericalSensitivityPreparationError as error:
            _error("SENSITIVITY_PHYSICAL_TREE_REHASH_FAILED", str(error))
        if digest != entry.get("sha256"):
            _error("SENSITIVITY_PHYSICAL_TREE_HASH_MISMATCH", entry.get("path"))


def _verify_hash_ref(case, raw, code):
    if not isinstance(raw, dict) or "path" not in raw or "sha256" not in raw:
        _error(code)
    path = _safe_child_file(case, raw.get("path"), code)
    if _sha256_file(path) != raw.get("sha256"):
        _error(code, raw.get("path"))
    return path


def _verify_case_result_artifacts(case, pair, checkpoint_side):
    run_path = _safe_child_file(case, "run_manifest.json", "SENSITIVITY_RUN_MISSING")
    result_path = _safe_child_file(
        case, "result_manifest.json", "SENSITIVITY_RESULT_MISSING")
    run_hash = _sha256_file(run_path)
    result_hash = _sha256_file(result_path)
    if run_hash != checkpoint_side.get("run_manifest_sha256"):
        _error("SENSITIVITY_RUN_HASH_MISMATCH", str(case))
    if result_hash != checkpoint_side.get("result_manifest_sha256"):
        _error("SENSITIVITY_RESULT_HASH_MISMATCH", str(case))
    logs = checkpoint_side.get("solver_logs")
    if not isinstance(logs, list) or not logs:
        _error("SENSITIVITY_SOLVER_LOG_EVIDENCE_INVALID", str(case))
    normalised_logs = []
    for entry in logs:
        path = _verify_hash_ref(case, entry, "SENSITIVITY_SOLVER_LOG_HASH_MISMATCH")
        normalised_logs.append({
            "path": path.relative_to(case).as_posix(),
            "sha256": _sha256_file(path),
        })
    if sensitivity_job.canonical_sha256(normalised_logs) != checkpoint_side.get(
            "solver_log_tree_sha256"):
        _error("SENSITIVITY_SOLVER_LOG_TREE_HASH_MISMATCH", str(case))
    run = _load_object(run_path, "SENSITIVITY_RUN_MANIFEST_INVALID")
    result = _load_object(result_path, "SENSITIVITY_RESULT_MANIFEST_INVALID")
    if result.get("contract") != "result_manifest.v1":
        _error("SENSITIVITY_RESULT_MANIFEST_INVALID", str(case))
    try:
        run_time = float(run["thermal_progress"]["latest_time_s"])
        result_time = float(result["time_s"])
    except (KeyError, TypeError, ValueError):
        _error("SENSITIVITY_RESULT_TIME_INVALID", str(case))
    if (not math.isfinite(run_time) or not math.isfinite(result_time)
            or not math.isclose(run_time, result_time, rel_tol=1e-9, abs_tol=1e-9)):
        _error("SENSITIVITY_RESULT_TIME_MISMATCH", str(case))
    if result.get("run_manifest_sha256") != run_hash:
        _error("SENSITIVITY_RESULT_RUN_BINDING_MISMATCH", str(case))
    mesh_path = _safe_child_file(
        case, "mesh_manifest.json", "SENSITIVITY_MESH_MANIFEST_MISSING")
    mesh_hash = _sha256_file(mesh_path)
    if (mesh_hash != pair["shared_input"]["mesh_sha256"]
            or result.get("mesh_manifest_sha256") != mesh_hash):
        _error("SENSITIVITY_MESH_HASH_MISMATCH", str(case))
    source_path = _verify_hash_ref(
        case, result.get("source"), "SENSITIVITY_RESULT_SOURCE_HASH_MISMATCH")
    summary_path = _safe_child_file(
        case, result.get("summary_path"), "SENSITIVITY_RESULT_SUMMARY_INVALID")
    if _sha256_file(summary_path) != result.get("summary_sha256"):
        _error("SENSITIVITY_RESULT_SUMMARY_HASH_MISMATCH", str(case))
    summary = _load_object(summary_path, "SENSITIVITY_RESULT_SUMMARY_INVALID")
    try:
        summary_time = float(summary["time_s"])
    except (KeyError, TypeError, ValueError):
        _error("SENSITIVITY_RESULT_SUMMARY_TIME_INVALID", str(case))
    if (not math.isfinite(summary_time)
            or not math.isclose(summary_time, result_time, rel_tol=1e-9, abs_tol=1e-9)):
        _error("SENSITIVITY_RESULT_SUMMARY_TIME_MISMATCH", str(case))
    slices = result.get("slices")
    if not isinstance(slices, list):
        _error("SENSITIVITY_RESULT_SLICES_INVALID", str(case))
    for item in slices:
        _verify_hash_ref(case, item, "SENSITIVITY_RESULT_SLICE_HASH_MISMATCH")
    return {
        "run": run,
        "run_manifest_sha256": run_hash,
        "result_manifest_sha256": result_hash,
        "mesh_manifest_sha256": mesh_hash,
        "source_vtu": {
            "path": source_path.relative_to(case).as_posix(),
            "sha256": _sha256_file(source_path),
        },
        "solver_log_tree_sha256": checkpoint_side["solver_log_tree_sha256"],
    }


def _occupied_floor_elevation(run, case):
    settings = run.get("effective_settings")
    value = settings.get("occupied_floor_elevation_m") if isinstance(settings, dict) else None
    if value is None:
        thermal = _load_object(
            Path(case) / "thermal_input.json", "SENSITIVITY_THERMAL_INPUT_MISSING")
        thermal_settings = thermal.get("settings")
        value = (thermal_settings.get("occupied_floor_elevation_m")
                 if isinstance(thermal_settings, dict) else None)
    try:
        value = float(value)
    except (TypeError, ValueError):
        _error("SENSITIVITY_OCCUPIED_FLOOR_ELEVATION_MISSING")
    if not math.isfinite(value):
        _error("SENSITIVITY_OCCUPIED_FLOOR_ELEVATION_INVALID")
    return value


def _solver_evidence_from_run(run):
    solver = run.get("solver")
    settings = run.get("effective_settings")
    thermal = run.get("thermal")
    quality = run.get("numerical_quality")
    if not all(isinstance(item, dict) for item in (solver, settings, thermal)):
        _error("SENSITIVITY_SOLVER_EVIDENCE_INVALID")
    residuals = {}
    final_rows = solver.get("thermal_residuals")
    histories = solver.get("thermal_residual_history")
    if not isinstance(final_rows, dict) or not isinstance(histories, dict):
        _error("SENSITIVITY_SOLVER_RESIDUAL_EVIDENCE_INVALID")
    for field, limit in cfd_numerics.THERMAL_RESIDUAL_LIMITS.items():
        row = final_rows.get(field)
        history = histories.get(field)
        if not isinstance(row, dict) or not isinstance(history, list):
            _error("SENSITIVITY_SOLVER_RESIDUAL_EVIDENCE_INVALID", field)
        tail = [item.get("final") for item in history[-cfd_numerics.DEFAULT_RESIDUAL_TAIL_SAMPLES:]
                if isinstance(item, dict)]
        try:
            final = float(row["final"])
            tail = [float(value) for value in tail]
        except (KeyError, TypeError, ValueError):
            _error("SENSITIVITY_SOLVER_RESIDUAL_EVIDENCE_INVALID", field)
        if len(tail) < cfd_numerics.DEFAULT_RESIDUAL_TAIL_SAMPLES:
            _error("SENSITIVITY_SOLVER_RESIDUAL_TAIL_MISSING", field)
        residuals[field] = {
            "final": final,
            "tail_maximum": max(tail),
            "tail_samples": len(tail),
            "limit": limit,
        }
    courant = solver.get("courant")
    continuity = solver.get("continuity")
    phi = (quality.get("flux_balance") if isinstance(quality, dict)
           else run.get("flux_balance"))
    if not all(isinstance(item, dict) for item in (courant, continuity, phi)):
        _error("SENSITIVITY_SOLVER_EVIDENCE_INVALID")
    return {
        "ended": solver.get("ended") is True,
        "fatal_error": solver.get("fatal") is True,
        "peak_courant": courant.get("peak_maximum"),
        "courant_limit": settings.get(
            "thermal_design_max_courant_gate",
            settings.get("thermal_max_courant_gate", 1.0)),
        "residuals": residuals,
        "continuity": {"global": continuity.get("global"), "limit": 1e-6},
        "phi_balance": {
            "available": phi.get("available") is True,
            "imbalance_ratio": phi.get("imbalance_ratio"),
            "limit": settings.get("terminal_phi_imbalance_max", 0.001),
        },
        "energy_closure_basis": thermal.get("energy_closure_basis"),
    }


def _verify_effective_numerics_files(case, run, expected_profile):
    numerics = run.get("effective_numerics")
    if (not isinstance(numerics, dict)
            or numerics.get("profile") != expected_profile):
        _error("SENSITIVITY_EFFECTIVE_PROFILE_MISMATCH", str(case))
    try:
        fv_schemes = _safe_child_file(
            case, "system/fvSchemes", "SENSITIVITY_FVSCHEMES_MISSING"
        ).read_text(encoding="utf-8")
        fv_solution = _safe_child_file(
            case, "system/fvSolution", "SENSITIVITY_FVSOLUTION_MISSING"
        ).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        _error("SENSITIVITY_NUMERICS_FILES_UNREADABLE", str(error))
    validation = cfd_numerics.validate_effective_openfoam_numerics(
        numerics, fv_schemes, fv_solution)
    if not validation.get("valid"):
        _error(
            "SENSITIVITY_EFFECTIVE_NUMERICS_INVALID",
            ",".join(validation.get("issues") or ["unknown"]),
        )


def _json_arg(path, code):
    return _load_object(path, code)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study_dir", nargs="?", type=Path)
    parser.add_argument("--mesh-case", type=Path)
    parser.add_argument("--study-root", type=Path)
    parser.add_argument("--selector-json", type=Path)
    parser.add_argument("--qoi-limits-json", type=Path)
    parser.add_argument("--variant-case", type=Path)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path)
    return parser


def _write_output(path, result):
    if path is None:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.mesh_case is not None or args.study_root is not None:
            if not (args.mesh_case and args.study_root and args.selector_json
                    and args.qoi_limits_json):
                _error("SENSITIVITY_PREPARE_ARGUMENTS_REQUIRED")
            result = preparation.prepare_serial_sensitivity_pair(
                args.mesh_case,
                args.study_root,
                selector=_json_arg(args.selector_json, "SENSITIVITY_SELECTOR_INVALID"),
                qoi_limits=_json_arg(args.qoi_limits_json, "SENSITIVITY_QOI_LIMITS_INVALID"),
            )
        else:
            if args.study_dir is None:
                _error("SENSITIVITY_STUDY_REQUIRED")
            if args.verify:
                if args.variant_case is None:
                    _error("SENSITIVITY_VARIANT_CASE_REQUIRED")
                result = verify_serial_sensitivity_pair(args.study_dir, args.variant_case)
            else:
                result = run_serial_sensitivity_pair(
                    args.study_dir, args.variant_case, execute=args.execute
                )
        _write_output(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if result.get("status") == "PASS" else 2
    except (NumericalSensitivityExecutionError,
            preparation.NumericalSensitivityPreparationError,
            sensitivity_job.NumericalSensitivityJobInputError,
            cfd_post.PostprocessEvidenceError) as error:
        result = {
            "contract": EXECUTION_CONTRACT,
            "status": "NOT_EVALUATED",
            "valid": False,
            "blockers": [str(error).split(":", 1)[0]],
            "error": str(error),
        }
        _write_output(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
