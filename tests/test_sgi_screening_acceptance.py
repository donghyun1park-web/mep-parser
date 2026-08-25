import json
import shutil
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import test_cfd_working_room as room_fixture


REPO = Path(__file__).resolve().parents[1]


def _sgi_geometry(source_path, source_sha256, *, authority="non_authoritative_working_fixture",
                  heat_w=15500.0):
    from cfd_working_room import build_working_room_geometry
    from geometry_v2 import build_review

    value = build_working_room_geometry()
    value["source"] = source_path
    value["source_sha256"] = source_sha256
    template = value["elements"]["equipment"][0]
    terminals = []
    for role, normal in (("supply", [1.0, 0.0, 0.0]), ("exhaust", [-1.0, 0.0, 0.0])):
        for index in range(15):
            item = json.loads(json.dumps(template))
            item["id"] = f"sgi-{role}-{index + 1:02d}"
            item["source_ref"].update(
                handle=item["id"], handles=[item["id"]], block_name=item["id"],
                entity_type="INSERT",
            )
            item["kind"] = "circle"
            item["center"] = [250.0 + (index % 5) * 350.0,
                              250.0 + (index // 5) * 500.0 + (0.0 if role == "supply" else 150.0)]
            item["radius"] = 100.0
            item["semantic"].update(
                role=role, airflow_cmh=444.0, diameter_mm=200.0,
                host_surface="ceiling", normal=normal,
            )
            item["semantic"].pop("center_z_mm", None)
            terminals.append(item)
    heat = value["elements"]["equipment"][2]
    heat["id"] = "manual_heat_sgi_01" if authority == "non_authoritative_working_fixture" else "sgi-schedule-heat-01"
    heat["source_ref"] = (
        {"handle": None, "handles": [], "layer": "USER_CONFIRMED",
         "block_name": heat["id"], "entity_type": "UI_INPUT", "source_id": heat["id"]}
        if authority == "non_authoritative_working_fixture"
        else {"handle": "HT-SGI-01", "handles": ["HT-SGI-01"], "layer": "M-EQPM",
              "block_name": "SGI_HEAT_01", "entity_type": "INSERT"}
    )
    heat["semantic"].update(
        input_power_w=heat_w, power_kw=heat_w / 1000.0,
        convective_fraction=1.0, radiative_fraction=0.0,
        convective_power_w=heat_w, radiative_power_w=0.0,
        evidence=(f"non_authoritative_working_fixture:{heat['id']}"
                  if authority == "non_authoritative_working_fixture"
                  else "site_schedule:M03-001"),
        override_of_dxf=False,
    )
    value["elements"]["equipment"] = terminals + [heat]
    value["scenario_authority"] = authority
    value["validation_fixture_only"] = authority == "non_authoritative_working_fixture"
    value["review"] = build_review(value)
    return value


def _opening_poly_mesh(case, patches, *, side_m=0.2):
    poly = case / "constant" / "polyMesh"
    points, faces, boundaries = [], [], []
    for index, patch in enumerate(patches):
        x = float(index * 2)
        start = len(points)
        points.extend(((x, 0.0, 0.0), (x + side_m, 0.0, 0.0),
                       (x + side_m, side_m, 0.0), (x, side_m, 0.0)))
        faces.append(f"4({start} {start + 1} {start + 2} {start + 3})")
        boundaries.append(f"{patch} {{ type patch; nFaces 1; startFace {index}; }}")
    room_fixture._write(poly / "points", f"\n{len(points)}\n(\n" + "\n".join(
        f"({x} {y} {z})" for x, y, z in points
    ) + "\n)\n")
    room_fixture._write(poly / "faces", f"\n{len(faces)}\n(\n" + "\n".join(faces) + "\n)\n")
    room_fixture._write(poly / "boundary", f"\n{len(boundaries)}\n(\n" + "\n".join(boundaries) + "\n)\n")
    room_fixture._write(poly / "owner", f"\n{len(faces)}\n(\n" + "\n".join("0" for _ in faces) + "\n)\n")


def _sgi_bundle(root, *, authority="non_authoritative_working_fixture", heat_w=15500.0):
    flow = 444.0 / 3600.0
    exhaust_total = 15 * flow
    temperature = 293.15 + heat_w / (1000.0 * exhaust_total)
    temporary, _ = room_fixture._case(root, "sgi-temporary", temperature=temperature,
                                      speed=0.2, closure=1.0, execution_id="sgi-final-run")
    case = root / "_body_solver" / "sgi-field-design"
    case.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(temporary), str(case))
    source_dxf = room_fixture._write(root / "_imports" / "한국-SGI-lobby.dxf", "0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n")
    source_rel = source_dxf.relative_to(root).as_posix()
    geometry = room_fixture._write_json(
        case / "geometry.json",
        _sgi_geometry(source_rel, room_fixture._sha256(source_dxf), authority=authority, heat_w=heat_w),
    )
    surface_path = case / "surface_manifest.json"
    surface = json.loads(surface_path.read_text(encoding="utf-8"))
    surface["source"].update(geometry_path=geometry.relative_to(root).as_posix(), geometry_sha256=room_fixture._sha256(geometry))
    geometry_payload = json.loads(geometry.read_text(encoding="utf-8"))
    terminal_elements = [row for row in geometry_payload["elements"]["equipment"]
                         if row["semantic"]["kind"] == "air_terminal"]
    heat_element = next(row for row in geometry_payload["elements"]["equipment"]
                        if row["semantic"]["role"] == "heat_source")
    surface["regions"] = [{
        "name": row["id"], "role": row["semantic"]["role"],
        "source_element_ids": [row["id"]], "airflow_cmh": 444.0,
        "design_normal": row["semantic"]["normal"], "area_m2": 0.04,
        "aabb": {}, "triangle_count": 2,
        "normalized_triangle_hash": room_fixture.hashlib.sha256(row["id"].encode()).hexdigest(),
    } for row in terminal_elements]
    surface["regions"].append({
        "name": "equipment_" + heat_element["id"], "role": "heat_source",
        "source_element_ids": [heat_element["id"]], "area_m2": 1.0,
        "aabb": {}, "triangle_count": 2, "normalized_triangle_hash": "9" * 64,
        "source_id": heat_element["id"], "source_label": heat_element["id"],
        "source_ref": heat_element["source_ref"], "source_type": "user_confirmed",
        "evidence": heat_element["semantic"]["evidence"], "override_of_dxf": False,
        "power_kw": heat_w / 1000.0, "input_power_w": heat_w,
        "convective_fraction": 1.0, "radiative_fraction": 0.0,
        "convective_power_w": heat_w, "radiative_power_w": 0.0,
        "excluded_radiative_power_w": 0.0,
    })
    surface_path.write_text(json.dumps(surface, sort_keys=True), encoding="utf-8")

    mesh_input_path = case / "mesh_input.json"
    mesh_input = json.loads(mesh_input_path.read_text(encoding="utf-8"))
    mesh_input["surface_manifest_sha256"] = room_fixture._sha256(surface_path)
    mesh_input_path.write_text(json.dumps(mesh_input, sort_keys=True), encoding="utf-8")
    mesh_path = case / "mesh_manifest.json"
    mesh = json.loads(mesh_path.read_text(encoding="utf-8"))
    mesh["input"] = {"surface_manifest_sha256": room_fixture._sha256(surface_path),
                     "mesh_input_sha256": room_fixture._sha256(mesh_input_path)}
    mesh["patches"] = [{
        "name": row["name"], "role": row["role"], "mesh_patch_name": row["name"],
        "faces": 1, "mesh_area_m2": row["area_m2"], "occ_area_m2": row["area_m2"],
        "area_error_ratio": 0.0,
    } for row in surface["regions"]]
    mesh_path.write_text(json.dumps(mesh, sort_keys=True), encoding="utf-8")

    terminals = []
    phi_supply, phi_exhaust, temp_supply, temp_exhaust = [], [], [], []
    for role in ("supply", "exhaust"):
        for index in range(15):
            patch = f"sgi-{role}-{index + 1:02d}"
            terminals.append({"name": patch, "mesh_patch_name": patch, "role": role,
                              "flow_rate_m3_s": flow,
                              "source_element_id": patch, "airflow_cmh": 444.0})
            if role == "supply":
                phi_supply.append((patch, [-flow]))
                temp_supply.append((patch, [293.15]))
            else:
                phi_exhaust.append((patch, [flow]))
                temp_exhaust.append((patch, [100.0]))
    thermal_path = case / "thermal_input.json"
    thermal = json.loads(thermal_path.read_text(encoding="utf-8"))
    thermal["mesh_manifest_sha256"] = room_fixture._sha256(mesh_path)
    thermal["terminals"] = terminals
    thermal["heat_sources"] = [{
        "source_id": heat_element["id"], "name": "equipment_" + heat_element["id"],
        "mesh_patch_name": "equipment_" + heat_element["id"],
        "source_ref": heat_element["source_ref"], "source_type": "user_confirmed",
        "evidence": heat_element["semantic"]["evidence"], "input_power_w": heat_w,
        "convective_fraction": 1.0, "radiative_fraction": 0.0,
        "convective_power_w": heat_w, "radiative_power_w": 0.0,
        "override_of_dxf": False,
    }]
    thermal["heat"]["applied_convective_power_w"] = 15500.0
    thermal["heat"]["applied_convective_power_w"] = heat_w
    thermal["scenario_authority"] = authority
    thermal["validation_fixture_only"] = authority == "non_authoritative_working_fixture"
    thermal_path.write_text(json.dumps(thermal, sort_keys=True), encoding="utf-8")

    latest = case / "240"
    room_fixture._write(latest / "phi", room_fixture._scalar([0.0])[:-2] + "\n".join(
        f"{patch} {{ value nonuniform List<scalar> 1 ({values[0]}); }}"
        for patch, values in phi_supply + phi_exhaust
    ) + " }\n")
    room_fixture._write(latest / "T", room_fixture._scalar([temperature])[:-2] + "\n".join(
        f"{patch} {{ value nonuniform List<scalar> 1 ({values[0]}); }}"
        for patch, values in temp_supply + temp_exhaust
    ) + " }\n")
    _opening_poly_mesh(case, [patch for patch, _ in phi_supply + phi_exhaust])

    run_path = case / "run_manifest.json"
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["input"]["thermal_input_sha256"] = room_fixture._sha256(thermal_path)
    run["input"]["numerical_provenance"]["thermal_input_sha256"] = room_fixture._sha256(thermal_path)
    run_path.write_text(json.dumps(run, sort_keys=True), encoding="utf-8")
    result_path = case / "result_manifest.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["run_manifest_sha256"] = room_fixture._sha256(run_path)
    result["mesh_manifest_sha256"] = room_fixture._sha256(mesh_path)
    result["thermal_input_sha256"] = room_fixture._sha256(thermal_path)
    result_path.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    report = case / "body_fitted_report.html"
    report.write_text("<html>SCREENING_ONLY</html>", encoding="utf-8")

    opening_rows = [{
        "opening_id": row["source_element_id"], "parent_name": row["name"],
        "role": row["role"], "flow_control": ("fixed_normal_velocity" if row["role"] == "supply" else "pressure_outlet"),
        "child_patch_names": [row["mesh_patch_name"]], "snapped_area_m2": 0.04,
        "design_cmh": 444.0,
    } for row in terminals]
    case_meta = room_fixture._write_json(case / "cfd_case_meta.json", {
        "config": {}, "patches": [],
        "opening_preflight": {"contract": "opening_preflight.v2", "terminals": opening_rows},
    })
    opening_verification = room_fixture._write_json(case / "opening_boundary_verification.v1.json", {
        "contract": "opening_boundary_verification.v1", "status": "PASS", "phi_time": "240",
        "terminals": [{"opening_id": row["opening_id"], "area_ratio": 1.0,
                       "flow_ratio": 1.0, "area_status": "PASS", "flow_status": "PASS"}
                      for row in opening_rows],
    })
    runner_log = room_fixture._write(case / "log.runner", "Maximum resident set size (kbytes): 1\n")

    paths = {
        "geometry": geometry, "surface": surface_path, "mesh_input": mesh_input_path,
        "mesh": mesh_path,
        "thermal_input": thermal_path, "thermal_progress": case / "thermal_progress.json",
        "run": run_path, "result": result_path, "check_mesh_log": case / "log.checkMesh",
        "solver_log": case / "log.buoyantBoussinesqPimpleFoam", "field_t": latest / "T",
        "field_u": latest / "U", "field_phi": latest / "phi", "field_v": latest / "V",
        "control_dict": case / "system/controlDict", "fv_schemes": case / "system/fvSchemes",
        "fv_solution": case / "system/fvSolution",
        "turbulence_properties": case / "constant/turbulenceProperties", "allrun": case / "Allrun",
        "vtu": case / "results/internal.vtu", "summary": case / "results/body_fitted_summary.json",
        "slice_x": case / "results/slices/x_mid.json", "slice_y": case / "results/slices/y_mid.json",
        "slice_z": case / "results/slices/z_mid.json", "report": report,
        "case_meta": case_meta, "opening_verification": opening_verification, "runner_log": runner_log,
    }
    job_path = root / "_field_jobs" / "field-aaaaaaaaaaaa" / "field_pipeline_job.json"
    evidence_dir = job_path.parent / "acceptance"
    resource_preflight = room_fixture._write_json(evidence_dir / "resource_preflight.json", {
        "contract": "field_resource_preflight.v1", "captured_at": "2026-08-25T00:15:00Z",
        "available_ram_bytes": 10240, "free_disk_bytes": 1000000,
        "estimated_peak_ram_bytes": 4096, "estimated_output_bytes": 10000,
    })
    checkpoint = case / "80"
    room_fixture._write(checkpoint / "T", room_fixture._scalar([296.0]))
    room_fixture._write(checkpoint / "U", room_fixture._vector([(0.1, 0, 0)]))
    room_fixture._write(checkpoint / "phi", room_fixture._scalar([0.0])[:-2] + "\n".join(
        f"{patch} {{ value nonuniform List<scalar> 1 ({values[0]}); }}"
        for patch, values in phi_supply + phi_exhaust
    ) + " }\n")
    checkpoint_log = room_fixture._write(
        evidence_dir / "attempt-1-solver.log",
        "Time = 0\nTime = 80\nCourant Number mean: 0.1 max: 0.8\nExecutionTime = 3 s ClockTime = 3 s\n",
    )
    pre_snapshot = room_fixture._write_json(evidence_dir / "attempt-1.json", {
        "contract": "field_attempt_snapshot.v1", "job": "field-aaaaaaaaaaaa",
        "attempt": 1, "phase": "pre_resume", "state": "interrupted_checkpoint",
        "case_path": case.relative_to(root).as_posix(),
        "geometry": {"path": geometry.relative_to(root).as_posix(), "sha256": room_fixture._sha256(geometry)},
        "thermal_input": {"path": thermal_path.relative_to(root).as_posix(), "sha256": room_fixture._sha256(thermal_path)},
        "solver_log": {"path": checkpoint_log.relative_to(root).as_posix(), "sha256": room_fixture._sha256(checkpoint_log)},
        "field_t": {"path": (checkpoint / "T").relative_to(root).as_posix(), "sha256": room_fixture._sha256(checkpoint / "T")},
        "field_u": {"path": (checkpoint / "U").relative_to(root).as_posix(), "sha256": room_fixture._sha256(checkpoint / "U")},
        "field_phi": {"path": (checkpoint / "phi").relative_to(root).as_posix(), "sha256": room_fixture._sha256(checkpoint / "phi")},
    })
    post_snapshot = room_fixture._write_json(evidence_dir / "attempt-2.json", {
        "contract": "field_attempt_snapshot.v1", "job": "field-aaaaaaaaaaaa",
        "attempt": 2, "phase": "post_resume", "state": "analysis_complete_not_citable",
        "case_path": case.relative_to(root).as_posix(),
        "geometry": {"path": geometry.relative_to(root).as_posix(), "sha256": room_fixture._sha256(geometry)},
        "thermal_input": {"path": thermal_path.relative_to(root).as_posix(), "sha256": room_fixture._sha256(thermal_path)},
        "solver_log": {"path": paths["solver_log"].relative_to(root).as_posix(), "sha256": room_fixture._sha256(paths["solver_log"])},
        "field_t": {"path": paths["field_t"].relative_to(root).as_posix(), "sha256": room_fixture._sha256(paths["field_t"])},
        "field_u": {"path": paths["field_u"].relative_to(root).as_posix(), "sha256": room_fixture._sha256(paths["field_u"])},
        "field_phi": {"path": paths["field_phi"].relative_to(root).as_posix(), "sha256": room_fixture._sha256(paths["field_phi"])},
    })
    process_audit = room_fixture._write_json(evidence_dir / "process-audit.json", {
        "contract": "field_resume_process_audit.v1", "job": "field-aaaaaaaaaaaa",
        "observations": [
            {"phase": "before_resume", "matching_solver_count": 0, "conflicting_solver_count": 0},
            {"phase": "after_resume_launch", "matching_solver_count": 1, "conflicting_solver_count": 0},
            {"phase": "after_completion", "matching_solver_count": 0, "conflicting_solver_count": 0},
        ],
        "conflicting_job_relaunched": False,
    })
    job = {
        "schema_version": 1, "contract": "field_pipeline_job.v1", "engine": "body_fitted_field_pipeline",
        "created_at": "2026-08-25T00:00:00Z", "updated_at": "2026-08-25T01:00:00Z",
        "job": "field-aaaaaaaaaaaa", "status": "analysis_complete_not_citable", "stage": "complete",
        "attempts": 2, "error": "",
        "input": {"geometry_path": geometry.relative_to(root).as_posix(), "geometry_sha256": room_fixture._sha256(geometry),
                  "source_dxf_path": source_dxf.relative_to(root).as_posix(), "source_dxf_sha256": room_fixture._sha256(source_dxf)},
        "level": {"thermal_case": case.relative_to(root).as_posix(), "latest_time_s": 240.0,
                  "flow_through_fraction": 3.0, "status": "PASS", "stage": "complete"},
        "result_case": case.relative_to(root).as_posix(), "report_path": report.relative_to(root).as_posix(),
        "citation_status": "SCREENING_ONLY", "citation_blockers": [],
        "attempt_history": [
            {"attempt": 1, "started_at": "2026-08-25T00:00:00Z", "finished_at": "2026-08-25T00:10:00Z",
             "elapsed_s": 600.0, "status": "FAIL",
             "snapshot_path": pre_snapshot.relative_to(root).as_posix(), "snapshot_sha256": room_fixture._sha256(pre_snapshot)},
            {"attempt": 2, "started_at": "2026-08-25T00:20:00Z", "finished_at": "2026-08-25T01:00:00Z",
             "elapsed_s": 2400.0, "status": "analysis_complete_not_citable",
             "snapshot_path": post_snapshot.relative_to(root).as_posix(), "snapshot_sha256": room_fixture._sha256(post_snapshot)},
        ],
        "resume_history": [{"resumed_at": "2026-08-25T00:20:00Z", "previous_status": "FAIL",
                            "previous_stage": "thermal", "previous_attempt": 1, "checkpoint_time_s": 80.0,
                            "flow_through_fraction": 1.0,
                            "checkpoint_log_path": checkpoint_log.relative_to(root).as_posix(),
                            "checkpoint_log_sha256": room_fixture._sha256(checkpoint_log)}],
    }
    room_fixture._write_json(job_path, job)
    manifest_path = root / "_working_validation" / "sgi-screening-v1" / "sgi_screening_acceptance.json"
    payload = {
        "schema_version": 1, "contract": "sgi_screening_acceptance.v1",
        "field_job": {"path": job_path.relative_to(root).as_posix(), "sha256": room_fixture._sha256(job_path)},
        "source_dxf": {"path": source_dxf.relative_to(root).as_posix(), "sha256": room_fixture._sha256(source_dxf)},
        "reviewed_geometry": {"path": geometry.relative_to(root).as_posix(), "sha256": room_fixture._sha256(geometry)},
        "resource_preflight": {"path": resource_preflight.relative_to(root).as_posix(), "sha256": room_fixture._sha256(resource_preflight)},
        "restart_evidence": {
            "pre_attempt_snapshot": {"path": pre_snapshot.relative_to(root).as_posix(), "sha256": room_fixture._sha256(pre_snapshot)},
            "post_attempt_snapshot": {"path": post_snapshot.relative_to(root).as_posix(), "sha256": room_fixture._sha256(post_snapshot)},
            "checkpoint_solver_log": {"path": checkpoint_log.relative_to(root).as_posix(), "sha256": room_fixture._sha256(checkpoint_log)},
            "checkpoint_field_t": {"path": (checkpoint / "T").relative_to(root).as_posix(), "sha256": room_fixture._sha256(checkpoint / "T")},
            "checkpoint_field_u": {"path": (checkpoint / "U").relative_to(root).as_posix(), "sha256": room_fixture._sha256(checkpoint / "U")},
            "checkpoint_field_phi": {"path": (checkpoint / "phi").relative_to(root).as_posix(), "sha256": room_fixture._sha256(checkpoint / "phi")},
            "process_audit": {"path": process_audit.relative_to(root).as_posix(), "sha256": room_fixture._sha256(process_audit)},
        },
        "solver_case": {"case_path": case.relative_to(root).as_posix(),
                        "case_tree_sha256": room_fixture._canonical_tree_sha256(case),
                        "artifacts": {key: {"path": path.relative_to(root).as_posix(), "sha256": room_fixture._sha256(path)}
                                      for key, path in paths.items()}},
        "limits": {"required_supply_terminals": 15, "required_exhaust_terminals": 15,
                   "required_airflow_per_terminal_cmh": 444.0, "maximum_supply_exhaust_difference_ratio": 0.01,
                   "minimum_flow_through_fraction": 3.0, "maximum_peak_courant": 1.0,
                   "maximum_global_continuity": 0.000001, "maximum_terminal_phi_imbalance_ratio": 0.001,
                   "maximum_opening_area_error_ratio": 0.03, "maximum_supply_flow_error_ratio": 0.01,
                   "minimum_energy_closure_ratio": 0.95, "maximum_energy_closure_ratio": 1.05,
                   "maximum_peak_rss_fraction": 0.8, "minimum_restart_attempts": 2},
    }
    room_fixture._write_json(manifest_path, payload)
    return manifest_path, {"case": case, "paths": paths, "job": job_path, "geometry": geometry,
                           "dxf": source_dxf, "resource": resource_preflight,
                           "pre_snapshot": pre_snapshot, "post_snapshot": post_snapshot,
                           "process_audit": process_audit, "checkpoint_log": checkpoint_log}


def _rehash_sgi_manifest(manifest):
    root = manifest.parents[2]
    payload = json.loads(manifest.read_text(encoding="utf-8"))

    def refresh(link):
        path = root.joinpath(*Path(link["path"]).parts)
        link["sha256"] = room_fixture._sha256(path)

    for key in ("field_job", "source_dxf", "reviewed_geometry", "resource_preflight"):
        refresh(payload[key])
    for link in payload["restart_evidence"].values():
        refresh(link)
    for link in payload["solver_case"]["artifacts"].values():
        refresh(link)
    case = root.joinpath(*Path(payload["solver_case"]["case_path"]).parts)
    payload["solver_case"]["case_tree_sha256"] = room_fixture._canonical_tree_sha256(case)
    manifest.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def test_sgi_schema_is_closed_pointer_manifest(tmp_path):
    schema = json.loads((REPO / "sgi_screening_acceptance.v1.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    manifest, _ = _sgi_bundle(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(payload)) == []
    payload["status"] = "PASS"
    assert list(Draft202012Validator(schema).iter_errors(payload))


def test_field_pipeline_create_job_has_a_relative_acceptance_adapter_but_restart_fails_closed(tmp_path):
    import field_pipeline_job
    from cfd_working_room import adapt_field_pipeline_job_for_acceptance

    source = room_fixture._write(tmp_path / "_imports" / "adapter-site.dxf", "0\nSECTION\n0\nEOF\n")
    geometry_payload = _sgi_geometry(
        str(source.resolve()), room_fixture._sha256(source),
    )
    geometry = room_fixture._write_json(tmp_path / "_imports" / "adapter.geometry.json", geometry_payload)
    created = field_pipeline_job.create_job(tmp_path, geometry)
    assert created["ok"], created

    adapted = adapt_field_pipeline_job_for_acceptance(created["manifest_path"], tmp_path)
    assert adapted["contract"] == "field_pipeline_acceptance_adapter.v1"
    assert adapted["relative_refs"]["field_job"]["path"].startswith("_field_jobs/field-")
    assert adapted["relative_refs"]["reviewed_geometry"]["path"] == "_imports/adapter.geometry.json"
    assert adapted["relative_refs"]["source_dxf"]["path"] == "_imports/adapter-site.dxf"
    assert adapted["status"] == "BLOCKED"
    assert "FIELD_PIPELINE_RESTART_EVIDENCE_NOT_PRODUCED" in adapted["blockers"]

    case = tmp_path / "_body_solver" / "adapter-case"
    case.mkdir(parents=True)
    job = json.loads(Path(created["manifest_path"]).read_text(encoding="utf-8"))
    job["result_case"] = str(case.resolve())
    Path(created["manifest_path"]).write_text(json.dumps(job, sort_keys=True), encoding="utf-8")
    acceptance = Path(created["manifest_path"]).parent / "acceptance"
    for name in (
        "resource_preflight.json", "attempt-1.json", "attempt-2.json",
        "attempt-1-solver.log", "checkpoint-T", "checkpoint-U", "checkpoint-phi",
        "process-audit.json",
    ):
        room_fixture._write(acceptance / name, "{}")
    adapted = adapt_field_pipeline_job_for_acceptance(created["manifest_path"], tmp_path)
    assert adapted["status"] == "BLOCKED"
    assert "FIELD_PIPELINE_ACCEPTANCE_MANIFEST_NOT_PRODUCED" in adapted["blockers"]


def test_sgi_and_restart_validators_recompute_and_are_stable(tmp_path):
    from cfd_working_room import validate_restart_integrity, validate_sgi_screening_acceptance

    manifest, _ = _sgi_bundle(tmp_path)
    sgi = validate_sgi_screening_acceptance(manifest, tmp_path)
    restart = validate_restart_integrity(manifest, tmp_path)

    assert sgi == validate_sgi_screening_acceptance(manifest, tmp_path)
    assert restart == validate_restart_integrity(manifest, tmp_path)
    assert sgi["check_id"] == "real_dxf_screening"
    assert sgi["status"] == "PASS", sgi
    assert restart["check_id"] == "restart_integrity"
    assert restart["status"] == "PASS", restart
    assert restart["metrics"] == {"attempt_count": 2, "final_flow_through_fraction": 3.0,
                                  "final_physical_time_s": 240.0, "verified_checkpoint_physical_time_s": 80.0}


def test_sgi_has_distinct_site_schedule_and_validation_fixture_authority_branches(tmp_path):
    from cfd_working_room import validate_sgi_screening_acceptance

    fixture_manifest, _ = _sgi_bundle(tmp_path / "fixture")
    schedule_manifest, _ = _sgi_bundle(
        tmp_path / "schedule", authority="site_schedule", heat_w=12000.0,
    )

    fixture = validate_sgi_screening_acceptance(fixture_manifest, tmp_path / "fixture")
    schedule = validate_sgi_screening_acceptance(schedule_manifest, tmp_path / "schedule")
    assert fixture["status"] == "PASS", fixture
    assert schedule["status"] == "PASS", schedule


def test_sgi_recomputes_dxf_terminal_and_fixture_heat_identity(tmp_path):
    from cfd_working_room import validate_sgi_screening_acceptance

    manifest, bundle = _sgi_bundle(tmp_path)
    geometry = json.loads(bundle["geometry"].read_text(encoding="utf-8"))
    terminals = [row for row in geometry["elements"]["equipment"]
                 if row["semantic"]["kind"] == "air_terminal"]
    terminals[1]["id"] = terminals[0]["id"]
    terminals[2]["semantic"]["normal"] = [0.0, 0.0, 0.0]
    geometry["source_sha256"] = "0" * 64
    bundle["geometry"].write_text(json.dumps(geometry, sort_keys=True), encoding="utf-8")
    thermal = json.loads(bundle["paths"]["thermal_input"].read_text(encoding="utf-8"))
    thermal["heat_sources"][0]["source_id"] = "forged-heat"
    bundle["paths"]["thermal_input"].write_text(json.dumps(thermal, sort_keys=True), encoding="utf-8")
    _rehash_sgi_manifest(manifest)

    blockers = validate_sgi_screening_acceptance(manifest, tmp_path)["blockers"]
    assert "SGI_DXF_PROVENANCE_INVALID" in blockers
    assert "SGI_TERMINAL_IDENTITY_INVALID" in blockers
    assert "SGI_TERMINAL_REVIEW_INVALID" in blockers
    assert "SGI_HEAT_SOURCE_CHAIN_INVALID" in blockers


def test_sgi_fixture_heat_requires_a_server_owned_location(tmp_path):
    from cfd_working_room import validate_sgi_screening_acceptance

    manifest, bundle = _sgi_bundle(tmp_path)
    geometry = json.loads(bundle["geometry"].read_text(encoding="utf-8"))
    heat = next(row for row in geometry["elements"]["equipment"]
                if row["semantic"]["role"] == "heat_source")
    heat["points"] = []
    bundle["geometry"].write_text(json.dumps(geometry, sort_keys=True), encoding="utf-8")
    _rehash_sgi_manifest(manifest)

    assert "SGI_FIXTURE_HEAT_PROVENANCE_INVALID" in validate_sgi_screening_acceptance(
        manifest, tmp_path,
    )["blockers"]


def test_sgi_recomputes_opening_flow_and_rss_from_pinned_raw_files(tmp_path):
    from cfd_working_room import validate_sgi_screening_acceptance

    manifest, bundle = _sgi_bundle(tmp_path)
    phi = bundle["paths"]["field_phi"]
    phi.write_text(phi.read_text(encoding="utf-8").replace(
        "sgi-supply-01 { value nonuniform List<scalar> 1 (-0.12333333333333334); }",
        "sgi-supply-01 { value nonuniform List<scalar> 1 (-0.06); }",
    ), encoding="utf-8")
    bundle["paths"]["runner_log"].write_text(
        "Maximum resident set size (kbytes): 9\n", encoding="utf-8",
    )
    _rehash_sgi_manifest(manifest)

    blockers = validate_sgi_screening_acceptance(manifest, tmp_path)["blockers"]
    assert "SGI_SUPPLY_FLOW_ERROR_EXCEEDED" in blockers
    assert "SGI_PEAK_RSS_LIMIT_EXCEEDED" in blockers


def test_sgi_opening_evidence_is_bound_to_reviewed_terminal_identity(tmp_path):
    from cfd_working_room import validate_sgi_screening_acceptance

    manifest, bundle = _sgi_bundle(tmp_path)
    case_meta = json.loads(bundle["paths"]["case_meta"].read_text(encoding="utf-8"))
    saved = json.loads(bundle["paths"]["opening_verification"].read_text(encoding="utf-8"))
    case_meta["opening_preflight"]["terminals"][0]["opening_id"] = "forged-opening"
    saved["terminals"][0]["opening_id"] = "forged-opening"
    bundle["paths"]["case_meta"].write_text(json.dumps(case_meta, sort_keys=True), encoding="utf-8")
    bundle["paths"]["opening_verification"].write_text(
        json.dumps(saved, sort_keys=True), encoding="utf-8",
    )
    _rehash_sgi_manifest(manifest)

    assert "SGI_OPENING_TERMINAL_BINDING_INVALID" in validate_sgi_screening_acceptance(
        manifest, tmp_path,
    )["blockers"]


def test_sgi_requires_one_explicit_final_time_and_full_artifact_chain(tmp_path):
    from cfd_working_room import validate_sgi_screening_acceptance

    manifest, bundle = _sgi_bundle(tmp_path)
    result = json.loads(bundle["paths"]["result"].read_text(encoding="utf-8"))
    result["time_s"] = 239.0
    bundle["paths"]["result"].write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    mesh_input = json.loads(bundle["paths"]["mesh_input"].read_text(encoding="utf-8"))
    mesh_input["surface_manifest_sha256"] = "0" * 64
    bundle["paths"]["mesh_input"].write_text(json.dumps(mesh_input, sort_keys=True), encoding="utf-8")
    _rehash_sgi_manifest(manifest)

    blockers = validate_sgi_screening_acceptance(manifest, tmp_path)["blockers"]
    assert "SGI_FINAL_TIME_AMBIGUOUS" in blockers
    assert "SGI_SURFACE_MESH_THERMAL_BINDING_INVALID" in blockers


def test_sgi_rejects_copied_result_and_invalid_review(tmp_path):
    from cfd_working_room import validate_sgi_screening_acceptance

    manifest, bundle = _sgi_bundle(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["solver_case"]["artifacts"]["result"] = payload["field_job"]
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    assert "SGI_ARTIFACT_REF_INVALID:result" in validate_sgi_screening_acceptance(manifest, tmp_path)["blockers"]

    manifest, bundle = _sgi_bundle(tmp_path / "review")
    geometry = json.loads(bundle["geometry"].read_text(encoding="utf-8"))
    geometry["review"]["ready"] = False
    bundle["geometry"].write_text(json.dumps(geometry), encoding="utf-8")
    result = validate_sgi_screening_acceptance(manifest, tmp_path / "review")
    assert "SGI_GEOMETRY_REVIEW_NOT_READY" in result["blockers"]


def test_restart_rejects_attempt_and_pre_post_hash_mismatch(tmp_path):
    from cfd_working_room import validate_restart_integrity

    manifest, bundle = _sgi_bundle(tmp_path)
    pre = json.loads(bundle["pre_snapshot"].read_text(encoding="utf-8"))
    pre["thermal_input"]["sha256"] = "0" * 64
    bundle["pre_snapshot"].write_text(json.dumps(pre, sort_keys=True), encoding="utf-8")
    job = json.loads(bundle["job"].read_text(encoding="utf-8"))
    job["attempts"] = 1
    job["attempt_history"][0]["snapshot_sha256"] = room_fixture._sha256(bundle["pre_snapshot"])
    bundle["job"].write_text(json.dumps(job), encoding="utf-8")
    _rehash_sgi_manifest(manifest)
    result = validate_restart_integrity(manifest, tmp_path)
    assert "RESTART_ATTEMPT_COUNT_INVALID" in result["blockers"]
    assert "RESTART_SNAPSHOT_HASH_MISMATCH:thermal_input" in result["blockers"]
    assert "RESTART_INPUT_HASH_CHANGED" in result["blockers"]


def test_restart_derives_checkpoint_time_and_rejects_duplicate_solver_observation(tmp_path):
    from cfd_working_room import validate_restart_integrity

    manifest, bundle = _sgi_bundle(tmp_path)
    checkpoint_log = bundle["checkpoint_log"]
    checkpoint_log.write_text(checkpoint_log.read_text(encoding="utf-8").replace(
        "Time = 80", "Time = 79",
    ), encoding="utf-8")
    process = json.loads(bundle["process_audit"].read_text(encoding="utf-8"))
    process["observations"][1]["matching_solver_count"] = 2
    bundle["process_audit"].write_text(json.dumps(process, sort_keys=True), encoding="utf-8")
    pre = json.loads(bundle["pre_snapshot"].read_text(encoding="utf-8"))
    pre["solver_log"]["sha256"] = room_fixture._sha256(checkpoint_log)
    bundle["pre_snapshot"].write_text(json.dumps(pre, sort_keys=True), encoding="utf-8")
    job = json.loads(bundle["job"].read_text(encoding="utf-8"))
    job["resume_history"][0]["checkpoint_log_sha256"] = room_fixture._sha256(checkpoint_log)
    job["attempt_history"][0]["snapshot_sha256"] = room_fixture._sha256(bundle["pre_snapshot"])
    bundle["job"].write_text(json.dumps(job, sort_keys=True), encoding="utf-8")
    _rehash_sgi_manifest(manifest)

    blockers = validate_restart_integrity(manifest, tmp_path)["blockers"]
    assert "RESTART_CHECKPOINT_TRANSITION_INVALID" in blockers
    assert "RESTART_SOLVER_CONFLICT_INVALID" in blockers


def test_restart_derives_attempt_state_and_resume_time_order(tmp_path):
    from cfd_working_room import validate_restart_integrity

    manifest, bundle = _sgi_bundle(tmp_path)
    job = json.loads(bundle["job"].read_text(encoding="utf-8"))
    job["attempt_history"][0]["status"] = "complete"
    job["resume_history"][0]["resumed_at"] = "2026-08-25T00:05:00Z"
    bundle["job"].write_text(json.dumps(job, sort_keys=True), encoding="utf-8")
    _rehash_sgi_manifest(manifest)

    assert "RESTART_HISTORY_STATE_INVALID" in validate_restart_integrity(
        manifest, tmp_path,
    )["blockers"]


def test_restart_reopens_checkpoint_field_contents(tmp_path):
    from cfd_working_room import validate_restart_integrity

    manifest, bundle = _sgi_bundle(tmp_path)
    pre = json.loads(bundle["pre_snapshot"].read_text(encoding="utf-8"))
    for key, artifact in (
        ("field_t", "restart_checkpoint_field_t"),
        ("field_u", "restart_checkpoint_field_u"),
        ("field_phi", "restart_checkpoint_field_phi"),
    ):
        path = bundle["case"] / "80" / {"field_t": "T", "field_u": "U", "field_phi": "phi"}[key]
        path.write_text("", encoding="utf-8")
        pre[key]["sha256"] = room_fixture._sha256(path)
    bundle["pre_snapshot"].write_text(json.dumps(pre, sort_keys=True), encoding="utf-8")
    job = json.loads(bundle["job"].read_text(encoding="utf-8"))
    job["attempt_history"][0]["snapshot_sha256"] = room_fixture._sha256(bundle["pre_snapshot"])
    bundle["job"].write_text(json.dumps(job, sort_keys=True), encoding="utf-8")
    _rehash_sgi_manifest(manifest)

    assert "RESTART_CHECKPOINT_FIELDS_INVALID" in validate_restart_integrity(
        manifest, tmp_path,
    )["blockers"]


def test_sgi_rejects_latest_output_alias_and_tampered_result(tmp_path):
    from cfd_working_room import validate_sgi_screening_acceptance

    manifest, bundle = _sgi_bundle(tmp_path)
    assert "EVALUATOR_OUTPUT_ALIASES_INPUT" in validate_sgi_screening_acceptance(
        manifest, tmp_path, evaluator_output_path=manifest
    )["blockers"]
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["solver_case"]["case_path"] = "_body_solver/latest"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    assert "SGI_SCREENING_MANIFEST_SCHEMA_INVALID" in validate_sgi_screening_acceptance(manifest, tmp_path)["blockers"]

    manifest, bundle = _sgi_bundle(tmp_path / "tamper")
    bundle["paths"]["vtu"].write_text("<VTKFile/>", encoding="utf-8")
    result = validate_sgi_screening_acceptance(manifest, tmp_path / "tamper")
    assert "SGI_ARTIFACT_HASH_MISMATCH:vtu" in result["blockers"]
    assert "SGI_VTU_FIELDS_INVALID" in result["blockers"]


def test_every_sgi_hash_and_ref_source_is_enforced(tmp_path):
    from cfd_working_room import validate_sgi_screening_acceptance

    manifest, _ = _sgi_bundle(tmp_path)
    pristine = json.loads(manifest.read_text(encoding="utf-8"))
    for key in ("field_job", "source_dxf", "reviewed_geometry"):
        payload = json.loads(json.dumps(pristine))
        payload[key]["sha256"] = "0" * 64
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        assert f"SGI_POINTER_HASH_MISMATCH:{key}" in validate_sgi_screening_acceptance(manifest, tmp_path)["blockers"]
    for key in pristine["solver_case"]["artifacts"]:
        payload = json.loads(json.dumps(pristine))
        payload["solver_case"]["artifacts"][key]["sha256"] = "0" * 64
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        assert f"SGI_ARTIFACT_HASH_MISMATCH:{key}" in validate_sgi_screening_acceptance(manifest, tmp_path)["blockers"]
        payload = json.loads(json.dumps(pristine))
        payload["solver_case"]["artifacts"][key]["path"] = f"latest/{key}"
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        assert "SGI_SCREENING_MANIFEST_SCHEMA_INVALID" in validate_sgi_screening_acceptance(manifest, tmp_path)["blockers"]


@pytest.mark.parametrize("limit", (
    "required_supply_terminals", "required_exhaust_terminals", "required_airflow_per_terminal_cmh",
    "maximum_supply_exhaust_difference_ratio", "minimum_flow_through_fraction", "maximum_peak_courant",
    "maximum_global_continuity", "maximum_terminal_phi_imbalance_ratio", "maximum_opening_area_error_ratio",
    "maximum_supply_flow_error_ratio", "minimum_energy_closure_ratio", "maximum_energy_closure_ratio",
    "maximum_peak_rss_fraction", "minimum_restart_attempts",
))
def test_every_sgi_threshold_source_is_fixed_by_schema(tmp_path, limit):
    from cfd_working_room import validate_sgi_screening_acceptance

    manifest, _ = _sgi_bundle(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["limits"][limit] = 999.0
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    assert "SGI_SCREENING_MANIFEST_SCHEMA_INVALID" in validate_sgi_screening_acceptance(manifest, tmp_path)["blockers"]


def test_sgi_rejects_duplicate_keys_and_stops_after_manifest_structure_error(tmp_path):
    from cfd_working_room import validate_sgi_screening_acceptance

    manifest, _ = _sgi_bundle(tmp_path / "duplicate")
    manifest.write_text(manifest.read_text(encoding="utf-8").replace(
        '"schema_version": 1', '"schema_version": 1, "schema_version": 1', 1,
    ), encoding="utf-8")
    duplicate = validate_sgi_screening_acceptance(manifest, tmp_path / "duplicate")
    assert duplicate["blockers"] == ["SGI_SCREENING_MANIFEST_MALFORMED"]

    manifest, _ = _sgi_bundle(tmp_path / "structure")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["solver_case"] = "not-an-object"
    manifest.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    structural = validate_sgi_screening_acceptance(manifest, tmp_path / "structure")
    assert structural["blockers"] == ["SGI_SCREENING_MANIFEST_SCHEMA_INVALID"]


def test_restart_malformed_snapshot_type_is_blocked_without_crashing(tmp_path):
    from cfd_working_room import validate_restart_integrity

    manifest, bundle = _sgi_bundle(tmp_path)
    pre = json.loads(bundle["pre_snapshot"].read_text(encoding="utf-8"))
    pre["geometry"] = "not-a-link"
    bundle["pre_snapshot"].write_text(json.dumps(pre, sort_keys=True), encoding="utf-8")
    job = json.loads(bundle["job"].read_text(encoding="utf-8"))
    job["attempt_history"][0]["snapshot_sha256"] = room_fixture._sha256(bundle["pre_snapshot"])
    bundle["job"].write_text(json.dumps(job, sort_keys=True), encoding="utf-8")
    _rehash_sgi_manifest(manifest)

    result = validate_restart_integrity(manifest, tmp_path)
    assert result["status"] == "BLOCKED"
    assert "RESTART_STRUCTURAL_EVIDENCE_INVALID" in result["blockers"]


def test_sgi_rejects_output_inside_case_and_detects_late_file_addition(monkeypatch, tmp_path):
    import cfd_working_room

    manifest, bundle = _sgi_bundle(tmp_path)
    output = bundle["case"] / "future-evaluator-output.json"
    assert "EVALUATOR_OUTPUT_ALIASES_INPUT" in cfd_working_room.validate_sgi_screening_acceptance(
        manifest, tmp_path, evaluator_output_path=output,
    )["blockers"]

    original = cfd_working_room._sgi_field_metrics

    def add_after_enumeration(case, paths, thermal, blockers):
        result = original(case, paths, thermal, blockers)
        (case / "late-addition.txt").write_text("late", encoding="utf-8")
        return result

    monkeypatch.setattr(cfd_working_room, "_sgi_field_metrics", add_after_enumeration)
    result = cfd_working_room.validate_sgi_screening_acceptance(manifest, tmp_path)
    assert "POST_LOAD_CASE_TREE_CHANGED:sgi" in result["blockers"]


def test_sgi_malformed_thermal_rows_are_blocked_without_crashing(tmp_path):
    from cfd_working_room import validate_sgi_screening_acceptance

    manifest, bundle = _sgi_bundle(tmp_path)
    thermal = json.loads(bundle["paths"]["thermal_input"].read_text(encoding="utf-8"))
    thermal["terminals"] = ["not-a-terminal"]
    bundle["paths"]["thermal_input"].write_text(
        json.dumps(thermal, sort_keys=True), encoding="utf-8",
    )
    _rehash_sgi_manifest(manifest)

    result = validate_sgi_screening_acceptance(manifest, tmp_path)
    assert result["status"] == "BLOCKED"
    assert "SGI_THERMAL_INPUT_INVALID" in result["blockers"]


def test_sgi_rejects_output_anywhere_in_field_job_acceptance_scope(tmp_path):
    from cfd_working_room import validate_sgi_screening_acceptance

    manifest, bundle = _sgi_bundle(tmp_path)
    output = bundle["job"].parent / "acceptance" / "future-evaluator-output.json"

    result = validate_sgi_screening_acceptance(
        manifest, tmp_path, evaluator_output_path=output,
    )
    assert "EVALUATOR_OUTPUT_ALIASES_INPUT" in result["blockers"]


def test_sgi_and_restart_accept_hash_pinned_absolute_producer_case_refs(tmp_path):
    from cfd_working_room import validate_restart_integrity, validate_sgi_screening_acceptance

    manifest, bundle = _sgi_bundle(tmp_path)
    job = json.loads(bundle["job"].read_text(encoding="utf-8"))
    absolute_case = str(bundle["case"].resolve())
    job["result_case"] = absolute_case
    job["level"]["thermal_case"] = absolute_case
    bundle["job"].write_text(json.dumps(job, sort_keys=True), encoding="utf-8")
    _rehash_sgi_manifest(manifest)

    sgi = validate_sgi_screening_acceptance(manifest, tmp_path)
    restart = validate_restart_integrity(manifest, tmp_path)
    assert sgi["status"] == "PASS", sgi
    assert restart["status"] == "PASS", restart


@pytest.mark.parametrize("malformation, expected_blocker", (
    ("opening_ratio", "SGI_OPENING_VERIFICATION_MISMATCH"),
    ("summary_temperature", "SGI_SUMMARY_MISMATCH"),
    ("mixed_timezone_attempt", "SGI_RUNTIME_EVIDENCE_INVALID"),
))
def test_sgi_malformed_raw_evidence_types_are_blocked_without_crashing(
        tmp_path, malformation, expected_blocker):
    from cfd_working_room import validate_sgi_screening_acceptance

    manifest, bundle = _sgi_bundle(tmp_path)
    if malformation == "opening_ratio":
        path = bundle["paths"]["opening_verification"]
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["terminals"][0]["area_ratio"] = [1.0]
    elif malformation == "summary_temperature":
        path = bundle["paths"]["summary"]
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["temperature"] = "not-an-object"
    else:
        path = bundle["job"]
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["attempt_history"][-1]["started_at"] = "2026-08-25T00:00:00"
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    _rehash_sgi_manifest(manifest)

    result = validate_sgi_screening_acceptance(manifest, tmp_path)
    assert result["status"] == "BLOCKED"
    assert expected_blocker in result["blockers"]


@pytest.mark.parametrize("malformation, expected_blocker", (
    ("thermal_patch", "SGI_THERMAL_INPUT_INVALID"),
    ("thermal_heat_id", "SGI_THERMAL_INPUT_INVALID"),
    ("opening_children", "SGI_OPENING_PREFLIGHT_INVALID"),
    ("opening_saved_id", "SGI_OPENING_VERIFICATION_MISMATCH"),
    ("surface_source_ids", "SGI_SURFACE_REGION_INVALID"),
))
def test_sgi_nested_malformed_evidence_is_blocked_without_crashing(
        tmp_path, malformation, expected_blocker):
    from cfd_working_room import validate_sgi_screening_acceptance

    manifest, bundle = _sgi_bundle(tmp_path)
    if malformation == "thermal_patch":
        path = bundle["paths"]["thermal_input"]
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["terminals"][0].pop("mesh_patch_name")
    elif malformation == "thermal_heat_id":
        path = bundle["paths"]["thermal_input"]
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["heat_sources"][0]["source_id"] = ["bad"]
    elif malformation == "opening_children":
        path = bundle["paths"]["case_meta"]
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["opening_preflight"]["terminals"][0]["child_patch_names"] = [["bad"]]
    elif malformation == "opening_saved_id":
        path = bundle["paths"]["opening_verification"]
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["terminals"][0]["opening_id"] = ["bad"]
    else:
        path = bundle["paths"]["surface"]
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["regions"][0]["source_element_ids"] = 1
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    _rehash_sgi_manifest(manifest)

    result = validate_sgi_screening_acceptance(manifest, tmp_path)
    assert result["status"] == "BLOCKED"
    assert expected_blocker in result["blockers"]
