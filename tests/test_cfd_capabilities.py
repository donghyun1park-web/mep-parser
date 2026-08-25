import json
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cfd_capabilities
import cfd_run


class RuntimeCapabilityTests(unittest.TestCase):
    def _probe(self, commands):
        return {
            "wsl_available": True,
            "returncode": 0,
            "error": "",
            "distro": "Ubuntu-24.04",
            "bashrc": "/usr/lib/openfoam/openfoam2606/etc/bashrc",
            "version": "v2606",
            "package_version": "2606.0-1",
            "commands": commands,
            "effective_cpu_count": 8,
            "effective_cpu_source": "WSL nproc",
            "mpi_version": "mpirun (Open MPI) 4.1.6",
        }

    def test_missing_mpi_keeps_serial_runtime_ready_but_blocks_parallel(self):
        commands = {
            name: f"/usr/bin/{name}"
            for name in cfd_run.ALL_OPENFOAM_COMMANDS
            if name not in cfd_run.MPI_COMMANDS
        }
        result = cfd_run._capability_result(
            self._probe(commands), ["Ubuntu-24.04"], "configured"
        )

        self.assertTrue(result["ok"])
        self.assertFalse(result["parallel_ready"])
        self.assertEqual(result["missing_parallel_commands"], list(cfd_run.MPI_COMMANDS))
        self.assertFalse(result["mpi_tools_available"])

    def test_wsl_access_denied_is_not_misreported_as_missing_distribution(self):
        probe = self._probe({})
        probe.update(
            returncode=1,
            distro="",
            bashrc="",
            error="Wsl/Service/CreateInstance/E_ACCESSDENIED",
        )
        result = cfd_run._capability_result(probe, [], "default")

        self.assertEqual(result["status"], "wsl_access_denied")
        self.assertIn("권한", result["fix"])
        self.assertNotIn("install_openfoam2606.bat", result["fix"])

    def test_utf16_wsl_access_denied_output_is_decoded_to_stable_reason_code(self):
        raw_error = "Wsl/Service/CreateInstance/E_ACCESSDENIED".encode("utf-16-le")
        completed = subprocess.CompletedProcess(
            ["wsl", "-e", "bash", "-c", "true"],
            returncode=1,
            stdout=b"",
            stderr=raw_error,
        )
        with mock.patch.object(cfd_run.subprocess, "run", return_value=completed):
            probe = cfd_run._probe_openfoam()

        result = cfd_run._capability_result(probe, [], "default")

        self.assertEqual(probe["error"], "Wsl/Service/CreateInstance/E_ACCESSDENIED")
        self.assertEqual(result["status"], "wsl_access_denied")
        self.assertEqual(result["reason_code"], "WSL_ACCESS_DENIED")
        self.assertFalse(result["ok"])
        self.assertFalse(result["parallel_ready"])
        self.assertIn("WSL_ACCESS_DENIED", result["fix"])
        self.assertIn("이 프로그램은 WSL을 자동으로 재시작하거나 설치하지 않습니다", result["fix"])

    def test_ordinary_wsl_probe_failure_remains_generic(self):
        probe = self._probe({})
        probe.update(returncode=1, distro="", bashrc="", error="WSL endpoint unavailable")

        result = cfd_run._capability_result(probe, [], "default")

        self.assertEqual(result["status"], "wsl_probe_failed")
        self.assertEqual(result["reason_code"], "")

    def test_failed_wsl_listing_does_not_become_fake_distro_names(self):
        completed = subprocess.CompletedProcess(
            ["wsl", "--list", "--quiet"],
            returncode=1,
            stdout="액세스가 거부되었습니다.\n".encode("utf-16-le"),
            stderr=b"",
        )
        with mock.patch.object(cfd_run.subprocess, "run", return_value=completed):
            self.assertEqual(cfd_run._list_wsl_distros(), [])

    def test_runtime_manifest_keeps_unverified_mpi_and_writes_contract(self):
        commands = {
            name: f"/usr/bin/{name}" for name in cfd_run.ALL_OPENFOAM_COMMANDS
        }
        openfoam = cfd_run._capability_result(
            self._probe(commands), ["Ubuntu-24.04"], "configured"
        )
        payload = cfd_capabilities.build_runtime_capability(
            openfoam,
            baseline={
                "status": "PASS",
                "runner_wall_seconds": 12.5,
                "solver_clock_seconds": 8.75,
                "peak_rss_kib": 4096,
                "case_input_sha256": "a" * 64,
                "solver_log_sha256": "b" * 64,
            },
            created_at="2026-08-11T00:00:00+00:00",
        )

        self.assertEqual(payload["contract"], "runtime_capability.v1")
        self.assertTrue(payload["serial_runtime_ready"])
        self.assertTrue(payload["mpi"]["tools_available"])
        self.assertEqual(payload["mpi"]["execution_smoke"], "NOT_RUN")
        self.assertFalse(payload["parallel_runtime_ready"])
        self.assertEqual(payload["cpu"]["effective_logical_count"], 8)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime_capability.v1.json"
            cfd_capabilities.write_runtime_capability(path, payload)
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved, payload)

    def test_invalid_wsl_cpu_is_not_replaced_with_host_cpu(self):
        openfoam = {
            "ok": True,
            "status": "ready",
            "commands": {},
            "effective_cpu_count": 0,
            "effective_cpu_source": "WSL nproc",
        }
        payload = cfd_capabilities.build_runtime_capability(openfoam)
        self.assertIsNone(payload["cpu"]["effective_logical_count"])
        self.assertFalse(payload["parallel_runtime_ready"])

    def test_runtime_baseline_reads_solver_clock_memory_and_input_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            (case / "system").mkdir()
            (case / "cfd_case_meta.json").write_text("{}", encoding="utf-8")
            (case / "Allrun").write_text("#!/bin/bash\n", encoding="utf-8")
            (case / "system" / "controlDict").write_text(
                "endTime 1000;\n", encoding="utf-8"
            )
            (case / "log.buoyantBoussinesqSimpleFoam").write_text(
                "ExecutionTime = 1.25 s  ClockTime = 2.5 s\n",
                encoding="utf-8",
            )
            (case / "log.runner").write_text(
                "Maximum resident set size (kbytes): 2048\n", encoding="utf-8"
            )
            baseline = cfd_run.runtime_baseline(case, 3.75)

        self.assertEqual(baseline["status"], "PASS")
        self.assertEqual(baseline["runner_wall_seconds"], 3.75)
        self.assertEqual(baseline["solver_clock_seconds"], 2.5)
        self.assertEqual(baseline["peak_rss_kib"], 2048)
        self.assertEqual(len(baseline["case_input_sha256"]), 64)
        self.assertEqual(len(baseline["solver_log_sha256"]), 64)

    def test_runtime_capability_recording_keeps_runtime_failure_honest(self):
        openfoam = {
            "ok": False,
            "status": "wsl_missing",
            "commands": {},
            "effective_cpu_count": None,
            "effective_cpu_source": "WSL nproc",
        }
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(cfd_run, "diagnose_openfoam", return_value=openfoam):
            path = Path(tmp) / "runtime_capability.v1.json"
            result = cfd_run.record_runtime_capability(path)
            saved = json.loads(path.read_text(encoding="utf-8"))

        self.assertTrue(result["ok"])
        self.assertFalse(saved["serial_runtime_ready"])
        self.assertEqual(saved["serial_baseline"]["status"], "NOT_RUN")

    def test_blocked_mpi_smoke_is_preserved_and_keeps_parallel_disabled(self):
        commands = {
            name: f"/usr/bin/{name}" for name in cfd_run.ALL_OPENFOAM_COMMANDS
        }
        openfoam = cfd_run._capability_result(
            self._probe(commands), ["Ubuntu-24.04"], "configured"
        )
        payload = cfd_capabilities.build_runtime_capability(
            openfoam,
            mpi_smoke={
                "status": "BLOCKED",
                "reason_code": "MPI_RANK_SPAWN_HANG",
                "artifact_path": "cfd_projects/_release_evidence/mpi_runtime_smoke.v1.json",
                "artifact_sha256": "c" * 64,
            },
        )

        self.assertEqual(payload["mpi"]["execution_smoke"], "BLOCKED")
        self.assertEqual(payload["mpi"]["reason_code"], "MPI_RANK_SPAWN_HANG")
        self.assertEqual(payload["mpi"]["artifact_sha256"], "c" * 64)
        self.assertFalse(payload["parallel_runtime_ready"])

    def test_passed_mpi_smoke_enables_parallel_only_with_static_prerequisites(self):
        commands = {
            name: f"/usr/bin/{name}" for name in cfd_run.ALL_OPENFOAM_COMMANDS
        }
        openfoam = cfd_run._capability_result(
            self._probe(commands), ["Ubuntu-24.04"], "configured"
        )
        payload = cfd_capabilities.build_runtime_capability(
            openfoam,
            mpi_smoke={
                "status": "PASS", "reason_code": "",
                "artifact_path": "cfd_projects/_release_evidence/mpi_runtime_smoke.v1.json",
                "artifact_sha256": "d" * 64,
                "identity": {
                    "distro": "Ubuntu-24.04", "kernel": "kernel",
                    "mpirun_path": "/usr/bin/mpirun",
                    "mpirun_version": "mpirun (Open MPI) 4.1.6",
                    "ompi_info_version": "Open MPI v4.1.6",
                    "effective_cpu_count": 8,
                },
            },
        )

        self.assertEqual(payload["mpi"]["execution_smoke"], "PASS")
        self.assertTrue(payload["parallel_runtime_ready"])

    def test_passed_mpi_without_artifact_identity_never_enables_parallel(self):
        commands = {
            name: f"/usr/bin/{name}" for name in cfd_run.ALL_OPENFOAM_COMMANDS
        }
        openfoam = cfd_run._capability_result(
            self._probe(commands), ["Ubuntu-24.04"], "configured"
        )
        payload = cfd_capabilities.build_runtime_capability(
            openfoam, mpi_smoke={"status": "PASS"}
        )

        self.assertFalse(payload["parallel_runtime_ready"])

    def test_runtime_manifest_preserves_smoke_identity_for_later_freshness_checks(self):
        commands = {
            name: f"/usr/bin/{name}" for name in cfd_run.ALL_OPENFOAM_COMMANDS
        }
        openfoam = cfd_run._capability_result(
            self._probe(commands), ["Ubuntu-24.04"], "configured"
        )
        identity = {
            "distro": "Ubuntu-24.04",
            "kernel": "6.18.33.2-microsoft-standard-WSL2",
            "mpirun_path": "/usr/bin/mpirun",
            "mpirun_version": "mpirun (Open MPI) 4.1.6",
            "ompi_info_version": "Open MPI v4.1.6",
            "effective_cpu_count": 8,
        }
        payload = cfd_capabilities.build_runtime_capability(
            openfoam, mpi_smoke={"status": "PASS", "identity": identity}
        )

        self.assertEqual(payload["mpi"]["smoke_identity"], identity)


