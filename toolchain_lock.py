"""Fail-closed validation for the pinned test/runtime toolchain contract."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


_EXACT_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_PINNED_PACKAGE_VERSION = re.compile(r"^\d+(?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


def validate_lock(payload: dict[str, Any]) -> list[str]:
    """Return stable blockers; an empty list means the lock is installable."""

    blockers: list[str] = []
    if payload.get("schema_version") != 1:
        blockers.append("SCHEMA_VERSION_INVALID")
    if payload.get("contract") != "toolchain.lock.v1":
        blockers.append("CONTRACT_INVALID")

    python = payload.get("python")
    if not isinstance(python, dict):
        blockers.append("PYTHON_SECTION_MISSING")
    else:
        version = python.get("version")
        if not isinstance(version, str) or not _EXACT_VERSION.fullmatch(version):
            blockers.append("PYTHON_VERSION_NOT_EXACT")
        if python.get("architecture") != "x64":
            blockers.append("PYTHON_ARCHITECTURE_NOT_X64")
        installer_hash = python.get("installer_sha256")
        if not isinstance(installer_hash, str) or not _SHA256.fullmatch(installer_hash):
            blockers.append("PYTHON_INSTALLER_HASH_MISSING")

    pip = payload.get("pip")
    if not isinstance(pip, dict) or not _EXACT_VERSION.fullmatch(str(pip.get("version", ""))):
        blockers.append("PIP_VERSION_NOT_EXACT")

    packages = payload.get("packages")
    if not isinstance(packages, dict) or not packages:
        blockers.append("PACKAGES_MISSING")
    else:
        for name in sorted(packages):
            package = packages[name]
            if not isinstance(package, dict):
                blockers.append(f"PACKAGE_SECTION_INVALID:{name}")
                continue
            version = package.get("version")
            if not isinstance(version, str) or not _PINNED_PACKAGE_VERSION.fullmatch(version):
                blockers.append(f"PACKAGE_VERSION_NOT_EXACT:{name}")
            hashes = package.get("hashes")
            if not isinstance(hashes, list) or not hashes or any(
                not isinstance(value, str) or not _SHA256.fullmatch(value) for value in hashes
            ):
                blockers.append(f"PACKAGE_HASH_MISSING:{name}")

    if payload.get("ready") is not True:
        blockers.append("LOCK_NOT_READY")
    return blockers


def load_lock(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=Path("toolchain.lock.json"))
    args = parser.parse_args(argv)
    try:
        blockers = validate_lock(load_lock(args.lock))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "BLOCKED", "blockers": [f"LOCK_READ_ERROR:{exc}"]}))
        return 2
    status = "PASS" if not blockers else "BLOCKED"
    print(json.dumps({"status": status, "blockers": blockers}, ensure_ascii=False))
    return 0 if not blockers else 2


if __name__ == "__main__":
    raise SystemExit(main())

