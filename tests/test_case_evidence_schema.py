import copy
import json
from pathlib import Path

import pytest
from jsonschema import ValidationError, validate


ROOT = Path(__file__).resolve().parents[1]
SHA256 = "a" * 64


def _schema(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def _link(path, contract=None):
    value = {"path": path, "sha256": SHA256}
    if contract is not None:
        value["contract"] = contract
    return value


def _evidence_check(check_id, status="PASS"):
    return {"id": check_id, "status": status, "reason_codes": [], "evidence_refs": []}


def _evidence():
    from cfd_status_catalog import EVIDENCE_CHECKS

    return {
        "contract": "case_evidence.v1",
        "schema_version": 1,
        "created_at": "2026-08-24T00:00:00Z",
        "purpose": "design_review_candidate",
        "case_identity": _link("_body_solver/room-001/case_identity.v1.json", "case_identity.v1"),
        "checks": [_evidence_check(check_id) for check_id in EVIDENCE_CHECKS],
        "artifact_refs": {
            "geometry": _link("_body_solver/room-001/geometry.json"),
            "surface": _link("_body_solver/room-001/surface_manifest.json", "surface_manifest.v1"),
            "mesh": _link("_body_solver/room-001/mesh_manifest.json", "mesh_manifest.v1"),
            "run": _link("_body_solver/room-001/run_manifest.json", "run_manifest.v1"),
            "result": _link("_body_solver/room-001/result_manifest.json", "result_manifest.v1"),
        },
        "status": "PASS",
        "errors": [],
    }


def _legacy_evidence():
    value = _evidence()
    value.pop("case_identity")
    value["purpose"] = "screening"
    value["legacy_case_ref"] = {
        "case_id": "room-001",
        "geometry_path": "_body_solver/room-001/geometry.json",
        "geometry_sha256": SHA256,
        "run_manifest_path": "_body_solver/room-001/run_manifest.json",
        "run_manifest_sha256": SHA256,
    }
    return value


def _health_check(status="PASS"):
    return {
        "status": status,
        "reason_codes": [],
        "evidence_refs": [],
        "impact": "검증 상태를 확인해야 합니다.",
        "next_actions": ["근거를 검토하세요."],
    }


def _health():
    from cfd_status_catalog import CASE_HEALTH_CHECKS, CITATION_DECISION_TABLE, CITATION_DECISION_TABLE_VERSION

    return {
        "contract": "case_health.v1",
        "schema_version": 1,
        "created_at": "2026-08-24T00:00:00Z",
        "purpose": "design_review_candidate",
        "evidence": _link("_body_solver/room-001/case_evidence.v1.json", "case_evidence.v1"),
        "case_identity": _link("_body_solver/room-001/case_identity.v1.json", "case_identity.v1"),
        "checks": {check_id: _health_check() for check_id in CASE_HEALTH_CHECKS},
        "status": "PASS",
        "citation_status": "DESIGN_CITABLE",
        "citation_decision_table_version": CITATION_DECISION_TABLE_VERSION,
        "citation_decision_table": list(CITATION_DECISION_TABLE),
        "errors": [],
    }


def _review():
    return {
        "contract": "case_review.v1",
        "schema_version": 1,
        "created_at": "2026-08-24T00:00:00Z",
        "review_id": "review-001",
        "reviewer": "reviewer@example.com",
        "decision": "APPROVED",
        "status": "APPROVED",
        "reason": "검토를 완료했습니다.",
        "target": _link("_body_solver/room-001/case_evidence.v1.json", "case_evidence.v1"),
        "supersedes_review_ids": [],
        "errors": [],
    }


def _assert_rejected(schema_name, payload):
    with pytest.raises(ValidationError):
        validate(payload, _schema(schema_name))


def test_case_evidence_schema_accepts_closed_current_identity_payload():
    validate(_evidence(), _schema("case_evidence.v1.schema.json"))


def test_case_evidence_schema_accepts_closed_legacy_screening_payload():
    validate(_legacy_evidence(), _schema("case_evidence.v1.schema.json"))


@pytest.mark.parametrize(
    "mutation",
    (
        lambda payload: payload["artifact_refs"]["geometry"].pop("sha256"),
        lambda payload: payload["artifact_refs"]["geometry"].__setitem__("sha256", "A" * 64),
        lambda payload: payload.__setitem__("status", "UNKNOWN"),
        lambda payload: payload["artifact_refs"]["geometry"].__setitem__("path", "C:/escape.json"),
        lambda payload: payload["artifact_refs"]["geometry"].__setitem__("path", "_body_solver\\escape.json"),
        lambda payload: payload["artifact_refs"]["geometry"].__setitem__("path", "_body_solver/../escape.json"),
        lambda payload: payload.__setitem__("unexpected", True),
        lambda payload: payload["checks"].__setitem__(1, copy.deepcopy(payload["checks"][0])),
        lambda payload: payload["checks"][0].__setitem__("reason_codes", ["CODE", "CODE"]),
        lambda payload: payload["checks"][0].__setitem__("evidence_refs", ["geometry", "geometry"]),
        lambda payload: payload.pop("case_identity"),
        lambda payload: payload.__setitem__("legacy_case_ref", _legacy_evidence()["legacy_case_ref"]),
    ),
)
def test_case_evidence_schema_rejects_invalid_current_identity_or_reference(mutation):
    payload = _evidence()
    mutation(payload)
    _assert_rejected("case_evidence.v1.schema.json", payload)


@pytest.mark.parametrize(
    "mutation",
    (
        lambda payload: payload["legacy_case_ref"].pop("geometry_sha256"),
        lambda payload: payload["legacy_case_ref"].__setitem__("extra", True),
        lambda payload: payload.__setitem__("purpose", "design_review_candidate"),
    ),
)
def test_case_evidence_schema_rejects_invalid_legacy_bridge(mutation):
    payload = _legacy_evidence()
    mutation(payload)
    _assert_rejected("case_evidence.v1.schema.json", payload)


def test_case_health_schema_accepts_exact_catalog_table_and_fixed_checks():
    from cfd_status_catalog import CITATION_DECISION_TABLE, CITATION_DECISION_TABLE_VERSION

    schema = _schema("case_health.v1.schema.json")

    assert schema["properties"]["citation_decision_table_version"]["const"] == CITATION_DECISION_TABLE_VERSION
    assert schema["properties"]["citation_decision_table"]["const"] == list(CITATION_DECISION_TABLE)
    validate(_health(), schema)


@pytest.mark.parametrize(
    "mutation",
    (
        lambda payload: payload["checks"].pop("design_ready"),
        lambda payload: payload["checks"].__setitem__("unknown", _health_check()),
        lambda payload: payload.pop("citation_decision_table_version"),
        lambda payload: payload.pop("citation_decision_table"),
        lambda payload: payload.__setitem__("citation_status", "UNKNOWN"),
        lambda payload: payload["citation_decision_table"].__setitem__(0, {"id": "drift"}),
        lambda payload: payload.__setitem__("unexpected", True),
    ),
)
def test_case_health_schema_rejects_drift_or_invalid_check_shape(mutation):
    payload = _health()
    mutation(payload)
    _assert_rejected("case_health.v1.schema.json", payload)


def test_case_health_schema_rejects_legacy_design_citation():
    payload = _health()
    payload.pop("case_identity")
    payload["purpose"] = "screening"
    payload["legacy_case_ref"] = _legacy_evidence()["legacy_case_ref"]

    _assert_rejected("case_health.v1.schema.json", payload)


def test_case_review_schema_accepts_closed_append_only_review():
    validate(_review(), _schema("case_review.v1.schema.json"))


@pytest.mark.parametrize(
    "mutation",
    (
        lambda payload: payload["target"].pop("sha256"),
        lambda payload: payload["target"].__setitem__("path", "../case_evidence.v1.json"),
        lambda payload: payload.__setitem__("decision", "UNKNOWN"),
        lambda payload: payload.__setitem__("status", "REJECTED"),
        lambda payload: payload.__setitem__("unexpected", True),
        lambda payload: payload["supersedes_review_ids"].extend(["review-0", "review-0"]),
    ),
)
def test_case_review_schema_rejects_invalid_target_lifecycle_or_extra_data(mutation):
    payload = _review()
    mutation(payload)
    _assert_rejected("case_review.v1.schema.json", payload)
