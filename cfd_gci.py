"""Mesh-convergence gates for body-fitted thermal OpenFOAM results.

The legacy :mod:`cfd_gridstudy` module works on structured background cell
sizes.  Body-fitted meshes are unstructured, so this module derives the
representative grid width from the actual fluid volume and cell count and
compares only cases with the same geometry, physics and physical time.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import tempfile

import cfd_result_gate
from heat_source_contract import HeatSourceContractError, source_reference_kind


GCI_LIMIT_PCT = 5.0
# Closely spaced grids amplify uncertainty in the GCI denominator and make
# the observed order unreliable. Target nominal spacing ratios near 1.4 and
# require at least 1.25 after the actual unstructured cell counts are known.
MIN_REFINEMENT_RATIO = 1.25
TIME_RELATIVE_TOLERANCE = 0.005
V2_MINIMUM_FLOW_THROUGH_FRACTION = 1.0
V2_WINDOW_FLOW_THROUGH_FRACTION = 0.1
V2_MINIMUM_WINDOW_SNAPSHOTS = 5
V3_MINIMUM_FLOW_THROUGH_FRACTION = 3.0
V3_MAX_WINDOW_DRIFT_PCT = 2.0
_METRICS = (
    ("temperature_max_rise_k", "최고 온도 상승", "K"),
    ("temperature_p95_rise_k", "온도 상승 p95", "K"),
    ("velocity_p95_m_s", "유속 p95", "m/s"),
)
_V2_METRICS = (
    ("temperature_volume_mean_rise_k", "체적가중 평균 온도 상승", "K"),
    ("temperature_volume_p95_rise_k", "체적가중 온도 상승 p95", "K"),
    ("velocity_volume_p95_m_s", "체적가중 유속 p95", "m/s"),
)


class GCIInputError(ValueError):
    """Raised when completed cases cannot form a valid grid study."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _canonical_hash(value):
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _case_provenance(case):
    case = Path(case)
    return {
        "run_manifest_sha256": _file_sha256(case / "run_manifest.json"),
        "result_manifest_sha256": _file_sha256(case / "result_manifest.json"),
        "mesh_manifest_sha256": _file_sha256(case / "mesh_manifest.json"),
        "thermal_input_sha256": _file_sha256(case / "thermal_input.json"),
    }


def _require_current_hash(case, label, path, expected_sha256):
    """Reject a GCI input when an artifact no longer matches its manifest."""
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        raise GCIInputError(f"{case.name}: {label} 해시가 없습니다.")
    try:
        actual = _file_sha256(path)
    except OSError as exc:
        raise GCIInputError(f"{case.name}: {label} 파일을 읽지 못했습니다.") from exc
    if actual != expected_sha256:
        raise GCIInputError(f"{case.name}: {label} 해시가 현재 artifact와 일치하지 않습니다.")


def _atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", delete=False,
            dir=path.parent, prefix="." + path.name + ".", suffix=".tmp") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _physical_settings(settings):
    """Select model inputs while allowing mesh-dependent time-step controls."""
    names = (
        "air_density_kg_m3", "air_specific_heat_j_kg_k",
        "boussinesq_max_temperature_rise_k", "initial_temperature_k",
        "kinematic_viscosity_m2_s", "laminar_prandtl",
        "reference_temperature_k", "supply_temperature_k",
        "thermal_expansion_coefficient_1_k", "thermal_flow_scale",
        "thermal_gravity_scale", "thermal_heat_application",
        "thermal_heat_scale", "turbulence_intensity",
        "turbulence_length_scale_ratio", "turbulent_prandtl",
    )
    return {name: settings.get(name) for name in names if name in settings}


def _terminal_signature(terminals):
    keep = ("name", "role", "airflow_cmh", "flow_rate_m3_s", "area_m2",
            "design_normal")
    rows = [{key: row.get(key) for key in keep if key in row}
            for row in (terminals or [])]
    return sorted(rows, key=lambda row: (str(row.get("role")), str(row.get("name"))))


def _heat_source_contract(thermal):
    """Return the confirmed heat-source identity used for a GCI comparison.

    A mesh-convergence comparison must not silently compare a different
    equipment schedule just because total kW happens to be equal.  Preserve
    the reviewed DXF/source identity alongside the thermal values, while
    normalising list order so equivalent source references hash identically.
    """
    rows = []
    keep = (
        "name", "source_id", "source_label", "source_ref", "power_kw",
        "input_power_w", "convective_fraction", "radiative_fraction",
        "convective_power_w",
        "requested_convective_power_w", "applied_convective_power_w",
        "deferred_convective_power_w", "radiative_power_w",
        "excluded_radiative_power_w",
        "application_scale", "exposed_area_m2", "evidence", "source_type",
        "override_of_dxf", "provenance",
    )
    for source in thermal.get("heat_sources") or []:
        row = {key: source.get(key) for key in keep if key in source}
        source_ids = source.get("source_element_ids") or []
        row["source_element_ids"] = sorted(str(item) for item in source_ids)
        rows.append(row)
    return sorted(rows, key=lambda row: (
        tuple(row.get("source_element_ids") or []),
        str(row.get("name") or ""),
    ))


def _heat_contract_payload(document):
    heat = document.get("heat") if isinstance(document, dict) else None
    if not isinstance(heat, dict):
        heat = {}
    keys = (
        "input_power_w", "requested_convective_power_w",
        "applied_convective_power_w", "deferred_convective_power_w",
        "radiative_power_w", "excluded_radiative_power_w", "source_count", "model",
        "application_scale",
    )
    return {
        "heat": {key: heat.get(key) for key in keys if key in heat},
        "heat_sources": _heat_source_contract(document),
    }


_HEAT_ABSOLUTE_TOLERANCE_W = 1.0e-3
_HEAT_RELATIVE_TOLERANCE = 1.0e-6
_FRACTION_ABSOLUTE_TOLERANCE = 1.0e-6
_FIXTURE_SOURCE_TYPES = frozenset(("fixture", "test_fixture"))


def _heat_values_match(left, right):
    """Compare heat powers while allowing only file-format round-off."""
    return math.isclose(
        float(left), float(right), rel_tol=_HEAT_RELATIVE_TOLERANCE,
        abs_tol=_HEAT_ABSOLUTE_TOLERANCE_W,
    )


def _source_text(source, key):
    """Return a stripped source text field without coercing missing values."""
    value = source.get(key)
    return str(value).strip() if value is not None else ""


def _fractions_match(left, right):
    """Compare dimensionless heat fractions independently of W tolerance."""
    return math.isclose(
        float(left), float(right), rel_tol=0.0,
        abs_tol=_FRACTION_ABSOLUTE_TOLERANCE,
    )


