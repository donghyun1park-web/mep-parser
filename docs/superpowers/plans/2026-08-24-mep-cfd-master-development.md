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

### 문서 은퇴 정책

계획 문서 총량은 현재 8종 361KB다. 문서가 늘어날수록 어느 것이 현역인지 판단하는 비용이 커지므로 각 문서에 은퇴 조건을 명시한다.

| 문서 | 은퇴 조건 | 은퇴 후 상태 |
|---|---|---|
| `NEXT_SPRINT_2026-08-13.md` | 이미 충족 | 완료 이력 — 백로그 권위 없음 |
| `NEXT_PHASE_PLAN.md` | M2 Exit 시 | 참조 전용 — body-fitted 형성 이력 |
| `IMPROVEMENT_PLAN_2026-08.md` | M3 Exit 시 | 참조 전용 — 확정 threshold만 인용 |
| `2026-08-14-single-pc-working-validation.md` | M1 Exit 시 | 참조 전용 |
| `2026-08-14-vv-release.md` | M7 Exit 시 | 참조 전용 |
| 본 문서 | L4 도달 시 | 후속 roadmap으로 이관 |

은퇴한 문서는 삭제하지 않고 첫 줄에 `> **상태: 참조 전용 — <Milestone> 이후 은퇴. 현행 권위는 <문서>.**`를 추가한다.

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

- 원본 checkout의 `codex/cfd-studio-improvements`는 다수 dirty/untracked 경로를 가진 clean RC가 아니며 그대로 보존한다. 개발 기준선은 격리된 public branch `codex/case-evidence-review-gate`로 분리됐고 Windows CI가 green이지만, 이 사실은 frozen RC나 일반 배포 준비를 의미하지 않는다.
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

#### Schema 소비자 요건 — 강제 규칙

§12의 "Schema proliferation ahead of product value" 위험을 원칙이 아니라 **착수 조건**으로 강제한다. 새 schema를 만드는 Task는 그 schema를 도입하기 전에 다음 표를 채워야 한다.

| 필수 항목 | 의미 |
|---|---|
| Producer | 이 문서를 쓰는 모듈 |
| Validator | 위조·stale·hash 불일치를 거부하는 함수 |
| Consumer | 이 문서를 **읽어서 사용자에게 보여주거나 gate 판정에 쓰는** 화면·보고서·평가기 |
| Test | producer/validator/consumer를 각각 덮는 test |

**Consumer가 정해지지 않은 schema는 만들지 않는다.** 계획에 남아 있더라도 해당 Task에서 제거하고, 소비자가 생기는 Milestone으로 미룬다. 25개 전부를 만드는 것은 목표가 아니다.

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

계획 가정은 주 개발자 1명, CFD/V&V 검토자 part-time 1명, Windows/IT 지원 part-time, 현장/TAB 및 UAT 협조자다. 계산·현장 일정은 코드 개발과 병렬 진행한다. 이 가정의 실제 가용성은 Task 0 Step 7에서 확인하며, 확보되지 않은 역할이 필요한 Task는 임계경로에서 제외한다.

### 두 개의 시계

이 계획의 작업은 성격이 다른 두 종류이며 **같은 시간 단위로 합산하지 않는다.**

| 시계 | 성격 | 압축 가능성 |
|---|---|---|
| **코드 시계** | 모듈·schema·test 구현. 에이전트 보조로 수행 | 압축 가능 |
| **증거 시계** | solver wall-clock, 현장 TAB 측정, 외부 검토자·UAT 협조 | **압축 불가** |

실측 기준점(2026-08-24): Task 0~4의 코드 작업은 **5.6시간·17 커밋**에 완료됐다. 반면 단일 thermal case의 3.0 FTT는 수 시간~수십 시간이고, 현장 측정과 UAT는 외부 일정에 종속된다.

따라서 **Milestone 기간은 증거 시계로만 산정한다.** 코드 시계가 짧아지는 것은 일정 단축이 아니라 증거 대기 시간이 드러나는 것이다.

| Milestone | 목표 | 코드 시계 | 증거 시계 | Exit |
|---|---|---:|---:|---|
| M0 | 기준선·소유권·test bootstrap·원격 반영 | 0.5~1일 | 없음 | clean 또는 승인된 baseline, 실패 0, origin 반영 |
| M1 | Case Evidence & Review Gate + Working Single PC | 1~3일 | **1~3주** | 실제 DXF 1건 SCREENING_ONLY E2E, recomputation health |
| M2 | Design → Scenario → Run + compare/template | 3~6일 | 3~5일 | 같은 Design의 2개 Scenario 비교·보고 |
| M3 | Validation anchor, formal V&V, field TAB | 3~6일 | **10~16주** | GCI/benchmark/field holdout gate, L3 후보 |
| M4 | Comfort | 2~4일 | 1~2주 | reference vectors, complete inputs, conditional report |
| M5 | IAQ | 4~7일 | 4~7주 | scalar conservation, benchmark, exposure report |
| M6 | Radiation/CHT | 4~8일 | 5~9주 | two-plate + enclosure/CHT benchmark, production fail-closed |
| M7 | Product hardening and general release | 4~8일 | **6~10주** | frozen RC, package/recovery/UAT/support, L4 |
| M8 | MPI and surrogate | 각 2~5일 | 각 3~7주 | rank smoke/equivalence 또는 OOD model-card gate |

증거 시계의 주요 구속 요인: M1은 solver 실행과 geometry 확정, M3는 GCI 계산과 현장 측정, M7은 설치 검증 2대와 UAT 3명이다.

M0~M3와 M7을 병렬화한 core 제품 목표는 **증거 시계 기준 14~22주**가 현실적이다. M4~M6까지 순차 완료하는 extended physics 목표는 7~11개월 범위로 관리한다. 실패한 계산은 일정을 늘릴 수 있지만 threshold를 낮추는 근거가 되지 않는다.

매 2주 점검에서 실제 코드 시계와 증거 시계를 각각 기록하고, 다음 Milestone 추정을 실측으로 보정한다.

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

- [x] **Step 1: 변경 소유권 gate를 통과한다**

  `git status --short`에서 `cfd_studio.py`, `cfd_run.py`, `cfd_report.py`, `cfd_physics.py`와 신규 schema/test의 소유권을 확인한다. 사용자 승인 없이 기존 dirty 파일 전체를 stage하거나 정리하지 않는다. 승인된 baseline commit 또는 승인된 파일 목록이 없으면 이 계획의 코드 작업을 시작하지 않는다.

- [x] **Step 2: 기준선 tests를 실행한다**

  Run:

  ```powershell
  $Python = (Resolve-Path '.venv\Scripts\python.exe').Path
  & $Python -B -m pytest -q tests/test_vv_baseline.py tests/test_working_validation.py tests/test_io_acceptance.py
  ```

  Expected: failed 0. Runtime-dependent skip은 이유와 해제 조건이 문자열로 기록된다.

