import hashlib
import json
import platform
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_RELATIVE = Path("_working_validation/local_usability_acceptance.json")
RAW_ROOT = Path("_system/environment_acceptance")
DIAGNOSTIC_CODES = (
    "WSL_UNAVAILABLE",
    "FREECAD_UNAVAILABLE",
    "INVALID_GEOMETRY",
    "MESH_FAILURE",
    "SOLVER_OR_DISK_FAILURE",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(root: Path, relative: Path | str, payload) -> Path:
    path = root / Path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, (dict, list)):
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    else:
        text = str(payload)
    path.write_text(text, encoding="utf-8")
    return path


def _link(root: Path, path: Path) -> dict:
    return {"path": path.relative_to(root).as_posix(), "sha256": _sha256(path)}


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _expected_case_input_sha256(case_root: Path) -> str:
    digest = hashlib.sha256()
    for relative in ("cfd_case_meta.json", "Allrun", "system/controlDict"):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update((case_root / relative).read_bytes())
    return digest.hexdigest()


def _build_bundle(root: Path) -> tuple[Path, dict]:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    evidence_time = now - timedelta(seconds=10)
    raw_root = root / RAW_ROOT
    case_meta = _write(
        root,
        RAW_ROOT / "cfd_case_meta.json",
        {"contract": "environment_acceptance_case.v1", "cells": 64},
    )
    allrun = _write(
        root,
        RAW_ROOT / "Allrun",
        "#!/bin/sh\nbuoyantBoussinesqPimpleFoam\n",
    )
    control_dict = _write(
        root,
        RAW_ROOT / "system/controlDict",
        "application buoyantBoussinesqPimpleFoam;\nendTime 1;\n",
    )
    mesh_log = _write(
        root, RAW_ROOT / "log.checkMesh",
        "Mesh stats\n    cells: 64\nMesh OK.\n",
    )
    solver_log = _write(
        root, RAW_ROOT / "log.buoyantBoussinesqPimpleFoam",
        "Time = 0.5\nTime = 1\nEnd\n",
    )
    report = _write(
        root, RAW_ROOT / "cfd_report_environment_acceptance.html",
        "<!doctype html><title>environment acceptance</title>\n",
    )

    executable = Path(sys.executable).resolve()
    executable_sha256 = _sha256(executable)
    freecad_executable = _write(root, "installed/FreeCADCmd.exe", "freecad-binary\n")
    freecad_executable_sha256 = _sha256(freecad_executable)
    freecad_diagnostics = _write(root, RAW_ROOT / "freecad_stages.json", {
        "schema_version": 1,
        "contract": "freecad_staged_diagnostics.v1",
        "checked_at": _stamp(evidence_time),
        "run_id": "serial-run-001",
        "ok": True,
        "status": "ready",
        "failed_stage": None,
        "summary": "FreeCAD 단계별 형상 환경이 준비되었습니다.",
        "fix": "",
        "selection": "explicit",
        "executable": str(freecad_executable.resolve()),
        "executable_sha256": freecad_executable_sha256,
        "freecad_version": "1.1.1",
        "revision": "20260414",
        "python_version": "3.11.14",
        "occ_version": "7.8.1",
        "compatible_profile": "freecad-1.1.1-occ-7.8.1",
        "stages": [
            {
                "id": "discovery", "status": "PASS", "reason_code": "",
                "details": {"selection": "explicit"},
            },
            {
                "id": "imports", "status": "PASS", "reason_code": "",
                "details": {
                    "stage": "imports", "ok": True,
                    "freecad_version": "1.1.1", "revision": "20260414",
                    "python_version": "3.11.14", "occ_version": "7.8.1",
                    "modules": {
                        name: True for name in (
                            "FreeCAD", "Part", "Draft", "Arch", "Mesh", "MeshPart",
                            "BOPTools.SplitAPI",
                        )
                    },
                },
            },
            {
                "id": "boolean", "status": "PASS", "reason_code": "",
                "details": {
                    "stage": "boolean", "ok": True, "valid": True,
                    "solid_count": 1, "volume_mm3": 239250000000.0,
                    "relative_volume_error": 0.0,
                },
            },
            {
                "id": "tessellation", "status": "PASS", "reason_code": "",
                "details": {
                    "stage": "tessellation", "ok": True,
                    "vertices": 8, "facets": 12,
                },
            },
        ],
    })
    runtime_capability = _write(root, "_working_validation/runtime_capability.v1.json", {
        "schema_version": 1,
        "contract": "runtime_capability.v1",
        "created_at": _stamp(evidence_time),
        "run_id": "serial-run-001",
        "serial_runtime_ready": True,
        "parallel_runtime_ready": False,
        "openfoam": {
            "status": "ready",
            "distro": "Ubuntu-24.04",
            "kernel": "6.18.33.2-microsoft-standard-WSL2",
            "version": "v2606",
            "package_version": "2606.0-1",
            "compatible_profile": "openfoam-v2606",
            "solvers": {"buoyantBoussinesqPimpleFoam": "/usr/bin/buoyantBoussinesqPimpleFoam"},
        },
        "mpi": {},
        "cpu": {"effective_logical_count": 8, "source": "WSL nproc"},
        "serial_baseline": {
            "status": "PASS",
            "runner_wall_seconds": 2.0,
            "solver_clock_seconds": 1.0,
            "peak_rss_kib": 2048,
            "case_input_sha256": _expected_case_input_sha256(raw_root),
            "solver_log_sha256": _sha256(solver_log),
        },
    })
    environment_acceptance = _write(root, RAW_ROOT / "environment_acceptance.json", {
        "contract": "environment_acceptance.v1",
        "created_at": _stamp(evidence_time),
        "run_id": "serial-run-001",
        "status": "PASS",
        "mesh_ok": True,
        "cells": 64,
        "latest_time": 1.0,
        "openfoam_profile": "openfoam-v2606",
        "case_meta": _link(root, case_meta),
        "allrun": _link(root, allrun),
        "control_dict": _link(root, control_dict),
        "case_input_sha256": _expected_case_input_sha256(raw_root),
        "mesh_log": _link(root, mesh_log),
        "solver_log": _link(root, solver_log),
        "report": _link(root, report),
        "runtime_capability": _link(root, runtime_capability),
    })

    launches = []
    for attempt in range(1, 4):
        observation = _write(root, RAW_ROOT / f"launch-{attempt}.json", {
            "contract": "studio_launch_observation.v1",
            "run_id": "serial-run-001",
            "attempt": attempt,
            "process_started_at": _stamp(now - timedelta(seconds=attempt * 3)),
            "http_ready_at": _stamp(now - timedelta(seconds=attempt * 3 - 1)),
            "dom_ready_at": _stamp(now - timedelta(seconds=attempt * 3 - 2)),
            "required_dom_marker": "MEP CFD Studio",
            "status": "PASS",
        })
        launches.append(_link(root, observation))

    diagnostics = []
    for index, code in enumerate(DIAGNOSTIC_CODES, start=1):
        diagnostic_log = _write(root, RAW_ROOT / f"diagnostic-{index}.log", f"{code}\n")
        observation = _write(root, RAW_ROOT / f"diagnostic-{index}.json", {
            "contract": "actionable_diagnostic_observation.v1",
            "run_id": "serial-run-001",
            "observed_at": _stamp(now - timedelta(seconds=index)),
            "code": code,
            "cause_ko": "환경 원인을 확인했습니다.",
            "impact_ko": "현재 계산을 시작할 수 없습니다.",
            "next_action_ko": "환경 진단 로그를 확인하고 다시 검사하세요.",
            "log": _link(root, diagnostic_log),
            "raw_traceback_count": 0,
            "status": "PASS",
        })
        diagnostics.append(_link(root, observation))

    manifest = {
        "schema_version": 1,
        "contract": "local_usability_acceptance.v1",
        "created_at": _stamp(now),
        "run_id": "serial-run-001",
        "scope": "single_pc_serial_current_user",
        "status": "PASS",
        "blockers": [],
        "identities": {
            "python": {
                "executable": str(executable),
                "executable_sha256": executable_sha256,
                "version": sys.version,
                "architecture": platform.architecture()[0],
            },
            "freecad": {
                "executable": str(freecad_executable.resolve()),
                "executable_sha256": freecad_executable_sha256,
                "freecad_version": "1.1.1",
                "occ_version": "7.8.1",
                "compatible_profile": "freecad-1.1.1-occ-7.8.1",
            },
            "openfoam": {
                "distro": "Ubuntu-24.04",
                "kernel": "6.18.33.2-microsoft-standard-WSL2",
                "version": "v2606",
                "package_version": "2606.0-1",
                "compatible_profile": "openfoam-v2606",
            },
        },
        "sources": {
            "environment_acceptance": _link(root, environment_acceptance),
            "runtime_capability": _link(root, runtime_capability),
            "case_meta": _link(root, case_meta),
            "allrun": _link(root, allrun),
            "control_dict": _link(root, control_dict),
            "mesh_log": _link(root, mesh_log),
            "solver_log": _link(root, solver_log),
            "report": _link(root, report),
            "freecad_diagnostics": _link(root, freecad_diagnostics),
        },
        "launch_observations": launches,
        "diagnostic_observations": diagnostics,
    }
    manifest_path = _write(root, MANIFEST_RELATIVE, manifest)
    assert raw_root.is_dir()
    return manifest_path, manifest


