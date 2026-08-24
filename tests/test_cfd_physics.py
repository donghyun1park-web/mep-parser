import json
import os
import copy
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import cfd_mesh
import cfd_numerics
import cfd_occ
import cfd_physics
from tests.test_cfd_occ import _geometry


def _manifests(exhaust_cmh=500.0, with_heat=False):
    surface = {
        "regions": [
            {"name": "wall", "role": "wall", "area_m2": 60.0},
            {"name": "supply_A", "role": "supply", "area_m2": 0.125,
             "airflow_cmh": 500.0, "design_normal": [0, 0, -1]},
            {"name": "exhaust_A", "role": "exhaust", "area_m2": 0.125,
             "airflow_cmh": exhaust_cmh},
        ]
    }
    mesh = {
        "status": "PASS",
        "occ_volume_m3": 30.0,
        "patches": [
            {"name": "wall", "mesh_patch_name": "airVolume_wall"},
            {"name": "supply_A", "mesh_patch_name": "airVolume_supply_A"},
            {"name": "exhaust_A", "mesh_patch_name": "airVolume_exhaust_A"},
        ],
    }
    if with_heat:
        surface["regions"].append({
            "name": "equipment_HEATER_A", "role": "heat_source",
            "source_element_ids": ["HEATER_A"], "area_m2": 5.0,
            "source_id": "HEATER_A", "source_label": "로비 히터 A",
            "source_ref": {"handle": "HT-A", "layer": "M-EQPM"},
            "power_kw": 5.0, "convective_fraction": 0.8,
            "evidence": "equipment_schedule:M03-001",
            "source_type": "user_confirmed",
        })
        mesh["patches"].append({
            "name": "equipment_HEATER_A",
            "mesh_patch_name": "airVolume_equipment_HEATER_A",
        })
    return surface, mesh


class PhysicsBuildTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[1]

    def _mesh_case(self, root, exhaust_cmh=500.0, with_heat=False, cells=None,
                   max_nonorth=None):
        case = root / "mesh"
        (case / "constant" / "polyMesh").mkdir(parents=True)
        surface, mesh = _manifests(exhaust_cmh, with_heat=with_heat)
        if cells is not None or max_nonorth is not None:
            mesh["mesh"] = {}
            if cells is not None:
                mesh["mesh"]["cells"] = int(cells)
            if max_nonorth is not None:
                mesh["mesh"]["max_non_orthogonality"] = float(max_nonorth)
        (case / "surface_manifest.json").write_text(json.dumps(surface), encoding="utf-8")
        (case / "mesh_manifest.json").write_text(json.dumps(mesh), encoding="utf-8")
        (case / "mesh_input.json").write_text("{}", encoding="utf-8")
        return case

    def test_isothermal_case_uses_cmh_and_wall_functions(self):
        with tempfile.TemporaryDirectory(prefix=".test-physics-build-", dir=self.repo) as tmp:
            root = Path(tmp)
            mesh = self._mesh_case(root)
            result = cfd_physics.build_isothermal_case(mesh, root / "solver")
            u = (root / "solver" / "0" / "U").read_text(encoding="utf-8")
            omega = (root / "solver" / "0" / "omega").read_text(encoding="utf-8")
            control = (root / "solver" / "system" / "controlDict").read_text(encoding="utf-8")
            allrun = (root / "solver" / "Allrun").read_text(encoding="utf-8")
            contract = json.loads((root / "solver" / "physics_input.json").read_text(
                encoding="utf-8"
            ))
        self.assertTrue(result["ok"], result)
        self.assertIn("flowRateInletVelocity", u)
        self.assertIn("pressureInletOutletVelocity", u)
        self.assertIn("omegaWallFunction", omega)
        self.assertNotIn("functions", control)
        self.assertIn("simpleFoam > log.simpleFoam", allrun)
        self.assertEqual(contract["airflow"]["design_imbalance_ratio"], 0.0)

    def test_closed_room_rejects_unbalanced_design_airflow(self):
        with tempfile.TemporaryDirectory(prefix=".test-physics-balance-", dir=self.repo) as tmp:
            root = Path(tmp)
            result = cfd_physics.build_isothermal_case(
                self._mesh_case(root, exhaust_cmh=450.0), root / "solver"
            )
        self.assertFalse(result["ok"])
        self.assertIn("불균형", result["error"])

    def test_buoyant_case_uses_exposed_equipment_heat_and_tracks_radiation(self):
        with tempfile.TemporaryDirectory(prefix=".test-thermal-build-", dir=self.repo) as tmp:
            root = Path(tmp)
            result = cfd_physics.build_buoyant_case(
                self._mesh_case(root, with_heat=True), root / "thermal", {
                    "thermal_log_field_extrema": True,
                }
            )
            temperature = (root / "thermal" / "0" / "T").read_text(encoding="utf-8")
            control = (root / "thermal" / "system" / "controlDict").read_text(
                encoding="utf-8"
            )
            velocity = (root / "thermal" / "0" / "U").read_text(encoding="utf-8")
            pressure = (root / "thermal" / "0" / "p_rgh").read_text(encoding="utf-8")
            solution = (root / "thermal" / "system" / "fvSolution").read_text(
                encoding="utf-8"
            )
            schemes = (root / "thermal" / "system" / "fvSchemes").read_text(
                encoding="utf-8"
            )
            allrun = (root / "thermal" / "Allrun").read_text(encoding="utf-8")
            contract = json.loads((root / "thermal" / "thermal_input.json").read_text(
                encoding="utf-8"
            ))
        self.assertTrue(result["ok"], result)
        self.assertIn("zeroGradient", temperature)
        self.assertIn("airVolume_equipment_HEATER_A", temperature)
        self.assertIn("application buoyantBoussinesqPimpleFoam", control)
        self.assertIn("type flowRateInletVelocity", velocity)
        self.assertIn("volumetricFlowRate table", velocity)
        self.assertIn("(0.1 0.138888888889)", velocity)
        self.assertGreaterEqual(pressure.count("type fixedFluxPressure"), 2)
        self.assertIn("type prghTotalPressure", pressure)
        self.assertIn("p_rgh { solver PCG", solution)
        self.assertIn("div(phi,T) bounded Gauss upwind", schemes)
        self.assertIn("U { solver PBiCGStab", solution)
        self.assertIn("momentumPredictor no", solution)
        self.assertIn("nOuterCorrectors 1", solution)
        self.assertIn("nCorrectors 2", solution)
        self.assertIn("nNonOrthogonalCorrectors 0", solution)
        self.assertIn("rho rhok", pressure)
        self.assertIn("type fieldMinMax", control)
        self.assertIn("velocityExtrema", control)
        self.assertIn("fields (U)", control)
        self.assertIn("temperatureExtrema", control)
        self.assertIn("fields (T)", control)
        self.assertEqual(contract["heat"]["input_power_w"], 5000.0)
        self.assertEqual(contract["heat"]["applied_convective_power_w"], 4000.0)
        self.assertEqual(contract["heat"]["radiative_power_w"], 1000.0)
        self.assertEqual(contract["heat"]["excluded_radiative_power_w"], 1000.0)
        self.assertEqual(contract["heat_sources"][0]["radiative_fraction"], 0.2)
        self.assertEqual(contract["heat_sources"][0]["radiative_power_w"], 1000.0)
        self.assertEqual(
            contract["heat_sources"][0]["evidence"],
            "equipment_schedule:M03-001",
        )
        self.assertEqual(
            contract["heat_sources"][0]["source_type"], "user_confirmed"
        )
        self.assertEqual(contract["heat_sources"][0]["source_id"], "HEATER_A")
        self.assertEqual(contract["heat_sources"][0]["source_ref"]["handle"], "HT-A")
        self.assertFalse(contract["assumptions"]["radiation_modelled"])
        self.assertEqual(
            contract["heat"]["model"], "equipment_wall_adjacent_cells_v1"
        )

    def test_buoyant_case_rejects_conflicting_serialized_heat_power_fields(self):
        """OCC's serialized W values must not silently override the source split."""
        conflicts = {
            "convective_power_w": 3999.0,
            "radiative_power_w": 999.0,
            "excluded_radiative_power_w": 999.0,
        }
        for field, conflicting_value in conflicts.items():
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory(
                        prefix=".test-thermal-serialized-heat-", dir=self.repo) as tmp:
                    root = Path(tmp)
                    mesh = self._mesh_case(root, with_heat=True)
                    manifest_path = mesh / "surface_manifest.json"
                    surface = json.loads(manifest_path.read_text(encoding="utf-8"))
                    heat_source = next(
                        row for row in surface["regions"]
                        if row["role"] == "heat_source"
                    )
                    heat_source[field] = conflicting_value
                    manifest_path.write_text(json.dumps(surface), encoding="utf-8")

                    result = cfd_physics.build_buoyant_case(mesh, root / "thermal")

                self.assertFalse(result["ok"])
                self.assertIn(field, result["error"])

    def test_buoyant_case_forwards_occ_heat_source_identity(self):
        """Thermal evidence must keep the original reviewed DXF source identity."""
        with tempfile.TemporaryDirectory(
                prefix=".test-thermal-source-identity-", dir=self.repo) as tmp:
            root = Path(tmp)
            mesh = self._mesh_case(root, with_heat=True)
            manifest_path = mesh / "surface_manifest.json"
            surface = json.loads(manifest_path.read_text(encoding="utf-8"))
            heat_source = next(
                row for row in surface["regions"] if row["role"] == "heat_source"
            )
            heat_source.update({
                "source_id": "dxf:equipment:HEATER_A",
                "source_label": "EHP-1 equipment schedule",
                "source_ref": {"layer": "EHP", "handle": "1A2B"},
            })
            manifest_path.write_text(json.dumps(surface), encoding="utf-8")

            result = cfd_physics.build_buoyant_case(mesh, root / "thermal")

        self.assertTrue(result["ok"], result)
        source = result["thermal_input"]["heat_sources"][0]
        self.assertEqual(source["source_id"], "dxf:equipment:HEATER_A")
        self.assertEqual(source["source_label"], "EHP-1 equipment schedule")
        self.assertEqual(
            source["source_ref"], {"layer": "EHP", "handle": "1A2B"}
        )

    def test_buoyant_case_rejects_unvalidated_radiation_request(self):
        """A normal room case must not silently claim that radiation is modelled."""
        with tempfile.TemporaryDirectory(prefix=".test-radiation-guard-", dir=self.repo) as tmp:
            root = Path(tmp)
            result = cfd_physics.build_buoyant_case(
                self._mesh_case(root, with_heat=True), root / "thermal", {
                    "radiation_modelled": True,
                }
            )

        self.assertFalse(result["ok"])
        self.assertIn("복사", result["error"])
        self.assertIn("benchmark", result["error"])

    def test_buoyant_case_preserves_confirmed_dxf_override_marker(self):
        with tempfile.TemporaryDirectory(prefix=".test-thermal-dxf-override-", dir=self.repo) as tmp:
            root = Path(tmp)
            mesh = self._mesh_case(root, with_heat=True)
            surface_path = mesh / "surface_manifest.json"
            surface = json.loads(surface_path.read_text(encoding="utf-8"))
            next(item for item in surface["regions"]
                 if item["role"] == "heat_source")["override_of_dxf"] = True
            surface_path.write_text(json.dumps(surface), encoding="utf-8")

            result = cfd_physics.build_buoyant_case(mesh, root / "thermal")
            contract = json.loads((root / "thermal" / "thermal_input.json").read_text(
                encoding="utf-8"
            ))

        self.assertTrue(result["ok"], result)
        self.assertTrue(contract["heat_sources"][0]["override_of_dxf"])

    def test_body_fitted_heat_contract_rejects_duplicate_positive_source_id(self):
        surface, mesh = _manifests(with_heat=True)
        duplicate = copy.deepcopy(next(
            item for item in surface["regions"] if item["role"] == "heat_source"
        ))
        duplicate["name"] = "equipment_HEATER_B"
        # Deliberately retain HEATER_A source identity despite a new patch.
        duplicate["source_element_ids"] = ["HEATER_A"]
        duplicate["source_id"] = "HEATER_A"
        surface["regions"].append(duplicate)
        mesh["patches"].append({
            "name": "equipment_HEATER_B",
            "mesh_patch_name": "airVolume_equipment_HEATER_B",
        })

        with self.assertRaisesRegex(ValueError, "duplicate source_id"):
            cfd_physics._heat_source_contract(
                surface, mesh, cfd_physics.DEFAULT_SETTINGS
            )

    def test_body_fitted_heat_contract_requires_explicit_user_confirmed_type(self):
        surface, mesh = _manifests(with_heat=True)
        heat_source = next(
            item for item in surface["regions"] if item["role"] == "heat_source"
        )
        del heat_source["source_type"]

        with self.assertRaisesRegex(ValueError, "source_type"):
            cfd_physics._heat_source_contract(
                surface, mesh, cfd_physics.DEFAULT_SETTINGS
            )

    def test_buoyant_case_records_mesh_dependent_screening_numerics(self):
        with tempfile.TemporaryDirectory(prefix=".test-thermal-numerics-", dir=self.repo) as tmp:
            root = Path(tmp)
            result = cfd_physics.build_buoyant_case(
                self._mesh_case(root, with_heat=True, max_nonorth=54), root / "thermal"
            )
            contract = result["thermal_input"]
            solution = (root / "thermal" / "system" / "fvSolution").read_text(
                encoding="utf-8"
            )

        self.assertTrue(result["ok"], result)
        self.assertEqual(contract["numerics"]["status"], "SCREENING_ONLY")
        self.assertEqual(contract["numerics"]["required_non_orthogonal_correctors"], 2)
        self.assertIn("nNonOrthogonalCorrectors 2", solution)

    def test_buoyant_case_builds_limited_second_order_candidate_profile(self):
        with tempfile.TemporaryDirectory(prefix=".test-thermal-numerics-", dir=self.repo) as tmp:
            root = Path(tmp)
            result = cfd_physics.build_buoyant_case(
                self._mesh_case(root, with_heat=True, max_nonorth=54), root / "thermal", {
                    "thermal_numerics_profile": "design_limited_second_order_v1",
                }
            )
            contract = result["thermal_input"]
            schemes = (root / "thermal" / "system" / "fvSchemes").read_text(
                encoding="utf-8"
            )
            allrun = (root / "thermal" / "Allrun").read_text(encoding="utf-8")

        self.assertTrue(result["ok"], result)
        self.assertEqual(contract["numerics"]["status"], "CANDIDATE")
        self.assertIn("div(phi,U) bounded Gauss linearUpwind grad(U)", schemes)
        self.assertIn("div(phi,T) bounded Gauss limitedLinear 1", schemes)
        self.assertIn("laplacianSchemes { default Gauss linear limited 0.5; }", schemes)
        self.assertIn("postProcess -func yPlus -latestTime > log.yPlus", allrun)

    def test_buoyant_case_writes_profile_free_physical_input_snapshot(self):
        """Numerical-profile changes must not alter the frozen physical input."""
        with tempfile.TemporaryDirectory(
                prefix=".test-thermal-physical-input-", dir=self.repo) as tmp:
            root = Path(tmp)
            mesh = self._mesh_case(root, with_heat=True, max_nonorth=54)
            baseline = cfd_physics.build_buoyant_case(
                mesh, root / "baseline", {
                    "thermal_numerics_profile": "stabilized_first_order_v1",
                }
            )
            variant = cfd_physics.build_buoyant_case(
                mesh, root / "variant", {
                    "thermal_numerics_profile": "design_limited_second_order_v1",
                }
            )
            baseline_snapshot = json.loads((
                root / "baseline" / "thermal_input.physical.v1.json"
            ).read_text(encoding="utf-8"))
            variant_snapshot = json.loads((
                root / "variant" / "thermal_input.physical.v1.json"
            ).read_text(encoding="utf-8"))

        self.assertTrue(baseline["ok"], baseline)
        self.assertTrue(variant["ok"], variant)
        self.assertEqual(baseline_snapshot, variant_snapshot)
        self.assertEqual(
            baseline_snapshot["contract"], "thermal_input.physical.v1"
        )
        self.assertNotIn("created_at", baseline_snapshot)
        self.assertNotIn("numerics", baseline_snapshot)
        self.assertNotIn(
            "thermal_numerics_profile", baseline_snapshot["settings"]
        )
        self.assertEqual(
            baseline_snapshot["initialisation"],
            {
                "mode": "zero_flow",
                "pressure_mapping": None,
                "boussinesq_preconditioning_iterations": 0,
            },
        )
        self.assertNotIn("source_case", baseline_snapshot["initialisation"])
        self.assertNotIn("source_time", baseline_snapshot["initialisation"])
        snapshot_body = dict(baseline_snapshot)
        snapshot_sha256 = snapshot_body.pop("physical_input_sha256")
        self.assertEqual(
            snapshot_sha256,
            cfd_physics._canonical_json_sha256(snapshot_body),
        )

    def test_buoyant_case_exposes_deterministic_initial_seed_expectations(self):
        """A paired-study runner must be able to rederive its allowed seed."""
        with tempfile.TemporaryDirectory(
                prefix=".test-thermal-seed-expectations-", dir=self.repo) as tmp:
            root = Path(tmp)
            built = cfd_physics.build_buoyant_case(
                self._mesh_case(root, with_heat=True, max_nonorth=54),
                root / "thermal", {
                    "thermal_numerics_profile": "design_limited_second_order_v1",
                    "thermal_parallel_processes": 1,
                },
            )
            case = root / "thermal"
            expected = cfd_physics.buoyant_initial_seed_expectations(
                built["thermal_input"]
            )
            self.assertTrue(built["ok"], built)
            self.assertEqual(expected["initialisation"], "zero_flow")
            self.assertEqual(
                expected["profile"], "design_limited_second_order_v1"
            )
            self.assertEqual(expected["Allrun"], (case / "Allrun").read_text(
                encoding="utf-8"
            ))
            for relative_path, text in expected["system"].items():
                self.assertEqual(
                    text,
                    (case / relative_path).read_text(encoding="utf-8"),
                )
            self.assertEqual(
                cfd_physics.profile_free_thermal_input_snapshot(
                    built["thermal_input"]
                ),
                json.loads((case / "thermal_input.physical.v1.json").read_text(
                    encoding="utf-8"
                )),
            )

    def test_buoyant_physical_input_snapshot_changes_for_temperature_or_heat(self):
        with tempfile.TemporaryDirectory(
                prefix=".test-thermal-physical-change-", dir=self.repo) as tmp:
            root = Path(tmp)
            mesh = self._mesh_case(root, with_heat=True)
            baseline = cfd_physics.build_buoyant_case(mesh, root / "baseline")
            warmer_supply = cfd_physics.build_buoyant_case(
                mesh, root / "warmer-supply", {"supply_temperature_k": 296.15}
            )
            reduced_heat = cfd_physics.build_buoyant_case(
                mesh, root / "reduced-heat", {"thermal_heat_scale": 0.5}
            )
            snapshots = {
                name: json.loads((root / name / "thermal_input.physical.v1.json").read_text(
                    encoding="utf-8"
                ))
                for name in ("baseline", "warmer-supply", "reduced-heat")
            }

        self.assertTrue(baseline["ok"], baseline)
        self.assertTrue(warmer_supply["ok"], warmer_supply)
        self.assertTrue(reduced_heat["ok"], reduced_heat)
        self.assertNotEqual(
            snapshots["baseline"]["physical_input_sha256"],
            snapshots["warmer-supply"]["physical_input_sha256"],
        )
        self.assertNotEqual(
            snapshots["baseline"]["physical_input_sha256"],
            snapshots["reduced-heat"]["physical_input_sha256"],
        )
        self.assertEqual(
            snapshots["warmer-supply"]["settings"]["supply_temperature_k"],
            296.15,
        )
        self.assertEqual(
            snapshots["reduced-heat"]["heat"]["applied_convective_power_w"],
            2000.0,
        )

    def test_design_profile_locks_initial_and_restart_control_dicts(self):
        settings = dict(cfd_physics.DEFAULT_SETTINGS)
        settings["thermal_numerics_profile"] = "design_limited_second_order_v1"

        initial = cfd_physics._thermal_control_dict(settings)
        restart = cfd_physics._thermal_restart_control_dict(settings, 0.1, 1.0)

        self.assertIn("runTimeModifiable false;", initial)
        self.assertIn("runTimeModifiable false;", restart)
        self.assertIn("startFrom latestTime;", restart)

    def test_screening_profile_keeps_initial_and_restart_control_dicts_modifiable(self):
        settings = dict(cfd_physics.DEFAULT_SETTINGS)
        settings["thermal_numerics_profile"] = "stabilized_first_order_v1"

        initial = cfd_physics._thermal_control_dict(settings)
        restart = cfd_physics._thermal_restart_control_dict(settings, 0.1, 1.0)

        self.assertIn("runTimeModifiable true;", initial)
        self.assertIn("runTimeModifiable true;", restart)

    def test_buoyant_case_rejects_mesh_without_confirmed_heat_source(self):
        with tempfile.TemporaryDirectory(prefix=".test-thermal-missing-", dir=self.repo) as tmp:
            root = Path(tmp)
            result = cfd_physics.build_buoyant_case(
                self._mesh_case(root), root / "thermal"
            )
        self.assertFalse(result["ok"])
        self.assertIn("발열 장비", result["error"])

    def test_g2_zero_stage_disables_flow_gravity_heat_and_preconditioning(self):
        with tempfile.TemporaryDirectory(prefix=".test-g2-zero-", dir=self.repo) as tmp:
            root = Path(tmp)
            result = cfd_physics.build_buoyant_case(
                self._mesh_case(root, with_heat=True), root / "thermal", {
                    "thermal_flow_scale": 0,
                    "thermal_gravity_scale": 0,
                    "thermal_heat_scale": 0,
                    "thermal_preconditioning_iterations": 0,
                }
            )
            self.assertTrue(result["ok"], result)
            u = (root / "thermal" / "0" / "U").read_text(encoding="utf-8")
            gravity = (root / "thermal" / "constant" / "g").read_text(encoding="utf-8")
            options = (root / "thermal" / "constant" / "fvOptions").read_text(
                encoding="utf-8"
            )
            allrun = (root / "thermal" / "Allrun").read_text(encoding="utf-8")
            contract = result["thermal_input"]
        self.assertIn("volumetricFlowRate constant 0;", u)
        self.assertIn("value (0 0 0);", gravity)
        self.assertIn("T (0 0)", options)
        self.assertNotIn("buoyant pressure-flow preconditioning", allrun)
        self.assertEqual(contract["heat"]["applied_convective_power_w"], 0.0)
        self.assertEqual(contract["heat"]["requested_convective_power_w"], 4000.0)
        self.assertEqual(
            contract["heat"]["deferred_convective_power_w"], 4000.0
        )
        self.assertEqual(
            contract["heat_sources"][0]["applied_convective_power_w"], 0.0
        )
        self.assertEqual(contract["condition_matrix"]["flow_scale"], 0.0)

    def test_heat_scale_keeps_per_source_and_total_applied_power_in_sync(self):
        with tempfile.TemporaryDirectory(prefix=".test-thermal-heat-scale-", dir=self.repo) as tmp:
            root = Path(tmp)
            result = cfd_physics.build_buoyant_case(
                self._mesh_case(root, with_heat=True), root / "thermal", {
                    "thermal_heat_scale": 0.5,
                }
            )
            self.assertTrue(result["ok"], result)
            contract = result["thermal_input"]
            options = (root / "thermal" / "constant" / "fvOptions").read_text(
                encoding="utf-8"
            )

        source = contract["heat_sources"][0]
        self.assertEqual(source["requested_convective_power_w"], 4000.0)
        self.assertEqual(source["applied_convective_power_w"], 2000.0)
        self.assertEqual(source["deferred_convective_power_w"], 2000.0)
        self.assertEqual(contract["heat"]["input_power_w"], 5000.0)
        self.assertEqual(contract["heat"]["requested_convective_power_w"], 4000.0)
        self.assertEqual(contract["heat"]["applied_convective_power_w"], 2000.0)
        self.assertEqual(contract["heat"]["deferred_convective_power_w"], 2000.0)
        self.assertEqual(contract["heat"]["excluded_radiative_power_w"], 1000.0)
        self.assertIn(f"T ({2000.0 / (1.204 * 1006.0):.12g} 0)", options)

    def test_scaled_buoyant_run_is_not_design_ready(self):
        with tempfile.TemporaryDirectory(prefix=".test-thermal-scale-gate-", dir=self.repo) as tmp:
            root = Path(tmp)
            built = cfd_physics.build_buoyant_case(
                self._mesh_case(root, with_heat=True), root / "thermal", {
                    "thermal_heat_scale": 0.5,
                }
            )
            case = root / "thermal"
            (case / "log.buoyantBoussinesqPimpleFoam").write_text(
                "Time = 0.1\nCourant Number mean: 0.1 max: 0.8\nEnd\n",
                encoding="ascii",
            )
            metrics = {
                "available": True, "latest_time_s": 0.1,
                "minimum_k": 293.15, "maximum_k": 294.0, "mean_k": 293.5,
                "temperature_rise_k": 0.85, "energy_closure_ratio": 1.0,
                "exhaust_sensible_power_w": 2000.0,
            }
            with mock.patch.object(cfd_physics, "thermal_result_metrics", return_value=metrics):
                manifest = cfd_physics.evaluate_buoyant_run(
                    case, {"ok": True}, built["thermal_input"]
                )

        self.assertEqual(manifest["status"], "WARN")
        self.assertFalse(manifest["design_ready"])
        self.assertIn("CONDITION_MATRIX_NOT_FULL", manifest["warnings"])

    def test_buoyant_case_bundles_different_mesh_isothermal_mapping(self):
        with tempfile.TemporaryDirectory(prefix=".test-thermal-map-", dir=self.repo) as tmp:
            root = Path(tmp)
            target_mesh = self._mesh_case(root, with_heat=True)
            source = root / "quick-steady"
            (source / "constant" / "polyMesh").mkdir(parents=True)
            (source / "system").mkdir()
            (source / "system" / "controlDict").write_text(
                "FoamFile{}\nstartFrom latestTime;\n", encoding="ascii"
            )
            (source / "mesh_manifest.json").write_text(
                '{"profile":"quick","status":"PASS"}', encoding="ascii"
            )
            time_dir = source / "30"
            time_dir.mkdir()
            dimensions = {
                "U": "[0 1 -1 0 0 0 0]",
                "p": "[0 2 -2 0 0 0 0]",
                "k": "[0 2 -2 0 0 0 0]",
                "omega": "[0 0 -1 0 0 0 0]",
                "nut": "[0 2 -1 0 0 0 0]",
            }
            for field, unit in dimensions.items():
                class_name = "volVectorField" if field == "U" else "volScalarField"
                value = "(1 0 0)" if field == "U" else "1"
                (time_dir / field).write_text(
                    f"FoamFile\n{{ class {class_name}; object {field}; }}\n"
                    f"dimensions {unit};\ninternalField uniform {value};\n"
                    "boundaryField {}\n",
                    encoding="ascii",
                )
            result = cfd_physics.build_buoyant_case(
                target_mesh, root / "thermal", initial_case_dir=source
            )
            self.assertTrue(result["ok"], result)
            case = root / "thermal"
            allrun = (case / "Allrun").read_text(encoding="utf-8")
            mapped_pressure = (case / "initialMappingSource" / "30" / "p_rgh").read_text(
                encoding="utf-8"
            )
            target_velocity = (case / "0" / "U").read_text(encoding="utf-8")
            contract = json.loads((case / "thermal_input.json").read_text(encoding="utf-8"))
        self.assertIn("mapFields initialMappingSource", allrun)
        self.assertIn("-mapMethod interpolate", allrun)
        self.assertIn("object p_rgh;", mapped_pressure)
        self.assertIn("flowRateInletVelocity", target_velocity)
        self.assertIn("volumetricFlowRate constant 0.138888888889", target_velocity)
        self.assertNotIn("volumetricFlowRate table", target_velocity)
        self.assertEqual(contract["initialisation"]["mode"], "mapped_isothermal_fields")
        self.assertEqual(
            contract["initialisation"]["pressure_mapping"],
            "mapFields_interpolate_p_and_p_rgh",
        )

    def test_buoyant_result_reports_temperature_and_energy_closure(self):
        with tempfile.TemporaryDirectory(prefix=".test-thermal-result-", dir=self.repo) as tmp:
            root = Path(tmp)
            built = cfd_physics.build_buoyant_case(
                self._mesh_case(root, with_heat=True), root / "thermal"
            )
            self.assertTrue(built["ok"], built)
            case = root / "thermal"
            (case / "log.buoyantBoussinesqPimpleFoam").write_text("""
Time = 0.1
Courant Number mean: 0.1 max: 0.8
smoothSolver: Solving for T, Initial residual = 0.01, Final residual = 1e-7, No Iterations 2
End
""", encoding="ascii")
            time_dir = case / "0.1"
            time_dir.mkdir()
            exhaust_t = 293.15 + 4000.0 / (1.204 * 1006.0 * (500.0 / 3600.0))
            (time_dir / "T").write_text(f"""FoamFile{{}}
internalField nonuniform List<scalar>
2
(
293.15
300
);
boundaryField
{{
    airVolume_exhaust_A
    {{
        type inletOutlet;
        value nonuniform List<scalar>
        1
        (
        {exhaust_t}
        );
    }}
}}
""", encoding="ascii")
            manifest = cfd_physics.evaluate_buoyant_run(
                case, {"ok": True}, built["thermal_input"]
            )
        self.assertEqual(manifest["status"], "WARN")
        self.assertFalse(manifest["design_ready"])
        self.assertEqual(manifest["numerical_quality"]["status"], "SCREENING_ONLY")
        self.assertIn("NUMERICS_SCREENING_ONLY", manifest["warnings"])
        self.assertAlmostEqual(manifest["thermal"]["energy_closure_ratio"], 1.0)
        self.assertEqual(manifest["thermal"]["maximum_k"], 300.0)
        self.assertNotIn("TEMPERATURE_UNDERSHOOT", manifest["warnings"])

    def test_second_order_candidate_needs_actual_numerical_evidence(self):
        with tempfile.TemporaryDirectory(prefix=".test-thermal-numerical-gate-", dir=self.repo) as tmp:
            root = Path(tmp)
            built = cfd_physics.build_buoyant_case(
                self._mesh_case(root, with_heat=True, max_nonorth=54), root / "thermal", {
                    "thermal_numerics_profile": "design_limited_second_order_v1",
                }
            )
            case = root / "thermal"
            (case / "log.buoyantBoussinesqPimpleFoam").write_text(
                "Time = 0.1\nCourant Number mean: 0.1 max: 0.8\n"
                "smoothSolver: Solving for T, Initial residual = 0.01, Final residual = 1e-7, No Iterations 2\nEnd\n",
                encoding="ascii",
            )
            metrics = {
                "available": True, "latest_time_s": 0.1,
                "minimum_k": 293.0, "maximum_k": 294.0, "mean_k": 293.5,
                "temperature_rise_k": 0.85, "energy_closure_ratio": 1.0,
                "energy_closure_basis": "solver_positive_phi_and_owner_cell_temperature",
                "exhaust_sensible_power_w": 4000.0,
            }
            with mock.patch.object(cfd_physics, "thermal_result_metrics", return_value=metrics):
                manifest = cfd_physics.evaluate_buoyant_run(
                    case, {"ok": True}, built["thermal_input"]
                )

        self.assertEqual(manifest["status"], "WARN")
        self.assertFalse(manifest["design_ready"])
        self.assertEqual(manifest["numerical_quality"]["status"], "NOT_EVALUATED")
        self.assertIn("NUMERICAL_EVIDENCE_MISSING", manifest["warnings"])
        self.assertIn("NUMERICAL_SENSITIVITY_PENDING", manifest["warnings"])

    def test_buoyant_evaluator_uses_effective_restart_numerics(self):
        """Continuation evidence must describe the scheme actually rerun."""
        with tempfile.TemporaryDirectory(prefix=".test-thermal-restart-numerics-", dir=self.repo) as tmp:
            root = Path(tmp)
            built = cfd_physics.build_buoyant_case(
                self._mesh_case(root, with_heat=True, max_nonorth=54), root / "thermal", {
                    "thermal_numerics_profile": "design_limited_second_order_v1",
                }
            )
            case = root / "thermal"
            (case / "log.buoyantBoussinesqPimpleFoam").write_text(
                "Time = 0.1\nCourant Number mean: 0.1 max: 0.8\nEnd\n",
                encoding="ascii",
            )
            restart_settings = dict(built["thermal_input"]["settings"])
            restart_settings["thermal_numerics_profile"] = "stabilized_first_order_v1"
            restart_numerics = cfd_numerics.thermal_numerics_contract(
                json.loads((case / "mesh_manifest.json").read_text(encoding="utf-8")),
                restart_settings,
            )
            metrics = {
                "available": True, "latest_time_s": 0.1,
                "minimum_k": 293.0, "maximum_k": 294.0, "mean_k": 293.5,
                "temperature_rise_k": 0.85, "energy_closure_ratio": 1.0,
                "energy_closure_basis": "solver_positive_phi_and_owner_cell_temperature",
                "exhaust_sensible_power_w": 4000.0,
            }
            with mock.patch.object(cfd_physics, "thermal_result_metrics", return_value=metrics):
                manifest = cfd_physics.evaluate_buoyant_run(
                    case, {"ok": True}, built["thermal_input"],
                    effective_settings=restart_settings,
                    effective_numerics=restart_numerics,
                )

        self.assertEqual(manifest["numerical_quality"]["profile"], "stabilized_first_order_v1")
        self.assertEqual(manifest["numerical_quality"]["status"], "SCREENING_ONLY")
        self.assertEqual(manifest["effective_numerics"]["profile"], "stabilized_first_order_v1")

    def test_buoyant_evaluator_records_restart_and_system_provenance(self):
        with tempfile.TemporaryDirectory(prefix=".test-thermal-restart-provenance-", dir=self.repo) as tmp:
            root = Path(tmp)
            built = cfd_physics.build_buoyant_case(
                self._mesh_case(root, with_heat=True), root / "thermal"
            )
            self.assertTrue(built["ok"], built)
            case = root / "thermal"
            (case / "log.buoyantBoussinesqPimpleFoam").write_text(
                "Time = 0.1\nCourant Number mean: 0.1 max: 0.8\nEnd\n",
                encoding="ascii",
            )
            settings = dict(built["thermal_input"]["settings"])
            numerics = dict(built["thermal_input"]["numerics"])
            restart_path = case / "thermal_restart_input.json"
            restart_path.write_text(json.dumps({
                "contract": "thermal_restart_input.v1",
                "start_time_s": 0.1,
                "duration_s": 1.0,
                "settings": settings,
                "thermal_numerics": numerics,
                "thermal_input_sha256": cfd_physics._sha256(
                    case / "thermal_input.json"
                ),
            }), encoding="utf-8")
            (case / "system" / "controlDict").write_text(
                cfd_physics._thermal_restart_control_dict(settings, 0.1, 1.0),
                encoding="utf-8", newline="\n",
            )
            metrics = {
                "available": True, "latest_time_s": 0.1,
                "minimum_k": 293.0, "maximum_k": 294.0, "mean_k": 293.5,
                "temperature_rise_k": 0.85, "energy_closure_ratio": 1.0,
                "energy_closure_basis": "solver_positive_phi_and_owner_cell_temperature",
                "exhaust_sensible_power_w": 4000.0,
            }
            with mock.patch.object(cfd_physics, "thermal_result_metrics", return_value=metrics):
                manifest = cfd_physics.evaluate_buoyant_run(
                    case, {"ok": True}, built["thermal_input"],
                    effective_settings=settings, effective_numerics=numerics,
                    restart_input_path=restart_path,
                )
            restart_hash = cfd_physics._sha256(restart_path)

        provenance = manifest["input"]["numerical_provenance"]
        self.assertEqual(provenance["contract"], "thermal_numerics_provenance.v1")
        self.assertEqual(provenance["source"], "thermal_restart_input")
        self.assertEqual(
            provenance["thermal_restart_input_sha256"],
            restart_hash,
        )
        self.assertEqual(set(provenance["system"]), {"controlDict", "fvSchemes", "fvSolution"})
        self.assertTrue(all(len(value) == 64 for value in provenance["system"].values()))
        self.assertEqual(provenance["expected_system"], provenance["system"])

    def test_initial_run_uses_pre_run_numerical_provenance_snapshot(self):
        with tempfile.TemporaryDirectory(prefix=".test-thermal-provenance-snapshot-", dir=self.repo) as tmp:
            root = Path(tmp)
            built = cfd_physics.build_buoyant_case(
                self._mesh_case(root, with_heat=True), root / "thermal"
            )
            self.assertTrue(built["ok"], built)
            case = root / "thermal"
            original_hash = cfd_physics._sha256(case / "system" / "fvSchemes")

            def mutate_after_snapshot(*_args, **_kwargs):
                (case / "system" / "fvSchemes").write_text(
                    "divSchemes { default upwind; }\n", encoding="ascii"
                )
                return {"ok": True}

            expected = {"status": "WARN", "errors": [], "warnings": []}
            with mock.patch.object(cfd_physics, "run_case", side_effect=mutate_after_snapshot), \
                    mock.patch.object(cfd_physics, "evaluate_buoyant_run", return_value=expected) as evaluate, \
                    mock.patch.object(cfd_physics, "_attach_thermal_progress", return_value=expected), \
                    mock.patch.object(cfd_physics, "_build_body_fitted_results", return_value={"ok": True}):
                result = cfd_physics.run_buoyant_case(case)

            snapshot = evaluate.call_args.kwargs["numerical_provenance"]
            changed_hash = cfd_physics._sha256(case / "system" / "fvSchemes")

        self.assertTrue(result["ok"])
        self.assertEqual(snapshot["system"]["fvSchemes"], original_hash)
        self.assertNotEqual(snapshot["system"]["fvSchemes"], changed_hash)
        self.assertEqual(snapshot["expected_system"]["fvSchemes"], original_hash)

    def test_initial_buoyant_run_uses_only_its_initial_input(self):
        """A first run has no restart contract to leak into its evidence."""
        with tempfile.TemporaryDirectory(prefix=".test-initial-thermal-run-", dir=self.repo) as tmp:
            case = Path(tmp)
            (case / "thermal_input.json").write_text("{}", encoding="utf-8")
            expected = {"status": "WARN", "errors": [], "warnings": []}
            with mock.patch.object(cfd_physics, "run_case", return_value={"ok": True}), \
                    mock.patch.object(cfd_physics, "evaluate_buoyant_run", return_value=expected) as evaluate, \
                    mock.patch.object(cfd_physics, "_attach_thermal_progress", return_value=expected), \
                    mock.patch.object(cfd_physics, "_build_body_fitted_results", return_value={"ok": True}):
                result = cfd_physics.run_buoyant_case(case)

        self.assertTrue(result["ok"])
        self.assertEqual(evaluate.call_args.kwargs, {})

    def test_continuation_passes_restart_settings_and_numerics_to_evaluator(self):
        """The continuation gate must inspect the schemes that were just run."""
        with tempfile.TemporaryDirectory(prefix=".test-continued-thermal-run-", dir=self.repo) as tmp:
            case = Path(tmp)
            (case / "thermal_input.json").write_text("{}", encoding="utf-8")
            restart = {
                "start_time_s": 0.1,
                "settings": {"thermal_numerics_profile": "stabilized_first_order_v1"},
                "thermal_numerics": {"profile": "stabilized_first_order_v1"},
                "numerics": {"profile": "v2606_bounded_fast_v1"},
            }
            expected = {"status": "WARN", "errors": [], "warnings": []}
            with mock.patch.object(cfd_physics, "prepare_buoyant_restart", return_value={
                "ok": True, "thermal_restart_input": restart,
            }), mock.patch.object(cfd_physics, "run_case", return_value={"ok": True}), \
                    mock.patch.object(cfd_physics, "evaluate_buoyant_run", return_value=expected) as evaluate, \
                    mock.patch.object(cfd_physics, "_attach_thermal_progress", return_value=expected), \
                    mock.patch.object(cfd_physics, "_build_body_fitted_results", return_value={"ok": True}):
                result = cfd_physics.run_buoyant_continuation(case)

        self.assertTrue(result["ok"])
        kwargs = evaluate.call_args.kwargs
        self.assertEqual(kwargs["effective_settings"], restart["settings"])
        self.assertEqual(kwargs["effective_numerics"], restart["thermal_numerics"])
        self.assertEqual(kwargs["restart_input_path"], case / "thermal_restart_input.json")
        self.assertEqual(
            kwargs["numerical_provenance"]["source"], "thermal_restart_input"
        )

    def test_energy_closure_uses_positive_phi_weighted_owner_temperature(self):
        with tempfile.TemporaryDirectory(prefix=".test-thermal-flux-", dir=self.repo) as tmp:
            case = Path(tmp)
            poly = case / "constant" / "polyMesh"
            poly.mkdir(parents=True)
            (poly / "points").write_text("\n0\n(\n)\n", encoding="ascii")
            (poly / "faces").write_text("\n2\n(\n0()\n0()\n)\n", encoding="ascii")
            (poly / "boundary").write_text("""
1
(
airVolume_exhaust_A
{
    type patch;
    nFaces 2;
    startFace 0;
}
)
""", encoding="ascii")
            (poly / "owner").write_text("""
FoamFile { class labelList; object owner; }
2
(
0
1
)
""", encoding="ascii")
            latest = case / "1"
            latest.mkdir()
            (latest / "T").write_text("""
internalField nonuniform List<scalar>
2
(
300
310
);
boundaryField
{
airVolume_exhaust_A
{
    type inletOutlet;
    value uniform 293.15;
}
}
""", encoding="ascii")
            (latest / "phi").write_text("""
internalField uniform 0;
boundaryField
{
airVolume_exhaust_A
{
    type calculated;
    value nonuniform List<scalar>
    2
    (
    0.1
    0.3
    );
}
} 
""", encoding="ascii")
            (latest / "V").write_text("""
internalField nonuniform List<scalar>
2
(
0.1
0.3
);
boundaryField {}
""", encoding="ascii")
            thermal = {
                "settings": dict(cfd_physics.DEFAULT_SETTINGS),
                "terminals": [{
                    "role": "exhaust",
                    "mesh_patch_name": "airVolume_exhaust_A",
                    "flow_rate_m3_s": 500.0 / 3600.0,
                }],
                "heat": {"applied_convective_power_w": (
                    1.204 * 1006.0 * 0.4 * (307.5 - 293.15)
                )},
            }
            metrics = cfd_physics.thermal_result_metrics(case, thermal)
        self.assertAlmostEqual(metrics["exhausts"][0]["temperature_k"], 307.5)
        self.assertAlmostEqual(metrics["exhausts"][0]["solved_outflow_rate_m3_s"], 0.4)
        self.assertAlmostEqual(metrics["energy_closure_ratio"], 1.0)
        self.assertAlmostEqual(metrics["room_heat_storage"]["cell_volume_sum_m3"], 0.4)
        self.assertEqual(
            metrics["energy_closure_basis"],
            "solver_positive_phi_and_owner_cell_temperature",
        )

    def test_terminal_flux_balance_uses_solved_phi_and_records_backflow(self):
        with tempfile.TemporaryDirectory(prefix=".test-terminal-phi-", dir=self.repo) as tmp:
            time_dir = Path(tmp) / "0.1"
            time_dir.mkdir()
            (time_dir / "phi").write_text("""FoamFile{}
boundaryField
{
    supply_A
    {
        value nonuniform List<scalar>
        2
        (
        -0.1
        0.02
        );
    }
    exhaust_A
    {
        value nonuniform List<scalar>
        2
        (
        0.06
        0.02
        );
    }
}
""", encoding="ascii")
            balance = cfd_physics.terminal_flux_balance(time_dir, [
                {"role": "supply", "mesh_patch_name": "supply_A"},
                {"role": "exhaust", "mesh_patch_name": "exhaust_A"},
            ])

        self.assertTrue(balance["available"])
        self.assertAlmostEqual(balance["inflow_m3_s"], 0.1)
        self.assertAlmostEqual(balance["outflow_m3_s"], 0.1)
        self.assertAlmostEqual(balance["imbalance_ratio"], 0.0)
        self.assertAlmostEqual(balance["supply_backflow_m3_s"], 0.02)

    def test_transient_energy_balance_combines_storage_and_integrated_exhaust(self):
        with tempfile.TemporaryDirectory(prefix=".test-transient-energy-", dir=self.repo) as tmp:
            case = Path(tmp)
            poly = case / "constant" / "polyMesh"
            poly.mkdir(parents=True)
            (poly / "points").write_text("\n0\n(\n)\n", encoding="ascii")
            (poly / "faces").write_text("\n2\n(\n0()\n0()\n)\n", encoding="ascii")
            (poly / "boundary").write_text("""
1
(
exhaust { type patch; nFaces 2; startFace 0; }
)
""", encoding="ascii")
            (poly / "owner").write_text("\n2\n(\n0\n1\n)\n", encoding="ascii")
            for name, temperature in (("0.5", 10), ("1", 20)):
                time_dir = case / name
                time_dir.mkdir()
                (time_dir / "T").write_text(f"""
internalField nonuniform List<scalar>
2
(
{temperature}
{temperature}
);
boundaryField {{}}
""", encoding="ascii")
                (time_dir / "phi").write_text("""
internalField uniform 0;
boundaryField
{
exhaust
{
    value nonuniform List<scalar>
    2
    (
    0.5
    0.5
    );
}
}
""", encoding="ascii")
            thermal_input = {
                "settings": {
                    "supply_temperature_k": 0,
                    "air_density_kg_m3": 1,
                    "air_specific_heat_j_kg_k": 1,
                },
                "terminals": [{"role": "exhaust", "mesh_patch_name": "exhaust"}],
                "heat": {"applied_convective_power_w": 100},
            }
            balance = cfd_physics._transient_energy_balance(
                case, thermal_input, {
                    "room_heat_storage": {"stored_sensible_energy_j": 90},
                    "exhaust_sensible_power_w": 20,
                }, {}, 0.0, 1.0,
            )
        self.assertTrue(balance["available"])
        self.assertTrue(balance["history_complete"])
        self.assertAlmostEqual(balance["cumulative_exhaust_energy_j"], 10.0)
        self.assertAlmostEqual(balance["transient_closure_ratio"], 1.0)

    def test_buoyant_result_warns_on_unphysical_temperature_undershoot(self):
        with tempfile.TemporaryDirectory(prefix=".test-thermal-floor-", dir=self.repo) as tmp:
            root = Path(tmp)
            built = cfd_physics.build_buoyant_case(
                self._mesh_case(root, with_heat=True), root / "thermal"
            )
            case = root / "thermal"
            (case / "log.buoyantBoussinesqPimpleFoam").write_text(
                "Time = 0.1\nCourant Number mean: 0.1 max: 0.8\nEnd\n",
                encoding="ascii",
            )
            metrics = {
                "available": True,
                "latest_time_s": 0.1,
                "minimum_k": 292.0,
                "maximum_k": 294.0,
                "mean_k": 293.2,
                "temperature_rise_k": 0.85,
                "energy_closure_ratio": 1.0,
                "exhaust_sensible_power_w": 4000.0,
            }
            with mock.patch.object(cfd_physics, "thermal_result_metrics", return_value=metrics):
                manifest = cfd_physics.evaluate_buoyant_run(
                    case, {"ok": True}, built["thermal_input"]
                )
        self.assertEqual(manifest["status"], "WARN")
        self.assertIn("TEMPERATURE_UNDERSHOOT", manifest["warnings"])

    def test_buoyant_failure_still_publishes_diagnostic_manifest(self):
        with tempfile.TemporaryDirectory(prefix=".test-thermal-failure-", dir=self.repo) as tmp:
            root = Path(tmp)
            built = cfd_physics.build_buoyant_case(
                self._mesh_case(root, with_heat=True), root / "thermal"
            )
            self.assertTrue(built["ok"], built)
            case = root / "thermal"
            (case / "log.buoyantBoussinesqSimpleFoam").write_text(
                "Time = 1\nFOAM FATAL ERROR\n", encoding="ascii"
            )
            with mock.patch.object(cfd_physics, "run_case", return_value={
                "ok": False, "error": "solver exit 136",
            }):
                result = cfd_physics.run_buoyant_case(case)
            manifest = json.loads((case / "run_manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(result["ok"])
        self.assertEqual(manifest["status"], "FAIL")
        self.assertIn("THERMAL_PRECONDITION_FAILED", manifest["errors"])

    def test_run_manifest_schema_is_valid_json(self):
        schema = json.loads(
            (self.repo / "run_manifest.v1.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(schema["properties"]["contract"]["const"], "run_manifest.v1")
        progress_schema = json.loads(
            (self.repo / "transient_progress.v1.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            progress_schema["properties"]["contract"]["const"], "transient_progress.v1"
        )
        thermal_progress_schema = json.loads(
            (self.repo / "thermal_progress.v1.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            thermal_progress_schema["properties"]["contract"]["const"],
            "thermal_progress.v1",
        )
        result_schema = json.loads(
            (self.repo / "result_manifest.v1.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            result_schema["properties"]["contract"]["const"], "result_manifest.v1"
        )
        self.assertIn("summary_sha256", result_schema["required"])
        self.assertEqual(
            result_schema["properties"]["slices"]["items"]["required"],
            ["axis", "path", "sha256"],
        )

    def test_buoyant_run_manifest_schema_requires_numerical_quality(self):
        schema = json.loads(
            (self.repo / "run_manifest.v1.schema.json").read_text(encoding="utf-8")
        )
        buoyant_condition = next(
            item for item in schema["allOf"]
            if item.get("if", {}).get("properties", {}).get("engine", {}).get("const")
            == "body_fitted_buoyant_urans"
        )
        self.assertIn("numerical_quality", buoyant_condition["then"]["required"])
        self.assertIn(
            "numerical_provenance",
            buoyant_condition["then"]["properties"]["input"]["required"],
        )
        quality = schema["properties"]["numerical_quality"]
        self.assertEqual(quality["properties"]["contract"]["const"], "numerical_quality.v1")
        self.assertTrue({"status", "design_ready", "profile", "convection_order", "blockers"}
                        .issubset(quality["required"]))

    def test_buoyant_restart_uses_latest_time_and_bounded_duration(self):
        with tempfile.TemporaryDirectory(prefix=".test-thermal-restart-", dir=self.repo) as tmp:
            root = Path(tmp)
            built = cfd_physics.build_buoyant_case(
                self._mesh_case(root, with_heat=True), root / "thermal",
                {"thermal_duration_s": 0.05},
            )
            self.assertTrue(built["ok"], built)
            case = root / "thermal"
            (case / "0.05").mkdir()
            (case / "run_manifest.json").write_text(json.dumps({
                "status": "WARN", "engine": "body_fitted_buoyant_urans",
            }), encoding="utf-8")
            prepared = cfd_physics.prepare_buoyant_restart(case)
            control = (case / "system" / "controlDict").read_text(encoding="utf-8")
            allrun = (case / "Allrun").read_text(encoding="utf-8")
            long = cfd_physics.prepare_buoyant_restart(
                case, {"thermal_duration_s": 5.0}
            )
            long_control = (case / "system" / "controlDict").read_text(
                encoding="utf-8"
            )
            rejected = cfd_physics.prepare_buoyant_restart(
                case, {"thermal_duration_s": 5.01}
            )
            short = cfd_physics.prepare_buoyant_restart(
                case, {"thermal_duration_s": 0.0636}
            )
            short_control = (case / "system" / "controlDict").read_text(
                encoding="utf-8"
            )
        self.assertTrue(prepared["ok"], prepared)
        self.assertIn("startFrom latestTime", control)
        self.assertIn("startTime 0.05", control)
        self.assertIn("endTime 0.1", control)
        self.assertIn("maxDeltaT 0.02", control)
        self.assertIn("writeInterval 0.05", control)
        self.assertIn("continue buoyant transient", allrun)
        self.assertIn("topoSet > log.topoSet", allrun)
        self.assertIn("foamToVTK -latestTime -ascii", allrun)
        self.assertIn("postProcess -func writeCellVolumes -latestTime", allrun)
        self.assertIn("postProcess -func yPlus -latestTime > log.yPlus", allrun)
        self.assertIn('cp "$start_time/V" "$latest_time/V"', allrun)
        self.assertEqual(
            prepared["thermal_restart_input"]["numerics"]["profile"],
            "v2606_bounded_fast_v1",
        )
        self.assertTrue(long["ok"], long)
        self.assertIn("writeInterval 2", long_control)
        self.assertFalse(rejected["ok"])
        self.assertIn("최대 5.0초", rejected["error"])
        self.assertTrue(short["ok"], short)
        self.assertIn("writeInterval 0.0636", short_control)

    def test_large_buoyant_restart_uses_bounded_mpi_parallelism(self):
        with tempfile.TemporaryDirectory(prefix=".test-thermal-mpi-", dir=self.repo) as tmp:
            root = Path(tmp)
            built = cfd_physics.build_buoyant_case(
                self._mesh_case(root, with_heat=True, cells=107991),
                root / "thermal", {"thermal_duration_s": 0.05},
            )
            self.assertTrue(built["ok"], built)
            case = root / "thermal"
            (case / "0.05").mkdir()
            (case / "run_manifest.json").write_text(json.dumps({
                "status": "WARN", "engine": "body_fitted_buoyant_urans",
            }), encoding="utf-8")

            prepared = cfd_physics.prepare_buoyant_restart(
                case, {
                    "thermal_parallel_processes": 8,
                    "parallel_capability": {
                        "contract": "runtime_capability.v1",
                        "parallel_runtime_ready": True,
                        "mpi": {"execution_smoke": "PASS"},
                        "cpu": {"effective_logical_count": 8},
                    },
                },
            )
            allrun = (case / "Allrun").read_text(encoding="utf-8")
            decomposition = (case / "system" / "decomposeParDict").read_text(
                encoding="utf-8"
            )
            parallel_artifact_exists = (case / "parallel_run.v1.json").is_file()

        self.assertTrue(prepared["ok"], prepared)
        self.assertIn("decomposePar -force", allrun)
        self.assertIn("mpirun -np 8", allrun)
        self.assertNotIn("--oversubscribe", allrun)
        self.assertIn("reconstructPar -latestTime", allrun)
        self.assertIn(
            'if [ "$rc" -ne 0 ]; then\n'
            '    echo "buoyantBoussinesqPimpleFoam continuation FAILED (exit $rc)"\n'
            '    tail -100 log.buoyantBoussinesqPimpleFoam\n'
            '    exit "$rc"\n'
            'fi\n'
            'reconstructPar -latestTime',
            allrun,
        )
        self.assertIn(
            'if [ "$reconstruct_rc" -ne 0 ]; then\n'
            '    echo "reconstructPar FAILED"\n'
            '    tail -80 log.reconstructPar\n'
            '    exit 26\n'
            'fi\n'
            'rm -rf processor*',
            allrun,
        )
        self.assertIn("numberOfSubdomains 8", decomposition)
        self.assertEqual(
            prepared["thermal_restart_input"]["numerics"]["parallel_processes"],
            8,
        )
        self.assertEqual(prepared["parallel_plan"]["mode"], "mpi")
        self.assertTrue(parallel_artifact_exists)

    def test_large_buoyant_restart_without_mpi_smoke_stays_serial(self):
        with tempfile.TemporaryDirectory(prefix=".test-thermal-serial-", dir=self.repo) as tmp:
            root = Path(tmp)
            built = cfd_physics.build_buoyant_case(
                self._mesh_case(root, with_heat=True, cells=107991),
                root / "thermal", {"thermal_duration_s": 0.05},
            )
            self.assertTrue(built["ok"], built)
            case = root / "thermal"
            (case / "0.05").mkdir()
            (case / "run_manifest.json").write_text(json.dumps({
                "status": "WARN", "engine": "body_fitted_buoyant_urans",
            }), encoding="utf-8")

            prepared = cfd_physics.prepare_buoyant_restart(
                case, {"thermal_parallel_processes": 8},
            )
            allrun = (case / "Allrun").read_text(encoding="utf-8")

        self.assertTrue(prepared["ok"], prepared)
        self.assertNotIn("mpirun", allrun)
        self.assertEqual(prepared["parallel_plan"]["mode"], "serial")
        self.assertIn(
            "mpi_execution_smoke_not_passed",
            prepared["parallel_plan"]["blockers"],
        )

    def test_thermal_progress_projects_flow_through_runtime(self):
        with tempfile.TemporaryDirectory(prefix=".test-thermal-progress-", dir=self.repo) as tmp:
            root = Path(tmp)
            built = cfd_physics.build_buoyant_case(
                self._mesh_case(root, with_heat=True), root / "thermal",
                {"thermal_duration_s": 0.05},
            )
            self.assertTrue(built["ok"], built)
            case = root / "thermal"
            (case / "0.05").mkdir()
            progress = cfd_physics._thermal_progress(
                case, built["thermal_input"], {
                    "execution": {"clock_seconds": 20.0},
                    "courant": {"maximum": 0.02},
                }, runtime_seconds=25.0, start_time_s=0.0,
            )
        self.assertEqual(progress["latest_time_s"], 0.05)
        self.assertEqual(progress["last_solver_runtime_per_simulated_second"], 400.0)
        self.assertGreater(progress["required_duration_s"], 0.05)
        self.assertIsNone(progress["estimated_remaining_runtime_seconds"])
        self.assertEqual(progress["estimate_status"],
                         "awaiting_continuation_sample")
        self.assertFalse(progress["interactive_budget_exceeded"])

    def test_thermal_progress_estimates_only_after_continuation_sample(self):
        with tempfile.TemporaryDirectory(prefix=".test-thermal-estimate-", dir=self.repo) as tmp:
            root = Path(tmp)
            built = cfd_physics.build_buoyant_case(
                self._mesh_case(root, with_heat=True), root / "thermal",
                {"thermal_duration_s": 0.05},
            )
            self.assertTrue(built["ok"], built)
            case = root / "thermal"
            (case / "0.05").mkdir()
            (case / "5.05").mkdir()
            progress = cfd_physics._thermal_progress(
                case, built["thermal_input"], {
                    "execution": {"clock_seconds": 10.0},
                    "courant": {"maximum": 0.5},
                }, runtime_seconds=12.0, start_time_s=0.05,
                numerics={"profile": "v2606_bounded_fast_v1"},
            )

        self.assertEqual(progress["estimate_status"], "measured_continuation")
        self.assertGreater(progress["estimated_remaining_runtime_seconds"], 0)

    def test_initial_stability_rate_bounds_the_first_checkpoint_not_the_eta(self):
        with tempfile.TemporaryDirectory(prefix=".test-thermal-first-checkpoint-",
                                         dir=self.repo) as tmp:
            root = Path(tmp)
            built = cfd_physics.build_buoyant_case(
                self._mesh_case(root, with_heat=True), root / "thermal", {
                    "thermal_duration_s": 0.05,
                    "thermal_max_delta_t_s": 0.0005,
                    "thermal_max_single_run_s": 60.0,
                    "thermal_checkpoint_wall_budget_s": 1800.0,
                },
            )
            self.assertTrue(built["ok"], built)
            case = root / "thermal"
            (case / "0.05").mkdir()
            progress = cfd_physics._thermal_progress(
                case, built["thermal_input"], {
                    "execution": {"clock_seconds": 2000.0},
                    "courant": {"maximum": 0.02},
                }, runtime_seconds=2005.0, start_time_s=0.0,
            )

        self.assertIsNone(progress["estimated_remaining_runtime_seconds"])
        self.assertEqual(progress["checkpoint_rate_source"],
                         "initial_stability_scaled")
        self.assertAlmostEqual(
            progress["checkpoint_rate_seconds_per_simulated_second"], 2000.0
        )
        self.assertAlmostEqual(progress["recommended_next_duration_s"], 0.9)

    def test_measured_rate_bounds_later_checkpoints_to_wall_budget(self):
        with tempfile.TemporaryDirectory(prefix=".test-thermal-wall-checkpoint-",
                                         dir=self.repo) as tmp:
            root = Path(tmp)
            built = cfd_physics.build_buoyant_case(
                self._mesh_case(root, with_heat=True), root / "thermal", {
                    "thermal_duration_s": 0.05,
                    "thermal_max_single_run_s": 60.0,
                    "thermal_checkpoint_wall_budget_s": 30.0,
                },
            )
            self.assertTrue(built["ok"], built)
            case = root / "thermal"
            (case / "0.05").mkdir()
            (case / "5.05").mkdir()
            progress = cfd_physics._thermal_progress(
                case, built["thermal_input"], {
                    "execution": {"clock_seconds": 10.0},
                    "courant": {"maximum": 0.5},
                }, runtime_seconds=12.0, start_time_s=0.05,
                numerics={"profile": "v2606_bounded_fast_v1"},
            )

        self.assertEqual(progress["checkpoint_rate_source"],
                         "measured_continuation")
        self.assertEqual(progress["recommended_next_duration_s"], 15.0)

    def test_transient_restart_uses_latest_time_and_flow_through_contract(self):
        with tempfile.TemporaryDirectory(prefix=".test-transient-build-", dir=self.repo) as tmp:
            root = Path(tmp)
            result = cfd_physics.build_isothermal_case(self._mesh_case(root), root / "solver")
            self.assertTrue(result["ok"], result)
            solver = root / "solver"
            (solver / "600").mkdir()
            (solver / "run_manifest.json").write_text(json.dumps({
                "status": "WARN", "warnings": ["ITERATION_LIMIT"],
                "engine": "body_fitted_isothermal_rans",
            }), encoding="utf-8")
            prepared = cfd_physics.prepare_transient_restart(solver)
            control = (solver / "system" / "controlDict").read_text(encoding="utf-8")
            solution = (solver / "system" / "fvSolution").read_text(encoding="utf-8")
            allrun = (solver / "Allrun").read_text(encoding="utf-8")
        self.assertTrue(prepared["ok"], prepared)
        self.assertIn("application pimpleFoam", control)
        self.assertIn("startFrom startTime", control)
        self.assertIn("pimpleFoam > log.pimpleFoam", allrun)
        self.assertIn("pFinal", solution)
        self.assertAlmostEqual(prepared["transient_input"]["flow_through_time_s"], 216.0)
        self.assertEqual(prepared["transient_input"]["baseline_time_s"], 600.0)

    def test_transient_single_run_duration_is_bounded(self):
        with tempfile.TemporaryDirectory(prefix=".test-transient-limit-", dir=self.repo) as tmp:
            root = Path(tmp)
            built = cfd_physics.build_isothermal_case(self._mesh_case(root), root / "solver")
            self.assertTrue(built["ok"], built)
            solver = root / "solver"
            (solver / "600").mkdir()
            (solver / "run_manifest.json").write_text(json.dumps({
                "status": "WARN", "warnings": ["ITERATION_LIMIT"],
                "engine": "body_fitted_isothermal_rans",
            }), encoding="utf-8")
            result = cfd_physics.prepare_transient_restart(
                solver, {"transient_duration_s": 121}
            )
        self.assertFalse(result["ok"])
        self.assertIn("최대 120초", result["error"])


class PhysicsParserTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[1]

    def test_solver_parser_reads_final_residuals_and_continuity(self):
        parsed = cfd_physics.parse_solver_log("""
trapFpe: Floating point exception trapping enabled (FOAM_SIGFPE).
Time = 25
smoothSolver:  Solving for Ux, Initial residual = 1e-05, Final residual = 1e-08, No Iterations 2
smoothSolver:  Solving for Uy, Initial residual = 2e-05, Final residual = 1e-08, No Iterations 2
smoothSolver:  Solving for Uz, Initial residual = 3e-05, Final residual = 1e-08, No Iterations 2
GAMG:  Solving for p, Initial residual = 4e-05, Final residual = 1e-08, No Iterations 2
smoothSolver:  Solving for k, Initial residual = 2e-05, Final residual = 1e-08, No Iterations 2
smoothSolver:  Solving for omega, Initial residual = 2e-05, Final residual = 1e-08, No Iterations 2
time step continuity errors : sum local = 1e-08, global = -2e-09, cumulative = 3e-08
SIMPLE solution converged in 25 iterations
End
        """)
        self.assertTrue(parsed["ended"])
        self.assertFalse(parsed["fatal"])
        self.assertTrue(parsed["converged"])
        self.assertAlmostEqual(parsed["residuals"]["p"]["initial"], 4e-5)
        self.assertAlmostEqual(parsed["continuity"]["global"], -2e-9)
        self.assertEqual(parsed["residual_history"]["p"]["samples"], 1)

    def test_iteration_limit_is_warning_when_linear_and_continuity_gates_pass(self):
        physics = {
            "settings": dict(cfd_physics.DEFAULT_SETTINGS),
            "wall_patches": [], "airflow": {}, "terminals": [],
        }
        log = """
Time = 400
smoothSolver: Solving for Ux, Initial residual = 0.01, Final residual = 0.0001, No Iterations 3
smoothSolver: Solving for Uy, Initial residual = 0.01, Final residual = 0.0001, No Iterations 3
smoothSolver: Solving for Uz, Initial residual = 0.01, Final residual = 0.0001, No Iterations 3
GAMG: Solving for p, Initial residual = 0.1, Final residual = 0.0005, No Iterations 3
smoothSolver: Solving for k, Initial residual = 0.01, Final residual = 0.0001, No Iterations 3
smoothSolver: Solving for omega, Initial residual = 0.01, Final residual = 0.0001, No Iterations 3
time step continuity errors : sum local = 1e-7, global = 1e-8, cumulative = 1e-7
End
"""
        with tempfile.TemporaryDirectory(prefix=".test-run-gate-", dir=self.repo) as tmp:
            case = Path(tmp)
            (case / "log.simpleFoam").write_text(log, encoding="ascii")
            (case / "physics_input.json").write_text("{}", encoding="ascii")
            manifest = cfd_physics.evaluate_run(case, {"ok": True}, physics)
        self.assertEqual(manifest["status"], "WARN")
        self.assertIn("ITERATION_LIMIT", manifest["warnings"])
        self.assertNotIn("RESIDUAL_LIMIT", manifest["errors"])

    def test_y_plus_area_ratio_uses_boundary_face_areas(self):
        with tempfile.TemporaryDirectory(prefix=".test-yplus-", dir=self.repo) as tmp:
            case = Path(tmp)
            poly = case / "constant" / "polyMesh"
            poly.mkdir(parents=True)
            (poly / "points").write_text(
                "FoamFile{}\n4\n(\n(0 0 0)\n(1 0 0)\n(1 1 0)\n(0 1 0)\n)\n", encoding="ascii"
            )
            (poly / "faces").write_text(
                "FoamFile{}\n1\n(\n4(0 1 2 3)\n)\n", encoding="ascii"
            )
            (poly / "boundary").write_text(
                "FoamFile{}\n1\n(\nwall\n{\n type wall;\n nFaces 1;\n startFace 0;\n}\n)\n",
                encoding="ascii",
            )
            time = case / "100"
            time.mkdir()
            (time / "yPlus").write_text("""FoamFile{}
boundaryField
{
 wall
 {
  type calculated;
  value nonuniform List<scalar>
  1
  (
   100
  );
 }
}
""", encoding="ascii")
            result = cfd_physics.y_plus_metrics(case, ["wall"], 30, 300)
        self.assertTrue(result["available"])
        self.assertEqual(result["area_ratio_in_target"], 1.0)
        self.assertEqual(result["area_weighted_average"], 100.0)
        self.assertEqual(result["wall_treatment_acceptable_area_ratio"], 1.0)

    def test_zero_nut_is_reported_as_viscous_branch_not_failed_log_layer(self):
        with tempfile.TemporaryDirectory(prefix=".test-wall-treatment-", dir=self.repo) as tmp:
            case = Path(tmp)
            poly = case / "constant" / "polyMesh"
            poly.mkdir(parents=True)
            (poly / "points").write_text(
                "FoamFile{}\n4\n(\n(0 0 0)\n(1 0 0)\n(1 1 0)\n(0 1 0)\n)\n", encoding="ascii"
            )
            (poly / "faces").write_text(
                "FoamFile{}\n1\n(\n4(0 1 2 3)\n)\n", encoding="ascii"
            )
            (poly / "boundary").write_text(
                "FoamFile{}\n1\n(\nwall\n{\n type wall;\n nFaces 1;\n startFace 0;\n}\n)\n",
                encoding="ascii",
            )
            time = case / "100"
            time.mkdir()
            (time / "nut").write_text("""FoamFile{}
boundaryField
{
 wall
 {
  type nutkWallFunction;
  value nonuniform List<scalar>
  1
  (
   0
  );
 }
}
""", encoding="ascii")
            result = cfd_physics.y_plus_metrics(case, ["wall"], 30, 300)
        self.assertEqual(result["area_ratio_in_target"], 0.0)
        self.assertEqual(result["viscous_branch_area_ratio"], 1.0)
        self.assertEqual(result["wall_treatment_acceptable_area_ratio"], 1.0)

    def test_engineering_tail_gate_accepts_reduced_bounded_initial_residuals(self):
        physics = {
            "settings": dict(cfd_physics.DEFAULT_SETTINGS),
            "wall_patches": [], "airflow": {}, "terminals": [],
        }
        lines = []
        for iteration in range(1, 101):
            initial = 0.1 if iteration == 1 else 1e-4
            lines.append(f"Time = {iteration}")
            for field in ("Ux", "Uy", "Uz", "p", "k", "omega"):
                lines.append(
                    f"smoothSolver: Solving for {field}, Initial residual = {initial}, "
                    "Final residual = 1e-8, No Iterations 2"
                )
            lines.append(
                "time step continuity errors : sum local = 1e-8, global = 1e-8, "
                "cumulative = 1e-7"
            )
        lines.append("End")
        with tempfile.TemporaryDirectory(prefix=".test-tail-gate-", dir=self.repo) as tmp:
            case = Path(tmp)
            (case / "log.simpleFoam").write_text("\n".join(lines), encoding="ascii")
            (case / "physics_input.json").write_text("{}", encoding="ascii")
            manifest = cfd_physics.evaluate_run(case, {"ok": True}, physics)
        self.assertTrue(manifest["solver"]["engineering_converged"])
        self.assertEqual(manifest["solver"]["convergence_mode"], "engineering_tail_gate")
        self.assertNotIn("ITERATION_LIMIT", manifest["warnings"])

    def test_wall_function_nut_inverse_is_monotonic(self):
        low = cfd_physics._y_plus_from_nut(1e-5, 1.5e-5)
        high = cfd_physics._y_plus_from_nut(1e-3, 1.5e-5)
        self.assertGreaterEqual(low, 11.0)
        self.assertGreater(high, low)

    def test_transient_parser_reads_courant_and_time_window(self):
        parsed = cfd_physics.parse_transient_log("""
Time = 600.1
Courant Number mean: 0.12 max: 0.85
smoothSolver: Solving for Ux, Initial residual = 0.01, Final residual = 1e-6, No Iterations 2
time step continuity errors : sum local = 1e-8, global = -2e-9, cumulative = 3e-8
Time = 600.15
Courant Number mean: 0.30 max: 2.4
Time = 600.2
Courant Number mean: 0.15 max: 0.91
ExecutionTime = 12.5 s  ClockTime = 14 s
End
""")
        self.assertTrue(parsed["ended"])
        self.assertEqual(parsed["start_time"], 600.1)
        self.assertEqual(parsed["end_time"], 600.2)
        self.assertAlmostEqual(parsed["courant"]["maximum"], 0.91)
        self.assertAlmostEqual(parsed["courant"]["peak_maximum"], 2.4)
        self.assertEqual(parsed["execution"]["clock_seconds"], 14.0)

    def test_thermal_parser_records_peak_velocity_cell_and_temperature_floor(self):
        parsed = cfd_physics.parse_thermal_log("""
Time = 0.001
fieldMinMax thermalFieldExtrema write:
    min(mag(U)) = 0 in cell 2 at location (0.1 0.2 0.3)
    max(mag(U)) = 2.5 in cell 42 at location (3.9 1.5 2.95)
    min(T) = 292.8 in cell 17 at location (2.0 1.0 0.5)
    max(T) = 293.7 in cell 21 at location (2.1 1.1 0.6)
Time = 0.002
fieldMinMax thermalFieldExtrema write:
    max(mag(U)) = 8.25 in cell 77 at location (3.95 1.5 2.98)
    min(T) = 292.4 in cell 18 at location (2.0 1.0 0.5)
""")
        extrema = parsed["field_extrema"]
        self.assertTrue(extrema["available"])
        self.assertEqual(extrema["velocity"]["maximum"]["cell"], 77)
        self.assertEqual(extrema["velocity"]["maximum"]["time_s"], 0.002)
        self.assertEqual(extrema["temperature"]["minimum"]["value"], 292.4)

    def test_thermal_parser_preserves_recent_residual_history_for_quality_gate(self):
        parsed = cfd_physics.parse_thermal_log("""
Time = 0.001
smoothSolver: Solving for T, Initial residual = 0.1, Final residual = 2e-5, No Iterations 3
Time = 0.002
smoothSolver: Solving for T, Initial residual = 0.01, Final residual = 8e-6, No Iterations 2
""")

        history = parsed["thermal_residual_history"]["T"]
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["final"], 2e-5)
        self.assertEqual(parsed["thermal_residuals"]["T"]["final"], 8e-6)

    def test_balanced_fast_transient_profile_reduces_pimple_corrections(self):
        settings = dict(cfd_physics.DEFAULT_SETTINGS)
        solution = cfd_physics._transient_fv_solution(settings)
        numerics = cfd_physics._transient_numerics(settings)
        self.assertIn("nOuterCorrectors 1;", solution)
        self.assertIn("nCorrectors 1;", solution)
        self.assertIn("nNonOrthogonalCorrectors 0;", solution)
        self.assertEqual(numerics["profile"], "balanced_fast_v2")
        self.assertEqual(numerics["outer_correctors"], 1)
        self.assertEqual(numerics["pressure_correctors"], 1)
        self.assertEqual(numerics["non_orthogonal_correctors"], 0)

    def test_transient_window_uses_three_recovered_velocity_snapshots(self):
        field = """FoamFile{}
internalField nonuniform List<vector>
2
(
(1 0 0)
(0 1 0)
);
boundaryField{}
"""
        with tempfile.TemporaryDirectory(prefix=".test-transient-window-", dir=self.repo) as tmp:
            case = Path(tmp)
            for name in ("602", "604", "606"):
                time = case / name
                time.mkdir()
                (time / "U").write_text(field, encoding="ascii")
            result = cfd_physics.transient_window_metrics(case, 600, 200)
        self.assertTrue(result["available"])
        self.assertEqual(result["sampled_duration_s"], 4.0)
        self.assertEqual(result["flow_through_fraction"], 0.02)
        self.assertEqual(result["mean_speed_relative_span"], 0.0)

    def test_transient_progress_accumulates_from_original_baseline_and_estimates_cost(self):
        settings = dict(cfd_physics.DEFAULT_SETTINGS)
        transient_input = {
            "created_at": "2026-07-16T00:00:00+00:00",
            "settings": settings,
            "start_time_s": 610.0, "baseline_time_s": 600.0,
            "flow_through_time_s": 200.0, "minimum_required_duration_s": 50.0,
            "wall_patches": ["wall"], "airflow": {}, "terminals": [],
            "steady_run_manifest_sha256": "abc",
        }
        log = """
Time = 620
Courant Number mean: 0.1 max: 0.8
time step continuity errors : sum local = 1e-8, global = 1e-8, cumulative = 1e-7
End
"""
        window = {
            "available": True, "snapshots": [], "sampled_duration_s": 10.0,
            "flow_through_fraction": 0.05,
            "mean_speed_relative_span": 0.01, "rms_speed_relative_span": 0.01,
        }
        y_plus = {"available": True, "wall_treatment_acceptable_area_ratio": 1.0}
        with tempfile.TemporaryDirectory(prefix=".test-transient-progress-", dir=self.repo) as tmp:
            case = Path(tmp)
            (case / "log.pimpleFoam").write_text(log, encoding="ascii")
            (case / "transient_input.json").write_text("{}", encoding="ascii")
            with mock.patch.object(
                cfd_physics, "transient_window_metrics", return_value=window,
            ), mock.patch.object(
                cfd_physics, "y_plus_metrics", return_value=y_plus,
            ):
                manifest = cfd_physics.evaluate_transient_run(
                    case, {"ok": True}, transient_input, runtime_seconds=20.0
                )
        progress = manifest["transient_progress"]
        self.assertEqual(progress["completed_duration_s"], 20.0)
        self.assertEqual(progress["remaining_duration_s"], 30.0)
        self.assertEqual(progress["estimated_remaining_runtime_seconds"], 60.0)
        self.assertFalse(progress["interactive_budget_exceeded"])
        self.assertEqual(progress["numerics"]["profile"], "balanced_fast_v2")
        self.assertEqual(progress["runs"][0]["numerics"]["outer_correctors"], 1)
        self.assertIn("TRANSIENT_WINDOW_TOO_SHORT", manifest["warnings"])

    def test_transient_progress_blocks_over_budget_interactive_run(self):
        settings = dict(cfd_physics.DEFAULT_SETTINGS)
        transient_input = {
            "created_at": "2026-07-16T00:00:00+00:00", "settings": settings,
            "start_time_s": 600.0, "baseline_time_s": 600.0,
            "flow_through_time_s": 240.0, "minimum_required_duration_s": 60.0,
            "wall_patches": [], "airflow": {}, "terminals": [],
            "steady_run_manifest_sha256": "abc",
        }
        log = """
Time = 600.5
Courant Number mean: 0.1 max: 0.8
time step continuity errors : sum local = 1e-8, global = 1e-8, cumulative = 1e-7
End
"""
        window = {
            "available": True, "snapshots": [], "sampled_duration_s": 0.5,
            "flow_through_fraction": 0.0,
            "mean_speed_relative_span": 0.01, "rms_speed_relative_span": 0.01,
        }
        y_plus = {"available": True, "wall_treatment_acceptable_area_ratio": 1.0}
        with tempfile.TemporaryDirectory(prefix=".test-transient-budget-", dir=self.repo) as tmp:
            case = Path(tmp)
            (case / "log.pimpleFoam").write_text(log, encoding="ascii")
            (case / "transient_input.json").write_text("{}", encoding="ascii")
            with mock.patch.object(cfd_physics, "transient_window_metrics", return_value=window), \
                    mock.patch.object(cfd_physics, "y_plus_metrics", return_value=y_plus):
                manifest = cfd_physics.evaluate_transient_run(
                    case, {"ok": True}, transient_input, runtime_seconds=100.0
                )
        self.assertTrue(manifest["transient_progress"]["interactive_budget_exceeded"])
        self.assertIn("TRANSIENT_RUNTIME_BUDGET", manifest["warnings"])


@unittest.skipUnless(
    os.environ.get("MEP_CFD_RUN_PHYSICS_TESTS") == "1",
    "set MEP_CFD_RUN_PHYSICS_TESTS=1 for the actual isothermal OpenFOAM pilot",
)
class PhysicsOpenFOAMIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[1]

    def test_balanced_terminals_run_isothermal_rans_and_y_plus(self):
        data = _geometry(
            [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
            with_obstacles=True, with_terminals=True,
        )
        terminals = [item for item in data["elements"]["equipment"]
                     if (item.get("semantic") or {}).get("kind") == "air_terminal"]
        for item in terminals:
            item["semantic"]["airflow_cmh"] = 500.0
        with tempfile.TemporaryDirectory(prefix=".test-physics-openfoam-", dir=self.repo) as tmp:
            root = Path(tmp)
            geometry = root / "geometry.json"
            geometry.write_text(json.dumps(data), encoding="utf-8")
            occ = cfd_occ.run_occ_job(geometry, root / "occ", timeout=180)
            self.assertTrue(occ.get("ok"), occ)
            built_mesh = cfd_mesh.build_mesh_case(
                root / "occ", root / "mesh", {"preset": "quick"}
            )
            self.assertTrue(built_mesh.get("ok"), built_mesh)
            mesh_result = cfd_mesh.run_mesh_case(root / "mesh")
            mesh_manifest = mesh_result.get("manifest") or {}
            self.assertTrue(mesh_result.get("ok"), {
                "error": mesh_result.get("error"),
                "errors": mesh_manifest.get("errors"),
                "warnings": mesh_manifest.get("warnings"),
                "mesh": mesh_manifest.get("mesh"),
                "layer": mesh_manifest.get("layer"),
            })
            built_solver = cfd_physics.build_isothermal_case(root / "mesh", root / "solver")
            self.assertTrue(built_solver.get("ok"), built_solver)
            run = cfd_physics.run_isothermal_case(root / "solver")
            self.assertTrue(run.get("ok"), run)
            manifest = run["manifest"]
        diagnostic = {
            "status": manifest["status"], "warnings": manifest["warnings"],
            "solver": {
                "iterations": manifest["solver"]["iterations"],
                "converged": manifest["solver"]["converged"],
                "engineering_converged": manifest["solver"]["engineering_converged"],
                "residual_history": manifest["solver"]["residual_history"],
                "continuity": manifest["solver"]["continuity"],
            },
            "wall_treatment": {
                key: manifest["y_plus"].get(key) for key in (
                    "area_ratio_in_target", "viscous_branch_area_ratio",
                    "buffer_layer_area_ratio", "above_target_area_ratio",
                    "wall_treatment_acceptable_area_ratio",
                )
            },
        }
        self.assertEqual(manifest["status"], "WARN", diagnostic)
        self.assertIn("ITERATION_LIMIT", manifest["warnings"], diagnostic)
        self.assertNotIn("WALL_TREATMENT_COVERAGE", manifest["warnings"], diagnostic)
        self.assertTrue(manifest["solver"]["ended"])
        self.assertLessEqual(abs(manifest["solver"]["continuity"]["global"]), 1e-6)
        self.assertFalse(manifest["design_ready"], diagnostic)
        self.assertTrue(manifest["y_plus"]["available"])
        self.assertGreaterEqual(
            manifest["y_plus"]["wall_treatment_acceptable_area_ratio"], 0.80,
            diagnostic,
        )


@unittest.skipUnless(
    os.environ.get("MEP_CFD_RUN_TRANSIENT_TESTS") == "1",
    "set MEP_CFD_RUN_TRANSIENT_TESTS=1 for the pimpleFoam restart smoke",
)
class TransientOpenFOAMIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[1]

    def test_quick_mesh_restarts_latest_steady_time_with_pimplefoam(self):
        data = _geometry(
            [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
            with_obstacles=False, with_terminals=True,
        )
        terminals = [item for item in data["elements"]["equipment"]
                     if (item.get("semantic") or {}).get("kind") == "air_terminal"]
        for item in terminals:
            item["semantic"]["airflow_cmh"] = 500.0
        with tempfile.TemporaryDirectory(prefix=".test-transient-openfoam-", dir=self.repo) as tmp:
            root = Path(tmp)
            geometry = root / "geometry.json"
            geometry.write_text(json.dumps(data), encoding="utf-8")
            occ = cfd_occ.run_occ_job(geometry, root / "occ", timeout=180)
            self.assertTrue(occ.get("ok"), occ)
            built_mesh = cfd_mesh.build_mesh_case(root / "occ", root / "mesh", {"preset": "quick"})
            self.assertTrue(built_mesh.get("ok"), built_mesh)
            mesh_result = cfd_mesh.run_mesh_case(root / "mesh")
            self.assertTrue(mesh_result.get("ok"), mesh_result.get("error"))
            built = cfd_physics.build_isothermal_case(
                root / "mesh", root / "solver", {"end_time": 30, "write_interval": 10}
            )
            self.assertTrue(built.get("ok"), built)
            steady = cfd_physics.run_case(
                root / "solver", name="transient_smoke_steady", keep_mesh=False
            )
            self.assertTrue(steady.get("ok"), steady)
            (root / "solver" / "run_manifest.json").write_text(json.dumps({
                "status": "WARN", "warnings": ["ITERATION_LIMIT"],
                "engine": "body_fitted_isothermal_rans",
            }), encoding="utf-8")
            transient = cfd_physics.run_transient_diagnostic(root / "solver", settings={
                "transient_duration_s": 0.5,
                "transient_initial_delta_t_s": 0.001,
                "transient_max_delta_t_s": 0.1,
                "transient_write_interval_s": 0.1,
                "max_continuity_global": 1e-3,
                "minimum_wall_treatment_area_ratio": 0.0,
            })
            transient_log = ((root / "solver" / "log.pimpleFoam").read_text(
                encoding="utf-8", errors="replace"
            ) if (root / "solver" / "log.pimpleFoam").is_file() else "")
            self.assertTrue(transient.get("ok"), {
                "result": transient, "log_tail": transient_log[-4000:],
            })
            manifest = transient["manifest"]
            recovered = {}
            for path in sorted((root / "solver").iterdir()):
                if path.is_dir() and (path / "U").is_file():
                    recovered[path.name] = (path / "U").read_text(
                        encoding="utf-8", errors="replace"
                    )[:600]
        self.assertEqual(manifest["engine"], "body_fitted_isothermal_urans")
        self.assertTrue(manifest["solver"]["ended"])
        self.assertLessEqual(manifest["solver"]["courant"]["maximum"], 2.0)
        self.assertTrue(manifest["transient_window"]["available"], {
            "window": manifest["transient_window"], "recovered_U": recovered,
        })


@unittest.skipUnless(
    os.environ.get("MEP_CFD_RUN_DETAILED_TRANSIENT_TESTS") == "1",
    "set MEP_CFD_RUN_DETAILED_TRANSIENT_TESTS=1 for the detailed transient benchmark",
)
class DetailedTransientOpenFOAMIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[1]

    def test_detailed_mesh_records_transient_cost_projection(self):
        data = _geometry(
            [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
            with_obstacles=True, with_terminals=True,
        )
        terminals = [item for item in data["elements"]["equipment"]
                     if (item.get("semantic") or {}).get("kind") == "air_terminal"]
        for item in terminals:
            item["semantic"]["airflow_cmh"] = 500.0
        with tempfile.TemporaryDirectory(prefix=".test-detailed-transient-", dir=self.repo) as tmp:
            root = Path(tmp)
            geometry = root / "geometry.json"
            geometry.write_text(json.dumps(data), encoding="utf-8")
            occ = cfd_occ.run_occ_job(geometry, root / "occ", timeout=180)
            self.assertTrue(occ.get("ok"), occ)
            built_mesh = cfd_mesh.build_mesh_case(
                root / "occ", root / "mesh", {"preset": "detailed"}
            )
            self.assertTrue(built_mesh.get("ok"), built_mesh)
            mesh_result = cfd_mesh.run_mesh_case(root / "mesh")
            self.assertTrue(mesh_result.get("ok"), mesh_result.get("error"))
            mesh_manifest = mesh_result["manifest"]
            built = cfd_physics.build_isothermal_case(
                root / "mesh", root / "solver", {"end_time": 30, "write_interval": 10}
            )
            self.assertTrue(built.get("ok"), built)
            steady = cfd_physics.run_case(
                root / "solver", name="detailed_transient_steady", keep_mesh=False
            )
            self.assertTrue(steady.get("ok"), steady)
            (root / "solver" / "run_manifest.json").write_text(json.dumps({
                "status": "WARN", "warnings": ["ITERATION_LIMIT"],
                "engine": "body_fitted_isothermal_rans",
            }), encoding="utf-8")
            transient = cfd_physics.run_transient_diagnostic(root / "solver", settings={
                "transient_duration_s": 0.5,
                "transient_initial_delta_t_s": 0.001,
                "transient_max_delta_t_s": 0.05,
                "transient_write_interval_s": 0.1,
                "max_continuity_global": 1e-3,
                "minimum_wall_treatment_area_ratio": 0.0,
            })
            self.assertTrue(transient.get("ok"), transient)
            manifest = transient["manifest"]
        progress = manifest["transient_progress"]
        self.assertGreater(mesh_manifest["mesh"]["cells"], 30_000)
        self.assertLess(mesh_manifest["mesh"]["cells"], 100_000)
        self.assertTrue(manifest["transient_window"]["available"])
        self.assertGreater(progress["last_runtime_per_simulated_second"], 0)
        self.assertGreater(progress["estimated_remaining_runtime_seconds"], 0)
        self.assertFalse(progress["interactive_budget_exceeded"])
        self.assertLessEqual(manifest["solver"]["courant"]["maximum"], 2.0)
        self.assertLessEqual(
            abs(manifest["solver"]["continuity"]["global"]), 1e-3
        )
        self.assertGreaterEqual(mesh_manifest["layer"]["coverage_ratio"], 0.55)
        print(json.dumps({
            "detailed_transient_benchmark": {
                "cells": mesh_manifest["mesh"]["cells"],
                "layer_coverage": mesh_manifest["layer"]["coverage_ratio"],
                "runtime_seconds": progress["total_runtime_seconds"],
                "simulated_duration_s": progress["completed_duration_s"],
                "runtime_per_simulated_second": progress["last_runtime_per_simulated_second"],
                "solver_clock_seconds": progress["last_solver_clock_seconds"],
                "solver_runtime_per_simulated_second": progress[
                    "last_solver_runtime_per_simulated_second"
                ],
                "fixed_runtime_overhead_seconds": progress[
                    "last_fixed_runtime_overhead_seconds"
                ],
                "required_duration_s": progress["required_duration_s"],
                "estimated_remaining_runtime_seconds": progress["estimated_remaining_runtime_seconds"],
                "interactive_budget_exceeded": progress["interactive_budget_exceeded"],
                "numerics": progress["numerics"],
            }
        }, ensure_ascii=False))


@unittest.skipUnless(
    os.environ.get("MEP_CFD_RUN_THERMAL_TESTS") == "1",
    "set MEP_CFD_RUN_THERMAL_TESTS=1 for the body-fitted heat/buoyancy smoke",
)
class ThermalOpenFOAMIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[1]

    def test_equipment_surface_heat_runs_buoyant_pimplefoam(self):
        data = _geometry(
            [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
            with_obstacles=True, with_terminals=True,
        )
        equipment = data["elements"]["equipment"]
        for item in equipment:
            semantic = item.get("semantic") or {}
            if semantic.get("kind") == "air_terminal":
                semantic["airflow_cmh"] = 500.0
            else:
                semantic["role"] = "heat_source"
                semantic["power_kw"] = 1.0
                semantic["convective_fraction"] = 0.8
        with tempfile.TemporaryDirectory(prefix=".test-thermal-openfoam-", dir=self.repo) as tmp:
            root = Path(tmp)
            geometry = root / "geometry.json"
            geometry.write_text(json.dumps(data), encoding="utf-8")
            occ = cfd_occ.run_occ_job(geometry, root / "occ", timeout=180)
            self.assertTrue(occ.get("ok"), occ)
            built_mesh = cfd_mesh.build_mesh_case(
                root / "occ", root / "mesh", {"preset": "quick"}
            )
            self.assertTrue(built_mesh.get("ok"), built_mesh)
            mesh = cfd_mesh.run_mesh_case(root / "mesh")
            self.assertTrue(mesh.get("ok"), mesh)
            steady_built = cfd_physics.build_isothermal_case(
                root / "mesh", root / "steady", {"end_time": 30, "write_interval": 10}
            )
            self.assertTrue(steady_built.get("ok"), steady_built)
            steady = cfd_physics.run_case(
                root / "steady", name="thermal_initial_steady", keep_mesh=False
            )
            self.assertTrue(steady.get("ok"), steady)
            built = cfd_physics.build_buoyant_case(root / "mesh", root / "thermal", {
                "thermal_duration_s": 0.05,
                "thermal_initial_delta_t_s": 0.001,
                "thermal_max_delta_t_s": 0.01,
                "thermal_write_interval_s": 0.01,
            }, initial_case_dir=root / "steady")
            self.assertTrue(built.get("ok"), built)
            run = cfd_physics.run_buoyant_case(root / "thermal")
            log = (root / "thermal" / "log.buoyantBoussinesqPimpleFoam").read_text(
                encoding="utf-8", errors="replace"
            ) if (root / "thermal" / "log.buoyantBoussinesqPimpleFoam").is_file() else ""
            self.assertTrue(run.get("ok"), {"run": run, "log_tail": log[-5000:]})
            manifest = run["manifest"]
        self.assertTrue(manifest["solver"]["ended"])
        self.assertTrue(manifest["thermal"]["available"])
        self.assertLessEqual(manifest["solver"]["courant"]["maximum"], 2.0)
        self.assertLess(manifest["thermal"]["temperature_rise_k"], 30.0)

    def test_quick_steady_maps_to_detailed_thermal_case(self):
        data = _geometry(
            [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
            with_obstacles=True, with_terminals=True,
        )
        for item in data["elements"]["equipment"]:
            semantic = item.get("semantic") or {}
            if semantic.get("kind") == "air_terminal":
                semantic["airflow_cmh"] = 500.0
            else:
                semantic["role"] = "heat_source"
                semantic["power_kw"] = 1.0
                semantic["convective_fraction"] = 0.8
        with tempfile.TemporaryDirectory(prefix=".test-thermal-map-openfoam-", dir=self.repo) as tmp:
            root = Path(tmp)
            geometry = root / "geometry.json"
            geometry.write_text(json.dumps(data), encoding="utf-8")
            occ = cfd_occ.run_occ_job(geometry, root / "occ", timeout=180)
            self.assertTrue(occ.get("ok"), occ)

            quick_built = cfd_mesh.build_mesh_case(
                root / "occ", root / "quick-mesh", {"preset": "quick"}
            )
            self.assertTrue(quick_built.get("ok"), quick_built)
            quick_mesh = cfd_mesh.run_mesh_case(root / "quick-mesh")
            self.assertTrue(quick_mesh.get("ok"), quick_mesh)
            steady_built = cfd_physics.build_isothermal_case(
                root / "quick-mesh", root / "quick-steady", {
                    "end_time": 30,
                    "write_interval": 10,
                },
            )
            self.assertTrue(steady_built.get("ok"), steady_built)
            steady = cfd_physics.run_case(
                root / "quick-steady", name="quick_mapping_source", keep_mesh=False
            )
            self.assertTrue(steady.get("ok"), steady)

            detailed_built = cfd_mesh.build_mesh_case(
                root / "occ", root / "detailed-mesh", {"preset": "detailed"}
            )
            self.assertTrue(detailed_built.get("ok"), detailed_built)
            detailed_mesh = cfd_mesh.run_mesh_case(root / "detailed-mesh")
            self.assertTrue(detailed_mesh.get("ok"), detailed_mesh)
            thermal_built = cfd_physics.build_buoyant_case(
                root / "detailed-mesh", root / "detailed-thermal", {
                    "thermal_duration_s": 0.05,
                    "thermal_initial_delta_t_s": 0.0001,
                    "thermal_max_delta_t_s": 0.0005,
                    "thermal_write_interval_s": 0.01,
                }, initial_case_dir=root / "quick-steady",
            )
            self.assertTrue(thermal_built.get("ok"), thermal_built)
            self.assertEqual(
                thermal_built["thermal_input"]["initialisation"]["mode"],
                "mapped_isothermal_fields",
            )
            run = cfd_physics.run_buoyant_case(root / "detailed-thermal")
            map_log = (root / "detailed-thermal" / "log.mapFields").read_text(
                encoding="utf-8", errors="replace"
            ) if (root / "detailed-thermal" / "log.mapFields").is_file() else ""
            solver_log = (
                root / "detailed-thermal" / "log.buoyantBoussinesqPimpleFoam"
            ).read_text(encoding="utf-8", errors="replace") if (
                root / "detailed-thermal" / "log.buoyantBoussinesqPimpleFoam"
            ).is_file() else ""
            self.assertTrue(run.get("ok"), {
                "run": run,
                "map_tail": map_log[-3000:],
                "solver_tail": solver_log[-5000:],
            })
            manifest = run["manifest"]
            continued = cfd_physics.run_buoyant_continuation(
                root / "detailed-thermal", {"thermal_duration_s": 0.01}
            )
            self.assertTrue(continued.get("ok"), continued)
            continuation_manifest = continued["manifest"]
        self.assertIn("Source time", map_log)
        self.assertTrue(manifest["solver"]["ended"])
        self.assertLessEqual(manifest["solver"]["courant"]["maximum"], 2.0)
        self.assertGreaterEqual(manifest["thermal"]["minimum_k"], 293.05)
        self.assertAlmostEqual(
            continuation_manifest["thermal_progress"]["latest_time_s"], 0.06
        )
        self.assertEqual(
            continuation_manifest["thermal_progress"]["runs_completed"], 2
        )
        self.assertIn("THERMAL_WINDOW_TOO_SHORT", continuation_manifest["warnings"])


@unittest.skipUnless(
    os.environ.get("MEP_CFD_RUN_THERMAL_MATRIX_TESTS") == "1",
    "set MEP_CFD_RUN_THERMAL_MATRIX_TESTS=1 for the G2 condition matrix",
)
class ThermalConditionMatrixOpenFOAMTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[1]

    def test_g2_identifies_first_unstable_condition(self):
        data = _geometry(
            [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
            with_obstacles=True, with_terminals=True,
        )
        for item in data["elements"]["equipment"]:
            semantic = item.get("semantic") or {}
            if semantic.get("kind") == "air_terminal":
                semantic["airflow_cmh"] = 500.0
            else:
                semantic["role"] = "heat_source"
                semantic["power_kw"] = 1.0
                semantic["convective_fraction"] = 0.8
        stages = [
            ("zero", 0.0, 0.0, 0.0),
            ("flow", 1.0, 0.0, 0.0),
            ("gravity", 1.0, 1.0, 0.0),
            ("heat", 1.0, 1.0, 1.0),
        ]
        with tempfile.TemporaryDirectory(prefix=".test-thermal-matrix-", dir=self.repo) as tmp:
            root = Path(tmp)
            geometry = root / "geometry.json"
            geometry.write_text(json.dumps(data), encoding="utf-8")
            occ = cfd_occ.run_occ_job(geometry, root / "occ", timeout=180)
            self.assertTrue(occ.get("ok"), occ)
            built_mesh = cfd_mesh.build_mesh_case(
                root / "occ", root / "mesh", {"preset": "quick"}
            )
            self.assertTrue(built_mesh.get("ok"), built_mesh)
            mesh = cfd_mesh.run_mesh_case(root / "mesh")
            self.assertTrue(mesh.get("ok"), mesh)
            matrix = {}
            for name, flow, gravity, heat in stages:
                built = cfd_physics.build_buoyant_case(
                    root / "mesh", root / name, {
                        "thermal_flow_scale": flow,
                        "thermal_gravity_scale": gravity,
                        "thermal_heat_scale": heat,
                        "thermal_preconditioning_iterations": 0,
                        "thermal_duration_s": 0.002,
                        "thermal_initial_delta_t_s": 0.0001,
                        "thermal_max_delta_t_s": 0.0005,
                        "thermal_write_interval_s": 0.001,
                    },
                )
                self.assertTrue(built.get("ok"), built)
                run = cfd_physics.run_buoyant_case(root / name)
                manifest = run.get("manifest") or {}
                matrix[name] = {
                    "ok": bool(run.get("ok")),
                    "status": manifest.get("status"),
                    "errors": manifest.get("errors"),
                    "courant_max": ((manifest.get("solver") or {}).get("courant") or {}).get(
                        "maximum"
                    ),
                    "temperature_max_k": (manifest.get("thermal") or {}).get("maximum_k"),
                    "field_extrema": (manifest.get("solver") or {}).get("field_extrema"),
                }
            steady_built = cfd_physics.build_isothermal_case(
                root / "mesh", root / "steady", {
                    "end_time": 10,
                    "write_interval": 10,
                },
            )
            self.assertTrue(steady_built.get("ok"), steady_built)
            steady = cfd_physics.run_case(
                root / "steady", name="g2_initial_steady", keep_mesh=False
            )
            self.assertTrue(steady.get("ok"), steady)
            initialisation_stages = [
                ("heat_precondition", 1, None),
                ("heat_isothermal", 0, root / "steady"),
                ("heat_isothermal_precondition", 1, root / "steady"),
            ]
            for name, precondition, initial_case in initialisation_stages:
                built = cfd_physics.build_buoyant_case(
                    root / "mesh", root / name, {
                        "thermal_preconditioning_iterations": precondition,
                        "thermal_duration_s": 0.002,
                        "thermal_initial_delta_t_s": 0.0001,
                        "thermal_max_delta_t_s": 0.0005,
                        "thermal_write_interval_s": 0.001,
                    }, initial_case_dir=initial_case,
                )
                self.assertTrue(built.get("ok"), built)
                run = cfd_physics.run_buoyant_case(root / name)
                manifest = run.get("manifest") or {}
                matrix[name] = {
                    "ok": bool(run.get("ok")),
                    "status": manifest.get("status"),
                    "errors": manifest.get("errors"),
                    "courant_max": ((manifest.get("solver") or {}).get("courant") or {}).get(
                        "maximum"
                    ),
                    "temperature_max_k": (manifest.get("thermal") or {}).get("maximum_k"),
                    "field_extrema": (manifest.get("solver") or {}).get("field_extrema"),
                }
            print(json.dumps({"thermal_condition_matrix": matrix}, ensure_ascii=False))
        for name, _flow, _gravity, _heat in stages:
            self.assertTrue(matrix[name]["ok"], matrix)
            self.assertLessEqual(matrix[name]["courant_max"], 2.0)

    def test_g2_detailed_mesh_full_physics_starts_without_preconditioning(self):
        duration_s = float(os.environ.get(
            "MEP_CFD_THERMAL_DETAILED_DURATION", "0.002"
        ))
        data = _geometry(
            [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
            with_obstacles=True, with_terminals=True,
        )
        for item in data["elements"]["equipment"]:
            semantic = item.get("semantic") or {}
            if semantic.get("kind") == "air_terminal":
                semantic["airflow_cmh"] = 500.0
            else:
                semantic["role"] = "heat_source"
                semantic["power_kw"] = 1.0
                semantic["convective_fraction"] = 0.8
        with tempfile.TemporaryDirectory(prefix=".test-thermal-detailed-", dir=self.repo) as tmp:
            root = Path(tmp)
            geometry = root / "geometry.json"
            geometry.write_text(json.dumps(data), encoding="utf-8")
            occ = cfd_occ.run_occ_job(geometry, root / "occ", timeout=180)
            self.assertTrue(occ.get("ok"), occ)
            built_mesh = cfd_mesh.build_mesh_case(
                root / "occ", root / "mesh", {"preset": "detailed"}
            )
            self.assertTrue(built_mesh.get("ok"), built_mesh)
            mesh = cfd_mesh.run_mesh_case(root / "mesh")
            self.assertTrue(mesh.get("ok"), mesh)
            stages = [
                ("zero", 0.0, 0.0, 0.0),
                ("flow", 1.0, 0.0, 0.0),
                ("gravity", 1.0, 1.0, 0.0),
                ("heat", 1.0, 1.0, 1.0),
            ]
            requested_stage = os.environ.get("MEP_CFD_THERMAL_MATRIX_STAGE")
            if requested_stage:
                stages = [stage for stage in stages if stage[0] == requested_stage]
                self.assertTrue(stages, requested_stage)
            matrix = {}
            for name, flow, gravity, heat in stages:
                built = cfd_physics.build_buoyant_case(
                    root / "mesh", root / name, {
                        "thermal_flow_scale": flow,
                        "thermal_gravity_scale": gravity,
                        "thermal_heat_scale": heat,
                        "thermal_preconditioning_iterations": 0,
                        "thermal_duration_s": duration_s,
                        "thermal_initial_delta_t_s": 0.0001,
                        "thermal_max_delta_t_s": 0.0005,
                        "thermal_write_interval_s": min(0.01, duration_s / 5.0),
                        "thermal_log_field_extrema": False,
                    },
                )
                self.assertTrue(built.get("ok"), built)
                run = cfd_physics.run_buoyant_case(root / name)
                manifest = run.get("manifest") or {}
                matrix[name] = {
                    "ok": bool(run.get("ok")),
                    "status": manifest.get("status"),
                    "errors": manifest.get("errors"),
                    "courant_max": ((manifest.get("solver") or {}).get("courant") or {}).get(
                        "maximum"
                    ),
                    "temperature_max_k": (manifest.get("thermal") or {}).get("maximum_k"),
                    "temperature_min_k": (manifest.get("thermal") or {}).get("minimum_k"),
                    "field_extrema": (manifest.get("solver") or {}).get("field_extrema"),
                }
            mesh_manifest = json.loads(
                (root / "mesh" / "mesh_manifest.json").read_text(encoding="utf-8")
            )
            print(json.dumps({
                "thermal_detailed_matrix": matrix,
                "cells": (mesh_manifest.get("mesh") or {}).get("cells"),
            }, ensure_ascii=False))
        for name, _flow, _gravity, _heat in stages:
            self.assertTrue(matrix[name]["ok"], matrix)
            self.assertLessEqual(matrix[name]["courant_max"], 2.0)
            self.assertGreaterEqual(matrix[name]["temperature_min_k"], 293.05)


if __name__ == "__main__":
    unittest.main()
