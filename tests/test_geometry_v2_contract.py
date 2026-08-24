import copy
import json
from pathlib import Path
import tempfile
import unittest

import ezdxf

import dxf_parser
import geometry_v2


class GeometryV2ContractTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[1]

    @staticmethod
    def _legacy(elements=None):
        return {
            "source": "legacy.geometry.json",
            "units": "mm",
            "scale_applied": 1.0,
            "params": {"wall": {"height": 2800.0}},
            "floors": [{"z": 0.0, "label": "Ground"}],
            "elements": elements or {
                "wall": [{
                    "kind": "polyline", "closed": False,
                    "points": [[0, 0], [1000, 0]], "layer": "A-WALL",
                    "source_handle": "10",
                }],
                "zone": [{
                    "kind": "polyline", "closed": True,
                    "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
                    "layer": "A-ZONE", "source_handle": "20",
                }],
            },
            "custom_legacy_field": {"keep": True},
        }

    def test_migration_is_additive_and_does_not_mutate_input(self):
        source = self._legacy()
        untouched = copy.deepcopy(source)
        migrated = geometry_v2.migrate_geometry(source)

        self.assertEqual(source, untouched)
        self.assertEqual(migrated["schema_version"], 2)
        self.assertEqual(migrated["contract"], "geometry.v2")
        self.assertEqual(migrated["custom_legacy_field"], {"keep": True})
        self.assertEqual(migrated["elements"]["wall"][0]["points"], [[0, 0], [1000, 0]])
        self.assertEqual(geometry_v2.validate_geometry_v2(migrated), [])

    def test_source_handle_ids_do_not_depend_on_element_order(self):
        first = {
            "kind": "polyline", "closed": False, "points": [[0, 0], [1000, 0]],
            "layer": "WALL", "source_handle": "A1",
        }
        second = {
            "kind": "polyline", "closed": False, "points": [[0, 100], [1000, 100]],
            "layer": "WALL", "source_handle": "A2",
        }
        one = geometry_v2.migrate_geometry(self._legacy({"wall": [first, second]}))
        two = geometry_v2.migrate_geometry(self._legacy({"wall": [second, first]}))
        self.assertEqual(
            {item["source_handle"]: item["id"] for item in one["elements"]["wall"]},
            {item["source_handle"]: item["id"] for item in two["elements"]["wall"]},
        )

    def test_migration_keeps_a_string_handle_list_as_one_token(self):
        """A legacy string must not become one fake handle per character."""
        migrated = geometry_v2.migrate_geometry(self._legacy({
            "wall": [{
                "kind": "polyline", "closed": False,
                "points": [[0, 0], [1000, 0]],
                "source_ref": {"handles": "A1B2", "layer": "A-WALL"},
            }],
        }))

        self.assertEqual(migrated["elements"]["wall"][0]["source_ref"]["handles"], ["A1B2"])

    def test_migration_drops_structured_handle_tokens_before_heat_review(self):
        """JSON objects cannot impersonate a CAD handle in a reviewed load."""
        data = geometry_v2.migrate_geometry(self._legacy({
            "zone": [{
                "kind": "polyline", "closed": True, "confirmed": True,
                "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
            }],
            "equipment": [{
                "kind": "polyline", "closed": True, "confirmed": True,
                "points": [[500, 500], [1000, 500], [1000, 1000], [500, 1000]],
                "source_ref": {"handles": [{"note": "forged"}]},
                "semantic": {
                    "kind": "equipment", "role": "heat_source",
                    "height_mm": 1000, "input_power_w": 5000,
                    "convective_fraction": 0.8,
                    "evidence": "equipment_schedule:M03-001",
                    "source_type": "user_confirmed",
                },
            }],
        }))

        codes = {item["code"] for item in data["review"]["items"]}
        self.assertIn("EQUIPMENT_HEAT_CONTRACT_INVALID", codes)
        self.assertEqual(data["elements"]["equipment"][0]["source_ref"]["handles"], [])

    def test_duplicate_legacy_geometry_gets_unique_traceable_ids(self):
        rec = {"kind": "circle", "center": [100, 200], "radius": 50, "layer": "EQ"}
        migrated = geometry_v2.migrate_geometry(
            self._legacy({"equipment": [copy.deepcopy(rec), copy.deepcopy(rec)]})
        )
        records = migrated["elements"]["equipment"]
        self.assertEqual(len({item["id"] for item in records}), 2)
        self.assertEqual(records[0]["id_stability"], "geometry_derived")
        self.assertEqual(records[1]["id_stability"], "geometry_derived_duplicate")

    def test_body_fitted_review_requires_confirmed_space(self):
        migrated = geometry_v2.migrate_geometry(self._legacy())
        codes = {item["code"] for item in migrated["review"]["items"]}
        self.assertIn("SPACE_CONFIRMATION_REQUIRED", codes)
        self.assertTrue(migrated["review"]["blocking"])
        self.assertTrue(migrated["review"]["screening_voxel_allowed"])

    def test_air_terminal_requires_cmh_host_direction_and_confirmation(self):
        terminal = {
            "kind": "circle", "center": [1000, 1000], "radius": 300,
            "block_name": "SUPPLY_DIFFUSER", "source_handle": "30",
        }
        data = geometry_v2.migrate_geometry(self._legacy({
            "zone": [{
                "kind": "polyline", "closed": True, "confirmed": True,
                "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
                "source_handle": "20",
            }],
            "equipment": [terminal],
        }))
        rec = data["elements"]["equipment"][0]
        self.assertEqual(rec["semantic"]["kind"], "air_terminal")
        self.assertEqual(rec["semantic"]["role"], "supply")
        codes = {item["code"] for item in data["review"]["items"]}
        self.assertTrue({
            "TERMINAL_AIRFLOW_REQUIRED", "TERMINAL_HOST_REQUIRED",
            "TERMINAL_NORMAL_REQUIRED", "TERMINAL_CONFIRMATION_REQUIRED",
        }.issubset(codes))
        self.assertEqual(data["review"]["blocker_count"], len(data["review"]["items"]))
        self.assertTrue(all(item["severity"] == "error" for item in data["review"]["items"]))

    def test_complete_space_and_terminal_semantics_are_body_fitted_ready(self):
        data = geometry_v2.migrate_geometry(self._legacy({
            "zone": [{
                "kind": "polyline", "closed": True, "confirmed": True,
                "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
                "source_handle": "20",
            }],
            "equipment": [{
                "kind": "circle", "center": [1000, 1000], "radius": 300,
                "block_name": "SUPPLY_DIFFUSER", "source_handle": "30",
                "confirmed": True,
                "semantic": {
                    "role": "supply", "airflow_cmh": 500,
                    "host_surface": "ceiling", "normal": [0, 0, -1],
                },
            }],
        }))
        self.assertTrue(data["review"]["ready"], data["review"]["items"])
        self.assertFalse(data["review"]["blocking"])

    def test_heat_source_requires_explicit_convective_fraction(self):
        base = self._legacy({
            "zone": [{
                "kind": "polyline", "closed": True, "confirmed": True,
                "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
                "source_handle": "20",
            }],
            "equipment": [{
                "kind": "polyline", "closed": True, "confirmed": True,
                "points": [[500, 500], [1000, 500], [1000, 1000], [500, 1000]],
                "source_handle": "40",
                "semantic": {
                    "kind": "equipment", "role": "heat_source",
                    "height_mm": 1000, "power_kw": 5,
                },
            }],
        })
        missing = geometry_v2.migrate_geometry(base)
        self.assertIn(
            "EQUIPMENT_CONVECTIVE_FRACTION_REQUIRED",
            {item["code"] for item in missing["review"]["items"]},
        )
        base["elements"]["equipment"][0]["semantic"]["convective_fraction"] = 0.8
        base["elements"]["equipment"][0]["semantic"]["evidence"] = "equipment_schedule:M03-001"
        accepted = geometry_v2.migrate_geometry(base)
        self.assertNotIn(
            "EQUIPMENT_CONVECTIVE_FRACTION_REQUIRED",
            {item["code"] for item in accepted["review"]["items"]},
        )

    def test_heat_source_requires_height_and_evidence_for_traceable_thermal_input(self):
        base = self._legacy({
            "zone": [{
                "kind": "polyline", "closed": True, "confirmed": True,
                "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
            }],
            "equipment": [{
                "kind": "polyline", "closed": True, "confirmed": True,
                "points": [[500, 500], [1000, 500], [1000, 1000], [500, 1000]],
                "semantic": {
                    "kind": "equipment", "role": "heat_source",
                    "power_kw": 5, "convective_fraction": 0.8,
                },
            }],
        })
        missing = geometry_v2.migrate_geometry(base)
        codes = {item["code"] for item in missing["review"]["items"]}
        self.assertIn("EQUIPMENT_HEIGHT_REQUIRED", codes)
        self.assertIn("EQUIPMENT_HEAT_EVIDENCE_REQUIRED", codes)

        semantic = base["elements"]["equipment"][0]["semantic"]
        semantic.update({
            "height_mm": 1000,
            "evidence": "equipment_schedule:M03-001",
        })
        accepted = geometry_v2.migrate_geometry(base)
        accepted_codes = {item["code"] for item in accepted["review"]["items"]}
        self.assertNotIn("EQUIPMENT_HEIGHT_REQUIRED", accepted_codes)
        self.assertNotIn("EQUIPMENT_HEAT_EVIDENCE_REQUIRED", accepted_codes)

    def test_heat_source_accepts_canonical_input_power_w_without_power_kw(self):
        """The v2 W-based contract must not require a legacy kW alias."""
        data = geometry_v2.migrate_geometry(self._legacy({
            "zone": [{
                "kind": "polyline", "closed": True, "confirmed": True,
                "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
            }],
            "equipment": [{
                "kind": "polyline", "closed": True, "confirmed": True,
                "points": [[500, 500], [1000, 500], [1000, 1000], [500, 1000]],
                "source_handle": "W-ONLY-EHP-01",
                "semantic": {
                    "kind": "equipment", "role": "heat_source",
                    "height_mm": 1000,
                    "input_power_w": 5000,
                    "convective_fraction": 0.8,
                    "evidence": "equipment_schedule:M03-001",
                    "source_type": "user_confirmed",
                },
            }],
        }))

        codes = {item["code"] for item in data["review"]["items"]}

        self.assertNotIn("EQUIPMENT_POWER_REQUIRED", codes)
        semantic = data["elements"]["equipment"][0]["semantic"]
        self.assertEqual(semantic["input_power_w"], 5000.0)
        self.assertEqual(semantic["convective_power_w"], 4000.0)
        self.assertEqual(semantic["radiative_power_w"], 1000.0)

    def test_heat_source_rejects_an_explicit_heat_split_that_does_not_close(self):
        base = self._legacy({
            "zone": [{
                "kind": "polyline", "closed": True, "confirmed": True,
                "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
            }],
            "equipment": [{
                "kind": "polyline", "closed": True, "confirmed": True,
                "points": [[500, 500], [1000, 500], [1000, 1000], [500, 1000]],
                "source_handle": "40",
                "semantic": {
                    "kind": "equipment", "role": "heat_source",
                    "height_mm": 1000, "power_kw": 5,
                    "convective_fraction": 0.8, "radiative_fraction": 0.3,
                    "evidence": "equipment_schedule:M03-001",
                    "source_type": "user_confirmed",
                },
            }],
        })

        reviewed = geometry_v2.migrate_geometry(base)

        self.assertIn(
            "EQUIPMENT_HEAT_FRACTION_SUM_REQUIRED",
            {item["code"] for item in reviewed["review"]["items"]},
        )

    def test_raw_dxf_detected_heat_source_is_blocked_before_body_fitted_build(self):
        """A detected CAD block requires review even when its kW is populated."""
        data = geometry_v2.migrate_geometry(self._legacy({
            "zone": [{
                "kind": "polyline", "closed": True, "confirmed": True,
                "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
            }],
            "equipment": [{
                "kind": "polyline", "closed": True, "confirmed": True,
                "points": [[500, 500], [1000, 500], [1000, 1000], [500, 1000]],
                "source_handle": "DXF-EHP-01",
                "semantic": {
                    "kind": "equipment", "role": "heat_source",
                    "height_mm": 1000, "power_kw": 5,
                    "convective_fraction": 0.8,
                    "evidence": "equipment_schedule:M03-001",
                    "source_type": "dxf_detected",
                },
            }],
        }))

        self.assertIn(
            "EQUIPMENT_HEAT_SOURCE_CONFIRMATION_REQUIRED",
            {item["code"] for item in data["review"]["items"]},
        )

    def test_confirmed_heat_source_requires_explicit_user_confirmed_type(self):
        """A checked box must not silently promote a heat load's provenance."""
        data = geometry_v2.migrate_geometry(self._legacy({
            "zone": [{
                "kind": "polyline", "closed": True, "confirmed": True,
                "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
                "source_handle": "20",
            }],
            "equipment": [{
                "kind": "polyline", "closed": True, "confirmed": True,
                "points": [[500, 500], [1000, 500], [1000, 1000], [500, 1000]],
                "source_handle": "DXF-EHP-MISSING-TYPE",
                "semantic": {
                    "kind": "equipment", "role": "heat_source",
                    "height_mm": 1000, "power_kw": 5,
                    "convective_fraction": 0.8,
                    "evidence": "equipment_schedule:M03-001",
                },
            }],
        }))

        self.assertIn(
            "EQUIPMENT_HEAT_SOURCE_CONFIRMATION_REQUIRED",
            {item["code"] for item in data["review"]["items"]},
        )
        self.assertNotIn(
            "source_type", data["elements"]["equipment"][0]["semantic"]
        )

    def test_body_fitted_review_rejects_duplicate_confirmed_heat_source_id(self):
        source = {
            "kind": "polyline", "closed": True, "confirmed": True,
            "points": [[500, 500], [1000, 500], [1000, 1000], [500, 1000]],
            "source_handle": "EHP-A1",
            "semantic": {
                "kind": "equipment", "role": "heat_source",
                "height_mm": 1000, "power_kw": 5,
                "convective_fraction": 0.8,
                "evidence": "equipment_schedule:M03-001",
                "source_type": "user_confirmed",
            },
        }
        data = geometry_v2.migrate_geometry(self._legacy({
            "zone": [{
                "kind": "polyline", "closed": True, "confirmed": True,
                "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
            }],
            "equipment": [copy.deepcopy(source), copy.deepcopy(source)],
        }))
        # A hand-edited migrated artifact can still duplicate an identity,
        # even though initial migration intentionally de-duplicates IDs.
        data["elements"]["equipment"][1]["id"] = data["elements"]["equipment"][0]["id"]
        review = geometry_v2.build_review(data)

        self.assertIn(
            "EQUIPMENT_HEAT_SOURCE_ID_DUPLICATE",
            {item["code"] for item in review["items"]},
        )

    def test_every_element_has_v2_identity_and_review_fields(self):
        migrated = geometry_v2.migrate_geometry(self._legacy())
        for category, records in migrated["elements"].items():
            for rec in records:
                self.assertEqual(rec["category"], category)
                self.assertTrue(rec["id"])
                self.assertIsInstance(rec["source_ref"], dict)
                self.assertIsInstance(rec["confirmed"], bool)
                self.assertIn(rec["confirmation_state"], ("confirmed", "unconfirmed"))
                self.assertTrue(rec["level_id"])

    def test_mm_and_inch_drawings_normalize_to_the_same_geometry_and_id(self):
        with tempfile.TemporaryDirectory(prefix=".test-geometry-v2-", dir=self.repo) as tmp:
            paths = []
            for units, length, name in ((4, 2540.0, "mm.dxf"), (1, 100.0, "inch.dxf")):
                doc = ezdxf.new("R2010")
                doc.header["$INSUNITS"] = units
                doc.layers.add("WALL")
                doc.modelspace().add_line((0, 0), (length, 0), dxfattribs={"layer": "WALL"})
                path = Path(tmp) / name
                doc.saveas(path)
                paths.append(path)
            parsed = [dxf_parser.parse(str(path), [(r"WALL", "wall", {})], [])
                      for path in paths]
        one, two = (item["elements"]["wall"][0] for item in parsed)
        self.assertAlmostEqual(one["points"][1][0], 2540.0, places=6)
        self.assertAlmostEqual(two["points"][1][0], 2540.0, places=6)
        self.assertEqual(one["id"], two["id"])
        self.assertEqual(parsed[0]["source_units"]["millimetres_per_source_unit"], 1.0)
        self.assertEqual(parsed[1]["source_units"]["millimetres_per_source_unit"], 25.4)

    def test_wall_join_preserves_all_source_handles(self):
        joined = dxf_parser.join_connected_lines([
            {"kind": "polyline", "closed": False, "points": [[0, 0], [100, 0]],
             "layer": "WALL", "source_handle": "A", "source_handles": ["A"]},
            {"kind": "polyline", "closed": False, "points": [[100, 0], [200, 0]],
             "layer": "WALL", "source_handle": "B", "source_handles": ["B"]},
        ])
        self.assertEqual(len(joined), 1)
        self.assertEqual(joined[0]["source_handles"], ["A", "B"])

    def test_schema_document_is_valid_json_and_semantic_diff_uses_ids(self):
        schema = json.loads((self.repo / "geometry.v2.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["schema_version"]["const"], 2)
        heat_semantic = schema["$defs"]["element"]["properties"]["semantic"]
        self.assertEqual(
            heat_semantic["properties"]["input_power_w"]["exclusiveMinimum"], 0
        )
        self.assertEqual(
            heat_semantic["properties"]["radiative_fraction"]["maximum"], 1
        )
        before = geometry_v2.migrate_geometry(self._legacy())
        after = copy.deepcopy(before)
        after["elements"]["zone"][0]["confirmed"] = True
        after["elements"]["zone"][0]["confirmation_state"] = "confirmed"
        diff = geometry_v2.semantic_diff(before, after)
        self.assertEqual(diff["changed"][0]["id"], before["elements"]["zone"][0]["id"])


if __name__ == "__main__":
    unittest.main()
