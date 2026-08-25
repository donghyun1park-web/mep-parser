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


def test_external_results_dispatches_fixed_code_owned_validators_and_merges_hashes(
    tmp_path, monkeypatch,
):
    import cfd_numerical_spotcheck
    import cfd_verification
    import cfd_working_room
    from scripts import local_usability_acceptance
    import working_validation

    root = tmp_path.resolve()
    calls = []

    evidence_paths = {
        check_id: _write_json(root, f"evidence/{check_id}.json", {"id": check_id})
        for check_id in CHECK_IDS
    }

    def fake_validator(check_id):
        def validate(manifest_path, projects_root, evaluator_output=None):
            calls.append((
                check_id, Path(manifest_path), Path(projects_root),
                Path(evaluator_output) if evaluator_output is not None else None,
            ))
            return {
                "check_id": check_id,
                "status": "PASS",
                "blockers": [],
                "evidence_sha256": {
                    f"evidence/{check_id}.json": _sha256(evidence_paths[check_id])
                },
            }

        return validate

    monkeypatch.setattr(
        working_validation,
        "_evaluate_code_baseline",
        lambda _root, _output: (
            working_validation.CheckResult("code_baseline", "PASS"),
            {"evidence/code_baseline.json": _sha256(evidence_paths["code_baseline"])},
        ),
    )
    monkeypatch.setattr(
        working_validation,
        "_evaluate_filesystem_io",
        lambda _root, _output: (
            working_validation.CheckResult("filesystem_io", "PASS"),
            {"evidence/filesystem_io.json": _sha256(evidence_paths["filesystem_io"])},
        ),
    )
    monkeypatch.setattr(
        local_usability_acceptance,
        "validate_local_usability_acceptance",
        fake_validator("serial_environment"),
    )
    monkeypatch.setattr(
        cfd_working_room,
        "validate_working_room",
        fake_validator("working_room_e2e"),
    )
    monkeypatch.setattr(
        cfd_working_room,
        "validate_sgi_screening_acceptance",
        fake_validator("real_dxf_screening"),
    )
    monkeypatch.setattr(
        cfd_working_room,
        "validate_restart_integrity",
        fake_validator("restart_integrity"),
    )
    monkeypatch.setattr(
        cfd_verification,
        "validate_heat_box_manifest",
        fake_validator("exact_heat_verification"),
    )
    monkeypatch.setattr(
        cfd_numerical_spotcheck,
        "validate_numerical_spotcheck_manifest",
        fake_validator("limited_numerical_spotchecks"),
    )

    output = root / "working_validation-result.json"
    results, evidence = working_validation._external_results(root, output)

    assert [result.check_id for result in results] == list(CHECK_IDS)
    assert [result.status for result in results] == ["PASS"] * 8
    assert calls == [
        (
            check_id,
            root / working_validation.CODE_OWNED_ARTIFACTS[check_id],
            root,
            None if check_id == "serial_environment" else output,
        )
        for check_id in CHECK_IDS[2:]
    ]
    assert set(evidence) == {
        "evidence/code_baseline.json",
        "evidence/filesystem_io.json",
        *(f"evidence/{check_id}.json" for check_id in CHECK_IDS[2:]),
    }


def test_code_owned_result_contract_and_conflicting_evidence_fail_closed(
    tmp_path, monkeypatch,
):
    import working_validation

    result, hashes = working_validation._normalize_code_owned_result(
        "serial_environment",
        {"status": "PASS", "blockers": [], "evidence_sha256": {}},
    )
    assert result.status == "BLOCKED"
    assert hashes == {}
    assert result.blockers == ("SERIAL_ENVIRONMENT_VALIDATOR_RESULT_INVALID",)

    for malformed_status in ({}, []):
        result, hashes = working_validation._normalize_code_owned_result(
            "serial_environment",
            {
                "status": malformed_status,
                "blockers": ["MALFORMED_FOR_TEST"],
                "evidence_sha256": {"evidence/source.json": "a" * 64},
            },
        )
        assert result.status == "BLOCKED"
        assert hashes == {}
        assert result.blockers == (
            "SERIAL_ENVIRONMENT_VALIDATOR_RESULT_INVALID",
        )

    shared = _write_json(tmp_path, "shared", {"value": 1})
    shared_digest = _sha256(shared)

    monkeypatch.setattr(
        working_validation,
        "_evaluate_code_baseline",
        lambda _root, _output: (
            working_validation.CheckResult("code_baseline", "PASS"),
            {"shared": shared_digest},
        ),
    )
    monkeypatch.setattr(
        working_validation,
        "_evaluate_filesystem_io",
        lambda _root, _output: (
            working_validation.CheckResult("filesystem_io", "PASS"),
            {"shared": "b" * 64},
        ),
    )
    monkeypatch.setattr(
        working_validation,
        "_evaluate_code_owned",
        lambda check_id, _root, _output: (
            working_validation.CheckResult(
                check_id, "BLOCKED", ("MISSING_FOR_TEST",)
            ),
            {},
        ),
    )

    results, evidence = working_validation._external_results(tmp_path.resolve(), None)

    assert results[1].status == "BLOCKED"
    assert results[1].blockers == ("EVIDENCE_HASH_CONFLICT",)
    assert results[0].status == "BLOCKED"
    assert results[0].blockers == ("EVIDENCE_HASH_CONFLICT",)
    assert evidence == {}


