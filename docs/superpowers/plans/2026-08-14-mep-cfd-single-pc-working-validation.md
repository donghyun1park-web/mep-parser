# MEP CFD Studio Single-PC Working Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** MEP CFD Studio가 현재 Windows PC 한 대에서 GUI 입력부터 직렬 OpenFOAM 계산, 후처리, 보고서, 중단 후 재개까지 작동하고, 작은 exact 문제와 bounded spot-check로 수치 구현을 제한적으로 점검할 수 있음을 재현 가능한 증거로 확인한다.

**Architecture:** 제품 출시와 계산 프로그램 검증을 분리한다. 먼저 `WORKING_SINGLE_PC`로 실제 계산 체인을 검증하고, 이어 `NUMERICAL_SPOTCHECK_PASS_SINGLE_PC`로 exact heat 문제와 최소 scheme/time/mesh 점검을 확인한다. 두 상태 모두 `design_citable=false`, `release_ready=false`다.

**Tech Stack:** Windows 11, Python, pytest, WSL2 Ubuntu-24.04, OpenFOAM v2606, FreeCAD 1.1.1/OCC 7.8.1, serial `buoyantBoussinesqPimpleFoam`, k-omega SST, `blockMesh`/`snappyHexMesh`, JSON Schema, 로컬 웹 GUI.

## Global Constraints

- 검증 대상은 현재 PC와 현재 사용자 계정 하나다.
- 모든 계산은 직렬 실행이다. `MPI_RANK_SPAWN_HANG`은 기록하되 이번 완료 조건에서 제외한다.
- solver exit 0, pytest 통과, JSON의 `PASS` 문자열만으로 완료를 선언하지 않는다. 현재 artifact와 hash를 다시 읽어 판정한다.
- SGI 결과는 기능 검증용 `SCREENING_ONLY`다. 현장 계측이 없으므로 `DESIGN_CITABLE`을 허용하지 않는다.
- 실제 solver 증거가 없는 scheme/time artifact는 계속 `PENDING_SOLVER_EVIDENCE`다.
- 복사, PMV/PPD, IAQ, age-of-air, terminal SKU, 병렬 계산은 범위 밖이다.
- 기존 `2026-08-14-mep-cfd-validation-vv-release.md`는 삭제하지 않고 장기 출시 계획으로 보존한다.
- 모든 PowerShell 명령은 Studio launcher와 같은 `.venv\Scripts\python.exe`를 `$Python`으로 resolve한 뒤 실행한다. bare `python` fallback은 acceptance evidence로 인정하지 않는다.

## 이번 계획에서 제거하는 출시 항목

| 기존 항목 | 처리 |
|---|---|
| 두 대 PC 검증 | 제거 |
| installer, Authenticode, SBOM | 제거 |
| upgrade/rollback/uninstall | 제거 |
| 장애주입 72회 | 수동 resume 1회로 축소 |
| 실제 DXF 3건 | SGI 1건으로 축소 |
| 사용자 UAT 3명 | 제거 |
| blind field validation/U95 | 제거 |
| WORM archive/독립 승인 | 제거 |
| MPI/병렬 성능 | 제거 |
| `release_readiness.v2` | 단일-PC `working_validation.v1`로 대체 |

## 두 단계 완료 정의

### A. WORKING_SINGLE_PC

다음 6개 check가 모두 PASS여야 한다.

1. `code_baseline`: 전체 테스트 실패 0, runtime skip 목록 기록
2. `filesystem_io`: 결과 폴더 I/O와 ACL PASS
3. `serial_environment`: FreeCAD/OCC, OpenFOAM v2606, 현재 64-cell serial acceptance PASS
4. `working_room_e2e`: 작은 working-room E2E PASS
5. `real_dxf_screening`: SGI DXF 1건 GUI screening 완료
6. `restart_integrity`: checkpoint 중단 후 수동 resume 1회 PASS

### B. NUMERICAL_SPOTCHECK_PASS_SINGLE_PC

`WORKING_SINGLE_PC`에 더해 나머지 2개 check가 PASS여야 한다.

1. `exact_heat_verification`: adiabatic heat-box의 analytic mean-temperature 오차 1% 이하
2. `limited_numerical_spotchecks`: working-room의 first/second-order, medium/fine mesh, dt=0.02/0.01 s 차이가 각각 T 0.5 K, U 0.05 m/s 이내

사용자 문구는 상태별로 다음처럼 고정한다.

```text
WORKING_SINGLE_PC:
이 PC에서 직렬 CFD 계산 경로의 동작을 확인했습니다. 제한적 수치 spot-check는 미통과 또는 미평가 상태입니다.

NUMERICAL_SPOTCHECK_PASS_SINGLE_PC:
이 PC에서 직렬 CFD 계산 경로가 작동했고 제한적 수치 spot-check를 통과했습니다. 설계 인용에는 사용할 수 없습니다.

공통 제한:
실험·현장 자료에 대한 물리 모델 정확도, 다른 PC 재현성, 병렬 계산, 제품 출시 준비는 검증되지 않았습니다.
```

---

### Task 1: 단일-PC 판정 계약과 현재 기준선을 만든다

**Files:**

