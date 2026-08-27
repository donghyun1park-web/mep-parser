import json
from pathlib import Path
import urllib.request

from jsonschema import Draft202012Validator

import cfd_studio
import project_model
from test_cfd_compare import _conditions, comparable_runs
from test_studio_workflow import _request_json, _studio_server


def _executable_identity(root: Path):
    from cfd_working_room import build_working_room_geometry

    geometry_path = root / "reviewed-working.geometry.v2.json"
    geometry_path.write_text(
        json.dumps(build_working_room_geometry(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    design = project_model.create_design(
        root, geometry_path=geometry_path, name="Executable reviewed room",
        created_by="user:mep-01",
    )
    conditions = _conditions(airflow=520.0, temperature=290.15, heat=16500.0)
    conditions["input_authority"] = {
        "terminals[working-room-supply].airflow_cmh": "user_confirmed:review-1",
        "terminals[working-room-supply].supply_temperature_k": "user_confirmed:review-1",
        "terminals[working-room-exhaust].airflow_cmh": "user_confirmed:review-1",
        "heat_sources[manual_heat_1].convective_power_w": "user_confirmed:review-1",
    }
    conditions["occupied_volume"] = {
        "selector": {
            "contract": "occupied_volume_band.v1",
            "coordinate_source": "cell_center_m_agl",
            "z_min_agl_m": 0.1,
            "z_max_agl_m": 1.8,
        },
        "floor_elevation_m": 0.0,
        "authority": "user_confirmed:review-1",
    }
    scenario = project_model.create_scenario(
        Path(design["path"]), name="Executable alternative",
        operating_conditions=conditions, purpose="design_review_candidate",
    )
    identity = project_model.create_case_identity(
        Path(design["path"]), Path(scenario["path"]), run_id="overlay-test",
        solver_profile="design_limited_second_order_v1",
    )
    return design, scenario, identity


def test_project_model_api_crud_clone_run_and_compare(tmp_path, monkeypatch):
    root, design, scenarios, identities, _ = comparable_runs(tmp_path)
    monkeypatch.setattr(cfd_studio, "ROOT", str(root))

    with _studio_server() as base:
        status, designs, _ = _request_json(base, "/api/designs")
        assert status == 200, designs
        assert designs["designs"][0]["design_id"] == design["design_id"]
        status, detail, _ = _request_json(base, f"/api/designs/{design['design_id']}")
        assert status == 200, detail
        assert detail["revisions"][0]["geometry_review"]["ready"] is True
        status, listed, _ = _request_json(
            base, f"/api/scenarios?design={design['design_id']}",
        )
        assert status == 200, listed
        assert len(listed["scenarios"]) == 2
        status, cloned, _ = _request_json(base, "/api/scenario-clone", payload={
            "scenario_revision": scenarios[0]["path"],
            "name": "Cloned alternative",
            "operating_conditions": _conditions(
                airflow=520.0, temperature=290.15, heat=16500.0,
            ),
        })
        assert status == 200, cloned
        assert cloned["scenario"]["scenario_id"] != scenarios[0]["scenario_id"]
        assert cloned["scenario_diff"]
        status, runs, _ = _request_json(
            base, f"/api/runs?scenario={scenarios[0]['scenario_id']}",
        )
        assert status == 200, runs
        assert runs["runs"][0]["run_id"] == identities[0]["run_id"]
        query = "&".join(f"run={row['run_id']}" for row in identities)
        status, comparison, _ = _request_json(base, f"/api/scenario-compare?{query}")
        assert status == 200, comparison
        assert comparison["eligible"] is True
        assert comparison["report_url"].startswith("/scenario-compare-report/")
        schema = json.loads(Path("scenario_comparison.v1.schema.json").read_text(
            encoding="utf-8"
        ))
        assert list(Draft202012Validator(schema).iter_errors(comparison)) == []


def test_post_design_scenario_and_run_use_project_confined_artifacts(tmp_path, monkeypatch):
    paths = comparable_runs(tmp_path)
    root = paths[0]
    monkeypatch.setattr(cfd_studio, "ROOT", str(root))
    geometry = next((root / "imports").rglob("geometry.v2.json"))

    with _studio_server() as base:
        status, created, _ = _request_json(base, "/api/designs", payload={
            "geometry_path": geometry.relative_to(root).as_posix(),
            "name": "API reviewed room", "created_by": "user:mep-02",
        })
        assert status == 200
        status, scenario, _ = _request_json(base, "/api/scenarios", payload={
            "design_revision": created["design"]["path"],
            "name": "API scenario", "purpose": "screening",
            "operating_conditions": _conditions(
                airflow=480.0, temperature=291.15, heat=15500.0,
            ),
        })
        assert status == 200, scenario
        status, run, _ = _request_json(base, "/api/scenario-runs", payload={
            "design_revision": created["design"]["path"],
            "scenario_revision": scenario["scenario"]["path"],
            "run_id": "api-created", "solver_profile": "design_limited_second_order_v1",
        })
        assert status == 200
        assert run["runtime_state"] == "identity_created"
        assert run["comparison_eligible"] is False

        status, escaped, _ = _request_json(base, "/api/designs", payload={
            "geometry_path": "../outside.json", "name": "Bad",
            "created_by": "user:mep-02",
        })
        assert status == 400
        assert escaped["code"] == "PROJECT_PATH_ESCAPE"


def test_legacy_routes_and_new_workflow_page_remain_available(tmp_path, monkeypatch):
    root, *_ = comparable_runs(tmp_path)
    legacy = root / "legacy-visible"
    legacy.mkdir()
    (legacy / "cfd_case_meta.json").write_text('{"name":"legacy-visible"}', encoding="utf-8")
    monkeypatch.setattr(cfd_studio, "ROOT", str(root))

    with _studio_server() as base:
        for path in ("/", "/new", "/field-run", "/release-readiness", "/uat",
                     "/project-workflow", "/designs", "/scenarios", "/runs"):
            with urllib.request.urlopen(base + path, timeout=10) as response:
                assert response.status == 200, path
        status, cases, _ = _request_json(base, "/api/cases")
        assert status == 200
        row = next(item for item in cases["cases"] if item["dir"] == "legacy-visible")
        assert row["case_identity_status"] == "legacy_unlinked"
        assert row["scenario_comparison_eligible"] is False
        status, missing, _ = _request_json(base, "/api/body-results/not-there")
        assert status == 404
        assert missing["ok"] is False
    assert "/api/designs" in cfd_studio.PAGE_PROJECT_WORKFLOW
    assert "/api/scenario-compare" in cfd_studio.PAGE_PROJECT_WORKFLOW
    assert "첫 Scenario 생성" in cfd_studio.PAGE_PROJECT_WORKFLOW
    assert "x.design.design_id===selectedDesign" in cfd_studio.PAGE_PROJECT_WORKFLOW


def test_scenario_run_materializes_an_identity_bound_overlay_without_mutating_design(tmp_path, monkeypatch):
    root, _, _, _, _ = comparable_runs(tmp_path)
    monkeypatch.setattr(cfd_studio, "ROOT", str(root))
    design, scenario, identity = _executable_identity(root)
    design_geometry = root / json.loads(Path(design["path"]).read_text(
        encoding="utf-8"
    ))["geometry"]["path"]
    before = design_geometry.read_bytes()

    runtime = cfd_studio.materialize_scenario_run_input(identity["path"])

    assert design_geometry.read_bytes() == before
    generated = json.loads(Path(runtime["geometry_path"]).read_text(encoding="utf-8"))
    by_id = {row["id"]: row["semantic"] for row in generated["elements"]["equipment"]}
    assert by_id["working-room-supply"]["airflow_cmh"] == 520.0
    assert by_id["working-room-exhaust"]["airflow_cmh"] == 520.0
    assert by_id["manual_heat_1"]["convective_power_w"] == 16500.0
    assert runtime["settings"]["thermal_settings"]["supply_temperature_k"] == 290.15
    assert runtime["settings"]["thermal_settings"]["occupied_volume_selector"][
        "selector_sha256"
    ]
    provenance = json.loads(Path(runtime["provenance_path"]).read_text(encoding="utf-8"))
    assert provenance["scenario_revision_sha256"] == scenario["revision_sha256"]


def test_scenario_runtime_overlay_publish_failure_leaves_no_partial_file(tmp_path, monkeypatch):
    root, _, _, _, _ = comparable_runs(tmp_path)
    monkeypatch.setattr(cfd_studio, "ROOT", str(root))
    _, _, identity = _executable_identity(root)
    real_replace = cfd_studio.os.replace

    def fail_runtime_replace(source, target):
        if str(target).endswith("scenario.geometry.v2.json"):
            raise OSError("simulated runtime publish failure")
        return real_replace(source, target)

    monkeypatch.setattr(cfd_studio.os, "replace", fail_runtime_replace)
    try:
        cfd_studio.materialize_scenario_run_input(identity["path"])
    except OSError as exc:
        assert "simulated runtime publish failure" in str(exc)
    else:
        raise AssertionError("runtime publication unexpectedly succeeded")
    runtime_dir = root / "_project_model" / "runtime_inputs" / identity["identity_sha256"]
    assert not (runtime_dir / "scenario.geometry.v2.json").exists()
    assert list(runtime_dir.glob("*.tmp")) == []


def test_report_templates_keep_legacy_filename_and_expose_scope(tmp_path):
    import cfd_report

    root, _, _, identities, cases = comparable_runs(tmp_path)
    for mode in ("screening", "design-review", "field-comparison"):
        result = cfd_report.generate_body_fitted_report(cases[0], report_mode=mode)
        assert result["ok"] is True
        assert Path(result["report_path"]).name == "body_fitted_report.html"
        text = Path(result["report_path"]).read_text(encoding="utf-8")
        assert mode in text
    comparison = __import__("cfd_compare").compare_runs(
        [Path(row["path"]) for row in identities], projects_root=root,
    )
    report = cfd_report.generate_scenario_comparison_report(comparison, projects_root=root)
    text = Path(report["report_path"]).read_text(encoding="utf-8")
    first_page = text.split("</section>", 1)[0]
    assert "입력 차이" in text
    assert "신뢰도 차이" in text
    assert "동일/상이한 evidence scope" in text
    assert str(comparison["scenario_diff"][0]["baseline"]) in first_page
    assert comparison["runs"][0]["run_id"] in first_page
    assert comparison["runs"][0]["case_health"]["evidence"]["sha256"] in first_page


def test_comparison_publication_preserves_order_and_never_overwrites(tmp_path, monkeypatch):
    root, _, _, identities, _ = comparable_runs(tmp_path)
    monkeypatch.setattr(cfd_studio, "ROOT", str(root))
    run_ids = [row["run_id"] for row in identities]

    first = cfd_studio.create_scenario_comparison(run_ids)
    first_path = Path(first["comparison_path"])
    first_bytes = first_path.read_bytes()
    reversed_result = cfd_studio.create_scenario_comparison(list(reversed(run_ids)))

    assert first["report_url"] != reversed_result["report_url"]
    assert first_path.read_bytes() == first_bytes
    assert first["scenario_diff"][0]["baseline"] != (
        reversed_result["scenario_diff"][0]["baseline"]
    )


def test_comparison_report_rejects_changed_pinned_artifact(tmp_path):
    import cfd_report

    root, _, _, identities, _ = comparable_runs(tmp_path)
    comparison = __import__("cfd_compare").compare_runs(
        [Path(row["path"]) for row in identities], projects_root=root,
    )
    identity_path = root / comparison["runs"][0]["artifacts"]["run_identity"]["path"]
    identity_path.write_text(identity_path.read_text(encoding="utf-8") + " ", encoding="utf-8")

    report = cfd_report.generate_scenario_comparison_report(
        comparison, projects_root=root,
    )

    assert report["ok"] is False
    assert "hash mismatch" in report["error"]


def test_comparison_report_recomputes_and_rejects_forged_kpi(tmp_path):
    import cfd_report

    root, _, _, identities, _ = comparable_runs(tmp_path)
    comparison = __import__("cfd_compare").compare_runs(
        [Path(row["path"]) for row in identities], projects_root=root,
    )
    comparison["runs"][0]["kpis"]["temperature"][
        "volume_weighted_mean_k"
    ] += 25.0

    report = cfd_report.generate_scenario_comparison_report(
        comparison, projects_root=root,
    )

    assert report["ok"] is False
    assert "claims differ" in report["error"]


def test_comparison_publication_fails_when_report_validation_fails(tmp_path, monkeypatch):
    root, _, _, identities, _ = comparable_runs(tmp_path)
    monkeypatch.setattr(cfd_studio, "ROOT", str(root))
    monkeypatch.setattr(
        cfd_studio.cfd_report, "generate_scenario_comparison_report",
        lambda *args, **kwargs: {"ok": False, "error": "forced validation failure"},
    )

    try:
        cfd_studio.create_scenario_comparison([row["run_id"] for row in identities])
    except project_model.ProjectModelError as exc:
        assert exc.code == "COMPARISON_REPORT_FAILED"
    else:
        raise AssertionError("comparison publication unexpectedly succeeded")
    comparisons = root / "_project_model" / "comparisons"
    assert list(comparisons.glob("compare-*")) == []
    assert list(comparisons.glob(".compare-*")) == []
