import copy
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator

import cfd_numerical_sensitivity_job as sensitivity_job
import cfd_numerical_spotcheck
import cfd_numerics
import cfd_physics


CHILD_SPECS = {
    "scheme_first_order": ("scheme", 0.125, "first_order", 0.02, 298.10, 0.205),
    "time_dt_0_01": ("time", 0.125, "second_order", 0.01, 297.95, 0.198),
    "mesh_coarse": ("mesh", 0.177, "second_order", 0.02, 298.20, 0.210),
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _ref(root: Path, path: Path) -> dict:
    return {"path": path.relative_to(root).as_posix(), "sha256": _sha(path)}


def _solver_log(delta_t_s: float, duration_s=240.0) -> str:
    count = round(duration_s / delta_t_s)
    rows = [f"Time = {index * delta_t_s:.12g}" for index in range(1, count + 1)]
    for _ in range(5):
        for field in ("Ux", "Uy", "Uz", "p_rgh", "T", "k", "omega"):
            rows.append(
                f"Solving for {field}, Initial residual = 1e-7, Final residual = 1e-8"
            )
    rows.extend([
        "Courant Number mean: 0.1 max: 0.6",
        "time step continuity errors : sum local = 1e-8, global = 5e-8, cumulative = 1e-7",
        "End",
    ])
    return "\n".join(rows) + "\n"


def _schemes(scheme: str) -> str:
    profile = (
        cfd_numerics.STABILIZED_FIRST_ORDER
        if scheme == "first_order"
        else cfd_numerics.DESIGN_LIMITED_SECOND_ORDER
    )
    return cfd_physics._thermal_fv_schemes({"profile": profile})


def _solution(settings: dict, numerics: dict) -> str:
    return cfd_physics._thermal_fv_solution(settings, numerics)


def _samples(mean_t: float, mean_u: float, case_id: str) -> dict:
    rho, cp, flow, power, supply_t = 1.204, 1006.0, 0.1, 1000.0, 293.15
    exhaust_t = supply_t + power / (rho * cp * flow)
    mesh_tag = "coarse" if case_id == "mesh_coarse" else "fine"
    snapshots = []
    for time_s in (232.0, 234.0, 236.0, 238.0, 240.0):
        snapshots.append({
            "time_s": time_s,
            "cells": [
                {"id": f"{mesh_tag}-c1", "center_m": [0.5, 0.5, 0.5],
                 "volume_m3": 4.0,
                 "temperature_k": mean_t - 0.1, "velocity_m_s": mean_u - 0.01},
                {"id": f"{mesh_tag}-c2", "center_m": [1.5, 1.5, 1.5],
                 "volume_m3": 4.0,
                 "temperature_k": mean_t + 0.1, "velocity_m_s": mean_u + 0.01},
            ],
            "terminal_faces": [
                {"face_id": "terminal-supply", "area_m2": 0.0625,
                 "source_id": "SUP-1", "patch": "supply", "role": "supply",
                 "phi_m3_s": -flow, "owner_temperature_k": supply_t},
                {"face_id": "terminal-exhaust", "area_m2": 0.0625,
                 "source_id": "EXH-1", "patch": "exhaust", "role": "exhaust",
                 "phi_m3_s": flow, "owner_temperature_k": exhaust_t},
            ],
            "wall_faces": [
                {"face_id": f"{mesh_tag}-w1", "patch": "wall",
                 "area_m2": 10.0, "y_plus": 45.0},
                {"face_id": f"{mesh_tag}-w2", "patch": "wall",
                 "area_m2": 2.0, "y_plus": 4.0},
            ],
        })
    return {
        "contract": "numerical_spotcheck_samples.v1",
        "case_id": case_id,
        "floor_elevation_m": 0.0,
        "y_plus_source": "openfoam_yPlus_field",
        "snapshots": snapshots,
    }


def _case_paths(root: Path, case_path: str) -> dict[str, Path]:
    case = root / case_path
    return {
        "mesh_manifest": case / "mesh_manifest.json",
        "thermal_input": case / "thermal_input.json",
        "control_dict": case / "system" / "controlDict",
        "fv_schemes": case / "system" / "fvSchemes",
        "fv_solution": case / "system" / "fvSolution",
        "solver_log": case / "log.buoyantBoussinesqPimpleFoam",
        "run_manifest": case / "run_manifest.json",
        "result_manifest": case / "result_manifest.json",
        "sample_data": case / "numerical_spotcheck_samples.json",
    }


def _physical_paths(root: Path, case_path: str) -> dict[str, Path]:
    case = root / case_path
    return {
        relative: case / relative
        for relative in cfd_numerical_spotcheck._REQUIRED_PHYSICAL_TREE_PATHS
    }


def _physical_tree(root: Path, record: dict) -> dict:
    entries = []
    for relative, path in sorted(
        _physical_paths(root, record["case_path"]).items()
    ):
        digest = (
            cfd_numerical_spotcheck._snapshot_directory_tree(path)[0]
            if relative == "constant/polyMesh" else _sha(path)
        )
        entries.append({"path": relative, "sha256": digest, "immutable": True})
    return sensitivity_job.create_physical_tree_snapshot(entries)


def _seed(
    common_sha: str, paths: dict[str, Path], physical_tree_sha256: str,
) -> str:
    return _canonical({
        "common_input_sha256": common_sha,
        "mesh_manifest_sha256": _sha(paths["mesh_manifest"]),
        "thermal_input_sha256": _sha(paths["thermal_input"]),
        "control_dict_sha256": _sha(paths["control_dict"]),
        "fv_schemes_sha256": _sha(paths["fv_schemes"]),
        "fv_solution_sha256": _sha(paths["fv_solution"]),
        "physical_tree_sha256": physical_tree_sha256,
    })


def _sync_case(root: Path, record: dict, common_sha: str, selector_sha: str) -> None:
    paths = _case_paths(root, record["case_path"])
    thermal = json.loads(paths["thermal_input"].read_text(encoding="utf-8"))
    thermal["common_input_sha256"] = common_sha
    thermal["selector_sha256"] = selector_sha
    thermal["mesh_manifest_sha256"] = _sha(paths["mesh_manifest"])
    _write_json(paths["thermal_input"], thermal)
    physical_paths = _physical_paths(root, record["case_path"])
    _write_json(
        physical_paths["thermal_input.physical.v1.json"],
        cfd_physics.profile_free_thermal_input_snapshot(thermal),
    )
    record["physical_tree"] = _physical_tree(root, record)
    record["case_seed_sha256"] = _seed(
        common_sha, paths, record["physical_tree"]["tree_sha256"]
    )
    samples = json.loads(paths["sample_data"].read_text(encoding="utf-8"))
    samples["case_id"] = record["case_id"]
    samples["case_seed_sha256"] = record["case_seed_sha256"]
    _write_json(paths["sample_data"], samples)
    run = json.loads(paths["run_manifest"].read_text(encoding="utf-8"))
    run["case_id"] = record["case_id"]
    run["case_seed_sha256"] = record["case_seed_sha256"]
    run["effective_settings"] = thermal["settings"]
    run["effective_numerics"] = thermal["numerics"]
    run["input"] = {
        "thermal_input_sha256": _sha(paths["thermal_input"]),
        "physical_tree_sha256": record["physical_tree"]["tree_sha256"],
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
    result.update({
        "case_id": record["case_id"],
        "case_seed_sha256": record["case_seed_sha256"],
        "time_s": 240.0,
        "thermal_input_sha256": _sha(paths["thermal_input"]),
        "physical_tree_sha256": record["physical_tree"]["tree_sha256"],
        "run_manifest_sha256": _sha(paths["run_manifest"]),
        "source": {
            "path": paths["sample_data"].relative_to(root).as_posix(),
            "sha256": _sha(paths["sample_data"]),
            "format": "numerical_spotcheck_samples.v1",
        },
    })
    _write_json(paths["result_manifest"], result)
    record["artifacts"] = {name: _ref(root, path) for name, path in paths.items()}


def _study_tree(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "projects"
    study = root / "_working_validation" / "numerical-spotcheck-v1"
    manifest_path = study / "numerical_spotcheck.json"
    common_dir = study / "common"
    common_paths = {
        "geometry": common_dir / "geometry.json",
        "terminal_contract": common_dir / "terminal_contract.json",
        "heat_contract": common_dir / "heat_contract.json",
        "initial_fields": common_dir / "initial_fields.json",
        "selector": common_dir / "occupied_selector.json",
        "zone": common_dir / "occupied_zone.json",
    }
    _write_json(common_paths["geometry"], {"contract": "geometry.v2", "sha": "room"})
    _write_json(common_paths["terminal_contract"], {
        "contract": "terminal_contract.v1",
        "source_ids": ["SUP-1", "EXH-1"],
        "patch_topology": ["supply", "exhaust"],
        "supply_flow_m3_s": 0.1,
        "opening_area_m2_by_source": {"SUP-1": 0.0625, "EXH-1": 0.0625},
    })
    _write_json(common_paths["heat_contract"], {
        "contract": "heat_contract.v1", "applied_convective_power_w": 1000.0,
    })
    _write_json(common_paths["initial_fields"], {
        "contract": "initial_fields.v1", "temperature_k": 293.15,
    })
    selector = sensitivity_job.normalize_occupied_volume_band({
        "contract": "occupied_volume_band.v1",
        "coordinate_source": "cell_center_m_agl",
        "xy_bounds_m": {"x_min_m": 0.1, "x_max_m": 1.9,
                        "y_min_m": 0.1, "y_max_m": 1.9},
        "z_min_agl_m": 0.1,
        "z_max_agl_m": 1.8,
    })
    _write_json(common_paths["selector"], selector)
    _write_json(common_paths["zone"], {
        "contract": "occupied_zone.v1", "selector_sha256": selector["selector_sha256"],
    })
    common_hashes = {name: _sha(path) for name, path in common_paths.items()}
    common_sha = _canonical(common_hashes)

    anchor_path = "_working_validation/working-room-v1/anchor"
    records = {
        "anchor": {
            "case_id": "anchor", "variation": "anchor", "case_path": anchor_path,
            "case_seed_sha256": "0" * 64, "mesh_cell_size_m": 0.125,
            "scheme": "second_order", "delta_t_s": 0.02,
            "claimed_status": "PASS", "artifacts": {},
        }
    }
    for child, (variation, size, scheme, delta_t, _temp, _speed) in CHILD_SPECS.items():
        records[child] = {
            "case_id": child, "variation": variation,
            "case_path": f"_working_validation/numerical-spotcheck-v1/{child}",
            "case_seed_sha256": "0" * 64, "mesh_cell_size_m": size,
            "scheme": scheme, "delta_t_s": delta_t,
            "claimed_status": "PASS", "artifacts": {},
        }

    for case_id, record in records.items():
        if case_id == "anchor":
            variation, size, scheme, delta_t, mean_t, mean_u = (
                "anchor", 0.125, "second_order", 0.02, 298.0, 0.2
            )
        else:
            variation, size, scheme, delta_t, mean_t, mean_u = CHILD_SPECS[case_id]
        paths = _case_paths(root, record["case_path"])
        sample_payload = _samples(mean_t, mean_u, case_id)
        _fingerprint, _valid, sample_inventory = (
            cfd_numerical_spotcheck._physical_sample_fingerprint(sample_payload)
        )
        assert sample_inventory is not None
        _write_json(paths["mesh_manifest"], {
            "contract": "mesh_manifest.v1", "status": "PASS",
            "effective_h_m": size,
            "cell_count": len(sample_inventory["cells"]),
            "occ_volume_m3": sum(
                row["volume_m3"] for row in sample_inventory["cells"]
            ),
            "wall_area_m2": sum(
                row["area_m2"] for row in sample_inventory["wall_faces"]
            ),
            "sampling_inventory_sha256": _canonical(sample_inventory),
            "geometry_sha256": common_hashes["geometry"],
            "terminal_source_ids": ["SUP-1", "EXH-1"],
            "patch_topology": ["supply", "exhaust"],
            "applied_opening_area_m2_by_source": {
                "SUP-1": 0.062, "EXH-1": 0.062,
            },
            "actual_supply_flow_m3_s": 0.1005,
            "applied_opening_area_error_ratio": 0.01,
            "actual_supply_flow_error_ratio": 0.005,
        })
        profile = (
            "stabilized_first_order_v1" if scheme == "first_order"
            else "design_limited_second_order_v1"
        )
        settings = {
            "air_density_kg_m3": 1.204,
            "air_specific_heat_j_kg_k": 1006.0,
            "supply_temperature_k": 293.15,
            "initial_temperature_k": 293.15,
            "reference_temperature_k": 293.15,
            "thermal_expansion_coefficient_1_k": 0.00341,
            "thermal_duration_s": 240.0,
            "flow_through_time_s": 80.0,
            "thermal_initial_delta_t_s": delta_t,
            "thermal_max_delta_t_s": delta_t,
            "thermal_max_co": 1.0,
            "thermal_write_interval_s": 2.0,
            "thermal_residual_tail_samples": 5,
            "minimum_wall_treatment_area_ratio": 0.8,
            "linear_solver_relative_tolerance": 0.05,
            "thermal_scalar_relative_tolerance": 0.05,
            "thermal_outer_correctors": 2,
            "thermal_pressure_correctors": 2,
            "thermal_non_orthogonal_correctors": 0,
            "thermal_preconditioning_iterations": 0,
            "thermal_parallel_processes": 1,
            "thermal_numerics_profile": profile,
        }
        numerics = {
            "profile": profile,
            "convection_order": 1 if scheme == "first_order" else 2,
            "laplacian_correction": (
                "uncorrected" if scheme == "first_order" else "limited 0.5"
            ),
            "sn_grad_correction": (
                "uncorrected" if scheme == "first_order" else "limited 0.5"
            ),
            "required_non_orthogonal_correctors": 0,
        }
        _write_json(paths["thermal_input"], {
            "contract": "thermal_input.v1",
            "validation_scope": "single_pc_numerical_spotcheck",
            "engine": "body_fitted_buoyant_urans",
            "mesh_manifest_sha256": _sha(paths["mesh_manifest"]),
            "common_input_sha256": common_sha,
            "selector_sha256": selector["selector_sha256"],
            "settings": settings,
            "airflow": {"supply_cmh": 360.0, "exhaust_cmh": 360.0},
            "terminals": ["SUP-1", "EXH-1"],
            "wall_patches": ["wall"],
            "heat_sources": [{
                "source_id": "HEAT-1", "name": "load",
                "mesh_patch_name": "load", "convective_power_w": 1000.0,
                "applied_convective_power_w": 1000.0,
            }],
            "heat": {"applied_convective_power_w": 1000.0},
            "assumptions": {"model": "boussinesq_screening"},
            "condition_matrix": {
                "flow_scale": 1.0, "gravity_scale": 1.0, "heat_scale": 1.0,
            },
            "initialisation": {
                "mode": "zero_flow", "pressure_mapping": "none",
                "boussinesq_preconditioning_iterations": 0,
            },
            "numerics": numerics,
        })
        _write_text(
            paths["control_dict"],
            cfd_physics._thermal_control_dict(settings, "single_pc_numerical_spotcheck"),
        )
        _write_text(paths["fv_schemes"], _schemes(scheme))
        _write_text(paths["fv_solution"], _solution(settings, numerics))
        physical_paths = _physical_paths(root, record["case_path"])
        for relative in (
            "0/U", "0/T", "0/k", "0/omega", "0/p", "0/p_rgh",
            "0/nut", "0/alphat",
        ):
            _write_text(physical_paths[relative], f"field {relative};\n")
        _write_text(
            physical_paths["constant/transportProperties"],
            "transportModel Newtonian;\n",
        )
        _write_text(physical_paths["constant/g"], "value (0 0 -9.81);\n")
        _write_text(
            physical_paths["constant/turbulenceProperties"],
            "simulationType RAS; RAS { RASModel kOmegaSST; }\n",
        )
        _write_text(
            physical_paths["constant/fvOptions"],
            cfd_physics._thermal_fv_options(
                json.loads(paths["thermal_input"].read_text(encoding="utf-8"))[
                    "heat_sources"
                ],
                settings,
            ),
        )
        thermal_payload = json.loads(
            paths["thermal_input"].read_text(encoding="utf-8")
        )
        _write_text(
            physical_paths["Allrun"],
            cfd_physics._thermal_allrun(settings, map_initial_fields=False),
        )
        _write_text(
            physical_paths["system/controlDict.transient"],
            cfd_physics._thermal_control_dict(
                settings, "single_pc_numerical_spotcheck"
            ),
        )
        _write_text(
            physical_paths["system/fvSchemes.transient"],
            cfd_physics._thermal_fv_schemes(numerics),
        )
        _write_text(
            physical_paths["system/fvSolution.transient"],
            cfd_physics._thermal_fv_solution(settings, numerics),
        )
        _write_text(
            physical_paths["system/controlDict.precondition"],
            cfd_physics._thermal_precondition_control_dict(settings),
        )
        _write_text(
            physical_paths["system/fvSchemes.precondition"],
            cfd_physics._thermal_precondition_fv_schemes(),
        )
        _write_text(
            physical_paths["system/fvSolution.precondition"],
            cfd_physics._thermal_precondition_fv_solution(settings),
        )
        _write_text(
            physical_paths["system/topoSetDict"],
            cfd_physics._thermal_toposet_dict(thermal_payload["heat_sources"]),
        )
        _write_text(
            physical_paths["constant/polyMesh"] / "points",
            f"synthetic mesh h={size:.12g}\n",
        )
        _write_json(
            physical_paths["mesh_input.json"],
            {"contract": "mesh_input.v1", "background_cell_m": size},
        )
        _write_json(
            physical_paths["surface_manifest.json"],
            {"contract": "surface_manifest.v1", "topology": ["supply", "exhaust"]},
        )
        _write_json(physical_paths["thermal_input.physical.v1.json"], {})
        _write_text(paths["solver_log"], _solver_log(delta_t))
        _write_json(paths["sample_data"], sample_payload)
        _write_json(paths["run_manifest"], {
            "schema_version": 1,
            "contract": "run_manifest.v1", "engine": "body_fitted_buoyant_urans",
            "created_at": "2026-08-25T00:00:00Z",
            "status": "PASS", "design_ready": False,
            "errors": [], "warnings": [],
            "solver": {"ended": True, "fatal": False, "end_time": 240.0},
            "airflow": {"supply_cmh": 360.0, "exhaust_cmh": 360.0},
            "terminals": [],
            "y_plus": {
                "available": True, "time": 240.0, "area_ratio_in_target": 1.0,
                "wall_treatment_acceptable_area_ratio": 1.0,
                "minimum": 4.0, "maximum": 45.0,
                "area_weighted_average": 38.0, "patches": [],
            },
            "effective_settings": {}, "effective_numerics": {},
            "numerical_quality": {
                "contract": "numerical_quality.v1", "status": "SCREENING_ONLY",
                "design_ready": False, "profile": profile,
                "convection_order": 1 if scheme == "first_order" else 2,
                "blockers": [],
            },
            "input": {},
        })
        _write_json(paths["result_manifest"], {
            "schema_version": 1, "contract": "result_manifest.v1",
            "engine": "body_fitted_openfoam_vtu",
            "created_at": "2026-08-25T00:00:01Z", "time_s": 240.0,
            "field_location": "cell", "fields": {"T": {}, "U": {}},
            "summary_path": "summary.json", "summary_sha256": "d" * 64,
            "slices": [
                {"axis": "x", "path": "slice-x.json", "sha256": "e" * 64},
                {"axis": "y", "path": "slice-y.json", "sha256": "f" * 64},
                {"axis": "z", "path": "slice-z.json", "sha256": "1" * 64},
            ],
            "thermal_input_sha256": "0" * 64,
            "run_manifest_sha256": "0" * 64,
            "source": {},
        })
        _sync_case(root, record, common_sha, selector["selector_sha256"])

    acceptance_path = root / "_working_validation" / "working-room-v1" / "working_room_acceptance.json"
    _write_json(acceptance_path, {
        "contract": "working_room_acceptance.v1", "status": "PASS",
        "authoritative_case_path": anchor_path,
        "authoritative_case_sha256": (
            cfd_numerical_spotcheck._snapshot_directory_tree(
                root / anchor_path
            )[0]
        ),
    })
    common_artifacts = {
        "working_room_acceptance": _ref(root, acceptance_path),
        **{name: _ref(root, path) for name, path in common_paths.items()},
    }
    _write_json(manifest_path, {
        "contract": "numerical_spotcheck.v1",
        "status": "PASS",
        "verification_scope": "two_level_scheme_time_mesh_spotchecks",
        "common_artifacts": common_artifacts,
        "anchor": records["anchor"],
        "children": {name: records[name] for name in CHILD_SPECS},
        "claimed_comparisons": [
            {"variation": name, "passed": True} for name in CHILD_SPECS
        ],
        "limitations": [
            "two_level_engineering_spotcheck_not_gci",
            "not_design_citable",
            "not_release_ready",
        ],
    })
    return root, manifest_path


def _refresh_manifest_refs(root: Path, manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for ref in manifest["common_artifacts"].values():
        ref["sha256"] = _sha(root / ref["path"])
    for record in [manifest["anchor"], *manifest["children"].values()]:
        for ref in record["artifacts"].values():
            ref["sha256"] = _sha(root / ref["path"])
        record["physical_tree"] = _physical_tree(root, record)
    _write_json(manifest_path, manifest)
    return manifest


def _refresh_case_result_and_manifest(
    root: Path,
    manifest_path: Path,
    case_id: str,
    *,
    refresh_source: bool = False,
) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = manifest["anchor"] if case_id == "anchor" else manifest["children"][case_id]
    paths = _case_paths(root, record["case_path"])
    result = json.loads(paths["result_manifest"].read_text(encoding="utf-8"))
    result["run_manifest_sha256"] = _sha(paths["run_manifest"])
    if refresh_source:
        result["source"]["sha256"] = _sha(paths["sample_data"])
    _write_json(paths["result_manifest"], result)
    return _refresh_manifest_refs(root, manifest_path)


def _resync_case(root: Path, manifest_path: Path, case_id: str) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = manifest["anchor"] if case_id == "anchor" else manifest["children"][case_id]
    common_names = (
        "geometry", "terminal_contract", "heat_contract",
        "initial_fields", "selector", "zone",
    )
    common_sha = _canonical({
        name: _sha(root / manifest["common_artifacts"][name]["path"])
        for name in common_names
    })
    selector = json.loads(
        (root / manifest["common_artifacts"]["selector"]["path"])
        .read_text(encoding="utf-8")
    )
    _sync_case(root, record, common_sha, selector["selector_sha256"])
    if case_id == "anchor":
        manifest["anchor"] = record
    else:
        manifest["children"][case_id] = record
    _write_json(manifest_path, manifest)
    return _refresh_manifest_refs(root, manifest_path)


def test_numerical_spotcheck_schema_is_closed_and_validates_fixture(tmp_path):
    _root, manifest_path = _study_tree(tmp_path)
    schema = json.loads(Path(cfd_numerical_spotcheck.__file__).with_name(
        "numerical_spotcheck.v1.schema.json"
    ).read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(manifest)) == []
    manifest["extra"] = True
    assert list(Draft202012Validator(schema).iter_errors(manifest))


def test_strict_scheme_semantics_rejects_duplicates_and_indirection():
    for scheme in ("first_order", "second_order"):
        assert cfd_numerical_spotcheck._strict_scheme_semantics(
            scheme, _schemes(scheme)
        )["valid"] is True

    first_order = _schemes("first_order")
    mutations = (
        first_order.replace("default Euler;", "default backward;", 1),
        first_order.replace(
            "div(phi,T) bounded Gauss upwind;",
            "div(phi,T) bounded Gauss upwind;\n"
            "  div(phi,T) bounded Gauss upwind;",
            1,
        ),
        first_order + "\nddtSchemes { default Euler; }\n",
        '#include "hiddenSchemes"\n' + first_order,
    )
    assert all(
        cfd_numerical_spotcheck._strict_scheme_semantics(
            "first_order", text
        )["valid"] is False
        for text in mutations
    )


def test_numerical_spotcheck_recomputes_three_variants_and_is_stable(tmp_path):
    root, manifest_path = _study_tree(tmp_path)
    first = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
        manifest_path, root
    )
    second = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
        manifest_path, root
    )
    assert first == second
    assert first["status"] == "PASS", first
    assert set(first["comparisons"]) == set(CHILD_SPECS)
    assert first["label"] == "two_level_engineering_spotcheck_not_gci"
    assert first["design_citable"] is False
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = [manifest["anchor"], *manifest["children"].values()]
    expected_evidence = {
        manifest_path.relative_to(root).as_posix(),
        *(ref["path"] for ref in manifest["common_artifacts"].values()),
        *(
            ref["path"]
            for record in records
            for ref in record["artifacts"].values()
        ),
    }
    for record in records:
        for entry in record["physical_tree"]["entries"]:
            expected_evidence.add(f"{record['case_path']}/{entry['path']}")
        expected_evidence.add(f"{record['case_path']}/constant/polyMesh/points")
    expected_evidence.add(manifest["anchor"]["case_path"])
    assert set(first["evidence_sha256"]) == expected_evidence


def test_numerical_spotcheck_rejects_wrong_child_set_and_anchor(tmp_path):
    root, manifest_path = _study_tree(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["children"]["unexpected"] = manifest["children"].pop("mesh_coarse")
    _write_json(manifest_path, manifest)
    result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
        manifest_path, root
    )
    assert "NUMERICAL_SPOTCHECK_CHILD_SET_INVALID" in result["blockers"]

    root, manifest_path = _study_tree(tmp_path / "anchor")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["anchor"]["case_path"] = "_working_validation/working-room-v1/repeat"
    _write_json(manifest_path, manifest)
    result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
        manifest_path, root
    )
    assert "NUMERICAL_SPOTCHECK_ANCHOR_INVALID" in result["blockers"]


def test_numerical_spotcheck_rejects_adaptive_or_nonfixed_history(tmp_path):
    root, manifest_path = _study_tree(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = manifest["children"]["time_dt_0_01"]
    control = root / record["artifacts"]["control_dict"]["path"]
    _write_text(control, "deltaT 0.01;\nadjustTimeStep yes;\nmaxCo 1;\n")
    _refresh_manifest_refs(root, manifest_path)
    result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
        manifest_path, root
    )
    assert "NUMERICAL_SPOTCHECK_FIXED_DT_REQUIRED" in result["blockers"]

    root, manifest_path = _study_tree(tmp_path / "history")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = manifest["children"]["scheme_first_order"]
    log = root / record["artifacts"]["solver_log"]["path"]
    text = log.read_text(encoding="utf-8").replace("Time = 0.04", "Time = 0.041", 1)
    _write_text(log, text)
    _refresh_manifest_refs(root, manifest_path)
    result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
        manifest_path, root
    )
    assert "NUMERICAL_SPOTCHECK_FIXED_DT_HISTORY_INVALID" in result["blockers"]


