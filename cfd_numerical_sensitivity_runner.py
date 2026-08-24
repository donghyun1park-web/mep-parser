"""Prepare, but never execute, a serial numerical-sensitivity case pair.

The paired first-/second-order calculation is an evidence-generating workflow,
not a convenience rerun of an existing case.  This module creates two fresh,
zero-flow children from the same PASS body-fitted mesh and freezes their
pre-run input state.  It deliberately does **not** import a solver runner,
WSL, OpenFOAM, or MPI.  A later execution module must revalidate the retained
physical tree and case-seed snapshots before it starts either child.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import threading

import cfd_numerical_sensitivity_job as sensitivity_job
import cfd_numerics
import cfd_physics


PREPARATION_CONTRACT = "serial_numerical_sensitivity_preparation.v1"
CASE_SEED_SNAPSHOT_CONTRACT = "case_seed_snapshot.v1"
_BASELINE_ROLE = "baseline"
_VARIANT_ROLE = "variant"
_CHILD_BY_ROLE = {
    _BASELINE_ROLE: "baseline_first_order",
    _VARIANT_ROLE: "variant_second_order",
}
_PROFILE_BY_ROLE = {
    _BASELINE_ROLE: cfd_numerics.STABILIZED_FIRST_ORDER,
    _VARIANT_ROLE: cfd_numerics.DESIGN_LIMITED_SECOND_ORDER,
}
_CASE_SEED_PATHS = (
    "Allrun",
    "thermal_input.json",
    "system/controlDict",
    "system/fvSchemes",
    "system/fvSolution",
    "system/controlDict.transient",
    "system/fvSchemes.transient",
    "system/fvSolution.transient",
    "system/controlDict.precondition",
    "system/fvSchemes.precondition",
    "system/fvSolution.precondition",
    "system/topoSetDict",
)
_POST_RUN_OR_RESTART_ARTIFACTS = (
    "thermal_restart_input.json",
    "run_manifest.json",
    "result_manifest.json",
    "thermal_progress.json",
)
_PARALLEL_COMMAND_PATTERN = re.compile(
    r"(?:\bmpirun\b|\bmpiexec\b|\bdecomposePar\b|\breconstructPar\b|(?<!\w)-parallel\b)",
    re.IGNORECASE,
)
_TARGET_LOCKS_GUARD = threading.Lock()
_TARGET_LOCKS = {}


class NumericalSensitivityPreparationError(ValueError):
    """Raised when a paired comparison cannot be frozen safely."""


def _fail(code, detail=None):
    message = str(code)
    if detail:
        message = f"{message}: {detail}"
    raise NumericalSensitivityPreparationError(message)


def _canonical_sha256(value):
    """Use the foundation's canonical encoding for every persisted contract."""
    try:
        return sensitivity_job.canonical_sha256(value)
    except sensitivity_job.NumericalSensitivityJobInputError as error:
        _fail("NUMERICAL_SENSITIVITY_CANONICAL_HASH_INVALID", str(error))


def _sha256_file(path):
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        _fail("NUMERICAL_SENSITIVITY_FILE_HASH_FAILED", str(error))
    return digest.hexdigest()


def _is_reparse_or_symlink(path):
    """Treat every link-like node as an unsafe indirection, including junctions."""
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())
    except OSError:
        return True


def _resolved_path(value, code):
    try:
        return Path(value).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, TypeError) as error:
        _fail(code, str(error))


