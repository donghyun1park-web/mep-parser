"""Numerical-quality contracts for body-fitted buoyant OpenFOAM cases.

The contract intentionally separates a stable first-order screening solve from
a design-review candidate.  It does not infer physical credibility from a
solver exit code alone.
"""

from __future__ import annotations

import math
from numbers import Real
import re


CONTRACT = "thermal_numerics.v1"
STABILIZED_FIRST_ORDER = "stabilized_first_order_v1"
DESIGN_LIMITED_SECOND_ORDER = "design_limited_second_order_v1"
SUPPORTED_PROFILES = (STABILIZED_FIRST_ORDER, DESIGN_LIMITED_SECOND_ORDER)
THERMAL_RESIDUAL_LIMITS = {
    "Ux": 1e-4,
    "Uy": 1e-4,
    "Uz": 1e-4,
    "p_rgh": 1e-4,
    "T": 1e-5,
    "k": 1e-4,
    "omega": 1e-4,
}
# A candidate case needs several recent linear-solver samples, not just the
# final line in the log.  The parser retains a longer history separately; this
# is the minimum tail used for a design-review gate.
DEFAULT_RESIDUAL_TAIL_SAMPLES = 5
SENSITIVITY_CONTRACT = "numerical_sensitivity.v1"
REQUIRED_SENSITIVITY_QOIS = (
    "occupied_zone_mean_temperature_k",
    "occupied_zone_mean_speed_m_s",
    "exhaust_temperature_rise_k",
)
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_SOLVER_PHI_ENERGY_BASIS = "solver_positive_phi_and_owner_cell_temperature"
OPENFOAM_NUMERICS_SEMANTICS_CONTRACT = "openfoam_numerics_semantics.v1"
_DESIGN_LIMITED_CORRECTION = "limited 0.5"


class NumericalInputError(ValueError):
    """Raised when a numerical profile cannot be evaluated safely."""


def _without_openfoam_comments(text):
    """Return a small, parser-safe view of an OpenFOAM dictionary text.

    The numerical provenance gate does not need a general OpenFOAM parser.  It
    only reads the named top-level dictionaries and their simple semicolon
    terminated entries, after removing comments so a commented-out scheme
    cannot be mistaken for the active configuration.
    """
    if not isinstance(text, str):
        return ""
    without_blocks = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//[^\r\n]*", "", without_blocks)


def _openfoam_dictionary_block(text, name):
    """Extract the braced body for one named OpenFOAM dictionary."""
    match = re.search(rf"\b{re.escape(name)}\b\s*\{{", text)
    if match is None:
        return None
    depth = 1
    start = match.end()
    for position in range(start, len(text)):
        character = text[position]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[start:position]
    return None


def _openfoam_statement(block, key):
    """Read one direct ``key value;`` entry without evaluating it."""
    if not isinstance(block, str):
        return None
    match = re.search(
        rf"(?:^|[;\r\n])\s*{re.escape(key)}\s+([^;{{}}]+);",
        block,
    )
    return match.group(1).strip() if match is not None else None


def _openfoam_integer(block, key):
    value = _openfoam_statement(block, key)
    if value is None or re.fullmatch(r"[+-]?\d+", value) is None:
        return None
    return int(value)


def _declares_limited_half(value):
    return (isinstance(value, str)
            and re.search(r"\blimited\s+0?\.5(?:0+)?\b", value) is not None)


def _declares_limited_linear(value):
    return isinstance(value, str) and re.search(r"\blimitedLinear\b", value) is not None


def _declares_second_order_velocity(value):
    return (isinstance(value, str)
            and re.search(r"\b(?:linearUpwind|limitedLinear)\b", value) is not None)


def _integer_nonnegative(value):
    return (isinstance(value, Real) and not isinstance(value, bool)
            and math.isfinite(value) and int(value) == value and value >= 0)


