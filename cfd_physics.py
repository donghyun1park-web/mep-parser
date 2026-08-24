"""P4 isothermal body-fitted RANS case builder and result gate."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import uuid

import cfd_mesh
import cfd_numerics
import cfd_parallel
from cfd_run import run_case
from heat_source_contract import (
    HeatSourceContractError,
    assert_unique_positive_source_ids,
    normalize_confirmed_heat_source,
)


DEFAULT_SETTINGS = {
    "airflow_balance_tolerance": 0.01,
    "kinematic_viscosity_m2_s": 1.5e-5,
    "turbulence_intensity": 0.05,
    "turbulence_length_scale_ratio": 0.07,
    "end_time": 600,
    "write_interval": 100,
    "max_final_residual": 1e-3,
    "max_continuity_global": 1e-6,
    "linear_solver_relative_tolerance": 0.01,
    "target_y_plus_min": 30.0,
    "target_y_plus_max": 300.0,
    "low_y_plus_max": 5.0,
    "minimum_wall_treatment_area_ratio": 0.80,
    "minimum_engineering_iterations": 100,
    "minimum_residual_reduction_orders": 1.5,
    "steady_initial_residual_limits": {
        "Ux": 1e-3, "Uy": 1e-3, "Uz": 1e-3,
        "p": 1e-2, "k": 1e-3, "omega": 1e-3,
    },
    "transient_duration_s": 10.0,
    "transient_initial_delta_t_s": 0.01,
    "transient_max_delta_t_s": 0.2,
    "transient_max_co": 1.0,
    "transient_max_courant_gate": 2.0,
    "transient_write_interval_s": 2.0,
    "transient_stability_relative_span": 0.05,
    "transient_minimum_flow_through_fraction": 0.25,
    "transient_max_single_run_s": 120.0,
    "transient_interactive_runtime_budget_s": 3600.0,
    # Balanced PIMPLE profile for the current detailed mesh quality.
    "transient_numerics_profile": "balanced_fast_v2",
    "transient_outer_correctors": 1,
    "transient_pressure_correctors": 1,
    "transient_non_orthogonal_correctors": 0,
    "supply_temperature_k": 293.15,
    "initial_temperature_k": 293.15,
    "reference_temperature_k": 293.15,
    "thermal_expansion_coefficient_1_k": 0.00341,
    "laminar_prandtl": 0.71,
    "turbulent_prandtl": 0.85,
    "air_density_kg_m3": 1.204,
    "air_specific_heat_j_kg_k": 1006.0,
    "thermal_duration_s": 1.0,
    "thermal_initial_delta_t_s": 0.001,
    "thermal_max_delta_t_s": 0.05,
    "thermal_max_co": 1.0,
    # The solve controller targets Co <= 1.  A looser screening gate keeps
    # an interactive recovery diagnostic visible; design review uses the
    # profile-specific gate recorded by cfd_numerics.
    "thermal_max_courant_gate": 2.0,
    "thermal_design_max_courant_gate": 1.0,
    "thermal_residual_tail_samples": cfd_numerics.DEFAULT_RESIDUAL_TAIL_SAMPLES,
    "thermal_write_interval_s": 0.2,
    "thermal_max_temperature_k": 333.15,
    "thermal_min_temperature_tolerance_k": 0.1,
    "boussinesq_max_temperature_rise_k": 30.0,
    "minimum_energy_closure_ratio": 0.95,
    "maximum_energy_closure_ratio": 1.05,
    "thermal_heat_application": "equipment_wall_adjacent_cells_v1",
    "thermal_preconditioning_iterations": 0,
    "thermal_flow_scale": 1.0,
    "thermal_gravity_scale": 1.0,
    "thermal_heat_scale": 1.0,
    "thermal_flow_ramp_s": 0.1,
    "thermal_outer_correctors": 1,
    "thermal_pressure_correctors": 2,
    "thermal_non_orthogonal_correctors": 0,
    "thermal_scalar_relative_tolerance": 0.1,
    # OpenFOAM 1912 Ubuntu packages can fail inside function-object SHA1
    # bookkeeping before the first time step. Keep this opt-in and diagnose
    # saved fields with foamToVTK instead.
    "thermal_log_field_extrema": False,
    "thermal_minimum_flow_through_fraction": 0.25,
    "thermal_max_single_run_s": 5.0,
    "thermal_interactive_runtime_budget_s": 3600.0,
    # Keep restartable checkpoints reasonably close in wall-clock time. The
    # first continuation uses a conservative projection from the stability
    # run; later chunks use the measured continuation solver rate.
    "thermal_checkpoint_wall_budget_s": 1800.0,
    "thermal_checkpoint_initial_rate_safety_factor": 2.0,
    "thermal_checkpoint_min_duration_s": 0.5,
    "thermal_continuation_profile": "v2606_bounded_fast_v1",
    "thermal_continuation_max_delta_t_s": 0.02,
    # Recovery deliberately keeps only start/middle/end snapshots from each
    # bounded continuation. Writing every 0.1 s created ~200 disposable time
    # directories per 20 s GCI chunk without increasing the retained temporal
    # resolution. Two seconds still yields enough candidates for the same
    # three-point recovery while greatly reducing solver I/O.
    "thermal_continuation_write_interval_s": 2.0,
    "thermal_parallel_min_cells": 80000,
    # Open MPI can be present but unable to launch workers in some WSL builds.
    # Keep parallelism opt-in until a live mpirun smoke has passed.
    "thermal_parallel_processes": 1,
    # Stable first-order flow is useful for recovery/screening. A separate
    # limited-second-order profile is required before numerical design review.
    "thermal_numerics_profile": cfd_numerics.STABILIZED_FIRST_ORDER,
    # Radiation is intentionally disabled for field cases until the enclosure,
    # surface-material, and external-boundary contract has been validated.
    "radiation_modelled": False,
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_THERMAL_NUMERICS_PROVENANCE_CONTRACT = "thermal_numerics_provenance.v1"
_THERMAL_NUMERICS_SYSTEM_FILES = {
    "controlDict": Path("system") / "controlDict",
    "fvSchemes": Path("system") / "fvSchemes",
    "fvSolution": Path("system") / "fvSolution",
}
_THERMAL_PHYSICAL_INPUT_CONTRACT = "thermal_input.physical.v1"
_THERMAL_PHYSICAL_INPUT_REQUIRED_KEYS = (
    "engine",
    "mesh_manifest_sha256",
    "settings",
    "airflow",
    "terminals",
    "wall_patches",
    "heat_sources",
    "heat",
    "assumptions",
    "condition_matrix",
    "initialisation",
)
_THERMAL_PHYSICAL_INITIALISATION_KEYS = (
    "mode",
    "pressure_mapping",
    "boussinesq_preconditioning_iterations",
)


def _canonical_json_sha256(value):
    """Hash structured evidence without depending on file pretty-printing."""
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _json_snapshot_value(value):
    """Return a JSON-only copy so the evidence cannot retain caller aliases."""
    return json.loads(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ))


def _thermal_physical_input_snapshot(contract):
    """Build deterministic, profile-free input evidence for paired sensitivity.

    The normal ``thermal_input.json`` deliberately carries creation time and
    the chosen OpenFOAM numerical profile.  Those are useful for an individual
    run but must not alter the common physical-input fingerprint of a paired
    first-/second-order comparison.  All effective settings are retained
    except ``thermal_numerics_profile``; the runner's separate case-seed
    snapshot binds the profile-specific dictionaries.

    External mapped initial fields are intentionally not represented by their
    source path or source time.  This snapshot preserves their *semantics*
    (mode, pressure mapping, preconditioning) only.  The paired-sensitivity
    runner must therefore accept ``zero_flow`` initialisation until it has a
    content-addressed external-field contract.
    """
    if not isinstance(contract, dict):
        raise ValueError("thermal input contract must be a mapping")
    missing = [
        key for key in _THERMAL_PHYSICAL_INPUT_REQUIRED_KEYS
        if key not in contract
    ]
    if missing:
        raise ValueError(
            "thermal input is missing physical snapshot fields: "
            + ", ".join(missing)
        )
    settings = contract["settings"]
    initialisation = contract["initialisation"]
    if not isinstance(settings, dict) or not isinstance(initialisation, dict):
        raise ValueError("thermal input settings and initialisation must be mappings")
    missing_initialisation = [
        key for key in _THERMAL_PHYSICAL_INITIALISATION_KEYS
        if key not in initialisation
    ]
    if missing_initialisation:
        raise ValueError(
            "thermal input is missing initialisation semantics: "
            + ", ".join(missing_initialisation)
        )

    snapshot = {
        "schema_version": 1,
        "contract": _THERMAL_PHYSICAL_INPUT_CONTRACT,
        "engine": _json_snapshot_value(contract["engine"]),
        "mesh_manifest_sha256": _json_snapshot_value(
            contract["mesh_manifest_sha256"]
        ),
        "settings": _json_snapshot_value({
            key: value for key, value in settings.items()
            if key != "thermal_numerics_profile"
        }),
        "airflow": _json_snapshot_value(contract["airflow"]),
        "terminals": _json_snapshot_value(contract["terminals"]),
        "wall_patches": _json_snapshot_value(contract["wall_patches"]),
        "heat_sources": _json_snapshot_value(contract["heat_sources"]),
        "heat": _json_snapshot_value(contract["heat"]),
        "assumptions": _json_snapshot_value(contract["assumptions"]),
        "condition_matrix": _json_snapshot_value(contract["condition_matrix"]),
        "initialisation": _json_snapshot_value({
            key: initialisation[key]
            for key in _THERMAL_PHYSICAL_INITIALISATION_KEYS
        }),
    }
    snapshot["physical_input_sha256"] = _canonical_json_sha256(snapshot)
    return snapshot


def profile_free_thermal_input_snapshot(contract):
    """Return the public, deterministic physical input snapshot for a case.

    The sensitivity-preparation and later execution verifier use this helper
    to derive the profile-free sidecar from the *actual* thermal input rather
    than trusting a previously written JSON file.  It performs no filesystem
    work and makes no solver call.
    """
    return _thermal_physical_input_snapshot(contract)


def buoyant_initial_seed_expectations(contract):
    """Regenerate every allowed zero-flow initial numerical input.

    This public pure helper is intentionally restricted to fresh initial
    buoyant cases.  It exposes the exact generator output that a serial
    numerical-sensitivity preparation must compare with saved files so a
    stale or arbitrary `system/`/`Allrun` edit cannot be frozen merely because
    it acquired a new seed hash.  Restart/mapped state belongs to a different
    content-addressed workflow and is rejected here.
    """
    if not isinstance(contract, dict):
        raise ValueError("thermal input contract must be a mapping")
    settings = contract.get("settings")
    numerics = contract.get("numerics")
    initialisation = contract.get("initialisation")
    if not isinstance(settings, dict) or not isinstance(numerics, dict):
        raise ValueError("thermal input settings/numerics must be mappings")
    if (not isinstance(initialisation, dict)
            or initialisation.get("mode") != "zero_flow"
            or initialisation.get("source_case") is not None
            or initialisation.get("source_time") is not None
            or initialisation.get("pressure_mapping") is not None):
        raise ValueError("only zero_flow thermal initialisation is supported")
    profile = settings.get("thermal_numerics_profile")
    if (profile not in cfd_numerics.SUPPORTED_PROFILES
            or numerics.get("profile") != profile):
        raise ValueError("thermal numerical profile is invalid")
    processes = settings.get("thermal_parallel_processes")
    if (not isinstance(processes, int) or isinstance(processes, bool)
            or processes != 1):
        raise ValueError("only serial thermal initialisation is supported")
    return {
        "profile": profile,
        "initialisation": "zero_flow",
        "Allrun": _thermal_allrun(settings, map_initial_fields=False),
        "system": {
            "system/controlDict": _thermal_control_dict(settings),
            "system/fvSchemes": _thermal_fv_schemes(numerics),
            "system/fvSolution": _thermal_fv_solution(settings, numerics),
            "system/controlDict.transient": _thermal_control_dict(settings),
            "system/fvSchemes.transient": _thermal_fv_schemes(numerics),
            "system/fvSolution.transient": _thermal_fv_solution(settings, numerics),
            "system/controlDict.precondition": _thermal_precondition_control_dict(
                settings
            ),
            "system/fvSchemes.precondition": _thermal_precondition_fv_schemes(),
            "system/fvSolution.precondition": _thermal_precondition_fv_solution(
                settings
            ),
            "system/topoSetDict": _thermal_toposet_dict(
                contract.get("heat_sources") or []
            ),
        },
    }


def _sha256_if_file(path):
    path = Path(path)
    try:
        return _sha256(path) if path.is_file() else None
    except OSError:
        return None


def _text_sha256(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _expected_thermal_system_hashes(settings, numerics, restart_input_path=None):
    """Hash the dictionaries this numerical profile is required to generate."""
    if not isinstance(settings, dict) or not isinstance(numerics, dict):
        return {name: None for name in _THERMAL_NUMERICS_SYSTEM_FILES}
    try:
        if restart_input_path is None:
            control = _thermal_control_dict(settings)
        else:
            restart = _read_json(restart_input_path)
            control = _thermal_restart_control_dict(
                settings,
                float(restart["start_time_s"]),
                float(restart["duration_s"]),
            )
        return {
            "controlDict": _text_sha256(control),
            "fvSchemes": _text_sha256(_thermal_fv_schemes(numerics)),
            "fvSolution": _text_sha256(_thermal_fv_solution(settings, numerics)),
        }
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return {name: None for name in _THERMAL_NUMERICS_SYSTEM_FILES}


def _thermal_numerics_provenance(case, settings, numerics, *,
                                 restart_input_path=None):
    """Bind the numerical evaluator to the exact files passed to OpenFOAM.

    ``thermal_input.json`` declares the initial settings.  A continuation may
    replace both the PIMPLE controls and the scheme profile through
    ``thermal_restart_input.json``.  The run manifest must retain the source
    contract *and* hashes of the generated OpenFOAM dictionaries so result
    review can fail closed when either one changes after the solve.
    """
    case = Path(case)
    restart_path = (Path(restart_input_path)
                    if restart_input_path is not None else None)
    return {
        "contract": _THERMAL_NUMERICS_PROVENANCE_CONTRACT,
        "source": (
            "thermal_restart_input" if restart_path is not None
            else "thermal_initial_input"
        ),
        "thermal_input_sha256": _sha256_if_file(case / "thermal_input.json"),
        "thermal_restart_input_sha256": (
            _sha256_if_file(restart_path) if restart_path is not None else None
        ),
        "effective_settings_sha256": _canonical_json_sha256(settings),
        "effective_numerics_sha256": _canonical_json_sha256(numerics),
        "expected_system": _expected_thermal_system_hashes(
            settings, numerics, restart_input_path=restart_path
        ),
        "system": {
            name: _sha256_if_file(case / relative_path)
            for name, relative_path in _THERMAL_NUMERICS_SYSTEM_FILES.items()
        },
    }


def _header(class_name, object_name, location):
    return (
        "FoamFile\n{\n    version 2.0;\n    format ascii;\n"
        f"    class {class_name};\n    location \"{location}\";\n"
        f"    object {object_name};\n}}\n\n"
    )


def _publish(staging, target):
    target = Path(target)
    backup = target.with_name(target.name + ".backup." + uuid.uuid4().hex)
    if target.exists():
        os.replace(target, backup)
    try:
        for attempt in range(5):
            try:
                os.replace(staging, target)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except BaseException:
        if backup.exists():
            os.replace(backup, target)
        raise
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)


def _read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _terminal_contract(surface_manifest, mesh_manifest, settings):
    patch_by_region = {item["name"]: item.get("mesh_patch_name")
                       for item in mesh_manifest.get("patches") or []}
    terminals = []
    for region in surface_manifest.get("regions") or []:
        role = region.get("role")
        if role not in ("supply", "exhaust"):
            continue
        airflow = float(region.get("airflow_cmh", 0.0) or 0.0)
        patch = patch_by_region.get(region["name"])
        area = float(region.get("area_m2", 0.0) or 0.0)
        if airflow <= 0 or not patch or area <= 0:
            raise ValueError(f"말단 유량·면적·mesh patch가 불완전합니다: {region['name']}")
        terminals.append({
            "name": region["name"], "role": role, "mesh_patch_name": patch,
            "airflow_cmh": airflow, "flow_rate_m3_s": airflow / 3600.0,
            "area_m2": area, "nominal_velocity_m_s": airflow / 3600.0 / area,
            "design_normal": region.get("design_normal") or [],
        })
    supplies = [item for item in terminals if item["role"] == "supply"]
    exhausts = [item for item in terminals if item["role"] == "exhaust"]
    if not supplies or not exhausts:
        raise ValueError("등온 유동에는 최소 1개의 급기구와 배기구가 필요합니다.")
    supply_total = sum(item["airflow_cmh"] for item in supplies)
    exhaust_total = sum(item["airflow_cmh"] for item in exhausts)
    imbalance = abs(supply_total - exhaust_total) / max(supply_total, exhaust_total)
    if imbalance > float(settings["airflow_balance_tolerance"]):
        raise ValueError(
            f"닫힌 실내의 급배기 설계유량이 불균형합니다: 급기 {supply_total:g} CMH, "
            f"배기 {exhaust_total:g} CMH, 오차 {imbalance * 100:.2f}%"
        )
    return terminals, {
        "supply_cmh": supply_total, "exhaust_cmh": exhaust_total,
        "design_imbalance_ratio": imbalance,
    }


def _wall_patches(surface_manifest, mesh_manifest):
    terminal_names = {region["name"] for region in surface_manifest.get("regions") or []
                      if region.get("role") in ("supply", "exhaust")}
    output = []
    for item in mesh_manifest.get("patches") or []:
        if item.get("name") not in terminal_names and item.get("mesh_patch_name"):
            output.append(item["mesh_patch_name"])
    return list(dict.fromkeys(output))


def _boundary_blocks(terminals, walls, field, settings):
    blocks = []
    for item in terminals:
        patch, role = item["mesh_patch_name"], item["role"]
        q = item["flow_rate_m3_s"]
        speed = item["nominal_velocity_m_s"]
        intensity = float(settings["turbulence_intensity"])
        k_value = max(1e-8, 1.5 * (speed * intensity) ** 2)
        length = max(1e-4, float(settings["turbulence_length_scale_ratio"]) *
                     math.sqrt(item["area_m2"]))
        omega = max(1e-6, math.sqrt(k_value) / (0.09 ** 0.25 * length))
        if field == "U":
            if role == "supply":
                body = ("        type flowRateInletVelocity;\n"
                        f"        volumetricFlowRate constant {q:.12g};\n"
                        "        value uniform (0 0 0);\n")
            else:
                body = ("        type pressureInletOutletVelocity;\n"
                        "        value uniform (0 0 0);\n")
        elif field == "p":
            body = ("        type zeroGradient;\n" if role == "supply" else
                    "        type fixedValue;\n        value uniform 0;\n")
        elif field == "k":
            if role == "supply":
                body = f"        type fixedValue;\n        value uniform {k_value:.12g};\n"
            else:
                body = (f"        type inletOutlet;\n        inletValue uniform {k_value:.12g};\n"
                        f"        value uniform {k_value:.12g};\n")
        elif field == "omega":
            if role == "supply":
                body = f"        type fixedValue;\n        value uniform {omega:.12g};\n"
            else:
                body = (f"        type inletOutlet;\n        inletValue uniform {omega:.12g};\n"
                        f"        value uniform {omega:.12g};\n")
        else:
            body = "        type calculated;\n        value uniform 0;\n"
        blocks.append(f"    {patch}\n    {{\n{body}    }}\n")
    for patch in walls:
        if field == "U":
            body = "        type fixedValue;\n        value uniform (0 0 0);\n"
        elif field == "p":
            body = "        type zeroGradient;\n"
        elif field == "k":
            body = "        type kqRWallFunction;\n        value uniform 1e-10;\n"
        elif field == "omega":
            body = "        type omegaWallFunction;\n        value uniform 1;\n"
        else:
            body = "        type nutkWallFunction;\n        value uniform 0;\n"
        blocks.append(f"    {patch}\n    {{\n{body}    }}\n")
    return "".join(blocks)


def _field(field, terminals, walls, settings):
    if field == "U":
        dimensions, internal, class_name = "[0 1 -1 0 0 0 0]", "uniform (0 0 0)", "volVectorField"
    elif field == "p":
        dimensions, internal, class_name = "[0 2 -2 0 0 0 0]", "uniform 0", "volScalarField"
    elif field == "k":
        dimensions, internal, class_name = "[0 2 -2 0 0 0 0]", "uniform 1e-4", "volScalarField"
    elif field == "omega":
        dimensions, internal, class_name = "[0 0 -1 0 0 0 0]", "uniform 1", "volScalarField"
    else:
        dimensions, internal, class_name = "[0 2 -1 0 0 0 0]", "uniform 0", "volScalarField"
    return (_header(class_name, field, "0") + f"dimensions {dimensions};\n"
            f"internalField {internal};\n\nboundaryField\n{{\n"
            + _boundary_blocks(terminals, walls, field, settings) + "}\n")


def _control_dict(settings):
    return (_header("dictionary", "controlDict", "system") +
            "application simpleFoam;\nstartFrom startTime;\nstartTime 0;\n"
            "stopAt endTime;\n" + f"endTime {int(settings['end_time'])};\n"
            "deltaT 1;\nwriteControl timeStep;\n" +
            f"writeInterval {int(settings['write_interval'])};\n"
            "purgeWrite 0;\nwriteFormat ascii;\nwritePrecision 10;\n"
            "writeCompression off;\ntimeFormat general;\ntimePrecision 6;\n"
            "runTimeModifiable true;\n")


def _fv_schemes():
    return (_header("dictionary", "fvSchemes", "system") +
            "ddtSchemes { default steadyState; }\n"
            "gradSchemes { default cellLimited Gauss linear 1; }\n"
            "divSchemes\n{\n    default none;\n"
            "    div(phi,U) bounded Gauss linearUpwind grad(U);\n"
            "    div(phi,k) bounded Gauss upwind;\n"
            "    div(phi,omega) bounded Gauss upwind;\n"
            "    div((nuEff*dev2(T(grad(U))))) Gauss linear;\n}\n"
            "laplacianSchemes { default Gauss linear limited 0.5; }\n"
            "interpolationSchemes { default linear; }\n"
            "snGradSchemes { default limited 0.5; }\n"
            "wallDist { method meshWave; }\n")


def _fv_solution(settings):
    residual = float(settings["max_final_residual"])
    relative = float(settings["linear_solver_relative_tolerance"])
    return (_header("dictionary", "fvSolution", "system") +
            "solvers\n{\n"
            f"    p {{ solver GAMG; tolerance 1e-8; relTol {relative:.9g}; smoother GaussSeidel; }}\n"
            "    pFinal { $p; relTol 0; }\n"
            f"    U {{ solver smoothSolver; smoother symGaussSeidel; tolerance 1e-8; relTol {relative:.9g}; }}\n"
            "    UFinal { $U; relTol 0; }\n"
            f"    k {{ solver smoothSolver; smoother symGaussSeidel; tolerance 1e-8; relTol {relative:.9g}; }}\n"
            "    kFinal { $k; relTol 0; }\n"
            f"    omega {{ solver smoothSolver; smoother symGaussSeidel; tolerance 1e-8; relTol {relative:.9g}; }}\n"
            "    omegaFinal { $omega; relTol 0; }\n"
            "}\nSIMPLE\n{\n    nNonOrthogonalCorrectors 1;\n    pRefCell 0;\n    pRefValue 0;\n"
            "    residualControl\n    {\n"
            f"        p {residual:.9g};\n        U {residual:.9g};\n"
            f"        k {residual:.9g};\n        omega {residual:.9g};\n    }}\n}}\n"
            "relaxationFactors\n{\n    fields { p 0.3; }\n"
            "    equations { U 0.7; k 0.5; omega 0.5; }\n}\n")


def _allrun():
    return """#!/bin/bash
