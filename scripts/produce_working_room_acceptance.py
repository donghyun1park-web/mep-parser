"""Produce and publish independently validated working-room runtime evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cfd_working_room import build_working_room_geometry, validate_working_room


_AUTHORITY_RELATIVE = Path("_working_validation") / "working-room-v1"
_MANIFEST_NAME = "working_room_acceptance.json"
_LIMITS = {
    "minimum_physical_time_s": 240.0,
    "maximum_peak_courant": 1.0,
    "maximum_terminal_phi_imbalance_ratio": 0.001,
    "minimum_energy_closure_ratio": 0.95,
    "maximum_energy_closure_ratio": 1.05,
    "maximum_mean_temperature_delta_k": 0.02,
    "maximum_mean_speed_delta_m_s": 0.005,
    "maximum_energy_closure_delta_percentage_points": 0.5,
}


class WorkingRoomRuntime(Protocol):
    def run_case(self, projects_root: Path, case_id: str) -> Path:
        """Create one complete case and return its directory."""


class SystemWorkingRoomRuntime:
    """Run the real FreeCAD OCC, OpenFOAM mesh, and thermal toolchain."""

    def __init__(self, repo_root: Path | str, *, solver_lock_root: Path | str | None = None):
        self.repo_root = Path(repo_root).resolve()
        self.solver_lock_root = (
            Path(solver_lock_root).resolve() if solver_lock_root is not None else None
        )

    @staticmethod
    def _require_ok(result: object, stage: str) -> dict:
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise RuntimeError(f"WORKING_ROOM_{stage}_FAILED")
        return result

    def _locked(self, projects_root: Path, operation):
        import cfd_gci_job

        lock_root = self.solver_lock_root or projects_root
        token, _owner = cfd_gci_job.acquire_solver_lock(lock_root)
        if token is None:
            raise RuntimeError("WORKING_ROOM_SOLVER_BUSY")
        try:
            return operation()
        finally:
            cfd_gci_job.release_solver_lock(lock_root, token)

    def run_case(self, projects_root: Path, case_id: str) -> Path:
        import cfd_mesh
        import cfd_occ
        import cfd_physics
        import cfd_report

        projects_root = Path(projects_root).resolve()
        if case_id not in {"anchor", "repeat"}:
            raise ValueError("Working-room case id must be anchor or repeat")
        final_case = projects_root / _AUTHORITY_RELATIVE / case_id
        if final_case.exists():
            raise FileExistsError("Working-room case directory must be fresh")
        work_root = (
            projects_root / "_working_validation"
            / f".working-room-runtime-{case_id}-{uuid.uuid4().hex}"
        )
        geometry_path = work_root / "geometry.json"
        occ_output = work_root / "occ"
        mesh_case = work_root / "mesh"
        work_root.mkdir(parents=True)
        try:
            _write_json(geometry_path, build_working_room_geometry())
            print(f"[{case_id}] OCC geometry", flush=True)
            occ_result = cfd_occ.run_occ_job(
                geometry_path,
                occ_output,
                executable=os.environ.get("MEP_CFD_FREECADCMD") or None,
            )
            self._require_ok(occ_result, "OCC")

            surface_path = occ_output / "surface_manifest.json"
            surface = _load_json(surface_path)
            source = surface.get("source")
            if not isinstance(source, dict):
                raise RuntimeError("WORKING_ROOM_OCC_SOURCE_INVALID")
            source["geometry_path"] = (
                _AUTHORITY_RELATIVE / case_id / "geometry.json"
            ).as_posix()
            source["geometry_sha256"] = _sha256(geometry_path)
            _write_json(surface_path, surface)
            self._require_ok(cfd_occ.inspect_occ_output(occ_output), "OCC_RECHECK")

            print(f"[{case_id}] OpenFOAM mesh build", flush=True)
            self._require_ok(cfd_mesh.build_mesh_case(
                occ_output,
                mesh_case,
                {
                    "preset": "detailed",
                    "background_cell_m": 0.125,
                    "surface_level_min": 0,
                    "surface_level_max": 0,
                    "terminal_level": 1,
                    "equipment_level": 0,
                    "local_refinement_level": 0,
                    "feature_level": 0,
                },
            ), "MESH_BUILD")
            mesh_run = self._locked(
                projects_root,
                lambda: cfd_mesh.run_mesh_case(
                    mesh_case,
                    progress_cb=lambda line: print(f"[{case_id}] {line}", flush=True),
                ),
            )
            self._require_ok(mesh_run, "MESH_RUN")

            print(f"[{case_id}] 240 s fixed-step thermal build", flush=True)
            thermal_settings = {
                "supply_temperature_k": 293.15,
                "initial_temperature_k": 293.15,
                "reference_temperature_k": 293.15,
                "thermal_duration_s": 240.0,
                "thermal_initial_delta_t_s": 0.01,
                "thermal_max_delta_t_s": 0.01,
                "thermal_write_interval_s": 20.0,
                "thermal_max_co": 1.0,
                "thermal_max_courant_gate": 1.0,
                "thermal_design_max_courant_gate": 1.0,
                "thermal_numerics_profile": "design_limited_second_order_v1",
                "thermal_parallel_processes": 1,
                "thermal_preconditioning_iterations": 0,
                "thermal_minimum_flow_through_fraction": 3.0,
                "thermal_interactive_runtime_budget_s": 86400.0,
            }
            self._require_ok(
                cfd_physics.build_single_pc_numerical_spotcheck_case(
                    mesh_case, final_case, thermal_settings,
                ),
                "THERMAL_BUILD",
            )
            shutil.copy2(geometry_path, final_case / "geometry.json")
            shutil.copy2(mesh_case / "log.checkMesh", final_case / "log.checkMesh")

            print(f"[{case_id}] OpenFOAM 240 s thermal solve", flush=True)
            thermal_run = self._locked(
                projects_root,
                lambda: cfd_physics.run_buoyant_case(
                    final_case,
                    progress_cb=lambda line: print(f"[{case_id}] {line}", flush=True),
                ),
            )
            thermal_run = self._require_ok(thermal_run, "THERMAL_RUN")
            self._require_ok(thermal_run.get("result_artifacts"), "POSTPROCESS")
            self._require_ok(cfd_report.generate_body_fitted_report(
                final_case, projects_root=projects_root,
            ), "REPORT")
            return final_case
        finally:
            if work_root.exists():
                shutil.rmtree(work_root)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path.name}")
    return value


def _canonical_tree_sha256(case_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((row for row in case_dir.rglob("*") if row.is_file()), key=lambda row: row.relative_to(case_dir).as_posix()):
        digest.update(path.relative_to(case_dir).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(_sha256(path)))
    return digest.hexdigest()


def _time_directory(case_dir: Path, expected_time: float) -> Path:
    candidates: list[tuple[float, Path]] = []
    for path in case_dir.iterdir():
        if not path.is_dir():
            continue
        try:
            value = float(path.name)
        except ValueError:
            continue
        if abs(value - expected_time) <= 1e-9:
            candidates.append((value, path))
    if len(candidates) != 1:
        raise ValueError("Expected exactly one hash-pinned terminal field directory")
    return candidates[0][1]


def _case_artifacts(case_dir: Path) -> dict[str, Path]:
    result = _load_json(case_dir / "result_manifest.json")
    time_dir = _time_directory(case_dir, float(result["time_s"]))
    slices = result.get("slices")
    if not isinstance(slices, list):
        raise ValueError("Result slices are missing")
    slice_paths = {
        row.get("axis"): case_dir / row["path"]
        for row in slices if isinstance(row, dict) and isinstance(row.get("path"), str)
    }
    artifacts = {
        "geometry": case_dir / "geometry.json",
        "surface": case_dir / "surface_manifest.json",
        "mesh_input": case_dir / "mesh_input.json",
        "mesh": case_dir / "mesh_manifest.json",
        "thermal_input": case_dir / "thermal_input.json",
        "control_dict": case_dir / "system" / "controlDict",
        "fv_schemes": case_dir / "system" / "fvSchemes",
        "fv_solution": case_dir / "system" / "fvSolution",
        "turbulence_properties": case_dir / "constant" / "turbulenceProperties",
        "allrun": case_dir / "Allrun",
        "thermal_progress": case_dir / "thermal_progress.json",
        "run": case_dir / "run_manifest.json",
        "result": case_dir / "result_manifest.json",
        "check_mesh_log": case_dir / "log.checkMesh",
        "solver_log": case_dir / "log.buoyantBoussinesqPimpleFoam",
        "field_t": time_dir / "T",
        "field_u": time_dir / "U",
        "field_phi": time_dir / "phi",
        "field_v": time_dir / "V",
        "vtu": case_dir / result["source"]["path"],
        "summary": case_dir / result["summary_path"],
        "slice_x": slice_paths["x"],
        "slice_y": slice_paths["y"],
        "slice_z": slice_paths["z"],
        "report": case_dir / "body_fitted_report.html",
    }
    missing = [key for key, path in artifacts.items() if not path.is_file()]
    if missing:
        raise ValueError("Missing required case artifacts: " + ",".join(sorted(missing)))
    return artifacts


def _case_record(case_dir: Path, projects_root: Path) -> dict:
    artifacts = _case_artifacts(case_dir)
    return {
        "case_path": case_dir.relative_to(projects_root).as_posix(),
        "case_tree_sha256": _canonical_tree_sha256(case_dir),
        "artifacts": {
            key: {
                "path": path.relative_to(projects_root).as_posix(),
                "sha256": _sha256(path),
            }
            for key, path in artifacts.items()
        },
    }


def _blocked_runtime_result() -> dict:
    return {
        "check_id": "working_room_e2e",
        "status": "BLOCKED",
        "blockers": ["EXTERNAL_RUNTIME_FAILURE"],
        "message": "Working-room runtime execution failed; prior authority was preserved.",
    }


def _publish_authority(candidate: Path, authority: Path) -> None:
    authority.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if authority.exists():
        history = authority.parent / "history" / "working-room"
        history.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = history / f"{timestamp}-{uuid.uuid4().hex[:12]}"
        os.replace(authority, backup)
    try:
        os.replace(candidate, authority)
    except BaseException:
        if backup is not None and backup.exists() and not authority.exists():
            os.replace(backup, authority)
        raise


def produce_working_room_acceptance(
    repo_root: Path | str,
    *,
    projects_root: Path | str | None = None,
    runtime: WorkingRoomRuntime | None = None,
) -> dict:
    """Run an isolated anchor/repeat pair, validate it, then publish atomically."""

    repo_root = Path(repo_root).resolve()
    projects_root = Path(projects_root or (repo_root / "cfd_projects")).resolve()
    validation_root = projects_root / "_working_validation"
    validation_root.mkdir(parents=True, exist_ok=True)
    system_temp = Path(tempfile.gettempdir()).resolve()
    stage_parent = (
        system_temp
        if not projects_root.drive or system_temp.drive.lower() == projects_root.drive.lower()
        else projects_root.parent
    )
    stage_root = Path(tempfile.mkdtemp(
        prefix=".working-room-stage-", dir=stage_parent,
    )).resolve()
    candidate = stage_root / _AUTHORITY_RELATIVE

    try:
        if runtime is None:
            runtime = SystemWorkingRoomRuntime(repo_root, solver_lock_root=projects_root)
        cases = {}
        for label in ("anchor", "repeat"):
            case_dir = Path(runtime.run_case(stage_root, label)).resolve()
            expected = (candidate / label).resolve()
            if case_dir != expected:
                raise ValueError("Runtime returned a non-canonical case directory")
            cases[label] = case_dir

        manifest_path = candidate / _MANIFEST_NAME
        _write_json(manifest_path, {
            "schema_version": 1,
            "contract": "working_room_acceptance.v1",
            "anchor": _case_record(cases["anchor"], stage_root),
            "repeat": _case_record(cases["repeat"], stage_root),
            "limits": _LIMITS,
        })
        candidate_result = validate_working_room(manifest_path, stage_root)
        if candidate_result["status"] != "PASS":
            return candidate_result

        authority = projects_root / _AUTHORITY_RELATIVE
        _publish_authority(candidate, authority)
        return validate_working_room(authority / _MANIFEST_NAME, projects_root)
    except (Exception, SystemExit):
        return _blocked_runtime_result()
    finally:
        if stage_root.exists():
            shutil.rmtree(stage_root)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = produce_working_room_acceptance(args.repo_root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
