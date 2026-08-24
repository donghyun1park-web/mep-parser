import hashlib
import json
import os
from pathlib import Path
import shutil
from unittest import mock

from jsonschema import Draft202012Validator
import pytest

import cfd_evidence


STAMP = "2026-08-24T00:00:00Z"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_sha256(value: dict) -> str:
    data = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _directory_symlink(target: Path, link: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink creation unavailable: {exc}")


def _geometry() -> dict:
    return {
        "schema_version": 2,
        "contract": "geometry.v2",
        "source": "input/site.dxf",
        "units": "mm",
        "source_units": {
            "millimetres_per_source_unit": 1.0,
            "normalized_length_unit": "mm",
            "assumed": False,
        },
        "coordinate_system": {
            "axis_convention": "XY_Z_UP",
            "origin_mm": [0.0, 0.0, 0.0],
            "rotation_deg": 0.0,
            "millimetres_to_metres": 0.001,
        },
        "levels": [{"id": "L1", "label": "Level 1", "elevation_mm": 0.0}],
        "elements": {
            "zone": [{
                "id": "zone-1",
                "id_stability": "geometry_derived",
                "category": "zone",
                "source_ref": {
                    "handles": ["1"], "layer": "A-ZONE",
                    "block_name": None, "entity_type": "LWPOLYLINE",
                },
                "confirmed": True,
                "confirmation_state": "confirmed",
                "semantic": {"ceiling_height_mm": 3000.0},
                "level_id": "L1",
                "closed": True,
                "points": [[0, 0], [1000, 0], [1000, 1000], [0, 1000]],
            }],
        },
        "review": {
            "engine": "deterministic",
            "ready": True,
            "blocking": False,
            "blocker_count": 0,
            "items": [],
            "screening_voxel_allowed": True,
        },
    }


def _surface(root: Path, geometry_path: Path, surface_dir: Path) -> dict:
    stl = surface_dir / "air_volume_regions.stl"
    brep = surface_dir / "air_volume.brep"
    stl.parent.mkdir(parents=True, exist_ok=True)
    stl.write_text("solid room\nendsolid room\n", encoding="ascii")
    brep.write_text("BREP", encoding="ascii")
    return {
        "schema_version": 1,
        "contract": "surface_manifest.v1",
        "engine": "body_fitted_airflow",
        "source": {
            "geometry_path": geometry_path.relative_to(root).as_posix(),
            "geometry_sha256": _sha256(geometry_path),
            "geometry_contract": "geometry.v2",
            "space_element_id": "zone-1",
        },
        "tools": {"freecad": "1.1", "occ": "7.8", "python": "3.12"},
        "transform": {
            "occ_units": "mm", "stl_units": "m", "scale": 0.001,
            "origin_mm": [0, 0, 0], "rotation_deg": 0,
            "inverse": {"scale": 1000, "rotation_deg": 0, "translation_mm": [0, 0, 0]},
        },
        "tessellation": {"algorithm": "fixture", "linear_deflection_mm": 0.5},
        "air_volume": {
            "valid": True, "solid_count": 1, "volume_m3": 1.0,
            "boundary_area_m2": 6.0, "region_area_m2": 6.0,
            "area_error_ratio": 0.0, "obstacle_count": 0,
            "location_in_mesh": {"point_m": [0.5, 0.5, 0.5]},
        },
        "regions": [{
            "name": "walls", "role": "wall", "source_element_ids": ["zone-1"],
            "area_m2": 6.0,
            "aabb": {"min_m": [0, 0, 0], "max_m": [1, 1, 1]},
            "triangle_count": 12, "normalized_triangle_hash": "a" * 64,
        }],
        "topology": {
            "open_edges": 0, "non_manifold_edges": 0,
            "duplicate_triangles": 0, "watertight": True,
        },
        "outputs": {
            "multi_region_stl": "air_volume_regions.stl",
            "stl_sha256": _sha256(stl),
            "brep": "air_volume.brep",
            "brep_sha256": _sha256(brep),
            "freecad_document": "air_volume.FCStd",
        },
        "surface_hash": "b" * 64,
    }


def _mesh(surface_copy: Path, mesh_input_path: Path) -> dict:
    return {
        "schema_version": 1,
        "contract": "mesh_manifest.v1",
        "engine": "body_fitted_airflow",
        "created_at": STAMP,
        "status": "PASS",
        "errors": [],
        "warnings": [],
        "profile": "detailed",
        "surface": {
            "closed": True, "illegal_triangles": 0,
            "unconnected_parts": 1, "triangles": 12,
        },
        "mesh": {
            "mesh_ok": True, "fatal": False, "failed_checks": 0,
            "concave_cells": 0, "cells": 100, "regions": 1,
            "min_volume_m3": 0.001, "total_volume_m3": 1.0,
            "max_non_orthogonality": 10.0, "max_skewness": 1.0,
        },
        "strict_diagnostics": {
            "mesh_ok": True, "fatal": False, "failed_checks": 0,
            "concave_cells": 0,
        },
        "layer": {
            "enabled": False, "extruded_faces": 0, "candidate_faces": 0,
            "coverage_ratio": 0.0, "added_cells": 0, "patches": [],
            "expected_patches": [],
        },
        "y_plus": {
            "status": "NOT_APPLICABLE", "target_min": 30,
            "target_max": 300, "measured_wall_area_ratio": None,
        },
        "patches": [],
        "default_faces": 0,
        "occ_volume_m3": 1.0,
        "mesh_volume_error_ratio": 0.0,
        "input": {
            "surface_manifest_sha256": _sha256(surface_copy),
            "mesh_input_sha256": _sha256(mesh_input_path),
        },
        "tools": {"openfoam_version": "OpenFOAM-v2606"},
    }


def _progress() -> dict:
    return {
        "schema_version": 1,
        "contract": "thermal_progress.v1",
        "latest_time_s": 3.0,
        "completed_duration_s": 3.0,
        "required_duration_s": 3.0,
        "remaining_duration_s": 0.0,
        "flow_through_time_s": 1.0,
        "minimum_flow_through_fraction": 3.0,
        "flow_through_fraction": 3.0,
        "runs_completed": 1,
        "total_runtime_seconds": 10.0,
        "last_solver_clock_seconds": 9.0,
        "last_solver_runtime_per_simulated_second": 3.0,
        "last_fixed_runtime_overhead_seconds": 1.0,
        "estimated_remaining_runs": 0,
        "estimate_status": "complete",
        "estimated_remaining_runtime_seconds": 0.0,
        "interactive_runtime_budget_seconds": 300.0,
        "interactive_budget_exceeded": False,
        "recommended_next_duration_s": 0.0,
        "checkpoint_wall_budget_seconds": 30.0,
        "checkpoint_rate_source": "measured_continuation",
        "checkpoint_rate_seconds_per_simulated_second": 3.0,
        "energy_balance": {
            "available": True, "history_complete": True,
            "input_energy_j": 100.0, "stored_sensible_energy_j": 50.0,
            "cumulative_exhaust_energy_j": 50.0,
            "transient_closure_ratio": 1.0,
        },
        "runs": [{
            "started_at": STAMP, "start_time_s": 0.0, "end_time_s": 3.0,
            "simulated_duration_s": 3.0, "runtime_seconds": 10.0,
            "solver_clock_seconds": 9.0,
        }],
    }


def _gci_manifest(case: Path) -> dict:
    provenance = {
        "run_manifest_sha256": _sha256(case / "run_manifest.json"),
        "result_manifest_sha256": _sha256(case / "result_manifest.json"),
        "mesh_manifest_sha256": _sha256(case / "mesh_manifest.json"),
        "thermal_input_sha256": _sha256(case / "thermal_input.json"),
    }
    cases = [{
        "path": str(case.resolve()), "cell_count": 100 * (index + 1),
        "metrics": {}, "diagnostics": {}, "time_window": {},
        "provenance": dict(provenance),
    } for index in range(4)]
    metric = {
        "key": "T", "label": "Temperature", "unit": "K", "fine": 295.0,
        "grid_values": [294, 294.5, 294.8, 295],
        "grid_width_ratios": [2, 1.5, 1.2], "convergence": "monotonic",
        "observed_order": 2.0, "extrapolated": 295.1,
        "uncertainty_method": "LSR", "uncertainty_fine": 0.1,
        "uncertainty_fine_pct": 0.04, "error_estimator": "GCI",
        "fit_weighting": "equal", "fit_standard_deviation": 0.01,
        "data_range_parameter": 0.1, "safety_factor": 1.25,
        "uncertainty_status": "PASS", "window_drift_pct": 0.1,
        "stationarity_limit_pct": 1.0, "stationarity_status": "PASS",
        "limit_pct": 5.0, "status": "PASS",
    }
    return {
        "schema_version": 3,
        "contract": "grid_convergence.v3",
        "engine": "body_fitted_thermal_mesh_uncertainty_lsr",
        "created_at": STAMP,
        "status": "PASS",
        "design_ready": True,
        "uncertainty_limit_pct": 5.0,
        "errors": [],
        "warnings": [],
        "comparison": {
            "method": "LSR", "method_doi": "fixture", "grid_count": 4,
            "minimum_flow_through_fraction": 3.0,
            "window_flow_through_fraction": 1.0,
            "refinement_ratios_fine_to_coarse": [1.2, 1.5, 2.0],
            "spatial_aggregation": "volume", "temporal_aggregation": "mean",
            "heat_source_contract": [{
                "name": "load", "source_element_ids": ["eq-1"],
                "power_kw": 1.0, "convective_fraction": 1.0,
                "applied_convective_power_w": 1000.0, "evidence": "confirmed",
            }],
        },
        "cases": cases,
        "metrics": [{**metric, "key": key, "label": key} for key in ("T", "U", "Q")],
    }


def make_complete_case(base: Path, *, with_gci: bool = False) -> dict[str, Path]:
    root = base / "projects"
    geometry = root / "imports" / "project" / "geometry.v2.json"
    surface_dir = root / "_occ_geometry" / "surface-a"
    mesh_dir = root / "_body_mesh" / "mesh-a"
    case = root / "_body_solver" / "case-a"
    _write_json(geometry, _geometry())

    surface_manifest = surface_dir / "surface_manifest.json"
    _write_json(surface_manifest, _surface(root, geometry, surface_dir))
    mesh_surface = mesh_dir / "surface_manifest.json"
    _copy(surface_manifest, mesh_surface)
    _copy(
        surface_dir / "air_volume_regions.stl",
        mesh_dir / "constant" / "triSurface" / "air_volume_regions.stl",
    )
    mesh_input = mesh_dir / "mesh_input.json"
    _write_json(mesh_input, {
        "schema_version": 1,
        "contract": "mesh_input.v1",
        "engine": "body_fitted_airflow",
        "surface_manifest_sha256": _sha256(mesh_surface),
        "surface_stl_sha256": _sha256(surface_dir / "air_volume_regions.stl"),
        "estimate": {"settings": {"preset": "detailed"}},
    })
    mesh_manifest = mesh_dir / "mesh_manifest.json"
    _write_json(mesh_manifest, _mesh(mesh_surface, mesh_input))

    for source in (mesh_surface, mesh_input, mesh_manifest):
        _copy(source, case / source.name)

    thermal = case / "thermal_input.json"
    settings = {"thermal_numerics_profile": "design_limited_second_order_v1"}
    numerics = {
        "profile": "design_limited_second_order_v1",
        "convection_order": 2,
        "laplacian_correction": "limited 0.5",
        "sn_grad_correction": "limited 0.5",
        "required_non_orthogonal_correctors": 2,
    }
    _write_json(thermal, {
        "schema_version": 1,
        "contract": "thermal_input.v1",
        "engine": "body_fitted_buoyant_urans",
        "created_at": STAMP,
        "mesh_manifest_sha256": _sha256(case / "mesh_manifest.json"),
        "settings": settings,
        "numerics": numerics,
        "airflow": {"supply_cmh": 3600.0},
        "terminals": [], "wall_patches": [], "heat_sources": [], "heat": {},
        "assumptions": {"radiation_modelled": False},
    })

    system = case / "system"
    system.mkdir(parents=True)
    (system / "controlDict").write_text(
        "application buoyantBoussinesqPimpleFoam;\n", encoding="ascii"
    )
    (system / "fvSchemes").write_text(
        "divSchemes\n{\n"
        "default none;\n"
        "div(phi,U) bounded Gauss linearUpwind grad(U);\n"
        "div(phi,T) bounded Gauss limitedLinear 1;\n"
        "div(phi,k) bounded Gauss limitedLinear 1;\n"
        "div(phi,omega) bounded Gauss limitedLinear 1;\n}\n"
        "laplacianSchemes { default Gauss linear limited 0.5; }\n"
        "snGradSchemes { default limited 0.5; }\n",
        encoding="ascii",
    )
    (system / "fvSolution").write_text(
        "PIMPLE { nCorrectors 2; nNonOrthogonalCorrectors 2; }\n",
        encoding="ascii",
    )
    progress = _progress()
    _write_json(case / "thermal_progress.json", progress)
    system_hashes = {
        name: _sha256(system / name)
        for name in ("controlDict", "fvSchemes", "fvSolution")
    }
    run = case / "run_manifest.json"
    _write_json(run, {
        "schema_version": 1,
        "contract": "run_manifest.v1",
        "engine": "body_fitted_buoyant_urans",
        "created_at": STAMP,
        "status": "PASS",
        "design_ready": True,
        "errors": [], "warnings": [], "solver": {}, "airflow": {},
        "terminals": [],
        "y_plus": {
            "available": True, "time": 3.0, "area_ratio_in_target": 1.0,
            "wall_treatment_acceptable_area_ratio": 1.0,
            "minimum": 30.0, "maximum": 100.0,
            "area_weighted_average": 60.0, "patches": [],
        },
        "effective_settings": settings,
        "effective_numerics": numerics,
        "numerical_quality": {
            "contract": "numerical_quality.v1", "status": "PASS",
            "design_ready": True, "profile": numerics["profile"],
            "convection_order": 2, "blockers": [],
        },
        "input": {
            "thermal_input_sha256": _sha256(thermal),
            "numerical_provenance": {
                "contract": "thermal_numerics_provenance.v1",
                "source": "thermal_initial_input",
                "thermal_input_sha256": _sha256(thermal),
                "thermal_restart_input_sha256": None,
                "effective_settings_sha256": _canonical_sha256(settings),
                "effective_numerics_sha256": _canonical_sha256(numerics),
                "expected_system": dict(system_hashes),
                "system": dict(system_hashes),
            },
        },
        "thermal_progress": progress,
    })

    results = case / "results"
    slices_dir = results / "slices"
    slices_dir.mkdir(parents=True)
    source_vtu = results / "internal.vtu"
    source_vtu.write_text("<VTKFile/>", encoding="ascii")
    summary = results / "body_fitted_summary.json"
    _write_json(summary, {
        "contract": "body_fitted_summary.v1", "time_s": 3.0,
        "cell_count": 100,
        "bounds_m": {"minimum": [0, 0, 0], "maximum": [1, 1, 1]},
        "fields": {"T": {"unit": "K"}, "U": {"unit": "m/s"}},
        "temperature": {"maximum": 295.0}, "velocity": {"p95_speed": 0.2},
    })
    slice_refs = []
    for axis in "xyz":
        slice_path = slices_dir / f"{axis}_mid.json"
        _write_json(slice_path, {
            "axis": axis, "target_m": 0.5, "sample_count": 0, "samples": [],
        })
        slice_refs.append({
            "axis": axis,
            "path": slice_path.relative_to(case).as_posix(),
            "sha256": _sha256(slice_path),
        })
    result = case / "result_manifest.json"
    _write_json(result, {
        "schema_version": 1,
        "contract": "result_manifest.v1",
        "engine": "body_fitted_openfoam_vtu",
        "created_at": STAMP,
        "time_s": 3.0,
        "source": {
            "path": source_vtu.relative_to(case).as_posix(),
            "sha256": _sha256(source_vtu),
            "format": "VTK XML UnstructuredGrid ASCII",
        },
        "field_location": "cell",
        "fields": {"T": {"unit": "K"}, "U": {"unit": "m/s"}},
        "summary_path": summary.relative_to(case).as_posix(),
        "summary_sha256": _sha256(summary),
        "slices": slice_refs,
        "mesh_manifest_sha256": _sha256(case / "mesh_manifest.json"),
        "run_manifest_sha256": _sha256(run),
        "thermal_input_sha256": _sha256(thermal),
    })

    gci_root = root / "_body_gci"
    if with_gci:
        _write_json(gci_root / "study-a" / "grid_convergence.json", _gci_manifest(case))
    return {
        "root": root, "geometry": geometry, "surface": surface_manifest,
        "mesh": mesh_manifest, "mesh_input": mesh_input, "case": case,
        "thermal": thermal, "progress": case / "thermal_progress.json",
        "run": run, "result": result, "source_vtu": source_vtu,
        "summary": summary, "gci_root": gci_root,
        "evidence": case / "case_evidence.v1.json",
    }


def make_valid_field_evidence(paths: dict[str, Path]) -> tuple[Path, Path]:
    import ezdxf

    source = paths["root"] / "imports" / "actual-site-unique.dxf"
    document = ezdxf.new("R2010")
    document.units = ezdxf.units.MM
    document.layers.add("ACTUAL-SITE")
    document.modelspace().add_lwpolyline(
        [(0, 0), (1000, 0), (1000, 1000), (0, 1000), (0, 0)],
        dxfattribs={"layer": "ACTUAL-SITE"},
    )
    document.saveas(source)

    geometry = _read_json(paths["geometry"])
    geometry["source"] = str(source.resolve())
    _write_json(paths["geometry"], geometry)

    surface = _read_json(paths["surface"])
    surface["source"]["geometry_path"] = str(paths["geometry"].resolve())
    surface["source"]["geometry_sha256"] = _sha256(paths["geometry"])
    _write_json(paths["surface"], surface)
    mesh_surface = paths["mesh"].parent / "surface_manifest.json"
    solver_surface = paths["case"] / "surface_manifest.json"
    _copy(paths["surface"], mesh_surface)
    _copy(paths["surface"], solver_surface)

    mesh_input = _read_json(paths["mesh_input"])
    mesh_input["surface_manifest_sha256"] = _sha256(mesh_surface)
    _write_json(paths["mesh_input"], mesh_input)
    mesh = _read_json(paths["mesh"])
    mesh["input"]["surface_manifest_sha256"] = _sha256(mesh_surface)
    mesh["input"]["mesh_input_sha256"] = _sha256(paths["mesh_input"])
    _write_json(paths["mesh"], mesh)
    _copy(paths["mesh_input"], paths["case"] / "mesh_input.json")
    _copy(paths["mesh"], paths["case"] / "mesh_manifest.json")

    thermal = _read_json(paths["thermal"])
    thermal["mesh_manifest_sha256"] = _sha256(paths["case"] / "mesh_manifest.json")
    _write_json(paths["thermal"], thermal)
    run = _read_json(paths["run"])
    run["input"]["thermal_input_sha256"] = _sha256(paths["thermal"])
    run["input"]["numerical_provenance"]["thermal_input_sha256"] = _sha256(
        paths["thermal"]
    )
    _write_json(paths["run"], run)
    result = _read_json(paths["result"])
    result["mesh_manifest_sha256"] = _sha256(paths["case"] / "mesh_manifest.json")
    result["run_manifest_sha256"] = _sha256(paths["run"])
    result["thermal_input_sha256"] = _sha256(paths["thermal"])
    _write_json(paths["result"], result)
    _write_json(
        paths["gci_root"] / "study-a" / "grid_convergence.json",
        _gci_manifest(paths["case"]),
    )

    evidence = paths["root"] / "_release_evidence" / "field_dxf" / "actual-site.json"
    built = cfd_evidence.field_acceptance.build_field_acceptance(
        source, paths["geometry"], paths["surface"].parent,
        paths["mesh"].parent, paths["case"], paths["root"], True, evidence,
    )
    assert built["ok"], built
    assert cfd_evidence.field_acceptance.validate_evidence(
        evidence, projects_root=paths["root"]
    )["ok"] is True
    return evidence, source


def _check(evidence: dict, check_id: str) -> dict:
    return next(item for item in evidence["checks"] if item["id"] == check_id)


def _codes(evidence: dict) -> set[str]:
    return {item["code"] for item in evidence.get("errors", [])}


def test_complete_core_chain_builds_schema_valid_screening_evidence(tmp_path):
    paths = make_complete_case(tmp_path)

    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"]
    )

    schema = _read_json(Path(cfd_evidence.__file__).with_name("case_evidence.v1.schema.json"))
    Draft202012Validator(schema).validate(evidence)
    assert evidence["status"] == "PASS"
    assert evidence["purpose"] == "screening"
    assert evidence["legacy_case_ref"]["case_id"].startswith("legacy-")
    assert _check(evidence, "grid_verified")["status"] == "NOT_EVALUATED"
    assert _check(evidence, "benchmark_validated")["status"] == "NOT_EVALUATED"
    assert _check(evidence, "field_calibrated")["status"] == "NOT_EVALUATED"
    assert paths["evidence"].is_file()
    assert all("\\" not in ref["path"] and not Path(ref["path"]).is_absolute()
               for ref in evidence["artifact_refs"].values())