set -o pipefail
cd "${0%/*}" || exit 20
echo "=== isothermal simpleFoam ==="
simpleFoam > log.simpleFoam 2>&1
rc=$?
grep -E '^Time = |SIMPLE solution converged|FOAM FATAL|End$' log.simpleFoam | tail -20
if [ "$rc" -ne 0 ]; then
    echo "simpleFoam FAILED (exit $rc)"
    tail -80 log.simpleFoam
    exit "$rc"
fi
echo "=== isothermal solve done ==="
"""


def _transient_control_dict(settings, start_time):
    end_time = start_time + float(settings["transient_duration_s"])
    return (_header("dictionary", "controlDict", "system") +
            "application pimpleFoam;\nstartFrom startTime;\n"
            f"startTime {start_time:.12g};\nstopAt endTime;\nendTime {end_time:.12g};\n"
            f"deltaT {float(settings['transient_initial_delta_t_s']):.12g};\n"
            "adjustTimeStep yes;\n"
            f"maxCo {float(settings['transient_max_co']):.12g};\n"
            f"maxDeltaT {float(settings['transient_max_delta_t_s']):.12g};\n"
            "writeControl adjustableRunTime;\n"
            f"writeInterval {float(settings['transient_write_interval_s']):.12g};\n"
            "purgeWrite 0;\nwriteFormat ascii;\nwritePrecision 10;\n"
            "writeCompression off;\ntimeFormat general;\ntimePrecision 8;\n"
            "runTimeModifiable true;\n")


def _transient_fv_schemes():
    return (_header("dictionary", "fvSchemes", "system") +
            "ddtSchemes { default Euler; }\n"
            "gradSchemes { default cellLimited Gauss linear 1; }\n"
            "divSchemes\n{\n    default none;\n"
            "    div(phi,U) bounded Gauss linearUpwind grad(U);\n"
            "    div(phi,k) bounded Gauss upwind;\n"
            "    div(phi,omega) bounded Gauss upwind;\n"
            "    div((nuEff*dev2(T(grad(U))))) Gauss linear;\n}\n"
            "laplacianSchemes { default Gauss linear limited 0.5; }\n"
            "interpolationSchemes { default linear; }\n"
            "snGradSchemes { default limited 0.5; }\n"
            "wallDist { method meshWave; }\n")


def _transient_fv_solution(settings):
    relative = float(settings["linear_solver_relative_tolerance"])
    outer = int(settings["transient_outer_correctors"])
    pressure = int(settings["transient_pressure_correctors"])
    non_orthogonal = int(settings["transient_non_orthogonal_correctors"])
    return (_header("dictionary", "fvSolution", "system") +
            "solvers\n{\n"
            f"    p {{ solver GAMG; tolerance 1e-8; relTol {relative:.9g}; smoother GaussSeidel; }}\n"
            "    pFinal { $p; relTol 0; }\n"
            f"    U {{ solver smoothSolver; smoother symGaussSeidel; tolerance 1e-8; relTol {relative:.9g}; }}\n"
            "    UFinal { $U; relTol 0; }\n"
            f"    k {{ solver smoothSolver; smoother symGaussSeidel; tolerance 1e-8; relTol {relative:.9g}; }}\n"
            "    kFinal { $k; relTol 0; }\n"
            f"    omega {{ solver smoothSolver; smoother symGaussSeidel; tolerance 1e-8; relTol {relative:.9g}; }}\n"
            "    omegaFinal { $omega; relTol 0; }\n"
            "}\nPIMPLE\n{\n    momentumPredictor yes;\n"
            f"    nOuterCorrectors {outer};\n"
            f"    nCorrectors {pressure};\n"
            f"    nNonOrthogonalCorrectors {non_orthogonal};\n}}\n"
            "relaxationFactors\n{\n    fields { p 1; }\n"
            "    equations { U 1; k 1; omega 1; }\n}\n")


def _transient_numerics(settings):
    """Return the auditable subset of settings that controls PIMPLE cost."""
    return {
        "profile": str(settings["transient_numerics_profile"]),
        "momentum_predictor": True,
        "outer_correctors": int(settings["transient_outer_correctors"]),
        "pressure_correctors": int(settings["transient_pressure_correctors"]),
        "non_orthogonal_correctors": int(
            settings["transient_non_orthogonal_correctors"]
        ),
        "max_courant": float(settings["transient_max_co"]),
        "courant_gate": float(settings["transient_max_courant_gate"]),
        "continuity_gate": float(settings["max_continuity_global"]),
    }


def _transient_allrun():
    return """#!/bin/bash
set -o pipefail
cd "${0%/*}" || exit 20
echo "=== isothermal pimpleFoam diagnostic ==="
pimpleFoam > log.pimpleFoam 2>&1
rc=$?
grep -E '^Time = |Courant Number|FOAM FATAL|End$' log.pimpleFoam | tail -30
if [ "$rc" -ne 0 ]; then
    echo "pimpleFoam FAILED (exit $rc)"
    tail -80 log.pimpleFoam
    exit "$rc"