def validate_effective_openfoam_numerics(numerics, fv_schemes, fv_solution):
    """Validate that saved OpenFOAM files mean what a profile declares.

    Hashes establish file identity but cannot establish numerical meaning when
    a malformed case was already present before a run.  This pure verifier
    therefore compares the active dictionary semantics against the effective
    numerical profile.  It intentionally accepts only the in-product design
    limited-second-order form for a design profile; it does not guess a scheme
    order from a file hash or a solver result.
    """
    numerics = numerics if isinstance(numerics, dict) else {}
    profile = numerics.get("profile")
    issues = []
    observed = {}

    if profile not in SUPPORTED_PROFILES:
        issues.append("PROFILE_UNSUPPORTED")
        return {
            "contract": OPENFOAM_NUMERICS_SEMANTICS_CONTRACT,
            "profile": profile,
            "valid": False,
            "issues": issues,
            "observed": observed,
        }

    schemes = _without_openfoam_comments(fv_schemes)
    solution = _without_openfoam_comments(fv_solution)
    div_schemes = _openfoam_dictionary_block(schemes, "divSchemes")
    laplacian_schemes = _openfoam_dictionary_block(schemes, "laplacianSchemes")
    sn_grad_schemes = _openfoam_dictionary_block(schemes, "snGradSchemes")
    pimple = _openfoam_dictionary_block(solution, "PIMPLE")
    observed["div_phi_u"] = _openfoam_statement(div_schemes, "div(phi,U)")
    observed["div_phi_t"] = _openfoam_statement(div_schemes, "div(phi,T)")
    observed["div_phi_k"] = _openfoam_statement(div_schemes, "div(phi,k)")
    observed["div_phi_omega"] = _openfoam_statement(div_schemes, "div(phi,omega)")
    observed["laplacian_default"] = _openfoam_statement(laplacian_schemes, "default")
    observed["sn_grad_default"] = _openfoam_statement(sn_grad_schemes, "default")
    observed["pimple_correctors"] = _openfoam_integer(pimple, "nCorrectors")
    observed["pimple_non_orthogonal_correctors"] = _openfoam_integer(
        pimple, "nNonOrthogonalCorrectors"
    )

    if profile != DESIGN_LIMITED_SECOND_ORDER:
        # A first-order profile is never design-citable.  Its detailed scheme
        # validation remains intentionally outside this promotion gate.
        return {
            "contract": OPENFOAM_NUMERICS_SEMANTICS_CONTRACT,
            "profile": profile,
            "valid": True,
            "issues": [],
            "observed": observed,
        }

    if numerics.get("convection_order") != 2:
        issues.append("CONVECTION_ORDER_DECLARATION_INVALID")
    if numerics.get("laplacian_correction") != _DESIGN_LIMITED_CORRECTION:
        issues.append("LAPLACIAN_DECLARATION_INVALID")
    if numerics.get("sn_grad_correction") != _DESIGN_LIMITED_CORRECTION:
        issues.append("SN_GRAD_DECLARATION_INVALID")
    required_non_orthogonal = numerics.get("required_non_orthogonal_correctors")
    if not _integer_nonnegative(required_non_orthogonal):
        issues.append("PIMPLE_NONORTH_EXPECTATION_INVALID")
        required_non_orthogonal = None

    if div_schemes is None:
        issues.append("DIV_SCHEMES_MISSING")
    else:
        if not _declares_second_order_velocity(observed["div_phi_u"]):
            issues.append("DIV_PHI_U_NOT_LIMITED_SECOND_ORDER")
        for field, issue in (
                ("div_phi_t", "DIV_PHI_T_NOT_LIMITED_SECOND_ORDER"),
                ("div_phi_k", "DIV_PHI_K_NOT_LIMITED_SECOND_ORDER"),
                ("div_phi_omega", "DIV_PHI_OMEGA_NOT_LIMITED_SECOND_ORDER")):
            if not _declares_limited_linear(observed[field]):
                issues.append(issue)
    if not _declares_limited_half(observed["laplacian_default"]):
        issues.append("LAPLACIAN_NOT_LIMITED")
    if not _declares_limited_half(observed["sn_grad_default"]):
        issues.append("SN_GRAD_NOT_LIMITED")
    if observed["pimple_correctors"] is None or observed["pimple_correctors"] < 2:
        issues.append("PIMPLE_CORRECTORS_INSUFFICIENT")
    actual_non_orthogonal = observed["pimple_non_orthogonal_correctors"]
    if actual_non_orthogonal is None:
        issues.append("PIMPLE_NONORTH_MISSING")
    elif (required_non_orthogonal is not None
          and actual_non_orthogonal < required_non_orthogonal):
        issues.append("PIMPLE_NONORTH_BELOW_REQUIRED")

    return {
        "contract": OPENFOAM_NUMERICS_SEMANTICS_CONTRACT,
        "profile": profile,
        "valid": not issues,
        "issues": list(dict.fromkeys(issues)),
        "observed": observed,
    }


