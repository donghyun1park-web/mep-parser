import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
from unittest import mock

from jsonschema import Draft202012Validator
import pytest

import cfd_case_health
import cfd_evidence
import cfd_review
from cfd_status_catalog import (
    CASE_HEALTH_CHECKS,
    CITATION_DECISION_TABLE,
    CITATION_DECISION_TABLE_VERSION,
    EVIDENCE_CHECKS,
    STATUS_CATALOG,
)
from test_cfd_evidence import make_complete_case


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _future_evidence(tmp_path, *, purpose="design_review_candidate"):
    paths = make_complete_case(tmp_path, with_gci=True)
    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"]
    )
    evidence.pop("legacy_case_ref")
    evidence["case_identity"] = {
        "contract": "case_identity.v1",
        "path": evidence["artifact_refs"]["geometry"]["path"],
        "sha256": evidence["artifact_refs"]["geometry"]["sha256"],
    }
    evidence["purpose"] = purpose
    for check in evidence["checks"]:
        check.update(status="PASS", reason_codes=[], evidence_refs=[])
    evidence["status"] = "PASS"
    evidence["errors"] = []
    _write(paths["evidence"], evidence)
    return paths, evidence


def _set_check(evidence: dict, check_id: str, status: str, reason="TEST_REASON"):
    row = next(item for item in evidence["checks"] if item["id"] == check_id)
    row["status"] = status
    row["reason_codes"] = [] if status == "PASS" else [reason]
    row["evidence_refs"] = [] if status == "PASS" else ["mesh"]


def _approve(paths, decision="APPROVED"):
    digest = cfd_review.sha256_file(paths["evidence"])
    return cfd_review.create_review(
        paths["evidence"],
        projects_root=paths["root"],
        expected_target_sha256=digest,
        reviewer_id="reviewer-1",
        decision=decision,
        reason="reviewed against current evidence",
    )


def _codes(health: dict) -> list[str]:
    return [item["code"] for item in health["errors"]]


def _build_future(paths):
    with mock.patch.object(
        cfd_case_health.cfd_evidence, "validate_case_evidence", return_value=[]
    ):
        return cfd_case_health.build_case_health(
            paths["evidence"], projects_root=paths["root"]
        )


def test_health_has_exact_nine_key_projection_and_catalog_text(tmp_path):
    paths, evidence = _future_evidence(tmp_path, purpose="screening")
    _set_check(evidence, "grid_verified", "NOT_EVALUATED", "GCI_NOT_FOUND")
    _set_check(evidence, "field_calibrated", "BLOCKED", "FIELD_EVIDENCE_INVALID")
    _write(paths["evidence"], evidence)

    health = _build_future(paths)

    assert tuple(health["checks"]) == CASE_HEALTH_CHECKS
    assert health["checks"]["grid_verified"]["reason_codes"] == ["GCI_NOT_FOUND"]
    assert health["checks"]["field_calibrated"]["evidence_refs"] == ["mesh"]
    catalog_impacts = {row["impact"] for row in STATUS_CATALOG.values()}
    catalog_actions = {row["next_action"] for row in STATUS_CATALOG.values()}
    assert all(row["impact"] in catalog_impacts for row in health["checks"].values())
    assert all(set(row["next_actions"]) <= catalog_actions for row in health["checks"].values())
    assert health["citation_decision_table_version"] == CITATION_DECISION_TABLE_VERSION
    assert health["citation_decision_table"] == list(CITATION_DECISION_TABLE)
    schema = _read(Path(cfd_case_health.__file__).with_name("case_health.v1.schema.json"))
    Draft202012Validator(schema).validate(health)
    assert (paths["case"] / "case_health.v1.json").is_file()


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["PASS"] * 8, "PASS"),
        (["PASS"] * 7 + ["NOT_EVALUATED"], "NOT_EVALUATED"),
        (["PASS"] * 6 + ["BLOCKED", "NOT_EVALUATED"], "BLOCKED"),
        (["PASS"] * 5 + ["FAIL", "BLOCKED", "NOT_EVALUATED"], "FAIL"),
    ],
)
def test_design_ready_aggregates_all_eight_source_checks(tmp_path, statuses, expected):
    paths, evidence = _future_evidence(tmp_path)
    for check_id, status in zip(EVIDENCE_CHECKS, statuses):
        _set_check(evidence, check_id, status, f"{check_id.upper()}_{status}")
    _write(paths["evidence"], evidence)

    health = _build_future(paths)

    design = health["checks"]["design_ready"]
    assert design["status"] == expected
    assert design["reason_codes"] == list(dict.fromkeys(
        f"{check_id.upper()}_{status}"
        for check_id, status in zip(EVIDENCE_CHECKS, statuses)
        if status != "PASS"
    ))


