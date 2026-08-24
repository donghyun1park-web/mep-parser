import copy
import hashlib
import json
from pathlib import Path
import unittest


import cfd_numerical_sensitivity_job as sensitivity_job


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
HASH_E = "e" * 64
HASH_F = "f" * 64


class OccupiedVolumeBandTests(unittest.TestCase):
    def _selector(self, **overrides):
        selector = {
            "contract": "occupied_volume_band.v1",
            "coordinate_source": "cell_center_m_agl",
            "z_min_agl_m": 0.1,
            "z_max_agl_m": 1.8,
        }
        selector.update(overrides)
        return selector

    def test_missing_selector_is_rejected_without_a_whole_volume_default(self):
        validation = sensitivity_job.validate_occupied_volume_band(None)

        self.assertFalse(validation["valid"])
        self.assertIn("OCCUPIED_SELECTOR_MISSING", validation["blockers"])
        with self.assertRaises(sensitivity_job.NumericalSensitivityJobInputError):
            sensitivity_job.compute_occupied_volume_qois([], None)

    def test_invalid_or_empty_band_is_rejected(self):
        with self.assertRaises(sensitivity_job.NumericalSensitivityJobInputError):
            sensitivity_job.normalize_occupied_volume_band(
                self._selector(z_min_agl_m=1.8, z_max_agl_m=1.8)
            )

        cells = [{
            "center_m": [1.0, 1.0, 2.0],
            "volume_m3": 1.0,
            "temperature_k": 300.0,
            "velocity_m_s": 0.2,
        }]
        with self.assertRaises(sensitivity_job.NumericalSensitivityJobInputError) as caught:
            sensitivity_job.compute_occupied_volume_qois(cells, self._selector())
        self.assertIn("OCCUPIED_SELECTED_CELLS_EMPTY", str(caught.exception))

    def test_selected_cells_use_volume_weighted_temperature_and_speed(self):
        cells = [
            {
                "center_m": [1.0, 1.0, 0.5],
                "volume_m3": 1.0,
                "temperature_k": 290.0,
                "velocity_m_s": 0.1,
            },
            {
                "center_m": [2.0, 1.0, 1.2],
                "volume_m3": 3.0,
                "temperature_k": 300.0,
                "velocity_m_s": 0.5,
            },
            {
                "center_m": [3.0, 1.0, 2.4],
                "volume_m3": 100.0,
                "temperature_k": 999.0,
                "velocity_m_s": 99.0,
            },
        ]

        qois = sensitivity_job.compute_occupied_volume_qois(cells, self._selector())

        self.assertEqual(qois["scope"], "selected_occupied_volume_band")
        self.assertEqual(qois["aggregation"], "volume_weighted_cell_centers.v1")
        self.assertEqual(qois["selected_cell_count"], 2)
        self.assertAlmostEqual(qois["selected_volume_m3"], 4.0)
        self.assertAlmostEqual(qois["occupied_zone_mean_temperature_k"], 297.5)
        self.assertAlmostEqual(qois["occupied_zone_mean_speed_m_s"], 0.4)
        self.assertNotIn("whole_volume", qois.values())

    def test_xy_bounds_and_nonfinite_or_missing_cell_data_are_not_silently_ignored(self):
        selector = self._selector(xy_bounds_m={
            "x_min_m": 0.0,
            "x_max_m": 1.5,
            "y_min_m": 0.0,
            "y_max_m": 2.0,
        })
        cells = [
            {
                "center_m": [1.0, 1.0, 0.5],
                "volume_m3": 1.0,
                "temperature_k": 290.0,
                "velocity_m_s": 0.1,
            },
            {
                "center_m": [2.0, 1.0, 0.5],
                "volume_m3": 1.0,
                "temperature_k": 400.0,
                "velocity_m_s": 1.0,
            },
        ]
        qois = sensitivity_job.compute_occupied_volume_qois(cells, selector)
        self.assertEqual(qois["selected_cell_count"], 1)
        self.assertAlmostEqual(qois["occupied_zone_mean_temperature_k"], 290.0)

        invalid = copy.deepcopy(cells)
        invalid[1].pop("volume_m3")
        with self.assertRaises(sensitivity_job.NumericalSensitivityJobInputError) as caught:
            sensitivity_job.compute_occupied_volume_qois(invalid, selector)
        self.assertIn("OCCUPIED_CELL_VOLUME_INVALID", str(caught.exception))

    def test_occupied_volume_band_contract_has_a_strict_schema(self):
        schema_path = Path("occupied_volume_band.v1.schema.json")

        with schema_path.open(encoding="utf-8") as handle:
            schema = json.load(handle)

        self.assertEqual(schema["$id"], "occupied_volume_band.v1.schema.json")
        self.assertEqual(schema["properties"]["contract"]["const"], "occupied_volume_band.v1")
        self.assertTrue(schema["additionalProperties"] is False)
        self.assertEqual(
            set(schema["required"]),
            {"contract", "coordinate_source", "z_min_agl_m", "z_max_agl_m"},
        )


