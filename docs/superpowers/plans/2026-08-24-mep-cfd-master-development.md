# MEP CFD Studio Product Development Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Plan date:** 2026-08-24  
**Current disposition:** NO-GO for general release; development may proceed only through the gates in this plan.  
**Goal:** MEP CFD Studio를 현재의 강력한 DXF→OCC→OpenFOAM 계산 기반에서, 비전문 기계설비 사용자가 Design·Scenario·Run을 반복 비교하고 결과의 수치·물리·현장 신뢰도를 이해하며 승인할 수 있는 검증형 로컬 CFD 제품으로 발전시킨다.

**Architecture:** 기존 `geometry.v2 → surface_manifest → mesh_manifest → run_manifest → result_manifest` 체인을 변경하지 않고, 상위에 immutable `Design → Scenario → Run → Evidence → Review` 계층을 추가한다. 계산 결과의 과학적 gate는 `cfd_result_gate.py`가 계속 재계산하고, 새 case-health read model이 geometry·BC·mesh·solver·GCI·benchmark·field 상태를 사용자 언어로 결합한다. Comfort, IAQ, radiation/CHT, MPI, surrogate는 각각 독립 계약과 V&V gate를 통과한 뒤에만 제품 주장 범위에 들어간다.

**Tech Stack:** Windows 11, Python 3.11+, pytest, JSON Schema, 로컬 `http.server` 기반 Studio, WSL2 Ubuntu-24.04, OpenFOAM v2606, FreeCAD 1.1.1/OCC 7.8.1, `blockMesh`/`snappyHexMesh`, `simpleFoam`, `pimpleFoam`, `buoyantBoussinesqPimpleFoam`, VTU/HTML reporting.

**Spec:** `exa-results/mep-cfd-benchmark-2026-08-24.md`

**Required companion plans:**

- `docs/superpowers/plans/2026-08-14-mep-cfd-single-pc-working-validation.md` — 현재 PC의 실제 직렬 계산 경로와 제한적 수치 spot-check.
- `docs/superpowers/plans/2026-08-14-mep-cfd-validation-vv-release.md` — 과학적 V&V, frozen RC, 현장 검증, 패키징, UAT의 상세 실행 순서.
- `IMPROVEMENT_PLAN_2026-08.md` — 기존 제한 베타·복사·comfort 계약과 이미 구현된 A0~A5 자산.
- `NEXT_PHASE_PLAN.md` — `geometry.v2`, OCC, body-fitted 파이프라인의 형성 과정과 기존 완료 범위.

## Global Constraints

- 기본 사용자는 CFD 전문가가 아닌 기계설비 담당자다. 정상 업무는 GUI로 완료하고 CLI와 원시 경로는 진단·지원·개발 모드에만 둔다.
- 현재 지원 계산은 serial-only다. `MPI_RANK_SPAWN_HANG`이 해결되고 serial equivalence가 통과하기 전에는 MPI를 기본 선택하거나 출시 성능 주장에 포함하지 않는다.
- `solver exit 0`, JSON의 `PASS`, 초록색 배지, pytest 성공, cache hit는 물리적 타당성 또는 설계 인용의 충분조건이 아니다.
- `PASS`, `FAIL`, `BLOCKED`, `NOT_EVALUATED`를 evidence 상태로 사용하고, `SCREENING_ONLY`, `NOT_EVALUATED`, `DESIGN_CITABLE`을 citation 상태로 별도 유지한다.
- `geometry.v2`, `surface_manifest.v1`, `mesh_manifest.v1`, `run_manifest.v1`, `result_manifest.v1`, thermal/GCI/field/UAT evidence의 기존 provenance와 SHA-256 연결을 약화하지 않는다.
- 자동 DXF 추론은 입력 후보다. 공간, terminal 역할·방향·CMH, 열원 kW·분율·근거, 점유영역은 사용자 검토 전 solver 입력으로 승격하지 않는다.
- 장시간 작업은 공용 solver lock, 입력 지문, 원자적 체크포인트, 재개 이력을 사용한다. 원본 DXF와 기존 결과를 덮어쓰지 않는다.
- 실제 현장 증거는 최소 3.0 FTT, 완전한 energy history, 현재 artifact hash, 실제 OpenFOAM v2606 결과를 요구한다.
- PMV/PPD는 RH·met·clo·MRT·점유영역·적용성 입력이 없으면 `NOT_EVALUATED`다. ACH·온도·풍량만으로 IAQ 또는 ventilation effectiveness를 주장하지 않는다.
- 복사·CHT, MPI, surrogate는 독립 benchmark와 적용범위 검증 전까지 production flag를 fail-closed한다.
- 현재 dirty/untracked 경로는 사용자 자산이다. 실행 전 사용자 승인으로 기준선을 동결하고, `git reset --hard`, `git clean`, whole-file 무단 staging을 금지한다.
- 기존 v1 artifact는 in-place rewrite하지 않는다. 새 metadata는 optional sidecar와 versioned reader/migrator로 도입한다.

---

## 1. 계획서 권위와 문서 간 관계

이 문서는 **전체 제품 로드맵과 단계 순서의 최상위 권위**다. 세부 수치 V&V 명령과 frozen RC 절차는 companion plan이 실행 권위를 갖는다.

| 문서 | 유지 목적 | 이 문서와 충돌할 때 |
|---|---|---|
| `NEXT_PHASE_PLAN.md` | body-fitted 아키텍처와 완료 이력 | 현재 구현 사실의 참고 자료로만 사용한다. |
| `IMPROVEMENT_PLAN_2026-08.md` | A0~A5, radiation/comfort 선행 설계 | 이미 구현된 자산과 확정 threshold는 보존하고, 새 단계 순서는 본 문서를 따른다. |
| `NEXT_SPRINT_2026-08-13.md` | 과거 Sprint C 실측·판단 기록 | 완료 이력으로 보존하며 새로운 백로그 권위로 사용하지 않는다. |
| `2026-08-14-mep-cfd-single-pc-working-validation.md` | 현재 PC L1 evidence | Milestone M1의 필수 실행 subplan이다. |
| `2026-08-14-mep-cfd-validation-vv-release.md` | L3/L4 과학·현장·출시 evidence | Milestone M3와 M7의 필수 실행 subplan이다. |
| 본 문서 | 제품 모델, UX, field calibration, comfort, IAQ, CHT, 가속화의 통합 순서 | 2026-08-24 이후 신규 개발의 roadmap authority다. |

## 2. 현재 기준선과 정직한 완료 수준

현재 checkout에는 다음 기반이 실제 코드로 존재한다.

- ASCII DXF parsing, source handle provenance, `geometry.v2`, 사용자 geometry review.
- FreeCAD/OCC air solid와 multi-region STL, `surface_manifest.v1`.
- `blockMesh → surfaceFeatureExtract → snappyHexMesh → checkMesh`, resource/quality gate, y+ evidence.
- 등온 및 Boussinesq 열부력 case, terminal actual `phi`, energy storage/exhaust balance, restart/continuation.
- 3/4-grid GCI, first/second-order sensitivity 계약, thermal window, result gate와 tamper-resistant provenance.
- field pipeline, 3.0 FTT continuation, checkpoint resume, field evidence, install recovery, UAT, release audit의 코드 기반.
- body-fitted HTML/VTU/slice 결과와 screening/design-citation 상태 분리.

그러나 일반 배포나 설계 인용을 완료로 볼 수 없는 이유는 다음과 같다.

- 현재 branch `codex/cfd-studio-improvements`는 다수 dirty/untracked 경로를 가진 clean RC가 아니다.
- `working_validation.v1`의 `serial_environment` 이후 여섯 validator는 아직 code-owned recomputation으로 닫히지 않았다.
- 실제 현장 DXF 3건, TAB/sensor holdout, 기계설비 사용자 UAT 3명의 current frozen-RC evidence가 없다.
- G2 또는 대체 production-like GCI는 threshold를 낮추지 않고 통과해야 한다.
- PMV/PPD, IAQ/CO2/age-of-air, production radiation/CHT는 현재 제품 주장 범위가 아니다.

## 3. 제품 완료 수준

| Level | 사용자에게 허용되는 표현 | 필수 진입 조건 |
|---|---|---|
| L0 — Code Baseline | “코드 계약 시험 통과” | 전체 test 실패 0, runtime skip 이유, schema/hash baseline |
| L1 — Working Single PC | “이 PC에서 직렬 CFD 경로 동작 확인” | environment, 64-cell, working-room, 실제 DXF screening, resume integrity |
| L2 — Limited Screening Beta | “초기안 비교와 이상 징후 확인” | Case Evidence UX, Design/Scenario, 실제 DXF 3건, install/recovery, UAT, 정직한 report |
| L3 — Validated Design Review | “검증된 적용범위 안의 설계 검토 근거” | scheme/time/GCI, benchmark, field holdout/U95, terminal/model applicability |
| L4 — General Internal Release | “회사 내부 반복 배포 가능” | frozen RC, package/rollback/support/security, UAT, release audit PASS |
| L5 — Extended HVAC Physics | “검증 범위 내 comfort/IAQ/CHT 평가” | 기능별 독립 input contract, benchmark, field validation, report/release gate |

L1 또는 L2를 달성해도 L3 표현을 사용하지 않는다. L5 기능 하나가 PASS해도 다른 L5 기능이나 전체 제품 범위로 일반화하지 않는다.

## 4. 목표 데이터 흐름

```text
DXF
  → geometry.v2                       형상·원본 provenance·검토된 객체
  → design.v1                         geometry revision 고정
  → scenario.v1                       운전·열·점유·날씨·분석 목적
  → case_identity.v1                  Design + Scenario + Run 불변 identity
  → surface_manifest.v1               OCC 공기체적·named surface
  → mesh_manifest.v1                  메시·patch·quality
  → run_manifest.v1                   solver·BC·수치·runtime
  → result_manifest.v1                VTU·slice·QoI
  → case_evidence.v1                  현재 raw artifact 재검증과 hash chain
  → case_health.v1                    사용자용 evidence 상태
  → case_review.v1                    승인/반려/무효 이력
  → report / compare / field / release
```

필수 불변식:

1. Geometry 변경은 새 Design revision이다.
2. 운전조건 변경은 새 Scenario revision이다.
3. mesh·solver·numerical profile·seed 변경은 새 Run이다.
4. Review는 evidence가 아니라 사람의 결정 기록이다. Review가 실패한 evidence를 PASS로 바꾸지 못한다.
5. Release readiness는 product-level이고 case health는 case-level이다. 서로를 대체하지 않는다.
6. Cache는 화면 성능 최적화일 뿐이며 authoritative evaluator는 원본을 다시 읽는다.

## 5. 목표 파일 구조와 책임

### 유지하는 기존 경계

| 파일 | 계속 맡는 책임 |
|---|---|
| `dxf_parser.py`, `geometry_v2.py` | DXF 의미 후보와 geometry contract |
| `cfd_occ.py`, `cfd_occ_worker.py` | FreeCAD/OCC air-volume adapter |
| `cfd_mesh.py` | OpenFOAM mesh 생성·품질 판정 |
| `cfd_physics.py`, `cfd_run.py` | solver case, run, restart, raw physics metrics |
| `cfd_post.py` | VTU/QoI의 결정론적 후처리 |
| `cfd_result_gate.py` | 수치·physics artifact의 authoritative citation gate |
| `cfd_report.py` | self-contained report 생성 |
| `field_pipeline_job.py` | 현장 장시간 workflow와 checkpoint orchestration |
| `working_validation.py`, `release_audit.py` | PC/product-level fail-closed 평가 |
| `cfd_studio.py` | HTTP routing과 기존 호환 UI; 새 domain logic은 서비스 모듈로 이동 |

### 새로 만드는 경계

