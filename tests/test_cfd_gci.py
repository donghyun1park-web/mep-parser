import json
from pathlib import Path
import tempfile
import unittest

import cfd_gci
import cfd_report


class BodyFittedGCITests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[1]
        self.tmp = tempfile.TemporaryDirectory(prefix=".test-gci-", dir=self.repo)
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _case(self, name, cells, tmax, tp95, up95, *, flow=500.0,
              time_s=60.0, surface_hash="same-cad"):
        case = self.root / name
        (case / "results").mkdir(parents=True)
        system = case / "system"
        system.mkdir()
        mesh = {
            "contract": "mesh_manifest.v1", "status": "PASS",
            "occ_volume_m3": 32.0, "mesh": {"cells": cells},
            "input": {"surface_manifest_sha256": surface_hash},
        }
        thermal = {
            "contract": "thermal_input.v1",
            "airflow": {"supply_cmh": flow, "exhaust_cmh": flow},
            "assumptions": {"density_model": "Boussinesq"},
            "condition_matrix": {"flow_scale": 1, "heat_scale": 1,
                                 "gravity_scale": 1},
            "heat": {"applied_convective_power_w": 800, "input_power_w": 1000,
                     "requested_convective_power_w": 800,
                     "deferred_convective_power_w": 0,
                     "radiative_power_w": 200,
                     "excluded_radiative_power_w": 200,
                     "source_count": 1, "application_scale": 1,
                     "model": "equipment_wall_adjacent_cells_v1"},
            "heat_sources": [{"name": "heater", "power_kw": 1,
                              "convective_fraction": 0.8,
                              "radiative_fraction": 0.2,
                              "convective_power_w": 800,
                              "requested_convective_power_w": 800,
                              "applied_convective_power_w": 800,
                              "deferred_convective_power_w": 0,
                              "radiative_power_w": 200,
                              "excluded_radiative_power_w": 200,
                              "application_scale": 1,
                              "source_element_ids": ["fixture:heater"],
                              "evidence": "fixture:confirmed",
                              "source_type": "fixture"}],
            "settings": {"reference_temperature_k": 293.15,
                         "initial_temperature_k": 293.15,
                         "supply_temperature_k": 293.15,
                         "air_density_kg_m3": 1.204,
                         "air_specific_heat_j_kg_k": 1006,
                         "thermal_expansion_coefficient_1_k": 0.00341,
                         "thermal_duration_s": 5 if cells < 10000 else 1,
                         "thermal_numerics_profile": "design_limited_second_order_v1"},
            "numerics": {
                "profile": "design_limited_second_order_v1",
                "convection_order": 2,
                "laplacian_correction": "limited 0.5",
                "sn_grad_correction": "limited 0.5",
                "required_non_orthogonal_correctors": 2,
            },
            "terminals": [{"name": "S1", "role": "supply", "airflow_cmh": flow},
                          {"name": "E1", "role": "exhaust", "airflow_cmh": flow}],
        }
        run = {
            "contract": "run_manifest.v1", "status": "PASS",
            "design_ready": True, "engine": "body_fitted_buoyant_urans",
            "thermal_progress": {
                "minimum_flow_through_fraction": 0.25,
                "flow_through_fraction": 0.25,
                "energy_balance": {"available": True, "history_complete": True},
            },
            "effective_settings": thermal["settings"],
            "effective_numerics": thermal["numerics"],
            "numerical_quality": {
                "contract": "numerical_quality.v1",
                "status": "PASS",
                "design_ready": True,
                "profile": "design_limited_second_order_v1",
                "convection_order": 2,
                "blockers": [],
            },
        }
        summary = {
            "contract": "body_fitted_summary.v1", "time_s": time_s,
            "cell_count": cells,
            "bounds_m": {"minimum": [0, 0, 0], "maximum": [1, 1, 1]},
            "fields": {"T": {"unit": "K"}, "U": {"unit": "m/s"}},
            "temperature": {"maximum": 293.15 + tmax, "p95": 293.15 + tp95},
            "velocity": {"p95_speed": up95},
        }
        result = {"contract": "result_manifest.v1",
                  "engine": "body_fitted_openfoam_vtu",
                  "summary_path": "results/body_fitted_summary.json"}
        mesh_path = case / "mesh_manifest.json"
        thermal_path = case / "thermal_input.json"
        run_path = case / "run_manifest.json"
        result_path = case / "result_manifest.json"
        for name, contents in {
            "controlDict": "application buoyantBoussinesqPimpleFoam;\n",
            "fvSchemes": (
                "divSchemes\n{\n"
                "    default none;\n"
                "    div(phi,U) bounded Gauss linearUpwind grad(U);\n"
                "    div(phi,T) bounded Gauss limitedLinear 1;\n"
                "    div(phi,k) bounded Gauss limitedLinear 1;\n"
                "    div(phi,omega) bounded Gauss limitedLinear 1;\n"
                "}\n"
                "laplacianSchemes { default Gauss linear limited 0.5; }\n"
                "snGradSchemes { default limited 0.5; }\n"
            ),
            "fvSolution": "PIMPLE { nCorrectors 2; nNonOrthogonalCorrectors 2; }\n",
        }.items():
            (system / name).write_text(contents, encoding="ascii")
        mesh_path.write_text(json.dumps(mesh), encoding="utf-8")
        thermal["mesh_manifest_sha256"] = cfd_gci._file_sha256(mesh_path)
        thermal_path.write_text(json.dumps(thermal), encoding="utf-8")
        system_hashes = {
            name: cfd_gci._file_sha256(system / name)
            for name in ("controlDict", "fvSchemes", "fvSolution")
        }
        run["input"] = {
            "thermal_input_sha256": cfd_gci._file_sha256(thermal_path),
            "numerical_provenance": {
                "contract": "thermal_numerics_provenance.v1",
                "source": "thermal_initial_input",
                "thermal_input_sha256": cfd_gci._file_sha256(thermal_path),
                "thermal_restart_input_sha256": None,
                "effective_settings_sha256": cfd_gci._canonical_hash(thermal["settings"]),
                "effective_numerics_sha256": cfd_gci._canonical_hash(thermal["numerics"]),
                "expected_system": dict(system_hashes),
                "system": dict(system_hashes),
            },
        }
        run["heat"] = thermal["heat"]
        run["heat_sources"] = thermal["heat_sources"]
        run_path.write_text(json.dumps(run), encoding="utf-8")
        summary_path = case / "results" / "body_fitted_summary.json"
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        source_path = case / "results" / "internal.vtu"
        source_path.write_text("<VTKFile/>", encoding="ascii")
        slice_refs = []
        slices_dir = case / "results" / "slices"
        slices_dir.mkdir()
        for axis in "xyz":
            slice_path = slices_dir / f"{axis}_mid.json"
            slice_path.write_text(json.dumps({
                "axis": axis, "target_m": 0.5,
                "sample_count": 0, "samples": [],
            }), encoding="utf-8")
            slice_refs.append({
                "axis": axis,
                "path": slice_path.relative_to(case).as_posix(),
                "sha256": cfd_gci._file_sha256(slice_path),
            })
        result.update({
            "source": {
                "path": source_path.relative_to(case).as_posix(),
                "sha256": cfd_gci._file_sha256(source_path),
            },
            "summary_sha256": cfd_gci._file_sha256(summary_path),
            "slices": slice_refs,
            "run_manifest_sha256": cfd_gci._file_sha256(run_path),
            "mesh_manifest_sha256": cfd_gci._file_sha256(mesh_path),
            "thermal_input_sha256": cfd_gci._file_sha256(thermal_path),
        })
        result_path.write_text(json.dumps(result), encoding="utf-8")
        return case

    @staticmethod
    def _sync_case_thermal_provenance(case):
        """Update the fixture's manifest chain after an intentional input edit."""
        thermal_path = case / "thermal_input.json"
        run_path = case / "run_manifest.json"
        result_path = case / "result_manifest.json"
        thermal = json.loads(thermal_path.read_text(encoding="utf-8"))
        run = json.loads(run_path.read_text(encoding="utf-8"))
        system = case / "system"
        system_hashes = {
            name: cfd_gci._file_sha256(system / name)
            for name in ("controlDict", "fvSchemes", "fvSolution")
        }
        run["input"] = {
            "thermal_input_sha256": cfd_gci._file_sha256(thermal_path),
            "numerical_provenance": {
                "contract": "thermal_numerics_provenance.v1",
                "source": "thermal_initial_input",
                "thermal_input_sha256": cfd_gci._file_sha256(thermal_path),
                "thermal_restart_input_sha256": None,
                "effective_settings_sha256": cfd_gci._canonical_hash(
                    run["effective_settings"]
                ),
                "effective_numerics_sha256": cfd_gci._canonical_hash(
                    run["effective_numerics"]
                ),
                "expected_system": dict(system_hashes),
                "system": dict(system_hashes),
            },
        }
        run["heat"] = thermal["heat"]
        run["heat_sources"] = thermal["heat_sources"]
        run_path.write_text(json.dumps(run), encoding="utf-8")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result.update({
            "run_manifest_sha256": cfd_gci._file_sha256(run_path),
            "mesh_manifest_sha256": cfd_gci._file_sha256(case / "mesh_manifest.json"),
            "thermal_input_sha256": cfd_gci._file_sha256(thermal_path),
        })
        result_path.write_text(json.dumps(result), encoding="utf-8")

    @staticmethod
    def _scalar_field(values):
        body = "\n".join(f"{value:.12g}" for value in values)
        return (
            "FoamFile { format ascii; class volScalarField; object field; }\n"
            f"internalField nonuniform List<scalar>\n{len(values)}\n(\n{body}\n)\n;\n"
            "boundaryField {}\n"
        )

    @staticmethod
    def _vector_field(values):
        body = "\n".join(
            f"({x:.12g} {y:.12g} {z:.12g})" for x, y, z in values
        )
        return (
            "FoamFile { format ascii; class volVectorField; object U; }\n"
            f"internalField nonuniform List<vector>\n{len(values)}\n(\n{body}\n)\n;\n"
            "boundaryField {}\n"
        )

    def _window_case(self, name, cells, temperature_rise, speed, *, flow_fraction=1.0):
        end_time = 60.0 * flow_fraction
        case = self._case(
            name, cells, temperature_rise, temperature_rise, speed,
            time_s=end_time,
        )
        run_path = case / "run_manifest.json"
        run = json.loads(run_path.read_text(encoding="utf-8"))
        run["thermal_progress"].update({
            "minimum_flow_through_fraction": 1.0,
            "flow_through_fraction": flow_fraction,
            "flow_through_time_s": 60.0,
            "latest_time_s": end_time,
        })
        run_path.write_text(json.dumps(run), encoding="utf-8")
        self._sync_case_thermal_provenance(case)
        volume = 32.0 / cells
        for time_s in (end_time - 6.0, end_time - 4.5, end_time - 3.0,
                       end_time - 1.5, end_time):
            folder = case / f"{time_s:g}"
            folder.mkdir()
            (folder / "T").write_text(
                self._scalar_field([293.15 + temperature_rise] * cells),
                encoding="utf-8",
            )
            (folder / "V").write_text(
                self._scalar_field([volume] * cells), encoding="utf-8"
            )
            (folder / "U").write_text(
                self._vector_field([(speed, 0.0, 0.0)] * cells), encoding="utf-8"
            )
        return case

    def test_three_monotonic_grids_pass_and_are_sorted_by_actual_width(self):
        coarse = self._case("coarse", 4000, 13.0, 3.9, 0.39)
        medium = self._case("medium", 8000, 12.25, 3.675, 0.3675)
        fine = self._case("fine", 16000, 12.0, 3.6, 0.36)
        output = self.root / "study" / "grid_convergence.json"
        result = cfd_gci.build_grid_convergence([medium, coarse, fine], output)
        self.assertTrue(result["ok"], result)
        manifest = result["manifest"]
        self.assertEqual(manifest["status"], "PASS")
        self.assertEqual([row["name"] for row in manifest["cases"]],
                         ["fine", "medium", "coarse"])
        self.assertTrue(all(row["gci_fine_pct"] <= 5 for row in manifest["metrics"]))
        self.assertAlmostEqual(
            manifest["cases"][0]["effective_grid_width_m"],
            (32.0 / 16000) ** (1 / 3),
        )
        self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["contract"],
                         "grid_convergence.v1")
        report = cfd_report.generate_gci_report(output.parent)
        self.assertTrue(report["ok"], report)
        html = Path(report["path"]).read_text(encoding="utf-8")
        self.assertIn("3수준 메시 불확실성 보고서", html)
        self.assertIn("class='pass'>PASS", html)
        self.assertIn("온도는 절대온도가 아니라", html)

    def test_mismatched_physics_or_time_is_rejected(self):
        first = self._case("a", 4000, 13, 4, 0.4)
        different_flow = self._case("b", 8000, 12.5, 3.8, 0.38, flow=600)
        third = self._case("c", 16000, 12.2, 3.7, 0.37)
        result = cfd_gci.build_grid_convergence([first, different_flow, third])
        self.assertFalse(result["ok"])
        self.assertIn("조건", result["error"])

        different_time = self._case("d", 8000, 12.5, 3.8, 0.38, time_s=61)
        result = cfd_gci.build_grid_convergence([first, different_time, third])
        self.assertFalse(result["ok"])
        self.assertIn("물리시간", result["error"])

    def test_stale_or_changed_heat_provenance_is_rejected_before_gci(self):
        stale = self._case("stale-input", 4000, 13, 4, 0.4)
        result_path = stale / "result_manifest.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["thermal_input_sha256"] = "stale"
        result_path.write_text(json.dumps(result), encoding="utf-8")
        with self.assertRaises(cfd_gci.GCIInputError):
            cfd_gci.load_body_fitted_case(stale)

        changed = self._case("changed-heat", 4000, 13, 4, 0.4)
        run_path = changed / "run_manifest.json"
        run = json.loads(run_path.read_text(encoding="utf-8"))
        run["heat_sources"][0]["convective_power_w"] = 799.0
        run_path.write_text(json.dumps(run), encoding="utf-8")
        result_path = changed / "result_manifest.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["run_manifest_sha256"] = cfd_gci._file_sha256(run_path)
        result_path.write_text(json.dumps(result), encoding="utf-8")
        with self.assertRaises(cfd_gci.GCIInputError):
            cfd_gci.load_body_fitted_case(changed)

    def test_gci_requires_design_ready_numerical_quality(self):
        case = self._case("screening-numerics", 4000, 13, 4, 0.4)
        run_path = case / "run_manifest.json"
        run = json.loads(run_path.read_text(encoding="utf-8"))
        run["numerical_quality"].update({
            "status": "SCREENING_ONLY",
            "design_ready": False,
            "convection_order": 1,
        })
        run_path.write_text(json.dumps(run), encoding="utf-8")
        self._sync_case_thermal_provenance(case)

        with self.assertRaisesRegex(cfd_gci.GCIInputError, "numerical_quality"):
            cfd_gci.load_body_fitted_case(case)

    def test_gci_accepts_second_order_candidate_when_only_sensitivity_is_pending(self):
        case = self._case("gci-candidate", 4000, 13, 4, 0.4)
        run_path = case / "run_manifest.json"
        run = json.loads(run_path.read_text(encoding="utf-8"))
        run["design_ready"] = False
        run["numerical_quality"].update({
            "status": "NOT_EVALUATED",
            "design_ready": False,
            "blockers": ["NUMERICAL_SENSITIVITY_ARTIFACT_UNVERIFIED"],
        })
        run_path.write_text(json.dumps(run), encoding="utf-8")
        self._sync_case_thermal_provenance(case)

        loaded = cfd_gci.load_body_fitted_case(case)

        self.assertEqual(loaded["candidate_status"], "GCI_CANDIDATE")

    def test_gci_rejects_freshly_rehashed_upwind_numerical_semantics(self):
        """A self-consistent hash must not hide a first-order scheme."""
        case = self._case("fresh-upwind", 4000, 13, 4, 0.4)
        (case / "system" / "fvSchemes").write_text(
            "divSchemes { default upwind; }\n", encoding="ascii"
        )
        self._sync_case_thermal_provenance(case)

        with self.assertRaisesRegex(
                cfd_gci.GCIInputError,
                "SEMANTIC_DIV_PHI_U_NOT_LIMITED_SECOND_ORDER"):
            cfd_gci.load_body_fitted_case(case)

    def test_heat_contract_accepts_two_sources_with_half_application_scale(self):
        case = self._case("two-source-half-scale", 4000, 13, 4, 0.4)
        thermal_path = case / "thermal_input.json"
        thermal = json.loads(thermal_path.read_text(encoding="utf-8"))
        thermal["heat_sources"] = [
            {
                "name": "heater-a", "power_kw": 1.0,
                "convective_fraction": 0.8,
                "radiative_fraction": 0.2,
                "convective_power_w": 800.0,
                "requested_convective_power_w": 800.0,
                "applied_convective_power_w": 400.0,
                "deferred_convective_power_w": 400.0,
                "radiative_power_w": 200.0,
                "excluded_radiative_power_w": 200.0,
                "application_scale": 0.5,
                "source_element_ids": ["fixture:heater-a"],
                "evidence": "fixture:confirmed", "source_type": "fixture",
            },
            {
                "name": "heater-b", "power_kw": 2.0,
                "convective_fraction": 0.5,
                "radiative_fraction": 0.5,
                "convective_power_w": 1000.0,
                "requested_convective_power_w": 1000.0,
                "applied_convective_power_w": 500.0,
                "deferred_convective_power_w": 500.0,
                "radiative_power_w": 1000.0,
                "excluded_radiative_power_w": 1000.0,
                "application_scale": 0.5,
                "source_element_ids": ["fixture:heater-b"],
                "evidence": "fixture:confirmed", "source_type": "fixture",
            },
        ]
        thermal["heat"] = {
            "input_power_w": 3000.0,
            "requested_convective_power_w": 1800.0,
            "applied_convective_power_w": 900.0,
            "deferred_convective_power_w": 900.0,
            "radiative_power_w": 1200.0,
            "excluded_radiative_power_w": 1200.0,
            "source_count": 2,
            "application_scale": 0.5,
            "model": "equipment_wall_adjacent_cells_v1",
        }
        thermal_path.write_text(json.dumps(thermal), encoding="utf-8")
        self._sync_case_thermal_provenance(case)

        loaded = cfd_gci.load_body_fitted_case(case)

        self.assertEqual(loaded["heat_source_contract"], [
            {
                "name": "heater-a", "power_kw": 1.0,
                "convective_fraction": 0.8,
                "radiative_fraction": 0.2,
                "convective_power_w": 800.0,
                "requested_convective_power_w": 800.0,
                "applied_convective_power_w": 400.0,
                "deferred_convective_power_w": 400.0,
                "radiative_power_w": 200.0,
                "excluded_radiative_power_w": 200.0,
                "application_scale": 0.5,
                "source_element_ids": ["fixture:heater-a"],
                "evidence": "fixture:confirmed", "source_type": "fixture",
            },
            {
                "name": "heater-b", "power_kw": 2.0,
                "convective_fraction": 0.5,
                "radiative_fraction": 0.5,
                "convective_power_w": 1000.0,
                "requested_convective_power_w": 1000.0,
                "applied_convective_power_w": 500.0,
                "deferred_convective_power_w": 500.0,
                "radiative_power_w": 1000.0,
                "excluded_radiative_power_w": 1000.0,
                "application_scale": 0.5,
                "source_element_ids": ["fixture:heater-b"],
                "evidence": "fixture:confirmed", "source_type": "fixture",
            },
        ])

    def test_heat_contract_rejects_aggregate_source_mismatch(self):
        case = self._case("corrupt-heat-contract", 4000, 13, 4, 0.4)
        thermal_path = case / "thermal_input.json"
        thermal = json.loads(thermal_path.read_text(encoding="utf-8"))
        thermal["heat"]["applied_convective_power_w"] = 799.0
        thermal_path.write_text(json.dumps(thermal), encoding="utf-8")
        self._sync_case_thermal_provenance(case)

        with self.assertRaisesRegex(cfd_gci.GCIInputError, "적용 대류발열"):
            cfd_gci.load_body_fitted_case(case)

    def test_heat_contract_rejects_radiative_power_that_differs_from_excluded(self):
        """A copied manifest cannot conceal a different radiation split."""
        case = self._case("corrupt-radiative-contract", 4000, 13, 4, 0.4)
        thermal_path = case / "thermal_input.json"
        thermal = json.loads(thermal_path.read_text(encoding="utf-8"))
        thermal["heat_sources"][0]["radiative_power_w"] = 0.0
        thermal_path.write_text(json.dumps(thermal), encoding="utf-8")
        self._sync_case_thermal_provenance(case)

        with self.assertRaisesRegex(cfd_gci.GCIInputError, "radiative_power_w|복사발열"):
            cfd_gci.load_body_fitted_case(case)

    def test_heat_source_provenance_is_compared_and_written_to_gci(self):
        cases = [
            self._case("coarse-heat", 4000, 13.0, 3.9, 0.39),
            self._case("medium-heat", 8000, 12.25, 3.675, 0.3675),
            self._case("fine-heat", 16000, 12.0, 3.6, 0.36),
        ]
        for case in cases:
            path = case / "thermal_input.json"
            thermal = json.loads(path.read_text(encoding="utf-8"))
            thermal["heat_sources"][0].update({
                "source_id": "DXF:INSERT:AHU-01",
                "source_label": "AHU-01 (기계실)",
                "source_ref": {
                    "handle": "A1B2",
                    "layer": "M-EQPM",
                    "block_name": "EHP_DUCT",
                },
                "source_element_ids": ["DXF:INSERT:AHU-01"],
                "evidence": "equipment_schedule:M03-001",
                "source_type": "user_confirmed",
                "radiative_fraction": 0.2,
                "override_of_dxf": True,
                "provenance": {
                    "source_id": "DXF:INSERT:AHU-01",
                    "source_ref": {"handle": "A1B2", "layer": "M-EQPM"},
                    "evidence": "equipment_schedule:M03-001",
                    "source_type": "user_confirmed",
                    "override_of_dxf": True,
                },
            })
            path.write_text(json.dumps(thermal), encoding="utf-8")
            self._sync_case_thermal_provenance(case)

        output = self.root / "study-heat" / "grid_convergence.json"
        result = cfd_gci.build_grid_convergence(cases, output)

        self.assertTrue(result["ok"], result)
        source = result["manifest"]["comparison"]["heat_source_contract"][0]
        self.assertEqual(source["source_id"], "DXF:INSERT:AHU-01")
        self.assertEqual(source["source_label"], "AHU-01 (기계실)")
        self.assertEqual(source["source_ref"]["handle"], "A1B2")
        self.assertEqual(source["source_element_ids"], ["DXF:INSERT:AHU-01"])
        self.assertEqual(source["evidence"], "equipment_schedule:M03-001")
        self.assertEqual(source["source_type"], "user_confirmed")
        self.assertEqual(source["radiative_fraction"], 0.2)
        self.assertTrue(source["override_of_dxf"])
        self.assertEqual(source["provenance"]["source_ref"]["handle"], "A1B2")
        self.assertTrue(source["provenance"]["override_of_dxf"])
        report = cfd_report.generate_gci_report(output.parent)
        self.assertTrue(report["ok"], report)
        report_html = Path(report["path"]).read_text(encoding="utf-8")
        self.assertIn("검증 열원 계약", report_html)
        self.assertIn("DXF:INSERT:AHU-01", report_html)
        self.assertIn("A1B2", report_html)
        self.assertIn("DXF 원본 + 사용자 변경", report_html)
        self.assertIn("equipment_schedule:M03-001", report_html)

        changed = json.loads((cases[1] / "thermal_input.json").read_text(
            encoding="utf-8"
        ))
        changed["heat_sources"][0]["evidence"] = "equipment_schedule:M03-002"
        (cases[1] / "thermal_input.json").write_text(
            json.dumps(changed), encoding="utf-8"
        )
        self._sync_case_thermal_provenance(cases[1])
        mismatch = cfd_gci.build_grid_convergence(cases)
        self.assertFalse(mismatch["ok"])
        self.assertIn("조건", mismatch["error"])

    def test_heat_contract_rejects_raw_dxf_detected_source(self):
        """A raw drawing detection cannot become a design-ready thermal input."""
        case = self._case("raw-dxf-heat", 4000, 13, 4, 0.4)
        thermal_path = case / "thermal_input.json"
        thermal = json.loads(thermal_path.read_text(encoding="utf-8"))
        thermal["heat_sources"][0].update({
            "source_id": "DXF:INSERT:EHP-01",
            "source_ref": {"handle": "EHP1", "layer": "DVM_INDOOR"},
            "evidence": "equipment_schedule:M03-001",
            "source_type": "dxf_detected",
            "radiative_fraction": 0.2,
        })
        thermal_path.write_text(json.dumps(thermal), encoding="utf-8")
        self._sync_case_thermal_provenance(case)

        with self.assertRaisesRegex(cfd_gci.GCIInputError, "dxf_detected"):
            cfd_gci.load_body_fitted_case(case)

    def test_production_heat_source_accepts_only_user_confirmed_type(self):
        """GCI/design evidence cannot cite a legacy or unknown source type."""
        for source_type in ("legacy_manual_input", "geometry_confirmed", "auto"):
            with self.subTest(source_type=source_type):
                case = self._case(
                    f"unconfirmed-type-{source_type}", 4000, 13, 4, 0.4
                )
                thermal_path = case / "thermal_input.json"
                thermal = json.loads(thermal_path.read_text(encoding="utf-8"))
                thermal["heat_sources"][0].update({
                    "source_id": "DXF:INSERT:EHP-01",
                    "source_ref": {
                        "handle": "EHP1", "layer": "DVM_INDOOR"
                    },
                    "evidence": "equipment_schedule:M03-001",
                    "source_type": source_type,
                    "radiative_fraction": 0.2,
                })
                thermal_path.write_text(json.dumps(thermal), encoding="utf-8")
                self._sync_case_thermal_provenance(case)

                with self.assertRaisesRegex(
                        cfd_gci.GCIInputError, "source_type|user_confirmed"):
                    cfd_gci.load_body_fitted_case(case)

    def test_production_heat_source_requires_traceable_origin_fields(self):
        """Unlike fixture sources, real heat inputs need DXF identity and evidence."""
        fields = (
            ("source_id", ""),
            ("source_ref", {}),
            ("evidence", ""),
            ("source_type", ""),
        )
        for field, missing_value in fields:
            with self.subTest(field=field):
                case = self._case(f"missing-{field}", 4000, 13, 4, 0.4)
                thermal_path = case / "thermal_input.json"
                thermal = json.loads(thermal_path.read_text(encoding="utf-8"))
                thermal["heat_sources"][0].update({
                    "source_id": "DXF:INSERT:EHP-01",
                    "source_ref": {"handle": "EHP1", "layer": "DVM_INDOOR"},
                    "evidence": "equipment_schedule:M03-001",
                    "source_type": "user_confirmed",
                    "radiative_fraction": 0.2,
                    field: missing_value,
                })
                thermal_path.write_text(json.dumps(thermal), encoding="utf-8")
                self._sync_case_thermal_provenance(case)

                with self.assertRaisesRegex(cfd_gci.GCIInputError, field):
                    cfd_gci.load_body_fitted_case(case)

    def test_production_heat_source_requires_meaningful_source_reference(self):
        """A populated object containing only blank values is not provenance."""
        empty_references = (
            {"handle": ""},
            {"handle": "   ", "layer": "\t"},
            {"nested": {"handle": ""}},
            {"handles": []},
        )
        for index, source_ref in enumerate(empty_references):
            with self.subTest(source_ref=source_ref):
                case = self._case(f"empty-source-ref-{index}", 4000, 13, 4, 0.4)
                thermal_path = case / "thermal_input.json"
                thermal = json.loads(thermal_path.read_text(encoding="utf-8"))
                thermal["heat_sources"][0].update({
                    "source_id": "DXF:INSERT:EHP-01",
                    "source_ref": source_ref,
                    "evidence": "equipment_schedule:M03-001",
                    "source_type": "user_confirmed",
                    "radiative_fraction": 0.2,
                })
                thermal_path.write_text(json.dumps(thermal), encoding="utf-8")
                self._sync_case_thermal_provenance(case)

                with self.assertRaisesRegex(cfd_gci.GCIInputError, "source_ref"):
                    cfd_gci.load_body_fitted_case(case)

    def test_production_heat_source_rejects_annotation_only_source_reference(self):
        """A free-text annotation cannot prove the CAD/manual origin of a load."""
        case = self._case("note-only-source-ref", 4000, 13, 4, 0.4)
        thermal_path = case / "thermal_input.json"
        thermal = json.loads(thermal_path.read_text(encoding="utf-8"))
        thermal["heat_sources"][0].update({
            "source_id": "DXF:INSERT:EHP-01",
            "source_ref": {"note": "not a CAD identity"},
            "evidence": "equipment_schedule:M03-001",
            "source_type": "user_confirmed",
            "radiative_fraction": 0.2,
        })
        thermal_path.write_text(json.dumps(thermal), encoding="utf-8")
        self._sync_case_thermal_provenance(case)

        with self.assertRaisesRegex(cfd_gci.GCIInputError, "source_ref"):
            cfd_gci.load_body_fitted_case(case)

    def test_production_heat_source_override_marker_must_be_boolean(self):
        case = self._case("invalid-override-marker", 4000, 13, 4, 0.4)
        thermal_path = case / "thermal_input.json"
        thermal = json.loads(thermal_path.read_text(encoding="utf-8"))
        thermal["heat_sources"][0].update({
            "source_id": "DXF:INSERT:EHP-01",
            "source_ref": {"handle": "EHP1", "layer": "DVM_INDOOR"},
            "evidence": "equipment_schedule:M03-001",
            "source_type": "user_confirmed",
            "radiative_fraction": 0.2,
            "override_of_dxf": "true",
        })
        thermal_path.write_text(json.dumps(thermal), encoding="utf-8")
        self._sync_case_thermal_provenance(case)

        with self.assertRaisesRegex(cfd_gci.GCIInputError, "override_of_dxf"):
            cfd_gci.load_body_fitted_case(case)

    def test_heat_contract_rejects_duplicate_production_source_id(self):
        case = self._case("duplicate-heat-id", 4000, 13, 4, 0.4)
        thermal_path = case / "thermal_input.json"
        thermal = json.loads(thermal_path.read_text(encoding="utf-8"))
        source = thermal["heat_sources"][0]
        source.update({
            "source_id": "DXF:INSERT:EHP-01",
            "source_ref": {"handle": "EHP1", "layer": "DVM_INDOOR"},
            "evidence": "equipment_schedule:M03-001",
            "source_type": "user_confirmed",
            "radiative_fraction": 0.2,
        })
        copied = dict(source)
        copied["name"] = "heater-copy"
        thermal["heat_sources"] = [source, copied]
        thermal["heat"].update({
            "input_power_w": 2000.0,
            "requested_convective_power_w": 1600.0,
            "applied_convective_power_w": 1600.0,
            "deferred_convective_power_w": 0.0,
            "radiative_power_w": 400.0,
            "excluded_radiative_power_w": 400.0,
            "source_count": 2,
        })
        thermal_path.write_text(json.dumps(thermal), encoding="utf-8")
        self._sync_case_thermal_provenance(case)

        with self.assertRaisesRegex(cfd_gci.GCIInputError, "duplicate.*source_id"):
            cfd_gci.load_body_fitted_case(case)

    def test_heat_contract_rejects_case_variant_duplicate_source_id(self):
        """DXF identity must not double-inject heat only by changing letter case."""
        case = self._case("duplicate-heat-id-case", 4000, 13, 4, 0.4)
        thermal_path = case / "thermal_input.json"
        thermal = json.loads(thermal_path.read_text(encoding="utf-8"))
        source = thermal["heat_sources"][0]
        source.update({
            "source_id": "DXF:INSERT:EHP-01",
            "source_ref": {"handle": "EHP1", "layer": "DVM_INDOOR"},
            "evidence": "equipment_schedule:M03-001",
            "source_type": "user_confirmed",
            "radiative_fraction": 0.2,
        })
        copied = dict(source)
        copied["name"] = "heater-case-copy"
        copied["source_id"] = "dxf:insert:ehp-01"
        thermal["heat_sources"] = [source, copied]
        thermal["heat"].update({
            "input_power_w": 2000.0,
            "requested_convective_power_w": 1600.0,
            "applied_convective_power_w": 1600.0,
            "deferred_convective_power_w": 0.0,
            "radiative_power_w": 400.0,
            "excluded_radiative_power_w": 400.0,
            "source_count": 2,
        })
        thermal_path.write_text(json.dumps(thermal), encoding="utf-8")
        self._sync_case_thermal_provenance(case)

        with self.assertRaisesRegex(cfd_gci.GCIInputError, "duplicate.*source_id"):
            cfd_gci.load_body_fitted_case(case)

    def test_heat_contract_rejects_radiative_fraction_without_closure(self):
        """Radiation share must reconcile with the saved W-level accounting."""
        case = self._case("radiative-fraction-mismatch", 4000, 13, 4, 0.4)
        thermal_path = case / "thermal_input.json"
        thermal = json.loads(thermal_path.read_text(encoding="utf-8"))
        thermal["heat_sources"][0].update({
            "source_id": "DXF:INSERT:EHP-01",
            "source_ref": {"handle": "EHP1", "layer": "DVM_INDOOR"},
            "evidence": "equipment_schedule:M03-001",
            "source_type": "user_confirmed",
            "convective_fraction": 0.75,
            "radiative_fraction": 0.2,
            "convective_power_w": 750.0,
            "requested_convective_power_w": 750.0,
            "applied_convective_power_w": 750.0,
            "deferred_convective_power_w": 0.0,
            "radiative_power_w": 250.0,
            "excluded_radiative_power_w": 250.0,
        })
        thermal["heat"].update({
            "requested_convective_power_w": 750.0,
            "applied_convective_power_w": 750.0,
            "deferred_convective_power_w": 0.0,
            "radiative_power_w": 250.0,
            "excluded_radiative_power_w": 250.0,
        })
        thermal_path.write_text(json.dumps(thermal), encoding="utf-8")
        self._sync_case_thermal_provenance(case)

        with self.assertRaisesRegex(cfd_gci.GCIInputError, "radiative_fraction"):
            cfd_gci.load_body_fitted_case(case)

    def test_heat_contract_rejects_fraction_gap_hidden_by_watt_tolerance(self):
        """Fraction closure uses fraction tolerance, not the 0.001 W tolerance."""
        case = self._case("fraction-gap-low-power", 4000, 13, 4, 0.4)
        thermal_path = case / "thermal_input.json"
        thermal = json.loads(thermal_path.read_text(encoding="utf-8"))
        thermal["heat_sources"][0].update({
            "power_kw": 0.001,
            "convective_fraction": 0.8,
            "radiative_fraction": 0.1995,
            "convective_power_w": 0.8,
            "requested_convective_power_w": 0.8,
            "applied_convective_power_w": 0.8,
            "deferred_convective_power_w": 0.0,
            "radiative_power_w": 0.1995,
            "excluded_radiative_power_w": 0.2,
        })
        thermal["heat"].update({
            "input_power_w": 1.0,
            "requested_convective_power_w": 0.8,
            "applied_convective_power_w": 0.8,
            "deferred_convective_power_w": 0.0,
            "radiative_power_w": 0.1995,
            "excluded_radiative_power_w": 0.2,
        })
        thermal_path.write_text(json.dumps(thermal), encoding="utf-8")
        self._sync_case_thermal_provenance(case)

        with self.assertRaisesRegex(cfd_gci.GCIInputError, "radiative_fraction"):
            cfd_gci.load_body_fitted_case(case)

    def test_fixture_heat_source_still_requires_radiative_fraction(self):
        """Fixture provenance is relaxed, but its physical heat split is not."""
        case = self._case("fixture-radiative-fraction", 4000, 13, 4, 0.4)
        thermal_path = case / "thermal_input.json"
        thermal = json.loads(thermal_path.read_text(encoding="utf-8"))
        del thermal["heat_sources"][0]["radiative_fraction"]
        thermal_path.write_text(json.dumps(thermal), encoding="utf-8")
        self._sync_case_thermal_provenance(case)

        with self.assertRaisesRegex(cfd_gci.GCIInputError, "radiative_fraction"):
            cfd_gci.load_body_fitted_case(case)

    def test_non_monotonic_metric_fails_gate_but_writes_study(self):
        coarse = self._case("coarse", 4000, 13.0, 3.9, 0.39)
        medium = self._case("medium", 8000, 11.5, 3.5, 0.35)
        fine = self._case("fine", 16000, 12.0, 3.6, 0.36)
        result = cfd_gci.build_grid_convergence([coarse, medium, fine])
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["manifest"]["status"], "FAIL")
        failed = {row["key"]: row for row in result["manifest"]["metrics"]}
        self.assertEqual(failed["temperature_max_rise_k"]["convergence"],
                         "non_monotonic")

    def test_schema_contract_is_available(self):
        schema = json.loads((self.repo / "grid_convergence.v1.schema.json").read_text(
            encoding="utf-8"
        ))
        self.assertEqual(schema["properties"]["contract"]["const"],
                         "grid_convergence.v1")

    def test_v2_uses_volume_weighted_late_time_window(self):
        coarse = self._window_case("coarse-v2", 8, 3.9, 0.39)
        medium = self._window_case("medium-v2", 64, 3.675, 0.3675)
        fine = self._window_case("fine-v2", 512, 3.6, 0.36)
        output = self.root / "study-v2" / "grid_convergence.json"
        result = cfd_gci.build_grid_convergence(
            [medium, coarse, fine], output, contract="grid_convergence.v2"
        )
        self.assertTrue(result["ok"], result)
        manifest = result["manifest"]
        self.assertEqual(manifest["contract"], "grid_convergence.v2")
        self.assertEqual(manifest["status"], "PASS")
        self.assertEqual(
            {row["key"] for row in manifest["metrics"]},
            {
                "temperature_volume_mean_rise_k",
                "temperature_volume_p95_rise_k",
                "velocity_volume_p95_m_s",
            },
        )
        self.assertTrue(all(row["time_window"]["snapshot_count"] == 5
                            for row in manifest["cases"]))
        self.assertEqual(
            manifest["comparison"]["maximum_temperature_usage"],
            "diagnostic_only_not_a_gate",
        )
        self.assertEqual(
            json.loads(output.read_text(encoding="utf-8"))["schema_version"], 2
        )
        report = cfd_report.generate_gci_report(output.parent)
        self.assertTrue(report["ok"], report)
        report_html = Path(report["path"]).read_text(encoding="utf-8")
        self.assertIn("셀 체적으로 가중", report_html)
        self.assertIn("시간창 스냅샷", report_html)

    def test_v2_reuses_fixed_mesh_volume_field_across_snapshots(self):
        case = self._window_case("shared-volume-v2", 8, 3.9, 0.39)
        for time_s in ("54", "55.5", "57", "58.5"):
            (case / time_s / "V").unlink()

        loaded = cfd_gci.load_time_window_case(case)

        self.assertEqual(loaded["time_window"]["snapshot_count"], 5)
        self.assertAlmostEqual(
            loaded["metrics"]["temperature_volume_mean_rise_k"], 3.9,
        )

    def test_v2_uses_full_precision_run_time_for_final_snapshot(self):
        case = self._window_case("precise-final-v2", 8, 3.9, 0.39)
        summary_path = case / "results" / "body_fitted_summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["time_s"] = 59.9996
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
        result_path = case / "result_manifest.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["summary_sha256"] = cfd_gci._file_sha256(summary_path)
        result_path.write_text(json.dumps(result), encoding="utf-8")

        loaded = cfd_gci.load_time_window_case(case)

        self.assertEqual(loaded["time_s"], 60.0)
        self.assertEqual(loaded["time_window"]["end_time_s"], 60.0)
        self.assertEqual(loaded["time_window"]["snapshot_count"], 5)

    def test_v2_rejects_short_snapshot_results(self):
        cases = [
            self._case("short-a", 8, 4, 3.9, 0.39),
            self._case("short-b", 64, 3.8, 3.7, 0.37),
            self._case("short-c", 512, 3.6, 3.5, 0.35),
        ]
        result = cfd_gci.build_grid_convergence(
            cases, contract="grid_convergence.v2"
        )
        self.assertFalse(result["ok"])
        self.assertIn("1.0", result["error"])

    def test_v2_rejects_actual_refinement_ratios_below_minimum(self):
        cases = [
            self._window_case("close-coarse", 8, 3.9, 0.39),
            self._window_case("close-medium", 12, 3.7, 0.37),
            self._window_case("close-fine", 18, 3.6, 0.36),
        ]

        result = cfd_gci.build_grid_convergence(
            cases, contract="grid_convergence.v2"
        )

        self.assertFalse(result["ok"])
        self.assertIn("1.25", result["error"])

    def test_v2_schema_contract_is_available(self):
        schema = json.loads((self.repo / "grid_convergence.v2.schema.json").read_text(
            encoding="utf-8"
        ))
        self.assertEqual(schema["properties"]["contract"]["const"],
                         "grid_convergence.v2")

    def test_v3_eca_hoekstra_four_grid_fit_passes_smooth_quadratic_data(self):
        widths = [1.0, 1.3, 1.7, 2.2]
        values = [10.0 + 0.1 * width ** 2 for width in widths]

        metric = cfd_gci.calculate_metric_lsr(
            "temperature_volume_mean_rise_k", "mean", "K", values, widths,
        )

        self.assertEqual(metric["status"], "PASS")
        self.assertEqual(metric["uncertainty_method"], "eca_hoekstra_lsr_2014")
        self.assertEqual(metric["error_estimator"], "richardson")
        self.assertAlmostEqual(metric["observed_order"], 2.0, places=5)
        self.assertLess(metric["uncertainty_fine_pct"], 5.0)

    def test_v3_anomalous_scatter_is_not_mislabeled_as_gci(self):
        metric = cfd_gci.calculate_metric_lsr(
            "velocity_volume_p95_m_s", "velocity", "m/s",
            [0.30, 0.36, 0.31, 0.37], [1.0, 1.3, 1.7, 2.2],
        )

        self.assertEqual(metric["status"], "FAIL")
        self.assertIsNone(metric["gci_fine_pct"])
        self.assertGreater(metric["uncertainty_fine_pct"], 5.0)

    def test_v3_matches_marin_2014_reference_implementation(self):
        # Four-grid G2 values cross-checked with MARIN's official
        # numerical_uncertainty_std wrapper using iProcedure=2014.
        widths = [
            1.0,
            1.3106984809996607,
            1.743475193134829,
            2.2585040370575964,
        ]
        cases = (
            (
                "temperature_volume_mean_rise_k",
                [3.3760896814363344, 3.476449142046115,
                 3.457930649793182, 3.614829720460331],
                1.67357877714152,
                3.319566361186242,
                0.1694353838044657,
                "richardson",
            ),
            (
                "temperature_volume_p95_rise_k",
                [5.635395931273976, 5.288166175578047,
                 5.131835812335907, 5.25133489153664],
                None,
                7.682770255363745,
                6.197851128576589,
                "first_second_order",
            ),
            (
                "velocity_volume_p95_m_s",
                [0.33921832406809405, 0.3517844109245877,
                 0.33488081707975426, 0.3291117868913231],
                3.86807328527407,
                0.3493615418447118,
                0.05801218075286875,
                "second_order",
            ),
        )

        for key, values, order, extrapolated, uncertainty, estimator in cases:
            with self.subTest(key=key):
                metric = cfd_gci.calculate_metric_lsr(
                    key, key, "", values, widths,
                )
                if order is None:
                    self.assertIsNone(metric["observed_order"])
                else:
                    self.assertAlmostEqual(metric["observed_order"], order,
                                           places=5)
                self.assertEqual(metric["error_estimator"], estimator)
                self.assertAlmostEqual(metric["extrapolated"], extrapolated,
                                       places=5)
                self.assertAlmostEqual(metric["uncertainty_fine"], uncertainty,
                                       places=5)

    def test_v3_builds_four_grid_time_window_uncertainty_report(self):
        # Cell counts give exact effective-width ratios of two after sorting.
        cases = [
            self._window_case("very-coarse-v3", 8, 10.64, 0.3192,
                              flow_fraction=3.0),
            self._window_case("coarse-v3", 64, 10.16, 0.3048,
                              flow_fraction=3.0),
            self._window_case("medium-v3", 512, 10.04, 0.3012,
                              flow_fraction=3.0),
            self._window_case("fine-v3", 4096, 10.01, 0.3003,
                              flow_fraction=3.0),
        ]
        output = self.root / "study-v3" / "grid_convergence.json"

        result = cfd_gci.build_grid_convergence(
            cases, output, contract="grid_convergence.v3",
        )

        self.assertTrue(result["ok"], result)
        manifest = result["manifest"]
        self.assertEqual(manifest["status"], "PASS")
        self.assertEqual(manifest["comparison"]["grid_count"], 4)
        self.assertEqual(len(manifest["comparison"]["refinement_ratios_fine_to_coarse"]), 3)
        self.assertTrue(all(row["uncertainty_fine_pct"] <= 5
                            for row in manifest["metrics"]))
        report = cfd_report.generate_gci_report(output.parent)
        self.assertTrue(report["ok"], report)
        html = Path(report["path"]).read_text(encoding="utf-8")
        self.assertIn("4수준 메시 불확실성 보고서", html)
        self.assertIn("Eça–Hoekstra", html)

    def test_v3_schema_contract_is_available(self):
        schema = json.loads((self.repo / "grid_convergence.v3.schema.json").read_text(
            encoding="utf-8"
        ))
        self.assertEqual(schema["properties"]["contract"]["const"],
                         "grid_convergence.v3")
        self.assertIn("provenance", schema["properties"]["cases"]["items"]["required"])
        self.assertIn("heat_source_contract", schema["properties"]["comparison"]["required"])

    def test_v3_records_current_case_provenance(self):
        cases = [
            self._window_case("very-coarse-provenance", 8, 10.64, 0.3192,
                              flow_fraction=3.0),
            self._window_case("coarse-provenance", 64, 10.16, 0.3048,
                              flow_fraction=3.0),
            self._window_case("medium-provenance", 512, 10.04, 0.3012,
                              flow_fraction=3.0),
            self._window_case("fine-provenance", 4096, 10.01, 0.3003,
                              flow_fraction=3.0),
        ]
        result = cfd_gci.build_grid_convergence(cases, contract="grid_convergence.v3")

        self.assertTrue(result["ok"], result)
        provenance = result["manifest"]["cases"][0]["provenance"]
        self.assertEqual(set(provenance), {
            "run_manifest_sha256", "result_manifest_sha256",
            "mesh_manifest_sha256", "thermal_input_sha256",
        })


if __name__ == "__main__":
    unittest.main()