@pytest.mark.parametrize(
    ("case_name", "purpose", "check_id", "check_status", "review", "validation_errors", "expected_status", "expected_reason"),
    [
        ("invalid", "design_review_candidate", None, None, "APPROVED", [{"code": "ARTIFACT_HASH_MISMATCH"}], "CITATION_BLOCKED", "CITATION_EVIDENCE_OR_REVIEW_INVALID"),
        ("blocked", "design_review_candidate", "mesh_checked", "BLOCKED", "APPROVED", [], "CITATION_BLOCKED", "REQUIRED_CHECK_FAILED_OR_BLOCKED"),
        ("rejected", "design_review_candidate", None, None, "REJECTED", [], "CITATION_BLOCKED", "REVIEW_REJECTED"),
        ("not-evaluated", "design_review_candidate", "field_calibrated", "NOT_EVALUATED", None, [], "NOT_EVALUATED", "REQUIRED_CHECK_NOT_EVALUATED"),
        ("benchmark", "benchmark", None, None, None, [], "NOT_EVALUATED", "BENCHMARK_NOT_DESIGN_CITABLE"),
        ("screening", "screening", None, None, None, [], "SCREENING_ONLY", "SCREENING_ONLY"),
        ("approved", "design_review_candidate", None, None, "APPROVED", [], "DESIGN_CITABLE", "DESIGN_CITABLE"),
        ("missing-review", "field_validation", None, None, None, [], "NOT_EVALUATED", "REVIEW_REQUIRED"),
    ],
)
def test_exact_eight_row_citation_decision_table(
    tmp_path, case_name, purpose, check_id, check_status, review,
    validation_errors, expected_status, expected_reason,
):
    paths, evidence = _future_evidence(tmp_path / case_name, purpose=purpose)
    if check_id:
        _set_check(evidence, check_id, check_status)
        _write(paths["evidence"], evidence)
    if review:
        _approve(paths, review)
    with mock.patch.object(
        cfd_case_health.cfd_evidence,
        "validate_case_evidence",
        return_value=validation_errors,
    ):
        health = cfd_case_health.build_case_health(
            paths["evidence"], projects_root=paths["root"]
        )

    assert health["citation_status"] == expected_status
    assert _codes(health)[0] == expected_reason


def test_precedence_collisions_use_first_matching_row(tmp_path):
    paths, evidence = _future_evidence(tmp_path)
    _set_check(evidence, "mesh_checked", "FAIL", "MESH_FAILED")
    _set_check(evidence, "field_calibrated", "NOT_EVALUATED", "FIELD_MISSING")
    _write(paths["evidence"], evidence)
    _approve(paths, "REJECTED")

    with mock.patch.object(
        cfd_case_health.cfd_evidence,
        "validate_case_evidence",
        return_value=[{"code": "ARTIFACT_HASH_MISMATCH"}],
    ):
        invalid = cfd_case_health.build_case_health(
            paths["evidence"], projects_root=paths["root"]
        )
    assert _codes(invalid)[0] == "CITATION_EVIDENCE_OR_REVIEW_INVALID"

    with mock.patch.object(
        cfd_case_health.cfd_evidence, "validate_case_evidence", return_value=[]
    ):
        source_failed = cfd_case_health.build_case_health(
            paths["evidence"], projects_root=paths["root"]
        )
    assert _codes(source_failed)[0] == "REQUIRED_CHECK_FAILED_OR_BLOCKED"

    _set_check(evidence, "mesh_checked", "PASS")
    _write(paths["evidence"], evidence)
    _approve(paths, "REJECTED")
    rejected = _build_future(paths)
    assert _codes(rejected)[0] == "REVIEW_REJECTED"


def test_optional_checks_do_not_change_screening_ceiling(tmp_path):
    paths, evidence = _future_evidence(tmp_path, purpose="screening")
    for check_id in ("grid_verified", "benchmark_validated", "field_calibrated"):
        _set_check(evidence, check_id, "FAIL", f"{check_id.upper()}_FAIL")
    _write(paths["evidence"], evidence)

    health = _build_future(paths)

    assert health["checks"]["design_ready"]["status"] == "FAIL"
    assert health["citation_status"] == "SCREENING_ONLY"
    assert _codes(health)[0] == "SCREENING_ONLY"