def test_numerical_spotcheck_rejects_copied_or_missing_child_results(tmp_path):
    root, manifest_path = _study_tree(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    anchor_result = root / manifest["anchor"]["artifacts"]["result_manifest"]["path"]
    child_result = root / manifest["children"]["scheme_first_order"]["artifacts"]["result_manifest"]["path"]
    _write_text(child_result, anchor_result.read_text(encoding="utf-8"))
    _refresh_manifest_refs(root, manifest_path)
    result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
        manifest_path, root
    )
    assert "NUMERICAL_SPOTCHECK_COPIED_RESULT" in result["blockers"]

    root, manifest_path = _study_tree(tmp_path / "missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["children"]["mesh_coarse"]["artifacts"]["result_manifest"]["path"] = (
        "_working_validation/numerical-spotcheck-v1/mesh_coarse/missing.json"
    )
    _write_json(manifest_path, manifest)
    result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
        manifest_path, root
    )
    assert "NUMERICAL_SPOTCHECK_ARTIFACT_PATH_INVALID" in result["blockers"]


def test_numerical_spotcheck_rejects_selector_drift_and_child_inherited_pass(tmp_path):
    root, manifest_path = _study_tree(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    selector_path = root / manifest["common_artifacts"]["selector"]["path"]
    selector = json.loads(selector_path.read_text(encoding="utf-8"))
    selector["z_max_agl_m"] = 1.7
    _write_json(selector_path, selector)
    _refresh_manifest_refs(root, manifest_path)
    result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
        manifest_path, root
    )
    assert "NUMERICAL_SPOTCHECK_SELECTOR_DRIFT" in result["blockers"]

    root, manifest_path = _study_tree(tmp_path / "forged")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = manifest["children"]["mesh_coarse"]
    samples_path = root / record["artifacts"]["sample_data"]["path"]
    samples = json.loads(samples_path.read_text(encoding="utf-8"))
    for snapshot in samples["snapshots"]:
        for cell in snapshot["cells"]:
            cell["temperature_k"] += 2.0
    _write_json(samples_path, samples)
    result_manifest = root / record["artifacts"]["result_manifest"]["path"]
    result_payload = json.loads(result_manifest.read_text(encoding="utf-8"))
    result_payload["source"]["sha256"] = _sha(samples_path)
    _write_json(result_manifest, result_payload)
    _refresh_manifest_refs(root, manifest_path)
    result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
        manifest_path, root
    )
    assert result["status"] == "FAIL"
    assert "NUMERICAL_SPOTCHECK_QOI_LIMIT" in result["blockers"]


