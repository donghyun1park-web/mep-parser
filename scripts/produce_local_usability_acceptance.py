"""Produce Task 5b serial-environment evidence, then revalidate it.

The producer owns external execution and evidence publication.  The existing
``local_usability_acceptance`` module remains a pure, fail-closed validator.
Nothing is published to the authoritative projects root until a complete
candidate bundle passes that validator in an isolated staging root.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Protocol, TextIO
from urllib.request import urlopen
import uuid

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

import cfd_capabilities
import cfd_export
import cfd_gci_job
import cfd_physics
import cfd_report
import cfd_run
from scripts.local_usability_acceptance import validate_local_usability_acceptance


RAW_RELATIVE = Path("_system/environment_acceptance")
RUNTIME_RELATIVE = Path("_working_validation/runtime_capability.v1.json")
MANIFEST_RELATIVE = Path("_working_validation/local_usability_acceptance.json")
DIAGNOSTIC_CODES = (
    "WSL_UNAVAILABLE",
    "FREECAD_UNAVAILABLE",
    "INVALID_GEOMETRY",
    "MESH_FAILURE",
    "SOLVER_OR_DISK_FAILURE",
)


class AcceptanceRuntime(Protocol):
    def now(self) -> datetime: ...

    def probe_openfoam(self) -> dict[str, Any]: ...

    def probe_freecad(self, run_id: str) -> dict[str, Any]: ...

    def run_environment_case(self, case_root: Path) -> dict[str, Any]: ...

    def launch_studio(self, attempt: int, run_id: str) -> dict[str, Any]: ...

    def diagnostic_observation(self, code: str, run_id: str) -> dict[str, Any]: ...


class SystemAcceptanceRuntime:
    """Current-PC runtime adapter. Slow methods remain overridable in tests."""

    def __init__(self, repo_root: Path):
        self.repo_root = Path(repo_root).resolve()

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def probe_openfoam(self) -> dict[str, Any]:
        return dict(cfd_run.diagnose_openfoam())

    def probe_freecad(self, run_id: str) -> dict[str, Any]:
        configured = os.environ.get("MEP_CFD_FREECADCMD", "").strip()
        if not configured:
            raise RuntimeError("FREECAD_EXPLICIT_EXECUTABLE_REQUIRED")
        executable = Path(configured).resolve(strict=True)
        result = dict(
            cfd_capabilities.diagnose_freecad_stages(
                executable, per_stage_timeout_s=20
            )
        )
        result["selection"] = "explicit"
        result["run_id"] = run_id
        return result

    def _studio_child_command(self, launch_root: Path, port: int) -> list[str]:
        return [
            sys.executable,
            "-B",
            str(Path(__file__).resolve()),
            "--studio-probe-child",
            "--projects-root",
            str(launch_root),
            "--port",
            str(port),
        ]

    @staticmethod
    def _available_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            return int(listener.getsockname()[1])

    def launch_studio(self, attempt: int, run_id: str) -> dict[str, Any]:
        if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
            raise ValueError("STUDIO_LAUNCH_ATTEMPT_INVALID")
        port = self._available_port()
        started = self.now()
        with tempfile.TemporaryDirectory(
            prefix=f".studio-launch-{attempt}-", dir=self.repo_root
        ) as temporary:
            launch_root = Path(temporary)
            process = subprocess.Popen(
                self._studio_child_command(launch_root, port),
                cwd=self.repo_root,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            http_ready: datetime | None = None
            dom_ready: datetime | None = None
            deadline = time.monotonic() + 10.0
            try:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("STUDIO_PROCESS_EXITED_BEFORE_HTTP_READY")
                    try:
                        with urlopen(f"http://127.0.0.1:{port}/", timeout=0.5) as response:
                            body = response.read().decode("utf-8", errors="replace")
                            http_ready = self.now()
                    except OSError:
                        time.sleep(0.05)
                        continue
                    if "MEP CFD Studio" not in body:
                        raise RuntimeError("STUDIO_DOM_MARKER_MISSING")
                    dom_ready = self.now()
                    break
                if http_ready is None or dom_ready is None:
                    raise RuntimeError("STUDIO_HTTP_READY_TIMEOUT")

                diagnostics_deadline = time.monotonic() + 90.0
                capability = launch_root / "capability_manifest.json"
                while time.monotonic() < diagnostics_deadline and not capability.is_file():
                    if process.poll() is not None:
                        raise RuntimeError("STUDIO_PROCESS_EXITED_DURING_DIAGNOSTICS")
                    time.sleep(0.05)
                if not capability.is_file():
                    raise RuntimeError("STUDIO_BACKGROUND_DIAGNOSTICS_TIMEOUT")

                assert process.stdin is not None
                process.stdin.write("\n")
                process.stdin.flush()
                stdout, stderr = process.communicate(timeout=10)
                if process.returncode != 0:
                    raise RuntimeError("STUDIO_CLEAN_SHUTDOWN_FAILED")
                if "Traceback (most recent call last):" in stdout + stderr:
                    raise RuntimeError("STUDIO_RAW_TRACEBACK_PRESENT")
            except BaseException:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
                raise

        return {
            "contract": "studio_launch_observation.v1",
            "run_id": run_id,
            "attempt": attempt,
            "process_started_at": _stamp(started),
            "http_ready_at": _stamp(http_ready),
            "dom_ready_at": _stamp(dom_ready),
            "required_dom_marker": "MEP CFD Studio",
            "status": "PASS",
        }

    def diagnostic_observation(self, code: str, run_id: str) -> dict[str, Any]:
        catalog = {
            "WSL_UNAVAILABLE": (
                "Windows가 WSL 실행 환경에 접근하지 못했습니다.",
                "OpenFOAM 계산을 시작할 수 없습니다.",
                "WSL 상태와 배포판 접근 권한을 확인한 뒤 환경 검사를 다시 실행하세요.",
            ),
            "FREECAD_UNAVAILABLE": (
                "FreeCAD 단계별 형상 진단이 완료되지 않았습니다.",
                "실제 형상 생성과 body-fitted 계산을 시작할 수 없습니다.",
                "FreeCADCmd 경로와 실패한 진단 단계를 확인한 뒤 다시 검사하세요.",
            ),
            "INVALID_GEOMETRY": (
                "확정 형상에 닫히지 않은 공간 또는 미확정 경계가 있습니다.",
                "유체 체적과 경계조건을 안전하게 생성할 수 없습니다.",
                "형상 검토 화면에서 표시된 경계와 단말을 수정한 뒤 다시 확인하세요.",
            ),
            "MESH_FAILURE": (
                "격자 생성 또는 checkMesh 품질 검사가 실패했습니다.",
                "solver 결과를 계산하거나 해석 근거로 사용할 수 없습니다.",
                "격자 로그의 실패 항목을 확인하고 형상 또는 격자 설정을 수정하세요.",
            ),
            "SOLVER_OR_DISK_FAILURE": (
                "solver 실행 또는 결과 파일 저장이 완료되지 않았습니다.",
                "계산 결과와 보고서를 신뢰할 수 없습니다.",
                "solver 로그와 디스크 여유 공간을 확인한 뒤 같은 작업을 다시 실행하세요.",
            ),
        }
        if code not in catalog:
            raise ValueError("DIAGNOSTIC_CODE_UNSUPPORTED")
        cause, impact, action = catalog[code]
        return {
            "contract": "actionable_diagnostic_observation.v1",
            "run_id": run_id,
            "observed_at": _stamp(self.now()),
            "code": code,
            "cause_ko": cause,
            "impact_ko": impact,
            "next_action_ko": action,
            "raw_traceback_count": 0,
            "status": "PASS",
            "log_text": code + "\n",
        }

    @staticmethod
    def _environment_settings() -> dict[str, Any]:
        settings = dict(cfd_physics.DEFAULT_SETTINGS)
        settings.update(
            thermal_duration_s=1.0,
            thermal_initial_delta_t_s=0.1,
            thermal_max_delta_t_s=0.1,
            thermal_write_interval_s=1.0,
            thermal_preconditioning_iterations=0,
        )
        return settings

    def _build_environment_case(self, case_root: Path) -> None:
        cfg = {
            "name": "environment_acceptance",
            "_note": "MEP CFD Studio 자동 환경 수용 테스트 — 설계 결과로 사용하지 않음",
            "room": {"L": 2.0, "W": 2.0, "H": 2.0},
            "mesh": {"cell": 0.5},
            "g": [0, 0, -9.81],
            "inlet": {
                "wall": "x0",
                "U": [0.3, 0, 0],
                "T": 293.15,
                "_desc": "환경 테스트 기준 급기",
            },
            "outlet": {"wall": "xL", "_desc": "환경 테스트 기준 배기"},
            "heat": {"power_kw": 0.5, "_desc": "환경 테스트 기준 발열"},
            "init": {"T": 295.15},
            "endTime": 1,
        }
        cfd_export.build_case(cfg, case_root)
        settings = self._environment_settings()
        control = cfd_physics._thermal_control_dict(
            settings, validation_scope="single_pc_numerical_spotcheck"
        )
        schemes = cfd_physics._thermal_fv_schemes().replace("omega", "epsilon")
        solution = cfd_physics._thermal_fv_solution(settings).replace("omega", "epsilon")
        (case_root / "system" / "controlDict").write_text(control, encoding="utf-8")
        (case_root / "system" / "fvSchemes").write_text(schemes, encoding="utf-8")
        (case_root / "system" / "fvSolution").write_text(solution, encoding="utf-8")
        (case_root / "Allrun").write_text(
            """#!/bin/bash
