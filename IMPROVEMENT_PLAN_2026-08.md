# MEP CFD Studio 신뢰성·성능 개선 실행계획

> **실행 원칙:** 각 작업은 실패 테스트 → 최소 구현 → 회귀 테스트 순서로 진행한다. 특정 AI 도구·서브스킬의 설치 여부는 제품 실행 또는 검증 조건이 아니다.

**작성일:** 2026-08-12  
**대상 환경:** Windows + WSL Ubuntu 24.04 + OpenFOAM v2606 + FreeCAD/OCC + Python  
**목표:** 기계설비 담당자가 DXF 도면에서 해석 조건을 확인하고, 신뢰 수준이 표시된 공조 CFD 스크리닝 결과를 빠르게 얻을 수 있게 한다.

**Architecture:** 제품은 두 해석 경로를 명시적으로 구분한다. 구조격자 `buoyantBoussinesqSimpleFoam` 경로는 빠른 배치·열부하 스크리닝용이며, body-fitted 경로는 검증된 형상·메시·열원 계약을 통과한 설계 검토용이다. 결과 화면은 실행 완료, 수치 수렴, 설계 검토 가능을 서로 다른 상태로 표시하고, 어느 단계도 다음 단계를 자동으로 의미하지 않는다.

**Tech Stack:** Python, OpenFOAM v2606, WSL, FreeCAD/OCC, DXF/geometry.v2, pytest, 기존 브라우저 기반 `cfd_studio.py` GUI.

## 현재 구현·검증 상태 (2026-08-12)

| 항목 | 구현 상태 | 현재 근거 | 남은 확인 |
|---|---|---|---|
| A0 결과 신뢰도 계약 | 구현·회귀검증 완료 | `result_gate.v1`, run/result/thermal/mesh manifest hash 검증, 전체 pytest 366 passed | 실제 body-fitted 설계 검토 case와 GCI 산출물 |
| A1 목록/결과 캐시 | 구현·회귀검증 완료 | fingerprint, lock, 원자적 publish, 손상/권한오류 회귀 테스트 | 현장 corpus의 정식 p95 재측정 |
| A0.2 런타임 증적 | 코드·schema·안전 smoke 완료, MPI 런타임 차단 실측 | Ubuntu 24.04 WSL2/Open MPI 4.1.6에서 기본값과 vader=none 모두 `mpirun -np 2 hostname`이 4초 제한 내 rank를 만들지 못함. token-owned 세션 cleanup은 CLEAN | Open MPI 환경 복구 후 동일 smoke PASS 및 실제 OpenFOAM/MPI 증적 재수집 |
| A2 개구부 신뢰성 | 구현·회귀검증 완료 | 부모 단말 preflight, 4-way 사분면 균형 경고, `phi`/mesh sidecar, 전체 pytest 366 passed | 실제 OpenFOAM case에서 보존한 `polyMesh`로 경계면적 및 배기 `phi` 확인 |
| A3 병렬 실행 | 안전 정책·안전 smoke·UI 차단 표시 완료, 실제 MPI는 보류 | 기본값/vader=none 모두 `mpirun -np 2 hostname`이 time-out되어 `MPI_RANK_SPAWN_HANG`; cleanup CLEAN, 공용 solver lock 및 hash·runtime identity가 일치하지 않는 PASS 증적의 직렬 강등 | WSL Open MPI 복구 smoke PASS 뒤 legacy·최초 계산 통합 및 benchmark |
| A4 확인 열원 | 코드·회귀검증 완료, 현장 입력 검증 대기 | raw DXF 열원 차단, 서버 소유 수동입력 provenance, DXF override 원본 handle 보존, legacy/body/GCI/report 공통 열량 계약; A4 집중 회귀 286 passed, 9 FreeCAD 환경 skip | 실제 장비일람표 기반 33대 확인 및 field body-fitted 4+ mesh 검증 |
| A5 수치 품질 | 기본 gate 구현, 실계산 sensitivity job 대기 | 1차 SCREENING_ONLY 분리, 2차 profile·tail residual·peak Courant·phi·y+·Boussinesq gate, forged sensitivity artifact 차단 | 직렬 1차/2차 paired run producer, 실제 solver evidence 및 현장 case 비교 |

`SCREENING_ONLY` 결과는 계산 결과를 검토하는 데 쓸 수 있지만 설계 승인 결과가 아니다. 배기구의 CMH는 현재 압력출구의 **설계 목표값**이며, 실제 배기 유량은 계산 후 `phi` 결과로만 확인한다.

## 이 문서의 구성

- **A부 — 제한 베타 진입 실행계획:** 기존 스크리닝 기능을 더 빠르고 정직하게 만드는 작업이다. 개구부·풍량·발열·결과 상태를 안전하게 표시하고, 실사용 DXF 검증을 시작한다.
- **B부 — 설계 검토 고도화 로드맵:** 복사, 열쾌적, GCI, 실제 현장 검증을 통해 장기적으로 body-fitted 설계 검토로 확장하는 작업이다. A부 완료만으로 ASHRAE 적합 또는 설계 승인 결과를 발행하지 않는다.

---

## 감사로 확인된 기준선

| 항목 | 2026-08-11 확인값 | 이 계획에서의 처리 |
|---|---:|---|
| 케이스 목록 재집계 | 이전 측정 4개 케이스 약 204초, 별도 실측 149초 | 동일 corpus·cold/warm·환경 manifest가 아니므로 어느 값도 수용 기준으로 확정하지 않고 재측정 |
| 대형 케이스 | 1,306,768 cells, 솔버 ClockTime 5,448초(약 90.8분) | legacy 구조격자 병렬화의 기준선 |
| 현재 병렬 기능 | body-fitted 이어계산 일부만 MPI 지원 | legacy와 body-fitted를 별도 검증 |
| 개구부 해상도 | SGI 전체 케이스 30개 중 29개가 한 변 2셀 미만 | 제트·최대유속 인용을 차단하는 preflight 필요 |
| 장비별 발열 | 구조격자 `equip_zones` 및 body-fitted `heat_source`가 이미 존재 | 새 엔진 개발이 아니라 확인·배정·연결 작업 |
| SGI 전체 ΔT | 이론 대비 약 5.94% | 기존 ±5% 공통 게이트를 아직 통과하지 못함 |
| 테스트 수 | 2026-08-11 전체 pytest: 366 passed, 14 skipped, 11 subtests passed | 고정 숫자가 아닌 CI 실행 결과를 기록 |
| 출시 상태 | `product_ready=false` | 기능 완료와 제품 출시를 분리 |