@pytest.mark.parametrize(
    "bad_path",
    [
        "../escape.json",
        "C:/outside.json",
        "evidence\\source.json",
        "evidence/./source.json",
        "evidence//source.json",
        ".pytest_cache/source.json",
        "tmp/source.json",
        "evidence/latest/source.json",
        "evidence/missing.json",
        "working_validation-result.json",
    ],
)
def test_noncanonical_or_unsafe_evidence_paths_fail_closed(
    tmp_path, monkeypatch, bad_path,
):
    import working_validation

    source = _write_json(tmp_path, "evidence/source.json", {"value": 1})
    digest = _sha256(source)
    monkeypatch.setattr(
        working_validation,
        "_evaluate_code_baseline",
        lambda _root, _output: (
            working_validation.CheckResult("code_baseline", "PASS"),
            {bad_path: digest},
        ),
    )
    monkeypatch.setattr(
        working_validation,
        "_evaluate_filesystem_io",
        lambda _root, _output: (
            working_validation.CheckResult(
                "filesystem_io", "BLOCKED", ("MISSING_FOR_TEST",)
            ),
            {},
        ),
    )
    monkeypatch.setattr(
        working_validation,
        "_evaluate_code_owned",
        lambda check_id, _root, _output: (
            working_validation.CheckResult(
                check_id, "BLOCKED", ("MISSING_FOR_TEST",)
            ),
            {},
        ),
    )

    results, evidence = working_validation._external_results(
        tmp_path.resolve(), None
    )

    assert results[0].status == "BLOCKED"
    assert "EVIDENCE_PATH_INVALID" in results[0].blockers
    assert evidence == {}


def test_case_only_evidence_alias_is_not_canonical(tmp_path):
    import working_validation

    source = _write_json(tmp_path, "Evidence/Source.json", {"value": 1})

    captured, error = working_validation._capture_evidence(
        tmp_path.resolve(),
        "code_baseline",
        "evidence/source.json",
        _sha256(source),
    )

    assert captured is None
    assert error == "EVIDENCE_PATH_INVALID"


def test_evidence_is_rehashed_after_all_validators_and_drift_blocks_owner(
    tmp_path, monkeypatch,
):
    import working_validation

    source = _write_json(tmp_path, "evidence/source.json", {"value": 1})
    original_digest = _sha256(source)

    monkeypatch.setattr(
        working_validation,
        "_evaluate_code_baseline",
        lambda _root, _output: (
            working_validation.CheckResult("code_baseline", "PASS"),
            {"evidence/source.json": original_digest},
        ),
    )

    def mutate_after_first_validator(_root, _output):
        source.write_text('{"value": 2}', encoding="utf-8")
        return (
            working_validation.CheckResult(
                "filesystem_io", "BLOCKED", ("MISSING_FOR_TEST",)
            ),
            {},
        )

    monkeypatch.setattr(
        working_validation, "_evaluate_filesystem_io", mutate_after_first_validator
    )
    monkeypatch.setattr(
        working_validation,
        "_evaluate_code_owned",
        lambda check_id, _root, _output: (
            working_validation.CheckResult(
                check_id, "BLOCKED", ("MISSING_FOR_TEST",)
            ),
            {},
        ),
    )

    results, evidence = working_validation._external_results(
        tmp_path.resolve(), None
    )

    assert results[0].status == "BLOCKED"
    assert "EVIDENCE_CHANGED_DURING_AGGREGATION" in results[0].blockers
    assert "evidence/source.json" not in evidence