def test_numerical_spotcheck_recomputes_mesh_opening_and_flow_errors(tmp_path):
    root, manifest_path = _study_tree(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = manifest["children"]["mesh_coarse"]
    mesh_path = root / record["artifacts"]["mesh_manifest"]["path"]
    mesh = json.loads(mesh_path.read_text(encoding="utf-8"))
    mesh["applied_opening_area_m2_by_source"] = {
        "SUP-1": 0.05, "EXH-1": 0.05,
    }
    mesh["actual_supply_flow_m3_s"] = 0.11
    mesh["applied_opening_area_error_ratio"] = 0.0
    mesh["actual_supply_flow_error_ratio"] = 0.0
    _write_json(mesh_path, mesh)
    samples_path = root / record["artifacts"]["sample_data"]["path"]
    samples = json.loads(samples_path.read_text(encoding="utf-8"))
    for snapshot in samples["snapshots"]:
        for face in snapshot["terminal_faces"]:
            face["phi_m3_s"] = -0.11 if face["role"] == "supply" else 0.11
    _write_json(samples_path, samples)
    result_path = root / record["artifacts"]["result_manifest"]["path"]
    result_manifest = json.loads(result_path.read_text(encoding="utf-8"))
    result_manifest["source"]["sha256"] = _sha(samples_path)
    _write_json(result_path, result_manifest)
    _refresh_manifest_refs(root, manifest_path)
    result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
        manifest_path, root
    )
    assert "NUMERICAL_SPOTCHECK_MESH_TERMINAL_LIMIT" in result["blockers"]