### MPI 런타임 실측 기록 — A3 선행 차단

2026-08-11 WSL Ubuntu 24.04.4/커널 `6.18.33.2-microsoft-standard-WSL2`의 Open MPI 4.1.6에서 `decomposePar -method scotch`는 processor 디렉터리를 만들고 정상 종료했지만, token-owned 안전 wrapper로 재측정한 기본 설정과 `OMPI_MCA_btl_vader_single_copy_mechanism=none` 모두 `mpirun -np 2 hostname`이 4초 안에 hostname/rank를 만들지 못했다. 두 실험은 각각 6.455~7.297초에 timeout으로 끝났고 전용 세션 cleanup은 `CLEAN`이었다. 증거는 `cfd_projects/_release_evidence/mpi_runtime_smoke_default.v1.json`, `mpi_runtime_smoke_vader_none.v1.json`, `runtime_capability.v1.json`에 저장했다. 따라서 이는 OpenFOAM case 또는 분해 방식의 실패가 아니라 **MPI rank-spawn runtime failure**이며, 원인은 아직 확정하지 않는다. `vader=none`은 효과가 없었으므로 영구 설정·shell profile·제품 기본값으로 저장하지 않는다.

## 전역 제약과 결과 계약

1. **스크리닝과 설계 검토를 혼동하지 않는다.** 구조격자 V3 결과는 기본적으로 `screening_only`이며, `design_ready=true`는 body-fitted 검증 게이트를 통과했을 때만 가능하다.
2. **풍량 보존식은 변경하지 않는다.** 실제 경계면적이 `A_snap`이면 패치 유량은 `Q = U × A_snap`이다. `U = Q_design / A_design`을 작은 `A_snap`에 적용해 풍량을 유지한다고 표시하는 방식은 금지한다.
3. **개구부 해상도가 부족하면 결과를 숨기지 말고 제한을 표시한다.** 급기구 제트, 최대유속, 도달거리는 `opening_resolution_ok=false`일 때 설계 판단 지표로 인용하지 않는다.
4. **자동 검출값은 사용자 확인 전까지 입력 후보일 뿐이다.** DXF에서 검출한 장비·디퓨저·벽은 명시적 확인, 좌표 미리보기, 근거를 거친 뒤에만 경계조건 또는 열원으로 사용한다.
5. **열량 계약을 하나로 통일한다.** 모든 열원은 `input_power_w = convective_power_w + radiative_power_w + stored_or_unmodelled_power_w` 관계와 provenance를 기록한다.
6. **결과 등급을 항상 함께 기록한다.**

```json
{
  "run_status": "PASS | WARN | FAIL",
  "convergence_status": "PASS | WARN | FAIL",
  "design_ready": false,
  "citation_status": "SCREENING_ONLY | NOT_EVALUATED | DESIGN_CITABLE",
  "blockers": ["opening_resolution", "gci", "heat_source_confirmation"]
}
```

7. **기계설비 사용자의 기본 화면은 GUI다.** 명령행은 설치·진단용으로만 제공하며, 모든 차단 상태는 원인, 영향, 다음 조치를 한국어로 보여 준다.

---

# A부 — 제한 베타 진입 실행계획

## 제한 베타의 범위와 비범위

**범위:** DXF → 사용자 확인 → 구조격자 스크리닝 → 결과·한계·재실행 조치가 일관되게 연결되는 흐름, 빠른 케이스 목록, 장비별 열원 입력 연결, legacy 구조격자 병렬 실행 검증.

**비범위:** ASHRAE 55 전체 준수 판정, 보증 설계 승인, 일반 실내공기용 P1 복사 기본 적용, LES/DES, GPU/LBM, 자동 장비 kW 추정.

## Sprint A0 — 기준선과 결과 신뢰 계약 고정

### Task A0.1: 실행·수렴·설계검토 상태를 분리한다

**Files:**

- Create: `cfd_result_gate.py`
- Modify: `cfd_report.py`, `cfd_studio.py`, `release_audit.py`
- Test: `tests/test_cfd_result_gate.py`, `tests/test_cfd_report_summary.py`

**Consumes:** solver log, `cfd_case_meta.json`, mesh/run/result manifest, 개구부 preflight 결과.  
**Produces:** `result_gate.v1` JSON과 UI용 상태·차단 사유 목록.

- [ ] **Step 1: 실패하는 결과 등급 테스트를 작성한다.**

```python
def test_closure_pass_without_residual_or_mesh_gate_is_screening_only():
    gate = evaluate_result_gate(
        run_complete=True,
        residuals_pass=False,
        continuity_pass=True,
        energy_pass=True,
        opening_resolution_ok=False,
        gci_pass=False,
    )
    assert gate["citation_status"] == "SCREENING_ONLY"
    assert "opening_resolution" in gate["blockers"]
```

- [ ] **Step 2: 테스트가 실패하는지 확인한다.**

Run: `python -m pytest tests/test_cfd_result_gate.py -q`  
Expected: `ImportError` 또는 assertion failure.

- [ ] **Step 3: 최소 결과 게이트를 구현한다.**

```python
def evaluate_result_gate(*, run_complete, residuals_pass, continuity_pass,
                         energy_pass, opening_resolution_ok, gci_pass):
    convergence = "PASS" if all((residuals_pass, continuity_pass, energy_pass)) else "WARN"
    design_ready = all((run_complete, convergence == "PASS", opening_resolution_ok, gci_pass))
    return {
        "run_status": "PASS" if run_complete else "FAIL",
        "convergence_status": convergence,
        "design_ready": design_ready,
        "citation_status": "DESIGN_CITABLE" if design_ready else "SCREENING_ONLY",
        "blockers": [],
    }
```

`blockers`에는 실패한 정확한 게이트 이름을 넣고, `cfd_report.case_summary()`와 Studio 카드가 동일 JSON만 사용하게 한다.

- [ ] **Step 4: 집계·UI 회귀 테스트를 통과시킨다.**

