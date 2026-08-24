# MEP CFD Studio — Exa 벤치마크 및 개선 권고

조사일: 2026-08-24  
범위: MEP CFD Studio의 DXF → 3D → OpenFOAM → 브라우저 GUI 흐름을, HVAC 실무 CFD 제품과 실내공기 유동 연구 근거에 대조했다.

## 결론

Studio가 다음 단계에서 이겨야 할 지점은 ‘CFD를 실행하는 버튼’이 아니라 **현장 엔지니어가 결과를 신뢰하고 승인할 수 있게 만드는 증거 흐름**이다. 현 코드에는 body-fitted mesh, `checkMesh`, GCI, result gate, provenance 같은 좋은 기반이 이미 있다. 우선순위는 이들을 실제 현장 DXF와 측정값으로 검증하고, 사용자에게 명확한 신뢰도 상태와 Design → Scenario → Run 비교로 노출하는 것이다.

PMV/PPD, IAQ/CO2, 복사·CHT, 대리모델은 가치가 크지만 현재 기반의 엔드투엔드 수용 증거와 경계조건 QA보다 먼저 ‘완료’로 취급하면 안 된다.

## 조사 방법과 신호 품질

- Exa 검색 17회, 검색 결과 154건을 검토했다. 경쟁 제품, 오픈소스/solver workflow, 검증·격자·실내환경 논문이라는 3개 독립 workstream으로 나눴다.
- Exa `web_fetch`로 공식 문서, 공개 저장소, 논문/DOI 랜딩 페이지 21건 이상을 재확인했다.
- 제품 페이지는 구현 가능한 UX 패턴의 근거로만 사용했다. 성능·시간 절감 수치는 독립 검증이 없는 마케팅 주장으로 설계 판단에 쓰지 않았다.
- 논문은 기능의 필요 조건과 검증 게이트의 근거로 사용했으며, 한 연구의 수치를 모든 프로젝트에 일반화하지 않았다.

## 현재 checkout의 검증된 출발점

| 영역 | 이미 확인된 기반 | 해석 시 주의할 경계 |
|---|---|---|
| 형상·입력 | ASCII DXF의 provenance, zone/설비/단말 후보 추출, `geometry.v2 → FreeCAD/OCC air solid/STL → snappyHexMesh` body-fitted 경로 | DXF는 CMH·단말 역할·열원의 진실값을 담지 않는다. 사용자 확인이 필수다. |
| 물리·mesh | 등온 및 Boussinesq 열부력 RANS, `kOmegaSST`, `blockMesh → snappyHexMesh → checkMesh`, 메시·자원 게이트 | production 복사·벽체 전도·외피 열경계/CHT는 확인되지 않았고, 병렬 MPI는 실제 운용 증거가 없다. |
| QA·결과 | residual/continuity/y+, 개구부 실제 유량, 에너지 수지, 3-격자 GCI·시간창 GCI, Annex 20 단일 프로파일 benchmark, result/provenance gate | solver 완료/`mesh OK`/테스트 통과는 설계 인용 가능 또는 현장 검증 완료와 동치가 아니다. |
| UX·운영 | 웹 Studio의 DXF 자동변환 → 사용자 검토 → 3D 검토 → 실행 흐름, WSL/OpenFOAM capability 탐지와 직렬 job lock | 현장 DXF 3건 및 사용자 UAT의 재현 가능한 release evidence는 아직 완결됐다고 볼 수 없다. |

이 기준선은 읽기 전용 감사에서 `README.md`, `cfd_mesh.py`, `cfd_physics.py`, `cfd_validate.py`, `cfd_result_gate.py`, `cfd_studio.py`를 대조해 작성했다. 작업트리는 수정·미추적 파일이 있어, 현 상태를 깨끗한 release 기준선으로 간주하지 않는다.

## 유사 제품에서 가져올 패턴