def _rewrite_manifest(path: Path, manifest: dict) -> None:
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _relink_environment_source(
    root: Path, manifest_path: Path, manifest: dict, source_name: str
) -> None:
    source_path = root / manifest["sources"][source_name]["path"]
    manifest["sources"][source_name] = _link(root, source_path)
    environment_path = root / manifest["sources"]["environment_acceptance"]["path"]
    environment = json.loads(environment_path.read_text(encoding="utf-8"))
    environment[source_name] = manifest["sources"][source_name]
    _write(root, environment_path.relative_to(root), environment)
    manifest["sources"]["environment_acceptance"] = _link(root, environment_path)
    _rewrite_manifest(manifest_path, manifest)


def _relink_top_source(
    root: Path, manifest_path: Path, manifest: dict, source_name: str
) -> None:
    source_path = root / manifest["sources"][source_name]["path"]
    manifest["sources"][source_name] = _link(root, source_path)
    _rewrite_manifest(manifest_path, manifest)


def _relink_diagnostic_log(
    root: Path, manifest_path: Path, manifest: dict, index: int
) -> None:
    observation_link = manifest["diagnostic_observations"][index]
    observation_path = root / observation_link["path"]
    observation = json.loads(observation_path.read_text(encoding="utf-8"))
    log_path = root / observation["log"]["path"]
    observation["log"] = _link(root, log_path)
    _write(root, observation_path.relative_to(root), observation)
    manifest["diagnostic_observations"][index] = _link(root, observation_path)
    _rewrite_manifest(manifest_path, manifest)


def _relink_solver_log_everywhere(
    root: Path, manifest_path: Path, manifest: dict
) -> None:
    solver_path = root / manifest["sources"]["solver_log"]["path"]
    manifest["sources"]["solver_log"] = _link(root, solver_path)
    runtime_path = root / manifest["sources"]["runtime_capability"]["path"]
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    runtime["serial_baseline"]["solver_log_sha256"] = _sha256(solver_path)
    _write(root, runtime_path.relative_to(root), runtime)
    manifest["sources"]["runtime_capability"] = _link(root, runtime_path)
    environment_path = root / manifest["sources"]["environment_acceptance"]["path"]
    environment = json.loads(environment_path.read_text(encoding="utf-8"))
    environment["solver_log"] = manifest["sources"]["solver_log"]
    environment["runtime_capability"] = manifest["sources"]["runtime_capability"]
    _write(root, environment_path.relative_to(root), environment)
    manifest["sources"]["environment_acceptance"] = _link(root, environment_path)
    _rewrite_manifest(manifest_path, manifest)


def _relink_control_dict_everywhere(
    root: Path, manifest_path: Path, manifest: dict
) -> None:
    control_path = root / manifest["sources"]["control_dict"]["path"]
    manifest["sources"]["control_dict"] = _link(root, control_path)
    runtime_path = root / manifest["sources"]["runtime_capability"]["path"]
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    aggregate = _expected_case_input_sha256(root / RAW_ROOT)
    runtime["serial_baseline"]["case_input_sha256"] = aggregate
    _write(root, runtime_path.relative_to(root), runtime)
    manifest["sources"]["runtime_capability"] = _link(root, runtime_path)
    environment_path = root / manifest["sources"]["environment_acceptance"]["path"]
    environment = json.loads(environment_path.read_text(encoding="utf-8"))
    environment["control_dict"] = manifest["sources"]["control_dict"]
    environment["case_input_sha256"] = aggregate
    environment["runtime_capability"] = manifest["sources"]["runtime_capability"]
    _write(root, environment_path.relative_to(root), environment)
    manifest["sources"]["environment_acceptance"] = _link(root, environment_path)
    _rewrite_manifest(manifest_path, manifest)


def test_schema_accepts_closed_complete_manifest_and_rejects_bad_refs(tmp_path):
    manifest_path, manifest = _build_bundle(tmp_path)
    schema = json.loads(
        (REPO_ROOT / "local_usability_acceptance.v1.schema.json").read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema)

    assert list(validator.iter_errors(manifest)) == []

    with_extra = json.loads(json.dumps(manifest))
    with_extra["unexpected"] = True
    assert list(validator.iter_errors(with_extra))

    with_backslash = json.loads(json.dumps(manifest))
    with_backslash["sources"]["mesh_log"]["path"] = r"_system\environment_acceptance\log.checkMesh"
    assert list(validator.iter_errors(with_backslash))

    with_legacy_case_input = json.loads(json.dumps(manifest))
    with_legacy_case_input["sources"]["case_input"] = with_legacy_case_input[
        "sources"
    ]["control_dict"]
    assert list(validator.iter_errors(with_legacy_case_input))
    for required_case_source in ("case_meta", "allrun", "control_dict"):
        missing_case_source = json.loads(json.dumps(manifest))
        missing_case_source["sources"].pop(required_case_source)
        assert list(validator.iter_errors(missing_case_source))
    assert manifest_path.is_file()


