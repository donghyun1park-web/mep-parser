import hashlib
import json
from pathlib import Path
import shutil

from jsonschema import Draft202012Validator

import cfd_evidence
import cfd_numerical_sensitivity_job
import cfd_physics
import project_model
from test_cfd_evidence import make_complete_case


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _conditions(*, airflow: float, temperature: float, heat: float) -> dict:
    selector = {
        "contract": "occupied_volume_band.v1",
        "coordinate_source": "cell_center_m_agl",
        "z_min_agl_m": 0.1,
        "z_max_agl_m": 1.8,
    }
    return {
        "terminals": [
            {"terminal_id": "working-room-supply", "airflow_cmh": airflow,
             "supply_temperature_k": temperature},
            {"terminal_id": "working-room-exhaust", "airflow_cmh": airflow},
        ],
        "heat_sources": [{"source_id": "manual_heat_1",
                          "convective_power_w": heat,
                          "authority": "user_confirmed:lobby_people_estimate"}],
        "occupancy": {"people_count": 30, "schedule_name": "design_peak"},
        "weather": None,
        "occupied_volume": {
            "selector": selector,
            "floor_elevation_m": 0.0,
            "authority": "user_confirmed:occupied-review-1",
        },
        "operating_period": {"duration_s": 240.0},
        "mesh_intent": {"preset": "detailed", "background_cell_m": 0.125},
        "physics_intent": {"profile_name": "design_limited_second_order_v1",
                           "profile_scope": "thermal_numerics"},
    }


def _attach_qoi(
    case: Path, *, selector: dict, temperature: float, speed: float,
    sync_progress: bool = True,
) -> None:
    selector = cfd_numerical_sensitivity_job.normalize_occupied_volume_band(selector)
    manifest_path = case / "result_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_path = case / manifest["source"]["path"]
    qoi_path = case / "results" / "occupied_volume_qoi.json"
    _write_json(qoi_path, {
        "contract": "occupied_volume_qoi.v1",
        "source_vtu_sha256": _sha(source_path),
        "selector": selector,
        "selector_sha256": selector["selector_sha256"],
        "floor_elevation_m": 0.0,
        "scope": "selected_occupied_volume_band",
        "sample_count": 20,
        "aggregation": "cell_volume_weighted",
        "temperature": {"mean_k": temperature, "p95_k": temperature + 1.0},
        "velocity": {"p95_speed_m_s": speed},
    })
    summary_path = case / "results" / "body_fitted_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["temperature"].update({
        "volume_weighted_mean_k": temperature,
        "volume_weighted_p95_k": temperature + 1.0,
        "hottest_cell": {"temperature_k": temperature + 2.0,
                         "centre_m": [1.0, 2.0, 1.5]},
    })
    summary["velocity"]["occupied_p95_speed_m_s"] = speed
    _write_json(summary_path, summary)
    run_path = case / "run_manifest.json"
    run = json.loads(run_path.read_text(encoding="utf-8"))
    run["airflow"] = {"actual_supply_cmh": 444.0,
                      "actual_exhaust_cmh": 443.0}
    run.setdefault("numerical_quality", {})["flux_balance"] = {
        "available": True,
        "inflow_m3_s": 0.125,
        "outflow_m3_s": 0.124,
        "method": "solved_boundary_phi_signed_volumetric_flux",
    }
    run["thermal_progress"]["energy_balance"]["transient_closure_ratio"] = 0.99
    _write_json(run_path, run)
    if sync_progress:
        _write_json(case / "thermal_progress.json", run["thermal_progress"])
    thermal_path = case / "thermal_input.json"
    thermal = json.loads(thermal_path.read_text(encoding="utf-8"))
    thermal.setdefault("settings", {}).update({
        "occupied_volume_selector": selector,
        "occupied_floor_elevation_m": 0.0,
    })
    _write_json(thermal_path, thermal)
    run["effective_settings"] = thermal["settings"]
    run["input"]["thermal_input_sha256"] = _sha(thermal_path)
    provenance = run["input"]["numerical_provenance"]
    provenance["thermal_input_sha256"] = _sha(thermal_path)
    provenance["effective_settings_sha256"] = cfd_physics._canonical_json_sha256(
        thermal["settings"]
    )
    _write_json(run_path, run)
    if sync_progress:
        _write_json(case / "thermal_progress.json", run["thermal_progress"])
    manifest["summary_sha256"] = _sha(summary_path)
    manifest["run_manifest_sha256"] = _sha(run_path)
    manifest["thermal_input_sha256"] = _sha(thermal_path)
    manifest["occupied_qoi"] = {
        "path": qoi_path.relative_to(case).as_posix(),
        "sha256": _sha(qoi_path),
        "selector_sha256": selector["selector_sha256"],
    }
    _write_json(manifest_path, manifest)


