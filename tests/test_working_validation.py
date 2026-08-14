import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


CHECK_IDS = (
    "code_baseline",
    "filesystem_io",
    "serial_environment",
    "working_room_e2e",
    "real_dxf_screening",
    "restart_integrity",
    "exact_heat_verification",
    "limited_numerical_spotchecks",
)

CHECK_PATHS = {
    "code_baseline": "_working_validation/evidence/code_baseline.json",
    "filesystem_io": "_working_validation/evidence/filesystem_io.json",
    "serial_environment": "capability_manifest.json",
    "working_room_e2e": "_working_validation/evidence/working_room_e2e.json",
    "real_dxf_screening": "_working_validation/evidence/real_dxf_screening.json",
    "restart_integrity": "_working_validation/evidence/restart_integrity.json",
    "exact_heat_verification": "_working_validation/evidence/exact_heat_verification.json",
    "limited_numerical_spotchecks": "_working_validation/evidence/limited_numerical_spotchecks.json",
}


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json(root, relative, value):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    return path


def _link(root, relative, *, contract=None):
    path = root / relative
    value = {"path": relative.replace("\\", "/"), "sha256": _sha256(path)}
    if contract:
        value["contract"] = contract
    return value


def _write_case(root):
    case = root / "_body_solver" / "room-001"
    artifacts = {
        "log.checkMesh": "checkMesh recovered output",
        "log.buoyantBoussinesqPimpleFoam": "solver recovered output",
        "12/T": "temperature",
        "12/U": "velocity",
        "12/phi": "flux",
        "12/V": "volume",
        "results/room-001.vtu": "vtu",
        "reports/room-001.html": "html",
    }
    for relative, contents in artifacts.items():
        path = case / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    mesh = _write_json(root, "_body_solver/room-001/mesh_manifest.json", {
        "contract": "mesh_manifest.v1", "status": "PASS", "created_at": _stamp(),
    })
    run = _write_json(root, "_body_solver/room-001/run_manifest.json", {
        "contract": "run_manifest.v1", "status": "PASS", "created_at": _stamp(),
        "requested_ranks": 1,
    })
    result = _write_json(root, "_body_solver/room-001/result_manifest.json", {
        "contract": "result_manifest.v1", "created_at": _stamp(),
        "source": _link(root, "_body_solver/room-001/results/room-001.vtu"),
        "html": _link(root, "_body_solver/room-001/reports/room-001.html"),
        "mesh_manifest_sha256": _sha256(mesh),
        "run_manifest_sha256": _sha256(run),
    })
    return case


