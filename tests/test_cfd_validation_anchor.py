import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import cfd_numerical_sensitivity_job as sensitivity_job
import cfd_gci
import cfd_result_gate
import cfd_temporal_sensitivity as temporal
import cfd_validation_anchor as validation_anchor
import field_acceptance
import field_pipeline_job
from geometry_v2 import migrate_geometry
from jsonschema import Draft202012Validator


class ValidationAnchorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.case = self.root / "fine-case"
        self.case.mkdir()
        self.selector_path = self.case / "occupied_selector.json"
        selector = sensitivity_job.normalize_occupied_volume_band({
            "contract": "occupied_volume_band.v1",
            "coordinate_source": "cell_center_m_agl",
            "z_min_agl_m": 0.1,
            "z_max_agl_m": 1.8,
        })
        self._write_json(self.selector_path, selector)
        self._build_case()

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _sha256(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    @staticmethod
    def _write_json(path, payload):
        Path(path).write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )

    def _build_case(self):
        geometry = self.case / "geometry.json"
        surface = self.case / "surface_manifest.json"
        mesh = self.case / "mesh_manifest.json"
        thermal = self.case / "thermal_input.json"
        run = self.case / "run_manifest.json"
        result = self.case / "result_manifest.json"
        source = self.case / "results" / "internal.vtu"
        source.parent.mkdir()
        self._write_json(geometry, {
            "contract": "geometry.v2", "schema_version": 2,
            "review": {"ready": True},
        })
        self._write_json(surface, {
            "contract": "surface_manifest.v1", "status": "PASS",
            "input": {
                "geometry_path": "geometry.json",
                "geometry_sha256": self._sha256(geometry),
            },
        })
        self._write_json(mesh, {
            "contract": "mesh_manifest.v1", "status": "PASS",
            "input": {
                "surface_manifest_path": "surface_manifest.json",
                "surface_manifest_sha256": self._sha256(surface),
            },
        })
        settings = {
            "reference_temperature_k": 293.15,
            "supply_temperature_k": 289.15,
        }
        numerics = {
            "profile": "design_limited_second_order_v1",
            "convection_order": 2,
        }
        self._write_json(thermal, {
            "contract": "thermal_input.v1",
            "mesh_manifest_sha256": self._sha256(mesh),
            "settings": settings,
            "numerics": numerics,
            "terminals": [{"name": "SA-1", "airflow_cmh": 444.0}],
            "heat_sources": [{
                "name": "people", "source_element_ids": ["people-zone"],
                "convective_power_w": 15500.0,
            }],
        })
        self._write_json(run, {
            "contract": "run_manifest.v1",
            "engine": "body_fitted_buoyant_urans",
            "status": "PASS",
            "design_ready": False,
            "effective_settings": settings,
            "effective_numerics": numerics,
            "solver": {
                "application": "buoyantBoussinesqPimpleFoam",
                "openfoam_version": "1912",
            },
            "input": {
                "thermal_input_sha256": self._sha256(thermal),
                "mesh_manifest_sha256": self._sha256(mesh),
            },
        })
        source.write_text("<VTKFile/>", encoding="ascii")
        self._write_json(result, {
            "contract": "result_manifest.v1",
            "engine": "body_fitted_openfoam_vtu",
            "source": {
                "path": "results/internal.vtu",
                "sha256": self._sha256(source),
            },
            "run_manifest_sha256": self._sha256(run),
            "mesh_manifest_sha256": self._sha256(mesh),
            "thermal_input_sha256": self._sha256(thermal),
        })

    def _create(self, *, output_name="validation_anchor.json", role="gci_fine"):
        return validation_anchor.create_validation_anchor(
            self.case,
            selector_path=self.selector_path,
            role=role,
            output_path=self.root / output_name,
        )

    def test_create_binds_current_raw_tree_and_validates_against_schema(self):
        created = self._create()
        anchor_path = Path(created["path"])
        payload = json.loads(anchor_path.read_text(encoding="utf-8"))
        schema = json.loads(
            (Path(__file__).resolve().parents[1]
             / "validation_anchor.v1.schema.json").read_text(encoding="utf-8")
        )

        self.assertEqual(created["anchor_id"], payload["anchor_id"])
        self.assertEqual(payload["status"], "BOUND_NOT_CITABLE")
        self.assertEqual(payload["role"], "gci_fine")
        self.assertEqual(
            [row["role"] for row in payload["artifacts"]],
            [
                "geometry", "surface_manifest", "mesh_manifest",
                "thermal_input", "run_manifest", "result_manifest",
                "result_source", "occupied_selector",
            ],
        )
        self.assertEqual(
            list(Draft202012Validator(schema).iter_errors(payload)), []
        )
        self.assertEqual(
            validation_anchor.validate_validation_anchor(
                anchor_path, expected_case=self.case,
            ),
            [],
        )

    def test_validator_rehashes_artifacts_instead_of_trusting_anchor_status(self):
        created = self._create()
        (self.case / "thermal_input.json").write_text(
            '{"changed":true}', encoding="utf-8"
        )

        issues = validation_anchor.validate_validation_anchor(
            created["path"], expected_case=self.case,
        )

        self.assertIn("ANCHOR_ARTIFACT_HASH_MISMATCH", {
            row["code"] for row in issues
        })

    def test_accepts_occ_manifest_hash_of_normalised_geometry_copy(self):
        geometry_path = self.case / "geometry.json"
        source = json.loads(geometry_path.read_text(encoding="utf-8"))
        normalised = migrate_geometry(
            source, source_path=source.get("source") or str(geometry_path.resolve()),
        )
        normalised["occ_source_path"] = str(geometry_path.resolve())
        normalised_bytes = (
            json.dumps(normalised, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        surface_path = self.case / "surface_manifest.json"
        surface = json.loads(surface_path.read_text(encoding="utf-8"))
        surface["input"]["geometry_sha256"] = hashlib.sha256(
            normalised_bytes
        ).hexdigest()
        self._write_json(surface_path, surface)
        mesh_path = self.case / "mesh_manifest.json"
        mesh = json.loads(mesh_path.read_text(encoding="utf-8"))
        mesh["input"]["surface_manifest_sha256"] = self._sha256(surface_path)
        self._write_json(mesh_path, mesh)
        thermal_path = self.case / "thermal_input.json"
        thermal = json.loads(thermal_path.read_text(encoding="utf-8"))
        thermal["mesh_manifest_sha256"] = self._sha256(mesh_path)
        self._write_json(thermal_path, thermal)
        run_path = self.case / "run_manifest.json"
        run = json.loads(run_path.read_text(encoding="utf-8"))
        run["input"]["mesh_manifest_sha256"] = self._sha256(mesh_path)
        run["input"]["thermal_input_sha256"] = self._sha256(thermal_path)
        self._write_json(run_path, run)
        result_path = self.case / "result_manifest.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["mesh_manifest_sha256"] = self._sha256(mesh_path)
        result["thermal_input_sha256"] = self._sha256(thermal_path)
        result["run_manifest_sha256"] = self._sha256(run_path)
        self._write_json(result_path, result)

        created = self._create()

        self.assertEqual(
            validation_anchor.validate_validation_anchor(created["path"]), []
        )

    def test_expected_case_mismatch_is_rejected(self):
        created = self._create()
        other = self.root / "other-case"
        other.mkdir()

        issues = validation_anchor.validate_validation_anchor(
            created["path"], expected_case=other,
        )

        self.assertIn("ANCHOR_CASE_MISMATCH", {row["code"] for row in issues})

    def test_existing_output_is_immutable_even_when_source_changes(self):
        created = self._create()
        (self.case / "geometry.json").write_text(
            '{"contract":"geometry.v2","changed":true}', encoding="utf-8"
        )

        with self.assertRaises(validation_anchor.ValidationAnchorError) as caught:
            self._create()

        self.assertEqual(caught.exception.code, "ANCHOR_OUTPUT_IMMUTABLE")
        self.assertEqual(
            json.loads(Path(created["path"]).read_text(encoding="utf-8"))[
                "anchor_id"
            ],
            created["anchor_id"],
        )

    def test_unknown_role_is_rejected(self):
        with self.assertRaises(validation_anchor.ValidationAnchorError) as caught:
            self._create(role="design_citable")

        self.assertEqual(caught.exception.code, "ANCHOR_ROLE_INVALID")

    def test_role_documents_share_anchor_identity_for_the_same_raw_case(self):
        identities = []
        for role in ("gci_fine", "temporal_fine", "field_authority"):
            identities.append(self._create(
                output_name=f"{role}.validation-anchor.json", role=role,
            )["anchor_id"])

        self.assertEqual(len(set(identities)), 1)

    def test_gci_binding_accepts_only_the_anchored_fine_case(self):
        created = self._create()
        manifest = {
            "contract": "grid_convergence.v3", "status": "PASS",
            "cases": [{"path": str(self.case.resolve())}],
        }

        bound = cfd_gci.bind_validation_anchor(
            manifest, created["path"], fine_case=self.case,
        )

        self.assertEqual(
            bound["validation_anchor"]["anchor_id"], created["anchor_id"]
        )
        other = self.root / "other-fine"
        other.mkdir()
        with self.assertRaises(cfd_gci.GCIInputError):
            cfd_gci.bind_validation_anchor(
                manifest, created["path"], fine_case=other,
            )

    def test_final_gate_resolves_external_anchor_from_gci_manifest(self):
        created = self._create()
        manifest = cfd_gci.bind_validation_anchor(
            {
                "contract": "grid_convergence.v3", "status": "PASS",
                "cases": [{"path": str(self.case.resolve())}],
            },
            created["path"], fine_case=self.case,
        )
        gci_path = self.root / "study" / "grid_convergence.json"
        gci_path.parent.mkdir()
        self._write_json(gci_path, manifest)

        reference, issues = cfd_result_gate._resolve_gci_validation_anchor(
            gci_path, self.case,
        )

        self.assertEqual(issues, [])
        self.assertEqual(reference["anchor_id"], created["anchor_id"])
        self.assertEqual(reference["path"], str(Path(created["path"]).resolve()))

    def test_temporal_study_pins_a_role_document_with_the_same_anchor_identity(self):
        created = self._create(
            output_name="temporal.validation-anchor.json", role="temporal_fine",
        )
        seed = self.root / "temporal-seed"
        (seed / "0").mkdir(parents=True)
        (seed / "system").mkdir()
        (seed / "constant" / "polyMesh").mkdir(parents=True)
        (seed / "0" / "U").write_text("uniform (0 0 0);\n", encoding="ascii")
        (seed / "system" / "controlDict").write_text(
            "adjustTimeStep no;\ndeltaT 0.02;\n", encoding="ascii"
        )

        manifest = temporal.create_temporal_study(
            seed, [0.04, 0.02, 0.01],
            anchor_fine_case=self.case,
            validation_anchor_path=created["path"],
        )

        self.assertEqual(
            manifest["validation_anchor"]["anchor_id"], created["anchor_id"]
        )
        self.assertTrue(temporal.validate_temporal_manifest(manifest)["valid"])

    def test_field_authority_rejects_a_different_solver_case(self):
        created = self._create(
            output_name="field.validation-anchor.json", role="field_authority",
        )
        manifest = {
            "validation_anchor": validation_anchor.anchor_reference(
                created["path"], expected_case=self.case,
                expected_role="field_authority",
            ),
            "authoritative_solver_case": str(self.case.resolve()),
            "validation_study_id": "gci-study-001",
            "authority_reason": "verified GCI fine case",
        }
        manifest["authoritative_case_sha256"] = manifest[
            "validation_anchor"
        ]["binding_sha256"]
        self.assertEqual(
            field_pipeline_job.validate_authoritative_case_binding(manifest), []
        )

        manifest["authoritative_solver_case"] = str(self.root / "other-case")
        issues = field_pipeline_job.validate_authoritative_case_binding(manifest)
        self.assertIn("FIELD_AUTHORITY_CASE_MISMATCH", {
            row["code"] for row in issues
        })

    def test_analysis_only_field_check_never_publishes_release_evidence(self):
        output = self.root / "release-evidence.json"
        with mock.patch.object(
            field_acceptance, "evaluate_field_case",
            return_value={
                "status": "PASS", "errors": [], "artifacts": {},
                "variation": {}, "gates": {}, "scope": "analysis_only",
            },
        ):
            result = field_acceptance.build_field_acceptance(
                self.case / "source.dxf", self.case / "geometry.json",
                self.case, self.case, self.case, self.root,
                actual_site_drawing=True, output_path=output,
                analysis_only=True,
            )

        self.assertTrue(result["ok"])
        self.assertTrue(result["analysis_only"])
        self.assertEqual(result["citation_status"], "NOT_EVALUATED")
        self.assertIsNone(result["manifest_path"])
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