def test_self_declared_pass_and_prior_evidence_are_not_source_evidence(tmp_path):
    paths = make_complete_case(tmp_path)
    paths["run"].unlink()
    _write_json(paths["evidence"], {"status": "PASS"})

    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"]
    )

    assert evidence["status"] == "BLOCKED"
    assert "MISSING_ARTIFACT" in _codes(evidence)


@pytest.mark.parametrize("key", ["geometry", "surface", "mesh", "run", "result"])
def test_missing_core_artifact_is_blocked(tmp_path, key):
    paths = make_complete_case(tmp_path)
    paths[key].unlink()

    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"]
    )

    assert evidence["status"] == "BLOCKED"
    assert "MISSING_ARTIFACT" in _codes(evidence)


def test_geometry_change_after_surface_is_blocked(tmp_path):
    paths = make_complete_case(tmp_path)
    geometry = _read_json(paths["geometry"])
    geometry["review"]["ready"] = False
    _write_json(paths["geometry"], geometry)

    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"]
    )

    assert evidence["status"] == "BLOCKED"
    assert "GEOMETRY_HASH_MISMATCH" in _codes(evidence)


def test_geometry_authority_requires_json_suffix(tmp_path):
    paths = make_complete_case(tmp_path)
    renamed = paths["geometry"].with_suffix(".txt")
    paths["geometry"].replace(renamed)
    for surface_path in (
        paths["surface"], paths["mesh"].parent / "surface_manifest.json",
        paths["case"] / "surface_manifest.json",
    ):
        surface = _read_json(surface_path)
        surface["source"]["geometry_path"] = renamed.relative_to(paths["root"]).as_posix()
        surface["source"]["geometry_sha256"] = _sha256(renamed)
        _write_json(surface_path, surface)

    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"]
    )

    assert _check(evidence, "geometry_valid")["status"] == "BLOCKED"
    assert "GEOMETRY_PATH_INVALID" in _codes(evidence)