fi
echo "=== transient diagnostic done ==="
"""


def build_isothermal_case(mesh_case_dir, solver_case_dir, settings=None):
    cfg = dict(DEFAULT_SETTINGS, **(settings or {}))
    mesh_case = Path(mesh_case_dir).expanduser().resolve()
    try:
        mesh_manifest = _read_json(mesh_case / "mesh_manifest.json")
        surface_manifest = _read_json(mesh_case / "surface_manifest.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"검증된 메시 manifest를 읽지 못했습니다: {exc}"}
    if mesh_manifest.get("status") != "PASS":
        return {"ok": False, "error": "PASS 상태의 body-fitted 메시만 계산할 수 있습니다."}
    if not (mesh_case / "constant" / "polyMesh").is_dir():
        return {"ok": False, "error": "검증된 constant/polyMesh가 없습니다."}
    try:
        terminals, airflow = _terminal_contract(surface_manifest, mesh_manifest, cfg)
        walls = _wall_patches(surface_manifest, mesh_manifest)
        if not walls:
            raise ValueError("wall patch를 찾지 못했습니다.")
    except (KeyError, TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}

    target = Path(solver_case_dir).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=target.name + ".staging-", dir=target.parent))
    try:
        shutil.copytree(mesh_case / "constant" / "polyMesh", staging / "constant" / "polyMesh")
        for name in ("surface_manifest.json", "mesh_manifest.json", "mesh_input.json"):
            shutil.copy2(mesh_case / name, staging / name)
        for field in ("U", "p", "k", "omega", "nut"):
            _write(staging / "0" / field, _field(field, terminals, walls, cfg))
        _write(staging / "constant" / "transportProperties",
               _header("dictionary", "transportProperties", "constant") +
               f"transportModel Newtonian;\nnu [0 2 -1 0 0 0 0] {float(cfg['kinematic_viscosity_m2_s']):.12g};\n")
        _write(staging / "constant" / "turbulenceProperties",
               _header("dictionary", "turbulenceProperties", "constant") +
               "simulationType RAS;\nRAS\n{\n    RASModel kOmegaSST;\n"
               "    turbulence on;\n    printCoeffs on;\n}\n")
        _write(staging / "system" / "controlDict", _control_dict(cfg))
        _write(staging / "system" / "fvSchemes", _fv_schemes())
        _write(staging / "system" / "fvSolution", _fv_solution(cfg))
        _write(staging / "Allrun", _allrun())
        os.chmod(staging / "Allrun", 0o755)
        contract = {
            "schema_version": 1, "contract": "physics_input.v1",
            "engine": "body_fitted_isothermal_rans", "created_at": _now(),
            "mesh_manifest_sha256": _sha256(staging / "mesh_manifest.json"),
            "settings": cfg, "airflow": airflow, "terminals": terminals,
            "wall_patches": walls,
        }
        _write(staging / "physics_input.json",
               json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        _publish(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"ok": True, "case": str(target), "physics_input": contract}


def _heat_source_contract(surface_manifest, mesh_manifest, settings):
    """Map confirmed heat-source equipment to exposed body-fitted patches."""
    patch_by_region = {
        item["name"]: item.get("mesh_patch_name")
        for item in mesh_manifest.get("patches") or []
    }
    rho_cp = (
        float(settings["air_density_kg_m3"])
        * float(settings["air_specific_heat_j_kg_k"])
    )
    alpha = (
        float(settings["kinematic_viscosity_m2_s"])
        / float(settings["laminar_prandtl"])
    )
    sources = []
    for region in surface_manifest.get("regions") or []:
        if region.get("role") != "heat_source":
            continue
        power_kw = float(region.get("power_kw", 0.0) or 0.0)  # legacy input alias
        fraction = float(region.get("convective_fraction", 0.0) or 0.0)
        area = float(region.get("area_m2", 0.0) or 0.0)
        patch = patch_by_region.get(region["name"])
        source_ids = region.get("source_element_ids") or []
        source_id = str(
            region.get("source_id")
            or (source_ids[0] if source_ids else region.get("name") or "")
        )
        try:
            canonical = normalize_confirmed_heat_source({
                "source_id": source_id,
                "source_label": region.get("source_label") or region.get("name"),
                "source_type": region.get("source_type"),
                "evidence": region.get("evidence"),
                "source_ref": region.get("source_ref"),
                "override_of_dxf": region.get("override_of_dxf"),
                "input_power_w": region.get("input_power_w"),
                "power_kw": region.get("power_kw"),
                "convective_fraction": region.get("convective_fraction"),
                "radiative_fraction": region.get("radiative_fraction"),
            })
        except HeatSourceContractError as exc:
            raise ValueError(
                "발열 장비 열원 계약이 불완전합니다: "
                + str(region.get("name") or "?") + " (" + str(exc) + ")"
            ) from exc
        if canonical["source_type"] != "user_confirmed":
            raise ValueError(
                "발열 장비는 user_confirmed 열원만 body-fitted 해석에 사용할 수 있습니다: "
                + str(region.get("name") or "?")
            )
        for field in (
                "convective_power_w", "radiative_power_w",
                "excluded_radiative_power_w"):
            serialized = region.get(field)
            if serialized is None:
                continue
            if isinstance(serialized, bool):
                raise ValueError(
                    f"{region.get('name') or '?'}: {field} must be a finite W value"
                )
            try:
                serialized_w = float(serialized)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"{region.get('name') or '?'}: {field} must be a finite W value"
                ) from exc
            if not math.isfinite(serialized_w) or not math.isclose(
                    serialized_w, canonical[field], rel_tol=1e-6, abs_tol=1e-3):
                raise ValueError(
                    f"{region.get('name') or '?'}: serialized {field} conflicts "
                    "with the confirmed input power and heat split"
                )
        power_kw = canonical["power_kw"]
        fraction = canonical["convective_fraction"]
        if power_kw <= 0 or not 0 < fraction <= 1 or area <= 0 or not patch:
            raise ValueError(
                "발열 장비의 kW·대류분율·노출면적·mesh patch가 불완전합니다: "
                + str(region.get("name") or "?")
            )
        convective_w = canonical["convective_power_w"]
        radiative_w = canonical["radiative_power_w"]
        heat_flux = convective_w / area
        sources.append({
            "name": region["name"],
            "source_element_ids": source_ids,
            "mesh_patch_name": patch,
            "exposed_area_m2": area,
            **canonical,
            "surface_heat_flux_w_m2": heat_flux,
            "equivalent_temperature_gradient_k_m": heat_flux / (rho_cp * alpha),
        })
    if not sources:
        raise ValueError(
            "확정된 발열 장비가 없습니다. 장비 kW와 대류분율을 먼저 확인하세요."
        )
    try:
        assert_unique_positive_source_ids(sources)
    except HeatSourceContractError as exc:
        raise ValueError("발열 장비 ID 계약이 불완전합니다: " + str(exc)) from exc
    return sources, {
        "input_power_w": sum(item["input_power_w"] for item in sources),
        "applied_convective_power_w": sum(
            item["convective_power_w"] for item in sources
        ),
        "radiative_power_w": sum(item["radiative_power_w"] for item in sources),
        "excluded_radiative_power_w": sum(
            item["excluded_radiative_power_w"] for item in sources
        ),
        "source_count": len(sources),
        "model": str(settings["thermal_heat_application"]),
    }


def _thermal_field(field, terminals, walls, heat_sources, settings):
    heat_by_patch = {item["mesh_patch_name"]: item for item in heat_sources}
    supply_t = float(settings["supply_temperature_k"])
    initial_t = float(settings["initial_temperature_k"])
    if field == "T":
        dimensions, internal = "[0 0 0 1 0 0 0]", f"uniform {initial_t:.12g}"
    elif field in ("p", "p_rgh"):
        dimensions, internal = "[0 2 -2 0 0 0 0]", "uniform 0"
    else:
        dimensions, internal = "[0 2 -1 0 0 0 0]", "uniform 0"
    blocks = []
    for item in terminals:
        patch, role = item["mesh_patch_name"], item["role"]
        if field == "T":
            body = (f"        type fixedValue;\n        value uniform {supply_t:.12g};\n"
                    if role == "supply" else
                    "        type inletOutlet;\n"
                    f"        inletValue uniform {supply_t:.12g};\n"
                    f"        value uniform {supply_t:.12g};\n")
        elif field == "p_rgh":
            body = ("        type fixedFluxPressure;\n        rho rhok;\n"
                    "        value uniform 0;\n"
                    if role == "supply" else
                    "        type prghTotalPressure;\n        rho rhok;\n"
                    "        p0 uniform 0;\n"
                    "        value uniform 0;\n")
        else:
            body = "        type calculated;\n        value uniform 0;\n"
        blocks.append(f"    {patch}\n    {{\n{body}    }}\n")
    for patch in walls:
        if field == "T":
            body = "        type zeroGradient;\n"
        elif field == "p_rgh":
            body = ("        type fixedFluxPressure;\n        rho rhok;\n"
                    "        value uniform 0;\n")
        elif field == "alphat":
            body = ("        type alphatJayatillekeWallFunction;\n"
                    f"        Prt {float(settings['turbulent_prandtl']):.12g};\n"
                    "        value uniform 0;\n")
        else:
            body = "        type calculated;\n        value uniform 0;\n"
        blocks.append(f"    {patch}\n    {{\n{body}    }}\n")
    return (_header("volScalarField", field, "0")
            + f"dimensions {dimensions};\ninternalField {internal};\n\n"
            + "boundaryField\n{\n" + "".join(blocks) + "}\n")


def _replace_internal_scalar_field(target_text, source_path):
    source = Path(source_path).read_text(encoding="utf-8", errors="replace")
    match = re.search(
        r"internalField\s+((?:uniform\s+[^;]+|nonuniform\s+List<scalar>\s+"
        r"\d+\s*\(.*?\))\s*;)",
        source, re.S,
    )
    if not match:
        raise ValueError(f"초기 압력 internalField를 읽지 못했습니다: {source_path}")
    return re.sub(
        r"internalField\s+uniform\s+0\s*;",
        "internalField " + match.group(1),
        target_text,
        count=1,
    )


def _thermal_fixed_velocity_inlets(field_text, terminals, flow_scale=1.0,
                                   ramp_s=0.0):
    """Apply the design flow along the actual snapped inlet-patch normals."""
    output = field_text
    for item in terminals:
        if item["role"] != "supply":
            continue
        flow_rate = float(item["flow_rate_m3_s"]) * float(flow_scale)
        patch = re.escape(item["mesh_patch_name"])
        if float(ramp_s) > 0 and abs(flow_rate) > 0:
            replacement = (
                f"    {item['mesh_patch_name']}\n    {{\n"
                "        type flowRateInletVelocity;\n"
                "        volumetricFlowRate table\n"
                "        (\n"
                "            (0 0)\n"
                f"            ({float(ramp_s):.12g} {flow_rate:.12g})\n"
                "        );\n"
                "        value uniform (0 0 0);\n"
                "    }"
            )
        else:
            replacement = (
                f"    {item['mesh_patch_name']}\n    {{\n"
                "        type flowRateInletVelocity;\n"
                f"        volumetricFlowRate constant {flow_rate:.12g};\n"
                "        value uniform (0 0 0);\n"
                "    }"
            )
        output, count = re.subn(
            rf"\s*{patch}\s*\{{.*?\n\s*\}}",
            "\n" + replacement,
            output,
            count=1,
            flags=re.S,
        )
        if count != 1:
            raise ValueError(f"급기 U 경계조건을 변환하지 못했습니다: {item['mesh_patch_name']}")
    return output


def _thermal_control_dict(settings):
    extrema = ""
    runtime_modifiable = (
        "false"
        if settings.get("thermal_numerics_profile")
        == cfd_numerics.DESIGN_LIMITED_SECOND_ORDER
        else "true"
    )
    if settings.get("thermal_log_field_extrema", False):
        extrema = (
            "functions\n{\n"
            "    velocityExtrema\n    {\n"
            "        type fieldMinMax;\n"
            "        libs (fieldFunctionObjects);\n"
            "        fields (U);\n"
            "        log true;\n"
            "    }\n"
            "    temperatureExtrema\n    {\n"
            "        type fieldMinMax;\n"
            "        libs (fieldFunctionObjects);\n"
            "        fields (T);\n"
            "        log true;\n"
            "    }\n}\n"
        )
    return (_header("dictionary", "controlDict", "system")
            + "application buoyantBoussinesqPimpleFoam;\n"
            "startFrom startTime;\nstartTime 0;\nstopAt endTime;\n"
            f"endTime {float(settings['thermal_duration_s']):.12g};\n"
            f"deltaT {float(settings['thermal_initial_delta_t_s']):.12g};\n"
            "adjustTimeStep yes;\n"
            f"maxCo {float(settings['thermal_max_co']):.12g};\n"
            f"maxDeltaT {float(settings['thermal_max_delta_t_s']):.12g};\n"
            "writeControl adjustableRunTime;\n"
            f"writeInterval {float(settings['thermal_write_interval_s']):.12g};\n"
            "purgeWrite 0;\nwriteFormat ascii;\nwritePrecision 10;\n"
            "writeCompression off;\ntimeFormat general;\ntimePrecision 8;\n"
            f"runTimeModifiable {runtime_modifiable};\n" + extrema)


def _thermal_restart_control_dict(settings, start_time, duration):
    """Build an absolute-time controlDict for a latestTime thermal restart."""
    output = _thermal_control_dict(settings)
    output = output.replace("startFrom startTime;", "startFrom latestTime;", 1)
    output = re.sub(r"\bstartTime\s+0\s*;", f"startTime {start_time:.12g};", output, count=1)
    output = re.sub(
        r"\bendTime\s+[-+0-9.eE]+\s*;",
        f"endTime {start_time + duration:.12g};",
        output,
        count=1,
    )
    return output


def _thermal_fv_schemes(numerics=None):
    profile = (numerics or {}).get("profile", cfd_numerics.STABILIZED_FIRST_ORDER)
    if profile == cfd_numerics.DESIGN_LIMITED_SECOND_ORDER:
        return (_header("dictionary", "fvSchemes", "system")
                + "ddtSchemes { default Euler; }\n"
                "gradSchemes { default cellLimited Gauss linear 1; }\n"
                "divSchemes\n{\n    default none;\n"
                "    div(phi,U) bounded Gauss linearUpwind grad(U);\n"
                "    div(phi,T) bounded Gauss limitedLinear 1;\n"
                "    div(phi,k) bounded Gauss limitedLinear 1;\n"
                "    div(phi,omega) bounded Gauss limitedLinear 1;\n"
                "    div((nuEff*dev2(T(grad(U))))) Gauss linear;\n}\n"
                "laplacianSchemes { default Gauss linear limited 0.5; }\n"
                "interpolationSchemes { default linear; }\n"
                "snGradSchemes { default limited 0.5; }\n"
                "wallDist { method meshWave; }\n"
                "fluxRequired { default no; p_rgh; }\n")
    return (_header("dictionary", "fvSchemes", "system")
            + "ddtSchemes { default Euler; }\n"
            "gradSchemes { default Gauss linear; }\n"
            "divSchemes\n{\n    default none;\n"
            "    div(phi,U) bounded Gauss upwind;\n"
            "    div(phi,T) bounded Gauss upwind;\n"
            "    div(phi,k) bounded Gauss upwind;\n"
            "    div(phi,omega) bounded Gauss upwind;\n"
            "    div((nuEff*dev2(T(grad(U))))) Gauss linear;\n}\n"
            "laplacianSchemes { default Gauss linear uncorrected; }\n"
            "interpolationSchemes { default linear; }\n"
            "snGradSchemes { default uncorrected; }\n"
            "wallDist { method meshWave; }\n"
            "fluxRequired { default no; p_rgh; }\n")


def _thermal_fv_solution(settings, numerics=None):
    relative = float(settings["linear_solver_relative_tolerance"])
    scalar_relative = float(settings["thermal_scalar_relative_tolerance"])
    outer = int(settings["thermal_outer_correctors"])
    pressure = int(settings["thermal_pressure_correctors"])
    required_non_orthogonal = int(
        (numerics or {}).get("required_non_orthogonal_correctors", 0)
    )
    non_orthogonal = max(
        int(settings["thermal_non_orthogonal_correctors"]), required_non_orthogonal
    )
    return (_header("dictionary", "fvSolution", "system")
            + "solvers\n{\n"
            f"    p_rgh {{ solver PCG; preconditioner DIC; tolerance 1e-8; relTol {relative:.9g}; }}\n"
            "    p_rghFinal { $p_rgh; relTol 0; }\n"
            f"    U {{ solver PBiCGStab; preconditioner DILU; tolerance 1e-6; relTol {scalar_relative:.9g}; }}\n"
            "    UFinal { $U; relTol 0; }\n"
            f"    T {{ solver PBiCGStab; preconditioner DILU; tolerance 1e-6; relTol {scalar_relative:.9g}; }}\n"
            "    TFinal { $T; relTol 0; }\n"
            f"    k {{ solver PBiCGStab; preconditioner DILU; tolerance 1e-6; relTol {scalar_relative:.9g}; }}\n"
            "    kFinal { $k; relTol 0; }\n"
            f"    omega {{ solver PBiCGStab; preconditioner DILU; tolerance 1e-6; relTol {scalar_relative:.9g}; }}\n"
            "    omegaFinal { $omega; relTol 0; }\n}\n"
            "PIMPLE\n{\n    momentumPredictor no;\n"
            f"    nOuterCorrectors {outer};\n"
            f"    nCorrectors {pressure};\n"
            f"    nNonOrthogonalCorrectors {non_orthogonal};\n"
            "    pRefCell 0;\n    pRefValue 0;\n}\n"
            "relaxationFactors\n{\n"
            "    equations { U 1; T 1; k 1; omega 1; }\n}\n")


def _thermal_precondition_control_dict(settings):
    iterations = int(settings["thermal_preconditioning_iterations"])
    return (_header("dictionary", "controlDict", "system")
            + "application buoyantBoussinesqSimpleFoam;\n"
            "startFrom startTime;\nstartTime 0;\nstopAt endTime;\n"
            f"endTime {iterations};\ndeltaT 1;\nwriteControl timeStep;\n"
            f"writeInterval {iterations};\n"
            "purgeWrite 0;\nwriteFormat ascii;\nwritePrecision 10;\n"
            "writeCompression off;\ntimeFormat general;\ntimePrecision 8;\n"
            "runTimeModifiable true;\n")


def _thermal_precondition_fv_schemes():
    return (_header("dictionary", "fvSchemes", "system")
            + "ddtSchemes { default steadyState; }\n"
            "gradSchemes { default cellLimited Gauss linear 1; }\n"
            "divSchemes\n{\n    default none;\n"
            "    div(phi,U) bounded Gauss upwind;\n"
            "    div(phi,T) bounded Gauss upwind;\n"
            "    div(phi,k) bounded Gauss upwind;\n"
            "    div(phi,omega) bounded Gauss upwind;\n"
            "    div((nuEff*dev2(T(grad(U))))) Gauss linear;\n}\n"
            "laplacianSchemes { default Gauss linear limited 0.5; }\n"
            "interpolationSchemes { default linear; }\n"
            "snGradSchemes { default limited 0.5; }\n"
            "wallDist { method meshWave; }\n"
            "fluxRequired { default no; p_rgh; }\n")


def _thermal_precondition_fv_solution(settings):
    relative = float(settings["linear_solver_relative_tolerance"])
    return (_header("dictionary", "fvSolution", "system")
            + "solvers\n{\n"
            f"    p_rgh {{ solver PCG; preconditioner DIC; tolerance 1e-8; relTol {relative:.9g}; }}\n"
            "    p_rghFinal { $p_rgh; relTol 0; }\n"
            f"    \"(U|T|k|omega)\" {{ solver smoothSolver; smoother symGaussSeidel; tolerance 1e-8; relTol {relative:.9g}; }}\n"
            "}\nSIMPLE\n{\n    nNonOrthogonalCorrectors 1;\n"
            "    pRefCell 0;\n    pRefValue 0;\n}\n"
            "relaxationFactors\n{\n    fields { p_rgh 0.7; }\n"
            "    equations { U 0.3; T 0.5; k 0.7; omega 0.7; }\n}\n")


def _thermal_allrun(settings, map_initial_fields=False):
    iterations = int(settings["thermal_preconditioning_iterations"])
    mapping = ""
    if map_initial_fields:
        mapping = """echo "=== map quick isothermal fields to target mesh ==="
mapFields initialMappingSource -sourceTime latestTime -consistent \\
    -mapMethod interpolate -noFunctionObjects > log.mapFields 2>&1
rc=$?
if [ "$rc" -ne 0 ]; then
    echo "mapFields initialisation FAILED (exit $rc)"
    tail -100 log.mapFields
    exit "$rc"
fi
"""
    precondition = ""
    if iterations > 0:
        precondition = f"""echo "=== buoyant pressure-flow preconditioning ==="
