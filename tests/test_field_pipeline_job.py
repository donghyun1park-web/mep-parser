import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import cfd_case_health
import cfd_evidence
import cfd_review
import field_pipeline_job
from test_cfd_evidence import make_complete_case


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
        self.assertEqual(saved["citation_status"], "CITATION_BLOCKED")
        self.assertIn("CASE_EVIDENCE_NOT_FOUND", saved["citation_blockers"])
        self.assertEqual(saved["level"]["status"], "WARN")
        self.assertEqual(saved["level"]["flow_through_fraction"], 3.0)
        self.assertEqual(saved["result_case"], str(thermal))

    def test_forged_legacy_result_gate_cannot_promote_analysis(self):
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
        forged_design_citable = {
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
            return_value=forged_design_citable,
        ) as gate:
            result = field_pipeline_job.run_job(self.root, job)

        self.assertTrue(result["ok"], result)
        saved = field_pipeline_job.load_job(self.root, job)
        self.assertEqual(saved["status"], "analysis_complete_not_citable")
        self.assertNotEqual(saved["citation_status"], "DESIGN_CITABLE")
        gate.assert_not_called()

    def test_terminal_job_is_live_reviewed_before_claiming_design_citation(self):
        thermal = self.root / "_body_solver" / "previous-complete"
        thermal.mkdir(parents=True)
        manifest = {
            "status": "complete", "result_case": str(thermal),
            "citation_status": "DESIGN_CITABLE", "citation_blockers": [],
        }
        with mock.patch.object(
            field_pipeline_job.cfd_evidence, "build_case_evidence",
            side_effect=ValueError("no current evidence"),
        ):
            reviewed = field_pipeline_job.review_terminal_job_citation(self.root, manifest)

        self.assertEqual(reviewed["status"], "analysis_complete_not_citable")
        self.assertEqual(reviewed["citation_status"], "CITATION_BLOCKED")
        self.assertIn("CASE_EVIDENCE_NOT_FOUND", reviewed["citation_blockers"])

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
        with mock.patch.object(
            field_pipeline_job.cfd_evidence, "build_case_evidence",
            side_effect=ValueError("missing raw chain"),
        ), mock.patch.object(field_pipeline_job.cfd_occ, "run_occ_job") as occ:
            result = field_pipeline_job.run_job(self.root, created["job"])

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["already_complete"])
        self.assertEqual(result["manifest"]["status"], "analysis_complete_not_citable")
        occ.assert_not_called()

    def test_terminal_refresh_persists_current_validated_snapshots_without_solver_rerun(self):
        created = field_pipeline_job.create_job(self.root, self.geometry)
        case = self.root / "_body_solver" / "previous-result"
        case.mkdir(parents=True)
        evidence_path = case / "case_evidence.v1.json"
        health_path = case / "case_health.v1.json"
        evidence_path.write_text('{"contract":"case_evidence.v1"}\n', encoding="utf-8")
        health_path.write_text('{"contract":"case_health.v1"}\n', encoding="utf-8")
        terminal = dict(created["manifest"])
        terminal.update(status="complete", stage="complete", result_case=str(case))
        field_pipeline_job.cfd_gci_job._atomic_json(created["manifest_path"], terminal)
        evidence = {"contract": "case_evidence.v1"}
        health = {
            "contract": "case_health.v1", "citation_status": "DESIGN_CITABLE",
            "evidence": {
                "path": evidence_path.relative_to(self.root).as_posix(),
                "sha256": field_pipeline_job._sha256(evidence_path),
            },
            "errors": [{"code": "DESIGN_CITABLE"}],
        }
        health_path.write_text(json.dumps(health) + "\n", encoding="utf-8")
        with mock.patch.object(
            field_pipeline_job.cfd_evidence, "build_case_evidence", return_value=evidence,
        ), mock.patch.object(
            field_pipeline_job.cfd_evidence, "validate_case_evidence", return_value=[],
        ), mock.patch.object(
            field_pipeline_job.cfd_case_health, "build_case_health", return_value=health,
        ), mock.patch.object(
            field_pipeline_job.cfd_case_health, "review_summary",
            return_value={"status": "APPROVED", "review_id": "review-" + "a" * 32},
        ), mock.patch.object(field_pipeline_job.cfd_occ, "run_occ_job") as occ:
            result = field_pipeline_job.run_job(self.root, created["job"])

        self.assertTrue(result["already_complete"])
        self.assertEqual(result["manifest"]["status"], "complete")
        self.assertEqual(result["manifest"]["citation_status"], "DESIGN_CITABLE")
        self.assertEqual(result["manifest"]["citation_blockers"], [])
        self.assertEqual(
            result["manifest"]["case_evidence_path"],
            evidence_path.relative_to(self.root).as_posix(),
        )
        self.assertEqual(
            result["manifest"]["case_health_path"],
            health_path.relative_to(self.root).as_posix(),
        )
        self.assertEqual(
            field_pipeline_job.load_job(self.root, created["job"])["review_summary"]["status"],
            "APPROVED",
        )
        occ.assert_not_called()

    def test_terminal_refresh_can_demote_complete_and_clears_unvalidated_snapshots(self):
        created = field_pipeline_job.create_job(self.root, self.geometry)
        case = self.root / "_body_solver" / "previous-result"
        case.mkdir(parents=True)
        terminal = dict(created["manifest"])
        terminal.update(
            status="complete", stage="complete", result_case=str(case),
            case_evidence_path="forged.json", case_evidence_sha256="f" * 64,
            case_health_path="forged-health.json", case_health_sha256="e" * 64,
            citation_status="DESIGN_CITABLE",
            review_summary={"status": "APPROVED", "review_id": "forged"},
        )
        field_pipeline_job.cfd_gci_job._atomic_json(created["manifest_path"], terminal)

        with mock.patch.object(
            field_pipeline_job.cfd_evidence, "build_case_evidence",
            side_effect=ValueError("stale raw artifacts"),
        ), mock.patch.object(field_pipeline_job.cfd_occ, "run_occ_job") as occ:
            result = field_pipeline_job.run_job(self.root, created["job"])

        refreshed = result["manifest"]
        self.assertEqual(refreshed["status"], "analysis_complete_not_citable")
        self.assertEqual(refreshed["citation_status"], "CITATION_BLOCKED")
        self.assertIn("CASE_EVIDENCE_NOT_FOUND", refreshed["citation_blockers"])
        self.assertNotIn("case_evidence_path", refreshed)
        self.assertNotIn("case_health_path", refreshed)
        self.assertEqual(refreshed["stage"], "complete")
        occ.assert_not_called()

    def test_field_snapshot_rejects_health_bound_to_pre_mutation_evidence(self):
        case = self.root / "_body_solver" / "pair-race"
        case.mkdir(parents=True)
        evidence_path = case / "case_evidence.v1.json"
        health_path = case / "case_health.v1.json"
        evidence_path.write_text('{"contract":"case_evidence.v1"}\n', encoding="utf-8")
        old_evidence_hash = field_pipeline_job._sha256(evidence_path)

        def publish_stale_health(*_args, **_kwargs):
            health = {
                "contract": "case_health.v1",
                "citation_status": "DESIGN_CITABLE",
                "evidence": {"sha256": old_evidence_hash},
                "errors": [{"code": "DESIGN_CITABLE"}],
            }
            health_path.write_text(json.dumps(health) + "\n", encoding="utf-8")
            evidence_path.write_bytes(evidence_path.read_bytes() + b" ")
            return health

        with mock.patch.object(
            field_pipeline_job.cfd_evidence, "build_case_evidence",
            return_value={"contract": "case_evidence.v1"},
        ), mock.patch.object(
            field_pipeline_job.cfd_evidence, "validate_case_evidence", return_value=[],
        ), mock.patch.object(
            field_pipeline_job.cfd_case_health, "build_case_health",
            side_effect=publish_stale_health,
        ), mock.patch.object(
            field_pipeline_job.cfd_case_health, "review_summary",
            return_value={"status": "APPROVED", "review_id": "review-" + "a" * 32},
        ):
            snapshot = field_pipeline_job._current_health_snapshot(self.root, case)

        self.assertEqual(snapshot["citation_status"], "CITATION_BLOCKED")
        self.assertNotIn("case_evidence_path", snapshot)
        self.assertNotIn("case_health_path", snapshot)

    def test_field_snapshot_rejects_citable_health_when_review_is_now_ambiguous(self):
        case = self.root / "_body_solver" / "review-race"
        case.mkdir(parents=True)
        evidence_path = case / "case_evidence.v1.json"
        health_path = case / "case_health.v1.json"
        evidence_path.write_text('{"contract":"case_evidence.v1"}\n', encoding="utf-8")
        health = {
            "contract": "case_health.v1",
            "citation_status": "DESIGN_CITABLE",
            "evidence": {
                "path": evidence_path.relative_to(self.root).as_posix(),
                "sha256": field_pipeline_job._sha256(evidence_path),
            },
            "errors": [{"code": "DESIGN_CITABLE"}],
        }
        health_path.write_text(json.dumps(health) + "\n", encoding="utf-8")

        with mock.patch.object(
            field_pipeline_job.cfd_evidence, "build_case_evidence",
            return_value={"contract": "case_evidence.v1"},
        ), mock.patch.object(
            field_pipeline_job.cfd_evidence, "validate_case_evidence", return_value=[],
        ), mock.patch.object(
            field_pipeline_job.cfd_case_health, "build_case_health", return_value=health,
        ), mock.patch.object(
            field_pipeline_job.cfd_case_health, "review_summary",
            return_value={"status": "AMBIGUOUS"},
        ):
            snapshot = field_pipeline_job._current_health_snapshot(self.root, case)

        self.assertEqual(snapshot["citation_status"], "CITATION_BLOCKED")
        self.assertNotIn("case_health_path", snapshot)

    def test_field_snapshot_rejects_health_mutation_during_review_summary(self):
        case = self.root / "_body_solver" / "late-health-race"
        case.mkdir(parents=True)
        evidence_path = case / "case_evidence.v1.json"
        health_path = case / "case_health.v1.json"
        evidence_path.write_text('{"contract":"case_evidence.v1"}\n', encoding="utf-8")
        health = {
            "contract": "case_health.v1",
            "citation_status": "DESIGN_CITABLE",
            "evidence": {
                "path": evidence_path.relative_to(self.root).as_posix(),
                "sha256": field_pipeline_job._sha256(evidence_path),
            },
            "errors": [{"code": "DESIGN_CITABLE"}],
        }
        health_path.write_text(json.dumps(health) + "\n", encoding="utf-8")

        def mutate_health_then_approve(*_args, **_kwargs):
            health_path.write_bytes(health_path.read_bytes() + b" ")
            return {"status": "APPROVED", "review_id": "review-" + "a" * 32}

        with mock.patch.object(
            field_pipeline_job.cfd_evidence, "build_case_evidence",
            return_value={"contract": "case_evidence.v1"},
        ), mock.patch.object(
            field_pipeline_job.cfd_evidence, "validate_case_evidence", return_value=[],
        ), mock.patch.object(
            field_pipeline_job.cfd_case_health, "build_case_health", return_value=health,
        ), mock.patch.object(
            field_pipeline_job.cfd_case_health, "review_summary",
            side_effect=mutate_health_then_approve,
        ):
            snapshot = field_pipeline_job._current_health_snapshot(self.root, case)

        self.assertEqual(snapshot["citation_status"], "CITATION_BLOCKED")
        self.assertNotIn("case_health_sha256", snapshot)

    def test_terminal_manifest_publish_serializes_cooperating_review_writer(self):
        paths = make_complete_case(Path(self.tmp.name) / "serialized", with_gci=True)
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
        paths["evidence"].write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        approved = cfd_review.create_review(
            paths["evidence"], projects_root=paths["root"],
            expected_target_sha256=cfd_review.sha256_file(paths["evidence"]),
            reviewer_id="reviewer", decision="APPROVED", reason="approved",
        )
        self.assertEqual(approved["decision"], "APPROVED")

        job_id = "field-" + "a" * 12
        manifest_path = field_pipeline_job._job_path(paths["root"], job_id)
        manifest_path.parent.mkdir(parents=True)
        field_pipeline_job.cfd_gci_job._atomic_json(manifest_path, {
            "schema_version": 1, "contract": "field_pipeline_job.v1",
            "engine": "body_fitted_field_pipeline", "created_at": "old",
            "updated_at": "old", "job": job_id,
            "status": "analysis_complete_not_citable", "stage": "complete",
            "attempts": 1, "error": "", "input": {}, "level": {},
            "result_case": str(paths["case"]),
        })
        real_publish = field_pipeline_job._publish
        real_history = cfd_review._history
        manifest_paused = threading.Event()
        release_manifest = threading.Event()
        writer_entered_history = threading.Event()
        paused_once = False

        def pause_manifest_publish(path, manifest, callback=None, message=""):
            nonlocal paused_once
            if Path(path) == manifest_path and not paused_once:
                paused_once = True
                manifest_paused.set()
                assert release_manifest.wait(5), "manifest publish release timed out"
            return real_publish(path, manifest, callback, message)

        def observe_history(*args, **kwargs):
            if threading.current_thread().name.startswith("field-review-writer"):
                writer_entered_history.set()
            return real_history(*args, **kwargs)

        def reject_current():
            return cfd_review.create_review(
                paths["evidence"], projects_root=paths["root"],
                expected_target_sha256=cfd_review.sha256_file(paths["evidence"]),
                reviewer_id="reviewer-2", decision="REJECTED", reason="rejected",
            )

        with mock.patch.object(
            field_pipeline_job.cfd_evidence, "build_case_evidence", return_value=evidence
        ), mock.patch.object(
            field_pipeline_job.cfd_evidence, "validate_case_evidence", return_value=[]
        ), mock.patch.object(
            field_pipeline_job, "_publish", side_effect=pause_manifest_publish
        ), mock.patch.object(
            cfd_review, "_history", side_effect=observe_history
        ), ThreadPoolExecutor(max_workers=1, thread_name_prefix="field-refresh") as field_pool, ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="field-review-writer"
        ) as writer_pool:
            field_future = field_pool.submit(
                field_pipeline_job.run_job, paths["root"], job_id
            )
            self.assertTrue(manifest_paused.wait(5), "field did not reach manifest publish")
            writer_future = writer_pool.submit(reject_current)
            writer_was_blocked = not writer_entered_history.wait(0.25)
            release_manifest.set()
            first = field_future.result(timeout=5)
            writer_future.result(timeout=5)
            second = field_pipeline_job.run_job(paths["root"], job_id)

        self.assertTrue(writer_was_blocked)
        self.assertEqual(first["manifest"]["status"], "complete")
        self.assertEqual(second["manifest"]["status"], "analysis_complete_not_citable")
        self.assertEqual(second["manifest"]["citation_status"], "CITATION_BLOCKED")

    def test_old_terminal_fixture_without_health_fields_still_loads_and_refreshes(self):
        created = field_pipeline_job.create_job(self.root, self.geometry)
        old = {
            "schema_version": 1, "contract": "field_pipeline_job.v1",
            "engine": "body_fitted_field_pipeline", "created_at": "old",
            "updated_at": "old", "job": created["job"], "status": "complete",
            "stage": "complete", "attempts": 1, "error": "", "input": {},
            "level": {}, "result_case": "",
        }
        field_pipeline_job.cfd_gci_job._atomic_json(created["manifest_path"], old)

        loaded = field_pipeline_job.load_job(self.root, created["job"])
        refreshed = field_pipeline_job.review_terminal_job_citation(self.root, loaded)

        self.assertEqual(refreshed["status"], "analysis_complete_not_citable")
        self.assertEqual(refreshed["citation_status"], "CITATION_BLOCKED")
        self.assertEqual(refreshed["stage"], "complete")

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