| 파일 | 단일 책임 |
|---|---|
| `cfd_status_catalog.py` | UI/report/API 상태명·영향·다음 행동의 단일 소스 |
| `cfd_evidence.py` | authoritative artifact를 재검증한 immutable evidence bundle |
| `cfd_case_health.py` | evidence를 사용자용 health read model로 조합 |
| `cfd_review.py` | 대상 hash에 묶인 Design/Case review 기록 |
| `project_model.py` | Design/Scenario/Run repository, revision, legacy link |
| `cfd_templates.py` | HVAC template 검증·적용·semantic diff |
| `cfd_compare.py` | 동일 Design의 Scenario/Run KPI 비교 |
| `cfd_validation_anchor.py` | scheme/time/GCI/field authority가 공유하는 immutable anchor |
| `cfd_measurements.py` | TAB/sensor import, 단위·좌표·시간·calibration metadata |
| `cfd_field_validate.py` | calibration/holdout 분리와 CFD-measurement uncertainty comparison |
| `cfd_comfort.py` | PMV/PPD 입력 검증, 계산, 적용성 gate |
| `cfd_iaq.py` | passive scalar/age-of-air/exposure input와 result 평가 |
| `cfd_support_bundle.py` | 민감정보를 제외한 진단·환경·로그·hash 지원 묶음 |
| `cfd_surrogate.py` | 검증된 dataset의 OOD-aware 추천; authoritative CFD와 분리 |

### 새 schema

`design.v1.schema.json`, `scenario.v1.schema.json`, `case_identity.v1.schema.json`, `case_evidence.v1.schema.json`, `case_health.v1.schema.json`, `case_review.v1.schema.json`, `field_pipeline_job.v2.schema.json`, `hvac_template.v1.schema.json`, `scenario_comparison.v1.schema.json`, `validation_anchor.v1.schema.json`, `benchmark_source.v1.schema.json`, `benchmark_validation.v1.schema.json`, `field_measurement.v1.schema.json`, `field_calibration.v1.schema.json`, `field_validation.v1.schema.json`, `uncertainty_budget.v1.schema.json`, `comfort_manifest.v1.schema.json`, `iaq_input.v1.schema.json`, `iaq_result.v1.schema.json`, `radiation_validation.v1.schema.json`, `surface_thermal_contract.v1.schema.json`, `cht_input.v1.schema.json`, `cht_validation.v1.schema.json`, `mpi_runtime_smoke.v1.schema.json`, `surrogate_model_card.v1.schema.json`.

## 6. 상태 모델

### Evidence 상태

```text
PASS            현재 artifact가 정의된 gate를 통과
FAIL            평가가 실행됐고 정량 또는 계약 기준을 통과하지 못함
BLOCKED         validator, 환경, 필수 입력 또는 현재 artifact가 없음
NOT_EVALUATED   의도적으로 적용범위 밖이거나 선행조건 미충족
```

### Citation 상태

```text
SCREENING_ONLY  경향 비교 가능, 설계 인용 불가
NOT_EVALUATED   결과는 존재할 수 있으나 신뢰도 평가 미완료
DESIGN_CITABLE  정의된 적용범위 안에서 모든 required evidence 통과
```

### 사용자 상태

```text
DRAFT → REVIEW_REQUIRED → READY_TO_RUN → QUEUED → RUNNING
      → RECOVERABLE_INTERRUPTION → ANALYSIS_COMPLETE
      → SCREENING_RESULT | CITATION_BLOCKED | DESIGN_CITABLE
      → INVALIDATED | SUPERSEDED
```

`design_ready=false`는 정상적인 신뢰도 상태다. 빨간 실패 문구만 표시하지 않고 사용할 수 있는 범위, blocker, 정확한 다음 행동을 함께 표시한다.

## 7. 로드맵과 예상 일정

계획 가정은 주 개발자 1명, CFD/V&V 검토자 part-time 1명, Windows/IT 지원 part-time, 현장/TAB 및 UAT 협조자다. 계산·현장 일정은 코드 개발과 병렬 진행한다.

| Milestone | 목표 | 예상 범위 | Exit |
|---|---|---:|---|
| M0 | 기준선·소유권·test bootstrap | 2~5일 | clean 또는 승인된 baseline, 실패 0 |
| M1 | Case Evidence & Review Gate + Working Single PC | 3~5주 | 실제 DXF 1건 SCREENING_ONLY E2E, recomputation health |
| M2 | Design → Scenario → Run + compare/template | 4~6주 | 같은 Design의 2개 Scenario 비교·보고 |
| M3 | Validation anchor, formal V&V, field TAB | 10~16주 | GCI/benchmark/field holdout gate, L3 후보 |
| M4 | Comfort | 3~5주 | reference vectors, complete inputs, conditional report |
| M5 | IAQ | 5~8주 | scalar conservation, benchmark, exposure report |
| M6 | Radiation/CHT | 6~10주 | two-plate + enclosure/CHT benchmark, production fail-closed |
| M7 | Product hardening and general release | M3와 병렬 6~10주 | frozen RC, package/recovery/UAT/support, L4 |
| M8 | MPI and surrogate | 각 4~8주 | rank smoke/equivalence 또는 OOD model-card gate |

M0~M3와 M7을 병렬화한 core 제품 목표는 14~22주가 현실적이다. M4~M6까지 순차 완료하는 extended physics 목표는 7~11개월 범위로 관리한다. 실패한 계산은 일정을 늘릴 수 있지만 threshold를 낮추는 근거가 되지 않는다.

### 의존성 및 병렬화 규칙

```text
M0 baseline
  └─ M1 evidence/review + working PC
       ├─ M2 design/scenario/run
       │    ├─ M3 validation anchor + benchmark + field holdout
       │    ├─ M4 comfort
       │    └─ M5 IAQ ── M6 radiation/CHT
       └─ M7 packaging/support/UAT ── L4 release (M3도 필수)

M8-A MPI: M1 serial truth + M3 frozen QoI가 선행
M8-B surrogate: M3/L3 eligible run inventory와 site/design holdout이 선행
```

- M1의 schema/evidence 작업과 실제 solver runtime은 병렬 가능하지만 M1 gate는 둘 다 완료돼야 닫힌다.
- M2 UI는 M1 branch가 merge된 뒤 시작한다. M3의 현장 협조·센서 설치·benchmark source 확보는 M2 후반부터 병렬 준비할 수 있다.
- M4와 M5는 공통 Scenario/occupancy contract를 재사용하되 서로의 PASS를 요구하지 않는다. M6의 MRT를 comfort에 연결하는 작업만 M4와 M6 양쪽 gate를 요구한다.
- M7의 installer/support/UAT 준비는 M3 계산과 병렬 가능하지만 frozen RC audit은 동일 candidate에 대한 M3 evidence가 있어야 한다.
- 매 2주 계획 점검에서 남은 기간을 재산정한다. Gate 실패, 현장 접근 지연, 승인되지 않은 threshold는 일정 사유이지 우회 승인 사유가 아니다.

---

## 8. Detailed Implementation Tasks

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

### Task 1: 상태 catalog와 Case Evidence schemas를 고정한다

**Files:**

- Create: `cfd_status_catalog.py`
- Create: `case_evidence.v1.schema.json`
- Create: `case_health.v1.schema.json`
- Create: `case_review.v1.schema.json`
- Create: `tests/test_cfd_status_catalog.py`
- Create: `tests/test_case_evidence_schema.py`

**Interfaces:**

- Produces: `status_descriptor(code: str) -> dict[str, str]`.
- Produces: four evidence states and the fixed case-health check IDs.
- Consumed by: `cfd_evidence.py`, `cfd_case_health.py`, `cfd_review.py`, Studio, reports.

- [ ] **Step 1: 상태 단일 소스의 실패 tests를 작성한다**

  ```python
  def test_status_catalog_has_actionable_korean_copy():
      row = status_descriptor("MESH_QUALITY_BLOCKED")
      assert row["status"] == "BLOCKED"
      assert row["impact"]
      assert row["next_action"]

  def test_design_ready_checks_are_fixed_order():
      assert CASE_HEALTH_CHECKS == (
          "geometry_valid", "bc_reviewed", "mesh_checked",
          "solver_converged", "numerics_verified", "grid_verified",
          "benchmark_validated", "field_calibrated", "design_ready",
      )
  ```

- [ ] **Step 2: tests가 import failure로 실패하는지 확인한다**

  Run: `& $Python -B -m pytest -q tests/test_cfd_status_catalog.py tests/test_case_evidence_schema.py`

  Expected: FAIL because module/schema files do not exist.

- [ ] **Step 3: 최소 catalog와 JSON Schemas를 구현한다**

  ```python
  EVIDENCE_STATUSES = ("PASS", "FAIL", "BLOCKED", "NOT_EVALUATED")
  CITATION_STATUSES = ("SCREENING_ONLY", "NOT_EVALUATED", "DESIGN_CITABLE")

  def status_descriptor(code: str) -> dict[str, str]:
      try:
          return STATUS_CATALOG[code].copy()
      except KeyError as exc:
          raise ValueError(f"Unknown status code: {code}") from exc
  ```

  Schema는 `contract`, `created_at`, `case_identity`, `checks`, `artifact_refs`, `status`, `errors`를 필수로 하고 `additionalProperties: false`를 기본으로 한다.

- [ ] **Step 4: schema positive/negative tests를 통과시킨다**

  Missing hash, unknown status, duplicate check ID, root 밖 경로, review target hash 누락을 각각 거부한다.

- [ ] **Step 5: focused tests를 실행한다**

  Run: `& $Python -B -m pytest -q tests/test_cfd_status_catalog.py tests/test_case_evidence_schema.py`

  Expected: PASS.

- [ ] **Step 6: commit한다**

  ```powershell
  git add cfd_status_catalog.py case_evidence.v1.schema.json case_health.v1.schema.json case_review.v1.schema.json tests/test_cfd_status_catalog.py tests/test_case_evidence_schema.py
  git commit -m "feat: define case evidence status contracts"
  ```

### Task 2: authoritative Case Evidence 재계산기를 만든다

**Files:**

- Create: `cfd_evidence.py`
- Create: `tests/test_cfd_evidence.py`
- Modify: `cfd_result_gate.py` only to expose stable public artifact-validation helpers; do not weaken existing gates.
- Test: `tests/test_cfd_result_gate.py`

**Interfaces:**

```python
def build_case_evidence(
    case_dir: Path,
    *,
    projects_root: Path,
    gci_root: Path | None = None,
    field_evidence_path: Path | None = None,
    output_path: Path | None = None,
) -> dict: ...

def validate_case_evidence(
    evidence_path: Path,
    *,
    projects_root: Path,
) -> list[dict[str, str]]: ...
```

- [ ] **Step 1: forged/stale evidence의 실패 tests를 작성한다**

  ```python
  def test_self_declared_pass_is_not_evidence(tmp_path):
      case = make_complete_case(tmp_path)
      forged = case / "case_evidence.json"
      forged.write_text('{"status":"PASS"}', encoding="utf-8")
      result = build_case_evidence(case, projects_root=tmp_path)
      assert result["status"] != "PASS"

  def test_changed_result_manifest_invalidates_evidence(tmp_path):
      evidence = build_complete_evidence(tmp_path)
      mutate_file(Path(evidence["artifact_refs"]["result"]["path"]))
      assert any(x["code"] == "ARTIFACT_HASH_MISMATCH"
                 for x in validate_case_evidence(Path(evidence["path"]), projects_root=tmp_path))
  ```

- [ ] **Step 2: tests가 실패하는지 확인한다**

  Run: `& $Python -B -m pytest -q tests/test_cfd_evidence.py`

  Expected: FAIL because functions do not exist.

- [ ] **Step 3: authoritative artifact inventory를 구현한다**

  현재 `geometry.v2`, surface, mesh, run, thermal input/progress, result, numerical sensitivity, GCI, field evidence를 현재 disk에서 다시 열고 path containment, schema, SHA-256, cross-reference를 검증한다. `case_summary()` cache와 report HTML은 source evidence로 사용하지 않는다.

- [ ] **Step 4: 원자 publish를 구현한다**

  같은 parent의 staging file에 JSON을 쓰고 `os.replace()`한다. output 자체와 generated report는 다음 run의 source inventory에서 제외한다.