mv constant/fvOptions constant/fvOptions.heat
cp system/controlDict.precondition system/controlDict
cp system/fvSchemes.precondition system/fvSchemes
cp system/fvSolution.precondition system/fvSolution
buoyantBoussinesqSimpleFoam > log.buoyantBoussinesqSimpleFoam 2>&1
rc=$?
if [ "$rc" -ne 0 ]; then
    echo "buoyantBoussinesqSimpleFoam preconditioning FAILED (exit $rc)"
    tail -100 log.buoyantBoussinesqSimpleFoam
    exit "$rc"
fi
for field in U p p_rgh T k omega nut alphat; do
    [ -f "{iterations}/$field" ] && cp "{iterations}/$field" "0/$field"
done
rm -rf "{iterations}"
mv constant/fvOptions.heat constant/fvOptions
cp system/controlDict.transient system/controlDict
cp system/fvSchemes.transient system/fvSchemes
cp system/fvSolution.transient system/fvSolution
"""
    return f"""#!/bin/bash
set -o pipefail
cd "${{0%/*}}" || exit 20
{mapping}
topoSet > log.topoSet 2>&1
if [ "$?" -ne 0 ]; then
    echo "topoSet FAILED"
    tail -80 log.topoSet
    exit 24
fi
{precondition}
echo "=== buoyant transient diagnostic ==="
buoyantBoussinesqPimpleFoam > log.buoyantBoussinesqPimpleFoam 2>&1
rc=$?
grep -E '^Time = |Courant Number|Min/max T|FOAM FATAL|End$' log.buoyantBoussinesqPimpleFoam | tail -40
if [ "$rc" -ne 0 ]; then
    echo "buoyantBoussinesqPimpleFoam FAILED (exit $rc)"
    tail -100 log.buoyantBoussinesqPimpleFoam
    exit "$rc"
fi
echo "=== calculate wall yPlus evidence ==="
postProcess -func yPlus -latestTime > log.yPlus 2>&1
if [ "$?" -ne 0 ]; then
    echo "yPlus warning: direct wall-treatment evidence unavailable"
    tail -40 log.yPlus
fi
echo "=== export latest thermal VTK ==="
postProcess -func writeCellVolumes -latestTime > log.writeCellVolumes 2>&1
if [ "$?" -ne 0 ]; then
    echo "writeCellVolumes warning: transient energy storage unavailable"
    tail -40 log.writeCellVolumes
fi
rm -rf VTK
foamToVTK -latestTime -ascii > log.foamToVTK 2>&1
if [ "$?" -ne 0 ]; then
    echo "foamToVTK warning: result export failed"
    tail -60 log.foamToVTK
fi
echo "=== buoyant transient done ==="
"""


def _decompose_par_dict(processes):
    return (
        _header("dictionary", "decomposeParDict", "system")
        + f"numberOfSubdomains {int(processes)};\nmethod scotch;\n"
    )


def _thermal_restart_allrun(parallel_processes=1):
    processes = max(1, int(parallel_processes))
    if processes > 1:
        solver = f"""decomposePar -force > log.decomposePar 2>&1
if [ "$?" -ne 0 ]; then
    echo "decomposePar FAILED"
    tail -80 log.decomposePar
    exit 25
fi
mpirun -np {processes} buoyantBoussinesqPimpleFoam -parallel > log.buoyantBoussinesqPimpleFoam 2>&1
rc=$?
if [ "$rc" -ne 0 ]; then
    echo "buoyantBoussinesqPimpleFoam continuation FAILED (exit $rc)"
    tail -100 log.buoyantBoussinesqPimpleFoam
    exit "$rc"
fi
reconstructPar -latestTime > log.reconstructPar 2>&1
reconstruct_rc=$?
if [ "$reconstruct_rc" -ne 0 ]; then
    echo "reconstructPar FAILED"
    tail -80 log.reconstructPar
    exit 26
fi
rm -rf processor*"""
    else:
        solver = """buoyantBoussinesqPimpleFoam > log.buoyantBoussinesqPimpleFoam 2>&1
rc=$?"""
    return f"""#!/bin/bash
set -o pipefail
cd "${{0%/*}}" || exit 20
echo "=== continue buoyant transient ==="
start_time=$(foamListTimes -latestTime 2>/dev/null)
topoSet > log.topoSet 2>&1
if [ "$?" -ne 0 ]; then
    echo "topoSet continuation FAILED"
    tail -80 log.topoSet
    exit 24
fi
{solver}
grep -E '^Time = |Courant Number|FOAM FATAL|End$' log.buoyantBoussinesqPimpleFoam | tail -40
if [ "$rc" -ne 0 ]; then
    echo "buoyantBoussinesqPimpleFoam continuation FAILED (exit $rc)"
    tail -100 log.buoyantBoussinesqPimpleFoam
    exit "$rc"
fi
echo "=== calculate wall yPlus evidence ==="
postProcess -func yPlus -latestTime > log.yPlus 2>&1
if [ "$?" -ne 0 ]; then
    echo "yPlus warning: direct wall-treatment evidence unavailable"
    tail -40 log.yPlus
fi
echo "=== export latest thermal VTK ==="
latest_time=$(foamListTimes -latestTime 2>/dev/null)
if [ -n "$start_time" ] && [ -n "$latest_time" ] && [ -f "$start_time/V" ]; then
    cp "$start_time/V" "$latest_time/V"
else
    postProcess -func writeCellVolumes -latestTime > log.writeCellVolumes 2>&1
    if [ "$?" -ne 0 ]; then
        echo "writeCellVolumes warning: transient energy storage unavailable"
        tail -40 log.writeCellVolumes
    fi
fi
rm -rf VTK
foamToVTK -latestTime -ascii > log.foamToVTK 2>&1
if [ "$?" -ne 0 ]; then
    echo "foamToVTK warning: result export failed"
    tail -60 log.foamToVTK