def test_numerical_spotcheck_rejects_traversal_reparse_and_output_alias(tmp_path, monkeypatch):
    root, manifest_path = _study_tree(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["common_artifacts"]["geometry"]["path"] = "../outside.json"
    _write_json(manifest_path, manifest)
    result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
        manifest_path, root
    )
    assert "NUMERICAL_SPOTCHECK_ARTIFACT_PATH_INVALID" in result["blockers"]


def test_numerical_spotcheck_rejects_post_load_hash_drift(tmp_path, monkeypatch):
    root, manifest_path = _study_tree(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    target = (
        root / manifest["children"]["time_dt_0_01"]["artifacts"]["sample_data"]["path"]
    ).resolve()
    original_reader = cfd_numerical_spotcheck._snapshot_file
    changed = False

    def drifting_snapshot(path):
        nonlocal changed
        value = original_reader(path)
        if Path(path).resolve() == target and not changed:
            changed = True
            target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        return value

    monkeypatch.setattr(cfd_numerical_spotcheck, "_snapshot_file", drifting_snapshot)
    result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
        manifest_path, root
    )
    assert "ARTIFACT_CHANGED_DURING_VALIDATION" in result["blockers"]

    root, manifest_path = _study_tree(tmp_path / "alias")
    result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
        manifest_path, root, evaluator_output_path=manifest_path
    )
    assert "OUTPUT_ALIAS" in result["blockers"]

    root, manifest_path = _study_tree(tmp_path / "nested-output")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    nested_output = root / manifest["anchor"]["case_path"] / "new-output.json"
    result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
        manifest_path, root, evaluator_output_path=nested_output
    )
    assert "OUTPUT_ALIAS" in result["blockers"]

    root, manifest_path = _study_tree(tmp_path / "reparse")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    geometry = root / manifest["common_artifacts"]["geometry"]["path"]
    original = cfd_numerical_spotcheck._is_reparse
    monkeypatch.setattr(
        cfd_numerical_spotcheck,
        "_is_reparse",
        lambda path: Path(path).absolute() == geometry.absolute() or original(path),
    )
    result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
        manifest_path, root
    )
    assert "NUMERICAL_SPOTCHECK_ARTIFACT_PATH_INVALID" in result["blockers"]