Run: `python -m pytest tests/test_cfd_result_gate.py tests/test_cfd_report_summary.py tests/test_studio_workflow.py -q`  
Expected: 모든 테스트 통과.

- [ ] **Step 5: SGI 두 케이스의 상태를 재생성하여 보관한다.**

Run: `python release_audit.py`  
Expected: 실행완료 여부와 설계검토 차단 사유가 분리된 JSON/HTML 산출물.

**수용 기준:** 종료 코드 0이나 폐합율만으로 `DESIGN_CITABLE`이 되지 않는다. 잔차, 연속성, 에너지, 개구부, GCI, 입력 확인 상태가 결과와 함께 표시된다.

### Task A0.2: 기준 성능과 런타임 능력을 재현 가능하게 기록한다

**Files:**

- Modify: `cfd_diagnostics.py`, `cfd_capabilities.py`, `cfd_run.py`
- Create: `cfd_projects/_release_evidence/runtime_capability.v1.json`
- Test: `tests/test_cfd_diagnostics.py`, `tests/test_cfd_capabilities.py`

**Consumes:** WSL OpenFOAM 설치 상태와 CPU 정보.  
**Produces:** `mpirun`, `decomposePar`, `reconstructPar`, solver, OpenFOAM 버전, 유효 CPU 수의 capability manifest.

- [ ] **Step 1: MPI 도구 누락을 검출하는 실패 테스트를 작성한다.**
- [ ] **Step 2: `cfd_run` 진단 목록에 세 MPI 명령과 버전을 추가한다.**
- [ ] **Step 3: 동일 대형 케이스를 직렬 기준으로 실행하고, runner 전체 wall time·solver ClockTime·peak RAM·hash를 저장한다.**
- [ ] **Step 4: manifest 내용과 경로를 리그레션 테스트로 검증한다.**

**수용 기준:** 성능 목표는 ‘i5-1240P 12코어’ 같은 하드코딩이 아니라 실제 WSL이 제공한 CPU 수와 manifest를 근거로 계산한다.

---

## Sprint A1 — 목록 응답성 및 결과 캐시

### Task A1.1: `case_summary()`에 원자적 내용 기반 캐시를 구현한다

**Files:**

- Modify: `cfd_report.py`, `cfd_studio.py`
- Create: `cfd_case_cache.py`
- Test: `tests/test_cfd_case_cache.py`, `tests/test_cfd_report_summary.py`

**Consumes:** `cfd_case_meta.json`, 선택된 solver log, 양수 time 디렉터리, 최신 `T/U`, 최근 세 시점 `T/phi`, report 후보.  
**Produces:** case 내부 `cfd_case_summary.cache.v1.json`과 `summary_fingerprint`.

- [ ] **Step 1: cache hit이 field parser를 호출하지 않는 실패 테스트를 작성한다.**

```python
def test_cache_hit_skips_log_and_field_readers(case_dir, monkeypatch):
    first = case_summary(case_dir)
    monkeypatch.setattr(cfd_report, "field_metrics", lambda *_: (_ for _ in ()).throw(AssertionError()))
    second = case_summary(case_dir)
    assert second == first
```

- [ ] **Step 2: 아래 fingerprint를 구현한다.**

```python
def summary_fingerprint(case_dir: Path) -> dict:
    return {
        "schema": "case-summary-cache.v1",
        "meta": file_stat(case_dir / "cfd_case_meta.json"),
        "solver_log": file_stat(select_solver_log(case_dir)),
        "time_names": positive_time_names(case_dir),
        "latest_T": file_stat(latest_field(case_dir, "T")),
        "latest_U": file_stat(latest_field(case_dir, "U")),
        "recent_T_phi": recent_field_stats(case_dir, count=3),
        "report": report_candidate_stat(case_dir),
    }
```

`file_stat()`은 `path`, `size`, `mtime_ns`를 저장한다. cache 파일 자체와 관련 없는 파일은 fingerprint에서 제외한다.

- [ ] **Step 3: 계산 전후 fingerprint가 같은 경우에만 임시 파일을 `os.replace()`로 발행한다.**
- [ ] **Step 4: 손상 cache, 최신 time 생성/삭제, meta 변경, log 변경, T/U/phi 변경의 무효화 테스트를 작성한다.**
- [ ] **Step 5: 동일 4-case corpus로 cold/warm 성능을 재측정한다.** 실행 전 `runtime_capability.v1` hash, Python PID, cache 삭제 여부, 실행 중 CFD job 유무를 기록하고 case별 raw time·p50·p95를 함께 남긴다. 204초와 149초처럼 조건이 다른 수치는 비교하거나 수용 기준에 사용하지 않는다.

Run: `python -m pytest tests/test_cfd_case_cache.py tests/test_cfd_report_summary.py -q`  
Expected: cache hit/invalidation/corruption tests 모두 통과.

**수용 기준:** 동일 corpus·동일 runtime manifest·다른 CFD job 없음 조건에서 cold 결과는 non-cache 결과와 동등하고, 새 프로세스의 두 번째 4-case 목록 로딩 p95가 3초 이하다. cache hit에서는 solver log, `T`, `U`, `phi` parser가 0회 호출된다.

---

## Sprint A2 — 개구부 면적·풍량·제트 신뢰성

### Task A2.1: 개구부 스냅을 부모 단말 기준으로 검증한다

**Files:**

- Modify: `cfd_export.py`, `cfd_advice.py`, `cfd_report.py`, `cfd_studio.py`
- Test: `tests/test_cfd_energy_balance.py`, `tests/test_cfd_advice.py`, `tests/test_cfd_openings.py`

**Consumes:** 원래 단말 `opening_id`, 설계 사각형/면적/CMH, 격자 셀 크기, 실제 boundary face area.  
**Produces:** `opening_preflight.v2`와 패치별·부모 단말별 비교 결과.

- [ ] **Step 1: 4-way 부모 디퓨저의 네 사분면을 합산하는 실패 테스트를 작성한다.**

```python
def test_four_way_area_is_compared_as_one_parent_diffuser():
    result = summarize_opening_group([
        {"opening_id": "sup0", "area": 0.011025, "cmh": 111},
    ] * 4, design_area=0.0441, design_cmh=444)
    assert result["area_ratio"] == pytest.approx(1.0)
    assert result["cmh_ratio"] == pytest.approx(1.0)
```