def test_legacy_identity_never_becomes_design_citable(tmp_path):
    paths = make_complete_case(tmp_path, with_gci=True)
    cfd_evidence.build_case_evidence(paths["case"], projects_root=paths["root"])
    _approve(paths, "APPROVED")

    health = cfd_case_health.build_case_health(
        paths["evidence"], projects_root=paths["root"]
    )

    assert health["purpose"] == "screening"
    assert health["citation_status"] == "SCREENING_ONLY"


def test_human_approval_cannot_override_failed_mesh(tmp_path):
    paths, evidence = _future_evidence(tmp_path)
    _set_check(evidence, "mesh_checked", "FAIL", "MESH_FAILED")
    _write(paths["evidence"], evidence)
    review = _approve(paths, "APPROVED")

    health = _build_future(paths)

    assert review["decision"] == "APPROVED"
    assert health["checks"]["mesh_checked"]["status"] == "FAIL"
    assert health["checks"]["design_ready"]["status"] == "FAIL"
    assert health["citation_status"] == "CITATION_BLOCKED"


def test_real_evidence_and_raw_artifact_mutations_block_health(tmp_path):
    evidence_case = make_complete_case(tmp_path / "evidence")
    cfd_evidence.build_case_evidence(
        evidence_case["case"], projects_root=evidence_case["root"]
    )
    edited = _read(evidence_case["evidence"])
    edited["checks"][0]["reason_codes"] = ["FORGED"]
    _write(evidence_case["evidence"], edited)

    evidence_health = cfd_case_health.build_case_health(
        evidence_case["evidence"], projects_root=evidence_case["root"]
    )

    raw_case = make_complete_case(tmp_path / "raw")
    cfd_evidence.build_case_evidence(raw_case["case"], projects_root=raw_case["root"])
    raw_case["source_vtu"].write_bytes(raw_case["source_vtu"].read_bytes() + b"tamper")
    raw_health = cfd_case_health.build_case_health(
        raw_case["evidence"], projects_root=raw_case["root"]
    )

    assert evidence_health["citation_status"] == "CITATION_BLOCKED"
    assert raw_health["citation_status"] == "CITATION_BLOCKED"
    assert _codes(evidence_health)[0] == "CITATION_EVIDENCE_OR_REVIEW_INVALID"
    assert _codes(raw_health)[0] == "CITATION_EVIDENCE_OR_REVIEW_INVALID"


def test_health_rejects_catalog_version_or_table_mismatch(tmp_path):
    paths, _ = _future_evidence(tmp_path)

    with mock.patch.object(
        cfd_case_health, "CITATION_DECISION_TABLE_VERSION", "drifted.v9"
    ), pytest.raises(RuntimeError, match="CITATION_DECISION_TABLE_MISMATCH"):
        _build_future(paths)

    drifted = copy.deepcopy(CITATION_DECISION_TABLE)
    drifted = tuple(({**drifted[0], "id": "drift"}, *drifted[1:]))
    with mock.patch.object(
        cfd_case_health, "CITATION_DECISION_TABLE", drifted
    ), pytest.raises(RuntimeError, match="CITATION_DECISION_TABLE_MISMATCH"):
        _build_future(paths)


@pytest.mark.parametrize("mutation", ["evidence", "raw"])
def test_final_health_revalidation_catches_mutation_after_projection(tmp_path, mutation):
    paths = make_complete_case(tmp_path)
    cfd_evidence.build_case_evidence(paths["case"], projects_root=paths["root"])
    original_decision = cfd_case_health._decision
    injected = False

    def mutate_after_projection(*args, **kwargs):
        nonlocal injected
        result = original_decision(*args, **kwargs)
        if not injected:
            injected = True
            if mutation == "evidence":
                payload = _read(paths["evidence"])
                row = next(item for item in payload["checks"] if item["id"] == "mesh_checked")
                row.update(status="FAIL", reason_codes=["MESH_FAILED_AFTER_PROJECTION"])
                _write(paths["evidence"], payload)
            else:
                paths["source_vtu"].write_bytes(
                    paths["source_vtu"].read_bytes() + b"race-mutation"
                )
        return result

    with mock.patch.object(
        cfd_case_health, "_decision", side_effect=mutate_after_projection
    ):
        health = cfd_case_health.build_case_health(
            paths["evidence"], projects_root=paths["root"]
        )

    assert health["citation_status"] == "CITATION_BLOCKED"
    assert health["errors"][0]["code"] == "CITATION_EVIDENCE_OR_REVIEW_INVALID"
    assert health["evidence"]["sha256"] == cfd_review.sha256_file(paths["evidence"])
    if mutation == "evidence":
        assert health["checks"]["mesh_checked"]["status"] == "FAIL"
    assert _read(paths["case"] / "case_health.v1.json") == health