def test_numerical_spotcheck_requires_fixed_authoritative_manifest(tmp_path):
    root, manifest_path = _study_tree(tmp_path)
    payload = manifest_path.read_text(encoding="utf-8")
    for relative in (
        "_working_validation/numerical-spotcheck-v1/copied.json",
        "_working_validation/numerical-spotcheck-v1/latest/numerical_spotcheck.json",
    ):
        copied = root / relative
        _write_text(copied, payload)
        result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
            copied, root
        )
        assert "NUMERICAL_SPOTCHECK_MANIFEST_PATH_INVALID" in result["blockers"]


def test_numerical_spotcheck_rejects_failed_incomplete_or_unbound_run(tmp_path):
    for mutation in ("failed", "incomplete", "log_unbound"):
        root, manifest_path = _study_tree(tmp_path / mutation)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        record = manifest["children"]["time_dt_0_01"]
        run_path = root / record["artifacts"]["run_manifest"]["path"]
        run = json.loads(run_path.read_text(encoding="utf-8"))
        if mutation == "failed":
            run["status"] = "FAIL"
        elif mutation == "incomplete":
            run["solver"]["ended"] = False
        else:
            run["input"]["solver_log_sha256"] = "9" * 64
        _write_json(run_path, run)
        _refresh_case_result_and_manifest(
            root, manifest_path, "time_dt_0_01"
        )
        result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
            manifest_path, root
        )
        assert "NUMERICAL_SPOTCHECK_RUN_INCOMPLETE_OR_UNBOUND" in result["blockers"]