def test_validator_blocks_non_string_source_path_without_raising(tmp_path):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    manifest["sources"]["mesh_log"]["path"] = []
    _rewrite_manifest(manifest_path, manifest)

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "SOURCE_PATH_INVALID" in result["blockers"]


def test_validator_recomputes_complete_raw_tree_and_returns_every_consumed_hash(tmp_path):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    expected = {
        link["path"]
        for link in list(manifest["sources"].values())
        + manifest["launch_observations"]
        + manifest["diagnostic_observations"]
    }
    expected.update((RAW_ROOT / f"diagnostic-{index}.log").as_posix() for index in range(1, 6))
    assert result["status"] == "PASS"
    assert result["blockers"] == []
    assert set(result["evidence_sha256"]) == expected
    assert all(len(value) == 64 for value in result["evidence_sha256"].values())


def test_case_input_fingerprint_matches_current_cfd_run_aggregate_contract(tmp_path):
    import cfd_run
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    runtime_path = tmp_path / manifest["sources"]["runtime_capability"]["path"]
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))

    assert runtime["serial_baseline"]["case_input_sha256"] == (
        cfd_run._case_input_sha256(tmp_path / RAW_ROOT)
    )
    result = validate_local_usability_acceptance(manifest_path, tmp_path)
    assert result["status"] == "PASS"


def test_case_input_aggregate_is_recomputed_in_fixed_order_with_path_labels(tmp_path):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    runtime_path = tmp_path / manifest["sources"]["runtime_capability"]["path"]
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    runtime["serial_baseline"]["case_input_sha256"] = hashlib.sha256(
        b"".join(
            (tmp_path / RAW_ROOT / relative).read_bytes()
            for relative in ("system/controlDict", "Allrun", "cfd_case_meta.json")
        )
    ).hexdigest()
    _write(tmp_path, runtime_path.relative_to(tmp_path), runtime)
    _relink_environment_source(
        tmp_path, manifest_path, manifest, "runtime_capability"
    )

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "CASE_INPUT_RUNTIME_HASH_MISMATCH" in result["blockers"]


@pytest.mark.parametrize(
    ("source_name", "wrong_relative"),
    [
        ("case_meta", RAW_ROOT / "renamed-case-meta.json"),
        ("allrun", RAW_ROOT / "run/Allrun"),
        ("control_dict", RAW_ROOT / "system/controlDict.copy"),
    ],
)
def test_case_input_source_locations_are_fixed(
    tmp_path, source_name, wrong_relative
):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    original = tmp_path / manifest["sources"][source_name]["path"]
    wrong = tmp_path / wrong_relative
    wrong.parent.mkdir(parents=True, exist_ok=True)
    wrong.write_bytes(original.read_bytes())
    manifest["sources"][source_name] = _link(tmp_path, wrong)
    environment_path = tmp_path / manifest["sources"]["environment_acceptance"]["path"]
    environment = json.loads(environment_path.read_text(encoding="utf-8"))
    environment[source_name] = manifest["sources"][source_name]
    _write(tmp_path, environment_path.relative_to(tmp_path), environment)
    manifest["sources"]["environment_acceptance"] = _link(
        tmp_path, environment_path
    )
    _rewrite_manifest(manifest_path, manifest)

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "CASE_INPUT_SOURCE_LOCATION_INVALID" in result["blockers"]


def test_forged_pass_with_missing_raw_source_is_blocked(tmp_path):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    (tmp_path / manifest["sources"]["runtime_capability"]["path"]).unlink()

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "SOURCE_MISSING" in result["blockers"]


def test_altered_runtime_capability_is_recomputed_after_wrapper_hashes_are_updated(tmp_path):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    runtime_path = tmp_path / manifest["sources"]["runtime_capability"]["path"]
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    runtime["serial_runtime_ready"] = False
    _write(tmp_path, runtime_path.relative_to(tmp_path), runtime)
    _relink_environment_source(tmp_path, manifest_path, manifest, "runtime_capability")

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "SERIAL_RUNTIME_NOT_READY" in result["blockers"]
    assert "SOURCE_HASH_MISMATCH" not in result["blockers"]


def test_current_python_executable_identity_mismatch_is_blocked(tmp_path):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    manifest["identities"]["python"]["executable_sha256"] = "0" * 64
    _rewrite_manifest(manifest_path, manifest)

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "PYTHON_IDENTITY_MISMATCH" in result["blockers"]


def test_current_openfoam_identity_mismatch_is_blocked_even_with_relinked_wrapper(tmp_path):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    runtime_path = tmp_path / manifest["sources"]["runtime_capability"]["path"]
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    runtime["openfoam"]["kernel"] = "changed-kernel"
    _write(tmp_path, runtime_path.relative_to(tmp_path), runtime)
    _relink_environment_source(tmp_path, manifest_path, manifest, "runtime_capability")

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "OPENFOAM_IDENTITY_MISMATCH" in result["blockers"]


def test_hash_bound_control_dict_application_is_parsed_not_caller_claimed(tmp_path):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    control_dict = tmp_path / manifest["sources"]["control_dict"]["path"]
    control_dict.write_text("application simpleFoam;\nendTime 1;\n", encoding="utf-8")
    _relink_control_dict_everywhere(tmp_path, manifest_path, manifest)

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "CONTROL_DICT_APPLICATION_INVALID" in result["blockers"]


def test_control_dict_preprocessing_cannot_introduce_unhashed_application_override(
    tmp_path,
):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    control_dict = tmp_path / manifest["sources"]["control_dict"]["path"]
    override = control_dict.with_name("applicationOverride")
    override.write_text("application simpleFoam;\n", encoding="utf-8")
    control_dict.write_text(
        "application buoyantBoussinesqPimpleFoam;\n"
        '#include "applicationOverride"\n'
        "endTime 1;\n",
        encoding="utf-8",
    )
    _relink_control_dict_everywhere(tmp_path, manifest_path, manifest)

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "CONTROL_DICT_PREPROCESSING_FORBIDDEN" in result["blockers"]
    assert override.relative_to(tmp_path).as_posix() not in result["evidence_sha256"]


