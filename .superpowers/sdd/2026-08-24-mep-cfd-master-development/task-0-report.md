# Task 0 report — reproducible validation/toolchain baseline

## Status

`DONE_WITH_CONCERNS` on commit `eef978ee103bb11ac6fc639d1a9422cb87f0e674` (`build: lock reproducible validation toolchain`).

The source/config/test baseline is committed. Runtime evidence is ignored and was not staged.

## Implemented changes

- Replaced the placeholder lock with a ready Python 3.12 x64 contract: Python installer SHA-256 `67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb`, pip `25.1.1`, and 24 exact CPython 3.12 x64 Windows wheel hashes (six direct pins plus transitives).
- Made `requirements-dev.lock` complete, exact, hash-required, and sourced from the official `https://pypi.org/simple` index. `requirements-dev.in` remains the specified six direct packages.
- Updated `toolchain_lock.py` to accept exact PEP 440-style transitive pins such as `26.3` and `2.9.0.post0`, while retaining exact Python/pip identity checks.
- Made `bootstrap_test_env.ps1` install the pinned pip wheel in a dedicated hash-locked phase, install the full lock from PyPI with `--require-hashes`, verify pip `25.1.1`, and then verify pytest.
- Added behavior tests for ready repository locks, real hash syntax, portable mismatch preflight without environment creation, and a clean supplied-Python bootstrap that yields Python `3.12.10`, pip `25.1.1`, and pytest `8.3.5`.
- Added `.venv-vv/` to `.gitignore`; the environment is runtime-only.
- Added `vv_baseline.write_vv_baseline_reference()` and a test. It writes a sibling reference containing the canonical projects-root-relative `baseline_evidence_path` and SHA-256 required by the controller ruling.

The Python 3.12.10 installer was downloaded from python.org into ignored task scratch only, checked against the supplied SHA-256, and had `AuthenticodeSignature Status=Valid`. CPython 3.12 Windows wheels were resolved through the official PyPI URL only.

## TDD evidence

### RED

Command:

```powershell
python -B -m pytest -q tests/test_dependency_lock.py
```

Output:

```text
...FF                                                                    [100%]
FAILED test_repository_lock_is_ready_with_exact_pip_and_hashed_wheels
  assert False is True
FAILED test_bootstrap_rejects_mismatched_python_before_creating_environment
  expected PYTHON_IDENTITY_MISMATCH, received TOOLCHAIN_LOCK_BLOCKED with
  PYTHON_INSTALLER_HASH_MISSING, PACKAGE_HASH_MISSING:..., LOCK_NOT_READY
2 failed, 3 passed
```

This proved both missing readiness/hash material and the incomplete bootstrap path before config/production changes.

Command:

```powershell
.\.venv-vv\Scripts\python.exe -B -m pytest -q tests/test_vv_baseline.py::test_write_vv_baseline_reference_binds_canonical_relative_path_and_hash
```

Output:

```text
ImportError: cannot import name 'write_vv_baseline_reference' from 'vv_baseline'
1 failed
```

### GREEN

Command:

```powershell
.\.venv-vv\Scripts\python.exe -B -m pytest -q tests/test_dependency_lock.py tests/test_vv_baseline.py tests/test_working_validation.py tests/test_io_acceptance.py --junitxml=.superpowers\sdd\2026-08-24-mep-cfd-master-development\task-0-focused-junit.xml
```

JUnit result: `tests=26 failures=0 errors=0 skipped=0`.

Command:

```powershell
.\.venv-vv\Scripts\python.exe -B -m pytest -q tests --junitxml=.superpowers\sdd\2026-08-24-mep-cfd-master-development\task-0-precommit-full-junit.xml
```

Result: `tests=632 failures=0 errors=0 skipped=14`.