def _finite_nonnegative(value, label):
    if not isinstance(value, Real) or isinstance(value, bool) or not math.isfinite(value):
        raise NumericalInputError(f"{label} 값은 유한한 숫자여야 합니다.")
    if value < 0:
        raise NumericalInputError(f"{label} 값은 0 이상이어야 합니다.")
    return float(value)


def required_non_orthogonal_correctors(max_non_orthogonality):
    """Return the conservative PIMPLE correction count for a mesh metric."""
    value = _finite_nonnegative(max_non_orthogonality, "최대 비직교성")
    if value <= 20:
        return 0
    if value <= 45:
        return 1
    return 2


def _mesh_non_orthogonality(mesh_manifest):
    if not isinstance(mesh_manifest, dict):
        return None
    mesh = mesh_manifest.get("mesh")
    if not isinstance(mesh, dict):
        return None
    value = mesh.get("max_non_orthogonality")
    if value is None:
        return None
    return _finite_nonnegative(value, "최대 비직교성")


def thermal_numerics_contract(mesh_manifest, settings=None):
    """Describe the configured solver order and mesh-dependent corrections.

    ``CANDIDATE`` means the input settings are eligible for a later numerical
    sensitivity study.  It is deliberately not a result PASS status.
    """
    cfg = dict(settings or {})
    profile = str(cfg.get("thermal_numerics_profile") or STABILIZED_FIRST_ORDER)
    if profile not in SUPPORTED_PROFILES:
        raise NumericalInputError(f"지원하지 않는 thermal 수치 프로필입니다: {profile}")
    max_nonorth = _mesh_non_orthogonality(mesh_manifest)
    blockers = []
    if max_nonorth is None:
        blockers.append("MESH_NONORTH_EVIDENCE_MISSING")
        required_correctors = 0
    else:
        required_correctors = required_non_orthogonal_correctors(max_nonorth)
        if max_nonorth > 65:
            blockers.append("MESH_NONORTH_HIGH")

    if profile == STABILIZED_FIRST_ORDER:
        convection_order = 1
        laplacian_correction = "uncorrected"
        sn_grad_correction = "uncorrected"
        blockers.insert(0, "FIRST_ORDER_PROFILE")
        status = "SCREENING_ONLY"
        design_eligible = False
    else:
        convection_order = 2
        laplacian_correction = "limited 0.5"
        sn_grad_correction = "limited 0.5"
        design_eligible = not blockers
        status = "CANDIDATE" if design_eligible else "SCREENING_ONLY"

    return {
        "contract": CONTRACT,
        "profile": profile,
        "convection_order": convection_order,
        "laplacian_correction": laplacian_correction,
        "sn_grad_correction": sn_grad_correction,
        "max_non_orthogonality": max_nonorth,
        "required_non_orthogonal_correctors": required_correctors,
        "design_eligible": design_eligible,
        "status": status,
        "blockers": list(dict.fromkeys(blockers)),
    }


def _finite_or_none(value):
    if not isinstance(value, Real) or isinstance(value, bool) or not math.isfinite(value):
        return None
    return float(value)