- [ ] **Step 5: tamper matrix를 통과시킨다**

  Test missing artifact, symlink/root escape, wrong contract, mismatched current hash, stale GCI case, copied benchmark manifest, modified geometry, caller-authored PASS.

- [ ] **Step 6: focused regression을 실행한다**

  Run:

  ```powershell
  & $Python -B -m pytest -q tests/test_cfd_evidence.py tests/test_cfd_result_gate.py tests/test_field_pipeline_job.py
  ```

  Expected: PASS.

- [ ] **Step 7: commit한다**

  ```powershell
  git add cfd_evidence.py cfd_result_gate.py tests/test_cfd_evidence.py tests/test_cfd_result_gate.py
  git commit -m "feat: recompute immutable case evidence"
  ```

### Task 3: Case Health와 human Review를 분리한다

**Files:**

- Create: `cfd_case_health.py`
- Create: `cfd_review.py`
- Create: `tests/test_cfd_case_health.py`
- Create: `tests/test_cfd_review.py`
- Modify: `field_pipeline_job.py`
- Test: `tests/test_field_pipeline_job.py`

**Interfaces:**

```python
def build_case_health(
    evidence_path: Path,
    *,
    projects_root: Path,
) -> dict: ...

def create_review(
    target_path: Path,
    *,
    reviewer_id: str,
    decision: Literal["APPROVED", "REJECTED"],
    reason: str,
    output_dir: Path,
) -> dict: ...

def validate_review(review_path: Path, *, projects_root: Path) -> list[dict]: ...
```

- [ ] **Step 1: design-ready derivation의 실패 test를 작성한다**

  ```python
  def test_human_approval_cannot_override_failed_mesh(tmp_path):
      evidence = evidence_with(tmp_path, mesh_checked="FAIL")
      review = approved_review(tmp_path, evidence)
      health = build_case_health(Path(evidence["path"]), projects_root=tmp_path)
      assert review["decision"] == "APPROVED"
      assert health["checks"]["design_ready"]["status"] == "FAIL"
  ```

- [ ] **Step 2: health prerequisite table tests를 추가한다**

  `field_calibrated=NOT_EVALUATED`인 screening case는 정상 조회 가능하지만 `DESIGN_CITABLE`로 승격하지 않는다. `grid_verified`가 필요한 목적과 필요하지 않은 목적을 `purpose`로 분리한다.

- [ ] **Step 3: Case Health read model을 구현한다**

  `geometry_valid`, `bc_reviewed`, `mesh_checked`, `solver_converged`, `numerics_verified`, `grid_verified`, `benchmark_validated`, `field_calibrated`를 고정 순서로 만들고, 각 check에 `status`, `reason_codes`, `impact`, `next_actions`, `evidence_refs`를 둔다.

- [ ] **Step 4: Review record를 target hash에 묶는다**

  Review 생성 시 target file의 현재 SHA-256을 저장한다. target 변경 시 review는 `INVALIDATED`이며 자동 이전하지 않는다. supersede는 새 review가 이전 review ID를 참조하는 append-only 방식으로 처리한다.

- [ ] **Step 5: field pipeline의 terminal 상태를 정리한다**

  `analysis_complete_not_citable`을 유지하고, health의 blocker list와 current evidence path/hash를 manifest에 추가한다. 계산 완료가 review 승인 또는 design-ready를 암시하지 않게 한다.

- [ ] **Step 6: focused regression을 실행한다**

  Run:

  ```powershell
  & $Python -B -m pytest -q tests/test_cfd_case_health.py tests/test_cfd_review.py tests/test_field_pipeline_job.py tests/test_cfd_result_gate.py
  ```

  Expected: PASS.

- [ ] **Step 7: commit한다**

  ```powershell
  git add cfd_case_health.py cfd_review.py field_pipeline_job.py tests/test_cfd_case_health.py tests/test_cfd_review.py tests/test_field_pipeline_job.py
  git commit -m "feat: separate case health from human review"
  ```

### Task 4: Studio와 report에 Evidence & Review Gate를 노출한다

**Files:**

- Modify: `cfd_studio.py` around `body_result_payload()`, `StudioHandler.do_GET()`, `StudioHandler.do_POST()`, body results page.
- Modify: `cfd_report.py` in `generate_body_fitted_report()`.
- Modify: `cfd_advice.py` to group actions by input/model/evidence/field.
- Test: `tests/test_studio_workflow.py`
- Test: `tests/test_body_fitted_report.py`
- Test: `tests/test_cfd_advice.py`

**Interfaces:**

- GET `/api/case-health/<case-name>` → current recomputed `case_health.v1`.
- POST `/api/case-review` with `{case, reviewer_id, decision, reason, target_sha256}`.
- Existing `/api/body-results/<case>` remains backward compatible and adds `case_health` and `review_summary` fields.

- [ ] **Step 1: API contract tests를 먼저 작성한다**

  ```python
  def test_case_health_endpoint_never_uses_summary_cache(studio_client, case):
      mutate_authoritative_run_manifest(case)
      response = studio_client.get(f"/api/case-health/{case.name}")
      assert response.json()["checks"]["solver_converged"]["status"] == "BLOCKED"
  ```

- [ ] **Step 2: local-only POST와 stale review tests를 추가한다**

  기존 `_local_post_allowed()` 보호를 그대로 적용한다. 현재 target hash와 다른 승인 요청은 HTTP 409와 `REVIEW_TARGET_CHANGED`를 반환한다.

- [ ] **Step 3: thin endpoint를 구현한다**

  `cfd_studio.py`는 path validation과 HTTP 변환만 담당하고 모든 domain 판단을 새 service modules에 위임한다. 기존 single worker queue, solver lock, UAT mutex를 변경하지 않는다.

- [ ] **Step 4: 신뢰도 카드 UI를 구현한다**

  기본 카드에 사용자용 상태, 사용할 수 있는 범위, blocker, next action을 표시한다. residual/y+/phi/hash는 “근거 보기” 상세에 둔다. `SCREENING_ONLY`와 `CITATION_BLOCKED`는 성공 초록색으로 표시하지 않는다.

- [ ] **Step 5: report watermark와 evidence table을 구현한다**

  `SCREENING_ONLY`는 첫 페이지·상단에 “초기안 비교용·설계 인용 불가”를 고정한다. `DESIGN_CITABLE`만 검증범위, evidence IDs, reviewer를 포함한 설계 검토 문구를 허용한다.

- [ ] **Step 6: regression을 실행한다**

  Run:

  ```powershell
  & $Python -B -m pytest -q tests/test_studio_workflow.py tests/test_body_fitted_report.py tests/test_cfd_advice.py tests/test_cfd_result_gate.py
  ```

  Expected: PASS, 기존 body-result URLs와 response fields 보존.

- [ ] **Step 7: commit한다**

  ```powershell
  git add cfd_studio.py cfd_report.py cfd_advice.py tests/test_studio_workflow.py tests/test_body_fitted_report.py tests/test_cfd_advice.py
  git commit -m "feat: show evidence and review gates in studio"
  ```

### Task 5: WORKING_SINGLE_PC를 실제 code-owned validators로 닫는다

**Files:**

- Modify: `working_validation.py`
- Preserve: `working_validation.v1.schema.json`; the fixed eight-check contract and scientific labels do not change in this milestone.
- Modify: `cfd_capabilities.py`.
- Create: `scripts/local_usability_acceptance.py`, `cfd_working_room.py`, `cfd_verification.py`, `cfd_numerical_spotcheck.py` according to the fixed contracts in the companion plan.
- Test: `tests/test_working_validation.py`, `tests/test_cfd_capabilities.py`, `tests/test_local_usability_acceptance.py`, `tests/test_cfd_working_room.py`, `tests/test_cfd_verification.py`, `tests/test_cfd_numerical_spotcheck.py`.

**Interfaces:**

- `evaluate_working_validation(projects_root: Path) -> dict` remains the public evaluator.
- Each fixed check gets a code-owned validator; `_future_not_implemented()` is no longer used for completed checks.

- [ ] **Step 1: exact companion plan을 실행한다**

  Execute Tasks 2~5 in `docs/superpowers/plans/2026-08-14-mep-cfd-single-pc-working-validation.md` without changing its scientific labels. `WORKING_SINGLE_PC` and `NUMERICAL_SPOTCHECK_PASS_SINGLE_PC` remain non-citable and non-release states.

- [ ] **Step 2: serial environment acceptance를 세 번 실행한다**

  ```powershell
  & $Python scripts/local_usability_acceptance.py --repo-root . --python-executable $Python --launch-attempts 3 --output cfd_projects/_working_validation/local_usability_acceptance.json
  ```

  Expected: body-fitted runtime ready, current 64-cell acceptance, serial baseline, Studio startup 3/3. MPI may remain BLOCKED.

- [ ] **Step 3: working-room anchor/repeat를 실행한다**

  Acceptance: watertight single air volume, `checkMesh` PASS, illegal cells 0, physical time ≥240 s, Co ≤1.0, terminal phi imbalance ≤0.1%, energy closure 95~105%, finite VTU/slices/report. Repeat differences: mean T ≤0.02 K, mean speed ≤0.005 m/s, closure ≤0.5 percentage point.

- [ ] **Step 4: exact heat와 limited spot-check를 실행한다**

  Acceptance: heat-box analytic mean-temperature relative error ≤1%, storage closure 0.99~1.01, Co ≤1.0, global continuity ≤1e-6. Scheme/time/mesh comparison은 formal GCI가 아니라 two-level engineering spot-check로 라벨링한다.

- [ ] **Step 5: 실제 DXF 1건을 GUI만으로 실행하고 resume한다**

  원본 hash, confirmed geometry, OCC, mesh, thermal checkpoint, report, case evidence가 하나의 chain이어야 한다. 첫 verified checkpoint 뒤 Studio를 종료하고 같은 job을 GUI에서 재개한다. CLI/JSON 수동 편집이 필요하면 product defect로 기록한다.

- [ ] **Step 6: 두 번의 working validation을 비교 publish한다**

  ```powershell
  & $Python working_validation.py --projects-root cfd_projects --output cfd_projects/_working_validation/run1.json
  & $Python working_validation.py --projects-root cfd_projects --output cfd_projects/_working_validation/run2.json
  & $Python working_validation.py --compare cfd_projects/_working_validation/run1.json cfd_projects/_working_validation/run2.json --publish-json cfd_projects/_working_validation/working_validation.json --publish-html cfd_projects/_working_validation/working_validation.html
  ```

  Expected: deterministic evidence and no generated-report self-inventory contamination.

- [ ] **Step 7: commit code only; runtime evidence는 release storage에 보존한다**

  Stage exact producer/schema/test paths. 사용자 DXF, solver case, generated evidence는 source commit에 넣지 않는다.

**Gate M1:** Case Health tamper tests, actual GUI E2E, restart integrity, WORKING_SINGLE_PC가 모두 PASS해야 M2로 이동한다.

### Task 6: Design·Scenario·Run contracts와 repository를 만든다

**Files:**

- Create: `design.v1.schema.json`
- Create: `scenario.v1.schema.json`
- Create: `case_identity.v1.schema.json`
- Create: `project_model.py`
- Create: `tests/test_project_model.py`
- Create: `tests/test_design_scenario_contract.py`

**Interfaces:**

```python
def create_design(
    projects_root: Path,
    *,
    geometry_path: Path,
    name: str,
    created_by: str,
) -> dict: ...

def revise_design(
    design_id: str,
    *,
    geometry_path: Path,
    reason: str,
    revised_by: str,
) -> dict: ...

def create_scenario(
    design_revision_path: Path,
    *,
    name: str,
    operating_conditions: dict,
    purpose: Literal["screening", "design_review_candidate", "benchmark", "field_validation"],
) -> dict: ...

def create_case_identity(
    design_path: Path,
    scenario_path: Path,
    *,
    run_id: str,
    solver_profile: str,
    parent_run_id: str | None = None,
) -> dict: ...
```

