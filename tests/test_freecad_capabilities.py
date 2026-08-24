import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import cfd_capabilities


class FreeCADCapabilityTests(unittest.TestCase):
    def test_selection_skips_missing_candidates_and_keeps_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "FreeCADCmd.exe"
            executable.write_bytes(b"")
            candidates = [
                (str(Path(tmp) / "missing.exe"), "configured"),
                (str(executable), "standard"),
            ]
            with mock.patch.object(
                cfd_capabilities, "_candidate_paths", return_value=iter(candidates)
            ):
                path, selection = cfd_capabilities.select_freecadcmd()
        self.assertEqual(path, str(executable.resolve()))
        self.assertEqual(selection, "standard")

    def test_missing_runtime_has_actionable_status(self):
        with mock.patch.object(
            cfd_capabilities, "select_freecadcmd", return_value=("", "missing")
        ):
            result = cfd_capabilities.diagnose_freecad()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "missing")
        self.assertIn(cfd_capabilities.FREECAD_EXE_ENV, result["fix"])

    def test_invalid_configured_path_is_not_silently_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            fallback = Path(tmp) / "FreeCADCmd.exe"
            fallback.write_bytes(b"")
            with mock.patch.dict(
                os.environ,
                {cfd_capabilities.FREECAD_EXE_ENV: str(Path(tmp) / "missing.exe")},
            ), mock.patch.object(
                cfd_capabilities,
                "_candidate_paths",
                return_value=iter([(str(fallback), "standard")]),
            ):
                path, selection = cfd_capabilities.select_freecadcmd()
        self.assertEqual(path, "")
        self.assertEqual(selection, "configured_missing")

    def test_ready_probe_records_versions_modules_and_boolean_smoke(self):
        payload = {
            "freecad_version": "1.1.1",
            "revision": "20260414",
            "python_version": "3.11.14",
            "occ_version": "7.8.1",
            "modules": {
                name: True for name in (
                    "FreeCAD", "Part", "Draft", "Arch", "Mesh", "MeshPart",
                    "BOPTools.SplitAPI",
                )
            },
            "smoke": {
                "ok": True, "valid": True, "solid_count": 1,
                "closed": True, "volume_mm3": 239250000000.0,
            },
        }
        proc = subprocess.CompletedProcess(
            ["FreeCADCmd"], 0,
            stdout=cfd_capabilities._PROBE_MARKER + json.dumps(payload) + "\n",
            stderr="",
        )
        with mock.patch.object(
            cfd_capabilities, "select_freecadcmd",
            return_value=(r"C:\FreeCAD\bin\FreeCADCmd.exe", "explicit"),
        ), mock.patch.object(cfd_capabilities.subprocess, "run", return_value=proc):
            result = cfd_capabilities.diagnose_freecad()

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["compatible_profile"], "freecad-1.1.1-occ-7.8.1")
        self.assertEqual(result["error_detail"], "")

    def test_unverified_version_is_not_silently_accepted(self):
        payload = {
            "freecad_version": "1.2.0",
            "revision": "future",
            "python_version": "3.12",
            "occ_version": "7.9.0",
            "modules": {
                name: True for name in (
                    "FreeCAD", "Part", "Draft", "Arch", "Mesh", "MeshPart",
                    "BOPTools.SplitAPI",
                )
            },
            "smoke": {"ok": True},
        }
        proc = subprocess.CompletedProcess(
            ["FreeCADCmd"], 0,
            stdout=cfd_capabilities._PROBE_MARKER + json.dumps(payload) + "\n",
            stderr="",
        )
        with mock.patch.object(
            cfd_capabilities, "select_freecadcmd",
            return_value=(r"C:\FreeCAD\bin\FreeCADCmd.exe", "explicit"),
        ), mock.patch.object(cfd_capabilities.subprocess, "run", return_value=proc):
            result = cfd_capabilities.diagnose_freecad()

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "unsupported_version")
        self.assertIn("골든 케이스", result["fix"])

    def test_headless_command_uses_job_local_config_and_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            command = cfd_capabilities.freecad_headless_command(
                r"C:\FreeCAD\FreeCADCmd.exe", os.path.join(tmp, "worker.py"), tmp
            )
        self.assertIn("--safe-mode", command)
        self.assertEqual(command[command.index("-u") + 1], os.path.join(tmp, "user.cfg"))
        self.assertEqual(command[command.index("-s") + 1], os.path.join(tmp, "system.cfg"))
        self.assertEqual(
            command[command.index("--log-file") + 1], os.path.join(tmp, "freecad.log")
        )


if __name__ == "__main__":
    unittest.main()