- [ ] **Step 2: `resolve_openings()`이 아래 정보를 유지하도록 구현한다.**

```json
{
  "opening_id": "sup0",
  "design_area_m2": 0.0441,
  "snapped_area_m2": 0.045,
  "design_cmh": 444,
  "applied_cmh": 444,
  "area_ratio": 1.0204,
  "min_cells_each_side": 2,
  "jet_metrics_citable": true
}
```

- [ ] **Step 3: 면적 비율 ±15%를 만족하지 못하면 다음 중 하나만 허용한다.**

  1. mesh cell size를 줄여 재생성한다.
  2. 검증된 sub-grid momentum-source 모델이 별도 benchmark를 통과한 경우에만 사용한다.
  3. 그 외에는 생성은 가능하되 `opening_resolution_ok=false`로 표시하고 제트·최대유속 판단을 차단한다.

- [ ] **Step 4: 급기와 배기 모두에 면적·질량수지·압력출구 민감도 경고를 표시한다.**
- [ ] **Step 5: 실제 OpenFOAM boundary area와 meta의 `snapped_area_m2`를 비교하는 integration test를 추가한다.**

**수용 기준:** 임의의 설계 면적 기준 속도를 작은 스냅 면적에 적용하지 않는다. 모든 단말은 부모 단위로 풍량·면적·법선운동량을 검증하며, 부족한 해상도는 UI·report·result gate에 같은 차단 사유로 나타난다.

---

## Sprint A3 — legacy 구조격자 병렬 실행

### Task A3.0: WSL Open MPI rank-spawn을 복구·증명한다

**Files:**

- Modify: `cfd_capabilities.py`, `cfd_run.py`, `cfd_diagnostics.py`, `cfd_parallel.py`, `cfd_studio.py`
- Create: `cfd_projects/_release_evidence/mpi_runtime_smoke_default.v1.json`, `mpi_runtime_smoke_vader_none.v1.json`
- Test: `tests/test_cfd_mpi_smoke.py`, `tests/test_cfd_capabilities.py`, `tests/test_cfd_diagnostics.py`, `tests/test_cfd_parallel.py`, `tests/test_studio_workflow.py`

**Consumes:** WSL distribution/version, Open MPI version, effective CPU count, isolated temporary work directory.  
**Produces:** `PASS` 또는 `BLOCKED`인 MPI runtime smoke artifact. `PASS` 전에는 어떤 case generator도 MPI를 선택할 수 없다.

- [x] **Step 1: 기본 runtime identity를 저장했다.** Ubuntu 24.04.4/WSL2, kernel `6.18.33.2-microsoft-standard-WSL2`, Open MPI 4.1.6, 유효 CPU 10개를 artifact에 기록했다.
- [x] **Step 2: solver와 무관한 rank-spawn smoke를 실행했다.** 기본 설정의 `mpirun -np 2 hostname`은 4초 안에 hostname 0줄로 timeout되어 `MPI_RANK_SPAWN_HANG`이 되었고, token-owned process group의 잔류 여부는 `CLEAN`이었다. 2-rank가 PASS하지 못했으므로 4-rank/solver는 의도적으로 실행하지 않았다.
- [x] **Step 3: 후보 transport 설정을 격리 비교했다.** `OMPI_MCA_btl_vader_single_copy_mechanism=none`도 같은 2-rank smoke에서 timeout되었다. 이 설정은 전역 shell profile·Windows 환경변수·제품 기본값에 저장하지 않았다.
- [ ] **Step 4: rank-spawn PASS 뒤에만 OpenFOAM smoke를 실행한다.** 4 rank `decomposePar` → solver → `reconstructPar`이 시간 제한 안에 끝나고, solver log·rank 수·processor 보존/정리 상태를 artifact에 기록한다.
- [x] **Step 5: 실패를 안전하게 닫았다.** timeout 뒤 token-owned 세션의 cleanup은 `CLEAN`으로 확인했고, 결과는 `MPI_RANK_SPAWN_HANG`/`BLOCKED`로 기록했다. `runtime_capability.v1`의 `parallel_runtime_ready=false`와 Studio의 직렬 해석 안내를 유지한다. Studio의 **안전 제한 MPI 병렬 재점검**은 일반 CFD·격자검증·field continuation·직접 body-fitted 실행과 같은 cross-process `cfd_solver.lock`을 사용하며, FreeCAD probe를 다시 기다리지 않고 OpenFOAM/MPI 증적만 갱신한다. 저장된 `PASS`는 smoke artifact hash, baseline rank scope, override 없음, WSL/Open MPI identity, 현재 명령 경로·버전·distro가 모두 일치할 때만 인정하며, 하나라도 달라지면 `NOT_RUN`/직렬로 강등한다. 과거 수동 kill -9 기록은 원인 분석의 참고로만 보존한다.

**수용 기준:** `hostname`과 OpenFOAM smoke가 모두 PASS하고 잔류 프로세스가 없으며, 동일 WSL runtime에서 artifact가 재현 가능할 때만 `mpi.execution_smoke=PASS`다. `decomposePar` 성공만으로는 MPI 사용 가능으로 판정하지 않는다.

---

### Task A3.1: 두 해석 경로가 공유하는 병렬 실행 정책을 만든다

**Files:**

- Create: `cfd_parallel.py`
- Modify: `cfd_export.py`, `cfd_physics.py`, `cfd_run.py`, `cfd_diagnostics.py`
- Test: `tests/test_cfd_parallel.py`, `tests/test_cfd_physics.py`, `tests/test_cfd_energy_balance.py`, `tests/test_cfd_safety.py`

**Consumes:** runtime capability manifest, cell count, case type (`legacy_steady`, `body_fitted_initial`, `body_fitted_restart`).  
**Produces:** generator가 사용 가능한 `ParallelExecutionPlan`과 `parallel_run.v1.json`.

```python
@dataclass(frozen=True)
class ParallelExecutionPlan:
    mode: Literal["serial", "mpi"]
    ranks: int
    decomposition: Literal["scotch", "hierarchical", "simple"] | None
    fallback_chain: tuple[str, ...]

def choose_parallel_plan(case_kind: str, cell_count: int,
                         capabilities: dict) -> ParallelExecutionPlan: ...
```

