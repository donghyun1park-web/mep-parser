import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import cfd_temporal_sensitivity as temporal
import cfd_numerics
import cfd_physics
import run_temporal_sensitivity as runner
from jsonschema import validate


class TemporalSensitivityContractTests(unittest.TestCase):
    @staticmethod
    def _sha256(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def _seed(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name) / "seed"
        (root / "0").mkdir(parents=True)
        (root / "system").mkdir()
        (root / "constant" / "polyMesh").mkdir(parents=True)
        (root / "0" / "U").write_text("uniform (0 0 0);\n", encoding="utf-8")
        (root / "system" / "controlDict").write_text(
            "adjustTimeStep no;\ndeltaT 0.02;\nmaxDeltaT 0.02;\nmaxCo 1;\n",
            encoding="utf-8",
        )
        (root / "thermal_input.json").write_text(json.dumps({
            "contract": "thermal_input.v1",
            "settings": {
                "thermal_initial_delta_t_s": 0.02,
                "thermal_max_delta_t_s": 0.02,
                "thermal_numerics_profile": "design_limited_second_order_v1",
                "thermal_continuation_profile": "design_limited_second_order_v1",
            },
        }), encoding="utf-8")
        return tmp, root

    def test_requires_three_fixed_levels_and_rejects_nonuniform_ratios(self):
        tmp, seed = self._seed()
        self.addCleanup(tmp.cleanup)
        with self.assertRaises(temporal.TemporalSensitivityInputError) as caught:
            temporal.create_temporal_study(seed, [0.04, 0.03, 0.01])
        self.assertIn("TEMPORAL_LEVELS_INVALID", str(caught.exception))

    def test_create_returns_pending_manifest_with_seed_hash_and_fixed_controller(self):
        tmp, seed = self._seed()
        self.addCleanup(tmp.cleanup)
        manifest = temporal.create_temporal_study(seed, [0.04, 0.02, 0.01])
        self.assertEqual(manifest["contract"], "temporal_sensitivity.v1")
        self.assertEqual(manifest["status"], "PENDING_SOLVER_EVIDENCE")
        self.assertEqual(manifest["fixed_delta_t_s"], [0.04, 0.02, 0.01])
        self.assertFalse(manifest["controller"]["adjust_time_step"])
        self.assertEqual(manifest["controller"]["max_co"], 1.0)
        self.assertEqual(len(manifest["children"]), 3)
        self.assertNotIn("qoi_comparisons", manifest)

    def test_run_never_promotes_pending_manifest_without_solver_evidence(self):
        tmp, seed = self._seed()
        self.addCleanup(tmp.cleanup)
        study = Path(tmp.name) / "study"
        study.mkdir()
        manifest = temporal.create_temporal_study(seed, [0.04, 0.02, 0.01])
        (study / "temporal_sensitivity.v1.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        result = runner.run_temporal_study(study)
        self.assertEqual(result["status"], "PENDING_SOLVER_EVIDENCE")
        self.assertFalse(result["valid"])
        self.assertIn("SOLVER_EXECUTION_PENDING", result["blockers"])

    def test_executor_rejects_external_fine_anchor_until_p5_3_binding(self):
        tmp, seed = self._seed()
        self.addCleanup(tmp.cleanup)
        study = Path(tmp.name) / "study"
        study.mkdir()
        external_fine = Path(tmp.name) / "external-fine"
        external_fine.mkdir()
        manifest = temporal.create_temporal_study(
            seed, [0.04, 0.02, 0.01], external_fine
        )
        (study / "temporal_sensitivity.v1.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

        with self.assertRaises(runner.TemporalSensitivityExecutionError) as caught:
            runner.run_temporal_study(study, execute=True)
        self.assertIn(
            "TEMPORAL_EXTERNAL_FINE_ANCHOR_REQUIRES_P5_3", str(caught.exception)
        )

    def test_cli_accepts_the_documented_fixed_step_and_selector_arguments(self):
        args = runner.build_parser().parse_args([
            "--mesh-case", "seed",
            "--study-root", "study",
            "--selector", "selector.json",
            "--fixed-delta-t", "0.04", "0.02", "0.01",
            "--courant-ceiling", "1.0",
        ])

        self.assertEqual(args.fixed_delta_t, [0.04, 0.02, 0.01])
        self.assertEqual(args.case_seed, Path("seed"))
        self.assertEqual(args.courant_ceiling, 1.0)
        self.assertEqual(args.selector, Path("selector.json"))

    def test_verify_rejects_seed_mutation_and_missing_evidence(self):
        tmp, seed = self._seed()
        self.addCleanup(tmp.cleanup)
        study = Path(tmp.name) / "study"
        study.mkdir()
        manifest = temporal.create_temporal_study(seed, [0.04, 0.02, 0.01])
        (study / "temporal_sensitivity.v1.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        seed.joinpath("0", "U").write_text("uniform (1 0 0);\n", encoding="utf-8")
        result = runner.verify_temporal_study(study, seed)
        self.assertEqual(result["status"], "NOT_EVALUATED")
        self.assertFalse(result["valid"])
        self.assertIn("TEMPORAL_SEED_HASH_MISMATCH", result["blockers"])

    def test_schema_accepts_pending_input_and_verified_result_contracts(self):
        schema = json.loads(
            (Path(__file__).resolve().parents[1]
             / "temporal_sensitivity.v1.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(schema["oneOf"]), 2)
        tmp, seed = self._seed()
        self.addCleanup(tmp.cleanup)
        validate(
            instance=temporal.create_temporal_study(seed, [0.04, 0.02, 0.01]),
            schema=schema,
        )

    def test_actual_time_history_is_derived_from_solver_times(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        log = Path(tmp.name) / "log.buoyant"
        log.write_text(
            "Time = 0.04\nTime = 0.08\nTime = 0.12\nEnd\n",
            encoding="utf-8",
        )

        history = temporal.verify_fixed_delta_t_history([log], 0.04)

        self.assertTrue(history["valid"])
        self.assertEqual(history["sample_count"], 3)
        self.assertAlmostEqual(history["minimum_delta_t_s"], 0.04)
        self.assertAlmostEqual(history["maximum_delta_t_s"], 0.04)
        self.assertEqual(history["source_logs"], [log.name])

    def test_actual_time_history_rejects_controller_or_limiter_intervention(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        log = Path(tmp.name) / "log.buoyant"
        log.write_text(
            "Time = 0.04\nTime = 0.08\nTime = 0.10\nEnd\n",
            encoding="utf-8",
        )

        history = temporal.verify_fixed_delta_t_history([log], 0.04)

        self.assertFalse(history["valid"])
        self.assertIn("TEMPORAL_ACTUAL_DELTA_T_MISMATCH", history["blockers"])

    def test_fixed_controller_is_reread_from_the_executed_case(self):
        tmp, seed = self._seed()
        self.addCleanup(tmp.cleanup)

        runner._verify_fixed_step_configuration(seed, 0.02)
        control = seed / "system" / "controlDict"
        control.write_text(
            control.read_text(encoding="utf-8").replace(
                "adjustTimeStep no;", "adjustTimeStep yes;"
            ),
            encoding="utf-8",
        )

        with self.assertRaises(runner.TemporalSensitivityExecutionError) as caught:
            runner._verify_fixed_step_configuration(seed, 0.02)
        self.assertIn("TEMPORAL_FIXED_CONTROLLER_INVALID", str(caught.exception))

    def test_generalized_richardson_accepts_monotonic_first_order_history(self):
        result = temporal.calculate_temporal_richardson(
            "exhaust_temperature_rise_k",
            coarse=11.0,
            medium=10.5,
            fine=10.25,
            fixed_delta_t_s=[0.04, 0.02, 0.01],
            near_zero_floor=1.0,
            relative_limit_pct=5.0,
            absolute_limit=0.5,
        )

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["convergence"], "monotonic")
        self.assertAlmostEqual(result["observed_order"], 1.0)
        self.assertAlmostEqual(result["safety_factor"], 1.25)
        self.assertAlmostEqual(result["uncertainty_fine"], 0.3125)
        self.assertLessEqual(result["uncertainty_fine_pct"], 5.0)

    def test_generalized_richardson_does_not_pass_nonmonotonic_history(self):
        result = temporal.calculate_temporal_richardson(
            "occupied_zone_mean_speed_m_s",
            coarse=0.20,
            medium=0.24,
            fine=0.22,
            fixed_delta_t_s=[0.04, 0.02, 0.01],
            near_zero_floor=0.1,
            relative_limit_pct=5.0,
            absolute_limit=0.05,
        )

        self.assertEqual(result["status"], "NOT_EVALUATED")
        self.assertEqual(result["convergence"], "non_monotonic")
        self.assertIsNone(result["observed_order"])

    def test_execution_prepares_and_runs_three_fixed_step_children_serially(self):
        tmp, seed = self._seed()
        self.addCleanup(tmp.cleanup)
        study = Path(tmp.name) / "study"
        study.mkdir()
        manifest = temporal.create_temporal_study(seed, [0.04, 0.02, 0.01])
        (study / "temporal_sensitivity.v1.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        calls = []

        def solve(case, delta_t_s, target, progress_cb=None):
            calls.append((case.name, delta_t_s, target))
            run = {
                "contract": "run_manifest.v1",
                "thermal_progress": {
                    "flow_through_fraction": 3.0,
                    "flow_through_time_s": 1.0,
                    "latest_time_s": 3.0,
                },
            }
            (case / "run_manifest.json").write_text(
                json.dumps(run), encoding="utf-8"
            )
            (case / "result_manifest.json").write_text(
                json.dumps({"contract": "result_manifest.v1", "time_s": 3.0}),
                encoding="utf-8",
            )
            count = round(3.0 / delta_t_s)
            (case / "log.buoyant").write_text(
                "".join(
                    f"Time = {index * delta_t_s:.12g}\n"
                    for index in range(1, count + 1)
                ) + "End\n",
                encoding="utf-8",
            )
            return {"ok": True, "manifest": run}

        with mock.patch.object(
                runner.cfd_gci_job, "acquire_solver_lock",
                return_value=("lock-token", {"pid": 123})), mock.patch.object(
                runner.cfd_gci_job, "release_solver_lock") as release, mock.patch.object(
                runner, "_run_case_to_target", side_effect=solve):
            result = runner.run_temporal_study(study, execute=True)

        self.assertEqual(calls, [
            ("coarse_dt_0p04", 0.04, 3.0),
            ("medium_dt_0p02", 0.02, 3.0),
            ("fine_dt_0p01", 0.01, 3.0),
        ])
        self.assertEqual(result["status"], "SOLVER_RUNS_COMPLETE")
        self.assertFalse(result["valid"])
        self.assertIn("INDEPENDENT_VERIFICATION_REQUIRED", result["blockers"])
        release.assert_called_once()
        for child, delta_t_s in zip(
                ("coarse_dt_0p04", "medium_dt_0p02", "fine_dt_0p01"),
                (0.04, 0.02, 0.01)):
            control = (study / child / "system" / "controlDict").read_text(
                encoding="utf-8"
            )
            self.assertIn("adjustTimeStep no;", control)
            self.assertIn(f"deltaT {delta_t_s:.12g};", control)
            self.assertIn(f"maxDeltaT {delta_t_s:.12g};", control)
            checkpoint = json.loads(
                (study / "temporal_sensitivity_execution.v1.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(checkpoint[child]["time_history"]["valid"])

    def test_verifier_rehashes_evidence_and_publishes_richardson_pass(self):
        tmp, seed = self._seed()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        geometry = root / "confirmed-geometry.json"
        zone = root / "confirmed-zone.json"
        geometry.write_text('{"unit":"m"}', encoding="utf-8")
        zone.write_text('{"closed":true}', encoding="utf-8")
        selector = {
            "contract": "occupied_volume_band.v1",
            "coordinate_source": "cell_center_m_agl",
            "z_min_agl_m": 0.1,
            "z_max_agl_m": 1.8,
            "validation_scope": "design_validation",
            "coordinate_system": "local_cartesian",
            "coordinate_unit": "m",
            "geometry_ref": {"path": str(geometry), "sha256": self._sha256(geometry)},
            "zone_ref": {"path": str(zone), "sha256": self._sha256(zone)},
            "xy_polygon_m": [
                [0.0, 0.0], [4.0, 0.0], [4.0, 3.0], [0.0, 3.0], [0.0, 0.0]
            ],
            "exclusion_polygons_m": [],
            "exclusion_volumes": [],
            "confirmation": {
                "reviewer": "mechanical-reviewer",
                "confirmed_at": "2026-08-28T09:00:00+09:00",
                "selection_reason": "Closed test zone.",
                "closed_zone_verified": True,
                "multilevel_voids_accounted": True,
            },
        }
        (seed / "0" / "T").write_text("uniform 293.15;\n", encoding="utf-8")
        (seed / "constant" / "polyMesh" / "points").write_text(
            "shared mesh\n", encoding="utf-8"
        )
        mesh = {"mesh": {"max_non_orthogonality": 10.0}}
        (seed / "mesh_manifest.json").write_text(json.dumps(mesh), encoding="utf-8")
        (seed / "surface_manifest.json").write_text("{}", encoding="utf-8")
        numerics = cfd_numerics.thermal_numerics_contract(
            mesh,
            {**cfd_physics.DEFAULT_SETTINGS,
             "thermal_numerics_profile": "design_limited_second_order_v1"},
        )
        (seed / "system" / "fvSchemes").write_text(
            cfd_physics._thermal_fv_schemes(numerics), encoding="utf-8"
        )
        (seed / "system" / "fvSolution").write_text(
            cfd_physics._thermal_fv_solution(cfd_physics.DEFAULT_SETTINGS, numerics),
            encoding="utf-8",
        )
        study = root / "study"
        study.mkdir()
        manifest = temporal.create_temporal_study(
            seed, [0.04, 0.02, 0.01], selector=selector
        )
        (study / "temporal_sensitivity.v1.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

        def solve(case, delta_t_s, target, progress_cb=None):
            settings = dict(cfd_physics.DEFAULT_SETTINGS)
            settings.update({
                "thermal_numerics_profile": "design_limited_second_order_v1",
                "thermal_initial_delta_t_s": delta_t_s,
                "thermal_max_delta_t_s": delta_t_s,
                "thermal_design_max_courant_gate": 1.0,
                "terminal_phi_imbalance_max": 0.001,
                "occupied_floor_elevation_m": 0.0,
            })
            residual_rows = {
                name: {"final": 1e-6}
                for name in cfd_numerics.THERMAL_RESIDUAL_LIMITS
            }
            histories = {
                name: ([{"final": 1e-6}]
                       * cfd_numerics.DEFAULT_RESIDUAL_TAIL_SAMPLES)
                for name in cfd_numerics.THERMAL_RESIDUAL_LIMITS
            }
            run = {
                "contract": "run_manifest.v1",
                "status": "PASS",
                "effective_numerics": numerics,
                "effective_settings": settings,
                "thermal_progress": {
                    "flow_through_fraction": 3.0,
                    "flow_through_time_s": 1.0,
                    "latest_time_s": 3.0,
                },
                "solver": {
                    "ended": True,
                    "fatal": False,
                    "courant": {"peak_maximum": 0.7},
                    "thermal_residuals": residual_rows,
                    "thermal_residual_history": histories,
                    "continuity": {"global": 1e-7},
                },
                "flux_balance": {"available": True, "imbalance_ratio": 0.0005},
                "thermal": {
                    "energy_closure_basis": (
                        "solver_positive_phi_and_owner_cell_temperature"
                    )
                },
            }
            run_path = case / "run_manifest.json"
            run_path.write_text(json.dumps(run), encoding="utf-8")
            source = case / "VTK" / "final" / "internal.vtu"
            source.parent.mkdir(parents=True)
            source.write_text(f"vtu:{delta_t_s}\n", encoding="utf-8")
            summary = case / "results" / "body_fitted_summary.json"
            summary.parent.mkdir(parents=True)
            summary.write_text(json.dumps({"time_s": 3.0}), encoding="utf-8")
            result = {
                "contract": "result_manifest.v1",
                "time_s": 3.0,
                "source": {
                    "path": source.relative_to(case).as_posix(),
                    "sha256": self._sha256(source),
                },
                "run_manifest_sha256": self._sha256(run_path),
                "mesh_manifest_sha256": self._sha256(case / "mesh_manifest.json"),
                "summary_path": summary.relative_to(case).as_posix(),
                "summary_sha256": self._sha256(summary),
                "slices": [],
            }
            (case / "result_manifest.json").write_text(
                json.dumps(result), encoding="utf-8"
            )
            count = round(3.0 / delta_t_s)
            (case / "log.buoyant").write_text(
                "".join(
                    f"Time = {index * delta_t_s:.12g}\n"
                    for index in range(1, count + 1)
                ) + "End\n",
                encoding="utf-8",
            )
            return {"ok": True, "manifest": run}

        with mock.patch.object(
                runner.cfd_gci_job, "acquire_solver_lock",
                return_value=("lock-token", {"pid": 123})), mock.patch.object(
                runner.cfd_gci_job, "release_solver_lock"), mock.patch.object(
                runner, "_run_case_to_target", side_effect=solve):
            runner.run_temporal_study(study, execute=True)

        occupied = [
            {"occupied_zone_mean_temperature_k": 295.0,
             "occupied_zone_mean_speed_m_s": 0.22},
            {"occupied_zone_mean_temperature_k": 294.5,
             "occupied_zone_mean_speed_m_s": 0.21},
            {"occupied_zone_mean_temperature_k": 294.25,
             "occupied_zone_mean_speed_m_s": 0.205},
        ]
        exhaust = [
            {"exhaust_temperature_rise_k": 5.4},
            {"exhaust_temperature_rise_k": 5.2},
            {"exhaust_temperature_rise_k": 5.1},
        ]
        with mock.patch.object(
                runner.cfd_post, "compute_time_weighted_occupied_volume_qois",
                side_effect=occupied), mock.patch.object(
                runner.cfd_post,
                "read_time_weighted_exhaust_temperature_rise_from_case",
                side_effect=exhaust):
            result = runner.verify_temporal_study(
                study, study / "fine_dt_0p01"
            )

        self.assertEqual(result["status"], "PASS", result)
        self.assertTrue(result["valid"])
        self.assertTrue((study / "temporal_sensitivity_result.v1.json").is_file())
        self.assertTrue(
            (study / "fine_dt_0p01" / "temporal_sensitivity.json").is_file()
        )
        self.assertTrue(all(
            row["status"] == "PASS" for row in result["qoi_convergence"]
        ))
        plot = study / result["verification"]["plot_path"]
        self.assertTrue(plot.is_file())
        self.assertEqual(self._sha256(plot), result["verification"]["plot_sha256"])
        self.assertIn("<svg", plot.read_text(encoding="utf-8"))
        schema = json.loads(
            (Path(__file__).resolve().parents[1]
             / "temporal_sensitivity.v1.schema.json").read_text(encoding="utf-8")
        )
        validate(
            instance=json.loads(
                (study / "temporal_sensitivity_result.v1.json").read_text(
                    encoding="utf-8"
                )
            ),
            schema=schema,
        )


if __name__ == "__main__":
    unittest.main()