def test_current_producer_absolute_geometry_source_is_contained_and_accepted(tmp_path):
    paths = make_complete_case(tmp_path)
    for surface_path in (
        paths["surface"], paths["mesh"].parent / "surface_manifest.json",
        paths["case"] / "surface_manifest.json",
    ):
        surface = _read_json(surface_path)
        surface["source"]["geometry_path"] = str(paths["geometry"].resolve())
        _write_json(surface_path, surface)

    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"]
    )

    assert _check(evidence, "geometry_valid")["status"] == "PASS"


def test_surface_declared_path_traversal_is_rejected_as_path_escape(tmp_path):
    paths = make_complete_case(tmp_path)
    for surface_path in (
        paths["surface"], paths["mesh"].parent / "surface_manifest.json",
        paths["case"] / "surface_manifest.json",
    ):
        surface = _read_json(surface_path)
        surface["outputs"]["multi_region_stl"] = "../air_volume_regions.stl"
        _write_json(surface_path, surface)

    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"]
    )

    assert evidence["status"] == "BLOCKED"
    assert "PATH_ESCAPE" in _codes(evidence)


@pytest.mark.parametrize("target", ["surface_output", "mesh_surface", "mesh_input"])
def test_surface_and_mesh_input_tampering_is_blocked(tmp_path, target):
    paths = make_complete_case(tmp_path)
    if target == "surface_output":
        path = paths["surface"].parent / "air_volume_regions.stl"
    elif target == "mesh_surface":
        path = paths["mesh"].parent / "surface_manifest.json"
    else:
        path = paths["mesh_input"]
    path.write_bytes(path.read_bytes() + b"tampered")

    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"]
    )

    assert evidence["status"] == "BLOCKED"
    assert _codes(evidence) & {
        "SURFACE_OUTPUT_HASH_MISMATCH", "MESH_SURFACE_CHAIN_MISMATCH",
        "MESH_INPUT_HASH_MISMATCH",
    }