- [ ] **Step 1: immutability와 revision tests를 작성한다**

  ```python
  def test_geometry_change_creates_new_design_revision(tmp_path):
      d1 = create_design(tmp_path, geometry_path=geometry_a(tmp_path), name="전기실", created_by="u1")
      d2 = revise_design(d1["design_id"], geometry_path=geometry_b(tmp_path), reason="급기 위치 수정", revised_by="u1")
      assert d1["design_id"] == d2["design_id"]
      assert d1["revision_sha256"] != d2["revision_sha256"]

  def test_scenario_cannot_mutate_geometry(tmp_path):
      with pytest.raises(ProjectModelError, match="SCENARIO_GEOMETRY_MUTATION"):
          create_scenario(design_path(tmp_path), name="대안", operating_conditions={"geometry": {}}, purpose="screening")
  ```

- [ ] **Step 2: tests가 실패하는지 확인한다**

  Run: `& $Python -B -m pytest -q tests/test_project_model.py tests/test_design_scenario_contract.py`

  Expected: FAIL because module and schemas do not exist.

- [ ] **Step 3: contracts를 구현한다**

  `Design`은 reviewed `geometry.v2` path/hash와 revision history만 가진다. `Scenario`는 supply/exhaust CMH, supply temperature, heat-source authority, occupancy, weather/exterior assumptions, operating time, mesh/physics intent를 가진다. 실제 solver dictionaries는 Run이 만든다.

- [ ] **Step 4: content-derived IDs와 atomic repository를 구현한다**

  ID prefix는 `design-`, `scenario-`, `run-`으로 고정하고 canonical JSON SHA-256으로 revision을 식별한다. 사용자 표시는 name을 사용하며 name 변경은 identity를 바꾸지 않는다.

- [ ] **Step 5: variation whitelist tests를 추가한다**

  Scenario clone에서 허용되는 변화와 geometry change를 구분한다. terminal role/normal/size 변경은 Design revision, CMH/supply temperature/operating schedule 변경은 Scenario revision으로 판정한다.

- [ ] **Step 6: focused tests를 실행한다**

  Run: `& $Python -B -m pytest -q tests/test_project_model.py tests/test_design_scenario_contract.py tests/test_geometry_v2_contract.py`

  Expected: PASS.

- [ ] **Step 7: commit한다**

  ```powershell
  git add design.v1.schema.json scenario.v1.schema.json case_identity.v1.schema.json project_model.py tests/test_project_model.py tests/test_design_scenario_contract.py
  git commit -m "feat: add immutable design scenario run model"
  ```

### Task 7: 기존 case와 field workflow를 새 identity에 안전하게 연결한다

**Files:**

- Modify: `project_model.py`
- Modify: `field_pipeline_job.py`
- Create: `field_pipeline_job.v2.schema.json`
- Preserve: `field_pipeline_job.v1.schema.json` as a read-only compatibility contract.
- Modify: `cfd_studio.py` case scanning helpers.
- Test: `tests/test_project_model.py`
- Test: `tests/test_field_pipeline_job.py`
- Test: `tests/test_studio_workflow.py`

**Interfaces:**

```python
def import_legacy_case(case_dir: Path, *, projects_root: Path) -> dict: ...
def link_run_identity(case_dir: Path, identity_path: Path) -> dict: ...
def validate_run_identity(case_dir: Path, *, projects_root: Path) -> list[dict]: ...
```

- [ ] **Step 1: non-destructive legacy import tests를 작성한다**

  기존 case 폴더의 file list/hash가 import 전후 동일하고 metadata sidecar만 새로 생기는지 검증한다. provenance가 부족한 case는 `legacy_unlinked`, 조회 가능, scenario comparison 불가, design citation 불가다.

- [ ] **Step 2: run identity mismatch tests를 작성한다**

  Design revision 또는 Scenario hash가 달라지면 field resume을 막고 `RUN_IDENTITY_CHANGED`를 반환한다. 기존 checkpoint와 결과는 삭제하지 않는다.

- [ ] **Step 3: legacy reader와 sidecar writer를 구현한다**

  기존 `cfd_projects/<case>`를 이동하지 않는다. `_project_model/` metadata에서 legacy path를 상대 경로로 참조하고 현재 hash를 저장한다.

- [ ] **Step 4: field pipeline manifest에 identity reference를 추가한다**

  v2에 `case_identity_path`, `case_identity_sha256`, `design_revision_sha256`, `scenario_revision_sha256`를 필수로 추가한다. reader는 schema version을 먼저 판별하고, 이전 v1 문서는 메모리상 `case_identity_status=NOT_LINKED`를 보완해 읽되 원본 파일을 자동 변환하거나 덮어쓰지 않는다.

- [ ] **Step 5: invalidation을 구현한다**

  Design revision 변경 시 이전 Scenario/Run을 삭제하지 않고 `SUPERSEDED_DESIGN_REVISION`으로 표시한다. 같은 Run resume은 frozen identity가 current artifact와 일치할 때만 허용한다.

- [ ] **Step 6: regression을 실행한다**

  Run:

  ```powershell
  & $Python -B -m pytest -q tests/test_project_model.py tests/test_field_pipeline_job.py tests/test_studio_workflow.py
  ```

  Expected: PASS, 기존 field job v1 fixtures도 읽힌다.

- [ ] **Step 7: commit한다**

  ```powershell
  git add project_model.py field_pipeline_job.py field_pipeline_job.v2.schema.json cfd_studio.py tests/test_project_model.py tests/test_field_pipeline_job.py tests/test_studio_workflow.py
  git commit -m "feat: link legacy cases to immutable run identity"
  ```

### Task 8: HVAC templates와 Scenario semantic diff를 만든다

**Files:**

- Create: `hvac_template.v1.schema.json`
- Create: `cfd_templates.py`
- Create: `cfd_templates/hvac/mixing_ventilation.v1.json`
- Create: `cfd_templates/hvac/displacement_ventilation.v1.json`
- Create: `tests/test_cfd_templates.py`
- Modify: `project_model.py`

**Interfaces:**

```python
def load_hvac_template(template_path: Path) -> dict: ...
def apply_hvac_template(template: dict, design: dict, *, user_values: dict) -> dict: ...
def scenario_diff(baseline: dict, candidate: dict) -> list[dict]: ...
```

- [ ] **Step 1: template가 물리값을 추정하지 않는 실패 tests를 작성한다**

  CMH, equipment kW, RH, met, clo, external temperature가 사용자 또는 approved source 없이 채워지면 실패해야 한다. Template은 required roles, validation rules, UI copy, allowed parameters만 제공한다.

- [ ] **Step 2: terminal mapping tests를 작성한다**

  `geometry.v2`의 stable element ID와 role을 사용하고 화면 label이나 patch order에 의존하지 않는다. 빠진 terminal, 중복 terminal, 불균형 supply/exhaust를 blocker로 반환한다.

- [ ] **Step 3: template loader와 apply를 구현한다**

  Built-in templates에는 난류/수치 expert defaults를 직접 노출하지 않는다. physics profile name과 그 적용범위만 선택하고 실제 dictionaries는 `cfd_physics.py`가 생성한다.

- [ ] **Step 4: semantic diff를 구현한다**

  Diff row는 `path`, `baseline`, `candidate`, `unit`, `engineering_effect`, `requires_review`를 가진다. float display rounding과 identity hash calculation을 분리한다.

- [ ] **Step 5: tests를 실행한다**

  Run: `& $Python -B -m pytest -q tests/test_cfd_templates.py tests/test_project_model.py tests/test_heat_source_contract.py`

  Expected: PASS.

- [ ] **Step 6: commit한다**

  ```powershell
  git add hvac_template.v1.schema.json cfd_templates.py cfd_templates/hvac tests/test_cfd_templates.py project_model.py
  git commit -m "feat: add reviewable hvac scenario templates"
  ```

### Task 9: Project/Design/Scenario/Run UI와 비교 보고서를 연결한다

**Files:**

- Create: `cfd_compare.py`
- Create: `scenario_comparison.v1.schema.json`
- Create: `tests/test_cfd_compare.py`
- Create: `tests/test_studio_design_scenario_run.py`
- Modify: `cfd_studio.py`
- Modify: `cfd_report.py`
- Modify: `cfd_advice.py`

**Interfaces:**

- GET `/api/designs`, `/api/designs/<id>`, `/api/scenarios?design=<id>`, `/api/runs?scenario=<id>`.
- POST `/api/designs`, `/api/design-revisions`, `/api/scenarios`, `/api/scenario-clone`, `/api/scenario-runs`.
- GET `/api/scenario-compare?run=<id>&run=<id>`.
- Existing `/`, `/new`, `/field-run`, `/release-readiness`, `/uat` remain valid wrappers or redirects.

```python
def compare_runs(
    run_identity_paths: Sequence[Path],
    *,
    projects_root: Path,
) -> dict: ...
```

- [ ] **Step 1: API backward-compatibility tests를 작성한다**

  기존 `/api/cases`, `/api/body-results/<case>`, `/api/start-field-pipeline-job`가 그대로 동작하고 새 identity가 있을 때만 추가 metadata를 반환하는지 고정한다.

- [ ] **Step 2: comparison eligibility tests를 작성한다**

  같은 Design revision의 2~4개 Run만 기본 비교한다. 다른 geometry, incomplete evidence, incompatible QoI selector는 명확한 blocker를 반환한다.

- [ ] **Step 3: Project/Design/Scenario/Run 화면을 추가한다**

  Design 화면은 원본·2D·3D air volume·terminal/heat-source review·승인 이력을 보여준다. Scenario 화면은 baseline clone, semantic diff, 예상 자원, 기대 citation scope를 보여준다. Run center는 기존 serial queue와 checkpoint를 재사용한다.

- [ ] **Step 4: compare KPI를 제한한다**

  우선 KPI는 volume/time-weighted mean/p95 temperature, occupied p95 speed, actual supply/exhaust flow, energy closure, hotspot location, case-health blockers다. Max value는 mesh-independent evidence 없이는 설계 KPI로 강조하지 않는다.

- [ ] **Step 5: report 3종을 구현한다**

  `screening`, `design-review`, `field-comparison` template을 분리하되 기존 HTML filename/link는 유지한다. Compare report는 입력 차이, 신뢰도 차이, 동일/상이한 evidence scope를 첫 페이지에 표시한다.

- [ ] **Step 6: regression과 rendering smoke를 실행한다**

  Run:

  ```powershell
  & $Python -B -m pytest -q tests/test_cfd_compare.py tests/test_studio_design_scenario_run.py tests/test_studio_workflow.py tests/test_body_fitted_report.py tests/test_cfd_advice.py
  ```

  Expected: PASS. Browser smoke에서 새 Design 하나, Scenario 두 개, 각 Run 결과, compare report에 도달한다.

- [ ] **Step 7: commit한다**

  ```powershell
  git add cfd_compare.py scenario_comparison.v1.schema.json cfd_studio.py cfd_report.py cfd_advice.py tests/test_cfd_compare.py tests/test_studio_design_scenario_run.py tests/test_studio_workflow.py tests/test_body_fitted_report.py tests/test_cfd_advice.py
  git commit -m "feat: add design scenario run comparison workflow"
  ```

**Gate M2:** 같은 reviewed Design revision에서 두 Scenario가 서로 다른 CMH/temperature/heat setting으로 실행되고, 입력 diff·결과 KPI·case health·report를 GUI로 비교해야 한다. Legacy case는 계속 조회 가능해야 한다.

### Task 10: Validation Anchor로 sensitivity·GCI·field authority를 통합한다

**Files:**

- Create: `validation_anchor.v1.schema.json`
- Create: `cfd_validation_anchor.py`
- Create: `tests/test_cfd_validation_anchor.py`
- Modify: `cfd_numerical_sensitivity_job.py`
- Modify: `cfd_temporal_sensitivity.py`
- Modify: `cfd_gci.py`
- Modify: `cfd_result_gate.py`
- Modify: `field_pipeline_job.py`
- Test: `tests/test_cfd_numerical_sensitivity_job.py`, `tests/test_cfd_temporal_sensitivity.py`, `tests/test_cfd_gci.py`, `tests/test_field_pipeline_job.py`.

