import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from cfd_capabilities import find_freecadcmd, freecad_headless_command


@unittest.skipUnless(
    os.environ.get("MEP_CFD_RUN_FREECAD_TESTS") == "1",
    "set MEP_CFD_RUN_FREECAD_TESTS=1 for the pinned FreeCAD target-runner",
)
class FreeCADBuilderIntegrationTests(unittest.TestCase):
    def test_opening_build_returns_consistent_contract_and_unique_output(self):
        repo = Path(__file__).resolve().parents[1]
        executable = find_freecadcmd()
        self.assertTrue(executable, "FreeCADCmd not found")
        geometry = {
            "source": "opening-smoke.dxf",
            "units": "mm",
            "scale_applied": 1.0,
            "params": {
                "wall": {"width": 200.0, "height": 3000.0},
                "column": {"height": 3000.0},
                "slab": {"thickness": 200.0},
            },
            "elements": {
                "wall": [{
                    "kind": "polyline",
                    "closed": False,
                    "points": [[0.0, 0.0], [4000.0, 0.0]],
                    "centerline": [[0.0, 0.0], [4000.0, 0.0]],
                    "width_detected": 200.0,
                    "z_base": 0.0,
                }],
                "column": [],
                "slab": [],
                "zone": [{
                    "kind": "polyline",
                    "closed": True,
                    "points": [
                        [0.0, 0.0], [4000.0, 0.0],
                        [4000.0, 3000.0], [0.0, 3000.0],
                    ],
                    "z_base": 0.0,
                }],
                "opening": [{
                    "kind": "polyline",
                    "center": [2000.0, 0.0],
                    "width": 900.0,
                    "height": 2100.0,
                    "subtype": "door",
                    "wall_indices": [0],
                    "host_dir": [1.0, 0.0],
                    "host_width": 200.0,
                    "z_base": 0.0,
                }],
                "pipe": [],
                "duct": [],
                "tray": [],
                "equipment": [],
            },
            "floors": [{"z": 0.0, "label": "Level_1"}],
        }

        with tempfile.TemporaryDirectory(prefix=".test-freecad-", dir=repo) as tmp:
            tmp_path = Path(tmp)
            geometry_path = tmp_path / "opening.geometry.json"
            geometry_path.write_text(
                json.dumps(geometry, ensure_ascii=False), encoding="utf-8"
            )
            process_job = tmp_path / "process"
            process_job.mkdir()
            env = dict(
                os.environ,
                MEP_GEOMETRY=str(geometry_path),
                MEP_OUT=str(tmp_path / "opening_model"),
                MEP_CFD_JOB_ROOT=str(process_job),
                PYTHONIOENCODING="utf-8",
            )
            command = freecad_headless_command(
                executable, repo / "freecad_builder.py", process_job
            )
            proc = subprocess.run(
                command,
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            diagnostic = (proc.stdout or "")[-5000:] + (proc.stderr or "")[-2000:]
            self.assertEqual(proc.returncode, 0, diagnostic)
            self.assertIn("openings_void=1", proc.stdout)

            markers = {}
            for line in (proc.stdout or "").splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    if key in {"JOB_TMP", "FCSTD_TMP", "FCSTD_DST"}:
                        markers[key] = value.strip()
            self.assertIn("JOB_TMP", markers)
            self.assertIn("FCSTD_TMP", markers)
            fcstd = Path(markers["FCSTD_TMP"])
            self.assertTrue(fcstd.is_file(), diagnostic)
            self.assertGreater(fcstd.stat().st_size, 0)
            self.assertTrue(Path(markers["JOB_TMP"]).is_relative_to(process_job))

            shutil.rmtree(markers["JOB_TMP"], ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