| 제품/프로젝트 | 공식 근거 | 검증된 패턴 | Studio에 적용할 방식 |
|---|---|---|---|
| Autodesk CFD | [Design Study Automation](https://help.autodesk.com/cloudhelp/2024/ENU/SimCFD-Learning/files/GUID-A31B38D1-7C94-440F-8634-98C13CA8C540.htm) | CAD 변경을 Design, 조건 변경을 Scenario로 나누고 template/rule, 실행 관찰, 결과 비교를 제공 | case/job 중심 상태를 **Design → Scenario → Run** 계층으로 정리하고, `geometry.json` 객체 ID를 HVAC template/BC rule에 안정적으로 연결한다. |
| Autodesk CFD AEC workflow | [geometry](https://help.autodesk.com/cloudhelp/2024/CHT/SimCFD-UsersGuide/files/GUID-732BB959-E1BC-4EC7-A9C3-4D7997596365.htm), [meshing](https://help.autodesk.com/cloudhelp/2024/CHT/SimCFD-UsersGuide/files/GUID-E985B666-41E4-40EB-832D-FADF734707AC.htm) | 공기 체적, gap/interference, 작은 feature, diffuser/heat-source refinement를 별도 확인 | 기존 mesh gate 위에 **air-volume·누설·간섭·patch 역할·refinement 검토 화면**과 승인 이력을 추가한다. |
| Flow360 | [WebUI workflow](https://docs.simulation.cloud/projects/flow360/en/stable/user_guide/WorkflowsInterfaces/WorkflowsInterfaces.html) | geometry/mesh 품질 시각화, setup Inspector, live residual·CFL·min/max, case 비교와 CSV/image export | `checkMesh` 텍스트만 노출하지 말고, quality metric·residual·probe·경고를 한 화면의 **case health**로 보여준다. |
| CfdOF | [GitHub README](https://github.com/jaheyns/CfdOF) | FreeCAD GUI 안에서 물리, BC, mesh backend, post-mesh check, probes, solver 실행을 연결 | FreeCAD/OCC 경로를 유지하되 mesh backend와 `checkMesh`/probe collector를 교체 가능한 adapter로 유지한다. |
| OpenFOAM `snappyHexMesh` | [official guide](https://www.openfoam.com/documentation/guides/latest/doc/guide-meshing-snappyhexmesh.html) | dirty surface, geometry dry-run, gap/surface/volume refinement, `meshQualityControls` | watertight 변환 성공만 믿지 않고 preflight + dry-run + quality threshold 실패를 fail-closed 처리한다. |
| SimScale Indoor Environment | [HVAC/Indoor Environment](https://www.simscale.com/simulations/indoor-environment/) | PMV/PPD, CO2, age of air, contaminant transport, 여러 환기 시나리오 비교라는 HVAC 업무 언어 | PMV/PPD와 IAQ는 같은 ‘예쁜 contour’가 아니라 입력·검증·표시 상태가 분리된 기능으로 도입한다. 제품 기능 페이지이므로 실측 정확도 근거로 사용하지 않는다. |
| simulationHub Autonomous HVAC CFD | [product page](https://www.simulationhub.com/autonomous-hvac-cfd) | 비전문가용 BIM/import, 다중 design configuration, load/weather scenario, comfort report | 사용자가 CFD 용어를 배우게 하기보다 **급기/배기·열원·점유·날씨·운전조건**을 업무 모델로 입력하게 한다. 마케팅 페이지이므로 UX 참고로만 사용한다. |

## 연구가 요구하는 신뢰도 게이트

| 연구 | 확인된 핵심 | 제품 요구사항 |
|---|---|---|
| [Chen & Srebric (2002)](https://doi.org/10.1080/10789669.2002.10391437) | 실내 CFD의 verification, validation, reporting은 물리 현상, 난류/보조 모델, 수치법, 예측 평가와 한계를 드러내야 한다. | run마다 physics scope, BC, mesh, convergence, comparator, limitation을 자동 수집한 **V&V manifest/report**를 만든다. |
| [Kosutova, van Hooff & Blocken (2018)](https://doi.org/10.1016/j.ijthermalsci.2018.03.001) | 실내 velocity는 grid, inlet velocity, turbulence, near-wall treatment 등에 특히 민감하며, 하나의 RANS 모델이 온도·속도 모두에서 우세하지 않았다. | high-risk 제트/재순환 case에는 단일 모델 ‘정답’ 표현을 금지하고 민감도/전문가 검토 상태를 보여준다. |
| [Baker et al. (2019)](https://doi.org/10.1080/14733315.2019.1667558) | GCI는 체계적 3수준 grid refinement 및 점근성 확인을 요구한다. | 이미 있는 GCI 기능을 고위험 case의 **설계 인용 gate**로 승격하고 QoI 변화율·관측차수·비점근 경고를 보고서에 넣는다. |
| [Hajdukiewicz, Geron & Keane (2013)](https://doi.org/10.1016/j.buildenv.2012.08.027) | 센서/기상 데이터와 민감도 분석을 CFD 보정 절차에 연결했다. | measurement point, TAB/현장 센서 import, 실측-계산 비교, `calibrated`/`uncalibrated` 상태를 추가한다. |
| [van Hooff, Blocken & van Heijst (2013)](https://doi.org/10.1111/ina.12010) | transitional slot mixing ventilation에서 난류 모델 선택이 air-exchange prediction을 크게 바꿀 수 있다. | slot jet·강한 재순환에 risk flag, alternative turbulence model 비교 또는 설계 인용 보류를 둔다. |
| [Motamedi et al. (2022)](https://doi.org/10.1016/j.scs.2021.103397) | 환기 전략의 위해성은 위치·시간에 따라 달라지므로 누적 노출 관점이 필요하다. | IAQ는 평균 농도만 보이지 말고 점유자/호흡영역별 시간 누적 농도 지표를 출력한다. 감염확률은 가정 의존 scenario indicator로 표시한다. |
| [Morozova et al. (2025)](https://doi.org/10.1016/j.buildenv.2025.112533) | multi-fidelity surrogate는 비용을 줄일 수 있으나 적용 범위와 기준 CFD가 전제다. | AI/surrogate는 physics CFD·benchmark·domain-of-validity 경고 뒤에만 넣는다. |

## 권고 로드맵

### P0 — ‘실행됨’에서 ‘검토·재현 가능’으로

1. **현장 수용증거 패키지**: 서로 다른 실제 DXF 3건에 대해 입력 승인, mesh/solver log, TAB 온도·풍량 비교, 결과 report, reviewer sign-off를 연결한다. 모든 run의 입력/solver/환경 hash와 결과를 하나의 immutable manifest로 보존한다.
2. **신뢰도 상태를 UX의 1급 개념으로**: 기존 gate를 `geometry_valid`, `bc_reviewed`, `mesh_checked`, `solver_converged`, `grid_verified`, `benchmark_validated`, `field_calibrated`, `design_ready`로 분리한다. 어떤 상태가 `false`인지와 그 이유를 사용자가 바로 고칠 수 있어야 한다.
3. **물리 경계 승인**: DXF 자동 추론 뒤 air solid, room closure, supply/exhaust, CMH, 열원, 점유역, 평가점, refinement를 3D에서 사람이 확인·수정·승인하게 한다. 자동 추론 confidence와 원본 DXF handle도 보존한다.

### P1 — 반복 설계의 생산성

4. **Design → Scenario → Run + HVAC template**: 형상 변경과 운전조건 변경을 분리한다. 같은 design에서 디퓨저 위치/방향/CMH, 급기온, 점유·날씨를 scenario로 clone하고, 결과는 KPI와 slice/probe로 비교한다. `supply/exhaust/heat-source/window/occupant-zone` template은 geometry ID와 연결한다.
5. **V&V와 field calibration**: Annex 20 단일 프로파일을 벗어나 sidewall jet, 열부력, 복합 단말, obstruction, 현장 TAB의 benchmark matrix로 넓힌다. high-risk scenario는 3수준 GCI 또는 비실시 사유를 요구하고, 실측이 있으면 오차·불확실성·민감도 sweep을 같이 report한다.

### P2 — HVAC 의사결정 지표 확장

6. **Comfort module**: PMV/PPD는 RH, met, clo, MRT, 점유영역과 모델 가정을 입력·검토하지 못하면 `NOT_EVALUATED`로 남긴다. ‘ASHRAE/ISO compliant’라는 출력은 조건과 범위를 만족할 때만 허용한다.
7. **IAQ module**: passive scalar CO2, age of air, contaminant source, exposure integration을 온도 CFD와 별도 feature gate로 추가한다. ACH/풍량/온도만으로 IAQ 성능을 주장하지 않는다.
8. **복사·CHT/외피 모델**: independent two-plate 및 건물 외피 benchmark부터 시작해, production body-fitted case에는 benchmark 통과 뒤 확장한다.

### P3 — 가속 기능은 검증 뒤에

9. **MPI와 surrogate**: 우선 현재 MPI rank-spawn/결과동등성 문제를 benchmark로 해소한다. 그 뒤, 검증된 CFD dataset 안에서만 diffuser placement/CMH 후보를 빠르게 고르는 surrogate를 제공하고, 범위 밖 입력은 CFD 재실행을 강제한다.

## 명확히 미룰 것

- cloud화나 AI를 먼저 추가해 solver/현장 검증의 빈자리를 가리는 일
- residual 감소만으로 설계 승인 또는 기준 적합을 선언하는 일
- PMV/PPD 또는 IAQ 입력·검증이 없는 상태에서 compliance label을 출력하는 일
- 이미 존재하는 GCI/mesh gate를 또 만드는 중복 구현

## 다음 구현 단위 제안

가장 안전한 첫 slice는 **‘Case Evidence & Review Gate’**다. 기존 `cfd_result_gate`, provenance, report를 재사용해 위 P0 상태와 immutable manifest, Studio의 승인 화면, 현장 DXF 1건의 read-only acceptance fixture를 연결한다. 이것이 안정된 뒤 template/scenario 비교, comfort, IAQ 순으로 확장하는 것이 기존 코드와 연구 근거 모두에 맞다.

