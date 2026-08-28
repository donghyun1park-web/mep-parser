"""Shared trust contracts for screening and body-fitted CFD results.

The legacy structured-grid solver can produce useful screening evidence, but
it must never be promoted to a body-fitted design result merely because its
residuals and energy balance look good.  This module keeps that distinction
explicit and checks body-fitted result provenance before exposing a
design-citable status.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import cfd_convergence_spec
import cfd_numerics
import cfd_radiation
import cfd_validation_anchor


CONTRACT = "result_trust.v1"
THERMAL_NUMERICS_PROVENANCE_CONTRACT = "thermal_numerics_provenance.v1"
THERMAL_NUMERICS_SYSTEM_FILES = {
    "controlDict": Path("system") / "controlDict",
    "fvSchemes": Path("system") / "fvSchemes",
    "fvSolution": Path("system") / "fvSolution",
}
# 단일 정의는 cfd_convergence_spec 을 참고 — 여기서는 재정의하지 않고 그대로 가져온다.
RESIDUAL_LIMITS = cfd_convergence_spec.SCREENING_TRUST_RESIDUAL_LIMITS
CLOSURE_OK = (90.0, 110.0)
CLOSURE_HARD = (75.0, 125.0)
LEGACY_SCREENING_RESIDUALS = ("Ux", "Uy", "Uz", "p_rgh", "T", "k", "epsilon")
LEGACY_SCREENING_FIELD_METRICS = ("T_avg_C", "T_max_C", "U_max")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _canonical_json_sha256(value):
    try:
        payload = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(payload).hexdigest()


def _finite_at_least(value, threshold):
    try:
        return math.isfinite(float(value)) and float(value) >= threshold
    except (TypeError, ValueError):
        return False


def _payload(*, status, run_status, convergence_status, design_ready,
             citation_status, citable, blockers=None, reasons=None, evidence=None):
    return {
        "contract": CONTRACT,
        "status": status,
        "run_status": run_status,
        "convergence_status": convergence_status,
        "design_ready": bool(design_ready),
        "citation_status": citation_status,
        "citable": bool(citable),
        "blockers": list(blockers or []),
        "reasons": list(reasons or []),
        "evidence": dict(evidence or {}),
    }


def _opening_metric_evidence(opening_preflight, opening_verification=None):
    """Keep local jet credibility distinct from thermal screening trust."""
    preflight = opening_preflight if isinstance(opening_preflight, dict) else {}
    verification = (opening_verification if isinstance(opening_verification, dict)
                    and opening_verification.get("contract") == "opening_boundary_verification.v1"
                    else None)
    if preflight.get("contract") != "opening_preflight.v2":
        return {
            "preflight_status": "NOT_AVAILABLE",
            "opening_resolution_ok": None,
            "jet_metrics_citable": None,
            "boundary_verification_status": (
                verification.get("status") if verification else "NOT_AVAILABLE"
            ),
        }
    return {
        "preflight_status": "AVAILABLE",
        "opening_resolution_ok": bool(preflight.get("opening_resolution_ok")),
        "jet_metrics_citable": bool(preflight.get("jet_metrics_citable")),
        "warnings": list(preflight.get("warnings") or []),
        "result_required": list(preflight.get("result_required") or []),
        "boundary_verification_status": (
            verification.get("status") if verification else "NOT_AVAILABLE"
        ),
    }


def evaluate_screening_result(parsed, metrics, *, model_quality=None,
                              energy_required=False, opening_preflight=None,
                              opening_verification=None):
    """Return a ``result_trust.v1`` assessment for a legacy screening run."""
    parsed = parsed or {}
    metrics_available = metrics is not None
    metrics = metrics or {}
    blockers = ["screening_engine"]
    reasons = []
    opening_evidence = _opening_metric_evidence(opening_preflight, opening_verification)

    if parsed.get("crashed"):
        return _payload(
            status="FAIL", run_status="FAIL", convergence_status="FAIL",
            design_ready=False, citation_status="NOT_EVALUATED", citable=False,
            blockers=blockers + ["solver_crash"],
            reasons=["솔버가 발산 또는 크래시로 중단되어 결과를 평가할 수 없습니다."],
            evidence={"engine": "legacy_screening"},
        )

    def finite_metric(name):
        try:
            return math.isfinite(float(metrics.get(name)))
        except (TypeError, ValueError):
            return False

    required_metrics = list(LEGACY_SCREENING_FIELD_METRICS)
    heat_case = bool(energy_required) or metrics.get("heat_kw") is not None or metrics.get("closure_pct") is not None
    if heat_case:
        required_metrics.extend(("closure_pct", "mass_err_pct"))
    missing_metrics = [name for name in required_metrics if not finite_metric(name)]
    if not metrics_available or missing_metrics:
        return _payload(
            status="NOT_EVALUATED", run_status="PASS",
            convergence_status="NOT_EVALUATED", design_ready=False,
            citation_status="NOT_EVALUATED", citable=False,
            blockers=blockers + ["field_metrics"],
            reasons=["최종 T/U 또는 열수지 필수 지표를 읽지 못해 결과를 평가할 수 없습니다."],
            evidence={"engine": "legacy_screening", "missing_metrics": missing_metrics},
        )

    missing_residuals = []
    for field in LEGACY_SCREENING_RESIDUALS:
        values = [value for value in (parsed.get("residuals", {}).get(field) or [])
                  if value is not None]
        if not values:
            missing_residuals.append(field)
    if missing_residuals:
        return _payload(
            status="NOT_EVALUATED", run_status="PASS",
            convergence_status="NOT_EVALUATED", design_ready=False,
            citation_status="NOT_EVALUATED", citable=False,
            blockers=blockers + ["residuals"],
            reasons=["필수 난류·온도 잔차가 없어 수렴 여부를 평가할 수 없습니다."],
            evidence={"engine": "legacy_screening", "missing_residuals": missing_residuals},
        )

    continuity = parsed.get("continuity_global") or []
    continuity_ok = bool(continuity) and abs(continuity[-1][1]) < 1e-3
    residual_checks = []
    for field in LEGACY_SCREENING_RESIDUALS:
        limit = RESIDUAL_LIMITS[field]
        values = [value for value in (parsed.get("residuals", {}).get(field) or [])
                  if value is not None]
        residual_checks.append(values[-1] <= limit)
    residual_ok = all(residual_checks)

    if not continuity_ok:
        blockers.append("continuity")
        reasons.append("연속방정식 누적오차가 수렴 기준을 충족하지 못했습니다.")
    if not residual_ok:
        blockers.append("residuals")
        reasons.append("주요 잔차가 수렴 기준을 충족하지 못했습니다.")

    closure = metrics.get("closure_pct")
    closure_ok = True
    if closure is not None:
        oscillation = metrics.get("closure_osc") or 0.0
        mass_error = metrics.get("mass_err_pct")
        mass_ok = mass_error is None or abs(mass_error) <= 5.0
        closure_ok = CLOSURE_OK[0] <= closure <= CLOSURE_OK[1]
        hard_fail = not (CLOSURE_HARD[0] <= closure <= CLOSURE_HARD[1])
        if hard_fail:
            return _payload(
                status="FAIL", run_status="PASS", convergence_status="FAIL",
                design_ready=False, citation_status="NOT_EVALUATED", citable=False,
                blockers=blockers + ["energy_closure"],
                reasons=[
                    f"에너지 폐합율 {closure:.0f}%가 물리적 하드 한계 "
                    f"({CLOSURE_HARD[0]:.0f}~{CLOSURE_HARD[1]:.0f}%)를 벗어났습니다."
                ],
                evidence={"engine": "legacy_screening", "closure_pct": closure},
            )
        if not closure_ok:
            blockers.append("energy_closure")
            reasons.append(
                f"에너지 폐합율 {closure:.0f}%가 정상상태 허용범위 "
                f"({CLOSURE_OK[0]:.0f}~{CLOSURE_OK[1]:.0f}%)를 벗어났습니다."
            )
        if not mass_ok:
            blockers.append("mass_balance")
            reasons.append(f"급배기 질량수지 오차 {mass_error:.1f}%를 확인해야 합니다.")
        if oscillation > 10.0:
            blockers.append("energy_oscillation")
            reasons.append(f"에너지 폐합율 변동폭 ±{oscillation:.0f}%가 큽니다.")
        closure_ok = closure_ok and mass_ok and oscillation <= 10.0

    quality = model_quality or {}
    if quality.get("design_ready") is False:
        blockers.append("model_quality")
        reasons.append("구조격자/다공성 모델은 설계 검토용 형상 검증을 통과하지 않았습니다.")

    converged = continuity_ok and residual_ok and closure_ok
    if converged:
        return _payload(
            status="PASS", run_status="PASS", convergence_status="PASS",
            design_ready=False, citation_status="SCREENING_ONLY", citable=True,
            blockers=blockers, reasons=reasons,
            evidence={"engine": "legacy_screening", "closure_pct": closure,
                      "opening": opening_evidence},
        )
    return _payload(
        status="WARN", run_status="PASS", convergence_status="WARN",
        design_ready=False, citation_status="NOT_EVALUATED", citable=False,
        blockers=blockers, reasons=reasons,
        evidence={"engine": "legacy_screening", "closure_pct": closure,
                  "opening": opening_evidence},
    )


def _case_artifact(case: Path, relative_path):
    if not isinstance(relative_path, str) or not relative_path:
        return None
    try:
        target = (case / relative_path).resolve()
        target.relative_to(case.resolve())
    except (OSError, ValueError):
        return None
    return target if target.is_file() else None


def _matches_sha256(path: Path | None, expected) -> bool:
    if path is None or not isinstance(expected, str) or not expected:
        return False
    try:
        return _sha256(path) == expected
    except OSError:
        return False


def _numerical_provenance_issues(case: Path, run, thermal_input):
    """Return fail-closed issues for the actual thermal scheme/PIMPLE inputs."""
    run_input = run.get("input") if isinstance(run.get("input"), dict) else {}
    provenance = run_input.get("numerical_provenance")
    if not isinstance(provenance, dict):
        return ["MISSING"]
    issues = []
    if provenance.get("contract") != THERMAL_NUMERICS_PROVENANCE_CONTRACT:
        issues.append("CONTRACT")
    source = provenance.get("source")
    if source not in {"thermal_initial_input", "thermal_restart_input"}:
        issues.append("SOURCE")

    thermal_path = case / "thermal_input.json"
    if not _matches_sha256(thermal_path, provenance.get("thermal_input_sha256")):
        issues.append("THERMAL_INPUT")
    system = provenance.get("system") if isinstance(provenance.get("system"), dict) else {}
    expected_system = (provenance.get("expected_system")
                       if isinstance(provenance.get("expected_system"), dict)
                       else {})
    for name, relative_path in THERMAL_NUMERICS_SYSTEM_FILES.items():
        if not _matches_sha256(case / relative_path, system.get(name)):
            issues.append(f"SYSTEM_{name}")
        if expected_system.get(name) != system.get(name):
            issues.append(f"EXPECTED_SYSTEM_{name}")

    effective_settings = run.get("effective_settings")
    effective_numerics = run.get("effective_numerics")
    if not isinstance(effective_settings, dict):
        issues.append("EFFECTIVE_SETTINGS")
    elif (provenance.get("effective_settings_sha256")
          != _canonical_json_sha256(effective_settings)):
        issues.append("EFFECTIVE_SETTINGS_HASH")
    if not isinstance(effective_numerics, dict):
        issues.append("EFFECTIVE_NUMERICS")
    elif (provenance.get("effective_numerics_sha256")
          != _canonical_json_sha256(effective_numerics)):
        issues.append("EFFECTIVE_NUMERICS_HASH")

    source_input = thermal_input if source == "thermal_initial_input" else None
    if source == "thermal_restart_input":
        restart_path = case / "thermal_restart_input.json"
        restart_input = _load_json(restart_path)
        if not _matches_sha256(
                restart_path, provenance.get("thermal_restart_input_sha256")):
            issues.append("RESTART_INPUT")
        if (not isinstance(restart_input, dict)
                or restart_input.get("contract") != "thermal_restart_input.v1"
                or restart_input.get("thermal_input_sha256")
                != provenance.get("thermal_input_sha256")):
            issues.append("RESTART_CONTRACT")
        source_input = restart_input
    elif provenance.get("thermal_restart_input_sha256") is not None:
        issues.append("UNEXPECTED_RESTART_INPUT")

    if not isinstance(source_input, dict):
        issues.append("SOURCE_INPUT")
    else:
        source_settings = source_input.get("settings")
        source_numerics = (source_input.get("thermal_numerics")
                           if source == "thermal_restart_input"
                           else source_input.get("numerics"))
        if (not isinstance(source_settings, dict)
                or source_settings != effective_settings):
            issues.append("SETTINGS_MISMATCH")
        if (not isinstance(source_numerics, dict)
                or source_numerics != effective_numerics):
            issues.append("NUMERICS_MISMATCH")

    try:
        fv_schemes = (case / THERMAL_NUMERICS_SYSTEM_FILES["fvSchemes"]).read_text(
            encoding="utf-8", errors="replace"
        )
        fv_solution = (case / THERMAL_NUMERICS_SYSTEM_FILES["fvSolution"]).read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        issues.append("SEMANTIC_SYSTEM_READ")
    else:
        semantic = cfd_numerics.validate_effective_openfoam_numerics(
            effective_numerics, fv_schemes, fv_solution
        )
        issues.extend(f"SEMANTIC_{issue}" for issue in semantic["issues"])
    return list(dict.fromkeys(issues))


def body_fitted_numerical_provenance_issues(case_dir, run, thermal_input):
    """Return read-only numerical provenance failures without the GCI gate.

    GCI loading may call this small check before assembling a grid study.  It
    verifies both current file hashes and numerical semantics but deliberately
    does not call :func:`evaluate_body_fitted_case`, avoiding a GCI cycle.
    """
    return _numerical_provenance_issues(Path(case_dir), run, thermal_input)


def _valid_body_summary(payload) -> bool:
    if not isinstance(payload, dict):
        return False
    bounds = payload.get("bounds_m") if isinstance(payload.get("bounds_m"), dict) else {}
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    try:
        return (
            payload.get("contract") == "body_fitted_summary.v1"
            and math.isfinite(float(payload.get("time_s")))
            and int(payload.get("cell_count")) > 0
            and all(isinstance(bounds.get(name), list) and len(bounds[name]) == 3
                    for name in ("minimum", "maximum"))
            and {"T", "U"}.issubset(fields)
            and isinstance(payload.get("temperature"), dict)
            and isinstance(payload.get("velocity"), dict)
        )
    except (TypeError, ValueError):
        return False


def _valid_body_slice(payload, axis) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get("axis") == axis
        and isinstance(payload.get("samples"), list)
        and isinstance(payload.get("sample_count"), int)
        and payload["sample_count"] == len(payload["samples"])
    )


def _current_case_provenance(case: Path) -> dict | None:
    names = {
        "run_manifest_sha256": "run_manifest.json",
        "result_manifest_sha256": "result_manifest.json",
        "mesh_manifest_sha256": "mesh_manifest.json",
        "thermal_input_sha256": "thermal_input.json",
    }
    try:
        return {key: _sha256(case / filename) for key, filename in names.items()}
    except OSError:
        return None


def _find_passing_gci_manifest(case: Path, gci_root, provenance) -> Path | None:
    if not isinstance(provenance, dict) or not provenance:
        return None
    root = Path(gci_root) if gci_root is not None else case.parent.parent / "_body_gci"
    try:
        candidates = root.glob("*/grid_convergence.json")
    except OSError:
        return None
    for path in candidates:
        manifest = _load_json(path)
        if not manifest or manifest.get("status") != "PASS" or not manifest.get("design_ready"):
            continue
        if manifest.get("contract") != "grid_convergence.v3":
            continue
        for item in manifest.get("cases") or []:
            try:
                item_provenance = item.get("provenance") if isinstance(item, dict) else None
                if (Path(item.get("path") or "").resolve() == case.resolve()
                        and isinstance(item_provenance, dict)
                        and all(item_provenance.get(key) == value
                                for key, value in (provenance or {}).items())):
                    return path
            except (OSError, ValueError, TypeError):
                continue
    return None


def _validate_final_evidence_document(filename, evidence, *, anchor_reference,
                                      evidence_path=None, current_case=None):
    """Reject self-declared PASS files unless a live verifier owns the contract."""
    if not isinstance(evidence, dict):
        return ["FINAL_EVIDENCE_MISSING"]
    if filename == "numerical_sensitivity.json":
        blockers = list(
            cfd_numerics.validate_numerical_sensitivity(evidence).get("blockers") or []
        )
        if evidence.get("validation_anchor") != anchor_reference:
            blockers.append("NUMERICAL_SENSITIVITY_VALIDATION_ANCHOR_MISMATCH")
        verification = evidence.get("verification")
        if not isinstance(verification, dict):
            blockers.append("NUMERICAL_SENSITIVITY_LIVE_VERIFICATION_REQUIRED")
        elif current_case is None:
            blockers.append("NUMERICAL_SENSITIVITY_LIVE_VERIFICATION_REQUIRED")
        elif not blockers:
            try:
                import run_numerical_sensitivity
                study_root = Path(verification["study_root"])
                verification_evidence_path = (
                    study_root / verification["evidence_path"]
                )
                if (not verification_evidence_path.is_file()
                        or _sha256(verification_evidence_path)
                        != verification["evidence_sha256"]):
                    raise ValueError("verification evidence hash mismatch")
                recomputed = run_numerical_sensitivity.verify_serial_sensitivity_pair(
                    study_root, Path(current_case), publish=False)
            except (KeyError, OSError, TypeError, ValueError, RuntimeError):
                blockers.append("NUMERICAL_SENSITIVITY_LIVE_REVERIFICATION_FAILED")
            else:
                comparable = {
                    key: recomputed.get(key) for key in evidence
                }
                if comparable != evidence:
                    blockers.append("NUMERICAL_SENSITIVITY_LIVE_RECOMPUTE_MISMATCH")
        return list(dict.fromkeys(blockers))
    if filename == "temporal_sensitivity.json":
        # The current temporal contract deliberately prepares inputs only.  A
        # solver executor/verifier is required before any PASS can be trusted.
        return ["TEMPORAL_SENSITIVITY_VERIFIER_NOT_IMPLEMENTED"]
    if filename == "benchmark_validation.json":
        return ["BENCHMARK_VALIDATOR_NOT_IMPLEMENTED"]
    if filename == "applicability_envelope.json":
        return ["APPLICABILITY_VALIDATOR_NOT_IMPLEMENTED"]
    return ["FINAL_EVIDENCE_CONTRACT_UNKNOWN"]


def _resolve_gci_validation_anchor(gci_manifest_path, case):
    """Resolve and revalidate the anchor reference owned by the PASS GCI."""
    if gci_manifest_path is None:
        return None, [{
            "code": "GCI_VALIDATION_ANCHOR_MISSING",
            "message": "no passing GCI manifest",
        }]
    gci_manifest = _load_json(Path(gci_manifest_path)) or {}
    reference = gci_manifest.get("validation_anchor")
    if not isinstance(reference, dict):
        return None, [{
            "code": "GCI_VALIDATION_ANCHOR_MISSING",
            "message": str(gci_manifest_path),
        }]
    try:
        current = cfd_validation_anchor.anchor_reference(
            reference.get("path"), expected_case=case, expected_role="gci_fine",
        )
    except (OSError, cfd_validation_anchor.ValidationAnchorError) as exc:
        return None, [{
            "code": "GCI_VALIDATION_ANCHOR_INVALID", "message": str(exc),
        }]
    if current != reference:
        return None, [{
            "code": "GCI_VALIDATION_ANCHOR_CHANGED",
            "message": "GCI anchor reference differs from current anchor bytes",
        }]
    return current, []


def _evaluate_body_fitted_case(case_dir, *, gci_root=None, candidate_only=False):
    """Validate a body-fitted result against its run and mesh provenance."""
    case = Path(case_dir)
    run_path = case / "run_manifest.json"
    result_path = case / "result_manifest.json"
    mesh_path = case / "mesh_manifest.json"
    run = _load_json(run_path)
    result = _load_json(result_path)
    mesh = _load_json(mesh_path)
    thermal_path = case / "thermal_input.json"
    thermal_input = _load_json(thermal_path)
    blockers = []
    reasons = []

    if not run or not result or not mesh:
        missing = [name for name, value in (
            ("run_manifest", run), ("result_manifest", result), ("mesh_manifest", mesh)
        ) if not value]
        return _payload(
            status="NOT_EVALUATED", run_status="NOT_EVALUATED",
            convergence_status="NOT_EVALUATED", design_ready=False,
            citation_status="NOT_EVALUATED", citable=False,
            blockers=[f"{name}_missing" for name in missing],
            reasons=["설계 검토에 필요한 manifest가 완전하지 않습니다."],
            evidence={"engine": "body_fitted"},
        )

    run_status = run.get("status")
    if run_status == "FAIL":
        return _payload(
            status="FAIL", run_status="FAIL", convergence_status="FAIL",
            design_ready=False, citation_status="NOT_EVALUATED", citable=False,
            blockers=["run_manifest"],
            reasons=["상세 열해석 run manifest가 FAIL입니다."],
            evidence={"engine": run.get("engine")},
        )
    if run_status != "PASS":
        return _payload(
            status="WARN", run_status=run_status or "WARN", convergence_status="WARN",
            design_ready=False, citation_status="NOT_EVALUATED", citable=False,
            blockers=["run_manifest"],
            reasons=["상세 열해석 run manifest가 PASS 상태가 아닙니다."],
            evidence={"engine": run.get("engine")},
        )
    if run.get("engine") != "body_fitted_buoyant_urans":
        return _payload(
            status="NOT_EVALUATED", run_status="PASS",
            convergence_status="NOT_EVALUATED", design_ready=False,
            citation_status="NOT_EVALUATED", citable=False,
            blockers=["run_engine"],
            reasons=[
                "Only the body_fitted_buoyant_urans thermal contract can be "
                "considered for a design-citable result."
            ],
            evidence={"engine": run.get("engine")},
        )

    numerical_quality = run.get("numerical_quality")
    effective_numerics = run.get("effective_numerics")
    design_profile = cfd_numerics.DESIGN_LIMITED_SECOND_ORDER
    numerical_profile_ok = (
        isinstance(effective_numerics, dict)
        and effective_numerics.get("profile") == design_profile
        and effective_numerics.get("convection_order") == 2
        and isinstance(numerical_quality, dict)
        and numerical_quality.get("profile") == effective_numerics.get("profile")
        and numerical_quality.get("convection_order")
        == effective_numerics.get("convection_order")
    )
    quality_blockers = (
        numerical_quality.get("blockers")
        if isinstance(numerical_quality, dict)
        and isinstance(numerical_quality.get("blockers"), list)
        else []
    )
    pending_only = bool(quality_blockers) and all(
        isinstance(item, str) and item.startswith("NUMERICAL_SENSITIVITY_")
        for item in quality_blockers
    )
    numerical_quality_ok = (
        isinstance(numerical_quality, dict)
        and numerical_quality.get("contract") == "numerical_quality.v1"
        and (
            (
                numerical_quality.get("status") == "PASS"
                and numerical_quality.get("design_ready") is True
                and not quality_blockers
            )
            or (
                numerical_quality.get("status") == "NOT_EVALUATED"
                and numerical_quality.get("design_ready") is False
                and pending_only
            )
        )
        and _finite_at_least(numerical_quality.get("convection_order"), 2)
        and numerical_profile_ok
    )
    if not numerical_quality_ok:
        blockers.append("numerical_quality")
        reasons.append(
            "설계 검토에 필요한 2차 수치 스킴·잔차·phi 수지·민감도 증적이 없습니다."
        )
    if not numerical_profile_ok:
        blockers.append("numerical_profile")
        reasons.append(
            "설계 인용에는 run의 effective_numerics와 numerical_quality가 모두 "
            "design_limited_second_order_v1 / 2차 대류 차수로 일치해야 합니다."
        )
    if run.get("contract") != "run_manifest.v1":
        blockers.append("run_manifest")
        reasons.append("run manifest 계약을 확인할 수 없습니다.")
    run_input = run.get("input") if isinstance(run.get("input"), dict) else {}
    if not run_input:
        blockers.append("input_provenance")
        reasons.append("입력 조건 provenance가 run manifest에 없습니다.")
    elif not _matches_sha256(
            case / "thermal_input.json", run_input.get("thermal_input_sha256")):
        blockers.append("input_provenance")
        reasons.append("현재 thermal input이 run manifest의 입력 해시와 일치하지 않습니다.")
    numerical_provenance_issues = body_fitted_numerical_provenance_issues(
        case, run, thermal_input
    )
    if numerical_provenance_issues:
        blockers.append("numerical_provenance")
        reasons.append(
            "The OpenFOAM thermal scheme/PIMPLE provenance is missing, stale, "
            "or differs from the recorded numerical profile: "
            + ", ".join(numerical_provenance_issues)
        )
    if (isinstance(thermal_input, dict)
            and isinstance(thermal_input.get("assumptions"), dict)
            and thermal_input["assumptions"].get("radiation_modelled") is True):
        # The only current radiation artifact is a standalone two-plate
        # benchmark. It validates the view-factor toolchain, but cannot
        # represent the split enclosure surfaces, materials, or exterior
        # thermal boundaries of a field DXF/OCC case. Keep a field result
        # fail-closed even if a benchmark-shaped manifest is copied beside it.
        blockers.append("radiation_project_integration_pending")
        reasons.append(
            "Radiation is not enabled for field/project body-fitted cases; "
            "the serial two-plate artifact is benchmark-only."
        )
        radiation_path = case / "radiation_manifest.json"
        radiation = _load_json(radiation_path)
        radiation_ok = False
        if thermal_path.is_file():
            try:
                cfd_radiation.validate_radiation_manifest(
                    radiation, thermal_input_sha256=_sha256(thermal_path)
                )
                radiation_ok = True
            except cfd_radiation.RadiationInputError:
                pass
        if not radiation_ok:
            blockers.append("radiation_manifest")
            reasons.append(
                "복사 모델이 선언되었지만 현재 thermal input과 연결된 PASS radiation manifest가 없습니다."
            )
    if mesh.get("contract") != "mesh_manifest.v1":
        blockers.append("mesh_manifest")
        reasons.append("mesh manifest 계약을 확인할 수 없습니다.")
    if mesh.get("status") != "PASS":
        blockers.append("mesh_manifest")
        reasons.append("메시 품질 manifest가 PASS가 아닙니다.")
    if (result.get("contract") != "result_manifest.v1"
            or result.get("engine") != "body_fitted_openfoam_vtu"):
        blockers.append("result_manifest")
        reasons.append("결과 artifact 계약 또는 엔진을 확인할 수 없습니다.")

    source = result.get("source") if isinstance(result.get("source"), dict) else {}
    source_path = _case_artifact(case, source.get("path"))
    summary_path = _case_artifact(case, result.get("summary_path"))
    slices = result.get("slices") if isinstance(result.get("slices"), list) else []
    slice_paths = [_case_artifact(case, item.get("path")) for item in slices
                   if isinstance(item, dict)]
    axes = {item.get("axis") for item in slices if isinstance(item, dict)}
    summary = _load_json(summary_path) if summary_path is not None else None
    slices_ok = (
        len(slices) == len(slice_paths)
        and all(
            _matches_sha256(path, item.get("sha256"))
            and _valid_body_slice(_load_json(path), item.get("axis"))
            for item, path in zip(slices, slice_paths)
        )
    )
    if (not _matches_sha256(source_path, source.get("sha256"))
            or not _matches_sha256(summary_path, result.get("summary_sha256"))
            or not _valid_body_summary(summary)
            or len(slices) < 3 or axes != {"x", "y", "z"} or not slices_ok):
        blockers.append("result_artifacts")
        reasons.append("VTU source, 요약, 또는 x/y/z 단면 artifact가 완전하지 않거나 일치하지 않습니다.")
    if run_path.is_file() and result.get("run_manifest_sha256") != _sha256(run_path):
        blockers.append("result_manifest_stale")
        reasons.append("결과 artifact가 현재 run manifest와 일치하지 않습니다.")
    if mesh_path.is_file() and result.get("mesh_manifest_sha256") != _sha256(mesh_path):
        blockers.append("mesh_manifest_stale")
        reasons.append("결과 artifact가 현재 mesh manifest와 일치하지 않습니다.")
    thermal_path = case / "thermal_input.json"
    if (not thermal_path.is_file()
            or result.get("thermal_input_sha256") != _sha256(thermal_path)):
        blockers.append("result_input_provenance")
        reasons.append("결과 artifact가 현재 thermal input과 직접 연결되지 않습니다.")
    if candidate_only:
        if blockers:
            return _payload(
                status="NOT_EVALUATED", run_status="PASS",
                convergence_status="NOT_EVALUATED", design_ready=False,
                citation_status="NOT_EVALUATED", citable=False,
                blockers=blockers, reasons=reasons,
                evidence={"engine": run.get("engine"), "scope": "gci_candidate"},
            )
        return _payload(
            status="GCI_CANDIDATE", run_status="PASS", convergence_status="PASS",
            design_ready=False, citation_status="NOT_EVALUATED", citable=False,
            blockers=[], reasons=[],
            evidence={"engine": run.get("engine"), "scope": "gci_candidate"},
        )
    gci_manifest_path = _find_passing_gci_manifest(
        case, gci_root, _current_case_provenance(case)
    )
    if gci_manifest_path is None:
        blockers.append("gci")
        reasons.append("현재 케이스와 연결된 PASS 메시 불확실성(GCI) 증거가 없습니다.")
    anchor_reference, anchor_issues = _resolve_gci_validation_anchor(
        gci_manifest_path, case,
    )
    if anchor_issues:
        blockers.append("validation_anchor")
        reasons.append(
            "현재 fine case의 raw geometry/surface/mesh/run/result/thermal/selector를 "
            "다시 검증하는 Validation Anchor가 없습니다: "
            + ", ".join(item["code"] for item in anchor_issues)
        )
    for filename, blocker, message in (
        (
            "numerical_sensitivity.json", "scheme_sensitivity",
            "현재 Validation Anchor에 결속된 verified scheme sensitivity가 없습니다.",
        ),
        (
            "temporal_sensitivity.json", "temporal_sensitivity",
            "현재 Validation Anchor에 결속된 verified temporal sensitivity가 없습니다.",
        ),
        (
            "benchmark_validation.json", "benchmark_validation",
            "승인된 benchmark validation 증거가 없습니다.",
        ),
        (
            "applicability_envelope.json", "applicability",
            "현재 case의 benchmark-derived applicability 판정이 없습니다.",
        ),
    ):
        evidence = _load_json(case / filename)
        evidence_blockers = _validate_final_evidence_document(
            filename, evidence, anchor_reference=anchor_reference,
            evidence_path=case / filename, current_case=case,
        )
        if evidence_blockers:
            blockers.append(blocker)
            reasons.append(message + " [" + ", ".join(evidence_blockers) + "]")
    if blockers:
        return _payload(
            status="NOT_EVALUATED", run_status="PASS", convergence_status="PASS",
            design_ready=False, citation_status="NOT_EVALUATED", citable=False,
            blockers=blockers, reasons=reasons,
            evidence={"engine": run.get("engine")},
        )
    return _payload(
        status="PASS", run_status="PASS", convergence_status="PASS",
        design_ready=True, citation_status="DESIGN_CITABLE", citable=True,
        reasons=[], evidence={"engine": run.get("engine")},
    )


def evaluate_gci_candidate(case_dir):
    """Return a non-citable GCI input when only sensitivity/GCI is pending."""
    return _evaluate_body_fitted_case(case_dir, candidate_only=True)


def evaluate_body_fitted_case(case_dir, *, gci_root=None):
    """Evaluate the final citation gate, including independent V&V evidence."""
    return _evaluate_body_fitted_case(case_dir, gci_root=gci_root)
