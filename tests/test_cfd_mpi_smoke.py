import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import cfd_run


class MpiRuntimeSmokeRunnerTests(unittest.TestCase):
    def _identity(self):
        return {
            "distro": "Ubuntu-24.04",
            "kernel": "6.18.33.2-microsoft-standard-WSL2",
            "mpirun_path": "/usr/bin/mpirun",
            "mpirun_version": "mpirun (Open MPI) 4.1.6",
            "ompi_info_version": "Open MPI v4.1.6",
            "effective_cpu_count": 10,
        }

    def test_timeout_stops_after_first_rank_and_persists_blocked_evidence(self):
        timed_out = {
            "ranks": 2,
            "returncode": None,
            "elapsed_seconds": 10.0,
            "timed_out": True,
            "hostname_line_count": 0,
            "hostname_lines": [],
            "cleanup": {"status": "CLEAN"},
        }
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(cfd_run, "_mpi_smoke_identity", return_value=self._identity()), \
             mock.patch.object(cfd_run, "_mpi_smoke_active_processes", return_value=[]), \
             mock.patch.object(cfd_run, "_run_mpi_smoke_trial", return_value=timed_out) as trial:
            target = Path(tmp) / "mpi_runtime_smoke.v1.json"
            result = cfd_run.run_mpi_runtime_smoke(target, distro="Ubuntu-24.04")
            saved = json.loads(target.read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason_code"], "MPI_RANK_SPAWN_HANG")
        self.assertEqual(saved["status"], "BLOCKED")
        self.assertEqual(saved["trials"], [timed_out])
        self.assertEqual(trial.call_count, 1)
        self.assertEqual(trial.call_args.kwargs["ranks"], 2)
        self.assertEqual(len(result["artifact_sha256"]), 64)

    def test_existing_mpi_process_blocks_without_starting_a_trial(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(cfd_run, "_mpi_smoke_identity", return_value=self._identity()), \
             mock.patch.object(cfd_run, "_mpi_smoke_active_processes", return_value=["72 mpirun"]), \
             mock.patch.object(cfd_run, "_run_mpi_smoke_trial") as trial:
            target = Path(tmp) / "mpi_runtime_smoke.v1.json"
            result = cfd_run.run_mpi_runtime_smoke(target, distro="Ubuntu-24.04")
            saved = json.loads(target.read_text(encoding="utf-8"))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason_code"], "MPI_PREEXISTING_PROCESS")
        self.assertEqual(saved["trials"], [])
        trial.assert_not_called()

    def test_wrapper_uses_a_private_session_and_never_stops_the_whole_distro(self):
        token = "a" * 32
        script = cfd_run._mpi_smoke_wrapper_script(
            "/tmp/mep-cfd-mpi-smoke-" + token,
            token,
            ranks=2,
            timeout_seconds=10,
            cleanup_grace_seconds=3,
            environment_overrides={"OMPI_MCA_btl_vader_single_copy_mechanism": "none"},
        )

        self.assertIn("setsid", script)
        self.assertIn("leader.pid", script)
        self.assertIn("MEP_CFD_MPI_SMOKE_TOKEN", script)
        self.assertIn("mpirun -np 2 hostname", script)
        self.assertIn("kill -TERM --", script)
        self.assertIn("kill -KILL --", script)
        self.assertIn("runner_rc=$?\nset +e", script)
        self.assertNotIn("wsl --terminate", script)
        self.assertNotIn("wsl --shutdown", script)

    def test_environment_overrides_are_limited_to_the_one_smoke_process(self):
        token = "a" * 32
        script = cfd_run._mpi_smoke_wrapper_script(
            "/tmp/mep-cfd-mpi-smoke-" + token,
            token,
            ranks=2,
            timeout_seconds=10,
            cleanup_grace_seconds=3,
            environment_overrides={"OMPI_MCA_btl_vader_single_copy_mechanism": "none"},
        )

        self.assertIn("OMPI_MCA_btl_vader_single_copy_mechanism=none", script)
        self.assertNotIn("mca-params.conf", script)
        self.assertNotIn(".bashrc", script)


if __name__ == "__main__":
    unittest.main()
