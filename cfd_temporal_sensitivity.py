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
import stat


CONTRACT = "temporal_sensitivity.v1"
PENDING_STATUS = "PENDING_SOLVER_EVIDENCE"
FIXED_DELTA_T_S = (0.04, 0.02, 0.01)
MAX_CO = 1.0
_CHILD_NAMES = ("coarse_dt_0p04", "medium_dt_0p02", "fine_dt_0p01")
_FORBIDDEN_NAMES = {"run_manifest.json", "result_manifest.json", "thermal_progress.json"}


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
    return {"valid": not blockers, "contract": CONTRACT, "status": PENDING_STATUS,
            "blockers": list(dict.fromkeys(blockers))}


def create_temporal_study(case_seed: Path, fixed_delta_t: list[float],
                          anchor_fine_case: Path | None = None) -> dict:
    seed = _validate_seed(case_seed)
    levels = _normalise_levels(fixed_delta_t)
    anchor = None
    if anchor_fine_case is not None:
        anchor = Path(anchor_fine_case).expanduser().resolve(strict=False)
        if not anchor.is_dir():
            _fail("TEMPORAL_ANCHOR_CASE_INVALID", str(anchor))
        _validate_seed(anchor)
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
    manifest["manifest_sha256"] = _canonical_sha256(manifest)
    return manifest

