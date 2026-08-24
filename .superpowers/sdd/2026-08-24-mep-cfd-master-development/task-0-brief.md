### Task 0: 승인된 기준선과 실행 경계를 고정한다

**Files:**

- Existing evidence producer: `vv_baseline.py`
- Existing evaluator: `working_validation.py`
- Existing tests: `tests/test_vv_baseline.py`, `tests/test_working_validation.py`, `tests/test_io_acceptance.py`
- Runtime output: `cfd_projects/_release_evidence/vv_baseline.v1.json`

**Interfaces:**

- Consumes: current git HEAD, dirty path hashes, Python executable, package/schema/benchmark hashes.
- Produces: immutable baseline ID used by every later plan task.

- [ ] **Step 1: 변경 소유권 gate를 통과한다**

  `git status --short`에서 `cfd_studio.py`, `cfd_run.py`, `cfd_report.py`, `cfd_physics.py`와 신규 schema/test의 소유권을 확인한다. 사용자 승인 없이 기존 dirty 파일 전체를 stage하거나 정리하지 않는다. 승인된 baseline commit 또는 승인된 파일 목록이 없으면 이 계획의 코드 작업을 시작하지 않는다.

- [ ] **Step 2: 기준선 tests를 실행한다**

  Run:

  ```powershell
  $Python = (Resolve-Path '.venv\Scripts\python.exe').Path
  & $Python -B -m pytest -q tests/test_vv_baseline.py tests/test_working_validation.py tests/test_io_acceptance.py
  ```

  Expected: failed 0. Runtime-dependent skip은 이유와 해제 조건이 문자열로 기록된다.

- [ ] **Step 3: 전체 test baseline을 JUnit으로 기록한다**

  Run:

  ```powershell
  & $Python -B -m pytest -q tests --junitxml=cfd_projects/_release_evidence/junit-master-plan-baseline.xml
  ```

  Expected: failed 0. 실패가 있으면 새 기능을 시작하지 않고 기존 실패의 원인과 소유권을 먼저 분리한다.

- [ ] **Step 4: baseline evidence를 생성하고 자체 검증한다**

  Run the exact P0.0~P0.2 steps in `docs/superpowers/plans/2026-08-14-mep-cfd-validation-vv-release.md`, including ACL/I/O acceptance and authoritative inventory exclusion of generated reports.

- [ ] **Step 5: 계획 전용 branch/worktree를 만든다**

  기준선 commit이 승인된 후 `codex/case-evidence-review-gate`처럼 작업 단위별 branch를 만든다. dirty working tree 자체에서 large refactor를 시작하지 않는다.

**Gate M0:** tests failed 0, baseline artifact PASS, path ownership 승인, target branch가 없으면 NO-GO.

## Companion requirements P0.0-P0.2

This section is part of the binding Task 0 brief. Finish the incomplete validation bootstrap already present in the repository; do not redesign the product.

### P0.0 exact test bootstrap

- Existing files to modify only as required: `toolchain.lock.json`, `requirements-dev.in`, `requirements-dev.lock`, `scripts/bootstrap_test_env.ps1`, `toolchain_lock.py`, `tests/test_dependency_lock.py`.
- Exact runtime: Python `3.12.10`, x64; pip `25.1.1`.
- The official Python.org 64-bit installer was downloaded and Authenticode-verified. Its SHA-256 is `67B5635E80EA51072B87941312D00EC8927C4DB9BA18938F7AD2D27B328B95FB`.
- Direct packages remain exactly: ezdxf `1.4.2`, jsonschema `4.23.0`, matplotlib `3.9.2`, numpy `2.1.3`, pytest `8.3.5`, shapely `2.0.6`.
- Resolve and hash every required wheel, including transitive dependencies, for CPython 3.12 x64 Windows. `requirements-dev.lock` must install with `--require-hashes`; no sdist or floating version is allowed.
- Set the toolchain contract to ready only after the installer hash and package wheel hashes are real. Add behavior tests that first fail against the current incomplete lock/bootstrap and then pass after implementation. Tests must verify the repository lock is ready, pip is exact, lock hashes are real SHA-256 values, and bootstrap refuses mismatched Python identity before creating an environment.
- `scripts/bootstrap_test_env.ps1` must create `.venv-vv` only from the exact supplied Python, install the exact pip and locked requirements without arbitrary latest resolution, and leave no partial environment after a failed preflight.
- A local exact interpreter is available at `.superpowers/sdd/2026-08-24-mep-cfd-master-development/python-3.12-base/Scripts/python.exe`. It is scratch and must never be committed.
- Expected acceptance:

  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts\bootstrap_test_env.ps1 -PythonExe '.superpowers\sdd\2026-08-24-mep-cfd-master-development\python-3.12-base\Scripts\python.exe'
  .\.venv-vv\Scripts\python.exe -m pytest --version
  ```

  Fresh `.venv-vv` must report Python 3.12.10 x64, pip 25.1.1, pytest 8.3.5, and the bootstrap must exit 0.

### P0.1 baseline evidence

- Existing `vv_baseline.py`, schema, and tests must remain fail-closed.
- After the source/toolchain commit, regenerate the full JUnit and baseline JSON under `cfd_projects/_release_evidence/vv/<candidate_id>/`; failed tests must be zero and every skip must contain a test name, reason, and release condition.
- Do not stage or commit runtime evidence.

### P0.2 I/O acceptance

- Existing `scripts/io_acceptance.py`, schema, and tests must remain read/create/atomic-replace/delete probes confined to their test or selected output roots.
- Execute the acceptance for `_system`, `_body_mesh`, `_body_solver`, `_body_gci`, `_field_jobs`, `_release_evidence`; access denied must be zero and existing file hashes must not change.
- Do not stage or commit runtime evidence.

## Controller-completed setup

- Worktree and branch are already isolated at `codex/case-evidence-review-gate`.
- Approved code-only baseline commit: `130df3729f05e6a1573823b6ccd942c48c497b2b`.
- Focused Task 0 tests: `19 passed`.
- Full baseline: `614 passed, 14 skipped, 91 subtests passed`; JUnit exists at `cfd_projects/_release_evidence/junit-master-plan-baseline.xml`.
- Original dirty checkout must not be read for implementation or modified.

## Commit and report

- Follow strict RED-GREEN-REFACTOR and include exact RED/GREEN output in the report.
- Commit source/config/test changes only with subject `build: lock reproducible validation toolchain`.
- Write the full report to `.superpowers/sdd/2026-08-24-mep-cfd-master-development/task-0-report.md`.

## Controller rulings that supersede conflicting Task 0 prose

- P0.0 bootstrap precedes every authoritative Task 0 pytest/baseline command. Bind `$Python` to `.venv-vv\Scripts\python.exe`; the earlier `.venv` examples and controller setup run are diagnostic baseline facts only, not M0 acceptance.
- The one canonical runtime baseline path is `cfd_projects/_release_evidence/vv/{candidate_id}/vv_baseline.json`, accompanied by its JUnit file. The flat `cfd_projects/_release_evidence/vv_baseline.v1.json` path is superseded.
- The baseline artifact must expose or be referenced by a stable `baseline_evidence_path` and SHA-256 for later Task 13 inventory. Runtime evidence remains ignored and uncommitted.
