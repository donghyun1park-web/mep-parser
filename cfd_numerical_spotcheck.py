"""Pure validator for the bounded scheme/time/mesh single-PC spot checks.

This module validates one hash-pinned working-room anchor plus exactly three
named children.  It does not run a solver, select ``latest`` output, accept a
cached PASS, or claim Richardson/GCI uncertainty.  QoIs, fixed time-step
history, terminal/energy balance, residuals, continuity, Boussinesq range and
wall treatment are recomputed from each child's own current raw artifacts.
"""

from __future__ import annotations

import hashlib
import copy
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any

from jsonschema import Draft202012Validator

import cfd_numerics
import cfd_numerical_sensitivity_job as sensitivity_job
import cfd_physics
import cfd_verification as path_security


CONTRACT = "numerical_spotcheck_validation.v1"
MANIFEST_CONTRACT = "numerical_spotcheck.v1"
VALIDATION_SCOPE = "single_pc_numerical_spotcheck"
LABEL = "two_level_engineering_spotcheck_not_gci"
CANONICAL_MANIFEST_PATH = (
    "_working_validation/numerical-spotcheck-v1/numerical_spotcheck.json"
)
CANONICAL_WORKING_ROOM_ACCEPTANCE_PATH = (
    "_working_validation/working-room-v1/working_room_acceptance.json"
)
CHILD_SPECS = {
    "scheme_first_order": {
        "variation": "scheme", "mesh_cell_size_m": 0.125,
        "scheme": "first_order", "delta_t_s": 0.02,
    },
    "time_dt_0_01": {
        "variation": "time", "mesh_cell_size_m": 0.125,
        "scheme": "second_order", "delta_t_s": 0.01,
    },
    "mesh_coarse": {
        "variation": "mesh", "mesh_cell_size_m": 0.177,
        "scheme": "second_order", "delta_t_s": 0.02,
    },
}
ANCHOR_SPEC = {
    "variation": "anchor", "mesh_cell_size_m": 0.125,
    "scheme": "second_order", "delta_t_s": 0.02,
}
_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
# Conservative duplicate-output heuristic, not solver execution attestation.
# Task 5b must still bind genuine case-local raw fields and execution evidence.
_FINGERPRINT_DECIMAL_PLACES = 6
_STRUCTURAL_CASE_ARTIFACTS = (
    "mesh_manifest", "thermal_input", "control_dict", "fv_schemes",
    "fv_solution", "solver_log", "run_manifest", "result_manifest",
    "sample_data",
)
_COMMON_INPUT_NAMES = (
    "geometry", "terminal_contract", "heat_contract", "initial_fields",
    "selector", "zone",
)
_REQUIRED_PHYSICAL_TREE_PATHS = frozenset({
    "0/U", "0/T", "0/k", "0/omega", "0/p", "0/p_rgh", "0/nut",
    "0/alphat", "constant/transportProperties", "constant/g",
    "constant/turbulenceProperties", "constant/fvOptions",
    "constant/polyMesh", "mesh_input.json", "mesh_manifest.json",
    "surface_manifest.json", "thermal_input.physical.v1.json",
    "Allrun", "system/controlDict.transient", "system/fvSchemes.transient",
    "system/fvSolution.transient", "system/controlDict.precondition",
    "system/fvSchemes.precondition", "system/fvSolution.precondition",
    "system/topoSetDict",
})
_SCIENTIFIC_FAILURES = frozenset({
    "NUMERICAL_SPOTCHECK_QOI_LIMIT",
    "NUMERICAL_SPOTCHECK_DRIFT_LIMIT",
    "NUMERICAL_SPOTCHECK_COURANT_LIMIT",
    "NUMERICAL_SPOTCHECK_CONTINUITY_LIMIT",
    "NUMERICAL_SPOTCHECK_RESIDUAL_LIMIT",
    "NUMERICAL_SPOTCHECK_PHI_LIMIT",
    "NUMERICAL_SPOTCHECK_ENERGY_LIMIT",
    "NUMERICAL_SPOTCHECK_BOUSSINESQ_LIMIT",
    "NUMERICAL_SPOTCHECK_WALL_TREATMENT_LIMIT",
    "NUMERICAL_SPOTCHECK_MESH_RATIO_LIMIT",
    "NUMERICAL_SPOTCHECK_MESH_TERMINAL_LIMIT",
})


def _sha256(path: Path) -> str:
    return path_security._sha256(path)


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _lexical_chain_safe(path: Path) -> bool:
    try:
        lexical = path.absolute()
        current = Path(lexical.anchor)
        relative = lexical.relative_to(current)
    except (OSError, RuntimeError, ValueError):
        return False
    for part in (None, *relative.parts):
        if part is not None:
            current = current / part
        try:
            current.lstat()
        except OSError:
            return False
        if _is_reparse(current):
            return False
    return True


def _safe_existing_ref(value: object, root: Path) -> Path | None:
    relative = path_security._relative_ref(value)
    if relative is None:
        return None
    lexical = root.joinpath(*relative.parts)
    try:
        if not _lexical_chain_safe(lexical):
            return None
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(root)
        return resolved if resolved.is_file() else None
    except (OSError, RuntimeError, ValueError):
        return None


def _safe_existing_directory_ref(value: object, root: Path) -> Path | None:
    relative = path_security._relative_ref(value)
    if relative is None:
        return None
    lexical = root.joinpath(*relative.parts)
    try:
        if not _lexical_chain_safe(lexical):
            return None
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(root)
        return resolved if resolved.is_dir() else None
    except (OSError, RuntimeError, ValueError):
        return None


def _snapshot_directory_tree(
    root: Path,
) -> tuple[
    str, dict[Path, tuple[bytes, str, tuple[int, int, int]]]
] | None:
    """Snapshot a regular directory tree with the production hash algorithm."""
    try:
        root_mode = root.lstat().st_mode
    except OSError:
        return None
    if not stat.S_ISDIR(root_mode) or _is_reparse(root) or not _lexical_chain_safe(root):
        return None
    entries: list[dict[str, Any]] = []
    files: dict[Path, tuple[bytes, str, tuple[int, int, int]]] = {}

    def visit(directory: Path, relative: str) -> bool:
        if _is_reparse(directory) or not _lexical_chain_safe(directory):
            return False
        entries.append({"path": relative, "kind": "directory"})
        try:
            children = sorted(directory.iterdir(), key=lambda child: child.name)
        except OSError:
            return False
        for child in children:
            child_relative = f"{relative}/{child.name}" if relative else child.name
            try:
                mode = child.lstat().st_mode
            except OSError:
                return False
            if _is_reparse(child):
                return False
            if stat.S_ISDIR(mode):
                if not visit(child, child_relative):
                    return False
            elif stat.S_ISREG(mode):
                snapshot = _snapshot_file(child)
                if snapshot is None:
                    return False
                files[child.resolve()] = snapshot
                entries.append({
                    "path": child_relative, "kind": "file",
                    "sha256": snapshot[1],
                })
            else:
                return False
        return True

    if not visit(root, ""):
        return None
    return _canonical_sha256({"kind": "directory_tree.v1", "entries": entries}), files


def _read_json(path: Path) -> dict[str, Any] | None:
    return path_security._read_json(path)


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _snapshot_file(
    path: Path,
) -> tuple[bytes, str, tuple[int, int, int]] | None:
    """Capture the one byte sequence used for both hashing and parsing."""
    return path_security._snapshot_file(path)


def _json_from_bytes(data: bytes) -> dict[str, Any] | None:
    return path_security._json_from_bytes(data)


def _text_from_bytes(data: bytes) -> str | None:
    return path_security._text_from_bytes(data)


def _finite(value: object, *, positive: bool = False, nonnegative: bool = False) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    if positive and number <= 0:
        return None
    if nonnegative and number < 0:
        return None
    return number


def _float_text(value: str | None, *, positive: bool = False) -> float | None:
    try:
        return _finite(float(value), positive=positive) if value is not None else None
    except (TypeError, ValueError):
        return None


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _blocked(*codes: str) -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "status": "BLOCKED",
        "blockers": _dedupe(list(codes)),
        "cases": {},
        "comparisons": {},
        "evidence_sha256": {},
        "verification_scope": ["two_level_scheme_time_mesh_spotchecks"],
        "label": LABEL,
        "design_citable": False,
        "release_ready": False,
    }


def _schema_validator() -> Draft202012Validator:
    path = Path(__file__).with_name("numerical_spotcheck.v1.schema.json")
    return Draft202012Validator(json.loads(path.read_text(encoding="utf-8")))


def _record_matches(record: dict[str, Any], case_id: str, expected: dict[str, Any]) -> bool:
    if record.get("case_id") != case_id:
        return False
    for name, value in expected.items():
        actual = record.get(name)
        if isinstance(value, float):
            actual_number = _finite(actual, positive=True)
            if actual_number is None or not math.isclose(
                    actual_number, value, rel_tol=0.0, abs_tol=1e-12):
                return False
        elif actual != value:
            return False
    return True


def _time_weighted(rows: list[dict[str, float]], key: str) -> float | None:
    if len(rows) < 2:
        return None
    total = 0.0
    span = rows[-1]["time_s"] - rows[0]["time_s"]
    if span <= 0:
        return None
    for left, right in zip(rows, rows[1:]):
        width = right["time_s"] - left["time_s"]
        if width <= 0:
            return None
        total += width * 0.5 * (left[key] + right[key])
    value = total / span
    return value if math.isfinite(value) else None


