import copy
import json
from pathlib import Path
import tempfile

import pytest


BUILTIN = Path(__file__).resolve().parents[1] / "cfd_templates" / "hvac" / "mixing_ventilation.v1.json"


def _template():
    from cfd_templates import load_hvac_template

    return load_hvac_template(BUILTIN)


def _geometry():
    from cfd_working_room import build_working_room_geometry

    return build_working_room_geometry()


def _immutable_design(root: Path, geometry=None):
    from project_model import create_design

    path = root / "reviewed.geometry.v2.json"
    path.write_text(
        json.dumps(geometry or _geometry(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return create_design(
        root, geometry_path=path, name="Reviewed room", created_by="user:mep-01",
    )


def _user_values():
    authority = "user_confirmed:review-2026-08-24"
    return {
        "terminals": [
            {
                "terminal_id": "working-room-supply",
                "airflow_cmh": 444.0,
                "supply_temperature_k": 291.15,
                "authority": authority,
            },
            {
                "terminal_id": "working-room-exhaust",
                "airflow_cmh": 444.0,
                "authority": authority,
            },
        ],
        "heat_sources": [
            {
                "source_id": "manual_heat_1",
                "convective_power_w": 15500.0,
                "authority": "user_confirmed:lobby_people_estimate",
            }
        ],
        "occupancy": {
            "people_count": 30,
            "schedule_name": "design_peak",
            "authority": authority,
        },
        "weather": None,
        "operating_period": {"duration_s": 240.0},
        "mesh_intent": {"preset": "detailed", "background_cell_m": 0.125},
        "comfort_inputs": {
            "relative_humidity_pct": {"value": 50.0, "authority": authority},
            "metabolic_rate_met": {"value": 1.2, "authority": authority},
            "clothing_clo": {"value": 0.7, "authority": authority},
        },
    }


def test_builtin_templates_are_declarative_and_contain_no_physics_defaults():
    """A built-in template must not become an untraceable physics data source."""
    import cfd_numerics
    from cfd_templates import load_hvac_template

    for name in ("mixing_ventilation.v1.json", "displacement_ventilation.v1.json"):
        template = load_hvac_template(BUILTIN.with_name(name))
        encoded = json.dumps(template, sort_keys=True).lower()
        assert '"default"' not in encoded
        assert '"estimated_values"' not in encoded
        assert template["physics_profile"] == {
            "name": "stabilized_first_order_v1",
            "scope": "thermal_numerics",
        }
        assert template["physics_profile"]["name"] in cfd_numerics.SUPPORTED_PROFILES
        assert '"solver"' not in encoded
        assert '"turbulence_model"' not in encoded


def test_loader_rejects_a_physics_default_even_when_added_to_an_allowed_parameter(tmp_path):
    """Adding 444 CMH as a template default must fail schema validation."""
    from cfd_templates import HVACConfigError, load_hvac_template

    payload = json.loads(BUILTIN.read_text(encoding="utf-8"))
    payload["allowed_parameters"][0]["default"] = 444.0
    path = tmp_path / "unsafe-template.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(HVACConfigError, match="TEMPLATE_SCHEMA_INVALID"):
        load_hvac_template(path)


@pytest.mark.parametrize(
    "remove_authority",
    [
        "terminal_cmh",
        "equipment_kw",
        "external_temperature",
        "relative_humidity",
        "metabolic_rate",
        "clothing",
    ],
)
def test_apply_rejects_unattributed_physics_values(remove_authority):
    """CMH, heat, weather and comfort values need user/approved provenance."""
    from cfd_templates import HVACConfigError, apply_hvac_template

    values = _user_values()
    if remove_authority == "terminal_cmh":
        values["terminals"][0].pop("authority")
    elif remove_authority == "equipment_kw":
        values["heat_sources"][0].pop("authority")
    elif remove_authority == "external_temperature":
        values["weather"] = {"outdoor_temperature_k": 303.15}
    else:
        comfort_key = {
            "relative_humidity": "relative_humidity_pct",
            "metabolic_rate": "metabolic_rate_met",
            "clothing": "clothing_clo",
        }[remove_authority]
        values["comfort_inputs"][comfort_key].pop("authority")

    with pytest.raises(HVACConfigError, match="UNAPPROVED_PHYSICS_VALUE"):
        apply_hvac_template(_template(), _geometry(), user_values=values)


def test_apply_enforces_template_parameter_allowlist_and_validation_rules():
    """A template's allowed fields and bounds must govern the application."""
    from cfd_templates import HVACConfigError, apply_hvac_template

    template = _template()
    template["allowed_parameters"] = [
        row for row in template["allowed_parameters"]
        if row["key"] != "comfort.relative_humidity_pct"
    ]
    with pytest.raises(HVACConfigError, match="PARAMETER_NOT_ALLOWED"):
        apply_hvac_template(template, _geometry(), user_values=_user_values())

    bounded = _template()
    rh = next(
        row for row in bounded["allowed_parameters"]
        if row["key"] == "comfort.relative_humidity_pct"
    )
    rh["validation"]["maximum"] = 40
    with pytest.raises(HVACConfigError, match="PHYSICS_VALUE_INVALID"):
        apply_hvac_template(bounded, _geometry(), user_values=_user_values())


def test_unresolved_approved_source_cannot_self_assert_external_temperature():
    """An approved_source label alone is not proof that its evidence was approved."""
    from cfd_templates import HVACConfigError, apply_hvac_template

    values = _user_values()
    values["weather"] = {
        "outdoor_temperature_k": 303.15,
        "authority": "approved_source:weather-file-2026-08-24",
    }

    with pytest.raises(HVACConfigError, match="APPROVED_SOURCE_UNVERIFIED"):
        apply_hvac_template(_template(), _geometry(), user_values=values)


def test_apply_maps_by_stable_element_id_not_display_label_or_patch_order(tmp_path):
    """Reordering and relabelling geometry cannot redirect terminal values."""
    from cfd_templates import apply_hvac_template

    geometry = _geometry()
    geometry["elements"]["equipment"].reverse()
    for index, row in enumerate(geometry["elements"]["equipment"]):
        row["display_label"] = f"renamed-{index}"

    result = apply_hvac_template(
        _template(), _immutable_design(tmp_path, geometry), user_values=_user_values(),
    )

    assert result["ready"] is True
    assert result["blockers"] == []
    assert result["terminal_mapping"] == [
        {"terminal_id": "working-room-exhaust", "role": "exhaust"},
        {"terminal_id": "working-room-supply", "role": "supply"},
    ]
    by_id = {
        row["terminal_id"]: row
        for row in result["operating_conditions"]["terminals"]
    }
    assert by_id["working-room-supply"]["airflow_cmh"] == 444.0
    assert by_id["working-room-exhaust"]["airflow_cmh"] == 444.0


def test_apply_returns_blockers_for_missing_duplicate_and_unbalanced_terminals():
    """Topology and flow inconsistencies must not produce a ready scenario draft."""
    from cfd_templates import apply_hvac_template

    missing = _geometry()
    missing["elements"]["equipment"] = [
        row for row in missing["elements"]["equipment"]
        if row["semantic"].get("role") != "exhaust"
    ]
    duplicate = _geometry()
    supply = next(
        row for row in duplicate["elements"]["equipment"]
        if row["semantic"].get("role") == "supply"
    )
    duplicate["elements"]["equipment"].append(copy.deepcopy(supply))
    unbalanced_values = _user_values()
    unbalanced_values["terminals"][1]["airflow_cmh"] = 400.0

    missing_result = apply_hvac_template(_template(), missing, user_values=_user_values())
    duplicate_result = apply_hvac_template(_template(), duplicate, user_values=_user_values())
    imbalance_result = apply_hvac_template(
        _template(), _geometry(), user_values=unbalanced_values,
    )

    assert {row["code"] for row in missing_result["blockers"]} == {
        "DESIGN_REVISION_REQUIRED",
        "MISSING_REQUIRED_TERMINAL_ROLE",
        "UNKNOWN_TERMINAL_ID",
    }
    assert {row["code"] for row in duplicate_result["blockers"]} == {
        "DESIGN_REVISION_REQUIRED",
        "DUPLICATE_TERMINAL_ID",
    }
    assert {row["code"] for row in imbalance_result["blockers"]} == {
        "DESIGN_REVISION_REQUIRED",
        "AIRFLOW_IMBALANCE",
    }
    assert missing_result["ready"] is False
    assert duplicate_result["ready"] is False
    assert imbalance_result["ready"] is False


def test_geometry_duplicate_stability_marker_blocks_terminal_mapping():
    """A collision-disambiguated geometry ID cannot silently become a Scenario key."""
    from cfd_templates import apply_hvac_template

    geometry = _geometry()
    supply = next(
        row for row in geometry["elements"]["equipment"]
        if row["semantic"].get("role") == "supply"
    )
    supply["id_stability"] = "geometry_derived_duplicate"

    result = apply_hvac_template(_template(), geometry, user_values=_user_values())

    assert "UNSTABLE_TERMINAL_ID" in {row["code"] for row in result["blockers"]}
    assert result["ready"] is False


def test_unreviewed_geometry_cannot_be_used_as_a_template_design():
    """A direct geometry object cannot bypass the immutable Design review gate."""
    from cfd_templates import HVACConfigError, apply_hvac_template

    geometry = _geometry()
    geometry["review"].update({"ready": False, "blocking": True, "blocker_count": 1})

    with pytest.raises(HVACConfigError, match="DESIGN_REVIEW_NOT_READY"):
        apply_hvac_template(_template(), geometry, user_values=_user_values())


def test_raw_geometry_recomputes_schema_stability_and_confirmation():
    """Caller-authored ready flags cannot hide unstable or unconfirmed terminals."""
    from cfd_templates import HVACConfigError, apply_hvac_template

    missing_stability = _geometry()
    missing_stability["elements"]["equipment"][0].pop("id_stability")
    with pytest.raises(HVACConfigError, match="DESIGN_SCHEMA_INVALID"):
        apply_hvac_template(
            _template(), missing_stability, user_values=_user_values(),
        )

    unconfirmed = _geometry()
    unconfirmed["elements"]["equipment"][0].update({
        "confirmed": False, "confirmation_state": "unconfirmed",
    })
    result = apply_hvac_template(
        _template(), unconfirmed, user_values=_user_values(),
    )
    assert "UNCONFIRMED_TERMINAL" in {row["code"] for row in result["blockers"]}
    assert result["ready"] is False


def test_apply_requires_every_reviewed_design_terminal_and_heat_source_input():
    """Omitting a reviewed source is an explicit blocker, never a silent zero/default."""
    from cfd_templates import apply_hvac_template

    values = _user_values()
    values["terminals"].pop()
    values["heat_sources"] = []

    result = apply_hvac_template(_template(), _geometry(), user_values=values)

    assert {row["code"] for row in result["blockers"]} == {
        "DESIGN_REVISION_REQUIRED",
        "MISSING_HEAT_SOURCE_INPUT",
        "MISSING_TERMINAL_INPUT",
    }
    assert result["operating_conditions"] is None


def test_apply_keeps_authorities_and_defers_comfort_without_evaluating_it():
    """A template records inputs but cannot claim a PMV/PPD evaluation."""
    from cfd_templates import apply_hvac_template

    conditions = _applied_conditions()

    assert conditions["input_authority"][
        "terminals[working-room-supply].airflow_cmh"
    ].startswith("user_confirmed:")
    assert conditions["physics_intent"] == {
        "profile_name": "stabilized_first_order_v1",
        "profile_scope": "thermal_numerics",
    }
    with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
        result = apply_hvac_template(
            _template(), _immutable_design(Path(directory)), user_values=_user_values(),
        )
        assert result["deferred_inputs"]["comfort_status"] == "NOT_EVALUATED"
        assert result["deferred_inputs"]["comfort"]["relative_humidity_pct"]["value"] == 50.0


def test_template_application_creates_a_valid_scenario_from_immutable_design(tmp_path):
    """The public apply contract must consume the real Design response, not a test-only shape."""
    from cfd_templates import apply_hvac_template
    from project_model import create_design, create_scenario, validate_scenario_revision

    geometry_path = tmp_path / "reviewed.geometry.v2.json"
    geometry_path.write_text(
        json.dumps(_geometry(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    design = create_design(
        tmp_path,
        geometry_path=geometry_path,
        name="Reviewed room",
        created_by="user:mep-01",
    )

    applied = apply_hvac_template(_template(), design, user_values=_user_values())
    scenario = create_scenario(
        Path(design["path"]),
        name="444 CMH reviewed option",
        operating_conditions=applied["operating_conditions"],
        purpose="screening",
    )

    assert applied["design"]["revision_sha256"] == design["revision_sha256"]
    assert validate_scenario_revision(
        Path(scenario["path"]), projects_root=tmp_path,
    ) == []


def test_semantic_diff_uses_stable_ids_units_and_engineering_effects():
    """A reviewer should see physical changes, independent of list order."""
    from project_model import scenario_diff

    apply_result = _applied_conditions()
    baseline = {"operating_conditions": apply_result}
    candidate = copy.deepcopy(baseline)
    candidate_conditions = candidate["operating_conditions"]
    candidate_conditions["terminals"][0]["airflow_cmh"] = 500.0
    candidate_conditions["heat_sources"][0]["convective_power_w"] = 16000.0

    rows = scenario_diff(baseline, candidate)
    by_path = {row["path"]: row for row in rows}

    assert by_path[
        "operating_conditions.terminals[working-room-exhaust].airflow_cmh"
    ] == {
        "path": "operating_conditions.terminals[working-room-exhaust].airflow_cmh",
        "baseline": 444.0,
        "candidate": 500.0,
        "unit": "CMH",
        "engineering_effect": "terminal flow and room air-change distribution",
        "requires_review": True,
    }
    assert by_path[
        "operating_conditions.heat_sources[manual_heat_1].convective_power_w"
    ]["unit"] == "W"
    assert apply_result["terminals"][0]["airflow_cmh"] == 444.0


def test_semantic_diff_ignores_terminal_and_heat_source_array_order():
    """Patch/list order changes alone are not engineering changes."""
    from project_model import scenario_diff

    baseline = _applied_conditions()
    candidate = copy.deepcopy(baseline)
    candidate["terminals"].reverse()
    candidate["heat_sources"].reverse()

    assert scenario_diff(baseline, candidate) == []


def test_semantic_diff_detects_exact_float_change_before_display_rounding():
    """Presentation rounding must not erase a content-identity change."""
    from project_model import scenario_diff

    baseline = _applied_conditions()
    candidate = copy.deepcopy(baseline)
    baseline["terminals"][0]["airflow_cmh"] = 444.00000041
    candidate["terminals"][0]["airflow_cmh"] = 444.00000049

    rows = scenario_diff(baseline, candidate)

    assert len(rows) == 1
    assert rows[0]["baseline"] == rows[0]["candidate"] == 444.0
    assert rows[0]["requires_review"] is True


def test_semantic_diff_does_not_hide_integer_to_float_identity_change():
    """JSON-distinct 444 and 444.0 must remain visible because their hashes differ."""
    from project_model import scenario_diff

    baseline = _applied_conditions()
    candidate = copy.deepcopy(baseline)
    baseline["terminals"][0]["airflow_cmh"] = 444
    candidate["terminals"][0]["airflow_cmh"] = 444.0

    rows = scenario_diff(baseline, candidate)

    assert len(rows) == 1
    assert type(rows[0]["baseline"]) is int
    assert type(rows[0]["candidate"]) is float


def test_semantic_diff_expands_new_weather_object_with_unit_and_rounding():
    """A null-to-object change should show the physical field, not an opaque object."""
    from project_model import scenario_diff

    baseline = _applied_conditions()
    candidate = copy.deepcopy(baseline)
    candidate["weather"] = {
        "outdoor_temperature_k": 303.15000049,
        "authority": "user_confirmed:weather-review",
    }

    rows = scenario_diff(baseline, candidate)
    by_path = {row["path"]: row for row in rows}

    temperature = by_path["operating_conditions.weather.outdoor_temperature_k"]
    assert temperature["baseline"] == "<missing>"
    assert temperature["candidate"] == 303.15
    assert temperature["unit"] == "K"
    assert temperature["engineering_effect"] == "external thermal boundary assumption"


def test_scenario_identity_uses_exact_float_while_diff_rounding_is_presentational(tmp_path):
    """Two display-equal values must still produce distinct immutable Scenario IDs."""
    from project_model import create_design, create_scenario, scenario_diff

    geometry_path = tmp_path / "reviewed.geometry.v2.json"
    geometry_path.write_text(json.dumps(_geometry()), encoding="utf-8")
    design = create_design(
        tmp_path, geometry_path=geometry_path, name="Room", created_by="user:mep-01",
    )
    baseline = _applied_conditions()
    candidate = copy.deepcopy(baseline)
    for row in baseline["terminals"]:
        row["airflow_cmh"] = 444.00000041
    for row in candidate["terminals"]:
        row["airflow_cmh"] = 444.00000049

    first = create_scenario(
        Path(design["path"]), name="A", operating_conditions=baseline,
        purpose="screening",
    )
    second = create_scenario(
        Path(design["path"]), name="B", operating_conditions=candidate,
        purpose="screening",
    )
    rows = scenario_diff(first, second)

    assert first["scenario_id"] != second["scenario_id"]
    assert len(rows) == 2
    assert all(row["baseline"] == row["candidate"] == 444.0 for row in rows)


def _applied_conditions():
    from cfd_templates import apply_hvac_template

    with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
        design = _immutable_design(Path(directory))
        result = apply_hvac_template(_template(), design, user_values=_user_values())
        assert result["ready"] is True
        return result["operating_conditions"]
