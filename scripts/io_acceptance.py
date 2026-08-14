"""Probe root I/O and rehash every inventory-authoritative recovered artifact."""

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
INVENTORY_RELATIVE = Path("_working_validation") / "evidence" / "authoritative_case_inventory.v1.json"
_HEX_LENGTH = 64
_CASE_KEYS = frozenset({
    "case_id", "case_path", "mesh_manifest", "run_manifest", "result_manifest",
    "check_mesh_log", "solver_log", "latest_time", "fields", "vtu", "html",
})


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _in_projects(projects_root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(projects_root)
        return True
    except ValueError:
        return False


def _artifact_probe(case: Path | None, kind: str, path: Path | None, expected_sha256: object) -> dict[str, Any]:
    result: dict[str, Any] = {
        "case": str(case) if case else None,
        "kind": kind,
        "path": str(path) if path else None,
        "read": False,
        "sha256": None,
        "status": "BLOCKED",
        "error_code": None,
    }
    if not isinstance(expected_sha256, str) or len(expected_sha256) != _HEX_LENGTH:
        result["error_code"] = f"ARTIFACT_LINK_INVALID:{kind}"
        return result
    if path is None or not path.is_file():
        result["error_code"] = f"ARTIFACT_REHASH_MISMATCH:{kind}"
        return result
    try:
        digest = _sha256_file(path)
    except OSError as exc:
        result["error_code"] = f"ARTIFACT_READ_ERROR:{exc.__class__.__name__}"
        return result
    if digest != expected_sha256:
        result["error_code"] = f"ARTIFACT_REHASH_MISMATCH:{kind}"
        return result
    result.update({"read": True, "sha256": digest, "status": "PASS"})
    return result


def _link_path(projects_root: Path, case: Path, link: object, *, expected_contract: str | None = None) -> tuple[Path | None, object, str | None]:
    if not isinstance(link, dict) or set(link) - {"path", "sha256", "contract"}:
        return None, None, "ARTIFACT_LINK_INVALID"
    relative = link.get("path")
    if not isinstance(relative, str) or not isinstance(link.get("sha256"), str):
        return None, None, "ARTIFACT_LINK_INVALID"
    if expected_contract is not None and link.get("contract") != expected_contract:
        return None, None, "ARTIFACT_LINK_INVALID"
    candidate = (projects_root / relative).resolve()
    if not _in_projects(projects_root, candidate):
        return None, None, "ARTIFACT_OUTSIDE_PROJECTS_ROOT"
    try:
        candidate.relative_to(case)
    except ValueError:
        return None, None, "ARTIFACT_CASE_SCOPE_INVALID"
    return candidate, link["sha256"], None


def _observed_case_paths(projects_root: Path) -> set[Path]:
    root = projects_root / "_body_solver"
    if not root.is_dir():
        return set()
    return {path.resolve() for path in root.rglob("*") if path.is_dir() and (path / "run_manifest.json").is_file()}


def _inventory_cases(projects_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Path | None, str | None]:
    inventory_path = (projects_root / INVENTORY_RELATIVE).resolve()
    if not inventory_path.is_file():
        return [], [{"case": None, "kind": "authoritative_case_inventory", "path": str(inventory_path), "read": False, "sha256": None, "status": "BLOCKED", "error_code": "AUTHORITATIVE_CASE_INVENTORY_MISSING"}], None, None
    inventory = _read_json(inventory_path)
    inventory_sha = _sha256_file(inventory_path)
    if (inventory is None or inventory.get("contract") != "io_authoritative_case_inventory.v1"
            or not isinstance(inventory.get("cases"), list) or not inventory["cases"]):
        return [], [{"case": None, "kind": "authoritative_case_inventory", "path": str(inventory_path), "read": True, "sha256": inventory_sha, "status": "BLOCKED", "error_code": "AUTHORITATIVE_CASE_INVENTORY_INVALID"}], inventory_path, inventory_sha
    errors: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for row in inventory["cases"]:
        if not isinstance(row, dict) or set(row) != _CASE_KEYS:
            errors.append({"case": None, "kind": "authoritative_case_inventory", "path": str(inventory_path), "read": True, "sha256": inventory_sha, "status": "BLOCKED", "error_code": "AUTHORITATIVE_CASE_INVENTORY_INVALID"})
            continue
        case_relative = row.get("case_path")
        if not isinstance(case_relative, str):
            errors.append({"case": None, "kind": "authoritative_case_inventory", "path": str(inventory_path), "read": True, "sha256": inventory_sha, "status": "BLOCKED", "error_code": "AUTHORITATIVE_CASE_INVENTORY_INVALID"})
            continue
        case = (projects_root / case_relative).resolve()
        if (not _in_projects(projects_root, case) or not case.is_dir()
                or not str(case_relative).replace("\\", "/").startswith("_body_solver/")):
            errors.append({"case": str(case), "kind": "authoritative_case", "path": str(case), "read": False, "sha256": None, "status": "BLOCKED", "error_code": "AUTHORITATIVE_CASE_SCOPE_INVALID"})
            continue
        if case in seen:
            errors.append({"case": str(case), "kind": "authoritative_case", "path": str(case), "read": False, "sha256": None, "status": "BLOCKED", "error_code": "AUTHORITATIVE_CASE_DUPLICATE"})
            continue
        seen.add(case)
        cases.append(dict(row, _case=case))
    omitted = _observed_case_paths(projects_root) - seen
    for case in sorted(omitted, key=str):
        errors.append({"case": str(case), "kind": "authoritative_case", "path": str(case), "read": False, "sha256": None, "status": "BLOCKED", "error_code": "AUTHORITATIVE_CASE_INVENTORY_INCOMPLETE"})
    return cases, errors, inventory_path, inventory_sha


def _manifest_probe(projects_root: Path, case: Path, kind: str, link: object, expected_contract: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    path, expected, error = _link_path(projects_root, case, link, expected_contract=expected_contract)
    if error:
        return _artifact_probe(case, kind, None, None), None
    probe = _artifact_probe(case, kind, path, expected)
    return probe, _read_json(path) if probe["status"] == "PASS" and path else None


def _case_probes(projects_root: Path, entry: dict[str, Any]) -> list[dict[str, Any]]:
    case = entry["_case"]
    probes: list[dict[str, Any]] = []
    mesh_probe, mesh = _manifest_probe(projects_root, case, "mesh_manifest.json", entry["mesh_manifest"], "mesh_manifest.v1")
    run_probe, run = _manifest_probe(projects_root, case, "run_manifest.json", entry["run_manifest"], "run_manifest.v1")
    result_probe, result = _manifest_probe(projects_root, case, "result_manifest.json", entry["result_manifest"], "result_manifest.v1")
    probes.extend([mesh_probe, run_probe, result_probe])
    selections = (("log_checkMesh", "check_mesh_log"), ("solver_log", "solver_log"), ("vtu", "vtu"), ("html", "html"))
    selected: dict[str, dict[str, Any]] = {}
    for kind, key in selections:
        path, expected, error = _link_path(projects_root, case, entry[key])
        probe = _artifact_probe(case, kind, path if not error else None, expected if not error else None)
        probes.append(probe)
        selected[key] = entry[key]
    latest = entry.get("latest_time")
    fields = entry.get("fields") if isinstance(entry.get("fields"), dict) else {}
    for field in ("T", "U", "phi", "V"):
        link = fields.get(field)
        path, expected, error = _link_path(projects_root, case, link)
        if not isinstance(latest, str) or not isinstance(link, dict) or link.get("path") != f"{entry['case_path']}/{latest}/{field}":
            error = "ARTIFACT_LINK_INVALID"
        probes.append(_artifact_probe(case, f"latest_{field}", path if not error else None, expected if not error else None))
    # The result manifest is the authority for selected post-processing output,
    # so inventory links must agree exactly instead of merely finding a glob.
    if (mesh is None or run is None or result is None or mesh.get("status") != "PASS"
            or run.get("status") != "PASS" or run.get("requested_ranks") != 1
            or not isinstance(result.get("source"), dict) or not isinstance(result.get("html"), dict)
            or result["source"].get("path") != selected["vtu"].get("path")
            or result["source"].get("sha256") != selected["vtu"].get("sha256")
            or result["html"].get("path") != selected["html"].get("path")
            or result["html"].get("sha256") != selected["html"].get("sha256")
            or result.get("mesh_manifest_sha256") != entry["mesh_manifest"].get("sha256")
            or result.get("run_manifest_sha256") != entry["run_manifest"].get("sha256")):
        probes.append({"case": str(case), "kind": "manifest_linkage", "path": str(case / "result_manifest.json"), "read": False, "sha256": None, "status": "BLOCKED", "error_code": "MANIFEST_LINKAGE_INVALID"})
    return probes


def _recovered_artifact_probes(projects_root: Path) -> tuple[list[dict[str, Any]], Path | None, str | None]:
    entries, errors, inventory_path, inventory_sha = _inventory_cases(projects_root)
    probes = list(errors)
    for entry in entries:
        probes.extend(_case_probes(projects_root, entry))
    return probes, inventory_path, inventory_sha


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
    artifact_probes, inventory_path, inventory_sha = _recovered_artifact_probes(projects_root)
    status = "PASS" if all(item["status"] == "PASS" for item in probes + artifact_probes) else "BLOCKED"
    return {
        "schema_version": 1,
        "contract": "io_acceptance.v1",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "projects_root": str(projects_root),
        "roots": list(REQUIRED_ROOTS),
        "status": status,
        "probes": probes,
        "inventory_path": str(inventory_path.relative_to(projects_root)).replace("\\", "/") if inventory_path else str(INVENTORY_RELATIVE).replace("\\", "/"),
        "inventory_sha256": inventory_sha,
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