def comparable_runs(
    tmp_path: Path, *, second_selector: str | None = None,
    block_second_health: bool = False,
):
    paths = make_complete_case(tmp_path)
    root, first = paths["root"], paths["case"]
    second = first.with_name("case-b")
    shutil.copytree(first, second)
    selector_a = _conditions(
        airflow=444.0, temperature=291.15, heat=15500.0,
    )["occupied_volume"]["selector"]
    _attach_qoi(first, selector=selector_a, temperature=294.0, speed=0.20)
    selector_b = dict(selector_a)
    if second_selector is not None:
        selector_b["z_max_agl_m"] = 1.9
    _attach_qoi(second, selector=selector_b,
                temperature=296.0, speed=0.28,
                sync_progress=not block_second_health)
    for case in (first, second):
        built = cfd_evidence.build_case_evidence(case, projects_root=root)
        assert built["contract"] == "case_evidence.v1"
    design = project_model.create_design(
        root, geometry_path=paths["geometry"], name="Reviewed room",
        created_by="user:mep-01",
    )
    scenarios = []
    identities = []
    for index, case in enumerate((first, second)):
        conditions = _conditions(
            airflow=444.0 + index * 56.0,
            temperature=291.15 - index,
            heat=15500.0 + index * 1000.0,
        )
        if index == 1 and second_selector is not None:
            conditions["occupied_volume"]["selector"]["z_max_agl_m"] = 1.9
        scenario = project_model.create_scenario(
            Path(design["path"]), name=f"Scenario {index + 1}",
            operating_conditions=conditions, purpose="design_review_candidate",
        )
        identity = project_model.create_case_identity(
            Path(design["path"]), Path(scenario["path"]),
            run_id=f"comparison-{index + 1}",
            solver_profile="design_limited_second_order_v1",
        )
        project_model.link_run_identity(case, Path(identity["path"]))
        scenarios.append(scenario)
        identities.append(identity)
    return root, design, scenarios, identities, (first, second)


def test_compare_runs_recomputes_same_design_evidence_and_bounded_kpis(tmp_path):
    from cfd_compare import compare_runs

    root, design, scenarios, identities, _ = comparable_runs(tmp_path)
    comparison = compare_runs(
        [Path(row["path"]) for row in identities], projects_root=root,
    )

    assert comparison["contract"] == "scenario_comparison.v1"
    assert comparison["eligible"] is True
    assert comparison["blockers"] == []
    assert comparison["design_revision_sha256"] == design["revision_sha256"]
    expected_selector = cfd_numerical_sensitivity_job.normalize_occupied_volume_band(
        _conditions(airflow=444.0, temperature=291.15, heat=15500.0)[
            "occupied_volume"
        ]["selector"]
    )["selector_sha256"]
    assert comparison["qoi_selector_sha256"] == expected_selector
    assert comparison["scenario_diff"]
    assert [run["kpis"]["temperature"]["volume_weighted_mean_k"]
            for run in comparison["runs"]] == [294.0, 296.0]
    assert comparison["runs"][0]["kpis"]["velocity"] == {
        "occupied_p95_speed_m_s": 0.2,
    }
    assert comparison["runs"][0]["kpis"]["airflow"] == {
        "actual_supply_cmh": 450.0, "actual_exhaust_cmh": 446.4,
        "basis": "solved_boundary_phi_signed_volumetric_flux",
    }
    assert "maximum_speed" not in json.dumps(comparison["runs"][0]["kpis"])


