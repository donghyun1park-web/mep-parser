"""Pure, fail-closed validation for the single-PC adiabatic heat box.

The validator never runs OpenFOAM and never trusts the producer's PASS or
claimed metric fields.  It resolves only hash-pinned files below one explicit
``projects_root`` and recomputes the energy-accounting and numerical gates from
the current thermal input, solver log, and independent cell data.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any

from jsonschema import Draft202012Validator

import cfd_numerics
import cfd_physics


CONTRACT = "heat_box_validation.v1"
MANIFEST_CONTRACT = "verification_manifest.v1"
VALIDATION_SCOPE = "single_pc_adiabatic_heat_box"
CANONICAL_MANIFEST_PATH = PurePosixPath(
    "_working_validation/heat-box-v1/verification_manifest.json"
)
_HEX = re.compile(r"^[0-9a-f]{64}$")
_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_FORBIDDEN_PARTS = frozenset({
    ".cache", "cache", "caches", ".pytest_cache", "__pycache__",
    "tmp", "temp", ".tmp", "staging", "recovery", "backup", "latest",
})
_SCIENTIFIC_FAILURES = frozenset({
    "HEAT_BOX_MEAN_TEMPERATURE_ERROR_LIMIT",
    "HEAT_BOX_STORAGE_CLOSURE_LIMIT",
    "HEAT_BOX_COURANT_LIMIT",
    "HEAT_BOX_CONTINUITY_LIMIT",
    "HEAT_BOX_NET_BOUNDARY_FLUX_LIMIT",
    "HEAT_BOX_BOUSSINESQ_RANGE_LIMIT",
})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_sha256(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _file_identity(path: Path) -> tuple[int, int, int] | None:
    try:
        info = path.lstat()
    except OSError:
        return None
    if _is_reparse(path) or not stat.S_ISREG(info.st_mode):
        return None
    return (int(info.st_dev), int(info.st_ino), int(info.st_mode))


def _snapshot_file(
    path: Path,
) -> tuple[bytes, str, tuple[int, int, int]] | None:
    """Read one immutable-in-memory evidence snapshot for hashing and parsing."""
    before = _file_identity(path)
    if before is None or not _lexical_chain_safe(path):
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    after = _file_identity(path)
    if after != before or not _lexical_chain_safe(path):
        return None
    return data, hashlib.sha256(data).hexdigest(), before


def _has_dot_segment(value: object) -> bool:
    try:
        raw = os.fsdecode(os.fspath(value)).replace("\\", "/")
    except (TypeError, ValueError):
        return True
    return any(part in {".", ".."} for part in raw.split("/"))


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


def _canonical_root(projects_root: Path) -> Path | None:
    if _has_dot_segment(projects_root):
        return None
    try:
        lexical = Path(projects_root).expanduser().absolute()
        if not lexical.is_dir() or not _lexical_chain_safe(lexical):
            return None
        return lexical.resolve(strict=True)
    except (OSError, RuntimeError):
        return None


def _relative_ref(value: object) -> PurePosixPath | None:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or value.startswith("/")
        or (len(value) >= 2 and value[1] == ":")
        or _has_dot_segment(value)
    ):
        return None
    relative = PurePosixPath(value)
    folded = [part.casefold() for part in relative.parts]
    if any(
        part in _FORBIDDEN_PARTS or part.endswith(".tmp")
        for part in folded
    ):
        return None
    return relative


def _safe_existing_ref(value: object, root: Path) -> Path | None:
    relative = _relative_ref(value)
    if relative is None:
        return None
    lexical = root.joinpath(*relative.parts)
    try:
        if not _lexical_chain_safe(lexical):
            return None
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(root)
        if not resolved.is_file():
            return None
        return resolved
    except (OSError, RuntimeError, ValueError):
        return None


def _safe_manifest(path: Path, root: Path) -> Path | None:
    supplied = Path(path)
    if _has_dot_segment(supplied):
        return None
    lexical = supplied if supplied.is_absolute() else root / supplied
    try:
        if not _lexical_chain_safe(lexical):
            return None
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(root)
        return resolved if resolved.is_file() else None
    except (OSError, RuntimeError, ValueError):
        return None


def _output_blocker(
        output_path: Path | None, root: Path, sources: list[Path]) -> str | None:
    if output_path is None:
        return None
    raw = Path(output_path)
    if _has_dot_segment(raw):
        return "OUTPUT_PATH_INVALID"
    lexical = raw if raw.is_absolute() else root / raw
    try:
        resolved = lexical.resolve(strict=False)
        resolved.relative_to(root)
        parent = lexical.parent.resolve(strict=True)
        parent.relative_to(root)
        if not parent.is_dir() or not _lexical_chain_safe(lexical.parent):
            return "OUTPUT_PATH_INVALID"
        try:
            leaf_info = lexical.lstat()
        except FileNotFoundError:
            leaf_info = None
        if leaf_info is not None and (
            _is_reparse(lexical)
            or not stat.S_ISREG(leaf_info.st_mode)
            or not _lexical_chain_safe(lexical)
        ):
            return "OUTPUT_PATH_INVALID"
    except (OSError, RuntimeError, ValueError):
        return "OUTPUT_PATH_INVALID"
    for source in sources:
        try:
            if resolved == source.resolve(strict=False):
                return "OUTPUT_ALIAS"
            if lexical.exists() and os.path.samefile(lexical, source):
                return "OUTPUT_ALIAS"
        except OSError:
            return "OUTPUT_PATH_INVALID"
    return None


def _json_from_bytes(data: bytes) -> dict[str, Any] | None:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"duplicate JSON key: {key}")
            value[key] = item
        return value

    def reject_nonstandard_number(value: str) -> None:
        raise ValueError(f"non-standard JSON number: {value}")

    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_nonstandard_number,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return _json_from_bytes(path.read_bytes())
    except OSError:
        return None


def _text_from_bytes(data: bytes) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeError:
        return None


def _production_text_equal(actual: str, expected: str) -> bool:
    return actual.replace("\r\n", "\n") == expected.replace("\r\n", "\n")


def _read_text(path: Path) -> str | None:
    try:
        return _text_from_bytes(path.read_bytes())
    except OSError:
        return None


def _finite(value: object, *, positive: bool = False) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0):
        return None
    return number


def _float_text(value: str | None, *, positive: bool = False) -> float | None:
    try:
        return _finite(float(value), positive=positive) if value is not None else None
    except (TypeError, ValueError):
        return None


def _schema_validator() -> Draft202012Validator:
    schema_path = Path(__file__).with_name("verification_manifest.v1.schema.json")
    return Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8")))


def _artifact_schema_validator(filename: str) -> Draft202012Validator:
    schema_path = Path(__file__).with_name(filename)
    return Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8")))


def _openfoam_value(text: str, key: str) -> str | None:
    without_comments = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    without_comments = re.sub(r"//[^\r\n]*", "", without_comments)
    match = re.search(
        rf"(?:^|[;\r\n{{}}])\s*{re.escape(key)}\s+([^;{{}}]+);",
        without_comments,
    )
    return match.group(1).strip() if match else None


def _strip_openfoam_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\r\n]*", "", text)


def _named_brace_block(text: str, name: str) -> str | None:
    """Return one exact balanced OpenFOAM dictionary body."""
    blocks = _named_brace_blocks(text, name)
    return blocks[0] if len(blocks) == 1 else None


def _named_brace_blocks(text: str, name: str) -> list[str]:
    """Return every balanced dictionary with an exact name."""
    clean = _strip_openfoam_comments(text)
    pattern = re.compile(
        rf"(?<![A-Za-z0-9_.:+-])(?:{re.escape(name)}|\"{re.escape(name)}\")\s*\{{",
    )
    blocks: list[str] = []
    consumed_until = -1
    for match in pattern.finditer(clean):
        if match.start() < consumed_until:
            continue
        opening = clean.find("{", match.start(), match.end())
        depth = 0
        closing = None
        for index in range(opening, len(clean)):
            if clean[index] == "{":
                depth += 1
            elif clean[index] == "}":
                depth -= 1
                if depth == 0:
                    closing = index
                    break
        if closing is None:
            return []
        blocks.append(clean[opening + 1:closing])
        consumed_until = closing + 1
    return blocks


def _unique_openfoam_value(text: str, key: str) -> str | None:
    clean = _strip_openfoam_comments(text)
    matches = list(re.finditer(
        rf"(?m)(?:^|(?<=[;\r\n{{}}]))[ \t]*{re.escape(key)}\s+([^;{{}}]+);",
        clean,
    ))
    return matches[0].group(1).strip() if len(matches) == 1 else None


def _top_level_blocks(text: str) -> dict[str, str] | None:
    """Parse top-level named dictionaries from a brace body."""
    blocks: dict[str, str] = {}
    index = 0
    token = re.compile(r'\s*(?:"([^"\r\n]+)"|([A-Za-z0-9_.:+-]+))\s*\{')
    while index < len(text):
        if not text[index:].strip():
            break
        match = token.match(text, index)
        if match is None:
            return None
        name = match.group(1) or match.group(2)
        opening = text.find("{", match.start(), match.end())
        depth = 0
        closing = None
        for cursor in range(opening, len(text)):
            if text[cursor] == "{":
                depth += 1
            elif text[cursor] == "}":
                depth -= 1
                if depth == 0:
                    closing = cursor
                    break
        if closing is None or name in blocks:
            return None
        blocks[name] = text[opening + 1:closing]
        index = closing + 1
        while index < len(text) and text[index] in " \t\r\n;":
            index += 1
    return blocks


def _boundary_blocks(text: str) -> dict[str, str] | None:
    body = _named_brace_block(text, "boundaryField")
    return _top_level_blocks(body) if body is not None else None


def _is_zero_uniform_vector(value: str | None) -> bool:
    if value is None:
        return False
    match = re.fullmatch(
        rf"uniform\s*\(\s*({_NUMBER})\s+({_NUMBER})\s+({_NUMBER})\s*\)",
        value,
    )
    if match is None:
        return False
    try:
        return all(float(component) == 0.0 for component in match.groups())
    except ValueError:
        return False


def _heat_source_semantic(
    thermal: dict[str, Any],
    thermal_physical: dict[str, Any],
    fv_options_text: str,
    topo_set_text: str,
    mesh_digest: str,
    rho: float | None,
    cp: float | None,
) -> tuple[dict[str, Any], str] | None:
    sources = thermal.get("heat_sources")
    heat = thermal.get("heat")
    if (
        not isinstance(sources, list)
        or len(sources) != 1
        or not isinstance(sources[0], dict)
        or not isinstance(heat, dict)
        or rho is None
        or cp is None
    ):
        return None
    source = sources[0]
    source_id = source.get("source_id")
    source_name = source.get("name")
    mesh_patch_name = source.get("mesh_patch_name")
    source_power = _finite(
        source.get(
            "applied_convective_power_w", source.get("convective_power_w")
        ),
        positive=True,
    )
    declared_total = _finite(heat.get("applied_convective_power_w"), positive=True)
    if (
        not isinstance(source_id, str) or not source_id
        or not isinstance(source_name, str) or not source_name
        or not isinstance(mesh_patch_name, str) or not mesh_patch_name
        or source_power is None
        or declared_total is None
        or not math.isclose(source_power, 800.0, rel_tol=0.0, abs_tol=1e-9)
        or not math.isclose(declared_total, source_power, rel_tol=0.0, abs_tol=1e-9)
    ):
        return None

    try:
        expected_physical = cfd_physics.profile_free_thermal_input_snapshot(thermal)
    except (KeyError, TypeError, ValueError):
        return None
    if (
        thermal_physical != expected_physical
        or thermal.get("mesh_manifest_sha256") != mesh_digest
        or thermal_physical.get("mesh_manifest_sha256") != mesh_digest
        or thermal.get("engine") != "body_fitted_buoyant_urans"
        or thermal.get("airflow") != {"supply_cmh": 0.0, "exhaust_cmh": 0.0}
        or thermal.get("terminals") != []
        or thermal.get("condition_matrix") != {
            "flow_scale": 1.0, "gravity_scale": 1.0, "heat_scale": 1.0,
        }
        or thermal.get("initialisation") != {
            "mode": "zero_flow", "pressure_mapping": "none",
            "boussinesq_preconditioning_iterations": 0,
        }
    ):
        return None

    option_blocks = _top_level_blocks(_strip_openfoam_comments(fv_options_text))
    if option_blocks is None or set(option_blocks) != {"FoamFile", "heatSource0"}:
        return None
    expected_options = cfd_physics._thermal_fv_options(sources, thermal["settings"])
    expected_topology = cfd_physics._thermal_toposet_dict(sources)
    if (
        " ".join(_strip_openfoam_comments(fv_options_text).split())
        != " ".join(_strip_openfoam_comments(expected_options).split())
        or " ".join(_strip_openfoam_comments(topo_set_text).split())
        != " ".join(_strip_openfoam_comments(expected_topology).split())
    ):
        return None
    block = option_blocks["heatSource0"]
    injection_blocks = _named_brace_blocks(block, "injectionRateSuSp")
    injection = injection_blocks[0] if len(injection_blocks) == 1 else None
    raw_t = _unique_openfoam_value(injection, "T") if injection is not None else None
    match = re.fullmatch(
        rf"\(\s*({_NUMBER})\s+({_NUMBER})\s*\)", raw_t or ""
    )
    if (
        _unique_openfoam_value(block, "type") != "scalarSemiImplicitSource"
        or _unique_openfoam_value(block, "volumeMode") != "absolute"
        or _unique_openfoam_value(block, "selectionMode") != "cellZone"
        or _unique_openfoam_value(block, "cellZone") != "heatZone0"
        or match is None
    ):
        return None
    try:
        su, sp = (float(value) for value in match.groups())
    except ValueError:
        return None
    actual_power = su * rho * cp
    if (
        not math.isfinite(actual_power)
        or sp != 0.0
        or not math.isclose(actual_power, source_power, rel_tol=1e-9, abs_tol=1e-6)
    ):
        return None
    semantic = {
        "heat_sources": sources,
        "heat": heat,
        "fv_option": {
            "name": "heatSource0",
            "type": "scalarSemiImplicitSource",
            "volume_mode": "absolute",
            "selection_mode": "cellZone",
            "cell_zone": "heatZone0",
            "su": su,
            "sp": sp,
            "rho_kg_m3": rho,
            "cp_j_kg_k": cp,
            "actual_power_w": actual_power,
        },
        "topology": {
            "cell_set": "heatCells0",
            "cell_zone": "heatZone0",
            "mesh_patch_name": mesh_patch_name,
        },
    }
    return semantic, _canonical_json_sha256(semantic)


def _cell_rows(value: object) -> dict[str, tuple[float, float]] | None:
    if not isinstance(value, list) or not value:
        return None
    rows: dict[str, tuple[float, float]] = {}
    for item in value:
        if not isinstance(item, dict) or set(item) != {
                "id", "volume_m3", "temperature_k"}:
            return None
        cell_id = item.get("id")
        volume = _finite(item.get("volume_m3"), positive=True)
        temperature = _finite(item.get("temperature_k"))
        if (
            not isinstance(cell_id, str)
            or not cell_id
            or cell_id in rows
            or volume is None
            or temperature is None
        ):
            return None
        rows[cell_id] = (volume, temperature)
    return rows


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _blocked(*codes: str) -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "status": "BLOCKED",
        "blockers": _dedupe(list(codes)),
        "metrics": {},
        "evidence_sha256": {},
        "verification_scope": ["adiabatic_heat_box_energy_accounting"],
        "design_citable": False,
        "release_ready": False,
    }


def validate_heat_box_manifest(
    manifest_path: Path,
    projects_root: Path,
    evaluator_output_path: Path | None = None,
) -> dict[str, Any]:
    """Revalidate one fixed heat-box manifest without executing a solver.

    ``manifest_path`` and all producer references must resolve below
    ``projects_root`` without traversal/reparse aliases.  The optional output
    path is checked only for containment and source aliasing; this pure
    function does not write it.
    """
    root = _canonical_root(Path(projects_root))
    if root is None:
        return _blocked("PROJECTS_ROOT_INVALID")
    manifest_file = _safe_manifest(Path(manifest_path), root)
    if manifest_file is None:
        return _blocked("HEAT_BOX_MANIFEST_PATH_INVALID")
    canonical_manifest = _safe_existing_ref(CANONICAL_MANIFEST_PATH.as_posix(), root)
    if canonical_manifest is None or manifest_file != canonical_manifest:
        return _blocked("HEAT_BOX_MANIFEST_PATH_INVALID")
    manifest_snapshot = _snapshot_file(manifest_file)
    if manifest_snapshot is None:
        return _blocked("HEAT_BOX_ARTIFACT_READ_FAILED")
    manifest_bytes, manifest_digest, manifest_identity = manifest_snapshot
    manifest = _json_from_bytes(manifest_bytes)
    if manifest is None:
        return _blocked("HEAT_BOX_MANIFEST_MALFORMED")
    raw_artifacts = manifest.get("artifacts")
    if isinstance(raw_artifacts, dict) and any(
        not isinstance(ref, dict) or _relative_ref(ref.get("path")) is None
        for ref in raw_artifacts.values()
    ):
        return _blocked("HEAT_BOX_ARTIFACT_PATH_INVALID")
    try:
        schema_errors = list(_schema_validator().iter_errors(manifest))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _blocked("HEAT_BOX_SCHEMA_UNAVAILABLE")
    if schema_errors:
        return _blocked("HEAT_BOX_MANIFEST_SCHEMA_INVALID")

    case_ref = _relative_ref(manifest.get("case_path"))
    if case_ref is None:
        return _blocked("HEAT_BOX_CASE_PATH_INVALID")
    case_prefix = case_ref.as_posix().rstrip("/") + "/"
    artifacts: dict[str, Path] = {}
    artifact_bytes: dict[str, bytes] = {}
    artifact_hashes: dict[str, str] = {}
    artifact_identities: dict[Path, tuple[int, int, int]] = {}
    blockers: list[str] = []
    for name, ref in manifest["artifacts"].items():
        if not ref["path"].startswith(case_prefix):
            blockers.append("HEAT_BOX_ARTIFACT_OUTSIDE_CASE")
            continue
        resolved = _safe_existing_ref(ref["path"], root)
        if resolved is None:
            blockers.append("HEAT_BOX_ARTIFACT_PATH_INVALID")
            continue
        snapshot = _snapshot_file(resolved)
        if snapshot is None:
            blockers.append("HEAT_BOX_ARTIFACT_READ_FAILED")
            continue
        data, actual, identity_value = snapshot
        if actual != ref["sha256"]:
            blockers.append("HEAT_BOX_ARTIFACT_HASH_MISMATCH")
            continue
        artifacts[name] = resolved
        artifact_bytes[name] = data
        artifact_hashes[resolved.as_posix()] = actual
        artifact_identities[resolved] = identity_value
    if blockers:
        return _blocked(*blockers)
    sources = [manifest_file, *artifacts.values()]
    source_identities = {
        manifest_file: manifest_identity,
        **artifact_identities,
    }
    if (
        len(source_identities) != len(sources)
        or any(identity is None for identity in source_identities.values())
        or len(set(source_identities.values())) != len(source_identities)
    ):
        return _blocked("HEAT_BOX_ARTIFACT_PATH_INVALID")
    output_error = _output_blocker(evaluator_output_path, root, sources)
    if output_error:
        return _blocked(output_error)

    json_names = {
        "geometry", "surface_manifest", "mesh_manifest", "mesh_input", "thermal_input",
        "thermal_physical_input", "solver_identity", "run_manifest",
        "result_manifest", "cell_data",
    }
    payloads = {name: _json_from_bytes(artifact_bytes[name]) for name in json_names}
    if any(value is None for value in payloads.values()):
        return _blocked("HEAT_BOX_ARTIFACT_MALFORMED")

    def digest(name: str) -> str:
        return artifact_hashes[artifacts[name].as_posix()]
    geometry = payloads["geometry"] or {}
    surface = payloads["surface_manifest"] or {}
    mesh = payloads["mesh_manifest"] or {}
    mesh_input = payloads["mesh_input"] or {}
    thermal = payloads["thermal_input"] or {}
    thermal_physical = payloads["thermal_physical_input"] or {}
    identity = payloads["solver_identity"] or {}
    run = payloads["run_manifest"] or {}
    result = payloads["result_manifest"] or {}
    cells = payloads["cell_data"] or {}

    if (
        thermal.get("contract") != "thermal_input.v1"
        or thermal.get("validation_scope") != VALIDATION_SCOPE
        or thermal.get("terminals") != []
        or (thermal.get("assumptions") or {}).get("walls") != "adiabatic_screening"
    ):
        blockers.append("HEAT_BOX_THERMAL_INPUT_INVALID")
    if mesh.get("status") != "PASS":
        blockers.append("HEAT_BOX_MESH_INVALID")
    surface_regions = surface.get("regions")
    mesh_patches = mesh.get("patches")
    closed_roles = {"wall", "heat_source"}
    surface_pairs: list[tuple[str, str]] = []
    patch_pairs: list[tuple[str, str]] = []
    field_patch_names: list[str] = []
    if isinstance(surface_regions, list) and surface_regions:
        for region in surface_regions:
            if not isinstance(region, dict):
                surface_pairs = []
                break
            name = region.get("name")
            role = region.get("role")
            if not isinstance(name, str) or not name or role not in closed_roles:
                surface_pairs = []
                break
            surface_pairs.append((name, role))
    if isinstance(mesh_patches, list) and mesh_patches:
        for patch in mesh_patches:
            if not isinstance(patch, dict):
                patch_pairs = []
                field_patch_names = []
                break
            name = patch.get("name")
            patch_name = patch.get("mesh_patch_name")
            role = patch.get("role")
            if (
                not isinstance(name, str) or not name
                or not isinstance(patch_name, str) or not patch_name
                or role not in closed_roles
            ):
                patch_pairs = []
                field_patch_names = []
                break
            patch_pairs.append((name, role))
            field_patch_names.append(patch_name)
    if (
        surface.get("contract") != "surface_manifest.v1"
        or not surface_pairs
        or not patch_pairs
        or len(set(surface_pairs)) != len(surface_pairs)
        or len(set(patch_pairs)) != len(patch_pairs)
        or len({name for name, _role in surface_pairs}) != len(surface_pairs)
        or len({name for name, _role in patch_pairs}) != len(patch_pairs)
        or len(set(field_patch_names)) != len(field_patch_names)
        or set(surface_pairs) != set(patch_pairs)
        or thermal.get("wall_patches") != field_patch_names
    ):
        blockers.append("HEAT_BOX_BOUNDARY_NOT_CLOSED")
    if (
        geometry.get("contract") != "geometry.v2"
        or geometry.get("box_m") != [2.0, 2.0, 2.0]
        or mesh_input.get("contract") != "mesh_input.v1"
        or not math.isclose(
            _finite(mesh_input.get("cell_size_m"), positive=True) or math.inf,
            0.125,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        or not math.isclose(
            _finite((mesh.get("mesh") or {}).get("cell_size_m"), positive=True)
            or math.inf,
            0.125,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
    ):
        blockers.append("HEAT_BOX_CANONICAL_GEOMETRY_OR_MESH_INVALID")
    if (
        identity.get("contract") != "solver_identity.v1"
        or identity.get("executable") != "buoyantBoussinesqPimpleFoam"
        or not isinstance(identity.get("version"), str)
        or not identity.get("version")
        or not isinstance(identity.get("executable_sha256"), str)
        or not _HEX.fullmatch(identity.get("executable_sha256", ""))
    ):
        blockers.append("HEAT_BOX_SOLVER_IDENTITY_INVALID")

    control = _text_from_bytes(artifact_bytes["control_dict"])
    solution = _text_from_bytes(artifact_bytes["fv_solution"])
    schemes_text = _text_from_bytes(artifact_bytes["fv_schemes"])
    fv_options_text = _text_from_bytes(artifact_bytes["fv_options"])
    topo_set_text = _text_from_bytes(artifact_bytes["topo_set_dict"])
    u_text = _text_from_bytes(artifact_bytes["u_field"])
    t_text = _text_from_bytes(artifact_bytes["t_field"])
    p_rgh_text = _text_from_bytes(artifact_bytes["p_rgh_field"])
    if None in (
        control, solution, schemes_text, fv_options_text, topo_set_text,
        u_text, t_text, p_rgh_text,
    ):
        return _blocked("HEAT_BOX_ARTIFACT_READ_FAILED")
    assert control is not None and solution is not None and schemes_text is not None
    assert fv_options_text is not None
    assert topo_set_text is not None
    assert u_text is not None and t_text is not None and p_rgh_text is not None
    u_blocks = _boundary_blocks(u_text)
    t_blocks = _boundary_blocks(t_text)
    p_rgh_blocks = _boundary_blocks(p_rgh_text)
    expected_patch_names = set(field_patch_names)
    boundary_fields_valid = (
        bool(expected_patch_names)
        and u_blocks is not None
        and t_blocks is not None
        and p_rgh_blocks is not None
        and set(u_blocks) == expected_patch_names
        and set(t_blocks) == expected_patch_names
        and set(p_rgh_blocks) == expected_patch_names
    )
    if boundary_fields_valid:
        assert u_blocks is not None and t_blocks is not None and p_rgh_blocks is not None
        for patch_name in expected_patch_names:
            u_type = _unique_openfoam_value(u_blocks[patch_name], "type")
            no_slip = u_type == "noSlip" or (
                u_type == "fixedValue"
                and _is_zero_uniform_vector(
                    _unique_openfoam_value(u_blocks[patch_name], "value")
                )
            )
            if (
                not no_slip
                or _unique_openfoam_value(t_blocks[patch_name], "type")
                != "zeroGradient"
                or _unique_openfoam_value(p_rgh_blocks[patch_name], "type")
                != "fixedFluxPressure"
            ):
                boundary_fields_valid = False
                break
    if not boundary_fields_valid:
        blockers.append("HEAT_BOX_BOUNDARY_CONDITION_INVALID")
    settings = (
        thermal.get("settings") if isinstance(thermal.get("settings"), dict) else {}
    )
    raw_numerics = thermal.get("numerics")
    numerics = raw_numerics if isinstance(raw_numerics, dict) else {}
    try:
        expected_numerics = cfd_numerics.thermal_numerics_contract(mesh, settings)
    except (KeyError, TypeError, ValueError, cfd_numerics.NumericalInputError):
        expected_numerics = None
    if expected_numerics is None or raw_numerics != expected_numerics:
        blockers.append("HEAT_BOX_NUMERICS_INVALID")
    try:
        expected_control = cfd_physics._thermal_control_dict(
            settings, VALIDATION_SCOPE
        )
        expected_schemes = cfd_physics._thermal_fv_schemes(numerics)
        expected_solution = cfd_physics._thermal_fv_solution(settings, numerics)
    except (KeyError, TypeError, ValueError):
        expected_control = expected_schemes = expected_solution = ""
    if not (
        _production_text_equal(control, expected_control)
        and _production_text_equal(schemes_text, expected_schemes)
        and _production_text_equal(solution, expected_solution)
    ):
        blockers.append("HEAT_BOX_PRODUCTION_DICTIONARY_MISMATCH")
    fixed_delta_t = _finite(
        settings.get("thermal_initial_delta_t_s"), positive=True
    )
    fixed_max_delta_t = _finite(
        settings.get("thermal_max_delta_t_s"), positive=True
    )
    required_duration = _finite(settings.get("thermal_duration_s"), positive=True)
    adjust_time_step = _unique_openfoam_value(control, "adjustTimeStep")
    raw_control_delta_t = _unique_openfoam_value(control, "deltaT")
    raw_control_max_co = _unique_openfoam_value(control, "maxCo")
    raw_control_max_delta_t = _unique_openfoam_value(control, "maxDeltaT")
    raw_control_end_time = _unique_openfoam_value(control, "endTime")
    control_delta_t = _float_text(raw_control_delta_t, positive=True)
    control_max_co = _float_text(raw_control_max_co, positive=True)
    control_max_delta_t = _float_text(raw_control_max_delta_t, positive=True)
    control_end_time = _float_text(raw_control_end_time, positive=True)
    if (
        adjust_time_step != "no"
        or fixed_delta_t is None
        or fixed_max_delta_t is None
        or required_duration is None
        or control_delta_t is None
        or control_max_co is None
        or control_max_delta_t is None
        or control_end_time is None
        or control_max_co > 1.0
        or not math.isclose(control_delta_t, fixed_delta_t, rel_tol=0.0, abs_tol=1e-12)
        or not math.isclose(
            control_max_delta_t, fixed_max_delta_t, rel_tol=0.0, abs_tol=1e-12
        )
        or not math.isclose(
            control_end_time, required_duration, rel_tol=0.0, abs_tol=1e-12
        )
    ):
        blockers.append("HEAT_BOX_FIXED_DT_REQUIRED")
    p_ref_cell = _unique_openfoam_value(solution, "pRefCell")
    p_ref_value = _unique_openfoam_value(solution, "pRefValue")
    try:
        pressure_reference_ok = (
            p_ref_cell is not None
            and int(p_ref_cell) >= 0
            and p_ref_value is not None
            and math.isfinite(float(p_ref_value))
        )
    except (TypeError, ValueError):
        pressure_reference_ok = False
    if not pressure_reference_ok:
        blockers.append("HEAT_BOX_PRESSURE_REFERENCE_MISSING")
    ddt_blocks = _named_brace_blocks(schemes_text, "ddtSchemes")
    ddt_default = (
        _unique_openfoam_value(ddt_blocks[0], "default")
        if len(ddt_blocks) == 1 else None
    )
    if (
        None in (
            adjust_time_step, raw_control_delta_t, raw_control_max_co,
            raw_control_max_delta_t, raw_control_end_time,
            p_ref_cell, p_ref_value, ddt_default,
        )
        or not boundary_fields_valid
    ):
        blockers.append("HEAT_BOX_OPENFOAM_DICTIONARY_AMBIGUOUS")

    heat = thermal.get("heat") if isinstance(thermal.get("heat"), dict) else {}
    rho = _finite(settings.get("air_density_kg_m3"), positive=True)
    cp = _finite(settings.get("air_specific_heat_j_kg_k"), positive=True)
    duration = _finite(settings.get("thermal_duration_s"), positive=True)
    reference_t = _finite(settings.get("reference_temperature_k"))
    beta = _finite(settings.get("thermal_expansion_coefficient_1_k"), positive=True)
    power = _finite(heat.get("applied_convective_power_w"), positive=True)
    mesh_volume = _finite(mesh.get("occ_volume_m3"), positive=True)
    heat_source_evidence = _heat_source_semantic(
        thermal,
        thermal_physical,
        fv_options_text,
        topo_set_text,
        digest("mesh_manifest"),
        rho,
        cp,
    )
    heat_source_semantic_hash = (
        heat_source_evidence[1] if heat_source_evidence is not None else None
    )
    thermal_sources = thermal.get("heat_sources")
    source_patch_binding_ok = False
    if (
        isinstance(thermal_sources, list)
        and len(thermal_sources) == 1
        and isinstance(thermal_sources[0], dict)
        and isinstance(mesh_patches, list)
    ):
        source_row = thermal_sources[0]
        source_patch_binding_ok = any(
            isinstance(patch, dict)
            and patch.get("name") == source_row.get("name")
            and patch.get("role") == "heat_source"
            and patch.get("mesh_patch_name") == source_row.get("mesh_patch_name")
            for patch in mesh_patches
        )
    if heat_source_evidence is None or not source_patch_binding_ok:
        blockers.append("HEAT_BOX_HEAT_SOURCE_INVALID")
    initial_time = _finite(cells.get("initial_time_s"))
    final_time = _finite(cells.get("final_time_s"), positive=True)
    initial_cells = _cell_rows(cells.get("initial_cells"))
    final_cells = _cell_rows(cells.get("final_cells"))
    if None in (rho, cp, duration, reference_t, beta, power, mesh_volume):
        blockers.append("HEAT_BOX_PHYSICAL_INPUT_INVALID")
    elif (
        not math.isclose(duration, 60.0, rel_tol=0.0, abs_tol=1e-12)
        or not math.isclose(power, 800.0, rel_tol=0.0, abs_tol=1e-12)
        or not math.isclose(mesh_volume, 8.0, rel_tol=0.0, abs_tol=1e-12)
        or fixed_delta_t is None
        or not math.isclose(fixed_delta_t, 0.02, rel_tol=0.0, abs_tol=1e-12)
        or ddt_default != "Euler"
    ):
        blockers.append("HEAT_BOX_CANONICAL_INPUT_INVALID")
    if (
        cells.get("contract") != "heat_box_cells.v1"
        or initial_time is None
        or not math.isclose(initial_time, 0.0, rel_tol=0.0, abs_tol=1e-12)
        or final_time is None
        or initial_cells is None
        or final_cells is None
        or set(initial_cells or {}) != set(final_cells or {})
    ):
        blockers.append("HEAT_BOX_CELL_DATA_INVALID")

    run_input = run.get("input") if isinstance(run.get("input"), dict) else {}
    system_hashes = run_input.get("system") if isinstance(run_input.get("system"), dict) else {}
    provenance = (
        run_input.get("numerical_provenance")
        if isinstance(run_input.get("numerical_provenance"), dict)
        else {}
    )
    expected_system_hashes = {
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
    thermal_numerics = (
        thermal.get("numerics") if isinstance(thermal.get("numerics"), dict) else {}
    )
    try:
        if list(_artifact_schema_validator(
                "run_manifest.v1.schema.json").iter_errors(run)):
            blockers.append("HEAT_BOX_RUN_MANIFEST_SCHEMA_INVALID")
    except (OSError, UnicodeError, json.JSONDecodeError):
        blockers.append("HEAT_BOX_RUN_MANIFEST_SCHEMA_UNAVAILABLE")
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
        blockers.append("HEAT_BOX_RUN_INCOMPLETE_OR_UNBOUND")
    if (
        run.get("contract") != "run_manifest.v1"
        or run.get("engine") != "body_fitted_buoyant_urans"
        or run_input.get("thermal_input_sha256") != digest("thermal_input")
        or run_input.get("thermal_physical_input_sha256")
        != digest("thermal_physical_input")
        or run_input.get("fv_options_sha256") != digest("fv_options")
        or run_input.get("topo_set_dict_sha256") != digest("topo_set_dict")
        or run_input.get("heat_source_semantic_sha256")
        != heat_source_semantic_hash
        or system_hashes != expected_system_hashes
        or provenance.get("source") != "thermal_initial_input"
        or provenance.get("thermal_input_sha256") != digest("thermal_input")
        or provenance.get("thermal_restart_input_sha256") is not None
        or effective_settings != settings
        or effective_numerics != thermal_numerics
        or provenance.get("effective_settings_sha256")
        != _canonical_json_sha256(effective_settings)
        or provenance.get("effective_numerics_sha256")
        != _canonical_json_sha256(effective_numerics)
        or provenance.get("system") != expected_system_hashes
        or provenance.get("expected_system") != expected_system_hashes
    ):
        blockers.append("HEAT_BOX_RUN_CROSS_REFERENCE_INVALID")
    source = result.get("source") if isinstance(result.get("source"), dict) else {}
    try:
        if list(_artifact_schema_validator(
                "result_manifest.v1.schema.json").iter_errors(result)):
            blockers.append("HEAT_BOX_RESULT_MANIFEST_SCHEMA_INVALID")
    except (OSError, UnicodeError, json.JSONDecodeError):
        blockers.append("HEAT_BOX_RESULT_MANIFEST_SCHEMA_UNAVAILABLE")
    if (
        result.get("contract") != "result_manifest.v1"
        or result.get("engine") != "body_fitted_openfoam_vtu"
        or result.get("thermal_input_sha256") != digest("thermal_input")
        or result.get("run_manifest_sha256") != digest("run_manifest")
        or source.get("path") != manifest["artifacts"]["cell_data"]["path"]
        or source.get("sha256") != digest("cell_data")
        or source.get("format") != "heat_box_cells.v1"
    ):
        blockers.append("HEAT_BOX_RESULT_CROSS_REFERENCE_INVALID")

    log_text = _text_from_bytes(artifact_bytes["solver_log"])
    if log_text is None:
        return _blocked("HEAT_BOX_ARTIFACT_READ_FAILED")
    times = [float(value) for value in re.findall(rf"(?m)^Time =\s*({_NUMBER})", log_text)]
    courant_values = [float(value) for value in re.findall(
        rf"Courant Number mean:\s*{_NUMBER}\s+max:\s*({_NUMBER})", log_text, re.I
    )]
    continuity_values = [float(value) for value in re.findall(
        rf"time step continuity errors\s*:\s*sum local =\s*{_NUMBER}"
        rf", global =\s*({_NUMBER}), cumulative =\s*{_NUMBER}",
        log_text,
        re.I,
    )]
    if (
        not times
        or fixed_delta_t is None
        or not all(math.isclose(
            current - previous,
            fixed_delta_t,
            rel_tol=0.0,
            abs_tol=1e-9,
        ) for previous, current in zip([0.0, *times[:-1]], times))
        or duration is None
        or not math.isclose(times[-1], duration, rel_tol=0.0, abs_tol=1e-9)
        or not re.search(r"(?m)^End\s*$", log_text)
        or re.search(r"FOAM FATAL|Segmentation fault|Floating point exception", log_text, re.I)
    ):
        blockers.append("HEAT_BOX_FIXED_DT_HISTORY_INVALID")
    if not courant_values or not continuity_values:
        blockers.append("HEAT_BOX_SOLVER_NUMERICAL_EVIDENCE_MISSING")

    metrics: dict[str, float] = {}
    if not blockers and initial_cells is not None and final_cells is not None:
        ordered_ids = sorted(initial_cells)
        if any(not math.isclose(
                initial_cells[cell_id][0], final_cells[cell_id][0],
                rel_tol=0.0, abs_tol=1e-12) for cell_id in ordered_ids):
            blockers.append("HEAT_BOX_CELL_VOLUME_DRIFT")
        else:
            volume = sum(initial_cells[cell_id][0] for cell_id in ordered_ids)
            if mesh_volume is None or not math.isclose(
                    volume, mesh_volume, rel_tol=1e-9, abs_tol=1e-12):
                blockers.append("HEAT_BOX_VOLUME_MISMATCH")
            elif None not in (rho, cp, duration, power, reference_t, beta, final_time):
                initial_mean = sum(
                    initial_cells[cell_id][0] * initial_cells[cell_id][1]
                    for cell_id in ordered_ids
                ) / volume
                final_mean = sum(
                    final_cells[cell_id][0] * final_cells[cell_id][1]
                    for cell_id in ordered_ids
                ) / volume
                analytic_delta = power * duration / (rho * cp * volume)
                simulated_delta = final_mean - initial_mean
                relative_error = abs(simulated_delta - analytic_delta) / abs(analytic_delta)
                stored = rho * cp * sum(
                    initial_cells[cell_id][0]
                    * (final_cells[cell_id][1] - initial_cells[cell_id][1])
                    for cell_id in ordered_ids
                )
                closure = stored / (power * duration)
                fluxes = cells.get("boundary_phi_m3_s")
                if (
                    not isinstance(fluxes, list)
                    or not fluxes
                    or any(_finite(value) is None for value in fluxes)
                ):
                    blockers.append("HEAT_BOX_BOUNDARY_FLUX_INVALID")
                    net_flux = math.nan
                else:
                    numeric_fluxes = [float(value) for value in fluxes]
                    net_flux = sum(numeric_fluxes)
                    if max(abs(value) for value in numeric_fluxes) > 1e-9:
                        blockers.append("HEAT_BOX_NET_BOUNDARY_FLUX_LIMIT")
                maximum_delta = max(
                    abs(temperature - reference_t)
                    for rows in (initial_cells, final_cells)
                    for _volume, temperature in rows.values()
                )
                beta_delta = beta * maximum_delta
                peak_courant = max(courant_values)
                max_continuity = max(abs(value) for value in continuity_values)
                metrics = {
                    "initial_volume_mean_temperature_k": initial_mean,
                    "final_volume_mean_temperature_k": final_mean,
                    "analytic_delta_temperature_k": analytic_delta,
                    "simulated_delta_temperature_k": simulated_delta,
                    "mean_temperature_relative_error": relative_error,
                    "storage_energy_closure_ratio": closure,
                    "peak_courant": peak_courant,
                    "max_global_continuity": max_continuity,
                    "net_boundary_volume_flux_m3_s": net_flux,
                    "maximum_absolute_boundary_volume_flux_m3_s": (
                        max(abs(value) for value in numeric_fluxes)
                        if isinstance(fluxes, list) and fluxes
                        and all(_finite(value) is not None for value in fluxes)
                        else math.nan
                    ),
                    "boussinesq_beta_delta": beta_delta,
                    "cell_volume_sum_m3": volume,
                    "physical_time_s": duration,
                }
                initial_declared = _finite(settings.get("initial_temperature_k"))
                if initial_declared is None or not math.isclose(
                        initial_mean, initial_declared, rel_tol=0.0, abs_tol=1e-9):
                    blockers.append("HEAT_BOX_INITIAL_TEMPERATURE_MISMATCH")
                if relative_error > 0.01:
                    blockers.append("HEAT_BOX_MEAN_TEMPERATURE_ERROR_LIMIT")
                if not 0.99 <= closure <= 1.01:
                    blockers.append("HEAT_BOX_STORAGE_CLOSURE_LIMIT")
                if peak_courant > 1.0:
                    blockers.append("HEAT_BOX_COURANT_LIMIT")
                if max_continuity > 1e-6:
                    blockers.append("HEAT_BOX_CONTINUITY_LIMIT")
                if not math.isfinite(net_flux) or abs(net_flux) > 1e-9:
                    blockers.append("HEAT_BOX_NET_BOUNDARY_FLUX_LIMIT")
                if beta_delta > 0.1:
                    blockers.append("HEAT_BOX_BOUSSINESQ_RANGE_LIMIT")
                if not math.isclose(final_time, duration, rel_tol=0.0, abs_tol=1e-9):
                    blockers.append("HEAT_BOX_PHYSICAL_TIME_MISMATCH")
                result_time = _finite(result.get("time_s"), positive=True)
                if result_time is None or not math.isclose(
                        result_time, duration, rel_tol=0.0, abs_tol=1e-9):
                    blockers.append("HEAT_BOX_RESULT_TIME_MISMATCH")

    initial_hashes = {manifest_file.as_posix(): manifest_digest, **artifact_hashes}
    try:
        changed = any(
            not _lexical_chain_safe(Path(path_text))
            or _file_identity(Path(path_text))
            != source_identities[Path(path_text)]
            or _sha256(Path(path_text)) != expected_digest
            for path_text, expected_digest in initial_hashes.items()
        )
    except OSError:
        changed = True
    if changed:
        return _blocked("ARTIFACT_CHANGED_DURING_VALIDATION")
    evidence_hashes = {
        Path(path_text).relative_to(root).as_posix(): expected_digest
        for path_text, expected_digest in initial_hashes.items()
    }
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
        "metrics": metrics,
        "evidence_sha256": dict(sorted(evidence_hashes.items())),
        "verification_scope": ["adiabatic_heat_box_energy_accounting"],
        "design_citable": False,
        "release_ready": False,
    }