fi
echo "=== buoyant transient continuation done ==="
"""


def _thermal_toposet_dict(heat_sources):
    actions = []
    for index, item in enumerate(heat_sources):
        cell_set = f"heatCells{index}"
        cell_zone = f"heatZone{index}"
        patch = item["mesh_patch_name"]
        actions.append(
            "    {\n"
            f"        name {cell_set};\n        type cellSet;\n        action new;\n"
            "        source patchToCell;\n"
            f"        sourceInfo {{ name {patch}; }}\n"
            "    }\n"
            "    {\n"
            f"        name {cell_zone};\n        type cellZoneSet;\n        action new;\n"
            "        source setToCellZone;\n"
            f"        sourceInfo {{ set {cell_set}; }}\n"
            "    }\n"
        )
    return (_header("dictionary", "topoSetDict", "system")
            + "actions\n(\n" + "".join(actions) + ");\n")


def _copy_mapping_source(source_case, source_time, target):
    """Bundle a minimal same-geometry source case for OpenFOAM mapFields."""
    source_case = Path(source_case)
    source_time = Path(source_time)
    target = Path(target)
    shutil.copytree(
        source_case / "constant" / "polyMesh",
        target / "constant" / "polyMesh",
    )
    (target / source_time.name).mkdir(parents=True)
    for field in ("U", "p", "k", "omega", "nut"):
        shutil.copy2(source_time / field, target / source_time.name / field)
    pressure = (source_time / "p").read_text(
        encoding="utf-8", errors="replace"
    )
    pressure_rgh, count = re.subn(
        r"(\bobject\s+)p\s*;", r"\1p_rgh;", pressure, count=1
    )
    if count != 1:
        raise ValueError("매핑 원본 p 필드의 object 선언을 읽지 못했습니다.")
    _write(target / source_time.name / "p_rgh", pressure_rgh)
    source_control = source_case / "system" / "controlDict"
    if not source_control.is_file():
        raise ValueError("매핑 원본 등온 케이스의 controlDict가 없습니다.")
    (target / "system").mkdir(parents=True)
    shutil.copy2(source_control, target / "system" / "controlDict")
    source_manifest = source_case / "mesh_manifest.json"
    if source_manifest.is_file():
        shutil.copy2(source_manifest, target / "mesh_manifest.json")


def _thermal_fv_options(heat_sources, settings):
    rho_cp = (
        float(settings["air_density_kg_m3"])
        * float(settings["air_specific_heat_j_kg_k"])
    )
    entries = []
    for index, item in enumerate(heat_sources):
        source = float(item.get(
            "applied_convective_power_w", item["convective_power_w"]
        )) / rho_cp
        entries.append(
            f"heatSource{index}\n{{\n"
            "    type scalarSemiImplicitSource;\n"
            "    volumeMode absolute;\n"
            "    selectionMode cellZone;\n"
            f"    cellZone heatZone{index};\n"
            f"    injectionRateSuSp {{ T ({source:.12g} 0); }}\n"
            "}\n"
        )
    return (_header("dictionary", "fvOptions", "constant") + "".join(entries))


def build_buoyant_case(mesh_case_dir, solver_case_dir, settings=None,
                       initial_case_dir=None):
    """Build the first body-fitted heat and buoyancy transient contract."""
    cfg = dict(DEFAULT_SETTINGS, **(settings or {}))
    if bool(cfg.get("radiation_modelled", False)):
        return {
            "ok": False,
            "error": (
                "복사 모델은 아직 현장 body-fitted 해석에 사용할 수 없습니다. "
                "폐쇄형 viewFactor benchmark와 표면별 재질·열경계 검증을 먼저 완료하세요."
            ),
        }
    mesh_case = Path(mesh_case_dir).expanduser().resolve()
    try:
        mesh_manifest = _read_json(mesh_case / "mesh_manifest.json")
        surface_manifest = _read_json(mesh_case / "surface_manifest.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"검증된 메시 manifest를 읽지 못했습니다: {exc}"}
    if mesh_manifest.get("status") != "PASS":
        return {"ok": False, "error": "PASS 상태의 body-fitted 메시만 계산할 수 있습니다."}
    if not (mesh_case / "constant" / "polyMesh").is_dir():
        return {"ok": False, "error": "검증된 constant/polyMesh가 없습니다."}
    try:
        numerics = cfd_numerics.thermal_numerics_contract(mesh_manifest, cfg)
        terminals, airflow = _terminal_contract(surface_manifest, mesh_manifest, cfg)
        walls = _wall_patches(surface_manifest, mesh_manifest)
        heat_sources, heat = _heat_source_contract(
            surface_manifest, mesh_manifest, cfg
        )
    except (KeyError, TypeError, ValueError, cfd_numerics.NumericalInputError) as exc:
        return {"ok": False, "error": str(exc)}
    if (float(cfg["supply_temperature_k"]) <= 0
            or float(cfg["initial_temperature_k"]) <= 0):
        return {"ok": False, "error": "급기·초기 온도는 0 K보다 커야 합니다."}
    scale_names = ("thermal_flow_scale", "thermal_gravity_scale", "thermal_heat_scale")
    if any(not 0 <= float(cfg[name]) <= 1 for name in scale_names):
        return {"ok": False, "error": "G2 조건 스케일은 0~1 범위여야 합니다."}
    if float(cfg["thermal_flow_ramp_s"]) < 0:
        return {"ok": False, "error": "급기 시작 램프 시간은 0초 이상이어야 합니다."}
    heat_scale = float(cfg["thermal_heat_scale"])
    requested_total = applied_total = deferred_total = 0.0
    for source in heat_sources:
        requested = float(source["convective_power_w"])
        applied = requested * heat_scale
        deferred = requested - applied
        source.update({
            "requested_convective_power_w": requested,
            "applied_convective_power_w": applied,
            "deferred_convective_power_w": deferred,
            "application_scale": heat_scale,
            "applied_surface_heat_flux_w_m2": (
                float(source["surface_heat_flux_w_m2"]) * heat_scale
            ),
        })
        requested_total += requested
        applied_total += applied
        deferred_total += deferred
    heat.update({
        "requested_convective_power_w": requested_total,
        "applied_convective_power_w": applied_total,
        "deferred_convective_power_w": deferred_total,
        "application_scale": heat_scale,
    })
    initial_case = None
    initial_time = None
    same_mesh = False
    if initial_case_dir is not None:
        initial_case = Path(initial_case_dir).expanduser().resolve()
        initial_time = _latest_time(initial_case)
        try:
            same_mesh = (
                _sha256(initial_case / "mesh_manifest.json")
                == _sha256(mesh_case / "mesh_manifest.json")
            )
        except OSError:
            same_mesh = False
        required_fields = ("U", "p", "k", "omega", "nut")
        if (initial_time is None or any(
                not (initial_time / field).is_file() for field in required_fields
        ) or not (initial_case / "constant" / "polyMesh").is_dir()):
            return {
                "ok": False,
                "error": "완료된 등온 U/p/k/omega/nut와 원본 메시가 필요합니다.",
            }

    target = Path(solver_case_dir).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=target.name + ".staging-", dir=target.parent))
    try:
        shutil.copytree(mesh_case / "constant" / "polyMesh", staging / "constant" / "polyMesh")
        for name in ("surface_manifest.json", "mesh_manifest.json", "mesh_input.json"):
            shutil.copy2(mesh_case / name, staging / name)
        for field in ("U", "k", "omega", "nut"):
            if initial_time is not None and same_mesh:
                (staging / "0").mkdir(parents=True, exist_ok=True)
                shutil.copy2(initial_time / field, staging / "0" / field)
            else:
                _write(staging / "0" / field, _field(field, terminals, walls, cfg))
        u_path = staging / "0" / "U"
        if initial_time is None or not same_mesh:
            _write(u_path, _thermal_fixed_velocity_inlets(
                u_path.read_text(encoding="utf-8"), terminals,
                flow_scale=cfg["thermal_flow_scale"],
                ramp_s=(0.0 if initial_time is not None
                        else cfg["thermal_flow_ramp_s"]),
            ))
        for field in ("T", "p_rgh", "p", "alphat"):
            field_text = _thermal_field(field, terminals, walls, heat_sources, cfg)
            if field == "p_rgh" and initial_time is not None and same_mesh:
                field_text = _replace_internal_scalar_field(
                    field_text, initial_time / "p"
                )
            _write(staging / "0" / field, field_text)
        _write(staging / "constant" / "transportProperties",
               _header("dictionary", "transportProperties", "constant")
               + f"transportModel Newtonian;\nnu {float(cfg['kinematic_viscosity_m2_s']):.12g};\n"
               f"beta {float(cfg['thermal_expansion_coefficient_1_k']):.12g};\n"
               f"TRef {float(cfg['reference_temperature_k']):.12g};\n"
               f"Pr {float(cfg['laminar_prandtl']):.12g};\n"
               f"Prt {float(cfg['turbulent_prandtl']):.12g};\n")
        gravity_z = -9.81 * float(cfg["thermal_gravity_scale"])
        if abs(gravity_z) < 1e-15:
            gravity_z = 0.0
        _write(staging / "constant" / "g",
               _header("uniformDimensionedVectorField", "g", "constant")
               + "dimensions [0 1 -2 0 0 0 0];\n"
               + f"value (0 0 {gravity_z:.12g});\n")
        _write(staging / "constant" / "turbulenceProperties",
               _header("dictionary", "turbulenceProperties", "constant")
               + "simulationType RAS;\nRAS\n{\n    RASModel kOmegaSST;\n"
               "    turbulence on;\n    printCoeffs on;\n}\n")
        _write(staging / "constant" / "fvOptions", _thermal_fv_options(
            heat_sources, cfg
        ))
        _write(staging / "system" / "controlDict", _thermal_control_dict(cfg))
        _write(staging / "system" / "fvSchemes", _thermal_fv_schemes(numerics))
        _write(staging / "system" / "fvSolution", _thermal_fv_solution(cfg, numerics))
        shutil.copy2(staging / "system" / "controlDict",
                     staging / "system" / "controlDict.transient")
        shutil.copy2(staging / "system" / "fvSchemes",
                     staging / "system" / "fvSchemes.transient")
        shutil.copy2(staging / "system" / "fvSolution",
                     staging / "system" / "fvSolution.transient")
        _write(staging / "system" / "controlDict.precondition",
               _thermal_precondition_control_dict(cfg))
        _write(staging / "system" / "fvSchemes.precondition",
               _thermal_precondition_fv_schemes())
        _write(staging / "system" / "fvSolution.precondition",
               _thermal_precondition_fv_solution(cfg))
        _write(staging / "system" / "topoSetDict", _thermal_toposet_dict(heat_sources))
        map_initial_fields = initial_time is not None and not same_mesh
        if map_initial_fields:
            _copy_mapping_source(
                initial_case, initial_time, staging / "initialMappingSource"
            )
        _write(staging / "Allrun", _thermal_allrun(
            cfg, map_initial_fields=map_initial_fields
        ))
        os.chmod(staging / "Allrun", 0o755)
        contract = {
            "schema_version": 1, "contract": "thermal_input.v1",
            "engine": "body_fitted_buoyant_urans", "created_at": _now(),
            "mesh_manifest_sha256": _sha256(staging / "mesh_manifest.json"),
            "settings": cfg, "numerics": numerics,
            "airflow": airflow, "terminals": terminals,
            "wall_patches": walls, "heat_sources": heat_sources, "heat": heat,
            "assumptions": {
                "radiation_modelled": False,
                "walls": "adiabatic_screening",
                "density_model": "Boussinesq",
            },
            "initialisation": {
                "mode": (
                    "mapped_isothermal_fields" if map_initial_fields else
                    "completed_isothermal_fields" if initial_time is not None else
                    "zero_flow"
                ),
                "source_case": str(initial_case) if initial_case is not None else None,
                "source_time": float(initial_time.name) if initial_time is not None else None,
                "pressure_mapping": (
                    "mapFields_interpolate_p_and_p_rgh" if map_initial_fields
                    else "simpleFoam_p_internal_to_p_rgh"
                    if initial_time is not None else None
                ),
                "boussinesq_preconditioning_iterations": int(
                    cfg["thermal_preconditioning_iterations"]
                ),
            },
        }
        contract["condition_matrix"] = {
            "flow_scale": float(cfg["thermal_flow_scale"]),
            "gravity_scale": float(cfg["thermal_gravity_scale"]),
            "heat_scale": heat_scale,
        }
        _write(staging / "thermal_input.json",
               json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        physical_input = _thermal_physical_input_snapshot(contract)
        _write(staging / "thermal_input.physical.v1.json",
               json.dumps(
                   physical_input, ensure_ascii=False, indent=2, sort_keys=True
               ) + "\n")
        _publish(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"ok": True, "case": str(target), "thermal_input": contract}


_NUMBER = r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"


def parse_solver_log(text):
    residuals = {}
    histories = {}
    pattern = re.compile(
        r"Solving for (Ux|Uy|Uz|p|k|omega), Initial residual = " + _NUMBER +
        r", Final residual = " + _NUMBER,
    )
    for field, initial, final in pattern.findall(text):
        row = {"initial": float(initial), "final": float(final)}
        residuals[field] = row
        histories.setdefault(field, []).append(row)
    continuity = re.findall(
        r"time step continuity errors\s*:\s*sum local =\s*" + _NUMBER +
        r", global =\s*" + _NUMBER + r", cumulative =\s*" + _NUMBER,
        text, re.I,
    )
    local, global_value, cumulative = ((float(value) for value in continuity[-1])
                                       if continuity else (None, None, None))
    history_summary = {}
    for field, rows in histories.items():
        tail = rows[-min(20, len(rows)):]
        tail_initial = sorted(row["initial"] for row in tail)
        median = tail_initial[len(tail_initial) // 2]
        first = rows[0]["initial"]
        reduction = (math.log10(first / median)
                     if first > 0 and median > 0 and first > median else 0.0)
        history_summary[field] = {
            "samples": len(rows), "first_initial": first,
            "last_initial": rows[-1]["initial"],
            "tail_median_initial": median, "tail_max_initial": max(tail_initial),
            "reduction_orders": reduction,
        }
    return {
        "ended": bool(re.search(r"(?m)^End\s*$", text)),
        "fatal": bool(re.search(
            r"FOAM FATAL|Segmentation fault|Floating point exception\s*\(", text, re.I
        )),
        "converged": "SIMPLE solution converged" in text,
        "iterations": len(re.findall(r"(?m)^Time = ", text)),
        "residuals": residuals,
        "residual_history": history_summary,
        "continuity": {"local": local, "global": global_value, "cumulative": cumulative},
    }


def _latest_time(case):
    values = []
    for path in Path(case).iterdir():
        if path.is_dir():
            try:
                value = float(path.name)
            except ValueError:
                continue
            if value > 0:
                values.append((value, path))
    return max(values, default=(None, None))[1]


def prepare_transient_restart(solver_case_dir, settings=None):
    """Convert a completed steady case into a bounded pimpleFoam diagnostic."""
    case = Path(solver_case_dir).expanduser().resolve()
    try:
        physics_input = _read_json(case / "physics_input.json")
        current_manifest = _read_json(case / "run_manifest.json")
        mesh_manifest = _read_json(case / "mesh_manifest.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"정상상태 결과를 읽지 못했습니다: {exc}"}
    if current_manifest.get("status") == "FAIL":
        return {"ok": False, "error": "실패한 정상상태 결과에서는 시간변동 진단을 시작할 수 없습니다."}
    if ("ITERATION_LIMIT" not in (current_manifest.get("warnings") or [])
            and current_manifest.get("engine") != "body_fitted_isothermal_urans"):
        return {"ok": False, "error": "정상상태 수렴 미달 결과에서만 시간변동 진단을 시작합니다."}
    latest = _latest_time(case)
    if latest is None:
        return {"ok": False, "error": "시간변동 진단을 재시작할 결과 time이 없습니다."}
    cfg = dict(DEFAULT_SETTINGS, **(physics_input.get("settings") or {}))
    cfg.update(settings or {})
    positive = (
        "transient_duration_s", "transient_initial_delta_t_s",
        "transient_max_delta_t_s", "transient_max_co",
        "transient_write_interval_s",
    )
    if any(float(cfg[name]) <= 0 for name in positive):
        return {"ok": False, "error": "시간변동 계산 시간·시간간격·Courant 설정은 0보다 커야 합니다."}
    if (int(cfg["transient_outer_correctors"]) < 1
            or int(cfg["transient_pressure_correctors"]) < 1
            or int(cfg["transient_non_orthogonal_correctors"]) < 0):
        return {"ok": False, "error": "PIMPLE 보정 횟수 설정이 유효하지 않습니다."}
    if float(cfg["transient_duration_s"]) > float(cfg["transient_max_single_run_s"]):
        return {"ok": False, "error": "한 번의 시간변동 계산은 최대 120초 물리시간까지만 허용합니다."}
    supply_m3_s = float((physics_input.get("airflow") or {}).get("supply_cmh", 0.0)) / 3600.0
    volume_m3 = float(mesh_manifest.get("occ_volume_m3", 0.0) or 0.0)
    if supply_m3_s <= 0 or volume_m3 <= 0:
        return {"ok": False, "error": "방 체적 또는 급기유량이 없어 유동 교환시간을 계산하지 못했습니다."}
    start_time = float(latest.name)
    progress_path = case / "transient_progress.json"
    try:
        previous_progress = _read_json(progress_path) if progress_path.is_file() else {}
    except (OSError, ValueError, json.JSONDecodeError):
        previous_progress = {}
    baseline_time = float(previous_progress.get("baseline_time_s", start_time))
    steady_copy = case / "steady_run_manifest.json"
    if not steady_copy.exists():
        shutil.copy2(case / "run_manifest.json", steady_copy)
    steady_time_state = latest / "uniform" / "time"
    if steady_time_state.is_file():
        state_backup = case / "steady_time_state"
        if not state_backup.exists():
            shutil.copy2(steady_time_state, state_backup)
        steady_time_state.unlink()
    _write(case / "system" / "controlDict", _transient_control_dict(cfg, start_time))
    _write(case / "system" / "fvSchemes", _transient_fv_schemes())
    _write(case / "system" / "fvSolution", _transient_fv_solution(cfg))
    _write(case / "Allrun", _transient_allrun())
    os.chmod(case / "Allrun", 0o755)
    contract = {
        "schema_version": 1, "contract": "transient_input.v1",
        "engine": "body_fitted_isothermal_urans", "created_at": _now(),
        "start_time_s": start_time,
        "baseline_time_s": baseline_time,
        "end_time_s": start_time + float(cfg["transient_duration_s"]),
        "flow_through_time_s": volume_m3 / supply_m3_s,
        "minimum_required_duration_s": (
            volume_m3 / supply_m3_s
            * float(cfg["transient_minimum_flow_through_fraction"])
        ),
        "numerics": _transient_numerics(cfg),
        "settings": cfg,
        "physics_input_sha256": _sha256(case / "physics_input.json"),
        "steady_run_manifest_sha256": _sha256(steady_copy),
        "wall_patches": physics_input["wall_patches"],
        "airflow": physics_input["airflow"],
        "terminals": physics_input["terminals"],
    }
    _write(case / "transient_input.json",
           json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {"ok": True, "case": str(case), "transient_input": contract}


def _boundary_scalar_values(path):
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    values = {}
    for name, body in re.findall(r"([A-Za-z0-9_.:-]+)\s*\{([^{}]*)\}", text, re.S):
        match = re.search(
            r"value\s+nonuniform\s+List<scalar>\s+(\d+)\s*\((.*?)\)\s*;",
            body, re.S,
        )
        if match:
            data = [float(value) for value in re.findall(_NUMBER, match.group(2))]
            if len(data) == int(match.group(1)):
                values[name] = data
    return values


def terminal_flux_balance(time_dir, terminals):
    """Measure total terminal flow and backflow from solved boundary ``phi``.

    For an incompressible Boussinesq solve, the signed volumetric-flux balance
    is the relevant mass-conservation evidence.  It must not be replaced by
    design CMH when a result is promoted for numerical review.
    """
    try:
        boundary = _boundary_scalar_values(Path(time_dir) / "phi")
    except OSError:
        return {
            "available": False,
            "reason": "PHI_FIELD_MISSING",
            "rows": [],
            "inflow_m3_s": None,
            "outflow_m3_s": None,
            "imbalance_ratio": None,
        }
    rows = []
    inflow = outflow = 0.0
    supply_backflow = exhaust_backflow = 0.0
    missing = []
    for item in terminals or []:
        patch = str(item.get("mesh_patch_name") or "")
        role = str(item.get("role") or "")
        values = boundary.get(patch)
        if not patch or not isinstance(values, list):
            missing.append(patch or "?")
            continue
        patch_inflow = sum(-value for value in values if value < 0)
        patch_outflow = sum(value for value in values if value > 0)
        inflow += patch_inflow
        outflow += patch_outflow
        if role == "supply":
            supply_backflow += patch_outflow
        elif role == "exhaust":
            exhaust_backflow += patch_inflow
        rows.append({
            "mesh_patch_name": patch,
            "role": role,
            "net_phi_m3_s": sum(values),
            "inflow_m3_s": patch_inflow,
            "outflow_m3_s": patch_outflow,
            "face_count": len(values),
        })
    available = bool(rows) and not missing
    denominator = max(inflow, outflow)
    imbalance = abs(inflow - outflow) / denominator if denominator > 0 else None
    return {
        "available": available,
        "reason": None if available else "TERMINAL_PHI_MISSING",
        "missing_patches": missing,
        "rows": rows,
        "inflow_m3_s": inflow if available else None,
        "outflow_m3_s": outflow if available else None,
        "imbalance_ratio": imbalance if available else None,
        "supply_backflow_m3_s": supply_backflow if available else None,
        "exhaust_backflow_m3_s": exhaust_backflow if available else None,
        "method": "solved_boundary_phi_signed_volumetric_flux",
    }


def _internal_vector_values(path):
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    match = re.search(
        r"internalField\s+nonuniform\s+List<vector>\s+(\d+)\s*\((.*?)\)\s*;",
        text, re.S,
    )
    if not match:
        return []
    number = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
    values = [tuple(float(value) for value in row) for row in re.findall(
        rf"\(\s*({number})\s+({number})\s+({number})\s*\)", match.group(2)
    )]
    return values if len(values) == int(match.group(1)) else []


def parse_transient_log(text):
    parsed = parse_solver_log(text)
    courant = re.findall(
        r"Courant Number mean:\s*" + _NUMBER + r"\s+max:\s*" + _NUMBER,
        text, re.I,
    )
    courant_rows = [
        {"mean": float(mean), "maximum": float(maximum)}
        for mean, maximum in courant
    ]
    parsed["courant"] = {
        "mean": courant_rows[-1]["mean"] if courant_rows else None,
        "maximum": courant_rows[-1]["maximum"] if courant_rows else None,
        "peak_maximum": max(
            (row["maximum"] for row in courant_rows), default=None
        ),
        "samples": courant_rows,
    }
    times = [float(value) for value in re.findall(r"(?m)^Time =\s*" + _NUMBER, text)]
    parsed["start_time"] = times[0] if times else None
    parsed["end_time"] = times[-1] if times else None
    execution = re.findall(
        r"ExecutionTime\s*=\s*" + _NUMBER +
        r"\s*s\s+ClockTime\s*=\s*" + _NUMBER + r"\s*s",
        text, re.I,
    )
    parsed["execution"] = {
        "cpu_seconds": float(execution[-1][0]) if execution else None,
        "clock_seconds": float(execution[-1][1]) if execution else None,
    }
    return parsed


def parse_thermal_log(text):
    parsed = parse_transient_log(text)
    histories = {}
    pattern = re.compile(
        r"Solving for (Ux|Uy|Uz|p_rgh|T|k|omega), Initial residual = "
        + _NUMBER + r", Final residual = " + _NUMBER,
    )
    for field, initial, final in pattern.findall(text):
        histories.setdefault(field, []).append({
            "initial": float(initial), "final": float(final),
        })
    parsed["thermal_residuals"] = {
        field: rows[-1] for field, rows in histories.items()
    }
    # Keep a bounded tail as numerical-quality evidence.  The final residual
    # alone can look recovered after an earlier unstable solve; this list is
    # intentionally solver-call based (rather than claiming time-step values)
    # because each PIMPLE step can contain multiple linear solves.
    parsed["thermal_residual_history"] = {
        field: rows[-20:] for field, rows in histories.items()
    }
    parsed["field_extrema"] = _parse_thermal_field_extrema(text)
    return parsed


def _parse_thermal_field_extrema(text):
    number = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
    pattern = re.compile(
        rf"(?P<kind>min|max)\((?:mag\(\s*(?P<vector>U)\s*\)|"
        rf"(?P<scalar>U|T))\)\s*=\s*"
        rf"(?P<value>{number}).*?in cell\s+(?P<cell>\d+)\s+at location\s+"
        rf"\(\s*(?P<x>{number})\s+(?P<y>{number})\s+(?P<z>{number})\s*\)",
        re.I,
    )
    rows = []
    for match in pattern.finditer(text):
        prefix = text[:match.start()]
        times = re.findall(r"(?m)^Time =\s*" + _NUMBER, prefix)
        field = match.group("vector") or match.group("scalar")
        rows.append({
            "field": field,
            "kind": match.group("kind").lower(),
            "value": float(match.group("value")),
            "cell": int(match.group("cell")),
            "location_m": [
                float(match.group("x")),
                float(match.group("y")),
                float(match.group("z")),
            ],
            "time_s": float(times[-1]) if times else None,
        })
    output = {"available": bool(rows), "samples": len(rows)}
    for field, label in (("U", "velocity"), ("T", "temperature")):
        field_rows = [row for row in rows if row["field"] == field]
        minima = [row for row in field_rows if row["kind"] == "min"]
        maxima = [row for row in field_rows if row["kind"] == "max"]
        output[label] = {
            "minimum": min(minima, key=lambda row: row["value"]) if minima else None,
            "maximum": max(maxima, key=lambda row: row["value"]) if maxima else None,
            "latest_minimum": minima[-1] if minima else None,
            "latest_maximum": maxima[-1] if maxima else None,
        }
    return output


def _internal_scalar_values(path):
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    uniform = re.search(r"internalField\s+uniform\s+(" + _NUMBER[1:-1] + r")\s*;", text)
    if uniform:
        return [float(uniform.group(1))]
    match = re.search(
        r"internalField\s+nonuniform\s+List<scalar>\s+(\d+)\s*\((.*?)\)\s*;",
        text, re.S,
    )
    if not match:
        return []
    values = [float(value) for value in re.findall(_NUMBER, match.group(2))]
    return values if len(values) == int(match.group(1)) else []


def _foam_label_list(path):
    """Read an ASCII OpenFOAM labelList such as constant/polyMesh/owner."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//.*", "", text)
    match = re.search(r"\n\s*(\d+)\s*\n\s*\((.*?)\)\s*\Z", text, re.S)
    if not match:
        return []
    values = [int(value) for value in re.findall(r"\b\d+\b", match.group(2))]
    return values if len(values) == int(match.group(1)) else []


