import hashlib
import json
import math
import os
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


REPO = Path(__file__).resolve().parents[1]
HEX64 = "^[0-9a-f]{64}$"


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_json_sha256(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _write_json(path, value):
    return _write(path, json.dumps(value, sort_keys=True, allow_nan=False))


def _canonical_tree_sha256(case):
    digest = hashlib.sha256()
    for path in sorted((row for row in case.rglob("*") if row.is_file()), key=lambda row: row.relative_to(case).as_posix()):
        relative = path.relative_to(case).as_posix()
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(bytes.fromhex(_sha256(path)))
    return digest.hexdigest()


def _scalar(values, *, supply=None, exhaust=None):
    body = "\n".join(str(value) for value in values)
    boundaries = ""
    for name, rows in (("supply", supply), ("exhaust", exhaust)):
        if rows is not None:
            data = "\n".join(str(value) for value in rows)
            boundaries += f"{name} {{ value nonuniform List<scalar> {len(rows)} ({data}); }}\n"
    return f"internalField nonuniform List<scalar> {len(values)} ({body});\nboundaryField {{ {boundaries} }}\n"


def _vector(values):
    body = "\n".join(f"({x} {y} {z})" for x, y, z in values)
    return f"internalField nonuniform List<vector> {len(values)} ({body});\n"


def _vtu(temperature, speed):
    return f"""<VTKFile type="UnstructuredGrid"><UnstructuredGrid>
<FieldData><DataArray Name="TimeValue" format="ascii">240</DataArray></FieldData>
<Piece NumberOfPoints="4" NumberOfCells="1">
<Points><DataArray Name="Points" NumberOfComponents="3" format="ascii">0 0 0  2 0 0  0 2 0  0 0 2</DataArray></Points>
<Cells><DataArray Name="connectivity" format="ascii">0 1 2 3</DataArray><DataArray Name="offsets" format="ascii">4</DataArray></Cells>
<CellData><DataArray Name="T" format="ascii">{temperature}</DataArray><DataArray Name="U" NumberOfComponents="3" format="ascii">{speed} 0 0</DataArray><DataArray Name="V" format="ascii">8</DataArray></CellData>
</Piece></UnstructuredGrid></VTKFile>"""


def _poly_mesh(case, exhaust_patches):
    poly = case / "constant" / "polyMesh"
    _write(poly / "points", "\n0\n(\n)\n")
    _write(poly / "faces", "\n0\n(\n)\n")
    _write(poly / "boundary", "\n".join(
        f"{patch} {{ nFaces 1; startFace {index}; }}"
        for index, patch in enumerate(exhaust_patches)
    ))
    _write(poly / "owner", f"\n{len(exhaust_patches)}\n(\n" + "\n".join("0" for _ in exhaust_patches) + "\n)\n")


def _case(root, name, *, temperature=303.15, speed=0.2, closure=1.0, execution_id=None):
    from cfd_working_room import build_working_room_geometry

    case = root / "_working_validation" / "working-room-v1" / name
    geometry = _write_json(case / "geometry.json", build_working_room_geometry())
    surface = _write_json(case / "surface_manifest.json", {
        "schema_version": 1, "contract": "surface_manifest.v1", "engine": "body_fitted_airflow",
        "source": {"geometry_path": geometry.relative_to(root).as_posix(), "geometry_sha256": _sha256(geometry),
                   "geometry_contract": "geometry.v2", "space_element_id": "working-room-air"},
        "tools": {"freecad": "synthetic", "occ": "synthetic", "python": "synthetic"},
        "transform": {"occ_units": "mm", "stl_units": "m", "scale": 0.001,
                      "origin_mm": [0, 0, 0], "rotation_deg": 0, "inverse": {}},
        "tessellation": {},
        "air_volume": {"valid": True, "solid_count": 1, "volume_m3": 8.0,
                       "boundary_area_m2": 24.0, "region_area_m2": 24.0,
                       "area_error_ratio": 0.0, "obstacle_count": 0, "location_in_mesh": [1, 1, 1]},
        "regions": [{"name": "supply", "role": "supply", "source_element_ids": ["working-room-supply"],
                     "area_m2": 0.0625, "aabb": {}, "triangle_count": 2, "normalized_triangle_hash": "a" * 64},
                    {"name": "exhaust", "role": "exhaust", "source_element_ids": ["working-room-exhaust"],
                     "area_m2": 0.0625, "aabb": {}, "triangle_count": 2, "normalized_triangle_hash": "b" * 64}],
        "topology": {"open_edges": 0, "non_manifold_edges": 0, "duplicate_triangles": 0, "watertight": True},
        "outputs": {}, "surface_hash": "c" * 64,
    })
    mesh_input = _write_json(case / "mesh_input.json", {
        "schema_version": 1, "contract": "mesh_input.v1", "engine": "body_fitted_airflow",
        "surface_manifest_sha256": _sha256(surface), "surface_stl_sha256": "3" * 64,
        "estimate": {"settings": {"preset": "detailed", "background_cell_m": 0.125}},
    })
    mesh = _write_json(case / "mesh_manifest.json", {
        "schema_version": 1, "contract": "mesh_manifest.v1", "engine": "body_fitted_airflow",
        "created_at": "2026-08-25T00:00:00Z", "status": "PASS", "errors": [], "warnings": [], "profile": "detailed",
        "surface": {"closed": True, "illegal_triangles": 0, "unconnected_parts": 1, "triangles": 12},
        "mesh": {"mesh_ok": True, "fatal": False, "failed_checks": [], "concave_cells": 0, "cells": 4096,
                 "regions": 1, "min_volume_m3": 0.001, "total_volume_m3": 8.0,
                 "max_non_orthogonality": 10.0, "max_skewness": 0.2},
        "strict_diagnostics": {"mesh_ok": True, "fatal": False, "failed_checks": [], "concave_cells": 0},
        "layer": {"enabled": True, "extruded_faces": 1, "candidate_faces": 1, "coverage_ratio": 1.0,
                  "added_cells": 1, "patches": [], "expected_patches": []},
        "y_plus": {"status": "PASS", "target_min": 30, "target_max": 300, "measured_wall_area_ratio": 1.0},
        "patches": [], "default_faces": 0, "occ_volume_m3": 8.0, "mesh_volume_error_ratio": 0.0,
        "input": {"surface_manifest_sha256": _sha256(surface),
                  "mesh_input_sha256": _sha256(mesh_input)}, "tools": {},
    })
    effective_settings = {
        "supply_temperature_k": 293.15, "initial_temperature_k": 293.15,
        "air_density_kg_m3": 1.0, "air_specific_heat_j_kg_k": 1000.0,
        "thermal_duration_s": 240.0, "thermal_delta_t_s": 0.02,
        "thermal_adjust_time_step": False,
        "thermal_numerics_profile": "design_limited_second_order_v1",
        "thermal_parallel_processes": 1,
    }
    effective_numerics = {
        "profile": "design_limited_second_order_v1", "convection_order": 2,
    }
    thermal = _write_json(case / "thermal_input.json", {
        "contract": "thermal_input.v1", "engine": "body_fitted_buoyant_urans",
        "mesh_manifest_sha256": _sha256(mesh),
        "settings": effective_settings,
        "numerics": effective_numerics,
        "terminals": [{"mesh_patch_name": "supply", "role": "supply", "flow_rate_m3_s": 0.1},
                      {"mesh_patch_name": "exhaust", "role": "exhaust", "flow_rate_m3_s": 0.1}],
        "heat": {"applied_convective_power_w": 1000.0},
    })
    control_dict = _write(case / "system" / "controlDict", """application buoyantBoussinesqPimpleFoam;
startFrom startTime;
startTime 0;
stopAt endTime;
endTime 240;
deltaT 0.02;
adjustTimeStep no;
""")
    fv_schemes = _write(case / "system" / "fvSchemes", "div(phi,U) bounded Gauss linearUpwind grad(U);\n")
    fv_solution = _write(case / "system" / "fvSolution", "PIMPLE { nOuterCorrectors 2; }\n")
    turbulence_properties = _write(
        case / "constant" / "turbulenceProperties",
        "simulationType RAS;\nRAS { RASModel kOmegaSST; turbulence on; }\n",
    )
    allrun = _write(case / "Allrun", "#!/bin/sh\nbuoyantBoussinesqPimpleFoam > log.buoyantBoussinesqPimpleFoam 2>&1\n")
    progress_value = {
        "schema_version": 1, "contract": "thermal_progress.v1", "latest_time_s": 240.0,
        "completed_duration_s": 240.0, "required_duration_s": 240.0, "remaining_duration_s": 0.0,
        "flow_through_time_s": 80.0, "minimum_flow_through_fraction": 3.0, "flow_through_fraction": 3.0,
        "runs_completed": 1, "total_runtime_seconds": 10.0, "last_solver_clock_seconds": 9.0,
        "last_solver_runtime_per_simulated_second": 0.04, "last_fixed_runtime_overhead_seconds": 1.0,
        "estimated_remaining_runs": 0, "estimate_status": "complete", "estimated_remaining_runtime_seconds": 0.0,
        "interactive_runtime_budget_seconds": 3600.0, "interactive_budget_exceeded": False,
        "recommended_next_duration_s": 0.0, "checkpoint_wall_budget_seconds": 1800.0,
        "checkpoint_rate_source": "measured_continuation", "checkpoint_rate_seconds_per_simulated_second": 0.04,
        "energy_balance": {"available": True, "history_complete": True, "input_energy_j": 240000.0,
                           "stored_sensible_energy_j": 0.0, "cumulative_exhaust_energy_j": 240000.0,
                           "transient_closure_ratio": closure},
        "runs": [{"start_time_s": 0.0, "end_time_s": 240.0}],
    }
    progress = _write_json(case / "thermal_progress.json", progress_value)
    run = _write_json(case / "run_manifest.json", {
        "schema_version": 1, "contract": "run_manifest.v1", "engine": "body_fitted_buoyant_urans",
        "created_at": "2026-08-25T00:00:00Z", "execution_id": execution_id or name,
        "status": "PASS", "design_ready": False, "errors": [], "warnings": [],
        "solver": {"ended": True, "fatal": False, "courant": {"peak_maximum": 0.8}},
        "airflow": {}, "terminals": [],
        "y_plus": {"available": True, "time": 240.0, "area_ratio_in_target": 1.0,
                   "wall_treatment_acceptable_area_ratio": 1.0, "minimum": 30.0, "maximum": 100.0,
                   "area_weighted_average": 60.0, "patches": []},
        "effective_settings": effective_settings, "effective_numerics": effective_numerics,
        "numerical_quality": {"contract": "numerical_quality.v1", "status": "SCREENING_ONLY",
                              "design_ready": False, "profile": "design_limited_second_order_v1",
                              "convection_order": 2, "blockers": ["SCREENING_ONLY"]},
        "input": {"thermal_input_sha256": _sha256(thermal), "numerical_provenance": {
            "contract": "thermal_numerics_provenance.v1", "source": "thermal_initial_input",
            "thermal_input_sha256": _sha256(thermal), "thermal_restart_input_sha256": None,
            "effective_settings_sha256": _canonical_json_sha256(effective_settings),
            "effective_numerics_sha256": _canonical_json_sha256(effective_numerics),
            "expected_system": {"controlDict": _sha256(control_dict), "fvSchemes": _sha256(fv_schemes),
                                "fvSolution": _sha256(fv_solution)},
            "system": {"controlDict": _sha256(control_dict), "fvSchemes": _sha256(fv_schemes),
                       "fvSolution": _sha256(fv_solution)}}},
        "thermal_progress": progress_value,
    })
    check_mesh = _write(case / "log.checkMesh", "Mesh OK.\nNumber of illegal cells: 0\n")
    solver_log = _write(case / "log.buoyantBoussinesqPimpleFoam", """Time = 0
Courant Number mean: 0.1 max: 0.8
time step continuity errors : sum local = 1e-9, global = 1e-9, cumulative = 1e-9
Solving for T, Initial residual = 1e-6, Final residual = 1e-8
Time = 240
Courant Number mean: 0.1 max: 0.8
time step continuity errors : sum local = 1e-9, global = 1e-9, cumulative = 1e-9
Solving for T, Initial residual = 1e-6, Final residual = 1e-8
ExecutionTime = 9 s ClockTime = 9 s
End
""")
    latest = case / "240"
    _poly_mesh(case, ["exhaust"])
    field_t = _write(latest / "T", _scalar([temperature], supply=[293.15], exhaust=[100.0]))
    field_u = _write(latest / "U", _vector([(speed, 0, 0)]))
    field_phi = _write(latest / "phi", _scalar([0.0], supply=[-0.1], exhaust=[0.1]))
    field_v = _write(latest / "V", _scalar([8.0]))
    vtu = _write(case / "results" / "internal.vtu", _vtu(temperature, speed))
    summary = _write_json(case / "results" / "body_fitted_summary.json", {
        "contract": "body_fitted_summary.v1", "time_s": 240.0, "cell_count": 1,
        "temperature": {"mean": temperature}, "velocity": {"mean_speed": speed},
    })
    slices = {}
    for axis in "xyz":
        slices[axis] = _write_json(case / "results" / "slices" / f"{axis}_mid.json", {
            "axis": axis, "target_m": 1.0, "sample_count": 1,
            "samples": [{"temperature_k": temperature, "speed_m_s": speed}],
        })
    report = _write(case / "body_fitted_report.html", "<html>SCREENING_ONLY</html>")
    result = _write_json(case / "result_manifest.json", {
        "schema_version": 1, "contract": "result_manifest.v1", "engine": "body_fitted_openfoam_vtu",
        "created_at": "2026-08-25T00:00:00Z", "time_s": 240.0,
        "source": {"path": vtu.relative_to(case).as_posix(), "sha256": _sha256(vtu), "format": "VTK XML ASCII"},
        "field_location": "cell", "fields": {"T": {}, "U": {}},
        "summary_path": summary.relative_to(case).as_posix(), "summary_sha256": _sha256(summary),
        "slices": [{"axis": axis, "path": slices[axis].relative_to(case).as_posix(), "sha256": _sha256(slices[axis])}
                   for axis in "xyz"],
        "mesh_manifest_sha256": _sha256(mesh), "run_manifest_sha256": _sha256(run),
        "thermal_input_sha256": _sha256(thermal),
    })
    paths = {
        "geometry": geometry, "surface": surface, "mesh_input": mesh_input, "mesh": mesh,
        "thermal_input": thermal, "control_dict": control_dict,
        "fv_schemes": fv_schemes, "fv_solution": fv_solution,
        "turbulence_properties": turbulence_properties, "allrun": allrun,
        "thermal_progress": progress, "run": run, "result": result, "check_mesh_log": check_mesh,
        "solver_log": solver_log, "field_t": field_t, "field_u": field_u, "field_phi": field_phi,
        "field_v": field_v, "vtu": vtu, "summary": summary, "slice_x": slices["x"],
        "slice_y": slices["y"], "slice_z": slices["z"], "report": report,
    }
    return case, paths


def _room_bundle(root):
    anchor, anchor_paths = _case(root, "anchor", execution_id="anchor-run")
    repeat, repeat_paths = _case(root, "repeat", temperature=303.16, speed=0.203, closure=1.004,
                                 execution_id="repeat-run")

    def record(case, paths):
        return {
            "case_path": case.relative_to(root).as_posix(),
            "case_tree_sha256": _canonical_tree_sha256(case),
            "artifacts": {key: {"path": path.relative_to(root).as_posix(), "sha256": _sha256(path)}
                          for key, path in paths.items()},
        }

    manifest = _write_json(root / "_working_validation" / "working-room-v1" / "working_room_acceptance.json", {
        "schema_version": 1, "contract": "working_room_acceptance.v1",
        "anchor": record(anchor, anchor_paths), "repeat": record(repeat, repeat_paths),
        "limits": {"minimum_physical_time_s": 240.0, "maximum_peak_courant": 1.0,
                   "maximum_terminal_phi_imbalance_ratio": 0.001,
                   "minimum_energy_closure_ratio": 0.95, "maximum_energy_closure_ratio": 1.05,
                   "maximum_mean_temperature_delta_k": 0.02, "maximum_mean_speed_delta_m_s": 0.005,
                   "maximum_energy_closure_delta_percentage_points": 0.5},
    })
    return manifest, {"anchor": (anchor, anchor_paths), "repeat": (repeat, repeat_paths)}


def _rehash_case_record(manifest, label, case, paths):
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload[label]["artifacts"] = {
        key: {"path": path.relative_to(manifest.parents[2]).as_posix(), "sha256": _sha256(path)}
        for key, path in paths.items()
    }
    payload[label]["case_tree_sha256"] = _canonical_tree_sha256(case)
    manifest.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def test_build_working_room_geometry_is_schema_valid_and_canonical():
    from cfd_working_room import build_working_room_geometry
    from geometry_v2 import validate_for_body_fitted

    geometry = build_working_room_geometry()
    schema = json.loads((REPO / "geometry.v2.schema.json").read_text(encoding="utf-8"))

    assert list(Draft202012Validator(schema).iter_errors(geometry)) == []
    assert geometry["contract"] == "geometry.v2"
    assert geometry["review"]["ready"] is True
    assert geometry["review"]["blocking"] is False
    assert geometry["review"]["blocker_count"] == 0
    assert validate_for_body_fitted(geometry) == []
    assert set(geometry["elements"]) == {
        "wall", "column", "slab", "zone", "opening",
        "pipe", "duct", "tray", "equipment",
    }
    assert len(geometry["elements"]["zone"]) == 1
    zone = geometry["elements"]["zone"][0]
    assert zone["points"] == [[0.0, 0.0], [2000.0, 0.0], [2000.0, 2000.0], [0.0, 2000.0]]
    assert zone["semantic"]["ceiling_height_mm"] == 2000.0

    terminals = [
        row for row in geometry["elements"]["equipment"]
        if row["semantic"]["kind"] == "air_terminal"
    ]
    assert [(row["semantic"]["role"], row["semantic"]["airflow_cmh"]) for row in terminals] == [
        ("supply", 360.0),
        ("exhaust", 360.0),
    ]
    assert all(row["semantic"]["host_surface"] for row in terminals)
    assert all(row["semantic"]["center_z_mm"] > 0 for row in terminals)
    assert all(row["semantic"]["normal"] for row in terminals)
    heat = [
        row for row in geometry["elements"]["equipment"]
        if row["semantic"]["role"] == "heat_source"
    ]
    assert len(heat) == 1
    assert heat[0]["semantic"]["convective_power_w"] == 1000.0


def test_working_room_schema_is_closed_and_requires_fixed_anchor_repeat(tmp_path):
    schema = json.loads((REPO / "working_room_acceptance.v1.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    manifest, _ = _room_bundle(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(payload)) == []

    payload["status"] = "PASS"
    assert list(Draft202012Validator(schema).iter_errors(payload))


def test_validate_working_room_recomputes_raw_artifacts_and_is_stable(tmp_path):
    from cfd_working_room import validate_working_room

    manifest, _ = _room_bundle(tmp_path)
    first = validate_working_room(manifest, tmp_path)
    second = validate_working_room(manifest, tmp_path)

    assert first == second
    assert first["check_id"] == "working_room_e2e"
    assert first["status"] == "PASS", first
    assert first["blockers"] == []
    assert first["metrics"] == {
        "anchor_energy_closure_ratio": 1.0,
        "anchor_mean_speed_m_s": 0.2,
        "anchor_mean_temperature_k": 303.15,
        "energy_closure_delta_percentage_points": 0.1,
        "mean_speed_delta_m_s": 0.003,
        "mean_temperature_delta_k": 0.01,
        "repeat_energy_closure_ratio": 1.001,
        "repeat_mean_speed_m_s": 0.203,
        "repeat_mean_temperature_k": 303.16,
    }
    assert set(first["evidence_sha256"]) == {
        path.relative_to(tmp_path).as_posix()
        for case_name in ("anchor", "repeat") for path in _[case_name][0].rglob("*") if path.is_file()
    } | {manifest.relative_to(tmp_path).as_posix()}
    assert all(__import__("re").fullmatch(HEX64, value) for value in first["evidence_sha256"].values())


def test_working_room_missing_manifest_is_blocked_not_an_exception(tmp_path):
    from cfd_working_room import validate_working_room

    result = validate_working_room(
        tmp_path / "_working_validation/working-room-v1/working_room_acceptance.json", tmp_path
    )

    assert result["status"] == "BLOCKED"
    assert result["blockers"] == ["WORKING_ROOM_MANIFEST_MISSING"]
    assert result["evidence_sha256"] == {}


def test_working_room_recomputes_the_canonical_two_metre_geometry(tmp_path):
    from cfd_working_room import validate_working_room

    manifest, cases = _room_bundle(tmp_path)
    case, paths = cases["anchor"]
    geometry = json.loads(paths["geometry"].read_text(encoding="utf-8"))
    geometry["elements"]["zone"][0]["semantic"]["ceiling_height_mm"] = 1999.0
    paths["geometry"].write_text(json.dumps(geometry, sort_keys=True), encoding="utf-8")
    _rehash_case_record(manifest, "anchor", case, paths)

    assert "ANCHOR_CANONICAL_GEOMETRY_INVALID" in validate_working_room(manifest, tmp_path)["blockers"]


@pytest.mark.parametrize(("target", "value"), (
    (("equipment", 0, "airflow_cmh"), 359.0),
    (("equipment", 2, "input_power_w"), 999.0),
))
def test_working_room_recomputes_terminal_flow_and_heat(target, value, tmp_path):
    from cfd_working_room import validate_working_room

    manifest, cases = _room_bundle(tmp_path)
    case, paths = cases["anchor"]
    geometry = json.loads(paths["geometry"].read_text(encoding="utf-8"))
    group, index, field = target
    geometry["elements"][group][index]["semantic"][field] = value
    paths["geometry"].write_text(json.dumps(geometry, sort_keys=True), encoding="utf-8")
    _rehash_case_record(manifest, "anchor", case, paths)

    assert "ANCHOR_CANONICAL_GEOMETRY_INVALID" in validate_working_room(manifest, tmp_path)["blockers"]


def test_working_room_recomputes_required_mesh_and_numerics(tmp_path):
    from cfd_working_room import validate_working_room

    manifest, cases = _room_bundle(tmp_path)
    case, paths = cases["repeat"]
    thermal = json.loads(paths["thermal_input"].read_text(encoding="utf-8"))
    thermal["numerics"].update(profile="first_order_v1", convection_order=1)
    paths["thermal_input"].write_text(json.dumps(thermal, sort_keys=True), encoding="utf-8")
    _rehash_case_record(manifest, "repeat", case, paths)

    blockers = validate_working_room(manifest, tmp_path)["blockers"]
    assert "REPEAT_NUMERICS_INVALID" in blockers
    assert "WORKING_ROOM_INPUT_FINGERPRINT_MISMATCH" in blockers


def test_working_room_input_fingerprint_includes_all_solver_configuration_files(tmp_path):
    from cfd_working_room import validate_working_room

    manifest, cases = _room_bundle(tmp_path)
    case, paths = cases["repeat"]
    turbulence = paths["turbulence_properties"]
    turbulence.write_text(
        turbulence.read_text(encoding="utf-8") + "// repeat-only drift\n",
        encoding="utf-8",
    )
    _rehash_case_record(manifest, "repeat", case, paths)

    assert "WORKING_ROOM_INPUT_FINGERPRINT_MISMATCH" in validate_working_room(
        manifest, tmp_path,
    )["blockers"]


@pytest.mark.parametrize("artifact, mutate", (
    ("surface", lambda value: value["tessellation"].update(repeat_only_tolerance=0.123)),
    ("mesh_input", lambda value: value.update(repeat_only_refinement={"level": 9})),
    ("thermal_input", lambda value: value.update(wall_patches=["repeat-only-wall"])),
))
def test_working_room_input_fingerprint_covers_complete_normalized_inputs(
        tmp_path, artifact, mutate):
    from cfd_working_room import validate_working_room

    manifest, cases = _room_bundle(tmp_path)
    case, paths = cases["repeat"]
    path = paths[artifact]
    value = json.loads(path.read_text(encoding="utf-8"))
    mutate(value)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    _rehash_case_record(manifest, "repeat", case, paths)

    assert "WORKING_ROOM_INPUT_FINGERPRINT_MISMATCH" in validate_working_room(
        manifest, tmp_path,
    )["blockers"]


def test_working_room_energy_closure_uses_only_the_hash_pinned_time(tmp_path):
    from cfd_working_room import validate_working_room

    manifest, cases = _room_bundle(tmp_path)
    case, paths = cases["anchor"]
    original_t = paths["field_t"].read_text(encoding="utf-8")
    original_phi = paths["field_phi"].read_text(encoding="utf-8")
    _write(case / "999" / "T", original_t)
    _write(case / "999" / "phi", original_phi)
    paths["field_t"].write_text(
        _scalar([293.15], supply=[293.15], exhaust=[100.0]), encoding="utf-8",
    )
    _rehash_case_record(manifest, "anchor", case, paths)

    blockers = validate_working_room(manifest, tmp_path)["blockers"]
    assert "ANCHOR_ENERGY_CLOSURE_INVALID" in blockers


def test_working_room_rejects_tampered_hash_and_altered_solver_log(tmp_path):
    from cfd_working_room import validate_working_room

    manifest, cases = _room_bundle(tmp_path)
    cases["anchor"][1]["solver_log"].write_text("End\n", encoding="utf-8")

    result = validate_working_room(manifest, tmp_path)

    assert result["status"] == "BLOCKED"
    assert "ANCHOR_ARTIFACT_HASH_MISMATCH:solver_log" in result["blockers"]
    assert "ANCHOR_SOLVER_LOG_INVALID" in result["blockers"]


def test_working_room_rejects_swapped_children_and_copied_run_identity(tmp_path):
    from cfd_working_room import validate_working_room

    manifest, _ = _room_bundle(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["anchor"], payload["repeat"] = payload["repeat"], payload["anchor"]
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    result = validate_working_room(manifest, tmp_path)
    assert "ANCHOR_CASE_PATH_INVALID" in result["blockers"]
    assert "REPEAT_CASE_PATH_INVALID" in result["blockers"]

    manifest, cases = _room_bundle(tmp_path / "second")
    repeat_run = cases["repeat"][1]["run"]
    repeat_payload = json.loads(repeat_run.read_text(encoding="utf-8"))
    repeat_payload["execution_id"] = "anchor-run"
    repeat_run.write_text(json.dumps(repeat_payload), encoding="utf-8")
    result = validate_working_room(manifest, tmp_path / "second")
    assert "RUN_EXECUTION_ID_NOT_INDEPENDENT" in result["blockers"]


def test_working_room_rejects_latest_outside_and_output_alias_refs(tmp_path):
    from cfd_working_room import validate_working_room

    manifest, _ = _room_bundle(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["anchor"]["artifacts"]["field_t"]["path"] = "latest/T"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    assert "WORKING_ROOM_MANIFEST_SCHEMA_INVALID" in validate_working_room(manifest, tmp_path)["blockers"]

    manifest, cases = _room_bundle(tmp_path / "outside")
    result = validate_working_room(manifest, tmp_path / "outside", evaluator_output_path=manifest)
    assert result["status"] == "BLOCKED"
    assert "EVALUATOR_OUTPUT_ALIASES_INPUT" in result["blockers"]
    result = validate_working_room(
        manifest, tmp_path / "outside", evaluator_output_path=cases["anchor"][1]["field_t"]
    )
    assert "EVALUATOR_OUTPUT_ALIASES_INPUT" in result["blockers"]


@pytest.mark.parametrize("artifact", (
    "geometry", "surface", "mesh_input", "mesh", "thermal_input", "control_dict",
    "fv_schemes", "fv_solution", "turbulence_properties", "allrun",
    "thermal_progress", "run", "result",
    "check_mesh_log", "solver_log", "field_t", "field_u", "field_phi", "field_v", "vtu",
    "summary", "slice_x", "slice_y", "slice_z", "report",
))
def test_every_working_room_artifact_hash_is_enforced(tmp_path, artifact):
    from cfd_working_room import validate_working_room

    manifest, _ = _room_bundle(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["anchor"]["artifacts"][artifact]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    assert f"ANCHOR_ARTIFACT_HASH_MISMATCH:{artifact}" in validate_working_room(manifest, tmp_path)["blockers"]


@pytest.mark.parametrize("artifact", (
    "geometry", "surface", "mesh_input", "mesh", "thermal_input", "control_dict",
    "fv_schemes", "fv_solution", "turbulence_properties", "allrun",
    "thermal_progress", "run", "result",
    "check_mesh_log", "solver_log", "field_t", "field_u", "field_phi", "field_v", "vtu",
    "summary", "slice_x", "slice_y", "slice_z", "report",
))
def test_every_working_room_artifact_ref_rejects_unsafe_latest(tmp_path, artifact):
    from cfd_working_room import validate_working_room

    manifest, _ = _room_bundle(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["anchor"]["artifacts"][artifact]["path"] = f"latest/{artifact}"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    assert "WORKING_ROOM_MANIFEST_SCHEMA_INVALID" in validate_working_room(manifest, tmp_path)["blockers"]


@pytest.mark.parametrize("limit", (
    "minimum_physical_time_s", "maximum_peak_courant", "maximum_terminal_phi_imbalance_ratio",
    "minimum_energy_closure_ratio", "maximum_energy_closure_ratio", "maximum_mean_temperature_delta_k",
    "maximum_mean_speed_delta_m_s", "maximum_energy_closure_delta_percentage_points",
))
def test_every_working_room_threshold_source_is_fixed_by_schema(tmp_path, limit):
    from cfd_working_room import validate_working_room

    manifest, _ = _room_bundle(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["limits"][limit] = 999.0
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    assert "WORKING_ROOM_MANIFEST_SCHEMA_INVALID" in validate_working_room(manifest, tmp_path)["blockers"]


@pytest.mark.parametrize("unsafe_ref", (
    "../outside/T", "C:/outside/T", "anchor\\T", "cache/T", "temp/T", "generated/T",
))
def test_working_room_rejects_escape_backslash_absolute_and_generated_refs(tmp_path, unsafe_ref):
    from cfd_working_room import validate_working_room

    manifest, _ = _room_bundle(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["anchor"]["artifacts"]["field_t"]["path"] = unsafe_ref
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    assert "WORKING_ROOM_MANIFEST_SCHEMA_INVALID" in validate_working_room(manifest, tmp_path)["blockers"]


def test_working_room_rejects_reparse_component(monkeypatch, tmp_path):
    import cfd_working_room

    manifest, cases = _room_bundle(tmp_path)
    unsafe = cases["anchor"][0]
    original = cfd_working_room._is_reparse
    monkeypatch.setattr(cfd_working_room, "_is_reparse", lambda path: Path(path) == unsafe or original(Path(path)))

    result = cfd_working_room.validate_working_room(manifest, tmp_path)
    assert "ANCHOR_CASE_INVALID" in result["blockers"]


def test_working_room_detects_post_load_mutation(monkeypatch, tmp_path):
    import cfd_working_room

    manifest, cases = _room_bundle(tmp_path)
    original = cfd_working_room._case_metrics

    def mutate_after_repeat(label, record, root, evidence, blockers):
        result = original(label, record, root, evidence, blockers)
        if label == "repeat":
            cases["anchor"][1]["solver_log"].write_text("changed after load", encoding="utf-8")
        return result

    monkeypatch.setattr(cfd_working_room, "_case_metrics", mutate_after_repeat)
    result = cfd_working_room.validate_working_room(manifest, tmp_path)
    assert any(code.startswith("POST_LOAD_MUTATION:") for code in result["blockers"])


def test_working_room_rejects_duplicate_json_keys(tmp_path):
    from cfd_working_room import validate_working_room

    manifest, _ = _room_bundle(tmp_path)
    text = manifest.read_text(encoding="utf-8")
    manifest.write_text(text.replace(
        '"schema_version": 1', '"schema_version": 1, "schema_version": 1', 1,
    ), encoding="utf-8")

    result = validate_working_room(manifest, tmp_path)
    assert result["blockers"] == ["WORKING_ROOM_MANIFEST_MALFORMED"]


def test_working_room_stops_after_manifest_structure_error(tmp_path):
    from cfd_working_room import validate_working_room

    manifest, _ = _room_bundle(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["anchor"] = "not-an-object"
    manifest.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")

    result = validate_working_room(manifest, tmp_path)
    assert result["blockers"] == ["WORKING_ROOM_MANIFEST_SCHEMA_INVALID"]


def test_working_room_rejects_output_anywhere_inside_authoritative_case(tmp_path):
    from cfd_working_room import validate_working_room

    manifest, cases = _room_bundle(tmp_path)
    output = cases["anchor"][0] / "new-evaluator-output.json"
    result = validate_working_room(manifest, tmp_path, evaluator_output_path=output)

    assert "EVALUATOR_OUTPUT_ALIASES_INPUT" in result["blockers"]


def test_working_room_detects_post_load_case_file_addition(monkeypatch, tmp_path):
    import cfd_working_room

    manifest, cases = _room_bundle(tmp_path)
    original = cfd_working_room._case_metrics

    def add_file_after_anchor(label, record, root, evidence, blockers):
        result = original(label, record, root, evidence, blockers)
        if label == "anchor":
            room_fixture_path = cases["anchor"][0] / "added-after-enumeration.txt"
            room_fixture_path.write_text("late", encoding="utf-8")
        return result

    monkeypatch.setattr(cfd_working_room, "_case_metrics", add_file_after_anchor)
    result = cfd_working_room.validate_working_room(manifest, tmp_path)
    assert "POST_LOAD_CASE_TREE_CHANGED:anchor" in result["blockers"]


def test_working_room_malformed_thermal_rows_are_blocked_without_crashing(tmp_path):
    from cfd_working_room import validate_working_room

    manifest, cases = _room_bundle(tmp_path)
    case, paths = cases["anchor"]
    thermal = json.loads(paths["thermal_input"].read_text(encoding="utf-8"))
    thermal["terminals"] = ["not-a-terminal"]
    paths["thermal_input"].write_text(json.dumps(thermal, sort_keys=True), encoding="utf-8")
    _rehash_case_record(manifest, "anchor", case, paths)

    result = validate_working_room(manifest, tmp_path)
    assert result["status"] == "BLOCKED"
    assert "ANCHOR_THERMAL_INPUT_INVALID" in result["blockers"]


def test_working_room_missing_terminal_role_is_blocked_without_sort_crash(tmp_path):
    from cfd_working_room import validate_working_room

    manifest, cases = _room_bundle(tmp_path)
    case, paths = cases["anchor"]
    thermal = json.loads(paths["thermal_input"].read_text(encoding="utf-8"))
    thermal["terminals"][0].pop("role")
    paths["thermal_input"].write_text(json.dumps(thermal, sort_keys=True), encoding="utf-8")
    _rehash_case_record(manifest, "anchor", case, paths)

    result = validate_working_room(manifest, tmp_path)
    assert result["status"] == "BLOCKED"
    assert "ANCHOR_THERMAL_INPUT_INVALID" in result["blockers"]


@pytest.mark.parametrize("malformation, expected_blocker", (
    ("summary", "ANCHOR_SUMMARY_INVALID"),
    ("slice", "ANCHOR_SLICE_INVALID:x"),
))
def test_working_room_malformed_result_types_are_blocked_without_crashing(
        tmp_path, malformation, expected_blocker):
    from cfd_working_room import validate_working_room

    manifest, cases = _room_bundle(tmp_path)
    case, paths = cases["anchor"]
    path = paths["summary"] if malformation == "summary" else paths["slice_x"]
    value = json.loads(path.read_text(encoding="utf-8"))
    if malformation == "summary":
        value["temperature"] = "not-an-object"
    else:
        value["sample_count"] = [1]
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    _rehash_case_record(manifest, "anchor", case, paths)

    result = validate_working_room(manifest, tmp_path)
    assert result["status"] == "BLOCKED"
    assert expected_blocker in result["blockers"]


def test_working_room_rejects_non_json_numeric_constants(tmp_path):
    from cfd_working_room import validate_working_room

    manifest, _ = _room_bundle(tmp_path)
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace('"schema_version": 1', '"schema_version": NaN', 1),
        encoding="utf-8",
    )

    result = validate_working_room(manifest, tmp_path)
    assert result["blockers"] == ["WORKING_ROOM_MANIFEST_MALFORMED"]