def _terminal_metrics(
        faces: object, rho: float, cp: float, supply_t: float,
        applied_power: float) -> dict[str, float] | None:
    if not isinstance(faces, list) or not faces:
        return None
    inflow = outflow = weighted_exhaust_t = 0.0
    exhaust_flow = 0.0
    for face in faces:
        if not isinstance(face, dict) or set(face) != {
                "face_id", "area_m2", "source_id", "patch", "role",
                "phi_m3_s", "owner_temperature_k"}:
            return None
        if face.get("role") not in {"supply", "exhaust"}:
            return None
        if not isinstance(face.get("source_id"), str) or not face.get("source_id"):
            return None
        if not isinstance(face.get("patch"), str) or not face.get("patch"):
            return None
        phi = _finite(face.get("phi_m3_s"))
        temperature = _finite(face.get("owner_temperature_k"))
        if phi is None or temperature is None:
            return None
        if phi < 0:
            inflow += -phi
        elif phi > 0:
            outflow += phi
        if face["role"] == "exhaust" and phi > 0:
            exhaust_flow += phi
            weighted_exhaust_t += phi * temperature
    denominator = max(inflow, outflow)
    if denominator <= 0 or exhaust_flow <= 0:
        return None
    exhaust_t = weighted_exhaust_t / exhaust_flow
    return {
        "phi_imbalance_ratio": abs(inflow - outflow) / denominator,
        "inflow_m3_s": inflow,
        "outflow_m3_s": outflow,
        "exhaust_temperature_rise_k": exhaust_t - supply_t,
        "energy_closure_ratio": (
            rho * cp * exhaust_flow * (exhaust_t - supply_t) / applied_power
        ),
    }


def _wall_ratio(faces: object) -> float | None:
    if not isinstance(faces, list) or not faces:
        return None
    total = acceptable = 0.0
    for face in faces:
        if not isinstance(face, dict) or set(face) != {
                "face_id", "patch", "area_m2", "y_plus"}:
            return None
        area = _finite(face.get("area_m2"), positive=True)
        y_plus = _finite(face.get("y_plus"), nonnegative=True)
        if area is None or y_plus is None:
            return None
        total += area
        if y_plus <= 5.0 or 30.0 <= y_plus <= 300.0:
            acceptable += area
    return acceptable / total if total > 0 else None


def _positive_map(value: object) -> dict[str, float] | None:
    if not isinstance(value, dict) or not value:
        return None
    output: dict[str, float] = {}
    for key, raw in value.items():
        number = _finite(raw, positive=True)
        if not isinstance(key, str) or not key or number is None:
            return None
        output[key] = number
    return output


def _opening_area_error(mesh: dict[str, Any], terminals: dict[str, Any]) -> float | None:
    expected = _positive_map(terminals.get("opening_area_m2_by_source"))
    applied = _positive_map(mesh.get("applied_opening_area_m2_by_source"))
    if expected is None or applied is None or set(expected) != set(applied):
        return None
    return max(abs(applied[key] - expected[key]) / expected[key] for key in expected)


def _case_seed_sha256(
        common_sha: str, artifacts: dict[str, Path], initial_hashes: dict[str, str],
        physical_tree_sha256: str) -> str:
    def digest(name: str) -> str:
        return initial_hashes[artifacts[name].as_posix()]
    return _canonical_sha256({
        "common_input_sha256": common_sha,
        "mesh_manifest_sha256": digest("mesh_manifest"),
        "thermal_input_sha256": digest("thermal_input"),
        "control_dict_sha256": digest("control_dict"),
        "fv_schemes_sha256": digest("fv_schemes"),
        "fv_solution_sha256": digest("fv_solution"),
        "physical_tree_sha256": physical_tree_sha256,
    })


def _capture_physical_tree(
    record: dict[str, Any],
    root: Path,
    snapshot_bytes: dict[Path, bytes],
    artifact_hashes: dict[str, str],
    source_identities: dict[Path, tuple[int, int, int]],
    directory_hashes: dict[Path, str],
    blockers: list[str],
) -> dict[str, str]:
    raw_tree = record.get("physical_tree")
    if not isinstance(raw_tree, dict) or set(raw_tree) != {"tree_sha256", "entries"}:
        blockers.append("NUMERICAL_SPOTCHECK_PHYSICAL_TREE_INVALID")
        return {}
    entries = raw_tree.get("entries")
    if not isinstance(entries, list):
        blockers.append("NUMERICAL_SPOTCHECK_PHYSICAL_TREE_INVALID")
        return {}
    try:
        normalized = sensitivity_job.create_physical_tree_snapshot(entries)
    except sensitivity_job.NumericalSensitivityJobInputError:
        blockers.append("NUMERICAL_SPOTCHECK_PHYSICAL_TREE_INVALID")
        return {}
    paths = [entry.get("path") for entry in entries if isinstance(entry, dict)]
    if (
        set(paths) != set(_REQUIRED_PHYSICAL_TREE_PATHS)
        or len(paths) != len(set(paths))
        or normalized != raw_tree
    ):
        blockers.append("NUMERICAL_SPOTCHECK_PHYSICAL_TREE_INVALID")
        return {}
    declared = {entry["path"]: entry["sha256"] for entry in entries}
    actual: dict[str, str] = {}
    for relative in sorted(_REQUIRED_PHYSICAL_TREE_PATHS):
        full_ref = f"{record['case_path'].rstrip('/')}/{relative}"
        if relative == "constant/polyMesh":
            directory = _safe_existing_directory_ref(full_ref, root)
            captured = (
                _snapshot_directory_tree(directory) if directory is not None else None
            )
            if captured is None:
                blockers.append("NUMERICAL_SPOTCHECK_PHYSICAL_TREE_INVALID")
                continue
            tree_digest, files = captured
            directory_hashes[directory] = tree_digest
            actual[relative] = tree_digest
            for path, (data, digest, identity) in files.items():
                if path in snapshot_bytes and (
                    artifact_hashes.get(path.as_posix()) != digest
                    or source_identities.get(path) != identity
                ):
                    blockers.append("ARTIFACT_CHANGED_DURING_VALIDATION")
                    continue
                snapshot_bytes[path] = data
                artifact_hashes[path.as_posix()] = digest
                source_identities[path] = identity
        else:
            path = _safe_existing_ref(full_ref, root)
            if path is None:
                blockers.append("NUMERICAL_SPOTCHECK_PHYSICAL_TREE_INVALID")
                continue
            if path in snapshot_bytes:
                digest = artifact_hashes[path.as_posix()]
            else:
                captured = _snapshot_file(path)
                if captured is None:
                    blockers.append("NUMERICAL_SPOTCHECK_PHYSICAL_TREE_INVALID")
                    continue
                data, digest, identity = captured
                snapshot_bytes[path] = data
                artifact_hashes[path.as_posix()] = digest
                source_identities[path] = identity
            actual[relative] = digest
        if actual.get(relative) != declared[relative]:
            blockers.append("NUMERICAL_SPOTCHECK_PHYSICAL_TREE_HASH_MISMATCH")
    return actual


def _foam_value_matches(actual: str | None, expected: tuple[object, ...]) -> bool:
    if actual is None:
        return False
    tokens = actual.split()
    if len(tokens) != len(expected):
        return False
    for observed, required in zip(tokens, expected):
        if isinstance(required, float):
            try:
                number = float(observed)
            except ValueError:
                return False
            if not math.isfinite(number) or not math.isclose(
                number, required, rel_tol=0.0, abs_tol=1e-12
            ):
                return False
        elif observed != required:
            return False
    return True


def _strict_scheme_semantics(scheme: object, text: str) -> dict[str, Any]:
    """Validate the exact bounded first-/second-order OpenFOAM dictionaries."""
    clean = path_security._strip_openfoam_comments(text)
    if "#include" in clean or "$" in clean:
        return {"valid": False, "observed": {}}
    expected_by_scheme = {
        "first_order": {
            "ddtSchemes": {"default": ("Euler",)},
            "divSchemes": {
                "default": ("none",),
                "div(phi,U)": ("bounded", "Gauss", "upwind"),
                "div(phi,T)": ("bounded", "Gauss", "upwind"),
                "div(phi,k)": ("bounded", "Gauss", "upwind"),
                "div(phi,omega)": ("bounded", "Gauss", "upwind"),
            },
            "laplacianSchemes": {
                "default": ("Gauss", "linear", "uncorrected"),
            },
            "snGradSchemes": {"default": ("uncorrected",)},
        },
        "second_order": {
            "ddtSchemes": {"default": ("Euler",)},
            "divSchemes": {
                "default": ("none",),
                "div(phi,U)": (
                    "bounded", "Gauss", "linearUpwind", "grad(U)",
                ),
                "div(phi,T)": ("bounded", "Gauss", "limitedLinear", 1.0),
                "div(phi,k)": ("bounded", "Gauss", "limitedLinear", 1.0),
                "div(phi,omega)": (
                    "bounded", "Gauss", "limitedLinear", 1.0,
                ),
            },
            "laplacianSchemes": {
                "default": ("Gauss", "linear", "limited", 0.5),
            },
            "snGradSchemes": {"default": ("limited", 0.5)},
        },
    }
    expected = expected_by_scheme.get(scheme)
    if expected is None:
        return {"valid": False, "observed": {}}
    observed: dict[str, str] = {}
    valid = True
    for block_name, required_values in expected.items():
        blocks = path_security._named_brace_blocks(clean, block_name)
        if len(blocks) != 1:
            valid = False
            continue
        body = blocks[0]
        for key, required in required_values.items():
            value = path_security._unique_openfoam_value(body, key)
            observed[f"{block_name}.{key}"] = value or ""
            if not _foam_value_matches(value, required):
                valid = False
    profile = (
        cfd_numerics.STABILIZED_FIRST_ORDER
        if scheme == "first_order"
        else cfd_numerics.DESIGN_LIMITED_SECOND_ORDER
    )
    generated = cfd_physics._thermal_fv_schemes({"profile": profile})
    if _normalized_foam_text(text) != _normalized_foam_text(generated):
        valid = False
    return {"valid": valid, "observed": observed}


