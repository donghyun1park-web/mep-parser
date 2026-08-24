import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cfd_report


class BodyFittedReportTests(unittest.TestCase):
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

    def test_report_shows_authoritative_design_gate_and_numerical_evidence(self):
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
        self.assertIn("DESIGN_CITABLE", text)
        self.assertIn("design_limited_second_order_v1", text)
        self.assertIn("0.820", text)
        self.assertIn("91.0%", text)
        self.assertIn("0.030%", text)


if __name__ == "__main__":
    unittest.main()
