"""Closed status and citation vocabulary for case evidence contracts."""

EVIDENCE_STATUSES = ("PASS", "FAIL", "BLOCKED", "NOT_EVALUATED")
CITATION_STATUSES = (
    "SCREENING_ONLY", "NOT_EVALUATED", "CITATION_BLOCKED", "DESIGN_CITABLE",
)

EVIDENCE_CHECKS = (
    "geometry_valid", "bc_reviewed", "mesh_checked", "solver_converged",
    "numerics_verified", "grid_verified", "benchmark_validated", "field_calibrated",
)
CASE_HEALTH_CHECKS = EVIDENCE_CHECKS + ("design_ready",)

CASE_PURPOSES = (
    "screening", "design_review_candidate", "benchmark", "field_validation",
)
PURPOSE_PROFILES = {
    "screening": {
        "required_checks": EVIDENCE_CHECKS[:5],
        "review_required": False,
        "citation_ceiling": "SCREENING_ONLY",
    },
    "design_review_candidate": {
        "required_checks": EVIDENCE_CHECKS,
        "review_required": True,
        "citation_ceiling": "DESIGN_CITABLE",
    },
    "benchmark": {
        "required_checks": EVIDENCE_CHECKS[:7],
        "review_required": False,
        "citation_ceiling": "NOT_EVALUATED",
    },
    "field_validation": {
        "required_checks": EVIDENCE_CHECKS,
        "review_required": True,
        "citation_ceiling": "DESIGN_CITABLE",
    },
}

CITATION_DECISION_TABLE_VERSION = "citation_decision_table.v1"
CITATION_DECISION_TABLE = (
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


def _row(status, impact, next_action):
    return {"status": status, "impact": impact, "next_action": next_action}


STATUS_CATALOG = {
    "MESH_QUALITY_BLOCKED": _row("BLOCKED", "메시 품질 검증이 차단되어 해석 결과를 신뢰할 수 없습니다.", "메시 품질 오류를 수정하고 checkMesh를 다시 실행하세요."),
    "CASE_EVIDENCE_NOT_FOUND": _row("BLOCKED", "필수 Case Evidence를 찾을 수 없어 판단 근거가 없습니다.", "현재 케이스에서 Case Evidence를 다시 생성하세요."),
    "CASE_IDENTITY_INVALID": _row("BLOCKED", "케이스 식별 근거가 유효하지 않아 증적 연결을 신뢰할 수 없습니다.", "케이스 식별 파일과 해시를 다시 검증하세요."),
    "ARTIFACT_REF_INVALID": _row("BLOCKED", "증적 파일 참조가 유효하지 않아 원본을 확인할 수 없습니다.", "프로젝트 루트 기준 경로와 SHA-256을 다시 생성하세요."),
    "ARTIFACT_HASH_MISMATCH": _row("BLOCKED", "증적 파일의 SHA-256이 현재 파일과 일치하지 않습니다.", "변경된 원본을 검토한 뒤 증적을 다시 생성하세요."),
    "EVIDENCE_STATUS_NOT_EVALUATED": _row("NOT_EVALUATED", "필수 증적 검사가 아직 평가되지 않았습니다.", "필수 검사와 원본 근거를 완료한 뒤 다시 평가하세요."),
    "REVIEW_TARGET_CHANGED": _row("BLOCKED", "검토 대상 증적이 변경되어 기존 검토를 사용할 수 없습니다.", "현재 증적 해시를 기준으로 새 검토를 등록하세요."),
    "REVIEW_REQUIRED": _row("NOT_EVALUATED", "이 목적에는 현재 승인된 검토가 필요합니다.", "현재 증적 해시를 대상으로 검토 승인을 받으세요."),
    "REVIEW_REJECTED": _row("BLOCKED", "현재 검토가 거절되어 설계 인용을 허용할 수 없습니다.", "거절 사유를 해소하고 새 검토를 요청하세요."),
    "REVIEW_HISTORY_AMBIGUOUS": _row("BLOCKED", "검토 이력이 분기되어 현재 승인 상태를 확정할 수 없습니다.", "검토 분기를 해소하는 새 결의 검토를 등록하세요."),
    "CITATION_BLOCKED": _row("BLOCKED", "현재 케이스는 설계 인용 조건을 충족하지 못했습니다.", "차단 사유와 필수 증적을 해결한 뒤 다시 평가하세요."),
    "CITATION_EVIDENCE_OR_REVIEW_INVALID": _row("BLOCKED", "증적 또는 필수 검토가 무효·오래되어 인용할 수 없습니다.", "현재 원본과 검토 대상 해시를 다시 검증하세요."),
    "REQUIRED_CHECK_FAILED_OR_BLOCKED": _row("BLOCKED", "목적에 필요한 검사가 실패했거나 차단되었습니다.", "차단 또는 실패한 필수 검사를 해결하세요."),
    "REQUIRED_CHECK_NOT_EVALUATED": _row("NOT_EVALUATED", "목적에 필요한 검사가 아직 평가되지 않았습니다.", "누락된 필수 검사를 평가하세요."),
    "BENCHMARK_NOT_DESIGN_CITABLE": _row("NOT_EVALUATED", "벤치마크 목적은 설계 인용 판단 대상이 아닙니다.", "설계 인용이 필요하면 설계 검토 목적의 케이스를 준비하세요."),
    "SCREENING_ONLY": _row("NOT_EVALUATED", "현재 근거는 선별 검토용이며 설계 인용에는 사용할 수 없습니다.", "설계 검토용 증적과 승인을 완료하세요."),
    "DESIGN_CITABLE": _row("PASS", "필수 근거와 승인 검토가 충족되어 설계 인용이 가능합니다.", "인용 시 현재 증적과 검토 해시를 함께 보관하세요."),
}


def status_descriptor(code: str) -> dict[str, str]:
    """Return a copy so callers cannot mutate the catalog authority."""
    try:
        return STATUS_CATALOG[code].copy()
    except KeyError as exc:
        raise ValueError(f"Unknown status code: {code}") from exc
