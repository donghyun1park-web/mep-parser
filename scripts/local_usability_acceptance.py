"""Pure fail-closed validator for single-PC serial environment evidence.

The authoritative manifest is fixed at
``_working_validation/local_usability_acceptance.json`` under the evaluated
projects root.  This module never searches for ``latest`` evidence and never
runs FreeCAD, OpenFOAM, Studio, or a browser.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
import platform
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import uuid
from typing import Any

from jsonschema import Draft202012Validator


CONTRACT = "local_usability_acceptance.v1"
FIXED_MANIFEST = PurePosixPath("_working_validation/local_usability_acceptance.json")
RUNTIME_CAPABILITY = PurePosixPath("_working_validation/runtime_capability.v1.json")
RAW_ROOT = PurePosixPath("_system/environment_acceptance")
WORKING_OUTPUT_ROOT = PurePosixPath("_working_validation/evaluations")
MAX_ACCEPTANCE_AGE = timedelta(minutes=15)
MAX_FUTURE_SKEW = timedelta(seconds=30)
MAX_SAME_RUN_SKEW = timedelta(minutes=5)
DIAGNOSTIC_CODES = {
    "WSL_UNAVAILABLE",
    "FREECAD_UNAVAILABLE",
    "INVALID_GEOMETRY",
    "MESH_FAILURE",
    "SOLVER_OR_DISK_FAILURE",
}
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_CELL_COUNT = re.compile(r"(?im)^\s*cells\s*:\s*(\d+)\s*$")
_TIME_VALUE = re.compile(r"(?m)^Time\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*$")
_SOLVER_FATAL = re.compile(
    r"FOAM\s+FATAL|FATAL\s+ERROR|SEGMENTATION\s+FAULT|FLOATING\s+POINT\s+EXCEPTION"
    r"|NO\s+SPACE\s+LEFT\s+ON\s+DEVICE|^\s*KILLED\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_CACHE_PARTS = {".cache", "cache", ".pytest_cache", "__pycache__", "tmp", "temp", ".tmp"}
_FREECAD_MODULES = (
    "FreeCAD", "Part", "Draft", "Arch", "Mesh", "MeshPart", "BOPTools.SplitAPI",
)
_FREECAD_BOOLEAN_VOLUME_MM3 = 239_250_000_000.0


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _schema_path() -> Path:
    return Path(__file__).resolve().parents[1] / "local_usability_acceptance.v1.schema.json"


def _schema_errors(payload: object) -> list[str]:
    schema = _read_json(_schema_path())
    if schema is None:
        return ["SCHEMA_UNAVAILABLE"]
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload),
        key=lambda error: [str(part) for part in error.path],
    )
    blockers: list[str] = []
    for error in errors:
        path = list(error.path)
        if path == ["launch_observations"] and error.validator in {"minItems", "maxItems"}:
            blockers.append("LAUNCH_OBSERVATION_CARDINALITY_INVALID")
        elif path == ["diagnostic_observations"] and error.validator in {"minItems", "maxItems"}:
            blockers.append("DIAGNOSTIC_OBSERVATION_CARDINALITY_INVALID")
        else:
            blockers.append("MANIFEST_SCHEMA_INVALID")
    return blockers


def _freecad_schema_valid(payload: object) -> bool:
    schema = _read_json(_schema_path())
    if schema is None:
        return False
    validator_schema = {
        "$schema": schema.get("$schema"),
        "$defs": schema.get("$defs", {}),
        "$ref": "#/$defs/freecad_ready_diagnostics",
    }
    return not any(Draft202012Validator(validator_schema).iter_errors(payload))


def _link_rows(manifest: dict[str, Any]) -> list[dict[str, str]]:
    sources = manifest.get("sources") if isinstance(manifest.get("sources"), dict) else {}
    rows = [value for value in sources.values() if isinstance(value, dict)]
    rows.extend(value for value in manifest.get("launch_observations", []) if isinstance(value, dict))
    rows.extend(value for value in manifest.get("diagnostic_observations", []) if isinstance(value, dict))
    return rows


def _path_reference_blocker(relative: object) -> str | None:
    if not isinstance(relative, str) or not relative:
        return "SOURCE_PATH_INVALID"
    if "\\" in relative:
        return "SOURCE_PATH_BACKSLASH_FORBIDDEN"
    if relative.startswith("/") or re.match(r"^[A-Za-z]:", relative):
        return "SOURCE_PATH_ABSOLUTE_FORBIDDEN"
    if ":" in relative:
        return "SOURCE_PATH_COLON_FORBIDDEN"
    pure = PurePosixPath(relative)
    if pure.as_posix() != relative:
        return "SOURCE_PATH_NON_CANONICAL"
    if any(part in {"", ".", ".."} for part in pure.parts):
        return "SOURCE_PATH_TRAVERSAL"
    folded = [part.casefold() for part in pure.parts]
    if "latest" in folded or any(part.startswith("latest.") for part in folded):
        return "SOURCE_LATEST_FORBIDDEN"
    if any(part in _CACHE_PARTS or part.endswith(".tmp") for part in folded):
        return "SOURCE_CACHE_OR_TEMP_FORBIDDEN"
    return None


def _path_case_is_exact(root: Path, relative: str) -> bool:
    """Reject case-only aliases while allowing a genuinely missing component."""
    current = root
    for part in PurePosixPath(relative).parts:
        try:
            names = [entry.name for entry in os.scandir(current)]
        except OSError:
            return False
        if part in names:
            current = current / part
            continue
        if any(name.casefold() == part.casefold() for name in names):
            return False
        return not (current / part).exists()
    return True


def _file_identity(path: Path) -> tuple[int, int] | None:
    try:
        metadata = path.stat()
    except OSError:
        return None
    return metadata.st_dev, metadata.st_ino


def _has_multiple_hardlinks(path: Path) -> bool:
    try:
        return path.stat().st_nlink != 1
    except OSError:
        return False


def _same_file(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def _output_path_blockers(root: Path, output: Path) -> list[str]:
    blockers: list[str] = []
    working_root = root.joinpath(*WORKING_OUTPUT_ROOT.parts)
    try:
        relative = output.relative_to(working_root)
        if not relative.parts:
            raise ValueError
    except ValueError:
        blockers.append("EVALUATOR_OUTPUT_LOCATION_FORBIDDEN")
        return blockers
    if _path_has_link_or_reparse(root, output):
        blockers.append("EVALUATOR_OUTPUT_REPARSE_FORBIDDEN")
    if not _path_case_is_exact(root, output.relative_to(root).as_posix()):
        blockers.append("EVALUATOR_OUTPUT_PATH_CASE_MISMATCH")
    try:
        metadata = output.lstat()
    except OSError:
        return blockers
    if not stat.S_ISREG(metadata.st_mode):
        blockers.append("EVALUATOR_OUTPUT_INVALID")
    return blockers


def _path_has_link_or_reparse(root: Path, path: Path) -> bool:
    """Return true when a source or one of its in-root parents is redirected."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    for part in relative.parts:
        current = current / part
        try:
            metadata = current.lstat()
        except OSError:
            continue
        if current.is_symlink() or (
            getattr(metadata, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            return True
    return False


def _resolve_source(projects_root: Path, relative: str) -> Path | None:
    if _path_reference_blocker(relative):
        return None
    pure = PurePosixPath(relative)
    candidate = projects_root.joinpath(*pure.parts)
    if _path_has_link_or_reparse(projects_root, candidate):
        return None
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(projects_root)
    except (OSError, ValueError):
        return None
    return resolved


def _parse_utc(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    offset = parsed.utcoffset()
    return parsed if offset is not None and offset.total_seconds() == 0 else None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp_is_current(
    value: object, *, manifest_stamp: datetime | None, now: datetime
) -> bool:
    stamp = _parse_utc(value)
    if stamp is None or manifest_stamp is None:
        return False
    return bool(
        now - MAX_ACCEPTANCE_AGE <= stamp <= now + MAX_FUTURE_SKEW
        and abs(stamp - manifest_stamp) <= MAX_SAME_RUN_SKEW
    )


def _same_link(raw: object, expected: dict[str, str]) -> bool:
    return isinstance(raw, dict) and raw == expected


def _finite_number(value: object, *, positive: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    number = float(value)
    return math.isfinite(number) and (number > 0 if positive else number >= 0)


def _freecad_stage_invariants_ok(freecad: dict[str, Any]) -> bool:
    stages = freecad.get("stages")
    if not isinstance(stages, list) or len(stages) != 4:
        return False
    discovery, imports, boolean, tessellation = stages
    if discovery.get("details") != {"selection": "explicit"}:
        return False
    imports_details = imports.get("details")
    if not isinstance(imports_details, dict) or imports_details.get("ok") is not True:
        return False
    modules = imports_details.get("modules")
    if not isinstance(modules, dict) or set(modules) != set(_FREECAD_MODULES):
        return False
    if any(modules.get(name) is not True for name in _FREECAD_MODULES):
        return False
    for key in ("freecad_version", "revision", "python_version", "occ_version"):
        if imports_details.get(key) != freecad.get(key):
            return False
    boolean_details = boolean.get("details")
    if not isinstance(boolean_details, dict):
        return False
    volume = boolean_details.get("volume_mm3")
    declared_error = boolean_details.get("relative_volume_error")
    if (
        boolean_details.get("ok") is not True
        or boolean_details.get("valid") is not True
        or boolean_details.get("solid_count") != 1
        or not _finite_number(volume, positive=True)
        or not _finite_number(declared_error)
    ):
        return False
    recomputed_error = abs(float(volume) - _FREECAD_BOOLEAN_VOLUME_MM3) / _FREECAD_BOOLEAN_VOLUME_MM3
    if not math.isclose(float(declared_error), recomputed_error, rel_tol=0.0, abs_tol=1e-15):
        return False
    if recomputed_error > 1e-9:
        return False
    tessellation_details = tessellation.get("details")
    return bool(
        isinstance(tessellation_details, dict)
        and tessellation_details.get("ok") is True
        and isinstance(tessellation_details.get("vertices"), int)
        and not isinstance(tessellation_details.get("vertices"), bool)
        and tessellation_details["vertices"] > 0
        and isinstance(tessellation_details.get("facets"), int)
        and not isinstance(tessellation_details.get("facets"), bool)
        and tessellation_details["facets"] > 0
    )


def _freecad_executable_identity_valid(
    freecad_identity: dict[str, Any], freecad: dict[str, Any] | None
) -> bool:
    raw_executable = freecad_identity.get("executable")
    if not isinstance(raw_executable, str) or not raw_executable:
        return False
    try:
        executable = Path(raw_executable).resolve(strict=True)
        current_python = Path(sys.executable).resolve(strict=True)
        if str(executable) != raw_executable:
            return False
        if executable.name.casefold() not in {"freecadcmd", "freecadcmd.exe"}:
            return False
        if os.path.samefile(executable, current_python):
            return False
        digest = _sha256_file(executable)
    except OSError:
        return False
    if digest != freecad_identity.get("executable_sha256"):
        return False
    if not isinstance(freecad, dict):
        return False
    return (
        freecad.get("executable") == raw_executable
        and freecad.get("executable_sha256") == digest
        and freecad.get("selection") == "explicit"
    )


def _validate_runtime_semantics(
    manifest: dict[str, Any], loaded: dict[str, dict[str, Any]], blockers: list[str],
    *, now: datetime,
) -> None:
    sources = manifest["sources"]
    environment = loaded.get(sources["environment_acceptance"]["path"])
    runtime = loaded.get(sources["runtime_capability"]["path"])
    freecad = loaded.get(sources["freecad_diagnostics"]["path"])
    if environment is None or environment.get("contract") != "environment_acceptance.v1":
        blockers.append("ENVIRONMENT_ACCEPTANCE_INVALID")
        return
    run_id = manifest.get("run_id")
    if (
        environment.get("run_id") != run_id
        or not isinstance(runtime, dict)
        or runtime.get("run_id") != run_id
        or not isinstance(freecad, dict)
        or freecad.get("run_id") != run_id
    ):
        blockers.append("RUN_ID_MISMATCH")
    manifest_stamp = _parse_utc(manifest.get("created_at"))
    environment_stamp = _parse_utc(environment.get("created_at"))
    runtime_stamp = _parse_utc(runtime.get("created_at")) if isinstance(runtime, dict) else None
    if (
        manifest_stamp is None or environment_stamp is None
        or abs(manifest_stamp - environment_stamp) > MAX_SAME_RUN_SKEW
        or not _timestamp_is_current(environment.get("created_at"), manifest_stamp=manifest_stamp, now=now)
    ):
        blockers.append("ENVIRONMENT_ACCEPTANCE_STALE")
    if (
        manifest_stamp is None or runtime_stamp is None
        or abs(manifest_stamp - runtime_stamp) > MAX_SAME_RUN_SKEW
        or not _timestamp_is_current(runtime.get("created_at"), manifest_stamp=manifest_stamp, now=now)
    ):
        blockers.append("RUNTIME_CAPABILITY_STALE")
    if not _timestamp_is_current(
        freecad.get("checked_at") if isinstance(freecad, dict) else None,
        manifest_stamp=manifest_stamp,
        now=now,
    ):
        blockers.append("FREECAD_DIAGNOSTICS_STALE")
    for key in ("case_input", "mesh_log", "solver_log", "report", "runtime_capability"):
        if not _same_link(environment.get(key), sources[key]):
            blockers.append("ENVIRONMENT_ACCEPTANCE_LINK_MISMATCH")
            break
    if environment.get("status") != "PASS" or environment.get("mesh_ok") is not True:
        blockers.append("ENVIRONMENT_ACCEPTANCE_NOT_PASS")
    if runtime is None or runtime.get("contract") != "runtime_capability.v1":
        blockers.append("RUNTIME_CAPABILITY_INVALID")
    else:
        baseline = runtime.get("serial_baseline") if isinstance(runtime.get("serial_baseline"), dict) else {}
        if runtime.get("serial_runtime_ready") is not True or baseline.get("status") != "PASS":
            blockers.append("SERIAL_RUNTIME_NOT_READY")
        if (
            not _finite_number(baseline.get("runner_wall_seconds"), positive=True)
            or not _finite_number(baseline.get("solver_clock_seconds"))
            or not _finite_number(baseline.get("peak_rss_kib"), positive=True)
        ):
            blockers.append("SERIAL_RUNTIME_METRICS_INVALID")
        if baseline.get("case_input_sha256") != sources["case_input"]["sha256"]:
            blockers.append("CASE_INPUT_RUNTIME_HASH_MISMATCH")
        if baseline.get("solver_log_sha256") != sources["solver_log"]["sha256"]:
            blockers.append("SOLVER_LOG_RUNTIME_HASH_MISMATCH")

    identities = manifest["identities"]
    python_identity = identities["python"]
    current_python = Path(sys.executable).resolve()
    if (
        Path(python_identity["executable"]).resolve() != current_python
        or python_identity["executable_sha256"] != _sha256_file(current_python)
        or python_identity["version"] != sys.version
        or python_identity["architecture"] != platform.architecture()[0]
    ):
        blockers.append("PYTHON_IDENTITY_MISMATCH")

    freecad_identity = identities["freecad"]
    if not _freecad_executable_identity_valid(freecad_identity, freecad):
        blockers.append("FREECAD_EXECUTABLE_IDENTITY_INVALID")
    if not _freecad_schema_valid(freecad):
        blockers.append("FREECAD_DIAGNOSTICS_SCHEMA_INVALID")
    elif not _freecad_stage_invariants_ok(freecad):
        blockers.append("FREECAD_DIAGNOSTICS_INVARIANT_INVALID")
    if (
        freecad is None
        or freecad.get("contract") != "freecad_staged_diagnostics.v1"
        or freecad.get("ok") is not True
        or freecad.get("status") != "ready"
        or freecad.get("failed_stage") is not None
        or [
            row.get("id") if isinstance(row, dict) else None
            for row in freecad.get("stages", [])
        ] != ["discovery", "imports", "boolean", "tessellation"]
        or any(
            not isinstance(row, dict)
            or row.get("status") != "PASS"
            or row.get("reason_code") not in {"", None}
            for row in freecad.get("stages", [])
        )
        or any(freecad.get(key) != freecad_identity.get(key) for key in (
            "executable", "executable_sha256", "freecad_version", "occ_version",
            "compatible_profile",
        ))
    ):
        blockers.append("FREECAD_DIAGNOSTICS_MISMATCH")

    openfoam_identity = identities["openfoam"]
    current_openfoam = runtime.get("openfoam") if isinstance(runtime, dict) else None
    if not isinstance(current_openfoam, dict) or any(
        current_openfoam.get(key) != openfoam_identity.get(key)
        for key in ("distro", "kernel", "version", "package_version", "compatible_profile")
    ) or environment.get("openfoam_profile") != openfoam_identity["compatible_profile"]:
        blockers.append("OPENFOAM_IDENTITY_MISMATCH")


def _validate_raw_observations(
    manifest: dict[str, Any],
    loaded: dict[str, dict[str, Any]],
    blockers: list[str],
    root: Path,
    manifest_path: Path,
    output: Path | None,
    evidence: dict[str, str],
    source_paths: dict[str, Path],
    source_identities: dict[str, tuple[int, int] | None],
    *,
    now: datetime,
) -> None:
    manifest_stamp = _parse_utc(manifest.get("created_at"))
    run_id = manifest.get("run_id")
    attempts: set[int] = set()
    for link in manifest["launch_observations"]:
        observation = loaded.get(link["path"])
        if observation is None or observation.get("contract") != "studio_launch_observation.v1":
            blockers.append("LAUNCH_OBSERVATION_INVALID")
            continue
        start = _parse_utc(observation.get("process_started_at"))
        http = _parse_utc(observation.get("http_ready_at"))
        dom = _parse_utc(observation.get("dom_ready_at"))
        attempt = observation.get("attempt")
        if observation.get("run_id") != run_id:
            blockers.append("RUN_ID_MISMATCH")
        if not all(
            _timestamp_is_current(value, manifest_stamp=manifest_stamp, now=now)
            for value in (
                observation.get("process_started_at"),
                observation.get("http_ready_at"),
                observation.get("dom_ready_at"),
            )
        ):
            blockers.append("LAUNCH_OBSERVATION_STALE")
        if (
            not isinstance(attempt, int) or attempt < 1 or attempt in attempts
            or start is None or http is None or dom is None
            or not (start <= http <= dom) or (dom - start).total_seconds() > 10
            or not observation.get("required_dom_marker")
            or observation.get("status") != "PASS"
        ):
            blockers.append("LAUNCH_OBSERVATION_INVALID")
        attempts.add(attempt) if isinstance(attempt, int) else None
    if attempts != {1, 2, 3}:
        blockers.append("LAUNCH_OBSERVATION_CARDINALITY_INVALID")

    codes: set[str] = set()
    for link in manifest["diagnostic_observations"]:
        observation = loaded.get(link["path"])
        if observation is None or observation.get("contract") != "actionable_diagnostic_observation.v1":
            blockers.append("DIAGNOSTIC_OBSERVATION_INVALID")
            continue
        code = observation.get("code")
        if observation.get("run_id") != run_id:
            blockers.append("RUN_ID_MISMATCH")
        if not _timestamp_is_current(
            observation.get("observed_at"), manifest_stamp=manifest_stamp, now=now
        ):
            blockers.append("DIAGNOSTIC_OBSERVATION_STALE")
        korean_fields = [
            observation.get("cause_ko"), observation.get("impact_ko"),
            observation.get("next_action_ko"),
        ]
        if (
            code not in DIAGNOSTIC_CODES or code in codes
            or observation.get("status") != "PASS"
            or observation.get("raw_traceback_count") != 0
            or not all(isinstance(value, str) and re.search(r"[가-힣]", value) for value in korean_fields)
        ):
            blockers.append("DIAGNOSTIC_OBSERVATION_INVALID")
        log_link = observation.get("log")
        if not isinstance(log_link, dict):
            blockers.append("DIAGNOSTIC_LOG_LINK_INVALID")
        else:
            relative = log_link.get("path")
            path_blocker = _path_reference_blocker(relative)
            if path_blocker:
                blockers.append(path_blocker)
            elif not str(relative).startswith(RAW_ROOT.as_posix() + "/"):
                blockers.append("RAW_SOURCE_LOCATION_INVALID")
            elif relative in source_paths:
                blockers.append("SOURCE_DUPLICATE")
            else:
                unresolved = root.joinpath(*PurePosixPath(relative).parts)
                if _path_has_link_or_reparse(root, unresolved):
                    blockers.append("SOURCE_LINK_OR_REPARSE_FORBIDDEN")
                else:
                    source = _resolve_source(root, relative)
                    if source is None:
                        blockers.append("DIAGNOSTIC_LOG_PATH_INVALID")
                    elif _same_file(source, manifest_path) or (
                        output is not None and _same_file(source, output)
                    ):
                        blockers.append("SOURCE_SELF_OUTPUT_FORBIDDEN")
                    elif not source.is_file():
                        blockers.append("DIAGNOSTIC_LOG_MISSING")
                    else:
                        if not _path_case_is_exact(root, relative):
                            blockers.append("SOURCE_PATH_CASE_MISMATCH")
                        identity = _file_identity(source)
                        if _has_multiple_hardlinks(source):
                            blockers.append("SOURCE_HARDLINK_FORBIDDEN")
                        if identity is not None and identity in source_identities.values():
                            blockers.append("SOURCE_DUPLICATE")
                        source_identities[relative] = identity
                        try:
                            digest = _sha256_file(source)
                        except OSError:
                            blockers.append("DIAGNOSTIC_LOG_UNREADABLE")
                        else:
                            evidence[relative] = digest
                            source_paths[relative] = source
                            if digest != log_link.get("sha256"):
                                blockers.append("DIAGNOSTIC_LOG_HASH_MISMATCH")
                            try:
                                diagnostic_text = source.read_text(
                                    encoding="utf-8", errors="replace"
                                )
                            except OSError:
                                blockers.append("DIAGNOSTIC_LOG_UNREADABLE")
                            else:
                                if not diagnostic_text.strip():
                                    blockers.append("DIAGNOSTIC_LOG_EMPTY")
                                elif isinstance(code, str) and not re.search(
                                    rf"(?m)^\s*{re.escape(code)}\s*$",
                                    diagnostic_text,
                                ):
                                    blockers.append("DIAGNOSTIC_LOG_CODE_MISMATCH")
                                if re.search(
                                    r"(?m)^Traceback \(most recent call last\):\s*$",
                                    diagnostic_text,
                                ):
                                    blockers.append("DIAGNOSTIC_LOG_TRACEBACK_PRESENT")
        if isinstance(code, str):
            codes.add(code)
    if codes != DIAGNOSTIC_CODES:
        blockers.append("DIAGNOSTIC_OBSERVATION_CARDINALITY_INVALID")


def validate_local_usability_acceptance(
    manifest_path: Path,
    projects_root: Path,
    evaluator_output: Path | None = None,
) -> dict[str, Any]:
    """Recompute serial-environment evidence from one fixed manifest.

    Args:
        manifest_path: Must be ``_working_validation/local_usability_acceptance.json``
            below ``projects_root``; no discovery or glob selection is performed.
        projects_root: Authority root for every POSIX relative source reference.
        evaluator_output: Optional path for an atomically written evaluator result;
            it must be below ``_working_validation/evaluations`` and may not
            alias the manifest or any consumed source.

    Returns:
        ``{"status", "blockers", "evidence_sha256"}``, where the evidence
        mapping contains every consumed raw source path and recomputed SHA-256.
    """
    root = Path(projects_root).resolve()
    manifest_argument = Path(manifest_path)
    manifest_lexical = (
        manifest_argument if manifest_argument.is_absolute()
        else Path.cwd() / manifest_argument
    )
    output = (
        Path(os.path.abspath(os.fspath(evaluator_output)))
        if evaluator_output is not None else None
    )
    blockers: list[str] = []
    evidence: dict[str, str] = {}
    loaded: dict[str, dict[str, Any]] = {}
    source_paths: dict[str, Path] = {}
    source_identities: dict[str, tuple[int, int] | None] = {}
    now = _utc_now()
    output_safe = output is not None

    expected_manifest = root.joinpath(*FIXED_MANIFEST.parts)
    if str(manifest_lexical) != str(expected_manifest):
        blockers.append("MANIFEST_PATH_NON_CANONICAL")
    if _path_has_link_or_reparse(root, manifest_lexical):
        blockers.append("MANIFEST_LINK_OR_REPARSE_FORBIDDEN")
    try:
        resolved_manifest = manifest_lexical.resolve(strict=False)
    except OSError:
        resolved_manifest = manifest_lexical
    if resolved_manifest != expected_manifest.resolve(strict=False):
        blockers.append("MANIFEST_LOCATION_NOT_FIXED")
    manifest_identity = _file_identity(manifest_lexical)
    if _has_multiple_hardlinks(manifest_lexical):
        blockers.append("MANIFEST_HARDLINK_FORBIDDEN")
    if output is not None:
        output_blockers = _output_path_blockers(root, output)
        blockers.extend(output_blockers)
        output_safe = not output_blockers
    if output is not None and _same_file(output, manifest_lexical):
        blockers.append("EVALUATOR_OUTPUT_ALIASES_MANIFEST")
        output_safe = False
    manifest = _read_json(manifest_lexical)
    if manifest is None:
        blockers.append("MANIFEST_MISSING_OR_MALFORMED")
    else:
        blockers.extend(_schema_errors(manifest))
        manifest_stamp = _parse_utc(manifest.get("created_at"))
        if manifest_stamp is not None:
            if manifest_stamp < now - MAX_ACCEPTANCE_AGE:
                blockers.append("ACCEPTANCE_WINDOW_EXPIRED")
            if manifest_stamp > now + MAX_FUTURE_SKEW:
                blockers.append("ACCEPTANCE_TIMESTAMP_IN_FUTURE")
        for link in _link_rows(manifest):
            path_blocker = _path_reference_blocker(link.get("path"))
            if path_blocker:
                blockers.append(path_blocker)

    if manifest is not None and not any(
        blocker in {"MANIFEST_MISSING_OR_MALFORMED", "SCHEMA_UNAVAILABLE"}
        for blocker in blockers
    ):
        seen: set[str] = set()
        for link in _link_rows(manifest):
            relative = link.get("path")
            path_blocker = _path_reference_blocker(relative)
            if path_blocker:
                continue
            if relative in seen:
                blockers.append("SOURCE_DUPLICATE")
                continue
            seen.add(relative)
            unresolved = root.joinpath(*PurePosixPath(relative).parts)
            if _path_has_link_or_reparse(root, unresolved):
                blockers.append("SOURCE_LINK_OR_REPARSE_FORBIDDEN")
                continue
            source = _resolve_source(root, relative)
            if source is None:
                blockers.append("SOURCE_PATH_INVALID")
                continue
            if _same_file(source, manifest_lexical):
                blockers.append("SOURCE_SELF_OUTPUT_FORBIDDEN")
                continue
            if output is not None and _same_file(source, output):
                blockers.append("SOURCE_SELF_OUTPUT_FORBIDDEN")
                output_safe = False
            if not source.is_file():
                blockers.append("SOURCE_MISSING")
                continue
            if not _path_case_is_exact(root, relative):
                blockers.append("SOURCE_PATH_CASE_MISMATCH")
            identity = _file_identity(source)
            if _has_multiple_hardlinks(source):
                blockers.append("SOURCE_HARDLINK_FORBIDDEN")
            if identity is not None and identity in source_identities.values():
                blockers.append("SOURCE_DUPLICATE")
            source_identities[relative] = identity
            try:
                digest = _sha256_file(source)
            except OSError:
                blockers.append("SOURCE_UNREADABLE")
                continue
            source_paths[relative] = source
            evidence[relative] = digest
            if digest != link.get("sha256"):
                blockers.append("SOURCE_HASH_MISMATCH")
                continue
            if source.suffix.casefold() == ".json":
                payload = _read_json(source)
                if payload is None:
                    blockers.append("SOURCE_JSON_MALFORMED")
                else:
                    loaded[relative] = payload

        sources = manifest.get("sources") if isinstance(manifest.get("sources"), dict) else {}
        runtime_link = sources.get("runtime_capability") if isinstance(sources.get("runtime_capability"), dict) else {}
        if runtime_link.get("path") != RUNTIME_CAPABILITY.as_posix():
            blockers.append("RUNTIME_CAPABILITY_LOCATION_NOT_FIXED")
        raw_prefix = RAW_ROOT.as_posix() + "/"
        for name, link in sources.items():
            if name == "runtime_capability" or not isinstance(link, dict):
                continue
            if not str(link.get("path") or "").startswith(raw_prefix):
                blockers.append("RAW_SOURCE_LOCATION_INVALID")
        for collection in ("launch_observations", "diagnostic_observations"):
            for link in manifest.get(collection, []):
                if isinstance(link, dict) and not str(link.get("path") or "").startswith(raw_prefix):
                    blockers.append("RAW_SOURCE_LOCATION_INVALID")

        raw_root_path = root.joinpath(*RAW_ROOT.parts)
        if raw_root_path.is_dir():
            raw_files = [path for path in raw_root_path.rglob("*") if path.is_file()]
            mesh_candidates = [path for path in raw_files if path.name.startswith("log.checkMesh")]
            solver_candidates = [
                path for path in raw_files if re.fullmatch(r"log\..*Foam(?:\..*)?", path.name)
            ]
            report_candidates = [path for path in raw_files if path.suffix.casefold() == ".html"]
            if len(mesh_candidates) != 1:
                blockers.append("MESH_LOG_AMBIGUOUS" if mesh_candidates else "MESH_LOG_MISSING")
            if len(solver_candidates) != 1:
                blockers.append("SOLVER_LOG_AMBIGUOUS" if solver_candidates else "SOLVER_LOG_MISSING")
            if len(report_candidates) != 1:
                blockers.append("REPORT_AMBIGUOUS" if report_candidates else "REPORT_MISSING")
            selections = (
                ("mesh_log", mesh_candidates, "MESH_LOG_SELECTION_MISMATCH"),
                ("solver_log", solver_candidates, "SOLVER_LOG_SELECTION_MISMATCH"),
                ("report", report_candidates, "REPORT_SELECTION_MISMATCH"),
            )
            for source_name, candidates, blocker in selections:
                link = sources.get(source_name) if isinstance(sources.get(source_name), dict) else {}
                relative = link.get("path")
                selected = source_paths.get(relative) if isinstance(relative, str) else None
                if len(candidates) == 1 and selected is not None and selected != candidates[0].resolve():
                    blockers.append(blocker)

    if manifest is not None and not blockers:
        sources = manifest["sources"]
        try:
            mesh_text = root.joinpath(*PurePosixPath(sources["mesh_log"]["path"]).parts).read_text(
                encoding="utf-8", errors="replace"
            )
            solver_text = root.joinpath(*PurePosixPath(sources["solver_log"]["path"]).parts).read_text(
                encoding="utf-8", errors="replace"
            )
            report_text = root.joinpath(*PurePosixPath(sources["report"]["path"]).parts).read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            blockers.append("RAW_ACCEPTANCE_ARTIFACT_UNREADABLE")
        else:
            cells = [int(value) for value in _CELL_COUNT.findall(mesh_text)]
            times = [float(value) for value in _TIME_VALUE.findall(solver_text)]
            solver_lines = [line.strip() for line in solver_text.splitlines() if line.strip()]
            environment = loaded[sources["environment_acceptance"]["path"]]
            if cells != [64] or "Mesh OK" not in mesh_text:
                blockers.append("MESH_64_CELL_EVIDENCE_INVALID")
            if (
                not times
                or max(times) <= 0
                or times[-1] != max(times)
                or environment.get("latest_time") != times[-1]
            ):
                blockers.append("SOLVER_TIME_EVIDENCE_INVALID")
            if not solver_lines or solver_lines[-1] != "End":
                blockers.append("SOLVER_LOG_INCOMPLETE")
            if _SOLVER_FATAL.search(solver_text):
                blockers.append("SOLVER_LOG_FATAL")
            if not report_text.strip():
                blockers.append("REPORT_EVIDENCE_INVALID")
            if environment.get("cells") != 64:
                blockers.append("ENVIRONMENT_CELL_CLAIM_INVALID")
        _validate_runtime_semantics(manifest, loaded, blockers, now=now)
        _validate_raw_observations(
            manifest, loaded, blockers, root, manifest_lexical, output,
            evidence, source_paths, source_identities, now=now,
        )
        if "SOURCE_SELF_OUTPUT_FORBIDDEN" in blockers:
            output_safe = False

    for relative, source in source_paths.items():
        unresolved = root.joinpath(*PurePosixPath(relative).parts)
        if _path_has_link_or_reparse(root, unresolved):
            blockers.append("SOURCE_POST_LOAD_LINK_OR_REPARSE")
        if _file_identity(source) != source_identities.get(relative):
            blockers.append("SOURCE_FILE_IDENTITY_CHANGED")
        if _has_multiple_hardlinks(source):
            blockers.append("SOURCE_HARDLINK_FORBIDDEN")
        try:
            current_digest = _sha256_file(source)
        except OSError:
            blockers.append("SOURCE_POST_LOAD_UNREADABLE")
            continue
        if evidence.get(relative) != current_digest:
            blockers.append("SOURCE_POST_LOAD_HASH_DRIFT")

    if _path_has_link_or_reparse(root, manifest_lexical):
        blockers.append("MANIFEST_POST_LOAD_LINK_OR_REPARSE")
    if _file_identity(manifest_lexical) != manifest_identity:
        blockers.append("MANIFEST_POST_LOAD_IDENTITY_CHANGED")
    if _has_multiple_hardlinks(manifest_lexical):
        blockers.append("MANIFEST_HARDLINK_FORBIDDEN")

    result = {
        "contract": "local_usability_acceptance_evaluation.v1",
        "check_id": "serial_environment",
        "status": "PASS" if not blockers else "BLOCKED",
        "blockers": sorted(set(blockers)),
        "evidence_sha256": dict(sorted(evidence.items())),
    }
    if output is not None and output_safe:
        temporary: Path | None = None
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            if _path_has_link_or_reparse(root, output):
                blockers.append("EVALUATOR_OUTPUT_REPARSE_FORBIDDEN")
                result["status"] = "BLOCKED"
                result["blockers"] = sorted(set(blockers))
            else:
                temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
                temporary.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                os.replace(temporary, output)
        except OSError:
            blockers.append("EVALUATOR_OUTPUT_UNWRITABLE")
            result["status"] = "BLOCKED"
            result["blockers"] = sorted(set(blockers))
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
    return result


def run_local_usability_acceptance(
    repo_root: Path, python_executable: Path, *, launch_attempts: int = 3
) -> dict[str, Any]:
    """Validate existing fixed evidence without launching external runtimes.

    Real Studio launches and environment evidence are Task 5b/5c responsibilities.
    Missing fixed evidence therefore remains BLOCKED.
    """
    repo_root = Path(repo_root).resolve()
    projects_root = repo_root / "cfd_projects"
    if Path(python_executable).resolve() != Path(sys.executable).resolve():
        return {
            "contract": "local_usability_acceptance_evaluation.v1",
            "check_id": "serial_environment", "status": "BLOCKED",
            "blockers": ["PYTHON_EXECUTABLE_NOT_CURRENT"], "evidence_sha256": {},
        }
    if launch_attempts != 3:
        return {
            "contract": "local_usability_acceptance_evaluation.v1",
            "check_id": "serial_environment", "status": "BLOCKED",
            "blockers": ["LAUNCH_ATTEMPTS_MUST_EQUAL_THREE"], "evidence_sha256": {},
        }
    return validate_local_usability_acceptance(
        projects_root.joinpath(*FIXED_MANIFEST.parts), projects_root
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate fixed single-PC serial environment evidence")
    parser.add_argument("--projects-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = validate_local_usability_acceptance(args.manifest, args.projects_root, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