@pytest.mark.parametrize("target", ["thermal", "fvSchemes"])
def test_thermal_or_system_mutation_is_blocked(tmp_path, target):
    paths = make_complete_case(tmp_path)
    path = paths["thermal"] if target == "thermal" else paths["case"] / "system" / target
    path.write_bytes(path.read_bytes() + b"tampered")

    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"]
    )

    assert evidence["status"] == "BLOCKED"
    assert _check(evidence, "numerics_verified")["status"] == "BLOCKED"


def test_self_consistent_upwind_claim_is_semantically_blocked(tmp_path):
    paths = make_complete_case(tmp_path)
    schemes = paths["case"] / "system" / "fvSchemes"
    schemes.write_text("divSchemes { default upwind; }\n", encoding="ascii")
    run = _read_json(paths["run"])
    digest = _sha256(schemes)
    run["input"]["numerical_provenance"]["system"]["fvSchemes"] = digest
    run["input"]["numerical_provenance"]["expected_system"]["fvSchemes"] = digest
    _write_json(paths["run"], run)
    result = _read_json(paths["result"])
    result["run_manifest_sha256"] = _sha256(paths["run"])
    _write_json(paths["result"], result)

    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"]
    )

    assert evidence["status"] == "BLOCKED"
    assert "NUMERICAL_PROVENANCE_INVALID" in _codes(evidence)


