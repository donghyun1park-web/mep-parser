"""Create and independently verify real-site DXF acceptance evidence.

The release gate must never trust a JSON file that merely says each pipeline
stage passed.  This module re-opens the DXF and every published manifest,
checks the content hashes between stages, and derives the drawing-variation
signature from the source artifacts.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

import cfd_result_gate
from geometry_v2 import validate_for_body_fitted


CONTRACT = "field_dxf_acceptance.v1"
GENERATOR = "mep-cfd-studio/field-acceptance-v1"
SAMPLE_NAMES = {
    "sample_plan.dxf", "sample_mep.dxf", "sample_blocks.dxf",
    "sample_walls.dxf", "temp_export.dxf", "temp_export_fixed.dxf",
}


def bundled_sample_hashes(projects_root="cfd_projects"):
    """Return hashes of shipped sample drawings, independent of their names."""
    roots = {
        Path(projects_root).expanduser().resolve().parent,
        Path(__file__).resolve().parent,
    }
    hashes = set()
    for workspace in roots:
        for name in SAMPLE_NAMES:
            for path in (workspace / name, workspace / "debug_tools" / name):
                if path.is_file():
                    try:
                        hashes.add(_sha256(path).lower())
                    except OSError:
                        pass
    return hashes


def is_bundled_sample_drawing(path, projects_root="cfd_projects"):
    path = Path(path).expanduser().resolve()
    if (path.name.lower() in SAMPLE_NAMES
            or path.stem.lower().startswith("sample_")):
        return True
    try:
        return path.is_file() and _sha256(path).lower() in bundled_sample_hashes(
            projects_root
        )
    except OSError:
        return False


def _now():
    return datetime.now(timezone.utc).isoformat()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (OSError, ValueError):
        return False


def _atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                                     dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _path_record(path, projects_root):
    path = Path(path).resolve()
    root = Path(projects_root).resolve()
    try:
        stored = path.relative_to(root).as_posix()
    except ValueError:
        stored = str(path)
    return {"path": stored, "sha256": _sha256(path)}


def _resolve_record(record, projects_root):
    path = Path(str((record or {}).get("path") or ""))
    if not path.is_absolute():
        path = Path(projects_root) / path
    return path.resolve()


def _canonical_hash(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _dxf_variation(source):
    import ezdxf
    from ezdxf import bbox

    document = ezdxf.readfile(source)
    modelspace = document.modelspace()
    layer_names = sorted({str(entity.dxf.layer) for entity in modelspace})
    rotations = sorted({
        round(float(entity.dxf.get("rotation", 0.0) or 0.0), 6)
        for entity in modelspace if entity.dxftype() == "INSERT"
    })
    try:
        bounds = bbox.extents(modelspace, fast=True)
        extmin = [round(float(value), 6) for value in bounds.extmin]
        extmax = [round(float(value), 6) for value in bounds.extmax]
    except Exception:
        extmin, extmax = [], []
    raw = {
        "insunits": int(document.header.get("$INSUNITS", 0) or 0),
        "extmin": extmin,
        "extmax": extmax,
        "insert_rotations_deg": rotations,
        "layers": layer_names,
    }
    return {
        **raw,
        "unit": f"INSUNITS:{raw['insunits']}",
        "origin": json.dumps(extmin, separators=(",", ":")),
        "rotation": json.dumps(rotations, separators=(",", ":")),
        "layers_signature": _canonical_hash(layer_names),
        "layers": _canonical_hash(layer_names),
        "signature": _canonical_hash(raw),
    }


def _same_path(left, right):
    try:
        return Path(left).resolve() == Path(right).resolve()
    except (OSError, TypeError, ValueError):
        return False


def evaluate_field_case(source_dxf, geometry_path, surface_dir, mesh_case,
                        solver_case, projects_root="cfd_projects",
                        actual_site_drawing=False):
    """Return computed evidence; no stage status is accepted from the caller."""
    root = Path(projects_root).expanduser().resolve()
    source = Path(source_dxf).expanduser().resolve()
    geometry_path = Path(geometry_path).expanduser().resolve()
    surface_dir = Path(surface_dir).expanduser().resolve()
    mesh_case = Path(mesh_case).expanduser().resolve()
    solver_case = Path(solver_case).expanduser().resolve()
    paths = {
        "source_dxf": source,
        "geometry": geometry_path,
        "surface_manifest": surface_dir / "surface_manifest.json",
        "mesh_manifest": mesh_case / "mesh_manifest.json",
        "run_manifest": solver_case / "run_manifest.json",
        "result_manifest": solver_case / "result_manifest.json",
    }
    errors = []
    if actual_site_drawing is not True:
        errors.append("ACTUAL_SITE_ATTESTATION_REQUIRED")
    if is_bundled_sample_drawing(source, root):
        errors.append("BUNDLED_SAMPLE_NOT_ACCEPTED")
    for label, path in paths.items():
        if not _inside(path, root):
            errors.append(f"OUTSIDE_PROJECTS_ROOT:{label}")
        if not path.is_file():
            errors.append(f"FILE_MISSING:{label}")
    if errors:
        return {"status": "FAIL", "errors": errors, "artifacts": {},
                "variation": {}}

    try:
        geometry = _read(geometry_path)
        surface = _read(paths["surface_manifest"])
        mesh = _read(paths["mesh_manifest"])
        run = _read(paths["run_manifest"])
        result = _read(paths["result_manifest"])
        mesh_surface = _read(mesh_case / "surface_manifest.json")
        mesh_input = _read(mesh_case / "mesh_input.json")
        solver_mesh = _read(solver_case / "mesh_manifest.json")
        thermal_input = _read(solver_case / "thermal_input.json")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {"status": "FAIL", "errors": [f"ARTIFACT_READ_ERROR:{exc}"],
                "artifacts": {}, "variation": {}}

    geometry_issues = validate_for_body_fitted(geometry)
    if geometry_issues or not (geometry.get("review") or {}).get("ready"):
        errors.append("GEOMETRY_NOT_BODY_FITTED_READY")
    if geometry.get("contract") != "geometry.v2":
        errors.append("GEOMETRY_CONTRACT")
    geometry_source = Path(str(geometry.get("source") or ""))
    if not geometry_source.is_absolute():
        geometry_source = geometry_path.parent / geometry_source
    if not _same_path(geometry_source, source):
        errors.append("GEOMETRY_SOURCE_MISMATCH")

    surface_source = surface.get("source") or {}
    surface_air = surface.get("air_volume") or {}
    surface_topology = surface.get("topology") or {}
    if not (
        surface.get("contract") == "surface_manifest.v1"
        and surface_air.get("valid") is True
        and int(surface_air.get("solid_count") or 0) == 1
        and surface_topology.get("watertight") is True
        and int(surface_topology.get("open_edges") or 0) == 0
        and int(surface_topology.get("non_manifold_edges") or 0) == 0
        and int(surface_topology.get("duplicate_triangles") or 0) == 0
    ):
        errors.append("SURFACE_GATE")
    if (surface_source.get("geometry_contract") != "geometry.v2"
            or not _same_path(surface_source.get("geometry_path"), geometry_path)
            or str(surface_source.get("geometry_sha256") or "").lower()
            != _sha256(geometry_path)):
        errors.append("SURFACE_GEOMETRY_CHAIN")
    outputs = surface.get("outputs") or {}
    for name_key, hash_key in (("multi_region_stl", "stl_sha256"),
                               ("brep", "brep_sha256")):
        output = surface_dir / str(outputs.get(name_key) or "")
        if (not output.is_file() or not outputs.get(hash_key)
                or str(outputs[hash_key]).lower() != _sha256(output)):
            errors.append(f"SURFACE_OUTPUT_HASH:{name_key}")

    mesh_input_path = mesh_case / "mesh_input.json"
    mesh_surface_path = mesh_case / "surface_manifest.json"
    mesh_state = mesh.get("mesh") or {}
    if not (
        mesh.get("contract") == "mesh_manifest.v1"
        and mesh.get("status") == "PASS"
        and mesh_state.get("mesh_ok") is True
        and mesh_state.get("fatal") is False
        and (mesh.get("surface") or {}).get("closed") is True
    ):
        errors.append("MESH_GATE")
    if not (
        mesh_surface == surface
        and str(mesh_input.get("surface_manifest_sha256") or "").lower()
        == _sha256(mesh_surface_path)
        and str((mesh.get("input") or {}).get("surface_manifest_sha256") or "").lower()
        == _sha256(mesh_surface_path)
        and str((mesh.get("input") or {}).get("mesh_input_sha256") or "").lower()
        == _sha256(mesh_input_path)
    ):
        errors.append("MESH_SURFACE_CHAIN")
    if "2606" not in str((mesh.get("tools") or {}).get("openfoam_version") or ""):
        errors.append("OPENFOAM_V2606_REQUIRED")

    solver_mesh_path = solver_case / "mesh_manifest.json"
    thermal_input_path = solver_case / "thermal_input.json"
    if not (solver_mesh == mesh
            and thermal_input.get("contract") == "thermal_input.v1"
            and str(thermal_input.get("mesh_manifest_sha256") or "").lower()
            == _sha256(solver_mesh_path)):
        errors.append("SOLVER_MESH_CHAIN")
    progress = run.get("thermal_progress") or {}
    energy = progress.get("energy_balance") or {}
    if not (
        run.get("contract") == "run_manifest.v1"
        and run.get("engine") == "body_fitted_buoyant_urans"
        and run.get("status") == "PASS"
        and run.get("design_ready") is True
        and progress.get("contract") == "thermal_progress.v1"
        and float(progress.get("flow_through_time_s") or 0) > 0
        and float(progress.get("minimum_flow_through_fraction") or 0) >= 3.0
        and float(progress.get("flow_through_fraction") or 0) >= 3.0
        and energy.get("available") is True
        and energy.get("history_complete") is True
        and str((run.get("input") or {}).get("thermal_input_sha256") or "").lower()
        == _sha256(thermal_input_path)
    ):
        errors.append("SOLVER_GATE")

    result_source = solver_case / str((result.get("source") or {}).get("path") or "")
    summary_path = solver_case / str(result.get("summary_path") or "")
    slice_paths = [solver_case / str(item.get("path") or "")
                   for item in (result.get("slices") or [])]
    fields = result.get("fields") or {}
    if not (
        result.get("contract") == "result_manifest.v1"
        and float(result.get("time_s") or 0) > 0
        and abs(float(result.get("time_s") or 0)
                - float(progress.get("latest_time_s") or 0)) <= 1e-6
        and "T" in fields and "U" in fields
        and len(slice_paths) >= 3
        and result_source.is_file()
        and str((result.get("source") or {}).get("sha256") or "").lower()
        == _sha256(result_source)
        and summary_path.is_file()
        and all(path.is_file() for path in slice_paths)
        and str(result.get("mesh_manifest_sha256") or "").lower()
        == _sha256(solver_mesh_path)
        and str(result.get("run_manifest_sha256") or "").lower()
        == _sha256(paths["run_manifest"])
    ):
        errors.append("RESULT_GATE")

    # Field/release evidence must use the same authoritative result contract as
    # the Studio and report.  A self-declared run status is not sufficient for
    # a design-ready field case: the result must also be provenance-current,
    # numerically qualified, and tied to a passing GCI study.
    body_gate = cfd_result_gate.evaluate_body_fitted_case(
        solver_case, gci_root=root / "_body_gci"
    )
    if not (
        body_gate.get("status") == "PASS"
        and body_gate.get("design_ready") is True
        and body_gate.get("citation_status") == "DESIGN_CITABLE"
        and body_gate.get("citable") is True
    ):
        blockers = body_gate.get("blockers") or ["NOT_DESIGN_CITABLE"]
        errors.append("RESULT_CITATION_GATE:" + ",".join(
            str(item) for item in blockers
        ))

    try:
        variation = _dxf_variation(source)
    except Exception as exc:
        variation = {}
        errors.append(f"DXF_INSPECTION_ERROR:{exc}")
    artifacts = {name: _path_record(path, root) for name, path in paths.items()}
    gates = {
        "geometry": "PASS" if not any(item.startswith("GEOMETRY") for item in errors) else "FAIL",
        "surface": "PASS" if not any(item.startswith("SURFACE") for item in errors) else "FAIL",
        "mesh": "PASS" if not any(item.startswith(("MESH", "OPENFOAM")) for item in errors) else "FAIL",
        "solver": "PASS" if not any(item.startswith("SOLVER") for item in errors) else "FAIL",
        "result": "PASS" if not any(item.startswith("RESULT") for item in errors) else "FAIL",
    }
    return {"status": "PASS" if not errors else "FAIL", "errors": errors,
            "artifacts": artifacts, "variation": variation, "gates": gates}


def build_field_acceptance(source_dxf, geometry_path, surface_dir, mesh_case,
                           solver_case, projects_root="cfd_projects",
                           actual_site_drawing=False, output_path=None):
    root = Path(projects_root).expanduser().resolve()
    computed = evaluate_field_case(
        source_dxf, geometry_path, surface_dir, mesh_case, solver_case,
        root, actual_site_drawing=actual_site_drawing,
    )
    source = Path(source_dxf).expanduser().resolve()
    manifest = {
        "schema_version": 1,
        "contract": CONTRACT,
        "generator": GENERATOR,
        "created_at": _now(),
        "status": computed["status"],
        "actual_site_drawing": actual_site_drawing is True,
        "source_dxf_path": computed.get("artifacts", {}).get(
            "source_dxf", {"path": str(source)}
        )["path"],
        "source_sha256": (computed.get("artifacts", {}).get("source_dxf") or {}).get("sha256", ""),
        "artifacts": computed.get("artifacts", {}),
        "variation": computed.get("variation", {}),
        "gates": computed.get("gates", {}),
        "errors": computed.get("errors", []),
    }
    if output_path is None:
        token = manifest["source_sha256"][:12] or "invalid"
        output_path = root / "_release_evidence" / "field_dxf" / f"{source.stem}-{token}.json"
    _atomic_json(output_path, manifest)
    return {"ok": manifest["status"] == "PASS", "manifest": manifest,
            "manifest_path": str(Path(output_path).resolve())}


def validate_evidence(path, projects_root="cfd_projects"):
    """Recompute every gate and reject edited, stale, or detached evidence."""
    try:
        row = _read(path)
        if (row.get("contract") != CONTRACT or row.get("generator") != GENERATOR
                or row.get("status") != "PASS"
                or row.get("actual_site_drawing") is not True):
            return {"ok": False, "error": "EVIDENCE_CONTRACT_OR_STATUS"}
        artifacts = row.get("artifacts") or {}
        root = Path(projects_root).expanduser().resolve()
        required = ("source_dxf", "geometry", "surface_manifest",
                    "mesh_manifest", "run_manifest", "result_manifest")
        if any(key not in artifacts for key in required):
            return {"ok": False, "error": "EVIDENCE_ARTIFACTS_MISSING"}
        resolved = {key: _resolve_record(artifacts[key], root) for key in required}
        computed = evaluate_field_case(
            resolved["source_dxf"], resolved["geometry"],
            resolved["surface_manifest"].parent,
            resolved["mesh_manifest"].parent,
            resolved["run_manifest"].parent,
            root, actual_site_drawing=True,
        )
        if computed.get("status") != "PASS":
            return {"ok": False, "error": ",".join(computed.get("errors") or [])}
        if (computed.get("artifacts") != artifacts
                or computed.get("variation") != row.get("variation")
                or computed.get("gates") != row.get("gates")):
            return {"ok": False, "error": "EVIDENCE_STALE_OR_EDITED"}
        return {"ok": True, "manifest": row, "computed": computed}
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"EVIDENCE_READ_ERROR:{exc}"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projects-root", default="cfd_projects")
    parser.add_argument("--source-dxf", required=True)
    parser.add_argument("--geometry", required=True)
    parser.add_argument("--surface-dir", required=True)
    parser.add_argument("--mesh-case", required=True)
    parser.add_argument("--solver-case", required=True)
    parser.add_argument("--actual-site", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    result = build_field_acceptance(
        args.source_dxf, args.geometry, args.surface_dir, args.mesh_case,
        args.solver_case, args.projects_root, args.actual_site, args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
