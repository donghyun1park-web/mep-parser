import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cfd_report
import cfd_evidence
import cfd_review
from test_cfd_advice import BASE_METRICS, GOOD_TRUST, _case_health, _meta, _patch
from test_cfd_evidence import make_complete_case


def _write_json(path, payload):
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _future_design_case(base, *, reviewer="reviewer-1", reason="reviewed"):
    paths = make_complete_case(base, with_gci=True)
    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"]
    )
    evidence.pop("legacy_case_ref")
    evidence["case_identity"] = {
        "contract": "case_identity.v1",
        "path": evidence["artifact_refs"]["geometry"]["path"],
        "sha256": evidence["artifact_refs"]["geometry"]["sha256"],
    }
    evidence["purpose"] = "design_review_candidate"
    evidence["status"] = "PASS"
    evidence["errors"] = []
    for check in evidence["checks"]:
        check.update(status="PASS", reason_codes=[], evidence_refs=[])
    _write_json(paths["evidence"], evidence)
    review = cfd_review.create_review(
        paths["evidence"],
        projects_root=paths["root"],
        expected_target_sha256=hashlib.sha256(
            paths["evidence"].read_bytes()
        ).hexdigest(),
        reviewer_id=reviewer,
        decision="APPROVED",
        reason=reason,
    )
    return paths, review