@pytest.mark.parametrize("target", ["run", "source_vtu", "summary", "slice"])
def test_result_chain_mutation_is_blocked(tmp_path, target):
    paths = make_complete_case(tmp_path)
    if target == "slice":
        path = paths["case"] / "results" / "slices" / "x_mid.json"
    else:
        path = paths[target]
    path.write_bytes(path.read_bytes() + b"tampered")

    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"]
    )

    assert evidence["status"] == "BLOCKED"
    assert _codes(evidence) & {
        "RESULT_RUN_HASH_MISMATCH", "RESULT_ARTIFACT_HASH_MISMATCH",
        "RUN_SCHEMA_INVALID",
    }


def test_missing_slice_axis_is_blocked_even_when_hashes_are_refreshed(tmp_path):
    paths = make_complete_case(tmp_path)
    result = _read_json(paths["result"])
    result["slices"] = result["slices"][:2]
    _write_json(paths["result"], result)

    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"]
    )

    assert evidence["status"] == "BLOCKED"
    assert "RESULT_SCHEMA_INVALID" in _codes(evidence) or "RESULT_SLICES_INVALID" in _codes(evidence)


def test_standalone_progress_is_canonical_and_must_equal_embedded_copy(tmp_path):
    paths = make_complete_case(tmp_path)
    progress = _read_json(paths["progress"])
    progress["remaining_duration_s"] = 1.0
    _write_json(paths["progress"], progress)

    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"]
    )

    assert evidence["status"] == "BLOCKED"
    assert "THERMAL_PROGRESS_MISMATCH" in _codes(evidence)


