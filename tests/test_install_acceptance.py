import json
import hashlib
from pathlib import Path
import tempfile
import unittest

import install_acceptance


class InstallAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[1]

    def test_resume_evidence_requires_preservation_and_forward_progress(self):
        job = {
            "attempts": 2,
            "resume_history": [{
                "previous_status": "running",
                "checkpoint_times_s": {"very_coarse": 710.6832,
                                       "coarse": 401.8944},
            }],
            "levels": [
                {"name": "very_coarse", "latest_time_s": 710.6832},
                {"name": "coarse", "latest_time_s": 421.8944},
            ],
        }
        passed, detail, evidence = install_acceptance.validate_resume_evidence(job)
        self.assertTrue(passed, detail)
        self.assertEqual(evidence["current_checkpoint_times_s"]["coarse"], 421.8944)
        job["levels"][1]["latest_time_s"] = 400.0
        self.assertFalse(install_acceptance.validate_resume_evidence(job)[0])

    def test_schema_contract_is_available(self):
        schema = json.loads(
            (self.repo / "install_recovery_acceptance.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(schema["properties"]["contract"]["const"],
                         install_acceptance.CONTRACT)

    def test_validate_evidence_rejects_json_only_pass_and_log_tampering(self):
        with tempfile.TemporaryDirectory(prefix=".test-install-", dir=self.repo) as tmp:
            workspace = Path(tmp)
            projects = workspace / "cfd_projects"
            stable = projects / "capability_manifest.json"
            stable.parent.mkdir(parents=True)
            stable.write_text("{}", encoding="utf-8")
            stable_hash = hashlib.sha256(stable.read_bytes()).hexdigest()
            log_root = (projects / "_system" / "install_recovery_acceptance" /
                        "logs")
            log_root.mkdir(parents=True)
            contents = {
                "clean_install": "$ python -m pip install -r requirements.txt\nSuccessfully installed",
                "clean_verify": "$ python -c import ezdxf, shapely, numpy, matplotlib\nready",
                "dependency_repair": "$ python -m pip install -r requirements.txt\nRequirement already satisfied",
                "repair_verify": "$ python -c import ezdxf, shapely, numpy, matplotlib\nready",
                "launcher": "$ cmd /c run_cfd.bat --check\nlauncher: ready",
                "openfoam": "$ cmd /c install_openfoam2606.bat --check --no-pause\nOpenFOAM-v2606-ready",
            }
            logs = {}
            for name, content in contents.items():
                path = log_root / f"{name}.log"
                path.write_text(content, encoding="utf-8")
                logs[name] = str(path.resolve())
            manifest = {
                "contract": install_acceptance.CONTRACT, "status": "PASS",
                "project_data_preserved": True,
                "scenarios": {"clean_install": "PASS",
                              "dependency_repair": "PASS",
                              "interrupted_job_resume": "PASS"},
                "checks": {
                    "launcher": "PASS", "openfoam_v2606": "PASS",
                    "current_environment_acceptance": "PASS",
                    "stable_file_hashes": "PASS",
                    "resume_evidence": {
                        "attempts": 2,
                        "resume_event": {"previous_status": "running",
                                         "checkpoint_times_s": {"coarse": 10}},
                        "current_checkpoint_times_s": {"coarse": 20},
                    },
                    "stable_hashes_before": {str(stable.resolve()): stable_hash},
                    "stable_hashes_after": {str(stable.resolve()): stable_hash},
                },
                "logs": logs,
                "log_hashes": {name: hashlib.sha256(Path(path).read_bytes()).hexdigest()
                               for name, path in logs.items()},
            }
            evidence = projects / "_release_evidence" / "install_recovery.json"
            evidence.parent.mkdir(parents=True)
            evidence.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertTrue(install_acceptance.validate_evidence(
                evidence, projects
            )["ok"])
            openfoam_log = Path(logs["openfoam"])
            openfoam_log.write_text(
                contents["openfoam"] + "\nWsl/Service/WSL_E_DISTRO_NOT_FOUND",
                encoding="utf-8",
            )
            manifest["log_hashes"]["openfoam"] = hashlib.sha256(
                openfoam_log.read_bytes()
            ).hexdigest()
            evidence.write_text(json.dumps(manifest), encoding="utf-8")
            result = install_acceptance.validate_evidence(evidence, projects)
            self.assertFalse(result["ok"])
            self.assertIn("failure marker", result["error"])

            openfoam_log.write_text(contents["openfoam"], encoding="utf-8")
            manifest["log_hashes"]["openfoam"] = hashlib.sha256(
                openfoam_log.read_bytes()
            ).hexdigest()
            evidence.write_text(json.dumps(manifest), encoding="utf-8")
            Path(logs["launcher"]).write_text("forged PASS", encoding="utf-8")
            result = install_acceptance.validate_evidence(evidence, projects)
            self.assertFalse(result["ok"])
            self.assertIn("log does not prove success", result["error"])


if __name__ == "__main__":
    unittest.main()