def _valid_sha256(value):
    return isinstance(value, str) and _SHA256_PATTERN.fullmatch(value) is not None


def _validate_sensitivity_solver_evidence(run, role):
    """Validate one side of a sensitivity pair without trusting its peer."""
    prefix = f"NUMERICAL_SENSITIVITY_{role.upper()}"
    blockers = []
    evidence = run.get("solver_evidence") if isinstance(run, dict) else None
    if not isinstance(evidence, dict):
        return [f"{prefix}_SOLVER_EVIDENCE_MISSING"]

    if evidence.get("ended") is not True:
        blockers.append(f"{prefix}_NOT_ENDED")
    if evidence.get("fatal_error") is not False:
        blockers.append(f"{prefix}_FATAL_ERROR")

    peak_courant = _finite_or_none(evidence.get("peak_courant"))
    courant_limit = _finite_or_none(evidence.get("courant_limit"))
    if (peak_courant is None or peak_courant < 0
            or courant_limit is None or courant_limit <= 0):
        blockers.append(f"{prefix}_COURANT_EVIDENCE_INVALID")
    elif peak_courant > courant_limit:
        blockers.append(f"{prefix}_COURANT_LIMIT")

    residuals = evidence.get("residuals")
    residual_evidence_invalid = not isinstance(residuals, dict)
    residual_limit_failed = False
    if isinstance(residuals, dict):
        for field, maximum_limit in THERMAL_RESIDUAL_LIMITS.items():
            row = residuals.get(field)
            final = _finite_or_none(row.get("final") if isinstance(row, dict) else None)
            tail_maximum = _finite_or_none(
                row.get("tail_maximum") if isinstance(row, dict) else None
            )
            tail_samples = _finite_or_none(
                row.get("tail_samples") if isinstance(row, dict) else None
            )
            limit = _finite_or_none(row.get("limit") if isinstance(row, dict) else None)
            if (final is None or final < 0
                    or tail_maximum is None or tail_maximum < 0
                    or tail_samples is None or tail_samples < DEFAULT_RESIDUAL_TAIL_SAMPLES
                    or tail_samples != int(tail_samples)
                    or limit is None or limit <= 0 or limit > maximum_limit):
                residual_evidence_invalid = True
                continue
            if final > limit or tail_maximum > limit:
                residual_limit_failed = True
    if residual_evidence_invalid:
        blockers.append(f"{prefix}_RESIDUAL_EVIDENCE_INVALID")
    if residual_limit_failed:
        blockers.append(f"{prefix}_RESIDUAL_LIMIT")

    continuity = evidence.get("continuity")
    continuity_global = _finite_or_none(
        continuity.get("global") if isinstance(continuity, dict) else None
    )
    continuity_limit = _finite_or_none(
        continuity.get("limit") if isinstance(continuity, dict) else None
    )
    if (continuity_global is None or continuity_limit is None
            or continuity_limit <= 0 or continuity_limit > 1e-6):
        blockers.append(f"{prefix}_CONTINUITY_EVIDENCE_INVALID")
    elif abs(continuity_global) > continuity_limit:
        blockers.append(f"{prefix}_CONTINUITY_LIMIT")

    phi_balance = evidence.get("phi_balance")
    phi_imbalance = _finite_or_none(
        phi_balance.get("imbalance_ratio")
        if isinstance(phi_balance, dict) else None
    )
    phi_limit = _finite_or_none(
        phi_balance.get("limit") if isinstance(phi_balance, dict) else None
    )
    if (not isinstance(phi_balance, dict)
            or phi_balance.get("available") is not True
            or phi_imbalance is None or phi_imbalance < 0
            or phi_limit is None or phi_limit <= 0 or phi_limit > 0.001):
        blockers.append(f"{prefix}_PHI_EVIDENCE_INVALID")
    elif phi_imbalance > phi_limit:
        blockers.append(f"{prefix}_PHI_LIMIT")

    if evidence.get("energy_closure_basis") != _SOLVER_PHI_ENERGY_BASIS:
        blockers.append(f"{prefix}_ENERGY_BASIS")
    return blockers


