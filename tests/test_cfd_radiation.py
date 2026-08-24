import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import cfd_radiation


class RadiationContractTests(unittest.TestCase):
    def _enclosure_surfaces(self):
        return [
            {
                "name": "floor",
                "mesh_patch_name": "airVolume_floor",
                "role": "wall",
                "participating": True,
                "emissivity": 0.90,
                "material_source": "finish_schedule:floor-01",
                "thermal_boundary": {"type": "adiabatic"},
            },
            {
                "name": "ceiling",
                "mesh_patch_name": "airVolume_ceiling",
                "role": "wall",
                "participating": True,
                "emissivity": 0.85,
                "material_source": "finish_schedule:ceiling-01",
                "thermal_boundary": {"type": "adiabatic"},
            },
        ]

    def test_rejects_non_body_fitted_radiation_request(self):
        with self.assertRaisesRegex(cfd_radiation.RadiationInputError, "body-fitted"):
            cfd_radiation.validate_radiation_input(
                engine="screening_voxel_v3",
                surfaces=self._enclosure_surfaces(),
            )

    def test_rejects_collapsed_or_underspecified_enclosure_surfaces(self):
        surfaces = self._enclosure_surfaces()
        surfaces[1]["mesh_patch_name"] = surfaces[0]["mesh_patch_name"]
        with self.assertRaisesRegex(cfd_radiation.RadiationInputError, "분리된 mesh patch"):
            cfd_radiation.validate_radiation_input(
                engine="body_fitted_buoyant_urans", surfaces=surfaces
            )

        surfaces = self._enclosure_surfaces()
        del surfaces[1]["material_source"]
        with self.assertRaisesRegex(cfd_radiation.RadiationInputError, "재질 출처"):
            cfd_radiation.validate_radiation_input(
                engine="body_fitted_buoyant_urans", surfaces=surfaces
            )

    def test_two_plate_reference_flux_matches_closed_form(self):
        flux = cfd_radiation.two_plate_net_flux_w_m2(
            hot_temperature_k=400.0,
            cold_temperature_k=300.0,
            hot_emissivity=0.8,
            cold_emissivity=0.8,
        )
        self.assertAlmostEqual(flux, 661.544, places=3)

    def test_two_plate_reference_artifact_is_self_consistent(self):
        reference = cfd_radiation.load_two_plate_reference()
        inputs = reference["input"]
        expected = cfd_radiation.two_plate_net_flux_w_m2(
            hot_temperature_k=inputs["hot_temperature_k"],
            cold_temperature_k=inputs["cold_temperature_k"],
            hot_emissivity=inputs["hot_emissivity"],
            cold_emissivity=inputs["cold_emissivity"],
        )
        self.assertEqual(reference["contract"], "radiation_benchmark_reference.v1")
        self.assertAlmostEqual(expected, reference["expected"]["net_flux_w_m2"], places=6)
        self.assertEqual(reference["acceptance"]["solver_flux_relative_error_max"], 0.05)

    def test_view_factor_diagnostics_require_closure_and_reciprocity(self):
        valid = cfd_radiation.view_factor_diagnostics(
            areas_m2=[2.0, 1.0],
            view_factors=[[0.5, 0.5], [1.0, 0.0]],
        )
        self.assertTrue(valid["ok"])
        self.assertAlmostEqual(valid["max_row_sum_error"], 0.0)
        self.assertAlmostEqual(valid["max_reciprocity_error_m2"], 0.0)

        invalid = cfd_radiation.view_factor_diagnostics(
            areas_m2=[2.0, 1.0],
            view_factors=[[0.0, 1.0], [1.0, 0.0]],
        )
        self.assertFalse(invalid["ok"])
        self.assertGreater(invalid["max_reciprocity_error_m2"], 0.0)

    def test_radiation_manifest_rejects_summary_without_actual_benchmark_artifacts(self):
        """A copied JSON summary is never sufficient for a radiation claim."""
        thermal_hash = hashlib.sha256(b"thermal-input").hexdigest()
        manifest = {
            "contract": "radiation_manifest.v1",
            "status": "PASS",
            "thermal_input_sha256": thermal_hash,
            "benchmark_reference_sha256": cfd_radiation.benchmark_reference_sha256(),
            "view_factors": {
                "ok": True,
                "max_row_sum_error": 0.0,
                "max_reciprocity_error_m2": 0.0,
            },
            "energy_balance": {"internal_radiation_balance_relative_error": 0.0},
            "fields": {"qr_nonzero": True},
            "patch_net_radiation_power_w": {"hot_plate": -100.0, "cold_plate": 100.0},
        }

        with self.assertRaisesRegex(cfd_radiation.RadiationInputError, "case_dir"):
            cfd_radiation.validate_radiation_manifest(
                manifest, thermal_input_sha256=thermal_hash
            )

    def test_two_plate_generator_creates_serial_view_factor_case_with_pinned_sources(self):
        """Removing a required OF-v2606 input or adding MPI must fail this contract."""
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-radiation-case-", dir=repo) as tmp:
            case = Path(tmp) / "two-plate"

            created = cfd_radiation.build_two_plate_view_factor_case(case)

            source = json.loads((case / "radiation_benchmark_input.json").read_text(
                encoding="utf-8"
            ))
            radiation_properties = (case / "constant" / "radiationProperties").read_text(
                encoding="utf-8"
            )
            view_factors = (case / "constant" / "viewFactorsDict").read_text(
                encoding="utf-8"
            )
            qr = (case / "0" / "qr").read_text(encoding="utf-8")
            allrun = (case / "Allrun.serial").read_text(encoding="utf-8")
            block_mesh = (case / "system" / "blockMeshDict").read_text(
                encoding="utf-8"
            )

        self.assertEqual(created["contract"], "two_plate_view_factor_case.v1")
        self.assertEqual(created["execution_mode"], "serial_only")
        self.assertEqual(source["execution_mode"], "serial_only")
        self.assertEqual(source["reference_sha256"], cfd_radiation.benchmark_reference_sha256())
        self.assertIn("radiationModel  viewFactor;", radiation_properties)
        self.assertIn("raySearchEngine", view_factors)
        self.assertIn("viewFactorWall", block_mesh)
        self.assertIn("greyDiffusiveRadiationViewFactor", qr)
        self.assertIn("createViewFactors", allrun)
        self.assertNotIn("mpirun", allrun)
        self.assertNotIn("decomposePar", allrun)
        self.assertEqual(
            set(source["source_hashes"]),
            {
                "0/T", "0/U", "0/alphat", "0/k", "0/nut", "0/omega", "0/p_rgh", "0/qr",
                "Allrun.serial", "constant/boundaryRadiationProperties",
                "constant/g", "constant/radiationProperties", "constant/transportProperties",
                "constant/turbulenceProperties",
                "constant/viewFactorsDict", "system/blockMeshDict", "system/controlDict",
                "system/fvSchemes", "system/fvSolution",
            },
        )

    def test_two_plate_generator_refuses_to_write_into_field_case(self):
        """Pointing the benchmark generator at a project solver case must be rejected."""
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-radiation-field-guard-", dir=repo) as tmp:
            field_case = Path(tmp) / "thermal_case"
            field_case.mkdir()
            (field_case / "thermal_input.json").write_text("{}\n", encoding="utf-8")

            with self.assertRaisesRegex(cfd_radiation.RadiationInputError, "standalone"):
                cfd_radiation.build_two_plate_view_factor_case(field_case)

    def test_two_plate_generator_refuses_nonempty_generic_openfoam_case(self):
        """A generic existing case must not be overwritten just because it lacks markers."""
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-radiation-case-guard-", dir=repo) as tmp:
            existing_case = Path(tmp) / "other-openfoam-case"
            (existing_case / "system").mkdir(parents=True)
            (existing_case / "system" / "controlDict").write_text(
                "application simpleFoam;\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(cfd_radiation.RadiationInputError, "empty"):
                cfd_radiation.build_two_plate_view_factor_case(existing_case)

    def test_two_plate_collector_fails_closed_until_solver_outputs_exist(self):
        """A generated input case alone must never look like a radiation result."""
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-radiation-empty-", dir=repo) as tmp:
            case = Path(tmp) / "two-plate"
            cfd_radiation.build_two_plate_view_factor_case(case)

            manifest = cfd_radiation.collect_two_plate_view_factor_evidence(case)

            stored = json.loads((case / "radiation_manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["contract"], "radiation_manifest.v1")
        self.assertEqual(manifest["status"], "NOT_EVALUATED")
        self.assertFalse(manifest["fields"]["qr_nonzero"])
        self.assertIn("VIEW_FACTOR_MATRIX_MISSING", manifest["blockers"])
        self.assertIn("QR_FIELD_MISSING", manifest["blockers"])
        self.assertEqual(stored, manifest)

    def test_two_plate_collector_records_source_drift_in_manifest(self):
        """Changing a generated input must invalidate, rather than reuse, evidence."""
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-radiation-source-drift-", dir=repo) as tmp:
            case = Path(tmp) / "two-plate"
            cfd_radiation.build_two_plate_view_factor_case(case)
            temperature = case / "0" / "T"
            temperature.write_text(
                temperature.read_text(encoding="utf-8") + "// changed after generation\n",
                encoding="utf-8",
            )

            manifest = cfd_radiation.collect_two_plate_view_factor_evidence(case)

        self.assertEqual(manifest["status"], "NOT_EVALUATED")
        self.assertFalse(manifest["source_integrity"]["ok"])
        self.assertIn("SOURCE_HASH_MISMATCH:0/T", manifest["blockers"])
        self.assertIn("0/T", manifest["source_integrity"]["expected_hashes"])
        self.assertIn("0/T", manifest["source_integrity"]["observed_hashes"])

    def test_two_plate_collector_parses_solver_qr_and_view_factor_matrix(self):
        """Breaking qr/F parsing must turn a valid serial solver artifact into NOT_EVALUATED."""
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-radiation-output-", dir=repo) as tmp:
            case = Path(tmp) / "two-plate"
            cfd_radiation.build_two_plate_view_factor_case(case)
            (case / "constant" / "F").write_text(
                "FoamFile\n{\n    version 2.0;\n}\n\n6\n(\n"
                "(0 0.98 0.005 0.005 0.005 0.005)\n"
                "(0.98 0 0.005 0.005 0.005 0.005)\n"
                "(0.5 0.5 0 0 0 0)\n"
                "(0.5 0.5 0 0 0 0)\n"
                "(0.5 0.5 0 0 0 0)\n"
                "(0.5 0.5 0 0 0 0)\n"
                ")\n",
                encoding="utf-8",
            )
            result_dir = case / "1"
            result_dir.mkdir()
            (result_dir / "qr").write_text(
                "FoamFile\n{\n    version 2.0;\n}\n\n"
                "dimensions [1 0 -3 0 0 0 0];\ninternalField uniform 0;\n"
                "boundaryField\n{\n"
                "hot_plate { type calculated; value uniform -661.543682216667; }\n"
                "cold_plate { type calculated; value uniform 661.543682216667; }\n"
                "side_xmin { type calculated; value uniform 0; }\n"
                "side_xmax { type calculated; value uniform 0; }\n"
                "side_ymin { type calculated; value uniform 0; }\n"
                "side_ymax { type calculated; value uniform 0; }\n"
                "}\n",
                encoding="utf-8",
            )

            manifest = cfd_radiation.collect_two_plate_view_factor_evidence(case)
            validated = cfd_radiation.validate_radiation_manifest(
                manifest, thermal_input_sha256=None, case_dir=case
            )

        self.assertEqual(manifest["status"], "PASS")
        self.assertTrue(validated["validated_for_benchmark"])
        self.assertTrue(manifest["fields"]["qr_nonzero"])
        self.assertAlmostEqual(
            manifest["patch_net_radiation_power_w"]["hot_plate"],
            -661.543682216667,
            places=9,
        )
        self.assertAlmostEqual(
            manifest["patch_net_radiation_power_w"]["cold_plate"],
            661.543682216667,
            places=9,
        )
        self.assertTrue(manifest["view_factors"]["ok"])
        self.assertEqual(manifest["solver_outputs"]["qr_time"], "1")
        self.assertIn("constant/F", manifest["solver_outputs"]["hashes"])

    def test_two_plate_collector_rejects_reversed_reference_flux_direction(self):
        """A balance-preserving but cold-to-hot transfer is not the plate benchmark."""
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-radiation-reversed-", dir=repo) as tmp:
            case = Path(tmp) / "two-plate"
            cfd_radiation.build_two_plate_view_factor_case(case)
            (case / "constant" / "F").write_text(
                "FoamFile\n{\n    version 2.0;\n}\n\n6\n(\n"
                "(0 0.98 0.005 0.005 0.005 0.005)\n"
                "(0.98 0 0.005 0.005 0.005 0.005)\n"
                "(0.5 0.5 0 0 0 0)\n"
                "(0.5 0.5 0 0 0 0)\n"
                "(0.5 0.5 0 0 0 0)\n"
                "(0.5 0.5 0 0 0 0)\n"
                ")\n",
                encoding="utf-8",
            )
            result_dir = case / "1"
            result_dir.mkdir()
            (result_dir / "qr").write_text(
                "FoamFile\n{\n    version 2.0;\n}\n\n"
                "dimensions [1 0 -3 0 0 0 0];\ninternalField uniform 0;\n"
                "boundaryField\n{\n"
                "hot_plate { type calculated; value uniform 661.543682216667; }\n"
                "cold_plate { type calculated; value uniform -661.543682216667; }\n"
                "side_xmin { type calculated; value uniform 0; }\n"
                "side_xmax { type calculated; value uniform 0; }\n"
                "side_ymin { type calculated; value uniform 0; }\n"
                "side_ymax { type calculated; value uniform 0; }\n"
                "}\n",
                encoding="utf-8",
            )

            manifest = cfd_radiation.collect_two_plate_view_factor_evidence(case)

        self.assertEqual(manifest["status"], "NOT_EVALUATED")
        self.assertIn("REFERENCE_FLUX_DIRECTION_INVALID", manifest["blockers"])