class FrozenPairManifestTests(unittest.TestCase):
    _THERMAL_PHYSICAL_INPUT_PATH = "thermal_input.physical.v1.json"

    def _selector(self):
        return {
            "contract": "occupied_volume_band.v1",
            "coordinate_source": "cell_center_m_agl",
            "z_min_agl_m": 0.1,
            "z_max_agl_m": 1.8,
        }

    @staticmethod
    def _physical_tree():
        paths = [
            "0/U", "0/T", "0/k", "0/omega", "0/p", "0/p_rgh", "0/nut", "0/alphat",
            "constant/transportProperties", "constant/g",
            "constant/turbulenceProperties", "constant/fvOptions",
            "constant/polyMesh", "mesh_manifest.json", "surface_manifest.json",
            "thermal_input.physical.v1.json",
        ]
        entries = [
            {
                "path": path,
                "sha256": hashlib.sha256(path.encode("utf-8")).hexdigest(),
                "immutable": True,
            }
            for path in paths
        ]
        return sensitivity_job.create_physical_tree_snapshot(entries)

    def test_boussinesq_tree_allows_absent_thermophysical_properties_and_requires_runtime_inputs(self):
        entries = copy.deepcopy(self._physical_tree()["entries"])
        self.assertNotIn(
            "constant/thermophysicalProperties",
            {entry["path"] for entry in entries},
        )
        required_paths = (
            "0/p",
            "constant/turbulenceProperties",
            "mesh_manifest.json",
            "surface_manifest.json",
        )
        self.assertTrue({entry["path"] for entry in entries}.issuperset(required_paths))

        for path in required_paths:
            with self.subTest(path=path):
                missing = [entry for entry in entries if entry["path"] != path]

                with self.assertRaises(sensitivity_job.NumericalSensitivityJobInputError) as caught:
                    sensitivity_job.create_physical_tree_snapshot(missing)

                self.assertIn(
                    "FROZEN_PAIR_PHYSICAL_TREE_REQUIRED_PATH_MISSING",
                    str(caught.exception),
                )

    @staticmethod
    def _canonical_sha256(value):
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _physical_input_hash(cls, selector, physical_tree, mesh_sha256=HASH_C):
        selector_payload = {
            "contract": "occupied_volume_band.v1",
            "coordinate_source": "cell_center_m_agl",
            "z_min_agl_m": float(selector["z_min_agl_m"]),
            "z_max_agl_m": float(selector["z_max_agl_m"]),
        }
        if "xy_bounds_m" in selector:
            selector_payload["xy_bounds_m"] = {
                name: float(selector["xy_bounds_m"][name])
                for name in ("x_min_m", "x_max_m", "y_min_m", "y_max_m")
            }
        selector_sha256 = cls._canonical_sha256(selector_payload)
        thermal_input_sha256 = next(
            entry["sha256"] for entry in physical_tree["entries"]
            if entry["path"] == cls._THERMAL_PHYSICAL_INPUT_PATH
        )
        return cls._canonical_sha256({
            "mesh_sha256": mesh_sha256,
            "physical_tree_sha256": physical_tree["tree_sha256"],
            "selector_sha256": selector_sha256,
            "thermal_input_sha256": thermal_input_sha256,
        })

    @classmethod
    def _expected_job_id(cls, selector, physical_tree, mesh_sha256=HASH_C,
                         baseline_seed=HASH_A, variant_seed=HASH_E):
        """Independently derive the public frozen-pair identity for this fixture."""
        return "sens-" + cls._canonical_sha256({
            "physical_input_sha256": cls._physical_input_hash(
                selector, physical_tree, mesh_sha256
            ),
            "allowed_variation": {
                "parameter": "thermal_numerics_profile",
                "baseline": "stabilized_first_order_v1",
                "variant": "design_limited_second_order_v1",
                "all_other_inputs_equal": True,
            },
            "baseline": {
                "case_child": "baseline_first_order",
                "profile": "stabilized_first_order_v1",
                "case_seed_snapshot_sha256": baseline_seed,
            },
            "variant": {
                "case_child": "variant_second_order",
                "profile": "design_limited_second_order_v1",
                "case_seed_snapshot_sha256": variant_seed,
            },
        })[:24]

    @classmethod
    def _rehash_manifest(cls, manifest, *, sides=False):
        if sides:
            for name in ("baseline", "variant"):
                side = copy.deepcopy(manifest[name])
                side.pop("input_snapshot_sha256")
                manifest[name]["input_snapshot_sha256"] = cls._canonical_sha256(side)
        raw = copy.deepcopy(manifest)
        raw.pop("manifest_sha256")
        manifest["manifest_sha256"] = cls._canonical_sha256(raw)

    @staticmethod
    def _side(run_id, profile, seed_hash, *, case_child=None):
        if case_child is None:
            case_child = {
                "stabilized_first_order_v1": "baseline_first_order",
                "design_limited_second_order_v1": "variant_second_order",
            }[profile]
        return {
            "run_id": run_id,
            "profile": profile,
            "case_child": case_child or run_id,
            "processor_directories_present": False,
            "case_seed_snapshot_sha256": seed_hash,
        }

    def _manifest(self):
        selector = self._selector()
        physical_tree = self._physical_tree()
        return sensitivity_job.create_frozen_pair_manifest(
            job_id=self._expected_job_id(selector, physical_tree),
            selector=selector,
            mesh_sha256=HASH_C,
            physical_input_sha256=self._physical_input_hash(selector, physical_tree),
            physical_tree=physical_tree,
            baseline=self._side(
                "serial-baseline-001", "stabilized_first_order_v1", HASH_A
            ),
            variant=self._side(
                "serial-variant-001", "design_limited_second_order_v1", HASH_E
            ),
            requested_ranks=1,
        )

    def test_public_physical_input_derivation_matches_frozen_pair_binding(self):
        selector = self._selector()
        physical_tree = self._physical_tree()

        derived = sensitivity_job.derive_physical_input_sha256(
            mesh_sha256=HASH_C,
            physical_tree=physical_tree,
            selector=selector,
        )

        self.assertEqual(
            derived,
            self._physical_input_hash(selector, physical_tree),
        )

    def test_pre_run_pair_uses_only_frozen_input_evidence(self):
        manifest = self._manifest()

        self.assertEqual(manifest["status"], "FROZEN_INPUTS")
        for side_name in ("baseline", "variant"):
            side = manifest[side_name]
            self.assertIn("case_seed_snapshot_sha256", side)
            self.assertIn("input_snapshot_sha256", side)
            self.assertNotIn("artifacts", side)
            self.assertNotIn("run_manifest", side)
            self.assertNotIn("result_snapshot", side)
        self.assertTrue(sensitivity_job.validate_frozen_pair_manifest(manifest)["valid"])

    def test_pre_run_pair_rejects_missing_seed_or_post_run_artifacts(self):
        selector = self._selector()
        physical_tree = self._physical_tree()
        baseline = self._side(
            "serial-baseline-001", "stabilized_first_order_v1", HASH_A
        )
        baseline.pop("case_seed_snapshot_sha256")

        with self.assertRaises(sensitivity_job.NumericalSensitivityJobInputError) as caught:
            sensitivity_job.create_frozen_pair_manifest(
                job_id=self._expected_job_id(selector, physical_tree),
                selector=selector,
                mesh_sha256=HASH_C,
                physical_input_sha256=self._physical_input_hash(selector, physical_tree),
                physical_tree=physical_tree,
                baseline=baseline,
                variant=self._side(
                    "serial-variant-001", "design_limited_second_order_v1", HASH_E
                ),
                requested_ranks=1,
            )
        self.assertIn("FROZEN_PAIR_BASELINE_CASE_SEED_SNAPSHOT_MISSING", str(caught.exception))

        baseline = self._side(
            "serial-baseline-001", "stabilized_first_order_v1", HASH_A
        )
        baseline["artifacts"] = [
            {"name": "run_manifest", "sha256": HASH_B, "immutable": True},
            {"name": "result_snapshot", "sha256": HASH_F, "immutable": True},
        ]
        with self.assertRaises(sensitivity_job.NumericalSensitivityJobInputError) as caught:
            sensitivity_job.create_frozen_pair_manifest(
                job_id=self._expected_job_id(selector, physical_tree),
                selector=selector,
                mesh_sha256=HASH_C,
                physical_input_sha256=self._physical_input_hash(selector, physical_tree),
                physical_tree=physical_tree,
                baseline=baseline,
                variant=self._side(
                    "serial-variant-001", "design_limited_second_order_v1", HASH_E
                ),
                requested_ranks=1,
            )
        self.assertIn("FROZEN_PAIR_BASELINE_POST_RUN_ARTIFACTS_FORBIDDEN", str(caught.exception))

    def test_parallel_or_rank_greater_than_one_is_rejected(self):
        with self.assertRaises(sensitivity_job.NumericalSensitivityJobInputError) as caught:
            sensitivity_job.create_frozen_pair_manifest(
                job_id="sensitivity-job-001",
                selector=self._selector(),
                mesh_sha256=HASH_C,
                physical_input_sha256=HASH_D,
                physical_tree=self._physical_tree(),
                baseline=self._side(
                    "serial-baseline-001", "stabilized_first_order_v1", HASH_A
                ),
                variant=self._side(
                    "serial-variant-001", "design_limited_second_order_v1", HASH_E
                ),
                requested_ranks=2,
            )
        self.assertIn("NUMERICAL_SENSITIVITY_SERIAL_REQUIRED", str(caught.exception))

    def test_case_child_path_or_processor_directory_is_rejected(self):
        invalid_baseline = self._side(
            "serial-baseline-001", "stabilized_first_order_v1", HASH_A,
            case_child="../outside",
        )
        with self.assertRaises(sensitivity_job.NumericalSensitivityJobInputError) as caught:
            sensitivity_job.create_frozen_pair_manifest(
                job_id="sensitivity-job-001",
                selector=self._selector(),
                mesh_sha256=HASH_C,
                physical_input_sha256=HASH_D,
                physical_tree=self._physical_tree(),
                baseline=invalid_baseline,
                variant=self._side(
                    "serial-variant-001", "design_limited_second_order_v1", HASH_E
                ),
                requested_ranks=1,
            )
        self.assertIn("FROZEN_PAIR_BASELINE_CASE_CHILD_INVALID", str(caught.exception))

        invalid_processor = self._side(
            "serial-baseline-001", "stabilized_first_order_v1", HASH_A,
        )
        invalid_processor["processor_directories_present"] = True
        with self.assertRaises(sensitivity_job.NumericalSensitivityJobInputError) as caught:
            sensitivity_job.create_frozen_pair_manifest(
                job_id="sensitivity-job-001",
                selector=self._selector(),
                mesh_sha256=HASH_C,
                physical_input_sha256=HASH_D,
                physical_tree=self._physical_tree(),
                baseline=invalid_processor,
                variant=self._side(
                    "serial-variant-001", "design_limited_second_order_v1", HASH_E
                ),
                requested_ranks=1,
            )
        self.assertIn("FROZEN_PAIR_BASELINE_PROCESSOR_DIRS_PRESENT", str(caught.exception))

    def test_physical_tree_rejects_windows_absolute_path_before_snapshot(self):
        entries = copy.deepcopy(self._physical_tree()["entries"])
        entries.append({
            "path": "C:/outside",
            "sha256": HASH_A,
            "immutable": True,
        })

        with self.assertRaises(sensitivity_job.NumericalSensitivityJobInputError) as caught:
            sensitivity_job.create_physical_tree_snapshot(entries)

        self.assertIn("FROZEN_PAIR_PHYSICAL_TREE_PATH_INVALID", str(caught.exception))

    def test_create_rejects_an_opaque_physical_input_hash(self):
        selector = self._selector()
        physical_tree = self._physical_tree()

        with self.assertRaises(sensitivity_job.NumericalSensitivityJobInputError) as caught:
            sensitivity_job.create_frozen_pair_manifest(
                job_id="sensitivity-job-001",
                selector=selector,
                mesh_sha256=HASH_C,
                physical_input_sha256=HASH_D,
                physical_tree=physical_tree,
                baseline=self._side(
                    "serial-baseline-001", "stabilized_first_order_v1", HASH_A
                ),
                variant=self._side(
                    "serial-variant-001", "design_limited_second_order_v1", HASH_E
                ),
                requested_ranks=1,
            )

        self.assertIn("FROZEN_PAIR_PHYSICAL_INPUT_BINDING_MISMATCH", str(caught.exception))

    def test_manifest_requires_equal_frozen_mesh_and_physical_input_snapshots(self):
        manifest = self._manifest()
        forged = copy.deepcopy(manifest)
        forged["variant"]["mesh_sha256"] = HASH_A
        forged["variant"]["input_snapshot_sha256"] = sensitivity_job.canonical_sha256(
            sensitivity_job.without_input_snapshot_hash(forged["variant"])
        )
        forged["manifest_sha256"] = sensitivity_job.canonical_sha256(
            sensitivity_job.without_manifest_hash(forged)
        )

        validation = sensitivity_job.validate_frozen_pair_manifest(forged)

        self.assertFalse(validation["valid"])
        self.assertIn("FROZEN_PAIR_MESH_HASH_MISMATCH", validation["blockers"])

    def test_mutated_pre_run_seed_fails_the_immutable_manifest_hash_check(self):
        manifest = self._manifest()
        manifest["baseline"]["case_seed_snapshot_sha256"] = HASH_F

        validation = sensitivity_job.validate_frozen_pair_manifest(manifest)

        self.assertFalse(validation["valid"])
        self.assertIn("FROZEN_PAIR_MANIFEST_HASH_MISMATCH", validation["blockers"])

    def test_self_consistent_seed_rewrite_requires_a_new_pair_identity(self):
        """Both seeded child inputs are part of the immutable pair identity."""
        manifest = self._manifest()
        original_job_id = manifest["job_id"]
        replacement_job_id = sensitivity_job.derive_frozen_pair_job_id(
            mesh_sha256=HASH_C,
            physical_tree=manifest["shared_input"]["physical_tree"],
            selector=manifest["selector"],
            baseline_case_seed_snapshot_sha256=HASH_F,
            variant_case_seed_snapshot_sha256=HASH_E,
        )
        self.assertNotEqual(original_job_id, replacement_job_id)

        manifest["baseline"]["case_seed_snapshot_sha256"] = HASH_F
        manifest["baseline"]["input_snapshot_sha256"] = sensitivity_job.canonical_sha256(
            sensitivity_job.without_input_snapshot_hash(manifest["baseline"])
        )
        manifest["pair_input_sha256"] = sensitivity_job.derive_frozen_pair_input_sha256(
            mesh_sha256=HASH_C,
            physical_tree=manifest["shared_input"]["physical_tree"],
            selector=manifest["selector"],
            baseline_case_seed_snapshot_sha256=HASH_F,
            variant_case_seed_snapshot_sha256=HASH_E,
        )
        self._rehash_manifest(manifest)

        validation = sensitivity_job.validate_frozen_pair_manifest(manifest)

        self.assertFalse(validation["valid"])
        self.assertIn("FROZEN_PAIR_JOB_ID_MISMATCH", validation["blockers"])

        manifest["job_id"] = replacement_job_id
        self._rehash_manifest(manifest)
        self.assertTrue(sensitivity_job.validate_frozen_pair_manifest(manifest)["valid"])

    def test_self_consistent_physical_tree_tamper_fails_physical_input_binding(self):
        manifest = self._manifest()
        tree = manifest["shared_input"]["physical_tree"]
        tree["entries"][0]["sha256"] = HASH_F
        tree["tree_sha256"] = self._canonical_sha256({"entries": tree["entries"]})
        self._rehash_manifest(manifest)

        validation = sensitivity_job.validate_frozen_pair_manifest(manifest)

        self.assertFalse(validation["valid"])
        self.assertIn(
            "FROZEN_PAIR_PHYSICAL_INPUT_BINDING_MISMATCH", validation["blockers"]
        )

    def test_self_consistent_selector_tamper_fails_physical_input_binding(self):
        manifest = self._manifest()
        selector = manifest["selector"]
        selector["z_max_agl_m"] = 1.7
        selector_payload = dict(selector)
        selector_payload.pop("selector_sha256")
        selector["selector_sha256"] = self._canonical_sha256(selector_payload)
        self._rehash_manifest(manifest)

        validation = sensitivity_job.validate_frozen_pair_manifest(manifest)

        self.assertFalse(validation["valid"])
        self.assertIn(
            "FROZEN_PAIR_PHYSICAL_INPUT_BINDING_MISMATCH", validation["blockers"]
        )

    def test_self_consistent_mesh_tamper_fails_physical_input_binding(self):
        manifest = self._manifest()
        manifest["shared_input"]["mesh_sha256"] = HASH_A
        for name in ("baseline", "variant"):
            manifest[name]["mesh_sha256"] = HASH_A
        self._rehash_manifest(manifest, sides=True)

        validation = sensitivity_job.validate_frozen_pair_manifest(manifest)

        self.assertFalse(validation["valid"])
        self.assertIn(
            "FROZEN_PAIR_PHYSICAL_INPUT_BINDING_MISMATCH", validation["blockers"]
        )

    def test_self_consistent_claimed_physical_input_tamper_fails_binding(self):
        manifest = self._manifest()
        manifest["shared_input"]["physical_input_sha256"] = HASH_A
        for name in ("baseline", "variant"):
            manifest[name]["physical_input_sha256"] = HASH_A
        self._rehash_manifest(manifest, sides=True)

        validation = sensitivity_job.validate_frozen_pair_manifest(manifest)

        self.assertFalse(validation["valid"])
        self.assertIn(
            "FROZEN_PAIR_PHYSICAL_INPUT_BINDING_MISMATCH", validation["blockers"]
        )

    def test_self_consistent_input_rewrite_cannot_keep_the_original_job_identity(self):
        """Catch removal of the immutable job-id-to-physical-input binding."""
        manifest = self._manifest()
        original_job_id = manifest["job_id"]
        tree = manifest["shared_input"]["physical_tree"]
        tree["entries"][0]["sha256"] = HASH_F
        tree["tree_sha256"] = self._canonical_sha256({"entries": tree["entries"]})

        replacement_physical_hash = self._physical_input_hash(manifest["selector"], tree)
        manifest["shared_input"]["physical_input_sha256"] = replacement_physical_hash
        for name in ("baseline", "variant"):
            manifest[name]["physical_input_sha256"] = replacement_physical_hash
        self._rehash_manifest(manifest, sides=True)

        validation = sensitivity_job.validate_frozen_pair_manifest(manifest)

        self.assertEqual(manifest["job_id"], original_job_id)
        self.assertFalse(validation["valid"])
        self.assertIn("FROZEN_PAIR_JOB_ID_MISMATCH", validation["blockers"])

    def test_safe_but_arbitrary_case_child_is_rejected(self):
        """Catch allowing a serial run to escape the planned baseline layout."""
        selector = self._selector()
        physical_tree = self._physical_tree()
        baseline = self._side(
            "serial-baseline-001", "stabilized_first_order_v1", HASH_A,
            case_child="arbitrary_child",
        )

        with self.assertRaises(sensitivity_job.NumericalSensitivityJobInputError) as caught:
            sensitivity_job.create_frozen_pair_manifest(
                job_id=self._expected_job_id(selector, physical_tree),
                selector=selector,
                mesh_sha256=HASH_C,
                physical_input_sha256=self._physical_input_hash(selector, physical_tree),
                physical_tree=physical_tree,
                baseline=baseline,
                variant=self._side(
                    "serial-variant-001", "design_limited_second_order_v1", HASH_E
                ),
                requested_ranks=1,
            )

        self.assertIn("FROZEN_PAIR_BASELINE_CASE_CHILD_MISMATCH", str(caught.exception))

    def test_role_swapped_safe_case_children_are_rejected(self):
        """Catch a first-order/second-order directory role swap before a run starts."""
        selector = self._selector()
        physical_tree = self._physical_tree()

        with self.assertRaises(sensitivity_job.NumericalSensitivityJobInputError) as caught:
            sensitivity_job.create_frozen_pair_manifest(
                job_id=self._expected_job_id(selector, physical_tree),
                selector=selector,
                mesh_sha256=HASH_C,
                physical_input_sha256=self._physical_input_hash(selector, physical_tree),
                physical_tree=physical_tree,
                baseline=self._side(
                    "serial-baseline-001", "stabilized_first_order_v1", HASH_A,
                    case_child="variant_second_order",
                ),
                variant=self._side(
                    "serial-variant-001", "design_limited_second_order_v1", HASH_E,
                    case_child="baseline_first_order",
                ),
                requested_ranks=1,
            )

        self.assertIn("FROZEN_PAIR_BASELINE_CASE_CHILD_MISMATCH", str(caught.exception))