def test_numerical_spotcheck_rejects_unbound_effective_numerics(tmp_path):
    root, manifest_path = _study_tree(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = manifest["children"]["scheme_first_order"]
    run_path = root / record["artifacts"]["run_manifest"]["path"]
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["input"]["numerical_provenance"]["effective_numerics_sha256"] = "9" * 64
    _write_json(run_path, run)
    _refresh_case_result_and_manifest(
        root, manifest_path, "scheme_first_order"
    )
    result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
        manifest_path, root
    )
    assert "NUMERICAL_SPOTCHECK_RUN_CROSS_REFERENCE_INVALID" in result["blockers"]


def test_numerical_spotcheck_rejects_schema_invalid_run_and_result(tmp_path):
    for artifact, missing, expected in (
        ("run_manifest", "created_at", "NUMERICAL_SPOTCHECK_RUN_MANIFEST_SCHEMA_INVALID"),
        ("result_manifest", "created_at", "NUMERICAL_SPOTCHECK_RESULT_MANIFEST_SCHEMA_INVALID"),
    ):
        root, manifest_path = _study_tree(tmp_path / artifact)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        record = manifest["children"]["scheme_first_order"]
        target = root / record["artifacts"][artifact]["path"]
        payload = json.loads(target.read_text(encoding="utf-8"))
        payload.pop(missing)
        _write_json(target, payload)
        if artifact == "run_manifest":
            _refresh_case_result_and_manifest(
                root, manifest_path, "scheme_first_order"
            )
        else:
            _refresh_manifest_refs(root, manifest_path)
        result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
            manifest_path, root
        )
        assert expected in result["blockers"]


