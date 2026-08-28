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


def verify_serial_sensitivity_pair(study_dir: Path, current_case: Path) -> dict:
    """Verify the immutable pre-run binding without inventing post-run PASS."""
    study, pair, job, _ = _load_study(study_dir)
    expected_variant = _check_variant_case(study, pair, current_case)
    blockers = []
    seed_path = expected_variant / "case_seed_snapshot.v1.json"
    result_manifest = expected_variant / "result_manifest.json"
    if not seed_path.is_file():
        blockers.append("CASE_SEED_SNAPSHOT_MISSING")
    elif sensitivity_job.canonical_sha256(
            json.loads(seed_path.read_text(encoding="utf-8"))) != pair["variant"]["case_seed_snapshot_sha256"]:
        blockers.append("CASE_SEED_SNAPSHOT_HASH_MISMATCH")
    if not result_manifest.is_file():
        blockers.append("SOLVER_EVIDENCE_MISSING")
    else:
        # A result file alone is not trusted evidence.  The future verifier
        # must rehash the complete run/log/mesh/result and recompute QoIs.
        blockers.append("SOLVER_EVIDENCE_VERIFIER_PENDING")
    return {
        "contract": EXECUTION_CONTRACT,
        "status": "NOT_EVALUATED",
        "valid": False,
        "study_root": str(study),
        "current_case": str(expected_variant),
        "job_manifest_sha256": job.get("job_manifest_sha256"),
        "blockers": list(dict.fromkeys(blockers)),
    }


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
            sensitivity_job.NumericalSensitivityJobInputError) as error:
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
