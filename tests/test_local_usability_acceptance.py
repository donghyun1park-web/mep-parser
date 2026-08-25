import hashlib
import json
import platform
import sys
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


def _build_bundle(root: Path) -> tuple[Path, dict]:
    raw_root = root / RAW_ROOT
    case_input = _write(root, RAW_ROOT / "system/controlDict", "endTime 1;\n")
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
    freecad_diagnostics = _write(root, RAW_ROOT / "freecad_stages.json", {
        "contract": "freecad_staged_diagnostics.v1",
        "ok": True,
        "status": "ready",
        "failed_stage": None,
        "executable": str(executable),
        "executable_sha256": executable_sha256,
        "freecad_version": "1.1.1",
        "occ_version": "7.8.1",
        "compatible_profile": "freecad-1.1.1-occ-7.8.1",
        "stages": [
            {"id": stage, "status": "PASS", "reason_code": ""}
            for stage in ("discovery", "imports", "boolean", "tessellation")
        ],
    })
    runtime_capability = _write(root, "_working_validation/runtime_capability.v1.json", {
        "schema_version": 1,
        "contract": "runtime_capability.v1",
        "created_at": "2026-08-25T00:00:09+00:00",
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
            "case_input_sha256": _sha256(case_input),
            "solver_log_sha256": _sha256(solver_log),
        },
    })
    environment_acceptance = _write(root, RAW_ROOT / "environment_acceptance.json", {
        "contract": "environment_acceptance.v1",
        "created_at": "2026-08-25T00:00:09Z",
        "run_id": "serial-run-001",
        "status": "PASS",
        "mesh_ok": True,
        "cells": 64,
        "latest_time": 1.0,
        "openfoam_profile": "openfoam-v2606",
        "case_input": _link(root, case_input),
        "mesh_log": _link(root, mesh_log),
        "solver_log": _link(root, solver_log),
        "report": _link(root, report),
        "runtime_capability": _link(root, runtime_capability),
    })

    launches = []
    for attempt in range(1, 4):
        observation = _write(root, RAW_ROOT / f"launch-{attempt}.json", {
            "contract": "studio_launch_observation.v1",
            "attempt": attempt,
            "process_started_at": f"2026-08-25T00:00:0{attempt}Z",
            "http_ready_at": f"2026-08-25T00:00:0{attempt + 1}Z",
            "dom_ready_at": f"2026-08-25T00:00:0{attempt + 2}Z",
            "required_dom_marker": "MEP CFD Studio",
            "status": "PASS",
        })
        launches.append(_link(root, observation))

    diagnostics = []
    for index, code in enumerate(DIAGNOSTIC_CODES, start=1):
        diagnostic_log = _write(root, RAW_ROOT / f"diagnostic-{index}.log", f"{code}\n")
        observation = _write(root, RAW_ROOT / f"diagnostic-{index}.json", {
            "contract": "actionable_diagnostic_observation.v1",
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
        "created_at": "2026-08-25T00:00:10Z",
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
                "executable": str(executable),
                "executable_sha256": executable_sha256,
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
            "case_input": _link(root, case_input),
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
    ("malicious_path", "blocker"),
    [
        ("../outside.json", "SOURCE_PATH_TRAVERSAL"),
        ("C:/outside.json", "SOURCE_PATH_ABSOLUTE_FORBIDDEN"),
        (r"_system\environment_acceptance\log.checkMesh", "SOURCE_PATH_BACKSLASH_FORBIDDEN"),
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
