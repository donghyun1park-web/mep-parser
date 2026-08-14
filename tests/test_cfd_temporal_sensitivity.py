import json
from pathlib import Path
import tempfile
import unittest

import cfd_temporal_sensitivity as temporal
import run_temporal_sensitivity as runner
from jsonschema import validate


class TemporalSensitivityContractTests(unittest.TestCase):
    def _seed(self):
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name) / "seed"
        (root / "0").mkdir(parents=True)
        (root / "system").mkdir()
        (root / "constant" / "polyMesh").mkdir(parents=True)
        (root / "0" / "U").write_text("uniform (0 0 0);\n", encoding="utf-8")
        (root / "system" / "controlDict").write_text(
            "adjustTimeStep no;\ndeltaT 0.02;\n", encoding="utf-8"
        )
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

    def test_schema_is_pending_only(self):
        schema = json.loads(
            (Path(__file__).resolve().parents[1]
             / "temporal_sensitivity.v1.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(schema["properties"]["status"]["const"], "PENDING_SOLVER_EVIDENCE")
        tmp, seed = self._seed()
        self.addCleanup(tmp.cleanup)
        validate(
            instance=temporal.create_temporal_study(seed, [0.04, 0.02, 0.01]),
            schema=schema,
        )


if __name__ == "__main__":
    unittest.main()
