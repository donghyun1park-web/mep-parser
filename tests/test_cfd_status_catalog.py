import pytest


def test_status_catalog_has_actionable_korean_copy():
    from cfd_status_catalog import status_descriptor

    row = status_descriptor("MESH_QUALITY_BLOCKED")

    assert row["status"] == "BLOCKED"
    assert row["impact"]
    assert row["next_action"]
    assert any("가" <= character <= "힣" for character in row["impact"] + row["next_action"])


def test_status_catalog_rejects_unknown_code():
    from cfd_status_catalog import status_descriptor

    with pytest.raises(ValueError, match="Unknown status code: UNKNOWN"):
        status_descriptor("UNKNOWN")


def test_design_ready_checks_are_fixed_order():
    from cfd_status_catalog import CASE_HEALTH_CHECKS

    assert CASE_HEALTH_CHECKS == (
        "geometry_valid", "bc_reviewed", "mesh_checked",
        "solver_converged", "numerics_verified", "grid_verified",
        "benchmark_validated", "field_calibrated", "design_ready",
    )


def test_catalog_exposes_closed_evidence_citation_and_purpose_authority():
    from cfd_status_catalog import (
        CASE_PURPOSES,
        CITATION_STATUSES,
        EVIDENCE_STATUSES,
        PURPOSE_PROFILES,
    )

    assert EVIDENCE_STATUSES == ("PASS", "FAIL", "BLOCKED", "NOT_EVALUATED")
    assert CITATION_STATUSES == (
        "SCREENING_ONLY", "NOT_EVALUATED", "CITATION_BLOCKED", "DESIGN_CITABLE",
    )
    assert CASE_PURPOSES == (
        "screening", "design_review_candidate", "benchmark", "field_validation",
    )
    assert tuple(PURPOSE_PROFILES) == CASE_PURPOSES
    assert PURPOSE_PROFILES == {
        "screening": {
            "required_checks": (
                "geometry_valid", "bc_reviewed", "mesh_checked", "solver_converged", "numerics_verified",
            ),
            "review_required": False,
            "citation_ceiling": "SCREENING_ONLY",
        },
        "design_review_candidate": {
            "required_checks": (
                "geometry_valid", "bc_reviewed", "mesh_checked", "solver_converged", "numerics_verified",
                "grid_verified", "benchmark_validated", "field_calibrated",
            ),
            "review_required": True,
            "citation_ceiling": "DESIGN_CITABLE",
        },
        "benchmark": {
            "required_checks": (
                "geometry_valid", "bc_reviewed", "mesh_checked", "solver_converged", "numerics_verified",
                "grid_verified", "benchmark_validated",
            ),
            "review_required": False,
            "citation_ceiling": "NOT_EVALUATED",
        },
        "field_validation": {
            "required_checks": (
                "geometry_valid", "bc_reviewed", "mesh_checked", "solver_converged", "numerics_verified",
                "grid_verified", "benchmark_validated", "field_calibrated",
            ),
            "review_required": True,
            "citation_ceiling": "DESIGN_CITABLE",
        },
    }


def test_catalog_exports_exact_eight_row_citation_decision_table():
    from cfd_status_catalog import CITATION_DECISION_TABLE, CITATION_DECISION_TABLE_VERSION

    assert CITATION_DECISION_TABLE_VERSION == "citation_decision_table.v1"
    assert CITATION_DECISION_TABLE == (
        {
            "id": "invalid_or_stale_evidence_or_required_review",
            "citation_status": "CITATION_BLOCKED",
            "reason_code": "CITATION_EVIDENCE_OR_REVIEW_INVALID",
        },
        {
            "id": "purpose_required_check_fail_or_blocked",
            "citation_status": "CITATION_BLOCKED",
            "reason_code": "REQUIRED_CHECK_FAILED_OR_BLOCKED",
        },
        {
            "id": "current_rejected_review_for_review_required_purpose",
            "citation_status": "CITATION_BLOCKED",
            "reason_code": "REVIEW_REJECTED",
        },
        {
            "id": "purpose_required_check_not_evaluated",
            "citation_status": "NOT_EVALUATED",
            "reason_code": "REQUIRED_CHECK_NOT_EVALUATED",
        },
        {
            "id": "benchmark_purpose",
            "citation_status": "NOT_EVALUATED",
            "reason_code": "BENCHMARK_NOT_DESIGN_CITABLE",
        },
        {
            "id": "screening_purpose_or_legacy_case_ref",
            "citation_status": "SCREENING_ONLY",
            "reason_code": "SCREENING_ONLY",
        },
        {
            "id": "current_unambiguous_approved_review_and_required_checks_pass",
            "citation_status": "DESIGN_CITABLE",
            "reason_code": "DESIGN_CITABLE",
        },
        {
            "id": "review_required_purpose_without_current_approval",
            "citation_status": "NOT_EVALUATED",
            "reason_code": "REVIEW_REQUIRED",
        },
    )


@pytest.mark.parametrize(
    "code",
    (
        "CASE_EVIDENCE_NOT_FOUND", "REVIEW_TARGET_CHANGED", "REVIEW_REQUIRED",
        "REVIEW_REJECTED", "REVIEW_HISTORY_AMBIGUOUS", "CITATION_BLOCKED",
    ),
)
def test_cross_task_codes_have_actionable_korean_copy(code):
    from cfd_status_catalog import status_descriptor

    row = status_descriptor(code)

    assert row["impact"]
    assert row["next_action"]