def test_numerical_spotcheck_rejects_physical_sample_copy_with_new_wrapper(tmp_path):
    root, manifest_path = _study_tree(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    anchor = manifest["anchor"]
    child = manifest["children"]["scheme_first_order"]
    anchor_samples_path = root / anchor["artifacts"]["sample_data"]["path"]
    child_samples_path = root / child["artifacts"]["sample_data"]["path"]
    copied = copy.deepcopy(json.loads(anchor_samples_path.read_text(encoding="utf-8")))
    copied["case_id"] = child["case_id"]
    copied["case_seed_sha256"] = child["case_seed_sha256"]
    copied["nonce"] = "wrapper-only-change"
    _write_json(child_samples_path, copied)
    _refresh_case_result_and_manifest(
        root, manifest_path, "scheme_first_order", refresh_source=True
    )
    result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
        manifest_path, root
    )
    assert "NUMERICAL_SPOTCHECK_COPIED_RESULT" in result["blockers"]


def test_numerical_spotcheck_rejects_copied_sample_with_physical_nonce(tmp_path):
    root, manifest_path = _study_tree(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    anchor = manifest["anchor"]
    child = manifest["children"]["scheme_first_order"]
    anchor_samples = root / anchor["artifacts"]["sample_data"]["path"]
    child_samples = root / child["artifacts"]["sample_data"]["path"]
    copied = copy.deepcopy(json.loads(anchor_samples.read_text(encoding="utf-8")))
    for snapshot in copied["snapshots"]:
        snapshot["cells"].append({
            "id": "irrelevant-nonce", "center_m": [100.0, 100.0, 100.0],
            "volume_m3": 1e-9, "temperature_k": 293.15,
            "velocity_m_s": 0.0,
        })
    _write_json(child_samples, copied)
    mesh_path = root / child["artifacts"]["mesh_manifest"]["path"]
    mesh = json.loads(mesh_path.read_text(encoding="utf-8"))
    _fingerprint, _valid, inventory = (
        cfd_numerical_spotcheck._physical_sample_fingerprint(copied)
    )
    assert inventory is not None
    mesh["cell_count"] = len(inventory["cells"])
    mesh["occ_volume_m3"] = sum(row["volume_m3"] for row in inventory["cells"])
    mesh["sampling_inventory_sha256"] = _canonical(inventory)
    _write_json(mesh_path, mesh)
    _resync_case(root, manifest_path, "scheme_first_order")
    result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
        manifest_path, root
    )
    assert any(code in result["blockers"] for code in (
        "NUMERICAL_SPOTCHECK_COPIED_RESULT",
        "NUMERICAL_SPOTCHECK_SAMPLE_INVENTORY_INVALID",
        "NUMERICAL_SPOTCHECK_EXTRA_VARIATION",
    )), result


def test_numerical_spotcheck_rejects_copied_sample_with_tiny_field_nonce(
    tmp_path,
):
    root, manifest_path = _study_tree(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    anchor = manifest["anchor"]
    child = manifest["children"]["scheme_first_order"]
    anchor_samples = root / anchor["artifacts"]["sample_data"]["path"]
    child_samples = root / child["artifacts"]["sample_data"]["path"]
    copied = copy.deepcopy(json.loads(anchor_samples.read_text(encoding="utf-8")))
    for snapshot in copied["snapshots"]:
        snapshot["cells"][0]["temperature_k"] += 1e-8
    _write_json(child_samples, copied)
    _resync_case(root, manifest_path, "scheme_first_order")
    result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
        manifest_path, root
    )
    assert "NUMERICAL_SPOTCHECK_COPIED_RESULT" in result["blockers"], result


def test_numerical_spotcheck_rejects_terminal_face_split_nonce(tmp_path):
    root, manifest_path = _study_tree(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    child = manifest["children"]["time_dt_0_01"]
    samples_path = root / child["artifacts"]["sample_data"]["path"]
    samples = json.loads(samples_path.read_text(encoding="utf-8"))
    for snapshot in samples["snapshots"]:
        supply = next(
            row for row in snapshot["terminal_faces"] if row["role"] == "supply"
        )
        supply["phi_m3_s"] /= 2.0
        supply["area_m2"] /= 2.0
        duplicate = copy.deepcopy(supply)
        duplicate["face_id"] = supply["face_id"] + "-split"
        snapshot["terminal_faces"].append(duplicate)
    _write_json(samples_path, samples)
    _resync_case(root, manifest_path, "time_dt_0_01")
    result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
        manifest_path, root
    )
    assert "NUMERICAL_SPOTCHECK_SAMPLE_INVENTORY_INVALID" in result["blockers"]


def test_numerical_spotcheck_rejects_relinked_physical_tree_drift(tmp_path):
    root, manifest_path = _study_tree(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    child = manifest["children"]["mesh_coarse"]
    transport = root / child["case_path"] / "constant" / "transportProperties"
    _write_text(transport, "transportModel powerLaw;\n")
    _resync_case(root, manifest_path, "mesh_coarse")
    result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
        manifest_path, root
    )
    assert "NUMERICAL_SPOTCHECK_EXTRA_VARIATION" in result["blockers"]


def test_numerical_spotcheck_rejects_relinked_production_seed_drift(tmp_path):
    root, manifest_path = _study_tree(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    child = manifest["children"]["scheme_first_order"]
    allrun = root / child["case_path"] / "Allrun"
    _write_text(allrun, allrun.read_text(encoding="utf-8") + "# tampered\n")
    _resync_case(root, manifest_path, "scheme_first_order")
    result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
        manifest_path, root
    )
    assert "NUMERICAL_SPOTCHECK_PRODUCTION_SEED_MISMATCH" in result["blockers"]
    assert "NUMERICAL_SPOTCHECK_EXTRA_VARIATION" in result["blockers"]