def _write_authoritative_artifacts(root, check_ids=CHECK_IDS):
    """Create fixed-path, contract-specific fixture artifacts; never a source wrapper."""
    from vv_baseline import build_vv_baseline

    _write_case(root)
    baseline = build_vv_baseline(Path(__file__).resolve().parents[1], root)
    baseline["created_at"] = _stamp()
    baseline_path = _write_json(root, "_working_validation/evidence/vv_baseline.json", baseline)

    inventory = _write_json(root, "_working_validation/evidence/authoritative_case_inventory.v1.json", {
        "contract": "io_authoritative_case_inventory.v1", "created_at": _stamp(),
        "cases": [{
            "case_id": "room-001", "case_path": "_body_solver/room-001",
            "mesh_manifest": _link(root, "_body_solver/room-001/mesh_manifest.json", contract="mesh_manifest.v1"),
            "run_manifest": _link(root, "_body_solver/room-001/run_manifest.json", contract="run_manifest.v1"),
            "result_manifest": _link(root, "_body_solver/room-001/result_manifest.json", contract="result_manifest.v1"),
            "check_mesh_log": _link(root, "_body_solver/room-001/log.checkMesh"),
            "solver_log": _link(root, "_body_solver/room-001/log.buoyantBoussinesqPimpleFoam"),
            "latest_time": "12",
            "fields": {name: _link(root, f"_body_solver/room-001/12/{name}") for name in ("T", "U", "phi", "V")},
            "vtu": _link(root, "_body_solver/room-001/results/room-001.vtu"),
            "html": _link(root, "_body_solver/room-001/reports/room-001.html"),
        }],
    })
    io = _write_json(root, "_working_validation/evidence/io_acceptance.json", {
        "schema_version": 1, "contract": "io_acceptance.v1", "created_at": _stamp(),
        "status": "PASS", "inventory_path": str(inventory.relative_to(root)).replace("\\", "/"),
        "inventory_sha256": _sha256(inventory), "artifact_probes": [{"status": "PASS", "read": True, "sha256": "a" * 64}],
    })
    capability = _write_json(root, "capability_manifest.json", {
        "schema_version": 1, "contract": "runtime_capability.v1", "created_at": _stamp(),
        "serial_runtime_ready": True, "serial_only": True,
        "cpu": {"effective_logical_count": 1},
        "serial_baseline": {"status": "PASS", "solver_log_sha256": "b" * 64},
    })

    common = {"created_at": _stamp(), "target_identity": "single_pc_serial_current_user"}
    docs = {
        "code_baseline": dict(common, contract="working_validation.code_baseline.v1", check_id="code_baseline",
                              baseline=_link(root, str(baseline_path.relative_to(root)), contract="vv_baseline.v1")),
        "filesystem_io": dict(common, contract="working_validation.filesystem_io.v1", check_id="filesystem_io",
                               io_acceptance=_link(root, str(io.relative_to(root)), contract="io_acceptance.v1")),
        "working_room_e2e": dict(common, contract="working_validation.working_room_e2e.v1", check_id="working_room_e2e",
                                  case_id="room-001", case_path="_body_solver/room-001",
                                  artifacts={name: _link(root, f"_body_solver/room-001/{name}") for name in ("mesh_manifest.json", "run_manifest.json", "result_manifest.json", "log.checkMesh", "log.buoyantBoussinesqPimpleFoam", "12/T", "12/U", "12/phi", "12/V", "results/room-001.vtu", "reports/room-001.html")}),
        "real_dxf_screening": dict(common, contract="working_validation.real_dxf_screening.v1", check_id="real_dxf_screening",
                                    source_dxf=None, geometry=None, source_is_real=True, screening_status="PASS"),
        "restart_integrity": dict(common, contract="working_validation.restart_integrity.v1", check_id="restart_integrity",
                                  restart_input=None, run_manifest=_link(root, "_body_solver/room-001/run_manifest.json", contract="run_manifest.v1"), restart_status="PASS"),
        "exact_heat_verification": dict(common, contract="working_validation.exact_heat_verification.v1", check_id="exact_heat_verification",
                                         result_manifest=_link(root, "_body_solver/room-001/result_manifest.json", contract="result_manifest.v1"), heat_report=None,
                                         status="PASS", max_relative_error=0.01, tolerance=0.02),
        "limited_numerical_spotchecks": dict(common, contract="working_validation.limited_numerical_spotchecks.v1", check_id="limited_numerical_spotchecks",
                                               result_manifest=_link(root, "_body_solver/room-001/result_manifest.json", contract="result_manifest.v1"), spotcheck_report=None,
                                               status="PASS", qoi_count=2),
    }
    dxf = root / "_field_jobs" / "room-001.dxf"
    dxf.parent.mkdir(parents=True, exist_ok=True)
    dxf.write_text("real fixture dxf", encoding="utf-8")
    geometry = _write_json(root, "_field_jobs/room-001.geometry.json", {"contract": "geometry.v2", "source_dxf_sha256": _sha256(dxf)})
    docs["real_dxf_screening"]["source_dxf"] = _link(root, "_field_jobs/room-001.dxf")
    docs["real_dxf_screening"]["geometry"] = _link(root, str(geometry.relative_to(root)), contract="geometry.v2")
    restart = _write_json(root, "_body_solver/room-001/thermal_restart_input.json", {"contract": "thermal_restart_input.v1", "created_at": _stamp(), "restart_fingerprint": "c" * 64})
    docs["restart_integrity"]["restart_input"] = _link(root, str(restart.relative_to(root)), contract="thermal_restart_input.v1")
    heat = _write_json(root, "_working_validation/evidence/exact_heat_report.json", {"contract": "exact_heat_report.v1", "status": "PASS", "created_at": _stamp(), "max_relative_error": 0.01, "tolerance": 0.02})
    docs["exact_heat_verification"]["heat_report"] = _link(root, str(heat.relative_to(root)), contract="exact_heat_report.v1")
    spot = _write_json(root, "_working_validation/evidence/limited_numerical_spotchecks_report.json", {"contract": "limited_numerical_spotchecks_report.v1", "status": "PASS", "created_at": _stamp(), "qoi_count": 2})
    docs["limited_numerical_spotchecks"]["spotcheck_report"] = _link(root, str(spot.relative_to(root)), contract="limited_numerical_spotchecks_report.v1")
    for check_id, payload in docs.items():
        if check_id in check_ids:
            _write_json(root, CHECK_PATHS[check_id], payload)
    if "serial_environment" in check_ids:
        assert capability.is_file()
    return root