- Create: `working_validation.py`
- Create: `working_validation.v1.schema.json`
- Create: `requirements-working.in` — reuse `requirements.txt` and add `pytest>=9,<10`, `jsonschema>=4.25,<5` for this PC only.
- Create: `tests/test_working_validation.py`
- Modify: `vv_baseline.py` — 실제 Python executable hash, version, architecture와 installed-distribution snapshot hash를 기록한다.
- Modify: `tests/test_vv_baseline.py`
- Modify: `scripts/io_acceptance.py` — probe roots and open/read/hash actual recovered solver/result artifacts.
- Modify: `tests/test_io_acceptance.py`

**Interfaces:**

`requirements-working.in` contents:

```text
-r requirements.txt
pytest>=9,<10
jsonschema>=4.25,<5
```

```python
def evaluate_working_validation(projects_root: Path) -> dict:
    """Recompute evidence and return WORKING, NUMERICAL_SPOTCHECK_PASS, or BLOCKED."""

def write_working_validation(projects_root: Path, output: Path) -> dict:
    """Atomically write the recomputed single-PC manifest."""

def compare_working_validation_runs(first: Path, second: Path) -> dict:
    """Compare canonical status/check/evidence hashes, excluding created_at and output path."""
```

- [ ] **Step 0: Studio가 실제 사용하는 `.venv`에 working-validation dependencies를 설치하고 확인한다.**

```powershell
$Python = (Resolve-Path '.venv\Scripts\python.exe').Path
& $Python -m pip install -r requirements-working.in
& $Python -m pytest --version
& $Python -c "import jsonschema,pytest; print(jsonschema.__version__); print(pytest.__version__)"
```

Do not use the release-only `toolchain.lock.json` as a PASS gate here; it intentionally remains `ready=false`. Instead, `vv_baseline.v1` binds the exact executable SHA/version/architecture and canonical installed-distribution snapshot produced after this step.

- [ ] **Step 1: self-declared PASS와 stale hash를 거부하는 RED 테스트를 작성한다.**

```python
def test_fake_pass_cannot_promote_working_status(tmp_path):
    result = evaluate_working_validation(tmp_path)
    assert result["status"] == "BLOCKED"
    assert result["working_ready_on_target"] is False
    assert result["design_citable"] is False
    assert result["release_ready"] is False
```

- [ ] **Step 2: 테스트를 실행해 모듈 부재 RED를 확인한다.**

Run: `& $Python -B -m pytest -q tests/test_working_validation.py tests/test_vv_baseline.py`

- [ ] **Step 3: 아래 top-level 계약을 최소 구현한다.**

```json
{
  "contract": "working_validation.v1",
  "status": "BLOCKED|WORKING_SINGLE_PC|NUMERICAL_SPOTCHECK_PASS_SINGLE_PC",
  "scope": "single_pc_serial_current_user",
  "working_ready_on_target": false,
  "limited_numerical_spotchecks_pass_on_target": false,
  "design_citable": false,
  "release_ready": false,
  "checks": [],
  "blockers": [],
  "numerical_blockers": [],
  "verification_scope": [],
  "limitations": [],
  "evidence_sha256": {}
}
```

- [ ] **Step 4: 정확히 8개 check를 재계산하는 schema와 validator tests를 통과시킨다.**

Check IDs:

```text
code_baseline
filesystem_io
serial_environment
working_room_e2e
real_dxf_screening
restart_integrity
exact_heat_verification
limited_numerical_spotchecks
```

State transition and booleans are exact and fail-closed:

```text
all 8 checks PASS           -> NUMERICAL_SPOTCHECK_PASS_SINGLE_PC; working=true; spotchecks=true
first 6 checks PASS         -> WORKING_SINGLE_PC; working=true; spotchecks=false
                               last 2 may be PASS/FAIL/BLOCKED/NOT_EVALUATED and are reported in numerical_blockers
any of first 6 non-PASS     -> BLOCKED; working=false; spotchecks=false
design_citable              -> always false
release_ready               -> always false
```

Only the exact authoritative source artifacts named by the 8 checks enter `evidence_sha256`. Exclude `working_validation-run*.json`, the published working-validation JSON/HTML, temporary files and cache files from the evidence inventory so audit run1 cannot perturb run2.

- [ ] **Step 5: 현재 pytest/JUnit와 I/O evidence를 생성한다.**

```powershell
& $Python -c "import platform,sys; print(sys.executable); print(sys.version); print(platform.architecture())"
New-Item -ItemType Directory -Force cfd_projects/_working_validation | Out-Null
& $Python -B -m pytest tests -q --junitxml=cfd_projects/_working_validation/junit.xml
& $Python vv_baseline.py --repo-root . --projects-root cfd_projects --junit cfd_projects/_working_validation/junit.xml --output-root cfd_projects/_working_validation/vv
& $Python scripts/io_acceptance.py --projects-root cfd_projects --output cfd_projects/_working_validation/io_acceptance.json
```

**Completion:** 테스트 실패 0, 모든 skip의 이름과 해제 조건 기록, 6개 결과 root의 read/create/atomic replace/delete PASS. Final I/O acceptance must also open/read/SHA-256 actual `log.checkMesh`, solver log, latest `T/U/phi/V`, mesh/run/result manifests, VTU and HTML from each authoritative case; a permission-denied file is BLOCKED. `run_cfd.bat --check`와 JUnit/GUI가 같은 resolved executable을 사용하며 executable SHA-256, exact version/architecture, installed-distribution snapshot SHA-256이 모두 non-null이다. This initial baseline is provisional: any later code, dependency or capability change invalidates it, and Task 5 must regenerate the authoritative final JUnit/baseline/I/O evidence. 출시용 lock/installer는 요구하지 않는다.