def test_case_only_rename_during_aggregation_blocks_original_owner(
    tmp_path, monkeypatch,
):
    import working_validation

    source = _write_json(tmp_path, "Evidence/Source.json", {"value": 1})
    digest = _sha256(source)
    monkeypatch.setattr(
        working_validation,
        "_evaluate_code_baseline",
        lambda _root, _output: (
            working_validation.CheckResult("code_baseline", "PASS"),
            {"Evidence/Source.json": digest},
        ),
    )

    def case_rename_after_first_validator(_root, _output):
        temporary_source = source.with_name("source-case-transition.json")
        source.rename(temporary_source)
        lowered_source = source.with_name("source.json")
        temporary_source.rename(lowered_source)
        original_directory = source.parent
        temporary_directory = original_directory.with_name("case-transition")
        original_directory.rename(temporary_directory)
        temporary_directory.rename(original_directory.with_name("evidence"))
        return (
            working_validation.CheckResult(
                "filesystem_io", "BLOCKED", ("MISSING_FOR_TEST",)
            ),
            {},
        )

    monkeypatch.setattr(
        working_validation,
        "_evaluate_filesystem_io",
        case_rename_after_first_validator,
    )
    monkeypatch.setattr(
        working_validation,
        "_evaluate_code_owned",
        lambda check_id, _root, _output: (
            working_validation.CheckResult(
                check_id, "BLOCKED", ("MISSING_FOR_TEST",)
            ),
            {},
        ),
    )

    results, evidence = working_validation._external_results(
        tmp_path.resolve(), None
    )

    assert results[0].status == "BLOCKED"
    assert "EVIDENCE_CHANGED_DURING_AGGREGATION" in results[0].blockers
    assert evidence == {}


def test_hardlink_evidence_aliases_fail_closed(tmp_path, monkeypatch):
    import working_validation

    source = _write_json(tmp_path, "evidence/source.json", {"value": 1})
    alias = tmp_path / "evidence" / "alias.json"
    try:
        alias.hardlink_to(source)
    except (OSError, NotImplementedError):
        pytest.skip("hardlinks unavailable")
    digest = _sha256(source)
    monkeypatch.setattr(
        working_validation,
        "_evaluate_code_baseline",
        lambda _root, _output: (
            working_validation.CheckResult("code_baseline", "PASS"),
            {"evidence/source.json": digest},
        ),
    )
    monkeypatch.setattr(
        working_validation,
        "_evaluate_filesystem_io",
        lambda _root, _output: (
            working_validation.CheckResult("filesystem_io", "PASS"),
            {"evidence/alias.json": digest},
        ),
    )
    monkeypatch.setattr(
        working_validation,
        "_evaluate_code_owned",
        lambda check_id, _root, _output: (
            working_validation.CheckResult(
                check_id, "BLOCKED", ("MISSING_FOR_TEST",)
            ),
            {},
        ),
    )

    results, evidence = working_validation._external_results(
        tmp_path.resolve(), None
    )

    assert results[0].status == "BLOCKED"
    assert results[1].status == "BLOCKED"
    assert "EVIDENCE_ALIAS_CONFLICT" in results[0].blockers
    assert "EVIDENCE_ALIAS_CONFLICT" in results[1].blockers
    assert evidence == {}


def test_undeclared_hardlink_alias_is_rejected(tmp_path):
    import working_validation

    source = _write_json(tmp_path, "evidence/source.json", {"value": 1})
    alias = tmp_path / "hidden-alias.json"
    try:
        alias.hardlink_to(source)
    except (OSError, NotImplementedError):
        pytest.skip("hardlinks unavailable")

    captured, error = working_validation._capture_evidence(
        tmp_path.resolve(),
        "code_baseline",
        "evidence/source.json",
        _sha256(source),
    )

    assert captured is None
    assert error == "EVIDENCE_ALIAS_CONFLICT"


