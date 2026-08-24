import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import cfd_mesh
import cfd_occ
from tests.test_cfd_occ import _geometry


def _surface_manifest():
    return {
        "contract": "surface_manifest.v1",
        "air_volume": {
            "volume_m3": 33.6,
            "location_in_mesh": {"point_m": [2.0, 1.5, 1.4]},
        },
        "regions": [
            {
                "name": "wall", "role": "wall", "area_m2": 50.0,
                "aabb": {"min_m": [0, 0, 0], "max_m": [4, 3, 2.8]},
            },
            {
                "name": "supply_A", "role": "supply", "area_m2": 0.16,
                "aabb": {"min_m": [1, 1, 2.8], "max_m": [1.4, 1.4, 2.8]},
            },
        ],
    }


class MeshBuildTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[1]

    def test_resource_estimate_is_bounded_and_uses_padded_aabb(self):
        estimate = cfd_mesh.estimate_resources(_surface_manifest(), {
            "background_cell_m": 0.5,
        })
        self.assertGreater(estimate["background_cells"], 0)
        self.assertLessEqual(estimate["estimated_cells"], 500_000)
        self.assertLess(estimate["bounds_m"]["min"][0], 0)
        self.assertGreater(estimate["bounds_m"]["max"][2], 2.8)

    def test_mesh_manifest_schema_is_valid_json(self):
        schema = json.loads(
            (self.repo / "mesh_manifest.v1.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(schema["properties"]["contract"]["const"], "mesh_manifest.v1")
        self.assertEqual(schema["properties"]["status"]["enum"], ["PASS", "FAIL"])

    def test_excessive_global_cell_limit_is_rejected(self):
        estimate = cfd_mesh.estimate_resources(_surface_manifest(), {
            "max_global_cells": 2_000_001,
            "max_local_cells": 300_000,
        })
        with self.assertRaisesRegex(ValueError, "2,000,000"):
            cfd_mesh._validate_limits(estimate)

    def test_case_builder_creates_no_layer_snappy_pipeline(self):
        manifest = _surface_manifest()
        with tempfile.TemporaryDirectory(prefix=".test-mesh-build-", dir=self.repo) as tmp:
            root = Path(tmp)
            occ = root / "occ"
            occ.mkdir()
            (occ / "air_volume_regions.stl").write_text("solid wall\nendsolid wall\n", encoding="ascii")
            (occ / "surface_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with mock.patch.object(cfd_mesh, "inspect_occ_output", return_value={
                "ok": True, "manifest": manifest,
            }):
                result = cfd_mesh.build_mesh_case(occ, root / "mesh", {
                    "background_cell_m": 0.5,
                })
            self.assertTrue(result["ok"], result)
            snappy = (root / "mesh" / "system" / "snappyHexMeshDict").read_text(encoding="utf-8")
            allmesh = (root / "mesh" / "Allmesh").read_text(encoding="utf-8")
            quality = (root / "mesh" / "system" / "meshQualityDict").read_text(
                encoding="utf-8"
            )
            self.assertIn("addLayers false", snappy)
            self.assertIn("supply_A", snappy)
            self.assertIn("snappyHexMesh -overwrite", allmesh)
            self.assertIn("checkMesh -allTopology -meshQuality", allmesh)
            self.assertIn("checkMesh -allGeometry -allTopology -meshQuality", allmesh)
            self.assertIn("maxConcave 80", quality)

    def test_detailed_preset_keeps_stable_terminals_without_prism_layers(self):
        manifest = _surface_manifest()
        with tempfile.TemporaryDirectory(prefix=".test-detailed-mesh-", dir=self.repo) as tmp:
            root = Path(tmp)
            occ = root / "occ"
            occ.mkdir()
            (occ / "air_volume_regions.stl").write_text(
                "solid wall\nendsolid wall\n", encoding="ascii"
            )
            (occ / "surface_manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            with mock.patch.object(cfd_mesh, "inspect_occ_output", return_value={
                "ok": True, "manifest": manifest,
            }):
                result = cfd_mesh.build_mesh_case(
                    occ, root / "mesh", {"preset": "detailed"}
                )
            snappy = (root / "mesh" / "system" / "snappyHexMeshDict").read_text(
                encoding="utf-8"
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["estimate"]["settings"]["preset"], "detailed")
        self.assertIn("addLayers false", snappy)
        self.assertNotIn("terminalRefine", snappy)
        self.assertNotIn("type searchableBox", snappy)
        self.assertIn("wall", snappy)
        self.assertNotIn("nSurfaceLayers 2", snappy)
        self.assertIn("level (1 2)", snappy)
        self.assertIn("level (2 2)", snappy)
        self.assertIn("nCellsBetweenLevels 2", snappy)
        self.assertIn("minFaceWeight 0.02", snappy)
        self.assertIn("finalLayerThickness 0.3", snappy)
        self.assertIn("nGrow 1", snappy)

    def test_unknown_mesh_preset_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "지원하지 않는 메시 프리셋"):
            cfd_mesh.resolve_settings({"preset": "magic"})

    def test_single_region_detailed_profile_has_no_default_layers(self):
        manifest = _surface_manifest()
        manifest["regions"] = [manifest["regions"][0]]
        cfg = cfd_mesh.resolve_settings({"preset": "detailed"})
        self.assertEqual(
            cfd_mesh._layer_regions(manifest, cfg),
            [],
        )


class MeshGateTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[1]

    def test_check_mesh_parser_extracts_p3a_quality_values(self):
        parsed = cfd_mesh.parse_check_mesh("""
            cells: 12345
            Number of regions: 1 (OK).
            Min volume = 1e-08. Max volume = 0.1. Total volume = 33.5.
            Mesh non-orthogonality Max: 54 average: 8
            Max skewness = 2.4 OK.
            Mesh OK.
        """)
        self.assertTrue(parsed["mesh_ok"])
        self.assertEqual(parsed["cells"], 12345)
        self.assertEqual(parsed["regions"], 1)
        self.assertAlmostEqual(parsed["total_volume_m3"], 33.5)
        self.assertEqual(parsed["max_non_orthogonality"], 54)

    def test_strict_check_parser_records_concavity_as_diagnostic(self):
        parsed = cfd_mesh.parse_check_mesh("""
             ***Concave cells (using face planes) found, number of cells: 143
            Failed 1 mesh checks.
        """)
        self.assertEqual(parsed["concave_cells"], 143)
        self.assertEqual(parsed["failed_checks"], 1)
        self.assertTrue(parsed["fatal"])
        self.assertTrue(parsed["failure_details"])

    def test_layer_parser_uses_final_extrusion_and_patch_table(self):
        parsed = cfd_mesh.parse_layer_report("""
            Extruding 100 out of 120 faces (83.333%). Removed extrusion at 2 faces.
            Extruding 110 out of 120 faces (91.6667%). Removed extrusion at 0 faces.
            Added 210 out of 240 cells (87.5%).
            patch                                          faces    layers   overall thickness
                                                                     [m]       [%]
            -----                                          -----    ------   ---       ---
            airVolume                                      20       1.50     0.0200    70.0
            airVolume_wall                                 100      1.82     0.0363    81.1
            Layer mesh : cells:1000 faces:2000 points:3000
        """)
        self.assertEqual(parsed["extruded_faces"], 110)
        self.assertAlmostEqual(parsed["coverage_ratio"], 0.916667)
        self.assertEqual(parsed["added_cells"], 210)
        self.assertEqual(parsed["patches"][0]["mesh_patch_name"], "airVolume")
        self.assertAlmostEqual(parsed["patches"][1]["average_layers"], 1.82)

    def test_poly_mesh_patch_area_is_computed_from_ascii_files(self):
        with tempfile.TemporaryDirectory(prefix=".test-poly-mesh-", dir=self.repo) as tmp:
            poly = Path(tmp)
            (poly / "points").write_text("""FoamFile{}\n4\n(\n(0 0 0)\n(1 0 0)\n(1 1 0)\n(0 1 0)\n)\n""", encoding="ascii")
            (poly / "faces").write_text("""FoamFile{}\n1\n(\n4(0 1 2 3)\n)\n""", encoding="ascii")
            (poly / "boundary").write_text("""FoamFile{}\n1\n(\nwall\n{\n type wall;\n nFaces 1;\n startFace 0;\n}\n)\n""", encoding="ascii")
            metrics = cfd_mesh.patch_metrics(poly)
        self.assertEqual(metrics["wall"]["faces"], 1)
        self.assertAlmostEqual(metrics["wall"]["area_m2"], 1.0)