def test_compare_runs_blocks_mismatched_design_before_claiming_results(tmp_path):
    from cfd_compare import compare_runs
    from cfd_working_room import build_working_room_geometry

    root, _, _, identities, _ = comparable_runs(tmp_path)
    geometry = build_working_room_geometry()
    geometry["source"] = "other-room"
    geometry["elements"]["equipment"][0]["center"][0] += 50.0
    path = root / "other.geometry.v2.json"
    _write_json(path, geometry)
    design = project_model.create_design(
        root, geometry_path=path, name="Other room", created_by="user:mep-01",
    )
    scenario = project_model.create_scenario(
        Path(design["path"]), name="Other", operating_conditions=_conditions(
            airflow=444.0, temperature=291.15, heat=15500.0,
        ), purpose="design_review_candidate",
    )
    identity = project_model.create_case_identity(
        Path(design["path"]), Path(scenario["path"]), run_id="other",
        solver_profile="design_limited_second_order_v1",
    )

    comparison = compare_runs(
        [Path(identities[0]["path"]), Path(identity["path"])],
        projects_root=root,
    )
    assert comparison["eligible"] is False
    assert "DESIGN_REVISION_MISMATCH" in {row["code"] for row in comparison["blockers"]}
    assert comparison["runs"][1]["kpis"] is None


def test_compare_runs_blocks_incomplete_evidence_and_selector_mismatch(tmp_path):
    from cfd_compare import compare_runs

    root, _, _, identities, cases = comparable_runs(
        tmp_path, second_selector="b" * 64,
    )
    selector_mismatch = compare_runs(
        [Path(row["path"]) for row in identities], projects_root=root,
    )
    assert "QOI_SELECTOR_MISMATCH" in {
        row["code"] for row in selector_mismatch["blockers"]
    }

    (cases[1] / "case_evidence.v1.json").unlink()
    incomplete = compare_runs(
        [Path(row["path"]) for row in identities], projects_root=root,
    )
    assert "CASE_EVIDENCE_MISSING" in {row["code"] for row in incomplete["blockers"]}


def test_compare_runs_blocks_authoritatively_blocked_case_health(tmp_path):
    from cfd_compare import compare_runs

    root, _, _, identities, _ = comparable_runs(tmp_path, block_second_health=True)
    comparison = compare_runs(
        [Path(row["path"]) for row in identities], projects_root=root,
    )

    assert comparison["eligible"] is False
    assert "CASE_HEALTH_BLOCKED" in {row["code"] for row in comparison["blockers"]}


def test_compare_runs_rejects_qoi_not_bound_to_result_vtu_or_scenario_selector(tmp_path):
    from cfd_compare import compare_runs

    root, _, _, identities, cases = comparable_runs(tmp_path)
    qoi_path = cases[1] / "results" / "occupied_volume_qoi.json"
    qoi = json.loads(qoi_path.read_text(encoding="utf-8"))
    qoi["source_vtu_sha256"] = "f" * 64
    _write_json(qoi_path, qoi)
    manifest_path = cases[1] / "result_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["occupied_qoi"]["sha256"] = _sha(qoi_path)
    _write_json(manifest_path, manifest)
    cfd_evidence.build_case_evidence(cases[1], projects_root=root)

    comparison = compare_runs(
        [Path(row["path"]) for row in identities], projects_root=root,
    )

    assert comparison["eligible"] is False
    assert "QOI_SOURCE_VTU_MISMATCH" in {
        row["code"] for row in comparison["blockers"]
    }


def test_comparison_contract_has_strict_top_level_schema():
    schema = json.loads(Path("scenario_comparison.v1.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["contract"]["const"] == "scenario_comparison.v1"
    assert schema["additionalProperties"] is False
