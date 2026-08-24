import json
from pathlib import Path
import tempfile
import unittest

import cfd_export


class OpeningBoundaryVerificationTests(unittest.TestCase):
    def _write_case(self, root, *, predicted_area=1.0):
        case = Path(root)
        (case / "constant" / "polyMesh").mkdir(parents=True)
        (case / "1").mkdir()
        preflight = {
            "contract": "opening_preflight.v2",
            "terminals": [
                {
                    "opening_id": "SA-1", "parent_name": "sup0", "role": "supply",
                    "flow_control": "fixed_normal_velocity", "child_patch_names": ["sup0"],
                    "snapped_area_m2": predicted_area, "design_cmh": 360.0,
                },
                {
                    "opening_id": "RA-1", "parent_name": "exh0", "role": "exhaust",
                    "flow_control": "pressure_outlet", "child_patch_names": ["exh0"],
                    "snapped_area_m2": predicted_area, "design_cmh": 360.0,
                },
            ],
        }
        (case / "cfd_case_meta.json").write_text(json.dumps({
            "config": {}, "patches": [], "opening_preflight": preflight,
        }), encoding="utf-8")
        poly = case / "constant" / "polyMesh"
        (poly / "points").write_text(
            "FoamFile{}\n8\n(\n"
            "(0 0 0)\n(1 0 0)\n(1 1 0)\n(0 1 0)\n"
            "(2 0 0)\n(3 0 0)\n(3 1 0)\n(2 1 0)\n)\n",
            encoding="ascii",
        )
        (poly / "faces").write_text(
            "FoamFile{}\n2\n(\n4(0 1 2 3)\n4(4 5 6 7)\n)\n",
            encoding="ascii",
        )
        (poly / "boundary").write_text(
            "FoamFile{}\n2\n(\nsup0\n{\n type patch;\n nFaces 1;\n startFace 0;\n}\n"
            "exh0\n{\n type patch;\n nFaces 1;\n startFace 1;\n}\n)\n",
            encoding="ascii",
        )
        (case / "1" / "phi").write_text(
            "boundaryField\n{\n"
            "sup0\n{\n value nonuniform List<scalar>\n1\n(\n-0.1\n);\n}\n"
            "exh0\n{\n value nonuniform List<scalar>\n1\n(\n0.1\n);\n}\n}\n",
            encoding="ascii",
        )
        return case

    def test_mesh_area_and_phi_flow_are_verified_without_mutating_case_meta(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-opening-result-", dir=repo) as tmp:
            case = self._write_case(tmp)
            original_meta = (case / "cfd_case_meta.json").read_bytes()
            result = cfd_export.verify_opening_boundary_areas(case)

            self.assertEqual(result["contract"], "opening_boundary_verification.v1")
            self.assertEqual(result["status"], "PASS")
            self.assertTrue((case / "opening_boundary_verification.v1.json").is_file())
            self.assertEqual((case / "cfd_case_meta.json").read_bytes(), original_meta)
            self.assertEqual(result["terminals"][0]["area_status"], "PASS")
            self.assertEqual(result["terminals"][0]["flow_status"], "PASS")
            self.assertEqual(result["terminals"][1]["flow_status"], "PASS")
            self.assertAlmostEqual(result["terminals"][1]["solved_cmh"], 360.0)

    def test_actual_boundary_area_mismatch_is_a_warning(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-opening-result-", dir=repo) as tmp:
            case = self._write_case(tmp, predicted_area=0.5)
            result = cfd_export.verify_opening_boundary_areas(case, write=False)

        self.assertEqual(result["status"], "WARN")
        self.assertEqual(result["terminals"][0]["area_status"], "WARN")
        self.assertAlmostEqual(result["terminals"][0]["area_ratio"], 2.0)

