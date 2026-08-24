import json
import os
from pathlib import Path
import importlib.util
import sys
import tempfile
import types
import unittest

import cfd_occ
from geometry_v2 import migrate_geometry


def _geometry(zone_points, with_obstacles=False, with_terminals=False):
    elements = {
        "wall": [], "column": [], "slab": [], "zone": [], "opening": [],
        "pipe": [], "duct": [], "tray": [], "equipment": [],
    }
    elements["zone"].append({
        "kind": "polyline", "closed": True, "points": zone_points,
        "source_handle": "ZONE1", "confirmed": True,
        "semantic": {"kind": "space", "ceiling_height_mm": 2800.0},
    })
    if with_obstacles:
        elements["column"].append({
            "kind": "polyline", "closed": True,
            "points": [[500, 500], [900, 500], [900, 900], [500, 900]],
            "source_handle": "COL1",
        })
        elements["equipment"].append({
            "kind": "polyline", "closed": True,
            "points": [[2000, 1000], [2500, 1000], [2500, 1500], [2000, 1500]],
            "source_handle": "EQ1", "confirmed": True,
            "semantic": {"kind": "equipment", "role": "solid", "height_mm": 1000.0},
        })
    if with_terminals:
        elements["equipment"].extend([
            {
                "kind": "circle", "center": [1200, 2200], "radius": 200,
                "source_handle": "SA1", "block_name": "SUPPLY_DIFFUSER",
                "confirmed": True,
                "semantic": {
                    "kind": "air_terminal", "role": "supply", "airflow_cmh": 500,
                    "host_surface": "ceiling", "normal": [0, 0, -1],
                },
            },
            {
                "kind": "circle", "center": [3300, 2200], "radius": 200,
                "source_handle": "EA1", "block_name": "EXHAUST_DIFFUSER",
                "confirmed": True,
                "semantic": {
                    "kind": "air_terminal", "role": "exhaust", "airflow_cmh": 450,
                    "host_surface": "ceiling", "normal": [0, 0, 1],
                },
            },
        ])
    return migrate_geometry({
        "source": "occ-test.dxf", "units": "mm", "scale_applied": 1.0,
        "source_insunits": 4,
        "params": {"wall": {"height": 2800.0}, "column": {"height": 2800.0}},
        "floors": [{"z": 0.0, "label": "Ground"}],
        "elements": elements,
    })


class OccControllerTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[1]

    def test_unconfirmed_geometry_is_rejected_before_freecad(self):
        data = _geometry([[0, 0], [4000, 0], [4000, 3000], [0, 3000]])
        data["elements"]["zone"][0]["confirmed"] = False
        data["elements"]["zone"][0]["confirmation_state"] = "unconfirmed"
        with tempfile.TemporaryDirectory(prefix=".test-occ-controller-", dir=self.repo) as tmp:
            path = Path(tmp) / "geometry.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            result = cfd_occ.run_occ_job(path, Path(tmp) / "output", executable="missing.exe")
        self.assertFalse(result["ok"])
        self.assertIn("필수 확인", result["error"])

    def test_output_inspection_rejects_missing_artifacts(self):
        with tempfile.TemporaryDirectory(prefix=".test-occ-inspect-", dir=self.repo) as tmp:
            path = Path(tmp)
            (path / "surface_manifest.json").write_text(json.dumps({
                "contract": "surface_manifest.v1",
                "air_volume": {"valid": True, "solid_count": 1},
                "topology": {"watertight": True},
            }), encoding="utf-8")
            result = cfd_occ.inspect_occ_output(path)
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["missing"]), 3)

    def test_surface_manifest_schema_is_valid_json(self):
        schema = json.loads(
            (self.repo / "surface_manifest.v1.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(schema["properties"]["contract"]["const"], "surface_manifest.v1")
        self.assertEqual(schema["properties"]["topology"]["properties"]["open_edges"]["const"], 0)
        region = schema["properties"]["regions"]["items"]["properties"]
        self.assertEqual(region["evidence"]["type"], "string")
        self.assertEqual(region["source_type"]["type"], "string")
        self.assertEqual(region["source_id"]["type"], "string")
        self.assertEqual(region["source_label"]["type"], "string")
        self.assertEqual(region["source_ref"]["type"], "object")
        self.assertEqual(region["source_ref"]["minProperties"], 1)
        self.assertEqual(region["override_of_dxf"]["type"], "boolean")
        self.assertEqual(region["input_power_w"]["exclusiveMinimum"], 0)
        self.assertEqual(region["radiative_fraction"]["maximum"], 1)
        required = schema["properties"]["regions"]["items"]["allOf"][0]["then"]["required"]
        self.assertIn("source_ref", required)

    def test_occ_worker_requires_explicit_user_confirmed_heat_source_type(self):
        """The FreeCAD worker must not promote a typeless DXF heat source."""
        worker_path = self.repo / "cfd_occ_worker.py"
        spec = importlib.util.spec_from_file_location(
            "test_cfd_occ_worker_missing_source_type", worker_path
        )
        worker = importlib.util.module_from_spec(spec)
        old_modules = {
            name: sys.modules.get(name) for name in ("FreeCAD", "Part")
        }
        try:
            sys.modules["FreeCAD"] = types.ModuleType("FreeCAD")
            sys.modules["Part"] = types.ModuleType("Part")
            spec.loader.exec_module(worker)
        finally:
            for name, previous in old_modules.items():
                if previous is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = previous

        class Shape:
            Volume = 1.0
            Solids = [object()]

            @staticmethod
            def isNull():
                return False

            @staticmethod
            def isValid():
                return True

            def common(self, _other):
                return self

            def cut(self, _other):
                return self

        worker._solid_from_record = lambda _rec, _height: Shape()
        data = {
            "params": {},
            "elements": {"column": [], "equipment": [{
                "id": "EHP-MISSING-TYPE", "confirmed": True,
                "source_ref": {"handle": "EHP-01", "layer": "M-EQPM"},
                "semantic": {
                    "kind": "equipment", "role": "heat_source",
                    "height_mm": 1000, "input_power_w": 5000,
                    "convective_fraction": 0.8,
                    "evidence": "equipment_schedule:M03-001",
                },
            }]},
        }

        with self.assertRaisesRegex(worker.GeometryError, "source_type"):
            worker._build_air_volume(data, Shape(), 0, 2800, None)

    def test_occ_worker_rejects_override_that_lost_its_dxf_handle(self):
        """A stale surface cannot turn manual provenance into a DXF override."""
        worker_path = self.repo / "cfd_occ_worker.py"
        spec = importlib.util.spec_from_file_location(
            "test_cfd_occ_worker_override_reference", worker_path
        )
        worker = importlib.util.module_from_spec(spec)
        old_modules = {
            name: sys.modules.get(name) for name in ("FreeCAD", "Part")
        }
        try:
            sys.modules["FreeCAD"] = types.ModuleType("FreeCAD")
            sys.modules["Part"] = types.ModuleType("Part")
            spec.loader.exec_module(worker)
        finally:
            for name, previous in old_modules.items():
                if previous is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = previous

        class Shape:
            Volume = 1.0
            Solids = [object()]

            @staticmethod
            def isNull():
                return False

            @staticmethod
            def isValid():
                return True

            def common(self, _other):
                return self

            def cut(self, _other):
                return self

        worker._solid_from_record = lambda _rec, _height: Shape()
        data = {
            "params": {},
            "elements": {"column": [], "equipment": [{
                "id": "MANUAL-OVERRIDE", "confirmed": True,
                "source_ref": {
                    "layer": "USER_CONFIRMED", "entity_type": "UI_INPUT",
                    "source_id": "MANUAL-OVERRIDE",
                },
                "semantic": {
                    "kind": "equipment", "role": "heat_source",
                    "height_mm": 1000, "input_power_w": 5000,
                    "convective_fraction": 0.8,
                    "evidence": "equipment_schedule:M03-001",
                    "source_type": "user_confirmed",
                    "override_of_dxf": True,
                },
            }]},
        }

        with self.assertRaisesRegex(worker.GeometryError, "DXF override|source_ref"):
            worker._build_air_volume(data, Shape(), 0, 2800, None)


@unittest.skipUnless(
    os.environ.get("MEP_CFD_RUN_FREECAD_TESTS") == "1",
    "set MEP_CFD_RUN_FREECAD_TESTS=1 for the pinned FreeCAD target-runner",
)
class OccFreeCADIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[1]

    def _run(self, root, name, data):
        geometry_path = root / f"{name}.geometry.json"
        geometry_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        result = cfd_occ.run_occ_job(geometry_path, root / name, timeout=180)
        self.assertTrue(result.get("ok"), result)
        return result["manifest"]

    def test_rectangle_obstacles_terminals_and_deterministic_surface(self):
        data = _geometry(
            [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
            with_obstacles=True,
            with_terminals=True,
        )
        with tempfile.TemporaryDirectory(prefix=".test-occ-freecad-", dir=self.repo) as tmp:
            root = Path(tmp)
            first = self._run(root, "first", data)
            second = self._run(root, "second", data)
        self.assertEqual(first["surface_hash"], second["surface_hash"])
        self.assertAlmostEqual(first["air_volume"]["volume_m3"], 32.902, places=6)
        self.assertEqual(first["air_volume"]["solid_count"], 1)
        self.assertTrue(first["topology"]["watertight"])
        names = {region["name"] for region in first["regions"]}
        self.assertTrue(any(name.startswith("supply_") for name in names))
        self.assertTrue(any(name.startswith("exhaust_") for name in names))
        self.assertTrue(any(name.startswith("equipment_") for name in names))
        for region in first["regions"]:
            if region["role"] in ("supply", "exhaust"):
                self.assertLessEqual(region["area_error_ratio"], 0.02)
                self.assertGreater(region["airflow_cmh"], 0)
                self.assertEqual(len(region["design_normal"]), 3)

    def test_l_shaped_room_is_one_valid_solid(self):
        data = _geometry([
            [0, 0], [5000, 0], [5000, 2000], [3000, 2000],
            [3000, 4000], [0, 4000],
        ])
        with tempfile.TemporaryDirectory(prefix=".test-occ-l-room-", dir=self.repo) as tmp:
            manifest = self._run(Path(tmp), "l-room", data)
        self.assertAlmostEqual(manifest["air_volume"]["volume_m3"], 44.8, places=6)
        self.assertEqual(manifest["air_volume"]["solid_count"], 1)


if __name__ == "__main__":
    unittest.main()