def test_final_health_projection_rechecks_new_review_fork(tmp_path):
    paths, _ = _future_evidence(tmp_path)
    _approve(paths, "APPROVED")
    original_decision = cfd_case_health._decision
    injected = False

    def fork_after_approved_projection(*args, **kwargs):
        nonlocal injected
        result = original_decision(*args, **kwargs)
        if not injected:
            injected = True
            _approve(paths, "REJECTED")
        return result

    with mock.patch.object(
        cfd_case_health.cfd_evidence, "validate_case_evidence", return_value=[]
    ), mock.patch.object(
        cfd_case_health, "_decision", side_effect=fork_after_approved_projection
    ):
        health = cfd_case_health.build_case_health(
            paths["evidence"], projects_root=paths["root"]
        )

    assert health["citation_status"] == "CITATION_BLOCKED"
    assert "REVIEW_HISTORY_AMBIGUOUS" in _codes(health)


def test_health_publish_serializes_cooperating_review_writer(tmp_path):
    paths, _ = _future_evidence(tmp_path)
    _approve(paths, "APPROVED")
    real_replace = cfd_case_health.os.replace
    real_history = cfd_review._history
    health_paused = threading.Event()
    release_health = threading.Event()
    writer_entered_history = threading.Event()
    paused_once = False

    def pause_before_health_replace(source, destination):
        nonlocal paused_once
        if Path(destination).name == "case_health.v1.json" and not paused_once:
            paused_once = True
            health_paused.set()
            assert release_health.wait(5), "health publish release timed out"
        return real_replace(source, destination)

    def observe_history(*args, **kwargs):
        if threading.current_thread().name.startswith("review-writer"):
            writer_entered_history.set()
        return real_history(*args, **kwargs)

    with mock.patch.object(
        cfd_case_health.cfd_evidence, "validate_case_evidence", return_value=[]
    ), mock.patch.object(
        cfd_case_health.os, "replace", side_effect=pause_before_health_replace
    ), mock.patch.object(
        cfd_review, "_history", side_effect=observe_history
    ), ThreadPoolExecutor(max_workers=1, thread_name_prefix="health-publisher") as health_pool, ThreadPoolExecutor(
        max_workers=1, thread_name_prefix="review-writer"
    ) as writer_pool:
        health_future = health_pool.submit(
            cfd_case_health.build_case_health,
            paths["evidence"],
            projects_root=paths["root"],
        )
        assert health_paused.wait(5), "health did not reach final publish"
        writer_future = writer_pool.submit(_approve, paths, "REJECTED")
        writer_was_blocked = not writer_entered_history.wait(0.25)
        release_health.set()
        first_health = health_future.result(timeout=5)
        writer_future.result(timeout=5)

    assert writer_was_blocked
    assert first_health["citation_status"] == "DESIGN_CITABLE"
    with mock.patch.object(
        cfd_case_health.cfd_evidence, "validate_case_evidence", return_value=[]
    ):
        next_health = cfd_case_health.build_case_health(
            paths["evidence"], projects_root=paths["root"]
        )
    assert next_health["citation_status"] == "CITATION_BLOCKED"
    assert "REVIEW_HISTORY_AMBIGUOUS" in _codes(next_health)


def test_health_publish_failure_cleans_staging_and_releases_review_lock(tmp_path):
    paths, _ = _future_evidence(tmp_path)
    _approve(paths, "APPROVED")
    with mock.patch.object(
        cfd_case_health.cfd_evidence, "validate_case_evidence", return_value=[]
    ), mock.patch.object(
        cfd_case_health.os, "fsync", side_effect=OSError("disk")
    ), pytest.raises(OSError, match="disk"):
        cfd_case_health.build_case_health(
            paths["evidence"], projects_root=paths["root"]
        )

    assert not list(paths["case"].glob(".case_health.v1.json.*.tmp"))
    created = _approve(paths, "REJECTED")
    assert created["decision"] == "REJECTED"
