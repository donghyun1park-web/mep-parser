import hashlib
import json
from pathlib import Path

import pytest


def _valid_lock():
    return {
        "schema_version": 1,
        "contract": "toolchain.lock.v1",
        "ready": True,
        "python": {
            "version": "3.12.10",
            "architecture": "x64",
            "installer_sha256": "a" * 64,
        },
        "pip": {"version": "25.1.1"},
        "packages": {
            "pytest": {"version": "8.3.5", "hashes": ["b" * 64]},
            "jsonschema": {"version": "4.23.0", "hashes": ["c" * 64]},
        },
    }


def test_validate_lock_accepts_exact_pinned_hashes():
    from toolchain_lock import validate_lock

    assert validate_lock(_valid_lock()) == []


def test_validate_lock_rejects_missing_hashes_and_floating_versions():
    from toolchain_lock import validate_lock

    lock = _valid_lock()
    lock["python"]["installer_sha256"] = ""
    lock["packages"]["pytest"]["version"] = ">=8"
    lock["packages"]["pytest"]["hashes"] = []

    blockers = validate_lock(lock)

    assert "PYTHON_INSTALLER_HASH_MISSING" in blockers
    assert "PACKAGE_VERSION_NOT_EXACT:pytest" in blockers
    assert "PACKAGE_HASH_MISSING:pytest" in blockers


def test_lock_file_is_machine_readable_and_hashes_are_sha256():
    from toolchain_lock import validate_lock

    path = Path(__file__).resolve().parents[1] / "toolchain.lock.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    blockers = validate_lock(payload)

    assert payload["contract"] == "toolchain.lock.v1"
    installer_hash = payload["python"].get("installer_sha256")
    if installer_hash:
        assert len(installer_hash) == 64
    assert all(
        len(value) == 64
        for package in payload["packages"].values()
        for value in package.get("hashes", [])
    )
    assert isinstance(blockers, list)