- [ ] **Step 1: 작은 케이스는 직렬, 대형 legacy case는 MPI 계획을 반환하는 실패 테스트를 작성한다.**
- [ ] **Step 2: `cfd_export.gen_allrun()`에 `decomposePar → mpirun → reconstructPar` 경로를 추가한다.**
- [ ] **Step 3: body-fitted 최초 계산과 이어계산도 같은 정책 객체를 사용하게 한다.**
- [ ] **Step 4: 폴백을 다음처럼 제한한다.**

  - `decomposePar` 실패: `scotch → hierarchical → simple` 순으로 새 processor 디렉터리에서 재시도한다.
  - MPI launch 실패: 원본 checkpoint 또는 새 case에서 직렬 재시도하고 `fallback_reason`을 기록한다.
  - solver FATAL, 발산, reconstruct 실패: 직렬 성공으로 위장하지 않고 실패로 보고한다.
  - reconstruct 성공 전에는 `processor*`와 원본 time directory를 삭제하지 않는다.

- [ ] **Step 5: 1/2/4/6/8 ranks를 각 3회 실행해 median wall time, p95 wall time, peak RAM, field hash를 기록한다.**
- [ ] **Step 6: 실제 OpenFOAM 실행 smoke를 `scotch`, `hierarchical`, `simple` 각각으로 남긴다.**

**수용 기준:**

- 130만 셀, 1000 iteration legacy case에서 전체 runner wall time이 25분 이하이면서 직렬 대비 3배 이상 빨라진다.
- 직렬 대비 평균온도 ≤0.1 K, 최대온도 ≤0.2 K, 패치 유량 ≤1%, 에너지 폐합 ≤1%p, 체적가중 상대 L2(U/T) ≤0.5%를 만족한다.
- 실제 유효 CPU 수를 넘는 rank를 선택하지 않고, 선택 근거·분해 방식·폴백 여부가 manifest와 report에 남는다.

---

## Sprint A4 — 확인된 장비별 열원 연결

### Task A4.1: 기존 장비 발열 기능을 하나의 열원 계약으로 연결한다

**Files:**

- Modify: `geometry_v2.py`, `geometry.v2.schema.json`, `cfd_studio.py`, `cfd_export.py`, `cfd_physics.py`, `cfd_report.py`
- Modify: `cfd_occ_worker.py`, `surface_manifest.v1.schema.json`, `run_manifest.v1.schema.json`
- Test: `tests/test_geometry_v2_contract.py`, `tests/test_cfd_energy_balance.py`, `tests/test_cfd_physics.py`, `tests/test_studio_workflow.py`, `tests/test_body_fitted_report.py`

**Consumes:** 사용자가 확인한 `geometry.v2.semantic.role=heat_source` 요소와 kW/대류분율/근거.  
**Produces:** legacy `obstacles` adapter, body-fitted surface/volume heat-source adapter, heat-source provenance.

```json
{
  "id": "AHU-01",
  "semantic_role": "heat_source",
  "bbox_m": [1.0, 2.0, 2.8, 3.0, 2.2],
  "input_power_w": 5000,
  "convective_fraction": 0.8,
  "radiative_fraction": 0.2,
  "source_type": "user_confirmed",
  "evidence": "equipment_schedule:M03-001"
}
```

- [x] **Step 1: 미확인 DXF 장비가 자동 열원이 되지 않는 실패 테스트를 작성한다.**
- [x] **Step 2: UI에서 장비별 위치·kW·대류분율·근거를 검토/확정하게 한다.**
- [x] **Step 3: legacy와 body-fitted adapter가 동일 `input_power_w` 계약을 수신하게 한다.**
- [x] **Step 4: 균질 바닥 발열과 장비별 발열을 동시에 켜면 hard error를 발생시킨다.**
- [ ] **Step 5: SGI 33대 검출 케이스를 확인된 열원 수·개별 kW가 표시되는 케이스로 재생성한다.**

**현장 입력 보류 사유:** 현재 받은 SGI 자료는 장비일람표 일부와 로비 DXF이며, 33대 전체의 위치·개별 kW·대류/복사 분율·근거를 사용자가 확인한 확정 목록은 아직 없다. 프로그램은 자동 추정을 열원으로 승격하지 않으며, 이 목록이 확보될 때만 Step 5를 수행한다.

**수용 기준:** confirmed `heat_source` N개가 정확히 N개 zone/patch로 추적되고, `Σ input`, `Σ convective`, `Σ radiative`가 ±0.1% 내에서 열량 계약과 일치한다. report는 “33대 검출”과 “N대 확인·열원 적용”을 구분해 표시한다.

---

## Sprint A5 — OpenFOAM 수치 품질·보존 검증

### Task A5.1: 스크리닝 안정화와 설계 검토용 수치 증적을 분리한다

**근거:** 현재 body-fitted 열해석은 `buoyantBoussinesqPimpleFoam` + k-ω SST, `blockMesh → snappyHexMesh`, 급기 `flowRateInletVelocity`, 배기 `pressureInletOutletVelocity`/`inletOutlet`을 사용한다. 이 조합은 적절한 출발점이지만, 기본 열해석은 1차 upwind·uncorrected gradient이고 PASS 판정이 tail residual·continuity·실제 전체 `phi` 수지를 강제하지 않는다. 따라서 현재 기본 프로필은 안정화/스크리닝으로만 표시한다.

**Files:**

- Modify: `cfd_physics.py`, `cfd_result_gate.py`, `cfd_report.py`, `cfd_studio.py`, `run_manifest.v1.schema.json`
- Create: `cfd_numerics.py`, `numerical_sensitivity.v1.schema.json`, `tests/test_cfd_numerics.py`
- Test: `tests/test_cfd_physics.py`, `tests/test_cfd_result_gate.py`, `tests/test_body_fitted_report.py`, `tests/test_studio_workflow.py`

**Contracts:**

- `stabilized_first_order_v1`: 1차 안정화/스크리닝 전용, `design_eligible=false`.
- `design_limited_second_order_v1`: `linearUpwind`/`limitedLinear`과 limited laplacian/snGrad, mesh 기반 비직교 보정 횟수를 기록하는 설계 검토 후보.
- `numerical_sensitivity.v1`: 동일 mesh·thermal input에서 1차/2차 결과의 온도, 최대 유속, patch `phi`, 에너지 수지 차이와 모든 입력 hash를 보존한다.