def _normalized_foam_text(
    text: str, *, axis_keys: tuple[str, ...] = (),
) -> str | None:
    clean = path_security._strip_openfoam_comments(text)
    if "#include" in clean or "$" in clean:
        return None
    for key in axis_keys:
        pattern = re.compile(
            rf"(?m)(^|(?<=[;\r\n{{}}]))[ \t]*{re.escape(key)}\s+([^;{{}}]+);"
        )
        clean, count = pattern.subn(lambda match: f"{match.group(1)}{key} <axis>;", clean)
        if count != 1:
            return None
    return " ".join(clean.split())


def _production_text_equal(actual: str, expected: str) -> bool:
    return actual.replace("\r\n", "\n") == expected.replace("\r\n", "\n")


def _fingerprint_projection(value: Any) -> Any:
    if isinstance(value, float):
        rounded = round(value, _FINGERPRINT_DECIMAL_PLACES)
        return 0.0 if rounded == 0.0 else rounded
    if isinstance(value, list):
        return [_fingerprint_projection(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _fingerprint_projection(item) for key, item in value.items()
        }
    return value


def _physical_sample_fingerprint(
    samples: dict[str, Any],
) -> tuple[str | None, bool, dict[str, Any] | None]:
    """Detect near-copies; this bounded heuristic cannot attest solver execution."""
    valid = set(samples) == {
        "contract", "case_id", "case_seed_sha256", "floor_elevation_m",
        "y_plus_source", "snapshots",
    }
    snapshots = samples.get("snapshots")
    floor = _finite(samples.get("floor_elevation_m"))
    if not isinstance(snapshots, list) or not snapshots or floor is None:
        return None, False, None
    normalized: list[dict[str, Any]] = []
    inventory: dict[str, Any] | None = None
    for snapshot in snapshots:
        if not isinstance(snapshot, dict) or set(snapshot) != {
            "time_s", "cells", "terminal_faces", "wall_faces",
        }:
            return None, False, None
        time_s = _finite(snapshot.get("time_s"), positive=True)
        cells = snapshot.get("cells")
        terminals = snapshot.get("terminal_faces")
        walls = snapshot.get("wall_faces")
        if (
            time_s is None
            or not isinstance(cells, list) or not cells
            or not isinstance(terminals, list) or not terminals
            or not isinstance(walls, list) or not walls
        ):
            return None, False, None
        normalized_cells: list[dict[str, Any]] = []
        for cell in cells:
            if not isinstance(cell, dict) or set(cell) != {
                "id", "center_m", "volume_m3", "temperature_k", "velocity_m_s",
            }:
                return None, False, None
            cell_id = cell.get("id")
            center = cell.get("center_m")
            coordinates = (
                [_finite(value) for value in center]
                if isinstance(center, list) and len(center) == 3 else []
            )
            volume = _finite(cell.get("volume_m3"), positive=True)
            temperature = _finite(cell.get("temperature_k"))
            velocity = _finite(cell.get("velocity_m_s"), nonnegative=True)
            if (
                not isinstance(cell_id, str) or not cell_id
                or len(coordinates) != 3 or None in coordinates or None in (
                volume, temperature, velocity,
                )
            ):
                return None, False, None
            normalized_cells.append({
                "id": cell_id,
                "center_m": coordinates,
                "volume_m3": volume,
                "temperature_k": temperature,
                "velocity_m_s": velocity,
            })
        normalized_terminals: list[dict[str, Any]] = []
        for face in terminals:
            if not isinstance(face, dict) or set(face) != {
                "face_id", "area_m2", "source_id", "patch", "role", "phi_m3_s",
                "owner_temperature_k",
            }:
                return None, False, None
            face_id = face.get("face_id")
            area = _finite(face.get("area_m2"), positive=True)
            phi = _finite(face.get("phi_m3_s"))
            temperature = _finite(face.get("owner_temperature_k"))
            if (
                not isinstance(face_id, str) or not face_id
                or area is None
                or face.get("role") not in {"supply", "exhaust"}
                or not isinstance(face.get("source_id"), str)
                or not face.get("source_id")
                or not isinstance(face.get("patch"), str)
                or not face.get("patch")
                or phi is None or temperature is None
            ):
                return None, False, None
            normalized_terminals.append({
                "face_id": face_id, "area_m2": area,
                "source_id": face["source_id"], "patch": face["patch"],
                "role": face["role"], "phi_m3_s": phi,
                "owner_temperature_k": temperature,
            })
        normalized_walls: list[dict[str, Any]] = []
        for face in walls:
            if not isinstance(face, dict) or set(face) != {
                "face_id", "patch", "area_m2", "y_plus",
            }:
                return None, False, None
            face_id = face.get("face_id")
            patch = face.get("patch")
            area = _finite(face.get("area_m2"), positive=True)
            y_plus = _finite(face.get("y_plus"), nonnegative=True)
            if (
                not isinstance(face_id, str) or not face_id
                or not isinstance(patch, str) or not patch
                or area is None or y_plus is None
            ):
                return None, False, None
            normalized_walls.append({
                "face_id": face_id, "patch": patch,
                "area_m2": area, "y_plus": y_plus,
            })
        key = lambda value: json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        )
        current_inventory = {
            "cells": sorted(({
                "id": row["id"], "center_m": row["center_m"],
                "volume_m3": row["volume_m3"],
            } for row in normalized_cells), key=key),
            "terminal_faces": sorted(({
                "face_id": row["face_id"], "area_m2": row["area_m2"],
                "source_id": row["source_id"], "patch": row["patch"],
                "role": row["role"],
            } for row in normalized_terminals), key=key),
            "wall_faces": sorted(({
                "face_id": row["face_id"], "patch": row["patch"],
                "area_m2": row["area_m2"],
            } for row in normalized_walls), key=key),
        }
        if inventory is None:
            inventory = current_inventory
        elif current_inventory != inventory:
            return None, False, None
        if (
            len({row["id"] for row in normalized_cells}) != len(normalized_cells)
            or len({row["face_id"] for row in normalized_terminals})
            != len(normalized_terminals)
            or len({
                (row["source_id"], row["patch"], row["role"])
                for row in normalized_terminals
            }) != len(normalized_terminals)
            or len({row["face_id"] for row in normalized_walls})
            != len(normalized_walls)
        ):
            return None, False, None
        normalized.append({
            "time_s": time_s,
            "cells": sorted(normalized_cells, key=key),
            "terminal_faces": sorted(normalized_terminals, key=key),
            "wall_faces": sorted(normalized_walls, key=key),
        })
    normalized.sort(key=lambda row: (row["time_s"], _canonical_sha256(row)))
    try:
        fingerprint_input = _fingerprint_projection({"snapshots": normalized})
        return _canonical_sha256(fingerprint_input), valid, inventory
    except (TypeError, ValueError):
        return None, False, None


def _case_input_projection(
    record: dict[str, Any],
    mesh: dict[str, Any],
    thermal: dict[str, Any],
    control: str,
    fv_schemes: str,
    fv_solution: str,
    mesh_digest: str,
    physical_tree: dict[str, str],
) -> dict[str, Any] | None:
    """Separate invariant inputs from the one declared comparison axis."""
    thermal_shared = copy.deepcopy(thermal)
    thermal_shared.pop("created_at", None)
    thermal_mesh_digest = thermal_shared.pop("mesh_manifest_sha256", None)
    thermal_shared.pop("physical_input_sha256", None)
    numerics = thermal_shared.pop("numerics", None)
    settings = thermal_shared.get("settings")
    if (
        not isinstance(numerics, dict)
        or not isinstance(settings, dict)
        or thermal_mesh_digest != mesh_digest
    ):
        return None
    initial_dt = settings.pop("thermal_initial_delta_t_s", None)
    max_dt = settings.pop("thermal_max_delta_t_s", None)
    settings_profile = settings.pop("thermal_numerics_profile", None)
    numerics_invariant = copy.deepcopy(numerics)
    scheme_axis = {
        key: numerics_invariant.pop(key, None)
        for key in (
            "profile", "convection_order", "laplacian_correction",
            "sn_grad_correction",
        )
    }
    normalized_control = _normalized_foam_text(
        control, axis_keys=("deltaT", "maxDeltaT")
    )
    normalized_schemes = _normalized_foam_text(fv_schemes)
    normalized_solution = fv_solution.replace("\r\n", "\n")
    control_dt = _float_text(
        path_security._unique_openfoam_value(control, "deltaT"), positive=True
    )
    control_max_dt = _float_text(
        path_security._unique_openfoam_value(control, "maxDeltaT"), positive=True
    )
    if normalized_control is None or normalized_schemes is None:
        return None
    return {
        "shared": {
            "thermal": thermal_shared,
            "numerics_invariant": numerics_invariant,
            "control_without_delta_t": normalized_control,
            "fv_solution": normalized_solution,
        },
        "mesh": {
            "record_h_m": record.get("mesh_cell_size_m"),
            "manifest": mesh,
            "manifest_sha256": mesh_digest,
            "thermal_mesh_manifest_sha256": thermal_mesh_digest,
        },
        "scheme": {
            "record": record.get("scheme"),
            "settings_profile": settings_profile,
            "thermal": scheme_axis,
            "fv_schemes": normalized_schemes,
        },
        "time": {
            "record_delta_t_s": record.get("delta_t_s"),
            "thermal_initial_delta_t_s": initial_dt,
            "thermal_max_delta_t_s": max_dt,
            "control_delta_t_s": control_dt,
            "control_max_delta_t_s": control_max_dt,
        },
        "physical_tree": physical_tree,
    }