@pytest.mark.parametrize(
    ("mutation", "blocker"),
    [
        ("missing_status", "OPENFOAM_RUNTIME_NOT_READY"),
        ("missing_solver", "OPENFOAM_SOLVER_EXECUTABLE_MISSING"),
        ("wrong_solver_path", "OPENFOAM_SOLVER_EXECUTABLE_INVALID"),
    ],
)
def test_runtime_openfoam_must_be_ready_with_exact_solver_executable(
    tmp_path, mutation, blocker
):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    runtime_path = tmp_path / manifest["sources"]["runtime_capability"]["path"]
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    if mutation == "missing_status":
        runtime["openfoam"].pop("status")
    elif mutation == "missing_solver":
        runtime["openfoam"].pop("solvers")
    else:
        runtime["openfoam"]["solvers"]["buoyantBoussinesqPimpleFoam"] = (
            "/usr/bin/simpleFoam"
        )
    _write(tmp_path, runtime_path.relative_to(tmp_path), runtime)
    _relink_environment_source(tmp_path, manifest_path, manifest, "runtime_capability")

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert blocker in result["blockers"]


def test_claimed_64_cells_without_independent_log_parse_is_blocked(tmp_path):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    mesh_log = tmp_path / manifest["sources"]["mesh_log"]["path"]
    mesh_log.write_text("Mesh stats\nMesh OK.\n", encoding="utf-8")
    _relink_environment_source(tmp_path, manifest_path, manifest, "mesh_log")

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "MESH_64_CELL_EVIDENCE_INVALID" in result["blockers"]
    assert "SOURCE_HASH_MISMATCH" not in result["blockers"]


@pytest.mark.parametrize(
    ("source_name", "replacement", "blocker"),
    [
        ("solver_log", "Time = 0\nEnd\n", "SOLVER_TIME_EVIDENCE_INVALID"),
        ("report", "", "REPORT_EVIDENCE_INVALID"),
    ],
)
def test_stale_or_empty_selected_log_and_report_are_blocked(
    tmp_path, source_name, replacement, blocker
):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    source = tmp_path / manifest["sources"][source_name]["path"]
    source.write_text(replacement, encoding="utf-8")
    _relink_environment_source(tmp_path, manifest_path, manifest, source_name)

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert blocker in result["blockers"]


@pytest.mark.parametrize(
    ("replacement", "blocker"),
    [
        ("Time = 0.5\nTime = 1\n", "SOLVER_LOG_INCOMPLETE"),
        ("Time = 1\nFOAM FATAL ERROR: forged clean run\nEnd\n", "SOLVER_LOG_FATAL"),
        ("Time = 1\nTime = 0.5\nEnd\n", "SOLVER_TIME_EVIDENCE_INVALID"),
    ],
)
def test_solver_log_requires_clean_end_without_fatal_and_final_time(
    tmp_path, replacement, blocker
):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    solver = tmp_path / manifest["sources"]["solver_log"]["path"]
    solver.write_text(replacement, encoding="utf-8")
    _relink_solver_log_everywhere(tmp_path, manifest_path, manifest)

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert blocker in result["blockers"]


def test_solver_startup_banner_does_not_masquerade_as_floating_point_crash(tmp_path):
    """OpenFOAM reports that trapping is enabled even when the solve completes."""
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    solver = tmp_path / manifest["sources"]["solver_log"]["path"]
    solver.write_text(
        "trapFpe: Floating point exception trapping enabled (FOAM_SIGFPE).\n"
        "Time = 0.5\nTime = 1\nEnd\n",
        encoding="utf-8",
    )
    _relink_solver_log_everywhere(tmp_path, manifest_path, manifest)

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "PASS"
    assert "SOLVER_LOG_FATAL" not in result["blockers"]


@pytest.mark.parametrize(
    ("replacement", "blocker"),
    [
        ("", "DIAGNOSTIC_LOG_EMPTY"),
        ("UNRELATED_DIAGNOSTIC\n", "DIAGNOSTIC_LOG_CODE_MISMATCH"),
    ],
)
def test_diagnostic_log_must_be_nonempty_and_name_its_code(
    tmp_path, replacement, blocker
):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    observation_path = tmp_path / manifest["diagnostic_observations"][0]["path"]
    observation = json.loads(observation_path.read_text(encoding="utf-8"))
    log_path = tmp_path / observation["log"]["path"]
    log_path.write_text(replacement, encoding="utf-8")
    _relink_diagnostic_log(tmp_path, manifest_path, manifest, 0)

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert blocker in result["blockers"]


def test_claimed_zero_tracebacks_is_recomputed_from_diagnostic_log(tmp_path):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    observation_path = tmp_path / manifest["diagnostic_observations"][0]["path"]
    observation = json.loads(observation_path.read_text(encoding="utf-8"))
    log_path = tmp_path / observation["log"]["path"]
    log_path.write_text(
        f"{observation['code']}\nTraceback (most recent call last):\nRuntimeError\n",
        encoding="utf-8",
    )
    _relink_diagnostic_log(tmp_path, manifest_path, manifest, 0)

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "DIAGNOSTIC_LOG_TRACEBACK_PRESENT" in result["blockers"]


@pytest.mark.parametrize(
    ("collection", "blocker"),
    [
        ("launch_observations", "LAUNCH_OBSERVATION_CARDINALITY_INVALID"),
        ("diagnostic_observations", "DIAGNOSTIC_OBSERVATION_CARDINALITY_INVALID"),
    ],
)
def test_observation_cardinality_has_stable_blocker(tmp_path, collection, blocker):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    manifest[collection].pop()
    _rewrite_manifest(manifest_path, manifest)

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert blocker in result["blockers"]


@pytest.mark.parametrize(
    ("mutation", "value"),
    [
        ("attempt", True),
        ("required_dom_marker", "MEP CFD Studio clone"),
        ("caller_asserted_pass", True),
    ],
)
def test_launch_observation_is_closed_and_uses_fixed_first_page_marker(
    tmp_path, mutation, value
):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    link = manifest["launch_observations"][0]
    observation_path = tmp_path / link["path"]
    observation = json.loads(observation_path.read_text(encoding="utf-8"))
    observation[mutation] = value
    _write(tmp_path, observation_path.relative_to(tmp_path), observation)
    manifest["launch_observations"][0] = _link(tmp_path, observation_path)
    _rewrite_manifest(manifest_path, manifest)

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "LAUNCH_OBSERVATION_INVALID" in result["blockers"]


@pytest.mark.parametrize(
    ("malicious_path", "blocker"),
    [
        ("../outside.json", "SOURCE_PATH_TRAVERSAL"),
        ("C:/outside.json", "SOURCE_PATH_ABSOLUTE_FORBIDDEN"),
        (r"_system\environment_acceptance\log.checkMesh", "SOURCE_PATH_BACKSLASH_FORBIDDEN"),
        ("_system/environment_acceptance/log.checkMesh:stream", "SOURCE_PATH_COLON_FORBIDDEN"),
    ],
)
def test_manifest_source_path_attacks_have_stable_blockers(tmp_path, malicious_path, blocker):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    manifest["sources"]["mesh_log"]["path"] = malicious_path
    _rewrite_manifest(manifest_path, manifest)

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert blocker in result["blockers"]


