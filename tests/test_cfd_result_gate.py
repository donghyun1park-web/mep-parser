import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import cfd_radiation
import cfd_result_gate
import cfd_report


def _parsed(*, crashed=False, continuity=1e-7, residual=1e-4):
    return {
        "crashed": crashed,
        "continuity_global": [(1000, continuity)],
        "residuals": {
            "Ux": [1e-1, residual],
            "Uy": [1e-1, residual],
            "Uz": [1e-1, residual],
            "p_rgh": [1e-1, residual],
            "T": [1e-1, residual],
            "k": [1e-1, residual],
            "epsilon": [1e-1, residual],
        },
    }


def _complete_metrics(*, closure=99.0, mass_error=0.5, temperature=25.0):
    return {
        "T_avg_C": temperature,
        "T_max_C": temperature + 1.0,
        "U_max": 0.4,
        "closure_pct": closure,
        "closure_osc": 2.0,
        "mass_err_pct": mass_error,
    }


class ScreeningResultGateTests(unittest.TestCase):
    def test_missing_field_metrics_are_not_evaluated(self):
        gate = cfd_result_gate.evaluate_screening_result(_parsed(), None)

        self.assertEqual(gate["status"], "NOT_EVALUATED")
        self.assertFalse(gate["citable"])
        self.assertIn("field_metrics", gate["blockers"])

    def test_incomplete_field_metrics_are_not_evaluated(self):
        gate = cfd_result_gate.evaluate_screening_result(
            _parsed(), {"T_avg_C": 25.0}
        )

        self.assertEqual(gate["status"], "NOT_EVALUATED")
        self.assertFalse(gate["citable"])
        self.assertIn("field_metrics", gate["blockers"])

    def test_missing_required_residual_is_not_evaluated(self):
        parsed = {
            "crashed": False,
            "continuity_global": [(1000, 1e-7)],
            "residuals": {"Ux": [1e-4]},
        }
        metrics = {
            "T_avg_C": 25.0, "T_max_C": 26.0, "U_max": 0.4,
            "closure_pct": 99.0, "closure_osc": 2.0, "mass_err_pct": 0.5,
        }

        gate = cfd_result_gate.evaluate_screening_result(parsed, metrics)

        self.assertEqual(gate["status"], "NOT_EVALUATED")
        self.assertFalse(gate["citable"])
        self.assertIn("residuals", gate["blockers"])

    def test_heat_case_without_energy_balance_is_not_evaluated(self):
        metrics = _complete_metrics()
        metrics.pop("closure_pct")
        metrics.pop("mass_err_pct")

        gate = cfd_result_gate.evaluate_screening_result(
            _parsed(), metrics, energy_required=True
        )

        self.assertEqual(gate["status"], "NOT_EVALUATED")
        self.assertFalse(gate["citable"])
        self.assertIn("field_metrics", gate["blockers"])

    def test_good_screening_result_is_advisory_not_design_ready(self):
        gate = cfd_result_gate.evaluate_screening_result(
            _parsed(),
            _complete_metrics(),
            model_quality={"design_ready": False},
        )

        self.assertEqual(gate["status"], "PASS")
        self.assertEqual(gate["citation_status"], "SCREENING_ONLY")
        self.assertFalse(gate["design_ready"])
        self.assertTrue(gate["citable"])
        self.assertIn("model_quality", gate["blockers"])

    def test_unresolved_opening_blocks_jet_metrics_not_thermal_screening(self):
        gate = cfd_result_gate.evaluate_screening_result(
            _parsed(), _complete_metrics(),
            opening_preflight={
                "contract": "opening_preflight.v2",
                "opening_resolution_ok": False,
                "jet_metrics_citable": False,
                "warnings": ["sup0"],
            },
        )

        self.assertEqual(gate["status"], "PASS")
        self.assertTrue(gate["citable"])
        self.assertFalse(gate["evidence"]["opening"]["jet_metrics_citable"])
        self.assertFalse(gate["evidence"]["opening"]["opening_resolution_ok"])

    def test_residual_warning_blocks_screening_citation(self):
        gate = cfd_result_gate.evaluate_screening_result(
            _parsed(residual=2e-3),
            _complete_metrics(closure=101.0),
        )

        self.assertEqual(gate["status"], "WARN")
        self.assertEqual(gate["convergence_status"], "WARN")
        self.assertFalse(gate["citable"])
        self.assertEqual(gate["citation_status"], "NOT_EVALUATED")
        self.assertIn("residuals", gate["blockers"])

    def test_legacy_report_adapter_exposes_warning_as_not_evaluated(self):
        trust = cfd_report.result_trust(
            _parsed(residual=2e-3),
            _complete_metrics(closure=101.0),
        )

        self.assertFalse(trust["citable"])
        self.assertEqual(trust["status"], "WARN")
        self.assertEqual(trust["citation_status"], "NOT_EVALUATED")
        self.assertIn("residuals", trust["blockers"])

    def test_report_adapter_does_not_keep_green_badge_when_gate_is_not_evaluated(self):
        """A missing required residual must not look converged in the Studio/report."""
        parsed = _parsed()
        del parsed["residuals"]["T"]

        trust = cfd_report.result_trust(parsed, _complete_metrics())

        self.assertEqual(trust["status"], "NOT_EVALUATED")
        self.assertFalse(trust["citable"])
        self.assertNotEqual(trust["color"], "#1e8449")
        self.assertTrue(any(
            "수렴 여부를 평가할 수 없습니다." in reason
            for reason in trust["reasons"]
        ))


