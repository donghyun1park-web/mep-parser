import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import cfd_numerical_sensitivity_job as sensitivity_job
import cfd_numerical_sensitivity_runner as sensitivity_runner
import cfd_physics


def _file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_fingerprint(root):
    """A test-only mutation detector for the input mesh source."""
    root = Path(root)
    entries = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_file():
            entries.append({
                "path": path.relative_to(root).as_posix(),
                "sha256": _file_sha256(path),
            })
    payload = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class SerialSensitivityPreparationTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[1]
        self.tmp = tempfile.TemporaryDirectory(
            prefix=".test-serial-sensitivity-", dir=self.repo
        )
        self.root = Path(self.tmp.name)
        self.mesh_case = self._mesh_case(self.root / "mesh-source")

    def tearDown(self):
        self.tmp.cleanup()

    def _selector(self):
        geometry = self.root / "confirmed-geometry.json"
        zone = self.root / "confirmed-zone.json"
        geometry.write_text('{"unit":"m"}', encoding="utf-8")
        zone.write_text('{"closed":true}', encoding="utf-8")
        return {
            "contract": "occupied_volume_band.v1",
            "coordinate_source": "cell_center_m_agl",
            "z_min_agl_m": 0.1,
            "z_max_agl_m": 1.8,
            "validation_scope": "design_validation",
            "coordinate_system": "local_cartesian",
            "coordinate_unit": "m",
            "geometry_ref": {"path": str(geometry), "sha256": _file_sha256(geometry)},
            "zone_ref": {"path": str(zone), "sha256": _file_sha256(zone)},
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

    @staticmethod
    def _qoi_limits():
        return {
            "occupied_zone_mean_temperature_k": 0.5,
            "occupied_zone_mean_speed_m_s": 0.05,
            "exhaust_temperature_rise_k": 0.5,
        }

    @staticmethod
    def _mesh_case(case):
        case = Path(case)
        (case / "constant" / "polyMesh").mkdir(parents=True)
        surface = {
            "regions": [
                {"name": "wall", "role": "wall", "area_m2": 60.0},
                {
                    "name": "supply_A", "role": "supply", "area_m2": 0.125,
                    "airflow_cmh": 500.0, "design_normal": [0, 0, -1],
                },
                {
                    "name": "exhaust_A", "role": "exhaust", "area_m2": 0.125,
                    "airflow_cmh": 500.0,
                },
                {
                    "name": "equipment_HEATER_A", "role": "heat_source",
                    "source_element_ids": ["HEATER_A"], "area_m2": 5.0,
                    "source_id": "HEATER_A", "source_label": "heater A",
                    "source_ref": {"handle": "HT-A", "layer": "M-EQPM"},
                    "power_kw": 5.0, "convective_fraction": 0.8,
                    "evidence": "equipment_schedule:M03-001",
                    "source_type": "user_confirmed",
                },
            ]
        }
        mesh = {
            "status": "PASS",
            "occ_volume_m3": 30.0,
            "mesh": {"max_non_orthogonality": 10.0},
            "patches": [
                {"name": "wall", "mesh_patch_name": "airVolume_wall"},
                {"name": "supply_A", "mesh_patch_name": "airVolume_supply_A"},
                {"name": "exhaust_A", "mesh_patch_name": "airVolume_exhaust_A"},
                {
                    "name": "equipment_HEATER_A",
                    "mesh_patch_name": "airVolume_equipment_HEATER_A",
                },
            ],
        }
        (case / "surface_manifest.json").write_text(
            json.dumps(surface), encoding="utf-8"
        )
        (case / "mesh_manifest.json").write_text(
            json.dumps(mesh), encoding="utf-8"
        )
        (case / "mesh_input.json").write_text("{}", encoding="utf-8")
        return case

    def _prepare(self, target, settings=None):
        return sensitivity_runner.prepare_serial_sensitivity_pair(
            self.mesh_case,
            target,
            settings=dict(settings or {}),
            selector=self._selector(),
            qoi_limits=self._qoi_limits(),
        )

    def test_prepares_two_zero_flow_serial_cases_without_mutating_source(self):
        target = self.root / "prepared-study"
        before = _source_fingerprint(self.mesh_case)

        with mock.patch.object(
                sensitivity_runner.cfd_physics, "build_buoyant_case",
                wraps=cfd_physics.build_buoyant_case) as build:
            prepared = self._prepare(target, {"thermal_parallel_processes": 1})

        self.assertTrue(prepared["ok"], prepared)
        self.assertEqual(prepared["status"], "PENDING_SOLVER_EVIDENCE")
        self.assertEqual(_source_fingerprint(self.mesh_case), before)
        self.assertEqual(build.call_count, 2)
        self.assertEqual(
            [call.kwargs["settings"]["thermal_numerics_profile"]
             for call in build.call_args_list],
            ["stabilized_first_order_v1", "design_limited_second_order_v1"],
        )
        for call in build.call_args_list:
            self.assertNotIn("initial_case_dir", call.kwargs)
            self.assertEqual(call.kwargs["settings"]["thermal_parallel_processes"], 1)
            self.assertNotEqual(
                Path(call.kwargs["mesh_case_dir"]).resolve(), self.mesh_case.resolve(),
                "the case builder must never receive the user source directly",
            )

        pair = prepared["frozen_pair_manifest"]
        job = prepared["job_manifest"]
        pair_validation = sensitivity_job.validate_frozen_pair_manifest(pair)
        job_validation = sensitivity_job.validate_cfd_numerical_sensitivity_job_manifest(
            job, trusted_pair_manifest=pair
        )
        self.assertTrue(pair_validation["valid"], pair_validation)
        self.assertTrue(job_validation["structurally_valid"], job_validation)
        self.assertFalse(job_validation["valid"], job_validation)
        self.assertIn(
            "NUMERICAL_SENSITIVITY_SOLVER_EVIDENCE_PENDING",
            job_validation["blockers"],
        )

        for role, expected_profile in (
                ("baseline", "stabilized_first_order_v1"),
                ("variant", "design_limited_second_order_v1")):
            case = Path(prepared["cases"][role]["case_dir"])
            contract = json.loads((case / "thermal_input.json").read_text(
                encoding="utf-8"
            ))
            seed = json.loads((case / "case_seed_snapshot.v1.json").read_text(
                encoding="utf-8"
            ))
            self.assertEqual(contract["initialisation"]["mode"], "zero_flow")
            self.assertEqual(contract["settings"]["thermal_parallel_processes"], 1)
            self.assertEqual(contract["settings"]["thermal_numerics_profile"], expected_profile)
            self.assertEqual(seed["profile"], expected_profile)
            self.assertEqual(seed["case_seed_snapshot_sha256"],
                             pair[role]["case_seed_snapshot_sha256"])
            self.assertEqual(
                {entry["path"] for entry in seed["entries"]},
                {
                    "Allrun", "thermal_input.json",
                    "system/controlDict", "system/fvSchemes", "system/fvSolution",
                    "system/controlDict.transient", "system/fvSchemes.transient",
                    "system/fvSolution.transient",
                    "system/controlDict.precondition", "system/fvSchemes.precondition",
                    "system/fvSolution.precondition", "system/topoSetDict",
                },
            )
            self.assertTrue((case / "constant" / "polyMesh").is_dir())

        self.assertEqual(
            prepared["cases"]["baseline"]["physical_tree"],
            prepared["cases"]["variant"]["physical_tree"],
        )
        self.assertEqual(
            prepared["cases"]["baseline"]["physical_input_sha256"],
            prepared["cases"]["variant"]["physical_input_sha256"],
        )
        self.assertTrue((target / "frozen_pair_manifest.json").is_file())
        self.assertTrue((target / "cfd_numerical_sensitivity_job.v1.json").is_file())
        self.assertTrue((target / "serial_sensitivity_preparation.v1.json").is_file())
        self.assertRegex(
            prepared["preparation_manifest"]["mesh_source"]["source_tree_sha256"],
            r"^[0-9a-f]{64}$",
        )
        self.assertEqual(
            prepared["preparation_manifest"]["mesh_source"]["snapshot_path"],
            "mesh_source_snapshot",
        )

    def test_rejects_parallel_or_caller_profile_override_before_creating_output(self):
        invalid = (
            {"thermal_parallel_processes": 2},
            {"thermal_numerics_profile": "stabilized_first_order_v1"},
        )
        for index, settings in enumerate(invalid):
            with self.subTest(settings=settings):
                target = self.root / f"invalid-{index}"
                with self.assertRaisesRegex(
                        sensitivity_runner.NumericalSensitivityPreparationError,
                        "NUMERICAL_SENSITIVITY_"):
                    self._prepare(target, settings)
                self.assertFalse(target.exists())

    def test_rejects_unconfirmed_or_tampered_selector_evidence_before_building(self):
        basic = {
            "contract": "occupied_volume_band.v1",
            "coordinate_source": "cell_center_m_agl",
            "z_min_agl_m": 0.1,
            "z_max_agl_m": 1.8,
        }
        with self.assertRaisesRegex(
                sensitivity_runner.NumericalSensitivityPreparationError,
                "NUMERICAL_SENSITIVITY_CONFIRMED_SELECTOR_REQUIRED"):
            sensitivity_runner.prepare_serial_sensitivity_pair(
                self.mesh_case, self.root / "unconfirmed",
                selector=basic, qoi_limits=self._qoi_limits())

        confirmed = self._selector()
        Path(confirmed["zone_ref"]["path"]).write_text(
            '{"closed":false}', encoding="utf-8")
        with self.assertRaisesRegex(
                sensitivity_runner.NumericalSensitivityPreparationError,
                "NUMERICAL_SENSITIVITY_SELECTOR_EVIDENCE_HASH_MISMATCH"):
            sensitivity_runner.prepare_serial_sensitivity_pair(
                self.mesh_case, self.root / "tampered-selector",
                selector=confirmed, qoi_limits=self._qoi_limits())

    def test_rejects_unsafe_source_or_target_without_overwriting(self):
        nested_target = self.mesh_case / "must-not-write-here"
        with self.assertRaisesRegex(
                sensitivity_runner.NumericalSensitivityPreparationError,
                "NUMERICAL_SENSITIVITY_TARGET_UNSAFE"):
            self._prepare(nested_target)
        self.assertFalse(nested_target.exists())

        existing = self.root / "existing-study"
        existing.mkdir()
        marker = existing / "do-not-overwrite.txt"
        marker.write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(
                sensitivity_runner.NumericalSensitivityPreparationError,
                "NUMERICAL_SENSITIVITY_TARGET_EXISTS"):
            self._prepare(existing)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

        (self.mesh_case / "processor0").mkdir()
        processor_target = self.root / "processor-source"
        with self.assertRaisesRegex(
                sensitivity_runner.NumericalSensitivityPreparationError,
                "NUMERICAL_SENSITIVITY_PROCESSOR_DIRECTORIES_FORBIDDEN"):
            self._prepare(processor_target)
        self.assertFalse(processor_target.exists())

    def test_rejects_a_second_same_process_preparation_while_target_is_locked(self):
        target = self.root / "busy-study"
        lock = sensitivity_runner._target_lock_for(target.resolve())
        self.assertTrue(lock.acquire(blocking=False))
        try:
            with self.assertRaisesRegex(
                    sensitivity_runner.NumericalSensitivityPreparationError,
                    "NUMERICAL_SENSITIVITY_TARGET_BUSY"):
                self._prepare(target)
        finally:
            lock.release()
        self.assertFalse(target.exists())

    def test_rejects_missing_required_mesh_input_before_building(self):
        (self.mesh_case / "surface_manifest.json").unlink()
        target = self.root / "missing-manifest"

        with self.assertRaisesRegex(
                sensitivity_runner.NumericalSensitivityPreparationError,
                "NUMERICAL_SENSITIVITY_MESH_INPUT_INVALID"):
            self._prepare(target)

        self.assertFalse(target.exists())

    def test_rejects_a_profile_contaminated_physical_tree(self):
        target = self.root / "mismatched-physical-tree"
        actual = cfd_physics.build_buoyant_case

        def build_with_variant_mutation(mesh_case_dir, solver_case_dir, settings=None):
            built = actual(mesh_case_dir, solver_case_dir, settings=settings)
            if Path(solver_case_dir).name == "variant_second_order":
                with open(Path(solver_case_dir) / "0" / "T", "a", encoding="utf-8") as handle:
                    handle.write("// unexpected physical mutation\n")
            return built

        with mock.patch.object(
                sensitivity_runner.cfd_physics, "build_buoyant_case",
                side_effect=build_with_variant_mutation):
            with self.assertRaisesRegex(
                    sensitivity_runner.NumericalSensitivityPreparationError,
                    "NUMERICAL_SENSITIVITY_PHYSICAL_TREE_MISMATCH"):
                self._prepare(target)

        self.assertFalse(target.exists())

    def test_rejects_directory_where_a_required_physical_file_is_expected(self):
        target = self.root / "physical-file-replaced-with-directory"
        actual = cfd_physics.build_buoyant_case

        def build_with_bad_physical_node(mesh_case_dir, solver_case_dir, settings=None):
            built = actual(mesh_case_dir, solver_case_dir, settings=settings)
            if Path(solver_case_dir).name == "baseline_first_order":
                field = Path(solver_case_dir) / "0" / "T"
                field.unlink()
                field.mkdir()
            return built

        with mock.patch.object(
                sensitivity_runner.cfd_physics, "build_buoyant_case",
                side_effect=build_with_bad_physical_node):
            with self.assertRaisesRegex(
                    sensitivity_runner.NumericalSensitivityPreparationError,
                    "NUMERICAL_SENSITIVITY_REQUIRED_FILE_MISSING"):
                self._prepare(target)

        self.assertFalse(target.exists())

    def test_rejects_a_profile_contaminated_physical_input_snapshot(self):
        target = self.root / "mismatched-physical-input"
        actual = cfd_physics.build_buoyant_case

        def build_with_variant_physical_input_mutation(mesh_case_dir, solver_case_dir,
                                                       settings=None):
            built = actual(mesh_case_dir, solver_case_dir, settings=settings)
            if Path(solver_case_dir).name == "variant_second_order":
                path = Path(solver_case_dir) / "thermal_input.physical.v1.json"
                snapshot = json.loads(path.read_text(encoding="utf-8"))
                snapshot["settings"]["supply_temperature_k"] = 301.15
                body = dict(snapshot)
                body.pop("physical_input_sha256")
                snapshot["physical_input_sha256"] = cfd_physics._canonical_json_sha256(
                    body
                )
                path.write_text(json.dumps(snapshot), encoding="utf-8")
            return built

        with mock.patch.object(
                sensitivity_runner.cfd_physics, "build_buoyant_case",
                side_effect=build_with_variant_physical_input_mutation):
            with self.assertRaisesRegex(
                    sensitivity_runner.NumericalSensitivityPreparationError,
                    "NUMERICAL_SENSITIVITY_PHYSICAL_SNAPSHOT_MISMATCH"):
                self._prepare(target)

        self.assertFalse(target.exists())

    def test_rejects_stale_profile_free_snapshot_when_thermal_input_changes(self):
        target = self.root / "stale-physical-sidecar"
        actual = cfd_physics.build_buoyant_case

        def build_with_stale_sidecar(mesh_case_dir, solver_case_dir, settings=None):
            built = actual(mesh_case_dir, solver_case_dir, settings=settings)
            if Path(solver_case_dir).name == "variant_second_order":
                path = Path(solver_case_dir) / "thermal_input.json"
                contract = json.loads(path.read_text(encoding="utf-8"))
                contract["settings"]["supply_temperature_k"] = 301.15
                path.write_text(json.dumps(contract), encoding="utf-8")
            return built

        with mock.patch.object(
                sensitivity_runner.cfd_physics, "build_buoyant_case",
                side_effect=build_with_stale_sidecar):
            with self.assertRaisesRegex(
                    sensitivity_runner.NumericalSensitivityPreparationError,
                    "NUMERICAL_SENSITIVITY_PHYSICAL_SNAPSHOT_MISMATCH"):
                self._prepare(target)

        self.assertFalse(target.exists())

    def test_rejects_system_dictionary_not_generated_from_declared_profile(self):
        target = self.root / "tampered-system-dictionary"
        actual = cfd_physics.build_buoyant_case

        def build_with_tampered_system(mesh_case_dir, solver_case_dir, settings=None):
            built = actual(mesh_case_dir, solver_case_dir, settings=settings)
            if Path(solver_case_dir).name == "variant_second_order":
                path = Path(solver_case_dir) / "system" / "fvSchemes"
                path.write_text(
                    path.read_text(encoding="utf-8") + "// unrelated override\n",
                    encoding="utf-8",
                )
            return built

        with mock.patch.object(
                sensitivity_runner.cfd_physics, "build_buoyant_case",
                side_effect=build_with_tampered_system):
            with self.assertRaisesRegex(
                    sensitivity_runner.NumericalSensitivityPreparationError,
                    "NUMERICAL_SENSITIVITY_CASE_SEED_EXPECTATION_MISMATCH"):
                self._prepare(target)

        self.assertFalse(target.exists())

    def test_rejects_tampered_declared_numerics_even_when_system_is_regenerated(self):
        target = self.root / "tampered-declared-numerics"
        actual = cfd_physics.build_buoyant_case

        def build_with_tampered_numerics(mesh_case_dir, solver_case_dir, settings=None):
            built = actual(mesh_case_dir, solver_case_dir, settings=settings)
            if Path(solver_case_dir).name == "variant_second_order":
                case = Path(solver_case_dir)
                path = case / "thermal_input.json"
                contract = json.loads(path.read_text(encoding="utf-8"))
                contract["numerics"]["required_non_orthogonal_correctors"] = 99
                path.write_text(json.dumps(contract), encoding="utf-8")
                expected = cfd_physics.buoyant_initial_seed_expectations(contract)
                (case / "Allrun").write_text(expected["Allrun"], encoding="utf-8")
                for relative_path, text in expected["system"].items():
                    (case / relative_path).write_text(text, encoding="utf-8")
            return built

        with mock.patch.object(
                sensitivity_runner.cfd_physics, "build_buoyant_case",
                side_effect=build_with_tampered_numerics):
            with self.assertRaisesRegex(
                    sensitivity_runner.NumericalSensitivityPreparationError,
                    "NUMERICAL_SENSITIVITY_NUMERICS_CONTRACT_MISMATCH"):
                self._prepare(target)

        self.assertFalse(target.exists())

    def test_rejects_parallel_command_surface_in_allrun(self):
        target = self.root / "parallel-command"
        actual = cfd_physics.build_buoyant_case

        def build_with_parallel_command(mesh_case_dir, solver_case_dir, settings=None):
            built = actual(mesh_case_dir, solver_case_dir, settings=settings)
            if Path(solver_case_dir).name == "variant_second_order":
                path = Path(solver_case_dir) / "Allrun"
                path.write_text(
                    path.read_text(encoding="utf-8") + "mpirun -np 2 hostname\n",
                    encoding="utf-8",
                )
            return built

        with mock.patch.object(
                sensitivity_runner.cfd_physics, "build_buoyant_case",
                side_effect=build_with_parallel_command):
            with self.assertRaisesRegex(
                    sensitivity_runner.NumericalSensitivityPreparationError,
                    "NUMERICAL_SENSITIVITY_SERIAL_COMMAND_FORBIDDEN"):
                self._prepare(target)

        self.assertFalse(target.exists())

    def test_rejects_an_external_source_tree_mutation_during_preparation(self):
        target = self.root / "mutated-source"
        actual = cfd_physics.build_buoyant_case

        def build_with_source_mutation(mesh_case_dir, solver_case_dir, settings=None):
            built = actual(mesh_case_dir, solver_case_dir, settings=settings)
            if Path(solver_case_dir).name == "baseline_first_order":
                (self.mesh_case / "mesh_input.json").write_text(
                    '{"unexpected": "source rewrite"}', encoding="utf-8"
                )
            return built

        with mock.patch.object(
                sensitivity_runner.cfd_physics, "build_buoyant_case",
                side_effect=build_with_source_mutation):
            with self.assertRaisesRegex(
                    sensitivity_runner.NumericalSensitivityPreparationError,
                    "NUMERICAL_SENSITIVITY_SOURCE_MUTATED_DURING_PREPARATION"):
                self._prepare(target)

        self.assertFalse(target.exists())

    def test_rejects_source_snapshot_with_linked_input_before_any_case_build(self):
        linked_mesh_input = self.mesh_case / "mesh_input.json"
        real_mesh_input = self.mesh_case / "mesh_input.real.json"
        linked_mesh_input.rename(real_mesh_input)
        try:
            linked_mesh_input.symlink_to(real_mesh_input.name)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links are unavailable in this Windows test session")
        target = self.root / "linked-source"

        with mock.patch.object(
                sensitivity_runner.cfd_physics, "build_buoyant_case") as build:
            with self.assertRaisesRegex(
                    sensitivity_runner.NumericalSensitivityPreparationError,
                    "NUMERICAL_SENSITIVITY_MESH_INPUT_INVALID"):
                self._prepare(target)

        build.assert_not_called()
        self.assertFalse(target.exists())

    def test_rejects_any_non_zero_flow_or_restart_like_child_artifact(self):
        actual = cfd_physics.build_buoyant_case
        mutations = ("completed_mode", "restart_input", "numeric_time", "mapping_dir")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                target = self.root / f"bad-initialisation-{mutation}"

                def build_with_nonzero_flow_artifact(mesh_case_dir, solver_case_dir,
                                                     settings=None):
                    built = actual(mesh_case_dir, solver_case_dir, settings=settings)
                    case = Path(solver_case_dir)
                    if Path(solver_case_dir).name == "baseline_first_order":
                        if mutation == "completed_mode":
                            path = case / "thermal_input.json"
                            contract = json.loads(path.read_text(encoding="utf-8"))
                            contract["initialisation"]["mode"] = "completed_isothermal_fields"
                            contract["initialisation"]["source_case"] = "outside-case"
                            path.write_text(json.dumps(contract), encoding="utf-8")
                        elif mutation == "restart_input":
                            (case / "thermal_restart_input.json").write_text(
                                "{}", encoding="utf-8"
                            )
                        elif mutation == "numeric_time":
                            (case / "0.1").mkdir()
                        else:
                            (case / "initialMappingSource").mkdir()
                    return built

                with mock.patch.object(
                        sensitivity_runner.cfd_physics, "build_buoyant_case",
                        side_effect=build_with_nonzero_flow_artifact):
                    with self.assertRaisesRegex(
                            sensitivity_runner.NumericalSensitivityPreparationError,
                            "NUMERICAL_SENSITIVITY_INITIALISATION_UNSUPPORTED"):
                        self._prepare(target)
                self.assertFalse(target.exists())