@unittest.skipUnless(
    os.environ.get("MEP_CFD_RUN_MESH_TESTS") == "1",
    "set MEP_CFD_RUN_MESH_TESTS=1 for the WSL/OpenFOAM mesh target-runner",
)
class MeshOpenFOAMIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[1]

    def test_no_layer_body_fitted_mesh_passes_all_gates(self):
        data = _geometry(
            [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
            with_obstacles=False,
            with_terminals=False,
        )
        with tempfile.TemporaryDirectory(prefix=".test-body-mesh-", dir=self.repo) as tmp:
            root = Path(tmp)
            geometry = root / "geometry.json"
            geometry.write_text(json.dumps(data), encoding="utf-8")
            occ = cfd_occ.run_occ_job(geometry, root / "occ", timeout=180)
            self.assertTrue(occ.get("ok"), occ)
            built = cfd_mesh.build_mesh_case(root / "occ", root / "mesh", {
                "background_cell_m": 0.5,
                "max_global_cells": 400_000,
                "max_local_cells": 250_000,
            })
            self.assertTrue(built.get("ok"), built)
            result = cfd_mesh.run_mesh_case(root / "mesh")
            self.assertTrue(result.get("ok"), result)
            manifest = result["manifest"]
        self.assertEqual(manifest["status"], "PASS")
        self.assertTrue(manifest["surface"]["closed"])
        self.assertEqual(manifest["mesh"]["regions"], 1)
        self.assertLessEqual(manifest["mesh_volume_error_ratio"], 0.02)
        self.assertTrue(all(item["faces"] > 0 for item in manifest["patches"]))

    def test_obstacles_and_terminals_pass_solver_quality_with_strict_diagnostics(self):
        data = _geometry(
            [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
            with_obstacles=True,
            with_terminals=True,
        )
        with tempfile.TemporaryDirectory(prefix=".test-complex-mesh-", dir=self.repo) as tmp:
            root = Path(tmp)
            geometry = root / "geometry.json"
            geometry.write_text(json.dumps(data), encoding="utf-8")
            occ = cfd_occ.run_occ_job(geometry, root / "occ", timeout=180)
            self.assertTrue(occ.get("ok"), occ)
            built = cfd_mesh.build_mesh_case(root / "occ", root / "mesh", {
                "background_cell_m": 0.5,
                "max_global_cells": 400_000,
                "max_local_cells": 250_000,
            })
            self.assertTrue(built.get("ok"), built)
            result = cfd_mesh.run_mesh_case(root / "mesh")
            self.assertTrue(result.get("ok"), result)
            manifest = result["manifest"]
        self.assertEqual(manifest["status"], "PASS")
        self.assertTrue(manifest["mesh"]["mesh_ok"])
        self.assertEqual(manifest["mesh"]["failed_checks"], 0)
        self.assertGreater(manifest["strict_diagnostics"]["concave_cells"], 0)
        self.assertTrue(manifest["warnings"][0].startswith("STRICT_CONCAVE_CELLS:"))
        self.assertLessEqual(manifest["mesh_volume_error_ratio"], 0.02)

    def test_detailed_mesh_has_stable_local_refinement_without_layers(self):
        data = _geometry(
            [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
            with_obstacles=True,
            with_terminals=True,
        )
        with tempfile.TemporaryDirectory(prefix=".test-detailed-openfoam-", dir=self.repo) as tmp:
            root = Path(tmp)
            geometry = root / "geometry.json"
            geometry.write_text(json.dumps(data), encoding="utf-8")
            occ = cfd_occ.run_occ_job(geometry, root / "occ", timeout=180)
            self.assertTrue(occ.get("ok"), occ)
            built = cfd_mesh.build_mesh_case(
                root / "occ", root / "mesh", {"preset": "detailed"}
            )
            self.assertTrue(built.get("ok"), built)
            result = cfd_mesh.run_mesh_case(root / "mesh")
            self.assertTrue(result.get("ok"), result)
            manifest = result["manifest"]
        self.assertEqual(manifest["profile"], "detailed")
        self.assertFalse(manifest["layer"]["enabled"])
        self.assertEqual(manifest["layer"]["added_cells"], 0)
        self.assertEqual(manifest["y_plus"]["status"], "NOT_APPLICABLE")

    def test_detailed_single_room_uses_refinement_without_wall_layers(self):
        data = _geometry(
            [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
            with_obstacles=False,
            with_terminals=False,
        )
        with tempfile.TemporaryDirectory(prefix=".test-detailed-room-", dir=self.repo) as tmp:
            root = Path(tmp)
            geometry = root / "geometry.json"
            geometry.write_text(json.dumps(data), encoding="utf-8")
            occ = cfd_occ.run_occ_job(geometry, root / "occ", timeout=180)
            self.assertTrue(occ.get("ok"), occ)
            built = cfd_mesh.build_mesh_case(
                root / "occ", root / "mesh", {"preset": "detailed"}
            )
            self.assertTrue(built.get("ok"), built)
            result = cfd_mesh.run_mesh_case(root / "mesh")
            self.assertTrue(result.get("ok"), result)
            manifest = result["manifest"]
        self.assertEqual(manifest["layer"]["expected_patches"], [])
        self.assertEqual(manifest["layer"]["coverage_ratio"], 0.0)


if __name__ == "__main__":
    unittest.main()
