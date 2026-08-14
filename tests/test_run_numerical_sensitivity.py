import json
from pathlib import Path
import tempfile
import unittest

import run_numerical_sensitivity as execution
import cfd_numerical_sensitivity_job as sensitivity_job
from jsonschema import validate


class SerialSensitivityExecutionContractTests(unittest.TestCase):
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
        baseline_seed = "b" * 64
        variant_seed = "c" * 64
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

    def test_execution_interface_is_pending_and_never_starts_solver(self):
        tmp, study = self._study()
        self.addCleanup(tmp.cleanup)
        progress = []

        result = execution.run_serial_sensitivity_pair(
            study, progress_cb=progress.append
        )

        self.assertEqual(result["status"], "PENDING_SOLVER_EVIDENCE")
        self.assertFalse(result["valid"])
        self.assertIn("SOLVER_EXECUTION_PENDING", result["blockers"])
        self.assertIn("WSL_OR_OPENFOAM_REQUIRED", result["blockers"])
        self.assertEqual(progress[-1]["stage"], "pending_solver_evidence")

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

    def test_cli_execute_flag_is_fail_closed(self):
        tmp, study = self._study()
        self.addCleanup(tmp.cleanup)
        result = execution.run_serial_sensitivity_pair(study, execute=True)
        self.assertEqual(result["status"], "NOT_EVALUATED")
        self.assertIn("SOLVER_EXECUTION_DISABLED", result["blockers"])

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