**Interfaces:**

```python
def create_validation_anchor(
    case_dir: Path,
    *,
    selector_path: Path,
    role: Literal["gci_fine", "temporal_fine", "field_authority"],
    output_path: Path,
) -> dict: ...

def validate_validation_anchor(
    anchor_path: Path,
    *,
    expected_case: Path | None = None,
) -> list[dict]: ...
```

- [ ] **Step 1: circular-gate regression tests를 작성한다**

  GCI candidate를 만들기 위해 design-ready sensitivity를 선요구하거나, sensitivity를 승인하기 위해 final GCI를 선요구하는 cycle을 재현하고 실패로 고정한다.

- [ ] **Step 2: field/fine authority mismatch test를 작성한다**

  Field solver case와 GCI fine case가 path/hash/physical tree가 다르면 citation을 BLOCKED하고, 같은 anchor면 현재 artifact 재검증 후 통과할 수 있게 한다.

- [ ] **Step 3: immutable anchor를 구현한다**

  Anchor는 occupied selector, geometry/surface/mesh/run/result/thermal tree, physical settings, solver identity와 SHA-256을 묶는다. Anchor 자체의 `PASS`를 신뢰하지 않고 각 consumer가 raw artifacts를 다시 읽는다.

- [ ] **Step 4: `GCI_CANDIDATE`와 final citable gate를 분리한다**

  Candidate는 sensitivity study의 authority가 될 수 있지만 design citation을 허용하지 않는다. Final gate는 verified scheme/time sensitivity, GCI, benchmark, applicability를 모두 요구한다.

- [ ] **Step 5: focused tests를 실행한다**

  Run:

  ```powershell
  & $Python -B -m pytest -q tests/test_cfd_validation_anchor.py tests/test_cfd_numerical_sensitivity_job.py tests/test_cfd_temporal_sensitivity.py tests/test_cfd_gci.py tests/test_cfd_result_gate.py tests/test_field_pipeline_job.py
  ```

  Expected: PASS and no authority cycle.

- [ ] **Step 6: companion V&V Plan P1.1~P1.5를 실행한다**

  장시간 GCI를 다시 실행하기 전에 serial sensitivity executor/verifier, temporal contract, field authoritative fine-case 연결을 완료한다.

- [ ] **Step 7: commit한다**

  ```powershell
  git add validation_anchor.v1.schema.json cfd_validation_anchor.py cfd_numerical_sensitivity_job.py cfd_temporal_sensitivity.py cfd_gci.py cfd_result_gate.py field_pipeline_job.py tests/test_cfd_validation_anchor.py tests/test_cfd_numerical_sensitivity_job.py tests/test_cfd_temporal_sensitivity.py tests/test_cfd_gci.py tests/test_cfd_result_gate.py tests/test_field_pipeline_job.py
  git commit -m "feat: unify numerical validation authority"
  ```

### Task 11: Benchmark registry와 model-applicability matrix를 확장한다

**Files:**

- Create: `benchmark_source.v1.schema.json`
- Create: `benchmark_validation.v1.schema.json`
- Create: `cfd_benchmark_case.py`
- Create: `cfd_benchmark_validate.py`
- Create: `tests/test_cfd_benchmark_validate.py`
- Create/extend: `cfd_benchmarks/annex20/`, `cfd_benchmarks/sidewall_jet/`, `cfd_benchmarks/buoyancy/`, `cfd_benchmarks/terminal/`.
- Modify: `cfd_validate.py`, `cfd_result_gate.py`, `cfd_report.py`.

**Interfaces:**

```python
def register_benchmark_source(source_manifest: Path, *, registry_root: Path) -> dict: ...
def evaluate_benchmark(case_dir: Path, source_manifest: Path, *, output_path: Path) -> dict: ...
def benchmark_applicability(case_identity: dict, validation_manifest: dict) -> dict: ...
```

- [ ] **Step 1: source provenance tests를 작성한다**

  Raw numeric data/license, measurement uncertainty, coordinate transform, normalization, comparator version이 없으면 benchmark는 `NOT_EVALUATED`다. Plot image digitization만 있는 경우 그 uncertainty를 기록한다.

- [ ] **Step 2: Annex 20 기존 기준을 regression으로 고정한다**

  Current gate: RMS ≤0.10·U0 and three qualitative jet/return/decay conditions. Legacy와 body-fitted 결과를 같은 validation claim으로 합치지 않는다.

- [ ] **Step 3: benchmark family manifests를 구현한다**

  Sidewall jet, ceiling diffuser/return, buoyancy plume, obstruction, slot mixing, terminal SKU를 family로 나누고 각 QoI·measurement mapping·model limitation을 명시한다.

- [ ] **Step 4: threshold ownership을 분리한다**

  현재 코드에 없는 sidewall/terminal/field 허용오차는 informational output으로 시작한다. CFD/V&V 책임자가 raw data와 measurement uncertainty를 검토해 승인한 값만 versioned source manifest에 기록한다.

- [ ] **Step 5: model-form risk rule을 구현한다**

  Transitional slot jet, 강한 재순환, buoyancy-dominant case에는 alternate approved RANS comparison 또는 expert review를 요구한다. 하나의 난류모델 결과를 일반 정답으로 표현하지 않는다.

- [ ] **Step 6: tests를 실행한다**

  Run: `& $Python -B -m pytest -q tests/test_cfd_benchmark_validate.py tests/test_cfd_result_gate.py tests/test_cfd_physics.py tests/test_cfd_gci.py`

  Expected: PASS.

- [ ] **Step 7: commit한다**

  ```powershell
  git add benchmark_source.v1.schema.json benchmark_validation.v1.schema.json cfd_benchmark_case.py cfd_benchmark_validate.py cfd_benchmarks cfd_validate.py cfd_result_gate.py cfd_report.py tests/test_cfd_benchmark_validate.py
  git commit -m "feat: add benchmark applicability registry"
  ```

### Task 12: TAB·sensor measurement와 calibration/holdout을 구현한다

**Files:**

- Create: `field_measurement.v1.schema.json`
- Create: `field_calibration.v1.schema.json`
- Create: `field_validation.v1.schema.json`
- Create: `uncertainty_budget.v1.schema.json`
- Create: `cfd_measurements.py`
- Create: `cfd_field_validate.py`
- Create: `tests/test_cfd_measurements.py`
- Create: `tests/test_cfd_field_validation.py`
- Modify: `field_acceptance.py`, `cfd_post.py`, `cfd_report.py`, `cfd_case_health.py`, `cfd_studio.py`.

**Interfaces:**

```python
def import_measurements(
    csv_path: Path,
    *,
    mapping: dict,
    source_metadata: dict,
    output_path: Path,
) -> dict: ...

def compare_field_measurements(
    case_dir: Path,
    measurement_manifest: Path,
    *,
    calibration_ids: Sequence[str],
    holdout_ids: Sequence[str],
    uncertainty_budget: Path,
) -> dict: ...
```

- [ ] **Step 1: unit/coordinate/time failure tests를 작성한다**

  Unknown unit, out-of-room coordinate, missing AGL height, timezone/time-window mismatch, instrument certificate 누락, duplicate point ID를 거부한다.

- [ ] **Step 2: calibration leakage tests를 작성한다**

  `calibration_ids ∩ holdout_ids`가 비어 있지 않으면 `FIELD_VALIDATION_LEAKAGE`로 FAIL한다. Calibration 성공만으로 `field_validated` 또는 design citation을 허용하지 않는다.

- [ ] **Step 3: CSV importer와 immutable manifest를 구현한다**

  Required columns: point ID, x/y/z 또는 terminal ID, variable, value, unit, timestamp/window, instrument ID, calibration source, uncertainty. Raw CSV hash와 mapping transform을 보존한다.

- [ ] **Step 4: CFD sampling과 uncertainty comparison을 구현한다**

  `cfd_post.py`의 raw VTU/terminal `phi`를 사용한다. Sensor와 CFD temporal window를 일치시키고 measurement, sampling, mapping, numerical uncertainty를 분리 보고한다.

- [ ] **Step 5: GUI measurement mapping을 구현한다**

  TAB/현장 담당자가 도면 위 point/terminal을 확인하고 calibration/holdout 목적을 명시한다. Missing measurement에서는 `UNCALIBRATED`; 임의의 현장 검증 완료 문구를 금지한다.

- [ ] **Step 6: 승인되지 않은 threshold를 fail-open하지 않는다**

  Field error band는 CFD/V&V·TAB 책임자 승인을 받아 `uncertainty_budget.v1`에 version/hash로 기록한다. 승인 전에는 QoI와 combined uncertainty를 보고하되 `field_validated=NOT_EVALUATED`다.

- [ ] **Step 7: tests를 실행한다**

  Run:

  ```powershell
  & $Python -B -m pytest -q tests/test_cfd_measurements.py tests/test_cfd_field_validation.py tests/test_field_acceptance.py tests/test_body_fitted_report.py tests/test_studio_workflow.py
  ```

  Expected: PASS.

- [ ] **Step 8: commit한다**

  ```powershell
  git add field_measurement.v1.schema.json field_calibration.v1.schema.json field_validation.v1.schema.json uncertainty_budget.v1.schema.json cfd_measurements.py cfd_field_validate.py field_acceptance.py cfd_post.py cfd_report.py cfd_case_health.py cfd_studio.py tests/test_cfd_measurements.py tests/test_cfd_field_validation.py tests/test_field_acceptance.py tests/test_body_fitted_report.py tests/test_studio_workflow.py
  git commit -m "feat: add field measurement calibration and holdout validation"
  ```

### Task 13: 과학적 V&V와 실제 현장 gate를 frozen candidate에서 완료한다

**Files:**

- Execute: `docs/superpowers/plans/2026-08-14-mep-cfd-validation-vv-release.md` Tasks P2.1~P7.3.
- Existing modules: `cfd_verification.py`, `cfd_numerical_sensitivity_runner.py`, `cfd_temporal_sensitivity.py`, `cfd_gci.py`, `cfd_benchmark_validate.py`, `cfd_field_validate.py`.
- Create: `scripts/validation_artifact_inventory.py`.
- Create: `tests/test_validation_artifact_inventory.py`.
- Evidence root: `cfd_projects/_release_evidence/`.

**Interfaces:**

- Consumes: the frozen candidate commit, current toolchain identity, validation anchor, approved benchmark registry, calibrated measurement contract, and blind holdout set.
- Produces: immutable verification, scheme/time study, GCI, benchmark, field-validation, and independent-review evidence linked to the same candidate and anchor hashes.
- Does not mutate: the candidate source tree, reference benchmark data, calibration records, or holdout measurements.

- [ ] **Step 1: evidence-inventory validator의 실패 tests를 작성한다**

  Missing required contract, duplicate authority, path escape/reparse point, stale candidate/anchor hash, report-only evidence, malformed review signature를 각각 FAIL시키고 순서가 바뀐 동일 inventory의 canonical hash는 같아야 한다.

- [ ] **Step 2: read-only inventory validator를 구현한다**

  Evidence root 밖을 읽지 않고 symlink/reparse point를 거부한다. 각 artifact의 schema, file hash, producer identity, candidate commit, validation anchor, upstream hashes를 다시 검증한다. HTML/report의 자기 선언은 authoritative artifact를 대체하지 못한다.

- [ ] **Step 3: validator tests를 통과시키고 source만 commit한다**

  ```powershell
  & $Python -B -m pytest -q tests/test_validation_artifact_inventory.py tests/test_vv_baseline.py
  git add scripts/validation_artifact_inventory.py tests/test_validation_artifact_inventory.py
  git commit -m "test: add frozen validation evidence inventory"
  ```

  Expected: PASS. 이 commit의 `git rev-parse HEAD`가 아래 runtime study의 frozen candidate가 된다.