@pytest.mark.parametrize(
    ("forbidden_part", "blocker"),
    [
        ("cache", "SOURCE_CACHE_OR_TEMP_FORBIDDEN"),
        ("latest", "SOURCE_LATEST_FORBIDDEN"),
    ],
)
def test_cache_and_latest_sources_are_never_authoritative(
    tmp_path, forbidden_part, blocker
):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    original = tmp_path / manifest["sources"]["mesh_log"]["path"]
    forbidden = _write(
        tmp_path,
        RAW_ROOT / forbidden_part / "log.checkMesh",
        original.read_text(encoding="utf-8"),
    )
    manifest["sources"]["mesh_log"] = _link(tmp_path, forbidden)
    environment_path = tmp_path / manifest["sources"]["environment_acceptance"]["path"]
    environment = json.loads(environment_path.read_text(encoding="utf-8"))
    environment["mesh_log"] = manifest["sources"]["mesh_log"]
    _write(tmp_path, environment_path.relative_to(tmp_path), environment)
    manifest["sources"]["environment_acceptance"] = _link(tmp_path, environment_path)
    _rewrite_manifest(manifest_path, manifest)

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert blocker in result["blockers"]


def test_duplicate_source_reference_is_blocked(tmp_path):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    manifest["sources"]["report"] = dict(manifest["sources"]["solver_log"])
    environment_path = tmp_path / manifest["sources"]["environment_acceptance"]["path"]
    environment = json.loads(environment_path.read_text(encoding="utf-8"))
    environment["report"] = manifest["sources"]["report"]
    _write(tmp_path, environment_path.relative_to(tmp_path), environment)
    manifest["sources"]["environment_acceptance"] = _link(tmp_path, environment_path)
    _rewrite_manifest(manifest_path, manifest)

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "SOURCE_DUPLICATE" in result["blockers"]


@pytest.mark.parametrize(
    ("extra_name", "blocker"),
    [
        ("log.checkMesh.copy", "MESH_LOG_AMBIGUOUS"),
        ("log.alternateFoam", "SOLVER_LOG_AMBIGUOUS"),
        ("alternate-report.html", "REPORT_AMBIGUOUS"),
    ],
)
def test_multiple_raw_candidates_are_ambiguous(tmp_path, extra_name, blocker):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, _manifest = _build_bundle(tmp_path)
    _write(tmp_path, RAW_ROOT / extra_name, "alternate\n")

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert blocker in result["blockers"]


def test_evaluator_output_cannot_alias_or_overwrite_a_consumed_source(tmp_path):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    report = tmp_path / manifest["sources"]["report"]["path"]
    before = report.read_bytes()

    result = validate_local_usability_acceptance(manifest_path, tmp_path, report)

    assert result["status"] == "BLOCKED"
    assert "SOURCE_SELF_OUTPUT_FORBIDDEN" in result["blockers"]
    assert report.read_bytes() == before


@pytest.mark.parametrize(
    "output_relative",
    [Path("outside/evaluation.json"), RAW_ROOT / "evaluation.json"],
)
def test_evaluator_output_is_restricted_to_designated_working_area(
    tmp_path, output_relative
):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, _manifest = _build_bundle(tmp_path)
    output = tmp_path / output_relative

    result = validate_local_usability_acceptance(manifest_path, tmp_path, output)

    assert result["status"] == "BLOCKED"
    assert "EVALUATOR_OUTPUT_LOCATION_FORBIDDEN" in result["blockers"]
    assert not output.exists()


def test_evaluator_output_parent_must_be_reparse_safe(tmp_path, monkeypatch):
    import scripts.local_usability_acceptance as acceptance

    manifest_path, _manifest = _build_bundle(tmp_path)
    output = tmp_path / "_working_validation/evaluations/result.json"
    original = acceptance._path_has_link_or_reparse

    def output_parent_is_reparse(root, path):
        if Path(path) == output:
            return True
        return original(root, path)

    monkeypatch.setattr(acceptance, "_path_has_link_or_reparse", output_parent_is_reparse)
    result = acceptance.validate_local_usability_acceptance(manifest_path, tmp_path, output)

    assert result["status"] == "BLOCKED"
    assert "EVALUATOR_OUTPUT_REPARSE_FORBIDDEN" in result["blockers"]
    assert not output.exists()


def test_evaluator_output_is_two_run_idempotent_and_never_authoritative(tmp_path):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, _manifest = _build_bundle(tmp_path)
    output = tmp_path / "_working_validation/evaluations/serial_environment.json"
    raw_root = tmp_path / RAW_ROOT
    raw_before = {
        path.relative_to(raw_root).as_posix(): path.read_bytes()
        for path in raw_root.rglob("*") if path.is_file()
    }

    first = validate_local_usability_acceptance(manifest_path, tmp_path, output)
    first_bytes = output.read_bytes()
    second = validate_local_usability_acceptance(manifest_path, tmp_path, output)
    raw_after = {
        path.relative_to(raw_root).as_posix(): path.read_bytes()
        for path in raw_root.rglob("*") if path.is_file()
    }

    assert first == second
    assert first["status"] == "PASS"
    assert output.read_bytes() == first_bytes
    assert output.relative_to(tmp_path).as_posix() not in first["evidence_sha256"]
    assert raw_after == raw_before


def test_existing_evaluator_output_hardlink_to_source_is_not_overwritten(tmp_path):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    report = tmp_path / manifest["sources"]["report"]["path"]
    output = tmp_path / "_working_validation/evaluations/serial_environment.json"
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.hardlink_to(report)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")
    before = report.read_bytes()

    result = validate_local_usability_acceptance(manifest_path, tmp_path, output)

    assert result["status"] == "BLOCKED"
    assert "SOURCE_SELF_OUTPUT_FORBIDDEN" in result["blockers"]
    assert report.read_bytes() == before
    assert output.read_bytes() == before


def test_symlink_or_reparse_source_is_blocked(tmp_path, monkeypatch):
    import scripts.local_usability_acceptance as acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    mesh_log = (tmp_path / manifest["sources"]["mesh_log"]["path"]).resolve()
    monkeypatch.setattr(
        acceptance,
        "_path_has_link_or_reparse",
        lambda root, path: path.resolve() == mesh_log,
    )

    result = acceptance.validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "SOURCE_LINK_OR_REPARSE_FORBIDDEN" in result["blockers"]


@pytest.mark.parametrize(
    "noncanonical",
    [
        "_system//environment_acceptance/log.checkMesh",
        "_system/./environment_acceptance/log.checkMesh",
        "./_system/environment_acceptance/log.checkMesh",
        "_system/environment_acceptance/log.checkMesh/",
    ],
)
def test_noncanonical_source_spelling_is_blocked(tmp_path, noncanonical):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    manifest["sources"]["mesh_log"]["path"] = noncanonical
    _rewrite_manifest(manifest_path, manifest)

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "SOURCE_PATH_NON_CANONICAL" in result["blockers"]