def _is_within(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _relative_case_path(path):
    """Validate a contract path before resolving it below a case root."""
    try:
        pure = PurePosixPath(path)
    except TypeError:
        _fail("NUMERICAL_SENSITIVITY_CASE_PATH_INVALID")
    if (not isinstance(path, str) or not path or "\\" in path or pure.is_absolute()
            or any(part in {"", ".", ".."} for part in pure.parts)
            or any(":" in part for part in pure.parts)):
        _fail("NUMERICAL_SENSITIVITY_CASE_PATH_INVALID")
    return pure.parts


def _safe_case_path(case_root, relative_path, *, require_directory=None):
    """Resolve a regular path without accepting link/path-escape tricks."""
    root = Path(case_root)
    if _is_reparse_or_symlink(root) or not root.is_dir():
        _fail("NUMERICAL_SENSITIVITY_CASE_ROOT_INVALID")
    try:
        root_resolved = root.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        _fail("NUMERICAL_SENSITIVITY_CASE_ROOT_INVALID", str(error))

    candidate = root
    for part in _relative_case_path(relative_path):
        candidate = candidate / part
        if _is_reparse_or_symlink(candidate):
            _fail("NUMERICAL_SENSITIVITY_LINK_OR_PATH_ESCAPE", relative_path)
    if not candidate.exists():
        _fail("NUMERICAL_SENSITIVITY_REQUIRED_PATH_MISSING", relative_path)
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        _fail("NUMERICAL_SENSITIVITY_LINK_OR_PATH_ESCAPE", str(error))
    if not _is_within(resolved, root_resolved):
        _fail("NUMERICAL_SENSITIVITY_LINK_OR_PATH_ESCAPE", relative_path)
    try:
        mode = candidate.lstat().st_mode
    except OSError as error:
        _fail("NUMERICAL_SENSITIVITY_REQUIRED_PATH_MISSING", str(error))
    if require_directory is True and not stat.S_ISDIR(mode):
        _fail("NUMERICAL_SENSITIVITY_REQUIRED_DIRECTORY_MISSING", relative_path)
    if require_directory is False and not stat.S_ISREG(mode):
        _fail("NUMERICAL_SENSITIVITY_REQUIRED_FILE_MISSING", relative_path)
    if require_directory is None and not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
        _fail("NUMERICAL_SENSITIVITY_UNSAFE_FILE_TYPE", relative_path)
    return candidate


def _hash_regular_tree(path):
    """Hash one physical path recursively with stable ordering and no links."""
    path = Path(path)
    try:
        root_mode = path.lstat().st_mode
    except OSError as error:
        _fail("NUMERICAL_SENSITIVITY_FILE_HASH_FAILED", str(error))
    if _is_reparse_or_symlink(path):
        _fail("NUMERICAL_SENSITIVITY_LINK_OR_PATH_ESCAPE", str(path))
    if stat.S_ISREG(root_mode):
        return _sha256_file(path)
    if not stat.S_ISDIR(root_mode):
        _fail("NUMERICAL_SENSITIVITY_UNSAFE_FILE_TYPE", str(path))

    entries = []

    def visit(directory, relative):
        if _is_reparse_or_symlink(directory):
            _fail("NUMERICAL_SENSITIVITY_LINK_OR_PATH_ESCAPE", str(directory))
        entries.append({"path": relative, "kind": "directory"})
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as error:
            _fail("NUMERICAL_SENSITIVITY_FILE_HASH_FAILED", str(error))
        for child in children:
            child_relative = f"{relative}/{child.name}" if relative else child.name
            if _is_reparse_or_symlink(child):
                _fail("NUMERICAL_SENSITIVITY_LINK_OR_PATH_ESCAPE", child_relative)
            try:
                child_mode = child.lstat().st_mode
            except OSError as error:
                _fail("NUMERICAL_SENSITIVITY_FILE_HASH_FAILED", str(error))
            if stat.S_ISDIR(child_mode):
                visit(child, child_relative)
            elif stat.S_ISREG(child_mode):
                entries.append({
                    "path": child_relative,
                    "kind": "file",
                    "sha256": _sha256_file(child),
                })
            else:
                _fail("NUMERICAL_SENSITIVITY_UNSAFE_FILE_TYPE", child_relative)

    visit(path, "")
    return _canonical_sha256({"kind": "directory_tree.v1", "entries": entries})


def _contains_processor_directories(case_root):
    try:
        return any(
            child.name.casefold().startswith("processor") and child.is_dir()
            for child in Path(case_root).iterdir()
        )
    except OSError as error:
        _fail("NUMERICAL_SENSITIVITY_CASE_ROOT_INVALID", str(error))


def _read_json_file(path, code):
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        _fail(code, str(error))
    if not isinstance(value, dict):
        _fail(code)
    return value


def _validate_mesh_source(mesh_case):
    """Verify source safety before any child or staging directory is made."""
    mesh_case = Path(mesh_case)
    if _is_reparse_or_symlink(mesh_case) or not mesh_case.is_dir():
        _fail("NUMERICAL_SENSITIVITY_MESH_INPUT_INVALID")
    if _contains_processor_directories(mesh_case):
        _fail("NUMERICAL_SENSITIVITY_PROCESSOR_DIRECTORIES_FORBIDDEN")
    try:
        mesh_path = _safe_case_path(
            mesh_case, "mesh_manifest.json", require_directory=False
        )
        _safe_case_path(mesh_case, "surface_manifest.json", require_directory=False)
        _safe_case_path(mesh_case, "mesh_input.json", require_directory=False)
        _safe_case_path(mesh_case, "constant/polyMesh", require_directory=True)
    except NumericalSensitivityPreparationError as error:
        _fail("NUMERICAL_SENSITIVITY_MESH_INPUT_INVALID", str(error))
    mesh_manifest = _read_json_file(mesh_path, "NUMERICAL_SENSITIVITY_MESH_INPUT_INVALID")
    if mesh_manifest.get("status") != "PASS":
        _fail("NUMERICAL_SENSITIVITY_MESH_INPUT_INVALID", "mesh manifest is not PASS")
    return _sha256_file(mesh_path)


def _copy_mesh_source_snapshot(mesh_case, staging_root):
    """Copy the immutable mesh source once so both children share one snapshot.

    Passing the original mesh directory to two independent builders leaves a
    time-of-check/time-of-use window: an external edit between those builds
    could create a pair from different geometry or surface inputs.  The
    runner therefore accepts only a regular, link-free source and clones it
    once into the unpublished staging tree.  The source is re-hashed before
    publish, while both children are built exclusively from this same copy.
    """
    source = Path(mesh_case)
    target = Path(staging_root) / "mesh_source_snapshot"
    if target.exists() or _is_reparse_or_symlink(source):
        _fail("NUMERICAL_SENSITIVITY_MESH_INPUT_INVALID")
    try:
        shutil.copytree(source, target, symlinks=False)
    except (OSError, shutil.Error) as error:
        _fail("NUMERICAL_SENSITIVITY_SOURCE_SNAPSHOT_FAILED", str(error))
    try:
        _validate_mesh_source(target)
        return target
    except NumericalSensitivityPreparationError:
        shutil.rmtree(target, ignore_errors=True)
        raise


def _validate_settings(settings):
    if settings is None:
        supplied = {}
    elif isinstance(settings, dict):
        supplied = dict(settings)
    else:
        _fail("NUMERICAL_SENSITIVITY_SETTINGS_INVALID")
    if "thermal_numerics_profile" in supplied:
        _fail("NUMERICAL_SENSITIVITY_NUMERICAL_PROFILE_OVERRIDE_FORBIDDEN")
    if "thermal_parallel_processes" in supplied:
        processes = supplied["thermal_parallel_processes"]
        if (not isinstance(processes, int) or isinstance(processes, bool)
                or processes != 1):
            _fail("NUMERICAL_SENSITIVITY_SERIAL_REQUIRED")
    supplied["thermal_parallel_processes"] = 1
    return supplied


def _validate_child_build_location(staging_root, child_root):
    """Require the builder to publish exactly to its assigned fresh child."""
    staging_root = Path(staging_root)
    child_root = Path(child_root)
    if child_root.parent != staging_root or child_root.name not in _CHILD_BY_ROLE.values():
        _fail("NUMERICAL_SENSITIVITY_CHILD_PATH_UNSAFE")
    if child_root.exists():
        _fail("NUMERICAL_SENSITIVITY_CHILD_PATH_UNSAFE")


def _validate_zero_flow_case(case_root, expected_profile):
    """Reject a child that has any mapped/restarted/non-zero-time state."""
    case_root = Path(case_root)
    if _contains_processor_directories(case_root):
        _fail("NUMERICAL_SENSITIVITY_PROCESSOR_DIRECTORIES_FORBIDDEN")
    contract_path = _safe_case_path(
        case_root, "thermal_input.json", require_directory=False
    )
    contract = _read_json_file(
        contract_path, "NUMERICAL_SENSITIVITY_INITIALISATION_UNSUPPORTED"
    )
    initialisation = contract.get("initialisation")
    if (not isinstance(initialisation, dict)
            or initialisation.get("mode") != "zero_flow"
            or initialisation.get("source_case") is not None
            or initialisation.get("source_time") is not None
            or initialisation.get("pressure_mapping") is not None):
        _fail("NUMERICAL_SENSITIVITY_INITIALISATION_UNSUPPORTED")
    cfg = contract.get("settings")
    numerics = contract.get("numerics")
    if (not isinstance(cfg, dict) or cfg.get("thermal_parallel_processes") != 1
            or cfg.get("thermal_numerics_profile") != expected_profile
            or not isinstance(numerics, dict)
            or numerics.get("profile") != expected_profile):
        _fail("NUMERICAL_SENSITIVITY_SERIAL_OR_PROFILE_INVALID")
    for artifact in _POST_RUN_OR_RESTART_ARTIFACTS:
        if (case_root / artifact).exists():
            _fail("NUMERICAL_SENSITIVITY_INITIALISATION_UNSUPPORTED", artifact)
    mapping_source = case_root / "initialMappingSource"
    if mapping_source.exists():
        _fail("NUMERICAL_SENSITIVITY_INITIALISATION_UNSUPPORTED", "initialMappingSource")
    try:
        allrun = (case_root / "Allrun").read_text(encoding="utf-8")
    except OSError as error:
        _fail("NUMERICAL_SENSITIVITY_INITIALISATION_UNSUPPORTED", str(error))
    if "initialMappingSource" in allrun or "mapFields" in allrun:
        _fail("NUMERICAL_SENSITIVITY_INITIALISATION_UNSUPPORTED", "mapFields")
    try:
        children = list(case_root.iterdir())
    except OSError as error:
        _fail("NUMERICAL_SENSITIVITY_INITIALISATION_UNSUPPORTED", str(error))
    for child in children:
        if not child.is_dir():
            continue
        try:
            time_value = float(child.name)
        except ValueError:
            continue
        if time_value != 0.0:
            _fail("NUMERICAL_SENSITIVITY_INITIALISATION_UNSUPPORTED", child.name)
    return contract


def _validate_declared_numerics_contract(case_root, contract, expected_profile):
    """Recompute numerics from the saved mesh/settings before freezing a seed."""
    mesh_path = _safe_case_path(
        case_root, "mesh_manifest.json", require_directory=False
    )
    mesh_manifest = _read_json_file(
        mesh_path, "NUMERICAL_SENSITIVITY_NUMERICS_CONTRACT_MISMATCH"
    )
    settings = contract.get("settings") if isinstance(contract, dict) else None
    declared = contract.get("numerics") if isinstance(contract, dict) else None
    try:
        expected = cfd_numerics.thermal_numerics_contract(mesh_manifest, settings)
    except (TypeError, ValueError, cfd_numerics.NumericalInputError) as error:
        _fail("NUMERICAL_SENSITIVITY_NUMERICS_CONTRACT_MISMATCH", str(error))
    if (not isinstance(declared, dict) or expected != declared
            or expected.get("profile") != expected_profile):
        _fail("NUMERICAL_SENSITIVITY_NUMERICS_CONTRACT_MISMATCH")
    return expected


def _read_required_text(case_root, relative_path, *, code):
    path = _safe_case_path(case_root, relative_path, require_directory=False)
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        _fail(code, str(error))


def _validate_expected_initial_seed(case_root, contract, expected_profile):
    """Require saved system files/Allrun to be builder-derived, not merely hashed."""
    try:
        expected = cfd_physics.buoyant_initial_seed_expectations(contract)
    except (TypeError, ValueError, KeyError) as error:
        _fail("NUMERICAL_SENSITIVITY_CASE_SEED_EXPECTATION_INVALID", str(error))
    if (expected.get("profile") != expected_profile
            or expected.get("initialisation") != "zero_flow"):
        _fail("NUMERICAL_SENSITIVITY_CASE_SEED_EXPECTATION_INVALID")
    actual_allrun = _read_required_text(
        case_root, "Allrun", code="NUMERICAL_SENSITIVITY_CASE_SEED_EXPECTATION_MISMATCH"
    )
    if _PARALLEL_COMMAND_PATTERN.search(actual_allrun):
        _fail("NUMERICAL_SENSITIVITY_SERIAL_COMMAND_FORBIDDEN")
    if actual_allrun != expected.get("Allrun"):
        _fail("NUMERICAL_SENSITIVITY_CASE_SEED_EXPECTATION_MISMATCH", "Allrun")
    expected_system = expected.get("system")
    if not isinstance(expected_system, dict) or set(expected_system) != set(
            path for path in _CASE_SEED_PATHS if path.startswith("system/")
    ):
        _fail("NUMERICAL_SENSITIVITY_CASE_SEED_EXPECTATION_INVALID")
    for relative_path, expected_text in expected_system.items():
        actual_text = _read_required_text(
            case_root,
            relative_path,
            code="NUMERICAL_SENSITIVITY_CASE_SEED_EXPECTATION_MISMATCH",
        )
        if actual_text != expected_text:
            _fail(
                "NUMERICAL_SENSITIVITY_CASE_SEED_EXPECTATION_MISMATCH",
                relative_path,
            )


def _validated_thermal_physical_snapshot(case_root, contract):
    """Compare sidecar evidence with a fresh derivation from saved thermal input."""
    saved = _thermal_physical_snapshot(case_root)
    try:
        regenerated = cfd_physics.profile_free_thermal_input_snapshot(contract)
    except (TypeError, ValueError, KeyError) as error:
        _fail("NUMERICAL_SENSITIVITY_PHYSICAL_SNAPSHOT_INVALID", str(error))
    if saved != regenerated:
        _fail("NUMERICAL_SENSITIVITY_PHYSICAL_SNAPSHOT_MISMATCH")
    return saved


def _physical_tree_snapshot(case_root):
    """Hash every foundation-required physical input from one built child."""
    required = getattr(sensitivity_job, "_REQUIRED_PHYSICAL_TREE_PATHS", None)
    if not isinstance(required, frozenset):
        _fail("NUMERICAL_SENSITIVITY_FOUNDATION_PHYSICAL_TREE_UNAVAILABLE")
    entries = []
    for relative_path in sorted(required):
        path = _safe_case_path(
            case_root,
            relative_path,
            require_directory=(relative_path == "constant/polyMesh"),
        )
        entries.append({
            "path": relative_path,
            "sha256": _hash_regular_tree(path),
            "immutable": True,
        })
    try:
        return sensitivity_job.create_physical_tree_snapshot(entries)
    except sensitivity_job.NumericalSensitivityJobInputError as error:
        _fail("NUMERICAL_SENSITIVITY_PHYSICAL_TREE_INVALID", str(error))


def _thermal_physical_snapshot(case_root):
    path = _safe_case_path(
        case_root, "thermal_input.physical.v1.json", require_directory=False
    )
    snapshot = _read_json_file(
        path, "NUMERICAL_SENSITIVITY_PHYSICAL_SNAPSHOT_INVALID"
    )
    supplied_hash = snapshot.get("physical_input_sha256")
    body = dict(snapshot)
    body.pop("physical_input_sha256", None)
    if (snapshot.get("contract") != "thermal_input.physical.v1"
            or not isinstance(supplied_hash, str)
            or len(supplied_hash) != 64
            or any(char not in "0123456789abcdef" for char in supplied_hash.lower())
            or supplied_hash != cfd_physics._canonical_json_sha256(body)):
        _fail("NUMERICAL_SENSITIVITY_PHYSICAL_SNAPSHOT_INVALID")
    return snapshot


def _case_seed_snapshot(case_root, *, role, profile):
    """Freeze all profile-specific pre-run execution inputs for one child."""
    entries = []
    for relative_path in _CASE_SEED_PATHS:
        path = _safe_case_path(case_root, relative_path, require_directory=False)
        entries.append({"path": relative_path, "sha256": _sha256_file(path)})
    snapshot = {
        "contract": CASE_SEED_SNAPSHOT_CONTRACT,
        "role": role,
        "case_child": _CHILD_BY_ROLE[role],
        "profile": profile,
        "serial_required": True,
        "requested_ranks": 1,
        "initialisation": "zero_flow",
        "entries": entries,
    }
    snapshot["case_seed_snapshot_sha256"] = _canonical_sha256(snapshot)
    path = Path(case_root) / "case_seed_snapshot.v1.json"
    _write_json(path, snapshot)
    return snapshot


def _write_json(path, value):
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, TypeError, ValueError) as error:
        _fail("NUMERICAL_SENSITIVITY_EVIDENCE_WRITE_FAILED", str(error))