- [ ] **Step 4: serial environment와 64-cell acceptance 3/3를 재실행한다**

  WSL/OpenFOAM/FreeCAD/OCC identity와 logs가 current baseline에 묶여야 한다.

- [ ] **Step 5: exact verification을 완료한다**

  Heat-box와 laminar duct 등 code/postprocess verification이 각 analytic criterion을 통과하지 못하면 mesh/scheme/time study로 이동하지 않는다.

- [ ] **Step 6: scheme/time study를 실행한다**

  Anchor, fixed physical tree, actual `phi`, y+, continuity, Co, final 0.1 FTT window를 검증한다. Threshold 완화로 PASS시키지 않는다.

- [ ] **Step 7: 4-grid GCI를 실행한다**

  Current fixed gates: ≥4 grids, actual refinement ratio ≥1.25, ≥3.0 FTT, final-window drift ≤2%, each required metric uncertainty ≤5%.

- [ ] **Step 8: benchmark와 terminal applicability를 완료한다**

  Isothermal, buoyant, terminal/jet family의 raw comparator와 uncertainty를 포함한다. Terminal source/SKU가 없으면 jet throw/max-U citation을 withhold한다.

- [ ] **Step 9: field calibration과 blind holdout을 수행한다**

  Calibration과 holdout point/site를 분리하고 combined uncertainty/U95를 보고한다. Holdout failure는 model/BC/measurement uncertainty를 재평가하며 기준을 낮추지 않는다.

- [ ] **Step 10: independent review를 수행한다**

  CFD/V&V 검토자가 source, mapping, limitation, application scope를 승인하고 review record를 artifact hash에 묶는다.

- [ ] **Step 11: frozen-candidate evidence inventory를 재검증한다**

  ```powershell
  & $Python -B -m pytest -q tests/test_cfd_verification.py tests/test_cfd_numerical_sensitivity_runner.py tests/test_cfd_temporal_sensitivity.py tests/test_cfd_gci.py tests/test_cfd_benchmark_validate.py tests/test_cfd_field_validation.py tests/test_validation_artifact_inventory.py tests/test_vv_baseline.py
  & $Python -B scripts/validation_artifact_inventory.py --evidence-root cfd_projects/_release_evidence --candidate-commit <frozen-candidate-commit>
  ```

  Expected: tests failed 0; inventory의 required artifact가 모두 current candidate/anchor hash를 참조하고 missing, stale, duplicate authority가 0이다. `<frozen-candidate-commit>`은 Task 13 시작 시 `git rev-parse HEAD`로 고정한 40자리 commit ID로 치환하며 임의 문자열을 허용하지 않는다. Generated solver/field evidence는 source commit에 포함하지 않고 controlled release archive에 보존한다.

**Gate M3:** GCI, approved benchmark family, blind field holdout, current validation anchor와 case health가 모두 PASS해야 L3 후보를 만들 수 있다.

### Task 14: PMV/PPD Comfort module을 조건부 평가로 구현한다

**Files:**

- Create: `cfd_comfort.py`
- Create: `comfort_manifest.v1.schema.json`
- Create: `tests/test_cfd_comfort.py`
- Create: `tests/test_release_comfort_gate.py`
- Modify: `cfd_post.py`, `cfd_report.py`, `cfd_advice.py`, `cfd_studio.py`, `cfd_case_health.py`, `release_audit.py`.

**Interfaces:**

```python
def pmv_ppd_iso7730(
    *,
    air_temperature_c: float,
    mean_radiant_temperature_c: float,
    relative_air_speed_m_s: float,
    relative_humidity_pct: float,
    metabolic_rate_met: float,
    clothing_clo: float,
    external_work_met: float = 0.0,
) -> tuple[float, float]: ...

def evaluate_comfort(
    case_dir: Path,
    comfort_input: dict,
    *,
    evidence_path: Path,
    output_path: Path,
) -> dict: ...
```

- [ ] **Step 1: prerequisite failure tests를 작성한다**

  RH, met, clo, MRT source, occupied zone, time window, current result/evidence hash 중 하나라도 없으면 `comfort_status=NOT_EVALUATED`다. `design_ready=false`이면 계산값을 내부 진단으로 만들 수 있어도 citable report에 표시하지 않는다.

- [ ] **Step 2: published reference-vector tests를 작성한다**

  승인된 ISO 7730/ASHRAE reference vector source를 fixture metadata에 기록하고 PMV ±0.01, PPD ±0.5 percentage point 이내를 요구한다. Fixture를 코드에 옮길 때 source edition과 hash를 보존한다.

- [ ] **Step 3: nonuniform mesh weighting tests를 작성한다**

  Cell-count mean과 volume-weighted result가 다른 synthetic VTU를 사용해 occupied volume/time weighted p05/p50/p95가 선택되는지 검증한다.

- [ ] **Step 4: deterministic comfort evaluator를 구현한다**

  입력 범위와 적용성을 검증한다. Air speed method, posture, external work, occupancy duration, MRT source를 manifest에 기록한다. Defaults로 RH/met/clo를 조용히 채우지 않는다.

- [ ] **Step 5: MRT source hierarchy를 구현한다**

  `validated_view_factor_surface_temperature`, `reviewed_surface_temperature_approximation`, `user_supplied_measured_mrt`를 명시적으로 구분한다. Approximation은 limitation을 강제하며 전체 표준 적합 문구를 허용하지 않는다.

- [ ] **Step 6: GUI/report를 구현한다**

  공간용도, RH, met, clo, posture, work, MRT source, 점유영역 preview, reviewer sign-off를 입력한다. `ASHRAE 55 전체 적합` 자동 문구는 사용하지 않고 평가한 method와 미평가 항목을 분리한다.

- [ ] **Step 7: release gate를 연결한다**

  Comfort 기능을 켠 Scenario는 current comfort manifest와 prerequisites가 없으면 case health에서 `NOT_EVALUATED`; product core release를 막지는 않지만 comfort claim과 export를 막는다.

- [ ] **Step 8: tests를 실행한다**

  Run:

  ```powershell
  & $Python -B -m pytest -q tests/test_cfd_comfort.py tests/test_release_comfort_gate.py tests/test_body_fitted_report.py tests/test_cfd_advice.py tests/test_studio_workflow.py
  ```

  Expected: PASS.

- [ ] **Step 9: commit한다**

  ```powershell
  git add cfd_comfort.py comfort_manifest.v1.schema.json cfd_post.py cfd_report.py cfd_advice.py cfd_studio.py cfd_case_health.py release_audit.py tests/test_cfd_comfort.py tests/test_release_comfort_gate.py tests/test_body_fitted_report.py tests/test_cfd_advice.py tests/test_studio_workflow.py
  git commit -m "feat: add evidence-gated thermal comfort evaluation"
  ```

**Gate M4:** reference vectors, input completeness, occupied weighting, MRT limitation, report label tests가 PASS해야 comfort feature를 Design Partner 채널에 노출한다.

### Task 15: IAQ passive scalar·age-of-air·exposure module을 구현한다

**Files:**

- Create: `cfd_iaq.py`
- Create: `iaq_input.v1.schema.json`
- Create: `iaq_result.v1.schema.json`
- Create: `tests/test_cfd_iaq.py`
- Create: `tests/test_cfd_iaq_conservation.py`
- Modify: `cfd_physics.py`, `cfd_post.py`, `cfd_report.py`, `cfd_studio.py`, `cfd_case_health.py`.

**Interfaces:**

```python
def prepare_iaq_case(
    solver_case: Path,
    iaq_input: dict,
    *,
    scenario: dict,
) -> dict: ...

def evaluate_iaq_exposure(
    case_dir: Path,
    *,
    iaq_input_path: Path,
    occupant_zones: Sequence[dict],
    output_path: Path,
) -> dict: ...
```

- [ ] **Step 1: input contract tests를 작성한다**

  Species/scalar ID, source generation rate와 unit, time profile, outdoor/background concentration, removal/decay assumption, breathing-zone selector, occupancy time가 필수다. 불완전 입력은 `NOT_EVALUATED`다.

- [ ] **Step 2: conservation/manufactured-solution tests를 작성한다**

  Source-off uniform field, sealed-volume constant source, balanced inflow/outflow scalar cases에서 mass conservation, boundedness, sign convention을 검증한다.

- [ ] **Step 3: OpenFOAM passive scalar case builder를 구현한다**

  `cfd_physics.py`의 existing case preparation을 재사용하되 IAQ dictionaries와 source terms를 separate feature profile로 생성한다. Thermal result와 IAQ result manifest를 섞지 않는다.

- [ ] **Step 4: age-of-air와 exposure postprocess를 구현한다**

  Occupant/position/time별 concentration time series, cumulative exposure, breathing-zone statistics, mean age of air를 저장한다. 평균값만으로 모든 위치를 대표하지 않는다.

- [ ] **Step 5: benchmark와 field sensor path를 연결한다**

  Step-response/age-of-air benchmark와 field CO2 sensor comparison이 current source manifest와 uncertainty를 가져야 한다. Sensor threshold와 exposure acceptance band는 IAQ 책임자 승인 전 informational이다.

- [ ] **Step 6: 감염 위험 표현을 제한한다**

  감염확률 또는 risk index가 추가되면 assumption-based scenario indicator로만 표시하고 medical guarantee나 직접 compliance로 표현하지 않는다.

- [ ] **Step 7: GUI/report를 구현한다**

  Source 위치/시간, outdoor concentration, occupied zones, simulation duration, validation status를 검토한다. ACH/temperature만 있는 Scenario에서는 IAQ card를 `평가 불가`로 표시한다.

- [ ] **Step 8: tests를 실행한다**

  Run:

  ```powershell
  & $Python -B -m pytest -q tests/test_cfd_iaq.py tests/test_cfd_iaq_conservation.py tests/test_cfd_physics.py tests/test_cfd_post.py tests/test_body_fitted_report.py tests/test_studio_workflow.py
  ```

  Expected: PASS.

- [ ] **Step 9: commit한다**

  ```powershell
  git add cfd_iaq.py iaq_input.v1.schema.json iaq_result.v1.schema.json cfd_physics.py cfd_post.py cfd_report.py cfd_studio.py cfd_case_health.py tests/test_cfd_iaq.py tests/test_cfd_iaq_conservation.py tests/test_cfd_physics.py tests/test_cfd_post.py tests/test_body_fitted_report.py tests/test_studio_workflow.py
  git commit -m "feat: add validated indoor air quality workflow"
  ```

**Gate M5:** scalar conservation, age-of-air benchmark, input completeness와 field comparison path가 PASS할 때만 IAQ claim을 활성화한다.

### Task 16: Radiation/CHT를 benchmark-first로 production에 연결한다

**Files:**

- Modify: `cfd_radiation.py`, `tests/test_cfd_radiation.py`.
- Preserve and extend in place: `cfd_benchmarks/radiation/two_plate/`.
- Create: `radiation_validation.v1.schema.json`
- Create: `cht_input.v1.schema.json`
- Create: `cht_validation.v1.schema.json`
- Create: `surface_thermal_contract.v1.schema.json`
- Create: `cfd_cht.py`
- Create: `tests/test_cfd_cht.py`
- Preserve: `geometry.v2.schema.json`; thermal/material semantics live in the separate surface contract keyed by stable geometry element IDs.
- Modify: `cfd_occ.py`, `cfd_physics.py`, `cfd_post.py`, `cfd_report.py`, `cfd_result_gate.py`.

**Interfaces:**

```python
def build_cht_case(
    mesh_case_dir: Path,
    solver_case_dir: Path,
    *,
    material_contract: dict,
    exterior_boundary_contract: dict,
) -> dict: ...

def evaluate_cht_case(case_dir: Path, *, validation_manifest: Path | None) -> dict: ...
```