class BodyFittedResultGateTests(unittest.TestCase):
    def _write_json(self, path, payload):
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _sha256(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _json_sha256(self, payload):
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _write_valid_body_artifacts(self, case):
        run = case / "run_manifest.json"
        mesh = case / "mesh_manifest.json"
        result = case / "result_manifest.json"
        thermal = case / "thermal_input.json"
        system = case / "system"
        results = case / "results"
        slices = results / "slices"
        slices.mkdir(parents=True)
        system.mkdir()
        source = results / "internal.vtu"
        summary = results / "body_fitted_summary.json"
        source.write_text("<VTKFile/>", encoding="ascii")
        settings = {"thermal_numerics_profile": "design_limited_second_order_v1"}
        numerics = {
            "profile": "design_limited_second_order_v1",
            "convection_order": 2,
            "laplacian_correction": "limited 0.5",
            "sn_grad_correction": "limited 0.5",
            "required_non_orthogonal_correctors": 2,
        }
        self._write_json(thermal, {
            "contract": "thermal_input.v1", "case": "confirmed",
            "settings": settings, "numerics": numerics,
        })
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
            "fvSolution": (
                "PIMPLE { nCorrectors 2; nNonOrthogonalCorrectors 2; }\n"
            ),
        }.items():
            (system / name).write_text(contents, encoding="ascii")
        system_hashes = {
            name: self._sha256(system / name)
            for name in ("controlDict", "fvSchemes", "fvSolution")
        }
        self._write_json(summary, {
            "contract": "body_fitted_summary.v1", "time_s": 1.0,
            "cell_count": 4,
            "bounds_m": {"minimum": [0, 0, 0], "maximum": [1, 1, 1]},
            "fields": {"T": {"unit": "K"}, "U": {"unit": "m/s"}},
            "temperature": {"maximum": 295.0, "p95": 294.0},
            "velocity": {"p95_speed": 0.4},
        })
        slice_refs = []
        for axis in "xyz":
            path = slices / f"{axis}_mid.json"
            self._write_json(path, {
                "axis": axis, "target_m": 0.5, "sample_count": 0, "samples": [],
            })
            slice_refs.append({
                "axis": axis, "path": path.relative_to(case).as_posix(),
                "sha256": self._sha256(path),
            })
        self._write_json(mesh, {
            "contract": "mesh_manifest.v1",
            "status": "PASS",
        })
        self._write_json(run, {
            "contract": "run_manifest.v1",
            "engine": "body_fitted_buoyant_urans",
            "status": "PASS",
            "design_ready": True,
            "effective_settings": settings,
            "effective_numerics": numerics,
            "numerical_quality": {
                "contract": "numerical_quality.v1",
                "status": "PASS",
                "design_ready": True,
                "profile": "design_limited_second_order_v1",
                "convection_order": 2,
            },
            "input": {
                "thermal_input_sha256": self._sha256(thermal),
                "numerical_provenance": {
                    "contract": "thermal_numerics_provenance.v1",
                    "source": "thermal_initial_input",
                    "thermal_input_sha256": self._sha256(thermal),
                    "thermal_restart_input_sha256": None,
                    "effective_settings_sha256": self._json_sha256(settings),
                    "effective_numerics_sha256": self._json_sha256(numerics),
                    "expected_system": dict(system_hashes),
                    "system": dict(system_hashes),
                },
            },
        })
        self._write_json(result, {
            "contract": "result_manifest.v1",
            "engine": "body_fitted_openfoam_vtu",
            "source": {
                "path": source.relative_to(case).as_posix(),
                "sha256": self._sha256(source),
                "format": "VTK XML UnstructuredGrid ASCII",
            },
            "summary_path": summary.relative_to(case).as_posix(),
            "summary_sha256": self._sha256(summary),
            "slices": slice_refs,
            "run_manifest_sha256": self._sha256(run),
            "mesh_manifest_sha256": self._sha256(mesh),
            "thermal_input_sha256": self._sha256(thermal),
        })
        gci_root = case / "gci"
        gci_manifest = gci_root / "gci-pass" / "grid_convergence.json"
        gci_manifest.parent.mkdir(parents=True)
        self._write_json(gci_manifest, {
            "contract": "grid_convergence.v3",
            "status": "PASS",
            "design_ready": True,
            "cases": [{
                "path": str(case.resolve()),
                "provenance": {
                    "run_manifest_sha256": self._sha256(run),
                    "result_manifest_sha256": self._sha256(result),
                    "mesh_manifest_sha256": self._sha256(mesh),
                    "thermal_input_sha256": self._sha256(thermal),
                },
            }],
        })
        return run, source, gci_root

    def test_body_result_without_artifact_and_gci_evidence_is_not_evaluated(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-result-gate-", dir=repo) as tmp:
            case = Path(tmp)
            run = case / "run_manifest.json"
            mesh = case / "mesh_manifest.json"
            result = case / "result_manifest.json"
            self._write_json(mesh, {"contract": "mesh_manifest.v1", "status": "PASS"})
            self._write_json(run, {
                "contract": "run_manifest.v1",
                "engine": "body_fitted_buoyant_urans",
                "status": "PASS", "design_ready": True, "input": {"ok": True},
            })
            self._write_json(result, {
                "contract": "result_manifest.v1",
                "engine": "body_fitted_openfoam_vtu",
                "run_manifest_sha256": self._sha256(run),
                "mesh_manifest_sha256": self._sha256(mesh),
            })

            gate = cfd_result_gate.evaluate_body_fitted_case(case)

            self.assertEqual(gate["citation_status"], "NOT_EVALUATED")
            self.assertIn("result_artifacts", gate["blockers"])

    def test_stale_result_manifest_is_not_evaluated(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-result-gate-", dir=repo) as tmp:
            case = Path(tmp)
            run, _source, gci_root = self._write_valid_body_artifacts(case)

            fresh = cfd_result_gate.evaluate_gci_candidate(case)
            self.assertEqual(fresh["status"], "GCI_CANDIDATE")
            self.assertFalse(fresh["citable"])

            self._write_json(run, {
                "contract": "run_manifest.v1",
                "engine": "body_fitted_buoyant_urans",
                "status": "PASS",
                "design_ready": True,
                "changed_after_results": True,
            })
            stale = cfd_result_gate.evaluate_body_fitted_case(case, gci_root=gci_root)

            self.assertEqual(stale["status"], "NOT_EVALUATED")
            self.assertEqual(stale["citation_status"], "NOT_EVALUATED")
            self.assertIn("result_manifest_stale", stale["blockers"])

    def test_sensitivity_pending_second_order_case_is_gci_candidate_not_citable(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-gci-candidate-", dir=repo) as tmp:
            case = Path(tmp)
            run_path, _source, gci_root = self._write_valid_body_artifacts(case)
            run = json.loads(run_path.read_text(encoding="utf-8"))
            run["design_ready"] = False
            run["numerical_quality"].update({
                "status": "NOT_EVALUATED",
                "design_ready": False,
                "blockers": ["NUMERICAL_SENSITIVITY_ARTIFACT_UNVERIFIED"],
            })
            self._write_json(run_path, run)
            result_path = case / "result_manifest.json"
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["run_manifest_sha256"] = self._sha256(run_path)
            self._write_json(result_path, result)

            candidate = cfd_result_gate.evaluate_gci_candidate(case)
            final = cfd_result_gate.evaluate_body_fitted_case(case, gci_root=gci_root)

        self.assertEqual(candidate["status"], "GCI_CANDIDATE")
        self.assertEqual(candidate["citation_status"], "NOT_EVALUATED")
        self.assertFalse(candidate["design_ready"])
        self.assertFalse(candidate["citable"])
        self.assertNotEqual(final["citation_status"], "DESIGN_CITABLE")
        self.assertIn("validation_anchor", final["blockers"])

    def test_gci_candidate_rejects_non_sensitivity_numerical_failure(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-gci-candidate-", dir=repo) as tmp:
            case = Path(tmp)
            run_path, _source, _gci_root = self._write_valid_body_artifacts(case)
            run = json.loads(run_path.read_text(encoding="utf-8"))
            run["design_ready"] = False
            run["numerical_quality"].update({
                "status": "NOT_EVALUATED",
                "design_ready": False,
                "blockers": [
                    "NUMERICAL_SENSITIVITY_ARTIFACT_UNVERIFIED",
                    "COURANT_LIMIT",
                ],
            })
            self._write_json(run_path, run)
            result_path = case / "result_manifest.json"
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["run_manifest_sha256"] = self._sha256(run_path)
            self._write_json(result_path, result)

            candidate = cfd_result_gate.evaluate_gci_candidate(case)

        self.assertNotEqual(candidate["status"], "GCI_CANDIDATE")
        self.assertIn("numerical_quality", candidate["blockers"])

    def test_unverified_pass_document_is_not_final_validation_evidence(self):
        for filename in (
            "numerical_sensitivity.json", "temporal_sensitivity.json",
            "benchmark_validation.json", "applicability_envelope.json",
        ):
            blockers = cfd_result_gate._validate_final_evidence_document(
                filename, {"status": "PASS"}, anchor_reference={
                    "anchor_id": "anchor-" + "a" * 16,
                    "sha256": "b" * 64,
                },
            )
            self.assertTrue(blockers, filename)

    def test_body_result_requires_numerical_quality_evidence(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-result-gate-numerics-", dir=repo) as tmp:
            case = Path(tmp)
            run, _source, gci_root = self._write_valid_body_artifacts(case)
            payload = json.loads(run.read_text(encoding="utf-8"))
            payload.pop("numerical_quality")
            self._write_json(run, payload)

            gate = cfd_result_gate.evaluate_body_fitted_case(case, gci_root=gci_root)

        self.assertFalse(gate["design_ready"])
        self.assertIn("numerical_quality", gate["blockers"])

    def test_body_result_requires_buoyant_thermal_engine(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-result-gate-engine-", dir=repo) as tmp:
            case = Path(tmp)
            run, _source, gci_root = self._write_valid_body_artifacts(case)
            payload = json.loads(run.read_text(encoding="utf-8"))
            payload["engine"] = "body_fitted_isothermal_urans"
            self._write_json(run, payload)

            gate = cfd_result_gate.evaluate_body_fitted_case(case, gci_root=gci_root)

        self.assertFalse(gate["design_ready"])
        self.assertIn("run_engine", gate["blockers"])

    def test_body_result_requires_numerical_provenance(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-result-gate-numerics-", dir=repo) as tmp:
            case = Path(tmp)
            run, _source, gci_root = self._write_valid_body_artifacts(case)
            payload = json.loads(run.read_text(encoding="utf-8"))
            payload["input"].pop("numerical_provenance")
            self._write_json(run, payload)

            gate = cfd_result_gate.evaluate_body_fitted_case(case, gci_root=gci_root)

        self.assertFalse(gate["design_ready"])
        self.assertIn("numerical_provenance", gate["blockers"])

    def test_body_result_rejects_changed_fv_schemes(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-result-gate-numerics-", dir=repo) as tmp:
            case = Path(tmp)
            _run, _source, gci_root = self._write_valid_body_artifacts(case)
            (case / "system" / "fvSchemes").write_text(
                "divSchemes { default upwind; }\n", encoding="ascii"
            )

            gate = cfd_result_gate.evaluate_body_fitted_case(case, gci_root=gci_root)

        self.assertFalse(gate["design_ready"])
        self.assertIn("numerical_provenance", gate["blockers"])

    def test_body_result_rejects_preexisting_upwind_fv_schemes_with_fresh_hashes(self):
        """A self-consistent file hash cannot override the declared profile."""
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-result-gate-numerics-", dir=repo) as tmp:
            case = Path(tmp)
            run_path, _source, gci_root = self._write_valid_body_artifacts(case)
            (case / "system" / "fvSchemes").write_text(
                "divSchemes { default upwind; }\n", encoding="ascii"
            )
            run = json.loads(run_path.read_text(encoding="utf-8"))
            fresh_schemes_hash = self._sha256(case / "system" / "fvSchemes")
            provenance = run["input"]["numerical_provenance"]
            provenance["system"]["fvSchemes"] = fresh_schemes_hash
            provenance["expected_system"]["fvSchemes"] = fresh_schemes_hash
            self._write_json(run_path, run)

            result_path = case / "result_manifest.json"
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["run_manifest_sha256"] = self._sha256(run_path)
            self._write_json(result_path, result)
            gci_path = gci_root / "gci-pass" / "grid_convergence.json"
            gci = json.loads(gci_path.read_text(encoding="utf-8"))
            gci["cases"][0]["provenance"].update({
                "run_manifest_sha256": self._sha256(run_path),
                "result_manifest_sha256": self._sha256(result_path),
            })
            self._write_json(gci_path, gci)

            gate = cfd_result_gate.evaluate_body_fitted_case(case, gci_root=gci_root)

        self.assertFalse(gate["design_ready"])
        self.assertIn("numerical_provenance", gate["blockers"])
        self.assertIn("SEMANTIC_DIV_PHI_U_NOT_LIMITED_SECOND_ORDER", gate["reasons"][0])

    def test_body_result_rejects_first_order_profile_despite_claimed_second_order_quality(self):
        """A first-order physical case must not be promoted by a stale quality claim."""
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-result-gate-numerics-", dir=repo) as tmp:
            case = Path(tmp)
            run_path, _source, gci_root = self._write_valid_body_artifacts(case)
            settings = {"thermal_numerics_profile": "stabilized_first_order_v1"}
            numerics = {
                "profile": "stabilized_first_order_v1",
                "convection_order": 1,
                "laplacian_correction": "uncorrected",
                "sn_grad_correction": "uncorrected",
                "required_non_orthogonal_correctors": 2,
            }
            thermal_path = case / "thermal_input.json"
            thermal = json.loads(thermal_path.read_text(encoding="utf-8"))
            thermal.update({"settings": settings, "numerics": numerics})
            self._write_json(thermal_path, thermal)
            (case / "system" / "fvSchemes").write_text(
                "divSchemes { default upwind; }\n", encoding="ascii"
            )

            run = json.loads(run_path.read_text(encoding="utf-8"))
            run["effective_settings"] = settings
            run["effective_numerics"] = numerics
            # This represents a stale or otherwise inconsistent summary claim.
            run["numerical_quality"] = {
                "contract": "numerical_quality.v1",
                "status": "PASS",
                "design_ready": True,
                "convection_order": 2,
            }
            provenance = run["input"]["numerical_provenance"]
            provenance["thermal_input_sha256"] = self._sha256(thermal_path)
            provenance["effective_settings_sha256"] = self._json_sha256(settings)
            provenance["effective_numerics_sha256"] = self._json_sha256(numerics)
            for name in ("controlDict", "fvSchemes", "fvSolution"):
                fingerprint = self._sha256(case / "system" / name)
                provenance["system"][name] = fingerprint
                provenance["expected_system"][name] = fingerprint
            run["input"]["thermal_input_sha256"] = self._sha256(thermal_path)
            self._write_json(run_path, run)

            result_path = case / "result_manifest.json"
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["run_manifest_sha256"] = self._sha256(run_path)
            result["thermal_input_sha256"] = self._sha256(thermal_path)
            self._write_json(result_path, result)
            gci_path = gci_root / "gci-pass" / "grid_convergence.json"
            gci = json.loads(gci_path.read_text(encoding="utf-8"))
            gci["cases"][0]["provenance"].update({
                "run_manifest_sha256": self._sha256(run_path),
                "result_manifest_sha256": self._sha256(result_path),
                "thermal_input_sha256": self._sha256(thermal_path),
            })
            self._write_json(gci_path, gci)

            gate = cfd_result_gate.evaluate_body_fitted_case(case, gci_root=gci_root)

        self.assertFalse(gate["design_ready"])
        self.assertEqual(gate["citation_status"], "NOT_EVALUATED")
        self.assertIn("numerical_profile", gate["blockers"])

    def test_public_numerical_provenance_checker_exposes_semantic_upwind_failure(self):
        """GCI can reuse this check without invoking the GCI-dependent result gate."""
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-result-gate-numerics-", dir=repo) as tmp:
            case = Path(tmp)
            run_path, _source, _gci_root = self._write_valid_body_artifacts(case)
            (case / "system" / "fvSchemes").write_text(
                "divSchemes { default upwind; }\n", encoding="ascii"
            )
            run = json.loads(run_path.read_text(encoding="utf-8"))
            fresh_schemes_hash = self._sha256(case / "system" / "fvSchemes")
            provenance = run["input"]["numerical_provenance"]
            provenance["system"]["fvSchemes"] = fresh_schemes_hash
            provenance["expected_system"]["fvSchemes"] = fresh_schemes_hash
            self._write_json(run_path, run)
            thermal = json.loads((case / "thermal_input.json").read_text(encoding="utf-8"))

            issues = cfd_result_gate.body_fitted_numerical_provenance_issues(
                case, run, thermal
            )

        self.assertIn("SEMANTIC_DIV_PHI_U_NOT_LIMITED_SECOND_ORDER", issues)

    def test_body_result_rejects_changed_restart_numerics_input(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-result-gate-numerics-", dir=repo) as tmp:
            case = Path(tmp)
            run_path, _source, gci_root = self._write_valid_body_artifacts(case)
            thermal = case / "thermal_input.json"
            run = json.loads(run_path.read_text(encoding="utf-8"))
            restart = {
                "contract": "thermal_restart_input.v1",
                "settings": run["effective_settings"],
                "thermal_numerics": run["effective_numerics"],
                "thermal_input_sha256": self._sha256(thermal),
            }
            restart_path = case / "thermal_restart_input.json"
            self._write_json(restart_path, restart)
            provenance = run["input"]["numerical_provenance"]
            provenance["source"] = "thermal_restart_input"
            provenance["thermal_restart_input_sha256"] = self._sha256(restart_path)
            self._write_json(run_path, run)

            result_path = case / "result_manifest.json"
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["run_manifest_sha256"] = self._sha256(run_path)
            self._write_json(result_path, result)
            gci_path = gci_root / "gci-pass" / "grid_convergence.json"
            gci = json.loads(gci_path.read_text(encoding="utf-8"))
            gci["cases"][0]["provenance"].update({
                "run_manifest_sha256": self._sha256(run_path),
                "result_manifest_sha256": self._sha256(result_path),
            })
            self._write_json(gci_path, gci)

            fresh = cfd_result_gate.evaluate_gci_candidate(case)
            self.assertEqual(fresh["status"], "GCI_CANDIDATE")

            restart["settings"] = {"changed_after_run": True}
            self._write_json(restart_path, restart)
            stale = cfd_result_gate.evaluate_body_fitted_case(case, gci_root=gci_root)

        self.assertFalse(stale["design_ready"])
        self.assertIn("numerical_provenance", stale["blockers"])

    def test_claimed_radiation_requires_a_validated_radiation_manifest(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-result-gate-radiation-", dir=repo) as tmp:
            case = Path(tmp)
            self._write_valid_body_artifacts(case)
            thermal = case / "thermal_input.json"
            payload = json.loads(thermal.read_text(encoding="utf-8"))
            payload["assumptions"] = {"radiation_modelled": True}
            self._write_json(thermal, payload)

            gate = cfd_result_gate.evaluate_body_fitted_case(case)

        self.assertFalse(gate["design_ready"])
        self.assertIn("radiation_manifest", gate["blockers"])

    def test_claimed_radiation_rejects_manifest_without_benchmark_evidence(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-result-gate-radiation-", dir=repo) as tmp:
            case = Path(tmp)
            self._write_valid_body_artifacts(case)
            thermal = case / "thermal_input.json"
            payload = json.loads(thermal.read_text(encoding="utf-8"))
            payload["assumptions"] = {"radiation_modelled": True}
            self._write_json(thermal, payload)
            self._write_json(case / "radiation_manifest.json", {
                "contract": "radiation_manifest.v1",
                "status": "PASS",
                "thermal_input_sha256": self._sha256(thermal),
            })

            gate = cfd_result_gate.evaluate_body_fitted_case(case)

        self.assertIn("radiation_manifest", gate["blockers"])

    def test_claimed_radiation_stays_blocked_even_with_benchmark_shaped_evidence(self):
        """The serial two-plate benchmark cannot enable field radiation yet."""
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-result-gate-radiation-", dir=repo) as tmp:
            case = Path(tmp)
            run_path, _source, gci_root = self._write_valid_body_artifacts(case)
            thermal_path = case / "thermal_input.json"
            thermal = json.loads(thermal_path.read_text(encoding="utf-8"))
            thermal["assumptions"] = {"radiation_modelled": True}
            self._write_json(thermal_path, thermal)
            thermal_sha256 = self._sha256(thermal_path)

            run = json.loads(run_path.read_text(encoding="utf-8"))
            run["input"]["thermal_input_sha256"] = thermal_sha256
            run["input"]["numerical_provenance"]["thermal_input_sha256"] = thermal_sha256
            self._write_json(run_path, run)

            result_path = case / "result_manifest.json"
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["run_manifest_sha256"] = self._sha256(run_path)
            result["thermal_input_sha256"] = thermal_sha256
            self._write_json(result_path, result)
            gci_path = gci_root / "gci-pass" / "grid_convergence.json"
            gci = json.loads(gci_path.read_text(encoding="utf-8"))
            gci["cases"][0]["provenance"].update({
                "run_manifest_sha256": self._sha256(run_path),
                "result_manifest_sha256": self._sha256(result_path),
                "thermal_input_sha256": thermal_sha256,
            })
            self._write_json(gci_path, gci)
            self._write_json(case / "radiation_manifest.json", {
                "contract": "radiation_manifest.v1",
                "status": "PASS",
                "scope": "serial_two_plate_view_factor_benchmark_only",
                "design_ready": False,
                "citation_status": "BENCHMARK_ONLY",
                "thermal_input_sha256": thermal_sha256,
                "benchmark_reference_sha256": cfd_radiation.benchmark_reference_sha256(),
                "view_factors": {
                    "ok": True, "max_row_sum_error": 0.0,
                    "max_reciprocity_error_m2": 0.0,
                },
                "energy_balance": {"internal_radiation_balance_relative_error": 0.0},
                "fields": {"qr_nonzero": True},
                "patch_net_radiation_power_w": {
                    "hot_plate": -100.0, "cold_plate": 100.0,
                },
            })

            gate = cfd_result_gate.evaluate_body_fitted_case(case, gci_root=gci_root)

        self.assertFalse(gate["design_ready"])
        self.assertIn("radiation_project_integration_pending", gate["blockers"])

    def test_body_result_rejects_changed_hashed_summary(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-result-gate-", dir=repo) as tmp:
            case = Path(tmp)
            _run, _source, gci_root = self._write_valid_body_artifacts(case)
            summary = case / "results" / "body_fitted_summary.json"
            self._write_json(summary, {"tampered": True})

            gate = cfd_result_gate.evaluate_body_fitted_case(case, gci_root=gci_root)

        self.assertEqual(gate["citation_status"], "NOT_EVALUATED")
        self.assertIn("result_artifacts", gate["blockers"])

    def test_body_result_rejects_changed_thermal_input(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-result-gate-", dir=repo) as tmp:
            case = Path(tmp)
            _run, _source, gci_root = self._write_valid_body_artifacts(case)
            self._write_json(case / "thermal_input.json", {"changed_after_run": True})

            gate = cfd_result_gate.evaluate_body_fitted_case(case, gci_root=gci_root)

        self.assertEqual(gate["citation_status"], "NOT_EVALUATED")
        self.assertIn("input_provenance", gate["blockers"])

    def test_body_result_requires_direct_thermal_input_artifact_hash(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-result-gate-", dir=repo) as tmp:
            case = Path(tmp)
            _run, _source, gci_root = self._write_valid_body_artifacts(case)
            result_path = case / "result_manifest.json"
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result.pop("thermal_input_sha256")
            self._write_json(result_path, result)
            gci_path = gci_root / "gci-pass" / "grid_convergence.json"
            gci = json.loads(gci_path.read_text(encoding="utf-8"))
            gci["cases"][0]["provenance"]["result_manifest_sha256"] = self._sha256(
                result_path
            )
            self._write_json(gci_path, gci)

            gate = cfd_result_gate.evaluate_body_fitted_case(case, gci_root=gci_root)

        self.assertEqual(gate["citation_status"], "NOT_EVALUATED")
        self.assertIn("result_input_provenance", gate["blockers"])

    def test_body_result_rejects_stale_gci_case_provenance(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-result-gate-", dir=repo) as tmp:
            case = Path(tmp)
            _run, _source, gci_root = self._write_valid_body_artifacts(case)
            gci_path = gci_root / "gci-pass" / "grid_convergence.json"
            gci = json.loads(gci_path.read_text(encoding="utf-8"))
            gci["cases"][0]["provenance"]["result_manifest_sha256"] = "stale"
            self._write_json(gci_path, gci)

            gate = cfd_result_gate.evaluate_body_fitted_case(case, gci_root=gci_root)

        self.assertEqual(gate["citation_status"], "NOT_EVALUATED")
        self.assertIn("gci", gate["blockers"])

    def test_body_result_requires_readable_current_gci_provenance(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix=".test-result-gate-", dir=repo) as tmp:
            case = Path(tmp)
            _run, _source, gci_root = self._write_valid_body_artifacts(case)
            with mock.patch.object(cfd_result_gate, "_current_case_provenance", return_value=None):
                gate = cfd_result_gate.evaluate_body_fitted_case(case, gci_root=gci_root)

        self.assertEqual(gate["citation_status"], "NOT_EVALUATED")
        self.assertIn("gci", gate["blockers"])


if __name__ == "__main__":
    unittest.main()
