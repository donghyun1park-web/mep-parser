"""Fail-closed comparison of immutable CFD Run identities.

The comparison follows references from the identity documents to linked case
directories and re-reads raw result/evidence artifacts.  Caller-authored PASS
flags, maximum values and unlinked legacy cases are never comparison authority.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from jsonschema import Draft202012Validator

import cfd_case_health
import cfd_numerical_sensitivity_job
import project_model


_HERE = Path(__file__).resolve().parent


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _safe(root: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("unsafe artifact reference")
    relative = Path(value)
    if relative.is_absolute() or any(part in {".", ".."} for part in relative.parts):
        raise ValueError("unsafe artifact reference")
    target = (root / relative).resolve()
    target.relative_to(root)
    return target


def _issue(code: str, message: str, run_id: str | None = None) -> dict[str, str]:
    result = {"code": code, "message": message}
    if run_id:
        result["run_id"] = run_id
    return result


def _identity_case(root: Path, identity_path: Path) -> Path | None:
    identity_relative = identity_path.resolve().relative_to(root).as_posix()
    identity_sha = _sha(identity_path)
    links = root / "_project_model" / "legacy_cases"
    for link_path in sorted(links.glob("*/run_identity_link.v1.json")):
        try:
            link = _load(link_path)
            if (link.get("case_identity_path") != identity_relative
                    or link.get("case_identity_sha256") != identity_sha):
                continue
            case = _safe(root, link.get("case_path"))
            if not case.is_dir():
                return None
            if project_model.validate_run_identity(case, projects_root=root):
                return None
            return case
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return None


def _artifact(case: Path, reference: dict, *, code: str) -> Path:
    path = _safe(case, reference.get("path"))
    if not path.is_file() or _sha(path) != reference.get("sha256"):
        raise ValueError(code)
    return path


def _finite_number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and abs(result) != float("inf") else None


def _selector_sha256(value: Any) -> str:
    if not isinstance(value, dict):
        raise ValueError("QOI_SELECTOR_INVALID")
    raw = dict(value)
    supplied = raw.pop("selector_sha256", None)
    try:
        normalised = cfd_numerical_sensitivity_job.normalize_occupied_volume_band(raw)
    except cfd_numerical_sensitivity_job.NumericalSensitivityJobInputError as exc:
        raise ValueError("QOI_SELECTOR_INVALID") from exc
    if supplied is not None and supplied != normalised["selector_sha256"]:
        raise ValueError("QOI_SELECTOR_INVALID")
    return normalised["selector_sha256"]


def _read_case(
    case: Path,
    root: Path,
    run_id: str,
    *,
    expected_selector: Any,
    expected_floor_elevation_m: Any,
) -> tuple[
    dict | None, dict | None, list[dict], dict
]:
    blockers: list[dict] = []
    artifacts: dict[str, dict] = {}
    evidence_path = case / "case_evidence.v1.json"
    if not evidence_path.is_file():
        return None, None, [_issue(
            "CASE_EVIDENCE_MISSING", "case_evidence.v1.json is required", run_id,
        )], artifacts
    try:
        health = cfd_case_health.build_case_health(evidence_path, projects_root=root)
    except Exception as exc:
        return None, None, [_issue(
            "CASE_EVIDENCE_INVALID", f"case evidence validation failed: {exc}", run_id,
        )], artifacts
    if (health.get("status") in {"FAIL", "BLOCKED"}
            or health.get("citation_status") == "CITATION_BLOCKED"):
        blockers.append(_issue(
            "CASE_HEALTH_BLOCKED",
            "authoritative case health does not permit comparison",
            run_id,
        ))
    try:
        manifest_path = case / "result_manifest.json"
        run_path = case / "run_manifest.json"
        manifest = _load(manifest_path)
        run = _load(run_path)
        artifacts.update({
            "case_evidence": {
                "path": evidence_path.relative_to(root).as_posix(),
                "sha256": _sha(evidence_path),
            },
            "result_manifest": {
                "path": manifest_path.relative_to(root).as_posix(),
                "sha256": _sha(manifest_path),
            },
            "run_manifest": {
                "path": run_path.relative_to(root).as_posix(),
                "sha256": _sha(run_path),
            },
        })
        if manifest.get("contract") != "result_manifest.v1":
            raise ValueError("RESULT_MANIFEST_INVALID")
        source = manifest.get("source")
        if not isinstance(source, dict):
            raise ValueError("RESULT_SOURCE_INVALID")
        source_path = _artifact(
            case, source, code="RESULT_SOURCE_HASH_MISMATCH",
        )
        declared_run_sha = manifest.get("run_manifest_sha256")
        if declared_run_sha and declared_run_sha != _sha(run_path):
            raise ValueError("RUN_MANIFEST_HASH_MISMATCH")
        summary_path = _artifact(case, {
            "path": manifest.get("summary_path"),
            "sha256": manifest.get("summary_sha256"),
        }, code="RESULT_SUMMARY_HASH_MISMATCH")
        qoi_ref = manifest.get("occupied_qoi")
        if not isinstance(qoi_ref, dict):
            raise ValueError("QOI_SELECTOR_MISSING")
        qoi_path = _artifact(case, qoi_ref, code="OCCUPIED_QOI_HASH_MISMATCH")
        artifacts["occupied_qoi"] = {
            "path": qoi_path.relative_to(root).as_posix(),
            "sha256": _sha(qoi_path),
        }
        summary, qoi = _load(summary_path), _load(qoi_path)
        selector = qoi.get("selector_sha256")
        thermal_path = case / "thermal_input.json"
        if (not thermal_path.is_file()
                or manifest.get("thermal_input_sha256") != _sha(thermal_path)):
            raise ValueError("THERMAL_INPUT_HASH_MISMATCH")
        thermal_input = _load(thermal_path)
        thermal_settings = thermal_input.get("settings") or {}
        selector_hashes = {
            _selector_sha256(qoi.get("selector")),
            _selector_sha256(thermal_settings.get("occupied_volume_selector")),
            _selector_sha256(expected_selector),
            selector,
            qoi_ref.get("selector_sha256"),
        }
        if len(selector_hashes) != 1:
            raise ValueError("QOI_SELECTOR_MISMATCH")
        thermal_floor = _finite_number(
            thermal_settings.get("occupied_floor_elevation_m")
        )
        scenario_floor = _finite_number(expected_floor_elevation_m)
        qoi_floor = _finite_number(qoi.get("floor_elevation_m"))
        if (thermal_floor is None or scenario_floor is None or qoi_floor is None
                or len({thermal_floor, scenario_floor, qoi_floor}) != 1):
            raise ValueError("QOI_FLOOR_ELEVATION_MISMATCH")
        if (qoi.get("source_vtu_sha256") != source.get("sha256")
                or qoi.get("source_vtu_sha256") != _sha(source_path)):
            raise ValueError("QOI_SOURCE_VTU_MISMATCH")
        if (qoi.get("contract") != "occupied_volume_qoi.v1"
                or qoi.get("scope") != "selected_occupied_volume_band"
                or selector != qoi_ref.get("selector_sha256")
                or not isinstance(selector, str) or len(selector) != 64):
            raise ValueError("QOI_SELECTOR_INVALID")
        temperature = qoi.get("temperature") or {}
        velocity = qoi.get("velocity") or {}
        airflow = run.get("airflow") or {}
        flux = (run.get("numerical_quality") or {}).get("flux_balance") or {}
        if flux.get("available") is True:
            inflow = _finite_number(flux.get("inflow_m3_s"))
            actual_supply = None if inflow is None else inflow * 3600.0
            outflow = _finite_number(flux.get("outflow_m3_s"))
            actual_exhaust = None if outflow is None else outflow * 3600.0
            airflow_basis = flux.get("method") or "solved_boundary_phi"
        else:
            actual_supply = _finite_number(airflow.get("actual_supply_cmh"))
            actual_exhaust = _finite_number(airflow.get("actual_exhaust_cmh"))
            airflow_basis = "declared_actual_flow_unverified"
            blockers.append(_issue(
                "ACTUAL_FLOW_SOLVER_EVIDENCE_MISSING",
                "solver boundary phi is required for actual supply/exhaust flow",
                run_id,
            ))
        energy = (run.get("thermal_progress") or {}).get("energy_balance") or {}
        hottest = (summary.get("temperature") or {}).get("hottest_cell") or {}
        kpis = {
            "temperature": {
                "volume_weighted_mean_k": _finite_number(temperature.get("mean_k")),
                "volume_weighted_p95_k": _finite_number(temperature.get("p95_k")),
            },
            "velocity": {
                "occupied_p95_speed_m_s": _finite_number(
                    velocity.get("p95_speed_m_s")
                ),
            },
            "airflow": {
                "actual_supply_cmh": actual_supply,
                "actual_exhaust_cmh": actual_exhaust,
                "basis": airflow_basis,
            },
            "energy": {
                "transient_closure_ratio": _finite_number(
                    energy.get("transient_closure_ratio")
                ),
            },
            "hotspot": {
                "temperature_k": _finite_number(hottest.get("temperature_k")),
                "centre_m": hottest.get("centre_m")
                if isinstance(hottest.get("centre_m"), list) else None,
            },
            "selector_sha256": selector,
        }
        missing = [
            name for name, value in (
                ("temperature.mean", kpis["temperature"]["volume_weighted_mean_k"]),
                ("temperature.p95", kpis["temperature"]["volume_weighted_p95_k"]),
                ("velocity.occupied_p95", kpis["velocity"]["occupied_p95_speed_m_s"]),
                ("airflow.supply", kpis["airflow"]["actual_supply_cmh"]),
                ("airflow.exhaust", kpis["airflow"]["actual_exhaust_cmh"]),
                ("energy.closure", kpis["energy"]["transient_closure_ratio"]),
                ("hotspot.temperature", kpis["hotspot"]["temperature_k"]),
            ) if value is None
        ]
        if missing:
            blockers.append(_issue(
                "COMPARISON_KPI_INCOMPLETE",
                "missing bounded KPI values: " + ", ".join(missing), run_id,
            ))
        return kpis, health, blockers, artifacts
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        code = str(exc) if str(exc).isupper() else "RUN_RESULT_INVALID"
        return None, health, [_issue(
            code, f"run result validation failed: {exc}", run_id,
        )], artifacts


def _scope(rows: list[dict]) -> dict[str, list[str]]:
    fields = ("citation_status", "purpose", "status")
    common, differences = [], []
    for field in fields:
        values = {str((row.get("case_health") or {}).get(field)) for row in rows}
        (common if len(values) == 1 else differences).append(field)
    selectors = {((row.get("kpis") or {}).get("selector_sha256")) for row in rows}
    (common if len(selectors) == 1 else differences).append("occupied_qoi_selector")
    return {"common": common, "differences": differences}


def compare_runs(
    run_identity_paths: Sequence[Path],
    *,
    projects_root: Path,
) -> dict:
    """Compare two to four current Runs from the same immutable Design revision."""
    root = Path(projects_root).resolve()
    paths = [Path(path).resolve() for path in run_identity_paths]
    blockers: list[dict] = []
    identities: list[tuple[Path, dict, dict]] = []
    if not 2 <= len(paths) <= 4:
        raise ValueError("RUN_COUNT_INVALID: select two to four Run identities")
    if len(set(paths)) != len(paths):
        raise ValueError("RUN_DUPLICATE: Run identities must be unique")
    for path in paths:
        try:
            path.relative_to(root)
            issues = project_model.validate_case_identity(path, projects_root=root)
            if issues:
                raise ValueError(str(issues))
            identity = _load(path)
            scenario_path = _safe(root, identity["scenario"]["path"])
            scenario = _load(scenario_path)
            identities.append((path, identity, scenario))
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise ValueError(f"RUN_IDENTITY_INVALID: {exc}") from exc
    design_revisions = {
        identity["design"]["revision_sha256"] for _, identity, _ in identities
    }
    common_design = next(iter(design_revisions)) if len(design_revisions) == 1 else None
    if common_design is None:
        blockers.append(_issue(
            "DESIGN_REVISION_MISMATCH",
            "Runs must bind the same immutable Design revision",
        ))
    solver_profiles = {identity["solver_profile"] for _, identity, _ in identities}
    if len(solver_profiles) != 1:
        blockers.append(_issue(
            "SOLVER_PROFILE_MISMATCH", "Runs use incompatible solver profiles",
        ))

    rows: list[dict] = []
    baseline_design = identities[0][1]["design"]["revision_sha256"]
    for path, identity, scenario in identities:
        row = {
            "run_id": identity["run_id"],
            "scenario_id": scenario["scenario_id"],
            "scenario_name": scenario["name"],
            "solver_profile": identity["solver_profile"],
            "case_path": None,
            "identity_status": "VALID",
            "case_health": None,
            "kpis": None,
            "artifacts": {
                "run_identity": {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": _sha(path),
                },
                "scenario_revision": {
                    "path": _safe(root, identity["scenario"]["path"])
                    .relative_to(root).as_posix(),
                    "sha256": identity["scenario"]["sha256"],
                },
            },
        }
        if identity["design"]["revision_sha256"] != baseline_design:
            row["identity_status"] = "DESIGN_REVISION_MISMATCH"
            rows.append(row)
            continue
        case = _identity_case(root, path)
        if case is None:
            row["identity_status"] = "RUN_RESULT_NOT_LINKED"
            blockers.append(_issue(
                "RUN_RESULT_NOT_LINKED", "Run identity has no current immutable case link",
                identity["run_id"],
            ))
            rows.append(row)
            continue
        row["case_path"] = case.relative_to(root).as_posix()
        occupied = (scenario.get("operating_conditions") or {}).get(
            "occupied_volume"
        ) or {}
        kpis, health, run_blockers, artifacts = _read_case(
            case, root, identity["run_id"],
            expected_selector=occupied.get("selector"),
            expected_floor_elevation_m=occupied.get("floor_elevation_m"),
        )
        row["kpis"], row["case_health"] = kpis, health
        row["artifacts"].update(artifacts)
        blockers.extend(run_blockers)
        rows.append(row)

    # Every Run must have a selector and all selectors must be identical.
    selector_values = [
        (row.get("kpis") or {}).get("selector_sha256") for row in rows
    ]
    if all(selector_values) and len(set(selector_values)) == 1:
        selector = selector_values[0]
    elif all(selector_values):
        selector = None
        blockers.append(_issue(
            "QOI_SELECTOR_MISMATCH",
            "occupied-volume QoI selectors differ between Runs",
        ))
    else:
        selector = None

    baseline_scenario = identities[0][2]
    diffs = []
    for _, identity, scenario in identities[1:]:
        for item in project_model.scenario_diff(baseline_scenario, scenario):
            diffs.append({"candidate_run_id": identity["run_id"], **item})
    result = {
        "schema_version": 1,
        "contract": "scenario_comparison.v1",
        "created_at": _now(),
        "eligible": not blockers,
        "blockers": blockers,
        "design_revision_sha256": common_design,
        "qoi_selector_sha256": selector,
        "evidence_scope": _scope(rows),
        "scenario_diff": diffs,
        "runs": rows,
    }
    schema = _load(_HERE / "scenario_comparison.v1.schema.json")
    errors = list(Draft202012Validator(schema).iter_errors(result))
    if errors:
        raise ValueError("COMPARISON_CONTRACT_INVALID: " + errors[0].message)
    return result
