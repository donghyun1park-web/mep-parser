import json
from pathlib import Path
import tempfile
import unittest

import cfd_report


class CaseSummarySupplySpeedTests(unittest.TestCase):
    def _summary(self, meta):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-report-summary-", dir=repo) as tmp:
            case = Path(tmp)
            (case / "cfd_case_meta.json").write_text(
                json.dumps(meta), encoding="utf-8"
            )
            return cfd_report.case_summary(case)

    def test_opening_case_uses_area_weighted_supply_patch_speed(self):
        summary = self._summary({
            "config": {
                "name": "diffuser case",
                "room": {"L": 10, "W": 5, "H": 3},
                "inlet": {},
            },
            "mesh": {"cells": 1000},
            "patches": [
                {"role": "supply", "area": 0.04, "U": [0, 0, -3.0]},
                {"role": "supply", "area": 0.02, "U": [0, 0, -6.0]},
                {"role": "exhaust", "area": 0.04, "U": None},
            ],
        })

        self.assertEqual(summary["supply_u"], 4.0)

    def test_full_wall_case_keeps_configured_inlet_speed(self):
        summary = self._summary({
            "config": {
                "name": "wall case",
                "room": {"L": 10, "W": 5, "H": 3},
                "inlet": {"U": [0.3, 0, 0]},
            },
            "mesh": {"cells": 1000},
        })

        self.assertEqual(summary["supply_u"], 0.3)

    def test_opening_case_exposes_design_and_snapped_face_velocities(self):
        """The dashboard must not present a snapped opening speed as design speed."""
        summary = self._summary({
            "config": {
                "name": "opening preflight warning",
                "room": {"L": 10, "W": 5, "H": 3},
                "inlet": {},
            },
            "mesh": {"cells": 1000},
            "patches": [
                {"role": "supply", "area": 0.0225, "U": [0, 0, -5.481481]},
            ],
            "opening_preflight": {
                "contract": "opening_preflight.v2",
                "terminal_count": 1,
                "opening_resolution_ok": False,
                "jet_metrics_citable": False,
                "warnings": ["sup0"],
                "terminals": [{
                    "role": "supply",
                    "requested_area_m2": 0.04,
                    "snapped_area_m2": 0.0225,
                    "design_cmh": 444.0,
                    "applied_normal_cmh": 444.0,
                }],
            },
        })

        self.assertEqual(summary["opening_preflight_status"], "AVAILABLE")
        self.assertFalse(summary["opening_resolution_ok"])
        self.assertFalse(summary["jet_metrics_citable"])
        self.assertEqual(summary["opening_warning_count"], 1)
        self.assertAlmostEqual(summary["design_supply_u"], 3.083333, places=5)
        self.assertAlmostEqual(summary["snapped_supply_u"], 5.481481, places=5)

    def test_unrun_case_has_an_explicit_not_evaluated_result_contract(self):
        summary = self._summary({
            "config": {
                "name": "new case",
                "room": {"L": 10, "W": 5, "H": 3},
                "inlet": {"U": [0.3, 0, 0]},
            },
            "mesh": {"cells": 1000},
        })

        self.assertEqual(summary["result_status"], "NOT_EVALUATED")
        self.assertEqual(summary["citation_status"], "NOT_EVALUATED")
        self.assertFalse(summary["citable"])
        self.assertIn("solver_not_run", summary["blockers"])


if __name__ == "__main__":
    unittest.main()