---

### Task 2: 현재 PC의 직렬 계산 환경을 실제 수용한다

**Files:**

- Modify: `cfd_capabilities.py` — FreeCAD discovery와 bounded Boolean/tessellation probe를 분리하고 현재 runtime identity와 timeout reason code를 기록한다.
- Modify: `cfd_studio.py` — HTTP 서버와 브라우저를 먼저 시작하고 FreeCAD/OpenFOAM 진단은 background refresh로 실행한다.
- Modify: `cfd_run.py` — Studio acceptance가 한 번 실행한 solver result에서 runtime baseline을 만들 수 있도록 기존 recorder를 공유한다.
- Create: `scripts/produce_local_usability_acceptance.py`
- Preserve as pure validator: `scripts/local_usability_acceptance.py`
- Create: `local_usability_acceptance.v1.schema.json`
- Test: `tests/test_cfd_capabilities.py`
- Test: `tests/test_studio_workflow.py`
- Test: `tests/test_local_usability_acceptance.py`
- Runtime: `cfd_projects/_system/environment_acceptance/`
- Runtime: `cfd_projects/_working_validation/runtime_capability.v1.json`
- Runtime: `cfd_projects/_working_validation/local_usability_acceptance.json`

**Current evidence on 2026-08-14:**

```text
OpenFOAM v2606 static probe: ready
MPI: MPI_RANK_SPAWN_HANG (ignored)
FreeCAD: headless timeout after 60 s
64-cell acceptance: stale
serial baseline: NOT_RUN
```

**Interface added in `cfd_capabilities.py`:**

```python
def diagnose_freecad_stages(executable: Path, *, per_stage_timeout_s: float) -> dict:
    """Bound discovery, imports, Boolean and tessellation separately and report the failing stage."""
```

**Interfaces added for local usability acceptance:**

```python
def produce_local_usability_acceptance(repo_root: Path, python_executable: Path,
                                       *, runtime, launch_attempts: int = 3) -> dict:
    """Produce, stage, validate, and only then publish current runtime evidence."""

def validate_local_usability_acceptance(manifest_path: Path,
                                        projects_root: Path) -> dict:
    """Purely revalidate the fixed manifest and every hash-bound dependency."""
```

- [x] **Step 1: FreeCAD timeout과 GUI startup 비동기 동작을 재현하는 test를 실행한다.**

Run: `& $Python -B -m pytest -q tests/test_cfd_capabilities.py tests/test_studio_workflow.py`

- [x] **Step 2: 실제 FreeCAD probe가 다음 fields로 PASS하도록 원인을 복구한다.**

Use the currently discovered executable explicitly and an isolated config. Close orphan FreeCAD processes manually before the probe; the program must not kill unrelated user processes.

```powershell
$FreeCADCmd = 'C:\Program Files\FreeCAD 1.1\bin\freecadcmd.exe'
if (-not (Test-Path -LiteralPath $FreeCADCmd)) { throw 'FREECAD_EXECUTABLE_MISSING' }
$env:MEP_CFD_FREECADCMD = $FreeCADCmd
& $Python -c "import json; from pathlib import Path; import cfd_capabilities as c; print(json.dumps(c.diagnose_freecad_stages(Path(r'C:\Program Files\FreeCAD 1.1\bin\freecadcmd.exe'), per_stage_timeout_s=20), ensure_ascii=False, indent=2))"
```

Discovery must finish within 5 s; module imports within 15 s; Boolean and tessellation within 20 s each. A timeout/failure identifies the exact stage and leaves `serial_environment=BLOCKED`. Repair/reinstall of the external FreeCAD 1.1 runtime is then a prerequisite, not something Studio silently bypasses.

```text
freecad.ok=true
freecad.status=ready
freecad.compatible_profile=freecad-1.1.1-occ-7.8.1
Boolean smoke=PASS
tessellation smoke=PASS
```

- [x] **Step 3: Studio의 환경 사용 시험으로 64-cell case를 새로 실행한다.**

Required:

```text
acceptance.ok=true
acceptance.status=PASS
cells=64, independently parsed from checkMesh/polyMesh rather than copied from the requested config
mesh_ok=true
latest_time>0
solver log exists
HTML report exists
accepted OpenFOAM profile equals current profile
```

- [x] **Step 4: 같은 한 번의 64-cell 실행에서 report, acceptance와 serial runtime evidence를 함께 원자 발행한다.**

Do not invoke `cfd_run.py --once` a second time. `_do_environment_acceptance()` must retain the actual `run_until_closed()` result, derive runtime baseline from that result, and publish report/acceptance/runtime-capability records with one run ID and matching case/log hashes. Required non-null fields are runner wall seconds, solver clock seconds, peak RSS, case input SHA-256 and solver log SHA-256.

- [x] **Step 5: 현재 PC에서 startup과 핵심 한국어 오류 5종을 검증한다.**

Run three fresh Studio launches. Record process-start, HTTP-ready and required first-page DOM-marker timestamps. Each attempt must be ready within 10 s; after measuring readiness, wait for the bounded background diagnostics to settle and persist before clean shutdown. The actual SGI GUI session in Task 5 supplies the interactive browser proof. For WSL, FreeCAD, invalid geometry, mesh, and solver/disk failures, require a stable diagnostic code, plain-Korean cause, impact, exact next action and log path; raw traceback/path dumps shown to the novice must be zero.

