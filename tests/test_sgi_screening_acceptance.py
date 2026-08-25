import json
import shutil
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

import test_cfd_working_room as room_fixture


REPO = Path(__file__).resolve().parents[1]


def _sgi_geometry():
    from cfd_working_room import build_working_room_geometry

    value = build_working_room_geometry()
    value["source"] = "_imports/한국-SGI-lobby.dxf"
    template = value["elements"]["equipment"][0]
    terminals = []
    for role, normal in (("supply", [1.0, 0.0, 0.0]), ("exhaust", [-1.0, 0.0, 0.0])):
        for index in range(15):
            item = json.loads(json.dumps(template))
            item["id"] = f"sgi-{role}-{index + 1:02d}"
            item["semantic"].update(role=role, airflow_cmh=444.0, design_normal=normal)
            terminals.append(item)
    value["elements"]["equipment"] = terminals
    value["elements"]["heat_sources"][0]["semantic"].update(
        input_power_w=15500.0, power_kw=15.5, convective_power_w=15500.0,
        evidence="non_authoritative_working_fixture", override_of_dxf=True,
    )
    value["scenario_authority"] = "non_authoritative_working_fixture"
    value["validation_fixture_only"] = True
    return value


def _sgi_bundle(root):
    temporary, _ = room_fixture._case(root, "sgi-temporary", temperature=301.5283783783784,
                                      speed=0.2, closure=1.0, execution_id="sgi-final-run")
    case = root / "_body_solver" / "sgi-field-design"
    case.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(temporary), str(case))
    geometry = room_fixture._write_json(case / "geometry.json", _sgi_geometry())
    surface_path = case / "surface_manifest.json"
    surface = json.loads(surface_path.read_text(encoding="utf-8"))
    surface["source"].update(geometry_path=geometry.relative_to(root).as_posix(), geometry_sha256=room_fixture._sha256(geometry))
    surface_path.write_text(json.dumps(surface, sort_keys=True), encoding="utf-8")

    flow = 444.0 / 3600.0
    terminals = []
    phi_supply, phi_exhaust, temp_supply, temp_exhaust = [], [], [], []
    for role in ("supply", "exhaust"):
        for index in range(15):
            patch = f"sgi-{role}-{index + 1:02d}"
            terminals.append({"mesh_patch_name": patch, "role": role, "flow_rate_m3_s": flow,
                              "source_element_id": patch, "airflow_cmh": 444.0})
            if role == "supply":
                phi_supply.append((patch, [-flow]))
                temp_supply.append((patch, [293.15]))
            else:
                phi_exhaust.append((patch, [flow]))
                temp_exhaust.append((patch, [100.0]))
    thermal_path = case / "thermal_input.json"
    thermal = json.loads(thermal_path.read_text(encoding="utf-8"))
    thermal["terminals"] = terminals
    thermal["heat"]["applied_convective_power_w"] = 15500.0
    thermal["scenario_authority"] = "non_authoritative_working_fixture"
    thermal["validation_fixture_only"] = True
    thermal_path.write_text(json.dumps(thermal, sort_keys=True), encoding="utf-8")

    latest = case / "240"
    room_fixture._write(latest / "phi", room_fixture._scalar([0.0])[:-2] + "\n".join(
        f"{patch} {{ value nonuniform List<scalar> 1 ({values[0]}); }}"
        for patch, values in phi_supply + phi_exhaust
    ) + " }\n")
    room_fixture._write(latest / "T", room_fixture._scalar([301.5283783783784])[:-2] + "\n".join(
        f"{patch} {{ value nonuniform List<scalar> 1 ({values[0]}); }}"
        for patch, values in temp_supply + temp_exhaust
    ) + " }\n")
    room_fixture._poly_mesh(case, [patch for patch, _ in phi_exhaust])

    run_path = case / "run_manifest.json"
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["input"]["thermal_input_sha256"] = room_fixture._sha256(thermal_path)
    run["input"]["numerical_provenance"]["thermal_input_sha256"] = room_fixture._sha256(thermal_path)
    run["runtime"] = {"runner_wall_seconds": 10.0, "solver_clock_seconds": 9.0,
                      "peak_rss_bytes": 1000, "output_bytes": 10000, "available_ram_bytes": 10000}
    run["opening_verification"] = {"maximum_applied_area_error_ratio": 0.02,
                                   "actual_supply_flow_error_ratio": 0.005}
    run_path.write_text(json.dumps(run, sort_keys=True), encoding="utf-8")
    result_path = case / "result_manifest.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["run_manifest_sha256"] = room_fixture._sha256(run_path)
    result["thermal_input_sha256"] = room_fixture._sha256(thermal_path)
    result_path.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    report = case / "body_fitted_report.html"
    report.write_text("<html>SCREENING_ONLY</html>", encoding="utf-8")

    paths = {
        "geometry": geometry, "surface": surface_path, "mesh": case / "mesh_manifest.json",
        "thermal_input": thermal_path, "thermal_progress": case / "thermal_progress.json",
        "run": run_path, "result": result_path, "check_mesh_log": case / "log.checkMesh",
        "solver_log": case / "log.buoyantBoussinesqPimpleFoam", "field_t": latest / "T",
        "field_u": latest / "U", "field_phi": latest / "phi", "field_v": latest / "V",
        "vtu": case / "results/internal.vtu", "summary": case / "results/body_fitted_summary.json",
        "slice_x": case / "results/slices/x_mid.json", "slice_y": case / "results/slices/y_mid.json",
        "slice_z": case / "results/slices/z_mid.json", "report": report,
    }
    source_dxf = room_fixture._write(root / "_imports" / "한국-SGI-lobby.dxf", "0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n")
    job_path = root / "_field_jobs" / "field-aaaaaaaaaaaa" / "field_pipeline_job.json"
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
             "elapsed_s": 600.0, "status": "FAIL"},
            {"attempt": 2, "started_at": "2026-08-25T00:20:00Z", "finished_at": "2026-08-25T01:00:00Z",
             "elapsed_s": 2400.0, "status": "analysis_complete_not_citable"},
        ],
        "resume_history": [{"resumed_at": "2026-08-25T00:20:00Z", "previous_status": "FAIL",
                            "previous_stage": "thermal", "previous_attempt": 1, "checkpoint_time_s": 80.0,
                            "flow_through_fraction": 1.0, "transition": "verified_checkpoint_to_resumed",
                            "geometry_sha256": room_fixture._sha256(geometry),
                            "thermal_input_sha256": room_fixture._sha256(thermal_path)}],
        "recovery": {"transition": "verified_checkpoint_to_resumed", "verified_checkpoint_time_s": 80.0,
                     "pre_resume_geometry_sha256": room_fixture._sha256(geometry),
                     "post_resume_geometry_sha256": room_fixture._sha256(geometry),
                     "pre_resume_input_sha256": room_fixture._sha256(thermal_path),
                     "post_resume_input_sha256": room_fixture._sha256(thermal_path),
                     "duplicate_solver_count": 0, "conflicting_job_relaunched": False},
    }
    room_fixture._write_json(job_path, job)
    manifest_path = root / "_working_validation" / "sgi-screening-v1" / "sgi_screening_acceptance.json"
    payload = {
        "schema_version": 1, "contract": "sgi_screening_acceptance.v1",
        "field_job": {"path": job_path.relative_to(root).as_posix(), "sha256": room_fixture._sha256(job_path)},
        "source_dxf": {"path": source_dxf.relative_to(root).as_posix(), "sha256": room_fixture._sha256(source_dxf)},
        "reviewed_geometry": {"path": geometry.relative_to(root).as_posix(), "sha256": room_fixture._sha256(geometry)},
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
    return manifest_path, {"case": case, "paths": paths, "job": job_path, "geometry": geometry, "dxf": source_dxf}


def test_sgi_schema_is_closed_pointer_manifest(tmp_path):
    schema = json.loads((REPO / "sgi_screening_acceptance.v1.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    manifest, _ = _sgi_bundle(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(payload)) == []
    payload["status"] = "PASS"
    assert list(Draft202012Validator(schema).iter_errors(payload))


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
    job = json.loads(bundle["job"].read_text(encoding="utf-8"))
    job["attempts"] = 1
    job["recovery"]["post_resume_input_sha256"] = "0" * 64
    bundle["job"].write_text(json.dumps(job), encoding="utf-8")
    result = validate_restart_integrity(manifest, tmp_path)
    assert "RESTART_ATTEMPT_COUNT_INVALID" in result["blockers"]
    assert "RESTART_INPUT_HASH_CHANGED" in result["blockers"]


def test_sgi_rejects_latest_output_alias_and_tampered_result(tmp_path):
    from cfd_working_room import validate_sgi_screening_acceptance

    manifest, bundle = _sgi_bundle(tmp_path)
    assert "EVALUATOR_OUTPUT_ALIASES_INPUT" in validate_sgi_screening_acceptance(
        manifest, tmp_path, evaluator_output_path=manifest
    )["blockers"]
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["solver_case"]["case_path"] = "_body_solver/latest"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    assert "SGI_CASE_PATH_INVALID" in validate_sgi_screening_acceptance(manifest, tmp_path)["blockers"]

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
        assert f"SGI_ARTIFACT_REF_INVALID:{key}" in validate_sgi_screening_acceptance(manifest, tmp_path)["blockers"]


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