class BodyFittedReportTests(unittest.TestCase):
    def test_legacy_green_report_threads_blocked_health_into_advice_and_digests(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "legacy-report.html"
            parsed = {
                "n_iters": 1,
                "crashed": False,
                "residuals": {},
                "rho_min": [],
                "bounding": [],
                "continuity_global": [],
            }
            meta = _meta([_patch("sup0", "supply", 0.2, 12.0)])
            meta["mesh"] = {"nx": 2, "ny": 2, "nz": 1, "cells": 4}
            blocked = _case_health(
                "CITATION_BLOCKED", reason="ARTIFACT_HASH_MISMATCH"
            )
            legacy_green = {
                **GOOD_TRUST,
                "color": "#1e8449",
                "contract": "result_trust.v1",
                "status": "PASS",
                "run_status": "PASS",
                "convergence_status": "PASS",
                "design_ready": True,
                "citation_status": "DESIGN_CITABLE",
                "blockers": [],
                "evidence": {},
            }
            with patch.object(
                cfd_report, "result_trust", return_value=legacy_green
            ):
                cfd_report.build_html_report(
                    tmp, meta, parsed, None, None, BASE_METRICS, out,
                    case_health=blocked,
                )
            digest = out.with_name(out.stem + "_ai_digest.md").read_text(
                encoding="utf-8"
            )
            payload = json.loads(out.with_name(
                out.stem + "_ai_digest.json"
            ).read_text(encoding="utf-8"))

        self.assertIn("설계 결론을 내리지 마십시오", digest)
        self.assertIn("CITATION_BLOCKED", digest)
        self.assertFalse(payload["citable"])
        self.assertEqual(payload["citation_status"], "CITATION_BLOCKED")
        self.assertEqual(payload["recommendations"][0]["group"], "evidence")
    def test_report_uses_authoritative_not_evaluated_gate_when_evidence_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            (case / "results").mkdir()
            (case / "result_manifest.json").write_text(json.dumps({
                "summary_path": "results/body_fitted_summary.json",
                "slices": [
                    {"axis": "x", "target_m": 2.0, "sample_count": 100},
                    {"axis": "y", "target_m": 1.5, "sample_count": 120},
                    {"axis": "z", "target_m": 1.4, "sample_count": 90},
                ],
            }), encoding="utf-8")
            (case / "results" / "body_fitted_summary.json").write_text(json.dumps({
                "time_s": 0.26, "cell_count": 20377,
                "aggregation": "cell_count_unweighted",
                "temperature": {
                    "minimum": 293.15, "maximum": 293.974,
                    "hottest_cell": {"temperature_k": 293.974, "centre_m": [2.5, 1.1, 0.04]},
                },
                "velocity": {
                    "mean_speed": 0.168, "maximum_speed": 1.42,
                    "peak_cell": {"speed_m_s": 1.42, "centre_m": [3.27, 2.24, 2.76]},
                },
            }), encoding="utf-8")
            (case / "run_manifest.json").write_text(json.dumps({
                "status": "WARN", "warnings": ["THERMAL_WINDOW_TOO_SHORT"],
                "thermal_progress": {
                    "completed_duration_s": 0.26, "required_duration_s": 59.22,
                    "flow_through_fraction": 0.0011,
                    "estimated_remaining_runtime_seconds": 3120,
                    "energy_balance": {
                        "input_energy_j": 208.0,
                        "stored_sensible_energy_j": 207.9,
                        "cumulative_exhaust_energy_j": 0.1,
                        "transient_closure_ratio": 1.0,
                        "history_complete": True,
                        "method": "room_storage_plus_trapezoidal_solver_exhaust",
                    },
                },
            }), encoding="utf-8")
            (case / "thermal_input.json").write_text(json.dumps({
                "heat": {
                    "input_power_w": 5000.0,
                    "requested_convective_power_w": 4000.0,
                    "applied_convective_power_w": 4000.0,
                    "deferred_convective_power_w": 0.0,
                    "excluded_radiative_power_w": 1000.0,
                },
                "heat_sources": [{
                    "name": "equipment_DXF_EHP_01",
                    "source_id": "DXF-EHP-01",
                    "source_label": "EHP-01 (로비)",
                    "source_ref": {
                        "handle": "1A2B",
                        "layer": "DVM_INDOOR",
                        "block_name": "EHP_DUCT",
                    },
                    "source_element_ids": ["DXF-EHP-01"],
                    "override_of_dxf": True,
                    "power_kw": 5.0, "convective_fraction": 0.8,
                    "radiative_fraction": 0.2,
                    "convective_power_w": 4000.0,
                    "requested_convective_power_w": 4000.0,
                    "applied_convective_power_w": 4000.0,
                    "deferred_convective_power_w": 0.0,
                    "excluded_radiative_power_w": 1000.0,
                    "evidence": "equipment_schedule:M03-001",
                }],
            }), encoding="utf-8")
            result = cfd_report.generate_body_fitted_report(case)
            text = (case / "body_fitted_report.html").read_text(encoding="utf-8")
        self.assertTrue(result["ok"], result)
        self.assertIn("상세 열·부력 결과 요약", text)
        self.assertIn("NOT_EVALUATED", text)
        self.assertIn("Case Evidence", text)
        self.assertIn("설계 인용 불가", text)
        self.assertNotIn("스크리닝 결과", text)
        self.assertIn("293.974 K", text)
        self.assertIn("1.420 m/s", text)
        self.assertIn("cell_count_unweighted", text)
        self.assertIn("과도 에너지 폐합", text)
        self.assertIn("100.00%", text)
        self.assertIn("확정 장비 열원 계약", text)
        self.assertIn("DXF-EHP-01", text)
        self.assertIn("EHP-01 (로비)", text)
        self.assertIn("1A2B", text)
        self.assertIn("DXF 원본 + 사용자 변경", text)
        self.assertIn("equipment_schedule:M03-001", text)
        self.assertIn("보류 대류", text)
        self.assertIn("복사비", text)
        self.assertIn("20.0%", text)

    def test_report_distinguishes_scaled_applied_heat_from_requested_heat(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            (case / "results").mkdir()
            (case / "result_manifest.json").write_text(json.dumps({
                "summary_path": "results/body_fitted_summary.json", "slices": [],
            }), encoding="utf-8")
            (case / "results" / "body_fitted_summary.json").write_text(json.dumps({
                "time_s": 1.0, "cell_count": 1,
                "temperature": {}, "velocity": {},
            }), encoding="utf-8")
            (case / "run_manifest.json").write_text(json.dumps({
                "status": "WARN", "warnings": ["CONDITION_MATRIX_NOT_FULL"],
            }), encoding="utf-8")
            (case / "thermal_input.json").write_text(json.dumps({
                "heat": {
                    "input_power_w": 5000.0,
                    "requested_convective_power_w": 4000.0,
                    "applied_convective_power_w": 2000.0,
                    "deferred_convective_power_w": 2000.0,
                    "excluded_radiative_power_w": 1000.0,
                    "application_scale": 0.5,
                },
                "heat_sources": [{
                    "name": "equipment_DXF_EHP_01",
                    "source_element_ids": ["DXF-EHP-01"],
                    "power_kw": 5.0, "convective_fraction": 0.8,
                    "convective_power_w": 4000.0,
                    "requested_convective_power_w": 4000.0,
                    "applied_convective_power_w": 2000.0,
                    "deferred_convective_power_w": 2000.0,
                    "evidence": "equipment_schedule:M03-001",
                }],
            }), encoding="utf-8")

            result = cfd_report.generate_body_fitted_report(case)
            text = (case / "body_fitted_report.html").read_text(encoding="utf-8")

        self.assertTrue(result["ok"], result)
        self.assertIn("요청 대류", text)
        self.assertIn("CFD 대류 적용", text)
        self.assertIn("보류 대류", text)
        self.assertIn("2000.0 W", text)

    def test_report_labels_explicit_manual_heat_input_without_claiming_dxf_origin(self):
        """A UI-confirmed manual load is traceable, but it is not a DXF object."""
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            (case / "results").mkdir()
            (case / "result_manifest.json").write_text(json.dumps({
                "summary_path": "results/body_fitted_summary.json", "slices": [],
            }), encoding="utf-8")
            (case / "results" / "body_fitted_summary.json").write_text(json.dumps({
                "time_s": 1.0, "cell_count": 1,
                "temperature": {}, "velocity": {},
            }), encoding="utf-8")
            (case / "run_manifest.json").write_text(json.dumps({
                "status": "WARN", "warnings": ["SCREENING_ONLY"],
            }), encoding="utf-8")
            (case / "thermal_input.json").write_text(json.dumps({
                "heat": {"input_power_w": 1000.0},
                "heat_sources": [{
                    "source_id": "manual_heat_1",
                    "source_label": "사용자 확인 장비",
                    "source_type": "user_confirmed",
                    "source_ref": {
                        "layer": "USER_CONFIRMED",
                        "entity_type": "UI_INPUT",
                        "source_id": "manual_heat_1",
                    },
                    "provenance": {"source_reference_kind": "manual_input"},
                    "power_kw": 1.0,
                    "convective_fraction": 0.8,
                    "radiative_fraction": 0.2,
                    "convective_power_w": 800.0,
                    "evidence": "user_confirmed:equipment_schedule",
                }],
            }), encoding="utf-8")

            result = cfd_report.generate_body_fitted_report(case)
            text = (case / "body_fitted_report.html").read_text(encoding="utf-8")

        self.assertTrue(result["ok"], result)
        self.assertIn("입력 출처", text)
        self.assertIn("사용자 입력", text)
        self.assertNotIn("DXF 출처", text)

    def test_legacy_result_gate_cannot_create_design_review_banner(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            (case / "results").mkdir()
            (case / "result_manifest.json").write_text(json.dumps({
                "summary_path": "results/body_fitted_summary.json", "slices": [],
            }), encoding="utf-8")
            (case / "results" / "body_fitted_summary.json").write_text(json.dumps({
                "time_s": 1.0, "cell_count": 1,
                "temperature": {}, "velocity": {},
            }), encoding="utf-8")
            (case / "run_manifest.json").write_text(json.dumps({
                "status": "PASS",
                "numerical_quality": {
                    "profile": "design_limited_second_order_v1",
                    "status": "PASS",
                    "blockers": [],
                    "courant": {"peak_maximum": 0.82, "gate": 1.0},
                    "wall_treatment": {"acceptable_area_ratio": 0.91},
                    "flux_balance": {"imbalance_ratio": 0.0003},
                },
            }), encoding="utf-8")
            gate = {
                "citation_status": "DESIGN_CITABLE", "status": "PASS",
                "citable": True, "blockers": [], "reasons": [],
            }
            with patch("cfd_result_gate.evaluate_body_fitted_case", return_value=gate) as evaluate:
                result = cfd_report.generate_body_fitted_report(case)
            text = (case / "body_fitted_report.html").read_text(encoding="utf-8")

        self.assertTrue(result["ok"], result)
        evaluate.assert_called_once_with(str(case))
        self.assertNotIn("설계 검토 인용 가능", text)
        self.assertIn("Case Evidence", text)
        self.assertIn("레거시 결과 게이트", text)
        self.assertIn("design_limited_second_order_v1", text)
        self.assertIn("0.820", text)
        self.assertIn("91.0%", text)
        self.assertIn("0.030%", text)

    def test_screening_report_has_exact_top_and_print_watermark_and_fixed_check_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_complete_case(Path(tmp), with_gci=True)
            cfd_evidence.build_case_evidence(
                paths["case"], projects_root=paths["root"]
            )

            result = cfd_report.generate_body_fitted_report(
                paths["case"], projects_root=paths["root"]
            )
            text = Path(result["report_path"]).read_text(encoding="utf-8")

        self.assertTrue(result["ok"], result)
        watermark = "초기안 비교용 · 설계 인용 불가"
        self.assertEqual(text.count(watermark), 1)
        self.assertLess(text.index(watermark), text.index("<h1>"))
        self.assertIn("@media print", text)
        self.assertIn("first-content", text)
        order = [
            "geometry_valid", "bc_reviewed", "mesh_checked",
            "solver_converged", "numerics_verified", "grid_verified",
            "benchmark_validated", "field_calibrated", "design_ready",
        ]
        positions = [text.index(check_id) for check_id in order]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("설계 검토 인용 가능", text)

    def test_design_citable_report_shows_only_validated_review_binding_and_escapes_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths, review = _future_design_case(
                Path(tmp), reviewer="<script>alert(1)</script>",
                reason="<b>approved & bound</b>",
            )
            with patch.object(
                cfd_report.cfd_case_health.cfd_evidence,
                "validate_case_evidence", return_value=[],
            ):
                result = cfd_report.generate_body_fitted_report(
                    paths["case"], projects_root=paths["root"]
                )
            text = Path(result["report_path"]).read_text(encoding="utf-8")

        self.assertTrue(result["ok"], result)
        self.assertIn("DESIGN_CITABLE", text)
        self.assertIn("설계 검토 인용 가능", text)
        self.assertIn("design_review_candidate", text)
        self.assertIn(review["review_id"], text)
        self.assertIn(review["target"]["sha256"], text)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", text)
        self.assertIn("&lt;b&gt;approved &amp; bound&lt;/b&gt;", text)
        self.assertNotIn("<script>alert(1)</script>", text)
        self.assertNotIn("<b>approved & bound</b>", text)

    def test_blocked_and_not_evaluated_reports_are_non_green_and_forbid_design_wording(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = make_complete_case(Path(tmp))
            cfd_evidence.build_case_evidence(
                paths["case"], projects_root=paths["root"]
            )
            baseline = cfd_report.cfd_case_health.build_case_health(
                paths["evidence"], projects_root=paths["root"]
            )
            for status in ("CITATION_BLOCKED", "NOT_EVALUATED"):
                health = json.loads(json.dumps(baseline))
                health["citation_status"] = status
                health["errors"] = [{"code": "REVIEW_REQUIRED"}]
                with self.subTest(status=status), patch.object(
                    cfd_report.cfd_case_health, "build_case_health",
                    return_value=health,
                ), patch.object(
                    cfd_report.cfd_case_health, "review_summary",
                    return_value={"status": "MISSING"},
                ):
                    out = paths["case"] / f"{status}.html"
                    result = cfd_report.generate_body_fitted_report(
                        paths["case"], out, projects_root=paths["root"]
                    )
                    text = out.read_text(encoding="utf-8")
                self.assertTrue(result["ok"], result)
                self.assertIn(f"citation-banner {status.lower().replace('_', '-')}", text)
                self.assertNotIn("citation-banner pass", text)
                self.assertNotIn("설계 검토 인용 가능", text)


if __name__ == "__main__":
    unittest.main()
