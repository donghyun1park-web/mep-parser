"""P3 body-fitted mesh presets, quality gates, and WSL OpenFOAM runner."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import uuid

from cfd_occ import inspect_occ_output
from cfd_run import diagnose_openfoam, win_to_wsl, _wsl, _wsl_args, _remote_run_dir


DEFAULT_SETTINGS = {
    "preset": "quick",
    "background_cell_m": 0.4,
    "surface_level_min": 1,
    "surface_level_max": 2,
    "terminal_level": 2,
    "equipment_level": 2,
    "local_refinement_level": 3,
    "terminal_refinement_distance_m": 0.0,
    "equipment_refinement_distance_m": 0.0,
    "max_local_cells": 300_000,
    "max_global_cells": 500_000,
    "min_refinement_cells": 0,
    "n_cells_between_levels": 2,
    "feature_level": 2,
    "surface_feature_angle": 150,
    "resolve_feature_angle": 30,
    "snap_enabled": True,
    "snap_n_smooth_patch": 3,
    "snap_tolerance": 2.0,
    "snap_n_solve_iter": 30,
    "snap_n_relax_iter": 5,
    "snap_n_feature_iter": 10,
    "implicit_feature_snap": False,
    "explicit_feature_snap": True,
    "multi_region_feature_snap": True,
    "max_concave": 80,
    "min_face_weight": 0.05,
    "add_layers": False,
    "wall_layers": 0,
    "equipment_layers": 0,
    "layer_expansion_ratio": 1.2,
    "layer_final_thickness": 0.3,
    "layer_min_thickness": 0.1,
    "layer_n_grow": 0,
    "layer_feature_angle": 60,
    "min_layer_coverage": 0.0,
    "min_average_layers": 0.0,
    "target_y_plus_min": None,
    "target_y_plus_max": None,
    "ram_limit_gb": 8.0,
    "disk_limit_gb": 8.0,
}
MESH_PRESETS = {
    "quick": {},
    "detailed": {
        "background_cell_m": 0.35,
        # Use a uniformly finer background with proven level-2 surfaces.
        # Level-3 transitions and 0.3 m background cells both destabilized the
        # v1912 pressure solve in the G2 thermal matrix.
        "surface_level_min": 1,
        "surface_level_max": 2,
        "terminal_level": 2,
        "equipment_level": 2,
        "local_refinement_level": 2,
        "n_cells_between_levels": 2,
        "terminal_refinement_distance_m": 0.0,
        "equipment_refinement_distance_m": 0.0,
        "max_local_cells": 500_000,
        "max_global_cells": 750_000,
        # Prism layers are intentionally not part of the default detailed
        # profile on OpenFOAM 1912. In G2 they create thin cells at embedded
        # terminal edges and make the pressure solve diverge before 0.001 s.
        "add_layers": False,
        "wall_layers": 0,
        # Do not add millimetre-scale prisms on arbitrary CAD solids. Those
        # cells caused the pressure field to diverge before 0.001 s in G2.
        "equipment_layers": 0,
        "layer_expansion_ratio": 1.2,
        "layer_final_thickness": 0.3,
        "layer_min_thickness": 0.1,
        # Stop wall prisms one cell away from terminal patch boundaries. This
        # avoids thin polyhedra where ceiling layers terminate at round grilles.
        "layer_n_grow": 1,
        "min_face_weight": 0.02,
        "min_layer_coverage": 0.0,
        "min_average_layers": 0.0,
        "target_y_plus_min": None,
        "target_y_plus_max": None,
    },
}
LOG_NAMES = (
    "surfaceCheck", "blockMesh", "surfaceFeatureExtract", "snappyHexMesh",
    "checkMesh", "checkMesh.strict",
)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _foam_header(class_name, object_name, location="system"):
    return (
        "FoamFile\n{\n"
        "    version     2.0;\n"
        "    format      ascii;\n"
        f"    class       {class_name};\n"
        f"    location    \"{location}\";\n"
        f"    object      {object_name};\n"
        "}\n\n"
    )


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


def _surface_bounds(manifest):
    mins = [[], [], []]
    maxs = [[], [], []]
    for region in manifest.get("regions") or []:
        aabb = region.get("aabb") or {}
        for axis in range(3):
            mins[axis].append(float(aabb["min_m"][axis]))
            maxs[axis].append(float(aabb["max_m"][axis]))
    if not all(mins) or not all(maxs):
        raise ValueError("surface manifest에 유효한 AABB가 없습니다.")
    return [min(values) for values in mins], [max(values) for values in maxs]


def resolve_settings(settings=None):
    requested = dict(settings or {})
    preset = str(requested.pop("preset", "quick") or "quick").lower()
    if preset not in MESH_PRESETS:
        raise ValueError(f"지원하지 않는 메시 프리셋입니다: {preset}")
    cfg = dict(DEFAULT_SETTINGS)
    cfg.update(MESH_PRESETS[preset])
    cfg.update(requested)
    cfg["preset"] = preset
    return cfg


def estimate_resources(surface_manifest, settings=None):
    cfg = resolve_settings(settings)
    cell = float(cfg["background_cell_m"])
    if cell <= 0:
        raise ValueError("background_cell_m은 0보다 커야 합니다.")
    mins, maxs = _surface_bounds(surface_manifest)
    padding = max(0.3, cell * 2.0)
    lower, upper, counts = [], [], []
    for lo, hi in zip(mins, maxs):
        aligned_lo = math.floor((lo - padding) / cell) * cell
        aligned_hi = math.ceil((hi + padding) / cell) * cell
        count = max(1, int(round((aligned_hi - aligned_lo) / cell)))
        lower.append(aligned_lo)
        upper.append(aligned_lo + count * cell)
        counts.append(count)
    background = math.prod(counts)
    max_level = max(
        int(cfg["surface_level_max"]), int(cfg["terminal_level"]),
        int(cfg["equipment_level"]), int(cfg["local_refinement_level"]),
    )
    estimated = min(int(cfg["max_global_cells"]), background * (8 ** max_level) * 2)
    ram_gb = estimated * 7000 / (1024 ** 3)
    disk_gb = estimated * 3500 / (1024 ** 3)
    return {
        "bounds_m": {"min": lower, "max": upper},
        "background_divisions": counts,
        "background_cells": background,
        "estimated_cells": estimated,
        "estimated_ram_gb": round(ram_gb, 3),
        "estimated_disk_gb": round(disk_gb, 3),
        "settings": cfg,
    }


def _validate_limits(estimate):
    cfg = estimate["settings"]
    if int(cfg["max_global_cells"]) > 2_000_000:
        raise ValueError("P3A 안전 한도 max_global_cells=2,000,000을 초과합니다.")
    if int(cfg["max_local_cells"]) > int(cfg["max_global_cells"]):
        raise ValueError("max_local_cells는 max_global_cells 이하여야 합니다.")
    if estimate["estimated_ram_gb"] > float(cfg["ram_limit_gb"]):
        raise ValueError(
            f"예상 RAM {estimate['estimated_ram_gb']:.2f}GB가 설정 한도를 초과합니다."
        )
    if estimate["estimated_disk_gb"] > float(cfg["disk_limit_gb"]):
        raise ValueError(
            f"예상 디스크 {estimate['estimated_disk_gb']:.2f}GB가 설정 한도를 초과합니다."
        )


def _block_mesh_dict(estimate):
    lo = estimate["bounds_m"]["min"]
    hi = estimate["bounds_m"]["max"]
    nx, ny, nz = estimate["background_divisions"]
    vertices = [
        (lo[0], lo[1], lo[2]), (hi[0], lo[1], lo[2]),
        (hi[0], hi[1], lo[2]), (lo[0], hi[1], lo[2]),
        (lo[0], lo[1], hi[2]), (hi[0], lo[1], hi[2]),
        (hi[0], hi[1], hi[2]), (lo[0], hi[1], hi[2]),
    ]
    text = _foam_header("dictionary", "blockMeshDict") + "convertToMeters 1;\n\nvertices\n(\n"
    text += "".join("    (%.9g %.9g %.9g)\n" % point for point in vertices)
    text += (
        ");\n\nblocks\n(\n"
        f"    hex (0 1 2 3 4 5 6 7) ({nx} {ny} {nz}) simpleGrading (1 1 1)\n"
        ");\n\nedges ();\n\nboundary\n(\n"
        "    background\n    {\n        type patch;\n        faces\n        (\n"
        "            (0 4 7 3)\n            (1 2 6 5)\n"
        "            (0 1 5 4)\n            (3 7 6 2)\n"
        "            (0 3 2 1)\n            (4 5 6 7)\n"
        "        );\n    }\n);\n\nmergePatchPairs ();\n"
    )
    return text


def _foam_bool(value):
    return "true" if bool(value) else "false"


def _region_kind(region):
    role = str(region.get("role") or "").lower()
    name = str(region.get("name") or "").lower()
    if role in ("supply", "exhaust"):
        return "terminal"
    if name.startswith("equipment_") or role in ("solid", "heat_source", "column"):
        return "equipment"
    return "wall"


def _refinement_boxes(surface_manifest, cfg):
    boxes = []
    for index, region in enumerate(surface_manifest.get("regions") or []):
        kind = _region_kind(region)
        distance = float(cfg[f"{kind}_refinement_distance_m"] or 0.0) if kind != "wall" else 0.0
        if distance <= 0:
            continue
        aabb = region.get("aabb") or {}
        lower, upper = aabb.get("min_m"), aabb.get("max_m")
        if not lower or not upper:
            continue
        boxes.append({
            "name": f"{kind}Refine{index}",
            "kind": kind,
            "min": [float(value) - distance for value in lower],
            "max": [float(value) + distance for value in upper],
            "level": int(cfg["local_refinement_level"]),
        })
    return boxes


def _layer_regions(surface_manifest, cfg):
    output = []
    if not cfg["add_layers"]:
        return output
    regions = surface_manifest.get("regions") or []
    for region in regions:
        kind = _region_kind(region)
        count = int(cfg["equipment_layers"] if kind == "equipment"
                    else cfg["wall_layers"] if kind == "wall" else 0)
        if count > 0:
            patch_name = ("airVolume" if len(regions) == 1
                          else "airVolume_" + region["name"])
            output.append((patch_name, count, kind))
    return output


def _surface_feature_dict(settings=None):
    cfg = resolve_settings(settings)
    return (
        _foam_header("dictionary", "surfaceFeatureExtractDict")
        + "air_volume_regions.stl\n{\n"
          "    extractionMethod extractFromSurface;\n"
          "    extractFromSurfaceCoeffs\n    {\n"
          f"        includedAngle {float(cfg['surface_feature_angle']):.9g};\n    }}\n"
          "    writeObj yes;\n}\n"
    )


def _mesh_quality_dict(settings=None):
    cfg = resolve_settings(settings)
    return (
        _foam_header("dictionary", "meshQualityDict")
        + "maxNonOrtho 65;\n"
          "maxBoundarySkewness 20;\n"
          "maxInternalSkewness 4;\n"
          f"maxConcave {float(cfg['max_concave']):.9g};\n"
          "minVol 1e-13;\n"
          "minTetQuality 1e-15;\n"
          "minArea -1;\n"
          "minTwist 0.02;\n"
          "minDeterminant 0.001;\n"
          f"minFaceWeight {float(cfg['min_face_weight']):.9g};\n"
          "minVolRatio 0.01;\n"
          "minTriangleTwist -1;\n"
    )


def _snappy_dict(surface_manifest, estimate):
    cfg = estimate["settings"]
    point = surface_manifest["air_volume"]["location_in_mesh"]["point_m"]
    boxes = _refinement_boxes(surface_manifest, cfg)
    geometry_boxes = "".join(
        f"    {box['name']}\n    {{\n        type searchableBox;\n"
        f"        min ({box['min'][0]:.9g} {box['min'][1]:.9g} {box['min'][2]:.9g});\n"
        f"        max ({box['max'][0]:.9g} {box['max'][1]:.9g} {box['max'][2]:.9g});\n    }}\n"
        for box in boxes
    )
    refinement_boxes = "".join(
        f"        {box['name']}\n        {{\n            mode inside;\n"
        f"            levels ((1E15 {box['level']}));\n        }}\n"
        for box in boxes
    )
    regions = []
    for region in surface_manifest.get("regions") or []:
        name, role = region["name"], region["role"]
        kind = _region_kind(region)
        level = int(cfg["terminal_level"] if kind == "terminal"
                    else cfg["equipment_level"] if kind == "equipment"
                    else cfg["surface_level_max"])
        patch_type = "patch" if role in ("supply", "exhaust") else "wall"
        regions.append(
            f"            {name}\n            {{\n"
            f"                level ({level} {level});\n"
            f"                patchInfo {{ type {patch_type}; }}\n"
            "            }\n"
        )
    region_text = "".join(regions)
    layer_regions = _layer_regions(surface_manifest, cfg)
    layer_text = "".join(
        f"        {name}\n        {{\n            nSurfaceLayers {count};\n        }}\n"
        for name, count, _kind in layer_regions
    )
    return (
        _foam_header("dictionary", "snappyHexMeshDict")
        + f"castellatedMesh true;\nsnap {_foam_bool(cfg['snap_enabled'])};\n"
          f"addLayers {_foam_bool(cfg['add_layers'])};\n\n"
          "geometry\n{\n    air_volume_regions.stl\n    {\n"
          "        type triSurfaceMesh;\n        name airVolume;\n    }\n"
        + geometry_boxes
        + "}\n\n"
          "castellatedMeshControls\n{\n"
        + f"    maxLocalCells {int(cfg['max_local_cells'])};\n"
          f"    maxGlobalCells {int(cfg['max_global_cells'])};\n"
          f"    minRefinementCells {int(cfg['min_refinement_cells'])};\n"
          "    maxLoadUnbalance 0.10;\n"
          f"    nCellsBetweenLevels {int(cfg['n_cells_between_levels'])};\n"
          "    features\n    (\n"
          f"        {{ file \"air_volume_regions.eMesh\"; level {int(cfg['feature_level'])}; }}\n"
          "    );\n"
          "    refinementSurfaces\n    {\n        airVolume\n        {\n"
          f"            level ({int(cfg['surface_level_min'])} {int(cfg['surface_level_max'])});\n"
          "            patchInfo { type wall; }\n"
          "            regions\n            {\n"
        + region_text
        + "            }\n        }\n    }\n"
          f"    resolveFeatureAngle {float(cfg['resolve_feature_angle']):.9g};\n"
          "    refinementRegions\n    {\n"
        + refinement_boxes
        + "    }\n"
          f"    locationInMesh ({point[0]:.9g} {point[1]:.9g} {point[2]:.9g});\n"
          "    allowFreeStandingZoneFaces true;\n}\n\n"
          "snapControls\n{\n"
          f"    nSmoothPatch {int(cfg['snap_n_smooth_patch'])};\n"
          f"    tolerance {float(cfg['snap_tolerance']):.9g};\n"
          f"    nSolveIter {int(cfg['snap_n_solve_iter'])};\n"
          f"    nRelaxIter {int(cfg['snap_n_relax_iter'])};\n"
          f"    nFeatureSnapIter {int(cfg['snap_n_feature_iter'])};\n"
          f"    implicitFeatureSnap {_foam_bool(cfg['implicit_feature_snap'])};\n"
          f"    explicitFeatureSnap {_foam_bool(cfg['explicit_feature_snap'])};\n"
          f"    multiRegionFeatureSnap {_foam_bool(cfg['multi_region_feature_snap'])};\n}}\n\n"
          "addLayersControls\n{\n    relativeSizes true;\n    layers\n    {\n"
        + layer_text
        + "    }\n"
          f"    expansionRatio {float(cfg['layer_expansion_ratio']):.9g};\n"
          f"    finalLayerThickness {float(cfg['layer_final_thickness']):.9g};\n"
          f"    minThickness {float(cfg['layer_min_thickness']):.9g};\n"
          f"    nGrow {int(cfg['layer_n_grow'])};\n"
          f"    featureAngle {float(cfg['layer_feature_angle']):.9g};\n"
          "    slipFeatureAngle 30;\n    nRelaxIter 3;\n    nSmoothSurfaceNormals 1;\n"
          "    nSmoothNormals 3;\n    nSmoothThickness 10;\n    maxFaceThicknessRatio 0.5;\n"
          "    maxThicknessToMedialRatio 0.3;\n    minMedianAxisAngle 90;\n"
          "    nBufferCellsNoExtrude 0;\n    nLayerIter 50;\n}\n\n"
          "meshQualityControls\n{\n"
          "    maxNonOrtho 65;\n    maxBoundarySkewness 20;\n    maxInternalSkewness 4;\n"
          f"    maxConcave {float(cfg['max_concave']):.9g};\n"
          "    minVol 1e-13;\n    minTetQuality 1e-15;\n"
          "    minArea -1;\n    minTwist 0.02;\n    minDeterminant 0.001;\n"
          f"    minFaceWeight {float(cfg['min_face_weight']):.9g};\n"
          "    minVolRatio 0.01;\n    minTriangleTwist -1;\n"
          "    nSmoothScale 4;\n    errorReduction 0.75;\n"
          "    relaxed { maxNonOrtho 75; }\n}\n\nmergeTolerance 1e-6;\n"
    )


def _control_dict():
    return (
        _foam_header("dictionary", "controlDict")
        + "application snappyHexMesh;\nstartFrom startTime;\nstartTime 0;\n"
          "stopAt endTime;\nendTime 1;\ndeltaT 1;\nwriteControl timeStep;\n"
          "writeInterval 1;\npurgeWrite 0;\nwriteFormat ascii;\nwritePrecision 10;\n"
          "writeCompression off;\ntimeFormat general;\ntimePrecision 6;\n"
          "runTimeModifiable true;\n"
    )


def _fv_schemes():
    return (
        _foam_header("dictionary", "fvSchemes")
        + "ddtSchemes { default steadyState; }\n"
          "gradSchemes { default Gauss linear; }\n"
          "divSchemes { default none; }\n"
          "laplacianSchemes { default Gauss linear corrected; }\n"
          "interpolationSchemes { default linear; }\n"
          "snGradSchemes { default corrected; }\n"
    )


def _fv_solution():
    return _foam_header("dictionary", "fvSolution") + "solvers {}\n"


def _allmesh():
    return """#!/bin/bash