def _prepare_child(mesh_case, staging_root, *, role, common_settings):
    child = _CHILD_BY_ROLE[role]
    profile = _PROFILE_BY_ROLE[role]
    case_root = Path(staging_root) / child
    _validate_child_build_location(staging_root, case_root)
    # ``initial_case_dir`` is deliberately not supplied.  The post-build
    # validation below still rejects any future builder regression that makes
    # this path reuse a completed/mapped/restarted solution.
    settings = dict(common_settings)
    settings["thermal_numerics_profile"] = profile
    built = cfd_physics.build_buoyant_case(
        mesh_case_dir=mesh_case,
        solver_case_dir=case_root,
        settings=settings,
    )
    if not isinstance(built, dict) or built.get("ok") is not True:
        detail = built.get("error") if isinstance(built, dict) else None
        _fail("NUMERICAL_SENSITIVITY_CASE_BUILD_FAILED", detail)
    contract = _validate_zero_flow_case(case_root, profile)
    _validate_declared_numerics_contract(case_root, contract, profile)
    _validate_expected_initial_seed(case_root, contract, profile)
    thermal_snapshot = _validated_thermal_physical_snapshot(case_root, contract)
    physical_tree = _physical_tree_snapshot(case_root)
    seed = _case_seed_snapshot(case_root, role=role, profile=profile)
    return {
        "role": role,
        "case_child": child,
        "case_root": case_root,
        "profile": profile,
        "thermal_input": contract,
        "thermal_physical_snapshot": thermal_snapshot,
        "physical_tree": physical_tree,
        "case_seed": seed,
    }


