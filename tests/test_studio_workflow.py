from contextlib import contextmanager
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

import cfd_studio
import cfd_evidence
import cfd_review
from test_cfd_evidence import make_complete_case


@contextmanager
def _studio_server():
    server = cfd_studio.ThreadingHTTPServer(
        ("127.0.0.1", 0), cfd_studio.StudioHandler
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=10)


def _request_json(base, path, *, payload=None, raw=None, headers=None):
    data = raw
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        base + path,
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.load(response), dict(response.headers)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"raw": body}
        return exc.code, payload, dict(exc.headers)


class StudioWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[1]
        self.tmp = tempfile.TemporaryDirectory(prefix=".test-studio-", dir=self.repo)
        self.old_root = cfd_studio.ROOT
        self.old_openfoam_ok = cfd_studio.OPENFOAM_OK
        self.old_capabilities = dict(cfd_studio.OPENFOAM_CAPABILITIES)
        self.old_freecad_capabilities = dict(cfd_studio.FREECAD_CAPABILITIES)
        self.old_acceptance = dict(cfd_studio.ENVIRONMENT_ACCEPTANCE)
        self.old_run = {"active": cfd_studio.RUN["active"],
                        "queue": list(cfd_studio.RUN["queue"]),
                        "history": dict(cfd_studio.RUN["history"]),
                        "worker": cfd_studio.RUN["worker"]}
        cfd_studio.ROOT = self.tmp.name
        cfd_studio.RUN.update(active=None, queue=[], history={}, worker=False)

    def tearDown(self):
        cfd_studio.ROOT = self.old_root
        cfd_studio.OPENFOAM_OK = self.old_openfoam_ok
        cfd_studio.OPENFOAM_CAPABILITIES = self.old_capabilities
        cfd_studio.FREECAD_CAPABILITIES = self.old_freecad_capabilities
        cfd_studio.ENVIRONMENT_ACCEPTANCE = self.old_acceptance
        cfd_studio.RUN.update(self.old_run)
        self.tmp.cleanup()

    def _authoritative_body_case(self):
        paths = make_complete_case(Path(self.tmp.name))
        cfd_studio.ROOT = str(paths["root"])
        cfd_evidence.build_case_evidence(
            paths["case"], projects_root=paths["root"]
        )
        return paths

    def test_environment_refresh_writes_project_local_manifest(self):
        capabilities = {
            "schema_version": 1, "checked_at": "2026-07-15T00:00:00+00:00",
            "ok": True, "status": "ready", "summary": "준비 완료", "fix": "",
            "selection": "default", "distro": "Ubuntu-24.04",
            "available_distros": ["Ubuntu-24.04"],
            "bashrc": "/usr/share/openfoam/etc/bashrc", "version": "v1912",
            "package_version": "1912.200626-2build3",
            "compatible_profile": "openfoam-v1912", "commands": {},
            "missing_runtime_commands": [], "missing_body_fitted_commands": [],
            "body_fitted_ready": True, "thermal_detailed_ready": True,
            "error_detail": "",
        }
        freecad = {
            "schema_version": 1, "checked_at": "2026-07-15T00:00:00+00:00",
            "ok": True, "status": "ready", "summary": "FreeCAD 준비 완료",
            "fix": "", "selection": "standard",
            "executable": r"C:\Program Files\FreeCAD 1.1\bin\FreeCADCmd.exe",
            "freecad_version": "1.1.1", "revision": "20260414",
            "python_version": "3.11.14", "occ_version": "7.8.1",
            "compatible_profile": "freecad-1.1.1-occ-7.8.1",
            "modules": {}, "smoke": {"ok": True}, "error_detail": "",
        }
        with mock.patch.object(cfd_studio, "diagnose_openfoam",
                               return_value=capabilities), \
             mock.patch.object(cfd_studio, "diagnose_freecad",
                               return_value=freecad):
            result = cfd_studio.refresh_environment_capabilities()

        manifest = Path(self.tmp.name) / "capability_manifest.json"
        self.assertTrue(manifest.is_file())
        saved = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(saved["schema_version"], 2)
        self.assertEqual(saved["application"], "MEP CFD Studio")
        self.assertEqual(saved["openfoam"]["distro"], "Ubuntu-24.04")
        self.assertEqual(saved["freecad"]["freecad_version"], "1.1.1")
        self.assertTrue(saved["body_fitted_runtime_ready"])
        self.assertTrue(saved["body_fitted_engine_ready"])
        self.assertEqual(saved["engine"],
                         "screening_voxel+body_fitted_thermal")
        self.assertTrue(result["openfoam"]["ok"])
        self.assertTrue(result["freecad"]["ok"])
        self.assertEqual(cfd_studio.run_status()["environment"]["version"], "v1912")
        self.assertEqual(cfd_studio.run_status()["freecad"]["occ_version"], "7.8.1")

    def test_environment_panel_exposes_runtime_diagnostic_code(self):
        """A non-expert must be able to relay a stable WSL failure code to IT."""
        self.assertIn("e.reason_code", cfd_studio.PAGE_DASH)
        self.assertIn("진단 코드", cfd_studio.PAGE_DASH)

    def test_environment_refresh_surfaces_blocked_mpi_smoke_and_forces_serial(self):
        evidence_dir = Path(self.tmp.name) / "_release_evidence"
        evidence_dir.mkdir()
        (evidence_dir / "runtime_capability.v1.json").write_text(
            json.dumps({
                "contract": "runtime_capability.v1",
                "parallel_runtime_ready": False,
                "mpi": {
                    "execution_smoke": "BLOCKED",
                    "reason_code": "MPI_RANK_SPAWN_HANG",
                    "artifact_path": "mpi_runtime_smoke_default.v1.json",
                },
            }),
            encoding="utf-8",
        )
        openfoam = {
            "ok": True, "status": "ready", "summary": "ready", "fix": "",
            "distro": "Ubuntu-24.04", "version": "v2606",
            "parallel_ready": True, "mpi_tools_available": True,
        }
        freecad = {"ok": True, "summary": "ready"}
        with mock.patch.object(cfd_studio, "diagnose_openfoam", return_value=openfoam), \
             mock.patch.object(cfd_studio, "diagnose_freecad", return_value=freecad):
            result = cfd_studio.refresh_environment_capabilities()

        environment = result["openfoam"]
        self.assertEqual(environment["mpi_execution_smoke"], "BLOCKED")
        self.assertEqual(environment["mpi_runtime_reason_code"], "MPI_RANK_SPAWN_HANG")
        self.assertFalse(environment["parallel_ready"])
        self.assertFalse(environment["parallel_runtime_ready"])

    def test_passed_mpi_runtime_requires_an_intact_smoke_artifact(self):
        evidence_dir = Path(self.tmp.name) / "_release_evidence"
        evidence_dir.mkdir()
        missing = evidence_dir / "mpi_runtime_smoke.v1.json"
        (evidence_dir / "runtime_capability.v1.json").write_text(json.dumps({
            "contract": "runtime_capability.v1",
            "parallel_runtime_ready": True,
            "mpi": {
                "execution_smoke": "PASS",
                "artifact_path": str(missing),
                "artifact_sha256": "a" * 64,
            },
        }), encoding="utf-8")

        result = cfd_studio._apply_mpi_runtime_capability({
            "ok": True, "parallel_ready": True, "distro": "Ubuntu-24.04",
        })

        self.assertEqual(result["mpi_execution_smoke"], "NOT_RUN")
        self.assertEqual(result["mpi_runtime_reason_code"], "MPI_SMOKE_ARTIFACT_MISSING")
        self.assertFalse(result["parallel_ready"])

    def test_passed_mpi_runtime_is_invalidated_when_current_identity_changes(self):
        evidence_dir = Path(self.tmp.name) / "_release_evidence"
        evidence_dir.mkdir()
        identity = {
            "distro": "Ubuntu-24.04",
            "kernel": "6.18.33.2-microsoft-standard-WSL2",
            "mpirun_path": "/usr/bin/mpirun",
            "mpirun_version": "mpirun (Open MPI) 4.1.6",
            "ompi_info_version": "Open MPI v4.1.6",
            "effective_cpu_count": 10,
        }
        artifact = evidence_dir / "mpi_runtime_smoke.v1.json"
        artifact.write_text(json.dumps({
            "contract": "mpi_runtime_smoke.v1", "status": "PASS",
            "reason_code": "", "identity": identity,
            "requested_ranks": [2, 4], "environment_overrides": {}, "trials": [],
        }), encoding="utf-8")
        (evidence_dir / "runtime_capability.v1.json").write_text(json.dumps({
            "contract": "runtime_capability.v1",
            "parallel_runtime_ready": True,
            "openfoam": {"distro": "Ubuntu-24.04"},
            "cpu": {"effective_logical_count": 10},
            "mpi": {
                "execution_smoke": "PASS", "artifact_path": str(artifact),
                "artifact_sha256": cfd_studio._file_sha256(artifact),
                "smoke_identity": identity,
                "version": identity["mpirun_version"],
                "tools": {
                    "mpirun": "/usr/bin/mpirun",
                    "decomposePar": "/usr/bin/decomposePar",
                    "reconstructPar": "/usr/bin/reconstructPar",
                },
            },
        }), encoding="utf-8")
        current = {
            "ok": True, "parallel_ready": True, "distro": "Ubuntu-24.04",
            "kernel": identity["kernel"], "effective_cpu_count": 10,
            "mpi_version": identity["mpirun_version"],
            "ompi_info_version": identity["ompi_info_version"],
            "commands": {
                "mpirun": "/usr/bin/mpirun",
                "decomposePar": "/usr/bin/decomposePar",
                "reconstructPar": "/usr/bin/reconstructPar",
            },
        }

        self.assertTrue(cfd_studio._apply_mpi_runtime_capability(current)["parallel_ready"])
        changed = dict(current, kernel="6.19.0-different")
        result = cfd_studio._apply_mpi_runtime_capability(changed)

        self.assertEqual(result["mpi_execution_smoke"], "NOT_RUN")
        self.assertIn("MPI_SMOKE_RUNTIME_MISMATCH", result["mpi_runtime_reason_code"])
        self.assertFalse(result["parallel_ready"])

    def test_environment_acceptance_is_queued_only_once(self):
        cfd_studio.OPENFOAM_OK = True
        cfd_studio.OPENFOAM_CAPABILITIES = {"ok": True, "summary": "준비"}
        cfd_studio.RUN["worker"] = True  # keep the unit test from starting a thread

        self.assertIsNone(cfd_studio.enqueue_environment_acceptance())
        self.assertIn("이미 대기", cfd_studio.enqueue_environment_acceptance())
        queued = [q for q in cfd_studio.RUN["queue"] if q["kind"] == "acceptance"]
        self.assertEqual(len(queued), 1)
        self.assertEqual(cfd_studio.ENVIRONMENT_ACCEPTANCE["status"], "queued")

    def test_mpi_runtime_smoke_is_queued_once_without_a_case_name(self):
        cfd_studio.OPENFOAM_OK = True
        cfd_studio.OPENFOAM_CAPABILITIES = {
            "ok": True, "distro": "Ubuntu-24.04", "summary": "ready",
        }
        cfd_studio.RUN["worker"] = True  # no actual background work in the test

        self.assertIsNone(cfd_studio.enqueue_mpi_runtime_smoke())
        self.assertIn("이미 대기", cfd_studio.enqueue_mpi_runtime_smoke())
        queued = [q for q in cfd_studio.RUN["queue"] if q["kind"] == "mpi_smoke"]
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]["name"], cfd_studio.MPI_SMOKE_JOB)

    def test_mpi_runtime_smoke_records_capability_and_keeps_blocked_result_honest(self):
        act = {"step": "", "lines": []}
        smoke = {
            "status": "BLOCKED", "reason_code": "MPI_RANK_SPAWN_HANG",
            "artifact_path": str(Path(self.tmp.name) / "_release_evidence" / "mpi.json"),
            "artifact_sha256": "a" * 64,
            "trials": [{"ranks": 2, "timed_out": True}],
        }
        with mock.patch.object(cfd_studio, "run_mpi_runtime_smoke", return_value=smoke) as run, \
             mock.patch.object(cfd_studio, "record_runtime_capability") as record, \
             mock.patch.object(cfd_studio, "refresh_openfoam_runtime_evidence") as refresh, \
             mock.patch.object(cfd_studio.cfd_gci_job, "acquire_solver_lock", return_value=("token", {})), \
             mock.patch.object(cfd_studio.cfd_gci_job, "release_solver_lock") as release:
            ok, err, details = cfd_studio._do_mpi_runtime_smoke(act)

        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(details["status"], "BLOCKED")
        self.assertEqual(run.call_args.kwargs["ranks"], (2, 4))
        self.assertEqual(record.call_args.kwargs["mpi_smoke"]["status"], "BLOCKED")
        refresh.assert_called_once_with()
        release.assert_called_once_with(cfd_studio.ROOT, "token")

    def test_mpi_runtime_smoke_does_not_reprobe_freecad(self):
        """The MPI diagnostic must not wait on an unrelated FreeCAD probe."""
        act = {"step": "", "lines": []}
        smoke = {
            "status": "BLOCKED", "reason_code": "MPI_RANK_SPAWN_HANG",
            "artifact_path": str(Path(self.tmp.name) / "_release_evidence" / "mpi.json"),
            "artifact_sha256": "b" * 64,
        }
        cfd_studio.FREECAD_CAPABILITIES = {"ok": True, "summary": "cached"}

        def write_runtime_evidence(path, baseline=None, *, mpi_smoke=None):
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({
                "contract": "runtime_capability.v1",
                "parallel_runtime_ready": False,
                "mpi": {
                    "execution_smoke": mpi_smoke["status"],
                    "reason_code": mpi_smoke["reason_code"],
                    "artifact_path": mpi_smoke["artifact_path"],
                    "artifact_sha256": mpi_smoke["artifact_sha256"],
                },
            }), encoding="utf-8")
            return {"ok": True, "path": str(target)}

        with mock.patch.object(cfd_studio, "run_mpi_runtime_smoke", return_value=smoke), \
             mock.patch.object(cfd_studio, "record_runtime_capability", side_effect=write_runtime_evidence), \
             mock.patch.object(cfd_studio, "diagnose_openfoam", return_value={
                 "ok": True, "parallel_ready": True, "distro": "Ubuntu-24.04",
             }), \
             mock.patch.object(cfd_studio, "diagnose_freecad", side_effect=AssertionError(
                 "MPI recheck must not run the FreeCAD diagnostic"
             )), \
             mock.patch.object(cfd_studio.cfd_gci_job, "acquire_solver_lock", return_value=("token", {})), \
             mock.patch.object(cfd_studio.cfd_gci_job, "release_solver_lock"):
            ok, err, details = cfd_studio._do_mpi_runtime_smoke(act)

        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(details["status"], "BLOCKED")
        self.assertEqual(cfd_studio.OPENFOAM_CAPABILITIES["mpi_execution_smoke"], "BLOCKED")
        self.assertFalse(cfd_studio.OPENFOAM_CAPABILITIES["parallel_runtime_ready"])

    def test_legacy_run_does_not_start_when_cross_process_solver_slot_is_busy(self):
        act = {"step": "", "time": 0.0, "lines": []}
        with mock.patch.object(
            cfd_studio.cfd_gci_job, "acquire_solver_lock", return_value=(None, {"pid": 4321})
        ), mock.patch.object(cfd_studio, "run_until_closed", return_value={
            "ok": True, "error": None,
        }) as run:
            ok, error = cfd_studio._do_run("legacy", self.tmp.name, act)

        self.assertFalse(ok)
        self.assertIn("OpenFOAM", error)
        self.assertIn("4321", error)
        run.assert_not_called()

    def test_body_continuation_does_not_start_when_cross_process_solver_slot_is_busy(self):
        solver = Path(self.tmp.name) / "_body_solver" / "sample-thermal"
        solver.mkdir(parents=True)
        (solver / "thermal_input.json").write_text("{}", encoding="utf-8")
        with mock.patch.object(cfd_studio, "diagnose_openfoam", return_value={
            "thermal_detailed_ready": True,
        }), mock.patch.object(
            cfd_studio.cfd_gci_job, "acquire_solver_lock", return_value=(None, {"pid": 4321})
        ), mock.patch.object(cfd_studio.cfd_physics, "run_buoyant_continuation") as run:
            result = cfd_studio.continue_body_fitted_thermal(str(solver))

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "CFD_SOLVER_BUSY")
        self.assertEqual(result["lock"]["pid"], 4321)
        run.assert_not_called()

    def test_environment_acceptance_verifies_mesh_time_log_and_report(self):
        case_dir = Path(self.tmp.name) / "_system" / "environment_acceptance"
        act = {"step": "준비", "time": 0.0, "endTime": 100, "lines": []}
        cfd_studio.OPENFOAM_CAPABILITIES = {
            "compatible_profile": "openfoam-v2606",
            "version": "v2606",
            "distro": "Ubuntu-24.04",
        }

        def fake_build(_cfg, out_dir):
            Path(out_dir).mkdir(parents=True)

        def fake_run(_name, out_dir, active):
            active["mesh_ok"] = True
            (Path(out_dir) / "100").mkdir()
            (Path(out_dir) / "log.testFoam").write_text("End\n", encoding="ascii")
            (Path(out_dir) / "cfd_report_environment_acceptance.html").write_text(
                "<html></html>", encoding="ascii"
            )
            return True, None

        with mock.patch.object(cfd_studio.cfd_export, "build_case", side_effect=fake_build), \
             mock.patch.object(cfd_studio, "_do_run", side_effect=fake_run):
            ok, err, details = cfd_studio._do_environment_acceptance(str(case_dir), act)

        self.assertTrue(ok, err)
        self.assertTrue(details["mesh_ok"])
        self.assertEqual(details["latest_time"], 100.0)
        self.assertTrue(details["solver_log"].endswith("log.testFoam"))
        self.assertTrue(details["report_path"].endswith(".html"))
        self.assertEqual(details["openfoam_profile"], "openfoam-v2606")
        self.assertEqual(details["openfoam_version"], "v2606")

    def test_dxf_is_parsed_directly_into_project_imports(self):
        imports = Path(self.tmp.name) / "_imports"
        imports.mkdir()
        dxf = imports / "sample_plan.dxf"
        shutil.copyfile(self.repo / "sample_plan.dxf", dxf)
        result = cfd_studio._parse_uploaded_dxf(str(dxf))
        self.assertTrue(result["ok"])
        geometry = Path(result["geometry"])
        self.assertTrue(geometry.is_file())
        data = json.loads(geometry.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 2)
        self.assertEqual(data["contract"], "geometry.v2")
        self.assertIn("review", data)
        self.assertGreaterEqual(len(data["elements"]["zone"]), 1)
        self.assertGreaterEqual(result["inspect"]["walls"], 1)
        self.assertEqual(result["inspect"]["contract"], "geometry.v2")
        self.assertIn("review_items", result["inspect"])
        self.assertIn('id="dxfunit"', cfd_studio.PAGE_NEW)
        self.assertIn("unit=", cfd_studio.PAGE_NEW)
        self.assertIn("OPROWS.every(r=>r.cx===''&&r.cy==='')", cfd_studio.PAGE_NEW)
        self.assertIn("j.height_confirmed&&j.height_m", cfd_studio.PAGE_NEW)
        self.assertIn("confirmUnitMm()", cfd_studio.PAGE_NEW)
        self.assertIn("roleConfidence", cfd_studio.PAGE_NEW)

    def test_opening_editor_accepts_dxf_precision_without_html_step_mismatch(self):
        """DXF-derived 12.054 m / 0.20 m terminals must remain form-valid."""
        page = cfd_studio.PAGE_NEW
        editor_start = page.index("function opRender()")
        editor_end = page.index("function opValid()", editor_start)
        editor = page[editor_start:editor_end]

        # Parser output is recorded to millimetre precision in metres.  The
        # input contract must therefore accept the SGI-lobby values rather
        # than declaring imported geometry invalid before create().
        self.assertIn("const step=k==='cmh'?1:0.001;", editor)
        self.assertIn('step="${step}"', editor)
        self.assertIn(
            'id="su" type="number" min="0.01" step="0.01" value="0.3"',
            page,
        )

        def is_step_valid(value, minimum, step):
            quotient = (value - minimum) / step
            return abs(quotient - round(quotient)) < 1e-9

        self.assertTrue(is_step_valid(12.054, 0.0, 0.001))
        self.assertTrue(is_step_valid(0.20, 0.01, 0.001))
        self.assertTrue(is_step_valid(0.3, 0.01, 0.01))

    def test_opening_mode_keeps_explicit_grid_and_iteration_inputs(self):
        """Selecting diffuser mode must not overwrite an operator's case basis."""
        page = cfd_studio.PAGE_NEW
        start = page.index("function vmodeCh()")
        end = page.index("function opAdd(", start)
        mode_change = page[start:end]

        # A fresh form retains the literal HTML defaults.  The operator's
        # 0.20 m / 1000 iteration SGI case does not, so it must be retained
        # and accompanied by a recommendation instead of a hidden rewrite.
        self.assertIn("v('cell')===el('cell').defaultValue", mode_change)
        self.assertIn("v('iters')===el('iters').defaultValue", mode_change)
        self.assertIn("OPENING_MODE_RECOMMENDATION", mode_change)
        self.assertNotIn("if(+v('cell')>0.16){el('cell').value=0.15;}", mode_change)
        self.assertNotIn("if(+v('iters')<4000){el('iters').value=4000;}", mode_change)

        preview_start = page.index("function preview()")
        preview_end = page.index("function checkInput(", preview_start)
        self.assertIn("OPENING_MODE_RECOMMENDATION", page[preview_start:preview_end])

        self.assertNotEqual("0.20", "0.3")
        self.assertNotEqual("1000", "400")

    def test_created_case_has_3d_model_before_solver_run(self):
        result = cfd_studio.create_case({
            "mode": "manual", "name": "실행 전 모델", "L": 4, "W": 3, "H": 2.5,
            "power_kw": 5, "supply": "x0", "exhaust": "xL", "supply_u": 0.3,
            "supply_T_C": 20, "cell": 0.5, "endtime": 400,
        })
        self.assertTrue(result.get("ok"), result)
        model = cfd_studio.model_info("실행 전 모델")
        self.assertEqual(model["room"], {"L": 4.0, "W": 3.0, "H": 2.5})
        self.assertEqual(model["inlet"], "x0")
        self.assertEqual(model["outlet"], "xL")
        self.assertGreater(model["mesh"]["cells"], 0)

    def test_opening_case_preserves_exhaust_design_target_not_as_applied_flow(self):
        result = cfd_studio.create_case({
            "mode": "manual", "name": "opening target", "L": 6, "W": 5, "H": 3,
            "power_kw": 1, "supply_T_C": 20, "cell": 0.2, "endtime": 400,
            "openings": [
                {"role": "supply", "type": "4way", "wall": "ceiling",
                 "cx": 2, "cy": 2, "w": 0.6, "h": 0.6, "cmh": 720},
                {"role": "exhaust", "type": "grille", "wall": "ceiling",
                 "cx": 4, "cy": 3, "w": 0.6, "h": 0.6, "cmh": 720},
            ],
        })
        self.assertTrue(result.get("ok"), result)
        meta = json.loads((Path(self.tmp.name) / "opening target" /
                           "cfd_case_meta.json").read_text(encoding="utf-8"))
        exhaust_input = next(row for row in meta["config"]["openings"]
                             if row["role"] == "exhaust")
        exhaust_patch = next(row for row in meta["patches"]
                             if row["role"] == "exhaust")
        self.assertEqual(exhaust_input["cmh"], 720.0)
        self.assertEqual(exhaust_patch["design_cmh"], 720.0)
        self.assertIsNone(exhaust_patch["cmh"])
        self.assertEqual(exhaust_patch["flow_control"], "pressure_outlet")
        model = cfd_studio.model_info("opening target")
        self.assertEqual(model["opening_preflight"]["contract"], "opening_preflight.v2")

    def test_occ_geometry_build_is_published_under_project_root(self):
        geometry = Path(self.tmp.name) / "confirmed.geometry.json"
        geometry.write_text("{}", encoding="utf-8")
        fake_manifest = {
            "air_volume": {
                "volume_m3": 33.6,
                "location_in_mesh": {"point_m": [2.0, 1.5, 1.4]},
            },
            "regions": [{"name": "wall"}, {"name": "supply_A"}],
            "topology": {"watertight": True},
            "surface_hash": "abc123",
        }
        with mock.patch.object(cfd_studio.cfd_occ, "run_occ_job", return_value={
            "ok": True,
            "output": str(Path(self.tmp.name) / "_occ_geometry" / "published"),
            "manifest_path": str(Path(self.tmp.name) / "_occ_geometry" / "published" /
                                 "surface_manifest.json"),
            "manifest": fake_manifest,
        }) as run:
            result = cfd_studio.build_occ_geometry(str(geometry))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["volume_m3"], 33.6)
        self.assertEqual(result["region_count"], 2)
        output_arg = Path(run.call_args.args[1])
        self.assertTrue(output_arg.is_relative_to(Path(self.tmp.name) / "_occ_geometry"))

    def test_body_fitted_mesh_stops_at_independent_mesh_gate(self):
        occ_output = Path(self.tmp.name) / "_occ_geometry" / "ready"
        with mock.patch.object(cfd_studio, "build_occ_geometry", return_value={
            "ok": True, "output": str(occ_output),
        }), mock.patch.object(cfd_studio.cfd_mesh, "build_mesh_case", return_value={
            "ok": True, "case": str(Path(self.tmp.name) / "_body_mesh" / "ready"),
            "estimate": {"estimated_cells": 10000},
        }) as build, mock.patch.object(cfd_studio.cfd_mesh, "run_mesh_case", return_value={
            "ok": False, "error": "메시 품질 gate 실패: CHECKMESH_FAILED",
            "manifest": {"status": "FAIL", "errors": ["CHECKMESH_FAILED"]},
        }) as run:
            result = cfd_studio.build_body_fitted_mesh("confirmed.geometry.json")
        self.assertFalse(result["ok"])
        self.assertIn("메시 품질 gate", result["error"])
        self.assertEqual(result["estimate"]["estimated_cells"], 10000)
        mesh_path = Path(build.call_args.args[1])
        self.assertTrue(mesh_path.is_relative_to(Path(self.tmp.name) / "_body_mesh"))
        self.assertTrue(mesh_path.name.endswith("-quick"))
        self.assertEqual(build.call_args.kwargs["settings"]["preset"], "quick")
        run.assert_called_once_with(str(mesh_path))

    def test_body_fitted_mesh_rejects_unknown_preset_before_occ(self):
        with mock.patch.object(cfd_studio, "build_occ_geometry") as occ:
            result = cfd_studio.build_body_fitted_mesh(
                "confirmed.geometry.json", {"preset": "unknown"}
            )
        self.assertFalse(result["ok"])
        self.assertIn("지원하지 않는 메시 프리셋", result["error"])
        occ.assert_not_called()

    def test_isothermal_run_requires_project_detailed_mesh_and_keeps_warn_result(self):
        mesh = Path(self.tmp.name) / "_body_mesh" / "sample-detailed"
        mesh.mkdir(parents=True)
        (mesh / "mesh_manifest.json").write_text(json.dumps({
            "status": "PASS", "profile": "detailed",
        }), encoding="utf-8")
        with mock.patch.object(cfd_studio.cfd_physics, "build_isothermal_case", return_value={
            "ok": True, "physics_input": {"contract": "physics_input.v1"},
        }) as build, mock.patch.object(cfd_studio.cfd_physics, "run_isothermal_case", return_value={
            "ok": True, "manifest": {"status": "WARN", "design_ready": False},
        }) as run:
            result = cfd_studio.run_body_fitted_isothermal(str(mesh))
        self.assertTrue(result["ok"])
        self.assertEqual(result["manifest"]["status"], "WARN")
        solver_path = Path(build.call_args.args[1])
        self.assertTrue(solver_path.is_relative_to(Path(self.tmp.name) / "_body_solver"))
        run.assert_called_once_with(solver_path)

    def test_isothermal_run_rejects_path_outside_project_mesh_root(self):
        result = cfd_studio.run_body_fitted_isothermal(str(Path(self.tmp.name).parent))
        self.assertFalse(result["ok"])
        self.assertIn("프로젝트", result["error"])

    def test_thermal_screening_requires_v2606_and_project_results(self):
        mesh = Path(self.tmp.name) / "_body_mesh" / "sample-detailed"
        initial = Path(self.tmp.name) / "_body_solver" / "sample-isothermal"
        mesh.mkdir(parents=True)
        initial.mkdir(parents=True)
        (mesh / "mesh_manifest.json").write_text(json.dumps({
            "status": "PASS", "profile": "detailed",
        }), encoding="utf-8")
        (initial / "run_manifest.json").write_text(json.dumps({
            "status": "PASS", "engine": "body_fitted_isothermal_rans",
        }), encoding="utf-8")
        with mock.patch.object(cfd_studio, "diagnose_openfoam", return_value={
            "thermal_detailed_ready": True,
        }), mock.patch.object(cfd_studio.cfd_physics, "build_buoyant_case", return_value={
            "ok": True, "thermal_input": {"contract": "thermal_input.v1"},
        }) as build, mock.patch.object(cfd_studio.cfd_physics, "run_buoyant_case", return_value={
            "ok": True, "manifest": {"status": "PASS"},
        }) as run:
            result = cfd_studio.run_body_fitted_thermal(str(mesh), str(initial))
        self.assertTrue(result["ok"])
        self.assertTrue(result["screening_only"])
        self.assertEqual(build.call_args.kwargs["initial_case_dir"], initial)
        self.assertEqual(build.call_args.kwargs["settings"]["thermal_duration_s"], 0.05)
        run.assert_called_once()

        with mock.patch.object(cfd_studio, "diagnose_openfoam", return_value={
            "thermal_detailed_ready": False,
        }):
            rejected = cfd_studio.run_body_fitted_thermal(str(mesh), str(initial))
        self.assertFalse(rejected["ok"])
        self.assertIn("v2606", rejected["error"])

    def test_thermal_continuation_is_project_local_and_uses_restart_runner(self):
        solver = Path(self.tmp.name) / "_body_solver" / "sample-thermal"
        solver.mkdir(parents=True)
        (solver / "thermal_input.json").write_text("{}", encoding="utf-8")
        with mock.patch.object(cfd_studio, "diagnose_openfoam", return_value={
            "thermal_detailed_ready": True,
        }), mock.patch.object(
            cfd_studio.cfd_physics, "run_buoyant_continuation",
            return_value={"ok": True, "manifest": {"status": "WARN"}},
        ) as run:
            result = cfd_studio.continue_body_fitted_thermal(
                str(solver), {"thermal_duration_s": 0.05}
            )
        self.assertTrue(result["ok"])
        run.assert_called_once_with(solver, settings={"thermal_duration_s": 0.05})
        rejected = cfd_studio.continue_body_fitted_thermal(
            str(Path(self.tmp.name).parent)
        )
        self.assertFalse(rejected["ok"])

    def test_field_design_job_continues_to_three_flow_through_times(self):
        solver = Path(self.tmp.name) / "_body_solver" / "actual-field-thermal"
        solver.mkdir(parents=True)
        initial = {
            "engine": "body_fitted_buoyant_urans", "status": "WARN",
            "design_ready": False,
            "thermal_progress": {
                "latest_time_s": 100.0, "flow_through_time_s": 100.0,
                "flow_through_fraction": 1.0,
                "recommended_next_duration_s": 20.0,
            },
        }
        (solver / "run_manifest.json").write_text(
            json.dumps(initial), encoding="utf-8"
        )
        (solver / "result_manifest.json").write_text("{}", encoding="utf-8")
        (solver / "field_design_job.json").write_text(json.dumps({
            "status": "FAIL", "attempts": 1, "started_at": "2026-07-21T00:00:00+00:00",
            "latest_time_s": 100.0, "flow_through_fraction": 1.0,
            "resume_history": [],
        }), encoding="utf-8")

        def continue_once(case, settings=None, progress_cb=None):
            completed = {
                "engine": "body_fitted_buoyant_urans", "status": "PASS",
                "design_ready": True,
                "thermal_progress": {
                    "latest_time_s": 300.0, "flow_through_time_s": 100.0,
                    "flow_through_fraction": 3.0,
                    "recommended_next_duration_s": 0.0,
                },
            }
            (Path(case) / "run_manifest.json").write_text(
                json.dumps(completed), encoding="utf-8"
            )
            return {"ok": True, "manifest": completed}

        act = {"step": "", "time": 0.0, "lines": []}
        with mock.patch.object(
            cfd_studio.cfd_physics, "run_buoyant_continuation",
            side_effect=continue_once,
        ) as run, mock.patch.object(
            cfd_studio.cfd_report, "generate_body_fitted_report",
            return_value={"ok": True},
        ) as report:
            ok, error, details = cfd_studio._do_field_design_run(
                "actual-field-thermal", act
            )
        self.assertTrue(ok, error)
        self.assertIsNone(error)
        self.assertEqual(details["flow_through_fraction"], 3.0)
        self.assertEqual(
            run.call_args.kwargs["settings"]["thermal_minimum_flow_through_fraction"],
            3.0,
        )
        job = json.loads((solver / "field_design_job.json").read_text(encoding="utf-8"))
        self.assertEqual(job["status"], "complete")
        self.assertEqual(job["attempts"], 2)
        self.assertEqual(job["resume_history"][0]["checkpoint_time_s"], 100.0)
        report.assert_called_once_with(solver, projects_root=self.tmp.name)

    def test_body_result_payload_loads_only_project_local_slice_files(self):
        case = Path(self.tmp.name) / "_body_solver" / "sample-thermal"
        (case / "results" / "slices").mkdir(parents=True)
        (case / "results" / "body_fitted_summary.json").write_text(json.dumps({
            "time_s": 0.2, "cell_count": 2,
            "bounds_m": {"minimum": [0, 0, 0], "maximum": [1, 1, 1]},
        }), encoding="utf-8")
        refs = []
        for axis in "xyz":
            relative = f"results/slices/{axis}_mid.json"
            (case / relative).write_text(json.dumps({
                "axis": axis, "target_m": 0.5, "samples": [],
            }), encoding="utf-8")
            refs.append({"axis": axis, "path": relative})
        (case / "result_manifest.json").write_text(json.dumps({
            "summary_path": "results/body_fitted_summary.json", "slices": refs,
        }), encoding="utf-8")
        (case / "run_manifest.json").write_text(json.dumps({
            "status": "WARN", "design_ready": False,
            "thermal_progress": {"flow_through_fraction": 0.2},
        }), encoding="utf-8")
        result = cfd_studio.body_result_payload("sample-thermal")
        self.assertTrue(result["ok"], result)
        self.assertEqual(set(result["slices"]), {"x", "y", "z"})
        self.assertEqual(result["result_gate"]["citation_status"], "NOT_EVALUATED")
        self.assertIsNone(result["case_health"])
        self.assertEqual(result["review_summary"], {
            "status": "NOT_AVAILABLE",
            "reason_codes": ["CASE_EVIDENCE_NOT_FOUND"],
        })

        outside = Path(self.tmp.name) / "outside-summary.json"
        outside.write_text(json.dumps({"outside": True}), encoding="utf-8")
        (case / "result_manifest.json").write_text(json.dumps({
            "summary_path": "../../outside-summary.json", "slices": refs,
        }), encoding="utf-8")
        self.assertFalse(cfd_studio.body_result_payload("sample-thermal")["ok"])
        self.assertFalse(cfd_studio.body_result_payload("../sample-thermal")["ok"])

    def test_body_result_payload_preserves_legacy_keys_and_rebuilds_current_health(self):
        paths = self._authoritative_body_case()
        legacy_keys = {
            "ok", "case", "manifest", "run_manifest", "result_gate",
            "design_job", "summary", "slices",
        }

        first = cfd_studio.body_result_payload(paths["case"].name)
        self.assertTrue(first["ok"], first)
        self.assertTrue(legacy_keys.issubset(first))
        self.assertEqual(first["case_health"]["contract"], "case_health.v1")
        self.assertEqual(first["review_summary"]["status"], "MISSING")

        run = json.loads(paths["run"].read_text(encoding="utf-8"))
        run["status"] = "FAIL"
        paths["run"].write_text(json.dumps(run), encoding="utf-8")
        stale_cache = paths["case"] / "case_health.v1.json"
        cached = json.loads(stale_cache.read_text(encoding="utf-8"))
        cached["citation_status"] = "DESIGN_CITABLE"
        stale_cache.write_text(json.dumps(cached), encoding="utf-8")

        second = cfd_studio.body_result_payload(paths["case"].name)
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["case_health"]["citation_status"], "CITATION_BLOCKED")
        self.assertIn(
            "ARTIFACT_HASH_MISMATCH",
            [row["code"] for row in second["case_health"]["errors"]],
        )

    def test_case_health_get_is_fresh_no_store_and_maps_missing_and_defects(self):
        paths = self._authoritative_body_case()
        missing = paths["root"] / "_body_solver" / "missing-evidence"
        missing.mkdir()
        with _studio_server() as base:
            status, health, headers = _request_json(
                base, f"/api/case-health/{paths['case'].name}"
            )
            self.assertEqual(status, 200)
            self.assertEqual(health["contract"], "case_health.v1")
            self.assertEqual(headers.get("Cache-Control"), "no-store")

            status, body, _ = _request_json(
                base, "/api/case-health/missing-evidence"
            )
            self.assertEqual(status, 404)
            self.assertEqual(body, {
                "ok": False,
                "code": "CASE_EVIDENCE_NOT_FOUND",
                "case": "missing-evidence",
            })

            status, _, _ = _request_json(base, "/api/case-health/not-a-case")
            self.assertEqual(status, 404)

            with mock.patch.object(
                cfd_studio.cfd_case_health, "build_case_health",
                side_effect=RuntimeError("infrastructure failed"),
            ):
                status, body, _ = _request_json(
                    base, f"/api/case-health/{paths['case'].name}"
                )
            self.assertEqual(status, 500)
            self.assertNotIn("case_health", body)

    def test_case_review_post_creates_hash_bound_record_and_refreshes_health(self):
        paths = self._authoritative_body_case()
        digest = hashlib.sha256(paths["evidence"].read_bytes()).hexdigest()
        with _studio_server() as base:
            status, body, headers = _request_json(base, "/api/case-review", payload={
                "case": paths["case"].name,
                "reviewer_id": "reviewer-1",
                "decision": "APPROVED",
                "reason": "current evidence reviewed",
                "target_sha256": digest,
                "supersedes_review_ids": [],
            }, headers={"Origin": base, "Sec-Fetch-Site": "same-origin"})

        self.assertEqual(status, 201, body)
        self.assertTrue(body["ok"])
        self.assertEqual(body["review"]["target"]["sha256"], digest)
        self.assertEqual(body["review_summary"]["status"], "APPROVED")
        self.assertEqual(body["case_health"]["contract"], "case_health.v1")
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        reviews = list((paths["case"] / "_reviews").glob("*.case_review.v1.json"))
        self.assertEqual(len(reviews), 1)
        self.assertEqual(cfd_review.validate_review(
            reviews[0], projects_root=paths["root"]
        ), [])

    def test_case_review_post_rejects_closed_body_violations_without_writing(self):
        paths = self._authoritative_body_case()
        digest = hashlib.sha256(paths["evidence"].read_bytes()).hexdigest()
        baseline = {
            "case": paths["case"].name,
            "reviewer_id": "reviewer-1",
            "decision": "APPROVED",
            "reason": "reviewed",
            "target_sha256": digest,
        }
        bad_requests = [
            (None, b"{"),
            ([], None),
            ({**baseline, "unknown": True}, None),
            ({key: value for key, value in baseline.items() if key != "reason"}, None),
            ({**baseline, "decision": "PENDING"}, None),
            ({**baseline, "target_sha256": "ABC"}, None),
            ({**baseline, "case": "../escape"}, None),
            ({**baseline, "supersedes_review_ids": ["review-" + "a" * 32] * 2}, None),
        ]
        with _studio_server() as base:
            for payload, raw in bad_requests:
                status, _, _ = _request_json(
                    base, "/api/case-review", payload=payload, raw=raw
                )
                self.assertEqual(status, 400, (payload, raw))
        review_dir = paths["case"] / "_reviews"
        self.assertFalse(review_dir.exists())

    def test_case_review_post_maps_safe_missing_stale_and_post_lock_change(self):
        paths = self._authoritative_body_case()
        request = {
            "case": paths["case"].name,
            "reviewer_id": "reviewer-1",
            "decision": "APPROVED",
            "reason": "reviewed",
            "target_sha256": "f" * 64,
        }
        missing = paths["root"] / "_body_solver" / "safe-missing"
        missing.mkdir()
        with _studio_server() as base:
            status, body, _ = _request_json(base, "/api/case-review", payload=request)
            self.assertEqual(status, 409)
            self.assertEqual(body["code"], "REVIEW_TARGET_CHANGED")
            self.assertFalse((paths["case"] / "_reviews").exists())

            status, body, _ = _request_json(
                base, "/api/case-review", payload={**request, "case": "safe-missing"}
            )
            self.assertEqual(status, 404)
            self.assertEqual(body["code"], "CASE_EVIDENCE_NOT_FOUND")

            request["target_sha256"] = hashlib.sha256(
                paths["evidence"].read_bytes()
            ).hexdigest()
            with mock.patch.object(
                cfd_studio.cfd_review, "create_review",
                side_effect=ValueError("REVIEW_TARGET_CHANGED"),
            ):
                status, body, _ = _request_json(
                    base, "/api/case-review", payload=request
                )
            self.assertEqual(status, 409)
            self.assertEqual(body["code"], "REVIEW_TARGET_CHANGED")

        review_dir = paths["case"] / "_reviews"
        records = list(review_dir.glob("*.case_review.v1.json")) if review_dir.exists() else []
        self.assertEqual(records, [])

    def test_body_solver_routes_reject_in_root_reparse_aliases(self):
        body_root = Path(self.tmp.name) / "_body_solver"
        target = body_root / "physical-case"
        target.mkdir(parents=True)
        alias = body_root / "alias-case"
        try:
            alias.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlinks unavailable: {exc}")
        self.assertIsNone(cfd_studio._body_solver_case("alias-case"))

    def test_case_review_local_guard_runs_before_parsing_or_service(self):
        paths = self._authoritative_body_case()
        with _studio_server() as base, mock.patch.object(
            cfd_studio.cfd_review, "create_review"
        ) as create:
            rejected_headers = [
                {
                    "Origin": "https://malicious.example",
                    "Sec-Fetch-Site": "cross-site",
                },
                {"Origin": base, "Sec-Fetch-Site": "same-site"},
                {
                    "Origin": "https://malicious.example",
                    "Sec-Fetch-Site": "same-origin",
                },
                {"Host": "project.example:8090"},
            ]
            for headers in rejected_headers:
                status, _, _ = _request_json(
                    base, "/api/case-review", raw=b"{", headers=headers,
                )
                self.assertEqual(status, 403, headers)
        create.assert_not_called()

    def test_case_review_unexpected_service_defect_is_500(self):
        paths = self._authoritative_body_case()
        request = {
            "case": paths["case"].name,
            "reviewer_id": "reviewer-1",
            "decision": "APPROVED",
            "reason": "reviewed",
            "target_sha256": hashlib.sha256(
                paths["evidence"].read_bytes()
            ).hexdigest(),
        }
        with _studio_server() as base, mock.patch.object(
            cfd_studio.cfd_review, "create_review",
            side_effect=RuntimeError("review storage offline"),
        ):
            status, body, _ = _request_json(
                base, "/api/case-review", payload=request
            )
        self.assertEqual(status, 500)
        self.assertNotEqual(body.get("code"), "REVIEW_TARGET_CHANGED")

    def test_body_results_ui_uses_case_health_catalog_copy_and_hides_raw_evidence(self):
        page = cfd_studio.PAGE_BODY_RESULTS
        self.assertIn("function renderCaseHealth(health,review)", page)
        self.assertIn("health.citation_status==='DESIGN_CITABLE'", page)
        self.assertIn("review&&review.status==='APPROVED'", page)
        self.assertIn("SCREENING_ONLY", page)
        self.assertIn("CITATION_BLOCKED", page)
        self.assertIn("NOT_EVALUATED", page)
        self.assertIn("case-health screening-only", page)
        self.assertIn("case-health citation-blocked", page)
        self.assertNotIn("case-health screening-only green", page)
        self.assertIn("health.checks.design_ready.impact", page)
        self.assertIn("next_actions", page)
        self.assertIn("<summary>근거 보기</summary>", page)
        self.assertIn("초기안 비교용 · 설계 인용 불가", page)
        self.assertIn("@media print", page)
        self.assertNotIn('<div id="trust"', page)
        primary_end = page.index("<details id=\"evidenceDetails\"")
        primary = page[page.index("id=\"caseHealth\""):primary_end]
        self.assertNotIn("residual", primary.lower())
        self.assertNotIn("sha256", primary.lower())
        self.assertIn("legacy_result_gate", page)

    def test_body_gci_study_accepts_three_or_four_project_local_cases(self):
        self.assertIn("메시 불확실성 확인", cfd_studio.PAGE_BODY_GCI)
        self.assertIn("/api/body-gci", cfd_studio.PAGE_BODY_GCI)
        self.assertIn("/api/start-body-gci-job", cfd_studio.PAGE_BODY_GCI)
        self.assertIn("완료 단계부터 재개", cfd_studio.PAGE_BODY_GCI)
        self.assertIn("최소 3.0 유동 교환시간", cfd_studio.PAGE_BODY_GCI)
        self.assertIn("체적가중", cfd_studio.PAGE_BODY_GCI)
        self.assertIn("Eça–Hoekstra", cfd_studio.PAGE_BODY_GCI)
        self.assertIn("매우 거친</th>", cfd_studio.PAGE_BODY_GCI)
        self.assertIn("v2만 가능(3.0 교환시간 미완료)",
                      cfd_studio.PAGE_BODY_GCI)
        root = Path(self.tmp.name) / "_body_solver"
        cases = []
        for name in ("fine", "medium", "coarse", "very-coarse"):
            case = root / name
            case.mkdir(parents=True)
            cases.append(case)
        manifest = {"contract": "grid_convergence.v1", "status": "PASS"}
        with mock.patch.object(
            cfd_studio.cfd_gci, "build_grid_convergence",
            return_value={"ok": True, "manifest": manifest},
        ) as build, mock.patch.object(
            cfd_studio.cfd_report, "generate_gci_report",
            return_value={"ok": True, "path": "report.html"},
        ) as report:
            result = cfd_studio.build_body_fitted_gci(
                ["fine", "medium", "coarse"]
            )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["manifest"], manifest)
        output = build.call_args.args[1]
        self.assertEqual(
            build.call_args.kwargs["contract"], "grid_convergence.v2"
        )
        self.assertEqual(output.name, "grid_convergence.json")
        self.assertEqual(output.parent.parent, Path(self.tmp.name) / "_body_gci")
        report.assert_called_once_with(output.parent)
        self.assertTrue(result["report_url"].startswith("/body-gci-report/gci-"))
        with mock.patch.object(
            cfd_studio.cfd_gci, "build_grid_convergence",
            return_value={"ok": True, "manifest": manifest},
        ) as build, mock.patch.object(
            cfd_studio.cfd_report, "generate_gci_report",
            return_value={"ok": True, "path": "report.html"},
        ):
            four = cfd_studio.build_body_fitted_gci(
                ["fine", "medium", "coarse", "very-coarse"]
            )
        self.assertTrue(four["ok"], four)
        self.assertEqual(build.call_args.kwargs["contract"], "grid_convergence.v3")
        self.assertFalse(cfd_studio.build_body_fitted_gci(["fine", "medium"])["ok"])
        self.assertFalse(cfd_studio.build_body_fitted_gci(
            ["fine", "medium", "../coarse"]
        )["ok"])

    def test_body_gci_scan_distinguishes_v2_and_v3_time_readiness(self):
        case = Path(self.tmp.name) / "_body_solver" / "thermal-one-ftt"
        case.mkdir(parents=True)
        (case / "result_manifest.json").write_text("{}", encoding="utf-8")
        loaded = {
            "name": case.name,
            "cell_count": 1000,
            "time_s": 100.0,
            "effective_grid_width_m": 0.1,
            "metrics": {},
            "time_window": {},
        }

        def load(_case, minimum_flow_through_fraction=1.0):
            if minimum_flow_through_fraction >= 3.0:
                raise cfd_studio.cfd_gci.GCIInputError(
                    "3.0 유동 교환시간 미충족"
                )
            return loaded

        with mock.patch.object(
            cfd_studio.cfd_gci, "load_time_window_case", side_effect=load
        ):
            result = cfd_studio.scan_body_gci_cases()

        row = result["cases"][0]
        self.assertTrue(row["eligible"])
        self.assertFalse(row["v3_eligible"])
        self.assertEqual(row["contract"], "grid_convergence.v2_ready")
        self.assertIn("3.0 유동 교환시간", row["v3_reason"])

    def test_release_readiness_is_available_from_dashboard(self):
        self.assertIn('href="/release-readiness"', cfd_studio.PAGE_DASH)
        self.assertIn("출시 준비 현황", cfd_studio.PAGE_RELEASE_READINESS)
        self.assertIn('/api/register-field-evidence',
                      cfd_studio.PAGE_RELEASE_READINESS)
        manifest = {"status": "BLOCKED", "limited_beta_ready": False,
                    "product_ready": False, "checks": []}
        with mock.patch.object(
            cfd_studio.release_audit, "build_release_audit",
            return_value={"ok": True, "manifest": manifest},
        ) as audit:
            result = cfd_studio.release_readiness_payload()
        self.assertEqual(result["manifest"], manifest)
        audit.assert_called_once_with(cfd_studio.ROOT)

    def test_dashboard_and_body_viewer_use_result_trust_contract(self):
        self.assertIn("function resultTrust(c)", cfd_studio.PAGE_DASH)
        self.assertIn("citation_status", cfd_studio.PAGE_DASH)
        self.assertIn("citable", cfd_studio.PAGE_DASH)
        self.assertIn("screening_engine", cfd_studio.PAGE_DASH)
        self.assertIn("function renderResultGate(gate)", cfd_studio.PAGE_BODY_RESULTS)
        self.assertIn("renderResultGate(j.result_gate)", cfd_studio.PAGE_BODY_RESULTS)
        self.assertIn("결과 평가 보류", cfd_studio.PAGE_DASH)

    def test_dashboard_makes_opening_resolution_warning_non_blocking_but_visible(self):
        """A novice sees that snapped diffuser velocity is not a jet-design result."""
        self.assertIn("function openingReview(c)", cfd_studio.PAGE_DASH)
        self.assertIn("CFD 적용 급기 m/s", cfd_studio.PAGE_DASH)
        self.assertIn("설계 면적 급기 m/s", cfd_studio.PAGE_DASH)
        self.assertIn("스냅 면적 기준", cfd_studio.PAGE_DASH)
        self.assertIn("제트/최대 유속 설계 판단 보류", cfd_studio.PAGE_DASH)
        self.assertIn("열·에너지 스크리닝은 가능", cfd_studio.PAGE_DASH)

    def test_body_result_button_does_not_call_ftt_completion_design_ready(self):
        self.assertNotIn("설계 검토 계산 완료", cfd_studio.PAGE_BODY_RESULTS)
        self.assertIn("payload&&payload.case_health", cfd_studio.PAGE_BODY_RESULTS)
        self.assertNotIn("designComplete=fttComplete&&payload&&payload.result_gate",
                         cfd_studio.PAGE_BODY_RESULTS)
        self.assertIn("fttComplete", cfd_studio.PAGE_BODY_RESULTS)

    def test_completed_field_case_is_discovered_and_registered_without_paths(self):
        source = Path(self.tmp.name) / "_imports" / "actual-site.dxf"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"dxf")
        geometry = Path(self.tmp.name) / "_imports" / "actual-site.geometry.json"
        geometry.write_text(json.dumps({"source": str(source)}), encoding="utf-8")
        surface = {
            "source": {"geometry_path": str(geometry)},
            "contract": "surface_manifest.v1",
        }
        occ = Path(self.tmp.name) / "_occ_geometry" / "actual-site"
        occ.mkdir(parents=True)
        (occ / "surface_manifest.json").write_text(
            json.dumps(surface), encoding="utf-8"
        )
        mesh = Path(self.tmp.name) / "_body_mesh" / "actual-site-detailed"
        mesh.mkdir(parents=True)
        mesh_manifest = {"contract": "mesh_manifest.v1", "status": "PASS"}
        (mesh / "mesh_manifest.json").write_text(
            json.dumps(mesh_manifest), encoding="utf-8"
        )
        (mesh / "surface_manifest.json").write_text(
            json.dumps(surface), encoding="utf-8"
        )
        solver = Path(self.tmp.name) / "_body_solver" / "actual-site-thermal"
        solver.mkdir(parents=True)
        (solver / "mesh_manifest.json").write_text(
            json.dumps(mesh_manifest), encoding="utf-8"
        )
        (solver / "run_manifest.json").write_text(json.dumps({
            "engine": "body_fitted_buoyant_urans", "status": "PASS",
            "design_ready": True,
        }), encoding="utf-8")
        (solver / "result_manifest.json").write_text("{}", encoding="utf-8")

        design_citable = {
            "status": "PASS", "design_ready": True,
            "citation_status": "DESIGN_CITABLE", "citable": True,
            "blockers": [],
        }
        with mock.patch.object(
            cfd_studio.cfd_result_gate, "evaluate_body_fitted_case",
            return_value=design_citable,
        ):
            candidates = cfd_studio.field_evidence_candidates()
        self.assertEqual(candidates["cases"], [{
            "case": "actual-site-thermal", "eligible": True,
            "source": "actual-site.dxf",
        }])
        self.assertFalse(cfd_studio.record_field_evidence(
            "actual-site-thermal", False
        )["ok"])
        with mock.patch.object(
            cfd_studio.cfd_result_gate, "evaluate_body_fitted_case",
            return_value=design_citable,
        ), mock.patch.object(
            cfd_studio.field_acceptance, "build_field_acceptance",
            return_value={"ok": True, "manifest_path": "evidence.json"},
        ) as build:
            result = cfd_studio.record_field_evidence("actual-site-thermal", True)
        self.assertTrue(result["ok"])
        self.assertEqual(Path(build.call_args.args[0]), source)
        self.assertEqual(Path(build.call_args.args[1]), geometry)
        self.assertEqual(Path(build.call_args.args[2]), occ)
        self.assertEqual(Path(build.call_args.args[3]), mesh)
        self.assertEqual(Path(build.call_args.args[4]), solver)

        with mock.patch.object(
            cfd_studio.cfd_result_gate, "evaluate_body_fitted_case",
            return_value={
                "status": "NOT_EVALUATED", "design_ready": False,
                "citation_status": "NOT_EVALUATED", "citable": False,
                "blockers": ["numerical_quality"],
            },
        ):
            rejected = cfd_studio.field_evidence_candidates()
        self.assertEqual(rejected["cases"], [])

    def test_bundled_sample_result_is_disabled_before_field_registration(self):
        solver = Path(self.tmp.name) / "_body_solver" / "sample-thermal"
        solver.mkdir(parents=True)
        sample = Path(self.tmp.name) / "sample_plan.dxf"
        sample.write_text("sample", encoding="utf-8")
        with mock.patch.object(cfd_studio, "_field_case_chain", return_value={
            "ok": True,
            "source_dxf": str(sample),
        }):
            candidates = cfd_studio.field_evidence_candidates()

        self.assertEqual(len(candidates["cases"]), 1)
        row = candidates["cases"][0]
        self.assertFalse(row["eligible"])
        self.assertIn("샘플", row["reason"])
        self.assertIn("disabled", cfd_studio.PAGE_RELEASE_READINESS)

    def test_already_registered_field_drawing_is_disabled(self):
        solver = Path(self.tmp.name) / "_body_solver" / "actual-site-thermal"
        solver.mkdir(parents=True)
        source = Path(self.tmp.name) / "actual-site.dxf"
        source.write_text("actual-site", encoding="utf-8")
        source_hash = cfd_studio._file_sha256(source)
        evidence = (Path(self.tmp.name) / "_release_evidence" /
                    "field_dxf" / "actual-site.json")
        evidence.parent.mkdir(parents=True)
        evidence.write_text("{}", encoding="utf-8")
        with mock.patch.object(cfd_studio, "_field_case_chain", return_value={
            "ok": True, "source_dxf": str(source),
        }), mock.patch.object(
            cfd_studio.field_acceptance, "validate_evidence",
            return_value={"ok": True, "manifest": {
                "source_sha256": source_hash,
            }},
        ):
            candidates = cfd_studio.field_evidence_candidates()

        row = candidates["cases"][0]
        self.assertFalse(row["eligible"])
        self.assertIn("이미 검증 등록", row["reason"])

    def test_observed_uat_workflow_records_six_server_timed_tasks(self):
        evidence = (Path(self.tmp.name) / "_release_evidence" /
                    "field_dxf" / "actual-site.json")
        evidence.parent.mkdir(parents=True)
        evidence.write_text("{}", encoding="utf-8")
        with mock.patch.object(
            cfd_studio.field_acceptance, "validate_evidence",
            return_value={"ok": True, "manifest": {
                "source_dxf_path": "_imports/actual-site.dxf"
            }},
        ):
            candidates = cfd_studio.uat_field_evidence_candidates()
            self.assertEqual(candidates["cases"][0]["id"], "actual-site.json")
            started = cfd_studio.start_uat_session(
                "facility-A", "observer-B", "actual-site.json"
            )
            recovered = cfd_studio.start_uat_session(
                "FACILITY-a", "OBSERVER-b", "actual-site.json"
            )
            conflicting = cfd_studio.start_uat_session(
                "facility-A", "observer-C", "actual-site.json"
            )
        self.assertTrue(started["ok"], started)
        token = started["token"]
        self.assertTrue(recovered["ok"], recovered)
        self.assertTrue(recovered["resumed"])
        self.assertEqual(recovered["token"], token)
        self.assertFalse(conflicting["ok"])
        self.assertIn("진행 중 시험", conflicting["error"])
        resumed = cfd_studio.uat_session_status(token)
        self.assertTrue(resumed["ok"])
        self.assertEqual(resumed["task"], "launch_application")
        for index, task_id in enumerate(cfd_studio.uat_acceptance.TASKS):
            result = cfd_studio.record_uat_task(
                token, "PASS", index % 2, f"observed {task_id}"
            )
            self.assertTrue(result["ok"], result)
        self.assertTrue(result["done"])
        with mock.patch.object(
            cfd_studio.uat_acceptance, "build_uat_session",
            return_value={"ok": True, "manifest": {"status": "PASS"}},
        ) as build:
            finished = cfd_studio.finish_uat_session(token, [])
        self.assertTrue(finished["ok"])
        recorded_tasks = build.call_args.args[4]
        self.assertEqual([row["id"] for row in recorded_tasks],
                         list(cfd_studio.uat_acceptance.TASKS))
        self.assertFalse(cfd_studio._uat_draft_path(token).exists())
        self.assertIn("/api/uat/start", cfd_studio.PAGE_UAT_SESSION)

    def test_unfinished_uat_can_be_cancelled_without_creating_evidence(self):
        evidence = (Path(self.tmp.name) / "_release_evidence" /
                    "field_dxf" / "actual-site.json")
        evidence.parent.mkdir(parents=True)
        evidence.write_text("{}", encoding="utf-8")
        with mock.patch.object(
            cfd_studio.field_acceptance, "validate_evidence",
            return_value={"ok": True, "manifest": {
                "source_dxf_path": "_imports/actual-site.dxf"
            }},
        ):
            started = cfd_studio.start_uat_session(
                "facility-A", "observer-B", "actual-site.json"
            )
        token = started["token"]
        self.assertTrue(cfd_studio._uat_draft_path(token).is_file())
        first = cfd_studio.record_uat_task(
            token, "PASS", expected_task=cfd_studio.uat_acceptance.TASKS[0]
        )
        self.assertTrue(first["ok"], first)
        repeated = cfd_studio.record_uat_task(
            token, "PASS", expected_task=cfd_studio.uat_acceptance.TASKS[0]
        )
        self.assertFalse(repeated["ok"])
        self.assertIn("이미 저장", repeated["error"])
        self.assertEqual(
            cfd_studio.uat_session_status(token)["task"],
            cfd_studio.uat_acceptance.TASKS[1],
        )
        result = cfd_studio.cancel_uat_session(token)
        self.assertTrue(result["ok"], result)
        self.assertFalse(cfd_studio._uat_draft_path(token).exists())
        self.assertFalse(cfd_studio.cancel_uat_session(token)["ok"])
        self.assertIn("/api/uat/cancel", cfd_studio.PAGE_UAT_SESSION)
        self.assertIn("task:current", cfd_studio.PAGE_UAT_SESSION)
        self.assertIn("if(saving)return", cfd_studio.PAGE_UAT_SESSION)
        self.assertIn("if(finishing)return", cfd_studio.PAGE_UAT_SESSION)
        self.assertIn("saving||finishing", cfd_studio.PAGE_UAT_SESSION)

    def test_uat_finish_and_cancel_share_the_task_write_lock(self):
        class CountingLock:
            def __init__(self):
                self.entries = 0

            def __enter__(self):
                self.entries += 1

            def __exit__(self, *_args):
                return False

        lock = CountingLock()
        with mock.patch.object(cfd_studio, "UAT_LOCK", lock), \
                mock.patch.object(
                    cfd_studio, "_start_uat_session_unlocked",
                    return_value={"ok": True},
                ) as start, \
                mock.patch.object(
                    cfd_studio, "_finish_uat_session_unlocked",
                    return_value={"ok": True},
                ) as finish, mock.patch.object(
                    cfd_studio, "_cancel_uat_session_unlocked",
                    return_value={"ok": True},
                ) as cancel:
            self.assertTrue(cfd_studio.start_uat_session(
                "participant", "observer", "field.json"
            )["ok"])
            self.assertTrue(cfd_studio.finish_uat_session("token")["ok"])
            self.assertTrue(cfd_studio.cancel_uat_session("token")["ok"])

        self.assertEqual(lock.entries, 3)
        start.assert_called_once_with("participant", "observer", "field.json")
        finish.assert_called_once_with("token", None)
        cancel.assert_called_once_with("token")

    def test_completed_uat_participant_code_cannot_be_counted_twice(self):
        field = (Path(self.tmp.name) / "_release_evidence" /
                 "field_dxf" / "actual-site.json")
        field.parent.mkdir(parents=True)
        field.write_text("{}", encoding="utf-8")
        completed = (Path(self.tmp.name) / "_release_evidence" /
                     "uat" / "completed.json")
        completed.parent.mkdir(parents=True)
        completed.write_text("{}", encoding="utf-8")

        def validate_uat(path, _root):
            return {"ok": True, "manifest": {"participant_id": "Facility-A"}}

        with mock.patch.object(
            cfd_studio.uat_acceptance, "validate_evidence",
            side_effect=validate_uat,
        ), mock.patch.object(
            cfd_studio.field_acceptance, "validate_evidence",
            return_value={"ok": True, "manifest": {}},
        ):
            result = cfd_studio.start_uat_session(
                "facility-a", "observer-B", "actual-site.json"
            )
        self.assertFalse(result["ok"])
        self.assertIn("이미 완료", result["error"])

    def test_body_gci_automation_is_queued_once_and_persisted(self):
        geometry = Path(self.tmp.name) / "room.geometry.json"
        geometry.write_text(json.dumps({"contract": "geometry.v2"}), encoding="utf-8")
        cfd_studio.OPENFOAM_CAPABILITIES = {"thermal_detailed_ready": True}
        cfd_studio.FREECAD_CAPABILITIES = {"ok": True}
        thread = mock.Mock()
        with mock.patch.object(cfd_studio.threading, "Thread", return_value=thread):
            result = cfd_studio.start_body_gci_job(str(geometry))
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["queued"])
        self.assertRegex(result["study"], r"^gci-[0-9a-f]{12}$")
        thread.start.assert_called_once_with()
        payload = cfd_studio.body_gci_jobs_payload()
        self.assertEqual(payload["jobs"][0]["runtime_state"], "queued")
        duplicate = cfd_studio.resume_body_gci_job(result["study"])
        self.assertFalse(duplicate["ok"])
        self.assertIn("대기", duplicate["error"])

    def test_body_gci_geometry_is_selected_by_friendly_token(self):
        imports = Path(self.tmp.name) / "_imports"
        imports.mkdir()
        geometry = imports / "plant-room.geometry.json"
        source = json.loads(
            (self.repo / "cfd_benchmarks" / "g2_thermal" / "geometry.json")
            .read_text(encoding="utf-8")
        )
        source["source"] = "plant-room.dxf"
        geometry.write_text(json.dumps(source), encoding="utf-8")

        candidates = cfd_studio.body_gci_geometry_candidates()
        self.assertTrue(candidates["ok"])
        self.assertEqual(len(candidates["geometries"]), 1)
        candidate = candidates["geometries"][0]
        self.assertEqual(candidate["label"], "plant-room.dxf")
        self.assertTrue(candidate["ready"], candidate["issues"])

        with mock.patch.object(
            cfd_studio, "start_body_gci_job", return_value={"ok": True}
        ) as start:
            result = cfd_studio.start_body_gci_selection(candidate["id"])
        self.assertTrue(result["ok"])
        start.assert_called_once_with(str(geometry.resolve()), settings=None)
        self.assertIn("/api/body-gci-geometries", cfd_studio.PAGE_BODY_GCI)
        self.assertIn("검증할 도면 선택", cfd_studio.PAGE_BODY_GCI)
        self.assertIn("/new?geometry=", cfd_studio.PAGE_BODY_GCI)

    def test_field_pipeline_is_selected_by_token_and_queued_once(self):
        imports = Path(self.tmp.name) / "_imports"
        imports.mkdir()
        dxf = imports / "actual-plant-room.dxf"
        dxf.write_text("0\nSECTION\n0\nEOF\n", encoding="ascii")
        geometry = imports / "actual-plant-room.geometry.json"
        source = json.loads(
            (self.repo / "cfd_benchmarks" / "g2_thermal" / "geometry.json")
            .read_text(encoding="utf-8")
        )
        source["source"] = str(dxf.resolve())
        geometry.write_text(json.dumps(source), encoding="utf-8")
        candidate = cfd_studio.body_gci_geometry_candidates()["geometries"][0]
        cfd_studio.OPENFOAM_CAPABILITIES = {"thermal_detailed_ready": True}
        cfd_studio.FREECAD_CAPABILITIES = {"ok": True}
        cfd_studio.RUN["worker"] = True

        result = cfd_studio.start_field_pipeline_selection(candidate["id"])

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["queued"])
        self.assertRegex(result["job"], r"^field-[0-9a-f]{12}$")
        self.assertEqual(cfd_studio.RUN["queue"][0]["kind"], "field_pipeline")
        duplicate = cfd_studio.resume_field_pipeline_job(result["job"])
        self.assertFalse(duplicate["ok"])
        self.assertIn("대기", duplicate["error"])
        payload = cfd_studio.field_pipeline_jobs_payload()
        self.assertEqual(payload["jobs"][0]["runtime_state"], "queued")
        self.assertIn("/api/start-field-pipeline-job", cfd_studio.PAGE_FIELD_RUN)
        self.assertIn("3.0 FTT", cfd_studio.PAGE_FIELD_RUN)
        self.assertIn("이 도면 확인하기", cfd_studio.PAGE_FIELD_RUN)
        self.assertIn("/field-run", cfd_studio.PAGE_RELEASE_READINESS)

    def test_renamed_bundled_sample_cannot_start_long_field_pipeline(self):
        imports = Path(self.tmp.name) / "_imports"
        imports.mkdir()
        dxf = imports / "actual-looking-room.dxf"
        shutil.copyfile(self.repo / "sample_plan.dxf", dxf)
        geometry = imports / "actual-looking-room.geometry.json"
        source = json.loads(
            (self.repo / "cfd_benchmarks" / "g2_thermal" / "geometry.json")
            .read_text(encoding="utf-8")
        )
        source["source"] = str(dxf.resolve())
        geometry.write_text(json.dumps(source), encoding="utf-8")
        candidate = cfd_studio.body_gci_geometry_candidates()["geometries"][0]
        cfd_studio.OPENFOAM_CAPABILITIES = {"thermal_detailed_ready": True}
        cfd_studio.FREECAD_CAPABILITIES = {"ok": True}

        self.assertFalse(candidate["field_eligible"])
        result = cfd_studio.start_field_pipeline_selection(candidate["id"])
        self.assertFalse(result["ok"])
        self.assertIn("샘플", result["error"])

    def test_running_field_pipeline_reports_bounded_live_ftt_estimate(self):
        case = Path(self.tmp.name) / "_body_solver" / "field-design-thermal"
        case.mkdir(parents=True)
        (case / "run_manifest.json").write_text(json.dumps({
            "thermal_progress": {
                "latest_time_s": 100.0,
                "flow_through_time_s": 100.0,
                "required_duration_s": 300.0,
                "recommended_next_duration_s": 20.0,
                "last_solver_runtime_per_simulated_second": 10.0,
                "last_fixed_runtime_overhead_seconds": 0.0,
                "estimated_remaining_runtime_seconds": 2000.0,
            },
        }), encoding="utf-8")
        updated = (datetime.now(timezone.utc) - timedelta(seconds=110)).isoformat()
        manifest = {
            "job": "field-123456789abc", "status": "running",
            "stage": "design:thermal_continue", "updated_at": updated,
            "level": {
                "name": "design", "status": "running",
                "stage": "thermal_continue", "latest_time_s": 100.0,
                "flow_through_fraction": 1.0, "thermal_case": str(case),
            },
        }
        with (
            mock.patch.object(
                cfd_studio.field_pipeline_job, "list_jobs",
                return_value=[manifest],
            ),
            mock.patch.object(
                cfd_studio.field_pipeline_job, "active_run_lock",
                return_value={"pid": 4321, "started_at": updated},
            ),
        ):
            payload = cfd_studio.field_pipeline_jobs_payload()

        job = payload["jobs"][0]
        live = job["live_progress"]
        self.assertGreater(live["estimated_time_s"], 109.0)
        self.assertLess(live["estimated_time_s"], 113.0)
        self.assertEqual(live["next_checkpoint_time_s"], 120.0)
        self.assertAlmostEqual(
            job["level"]["estimated_flow_through_fraction"],
            live["estimated_time_s"] / 100.0,
        )
        self.assertIn("다음 저장", cfd_studio.PAGE_FIELD_RUN)
        self.assertIn("남은 실제시간 약", cfd_studio.PAGE_FIELD_RUN)
        self.assertIn("완료 예상", cfd_studio.PAGE_FIELD_RUN)
        self.assertIn("남은 실제시간 약", cfd_studio.PAGE_BODY_GCI)

    def test_preliminary_field_analysis_keeps_results_visible_with_citation_hold(self):
        manifest = {
            "job": "field-123456789abc",
            "status": "analysis_complete_not_citable",
            "stage": "complete",
            "result_case": str(Path(self.tmp.name) / "_body_solver" / "field-thermal"),
            "citation_status": "NOT_EVALUATED",
            "citation_blockers": ["gci", "numerical_quality"],
            "level": {
                "name": "design", "status": "PASS", "stage": "complete",
                "flow_through_fraction": 3.0,
            },
        }
        with (
            mock.patch.object(cfd_studio.field_pipeline_job, "list_jobs",
                              return_value=[manifest]),
            mock.patch.object(cfd_studio.field_pipeline_job, "active_run_lock",
                              return_value=None),
            mock.patch.object(cfd_studio.field_pipeline_job,
                              "review_terminal_job_citation",
                              return_value=manifest) as review,
        ):
            payload = cfd_studio.field_pipeline_jobs_payload()

        job = payload["jobs"][0]
        self.assertEqual(job["status"], "analysis_complete_not_citable")
        self.assertEqual(job["citation_status"], "NOT_EVALUATED")
        self.assertEqual(job["citation_blockers"], ["gci", "numerical_quality"])
        review.assert_called_once()
        self.assertTrue(job["results_url"].startswith("/body-results/"))
        self.assertTrue(job["report_url"].startswith("/body-report/"))
        self.assertIn("analysis_complete_not_citable", cfd_studio.PAGE_FIELD_RUN)
        self.assertIn("설계 인용 보류", cfd_studio.PAGE_FIELD_RUN)
        self.assertIn("설계 인용 증거 확인이 필요합니다", cfd_studio.PAGE_FIELD_RUN)

    def test_field_pipeline_page_and_status_api_are_served(self):
        server = cfd_studio.ThreadingHTTPServer(
            ("127.0.0.1", 0), cfd_studio.StudioHandler
        )
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/field-run", timeout=10) as response:
                page = response.read().decode("utf-8")
            with urllib.request.urlopen(
                base + "/api/field-pipeline-jobs", timeout=10
            ) as response:
                payload = json.load(response)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=10)

        self.assertIn("현장 도면 자동 해석", page)
        self.assertIn("/api/start-field-pipeline-job", page)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["jobs"], [])

    def test_body_gci_selection_rejects_unconfirmed_geometry(self):
        imports = Path(self.tmp.name) / "_imports"
        imports.mkdir()
        geometry = imports / "unconfirmed.geometry.json"
        geometry.write_text(json.dumps({
            "source": "unconfirmed.dxf", "units": "mm",
            "elements": {"zone": [{
                "kind": "polyline", "closed": True,
                "points": [[0, 0], [1000, 0], [1000, 1000], [0, 1000]],
                "confirmed": False,
            }]},
        }), encoding="utf-8")
        candidate = cfd_studio.body_gci_geometry_candidates()["geometries"][0]
        self.assertFalse(candidate["ready"])
        self.assertIn("해석할 방", candidate["issues"][0]["user_message"])
        self.assertIn("3D 의미 확인", candidate["issues"][0]["action"])
        codes = {item["code"] for item in candidate["issues"]}
        self.assertIn("SUPPLY_MISSING", codes)
        self.assertIn("EXHAUST_MISSING", codes)
        self.assertIn("HEAT_SOURCE_MISSING", codes)
        with mock.patch.object(cfd_studio, "start_body_gci_job") as start:
            result = cfd_studio.start_body_gci_selection(candidate["id"])
            advanced = cfd_studio.start_body_gci_selection("", str(geometry))
        self.assertFalse(result["ok"])
        self.assertIn("의미 확인", result["error"])
        self.assertFalse(advanced["ok"])
        self.assertIn("의미 확인", advanced["error"])
        start.assert_not_called()

    def test_body_gci_selection_revalidates_changed_geometry(self):
        imports = Path(self.tmp.name) / "_imports"
        imports.mkdir()
        geometry = imports / "plant-room.geometry.json"
        source = json.loads(
            (self.repo / "cfd_benchmarks" / "g2_thermal" / "geometry.json")
            .read_text(encoding="utf-8")
        )
        source["source"] = "plant-room.dxf"
        geometry.write_text(json.dumps(source), encoding="utf-8")
        candidate = cfd_studio.body_gci_geometry_candidates()["geometries"][0]
        self.assertTrue(candidate["ready"])

        source["elements"]["zone"][0]["confirmed"] = False
        geometry.write_text(json.dumps(source), encoding="utf-8")
        with mock.patch.object(cfd_studio, "start_body_gci_job") as start:
            result = cfd_studio.start_body_gci_selection(candidate["id"])
        self.assertFalse(result["ok"])
        self.assertIn("의미 확인", result["error"])
        start.assert_not_called()

    def test_body_gci_geometry_selection_http_flow(self):
        imports = Path(self.tmp.name) / "_imports"
        imports.mkdir()
        geometry = imports / "plant-room.geometry.json"
        source = json.loads(
            (self.repo / "cfd_benchmarks" / "g2_thermal" / "geometry.json")
            .read_text(encoding="utf-8")
        )
        source["source"] = "plant-room.dxf"
        geometry.write_text(json.dumps(source), encoding="utf-8")
        server = cfd_studio.ThreadingHTTPServer(
            ("127.0.0.1", 0), cfd_studio.StudioHandler
        )
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/body-gci", timeout=10) as response:
                page = response.read().decode("utf-8")
            with urllib.request.urlopen(
                base + "/api/body-gci-geometries", timeout=10
            ) as response:
                candidates = json.load(response)
            candidate = candidates["geometries"][0]
            request = urllib.request.Request(
                base + "/api/start-body-gci-job",
                data=json.dumps({"geometry_id": candidate["id"]}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with mock.patch.object(
                cfd_studio, "start_body_gci_job", return_value={"ok": True}
            ) as start:
                with urllib.request.urlopen(request, timeout=10) as response:
                    started = json.load(response)
            invalid = urllib.request.Request(
                base + "/api/start-body-gci-job",
                data=json.dumps({"geometry_id": "missing-token"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(invalid, timeout=10)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)

        self.assertIn("autoGeometrySelect", page)
        self.assertIn("autoGeometryHelp", page)
        self.assertTrue(candidate["ready"])
        self.assertTrue(started["ok"])
        start.assert_called_once_with(str(geometry.resolve()), settings=None)
        self.assertEqual(raised.exception.code, 400)

    def test_body_gci_semantic_confirmation_saves_ready_copy(self):
        imports = Path(self.tmp.name) / "_imports"
        imports.mkdir()
        geometry = imports / "plant-room.geometry.json"
        geometry.write_text(json.dumps({
            "source": "plant-room.dxf", "units": "mm",
            "params": {"wall": {"height": 2800.0}},
            "elements": {
                "zone": [{
                    "kind": "polyline", "closed": True,
                    "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
                    "confirmed": False,
                }],
                "equipment": [],
            },
        }), encoding="utf-8")
        terminals = [
            {"role": "supply", "type": "4way", "wall": "ceiling",
             "cx": 1.2, "cy": 2.2, "w": 0.4, "h": 0.4, "cmh": 500},
            {"role": "exhaust", "type": "grille", "wall": "ceiling",
             "cx": 3.3, "cy": 2.2, "w": 0.4, "h": 0.4, "cmh": 500},
        ]
        obstacles = [
            {"kind": "equipment", "x0": 2.0, "y0": 1.0,
             "x1": 2.5, "y1": 1.5, "h": 1.0, "kw": 1.0,
             "convective_fraction": 0.8,
             "evidence": "equipment_schedule:M03-001",
             "source_id": "equipment_DXF_EHP_01",
             "source_label": "DVM_INDOOR_01"},
        ]

        result = cfd_studio.confirm_body_gci_geometry(
            str(geometry), 0, 2.8, terminals, obstacles
        )

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["gci_ready"])
        confirmed = Path(result["geometry"])
        self.assertTrue(confirmed.is_file())
        self.assertNotEqual(confirmed, geometry)
        saved = json.loads(confirmed.read_text(encoding="utf-8"))
        original = json.loads(geometry.read_text(encoding="utf-8"))
        self.assertFalse(original["elements"]["zone"][0]["confirmed"])
        self.assertTrue(saved["elements"]["zone"][0]["confirmed"])
        self.assertEqual(
            saved["elements"]["zone"][0]["semantic"]["ceiling_height_mm"],
            2800.0,
        )
        self.assertEqual(len(saved["elements"]["equipment"]), 3)
        roles = [item["semantic"]["role"] for item in saved["elements"]["equipment"]]
        self.assertEqual(roles.count("supply"), 1)
        self.assertEqual(roles.count("exhaust"), 1)
        self.assertEqual(roles.count("heat_source"), 1)
        heat_source = next(item for item in saved["elements"]["equipment"]
                           if item["semantic"]["role"] == "heat_source")
        # The original geometry contains no such CAD element, so the submitted
        # CAD-looking browser ID must not become drawing provenance.
        self.assertEqual(heat_source["id"], "manual_heat_1")
        self.assertEqual(heat_source["source_ref"]["layer"], "USER_CONFIRMED")
        self.assertEqual(heat_source["source_ref"]["entity_type"], "UI_INPUT")
        self.assertEqual(heat_source["source_ref"]["source_id"], "manual_heat_1")
        self.assertEqual(heat_source["semantic"]["convective_fraction"], 0.8)
        self.assertEqual(heat_source["semantic"]["radiative_fraction"], 0.2)
        self.assertEqual(heat_source["semantic"]["input_power_w"], 1000.0)
        self.assertEqual(heat_source["semantic"]["convective_power_w"], 800.0)
        self.assertEqual(heat_source["semantic"]["radiative_power_w"], 200.0)
        self.assertEqual(heat_source["semantic"]["evidence"], "equipment_schedule:M03-001")
        self.assertIn("confirmBodyGeometry()", cfd_studio.PAGE_NEW)
        self.assertIn("/api/confirm-body-geometry", cfd_studio.PAGE_NEW)
        self.assertIn("loadRequestedGeometry()", cfd_studio.PAGE_NEW)
        self.assertIn("input[name=vmode][value=open]", cfd_studio.PAGE_NEW)
        self.assertIn("현장 자동 해석 시작", cfd_studio.PAGE_NEW)
        self.assertIn("메시 불확실성 검증", cfd_studio.PAGE_NEW)
        self.assertIn("총 배기", cfd_studio.PAGE_NEW)
        candidates = cfd_studio.body_gci_geometry_candidates()["geometries"]
        ready = [row for row in candidates if row["ready"]]
        self.assertEqual([row["path"] for row in ready], [str(confirmed.resolve())])

    def test_body_gci_confirmation_preserves_dxf_terminal_traceability(self):
        imports = Path(self.tmp.name) / "_imports"
        imports.mkdir()
        geometry = imports / "plant-room.geometry.json"
        geometry.write_text(json.dumps({
            "source": "plant-room.dxf", "units": "mm",
            "params": {"wall": {"height": 2800.0}},
            "elements": {
                "zone": [{
                    "kind": "polyline", "closed": True,
                    "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
                }],
                "equipment": [{
                    "id": "equipment_DXF_SA_01", "kind": "circle",
                    "center": [1200, 2200], "radius": 200,
                    "source_ref": {
                        "handle": "A1", "layer": "M-DUCT-SUPPLY",
                        "block_name": "SA_ROUND_01", "entity_type": "INSERT",
                    },
                    "semantic": {
                        "kind": "air_terminal", "role": "supply",
                        "terminal_type": "round",
                    },
                }],
            },
        }), encoding="utf-8")
        terminals = [
            {"role": "supply", "type": "round", "wall": "ceiling",
             "cx": 1.2, "cy": 2.2, "w": 0.4, "h": 0.4, "cmh": 500,
             "source_id": "equipment_DXF_SA_01", "source_label": "SA_ROUND_01",
             "source_type": "dxf_detected",
             # This value must never replace the original drawing reference.
             "source_ref": {"layer": "FORGED", "block_name": "FORGED"}},
            {"role": "exhaust", "type": "grille", "wall": "ceiling",
             "cx": 3.3, "cy": 2.2, "w": 0.4, "h": 0.4, "cmh": 500},
        ]
        obstacles = [{
            "kind": "equipment", "x0": 2.0, "y0": 1.0,
            "x1": 2.5, "y1": 1.5, "h": 1.0, "kw": 1.0,
            "convective_fraction": 0.8,
            "evidence": "equipment_schedule:M03-001",
            "source_id": "manual-heat-traceability",
            "source_type": "user_confirmed",
            "source_ref": {"layer": "USER_CONFIRMED",
                           "block_name": "MANUAL_HEAT_TRACEABILITY",
                           "entity_type": "UI_INPUT",
                           "source_id": "manual-heat-traceability"},
        }]

        result = cfd_studio.confirm_body_gci_geometry(
            str(geometry), 0, 2.8, terminals, obstacles,
        )

        self.assertTrue(result["ok"], result)
        saved = json.loads(Path(result["geometry"]).read_text(encoding="utf-8"))
        supply = next(item for item in saved["elements"]["equipment"]
                      if item["semantic"]["role"] == "supply")
        self.assertEqual(supply["id"], "equipment_DXF_SA_01")
        self.assertEqual(supply["source_ref"]["handle"], "A1")
        self.assertEqual(supply["source_ref"]["layer"], "M-DUCT-SUPPLY")
        self.assertEqual(supply["source_ref"]["block_name"], "SA_ROUND_01")
        self.assertNotEqual(supply["source_ref"]["layer"], "FORGED")
        self.assertEqual(supply["semantic"]["source_type"], "dxf_detected")
        exhaust = next(item for item in saved["elements"]["equipment"]
                       if item["semantic"]["role"] == "exhaust")
        self.assertEqual(exhaust["source_ref"]["layer"], "USER_CONFIRMED")
        self.assertEqual(exhaust["source_ref"]["entity_type"], "UI_INPUT")

    def test_body_gci_confirmation_assigns_traceable_id_to_manual_heat_row(self):
        """The normal UI add-row flow leaves source_id blank until save."""
        imports = Path(self.tmp.name) / "_imports"
        imports.mkdir()
        geometry = imports / "manual-heat.geometry.json"
        geometry.write_text(json.dumps({
            "source": "manual-heat.dxf", "units": "mm",
            "params": {"wall": {"height": 2800.0}},
            "elements": {"zone": [{
                "kind": "polyline", "closed": True,
                "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
            }], "equipment": []},
        }), encoding="utf-8")
        terminals = [
            {"role": "supply", "type": "4way", "wall": "ceiling",
             "cx": 1.0, "cy": 1.0, "w": 0.4, "h": 0.4, "cmh": 500},
            {"role": "exhaust", "type": "grille", "wall": "ceiling",
             "cx": 3.0, "cy": 2.0, "w": 0.4, "h": 0.4, "cmh": 500},
        ]
        obstacles = [{
            "kind": "equipment", "x0": 2.0, "y0": 1.0,
            "x1": 2.5, "y1": 1.5, "h": 1.0, "kw": 1.0,
            "convective_fraction": 0.8,
            "evidence": "equipment_schedule:M03-001",
            "source_type": "user_confirmed",
            # Browser-only provenance cannot be promoted into a CAD identity.
            "source_ref": {"handle": "FORGED", "layer": "FORGED"},
        }]

        result = cfd_studio.confirm_body_gci_geometry(
            str(geometry), 0, 2.8, terminals, obstacles,
        )

        self.assertTrue(result["ok"], result)
        saved = json.loads(Path(result["geometry"]).read_text(encoding="utf-8"))
        heat = next(item for item in saved["elements"]["equipment"]
                    if item["semantic"]["role"] == "heat_source")
        self.assertTrue(heat["id"].startswith("manual_heat_"))
        self.assertEqual(heat["semantic"]["source_type"], "user_confirmed")
        self.assertEqual(heat["source_ref"]["layer"], "USER_CONFIRMED")
        self.assertEqual(heat["source_ref"]["entity_type"], "UI_INPUT")
        self.assertEqual(heat["source_ref"]["source_id"], heat["id"])
        self.assertIsNone(heat["source_ref"].get("handle"))
        self.assertEqual(heat["source_ref"].get("handles"), [])

    def test_body_gci_confirmation_replaces_forged_manual_terminal_identity(self):
        """A browser-only terminal must get a server-owned UI_INPUT identity."""
        imports = Path(self.tmp.name) / "_imports"
        imports.mkdir()
        geometry = imports / "manual-terminal.geometry.json"
        geometry.write_text(json.dumps({
            "source": "manual-terminal.dxf", "units": "mm",
            "params": {"wall": {"height": 2800.0}},
            "elements": {"zone": [{
                "kind": "polyline", "closed": True,
                "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
            }], "equipment": []},
        }), encoding="utf-8")
        terminals = [
            {"role": "supply", "type": "4way", "wall": "ceiling",
             "cx": 1.0, "cy": 1.0, "w": 0.4, "h": 0.4, "cmh": 500,
             "source_id": "browser-claimed-cad-id",
             "source_type": "user_confirmed",
             "source_ref": {"handle": "FORGED", "layer": "FORGED"}},
            {"role": "exhaust", "type": "grille", "wall": "ceiling",
             "cx": 3.0, "cy": 2.0, "w": 0.4, "h": 0.4, "cmh": 500},
        ]
        obstacles = [{
            "kind": "equipment", "x0": 2.0, "y0": 1.0,
            "x1": 2.5, "y1": 1.5, "h": 1.0, "kw": 1.0,
            "convective_fraction": 0.8,
            "evidence": "equipment_schedule:M03-001",
        }]

        result = cfd_studio.confirm_body_gci_geometry(
            str(geometry), 0, 2.8, terminals, obstacles,
        )

        self.assertTrue(result["ok"], result)
        saved = json.loads(Path(result["geometry"]).read_text(encoding="utf-8"))
        supply = next(item for item in saved["elements"]["equipment"]
                      if item["semantic"]["role"] == "supply")
        self.assertEqual(supply["id"], "manual_terminal_1")
        self.assertEqual(supply["source_ref"]["layer"], "USER_CONFIRMED")
        self.assertEqual(supply["source_ref"]["entity_type"], "UI_INPUT")
        self.assertEqual(supply["source_ref"]["source_id"], "manual_terminal_1")
        self.assertIsNone(supply["source_ref"].get("handle"))
        self.assertEqual(supply["source_ref"].get("handles"), [])
        self.assertEqual(supply["semantic"]["source_type"], "user_confirmed")

    def test_body_gci_confirmation_replaces_forged_manual_heat_identity(self):
        """An unrecognised browser heat ID cannot become a CAD-like source."""
        imports = Path(self.tmp.name) / "_imports"
        imports.mkdir()
        geometry = imports / "manual-heat-forged.geometry.json"
        geometry.write_text(json.dumps({
            "source": "manual-heat-forged.dxf", "units": "mm",
            "params": {"wall": {"height": 2800.0}},
            "elements": {"zone": [{
                "kind": "polyline", "closed": True,
                "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
            }], "equipment": []},
        }), encoding="utf-8")
        terminals = [
            {"role": "supply", "type": "4way", "wall": "ceiling",
             "cx": 1.0, "cy": 1.0, "w": 0.4, "h": 0.4, "cmh": 500},
            {"role": "exhaust", "type": "grille", "wall": "ceiling",
             "cx": 3.0, "cy": 2.0, "w": 0.4, "h": 0.4, "cmh": 500},
        ]
        obstacles = [{
            "kind": "equipment", "x0": 2.0, "y0": 1.0,
            "x1": 2.5, "y1": 1.5, "h": 1.0, "kw": 1.0,
            "convective_fraction": 0.8,
            "evidence": "equipment_schedule:M03-001",
            "source_id": "browser-claimed-cad-id",
            "source_label": "FORGED CAD LABEL",
            "source_type": "user_confirmed",
            "source_ref": {"handle": "FORGED", "layer": "FORGED"},
        }]

        result = cfd_studio.confirm_body_gci_geometry(
            str(geometry), 0, 2.8, terminals, obstacles,
        )

        self.assertTrue(result["ok"], result)
        saved = json.loads(Path(result["geometry"]).read_text(encoding="utf-8"))
        heat = next(item for item in saved["elements"]["equipment"]
                    if item["semantic"]["role"] == "heat_source")
        self.assertEqual(heat["id"], "manual_heat_1")
        self.assertEqual(heat["source_ref"]["layer"], "USER_CONFIRMED")
        self.assertEqual(heat["source_ref"]["entity_type"], "UI_INPUT")
        self.assertEqual(heat["source_ref"]["source_id"], "manual_heat_1")
        self.assertIsNone(heat["source_ref"].get("handle"))
        self.assertEqual(heat["source_ref"].get("handles"), [])
        self.assertEqual(heat["source_label"], "MANUAL_HEAT_1")
        self.assertEqual(heat["semantic"]["source_type"], "user_confirmed")

    def test_body_gci_confirmation_avoids_dxf_id_when_allocating_manual_heat(self):
        """A server-generated manual ID must not collide with a CAD identity."""
        imports = Path(self.tmp.name) / "_imports"
        imports.mkdir()
        geometry = imports / "manual-heat-collision.geometry.json"
        geometry.write_text(json.dumps({
            "source": "manual-heat-collision.dxf", "units": "mm",
            "params": {"wall": {"height": 2800.0}},
            "elements": {
                "zone": [{
                    "kind": "polyline", "closed": True,
                    "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
                }],
                "equipment": [{
                    "id": "manual_heat_1", "kind": "polyline", "closed": True,
                    "points": [[100, 100], [500, 100], [500, 500], [100, 500]],
                    "source_ref": {"handle": "CAD-H1", "layer": "M-EQPM"},
                    "semantic": {"kind": "equipment", "role": "solid"},
                }],
            },
        }), encoding="utf-8")
        terminals = [
            {"role": "supply", "type": "4way", "wall": "ceiling",
             "cx": 1.0, "cy": 1.0, "w": 0.4, "h": 0.4, "cmh": 500},
            {"role": "exhaust", "type": "grille", "wall": "ceiling",
             "cx": 3.0, "cy": 2.0, "w": 0.4, "h": 0.4, "cmh": 500},
        ]
        obstacles = [{
            "kind": "equipment", "x0": 2.0, "y0": 1.0,
            "x1": 2.5, "y1": 1.5, "h": 1.0, "kw": 1.0,
            "convective_fraction": 0.8,
            "evidence": "equipment_schedule:M03-001",
        }]

        result = cfd_studio.confirm_body_gci_geometry(
            str(geometry), 0, 2.8, terminals, obstacles,
        )

        self.assertTrue(result["ok"], result)
        saved = json.loads(Path(result["geometry"]).read_text(encoding="utf-8"))
        heat = next(item for item in saved["elements"]["equipment"]
                    if item["semantic"]["role"] == "heat_source")
        self.assertEqual(heat["id"], "manual_heat_1_2")
        self.assertEqual(heat["source_ref"]["source_id"], "manual_heat_1_2")

    def test_body_gci_confirmation_excludes_ui_input_handles_from_dxf_source_map(self):
        """A stored UI_INPUT row with a forged handle must never become DXF evidence."""
        imports = Path(self.tmp.name) / "_imports"
        imports.mkdir()
        geometry = imports / "manual-source-map.geometry.json"
        geometry.write_text(json.dumps({
            "source": "manual-source-map.dxf", "units": "mm",
            "params": {"wall": {"height": 2800.0}},
            "elements": {
                "zone": [{
                    "kind": "polyline", "closed": True,
                    "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
                }],
                "equipment": [{
                    "id": "manual_heat_1", "kind": "polyline", "closed": True,
                    "points": [[1800, 800], [2600, 800], [2600, 1400], [1800, 1400]],
                    "source_ref": {
                        "handle": "FORGED", "layer": "USER_CONFIRMED",
                        "entity_type": "UI_INPUT", "source_id": "manual_heat_1",
                    },
                    "semantic": {"kind": "equipment", "role": "heat_source",
                                 "source_type": "user_confirmed"},
                }],
            },
        }), encoding="utf-8")
        terminals = [
            {"role": "supply", "type": "4way", "wall": "ceiling",
             "cx": 1.0, "cy": 1.0, "w": 0.4, "h": 0.4, "cmh": 500},
            {"role": "exhaust", "type": "grille", "wall": "ceiling",
             "cx": 3.0, "cy": 2.0, "w": 0.4, "h": 0.4, "cmh": 500},
        ]
        obstacles = [{
            "kind": "equipment", "x0": 2.0, "y0": 1.0,
            "x1": 2.5, "y1": 1.5, "h": 1.0, "kw": 1.0,
            "convective_fraction": 0.8,
            "evidence": "equipment_schedule:M03-001",
            "source_id": "manual_heat_1", "source_type": "user_confirmed",
            "source_ref": {"handle": "BROWSER_FORGED", "layer": "FORGED"},
        }]

        result = cfd_studio.confirm_body_gci_geometry(
            str(geometry), 0, 2.8, terminals, obstacles,
        )

        self.assertTrue(result["ok"], result)
        saved = json.loads(Path(result["geometry"]).read_text(encoding="utf-8"))
        heat = next(item for item in saved["elements"]["equipment"]
                    if item["semantic"]["role"] == "heat_source")
        self.assertEqual(heat["id"], "manual_heat_1")
        self.assertEqual(heat["source_ref"]["layer"], "USER_CONFIRMED")
        self.assertIsNone(heat["source_ref"].get("handle"))
        self.assertEqual(heat["source_ref"].get("handles"), [])

    def test_body_gci_confirmation_marks_changed_dxf_terminal_as_user_override(self):
        """Changed CFD conditions may keep the DXF reference but not its claim."""
        imports = Path(self.tmp.name) / "_imports"
        imports.mkdir()
        geometry = imports / "plant-room.geometry.json"
        geometry.write_text(json.dumps({
            "source": "plant-room.dxf", "units": "mm",
            "params": {"wall": {"height": 2800.0}},
            "elements": {
                "zone": [{"kind": "polyline", "closed": True,
                          "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}],
                "equipment": [{
                    "id": "equipment_DXF_SA_01", "kind": "circle",
                    "center": [1200, 2200], "radius": 200,
                    "source_ref": {"handle": "A1", "layer": "M-SUPPLY",
                                   "block_name": "SA-01", "entity_type": "INSERT"},
                    "semantic": {"kind": "air_terminal", "role": "supply",
                                 "terminal_type": "round", "host_surface": "ceiling",
                                 "airflow_cmh": 500},
                }],
            },
        }), encoding="utf-8")
        terminals = [
            {"role": "supply", "type": "round", "wall": "ceiling",
             "cx": 1.2, "cy": 2.2, "w": 0.4, "h": 0.4, "cmh": 600,
            # A client can lie about the review marker.  Drawing membership
            # comes from the original source id, not client-supplied flags.
            "source_id": "equipment_DXF_SA_01", "source_label": "FORGED",
            "source_type": "user_confirmed",
            "source_ref": {"handle": "FORGED", "layer": "FORGED"}},
            {"role": "exhaust", "type": "grille", "wall": "ceiling",
             "cx": 3.3, "cy": 2.2, "w": 0.4, "h": 0.4, "cmh": 600},
        ]
        obstacles = [{"kind": "equipment", "x0": 2.0, "y0": 1.0,
                      "x1": 2.5, "y1": 1.5, "h": 1.0, "kw": 1.0,
                      "convective_fraction": 0.8,
                      "evidence": "equipment_schedule:M03-001",
                      "source_id": "manual-heat-01",
                      "source_label": "MANUAL_HEAT_01",
                      "source_type": "user_confirmed",
                      "source_ref": {"layer": "USER_CONFIRMED",
                                     "block_name": "MANUAL_HEAT_01",
                                     "entity_type": "UI_INPUT",
                                     "source_id": "manual-heat-01"}}]

        result = cfd_studio.confirm_body_gci_geometry(
            str(geometry), 0, 2.8, terminals, obstacles,
        )

        self.assertTrue(result["ok"], result)
        saved = json.loads(Path(result["geometry"]).read_text(encoding="utf-8"))
        supply = next(item for item in saved["elements"]["equipment"]
                      if item["semantic"]["role"] == "supply")
        self.assertEqual(supply["id"], "equipment_DXF_SA_01")
        self.assertEqual(supply["source_ref"]["handle"], "A1")
        self.assertEqual(supply["source_ref"]["layer"], "M-SUPPLY")
        self.assertEqual(supply["source_label"], "SA-01")
        self.assertEqual(supply["semantic"]["source_type"], "user_confirmed")
        self.assertTrue(supply["semantic"]["override_of_dxf"])

    def test_body_gci_confirmation_marks_unresolved_dxf_role_as_user_override(self):
        """A DXF terminal with no identified SA/RA role needs user provenance."""
        imports = Path(self.tmp.name) / "_imports"
        imports.mkdir()
        geometry = imports / "plant-room.geometry.json"
        geometry.write_text(json.dumps({
            "source": "plant-room.dxf", "units": "mm",
            "params": {"wall": {"height": 2800.0}},
            "elements": {
                "zone": [{"kind": "polyline", "closed": True,
                          "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}],
                "equipment": [{
                    "id": "equipment_DXF_UNRESOLVED", "kind": "circle",
                    "center": [1200, 2200], "radius": 200,
                    "source_ref": {"handle": "A1", "layer": "M-DUCT"},
                    "semantic": {"kind": "air_terminal", "role": "unresolved",
                                 "terminal_type": "round", "host_surface": "ceiling"},
                }],
            },
        }), encoding="utf-8")
        terminals = [
            {"role": "supply", "type": "round", "wall": "ceiling",
             "cx": 1.2, "cy": 2.2, "w": 0.4, "h": 0.4, "cmh": 500,
             "source_id": "equipment_DXF_UNRESOLVED", "source_type": "dxf_detected"},
            {"role": "exhaust", "type": "grille", "wall": "ceiling",
             "cx": 3.3, "cy": 2.2, "w": 0.4, "h": 0.4, "cmh": 500},
        ]
        obstacles = [{"kind": "equipment", "x0": 2.0, "y0": 1.0,
                      "x1": 2.5, "y1": 1.5, "h": 1.0, "kw": 1.0,
                      "convective_fraction": 0.8,
                      "evidence": "equipment_schedule:M03-001",
                      "source_id": "manual-heat-unresolved",
                      "source_type": "user_confirmed",
                      "source_ref": {"layer": "USER_CONFIRMED",
                                     "block_name": "MANUAL_HEAT_UNRESOLVED",
                                     "entity_type": "UI_INPUT",
                                     "source_id": "manual-heat-unresolved"}}]

        result = cfd_studio.confirm_body_gci_geometry(
            str(geometry), 0, 2.8, terminals, obstacles,
        )

        self.assertTrue(result["ok"], result)
        saved = json.loads(Path(result["geometry"]).read_text(encoding="utf-8"))
        supply = next(item for item in saved["elements"]["equipment"]
                      if item["semantic"]["role"] == "supply")
        self.assertEqual(supply["semantic"]["source_type"], "user_confirmed")
        self.assertTrue(supply["semantic"]["override_of_dxf"])

    def test_drawing_terminal_import_keeps_traceability_in_opening_rows(self):
        page = cfd_studio.PAGE_NEW
        start = page.index("function opFromDrawing()")
        end = page.index("function confirmTerminalRoles()", start)
        importer = page[start:end]

        self.assertIn("source_id:d.source_id||''", importer)
        self.assertIn("source_label:d.source_label||d.name||''", importer)
        self.assertIn("source_ref:{...(d.source_ref||{})}", importer)
        self.assertIn("source_type:d.source_type||''", importer)
        self.assertIn("override_of_dxf:d.override_of_dxf===true", importer)

    def test_drawing_obstacle_import_keeps_override_marker(self):
        """Reloading a reviewed DXF obstacle must retain its override state."""
        page = cfd_studio.PAGE_NEW
        start = page.index("function obFromDrawing()")
        end = page.index("function opFromDrawing()", start)
        importer = page[start:end]

        self.assertIn("override_of_dxf:o.override_of_dxf===true", importer)

    def test_obstacle_review_ui_does_not_mislabel_manual_identity_as_dxf(self):
        """A server-owned UI_INPUT id must remain visibly manual after reload."""
        page = cfd_studio.PAGE_NEW
        start = page.index("function obRender()")
        end = page.index("function obFromDrawing()", start)
        renderer = page[start:end]

        self.assertIn("r.source_type==='dxf_detected'", renderer)
        self.assertIn("r.override_of_dxf", renderer)
        self.assertNotIn("const source=r.source_id\n", renderer)

    def test_terminal_review_ui_distinguishes_dxf_origin_from_user_override(self):
        """The opening table must not show an edited terminal as raw DXF data."""
        page = cfd_studio.PAGE_NEW
        start = page.index("function opMatchesDxf(")
        end = page.index("function opValid()", start)
        editor = page[start:end]

        self.assertIn("dxf_defaults", editor)
        self.assertIn("DXF 원본", editor)
        self.assertIn("사용자 변경", editor)
        self.assertIn("override_of_dxf", editor)

    def test_terminal_role_confirmation_marks_dxf_recommendation_as_user_reviewed(self):
        page = cfd_studio.PAGE_NEW
        start = page.index("function confirmTerminalRoles()")
        end = page.index("function vmodeCh()", start)
        confirm = page[start:end]

        self.assertIn("source_type='user_confirmed'", confirm)
        self.assertIn("override_of_dxf=true", confirm)

    def test_body_gci_confirmation_rejects_unknown_dxf_terminal_identity(self):
        imports = Path(self.tmp.name) / "_imports"
        imports.mkdir()
        geometry = imports / "plant-room.geometry.json"
        geometry.write_text(json.dumps({
            "source": "plant-room.dxf", "units": "mm",
            "params": {"wall": {"height": 2800.0}},
            "elements": {"zone": [{
                "kind": "polyline", "closed": True,
                "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
            }], "equipment": []},
        }), encoding="utf-8")
        terminals = [
            {"role": "supply", "type": "round", "wall": "ceiling",
             "cx": 1.2, "cy": 2.2, "w": 0.4, "h": 0.4, "cmh": 500,
             "source_id": "not-in-drawing", "source_type": "dxf_detected"},
            {"role": "exhaust", "type": "grille", "wall": "ceiling",
             "cx": 3.3, "cy": 2.2, "w": 0.4, "h": 0.4, "cmh": 500},
        ]
        obstacles = [{
            "kind": "equipment", "x0": 2.0, "y0": 1.0,
            "x1": 2.5, "y1": 1.5, "h": 1.0, "kw": 1.0,
            "convective_fraction": 0.8,
            "evidence": "equipment_schedule:M03-001",
        }]

        result = cfd_studio.confirm_body_gci_geometry(
            str(geometry), 0, 2.8, terminals, obstacles,
        )

        self.assertFalse(result["ok"])
        self.assertIn("DXF", result["error"])

    def test_body_gci_confirmation_rejects_duplicate_dxf_terminal_identity(self):
        """One drawing terminal cannot be confirmed twice as two CFD patches."""
        imports = Path(self.tmp.name) / "_imports"
        imports.mkdir()
        geometry = imports / "plant-room.geometry.json"
        geometry.write_text(json.dumps({
            "source": "plant-room.dxf", "units": "mm",
            "params": {"wall": {"height": 2800.0}},
            "elements": {
                "zone": [{"kind": "polyline", "closed": True,
                          "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}],
                "equipment": [{
                    "id": "equipment_DXF_SA_01", "kind": "circle",
                    "center": [1200, 2200], "radius": 200,
                    "source_ref": {"handle": "A1", "layer": "M-SUPPLY"},
                    "semantic": {"kind": "air_terminal", "role": "supply",
                                 "terminal_type": "round", "airflow_cmh": 500},
                }],
            },
        }), encoding="utf-8")
        terminals = [
            {"role": "supply", "type": "round", "wall": "ceiling",
             "cx": 1.2, "cy": 2.2, "w": 0.4, "h": 0.4, "cmh": 500,
             "source_id": "equipment_DXF_SA_01", "source_type": "dxf_detected"},
            {"role": "exhaust", "type": "grille", "wall": "ceiling",
             "cx": 3.3, "cy": 2.2, "w": 0.4, "h": 0.4, "cmh": 500,
             "source_id": "EQUIPMENT_dxf_sa_01", "source_type": "user_confirmed"},
        ]
        obstacles = [{"kind": "equipment", "x0": 2.0, "y0": 1.0,
                      "x1": 2.5, "y1": 1.5, "h": 1.0, "kw": 1.0,
                      "convective_fraction": 0.8,
                      "evidence": "equipment_schedule:M03-001"}]

        result = cfd_studio.confirm_body_gci_geometry(
            str(geometry), 0, 2.8, terminals, obstacles,
        )

        self.assertFalse(result["ok"])
        self.assertIn("source_id", result["error"])

    def test_body_gci_confirmation_rejects_unknown_dxf_obstacle_identity(self):
        """A browser must not invent a DXF equipment ID for a heat source."""
        imports = Path(self.tmp.name) / "_imports"
        imports.mkdir()
        geometry = imports / "plant-room.geometry.json"
        geometry.write_text(json.dumps({
            "source": "plant-room.dxf", "units": "mm",
            "params": {"wall": {"height": 2800.0}},
            "elements": {"zone": [{"kind": "polyline", "closed": True,
                          "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}],
                         "equipment": []},
        }), encoding="utf-8")
        terminals = [
            {"role": "supply", "type": "round", "wall": "ceiling",
             "cx": 1.0, "cy": 1.0, "w": 0.4, "h": 0.4, "cmh": 500},
            {"role": "exhaust", "type": "grille", "wall": "ceiling",
             "cx": 3.0, "cy": 2.0, "w": 0.4, "h": 0.4, "cmh": 500},
        ]
        obstacles = [{
            "kind": "equipment", "x0": 2.0, "y0": 1.0,
            "x1": 2.5, "y1": 1.5, "h": 1.0, "kw": 1.0,
            "convective_fraction": 0.8,
            "evidence": "equipment_schedule:M03-001",
            "source_id": "invented_DXF_EHP", "source_type": "user_confirmed",
            "override_of_dxf": True,
        }]

        result = cfd_studio.confirm_body_gci_geometry(
            str(geometry), 0, 2.8, terminals, obstacles,
        )

        self.assertFalse(result["ok"])
        self.assertIn("DXF", result["error"])

    def test_body_gci_confirmation_rejects_casefold_ambiguous_dxf_identity(self):
        """Case-variant source IDs must not select an arbitrary DXF block."""
        imports = Path(self.tmp.name) / "_imports"
        imports.mkdir()
        geometry = imports / "ambiguous-dxf.geometry.json"
        geometry.write_text(json.dumps({
            "source": "ambiguous.dxf", "units": "mm",
            "params": {"wall": {"height": 2800.0}},
            "elements": {
                "zone": [{"kind": "polyline", "closed": True,
                          "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}],
                "equipment": [
                    {"id": "EHP1", "kind": "polyline", "closed": True,
                     "points": [[1500, 800], [1900, 800], [1900, 1200], [1500, 1200]],
                     "source_ref": {"handle": "H1", "layer": "M-EQPM"},
                     "semantic": {"kind": "equipment", "role": "solid"}},
                    {"id": "ehp1", "kind": "polyline", "closed": True,
                     "points": [[2200, 800], [2600, 800], [2600, 1200], [2200, 1200]],
                     "source_ref": {"handle": "H2", "layer": "M-EQPM"},
                     "semantic": {"kind": "equipment", "role": "solid"}},
                ],
            },
        }), encoding="utf-8")
        terminals = [
            {"role": "supply", "type": "4way", "wall": "ceiling",
             "cx": 1.0, "cy": 1.0, "w": 0.4, "h": 0.4, "cmh": 500},
            {"role": "exhaust", "type": "grille", "wall": "ceiling",
             "cx": 3.0, "cy": 2.0, "w": 0.4, "h": 0.4, "cmh": 500},
        ]
        obstacles = [{
            "kind": "equipment", "x0": 1.5, "y0": 0.8,
            "x1": 1.9, "y1": 1.2, "h": 1.0, "kw": 1.0,
            "convective_fraction": 0.8,
            "evidence": "equipment_schedule:M03-001",
            "source_id": "EHP1", "source_type": "user_confirmed",
        }]

        result = cfd_studio.confirm_body_gci_geometry(
            str(geometry), 0, 2.8, terminals, obstacles,
        )

        self.assertFalse(result["ok"])
        self.assertIn("source_id", result["error"])

    def test_body_gci_confirmation_forces_original_dxf_obstacle_reference(self):
        """A forged browser reference cannot replace the drawing equipment origin."""
        imports = Path(self.tmp.name) / "_imports"
        imports.mkdir()
        geometry = imports / "plant-room.geometry.json"
        original_ref = {"handle": "EHP-A1", "layer": "DVM_INDOOR",
                        "block_name": "DVM_INDOOR_01", "entity_type": "INSERT"}
        geometry.write_text(json.dumps({
            "source": "plant-room.dxf", "units": "mm",
            "params": {"wall": {"height": 2800.0}},
            "elements": {
                "zone": [{"kind": "polyline", "closed": True,
                          "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}],
                "equipment": [{
                    "id": "equipment_DXF_EHP_01", "confirmed": True,
                    "points": [[1800, 800], [2600, 800], [2600, 1400], [1800, 1400]],
                    "source_ref": original_ref,
                    "semantic": {"kind": "equipment", "role": "heat_source",
                                 "height_mm": 900, "power_kw": 5.0,
                                 "convective_fraction": 0.8,
                                 "evidence": "equipment_schedule:M03-001"},
                }],
            },
        }), encoding="utf-8")
        terminals = [
            {"role": "supply", "type": "round", "wall": "ceiling",
             "cx": 1.0, "cy": 1.0, "w": 0.4, "h": 0.4, "cmh": 500},
            {"role": "exhaust", "type": "grille", "wall": "ceiling",
             "cx": 3.0, "cy": 2.0, "w": 0.4, "h": 0.4, "cmh": 500},
        ]
        obstacles = [{
            "kind": "equipment", "x0": 1.8, "y0": 0.8,
            "x1": 2.6, "y1": 1.4, "h": 0.9, "kw": 5.0,
            "convective_fraction": 0.8,
            "evidence": "equipment_schedule:M03-001",
            # A client can omit the override marker.  The original source id
            # still makes this drawing-derived and the forged ref is ignored.
            "source_id": "equipment_DXF_EHP_01", "source_label": "FORGED",
            "source_type": "user_confirmed",
            "source_ref": {"handle": "FORGED", "layer": "FORGED"},
        }]

        result = cfd_studio.confirm_body_gci_geometry(
            str(geometry), 0, 2.8, terminals, obstacles,
        )

        self.assertTrue(result["ok"], result)
        saved = json.loads(Path(result["geometry"]).read_text(encoding="utf-8"))
        heat = next(item for item in saved["elements"]["equipment"]
                    if item["semantic"]["role"] == "heat_source")
        self.assertEqual(heat["source_ref"]["handle"], original_ref["handle"])
        self.assertEqual(heat["source_ref"]["layer"], original_ref["layer"])
        self.assertEqual(heat["source_ref"]["block_name"], original_ref["block_name"])
        self.assertNotEqual(heat["source_ref"]["handle"], "FORGED")
        self.assertEqual(heat["source_label"], "DVM_INDOOR_01")
        self.assertEqual(heat["semantic"]["source_type"], "user_confirmed")
        self.assertTrue(heat["semantic"]["override_of_dxf"])

    def test_body_gci_confirmation_rejects_duplicate_dxf_obstacle_identity(self):
        """One DXF equipment item cannot create two confirmed heat sources."""
        imports = Path(self.tmp.name) / "_imports"
        imports.mkdir()
        geometry = imports / "plant-room.geometry.json"
        geometry.write_text(json.dumps({
            "source": "plant-room.dxf", "units": "mm",
            "params": {"wall": {"height": 2800.0}},
            "elements": {
                "zone": [{"kind": "polyline", "closed": True,
                          "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}],
                "equipment": [{
                    "id": "equipment_DXF_EHP_01",
                    "points": [[1800, 800], [2600, 800], [2600, 1400], [1800, 1400]],
                    "source_ref": {"handle": "EHP-A1", "layer": "DVM_INDOOR"},
                    "semantic": {"kind": "equipment", "role": "heat_source"},
                }],
            },
        }), encoding="utf-8")
        terminals = [
            {"role": "supply", "type": "round", "wall": "ceiling",
             "cx": 1.0, "cy": 1.0, "w": 0.4, "h": 0.4, "cmh": 500},
            {"role": "exhaust", "type": "grille", "wall": "ceiling",
             "cx": 3.0, "cy": 2.0, "w": 0.4, "h": 0.4, "cmh": 500},
        ]
        obstacle = {
            "kind": "equipment", "x0": 1.8, "y0": 0.8,
            "x1": 2.6, "y1": 1.4, "h": 1.0, "kw": 1.0,
            "convective_fraction": 0.8,
            "evidence": "equipment_schedule:M03-001",
            "source_id": "equipment_DXF_EHP_01", "source_type": "dxf_detected",
        }

        reviewed_override = dict(obstacle)
        reviewed_override.update({
            "source_id": "EQUIPMENT_dxf_ehp_01",
            "source_type": "user_confirmed",
        })
        result = cfd_studio.confirm_body_gci_geometry(
            str(geometry), 0, 2.8, terminals, [obstacle, reviewed_override],
        )

        self.assertFalse(result["ok"])
        self.assertIn("source_id", result["error"])

    def test_body_gci_confirmation_rejects_cross_kind_dxf_source_ids(self):
        """A terminal cannot claim an obstacle id, or an obstacle a terminal id."""
        imports = Path(self.tmp.name) / "_imports"
        imports.mkdir()
        geometry = imports / "plant-room.geometry.json"
        geometry.write_text(json.dumps({
            "source": "plant-room.dxf", "units": "mm",
            "params": {"wall": {"height": 2800.0}},
            "elements": {
                "zone": [{"kind": "polyline", "closed": True,
                          "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]]}],
                "equipment": [
                    {
                        "id": "terminal_DXF_01", "kind": "circle",
                        "center": [1000, 1000], "radius": 200,
                        "source_ref": {"handle": "SA-1", "layer": "M-SUPPLY"},
                        "semantic": {"kind": "air_terminal", "role": "supply",
                                     "terminal_type": "round", "airflow_cmh": 500},
                    },
                    {
                        "id": "obstacle_DXF_01", "kind": "polyline", "closed": True,
                        "points": [[1800, 800], [2600, 800], [2600, 1400], [1800, 1400]],
                        "source_ref": {"handle": "EHP-1", "layer": "DVM_INDOOR"},
                        "semantic": {"kind": "equipment", "role": "solid"},
                    },
                ],
            },
        }), encoding="utf-8")
        terminals = [
            {"role": "supply", "type": "round", "wall": "ceiling",
             "cx": 1.0, "cy": 1.0, "w": 0.4, "h": 0.4, "cmh": 500},
            {"role": "exhaust", "type": "grille", "wall": "ceiling",
             "cx": 3.0, "cy": 2.0, "w": 0.4, "h": 0.4, "cmh": 500},
        ]
        obstacles = [{
            "kind": "equipment", "x0": 1.8, "y0": 0.8,
            "x1": 2.6, "y1": 1.4, "h": 1.0, "kw": 1.0,
            "convective_fraction": 0.8,
            "evidence": "equipment_schedule:M03-001",
        }]

        terminal_claim = [dict(terminals[0], source_id="obstacle_DXF_01",
                               source_type="user_confirmed",
                               source_ref={"handle": "FORGED"}), terminals[1]]
        obstacle_claim = [dict(obstacles[0], source_id="terminal_DXF_01",
                               source_type="user_confirmed",
                               source_ref={"handle": "FORGED"})]

        terminal_result = cfd_studio.confirm_body_gci_geometry(
            str(geometry), 0, 2.8, terminal_claim, obstacles,
        )
        obstacle_result = cfd_studio.confirm_body_gci_geometry(
            str(geometry), 0, 2.8, terminals, obstacle_claim,
        )

        self.assertFalse(terminal_result["ok"])
        self.assertIn("급·배기구", terminal_result["error"])
        self.assertFalse(obstacle_result["ok"])
        self.assertIn("장애물", obstacle_result["error"])

    def test_body_gci_confirmation_can_promote_a_reviewed_bbox_candidate(self):
        imports = Path(self.tmp.name) / "_imports"
        imports.mkdir()
        geometry = imports / "lobby.geometry.json"
        geometry.write_text(json.dumps({
            "source": "lobby.dxf", "units": "mm",
            "unit_review": {"required": True, "resolved": False},
            "params": {"wall": {"height": 2800.0}},
            "elements": {"zone": [], "equipment": []},
            "zone_candidates": [{
                "source_layer": "A-ELE04", "confirmed": False,
                "bbox_mm": [0.0, 0.0, 4000.0, 3000.0],
            }],
        }), encoding="utf-8")
        terminals = [
            {"role": "supply", "type": "round", "wall": "ceiling",
             "cx": 1.0, "cy": 1.0, "w": 0.4, "h": 0.4, "cmh": 500},
            {"role": "exhaust", "type": "grille", "wall": "ceiling",
             "cx": 3.0, "cy": 2.0, "w": 0.4, "h": 0.4, "cmh": 500},
        ]
        blocked = cfd_studio.confirm_body_gci_geometry(
            str(geometry), None, 2.8, terminals,
            [{"kind": "equipment", "x0": 1.5, "y0": 1.0,
              "x1": 2.0, "y1": 1.5, "h": 1.0, "kw": 1.0,
              "convective_fraction": 0.8,
              "evidence": "equipment_schedule:M03-001",
              "source_id": "manual-heat-bbox",
              "source_type": "user_confirmed",
              "source_ref": {"layer": "USER_CONFIRMED",
                             "block_name": "MANUAL_HEAT_BBOX",
                             "entity_type": "UI_INPUT",
                             "source_id": "manual-heat-bbox"}}],
            bbox=[0.0, 0.0, 4000.0, 3000.0],
        )
        self.assertFalse(blocked["ok"])
        self.assertIn("단위", blocked["error"])
        result = cfd_studio.confirm_body_gci_geometry(
            str(geometry), None, 2.8, terminals,
            [{"kind": "equipment", "x0": 1.5, "y0": 1.0,
              "x1": 2.0, "y1": 1.5, "h": 1.0, "kw": 1.0,
              "convective_fraction": 0.8,
              "evidence": "equipment_schedule:M03-001",
              "source_id": "manual-heat-bbox",
              "source_type": "user_confirmed",
              "source_ref": {"layer": "USER_CONFIRMED",
                             "block_name": "MANUAL_HEAT_BBOX",
                             "entity_type": "UI_INPUT",
                             "source_id": "manual-heat-bbox"}}],
            bbox=[0.0, 0.0, 4000.0, 3000.0], unit_confirmed=True,
        )

        self.assertTrue(result["ok"], result)
        saved = json.loads(Path(result["geometry"]).read_text(encoding="utf-8"))
        self.assertEqual(result["zone_index"], 0)
        self.assertEqual(len(saved["elements"]["zone"]), 1)
        self.assertTrue(saved["elements"]["zone"][0]["confirmed"])
        self.assertEqual(saved["elements"]["zone"][0]["source_ref"]["layer"],
                         "USER_CONFIRMED_BBOX")
        self.assertEqual(saved["elements"]["zone"][0]["semantic"]["ceiling_height_mm"],
                         2800.0)
        self.assertFalse(saved["unit_review"]["required"])
        self.assertTrue(saved["unit_review"]["resolved"])

    def test_body_gci_semantic_confirmation_rejects_flow_imbalance(self):
        imports = Path(self.tmp.name) / "_imports"
        imports.mkdir()
        geometry = imports / "plant-room.geometry.json"
        geometry.write_text(json.dumps({
            "source": "plant-room.dxf", "units": "mm",
            "params": {"wall": {"height": 2800.0}},
            "elements": {"zone": [{
                "kind": "polyline", "closed": True,
                "points": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
            }]},
        }), encoding="utf-8")
        terminals = [
            {"role": "supply", "wall": "ceiling", "cx": 1, "cy": 1,
             "w": .4, "h": .4, "cmh": 500},
            {"role": "exhaust", "wall": "ceiling", "cx": 3, "cy": 1,
             "w": .4, "h": .4, "cmh": 400},
        ]
        result = cfd_studio.confirm_body_gci_geometry(
            str(geometry), 0, 2.8, terminals,
            [{"kind": "equipment", "x0": 1, "y0": 1, "x1": 2, "y1": 2,
              "h": 1, "kw": 1}],
        )
        self.assertFalse(result["ok"])
        self.assertIn("1%", result["error"])
        self.assertFalse((imports / "plant-room.confirmed.geometry.json").exists())

    def test_body_gci_worker_publishes_gate_status_and_report_url(self):
        active = {"step": "", "lines": []}
        with mock.patch.object(
            cfd_studio.cfd_gci_job, "run_study",
            return_value={
                "ok": True,
                "manifest": {"gate_status": "PASS"},
            },
        ) as run:
            ok, error, details = cfd_studio._do_body_gci("gci-123456789abc", active)
        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(details["gate_status"], "PASS")
        self.assertEqual(details["report_url"],
                         "/body-gci-report/gci-123456789abc")
        run.assert_called_once()

    def test_body_gci_external_process_lock_is_shown_and_not_requeued(self):
        study = "gci-123456789abc"
        owner = {"pid": 4321, "started_at": "2026-07-22T00:00:00+00:00"}
        with (
            mock.patch.object(
                cfd_studio.cfd_gci_job,
                "list_studies",
                return_value=[{
                    "study": study,
                    "status": "FAIL",
                    "error": "previous interrupted monitor",
                }],
            ),
            mock.patch.object(
                cfd_studio.cfd_gci_job,
                "active_run_lock",
                return_value=owner,
            ),
        ):
            payload = cfd_studio.body_gci_jobs_payload()
            duplicate = cfd_studio._queue_body_gci_study(study)

        self.assertEqual(payload["jobs"][0]["runtime_state"], "running")
        self.assertEqual(payload["jobs"][0]["status"], "running")
        self.assertEqual(payload["jobs"][0]["persisted_status"], "FAIL")
        self.assertEqual(payload["jobs"][0]["error"], "")
        self.assertEqual(
            payload["jobs"][0]["persisted_error"],
            "previous interrupted monitor",
        )
        self.assertEqual(payload["jobs"][0]["run_lock"]["pid"], 4321)
        self.assertIn("PID 4321", duplicate)

    def test_running_gci_job_reports_bounded_between_checkpoint_estimate(self):
        study = "gci-123456789abc"
        case = Path(self.tmp.name) / "_body_solver" / "fine-thermal"
        case.mkdir(parents=True)
        (case / "run_manifest.json").write_text(json.dumps({
            "thermal_progress": {
                "latest_time_s": 100.0,
                "flow_through_time_s": 100.0,
                "required_duration_s": 300.0,
                "recommended_next_duration_s": 20.0,
                "last_solver_runtime_per_simulated_second": 10.0,
                "last_fixed_runtime_overhead_seconds": 0.0,
                "estimated_remaining_runtime_seconds": 2000.0,
            },
        }), encoding="utf-8")
        updated = (datetime.now(timezone.utc) - timedelta(seconds=110)).isoformat()
        manifest = {
            "study": study,
            "status": "running",
            "stage": "fine:thermal_continue",
            "updated_at": updated,
            "levels": [{
                "name": "fine", "status": "running", "stage": "thermal_continue",
                "latest_time_s": 100.0, "thermal_case": str(case),
            }],
        }
        with (
            mock.patch.object(
                cfd_studio.cfd_gci_job, "list_studies", return_value=[manifest]
            ),
            mock.patch.object(
                cfd_studio.cfd_gci_job, "active_run_lock",
                return_value={"pid": 4321, "started_at": updated},
            ),
        ):
            payload = cfd_studio.body_gci_jobs_payload()

        job = payload["jobs"][0]
        live = job["live_progress"]
        self.assertGreater(live["estimated_time_s"], 109.0)
        self.assertLess(live["estimated_time_s"], 113.0)
        self.assertEqual(live["next_checkpoint_time_s"], 120.0)
        self.assertLessEqual(live["estimated_time_s"], live["next_checkpoint_time_s"])
        self.assertAlmostEqual(
            job["levels"][0]["estimated_flow_through_fraction"],
            live["estimated_time_s"] / 100.0,
        )
        self.assertIn("estimate_basis", cfd_studio.PAGE_BODY_GCI)
        self.assertIn("보수 추정", cfd_studio.PAGE_BODY_GCI)

    def test_running_gci_estimate_ignores_malformed_optional_cost_fields(self):
        study = "gci-123456789abc"
        case = Path(self.tmp.name) / "_body_solver" / "fine-thermal"
        case.mkdir(parents=True)
        (case / "run_manifest.json").write_text(json.dumps({
            "thermal_progress": {
                "latest_time_s": 100.0,
                "flow_through_time_s": 100.0,
                "required_duration_s": 300.0,
                "recommended_next_duration_s": 20.0,
                "last_solver_runtime_per_simulated_second": 10.0,
                "last_fixed_runtime_overhead_seconds": "invalid",
                "estimated_remaining_runtime_seconds": "invalid",
            },
        }), encoding="utf-8")
        updated = (datetime.now(timezone.utc) - timedelta(seconds=50)).isoformat()
        manifest = {
            "study": study,
            "status": "running",
            "stage": "fine:thermal_continue",
            "updated_at": updated,
            "levels": [{
                "name": "fine", "status": "running", "stage": "thermal_continue",
                "latest_time_s": 100.0, "thermal_case": str(case),
            }],
        }
        with (
            mock.patch.object(
                cfd_studio.cfd_gci_job, "list_studies", return_value=[manifest]
            ),
            mock.patch.object(
                cfd_studio.cfd_gci_job, "active_run_lock",
                return_value={"pid": 4321, "started_at": updated},
            ),
        ):
            payload = cfd_studio.body_gci_jobs_payload()

        live = payload["jobs"][0]["live_progress"]
        self.assertGreater(live["estimated_time_s"], 100.0)
        self.assertIsNone(live["estimated_remaining_runtime_seconds"])

    def test_transient_run_requires_project_local_iteration_limit_result(self):
        solver = Path(self.tmp.name) / "_body_solver" / "sample-isothermal"
        solver.mkdir(parents=True)
        (solver / "run_manifest.json").write_text(json.dumps({
            "status": "WARN", "warnings": ["ITERATION_LIMIT"],
            "engine": "body_fitted_isothermal_rans",
        }), encoding="utf-8")
        with mock.patch.object(
            cfd_studio.cfd_physics, "run_transient_diagnostic",
            return_value={"ok": True, "manifest": {"status": "WARN"}},
        ) as run:
            result = cfd_studio.run_body_fitted_transient(str(solver))
        self.assertTrue(result["ok"])
        run.assert_called_once_with(solver, settings=None)
        rejected = cfd_studio.run_body_fitted_transient(str(Path(self.tmp.name).parent))
        self.assertFalse(rejected["ok"])

    def test_deleting_a_queued_case_removes_its_queue_record(self):
        result = cfd_studio.create_case({
            "mode": "manual", "name": "queued", "L": 2, "W": 2, "H": 2,
            "power_kw": 1, "supply": "x0", "exhaust": "xL", "supply_u": 0.3,
            "supply_T_C": 20, "cell": 1, "endtime": 100,
        })
        self.assertTrue(result.get("ok"), result)
        cfd_studio.RUN["queue"] = [{"name": "queued", "kind": "run"},
                                    {"name": "keep", "kind": "run"}]
        self.assertEqual(cfd_studio.delete_case("queued"), {"ok": True})
        self.assertEqual(cfd_studio.RUN["queue"], [{"name": "keep", "kind": "run"}])

    def test_create_case_preserves_confirmed_equipment_heat_provenance(self):
        result = cfd_studio.create_case({
            "mode": "manual", "name": "confirmed-equipment", "L": 4, "W": 3,
            "H": 2.5, "power_kw": "", "supply": "x0", "exhaust": "xL",
            "supply_u": 0.3, "supply_T_C": 20, "cell": 0.5, "endtime": 100,
            "openings": [
                {"role": "supply", "type": "grille", "wall": "ceiling",
                 "cx": 1.0, "cy": 1.0, "w": 0.5, "h": 0.5, "cmh": 300},
                {"role": "exhaust", "type": "grille", "wall": "ceiling",
                 "cx": 3.0, "cy": 2.0, "w": 0.5, "h": 0.5, "cmh": 300},
            ],
            "obstacles": [{
                "kind": "equipment", "x0": 1.5, "y0": 1.0,
                "x1": 2.0, "y1": 1.5, "h": 1.0, "kw": 5.0,
                "convective_fraction": 0.8,
                "source_id": "DXF-EHP-01", "source_label": "EHP 실내기 1",
                "evidence": "equipment_schedule:M03-001",
                "source_type": "user_confirmed",
            }],
        })

        self.assertTrue(result.get("ok"), result)
        meta = json.loads((Path(self.tmp.name) / "confirmed-equipment" /
                           "cfd_case_meta.json").read_text(encoding="utf-8"))
        obstacle = meta["config"]["obstacles"][0]
        self.assertEqual(obstacle["source_id"], "DXF-EHP-01")
        self.assertEqual(obstacle["convective_fraction"], 0.8)
        self.assertEqual(obstacle["evidence"], "equipment_schedule:M03-001")
        self.assertEqual(meta["heat"]["applied_convective_power_w"], 4000.0)

    def test_quick_v3_case_preserves_opening_and_obstacle_source_references(self):
        """Quick-V3 metadata must retain the DXF reference shown to the user."""
        supply_ref = {"handle": "SA-17", "layer": "M-SUPPLY",
                      "block_name": "ROUND-SA-17", "entity_type": "INSERT"}
        equipment_ref = {"handle": "EHP-01", "layer": "DVM_INDOOR",
                         "block_name": "DVM-01", "entity_type": "INSERT"}
        result = cfd_studio.create_case({
            "mode": "manual", "name": "quick-v3-source-refs", "L": 4, "W": 3,
            "H": 2.5, "power_kw": "", "supply_u": 0.3, "supply_T_C": 20,
            "cell": 0.5, "endtime": 100,
            "openings": [
                {"role": "supply", "type": "grille", "wall": "ceiling",
                 "cx": 1.0, "cy": 1.0, "w": 0.5, "h": 0.5, "cmh": 300,
                 "source_id": "equipment_DXF_SA_17", "source_type": "dxf_detected",
                 "source_ref": supply_ref},
                {"role": "exhaust", "type": "grille", "wall": "ceiling",
                 "cx": 3.0, "cy": 2.0, "w": 0.5, "h": 0.5, "cmh": 300},
            ],
            "obstacles": [{
                "kind": "equipment", "x0": 1.5, "y0": 1.0,
                "x1": 2.0, "y1": 1.5, "h": 1.0, "kw": 5.0,
                "convective_fraction": 0.8,
                "source_id": "equipment_DXF_EHP_01", "source_type": "user_confirmed",
                "source_ref": equipment_ref,
                "override_of_dxf": True,
                "evidence": "equipment_schedule:M03-001",
            }],
        })

        self.assertTrue(result.get("ok"), result)
        meta = json.loads((Path(self.tmp.name) / "quick-v3-source-refs" /
                           "cfd_case_meta.json").read_text(encoding="utf-8"))
        supply = next(row for row in meta["config"]["openings"]
                      if row["role"] == "supply")
        self.assertEqual(supply["source_ref"], supply_ref)
        self.assertEqual(meta["config"]["obstacles"][0]["source_ref"], equipment_ref)
        supply_patch = next(row for row in meta["patches"]
                            if row["role"] == "supply")
        self.assertEqual(supply_patch["source_ref"], supply_ref)
        self.assertEqual(supply_patch["source_type"], "dxf_detected")
        obstacle = meta["config"]["obstacles"][0]
        self.assertTrue(obstacle["override_of_dxf"])
        self.assertEqual(meta["equip_zones"][0]["source_ref"], equipment_ref)
        self.assertTrue(meta["equip_zones"][0]["override_of_dxf"])

    def test_quick_v3_rejects_unconfirmed_dxf_heat_source(self):
        """A detected DXF block cannot become a heat load without review."""
        result = cfd_studio.create_case({
            "mode": "manual", "name": "raw-dxf-heat", "L": 4, "W": 3,
            "H": 2.5, "power_kw": "", "supply_u": 0.3, "supply_T_C": 20,
            "cell": 0.5, "endtime": 100,
            "openings": [
                {"role": "supply", "type": "grille", "wall": "ceiling",
                 "cx": 1.0, "cy": 1.0, "w": 0.5, "h": 0.5, "cmh": 300},
                {"role": "exhaust", "type": "grille", "wall": "ceiling",
                 "cx": 3.0, "cy": 2.0, "w": 0.5, "h": 0.5, "cmh": 300},
            ],
            "obstacles": [{
                "kind": "equipment", "x0": 1.5, "y0": 1.0,
                "x1": 2.0, "y1": 1.5, "h": 1.0, "kw": 5.0,
                "convective_fraction": 0.8,
                "source_id": "equipment_DXF_EHP_01", "source_type": "dxf_detected",
                "evidence": "equipment_schedule:M03-001",
            }],
        })

        self.assertIn("DXF", result["error"])
        self.assertFalse((Path(self.tmp.name) / "raw-dxf-heat").exists())

    def test_quick_v3_rejects_missing_type_cad_heat_source(self):
        """A missing review marker cannot turn a CAD handle into legacy heat."""
        result = cfd_studio.create_case({
            "mode": "manual", "name": "typeless-cad-heat", "L": 4, "W": 3,
            "H": 2.5, "power_kw": "", "supply_u": 0.3, "supply_T_C": 20,
            "cell": 0.5, "endtime": 100,
            "openings": [
                {"role": "supply", "type": "grille", "wall": "ceiling",
                 "cx": 1.0, "cy": 1.0, "w": 0.5, "h": 0.5, "cmh": 300},
                {"role": "exhaust", "type": "grille", "wall": "ceiling",
                 "cx": 3.0, "cy": 2.0, "w": 0.5, "h": 0.5, "cmh": 300},
            ],
            "obstacles": [{
                "kind": "equipment", "x0": 1.5, "y0": 1.0,
                "x1": 2.0, "y1": 1.5, "h": 1.0, "kw": 5.0,
                "convective_fraction": 0.8,
                "source_id": "equipment_DXF_EHP_01",
                "source_ref": {"source_handle": "EHP-A1", "layer": "DVM_INDOOR"},
                "evidence": "equipment_schedule:M03-001",
            }],
        })

        self.assertIn("DXF", result["error"])
        self.assertFalse((Path(self.tmp.name) / "typeless-cad-heat").exists())

    def test_dxf_obstacle_review_ui_shows_original_source_identity(self):
        """A novice must see which DXF item is being confirmed as equipment."""
        self.assertIn("DXF 출처", cfd_studio.PAGE_NEW)
        self.assertIn("r.source_label", cfd_studio.PAGE_NEW)
        self.assertIn("r.source_id", cfd_studio.PAGE_NEW)
        self.assertIn("dxf_detected", cfd_studio.PAGE_NEW)

    def test_upload_filename_cannot_escape_import_directory(self):
        stem, ext = cfd_studio._safe_upload_stem(r"..\..\전기실?.DXF")
        self.assertNotIn("..", stem)
        self.assertEqual(ext, ".dxf")

    def test_local_server_rejects_cross_site_mutations(self):
        self.assertTrue(cfd_studio._local_post_allowed("127.0.0.1:8090"))
        self.assertTrue(cfd_studio._local_post_allowed(
            "localhost:8090", "http://localhost:8090", "same-origin"))
        self.assertFalse(cfd_studio._local_post_allowed(
            "127.0.0.1:8090", "https://malicious.example", "cross-site"))
        self.assertFalse(cfd_studio._local_post_allowed("project.example:8090"))


if __name__ == "__main__":
    unittest.main()