def test_distinct_source_paths_to_same_hardlink_identity_are_duplicate(tmp_path):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    solver = tmp_path / manifest["sources"]["solver_log"]["path"]
    report = tmp_path / manifest["sources"]["report"]["path"]
    report.unlink()
    try:
        report.hardlink_to(solver)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")
    _relink_environment_source(tmp_path, manifest_path, manifest, "report")

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "SOURCE_DUPLICATE" in result["blockers"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows case-alias behavior")
def test_source_case_alias_is_not_canonical_identity(tmp_path):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    actual = manifest["sources"]["mesh_log"]["path"]
    alias = actual.replace("log.checkMesh", "LOG.CHECKMESH")
    manifest["sources"]["mesh_log"]["path"] = alias
    environment_path = tmp_path / manifest["sources"]["environment_acceptance"]["path"]
    environment = json.loads(environment_path.read_text(encoding="utf-8"))
    environment["mesh_log"] = manifest["sources"]["mesh_log"]
    _write(tmp_path, environment_path.relative_to(tmp_path), environment)
    manifest["sources"]["environment_acceptance"] = _link(tmp_path, environment_path)
    _rewrite_manifest(manifest_path, manifest)

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "SOURCE_PATH_CASE_MISMATCH" in result["blockers"]


def test_manifest_case_alias_is_not_the_fixed_lexical_path(tmp_path):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, _manifest = _build_bundle(tmp_path)
    alias = manifest_path.with_name(manifest_path.name.upper())

    result = validate_local_usability_acceptance(alias, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "MANIFEST_PATH_NON_CANONICAL" in result["blockers"]


def test_post_load_hash_drift_is_blocked(tmp_path, monkeypatch):
    import scripts.local_usability_acceptance as acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    mesh_log = (tmp_path / manifest["sources"]["mesh_log"]["path"]).resolve()
    original_sha256 = acceptance._sha256_file
    mesh_hash_calls = 0

    def drifting_sha256(path):
        nonlocal mesh_hash_calls
        if Path(path).resolve() == mesh_log:
            mesh_hash_calls += 1
            if mesh_hash_calls >= 2:
                return "f" * 64
        return original_sha256(path)

    monkeypatch.setattr(acceptance, "_sha256_file", drifting_sha256)

    result = acceptance.validate_local_usability_acceptance(manifest_path, tmp_path)

    assert mesh_hash_calls >= 2
    assert result["status"] == "BLOCKED"
    assert "SOURCE_POST_LOAD_HASH_DRIFT" in result["blockers"]


def test_same_content_file_replacement_changes_authoritative_identity(tmp_path, monkeypatch):
    import os
    import scripts.local_usability_acceptance as acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    mesh_log = (tmp_path / manifest["sources"]["mesh_log"]["path"]).resolve()
    original_sha256 = acceptance._sha256_file
    replaced = False

    def replace_after_hash(path):
        nonlocal replaced
        digest = original_sha256(path)
        if Path(path).resolve() == mesh_log and not replaced:
            replacement = mesh_log.with_name("replacement.checkMesh")
            replacement.write_bytes(mesh_log.read_bytes())
            os.replace(replacement, mesh_log)
            replaced = True
        return digest

    monkeypatch.setattr(acceptance, "_sha256_file", replace_after_hash)
    result = acceptance.validate_local_usability_acceptance(manifest_path, tmp_path)

    assert replaced
    assert result["status"] == "BLOCKED"
    assert "SOURCE_FILE_IDENTITY_CHANGED" in result["blockers"]


@pytest.mark.parametrize("source_name", ["case_meta", "allrun", "control_dict"])
def test_case_input_same_content_replacement_changes_authoritative_identity(
    tmp_path, monkeypatch, source_name
):
    import os
    import scripts.local_usability_acceptance as acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    source = (tmp_path / manifest["sources"][source_name]["path"]).resolve()
    original_sha256 = acceptance._sha256_file
    replaced = False

    def replace_after_hash(path):
        nonlocal replaced
        digest = original_sha256(path)
        if Path(path).resolve() == source and not replaced:
            replacement = source.with_name(source.name + ".replacement")
            replacement.write_bytes(source.read_bytes())
            os.replace(replacement, source)
            replaced = True
        return digest

    monkeypatch.setattr(acceptance, "_sha256_file", replace_after_hash)
    result = acceptance.validate_local_usability_acceptance(manifest_path, tmp_path)

    assert replaced
    assert result["status"] == "BLOCKED"
    assert "SOURCE_FILE_IDENTITY_CHANGED" in result["blockers"]


@pytest.mark.parametrize("source_name", ["case_meta", "allrun", "control_dict"])
def test_case_input_source_is_rehashed_after_semantic_validation(
    tmp_path, monkeypatch, source_name
):
    import scripts.local_usability_acceptance as acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    source = (tmp_path / manifest["sources"][source_name]["path"]).resolve()
    original_sha256 = acceptance._sha256_file
    calls = 0

    def drift_on_final_hash(path):
        nonlocal calls
        if Path(path).resolve() == source:
            calls += 1
            if calls >= 2:
                return "f" * 64
        return original_sha256(path)

    monkeypatch.setattr(acceptance, "_sha256_file", drift_on_final_hash)
    result = acceptance.validate_local_usability_acceptance(manifest_path, tmp_path)

    assert calls >= 2
    assert result["status"] == "BLOCKED"
    assert "SOURCE_POST_LOAD_HASH_DRIFT" in result["blockers"]


def test_second_solver_log_introduced_after_initial_inventory_is_blocked(
    tmp_path, monkeypatch
):
    import scripts.local_usability_acceptance as acceptance

    manifest_path, _manifest = _build_bundle(tmp_path)
    extra_solver_log = tmp_path / RAW_ROOT / "log.simpleFoam"
    original = acceptance._validate_runtime_semantics

    def add_log_after_initial_inventory(*args, **kwargs):
        original(*args, **kwargs)
        extra_solver_log.write_text("Time = 1\nEnd\n", encoding="utf-8")

    monkeypatch.setattr(
        acceptance, "_validate_runtime_semantics", add_log_after_initial_inventory
    )

    result = acceptance.validate_local_usability_acceptance(manifest_path, tmp_path)

    assert extra_solver_log.is_file()
    assert result["status"] == "BLOCKED"
    assert "RAW_INVENTORY_POST_LOAD_DRIFT" in result["blockers"]


def test_source_becoming_reparse_after_load_is_blocked(tmp_path, monkeypatch):
    import scripts.local_usability_acceptance as acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    mesh_log = (tmp_path / manifest["sources"]["mesh_log"]["path"]).resolve()
    original = acceptance._path_has_link_or_reparse
    target_checks = 0

    def turns_into_reparse(root, path):
        nonlocal target_checks
        if Path(path).resolve(strict=False) == mesh_log:
            target_checks += 1
            return target_checks >= 3
        return original(root, path)

    monkeypatch.setattr(acceptance, "_path_has_link_or_reparse", turns_into_reparse)
    result = acceptance.validate_local_usability_acceptance(manifest_path, tmp_path)

    assert target_checks >= 3
    assert result["status"] == "BLOCKED"
    assert "SOURCE_POST_LOAD_LINK_OR_REPARSE" in result["blockers"]


def test_fixed_manifest_becoming_reparse_after_load_is_blocked(tmp_path, monkeypatch):
    import scripts.local_usability_acceptance as acceptance

    manifest_path, _manifest = _build_bundle(tmp_path)
    original = acceptance._path_has_link_or_reparse
    manifest_checks = 0

    def manifest_turns_into_reparse(root, path):
        nonlocal manifest_checks
        if Path(path) == manifest_path:
            manifest_checks += 1
            return manifest_checks >= 2
        return original(root, path)

    monkeypatch.setattr(
        acceptance, "_path_has_link_or_reparse", manifest_turns_into_reparse
    )
    result = acceptance.validate_local_usability_acceptance(manifest_path, tmp_path)

    assert manifest_checks >= 2
    assert result["status"] == "BLOCKED"
    assert "MANIFEST_POST_LOAD_LINK_OR_REPARSE" in result["blockers"]


def test_in_place_manifest_drift_after_exact_parse_snapshot_is_blocked(
    tmp_path, monkeypatch
):
    import scripts.local_usability_acceptance as acceptance

    manifest_path, _manifest = _build_bundle(tmp_path)

    def snapshot_then_drift(path):
        raw = Path(path).read_bytes()
        payload = json.loads(raw.decode("utf-8"))
        drifted = raw.replace(b"serial-run-001", b"serial-run-002", 1)
        assert drifted != raw
        Path(path).write_bytes(drifted)
        return payload, hashlib.sha256(raw).hexdigest()

    monkeypatch.setattr(
        acceptance, "_read_manifest_snapshot", snapshot_then_drift, raising=False
    )
    result = acceptance.validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "MANIFEST_POST_LOAD_HASH_DRIFT" in result["blockers"]
    assert "MANIFEST_POST_LOAD_IDENTITY_CHANGED" not in result["blockers"]


def test_relinked_runtime_from_another_run_is_blocked(tmp_path):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    runtime_path = tmp_path / manifest["sources"]["runtime_capability"]["path"]
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    runtime["run_id"] = "serial-run-older"
    _write(tmp_path, runtime_path.relative_to(tmp_path), runtime)
    _relink_environment_source(tmp_path, manifest_path, manifest, "runtime_capability")

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "RUN_ID_MISMATCH" in result["blockers"]


def test_relinked_stale_runtime_identity_is_blocked(tmp_path):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    runtime_path = tmp_path / manifest["sources"]["runtime_capability"]["path"]
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    runtime["created_at"] = "2026-08-24T00:00:00Z"
    _write(tmp_path, runtime_path.relative_to(tmp_path), runtime)
    _relink_environment_source(tmp_path, manifest_path, manifest, "runtime_capability")

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "RUNTIME_CAPABILITY_STALE" in result["blockers"]


def test_acceptance_manifest_has_bounded_age_with_injectable_clock(tmp_path, monkeypatch):
    import scripts.local_usability_acceptance as acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    created_at = datetime.fromisoformat(manifest["created_at"].replace("Z", "+00:00"))
    monkeypatch.setattr(
        acceptance,
        "_utc_now",
        lambda: created_at + acceptance.MAX_ACCEPTANCE_AGE + timedelta(seconds=1),
    )

    result = acceptance.validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "ACCEPTANCE_WINDOW_EXPIRED" in result["blockers"]


def test_acceptance_manifest_rejects_excessive_future_skew(tmp_path, monkeypatch):
    import scripts.local_usability_acceptance as acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    created_at = datetime.fromisoformat(manifest["created_at"].replace("Z", "+00:00"))
    monkeypatch.setattr(
        acceptance,
        "_utc_now",
        lambda: created_at - acceptance.MAX_FUTURE_SKEW - timedelta(seconds=1),
    )

    result = acceptance.validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "ACCEPTANCE_TIMESTAMP_IN_FUTURE" in result["blockers"]


@pytest.mark.parametrize("record_kind", ["freecad", "launch", "diagnostic"])
def test_every_observation_is_bound_to_manifest_run_id(tmp_path, record_kind):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    if record_kind == "freecad":
        link = manifest["sources"]["freecad_diagnostics"]
        path = tmp_path / link["path"]
        record = json.loads(path.read_text(encoding="utf-8"))
        record["run_id"] = "serial-run-mixed"
        _write(tmp_path, path.relative_to(tmp_path), record)
        _relink_top_source(tmp_path, manifest_path, manifest, "freecad_diagnostics")
    else:
        collection = f"{record_kind}_observations"
        link = manifest[collection][0]
        path = tmp_path / link["path"]
        record = json.loads(path.read_text(encoding="utf-8"))
        record["run_id"] = "serial-run-mixed"
        _write(tmp_path, path.relative_to(tmp_path), record)
        manifest[collection][0] = _link(tmp_path, path)
        _rewrite_manifest(manifest_path, manifest)

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "RUN_ID_MISMATCH" in result["blockers"]


@pytest.mark.parametrize(
    ("record_kind", "timestamp_field", "blocker"),
    [
        ("freecad", "checked_at", "FREECAD_DIAGNOSTICS_STALE"),
        ("launch", "dom_ready_at", "LAUNCH_OBSERVATION_STALE"),
        ("diagnostic", "observed_at", "DIAGNOSTIC_OBSERVATION_STALE"),
    ],
)
def test_observation_timestamps_must_belong_to_current_acceptance_run(
    tmp_path, record_kind, timestamp_field, blocker
):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    if record_kind == "freecad":
        link = manifest["sources"]["freecad_diagnostics"]
        path = tmp_path / link["path"]
    else:
        collection = f"{record_kind}_observations"
        link = manifest[collection][0]
        path = tmp_path / link["path"]
    record = json.loads(path.read_text(encoding="utf-8"))
    record[timestamp_field] = "2000-01-01T00:00:00Z"
    _write(tmp_path, path.relative_to(tmp_path), record)
    if record_kind == "freecad":
        _relink_top_source(tmp_path, manifest_path, manifest, "freecad_diagnostics")
    else:
        manifest[collection][0] = _link(tmp_path, path)
        _rewrite_manifest(manifest_path, manifest)

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert blocker in result["blockers"]


def test_missing_hash_bound_diagnostic_log_is_blocked(tmp_path):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    diagnostic_path = tmp_path / manifest["diagnostic_observations"][0]["path"]
    diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    (tmp_path / diagnostic["log"]["path"]).unlink()

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "DIAGNOSTIC_LOG_MISSING" in result["blockers"]


def test_ready_freecad_claim_without_all_exact_stages_is_blocked(tmp_path):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    freecad_path = tmp_path / manifest["sources"]["freecad_diagnostics"]["path"]
    freecad = json.loads(freecad_path.read_text(encoding="utf-8"))
    freecad["stages"] = freecad["stages"][:-1]
    _write(tmp_path, freecad_path.relative_to(tmp_path), freecad)
    manifest["sources"]["freecad_diagnostics"] = _link(tmp_path, freecad_path)
    _rewrite_manifest(manifest_path, manifest)

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "FREECAD_DIAGNOSTICS_MISMATCH" in result["blockers"]


def test_freecad_staged_diagnostics_reject_unknown_fields(tmp_path):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    freecad_path = tmp_path / manifest["sources"]["freecad_diagnostics"]["path"]
    freecad = json.loads(freecad_path.read_text(encoding="utf-8"))
    freecad["caller_asserted_pass"] = True
    _write(tmp_path, freecad_path.relative_to(tmp_path), freecad)
    _relink_top_source(tmp_path, manifest_path, manifest, "freecad_diagnostics")

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "FREECAD_DIAGNOSTICS_SCHEMA_INVALID" in result["blockers"]


@pytest.mark.parametrize(
    ("stage_index", "field_path", "forged_value"),
    [
        (1, ("modules", "Part"), False),
        (2, ("valid",), False),
        (2, ("volume_mm3",), 1.0),
        (3, ("facets",), 0),
    ],
)
def test_freecad_pass_rows_recompute_stage_invariants(
    tmp_path, stage_index, field_path, forged_value
):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    freecad_path = tmp_path / manifest["sources"]["freecad_diagnostics"]["path"]
    freecad = json.loads(freecad_path.read_text(encoding="utf-8"))
    target = freecad["stages"][stage_index]["details"]
    for key in field_path[:-1]:
        target = target[key]
    target[field_path[-1]] = forged_value
    _write(tmp_path, freecad_path.relative_to(tmp_path), freecad)
    _relink_top_source(tmp_path, manifest_path, manifest, "freecad_diagnostics")

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "FREECAD_DIAGNOSTICS_INVARIANT_INVALID" in result["blockers"]


def test_python_executable_cannot_masquerade_as_freecadcmd(tmp_path):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    python_executable = Path(sys.executable).resolve()
    identity = manifest["identities"]["freecad"]
    identity["executable"] = str(python_executable)
    identity["executable_sha256"] = _sha256(python_executable)
    freecad_path = tmp_path / manifest["sources"]["freecad_diagnostics"]["path"]
    freecad = json.loads(freecad_path.read_text(encoding="utf-8"))
    freecad["executable"] = identity["executable"]
    freecad["executable_sha256"] = identity["executable_sha256"]
    _write(tmp_path, freecad_path.relative_to(tmp_path), freecad)
    _relink_top_source(tmp_path, manifest_path, manifest, "freecad_diagnostics")

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "FREECAD_EXECUTABLE_IDENTITY_INVALID" in result["blockers"]


def test_nonlocal_freecad_executable_is_rejected_before_hashing(tmp_path, monkeypatch):
    import scripts.local_usability_acceptance as acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    freecad_executable = Path(manifest["identities"]["freecad"]["executable"])
    original_sha256 = acceptance._sha256_file
    freecad_hash_calls = 0

    def tracked_sha256(path):
        nonlocal freecad_hash_calls
        if Path(path) == freecad_executable:
            freecad_hash_calls += 1
        return original_sha256(path)

    monkeypatch.setattr(
        acceptance,
        "_is_strict_local_executable_path",
        lambda _path: False,
        raising=False,
    )
    monkeypatch.setattr(acceptance, "_sha256_file", tracked_sha256)

    result = acceptance.validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "FREECAD_EXECUTABLE_IDENTITY_INVALID" in result["blockers"]
    assert freecad_hash_calls == 0


def test_runtime_pass_claim_without_required_metrics_is_blocked(tmp_path):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    runtime_path = tmp_path / manifest["sources"]["runtime_capability"]["path"]
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    runtime["serial_baseline"]["runner_wall_seconds"] = None
    _write(tmp_path, runtime_path.relative_to(tmp_path), runtime)
    _relink_environment_source(tmp_path, manifest_path, manifest, "runtime_capability")

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "SERIAL_RUNTIME_METRICS_INVALID" in result["blockers"]


@pytest.mark.parametrize(
    ("source_name", "alternate_name", "blocker"),
    [
        ("mesh_log", "selected-mesh.txt", "MESH_LOG_SELECTION_MISMATCH"),
        ("solver_log", "selected-solver.txt", "SOLVER_LOG_SELECTION_MISMATCH"),
        ("report", "selected-report.txt", "REPORT_SELECTION_MISMATCH"),
    ],
)
def test_selected_artifact_must_equal_sole_raw_candidate(
    tmp_path, source_name, alternate_name, blocker
):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    original = tmp_path / manifest["sources"][source_name]["path"]
    alternate = _write(
        tmp_path, RAW_ROOT / alternate_name,
        original.read_text(encoding="utf-8"),
    )
    manifest["sources"][source_name] = _link(tmp_path, alternate)
    environment_path = tmp_path / manifest["sources"]["environment_acceptance"]["path"]
    environment = json.loads(environment_path.read_text(encoding="utf-8"))
    environment[source_name] = manifest["sources"][source_name]
    _write(tmp_path, environment_path.relative_to(tmp_path), environment)
    manifest["sources"]["environment_acceptance"] = _link(tmp_path, environment_path)
    _rewrite_manifest(manifest_path, manifest)

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert blocker in result["blockers"]


def test_solver_log_name_must_match_control_dict_application(tmp_path):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    original = tmp_path / manifest["sources"]["solver_log"]["path"]
    wrong_solver_log = original.with_name("log.simpleFoam")
    original.rename(wrong_solver_log)
    manifest["sources"]["solver_log"]["path"] = wrong_solver_log.relative_to(
        tmp_path
    ).as_posix()
    _relink_solver_log_everywhere(tmp_path, manifest_path, manifest)

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "SOLVER_LOG_IDENTITY_INVALID" in result["blockers"]


def test_solver_log_must_be_at_canonical_raw_root_path(tmp_path):
    from scripts.local_usability_acceptance import validate_local_usability_acceptance

    manifest_path, manifest = _build_bundle(tmp_path)
    original = tmp_path / manifest["sources"]["solver_log"]["path"]
    nested = original.parent / "nested" / original.name
    nested.parent.mkdir()
    original.rename(nested)
    manifest["sources"]["solver_log"]["path"] = nested.relative_to(
        tmp_path
    ).as_posix()
    _relink_solver_log_everywhere(tmp_path, manifest_path, manifest)

    result = validate_local_usability_acceptance(manifest_path, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "SOLVER_LOG_IDENTITY_INVALID" in result["blockers"]
