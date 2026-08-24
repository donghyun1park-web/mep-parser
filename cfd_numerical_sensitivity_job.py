"""Pure, serial-only foundations for a frozen numerical-sensitivity study.

This module deliberately does *not* start OpenFOAM.  It defines the immutable
pre-run input and occupied-volume contracts a later runner must satisfy before
it can publish a final ``numerical_sensitivity.v1`` result.  A frozen pair
contains only a physical tree and per-side case-seed inputs; run manifests,
solver logs, and result snapshots are post-run evidence and therefore cannot
appear in ``FROZEN_INPUTS``.  In particular, a JSON file written into one case
directory cannot promote itself to a PASS: a caller must supply the
independently retained frozen pair manifest, and solver evidence remains
pending outside this foundation.  A deterministic job ID makes a deliberate
full rewrite a different job; it is not a signature or a substitute for an
immutable external evidence store.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from numbers import Real

import cfd_numerics


OCCUPIED_VOLUME_BAND_CONTRACT = "occupied_volume_band.v1"
OCCUPIED_VOLUME_QOI_CONTRACT = "occupied_volume_qoi.v1"
FROZEN_PAIR_MANIFEST_CONTRACT = "numerical_sensitivity_pair_manifest.v1"
JOB_MANIFEST_CONTRACT = "cfd_numerical_sensitivity_job.v1"
FINAL_RESULT_CONTRACT = cfd_numerics.SENSITIVITY_CONTRACT
OCCUPIED_AGGREGATION = "volume_weighted_cell_centers.v1"
OCCUPIED_SCOPE = "selected_occupied_volume_band"
QOI_PLAN_CONTRACT = "numerical_sensitivity_qoi_plan.v1"
_PENDING_STATUS = "PENDING_SOLVER_EVIDENCE"
_SHA256_LENGTH = 64
_CASE_CHILD_BY_ROLE = {
    "BASELINE": "baseline_first_order",
    "VARIANT": "variant_second_order",
}
_REQUIRED_PHYSICAL_TREE_PATHS = frozenset({
    "0/U",
    "0/T",
    "0/k",
    "0/omega",
    # ``p`` is the initial pressure source used to initialise ``p_rgh`` for
    # a buoyant case; hashing only p_rgh leaves that physical initial state
    # outside the frozen input contract.
    "0/p",
    "0/p_rgh",
    "0/nut",
    "0/alphat",
    "constant/transportProperties",
    "constant/g",
    # build_buoyant_case selects kOmegaSST here, so it is a physical/numerical
    # model input rather than an optional presentation artifact.
    "constant/turbulenceProperties",
    "constant/fvOptions",
    "constant/polyMesh",
    # build_buoyant_case derives terminal/wall/heat bindings from these source
    # manifests and retains mesh provenance in thermal_input.json.
    "mesh_manifest.json",
    "surface_manifest.json",
    # This is a profile-free snapshot of the thermal input.  The current
    # thermal_input.json also declares the numerical profile, so it cannot be
    # the shared physical artifact for a first/second-order comparison.
    "thermal_input.physical.v1.json",
})


def _allowed_numerical_variation():
    """Return the sole input distinction that a frozen pair may declare."""
    return {
        "parameter": "thermal_numerics_profile",
        "baseline": cfd_numerics.STABILIZED_FIRST_ORDER,
        "variant": cfd_numerics.DESIGN_LIMITED_SECOND_ORDER,
        "all_other_inputs_equal": True,
    }


class NumericalSensitivityJobInputError(ValueError):
    """Raised when a sensitivity input would make the comparison ambiguous."""


def _error(code):
    raise NumericalSensitivityJobInputError(code)


def _error_code(error):
    return str(error).split(":", 1)[0]


def _finite_number(value, code, *, positive=False, nonnegative=False):
    if (not isinstance(value, Real) or isinstance(value, bool)
            or not math.isfinite(value)):
        _error(code)
    number = float(value)
    if positive and number <= 0:
        _error(code)
    if nonnegative and number < 0:
        _error(code)
    return number


def _nonempty_text(value, code):
    if not isinstance(value, str) or not value.strip():
        _error(code)
    return value.strip()


def _sha256(value, code):
    value = _nonempty_text(value, code).lower()
    if len(value) != _SHA256_LENGTH or any(char not in "0123456789abcdef" for char in value):
        _error(code)
    return value


def canonical_sha256(value):
    """Hash JSON by a deterministic encoding; NaN and object hooks are rejected."""
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        _error("CANONICAL_HASH_INPUT_INVALID")
    return hashlib.sha256(encoded).hexdigest()


def _unexpected_keys(value, allowed, code):
    if not isinstance(value, dict):
        _error(code)
    if set(value) - set(allowed):
        _error(code)


def _normalise_xy_bounds(raw):
    if raw is None:
        return None
    allowed = {"x_min_m", "x_max_m", "y_min_m", "y_max_m"}
    _unexpected_keys(raw, allowed, "OCCUPIED_SELECTOR_XY_BOUNDS_INVALID")
    if set(raw) != allowed:
        _error("OCCUPIED_SELECTOR_XY_BOUNDS_INVALID")
    bounds = {
        name: _finite_number(raw.get(name), "OCCUPIED_SELECTOR_XY_BOUNDS_INVALID")
        for name in sorted(allowed)
    }
    if bounds["x_min_m"] >= bounds["x_max_m"] or bounds["y_min_m"] >= bounds["y_max_m"]:
        _error("OCCUPIED_SELECTOR_XY_BOUNDS_INVALID")
    return bounds


def normalize_occupied_volume_band(selector):
    """Validate an explicit AGL selector and attach a canonical selector hash.

    There is deliberately no implicit whole-room or 1.1 m default.  The caller
    must state both the AGL coordinate source and the vertical band.
    """
    if selector is None:
        _error("OCCUPIED_SELECTOR_MISSING")
    allowed = {
        "contract",
        "coordinate_source",
        "z_min_agl_m",
        "z_max_agl_m",
        "xy_bounds_m",
    }
    _unexpected_keys(selector, allowed, "OCCUPIED_SELECTOR_UNSUPPORTED_FIELD")
    if selector.get("contract") != OCCUPIED_VOLUME_BAND_CONTRACT:
        _error("OCCUPIED_SELECTOR_CONTRACT_INVALID")
    if selector.get("coordinate_source") != "cell_center_m_agl":
        _error("OCCUPIED_SELECTOR_COORDINATE_SOURCE_INVALID")
    z_min = _finite_number(
        selector.get("z_min_agl_m"), "OCCUPIED_SELECTOR_Z_BAND_INVALID", nonnegative=True
    )
    z_max = _finite_number(
        selector.get("z_max_agl_m"), "OCCUPIED_SELECTOR_Z_BAND_INVALID", nonnegative=True
    )
    if z_min >= z_max:
        _error("OCCUPIED_SELECTOR_Z_BAND_INVALID")

    normalised = {
        "contract": OCCUPIED_VOLUME_BAND_CONTRACT,
        "coordinate_source": "cell_center_m_agl",
        "z_min_agl_m": z_min,
        "z_max_agl_m": z_max,
    }
    xy_bounds = _normalise_xy_bounds(selector.get("xy_bounds_m"))
    if xy_bounds is not None:
        normalised["xy_bounds_m"] = xy_bounds
    normalised["selector_sha256"] = canonical_sha256(normalised)
    return normalised


def validate_occupied_volume_band(selector):
    """Return a fail-closed validation result for a proposed selector."""
    try:
        normalised = normalize_occupied_volume_band(selector)
    except NumericalSensitivityJobInputError as error:
        return {
            "contract": OCCUPIED_VOLUME_BAND_CONTRACT,
            "valid": False,
            "blockers": [_error_code(error)],
        }
    return {
        "contract": OCCUPIED_VOLUME_BAND_CONTRACT,
        "valid": True,
        "blockers": [],
        "selector": normalised,
    }


def _validate_stored_selector(selector):
    if not isinstance(selector, dict):
        _error("FROZEN_PAIR_SELECTOR_INVALID")
    supplied_hash = selector.get("selector_sha256")
    raw = dict(selector)
    raw.pop("selector_sha256", None)
    try:
        normalised = normalize_occupied_volume_band(raw)
    except NumericalSensitivityJobInputError:
        _error("FROZEN_PAIR_SELECTOR_INVALID")
    if _sha256(supplied_hash, "FROZEN_PAIR_SELECTOR_HASH_INVALID") != normalised["selector_sha256"]:
        _error("FROZEN_PAIR_SELECTOR_HASH_MISMATCH")
    return normalised


def _normalise_cell(cell):
    if not isinstance(cell, dict):
        _error("OCCUPIED_CELL_INVALID")
    center = cell.get("center_m")
    if not isinstance(center, (list, tuple)) or len(center) != 3:
        _error("OCCUPIED_CELL_CENTER_INVALID")
    x, y, z = (
        _finite_number(value, "OCCUPIED_CELL_CENTER_INVALID")
        for value in center
    )
    return {
        "center_m": (x, y, z),
        "volume_m3": _finite_number(
            cell.get("volume_m3"), "OCCUPIED_CELL_VOLUME_INVALID", positive=True
        ),
        "temperature_k": _finite_number(
            cell.get("temperature_k"), "OCCUPIED_CELL_TEMPERATURE_INVALID"
        ),
        "velocity_m_s": _finite_number(
            cell.get("velocity_m_s"), "OCCUPIED_CELL_SPEED_INVALID", nonnegative=True
        ),
    }


def _cell_is_selected(cell, selector):
    x, y, z = cell["center_m"]
    if not selector["z_min_agl_m"] <= z <= selector["z_max_agl_m"]:
        return False
    xy = selector.get("xy_bounds_m")
    if xy is None:
        return True
    return (
        xy["x_min_m"] <= x <= xy["x_max_m"]
        and xy["y_min_m"] <= y <= xy["y_max_m"]
    )


def compute_occupied_volume_qois(cells, selector):
    """Compute only a selected-band, volume-weighted occupied QOI pair.

    Every supplied cell is validated before selection, so invalid data outside a
    band cannot silently influence a result when that band is changed later.
    """
    normalised_selector = normalize_occupied_volume_band(selector)
    if not isinstance(cells, (list, tuple)) or not cells:
        _error("OCCUPIED_CELLS_MISSING")
    normalised_cells = [_normalise_cell(cell) for cell in cells]
    selected = [
        cell for cell in normalised_cells
        if _cell_is_selected(cell, normalised_selector)
    ]
    if not selected:
        _error("OCCUPIED_SELECTED_CELLS_EMPTY")
    volume = sum(cell["volume_m3"] for cell in selected)
    if not math.isfinite(volume) or volume <= 0:
        _error("OCCUPIED_SELECTED_VOLUME_INVALID")
    mean_temperature = sum(
        cell["volume_m3"] * cell["temperature_k"] for cell in selected
    ) / volume
    mean_speed = sum(
        cell["volume_m3"] * cell["velocity_m_s"] for cell in selected
    ) / volume
    return {
        "contract": OCCUPIED_VOLUME_QOI_CONTRACT,
        "scope": OCCUPIED_SCOPE,
        "aggregation": OCCUPIED_AGGREGATION,
        "selector_sha256": normalised_selector["selector_sha256"],
        "selected_cell_count": len(selected),
        "selected_volume_m3": volume,
        "occupied_zone_mean_temperature_k": mean_temperature,
        "occupied_zone_mean_speed_m_s": mean_speed,
    }


def _normalise_case_seed_snapshot(value, role):
    """Validate the immutable pre-run snapshot of one seeded case.

    Resolving this hash to an actual cloned case is runner work.  A run/result
    manifest is later, external post-run evidence and must never be frozen
    into a ``FROZEN_INPUTS`` pair.
    """
    return _sha256(value, f"FROZEN_PAIR_{role}_CASE_SEED_SNAPSHOT_INVALID")


def _safe_relative_path(value, code):
    path = _nonempty_text(value, code)
    if (path != value or "\\" in path or path.startswith("/")
            or path.endswith("/") or "//" in path or ":" in path):
        _error(code)
    parts = path.split("/")
    if (any(part in {"", ".", ".."} for part in parts)
            or parts[0].lower().startswith("processor")):
        _error(code)
    return path


def _normalise_physical_tree_entries(entries):
    if not isinstance(entries, list) or not entries:
        _error("FROZEN_PAIR_PHYSICAL_TREE_INVALID")
    normalised = []
    paths = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "immutable"}:
            _error("FROZEN_PAIR_PHYSICAL_TREE_INVALID")
        path = _safe_relative_path(entry.get("path"), "FROZEN_PAIR_PHYSICAL_TREE_PATH_INVALID")
        if path in paths or entry.get("immutable") is not True:
            _error("FROZEN_PAIR_PHYSICAL_TREE_INVALID")
        paths.add(path)
        normalised.append({
            "path": path,
            "sha256": _sha256(entry.get("sha256"), "FROZEN_PAIR_PHYSICAL_TREE_INVALID"),
            "immutable": True,
        })
    if not _REQUIRED_PHYSICAL_TREE_PATHS.issubset(paths):
        _error("FROZEN_PAIR_PHYSICAL_TREE_REQUIRED_PATH_MISSING")
    return sorted(normalised, key=lambda entry: entry["path"])


def create_physical_tree_snapshot(entries):
    """Freeze the non-numerical case tree used by both serial runs.

    It intentionally covers the 0/ fields and physical constant/polyMesh
    records, while excluding the numerical ``system/`` dictionaries because
    the numerical profile is the one permitted variation between pair sides.
    """
    normalised = _normalise_physical_tree_entries(entries)
    snapshot = {"entries": normalised}
    return {
        "tree_sha256": canonical_sha256(snapshot),
        "entries": normalised,
    }


def _normalise_physical_tree(raw):
    if not isinstance(raw, dict) or set(raw) != {"tree_sha256", "entries"}:
        _error("FROZEN_PAIR_PHYSICAL_TREE_INVALID")
    entries = _normalise_physical_tree_entries(raw.get("entries"))
    tree_sha256 = _sha256(raw.get("tree_sha256"), "FROZEN_PAIR_PHYSICAL_TREE_HASH_INVALID")
    if tree_sha256 != canonical_sha256({"entries": entries}):
        _error("FROZEN_PAIR_PHYSICAL_TREE_HASH_MISMATCH")
    return {"tree_sha256": tree_sha256, "entries": entries}


def _bound_physical_input_sha256(mesh_sha256, physical_tree, selector):
    """Derive, rather than trust, the physical input identity of a pair.

    The profile-free thermal snapshot carries terminal/heat/thermal-condition
    inputs without allowing the differing numerical profile to contaminate the
    shared physical identity.  The calling runner must later resolve each
    frozen entry to its actual file; this pure foundation makes a manifest
    internally tamper-evident in the meantime.
    """
    thermal_input_sha256 = next(
        (entry["sha256"] for entry in physical_tree["entries"]
         if entry["path"] == "thermal_input.physical.v1.json"),
        None,
    )
    if thermal_input_sha256 is None:
        _error("FROZEN_PAIR_THERMAL_PHYSICAL_INPUT_MISSING")
    return canonical_sha256({
        "mesh_sha256": mesh_sha256,
        "physical_tree_sha256": physical_tree["tree_sha256"],
        "selector_sha256": selector["selector_sha256"],
        "thermal_input_sha256": thermal_input_sha256,
    })


def derive_physical_input_sha256(*, mesh_sha256, physical_tree, selector):
    """Return the public shared-physics fingerprint for one frozen pair.

    A preparation runner needs this same derivation before it can build a
    manifest.  Keeping it public avoids reimplementing or reaching into the
    foundation's private binding helper, while retaining the strict mesh/tree/
    selector validation used by final pair creation.
    """
    mesh_sha256 = _sha256(mesh_sha256, "FROZEN_PAIR_MESH_HASH_INVALID")
    physical_tree = _normalise_physical_tree(physical_tree)
    if isinstance(selector, dict) and "selector_sha256" in selector:
        selector = _validate_stored_selector(selector)
    else:
        selector = normalize_occupied_volume_band(selector)
    return _bound_physical_input_sha256(mesh_sha256, physical_tree, selector)


def _bound_pair_input_sha256(*, physical_input_sha256,
                             baseline_case_seed_snapshot_sha256,
                             variant_case_seed_snapshot_sha256):
    """Bind a physical pair to its two role-specific seeded child inputs.

    ``physical_input_sha256`` covers every input intentionally shared between
    the child cases.  The two seed snapshots then bind the profile-specific
    ``system/`` setup that is the sole declared distinction.  A later runner
    still has to resolve the snapshots and verify that declaration against
    actual files; this pure input contract cannot promote a result to PASS.
    """
    physical_input_sha256 = _sha256(
        physical_input_sha256, "FROZEN_PAIR_PHYSICAL_INPUT_HASH_INVALID"
    )
    baseline_seed = _normalise_case_seed_snapshot(
        baseline_case_seed_snapshot_sha256, "BASELINE"
    )
    variant_seed = _normalise_case_seed_snapshot(
        variant_case_seed_snapshot_sha256, "VARIANT"
    )
    return canonical_sha256({
        "physical_input_sha256": physical_input_sha256,
        "allowed_variation": _allowed_numerical_variation(),
        "baseline": {
            "case_child": _CASE_CHILD_BY_ROLE["BASELINE"],
            "profile": cfd_numerics.STABILIZED_FIRST_ORDER,
            "case_seed_snapshot_sha256": baseline_seed,
        },
        "variant": {
            "case_child": _CASE_CHILD_BY_ROLE["VARIANT"],
            "profile": cfd_numerics.DESIGN_LIMITED_SECOND_ORDER,
            "case_seed_snapshot_sha256": variant_seed,
        },
    })


def derive_frozen_pair_input_sha256(*, mesh_sha256, physical_tree, selector,
                                    baseline_case_seed_snapshot_sha256,
                                    variant_case_seed_snapshot_sha256):
    """Return the deterministic fingerprint of all pre-run pair inputs."""
    mesh_sha256 = _sha256(mesh_sha256, "FROZEN_PAIR_MESH_HASH_INVALID")
    physical_tree = _normalise_physical_tree(physical_tree)
    if isinstance(selector, dict) and "selector_sha256" in selector:
        selector = _validate_stored_selector(selector)
    else:
        selector = normalize_occupied_volume_band(selector)
    physical_input_sha256 = _bound_physical_input_sha256(
        mesh_sha256, physical_tree, selector
    )
    return _bound_pair_input_sha256(
        physical_input_sha256=physical_input_sha256,
        baseline_case_seed_snapshot_sha256=baseline_case_seed_snapshot_sha256,
        variant_case_seed_snapshot_sha256=variant_case_seed_snapshot_sha256,
    )


def derive_frozen_pair_job_id(*, mesh_sha256, physical_tree, selector,
                              baseline_case_seed_snapshot_sha256,
                              variant_case_seed_snapshot_sha256):
    """Return the deterministic identity for one frozen serial input pair.

    A deliberate full rewrite of shared physical inputs *or either seeded
    child* obtains a different job ID.  This detects attempts to keep an old
    identity after an input rewrite, but is not signed tamper proof for a
    mutable filesystem.
    """
    pair_input_sha256 = derive_frozen_pair_input_sha256(
        mesh_sha256=mesh_sha256,
        physical_tree=physical_tree,
        selector=selector,
        baseline_case_seed_snapshot_sha256=baseline_case_seed_snapshot_sha256,
        variant_case_seed_snapshot_sha256=variant_case_seed_snapshot_sha256,
    )
    return f"sens-{pair_input_sha256[:24]}"


def _safe_case_child(value, role):
    child = _nonempty_text(value, f"FROZEN_PAIR_{role}_CASE_CHILD_INVALID")
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    if (child in {".", ".."} or any(char not in allowed for char in child)
            or child.lower().startswith("processor")):
        _error(f"FROZEN_PAIR_{role}_CASE_CHILD_INVALID")
    return child


_POST_RUN_SIDE_FIELDS = frozenset({
    "artifacts",
    "run_manifest",
    "result_snapshot",
    "run_hash",
    "result_hash",
    "solver_evidence",
})


def _side_input_snapshot_payload(side):
    """Return only pre-run data bound by a side input snapshot hash."""
    return {
        "run_id": side["run_id"],
        "profile": side["profile"],
        "case_child": side["case_child"],
        "processor_directories_present": side["processor_directories_present"],
        "mesh_sha256": side["mesh_sha256"],
        "physical_input_sha256": side["physical_input_sha256"],
        "case_seed_snapshot_sha256": side["case_seed_snapshot_sha256"],
    }


def without_input_snapshot_hash(side):
    """Copy one frozen side without its derived pre-run input hash."""
    copied = copy.deepcopy(side)
    if isinstance(copied, dict):
        copied.pop("input_snapshot_sha256", None)
    return copied


def _normalise_side(raw, role, profile, mesh_sha256, physical_input_sha256):
    if isinstance(raw, dict) and set(raw) & _POST_RUN_SIDE_FIELDS:
        _error(f"FROZEN_PAIR_{role}_POST_RUN_ARTIFACTS_FORBIDDEN")
    allowed = {
        "run_id",
        "profile",
        "case_child",
        "processor_directories_present",
        "case_seed_snapshot_sha256",
    }
    _unexpected_keys(raw, allowed, f"FROZEN_PAIR_{role}_INVALID")
    if "case_seed_snapshot_sha256" not in raw:
        _error(f"FROZEN_PAIR_{role}_CASE_SEED_SNAPSHOT_MISSING")
    if raw.get("profile") != profile:
        _error(f"FROZEN_PAIR_{role}_PROFILE_INVALID")
    if raw.get("processor_directories_present") is not False:
        _error(f"FROZEN_PAIR_{role}_PROCESSOR_DIRS_PRESENT")
    case_child = _safe_case_child(raw.get("case_child"), role)
    if case_child != _CASE_CHILD_BY_ROLE[role]:
        _error(f"FROZEN_PAIR_{role}_CASE_CHILD_MISMATCH")
    side = {
        "run_id": _nonempty_text(raw.get("run_id"), f"FROZEN_PAIR_{role}_RUN_ID_INVALID"),
        "profile": profile,
        "case_child": case_child,
        "processor_directories_present": False,
        "mesh_sha256": mesh_sha256,
        "physical_input_sha256": physical_input_sha256,
        "case_seed_snapshot_sha256": _normalise_case_seed_snapshot(
            raw.get("case_seed_snapshot_sha256"), role
        ),
    }
    side["input_snapshot_sha256"] = canonical_sha256(
        _side_input_snapshot_payload(side)
    )
    return side


def without_manifest_hash(manifest):
    """Copy a manifest without its self-hash for deterministic verification."""
    copied = copy.deepcopy(manifest)
    if isinstance(copied, dict):
        copied.pop("manifest_sha256", None)
    return copied


def create_frozen_pair_manifest(*, job_id, selector, mesh_sha256,
                                physical_input_sha256, physical_tree,
                                baseline, variant,
                                requested_ranks):
    """Freeze serial pair inputs; this creates evidence, not a solver PASS."""
    if (not isinstance(requested_ranks, int) or isinstance(requested_ranks, bool)
            or requested_ranks != 1):
        _error("NUMERICAL_SENSITIVITY_SERIAL_REQUIRED")
    job_id = _nonempty_text(job_id, "FROZEN_PAIR_JOB_ID_INVALID")
    mesh_sha256 = _sha256(mesh_sha256, "FROZEN_PAIR_MESH_HASH_INVALID")
    claimed_physical_input_sha256 = _sha256(
        physical_input_sha256, "FROZEN_PAIR_PHYSICAL_INPUT_HASH_INVALID"
    )
    physical_tree = _normalise_physical_tree(physical_tree)
    selector = normalize_occupied_volume_band(selector)
    bound_physical_input_sha256 = _bound_physical_input_sha256(
        mesh_sha256, physical_tree, selector
    )
    normalised_baseline = _normalise_side(
        baseline,
        "BASELINE",
        cfd_numerics.STABILIZED_FIRST_ORDER,
        mesh_sha256,
        claimed_physical_input_sha256,
    )
    normalised_variant = _normalise_side(
        variant,
        "VARIANT",
        cfd_numerics.DESIGN_LIMITED_SECOND_ORDER,
        mesh_sha256,
        claimed_physical_input_sha256,
    )
    pair_input_sha256 = _bound_pair_input_sha256(
        physical_input_sha256=claimed_physical_input_sha256,
        baseline_case_seed_snapshot_sha256=(
            normalised_baseline["case_seed_snapshot_sha256"]
        ),
        variant_case_seed_snapshot_sha256=(
            normalised_variant["case_seed_snapshot_sha256"]
        ),
    )
    expected_job_id = derive_frozen_pair_job_id(
        mesh_sha256=mesh_sha256,
        physical_tree=physical_tree,
        selector=selector,
        baseline_case_seed_snapshot_sha256=(
            normalised_baseline["case_seed_snapshot_sha256"]
        ),
        variant_case_seed_snapshot_sha256=(
            normalised_variant["case_seed_snapshot_sha256"]
        ),
    )
    manifest = {
        "contract": FROZEN_PAIR_MANIFEST_CONTRACT,
        "status": "FROZEN_INPUTS",
        "job_id": job_id,
        "pair_input_sha256": pair_input_sha256,
        "serial_required": True,
        "requested_ranks": 1,
        "selector": selector,
        "shared_input": {
            "mesh_sha256": mesh_sha256,
            "physical_input_sha256": claimed_physical_input_sha256,
            "physical_tree": physical_tree,
        },
        "baseline": normalised_baseline,
        "variant": normalised_variant,
    }
    if manifest["baseline"]["run_id"].casefold() == manifest["variant"]["run_id"].casefold():
        _error("FROZEN_PAIR_RUN_IDS_NOT_DISTINCT")
    if manifest["baseline"]["case_child"].casefold() == manifest["variant"]["case_child"].casefold():
        _error("FROZEN_PAIR_CASE_CHILDREN_NOT_DISTINCT")
    if claimed_physical_input_sha256 != bound_physical_input_sha256:
        _error("FROZEN_PAIR_PHYSICAL_INPUT_BINDING_MISMATCH")
    if job_id != expected_job_id:
        _error("FROZEN_PAIR_JOB_ID_MISMATCH")
    manifest["manifest_sha256"] = canonical_sha256(manifest)
    return manifest


def _validate_manifest_side(raw, role, profile):
    if not isinstance(raw, dict):
        _error(f"FROZEN_PAIR_{role}_INVALID")
    if set(raw) & _POST_RUN_SIDE_FIELDS:
        _error(f"FROZEN_PAIR_{role}_POST_RUN_ARTIFACTS_FORBIDDEN")
    if "case_seed_snapshot_sha256" not in raw:
        _error(f"FROZEN_PAIR_{role}_CASE_SEED_SNAPSHOT_MISSING")
    required = {
        "run_id", "profile", "case_child", "processor_directories_present",
        "mesh_sha256", "physical_input_sha256", "case_seed_snapshot_sha256",
        "input_snapshot_sha256",
    }
    if set(raw) != required:
        _error(f"FROZEN_PAIR_{role}_INVALID")
    case_child = _safe_case_child(raw.get("case_child"), role)
    if case_child != _CASE_CHILD_BY_ROLE[role]:
        _error(f"FROZEN_PAIR_{role}_CASE_CHILD_MISMATCH")
    side = {
        "run_id": _nonempty_text(raw.get("run_id"), f"FROZEN_PAIR_{role}_RUN_ID_INVALID"),
        "profile": raw.get("profile"),
        "case_child": case_child,
        "processor_directories_present": raw.get("processor_directories_present"),
        "mesh_sha256": _sha256(raw.get("mesh_sha256"), f"FROZEN_PAIR_{role}_MESH_HASH_INVALID"),
        "physical_input_sha256": _sha256(
            raw.get("physical_input_sha256"),
            f"FROZEN_PAIR_{role}_PHYSICAL_INPUT_HASH_INVALID",
        ),
        "case_seed_snapshot_sha256": _normalise_case_seed_snapshot(
            raw.get("case_seed_snapshot_sha256"), role
        ),
        "input_snapshot_sha256": _sha256(
            raw.get("input_snapshot_sha256"),
            f"FROZEN_PAIR_{role}_INPUT_SNAPSHOT_HASH_INVALID",
        ),
    }
    if side["profile"] != profile:
        _error(f"FROZEN_PAIR_{role}_PROFILE_INVALID")
    if side["processor_directories_present"] is not False:
        _error(f"FROZEN_PAIR_{role}_PROCESSOR_DIRS_PRESENT")
    if side["input_snapshot_sha256"] != canonical_sha256(_side_input_snapshot_payload(side)):
        _error(f"FROZEN_PAIR_{role}_INPUT_SNAPSHOT_HASH_MISMATCH")
    return side


def validate_frozen_pair_manifest(manifest):
    """Validate an immutable pair snapshot without treating it as a result."""
    blockers = []
    if not isinstance(manifest, dict):
        return {
            "contract": FROZEN_PAIR_MANIFEST_CONTRACT,
            "valid": False,
            "blockers": ["FROZEN_PAIR_MANIFEST_MISSING"],
        }
    required_manifest_fields = {
        "contract", "status", "job_id", "pair_input_sha256",
        "serial_required", "requested_ranks", "selector", "shared_input",
        "baseline", "variant", "manifest_sha256",
    }
    if set(manifest) != required_manifest_fields:
        blockers.append("FROZEN_PAIR_MANIFEST_FIELDS_INVALID")
    if manifest.get("contract") != FROZEN_PAIR_MANIFEST_CONTRACT:
        blockers.append("FROZEN_PAIR_CONTRACT_INVALID")
    if manifest.get("status") != "FROZEN_INPUTS":
        blockers.append("FROZEN_PAIR_STATUS_INVALID")
    if manifest.get("serial_required") is not True or manifest.get("requested_ranks") != 1:
        blockers.append("NUMERICAL_SENSITIVITY_SERIAL_REQUIRED")
    job_id = None
    try:
        job_id = _nonempty_text(manifest.get("job_id"), "FROZEN_PAIR_JOB_ID_INVALID")
    except NumericalSensitivityJobInputError as error:
        blockers.append(_error_code(error))
    pair_input_sha256 = None
    try:
        pair_input_sha256 = _sha256(
            manifest.get("pair_input_sha256"),
            "FROZEN_PAIR_PAIR_INPUT_HASH_INVALID",
        )
    except NumericalSensitivityJobInputError as error:
        blockers.append(_error_code(error))
    try:
        selector = _validate_stored_selector(manifest.get("selector"))
    except NumericalSensitivityJobInputError as error:
        blockers.append(_error_code(error))
        selector = None

    shared = manifest.get("shared_input")
    shared_mesh = shared_physical = physical_tree = None
    if (not isinstance(shared, dict)
            or set(shared) != {"mesh_sha256", "physical_input_sha256", "physical_tree"}):
        blockers.append("FROZEN_PAIR_SHARED_INPUT_INVALID")
    else:
        try:
            shared_mesh = _sha256(shared.get("mesh_sha256"), "FROZEN_PAIR_MESH_HASH_INVALID")
            shared_physical = _sha256(
                shared.get("physical_input_sha256"),
                "FROZEN_PAIR_PHYSICAL_INPUT_HASH_INVALID",
            )
            physical_tree = _normalise_physical_tree(shared.get("physical_tree"))
        except NumericalSensitivityJobInputError as error:
            blockers.append(_error_code(error))

    if (shared_mesh is not None and shared_physical is not None
            and physical_tree is not None and selector is not None):
        try:
            bound_physical_input_sha256 = _bound_physical_input_sha256(
                shared_mesh, physical_tree, selector
            )
            if shared_physical != bound_physical_input_sha256:
                blockers.append("FROZEN_PAIR_PHYSICAL_INPUT_BINDING_MISMATCH")
        except NumericalSensitivityJobInputError as error:
            blockers.append(_error_code(error))

    baseline = variant = None
    for name, profile in (
            ("baseline", cfd_numerics.STABILIZED_FIRST_ORDER),
            ("variant", cfd_numerics.DESIGN_LIMITED_SECOND_ORDER)):
        try:
            side = _validate_manifest_side(manifest.get(name), name.upper(), profile)
        except NumericalSensitivityJobInputError as error:
            blockers.append(_error_code(error))
            side = None
        if name == "baseline":
            baseline = side
        else:
            variant = side

    if baseline is not None and variant is not None:
        if baseline["run_id"].casefold() == variant["run_id"].casefold():
            blockers.append("FROZEN_PAIR_RUN_IDS_NOT_DISTINCT")
        if baseline["case_child"].casefold() == variant["case_child"].casefold():
            blockers.append("FROZEN_PAIR_CASE_CHILDREN_NOT_DISTINCT")
    for side in (baseline, variant):
        if side is None or shared_mesh is None or shared_physical is None:
            continue
        if side["mesh_sha256"] != shared_mesh:
            blockers.append("FROZEN_PAIR_MESH_HASH_MISMATCH")
        if side["physical_input_sha256"] != shared_physical:
            blockers.append("FROZEN_PAIR_PHYSICAL_INPUT_HASH_MISMATCH")

    if (shared_mesh is not None and shared_physical is not None
            and physical_tree is not None and selector is not None
            and baseline is not None and variant is not None):
        try:
            expected_pair_input_sha256 = _bound_pair_input_sha256(
                physical_input_sha256=shared_physical,
                baseline_case_seed_snapshot_sha256=(
                    baseline["case_seed_snapshot_sha256"]
                ),
                variant_case_seed_snapshot_sha256=(
                    variant["case_seed_snapshot_sha256"]
                ),
            )
            if (pair_input_sha256 is not None
                    and pair_input_sha256 != expected_pair_input_sha256):
                blockers.append("FROZEN_PAIR_PAIR_INPUT_HASH_MISMATCH")
            expected_job_id = derive_frozen_pair_job_id(
                mesh_sha256=shared_mesh,
                physical_tree=physical_tree,
                selector=selector,
                baseline_case_seed_snapshot_sha256=(
                    baseline["case_seed_snapshot_sha256"]
                ),
                variant_case_seed_snapshot_sha256=(
                    variant["case_seed_snapshot_sha256"]
                ),
            )
            if job_id is not None and job_id != expected_job_id:
                blockers.append("FROZEN_PAIR_JOB_ID_MISMATCH")
        except NumericalSensitivityJobInputError as error:
            blockers.append(_error_code(error))

    supplied_hash = manifest.get("manifest_sha256")
    try:
        supplied_hash = _sha256(supplied_hash, "FROZEN_PAIR_MANIFEST_HASH_INVALID")
        if supplied_hash != canonical_sha256(without_manifest_hash(manifest)):
            blockers.append("FROZEN_PAIR_MANIFEST_HASH_MISMATCH")
    except NumericalSensitivityJobInputError as error:
        blockers.append(_error_code(error))

    blockers = list(dict.fromkeys(blockers))
    result = {
        "contract": FROZEN_PAIR_MANIFEST_CONTRACT,
        "valid": not blockers,
        "blockers": blockers,
    }
    if not blockers and selector is not None and physical_tree is not None:
        result["manifest_sha256"] = supplied_hash
        result["selector"] = selector
    return result


def _normalise_qoi_limits(limits):
    if not isinstance(limits, dict) or set(limits) != set(cfd_numerics.REQUIRED_SENSITIVITY_QOIS):
        _error("NUMERICAL_SENSITIVITY_QOI_LIMITS_INVALID")
    return {
        name: _finite_number(
            limits[name], "NUMERICAL_SENSITIVITY_QOI_LIMITS_INVALID", positive=True
        )
        for name in cfd_numerics.REQUIRED_SENSITIVITY_QOIS
    }


def _qoi_plan(limits):
    """Define future comparison criteria without recording any result value."""
    return {
        "contract": QOI_PLAN_CONTRACT,
        "definitions": [
            {"name": name, "limit": limits[name]}
            for name in cfd_numerics.REQUIRED_SENSITIVITY_QOIS
        ],
    }


def _normalise_qoi_plan(plan):
    if not isinstance(plan, dict) or set(plan) != {"contract", "definitions"}:
        _error("NUMERICAL_SENSITIVITY_QOI_PLAN_INVALID")
    if plan.get("contract") != QOI_PLAN_CONTRACT:
        _error("NUMERICAL_SENSITIVITY_QOI_PLAN_INVALID")
    definitions = plan.get("definitions")
    if not isinstance(definitions, list):
        _error("NUMERICAL_SENSITIVITY_QOI_PLAN_INVALID")
    expected_names = set(cfd_numerics.REQUIRED_SENSITIVITY_QOIS)
    limits = {}
    for definition in definitions:
        if not isinstance(definition, dict) or set(definition) != {"name", "limit"}:
            _error("NUMERICAL_SENSITIVITY_QOI_PLAN_INVALID")
        name = definition.get("name")
        if name not in expected_names or name in limits:
            _error("NUMERICAL_SENSITIVITY_QOI_PLAN_INVALID")
        limits[name] = _finite_number(
            definition.get("limit"), "NUMERICAL_SENSITIVITY_QOI_PLAN_INVALID", positive=True
        )
    if set(limits) != expected_names:
        _error("NUMERICAL_SENSITIVITY_QOI_PLAN_INVALID")
    return _qoi_plan(limits)


def _job_side(side):
    """Expose only frozen inputs to the central PENDING job manifest.

    In particular this is not a final-result side: it deliberately has no
    ``run_hash``, solver evidence, run manifest, or result snapshot.  Those
    records can only be attached by a future post-run evidence verifier.
    """
    return {
        "run_id": side["run_id"],
        "profile": side["profile"],
        "case_child": side["case_child"],
        "case_seed_snapshot_sha256": side["case_seed_snapshot_sha256"],
        "input_snapshot_sha256": side["input_snapshot_sha256"],
        "mesh_sha256": side["mesh_sha256"],
        "physical_input_sha256": side["physical_input_sha256"],
    }


def without_job_manifest_hash(job_manifest):
    """Copy an external job manifest without its self-hash."""
    copied = copy.deepcopy(job_manifest)
    if isinstance(copied, dict):
        copied.pop("job_manifest_sha256", None)
    return copied


def build_cfd_numerical_sensitivity_job_manifest(frozen_pair_manifest, *,
                                                 qoi_limits):
    """Build an external central job manifest tied to one frozen serial pair.

    This is deliberately *not* a ``numerical_sensitivity.v1`` result.  It only
    records only the frozen pair, QOI definitions/limits, and eventual result
    target.  It deliberately cannot accept baseline/variant QOI values or
    comparison/pass results; the later central runner must create the existing
    final result contract after it has immutable post-run solver evidence.
    """
    pair_validation = validate_frozen_pair_manifest(frozen_pair_manifest)
    if not pair_validation["valid"]:
        _error("NUMERICAL_SENSITIVITY_FROZEN_PAIR_INVALID")
    limits = _normalise_qoi_limits(qoi_limits)
    job_manifest = {
        "contract": JOB_MANIFEST_CONTRACT,
        "status": _PENDING_STATUS,
        "authority": {
            "source": "cfd_numerical_sensitivity_job",
            "central_orchestrator_required": True,
            "case_local_artifact_authoritative": False,
        },
        "final_result_target": {
            "contract": FINAL_RESULT_CONTRACT,
            "status": _PENDING_STATUS,
            "requires_solver_evidence": True,
        },
        "pair_manifest_sha256": pair_validation["manifest_sha256"],
        "selector_sha256": pair_validation["selector"]["selector_sha256"],
        "aggregation": {
            "scope": OCCUPIED_SCOPE,
            "occupied_zone": OCCUPIED_AGGREGATION,
            "exhaust_temperature_rise_k": "explicit_solver_postprocess_qoi.v1",
        },
        "baseline": _job_side(frozen_pair_manifest["baseline"]),
        "variant": _job_side(frozen_pair_manifest["variant"]),
        "allowed_variation": _allowed_numerical_variation(),
        "qoi_plan": _qoi_plan(limits),
    }
    job_manifest["job_manifest_sha256"] = canonical_sha256(job_manifest)
    return job_manifest


def _job_manifest_structure_blockers(job_manifest, pair_manifest):
    blockers = []
    if not isinstance(job_manifest, dict):
        return ["NUMERICAL_SENSITIVITY_JOB_MANIFEST_MISSING"]
    allowed = {
        "contract", "status", "authority", "final_result_target", "pair_manifest_sha256",
        "selector_sha256", "aggregation", "baseline", "variant",
        "allowed_variation", "qoi_plan", "job_manifest_sha256",
    }
    if set(job_manifest) != allowed:
        blockers.append("NUMERICAL_SENSITIVITY_JOB_MANIFEST_FIELDS_INVALID")
    if job_manifest.get("contract") != JOB_MANIFEST_CONTRACT:
        blockers.append("NUMERICAL_SENSITIVITY_JOB_MANIFEST_CONTRACT_INVALID")
    if job_manifest.get("status") == "PASS":
        blockers.append("NUMERICAL_SENSITIVITY_INPUT_ONLY_NOT_PASSABLE")
    elif job_manifest.get("status") != _PENDING_STATUS:
        blockers.append("NUMERICAL_SENSITIVITY_JOB_MANIFEST_STATUS_INVALID")
    authority = job_manifest.get("authority")
    expected_authority = {
        "source": "cfd_numerical_sensitivity_job",
        "central_orchestrator_required": True,
        "case_local_artifact_authoritative": False,
    }
    if authority != expected_authority:
        blockers.append("NUMERICAL_SENSITIVITY_JOB_AUTHORITY_INVALID")
    final_result_target = job_manifest.get("final_result_target")
    if final_result_target != {
            "contract": FINAL_RESULT_CONTRACT,
            "status": _PENDING_STATUS,
            "requires_solver_evidence": True,
    }:
        blockers.append("NUMERICAL_SENSITIVITY_FINAL_RESULT_TARGET_INVALID")
    expected_manifest_hash = pair_manifest.get("manifest_sha256")
    try:
        if _sha256(
                job_manifest.get("pair_manifest_sha256"),
                "NUMERICAL_SENSITIVITY_PAIR_MANIFEST_HASH_INVALID",
        ) != expected_manifest_hash:
            blockers.append("NUMERICAL_SENSITIVITY_PAIR_MANIFEST_HASH_MISMATCH")
    except NumericalSensitivityJobInputError as error:
        blockers.append(_error_code(error))
    expected_selector_hash = pair_manifest.get("selector", {}).get("selector_sha256")
    try:
        if _sha256(
                job_manifest.get("selector_sha256"),
                "NUMERICAL_SENSITIVITY_SELECTOR_HASH_INVALID",
        ) != expected_selector_hash:
            blockers.append("NUMERICAL_SENSITIVITY_SELECTOR_HASH_MISMATCH")
    except NumericalSensitivityJobInputError as error:
        blockers.append(_error_code(error))

    aggregation = job_manifest.get("aggregation")
    expected_aggregation = {
        "scope": OCCUPIED_SCOPE,
        "occupied_zone": OCCUPIED_AGGREGATION,
        "exhaust_temperature_rise_k": "explicit_solver_postprocess_qoi.v1",
    }
    if aggregation != expected_aggregation:
        blockers.append("NUMERICAL_SENSITIVITY_QOI_AGGREGATION_INVALID")
    expected_allowed = _allowed_numerical_variation()
    if job_manifest.get("allowed_variation") != expected_allowed:
        blockers.append("NUMERICAL_SENSITIVITY_ALLOWED_VARIATION_INVALID")
    for name in ("baseline", "variant"):
        expected = _job_side(pair_manifest[name])
        if job_manifest.get(name) != expected:
            blockers.append(f"NUMERICAL_SENSITIVITY_{name.upper()}_SNAPSHOT_MISMATCH")

    try:
        _normalise_qoi_plan(job_manifest.get("qoi_plan"))
    except NumericalSensitivityJobInputError as error:
        blockers.append(_error_code(error))
    try:
        job_hash = _sha256(
            job_manifest.get("job_manifest_sha256"),
            "NUMERICAL_SENSITIVITY_JOB_MANIFEST_HASH_INVALID",
        )
        if job_hash != canonical_sha256(without_job_manifest_hash(job_manifest)):
            blockers.append("NUMERICAL_SENSITIVITY_JOB_MANIFEST_HASH_MISMATCH")
    except NumericalSensitivityJobInputError as error:
        blockers.append(_error_code(error))
    return blockers


def validate_cfd_numerical_sensitivity_job_manifest(job_manifest, *,
                                                    trusted_pair_manifest=None):
    """Fail closed for an external job manifest, even when structure is correct.

    ``trusted_pair_manifest`` must be loaded by the central job/orchestrator,
    not copied from an individual case directory.  This pure foundation never
    accepts case-local JSON as final solver evidence.
    """
    blockers = []
    if trusted_pair_manifest is None:
        blockers.append("NUMERICAL_SENSITIVITY_TRUSTED_PAIR_MANIFEST_REQUIRED")
        pair_manifest = None
    else:
        pair_validation = validate_frozen_pair_manifest(trusted_pair_manifest)
        if not pair_validation["valid"]:
            blockers.append("NUMERICAL_SENSITIVITY_TRUSTED_PAIR_MANIFEST_INVALID")
            pair_manifest = None
        else:
            pair_manifest = trusted_pair_manifest
    if pair_manifest is not None:
        blockers.extend(_job_manifest_structure_blockers(job_manifest, pair_manifest))
    elif isinstance(job_manifest, dict) and job_manifest.get("status") == "PASS":
        blockers.append("NUMERICAL_SENSITIVITY_INPUT_ONLY_NOT_PASSABLE")
    elif not isinstance(job_manifest, dict):
        blockers.append("NUMERICAL_SENSITIVITY_JOB_MANIFEST_MISSING")

    blockers = list(dict.fromkeys(blockers))
    structural_blockers = [
        blocker for blocker in blockers
        if blocker not in {"NUMERICAL_SENSITIVITY_INPUT_ONLY_NOT_PASSABLE"}
    ]
    # The foundation has no execution provenance or solver residuals, so even
    # a structurally correct candidate is not a PASS or design-ready result.
    blockers.append("NUMERICAL_SENSITIVITY_SOLVER_EVIDENCE_PENDING")
    return {
        "contract": JOB_MANIFEST_CONTRACT,
        "status": _PENDING_STATUS,
        "structurally_valid": not structural_blockers,
        "valid": False,
        "blockers": list(dict.fromkeys(blockers)),
    }