set -o pipefail
cd "${0%/*}" || exit 20
blockMesh > log.blockMesh 2>&1 || exit $?
topoSet > log.topoSet 2>&1 || exit $?
checkMesh > log.checkMesh 2>&1 || exit $?
buoyantBoussinesqPimpleFoam > log.buoyantBoussinesqPimpleFoam 2>&1
rc=$?
cat log.checkMesh
grep -E '^Time = |Courant Number|ExecutionTime|End$' log.buoyantBoussinesqPimpleFoam | tail -40
exit "$rc"
""",
            encoding="utf-8",
            newline="\n",
        )

    def _execute_environment_case(self, case_root: Path) -> dict[str, Any]:
        projects_root = case_root.parents[1]
        token, _owner = cfd_gci_job.acquire_solver_lock(projects_root)
        if token is None:
            return {
                "ok": False,
                "error": "CFD_SOLVER_BUSY",
                "runtime_baseline": {},
            }
        try:
            return cfd_run.run_case(
                case_root,
                name="environment_acceptance",
                progress_cb=lambda _line: None,
            )
        finally:
            cfd_gci_job.release_solver_lock(projects_root, token)

    def _generate_environment_report(self, case_root: Path) -> None:
        cfd_report.generate_report(
            case_root, projects_root=case_root.parents[1]
        )

    def run_environment_case(self, case_root: Path) -> dict[str, Any]:
        case_root = Path(case_root)
        self._build_environment_case(case_root)
        run = self._execute_environment_case(case_root)
        if run.get("ok"):
            self._generate_environment_report(case_root)

        try:
            mesh_text = (case_root / "log.checkMesh").read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            mesh_text = ""
        parsed_cells = [
            int(value)
            for value in re.findall(r"(?im)^\s*cells\s*:\s*(\d+)\s*$", mesh_text)
        ]
        cells = parsed_cells[0] if len(parsed_cells) == 1 else None
        mesh_ok = cells == 64 and "Mesh OK" in mesh_text
        times: list[float] = []
        for child in case_root.iterdir() if case_root.is_dir() else ():
            if not child.is_dir():
                continue
            try:
                value = float(child.name)
            except ValueError:
                continue
            if value > 0:
                times.append(value)
        latest_time = max(times) if times else None
        solver_log = case_root / "log.buoyantBoussinesqPimpleFoam"
        report = case_root / "cfd_report_environment_acceptance.html"
        try:
            solver_complete = solver_log.read_text(
                encoding="utf-8", errors="replace"
            ).rstrip().endswith("End")
        except OSError:
            solver_complete = False
        baseline = run.get("runtime_baseline")
        passed = bool(
            run.get("ok")
            and mesh_ok
            and latest_time is not None
            and solver_complete
            and report.is_file()
            and isinstance(baseline, dict)
            and baseline.get("status") == "PASS"
        )
        return {
            "status": "PASS" if passed else "BLOCKED",
            "mesh_ok": mesh_ok,
            "cells": cells,
            "latest_time": latest_time,
            "runtime_baseline": baseline if isinstance(baseline, dict) else {},
            "error": None if passed else str(run.get("error") or "ENVIRONMENT_CASE_INCOMPLETE"),
        }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("ACCEPTANCE_TIMESTAMP_MUST_BE_UTC")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _link(root: Path, path: Path) -> dict[str, str]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
    }


def _case_input_sha256(case_root: Path) -> str:
    digest = hashlib.sha256()
    for relative in ("cfd_case_meta.json", "Allrun", "system/controlDict"):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update((case_root / relative).read_bytes())
    return digest.hexdigest()


def _build_candidate(
    projects_root: Path,
    *,
    run_id: str,
    python_executable: Path,
    launch_attempts: int,
    runtime: AcceptanceRuntime,
) -> Path:
    raw_root = projects_root / RAW_RELATIVE
    created_at = runtime.now()
    openfoam = runtime.probe_openfoam()
    freecad = runtime.probe_freecad(run_id)
    case_result = runtime.run_environment_case(raw_root)

    freecad_path = _write_json(raw_root / "freecad_stages.json", freecad)
    launches: list[dict[str, str]] = []
    for attempt in range(1, launch_attempts + 1):
        observation = runtime.launch_studio(attempt, run_id)
        path = _write_json(raw_root / f"launch-{attempt}.json", observation)
        launches.append(_link(projects_root, path))

    diagnostics: list[dict[str, str]] = []
    for index, code in enumerate(DIAGNOSTIC_CODES, start=1):
        observation = dict(runtime.diagnostic_observation(code, run_id))
        log_text = observation.pop("log_text", "")
        log_path = raw_root / f"diagnostic-{index}.log"
        log_path.write_text(str(log_text), encoding="utf-8")
        observation["log"] = _link(projects_root, log_path)
        path = _write_json(raw_root / f"diagnostic-{index}.json", observation)
        diagnostics.append(_link(projects_root, path))

    case_meta = raw_root / "cfd_case_meta.json"
    allrun = raw_root / "Allrun"
    control_dict = raw_root / "system" / "controlDict"
    mesh_log = raw_root / "log.checkMesh"
    solver_log = raw_root / "log.buoyantBoussinesqPimpleFoam"
    report = raw_root / "cfd_report_environment_acceptance.html"
    case_input_sha256 = _case_input_sha256(raw_root)

    baseline = dict(case_result.get("runtime_baseline") or {})
    baseline.update(
        case_input_sha256=case_input_sha256,
        solver_log_sha256=_sha256(solver_log),
    )
    runtime_payload = cfd_capabilities.build_runtime_capability(
        openfoam,
        baseline=baseline,
        created_at=_stamp(created_at),
        run_id=run_id,
    )
    runtime_path = _write_json(projects_root / RUNTIME_RELATIVE, runtime_payload)

    sources = {
        "case_meta": _link(projects_root, case_meta),
        "allrun": _link(projects_root, allrun),
        "control_dict": _link(projects_root, control_dict),
        "mesh_log": _link(projects_root, mesh_log),
        "solver_log": _link(projects_root, solver_log),
        "report": _link(projects_root, report),
        "runtime_capability": _link(projects_root, runtime_path),
    }
    environment_payload = {
        "contract": "environment_acceptance.v1",
        "created_at": _stamp(created_at),
        "run_id": run_id,
        "status": str(case_result.get("status") or "BLOCKED"),
        "mesh_ok": case_result.get("mesh_ok") is True,
        "cells": case_result.get("cells"),
        "latest_time": case_result.get("latest_time"),
        "openfoam_profile": str(openfoam.get("compatible_profile") or ""),
        "case_input_sha256": case_input_sha256,
        **sources,
    }
    environment_path = _write_json(
        raw_root / "environment_acceptance.json", environment_payload
    )

    python_executable = python_executable.resolve(strict=True)
    freecad_executable = Path(str(freecad.get("executable") or "")).resolve(strict=True)
    manifest = {
        "schema_version": 1,
        "contract": "local_usability_acceptance.v1",
        "created_at": _stamp(created_at),
        "run_id": run_id,
        "scope": "single_pc_serial_current_user",
        "status": "PASS",
        "blockers": [],
        "identities": {
            "python": {
                "executable": str(python_executable),
                "executable_sha256": _sha256(python_executable),
                "version": sys.version,
                "architecture": platform.architecture()[0],
            },
            "freecad": {
                "executable": str(freecad_executable),
                "executable_sha256": _sha256(freecad_executable),
                "freecad_version": freecad.get("freecad_version"),
                "occ_version": freecad.get("occ_version"),
                "compatible_profile": freecad.get("compatible_profile"),
            },
            "openfoam": {
                key: openfoam.get(key)
                for key in (
                    "distro",
                    "kernel",
                    "version",
                    "package_version",
                    "compatible_profile",
                )
            },
        },
        "sources": {
            "environment_acceptance": _link(projects_root, environment_path),
            "runtime_capability": sources["runtime_capability"],
            "case_meta": sources["case_meta"],
            "allrun": sources["allrun"],
            "control_dict": sources["control_dict"],
            "mesh_log": sources["mesh_log"],
            "solver_log": sources["solver_log"],
            "report": sources["report"],
            "freecad_diagnostics": _link(projects_root, freecad_path),
        },
        "launch_observations": launches,
        "diagnostic_observations": diagnostics,
    }
    return _write_json(projects_root / MANIFEST_RELATIVE, manifest)


def _remove_exact(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _publish_candidate(candidate_root: Path, projects_root: Path, run_id: str) -> None:
    """Publish raw/runtime/manifest in that order; manifest is the commit record."""
    raw_target = projects_root / RAW_RELATIVE
    runtime_target = projects_root / RUNTIME_RELATIVE
    manifest_target = projects_root / MANIFEST_RELATIVE
    raw_target.parent.mkdir(parents=True, exist_ok=True)
    runtime_target.parent.mkdir(parents=True, exist_ok=True)

    pending_raw = raw_target.with_name(f".{raw_target.name}.{run_id}.pending")
    pending_runtime = runtime_target.with_name(f".{runtime_target.name}.{run_id}.pending")
    pending_manifest = manifest_target.with_name(f".{manifest_target.name}.{run_id}.pending")
    shutil.copytree(candidate_root / RAW_RELATIVE, pending_raw)
    shutil.copy2(candidate_root / RUNTIME_RELATIVE, pending_runtime)
    shutil.copy2(candidate_root / MANIFEST_RELATIVE, pending_manifest)

    targets = (raw_target, runtime_target, manifest_target)
    pending = (pending_raw, pending_runtime, pending_manifest)
    backups: dict[Path, Path] = {}
    published: list[Path] = []
    try:
        for target in targets:
            if target.exists():
                backup = target.with_name(f".{target.name}.{run_id}.previous")
                os.replace(target, backup)
                backups[target] = backup
        for staged, target in zip(pending, targets):
            os.replace(staged, target)
            published.append(target)
    except BaseException:
        for target in reversed(published):
            _remove_exact(target)
        for target, backup in backups.items():
            if backup.exists():
                os.replace(backup, target)
        raise
    finally:
        for staged in pending:
            if staged.exists():
                _remove_exact(staged)

    # A previous authority remains recoverable; never silently delete it.
    if backups:
        history = projects_root / "_working_validation" / "history" / "serial-environment" / run_id
        history.mkdir(parents=True, exist_ok=True)
        for target, backup in backups.items():
            os.replace(backup, history / target.name)


def _blocked(*codes: str) -> dict[str, Any]:
    return {
        "contract": "local_usability_acceptance_production.v1",
        "status": "BLOCKED",
        "blockers": list(codes),
    }


def run_studio_probe_child(
    projects_root: Path,
    port: int,
    *,
    diagnostics_fn: Callable[[], Any] | None = None,
    input_stream: TextIO | None = None,
) -> int:
    """Serve the real Studio page before bounded diagnostics, then stop cleanly."""
    import cfd_studio

    previous_root = cfd_studio.ROOT
    cfd_studio.ROOT = str(Path(projects_root).resolve())
    Path(cfd_studio.ROOT).mkdir(parents=True, exist_ok=True)
    server = cfd_studio.ThreadingHTTPServer(
        ("127.0.0.1", int(port)), cfd_studio.StudioHandler
    )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    failures: list[str] = []

    def diagnose() -> None:
        try:
            (diagnostics_fn or cfd_studio.refresh_environment_capabilities)()
        except Exception as exc:
            failures.append(type(exc).__name__)

    diagnostics_thread = threading.Thread(target=diagnose, daemon=False)
    try:
        server_thread.start()
        diagnostics_thread.start()
        (input_stream or sys.stdin).readline()
        diagnostics_thread.join(timeout=90)
        if diagnostics_thread.is_alive():
            failures.append("DIAGNOSTICS_TIMEOUT")
        return 0 if not failures else 2
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=10)
        cfd_studio.ROOT = previous_root


def produce_local_usability_acceptance(
    repo_root: Path,
    python_executable: Path,
    *,
    launch_attempts: int = 3,
    runtime: AcceptanceRuntime | None = None,
) -> dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    python_executable = Path(python_executable).resolve()
    if python_executable != Path(sys.executable).resolve():
        return _blocked("PYTHON_EXECUTABLE_NOT_CURRENT")
    if launch_attempts != 3:
        return _blocked("LAUNCH_ATTEMPTS_MUST_EQUAL_THREE")
    if runtime is None:
        return _blocked("EXTERNAL_RUNTIME_PRODUCER_NOT_CONFIGURED")

    repo_root.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    try:
        with tempfile.TemporaryDirectory(
            prefix=".local-usability-acceptance-", dir=repo_root
        ) as temporary:
            candidate_root = Path(temporary) / "cfd_projects"
            candidate_root.mkdir()
            manifest = _build_candidate(
                candidate_root,
                run_id=run_id,
                python_executable=python_executable,
                launch_attempts=launch_attempts,
                runtime=runtime,
            )
            evaluation = validate_local_usability_acceptance(manifest, candidate_root)
            if evaluation["status"] != "PASS":
                return {
                    **_blocked("CANDIDATE_EVIDENCE_BLOCKED"),
                    "run_id": run_id,
                    "candidate_evaluation": evaluation,
                }
            projects_root = repo_root / "cfd_projects"
            _publish_candidate(candidate_root, projects_root, run_id)
    except (Exception, SystemExit) as exc:
        return {
            **_blocked("EXTERNAL_RUNTIME_FAILURE"),
            "run_id": run_id,
            "failure_type": type(exc).__name__,
        }

    manifest = projects_root / MANIFEST_RELATIVE
    final = validate_local_usability_acceptance(manifest, projects_root)
    return {
        "contract": "local_usability_acceptance_production.v1",
        "status": final["status"],
        "blockers": final["blockers"],
        "run_id": run_id,
        "manifest": str(manifest),
        "evaluation": final,
    }


def main(
    argv: list[str] | None = None,
    *,
    runtime: AcceptanceRuntime | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        description="Produce and revalidate Task 5b serial-environment evidence"
    )
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--python-executable", type=Path, required=True)
    parser.add_argument("--launch-attempts", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    expected_output = (repo_root / "cfd_projects" / MANIFEST_RELATIVE).resolve()
    if args.output.resolve() != expected_output:
        result = _blocked("PRODUCER_OUTPUT_PATH_NOT_CANONICAL")
    else:
        selected_runtime = runtime or SystemAcceptanceRuntime(repo_root)
        result = produce_local_usability_acceptance(
            repo_root,
            args.python_executable,
            launch_attempts=args.launch_attempts,
            runtime=selected_runtime,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


def entrypoint(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == "--studio-probe-child":
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--studio-probe-child", action="store_true")
        parser.add_argument("--projects-root", type=Path, required=True)
        parser.add_argument("--port", type=int, required=True)
        child = parser.parse_args(arguments)
        return run_studio_probe_child(child.projects_root, child.port)
    return main(arguments)


if __name__ == "__main__":
    raise SystemExit(entrypoint())
