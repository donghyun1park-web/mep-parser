import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import cfd_gci_job


class GCIJobTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[1]
        self.tmp = tempfile.TemporaryDirectory(prefix=".test-gci-job-", dir=self.repo)
        self.root = Path(self.tmp.name)
        self.geometry = self.root / "room.geometry.json"
        self.geometry.write_text(json.dumps({"contract": "geometry.v2"}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_is_deterministic_and_defaults_to_four_level_v3(self):
        first = cfd_gci_job.create_study(self.root, self.geometry)
        second = cfd_gci_job.create_study(self.root, self.geometry)
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["study"], second["study"])
        self.assertFalse(first["existing"])
        self.assertTrue(second["existing"])
        self.assertEqual(
            [row["name"] for row in first["manifest"]["levels"]],
            ["very_coarse", "coarse", "medium", "fine"],
        )
        self.assertEqual(
            [row["background_cell_m"] for row in first["manifest"]["levels"]],
            [0.504, 0.35, 0.243, 0.169],
        )
        self.assertEqual(
            first["manifest"]["input"]["gci_contract"], "grid_convergence.v3"
        )
        self.assertEqual(
            first["manifest"]["input"]["thermal_settings"][
                "thermal_minimum_flow_through_fraction"
            ], 3.0,
        )
        self.assertEqual(
            first["manifest"]["input"]["thermal_settings"][
                "thermal_max_single_run_s"
            ], 20.0,
        )
        self.assertEqual(
            first["manifest"]["input"]["thermal_settings"][
                "thermal_continuation_write_interval_s"
            ], 2.0,
        )
        rejected = cfd_gci_job.create_study(
            self.root, self.geometry,
            {"mesh_widths_m": [0.4, 0.39, 0.38, 0.37]},
        )
        self.assertFalse(rejected["ok"])
        self.assertIn("1.10", rejected["error"])

    def test_live_progress_is_bounded_to_next_saved_checkpoint(self):
        case = self.root / "_body_solver" / "fine-thermal"
        case.mkdir(parents=True)
        (case / "run_manifest.json").write_text(json.dumps({
            "thermal_progress": {
                "latest_time_s": 100.0,
                "flow_through_time_s": 100.0,
                "required_duration_s": 300.0,
                "recommended_next_duration_s": 20.0,
                "last_solver_runtime_per_simulated_second": 10.0,
                "last_fixed_runtime_overhead_seconds": 0.0,
                "estimated_remaining_runtime_seconds": 2000.0,
            },
        }), encoding="utf-8")
        updated = datetime(2026, 7, 22, tzinfo=timezone.utc)
        job = {
            "stage": "fine:thermal_continue",
            "updated_at": (updated + timedelta(seconds=100)).isoformat(),
            "levels": [{
                "name": "fine", "latest_time_s": 100.0,
                "thermal_case": str(case),
                "stage_started_at": updated.isoformat(),
            }],
        }
        live = cfd_gci_job.bounded_live_progress(
            job, self.root, now=updated + timedelta(seconds=110)
        )
        self.assertAlmostEqual(live["estimated_time_s"], 111.0)
        self.assertEqual(live["next_checkpoint_time_s"], 120.0)
        capped = cfd_gci_job.bounded_live_progress(
            job, self.root, now=updated + timedelta(seconds=1000)
        )
        self.assertEqual(capped["estimated_time_s"], 120.0)
        self.assertIsNone(cfd_gci_job.bounded_live_progress({
            **job,
            "levels": [{**job["levels"][0],
                        "thermal_case": str(self.root.parent / "outside")}],
        }, self.root))
        manifest_path = case / "run_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["thermal_progress"]["estimate_status"] = (
            "awaiting_continuation_sample"
        )
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertIsNone(cfd_gci_job.bounded_live_progress(job, self.root,
                                                            now=updated))

        manifest["thermal_progress"].update({
            "checkpoint_rate_seconds_per_simulated_second": 20.0,
            "recommended_next_duration_s": 5.0,
            "estimated_remaining_runtime_seconds": None,
        })
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        initial = cfd_gci_job.bounded_live_progress(
            job, self.root, now=updated + timedelta(seconds=40)
        )
        self.assertAlmostEqual(initial["estimated_time_s"], 102.0)
        self.assertEqual(initial["next_checkpoint_time_s"], 105.0)
        self.assertEqual(initial["estimate_basis"], "initial_stability_scaled")
        self.assertIsNone(initial["estimated_remaining_runtime_seconds"])

    def test_solver_line_callback_persists_throttled_job_heartbeat(self):
        job_path = self.root / "_body_gci" / "gci-aabbccddeeff" / "gci_job.json"
        level = {"name": "fine", "stage": "isothermal_run", "status": "running"}
        manifest = {
            "study": "gci-aabbccddeeff",
            "stage": "fine:isothermal_run",
            "levels": [level],
        }
        messages = []
        emit = cfd_gci_job._line_callback(
            messages.append, manifest, level, "isothermal_run", job_path,
            heartbeat_interval_s=60.0,
        )

        emit("Time = 120")
        emit("Time = 121")

        saved = json.loads(job_path.read_text(encoding="utf-8"))
        self.assertEqual(
            saved["levels"][0]["live_message"], "등온 계산 반복 120"
        )
        self.assertIn("live_updated_at", saved["levels"][0])
        self.assertEqual(len(messages), 2)
        cfd_gci_job._set_level(manifest, level, "thermal_build")
        self.assertNotIn("live_message", level)
        self.assertNotIn("live_updated_at", level)
        self.assertIn("stage_started_at", level)

    def test_screening_warn_at_target_flow_returns_raw_analysis_case(self):
        """A finished 3 FTT WARN run is viewable before citation evidence exists."""
        mesh_case = self.root / "mesh"
        iso_case = self.root / "isothermal"
        thermal_case = self.root / "_body_solver" / "field-design-thermal"
        (mesh_case / "constant" / "polyMesh").mkdir(parents=True)
        (mesh_case / "mesh_manifest.json").write_text(
            json.dumps({"status": "PASS", "mesh": {"cells": 12000}}),
            encoding="utf-8",
        )
        (iso_case / "1").mkdir(parents=True)
        (iso_case / "run_manifest.json").write_text(
            json.dumps({"status": "WARN"}), encoding="utf-8"
        )
        thermal_case.mkdir(parents=True)
        (thermal_case / "result_manifest.json").write_text("{}", encoding="utf-8")
        (thermal_case / "run_manifest.json").write_text(json.dumps({
            "status": "WARN",
            "design_ready": False,
            "errors": [],
            "warnings": ["NUMERICS_SCREENING_ONLY"],
            "solver": {"fatal": False},
            "thermal_progress": {
                "latest_time_s": 300.0,
                "flow_through_time_s": 100.0,
                "flow_through_fraction": 3.0,
                "remaining_duration_s": 0.0,
                "recommended_next_duration_s": 0.0,
            },
        }), encoding="utf-8")
        manifest = {
            "study": "field-candidate",
            "stage": "starting",
            "input": {"thermal_settings": {
                "thermal_minimum_flow_through_fraction": 3.0,
            }},
        }
        level = {
            "name": "design", "background_cell_m": 0.35,
            "mesh_case": str(mesh_case), "isothermal_case": str(iso_case),
        }

        with mock.patch.object(
            cfd_gci_job.cfd_report, "generate_body_fitted_report",
            return_value={"ok": True},
        ):
            completed = cfd_gci_job.run_thermal_design_level(
                self.root, self.root / "occ", manifest, level,
                self.root / "field_job.json", case_prefix="field-design",
            )

        self.assertEqual(completed, thermal_case)
        self.assertEqual(level["status"], "WARN")
        self.assertEqual(level["stage"], "complete")
        self.assertEqual(level["flow_through_fraction"], 3.0)

    def test_failed_thermal_run_is_not_raw_analysis_complete(self):
        case = self.root / "failed-thermal"
        case.mkdir()
        (case / "result_manifest.json").write_text("{}", encoding="utf-8")

        completed = cfd_gci_job._raw_thermal_analysis_complete(
            case,
            {
                "status": "FAIL", "errors": ["THERMAL_SOLVER_FAILED"],
                "solver": {"fatal": True},
                "thermal_progress": {"flow_through_fraction": 3.0},
            },
            3.0,
        )

        self.assertFalse(completed)

    def test_field_validator_rejects_partial_result_below_three_ftt(self):
        """A resumed field job must continue a 0.25 FTT result, not reuse it."""
        case = self.root / "partial-field-thermal"
        case.mkdir()
        (case / "result_manifest.json").write_text("{}", encoding="utf-8")
        (case / "run_manifest.json").write_text(json.dumps({
            "status": "WARN",
            "design_ready": False,
            "errors": [],
            "solver": {"fatal": False},
            "thermal_progress": {
                "latest_time_s": 25.0,
                "flow_through_fraction": 0.25,
            },
        }), encoding="utf-8")
        level = {
            "name": "design", "status": "pending", "stage": "pending",
            "thermal_case": str(case),
        }

        with mock.patch.object(
            cfd_gci_job.cfd_gci, "load_body_fitted_case",
            return_value={"cell_count": 12000, "time_s": 25.0},
        ):
            completed = cfd_gci_job.validate_completed_design_level(level)

        self.assertIsNone(completed)
        self.assertEqual(level["status"], "pending")

    def _run_mocks(self):
        def occ_side_effect(geometry, output):
            output = Path(output)
            output.mkdir(parents=True, exist_ok=True)
            (output / "surface_manifest.json").write_text("{}", encoding="utf-8")
            return {"ok": True, "output": str(output)}

        def mesh_build(occ, case, settings=None):
            Path(case).mkdir(parents=True, exist_ok=True)
            return {"ok": True}

        def mesh_run(case, progress_cb=None):
            width_name = Path(case).name.rsplit("-", 1)[-1]
            cells = {"very_coarse": 2000, "coarse": 4000,
                     "medium": 8000, "fine": 16000}[width_name]
            manifest = {"status": "PASS", "mesh": {"cells": cells}}
            (Path(case) / "constant" / "polyMesh").mkdir(parents=True, exist_ok=True)
            (Path(case) / "mesh_manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            return {"ok": True, "manifest": manifest}

        def iso_build(mesh, case, settings=None):
            Path(case).mkdir(parents=True, exist_ok=True)
            return {"ok": True}

        def iso_run(case, progress_cb=None):
            manifest = {"status": "WARN", "engine": "body_fitted_isothermal_rans"}
            (Path(case) / "30").mkdir(parents=True, exist_ok=True)
            (Path(case) / "run_manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            return {"ok": True, "manifest": manifest}

        def thermal_build(mesh, case, settings=None, initial_case_dir=None):
            Path(case).mkdir(parents=True, exist_ok=True)
            return {"ok": True}

        def initial_run(case, progress_cb=None):
            manifest = {
                "status": "WARN", "design_ready": False,
                "thermal_progress": {
                    "latest_time_s": 0.05, "remaining_duration_s": 1.0,
                    "recommended_next_duration_s": 1.0,
                    "flow_through_fraction": 0.01,
                },
            }
            (Path(case) / "run_manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            return {"ok": True, "manifest": manifest}

        def continue_run(case, settings=None, progress_cb=None):
            manifest = {
                "status": "PASS", "design_ready": True,
                "thermal_progress": {
                    "latest_time_s": 1.05, "remaining_duration_s": 0.0,
                    "recommended_next_duration_s": 0.0,
                    "flow_through_fraction": 3.0,
                },
            }
            case = Path(case)
            (case / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (case / "result_manifest.json").write_text("{}", encoding="utf-8")
            return {"ok": True, "manifest": manifest}

        gate = {"contract": "grid_convergence.v1", "status": "PASS"}
        stack = mock.patch.multiple(
            cfd_gci_job.cfd_occ,
            run_occ_job=mock.Mock(side_effect=occ_side_effect),
            inspect_occ_output=mock.Mock(
                side_effect=lambda output: {
                    "ok": (Path(output) / "surface_manifest.json").is_file(),
                    "manifest": {},
                }
            ),
        )
        patches = [
            stack,
            mock.patch.object(cfd_gci_job.cfd_mesh, "build_mesh_case", side_effect=mesh_build),
            mock.patch.object(cfd_gci_job.cfd_mesh, "run_mesh_case", side_effect=mesh_run),
            mock.patch.object(cfd_gci_job.cfd_physics, "build_isothermal_case", side_effect=iso_build),
            mock.patch.object(cfd_gci_job.cfd_physics, "run_isothermal_case", side_effect=iso_run),
            mock.patch.object(cfd_gci_job.cfd_physics, "build_buoyant_case", side_effect=thermal_build),
            mock.patch.object(cfd_gci_job.cfd_physics, "run_buoyant_case", side_effect=initial_run),
            mock.patch.object(cfd_gci_job.cfd_physics, "run_buoyant_continuation", side_effect=continue_run),
            mock.patch.object(cfd_gci_job.cfd_gci, "build_grid_convergence",
                              return_value={"ok": True, "manifest": gate}),
            mock.patch.object(cfd_gci_job.cfd_report, "generate_body_fitted_report",
                              return_value={"ok": True}),
            mock.patch.object(cfd_gci_job.cfd_report, "generate_gci_report",
                              side_effect=lambda path: {
                                  "ok": True, "path": str(Path(path) / "gci_report.html")
                              }),
            mock.patch.object(cfd_gci_job.cfd_mesh, "estimate_resources",
                              return_value={
                                  "background_cells": 100,
                                  "estimated_cells": 1000,
                                  "estimated_ram_gb": 0.1,
                                  "estimated_disk_gb": 0.1,
                              }),
        ]
        return patches

    def test_run_study_completes_all_levels_and_can_resume_without_rerunning(self):
        created = cfd_gci_job.create_study(self.root, self.geometry)
        patches = self._run_mocks()
        entered = [item.start() for item in patches]
        try:
            messages = []
            result = cfd_gci_job.run_study(
                self.root, created["study"], callback=messages.append
            )
        finally:
            for item in reversed(patches):
                item.stop()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["manifest"]["status"], "complete")
        self.assertEqual(result["manifest"]["gate_status"], "PASS")
        self.assertTrue(all(row["status"] == "PASS"
                            for row in result["manifest"]["levels"]))
        self.assertEqual(len(entered[2].call_args_list), 4)  # run_mesh_case
        self.assertEqual(len(entered[7].call_args_list), 4)  # continuation
        self.assertEqual(
            entered[8].call_args.kwargs["contract"], "grid_convergence.v3"
        )
        self.assertTrue(any(row["stage"] == "gci" for row in messages))

        with mock.patch.object(cfd_gci_job.cfd_occ, "run_occ_job") as occ, \
             mock.patch.object(cfd_gci_job.cfd_occ, "inspect_occ_output",
                               return_value={"ok": True, "manifest": {}}), \
             mock.patch.object(cfd_gci_job.cfd_mesh, "estimate_resources",
                               return_value={}), \
             mock.patch.object(cfd_gci_job.cfd_mesh, "build_mesh_case") as mesh, \
             mock.patch.object(cfd_gci_job.cfd_physics, "build_isothermal_case") as iso, \
             mock.patch.object(cfd_gci_job.cfd_physics, "build_buoyant_case") as thermal, \
             mock.patch.object(cfd_gci_job.cfd_gci, "build_grid_convergence",
                               return_value={"ok": True, "manifest": {
                                   "contract": "grid_convergence.v1", "status": "PASS"
                               }}), \
             mock.patch.object(cfd_gci_job.cfd_report, "generate_body_fitted_report",
                               return_value={"ok": True}), \
             mock.patch.object(cfd_gci_job.cfd_report, "generate_gci_report",
                               return_value={"ok": True, "path": "gci_report.html"}):
            resumed = cfd_gci_job.run_study(self.root, created["study"])
        self.assertTrue(resumed["ok"], resumed)
        occ.assert_not_called()
        mesh.assert_not_called()
        iso.assert_not_called()
        thermal.assert_not_called()

    def test_schema_contract_is_available(self):
        schema = json.loads((self.repo / "gci_job.v1.schema.json").read_text(
            encoding="utf-8"
        ))
        self.assertEqual(schema["properties"]["contract"]["const"], "gci_job.v1")

    def test_refined_followup_reuses_shared_completed_widths(self):
        original = cfd_gci_job.create_study(
            self.root, self.geometry, {
                "gci_contract": "grid_convergence.v3",
                "mesh_widths_m": [0.7, 0.504, 0.35, 0.243],
                "level_mesh_settings": {},
            }
        )
        patches = self._run_mocks()
        [item.start() for item in patches]
        try:
            first = cfd_gci_job.run_study(self.root, original["study"])
        finally:
            for item in reversed(patches):
                item.stop()
        self.assertTrue(first["ok"], first)

        refined = cfd_gci_job.create_study(
            self.root, self.geometry, {"level_mesh_settings": {}}
        )
        patches = self._run_mocks()
        entered = [item.start() for item in patches]
        try:
            with mock.patch.object(
                cfd_gci_job.cfd_gci,
                "load_body_fitted_case",
                side_effect=lambda case: {
                    "cell_count": 8000 if "medium" in Path(case).name else 16000,
                    "time_s": 1.05,
                },
            ):
                second = cfd_gci_job.run_study(self.root, refined["study"])
        finally:
            for item in reversed(patches):
                item.stop()
        self.assertTrue(second["ok"], second)
        self.assertEqual(len(entered[2].call_args_list), 1)  # only new fine mesh
        levels = second["manifest"]["levels"]
        self.assertTrue(all(row["reused_from_study"] == original["study"]
                            for row in levels[:3]))
        self.assertNotIn("reused_from_study", levels[3])

    def test_followup_does_not_reuse_width_with_different_level_mesh_controls(self):
        source = cfd_gci_job.create_study(
            self.root, self.geometry, {
                "mesh_widths_m": [0.7, 0.504, 0.35, 0.243],
                "level_mesh_settings": {"coarse": {"terminal_level": 2}},
            },
        )
        saved = source["manifest"]["levels"][1]
        saved.update({
            "status": "PASS", "stage": "complete",
            "mesh_case": str(self.root / "source-mesh"),
            "isothermal_case": str(self.root / "source-iso"),
            "thermal_case": str(self.root / "source-thermal"),
            "cell_count": 8000, "latest_time_s": 60.0,
            "flow_through_fraction": 3.0,
        })
        cfd_gci_job._atomic_json(Path(source["manifest_path"]), source["manifest"])
        target = cfd_gci_job.create_study(
            self.root, self.geometry, {
                "level_mesh_settings": {"very_coarse": {"terminal_level": 3}},
            },
        )

        with mock.patch.object(
            cfd_gci_job, "_validate_completed_level",
            return_value=self.root / "source-thermal",
        ):
            reused = cfd_gci_job._reuse_compatible_level(
                self.root, target["manifest"], target["manifest"]["levels"][0]
            )

        self.assertIsNone(reused)

    def test_v2_can_seed_from_shorter_legacy_result(self):
        legacy = cfd_gci_job.create_study(
            self.root, self.geometry, {
                "gci_contract": "grid_convergence.v1",
                "mesh_widths_m": [0.35, 0.292, 0.243],
            },
        )
        source = legacy["manifest"]
        source["status"] = "complete"
        saved = source["levels"][0]
        saved.update({
            "status": "PASS", "stage": "complete",
            "mesh_case": str(self.root / "legacy-mesh"),
            "isothermal_case": str(self.root / "legacy-iso"),
            "thermal_case": str(self.root / "legacy-thermal"),
            "cell_count": 8000, "latest_time_s": 60.0,
            "flow_through_fraction": 0.25,
        })
        cfd_gci_job._atomic_json(Path(legacy["manifest_path"]), source)

        target = cfd_gci_job.create_study(
            self.root, self.geometry, {"gci_contract": "grid_convergence.v2"}
        )
        target_level = target["manifest"]["levels"][0]
        with mock.patch.object(
            cfd_gci_job, "_validate_completed_level",
            side_effect=lambda row, **_: Path(row["thermal_case"])
            if row.get("thermal_case") else None,
        ):
            seeded = cfd_gci_job._seed_compatible_level(
                self.root, target["manifest"], target_level
            )
        self.assertTrue(seeded)
        self.assertEqual(target_level["seeded_from_study"], legacy["study"])
        self.assertEqual(target_level["seeded_flow_through_fraction"], 0.25)

    def test_changed_geometry_fails_without_starting_occ_and_is_persisted(self):
        created = cfd_gci_job.create_study(self.root, self.geometry)
        self.geometry.write_text(json.dumps({"contract": "geometry.v2", "changed": True}),
                                 encoding="utf-8")
        with mock.patch.object(cfd_gci_job.cfd_occ, "run_occ_job") as occ:
            result = cfd_gci_job.run_study(self.root, created["study"])
        self.assertFalse(result["ok"])
        self.assertIn("변경", result["error"])
        occ.assert_not_called()

    def test_interrupted_study_records_resume_checkpoint_evidence(self):
        created = cfd_gci_job.create_study(self.root, self.geometry)
        manifest = created["manifest"]
        manifest.update(status="running", stage="coarse:thermal_continue",
                        attempts=1)
        manifest["levels"][0].update(
            status="PASS", stage="complete", latest_time_s=710.6832
        )
        manifest["levels"][1]["latest_time_s"] = 361.8944
        cfd_gci_job._atomic_json(Path(created["manifest_path"]), manifest)
        self.geometry.write_text(
            json.dumps({"contract": "geometry.v2", "changed": True}),
            encoding="utf-8",
        )

        result = cfd_gci_job.run_study(self.root, created["study"])

        self.assertFalse(result["ok"])
        resumed = result["manifest"]["resume_history"][0]
        self.assertEqual(resumed["previous_status"], "running")
        self.assertEqual(resumed["previous_stage"], "coarse:thermal_continue")
        self.assertEqual(resumed["previous_attempt"], 1)
        self.assertEqual(resumed["completed_levels"], ["very_coarse"])
        self.assertEqual(resumed["checkpoint_times_s"]["coarse"], 361.8944)
        saved = cfd_gci_job.load_study(self.root, created["study"])
        self.assertEqual(saved["status"], "FAIL")

    def test_live_process_lock_rejects_duplicate_runner(self):
        created = cfd_gci_job.create_study(self.root, self.geometry)
        job_path = Path(created["manifest_path"])
        token, owner = cfd_gci_job._acquire_run_lock(job_path)
        self.assertIsNotNone(token)
        try:
            result = cfd_gci_job.run_study(self.root, created["study"])
        finally:
            cfd_gci_job._release_run_lock(job_path, token)

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "GCI_JOB_ALREADY_RUNNING")
        self.assertEqual(result["lock"]["pid"], owner["pid"])
        self.assertEqual(
            cfd_gci_job.load_study(self.root, created["study"])["attempts"], 0
        )

    def test_stale_process_lock_is_reclaimed_and_released(self):
        created = cfd_gci_job.create_study(self.root, self.geometry)
        lock_path = Path(created["manifest_path"]).with_name("gci_job.lock")
        lock_path.write_text(
            json.dumps({"pid": 999999999, "token": "stale"}), encoding="utf-8"
        )
        completed = {"ok": True, "study": created["study"]}
        with (
            mock.patch.object(cfd_gci_job, "_pid_is_alive", return_value=False),
            mock.patch.object(
                cfd_gci_job, "_run_study_unlocked", return_value=completed
            ) as run,
        ):
            result = cfd_gci_job.run_study(self.root, created["study"])

        self.assertEqual(result, completed)
        run.assert_called_once()
        self.assertFalse(lock_path.exists())

    @unittest.skipUnless(sys.platform == "win32", "Windows process-handle semantics")
    def test_exited_process_with_open_handle_is_not_alive(self):
        process = subprocess.Popen([sys.executable, "-c", "raise SystemExit(7)"])
        process.wait(timeout=10)
        try:
            self.assertFalse(cfd_gci_job._pid_is_alive(process.pid))
        finally:
            process._handle.Close()

    def test_live_field_job_blocks_gci_solver_overlap(self):
        created = cfd_gci_job.create_study(self.root, self.geometry)
        field_manifest = (
            self.root / "_field_jobs" / "field-123456789abc"
            / "field_pipeline_job.json"
        )
        field_manifest.parent.mkdir(parents=True)
        field_manifest.write_text("{}", encoding="utf-8")
        token, owner = cfd_gci_job.acquire_job_lock(field_manifest)
        self.assertIsNotNone(token)
        try:
            result = cfd_gci_job.run_study(self.root, created["study"])
        finally:
            cfd_gci_job.release_job_lock(field_manifest, token)

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "CFD_SOLVER_BUSY")
        self.assertEqual(result["lock"]["pid"], owner["pid"])
        self.assertEqual(cfd_gci_job.load_study(
            self.root, created["study"]
        )["attempts"], 0)

    def test_global_solver_lock_is_released_when_gci_cannot_claim_it(self):
        created = cfd_gci_job.create_study(self.root, self.geometry)
        solver_token, owner = cfd_gci_job.acquire_solver_lock(self.root)
        self.assertIsNotNone(solver_token)
        try:
            result = cfd_gci_job.run_study(self.root, created["study"])
        finally:
            cfd_gci_job.release_solver_lock(self.root, solver_token)

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "CFD_SOLVER_BUSY")
        self.assertEqual(result["lock"]["pid"], owner["pid"])
        self.assertFalse(
            Path(created["manifest_path"]).with_name("gci_job.lock").exists()
        )

    def test_live_legacy_gci_job_blocks_a_different_study(self):
        first = cfd_gci_job.create_study(self.root, self.geometry)
        second = cfd_gci_job.create_study(
            self.root, self.geometry,
            {"mesh_widths_m": [0.55, 0.38, 0.26, 0.18]},
        )
        first_path = Path(first["manifest_path"])
        token, owner = cfd_gci_job.acquire_job_lock(first_path)
        self.assertIsNotNone(token)
        try:
            result = cfd_gci_job.run_study(self.root, second["study"])
        finally:
            cfd_gci_job.release_job_lock(first_path, token)

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "CFD_SOLVER_BUSY")
        self.assertEqual(result["lock"]["pid"], owner["pid"])
        self.assertEqual(cfd_gci_job.load_study(
            self.root, second["study"]
        )["attempts"], 0)


if __name__ == "__main__":
    unittest.main()
