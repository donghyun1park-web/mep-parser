"""Fail-closed validation for the pinned test/runtime toolchain contract."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


_RUNTIME_VERSION = re.compile(r"^\d+\.\d+\.\d+$")
_PACKAGE_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+)*(?:\.post[0-9]+)?$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_THUMBPRINT = re.compile(r"^[0-9a-fA-F]{40}$")
_REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[0-9]+(?:\.[0-9]+)*(?:\.post[0-9]+)?)"
    r"(?P<hashes>(?:\s+--hash=sha256:[0-9a-fA-F]{64})+)$"
)


def _normalize_package_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_requirements_lock(path: Path) -> dict[str, tuple[str, set[str]]]:
    """Parse the closed requirements lock without allowing resolver syntax."""

    packages: dict[str, tuple[str, set[str]]] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _REQUIREMENT.fullmatch(line)
        if match is None:
            raise ValueError(f"REQUIREMENTS_LOCK_INVALID:line={line_number}")
        name = _normalize_package_name(match.group("name"))
        hashes = set(re.findall(r"--hash=sha256:([0-9a-fA-F]{64})", match.group("hashes")))
        if name in packages:
            raise ValueError(f"REQUIREMENTS_LOCK_DUPLICATE:{name}")
        packages[name] = (match.group("version"), {value.lower() for value in hashes})
    return packages


def validate_lock(payload: dict[str, Any], requirements_lock: Path | None = None) -> list[str]:
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
        if not isinstance(version, str) or not _RUNTIME_VERSION.fullmatch(version):
            blockers.append("PYTHON_VERSION_NOT_EXACT")
        if python.get("architecture") != "x64":
            blockers.append("PYTHON_ARCHITECTURE_NOT_X64")
        installer_hash = python.get("installer_sha256")
        if not isinstance(installer_hash, str) or not _SHA256.fullmatch(installer_hash):
            blockers.append("PYTHON_INSTALLER_HASH_MISSING")
        executable_hash = python.get("executable_sha256")
        if not isinstance(executable_hash, str) or not _SHA256.fullmatch(executable_hash):
            blockers.append("PYTHON_EXECUTABLE_HASH_MISSING")
        signer_thumbprint = python.get("signer_thumbprint")
        if not isinstance(signer_thumbprint, str) or not _THUMBPRINT.fullmatch(signer_thumbprint):
            blockers.append("PYTHON_SIGNER_THUMBPRINT_MISSING")

    pip = payload.get("pip")
    if not isinstance(pip, dict) or not _RUNTIME_VERSION.fullmatch(str(pip.get("version", ""))):
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
            if not isinstance(version, str) or not _PACKAGE_VERSION.fullmatch(version):
                blockers.append(f"PACKAGE_VERSION_NOT_EXACT:{name}")
            hashes = package.get("hashes")
            if not isinstance(hashes, list) or not hashes or any(
                not isinstance(value, str) or not _SHA256.fullmatch(value) for value in hashes
            ):
                blockers.append(f"PACKAGE_HASH_MISSING:{name}")

    if requirements_lock is not None:
        try:
            requirements = parse_requirements_lock(requirements_lock)
        except (OSError, ValueError) as exc:
            blockers.append(str(exc).split(":", 1)[0])
        else:
            expected = {
                _normalize_package_name(name): (
                    package.get("version"),
                    {str(value).lower() for value in package.get("hashes", [])},
                )
                for name, package in packages.items()
                if isinstance(package, dict)
            } if isinstance(packages, dict) else {}
            if set(requirements) != set(expected):
                blockers.append("REQUIREMENTS_LOCK_PACKAGE_SET_MISMATCH")
            for name in sorted(set(requirements) & set(expected)):
                requirement_version, requirement_hashes = requirements[name]
                lock_version, lock_hashes = expected[name]
                if requirement_version != lock_version:
                    blockers.append(f"REQUIREMENTS_LOCK_VERSION_MISMATCH:{name}")
                if requirement_hashes != lock_hashes:
                    blockers.append(f"REQUIREMENTS_LOCK_HASH_MISMATCH:{name}")

    if payload.get("ready") is not True:
        blockers.append("LOCK_NOT_READY")
    return blockers


def load_lock(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=Path("toolchain.lock.json"))
    parser.add_argument("--requirements-lock", type=Path, default=Path("requirements-dev.lock"))
    args = parser.parse_args(argv)
    try:
        blockers = validate_lock(load_lock(args.lock), args.requirements_lock)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "BLOCKED", "blockers": [f"LOCK_READ_ERROR:{exc}"]}))
        return 2
    status = "PASS" if not blockers else "BLOCKED"
    print(json.dumps({"status": status, "blockers": blockers}, ensure_ascii=False))
    return 0 if not blockers else 2


if __name__ == "__main__":
    raise SystemExit(main())