def test_impossible_progress_history_is_blocked_even_if_copies_match(tmp_path):
    paths = make_complete_case(tmp_path)
    progress = _read_json(paths["progress"])
    progress["runs"][0]["end_time_s"] = -1.0
    _write_json(paths["progress"], progress)
    run = _read_json(paths["run"])
    run["thermal_progress"] = progress
    _write_json(paths["run"], run)
    result = _read_json(paths["result"])
    result["run_manifest_sha256"] = _sha256(paths["run"])
    _write_json(paths["result"], result)

    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"]
    )

    assert evidence["status"] == "BLOCKED"
    assert "THERMAL_PROGRESS_INVALID" in _codes(evidence)


def test_unique_current_gci_passes_and_zero_match_is_not_evaluated(tmp_path):
    no_gci = make_complete_case(tmp_path / "none")
    with_gci = make_complete_case(tmp_path / "one", with_gci=True)

    absent = cfd_evidence.build_case_evidence(
        no_gci["case"], projects_root=no_gci["root"]
    )
    current = cfd_evidence.build_case_evidence(
        with_gci["case"], projects_root=with_gci["root"]
    )

    assert _check(absent, "grid_verified")["status"] == "NOT_EVALUATED"
    assert _check(current, "grid_verified")["status"] == "PASS"
    assert current["artifact_refs"]["gci"]["path"].endswith("grid_convergence.json")


def test_multiple_matching_gci_manifests_are_blocked(tmp_path):
    paths = make_complete_case(tmp_path, with_gci=True)
    source = paths["gci_root"] / "study-a" / "grid_convergence.json"
    _copy(source, paths["gci_root"] / "study-b" / "grid_convergence.json")

    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"]
    )

    assert _check(evidence, "grid_verified")["status"] == "BLOCKED"
    assert "AMBIGUOUS_GCI_EVIDENCE" in _codes(evidence)


def test_stale_other_case_gci_blocks_the_grid_authority(tmp_path):
    paths = make_complete_case(tmp_path, with_gci=True)
    manifest_path = paths["gci_root"] / "study-a" / "grid_convergence.json"
    manifest = _read_json(manifest_path)
    for item in manifest["cases"]:
        item["path"] = str(paths["case"].with_name("other-case"))
    _write_json(manifest_path, manifest)

    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"], gci_root=paths["gci_root"]
    )

    assert _check(evidence, "grid_verified")["status"] == "BLOCKED"
    assert "GCI_EVIDENCE_STALE" in _codes(evidence)
    assert "gci" not in evidence["artifact_refs"]
    assert cfd_evidence.validate_case_evidence(
        paths["evidence"], projects_root=paths["root"]
    ) == []


def test_default_gci_authority_with_only_other_case_is_not_evaluated(tmp_path):
    paths = make_complete_case(tmp_path, with_gci=True)
    manifest_path = paths["gci_root"] / "study-a" / "grid_convergence.json"
    manifest = _read_json(manifest_path)
    other_case = paths["root"] / "_body_solver" / "case-other"
    other_case.mkdir()
    for item in manifest["cases"]:
        item["path"] = str(other_case.resolve())
    _write_json(manifest_path, manifest)

    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"]
    )

    assert _check(evidence, "grid_verified")["status"] == "NOT_EVALUATED"
    assert "GCI_EVIDENCE_STALE" not in _codes(evidence)


def test_default_gci_authority_with_only_malformed_candidate_is_not_evaluated(tmp_path):
    paths = make_complete_case(tmp_path)
    _write_json(
        paths["gci_root"] / "unrelated" / "grid_convergence.json",
        {"contract": "grid_convergence.v3", "status": "PASS"},
    )

    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"]
    )

    assert _check(evidence, "grid_verified")["status"] == "NOT_EVALUATED"
    assert "GCI_SCHEMA_INVALID" not in _codes(evidence)


def test_default_gci_uses_one_current_match_despite_unrelated_malformed_candidate(tmp_path):
    paths = make_complete_case(tmp_path, with_gci=True)
    _write_json(
        paths["gci_root"] / "unrelated" / "grid_convergence.json",
        {"contract": "grid_convergence.v3", "status": "PASS"},
    )

    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"]
    )

    assert _check(evidence, "grid_verified")["status"] == "PASS"
    assert "GCI_SCHEMA_INVALID" not in _codes(evidence)


def test_supplied_gci_root_with_benchmark_shaped_manifest_is_blocked(tmp_path):
    paths = make_complete_case(tmp_path)
    _write_json(paths["gci_root"] / "copied" / "grid_convergence.json", {
        "contract": "radiation_manifest.v1", "status": "PASS"
    })

    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"], gci_root=paths["gci_root"]
    )

    assert _check(evidence, "grid_verified")["status"] == "BLOCKED"
    assert "GCI_SCHEMA_INVALID" in _codes(evidence)


