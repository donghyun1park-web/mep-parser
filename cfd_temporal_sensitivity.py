"""Immutable, serial-only temporal sensitivity preparation contract.

This module freezes three fixed-step children (0.04/0.02/0.01 s) but never
starts OpenFOAM.  Until a trusted executor and post-run verifier exist, the
result remains ``PENDING_SOLVER_EVIDENCE``.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import stat

import cfd_numerical_sensitivity_job as sensitivity_job
import cfd_validation_anchor


CONTRACT = "temporal_sensitivity.v1"
PENDING_STATUS = "PENDING_SOLVER_EVIDENCE"
FIXED_DELTA_T_S = (0.04, 0.02, 0.01)
MAX_CO = 1.0
_CHILD_NAMES = ("coarse_dt_0p04", "medium_dt_0p02", "fine_dt_0p01")
_FORBIDDEN_NAMES = {"run_manifest.json", "result_manifest.json", "thermal_progress.json"}
_TIME_LINE = re.compile(
    r"(?m)^\s*Time\s*=\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*$"
)


class TemporalSensitivityInputError(ValueError):
    """Raised when temporal study input is not immutable and pre-run safe."""


def _fail(code, detail=None):
    message = str(code)
    if detail:
        message = f"{message}: {detail}"
    raise TemporalSensitivityInputError(message)


def _canonical_sha256(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path):
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        _fail("TEMPORAL_SEED_READ_FAILED", str(error))
    return digest.hexdigest()


def _tree_sha256(root):
    root = Path(root).expanduser().resolve(strict=False)
    if not root.is_dir() or root.is_symlink():
        _fail("TEMPORAL_SEED_DIRECTORY_INVALID", str(root))
    entries = []
    try:
        paths = sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())
    except OSError as error:
        _fail("TEMPORAL_SEED_READ_FAILED", str(error))
    for path in paths:
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            _fail("TEMPORAL_SEED_LINK_FORBIDDEN", relative)
        try:
            mode = path.lstat().st_mode
        except OSError as error:
            _fail("TEMPORAL_SEED_READ_FAILED", str(error))
        if stat.S_ISDIR(mode):
            entries.append({"path": relative, "kind": "directory"})
        elif stat.S_ISREG(mode):
            entries.append({"path": relative, "kind": "file", "sha256": _sha256_file(path)})
        else:
            _fail("TEMPORAL_SEED_UNSAFE_NODE", relative)
    return _canonical_sha256({"contract": "temporal_seed_tree.v1", "entries": entries})


def _validate_seed(seed):
    seed = Path(seed).expanduser().resolve(strict=False)
    if not seed.is_dir():
        _fail("TEMPORAL_SEED_DIRECTORY_INVALID", str(seed))
    try:
        for child in seed.iterdir():
            if child.is_dir() and child.name.casefold().startswith("processor"):
                _fail("TEMPORAL_PROCESSOR_DIRECTORIES_FORBIDDEN", str(child))
        for forbidden in _FORBIDDEN_NAMES:
            if (seed / forbidden).exists():
                _fail("TEMPORAL_POST_RUN_ARTIFACT_FORBIDDEN", forbidden)
    except OSError as error:
        _fail("TEMPORAL_SEED_READ_FAILED", str(error))
    return seed


def _normalise_levels(levels):
    if not isinstance(levels, (list, tuple)) or len(levels) != 3:
        _fail("TEMPORAL_LEVELS_INVALID")
    values = []
    for value in levels:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            _fail("TEMPORAL_LEVELS_INVALID")
        values.append(float(value))
    if any(not math.isclose(value, expected, rel_tol=0.0, abs_tol=1e-12)
           for value, expected in zip(values, FIXED_DELTA_T_S)):
        _fail("TEMPORAL_LEVELS_INVALID", "required 0.04, 0.02, 0.01 s")
    return list(FIXED_DELTA_T_S)


def verify_fixed_delta_t_history(log_paths, expected_delta_t_s):
    """Reconstruct actual solver steps and reject any controller intervention."""
    try:
        expected = float(expected_delta_t_s)
    except (TypeError, ValueError):
        expected = math.nan
    blockers = []
    if not math.isfinite(expected) or expected <= 0:
        blockers.append("TEMPORAL_EXPECTED_DELTA_T_INVALID")
    if not isinstance(log_paths, (list, tuple)) or not log_paths:
        blockers.append("TEMPORAL_TIME_HISTORY_MISSING")
        paths = []
    else:
        paths = [Path(path) for path in log_paths]
    times = []
    source_logs = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            blockers.append("TEMPORAL_TIME_HISTORY_READ_FAILED")
            continue
        source_logs.append(path.name)
        try:
            times.extend(float(value) for value in _TIME_LINE.findall(text))
        except ValueError:
            blockers.append("TEMPORAL_TIME_HISTORY_INVALID")
    ordered = sorted(set(times))
    if not ordered or any(not math.isfinite(value) or value <= 0 for value in ordered):
        blockers.append("TEMPORAL_TIME_HISTORY_MISSING")
        steps = []
    else:
        steps = [current - previous for previous, current in zip([0.0, *ordered[:-1]], ordered)]
        if (math.isfinite(expected)
                and any(not math.isclose(
                    step, expected, rel_tol=0.0, abs_tol=max(1e-12, expected * 1e-9)
                ) for step in steps)):
            blockers.append("TEMPORAL_ACTUAL_DELTA_T_MISMATCH")
    return {
        "valid": not blockers,
        "expected_delta_t_s": expected_delta_t_s,
        "sample_count": len(ordered),
        "minimum_delta_t_s": min(steps) if steps else None,
        "maximum_delta_t_s": max(steps) if steps else None,
        "first_time_s": ordered[0] if ordered else None,
        "last_time_s": ordered[-1] if ordered else None,
        "source_logs": source_logs,
        "blockers": list(dict.fromkeys(blockers)),
    }


def calculate_temporal_richardson(
        name, *, coarse, medium, fine, fixed_delta_t_s,
        near_zero_floor, relative_limit_pct, absolute_limit):
    """Evaluate a uniform three-level first-order temporal refinement."""
    levels = _normalise_levels(fixed_delta_t_s)
    try:
        values = [float(coarse), float(medium), float(fine)]
        floor = float(near_zero_floor)
        relative_limit = float(relative_limit_pct)
        absolute = float(absolute_limit)
    except (TypeError, ValueError):
        _fail("TEMPORAL_RICHARDSON_INPUT_INVALID", str(name))
    if (not all(math.isfinite(value) for value in values)
            or not all(math.isfinite(value) and value > 0
                       for value in (floor, relative_limit, absolute))):
        _fail("TEMPORAL_RICHARDSON_INPUT_INVALID", str(name))
    ratios = [levels[0] / levels[1], levels[1] / levels[2]]
    if (min(ratios) < 1.8
            or not math.isclose(ratios[0], ratios[1], rel_tol=1e-9, abs_tol=1e-12)):
        _fail("TEMPORAL_REFINEMENT_RATIO_INVALID", str(name))
    coarse_value, medium_value, fine_value = values
    coarse_medium = medium_value - coarse_value
    medium_fine = fine_value - medium_value
    scale = max(abs(value) for value in values)
    tiny = max(1.0, scale) * 1e-12
    result = {
        "name": str(name),
        "coarse": coarse_value,
        "medium": medium_value,
        "fine": fine_value,
        "fixed_delta_t_s": levels,
        "refinement_ratios": ratios,
        "convergence": None,
        "observed_order": None,
        "extrapolated": None,
        "safety_factor": 1.25,
        "uncertainty_fine": None,
        "uncertainty_fine_pct": None,
        "relative_limit_pct": relative_limit,
        "medium_fine_absolute_difference": abs(medium_fine),
        "absolute_limit": absolute,
        "asymptotic_ratio": None,
        "status": "NOT_EVALUATED",
        "blockers": [],
    }
    if (abs(coarse_medium) <= tiny or abs(medium_fine) <= tiny
            or coarse_medium * medium_fine <= 0):
        result["convergence"] = "non_monotonic"
        result["blockers"] = ["TEMPORAL_CONVERGENCE_NON_MONOTONIC"]
        return result
    observed_order = math.log(abs(coarse_medium / medium_fine)) / math.log(ratios[1])
    result["convergence"] = "monotonic"
    result["observed_order"] = observed_order
    if not math.isfinite(observed_order) or not 0.5 <= observed_order <= 1.5:
        result["blockers"] = ["TEMPORAL_OBSERVED_ORDER_OUT_OF_RANGE"]
        return result
    denominator = ratios[1] ** observed_order - 1.0
    if denominator <= 0:
        result["convergence"] = "indeterminate"
        result["blockers"] = ["TEMPORAL_RICHARDSON_INDETERMINATE"]
        return result
    extrapolated = fine_value + (fine_value - medium_value) / denominator
    uncertainty = 1.25 * abs(extrapolated - fine_value)
    uncertainty_pct = uncertainty / max(abs(fine_value), floor) * 100.0
    asymptotic_ratio = abs(coarse_medium / medium_fine) / (
        ratios[1] ** observed_order
    )
    result.update({
        "extrapolated": extrapolated,
        "uncertainty_fine": uncertainty,
        "uncertainty_fine_pct": uncertainty_pct,
        "asymptotic_ratio": asymptotic_ratio,
    })
    if not 0.8 <= asymptotic_ratio <= 1.2:
        result["blockers"] = ["TEMPORAL_ASYMPTOTIC_RATIO_FAILED"]
        return result
    if uncertainty_pct > relative_limit or abs(medium_fine) > absolute:
        result["status"] = "FAIL"
        if uncertainty_pct > relative_limit:
            result["blockers"].append("TEMPORAL_UNCERTAINTY_LIMIT_FAILED")
        if abs(medium_fine) > absolute:
            result["blockers"].append("TEMPORAL_MEDIUM_FINE_LIMIT_FAILED")
        return result
    result["status"] = "PASS"
    return result


def _without_manifest_hash(manifest):
    copied = dict(manifest)
    copied.pop("manifest_sha256", None)
    return copied


def validate_temporal_manifest(manifest):
    blockers = []
    if not isinstance(manifest, dict):
        return {"valid": False, "blockers": ["TEMPORAL_MANIFEST_MISSING"]}
    if manifest.get("contract") != CONTRACT:
        blockers.append("TEMPORAL_CONTRACT_INVALID")
    if manifest.get("status") != PENDING_STATUS:
        blockers.append("TEMPORAL_STATUS_INVALID")
    try:
        levels = _normalise_levels(manifest.get("fixed_delta_t_s"))
        if manifest.get("fixed_delta_t_s") != levels:
            blockers.append("TEMPORAL_LEVELS_INVALID")
    except TemporalSensitivityInputError as error:
        blockers.append(str(error).split(":", 1)[0])
    if manifest.get("controller") != {
            "adjust_time_step": False,
            "max_co": MAX_CO,
            "limiter_policy": "fixed_delta_t_no_controller_intervention.v1",
    }:
        blockers.append("TEMPORAL_CONTROLLER_INVALID")
    supplied_hash = manifest.get("manifest_sha256")
    if not isinstance(supplied_hash, str) or supplied_hash != _canonical_sha256(_without_manifest_hash(manifest)):
        blockers.append("TEMPORAL_MANIFEST_HASH_MISMATCH")
    children = manifest.get("children")
    if not isinstance(children, list) or len(children) != 3:
        blockers.append("TEMPORAL_CHILDREN_INVALID")
    else:
        for child, expected_name, expected_dt in zip(children, _CHILD_NAMES, FIXED_DELTA_T_S):
            if (not isinstance(child, dict)
                    or child.get("case_child") != expected_name
                    or child.get("delta_t_s") != expected_dt
                    or child.get("adjust_time_step") is not False):
                blockers.append("TEMPORAL_CHILD_INPUT_INVALID")
    anchor_ref = manifest.get("validation_anchor")
    if anchor_ref is not None:
        if not isinstance(anchor_ref, dict):
            blockers.append("TEMPORAL_VALIDATION_ANCHOR_INVALID")
        else:
            try:
                current = cfd_validation_anchor.anchor_reference(
                    anchor_ref.get("path"),
                    expected_case=manifest.get("anchor_fine_case"),
                    expected_role="temporal_fine",
                )
                if current != anchor_ref:
                    blockers.append("TEMPORAL_VALIDATION_ANCHOR_CHANGED")
            except (OSError, cfd_validation_anchor.ValidationAnchorError):
                blockers.append("TEMPORAL_VALIDATION_ANCHOR_INVALID")
    selector = manifest.get("selector")
    if selector is not None:
        try:
            supplied_selector_sha = selector.get("selector_sha256")
            normalised = sensitivity_job.require_confirmed_occupied_volume_band({
                key: value for key, value in selector.items()
                if key != "selector_sha256"
            })
            if (supplied_selector_sha != normalised.get("selector_sha256")
                    or selector != normalised):
                blockers.append("TEMPORAL_SELECTOR_HASH_MISMATCH")
        except (AttributeError, sensitivity_job.NumericalSensitivityJobInputError):
            blockers.append("TEMPORAL_CONFIRMED_SELECTOR_INVALID")
    return {"valid": not blockers, "contract": CONTRACT, "status": PENDING_STATUS,
            "blockers": list(dict.fromkeys(blockers))}


def create_temporal_study(case_seed: Path, fixed_delta_t: list[float],
                          anchor_fine_case: Path | None = None,
                          validation_anchor_path: Path | None = None,
                          selector: dict | None = None) -> dict:
    seed = _validate_seed(case_seed)
    levels = _normalise_levels(fixed_delta_t)
    anchor = None
    if anchor_fine_case is not None:
        anchor = Path(anchor_fine_case).expanduser().resolve(strict=False)
        if not anchor.is_dir():
            _fail("TEMPORAL_ANCHOR_CASE_INVALID", str(anchor))
    anchor_reference = None
    if validation_anchor_path is not None:
        if anchor is None:
            _fail("TEMPORAL_ANCHOR_CASE_REQUIRED")
        try:
            anchor_reference = cfd_validation_anchor.anchor_reference(
                validation_anchor_path,
                expected_case=anchor,
                expected_role="temporal_fine",
            )
        except (OSError, cfd_validation_anchor.ValidationAnchorError) as exc:
            _fail("TEMPORAL_VALIDATION_ANCHOR_INVALID", str(exc))
    normalised_selector = None
    if selector is not None:
        try:
            normalised_selector = sensitivity_job.require_confirmed_occupied_volume_band(
                selector
            )
        except sensitivity_job.NumericalSensitivityJobInputError as exc:
            _fail("TEMPORAL_CONFIRMED_SELECTOR_INVALID", str(exc))
    seed_hash = _tree_sha256(seed)
    children = [
        {
            "case_child": name,
            "delta_t_s": delta,
            "adjust_time_step": False,
            "case_seed_tree_sha256": seed_hash,
            "solver_evidence_status": PENDING_STATUS,
        }
        for name, delta in zip(_CHILD_NAMES, levels)
    ]
    manifest = {
        "contract": CONTRACT,
        "status": PENDING_STATUS,
        "seed_case_path": str(seed),
        "seed_tree_sha256": seed_hash,
        "anchor_fine_case": str(anchor) if anchor is not None else None,
        "fixed_delta_t_s": levels,
        "controller": {
            "adjust_time_step": False,
            "max_co": MAX_CO,
            "limiter_policy": "fixed_delta_t_no_controller_intervention.v1",
        },
        "children": children,
        "allowed_variation": {
            "parameter": "delta_t_s",
            "baseline": 0.04,
            "medium": 0.02,
            "fine": 0.01,
            "all_other_inputs_equal": True,
        },
        "evidence_requirements": {
            "minimum_flow_through_time_s": 3.0,
            "common_final_window_s": 0.1,
            "peak_co_limit": MAX_CO,
            "requires_time_history": True,
            "requires_qoi_recomputation": True,
        },
    }
    if anchor_reference is not None:
        manifest["validation_anchor"] = anchor_reference
    if normalised_selector is not None:
        manifest["selector"] = normalised_selector
    manifest["manifest_sha256"] = _canonical_sha256(manifest)
    return manifest

