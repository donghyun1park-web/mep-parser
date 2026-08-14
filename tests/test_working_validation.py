import hashlib
import json
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
BASELINE_PATH = "_working_validation/evidence/vv_baseline.json"
IO_ACCEPTANCE_PATH = "_working_validation/evidence/io_acceptance.json"


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


def _write_recovered_case(root):
    case = root / "_body_solver" / "room-001"
    for relative in (
        "log.checkMesh", "log.buoyantBoussinesqPimpleFoam", "12/T", "12/U", "12/phi", "12/V",
        "results/room-001.vtu", "reports/room-001.html",
    ):
        path = case / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
    mesh = _write_json(root, "_body_solver/room-001/mesh_manifest.json", {"contract": "mesh_manifest.v1", "status": "PASS"})
    run = _write_json(root, "_body_solver/room-001/run_manifest.json", {"contract": "run_manifest.v1", "status": "PASS", "requested_ranks": 1})
    _write_json(root, "_body_solver/room-001/result_manifest.json", {
        "contract": "result_manifest.v1",
        "source": _link(root, "_body_solver/room-001/results/room-001.vtu"),
        "html": _link(root, "_body_solver/room-001/reports/room-001.html"),
        "mesh_manifest_sha256": _sha256(mesh),
        "run_manifest_sha256": _sha256(run),
    })
    return case


def _write_real_task1_raw_artifacts(root):
    """Use the existing raw baseline builder and I/O recomputation, never a PASS wrapper."""
    from scripts.io_acceptance import run_io_acceptance
    from vv_baseline import build_vv_baseline

    case = _write_recovered_case(root)
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
            "fields": {field: _link(root, f"_body_solver/room-001/12/{field}") for field in ("T", "U", "phi", "V")},
            "vtu": _link(root, "_body_solver/room-001/results/room-001.vtu"),
            "html": _link(root, "_body_solver/room-001/reports/room-001.html"),
        }],
    })
    assert inventory.is_file()
    baseline = build_vv_baseline(Path(__file__).resolve().parents[1], root)
    _write_json(root, BASELINE_PATH, baseline)
    # This computes the root probes and every selected recovered-artifact hash.
    _write_json(root, IO_ACCEPTANCE_PATH, run_io_acceptance(root))
    return case


def _write_complete_matching_dummy_bundle(root):
    """Reviewer-described future-looking documents; public Task 1 must ignore them."""
    _write_real_task1_raw_artifacts(root)
    for check_id in CHECK_IDS[2:]:
        _write_json(root, f"_working_validation/evidence/{check_id}.json", {
            "contract": f"working_validation.{check_id}.v1",
            "check_id": check_id,
            "created_at": _stamp(),
            "status": "PASS",
            "sha256": "a" * 64,
            "artifacts": {"dummy": {"path": "dummy", "sha256": "b" * 64}},
        })
    (root / "capability_manifest.json").write_text(json.dumps({"contract": "runtime_capability.v1", "status": "PASS"}), encoding="utf-8")


def _results(statuses):
    from working_validation import CheckResult

    return [CheckResult(check_id, status, () if status == "PASS" else ("BLOCKED_FOR_TEST",)) for check_id, status in zip(CHECK_IDS, statuses)]


def test_result_to_state_purely_derives_blocked_working_and_numerical_transitions():
    from working_validation import _derive_working_validation_state

    blocked = _derive_working_validation_state(_results(["BLOCKED"] + ["PASS"] * 7))
    working = _derive_working_validation_state(_results(["PASS"] * 6 + ["BLOCKED", "NOT_EVALUATED"]))
    numerical = _derive_working_validation_state(_results(["PASS"] * 8))

    assert blocked["status"] == "BLOCKED"
    assert blocked["working_ready_on_target"] is False
    assert working["status"] == "WORKING_SINGLE_PC"
    assert working["working_ready_on_target"] is True
    assert working["limited_numerical_spotchecks_pass_on_target"] is False
    assert numerical["status"] == "NUMERICAL_SPOTCHECK_PASS_SINGLE_PC"
    assert numerical["limited_numerical_spotchecks_pass_on_target"] is True
    assert numerical["design_citable"] is False
    assert numerical["release_ready"] is False


