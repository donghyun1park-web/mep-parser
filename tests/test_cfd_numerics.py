import copy
import json
from pathlib import Path
import unittest

import cfd_numerics
import cfd_physics


class ThermalNumericsContractTests(unittest.TestCase):
    @staticmethod
    def _design_fv_schemes():
        return """divSchemes
{
    default none;
    div(phi,U) bounded Gauss linearUpwind grad(U);
    div(phi,T) bounded Gauss limitedLinear 1;
    div(phi,k) bounded Gauss limitedLinear 1;
    div(phi,omega) bounded Gauss limitedLinear 1;
}
laplacianSchemes { default Gauss linear limited 0.5; }
snGradSchemes { default limited 0.5; }
"""

    @staticmethod
    def _design_fv_solution():
        return """PIMPLE
{
    nCorrectors 2;
    nNonOrthogonalCorrectors 2;
}
"""

    def _candidate(self):
        return cfd_numerics.thermal_numerics_contract(
            {"mesh": {"max_non_orthogonality": 54}},
            {"thermal_numerics_profile": "design_limited_second_order_v1"},
        )

    def _valid_sensitivity(self):
        residuals = {
            field: {
                "final": 1e-6,
                "tail_maximum": 1e-6,
                "tail_samples": cfd_numerics.DEFAULT_RESIDUAL_TAIL_SAMPLES,
                "limit": limit,
            }
            for field, limit in cfd_numerics.THERMAL_RESIDUAL_LIMITS.items()
        }
        solver_evidence = {
            "ended": True,
            "fatal_error": False,
            "peak_courant": 0.7,
            "courant_limit": 1.0,
            "residuals": residuals,
            "continuity": {"global": 1e-7, "limit": 1e-6},
            "phi_balance": {
                "available": True,
                "imbalance_ratio": 0.0005,
                "limit": 0.001,
            },
            "energy_closure_basis": (
                "solver_positive_phi_and_owner_cell_temperature"
            ),
        }
        return {
            "contract": "numerical_sensitivity.v1",
            "status": "PASS",
            "provenance": {
                "explicit_job": True,
                "source": "cfd_numerical_sensitivity_job",
                "job_id": "sensitivity-job-001",
            },
            "baseline": {
                "profile": "stabilized_first_order_v1",
                "run_hash": "a" * 64,
                "mesh_hash": "c" * 64,
                "physical_input_hash": "d" * 64,
                "solver_evidence": copy.deepcopy(solver_evidence),
            },
            "variant": {
                "profile": "design_limited_second_order_v1",
                "run_hash": "b" * 64,
                "mesh_hash": "c" * 64,
                "physical_input_hash": "d" * 64,
                "solver_evidence": copy.deepcopy(solver_evidence),
            },
            "allowed_variation": {
                "parameter": "thermal_numerics_profile",
                "baseline": "stabilized_first_order_v1",
                "variant": "design_limited_second_order_v1",
                "all_other_inputs_equal": True,
            },
            "qoi_comparisons": [
                {
                    "name": "occupied_zone_mean_temperature_k",
                    "baseline": 294.10,
                    "variant": 294.20,
                    "absolute_difference": 0.10,
                    "limit": 0.50,
                    "passed": True,
                },
                {
                    "name": "occupied_zone_mean_speed_m_s",
                    "baseline": 0.20,
                    "variant": 0.21,
                    "absolute_difference": 0.01,
                    "limit": 0.05,
                    "passed": True,
                },
                {
                    "name": "exhaust_temperature_rise_k",
                    "baseline": 5.00,
                    "variant": 5.10,
                    "absolute_difference": 0.10,
                    "limit": 0.50,
                    "passed": True,
                },
            ],
        }

    def _candidate_solver(self):
        residuals = {
            field: {"final": 1e-6}
            for field in cfd_numerics.THERMAL_RESIDUAL_LIMITS
        }
        return {
            "thermal_residuals": {
                field: dict(row) for field, row in residuals.items()
            },
            "thermal_residual_history": {
                field: [
                    {"initial": 1e-4, "final": row["final"]}
                    for _ in range(5)
                ]
                for field, row in residuals.items()
            },
            "continuity": {"global": 1e-7},
            "courant": {"maximum": 0.7, "peak_maximum": 0.7},
        }

    def _evaluate_candidate(self, sensitivity, solver=None):
        solver = solver if solver is not None else self._candidate_solver()
        thermal = {
            "minimum_k": 292.5,
            "maximum_k": 294.0,
            "energy_closure_basis": (
                "solver_positive_phi_and_owner_cell_temperature"
            ),
        }
        return cfd_numerics.evaluate_thermal_numerics(
            self._candidate(),
            solver,
            thermal,
            {"available": True, "imbalance_ratio": 0.0005},
            {
                "reference_temperature_k": 293.15,
                "thermal_expansion_coefficient_1_k": 0.00341,
            },
            sensitivity=sensitivity,
            y_plus={
                "available": True,
                "method": "openfoam_yPlus_field",
                "wall_treatment_acceptable_area_ratio": 1.0,
            },
        )

    def test_non_orthogonal_correction_tiers_are_deterministic(self):
        self.assertEqual(cfd_numerics.required_non_orthogonal_correctors(20), 0)
        self.assertEqual(cfd_numerics.required_non_orthogonal_correctors(20.01), 1)
        self.assertEqual(cfd_numerics.required_non_orthogonal_correctors(45), 1)
        self.assertEqual(cfd_numerics.required_non_orthogonal_correctors(45.01), 2)
        self.assertEqual(cfd_numerics.required_non_orthogonal_correctors(65), 2)
        self.assertEqual(cfd_numerics.required_non_orthogonal_correctors(66), 2)

    def test_design_profile_is_semantically_bound_to_limited_second_order_files(self):
        result = cfd_numerics.validate_effective_openfoam_numerics(
            self._candidate(),
            self._design_fv_schemes(),
            self._design_fv_solution(),
        )

        self.assertTrue(result["valid"], result)
        self.assertEqual(result["issues"], [])

    def test_design_profile_rejects_upwind_even_when_its_file_hash_would_match(self):
        wrong_schemes = self._design_fv_schemes().replace(
            "div(phi,U) bounded Gauss linearUpwind grad(U);",
            "div(phi,U) bounded Gauss upwind;",
        )

        result = cfd_numerics.validate_effective_openfoam_numerics(
            self._candidate(), wrong_schemes, self._design_fv_solution()
        )

        self.assertFalse(result["valid"])
        self.assertIn("DIV_PHI_U_NOT_LIMITED_SECOND_ORDER", result["issues"])

    def test_design_profile_requires_limited_nonorthogonal_corrections(self):
        wrong_schemes = (self._design_fv_schemes()
                         .replace("Gauss linear limited 0.5", "Gauss linear uncorrected")
                         .replace("default limited 0.5", "default uncorrected"))
        wrong_solution = self._design_fv_solution().replace(
            "nNonOrthogonalCorrectors 2;", "nNonOrthogonalCorrectors 1;"
        )

        result = cfd_numerics.validate_effective_openfoam_numerics(
            self._candidate(), wrong_schemes, wrong_solution
        )

        self.assertFalse(result["valid"])
        self.assertIn("LAPLACIAN_NOT_LIMITED", result["issues"])
        self.assertIn("SN_GRAD_NOT_LIMITED", result["issues"])
        self.assertIn("PIMPLE_NONORTH_BELOW_REQUIRED", result["issues"])

    def test_generated_design_profile_matches_its_semantic_contract(self):
        settings = dict(cfd_physics.DEFAULT_SETTINGS)
        settings["thermal_numerics_profile"] = cfd_numerics.DESIGN_LIMITED_SECOND_ORDER
        numerics = cfd_numerics.thermal_numerics_contract(
            {"mesh": {"max_non_orthogonality": 54}}, settings
        )

        result = cfd_numerics.validate_effective_openfoam_numerics(
            numerics,
            cfd_physics._thermal_fv_schemes(numerics),
            cfd_physics._thermal_fv_solution(settings, numerics),
        )

        self.assertTrue(result["valid"], result)

    def test_stabilized_first_order_is_screening_only(self):
        contract = cfd_numerics.thermal_numerics_contract(
            {"mesh": {"max_non_orthogonality": 54}}, {}
        )

        self.assertEqual(contract["profile"], "stabilized_first_order_v1")
        self.assertEqual(contract["convection_order"], 1)
        self.assertEqual(contract["required_non_orthogonal_correctors"], 2)
        self.assertEqual(contract["status"], "SCREENING_ONLY")
        self.assertFalse(contract["design_eligible"])

    def test_second_order_profile_requires_acceptable_mesh(self):
        candidate = self._candidate()
        self.assertEqual(candidate["convection_order"], 2)
        self.assertEqual(candidate["status"], "CANDIDATE")
        self.assertTrue(candidate["design_eligible"])
        self.assertEqual(candidate["required_non_orthogonal_correctors"], 2)

        high_nonorth = cfd_numerics.thermal_numerics_contract(
            {"mesh": {"max_non_orthogonality": 66}},
            {"thermal_numerics_profile": "design_limited_second_order_v1"},
        )
        self.assertEqual(high_nonorth["status"], "SCREENING_ONLY")
        self.assertFalse(high_nonorth["design_eligible"])
        self.assertIn("MESH_NONORTH_HIGH", high_nonorth["blockers"])

    def test_design_candidate_needs_residual_phi_and_sensitivity_evidence(self):
        solver = self._candidate_solver()
        thermal = {
            "minimum_k": 292.5,
            "maximum_k": 294.0,
            "energy_closure_basis": "solver_positive_phi_and_owner_cell_temperature",
        }
        flux_balance = {"available": True, "imbalance_ratio": 0.0005}
        settings = {
            "reference_temperature_k": 293.15,
            "thermal_expansion_coefficient_1_k": 0.00341,
        }

        pending = cfd_numerics.evaluate_thermal_numerics(
            self._candidate(), solver, thermal, flux_balance, settings
        )
        self.assertEqual(pending["status"], "NOT_EVALUATED")
        self.assertIn("NUMERICAL_SENSITIVITY_PENDING", pending["blockers"])

        forged = cfd_numerics.evaluate_thermal_numerics(
            self._candidate(), solver, thermal, flux_balance, settings,
            sensitivity={"contract": "numerical_sensitivity.v1", "status": "PASS"},
            y_plus={
                "available": True,
                "method": "openfoam_yPlus_field",
                "wall_treatment_acceptable_area_ratio": 1.0,
            },
        )
        self.assertEqual(forged["status"], "NOT_EVALUATED")
        self.assertIn(
            "NUMERICAL_SENSITIVITY_PROVENANCE_MISSING", forged["blockers"]
        )

        structurally_valid = cfd_numerics.validate_numerical_sensitivity(
            self._valid_sensitivity()
        )
        self.assertTrue(structurally_valid["valid"])

        unverified = cfd_numerics.evaluate_thermal_numerics(
            self._candidate(), solver, thermal, flux_balance, settings,
            sensitivity=self._valid_sensitivity(),
            y_plus={
                "available": True,
                "method": "openfoam_yPlus_field",
                "wall_treatment_acceptable_area_ratio": 1.0,
            },
        )
        self.assertEqual(unverified["status"], "NOT_EVALUATED")
        self.assertFalse(unverified["design_ready"])
        self.assertIn(
            "NUMERICAL_SENSITIVITY_ARTIFACT_UNVERIFIED",
            unverified["blockers"],
        )

    def test_design_candidate_requires_wall_treatment_evidence(self):
        """A second-order candidate must not bypass the wall-function check."""
        solver = self._candidate_solver()
        thermal = {
            "minimum_k": 292.5,
            "maximum_k": 294.0,
            "energy_closure_basis": "solver_positive_phi_and_owner_cell_temperature",
        }
        flux_balance = {"available": True, "imbalance_ratio": 0.0005}
        settings = {
            "reference_temperature_k": 293.15,
            "thermal_expansion_coefficient_1_k": 0.00341,
            "minimum_wall_treatment_area_ratio": 0.80,
        }
        sensitivity = self._valid_sensitivity()

        missing = cfd_numerics.evaluate_thermal_numerics(
            self._candidate(), solver, thermal, flux_balance, settings,
            sensitivity=sensitivity, y_plus={"available": False},
        )
        self.assertEqual(missing["status"], "NOT_EVALUATED")
        self.assertIn("YPLUS_EVIDENCE_MISSING", missing["blockers"])

        wall_resolved = cfd_numerics.evaluate_thermal_numerics(
            self._candidate(), solver, thermal, flux_balance, settings,
            sensitivity=sensitivity,
            y_plus={
                "available": True,
                "method": "openfoam_yPlus_field",
                "wall_treatment_acceptable_area_ratio": 1.0,
            },
        )
        self.assertEqual(wall_resolved["status"], "NOT_EVALUATED")
        self.assertNotIn("YPLUS_DIRECT_FIELD_REQUIRED", wall_resolved["blockers"])
        self.assertIn(
            "NUMERICAL_SENSITIVITY_ARTIFACT_UNVERIFIED",
            wall_resolved["blockers"],
        )

        inferred_from_nut = cfd_numerics.evaluate_thermal_numerics(
            self._candidate(), solver, thermal, flux_balance, settings,
            sensitivity=sensitivity,
            y_plus={
                "available": True,
                "method": "nutkWallFunction_log_law_inverse",
                "wall_treatment_acceptable_area_ratio": 1.0,
            },
        )
        self.assertEqual(inferred_from_nut["status"], "NOT_EVALUATED")
        self.assertIn("YPLUS_DIRECT_FIELD_REQUIRED", inferred_from_nut["blockers"])

    def test_design_candidate_checks_the_residual_tail_not_only_its_last_row(self):
        """A recovered last residual cannot hide an unstable tail sample."""
        solver = self._candidate_solver()
        solver["thermal_residual_history"]["T"][1]["final"] = 2e-5

        result = self._evaluate_candidate(self._valid_sensitivity(), solver=solver)

        self.assertEqual(result["status"], "NOT_EVALUATED")
        self.assertIn("TAIL_RESIDUAL_LIMIT", result["blockers"])

    def test_design_candidate_uses_peak_courant_not_last_log_value(self):
        """An early Co spike must block design review even after recovery."""
        solver = {
            "thermal_residuals": {
                field: {"final": 1e-6}
                for field in ("Ux", "Uy", "Uz", "p_rgh", "T", "k", "omega")
            },
            "continuity": {"global": 1e-7},
            "courant": {"maximum": 0.7, "peak_maximum": 1.2},
        }
        thermal = {
            "minimum_k": 292.5,
            "maximum_k": 294.0,
            "energy_closure_basis": "solver_positive_phi_and_owner_cell_temperature",
        }
        result = cfd_numerics.evaluate_thermal_numerics(
            self._candidate(), solver, thermal,
            {"available": True, "imbalance_ratio": 0.0005},
            {
                "reference_temperature_k": 293.15,
                "thermal_expansion_coefficient_1_k": 0.00341,
                "minimum_wall_treatment_area_ratio": 0.80,
                "thermal_max_courant_gate": 1.0,
            },
            sensitivity={"contract": "numerical_sensitivity.v1", "status": "PASS"},
            y_plus={
                "available": True,
                "method": "openfoam_yPlus_field",
                "wall_treatment_acceptable_area_ratio": 1.0,
            },
        )

        self.assertEqual(result["status"], "NOT_EVALUATED")
        self.assertIn("COURANT_LIMIT", result["blockers"])

    def test_numerical_sensitivity_schema_is_available(self):
        schema_path = Path(__file__).resolve().parents[1] / (
            "numerical_sensitivity.v1.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))

        self.assertEqual(schema["$id"], "numerical_sensitivity.v1.schema.json")
        self.assertEqual(
            schema["properties"]["contract"]["const"],
            "numerical_sensitivity.v1",
        )
        self.assertIn("qoi_comparisons", schema["required"])
        self.assertTrue({"tail_maximum", "tail_samples"}.issubset(
            schema["$defs"]["residual1e5"]["required"]
        ))

    def test_sensitivity_pair_fails_closed_on_provenance_and_independence(self):
        cases = []

        same_run = self._valid_sensitivity()
        same_run["variant"]["run_hash"] = same_run["baseline"]["run_hash"]
        cases.append((same_run, "NUMERICAL_SENSITIVITY_RUN_HASH_NOT_DISTINCT"))

        mesh_changed = self._valid_sensitivity()
        mesh_changed["variant"]["mesh_hash"] = "e" * 64
        cases.append((mesh_changed, "NUMERICAL_SENSITIVITY_MESH_HASH_MISMATCH"))

        physics_changed = self._valid_sensitivity()
        physics_changed["variant"]["physical_input_hash"] = "f" * 64
        cases.append(
            (physics_changed, "NUMERICAL_SENSITIVITY_PHYSICAL_INPUT_HASH_MISMATCH")
        )

        undeclared_change = self._valid_sensitivity()
        undeclared_change["allowed_variation"]["all_other_inputs_equal"] = False
        cases.append(
            (undeclared_change, "NUMERICAL_SENSITIVITY_ALLOWED_VARIATION_INVALID")
        )

        for artifact, blocker in cases:
            with self.subTest(blocker=blocker):
                result = self._evaluate_candidate(artifact)
                self.assertEqual(result["status"], "NOT_EVALUATED")
                self.assertIn(blocker, result["blockers"])

    def test_sensitivity_pair_requires_qoi_and_stable_solver_evidence(self):
        cases = []

        missing_qoi = self._valid_sensitivity()
        missing_qoi["qoi_comparisons"] = missing_qoi["qoi_comparisons"][:-1]
        cases.append((missing_qoi, "NUMERICAL_SENSITIVITY_QOI_MISSING"))

        unstable = self._valid_sensitivity()
        unstable["baseline"]["solver_evidence"]["ended"] = False
        cases.append((unstable, "NUMERICAL_SENSITIVITY_BASELINE_NOT_ENDED"))

        fatal = self._valid_sensitivity()
        fatal["variant"]["solver_evidence"]["fatal_error"] = True
        cases.append((fatal, "NUMERICAL_SENSITIVITY_VARIANT_FATAL_ERROR"))

        high_co = self._valid_sensitivity()
        high_co["variant"]["solver_evidence"]["peak_courant"] = 1.1
        cases.append((high_co, "NUMERICAL_SENSITIVITY_VARIANT_COURANT_LIMIT"))

        bad_residual = self._valid_sensitivity()
        bad_residual["baseline"]["solver_evidence"]["residuals"]["T"] = {
            "final": 2e-5,
            "tail_maximum": 2e-5,
            "tail_samples": cfd_numerics.DEFAULT_RESIDUAL_TAIL_SAMPLES,
            "limit": 1e-5,
        }
        cases.append((bad_residual, "NUMERICAL_SENSITIVITY_BASELINE_RESIDUAL_LIMIT"))

        missing_residual_tail = self._valid_sensitivity()
        missing_residual_tail["baseline"]["solver_evidence"]["residuals"]["T"].pop(
            "tail_maximum"
        )
        cases.append((
            missing_residual_tail,
            "NUMERICAL_SENSITIVITY_BASELINE_RESIDUAL_EVIDENCE_INVALID",
        ))

        bad_continuity = self._valid_sensitivity()
        bad_continuity["variant"]["solver_evidence"]["continuity"]["global"] = 2e-6
        cases.append(
            (bad_continuity, "NUMERICAL_SENSITIVITY_VARIANT_CONTINUITY_LIMIT")
        )

        bad_phi = self._valid_sensitivity()
        bad_phi["baseline"]["solver_evidence"]["phi_balance"][
            "imbalance_ratio"
        ] = 0.002
        cases.append((bad_phi, "NUMERICAL_SENSITIVITY_BASELINE_PHI_LIMIT"))

        wrong_energy_basis = self._valid_sensitivity()
        wrong_energy_basis["variant"]["solver_evidence"][
            "energy_closure_basis"
        ] = "design_input_fallback"
        cases.append(
            (wrong_energy_basis, "NUMERICAL_SENSITIVITY_VARIANT_ENERGY_BASIS")
        )

        nonfinite_qoi = self._valid_sensitivity()
        nonfinite_qoi["qoi_comparisons"][0]["variant"] = float("nan")
        cases.append((nonfinite_qoi, "NUMERICAL_SENSITIVITY_QOI_INVALID"))

        for artifact, blocker in cases:
            with self.subTest(blocker=blocker):
                result = self._evaluate_candidate(copy.deepcopy(artifact))
                self.assertEqual(result["status"], "NOT_EVALUATED")
                self.assertIn(blocker, result["blockers"])