def test_shared_canonical_evidence_with_same_digest_is_allowed(
    tmp_path, monkeypatch,
):
    import working_validation

    source = _write_json(tmp_path, "evidence/shared.json", {"value": 1})
    digest = _sha256(source)
    monkeypatch.setattr(
        working_validation,
        "_evaluate_code_baseline",
        lambda _root, _output: (
            working_validation.CheckResult("code_baseline", "PASS"),
            {"evidence/shared.json": digest},
        ),
    )
    monkeypatch.setattr(
        working_validation,
        "_evaluate_filesystem_io",
        lambda _root, _output: (
            working_validation.CheckResult("filesystem_io", "PASS"),
            {"evidence/shared.json": digest},
        ),
    )
    monkeypatch.setattr(
        working_validation,
        "_evaluate_code_owned",
        lambda check_id, _root, _output: (
            working_validation.CheckResult(
                check_id, "BLOCKED", ("MISSING_FOR_TEST",)
            ),
            {},
        ),
    )

    results, evidence = working_validation._external_results(
        tmp_path.resolve(), None
    )

    assert results[0].status == "PASS"
    assert results[1].status == "PASS"
    assert evidence == {"evidence/shared.json": digest}


def test_numerical_directory_evidence_is_rehashed_at_finalization(
    tmp_path, monkeypatch,
):
    import cfd_numerical_spotcheck
    import working_validation

    case = tmp_path / "evidence" / "numerical-case"
    _write_json(case, "source.json", {"value": 1})
    captured = cfd_numerical_spotcheck._snapshot_directory_tree(case)
    assert captured is not None
    digest = captured[0]
    monkeypatch.setattr(
        working_validation,
        "_evaluate_code_baseline",
        lambda _root, _output: (
            working_validation.CheckResult(
                "code_baseline", "BLOCKED", ("MISSING_FOR_TEST",)
            ),
            {},
        ),
    )
    monkeypatch.setattr(
        working_validation,
        "_evaluate_filesystem_io",
        lambda _root, _output: (
            working_validation.CheckResult(
                "filesystem_io", "BLOCKED", ("MISSING_FOR_TEST",)
            ),
            {},
        ),
    )

    def fake_code_owned(check_id, _root, _output):
        if check_id == "limited_numerical_spotchecks":
            return (
                working_validation.CheckResult(check_id, "PASS"),
                {"evidence/numerical-case": digest},
            )
        return (
            working_validation.CheckResult(
                check_id, "BLOCKED", ("MISSING_FOR_TEST",)
            ),
            {},
        )

    monkeypatch.setattr(
        working_validation, "_evaluate_code_owned", fake_code_owned
    )
    original_digest = working_validation._evidence_digest
    calls = 0

    def mutate_before_final_digest(check_id, path):
        nonlocal calls
        if check_id == "limited_numerical_spotchecks" and Path(path) == case:
            calls += 1
            if calls == 2:
                (case / "late-empty-directory").mkdir()
        return original_digest(check_id, path)

    monkeypatch.setattr(
        working_validation, "_evidence_digest", mutate_before_final_digest
    )

    results, evidence = working_validation._external_results(
        tmp_path.resolve(), None
    )

    numerical = results[-1]
    assert numerical.status == "BLOCKED"
    assert "EVIDENCE_CHANGED_DURING_AGGREGATION" in numerical.blockers
    assert "evidence/numerical-case" not in evidence


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


def test_public_evaluator_uses_code_owned_validators_and_missing_evidence_blocks(tmp_path):
    from working_validation import evaluate_working_validation

    _write_real_task1_raw_artifacts(tmp_path)

    result = evaluate_working_validation(tmp_path)

    assert result["status"] == "BLOCKED"
    assert [row["status"] for row in result["checks"][:2]] == ["PASS", "PASS"]
    for row in result["checks"][2:]:
        assert row["status"] == "BLOCKED"
        assert row["blockers"]
        assert not any(
            "VALIDATOR_NOT_IMPLEMENTED" in blocker for blocker in row["blockers"]
        )


def test_complete_matching_dummy_bundle_remains_blocked(tmp_path):
    from working_validation import evaluate_working_validation

    _write_complete_matching_dummy_bundle(tmp_path)

    result = evaluate_working_validation(tmp_path)

    assert result["status"] == "BLOCKED"
    assert result["working_ready_on_target"] is False
    for row in result["checks"][2:]:
        assert row["status"] == "BLOCKED"
        assert row["blockers"]
        assert not any(
            "VALIDATOR_NOT_IMPLEMENTED" in blocker for blocker in row["blockers"]
        )