def _exhaust_flux_temperature(case, latest, patch_name, internal_t):
    """Return the positive-outflow, phi-weighted owner-cell temperature.

    OpenFOAM's inletOutlet patch stores ``inletValue`` in the boundary T field.
    On an outflow this value is not the fluid temperature, so energy accounting
    must use the adjacent internal cells and the solved face flux instead.
    """
    poly_mesh = Path(case) / "constant" / "polyMesh"
    try:
        _points, _faces, patches = cfd_mesh._read_poly_mesh(poly_mesh)
    except OSError:
        return None
    info = patches.get(patch_name)
    owners = _foam_label_list(poly_mesh / "owner")
    try:
        phi = _boundary_scalar_values(Path(latest) / "phi").get(patch_name)
    except OSError:
        return None
    if (not info or not owners or phi is None
            or len(phi) != info["nFaces"]):
        return None
    start = info["startFace"]
    face_owners = owners[start:start + info["nFaces"]]
    if len(face_owners) != len(phi):
        return None
    weighted, outflow = 0.0, 0.0
    for flux, owner in zip(phi, face_owners):
        if flux <= 0 or owner < 0 or owner >= len(internal_t):
            continue
        weighted += flux * internal_t[owner]
        outflow += flux
    if outflow <= 0:
        return None
    return {
        "temperature_k": weighted / outflow,
        "flow_rate_m3_s": outflow,
        "method": "positive_phi_weighted_owner_cell_temperature",
    }


def _solved_exhaust_power(case, time_dir, thermal_input):
    """Return solver-flux exhaust sensible power for one saved time."""
    try:
        internal_t = _internal_scalar_values(Path(time_dir) / "T")
    except OSError:
        return None
    if not internal_t:
        return None
    settings = thermal_input["settings"]
    supply_t = float(settings["supply_temperature_k"])
    rho_cp = (
        float(settings["air_density_kg_m3"])
        * float(settings["air_specific_heat_j_kg_k"])
    )
    total, found = 0.0, False
    for item in thermal_input["terminals"]:
        if item["role"] != "exhaust":
            continue
        solved = _exhaust_flux_temperature(
            case, time_dir, item["mesh_patch_name"], internal_t,
        )
        if solved is None:
            return None
        found = True
        total += (rho_cp * solved["flow_rate_m3_s"]
                  * (solved["temperature_k"] - supply_t))
    return total if found else None


def _stored_sensible_energy(time_dir, internal_t, settings):
    """Calculate rho*cp*integral(V*(T-Tinitial)) from OpenFOAM cell volumes."""
    try:
        volumes = _internal_scalar_values(Path(time_dir) / "V")
    except OSError:
        return None
    if len(volumes) != len(internal_t):
        return None
    rho_cp = (
        float(settings["air_density_kg_m3"])
        * float(settings["air_specific_heat_j_kg_k"])
    )
    initial_t = float(settings["initial_temperature_k"])
    return {
        "stored_sensible_energy_j": rho_cp * sum(
            volume * (temperature - initial_t)
            for volume, temperature in zip(volumes, internal_t)
        ),
        "cell_volume_sum_m3": sum(volumes),
        "reference_temperature_k": initial_t,
        "method": "openfoam_cell_volume_temperature_integral",
    }


def thermal_result_metrics(case, thermal_input):
    latest = _latest_time(Path(case))
    if latest is None or not (latest / "T").is_file():
        return {
            "available": False, "latest_time_s": None, "minimum_k": None,
            "maximum_k": None, "mean_k": None, "temperature_rise_k": None,
            "energy_closure_ratio": None, "exhaust_sensible_power_w": None,
        }
    values = _internal_scalar_values(latest / "T")
    boundary = _boundary_scalar_values(latest / "T")
    if not values:
        return {
            "available": False, "latest_time_s": float(latest.name),
            "minimum_k": None, "maximum_k": None, "mean_k": None,
            "temperature_rise_k": None, "energy_closure_ratio": None,
            "exhaust_sensible_power_w": None,
        }
    settings = thermal_input["settings"]
    supply_t = float(settings["supply_temperature_k"])
    rho_cp = (
        float(settings["air_density_kg_m3"])
        * float(settings["air_specific_heat_j_kg_k"])
    )
    exhaust_rows, sensible = [], 0.0
    for item in thermal_input["terminals"]:
        if item["role"] != "exhaust":
            continue
        solved = _exhaust_flux_temperature(
            case, latest, item["mesh_patch_name"], values,
        )
        patch_values = boundary.get(item["mesh_patch_name"]) or []
        outlet_t = (solved["temperature_k"] if solved else
                    sum(patch_values) / len(patch_values) if patch_values else None)
        flow_rate = (solved["flow_rate_m3_s"] if solved else
                     float(item["flow_rate_m3_s"]))
        method = (solved["method"] if solved else
                  "design_flow_and_saved_boundary_temperature_fallback")
        power = (rho_cp * flow_rate * (outlet_t - supply_t)
                 if outlet_t is not None else None)
        if power is not None:
            sensible += power
        exhaust_rows.append({
            "mesh_patch_name": item["mesh_patch_name"],
            "temperature_k": outlet_t,
            "design_flow_rate_m3_s": item["flow_rate_m3_s"],
            "solved_outflow_rate_m3_s": solved["flow_rate_m3_s"] if solved else None,
            "temperature_method": method,
            "sensible_power_w": power,
        })
    applied = float(thermal_input["heat"]["applied_convective_power_w"])
    closure = sensible / applied if applied > 0 and all(
        row["temperature_k"] is not None for row in exhaust_rows
    ) else None
    storage = _stored_sensible_energy(latest, values, settings)
    return {
        "available": True, "latest_time_s": float(latest.name),
        "minimum_k": min(values), "maximum_k": max(values),
        "mean_k": sum(values) / len(values),
        "temperature_rise_k": max(values) - supply_t,
        "exhaust_sensible_power_w": sensible if closure is not None else None,
        "energy_closure_ratio": closure,
        "exhaust_heat_recovery_ratio": closure,
        "energy_closure_interpretation": (
            "steady_state_exhaust_balance; transient_room_heat_storage_not_included"
        ),
        "room_heat_storage": storage,
        "energy_closure_basis": (
            "solver_positive_phi_and_owner_cell_temperature"
            if exhaust_rows and all(row["solved_outflow_rate_m3_s"] is not None
                                    for row in exhaust_rows)
            else "design_flow_and_saved_boundary_temperature_fallback"
        ),
        "exhausts": exhaust_rows,
    }


def evaluate_buoyant_run(case, run_return, thermal_input, *,
                         effective_settings=None, effective_numerics=None,
                         restart_input_path=None, numerical_provenance=None):
    log_path = Path(case) / "log.buoyantBoussinesqPimpleFoam"
    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    solver = parse_thermal_log(text)
    thermal = thermal_result_metrics(case, thermal_input)
    settings = dict(
        effective_settings
        if isinstance(effective_settings, dict)
        else thermal_input["settings"]
    )
    y_plus = y_plus_metrics(
        case, thermal_input["wall_patches"], float(settings["target_y_plus_min"]),
        float(settings["target_y_plus_max"]),
        float(settings["kinematic_viscosity_m2_s"]),
        float(settings["low_y_plus_max"]),
    )
    latest = _latest_time(case)
    flux_balance = terminal_flux_balance(
        latest, thermal_input.get("terminals") or []
    ) if latest is not None else {
        "available": False,
        "reason": "LATEST_TIME_MISSING",
        "rows": [],
        "inflow_m3_s": None,
        "outflow_m3_s": None,
        "imbalance_ratio": None,
    }
    numerics = effective_numerics if isinstance(effective_numerics, dict) else (
        thermal_input.get("numerics")
    )
    if not isinstance(numerics, dict):
        try:
            numerics = cfd_numerics.thermal_numerics_contract(
                _read_json(Path(case) / "mesh_manifest.json"), settings
            )
        except (OSError, ValueError, json.JSONDecodeError,
                cfd_numerics.NumericalInputError):
            numerics = cfd_numerics.thermal_numerics_contract({}, settings)
    sensitivity_path = Path(case) / "numerical_sensitivity.json"
    try:
        sensitivity = (_read_json(sensitivity_path)
                       if sensitivity_path.is_file() else None)
    except (OSError, ValueError, json.JSONDecodeError):
        sensitivity = None
    numerical_quality = cfd_numerics.evaluate_thermal_numerics(
        numerics, solver, thermal, flux_balance, settings, sensitivity, y_plus
    )
    errors, warnings = [], []
    condition_matrix = thermal_input.get("condition_matrix") or {}
    condition_scales = {
        "flow_scale": condition_matrix.get(
            "flow_scale", settings.get("thermal_flow_scale", 1.0)
        ),
        "gravity_scale": condition_matrix.get(
            "gravity_scale", settings.get("thermal_gravity_scale", 1.0)
        ),
        "heat_scale": condition_matrix.get(
            "heat_scale", settings.get("thermal_heat_scale", 1.0)
        ),
    }
    try:
        if any(abs(float(value) - 1.0) > 1e-12
               for value in condition_scales.values()):
            warnings.append("CONDITION_MATRIX_NOT_FULL")
    except (TypeError, ValueError):
        warnings.append("CONDITION_MATRIX_INVALID")
    if not run_return.get("ok") or not solver["ended"] or solver["fatal"]:
        if ((Path(case) / "log.buoyantBoussinesqSimpleFoam").is_file()
                and not log_path.is_file()):
            errors.append("THERMAL_PRECONDITION_FAILED")
        else:
            errors.append("THERMAL_SOLVER_FAILED")
    courant = solver.get("courant", {}).get("peak_maximum")
    if courant is None or courant > float(settings["thermal_max_courant_gate"]):
        errors.append("COURANT_LIMIT")
    if not thermal["available"]:
        errors.append("TEMPERATURE_FIELD_MISSING")
    else:
        if thermal["maximum_k"] > float(settings["thermal_max_temperature_k"]):
            errors.append("TEMPERATURE_LIMIT")
        minimum_expected = min(
            float(settings["initial_temperature_k"]),
            float(settings["supply_temperature_k"]),
        )
        if (thermal["minimum_k"] < minimum_expected
                - float(settings["thermal_min_temperature_tolerance_k"])):
            warnings.append("TEMPERATURE_UNDERSHOOT")
        if (thermal["temperature_rise_k"] >
                float(settings["boussinesq_max_temperature_rise_k"])):
            errors.append("BOUSSINESQ_RANGE")
        closure = thermal["energy_closure_ratio"]
        if closure is None:
            warnings.append("ENERGY_CLOSURE_MISSING")
        elif not (float(settings["minimum_energy_closure_ratio"]) <= closure <=
                   float(settings["maximum_energy_closure_ratio"])):
            warnings.append("ENERGY_CLOSURE_PENDING")
    if numerical_quality["status"] == "SCREENING_ONLY":
        warnings.append("NUMERICS_SCREENING_ONLY")
    elif numerical_quality["status"] != "PASS":
        warnings.extend(numerical_quality["blockers"])
    errors = list(dict.fromkeys(errors))
    warnings = list(dict.fromkeys(warnings))
    return {
        "schema_version": 1, "contract": "run_manifest.v1",
        "engine": "body_fitted_buoyant_urans", "mode": "thermal_transient",
        "created_at": _now(),
        "status": "FAIL" if errors else "WARN" if warnings else "PASS",
        "design_ready": not errors and not warnings,
        "errors": errors, "warnings": warnings,
        "solver": solver, "thermal": thermal,
        "numerical_quality": numerical_quality,
        "effective_settings": settings,
        "effective_numerics": numerics,
        "y_plus": y_plus,
        "airflow": thermal_input["airflow"],
        "terminals": thermal_input["terminals"],
        "heat_sources": thermal_input["heat_sources"],
        "heat": thermal_input["heat"],
        "assumptions": thermal_input["assumptions"],
        "input": {
            "thermal_input_sha256": _sha256_if_file(
                Path(case) / "thermal_input.json"
            ),
            "numerical_provenance": (
                dict(numerical_provenance)
                if isinstance(numerical_provenance, dict)
                else _thermal_numerics_provenance(
                    case, settings, numerics,
                    restart_input_path=restart_input_path,
                )
            ),
        },
    }


def transient_window_metrics(case, start_time, flow_through_time):
    snapshots = []
    for path in Path(case).iterdir():
        if not path.is_dir():
            continue
        try:
            value = float(path.name)
        except ValueError:
            continue
        if value <= float(start_time) or not (path / "U").is_file():
            continue
        vectors = _internal_vector_values(path / "U")
        if not vectors:
            continue
        speeds = [math.sqrt(x * x + y * y + z * z) for x, y, z in vectors]
        snapshots.append({
            "time_s": value, "cells": len(speeds),
            "mean_speed_m_s": sum(speeds) / len(speeds),
            "rms_speed_m_s": math.sqrt(sum(speed * speed for speed in speeds) / len(speeds)),
        })
    snapshots.sort(key=lambda item: item["time_s"])
    if len(snapshots) < 3:
        return {"available": False, "snapshots": snapshots, "sampled_duration_s": 0.0,
                "flow_through_fraction": 0.0, "mean_speed_relative_span": None,
                "rms_speed_relative_span": None}
    duration = snapshots[-1]["time_s"] - snapshots[0]["time_s"]
    def relative_span(key):
        values = [item[key] for item in snapshots]
        return (max(values) - min(values)) / max(sum(values) / len(values), 1e-12)
    return {
        "available": True, "snapshots": snapshots,
        "sampled_duration_s": duration,
        "flow_through_fraction": duration / flow_through_time if flow_through_time > 0 else 0.0,
        "mean_speed_relative_span": relative_span("mean_speed_m_s"),
        "rms_speed_relative_span": relative_span("rms_speed_m_s"),
    }


def _y_plus_from_nut(nut, nu, kappa=0.41, e_constant=9.8):
    """Invert the nutkWallFunction log-law relation for solver-written wall nut."""
    if nut <= 0 or nu <= 0:
        return 0.0
    target = nut / nu + 1.0
    low, high = 11.0, 1.0e7
    for _ in range(80):
        middle = 0.5 * (low + high)
        value = middle * kappa / math.log(max(e_constant * middle, 1.000001))
        if value < target:
            low = middle
        else:
            high = middle
    return 0.5 * (low + high)


def y_plus_metrics(case, wall_patches, target_min, target_max, nu=None,
                   low_y_plus_max=5.0):
    latest = _latest_time(case)
    if latest is None:
        return {"available": False, "time": None, "area_ratio_in_target": None,
                "wall_treatment_acceptable_area_ratio": None,
                "minimum": None, "maximum": None, "area_weighted_average": None,
                "method": None, "patches": []}
    if (latest / "yPlus").is_file():
        field = _boundary_scalar_values(latest / "yPlus")
        raw_nut_field = None
        method = "openfoam_yPlus_field"
    elif (latest / "nut").is_file():
        nut_field = _boundary_scalar_values(latest / "nut")
        nu = float(nu or DEFAULT_SETTINGS["kinematic_viscosity_m2_s"])
        field = {name: [_y_plus_from_nut(value, nu) for value in values]
                 for name, values in nut_field.items()}
        raw_nut_field = nut_field
        method = "nutkWallFunction_log_law_inverse"
    else:
        return {"available": False, "time": float(latest.name),
                "area_ratio_in_target": None, "minimum": None, "maximum": None,
                "wall_treatment_acceptable_area_ratio": None,
                "area_weighted_average": None, "method": None, "patches": []}
    points, faces, patches = cfd_mesh._read_poly_mesh(Path(case) / "constant" / "polyMesh")
    rows, total_area, target_area, weighted = [], 0.0, 0.0, 0.0
    viscous_area = buffer_area = high_area = 0.0
    all_values = []
    for patch_name in wall_patches:
        info = patches.get(patch_name)
        values = field.get(patch_name)
        if not info or values is None or len(values) != info["nFaces"]:
            continue
        patch_area = patch_target = patch_weighted = 0.0
        raw_nut = raw_nut_field.get(patch_name) if raw_nut_field else None
        patch_viscous = patch_buffer = patch_high = 0.0
        for index, (face, value) in enumerate(zip(
                faces[info["startFace"]:info["startFace"] + info["nFaces"]], values)):
            area = cfd_mesh._polygon_area([points[index] for index in face])
            patch_area += area
            patch_weighted += area * value
            is_viscous = ((raw_nut is not None and raw_nut[index] <= 1e-14)
                          or (raw_nut is None and value <= low_y_plus_max))
            if is_viscous:
                patch_viscous += area
            elif target_min <= value <= target_max:
                patch_target += area
            elif value < target_min:
                patch_buffer += area
            else:
                patch_high += area
            all_values.append(value)
        total_area += patch_area
        target_area += patch_target
        viscous_area += patch_viscous
        buffer_area += patch_buffer
        high_area += patch_high
        weighted += patch_weighted
        rows.append({
            "mesh_patch_name": patch_name, "area_m2": patch_area,
            "area_ratio_in_target": patch_target / patch_area if patch_area else None,
            "viscous_branch_area_ratio": patch_viscous / patch_area if patch_area else None,
            "wall_treatment_acceptable_area_ratio": (
                (patch_viscous + patch_target) / patch_area if patch_area else None
            ),
            "minimum": min(values) if values else None, "maximum": max(values) if values else None,
            "area_weighted_average": patch_weighted / patch_area if patch_area else None,
        })
    return {
        "available": bool(rows), "time": float(latest.name), "method": method,
        "target_min": target_min, "target_max": target_max,
        "area_ratio_in_target": target_area / total_area if total_area else None,
        "viscous_branch_area_ratio": viscous_area / total_area if total_area else None,
        "buffer_layer_area_ratio": buffer_area / total_area if total_area else None,
        "above_target_area_ratio": high_area / total_area if total_area else None,
        "wall_treatment_acceptable_area_ratio": (
            (viscous_area + target_area) / total_area if total_area else None
        ),
        "low_y_plus_max": low_y_plus_max,
        "minimum": min(all_values) if all_values else None,
        "maximum": max(all_values) if all_values else None,
        "area_weighted_average": weighted / total_area if total_area else None,
        "patches": rows,
    }


