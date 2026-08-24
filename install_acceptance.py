"""Run an isolated install, repair, launcher, runtime, and resume acceptance."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import venv


CONTRACT = "install_recovery_acceptance.v1"
GENERATOR = "mep-cfd-studio/install-acceptance-v1"


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


def _atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                                     dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _run(command, log_path, cwd):
    result = subprocess.run(
        [str(item) for item in command], cwd=str(cwd), capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    Path(log_path).write_text(
        "$ " + " ".join(str(item) for item in command) + "\n\n"
        + (result.stdout or "") + ("\n[stderr]\n" + result.stderr
                                    if result.stderr else "")
        + f"\n[returncode] {result.returncode}\n",
        encoding="utf-8", newline="\n",
    )
    return result


def validate_resume_evidence(job):
    """Require a recorded interrupted attempt and a later physical checkpoint."""
    history = list(job.get("resume_history") or [])
    if not history or int(job.get("attempts") or 0) < 2:
        return False, "재개 이력이 없습니다.", {}
    event = history[-1]
    checkpoints = event.get("checkpoint_times_s") or {}
    current = {
        str(row.get("name")): row.get("latest_time_s")
        for row in job.get("levels") or [] if row.get("latest_time_s") is not None
    }
    preserved = all(
        name in current and float(current[name]) + 1e-12 >= float(value)
        for name, value in checkpoints.items()
    )
    advanced = any(
        name in current and float(current[name]) > float(value) + 1e-9
        for name, value in checkpoints.items()
    )
    passed = bool(
        event.get("previous_status") in ("running", "FAIL")
        and preserved and advanced
    )
    detail = ("중단 전 체크포인트 보존 후 물리시간 전진" if passed else
              "재개 후 체크포인트 보존·전진 증거가 부족합니다.")
    return passed, detail, {
        "attempts": job.get("attempts"), "resume_event": event,
        "current_checkpoint_times_s": current,
    }


def validate_recorded_resume_evidence(evidence):
    """Recheck that a recorded resume event preserved and advanced a checkpoint."""
    evidence = evidence or {}
    event = evidence.get("resume_event") or {}
    checkpoints = event.get("checkpoint_times_s") or {}
    current = evidence.get("current_checkpoint_times_s") or {}
    try:
        attempts = int(evidence.get("attempts") or 0)
        preserved = bool(checkpoints) and all(
            name in current and float(current[name]) + 1e-12 >= float(value)
            for name, value in checkpoints.items()
        )
        advanced = any(
            name in current and float(current[name]) > float(value) + 1e-9
            for name, value in checkpoints.items()
        )
    except (TypeError, ValueError):
        return False
    return bool(
        attempts >= 2
        and event.get("previous_status") in ("running", "FAIL")
        and preserved
        and advanced
    )


def _inside(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (OSError, ValueError):
        return False


def validate_evidence(path, projects_root):
    """Independently validate a completed acceptance manifest and its artifacts."""
    path = Path(path).expanduser().resolve()
    projects = Path(projects_root).expanduser().resolve()
    workspace = projects.parent
    manifest = {}
    try:
        manifest = _read(path)
        scenarios = manifest.get("scenarios") or {}
        checks = manifest.get("checks") or {}
        if manifest.get("contract") != CONTRACT:
            raise ValueError("unexpected contract")
        if manifest.get("status") != "PASS":
            raise ValueError("acceptance status is not PASS")
        if manifest.get("project_data_preserved") is not True:
            raise ValueError("project preservation is not confirmed")
        if not all(scenarios.get(name) == "PASS" for name in (
                "clean_install", "dependency_repair",
                "interrupted_job_resume")):
            raise ValueError("one or more scenarios did not pass")
        for name in ("launcher", "openfoam_v2606",
                     "current_environment_acceptance", "stable_file_hashes"):
            if checks.get(name) != "PASS":
                raise ValueError(f"check did not pass: {name}")
        if not validate_recorded_resume_evidence(checks.get("resume_evidence")):
            raise ValueError("recorded resume evidence is not physically progressive")

        before = checks.get("stable_hashes_before") or {}
        after = checks.get("stable_hashes_after") or {}
        if not before or before != after:
            raise ValueError("stable evidence hashes are missing or changed")
        for raw_path, expected in before.items():
            stable_path = Path(raw_path).expanduser().resolve()
            if (not _inside(stable_path, workspace) or not stable_path.is_file()
                    or _sha256(stable_path).lower() != str(expected).lower()):
                raise ValueError(f"stable evidence file mismatch: {stable_path}")

        log_root = (projects / "_system" / "install_recovery_acceptance" /
                    "logs").resolve()
        logs = manifest.get("logs") or {}
        required_logs = {
            "clean_install": (" -m pip install -r ", "successfully installed"),
            "clean_verify": ("import ezdxf, shapely, numpy, matplotlib", "ready"),
            "dependency_repair": (" -m pip install -r ",
                                  "requirement already satisfied"),
            "repair_verify": ("import ezdxf, shapely, numpy, matplotlib",
                              "ready"),
            "launcher": ("run_cfd.bat --check", "launcher: ready"),
            "openfoam": ("install_openfoam2606.bat --check --no-pause",
                         "openfoam-v2606-ready"),
        }
        forbidden_log_markers = {
            "launcher": ("[error]", "traceback"),
            "openfoam": (
                "[error]", "wsl_e_distro_not_found", "command not found",
                "setup did not complete",
            ),
        }
        log_hashes = manifest.get("log_hashes") or {}
        verified_logs = {}
        for name, markers in required_logs.items():
            raw_log_path = logs.get(name)
            if name == "repair_verify" and not raw_log_path:
                # v1 evidence created before this path was added to the manifest
                # still has the fixed, isolated repair verification log on disk.
                raw_log_path = log_root / "repair_verify.log"
            log_path = Path(str(raw_log_path or "")).expanduser().resolve()
            if not _inside(log_path, log_root) or not log_path.is_file():
                raise ValueError(f"missing or unsafe log: {name}")
            content = log_path.read_text(encoding="utf-8", errors="replace").lower()
            if not all(marker in content for marker in markers):
                raise ValueError(f"log does not prove success: {name}")
            if any(marker in content
                   for marker in forbidden_log_markers.get(name, ())):
                raise ValueError(f"log contains a failure marker: {name}")
            if (manifest.get("generator") == GENERATOR
                    and "[returncode] 0" not in content):
                raise ValueError(f"log has no successful return code: {name}")
            actual_hash = _sha256(log_path)
            if (name in log_hashes and actual_hash.lower()
                    != str(log_hashes[name]).lower()):
                raise ValueError(f"log hash mismatch: {name}")
            verified_logs[name] = str(log_path)
        return {"ok": True, "manifest": manifest, "error": "",
                "verified_logs": verified_logs}
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {"ok": False, "manifest": manifest, "error": str(exc),
                "verified_logs": {}}


def _stable_evidence_files(workspace, projects, job):
    paths = [workspace / "cfd_benchmarks" / "g2_thermal" / "geometry.json",
             projects / "capability_manifest.json"]
    completed = next((row for row in job.get("levels") or []
                      if row.get("status") == "PASS" and row.get("thermal_case")), None)
    if completed:
        paths.append(Path(completed["thermal_case"]) / "result_manifest.json")
    return [path.resolve() for path in paths if path.is_file()]


def run_acceptance(workspace, projects_root, study):
    workspace = Path(workspace).expanduser().resolve()
    projects = Path(projects_root).expanduser().resolve()
    job_path = projects / "_body_gci" / study / "gci_job.json"
    system_root = projects / "_system" / "install_recovery_acceptance"
    logs = system_root / "logs"
    fresh_venv = system_root / "fresh_venv"
    logs.mkdir(parents=True, exist_ok=True)
    evidence_path = (projects / "_release_evidence" / "install_recovery" /
                     "current_machine.json")
    manifest = {
        "schema_version": 1, "contract": CONTRACT, "created_at": _now(),
        "generator": GENERATOR,
        "status": "FAIL", "project_data_preserved": False,
        "scenarios": {"clean_install": "FAIL", "dependency_repair": "FAIL",
                      "interrupted_job_resume": "FAIL"},
        "checks": {}, "logs": {}, "error": "",
    }
    try:
        job = _read(job_path)
        stable_files = _stable_evidence_files(workspace, projects, job)
        before = {str(path): _sha256(path) for path in stable_files}

        if fresh_venv.exists():
            resolved = fresh_venv.resolve()
            resolved.relative_to(system_root.resolve())
            shutil.rmtree(resolved)
        venv.EnvBuilder(with_pip=True, clear=False).create(fresh_venv)
        python = fresh_venv / "Scripts" / "python.exe"
        if not python.is_file():
            raise RuntimeError("격리 가상환경 Python을 만들지 못했습니다.")

        install_log = logs / "clean_install.log"
        installed = _run(
            [python, "-m", "pip", "install", "-r", workspace / "requirements.txt"],
            install_log, workspace,
        )
        verify_log = logs / "clean_verify.log"
        verified = _run(
            [python, "-c", "import ezdxf, shapely, numpy, matplotlib; print('ready')"],
            verify_log, workspace,
        )
        clean_pass = installed.returncode == 0 and verified.returncode == 0

        repair_log = logs / "dependency_repair.log"
        repaired = _run(
            [python, "-m", "pip", "install", "-r", workspace / "requirements.txt"],
            repair_log, workspace,
        )
        repaired_verify = _run(
            [python, "-c", "import ezdxf, shapely, numpy, matplotlib; print('ready')"],
            logs / "repair_verify.log", workspace,
        )
        repair_pass = repaired.returncode == 0 and repaired_verify.returncode == 0

        launcher = _run(
            ["cmd.exe", "/d", "/c", workspace / "run_cfd.bat", "--check"],
            logs / "launcher_check.log", workspace,
        )
        openfoam = _run(
            ["cmd.exe", "/d", "/c", workspace / "install_openfoam2606.bat",
             "--check", "--no-pause"],
            logs / "openfoam_check.log", workspace,
        )
        capability = _read(projects / "capability_manifest.json")
        environment_pass = bool(
            capability.get("body_fitted_engine_ready")
            and (capability.get("acceptance") or {}).get("ok")
            and (capability.get("acceptance") or {}).get("openfoam_profile")
            == (capability.get("openfoam") or {}).get("compatible_profile")
        )
        resume_pass, resume_detail, resume_evidence = validate_resume_evidence(job)

        after = {str(path): _sha256(path) for path in stable_files}
        preserved = bool(before) and before == after
        manifest.update(
            status=("PASS" if clean_pass and repair_pass and resume_pass
                    and launcher.returncode == 0 and openfoam.returncode == 0
                    and environment_pass and preserved else "FAIL"),
            project_data_preserved=preserved,
            scenarios={
                "clean_install": "PASS" if clean_pass else "FAIL",
                "dependency_repair": "PASS" if repair_pass else "FAIL",
                "interrupted_job_resume": "PASS" if resume_pass else "FAIL",
            },
            checks={
                "launcher": "PASS" if launcher.returncode == 0 else "FAIL",
                "openfoam_v2606": "PASS" if openfoam.returncode == 0 else "FAIL",
                "current_environment_acceptance": (
                    "PASS" if environment_pass else "FAIL"
                ),
                "stable_file_hashes": "PASS" if preserved else "FAIL",
                "resume_detail": resume_detail,
                "resume_evidence": resume_evidence,
                "stable_hashes_before": before,
                "stable_hashes_after": after,
            },
            logs={
                "clean_install": str(install_log.resolve()),
                "clean_verify": str(verify_log.resolve()),
                "dependency_repair": str(repair_log.resolve()),
                "repair_verify": str((logs / "repair_verify.log").resolve()),
                "launcher": str((logs / "launcher_check.log").resolve()),
                "openfoam": str((logs / "openfoam_check.log").resolve()),
            },
            log_hashes={
                "clean_install": _sha256(install_log),
                "clean_verify": _sha256(verify_log),
                "dependency_repair": _sha256(repair_log),
                "repair_verify": _sha256(logs / "repair_verify.log"),
                "launcher": _sha256(logs / "launcher_check.log"),
                "openfoam": _sha256(logs / "openfoam_check.log"),
            },
            error="",
        )
    except Exception as exc:
        manifest["error"] = f"{type(exc).__name__}: {exc}"
    _atomic_json(evidence_path, manifest)
    return {"ok": manifest["status"] == "PASS", "manifest": manifest,
            "manifest_path": str(evidence_path.resolve())}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=Path(__file__).resolve().parent)
    parser.add_argument("--projects-root", default="cfd_projects")
    parser.add_argument("--study", required=True)
    args = parser.parse_args(argv)
    result = run_acceptance(args.workspace, args.projects_root, args.study)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