- [x] **Step 3: 전체 test baseline을 JUnit으로 기록한다**

  Run:

  ```powershell
  & $Python -B -m pytest -q tests --junitxml=cfd_projects/_release_evidence/junit-master-plan-baseline.xml
  ```

  Expected: failed 0. 실패가 있으면 새 기능을 시작하지 않고 기존 실패의 원인과 소유권을 먼저 분리한다.

- [x] **Step 4: baseline evidence를 생성하고 자체 검증한다**

  Run the exact P0.0~P0.2 steps in `docs/superpowers/plans/2026-08-14-mep-cfd-validation-vv-release.md`, including ACL/I/O acceptance and authoritative inventory exclusion of generated reports.

- [x] **Step 5: 계획 전용 branch/worktree를 만든다**

  기준선 commit이 승인된 후 `codex/case-evidence-review-gate`처럼 작업 단위별 branch를 만든다. dirty working tree 자체에서 large refactor를 시작하지 않는다.

- [x] **Step 6: 승인된 baseline을 원격에 반영한다**

  로컬 commit만으로는 baseline이 보존되지 않는다. 2026-08-24 감사에서 39,046줄과 test 전량이 단일 디스크에만 존재하는 상태가 확인됐다. 승인 후 다음을 수행한다.

  ```powershell
  git ls-remote origin
  git push -u origin codex/case-evidence-review-gate
  ```

  Expected: 원격에 branch가 생성되고 `git rev-list --count <branch> --not --remotes` 가 0이다.

  푸시 전에 필수 안전 점검을 통과해야 한다.

  | 점검 | 기준 |
  |---|---|
  | 고객·현장 식별 파일 | 저장소 전체에 0건 |
  | API key·token·private key | 0건 |
  | `cfd_projects/` 해석 결과 | 추적되지 않음 |
  | DXF | 합성 샘플만 |

  저장소가 public이면 실제 현장 데이터를 다루기 전에 private 전환 여부를 결정하고 결과를 baseline evidence에 기록한다. 이 Step은 코드 작업이 아니므로 어떤 Task보다 먼저 완료할 수 있다.

  **2026-08-25 공개 진행 결정:** 사용자(저장소 공개 범위 결정권자)는 `origin`을 public으로 유지하고 이 branch를 공개 push하도록 명시적으로 승인했다. 안전 점검에서 신규 secret, 추적된 `cfd_projects/`, 신규 현장 artifact는 발견되지 않았지만, public `feature/cfd`에 이미 존재하는 `debug_tools/temp_geometry.json`, `temp_export*.dxf`, 관련 screenshot은 실제 도면 파생 가능성이 있어 “clean”으로 표현하지 않는다. 이번 branch가 해당 blob을 새로 추가·변경하지 않는다는 비교 결과와 사용자의 알려진 위험 수용을 예외 근거로 보존한다. 이후 실제 현장 입력·solver 결과는 계속 저장소 밖에 둔다.

  **완료 근거:** public `origin/codex/case-evidence-review-gate`가 생성됐고 Task 0 구현 HEAD `113a2e7b6b20f25f2700408d6a3c11709c48869b`까지 반영됐다. 해당 push 직후 로컬 미푸시 커밋 수는 0이었다.

- [x] **Step 7: 역할별 담당자와 가용성을 확인한다**

  §11 RACI의 8개 역할 각각에 대해 실제 담당자 ID와 가용 시점을 기록한다. 확보되지 않은 역할은 다음과 같이 처리한다.

  - 해당 역할이 필요한 Task를 `BLOCKED_NO_OWNER`로 표시한다.
  - 그 Task를 임계경로와 Milestone 기간 산정에서 제외한다.
  - 확보 전까지 그 Milestone의 Exit를 선언하지 않는다.

  특히 M3(현장 TAB·독립 CFD 검토자)와 M7(기계설비 사용자 3명·관찰자)은 외부 인력 없이는 착수 자체가 불가능하다. 없는 인력을 전제한 일정을 유일한 기준으로 사용하지 않는다.

  **완료 근거:** `docs/governance/mep-cfd-raci-availability-2026-08-25.md`에 8개 decision의 역할·owner ID·가용성을 기록했다. 현재 확인된 것은 `codex-agent`의 제한된 개발 capability뿐이며, 모든 사람/외부 결정 역할은 `UNASSIGNED/BLOCKED_NO_OWNER`로 기록하고 해당 외부 Task를 active critical path에서 제외했다.