def evaluate_run(case, run_return, physics_input):
    settings = physics_input["settings"]
    log_path = Path(case) / "log.simpleFoam"
    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    solver = parse_solver_log(text)
    y_plus = y_plus_metrics(
        case, physics_input["wall_patches"], float(settings["target_y_plus_min"]),
        float(settings["target_y_plus_max"]), float(settings["kinematic_viscosity_m2_s"]),
        float(settings["low_y_plus_max"]),
    )
    errors, warnings = [], []
    if not run_return.get("ok") or not solver["ended"] or solver["fatal"]:
        errors.append("SOLVER_FAILED")
    required = ("Ux", "Uy", "Uz", "p", "k", "omega")
    if any(field not in solver["residuals"] for field in required):
        errors.append("RESIDUALS_MISSING")
    else:
        limit = float(settings["max_final_residual"])
        if max(solver["residuals"][field]["final"] for field in required) > limit:
            errors.append("RESIDUAL_LIMIT")
    global_error = solver["continuity"]["global"]
    if global_error is None or abs(global_error) > float(settings["max_continuity_global"]):
        errors.append("CONTINUITY_LIMIT")
    history = solver.get("residual_history") or {}
    limits = settings["steady_initial_residual_limits"]
    enough_iterations = solver["iterations"] >= int(settings["minimum_engineering_iterations"])
    reduction_limit = float(settings["minimum_residual_reduction_orders"])
    tail_gate = enough_iterations and all(
        field in history
        and history[field]["tail_max_initial"] <= float(limits[field])
        and (history[field]["reduction_orders"] >= reduction_limit
             or history[field]["first_initial"] <= float(limits[field]))
        for field in required
    )
    solver["engineering_converged"] = bool(tail_gate)
    solver["convergence_mode"] = (
        "residual_control" if solver["converged"] else
        "engineering_tail_gate" if tail_gate else None
    )
    if solver["ended"] and not (solver["converged"] or tail_gate):
        warnings.append("ITERATION_LIMIT")
    if not y_plus["available"]:
        warnings.append("YPLUS_FIELD_MISSING")
    elif (y_plus["wall_treatment_acceptable_area_ratio"] <
          float(settings["minimum_wall_treatment_area_ratio"])):
        warnings.append("WALL_TREATMENT_COVERAGE")
    errors = list(dict.fromkeys(errors))
    warnings = list(dict.fromkeys(warnings))
    return {
        "schema_version": 1, "contract": "run_manifest.v1",
        "engine": "body_fitted_isothermal_rans", "created_at": _now(),
        "status": "FAIL" if errors else "WARN" if warnings else "PASS",
        "design_ready": not errors and not warnings,
        "errors": errors, "warnings": warnings, "solver": solver,
        "airflow": physics_input["airflow"], "terminals": physics_input["terminals"],
        "y_plus": y_plus,
        "input": {"physics_input_sha256": _sha256(Path(case) / "physics_input.json")},
    }


def evaluate_transient_run(case, run_return, transient_input, runtime_seconds=None):
    settings = transient_input["settings"]
    log_path = Path(case) / "log.pimpleFoam"
    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
    solver = parse_transient_log(text)
    window = transient_window_metrics(
        case, transient_input["start_time_s"], transient_input["flow_through_time_s"]
    )
    solver_end = solver.get("end_time")
    cumulative_duration = max(
        0.0, float(solver_end) - float(transient_input["baseline_time_s"])
    ) if solver_end is not None else 0.0
    simulated_this_run = max(
        0.0, float(solver_end) - float(transient_input["start_time_s"])
    ) if solver_end is not None else 0.0
    required_duration = float(transient_input["minimum_required_duration_s"])
    remaining_duration = max(0.0, required_duration - cumulative_duration)
    runtime_seconds = float(runtime_seconds or 0.0)
    runtime_per_simulated_second = (
        runtime_seconds / simulated_this_run if simulated_this_run > 0 else None
    )
    solver_clock_seconds = solver.get("execution", {}).get("clock_seconds")
    solver_runtime_per_simulated_second = (
        float(solver_clock_seconds) / simulated_this_run
        if solver_clock_seconds is not None and simulated_this_run > 0 else None
    )
    fixed_runtime_overhead_seconds = (
        max(0.0, runtime_seconds - float(solver_clock_seconds))
        if solver_clock_seconds is not None else 0.0
    )
    projection_rate = (
        solver_runtime_per_simulated_second
        if solver_runtime_per_simulated_second is not None
        else runtime_per_simulated_second
    )
    remaining_runs = (
        math.ceil(remaining_duration / float(settings["transient_max_single_run_s"]))
        if remaining_duration > 0 else 0
    )
    estimated_remaining_runtime = (
        remaining_duration * projection_rate
        + remaining_runs * fixed_runtime_overhead_seconds
        if projection_rate is not None else None
    )
    window["run_sampled_duration_s"] = window.get("sampled_duration_s", 0.0)
    window["cumulative_duration_s"] = cumulative_duration
    window["required_duration_s"] = required_duration
    window["remaining_duration_s"] = remaining_duration
    window["flow_through_fraction"] = (
        cumulative_duration / float(transient_input["flow_through_time_s"])
        if float(transient_input["flow_through_time_s"]) > 0 else 0.0
    )
    y_plus = y_plus_metrics(
        case, transient_input["wall_patches"], float(settings["target_y_plus_min"]),
        float(settings["target_y_plus_max"]), float(settings["kinematic_viscosity_m2_s"]),
        float(settings["low_y_plus_max"]),
    )
    errors, warnings = [], []
    if not run_return.get("ok") or not solver["ended"] or solver["fatal"]:
        errors.append("TRANSIENT_SOLVER_FAILED")
    global_error = solver["continuity"]["global"]
    if global_error is None or abs(global_error) > float(settings["max_continuity_global"]):
        errors.append("CONTINUITY_LIMIT")
    courant_max = solver["courant"]["maximum"]
    if courant_max is None or courant_max > float(settings["transient_max_courant_gate"]):
        errors.append("COURANT_LIMIT")
    if not window["available"]:
        warnings.append("TRANSIENT_WINDOW_MISSING")
    else:
        span_limit = float(settings["transient_stability_relative_span"])
        if (window["mean_speed_relative_span"] > span_limit
                or window["rms_speed_relative_span"] > span_limit):
            warnings.append("TRANSIENT_WINDOW_UNSTABLE")
        if (window["flow_through_fraction"] <
                float(settings["transient_minimum_flow_through_fraction"])):
            warnings.append("TRANSIENT_WINDOW_TOO_SHORT")
    if not y_plus["available"]:
        warnings.append("YPLUS_FIELD_MISSING")
    elif (y_plus["wall_treatment_acceptable_area_ratio"] <
          float(settings["minimum_wall_treatment_area_ratio"])):
        warnings.append("WALL_TREATMENT_COVERAGE")
    errors = list(dict.fromkeys(errors))
    warnings = list(dict.fromkeys(warnings))
    progress_path = Path(case) / "transient_progress.json"
    try:
        previous = _read_json(progress_path) if progress_path.is_file() else {}
    except (OSError, ValueError, json.JSONDecodeError):
        previous = {}
    runs = list(previous.get("runs") or [])
    runs.append({
        "started_at": transient_input["created_at"],
        "start_time_s": transient_input["start_time_s"],
        "end_time_s": solver_end,
        "simulated_duration_s": simulated_this_run,
        "runtime_seconds": runtime_seconds,
        "courant_max": solver["courant"]["maximum"],
        "mean_speed_relative_span": window.get("mean_speed_relative_span"),
        "rms_speed_relative_span": window.get("rms_speed_relative_span"),
        "numerics": transient_input.get("numerics") or _transient_numerics(settings),
    })
    progress = {
        "schema_version": 1, "contract": "transient_progress.v1",
        "baseline_time_s": transient_input["baseline_time_s"],
        "latest_time_s": solver_end,
        "completed_duration_s": cumulative_duration,
        "required_duration_s": required_duration,
        "remaining_duration_s": remaining_duration,
        "flow_through_time_s": transient_input["flow_through_time_s"],
        "flow_through_fraction": window["flow_through_fraction"],
        "runs_completed": len(runs),
        "total_runtime_seconds": sum(float(item.get("runtime_seconds") or 0.0) for item in runs),
        "last_runtime_per_simulated_second": runtime_per_simulated_second,
        "last_solver_clock_seconds": solver_clock_seconds,
        "last_solver_runtime_per_simulated_second": solver_runtime_per_simulated_second,
        "last_fixed_runtime_overhead_seconds": fixed_runtime_overhead_seconds,
        "estimated_remaining_runs": remaining_runs,
        "estimated_remaining_runtime_seconds": estimated_remaining_runtime,
        "interactive_runtime_budget_seconds": float(
            settings["transient_interactive_runtime_budget_s"]
        ),
        "interactive_budget_exceeded": bool(
            estimated_remaining_runtime is not None
            and estimated_remaining_runtime > float(
                settings["transient_interactive_runtime_budget_s"]
            )
        ),
        "recommended_next_duration_s": min(
            remaining_duration, float(settings["transient_max_single_run_s"])
        ),
        "numerics": transient_input.get("numerics") or _transient_numerics(settings),
        "runs": runs[-50:],
    }
    if progress["interactive_budget_exceeded"]:
        warnings.append("TRANSIENT_RUNTIME_BUDGET")
        warnings = list(dict.fromkeys(warnings))
    return {
        "schema_version": 1, "contract": "run_manifest.v1",
        "engine": "body_fitted_isothermal_urans", "mode": "transient_diagnostic",
        "created_at": _now(),
        "status": "FAIL" if errors else "WARN" if warnings else "PASS",
        "design_ready": not errors and not warnings,
        "errors": errors, "warnings": warnings, "solver": solver,
        "transient_window": window,
        "transient_progress": progress,
        "airflow": transient_input["airflow"], "terminals": transient_input["terminals"],
        "y_plus": y_plus,
        "input": {
            "transient_input_sha256": _sha256(Path(case) / "transient_input.json"),
            "steady_run_manifest_sha256": transient_input["steady_run_manifest_sha256"],
        },
    }