def validate_numerical_sensitivity(sensitivity):
    """Fail closed unless a complete, stable same-input solver pair is proven."""
    if not isinstance(sensitivity, dict):
        return {
            "contract": SENSITIVITY_CONTRACT,
            "valid": False,
            "blockers": ["NUMERICAL_SENSITIVITY_PENDING"],
        }

    blockers = []
    if sensitivity.get("contract") != SENSITIVITY_CONTRACT:
        blockers.append("NUMERICAL_SENSITIVITY_CONTRACT_INVALID")
    if sensitivity.get("status") != "PASS":
        blockers.append("NUMERICAL_SENSITIVITY_STATUS_NOT_PASS")

    verification = sensitivity.get("verification")
    expected_verification_fields = {
        "contract", "verifier", "raw_artifacts_rehashed", "study_root",
        "current_case_child", "evidence_path", "evidence_sha256",
    }
    if (not isinstance(verification, dict)
            or set(verification) != expected_verification_fields
            or verification.get("contract") != "numerical_sensitivity_verification.v1"
            or verification.get("verifier") != (
                "run_numerical_sensitivity.verify_serial_sensitivity_pair")
            or verification.get("raw_artifacts_rehashed") is not True
            or not isinstance(verification.get("study_root"), str)
            or not verification.get("study_root", "").strip()
            or verification.get("current_case_child") != "variant_second_order"
            or verification.get("evidence_path") != (
                "numerical_sensitivity_verification.v1.json")
            or not _valid_sha256(verification.get("evidence_sha256"))):
        blockers.append("NUMERICAL_SENSITIVITY_VERIFICATION_INVALID")

    provenance = sensitivity.get("provenance")
    if (not isinstance(provenance, dict)
            or provenance.get("explicit_job") is not True
            or provenance.get("source") != "cfd_numerical_sensitivity_job"
            or not isinstance(provenance.get("job_id"), str)
            or not provenance.get("job_id", "").strip()):
        blockers.append("NUMERICAL_SENSITIVITY_PROVENANCE_MISSING")

    baseline = sensitivity.get("baseline")
    variant = sensitivity.get("variant")
    if not isinstance(baseline, dict) or not isinstance(variant, dict):
        blockers.append("NUMERICAL_SENSITIVITY_PAIR_MISSING")
        baseline = baseline if isinstance(baseline, dict) else {}
        variant = variant if isinstance(variant, dict) else {}

    hash_fields = ("run_hash", "mesh_hash", "physical_input_hash")
    if any(
        not _valid_sha256(side.get(field))
        for side in (baseline, variant)
        for field in hash_fields
    ):
        blockers.append("NUMERICAL_SENSITIVITY_HASH_INVALID")
    else:
        if baseline["run_hash"].lower() == variant["run_hash"].lower():
            blockers.append("NUMERICAL_SENSITIVITY_RUN_HASH_NOT_DISTINCT")
        if baseline["mesh_hash"].lower() != variant["mesh_hash"].lower():
            blockers.append("NUMERICAL_SENSITIVITY_MESH_HASH_MISMATCH")
        if (baseline["physical_input_hash"].lower()
                != variant["physical_input_hash"].lower()):
            blockers.append("NUMERICAL_SENSITIVITY_PHYSICAL_INPUT_HASH_MISMATCH")

    allowed = sensitivity.get("allowed_variation")
    if (not isinstance(allowed, dict)
            or allowed.get("parameter") != "thermal_numerics_profile"
            or allowed.get("baseline") != STABILIZED_FIRST_ORDER
            or allowed.get("variant") != DESIGN_LIMITED_SECOND_ORDER
            or allowed.get("all_other_inputs_equal") is not True
            or baseline.get("profile") != STABILIZED_FIRST_ORDER
            or variant.get("profile") != DESIGN_LIMITED_SECOND_ORDER):
        blockers.append("NUMERICAL_SENSITIVITY_ALLOWED_VARIATION_INVALID")

    blockers.extend(_validate_sensitivity_solver_evidence(baseline, "baseline"))
    blockers.extend(_validate_sensitivity_solver_evidence(variant, "variant"))

    comparisons = sensitivity.get("qoi_comparisons")
    rows_by_name = {}
    qoi_invalid = not isinstance(comparisons, list)
    qoi_limit_failed = False
    if isinstance(comparisons, list):
        for row in comparisons:
            if not isinstance(row, dict):
                qoi_invalid = True
                continue
            name = row.get("name")
            if not isinstance(name, str) or not name or name in rows_by_name:
                qoi_invalid = True
                continue
            rows_by_name[name] = row
            baseline_value = _finite_or_none(row.get("baseline"))
            variant_value = _finite_or_none(row.get("variant"))
            recorded_difference = _finite_or_none(row.get("absolute_difference"))
            limit = _finite_or_none(row.get("limit"))
            if (baseline_value is None or variant_value is None
                    or recorded_difference is None or recorded_difference < 0
                    or limit is None or limit <= 0
                    or row.get("passed") is not True):
                qoi_invalid = True
                continue
            actual_difference = abs(variant_value - baseline_value)
            tolerance = max(1e-9, actual_difference * 1e-9)
            if not math.isclose(
                recorded_difference, actual_difference, rel_tol=1e-9, abs_tol=tolerance
            ):
                qoi_invalid = True
            if actual_difference > limit:
                qoi_limit_failed = True
    if set(REQUIRED_SENSITIVITY_QOIS) - set(rows_by_name):
        blockers.append("NUMERICAL_SENSITIVITY_QOI_MISSING")
    if qoi_invalid:
        blockers.append("NUMERICAL_SENSITIVITY_QOI_INVALID")
    if qoi_limit_failed:
        blockers.append("NUMERICAL_SENSITIVITY_QOI_LIMIT")

    blockers = list(dict.fromkeys(blockers))
    return {
        "contract": SENSITIVITY_CONTRACT,
        "valid": not blockers,
        "blockers": blockers,
    }