- [x] **Step 1: max non-orthogonality 20/45/65 기준으로 필수 `nNonOrthogonalCorrectors`와 설계 승격 가능 여부를 판정하는 실패 테스트를 작성한다.**
- [x] **Step 2: thermal `fvSchemes`/`fvSolution`을 profile 기반으로 생성하고, 현재 1차 profile을 명시적으로 SCREENING_ONLY로 기록한다.**
- [x] **Step 3: 마지막 time window의 Ux/Uy/Uz/p_rgh/T/k/omega final residual, continuity, Courant, `beta·|T−TRef|`를 검사한다. 증적이 없으면 `NUMERICAL_EVIDENCE_MISSING`으로 설계 승격을 막는다.**
- [x] **Step 4: 모든 terminal patch의 실제 `phi`를 합산해 체적유량 불일치 ≤0.1%, 배기 역류량, solver-phi 열수지 근거를 기록한다. 설계 CMH/T fallback은 SCREENING_ONLY다.**
- [x] **Step 5: 동일 조건의 1차/2차 sensitivity artifact 없이는 `NUMERICAL_SENSITIVITY_PENDING`으로 남기고, report/Studio에서 다음 조치를 안내한다.**

**현재 보류 범위:** 실제 1차/2차 paired solver job 생성기는 아직 별도 작업이다. 따라서 사람이 만든 `numerical_sensitivity.json`은 형식이 맞더라도 `NUMERICAL_SENSITIVITY_ARTIFACT_UNVERIFIED`로 설계 승격을 차단한다.

**A5.1 구현 상태 (2026-08-12):**

- `cfd_numerics.validate_effective_openfoam_numerics()`가 run manifest의 선언만 믿지 않고, 실제 `fvSchemes`의 `div(phi,U/T/k/omega)`, limited laplacian/snGrad 및 `fvSolution`의 PIMPLE 보정 횟수를 profile과 대조한다. 2차라고 선언했어도 `upwind` 파일을 해시까지 새로 기록한 경우 `SEMANTIC_*` provenance blocker로 차단한다.
- body-fitted 결과 gate와 GCI 입력 gate는 같은 읽기 전용 provenance 검사를 사용한다. 따라서 GCI 전에 각 격자 case의 2차 profile, 수치 품질, 현재 OpenFOAM 파일 hash/의미가 모두 맞아야 한다.
- 2차 설계 profile은 `runTimeModifiable false`로 생성한다. 이어 계산은 새 실행 전에 controlDict/fvSchemes/fvSolution을 다시 생성·해시하므로, 실행 중 스킴을 바꿨다가 복원하는 방식으로 증적을 우회할 수 없다. 1차 스크리닝 profile의 기존 `true` 동작은 유지한다.
- G2 출시 증거는 self-declared PASS가 아니라 v3 contract, 4개 고유 case, 각 case의 현재 run/result/mesh/thermal hash, GCI job의 benchmark geometry hash를 모두 요구한다.

### Task A5.2: 직렬 1차/2차 paired sensitivity job을 불변 증적으로 만든다

**구현 상태 (2026-08-12):** 실행 전 foundation을 `cfd_numerical_sensitivity_job.py`에 구현했다. 명시적 점유영역 selector, profile-free physical tree, 고정 직렬 child 역할(`baseline_first_order` / `variant_second_order`), 각 child의 seed snapshot을 동결한다. pair input fingerprint와 job ID에는 두 seed hash가 모두 포함되므로 seed가 바뀌면 새 job ID가 필요하다. `PENDING_SOLVER_EVIDENCE` manifest에는 QoI 정의와 허용오차만 넣고, 계산된 QoI 값·run/result hash·PASS 판정은 넣지 않는다. 다음 작업에서 실제 직렬 runner와 실행 후 verifier를 구현하기 전까지 sensitivity 결과를 PASS로 승격할 수 없다.

**선행 조건:** 현재 `cfd_post` summary의 평균은 전체 공기체적 기준이다. 이를 재실자 영역 평균으로 잘못 라벨링하면 안 된다. paired job을 만들기 전에 사용자가 확인한 `occupant_zone.v1`(XY 범위, Z 하한/상한, 제외 영역, 좌표계, 근거) selector를 별도 artifact로 고정·해시하고, selector가 없으면 `NOT_EVALUATED`로 둔다.

**설계 원칙:**

- MPI는 사용하지 않는다. WSL Open MPI rank-spawn smoke가 PASS 되기 전에는 두 case 모두 serial only다.
- 원본 case를 이어서 덮어쓰지 않는다. 같은 mesh/물리 입력 snapshot에서 `baseline_first_order`와 `variant_second_order`를 각각 새 디렉터리에 생성하고, solver·mesh·thermal/result manifest hash를 pair artifact에 기록한다.
- `physical_input_hash`는 geometry, mesh, terminal, heat, thermal condition, occupant selector만 포함하고 numerical profile은 제외한다. 두 측의 mesh/physical hash는 정확히 같고 run hash는 달라야 한다.
- 기존 `numerical_sensitivity.v1`은 화면용 구조 요약으로 유지한다. 설계 승격용 원본 증적은 별도 `cfd_numerical_sensitivity_job.v1`에 두 run의 종료/잔차 tail/peak Courant/continuity/phi/solver-phi energy evidence, frozen physical tree hash, selector 및 side artifact hash를 기록하고 재계산한다. JSON의 PASS 문구만으로는 승격하지 않는다.
- QoI는 전체 체적 평균과 재실자 영역 평균을 명확히 구분한다. 최소: 재실자 영역 평균 온도·속도, 배기 온도상승, 실제 terminal phi 수지이며, 각각 시간/체적 가중 방식과 허용 오차를 artifact에 명시한다.

