"""Fail-closed orchestration boundary for serial scheme sensitivity.

The preparation module creates immutable first-/second-order children, but it
does not run OpenFOAM.  This module is the deliberate boundary between that
preparation and a future solver executor.  Until a trusted serial executor
and post-run verifier are available, every request remains pending and no
solver process is started.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cfd_numerical_sensitivity_job as sensitivity_job
import cfd_numerical_sensitivity_runner as preparation


EXECUTION_CONTRACT = "serial_numerical_sensitivity_execution.v1"


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
                                progress_cb=None, *, execute=False) -> dict:
    """Request a serial pair run without silently launching a solver.

    ``execute=True`` is intentionally fail-closed until the trusted executor
    is implemented and runtime capability evidence is PASS.  The returned
    pending contract is not numerical sensitivity evidence and cannot make a
    case design-ready.
    """
    study, pair, job, _ = _load_study(study_dir)
    expected_variant = _check_variant_case(study, pair, variant_case)
    if progress_cb is not None:
        progress_cb({"stage": "validated_frozen_inputs", "study_root": str(study)})
    if execute:
        result = {
            "contract": EXECUTION_CONTRACT,
            "status": "NOT_EVALUATED",
            "valid": False,
            "study_root": str(study),
            "variant_case": str(expected_variant),
            "job_manifest_sha256": job.get("job_manifest_sha256"),
            "blockers": ["SOLVER_EXECUTION_DISABLED", "WSL_OR_OPENFOAM_REQUIRED"],
        }
    else:
        result = {
            "contract": EXECUTION_CONTRACT,
            "status": "PENDING_SOLVER_EVIDENCE",
            "valid": False,
            "study_root": str(study),
            "variant_case": str(expected_variant),
            "job_manifest_sha256": job.get("job_manifest_sha256"),
            "blockers": ["SOLVER_EXECUTION_PENDING", "WSL_OR_OPENFOAM_REQUIRED"],
        }
    if progress_cb is not None:
        progress_cb({"stage": "pending_solver_evidence", "status": result["status"]})
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