class StagedFreeCADCapabilityTests(unittest.TestCase):
    def test_non_finite_or_non_positive_stage_timeout_is_rejected_before_discovery(self):
        for timeout in (0, -1, float("nan"), float("inf")):
            with self.subTest(timeout=timeout), mock.patch.object(
                cfd_capabilities, "select_freecadcmd"
            ) as select:
                with self.assertRaisesRegex(ValueError, "FREECAD_STAGE_TIMEOUT_INVALID"):
                    cfd_capabilities.diagnose_freecad_stages(
                        Path(r"C:\FreeCAD\FreeCADCmd.exe"),
                        per_stage_timeout_s=timeout,
                    )
                select.assert_not_called()

    def test_missing_runtime_blocks_at_discovery_without_launching(self):
        with mock.patch.object(cfd_capabilities.subprocess, "run") as run:
            result = cfd_capabilities.diagnose_freecad_stages(
                Path(r"C:\missing\FreeCADCmd.exe"), per_stage_timeout_s=0.25
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "missing")
        self.assertEqual(result["failed_stage"], "discovery")
        self.assertEqual(result["stages"][0]["reason_code"], "FREECAD_EXECUTABLE_MISSING")
        self.assertEqual([row["status"] for row in result["stages"]], [
            "BLOCKED", "NOT_RUN", "NOT_RUN", "NOT_RUN",
        ])
        run.assert_not_called()

    def test_explicit_staged_probe_never_calls_potentially_stalled_auto_selector(self):
        with mock.patch.object(
            cfd_capabilities, "select_freecadcmd",
            side_effect=TimeoutError("auto discovery stalled"),
        ) as select:
            result = cfd_capabilities.diagnose_freecad_stages(
                Path(r"C:\missing\FreeCADCmd.exe"), per_stage_timeout_s=0.25
            )

        self.assertEqual(result["status"], "missing")
        self.assertEqual(result["failed_stage"], "discovery")
        select.assert_not_called()

    def test_each_runtime_stage_is_bounded_and_ready_result_binds_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "FreeCADCmd.exe"
            executable.write_bytes(b"freecad-binary")
            marker = "MEP_CFD_FREECAD_STAGE:"
            payloads = [
                {
                    "stage": "imports", "ok": True,
                    "freecad_version": "1.1.1", "occ_version": "7.8.1",
                    "revision": "20260414", "python_version": "3.11.14",
                    "modules": {
                        name: True for name in (
                            "FreeCAD", "Part", "Draft", "Arch", "Mesh", "MeshPart",
                            "BOPTools.SplitAPI",
                        )
                    },
                },
                {
                    "stage": "boolean", "ok": True, "valid": True,
                    "solid_count": 1, "volume_mm3": 239250000000.0,
                    "relative_volume_error": 0.0,
                },
                {
                    "stage": "tessellation", "ok": True,
                    "vertices": 8, "facets": 12,
                },
            ]
            completed = [
                subprocess.CompletedProcess(
                    [str(executable)], 0,
                    stdout=marker + json.dumps(payload) + "\n", stderr="",
                )
                for payload in payloads
            ]
            with mock.patch.object(
                cfd_capabilities.subprocess, "run", side_effect=completed
            ) as run:
                result = cfd_capabilities.diagnose_freecad_stages(
                    executable, per_stage_timeout_s=1.25
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "ready")
        self.assertIsNone(result["failed_stage"])
        self.assertEqual(result["compatible_profile"], "freecad-1.1.1-occ-7.8.1")
        self.assertEqual(
            result["executable_sha256"], hashlib.sha256(b"freecad-binary").hexdigest()
        )
        self.assertEqual([row["status"] for row in result["stages"]], [
            "PASS", "PASS", "PASS", "PASS",
        ])
        self.assertEqual(run.call_count, 3)
        self.assertTrue(all(call.kwargs["timeout"] == 1.25 for call in run.call_args_list))

    def test_stage_timeout_names_exact_failed_stage_and_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "FreeCADCmd.exe"
            executable.write_bytes(b"freecad-binary")
            with mock.patch.object(
                cfd_capabilities.subprocess, "run",
                side_effect=subprocess.TimeoutExpired([str(executable)], 0.5),
            ) as run:
                result = cfd_capabilities.diagnose_freecad_stages(
                    executable, per_stage_timeout_s=0.5
                )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "timeout")
        self.assertEqual(result["failed_stage"], "imports")
        self.assertEqual(result["stages"][1]["reason_code"], "FREECAD_IMPORTS_TIMEOUT")
        self.assertEqual([row["status"] for row in result["stages"]], [
            "PASS", "BLOCKED", "NOT_RUN", "NOT_RUN",
        ])
        self.assertEqual(run.call_count, 1)

    def test_non_freecad_executable_identity_is_rejected_without_launch(self):
        with mock.patch.object(cfd_capabilities.subprocess, "run") as run:
            result = cfd_capabilities.diagnose_freecad_stages(
                Path(sys.executable), per_stage_timeout_s=0.5
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["failed_stage"], "discovery")
        self.assertEqual(
            result["stages"][0]["reason_code"],
            "FREECAD_EXECUTABLE_IDENTITY_INVALID",
        )
        run.assert_not_called()

    def test_boolean_and_tessellation_pass_claims_are_recomputed(self):
        imports = {
            "stage": "imports", "ok": True,
            "freecad_version": "1.1.1", "occ_version": "7.8.1",
            "revision": "20260414", "python_version": "3.11.14",
            "modules": {
                name: True for name in (
                    "FreeCAD", "Part", "Draft", "Arch", "Mesh", "MeshPart",
                    "BOPTools.SplitAPI",
                )
            },
        }
        valid_boolean = {
            "stage": "boolean", "ok": True, "valid": True,
            "solid_count": 1, "volume_mm3": 239250000000.0,
            "relative_volume_error": 0.0,
        }
        valid_tessellation = {
            "stage": "tessellation", "ok": True, "vertices": 8, "facets": 12,
        }
        cases = [
            (
                "boolean",
                dict(valid_boolean, volume_mm3=1.0, relative_volume_error=0.0),
                valid_tessellation,
            ),
            ("tessellation", valid_boolean, dict(valid_tessellation, facets=0)),
        ]
        marker = "MEP_CFD_FREECAD_STAGE:"
        for failed_stage, boolean, tessellation in cases:
            with self.subTest(failed_stage=failed_stage), tempfile.TemporaryDirectory() as tmp:
                executable = Path(tmp) / "FreeCADCmd.exe"
                executable.write_bytes(b"freecad-binary")
                completed = [
                    subprocess.CompletedProcess(
                        [str(executable)], 0,
                        stdout=marker + json.dumps(payload) + "\n", stderr="",
                    )
                    for payload in (imports, boolean, tessellation)
                ]
                with mock.patch.object(
                    cfd_capabilities.subprocess, "run", side_effect=completed
                ):
                    result = cfd_capabilities.diagnose_freecad_stages(
                        executable, per_stage_timeout_s=0.5
                    )

            self.assertFalse(result["ok"])
            self.assertEqual(result["failed_stage"], failed_stage)


if __name__ == "__main__":
    unittest.main()