def run_isothermal_case(solver_case_dir, progress_cb=None):
    case = Path(solver_case_dir).expanduser().resolve()
    if not (case / "physics_input.json").is_file():
        return {"ok": False, "error": f"유효한 등온 solver case가 아닙니다: {case}"}
    physics_input = _read_json(case / "physics_input.json")
    result = run_case(case, name=case.name + "_isothermal", keep_mesh=False,
                      progress_cb=progress_cb)
    if not result.get("ok"):
        return result
    manifest = evaluate_run(case, result, physics_input)
    _write(case / "run_manifest.json",
           json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {
        "ok": manifest["status"] != "FAIL", "error": None if manifest["status"] != "FAIL"
        else "등온 유동 gate 실패: " + ", ".join(manifest["errors"]),
        "case": str(case), "manifest": manifest,
        "manifest_path": str(case / "run_manifest.json"),
    }


def prepare_buoyant_restart(solver_case_dir, settings=None):
    """Prepare a bounded latestTime continuation of an accepted thermal case."""
    case = Path(solver_case_dir).expanduser().resolve()
    try:
        thermal_input = _read_json(case / "thermal_input.json")
        current_manifest = _read_json(case / "run_manifest.json")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"열·부력 결과를 읽지 못했습니다: {exc}"}
    if current_manifest.get("status") == "FAIL":
        return {"ok": False, "error": "실패한 열·부력 결과에서는 이어 계산할 수 없습니다."}
    latest = _latest_time(case)
    if latest is None:
        return {"ok": False, "error": "이어 계산할 최신 결과 time이 없습니다."}
    requested = dict(settings or {})
    cfg = dict(DEFAULT_SETTINGS, **(thermal_input.get("settings") or {}))
    cfg.update(requested)
    if "thermal_max_delta_t_s" not in requested:
        cfg["thermal_max_delta_t_s"] = float(
            DEFAULT_SETTINGS["thermal_continuation_max_delta_t_s"]
        )
    if "thermal_write_interval_s" not in requested:
        cfg["thermal_write_interval_s"] = float(
            cfg["thermal_continuation_write_interval_s"]
        )
    duration = float(cfg["thermal_duration_s"])
    if duration <= 0:
        return {"ok": False, "error": "추가 계산 시간은 0보다 커야 합니다."}
    if duration > float(cfg["thermal_max_single_run_s"]):
        return {
            "ok": False,
            "error": f"한 번의 열·부력 이어 계산은 최대 {cfg['thermal_max_single_run_s']}초입니다.",
        }
    # OpenFOAM may finish without writing a new time when the final remainder
    # is shorter than writeInterval. Always make the bounded remainder recoverable.
    cfg["thermal_write_interval_s"] = min(
        float(cfg["thermal_write_interval_s"]), duration,
    )
    try:
        mesh_manifest = _read_json(case / "mesh_manifest.json")
        cell_count = int((mesh_manifest.get("mesh") or {}).get("cells") or 0)
        thermal_numerics = cfd_numerics.thermal_numerics_contract(mesh_manifest, cfg)
    except (OSError, ValueError, TypeError, json.JSONDecodeError,
            cfd_numerics.NumericalInputError):
        mesh_manifest = {}
        cell_count = 0
        thermal_numerics = cfd_numerics.thermal_numerics_contract({}, cfg)
    requested_processes = max(1, int(cfg["thermal_parallel_processes"]))
    parallel_capability = cfg.pop("parallel_capability", None)
    parallel_plan = cfd_parallel.choose_parallel_plan(
        "body_fitted_restart",
        cell_count,
        parallel_capability,
        requested_ranks=requested_processes,
        min_cells=int(cfg["thermal_parallel_min_cells"]),
    )
    parallel_processes = parallel_plan.ranks
    start_time = float(latest.name)
    _write(
        case / "system" / "controlDict",
        _thermal_restart_control_dict(cfg, start_time, duration),
    )
    _write(case / "system" / "fvSchemes", _thermal_fv_schemes(thermal_numerics))
    _write(case / "system" / "fvSolution", _thermal_fv_solution(cfg, thermal_numerics))
    if parallel_processes > 1:
        _write(
            case / "system" / "decomposeParDict",
            _decompose_par_dict(parallel_processes),
        )
    _write(case / "Allrun", _thermal_restart_allrun(parallel_processes))
    os.chmod(case / "Allrun", 0o755)
    contract = {
        "schema_version": 1,
        "contract": "thermal_restart_input.v1",
        "engine": "body_fitted_buoyant_urans",
        "created_at": _now(),
        "start_time_s": start_time,
        "end_time_s": start_time + duration,
        "duration_s": duration,
        "numerics": {
            "profile": cfg["thermal_continuation_profile"],
            "max_delta_t_s": float(cfg["thermal_max_delta_t_s"]),
            "write_interval_s": float(cfg["thermal_write_interval_s"]),
            "max_courant": float(cfg["thermal_max_co"]),
            "courant_gate": float(cfg["thermal_max_courant_gate"]),
            "outer_correctors": int(cfg["thermal_outer_correctors"]),
            "pressure_correctors": int(cfg["thermal_pressure_correctors"]),
            "parallel_processes": parallel_processes,
            "parallel_mode": parallel_plan.mode,
            "parallel_blockers": list(parallel_plan.blockers),
            "cell_count": cell_count,
        },
        "thermal_numerics": thermal_numerics,
        "settings": cfg,
        "parallel_plan": asdict(parallel_plan),
        "thermal_input_sha256": _sha256(case / "thermal_input.json"),
    }
    _write(
        case / "thermal_restart_input.json",
        json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    parallel_run = cfd_parallel.write_parallel_run(
        case / "parallel_run.v1.json",
        parallel_plan,
        case_kind="body_fitted_restart",
        input_sha256=_sha256(case / "thermal_restart_input.json"),
    )
    return {
        "ok": True,
        "case": str(case),
        "thermal_restart_input": contract,
        "parallel_plan": asdict(parallel_plan),
        "parallel_run": parallel_run,
    }


def _transient_energy_balance(case, thermal_input, thermal, previous_progress,
                              start_time_s, latest_time_s):
    """Integrate exhaust heat and combine it with independently stored room heat."""
    storage = thermal.get("room_heat_storage") or {}
    stored = storage.get("stored_sensible_energy_j")
    applied_power = float(
        (thermal_input.get("heat") or {}).get("applied_convective_power_w") or 0.0
    )
    input_energy = applied_power * float(latest_time_s or 0.0)
    previous_balance = (previous_progress or {}).get("energy_balance") or {}
    history_complete = (
        float(start_time_s) <= 1e-12
        or bool(previous_balance.get("history_complete"))
    )
    cumulative = float(previous_balance.get("cumulative_exhaust_energy_j") or 0.0)
    previous_power = float(previous_balance.get("latest_exhaust_power_w") or 0.0)
    samples = [(float(start_time_s), previous_power)]
    for path in Path(case).iterdir():
        if not path.is_dir():
            continue
        try:
            sample_time = float(path.name)
        except ValueError:
            continue
        if sample_time <= float(start_time_s) or sample_time > float(latest_time_s):
            continue
        power = _solved_exhaust_power(case, path, thermal_input)
        if power is not None:
            samples.append((sample_time, float(power)))
    current_power = thermal.get("exhaust_sensible_power_w")
    if (current_power is not None and latest_time_s is not None
            and not any(abs(row[0] - float(latest_time_s)) < 1e-9 for row in samples)):
        samples.append((float(latest_time_s), float(current_power)))
    samples = sorted({time_value: power for time_value, power in samples}.items())
    interval_energy = 0.0
    for (left_t, left_p), (right_t, right_p) in zip(samples, samples[1:]):
        interval_energy += max(0.0, right_t - left_t) * (left_p + right_p) * 0.5
    cumulative += interval_energy
    accounted = None if stored is None else float(stored) + cumulative
    closure = (
        accounted / input_energy
        if accounted is not None and input_energy > 0 else None
    )
    return {
        "available": stored is not None and input_energy > 0,
        "history_complete": history_complete,
        "method": "room_storage_plus_trapezoidal_solver_exhaust",
        "input_energy_j": input_energy,
        "stored_sensible_energy_j": stored,
        "cumulative_exhaust_energy_j": cumulative,
        "current_interval_exhaust_energy_j": interval_energy,
        "accounted_energy_j": accounted,
        "transient_closure_ratio": closure,
        "latest_exhaust_power_w": current_power,
        "sample_count_current_run": max(0, len(samples) - 1),
        "cell_volume_sum_m3": storage.get("cell_volume_sum_m3"),
        "reference_temperature_k": storage.get("reference_temperature_k"),
    }


def _thermal_progress(case, thermal_input, solver, runtime_seconds, start_time_s,
                      settings=None, numerics=None, thermal=None):
    """Calculate flow-through progress and wall-clock projection for thermal runs."""
    cfg = dict(DEFAULT_SETTINGS, **(thermal_input.get("settings") or {}))
    cfg.update(settings or {})
    mesh_manifest = _read_json(Path(case) / "mesh_manifest.json")
    volume_m3 = float(mesh_manifest.get("occ_volume_m3", 0.0) or 0.0)
    supply_m3_s = float((thermal_input.get("airflow") or {}).get("supply_cmh", 0.0)) / 3600.0
    flow_through_time = volume_m3 / supply_m3_s if volume_m3 > 0 and supply_m3_s > 0 else 0.0
    minimum_fraction = float(cfg["thermal_minimum_flow_through_fraction"])
    required_duration = flow_through_time * minimum_fraction
    latest = _latest_time(case)
    latest_time = float(latest.name) if latest is not None else None
    completed = max(0.0, latest_time or 0.0)
    simulated_this_run = max(0.0, (latest_time or start_time_s) - start_time_s)
    runtime_seconds = float(runtime_seconds or 0.0)
    solver_clock = solver.get("execution", {}).get("clock_seconds")
    solver_rate = (
        float(solver_clock) / simulated_this_run
        if solver_clock is not None and simulated_this_run > 0 else None
    )
    overall_rate = runtime_seconds / simulated_this_run if simulated_this_run > 0 else None
    overhead = (
        max(0.0, runtime_seconds - float(solver_clock))
        if solver_clock is not None else 0.0
    )
    rate = solver_rate if solver_rate is not None else overall_rate
    remaining = max(0.0, required_duration - completed)
    max_run = float(cfg["thermal_max_single_run_s"])
    # The 0.05 s stability run intentionally uses a very small fixed deltaT.
    # Its cost is not representative of the adaptive continuation profile and
    # can overstate a multi-FTT forecast by orders of magnitude.  Wait for one
    # real continuation sample before publishing a wall-clock estimate.
    continuation_sample = numerics is not None
    estimate_status = (
        "complete" if remaining <= 0 else
        "measured_continuation" if continuation_sample else
        "awaiting_continuation_sample"
    )
    estimated = (
        0.0 if remaining <= 0 else
        remaining * rate
        if continuation_sample and rate is not None else None
    )
    checkpoint_budget = max(
        0.0, float(cfg["thermal_checkpoint_wall_budget_s"])
    )
    checkpoint_min_duration = max(
        1e-6, float(cfg["thermal_checkpoint_min_duration_s"])
    )
    checkpoint_rate = rate
    checkpoint_rate_source = (
        "measured_continuation" if continuation_sample and rate is not None
        else "unavailable"
    )
    if not continuation_sample and rate is not None:
        initial_delta_t = max(1e-12, float(cfg["thermal_max_delta_t_s"]))
        continuation_delta_t = max(
            initial_delta_t,
            float(cfg["thermal_continuation_max_delta_t_s"]),
        )
        safety = max(
            1.0, float(cfg["thermal_checkpoint_initial_rate_safety_factor"])
        )
        checkpoint_rate = rate * initial_delta_t / continuation_delta_t * safety
        checkpoint_rate_source = "initial_stability_scaled"
    recommended = min(remaining, max_run)
    if checkpoint_budget > 0 and checkpoint_rate is not None and checkpoint_rate > 0:
        wall_bounded = max(
            checkpoint_min_duration, checkpoint_budget / checkpoint_rate
        )
        recommended = min(recommended, wall_bounded)
    remaining_runs = (
        math.ceil(remaining / recommended)
        if remaining > 0 and recommended > 0 else 0
    )
    if estimated is not None:
        estimated += remaining_runs * overhead
    progress_path = Path(case) / "thermal_progress.json"
    try:
        previous = _read_json(progress_path) if progress_path.is_file() else {}
    except (OSError, ValueError, json.JSONDecodeError):
        previous = {}
    runs = list(previous.get("runs") or [])
    runs.append({
        "started_at": _now(),
        "start_time_s": start_time_s,
        "end_time_s": latest_time,
        "simulated_duration_s": simulated_this_run,
        "runtime_seconds": runtime_seconds,
        "solver_clock_seconds": solver_clock,
        "courant_max": solver.get("courant", {}).get("peak_maximum"),
        "exhaust_sensible_power_w": (
            (thermal or {}).get("exhaust_sensible_power_w")
        ),
        "numerics": numerics or {
            "profile": "initial_stability_v1",
            "max_delta_t_s": float(cfg["thermal_max_delta_t_s"]),
            "write_interval_s": float(cfg["thermal_write_interval_s"]),
        },
    })
    budget = float(cfg["thermal_interactive_runtime_budget_s"])
    energy_balance = _transient_energy_balance(
        case, thermal_input, thermal or {}, previous, start_time_s, latest_time,
    )
    return {
        "schema_version": 1,
        "contract": "thermal_progress.v1",
        "latest_time_s": latest_time,
        "completed_duration_s": completed,
        "required_duration_s": required_duration,
        "remaining_duration_s": remaining,
        "flow_through_time_s": flow_through_time,
        "minimum_flow_through_fraction": minimum_fraction,
        "flow_through_fraction": completed / flow_through_time if flow_through_time > 0 else 0.0,
        "runs_completed": len(runs),
        "total_runtime_seconds": sum(float(row.get("runtime_seconds") or 0.0) for row in runs),
        "last_solver_clock_seconds": solver_clock,
        "last_solver_runtime_per_simulated_second": solver_rate,
        "last_fixed_runtime_overhead_seconds": overhead,
        "estimated_remaining_runs": remaining_runs,
        "estimate_status": estimate_status,
        "estimated_remaining_runtime_seconds": estimated,
        "interactive_runtime_budget_seconds": budget,
        "interactive_budget_exceeded": bool(estimated is not None and estimated > budget),
        "recommended_next_duration_s": recommended,
        "checkpoint_wall_budget_seconds": checkpoint_budget,
        "checkpoint_rate_source": checkpoint_rate_source,
        "checkpoint_rate_seconds_per_simulated_second": checkpoint_rate,
        "energy_balance": energy_balance,
        "numerics": runs[-1]["numerics"],
        "runs": runs[-50:],
    }


def _attach_thermal_progress(case, manifest, thermal_input, runtime_seconds,
                             start_time_s, settings=None, numerics=None):
    progress = _thermal_progress(
        case, thermal_input, manifest["solver"], runtime_seconds, start_time_s,
        settings=settings, numerics=numerics, thermal=manifest.get("thermal"),
    )
    warnings = list(manifest.get("warnings") or [])
    cfg = dict(DEFAULT_SETTINGS, **(thermal_input.get("settings") or {}))
    cfg.update(settings or {})
    if progress["flow_through_fraction"] < float(
            cfg["thermal_minimum_flow_through_fraction"]):
        warnings.append("THERMAL_WINDOW_TOO_SHORT")
    if progress["interactive_budget_exceeded"]:
        warnings.append("THERMAL_RUNTIME_BUDGET")
    balance = progress.get("energy_balance") or {}
    ratio = balance.get("transient_closure_ratio")
    if balance.get("available") and balance.get("history_complete"):
        warnings = [item for item in warnings if item != "ENERGY_CLOSURE_PENDING"]
        if not (float(cfg["minimum_energy_closure_ratio"]) <= float(ratio) <=
                float(cfg["maximum_energy_closure_ratio"])):
            warnings.append("TRANSIENT_ENERGY_CLOSURE_PENDING")
    elif manifest.get("thermal", {}).get("energy_closure_ratio") is not None:
        warnings.append("TRANSIENT_ENERGY_HISTORY_INCOMPLETE")
    manifest["warnings"] = list(dict.fromkeys(warnings))
    manifest["thermal_progress"] = progress
    manifest["status"] = (
        "FAIL" if manifest.get("errors") else "WARN" if manifest["warnings"] else "PASS"
    )
    manifest["design_ready"] = not manifest.get("errors") and not manifest["warnings"]
    _write(
        Path(case) / "thermal_progress.json",
        json.dumps(progress, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def _build_body_fitted_results(case):
    """Build optional VTU artifacts without changing the solver gate result."""
    try:
        import cfd_post
        return cfd_post.build_result_artifacts(case)
    except Exception as exc:  # result export must not rewrite solver evidence
        return {"ok": False, "error": str(exc), "case": str(case)}


def run_buoyant_case(solver_case_dir, progress_cb=None):
    case = Path(solver_case_dir).expanduser().resolve()
    if not (case / "thermal_input.json").is_file():
        return {"ok": False, "error": f"유효한 열·부력 solver case가 아닙니다: {case}"}
    thermal_input = _read_json(case / "thermal_input.json")
    pre_run_provenance = None
    if (isinstance(thermal_input.get("settings"), dict)
            and isinstance(thermal_input.get("numerics"), dict)):
        pre_run_provenance = _thermal_numerics_provenance(
            case, thermal_input["settings"], thermal_input["numerics"]
        )
    started = time.monotonic()
    result = run_case(
        case, name=case.name + "_buoyant", keep_mesh=False,
        progress_cb=progress_cb,
    )
    runtime_seconds = time.monotonic() - started
    evaluation_kwargs = (
        {"numerical_provenance": pre_run_provenance}
        if pre_run_provenance is not None else {}
    )
    manifest = evaluate_buoyant_run(
        case, result, thermal_input, **evaluation_kwargs
    )
    if result.get("ok"):
        manifest = _attach_thermal_progress(
            case, manifest, thermal_input, runtime_seconds, 0.0
        )
    _write(case / "run_manifest.json",
           json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    postprocess = _build_body_fitted_results(case) if result.get("ok") else None
    if not result.get("ok"):
        return {
            "ok": False,
            "error": result.get("error") or "열·부력 계산이 실패했습니다.",
            "case": str(case), "manifest": manifest,
            "manifest_path": str(case / "run_manifest.json"),
            "result_artifacts": postprocess,
        }
    return {
        "ok": manifest["status"] != "FAIL",
        "error": None if manifest["status"] != "FAIL" else
        "열·부력 유동 gate 실패: " + ", ".join(manifest["errors"]),
        "case": str(case), "manifest": manifest,
        "manifest_path": str(case / "run_manifest.json"),
        "result_artifacts": postprocess,
    }


def run_buoyant_continuation(solver_case_dir, settings=None, progress_cb=None):
    """Continue a thermal case from latestTime and update runtime projections."""
    case = Path(solver_case_dir).expanduser().resolve()
    prepared = prepare_buoyant_restart(case, settings=settings)
    if not prepared.get("ok"):
        return prepared
    restart_input = prepared["thermal_restart_input"]
    thermal_input = _read_json(case / "thermal_input.json")
    restart_input_path = case / "thermal_restart_input.json"
    pre_run_provenance = _thermal_numerics_provenance(
        case, restart_input["settings"], restart_input["thermal_numerics"],
        restart_input_path=restart_input_path,
    )
    started = time.monotonic()
    result = run_case(
        case, name=case.name + "_buoyant_continue", keep_mesh=False,
        progress_cb=progress_cb, restart_from_latest=True,
    )
    if not result.get("ok"):
        return result
    runtime_seconds = time.monotonic() - started
    manifest = evaluate_buoyant_run(
        case, result, thermal_input,
        effective_settings=restart_input["settings"],
        effective_numerics=restart_input["thermal_numerics"],
        restart_input_path=restart_input_path,
        numerical_provenance=pre_run_provenance,
    )
    manifest["mode"] = "thermal_transient_continuation"
    manifest = _attach_thermal_progress(
        case, manifest, thermal_input, runtime_seconds,
        float(restart_input["start_time_s"]), settings=restart_input["settings"],
        numerics=restart_input["numerics"],
    )
    _write(
        case / "run_manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    postprocess = _build_body_fitted_results(case)
    return {
        "ok": manifest["status"] != "FAIL",
        "error": None if manifest["status"] != "FAIL" else
        "열·부력 이어 계산 gate 실패: " + ", ".join(manifest["errors"]),
        "case": str(case),
        "manifest": manifest,
        "manifest_path": str(case / "run_manifest.json"),
        "result_artifacts": postprocess,
    }


def run_transient_diagnostic(solver_case_dir, settings=None, progress_cb=None):
    case = Path(solver_case_dir).expanduser().resolve()
    prepared = prepare_transient_restart(case, settings=settings)
    if not prepared.get("ok"):
        return prepared
    transient_input = prepared["transient_input"]
    started = time.monotonic()
    result = run_case(
        case, name=case.name + "_transient", keep_mesh=False,
        progress_cb=progress_cb, restart_from_latest=True,
    )
    if not result.get("ok"):
        return result
    runtime_seconds = time.monotonic() - started
    manifest = evaluate_transient_run(
        case, result, transient_input, runtime_seconds=runtime_seconds
    )
    _write(case / "transient_progress.json", json.dumps(
        manifest["transient_progress"], ensure_ascii=False, indent=2, sort_keys=True
    ) + "\n")
    _write(case / "run_manifest.json",
           json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {
        "ok": manifest["status"] != "FAIL",
        "error": None if manifest["status"] != "FAIL" else
        "시간변동 진단 gate 실패: " + ", ".join(manifest["errors"]),
        "case": str(case), "manifest": manifest,
        "manifest_path": str(case / "run_manifest.json"),
    }
