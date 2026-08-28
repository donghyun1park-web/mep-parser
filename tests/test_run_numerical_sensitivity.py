import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import run_numerical_sensitivity as execution
import cfd_numerical_sensitivity_job as sensitivity_job
import cfd_numerical_sensitivity_runner as preparation
import cfd_numerics
import cfd_physics
from jsonschema import validate


class SerialSensitivityExecutionContractTests(unittest.TestCase):
    @staticmethod
    def _sha256(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def _seed_case(self, case, role, profile):
        source = case / "frozen-input.txt"
        source.write_text(f"{role}:{profile}\n", encoding="utf-8")
        snapshot = {
            "contract": "case_seed_snapshot.v1",
            "role": role,
            "case_child": case.name,
            "profile": profile,
            "serial_required": True,
            "requested_ranks": 1,
            "initialisation": "zero_flow",
            "entries": [{"path": source.name, "sha256": self._sha256(source)}],
        }
        snapshot["case_seed_snapshot_sha256"] = sensitivity_job.canonical_sha256(
            snapshot
        )
        (case / "case_seed_snapshot.v1.json").write_text(
            json.dumps(snapshot), encoding="utf-8"
        )
        return snapshot["case_seed_snapshot_sha256"]

    def _study(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        (root / "baseline_first_order").mkdir()
        (root / "variant_second_order").mkdir()
        geometry = root / "confirmed-geometry.json"
        zone = root / "confirmed-closed-zone.json"
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
        required_paths = [
            "0/U", "0/T", "0/k", "0/omega", "0/p", "0/p_rgh", "0/nut",
            "0/alphat", "constant/transportProperties", "constant/g",
            "constant/turbulenceProperties", "constant/fvOptions",
            "constant/polyMesh", "mesh_manifest.json", "surface_manifest.json",
            "thermal_input.physical.v1.json",
        ]
        for case_name in ("baseline_first_order", "variant_second_order"):
            case = root / case_name
            for relative in required_paths:
                target = case / relative
                if relative == "constant/polyMesh":
                    target.mkdir(parents=True, exist_ok=True)
                    (target / "points").write_text("shared mesh\n", encoding="utf-8")
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(f"shared:{relative}\n", encoding="utf-8")
        physical_tree = sensitivity_job.create_physical_tree_snapshot([
            {
                "path": path,
                "sha256": preparation._hash_regular_tree(
                    root / "baseline_first_order" / path
                ),
                "immutable": True,
            }
            for path in required_paths
        ])
        mesh_sha = self._sha256(root / "baseline_first_order" / "mesh_manifest.json")
        physical_sha = sensitivity_job.derive_physical_input_sha256(
            mesh_sha256=mesh_sha, physical_tree=physical_tree, selector=selector
        )
        baseline_seed = self._seed_case(
            root / "baseline_first_order", "baseline", "stabilized_first_order_v1"
        )
        variant_seed = self._seed_case(
            root / "variant_second_order", "variant", "design_limited_second_order_v1"
        )
        job_id = sensitivity_job.derive_frozen_pair_job_id(
            mesh_sha256=mesh_sha, physical_tree=physical_tree, selector=selector,
            baseline_case_seed_snapshot_sha256=baseline_seed,
            variant_case_seed_snapshot_sha256=variant_seed,
        )
        pair = sensitivity_job.create_frozen_pair_manifest(
            job_id=job_id, selector=selector, mesh_sha256=mesh_sha,
            physical_input_sha256=physical_sha, physical_tree=physical_tree,
            baseline={"run_id": "baseline-run", "profile": "stabilized_first_order_v1",
                      "case_child": "baseline_first_order",
                      "processor_directories_present": False,
                      "case_seed_snapshot_sha256": baseline_seed},
            variant={"run_id": "variant-run", "profile": "design_limited_second_order_v1",
                     "case_child": "variant_second_order",
                     "processor_directories_present": False,
                     "case_seed_snapshot_sha256": variant_seed},
            requested_ranks=1,
        )
        job = sensitivity_job.build_cfd_numerical_sensitivity_job_manifest(
            pair,
            qoi_limits={
                "occupied_zone_mean_temperature_k": 0.5,
                "occupied_zone_mean_speed_m_s": 0.05,
                "exhaust_temperature_rise_k": 0.5,
            },
        )
        prep = {
            "contract": "serial_numerical_sensitivity_preparation.v1",
            "status": "PENDING_SOLVER_EVIDENCE",
            "frozen_pair_manifest_sha256": pair["manifest_sha256"],
            "job_manifest_sha256": job["job_manifest_sha256"],
        }
        for name, value in (
            ("frozen_pair_manifest.json", pair),
            ("cfd_numerical_sensitivity_job.v1.json", job),
            ("serial_sensitivity_preparation.v1.json", prep),
        ):
            (root / name).write_text(json.dumps(value), encoding="utf-8")
        return tmp, root

    @staticmethod
    def _completed_solver_result(case, profile):
        run = {
            "contract": "run_manifest.v1",
            "status": "WARN",
            "effective_numerics": {"profile": profile},
            "thermal_progress": {
                "flow_through_fraction": 3.0,
                "flow_through_time_s": 10.0,
                "latest_time_s": 30.0,
            },
        }
        result = {"contract": "result_manifest.v1", "time_s": 30.0}
        (case / "run_manifest.json").write_text(json.dumps(run), encoding="utf-8")
        (case / "result_manifest.json").write_text(json.dumps(result), encoding="utf-8")
        (case / "log.solver").write_text(profile, encoding="utf-8")
        return {"ok": True, "manifest": run}

    def test_executes_baseline_then_variant_under_one_solver_lock_and_checkpoints(self):
        tmp, study = self._study()
        self.addCleanup(tmp.cleanup)
        progress = []
        calls = []

        def solve(case, target, progress_cb=None):
            calls.append(case.name)
            if case.name == "variant_second_order":
                checkpoint = json.loads(
                    (study / "serial_sensitivity_execution.v1.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(checkpoint["baseline"]["status"], "COMPLETE")
                self.assertEqual(checkpoint["variant"]["status"], "RUNNING")
            profile = (
                "stabilized_first_order_v1"
                if case.name == "baseline_first_order"
                else "design_limited_second_order_v1"
            )
            return self._completed_solver_result(case, profile)

        with mock.patch.object(
                execution.cfd_gci_job, "acquire_solver_lock",
                return_value=("lock-token", {"pid": 123})) as acquire, mock.patch.object(
                execution.cfd_gci_job, "release_solver_lock") as release, mock.patch.object(
                execution, "_run_case_to_target", side_effect=solve):
            result = execution.run_serial_sensitivity_pair(
                study, progress_cb=progress.append
            )

        self.assertEqual(calls, ["baseline_first_order", "variant_second_order"])
        self.assertEqual(result["status"], "SOLVER_RUNS_COMPLETE")
        self.assertFalse(result["valid"])
        self.assertIn("INDEPENDENT_VERIFICATION_REQUIRED", result["blockers"])
        acquire.assert_called_once()
        release.assert_called_once()
        released_root, released_token = release.call_args.args
        self.assertEqual(released_root, study.parent.resolve())
        self.assertEqual(released_token, "lock-token")
        self.assertEqual(progress[-1]["stage"], "solver_runs_complete")
        saved = json.loads(
            (study / "serial_sensitivity_execution.v1.json").read_text(encoding="utf-8")
        )
        self.assertEqual(saved["baseline"]["status"], "COMPLETE")
        self.assertEqual(saved["variant"]["status"], "COMPLETE")
        self.assertNotEqual(
            saved["baseline"]["run_manifest_sha256"],
            saved["variant"]["run_manifest_sha256"],
        )

    def test_execution_refuses_arbitrary_variant_case(self):
        tmp, study = self._study()
        self.addCleanup(tmp.cleanup)
        with self.assertRaises(execution.NumericalSensitivityExecutionError) as caught:
            execution.run_serial_sensitivity_pair(
                study, variant_case=study / "baseline_first_order"
            )
        self.assertIn("SENSITIVITY_VARIANT_CASE_MISMATCH", str(caught.exception))

    def test_verifier_does_not_promote_missing_solver_evidence(self):
        tmp, study = self._study()
        self.addCleanup(tmp.cleanup)
        result = execution.verify_serial_sensitivity_pair(
            study, study / "variant_second_order"
        )
        self.assertEqual(result["status"], "NOT_EVALUATED")
        self.assertFalse(result["valid"])
        self.assertIn("SOLVER_EVIDENCE_MISSING", result["blockers"])

    def _install_verifiable_solver_evidence(self, study, *, variant_delta=0.1):
        residuals = {
            field: {
                "final": 1e-6,
                "tail_maximum": 1e-6,
                "tail_samples": cfd_numerics.DEFAULT_RESIDUAL_TAIL_SAMPLES,
                "limit": limit,
            }
            for field, limit in cfd_numerics.THERMAL_RESIDUAL_LIMITS.items()
        }
        pair = json.loads((study / "frozen_pair_manifest.json").read_text(
            encoding="utf-8"))
        checkpoint = {
            "contract": "serial_numerical_sensitivity_execution.v1",
            "status": "SOLVER_RUNS_COMPLETE",
            "valid": False,
            "job_id": pair["job_id"],
            "pair_manifest_sha256": pair["manifest_sha256"],
            "job_manifest_sha256": json.loads(
                (study / "cfd_numerical_sensitivity_job.v1.json").read_text(
                    encoding="utf-8"))["job_manifest_sha256"],
            "target_flow_through_fraction": 3.0,
            "blockers": ["INDEPENDENT_VERIFICATION_REQUIRED"],
        }
        for role in ("baseline", "variant"):
            case = study / pair[role]["case_child"]
            profile = pair[role]["profile"]
            settings = dict(cfd_physics.DEFAULT_SETTINGS)
            settings.update({
                "thermal_numerics_profile": profile,
                "thermal_design_max_courant_gate": 1.0,
                "terminal_phi_imbalance_max": 0.001,
                "occupied_floor_elevation_m": 0.0,
            })
            numerics = cfd_numerics.thermal_numerics_contract(
                {"mesh": {"max_non_orthogonality": 10.0}}, settings)
            (case / "system").mkdir(exist_ok=True)
            (case / "system" / "fvSchemes").write_text(
                cfd_physics._thermal_fv_schemes(numerics), encoding="utf-8")
            (case / "system" / "fvSolution").write_text(
                cfd_physics._thermal_fv_solution(settings, numerics), encoding="utf-8")
            run = {
                "contract": "run_manifest.v1",
                "status": "PASS",
                "effective_numerics": numerics,
                "thermal_progress": {
                    "flow_through_fraction": 3.0,
                    "flow_through_time_s": 10.0,
                    "latest_time_s": 30.0,
                },
                "solver": {
                    "ended": True,
                    "fatal": False,
                    "courant": {"peak_maximum": 0.7},
                    "thermal_residuals": {
                        name: {"final": row["final"]}
                        for name, row in residuals.items()
                    },
                    "thermal_residual_history": {
                        name: [{"final": row["tail_maximum"]}] * row["tail_samples"]
                        for name, row in residuals.items()
                    },
                    "continuity": {"global": 1e-7},
                },
                "flux_balance": {"available": True, "imbalance_ratio": 0.0005},
                "thermal": {
                    "energy_closure_basis": (
                        "solver_positive_phi_and_owner_cell_temperature"
                    )
                },
                "effective_settings": settings,
            }
            run_path = case / "run_manifest.json"
            run_path.write_text(json.dumps(run), encoding="utf-8")
            log_path = case / "log.solver"
            log_path.write_text(f"solver evidence:{profile}\n", encoding="utf-8")
            source = case / "VTK" / "final" / "internal.vtu"
            source.parent.mkdir(parents=True)
            source.write_text(f"raw-vtu:{profile}\n", encoding="utf-8")
            summary = case / "results" / "body_fitted_summary.json"
            summary.parent.mkdir(parents=True)
            summary.write_text(json.dumps({"time_s": 30.0}), encoding="utf-8")
            result = {
                "contract": "result_manifest.v1",
                "time_s": 30.0,
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
            result_path = case / "result_manifest.json"
            result_path.write_text(json.dumps(result), encoding="utf-8")
            checkpoint[role] = execution._completed_side(case, pair[role])
        checkpoint["completed_at"] = "2026-08-28T12:00:00+00:00"
        (study / "serial_sensitivity_execution.v1.json").write_text(
            json.dumps(checkpoint), encoding="utf-8")
        occupied = {
            "baseline": {
                "occupied_zone_mean_temperature_k": 294.0,
                "occupied_zone_mean_speed_m_s": 0.20,
            },
            "variant": {
                "occupied_zone_mean_temperature_k": 294.0 + variant_delta,
                "occupied_zone_mean_speed_m_s": 0.20 + variant_delta / 10.0,
            },
        }
        exhaust = {
            "baseline": {"exhaust_temperature_rise_k": 5.0},
            "variant": {"exhaust_temperature_rise_k": 5.0 + variant_delta},
        }
        return occupied, exhaust

    def test_verifier_rehashes_raw_evidence_recomputes_qois_and_publishes_pass(self):
        tmp, study = self._study()
        self.addCleanup(tmp.cleanup)
        occupied, exhaust = self._install_verifiable_solver_evidence(study)

        with mock.patch.object(
                execution.cfd_post, "compute_time_weighted_occupied_volume_qois",
                side_effect=[occupied["baseline"], occupied["variant"]]), mock.patch.object(
                execution.cfd_post,
                "read_time_weighted_exhaust_temperature_rise_from_case",
                side_effect=[exhaust["baseline"], exhaust["variant"]]):
            result = execution.verify_serial_sensitivity_pair(
                study, study / "variant_second_order"
            )

        self.assertEqual(result["status"], "PASS", result)
        self.assertTrue(result["valid"])
        self.assertTrue((study / "numerical_sensitivity.v1.json").is_file())
        self.assertTrue(
            (study / "variant_second_order" / "numerical_sensitivity.json").is_file()
        )
        schema = json.loads((Path(__file__).resolve().parents[1]
                             / "numerical_sensitivity.v1.schema.json").read_text(
                                 encoding="utf-8"))
        validate(instance=json.loads(
            (study / "numerical_sensitivity.v1.json").read_text(encoding="utf-8")
        ), schema=schema)
        self.assertEqual({row["name"] for row in result["qoi_comparisons"]}, {
            "occupied_zone_mean_temperature_k",
            "occupied_zone_mean_speed_m_s",
            "exhaust_temperature_rise_k",
        })
        self.assertTrue(result["verification"]["raw_artifacts_rehashed"])

    def test_verifier_blocks_tampered_result_log_or_physical_tree(self):
        for target in ("result", "result_time", "log", "physical", "numerics"):
            with self.subTest(target=target):
                tmp, study = self._study()
                self.addCleanup(tmp.cleanup)
                occupied, exhaust = self._install_verifiable_solver_evidence(study)
                case = study / "variant_second_order"
                if target == "result":
                    (case / "result_manifest.json").write_text("{}", encoding="utf-8")
                elif target == "result_time":
                    result_path = case / "result_manifest.json"
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                    result["time_s"] = 29.0
                    result_path.write_text(json.dumps(result), encoding="utf-8")
                    checkpoint_path = study / "serial_sensitivity_execution.v1.json"
                    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
                    checkpoint["variant"]["result_manifest_sha256"] = self._sha256(
                        result_path)
                    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
                elif target == "log":
                    (case / "log.solver").write_text("tampered", encoding="utf-8")
                elif target == "physical":
                    (case / "0" / "T").write_text("tampered", encoding="utf-8")
                else:
                    schemes = case / "system" / "fvSchemes"
                    schemes.write_text(
                        schemes.read_text(encoding="utf-8").replace(
                            "linearUpwind grad(U)", "upwind"),
                        encoding="utf-8",
                    )
                with mock.patch.object(
                        execution.cfd_post,
                        "compute_time_weighted_occupied_volume_qois",
                        side_effect=[occupied["baseline"], occupied["variant"]]), \
                        mock.patch.object(
                        execution.cfd_post,
                        "read_time_weighted_exhaust_temperature_rise_from_case",
                        side_effect=[exhaust["baseline"], exhaust["variant"]]):
                    with self.assertRaises(execution.NumericalSensitivityExecutionError):
                        execution.verify_serial_sensitivity_pair(
                            study, study / "variant_second_order")

    def test_verifier_blocks_qoi_limit_failure_without_writing_pass(self):
        tmp, study = self._study()
        self.addCleanup(tmp.cleanup)
        occupied, exhaust = self._install_verifiable_solver_evidence(
            study, variant_delta=1.0)
        with mock.patch.object(
                execution.cfd_post, "compute_time_weighted_occupied_volume_qois",
                side_effect=[occupied["baseline"], occupied["variant"]]), mock.patch.object(
                execution.cfd_post,
                "read_time_weighted_exhaust_temperature_rise_from_case",
                side_effect=[exhaust["baseline"], exhaust["variant"]]):
            with self.assertRaisesRegex(
                    execution.NumericalSensitivityExecutionError,
                    "SENSITIVITY_QOI_LIMIT_FAILED"):
                execution.verify_serial_sensitivity_pair(
                    study, study / "variant_second_order")
        self.assertFalse((study / "numerical_sensitivity.v1.json").exists())

    def test_execution_rejects_processor_directories_before_solver(self):
        tmp, study = self._study()
        self.addCleanup(tmp.cleanup)
        (study / "variant_second_order" / "processor0").mkdir()
        with self.assertRaises(execution.NumericalSensitivityExecutionError) as caught:
            execution.run_serial_sensitivity_pair(study)
        self.assertIn("SENSITIVITY_PROCESSOR_DIRECTORIES_FORBIDDEN", str(caught.exception))

    def test_execution_rejects_existing_solver_evidence_instead_of_reusing_a_run(self):
        tmp, study = self._study()
        self.addCleanup(tmp.cleanup)
        (study / "baseline_first_order" / "run_manifest.json").write_text(
            "{}", encoding="utf-8"
        )
        with self.assertRaises(execution.NumericalSensitivityExecutionError) as caught:
            execution.run_serial_sensitivity_pair(study)
        self.assertIn("SENSITIVITY_EXISTING_RUN_FORBIDDEN", str(caught.exception))

    def test_execution_rehashes_frozen_seed_files_before_any_solver_call(self):
        tmp, study = self._study()
        self.addCleanup(tmp.cleanup)
        (study / "variant_second_order" / "frozen-input.txt").write_text(
            "changed beyond numerical profile", encoding="utf-8"
        )
        with mock.patch.object(execution, "_run_case_to_target") as solve:
            with self.assertRaises(execution.NumericalSensitivityExecutionError) as caught:
                execution.run_serial_sensitivity_pair(study)
        solve.assert_not_called()
        self.assertIn("SENSITIVITY_CASE_SEED_ENTRY_HASH_MISMATCH", str(caught.exception))

    def test_execution_fails_when_global_solver_slot_is_busy(self):
        tmp, study = self._study()
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(
                execution.cfd_gci_job, "acquire_solver_lock",
                return_value=(None, {"pid": 9876})), mock.patch.object(
                execution, "_run_case_to_target") as solve:
            with self.assertRaises(execution.NumericalSensitivityExecutionError) as caught:
                execution.run_serial_sensitivity_pair(study)
        solve.assert_not_called()
        self.assertIn("CFD_SOLVER_BUSY", str(caught.exception))

    def test_job_schema_is_strictly_pending_input_only(self):
        schema = json.loads(
            (Path(__file__).resolve().parents[1]
             / "cfd_numerical_sensitivity_job.v1.schema.json").read_text(
                 encoding="utf-8"
             )
        )
        self.assertEqual(schema["$id"], "cfd_numerical_sensitivity_job.v1.schema.json")
        self.assertEqual(
            schema["properties"]["contract"]["const"],
            "cfd_numerical_sensitivity_job.v1",
        )
        self.assertEqual(
            schema["properties"]["status"]["const"],
            "PENDING_SOLVER_EVIDENCE",
        )
        tmp, study = self._study()
        self.addCleanup(tmp.cleanup)
        job = json.loads(
            (study / "cfd_numerical_sensitivity_job.v1.json").read_text(
                encoding="utf-8"
            )
        )
        validate(instance=job, schema=schema)


if __name__ == "__main__":
    unittest.main()
