"""Fail-closed temporal sensitivity runner boundary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cfd_temporal_sensitivity as temporal


EXECUTION_CONTRACT = "temporal_sensitivity_execution.v1"


class TemporalSensitivityExecutionError(ValueError):
    pass


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
    if execute:
        result = {
            "contract": EXECUTION_CONTRACT,
            "status": "NOT_EVALUATED",
            "valid": False,
            "study_root": str(study),
            "manifest_sha256": manifest["manifest_sha256"],
            "blockers": ["SOLVER_EXECUTION_DISABLED", "WSL_OR_OPENFOAM_REQUIRED"],
        }
    else:
        result = {
            "contract": EXECUTION_CONTRACT,
            "status": "PENDING_SOLVER_EVIDENCE",
            "valid": False,
            "study_root": str(study),
            "manifest_sha256": manifest["manifest_sha256"],
            "blockers": ["SOLVER_EXECUTION_PENDING", "WSL_OR_OPENFOAM_REQUIRED"],
        }
    if progress_cb:
        progress_cb({"stage": "pending_solver_evidence", "status": result["status"]})
    return result


def verify_temporal_study(study_dir: Path, current_case: Path) -> dict:
    study, manifest = _load_manifest(study_dir)
    expected = (study / "fine_dt_0p01").resolve(strict=False)
    current = Path(current_case).expanduser().resolve(strict=False)
    blockers = []
    if current != expected:
        blockers.append("TEMPORAL_CURRENT_CASE_NOT_FINE_CHILD")
    if not current.is_dir():
        blockers.append("TEMPORAL_CURRENT_CASE_MISSING")
    else:
        try:
            current_hash = temporal._tree_sha256(current)
        except temporal.TemporalSensitivityInputError as error:
            blockers.append(str(error).split(":", 1)[0])
        else:
            if current_hash != manifest["seed_tree_sha256"]:
                blockers.append("TEMPORAL_SEED_HASH_MISMATCH")
        if (current / "result_manifest.json").is_file():
            blockers.append("TEMPORAL_SOLVER_EVIDENCE_VERIFIER_PENDING")
        else:
            blockers.append("TEMPORAL_SOLVER_EVIDENCE_MISSING")
    return {
        "contract": EXECUTION_CONTRACT,
        "status": "NOT_EVALUATED",
        "valid": False,
        "study_root": str(study),
        "current_case": str(current),
        "manifest_sha256": manifest["manifest_sha256"],
        "blockers": list(dict.fromkeys(blockers)),
    }


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study_dir", nargs="?", type=Path)
    parser.add_argument("--case-seed", type=Path)
    parser.add_argument("--study-root", type=Path)
    parser.add_argument("--delta-t", nargs=3, type=float, default=[0.04, 0.02, 0.01])
    parser.add_argument("--anchor-fine-case", type=Path)
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
            manifest = temporal.create_temporal_study(
                args.case_seed, args.delta_t, args.anchor_fine_case
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