def test_write_rejects_output_alias_of_a_code_owned_manifest(tmp_path):
    from working_validation import write_working_validation

    manifest = _write_json(
        tmp_path,
        "_working_validation/local_usability_acceptance.json",
        {"contract": "local_usability_acceptance.v1", "status": "PASS"},
    )
    original = manifest.read_bytes()

    with pytest.raises(ValueError, match="OUTPUT_ALIAS"):
        write_working_validation(tmp_path, manifest)

    assert manifest.read_bytes() == original


def test_output_authority_rejects_descendant_of_directory_evidence(tmp_path):
    from working_validation import _output_authority_blocker

    case = tmp_path / "_working_validation" / "numerical-spotcheck-v1" / "anchor"
    case.mkdir(parents=True)
    output = case / "aggregate.json"

    assert _output_authority_blocker(
        tmp_path.resolve(),
        output,
        {"_working_validation/numerical-spotcheck-v1/anchor": "a" * 64},
    ) == "OUTPUT_ALIAS"


def test_serial_authority_roots_are_protected_without_using_writer_argument(
    tmp_path,
):
    from working_validation import _output_authority_blocker

    raw_root = tmp_path / "_system" / "environment_acceptance"
    raw_root.mkdir(parents=True)

    assert _output_authority_blocker(
        tmp_path.resolve(), raw_root / "new-output.json"
    ) == "OUTPUT_ALIAS"
    assert _output_authority_blocker(
        tmp_path.resolve(), tmp_path / "working-result.json"
    ) is None


@pytest.mark.parametrize(
    "relative_output",
    [
        "_working_validation/evidence/new-result.json",
        "_working_validation/evaluations/new-result.json",
        "_working_validation/working-room-v1/anchor/new-result.json",
        "_working_validation/sgi-screening-v1/new-result.json",
        "_working_validation/heat-box-v1/case/new-result.json",
        "_working_validation/numerical-spotcheck-v1/anchor/new-result.json",
        "_imports/new-result.json",
        "_field_jobs/field-test/new-result.json",
        "_body_mesh/case/new-result.json",
        "_body_solver/case/new-result.json",
        "_body_gci/case/new-result.json",
    ],
)
def test_writer_rejects_missing_manifest_producer_subtrees(
    tmp_path, relative_output,
):
    from working_validation import write_working_validation

    output = tmp_path.joinpath(*relative_output.split("/"))

    with pytest.raises(ValueError, match="OUTPUT_ALIAS"):
        write_working_validation(tmp_path, output)

    assert not output.exists()


@pytest.mark.parametrize(
    "relative_output",
    [
        "aggregate.json:secret",
        "tmp/result.json",
        ".pytest_cache/result.json",
        "result.tmp",
        "CON.json",
        "bad?.json",
        "bad*.json",
        "bad|.json",
        "bad<.json",
        "bad>.json",
        'bad".json',
    ],
)
def test_writer_rejects_unsafe_windows_cache_and_temp_outputs(
    tmp_path, relative_output,
):
    from working_validation import write_working_validation

    output = tmp_path.joinpath(*relative_output.split("/"))

    with pytest.raises(ValueError, match="WORKING_VALIDATION_OUTPUT_"):
        write_working_validation(tmp_path, output)

    assert not any(path.is_file() for path in tmp_path.rglob("*"))


def test_writer_rejects_existing_directory_output_without_temp_residue(tmp_path):
    from working_validation import write_working_validation

    output = tmp_path / "existing-directory"
    output.mkdir()

    with pytest.raises(ValueError, match="OUTPUT_PATH_INVALID"):
        write_working_validation(tmp_path, output)

    assert output.is_dir()
    assert not list(tmp_path.glob(".existing-directory.*.tmp"))