def _physical_input_sha256(*, mesh_sha256, physical_tree, selector):
    """Bind the same physical input identity used by the pure foundation.

    The foundation intentionally owns this derivation.  Duplicating the
    canonical hash here would make the pre-run and verifier contracts silently
    drift apart.
    """
    try:
        return sensitivity_job.derive_physical_input_sha256(
            mesh_sha256=mesh_sha256,
            physical_tree=physical_tree,
            selector=selector,
        )
    except sensitivity_job.NumericalSensitivityJobInputError as error:
        _fail("NUMERICAL_SENSITIVITY_PHYSICAL_INPUT_BINDING_INVALID", str(error))


def _preparation_manifest(*, mesh_case, mesh_sha256, source_tree_sha256,
                          pair_manifest, job_manifest, baseline, variant):
    children = {}
    for child in (baseline, variant):
        role = child["role"]
        tree = child["physical_tree"]
        children[role] = {
            "case_child": child["case_child"],
            "profile": child["profile"],
            "case_seed_snapshot_path": (
                f"{child['case_child']}/case_seed_snapshot.v1.json"
            ),
            "case_seed_snapshot_sha256": child["case_seed"]["case_seed_snapshot_sha256"],
            "physical_tree_sha256": tree["tree_sha256"],
            "thermal_physical_input_sha256": child[
                "thermal_physical_snapshot"
            ]["physical_input_sha256"],
        }
    manifest = {
        "contract": PREPARATION_CONTRACT,
        "status": "PENDING_SOLVER_EVIDENCE",
        "serial_required": True,
        "requested_ranks": 1,
        "mesh_source": {
            "path": str(mesh_case),
            "mesh_manifest_sha256": mesh_sha256,
            "source_tree_sha256": source_tree_sha256,
            "snapshot_path": "mesh_source_snapshot",
        },
        "frozen_pair_manifest_path": "frozen_pair_manifest.json",
        "frozen_pair_manifest_sha256": pair_manifest["manifest_sha256"],
        "job_manifest_path": "cfd_numerical_sensitivity_job.v1.json",
        "job_manifest_sha256": job_manifest["job_manifest_sha256"],
        "children": children,
    }
    manifest["preparation_manifest_sha256"] = _canonical_sha256(manifest)
    return manifest