def _validate_controlled_variations(
    projections: dict[str, dict[str, Any]], blockers: list[str],
) -> None:
    anchor = projections.get("anchor")
    if anchor is None or set(projections) != {"anchor", *CHILD_SPECS}:
        blockers.append("NUMERICAL_SPOTCHECK_EXTRA_VARIATION")
        return
    for case_id, projection in projections.items():
        if projection.get("shared") != anchor.get("shared"):
            blockers.append("NUMERICAL_SPOTCHECK_EXTRA_VARIATION")
        anchor_tree = anchor.get("physical_tree")
        current_tree = projection.get("physical_tree")
        if not isinstance(anchor_tree, dict) or not isinstance(current_tree, dict):
            blockers.append("NUMERICAL_SPOTCHECK_EXTRA_VARIATION")
        else:
            changed_physical_paths = {
                path for path in _REQUIRED_PHYSICAL_TREE_PATHS
                if current_tree.get(path) != anchor_tree.get(path)
            }
            expected_physical_changes = {
                "anchor": set(),
                "scheme_first_order": {"system/fvSchemes.transient"},
                "time_dt_0_01": {
                    "system/controlDict.transient",
                    "thermal_input.physical.v1.json",
                },
                "mesh_coarse": {
                    "constant/polyMesh", "mesh_input.json", "mesh_manifest.json",
                    "thermal_input.physical.v1.json",
                },
            }[case_id]
            if changed_physical_paths != expected_physical_changes:
                blockers.append("NUMERICAL_SPOTCHECK_EXTRA_VARIATION")
        allowed = None if case_id == "anchor" else CHILD_SPECS[case_id]["variation"]
        for axis in ("mesh", "scheme", "time"):
            equal = projection.get(axis) == anchor.get(axis)
            if axis == allowed:
                if equal:
                    blockers.append("NUMERICAL_SPOTCHECK_EXTRA_VARIATION")
            elif not equal:
                blockers.append("NUMERICAL_SPOTCHECK_EXTRA_VARIATION")