- [x] **Step 8: 최소 CI를 구성한다**

  `.github/workflows/windows-ci.yml`에서 `toolchain.lock.json`이 고정한 Python(현재 3.12.10)으로 전체 test를 실행한다. 다른 patch version에서는 bootstrap 검증 test가 정상적으로 실패하므로 CI runtime을 lock에 맞춘다.

  Expected: push마다 `pytest` failed 0. CI 없이는 test 결과가 개발 PC 한 대에서만 의미를 갖는다.

  **완료 근거:** `.github/workflows/windows-ci.yml`은 잠금 Python 3.12.10, lock identity, 전체 `pytest`, JUnit upload를 실행한다. public [Windows CI run 32800246154](https://github.com/donghyun1park-web/mep-parser/actions/runs/32800246154)의 HEAD `113a2e7`에서 job `97659530263`가 green이며, JUnit은 851 tests = 837 passed + 14 skipped, failures/errors 0, 103.521초다. artifact `9546275605` digest는 `sha256:9e0b2d7d6d4a8129b075b488b173d666afc5269f79e8a2fac561dc7a96932874`다.

**Gate M0:** tests failed 0, baseline artifact PASS, path ownership 승인, target branch, **원격 반영 완료**, **역할 가용성 기록**, **CI 최초 green**이 없으면 NO-GO.

**2026-08-25 판정:** M0 COMPLETE. 일반 배포는 계속 NO-GO이며, active code critical path만 Task 5a로 이동한다. Task 4.5/5b/5c와 M1 Exit는 confirmed geometry와 사람 역할/실행 증거가 없어 계속 차단된다.

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

### Task 4.5: 실제 DXF 1건의 geometry를 확정한다 — MEP 담당자 트랙

이 Task는 코드 작업이 아니라 **기계설비 담당자의 판단 작업**이며, Task 5c와 M1 Exit의 필수 선행조건이다. 개발 Task와 별도 트랙으로 병렬 진행하고, 담당자 확보 전에는 Task 5c를 착수하지 않는다. 사용자 승인으로 현장 geometry를 소비하지 않는 Task 5b의 합성 acceptance/benchmark 실행은 병렬 진행할 수 있지만, 그 결과가 confirmed geometry나 M1 조건을 대체하지 않는다.

**Files:**

- Read: 대상 현장 DXF와 `*.geometry.json`
- Produce at runtime: `*.confirmed.geometry.json` (원본은 변경하지 않는다)

**Interfaces:**

- Consumes: Studio의 geometry 확인 화면.
- Produces: `contract=geometry.v2`, `review.ready=true`, blocker 0인 confirmed geometry.

**현재 상태(2026-08-24 실측):** 저장소 전체에 `*.confirmed.geometry.json`이 **0건**이며, 최신 SGI geometry는 `review.ready=false`다. 즉 M1은 코드가 아니라 이 입력 확정에 막혀 있다.

**2026-08-26 기술 준비:** 격리 작업공간의 로컬 전용 검토 패키지는
`READY_FOR_MEP_REVIEW`이며, 최신 입력은 재계산 결과 blocker 128이다.
MEP 승인과 confirmed geometry는 여전히 0건이므로 Task 4.5, Task 5c,
M1 Exit는 계속 열려 있다. 공개 브랜치에는 현장 좌표·원본·확정본을
포함하지 않는다.

- [ ] **Step 1: companion plan P7.1을 그대로 실행한다**

  `docs/superpowers/plans/2026-08-14-mep-cfd-validation-vv-release.md`의 Task P7.1 5개 Step을 수행한다: 단일 closed air zone 확정 → 층고·급배기 단말 수·방향 확인 → 총 급기와 총 배기 차이 ≤1% → 발열 kW의 실제 위치·대류분율·근거 확인 → confirmed geometry 저장.

- [ ] **Step 2: 자동 검출값을 승격하지 않는다**

  DXF에서 검출한 공간·단말·장비는 입력 후보일 뿐이다. 위치별 kW 근거가 없으면 장비를 개별 열원으로 만들지 않고, 바닥 균질 발열은 source-location sensitivity 전까지 `SCREENING_ONLY`로 유지한다.

- [ ] **Step 3: 확정 결과를 검증한다**

  Expected: `review.ready=true`, blockers 0, body-fitted issues 0, 원본 DXF hash와 confirmed terminal/heat evidence 보존.

**Gate 4.5:** confirmed geometry 1건이 없으면 Task 5c와 M1 Exit를 선언하지 않는다.

### Task 5a: WORKING_SINGLE_PC validators를 code-owned로 닫는다 — 코드 시계

**Files:**

- Modify: `working_validation.py`
- Preserve: `working_validation.v1.schema.json`; the fixed eight-check contract and scientific labels do not change in this milestone.
- Modify: `cfd_capabilities.py`.
- Modify: `cfd_physics.py` only for explicit fail-closed validation scopes; ordinary Studio/field physics remains unchanged.
- Create: `scripts/local_usability_acceptance.py`, `cfd_working_room.py`, `cfd_verification.py`, `cfd_numerical_spotcheck.py` according to the fixed contracts in the companion plan.
- Create schemas: `local_usability_acceptance.v1.schema.json`, `working_room_acceptance.v1.schema.json`, `sgi_screening_acceptance.v1.schema.json`, `verification_manifest.v1.schema.json`, `numerical_spotcheck.v1.schema.json`.
- Test: `tests/test_working_validation.py`, `tests/test_cfd_capabilities.py`, `tests/test_cfd_physics.py`, `tests/test_local_usability_acceptance.py`, `tests/test_cfd_working_room.py`, `tests/test_sgi_screening_acceptance.py`, `tests/test_cfd_verification.py`, `tests/test_cfd_numerical_spotcheck.py`.
- Read-only reuse in 5a: `cfd_run.py`, `cfd_post.py`; do not change `cfd_studio.py` merely to make synthetic validators pass.

**Interfaces:**

- `evaluate_working_validation(projects_root: Path) -> dict` remains the public evaluator.
- Each fixed check gets a code-owned validator; `_future_not_implemented()` is no longer used for completed checks.
- Fixed authoritative manifests under the evaluated `projects_root` are:

  | Check | Fixed source manifest | Raw authority |
  |---|---|---|
  | `serial_environment` | `_working_validation/local_usability_acceptance.json` | `_system/environment_acceptance/` and `_working_validation/runtime_capability.v1.json` |
  | `working_room_e2e` | `_working_validation/working-room-v1/working_room_acceptance.json` | hash-pinned `anchor/` and `repeat/` children |
  | `real_dxf_screening`, `restart_integrity` | `_working_validation/sgi-screening-v1/sgi_screening_acceptance.json` | hash-bound actual `_field_jobs/...` and `_body_*` case |
  | `exact_heat_verification` | `_working_validation/heat-box-v1/verification_manifest.json` | hash-pinned heat-box case |
  | `limited_numerical_spotchecks` | `_working_validation/numerical-spotcheck-v1/numerical_spotcheck.json` | hash-pinned anchor plus exactly three named children |

- Every manifest uses relative paths and hashes. Validators reject `latest`, cache/temp paths, sibling escapes, self-output, and post-load hash drift, and add every consumed raw file to `evidence_sha256`.
- Task 5a creates schemas, pure validators, explicit validation-scope guards, and synthetic immutable-tree/tamper tests only. It runs no solver, FreeCAD, Studio, browser, GUI DXF, or generated-evidence acceptance.
- Task 5b owns real serial/working-room/heat-box/numerical evidence producers. Task 5c owns confirmed-DXF GUI, shutdown/resume, SGI/restart manifest production, and usability observation.
- `NUMERICAL_SPOTCHECK_COPIED_RESULT`의 6-decimal fingerprint는 미세 nonce를 제거하는 bounded near-copy heuristic일 뿐 독립 solver 실행 증명이 아니다. Task 5b가 각 case의 raw `T/U/phi/yPlus` provenance와 실행 증거를 hash-bind하고 현재 파일에서 재계산하기 전에는 “anchor evidence가 복사되지 않았다”는 무제한 주장을 금지한다.

- [x] **Step 1: exact companion plan의 코드 부분만 실행한다**

  Execute the code-owned validator portions of Tasks 2~5 in `docs/superpowers/plans/2026-08-14-mep-cfd-single-pc-working-validation.md` without changing its scientific labels. Real runtime evidence belongs to 5b/5c. Without genuine artifacts the evaluator must remain `BLOCKED`; `WORKING_SINGLE_PC` and `NUMERICAL_SPOTCHECK_PASS_SINGLE_PC` remain non-citable and non-release states.

- [x] **Step 2: focused tests를 통과시킨다**

  Run: `& $Python -B -m pytest -q tests/test_working_validation.py tests/test_cfd_capabilities.py tests/test_cfd_physics.py tests/test_local_usability_acceptance.py tests/test_cfd_working_room.py tests/test_sgi_screening_acceptance.py tests/test_cfd_verification.py tests/test_cfd_numerical_spotcheck.py`

  Expected: PASS. 실제 solver 실행이 없는 상태에서도 validator는 `BLOCKED`를 정확히 보고해야 한다.

- [x] **Step 3: commit code only**

  Stage exact producer/schema/test paths. 사용자 DXF, solver case, generated evidence는 source commit에 넣지 않는다.

**Gate 5a:** COMPLETE (code-only). validator 코드가 모두 존재하고 `_future_not_implemented()`가 완료 check에서 제거됐다. 최종 focused 분할 검증은 `421 passed, 7 skipped, 9 subtests passed`이고 독립 재검토는 `CLEAN`이다. 로컬 전체 suite는 호출 가능한 Python 3.14.3에서 `1177 passed, 2 failed, 14 skipped`였으며, 두 실패는 실행 Python SHA가 고정 Python 3.12.10 SHA와 다른 동일한 toolchain-authentication precondition이므로 green으로 기록하지 않는다. Exact code commit `f3b7109386195ae665bd216cb689c686f23dea99`의 공개 Windows CI [run 32822053903](https://github.com/donghyun1park-web/mep-parser/actions/runs/32822053903)는 고정 Python 3.12.10 환경에서 성공했다. JUnit artifact `9553586783`은 1,193 tests = 1,179 passed + 14 skipped, failures/errors 0, 217.691 seconds이며 digest는 `sha256:1be289869b9ff4a78944997c7f1755361d6be334551045b5607ff5ae678d64d9`다. 이 gate는 solver/FreeCAD/Studio/browser/real-DXF 실행 또는 runtime evidence PASS를 뜻하지 않는다.

### Task 5b: 실제 solver 증거를 수집한다 — 증거 시계

이 Task의 각 Step은 solver wall-clock에 묶여 있으며 코드 속도로 압축되지 않는다.

- [x] **Step 1: serial environment acceptance를 세 번 실행한다**

  ```powershell
  & $Python -B scripts/produce_local_usability_acceptance.py --repo-root . --python-executable $Python --launch-attempts 3 --output cfd_projects/_working_validation/local_usability_acceptance.json
  & $Python -B scripts/local_usability_acceptance.py --projects-root cfd_projects --manifest cfd_projects/_working_validation/local_usability_acceptance.json --output cfd_projects/_working_validation/evaluations/serial-environment-evaluation.json
  ```

  Expected: body-fitted runtime ready, current 64-cell acceptance, serial baseline, Studio startup 3/3. MPI may remain BLOCKED.

  **2026-08-27 actual:** locked Python 3.12.10, FreeCAD 1.1.1/OCC 7.8.1, OpenFOAM v2606 serial, independently parsed 64 cells, physical time 1.0 s, clean solver `End`, report, runtime baseline, Studio startup 3/3, and five actionable diagnostic observations were bound to one run and independently revalidated `PASS` with blockers 0. MPI execution smoke remains `NOT_RUN`. Runtime evidence stays ignored under `cfd_projects/`.

- [ ] **Step 2: working-room anchor/repeat를 실행한다**

  Acceptance: watertight single air volume, `checkMesh` PASS, illegal cells 0, terminal-only mesh refinement level 1 on the 0.125 m background, fixed `deltaT=0.01 s`, physical time ≥240 s, Co ≤1.0, terminal phi imbalance ≤0.1%, energy closure 95~105%, finite VTU/slices/report. Repeat differences: mean T ≤0.02 K, mean speed ≤0.005 m/s, closure ≤0.5 percentage point.

  **2026-08-27 approved revision:** fixed `deltaT=0.02 s` produced peak Co 2.40 at terminal level 2 and 1.3895 at terminal level 1. Level 0 reduced Co but failed the 5% terminal-area mesh gate with 9.997% error. The user approved terminal level 1 plus fixed `deltaT=0.01 s`; no acceptance threshold was relaxed and no failed candidate was published.

- [ ] **Step 3: exact heat와 limited spot-check를 실행한다**

  Acceptance: heat-box analytic mean-temperature relative error ≤1%, storage closure 0.99~1.01, Co ≤1.0, global continuity ≤1e-6. Scheme/time/mesh comparison은 formal GCI가 아니라 two-level engineering spot-check로 라벨링한다.

**Gate 5b:** Step 1은 완료됐다. 전체 Gate는 Step 2 working-room과 Step 3 exact-heat/limited-spot-check 실행 증거까지 현재 artifact hash로 재검증된 뒤에만 완료한다.

### Task 5c: 실제 DXF GUI E2E와 resume 무결성을 확인한다 — 증거 시계

**선행조건:** Task 4.5의 confirmed geometry 1건. 없으면 이 Task를 시작하지 않는다.

- [ ] **Step 1: 실제 DXF 1건을 GUI만으로 실행하고 resume한다**

  원본 hash, confirmed geometry, OCC, mesh, thermal checkpoint, report, case evidence가 하나의 chain이어야 한다. 첫 verified checkpoint 뒤 Studio를 종료하고 같은 job을 GUI에서 재개한다. CLI/JSON 수동 편집이 필요하면 product defect로 기록한다.

- [ ] **Step 2: 축소 usability check를 수행한다**

  기계설비 담당자 1명에게 완료된 결과 화면을 보여주고 세 가지를 답하게 한다: (1) 이 결과를 어디까지 쓸 수 있는가 (2) 무엇이 막고 있는가 (3) 다음에 무엇을 해야 하는가. 30분 이내, 관찰 기록만 남긴다.

  이것은 M7의 정식 UAT를 대체하지 않는다. 목적은 UX 결함을 M7까지 미루지 않고 M1에서 발견하는 것이다. 세 질문 중 하나라도 답하지 못하면 상태 문구와 next action을 수정한다.

- [ ] **Step 3: 두 번의 working validation을 비교 publish한다**

  ```powershell
  & $Python working_validation.py --projects-root cfd_projects --output cfd_projects/_working_validation/run1.json
  & $Python working_validation.py --projects-root cfd_projects --output cfd_projects/_working_validation/run2.json
  & $Python working_validation.py --compare cfd_projects/_working_validation/run1.json cfd_projects/_working_validation/run2.json --publish-json cfd_projects/_working_validation/working_validation.json --publish-html cfd_projects/_working_validation/working_validation.html
  ```

  Expected: deterministic evidence and no generated-report self-inventory contamination.

**Gate M1:** Case Health tamper tests, actual GUI E2E, restart integrity, WORKING_SINGLE_PC, 축소 usability check가 모두 PASS해야 M2로 이동한다. Task 4.5가 미완이면 M1은 `BLOCKED_INPUT_CONFIRMATION`이며 코드 완성도와 무관하게 Exit를 선언하지 않는다.

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

- [x] **Step 1: immutability와 revision tests를 작성한다**

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

- [x] **Step 2: tests가 실패하는지 확인한다**

  Run: `& $Python -B -m pytest -q tests/test_project_model.py tests/test_design_scenario_contract.py`

  Expected: FAIL because module and schemas do not exist.

- [x] **Step 3: contracts를 구현한다**

  `Design`은 reviewed `geometry.v2` path/hash와 revision history만 가진다. `Scenario`는 supply/exhaust CMH, supply temperature, heat-source authority, occupancy, weather/exterior assumptions, operating time, mesh/physics intent를 가진다. 실제 solver dictionaries는 Run이 만든다.

- [x] **Step 4: content-derived IDs와 atomic repository를 구현한다**

  ID prefix는 `design-`, `scenario-`, `run-`으로 고정하고 canonical JSON SHA-256으로 revision을 식별한다. 사용자 표시는 name을 사용하며 name 변경은 identity를 바꾸지 않는다.

- [x] **Step 5: variation whitelist tests를 추가한다**

  Scenario clone에서 허용되는 변화와 geometry change를 구분한다. terminal role/normal/size 변경은 Design revision, CMH/supply temperature/operating schedule 변경은 Scenario revision으로 판정한다.

- [x] **Step 6: focused tests를 실행한다**

  Run: `& $Python -B -m pytest -q tests/test_project_model.py tests/test_design_scenario_contract.py tests/test_geometry_v2_contract.py`

  Expected: PASS.

- [x] **Step 7: commit한다**

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

- [x] **Step 1: non-destructive legacy import tests를 작성한다**

  기존 case 폴더의 file list/hash가 import 전후 동일하고 metadata sidecar만 새로 생기는지 검증한다. provenance가 부족한 case는 `legacy_unlinked`, 조회 가능, scenario comparison 불가, design citation 불가다.

- [x] **Step 2: run identity mismatch tests를 작성한다**

  Design revision 또는 Scenario hash가 달라지면 field resume을 막고 `RUN_IDENTITY_CHANGED`를 반환한다. 기존 checkpoint와 결과는 삭제하지 않는다.

- [x] **Step 3: legacy reader와 sidecar writer를 구현한다**

  기존 `cfd_projects/<case>`를 이동하지 않는다. `_project_model/` metadata에서 legacy path를 상대 경로로 참조하고 현재 hash를 저장한다.

- [x] **Step 4: field pipeline manifest에 identity reference를 추가한다**

  v2에 `case_identity_path`, `case_identity_sha256`, `design_revision_sha256`, `scenario_revision_sha256`를 필수로 추가한다. reader는 schema version을 먼저 판별하고, 이전 v1 문서는 메모리상 `case_identity_status=NOT_LINKED`를 보완해 읽되 원본 파일을 자동 변환하거나 덮어쓰지 않는다.

- [x] **Step 5: invalidation을 구현한다**

  Design revision 변경 시 이전 Scenario/Run을 삭제하지 않고 `SUPERSEDED_DESIGN_REVISION`으로 표시한다. 같은 Run resume은 frozen identity가 current artifact와 일치할 때만 허용한다.

- [x] **Step 6: regression을 실행한다**

  Run:

  ```powershell
  & $Python -B -m pytest -q tests/test_project_model.py tests/test_field_pipeline_job.py tests/test_studio_workflow.py
  ```

  Expected: PASS, 기존 field job v1 fixtures도 읽힌다.

- [x] **Step 7: commit한다**

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

- [x] **Step 1: template가 물리값을 추정하지 않는 실패 tests를 작성한다**

  CMH, equipment kW, RH, met, clo, external temperature가 사용자 또는 approved source 없이 채워지면 실패해야 한다. Template은 required roles, validation rules, UI copy, allowed parameters만 제공한다.

- [x] **Step 2: terminal mapping tests를 작성한다**

  `geometry.v2`의 stable element ID와 role을 사용하고 화면 label이나 patch order에 의존하지 않는다. 빠진 terminal, 중복 terminal, 불균형 supply/exhaust를 blocker로 반환한다.

- [x] **Step 3: template loader와 apply를 구현한다**

  Built-in templates에는 난류/수치 expert defaults를 직접 노출하지 않는다. physics profile name과 그 적용범위만 선택하고 실제 dictionaries는 `cfd_physics.py`가 생성한다.

- [x] **Step 4: semantic diff를 구현한다**

  Diff row는 `path`, `baseline`, `candidate`, `unit`, `engineering_effect`, `requires_review`를 가진다. float display rounding과 identity hash calculation을 분리한다.

- [x] **Step 5: tests를 실행한다**

  Run: `& $Python -B -m pytest -q tests/test_cfd_templates.py tests/test_project_model.py tests/test_heat_source_contract.py`

  Expected: PASS.

- [x] **Step 6: commit한다**

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
- Modify: `cfd_studio.py`, `cfd_post.py`
- Modify: `cfd_report.py`
- Modify: `cfd_advice.py`
- Modify: `scenario.v1.schema.json`, `result_manifest.v1.schema.json`

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

- [x] **Step 1: API backward-compatibility tests를 작성한다**

  기존 `/api/cases`, `/api/body-results/<case>`, `/api/start-field-pipeline-job`가 그대로 동작하고 새 identity가 있을 때만 추가 metadata를 반환하는지 고정한다.

- [x] **Step 2: comparison eligibility tests를 작성한다**

  같은 Design revision의 2~4개 Run만 기본 비교한다. 다른 geometry, incomplete evidence, incompatible QoI selector는 명확한 blocker를 반환한다.

- [x] **Step 3: Project/Design/Scenario/Run 화면을 추가한다**

  Design 화면은 원본·2D·3D air volume·terminal/heat-source review·승인 이력을 보여준다. Scenario 화면은 baseline clone, semantic diff, 예상 자원, 기대 citation scope를 보여준다. Run center는 기존 serial queue와 checkpoint를 재사용한다.

- [x] **Step 4: compare KPI를 제한한다**

  우선 KPI는 명시적으로 확인한 occupied-volume selector의 최종 snapshot cell-volume-weighted mean/p95 temperature와 p95 speed, solver `phi` 기반 actual supply/exhaust flow, energy closure, hotspot location, case-health blockers다. Aggregation은 `cell_volume_weighted_final_snapshot`으로 기록하며 아직 구현하지 않은 time-window weighting으로 오인시키지 않는다. Max value는 mesh-independent evidence 없이는 설계 KPI로 강조하지 않는다.

- [x] **Step 5: report 3종을 구현한다**

  `screening`, `design-review`, `field-comparison` template을 분리하되 기존 HTML filename/link는 유지한다. Compare report는 입력 차이, 신뢰도 차이, 동일/상이한 evidence scope를 첫 페이지에 표시한다.

- [x] **Step 6: regression과 rendering smoke를 실행한다**

  Run:

  ```powershell
  & $Python -B -m pytest -q tests/test_cfd_compare.py tests/test_studio_design_scenario_run.py tests/test_studio_workflow.py tests/test_body_fitted_report.py tests/test_cfd_advice.py
  ```

  Expected: PASS. Browser smoke에서 새 Design 하나, Scenario 두 개, 각 Run 결과, compare report에 도달한다.

- [x] **Step 7: commit한다**

  ```powershell
  git add cfd_compare.py scenario_comparison.v1.schema.json cfd_studio.py cfd_report.py cfd_advice.py tests/test_cfd_compare.py tests/test_studio_design_scenario_run.py tests/test_studio_workflow.py tests/test_body_fitted_report.py tests/test_cfd_advice.py
  git commit -m "feat: add design scenario run comparison workflow"
  ```

**Gate M2:** 같은 reviewed Design revision에서 두 Scenario가 서로 다른 CMH/temperature/heat setting으로 실행되고, 입력 diff·결과 KPI·case health·report를 GUI로 비교해야 한다. Legacy case는 계속 조회 가능해야 한다.

**Task 9 code-contract 상태(2026-08-27):** API·GUI·불변 비교 artifact·3개 report scope·점유영역 QoI producer/manifest/consumer 연결은 구현됐다. 화면은 첫 Scenario를 물리 기본값 없이 생성하고 같은 Design의 서로 다른 Scenario Run 2~4개를 함께 선택한다. 비교기는 current identity/evidence/result/QoI hash, Case Health, 동일 Design revision·solver profile·selector를 재검증하고 fail closed한다. 실제 OpenFOAM으로 서로 다른 두 Scenario를 완주해 GUI compare report까지 도달한 실행 증거는 아직 없으므로 Gate M2는 `OPEN`이다. 최종 snapshot을 넘어선 occupied-volume time-window weighting은 Validation Anchor와 시간창 근거를 연결하는 후속 범위로 남는다.

### Task 10: Validation Anchor로 sensitivity·GCI·field authority를 통합한다

**Files:**

- Create: `validation_anchor.v1.schema.json`
- Create: `cfd_validation_anchor.py`
- Create: `tests/test_cfd_validation_anchor.py`
- Modify: `cfd_numerical_sensitivity_job.py`
- Modify: `cfd_temporal_sensitivity.py`
- Modify: `cfd_gci.py`
- Modify: `cfd_result_gate.py`
- Modify: `field_acceptance.py`
- Modify: `field_pipeline_job.py`
- Test: `tests/test_cfd_numerical_sensitivity_job.py`, `tests/test_cfd_temporal_sensitivity.py`, `tests/test_cfd_gci.py`, `tests/test_field_pipeline_job.py`, `tests/test_cfd_evidence.py`, `tests/test_release_audit.py`.

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

- [x] **Step 1: circular-gate regression tests를 작성한다**

  GCI candidate를 만들기 위해 design-ready sensitivity를 선요구하거나, sensitivity를 승인하기 위해 final GCI를 선요구하는 cycle을 재현하고 실패로 고정한다.

- [x] **Step 2: field/fine authority mismatch test를 작성한다**

  Field solver case와 GCI fine case가 path/hash/physical tree가 다르면 citation을 BLOCKED하고, 같은 anchor면 현재 artifact 재검증 후 통과할 수 있게 한다.

- [x] **Step 3: immutable anchor를 구현한다**

  Anchor는 occupied selector, geometry/surface/mesh/run/result/thermal tree, physical settings, solver identity와 SHA-256을 묶는다. Anchor 자체의 `PASS`를 신뢰하지 않고 각 consumer가 raw artifacts를 다시 읽는다.

- [x] **Step 4: `GCI_CANDIDATE`와 final citable gate를 분리한다**

  Candidate는 sensitivity study의 authority가 될 수 있지만 design citation을 허용하지 않는다. Final gate는 verified scheme/time sensitivity, GCI, benchmark, applicability를 모두 요구한다.

- [x] **Step 5: focused tests를 실행한다**

  Run:

  ```powershell
  & $Python -B -m pytest -q tests/test_cfd_validation_anchor.py tests/test_cfd_numerical_sensitivity_job.py tests/test_cfd_temporal_sensitivity.py tests/test_cfd_gci.py tests/test_cfd_result_gate.py tests/test_field_pipeline_job.py
  ```

  Expected: PASS and no authority cycle.

- [ ] **Step 6: companion V&V Plan P1.1~P1.5를 실행한다**

  장시간 GCI를 다시 실행하기 전에 serial sensitivity executor/verifier, temporal contract, field authoritative fine-case 연결을 완료한다.

- [x] **Step 7: commit한다**

  ```powershell
  git add validation_anchor.v1.schema.json cfd_validation_anchor.py cfd_numerical_sensitivity_job.py cfd_temporal_sensitivity.py cfd_gci.py cfd_result_gate.py field_pipeline_job.py tests/test_cfd_validation_anchor.py tests/test_cfd_numerical_sensitivity_job.py tests/test_cfd_temporal_sensitivity.py tests/test_cfd_gci.py tests/test_cfd_result_gate.py tests/test_field_pipeline_job.py
  git commit -m "feat: unify numerical validation authority"
  ```

**Task 10 부분 완료 상태(2026-08-28):** Steps 1~5와 코드 커밋 범위는 구현됐다. `validation_anchor.v1`은 occupied selector와 geometry→surface→mesh→thermal→run→result/source의 현재 바이트, 물리 설정, solver identity를 묶고 각 consumer가 다시 해시한다. sensitivity가 pending인 2차 case는 비인용 `GCI_CANDIDATE`로만 사용할 수 있고, GCI는 정확한 `gci_fine` anchor에 결속된다. field job은 별도 해석을 만들지 않고 동일 anchor identity의 `field_authority` 문서가 지정한 fine case를 재검증·재사용하며, `--analysis-only`는 release evidence를 발행하지 않는다. 임의 `PASS` 파일은 최종 gate를 열지 못한다. exact code commit `2ce0b65`의 locked Windows CI는 `1280 passed, 14 skipped`, 실패/오류 0으로 통과했다. P1.2의 serial scheme executor는 frozen seed 재해시, 기존 결과/parallel-state 거부, 전역 solver lock, baseline→variant 원자 checkpoint, 각 3.0 FTT와 독립 run hash 확인까지 구현됐고 focused 회귀는 `77 passed, 41 subtests passed`다. 하지만 P1.2 독립 verifier와 마지막 0.1 FTT QoI, P1.3 temporal executor/verifier, Task 11 benchmark/applicability validator 및 실제 장시간 solver evidence가 아직 없으므로 Step 6과 최종 `DESIGN_CITABLE`은 `OPEN`이다. Gate M2도 confirmed geometry와 근거 입력 부재 때문에 계속 `OPEN`이다.

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

- [ ] **Step 0: 원인규명 spike를 먼저 수행한다 — 기간 상한 1주**

  현재 `MPI_RANK_SPAWN_HANG`의 원인은 확정되지 않았고, 기본 설정과 `vader=none` 모두 실패한 실측만 있다. Step 1 이후를 착수하기 전에 상한 1주의 조사를 수행한다.

  조사 순서: WSL 재시작 → Open MPI 재설치 → `--mca plm isolated` 등 launcher 우회 → MPICH 대체 검토.

  **1주 안에 rank-spawn이 복구되지 않으면 M8-A를 폐기하고 "영구 직렬"로 확정한다.** 원인 미상 항목을 로드맵에 무기한 남겨 두지 않는다. 폐기 시 `runtime_capability.v1`의 `parallel_runtime_ready=false`를 최종 상태로 기록하고, 성능 개선은 직렬 최적화(스킴·완화계수·초기장)로 목표를 바꾼다.

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
| **Single point of failure — 원격 백업 없음** | `git rev-list --count <branch> --not --remotes` > 0 | Task 0 Step 6 원격 반영, Step 8 CI | 코드 작업 중단하고 백업 우선 |
| **없는 인력을 전제한 일정** | RACI 역할에 담당자 ID 없음 | Task 0 Step 7 가용성 확인 | 해당 Task를 임계경로에서 제외 |
| **입력 확정 지연이 M1을 막음** | confirmed geometry 0건 | Task 4.5를 별도 트랙으로 병렬 진행 | M1을 `BLOCKED_INPUT_CONFIRMATION`으로 유지 |
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

### M0 — Baseline

- [x] Tests failed 0 and baseline artifact PASS.
- [x] Approved baseline pushed to origin; unpushed commit count is 0.
- [x] Pre-push safety scan clean, 또는 이미 공개된 선행 blob의 알려진 위험을 저장소 공개 범위 결정권자가 명시적으로 수용하고 이번 branch에 신규 민감 artifact가 없음을 기록.
- [x] RACI roles have named owners or are marked `BLOCKED_NO_OWNER` and excluded from the critical path.
- [x] CI runs the full suite on the locked Python and is green.

### M1 — Trust Core

- [ ] Case evidence rejects copied/stale/self-declared PASS.
- [ ] Human approval cannot override failed evidence.
- [ ] Confirmed geometry exists for at least one real DXF (Task 4.5).
- [ ] Actual DXF 1 case reaches report through GUI and resume.
- [ ] WORKING_SINGLE_PC current artifact PASS.
- [ ] User can state usable scope and next action from one case-health screen — verified by the reduced usability check with one MEP engineer, not deferred to M7 UAT.

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

**2026-08-24 개정 — Tasks 1~4 완료 후의 다음 slice:**

Tasks 1~4는 완료됐다. 다음 slice는 코드가 아니라 **보존과 입력 확정**이며, 순서가 중요하다.

```text
Task 0 Step 6 (원격 반영)        ← 코드 시계 30분, 위험 감소 최대
  → Task 0 Step 7~8 (역할 확인·CI)
  → Task 4.5 (geometry 확정)     ← MEP 담당자 트랙, 병렬 시작
  → Task 5a (validators 코드)    ← 개발 트랙, 병렬 진행
  → Task 5b (solver 증거)
  → Task 5c (GUI E2E + 축소 usability)
  → M1 Exit
```

Task 0 Step 6은 어떤 코드 Task보다 먼저 수행한다. 현재 Tasks 1~4의 산출물이 단일 디스크에만 존재하므로, 이후 작업을 아무리 진행해도 그 위험은 줄지 않는다.

## 16. Plan Self-Review Record

- Spec coverage: Exa P0 case evidence/review, P1 scenario/V&V/field calibration, P2 comfort/IAQ/CHT, P3 MPI/surrogate를 각각 Tasks 1~19에 연결했다.
- Existing assets: GCI, result gate, field pipeline, UAT, radiation two-plate, working/release plans을 중복 개발 대상으로 두지 않고 상위 계약에서 재사용했다.
- Authority cycles: Task 10의 validation anchor와 `GCI_CANDIDATE` 분리로 sensitivity–GCI와 field–fine-case 순환을 제거했다.
- Backward compatibility: 기존 `cfd_projects`, v1 manifests, API URLs, legacy case 조회를 유지한다.
- Claim discipline: solver/test/PASS 문자열을 design/release 증거로 사용하지 않고 comfort/IAQ/CHT/MPI/surrogate의 independent gates를 명시했다.
- External approvals: field/terminal/IAQ/radiation/MPI/surrogate의 미확정 threshold는 승인 전 informational로 고정했다.
- Placeholders: 실행 시 생성되는 IDs와 runtime paths를 제외한 미정 구현 항목은 없다. Runtime ID는 authoritative manifest가 발행한다.
- Structural check: Tasks 0~19가 각각 Files와 Interfaces를 가지며 task 번호는 중복·누락 없이 20개다. 2026-08-24 개정에서 Task 4.5(입력 확정 트랙)를 추가하고 Task 5를 5a/5b/5c로 분할해 실행 단위는 23개다.
- Path check: 현재 `Modify`/`Preserve` 대상은 baseline에 존재하거나 앞선 task가 명시적으로 생성하며, `Create` 대상은 2026-08-24 checkout에서 존재하지 않음을 대조했다.
- Markdown check: code fence 수는 짝수이고 `git diff --check`에 whitespace error가 없다.

## 17. Execution Handoff

권장 실행은 Tasks 1~4를 하나의 vertical slice로 처리하는 **Subagent-Driven Development**다. 각 task마다 implementer → spec review → code-quality review를 수행하고 focused/full tests를 통과한 뒤 다음 task로 이동한다. 장시간 solver evidence가 필요한 Tasks 5, 13, 16, 17은 코드 리뷰와 실제 실행 review를 분리한다.

## 18. Execution Status Snapshot — 2026-08-25

- Isolated public execution branch: `codex/case-evidence-review-gate`; Task 5a aggregate code commit: `f3b7109386195ae665bd216cb689c686f23dea99`.
- Task 0~4 and Task 5a's code-only gate are complete. The reproducible toolchain/baseline, Case Evidence contracts, immutable evidence recomputation, append-only human review, Studio/API/report/advice citation gates, and six concrete WORKING_SINGLE_PC revalidators are implemented.
- Task 5a final scoped review verdict: `CLEAN`; focused split total: `421 passed, 7 skipped, 9 subtests passed`.
- The local Python 3.14.3 full suite is not green: `1177 passed, 2 failed, 14 skipped, 7 warnings, 115 subtests passed`. Both failures require the pinned Python 3.12.10 executable identity. The authoritative public pinned Windows CI [run 32822053903](https://github.com/donghyun1park-web/mep-parser/actions/runs/32822053903) is green at the exact Task 5a code commit: 1,193 tests, 0 failures/errors, 14 skipped.
- M0 remains complete. M1 and general release remain `NO-GO`: Task 4.5 confirmed geometry, Task 5b genuine solver evidence, Task 5c real-DXF GUI/restart/usability evidence, and named human roles remain unavailable.
- No FreeCAD/OpenFOAM/solver run, Studio/browser execution, real-DXF run, or manual print-preview is claimed by this snapshot.
- Detailed rulings, per-task commits, review findings, and verification evidence are maintained in the [SDD progress ledger](../../../.superpowers/sdd/2026-08-24-mep-cfd-master-development/progress.md) and [Task 5a report](../../../.superpowers/sdd/2026-08-24-mep-cfd-master-development/task-5a-report.md).

### Execution status update — 2026-08-27

- The user authorized synthetic-evidence-only Task 5b execution; confirmed site geometry and accountable MEP approval are still not supplied.
- Task 5b Step 1 is complete. A separate producer staged and generated current FreeCAD/Studio/OpenFOAM evidence, the pure validator passed the candidate, and an independent post-publication evaluation returned `PASS` with blockers 0.
- Actual scope: locked Python 3.12.10, FreeCAD 1.1.1/OCC 7.8.1, OpenFOAM v2606 serial, 64 cells, physical time 1.0 s, Studio readiness 3/3, actionable diagnostics 5/5. Focused regression: `289 passed, 7 skipped, 7 warnings`. Public exact-code commit `9e625e2dfd6c05b03e0d6efdffbcbf6b8fc5cb35` passed Windows CI [run `33028118325`](https://github.com/donghyun1park-web/mep-parser/actions/runs/33028118325) with `1194 passed, 14 skipped, 7 warnings`.
- Runtime artifacts remain ignored under `cfd_projects/`; the public branch contains only producer/validator code, tests, plans, governance, and sanitized progress/report records.
- Task 5b Steps 2–3, Task 4.5, Task 5c, M1, design citation, and release remain open or blocked. MPI execution smoke remains `NOT_RUN`.
- Task 5b Step 2 implementation now uses the approved terminal level 1 and fixed `deltaT=0.01 s` contract. The 240 s anchor/repeat evidence has not yet been rerun, so Step 2 remains open while code-clock Task 6 proceeds by explicit user direction.
- Task 6 is complete in code: closed `design.v1`, `scenario.v1`, and `case_identity.v1` contracts, immutable canonical-JSON-derived revisions, atomic publication, path containment, Design/Scenario variation classification, and current-reference tamper validation are implemented. Focused Task 6 plus geometry/working-room/GCI regression completed with `142 passed`; this is code-contract evidence and does not close Task 5b runtime or M1.
- Task 6 public verification is green at exact code/docs commit `923e4f438c35e390ca0c35600400626cace02393`: locked Windows CI run `33031962459`, job `98386275226`, completed `1208 passed, 14 skipped, 7 warnings` with zero failures/errors. The 14 skips remain runtime-gated and this CI does not assert a completed 240 s solver acceptance.
- Task 7 is complete in code-contract scope: legacy case inventory and Run links are external immutable sidecars under `_project_model/legacy_cases`, legacy case/checkpoint/result bytes are not rewritten, identity mismatch blocks queue/solver entry, and newer Design revisions compute `SUPERSEDED_DESIGN_REVISION` without deleting prior Scenario/Run evidence. Identity-less v1 field jobs remain readable and receive in-memory `NOT_LINKED`; identity-supplied jobs publish v2 with the four frozen identity references. Focused Task 7 regression completed with `132 passed, 7 warnings, 9 subtests passed`; M2 and actual field execution remain open.
- Task 7 public verification is green at exact implementation commit `312cb2d7b3d10d420ab18b9aa58eae8aa79c21e6`: locked Windows CI run `33036491175`, job `98400191067`, completed `1217 passed, 14 skipped, 7 warnings` with zero failures/errors. Runtime-gated skips and the absence of an actual identity-linked field solve remain explicit.
- Task 8 is complete in code-contract scope: mixing/displacement templates contain roles, validation rules, UI copy, and a supported screening profile but no physical or solver-dictionary defaults. Applying a template requires a traceable direct `user_confirmed:<ref>` authority; a bare `approved_source:<ref>` label fails closed until a verified approval-artifact contract can resolve it. Raw `geometry.v2` is schema/recomputation-checked preview-only, and only an authoritative validated immutable Design revision can become ready. Mapping uses stable element ID/role and blocks missing/duplicate/unstable/unconfirmed terminals, missing heat inputs, unknown IDs, and supply/exhaust imbalance. Scenario semantic diff is stable-ID based, unit/effect annotated, order-insensitive, recursively expands compound additions, and compares canonical exact JSON identity before display rounding, including integer/float and missing/null differences. Focused Task 8 verification completed with `50 passed, 10 subtests passed`; the extended Design/geometry/numerics/physics boundary completed with `145 passed, 7 skipped, 7 warnings, 27 subtests passed`. Independent final review verdict is `CLEAN`. Task 9, M2, an actual two-Scenario run comparison, verified approved-source ingestion, and release remain open.
- Task 8 public verification is green at exact implementation commit `4b2794138e7ba63031e9a329c77624130ee3197f`: locked Windows CI run [`33041856613`](https://github.com/donghyun1park-web/mep-parser/actions/runs/33041856613), job `98417004740`, completed `1241 passed, 14 skipped, 7 warnings` with zero failures/errors. Runtime-gated skips remain explicit; this CI does not claim an actual paired Scenario solve, PMV/PPD evaluation, or M2 completion.
- Task 9 is complete in code-contract scope: Project/Design/Scenario/Run API and GUI, first-Scenario confirmed-input flow, cross-Scenario Run selection, identity-bound serial queue input, atomic immutable ordered comparison publication, first-page input/evidence scope, three report modes, fail-closed Case Health, and actual VTU occupied-volume QoI production are connected. Comparison cross-checks the result VTU, Scenario/thermal-input selector and floor datum; solver `phi` has authority over declared actual-flow fields. Reports recompute comparison claims from pinned raw artifacts. Focused authority regression completed with `37 passed, 19 subtests passed`; extended boundary completed with `255 passed, 7 skipped, 7 warnings, 38 subtests passed`; independent final review is `CLEAN`. The local Python 3.14 full suite completed `1256 passed, 14 skipped, 2 failed, 7 warnings, 115 subtests passed`; both failures are the expected authenticated Python 3.12.10 executable-hash checks. Exact implementation commit `3b12da0689f9e7bdf0df95f2e0ac8adff089c6b5` passed locked Windows CI [run `33052192581`](https://github.com/donghyun1park-web/mep-parser/actions/runs/33052192581), job `98450163311`, with `1258 passed, 14 skipped, 7 warnings` and zero failures/errors. An actual paired OpenFOAM Scenario execution is still pending; therefore M2 remains `OPEN` and no design-readiness or release claim is made.

## 19. 계획 개정 기록 — 2026-08-24 (검토 반영)

독립 검토에서 방법론은 유지 판정을 받았고, 시간·인력 가정과 계획 밖 안전장치에 대해 6건이 반영됐다.

| ID | 개정 내용 | 반영 위치 |
|---|---|---|
| P1 | 일정을 코드 시계와 증거 시계로 분리. Milestone 기간은 증거 시계로만 산정 | §7 |
| P2 | 원격 반영·역할 확인·최소 CI를 Task 0 Step 6~8로 추가하고 Gate M0에 포함 | Task 0, §14 |
| P3 | geometry 확정을 Task 4.5 독립 트랙으로 분리 | Task 4.5 |
| P4 | Task 5를 5a(코드)/5b(solver 증거)/5c(GUI E2E)로 분할 | Task 5a~5c |
| P5 | 인력 가용성 게이트와 `BLOCKED_NO_OWNER` 처리 규칙 | Task 0 Step 7, §12 |
| P6 | schema Consumer 필수 요건, 축소 usability check, MPI 1주 spike 상한, 문서 은퇴 정책 | §5, Task 5c, Task 18, §1 |

개정의 근거가 된 실측: Task 0~4 코드 작업 5.6시간·17 커밋, 미푸시 39,046줄, confirmed geometry 0건, 계획서 1,747줄 내 CI·백업 언급 0건.

과학적 규율(evidence/citation 분리, 승인이 증거를 이길 수 없음, 미승인 threshold 격리, 명시적 비목표)은 변경하지 않았다.