def _publish_without_overwrite(staging, target):
    """Publish a complete directory only while the requested target is absent."""
    staging = Path(staging)
    target = Path(target)
    if target.exists():
        _fail("NUMERICAL_SENSITIVITY_TARGET_EXISTS", str(target))
    try:
        # On Windows ``rename`` refuses an existing destination.  The explicit
        # check also protects the normal single-user workflow on other hosts;
        # unlike ``replace``, this function never intentionally overwrites a
        # study that already exists.
        os.rename(staging, target)
    except FileExistsError:
        _fail("NUMERICAL_SENSITIVITY_TARGET_EXISTS", str(target))
    except OSError as error:
        if target.exists():
            _fail("NUMERICAL_SENSITIVITY_TARGET_EXISTS", str(target))
        _fail("NUMERICAL_SENSITIVITY_PUBLISH_FAILED", str(error))


def _target_lock_for(target):
    """Serialize same-process target preparation before the final rename.

    `os.rename` remains the cross-process no-replace boundary: an externally
    created target makes publication fail instead of replacing it.  This small
    keyed lock closes the ordinary multi-click / two-thread race in the Studio
    process without adding a stale cross-process lock-file protocol.
    """
    key = os.path.normcase(os.path.normpath(str(Path(target))))
    with _TARGET_LOCKS_GUARD:
        return _TARGET_LOCKS.setdefault(key, threading.Lock())


