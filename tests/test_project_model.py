import json
from pathlib import Path

import pytest


def _geometry(root: Path, name: str, *, supply_x_mm: float) -> Path:
    from cfd_working_room import build_working_room_geometry

    value = build_working_room_geometry()
    value["source"] = name
    terminal = next(
        row for row in value["elements"]["equipment"]
        if row["semantic"].get("role") == "supply"
    )
    terminal["center"][0] = supply_x_mm
    path = root / name
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _conditions(*, airflow_cmh=360.0, supply_temperature_k=293.15):
    return {
        "terminals": [
            {
                "terminal_id": "working-room-supply",
                "airflow_cmh": airflow_cmh,
                "supply_temperature_k": supply_temperature_k,
            },
            {"terminal_id": "working-room-exhaust", "airflow_cmh": airflow_cmh},
        ],
        "heat_sources": [
            {
                "source_id": "manual_heat_1",
                "convective_power_w": 1000.0,
                "authority": "user_confirmed:lobby_people_estimate",
            }
        ],
        "occupancy": {"people_count": 30, "schedule_name": "design_peak"},
        "weather": None,
        "operating_period": {"duration_s": 240.0},
        "mesh_intent": {"preset": "detailed", "background_cell_m": 0.125},
        "physics_intent": {
            "solver": "buoyantBoussinesqPimpleFoam",
            "turbulence_model": "kOmegaSST",
        },
    }


def test_geometry_change_creates_new_immutable_design_revision(tmp_path):
    from project_model import create_design, revise_design, validate_design_revision

    first = create_design(
        tmp_path,
        geometry_path=_geometry(tmp_path, "geometry-a.json", supply_x_mm=0.0),
        name="전기실",
        created_by="user:mep-01",
    )
    first_bytes = Path(first["path"]).read_bytes()
    second = revise_design(
        first["design_id"],
        geometry_path=_geometry(tmp_path, "geometry-b.json", supply_x_mm=10.0),
        reason="급기 위치 수정",
        revised_by="user:mep-01",
    )

    assert first["design_id"] == second["design_id"]
    assert first["revision_sha256"] != second["revision_sha256"]
    assert first["revision_number"] == 1
    assert second["revision_number"] == 2
    assert Path(first["path"]).read_bytes() == first_bytes
    assert Path(first["path"]) != Path(second["path"])
    assert validate_design_revision(Path(first["path"]), projects_root=tmp_path) == []
    assert validate_design_revision(Path(second["path"]), projects_root=tmp_path) == []


def test_display_name_change_does_not_change_design_or_scenario_identity(tmp_path):
    from project_model import create_design, create_scenario

    geometry = _geometry(tmp_path, "geometry.json", supply_x_mm=0.0)
    first_design = create_design(
        tmp_path, geometry_path=geometry, name="전기실", created_by="user:mep-01",
    )
    renamed_design = create_design(
        tmp_path, geometry_path=geometry, name="Electrical Room", created_by="user:mep-01",
    )
    first_scenario = create_scenario(
        Path(first_design["path"]), name="기본안",
        operating_conditions=_conditions(), purpose="screening",
    )
    renamed_scenario = create_scenario(
        Path(first_design["path"]), name="Base Case",
        operating_conditions=_conditions(), purpose="screening",
    )

    assert first_design["design_id"] == renamed_design["design_id"]
    assert first_scenario["scenario_id"] == renamed_scenario["scenario_id"]


def test_geometry_json_formatting_does_not_change_design_identity(tmp_path):
    from project_model import create_design

    geometry = _geometry(tmp_path, "geometry.json", supply_x_mm=0.0)
    first = create_design(
        tmp_path, geometry_path=geometry, name="전기실", created_by="user:mep-01",
    )
    value = json.loads(geometry.read_text(encoding="utf-8"))
    geometry.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8",
    )
    second = create_design(
        tmp_path, geometry_path=geometry, name="전기실", created_by="user:mep-01",
    )

    assert first["design_id"] == second["design_id"]


def test_case_identity_hash_binds_current_design_and_scenario(tmp_path):
    from project_model import (
        create_case_identity,
        create_design,
        create_scenario,
        validate_case_identity,
    )

    design = create_design(
        tmp_path,
        geometry_path=_geometry(tmp_path, "geometry.json", supply_x_mm=0.0),
        name="전기실",
        created_by="user:mep-01",
    )
    scenario = create_scenario(
        Path(design["path"]), name="기본안",
        operating_conditions=_conditions(), purpose="screening",
    )
    identity = create_case_identity(
        Path(design["path"]), Path(scenario["path"]),
        run_id="working-room-anchor", solver_profile="design_limited_second_order_v1",
    )

    assert identity["run_id"].startswith("run-")
    assert validate_case_identity(Path(identity["path"]), projects_root=tmp_path) == []

    scenario_payload = json.loads(Path(scenario["path"]).read_text(encoding="utf-8"))
    scenario_payload["name"] = "tampered"
    Path(scenario["path"]).write_text(json.dumps(scenario_payload), encoding="utf-8")
    assert {row["code"] for row in validate_case_identity(
        Path(identity["path"]), projects_root=tmp_path,
    )} == {"SCENARIO_REVISION_CHANGED"}


def test_scenario_rejects_geometry_and_terminal_shape_mutation(tmp_path):
    from project_model import ProjectModelError, create_design, create_scenario

    design = create_design(
        tmp_path,
        geometry_path=_geometry(tmp_path, "geometry.json", supply_x_mm=0.0),
        name="전기실",
        created_by="user:mep-01",
    )
    for forbidden in (
        {"geometry": {}},
        {"terminals": [{"terminal_id": "working-room-supply", "role": "exhaust"}]},
        {"terminals": [{"terminal_id": "working-room-supply", "normal": [0, 0, -1]}]},
        {"terminals": [{"terminal_id": "working-room-supply", "diameter_m": 0.3}]},
    ):
        with pytest.raises(ProjectModelError, match="SCENARIO_GEOMETRY_MUTATION"):
            create_scenario(
                Path(design["path"]), name="잘못된 대안",
                operating_conditions=forbidden, purpose="screening",
            )


def test_design_publish_failure_leaves_no_partial_revision(tmp_path, monkeypatch):
    import project_model

    geometry = _geometry(tmp_path, "geometry.json", supply_x_mm=0.0)
    real_replace = project_model.os.replace

    def fail_design_replace(source, target):
        if str(target).endswith(".design.v1.json"):
            raise OSError("simulated atomic replace failure")
        return real_replace(source, target)

    monkeypatch.setattr(project_model.os, "replace", fail_design_replace)
    with pytest.raises(OSError, match="simulated atomic replace failure"):
        project_model.create_design(
            tmp_path, geometry_path=geometry, name="전기실", created_by="user:mep-01",
        )

    revisions = tmp_path / "_project_model" / "designs"
    assert list(revisions.rglob("*.design.v1.json")) == []
    assert list(revisions.rglob("*.tmp")) == []


def test_design_validator_rejects_reference_path_escape(tmp_path):
    import project_model

    design = project_model.create_design(
        tmp_path,
        geometry_path=_geometry(tmp_path, "geometry.json", supply_x_mm=0.0),
        name="전기실",
        created_by="user:mep-01",
    )
    path = Path(design["path"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["geometry"]["path"] = "../outside.json"
    payload["revision_sha256"] = project_model._revision_sha(payload, "revision_sha256")
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert {row["code"] for row in project_model.validate_design_revision(
        path, projects_root=tmp_path,
    )} == {"DESIGN_REVISION_INVALID"}
