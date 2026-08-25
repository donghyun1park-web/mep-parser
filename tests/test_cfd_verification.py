import copy
import hashlib
import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator

import cfd_numerics
import cfd_physics
import cfd_verification


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _ref(root: Path, path: Path) -> dict:
    return {"path": path.relative_to(root).as_posix(), "sha256": _sha(path)}


def _solver_log(duration_s=60.0, delta_t_s=0.02) -> str:
    count = round(duration_s / delta_t_s)
    rows = [f"Time = {index * delta_t_s:.12g}" for index in range(1, count + 1)]
    rows.extend([
        "Courant Number mean: 0.04 max: 0.25",
        "time step continuity errors : sum local = 1e-8, global = 5e-8, cumulative = 1e-7",
        "End",
    ])
    return "\n".join(rows) + "\n"


def _sync_refs(root: Path, manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    paths = {
        name: root / row["path"] for name, row in manifest["artifacts"].items()
    }
    thermal = json.loads(paths["thermal_input"].read_text(encoding="utf-8"))
    thermal["mesh_manifest_sha256"] = _sha(paths["mesh_manifest"])
    _write_json(paths["thermal_input"], thermal)
    physical = cfd_physics.profile_free_thermal_input_snapshot(
        thermal
    )
    _write_json(paths["thermal_physical_input"], physical)
    semantic = cfd_verification._heat_source_semantic(
        thermal,
        physical,
        paths["fv_options"].read_text(encoding="utf-8"),
        paths["topo_set_dict"].read_text(encoding="utf-8"),
        _sha(paths["mesh_manifest"]),
        thermal["settings"]["air_density_kg_m3"],
        thermal["settings"]["air_specific_heat_j_kg_k"],
    )
    run = json.loads(paths["run_manifest"].read_text(encoding="utf-8"))
    existing_input = run.get("input") if isinstance(run.get("input"), dict) else {}
    semantic_hash = (
        semantic[1] if semantic is not None
        else existing_input.get("heat_source_semantic_sha256", "0" * 64)
    )
    run["effective_settings"] = thermal["settings"]
    run["effective_numerics"] = thermal.get("numerics", {})
    run["input"] = {
        "thermal_input_sha256": _sha(paths["thermal_input"]),
        "thermal_physical_input_sha256": _sha(paths["thermal_physical_input"]),
        "fv_options_sha256": _sha(paths["fv_options"]),
        "topo_set_dict_sha256": _sha(paths["topo_set_dict"]),
        "heat_source_semantic_sha256": semantic_hash,
        "solver_log_sha256": _sha(paths["solver_log"]),
        "system": {
            "controlDict": _sha(paths["control_dict"]),
            "fvSchemes": _sha(paths["fv_schemes"]),
            "fvSolution": _sha(paths["fv_solution"]),
        },
        "numerical_provenance": {
            "contract": "thermal_numerics_provenance.v1",
            "source": "thermal_initial_input",
            "thermal_input_sha256": _sha(paths["thermal_input"]),
            "thermal_restart_input_sha256": None,
            "effective_settings_sha256": _canonical(run["effective_settings"]),
            "effective_numerics_sha256": _canonical(run["effective_numerics"]),
            "expected_system": {
                "controlDict": _sha(paths["control_dict"]),
                "fvSchemes": _sha(paths["fv_schemes"]),
                "fvSolution": _sha(paths["fv_solution"]),
            },
            "system": {
                "controlDict": _sha(paths["control_dict"]),
                "fvSchemes": _sha(paths["fv_schemes"]),
                "fvSolution": _sha(paths["fv_solution"]),
            },
        },
    }
    _write_json(paths["run_manifest"], run)
    result = json.loads(paths["result_manifest"].read_text(encoding="utf-8"))
    result["thermal_input_sha256"] = _sha(paths["thermal_input"])
    result["run_manifest_sha256"] = _sha(paths["run_manifest"])
    result["source"] = {
        "path": manifest["artifacts"]["cell_data"]["path"],
        "sha256": _sha(paths["cell_data"]),
        "format": "heat_box_cells.v1",
    }
    _write_json(paths["result_manifest"], result)
    for name, path in paths.items():
        manifest["artifacts"][name]["sha256"] = _sha(path)
    _write_json(manifest_path, manifest)
    return manifest


def _refresh_run_result_refs(root: Path, manifest_path: Path) -> dict:
    """Refresh only the outer hash chain after an intentional run mutation."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    paths = {
        name: root / row["path"] for name, row in manifest["artifacts"].items()
    }
    result = json.loads(paths["result_manifest"].read_text(encoding="utf-8"))
    result["run_manifest_sha256"] = _sha(paths["run_manifest"])
    _write_json(paths["result_manifest"], result)
    for name, path in paths.items():
        manifest["artifacts"][name]["sha256"] = _sha(path)
    _write_json(manifest_path, manifest)
    return manifest


def _heat_tree(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "projects"
    case = root / "_working_validation" / "heat-box-v1" / "case"
    manifest_path = root / "_working_validation" / "heat-box-v1" / "verification_manifest.json"
    geometry = case / "geometry.json"
    surface_manifest = case / "surface_manifest.json"
    mesh_manifest = case / "mesh_manifest.json"
    mesh_input = case / "mesh_input.json"
    thermal_input = case / "thermal_input.json"
    thermal_physical_input = case / "thermal_input.physical.v1.json"
    control = case / "system" / "controlDict"
    schemes = case / "system" / "fvSchemes"
    solution = case / "system" / "fvSolution"
    topo_set = case / "system" / "topoSetDict"
    fv_options = case / "constant" / "fvOptions"
    u_field = case / "0" / "U"
    t_field = case / "0" / "T"
    p_rgh_field = case / "0" / "p_rgh"
    identity = case / "solver_identity.json"
    solver_log = case / "log.buoyantBoussinesqPimpleFoam"
    run_manifest = case / "run_manifest.json"
    result_manifest = case / "result_manifest.json"
    cells = case / "heat_box_cells.json"

    rho, cp, volume, power, duration = 1.204, 1006.0, 8.0, 800.0, 60.0
    analytic_delta = power * duration / (rho * cp * volume)
    _write_json(geometry, {"contract": "geometry.v2", "box_m": [2.0, 2.0, 2.0]})
    _write_json(surface_manifest, {
        "contract": "surface_manifest.v1",
        "regions": [
            {"name": "wall", "role": "wall"},
            {"name": "heater", "role": "heat_source"},
        ],
    })
    _write_json(mesh_manifest, {
        "contract": "mesh_manifest.v1", "status": "PASS", "occ_volume_m3": volume,
        "mesh": {"cell_size_m": 0.125},
        "patches": [
            {"name": "wall", "mesh_patch_name": "box_wall", "role": "wall"},
            {"name": "heater", "mesh_patch_name": "box_heater", "role": "heat_source"},
        ],
    })
    _write_json(mesh_input, {"contract": "mesh_input.v1", "cell_size_m": 0.125})
    _write_json(thermal_input, {
        "contract": "thermal_input.v1",
        "validation_scope": "single_pc_adiabatic_heat_box",
        "engine": "body_fitted_buoyant_urans",
        "mesh_manifest_sha256": "0" * 64,
        "airflow": {"supply_cmh": 0.0, "exhaust_cmh": 0.0},
        "terminals": [],
        "wall_patches": ["box_wall", "box_heater"],
        "settings": {
            "air_density_kg_m3": rho,
            "air_specific_heat_j_kg_k": cp,
            "initial_temperature_k": 293.15,
            "reference_temperature_k": 293.15,
            "thermal_expansion_coefficient_1_k": 0.00341,
            "thermal_duration_s": duration,
            "thermal_initial_delta_t_s": 0.02,
            "thermal_max_delta_t_s": 0.02,
            "thermal_max_co": 1.0,
            "thermal_write_interval_s": 2.0,
            "linear_solver_relative_tolerance": 0.05,
            "thermal_scalar_relative_tolerance": 0.05,
            "thermal_outer_correctors": 2,
            "thermal_pressure_correctors": 2,
            "thermal_non_orthogonal_correctors": 0,
        },
        "heat": {"applied_convective_power_w": power},
        "heat_sources": [{
            "name": "heater", "source_id": "HEAT-1",
            "mesh_patch_name": "box_heater",
            "convective_power_w": power,
            "applied_convective_power_w": power,
        }],
        "assumptions": {"walls": "adiabatic_screening"},
        "condition_matrix": {
            "flow_scale": 1.0, "gravity_scale": 1.0, "heat_scale": 1.0,
        },
        "initialisation": {
            "mode": "zero_flow", "pressure_mapping": "none",
            "boussinesq_preconditioning_iterations": 0,
        },
    })
    _write_json(thermal_physical_input, {})
    thermal_payload = json.loads(thermal_input.read_text(encoding="utf-8"))
    mesh_payload = json.loads(mesh_manifest.read_text(encoding="utf-8"))
    thermal_payload["numerics"] = cfd_numerics.thermal_numerics_contract(
        mesh_payload, thermal_payload["settings"]
    )
    _write_json(thermal_input, thermal_payload)
    _write_text(
        control,
        cfd_physics._thermal_control_dict(
            thermal_payload["settings"], "single_pc_adiabatic_heat_box"
        ),
    )
    _write_text(
        schemes,
        cfd_physics._thermal_fv_schemes(thermal_payload["numerics"]),
    )
    _write_text(
        solution,
        cfd_physics._thermal_fv_solution(
            thermal_payload["settings"], thermal_payload["numerics"]
        ),
    )
    _write_text(
        fv_options,
        cfd_physics._thermal_fv_options(
            thermal_payload["heat_sources"], thermal_payload["settings"]
        ),
    )
    _write_text(
        topo_set,
        cfd_physics._thermal_toposet_dict(
            thermal_payload["heat_sources"]
        ),
    )
    _write_text(u_field, """boundaryField {
box_wall { type fixedValue; value uniform (0 0 0); }
box_heater { type fixedValue; value uniform (0 0 0); }
}
""")
    _write_text(t_field, """boundaryField {
box_wall { type zeroGradient; }
box_heater { type zeroGradient; }
}
""")
    _write_text(p_rgh_field, """boundaryField {
box_wall { type fixedFluxPressure; value uniform 0; }
box_heater { type fixedFluxPressure; value uniform 0; }
}
""")
    _write_json(identity, {
        "contract": "solver_identity.v1",
        "executable": "buoyantBoussinesqPimpleFoam",
        "version": "OpenFOAM-v2606",
        "executable_sha256": "a" * 64,
    })
    _write_text(solver_log, _solver_log())
    _write_json(cells, {
        "contract": "heat_box_cells.v1",
        "initial_time_s": 0.0,
        "final_time_s": duration,
        "initial_cells": [
            {"id": "c1", "volume_m3": 2.0, "temperature_k": 293.15},
            {"id": "c2", "volume_m3": 6.0, "temperature_k": 293.15},
        ],
        "final_cells": [
            {"id": "c1", "volume_m3": 2.0,
             "temperature_k": 293.15 + analytic_delta * 0.5},
            {"id": "c2", "volume_m3": 6.0,
             "temperature_k": 293.15 + analytic_delta * (7.0 / 6.0)},
        ],
        "boundary_phi_m3_s": [5e-10, -5e-10],
    })
    _write_json(run_manifest, {
        "schema_version": 1,
        "contract": "run_manifest.v1", "engine": "body_fitted_buoyant_urans",
        "created_at": "2026-08-25T00:00:00Z",
        "status": "PASS", "design_ready": False,
        "errors": [], "warnings": [],
        "solver": {"ended": True, "fatal": False, "end_time": duration},
        "airflow": {"supply_cmh": 0.0, "exhaust_cmh": 0.0},
        "terminals": [],
        "y_plus": {
            "available": True, "time": duration, "area_ratio_in_target": 1.0,
            "wall_treatment_acceptable_area_ratio": 1.0,
            "minimum": 0.0, "maximum": 0.0, "area_weighted_average": 0.0,
            "patches": [],
        },
        "effective_settings": {}, "effective_numerics": {},
        "numerical_quality": {
            "contract": "numerical_quality.v1", "status": "NOT_EVALUATED",
            "design_ready": False, "profile": "heat_box_exact_v1",
            "convection_order": 1, "blockers": [],
        },
        "input": {},
    })
    _write_json(result_manifest, {
        "schema_version": 1, "contract": "result_manifest.v1",
        "engine": "body_fitted_openfoam_vtu",
        "created_at": "2026-08-25T00:00:01Z", "time_s": duration,
        "field_location": "cell", "fields": {"T": {}, "U": {}},
        "summary_path": "summary.json", "summary_sha256": "d" * 64,
        "slices": [
            {"axis": "x", "path": "slice-x.json", "sha256": "e" * 64},
            {"axis": "y", "path": "slice-y.json", "sha256": "f" * 64},
            {"axis": "z", "path": "slice-z.json", "sha256": "1" * 64}
        ],
        "thermal_input_sha256": "0" * 64, "run_manifest_sha256": "0" * 64,
        "source": {},
    })
    artifacts = {
        "geometry": _ref(root, geometry),
        "surface_manifest": _ref(root, surface_manifest),
        "mesh_manifest": _ref(root, mesh_manifest),
        "mesh_input": _ref(root, mesh_input),
        "thermal_input": _ref(root, thermal_input),
        "thermal_physical_input": _ref(root, thermal_physical_input),
        "control_dict": _ref(root, control),
        "fv_schemes": _ref(root, schemes),
        "fv_solution": _ref(root, solution),
        "fv_options": _ref(root, fv_options),
        "topo_set_dict": _ref(root, topo_set),
        "u_field": _ref(root, u_field),
        "t_field": _ref(root, t_field),
        "p_rgh_field": _ref(root, p_rgh_field),
        "solver_identity": _ref(root, identity),
        "solver_log": _ref(root, solver_log),
        "run_manifest": _ref(root, run_manifest),
        "result_manifest": _ref(root, result_manifest),
        "cell_data": _ref(root, cells),
    }
    _write_json(manifest_path, {
        "contract": "verification_manifest.v1",
        "status": "PASS",
        "validation_scope": "single_pc_adiabatic_heat_box",
        "case_id": "heat-box-v1",
        "case_path": case.relative_to(root).as_posix(),
        "artifacts": artifacts,
        "claimed_metrics": {
            "analytic_delta_temperature_k": analytic_delta,
            "simulated_delta_temperature_k": analytic_delta,
            "mean_temperature_relative_error": 0.0,
            "storage_energy_closure_ratio": 1.0,
            "peak_courant": 0.25,
            "max_global_continuity": 5e-8,
            "net_boundary_volume_flux_m3_s": 0.0,
            "boussinesq_beta_delta": 0.02,
        },
        "limitations": ["not_design_citable", "not_release_ready"],
    })
    _sync_refs(root, manifest_path)
    return root, manifest_path


def test_heat_box_schema_is_closed_and_validates_producer_contract(tmp_path):
    root, manifest_path = _heat_tree(tmp_path)
    schema = json.loads(
        Path(cfd_verification.__file__).with_name(
            "verification_manifest.v1.schema.json"
        ).read_text(encoding="utf-8")
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(manifest)) == []
    manifest["unexpected"] = True
    assert list(Draft202012Validator(schema).iter_errors(manifest))
    assert root.is_dir()


def test_heat_box_recomputes_volume_weighted_energy_and_is_stable(tmp_path):
    root, manifest_path = _heat_tree(tmp_path)
    first = cfd_verification.validate_heat_box_manifest(manifest_path, root)
    second = cfd_verification.validate_heat_box_manifest(manifest_path, root)
    assert first == second
    assert first["status"] == "PASS"
    assert first["blockers"] == []
    assert first["metrics"]["mean_temperature_relative_error"] <= 1e-9
    assert abs(first["metrics"]["storage_energy_closure_ratio"] - 1.0) <= 1e-9
    assert set(first["evidence_sha256"]) == {
        manifest_path.relative_to(root).as_posix(),
        *(row["path"] for row in json.loads(
            manifest_path.read_text(encoding="utf-8")
        )["artifacts"].values()),
    }


def test_heat_box_rejects_changed_power_volume_time_and_cells(tmp_path):
    mutations = ("power", "volume", "time", "cells")
    for mutation in mutations:
        root, manifest_path = _heat_tree(tmp_path / mutation)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        paths = {name: root / ref["path"] for name, ref in manifest["artifacts"].items()}
        if mutation == "power":
            thermal = json.loads(paths["thermal_input"].read_text(encoding="utf-8"))
            thermal["heat"]["applied_convective_power_w"] = 900.0
            _write_json(paths["thermal_input"], thermal)
        elif mutation == "volume":
            mesh = json.loads(paths["mesh_manifest"].read_text(encoding="utf-8"))
            mesh["occ_volume_m3"] = 9.0
            _write_json(paths["mesh_manifest"], mesh)
        elif mutation == "time":
            result = json.loads(paths["result_manifest"].read_text(encoding="utf-8"))
            result["time_s"] = 59.0
            _write_json(paths["result_manifest"], result)
        else:
            cells = json.loads(paths["cell_data"].read_text(encoding="utf-8"))
            cells["final_cells"][0]["temperature_k"] += 1.0
            _write_json(paths["cell_data"], cells)
        _sync_refs(root, manifest_path)
        result = cfd_verification.validate_heat_box_manifest(manifest_path, root)
        assert result["status"] != "PASS", (mutation, result)


def test_heat_box_rejects_adaptive_controller_and_missing_pressure_reference(tmp_path):
    for mutation, expected in (
        ("adaptive", "HEAT_BOX_FIXED_DT_REQUIRED"),
        ("pressure", "HEAT_BOX_PRESSURE_REFERENCE_MISSING"),
    ):
        root, manifest_path = _heat_tree(tmp_path / mutation)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        paths = {name: root / ref["path"] for name, ref in manifest["artifacts"].items()}
        if mutation == "adaptive":
            _write_text(paths["control_dict"], "deltaT 0.02;\nadjustTimeStep yes;\nmaxCo 1;\n")
        else:
            _write_text(paths["fv_solution"], "PIMPLE { nCorrectors 2; }\n")
        _sync_refs(root, manifest_path)
        result = cfd_verification.validate_heat_box_manifest(manifest_path, root)
        assert expected in result["blockers"]


def test_heat_box_rejects_traversal_reparse_and_output_alias(tmp_path, monkeypatch):
    root, manifest_path = _heat_tree(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"]["geometry"]["path"] = "../outside.json"
    _write_json(manifest_path, manifest)
    result = cfd_verification.validate_heat_box_manifest(manifest_path, root)
    assert "HEAT_BOX_ARTIFACT_PATH_INVALID" in result["blockers"]

    root, manifest_path = _heat_tree(tmp_path / "alias")
    result = cfd_verification.validate_heat_box_manifest(
        manifest_path, root, evaluator_output_path=manifest_path
    )
    assert "OUTPUT_ALIAS" in result["blockers"]

    root, manifest_path = _heat_tree(tmp_path / "reparse")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    geometry = root / manifest["artifacts"]["geometry"]["path"]
    original = cfd_verification._is_reparse
    monkeypatch.setattr(
        cfd_verification,
        "_is_reparse",
        lambda path: Path(path).absolute() == geometry.absolute() or original(path),
    )
    result = cfd_verification.validate_heat_box_manifest(manifest_path, root)
    assert "HEAT_BOX_ARTIFACT_PATH_INVALID" in result["blockers"]


def test_heat_box_ignores_forged_claimed_pass_metrics(tmp_path):
    root, manifest_path = _heat_tree(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["claimed_metrics"] = {
        key: 0.0 for key in manifest["claimed_metrics"]
    }
    manifest["claimed_metrics"]["storage_energy_closure_ratio"] = 1.0
    _write_json(manifest_path, manifest)
    result = cfd_verification.validate_heat_box_manifest(manifest_path, root)
    assert result["status"] == "PASS"
    assert result["metrics"]["analytic_delta_temperature_k"] > 0


def test_heat_box_rejects_post_load_hash_drift(tmp_path, monkeypatch):
    root, manifest_path = _heat_tree(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    target = (root / manifest["artifacts"]["cell_data"]["path"]).resolve()
    original = cfd_verification._snapshot_file
    changed = False

    def drifting_snapshot(path):
        nonlocal changed
        value = original(path)
        if Path(path).resolve() == target and not changed:
            changed = True
            target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        return value

    monkeypatch.setattr(cfd_verification, "_snapshot_file", drifting_snapshot)
    result = cfd_verification.validate_heat_box_manifest(manifest_path, root)
    assert "ARTIFACT_CHANGED_DURING_VALIDATION" in result["blockers"]


def test_heat_box_requires_the_fixed_authoritative_manifest(tmp_path):
    root, manifest_path = _heat_tree(tmp_path)
    payload = manifest_path.read_text(encoding="utf-8")
    for relative in (
        "_working_validation/heat-box-v1/copied.json",
        "_working_validation/heat-box-v1/latest/verification_manifest.json",
    ):
        copied = root / relative
        _write_text(copied, payload)
        result = cfd_verification.validate_heat_box_manifest(copied, root)
        assert "HEAT_BOX_MANIFEST_PATH_INVALID" in result["blockers"]


def test_heat_box_rejects_failed_incomplete_or_unbound_solver_run(tmp_path):
    for mutation in ("failed", "incomplete", "log_unbound"):
        root, manifest_path = _heat_tree(tmp_path / mutation)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        run_path = root / manifest["artifacts"]["run_manifest"]["path"]
        run = json.loads(run_path.read_text(encoding="utf-8"))
        if mutation == "failed":
            run["status"] = "FAIL"
        elif mutation == "incomplete":
            run["solver"]["ended"] = False
        else:
            run["input"]["solver_log_sha256"] = "9" * 64
        _write_json(run_path, run)
        _refresh_run_result_refs(root, manifest_path)
        result = cfd_verification.validate_heat_box_manifest(manifest_path, root)
        assert "HEAT_BOX_RUN_INCOMPLETE_OR_UNBOUND" in result["blockers"]


def test_heat_box_rejects_unbound_effective_numerics(tmp_path):
    root, manifest_path = _heat_tree(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    run_path = root / manifest["artifacts"]["run_manifest"]["path"]
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["input"]["numerical_provenance"]["effective_numerics_sha256"] = "9" * 64
    _write_json(run_path, run)
    _refresh_run_result_refs(root, manifest_path)
    result = cfd_verification.validate_heat_box_manifest(manifest_path, root)
    assert "HEAT_BOX_RUN_CROSS_REFERENCE_INVALID" in result["blockers"]


def test_heat_box_proves_closed_patches_and_adiabatic_fields(tmp_path):
    root, manifest_path = _heat_tree(tmp_path / "surface")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    surface_path = root / manifest["artifacts"]["surface_manifest"]["path"]
    surface = json.loads(surface_path.read_text(encoding="utf-8"))
    surface["regions"][0]["role"] = "supply"
    _write_json(surface_path, surface)
    _sync_refs(root, manifest_path)
    result = cfd_verification.validate_heat_box_manifest(manifest_path, root)
    assert "HEAT_BOX_BOUNDARY_NOT_CLOSED" in result["blockers"]

    root, manifest_path = _heat_tree(tmp_path / "field")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    u_path = root / manifest["artifacts"]["u_field"]["path"]
    _write_text(u_path, u_path.read_text(encoding="utf-8").replace(
        "value uniform (0 0 0)", "value uniform (1 0 0)", 1
    ))
    _sync_refs(root, manifest_path)
    result = cfd_verification.validate_heat_box_manifest(manifest_path, root)
    assert "HEAT_BOX_BOUNDARY_CONDITION_INVALID" in result["blockers"]


def test_heat_box_rejects_large_canceling_boundary_fluxes(tmp_path):
    root, manifest_path = _heat_tree(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cells_path = root / manifest["artifacts"]["cell_data"]["path"]
    cells = json.loads(cells_path.read_text(encoding="utf-8"))
    cells["boundary_phi_m3_s"] = [1e-4, -1e-4]
    _write_json(cells_path, cells)
    _sync_refs(root, manifest_path)
    result = cfd_verification.validate_heat_box_manifest(manifest_path, root)
    assert "HEAT_BOX_NET_BOUNDARY_FLUX_LIMIT" in result["blockers"]


def test_heat_box_rejects_reparse_output_leaf(tmp_path, monkeypatch):
    root, manifest_path = _heat_tree(tmp_path)
    output = root / "_working_validation" / "reports" / "heat-box.json"
    _write_text(output, "{}")
    original = cfd_verification._is_reparse
    monkeypatch.setattr(
        cfd_verification,
        "_is_reparse",
        lambda path: Path(path).absolute() == output.absolute() or original(path),
    )
    result = cfd_verification.validate_heat_box_manifest(
        manifest_path, root, evaluator_output_path=output
    )
    assert "OUTPUT_PATH_INVALID" in result["blockers"]


def test_heat_box_rejects_duplicate_json_keys(tmp_path):
    root, manifest_path = _heat_tree(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    geometry_path = root / manifest["artifacts"]["geometry"]["path"]
    _write_text(
        geometry_path,
        '{"contract":"geometry.v2","contract":"geometry.v2",'
        '"box_m":[2.0,2.0,2.0]}',
    )
    manifest["artifacts"]["geometry"]["sha256"] = _sha(geometry_path)
    _write_json(manifest_path, manifest)
    result = cfd_verification.validate_heat_box_manifest(manifest_path, root)
    assert "HEAT_BOX_ARTIFACT_MALFORMED" in result["blockers"]


def test_heat_box_rejects_same_content_atomic_replacement(tmp_path, monkeypatch):
    root, manifest_path = _heat_tree(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    target = (root / manifest["artifacts"]["cell_data"]["path"]).resolve()
    original = cfd_verification._snapshot_file
    replaced = False

    def replacing_snapshot(path):
        nonlocal replaced
        value = original(path)
        if Path(path).resolve() == target and not replaced:
            replaced = True
            replacement = target.with_name("replacement.json")
            replacement.write_bytes(target.read_bytes())
            replacement.replace(target)
        return value

    monkeypatch.setattr(cfd_verification, "_snapshot_file", replacing_snapshot)
    result = cfd_verification.validate_heat_box_manifest(manifest_path, root)
    assert "ARTIFACT_CHANGED_DURING_VALIDATION" in result["blockers"]


def test_heat_box_binds_production_fv_options_and_physical_heat_input(tmp_path):
    root, manifest_path = _heat_tree(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    options_path = root / manifest["artifacts"]["fv_options"]["path"]
    expected_source = 800.0 / (1.204 * 1006.0)
    _write_text(
        options_path,
        options_path.read_text(encoding="utf-8").replace(
            f"T ({expected_source:.12g} 0)",
            f"T ({expected_source / 2.0:.12g} 0)",
        ),
    )
    _sync_refs(root, manifest_path)
    result = cfd_verification.validate_heat_box_manifest(manifest_path, root)
    assert "HEAT_BOX_HEAT_SOURCE_INVALID" in result["blockers"]


def test_heat_box_rejects_extra_source_scaled_physics_and_topology_drift(tmp_path):
    for mutation in ("extra_source", "heat_scale", "topology"):
        root, manifest_path = _heat_tree(tmp_path / mutation)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        paths = {
            name: root / ref["path"] for name, ref in manifest["artifacts"].items()
        }
        if mutation == "extra_source":
            path = paths["fv_options"]
            _write_text(
                path,
                path.read_text(encoding="utf-8")
                + "rogueSource { type scalarSemiImplicitSource; }\n",
            )
        elif mutation == "heat_scale":
            path = paths["thermal_input"]
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["condition_matrix"]["heat_scale"] = 0.5
            _write_json(path, payload)
        else:
            path = paths["topo_set_dict"]
            _write_text(
                path,
                path.read_text(encoding="utf-8").replace(
                    "name box_heater;", "name box_wall;", 1
                ),
            )
        _sync_refs(root, manifest_path)
        result = cfd_verification.validate_heat_box_manifest(manifest_path, root)
        assert "HEAT_BOX_HEAT_SOURCE_INVALID" in result["blockers"], (
            mutation, result
        )


def test_heat_box_requires_bound_end_time_and_max_delta_t(tmp_path):
    for key in ("endTime", "maxDeltaT"):
        root, manifest_path = _heat_tree(tmp_path / key)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        control = root / manifest["artifacts"]["control_dict"]["path"]
        text = control.read_text(encoding="utf-8")
        text = re.sub(rf"(?m)^{key}\s+[^;]+;\s*$", "", text)
        _write_text(control, text)
        _sync_refs(root, manifest_path)
        result = cfd_verification.validate_heat_box_manifest(manifest_path, root)
        assert "HEAT_BOX_FIXED_DT_REQUIRED" in result["blockers"], (key, result)


def test_heat_box_rejects_extra_or_conflicting_production_dictionary_content(
    tmp_path,
):
    mutations = {
        "schemes_duplicate": (
            "fv_schemes", "\ninterpolationSchemes { default cubic; }\n"
        ),
        "solution_duplicate": (
            "fv_solution", "\nPIMPLE { nCorrectors 99; }\n"
        ),
        "quoted_control_conflict": ("control_dict", '\n"endTime" 1;\n'),
        "coded_function": (
            "control_dict", "\nfunctions { evil { type coded; } }\n"
        ),
    }
    for name, (artifact, suffix) in mutations.items():
        root, manifest_path = _heat_tree(tmp_path / name)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        path = root / manifest["artifacts"][artifact]["path"]
        _write_text(path, path.read_text(encoding="utf-8") + suffix)
        _sync_refs(root, manifest_path)
        result = cfd_verification.validate_heat_box_manifest(manifest_path, root)
        assert "HEAT_BOX_PRODUCTION_DICTIONARY_MISMATCH" in result["blockers"], (
            name, result
        )


def test_heat_box_rejects_malformed_or_unsupported_numerics(tmp_path):
    for name, profile in (("list", []), ("unsupported", "evil")):
        root, manifest_path = _heat_tree(tmp_path / name)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        thermal_path = root / manifest["artifacts"]["thermal_input"]["path"]
        thermal = json.loads(thermal_path.read_text(encoding="utf-8"))
        thermal["numerics"] = {"profile": profile}
        _write_json(thermal_path, thermal)
        _sync_refs(root, manifest_path)
        result = cfd_verification.validate_heat_box_manifest(manifest_path, root)
        assert "HEAT_BOX_NUMERICS_INVALID" in result["blockers"], (name, result)


def test_heat_box_rejects_ambiguous_openfoam_dictionaries(tmp_path):
    for mutation in ("u_type", "control", "solution", "schemes"):
        root, manifest_path = _heat_tree(tmp_path / mutation)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        paths = {
            name: root / ref["path"] for name, ref in manifest["artifacts"].items()
        }
        if mutation == "u_type":
            path = paths["u_field"]
            text = path.read_text(encoding="utf-8").replace(
                "type fixedValue;", "type fixedValue; type slip;", 1
            )
        elif mutation == "control":
            path = paths["control_dict"]
            text = path.read_text(encoding="utf-8") + "adjustTimeStep yes;\n"
        elif mutation == "solution":
            path = paths["fv_solution"]
            text = path.read_text(encoding="utf-8").replace(
                "pRefCell 0;", "pRefCell 0; pRefCell 1;", 1
            )
        else:
            path = paths["fv_schemes"]
            text = path.read_text(encoding="utf-8") + (
                "ddtSchemes { default backward; }\n"
            )
        _write_text(path, text)
        _sync_refs(root, manifest_path)
        result = cfd_verification.validate_heat_box_manifest(manifest_path, root)
        assert "HEAT_BOX_OPENFOAM_DICTIONARY_AMBIGUOUS" in result["blockers"]


def test_heat_box_parses_the_same_bytes_that_were_hashed(tmp_path, monkeypatch):
    root, manifest_path = _heat_tree(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cells_path = (root / manifest["artifacts"]["cell_data"]["path"]).resolve()
    passing = json.loads(cells_path.read_text(encoding="utf-8"))
    failing = copy.deepcopy(passing)
    failing["final_cells"][0]["temperature_k"] += 1.0
    _write_json(cells_path, failing)
    _sync_refs(root, manifest_path)
    original = cfd_verification._read_json

    def forged_second_read(path):
        if Path(path).resolve() == cells_path:
            return copy.deepcopy(passing)
        return original(path)

    monkeypatch.setattr(cfd_verification, "_read_json", forged_second_read)
    result = cfd_verification.validate_heat_box_manifest(manifest_path, root)
    assert result["status"] == "FAIL"
    assert "HEAT_BOX_MEAN_TEMPERATURE_ERROR_LIMIT" in result["blockers"]