def _evaluate_case(
    record: dict[str, Any],
    artifacts: dict[str, Path],
    common_sha: str,
    selector: dict[str, Any],
    selector_sha: str,
    geometry_sha: str,
    terminal_contract: dict[str, Any],
    heat_contract: dict[str, Any],
    initial_fields: dict[str, Any],
    initial_hashes: dict[str, str],
    snapshot_bytes: dict[Path, bytes],
    physical_tree_hashes: dict[str, str],
    blockers: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    mesh = _json_from_bytes(snapshot_bytes[artifacts["mesh_manifest"]])
    thermal = _json_from_bytes(snapshot_bytes[artifacts["thermal_input"]])
    run = _json_from_bytes(snapshot_bytes[artifacts["run_manifest"]])
    result = _json_from_bytes(snapshot_bytes[artifacts["result_manifest"]])
    samples = _json_from_bytes(snapshot_bytes[artifacts["sample_data"]])
    if any(value is None for value in (mesh, thermal, run, result, samples)):
        blockers.append("NUMERICAL_SPOTCHECK_ARTIFACT_MALFORMED")
        return {}, {}
    assert mesh is not None and thermal is not None and run is not None
    assert result is not None and samples is not None

    case_id = record["case_id"]
    raw_physical_tree = record.get("physical_tree")
    physical_tree_sha = (
        raw_physical_tree.get("tree_sha256")
        if isinstance(raw_physical_tree, dict) else ""
    )
    expected_seed = _case_seed_sha256(
        common_sha, artifacts, initial_hashes, physical_tree_sha
    )

    def digest(name: str) -> str:
        return initial_hashes[artifacts[name].as_posix()]
    if record.get("case_seed_sha256") != expected_seed:
        blockers.append("NUMERICAL_SPOTCHECK_CASE_SEED_STALE")
    settings = thermal.get("settings") if isinstance(thermal.get("settings"), dict) else {}
    heat = thermal.get("heat") if isinstance(thermal.get("heat"), dict) else {}
    numerics = thermal.get("numerics") if isinstance(thermal.get("numerics"), dict) else {}
    physical_sidecar_path = artifacts["thermal_input"].parent / (
        "thermal_input.physical.v1.json"
    )
    physical_sidecar = (
        _json_from_bytes(snapshot_bytes[physical_sidecar_path])
        if physical_sidecar_path in snapshot_bytes else None
    )
    try:
        expected_physical_sidecar = cfd_physics.profile_free_thermal_input_snapshot(
            thermal
        )
    except (KeyError, TypeError, ValueError):
        expected_physical_sidecar = None
    if physical_sidecar != expected_physical_sidecar:
        blockers.append("NUMERICAL_SPOTCHECK_PHYSICAL_INPUT_INVALID")
    if (
        thermal.get("contract") != "thermal_input.v1"
        or thermal.get("validation_scope") != VALIDATION_SCOPE
        or thermal.get("common_input_sha256") != common_sha
        or thermal.get("selector_sha256") != selector_sha
    ):
        blockers.append("NUMERICAL_SPOTCHECK_COMMON_INPUT_DRIFT")
    if (
        mesh.get("status") != "PASS"
        or mesh.get("geometry_sha256") != geometry_sha
        or mesh.get("terminal_source_ids") != terminal_contract.get("source_ids")
        or mesh.get("patch_topology") != terminal_contract.get("patch_topology")
    ):
        blockers.append("NUMERICAL_SPOTCHECK_MESH_INPUT_DRIFT")
    opening_area_error = _opening_area_error(mesh, terminal_contract)
    mesh_h = _finite(mesh.get("effective_h_m"), positive=True)
    declared_h = _finite(record.get("mesh_cell_size_m"), positive=True)
    if mesh_h is None or declared_h is None or not math.isclose(
            mesh_h, declared_h, rel_tol=0.0, abs_tol=1e-12):
        blockers.append("NUMERICAL_SPOTCHECK_MESH_SIZE_MISMATCH")

    delta_t = _finite(record.get("delta_t_s"), positive=True)
    thermal_delta_t = _finite(settings.get("thermal_initial_delta_t_s"), positive=True)
    thermal_max_delta_t = _finite(
        settings.get("thermal_max_delta_t_s"), positive=True
    )
    thermal_duration = _finite(settings.get("thermal_duration_s"), positive=True)
    control = _text_from_bytes(snapshot_bytes[artifacts["control_dict"]])
    if control is None:
        blockers.append("NUMERICAL_SPOTCHECK_ARTIFACT_READ_FAILED")
        return {}, {}
    control_delta_t = _float_text(
        path_security._unique_openfoam_value(control, "deltaT"), positive=True
    )
    control_max_delta_t = _float_text(
        path_security._unique_openfoam_value(control, "maxDeltaT"), positive=True
    )
    control_end_time = _float_text(
        path_security._unique_openfoam_value(control, "endTime"), positive=True
    )
    if (
        delta_t is None
        or thermal_delta_t is None
        or thermal_max_delta_t is None
        or thermal_duration is None
        or control_delta_t is None
        or control_max_delta_t is None
        or control_end_time is None
        or path_security._unique_openfoam_value(control, "adjustTimeStep") != "no"
        or _float_text(
            path_security._unique_openfoam_value(control, "maxCo"), positive=True
        ) != 1.0
        or not math.isclose(delta_t, thermal_delta_t, rel_tol=0.0, abs_tol=1e-12)
        or not math.isclose(
            delta_t, thermal_max_delta_t, rel_tol=0.0, abs_tol=1e-12
        )
        or not math.isclose(delta_t, control_delta_t, rel_tol=0.0, abs_tol=1e-12)
        or not math.isclose(
            delta_t, control_max_delta_t, rel_tol=0.0, abs_tol=1e-12
        )
        or not math.isclose(
            thermal_duration, control_end_time, rel_tol=0.0, abs_tol=1e-12
        )
    ):
        blockers.append("NUMERICAL_SPOTCHECK_FIXED_DT_REQUIRED")

    expected_profile = (
        cfd_numerics.STABILIZED_FIRST_ORDER
        if record.get("scheme") == "first_order"
        else cfd_numerics.DESIGN_LIMITED_SECOND_ORDER
    )
    if numerics.get("profile") != expected_profile:
        blockers.append("NUMERICAL_SPOTCHECK_SCHEME_DECLARATION_INVALID")
    fv_schemes = _text_from_bytes(snapshot_bytes[artifacts["fv_schemes"]])
    fv_solution = _text_from_bytes(snapshot_bytes[artifacts["fv_solution"]])
    if fv_schemes is None or fv_solution is None:
        blockers.append("NUMERICAL_SPOTCHECK_ARTIFACT_READ_FAILED")
        return {}, {}
    semantic = cfd_numerics.validate_effective_openfoam_numerics(
        numerics, fv_schemes, fv_solution
    )
    strict_semantic = _strict_scheme_semantics(record.get("scheme"), fv_schemes)
    if semantic.get("valid") is not True or strict_semantic.get("valid") is not True:
        blockers.append("NUMERICAL_SPOTCHECK_SCHEME_SEMANTICS_INVALID")
    expected_numerics = {
        "profile": expected_profile,
        "convection_order": 1 if record.get("scheme") == "first_order" else 2,
        "laplacian_correction": (
            "uncorrected" if record.get("scheme") == "first_order" else "limited 0.5"
        ),
        "sn_grad_correction": (
            "uncorrected" if record.get("scheme") == "first_order" else "limited 0.5"
        ),
    }
    if any(numerics.get(key) != value for key, value in expected_numerics.items()):
        blockers.append("NUMERICAL_SPOTCHECK_SCHEME_SEMANTICS_INVALID")
    production_generation_ok = True
    expected_seed_files: dict[str, str] = {}
    try:
        heat_sources = thermal.get("heat_sources")
        if (
            not isinstance(heat_sources, list)
            or not heat_sources
            or any(not isinstance(source, dict) for source in heat_sources)
        ):
            raise TypeError("heat_sources must be a non-empty object list")
        expected_control = cfd_physics._thermal_control_dict(settings, VALIDATION_SCOPE)
        expected_schemes_text = cfd_physics._thermal_fv_schemes(numerics)
        expected_solution_text = cfd_physics._thermal_fv_solution(settings, numerics)
        expected_seed_files = {
            "Allrun": cfd_physics._thermal_allrun(
                settings, map_initial_fields=False
            ),
            "system/controlDict.transient": expected_control,
            "system/fvSchemes.transient": expected_schemes_text,
            "system/fvSolution.transient": expected_solution_text,
            "system/controlDict.precondition": (
                cfd_physics._thermal_precondition_control_dict(settings)
            ),
            "system/fvSchemes.precondition": (
                cfd_physics._thermal_precondition_fv_schemes()
            ),
            "system/fvSolution.precondition": (
                cfd_physics._thermal_precondition_fv_solution(settings)
            ),
            "system/topoSetDict": cfd_physics._thermal_toposet_dict(
                heat_sources
            ),
            "constant/fvOptions": cfd_physics._thermal_fv_options(
                heat_sources, settings
            ),
        }
    except (KeyError, TypeError, ValueError):
        production_generation_ok = False
        expected_control = expected_schemes_text = expected_solution_text = ""
    if not (
        production_generation_ok
        and _production_text_equal(control, expected_control)
        and _production_text_equal(fv_schemes, expected_schemes_text)
        and _production_text_equal(fv_solution, expected_solution_text)
    ):
        blockers.append("NUMERICAL_SPOTCHECK_PRODUCTION_DICTIONARY_MISMATCH")
    if not production_generation_ok:
        blockers.append("NUMERICAL_SPOTCHECK_PHYSICAL_INPUT_INVALID")
        blockers.append("NUMERICAL_SPOTCHECK_PRODUCTION_SEED_MISMATCH")
    case_root = artifacts["thermal_input"].parent
    for relative, expected_text in expected_seed_files.items():
        path = case_root / relative
        actual_text = (
            _text_from_bytes(snapshot_bytes[path]) if path in snapshot_bytes else None
        )
        if actual_text is None or not _production_text_equal(
            actual_text, expected_text
        ):
            blockers.append("NUMERICAL_SPOTCHECK_PRODUCTION_SEED_MISMATCH")
            break
    input_projection = _case_input_projection(
        record, mesh, thermal, control, fv_schemes, fv_solution,
        digest("mesh_manifest"), physical_tree_hashes,
    )
    if input_projection is None:
        blockers.append("NUMERICAL_SPOTCHECK_EXTRA_VARIATION")
        input_projection = {}

    duration = _finite(settings.get("thermal_duration_s"), positive=True)
    flow_time = _finite(settings.get("flow_through_time_s"), positive=True)
    rho = _finite(settings.get("air_density_kg_m3"), positive=True)
    cp = _finite(settings.get("air_specific_heat_j_kg_k"), positive=True)
    supply_t = _finite(settings.get("supply_temperature_k"))
    reference_t = _finite(settings.get("reference_temperature_k"))
    beta = _finite(settings.get("thermal_expansion_coefficient_1_k"), positive=True)
    applied_power = _finite(heat.get("applied_convective_power_w"), positive=True)
    common_power = _finite(
        heat_contract.get("applied_convective_power_w"), positive=True
    )
    initial_temperature = _finite(initial_fields.get("temperature_k"))
    case_initial_temperature = _finite(settings.get("initial_temperature_k"))
    if (
        None in (duration, flow_time, rho, cp, supply_t, reference_t, beta,
                 applied_power, common_power, initial_temperature,
                 case_initial_temperature)
        or not math.isclose(duration, 240.0, rel_tol=0.0, abs_tol=1e-9)
        or not math.isclose(flow_time, 80.0, rel_tol=0.0, abs_tol=1e-9)
        or not math.isclose(applied_power, common_power, rel_tol=0.0, abs_tol=1e-12)
        or not math.isclose(
            initial_temperature, case_initial_temperature,
            rel_tol=0.0, abs_tol=1e-12,
        )
    ):
        blockers.append("NUMERICAL_SPOTCHECK_PHYSICAL_INPUT_INVALID")

    log_text = _text_from_bytes(snapshot_bytes[artifacts["solver_log"]])
    if log_text is None:
        blockers.append("NUMERICAL_SPOTCHECK_ARTIFACT_READ_FAILED")
        return {}, {}
    times = [float(value) for value in re.findall(rf"(?m)^Time =\s*({_NUMBER})", log_text)]
    if (
        not times
        or delta_t is None
        or not all(math.isclose(
            current - previous, delta_t, rel_tol=0.0, abs_tol=1e-9
        ) for previous, current in zip([0.0, *times[:-1]], times))
        or duration is None
        or not math.isclose(times[-1], duration, rel_tol=0.0, abs_tol=1e-9)
        or not re.search(r"(?m)^End\s*$", log_text)
        or re.search(r"FOAM FATAL|Segmentation fault|Floating point exception", log_text, re.I)
    ):
        blockers.append("NUMERICAL_SPOTCHECK_FIXED_DT_HISTORY_INVALID")
    parsed = cfd_physics.parse_thermal_log(log_text)
    peak_courant = _finite((parsed.get("courant") or {}).get("peak_maximum"), nonnegative=True)
    continuity = _finite((parsed.get("continuity") or {}).get("global"))
    if peak_courant is None:
        blockers.append("NUMERICAL_SPOTCHECK_COURANT_EVIDENCE_MISSING")
    elif peak_courant > 1.0:
        blockers.append("NUMERICAL_SPOTCHECK_COURANT_LIMIT")
    if continuity is None:
        blockers.append("NUMERICAL_SPOTCHECK_CONTINUITY_EVIDENCE_MISSING")
    elif abs(continuity) > 1e-6:
        blockers.append("NUMERICAL_SPOTCHECK_CONTINUITY_LIMIT")
    histories = parsed.get("thermal_residual_history") or {}
    residual_failed = False
    for field, limit in cfd_numerics.THERMAL_RESIDUAL_LIMITS.items():
        rows = histories.get(field) if isinstance(histories, dict) else None
        if not isinstance(rows, list) or len(rows) < 5:
            blockers.append("NUMERICAL_SPOTCHECK_RESIDUAL_EVIDENCE_MISSING")
            break
        values = [_finite(row.get("initial"), nonnegative=True)
                  for row in rows[-5:] if isinstance(row, dict)]
        finals = [_finite(row.get("final"), nonnegative=True)
                  for row in rows[-5:] if isinstance(row, dict)]
        if len(values) != 5 or len(finals) != 5 or None in values or None in finals:
            blockers.append("NUMERICAL_SPOTCHECK_RESIDUAL_EVIDENCE_MISSING")
            break
        if max(values) > limit or max(finals) > limit:
            residual_failed = True
    if residual_failed:
        blockers.append("NUMERICAL_SPOTCHECK_RESIDUAL_LIMIT")

    run_input = run.get("input") if isinstance(run.get("input"), dict) else {}
    run_system = run_input.get("system") if isinstance(run_input.get("system"), dict) else {}
    provenance = (
        run_input.get("numerical_provenance")
        if isinstance(run_input.get("numerical_provenance"), dict)
        else {}
    )
    expected_system = {
        "controlDict": digest("control_dict"),
        "fvSchemes": digest("fv_schemes"),
        "fvSolution": digest("fv_solution"),
    }
    effective_settings = (
        run.get("effective_settings")
        if isinstance(run.get("effective_settings"), dict)
        else None
    )
    effective_numerics = (
        run.get("effective_numerics")
        if isinstance(run.get("effective_numerics"), dict)
        else None
    )
    try:
        if list(path_security._artifact_schema_validator(
                "run_manifest.v1.schema.json").iter_errors(run)):
            blockers.append("NUMERICAL_SPOTCHECK_RUN_MANIFEST_SCHEMA_INVALID")
    except (OSError, UnicodeError, json.JSONDecodeError):
        blockers.append("NUMERICAL_SPOTCHECK_RUN_MANIFEST_SCHEMA_UNAVAILABLE")
    solver = run.get("solver") if isinstance(run.get("solver"), dict) else {}
    solver_end_time = _finite(solver.get("end_time"), positive=True)
    if (
        run.get("status") not in {"PASS", "WARN"}
        or solver.get("ended") is not True
        or solver.get("fatal") is not False
        or duration is None
        or solver_end_time is None
        or not math.isclose(solver_end_time, duration, rel_tol=0.0, abs_tol=1e-9)
        or run_input.get("solver_log_sha256") != digest("solver_log")
    ):
        blockers.append("NUMERICAL_SPOTCHECK_RUN_INCOMPLETE_OR_UNBOUND")
    if (
        run.get("contract") != "run_manifest.v1"
        or run.get("engine") != "body_fitted_buoyant_urans"
        or run.get("case_id") != case_id
        or run.get("case_seed_sha256") != expected_seed
        or run_input.get("thermal_input_sha256") != digest("thermal_input")
        or run_system != expected_system
        or provenance.get("source") != "thermal_initial_input"
        or provenance.get("thermal_input_sha256") != digest("thermal_input")
        or provenance.get("thermal_restart_input_sha256") is not None
        or effective_settings != settings
        or effective_numerics != numerics
        or provenance.get("effective_settings_sha256")
        != cfd_physics._canonical_json_sha256(effective_settings)
        or provenance.get("effective_numerics_sha256")
        != cfd_physics._canonical_json_sha256(effective_numerics)
        or provenance.get("system") != expected_system
        or provenance.get("expected_system") != expected_system
        or run_input.get("physical_tree_sha256") != physical_tree_sha
    ):
        blockers.append("NUMERICAL_SPOTCHECK_RUN_CROSS_REFERENCE_INVALID")
    source = result.get("source") if isinstance(result.get("source"), dict) else {}
    expected_sample_path = record["artifacts"]["sample_data"]["path"]
    result_time = _finite(result.get("time_s"), positive=True)
    try:
        if list(path_security._artifact_schema_validator(
                "result_manifest.v1.schema.json").iter_errors(result)):
            blockers.append("NUMERICAL_SPOTCHECK_RESULT_MANIFEST_SCHEMA_INVALID")
    except (OSError, UnicodeError, json.JSONDecodeError):
        blockers.append("NUMERICAL_SPOTCHECK_RESULT_MANIFEST_SCHEMA_UNAVAILABLE")
    if (
        result.get("contract") != "result_manifest.v1"
        or result.get("engine") != "body_fitted_openfoam_vtu"
        or result.get("case_id") != case_id
        or result.get("case_seed_sha256") != expected_seed
        or duration is None
        or result_time is None
        or not math.isclose(result_time, duration, rel_tol=0.0, abs_tol=1e-9)
        or result.get("thermal_input_sha256") != digest("thermal_input")
        or result.get("physical_tree_sha256") != physical_tree_sha
        or result.get("run_manifest_sha256") != digest("run_manifest")
        or source.get("path") != expected_sample_path
        or source.get("sha256") != digest("sample_data")
        or source.get("format") != "numerical_spotcheck_samples.v1"
    ):
        blockers.append("NUMERICAL_SPOTCHECK_RESULT_CROSS_REFERENCE_INVALID")

    raw_selector = dict(selector)
    raw_selector.pop("selector_sha256", None)
    snapshot_rows: list[dict[str, float]] = []
    raw_snapshots = samples.get("snapshots")
    (
        _sample_fingerprint,
        sample_contract_valid,
        sample_inventory,
    ) = _physical_sample_fingerprint(samples)
    sample_inventory_sha = (
        _canonical_sha256(sample_inventory)
        if sample_inventory is not None else None
    )
    inventory_cells = (
        sample_inventory.get("cells")
        if isinstance(sample_inventory, dict) else None
    )
    inventory_terminals = (
        sample_inventory.get("terminal_faces")
        if isinstance(sample_inventory, dict) else None
    )
    inventory_walls = (
        sample_inventory.get("wall_faces")
        if isinstance(sample_inventory, dict) else None
    )
    inventory_volume = (
        sum(row["volume_m3"] for row in inventory_cells)
        if isinstance(inventory_cells, list) else None
    )
    inventory_wall_area = (
        sum(row["area_m2"] for row in inventory_walls)
        if isinstance(inventory_walls, list) else None
    )
    mesh_cell_count = mesh.get("cell_count")
    mesh_volume = _finite(mesh.get("occ_volume_m3"), positive=True)
    mesh_wall_area = _finite(mesh.get("wall_area_m2"), positive=True)
    if (
        not isinstance(inventory_cells, list)
        or not isinstance(inventory_terminals, list)
        or not isinstance(inventory_walls, list)
        or isinstance(mesh_cell_count, bool)
        or not isinstance(mesh_cell_count, int)
        or mesh_cell_count != len(inventory_cells)
        or sample_inventory_sha != mesh.get("sampling_inventory_sha256")
        or mesh_volume is None or inventory_volume is None
        or not math.isclose(mesh_volume, inventory_volume, rel_tol=0.0, abs_tol=1e-9)
        or mesh_wall_area is None or inventory_wall_area is None
        or not math.isclose(
            mesh_wall_area, inventory_wall_area, rel_tol=0.0, abs_tol=1e-9
        )
        or len(inventory_terminals) != len(terminal_contract.get("source_ids") or [])
        or {row["source_id"] for row in inventory_terminals}
        != set(terminal_contract.get("source_ids") or [])
        or {row["patch"] for row in inventory_terminals}
        != set(terminal_contract.get("patch_topology") or [])
        or {row["patch"] for row in inventory_walls}
        != set(thermal.get("wall_patches") or [])
    ):
        blockers.append("NUMERICAL_SPOTCHECK_SAMPLE_INVENTORY_INVALID")
    if (
        not sample_contract_valid
        or
        samples.get("contract") != "numerical_spotcheck_samples.v1"
        or samples.get("case_id") != case_id
        or samples.get("case_seed_sha256") != expected_seed
        or samples.get("y_plus_source") != "openfoam_yPlus_field"
        or not isinstance(raw_snapshots, list)
        or len(raw_snapshots) < 5
        or None in (rho, cp, supply_t, reference_t, beta, applied_power, duration, flow_time)
    ):
        blockers.append("NUMERICAL_SPOTCHECK_SAMPLE_DATA_INVALID")
        raw_snapshots = []
    floor = _finite(samples.get("floor_elevation_m"))
    if floor is None or not math.isclose(floor, 0.0, rel_tol=0.0, abs_tol=1e-12):
        blockers.append("NUMERICAL_SPOTCHECK_SAMPLE_DATA_INVALID")
    maximum_temperature_delta = 0.0
    terminal_ids = set(terminal_contract.get("source_ids") or [])
    terminal_patches = set(terminal_contract.get("patch_topology") or [])
    expected_supply_flow = _finite(
        terminal_contract.get("supply_flow_m3_s"), positive=True
    )
    supply_flow_errors: list[float] = []
    for snapshot in raw_snapshots:
        if not isinstance(snapshot, dict):
            blockers.append("NUMERICAL_SPOTCHECK_SAMPLE_DATA_INVALID")
            break
        time_s = _finite(snapshot.get("time_s"), positive=True)
        try:
            qoi = sensitivity_job.compute_occupied_volume_qois(
                snapshot.get("cells"), raw_selector
            )
        except sensitivity_job.NumericalSensitivityJobInputError:
            blockers.append("NUMERICAL_SPOTCHECK_OCCUPIED_QOI_INVALID")
            break
        terminal = _terminal_metrics(
            snapshot.get("terminal_faces"), rho, cp, supply_t, applied_power
        )
        wall_ratio = _wall_ratio(snapshot.get("wall_faces"))
        if time_s is None or terminal is None or wall_ratio is None:
            blockers.append("NUMERICAL_SPOTCHECK_SAMPLE_DATA_INVALID")
            break
        faces = snapshot["terminal_faces"]
        if (
            {row["source_id"] for row in faces} != terminal_ids
            or {row["patch"] for row in faces} != terminal_patches
        ):
            blockers.append("NUMERICAL_SPOTCHECK_TERMINAL_IDENTITY_DRIFT")
        if terminal["phi_imbalance_ratio"] > 0.001:
            blockers.append("NUMERICAL_SPOTCHECK_PHI_LIMIT")
        if expected_supply_flow is None:
            blockers.append("NUMERICAL_SPOTCHECK_TERMINAL_INPUT_INVALID")
        else:
            supply_flow_errors.append(
                abs(terminal["inflow_m3_s"] - expected_supply_flow)
                / expected_supply_flow
            )
        if not 0.95 <= terminal["energy_closure_ratio"] <= 1.05:
            blockers.append("NUMERICAL_SPOTCHECK_ENERGY_LIMIT")
        minimum_wall_ratio = _finite(
            settings.get("minimum_wall_treatment_area_ratio"), nonnegative=True
        )
        if minimum_wall_ratio is None or wall_ratio < minimum_wall_ratio:
            blockers.append("NUMERICAL_SPOTCHECK_WALL_TREATMENT_LIMIT")
        temperatures = [
            _finite(cell.get("temperature_k"))
            for cell in snapshot.get("cells") or [] if isinstance(cell, dict)
        ]
        if not temperatures or None in temperatures:
            blockers.append("NUMERICAL_SPOTCHECK_SAMPLE_DATA_INVALID")
            break
        maximum_temperature_delta = max(
            maximum_temperature_delta,
            max(abs(value - reference_t) for value in temperatures),
        )
        snapshot_rows.append({
            "time_s": time_s,
            "temperature_k": qoi["occupied_zone_mean_temperature_k"],
            "speed_m_s": qoi["occupied_zone_mean_speed_m_s"],
            "exhaust_delta_t_k": terminal["exhaust_temperature_rise_k"],
            "phi_imbalance_ratio": terminal["phi_imbalance_ratio"],
            "energy_closure_ratio": terminal["energy_closure_ratio"],
            "wall_treatment_ratio": wall_ratio,
        })
    if beta is not None and beta * maximum_temperature_delta > 0.1:
        blockers.append("NUMERICAL_SPOTCHECK_BOUSSINESQ_LIMIT")

    metrics: dict[str, Any] = {}
    if snapshot_rows and duration is not None and flow_time is not None:
        expected_start = duration - 0.1 * flow_time
        sample_times = [row["time_s"] for row in snapshot_rows]
        final_window_valid = (
            len(snapshot_rows) == len(raw_snapshots)
            and all(right > left for left, right in zip(sample_times, sample_times[1:]))
            and all(
                expected_start - 1e-9 <= time_s <= duration + 1e-9
                for time_s in sample_times
            )
            and math.isclose(
                sample_times[0], expected_start, rel_tol=0.0, abs_tol=1e-9
            )
            and math.isclose(
                sample_times[-1], duration, rel_tol=0.0, abs_tol=1e-9
            )
        )
        if not final_window_valid:
            blockers.append("NUMERICAL_SPOTCHECK_FINAL_WINDOW_INVALID")
            return metrics, input_projection
        full = {
            key: _time_weighted(snapshot_rows, key)
            for key in ("temperature_k", "speed_m_s", "exhaust_delta_t_k")
        }
        midpoint = 0.5 * (snapshot_rows[0]["time_s"] + snapshot_rows[-1]["time_s"])
        first_half = [row for row in snapshot_rows if row["time_s"] <= midpoint]
        second_half = [row for row in snapshot_rows if row["time_s"] >= midpoint]
        drifts: dict[str, float] = {}
        for key, normalizer in (
            ("temperature_k", max(abs((full["temperature_k"] or 0.0) - supply_t), 1.0)),
            ("speed_m_s", max(abs(full["speed_m_s"] or 0.0), 0.05)),
            ("exhaust_delta_t_k", max(abs(full["exhaust_delta_t_k"] or 0.0), 1.0)),
        ):
            left = _time_weighted(first_half, key)
            right = _time_weighted(second_half, key)
            if left is None or right is None:
                blockers.append("NUMERICAL_SPOTCHECK_DRIFT_EVIDENCE_MISSING")
                continue
            drifts[key] = abs(right - left) / normalizer
            if drifts[key] > 0.02:
                blockers.append("NUMERICAL_SPOTCHECK_DRIFT_LIMIT")
        metrics = {
            "occupied_zone_mean_temperature_k": full["temperature_k"],
            "occupied_zone_mean_speed_m_s": full["speed_m_s"],
            "exhaust_temperature_rise_k": full["exhaust_delta_t_k"],
            "maximum_phi_imbalance_ratio": max(
                row["phi_imbalance_ratio"] for row in snapshot_rows
            ),
            "minimum_energy_closure_ratio": min(
                row["energy_closure_ratio"] for row in snapshot_rows
            ),
            "maximum_energy_closure_ratio": max(
                row["energy_closure_ratio"] for row in snapshot_rows
            ),
            "minimum_wall_treatment_ratio": min(
                row["wall_treatment_ratio"] for row in snapshot_rows
            ),
            "peak_courant": peak_courant,
            "global_continuity": continuity,
            "boussinesq_beta_delta": (
                beta * maximum_temperature_delta if beta is not None else None
            ),
            "normalized_half_window_drift": drifts,
            "sample_count": len(snapshot_rows),
            "effective_h_m": mesh_h,
            "terminal_source_ids": mesh.get("terminal_source_ids"),
            "patch_topology": mesh.get("patch_topology"),
            "applied_opening_area_error_ratio": opening_area_error,
            "actual_supply_flow_error_ratio": (
                max(supply_flow_errors) if supply_flow_errors else None
            ),
            "sampling_inventory_sha256": sample_inventory_sha,
            "decision_trace_sha256": _canonical_sha256({
                "snapshot_rows": snapshot_rows,
                "peak_courant": peak_courant,
                "global_continuity": continuity,
                "maximum_temperature_delta": maximum_temperature_delta,
            }),
        }
    return metrics, input_projection


def validate_numerical_spotcheck_manifest(
    manifest_path: Path,
    projects_root: Path,
    evaluator_output_path: Path | None = None,
) -> dict[str, Any]:
    """Recompute the exact anchor + three-child bounded numerical study."""
    root = path_security._canonical_root(Path(projects_root))
    if root is None:
        return _blocked("PROJECTS_ROOT_INVALID")
    manifest_file = path_security._safe_manifest(Path(manifest_path), root)
    if manifest_file is None:
        return _blocked("NUMERICAL_SPOTCHECK_MANIFEST_PATH_INVALID")
    canonical_manifest = _safe_existing_ref(CANONICAL_MANIFEST_PATH, root)
    if canonical_manifest is None or manifest_file != canonical_manifest:
        return _blocked("NUMERICAL_SPOTCHECK_MANIFEST_PATH_INVALID")
    manifest_snapshot = _snapshot_file(manifest_file)
    if manifest_snapshot is None:
        return _blocked("NUMERICAL_SPOTCHECK_ARTIFACT_READ_FAILED")
    manifest_bytes, manifest_digest, manifest_identity = manifest_snapshot
    manifest = _json_from_bytes(manifest_bytes)
    if manifest is None:
        return _blocked("NUMERICAL_SPOTCHECK_MANIFEST_MALFORMED")
    children = manifest.get("children")
    if not isinstance(children, dict) or set(children) != set(CHILD_SPECS):
        return _blocked("NUMERICAL_SPOTCHECK_CHILD_SET_INVALID")
    all_refs: list[object] = []
    if isinstance(manifest.get("common_artifacts"), dict):
        all_refs.extend(manifest["common_artifacts"].values())
    for record in [manifest.get("anchor"), *children.values()]:
        if isinstance(record, dict) and isinstance(record.get("artifacts"), dict):
            all_refs.extend(record["artifacts"].values())
    if any(
        not isinstance(ref, dict)
        or path_security._relative_ref(ref.get("path")) is None
        for ref in all_refs
    ):
        return _blocked("NUMERICAL_SPOTCHECK_ARTIFACT_PATH_INVALID")
    common_artifact_refs = manifest.get("common_artifacts")
    acceptance_ref = (
        common_artifact_refs.get("working_room_acceptance")
        if isinstance(common_artifact_refs, dict)
        else None
    )
    if (
        not isinstance(acceptance_ref, dict)
        or acceptance_ref.get("path") != CANONICAL_WORKING_ROOM_ACCEPTANCE_PATH
    ):
        return _blocked("NUMERICAL_SPOTCHECK_ANCHOR_BINDING_INVALID")
    try:
        if list(_schema_validator().iter_errors(manifest)):
            return _blocked("NUMERICAL_SPOTCHECK_MANIFEST_SCHEMA_INVALID")
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _blocked("NUMERICAL_SPOTCHECK_SCHEMA_UNAVAILABLE")

    anchor = manifest["anchor"]
    if (
        not _record_matches(anchor, "anchor", ANCHOR_SPEC)
        or anchor.get("case_path") != "_working_validation/working-room-v1/anchor"
    ):
        return _blocked("NUMERICAL_SPOTCHECK_ANCHOR_INVALID")
    for name, expected in CHILD_SPECS.items():
        record = children[name]
        if not _record_matches(record, name, expected):
            return _blocked("NUMERICAL_SPOTCHECK_CHILD_DECLARATION_INVALID")
        if record.get("case_path") != f"_working_validation/numerical-spotcheck-v1/{name}":
            return _blocked("NUMERICAL_SPOTCHECK_CHILD_PATH_INVALID")

    blockers: list[str] = []
    artifact_hashes: dict[str, str] = {}
    snapshot_bytes: dict[Path, bytes] = {manifest_file: manifest_bytes}
    source_identities: dict[Path, tuple[int, int, int]] = {
        manifest_file: manifest_identity,
    }
    directory_hashes: dict[Path, str] = {}
    common_paths: dict[str, Path] = {}
    for name, ref in manifest["common_artifacts"].items():
        path = _safe_existing_ref(ref["path"], root)
        if path is None:
            blockers.append("NUMERICAL_SPOTCHECK_ARTIFACT_PATH_INVALID")
            continue
        snapshot = _snapshot_file(path)
        if snapshot is None:
            blockers.append("NUMERICAL_SPOTCHECK_ARTIFACT_READ_FAILED")
            continue
        data, digest, identity = snapshot
        if digest != ref["sha256"]:
            blockers.append("NUMERICAL_SPOTCHECK_ARTIFACT_HASH_MISMATCH")
            continue
        common_paths[name] = path
        artifact_hashes[path.as_posix()] = digest
        snapshot_bytes[path] = data
        source_identities[path] = identity
    record_paths: dict[str, dict[str, Path]] = {}
    records = {"anchor": anchor, **children}
    for case_id, record in records.items():
        prefix = record["case_path"].rstrip("/") + "/"
        paths: dict[str, Path] = {}
        for name, ref in record["artifacts"].items():
            if not ref["path"].startswith(prefix):
                blockers.append("NUMERICAL_SPOTCHECK_ARTIFACT_OUTSIDE_CASE")
                continue
            path = _safe_existing_ref(ref["path"], root)
            if path is None:
                blockers.append("NUMERICAL_SPOTCHECK_ARTIFACT_PATH_INVALID")
                continue
            snapshot = _snapshot_file(path)
            if snapshot is None:
                blockers.append("NUMERICAL_SPOTCHECK_ARTIFACT_READ_FAILED")
                continue
            data, digest, identity = snapshot
            if digest != ref["sha256"]:
                blockers.append("NUMERICAL_SPOTCHECK_ARTIFACT_HASH_MISMATCH")
                continue
            paths[name] = path
            artifact_hashes[path.as_posix()] = digest
            snapshot_bytes[path] = data
            source_identities[path] = identity
        if set(paths) == set(_STRUCTURAL_CASE_ARTIFACTS):
            record_paths[case_id] = paths
    physical_tree_hashes_by_case: dict[str, dict[str, str]] = {}
    for case_id, record in records.items():
        physical_tree_hashes_by_case[case_id] = _capture_physical_tree(
            record, root, snapshot_bytes, artifact_hashes,
            source_identities, directory_hashes, blockers,
        )
    anchor_case_directory = _safe_existing_directory_ref(anchor["case_path"], root)
    anchor_case_capture = (
        _snapshot_directory_tree(anchor_case_directory)
        if anchor_case_directory is not None else None
    )
    authoritative_anchor_sha = ""
    if anchor_case_capture is None:
        blockers.append("NUMERICAL_SPOTCHECK_ANCHOR_BINDING_INVALID")
    else:
        authoritative_anchor_sha, anchor_case_files = anchor_case_capture
        directory_hashes[anchor_case_directory] = authoritative_anchor_sha
        for path, (data, digest, identity) in anchor_case_files.items():
            if path in snapshot_bytes and (
                artifact_hashes.get(path.as_posix()) != digest
                or source_identities.get(path) != identity
            ):
                blockers.append("ARTIFACT_CHANGED_DURING_VALIDATION")
                continue
            snapshot_bytes[path] = data
            artifact_hashes[path.as_posix()] = digest
            source_identities[path] = identity
    if blockers:
        return _blocked(*blockers)
    sources = list(source_identities)
    if (
        len(source_identities) != len(sources)
        or len(set(source_identities.values())) != len(source_identities)
    ):
        return _blocked("NUMERICAL_SPOTCHECK_ARTIFACT_PATH_INVALID")
    output_error = path_security._output_blocker(
        evaluator_output_path, root, [*sources, *directory_hashes]
    )
    if output_error:
        return _blocked(output_error)

    common_payloads = {
        name: _json_from_bytes(snapshot_bytes[path])
        for name, path in common_paths.items()
    }
    if any(value is None for value in common_payloads.values()):
        return _blocked("NUMERICAL_SPOTCHECK_COMMON_ARTIFACT_MALFORMED")
    acceptance = common_payloads["working_room_acceptance"] or {}
    terminal_contract = common_payloads["terminal_contract"] or {}
    heat_contract = common_payloads["heat_contract"] or {}
    initial_fields = common_payloads["initial_fields"] or {}
    selector = common_payloads["selector"] or {}
    zone = common_payloads["zone"] or {}
    if (
        (common_payloads["geometry"] or {}).get("contract") != "geometry.v2"
        or terminal_contract.get("contract") != "terminal_contract.v1"
        or heat_contract.get("contract") != "heat_contract.v1"
        or initial_fields.get("contract") != "initial_fields.v1"
        or zone.get("contract") != "occupied_zone.v1"
    ):
        blockers.append("NUMERICAL_SPOTCHECK_COMMON_INPUT_INVALID")
    raw_selector = dict(selector)
    supplied_selector_sha = raw_selector.pop("selector_sha256", None)
    try:
        normalized_selector = sensitivity_job.normalize_occupied_volume_band(raw_selector)
    except sensitivity_job.NumericalSensitivityJobInputError:
        normalized_selector = None
    if (
        normalized_selector is None
        or supplied_selector_sha != normalized_selector.get("selector_sha256")
        or zone.get("selector_sha256") != supplied_selector_sha
    ):
        blockers.append("NUMERICAL_SPOTCHECK_SELECTOR_DRIFT")
        normalized_selector = selector
    common_hashes = {
        name: artifact_hashes[common_paths[name].as_posix()]
        for name in _COMMON_INPUT_NAMES
    }
    common_sha = _canonical_sha256(common_hashes)
    geometry_sha = common_hashes["geometry"]
    if (
        acceptance.get("contract") != "working_room_acceptance.v1"
        or acceptance.get("status") != "PASS"
        or acceptance.get("authoritative_case_path") != anchor["case_path"]
        or acceptance.get("authoritative_case_sha256") != authoritative_anchor_sha
    ):
        blockers.append("NUMERICAL_SPOTCHECK_ANCHOR_BINDING_INVALID")

    case_metrics: dict[str, dict[str, Any]] = {}
    input_projections: dict[str, dict[str, Any]] = {}
    for case_id, record in records.items():
        metrics, projection = _evaluate_case(
            record,
            record_paths[case_id],
            common_sha,
            normalized_selector,
            supplied_selector_sha,
            geometry_sha,
            terminal_contract,
            heat_contract,
            initial_fields,
            artifact_hashes,
            snapshot_bytes,
            physical_tree_hashes_by_case[case_id],
            blockers,
        )
        case_metrics[case_id] = metrics
        input_projections[case_id] = projection
    _validate_controlled_variations(input_projections, blockers)
    anchor_inventory = (case_metrics.get("anchor") or {}).get(
        "sampling_inventory_sha256"
    )
    if (
        not isinstance(anchor_inventory, str)
        or (case_metrics.get("scheme_first_order") or {}).get(
            "sampling_inventory_sha256"
        ) != anchor_inventory
        or (case_metrics.get("time_dt_0_01") or {}).get(
            "sampling_inventory_sha256"
        ) != anchor_inventory
        or (case_metrics.get("mesh_coarse") or {}).get(
            "sampling_inventory_sha256"
        ) == anchor_inventory
    ):
        blockers.append("NUMERICAL_SPOTCHECK_SAMPLE_INVENTORY_INVALID")

    for artifact_name in ("run_manifest", "result_manifest", "sample_data"):
        digests = [
            artifact_hashes[record_paths[case_id][artifact_name].as_posix()]
            for case_id in records
        ]
        if len(set(digests)) != len(digests):
            blockers.append("NUMERICAL_SPOTCHECK_COPIED_RESULT")
    physical_sample_fingerprints: list[str] = []
    for case_id in records:
        sample_path = record_paths[case_id]["sample_data"]
        samples = _json_from_bytes(snapshot_bytes[sample_path])
        if samples is None:
            blockers.append("NUMERICAL_SPOTCHECK_ARTIFACT_MALFORMED")
            continue
        fingerprint, sample_contract_valid, _inventory = (
            _physical_sample_fingerprint(samples)
        )
        if fingerprint is None:
            blockers.append("NUMERICAL_SPOTCHECK_SAMPLE_DATA_INVALID")
            continue
        physical_sample_fingerprints.append(fingerprint)
        if not sample_contract_valid:
            blockers.append("NUMERICAL_SPOTCHECK_SAMPLE_DATA_INVALID")
    if (
        len(physical_sample_fingerprints) != len(records)
        or len(set(physical_sample_fingerprints))
        != len(physical_sample_fingerprints)
    ):
        blockers.append("NUMERICAL_SPOTCHECK_COPIED_RESULT")
    decision_fingerprints = [
        (case_metrics.get(case_id) or {}).get("decision_trace_sha256")
        for case_id in records
    ]
    if (
        any(not isinstance(value, str) for value in decision_fingerprints)
        or len(set(decision_fingerprints)) != len(decision_fingerprints)
    ):
        blockers.append("NUMERICAL_SPOTCHECK_COPIED_RESULT")

    comparisons: dict[str, Any] = {}
    anchor_metrics = case_metrics.get("anchor") or {}
    required_qois = (
        "occupied_zone_mean_temperature_k",
        "occupied_zone_mean_speed_m_s",
        "exhaust_temperature_rise_k",
    )
    if all(_finite(anchor_metrics.get(name)) is not None for name in required_qois):
        for child in CHILD_SPECS:
            metrics = case_metrics.get(child) or {}
            if not all(_finite(metrics.get(name)) is not None for name in required_qois):
                blockers.append("NUMERICAL_SPOTCHECK_QOI_EVIDENCE_MISSING")
                continue
            delta_t = abs(
                metrics["occupied_zone_mean_temperature_k"]
                - anchor_metrics["occupied_zone_mean_temperature_k"]
            )
            delta_u = abs(
                metrics["occupied_zone_mean_speed_m_s"]
                - anchor_metrics["occupied_zone_mean_speed_m_s"]
            )
            relative_u = delta_u / max(
                abs(anchor_metrics["occupied_zone_mean_speed_m_s"]), 0.05
            )
            delta_exhaust = abs(
                metrics["exhaust_temperature_rise_k"]
                - anchor_metrics["exhaust_temperature_rise_k"]
            )
            passed = (
                delta_t <= 0.5
                and delta_u <= 0.05
                and relative_u <= 0.10
                and delta_exhaust <= 0.5
            )
            if not passed:
                blockers.append("NUMERICAL_SPOTCHECK_QOI_LIMIT")
            comparisons[child] = {
                "absolute_temperature_difference_k": delta_t,
                "absolute_speed_difference_m_s": delta_u,
                "relative_speed_difference": relative_u,
                "absolute_exhaust_temperature_rise_difference_k": delta_exhaust,
                "passed": passed,
            }
    else:
        blockers.append("NUMERICAL_SPOTCHECK_QOI_EVIDENCE_MISSING")

    mesh_metrics = case_metrics.get("mesh_coarse") or {}
    anchor_h = _finite(anchor_metrics.get("effective_h_m"), positive=True)
    mesh_h = _finite(mesh_metrics.get("effective_h_m"), positive=True)
    if anchor_h is None or mesh_h is None or max(anchor_h, mesh_h) / min(anchor_h, mesh_h) < 1.25:
        blockers.append("NUMERICAL_SPOTCHECK_MESH_RATIO_LIMIT")
    if (
        mesh_metrics.get("terminal_source_ids") != anchor_metrics.get("terminal_source_ids")
        or mesh_metrics.get("patch_topology") != anchor_metrics.get("patch_topology")
        or (_finite(mesh_metrics.get("applied_opening_area_error_ratio"), nonnegative=True) is None)
        or float(mesh_metrics.get("applied_opening_area_error_ratio", math.inf)) > 0.03
        or (_finite(mesh_metrics.get("actual_supply_flow_error_ratio"), nonnegative=True) is None)
        or float(mesh_metrics.get("actual_supply_flow_error_ratio", math.inf)) > 0.01
    ):
        blockers.append("NUMERICAL_SPOTCHECK_MESH_TERMINAL_LIMIT")

    evidence_hashes = {manifest_file.as_posix(): manifest_digest, **artifact_hashes}
    try:
        changed = any(
            not path_security._lexical_chain_safe(Path(path_text))
            or path_security._file_identity(Path(path_text))
            != source_identities[Path(path_text)]
            or _sha256(Path(path_text)) != expected_digest
            for path_text, expected_digest in evidence_hashes.items()
        )
    except OSError:
        changed = True
    if not changed:
        for directory, expected_digest in directory_hashes.items():
            captured = _snapshot_directory_tree(directory)
            if captured is None or captured[0] != expected_digest:
                changed = True
                break
    if changed:
        return _blocked("ARTIFACT_CHANGED_DURING_VALIDATION")
    public_evidence_hashes = {
        Path(path_text).relative_to(root).as_posix(): expected_digest
        for path_text, expected_digest in evidence_hashes.items()
    }
    public_evidence_hashes.update({
        directory.relative_to(root).as_posix(): digest
        for directory, digest in directory_hashes.items()
    })
    blockers = _dedupe(blockers)
    status = (
        "PASS" if not blockers
        else "FAIL" if any(code in _SCIENTIFIC_FAILURES for code in blockers)
        else "BLOCKED"
    )
    return {
        "contract": CONTRACT,
        "status": status,
        "blockers": blockers,
        "cases": case_metrics,
        "comparisons": comparisons,
        "evidence_sha256": dict(sorted(public_evidence_hashes.items())),
        "verification_scope": ["two_level_scheme_time_mesh_spotchecks"],
        "label": LABEL,
        "design_citable": False,
        "release_ready": False,
    }