def test_fake_pass_cannot_promote_working_status(tmp_path):
    from working_validation import evaluate_working_validation

    (tmp_path / "_release_evidence" / "working_validation" ).mkdir(parents=True)
    _write_json(tmp_path, "_release_evidence/working_validation/sources.json", {"status": "PASS", "checks": {}})
    result = evaluate_working_validation(tmp_path)

    assert result["status"] == "BLOCKED"
    assert result["working_ready_on_target"] is False
    assert result["design_citable"] is False
    assert result["release_ready"] is False


def test_fixed_authoritative_artifacts_produce_working_status(tmp_path):
    from working_validation import evaluate_working_validation

    _write_authoritative_artifacts(tmp_path, CHECK_IDS[:6])

    result = evaluate_working_validation(tmp_path)

    assert result["status"] == "WORKING_SINGLE_PC"
    assert result["working_ready_on_target"] is True
    assert result["limited_numerical_spotchecks_pass_on_target"] is False
    assert [row["id"] for row in result["checks"]] == list(CHECK_IDS)
    assert [row["id"] for row in result["numerical_blockers"]] == list(CHECK_IDS[6:])


def test_fixed_authoritative_artifacts_produce_numerical_spotcheck_status(tmp_path):
    from working_validation import evaluate_working_validation

    _write_authoritative_artifacts(tmp_path)

    result = evaluate_working_validation(tmp_path)

    assert result["status"] == "NUMERICAL_SPOTCHECK_PASS_SINGLE_PC"
    assert result["working_ready_on_target"] is True
    assert result["limited_numerical_spotchecks_pass_on_target"] is True
    assert result["design_citable"] is False
    assert result["release_ready"] is False


def test_fresh_generic_json_at_fixed_path_cannot_promote_even_with_matching_hash(tmp_path):
    from working_validation import evaluate_working_validation

    _write_authoritative_artifacts(tmp_path, CHECK_IDS[:6])
    fake = _write_json(tmp_path, CHECK_PATHS["code_baseline"], {
        "contract": "recovered_evidence.v1", "created_at": _stamp(), "artifact": "code_baseline",
    })

    result = evaluate_working_validation(tmp_path)

    assert result["status"] == "BLOCKED"
    assert any(row["id"] == "code_baseline" and row["status"] == "BLOCKED" for row in result["checks"])
    assert fake.is_file()


def test_check_document_cannot_nominate_a_valid_alternate_source_path(tmp_path):
    from working_validation import evaluate_working_validation

    _write_authoritative_artifacts(tmp_path, CHECK_IDS[:6])
    original = tmp_path / "_working_validation/evidence/vv_baseline.json"
    alternate = _write_json(tmp_path, "_field_jobs/alternate-baseline.json", json.loads(original.read_text(encoding="utf-8")))
    document = tmp_path / CHECK_PATHS["code_baseline"]
    payload = json.loads(document.read_text(encoding="utf-8"))
    payload["baseline"] = _link(tmp_path, str(alternate.relative_to(tmp_path)), contract="vv_baseline.v1")
    document.write_text(json.dumps(payload), encoding="utf-8")

    result = evaluate_working_validation(tmp_path)

    assert result["status"] == "BLOCKED"
    assert any(row["id"] == "code_baseline" and row["status"] == "BLOCKED" for row in result["checks"])


