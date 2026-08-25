"""Fail-closed recomputation of immutable CFD case evidence.

Only current raw artifacts are authority.  Status strings in prior evidence,
reports, caches, and producer manifests never substitute for the path, schema,
hash, semantic, and cross-reference checks performed here.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile
from typing import Any

from jsonschema import Draft202012Validator

import cfd_result_gate
from cfd_status_catalog import EVIDENCE_CHECKS
import field_acceptance
from geometry_v2 import validate_geometry_v2


CONTRACT = "case_evidence.v1"
ZERO_SHA256 = "0" * 64
CORE_CHECKS = EVIDENCE_CHECKS[:5]
GENERATED_PARTS = {
    "_release_evidence", "report", "reports", "cache", "caches",
    ".pytest_cache", "__pycache__", "temp", "tmp", "staging", "backup",
    "recovery",
}
SCHEMAS = {
    "evidence": "case_evidence.v1.schema.json",
    "geometry": "geometry.v2.schema.json",
    "surface": "surface_manifest.v1.schema.json",
    "mesh": "mesh_manifest.v1.schema.json",
    "run": "run_manifest.v1.schema.json",
    "progress": "thermal_progress.v1.schema.json",
    "result": "result_manifest.v1.schema.json",
    "gci": "grid_convergence.v3.schema.json",
    "field": "field_dxf_acceptance.v1.schema.json",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _schema(name: str) -> Draft202012Validator:
    path = Path(__file__).resolve().with_name(SCHEMAS[name])
    return Draft202012Validator(json.loads(path.read_text(encoding="utf-8")))


def _schema_ok(name: str, value: Any) -> bool:
    return isinstance(value, dict) and not list(_schema(name).iter_errors(value))


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return None


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _contained(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _has_raw_dot_segment(value: Any) -> bool:
    try:
        raw = os.fsdecode(os.fspath(value))
    except (TypeError, ValueError):
        return False
    return any(part in {".", ".."} for part in raw.replace("\\", "/").split("/"))


def _path_roots(path: Path, root: Path) -> tuple[Path, Path] | None:
    """Return the lexical root spelling and its canonical identity."""
    # A Path has already discarded benign "." spelling, but retains "..".
    if any(part in {".", ".."} for part in (*path.parts, *root.parts)):
        return None
    try:
        lexical = path.absolute()
        root_input = root.absolute()
        canonical_root = root_input.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    candidates: list[Path] = []
    try:
        lexical.relative_to(root_input)
        candidates.append(root_input)
    except ValueError:
        candidates.extend((lexical, *lexical.parents))
    for candidate in candidates:
        try:
            relative = lexical.relative_to(candidate)
            if any(part in {".", ".."} for part in relative.parts):
                return None
            if os.path.samefile(candidate, canonical_root):
                return candidate, canonical_root
        except (OSError, ValueError):
            continue
    return None


def _no_reparse_chain(path: Path, root: Path) -> bool:
    try:
        relative = path.absolute().relative_to(root.absolute())
    except ValueError:
        return False
    current = root
    if _is_reparse(current):
        return False
    for part in relative.parts:
        current = current / part
        if current.exists() and _is_reparse(current):
            return False
    return True


def _safe_existing(path: Path, root: Path, *, directory: bool = False) -> Path | None:
    try:
        lexical = path.absolute()
        roots = _path_roots(lexical, root)
        if roots is None:
            return None
        lexical_root, canonical_root = roots
        if not _no_reparse_chain(lexical, lexical_root):
            return None
        resolved = lexical.resolve(strict=True)
        if not _contained(resolved, canonical_root):
            return None
        if directory and not resolved.is_dir():
            return None
        if not directory and not resolved.is_file():
            return None
        return resolved
    except (OSError, RuntimeError, ValueError):
        return None


def _stored_path(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root).as_posix()


def _lexical_ref(value: Any) -> PurePosixPath | None:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or _has_raw_dot_segment(value)
    ):
        return None
    if value.startswith("/") or (len(value) >= 2 and value[1] == ":"):
        return None
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path


def _resolve_ref(value: Any, root: Path) -> Path | None:
    relative = _lexical_ref(value)
    if relative is None:
        return None
    return _safe_existing(root.joinpath(*relative.parts), root)


def _resolve_field_record_for_tracking(value: Any, root: Path) -> Path | None:
    """Mirror legacy field path spelling only to protect contained read sources."""
    if _has_raw_dot_segment(value):
        return None
    raw = Path(str(value or ""))
    candidate = raw if raw.is_absolute() else root / raw
    return _safe_existing(candidate, root)


def _resolve_raw(value: Any, root: Path, *, directory: bool = False) -> Path | None:
    if not isinstance(value, str) or not value or _has_raw_dot_segment(value):
        return None
    raw = Path(value)
    if raw.is_absolute():
        candidate = raw
    else:
        if "\\" in value:
            return None
        relative = _lexical_ref(value)
        if relative is None:
            return None
        candidate = root.joinpath(*relative.parts)
    return _safe_existing(candidate, root, directory=directory)


def _generated(path: Path, root: Path) -> bool:
    try:
        parts = [part.lower() for part in path.relative_to(root).parts]
    except ValueError:
        return True
    name = path.name.lower()
    return (
        any(part in GENERATED_PARTS for part in parts)
        or any(".staging-" in part or ".backup." in part for part in parts)
        or name.startswith("case_evidence")
        or name.endswith(".html")
    )


def _safe_child(parent: Path, value: Any, root: Path) -> Path | None:
    relative = _lexical_ref(value)
    if relative is None:
        return None
    candidate = parent.joinpath(*relative.parts)
    safe = _safe_existing(candidate, root)
    if safe is None or not _contained(safe, parent.resolve()):
        return None
    return safe


def _direct_candidates(root: Path, namespace: str, filename: str) -> tuple[list[Path], bool]:
    base = root / namespace
    if not base.exists():
        return [], False
    safe_base = _safe_existing(base, root, directory=True)
    if safe_base is None:
        return [], True
    paths: list[Path] = []
    unsafe = False
    try:
        children = sorted(base.iterdir(), key=lambda item: item.name)
    except OSError:
        return [], True
    for child in children:
        if _is_reparse(child):
            unsafe = True
            continue
        candidate = child / filename
        if not candidate.exists():
            continue
        safe = _safe_existing(candidate, root)
        if safe is None:
            unsafe = True
        else:
            paths.append(safe)
    return paths, unsafe


def _error(errors: list[dict[str, str]], code: str, detail: str,
           evidence_ref: str | None = None) -> None:
    row = {"code": code, "detail": detail}
    if evidence_ref:
        row["evidence_ref"] = evidence_ref
    if row not in errors:
        errors.append(row)


def _track(source_paths: set[Path], *paths: Path | None) -> None:
    """Record every real raw file consulted during recomputation."""
    for path in paths:
        if path is not None and path.is_file():
            source_paths.add(path.resolve())


def _link(path: Path, root: Path, contract: str) -> dict[str, str]:
    digest = _sha256(path) if path.is_file() else ZERO_SHA256
    return {"path": _stored_path(path, root), "sha256": digest, "contract": contract}


def _check(check_id: str, status: str, reasons: list[str], refs: list[str]) -> dict:
    return {
        "id": check_id,
        "status": status,
        "reason_codes": list(dict.fromkeys(reasons)),
        "evidence_refs": list(dict.fromkeys(refs)),
    }


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _progress_semantic_ok(progress: dict) -> bool:
    latest = _finite(progress.get("latest_time_s"))
    completed = _finite(progress.get("completed_duration_s"))
    required = _finite(progress.get("required_duration_s"))
    remaining = _finite(progress.get("remaining_duration_s"))
    flow_time = _finite(progress.get("flow_through_time_s"))
    fraction = _finite(progress.get("flow_through_fraction"))
    total_runtime = _finite(progress.get("total_runtime_seconds"))
    runs = progress.get("runs")
    if (None in (latest, completed, required, remaining, flow_time, fraction, total_runtime)
            or min(latest, completed, required, remaining, flow_time, fraction, total_runtime) < 0
            or abs(completed - latest) > 1e-9
            or abs(remaining - max(0.0, required - completed)) > 1e-9
            or (flow_time > 0 and abs(fraction - completed / flow_time) > 1e-9)
            or not isinstance(runs, list) or not runs
            or progress.get("runs_completed") != len(runs)):
        return False
    runtime_sum = 0.0
    prior_end = None
    for row in runs:
        if not isinstance(row, dict):
            return False
        start = _finite(row.get("start_time_s"))
        end = _finite(row.get("end_time_s"))
        duration = _finite(row.get("simulated_duration_s"))
        runtime = _finite(row.get("runtime_seconds"))
        if (None in (start, end, duration, runtime) or min(start, end, duration, runtime) < 0
                or end < start or abs(duration - (end - start)) > 1e-9
                or (prior_end is not None and start < prior_end)):
            return False
        prior_end = end
        runtime_sum += runtime
    return abs(runtime_sum - total_runtime) <= 1e-9 and abs(prior_end - latest) <= 1e-9


def _summary_ok(value: dict | None) -> bool:
    if not isinstance(value, dict):
        return False
    bounds = value.get("bounds_m")
    fields = value.get("fields")
    return (
        value.get("contract") == "body_fitted_summary.v1"
        and _finite(value.get("time_s")) is not None
        and isinstance(value.get("cell_count"), int) and value["cell_count"] > 0
        and isinstance(bounds, dict)
        and all(isinstance(bounds.get(key), list) and len(bounds[key]) == 3
                for key in ("minimum", "maximum"))
        and isinstance(fields, dict) and {"T", "U"}.issubset(fields)
        and isinstance(value.get("temperature"), dict)
        and isinstance(value.get("velocity"), dict)
    )


def _surface_validation(path: Path, payload: dict | None, geometry: Path | None,
                        root: Path, errors: list[dict[str, str]],
                        source_paths: set[Path]) -> bool:
    _track(source_paths, path)
    ok = True
    if not _schema_ok("surface", payload):
        _error(errors, "SURFACE_SCHEMA_INVALID", "surface manifest schema is invalid", "surface")
        ok = False
    payload = payload or {}
    air = payload.get("air_volume") if isinstance(payload.get("air_volume"), dict) else {}
    topology = payload.get("topology") if isinstance(payload.get("topology"), dict) else {}
    if not (
        payload.get("contract") == "surface_manifest.v1"
        and air.get("valid") is True and air.get("solid_count") == 1
        and topology.get("watertight") is True
        and all(topology.get(key) == 0 for key in (
            "open_edges", "non_manifold_edges", "duplicate_triangles"
        ))
    ):
        _error(errors, "SURFACE_SEMANTIC_INVALID", "surface topology/solid gate failed", "surface")
        ok = False
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    source_geometry = _resolve_raw(source.get("geometry_path"), root)
    if source_geometry is None:
        _error(errors, "PATH_ESCAPE", "surface geometry path is unsafe or unreadable", "geometry")
        ok = False
    elif _generated(source_geometry, root):
        _error(errors, "GENERATED_SOURCE_EXCLUDED", "generated namespace cannot supply geometry", "geometry")
        ok = False
    elif geometry is None or source_geometry != geometry:
        _error(errors, "SURFACE_GEOMETRY_PATH_MISMATCH", "surface does not bind selected geometry", "surface")
        ok = False
    elif source.get("geometry_sha256") != _sha256(geometry):
        _error(errors, "GEOMETRY_HASH_MISMATCH", "surface geometry hash is not current", "geometry")
        ok = False
    outputs = payload.get("outputs") if isinstance(payload.get("outputs"), dict) else {}
    for path_key, hash_key in (("multi_region_stl", "stl_sha256"), ("brep", "brep_sha256")):
        declared_path = outputs.get(path_key)
        if _lexical_ref(declared_path) is None:
            _error(errors, "PATH_ESCAPE", f"surface output {path_key} path is unsafe", "surface")
            ok = False
            continue
        artifact = _safe_child(path.parent, declared_path, root)
        if artifact is None:
            _error(errors, "MISSING_ARTIFACT", f"surface output {path_key} is missing or unsafe", "surface")
            ok = False
        else:
            _track(source_paths, artifact)
        if artifact is not None and outputs.get(hash_key) != _sha256(artifact):
            _error(errors, "SURFACE_OUTPUT_HASH_MISMATCH", f"surface output {path_key} is stale", "surface")
            ok = False
    return ok


def _mesh_validation(path: Path, payload: dict | None, surface: Path | None,
                     solver_case: Path, root: Path,
                     errors: list[dict[str, str]],
                     source_paths: set[Path]) -> bool:
    _track(source_paths, path)
    ok = True
    if not _schema_ok("mesh", payload):
        _error(errors, "MESH_SCHEMA_INVALID", "mesh manifest schema is invalid", "mesh")
        ok = False
    payload = payload or {}
    mesh_state = payload.get("mesh") if isinstance(payload.get("mesh"), dict) else {}
    strict = payload.get("strict_diagnostics") if isinstance(payload.get("strict_diagnostics"), dict) else {}
    surface_state = payload.get("surface") if isinstance(payload.get("surface"), dict) else {}
    if not (
        payload.get("contract") == "mesh_manifest.v1" and payload.get("status") == "PASS"
        and surface_state.get("closed") is True
        and mesh_state.get("mesh_ok") is True and mesh_state.get("fatal") is False
        and not mesh_state.get("failed_checks") and not mesh_state.get("concave_cells")
        and strict.get("mesh_ok") is True and strict.get("fatal") is False
        and not strict.get("failed_checks") and not strict.get("concave_cells")
    ):
        _error(errors, "MESH_GATE_BLOCKED", "mesh or strict mesh checks failed", "mesh")
        ok = False
    copied_surface = _safe_existing(path.parent / "surface_manifest.json", root)
    mesh_input_path = _safe_existing(path.parent / "mesh_input.json", root)
    mesh_input = _read_json(mesh_input_path) if mesh_input_path else None
    _track(source_paths, copied_surface, mesh_input_path)
    if (surface is None or copied_surface is None
            or _sha256(copied_surface) != _sha256(surface)):
        _error(errors, "MESH_SURFACE_CHAIN_MISMATCH", "mesh surface copy is not canonical", "mesh")
        ok = False
    if not isinstance(mesh_input, dict) or mesh_input.get("contract") != "mesh_input.v1":
        _error(errors, "MESH_INPUT_INVALID", "mesh input contract is invalid", "mesh")
        ok = False
    else:
        copied_sha = _sha256(copied_surface) if copied_surface else None
        if mesh_input.get("surface_manifest_sha256") != copied_sha:
            _error(errors, "MESH_SURFACE_CHAIN_MISMATCH", "mesh input surface hash is stale", "mesh")
            ok = False
        stl = _safe_existing(path.parent / "constant" / "triSurface" / "air_volume_regions.stl", root)
        _track(source_paths, stl)
        if stl is None or mesh_input.get("surface_stl_sha256") != _sha256(stl):
            _error(errors, "MESH_INPUT_HASH_MISMATCH", "mesh input STL hash is stale", "mesh")
            ok = False
    declared = payload.get("input") if isinstance(payload.get("input"), dict) else {}
    if (copied_surface is None or declared.get("surface_manifest_sha256") != _sha256(copied_surface)
            or mesh_input_path is None or declared.get("mesh_input_sha256") != _sha256(mesh_input_path)):
        _error(errors, "MESH_INPUT_HASH_MISMATCH", "mesh manifest input hashes are stale", "mesh")
        ok = False
    for name in ("surface_manifest.json", "mesh_input.json", "mesh_manifest.json"):
        canonical = path.parent / name
        solver_copy = _safe_existing(solver_case / name, root)
        _track(source_paths, canonical, solver_copy)
        if solver_copy is None or _sha256(solver_copy) != _sha256(canonical):
            _error(errors, "SOLVER_MESH_CHAIN_MISMATCH", f"solver copy {name} is stale", "mesh")
            ok = False
    return ok


def _result_validation(case: Path, result: dict | None, run_path: Path,
                       mesh_path: Path, thermal_path: Path, root: Path,
                       errors: list[dict[str, str]],
                       source_paths: set[Path]) -> bool:
    _track(source_paths, case / "result_manifest.json")
    ok = True
    if not _schema_ok("result", result):
        _error(errors, "RESULT_SCHEMA_INVALID", "result manifest schema is invalid", "result")
        ok = False
    result = result or {}
    if result.get("contract") != "result_manifest.v1" or result.get("engine") != "body_fitted_openfoam_vtu":
        _error(errors, "RESULT_CONTRACT_INVALID", "result contract/engine is invalid", "result")
        ok = False
    for key, source_path, code in (
        ("run_manifest_sha256", run_path, "RESULT_RUN_HASH_MISMATCH"),
        ("mesh_manifest_sha256", mesh_path, "RESULT_MESH_HASH_MISMATCH"),
        ("thermal_input_sha256", thermal_path, "RESULT_THERMAL_HASH_MISMATCH"),
    ):
        if not source_path.is_file() or result.get(key) != _sha256(source_path):
            _error(errors, code, f"result {key} is stale", "result")
            ok = False
    source = result.get("source") if isinstance(result.get("source"), dict) else {}
    source_path = _safe_child(case, source.get("path"), root)
    summary_path = _safe_child(case, result.get("summary_path"), root)
    summary = _read_json(summary_path) if summary_path else None
    _track(source_paths, source_path, summary_path)
    if (source_path is None or source.get("sha256") != _sha256(source_path)
            or summary_path is None or result.get("summary_sha256") != _sha256(summary_path)
            or not _summary_ok(summary)):
        _error(errors, "RESULT_ARTIFACT_HASH_MISMATCH", "result source/summary is stale or invalid", "result")
        ok = False
    slices = result.get("slices") if isinstance(result.get("slices"), list) else []
    axes = {item.get("axis") for item in slices if isinstance(item, dict)}
    if len(slices) < 3 or axes != {"x", "y", "z"}:
        _error(errors, "RESULT_SLICES_INVALID", "result requires unique x/y/z slices", "result")
        ok = False
    for item in slices:
        if not isinstance(item, dict):
            ok = False
            continue
        path = _safe_child(case, item.get("path"), root)
        _track(source_paths, path)
        payload = _read_json(path) if path else None
        if (path is None or item.get("sha256") != _sha256(path)
                or not isinstance(payload, dict) or payload.get("axis") != item.get("axis")
                or not isinstance(payload.get("samples"), list)
                or payload.get("sample_count") != len(payload["samples"])):
            _error(errors, "RESULT_ARTIFACT_HASH_MISMATCH", "result slice is stale or invalid", "result")
            ok = False
    return ok


def _select_matching_copy(root: Path, namespace: str, filename: str,
                          copied: Path | None, artifact: str,
                          errors: list[dict[str, str]],
                          source_paths: set[Path]) -> Path | None:
    candidates, unsafe = _direct_candidates(root, namespace, filename)
    _track(source_paths, copied, *candidates)
    if unsafe:
        _error(errors, "PATH_ESCAPE", f"unsafe {artifact} candidate", artifact)
        return None
    if copied is None:
        _error(errors, "MISSING_ARTIFACT", f"missing solver {artifact} copy", artifact)
        return None
    copied_hash = _sha256(copied)
    matches = [path for path in candidates if _sha256(path) == copied_hash]
    if not matches:
        _error(errors, "MISSING_ARTIFACT", f"no canonical current {artifact} artifact", artifact)
        return None
    if len(matches) > 1:
        _error(errors, f"AMBIGUOUS_{artifact.upper()}_EVIDENCE",
               f"multiple canonical {artifact} artifacts match", artifact)
        return None
    return matches[0]


def _gci(case: Path, root: Path, gci_root: Path | None, explicit: bool,
         provenance: dict[str, str], errors: list[dict[str, str]],
         source_paths: set[Path]) -> tuple[str, Path | None, list[str]]:
    authority = gci_root if gci_root is not None else root / "_body_gci"
    default_root = root / "_body_gci"
    if authority.exists():
        safe = _safe_existing(authority, root, directory=True)
        if safe is None or not _contained(safe, default_root.resolve()):
            _error(errors, "PATH_ESCAPE", "GCI root is outside permitted authority", "gci")
            return "BLOCKED", None, ["PATH_ESCAPE"]
    else:
        if explicit:
            _error(errors, "GCI_EVIDENCE_INVALID", "supplied GCI root is unreadable", "gci")
            return "BLOCKED", None, ["GCI_EVIDENCE_INVALID"]
        return "NOT_EVALUATED", None, ["GCI_NOT_FOUND"]
    candidates, unsafe = _direct_candidates(authority.parent, authority.name, "grid_convergence.json")
    _track(source_paths, *candidates)
    if unsafe:
        _error(errors, "PATH_ESCAPE", "unsafe GCI candidate", "gci")
        return "BLOCKED", None, ["PATH_ESCAPE"]
    invalid = False
    matches: list[Path] = []
    stale_for_case = False
    pass_candidates = 0
    for path in candidates:
        manifest = _read_json(path)
        if not _schema_ok("gci", manifest):
            invalid = True
            continue
        if manifest.get("status") != "PASS" or manifest.get("design_ready") is not True:
            continue
        pass_candidates += 1
        current = False
        selects_case = False
        for item in manifest.get("cases") or []:
            if not isinstance(item, dict):
                continue
            selected = _resolve_raw(item.get("path"), root, directory=True)
            item_provenance = item.get("provenance")
            if selected == case:
                selects_case = True
                if (isinstance(item_provenance, dict)
                        and all(item_provenance.get(key) == value
                                for key, value in provenance.items())):
                    current = True
                    break
        if current:
            matches.append(path)
        elif selects_case:
            stale_for_case = True
    if invalid and explicit:
        _error(errors, "GCI_SCHEMA_INVALID", "GCI authority contains invalid evidence", "gci")
        return "BLOCKED", None, ["GCI_SCHEMA_INVALID"]
    if stale_for_case and explicit:
        _error(errors, "GCI_EVIDENCE_STALE", "GCI evidence for the selected case is stale", "gci")
        return "BLOCKED", None, ["GCI_EVIDENCE_STALE"]
    if len(matches) > 1:
        _error(errors, "AMBIGUOUS_GCI_EVIDENCE", "multiple current GCI manifests", "gci")
        return "BLOCKED", None, ["AMBIGUOUS_GCI_EVIDENCE"]
    if len(matches) == 1:
        return "PASS", matches[0], []
    if pass_candidates and explicit:
        _error(errors, "GCI_EVIDENCE_STALE", "GCI authority has no current selected-case match", "gci")
        return "BLOCKED", None, ["GCI_EVIDENCE_STALE"]
    return "NOT_EVALUATED", None, ["GCI_NOT_FOUND"]


def _field(path: Path | None, root: Path, selected: dict[str, Path | None],
           errors: list[dict[str, str]],
           source_paths: set[Path]) -> tuple[str, list[str]]:
    if path is None:
        return "NOT_EVALUATED", ["FIELD_EVIDENCE_NOT_SUPPLIED"]
    safe = _safe_existing(path, root)
    release = (root / "_release_evidence").resolve()
    if safe is None or not _contained(safe, release):
        _error(errors, "FIELD_EVIDENCE_INVALID", "field evidence path is unsafe", "field_evidence")
        return "BLOCKED", ["FIELD_EVIDENCE_INVALID"]
    manifest = _read_json(safe)
    _track(source_paths, safe)
    records = (
        manifest.get("artifacts")
        if isinstance(manifest, dict)
        and isinstance(manifest.get("artifacts"), dict)
        else {}
    )
    for record in records.values():
        if isinstance(record, dict):
            _track(
                source_paths,
                _resolve_field_record_for_tracking(record.get("path"), root),
            )
    validation = field_acceptance.validate_evidence(safe, projects_root=root)
    if not _schema_ok("field", manifest) or validation.get("ok") is not True:
        _error(errors, "FIELD_EVIDENCE_INVALID", "field evidence failed independent validation", "field_evidence")
        return "BLOCKED", ["FIELD_EVIDENCE_INVALID"]
    artifact_map = {
        "geometry": "geometry", "surface_manifest": "surface",
        "mesh_manifest": "mesh", "run_manifest": "run", "result_manifest": "result",
    }
    for raw_key, selected_key in artifact_map.items():
        record = records.get(raw_key) if isinstance(records.get(raw_key), dict) else {}
        resolved = _resolve_ref(record.get("path"), root)
        wanted = selected.get(selected_key)
        if resolved is None or wanted is None or resolved != wanted or record.get("sha256") != _sha256(wanted):
            _error(errors, "FIELD_EVIDENCE_CASE_MISMATCH", "field evidence binds another chain", "field_evidence")
            return "BLOCKED", ["FIELD_EVIDENCE_CASE_MISMATCH"]
    return "PASS", []


def _compute(case_dir: Path, *, projects_root: Path, gci_root: Path | None,
             gci_explicit: bool, field_evidence_path: Path | None,
             source_paths: set[Path] | None = None) -> dict:
    root = projects_root
    case = case_dir
    errors: list[dict[str, str]] = []
    source_paths = source_paths if source_paths is not None else set()

    solver_surface = _safe_existing(case / "surface_manifest.json", root)
    solver_mesh = _safe_existing(case / "mesh_manifest.json", root)
    surface_path = _select_matching_copy(
        root, "_occ_geometry", "surface_manifest.json", solver_surface, "surface", errors,
        source_paths,
    )
    mesh_path = _select_matching_copy(
        root, "_body_mesh", "mesh_manifest.json", solver_mesh, "mesh", errors,
        source_paths,
    )
    surface_payload = _read_json(surface_path) if surface_path else _read_json(solver_surface) if solver_surface else None
    source = surface_payload.get("source") if isinstance(surface_payload, dict) and isinstance(surface_payload.get("source"), dict) else {}
    geometry_path = _resolve_raw(source.get("geometry_path"), root)
    _track(source_paths, geometry_path)
    if geometry_path is None:
        _error(errors, "MISSING_ARTIFACT", "geometry is missing or unsafe", "geometry")
        geometry_path = case / "missing-geometry.json"
    elif geometry_path.suffix.lower() != ".json":
        _error(errors, "GEOMETRY_PATH_INVALID", "geometry authority must be a .json file", "geometry")
        geometry_path = case / "missing-geometry.json"
    elif _generated(geometry_path, root):
        _error(errors, "GENERATED_SOURCE_EXCLUDED", "geometry is under a generated namespace", "geometry")

    run_path = case / "run_manifest.json"
    result_path = case / "result_manifest.json"
    thermal_path = case / "thermal_input.json"
    progress_path = case / "thermal_progress.json"
    for name, path in (("run", run_path), ("result", result_path),
                       ("thermal_input", thermal_path), ("thermal_progress", progress_path)):
        if _safe_existing(path, root) is None:
            _error(errors, "MISSING_ARTIFACT", f"missing {name}", name if name in {"run", "result"} else name)
    _track(
        source_paths, geometry_path if geometry_path.is_file() else None,
        run_path, result_path, thermal_path, progress_path,
    )

    geometry = _read_json(geometry_path) if geometry_path.is_file() else None
    geometry_ok = True
    if not _schema_ok("geometry", geometry) or (geometry and validate_geometry_v2(geometry)):
        _error(errors, "GEOMETRY_SCHEMA_INVALID", "geometry.v2 schema/semantics failed", "geometry")
        geometry_ok = False
    review = geometry.get("review") if isinstance(geometry, dict) and isinstance(geometry.get("review"), dict) else {}
    if review.get("ready") is not True or review.get("blocking") is True:
        _error(errors, "GEOMETRY_REVIEW_BLOCKED", "geometry review is not ready", "geometry")
        geometry_ok = False
    surface_ok = surface_path is not None and _surface_validation(
        surface_path or solver_surface, surface_payload, geometry_path if geometry_path.is_file() else None,
        root, errors,
        source_paths,
    )

    mesh_payload = _read_json(mesh_path) if mesh_path else _read_json(solver_mesh) if solver_mesh else None
    mesh_ok = mesh_path is not None and _mesh_validation(
        mesh_path or solver_mesh, mesh_payload, surface_path, case, root, errors,
        source_paths,
    )

    thermal = _read_json(thermal_path)
    thermal_ok = (
        isinstance(thermal, dict)
        and thermal.get("contract") == "thermal_input.v1"
        and thermal.get("engine") == "body_fitted_buoyant_urans"
        and isinstance(thermal.get("settings"), dict)
        and isinstance(thermal.get("numerics"), dict)
        and solver_mesh is not None
        and thermal.get("mesh_manifest_sha256") == _sha256(solver_mesh)
    )
    if not thermal_ok:
        _error(errors, "THERMAL_INPUT_INVALID", "thermal input contract or mesh link is invalid", "thermal_input")

    run = _read_json(run_path)
    run_schema_ok = _schema_ok("run", run)
    if not run_schema_ok:
        _error(errors, "RUN_SCHEMA_INVALID", "run manifest schema is invalid", "run")
    progress = _read_json(progress_path)
    progress_ok = _schema_ok("progress", progress) and _progress_semantic_ok(progress or {})
    if not progress_ok:
        _error(errors, "THERMAL_PROGRESS_INVALID", "standalone thermal progress is impossible", "thermal_progress")
    if not isinstance(run, dict) or run.get("thermal_progress") != progress:
        _error(errors, "THERMAL_PROGRESS_MISMATCH", "run progress differs from canonical standalone progress", "thermal_progress")
        progress_ok = False
    run_input = run.get("input") if isinstance(run, dict) and isinstance(run.get("input"), dict) else {}
    run_ok = (
        run_schema_ok and run.get("contract") == "run_manifest.v1"
        and run.get("engine") == "body_fitted_buoyant_urans"
        and run.get("status") == "PASS" and run.get("design_ready") is True
        and thermal_path.is_file() and run_input.get("thermal_input_sha256") == _sha256(thermal_path)
        and progress_ok
    )
    if not run_ok:
        _error(errors, "RUN_GATE_BLOCKED", "run is not current PASS buoyant evidence", "run")

    numerical_issues = (
        cfd_result_gate.body_fitted_numerical_provenance_issues(case, run, thermal)
        if isinstance(run, dict) and isinstance(thermal, dict) else ["MISSING"]
    )
    numerical_ok = not numerical_issues
    if numerical_issues:
        _error(errors, "NUMERICAL_PROVENANCE_INVALID", ",".join(numerical_issues), "run")
    for relative_path in cfd_result_gate.THERMAL_NUMERICS_SYSTEM_FILES.values():
        _track(source_paths, _safe_existing(case / relative_path, root))
    provenance_record = run_input.get("numerical_provenance") if isinstance(run_input.get("numerical_provenance"), dict) else {}
    if provenance_record.get("source") == "thermal_restart_input":
        _track(source_paths, _safe_existing(case / "thermal_restart_input.json", root))

    result = _read_json(result_path)
    result_ok = _result_validation(
        case, result, run_path, case / "mesh_manifest.json", thermal_path, root, errors,
        source_paths,
    )
    if isinstance(result, dict) and isinstance(progress, dict):
        if _finite(result.get("time_s")) != _finite(progress.get("latest_time_s")):
            _error(errors, "RESULT_PROGRESS_MISMATCH", "result time differs from progress", "result")
            result_ok = False

    refs: dict[str, dict[str, str]] = {
        "geometry": _link(geometry_path, root, "geometry.v2"),
        "surface": _link(surface_path or case / "surface_manifest.json", root, "surface_manifest.v1"),
        "mesh": _link(mesh_path or case / "mesh_manifest.json", root, "mesh_manifest.v1"),
        "run": _link(run_path, root, "run_manifest.v1"),
        "result": _link(result_path, root, "result_manifest.v1"),
        "thermal_input": _link(thermal_path, root, "thermal_input.v1"),
        "thermal_progress": _link(progress_path, root, "thermal_progress.v1"),
    }
    provenance = {
        "run_manifest_sha256": refs["run"]["sha256"],
        "result_manifest_sha256": refs["result"]["sha256"],
        "mesh_manifest_sha256": _sha256(case / "mesh_manifest.json") if (case / "mesh_manifest.json").is_file() else ZERO_SHA256,
        "thermal_input_sha256": refs["thermal_input"]["sha256"],
    }
    grid_status, gci_path, grid_reasons = _gci(
        case, root, gci_root, gci_explicit, provenance, errors, source_paths
    )
    if gci_path:
        refs["gci"] = _link(gci_path, root, "grid_convergence.v3")

    field_status, field_reasons = _field(
        field_evidence_path, root,
        {"geometry": geometry_path if geometry_path.is_file() else None,
         "surface": surface_path, "mesh": mesh_path, "run": run_path,
         "result": result_path},
        errors, source_paths,
    )
    if field_evidence_path is not None and _safe_existing(field_evidence_path, root):
        refs["field_evidence"] = _link(
            _safe_existing(field_evidence_path, root), root, "field_dxf_acceptance.v1"
        )

    geometry_status = "PASS" if geometry_ok and surface_ok else "BLOCKED"
    bc_status = "PASS" if surface_ok and thermal_ok else "BLOCKED"
    mesh_status = "PASS" if mesh_ok else "BLOCKED"
    solver_status = "PASS" if run_ok and result_ok else "BLOCKED"
    numerics_status = "PASS" if numerical_ok else "BLOCKED"
    checks = [
        _check("geometry_valid", geometry_status,
               [] if geometry_status == "PASS" else ["GEOMETRY_INVALID"], ["geometry", "surface"]),
        _check("bc_reviewed", bc_status,
               [] if bc_status == "PASS" else ["BC_EVIDENCE_INVALID"], ["surface", "thermal_input"]),
        _check("mesh_checked", mesh_status,
               [] if mesh_status == "PASS" else ["MESH_QUALITY_BLOCKED"], ["mesh"]),
        _check("solver_converged", solver_status,
               [] if solver_status == "PASS" else ["SOLVER_EVIDENCE_INVALID"],
               ["run", "thermal_progress", "result"]),
        _check("numerics_verified", numerics_status,
               [] if numerics_status == "PASS" else ["NUMERICAL_PROVENANCE_INVALID"], ["run"]),
        _check("grid_verified", grid_status, grid_reasons, ["gci"] if gci_path else []),
        _check("benchmark_validated", "NOT_EVALUATED", ["BENCHMARK_AUTHORITY_UNAVAILABLE"], []),
        _check("field_calibrated", field_status, field_reasons,
               ["field_evidence"] if "field_evidence" in refs else []),
    ]
    core_statuses = {item["status"] for item in checks if item["id"] in CORE_CHECKS}
    status = "PASS" if core_statuses == {"PASS"} else "BLOCKED"
    identity_payload = {
        "geometry_path": refs["geometry"]["path"],
        "geometry_sha256": refs["geometry"]["sha256"],
        "run_manifest_path": refs["run"]["path"],
        "run_manifest_sha256": refs["run"]["sha256"],
    }
    case_id = "legacy-" + hashlib.sha256(_canonical_bytes(identity_payload)).hexdigest()[:20]
    return {
        "contract": CONTRACT,
        "schema_version": 1,
        "created_at": _now(),
        "purpose": "screening",
        "legacy_case_ref": {"case_id": case_id, **identity_payload},
        "checks": checks,
        "artifact_refs": refs,
        "status": status,
        "errors": errors,
    }


def _prepare_context(case_dir: Path, projects_root: Path) -> tuple[Path, Path]:
    if _has_raw_dot_segment(projects_root):
        raise ValueError("projects_root must be a real directory")
    if _has_raw_dot_segment(case_dir):
        raise ValueError("case_dir must be strictly beneath projects_root/_body_solver")
    root_input = Path(projects_root).expanduser()
    root = root_input.resolve(strict=True)
    if not root.is_dir() or _is_reparse(root_input):
        raise ValueError("projects_root must be a real directory")
    case_input = Path(case_dir).expanduser()
    if not case_input.is_absolute():
        case_input = root_input / case_input
    case = _safe_existing(case_input, root_input, directory=True)
    solver_root = _safe_existing(
        root_input / "_body_solver", root_input, directory=True
    )
    if case is None or solver_root is None or case == solver_root or not _contained(case, solver_root):
        raise ValueError("case_dir must be strictly beneath projects_root/_body_solver")
    return root, case


def _safe_optional(path: Path | None, root: Path) -> Path | None:
    if path is None:
        return None
    if _has_raw_dot_segment(path):
        raise ValueError("optional path cannot contain dot traversal")
    raw = Path(path).expanduser()
    if not raw.is_absolute():
        raw = root / raw
    return raw


def _canonical_gci_root(path: Path | None, root: Path) -> Path | None:
    if path is None:
        return None
    raw = _safe_optional(path, root)
    canonical = root / "_body_gci"
    try:
        if raw.absolute().resolve() != canonical.absolute().resolve():
            raise ValueError
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(
            "gci_root must be the canonical projects_root/_body_gci authority"
        ) from exc
    if raw.exists() and _safe_existing(raw, root, directory=True) is None:
        raise ValueError("gci_root must be the canonical projects_root/_body_gci authority")
    return canonical


def _validate_output(path: Path, root: Path, source_paths: set[Path]) -> Path:
    if _has_raw_dot_segment(path):
        raise ValueError("output_path must be beneath projects_root")
    supplied = Path(path)
    raw = supplied if supplied.is_absolute() else root / supplied
    roots = _path_roots(raw, root)
    if roots is None:
        raise ValueError("output_path must be beneath projects_root")
    lexical_root, canonical_root = roots
    if _is_reparse(raw):
        raise ValueError("output_path has an unsafe parent")
    try:
        raw.resolve(strict=True)
    except FileNotFoundError:
        existing_output = None
    except (OSError, RuntimeError) as exc:
        raise ValueError("output_path has an unsafe parent") from exc
    else:
        existing_output = _safe_existing(raw, lexical_root)
        if existing_output is None:
            raise ValueError("output_path has an unsafe parent")
    if existing_output is not None:
        output = existing_output
    else:
        parent = raw.parent
        missing: list[str] = []
        while True:
            try:
                parent.resolve(strict=True)
            except FileNotFoundError:
                if parent == lexical_root or parent == parent.parent:
                    raise ValueError("output_path has an unsafe parent")
                missing.insert(0, parent.name)
                parent = parent.parent
                continue
            except (OSError, RuntimeError):
                raise ValueError("output_path has an unsafe parent")
            safe_parent = _safe_existing(parent, lexical_root, directory=True)
            if safe_parent is None:
                raise ValueError("output_path has an unsafe parent")
            break
        safe_parent = safe_parent.joinpath(*missing)
        safe_parent.mkdir(parents=True, exist_ok=True)
        safe_parent = _safe_existing(safe_parent, canonical_root, directory=True)
        if safe_parent is None:
            raise ValueError("output_path has an unsafe parent")
        output = (safe_parent / raw.name).resolve()
        if not _contained(output, canonical_root):
            raise ValueError("output_path must be beneath projects_root")
    for source in source_paths:
        try:
            if os.path.samefile(output, source):
                raise ValueError("output_path cannot overwrite a source artifact")
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ValueError("output_path cannot overwrite a source artifact") from exc
    return output


def _atomic_json(path: Path, payload: dict) -> None:
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def build_case_evidence(
    case_dir: Path,
    *,
    projects_root: Path,
    gci_root: Path | None = None,
    field_evidence_path: Path | None = None,
    output_path: Path | None = None,
) -> dict:
    """Recompute current raw evidence and atomically publish one screening record."""
    root, case = _prepare_context(case_dir, projects_root)
    gci_arg = _canonical_gci_root(gci_root, root)
    field_arg = _safe_optional(field_evidence_path, root)
    source_paths: set[Path] = set()
    evidence = _compute(
        case, projects_root=root, gci_root=gci_arg,
        gci_explicit=gci_root is not None, field_evidence_path=field_arg,
        source_paths=source_paths,
    )
    for record in evidence["artifact_refs"].values():
        path = _resolve_ref(record["path"], root)
        if path:
            source_paths.add(path)
    output = _validate_output(
        output_path if output_path is not None else case / "case_evidence.v1.json",
        root, source_paths,
    )
    _atomic_json(output, evidence)
    return evidence


def _validation_error(errors: list[dict[str, str]], code: str, detail: str) -> None:
    row = {"code": code, "detail": detail}
    if row not in errors:
        errors.append(row)


def validate_case_evidence(
    evidence_path: Path,
    *,
    projects_root: Path,
) -> list[dict[str, str]]:
    """Reopen stored evidence, rehash its refs, and repeat raw recomputation."""
    errors: list[dict[str, str]] = []
    if _has_raw_dot_segment(projects_root):
        return [{"code": "ARTIFACT_REF_INVALID", "detail": "projects_root is invalid"}]
    if _has_raw_dot_segment(evidence_path):
        return [{"code": "ARTIFACT_REF_INVALID", "detail": "evidence path is unsafe"}]
    try:
        root = Path(projects_root).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return [{"code": "ARTIFACT_REF_INVALID", "detail": "projects_root is invalid"}]
    raw = Path(evidence_path).expanduser()
    if not raw.is_absolute():
        raw = root / raw
    safe_evidence = _safe_existing(raw, root)
    if safe_evidence is None:
        return [{"code": "ARTIFACT_REF_INVALID", "detail": "evidence path is unsafe"}]
    stored = _read_json(safe_evidence)
    if not _schema_ok("evidence", stored):
        _validation_error(errors, "EVIDENCE_SCHEMA_INVALID", "case evidence schema is invalid")
    if not isinstance(stored, dict):
        return errors
    refs = stored.get("artifact_refs") if isinstance(stored.get("artifact_refs"), dict) else {}
    for key, record in refs.items():
        if not isinstance(record, dict):
            _validation_error(errors, "ARTIFACT_REF_INVALID", f"{key} reference is invalid")
            continue
        path = _resolve_ref(record.get("path"), root)
        if path is None:
            _validation_error(errors, "ARTIFACT_REF_INVALID", f"{key} path is unsafe or missing")
        elif record.get("sha256") != _sha256(path):
            _validation_error(errors, "ARTIFACT_HASH_MISMATCH", f"{key} hash is stale")
    run_record = refs.get("run") if isinstance(refs.get("run"), dict) else {}
    run_path = _resolve_ref(run_record.get("path"), root)
    if run_path is None:
        return errors
    try:
        _, case = _prepare_context(run_path.parent, root)
    except ValueError:
        _validation_error(errors, "ARTIFACT_REF_INVALID", "run is outside solver authority")
        return errors
    gci_record = refs.get("gci") if isinstance(refs.get("gci"), dict) else None
    gci_path = _resolve_ref(gci_record.get("path"), root) if gci_record else None
    explicit_gci_error = any(
        isinstance(item, dict) and item.get("code") in {
            "GCI_EVIDENCE_INVALID", "GCI_EVIDENCE_STALE",
        }
        for item in (stored.get("errors") or [])
    )
    gci_root = root / "_body_gci" if gci_path is not None or explicit_gci_error else None
    field_record = refs.get("field_evidence") if isinstance(refs.get("field_evidence"), dict) else None
    field_path = _resolve_ref(field_record.get("path"), root) if field_record else None
    current = _compute(
        case, projects_root=root, gci_root=gci_root,
        gci_explicit=explicit_gci_error, field_evidence_path=field_path,
    )
    comparable_stored = {key: value for key, value in stored.items() if key != "created_at"}
    comparable_current = {key: value for key, value in current.items() if key != "created_at"}
    if comparable_stored != comparable_current:
        _validation_error(
            errors, "EVIDENCE_RECOMPUTATION_MISMATCH",
            "stored evidence differs from current authoritative recomputation",
        )
    return errors
