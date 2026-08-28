import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import run_numerical_sensitivity as execution
import cfd_numerical_sensitivity_job as sensitivity_job
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
        selector = {
            "contract": "occupied_volume_band.v1",
            "coordinate_source": "cell_center_m_agl",
            "z_min_agl_m": 0.1,
            "z_max_agl_m": 1.8,
        }
        required_paths = [
            "0/U", "0/T", "0/k", "0/omega", "0/p", "0/p_rgh", "0/nut",
            "0/alphat", "constant/transportProperties", "constant/g",
            "constant/turbulenceProperties", "constant/fvOptions",
            "constant/polyMesh", "mesh_manifest.json", "surface_manifest.json",
            "thermal_input.physical.v1.json",
        ]
        physical_tree = sensitivity_job.create_physical_tree_snapshot([
            {"path": path, "sha256": f"{index + 1:064x}", "immutable": True}
            for index, path in enumerate(required_paths)
        ])
        mesh_sha = "a" * 64
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
        release.assert_called_once_with(study.parent, "lock-token")
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