- [ ] **Step 1: 실제 two-plate serial benchmark를 완료한다**

  `constant/F`, non-zero-time `qr`, view-factor row sum, reciprocity, patch net flux, internal radiation balance를 raw files에서 재수집한다. Current reference heat flux `661.543682 W/m²`와 source hash를 사용하되 허용오차는 CFD/V&V 승인본에서 읽는다.

- [ ] **Step 2: copied benchmark manifest bypass test를 유지한다**

  Field case에 two-plate manifest를 복사해도 `radiation_project_integration_pending`이 해제되지 않아야 한다.

- [ ] **Step 3: surface/material contract를 구현한다**

  `surface_thermal_contract.v1`에서 reviewed `geometry.v2`의 stable element ID/hash를 참조한다. Interior/exterior, wall/window/ceiling/floor, material layer, emissivity source, thermal BC를 named surface에 연결한다. geometry 원본이나 v2 schema에 thermal 필드를 역삽입하지 않는다. 단일 `wall` patch인 기존 case는 production radiation/CHT를 BLOCKED한다.

- [ ] **Step 4: CHT conservation tests를 작성한다**

  `input = exhaust enthalpy + external heat flux + air storage + solid storage`의 sign과 time integration을 synthetic fields로 검증한다.

- [ ] **Step 5: enclosure와 wall-conduction benchmark를 구현한다**

  Two-plate는 radiation subsystem verification이고 building field validation이 아니다. Independent enclosure와 wall conduction/CHT benchmark를 추가하고 raw comparator uncertainty를 기록한다.

- [ ] **Step 6: production builder를 feature-gated로 연결한다**

  Scenario가 material/exterior BC/radiation을 요청할 때만 별 profile을 생성한다. Missing validation/applicability면 case 생성 또는 citation을 fail-closed한다.

- [ ] **Step 7: ON/OFF comparison report를 구현한다**

  Air temperature, surface temperature, exhaust enthalpy, external flux, air/solid storage, PMV MRT source의 차이를 보고한다. 단순 90~110% closure만으로 radiation PASS를 선언하지 않는다.

- [ ] **Step 8: tests를 실행한다**

  Run:

  ```powershell
  & $Python -B -m pytest -q tests/test_cfd_radiation.py tests/test_cfd_cht.py tests/test_cfd_physics.py tests/test_cfd_result_gate.py tests/test_body_fitted_report.py
  ```

  Expected: PASS.

- [ ] **Step 9: commit한다**

  Stage exact schema version, modules, fixtures, tests and modified producers. Do not enable production flags in the same commit as the first benchmark generator; enable them only after raw solver evidence review.

**Gate M6:** two-plate, enclosure, wall-conduction/CHT, conservation, application-scope review가 PASS해야 production radiation/CHT를 노출한다.

### Task 17: 설치·진단·지원·Frozen RC 출시 경로를 완성한다

**Files:**

- Create: `cfd_support_bundle.py`
- Create: `tests/test_cfd_support_bundle.py`
- Create: `docs/product/status-vocabulary.md`
- Create: `docs/product/release-runbook.md`
- Create: `docs/training/mechanical-engineer-quickstart.md`
- Create: `docs/training/observer-uat-script.md`
- Modify: `cfd_diagnostics.py`, `cfd_capabilities.py`, `install_acceptance.py`, `uat_acceptance.py`, `release_audit.py`, `cfd_studio.py`, `README.md`, `CFD_사용설명서.md`.
- Test: `tests/test_install_acceptance.py`, `tests/test_release_audit.py`, `tests/test_studio_workflow.py`, `tests/test_windows_launchers.py`.

**Interfaces:**

```python
def build_support_bundle(
    projects_root: Path,
    *,
    case_id: str | None,
    output_zip: Path,
) -> dict: ...
```

- [ ] **Step 1: support bundle privacy tests를 작성한다**

  Bundle은 environment identity, selected logs, error codes, manifests와 hashes를 포함하되 API keys, browser cookies, unrelated user files, full DXF를 기본 포함하지 않는다.

- [ ] **Step 2: 첫 실행 준비 센터를 구현한다**

  Python, OpenFOAM/WSL, FreeCAD/OCC, disk, permission을 `확인 중/준비됨/조치 필요/지원 필요`로 표시한다. 64-cell test는 환경 수용이며 설계 결과가 아님을 명시한다.

- [ ] **Step 3: status vocabulary를 UI/report/docs에 고정한다**

  `SCREENING_ONLY`, `CITATION_BLOCKED`, `DESIGN_CITABLE`, `INVALIDATED`의 한국어 copy와 next action이 API, dashboard, report, field-run, release screen에서 일치하는 contract test를 추가한다.

- [ ] **Step 4: update/rollback acceptance를 구현한다**

  Current project data hash → backup/recovery point → update → environment acceptance → rollback drill 순으로 검증한다. Reinstall이 사용자 Design/Run data를 삭제하지 않아야 한다.

- [ ] **Step 5: frozen RC를 만든다**

  Execute Tasks P8.1~P8.4 of `docs/superpowers/plans/2026-08-14-mep-cfd-validation-vv-release.md`. Current source, toolchain lock, schema, benchmark, docs, package를 하나의 RC ID/hash에 묶는다.

- [ ] **Step 6: RC에서 과학·설치 증거를 재생성한다**

  Execute Tasks P9.1~P9.3. Pre-RC evidence는 hash가 동일할 때만 재사용한다. 두 clean PC install/recovery, performance, security, diagnostics를 검증한다.

- [ ] **Step 7: 실제 DXF 3건과 UAT 3명을 수행한다**

  Existing fixed gates: actual-site evidence 3건, unit/origin/block rotation/layer 각각 ≥2 variants; UAT participants ≥3, completion ≥90%, median setup ≤15 min, fatal usability errors 0.

- [ ] **Step 8: UAT 이해도와 복구 지표를 추가한다**

  신뢰도 상태 설명 성공률, 잘못된 설계 인용 시도, 도움 횟수, retry, checkpoint recovery를 기록한다. 새 제안 출시 기준은 설명 성공률 ≥80%, 잘못된 인용 시도 0, recovery success ≥95%이며 Product Owner와 CFD reviewer 승인 후 versioned UAT contract에 넣는다.

- [ ] **Step 9: final release audit를 실행한다**

  ```powershell
  & $Python -B -m pytest -q tests --junitxml=cfd_projects/_release_evidence/junit-final.xml
  & $Python release_audit.py --projects-root cfd_projects
  ```

  Expected: current RC의 environment, numerical/benchmark, field, install/recovery, UAT, documentation/support checks가 모두 PASS. 하나라도 미통과면 release는 BLOCKED다.

- [ ] **Step 10: commit source/docs; generated RC evidence는 release archive에 보존한다**

  Commit producer, schema, tests, docs. User drawings, sensor data, packaged evidence archive는 repository policy에 따라 별도 controlled storage에 둔다.

**Gate M7:** frozen RC의 독립 audit PASS와 Product Owner/CFD/IT 서면 review가 있어야 L4를 선언한다.

### Task 18: MPI를 serial-equivalent optional backend로 승격한다

**Files:**

- Modify: `cfd_parallel.py`, `cfd_capabilities.py`, `cfd_physics.py`, `cfd_run.py`, `cfd_studio.py`.
- Create: `mpi_runtime_smoke.v1.schema.json` for the existing runtime artifact contract.
- Create: `tests/test_cfd_mpi_equivalence.py`
- Test: `tests/test_cfd_parallel.py`, `tests/test_cfd_mpi_smoke.py`, `tests/test_cfd_physics.py`.

**Interfaces:**

```python
def evaluate_mpi_capability(*, evidence_path: Path, runtime_identity: dict) -> dict: ...
def compare_serial_parallel(serial_case: Path, parallel_case: Path) -> dict: ...
```

- The capability result is opt-in only and is invalidated by runtime, solver, mesh, physics, or evidence-hash drift.

- [ ] **Step 1: isolated rank-spawn을 복구한다**

  `mpirun -np 2 hostname`이 timeout 없이 두 rank output을 만든다. WSL distro, OpenMPI path/version, environment override, cleanup result를 artifact로 기록한다.

- [ ] **Step 2: 4-rank OpenFOAM smoke를 실행한다**

  Frozen small case에서 `decomposePar → mpirun solver → reconstructPar`를 실행하고 processor count, logs, orphan process 0, cleanup을 확인한다.

- [ ] **Step 3: serial/parallel equivalence tests를 구현한다**

  Frozen input/mesh/physical time에서 mass/energy balance, volume-weighted mean/p95 T/U, terminal `phi`, result field finiteness를 비교한다. 허용오차는 empirical run과 CFD reviewer 승인을 받아 versioned evidence에 기록한다.

- [ ] **Step 4: capability-based opt-in을 구현한다**

  Current runtime identity와 passing smoke/equivalence hash가 맞을 때만 parallel option을 표시한다. 그렇지 않으면 GUI는 “병렬 미지원—직렬 실행”으로 유지한다.

- [ ] **Step 5: performance benchmark를 별도 기록한다**

  준비/분해/solver/reconstruct/회수 시간을 분리한다. 작은 case에서 slower여도 correctness를 PASS시킬 수 있지만 성능 개선 주장을 하지 않는다.

- [ ] **Step 6: tests를 실행한다**

  Run: `& $Python -B -m pytest -q tests/test_cfd_parallel.py tests/test_cfd_mpi_smoke.py tests/test_cfd_mpi_equivalence.py tests/test_cfd_physics.py tests/test_cfd_capabilities.py`

  Expected: PASS. Smoke artifact가 없으면 serial fallback test가 PASS한다.

- [ ] **Step 7: commit한다**

  ```powershell
  git add cfd_parallel.py cfd_capabilities.py cfd_physics.py cfd_run.py cfd_studio.py mpi_runtime_smoke.v1.schema.json tests/test_cfd_mpi_equivalence.py tests/test_cfd_parallel.py tests/test_cfd_mpi_smoke.py tests/test_cfd_physics.py tests/test_cfd_capabilities.py
  git commit -m "feat: add evidence-gated optional mpi backend"
  ```

  Do not change the default from serial in the first equivalence commit.

**Gate M8-A:** rank-spawn, OpenFOAM multi-rank, equivalence, cleanup, current identity가 PASS한 설치에서만 MPI opt-in을 허용한다.

### Task 19: Surrogate를 OOD-aware screening assistant로 구현한다

**Files:**

- Create: `cfd_surrogate.py`
- Create: `surrogate_model_card.v1.schema.json`
- Create: `tests/test_cfd_surrogate.py`
- Modify: `project_model.py`, `cfd_compare.py`, `cfd_studio.py`, `cfd_report.py`.

**Interfaces:**

```python
def build_training_inventory(
    run_identity_paths: Sequence[Path],
    *,
    required_health: str = "DESIGN_CITABLE",
) -> dict: ...

def predict_scenario(
    model_card_path: Path,
    scenario: dict,
) -> dict: ...
```

- [ ] **Step 1: dataset eligibility tests를 작성한다**

  Training row는 Design/Scenario/Run identity, geometry/mesh/physics/toolchain hashes, QoI, numerical/field uncertainty를 가진다. Screening-only 또는 stale run은 default training inventory에서 제외한다.

- [ ] **Step 2: leakage tests를 작성한다**

  Random row split을 금지하고 Design/site/scenario-family holdout을 강제한다. Same Design revision의 near-duplicate Run이 train과 validation에 동시에 들어가면 FAIL한다.

- [ ] **Step 3: model card와 domain-of-validity를 구현한다**

  Feature range, geometry/terminal descriptors, CMH, heat/weather range, training evidence IDs, error by holdout family, uncertainty calibration, OOD distance를 기록한다.

- [ ] **Step 4: `CFD_REQUIRED` fail-closed response를 구현한다**

  OOD, missing feature, uncertainty upper bound 초과, unsupported template이면 prediction을 설계안으로 반환하지 않고 authoritative CFD run을 요구한다.

- [ ] **Step 5: UI를 추천 보조로 제한한다**

  Surrogate는 Scenario 후보 순위와 예상 범위를 표시하고 “screening recommendation” watermark를 가진다. Case Health, field evidence, release readiness를 직접 승격하지 않는다.