Fresh published acceptance command (after deleting only the task-created ignored `.venv-vv`):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\bootstrap_test_env.ps1 -PythonExe '.superpowers\sdd\2026-08-24-mep-cfd-master-development\python-3.12-base\Scripts\python.exe'
.\.venv-vv\Scripts\python.exe -c "import platform,sys; print(f'Python={sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}|{platform.architecture()[0]}')"
.\.venv-vv\Scripts\python.exe -m pip --version
.\.venv-vv\Scripts\python.exe -m pytest --version
```

Output:

```text
{"status": "PASS", "blockers": []}
Python=3.12.10|64bit
pip 25.1.1 ... (python 3.12)
pytest 8.3.5
ACCEPTANCE_EXIT=0
```

## Post-commit runtime evidence

The post-commit full suite was regenerated using the required `.venv-vv` runtime:

```powershell
.\.venv-vv\Scripts\python.exe -B -m pytest -q tests --junitxml=cfd_projects\_release_evidence\vv\staging\junit.xml
```

Final JUnit result: `tests=632 failures=0 errors=0 skipped=14`.

Canonical candidate: `baseline-20260824T020030Z-eef978ee103b`.

- JUnit: `cfd_projects/_release_evidence/vv/baseline-20260824T020030Z-eef978ee103b/junit.xml`
  - SHA-256: `884d8507fcef9722497d2f4f9d7b0f4474b0481f41cf3a628c0a3b05162b97fc`
- Baseline JSON: `cfd_projects/_release_evidence/vv/baseline-20260824T020030Z-eef978ee103b/vv_baseline.json`
  - SHA-256: `2989983447361da3f5f0e4b75a81b5e16da9bd97635ec5798a5903d4f0308abb`
  - JSON test summary: `PASS`, `632` tests, `0` failed, `0` errors, `14` skips. All 14 skip entries have test name/reason/release-condition data.
- Stable reference: `cfd_projects/_release_evidence/vv/baseline-20260824T020030Z-eef978ee103b/baseline_evidence.reference.v1.json`
  - SHA-256: `2155bfc7154d81385d1aa4f563e90d1e6cdab97e0e107aa8d8ef88434dd8518c`
  - `baseline_evidence_path`: `_release_evidence/vv/baseline-20260824T020030Z-eef978ee103b/vv_baseline.json`
  - `baseline_evidence_sha256`: `2989983447361da3f5f0e4b75a81b5e16da9bd97635ec5798a5903d4f0308abb`
- I/O probe: `cfd_projects/_release_evidence/vv/baseline-20260824T020030Z-eef978ee103b/io_acceptance.json`
  - SHA-256: `074bc8330b3315428c33480ad8eeaf9dd520161f4ff909aceaccfa0d0dc3b5ce`
  - Six selected roots passed read/create/atomic-replace/delete; access denied count is `0`.
  - Overall status is `BLOCKED` only because `AUTHORITATIVE_CASE_INVENTORY_MISSING`; this clean baseline has no recovered solver case inventory. No synthetic case/evidence was created to force a PASS.

## Files changed

- `.gitignore`
- `requirements-dev.lock`
- `scripts/bootstrap_test_env.ps1`
- `tests/test_dependency_lock.py`
- `tests/test_vv_baseline.py`
- `toolchain.lock.json`
- `toolchain_lock.py`
- `vv_baseline.py`

## Self-review and concerns

- Reviewed staged scope before commit: exactly the eight files above; no dirty original-checkout files, runtime evidence, credentials, wheels, installer, or environment were staged.
- `git diff --check` was clean (only Git line-ending warnings were emitted).
- The supplied Python scratch venv resolves its base interpreter from user AppData. Default sandbox execution cannot read that path and reports `No Python at ...`; elevated execution, the intended local runtime context, consistently reports Python 3.12.10 and completes the bootstrap. This is an environment permission boundary, not a quoted-path corruption.
- The I/O acceptance remains intentionally fail-closed at the authoritative-inventory layer. Root I/O is proven, but no solver/design evidence claim is made and Task 0 should not manufacture inventory merely to make the runtime artifact PASS.

---

## Review-fix round — commit `8bb9c5e5b8a19aacc69cd1249cb6303682d009eb`

Status: `DONE_WITH_CONCERNS` (`fix: harden reproducible validation bootstrap`). This section supersedes the earlier baseline runtime evidence above; the canonical post-fix candidate is recorded below.

### Implemented changes

- Authenticated the supplied base interpreter before executing it: it must be a real `.exe`, match SHA-256 `4d6f5f81a4bca11191c4c7c6b43632694d0a4ce74e068619d8fdc161d469859a`, have a valid Authenticode/Windows-trust signature, match signer thumbprint `DE01DAAE82D04F466A576E178F6B07A839238953`, and then match exactly `3.12.10|64bit`.
- Seeded a `--without-pip` venv using the exact, hashed pip `25.1.1` wheel into its site-packages, asserted one `pip-25.1.1.dist-info`, then installed the full lock with `--require-hashes` from PyPI. This avoids self-replacing the executing venv pip.
- Made every post-create bootstrap failure retry deletion of only the newly-created environment, then rethrow. A deterministic bad pip-hash test proves no residual environment.
- Made `toolchain_lock.py` parse `requirements-dev.lock` and require normalized package-name, version, and full hash-set equality (including pip); the CLI and bootstrap invoke that validation.
- Added the pinned executable hash/thumbprint fields to the closed lock/schema. Runtime/pip retain exact three-component versions; packages accept exact `26.3` and `2.9.0.post0` pins only.
- Bound baseline references to exactly `_release_evidence/vv/<payload candidate_id>/vv_baseline.json`, including candidate format validation and traversal/wrong-directory/wrong-name/candidate-mismatch rejection tests.

### TDD evidence

#### RED

Command:

```powershell
.\.venv-vv\Scripts\python.exe -B -m pytest -q tests/test_dependency_lock.py tests/test_vv_baseline.py
```

Output before production/config changes:

```text
11 failed, 9 passed in 14.59s
```

Representative expected failures covered missing executable authentication fields, schema rejection of `26.3`/`2.9.0.post0`, missing requirements-lock equality API, missing non-`.exe` preflight, missing post-create cleanup, and non-canonical baseline-reference rejection.

#### GREEN — authoritative focused suite

Command:

```powershell
.\.venv-vv\Scripts\python.exe -B -m pytest -q tests/test_dependency_lock.py tests/test_vv_baseline.py tests/test_working_validation.py tests/test_io_acceptance.py --junitxml .\.superpowers\sdd\2026-08-24-mep-cfd-master-development\task-0-fix-focused-26-junit.xml
```

JUnit result: `tests=38 failures=0 errors=0 skipped=0 time=115.734s`.

The direct clean bootstrap was run from the authenticated installed Python in a hidden elevated child (the command runner otherwise ends long installs at 30 seconds). Its ignored logs are under `.superpowers/sdd/2026-08-24-mep-cfd-master-development/bootstrap-runtime/`; final output included `Successfully installed ... pytest-8.3.5` and `pytest 8.3.5`. The fresh `.venv-vv` has Python `3.12.10`, pip `25.1.1`, pytest `8.3.5`, and exactly one `pip-25.1.1.dist-info`.

#### Full suite

Command:

```powershell
.\.venv-vv\Scripts\python.exe -B -m pytest -q --junitxml .\.superpowers\sdd\2026-08-24-mep-cfd-master-development\task-0-fix-full-junit.xml
```

JUnit result: `tests=644 failures=0 errors=0 skipped=14 time=211.104s`. The 14 skips are existing runtime-gated coverage, not a toolchain PASS claim.

### Canonical post-fix runtime evidence (ignored and uncommitted)

Candidate: `baseline-20260824T024845Z-8bb9c5e5b8a1` (baseline `git_head=8bb9c5e5b8a19aacc69cd1249cb6303682d009eb`, `dirty_paths=0`).

- `cfd_projects/_release_evidence/vv/baseline-20260824T024845Z-8bb9c5e5b8a1/junit.xml`
  - SHA-256: `8ebdff54716a39c078d306e567afedc5922b05a52e0be26737073e84697fae99`
- `cfd_projects/_release_evidence/vv/baseline-20260824T024845Z-8bb9c5e5b8a1/vv_baseline.json`
  - SHA-256: `c4de138685d1ae55e43a4eb36257c3684a11d9c14375b244d3a2e3d239ab8723`
  - Recomputed test summary: `PASS`, `644` tests, `0` failures, `0` errors, `14` skips.
- `cfd_projects/_release_evidence/vv/baseline-20260824T024845Z-8bb9c5e5b8a1/baseline_evidence.reference.v1.json`
  - SHA-256: `c501a316059cff9924642ae776b35965512aa2ae15588ecadcd601e65cc4c669`
  - Stable path: `_release_evidence/vv/baseline-20260824T024845Z-8bb9c5e5b8a1/vv_baseline.json`
  - Baseline SHA-256: `c4de138685d1ae55e43a4eb36257c3684a11d9c14375b244d3a2e3d239ab8723`
- `cfd_projects/_release_evidence/vv/baseline-20260824T024845Z-8bb9c5e5b8a1/io_acceptance.json`
  - SHA-256: `5df7c98d7cbc2798ce6f9a2caf0a54682451864fc71c068a10cf30516c8d891d`
  - All six configured roots passed read/create/atomic-replace/delete. Status is intentionally `BLOCKED`: artifact probe error `AUTHORITATIVE_CASE_INVENTORY_MISSING` at `_working_validation/evidence/authoritative_case_inventory.v1.json`.

### Files changed in review-fix commit

- `scripts/bootstrap_test_env.ps1`
- `tests/test_dependency_lock.py`
- `tests/test_vv_baseline.py`
- `toolchain.lock.json`
- `toolchain.lock.v1.schema.json`
- `toolchain_lock.py`
- `vv_baseline.py`

### Self-review and concerns

- Reviewed and committed exactly the seven source/schema/test files above; post-commit source/config status is clean. The environment, bootstrap logs, JUnit XML, baseline/reference, and I/O probe are ignored/uncommitted.
- The Windows PowerShell security module cannot auto-load in nested sandbox processes. The bootstrap retains `Get-AuthenticodeSignature` where available and falls back to Windows `WinVerifyTrust` plus the pinned signer thumbprint; focused behavior tests run with zero skips in that context.
- No solver case inventory was created. Test results and exit codes remain validation evidence only; the blocked I/O artifact is not design evidence.