def test_fake_wrapper_or_updated_matching_hash_cannot_promote_working_status(tmp_path):
    from working_validation import evaluate_working_validation

    _write_json(tmp_path, "_release_evidence/working_validation/sources.json", {"status": "PASS", "checks": {}})
    _write_json(tmp_path, "_working_validation/evidence/code_baseline.json", {
        "contract": "recovered_evidence.v1", "created_at": _stamp(), "sha256": "a" * 64,
    })

    result = evaluate_working_validation(tmp_path)

    assert result["status"] == "BLOCKED"
    assert result["working_ready_on_target"] is False
    assert result["design_citable"] is False
    assert result["release_ready"] is False


def test_public_evaluator_uses_real_task_one_raw_recomputation_but_future_checks_block(tmp_path):
    from working_validation import evaluate_working_validation

    _write_real_task1_raw_artifacts(tmp_path)

    result = evaluate_working_validation(tmp_path)

    assert result["status"] == "BLOCKED"
    assert [row["status"] for row in result["checks"][:2]] == ["PASS", "PASS"]
    for row in result["checks"][2:]:
        assert row["status"] == "BLOCKED"
        assert row["blockers"] == [f"{row['id'].upper()}_VALIDATOR_NOT_IMPLEMENTED"]


def test_complete_matching_dummy_bundle_remains_blocked(tmp_path):
    from working_validation import evaluate_working_validation

    _write_complete_matching_dummy_bundle(tmp_path)

    result = evaluate_working_validation(tmp_path)

    assert result["status"] == "BLOCKED"
    assert result["working_ready_on_target"] is False
    assert result["checks"][2]["blockers"] == ["SERIAL_ENVIRONMENT_VALIDATOR_NOT_IMPLEMENTED"]
    assert result["checks"][-1]["blockers"] == ["LIMITED_NUMERICAL_SPOTCHECKS_VALIDATOR_NOT_IMPLEMENTED"]


def test_stale_task_one_raw_evidence_blocks_its_check(tmp_path):
    from working_validation import evaluate_working_validation

    _write_real_task1_raw_artifacts(tmp_path)
    baseline = tmp_path / BASELINE_PATH
    payload = json.loads(baseline.read_text(encoding="utf-8"))
    payload["created_at"] = "2000-01-01T00:00:00Z"
    baseline.write_text(json.dumps(payload), encoding="utf-8")

    result = evaluate_working_validation(tmp_path)

    assert result["checks"][0]["status"] == "BLOCKED"
    assert "BASELINE_STALE_OR_TIMESTAMP_INVALID" in result["checks"][0]["blockers"]


def test_exact_registry_path_rejects_a_valid_alternate_baseline(tmp_path):
    from vv_baseline import build_vv_baseline
    from working_validation import evaluate_working_validation

    alternate = _write_json(tmp_path, "_field_jobs/alternate-baseline.json", build_vv_baseline(Path(__file__).resolve().parents[1], tmp_path))
    assert alternate.is_file()

    result = evaluate_working_validation(tmp_path)

    assert result["checks"][0]["status"] == "BLOCKED"
    assert "AUTHORITATIVE_ARTIFACT_MISSING" in result["checks"][0]["blockers"]


def test_write_rejects_output_alias_of_a_task_one_authoritative_input(tmp_path):
    from working_validation import write_working_validation

    _write_real_task1_raw_artifacts(tmp_path)

    with pytest.raises(ValueError, match="OUTPUT_ALIAS"):
        write_working_validation(tmp_path, tmp_path / BASELINE_PATH)


def test_schema_rejects_duplicate_ids_contradictory_state_and_bad_timestamp(tmp_path):
    from working_validation import evaluate_working_validation, validate_working_validation_payload

    _write_real_task1_raw_artifacts(tmp_path)
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

    _write_real_task1_raw_artifacts(tmp_path)
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
