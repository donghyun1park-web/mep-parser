"""Probe read/create/replace/delete permissions without touching user files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUIRED_ROOTS = (
    "_system",
    "_body_mesh",
    "_body_solver",
    "_body_gci",
    "_field_jobs",
    "_release_evidence",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_probe(case: Path, kind: str, path: Path | None) -> dict[str, Any]:
    result: dict[str, Any] = {"case": str(case), "kind": kind, "path": str(path) if path else None,
                              "read": False, "sha256": None, "status": "BLOCKED", "error_code": None}
    if path is None or not path.is_file():
        result["error_code"] = f"ARTIFACT_MISSING:{kind}"
        return result
    try:
        digest = _sha256_file(path)
        result.update({"read": True, "sha256": digest, "status": "PASS"})
    except OSError as exc:
        result["error_code"] = f"ARTIFACT_READ_ERROR:{exc.__class__.__name__}"
    return result


def _authoritative_cases(projects_root: Path) -> list[Path]:
    solver_root = projects_root / "_body_solver"
    if not solver_root.is_dir():
        return []
    markers = {"mesh_manifest.json", "run_manifest.json", "result_manifest.json"}
    return sorted((path for path in solver_root.rglob("*") if path.is_dir() and any((path / marker).is_file() for marker in markers)), key=str)


def _recovered_artifact_probes(projects_root: Path) -> list[dict[str, Any]]:
    cases = _authoritative_cases(projects_root)
    if not cases:
        return [{"case": None, "kind": "authoritative_case", "path": None, "read": False,
                 "sha256": None, "status": "BLOCKED", "error_code": "AUTHORITATIVE_CASE_MISSING"}]
    probes: list[dict[str, Any]] = []
    for case in cases:
        probes.append(_artifact_probe(case, "log_checkMesh", case / "log.checkMesh"))
        solver_logs = sorted(path for path in case.glob("log.*") if path.name != "log.checkMesh")
        probes.append(_artifact_probe(case, "solver_log", solver_logs[0] if solver_logs else None))
        times = []
        for candidate in case.iterdir():
            if candidate.is_dir():
                try:
                    times.append((float(candidate.name), candidate))
                except ValueError:
                    pass
        latest = max(times, default=(None, None))[1]
        for field in ("T", "U", "phi", "V"):
            probes.append(_artifact_probe(case, f"latest_{field}", latest / field if latest else None))
        for manifest in ("mesh_manifest.json", "run_manifest.json", "result_manifest.json"):
            probes.append(_artifact_probe(case, manifest, case / manifest))
        vtus = sorted(case.rglob("*.vtu"))
        html = sorted(case.rglob("*.html"))
        probes.append(_artifact_probe(case, "vtu", vtus[0] if vtus else None))
        probes.append(_artifact_probe(case, "html", html[0] if html else None))
    return probes


def probe_path(path: Path) -> dict[str, Any]:
    path = Path(path).resolve()
    result: dict[str, Any] = {
        "path": str(path), "read": False, "create": False,
        "replace": False, "delete": False, "status": "BLOCKED", "error_code": None,
    }
    probe = path / f".io-acceptance-{uuid.uuid4().hex}.tmp"
    replacement = path / f".io-acceptance-{uuid.uuid4().hex}.replace.tmp"
    try:
        if path.exists() and not path.is_dir():
            result["error_code"] = "IO_TARGET_NOT_DIRECTORY"
            return result
        path.mkdir(parents=True, exist_ok=True)
        result["read"] = path.is_dir() and os.access(path, os.R_OK)
        if not result["read"]:
            result["error_code"] = "IO_READ_DENIED"
            return result
        probe.write_text("probe-v1\n", encoding="utf-8")
        result["create"] = probe.is_file()
        replacement.write_text("probe-v2\n", encoding="utf-8")
        os.replace(replacement, probe)
        result["replace"] = probe.read_text(encoding="utf-8") == "probe-v2\n"
        probe.unlink()
        result["delete"] = not probe.exists()
        if not (result["create"] and result["replace"] and result["delete"]):
            result["error_code"] = "IO_OPERATION_FAILED"
            return result
        result["status"] = "PASS"
        return result
    except PermissionError:
        result["error_code"] = "IO_PERMISSION_DENIED"
        return result
    except OSError as exc:
        result["error_code"] = f"IO_OS_ERROR:{exc.__class__.__name__}"
        return result
    finally:
        for candidate in (probe, replacement):
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                pass


def run_io_acceptance(projects_root: Path) -> dict[str, Any]:
    projects_root = Path(projects_root).resolve()
    probes = [probe_path(projects_root / root) for root in REQUIRED_ROOTS]
    artifact_probes = _recovered_artifact_probes(projects_root)
    status = "PASS" if all(item["status"] == "PASS" for item in probes + artifact_probes) else "BLOCKED"
    return {
        "schema_version": 1,
        "contract": "io_acceptance.v1",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "projects_root": str(projects_root),
        "roots": list(REQUIRED_ROOTS),
        "status": status,
        "probes": probes,
        "artifact_probes": artifact_probes,
    }


def write_acceptance(payload: dict[str, Any], output: Path) -> Path:
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projects-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    payload = run_io_acceptance(args.projects_root)
    write_acceptance(payload, args.output)
    print(json.dumps({"status": payload["status"], "output": str(args.output)}))
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