def test_writer_cleans_temporary_file_if_atomic_replace_fails(
    tmp_path, monkeypatch,
):
    import working_validation

    output = tmp_path / "working-validation-result.json"

    def fail_replace(_source, _destination):
        raise OSError("replace failed for test")

    monkeypatch.setattr(working_validation.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed for test"):
        working_validation.write_working_validation(tmp_path, output)

    assert not output.exists()
    assert not list(tmp_path.glob(".working-validation-result.json.*.tmp"))


def test_writer_rejects_raw_dotdot_output_before_abspath_normalization(tmp_path):
    from working_validation import write_working_validation

    raw_output = tmp_path / "subdirectory" / ".." / "aggregate.json"

    with pytest.raises(ValueError, match="OUTPUT_PATH_INVALID"):
        write_working_validation(tmp_path, raw_output)

    assert not (tmp_path / "aggregate.json").exists()


@pytest.mark.parametrize(
    "unsafe_blocker",
    [
        "EVALUATOR_OUTPUT_LOCATION_FORBIDDEN",
        "EVALUATOR_OUTPUT_INVALID",
        "OUTPUT_PATH_INVALID",
        "SOURCE_SELF_OUTPUT_FORBIDDEN",
    ],
)
def test_writer_refuses_every_validator_reported_unsafe_output(
    tmp_path, monkeypatch, unsafe_blocker,
):
    import working_validation

    rows = [
        working_validation.CheckResult(check_id, "PASS")
        for check_id in CHECK_IDS
    ]
    rows[2] = working_validation.CheckResult(
        "serial_environment", "BLOCKED", (unsafe_blocker,)
    )
    monkeypatch.setattr(
        working_validation,
        "_external_results",
        lambda _root, _output: (rows, {}),
    )
    output = tmp_path / "working-validation-result.json"

    with pytest.raises(ValueError, match="UNSAFE_OUTPUT"):
        working_validation.write_working_validation(tmp_path, output)

    assert not output.exists()


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

    payload = evaluate_working_validation(tmp_path)
    payload["evidence_sha256"] = {"C:/outside.json": "a" * 64}
    assert "EVIDENCE_PATH_INVALID" in validate_working_validation_payload(payload)

    payload = evaluate_working_validation(tmp_path)
    payload["evidence_sha256"] = {}
    assert "EVIDENCE_MISSING" in validate_working_validation_payload(payload)

    payload = evaluate_working_validation(tmp_path)
    payload["checks"][0]["blockers"] = ["FORGED_BLOCKER"]
    assert "CHECK_BLOCKER_INVARIANT_INVALID" in (
        validate_working_validation_payload(payload)
    )

    payload = evaluate_working_validation(tmp_path)
    payload["checks"][2]["blockers"] = []
    assert "CHECK_BLOCKER_INVARIANT_INVALID" in (
        validate_working_validation_payload(payload)
    )


def test_compare_runs_validates_inputs_and_includes_all_declared_semantics(tmp_path):
    from working_validation import compare_working_validation_runs, write_working_validation

    _write_real_task1_raw_artifacts(tmp_path)
    first = tmp_path / "working_validation-run-one.json"
    second = tmp_path / "working_validation-run-two.json"
    write_working_validation(tmp_path, first)
    write_working_validation(tmp_path, second)
    assert compare_working_validation_runs(first, second)["equal"] is True

    with pytest.raises(ValueError, match="RUN_IDENTITY_NOT_INDEPENDENT"):
        compare_working_validation_runs(first, first)

    hardlink = tmp_path / "working_validation-hardlink.json"
    try:
        hardlink.hardlink_to(first)
    except (OSError, NotImplementedError):
        hardlink = None
    if hardlink is not None:
        with pytest.raises(ValueError, match="RUN_IDENTITY_NOT_INDEPENDENT"):
            compare_working_validation_runs(first, hardlink)

    copied = tmp_path / "working_validation-copied.json"
    copied.write_bytes(first.read_bytes())
    with pytest.raises(ValueError, match="RUN_OUTPUT_PATH_MISMATCH"):
        compare_working_validation_runs(first, copied)

    duplicate = tmp_path / "working_validation-duplicate-key.json"
    duplicate.write_text(
        first.read_text(encoding="utf-8").replace(
            '"contract": "working_validation.v1"',
            '"contract": "working_validation.v1",\n'
            '  "contract": "working_validation.v1"',
            1,
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="RUN_INVALID"):
        compare_working_validation_runs(first, duplicate)

    second_payload = json.loads(second.read_text(encoding="utf-8"))
    second_payload["evidence_sha256"]["semantic-change"] = "0" * 64
    second.write_text(json.dumps(second_payload), encoding="utf-8")
    assert compare_working_validation_runs(first, second)["equal"] is False

    first.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="RUN_INVALID"):
        compare_working_validation_runs(first, second)