- [ ] **Step 6: held-out benchmark와 field subset을 검증한다**

  허용 prediction error/OOD distance는 dataset 확보 후 CFD/Product reviewer가 승인한다. 승인 전 모델은 internal research channel에만 둔다.

- [ ] **Step 7: tests를 실행한다**

  Run: `& $Python -B -m pytest -q tests/test_cfd_surrogate.py tests/test_project_model.py tests/test_cfd_compare.py tests/test_studio_workflow.py`

  Expected: PASS, OOD paths return `CFD_REQUIRED`.

- [ ] **Step 8: commit한다**

  ```powershell
  git add cfd_surrogate.py surrogate_model_card.v1.schema.json project_model.py cfd_compare.py cfd_studio.py cfd_report.py tests/test_cfd_surrogate.py tests/test_project_model.py tests/test_cfd_compare.py tests/test_studio_workflow.py
  git commit -m "feat: add out-of-domain aware scenario assistant"
  ```

**Gate M8-B:** verified dataset, design/site holdout, calibrated uncertainty, OOD fail-closed, human review가 없으면 사용자 채널에 노출하지 않는다.

---

## 9. Quantitative Acceptance Matrix

### 현재 코드/기존 계획에서 확정된 값

| 영역 | 기준 |
|---|---|
| Legacy screening continuity | `< 1e-3` |
| Legacy screening residual | `U*=1e-3`, `p_rgh/T/k/epsilon/omega=1e-2` |
| Legacy energy closure | 정상 `90~110%`, hard fail outside `75~125%` |
| Legacy mass error | `≤5%` |
| Body-fitted global continuity | `≤1e-6` |
| Detailed-design Co | `≤1.0` |
| Body-fitted energy closure | `95~105%` |
| Boussinesq ΔT | `≤30 K` |
| y+ target | `30~300`, acceptable wall area ratio `≥0.80` |
| GCI | `≥4 grids`, actual refinement ratio `≥1.25`, uncertainty `≤5%`, `≥3.0 FTT`, final-window drift `≤2%` |
| Field evidence | actual-site, sample 제외, current hash chain, OpenFOAM v2606, `≥3.0 FTT`, complete energy history |
| Field DXF diversity | valid cases `≥3`; unit/origin/block rotation/layer 각각 `≥2` variants |
| UAT | users `≥3`, completion `≥90%`, median setup `≤15 min`, fatal errors `0` |
| Annex 20 | RMS `≤0.10·U0` and three qualitative flow conditions |
| Comfort calculation | PMV `±0.01`, PPD `±0.5 percentage point` against approved reference vectors |

### 엔지니어링 승인 전에는 informational인 값

- Sidewall jet/terminal profile error bands and model-form escalation tolerance.
- Field TAB temperature/velocity/flow combined acceptance bands and U95 target.
- PMV/PPD project design target, approved defaults, local-discomfort criteria.
- CO2/exposure/age-of-air acceptance limits and sensor sampling policy.
- Radiation view-factor/flux tolerance and CHT external-boundary closure tolerance.
- Serial/parallel QoI equivalence and required speedup.
- Surrogate prediction error, uncertainty calibration and OOD threshold.

위 값은 source, measurement uncertainty, reviewer, version, hash가 있는 manifest로 승인될 때까지 PASS/FAIL 기준에 쓰지 않는다.

## 10. Test Pyramid and Required Commands

| Layer | 목적 | 기본 명령 |
|---|---|---|
| Unit | canonical hash, schema, state, formulas, mapping | `& $Python -B -m pytest -q tests/test_<module>.py` |
| Contract/integrity | cross-hash, tamper, stale, root containment | evidence/project/field schema suites |
| Synthetic integration | fake OCC/mesh/run/result trees | Studio/result/field workflow suites |
| Exact verification | heat-box, duct, conservation | companion V&V plan commands |
| Real solver smoke | 64-cell, working-room, restart | current-PC acceptance scripts |
| Numerical V&V | scheme/time/GCI | validation-anchor workstream |
| Physical benchmark | Annex 20/jet/buoyancy/terminal/radiation/CHT | benchmark registry runner |
| Field | TAB/sensor calibration and blind holdout | field validation runner |
| Product | install/recovery/UAT/support/release | frozen RC acceptance |

Every task ends with its focused suite. Every milestone ends with:

```powershell
& $Python -B -m pytest -q tests --junitxml=cfd_projects/_release_evidence/junit-<milestone>.xml
```

`<milestone>` is replaced with the actual fixed milestone ID such as `m1-case-evidence`; no literal angle-bracket filename is used during execution.

## 11. RACI and Review Ownership

| Decision | Responsible | Accountable | Consulted | Informed |
|---|---|---|---|---|
| Domain/schema/code | Developer/agent | Tech lead | CFD reviewer | Product owner |
| Numerical threshold/GCI | CFD V&V engineer | CFD lead | Developer | Product owner |
| Terminal/heat/BC input | MEP engineer | Project MEP lead | TAB/CFD | Operator |
| Field measurement/uncertainty | TAB/field engineer | Project MEP lead | CFD reviewer | Developer |
| Comfort/IAQ scope | Building physics/IAQ reviewer | Project MEP lead | CFD/Product | Users |
| Release RC/package | Developer/IT | Product owner | CFD reviewer | Users |
| UAT result | Observer/Product | Product owner | MEP users | Developer |
| MPI/surrogate enablement | Developer/CFD | Tech lead | IT/Product | Users |

Reviewer identity는 개인 이름을 코드에 hard-code하지 않고 조직의 stable reviewer ID로 evidence에 기록한다.

## 12. Risk Register

| Risk | Early signal | Prevention | Fallback |
|---|---|---|---|
| Dirty worktree ownership conflict | targeted file already modified/untracked | M0 baseline and explicit ownership | stop and request scope approval |
| Schema proliferation ahead of product value | new contract without consumer/E2E | schema task must ship producer+validator+UI/test | remove unconsumed proposal before merge |
| Case health duplicates result gate | differing status for same artifact | health consumes authoritative result gate | block release on inconsistency test |
| Design/Scenario migration corrupts cases | file move/hash drift | optional sidecars, no move | legacy read-only mode |
| Attractive contour hides weak evidence | user chooses colors over blockers | KPI + trust first, plots under evidence tab | SCREENING watermark and UAT |
| GCI fails repeatedly | uncertainty >5% after 3 FTT | redesign mesh family/terminal topology | reduce applicability, never relax threshold |
| Calibration leaks into validation | same IDs/site in both sets | disjoint-set validator | field status NOT_EVALUATED |
| Radiation/CHT expands too early | missing surface/material source | benchmark-first feature gate | convection-only SCREENING |
| IAQ/comfort becomes false compliance | missing inputs/standard methods | independent contracts and NOT_EVALUATED | hide claim/export |
| MPI creates hangs/orphans | rank smoke timeout | serial-first capability gate | serial execution |
| Surrogate overconfidence | OOD/uncertainty high | model card and CFD_REQUIRED | authoritative CFD |
| Release evidence goes stale | RC/hash changes | frozen RC re-run and invalidation | BLOCKED release |

## 13. Explicit Non-Goals Until Core Release

- Direct DWG reading, arbitrary IFC/STEP import, or BIM authoring. Future input adapters must produce the same reviewed `geometry.v2` contract.
- Cloud orchestration, multi-user SaaS, mobile application, external API marketplace.
- LES/DES, GPU/LBM, general-purpose multiphysics or all-building energy simulation.
- Automatic equipment kW/CMH inference without approved source and review.
- AI-generated design approval, automated ASHRAE/ISO compliance, or infection guarantee.
- Removing the legacy screening engine before migration evidence shows it is no longer required.

## 14. Milestone Exit Checklist

### M1 — Trust Core

- [ ] Case evidence rejects copied/stale/self-declared PASS.
- [ ] Human approval cannot override failed evidence.
- [ ] Actual DXF 1 case reaches report through GUI and resume.
- [ ] WORKING_SINGLE_PC current artifact PASS.
- [ ] User can state usable scope and next action from one case-health screen.

### M2 — Repeatable Design

- [ ] Design revision and Scenario revision are immutable and distinct.
- [ ] Two Scenarios of one Design run without manual JSON/CLI.
- [ ] Compare report explains input and evidence differences.
- [ ] Legacy cases remain readable and unmodified.

### M3 — Validated Design Review Candidate

- [ ] Scheme/time/GCI share one validation anchor.
- [ ] Exact, GCI, approved benchmark and field holdout pass.
- [ ] Application scope and withheld metrics are explicit.
- [ ] Independent CFD review is hash-bound.

### M4/M5/M6 — Extended Physics

- [ ] Each module has input/result contract and NOT_EVALUATED path.
- [ ] Each module passes independent reference/benchmark tests.
- [ ] Core product release does not imply module approval.
- [ ] Report names assessed and unassessed methods separately.

### M7 — General Internal Release

- [ ] Frozen RC and toolchain lock.
- [ ] Two clean-PC install/recovery acceptance.
- [ ] Actual DXF 3, diversity, field evidence.
- [ ] UAT 3, completion/time/fatal gates.
- [ ] Support bundle, runbook, rollback and final release audit PASS.

## 15. First Recommended Execution Slice

첫 implementation branch는 **Tasks 1~4만** 수행한다.

```text
case_evidence.v1 + case_health.v1 + case_review.v1
  → current cfd_result_gate / field_pipeline / report 재사용
  → Studio 신뢰도 카드와 actionable blockers
  → actual DXF 1건 SCREENING_ONLY evidence chain
```

이 slice는 solver/mesh/physics를 바꾸지 않으면서 제품의 핵심을 “계산 실행”에서 “검토 가능한 증거”로 전환한다. 이 branch가 merge되고 M1 gate가 PASS하기 전에는 Design/Scenario UI, comfort, IAQ, radiation production을 병합하지 않는다.

## 16. Plan Self-Review Record

- Spec coverage: Exa P0 case evidence/review, P1 scenario/V&V/field calibration, P2 comfort/IAQ/CHT, P3 MPI/surrogate를 각각 Tasks 1~19에 연결했다.
- Existing assets: GCI, result gate, field pipeline, UAT, radiation two-plate, working/release plans을 중복 개발 대상으로 두지 않고 상위 계약에서 재사용했다.
- Authority cycles: Task 10의 validation anchor와 `GCI_CANDIDATE` 분리로 sensitivity–GCI와 field–fine-case 순환을 제거했다.
- Backward compatibility: 기존 `cfd_projects`, v1 manifests, API URLs, legacy case 조회를 유지한다.
- Claim discipline: solver/test/PASS 문자열을 design/release 증거로 사용하지 않고 comfort/IAQ/CHT/MPI/surrogate의 independent gates를 명시했다.
- External approvals: field/terminal/IAQ/radiation/MPI/surrogate의 미확정 threshold는 승인 전 informational로 고정했다.
- Placeholders: 실행 시 생성되는 IDs와 runtime paths를 제외한 미정 구현 항목은 없다. Runtime ID는 authoritative manifest가 발행한다.
- Structural check: Tasks 0~19가 각각 Files와 Interfaces를 가지며 task 번호는 중복·누락 없이 20개다.
- Path check: 현재 `Modify`/`Preserve` 대상은 baseline에 존재하거나 앞선 task가 명시적으로 생성하며, `Create` 대상은 2026-08-24 checkout에서 존재하지 않음을 대조했다.
- Markdown check: code fence 수는 짝수이고 `git diff --check`에 whitespace error가 없다.

## 17. Execution Handoff

권장 실행은 Tasks 1~4를 하나의 vertical slice로 처리하는 **Subagent-Driven Development**다. 각 task마다 implementer → spec review → code-quality review를 수행하고 focused/full tests를 통과한 뒤 다음 task로 이동한다. 장시간 solver evidence가 필요한 Tasks 5, 13, 16, 17은 코드 리뷰와 실제 실행 review를 분리한다.