def test_explicit_unreadable_canonical_gci_root_is_blocked_not_treated_as_absent(tmp_path):
    paths = make_complete_case(tmp_path)

    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"],
        gci_root=paths["gci_root"],
    )

    assert _check(evidence, "grid_verified")["status"] == "BLOCKED"
    assert "GCI_EVIDENCE_INVALID" in _codes(evidence)


def test_explicit_narrowed_gci_study_root_is_rejected(tmp_path):
    paths = make_complete_case(tmp_path, with_gci=True)

    with pytest.raises(ValueError, match="canonical projects_root/_body_gci"):
        cfd_evidence.build_case_evidence(
            paths["case"], projects_root=paths["root"],
            gci_root=paths["gci_root"] / "study-a",
        )


def test_canonical_gci_root_with_sibling_study_revalidates_cleanly(tmp_path):
    paths = make_complete_case(tmp_path, with_gci=True)
    source = paths["gci_root"] / "study-a" / "grid_convergence.json"
    sibling = paths["gci_root"] / "study-b" / "grid_convergence.json"
    _copy(source, sibling)
    source.unlink()

    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"], gci_root=paths["gci_root"]
    )

    assert _check(evidence, "grid_verified")["status"] == "PASS"
    assert cfd_evidence.validate_case_evidence(
        paths["evidence"], projects_root=paths["root"]
    ) == []


@pytest.mark.parametrize(
    ("namespace", "check_id"),
    [
        ("_occ_geometry", "geometry_valid"),
        ("_body_mesh", "mesh_checked"),
        ("_body_gci", "grid_verified"),
    ],
)
def test_unsafe_sibling_candidate_blocks_its_authority(tmp_path, namespace, check_id):
    paths = make_complete_case(tmp_path, with_gci=True)
    outside = tmp_path / f"outside-{namespace}"
    link = paths["root"] / namespace / "unsafe-linked-candidate"
    _directory_symlink(outside, link)

    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"]
    )

    assert _check(evidence, check_id)["status"] == "BLOCKED"
    assert "PATH_ESCAPE" in _codes(evidence)


def test_numerical_preparation_is_never_discovered_as_final_authority(tmp_path):
    paths = make_complete_case(tmp_path)
    _write_json(paths["case"] / "numerical_sensitivity.json", {
        "contract": "numerical_sensitivity.v1", "status": "PASS",
        "stage": "FROZEN_INPUTS",
    })

    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"]
    )

    assert "numerical_sensitivity" not in evidence["artifact_refs"]
    assert _check(evidence, "numerics_verified")["status"] == "PASS"


def test_absent_field_evidence_is_not_evaluated_but_supplied_bad_file_blocks(tmp_path):
    paths = make_complete_case(tmp_path)
    field = paths["root"] / "_release_evidence" / "field_dxf" / "bad.json"
    _write_json(field, {"status": "PASS"})

    absent = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"]
    )
    supplied = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"], field_evidence_path=field
    )

    assert _check(absent, "field_calibrated")["status"] == "NOT_EVALUATED"
    assert _check(supplied, "field_calibrated")["status"] == "BLOCKED"
    assert "FIELD_EVIDENCE_INVALID" in _codes(supplied)


def test_generated_namespace_cannot_supply_geometry(tmp_path):
    paths = make_complete_case(tmp_path)
    generated = paths["root"] / "_release_evidence" / "geometry.v2.json"
    _copy(paths["geometry"], generated)
    for surface_path in (
        paths["surface"], paths["mesh"].parent / "surface_manifest.json",
        paths["case"] / "surface_manifest.json",
    ):
        surface = _read_json(surface_path)
        surface["source"]["geometry_path"] = generated.relative_to(paths["root"]).as_posix()
        surface["source"]["geometry_sha256"] = _sha256(generated)
        _write_json(surface_path, surface)

    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"]
    )

    assert evidence["status"] == "BLOCKED"
    assert "GENERATED_SOURCE_EXCLUDED" in _codes(evidence)


def test_symlinked_geometry_is_rejected_before_hashing(tmp_path):
    paths = make_complete_case(tmp_path)
    outside = tmp_path / "outside-geometry.json"
    _copy(paths["geometry"], outside)
    link = paths["root"] / "imports" / "linked-geometry.json"
    try:
        os.symlink(outside, link)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    for surface_path in (
        paths["surface"], paths["mesh"].parent / "surface_manifest.json",
        paths["case"] / "surface_manifest.json",
    ):
        surface = _read_json(surface_path)
        surface["source"]["geometry_path"] = link.relative_to(paths["root"]).as_posix()
        surface["source"]["geometry_sha256"] = _sha256(outside)
        _write_json(surface_path, surface)

    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"]
    )

    assert evidence["status"] == "BLOCKED"
    assert "PATH_ESCAPE" in _codes(evidence)


def test_legacy_id_is_deterministic_across_projects_root_relocation(tmp_path):
    first = make_complete_case(tmp_path / "first")
    second = make_complete_case(tmp_path / "second")

    evidence_a = cfd_evidence.build_case_evidence(
        first["case"], projects_root=first["root"]
    )
    evidence_b = cfd_evidence.build_case_evidence(
        second["case"], projects_root=second["root"]
    )

    assert evidence_a["legacy_case_ref"] == evidence_b["legacy_case_ref"]


def test_rebuild_excludes_prior_output_and_is_stable_except_timestamp(tmp_path):
    paths = make_complete_case(tmp_path)
    first = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"]
    )
    _write_json(paths["evidence"], {"status": "PASS", "forged": True})

    second = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"]
    )

    assert first["artifact_refs"] == second["artifact_refs"]
    assert first["legacy_case_ref"] == second["legacy_case_ref"]
    assert first["checks"] == second["checks"]
    assert "case_evidence" not in second["artifact_refs"]


