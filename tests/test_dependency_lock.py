import hashlib
import json
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


def _valid_lock():
    return {
        "schema_version": 1,
        "contract": "toolchain.lock.v1",
        "ready": True,
        "python": {
            "version": "3.12.10",
            "architecture": "x64",
            "installer_sha256": "a" * 64,
            "executable_sha256": "b" * 64,
            "signer_thumbprint": "c" * 40,
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


def test_validate_lock_rejects_non_patch_runtime_and_pip_versions():
    from toolchain_lock import validate_lock

    lock = _valid_lock()
    lock["python"]["version"] = "3.12"
    lock["pip"]["version"] = "25.1"

    blockers = validate_lock(lock)

    assert "PYTHON_VERSION_NOT_EXACT" in blockers
    assert "PIP_VERSION_NOT_EXACT" in blockers


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


def test_repository_lock_is_ready_with_exact_pip_and_hashed_wheels():
    """A release lock must install without version resolution or placeholder hashes."""
    from toolchain_lock import validate_lock

    repo_root = Path(__file__).resolve().parents[1]
    payload = json.loads((repo_root / "toolchain.lock.json").read_text(encoding="utf-8"))
    requirement_lines = [
        line.strip()
        for line in (repo_root / "requirements-dev.lock").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]

    assert payload["ready"] is True
    assert payload["pip"]["version"] == "25.1.1"
    assert validate_lock(payload, repo_root / "requirements-dev.lock") == []
    assert all("==" in line and ">" not in line and "<" not in line for line in requirement_lines)
    assert all(re.search(r"--hash=sha256:[0-9a-f]{64}", line) for line in requirement_lines)
    assert set(payload["packages"]) == {
        re.match(r"([a-zA-Z0-9_.-]+)==", line).group(1).lower()
        for line in requirement_lines
    }
    assert all(
        all(re.fullmatch(r"[0-9a-f]{64}", digest) for digest in package["hashes"])
        for package in payload["packages"].values()
    )


def test_repository_lock_conforms_to_declared_draft_2020_12_schema():
    repo_root = Path(__file__).resolve().parents[1]
    payload = json.loads((repo_root / "toolchain.lock.json").read_text(encoding="utf-8"))
    schema = json.loads((repo_root / "toolchain.lock.v1.schema.json").read_text(encoding="utf-8"))

    assert list(Draft202012Validator(schema).iter_errors(payload)) == []


@pytest.mark.parametrize("mutation, expected", [
    ("version", "REQUIREMENTS_LOCK_VERSION_MISMATCH:pip"),
    ("hash", "REQUIREMENTS_LOCK_HASH_MISMATCH:pip"),
    ("missing", "REQUIREMENTS_LOCK_PACKAGE_SET_MISMATCH"),
    ("extra", "REQUIREMENTS_LOCK_PACKAGE_SET_MISMATCH"),
])
def test_validate_lock_rejects_requirement_lock_drift(tmp_path, mutation, expected):
    from toolchain_lock import validate_lock

    repo_root = Path(__file__).resolve().parents[1]
    payload = json.loads((repo_root / "toolchain.lock.json").read_text(encoding="utf-8"))
    requirements = (repo_root / "requirements-dev.lock").read_text(encoding="utf-8")
    if mutation == "version":
        requirements = requirements.replace("pip==25.1.1", "pip==25.1.1.post1")
    elif mutation == "hash":
        requirements = requirements.replace(
            "2913a38a2abf4ea6b64ab507bd9e967f3b53dc1ede74b01b0931e1ce548751af",
            "0" * 64,
        )
    elif mutation == "missing":
        requirements = "\n".join(
            line for line in requirements.splitlines() if not line.startswith("pip==")
        ) + "\n"
    else:
        requirements += "extra-package==1.0 --hash=sha256:" + "d" * 64 + "\n"
    lock_path = tmp_path / "requirements-dev.lock"
    lock_path.write_text(requirements, encoding="utf-8")

    assert expected in validate_lock(payload, lock_path)


def test_bootstrap_rejects_mismatched_python_before_creating_environment():
    """The identity preflight must stop before a mismatched interpreter can create a venv."""
    repo_root = Path(__file__).resolve().parents[1]
    environment_path = repo_root / ".superpowers" / "sdd" / "2026-08-24-mep-cfd-master-development" / "bootstrap-preflight-must-not-exist"
    python_exe = repo_root / ".superpowers" / "sdd" / "2026-08-24-mep-cfd-master-development" / f"mismatched-python-{uuid.uuid4().hex}.ps1"
    assert not environment_path.exists()
    python_exe.write_text(
        "param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)\n"
        "if ($Arguments -contains '-m') { exit 0 }\n"
        "if ($Arguments -contains '-c') { Write-Output '3.12.10|64bit'; exit 0 }\n"
        "exit 1\n",
        encoding="utf-8",
    )
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(repo_root / "scripts" / "bootstrap_test_env.ps1"),
                "-PythonExe",
                str(python_exe),
                "-EnvironmentPath",
                str(environment_path),
                "-LockPath",
                str(repo_root / "toolchain.lock.json"),
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        assert completed.returncode != 0
        assert "PYTHON_EXECUTABLE_NOT_EXE" in (
            completed.stdout + completed.stderr
        )
        assert not environment_path.exists()
    finally:
        python_exe.unlink(missing_ok=True)


def test_bootstrap_rejects_signed_venv_launcher_with_unpinned_hash(tmp_path):
    """Matching runtime output cannot substitute for the pinned base executable bytes."""
    repo_root = Path(__file__).resolve().parents[1]
    environment_path = tmp_path / "must-not-exist"

    completed = subprocess.run(
        ["powershell.exe", "-ExecutionPolicy", "Bypass", "-File", str(repo_root / "scripts" / "bootstrap_test_env.ps1"),
         "-PythonExe", sys.executable, "-EnvironmentPath", str(environment_path),
         "-LockPath", str(repo_root / "toolchain.lock.json")],
        cwd=repo_root, capture_output=True, text=True, encoding="utf-8",
    )

    assert completed.returncode != 0
    assert "PYTHON_EXECUTABLE_HASH_MISMATCH" in completed.stdout + completed.stderr
    assert not environment_path.exists()


def _authenticated_base_python(repo_root):
    payload = json.loads((repo_root / "toolchain.lock.json").read_text(encoding="utf-8"))
    python_exe = Path(sys._base_executable)
    assert python_exe.suffix.lower() == ".exe"
    assert hashlib.sha256(python_exe.read_bytes()).hexdigest() == payload["python"]["executable_sha256"]
    return python_exe


def test_bootstrap_removes_new_environment_after_post_create_install_failure(tmp_path):
    """A bad but structurally valid pip wheel hash must not leave a partial venv."""
    repo_root = Path(__file__).resolve().parents[1]
    environment_path = tmp_path / "must-be-removed"
    lock_path = tmp_path / "toolchain.lock.json"
    requirements_path = tmp_path / "requirements-dev.lock"
    payload = json.loads((repo_root / "toolchain.lock.json").read_text(encoding="utf-8"))
    payload["packages"]["pip"]["hashes"] = ["0" * 64]
    lock_path.write_text(json.dumps(payload), encoding="utf-8")
    requirements_path.write_text(
        (repo_root / "requirements-dev.lock").read_text(encoding="utf-8").replace(
            "2913a38a2abf4ea6b64ab507bd9e967f3b53dc1ede74b01b0931e1ce548751af", "0" * 64
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["powershell.exe", "-ExecutionPolicy", "Bypass", "-File", str(repo_root / "scripts" / "bootstrap_test_env.ps1"),
         "-PythonExe", str(_authenticated_base_python(repo_root)), "-EnvironmentPath", str(environment_path),
         "-LockPath", str(lock_path), "-RequirementsLock", str(requirements_path)],
        cwd=repo_root, capture_output=True, text=True, encoding="utf-8",
    )

    assert completed.returncode != 0
    assert "PIP_INSTALL_FAILED" in completed.stdout + completed.stderr
    assert not environment_path.exists()


def test_bootstrap_creates_exact_pip_and_pytest_environment():
    """A fresh supplied Python 3.12 runtime must produce the fully pinned test environment."""
    repo_root = Path(__file__).resolve().parents[1]
    environment_path = repo_root / ".superpowers" / "sdd" / "2026-08-24-mep-cfd-master-development" / f"bootstrap-success-{uuid.uuid4().hex}"
    python_exe = _authenticated_base_python(repo_root)
    assert not environment_path.exists()
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(repo_root / "scripts" / "bootstrap_test_env.ps1"),
                "-PythonExe",
                str(python_exe),
                "-EnvironmentPath",
                str(environment_path),
                "-LockPath",
                str(repo_root / "toolchain.lock.json"),
            ],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

        assert completed.returncode == 0, completed.stdout + completed.stderr
        identity = subprocess.run(
            [str(environment_path / "Scripts" / "python.exe"), "-c", "import pip,pytest,sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}|{pip.__version__}|{pytest.__version__}')"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        assert identity.stdout.strip() == "3.12.10|25.1.1|8.3.5"
        assert [path.name for path in (environment_path / "Lib" / "site-packages").glob("pip-*.dist-info")] == [
            "pip-25.1.1.dist-info"
        ]
    finally:
        if environment_path.exists():
            shutil.rmtree(environment_path)
        assert not environment_path.exists()
