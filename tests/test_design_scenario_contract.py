import json
from pathlib import Path

from jsonschema import Draft202012Validator

from test_project_model import _conditions, _geometry


REPO = Path(__file__).resolve().parents[1]


def _validator(name: str) -> Draft202012Validator:
    value = json.loads((REPO / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(value)
    return Draft202012Validator(value)


def test_produced_design_scenario_and_run_records_are_closed_schema_documents(tmp_path):
    from project_model import create_case_identity, create_design, create_scenario

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
        run_id="anchor", solver_profile="design_limited_second_order_v1",
    )

    for schema_name, artifact in (
        ("design.v1.schema.json", design),
        ("scenario.v1.schema.json", scenario),
        ("case_identity.v1.schema.json", identity),
    ):
        record = json.loads(Path(artifact["path"]).read_text(encoding="utf-8"))
        assert list(_validator(schema_name).iter_errors(record)) == []
        record["undeclared"] = True
        assert list(_validator(schema_name).iter_errors(record))


def test_operating_changes_are_scenario_variations_but_shape_changes_require_design(tmp_path):
    from project_model import classify_scenario_variation

    base = _conditions()
    airflow = _conditions(airflow_cmh=444.0)
    temperature = _conditions(supply_temperature_k=295.15)
    shape = _conditions()
    shape["terminals"][0]["normal"] = [0, 0, -1]

    assert classify_scenario_variation(base, airflow) == "SCENARIO_REVISION"
    assert classify_scenario_variation(base, temperature) == "SCENARIO_REVISION"
    assert classify_scenario_variation(base, shape) == "DESIGN_REVISION_REQUIRED"