def test_validate_detects_changed_manifest_and_later_raw_source_mutation(tmp_path):
    manifest_case = make_complete_case(tmp_path / "manifest")
    cfd_evidence.build_case_evidence(
        manifest_case["case"], projects_root=manifest_case["root"]
    )
    manifest_case["result"].write_bytes(manifest_case["result"].read_bytes() + b" ")

    manifest_errors = cfd_evidence.validate_case_evidence(
        manifest_case["evidence"], projects_root=manifest_case["root"]
    )

    assert any(item["code"] == "ARTIFACT_HASH_MISMATCH" for item in manifest_errors)

    raw_case = make_complete_case(tmp_path / "raw")
    cfd_evidence.build_case_evidence(
        raw_case["case"], projects_root=raw_case["root"]
    )
    raw_case["source_vtu"].write_bytes(raw_case["source_vtu"].read_bytes() + b"tamper")

    raw_errors = cfd_evidence.validate_case_evidence(
        raw_case["evidence"], projects_root=raw_case["root"]
    )

    assert any(item["code"] == "EVIDENCE_RECOMPUTATION_MISMATCH" for item in raw_errors)


def test_validate_clean_evidence_recomputes_without_errors(tmp_path):
    paths = make_complete_case(tmp_path)
    cfd_evidence.build_case_evidence(paths["case"], projects_root=paths["root"])

    assert cfd_evidence.validate_case_evidence(
        paths["evidence"], projects_root=paths["root"]
    ) == []


def test_validate_rejects_edited_evidence_and_escaping_stored_ref(tmp_path):
    paths = make_complete_case(tmp_path)
    cfd_evidence.build_case_evidence(paths["case"], projects_root=paths["root"])
    evidence = _read_json(paths["evidence"])
    evidence["status"] = "BLOCKED"
    evidence["artifact_refs"]["result"]["path"] = "../outside.json"
    _write_json(paths["evidence"], evidence)

    errors = cfd_evidence.validate_case_evidence(
        paths["evidence"], projects_root=paths["root"]
    )

    assert {item["code"] for item in errors} & {
        "ARTIFACT_REF_INVALID", "EVIDENCE_SCHEMA_INVALID",
    }


def test_atomic_publish_flush_failure_cleans_same_parent_staging(tmp_path):
    paths = make_complete_case(tmp_path)
    output = paths["case"] / "custom-evidence.json"

    with mock.patch.object(cfd_evidence.os, "fsync", side_effect=OSError("disk")):
        with pytest.raises(OSError, match="disk"):
            cfd_evidence.build_case_evidence(
                paths["case"], projects_root=paths["root"], output_path=output
            )

    assert not output.exists()
    assert not list(output.parent.glob(f".{output.name}.*.tmp"))


def test_output_must_be_safe_and_cannot_overwrite_a_source(tmp_path):
    paths = make_complete_case(tmp_path)

    with pytest.raises(ValueError):
        cfd_evidence.build_case_evidence(
            paths["case"], projects_root=paths["root"], output_path=paths["geometry"]
        )
    with pytest.raises(ValueError):
        cfd_evidence.build_case_evidence(
            paths["case"], projects_root=paths["root"],
            output_path=tmp_path / "outside.json",
        )


@pytest.mark.parametrize(
    "raw_source",
    ["fvSchemes", "mesh_input", "surface_stl", "result_vtu", "result_slice"],
)
def test_output_cannot_overwrite_authoritative_raw_child(tmp_path, raw_source):
    paths = make_complete_case(tmp_path)
    targets = {
        "fvSchemes": paths["case"] / "system" / "fvSchemes",
        "mesh_input": paths["mesh_input"],
        "surface_stl": paths["surface"].parent / "air_volume_regions.stl",
        "result_vtu": paths["source_vtu"],
        "result_slice": paths["case"] / "results" / "slices" / "x_mid.json",
    }
    target = targets[raw_source]
    original = target.read_bytes()

    with pytest.raises(ValueError, match="source artifact"):
        cfd_evidence.build_case_evidence(
            paths["case"], projects_root=paths["root"], output_path=target
        )

    assert target.read_bytes() == original


def test_output_cannot_overwrite_rejected_geometry_candidate(tmp_path):
    paths = make_complete_case(tmp_path)
    renamed = paths["geometry"].with_suffix(".txt")
    paths["geometry"].replace(renamed)
    for surface_path in (
        paths["surface"], paths["mesh"].parent / "surface_manifest.json",
        paths["case"] / "surface_manifest.json",
    ):
        surface = _read_json(surface_path)
        surface["source"]["geometry_path"] = renamed.relative_to(paths["root"]).as_posix()
        surface["source"]["geometry_sha256"] = _sha256(renamed)
        _write_json(surface_path, surface)
    original = renamed.read_bytes()

    with pytest.raises(ValueError, match="source artifact"):
        cfd_evidence.build_case_evidence(
            paths["case"], projects_root=paths["root"], output_path=renamed
        )

    assert renamed.read_bytes() == original


def test_output_cannot_overwrite_valid_field_evidence_source_dxf(tmp_path):
    paths = make_complete_case(tmp_path, with_gci=True)
    field_evidence, source_dxf = make_valid_field_evidence(paths)
    original = source_dxf.read_bytes()

    with pytest.raises(ValueError, match="source artifact"):
        cfd_evidence.build_case_evidence(
            paths["case"], projects_root=paths["root"],
            field_evidence_path=field_evidence, output_path=source_dxf,
        )

    assert source_dxf.read_bytes() == original