def evaluate_thermal_numerics(numerics, solver, thermal, flux_balance, settings,
                              sensitivity=None, y_plus=None):
    """Evaluate numerical evidence independently from a solver exit code.

    The result is intentionally conservative: a second-order case is only a
    design candidate until a same-input numerical sensitivity artifact exists.
    """
    numerics = numerics if isinstance(numerics, dict) else {}
    solver = solver if isinstance(solver, dict) else {}
    thermal = thermal if isinstance(thermal, dict) else {}
    flux_balance = flux_balance if isinstance(flux_balance, dict) else {}
    settings = settings if isinstance(settings, dict) else {}
    y_plus = y_plus if isinstance(y_plus, dict) else {}
    blockers = list(numerics.get("blockers") or [])
    candidate_profile = numerics.get("profile") == DESIGN_LIMITED_SECOND_ORDER

    raw_tail_samples = _finite_or_none(
        settings.get("thermal_residual_tail_samples", DEFAULT_RESIDUAL_TAIL_SAMPLES)
    )
    if (raw_tail_samples is None or raw_tail_samples < 1
            or raw_tail_samples != int(raw_tail_samples)):
        residual_tail_samples_required = DEFAULT_RESIDUAL_TAIL_SAMPLES
        blockers.append("RESIDUAL_TAIL_POLICY_INVALID")
    else:
        residual_tail_samples_required = int(raw_tail_samples)

    residuals = solver.get("thermal_residuals")
    residuals = residuals if isinstance(residuals, dict) else {}
    residual_history = solver.get("thermal_residual_history")
    residual_history = residual_history if isinstance(residual_history, dict) else {}
    residual_evidence = {}
    missing_residuals = []
    failed_residuals = []
    missing_residual_tail_fields = []
    failed_residual_tail_fields = []
    for field, limit in THERMAL_RESIDUAL_LIMITS.items():
        row = residuals.get(field)
        final = _finite_or_none(row.get("final") if isinstance(row, dict) else None)
        history_rows = residual_history.get(field)
        history_rows = history_rows if isinstance(history_rows, list) else []
        tail_values = [
            _finite_or_none(history_row.get("final"))
            for history_row in history_rows
            if isinstance(history_row, dict)
        ]
        tail_values = [value for value in tail_values if value is not None]
        tail_values = tail_values[-residual_tail_samples_required:]
        tail_maximum = max(tail_values) if tail_values else None
        tail_median = (
            sorted(tail_values)[len(tail_values) // 2]
            if tail_values else None
        )
        residual_evidence[field] = {
            "final": final,
            "limit": limit,
            "tail_samples": len(tail_values),
            "tail_maximum": tail_maximum,
            "tail_median": tail_median,
        }
        if final is None:
            missing_residuals.append(field)
        elif final > limit:
            failed_residuals.append(field)
        if candidate_profile:
            if len(tail_values) < residual_tail_samples_required:
                missing_residual_tail_fields.append(field)
            elif tail_maximum > limit:
                failed_residual_tail_fields.append(field)
    if missing_residuals:
        blockers.append("NUMERICAL_EVIDENCE_MISSING")
    if failed_residuals:
        blockers.append("TAIL_RESIDUAL_LIMIT")
    if missing_residual_tail_fields:
        blockers.append("RESIDUAL_TAIL_EVIDENCE_MISSING")
    if failed_residual_tail_fields:
        blockers.append("TAIL_RESIDUAL_LIMIT")

    continuity = solver.get("continuity") if isinstance(solver.get("continuity"), dict) else {}
    global_continuity = _finite_or_none(continuity.get("global"))
    if global_continuity is None:
        blockers.append("CONTINUITY_EVIDENCE_MISSING")
    elif abs(global_continuity) > 1e-6:
        blockers.append("CONTINUITY_LIMIT")

    courant = solver.get("courant") if isinstance(solver.get("courant"), dict) else {}
    final_courant = _finite_or_none(courant.get("maximum"))
    peak_courant = _finite_or_none(courant.get("peak_maximum"))
    control_max_courant = _finite_or_none(settings.get("thermal_max_co"))
    screening_courant_gate = _finite_or_none(
        settings.get("thermal_max_courant_gate", 2.0)
    )
    design_courant_gate = _finite_or_none(
        settings.get(
            "thermal_design_max_courant_gate",
            control_max_courant if control_max_courant is not None else 1.0,
        )
    )
    courant_gate = design_courant_gate if candidate_profile else screening_courant_gate
    if peak_courant is None:
        blockers.append("COURANT_EVIDENCE_MISSING")
    elif courant_gate is None:
        blockers.append("COURANT_POLICY_MISSING")
    elif peak_courant > courant_gate:
        blockers.append("COURANT_LIMIT")

    reference_t = _finite_or_none(settings.get("reference_temperature_k"))
    beta = _finite_or_none(settings.get("thermal_expansion_coefficient_1_k"))
    minimum_t = _finite_or_none(thermal.get("minimum_k"))
    maximum_t = _finite_or_none(thermal.get("maximum_k"))
    max_abs_delta_tref = None
    beta_delta = None
    if None in (reference_t, beta, minimum_t, maximum_t):
        blockers.append("BOUSSINESQ_EVIDENCE_MISSING")
    else:
        max_abs_delta_tref = max(abs(minimum_t - reference_t), abs(maximum_t - reference_t))
        beta_delta = beta * max_abs_delta_tref
        if beta_delta > float(settings.get("boussinesq_beta_delta_max", 0.1)):
            blockers.append("BOUSSINESQ_BETA_DELTA")

    imbalance = _finite_or_none(flux_balance.get("imbalance_ratio"))
    if flux_balance.get("available") is not True or imbalance is None:
        blockers.append("PHI_MASS_BALANCE_MISSING")
    elif imbalance > float(settings.get("terminal_phi_imbalance_max", 0.001)):
        blockers.append("PHI_MASS_BALANCE_LIMIT")
    if thermal.get("energy_closure_basis") != "solver_positive_phi_and_owner_cell_temperature":
        blockers.append("SOLVER_PHI_ENERGY_EVIDENCE_MISSING")

    y_plus_available = y_plus.get("available") is True
    wall_treatment_ratio = _finite_or_none(
        y_plus.get("wall_treatment_acceptable_area_ratio")
    )
    wall_treatment_limit = float(
        settings.get("minimum_wall_treatment_area_ratio", 0.80)
    )
    if not y_plus_available or wall_treatment_ratio is None:
        blockers.append("YPLUS_EVIDENCE_MISSING")
    elif wall_treatment_ratio < wall_treatment_limit:
        blockers.append("WALL_TREATMENT_COVERAGE")
    elif (candidate_profile
          and y_plus.get("method") != "openfoam_yPlus_field"):
        # The nut wall-function inversion is a useful screening diagnostic,
        # but a zero/low inferred nut is not direct evidence that a face is
        # genuinely wall-resolved (or outside the buffer layer).  A result
        # promoted for design review therefore needs the OpenFOAM yPlus field.
        blockers.append("YPLUS_DIRECT_FIELD_REQUIRED")

    sensitivity_validation = validate_numerical_sensitivity(sensitivity)
    if sensitivity_validation["valid"]:
        # ``numerical_sensitivity.json`` does not yet have an in-product
        # paired-solver producer nor a verifier that resolves its hashes back
        # to immutable run/mesh/log artifacts.  A structurally valid document
        # is therefore useful diagnostic information, but it is not evidence
        # that may promote this run to design-ready status.
        sensitivity_validation = {
            **sensitivity_validation,
            "structurally_valid": True,
            "valid": False,
            "blockers": ["NUMERICAL_SENSITIVITY_ARTIFACT_UNVERIFIED"],
        }
    else:
        sensitivity_validation = {
            **sensitivity_validation,
            "structurally_valid": False,
        }
    blockers.extend(sensitivity_validation["blockers"])

    blockers = list(dict.fromkeys(blockers))
    if numerics.get("profile") == STABILIZED_FIRST_ORDER:
        status = "SCREENING_ONLY"
    elif blockers:
        status = "NOT_EVALUATED"
    else:
        status = "PASS"
    return {
        "contract": "numerical_quality.v1",
        "status": status,
        "design_ready": status == "PASS",
        "profile": numerics.get("profile"),
        "convection_order": numerics.get("convection_order"),
        "blockers": blockers,
        "residuals": residual_evidence,
        "missing_residuals": missing_residuals,
        "failed_residuals": failed_residuals,
        "residual_tail": {
            "required_samples": residual_tail_samples_required,
            "scope": "design" if candidate_profile else "screening",
            "missing_fields": missing_residual_tail_fields,
            "failed_fields": failed_residual_tail_fields,
        },
        "continuity_global": global_continuity,
        "courant": {
            "final_maximum": final_courant,
            "peak_maximum": peak_courant,
            "control_max": control_max_courant,
            "gate": courant_gate,
            "scope": "design" if candidate_profile else "screening",
        },
        "flux_balance": flux_balance,
        "boussinesq": {
            "reference_temperature_k": reference_t,
            "max_abs_delta_tref_k": max_abs_delta_tref,
            "beta_delta": beta_delta,
            "beta_delta_limit": float(settings.get("boussinesq_beta_delta_max", 0.1)),
        },
        "wall_treatment": {
            "available": y_plus_available,
            "method": y_plus.get("method"),
            "direct_y_plus_field": y_plus.get("method") == "openfoam_yPlus_field",
            "acceptable_area_ratio": wall_treatment_ratio,
            "minimum_acceptable_area_ratio": wall_treatment_limit,
        },
        "numerical_sensitivity": sensitivity_validation,
    }