```powershell
& $Python -B scripts/produce_local_usability_acceptance.py --repo-root . --python-executable $Python --launch-attempts 3 --output cfd_projects/_working_validation/local_usability_acceptance.json
& $Python -B scripts/local_usability_acceptance.py --projects-root cfd_projects --manifest cfd_projects/_working_validation/local_usability_acceptance.json --output cfd_projects/_working_validation/evaluations/serial-environment-evaluation.json
```

`serial_environment` must revalidate this JSON, its Python/capability identity and all referenced evidence hashes. Missing, stale, malformed, fewer than 3 launches or fewer than 5 diagnostic rows is BLOCKED.

- [x] **Step 6: 환경 회귀를 통과시킨다.**

Run: `& $Python -B -m pytest -q tests/test_cfd_capabilities.py tests/test_cfd_mpi_smoke.py tests/test_studio_workflow.py`

**Completion:** `body_fitted_runtime_ready=true`, `body_fitted_engine_ready=true`, current 64-cell acceptance PASS, serial baseline PASS, startup 3/3 각각 10 s 이하, 한국어 오류 5/5 actionable, fatal/raw-traceback 0. MPI는 BLOCKED여도 된다.

**2026-08-27 completion evidence:** 별도 producer가 합성 64-cell case와 FreeCAD/Studio/OpenFOAM 증거를 임시 후보에 생성하고 순수 validator PASS 후 manifest-last로 게시했다. 게시된 canonical manifest의 독립 재검증도 blockers 0으로 PASS했다. 집중 회귀는 locked Python 3.12.10에서 `289 passed, 7 skipped, 7 warnings`다. 공개 exact-code commit `9e625e2dfd6c05b03e0d6efdffbcbf6b8fc5cb35`의 Windows CI [run `33028118325`](https://github.com/donghyun1park-web/mep-parser/actions/runs/33028118325)는 `1194 passed, 14 skipped, 7 warnings`로 성공했다. skips는 이 serial Step 밖의 runtime-gated 항목이며 MPI execution smoke는 `NOT_RUN`이다.

---

### Task 3: 작은 working-room으로 결정론적 E2E를 검증한다

**Files:**

- Create: `cfd_working_room.py`
- Create: `working_room_acceptance.v1.schema.json`
- Create: `tests/test_cfd_working_room.py`
- Modify: `cfd_physics.py` — production default는 adaptive-Co로 유지하고 `single_pc_numerical_spotcheck` scope에만 fail-closed fixed-Δt controlDict를 생성한다.
- Modify: `tests/test_cfd_physics.py`
- Reuse: `cfd_occ.py`, `cfd_mesh.py`, `cfd_post.py`
- Runtime: `cfd_projects/_working_validation/working-room-v1/`

**Interfaces:**

```python
def build_working_room_geometry() -> dict:
    """Return the canonical 2 m cube geometry.v2 input."""

def run_working_room(projects_root: Path, *, case_id: str, progress_cb=None) -> dict:
    """Run one immutable anchor or repeat child through OCC, mesh, thermal and report."""

def validate_working_room(case_root: Path) -> dict:
    """Rehash artifacts and recompute mass, energy and numerical evidence."""
```

Canonical input:

```text
inside size: 2.0 x 2.0 x 2.0 m
supply: 0.25 x 0.25 m, 360 CMH, 293.15 K
exhaust: 0.25 x 0.25 m, pressure outlet, design target 360 CMH
confirmed convective heat: 1.0 kW
walls: adiabatic
solver: serial buoyantBoussinesqPimpleFoam, kOmegaSST
mesh target: 0.125 m
numerics: design_limited_second_order_v1
time control: adjustTimeStep=no, fixed deltaT=0.02 s; observed peak Co must remain <=1.0
flow-through time: 80 s
target: 3.0 FTT = 240 s
```

- [ ] **Step 1: geometry, terminal identity, airflow, heat evidence 중 하나라도 빠지면 거부되는 RED tests를 작성한다.**

- [ ] **Step 2: canonical geometry builder와 schema-valid input을 구현한다.**

- [ ] **Step 3: mocked OCC→mesh→thermal→postprocess stage/hash tests를 통과시킨다.**

Add negative tests proving that normal Studio/field cases still emit `adjustTimeStep yes`, fixed-Δt requires `validation_scope=single_pc_numerical_spotcheck`, and a caller cannot enable the validation mode through ordinary project settings.

Run: `& $Python -B -m pytest -q tests/test_cfd_working_room.py`

- [ ] **Step 4: 현재 PC에서 actual serial E2E를 실행한다.**

```powershell
& $Python cfd_working_room.py --projects-root cfd_projects --case-id anchor --run
```

Required:

```text
single watertight air volume
checkMesh PASS, illegal cells 0
no fatal solver error
physical time >= 240 s
peak Co <= 1.0
global/terminal phi imbalance <= 0.1%
modeled-convective energy closure 95%..105%
finite T/U VTU
summary, x/y/z slices and HTML report exist
```

The same run may become the Task 4 anchor only when these additional numerical-candidate checks pass:

```text
opening applied-area error <= 3%
tail residuals, at least 5 samples: U/p_rgh/k/omega <= 1e-4, T <= 1e-5
global continuity <= 1e-6
beta * max(|T-Tref|) <= 0.1
direct OpenFOAM yPlus evidence exists
acceptable wall-treatment area ratio >= 0.80
```

If these additional checks fail, the functional E2E evidence remains available but Task 4 is `BLOCKED`; it is never silently promoted by the report.

- [ ] **Step 5: 동일 seed clean rerun 1회를 수행해 재현성을 확인한다.**

```powershell
& $Python cfd_working_room.py --projects-root cfd_projects --case-id repeat --run
& $Python cfd_working_room.py --projects-root cfd_projects --compare anchor repeat --output cfd_projects/_working_validation/working-room-v1/working_room_acceptance.json
```

`anchor/` and `repeat/` are separate immutable child directories. The comparison manifest stores `authoritative_case_path`, `authoritative_case_sha256`, `repeat_case_path` and `repeat_case_sha256`; neither run may overwrite the other.

```text
mesh/source/input hashes: exact match
mean temperature difference <= 0.02 K
mean speed difference <= 0.005 m/s
energy closure difference <= 0.5 percentage point
```

**Completion:** working-room E2E와 clean repeat가 PASS하면 `working_room_e2e` check만 PASS로 기록한다. Persistent job restart는 Task 5의 실제 Studio field job에서 검증한다. 아직 numerical spot-check 상태로 승격하지 않는다.

---

### Task 4: 최소 exact·scheme·time·mesh 검증을 수행한다

**Files:**

- Create: `cfd_verification.py`
- Create: `verification_manifest.v1.schema.json`
- Create: `cfd_numerical_spotcheck.py`
- Create: `numerical_spotcheck.v1.schema.json`
- Create: `tests/test_cfd_verification.py`
- Create: `tests/test_cfd_numerical_spotcheck.py`
- Modify: `cfd_physics.py` — add a closed/no-terminal domain only for `validation_scope=single_pc_adiabatic_heat_box`; ordinary Studio cases still require supply and exhaust.
- Modify: `tests/test_cfd_physics.py`
- Modify: `tests/test_cfd_post.py`
- Reuse: immutable seed/tree/hash helpers from `cfd_numerical_sensitivity_job.py`
- Reuse: fixed-Δt contract checks from `cfd_temporal_sensitivity.py`
- Runtime: `cfd_projects/_working_validation/heat-box-v1/`
- Runtime: `cfd_projects/_working_validation/numerical-spotcheck-v1/`

**Interfaces:**

```python
def build_adiabatic_heat_box(case_root: Path, *, cell_size_m: float,
                             delta_t_s: float) -> dict:
    """Build a closed constant-property 2 m cube with 800 W heat for 60 s."""

def evaluate_heat_box(case_root: Path) -> dict:
    """Compare volume-mean temperature rise with Q*t/(rho*cp*V)."""

def evaluate_two_level_spotcheck(reference_case: Path,
                                 comparison_case: Path,
                                 variation: str) -> dict:
    """Recompute QoIs and compare scheme, dt, or mesh variants."""

def prepare_numerical_spotcheck(working_room_acceptance: Path, study_root: Path) -> dict:
    """Resolve the immutable authoritative anchor and materialize three bounded variants."""

def run_numerical_spotcheck(study_root: Path, progress_cb=None) -> dict:
    """Run all pending children serially and atomically publish verified evidence."""
```

Only four working-room numerical states are required. The second-order fixed-`dt=0.02 s` reference case from Task 3 is reused. All four emit `adjustTimeStep no`; the verifier reconstructs every physical-time increment from the solver log and rejects controller intervention or a non-fixed history.

Occupied-zone temperature and speed QoIs use one immutable `occupied_volume_band.v1` selector. The spotcheck manifest records the selector SHA-256 beside the working-room geometry and zone SHA-256 values; exhaust temperature rise is recomputed from the exhaust patch rather than this selector.

```text
contract: occupied_volume_band.v1
coordinate_source: cell_center_m_agl
xy_bounds_m: {x_min_m: 0.10, x_max_m: 1.90, y_min_m: 0.10, y_max_m: 1.90}
z_min_agl_m: 0.10
z_max_agl_m: 1.80
```

| State | Mesh | Scheme | dt |
|---|---|---|---:|
| Anchor | 0.125 m | second order | 0.02 s |
| Scheme comparison | 0.125 m | first order | 0.02 s |
| Time comparison | 0.125 m | second order | 0.01 s |
| Mesh comparison | 0.177 m | second order | 0.02 s |

- [ ] **Step 1: synthetic volume weighting, p95, phi, storage integration RED tests를 작성한다.**

Acceptance for hand-constructed arrays: relative error `<=1e-9`.

- [ ] **Step 2: altered power/volume/time/hash를 거부하는 heat-box RED tests를 작성한다.**

Also write RED tests proving that an ordinary body-fitted case without supply/exhaust is still rejected, an unknown caller cannot set the validation scope, and only the exact heat-box builder may request closed-wall BCs plus an explicit pressure reference.

- [ ] **Step 3: heat-box builder/evaluator를 구현하고 actual serial case 1회를 실행한다.**

The builder must call the production `cfd_physics` source/BC/dictionary generator and the same `cfd_run` serial solver path used by Studio. In the bounded heat-box scope, that generator emits closed no-slip/adiabatic walls, no terminal patches, an explicit pressure reference and the normal production 800 W heat-source `fvOptions`. A hand-authored surrogate case is not acceptable. Record semantic and file hashes for `controlDict`, `fvSchemes`, `fvSolution`, heat-source input and solver executable identity.

Run: `& $Python cfd_verification.py heat-box --projects-root cfd_projects --run --output cfd_projects/_working_validation/heat-box-v1/verification_manifest.json`

Reference equation:

```python
analytic_delta_temperature_k = applied_power_w * physical_time_s / (
    rho_kg_m3 * cp_j_kg_k * volume_m3
)
simulated_delta_temperature_k = (
    volume_mean_temperature_final_k - volume_mean_temperature_initial_k
)
storage_energy_closure_ratio = (
    rho_kg_m3 * cp_j_kg_k
    * sum(cell_volume_m3 * (cell_temperature_final_k - cell_temperature_initial_k))
    / (applied_power_w * physical_time_s)
)
```

Heat-box acceptance:

The closed box has no exhaust, so do not reuse the normal exhaust/applied-power closure metric. Recompute transient stored energy from all cells as follows.

```text
cell target = 0.125 m, initial temperature = 293.15 K, physical time = 60 s
adjustTimeStep=no, fixed deltaT=0.02 s, observed peak Co <=1.0
rho and cp are read from the frozen thermal input, not hard-coded in the evaluator
mean-temperature relative error <= 1.0%
storage_energy_closure_ratio 0.99..1.01
global continuity <= 1e-6
absolute net boundary volume flux <= 1e-9 m3/s
beta * max(|T-Tref|) <= 0.1
all source/mesh/run/result hashes current
```

- [ ] **Step 4: forged PASS, wrong anchor, changed physical input, missing result를 거부하는 spotcheck RED tests를 작성한다.**

- [ ] **Step 5: three comparison states를 직렬로 실행하고 current files에서 QoIs를 다시 계산한다.**

Run: `& $Python cfd_numerical_spotcheck.py --acceptance-manifest cfd_projects/_working_validation/working-room-v1/working_room_acceptance.json --study-root cfd_projects/_working_validation/numerical-spotcheck-v1 --run`

The runner must use only `authoritative_case_path` whose current tree hash equals `authoritative_case_sha256`; it must reject parent-directory scans, `latest` selection and the repeat child as an anchor.

Before any solve, freeze one common geometry, terminal/heat contract, initial fields and occupied selector. Each child must change exactly one declared variable: scheme, fixed `deltaT`, or mesh. Reject extra changes to supply temperature/flow, heat, material properties, initial fields, end time or selector. Scheme/mesh children use fixed `0.02 s`; only the time child uses fixed `0.01 s`. All comparison QoIs are time-weighted volume- or patch-weighted averages over the same final `0.1 FTT` window after every case reaches `3.0 FTT`. Require at least 5 snapshots. Compare time-weighted first-half and second-half values with these exact drift normalizers: occupied temperature rise uses `max(abs(full_window_T_rise), 1 K)`, occupied speed uses `max(abs(full_window_speed), 0.05 m/s)`, and exhaust temperature rise uses `max(abs(full_window_exhaust_T_rise), 1 K)`; every normalized drift must be <=2%.

- [ ] **Step 6: 다음 절대차 기준을 적용한다.**

```text
scheme: |delta mean T| <= 0.5 K, |delta mean speed| <= 0.05 m/s
time:   |delta mean T| <= 0.5 K, |delta mean speed| <= 0.05 m/s
mesh:   |delta mean T| <= 0.5 K, |delta mean speed| <= 0.05 m/s
speed comparisons also require |delta U| / max(|U_reference|, 0.05 m/s) <= 10%
all cases: exhaust delta-T difference <= 0.5 K
all cases: terminal phi imbalance <= 0.1%
all cases: energy closure 95%..105%, peak Co <= 1.0
each case independently recomputes and passes its own residual-tail, continuity, beta*deltaT, direct-yPlus and wall-treatment gates; anchor evidence is never copied to a variant
all cases: geometry, selector, mesh, physical-input, run and result hashes are current
mesh pair: effective h ratio >= 1.25, identical terminal source IDs/patch topology, applied opening-area error <= 3%, actual imposed supply flow error <= 1%
selector SHA-256 + geometry SHA-256 + zone SHA-256 tuple exactly matches the frozen spotcheck manifest
```

This is a two-level engineering spot check, not Richardson uncertainty or formal GCI. The report must say so.

Task 5a의 6-decimal sample fingerprint는 exact/near-copy와 미세 float nonce를 막는 bounded heuristic이다. 이것만으로 독립 solver 실행을 증명하지 않으며, Task 5b producer가 case-local raw `T/U/phi/yPlus`와 실행 provenance를 hash-bind하고 evaluator가 현재 파일에서 재계산해야 위 표의 “anchor evidence is never copied” 운영 요건을 충족한 것으로 판정한다.

**2026-08-25 code-only status:** Master Task 5a의 여섯 code-owned validator, producer schema, 고정 manifest dispatch, 최종 evidence rehash, output-authority guard, synthetic/tamper tests는 구현 및 독립 `CLEAN` 검토를 마쳤다. Focused 분할 결과는 `421 passed, 7 skipped, 9 subtests passed`다. 아래 actual solve/GUI/SGI 실행 checkbox는 이 코드 완료로 닫지 않는다. 로컬 전체 suite의 Python 3.14 실행은 pinned 3.12.10 executable SHA 불일치 때문에 `1177 passed, 2 failed, 14 skipped`로 green이 아니다. Exact code commit `f3b7109386195ae665bd216cb689c686f23dea99`의 public pinned Windows CI [run 32822053903](https://github.com/donghyun1park-web/mep-parser/actions/runs/32822053903)는 1,193 tests, failures/errors 0, skipped 14로 성공해 code-only full-suite gate를 닫았다. 따라서 현재 상태는 Task 5a code-complete일 뿐 `WORKING_SINGLE_PC`, `NUMERICAL_SPOTCHECK_PASS_SINGLE_PC`, design-citable 또는 release-ready 증거가 아니다.

- [ ] **Step 7: focused tests를 통과시킨다.**

Run: `& $Python -B -m pytest -q tests/test_cfd_verification.py tests/test_cfd_numerical_spotcheck.py tests/test_cfd_post.py tests/test_cfd_energy_balance.py tests/test_cfd_numerics.py`

**Completion:** heat-box PASS와 세 spot checks PASS이면 `NUMERICAL_SPOTCHECK_PASS_SINGLE_PC` 후보가 된다. `verification_scope`에는 `adiabatic_heat_box_energy_accounting`과 `two_level_scheme_time_mesh_spotchecks`만 기록한다. Momentum/pressure exact solution, Richardson uncertainty, formal GCI와 external physical validation은 장기 검증용으로 `NOT_EVALUATED`를 유지한다.

---

### Task 5: SGI DXF 1건을 screening으로 실행하고 최종 판정한다

**Files:**

- Reuse: `cfd_projects/_imports/한국 SGI로비_c40b9ae8.dxf`
- Modify: `cfd_studio.py` — SGI GUI flow와 badge가 authoritative working-validation/result-gate 상태만 표시하도록 연결한다.
- Modify: `working_validation.py`
- Modify: `cfd_report.py`
- Test: `tests/test_studio_workflow.py`
- Test: `tests/test_field_pipeline_job.py`
- Test: `tests/test_working_validation.py`
- Modify: `tests/test_cfd_case_cache.py`
- Runtime: `cfd_projects/_working_validation/sgi-screening-v1/`
- Runtime: `cfd_projects/_working_validation/sgi-screening-v1/sgi_screening_acceptance.json` — pointer/hash manifest for the actual `_field_jobs` and `_body_*` artifacts; do not copy them.
- Runtime: `cfd_projects/_working_validation/working_validation.json`
- Runtime: `cfd_projects/_working_validation/working_validation.html`

- [ ] **Step 1: 기존 `review.ready=false` geometry를 재사용하지 않고 GUI에서 새 검토본을 저장한다.**

Required reviewed input:

```text
closed A-ELE04 zone: 1
supply terminals: 15
exhaust terminals: 15
design airflow per terminal: 444 CMH
total supply/exhaust difference <= 1%
terminal normals and roles confirmed
scenario_authority: site_schedule or non_authoritative_working_fixture
when a site schedule is unavailable, define an explicit 15.5 kW convective-only working fixture in the GUI with server-owned manual source IDs/locations; label it non-authoritative and never attribute it to the DXF
validation_fixture_only=true when the working fixture is used
review.ready=true
body-fitted blockers=0
```

- [ ] **Step 2: opening and resource preflight를 실행하고 최종 applied-area error가 3% 이내가 될 때까지 mesh/refinement를 정상 경로로 수정한다.**

Do not edit `jet_metrics_citable` or preflight JSON by hand. If the area gate cannot pass within the bounded mesh preset, leave `real_dxf_screening` BLOCKED and record the opening limitation. Record available RAM/free disk and the mesh/runtime estimate before starting; require estimated peak RAM <=80% of available RAM and free disk >=1.25 times estimated output. Otherwise stop before solver launch.

- [ ] **Step 3: GUI field flow로 OCC→detailed mesh→serial thermal→postprocess→report를 실행한다.**

Use the existing field pipeline target of at least `3.0 FTT`; do not add a shorter validation-only solver mode. The result still remains `SCREENING_ONLY` or `NOT_EVALUATED` because field measurement and physical benchmark validation are outside this plan.

- [ ] **Step 4: 첫 verified thermal checkpoint 뒤 Studio process를 한 번 종료하고 같은 GUI에서 수동 resume한다.**

Acceptance:

```text
field job attempts increases by 1
previous checkpoint physical time does not decrease
final physical time advances to >=3.0 FTT
duplicate Windows/WSL solver count = 0
active/conflicting WSL job is never relaunched
geometry/input hashes do not change
partial or corrupt result is never accepted as complete
```

- [ ] **Step 5: SGI artifact chain을 현재 파일에서 재검증한다.**

Required:

```text
no fatal solver error
geometry/surface/mesh/run/result hashes current
peak Co <= 1.0 and global continuity <= 1e-6
mass imbalance <= 0.1%
terminal supply/exhaust phi imbalance <= 0.1%
applied opening-area error <= 3%
actual imposed supply-flow error <= 1%
energy closure 95%..105%
runner wall time, solver clock, peak RSS and output bytes are finite/non-null
actual recorded peak RSS <= 80% of available RAM
finite T/U VTU and x/y/z slices
HTML report exists
report status is SCREENING_ONLY or NOT_EVALUATED, never DESIGN_CITABLE
```

The authoritative evaluator bypasses `cfd_case_cache` and recomputes VTU QoIs and hashes. Then run one cold and one warm dashboard summary read; values/QoIs must match exactly. A cache hit may improve display time but can never satisfy a working check by itself. The HTML embeds the current published `working_validation.json` SHA-256.

When this artifact gate and Step 4 recovery both pass, record `real_dxf_screening=PASS` and `restart_integrity=PASS`; otherwise the first-six working gate remains BLOCKED.

- [ ] **Step 6: Studio와 HTML에 단일-PC 검증 배지와 제한사항을 표시한다.**

Only this condition may show `제한적 수치 spot-check 통과 · 설계 인용 불가`:

```python
working_validation["status"] == "NUMERICAL_SPOTCHECK_PASS_SINGLE_PC"
```

When status is `WORKING_SINGLE_PC`, show `단일 PC 직렬 계산 경로 동작 확인 · 수치 spot-check 미통과/미평가 · 설계 인용 불가`. When status is `BLOCKED`, show `단일 PC 동작 검증 미완료` and the recomputed working blockers. Never derive these labels from raw `run.design_ready`.

- [ ] **Step 7: 전체 회귀와 최종 evaluator를 실행한다.**

```powershell
& $Python -B -m pytest -q tests --junitxml=cfd_projects/_working_validation/junit-final.xml
& $Python -B -m py_compile working_validation.py cfd_working_room.py cfd_verification.py cfd_numerical_spotcheck.py
& $Python -B scripts/produce_local_usability_acceptance.py --repo-root . --python-executable $Python --launch-attempts 3 --output cfd_projects/_working_validation/local_usability_acceptance.json
& $Python -B scripts/local_usability_acceptance.py --projects-root cfd_projects --manifest cfd_projects/_working_validation/local_usability_acceptance.json --output cfd_projects/_working_validation/evaluations/serial-environment-evaluation.json
& $Python vv_baseline.py --repo-root . --projects-root cfd_projects --junit cfd_projects/_working_validation/junit-final.xml --output cfd_projects/_working_validation/vv-final.json
$WorkingRoom = Get-Content cfd_projects/_working_validation/working-room-v1/working_room_acceptance.json -Raw -Encoding UTF8 | ConvertFrom-Json
$Sgi = Get-Content cfd_projects/_working_validation/sgi-screening-v1/sgi_screening_acceptance.json -Raw -Encoding UTF8 | ConvertFrom-Json
& $Python scripts/io_acceptance.py --projects-root cfd_projects --artifact-root cfd_projects/_system/environment_acceptance --artifact-root $WorkingRoom.authoritative_case_path --artifact-root $Sgi.solver_case_path --output cfd_projects/_working_validation/io-acceptance-final.json
& $Python working_validation.py --projects-root cfd_projects --output cfd_projects/_working_validation/working_validation-run1.json
& $Python working_validation.py --projects-root cfd_projects --output cfd_projects/_working_validation/working_validation-run2.json
& $Python working_validation.py --compare cfd_projects/_working_validation/working_validation-run1.json cfd_projects/_working_validation/working_validation-run2.json --publish-json cfd_projects/_working_validation/working_validation.json --publish-html cfd_projects/_working_validation/working_validation.html
```

**Completion:** first 6 checks가 PASS이면 `WORKING_SINGLE_PC`다. all 8 checks가 PASS일 때만 `NUMERICAL_SPOTCHECK_PASS_SINGLE_PC`다. 동일 파일 상태의 run1/run2는 `created_at`과 output path를 제외한 status/check/evidence hash set이 100% 같아야 한다. 두 상태 모두 blockers는 해당 working 상태 기준으로 비어 있어야 하며, 선택적 수치 실패는 `numerical_blockers`에 남긴다. limitations에는 단일 PC·serial-only·외부 benchmark/현장 물리 검증 없음·momentum/pressure exact 미수행·formal GCI 미수행·설계 인용 아님·release 아님이 기록된다.

---

## 실행 순서와 중단 기준

```text
Task 1 baseline/contract
  -> Task 2 current-PC runtime
  -> Task 3 small-room deterministic E2E
       -> Task 5 one SGI screening/recovery -> WORKING_SINGLE_PC
       -> Task 4 minimal numerical verification
Task 4 PASS + Task 5 PASS          -> NUMERICAL_SPOTCHECK_PASS_SINGLE_PC
```

Default execution prioritizes Task 5 after Task 3 to reach `WORKING_SINGLE_PC` first. Task 4 is a separate optional elevation step; its failure never invalidates the already proven working path.

- Task 2가 실패하면 working-room과 SGI 계산을 시작하지 않는다.
- Task 3가 실패하면 numerical spotcheck와 SGI screening을 시작하지 않는다.
- heat-box가 실패하면 scheme/time/mesh 기준을 완화하지 않고 energy accounting부터 수정한다.
- spotcheck가 실패하면 상태는 `WORKING_SINGLE_PC`에 머문다.
- SGI가 실패해도 작은 검증 문제의 evidence를 삭제하지 않는다. SGI 입력/mesh/solver 문제로 분리한다.
- 코드가 바뀌면 영향받는 Task부터 evidence를 다시 생성한다. 두 번째 PC나 release 전체 재실행은 요구하지 않는다.

## 예상 solver 실행량

재사용을 전제로 총 8개 solver case다. SGI resume는 같은 case의 continuation이므로 새 case로 세지 않는다.

| 용도 | 횟수 |
|---|---:|
| 64-cell acceptance | 1 |
| working-room anchor + clean repeat | 2 |
| heat-box | 1 |
| scheme/time/mesh comparisons | 3 |
| SGI screening | 1 |

이 계획이 PASS한 뒤에만 기존 release 계획을 다시 검토한다. 그때 installer, 두 번째 PC, UAT, field measurement, formal GCI, MPI를 각각 별도 프로젝트로 다룬다.