**수용 기준:** 설정 변경만 다른 두 직렬 run이 실제로 완료되고, frozen input/selector 및 모든 artifact hash가 현재 파일과 일치하며, 두 QoI 값·차이·허용 오차를 재계산할 수 있을 때만 외부 `cfd_numerical_sensitivity_job.v1`이 `PASS`를 쓸 수 있다. 기존 `numerical_sensitivity.v1`은 이 외부 증적을 요약할 때만 `PASS`로 쓸 수 있다. 그렇지 않으면 현 상태처럼 `NUMERICAL_SENSITIVITY_ARTIFACT_UNVERIFIED` 또는 `NOT_EVALUATED`로 유지한다.

**수용 기준:** body-fitted 결과가 설계 검토로 승격되려면 mesh PASS, Boussinesq 범위, 실제 phi 질량수지, tail residual/continuity, Courant, solver-phi 열수지 및 1·2차 sensitivity가 모두 PASS여야 한다. `thermophysicalProperties`/perfectGas 전환은 별도 solver 계열·검증 작업이며, 현 Boussinesq 경로에는 `transportProperties`가 올바른 계약이다.

---

## 제한 베타 진입 게이트

아래 항목은 모두 통과해야 `limited_beta_ready=true`로 표시할 수 있다.

1. 전체 pytest 실행 결과와 skip 사유를 CI 산출물로 남긴다.
2. cache cold/warm/invalidation/corruption 테스트와 4-case warm p95 ≤3초를 충족한다.
3. 개구부 부족 해상도 case가 제트 설계 판단을 차단하고, 개선 mesh case는 정확한 parent-level 유량을 보인다.
4. A3.0의 rank-spawn runtime smoke가 먼저 PASS하고, 그 뒤 legacy와 body-fitted 각각 최소 1개 실제 MPI smoke artifact가 있다.
5. confirmed heat source 수·합계·근거가 report와 manifest에서 일치한다.
6. `release_readiness.v1`을 새로 계산해 환경, G2, 실제 DXF, 설치·복구, UAT 상태를 정직하게 표시한다. 하나라도 미통과면 beta 상태는 `BLOCKED`다.
7. A5 수치 품질 gate가 1차 결과를 `SCREENING_ONLY`로 분리하고, 설계 검토 결과에는 실제 phi 수지·tail residual/continuity·수치 sensitivity 증적을 요구한다.

---

# B부 — 설계 검토 고도화 로드맵

## Phase B1 — body-fitted 복사 모델 검증

### Task B1.1: 표면간 복사를 body-fitted 경로에만 도입한다

**Files:**

- Modify: `cfd_physics.py`, `cfd_templates/`, `cfd_report.py`, `cfd_result_gate.py`
- Create: `cfd_radiation.py`, `tests/test_cfd_radiation.py`, `cfd_benchmarks/radiation/`

**Architecture:** 일반 실내공기는 광학적으로 얇은 비참여 매질로 취급하므로, 기본 후보는 body-fitted 표면 geometry의 `viewFactor`다. P1은 흡수계수와 광학두께 근거가 있는 참여매질 benchmark에서만 선택 가능한 옵션으로 둔다.

**현재 B1 안전 상태 (2026-08-12):**

- 구현: 일반 현장 body-fitted builder는 `radiation_modelled=true`를 거부한다. `cfd_radiation.validate_radiation_input()`은 body-fitted 이외 engine, 단일 wall patch, 누락된 방사율·재질 출처·열경계조건을 거부한다.
- 구현: `cfd_benchmarks/radiation/two_plate/reference.json`에 두 평행판 폐쇄형 해(661.543682 W/m²)와 `qr` 부호 규약을 고정했다. `cfd_radiation.build_two_plate_view_factor_case()`는 v2606 `viewFactor` 입력과 **serial-only** `Allrun.serial`을 독립·빈 디렉터리에만 생성하고, 현장 case 또는 기존 OpenFOAM case를 덮어쓰지 않는다.
- 구현: `collect_two_plate_view_factor_evidence()`는 실제 `constant/F`와 0 이외 시간의 `qr`가 있을 때만 행합·상호성·patch별 순복사열·내부 복사열수지·기준 열유속 및 방향을 검증한다. `validate_radiation_manifest()`도 JSON 요약만 믿지 않고 해당 benchmark 디렉터리에서 증적을 다시 수집해 일치 여부를 확인한다. 입력 source hash가 달라지거나 결과가 불완전하면 `NOT_EVALUATED`로 닫는다. `G`는 v2606 viewFactor의 보장 출력으로 가정하지 않는다.
- 구현: 현장 결과 gate는 `radiation_modelled=true`를 `radiation_project_integration_pending`으로 항상 차단한다. benchmark manifest를 복사해도 `DESIGN_CITABLE`로 승격될 수 없다.
- 미구현/차단: 실제 OpenFOAM serial benchmark 실행, 현장 DXF/OCC surface 분할, production radiation 및 ON/OFF 열수지 비교다. 현 DXF/OCC 경로는 외피를 단일 `wall` patch로 평탄화하므로, 이를 해결하기 전에는 production radiation을 활성화하지 않는다.

- [x] **Step 1: `radiation_modelled=true`가 구조격자 스크리닝 case에서는 거부되는 테스트를 작성한다.**
- [x] **Step 2: benchmark 입력에서 모든 참여 표면의 방사율, 출처, 열경계조건과 분리 mesh patch를 검증하는 contract를 구현한다. 현장 DXF surface schema 연동은 Step 3 전 선행한다.**
- [x] **Step 3: `radiationProperties`, `qr` 회수와 patch별 순복사열 적분을 일관되게 생성한다. `G`는 설치본·모델에서 실제 생성될 때만 보조 증적으로 사용한다.**
- [ ] **Step 4: 표준 enclosure benchmark로 view-factor 합, 상호성, 총 복사열수지를 검증한다.** 정적 collector 회귀는 완료했지만, 실제 OpenFOAM serial 실행 증적은 아직 없다.
- [ ] **Step 5: ON/OFF 비교에는 총열량, 외부경계 순열유속, 공기/고체 축열을 함께 보고한다.**

**수용 기준:** 복사 ON은 단순히 폐합 90~110%로 통과하지 않는다. `input = exhaust enthalpy + external heat flux + storage` 계약, 비영(非零) `qr`, patch별 내부 복사 플럭스 합, reference benchmark 오차를 모두 산출한다. `G`는 해당 OpenFOAM radiation model이 실제로 생성한 경우에만 보조 증적으로 쓴다.

