"""Pure working-room and SGI/restart acceptance validators.

The module deliberately does not run CAD, meshing, OpenFOAM, Studio, or a
browser.  It only reopens fixed, hash-pinned evidence beneath a caller-supplied
projects root and derives fail-closed acceptance outcomes.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_PARTS = frozenset({
    "latest", "cache", "caches", ".cache", ".pytest_cache", "__pycache__",
    "tmp", "temp", ".tmp", "generated",
})
_WORKING_ROOM_RELATIVE = PurePosixPath(
    "_working_validation/working-room-v1/working_room_acceptance.json"
)
_SGI_RELATIVE = PurePosixPath(
    "_working_validation/sgi-screening-v1/sgi_screening_acceptance.json"
)
_WORKING_ARTIFACTS = (
    "geometry", "surface", "mesh_input", "mesh", "thermal_input",
    "control_dict", "fv_schemes", "fv_solution", "turbulence_properties", "allrun",
    "thermal_progress", "run", "result",
    "check_mesh_log", "solver_log", "field_t", "field_u", "field_phi", "field_v", "vtu",
    "summary", "slice_x", "slice_y", "slice_z", "report",
)
_SGI_CASE_ARTIFACTS = _WORKING_ARTIFACTS + (
    "case_meta", "opening_verification", "runner_log",
)
_RESTART_EVIDENCE_KEYS = (
    "pre_attempt_snapshot", "post_attempt_snapshot", "checkpoint_solver_log",
    "checkpoint_field_t", "checkpoint_field_u", "checkpoint_field_phi", "process_audit",
)


def _canonical_json_sha256(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalized_working_input_hashes(
    surface: dict[str, Any], mesh_input: dict[str, Any], thermal: dict[str, Any],
) -> dict[str, str]:
    """Hash complete inputs after removing only case-namespace hash cascades."""

    normalized_surface = copy.deepcopy(surface)
    source = normalized_surface.get("source")
    if isinstance(source, dict):
        source["geometry_path"] = "$CASE/geometry.json"

    normalized_mesh_input = copy.deepcopy(mesh_input)
    normalized_mesh_input["surface_manifest_sha256"] = "$SURFACE_MANIFEST"

    normalized_thermal = copy.deepcopy(thermal)
    normalized_thermal["mesh_manifest_sha256"] = "$MESH_MANIFEST"
    return {
        "surface_input_sha256": _canonical_json_sha256(normalized_surface),
        "mesh_input_sha256": _canonical_json_sha256(normalized_mesh_input),
        "thermal_input_sha256": _canonical_json_sha256(normalized_thermal),
    }


def build_working_room_geometry() -> dict:
    """Return the canonical reviewed 2 m working-room ``geometry.v2`` input."""

    def element(element_id, category, semantic, *, geometry, source_ref=None):
        return {
            **geometry,
            "id": element_id,
            "id_stability": "source_derived",
            "category": category,
            "source_ref": dict(source_ref or {
                "handle": element_id,
                "handles": [element_id],
                "layer": "WORKING_VALIDATION",
                "block_name": element_id,
                "entity_type": "validation_fixture",
            }),
            "confirmed": True,
            "confirmation_state": "confirmed",
            "semantic": semantic,
            "level_id": "level-0",
        }

    zone_id = "working-room-air"
    equipment = [
        element("working-room-supply", "equipment", {
            "kind": "air_terminal",
            "role": "supply",
            "airflow_cmh": 360.0,
            "diameter_mm": 250.0,
            "host_surface": "wall:x0",
            "center_z_mm": 1500.0,
            "normal": [1.0, 0.0, 0.0],
            "supply_temperature_k": 293.15,
        }, geometry={
            "kind": "circle", "center": [0.0, 1000.0], "radius": 125.0,
            "space_id": zone_id,
        }),
        element("working-room-exhaust", "equipment", {
            "kind": "air_terminal",
            "role": "exhaust",
            "airflow_cmh": 360.0,
            "diameter_mm": 250.0,
            "host_surface": "wall:xl",
            "center_z_mm": 1500.0,
            "normal": [1.0, 0.0, 0.0],
        }, geometry={
            "kind": "circle", "center": [2000.0, 1000.0], "radius": 125.0,
            "space_id": zone_id,
        }),
        element("manual_heat_1", "equipment", {
            "kind": "equipment",
            "role": "heat_source",
            "height_mm": 1000.0,
            "input_power_w": 1000.0,
            "power_kw": 1.0,
            "convective_fraction": 1.0,
            "radiative_fraction": 0.0,
            "convective_power_w": 1000.0,
            "radiative_power_w": 0.0,
            "evidence": "non_authoritative_working_fixture:manual_heat_1",
            "source_type": "user_confirmed",
            "override_of_dxf": False,
        }, geometry={
            "kind": "polyline", "closed": True,
            "points": [[875.0, 875.0], [1125.0, 875.0],
                       [1125.0, 1125.0], [875.0, 1125.0]],
            "space_id": zone_id,
        }, source_ref={
            "handle": None,
            "handles": [],
            "layer": "USER_CONFIRMED",
            "block_name": "manual_heat_1",
            "entity_type": "UI_INPUT",
            "source_id": "manual_heat_1",
        }),
    ]
    geometry = {
        "schema_version": 2,
        "contract": "geometry.v2",
        "source": "working_validation/working-room-v1",
        "units": "mm",
        "source_units": {
            "millimetres_per_source_unit": 1.0,
            "normalized_length_unit": "mm",
            "assumed": False,
        },
        "coordinate_system": {
            "axis_convention": "XY_Z_UP",
            "origin_mm": [0.0, 0.0, 0.0],
            "rotation_deg": 0.0,
            "millimetres_to_metres": 0.001,
        },
        "levels": [{"id": "level-0", "label": "Working room", "elevation_mm": 0.0}],
        "elements": {
            "wall": [], "column": [], "slab": [],
            "zone": [element(zone_id, "zone", {
                "kind": "space", "boundary": "closed",
                "ceiling_height_mm": 2000.0,
            }, geometry={
                "kind": "polyline", "closed": True,
                "points": [[0.0, 0.0], [2000.0, 0.0],
                           [2000.0, 2000.0], [0.0, 2000.0]],
            })],
            "opening": [], "pipe": [], "duct": [], "tray": [],
            "equipment": equipment,
        },
        "review": {},
    }
    from geometry_v2 import build_review
    geometry["review"] = build_review(geometry)
    return geometry


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _DuplicateJsonKey(ValueError):
    pass


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateJsonKey(key)
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError("non-JSON numeric constant: " + value)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeError, ValueError, _DuplicateJsonKey):
        return None
    return value if isinstance(value, dict) else None


def _is_reparse(path: Path) -> bool:
    try:
        return path.is_symlink() or bool(path.lstat().st_file_attributes & 0x400)
    except (AttributeError, OSError):
        return path.is_symlink()


def _reparse_free_chain(path: Path, root: Path) -> bool:
    try:
        relative = path.absolute().relative_to(root.absolute())
    except ValueError:
        return False
    current = root.absolute()
    if _is_reparse(current):
        return False
    for part in relative.parts:
        current = current / part
        if current.exists() and _is_reparse(current):
            return False
    return True


def _projects_root(value: Path) -> Path | None:
    raw = Path(value).expanduser().absolute()
    try:
        if not raw.is_dir() or any(_is_reparse(path) for path in (raw, *raw.parents)):
            return None
        return raw.resolve(strict=True)
    except (OSError, RuntimeError):
        return None


def _lexical_ref(value: object) -> PurePosixPath | None:
    if not isinstance(value, str) or not value or "\\" in value or "//" in value:
        return None
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        return None
    ref = PurePosixPath(value)
    lowered = [part.casefold() for part in ref.parts]
    if (not ref.parts or any(part in {"", ".", ".."} for part in ref.parts)
            or any(part in _FORBIDDEN_PARTS or part.endswith(".tmp") for part in lowered)):
        return None
    return ref


def _resolve_ref(value: object, root: Path, *, directory: bool = False) -> Path | None:
    ref = _lexical_ref(value)
    if ref is None:
        return None
    raw = root.joinpath(*ref.parts)
    if not _reparse_free_chain(raw, root):
        return None
    try:
        resolved = raw.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None
    if directory and not resolved.is_dir():
        return None
    if not directory and not resolved.is_file():
        return None
    return resolved


def _paths_overlap(first: Path, second: Path) -> bool:
    """Return whether either lexical output/input scope contains the other."""

    try:
        left = Path(first).resolve(strict=False)
        right = Path(second).resolve(strict=False)
    except (OSError, RuntimeError):
        return True
    return left == right or left in right.parents or right in left.parents


def _tree_files(case: Path, root: Path) -> list[Path] | None:
    files: list[Path] = []
    try:
        for path in case.rglob("*"):
            relative = path.relative_to(root)
            lowered = [part.casefold() for part in relative.parts]
            if any(part in _FORBIDDEN_PARTS or part.endswith(".tmp") for part in lowered):
                return None
            if _is_reparse(path):
                return None
            if path.is_file():
                files.append(path)
    except OSError:
        return None
    return sorted(files, key=lambda path: path.relative_to(case).as_posix())


def _tree_sha256(case: Path, files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.relative_to(case).as_posix().encode("utf-8") + b"\0")
        digest.update(bytes.fromhex(_sha256_file(path)))
    return digest.hexdigest()


def _schema_errors(schema_name: str, payload: object) -> list[str]:
    schema = _read_json(Path(__file__).with_name(schema_name))
    if schema is None:
        return ["SCHEMA_UNAVAILABLE"]
    return [error.message for error in Draft202012Validator(schema).iter_errors(payload)]


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _has_physical_equipment_location(element: dict[str, Any]) -> bool:
    """Return whether an equipment footprint locates a non-zero physical area."""
    kind = element.get("kind")
    if kind == "circle":
        centre = element.get("center")
        radius = _finite(element.get("radius"))
        return (isinstance(centre, list) and len(centre) >= 2
                and all(_finite(value) is not None for value in centre[:2])
                and radius is not None and radius > 0)
    if kind != "polyline" or element.get("closed") is not True:
        return False
    points = element.get("points")
    if (not isinstance(points, list) or len(points) < 3
            or any(not isinstance(point, list) or len(point) < 2
                   or _finite(point[0]) is None or _finite(point[1]) is None
                   for point in points)):
        return False
    xy = [(float(point[0]), float(point[1])) for point in points]
    area_twice = abs(sum(
        x1 * y2 - x2 * y1
        for (x1, y1), (x2, y2) in zip(xy, xy[1:] + xy[:1])
    ))
    return area_twice > 0


def _finite_tree(value: object) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite_tree(item) for item in value.values())
    if isinstance(value, list):
        return all(_finite_tree(item) for item in value)
    return True


def _blocked(check_id: str, blockers: list[str], evidence: dict[str, str] | None = None,
             metrics: dict[str, float] | None = None) -> dict[str, Any]:
    ordered = sorted(dict.fromkeys(blockers))
    return {
        "check_id": check_id,
        "status": "BLOCKED" if ordered else "PASS",
        "blockers": ordered,
        "evidence_sha256": dict(sorted((evidence or {}).items())),
        "metrics": dict(sorted((metrics or {}).items())),
    }


def _case_metrics(label: str, record: object, root: Path, evidence: dict[str, str],
                  blockers: list[str]) -> tuple[dict[str, float], str | None]:
    prefix = label.upper()
    if not isinstance(record, dict):
        blockers.append(f"{prefix}_RECORD_INVALID")
        return {}, None
    expected_case = PurePosixPath("_working_validation/working-room-v1") / label
    if _lexical_ref(record.get("case_path")) != expected_case:
        blockers.append(f"{prefix}_CASE_PATH_INVALID")
    case = _resolve_ref(record.get("case_path"), root, directory=True)
    if case is None:
        blockers.append(f"{prefix}_CASE_INVALID")
        return {}, None
    files = _tree_files(case, root)
    if files is None:
        blockers.append(f"{prefix}_CASE_TREE_UNSAFE")
        return {}, None
    for path in files:
        evidence[path.relative_to(root).as_posix()] = _sha256_file(path)
    if not _SHA256.fullmatch(str(record.get("case_tree_sha256") or "")):
        blockers.append(f"{prefix}_CASE_TREE_HASH_INVALID")
    elif _tree_sha256(case, files) != record["case_tree_sha256"]:
        blockers.append(f"{prefix}_CASE_TREE_HASH_MISMATCH")

    artifacts = record.get("artifacts") if isinstance(record.get("artifacts"), dict) else {}
    paths: dict[str, Path] = {}
    for key in _WORKING_ARTIFACTS:
        link = artifacts.get(key)
        path = _resolve_ref(link.get("path"), root) if isinstance(link, dict) else None
        if (path is None or case not in path.parents
                or path.name in {"working_room_acceptance.json", "sgi_screening_acceptance.json"}):
            blockers.append(f"{prefix}_ARTIFACT_REF_INVALID:{key}")
            continue
        paths[key] = path
        actual = _sha256_file(path)
        evidence[path.relative_to(root).as_posix()] = actual
        if not isinstance(link.get("sha256"), str) or actual != link.get("sha256"):
            blockers.append(f"{prefix}_ARTIFACT_HASH_MISMATCH:{key}")

    if set(paths) != set(_WORKING_ARTIFACTS):
        return {}, None

    geometry = _read_json(paths["geometry"])
    surface = _read_json(paths["surface"])
    mesh_input = _read_json(paths["mesh_input"])
    mesh = _read_json(paths["mesh"])
    thermal = _read_json(paths["thermal_input"])
    progress = _read_json(paths["thermal_progress"])
    run = _read_json(paths["run"])
    result = _read_json(paths["result"])
    raw_contracts = (
        ("GEOMETRY", "geometry.v2.schema.json", geometry),
        ("SURFACE", "surface_manifest.v1.schema.json", surface),
        ("MESH", "mesh_manifest.v1.schema.json", mesh),
        ("PROGRESS", "thermal_progress.v1.schema.json", progress),
        ("RUN", "run_manifest.v1.schema.json", run),
        ("RESULT", "result_manifest.v1.schema.json", result),
    )
    structurally_invalid = False
    for kind, schema, payload in raw_contracts:
        if payload is None or _schema_errors(schema, payload):
            blockers.append(f"{prefix}_{kind}_SCHEMA_INVALID")
            structurally_invalid = True
    if structurally_invalid:
        return {}, None

    if not isinstance(mesh_input, dict):
        blockers.append(f"{prefix}_MESH_INPUT_INVALID")
        return {}, None
    if (not isinstance(thermal, dict)
            or not isinstance(thermal.get("settings"), dict)
            or not isinstance(thermal.get("numerics"), dict)
            or not isinstance(thermal.get("terminals"), list)
            or not all(isinstance(row, dict) for row in thermal.get("terminals", []))
            or not isinstance(thermal.get("heat"), dict)):
        blockers.append(f"{prefix}_THERMAL_INPUT_INVALID")
        return {}, None
    terminal_rows = thermal["terminals"]
    if any(
        not isinstance(row.get("role"), str)
        or not isinstance(row.get("mesh_patch_name"), str)
        or not row.get("mesh_patch_name")
        or _finite(row.get("flow_rate_m3_s")) is None
        for row in terminal_rows
    ):
        blockers.append(f"{prefix}_THERMAL_INPUT_INVALID")
        return {}, None

    if geometry != build_working_room_geometry():
        blockers.append(f"{prefix}_CANONICAL_GEOMETRY_INVALID")

    mesh_settings = (
        (mesh_input.get("estimate") or {}).get("settings")
        if isinstance(mesh_input, dict)
        and isinstance(mesh_input.get("estimate"), dict)
        and isinstance((mesh_input.get("estimate") or {}).get("settings"), dict)
        else {}
    )
    if (not isinstance(mesh_input, dict)
            or mesh_input.get("contract") != "mesh_input.v1"
            or mesh_input.get("engine") != "body_fitted_airflow"
            or mesh_settings.get("preset") != "detailed"
            or _finite(mesh_settings.get("background_cell_m")) != 0.125):
        blockers.append(f"{prefix}_MESH_INPUT_INVALID")

    if isinstance(surface, dict):
        air = surface.get("air_volume") if isinstance(surface.get("air_volume"), dict) else {}
        topology = surface.get("topology") if isinstance(surface.get("topology"), dict) else {}
        source = surface.get("source") if isinstance(surface.get("source"), dict) else {}
        if not (air.get("valid") is True and air.get("solid_count") == 1
                and topology.get("watertight") is True
                and topology.get("open_edges") == topology.get("non_manifold_edges") == 0):
            blockers.append(f"{prefix}_AIR_VOLUME_NOT_WATERTIGHT_SINGLE")
        if (source.get("geometry_path") != paths["geometry"].relative_to(root).as_posix()
                or source.get("geometry_sha256") != _sha256_file(paths["geometry"])):
            blockers.append(f"{prefix}_GEOMETRY_SURFACE_BINDING_INVALID")
    if isinstance(mesh, dict):
        mesh_quality = mesh.get("mesh") if isinstance(mesh.get("mesh"), dict) else {}
        strict = mesh.get("strict_diagnostics") if isinstance(mesh.get("strict_diagnostics"), dict) else {}
        if (mesh.get("status") != "PASS" or mesh_quality.get("mesh_ok") is not True
                or mesh_quality.get("fatal") is not False or mesh_quality.get("failed_checks") != []
                or strict.get("mesh_ok") is not True or strict.get("fatal") is not False):
            blockers.append(f"{prefix}_MESH_GATE_FAILED")
        mesh_declared = mesh.get("input") if isinstance(mesh.get("input"), dict) else {}
        if (mesh_declared.get("surface_manifest_sha256") != _sha256_file(paths["surface"])
                or mesh_declared.get("mesh_input_sha256") != _sha256_file(paths["mesh_input"])
                or not isinstance(mesh_input, dict)
                or mesh_input.get("surface_manifest_sha256") != _sha256_file(paths["surface"])):
            blockers.append(f"{prefix}_SURFACE_MESH_BINDING_INVALID")

    check_text = paths["check_mesh_log"].read_text(encoding="utf-8", errors="replace")
    illegal = re.search(r"(?:number\s+of\s+)?illegal\s+cells\s*[:=]\s*(\d+)", check_text, re.I)
    if (not re.search(r"(?m)^\s*Mesh OK\.\s*$", check_text)
            or illegal is None or int(illegal.group(1)) != 0
            or re.search(r"FOAM FATAL|Failed", check_text, re.I)):
        blockers.append(f"{prefix}_CHECK_MESH_INVALID")

    import cfd_physics
    import cfd_post

    solver_text = paths["solver_log"].read_text(encoding="utf-8", errors="replace")
    parsed = cfd_physics.parse_thermal_log(solver_text)
    end_time = _finite(parsed.get("end_time"))
    peak_co = _finite((parsed.get("courant") or {}).get("peak_maximum"))
    if not parsed.get("ended") or parsed.get("fatal") or end_time is None or end_time < 240.0:
        blockers.append(f"{prefix}_SOLVER_LOG_INVALID")
    if peak_co is None or peak_co > 1.0:
        blockers.append(f"{prefix}_COURANT_LIMIT_EXCEEDED")

    if not isinstance(thermal, dict) or thermal.get("contract") != "thermal_input.v1":
        blockers.append(f"{prefix}_THERMAL_INPUT_INVALID")
        return {}, None
    settings = thermal.get("settings") if isinstance(thermal.get("settings"), dict) else {}
    numerics = thermal.get("numerics") if isinstance(thermal.get("numerics"), dict) else {}
    if (settings.get("thermal_adjust_time_step") is not False
            or _finite(settings.get("thermal_delta_t_s")) != 0.02
            or _finite(settings.get("thermal_duration_s")) != 240.0
            or settings.get("thermal_numerics_profile") != "design_limited_second_order_v1"
            or settings.get("thermal_parallel_processes") != 1):
        blockers.append(f"{prefix}_TIME_CONTROL_INVALID")
    terminals = terminal_rows
    terminal_signature = sorted(
        (row.get("role"), _finite(row.get("flow_rate_m3_s")))
        for row in terminals if isinstance(row, dict)
    )
    if (numerics.get("profile") != "design_limited_second_order_v1"
            or numerics.get("convection_order") != 2
            or terminal_signature != [("exhaust", 0.1), ("supply", 0.1)]
            or _finite((thermal.get("heat") or {}).get("applied_convective_power_w")) != 1000.0):
        blockers.append(f"{prefix}_NUMERICS_INVALID")
    if (not isinstance(mesh, dict) or thermal.get("mesh_manifest_sha256") != _sha256_file(paths["mesh"])):
        blockers.append(f"{prefix}_THERMAL_MESH_BINDING_INVALID")
    if (not isinstance(run, dict) or (run.get("input") or {}).get("thermal_input_sha256") != _sha256_file(paths["thermal_input"])
            or run.get("thermal_progress") != progress):
        blockers.append(f"{prefix}_RUN_BINDING_INVALID")

    control_text = paths["control_dict"].read_text(encoding="utf-8", errors="replace")
    schemes_text = paths["fv_schemes"].read_text(encoding="utf-8", errors="replace")
    turbulence_text = paths["turbulence_properties"].read_text(encoding="utf-8", errors="replace")
    allrun_text = paths["allrun"].read_text(encoding="utf-8", errors="replace")
    if (not re.search(r"\bapplication\s+buoyantBoussinesqPimpleFoam\s*;", control_text)
            or not re.search(r"\badjustTimeStep\s+no\s*;", control_text)
            or not re.search(r"\bdeltaT\s+0\.0*2\s*;", control_text)
            or not re.search(r"\bendTime\s+240(?:\.0+)?\s*;", control_text)
            or "linearUpwind" not in schemes_text
            or not re.search(r"\bRASModel\s+kOmegaSST\s*;", turbulence_text)
            or "mpirun" in allrun_text or "-parallel" in allrun_text
            or not re.search(r"(?m)^\s*buoyantBoussinesqPimpleFoam\b", allrun_text)):
        blockers.append(f"{prefix}_OPENFOAM_NUMERICS_INVALID")
    provenance = (
        (run.get("input") or {}).get("numerical_provenance")
        if isinstance(run, dict) and isinstance(run.get("input"), dict)
        and isinstance((run.get("input") or {}).get("numerical_provenance"), dict)
        else {}
    )
    effective_settings = run.get("effective_settings") if isinstance(run, dict) else None
    effective_numerics = run.get("effective_numerics") if isinstance(run, dict) else None
    actual_system = {
        "controlDict": _sha256_file(paths["control_dict"]),
        "fvSchemes": _sha256_file(paths["fv_schemes"]),
        "fvSolution": _sha256_file(paths["fv_solution"]),
    }
    if (effective_settings != settings or effective_numerics != numerics
            or provenance.get("contract") != "thermal_numerics_provenance.v1"
            or provenance.get("source") != "thermal_initial_input"
            or provenance.get("thermal_input_sha256") != _sha256_file(paths["thermal_input"])
            or provenance.get("thermal_restart_input_sha256") is not None
            or provenance.get("effective_settings_sha256") != _canonical_json_sha256(settings)
            or provenance.get("effective_numerics_sha256") != _canonical_json_sha256(numerics)
            or provenance.get("system") != actual_system
            or provenance.get("expected_system") != actual_system):
        blockers.append(f"{prefix}_NUMERICAL_PROVENANCE_INVALID")

    physical_times = [end_time, _finite((progress or {}).get("latest_time_s")), _finite((result or {}).get("time_s"))]
    try:
        field_time = float(paths["field_t"].parent.name)
    except ValueError:
        field_time = None
    physical_times.append(field_time)
    if any(value is None or value < 240.0 for value in physical_times) or len({round(value, 9) for value in physical_times if value is not None}) != 1:
        blockers.append(f"{prefix}_PHYSICAL_TIME_INVALID")

    temperatures = cfd_physics._internal_scalar_values(paths["field_t"])
    velocities = cfd_physics._internal_vector_values(paths["field_u"])
    volumes = cfd_physics._internal_scalar_values(paths["field_v"])
    if (not temperatures or len(temperatures) != len(velocities) or len(volumes) != len(temperatures)
            or any(not math.isfinite(value) for value in temperatures + volumes)
            or any(not math.isfinite(component) for row in velocities for component in row)
            or any(value <= 0 for value in volumes)):
        blockers.append(f"{prefix}_RESULT_FIELDS_INVALID")

    flux = cfd_physics.terminal_flux_balance(paths["field_phi"].parent, thermal.get("terminals") or [])
    imbalance = _finite(flux.get("imbalance_ratio"))
    if not flux.get("available") or imbalance is None or imbalance > 0.001:
        blockers.append(f"{prefix}_TERMINAL_PHI_IMBALANCE")

    try:
        solved_power = cfd_physics._solved_exhaust_power(
            case, paths["field_t"].parent, thermal,
        )
    except (KeyError, OSError, TypeError, ValueError):
        solved_power = None
    applied_power = _finite(thermal["heat"].get("applied_convective_power_w"))
    closure = (solved_power / applied_power
               if solved_power is not None and applied_power is not None and applied_power > 0
               else None)
    if solved_power is None:
        blockers.append(f"{prefix}_ENERGY_CLOSURE_BASIS_UNTRUSTED")
    if closure is None or not 0.95 <= closure <= 1.05:
        blockers.append(f"{prefix}_ENERGY_CLOSURE_INVALID")

    try:
        vtu = cfd_post.read_internal_vtu(paths["vtu"])
        vtu_t = vtu["temperature_k"]
        vtu_u = vtu["velocity_m_s"]
        vtu_v = vtu.get("volume_m3")
        if (not vtu_t or not isinstance(vtu_v, list) or len(vtu_t) != len(vtu_u) or len(vtu_v) != len(vtu_t)
                or any(not math.isfinite(value) or value <= 0 for value in vtu_v)
                or any(not math.isfinite(value) for value in vtu_t)
                or any(not math.isfinite(component) for row in vtu_u for component in row)):
            raise ValueError
        total_v = sum(vtu_v)
        mean_t = sum(value * volume for value, volume in zip(vtu_t, vtu_v)) / total_v
        speeds = [math.sqrt(sum(component * component for component in row)) for row in vtu_u]
        mean_speed = sum(value * volume for value, volume in zip(speeds, vtu_v)) / total_v
    except (OSError, KeyError, TypeError, ValueError):
        blockers.append(f"{prefix}_VTU_FIELDS_INVALID")
        return {}, str((run or {}).get("execution_id") or "")

    summary = _read_json(paths["summary"])
    summary_temperature = (
        summary.get("temperature")
        if isinstance(summary, dict) and isinstance(summary.get("temperature"), dict)
        else None
    )
    summary_velocity = (
        summary.get("velocity")
        if isinstance(summary, dict) and isinstance(summary.get("velocity"), dict)
        else None
    )
    if (not isinstance(summary, dict) or not _finite_tree(summary)
            or summary_temperature is None or summary_velocity is None):
        blockers.append(f"{prefix}_SUMMARY_INVALID")
    elif (_finite(summary_temperature.get("mean")) != mean_t
            or _finite(summary_velocity.get("mean_speed")) != mean_speed):
        blockers.append(f"{prefix}_SUMMARY_MISMATCH")
    for axis in "xyz":
        item = _read_json(paths[f"slice_{axis}"])
        if (not isinstance(item, dict) or item.get("axis") != axis
                or not isinstance(item.get("sample_count"), int)
                or isinstance(item.get("sample_count"), bool)
                or item.get("sample_count") < 1 or not _finite_tree(item)):
            blockers.append(f"{prefix}_SLICE_INVALID:{axis}")
    report_text = paths["report"].read_text(encoding="utf-8", errors="replace")
    if not report_text.strip() or "DESIGN_CITABLE" in report_text:
        blockers.append(f"{prefix}_REPORT_INVALID")

    if isinstance(result, dict):
        expected_source = paths["vtu"].relative_to(case).as_posix()
        expected_summary = paths["summary"].relative_to(case).as_posix()
        slices = result.get("slices") if isinstance(result.get("slices"), list) else []
        if ((result.get("source") or {}).get("path") != expected_source
                or (result.get("source") or {}).get("sha256") != _sha256_file(paths["vtu"])
                or result.get("summary_path") != expected_summary
                or result.get("summary_sha256") != _sha256_file(paths["summary"])
                or result.get("run_manifest_sha256") != _sha256_file(paths["run"])
                or result.get("mesh_manifest_sha256") != _sha256_file(paths["mesh"])
                or result.get("thermal_input_sha256") != _sha256_file(paths["thermal_input"])
                or {row.get("axis") for row in slices if isinstance(row, dict)} != {"x", "y", "z"}):
            blockers.append(f"{prefix}_RESULT_BINDING_INVALID")

    metrics = {
        "energy_closure_ratio": round(float(closure), 12) if closure is not None else math.nan,
        "mean_speed_m_s": round(mean_speed, 12),
        "mean_temperature_k": round(mean_t, 12),
        "_input_fingerprint": {
            "geometry_sha256": _sha256_file(paths["geometry"]),
            **_normalized_working_input_hashes(surface, mesh_input, thermal),
            "numerical_files_sha256": _canonical_json_sha256({
                **actual_system,
                "turbulenceProperties": _sha256_file(paths["turbulence_properties"]),
                "Allrun": _sha256_file(paths["allrun"]),
            }),
        },
        "_case_file_set": tuple(path.relative_to(case).as_posix() for path in files),
    }
    return metrics, str((run or {}).get("execution_id") or "")


def validate_working_room(
    manifest_path: Path,
    projects_root: Path,
    evaluator_output_path: Path | None = None,
) -> dict[str, Any]:
    """Recompute working-room anchor/repeat acceptance from current raw files."""

    blockers: list[str] = []
    evidence: dict[str, str] = {}
    root = _projects_root(Path(projects_root))
    if root is None:
        return _blocked("working_room_e2e", ["PROJECTS_ROOT_INVALID"])
    supplied = Path(manifest_path).expanduser()
    if not supplied.is_absolute():
        supplied = root / supplied
    expected = root.joinpath(*_WORKING_ROOM_RELATIVE.parts)
    try:
        if supplied.resolve(strict=False) != expected.resolve(strict=False):
            return _blocked("working_room_e2e", ["WORKING_ROOM_MANIFEST_PATH_INVALID"])
    except (OSError, RuntimeError):
        return _blocked("working_room_e2e", ["WORKING_ROOM_MANIFEST_PATH_INVALID"])
    manifest = _resolve_ref(_WORKING_ROOM_RELATIVE.as_posix(), root)
    if manifest is None:
        return _blocked("working_room_e2e", ["WORKING_ROOM_MANIFEST_MISSING"])
    if evaluator_output_path is not None:
        output = Path(evaluator_output_path).expanduser()
        if not output.is_absolute():
            output = root / output
        try:
            if (_paths_overlap(output, manifest)
                    or any(_paths_overlap(
                        output, root.joinpath(*(_WORKING_ROOM_RELATIVE.parent / label).parts)
                    ) for label in ("anchor", "repeat"))):
                blockers.append("EVALUATOR_OUTPUT_ALIASES_INPUT")
        except (OSError, RuntimeError):
            blockers.append("EVALUATOR_OUTPUT_INVALID")
    payload = _read_json(manifest)
    evidence[manifest.relative_to(root).as_posix()] = _sha256_file(manifest)
    if payload is None:
        return _blocked("working_room_e2e", blockers + ["WORKING_ROOM_MANIFEST_MALFORMED"], evidence)
    if _schema_errors("working_room_acceptance.v1.schema.json", payload):
        blockers.append("WORKING_ROOM_MANIFEST_SCHEMA_INVALID")
        return _blocked("working_room_e2e", blockers, evidence)

    anchor, anchor_id = _case_metrics("anchor", payload.get("anchor"), root, evidence, blockers)
    repeat, repeat_id = _case_metrics("repeat", payload.get("repeat"), root, evidence, blockers)
    anchor_fingerprint = anchor.pop("_input_fingerprint", None)
    repeat_fingerprint = repeat.pop("_input_fingerprint", None)
    initial_case_sets = {
        "anchor": anchor.pop("_case_file_set", None),
        "repeat": repeat.pop("_case_file_set", None),
    }
    metrics: dict[str, float] = {}
    if anchor and repeat:
        metrics = {
            "anchor_energy_closure_ratio": anchor["energy_closure_ratio"],
            "anchor_mean_speed_m_s": anchor["mean_speed_m_s"],
            "anchor_mean_temperature_k": anchor["mean_temperature_k"],
            "energy_closure_delta_percentage_points": round(abs(anchor["energy_closure_ratio"] - repeat["energy_closure_ratio"]) * 100.0, 12),
            "mean_speed_delta_m_s": round(abs(anchor["mean_speed_m_s"] - repeat["mean_speed_m_s"]), 12),
            "mean_temperature_delta_k": round(abs(anchor["mean_temperature_k"] - repeat["mean_temperature_k"]), 12),
            "repeat_energy_closure_ratio": repeat["energy_closure_ratio"],
            "repeat_mean_speed_m_s": repeat["mean_speed_m_s"],
            "repeat_mean_temperature_k": repeat["mean_temperature_k"],
        }
        if metrics["mean_temperature_delta_k"] > 0.02:
            blockers.append("REPEAT_MEAN_TEMPERATURE_DELTA_EXCEEDED")
        if metrics["mean_speed_delta_m_s"] > 0.005:
            blockers.append("REPEAT_MEAN_SPEED_DELTA_EXCEEDED")
        if metrics["energy_closure_delta_percentage_points"] > 0.5:
            blockers.append("REPEAT_ENERGY_CLOSURE_DELTA_EXCEEDED")
    if (not isinstance(anchor_fingerprint, dict)
            or not isinstance(repeat_fingerprint, dict)
            or anchor_fingerprint != repeat_fingerprint):
        blockers.append("WORKING_ROOM_INPUT_FINGERPRINT_MISMATCH")
    if not anchor_id or not repeat_id or anchor_id == repeat_id:
        blockers.append("RUN_EXECUTION_ID_NOT_INDEPENDENT")

    if evaluator_output_path is not None:
        output = Path(evaluator_output_path).expanduser()
        if not output.is_absolute():
            output = root / output
        try:
            output = output.resolve(strict=False)
            if any(root.joinpath(*PurePosixPath(relative).parts).resolve(strict=False) == output
                   for relative in evidence):
                blockers.append("EVALUATOR_OUTPUT_ALIASES_INPUT")
        except (OSError, RuntimeError):
            blockers.append("EVALUATOR_OUTPUT_INVALID")

    # Detect a mutation that occurs after any artifact was consumed.
    for relative, prior in list(evidence.items()):
        path = root.joinpath(*PurePosixPath(relative).parts)
        try:
            if _sha256_file(path) != prior:
                blockers.append("POST_LOAD_MUTATION:" + relative)
        except OSError:
            blockers.append("POST_LOAD_MUTATION:" + relative)
    for label, initial in initial_case_sets.items():
        case = root.joinpath(*(_WORKING_ROOM_RELATIVE.parent / label).parts)
        final_files = _tree_files(case, root)
        final = (tuple(path.relative_to(case).as_posix() for path in final_files)
                 if final_files is not None else None)
        if final != initial:
            blockers.append("POST_LOAD_CASE_TREE_CHANGED:" + label)
    return _blocked("working_room_e2e", blockers, evidence, metrics)


evaluate_working_room_acceptance = validate_working_room


def adapt_field_pipeline_job_for_acceptance(
    field_job_path: Path,
    projects_root: Path,
) -> dict[str, Any]:
    """Normalize the current field-job producer into relative acceptance refs.

    ``field_pipeline_job.create_job`` currently persists absolute input paths
    and does not yet emit the immutable restart snapshots owned by Task 5c.
    This pure seam accepts either current absolute paths or future relative
    paths, proves that each existing input remains under ``projects_root``, and
    fails closed until the producer-owned restart/resource records exist.
    """

    blockers: list[str] = []
    refs: dict[str, dict[str, str]] = {}
    root = _projects_root(Path(projects_root))
    if root is None:
        return {
            "contract": "field_pipeline_acceptance_adapter.v1", "status": "BLOCKED",
            "blockers": ["PROJECTS_ROOT_INVALID"], "relative_refs": {},
        }
    supplied = Path(field_job_path).expanduser()
    if not supplied.is_absolute():
        supplied = root / supplied
    try:
        job_path = supplied.resolve(strict=True)
        job_path.relative_to(root / "_field_jobs")
    except (OSError, RuntimeError, ValueError):
        job_path = None
    if (job_path is None or job_path.name != "field_pipeline_job.json"
            or not re.fullmatch(r"field-[0-9a-f]{12}", job_path.parent.name)
            or not _reparse_free_chain(job_path, root)):
        blockers.append("FIELD_PIPELINE_JOB_REF_INVALID")
        job = None
    else:
        refs["field_job"] = {
            "path": job_path.relative_to(root).as_posix(), "sha256": _sha256_file(job_path),
        }
        job = _read_json(job_path)
        if not isinstance(job, dict) or _schema_errors("field_pipeline_job.v1.schema.json", job):
            blockers.append("FIELD_PIPELINE_JOB_SCHEMA_INVALID")

    def normalize_input(value: object, expected_suffix: str) -> Path | None:
        if not isinstance(value, str) or not value:
            return None
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            ref = _lexical_ref(value)
            candidate = root.joinpath(*ref.parts) if ref is not None else Path()
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            return None
        if (not resolved.is_file() or resolved.suffix.casefold() != expected_suffix
                or not _reparse_free_chain(resolved, root)):
            return None
        return resolved

    if isinstance(job, dict):
        job_input = job.get("input") if isinstance(job.get("input"), dict) else {}
        for output_key, input_key, suffix in (
            ("reviewed_geometry", "geometry_path", ".json"),
            ("source_dxf", "source_dxf_path", ".dxf"),
        ):
            path = normalize_input(job_input.get(input_key), suffix)
            if path is None or job_input.get(input_key.replace("path", "sha256")) != _sha256_file(path):
                blockers.append("FIELD_PIPELINE_INPUT_INVALID:" + output_key)
                continue
            refs[output_key] = {
                "path": path.relative_to(root).as_posix(), "sha256": _sha256_file(path),
            }
        case_value = job.get("result_case")
        case = None
        if isinstance(case_value, str) and case_value:
            candidate = Path(case_value)
            if not candidate.is_absolute():
                ref = _lexical_ref(case_value)
                candidate = root.joinpath(*ref.parts) if ref is not None else Path()
            try:
                resolved = candidate.resolve(strict=True)
                resolved.relative_to(root / "_body_solver")
                if resolved.is_dir() and _reparse_free_chain(resolved, root):
                    case = resolved
            except (OSError, RuntimeError, ValueError):
                pass
        if case is None:
            blockers.append("FIELD_PIPELINE_FINAL_CASE_NOT_PRODUCED")

    # Task 5a is deliberately read-only with respect to field_pipeline_job.py.
    # Individual filenames are not a producer contract and must never promote
    # the adapter. Task 5c must atomically produce the complete closed manifest,
    # including all three checkpoint fields and immutable attempt evidence.
    blockers.append("FIELD_PIPELINE_RESTART_EVIDENCE_NOT_PRODUCED")
    blockers.append("FIELD_PIPELINE_ACCEPTANCE_MANIFEST_NOT_PRODUCED")
    ordered = sorted(dict.fromkeys(blockers))
    return {
        "contract": "field_pipeline_acceptance_adapter.v1",
        "status": "BLOCKED" if ordered else "PASS",
        "blockers": ordered,
        "relative_refs": dict(sorted(refs.items())),
    }


def _fixed_manifest_context(manifest_path: Path, projects_root: Path, fixed: PurePosixPath,
                            evaluator_output_path: Path | None, check_id: str,
                            missing_code: str) -> tuple[Path | None, Path | None, dict[str, Any] | None,
                                                        dict[str, str], list[str]]:
    blockers: list[str] = []
    evidence: dict[str, str] = {}
    root = _projects_root(Path(projects_root))
    if root is None:
        return None, None, None, evidence, ["PROJECTS_ROOT_INVALID"]
    supplied = Path(manifest_path).expanduser()
    if not supplied.is_absolute():
        supplied = root / supplied
    expected = root.joinpath(*fixed.parts)
    try:
        if supplied.resolve(strict=False) != expected.resolve(strict=False):
            return root, None, None, evidence, [check_id.upper() + "_MANIFEST_PATH_INVALID"]
    except (OSError, RuntimeError):
        return root, None, None, evidence, [check_id.upper() + "_MANIFEST_PATH_INVALID"]
    manifest = _resolve_ref(fixed.as_posix(), root)
    if manifest is None:
        return root, None, None, evidence, [missing_code]
    if evaluator_output_path is not None:
        output = Path(evaluator_output_path).expanduser()
        if not output.is_absolute():
            output = root / output
        try:
            if _paths_overlap(output, manifest):
                blockers.append("EVALUATOR_OUTPUT_ALIASES_INPUT")
        except (OSError, RuntimeError):
            blockers.append("EVALUATOR_OUTPUT_INVALID")
    evidence[manifest.relative_to(root).as_posix()] = _sha256_file(manifest)
    return root, manifest, _read_json(manifest), evidence, blockers


def _sgi_context(manifest_path: Path, projects_root: Path,
                 evaluator_output_path: Path | None = None) -> dict[str, Any]:
    root, manifest, payload, evidence, blockers = _fixed_manifest_context(
        manifest_path, projects_root, _SGI_RELATIVE, evaluator_output_path,
        "sgi_screening", "SGI_SCREENING_MANIFEST_MISSING",
    )
    context: dict[str, Any] = {
        "root": root, "manifest": manifest, "payload": payload, "evidence": evidence,
        "blockers": blockers, "paths": {}, "case": None, "output": evaluator_output_path,
        "case_file_set": None,
    }
    if root is None or manifest is None:
        return context
    if payload is None:
        blockers.append("SGI_SCREENING_MANIFEST_MALFORMED")
        return context
    if _schema_errors("sgi_screening_acceptance.v1.schema.json", payload):
        blockers.append("SGI_SCREENING_MANIFEST_SCHEMA_INVALID")
        return context

    output_resolved = None
    if evaluator_output_path is not None:
        output = Path(evaluator_output_path).expanduser()
        if not output.is_absolute():
            output = root / output
        try:
            output_resolved = output.resolve(strict=False)
        except (OSError, RuntimeError):
            pass

    for key, namespace, suffix in (
        ("field_job", "_field_jobs", ".json"),
        ("source_dxf", "_imports", ".dxf"),
        ("reviewed_geometry", None, ".json"),
        ("resource_preflight", "_field_jobs", ".json"),
    ):
        link = payload.get(key)
        path = _resolve_ref(link.get("path"), root) if isinstance(link, dict) else None
        valid_namespace = path is not None and path.suffix.casefold() == suffix
        if valid_namespace and namespace is not None:
            try:
                path.relative_to(root / namespace)
            except ValueError:
                valid_namespace = False
        if key == "field_job" and (path is None or path.name != "field_pipeline_job.json"
                                    or not re.fullmatch(r"field-[0-9a-f]{12}", path.parent.name)):
            valid_namespace = False
        if not valid_namespace:
            blockers.append("SGI_POINTER_REF_INVALID:" + key)
            continue
        assert path is not None
        context["paths"][key] = path
        actual = _sha256_file(path)
        evidence[path.relative_to(root).as_posix()] = actual
        if actual != link.get("sha256"):
            blockers.append("SGI_POINTER_HASH_MISMATCH:" + key)
        if output_resolved is not None and _paths_overlap(path, output_resolved):
            blockers.append("EVALUATOR_OUTPUT_ALIASES_INPUT")

    field_job_path = context["paths"].get("field_job")
    resource_path = context["paths"].get("resource_preflight")
    if (field_job_path is not None and output_resolved is not None
            and _paths_overlap(field_job_path.parent, output_resolved)):
        blockers.append("EVALUATOR_OUTPUT_ALIASES_INPUT")
    if (field_job_path is not None and resource_path is not None
            and (resource_path.parent != field_job_path.parent / "acceptance"
                 or resource_path.name != "resource_preflight.json")):
        blockers.append("SGI_RESOURCE_PREFLIGHT_PATH_INVALID")

    restart = payload.get("restart_evidence") if isinstance(payload.get("restart_evidence"), dict) else {}
    for key in _RESTART_EVIDENCE_KEYS:
        link = restart.get(key)
        path = _resolve_ref(link.get("path"), root) if isinstance(link, dict) else None
        if (path is None or path.suffix.casefold() not in {".json", ".log", ""}
                or field_job_path is None):
            blockers.append("RESTART_EVIDENCE_REF_INVALID:" + key)
            continue
        try:
            path.relative_to(field_job_path.parent / "acceptance")
        except ValueError:
            # Checkpoint fields remain in the immutable solver case and are
            # constrained after the case pointer has been resolved below.
            if key not in {"checkpoint_field_t", "checkpoint_field_u", "checkpoint_field_phi"}:
                blockers.append("RESTART_EVIDENCE_REF_INVALID:" + key)
                continue
        context["paths"]["restart_" + key] = path
        actual = _sha256_file(path)
        evidence[path.relative_to(root).as_posix()] = actual
        if actual != link.get("sha256"):
            blockers.append("RESTART_EVIDENCE_HASH_MISMATCH:" + key)
        if output_resolved is not None and _paths_overlap(path, output_resolved):
            blockers.append("EVALUATOR_OUTPUT_ALIASES_INPUT")

    record = payload.get("solver_case") if isinstance(payload.get("solver_case"), dict) else {}
    case_ref = _lexical_ref(record.get("case_path"))
    case = _resolve_ref(record.get("case_path"), root, directory=True)
    if (case_ref is None or len(case_ref.parts) < 2 or case_ref.parts[0] != "_body_solver"
            or case is None or case == root / "_body_solver"):
        blockers.append("SGI_CASE_PATH_INVALID")
        return context
    context["case"] = case
    if output_resolved is not None and _paths_overlap(case, output_resolved):
        blockers.append("EVALUATOR_OUTPUT_ALIASES_INPUT")
    files = _tree_files(case, root)
    if files is None:
        blockers.append("SGI_CASE_TREE_UNSAFE")
        return context
    for path in files:
        evidence[path.relative_to(root).as_posix()] = _sha256_file(path)
        if output_resolved is not None and _paths_overlap(path, output_resolved):
            blockers.append("EVALUATOR_OUTPUT_ALIASES_INPUT")
    context["case_file_set"] = frozenset(path.relative_to(case).as_posix() for path in files)
    if _tree_sha256(case, files) != record.get("case_tree_sha256"):
        blockers.append("SGI_CASE_TREE_HASH_MISMATCH")
    artifacts = record.get("artifacts") if isinstance(record.get("artifacts"), dict) else {}
    for key in _SGI_CASE_ARTIFACTS:
        link = artifacts.get(key)
        path = _resolve_ref(link.get("path"), root) if isinstance(link, dict) else None
        if path is None or (key != "geometry" and case not in path.parents):
            blockers.append("SGI_ARTIFACT_REF_INVALID:" + key)
            continue
        context["paths"][key] = path
        actual = _sha256_file(path)
        evidence[path.relative_to(root).as_posix()] = actual
        if actual != link.get("sha256"):
            blockers.append("SGI_ARTIFACT_HASH_MISMATCH:" + key)
        if output_resolved is not None and _paths_overlap(path, output_resolved):
            blockers.append("EVALUATOR_OUTPUT_ALIASES_INPUT")
    if (context["paths"].get("reviewed_geometry") is not None
            and context["paths"].get("geometry") is not None
            and context["paths"]["reviewed_geometry"] != context["paths"]["geometry"]):
        blockers.append("SGI_REVIEWED_GEOMETRY_NOT_CURRENT_CASE")
    for key in ("checkpoint_field_t", "checkpoint_field_u", "checkpoint_field_phi"):
        path = context["paths"].get("restart_" + key)
        if path is None or case not in path.parents:
            blockers.append("RESTART_CHECKPOINT_FIELD_CASE_INVALID:" + key)
    return context


def _sgi_terminal_rows(geometry: dict[str, Any] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(geometry, dict):
        return [], []
    elements = geometry.get("elements") if isinstance(geometry.get("elements"), dict) else {}
    rows = [item for group in elements.values() if isinstance(group, list)
            for item in group if isinstance(item, dict)]
    supplies, exhausts = [], []
    for item in rows:
        semantic = item.get("semantic") if isinstance(item.get("semantic"), dict) else {}
        if semantic.get("role") == "supply":
            supplies.append(item)
        elif semantic.get("role") == "exhaust":
            exhausts.append(item)
    return supplies, exhausts


def _sgi_field_metrics(case: Path, paths: dict[str, Path], thermal: dict[str, Any],
                       blockers: list[str]) -> dict[str, float]:
    import cfd_physics
    import cfd_post

    metrics: dict[str, float] = {}
    solver_text = paths["solver_log"].read_text(encoding="utf-8", errors="replace")
    parsed = cfd_physics.parse_thermal_log(solver_text)
    peak_co = _finite((parsed.get("courant") or {}).get("peak_maximum"))
    global_continuity = _finite((parsed.get("continuity") or {}).get("global"))
    if not parsed.get("ended") or parsed.get("fatal"):
        blockers.append("SGI_SOLVER_LOG_INVALID")
    if peak_co is None or peak_co > 1.0:
        blockers.append("SGI_COURANT_LIMIT_EXCEEDED")
    if global_continuity is None or abs(global_continuity) > 0.000001:
        blockers.append("SGI_GLOBAL_CONTINUITY_EXCEEDED")
    if peak_co is not None:
        metrics["peak_courant"] = round(peak_co, 12)
    if global_continuity is not None:
        metrics["global_continuity"] = round(global_continuity, 12)

    try:
        final_time = float(paths["field_t"].parent.name)
    except (TypeError, ValueError):
        final_time = None
    field_parents = {
        paths[key].parent for key in ("field_t", "field_u", "field_phi", "field_v")
    }
    if len(field_parents) != 1 or final_time is None:
        blockers.append("SGI_FINAL_TIME_AMBIGUOUS")

    temperatures = cfd_physics._internal_scalar_values(paths["field_t"])
    velocities = cfd_physics._internal_vector_values(paths["field_u"])
    volumes = cfd_physics._internal_scalar_values(paths["field_v"])
    if (not temperatures or len(temperatures) != len(velocities)
            or len(temperatures) != len(volumes)
            or any(not math.isfinite(value) for value in temperatures + volumes)
            or any(value <= 0 for value in volumes)
            or any(not math.isfinite(component) for vector in velocities for component in vector)):
        blockers.append("SGI_RESULT_FIELDS_INVALID")

    flux = cfd_physics.terminal_flux_balance(paths["field_phi"].parent, thermal.get("terminals") or [])
    imbalance = _finite(flux.get("imbalance_ratio"))
    if not flux.get("available") or imbalance is None or imbalance > 0.001:
        blockers.append("SGI_TERMINAL_PHI_IMBALANCE")
    elif imbalance is not None:
        metrics["terminal_phi_imbalance_ratio"] = round(imbalance, 12)

    try:
        solved_power = (
            cfd_physics._solved_exhaust_power(case, paths["field_t"].parent, thermal)
            if final_time is not None else None
        )
    except (KeyError, OSError, TypeError, ValueError):
        solved_power = None
    applied_power = _finite((thermal.get("heat") or {}).get("applied_convective_power_w"))
    closure = (solved_power / applied_power
               if solved_power is not None and applied_power is not None and applied_power > 0
               else None)
    if solved_power is None:
        blockers.append("SGI_ENERGY_CLOSURE_BASIS_UNTRUSTED")
    if closure is None or not 0.95 <= closure <= 1.05:
        blockers.append("SGI_ENERGY_CLOSURE_INVALID")
    else:
        metrics["energy_closure_ratio"] = round(closure, 12)
    try:
        vtu = cfd_post.read_internal_vtu(paths["vtu"])
        values = list(vtu.get("temperature_k") or [])
        vectors = list(vtu.get("velocity_m_s") or [])
        volumes = list(vtu.get("volume_m3") or [])
        if (not values or len(values) != len(vectors) or len(values) != len(volumes)
                or any(not math.isfinite(value) for value in values + volumes)
                or any(value <= 0 for value in volumes)
                or any(not math.isfinite(component) for row in vectors for component in row)):
            raise ValueError
    except (OSError, TypeError, ValueError, KeyError):
        blockers.append("SGI_VTU_FIELDS_INVALID")
    else:
        total_volume = sum(volumes)
        mean_temperature = sum(value * volume for value, volume in zip(values, volumes)) / total_volume
        speeds = [math.sqrt(sum(component * component for component in row)) for row in vectors]
        mean_speed = sum(value * volume for value, volume in zip(speeds, volumes)) / total_volume
        summary = _read_json(paths["summary"])
        summary_temperature = (
            summary.get("temperature")
            if isinstance(summary, dict) and isinstance(summary.get("temperature"), dict)
            else {}
        )
        summary_velocity = (
            summary.get("velocity")
            if isinstance(summary, dict) and isinstance(summary.get("velocity"), dict)
            else {}
        )
        if (not isinstance(summary, dict) or not _finite_tree(summary)
                or _finite(summary.get("time_s")) != final_time
                or _finite(summary_temperature.get("mean")) != mean_temperature
                or _finite(summary_velocity.get("mean_speed")) != mean_speed):
            blockers.append("SGI_SUMMARY_MISMATCH")
        metrics["mean_temperature_k"] = round(mean_temperature, 12)
        metrics["mean_speed_m_s"] = round(mean_speed, 12)
    return metrics


def _sgi_opening_metrics(
    case: Path,
    paths: dict[str, Path],
    expected_terminals: dict[str, dict[str, object]],
    blockers: list[str],
) -> dict[str, float]:
    """Recompute terminal area and solved flow from explicit mesh/phi inputs."""

    import cfd_mesh
    import cfd_physics

    case_meta = _read_json(paths["case_meta"])
    saved = _read_json(paths["opening_verification"])
    preflight = (
        case_meta.get("opening_preflight")
        if isinstance(case_meta, dict) and isinstance(case_meta.get("opening_preflight"), dict)
        else {}
    )
    rows = preflight.get("terminals") if isinstance(preflight.get("terminals"), list) else []
    if preflight.get("contract") != "opening_preflight.v2" or not rows:
        blockers.append("SGI_OPENING_PREFLIGHT_INVALID")
        return {}
    try:
        patch_metrics = cfd_mesh.patch_metrics(case / "constant" / "polyMesh")
        phi = cfd_physics._boundary_scalar_values(paths["field_phi"])
    except (OSError, TypeError, ValueError):
        blockers.append("SGI_OPENING_RAW_EVIDENCE_INVALID")
        return {}

    opening_ids: set[str] = set()
    maximum_area_error = 0.0
    maximum_flow_error = 0.0
    computed: dict[str, tuple[float, float]] = {}
    for row in rows:
        if not isinstance(row, dict):
            blockers.append("SGI_OPENING_PREFLIGHT_INVALID")
            return {}
        opening_id = row.get("opening_id")
        children = row.get("child_patch_names")
        predicted = _finite(row.get("snapped_area_m2"))
        design_cmh = _finite(row.get("design_cmh"))
        role = row.get("role")
        expected = expected_terminals.get(str(opening_id))
        if (not isinstance(opening_id, str) or not opening_id
                or opening_id in opening_ids or role not in {"supply", "exhaust"}
                or not isinstance(children, list) or not children
                or any(not isinstance(child, str) or not child for child in children)
                or len(set(children)) != len(children)
                or predicted is None or predicted <= 0
                or design_cmh is None or design_cmh <= 0):
            blockers.append("SGI_OPENING_PREFLIGHT_INVALID")
            return {}
        if (not isinstance(expected, dict)
                or expected.get("role") != role
                or expected.get("parent_name") != row.get("parent_name")
                or _finite(expected.get("airflow_cmh")) != design_cmh
                or design_cmh != 444.0):
            blockers.append("SGI_OPENING_TERMINAL_BINDING_INVALID")
            return {}
        opening_ids.add(opening_id)
        try:
            actual_area = sum(float(patch_metrics[name]["area_m2"]) for name in children)
            signed_m3_s = sum(sum(float(value) for value in phi[name]) for name in children)
        except (KeyError, TypeError, ValueError):
            blockers.append("SGI_OPENING_RAW_EVIDENCE_INVALID")
            return {}
        area_ratio = actual_area / predicted
        flow_ratio = abs(signed_m3_s) * 3600.0 / design_cmh
        expected_sign = -1.0 if role == "supply" else 1.0
        if signed_m3_s * expected_sign <= 0:
            blockers.append("SGI_TERMINAL_FLOW_DIRECTION_INVALID")
        maximum_area_error = max(maximum_area_error, abs(area_ratio - 1.0))
        maximum_flow_error = max(maximum_flow_error, abs(flow_ratio - 1.0))
        computed[opening_id] = (area_ratio, flow_ratio)

    if opening_ids != set(expected_terminals):
        blockers.append("SGI_OPENING_TERMINAL_BINDING_INVALID")
        return {}

    saved_rows = saved.get("terminals") if isinstance(saved, dict) and isinstance(saved.get("terminals"), list) else []
    if any(not isinstance(row, dict)
           or not isinstance(row.get("opening_id"), str)
           or not row.get("opening_id") for row in saved_rows):
        blockers.append("SGI_OPENING_VERIFICATION_MISMATCH")
        return {
            "maximum_opening_area_error_ratio": round(maximum_area_error, 12),
            "maximum_supply_flow_error_ratio": round(maximum_flow_error, 12),
        }
    saved_by_id = {row["opening_id"]: row for row in saved_rows}
    if (not isinstance(saved, dict) or saved.get("contract") != "opening_boundary_verification.v1"
            or saved.get("status") != "PASS" or len(saved_by_id) != len(saved_rows)
            or set(saved_by_id) != set(computed)):
        blockers.append("SGI_OPENING_VERIFICATION_MISMATCH")
    else:
        for opening_id, (area_ratio, flow_ratio) in computed.items():
            row = saved_by_id[opening_id]
            saved_area_ratio = _finite(row.get("area_ratio"))
            saved_flow_ratio = _finite(row.get("flow_ratio"))
            if (saved_area_ratio is None
                    or saved_flow_ratio is None
                    or not math.isclose(saved_area_ratio, area_ratio, abs_tol=1e-8)
                    or not math.isclose(saved_flow_ratio, flow_ratio, abs_tol=1e-8)
                    or row.get("area_status") != "PASS" or row.get("flow_status") != "PASS"):
                blockers.append("SGI_OPENING_VERIFICATION_MISMATCH")
                break
    if maximum_area_error > 0.03:
        blockers.append("SGI_OPENING_AREA_ERROR_EXCEEDED")
    if maximum_flow_error > 0.01:
        blockers.append("SGI_SUPPLY_FLOW_ERROR_EXCEEDED")
    return {
        "maximum_opening_area_error_ratio": round(maximum_area_error, 12),
        "maximum_supply_flow_error_ratio": round(maximum_flow_error, 12),
    }


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _sgi_runtime_metrics(case_files: list[Path], paths: dict[str, Path], job: dict[str, Any],
                         blockers: list[str]) -> dict[str, float]:
    """Recompute time, RSS, and output size from the pinned raw records."""

    import cfd_diagnostics

    resource = _read_json(paths["resource_preflight"])
    runner_text = paths["runner_log"].read_text(encoding="utf-8", errors="replace")
    solver_text = paths["solver_log"].read_text(encoding="utf-8", errors="replace")
    memory = cfd_diagnostics.parse_gnu_time_v(runner_text).get("peak_rss_kib")
    solver_clock = cfd_diagnostics.parse_openfoam_timing(solver_text).get("clock_seconds")
    history = job.get("attempt_history") if isinstance(job.get("attempt_history"), list) else []
    final_attempt = history[-1] if history and isinstance(history[-1], dict) else {}
    started = _parse_iso(final_attempt.get("started_at"))
    finished = _parse_iso(final_attempt.get("finished_at"))
    try:
        runner_wall = ((finished - started).total_seconds()
                       if started is not None and finished is not None else None)
    except (OverflowError, TypeError):
        runner_wall = None
    try:
        output_bytes: float | None = float(sum(path.stat().st_size for path in case_files))
    except OSError:
        output_bytes = None
    available_ram = _finite((resource or {}).get("available_ram_bytes"))
    free_disk = _finite((resource or {}).get("free_disk_bytes"))
    estimated_ram = _finite((resource or {}).get("estimated_peak_ram_bytes"))
    estimated_output = _finite((resource or {}).get("estimated_output_bytes"))
    memory_kib = _finite(memory)
    solver_clock_value = _finite(solver_clock)
    runner_wall_value = _finite(runner_wall)
    peak_rss_bytes = memory_kib * 1024.0 if memory_kib is not None else None
    if (not isinstance(resource, dict) or resource.get("contract") != "field_resource_preflight.v1"
            or any(value is None or value <= 0 for value in (
                available_ram, free_disk, estimated_ram, estimated_output,
                peak_rss_bytes, solver_clock_value, runner_wall_value, output_bytes,
            ))):
        blockers.append("SGI_RUNTIME_EVIDENCE_INVALID")
        return {}
    assert available_ram is not None and free_disk is not None
    assert estimated_ram is not None and estimated_output is not None and peak_rss_bytes is not None
    if estimated_ram / available_ram > 0.8 or free_disk < 1.25 * estimated_output:
        blockers.append("SGI_RESOURCE_PREFLIGHT_FAILED")
    if peak_rss_bytes / available_ram > 0.8:
        blockers.append("SGI_PEAK_RSS_LIMIT_EXCEEDED")
    return {
        "runner_wall_seconds": round(runner_wall_value, 12),
        "solver_clock_seconds": round(solver_clock_value, 12),
        "peak_rss_bytes": round(peak_rss_bytes, 12),
        "available_ram_bytes": round(available_ram, 12),
        "output_bytes": output_bytes,
    }


def validate_sgi_screening_acceptance(
    manifest_path: Path,
    projects_root: Path,
    evaluator_output_path: Path | None = None,
) -> dict[str, Any]:
    """Recompute the actual SGI GUI job and current solver-case screening gate."""

    context = _sgi_context(manifest_path, projects_root, evaluator_output_path)
    blockers = context["blockers"]
    evidence = context["evidence"]
    paths = context["paths"]
    root = context["root"]
    case = context["case"]
    if root is None or context["manifest"] is None or context["payload"] is None:
        return _blocked("real_dxf_screening", blockers, evidence)
    required = set(_SGI_CASE_ARTIFACTS) | {
        "field_job", "source_dxf", "reviewed_geometry", "resource_preflight",
        *{"restart_" + key for key in _RESTART_EVIDENCE_KEYS},
    }
    if case is None or not required.issubset(paths):
        return _blocked("real_dxf_screening", blockers, evidence)

    geometry = _read_json(paths["geometry"])
    surface = _read_json(paths["surface"])
    mesh_input = _read_json(paths["mesh_input"])
    mesh = _read_json(paths["mesh"])
    thermal = _read_json(paths["thermal_input"])
    progress = _read_json(paths["thermal_progress"])
    run = _read_json(paths["run"])
    result = _read_json(paths["result"])
    job = _read_json(paths["field_job"])
    structurally_invalid = False
    for code, schema, payload in (
        ("GEOMETRY", "geometry.v2.schema.json", geometry),
        ("SURFACE", "surface_manifest.v1.schema.json", surface),
        ("MESH", "mesh_manifest.v1.schema.json", mesh),
        ("PROGRESS", "thermal_progress.v1.schema.json", progress),
        ("RUN", "run_manifest.v1.schema.json", run),
        ("RESULT", "result_manifest.v1.schema.json", result),
        ("FIELD_JOB", "field_pipeline_job.v1.schema.json", job),
    ):
        if payload is None or _schema_errors(schema, payload):
            blockers.append("SGI_" + code + "_SCHEMA_INVALID")
            structurally_invalid = True
    if structurally_invalid:
        return _blocked("real_dxf_screening", blockers, evidence)

    assert isinstance(geometry, dict) and isinstance(surface, dict)
    assert isinstance(mesh, dict) and isinstance(thermal, dict)
    assert isinstance(progress, dict) and isinstance(run, dict)
    assert isinstance(result, dict) and isinstance(job, dict)
    if (not isinstance(thermal.get("settings"), dict)
            or not isinstance(thermal.get("numerics"), dict)
            or not isinstance(thermal.get("heat"), dict)
            or not isinstance(thermal.get("terminals"), list)
            or not all(isinstance(row, dict) for row in thermal.get("terminals", []))
            or not isinstance(thermal.get("heat_sources"), list)
            or not all(isinstance(row, dict) for row in thermal.get("heat_sources", []))):
        return _blocked(
            "real_dxf_screening", blockers + ["SGI_THERMAL_INPUT_INVALID"], evidence,
        )
    thermal_settings = thermal["settings"]
    thermal_rows = thermal["terminals"]
    if (any(_finite(thermal_settings.get(key)) is None for key in (
            "supply_temperature_k", "air_density_kg_m3", "air_specific_heat_j_kg_k"))
            or any(
                row.get("role") not in {"supply", "exhaust"}
                or not isinstance(row.get("name"), str) or not row.get("name")
                or not isinstance(row.get("mesh_patch_name"), str) or not row.get("mesh_patch_name")
                or not isinstance(row.get("source_element_id"), str) or not row.get("source_element_id")
                or _finite(row.get("flow_rate_m3_s")) is None
                or _finite(row.get("airflow_cmh")) is None
                for row in thermal_rows
            )
            or any(not isinstance(row.get("source_id"), str) or not row.get("source_id")
                   for row in thermal["heat_sources"])):
        return _blocked(
            "real_dxf_screening", blockers + ["SGI_THERMAL_INPUT_INVALID"], evidence,
        )
    from geometry_v2 import validate_for_body_fitted
    review = geometry.get("review") if isinstance(geometry, dict) and isinstance(geometry.get("review"), dict) else {}
    if (review.get("ready") is not True or review.get("blocking") is True
            or review.get("blocker_count") != 0 or validate_for_body_fitted(geometry)):
        blockers.append("SGI_GEOMETRY_REVIEW_NOT_READY")
    supplies, exhausts = _sgi_terminal_rows(geometry)
    if len(supplies) != 15 or len(exhausts) != 15:
        blockers.append("SGI_TERMINAL_COUNT_INVALID")
    terminal_ids = [row.get("id") for row in supplies + exhausts]
    if (any(not isinstance(value, str) or not value for value in terminal_ids)
            or len(set(terminal_ids)) != len(terminal_ids)):
        blockers.append("SGI_TERMINAL_IDENTITY_INVALID")
    for item in supplies + exhausts:
        semantic = item.get("semantic") if isinstance(item.get("semantic"), dict) else {}
        normal = semantic.get("normal")
        if (item.get("confirmed") is not True or _finite(semantic.get("airflow_cmh")) != 444.0
                or not isinstance(normal, list) or len(normal) != 3
                or any(_finite(value) is None for value in normal)
                or math.sqrt(sum(float(value) ** 2 for value in normal)) <= 0):
            blockers.append("SGI_TERMINAL_REVIEW_INVALID")
            break
    expected_dxf = paths["source_dxf"].relative_to(root).as_posix()
    if (geometry.get("source") != expected_dxf
            or geometry.get("source_sha256") != _sha256_file(paths["source_dxf"])):
        blockers.append("SGI_DXF_PROVENANCE_INVALID")
    authority = geometry.get("scenario_authority")
    fixture_only = geometry.get("validation_fixture_only")
    if (authority == "non_authoritative_working_fixture" and fixture_only is not True
            or authority == "site_schedule" and fixture_only is not False
            or authority not in {"site_schedule", "non_authoritative_working_fixture"}):
        blockers.append("SGI_SCENARIO_AUTHORITY_INVALID")
    surface_source = surface.get("source") if isinstance(surface, dict) and isinstance(surface.get("source"), dict) else {}
    if (surface_source.get("geometry_path") != paths["geometry"].relative_to(root).as_posix()
            or surface_source.get("geometry_sha256") != _sha256_file(paths["geometry"])):
        blockers.append("SGI_SURFACE_GEOMETRY_BINDING_INVALID")

    if (not isinstance(mesh_input, dict) or mesh_input.get("contract") != "mesh_input.v1"
            or mesh_input.get("surface_manifest_sha256") != _sha256_file(paths["surface"])
            or (mesh.get("input") or {}).get("surface_manifest_sha256") != _sha256_file(paths["surface"])
            or (mesh.get("input") or {}).get("mesh_input_sha256") != _sha256_file(paths["mesh_input"])
            or thermal.get("mesh_manifest_sha256") != _sha256_file(paths["mesh"])):
        blockers.append("SGI_SURFACE_MESH_THERMAL_BINDING_INVALID")
    thermal_supplies = [row for row in thermal_rows if isinstance(row, dict) and row.get("role") == "supply"]
    thermal_exhausts = [row for row in thermal_rows if isinstance(row, dict) and row.get("role") == "exhaust"]
    flow_values = [_finite(row.get("flow_rate_m3_s")) for row in thermal_rows]
    supply_total = sum(value or 0.0 for row, value in zip(thermal_rows, flow_values)
                       if isinstance(row, dict) and row.get("role") == "supply")
    exhaust_total = sum(value or 0.0 for row, value in zip(thermal_rows, flow_values)
                        if isinstance(row, dict) and row.get("role") == "exhaust")
    denominator = max(supply_total, exhaust_total)
    if (len(thermal_supplies) != 15 or len(thermal_exhausts) != 15 or denominator <= 0
            or abs(supply_total - exhaust_total) / denominator > 0.01
            or any(value is None or value <= 0 for value in flow_values)
            or any(_finite(row.get("airflow_cmh")) != 444.0 for row in thermal_rows)):
        blockers.append("SGI_THERMAL_TERMINAL_INPUT_INVALID")

    surface_regions = surface.get("regions") if isinstance(surface.get("regions"), list) else []
    surface_by_source: dict[str, list[dict[str, Any]]] = {}
    for region in surface_regions:
        if not isinstance(region, dict):
            blockers.append("SGI_SURFACE_REGION_INVALID")
            continue
        source_ids = region.get("source_element_ids")
        if (not isinstance(source_ids, list)
                or any(not isinstance(source_id, str) or not source_id for source_id in source_ids)):
            blockers.append("SGI_SURFACE_REGION_INVALID")
            continue
        for source_id in source_ids:
            surface_by_source.setdefault(str(source_id), []).append(region)
    thermal_by_source: dict[str, list[dict[str, Any]]] = {}
    for row in thermal_rows:
        if isinstance(row, dict):
            thermal_by_source.setdefault(str(row.get("source_element_id") or ""), []).append(row)
    for terminal in supplies + exhausts:
        terminal_id = str(terminal.get("id") or "")
        semantic = terminal.get("semantic") or {}
        regions = surface_by_source.get(terminal_id, [])
        thermal_matches = thermal_by_source.get(terminal_id, [])
        if (len(regions) != 1 or len(thermal_matches) != 1
                or regions[0].get("role") != semantic.get("role")
                or _finite(regions[0].get("airflow_cmh")) != 444.0
                or regions[0].get("design_normal") != semantic.get("normal")
                or thermal_matches[0].get("role") != semantic.get("role")
                or thermal_matches[0].get("name") != regions[0].get("name")
                or thermal_matches[0].get("mesh_patch_name") != regions[0].get("name")):
            blockers.append("SGI_TERMINAL_CHAIN_INVALID")
            break

    equipment = geometry.get("elements", {}).get("equipment", [])
    geometry_heat = [row for row in equipment if isinstance(row, dict)
                     and isinstance(row.get("semantic"), dict)
                     and row["semantic"].get("role") == "heat_source"]
    thermal_heat = thermal.get("heat_sources") if isinstance(thermal.get("heat_sources"), list) else []
    heat_ids = [row.get("id") for row in geometry_heat]
    heat_total = sum(_finite((row.get("semantic") or {}).get("convective_power_w")) or 0.0
                     for row in geometry_heat)
    if (not geometry_heat or len(set(heat_ids)) != len(heat_ids)
            or len(thermal_heat) != len(geometry_heat)):
        blockers.append("SGI_HEAT_SOURCE_IDENTITY_INVALID")
    else:
        thermal_heat_by_id = {row.get("source_id"): row for row in thermal_heat if isinstance(row, dict)}
        for row in geometry_heat:
            source_id = row.get("id")
            semantic = row.get("semantic") or {}
            linked = thermal_heat_by_id.get(source_id)
            source_ref = row.get("source_ref") if isinstance(row.get("source_ref"), dict) else {}
            if (not isinstance(linked, dict) or linked.get("source_ref") != source_ref
                    or linked.get("evidence") != semantic.get("evidence")
                    or _finite(linked.get("input_power_w")) != _finite(semantic.get("input_power_w"))
                    or _finite(linked.get("convective_power_w")) != _finite(semantic.get("convective_power_w"))
                    or len(surface_by_source.get(str(source_id), [])) != 1
                    or surface_by_source[str(source_id)][0].get("role") != "heat_source"):
                blockers.append("SGI_HEAT_SOURCE_CHAIN_INVALID")
                break
            if authority == "non_authoritative_working_fixture":
                if (not str(source_id).startswith("manual_heat_")
                        or source_ref.get("entity_type") != "UI_INPUT"
                        or source_ref.get("layer") != "USER_CONFIRMED"
                        or source_ref.get("source_id") != source_id
                        or not _has_physical_equipment_location(row)
                        or not str(semantic.get("evidence") or "").startswith("non_authoritative_working_fixture:")
                        or _finite(semantic.get("convective_fraction")) != 1.0
                        or _finite(semantic.get("radiative_fraction")) != 0.0):
                    blockers.append("SGI_FIXTURE_HEAT_PROVENANCE_INVALID")
            elif not str(semantic.get("evidence") or "").startswith("site_schedule:"):
                blockers.append("SGI_SITE_SCHEDULE_HEAT_PROVENANCE_INVALID")
    applied_heat = _finite((thermal.get("heat") or {}).get("applied_convective_power_w"))
    if (thermal.get("scenario_authority") != authority
            or thermal.get("validation_fixture_only") is not fixture_only
            or applied_heat is None or not math.isclose(applied_heat, heat_total, abs_tol=1e-9)
            or authority == "non_authoritative_working_fixture" and not math.isclose(heat_total, 15500.0, abs_tol=1e-9)
            or authority == "site_schedule" and heat_total <= 0):
        blockers.append("SGI_THERMAL_SCENARIO_INVALID")

    air = surface.get("air_volume") if isinstance(surface, dict) and isinstance(surface.get("air_volume"), dict) else {}
    topology = surface.get("topology") if isinstance(surface, dict) and isinstance(surface.get("topology"), dict) else {}
    if not (air.get("valid") is True and air.get("solid_count") == 1 and topology.get("watertight") is True):
        blockers.append("SGI_AIR_VOLUME_INVALID")
    mesh_quality = mesh.get("mesh") if isinstance(mesh, dict) and isinstance(mesh.get("mesh"), dict) else {}
    if (not isinstance(mesh, dict) or mesh.get("status") != "PASS"
            or mesh_quality.get("mesh_ok") is not True or mesh_quality.get("fatal") is not False):
        blockers.append("SGI_MESH_GATE_FAILED")
    check_text = paths["check_mesh_log"].read_text(encoding="utf-8", errors="replace")
    illegal = re.search(r"(?:number\s+of\s+)?illegal\s+cells\s*[:=]\s*(\d+)", check_text, re.I)
    if not re.search(r"(?m)^\s*Mesh OK\.\s*$", check_text) or illegal is None or int(illegal.group(1)) != 0:
        blockers.append("SGI_CHECK_MESH_INVALID")

    metrics = _sgi_field_metrics(case, paths, thermal, blockers)
    flow_fraction = _finite((progress or {}).get("flow_through_fraction"))
    latest_time = _finite((progress or {}).get("latest_time_s"))
    if flow_fraction is None or flow_fraction < 3.0 or latest_time is None:
        blockers.append("SGI_FINAL_PHYSICAL_PROGRESS_INVALID")
    else:
        metrics.update(final_flow_through_fraction=round(flow_fraction, 12), final_physical_time_s=round(latest_time, 12))

    opening_expectations = {
        row["source_element_id"]: {
            "role": row["role"], "parent_name": row["mesh_patch_name"],
            "airflow_cmh": row["airflow_cmh"],
        }
        for row in thermal_rows
    }
    if len(opening_expectations) != len(thermal_rows):
        blockers.append("SGI_OPENING_TERMINAL_BINDING_INVALID")
    metrics.update(_sgi_opening_metrics(case, paths, opening_expectations, blockers))
    case_files = _tree_files(case, root) or []
    metrics.update(_sgi_runtime_metrics(case_files, paths, job, blockers))

    slices = result.get("slices") if isinstance(result.get("slices"), list) else []
    expected_slices = {
        axis: (paths["slice_" + axis].relative_to(case).as_posix(),
               _sha256_file(paths["slice_" + axis])) for axis in "xyz"
    }
    slice_map = {row.get("axis"): (row.get("path"), row.get("sha256"))
                 for row in slices if isinstance(row, dict)}
    provenance = (run.get("input") or {}).get("numerical_provenance")
    actual_system = {"controlDict": _sha256_file(paths["control_dict"]),
                     "fvSchemes": _sha256_file(paths["fv_schemes"]),
                     "fvSolution": _sha256_file(paths["fv_solution"])}
    if ((result.get("source") or {}).get("path") != paths["vtu"].relative_to(case).as_posix()
            or (result.get("source") or {}).get("sha256") != _sha256_file(paths["vtu"])
            or result.get("summary_path") != paths["summary"].relative_to(case).as_posix()
            or result.get("summary_sha256") != _sha256_file(paths["summary"])
            or result.get("run_manifest_sha256") != _sha256_file(paths["run"])
            or result.get("mesh_manifest_sha256") != _sha256_file(paths["mesh"])
            or result.get("thermal_input_sha256") != _sha256_file(paths["thermal_input"])
            or slice_map != expected_slices
            or (run.get("input") or {}).get("thermal_input_sha256") != _sha256_file(paths["thermal_input"])
            or not isinstance(provenance, dict) or provenance.get("system") != actual_system
            or provenance.get("expected_system") != actual_system):
        blockers.append("SGI_RESULT_BINDING_INVALID")
    for axis in "xyz":
        value = _read_json(paths["slice_" + axis])
        if not isinstance(value, dict) or value.get("axis") != axis or not _finite_tree(value):
            blockers.append("SGI_SLICE_INVALID:" + axis)
    report = paths["report"].read_text(encoding="utf-8", errors="replace")
    if not report.strip() or "DESIGN_CITABLE" in report or not any(label in report for label in ("SCREENING_ONLY", "NOT_EVALUATED")):
        blockers.append("SGI_REPORT_CITATION_INVALID")

    expected_case = case.relative_to(root).as_posix()
    job_input = job.get("input") if isinstance(job.get("input"), dict) else {}
    level = job.get("level") if isinstance(job.get("level"), dict) else {}

    def job_path(value: object, *, directory: bool = False) -> Path | None:
        if not isinstance(value, str) or not value:
            return None
        raw = Path(value)
        if not raw.is_absolute():
            return _resolve_ref(value, root, directory=directory)
        if not _reparse_free_chain(raw, root):
            return None
        try:
            resolved = raw.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, RuntimeError, ValueError):
            return None
        if directory and not resolved.is_dir() or not directory and not resolved.is_file():
            return None
        return resolved

    if (job.get("status") not in {"complete", "analysis_complete_not_citable"}
            or job.get("stage") != "complete"
            or job_path(job.get("result_case"), directory=True) != case
            or job_path(level.get("thermal_case"), directory=True) != case
            or job_path(job_input.get("geometry_path")) != paths["geometry"]
            or job_input.get("geometry_sha256") != _sha256_file(paths["geometry"])
            or job_path(job_input.get("source_dxf_path")) != paths["source_dxf"]
            or job_input.get("source_dxf_sha256") != _sha256_file(paths["source_dxf"])):
        blockers.append("SGI_FIELD_JOB_BINDING_INVALID")
    if job.get("citation_status") not in {"SCREENING_ONLY", "NOT_EVALUATED"}:
        blockers.append("SGI_FIELD_JOB_CITATION_INVALID")

    import cfd_physics
    parsed_log = cfd_physics.parse_thermal_log(
        paths["solver_log"].read_text(encoding="utf-8", errors="replace")
    )
    summary = _read_json(paths["summary"])
    opening_saved = _read_json(paths["opening_verification"])
    try:
        field_time = float(paths["field_t"].parent.name)
        opening_time = float((opening_saved or {}).get("phi_time"))
    except (TypeError, ValueError):
        field_time = opening_time = None
    final_times = [
        _finite(parsed_log.get("end_time")), _finite(progress.get("latest_time_s")),
        _finite(result.get("time_s")), _finite((summary or {}).get("time_s")),
        field_time, opening_time,
    ]
    if (any(value is None for value in final_times)
            or len({round(float(value), 9) for value in final_times if value is not None}) != 1):
        blockers.append("SGI_FINAL_TIME_AMBIGUOUS")

    for relative, prior in list(evidence.items()):
        try:
            if _sha256_file(root.joinpath(*PurePosixPath(relative).parts)) != prior:
                blockers.append("POST_LOAD_MUTATION:" + relative)
        except OSError:
            blockers.append("POST_LOAD_MUTATION:" + relative)
    final_files = _tree_files(case, root)
    final_set = (frozenset(path.relative_to(case).as_posix() for path in final_files)
                 if final_files is not None else None)
    if final_set != context.get("case_file_set"):
        blockers.append("POST_LOAD_CASE_TREE_CHANGED:sgi")
    return _blocked("real_dxf_screening", blockers, evidence, metrics)


def validate_restart_integrity(
    manifest_path: Path,
    projects_root: Path,
    evaluator_output_path: Path | None = None,
) -> dict[str, Any]:
    """Derive restart integrity from immutable attempt/checkpoint evidence."""

    context = _sgi_context(manifest_path, projects_root, evaluator_output_path)
    blockers = context["blockers"]
    evidence = context["evidence"]
    paths = context["paths"]
    root = context["root"]
    case = context["case"]
    metrics: dict[str, float | int] = {}
    if root is None or context["manifest"] is None or context["payload"] is None:
        return _blocked("restart_integrity", blockers, evidence)
    required = {
        "field_job", "geometry", "thermal_input", "thermal_progress", "run", "result",
        *{"restart_" + key for key in _RESTART_EVIDENCE_KEYS},
    }
    if case is None or not required.issubset(paths):
        return _blocked("restart_integrity", blockers, evidence)
    job = _read_json(paths["field_job"])
    thermal = _read_json(paths["thermal_input"])
    progress = _read_json(paths["thermal_progress"])
    run = _read_json(paths["run"])
    result = _read_json(paths["result"])
    pre = _read_json(paths["restart_pre_attempt_snapshot"])
    post = _read_json(paths["restart_post_attempt_snapshot"])
    process_audit = _read_json(paths["restart_process_audit"])
    if not all(isinstance(value, dict) for value in (
            job, thermal, progress, run, result, pre, post, process_audit)):
        return _blocked("restart_integrity", blockers + ["RESTART_STRUCTURAL_EVIDENCE_INVALID"], evidence)
    assert isinstance(job, dict) and isinstance(thermal, dict) and isinstance(progress, dict)
    assert isinstance(run, dict) and isinstance(result, dict)
    assert isinstance(pre, dict) and isinstance(post, dict) and isinstance(process_audit, dict)
    snapshot_keys = ("geometry", "thermal_input", "solver_log", "field_t", "field_u", "field_phi")
    if (any(not isinstance(snapshot.get(key), dict)
            or not isinstance(snapshot[key].get("path"), str)
            or not _SHA256.fullmatch(str(snapshot[key].get("sha256") or ""))
            for snapshot in (pre, post) for key in snapshot_keys)
            or not isinstance(process_audit.get("observations"), list)):
        return _blocked(
            "restart_integrity", blockers + ["RESTART_STRUCTURAL_EVIDENCE_INVALID"], evidence,
        )
    restart_terminals = thermal.get("terminals")
    if (not isinstance(restart_terminals, list) or not restart_terminals
            or any(not isinstance(row, dict)
                   or row.get("role") not in {"supply", "exhaust"}
                   or not isinstance(row.get("mesh_patch_name"), str)
                   or not row.get("mesh_patch_name")
                   for row in restart_terminals)):
        return _blocked(
            "restart_integrity", blockers + ["RESTART_STRUCTURAL_EVIDENCE_INVALID"], evidence,
        )

    attempts = job.get("attempts")
    history = job.get("attempt_history") if isinstance(job.get("attempt_history"), list) else []
    resume = job.get("resume_history") if isinstance(job.get("resume_history"), list) else []
    if (not isinstance(attempts, int) or isinstance(attempts, bool) or attempts != 2
            or len(history) != attempts
            or [row.get("attempt") for row in history if isinstance(row, dict)] != list(range(1, attempts + 1))):
        blockers.append("RESTART_ATTEMPT_COUNT_INVALID")
    else:
        metrics["attempt_count"] = attempts
    if (len(resume) != 1 or not isinstance(resume[0], dict)
            or len(history) != 2 or not all(isinstance(row, dict) for row in history)):
        blockers.append("RESTART_HISTORY_INVALID")
        resume_row = {}
    else:
        resume_row = resume[0]

    if len(history) == 2 and all(isinstance(row, dict) for row in history):
        first_attempt, second_attempt = history
        first_started = _parse_iso(first_attempt.get("started_at"))
        first_finished = _parse_iso(first_attempt.get("finished_at"))
        second_started = _parse_iso(second_attempt.get("started_at"))
        second_finished = _parse_iso(second_attempt.get("finished_at"))
        resumed_at = _parse_iso(resume_row.get("resumed_at"))
        ordered_times = all(value is not None for value in (
            first_started, first_finished, resumed_at, second_started, second_finished,
        ))
        if ordered_times:
            assert first_started is not None and first_finished is not None
            assert resumed_at is not None and second_started is not None and second_finished is not None
            try:
                ordered_times = (
                    first_started < first_finished <= resumed_at == second_started < second_finished
                )
                first_elapsed = (first_finished - first_started).total_seconds()
                second_elapsed = (second_finished - second_started).total_seconds()
            except (OverflowError, TypeError):
                ordered_times = False
                first_elapsed = second_elapsed = None
        else:
            first_elapsed = second_elapsed = None
        if (first_attempt.get("status") != "FAIL"
                or second_attempt.get("status") != job.get("status")
                or second_attempt.get("status") != post.get("state")
                or not ordered_times
                or _finite(first_attempt.get("elapsed_s")) != first_elapsed
                or _finite(second_attempt.get("elapsed_s")) != second_elapsed
                or job.get("updated_at") != second_attempt.get("finished_at")):
            blockers.append("RESTART_HISTORY_STATE_INVALID")
    else:
        blockers.append("RESTART_HISTORY_STATE_INVALID")

    root_ref = root

    def snapshot_link(snapshot: dict[str, Any], key: str) -> Path | None:
        link = snapshot.get(key)
        path = _resolve_ref(link.get("path"), root_ref) if isinstance(link, dict) else None
        if path is None:
            blockers.append("RESTART_SNAPSHOT_REF_INVALID:" + key)
            return None
        actual = _sha256_file(path)
        evidence[path.relative_to(root_ref).as_posix()] = actual
        if actual != link.get("sha256"):
            blockers.append("RESTART_SNAPSHOT_HASH_MISMATCH:" + key)
        return path

    pre_paths = {key: snapshot_link(pre, key) for key in snapshot_keys}
    post_paths = {key: snapshot_link(post, key) for key in snapshot_keys}
    expected_pre = {
        "geometry": paths["geometry"], "thermal_input": paths["thermal_input"],
        "solver_log": paths["restart_checkpoint_solver_log"],
        "field_t": paths["restart_checkpoint_field_t"],
        "field_u": paths["restart_checkpoint_field_u"],
        "field_phi": paths["restart_checkpoint_field_phi"],
    }
    expected_post = {
        "geometry": paths["geometry"], "thermal_input": paths["thermal_input"],
        "solver_log": paths["solver_log"], "field_t": paths["field_t"],
        "field_u": paths["field_u"], "field_phi": paths["field_phi"],
    }
    if pre_paths != expected_pre:
        blockers.append("RESTART_PRE_ATTEMPT_BINDING_INVALID")
    if post_paths != expected_post:
        blockers.append("RESTART_POST_ATTEMPT_BINDING_INVALID")

    checkpoint_parents = {path.parent for key, path in pre_paths.items()
                          if key.startswith("field_") and path is not None}
    try:
        checkpoint = (float(next(iter(checkpoint_parents)).name)
                      if len(checkpoint_parents) == 1 else None)
    except (StopIteration, ValueError):
        checkpoint = None
    import cfd_physics
    try:
        checkpoint_t = cfd_physics._internal_scalar_values(
            paths["restart_checkpoint_field_t"],
        )
        checkpoint_u = cfd_physics._internal_vector_values(
            paths["restart_checkpoint_field_u"],
        )
        checkpoint_phi = cfd_physics._boundary_scalar_values(
            paths["restart_checkpoint_field_phi"],
        )
        checkpoint_balance = cfd_physics.terminal_flux_balance(
            paths["restart_checkpoint_field_phi"].parent, restart_terminals,
        )
    except (KeyError, OSError, TypeError, ValueError):
        checkpoint_t, checkpoint_u, checkpoint_phi, checkpoint_balance = [], [], {}, {}
    expected_patches = {row["mesh_patch_name"] for row in restart_terminals}
    checkpoint_imbalance = _finite(checkpoint_balance.get("imbalance_ratio"))
    if (not checkpoint_t or len(checkpoint_t) != len(checkpoint_u)
            or any(not math.isfinite(value) for value in checkpoint_t)
            or any(not math.isfinite(component) for row in checkpoint_u for component in row)
            or not expected_patches.issubset(checkpoint_phi)
            or any(not values or any(not math.isfinite(value) for value in values)
                   for patch, values in checkpoint_phi.items() if patch in expected_patches)
            or checkpoint_balance.get("available") is not True
            or checkpoint_imbalance is None or checkpoint_imbalance > 0.001):
        blockers.append("RESTART_CHECKPOINT_FIELDS_INVALID")
    checkpoint_text = paths["restart_checkpoint_solver_log"].read_text(
        encoding="utf-8", errors="replace",
    )
    checkpoint_times = [float(value) for value in re.findall(
        r"(?m)^\s*Time\s*=\s*([-+0-9.eE]+)\s*$", checkpoint_text,
    )]
    if (checkpoint is None or checkpoint <= 0 or not checkpoint_times
            or checkpoint_times[-1] != checkpoint
            or _finite(resume_row.get("checkpoint_time_s")) != checkpoint
            or resume_row.get("previous_attempt") != 1
            or resume_row.get("previous_status") != "FAIL"
            or resume_row.get("previous_stage") != "thermal"
            or resume_row.get("checkpoint_log_path")
            != paths["restart_checkpoint_solver_log"].relative_to(root).as_posix()
            or resume_row.get("checkpoint_log_sha256")
            != _sha256_file(paths["restart_checkpoint_solver_log"])):
        blockers.append("RESTART_CHECKPOINT_TRANSITION_INVALID")
    else:
        metrics["verified_checkpoint_physical_time_s"] = round(checkpoint, 12)

    expected_job = job.get("job")
    expected_case = case.relative_to(root).as_posix()
    if (pre.get("contract") != "field_attempt_snapshot.v1"
            or pre.get("job") != expected_job or pre.get("attempt") != 1
            or pre.get("phase") != "pre_resume" or pre.get("state") != "interrupted_checkpoint"
            or pre.get("case_path") != expected_case
            or post.get("contract") != "field_attempt_snapshot.v1"
            or post.get("job") != expected_job or post.get("attempt") != 2
            or post.get("phase") != "post_resume"
            or post.get("state") not in {"complete", "analysis_complete_not_citable"}
            or post.get("case_path") != expected_case):
        blockers.append("RESTART_STATE_TRANSITION_INVALID")
    if len(history) == 2 and all(isinstance(row, dict) for row in history):
        for row, snapshot_path in zip(history, (
            paths["restart_pre_attempt_snapshot"], paths["restart_post_attempt_snapshot"],
        )):
            if (row.get("snapshot_path") != snapshot_path.relative_to(root).as_posix()
                    or row.get("snapshot_sha256") != _sha256_file(snapshot_path)):
                blockers.append("RESTART_HISTORY_SNAPSHOT_BINDING_INVALID")
                break

    observations = (process_audit.get("observations")
                    if isinstance(process_audit.get("observations"), list) else [])
    phases = [row.get("phase") for row in observations if isinstance(row, dict)]
    if (process_audit.get("contract") != "field_resume_process_audit.v1"
            or process_audit.get("job") != expected_job
            or phases != ["before_resume", "after_resume_launch", "after_completion"]
            or process_audit.get("conflicting_job_relaunched") is not False
            or any(not isinstance(row, dict)
                   or not isinstance(row.get("matching_solver_count"), int)
                   or row.get("matching_solver_count") < 0
                   or row.get("matching_solver_count") > 1
                   or row.get("conflicting_solver_count") != 0
                   for row in observations)):
        blockers.append("RESTART_SOLVER_CONFLICT_INVALID")

    geometry_sha = _sha256_file(paths["geometry"])
    input_sha = _sha256_file(paths["thermal_input"])
    if any(snapshot.get("geometry", {}).get("sha256") != geometry_sha for snapshot in (pre, post)):
        blockers.append("RESTART_GEOMETRY_HASH_CHANGED")
    if any(snapshot.get("thermal_input", {}).get("sha256") != input_sha for snapshot in (pre, post)):
        blockers.append("RESTART_INPUT_HASH_CHANGED")

    try:
        final_parents = {post_paths[key].parent for key in ("field_t", "field_u", "field_phi")
                         if post_paths[key] is not None}
        final_time = float(next(iter(final_parents)).name) if len(final_parents) == 1 else None
    except (StopIteration, ValueError):
        final_time = None
    flow_fraction = _finite(progress.get("flow_through_fraction"))
    if (final_time is None or checkpoint is None or final_time <= checkpoint
            or _finite(progress.get("latest_time_s")) != final_time
            or flow_fraction is None or flow_fraction < 3.0):
        blockers.append("RESTART_FINAL_PROGRESS_INVALID")
    else:
        metrics["final_physical_time_s"] = round(final_time, 12)
        metrics["final_flow_through_fraction"] = round(flow_fraction, 12)
    level = job.get("level") if isinstance(job.get("level"), dict) else {}

    def producer_case(value: object) -> Path | None:
        if not isinstance(value, str) or not value:
            return None
        raw = Path(value)
        if not raw.is_absolute():
            relative = _lexical_ref(value)
            if relative is None:
                return None
            raw = root.joinpath(*relative.parts)
        try:
            resolved = raw.resolve(strict=True)
            resolved.relative_to(root / "_body_solver")
        except (OSError, RuntimeError, ValueError):
            return None
        return resolved if resolved.is_dir() and _reparse_free_chain(resolved, root) else None

    if (producer_case(job.get("result_case")) != case
            or producer_case(level.get("thermal_case")) != case
            or not isinstance(run, dict) or (run.get("input") or {}).get("thermal_input_sha256") != input_sha
            or not isinstance(result, dict) or result.get("run_manifest_sha256") != _sha256_file(paths["run"])
            or result.get("thermal_input_sha256") != input_sha):
        blockers.append("RESTART_FINAL_CASE_BINDING_INVALID")
    for relative, prior in list(evidence.items()):
        try:
            if _sha256_file(root.joinpath(*PurePosixPath(relative).parts)) != prior:
                blockers.append("POST_LOAD_MUTATION:" + relative)
        except OSError:
            blockers.append("POST_LOAD_MUTATION:" + relative)
    final_files = _tree_files(case, root)
    final_set = (frozenset(path.relative_to(case).as_posix() for path in final_files)
                 if final_files is not None else None)
    if final_set != context.get("case_file_set"):
        blockers.append("POST_LOAD_CASE_TREE_CHANGED:sgi")
    return _blocked("restart_integrity", blockers, evidence, metrics)


evaluate_sgi_screening_acceptance = validate_sgi_screening_acceptance
evaluate_restart_integrity = validate_restart_integrity
