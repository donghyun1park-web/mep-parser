import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import field_acceptance
from geometry_v2 import build_review
import install_acceptance
import release_audit
import uat_acceptance


class ReleaseAuditTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[1]
        self.tmp = tempfile.TemporaryDirectory(prefix=".test-release-", dir=self.repo)
        self.workspace = Path(self.tmp.name)
        self.projects = self.workspace / "cfd_projects"

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _write(path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    @staticmethod
    def _json_hash(payload):
        encoded = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _traceable_g2_case(self, study, index):
        case = study / "cases" / f"level-{index}"
        artifacts = {
            "run_manifest.json": {
                "contract": "run_manifest.v1",
                "engine": "body_fitted_buoyant_urans",
                "status": "PASS", "design_ready": True,
            },
            "result_manifest.json": {
                "contract": "result_manifest.v1",
                "engine": "body_fitted_openfoam_vtu",
            },
            "mesh_manifest.json": {
                "contract": "mesh_manifest.v1", "status": "PASS",
            },
            "thermal_input.json": {"contract": "thermal_input.v1"},
        }
        for filename, payload in artifacts.items():
            self._write(case / filename, payload)
        provenance = {
            key: hashlib.sha256((case / filename).read_bytes()).hexdigest()
            for key, filename in {
                "run_manifest_sha256": "run_manifest.json",
                "result_manifest_sha256": "result_manifest.json",
                "mesh_manifest_sha256": "mesh_manifest.json",
                "thermal_input_sha256": "thermal_input.json",
            }.items()
        }
        return {
            "name": f"level-{index}", "path": str(case.resolve()),
            "time_s": 300.0, "cell_count": 1000 * index,
            "fluid_volume_m3": 100.0,
            "effective_grid_width_m": 0.1 * (5 - index),
            "reference_temperature_k": 293.15,
            "metrics": {"temperature_volume_mean_c": 22.0},
            "diagnostics": {}, "time_window": {"duration_s": 60.0},
            "provenance": provenance,
        }

    def _write_traceable_g2_evidence(self, benchmark, study_name="gci-pass"):
        study = self.projects / "_body_gci" / study_name
        cases = [self._traceable_g2_case(study, index)
                 for index in range(1, 5)]
        self._write(study / "gci_job.json", {
            "schema_version": 1, "contract": "gci_job.v1",
            "engine": "body_fitted_thermal_gci_job", "status": "complete",
            "input": {
                "gci_contract": "grid_convergence.v3",
                "geometry_path": str(benchmark.resolve()),
                "geometry_sha256": hashlib.sha256(
                    benchmark.read_bytes()
                ).hexdigest(),
            },
        })
        self._write(study / "grid_convergence.json", {
            "schema_version": 3, "contract": "grid_convergence.v3",
            "engine": "body_fitted_thermal_mesh_uncertainty_lsr",
            "created_at": "2026-07-21T00:00:00+00:00",
            "status": "PASS", "design_ready": True, "errors": [],
            "comparison": {
                "grid_count": len(cases),
                "minimum_flow_through_fraction": 3.0,
                "maximum_window_drift_pct": 2.0,
            },
            "cases": cases,
            "metrics": [{"status": "PASS"}] * 3,
        })
        return study

    def _field_evidence(self, index, unit, source_name=None):
        import ezdxf

        source = self.projects / "imports" / (source_name or f"site-{index}.dxf")
        source.parent.mkdir(parents=True, exist_ok=True)
        document = ezdxf.new("R2010")
        document.units = unit
        layer = f"SITE-{index}"
        document.layers.add(layer)
        offset = index * 10000.0
        document.modelspace().add_lwpolyline(
            [(offset, 0), (offset + 6000, 0), (offset + 6000, 4000),
             (offset, 4000), (offset, 0)], dxfattribs={"layer": layer}
        )
        block_name = f"ROTATED-EQUIPMENT-{index}"
        block = document.blocks.new(block_name)
        block.add_line((0, 0), (500, 0), dxfattribs={"layer": layer})
        document.modelspace().add_blockref(
            block_name, (offset + 1000, 1000),
            dxfattribs={"layer": layer, "rotation": index * 15.0},
        )
        document.saveas(source)

        geometry_path = self.projects / "geometry" / f"site-{index}.geometry.json"
        geometry = {
            "schema_version": 2, "contract": "geometry.v2",
            "source": str(source.resolve()), "units": "mm",
            "coordinate_system": {
                "axis_convention": "XY_Z_UP", "origin_mm": [0, 0, 0],
                "rotation_deg": 0, "millimetres_to_metres": 0.001,
            },
            "elements": {"zone": [{
                "id": f"zone-{index}", "category": "zone",
                "closed": True,
                "points": [[offset, 0], [offset + 6000, 0],
                           [offset + 6000, 4000], [offset, 4000]],
                "confirmed": True,
                "source_ref": {"handles": ["1"], "layer": layer,
                               "block_name": "", "entity_type": "LWPOLYLINE"},
                "semantic": {"ceiling_height_mm": 3000},
            }]},
        }
        geometry["review"] = build_review(geometry)
        self._write(geometry_path, geometry)

        surface_dir = self.projects / "surface" / f"site-{index}"
        surface_dir.mkdir(parents=True, exist_ok=True)
        stl = surface_dir / "air_volume_regions.stl"
        brep = surface_dir / "air_volume.brep"
        fcstd = surface_dir / "air_volume.FCStd"
        stl.write_text("solid room\nendsolid room\n", encoding="ascii")
        brep.write_bytes(f"brep-{index}".encode("ascii"))
        fcstd.write_bytes(f"fcstd-{index}".encode("ascii"))
        surface = {
            "contract": "surface_manifest.v1",
            "source": {"geometry_path": str(geometry_path.resolve()),
                       "geometry_sha256": hashlib.sha256(geometry_path.read_bytes()).hexdigest(),
                       "geometry_contract": "geometry.v2"},
            "air_volume": {"valid": True, "solid_count": 1},
            "topology": {"watertight": True, "open_edges": 0,
                         "non_manifold_edges": 0, "duplicate_triangles": 0},
            "outputs": {"multi_region_stl": stl.name, "brep": brep.name,
                        "freecad_document": fcstd.name,
                        "stl_sha256": hashlib.sha256(stl.read_bytes()).hexdigest(),
                        "brep_sha256": hashlib.sha256(brep.read_bytes()).hexdigest()},
        }
        surface_manifest = surface_dir / "surface_manifest.json"
        self._write(surface_manifest, surface)

        mesh_case = self.projects / "mesh" / f"site-{index}"
        self._write(mesh_case / "surface_manifest.json", surface)
        mesh_surface = mesh_case / "surface_manifest.json"
        mesh_input = {"contract": "mesh_input.v1",
                      "surface_manifest_sha256": hashlib.sha256(mesh_surface.read_bytes()).hexdigest()}
        self._write(mesh_case / "mesh_input.json", mesh_input)
        mesh = {
            "contract": "mesh_manifest.v1", "status": "PASS",
            "surface": {"closed": True},
            "mesh": {"mesh_ok": True, "fatal": False},
            "input": {
                "surface_manifest_sha256": hashlib.sha256(mesh_surface.read_bytes()).hexdigest(),
                "mesh_input_sha256": hashlib.sha256((mesh_case / "mesh_input.json").read_bytes()).hexdigest(),
            },
            "tools": {"openfoam_version": "OpenFOAM-v2606"},
        }
        self._write(mesh_case / "mesh_manifest.json", mesh)

        solver = self.projects / "solver" / f"site-{index}"
        self._write(solver / "mesh_manifest.json", mesh)
        solver_mesh = solver / "mesh_manifest.json"
        settings = {"thermal_numerics_profile": "design_limited_second_order_v1"}
        numerics = {
            "profile": "design_limited_second_order_v1",
            "convection_order": 2,
            "laplacian_correction": "limited 0.5",
            "sn_grad_correction": "limited 0.5",
            "required_non_orthogonal_correctors": 2,
        }
        thermal = {
            "contract": "thermal_input.v1",
            "mesh_manifest_sha256": hashlib.sha256(solver_mesh.read_bytes()).hexdigest(),
            "settings": settings,
            "numerics": numerics,
        }
        self._write(solver / "thermal_input.json", thermal)
        system = solver / "system"
        system.mkdir(parents=True, exist_ok=True)
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
            name: hashlib.sha256((system / name).read_bytes()).hexdigest()
            for name in ("controlDict", "fvSchemes", "fvSolution")
        }
        run = {
            "contract": "run_manifest.v1", "engine": "body_fitted_buoyant_urans",
            "status": "PASS", "design_ready": True,
            "effective_settings": settings,
            "effective_numerics": numerics,
            "numerical_quality": {
                "contract": "numerical_quality.v1", "status": "PASS",
                "design_ready": True, "profile": "design_limited_second_order_v1",
                "convection_order": 2, "blockers": [],
            },
            "thermal_progress": {
                "contract": "thermal_progress.v1",
                "flow_through_time_s": 100.0,
                "minimum_flow_through_fraction": 3.0,
                "flow_through_fraction": 3.0,
                "latest_time_s": 300.0,
                "energy_balance": {"available": True, "history_complete": True},
            },
            "input": {
                "thermal_input_sha256": hashlib.sha256(
                    (solver / "thermal_input.json").read_bytes()
                ).hexdigest(),
                "numerical_provenance": {
                    "contract": "thermal_numerics_provenance.v1",
                    "source": "thermal_initial_input",
                    "thermal_input_sha256": hashlib.sha256(
                        (solver / "thermal_input.json").read_bytes()
                    ).hexdigest(),
                    "thermal_restart_input_sha256": None,
                    "effective_settings_sha256": self._json_hash(settings),
                    "effective_numerics_sha256": self._json_hash(numerics),
                    "expected_system": dict(system_hashes),
                    "system": dict(system_hashes),
                },
            },
        }
        self._write(solver / "run_manifest.json", run)
        result_source = solver / "VTK" / "internal.vtu"
        result_source.parent.mkdir(parents=True, exist_ok=True)
        result_source.write_text(f"vtu-{index}", encoding="ascii")
        summary = solver / "results" / "summary.json"
        self._write(summary, {
            "contract": "body_fitted_summary.v1", "time_s": 300.0,
            "cell_count": 4,
            "bounds_m": {"minimum": [0, 0, 0], "maximum": [1, 1, 1]},
            "fields": {"T": {"unit": "K"}, "U": {"unit": "m/s"}},
            "temperature": {"maximum": 295.0, "p95": 294.0},
            "velocity": {"p95_speed": 0.4},
        })
        slices = []
        for axis in "xyz":
            path = solver / "results" / f"{axis}.json"
            self._write(path, {"axis": axis, "sample_count": 0, "samples": []})
            slices.append({
                "axis": axis,
                "path": path.relative_to(solver).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
        result = {
            "contract": "result_manifest.v1", "engine": "body_fitted_openfoam_vtu",
            "time_s": 300.0,
            "source": {"path": result_source.relative_to(solver).as_posix(),
                        "sha256": hashlib.sha256(result_source.read_bytes()).hexdigest()},
            "fields": {"T": {}, "U": {}}, "slices": slices,
            "summary_path": summary.relative_to(solver).as_posix(),
            "summary_sha256": hashlib.sha256(summary.read_bytes()).hexdigest(),
            "mesh_manifest_sha256": hashlib.sha256(solver_mesh.read_bytes()).hexdigest(),
            "run_manifest_sha256": hashlib.sha256((solver / "run_manifest.json").read_bytes()).hexdigest(),
            "thermal_input_sha256": hashlib.sha256(
                (solver / "thermal_input.json").read_bytes()
            ).hexdigest(),
        }
        self._write(solver / "result_manifest.json", result)
        self._write(
            self.projects / "_body_gci" / f"field-site-{index}" / "grid_convergence.json",
            {
                "contract": "grid_convergence.v3", "status": "PASS",
                "design_ready": True,
                "cases": [{
                    "path": str(solver.resolve()),
                    "provenance": {
                        "run_manifest_sha256": hashlib.sha256(
                            (solver / "run_manifest.json").read_bytes()
                        ).hexdigest(),
                        "result_manifest_sha256": hashlib.sha256(
                            (solver / "result_manifest.json").read_bytes()
                        ).hexdigest(),
                        "mesh_manifest_sha256": hashlib.sha256(
                            solver_mesh.read_bytes()
                        ).hexdigest(),
                        "thermal_input_sha256": hashlib.sha256(
                            (solver / "thermal_input.json").read_bytes()
                        ).hexdigest(),
                    },
                }],
            },
        )
        evidence = self.projects / "_release_evidence" / "field_dxf" / f"site-{index}.json"
        return field_acceptance.build_field_acceptance(
            source, geometry_path, surface_dir, mesh_case, solver,
            self.projects, True, evidence,
        )

    def _uat_evidence(self, index, setup_minutes):
        start = datetime(2026, 7, 21 + index, 1, 0, tzinfo=timezone.utc)
        cursor = start
        tasks = []
        for task_index, task_id in enumerate(uat_acceptance.TASKS):
            duration = setup_minutes / 4.0 if task_index < 4 else 1.0
            task_end = cursor + timedelta(minutes=duration)
            tasks.append({
                "id": task_id, "status": "PASS",
                "started_at": cursor.isoformat(),
                "completed_at": task_end.isoformat(),
                "assistance_count": 0, "notes": "observed",
            })
            cursor = task_end
        evidence = self.projects / "_release_evidence" / "uat" / f"user-{index}.json"
        return uat_acceptance.build_uat_session(
            f"participant-{index}", f"observer-{index}", start.isoformat(),
            cursor.isoformat(), tasks, [],
            self.projects / "_release_evidence" / "field_dxf" / "site-1.json",
            self.projects, evidence,
        )

    def _install_evidence(self):
        stable = self.projects / "capability_manifest.json"
        stable_hash = hashlib.sha256(stable.read_bytes()).hexdigest()
        log_root = (self.projects / "_system" /
                    "install_recovery_acceptance" / "logs")
        logs = {
            "clean_install": log_root / "clean_install.log",
            "clean_verify": log_root / "clean_verify.log",
            "dependency_repair": log_root / "dependency_repair.log",
            "repair_verify": log_root / "repair_verify.log",
            "launcher": log_root / "launcher_check.log",
            "openfoam": log_root / "openfoam_check.log",
        }
        contents = {
            "clean_install": "$ python -m pip install -r requirements.txt\nSuccessfully installed ezdxf",
            "clean_verify": "$ python -c import ezdxf, shapely, numpy, matplotlib\nready",
            "dependency_repair": "$ python -m pip install -r requirements.txt\nRequirement already satisfied",
            "repair_verify": "$ python -c import ezdxf, shapely, numpy, matplotlib\nready",
            "launcher": "$ cmd /c run_cfd.bat --check\nMEP CFD Studio launcher: ready",
            "openfoam": "$ cmd /c install_openfoam2606.bat --check --no-pause\nOpenFOAM-v2606-ready",
        }
        for name, path in logs.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(contents[name], encoding="utf-8")
        evidence = self.projects / "_release_evidence" / "install_recovery" / "target-1.json"
        self._write(evidence, {
            "contract": install_acceptance.CONTRACT, "status": "PASS",
            "project_data_preserved": True,
            "scenarios": {"clean_install": "PASS", "dependency_repair": "PASS",
                          "interrupted_job_resume": "PASS"},
            "checks": {
                "launcher": "PASS", "openfoam_v2606": "PASS",
                "current_environment_acceptance": "PASS",
                "stable_file_hashes": "PASS",
                "resume_evidence": {
                    "attempts": 2,
                    "resume_event": {"previous_status": "running",
                                     "checkpoint_times_s": {"coarse": 10.0}},
                    "current_checkpoint_times_s": {"coarse": 20.0},
                },
                "stable_hashes_before": {str(stable.resolve()): stable_hash},
                "stable_hashes_after": {str(stable.resolve()): stable_hash},
            },
            "logs": {name: str(path.resolve()) for name, path in logs.items()},
            "log_hashes": {name: hashlib.sha256(path.read_bytes()).hexdigest()
                           for name, path in logs.items()},
        })
        return evidence

    def _complete_evidence(self):
        benchmark = self.workspace / "cfd_benchmarks" / "g2_thermal" / "geometry.json"
        benchmark.parent.mkdir(parents=True, exist_ok=True)
        benchmark.write_text('{"contract":"geometry.v2"}', encoding="utf-8")
        self._write(self.projects / "capability_manifest.json", {
            "body_fitted_runtime_ready": True,
            "body_fitted_engine_ready": True,
            "openfoam": {"body_fitted_ready": True,
                         "thermal_detailed_ready": True,
                         "compatible_profile": "openfoam-v2606"},
            "freecad": {"ok": True},
            "acceptance": {"ok": True,
                           "openfoam_profile": "openfoam-v2606"},
        })
        self._write_traceable_g2_evidence(benchmark)
        evidence = self.projects / "_release_evidence"
        for index, unit in enumerate((4, 1, 6), 1):
            result = self._field_evidence(index, unit)
            self.assertTrue(result["ok"], result)
        self._install_evidence()
        for index, minutes in enumerate((12, 14, 15), 1):
            result = self._uat_evidence(index, minutes)
            self.assertTrue(result["ok"], result)

    def test_missing_evidence_is_blocked_and_writes_manifest(self):
        result = release_audit.build_release_audit(self.projects)
        self.assertTrue(result["ok"])
        self.assertEqual(result["manifest"]["status"], "BLOCKED")
        self.assertFalse(result["manifest"]["limited_beta_ready"])
        self.assertFalse(result["manifest"]["product_ready"])
        self.assertTrue(Path(result["manifest_path"]).is_file())
        self.assertEqual(len(result["manifest"]["checks"]), 5)

    def test_self_declared_incomplete_g2_pass_is_blocked(self):
        """A PASS label alone cannot substitute for four traceable cases."""
        benchmark = self.workspace / "cfd_benchmarks" / "g2_thermal" / "geometry.json"
        benchmark.parent.mkdir(parents=True, exist_ok=True)
        benchmark.write_text('{"contract":"geometry.v2"}', encoding="utf-8")
        study = self.projects / "_body_gci" / "self-declared"
        self._write(study / "gci_job.json", {
            "input": {
                "geometry_path": str(benchmark.resolve()),
                "geometry_sha256": hashlib.sha256(
                    benchmark.read_bytes()
                ).hexdigest(),
            },
        })
        self._write(study / "grid_convergence.json", {
            "contract": "grid_convergence.v3",
            "status": "PASS",
            "design_ready": True,
            "comparison": {
                "grid_count": 4,
                "minimum_flow_through_fraction": 3.0,
                "maximum_window_drift_pct": 2.0,
            },
            "metrics": [{"status": "PASS"}] * 3,
        })

        check = release_audit._g2_check(self.projects)

        self.assertEqual(check["status"], "BLOCKED")

    def test_traceable_four_case_g2_evidence_passes(self):
        benchmark = self.workspace / "cfd_benchmarks" / "g2_thermal" / "geometry.json"
        benchmark.parent.mkdir(parents=True, exist_ok=True)
        benchmark.write_text('{"contract":"geometry.v2"}', encoding="utf-8")
        self._write_traceable_g2_evidence(benchmark)

        check = release_audit._g2_check(self.projects)

        self.assertEqual(check["status"], "PASS")

    def test_g2_case_artifact_change_invalidates_traceability(self):
        benchmark = self.workspace / "cfd_benchmarks" / "g2_thermal" / "geometry.json"
        benchmark.parent.mkdir(parents=True, exist_ok=True)
        benchmark.write_text('{"contract":"geometry.v2"}', encoding="utf-8")
        study = self._write_traceable_g2_evidence(benchmark)
        (study / "cases" / "level-4" / "thermal_input.json").write_text(
            '{"contract":"thermal_input.v1","changed":true}', encoding="utf-8"
        )

        check = release_audit._g2_check(self.projects)

        self.assertEqual(check["status"], "BLOCKED")

    def test_g2_release_contract_rejects_each_required_claim(self):
        benchmark = self.workspace / "cfd_benchmarks" / "g2_thermal" / "geometry.json"
        benchmark.parent.mkdir(parents=True, exist_ok=True)
        benchmark.write_text('{"contract":"geometry.v2"}', encoding="utf-8")
        study = self._write_traceable_g2_evidence(benchmark)
        evidence_path = study / "grid_convergence.json"
        valid = json.loads(evidence_path.read_text(encoding="utf-8"))

        mutations = {
            "schema version": lambda row: row.update(schema_version=2),
            "engine": lambda row: row.update(engine="unverified"),
            "status": lambda row: row.update(status="WARN"),
            "design ready": lambda row: row.update(design_ready=False),
            "errors": lambda row: row.update(errors=["UNRESOLVED"]),
            "four cases": lambda row: (
                row.update(cases=row["cases"][:3]),
                row["comparison"].update(grid_count=3),
            ),
            "matching case count": lambda row: row["comparison"].update(
                grid_count=5
            ),
            "case provenance": lambda row: row["cases"][0]["provenance"].pop(
                "thermal_input_sha256"
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                row = json.loads(json.dumps(valid))
                mutate(row)
                self._write(evidence_path, row)

                check = release_audit._g2_check(self.projects)

                self.assertEqual(check["status"], "BLOCKED")

    def test_g2_job_contract_must_bind_to_v3_result(self):
        benchmark = self.workspace / "cfd_benchmarks" / "g2_thermal" / "geometry.json"
        benchmark.parent.mkdir(parents=True, exist_ok=True)
        benchmark.write_text('{"contract":"geometry.v2"}', encoding="utf-8")
        study = self._write_traceable_g2_evidence(benchmark)
        job_path = study / "gci_job.json"
        job = json.loads(job_path.read_text(encoding="utf-8"))
        job["input"]["gci_contract"] = "grid_convergence.v2"
        self._write(job_path, job)

        check = release_audit._g2_check(self.projects)

        self.assertEqual(check["status"], "BLOCKED")

    def test_live_g2_job_is_reported_instead_of_old_failed_result(self):
        benchmark = self.workspace / "cfd_benchmarks" / "g2_thermal" / "geometry.json"
        benchmark.parent.mkdir(parents=True, exist_ok=True)
        benchmark.write_text('{"contract":"geometry.v2"}', encoding="utf-8")
        study = self.projects / "_body_gci" / "gci-123456789abc"
        self._write(study / "gci_job.json", {
            "status": "running", "updated_at": "2026-07-22T01:00:00+00:00",
            "input": {
                "geometry_path": str(benchmark.resolve()),
                "geometry_sha256": hashlib.sha256(benchmark.read_bytes()).hexdigest(),
                "gci_contract": "grid_convergence.v3",
                "thermal_settings": {"thermal_minimum_flow_through_fraction": 3.0},
            },
            "levels": [
                {"name": "coarse", "status": "PASS", "flow_through_fraction": 3.0},
                {"name": "fine", "status": "running", "flow_through_fraction": 1.7},
            ],
        })
        self._write(study / "gci_job.lock", {"pid": os.getpid()})

        check = release_audit._g2_check(self.projects)

        self.assertEqual(check["status"], "BLOCKED")
        self.assertIn("fine 계산 실행 중", check["detail"])
        self.assertIn("1.70 / 3.00 FTT", check["detail"])
        self.assertIn("자동 재감사", check["detail"])

    def test_live_g2_audit_labels_bounded_estimate_and_saved_value(self):
        benchmark = self.workspace / "cfd_benchmarks" / "g2_thermal" / "geometry.json"
        benchmark.parent.mkdir(parents=True, exist_ok=True)
        benchmark.write_text('{"contract":"geometry.v2"}', encoding="utf-8")
        study = self.projects / "_body_gci" / "gci-123456789abc"
        self._write(study / "gci_job.json", {
            "status": "running", "updated_at": "2026-07-22T01:00:00+00:00",
            "input": {
                "geometry_path": str(benchmark.resolve()),
                "geometry_sha256": hashlib.sha256(benchmark.read_bytes()).hexdigest(),
                "gci_contract": "grid_convergence.v3",
                "thermal_settings": {"thermal_minimum_flow_through_fraction": 3.0},
            },
            "levels": [
                {"name": "fine", "status": "running",
                 "flow_through_fraction": 1.7},
            ],
        })
        self._write(study / "gci_job.lock", {"pid": os.getpid()})
        with mock.patch.object(
            release_audit.cfd_gci_job, "bounded_live_progress",
            return_value={
                "estimated_flow_through_fraction": 1.9,
                "next_checkpoint_time_s": 200.0,
                "estimated_remaining_runtime_seconds": 3660.0,
            },
        ):
            check = release_audit._g2_check(self.projects)

        self.assertIn("예상 1.90 / 3.00 FTT", check["detail"])
        self.assertIn("저장 1.70", check["detail"])
        self.assertIn("다음 200.000초", check["detail"])
        self.assertIn("남은 실제시간 약 1시간 1분", check["detail"])
        self.assertEqual(release_audit._duration_ko(0), "1분")
        self.assertEqual(release_audit._duration_ko(-1), "")

    def test_stale_g2_lock_is_not_reported_as_running(self):
        benchmark = self.workspace / "cfd_benchmarks" / "g2_thermal" / "geometry.json"
        benchmark.parent.mkdir(parents=True, exist_ok=True)
        benchmark.write_text('{"contract":"geometry.v2"}', encoding="utf-8")
        study = self.projects / "_body_gci" / "gci-123456789abc"
        self._write(study / "gci_job.json", {
            "status": "running", "updated_at": "2026-07-22T01:00:00+00:00",
            "input": {
                "geometry_path": str(benchmark.resolve()),
                "geometry_sha256": hashlib.sha256(benchmark.read_bytes()).hexdigest(),
                "gci_contract": "grid_convergence.v3",
            },
            "levels": [{"name": "fine", "status": "running",
                        "flow_through_fraction": 1.7}],
        })
        self._write(study / "gci_job.lock", {"pid": 2_147_483_647})

        check = release_audit._g2_check(self.projects)

        self.assertNotIn("실행 중", check["detail"])

    def test_complete_actual_evidence_passes_product_gate(self):
        self._complete_evidence()
        result = release_audit.build_release_audit(self.projects)
        self.assertEqual(result["manifest"]["status"], "PASS")
        self.assertTrue(result["manifest"]["limited_beta_ready"])
        self.assertTrue(result["manifest"]["product_ready"])
        self.assertTrue(all(item["status"] == "PASS"
                            for item in result["manifest"]["checks"]))

    def test_bundled_sample_name_cannot_count_as_field_evidence(self):
        self._complete_evidence()
        field = self.projects / "_release_evidence" / "field_dxf" / "site-3.json"
        field.unlink()
        result = self._field_evidence(3, 6, "sample_plan.dxf")
        self.assertFalse(result["ok"])
        result = release_audit.build_release_audit(self.projects)
        check = next(item for item in result["manifest"]["checks"]
                     if item["id"] == "field_dxf")
        self.assertEqual(check["status"], "BLOCKED")
        self.assertIn("2/3", check["detail"])

    def test_field_acceptance_rechecks_the_authoritative_body_result_gate(self):
        rejected = {
            "status": "NOT_EVALUATED",
            "design_ready": False,
            "citation_status": "NOT_EVALUATED",
            "citable": False,
            "blockers": ["numerical_quality"],
        }

        with mock.patch(
            "cfd_result_gate.evaluate_body_fitted_case", return_value=rejected
        ) as evaluate_gate:
            result = self._field_evidence(1, 4)

        self.assertFalse(result["ok"])
        self.assertIn(
            "RESULT_CITATION_GATE:numerical_quality",
            result["manifest"]["errors"],
        )
        evaluate_gate.assert_called_once()

    def test_renamed_bundled_sample_content_is_rejected(self):
        renamed = self.projects / "imports" / "actual-floor-a.dxf"
        renamed.parent.mkdir(parents=True)
        renamed.write_bytes((self.repo / "sample_plan.dxf").read_bytes())

        self.assertTrue(field_acceptance.is_bundled_sample_drawing(
            renamed, self.projects
        ))

    def test_three_valid_drawings_without_unit_diversity_remain_blocked(self):
        self._complete_evidence()
        field_root = self.projects / "_release_evidence" / "field_dxf"
        for path in field_root.glob("*.json"):
            path.unlink()
        for index in range(1, 4):
            result = self._field_evidence(index, 4)
            self.assertTrue(result["ok"], result)

        check = release_audit._field_check(
            self.projects, self.projects / "_release_evidence"
        )

        self.assertEqual(check["status"], "BLOCKED")
        self.assertIn("3/3", check["detail"])
        self.assertIn("단위 1/2", check["detail"])
        self.assertIn("더 필요한 차이: 단위", check["detail"])

    def test_self_declared_gate_edit_is_recomputed_and_rejected(self):
        self._complete_evidence()
        field = self.projects / "_release_evidence" / "field_dxf" / "site-3.json"
        row = json.loads(field.read_text(encoding="utf-8"))
        row["variation"]["signature"] = "0" * 64
        field.write_text(json.dumps(row), encoding="utf-8")
        result = release_audit.build_release_audit(self.projects)
        check = next(item for item in result["manifest"]["checks"]
                     if item["id"] == "field_dxf")
        self.assertEqual(check["status"], "BLOCKED")
        self.assertIn("2/3", check["detail"])

    def test_changed_pipeline_artifact_invalidates_evidence(self):
        self._complete_evidence()
        run = self.projects / "solver" / "site-3" / "run_manifest.json"
        row = json.loads(run.read_text(encoding="utf-8"))
        row["status"] = "FAIL"
        run.write_text(json.dumps(row), encoding="utf-8")
        result = release_audit.build_release_audit(self.projects)
        check = next(item for item in result["manifest"]["checks"]
                     if item["id"] == "field_dxf")
        self.assertEqual(check["status"], "BLOCKED")

    def test_edited_uat_summary_is_recomputed_and_rejected(self):
        self._complete_evidence()
        path = self.projects / "_release_evidence" / "uat" / "user-3.json"
        row = json.loads(path.read_text(encoding="utf-8"))
        row["setup_minutes"] = 1.0
        path.write_text(json.dumps(row), encoding="utf-8")
        result = release_audit.build_release_audit(self.projects)
        check = next(item for item in result["manifest"]["checks"]
                     if item["id"] == "mechanical_uat")
        self.assertEqual(check["status"], "BLOCKED")
        self.assertIn("무효 1건", check["detail"])

    def test_uat_release_thresholds_are_independently_enforced(self):
        evidence_root = self.projects / "_release_evidence"
        uat_root = evidence_root / "uat"
        uat_root.mkdir(parents=True)
        paths = []
        for index in range(3):
            path = uat_root / f"threshold-{index}.json"
            self._write(path, {})
            paths.append(path)

        def run(rows):
            by_name = {path.name: row for path, row in zip(paths, rows)}
            with mock.patch.object(
                release_audit.uat_acceptance, "validate_evidence",
                side_effect=lambda path, root: {
                    "ok": True, "manifest": by_name[Path(path).name]
                },
            ):
                return release_audit._uat_check(evidence_root)

        base = [{
            "participant_id": f"mechanical-{index}",
            "first_project_completed": True,
            "setup_minutes": 10.0,
            "fatal_usability_errors": 0,
        } for index in range(3)]

        slow = [dict(row, setup_minutes=value)
                for row, value in zip(base, (14.0, 16.0, 18.0))]
        check = run(slow)
        self.assertEqual(check["status"], "BLOCKED")
        self.assertIn("16.0분", check["detail"])

        incomplete = [dict(row) for row in base]
        incomplete[0]["first_project_completed"] = False
        check = run(incomplete)
        self.assertEqual(check["status"], "BLOCKED")
        self.assertIn("67%", check["detail"])

        fatal = [dict(row) for row in base]
        fatal[1]["fatal_usability_errors"] = 1
        check = run(fatal)
        self.assertEqual(check["status"], "BLOCKED")
        self.assertIn("치명 오류 1건", check["detail"])

        duplicate = [dict(row) for row in base]
        duplicate[2]["participant_id"] = duplicate[1]["participant_id"].upper()
        check = run(duplicate)
        self.assertEqual(check["status"], "BLOCKED")
        self.assertIn("2명", check["detail"])
        self.assertIn("무효 1건", check["detail"])

    def test_acceptance_from_another_openfoam_profile_is_stale(self):
        self._complete_evidence()
        path = self.projects / "capability_manifest.json"
        row = json.loads(path.read_text(encoding="utf-8"))
        row["acceptance"]["openfoam_profile"] = "openfoam-v1912"
        path.write_text(json.dumps(row), encoding="utf-8")
        result = release_audit.build_release_audit(self.projects)
        check = next(item for item in result["manifest"]["checks"]
                     if item["id"] == "environment")
        self.assertEqual(check["status"], "BLOCKED")
        self.assertIn("격리 환경 수용시험", check["detail"])

    def test_schema_contract_is_available(self):
        schema = json.loads(
            (self.repo / "release_readiness.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(schema["properties"]["contract"]["const"],
                         release_audit.RELEASE_CONTRACT)
        field_schema = json.loads(
            (self.repo / "field_dxf_acceptance.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(field_schema["properties"]["contract"]["const"],
                         field_acceptance.CONTRACT)
        uat_schema = json.loads(
            (self.repo / "mechanical_user_uat.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(uat_schema["properties"]["contract"]["const"],
                         uat_acceptance.CONTRACT)


if __name__ == "__main__":
    unittest.main()
