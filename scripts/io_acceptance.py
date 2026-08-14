"""Probe read/create/replace/delete permissions without touching user files."""

from __future__ import annotations

import argparse
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
    status = "PASS" if all(item["status"] == "PASS" for item in probes) else "BLOCKED"
    return {
        "schema_version": 1,
        "contract": "io_acceptance.v1",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "projects_root": str(projects_root),
        "roots": list(REQUIRED_ROOTS),
        "status": status,
        "probes": probes,
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
