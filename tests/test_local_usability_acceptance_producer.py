import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path


DIAGNOSTIC_CODES = (
    "WSL_UNAVAILABLE",
    "FREECAD_UNAVAILABLE",
    "INVALID_GEOMETRY",
    "MESH_FAILURE",
    "SOLVER_OR_DISK_FAILURE",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CompleteRuntime:
    """Real filesystem fixture; only slow external processes are substituted."""

    def __init__(self, root: Path):
        self.root = root
        self.observed_at = datetime.now(timezone.utc).replace(microsecond=0)
        self.freecad = root / "installed" / "FreeCADCmd.exe"
        self.freecad.parent.mkdir(parents=True)
        self.freecad.write_bytes(b"independent-freecad-executable\n")

    def now(self) -> datetime:
        return self.observed_at

    def probe_openfoam(self) -> dict:
        return {
            "ok": True,
            "status": "ready",
            "distro": "Ubuntu-24.04",
            "kernel": "6.18.33.2-microsoft-standard-WSL2",
            "version": "v2606",
            "package_version": "2606.0-1",
            "compatible_profile": "openfoam-v2606",
            "effective_cpu_count": 8,
            "effective_cpu_source": "WSL nproc",
            "parallel_ready": False,
            "commands": {
                "buoyantBoussinesqPimpleFoam": (
                    "/usr/lib/openfoam/openfoam2606/platforms/"
                    "linux64GccDPInt32Opt/bin/buoyantBoussinesqPimpleFoam"
                ),
            },
        }

    def probe_freecad(self, run_id: str) -> dict:
        return {
            "schema_version": 1,
            "contract": "freecad_staged_diagnostics.v1",
            "checked_at": self.observed_at.isoformat().replace("+00:00", "Z"),
            "run_id": run_id,
            "ok": True,
            "status": "ready",
            "failed_stage": None,
            "summary": "FreeCAD 단계별 형상 환경이 준비되었습니다.",
            "fix": "",
            "selection": "explicit",
            "executable": str(self.freecad.resolve()),
            "executable_sha256": _sha256(self.freecad),
            "freecad_version": "1.1.1",
            "revision": "20260414",
            "python_version": "3.11.14",
            "occ_version": "7.8.1",
            "compatible_profile": "freecad-1.1.1-occ-7.8.1",
            "stages": [
                {
                    "id": "discovery",
                    "status": "PASS",
                    "reason_code": "",
                    "details": {"selection": "explicit"},
                },
                {
                    "id": "imports",
                    "status": "PASS",
                    "reason_code": "",
                    "details": {
                        "stage": "imports",
                        "ok": True,
                        "freecad_version": "1.1.1",
                        "revision": "20260414",
                        "python_version": "3.11.14",
                        "occ_version": "7.8.1",
                        "modules": {
                            name: True
                            for name in (
                                "FreeCAD",
                                "Part",
                                "Draft",
                                "Arch",
                                "Mesh",
                                "MeshPart",
                                "BOPTools.SplitAPI",
                            )
                        },
                    },
                },
                {
                    "id": "boolean",
                    "status": "PASS",
                    "reason_code": "",
                    "details": {
                        "stage": "boolean",
                        "ok": True,
                        "valid": True,
                        "solid_count": 1,
                        "volume_mm3": 239250000000.0,
                        "relative_volume_error": 0.0,
                    },
                },
                {
                    "id": "tessellation",
                    "status": "PASS",
                    "reason_code": "",
                    "details": {
                        "stage": "tessellation",
                        "ok": True,
                        "vertices": 8,
                        "facets": 12,
                    },
                },
            ],
        }

    def run_environment_case(self, case_root: Path) -> dict:
        (case_root / "system").mkdir(parents=True)
        (case_root / "cfd_case_meta.json").write_text(
            '{"contract":"environment_acceptance_case.v1","cells":64}\n',
            encoding="utf-8",
        )
        (case_root / "Allrun").write_text(
            "#!/bin/sh\nbuoyantBoussinesqPimpleFoam\n", encoding="utf-8"
        )
        (case_root / "system" / "controlDict").write_text(
            "application buoyantBoussinesqPimpleFoam;\nendTime 1;\n",
            encoding="utf-8",
        )
        (case_root / "log.checkMesh").write_text(
            "Mesh stats\n    cells: 64\nMesh OK.\n", encoding="utf-8"
        )
        solver_log = case_root / "log.buoyantBoussinesqPimpleFoam"
        solver_log.write_text(
            "Time = 0.5\nTime = 1\nExecutionTime = 1 s  ClockTime = 1 s\nEnd\n",
            encoding="utf-8",
        )
        (case_root / "cfd_report_environment_acceptance.html").write_text(
            "<!doctype html><title>environment acceptance</title>\n",
            encoding="utf-8",
        )
        return {
            "status": "PASS",
            "mesh_ok": True,
            "cells": 64,
            "latest_time": 1.0,
            "runtime_baseline": {
                "status": "PASS",
                "runner_wall_seconds": 2.0,
                "solver_clock_seconds": 1.0,
                "peak_rss_kib": 2048,
            },
        }

    def launch_studio(self, attempt: int, run_id: str) -> dict:
        started = self.observed_at + timedelta(milliseconds=attempt * 10)
        return {
            "contract": "studio_launch_observation.v1",
            "run_id": run_id,
            "attempt": attempt,
            "process_started_at": started.isoformat(),
            "http_ready_at": (started + timedelta(seconds=1)).isoformat(),
            "dom_ready_at": (started + timedelta(seconds=2)).isoformat(),
            "required_dom_marker": "MEP CFD Studio",
            "status": "PASS",
        }

    def diagnostic_observation(self, code: str, run_id: str) -> dict:
        assert code in DIAGNOSTIC_CODES
        return {
            "contract": "actionable_diagnostic_observation.v1",
            "run_id": run_id,
            "observed_at": self.observed_at.isoformat(),
            "code": code,
            "cause_ko": "환경 원인을 확인했습니다.",
            "impact_ko": "현재 계산을 시작할 수 없습니다.",
            "next_action_ko": "환경 진단 로그를 확인하고 다시 검사하세요.",
            "raw_traceback_count": 0,
            "status": "PASS",
            "log_text": code + "\n",
        }


def test_producer_publishes_one_hash_bound_bundle_that_the_validator_passes(tmp_path):
    """Dropping a source link or using different run IDs must break this test."""
    from scripts.produce_local_usability_acceptance import (
        produce_local_usability_acceptance,
    )
    from scripts.local_usability_acceptance import (
        validate_local_usability_acceptance,
    )

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    runtime = CompleteRuntime(tmp_path)

    produced = produce_local_usability_acceptance(
        repo_root,
        Path(__import__("sys").executable),
        launch_attempts=3,
        runtime=runtime,
    )

    projects_root = repo_root / "cfd_projects"
    manifest = projects_root / "_working_validation" / "local_usability_acceptance.json"
    evaluated = validate_local_usability_acceptance(manifest, projects_root)
    assert produced["status"] == "PASS", produced
    assert evaluated["status"] == "PASS", evaluated
    assert produced["run_id"]
    assert manifest.is_file()
    assert (
        projects_root / "_working_validation" / "runtime_capability.v1.json"
    ).is_file()
    assert (projects_root / "_system" / "environment_acceptance" / "log.checkMesh").is_file()


def test_external_runtime_failure_is_blocked_without_replacing_current_authority(tmp_path):
    """An uncaught solver exception must not erase or partially replace a prior run."""
    from scripts.produce_local_usability_acceptance import (
        produce_local_usability_acceptance,
    )

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    current = produce_local_usability_acceptance(
        repo_root,
        Path(__import__("sys").executable),
        runtime=CompleteRuntime(tmp_path / "first"),
    )
    assert current["status"] == "PASS", current
    manifest = repo_root / "cfd_projects" / "_working_validation" / "local_usability_acceptance.json"
    before = manifest.read_bytes()

    class ExplodingRuntime(CompleteRuntime):
        def run_environment_case(self, case_root: Path) -> dict:
            raise RuntimeError("solver path contains private diagnostic details")

    blocked = produce_local_usability_acceptance(
        repo_root,
        Path(__import__("sys").executable),
        runtime=ExplodingRuntime(tmp_path / "second"),
    )

    assert blocked["status"] == "BLOCKED"
    assert blocked["blockers"] == ["EXTERNAL_RUNTIME_FAILURE"]
    assert blocked["failure_type"] == "RuntimeError"
    assert "private" not in str(blocked)
    assert manifest.read_bytes() == before


def test_producer_cli_accepts_the_task_5b_plan_arguments(tmp_path, capsys):
    """Removing any documented producer flag must fail this executable contract."""
    from scripts.produce_local_usability_acceptance import main

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    output = repo_root / "cfd_projects" / "_working_validation" / "local_usability_acceptance.json"
    exit_code = main(
        [
            "--repo-root",
            str(repo_root),
            "--python-executable",
            str(Path(__import__("sys").executable)),
            "--launch-attempts",
            "3",
            "--output",
            str(output),
        ],
        runtime=CompleteRuntime(tmp_path / "cli-runtime"),
    )

    printed = __import__("json").loads(capsys.readouterr().out)
    assert exit_code == 0
    assert printed["status"] == "PASS"
    assert output.is_file()


def test_system_runtime_builds_and_recomputes_the_exact_64_cell_pimple_case(tmp_path):
    """A requested 64-cell config must not substitute for parsed checkMesh evidence."""
    from scripts.produce_local_usability_acceptance import SystemAcceptanceRuntime

    class SolverFixtureRuntime(SystemAcceptanceRuntime):
        def _execute_environment_case(self, case_root: Path) -> dict:
            (case_root / "log.checkMesh").write_text(
                "Mesh stats\n    cells: 64\nMesh OK.\n", encoding="utf-8"
            )
            (case_root / "log.buoyantBoussinesqPimpleFoam").write_text(
                "Time = 0.5\nTime = 1\nExecutionTime = 1 s  ClockTime = 1 s\nEnd\n",
                encoding="utf-8",
            )
            (case_root / "1").mkdir()
            return {
                "ok": True,
                "error": None,
                "runtime_baseline": {
                    "status": "PASS",
                    "runner_wall_seconds": 2.0,
                    "solver_clock_seconds": 1.0,
                    "peak_rss_kib": 2048,
                },
            }

        def _generate_environment_report(self, case_root: Path) -> None:
            (case_root / "cfd_report_environment_acceptance.html").write_text(
                "<!doctype html><title>environment acceptance</title>\n",
                encoding="utf-8",
            )

    case_root = tmp_path / "projects" / "_system" / "environment_acceptance"
    result = SolverFixtureRuntime(tmp_path).run_environment_case(case_root)

    control = (case_root / "system" / "controlDict").read_text(encoding="utf-8")
    allrun = (case_root / "Allrun").read_text(encoding="utf-8")
    block_mesh = (case_root / "system" / "blockMeshDict").read_text(encoding="utf-8")
    assert result["status"] == "PASS", result
    assert result["cells"] == 64
    assert result["latest_time"] == 1.0
    assert result["runtime_baseline"]["peak_rss_kib"] == 2048
    assert "application buoyantBoussinesqPimpleFoam;" in control
    assert "adjustTimeStep no;" in control
    assert "\nbuoyantBoussinesqPimpleFoam > log.buoyantBoussinesqPimpleFoam" in allrun
    assert "(4 4 4)" in block_mesh


def test_system_runtime_emits_five_actionable_korean_diagnostic_fixtures(tmp_path):
    """Replacing actionable Korean guidance with a traceback must fail this test."""
    from scripts.produce_local_usability_acceptance import SystemAcceptanceRuntime

    runtime = SystemAcceptanceRuntime(tmp_path)
    rows = [runtime.diagnostic_observation(code, "run-001") for code in DIAGNOSTIC_CODES]

    assert [row["code"] for row in rows] == list(DIAGNOSTIC_CODES)
    assert all(row["run_id"] == "run-001" for row in rows)
    assert all(row["status"] == "PASS" for row in rows)
    assert all(row["raw_traceback_count"] == 0 for row in rows)
    assert all("Traceback" not in row["log_text"] for row in rows)
    for row in rows:
        assert any("가" <= char <= "힣" for char in row["cause_ko"])
        assert any("가" <= char <= "힣" for char in row["impact_ko"])
        assert any("가" <= char <= "힣" for char in row["next_action_ko"])


def test_system_runtime_binds_explicit_freecad_and_openfoam_probes_to_the_run(
    tmp_path, monkeypatch
):
    """Falling back to an unrecorded FreeCAD path must break this identity contract."""
    import cfd_capabilities
    import cfd_run
    from scripts.produce_local_usability_acceptance import SystemAcceptanceRuntime

    executable = tmp_path / "FreeCADCmd.exe"
    executable.write_bytes(b"freecad\n")
    monkeypatch.setenv("MEP_CFD_FREECADCMD", str(executable))
    monkeypatch.setattr(
        cfd_run,
        "diagnose_openfoam",
        lambda: {
            "ok": True,
            "status": "ready",
            "compatible_profile": "openfoam-v2606",
        },
    )

    def staged_probe(path: Path, *, per_stage_timeout_s: float) -> dict:
        assert path == executable.resolve()
        assert per_stage_timeout_s == 20
        return {
            "contract": "freecad_staged_diagnostics.v1",
            "selection": "explicit",
            "run_id": "probe-owned-id",
        }

    monkeypatch.setattr(cfd_capabilities, "diagnose_freecad_stages", staged_probe)
    runtime = SystemAcceptanceRuntime(tmp_path)

    openfoam = runtime.probe_openfoam()
    freecad = runtime.probe_freecad("producer-run-001")

    assert openfoam["compatible_profile"] == "openfoam-v2606"
    assert freecad["selection"] == "explicit"
    assert freecad["run_id"] == "producer-run-001"


def test_studio_launch_observation_uses_a_real_child_http_probe_and_clean_shutdown(tmp_path):
    """Self-declared readiness without a fetched DOM marker must fail this test."""
    import sys
    from scripts.produce_local_usability_acceptance import SystemAcceptanceRuntime

    child = tmp_path / "studio_child.py"
    clean_marker = tmp_path / "clean-shutdown.txt"
    child.write_text(
        """import sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

root, port, clean = Path(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3])
root.mkdir(parents=True, exist_ok=True)
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'<!doctype html><title>MEP CFD Studio</title>'
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *_args):
        pass
server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
(root / 'capability_manifest.json').write_text('{}\\n', encoding='utf-8')
worker = threading.Thread(target=server.serve_forever)
worker.start()
sys.stdin.readline()
server.shutdown()
server.server_close()
worker.join()
clean.write_text('clean\\n', encoding='utf-8')
""",
        encoding="utf-8",
    )

    class ChildFixtureRuntime(SystemAcceptanceRuntime):
        def _studio_child_command(self, launch_root: Path, port: int) -> list[str]:
            return [
                sys.executable,
                str(child),
                str(launch_root),
                str(port),
                str(clean_marker),
            ]

    observation = ChildFixtureRuntime(tmp_path).launch_studio(1, "run-001")

    started = datetime.fromisoformat(observation["process_started_at"])
    ready = datetime.fromisoformat(observation["http_ready_at"])
    dom = datetime.fromisoformat(observation["dom_ready_at"])
    assert observation == {
        "contract": "studio_launch_observation.v1",
        "run_id": "run-001",
        "attempt": 1,
        "process_started_at": observation["process_started_at"],
        "http_ready_at": observation["http_ready_at"],
        "dom_ready_at": observation["dom_ready_at"],
        "required_dom_marker": "MEP CFD Studio",
        "status": "PASS",
    }
    assert started <= ready <= dom
    assert (dom - started).total_seconds() <= 10
    assert clean_marker.read_text(encoding="utf-8") == "clean\n"


def test_studio_probe_child_starts_the_real_handler_before_background_diagnostics(
    tmp_path
):
    """Running diagnostics before the HTTP server must deadlock this ordering test."""
    import socket
    import threading
    from urllib.request import urlopen
    from scripts.produce_local_usability_acceptance import run_studio_probe_child

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    capability = tmp_path / "capability_manifest.json"
    fetched = []

    def diagnostics() -> None:
        with urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:
            fetched.append(response.read().decode("utf-8"))
        capability.write_text("{}\n", encoding="utf-8")

    class WaitForDiagnosticsInput:
        def readline(self) -> str:
            assert threading.Event().wait(0.05) is False
            deadline = __import__("time").monotonic() + 3
            while __import__("time").monotonic() < deadline:
                if capability.is_file():
                    return "\n"
                __import__("time").sleep(0.01)
            raise AssertionError("background diagnostics did not finish")

    exit_code = run_studio_probe_child(
        tmp_path,
        port,
        diagnostics_fn=diagnostics,
        input_stream=WaitForDiagnosticsInput(),
    )

    assert exit_code == 0
    assert fetched and "MEP CFD Studio" in fetched[0]


def test_producer_script_can_be_executed_directly_from_the_repository_root():
    """Removing the repo-root import bootstrap must reproduce ModuleNotFoundError."""
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            str(repo / "scripts" / "produce_local_usability_acceptance.py"),
            "--help",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--repo-root" in completed.stdout
    assert "--python-executable" in completed.stdout


def test_entrypoint_routes_the_internal_studio_probe_child_mode(tmp_path, monkeypatch):
    """Treating child flags as producer flags must fail this dispatch contract."""
    import scripts.produce_local_usability_acceptance as producer

    seen = {}

    def child(projects_root: Path, port: int) -> int:
        seen.update(projects_root=projects_root, port=port)
        return 0

    monkeypatch.setattr(producer, "run_studio_probe_child", child)
    exit_code = producer.entrypoint(
        [
            "--studio-probe-child",
            "--projects-root",
            str(tmp_path),
            "--port",
            "54321",
        ]
    )

    assert exit_code == 0
    assert seen == {"projects_root": tmp_path, "port": 54321}


def test_cli_selects_the_current_pc_runtime_when_no_test_runtime_is_injected(
    tmp_path, monkeypatch, capsys
):
    """Leaving the production CLI dependent on test injection must fail this test."""
    import scripts.produce_local_usability_acceptance as producer

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.setattr(
        producer,
        "SystemAcceptanceRuntime",
        lambda _repo_root: CompleteRuntime(tmp_path / "default-runtime"),
    )
    output = repo_root / "cfd_projects" / "_working_validation" / "local_usability_acceptance.json"

    exit_code = producer.main(
        [
            "--repo-root",
            str(repo_root),
            "--python-executable",
            str(Path(__import__("sys").executable)),
            "--launch-attempts",
            "3",
            "--output",
            str(output),
        ]
    )

    result = __import__("json").loads(capsys.readouterr().out)
    assert exit_code == 0
    assert result["status"] == "PASS"


def test_system_runtime_does_not_start_the_solver_when_the_project_lock_is_busy(
    tmp_path, monkeypatch
):
    """Calling run_case despite a busy project lock must fail this safety test."""
    import cfd_gci_job
    import cfd_run
    from scripts.produce_local_usability_acceptance import SystemAcceptanceRuntime

    case_root = tmp_path / "projects" / "_system" / "environment_acceptance"
    case_root.mkdir(parents=True)
    monkeypatch.setattr(
        cfd_gci_job,
        "acquire_solver_lock",
        lambda _projects_root: (None, {"pid": 4321}),
    )

    def forbidden_run(*_args, **_kwargs):
        raise AssertionError("solver must not start while the shared lock is busy")

    monkeypatch.setattr(cfd_run, "run_case", forbidden_run)
    result = SystemAcceptanceRuntime(tmp_path)._execute_environment_case(case_root)

    assert result["ok"] is False
    assert result["error"] == "CFD_SOLVER_BUSY"


def test_producer_gives_the_case_builder_a_fresh_nonexistent_staging_path(tmp_path):
    """Pre-creating the case directory must reproduce build_case's overwrite guard."""
    from scripts.produce_local_usability_acceptance import (
        produce_local_usability_acceptance,
    )

    class FreshCaseRuntime(CompleteRuntime):
        def run_environment_case(self, case_root: Path) -> dict:
            assert not case_root.exists(), "producer pre-created the protected case directory"
            return super().run_environment_case(case_root)

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    result = produce_local_usability_acceptance(
        repo_root,
        Path(__import__("sys").executable),
        runtime=FreshCaseRuntime(tmp_path / "runtime"),
    )

    assert result["status"] == "PASS", result


def test_case_builder_system_exit_is_reported_as_blocked_without_raw_details(tmp_path):
    """Legacy builder validation exits must not bypass the producer's fail-closed result."""
    from scripts.produce_local_usability_acceptance import (
        produce_local_usability_acceptance,
    )

    class RejectingBuilderRuntime(CompleteRuntime):
        def run_environment_case(self, case_root: Path) -> dict:
            raise SystemExit("private path and raw builder detail")

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    result = produce_local_usability_acceptance(
        repo_root,
        Path(__import__("sys").executable),
        runtime=RejectingBuilderRuntime(tmp_path / "runtime"),
    )

    assert result["status"] == "BLOCKED"
    assert result["blockers"] == ["EXTERNAL_RUNTIME_FAILURE"]
    assert result["failure_type"] == "SystemExit"
    assert "private" not in str(result)