## Phase B2 — 열쾌적 및 환기 지표

### Task B2.1: 표준 기반의 열쾌적 스크리닝 artifact를 만든다

**Files:**

- Create: `cfd_comfort.py`, `comfort_manifest.v1.schema.json`, `tests/test_cfd_comfort.py`
- Modify: `cfd_post.py`, `cfd_report.py`, `cfd_advice.py`, `cfd_studio.py`, `cfd_result_gate.py`

**Consumes:** body-fitted design-ready result, occupant profile, RH, met, clo, posture, external work, air-speed method, MRT source, 점유 시간, source evidence.  
**Produces:** `comfort_manifest.v1`과 `PASS | FAIL | NOT_EVALUATED` 열쾌적 스크리닝.

```json
{
  "standard": {"id": "ISO 7730", "edition": "2025", "method": "PMV_PPD"},
  "ashrae_reference": {"id": "ASHRAE 55", "edition": "2023", "method": "standard"},
  "applicability": "regularly_occupied",
  "mrt_source": "view_factor_surface_temperature",
  "result_gate_hash": "sha256:...",
  "comfort_status": "NOT_EVALUATED"
}
```

- [ ] **Step 1: design-ready, hash, FTT, energy history 중 하나라도 빠지면 `NOT_EVALUATED`가 되는 실패 테스트를 작성한다.**
- [ ] **Step 2: nonuniform mesh에서 cell-count 평균과 체적가중 평균이 달라지는 테스트를 작성한다.**
- [ ] **Step 3: 유체·확정 실·점유영역 mask를 사용해 체적/시간가중 p05/p50/p95를 계산한다.**
- [ ] **Step 4: PMV/PPD를 복수의 참조 벡터와 비교해 PMV ±0.01, PPD ±0.5 percentage-point 이내인지 검증한다.**
- [ ] **Step 5: GUI에 공간 용도, RH, met, clo, 자세, MRT 출처, 가정 근거, 점유영역 미리보기, reviewer sign-off를 추가한다.**
- [ ] **Step 6: `εT`를 ‘열제거 온도효율’로 표기하고, IAQ 효과는 별도 age-of-air/passive scalar 해석 없이는 환기효율이라고 부르지 않는다.**

**수용 기준:** report는 “ASHRAE 55 전체 적합”이라는 자동 문구를 쓰지 않는다. 국부 불쾌감, adaptive method, MRT, 적용성 중 미평가 항목은 각각 `NOT_EVALUATED`로 표시하고, 결과 기반 권고는 design-ready gate 이후에만 나온다.

## Phase B3 — 설계 인용과 제품 출시 검증

### Task B3.1: 현장 검증·GCI·사용자 UAT를 release gate에 연결한다

**Files:**

- Modify: `release_audit.py`, `cfd_gci.py`, `uat_acceptance.py`, `field_acceptance.py`
- Create: `tests/test_release_comfort_gate.py`

- [ ] **Step 1: GCI, run manifest, result hash, comfort manifest hash가 불일치하면 design-citable report를 거부하는 테스트를 작성한다.**
- [ ] **Step 2: 서로 다른 용도의 실제 DXF 3종을 geometry 확인→mesh→solver→report까지 실행한다.**
- [ ] **Step 3: 기계설비 사용자 3명이 다음을 수행하는 관찰형 UAT를 진행한다.**

  1. DXF를 넣고 공간·디퓨저·장비·풍량·열원을 확인한다.
  2. 계산 상태와 제한 사유를 이해한다.
  3. `SCREENING_ONLY`, `NOT_EVALUATED`, `DESIGN_CITABLE`의 차이를 설명한다.
  4. 보고서에서 다음 조치를 찾고 재실행한다.

- [ ] **Step 4: UAT 결과, 설치·복구, 실제 DXF, GCI, 환경, comfort gate를 `release_readiness.v1`에 합산한다.**

**수용 기준:** 제품 출시 선언은 `product_ready=true`인 새 release evidence가 있을 때만 가능하다. 기본 사례가 물리적으로 PASS일 필요는 없지만, 항상 정직한 `PASS`, `FAIL`, `NOT_EVALUATED`와 다음 조치를 제공해야 한다.

---

## 단계별 의존 관계

```text
A0 결과 계약·기준선
  └─ A1 캐시 ─ A2 개구부 preflight ─ A3 병렬 ─ A4 확인된 열원
                                               └─ B1 body-fitted 복사
                                                   └─ B2 열쾌적 artifact
                                                       └─ B3 GCI·현장 DXF·UAT 출시 게이트
```

## 구현 순서와 검토 지점

1. A0와 A1 완료 후: 목록 로딩과 결과 상태가 일관적인지 기계설비 담당자 화면에서 검토한다.
2. A2와 A3 완료 후: 기존 SGI와 synthetic diffuser case에서 풍량·면적·병렬 결과를 비교한다.
3. A4 완료 후: 실제 장비일람표 기반 kW를 사용자 확인 없이 자동 반영하지 않는지 검토한다.
4. B1 완료 후: 복사 모델이 standard benchmark를 통과한 경우에만 B2의 MRT 입력으로 사용한다.
5. B2와 B3 완료 후: 표준 인용 문구와 출시 선언을 담당 검토자가 승인한다.

## 계획 자체 검토 결과

- 기존 계획의 ‘개구부 면적만 보정하고 설계면적 기준 속도를 쓰는’ 대안은 질량보존에 맞지 않아 삭제했다.
- 기존 계획의 일반 실내 P1 기본 적용은 제거하고, body-fitted view-factor 검증 단계로 이동했다.
- 이미 존재하는 `equip_zones`/`heat_source` 기능은 새 엔진으로 중복 개발하지 않고, 입력 확인·adapter·열량 계약 작업으로 재정의했다.
- 캐시는 모든 후속 비교·검증 시간을 줄이므로 병렬화보다 먼저 배치했다.
- PMV/PPD는 표준 적합 판정이 아니라 조건부 열쾌적 스크리닝으로 한정했고, `NOT_EVALUATED`를 정상 결과 상태로 정의했다.
- 제품 출시 게이트는 기능 테스트와 분리하고, GCI·실제 DXF·설치복구·기계설비 사용자 UAT를 다시 포함했다.
