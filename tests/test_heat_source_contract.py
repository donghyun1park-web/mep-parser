"""Canonical confirmed-equipment heat-source contract tests.

These tests protect the conversion boundary before either legacy porous-zone
or body-fitted OpenFOAM adapters consume a user-confirmed equipment load.
"""
import unittest

from heat_source_contract import (
    HeatSourceContractError,
    assert_unique_positive_source_ids,
    normalize_confirmed_heat_source,
)


class ConfirmedHeatSourceContractTests(unittest.TestCase):
    def test_power_kw_derives_radiative_fraction_and_power_closure(self):
        """A 5 kW, 80% convective confirmed unit has a 1 kW held radiation part."""
        source = {
            "source_id": "DXF-EHP-01",
            "source_label": "EHP 실내기 1",
            "power_kw": 5.0,
            "convective_fraction": 0.8,
            "evidence": "equipment_schedule:M03-001",
            "source_type": "user_confirmed",
            "source_ref": {"handle": "DXF-EHP-01", "layer": "DVM_INDOOR"},
        }

        normalized = normalize_confirmed_heat_source(source)

        self.assertEqual(normalized["source_id"], "DXF-EHP-01")
        self.assertEqual(normalized["source_label"], "EHP 실내기 1")
        self.assertEqual(normalized["name"], "EHP 실내기 1")
        self.assertEqual(normalized["input_power_w"], 5000.0)
        self.assertEqual(normalized["convective_fraction"], 0.8)
        # This is persisted in manifests and compared across adapters, so a
        # simple user split must not be serialized as 0.19999999999999996.
        self.assertEqual(normalized["radiative_fraction"], 0.2)
        self.assertEqual(normalized["convective_power_w"], 4000.0)
        self.assertAlmostEqual(normalized["radiative_power_w"], 1000.0)
        self.assertEqual(normalized["excluded_radiative_power_w"], 1000.0)
        self.assertEqual(normalized["provenance"]["power_input"], "power_kw")
        self.assertEqual(normalized["provenance"]["radiative_fraction"], "derived")

    def test_explicit_watt_and_radiative_fraction_are_preserved(self):
        """An explicit radiation split must not be silently recomputed."""
        normalized = normalize_confirmed_heat_source({
            "source_id": "AHU-01",
            "source_label": "로비 공조기",
            "input_power_w": 5000,
            "convective_fraction": 0.7,
            "radiative_fraction": 0.3,
            "evidence": "equipment_schedule:M03-001",
            "source_type": "user_confirmed",
            "source_ref": {"handle": "AHU-01", "layer": "DVM_INDOOR"},
        })

        self.assertEqual(normalized["input_power_w"], 5000.0)
        self.assertEqual(normalized["convective_power_w"], 3500.0)
        self.assertEqual(normalized["radiative_fraction"], 0.3)
        self.assertEqual(normalized["radiative_power_w"], 1500.0)
        self.assertEqual(normalized["provenance"]["power_input"], "input_power_w")
        self.assertEqual(normalized["provenance"]["radiative_fraction"], "explicit")

    def test_legacy_kw_and_name_remain_compatible(self):
        """Legacy records can still be normalized without losing their label."""
        normalized = normalize_confirmed_heat_source({
            "source_id": "EQ-03",
            "name": "전기실 장비 3",
            "kw": 1.2,
            "convective_fraction": 1.0,
            "evidence": "field_confirmation:2026-08-12",
            "source_type": "fixture",
        })

        self.assertEqual(normalized["source_label"], "전기실 장비 3")
        self.assertEqual(normalized["name"], "전기실 장비 3")
        self.assertEqual(normalized["input_power_w"], 1200.0)
        self.assertEqual(normalized["radiative_power_w"], 0.0)
        self.assertEqual(normalized["provenance"]["power_input"], "kw")

    def test_confirmed_source_requires_traceable_identity_and_evidence(self):
        """Removing either traceability field must block a confirmed heat source."""
        base = {
            "source_id": "DXF-EHP-01",
            "source_label": "EHP 실내기 1",
            "power_kw": 5.0,
            "convective_fraction": 0.8,
            "evidence": "equipment_schedule:M03-001",
            "source_type": "user_confirmed",
            "source_ref": {"handle": "DXF-EHP-01", "layer": "DVM_INDOOR"},
        }
        missing_id = dict(base, source_id="  ")
        missing_evidence = dict(base, evidence="")

        with self.assertRaisesRegex(HeatSourceContractError, "source_id"):
            normalize_confirmed_heat_source(missing_id)
        with self.assertRaisesRegex(HeatSourceContractError, "evidence"):
            normalize_confirmed_heat_source(missing_evidence)

    def test_raw_dxf_detection_cannot_be_normalized_as_a_positive_heat_source(self):
        """Detection is a review candidate, never a directly usable heat load."""
        with self.assertRaisesRegex(HeatSourceContractError, "user_confirmed"):
            normalize_confirmed_heat_source({
                "source_id": "equipment_DXF_EHP_01",
                "source_label": "EHP-01",
                "power_kw": 5.0,
                "convective_fraction": 0.8,
                "evidence": "equipment_schedule:M03-001",
                "source_type": "dxf_detected",
            })

    def test_unreviewed_source_types_cannot_be_normalized_as_positive_heat(self):
        """Only a bounded, reviewed provenance state may introduce heat."""
        base = {
            "source_id": "equipment_DXF_EHP_01",
            "source_label": "EHP-01",
            "power_kw": 5.0,
            "convective_fraction": 0.8,
            "evidence": "equipment_schedule:M03-001",
            "source_ref": {"handle": "A1", "layer": "DVM_INDOOR"},
        }

        for source_type in ("dxf_detected", "unreviewed", "import_candidate"):
            with self.subTest(source_type=source_type):
                with self.assertRaisesRegex(HeatSourceContractError, "source_type"):
                    normalize_confirmed_heat_source(dict(base, source_type=source_type))

    def test_confirmed_source_requires_a_nonempty_source_reference(self):
        """A human heat confirmation must remain tied to the actual DXF item."""
        base = {
            "source_id": "DXF-EHP-01",
            "source_label": "EHP ?ㅻ궡湲?1",
            "power_kw": 5.0,
            "convective_fraction": 0.8,
            "evidence": "equipment_schedule:M03-001",
            "source_type": "user_confirmed",
        }

        for source_ref in (
            None, "", {}, {"handle": " "}, {"handles": []},
            {"handle": {"note": "not a CAD token"}}, {"handles": [{}]},
        ):
            with self.subTest(source_ref=source_ref):
                with self.assertRaisesRegex(HeatSourceContractError, "source_ref"):
                    normalize_confirmed_heat_source(dict(base, source_ref=source_ref))

    def test_confirmed_source_rejects_an_arbitrary_note_as_dxf_provenance(self):
        """A free-text note is not a CAD handle or an explicit manual source."""
        source = {
            "source_id": "DXF-EHP-NOTE-01",
            "source_label": "EHP note",
            "input_power_w": 1000,
            "convective_fraction": 1.0,
            "evidence": "equipment_schedule:M03-001",
            "source_type": "user_confirmed",
            "source_ref": {"note": "not a CAD identity"},
        }

        with self.assertRaisesRegex(HeatSourceContractError, "source_ref"):
            normalize_confirmed_heat_source(source)

    def test_confirmed_source_accepts_explicit_manual_input_provenance(self):
        """A genuinely manual source remains traceable without pretending to be DXF."""
        source = {
            "source_id": "manual-heat-01",
            "source_label": "현장 입력 장비",
            "input_power_w": 1000,
            "convective_fraction": 1.0,
            "evidence": "field_confirmation:2026-08-12",
            "source_type": "user_confirmed",
            "source_ref": {
                "layer": "USER_CONFIRMED",
                "entity_type": "UI_INPUT",
                "source_id": "manual-heat-01",
            },
        }

        normalized = normalize_confirmed_heat_source(source)

        self.assertEqual(
            normalized["provenance"]["source_reference_kind"], "manual_input"
        )

    def test_dxf_override_requires_a_real_cad_identity(self):
        """An override may change values, but cannot erase the original handle."""
        source = {
            "source_id": "DXF-EHP-OVERRIDE-01",
            "source_label": "EHP override",
            "input_power_w": 1000,
            "convective_fraction": 1.0,
            "evidence": "equipment_schedule:M03-001",
            "source_type": "user_confirmed",
            "source_ref": {
                "layer": "USER_CONFIRMED",
                "entity_type": "UI_INPUT",
                "source_id": "DXF-EHP-OVERRIDE-01",
            },
            "override_of_dxf": True,
        }

        with self.assertRaisesRegex(HeatSourceContractError, "source_ref"):
            normalize_confirmed_heat_source(source)

    def test_explicit_radiation_is_normalized_to_the_canonical_power_split(self):
        """A tolerance-sized input tail cannot leave fraction and W values apart."""
        normalized = normalize_confirmed_heat_source({
            "source_id": "EHP-02",
            "source_label": "EHP-02",
            "input_power_w": 5000,
            "convective_fraction": 0.8,
            "radiative_fraction": 0.2000005,
            "evidence": "equipment_schedule:M03-001",
            "source_type": "user_confirmed",
            "source_ref": {"handle": "A2", "layer": "DVM_INDOOR"},
        })

        self.assertEqual(normalized["radiative_fraction"], 0.2)
        self.assertAlmostEqual(
            normalized["radiative_power_w"],
            normalized["input_power_w"] * normalized["radiative_fraction"],
        )

    def test_positive_canonical_source_ids_must_be_unique(self):
        """The same DXF equipment source cannot inject its load twice."""
        source = normalize_confirmed_heat_source({
            "source_id": "EHP-UNIQUE-01",
            "source_label": "EHP-UNIQUE-01",
            "input_power_w": 1000,
            "convective_fraction": 1.0,
            "evidence": "equipment_schedule:M03-001",
            "source_type": "user_confirmed",
            "source_ref": {"handle": "U1", "layer": "DVM_INDOOR"},
        })

        with self.assertRaisesRegex(HeatSourceContractError, "duplicate source_id"):
            assert_unique_positive_source_ids([source, dict(source)])

    def test_positive_canonical_source_id_helper_returns_ordered_ids(self):
        """Adapters can retain the identity list after validating uniqueness."""
        sources = [
            normalize_confirmed_heat_source({
                "source_id": "EHP-A",
                "source_label": "EHP-A",
                "input_power_w": 1000,
                "convective_fraction": 1.0,
                "evidence": "equipment_schedule:M03-001",
                "source_type": "user_confirmed",
                "source_ref": {"handle": "A", "layer": "DVM_INDOOR"},
            }),
            normalize_confirmed_heat_source({
                "source_id": "EHP-B",
                "source_label": "EHP-B",
                "input_power_w": 2000,
                "convective_fraction": 1.0,
                "evidence": "equipment_schedule:M03-001",
                "source_type": "user_confirmed",
                "source_ref": {"handle": "B", "layer": "DVM_INDOOR"},
            }),
        ]

        self.assertEqual(assert_unique_positive_source_ids(sources), ("EHP-A", "EHP-B"))

    def test_override_of_dxf_must_be_a_boolean_and_is_preserved(self):
        """The record must state clearly when a human overrode the DXF read."""
        source = {
            "source_id": "DXF-EHP-OVERRIDE",
            "source_label": "EHP-override",
            "input_power_w": 1000,
            "convective_fraction": 1.0,
            "evidence": "equipment_schedule:M03-001",
            "source_type": "user_confirmed",
            "source_ref": {"handle": "OV1", "layer": "DVM_INDOOR"},
            "override_of_dxf": True,
        }

        normalized = normalize_confirmed_heat_source(source)
        self.assertIs(normalized["override_of_dxf"], True)
        self.assertIs(normalized["provenance"]["override_of_dxf"], True)
        with self.assertRaisesRegex(HeatSourceContractError, "override_of_dxf"):
            normalize_confirmed_heat_source(dict(source, override_of_dxf="true"))

    def test_null_dxf_override_marker_is_treated_as_absent(self):
        """Optional UI fields may serialize the no-marker state as null."""
        normalized = normalize_confirmed_heat_source({
            "source_id": "DXF-EHP-NO-OVERRIDE",
            "source_label": "EHP-no-override",
            "input_power_w": 1000,
            "convective_fraction": 1.0,
            "evidence": "equipment_schedule:M03-001",
            "source_type": "user_confirmed",
            "source_ref": {"handle": "NO1", "layer": "DVM_INDOOR"},
            "override_of_dxf": None,
        })

        self.assertNotIn("override_of_dxf", normalized)
        self.assertNotIn("override_of_dxf", normalized["provenance"])

    def test_rejects_nonphysical_power_or_fraction_sum(self):
        """A typo cannot create a negative load or an unclosed energy split."""
        base = {
            "source_id": "DXF-EHP-01",
            "source_label": "EHP 실내기 1",
            "power_kw": 5.0,
            "convective_fraction": 0.8,
            "evidence": "equipment_schedule:M03-001",
            "source_type": "user_confirmed",
            "source_ref": {"handle": "DXF-EHP-01", "layer": "DVM_INDOOR"},
        }

        with self.assertRaisesRegex(HeatSourceContractError, "input power"):
            normalize_confirmed_heat_source(dict(base, power_kw=0))
        with self.assertRaisesRegex(HeatSourceContractError, "sum to 1"):
            normalize_confirmed_heat_source(dict(
                base, convective_fraction=0.8, radiative_fraction=0.3
            ))


if __name__ == "__main__":
    unittest.main()