@pytest.mark.parametrize("bad_relative", [
    "_release_evidence/published/working_validation.json",
    "_release_evidence/published/working-validation.html",
    ".cache/alias.json",
    ".pytest_cache/alias.json",
    "tmp/alias.json",
])
def test_published_cache_and_temp_aliases_cannot_enter_authoritative_evidence(tmp_path, bad_relative):
    from working_validation import evaluate_working_validation

    _write_authoritative_artifacts(tmp_path, CHECK_IDS[:6])
    alias = _write_json(tmp_path, bad_relative, {"contract": "geometry.v2"})
    document = tmp_path / CHECK_PATHS["real_dxf_screening"]
    # It is a numerical check here, so add it as a direct source without changing the fixed registry.
    payload = json.loads(document.read_text(encoding="utf-8")) if document.is_file() else None
    if payload is None:
        _write_authoritative_artifacts(tmp_path)
        document = tmp_path / CHECK_PATHS["real_dxf_screening"]
        payload = json.loads(document.read_text(encoding="utf-8"))
    payload["geometry"] = {"path": bad_relative, "sha256": _sha256(alias), "contract": "geometry.v2"}
    document.write_text(json.dumps(payload), encoding="utf-8")

    result = evaluate_working_validation(tmp_path)

    assert result["status"] != "NUMERICAL_SPOTCHECK_PASS_SINGLE_PC"
    assert any(row["id"] == "real_dxf_screening" and row["status"] == "BLOCKED" for row in result["checks"])


def test_write_rejects_arbitrary_output_alias_of_authoritative_input(tmp_path):
    from working_validation import write_working_validation

    _write_authoritative_artifacts(tmp_path, CHECK_IDS[:6])

    with pytest.raises(ValueError, match="OUTPUT_ALIAS"):
        write_working_validation(tmp_path, tmp_path / CHECK_PATHS["code_baseline"])


def test_schema_rejects_duplicate_ids_contradictory_state_and_bad_timestamp(tmp_path):
    from working_validation import evaluate_working_validation, validate_working_validation_payload

    _write_authoritative_artifacts(tmp_path, CHECK_IDS[:6])
    payload = evaluate_working_validation(tmp_path)
    payload["checks"][1]["id"] = payload["checks"][0]["id"]
    assert validate_working_validation_payload(payload)

    payload = evaluate_working_validation(tmp_path)
    payload.update({"status": "NUMERICAL_SPOTCHECK_PASS_SINGLE_PC", "working_ready_on_target": False})
    assert validate_working_validation_payload(payload)

    payload = evaluate_working_validation(tmp_path)
    payload["created_at"] = "not-rfc3339"
    assert validate_working_validation_payload(payload)


def test_compare_runs_validates_inputs_and_includes_all_declared_semantics(tmp_path):
    from working_validation import compare_working_validation_runs, write_working_validation

    _write_authoritative_artifacts(tmp_path, CHECK_IDS[:6])
    first = tmp_path / "working_validation-run-one.json"
    second = tmp_path / "working_validation-run-two.json"
    write_working_validation(tmp_path, first)
    write_working_validation(tmp_path, second)
    assert compare_working_validation_runs(first, second)["equal"] is True

    second_payload = json.loads(second.read_text(encoding="utf-8"))
    second_payload["evidence_sha256"]["semantic-change"] = "0" * 64
    second.write_text(json.dumps(second_payload), encoding="utf-8")
    assert compare_working_validation_runs(first, second)["equal"] is False

    first.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="RUN_INVALID"):
        compare_working_validation_runs(first, second)
