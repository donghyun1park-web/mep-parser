"""Create a reproducible, read-only V&V baseline inventory."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _files_hash(paths: Iterable[Path], root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((item for item in paths if item.is_file()), key=lambda item: item.as_posix()):
        digest.update(root.joinpath(path).relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _git(repo_root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args], cwd=repo_root, capture_output=True, check=True,
            text=True, encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return completed.stdout.strip()


def _dirty_paths(repo_root: Path) -> list[str]:
    output = _git(repo_root, "status", "--porcelain")
    return [line[3:] if len(line) >= 3 else line for line in output.splitlines() if line]


def _dirty_path_hashes(repo_root: Path, paths: list[str]) -> dict[str, str | None]:
    hashes: dict[str, str | None] = {}
    for value in paths:
        candidate = repo_root / value
        hashes[value] = _sha256_file(candidate) if candidate.is_file() else None
    return hashes


def _junit_summary(junit_path: Path | None) -> tuple[dict[str, Any], list[dict[str, str]]]:
    if junit_path is None or not junit_path.is_file():
        return (
            {"status": "NOT_RUN", "passed": None, "failed": None, "errors": None, "skipped": None, "command": None},
            [],
        )
    root = ET.parse(junit_path).getroot()
    suites = list(root.iter("testsuite")) if root.tag == "testsuites" else [root]
    tests = sum(int(suite.attrib.get("tests", 0)) for suite in suites)
    failures = sum(int(suite.attrib.get("failures", 0)) for suite in suites)
    errors = sum(int(suite.attrib.get("errors", 0)) for suite in suites)
    skipped = sum(int(suite.attrib.get("skipped", 0)) for suite in suites)
    runtime_skips: list[dict[str, str]] = []
    for testcase in root.iter("testcase"):
        skipped_node = testcase.find("skipped")
        if skipped_node is not None:
            runtime_skips.append({
                "test": ".".join(filter(None, [testcase.attrib.get("classname"), testcase.attrib.get("name")])),
                "reason": skipped_node.attrib.get("message", "unspecified"),
                "condition": "environment-dependent test becomes runnable when its dependency is available",
            })
    return (
        {
            "status": "PASS" if failures == 0 and errors == 0 else "FAIL",
            "passed": max(tests - failures - errors - skipped, 0),
            "failed": failures,
            "errors": errors,
            "skipped": skipped,
            "tests": tests,
            "junit_sha256": _sha256_file(junit_path),
        },
        runtime_skips,
    )


def _hash_inventory(root: Path, pattern: str) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): _sha256_file(path)
        for path in sorted(root.rglob(pattern))
        if path.is_file()
    }


def _dependency_snapshot(repo_root: Path) -> str:
    names = ["requirements.txt", "requirements-dev.in", "requirements-dev.lock", "toolchain.lock.json"]
    paths = [repo_root / name for name in names if (repo_root / name).is_file()]
    return _files_hash(paths, repo_root)


def _installed_distribution_snapshot() -> str:
    rows = []
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name") or distribution.metadata.get("Summary") or "UNKNOWN"
        rows.append(f"{name.casefold()}=={distribution.version}")
    return _sha256_bytes("\n".join(sorted(rows)).encode("utf-8"))


def build_vv_baseline(repo_root: Path, projects_root: Path, junit_path: Path | None = None) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    projects_root = projects_root.resolve()
    git_head = _git(repo_root, "rev-parse", "HEAD") or "UNAVAILABLE"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate_id = f"baseline-{stamp}-{git_head[:12].lower()}"
    capability_path = projects_root / "capability_manifest.json"
    dirty_paths = _dirty_paths(repo_root)
    test_summary, runtime_skips = _junit_summary(junit_path)
    if junit_path is not None:
        test_summary["command"] = "python -m pytest tests -q --junitxml=<path>"
    return {
        "schema_version": 1,
        "contract": "vv_baseline.v1",
        "candidate_id": candidate_id,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "git_head": git_head,
        "dirty_paths": dirty_paths,
        "dirty_path_hashes": _dirty_path_hashes(repo_root, dirty_paths),
        "python_version": sys.version,
        "python_executable": str(Path(sys.executable).resolve()),
        "python_executable_sha256": _sha256_file(Path(sys.executable).resolve()),
        "python_architecture": platform.architecture()[0],
        "installed_distribution_snapshot_sha256": _installed_distribution_snapshot(),
        "dependency_snapshot_sha256": _dependency_snapshot(repo_root),
        "schema_hashes": _hash_inventory(repo_root, "*.schema.json"),
        "benchmark_hashes": _hash_inventory(repo_root / "cfd_benchmarks", "*") if (repo_root / "cfd_benchmarks").exists() else {},
        "capability_hash": _sha256_file(capability_path) if capability_path.is_file() else None,
        "test_summary": test_summary,
        "runtime_skips": runtime_skips,
        "projects_root": str(projects_root),
    }


def validate_vv_baseline(payload: dict[str, Any]) -> list[str]:
    required = [
        "candidate_id", "created_at", "git_head", "dirty_paths", "python_version", "python_executable",
        "python_executable_sha256", "python_architecture", "installed_distribution_snapshot_sha256",
        "dependency_snapshot_sha256", "schema_hashes", "benchmark_hashes",
        "capability_hash", "test_summary", "runtime_skips", "dirty_path_hashes",
    ]
    blockers = [f"MISSING:{name}" for name in required if name not in payload]
    if payload.get("contract") != "vv_baseline.v1" or payload.get("schema_version") != 1:
        blockers.append("CONTRACT_INVALID")
    for field in ("dependency_snapshot_sha256", "python_executable_sha256", "installed_distribution_snapshot_sha256"):
        value = payload.get(field)
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            blockers.append(f"HASH_INVALID:{field}")
    for field in ("python_executable", "python_version", "python_architecture"):
        if not isinstance(payload.get(field), str) or not payload[field].strip():
            blockers.append(f"RUNTIME_IDENTITY_INVALID:{field}")
    return blockers


def write_vv_baseline(payload: dict[str, Any], output: Path) -> Path:
    blockers = validate_vv_baseline(payload)
    if blockers:
        raise ValueError("INVALID_VV_BASELINE:" + ",".join(blockers))
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--projects-root", type=Path, required=True)
    parser.add_argument("--junit", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args(argv)
    payload = build_vv_baseline(args.repo_root, args.projects_root, args.junit)
    output = args.output
    if output is None and args.output_root is not None:
        output = args.output_root / payload["candidate_id"] / "vv_baseline.json"
    if output is None:
        parser.error("one of --output or --output-root is required")
    output = write_vv_baseline(payload, output)
    print(json.dumps({"status": "PASS", "output": str(output), "candidate_id": payload["candidate_id"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