set -o pipefail
cd "${0%/*}" || exit 20
run_stage() {
    label="$1"; log="$2"; shift 2
    echo "=== $label ==="
    "$@" > "$log" 2>&1
    rc=$?
    if [ "$rc" -ne 0 ]; then
        echo "$label FAILED (exit $rc)"
        tail -40 "$log"
        exit "$rc"
    fi
}
run_stage surfaceCheck log.surfaceCheck surfaceCheck constant/triSurface/air_volume_regions.stl
run_stage blockMesh log.blockMesh blockMesh
run_stage surfaceFeatureExtract log.surfaceFeatureExtract surfaceFeatureExtract
run_stage snappyHexMesh log.snappyHexMesh snappyHexMesh -overwrite
run_stage checkMesh log.checkMesh checkMesh -allTopology -meshQuality
echo "=== strict geometry diagnostics ==="
checkMesh -allGeometry -allTopology -meshQuality > log.checkMesh.strict 2>&1
strict_rc=$?
grep -E 'Concave cells|Mesh OK|Failed [0-9]+ mesh checks|FOAM FATAL' log.checkMesh.strict | tail -8
if [ "$strict_rc" -ne 0 ]; then
    echo "strict geometry diagnostics reported warnings (exit $strict_rc)"
fi
echo "=== mesh done ==="
"""


def _publish_case(staging, target):
    target = Path(target)
    backup = target.with_name(target.name + ".backup." + uuid.uuid4().hex)
    if target.exists():
        os.replace(target, backup)
    try:
        os.replace(staging, target)
    except BaseException:
        if backup.exists():
            os.replace(backup, target)
        raise
    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)


def build_mesh_case(occ_output_dir, mesh_case_dir, settings=None):
    occ = inspect_occ_output(occ_output_dir)
    if not occ.get("ok"):
        return {"ok": False, "error": occ.get("error") or "OCC 출력 검증 실패"}
    manifest = occ["manifest"]
    try:
        estimate = estimate_resources(manifest, settings)
        _validate_limits(estimate)
    except (KeyError, TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}

    target = Path(mesh_case_dir).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=target.name + ".staging-", dir=target.parent))
    try:
        tri = staging / "constant" / "triSurface"
        tri.mkdir(parents=True)
        (staging / "system").mkdir()
        shutil.copy2(Path(occ_output_dir) / "air_volume_regions.stl",
                     tri / "air_volume_regions.stl")
        shutil.copy2(Path(occ_output_dir) / "surface_manifest.json",
                     staging / "surface_manifest.json")
        _write(staging / "system" / "blockMeshDict", _block_mesh_dict(estimate))
        _write(staging / "system" / "surfaceFeatureExtractDict",
               _surface_feature_dict(estimate["settings"]))
        _write(staging / "system" / "snappyHexMeshDict", _snappy_dict(manifest, estimate))
        _write(staging / "system" / "meshQualityDict",
               _mesh_quality_dict(estimate["settings"]))
        _write(staging / "system" / "controlDict", _control_dict())
        _write(staging / "system" / "fvSchemes", _fv_schemes())
        _write(staging / "system" / "fvSolution", _fv_solution())
        _write(staging / "Allmesh", _allmesh())
        os.chmod(staging / "Allmesh", 0o755)
        _write(staging / "mesh_input.json", json.dumps({
            "schema_version": 1,
            "contract": "mesh_input.v1",
            "engine": "body_fitted_airflow",
            "surface_manifest_sha256": _sha256(staging / "surface_manifest.json"),
            "surface_stl_sha256": _sha256(tri / "air_volume_regions.stl"),
            "estimate": estimate,
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        _publish_case(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"ok": True, "case": str(target), "estimate": estimate}


def _read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _number(pattern, text, default=None, flags=re.IGNORECASE):
    match = re.search(pattern, text, flags)
    if not match:
        return default
    try:
        return float(match.group(1))
    except (TypeError, ValueError):
        return default


def parse_check_mesh(text):
    number = r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
    failed_checks = _number(r"Failed\s+(\d+)\s+mesh checks", text, 0)
    concave_cells = _number(
        r"Concave cells \(using face planes\) found, number of cells:\s*(\d+)", text, 0,
    )
    failure_details = []
    error_block = re.search(
        r"Checking faces in error\s*:(.*?)(?:Failed\s+\d+\s+mesh checks|\Z)",
        text, re.I | re.S,
    )
    if error_block:
        for label, count in re.findall(r"(?m)^\s*(.+?)\s*:\s*(\d+)\s*$", error_block.group(1)):
            if int(count) > 0:
                failure_details.append(f"{label.strip()}: {count}")
    for line in text.splitlines():
        if re.search(r"\*\*\*|FOAM FATAL", line, re.I):
            detail = line.strip()
            if detail and detail not in failure_details:
                failure_details.append(detail)
    if failed_checks and not failure_details:
        failure_details.append(f"Failed {int(failed_checks)} mesh checks")
    return {
        "mesh_ok": "Mesh OK." in text,
        "fatal": bool(re.search(r"FOAM FATAL|Failed\s+\d+\s+mesh checks", text, re.I)),
        "failed_checks": int(failed_checks or 0),
        "failure_details": failure_details[:20],
        "concave_cells": int(concave_cells or 0),
        "cells": int(_number(r"\bcells:\s*(\d+)", text, 0)),
        "regions": int(_number(r"Number of regions:\s*(\d+)", text, 0)),
        "min_volume_m3": _number(r"Min volume\s*=\s*" + number, text),
        "total_volume_m3": _number(r"Total volume\s*=\s*" + number, text),
        "max_non_orthogonality": _number(r"Mesh non-orthogonality Max:\s*" + number, text),
        "max_skewness": _number(r"Max skewness\s*=\s*" + number, text),
    }


def parse_surface_check(text):
    return {
        "closed": "Surface is closed. All edges connected to two faces." in text,
        "illegal_triangles": not ("Surface has no illegal triangles." in text),
        "unconnected_parts": int(_number(r"Number of unconnected parts\s*:\s*(\d+)", text, 0)),
        "triangles": int(_number(r"Triangles\s*:\s*(\d+)", text, 0)),
    }


def parse_layer_report(text):
    extrusions = re.findall(
        r"Extruding\s+(\d+)\s+out of\s+(\d+)\s+faces\s+\(([-+0-9.eE]+)%\)",
        text,
    )
    added = re.findall(
        r"Added\s+(\d+)\s+out of\s+(\d+)\s+cells\s+\(([-+0-9.eE]+)%\)",
        text,
    )
    patch_rows = []
    table = re.split(
        r"(?m)^\s*patch\s+faces\s+layers\s+overall thickness\s*$", text
    )
    if len(table) >= 2:
        table_text = table[-1].split("Layer mesh", 1)[0]
        row_pattern = re.compile(
            r"^\s*(\S+)\s+(\d+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s+([-+0-9.eE]+)\s*$",
            re.M,
        )
        for name, faces, average, thickness, percent in row_pattern.findall(table_text):
            if name == "airVolume" or name.startswith("airVolume_"):
                patch_rows.append({
                    "mesh_patch_name": name,
                    "faces": int(faces),
                    "average_layers": float(average),
                    "overall_thickness_m": float(thickness),
                    "overall_thickness_percent": float(percent),
                })
    if extrusions:
        extruded_faces, candidate_faces, coverage_percent = extrusions[-1]
    else:
        extruded_faces, candidate_faces, coverage_percent = 0, 0, 0.0
    if added:
        added_cells, requested_cells, added_percent = added[-1]
    else:
        added_cells, requested_cells, added_percent = 0, 0, 0.0
    return {
        "extruded_faces": int(extruded_faces),
        "candidate_faces": int(candidate_faces),
        "coverage_ratio": float(coverage_percent) / 100.0,
        "added_cells": int(added_cells),
        "requested_layer_cells": int(requested_cells),
        "added_cell_ratio": float(added_percent) / 100.0,
        "patches": patch_rows,
    }


def _strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//.*", "", text)


def _list_body(text):
    clean = _strip_comments(text)
    match = re.search(r"\n\s*\d+\s*\n\s*\(", clean)
    return clean[match.end():] if match else ""


def _read_poly_mesh(poly_mesh):
    root = Path(poly_mesh)
    points_body = _list_body(_read_text(root / "points"))
    number = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
    points = [tuple(float(value) for value in match)
              for match in re.findall(rf"\(\s*({number})\s+({number})\s+({number})\s*\)",
                                      points_body)]
    faces_body = _list_body(_read_text(root / "faces"))
    faces = []
    for count, values in re.findall(r"(\d+)\s*\(\s*([0-9\s]+?)\s*\)", faces_body, re.S):
        indices = [int(value) for value in values.split()]
        if len(indices) == int(count):
            faces.append(indices)
    boundary = _strip_comments(_read_text(root / "boundary"))
    patches = {}
    for name, body in re.findall(r"([A-Za-z0-9_.:-]+)\s*\{([^{}]*)\}", boundary, re.S):
        n_faces = _number(r"nFaces\s+(\d+)\s*;", body)
        start = _number(r"startFace\s+(\d+)\s*;", body)
        if n_faces is not None and start is not None:
            patches[name] = {"nFaces": int(n_faces), "startFace": int(start)}
    return points, faces, patches


def _polygon_area(vertices):
    if len(vertices) < 3:
        return 0.0
    nx = ny = nz = 0.0
    for current, nxt in zip(vertices, vertices[1:] + vertices[:1]):
        nx += (current[1] - nxt[1]) * (current[2] + nxt[2])
        ny += (current[2] - nxt[2]) * (current[0] + nxt[0])
        nz += (current[0] - nxt[0]) * (current[1] + nxt[1])
    return 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz)


def patch_metrics(poly_mesh):
    points, faces, patches = _read_poly_mesh(poly_mesh)
    output = {}
    for name, item in patches.items():
        start, count = item["startFace"], item["nFaces"]
        area = 0.0
        for face in faces[start:start + count]:
            try:
                area += _polygon_area([points[index] for index in face])
            except IndexError:
                continue
        output[name] = {"faces": count, "area_m2": area}
    return output


def evaluate_mesh_gate(recovery_root, surface_manifest, run_returncode, mesh_input=None):
    surface = parse_surface_check(_read_text(Path(recovery_root) / "log.surfaceCheck"))
    check = parse_check_mesh(_read_text(Path(recovery_root) / "log.checkMesh"))
    strict_check = parse_check_mesh(
        _read_text(Path(recovery_root) / "log.checkMesh.strict")
    )
    poly_mesh = Path(recovery_root) / "constant" / "polyMesh"
    patches = patch_metrics(poly_mesh) if poly_mesh.is_dir() else {}
    mesh_input = mesh_input or {}
    settings = (((mesh_input.get("estimate") or {}).get("settings")) or
                resolve_settings())
    preset = str(settings.get("preset") or "quick")
    layer = parse_layer_report(_read_text(Path(recovery_root) / "log.snappyHexMesh"))
    layers_enabled = bool(settings.get("add_layers"))
    layer["enabled"] = layers_enabled
    layer["minimum_coverage_ratio"] = float(settings.get("min_layer_coverage") or 0.0)
    layer["minimum_average_layers"] = float(settings.get("min_average_layers") or 0.0)
    expected_layer_patches = [name for name, _count, _kind in
                              _layer_regions(surface_manifest, settings)]
    layer["expected_patches"] = expected_layer_patches
    errors = []
    warnings = []
    if float(settings.get("min_face_weight", 0.05)) < 0.05:
        warnings.append(
            "PROFILE_MIN_FACE_WEIGHT:"
            + f"{float(settings['min_face_weight']):.6g}"
        )
    if run_returncode != 0:
        errors.append("ALLMESH_FAILED")
    if not surface["closed"]:
        errors.append("SURFACE_NOT_CLOSED")
    if surface["illegal_triangles"]:
        errors.append("ILLEGAL_SURFACE_TRIANGLES")
    if not check["mesh_ok"] or check["fatal"]:
        errors.append("CHECKMESH_FAILED")
    if check["regions"] != 1:
        errors.append("FLUID_REGION_COUNT")
    if check["min_volume_m3"] is None or check["min_volume_m3"] <= 0:
        errors.append("NON_POSITIVE_CELL_VOLUME")
    if check["max_non_orthogonality"] is None or check["max_non_orthogonality"] > 70:
        errors.append("NON_ORTHOGONALITY_LIMIT")
    if check["max_skewness"] is None or check["max_skewness"] > 4:
        errors.append("SKEWNESS_LIMIT")
    default_faces = patches.get("defaultFaces", {}).get("faces", 0)
    if default_faces:
        errors.append("DEFAULT_FACES_NONZERO")

    region_metrics = []
    for region in surface_manifest.get("regions") or []:
        candidates = (region["name"], "airVolume_" + region["name"])
        mesh_patch_name = next((name for name in candidates if name in patches), None)
        if mesh_patch_name is None and len(surface_manifest.get("regions") or []) == 1:
            mesh_patch_name = "airVolume" if "airVolume" in patches else None
        actual = patches.get(mesh_patch_name, {"faces": 0, "area_m2": 0.0})
        expected = float(region.get("area_m2", 0.0) or 0.0)
        area_error = abs(actual["area_m2"] - expected) / expected if expected > 0 else None
        if actual["faces"] <= 0:
            errors.append("PATCH_MISSING:" + region["name"])
        if region.get("role") in ("supply", "exhaust") and (
                area_error is None or area_error > 0.05):
            errors.append("TERMINAL_PATCH_AREA:" + region["name"])
        region_metrics.append({
            "name": region["name"], "role": region.get("role"),
            "mesh_patch_name": mesh_patch_name,
            "faces": actual["faces"], "mesh_area_m2": actual["area_m2"],
            "occ_area_m2": expected, "area_error_ratio": area_error,
        })

    occ_volume = float((surface_manifest.get("air_volume") or {}).get("volume_m3", 0.0) or 0.0)
    volume_error = (abs(check["total_volume_m3"] - occ_volume) / occ_volume
                    if check["total_volume_m3"] is not None and occ_volume > 0 else None)
    if volume_error is None or volume_error > 0.02:
        errors.append("MESH_VOLUME_ERROR")
    if layers_enabled:
        if not layer["candidate_faces"] or not layer["patches"]:
            errors.append("LAYER_REPORT_MISSING")
        if layer["coverage_ratio"] < layer["minimum_coverage_ratio"]:
            errors.append("LAYER_COVERAGE_LIMIT")
        actual_layers = {item["mesh_patch_name"]: item for item in layer["patches"]}
        for patch_name in expected_layer_patches:
            item = actual_layers.get(patch_name)
            if item is None:
                errors.append("LAYER_PATCH_MISSING:" + patch_name)
            elif item["average_layers"] < layer["minimum_average_layers"]:
                errors.append("LAYER_COUNT_LIMIT:" + patch_name)
    if strict_check["concave_cells"]:
        warnings.append(f"STRICT_CONCAVE_CELLS:{strict_check['concave_cells']}")
    elif strict_check["fatal"]:
        warnings.append(f"STRICT_GEOMETRY_CHECKS:{strict_check['failed_checks']}")
    errors = list(dict.fromkeys(errors))
    return {
        "schema_version": 1,
        "contract": "mesh_manifest.v1",
        "engine": "body_fitted_airflow",
        "profile": preset,
        "created_at": _now(),
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "warnings": warnings,
        "surface": surface,
        "mesh": check,
        "strict_diagnostics": strict_check,
        "layer": layer,
        "y_plus": {
            "status": "PENDING_SOLVER" if layers_enabled else "NOT_APPLICABLE",
            "target_min": settings.get("target_y_plus_min"),
            "target_max": settings.get("target_y_plus_max"),
            "measured_wall_area_ratio": None,
        },
        "patches": region_metrics,
        "default_faces": default_faces,
        "occ_volume_m3": occ_volume,
        "mesh_volume_error_ratio": volume_error,
    }


def _publish_recovery(stage, case):
    case = Path(case)
    backup = Path(tempfile.mkdtemp(prefix=".mesh-previous-", dir=case.parent))
    names = ["mesh_manifest.json"] + ["log." + name for name in LOG_NAMES]
    old = [name for name in names if (case / name).exists()]
    if (case / "constant" / "polyMesh").exists():
        old.append("constant/polyMesh")
    new = [name for name in names if (Path(stage) / name).exists()]
    if (Path(stage) / "constant" / "polyMesh").exists():
        new.append("constant/polyMesh")
    published = []
    try:
        for rel in old:
            destination = backup / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(case / rel, destination)
        for rel in new:
            destination = case / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(Path(stage) / rel, destination)
            published.append(rel)
    except BaseException:
        for rel in published:
            path = case / rel
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            elif path.exists():
                path.unlink()
        for rel in old:
            saved = backup / rel
            if saved.exists():
                destination = case / rel
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(saved, destination)
        raise
    finally:
        shutil.rmtree(backup, ignore_errors=True)


def run_mesh_case(mesh_case_dir, progress_cb=None):
    cb = progress_cb or (lambda line: print(line, flush=True))
    case = Path(mesh_case_dir).expanduser().resolve()
    if not (case / "Allmesh").is_file() or not (case / "surface_manifest.json").is_file():
        return {"ok": False, "error": f"유효한 body-fitted 메시 케이스가 아닙니다: {case}"}
    capabilities = diagnose_openfoam()
    if not capabilities.get("body_fitted_ready"):
        return {"ok": False, "error": capabilities.get("summary") or "body-fitted 도구가 없습니다."}
    distro = capabilities.get("distro") or None
    bashrc = capabilities.get("bashrc") or "/usr/share/openfoam/etc/bashrc"
    wsl_case = win_to_wsl(str(case), distro=distro)
    run_dir = _remote_run_dir(str(case), case.name + "_mesh")
    cb(f"[1/3] WSL 메시 작업공간 준비: {run_dir}")
    copied = _wsl(
        f"mkdir -p ~/cfd_runs && rm -rf {run_dir} && cp -r {shlex.quote(wsl_case)} {run_dir} && "
        f"rm -rf {run_dir}/constant/polyMesh {run_dir}/log.*",
        distro=distro,
    )
    if copied.returncode != 0:
        detail = (copied.stderr or copied.stdout or "").strip()
        return {"ok": False, "error": "WSL 메시 작업공간 복사 실패: " + detail}

    cb("[2/3] surfaceCheck → snappyHexMesh → checkMesh")
    command = (
        f"set -o pipefail; source {shlex.quote(bashrc)} >/dev/null 2>&1 || exit 20; "
        f"cd {run_dir} || exit 21; chmod +x Allmesh || exit 22; "
        "./Allmesh 2>&1 | awk '/^===/{print;next} /FAILED|FATAL|Mesh OK/{print;next}'"
    )
    proc = subprocess.Popen(
        _wsl_args(command, distro), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    for line in proc.stdout:
        cb(line.rstrip("\n"))
    proc.wait()
    if proc.stdout:
        proc.stdout.close()
    run_rc = proc.returncode

    cb("[3/3] 메시·진단 로그 회수")
    recovery = Path(tempfile.mkdtemp(prefix=".mesh-recovery-", dir=case.parent))
    try:
        recovery_wsl = win_to_wsl(str(recovery), distro=distro)
        recover = _wsl(
            f"set -e; cd {run_dir}; "
            f"for f in log.*; do [ -f \"$f\" ] && cp \"$f\" {shlex.quote(recovery_wsl)}/; done; "
            f"if [ -d constant/polyMesh ]; then mkdir -p {shlex.quote(recovery_wsl)}/constant; "
            f"cp -r constant/polyMesh {shlex.quote(recovery_wsl)}/constant/; fi; echo recovered",
            distro=distro,
        )
        if recover.returncode != 0:
            detail = (recover.stderr or recover.stdout or "").strip()
            return {"ok": False, "error": "메시 진단 결과 회수 실패: " + detail}
        surface_manifest = json.loads((case / "surface_manifest.json").read_text(encoding="utf-8"))
        mesh_input = json.loads((case / "mesh_input.json").read_text(encoding="utf-8"))
        manifest = evaluate_mesh_gate(recovery, surface_manifest, run_rc, mesh_input)
        manifest["input"] = {
            "surface_manifest_sha256": _sha256(case / "surface_manifest.json"),
            "mesh_input_sha256": _sha256(case / "mesh_input.json"),
        }
        manifest["tools"] = {
            "openfoam_version": capabilities.get("version"),
            "package_version": capabilities.get("package_version"),
            "distro": capabilities.get("distro"),
        }
        _write(recovery / "mesh_manifest.json",
               json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        _publish_recovery(recovery, case)
        return {
            "ok": manifest["status"] == "PASS",
            "error": None if manifest["status"] == "PASS" else
                     "메시 품질 gate 실패: " + ", ".join(manifest["errors"]),
            "case": str(case),
            "manifest_path": str(case / "mesh_manifest.json"),
            "manifest": manifest,
        }
    finally:
        shutil.rmtree(recovery, ignore_errors=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build or run P3A body-fitted mesh")
    sub = parser.add_subparsers(dest="command", required=True)
    build_parser = sub.add_parser("build")
    build_parser.add_argument("occ_output")
    build_parser.add_argument("mesh_case")
    build_parser.add_argument("--background-cell-m", type=float)
    build_parser.add_argument("--preset", default="detailed")
    build_parser.add_argument("--terminal-level", type=int)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("mesh_case")
    args = parser.parse_args()
    result = (build_mesh_case(
                  args.occ_output, args.mesh_case,
                  settings={key: value for key, value in {
                      "preset": args.preset,
                      "background_cell_m": args.background_cell_m,
                      "terminal_level": args.terminal_level,
                  }.items() if value is not None},
              )
              if args.command == "build" else run_mesh_case(args.mesh_case))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if result.get("ok") else 2)