def _validate_source_provenance(source, source_label, source_ids):
    """Fail closed when a production load lost its reviewed drawing record.

    ``fixture`` records remain useful for self-contained unit-test cases, but
    a saved design case must never use a raw DXF detection as a thermal load.
    Every supplied source ID is unique, including fixture IDs, so a comparison
    cannot silently count the same equipment twice.
    """
    source_type = _source_text(source, "source_type")
    if not source_type:
        raise GCIInputError(f"{source_label}: source_type 값이 없습니다.")
    normalized_type = source_type.casefold()
    if normalized_type == "dxf_detected":
        raise GCIInputError(
            f"{source_label}: dxf_detected 원본은 사용자 확인 후에만 열원으로 사용할 수 있습니다."
        )

    override_of_dxf = source.get("override_of_dxf")
    if override_of_dxf is not None and not isinstance(override_of_dxf, bool):
        raise GCIInputError(
            f"{source_label}: override_of_dxf는 boolean 값이어야 합니다."
        )

    source_id = _source_text(source, "source_id")
    if source_id:
        source_identity = source_id.casefold()
        if source_identity in source_ids:
            raise GCIInputError(
                f"{source_label}: duplicate source_id '{source_id}'가 있습니다."
            )
        source_ids.add(source_identity)

    if normalized_type in _FIXTURE_SOURCE_TYPES:
        return

    if normalized_type != "user_confirmed":
        raise GCIInputError(
            f"{source_label}: source_type은 user_confirmed여야 합니다."
        )

    if not source_id:
        raise GCIInputError(f"{source_label}: source_id 값이 없습니다.")
    try:
        source_reference_kind(
            source.get("source_ref"),
            source_id,
            override_of_dxf=override_of_dxf is True,
        )
    except HeatSourceContractError as exc:
        raise GCIInputError(f"{source_label}: {exc}") from exc
    if not _source_text(source, "evidence"):
        raise GCIInputError(f"{source_label}: evidence 값이 없습니다.")
    return


