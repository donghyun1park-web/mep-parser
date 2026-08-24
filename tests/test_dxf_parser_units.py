from pathlib import Path
import tempfile
import unittest

import ezdxf
import dxf_parser


class DxfUnitTests(unittest.TestCase):
    def test_common_insunits_are_normalized_to_mm(self):
        self.assertEqual(dxf_parser._insunits_to_mm_scale(1), (25.4, None))
        self.assertEqual(dxf_parser._insunits_to_mm_scale(4), (1.0, None))
        self.assertEqual(dxf_parser._insunits_to_mm_scale(5), (10.0, None))
        self.assertEqual(dxf_parser._insunits_to_mm_scale(6), (1000.0, None))

    def test_unitless_drawing_surfaces_the_mm_assumption(self):
        scale, warning = dxf_parser._insunits_to_mm_scale(0)
        self.assertEqual(scale, 1.0)
        self.assertIn("무단위", warning)
        self.assertIn("mm로 가정", warning)


class SgiDrawingRegressionTests(unittest.TestCase):
    def test_sgi_style_drawing_auto_corrects_bad_inch_header_and_extracts_terminals(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-sgi-dxf-", dir=repo) as tmp:
            path = Path(tmp) / "sgi-style.dxf"
            doc = ezdxf.new("R2010")
            doc.header["$INSUNITS"] = 1  # broken source header: coordinates are really mm
            for layer in ("A-ELE04", "HFB-101", "DVM_INDOOR"):
                doc.layers.add(layer)
            msp = doc.modelspace()
            # One 27.6 x 15.9 m lobby extent, intentionally not a closed zone.
            for start, end in (
                ((0, 0), (27600, 0)), ((27600, 0), (27600, 15900)),
                ((27600, 15900), (0, 15900)), ((0, 15900), (0, 0)),
            ):
                msp.add_line(start, end, dxfattribs={"layer": "A-ELE04"})
            xs = (100, 4900, 9700, 14500, 19300, 24100)
            ys = (2450, 4850, 7250, 9650, 12050)
            for x in xs:
                for y in ys:
                    msp.add_circle((x, y), 100, dxfattribs={"layer": "HFB-101"})
                    # Symbol detail must not become extra equipment records.
                    msp.add_line((x - 150, y), (x + 150, y),
                                 dxfattribs={"layer": "HFB-101"})
            for supply_x, return_x in ((2200, 3100), (11800, 12700), (21400, 22300)):
                msp.add_text("SA", dxfattribs={
                    "layer": "DVM_INDOOR", "insert": (supply_x, 8000),
                })
                msp.add_text("RA", dxfattribs={
                    "layer": "DVM_INDOOR", "insert": (return_x, 8000),
                })
            # This layer name contains DOOR but represents an indoor unit, not a door.
            msp.add_line((2000, 7900), (3300, 7900),
                         dxfattribs={"layer": "DVM_INDOOR"})
            doc.saveas(path)

            result = dxf_parser.parse(
                str(path),
                dxf_parser.load_layer_map(repo / "layer_map.csv"),
                dxf_parser.load_layer_map(repo / "block_map.csv"),
                unit_override="auto",
            )

        self.assertEqual(result["source_insunits"], 1)
        self.assertEqual(result["scale_applied"], 1.0)
        self.assertTrue(result["unit_detection"]["auto_corrected"])
        self.assertTrue(result["unit_review"]["required"])
        self.assertFalse(result["unit_review"]["resolved"])
        self.assertEqual(len(result["elements"]["opening"]), 0)
        terminals = [item for item in result["elements"]["equipment"]
                     if item["semantic"]["kind"] == "air_terminal"]
        self.assertEqual(len(terminals), 30)
        roles = [item["semantic"]["role"] for item in terminals]
        self.assertEqual(roles, ["unresolved"] * 30)
        suggestions = [item["semantic"]["suggested_role"] for item in terminals]
        self.assertEqual(suggestions.count("supply"), 15)
        self.assertEqual(suggestions.count("exhaust"), 15)
        self.assertTrue(all(not item["confirmed"] for item in terminals))
        self.assertTrue(all(item["semantic"]["terminal_type"] == "round"
                            for item in terminals))
        self.assertTrue(all(item["semantic"]["diameter_mm"] == 200.0
                            for item in terminals))
        indoor_units = [item for item in result["elements"]["equipment"]
                        if item["semantic"].get("equipment_type") ==
                        "ducted_ehp_indoor_unit"]
        self.assertEqual(len(indoor_units), 3)
        candidate = result["zone_candidates"][0]
        self.assertEqual(candidate["source_layer"], "A-ELE04")
        self.assertEqual(candidate["bbox_mm"], [0.0, 0.0, 27600.0, 15900.0])
        self.assertFalse(candidate["confirmed"])

    def test_door_token_rule_does_not_match_indoor_equipment_layer(self):
        category, _ = dxf_parser.classify(
            "DVM_INDOOR", dxf_parser.DEFAULT_LAYER_RULES,
        )
        self.assertEqual(category, "ignore")

    def test_header_unit_mode_can_preserve_a_real_inch_drawing(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-inch-dxf-", dir=repo) as tmp:
            path = Path(tmp) / "real-inch.dxf"
            doc = ezdxf.new("R2010")
            doc.header["$INSUNITS"] = 1
            doc.layers.add("WALL")
            doc.modelspace().add_line((0, 0), (100, 0), dxfattribs={"layer": "WALL"})
            doc.saveas(path)
            result = dxf_parser.parse(
                str(path), [(r"WALL", "wall", {})], [], unit_override="header",
            )
        self.assertEqual(result["scale_applied"], 25.4)
        self.assertFalse(result["unit_detection"]["auto_corrected"])
        self.assertFalse(result["unit_review"]["required"])


class ColumnGroupingTests(unittest.TestCase):
    @staticmethod
    def _rect(x0, y0, x1, y1):
        pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
        return [
            {"points": [list(pts[i]), list(pts[i + 1])], "z_base": 0.0}
            for i in range(4)
        ]

    def test_same_layer_columns_form_separate_components(self):
        records = self._rect(0, 0, 400, 600) + self._rect(2000, 0, 2400, 600)
        groups = dxf_parser._connected_column_components(records)
        self.assertEqual([len(g) for g in groups], [4, 4])


if __name__ == "__main__":
    unittest.main()