class CentralSensitivityArtifactTests(unittest.TestCase):
    def _manifest(self):
        selector = {
            "contract": "occupied_volume_band.v1",
            "coordinate_source": "cell_center_m_agl",
            "z_min_agl_m": 0.1,
            "z_max_agl_m": 1.8,
        }
        physical_tree = FrozenPairManifestTests._physical_tree()
        return sensitivity_job.create_frozen_pair_manifest(
            job_id=FrozenPairManifestTests._expected_job_id(selector, physical_tree),
            selector=selector,
            mesh_sha256=HASH_C,
            physical_input_sha256=FrozenPairManifestTests._physical_input_hash(
                selector, physical_tree
            ),
            physical_tree=physical_tree,
            baseline={
                "run_id": "serial-baseline-001",
                "profile": "stabilized_first_order_v1",
                "case_child": "baseline_first_order",
                "processor_directories_present": False,
                "case_seed_snapshot_sha256": HASH_A,
            },
            variant={
                "run_id": "serial-variant-001",
                "profile": "design_limited_second_order_v1",
                "case_child": "variant_second_order",
                "processor_directories_present": False,
                "case_seed_snapshot_sha256": HASH_E,
            },
            requested_ranks=1,
        )

    @staticmethod
    def _occupied_qois(temperature_k, speed_m_s, selector_sha256):
        return {
            "occupied_zone_mean_temperature_k": temperature_k,
            "occupied_zone_mean_speed_m_s": speed_m_s,
            "selector_sha256": selector_sha256,
            "aggregation": "volume_weighted_cell_centers.v1",
            "scope": "selected_occupied_volume_band",
        }

    @staticmethod
    def _qoi_limits():
        return {
            "occupied_zone_mean_temperature_k": 0.5,
            "occupied_zone_mean_speed_m_s": 0.05,
            "exhaust_temperature_rise_k": 0.5,
        }

    def test_pending_job_stores_only_qoi_plan_not_result_values(self):
        manifest = self._manifest()
        artifact = sensitivity_job.build_cfd_numerical_sensitivity_job_manifest(
            manifest,
            qoi_limits=self._qoi_limits(),
        )

        self.assertIn("qoi_plan", artifact)
        self.assertNotIn("qoi_comparisons", artifact)
        self.assertNotIn("baseline_qois", artifact)
        self.assertNotIn("variant_qois", artifact)
        for definition in artifact["qoi_plan"]["definitions"]:
            self.assertEqual(set(definition), {"name", "limit"})

        forged = copy.deepcopy(artifact)
        forged["qoi_comparisons"] = [{
            "name": "occupied_zone_mean_temperature_k",
            "baseline": 294.1,
            "variant": 294.2,
            "absolute_difference": 0.1,
            "limit": 0.5,
            "passed": True,
        }]
        forged["job_manifest_sha256"] = sensitivity_job.canonical_sha256(
            sensitivity_job.without_job_manifest_hash(forged)
        )
        validation = sensitivity_job.validate_cfd_numerical_sensitivity_job_manifest(
            forged, trusted_pair_manifest=manifest
        )
        self.assertFalse(validation["structurally_valid"])
        self.assertIn(
            "NUMERICAL_SENSITIVITY_JOB_MANIFEST_FIELDS_INVALID",
            validation["blockers"],
        )

    def test_pending_job_builder_rejects_result_qoi_arguments(self):
        manifest = self._manifest()
        selector_sha256 = manifest["selector"]["selector_sha256"]

        with self.assertRaises(TypeError):
            sensitivity_job.build_cfd_numerical_sensitivity_job_manifest(
                manifest,
                qoi_limits=self._qoi_limits(),
                baseline_qois={
                    **self._occupied_qois(294.1, 0.20, selector_sha256),
                    "exhaust_temperature_rise_k": 5.0,
                },
                variant_qois={
                    **self._occupied_qois(294.2, 0.21, selector_sha256),
                    "exhaust_temperature_rise_k": 5.1,
                },
            )

    def test_central_job_manifest_references_frozen_snapshots_and_stays_pending_without_solver_evidence(self):
        manifest = self._manifest()

        artifact = sensitivity_job.build_cfd_numerical_sensitivity_job_manifest(
            manifest,
            qoi_limits=self._qoi_limits(),
        )

        validation = sensitivity_job.validate_cfd_numerical_sensitivity_job_manifest(
            artifact, trusted_pair_manifest=manifest
        )

        self.assertEqual(artifact["contract"], "cfd_numerical_sensitivity_job.v1")
        self.assertEqual(artifact["status"], "PENDING_SOLVER_EVIDENCE")
        self.assertEqual(
            artifact["final_result_target"]["contract"], "numerical_sensitivity.v1"
        )
        self.assertEqual(
            artifact["selector_sha256"], manifest["selector"]["selector_sha256"]
        )
        self.assertEqual(
            artifact["aggregation"]["occupied_zone"],
            "volume_weighted_cell_centers.v1",
        )
        for side_name in ("baseline", "variant"):
            side = artifact[side_name]
            self.assertIn("case_seed_snapshot_sha256", side)
            self.assertIn("input_snapshot_sha256", side)
            self.assertNotIn("run_hash", side)
            self.assertNotIn("result_snapshot", side)
            self.assertNotIn("solver_evidence", side)
        self.assertTrue(validation["structurally_valid"])
        self.assertFalse(validation["valid"])
        self.assertIn(
            "NUMERICAL_SENSITIVITY_SOLVER_EVIDENCE_PENDING",
            validation["blockers"],
        )

    def test_forged_case_local_json_without_a_trusted_pair_manifest_is_not_accepted(self):
        manifest = self._manifest()
        artifact = sensitivity_job.build_cfd_numerical_sensitivity_job_manifest(
            manifest,
            qoi_limits=self._qoi_limits(),
        )
        forged = copy.deepcopy(artifact)
        forged["status"] = "PASS"
        forged["case_local"] = True

        validation = sensitivity_job.validate_cfd_numerical_sensitivity_job_manifest(
            forged
        )

        self.assertFalse(validation["valid"])
        self.assertIn(
            "NUMERICAL_SENSITIVITY_TRUSTED_PAIR_MANIFEST_REQUIRED",
            validation["blockers"],
        )
        self.assertIn(
            "NUMERICAL_SENSITIVITY_INPUT_ONLY_NOT_PASSABLE",
            validation["blockers"],
        )