def prepare_serial_sensitivity_pair(mesh_case_dir, study_root, *, settings=None,
                                    selector, qoi_limits):
    """Create a frozen serial pair in a new target directory.

    This is preparation only: it writes no solver evidence, starts no process,
    and returns a job manifest that is intentionally still
    ``PENDING_SOLVER_EVIDENCE``.  A result must be created by a later runner
    after both independent serial calculations and post-run validation.
    """
    mesh_case = _resolved_path(mesh_case_dir, "NUMERICAL_SENSITIVITY_MESH_INPUT_INVALID")
    target = _resolved_path(study_root, "NUMERICAL_SENSITIVITY_TARGET_UNSAFE")
    target_lock = _target_lock_for(target)
    if not target_lock.acquire(blocking=False):
        _fail("NUMERICAL_SENSITIVITY_TARGET_BUSY", str(target))
    try:
        return _prepare_serial_sensitivity_pair_locked(
            mesh_case, target, settings=settings, selector=selector,
            qoi_limits=qoi_limits,
        )
    finally:
        target_lock.release()


def _prepare_serial_sensitivity_pair_locked(mesh_case, target, *, settings,
                                            selector, qoi_limits):
    """Implementation after the per-process target publication lock is held."""
    if target.exists():
        _fail("NUMERICAL_SENSITIVITY_TARGET_EXISTS", str(target))
    if target == mesh_case or _is_within(target, mesh_case):
        _fail("NUMERICAL_SENSITIVITY_TARGET_UNSAFE", str(target))

    common_settings = _validate_settings(settings)
    try:
        normalised_selector = sensitivity_job.normalize_occupied_volume_band(selector)
    except sensitivity_job.NumericalSensitivityJobInputError as error:
        _fail("NUMERICAL_SENSITIVITY_SELECTOR_INVALID", str(error))
    source_mesh_sha256 = _validate_mesh_source(mesh_case)
    # The builder must be read-only with respect to its mesh source.  Hash the
    # entire source tree rather than just the mesh manifest: a rewrite of the
    # surface provenance or copied mesh input between child builds would make a
    # comparison non-reproducible even when the child physical hashes happen to
    # agree.
    source_tree_sha256 = _hash_regular_tree(mesh_case)
    # ``create_frozen_pair_manifest`` accepts an author-supplied selector and
    # normalises it itself.  Its canonical form carries selector_sha256, which
    # is intentionally not accepted as a user input field, so retain a clean
    # raw representation for that API while using the canonical one for local
    # identity calculations.
    selector_for_foundation = dict(normalised_selector)
    selector_for_foundation.pop("selector_sha256", None)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(
            prefix=f".{target.name}.staging-", dir=target.parent
        ))
    except OSError as error:
        _fail("NUMERICAL_SENSITIVITY_STAGING_FAILED", str(error))

    published = False
    try:
        mesh_snapshot = _copy_mesh_source_snapshot(mesh_case, staging)
        baseline = _prepare_child(
            mesh_snapshot, staging, role=_BASELINE_ROLE,
            common_settings=common_settings,
        )
        variant = _prepare_child(
            mesh_snapshot, staging, role=_VARIANT_ROLE,
            common_settings=common_settings,
        )
        if _contains_processor_directories(mesh_case):
            _fail("NUMERICAL_SENSITIVITY_PROCESSOR_DIRECTORIES_FORBIDDEN")
        if (_sha256_file(mesh_case / "mesh_manifest.json") != source_mesh_sha256
                or _hash_regular_tree(mesh_case) != source_tree_sha256):
            _fail("NUMERICAL_SENSITIVITY_SOURCE_MUTATED_DURING_PREPARATION")

        if (baseline["thermal_physical_snapshot"]
                != variant["thermal_physical_snapshot"]):
            _fail("NUMERICAL_SENSITIVITY_PHYSICAL_INPUT_MISMATCH")
        if baseline["physical_tree"] != variant["physical_tree"]:
            _fail("NUMERICAL_SENSITIVITY_PHYSICAL_TREE_MISMATCH")
        physical_tree = baseline["physical_tree"]
        thermal_snapshot = baseline["thermal_physical_snapshot"]
        tree_mesh_hash = next(
            entry["sha256"] for entry in physical_tree["entries"]
            if entry["path"] == "mesh_manifest.json"
        )
        if tree_mesh_hash != source_mesh_sha256:
            _fail("NUMERICAL_SENSITIVITY_MESH_INPUT_MISMATCH")

        physical_input_sha256 = _physical_input_sha256(
            mesh_sha256=source_mesh_sha256,
            physical_tree=physical_tree,
            selector=normalised_selector,
        )
        baseline_seed_sha256 = baseline["case_seed"]["case_seed_snapshot_sha256"]
        variant_seed_sha256 = variant["case_seed"]["case_seed_snapshot_sha256"]
        if baseline_seed_sha256 == variant_seed_sha256:
            _fail("NUMERICAL_SENSITIVITY_CASE_SEED_VARIATION_INVALID")
        job_id = sensitivity_job.derive_frozen_pair_job_id(
            mesh_sha256=source_mesh_sha256,
            physical_tree=physical_tree,
            selector=normalised_selector,
            baseline_case_seed_snapshot_sha256=baseline_seed_sha256,
            variant_case_seed_snapshot_sha256=variant_seed_sha256,
        )
        pair_manifest = sensitivity_job.create_frozen_pair_manifest(
            job_id=job_id,
            selector=selector_for_foundation,
            mesh_sha256=source_mesh_sha256,
            physical_input_sha256=physical_input_sha256,
            physical_tree=physical_tree,
            baseline={
                "run_id": f"{job_id}-baseline",
                "profile": baseline["profile"],
                "case_child": baseline["case_child"],
                "processor_directories_present": False,
                "case_seed_snapshot_sha256": baseline_seed_sha256,
            },
            variant={
                "run_id": f"{job_id}-variant",
                "profile": variant["profile"],
                "case_child": variant["case_child"],
                "processor_directories_present": False,
                "case_seed_snapshot_sha256": variant_seed_sha256,
            },
            requested_ranks=1,
        )
        pair_validation = sensitivity_job.validate_frozen_pair_manifest(pair_manifest)
        if not pair_validation["valid"]:
            _fail("NUMERICAL_SENSITIVITY_FROZEN_PAIR_INVALID")
        job_manifest = sensitivity_job.build_cfd_numerical_sensitivity_job_manifest(
            pair_manifest, qoi_limits=qoi_limits
        )
        job_validation = sensitivity_job.validate_cfd_numerical_sensitivity_job_manifest(
            job_manifest, trusted_pair_manifest=pair_manifest
        )
        if not job_validation["structurally_valid"]:
            _fail("NUMERICAL_SENSITIVITY_JOB_MANIFEST_INVALID")

        preparation = _preparation_manifest(
            mesh_case=mesh_case,
            mesh_sha256=source_mesh_sha256,
            source_tree_sha256=source_tree_sha256,
            pair_manifest=pair_manifest,
            job_manifest=job_manifest,
            baseline=baseline,
            variant=variant,
        )
        _write_json(staging / "frozen_pair_manifest.json", pair_manifest)
        _write_json(staging / "cfd_numerical_sensitivity_job.v1.json", job_manifest)
        _write_json(staging / "serial_sensitivity_preparation.v1.json", preparation)
        _publish_without_overwrite(staging, target)
        published = True
    except sensitivity_job.NumericalSensitivityJobInputError as error:
        _fail("NUMERICAL_SENSITIVITY_FOUNDATION_CONTRACT_INVALID", str(error))
    finally:
        if not published and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)

    cases = {}
    for child in (baseline, variant):
        role = child["role"]
        cases[role] = {
            "case_dir": str(target / child["case_child"]),
            "profile": child["profile"],
            "physical_tree": child["physical_tree"],
            "physical_input_sha256": physical_input_sha256,
            "thermal_physical_input_sha256": child[
                "thermal_physical_snapshot"
            ]["physical_input_sha256"],
            "case_seed_snapshot_sha256": child["case_seed"]["case_seed_snapshot_sha256"],
        }
    return {
        "ok": True,
        "status": "PENDING_SOLVER_EVIDENCE",
        "study_root": str(target),
        "frozen_pair_manifest": pair_manifest,
        "job_manifest": job_manifest,
        "preparation_manifest": preparation,
        "cases": cases,
    }
