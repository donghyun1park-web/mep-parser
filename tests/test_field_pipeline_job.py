import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

import field_pipeline_job


class FieldPipelineJobTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[1]
        self.tmp = tempfile.TemporaryDirectory(prefix=".test-field-pipeline-", dir=self.repo)
        self.root = Path(self.tmp.name) / "cfd_projects"
        self.root.mkdir()
        source = self.root / "imports" / "actual-site.dxf"
        source.parent.mkdir()
        source.write_text("0\nSECTION\n0\nEOF\n", encoding="ascii")
        geometry = json.loads(
            (self.repo / "cfd_benchmarks" / "g2_thermal" / "geometry.json")
            .read_text(encoding="utf-8")
        )
        geometry["source"] = str(source.resolve())
        self.geometry = self.root / "imports" / "actual-site.geometry.json"
        self.geometry.write_text(json.dumps(geometry), encoding="utf-8")
        self.source = source

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_is_deterministic_and_forces_design_contract(self):
        first = field_pipeline_job.create_job(self.root, self.geometry)
        second = field_pipeline_job.create_job(self.root, self.geometry)

        self.assertTrue(first["ok"], first)
        self.assertEqual(first["job"], second["job"])
        self.assertFalse(first["existing"])
        self.assertTrue(second["existing"])
        manifest = first["manifest"]
        self.assertEqual(manifest["contract"], "field_pipeline_job.v1")
        self.assertEqual(manifest["level"]["background_cell_m"], 0.35)
        self.assertEqual(manifest["citation_status"], "NOT_EVALUATED")
        self.assertEqual(manifest["citation_blockers"], [])
        self.assertEqual(
            manifest["input"]["thermal_settings"][
                "thermal_minimum_flow_through_fraction"
            ],
            3.0,
        )

    def test_external_or_missing_source_dxf_is_rejected(self):
        geometry = json.loads(self.geometry.read_text(encoding="utf-8"))
        geometry["source"] = "missing.dxf"
        self.geometry.write_text(json.dumps(geometry), encoding="utf-8")

        result = field_pipeline_job.create_job(self.root, self.geometry)

        self.assertFalse(result["ok"])
        self.assertIn("원본 DXF", result["error"])

    def test_renamed_bundled_sample_is_rejected_by_job_engine(self):
        shutil.copyfile(self.repo / "sample_plan.dxf", self.source)

        result = field_pipeline_job.create_job(self.root, self.geometry)

        self.assertFalse(result["ok"])
        self.assertIn("샘플", result["error"])

    def test_raw_analysis_completion_is_held_for_design_citation_review(self):
        created = field_pipeline_job.create_job(self.root, self.geometry)
        job = created["job"]
        thermal = self.root / "_body_solver" / "actual-field-design"

        def shared_runner(root, occ, manifest, level, job_path,
                          callback=None, case_prefix=None):
            thermal.mkdir(parents=True)
            (thermal / "result_manifest.json").write_text("{}", encoding="utf-8")
            (thermal / "run_manifest.json").write_text(json.dumps({
                "status": "PASS", "design_ready": True,
                "thermal_progress": {
                    "latest_time_s": 300.0, "flow_through_time_s": 100.0,
                    "flow_through_fraction": 3.0,
                },
            }), encoding="utf-8")
            level.update(thermal_case=str(thermal), status="WARN", stage="complete",
                         latest_time_s=300.0, flow_through_fraction=3.0)
            return thermal

        inspection = {"ok": True, "manifest": {"air_volume": {"volume_m3": 10}}}
        with mock.patch.object(
            field_pipeline_job.cfd_occ, "inspect_occ_output",
            side_effect=[{"ok": False}, inspection, inspection],
        ), mock.patch.object(
            field_pipeline_job.cfd_occ, "run_occ_job", return_value={"ok": True}
        ) as occ, mock.patch.object(
            field_pipeline_job.cfd_mesh, "estimate_resources",
            return_value={"estimated_cells": 12000, "estimated_ram_gb": 1.0},
        ), mock.patch.object(
            field_pipeline_job.cfd_gci_job, "validate_completed_design_level",
            return_value=None,
        ) as completed_validator, mock.patch.object(
            field_pipeline_job.cfd_gci_job, "run_thermal_design_level",
            side_effect=shared_runner,
        ) as runner:
            result = field_pipeline_job.run_job(self.root, job)

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["case"], thermal.name)
        occ.assert_called_once()
        completed_validator.assert_called_once_with(
            mock.ANY, target_flow_fraction=3.0
        )
        self.assertIn("-field-", runner.call_args.kwargs["case_prefix"])
        saved = field_pipeline_job.load_job(self.root, job)
        self.assertEqual(saved["status"], "analysis_complete_not_citable")
        self.assertEqual(saved["citation_status"], "NOT_EVALUATED")
        self.assertIn("mesh_manifest_missing", saved["citation_blockers"])
        self.assertEqual(saved["level"]["status"], "WARN")
        self.assertEqual(saved["level"]["flow_through_fraction"], 3.0)
        self.assertEqual(saved["result_case"], str(thermal))

    def test_design_citable_analysis_is_marked_complete(self):
        created = field_pipeline_job.create_job(self.root, self.geometry)
        job = created["job"]
        thermal = self.root / "_body_solver" / "actual-field-design"

        def shared_runner(root, occ, manifest, level, job_path,
                          callback=None, case_prefix=None):
            thermal.mkdir(parents=True)
            (thermal / "result_manifest.json").write_text("{}", encoding="utf-8")
            (thermal / "run_manifest.json").write_text(json.dumps({
                "status": "PASS", "design_ready": True,
                "thermal_progress": {
                    "latest_time_s": 300.0, "flow_through_time_s": 100.0,
                    "flow_through_fraction": 3.0,
                },
            }), encoding="utf-8")
            level.update(thermal_case=str(thermal), status="PASS", stage="complete",
                         latest_time_s=300.0, flow_through_fraction=3.0)
            return thermal

        inspection = {"ok": True, "manifest": {"air_volume": {"volume_m3": 10}}}
        design_citable = {
            "contract": "result_trust.v1", "status": "PASS", "design_ready": True,
            "citation_status": "DESIGN_CITABLE", "citable": True,
            "blockers": [], "reasons": [],
        }
        with mock.patch.object(
            field_pipeline_job.cfd_occ, "inspect_occ_output",
            side_effect=[{"ok": False}, inspection, inspection],
        ), mock.patch.object(
            field_pipeline_job.cfd_occ, "run_occ_job", return_value={"ok": True}
        ), mock.patch.object(
            field_pipeline_job.cfd_mesh, "estimate_resources",
            return_value={"estimated_cells": 12000, "estimated_ram_gb": 1.0},
        ), mock.patch.object(
            field_pipeline_job.cfd_gci_job, "validate_completed_design_level",
            return_value=None,
        ), mock.patch.object(
            field_pipeline_job.cfd_gci_job, "run_thermal_design_level",
            side_effect=shared_runner,
        ), mock.patch.object(
            field_pipeline_job.cfd_result_gate, "evaluate_body_fitted_case",
            return_value=design_citable,
        ) as gate:
            result = field_pipeline_job.run_job(self.root, job)

        self.assertTrue(result["ok"], result)
        saved = field_pipeline_job.load_job(self.root, job)
        self.assertEqual(saved["status"], "complete")
        self.assertEqual(saved["citation_status"], "DESIGN_CITABLE")
        self.assertEqual(saved["citation_blockers"], [])
        gate.assert_called_once_with(thermal, gci_root=self.root / "_body_gci")

    def test_terminal_job_is_live_reviewed_before_claiming_design_citation(self):
        thermal = self.root / "_body_solver" / "previous-complete"
        thermal.mkdir(parents=True)
        manifest = {
            "status": "complete", "result_case": str(thermal),
            "citation_status": "DESIGN_CITABLE", "citation_blockers": [],
        }
        not_citable = {
            "contract": "result_trust.v1", "status": "NOT_EVALUATED",
            "design_ready": False, "citation_status": "NOT_EVALUATED",
            "citable": False, "blockers": ["gci"], "reasons": ["missing GCI"],
        }
        with mock.patch.object(
            field_pipeline_job.cfd_result_gate, "evaluate_body_fitted_case",
            return_value=not_citable,
        ):
            reviewed = field_pipeline_job.review_terminal_job_citation(
                self.root, manifest
            )

        self.assertEqual(reviewed["status"], "analysis_complete_not_citable")
        self.assertEqual(reviewed["citation_status"], "NOT_EVALUATED")
        self.assertEqual(reviewed["citation_blockers"], ["gci"])

    def test_terminal_job_is_not_relaunched_for_citation_refresh(self):
        created = field_pipeline_job.create_job(self.root, self.geometry)
        terminal = dict(created["manifest"])
        terminal.update(
            status="analysis_complete_not_citable",
            result_case=str(self.root / "_body_solver" / "previous-result"),
        )
        field_pipeline_job.cfd_gci_job._atomic_json(
            created["manifest_path"], terminal
        )
        held = {
            "contract": "result_trust.v1", "status": "NOT_EVALUATED",
            "design_ready": False, "citation_status": "NOT_EVALUATED",
            "citable": False, "blockers": ["gci"], "reasons": ["missing GCI"],
        }

        with mock.patch.object(
            field_pipeline_job.cfd_result_gate, "evaluate_body_fitted_case",
            return_value=held,
        ), mock.patch.object(field_pipeline_job.cfd_occ, "run_occ_job") as occ:
            result = field_pipeline_job.run_job(self.root, created["job"])

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["already_complete"])
        self.assertEqual(result["manifest"]["status"], "analysis_complete_not_citable")
        occ.assert_not_called()

    def test_changed_source_is_rejected_before_occ(self):
        created = field_pipeline_job.create_job(self.root, self.geometry)
        self.source.write_text("changed", encoding="utf-8")

        with mock.patch.object(field_pipeline_job.cfd_occ, "run_occ_job") as occ:
            result = field_pipeline_job.run_job(self.root, created["job"])

        self.assertFalse(result["ok"])
        self.assertIn("원본 DXF가 변경", result["error"])
        occ.assert_not_called()

    def test_shared_stage_callbacks_support_single_level_manifest(self):
        created = field_pipeline_job.create_job(self.root, self.geometry)
        manifest = created["manifest"]
        manifest["stage"] = "design:mesh_run"
        events = []

        field_pipeline_job.cfd_gci_job._publish(
            created["manifest_path"], manifest, events.append, "메시 계산"
        )
        field_pipeline_job.cfd_gci_job._line_callback(
            events.append, manifest, manifest["level"], "mesh_run"
        )("Time = 1")

        self.assertEqual(events[0]["level"]["name"], "design")
        self.assertNotIn("levels", events[0])
        self.assertEqual(events[1]["message"], "Time = 1")

    def test_schema_is_available(self):
        schema = json.loads(
            (self.repo / "field_pipeline_job.v1.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(schema["properties"]["contract"]["const"],
                         "field_pipeline_job.v1")
        self.assertIn(
            "analysis_complete_not_citable",
            schema["properties"]["status"]["enum"],
        )

    def test_live_gci_job_blocks_field_solver_overlap(self):
        field = field_pipeline_job.create_job(self.root, self.geometry)
        gci = field_pipeline_job.cfd_gci_job.create_study(
            self.root, self.geometry
        )
        gci_path = Path(gci["manifest_path"])
        token, owner = field_pipeline_job.cfd_gci_job.acquire_job_lock(gci_path)
        self.assertIsNotNone(token)
        try:
            result = field_pipeline_job.run_job(self.root, field["job"])
        finally:
            field_pipeline_job.cfd_gci_job.release_job_lock(gci_path, token)

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "CFD_SOLVER_BUSY")
        self.assertEqual(result["lock"]["pid"], owner["pid"])
        self.assertEqual(field_pipeline_job.load_job(
            self.root, field["job"]
        )["attempts"], 0)


if __name__ == "__main__":
    unittest.main()