def _heat_contract_number(payload, key, label, *, upper=None):
    """Read one finite, non-negative heat-contract scalar or fail closed."""
    try:
        value = float(payload[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise GCIInputError(f"{label}: {key} 값이 없습니다.") from exc
    if not math.isfinite(value) or value < 0:
        raise GCIInputError(f"{label}: {key}는 유한한 0 이상의 값이어야 합니다.")
    if upper is not None and value > upper:
        raise GCIInputError(f"{label}: {key}는 {upper:g} 이하여야 합니다.")
    return value


def _validate_heat_contract(document, *, label):
    """Validate the source-to-aggregate thermal-load accounting contract.

    A completed body-fitted run is eligible for GCI only if every reported
    heat total can be reconstructed from the reviewed equipment sources.  The
    check is deliberately local and side-effect free: it does not launch a
    solver or re-evaluate a case, so it can safely protect saved-case reuse.
    """
    if not isinstance(document, dict):
        raise GCIInputError(f"{label}: 발열 계약 문서가 객체가 아닙니다.")
    heat = document.get("heat")
    sources = document.get("heat_sources")
    if not isinstance(heat, dict):
        raise GCIInputError(f"{label}: heat 발열 집계가 없습니다.")
    if not isinstance(sources, list) or not sources:
        raise GCIInputError(f"{label}: 검증할 heat_sources가 없습니다.")

    aggregate_label = f"{label} 발열 집계"
    application_scale = _heat_contract_number(
        heat, "application_scale", aggregate_label, upper=1.0
    )
    try:
        source_count = int(heat["source_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise GCIInputError(f"{aggregate_label}: source_count 값이 없습니다.") from exc
    if source_count < 0 or source_count != len(sources):
        raise GCIInputError(
            f"{aggregate_label}: source_count와 heat_sources 개수가 일치하지 않습니다."
        )

    aggregate = {
        key: _heat_contract_number(heat, key, aggregate_label)
        for key in (
            "input_power_w", "requested_convective_power_w",
            "applied_convective_power_w", "deferred_convective_power_w",
            "radiative_power_w", "excluded_radiative_power_w",
        )
    }
    totals = {key: 0.0 for key in aggregate}
    source_ids = set()
    for index, source in enumerate(sources):
        source_label = f"{label} 발열원 {index + 1}"
        if not isinstance(source, dict):
            raise GCIInputError(f"{source_label}: heat source가 객체가 아닙니다.")
        _validate_source_provenance(source, source_label, source_ids)

        # Existing thermal_input.v1 cases express input by power_kw; retain
        # support for an explicit W value so a future import does not silently
        # lose its source-to-total audit trail.
        source_input_w = None
        if "input_power_w" in source:
            source_input_w = _heat_contract_number(
                source, "input_power_w", source_label
            )
        if "power_kw" in source:
            power_kw = _heat_contract_number(source, "power_kw", source_label)
            power_input_w = power_kw * 1000.0
            if (source_input_w is not None
                    and not _heat_values_match(source_input_w, power_input_w)):
                raise GCIInputError(
                    f"{source_label}: input_power_w와 power_kw가 일치하지 않습니다."
                )
            source_input_w = power_input_w
        if source_input_w is None:
            raise GCIInputError(f"{source_label}: input_power_w 또는 power_kw가 없습니다.")

        source_scale = _heat_contract_number(
            source, "application_scale", source_label, upper=1.0
        )
        if not _heat_values_match(source_scale, application_scale):
            raise GCIInputError(
                f"{source_label}: application_scale가 발열 집계와 일치하지 않습니다."
            )
        requested_w = _heat_contract_number(
            source, "requested_convective_power_w", source_label
        )
        convective_w = _heat_contract_number(
            source, "convective_power_w", source_label
        )
        if not _heat_values_match(requested_w, convective_w):
            raise GCIInputError(
                f"{source_label}: 요청 대류발열과 convective_power_w가 일치하지 않습니다."
            )
        fraction = None
        if "convective_fraction" in source:
            fraction = _heat_contract_number(
                source, "convective_fraction", source_label, upper=1.0
            )
            if not _heat_values_match(requested_w, source_input_w * fraction):
                raise GCIInputError(
                    f"{source_label}: 대류분율과 요청 대류발열이 일치하지 않습니다."
                )
        else:
            raise GCIInputError(
                f"{source_label}: convective_fraction 값이 없습니다."
            )
        applied_w = _heat_contract_number(
            source, "applied_convective_power_w", source_label
        )
        deferred_w = _heat_contract_number(
            source, "deferred_convective_power_w", source_label
        )
        excluded_w = _heat_contract_number(
            source, "excluded_radiative_power_w", source_label
        )
        radiative_w = _heat_contract_number(
            source, "radiative_power_w", source_label
        )
        if not _heat_values_match(applied_w, requested_w * source_scale):
            raise GCIInputError(
                f"{source_label}: 적용 대류발열이 요청 대류발열×application_scale와 일치하지 않습니다."
            )
        if not _heat_values_match(deferred_w, requested_w - applied_w):
            raise GCIInputError(
                f"{source_label}: 이연 대류발열이 요청−적용 대류발열과 일치하지 않습니다."
            )
        if not _heat_values_match(excluded_w, source_input_w - requested_w):
            raise GCIInputError(
                f"{source_label}: 제외 복사발열이 입력−요청 대류발열과 일치하지 않습니다."
            )
        if not _heat_values_match(radiative_w, excluded_w):
            raise GCIInputError(
                f"{source_label}: 복사발열과 제외 복사발열이 일치하지 않습니다."
            )
        if "radiative_fraction" not in source:
            raise GCIInputError(
                f"{source_label}: radiative_fraction 값이 없습니다."
            )
        radiative_fraction = _heat_contract_number(
            source, "radiative_fraction", source_label, upper=1.0
        )
        if fraction is not None and not _fractions_match(
                fraction + radiative_fraction, 1.0):
            raise GCIInputError(
                f"{source_label}: convective_fraction과 radiative_fraction의 합은 1이어야 합니다."
            )
        if not _heat_values_match(
                radiative_w, source_input_w * radiative_fraction):
            raise GCIInputError(
                f"{source_label}: radiative_fraction과 radiative_power_w가 일치하지 않습니다."
            )
        totals["input_power_w"] += source_input_w
        totals["requested_convective_power_w"] += requested_w
        totals["applied_convective_power_w"] += applied_w
        totals["deferred_convective_power_w"] += deferred_w
        totals["radiative_power_w"] += radiative_w
        totals["excluded_radiative_power_w"] += excluded_w

    labels = {
        "input_power_w": "입력 발열",
        "requested_convective_power_w": "요청 대류발열",
        "applied_convective_power_w": "적용 대류발열",
        "deferred_convective_power_w": "이연 대류발열",
        "radiative_power_w": "복사발열",
        "excluded_radiative_power_w": "제외 복사발열",
    }
    for key, title in labels.items():
        if not _heat_values_match(aggregate[key], totals[key]):
            raise GCIInputError(
                f"{aggregate_label}: {title} 합계가 heat_sources와 일치하지 않습니다."
            )
    if not _heat_values_match(
            aggregate["input_power_w"],
            aggregate["requested_convective_power_w"]
            + aggregate["excluded_radiative_power_w"],
    ):
        raise GCIInputError(
            f"{aggregate_label}: 입력 발열이 요청 대류발열+제외 복사발열과 일치하지 않습니다."
        )
    if not _heat_values_match(
            aggregate["radiative_power_w"],
            aggregate["excluded_radiative_power_w"],
    ):
        raise GCIInputError(
            f"{aggregate_label}: 복사발열과 제외 복사발열 합계가 일치하지 않습니다."
        )
    if not _heat_values_match(
            aggregate["applied_convective_power_w"],
            aggregate["requested_convective_power_w"] * application_scale,
    ):
        raise GCIInputError(
            f"{aggregate_label}: 적용 대류발열이 요청 대류발열×application_scale와 일치하지 않습니다."
        )
    if not _heat_values_match(
            aggregate["deferred_convective_power_w"],
            aggregate["requested_convective_power_w"]
            - aggregate["applied_convective_power_w"],
    ):
        raise GCIInputError(
            f"{aggregate_label}: 이연 대류발열이 요청−적용 대류발열과 일치하지 않습니다."
        )


def _physics_payload(thermal):
    heat = thermal.get("heat") or {}
    heat_sources = _heat_source_contract(thermal)
    return {
        "airflow": thermal.get("airflow") or {},
        "assumptions": thermal.get("assumptions") or {},
        "condition_matrix": thermal.get("condition_matrix") or {},
        "heat": {key: heat.get(key) for key in (
            "applied_convective_power_w", "radiative_power_w",
            "excluded_radiative_power_w",
            "input_power_w", "model", "requested_convective_power_w",
            "deferred_convective_power_w", "source_count", "application_scale",
        ) if key in heat},
        "heat_sources": heat_sources,
        "settings": _physical_settings(thermal.get("settings") or {}),
        "terminals": _terminal_signature(thermal.get("terminals")),
    }


def _geometry_payload(mesh):
    surface_hash = (mesh.get("input") or {}).get("surface_manifest_sha256")
    if surface_hash:
        return {"surface_manifest_sha256": surface_hash}
    patches = [{key: row.get(key) for key in ("name", "role", "occ_area_m2")}
               for row in (mesh.get("patches") or [])]
    patches.sort(key=lambda row: (str(row.get("role")), str(row.get("name"))))
    return {"occ_volume_m3": mesh.get("occ_volume_m3"), "patches": patches}


def load_body_fitted_case(case_dir):
    """Load and validate one design-ready body-fitted thermal result."""
    case = Path(case_dir).expanduser().resolve()
    try:
        result = _read_json(case / "result_manifest.json")
        summary = _read_json(case / result["summary_path"])
        mesh = _read_json(case / "mesh_manifest.json")
        run = _read_json(case / "run_manifest.json")
        thermal = _read_json(case / "thermal_input.json")
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise GCIInputError(f"{case.name}: 필수 결과 파일을 읽지 못했습니다: {exc}") from exc

    if mesh.get("status") != "PASS":
        raise GCIInputError(f"{case.name}: 메시 품질 gate가 PASS가 아닙니다.")
    if run.get("status") != "PASS" or not run.get("design_ready"):
        raise GCIInputError(f"{case.name}: 설계 검토 가능한 열·부력 PASS 결과가 아닙니다.")
    if run.get("engine") != "body_fitted_buoyant_urans":
        raise GCIInputError(f"{case.name}: body-fitted 열·부력 결과가 아닙니다.")

    numerical_quality = run.get("numerical_quality")
    convection_order = (
        numerical_quality.get("convection_order")
        if isinstance(numerical_quality, dict) else None
    )
    numerical_quality_ok = (
        isinstance(numerical_quality, dict)
        and numerical_quality.get("contract") == "numerical_quality.v1"
        and numerical_quality.get("status") == "PASS"
        and numerical_quality.get("design_ready") is True
        and isinstance(convection_order, (int, float))
        and not isinstance(convection_order, bool)
        and math.isfinite(convection_order)
        and convection_order >= 2
    )
    if not numerical_quality_ok:
        raise GCIInputError(
            f"{case.name}: numerical_quality가 설계 검토 가능(PASS/2차) 상태가 아닙니다."
        )

    numerical_provenance_issues = (
        cfd_result_gate.body_fitted_numerical_provenance_issues(
            case, run, thermal
        )
    )
    if numerical_provenance_issues:
        raise GCIInputError(
            f"{case.name}: 수치 설정 provenance가 유효하지 않습니다: "
            + ", ".join(numerical_provenance_issues)
        )

    run_input = run.get("input") if isinstance(run.get("input"), dict) else {}
    _require_current_hash(
        case, "thermal input→mesh manifest", case / "mesh_manifest.json",
        thermal.get("mesh_manifest_sha256"),
    )
    _require_current_hash(
        case, "run manifest→thermal input", case / "thermal_input.json",
        run_input.get("thermal_input_sha256"),
    )
    _require_current_hash(
        case, "result artifact→run manifest", case / "run_manifest.json",
        result.get("run_manifest_sha256"),
    )
    _require_current_hash(
        case, "result artifact→mesh manifest", case / "mesh_manifest.json",
        result.get("mesh_manifest_sha256"),
    )
    _require_current_hash(
        case, "result artifact→thermal input", case / "thermal_input.json",
        result.get("thermal_input_sha256"),
    )
    if (_canonical_hash(_heat_contract_payload(run))
            != _canonical_hash(_heat_contract_payload(thermal))):
        raise GCIInputError(f"{case.name}: run manifest와 thermal input의 열원 계약이 다릅니다.")
    _validate_heat_contract(thermal, label=f"{case.name}: thermal input")
    _validate_heat_contract(run, label=f"{case.name}: run manifest")

    progress = run.get("thermal_progress") or {}
    minimum_fraction = float(progress.get("minimum_flow_through_fraction") or 0.0)
    flow_fraction = float(progress.get("flow_through_fraction") or 0.0)
    if flow_fraction + 1e-12 < minimum_fraction:
        raise GCIInputError(f"{case.name}: 필요한 유동 교환시간을 확보하지 못했습니다.")
    energy = progress.get("energy_balance") or {}
    if not energy.get("available") or not energy.get("history_complete"):
        raise GCIInputError(f"{case.name}: 완전한 과도 에너지 수지가 없습니다.")

    cells = int(summary.get("cell_count") or (mesh.get("mesh") or {}).get("cells") or 0)
    volume = float(mesh.get("occ_volume_m3") or 0.0)
    time_s = float(summary.get("time_s"))
    if cells <= 0 or volume <= 0 or time_s < 0:
        raise GCIInputError(f"{case.name}: 셀 수, 공기 체적 또는 물리시간이 올바르지 않습니다.")
    temperature = summary.get("temperature") or {}
    velocity = summary.get("velocity") or {}
    reference = float((thermal.get("settings") or {}).get(
        "reference_temperature_k",
        (thermal.get("settings") or {}).get("initial_temperature_k", 0.0),
    ))
    try:
        metrics = {
            "temperature_max_rise_k": float(temperature["maximum"]) - reference,
            "temperature_p95_rise_k": float(temperature["p95"]) - reference,
            "velocity_p95_m_s": float(velocity["p95_speed"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise GCIInputError(f"{case.name}: 필수 온도·유속 지표가 없습니다.") from exc
    if any(not math.isfinite(value) for value in metrics.values()):
        raise GCIInputError(f"{case.name}: GCI 지표는 유한한 값이어야 합니다.")
    if (abs(metrics["temperature_max_rise_k"]) <= 1e-12
            or abs(metrics["temperature_p95_rise_k"]) <= 1e-12
            or metrics["velocity_p95_m_s"] <= 0):
        raise GCIInputError(f"{case.name}: 온도 변화와 p95 유속이 0이 아니어야 합니다.")

    physics = _physics_payload(thermal)
    geometry = _geometry_payload(mesh)
    return {
        "name": case.name,
        "path": str(case),
        "time_s": time_s,
        "cell_count": cells,
        "fluid_volume_m3": volume,
        "effective_grid_width_m": (volume / cells) ** (1.0 / 3.0),
        "reference_temperature_k": reference,
        "geometry_signature": _canonical_hash(geometry),
        "physics_signature": _canonical_hash(physics),
        "heat_source_contract": physics["heat_sources"],
        "metrics": metrics,
        "provenance": _case_provenance(case),
    }


def _weighted_percentile(values, weights, fraction):
    rows = sorted((float(value), float(weight)) for value, weight in zip(values, weights)
                  if math.isfinite(float(value)) and math.isfinite(float(weight))
                  and float(weight) > 0)
    total = sum(weight for _, weight in rows)
    if not rows or total <= 0:
        raise GCIInputError("체적가중 백분위를 계산할 유효 셀이 없습니다.")
    target = float(fraction) * total
    cumulative = 0.0
    for value, weight in rows:
        cumulative += weight
        if cumulative + 1e-15 >= target:
            return value
    return rows[-1][0]


def _numeric_time_dirs(case):
    rows = []
    for path in Path(case).iterdir():
        if not path.is_dir():
            continue
        try:
            value = float(path.name)
        except ValueError:
            continue
        # V is invariant for a fixed finite-volume mesh. OpenFOAM's
        # writeCellVolumes post-process normally writes it only at the latest
        # time, while T/U are available at every requested transient snapshot.
        if value > 0 and all((path / field).is_file() for field in ("T", "U")):
            rows.append((value, path))
    return sorted(rows, key=lambda row: (row[0], str(row[1])))


def _volume_snapshot_metrics(path, reference_temperature_k, volume_path=None):
    # Keep the OpenFOAM ASCII parser in one place; this local import avoids a
    # module cycle during normal Studio startup.
    from cfd_physics import _internal_scalar_values, _internal_vector_values

    temperatures = _internal_scalar_values(Path(path) / "T")
    volumes = _internal_scalar_values(Path(volume_path or (Path(path) / "V")))
    velocity = _internal_vector_values(Path(path) / "U")
    if not temperatures or not volumes or not velocity:
        raise GCIInputError(f"{Path(path).name}: T/U/V 내부장 값을 읽지 못했습니다.")
    if not (len(temperatures) == len(volumes) == len(velocity)):
        raise GCIInputError(f"{Path(path).name}: T/U/V 셀 개수가 서로 다릅니다.")
    if any((not math.isfinite(value) or value <= 0) for value in volumes):
        raise GCIInputError(f"{Path(path).name}: 셀 체적 V가 양의 유한값이 아닙니다.")
    rises = [value - float(reference_temperature_k) for value in temperatures]
    speeds = [math.sqrt(x * x + y * y + z * z) for x, y, z in velocity]
    total_volume = sum(volumes)
    return {
        "temperature_volume_mean_rise_k": sum(
            value * weight for value, weight in zip(rises, volumes)
        ) / total_volume,
        "temperature_volume_p95_rise_k": _weighted_percentile(rises, volumes, 0.95),
        "velocity_volume_p95_m_s": _weighted_percentile(speeds, volumes, 0.95),
        "temperature_max_rise_k": max(rises),
        "fluid_volume_m3": total_volume,
        "cell_count": len(volumes),
    }


def _interpolate_metrics(left, right, time_s):
    span = float(right["time_s"]) - float(left["time_s"])
    if span <= 0:
        raise GCIInputError("시간창 스냅샷 시간이 증가하지 않습니다.")
    ratio = (float(time_s) - float(left["time_s"])) / span
    metrics = {}
    for key in _V2_METRIC_KEYS + ("temperature_max_rise_k",):
        metrics[key] = float(left["metrics"][key]) + ratio * (
            float(right["metrics"][key]) - float(left["metrics"][key])
        )
    return {"time_s": float(time_s), "metrics": metrics, "interpolated": True}


_V2_METRIC_KEYS = tuple(row[0] for row in _V2_METRICS)


def _time_window_average(snapshots, start_time_s, end_time_s):
    snapshots = sorted(snapshots, key=lambda row: row["time_s"])
    if not snapshots or snapshots[0]["time_s"] > start_time_s + 1e-9:
        raise GCIInputError("마지막 안정 시간창의 시작을 덮는 스냅샷이 없습니다.")
    if snapshots[-1]["time_s"] < end_time_s - 1e-9:
        raise GCIInputError("최종 물리시간 스냅샷이 없습니다.")

    previous = max((row for row in snapshots if row["time_s"] <= start_time_s),
                   key=lambda row: row["time_s"])
    points = []
    if abs(previous["time_s"] - start_time_s) <= 1e-9:
        points.append(previous)
    else:
        following = min((row for row in snapshots if row["time_s"] > start_time_s),
                        key=lambda row: row["time_s"])
        points.append(_interpolate_metrics(previous, following, start_time_s))
    points.extend(row for row in snapshots
                  if start_time_s < row["time_s"] < end_time_s)
    final = min((row for row in snapshots if row["time_s"] >= end_time_s),
                key=lambda row: row["time_s"])
    if abs(final["time_s"] - end_time_s) <= 1e-9:
        points.append(final)
    else:
        before = max((row for row in snapshots if row["time_s"] < end_time_s),
                     key=lambda row: row["time_s"])
        points.append(_interpolate_metrics(before, final, end_time_s))

    observed = [row for row in snapshots
                if start_time_s - 1e-9 <= row["time_s"] <= end_time_s + 1e-9]
    if len(observed) < V2_MINIMUM_WINDOW_SNAPSHOTS:
        raise GCIInputError(
            f"마지막 시간창에 실제 스냅샷이 {len(observed)}개뿐입니다. "
            f"최소 {V2_MINIMUM_WINDOW_SNAPSHOTS}개가 필요합니다."
        )
    duration = float(end_time_s) - float(start_time_s)
    if duration <= 0:
        raise GCIInputError("시간평균 구간 길이가 0보다 커야 합니다.")
    integrals = {key: 0.0 for key in _V2_METRIC_KEYS}
    max_integral = 0.0
    for left, right in zip(points, points[1:]):
        delta = right["time_s"] - left["time_s"]
        if delta <= 0:
            continue
        for key in _V2_METRIC_KEYS:
            integrals[key] += 0.5 * delta * (
                left["metrics"][key] + right["metrics"][key]
            )
        max_integral += 0.5 * delta * (
            left["metrics"]["temperature_max_rise_k"]
            + right["metrics"]["temperature_max_rise_k"]
        )
    averaged_metrics = {key: value / duration for key, value in integrals.items()}
    metric_drift_pct = {
        key: abs(points[-1]["metrics"][key] - points[0]["metrics"][key])
        / max(abs(averaged_metrics[key]), 1e-15) * 100.0
        for key in _V2_METRIC_KEYS
    }
    return {
        "metrics": averaged_metrics,
        "diagnostics": {
            "temperature_max_rise_time_average_k": max_integral / duration,
            "temperature_max_rise_window_peak_k": max(
                row["metrics"]["temperature_max_rise_k"] for row in points
            ),
        },
        "snapshot_count": len(observed),
        "integration_point_count": len(points),
        "metric_drift_pct": metric_drift_pct,
    }


def load_time_window_case(
        case_dir, minimum_flow_through_fraction=V2_MINIMUM_FLOW_THROUGH_FRACTION):
    """Load one v2 case using volume-weighted late-window statistics."""
    base = load_body_fitted_case(case_dir)
    case = Path(base["path"])
    run = _read_json(case / "run_manifest.json")
    progress = run.get("thermal_progress") or {}
    flow_fraction = float(progress.get("flow_through_fraction") or 0.0)
    flow_time = float(progress.get("flow_through_time_s") or 0.0)
    minimum_flow_through_fraction = float(minimum_flow_through_fraction)
    if flow_fraction + 1e-12 < minimum_flow_through_fraction:
        raise GCIInputError(
            f"{case.name}: 이 메시 불확실성 계약에는 최소 "
            f"{minimum_flow_through_fraction:.1f} "
            "유동 교환시간이 필요합니다."
        )
    if flow_time <= 0:
        raise GCIInputError(f"{case.name}: 유동 교환시간을 계산할 수 없습니다.")
    # The human-facing body summary rounds time for compact display. Use the
    # run manifest's full-precision latest time so the real final snapshot is
    # not accidentally excluded from the statistical window.
    end_time = float(progress.get("latest_time_s") or base["time_s"])
    base["time_s"] = end_time
    window_duration = flow_time * V2_WINDOW_FLOW_THROUGH_FRACTION
    start_time = end_time - window_duration
    volume_paths = [path / "V" for _, path in _numeric_time_dirs(case)
                    if (path / "V").is_file()]
    if not volume_paths:
        raise GCIInputError(f"{case.name}: 셀 체적 V 필드를 찾을 수 없습니다.")
    # Cell volumes do not change between time directories in this fixed-mesh
    # workflow. Use the newest exported V field for every T/U snapshot.
    shared_volume_path = volume_paths[-1]
    snapshots = []
    for time_s, path in _numeric_time_dirs(case):
        if time_s < start_time - max(window_duration, 1.0):
            continue
        metrics = _volume_snapshot_metrics(
            path, base["reference_temperature_k"], shared_volume_path,
        )
        volume_error = abs(metrics["fluid_volume_m3"] - base["fluid_volume_m3"])
        if volume_error > max(base["fluid_volume_m3"] * 1e-4, 1e-8):
            raise GCIInputError(f"{case.name}: V 필드 체적이 mesh 공기 체적과 일치하지 않습니다.")
        if metrics["cell_count"] != base["cell_count"]:
            raise GCIInputError(f"{case.name}: 시간 스냅샷 셀 수가 mesh 셀 수와 다릅니다.")
        snapshots.append({"time_s": time_s, "metrics": metrics})
    averaged = _time_window_average(snapshots, start_time, end_time)
    base.update(
        metrics=averaged["metrics"], diagnostics=averaged["diagnostics"],
        time_window={
            "start_time_s": start_time, "end_time_s": end_time,
            "duration_s": window_duration,
            "flow_through_fraction": V2_WINDOW_FLOW_THROUGH_FRACTION,
            "snapshot_count": averaged["snapshot_count"],
            "integration_point_count": averaged["integration_point_count"],
            "spatial_aggregation": "cell_volume_weighted",
            "temporal_aggregation": "piecewise_linear_time_average",
            "metric_drift_pct": averaged["metric_drift_pct"],
        },
    )
    return base


def _observed_order(f1, f2, f3, r21, r32):
    e21, e32 = f2 - f1, f3 - f2
    scale = max(abs(f1), abs(f2), abs(f3), 1.0)
    tiny = 1e-12 * scale
    if abs(e21) <= tiny and abs(e32) <= tiny:
        return 0.0, "exact"
    if abs(e21) <= tiny or abs(e32) <= tiny or e21 * e32 <= 0:
        return None, "non_monotonic"
    ratio = abs(e32 / e21)
    p = max(0.01, abs(math.log(ratio) / math.log(r21)))
    for _ in range(100):
        numerator = r21 ** p - 1.0
        denominator = r32 ** p - 1.0
        if numerator <= 0 or denominator <= 0:
            return None, "indeterminate"
        p_new = abs(math.log(ratio) + math.log(numerator / denominator)) / math.log(r21)
        if not math.isfinite(p_new):
            return None, "indeterminate"
        if abs(p_new - p) < 1e-8:
            return p_new, "monotonic"
        p = min(max(p_new, 0.01), 50.0)
    return p, "monotonic"


def calculate_metric_gci(name, label, unit, values, r21, r32, limit_pct=GCI_LIMIT_PCT):
    """Calculate fine-grid GCI for values ordered fine, medium, coarse."""
    f1, f2, f3 = (float(value) for value in values)
    p, convergence = _observed_order(f1, f2, f3, r21, r32)
    payload = {
        "key": name, "label": label, "unit": unit,
        "fine": f1, "medium": f2, "coarse": f3,
        "convergence": convergence, "observed_order": p,
        "extrapolated": None, "gci_fine_pct": None,
        "limit_pct": float(limit_pct), "status": "FAIL",
    }
    if convergence == "exact":
        payload.update(extrapolated=f1, gci_fine_pct=0.0, status="PASS")
        return payload
    if p is None:
        return payload
    denominator = r21 ** p - 1.0
    if denominator <= 0 or abs(f1) <= 1e-15:
        payload["convergence"] = "indeterminate"
        return payload
    payload["extrapolated"] = (r21 ** p * f1 - f2) / denominator
    payload["gci_fine_pct"] = 1.25 * abs((f1 - f2) / f1) / denominator * 100.0
    payload["status"] = "PASS" if payload["gci_fine_pct"] <= limit_pct else "FAIL"
    return payload


def _solve_linear_system(matrix, vector):
    """Solve a small dense linear system with partial pivoting."""
    size = len(vector)
    augmented = [list(map(float, row)) + [float(value)]
                 for row, value in zip(matrix, vector)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) <= 1e-14:
            raise ValueError("singular least-squares system")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        divisor = augmented[column][column]
        augmented[column] = [value / divisor for value in augmented[column]]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            augmented[row] = [
                value - factor * pivot_value
                for value, pivot_value in zip(augmented[row], augmented[column])
            ]
    return [augmented[row][-1] for row in range(size)]


def _least_squares_fit(widths, values, powers, weights,
                       estimated_parameter_count=None):
    """Fit ``phi0 + sum(alpha_j * h**p_j)`` and return paper-style sigma."""
    columns = [[1.0] + [float(width) ** power for power in powers]
               for width in widths]
    parameter_count = len(columns[0])
    sigma_parameter_count = (
        parameter_count if estimated_parameter_count is None
        else int(estimated_parameter_count)
    )
    if len(values) <= sigma_parameter_count:
        raise ValueError("least-squares fit needs redundant grid data")
    matrix = [[sum(
        weight * row[left] * row[right]
        for weight, row in zip(weights, columns)
    ) for right in range(parameter_count)] for left in range(parameter_count)]
    vector = [sum(
        weight * row[column] * value
        for weight, row, value in zip(weights, columns, values)
    ) for column in range(parameter_count)]
    parameters = _solve_linear_system(matrix, vector)
    fitted = [sum(coefficient * term for coefficient, term in zip(parameters, row))
              for row in columns]
    residual_sum = sum(
        weight * (value - estimate) ** 2
        for weight, value, estimate in zip(weights, values, fitted)
    )
    # Eca & Hoekstra normalize both weighted and unweighted sigma with ng.
    sigma = math.sqrt(
        len(values) * max(residual_sum, 0.0)
        / (len(values) - sigma_parameter_count)
    )
    return {
        "parameters": parameters, "fitted": fitted, "sigma": sigma,
        "powers": list(powers),
    }


def _power_fit(widths, values, weights):
    """Find the global one-term least-squares-root fit over useful p values."""
    def trial(order):
        try:
            # Although p is fixed during each linear sub-fit, it is estimated
            # from the same data.  Eca-Hoekstra Appendix B therefore uses
            # ng - 3 for sigma_RE (phi0, alpha and p), not ng - 2.
            fit = _least_squares_fit(
                widths, values, [order], weights,
                estimated_parameter_count=3,
            )
            return fit["sigma"], fit
        except (OverflowError, ValueError, ZeroDivisionError):
            return math.inf, None

    # The formal scheme is second order. A broad signed interval is retained
    # so anomalous data can be identified without forcing a positive order.
    samples = [(-10.0 + index * 0.025) for index in range(399)]
    samples += [(0.05 + index * 0.025) for index in range(399)]
    scored = [(trial(order)[0], order) for order in samples]
    _, best_order = min(scored, key=lambda row: row[0])
    step = 0.025
    left, right = best_order - step, best_order + step
    if left < 0 < right:
        left = 0.05 if best_order > 0 else -0.10
        right = 0.10 if best_order > 0 else -0.05
    ratio = (math.sqrt(5.0) - 1.0) / 2.0
    x1 = right - ratio * (right - left)
    x2 = left + ratio * (right - left)
    y1, y2 = trial(x1)[0], trial(x2)[0]
    for _ in range(80):
        if abs(right - left) <= 1e-8:
            break
        if y1 <= y2:
            right, x2, y2 = x2, x1, y1
            x1 = right - ratio * (right - left)
            y1 = trial(x1)[0]
        else:
            left, x1, y1 = x1, x2, y2
            x2 = left + ratio * (right - left)
            y2 = trial(x2)[0]
    order = 0.5 * (left + right)
    _, fit = trial(order)
    if fit is None:
        raise ValueError("observed-order fit failed")
    fit["observed_order"] = order
    return fit


def _fit_candidates(widths, values):
    count = len(values)
    weight_sets = {
        "unweighted": [1.0 / count] * count,
        "inverse_grid_width": [
            (1.0 / width) / sum(1.0 / item for item in widths)
            for width in widths
        ],
    }
    root_fits = []
    fixed_fits = []
    for weighting, weights in weight_sets.items():
        root = _power_fit(widths, values, weights)
        root.update(weighting=weighting, estimator="richardson")
        root_fits.append(root)
        for powers, estimator in (([1.0], "first_order"),
                                  ([2.0], "second_order"),
                                  ([1.0, 2.0], "first_second_order")):
            fit = _least_squares_fit(widths, values, powers, weights)
            fit.update(weighting=weighting, estimator=estimator)
            fixed_fits.append(fit)
    positive = [fit for fit in root_fits if fit["observed_order"] > 0]
    # The reference implementation gives priority to a Richardson fit in the
    # accepted second-order range.  Comparing every positive root by sigma
    # first can select a p > 2 fit and wrongly discard another valid root.
    accepted = [
        fit for fit in positive
        if 0.5 <= fit["observed_order"] <= 2.0
    ]
    root = min(accepted or positive or root_fits,
               key=lambda fit: fit["sigma"])
    anomalous = not positive
    order = None if anomalous else root["observed_order"]
    if anomalous or order < 0.5:
        eligible = fixed_fits
    elif order > 2.0:
        eligible = [fit for fit in fixed_fits
                    if fit["estimator"] in ("first_order", "second_order")]
    else:
        eligible = [root]
    return min(eligible, key=lambda fit: fit["sigma"]), order, anomalous


def calculate_metric_lsr(name, label, unit, values, widths,
                         limit_pct=GCI_LIMIT_PCT):
    """Estimate fine-grid uncertainty using Eca-Hoekstra 2014 LSR."""
    values = [float(value) for value in values]
    raw_widths = [float(width) for width in widths]
    if len(values) < 4 or len(values) != len(raw_widths):
        raise ValueError("Eca-Hoekstra LSR requires at least four grids")
    normalized = [width / raw_widths[0] for width in raw_widths]
    fine = values[0]
    payload = {
        "key": name, "label": label, "unit": unit,
        "fine": fine, "medium": values[1], "coarse": values[2],
        "grid_values": values, "grid_width_ratios": normalized,
        "convergence": "exact", "observed_order": None,
        "extrapolated": fine, "gci_fine_pct": None,
        "uncertainty_method": "eca_hoekstra_lsr_2014",
        "uncertainty_fine": 0.0, "uncertainty_fine_pct": 0.0,
        "error_estimator": "exact", "fit_weighting": "unweighted",
        "fit_standard_deviation": 0.0, "data_range_parameter": 0.0,
        "safety_factor": 1.25, "fit_value_fine": fine,
        "limit_pct": float(limit_pct), "status": "PASS",
    }
    scale = max(max(abs(value) for value in values), 1.0)
    if max(values) - min(values) <= 1e-12 * scale:
        return payload

    fit, order, anomalous = _fit_candidates(normalized, values)
    parameters = fit["parameters"]
    fine_fit = sum(parameters)
    error_estimate = abs(fine_fit - parameters[0])
    sigma = float(fit["sigma"])
    data_range = (max(values) - min(values)) / (len(values) - 1)
    reliable = (not anomalous and 0.5 <= order < 2.1 and sigma < data_range)
    safety_factor = 1.25 if reliable else 3.0
    components = error_estimate + sigma + abs(fine - fine_fit)
    if sigma < data_range:
        uncertainty = safety_factor * error_estimate + sigma + abs(fine - fine_fit)
    else:
        uncertainty = 3.0 * sigma / data_range * components
    uncertainty_pct = math.inf if abs(fine) <= 1e-15 else uncertainty / abs(fine) * 100
    payload.update(
        convergence="anomalous" if anomalous else "least_squares_monotonic",
        observed_order=order,
        extrapolated=parameters[0],
        uncertainty_fine=uncertainty,
        uncertainty_fine_pct=uncertainty_pct,
        error_estimator=fit["estimator"],
        fit_weighting=fit["weighting"],
        fit_standard_deviation=sigma,
        data_range_parameter=data_range,
        safety_factor=safety_factor,
        fit_value_fine=fine_fit,
        status="PASS" if uncertainty_pct <= limit_pct else "FAIL",
    )
    return payload


def build_grid_convergence(case_dirs, out_path=None, limit_pct=GCI_LIMIT_PCT,
                           contract="grid_convergence.v1"):
    """Build a ``grid_convergence.v1`` manifest from exactly three cases."""
    if contract == "grid_convergence.v3":
        return build_grid_convergence_v3(case_dirs, out_path, limit_pct)
    if contract == "grid_convergence.v2":
        return build_grid_convergence_v2(case_dirs, out_path, limit_pct)
    if contract != "grid_convergence.v1":
        return {"ok": False, "error": f"지원하지 않는 GCI 계약입니다: {contract}"}
    if not isinstance(case_dirs, (list, tuple)) or len(case_dirs) != 3:
        return {"ok": False, "error": "서로 다른 열·부력 결과 3개가 필요합니다."}
    try:
        cases = [load_body_fitted_case(path) for path in case_dirs]
        if len({item["path"] for item in cases}) != 3:
            raise GCIInputError("동일한 결과를 중복 선택할 수 없습니다.")
        if len({item["geometry_signature"] for item in cases}) != 1:
            raise GCIInputError("세 결과의 CAD/공기영역 형상이 서로 다릅니다.")
        if len({item["physics_signature"] for item in cases}) != 1:
            raise GCIInputError("세 결과의 유량·열원·부력 조건이 서로 다릅니다.")
        times = [item["time_s"] for item in cases]
        tolerance = max(max(times), 1.0) * TIME_RELATIVE_TOLERANCE
        if max(times) - min(times) > tolerance:
            raise GCIInputError("세 결과의 누적 물리시간이 같지 않습니다.")
        cases.sort(key=lambda item: item["effective_grid_width_m"])
        if len({item["cell_count"] for item in cases}) != 3:
            raise GCIInputError("셀 수가 서로 다른 3수준 메시가 필요합니다.")
        r21 = cases[1]["effective_grid_width_m"] / cases[0]["effective_grid_width_m"]
        r32 = cases[2]["effective_grid_width_m"] / cases[1]["effective_grid_width_m"]
        if min(r21, r32) < MIN_REFINEMENT_RATIO:
            raise GCIInputError(
                f"메시 간 유효 세분비는 {MIN_REFINEMENT_RATIO:.2f} 이상이어야 합니다."
            )
    except (GCIInputError, OSError, ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}

    metrics = [calculate_metric_gci(
        key, label, unit, [case["metrics"][key] for case in cases],
        r21, r32, limit_pct,
    ) for key, label, unit in _METRICS]
    failed = [item["key"] for item in metrics if item["status"] != "PASS"]
    warnings = []
    for item in metrics:
        if item["observed_order"] is not None and item["observed_order"] > 10:
            warnings.append(f"HIGH_OBSERVED_ORDER:{item['key']}")
    manifest = {
        "schema_version": 1,
        "contract": "grid_convergence.v1",
        "engine": "body_fitted_thermal_gci",
        "created_at": _now(),
        "status": "PASS" if not failed else "FAIL",
        "design_ready": not failed,
        "gci_limit_pct": float(limit_pct),
        "errors": [f"GCI_GATE_FAILED:{key}" for key in failed],
        "warnings": warnings,
        "comparison": {
            "physical_time_s": sum(item["time_s"] for item in cases) / 3.0,
            "time_tolerance_fraction": TIME_RELATIVE_TOLERANCE,
            "geometry_signature": cases[0]["geometry_signature"],
            "physics_signature": cases[0]["physics_signature"],
            "heat_source_contract": cases[0]["heat_source_contract"],
            "refinement_ratio_medium_to_fine": r21,
            "refinement_ratio_coarse_to_medium": r32,
            "minimum_refinement_ratio": MIN_REFINEMENT_RATIO,
            "grid_width_definition": "(fluid_volume_m3/cell_count)^(1/3)",
            "temperature_definition": "summary temperature minus reference_temperature_k",
        },
        "cases": [{key: item[key] for key in (
            "name", "path", "time_s", "cell_count", "fluid_volume_m3",
            "effective_grid_width_m", "reference_temperature_k", "metrics",
            "provenance",
        )} for item in cases],
        "metrics": metrics,
    }
    if out_path is not None:
        _atomic_json(out_path, manifest)
    return {"ok": True, "manifest": manifest,
            "manifest_path": str(Path(out_path).resolve()) if out_path else None}


def build_grid_convergence_v2(case_dirs, out_path=None, limit_pct=GCI_LIMIT_PCT):
    """Build the volume- and time-weighted ``grid_convergence.v2`` gate."""
    if not isinstance(case_dirs, (list, tuple)) or len(case_dirs) != 3:
        return {"ok": False, "error": "서로 다른 열·부력 결과 3개가 필요합니다."}
    try:
        cases = [load_time_window_case(path) for path in case_dirs]
        if len({item["path"] for item in cases}) != 3:
            raise GCIInputError("동일한 결과를 중복 선택할 수 없습니다.")
        if len({item["geometry_signature"] for item in cases}) != 1:
            raise GCIInputError("세 결과의 CAD/공기영역 형상이 서로 다릅니다.")
        if len({item["physics_signature"] for item in cases}) != 1:
            raise GCIInputError("세 결과의 유량·열원·부력 조건이 서로 다릅니다.")
        times = [item["time_s"] for item in cases]
        tolerance = max(max(times), 1.0) * TIME_RELATIVE_TOLERANCE
        if max(times) - min(times) > tolerance:
            raise GCIInputError("세 결과의 누적 물리시간이 같지 않습니다.")
        window_durations = [item["time_window"]["duration_s"] for item in cases]
        window_tolerance = max(max(window_durations), 1.0) * TIME_RELATIVE_TOLERANCE
        if max(window_durations) - min(window_durations) > window_tolerance:
            raise GCIInputError("세 결과의 시간평균 창 길이가 같지 않습니다.")
        cases.sort(key=lambda item: item["effective_grid_width_m"])
        if len({item["cell_count"] for item in cases}) != 3:
            raise GCIInputError("셀 수가 서로 다른 3수준 메시가 필요합니다.")
        r21 = cases[1]["effective_grid_width_m"] / cases[0]["effective_grid_width_m"]
        r32 = cases[2]["effective_grid_width_m"] / cases[1]["effective_grid_width_m"]
        if min(r21, r32) < MIN_REFINEMENT_RATIO:
            raise GCIInputError(
                f"메시 간 유효 세분비는 {MIN_REFINEMENT_RATIO:.2f} 이상이어야 합니다."
            )
    except (GCIInputError, OSError, ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}

    metrics = [calculate_metric_gci(
        key, label, unit, [case["metrics"][key] for case in cases],
        r21, r32, limit_pct,
    ) for key, label, unit in _V2_METRICS]
    failed = [item["key"] for item in metrics if item["status"] != "PASS"]
    warnings = []
    for item in metrics:
        if item["observed_order"] is not None and item["observed_order"] > 10:
            warnings.append(f"HIGH_OBSERVED_ORDER:{item['key']}")
    manifest = {
        "schema_version": 2,
        "contract": "grid_convergence.v2",
        "engine": "body_fitted_thermal_gci_time_window",
        "created_at": _now(),
        "status": "PASS" if not failed else "FAIL",
        "design_ready": not failed,
        "gci_limit_pct": float(limit_pct),
        "errors": [f"GCI_GATE_FAILED:{key}" for key in failed],
        "warnings": warnings,
        "comparison": {
            "physical_time_s": sum(item["time_s"] for item in cases) / 3.0,
            "minimum_flow_through_fraction": V2_MINIMUM_FLOW_THROUGH_FRACTION,
            "window_flow_through_fraction": V2_WINDOW_FLOW_THROUGH_FRACTION,
            "window_duration_s": sum(window_durations) / 3.0,
            "time_tolerance_fraction": TIME_RELATIVE_TOLERANCE,
            "geometry_signature": cases[0]["geometry_signature"],
            "physics_signature": cases[0]["physics_signature"],
            "heat_source_contract": cases[0]["heat_source_contract"],
            "refinement_ratio_medium_to_fine": r21,
            "refinement_ratio_coarse_to_medium": r32,
            "minimum_refinement_ratio": MIN_REFINEMENT_RATIO,
            "grid_width_definition": "(fluid_volume_m3/cell_count)^(1/3)",
            "spatial_aggregation": "cell_volume_weighted",
            "temporal_aggregation": "last_window_piecewise_linear_time_average",
            "temperature_definition": "T minus reference_temperature_k",
            "maximum_temperature_usage": "diagnostic_only_not_a_gate",
        },
        "cases": [{key: item[key] for key in (
            "name", "path", "time_s", "cell_count", "fluid_volume_m3",
            "effective_grid_width_m", "reference_temperature_k", "metrics",
            "diagnostics", "time_window", "provenance",
        )} for item in cases],
        "metrics": metrics,
    }
    if out_path is not None:
        _atomic_json(out_path, manifest)
    return {"ok": True, "manifest": manifest,
            "manifest_path": str(Path(out_path).resolve()) if out_path else None}


def build_grid_convergence_v3(case_dirs, out_path=None, limit_pct=GCI_LIMIT_PCT):
    """Build a 4+ grid Eca-Hoekstra LSR mesh-uncertainty gate."""
    if not isinstance(case_dirs, (list, tuple)) or len(case_dirs) < 4:
        return {"ok": False, "error": "서로 다른 열·부력 결과가 최소 4개 필요합니다."}
    try:
        cases = [load_time_window_case(
            path, minimum_flow_through_fraction=V3_MINIMUM_FLOW_THROUGH_FRACTION,
        ) for path in case_dirs]
        count = len(cases)
        if len({item["path"] for item in cases}) != count:
            raise GCIInputError("동일한 결과를 중복 선택할 수 없습니다.")
        if len({item["geometry_signature"] for item in cases}) != 1:
            raise GCIInputError("모든 결과의 CAD/공기영역 형상이 서로 같아야 합니다.")
        if len({item["physics_signature"] for item in cases}) != 1:
            raise GCIInputError("모든 결과의 유량·열원·부력 조건이 서로 같아야 합니다.")
        times = [item["time_s"] for item in cases]
        tolerance = max(max(times), 1.0) * TIME_RELATIVE_TOLERANCE
        if max(times) - min(times) > tolerance:
            raise GCIInputError("모든 결과의 누적 물리시간이 같아야 합니다.")
        window_durations = [item["time_window"]["duration_s"] for item in cases]
        window_tolerance = max(max(window_durations), 1.0) * TIME_RELATIVE_TOLERANCE
        if max(window_durations) - min(window_durations) > window_tolerance:
            raise GCIInputError("모든 결과의 시간평균 창 길이가 같아야 합니다.")
        cases.sort(key=lambda item: item["effective_grid_width_m"])
        if len({item["cell_count"] for item in cases}) != count:
            raise GCIInputError("셀 수가 서로 다른 최소 4수준 메시가 필요합니다.")
        widths = [item["effective_grid_width_m"] for item in cases]
        refinement_ratios = [right / left for left, right in zip(widths, widths[1:])]
        if min(refinement_ratios) < MIN_REFINEMENT_RATIO:
            raise GCIInputError(
                f"메시 간 유효 세분비는 {MIN_REFINEMENT_RATIO:.2f} 이상이어야 합니다."
            )
    except (GCIInputError, OSError, ValueError, TypeError) as exc:
        return {"ok": False, "error": str(exc)}

    metrics = [calculate_metric_lsr(
        key, label, unit, [case["metrics"][key] for case in cases],
        widths, limit_pct,
    ) for key, label, unit in _V2_METRICS]
    for metric in metrics:
        drift = max(
            float(case["time_window"]["metric_drift_pct"][metric["key"]])
            for case in cases
        )
        uncertainty_status = metric["status"]
        stationarity_status = (
            "PASS" if drift <= V3_MAX_WINDOW_DRIFT_PCT else "FAIL"
        )
        metric.update(
            uncertainty_status=uncertainty_status,
            window_drift_pct=drift,
            stationarity_limit_pct=V3_MAX_WINDOW_DRIFT_PCT,
            stationarity_status=stationarity_status,
            status=("PASS" if uncertainty_status == "PASS"
                    and stationarity_status == "PASS" else "FAIL"),
        )
    failed = [item["key"] for item in metrics if item["status"] != "PASS"]
    uncertainty_failed = [item["key"] for item in metrics
                          if item["uncertainty_status"] != "PASS"]
    stationarity_failed = [item["key"] for item in metrics
                           if item["stationarity_status"] != "PASS"]
    warnings = []
    for item in metrics:
        if item["fit_standard_deviation"] >= item["data_range_parameter"] > 0:
            warnings.append(f"HIGH_FIT_SCATTER:{item['key']}")
        if item["convergence"] == "anomalous":
            warnings.append(f"ANOMALOUS_GRID_BEHAVIOUR:{item['key']}")
    manifest = {
        "schema_version": 3,
        "contract": "grid_convergence.v3",
        "engine": "body_fitted_thermal_mesh_uncertainty_lsr",
        "created_at": _now(),
        "status": "PASS" if not failed else "FAIL",
        "design_ready": not failed,
        "uncertainty_limit_pct": float(limit_pct),
        "gci_limit_pct": float(limit_pct),
        "errors": (
            [f"MESH_UNCERTAINTY_GATE_FAILED:{key}" for key in uncertainty_failed]
            + [f"TEMPORAL_STATIONARITY_GATE_FAILED:{key}"
               for key in stationarity_failed]
        ),
        "warnings": warnings,
        "comparison": {
            "method": "Eca-Hoekstra 2014 least-squares-root",
            "method_doi": "10.1016/j.jcp.2014.01.006",
            "grid_count": len(cases),
            "physical_time_s": sum(item["time_s"] for item in cases) / len(cases),
            "minimum_flow_through_fraction": V3_MINIMUM_FLOW_THROUGH_FRACTION,
            "maximum_window_drift_pct": V3_MAX_WINDOW_DRIFT_PCT,
            "window_flow_through_fraction": V2_WINDOW_FLOW_THROUGH_FRACTION,
            "window_duration_s": sum(window_durations) / len(cases),
            "time_tolerance_fraction": TIME_RELATIVE_TOLERANCE,
            "geometry_signature": cases[0]["geometry_signature"],
            "physics_signature": cases[0]["physics_signature"],
            "heat_source_contract": cases[0]["heat_source_contract"],
            "refinement_ratios_fine_to_coarse": refinement_ratios,
            "minimum_refinement_ratio": MIN_REFINEMENT_RATIO,
            "grid_width_definition": "(fluid_volume_m3/cell_count)^(1/3)",
            "spatial_aggregation": "cell_volume_weighted",
            "temporal_aggregation": "last_window_piecewise_linear_time_average",
            "temperature_definition": "T minus reference_temperature_k",
            "maximum_temperature_usage": "diagnostic_only_not_a_gate",
        },
        "cases": [{key: item[key] for key in (
            "name", "path", "time_s", "cell_count", "fluid_volume_m3",
            "effective_grid_width_m", "reference_temperature_k", "metrics",
            "diagnostics", "time_window", "provenance",
        )} for item in cases],
        "metrics": metrics,
    }
    if out_path is not None:
        _atomic_json(out_path, manifest)
    return {"ok": True, "manifest": manifest,
            "manifest_path": str(Path(out_path).resolve()) if out_path else None}