def test_numerical_spotcheck_rejects_extra_physical_changes_in_time_child(tmp_path):
    mutations = (
        ("rho", "settings", "air_density_kg_m3", 1.205),
        ("supply", "settings", "supply_temperature_k", 294.15),
        ("duration", "settings", "thermal_duration_s", 241.0),
        ("heat", "heat", "applied_convective_power_w", 1001.0),
    )
    for name, section, key, value in mutations:
        root, manifest_path = _study_tree(tmp_path / name)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        record = manifest["children"]["time_dt_0_01"]
        thermal_path = root / record["artifacts"]["thermal_input"]["path"]
        thermal = json.loads(thermal_path.read_text(encoding="utf-8"))
        thermal[section][key] = value
        _write_json(thermal_path, thermal)
        _resync_case(root, manifest_path, "time_dt_0_01")
        result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
            manifest_path, root
        )
        assert "NUMERICAL_SPOTCHECK_EXTRA_VARIATION" in result["blockers"], (
            name, result
        )


def test_numerical_spotcheck_malformed_production_inputs_fail_closed(tmp_path):
    for mutation in ("settings_list", "settings_bad", "heat_source_none"):
        root, manifest_path = _study_tree(tmp_path / mutation)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        record = manifest["children"]["scheme_first_order"]
        thermal_path = root / record["artifacts"]["thermal_input"]["path"]
        thermal = json.loads(thermal_path.read_text(encoding="utf-8"))
        if mutation == "settings_list":
            thermal["settings"] = []
        elif mutation == "settings_bad":
            thermal["settings"] = {"thermal_duration_s": "bad"}
        else:
            thermal["heat_sources"] = [None]
        _write_json(thermal_path, thermal)
        _refresh_manifest_refs(root, manifest_path)
        result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
            manifest_path, root
        )
        assert "NUMERICAL_SPOTCHECK_PRODUCTION_SEED_MISMATCH" in result["blockers"], (
            mutation, result
        )
        assert "NUMERICAL_SPOTCHECK_PHYSICAL_INPUT_INVALID" in result["blockers"], (
            mutation, result
        )


def test_numerical_spotcheck_enforces_exact_first_order_scheme_semantics(tmp_path):
    root, manifest_path = _study_tree(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = manifest["children"]["scheme_first_order"]
    schemes_path = root / record["artifacts"]["fv_schemes"]["path"]
    _write_text(schemes_path, """ddtSchemes { default backward; }
divSchemes {
  default none;
  div(phi,U) bounded Gauss linearUpwind grad(U);
  div(phi,T) bounded Gauss linearUpwind grad(T);
  div(phi,k) bounded Gauss linearUpwind grad(k);
  div(phi,omega) bounded Gauss linearUpwind grad(omega);
}
laplacianSchemes { default Gauss linear corrected; }
snGradSchemes { default corrected; }
""")
    _resync_case(root, manifest_path, "scheme_first_order")
    result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
        manifest_path, root
    )
    assert "NUMERICAL_SPOTCHECK_SCHEME_SEMANTICS_INVALID" in result["blockers"]


def test_numerical_spotcheck_rejects_undeclared_dictionary_changes(tmp_path):
    mutations = (
        ("scheme_first_order", "control_dict", "\nwriteInterval 2;\n"),
        ("time_dt_0_01", "fv_schemes", "\nfluxRequired { default no; }\n"),
        ("mesh_coarse", "fv_solution", "\nrelaxationFactors { fields {}; }\n"),
    )
    for case_id, artifact, suffix in mutations:
        root, manifest_path = _study_tree(tmp_path / case_id)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        record = manifest["children"][case_id]
        path = root / record["artifacts"][artifact]["path"]
        _write_text(path, path.read_text(encoding="utf-8") + suffix)
        _resync_case(root, manifest_path, case_id)
        result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
            manifest_path, root
        )
        assert "NUMERICAL_SPOTCHECK_EXTRA_VARIATION" in result["blockers"], (
            case_id, result
        )


def test_numerical_spotcheck_parses_the_same_bytes_that_were_hashed(
    tmp_path, monkeypatch
):
    root, manifest_path = _study_tree(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = manifest["children"]["mesh_coarse"]
    samples_path = (root / record["artifacts"]["sample_data"]["path"]).resolve()
    passing = json.loads(samples_path.read_text(encoding="utf-8"))
    failing = copy.deepcopy(passing)
    for snapshot in failing["snapshots"]:
        for cell in snapshot["cells"]:
            cell["temperature_k"] += 2.0
    _write_json(samples_path, failing)
    _refresh_case_result_and_manifest(
        root, manifest_path, "mesh_coarse", refresh_source=True
    )
    original = cfd_numerical_spotcheck._read_json

    def forged_second_read(path):
        if Path(path).resolve() == samples_path:
            return copy.deepcopy(passing)
        return original(path)

    monkeypatch.setattr(cfd_numerical_spotcheck, "_read_json", forged_second_read)
    result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
        manifest_path, root
    )
    assert result["status"] == "FAIL"
    assert "NUMERICAL_SPOTCHECK_QOI_LIMIT" in result["blockers"]


def test_numerical_spotcheck_rejects_samples_outside_exact_final_window(tmp_path):
    for mutation, new_time in (("before", 231.0), ("after", 241.0)):
        root, manifest_path = _study_tree(tmp_path / mutation)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        record = manifest["children"]["mesh_coarse"]
        samples_path = root / record["artifacts"]["sample_data"]["path"]
        samples = json.loads(samples_path.read_text(encoding="utf-8"))
        index = 0 if mutation == "before" else -1
        samples["snapshots"][index]["time_s"] = new_time
        _write_json(samples_path, samples)
        _refresh_case_result_and_manifest(
            root, manifest_path, "mesh_coarse", refresh_source=True
        )
        result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
            manifest_path, root
        )
        assert "NUMERICAL_SPOTCHECK_FINAL_WINDOW_INVALID" in result["blockers"]


def test_numerical_spotcheck_rejects_reparse_output_leaf(tmp_path, monkeypatch):
    root, manifest_path = _study_tree(tmp_path)
    output = root / "_working_validation" / "reports" / "numerical.json"
    _write_text(output, "{}")
    path_security = cfd_numerical_spotcheck.path_security
    original = path_security._is_reparse
    monkeypatch.setattr(
        path_security,
        "_is_reparse",
        lambda path: Path(path).absolute() == output.absolute() or original(path),
    )
    result = cfd_numerical_spotcheck.validate_numerical_spotcheck_manifest(
        manifest_path, root, evaluator_output_path=output
    )
    assert "OUTPUT_PATH_INVALID" in result["blockers"]
