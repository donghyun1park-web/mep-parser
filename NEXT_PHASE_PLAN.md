# MEP CFD Studio 개발계획서 — mep-parser 활용과 body-fitted CFD

기준일: 2026-07-22

진행 갱신(2026-07-22 16:20 KST): G2 v3 네 격자 9,374/20,377/47,960/107,991셀은
모두 710.6832초·3.0 FTT까지 solver와 마지막 시간창 정상성 gate를 통과했다. 공식 MARIN
Eça–Hoekstra(2014) 구현과 교차검증해 Richardson 회귀 선택과 표준편차 자유도를 바로잡은 결과,
평균온도 불확실성은 5.01869%, 온도 p95는 109.98076%, 유속 p95는 17.10172%로 최종 메시
불확실성 gate는 `FAIL`이다. 기존 0.350/0.243/0.169 m 결과를 재사용하고 0.118 m만 새로 계산하는
후속 작업 `gci-18e8320a98c4`의 0.118 m 메시 232,842셀 생성, 600회 등온 초기장, 0.05초 열·부력
안정성 시험을 완료했고 3.0 FTT까지 자동 이어 계산 중이다. 실제 외부 현장 DXF와 기계설비 담당자 UAT는 여전히
각각 0/3이므로 최종 제품 준비는 아직 `BLOCKED`다.

## 검토 결론

`mep-parser`는 MEP CFD Studio에 **조건부로 도입할 가치가 있다.** 다만 도입 대상은 기존
`.FCStd`/IFC 결과 자체가 아니라 다음 두 부분이다.

- `dxf_parser.py`의 결정론적 DXF 정규화·레이어/블록 분류·zone/MEP 추출
- `freecad_builder.py`에서 검증된 OCC 기반 압출·Boolean·형상 유효성 검사 개념

현재 `freecad_builder.py`는 BIM 문서 생성기다. `build_spaces()`는 zone을 단순 압출하고 벽·기둥·
장비와 같은 문서에 배치하지만, `공간 - 기둥 - 장비` Boolean으로 단일 공기 체적을 만들지 않는다.
급기·배기구를 비중첩 CFD 경계면으로 분할하거나 OpenFOAM용 multi-region STL을 만드는 기능도 없다.
따라서 기존 FCStd/IFC를 CFD 입력으로 다시 읽지 않고, `geometry.v2`를 입력으로 받는 CFD 전용
FreeCADCmd/OCC worker를 별도로 만든다.

### 재검증한 사실

- 원격 `master`의 `freecad_builder.py`, `mep_gui.py`와 주요 연동 파일은 현재 작업 폴더의 파일과
  동일하다. 코드를 다시 복사하거나 원격 `master`로 로컬 파서를 덮어쓰지 않는다.
- `sample_mep.dxf` 파싱 스모크에서 `pipe=2`, `duct=1`, `tray=1`, `equipment=2`, 경고 0건을
  확인했다.
- 현재 제품 기준선은 `Ubuntu-24.04`, OpenFOAM v2606이며 body-fitted에 필요한 명령과 실제 소형
  계산이 모두 통과한다. v1912는 호환 확인용 legacy 프로필로만 남긴다.
- FreeCAD 1.1.1은 `C:\Program Files\FreeCAD 1.1\bin\FreeCADCmd.exe`에 설치되어 있고 PATH에는
  없다. `cfd_capabilities.py`가 PATH·표준 설치경로·설정값을 통합 탐지하고 절대경로·버전·OCC
  버전을 capability manifest에 기록하도록 구현했다.
- FreeCAD headless 스모크에서 `Part`, `Mesh`, `MeshPart`, `BOPTools.SplitAPI` import와 단일 공기
  Solid Boolean이 동작했다. OCC 결과가 `Compound`여도 유효한 Solid가 정확히 하나면 정규화해
  사용할 수 있다.
- `build_openings()`의 3개 반환값과 2개 unpack 불일치, `_mep_tmp_out.*` 고정 임시파일 충돌
  위험을 수정했다. opening 포함 실제 FreeCADCmd 빌드와 고유 작업폴더 회귀 테스트를 통과했다.
- FreeCAD capability/Boolean 스모크와 opening 포함 BIM build 회귀에 더해 공기영역·named
  surface, `surfaceCheck`, `snappyHexMesh`, 실제 body-fitted solve 및 비정형 후처리 자동 검증까지
  구현했다.

### 재사용·분리 결정

| 기존 구성 | 재사용 | 새 엔진에서 분리하거나 금지 |
|---|---|---|
| `dxf_parser.py` | 단위 정규화, 레이어/블록 분류, zone·기하 추출 | index 기반 ID, 전체 bbox를 방으로 간주하는 폴백 |
| 기존 `geometry.json` | v1 입력 호환과 마이그레이션 | CFD 필수 의미가 없는 v1을 직접 계산에 사용 |
| `freecad_builder.py` | 압출·기초 솔리드·Boolean·`Shape.isValid()` 개념 | Arch 문서, FCStd/IFC를 CFD 형상 원본으로 사용 |
| `mep_gui.py` | 자동분류 결과를 사람이 확인·수정하는 흐름 | 별도 Tkinter UI를 제품 UI에 중복 탑재 |
| `cfd_export.py` | 입력 검증, CMH·열량 계산, 안전한 atomic publish 개념 | porous/구조격자 생성을 body-fitted에 재사용 |
| `cfd_run.py` | WSL 진단, 실행·회수, 실패 처리 | mesh·solve·post를 하나의 불투명한 단계로 실행 |
| `cfd_report.py` | 로그·수렴 판정, HTML 보고서 틀 | `(nx, ny, nz)` reshape와 구조격자 solid mask |
| `cfd_studio.py` | 업로드, 경로 보안, 큐, 프로젝트 UI 셸 | `x0/xL` 같은 좌표축 기반 급배기 지정 |

핵심 결정은 `freecad_builder.py`를 확장해 모든 일을 맡기는 것이 아니라, BIM preview 경로는 유지하고
새 `cfd_occ_worker.py`에서 저수준 OCC 기하 원리만 재사용하는 것이다.

## 1. 오늘 확보한 실행 기준선

이 PC에서 다음 구성이 실제 계산까지 통과했다.

- WSL2, 기본 배포판 `Ubuntu-24.04`
- 일반 실행 사용자 `mepcfd`
- 제품 실행 패키지 OpenFOAM v2606 (v1912는 legacy 호환 프로필)
- 확인한 도구: `blockMesh`, `checkMesh`, `surfaceCheck`, `surfaceFeatureExtract`,
  `snappyHexMesh`, `buoyantBoussinesqSimpleFoam`, `foamToVTK`
- 저장소 소형 케이스: `Mesh OK`, 400 step 정상 종료, 결과 time 100/200/400 회수
- HTML 보고서 생성: 평균 24.8°C, 최고 26.3°C, 최대유속 0.655 m/s,
  최종 global continuity -1.55e-05

위 결과는 설치와 현재 수치 파이프라인의 동작 확인이다. 실제 설비 설계 적합성이나 실제 CAD
형상·경계조건의 정확성을 인증하는 결과는 아니다.

### 개발 진행 상태

- [x] WSL 배포판 자동 선택과 OpenFOAM v1912 호환성 검사
- [x] 현재 솔버 및 body-fitted 메시 도구별 진단
- [x] 프로젝트 로컬 `capability_manifest.json` 기록
- [x] 대시보드 한국어 상태 표시와 `환경 다시 검사` 버튼
- [x] 64셀 기준 케이스의 원클릭 실제 계산 수용 테스트
- [x] 관리자 권한 자동 요청·재시작 후 재개를 포함한 원클릭 WSL/Ubuntu-24.04/OpenFOAM v2606 설치·업데이트
- [x] FreeCADCmd 1.1 절대경로 자동탐지, 버전/OCC/모듈 capability 기록
- [x] BIM builder의 opening 반환계약·고정 임시파일 P0 수정과 실제 headless 회귀 테스트
- [x] `geometry.v2` 의미 계약·JSON Schema·v1 변환기·semantic diff
- [x] DXF handle 기반 안정 ID와 병합 후 source provenance 보존
- [x] Studio 정밀 3D CFD 준비도 진단(빠른 검토 해석은 계속 허용)
- [x] 격리형 `cfd_occ_worker.py`와 프로젝트 로컬 원클릭 OCC 실행
- [x] 확정 공간 압출·기둥/장비 차감·급배기 face 분할
- [x] m 단위 multi-region STL·BREP·FCStd·`surface_manifest.v1` 생성
- [x] OCC watertight/manifold 검사와 OpenFOAM v1912 `surfaceCheck` 실검증
- [x] P3A 독립 `Allmesh`와 WSL staging·실패 로그/polyMesh 회수
- [x] no-layer 사각방 `snappyHexMesh`·solver-grade `checkMesh` 기준 통과
- [x] `mesh_manifest.v1` 체적·patch·품질 gate와 Studio 실행 차단
- [x] 기둥·장비·말단 스냅 메시의 solver-grade 품질 통과와 strict concavity 진단 분리
- [x] 빠른/상세 메시 프리셋과 말단·장비 주변 자동 국부 세분화
- [x] 벽·장비 prism layer, layer coverage·평균 layer 수 gate와 실제 복합방 통과
- [x] 등온 유동 계산 결과에서 y+ 분포와 목표 범위 충족 면적 계산

## 2. 제품 목표와 엔진 구분

최종 목표는 컴퓨터에 익숙하지 않은 기계설비 담당자가 아래 흐름을 명령줄 없이 완료하는 것이다.

```text
도면 선택
  → 자동 인식 결과 확인
  → 실제 3D 공기영역 생성
  → 메시 품질 확인
  → 설비 조건 확인
  → 계산
  → 설계 판단용 결과와 신뢰도 확인
```

현재 직교격자·다공성 셀 방식은 빠른 비교용 `screening_voxel` 엔진으로 보존한다. 실제 형상을
따르는 새 경로는 `body_fitted_airflow` 엔진으로 분리한다. 두 엔진의 결과와 신뢰도 표시는 섞지 않는다.

## 3. 목표 파이프라인

```text
DXF (MVP)
  → geometry.v2.json + 사용자 의미 확인
  → FreeCADCmd/OCC 공기 체적 생성
  → 단일 비중첩 multi-region STL + surface_manifest.json
  → surfaceCheck
  → blockMesh 배경 격자
  → surfaceFeatureExtract
  → snappyHexMesh + 국부 세분화 + 경계층
  → checkMesh 품질 게이트
  → 검증된 실내 공조 물리 프리셋
  → OpenFOAM 계산
  → sample/VTK 기반 비정형 후처리
  → 수치·물리·모델 신뢰도 보고서
```

STEP/IFC는 이 파일럿 범위에 포함하지 않는다. 향후 별도 입력 어댑터가 같은 `geometry.v2` 계약을
생성하도록 연결한다.

## 4. 구현 순서와 통과 기준

### 0단계 — 실행환경을 제품 기능으로 만들기

- [완료] WSL 배포판, OpenFOAM 버전, bashrc 경로, 필수 명령을 한 번에 진단한다.
- [완료] 진단 결과를 `capability_manifest.json`에 기록하고 화면에 쉬운 한국어로 표시한다.
- [완료] 소형 수용 테스트를 버튼으로 실행하고 메시·솔버·결과 회수·보고서를 검증한다.
- [완료] FreeCADCmd를 PATH뿐 아니라 표준 설치 경로와 프로젝트 설정에서 찾고, 정확한 실행 파일·FreeCAD·
  Python·OCC 버전을 기록한다.
- [완료] `Part`, `Mesh`, `MeshPart`, `BOPTools.SplitAPI`, Boolean, tessellation을 headless cube
  스모크로 검사한다. 작업마다 고유한 user/system config, 로그, 임시폴더와 timeout을 사용한다.
- [완료] 기존 BIM builder의 opening 반환계약과 고정 임시파일을 수정하고, opening 포함 DXF가 FCStd까지
  생성되는 실제 FreeCADCmd 회귀 테스트를 추가한다.
- 설치·업데이트를 안전한 관리자 승인 흐름으로 실행할 수 있게 한다.

통과 기준: 새 PC에서 사용자가 터미널 명령을 입력하지 않고 설치 상태와 해결 방법을 확인할 수 있고,
관리자 설치 컨텍스트와 일반 사용자 앱 컨텍스트가 같은 WSL 배포판을 보며, OpenFOAM 기준 케이스가
`Mesh OK`부터 결과 회수까지 통과한다. FreeCAD가 없거나 스모크에 실패하면
`body_fitted_airflow`만 비활성화하고 `screening_voxel`은 계속 사용할 수 있어야 한다.

### 1단계 — 도면 의미 계약 `geometry.v2`

- `geometry.v2.schema.json`, v1→v2 변환기와 semantic diff를 만든다.
- 모든 요소에 안정적인 ID, `source_handle`/`source_handles`, 원본 레이어·블록, 단위, 좌표 변환,
  신뢰도, 사용자 확인 여부를 둔다. 리스트 순번이나 OCC `FaceN`은 ID로 사용하지 않는다.
- 공간에는 닫힌 경계·층·천장고를, 급배기구에는 역할·CMH·온도·방향·크기·부착 면을 둔다.
- 장비에는 고체/발열 역할, 실제 높이·외곽, 총 kW, 대류분율을 둔다.
- 자동 분류하지 못한 요소는 무시하지 않고 확인 목록에 올린다.
- body-fitted 모드에서는 전체 도면 bbox 폴백을 금지하고 닫힌 zone 또는 사용자가 확정한 공기영역만
  허용한다.

통과 기준: 모든 말단과 발열 장비가 계산 전에 사용자에게 보이고, 미확인 필수 항목이 있으면 실행을
차단한다. mm/인치로 작성한 동일 형상은 정규화 후 길이 1 mm 또는 0.1%, 면적 0.2% 이내에서
동일해야 하며, 열린·자가교차 zone, 공간 밖 말단, 단위 미확인은 hard fail이다.

### 2단계 — OCC 공기영역과 named surface

- 일반 Python은 DXF/`geometry.v2`를 담당하고, Windows FreeCAD 1.1.1의 `FreeCADCmd.exe`는
  1 작업=1 프로세스인 `cfd_occ_worker.py`로만 실행한다. FreeCAD를 WSL에 중복 설치하지 않는다.
- FreeCADCmd 안에서 `zone 압출 - fused(기둥, 고체 장비)`로 방 공기 체적을 만들고 유효한 Solid가
  정확히 하나인지 검사한다.
- 급기·배기 면을 벽/천장에 실제로 분할해 서로 겹치지 않는 경계 패치로 만든다.
- mm OCC 형상을 STL vertex에서 정확히 한 번 0.001배해 m로 바꾸고 원점 이동·회전과 역변환을
  기록한다.
- 동일 면을 master STL과 patch STL에 중복 투입하지 않는다. OpenFOAM 입력은
  `solid wall`, `solid supply_<id>`, `solid exhaust_<id>`, `solid equipment_<id>` region을 가진
  단일 ASCII multi-region STL로 만들고, 필요할 때만 patch별 진단 파일을 별도 생성한다.
- `surface_manifest.json`에 입력·도구 버전과 해시, OCC 체적, tessellation 공차, region별 원본
  element ID·역할·면적·법선·AABB·삼각형 수·정규화 해시를 기록한다.
- `locationInMesh`는 단순 centroid가 아니라 OCC `isInside`와 경계 이격거리를 만족하는 점을 탐색한다.
- 모든 출력은 job 전용 staging에서 만든 후 검증에 성공한 결과만 원자적으로 게시한다.

통과 기준: 사각방, L형 방, 기둥/장비, 복수 디퓨저 기준 모델에서 단일 공기 Solid가 유효하고,
전체 multi-region 표면 합집합이 watertight/manifold이며 체적 오차 1% 이하이다. 개별 patch는 열린
표면일 수 있지만 서로 겹치지 않고 합집합이 전체 외피를 누락 없이 구성해야 한다. 전체 patch 면적
합계 오차 0.1% 이하, 말단 면적 오차 2% 이하, 중복·자가교차 삼각형 0이며 같은 입력을 다시 실행하면
semantic ID·체적·면적·정규화 해시가 동일해야 한다.

### 3A단계 — body-fitted 기본 메시

- `surfaceCheck → blockMesh → surfaceFeatureExtract → snappyHexMesh -overwrite →
  checkMesh -allTopology -meshQuality`를 `Allmesh` 독립 단계로 만들고,
  `checkMesh -allGeometry -allTopology -meshQuality`는 별도 strict 진단으로 보존한다.
- 최초 수용은 prism layer를 끈 castellated+snap 메시로 원인을 분리한다.
- `maxGlobalCells`, `maxLocalCells`, 예상 RAM·디스크 한도를 실행 전에 검사한다.
- `log.surfaceCheck`, `log.surfaceFeatureExtract`, `log.snappyHexMesh`, `log.checkMesh`와
  `constant/polyMesh`를 성공·실패 여부와 무관하게 진단 산출물로 회수한다.
- 실패 시 계산을 시작하지 않고 문제 위치와 해결 방법을 3D 화면에 표시한다.

통과 기준: solver-grade 로그에 `Mesh OK.`가 존재하고 `Failed N mesh checks`·`FOAM FATAL`이 없다. 필수 patch가
모두 nonzero이고 `defaultFaces=0`, 유체 region=1이며, 음수/0 체적과 illegal face가 없다.
메시 체적과 OCC 체적 오차 2% 이하, 말단 면적 오차 5% 이하, 최대 non-orthogonality 70 이하,
내부 skewness 4 이하이다. strict zero-concavity 검사는 solver 차단 조건과 분리해 개수와 로그를 경고로 남긴다.

### 3B단계 — 국부 세분화·경계층·y+

- [완료] 말단 jet, 장비 모서리와 벽 근처에 검증된 상세 프리셋으로 국부 세분화와 prism layer를 적용한다.
- [완료] layer coverage와 실제 solver 기반 점성층·전이영역·로그층 면적을 manifest에 기록한다.
- layer 없는 `빠른 실제형상 스크리닝`과 layer를 포함한 `상세해석`을 서로 다른 신뢰도 등급으로
  표시한다.

통과 기준: 프리셋에 정의된 최소 layer 수·coverage를 만족하고, 선택한 난류 wall treatment의
검증 y+ 범위에 들어오는 벽 면적 비율을 보고서에 표시한다. layer gate가 실패하면 상세해석은
차단하되 3A 메시 결과를 성공으로 위장하지 않는다.

### 4단계 — 검증된 실내 공조 물리 v1

- 첫 범위는 단일 zone 정상상태 부력 유동으로 제한한다.
- 먼저 등온 유동을 통과시킨 뒤 열·부력을 추가한다.
- 급기는 CMH 기반 유량 경계, 배기는 압력 출구로 만들고 난류 강도·길이척도에서 `k/omega`를
  생성한다.
- grille/down은 면 법선 방향, 4way는 4개 semantic subpatch와 접선 취출벡터를 사용하되 총 CMH를
  보존한다. terminal 모델별 jet 골든 케이스를 분리한다.
- 장비는 바닥 접촉면을 제외한 실제 노출면적과 대류분율에서 표면 열유속을 계산한다. v1에서
  복사열을 조용히 대류열로 합치지 않고 적용 대류열과 제외된 복사열을 보고서에 나눈다.
- 벽 조건은 솔버 옵션 나열 대신 `단열 스크리닝`, `실내 표면온도 지정`, `외기 열전달`처럼 검증된
  프리셋으로 제공하고 선택 근거를 남긴다.

통과 기준: 급기 유량 입력 대비 ±1%, 전체 질량 불평형 1% 이하, 에너지 폐합 95~105%, 마지막
반복 구간의 주요 지표 변화 1% 이하이다. 공급 CMH·장비 대류발열·배기 질량/에너지 합계를
manifest와 보고서에서 입력 요소까지 역추적할 수 있어야 한다.

### 5단계 — 비정형 후처리

- 현재 `(nx, ny, nz)` reshape 방식은 `screening_voxel` 전용으로 분리한다.
- 새 엔진은 OpenFOAM `sample`/VTK를 사용해 실제 셀 좌표와 연결관계를 읽는다.
- 임의 절단면, 3D 유동, hotspot, 영역 평균, 패치 유량을 같은 데이터에서 계산한다.
- WSL에서 표준 sample/VTK를 만들고 UI에 필요한 축약 산출물만 Windows로 회수한다. 전체 VTK의
  무제한 브라우저 로드를 금지하고 파일·셀 수 한도를 둔다.
- `result_manifest.json`에 mesh/run hash, 필드 위치(cell/node), 단위, time, sampling 정의와
  산출물 해시를 기록한다.

통과 기준: 같은 결과를 다시 열어도 지표가 결정론적으로 같고, `polyMesh` 셀 순서를 추측하지 않는다.
상수장 평균은 상대오차 `1e-9` 이하, 선형 절단면 보간 1% 이하, patch flux와 OpenFOAM 적분값 차이
0.5% 이하이며 cell/node 순서를 섞어도 결과가 같아야 한다.

### 6단계 — 검증 게이트

- Annex 20 body-fitted 버전, 부력·발열 방, L형/장애물 회귀 케이스를 둔다.
- 3개 격자로 GCI를 계산하고 TAB/현장 온도·풍량과 대조하는 입력 형식을 만든다.
- geometry, mesh, solver, result 게이트를 분리한다.
- 각 단계는 `gate_result.json`에 입력 해시, 도구 버전, 지표, `PASS|WARN|FAIL`, 오류코드를 남긴다.
  앞 단계가 `FAIL` 또는 `UNKNOWN`이면 다음 단계를 실행하지 않는다.
- residual, continuity, 질량, 에너지, GCI를 AND gate로 판정한다. `screening_voxel`과 값이 비슷하다는
  이유만으로 body-fitted 결과를 정답으로 간주하지 않는다.

통과 기준: 질량·에너지 기준을 모두 통과하고 주요 설계지표 GCI 5% 이하이다. 어느 게이트든 실패하면
초록색 `적합` 표시는 금지하며, 벤치마크·현장 검증 전에는 `design_ready=false`를 유지한다.

### 7단계 — 초보자용 마법사와 배포

- 화면 순서를 `도면 → 자동정리 → 3D 확인 → 조건 확인 → 메시 확인 → 계산 → 결과`로 고정한다.
- 사용자는 STL, snappyHexMesh, solver 이름, 좌표 범위를 입력하지 않는다.
- 자동 판단마다 근거·신뢰도·확인 상태를 표시한다.
- 실행 전에 예상 셀 수, RAM, 계산 시간을 범위로 보여준다.
- 급기·배기는 `x0/xL` 입력 대신 3D 면 선택과 설비 블록 확인으로 지정하고 언제든 원본 도면과
  나란히 대조할 수 있게 한다.
- 오류는 `사용자가 수정 가능`, `환경 복구 필요`, `개발자 진단 필요`로 나누고 바로 실행할 수 있는
  해결 절차를 제공한다.

통과 기준: 기계설비 담당자가 별도 명령줄 없이 첫 프로젝트를 완료하고, 치명 오류와 단순 경고를
구분하며, 보고서에서 입력값·가정·검증상태를 추적할 수 있다. 제한적 베타 전 대표 기계설비 사용자
3명 이상으로 표준 프로젝트 완료율 90% 이상, 계산시간을 제외한 설정 완료 중앙값 15분 이하,
치명 사용성 오류 0건을 확인한다.

## 5. 첫 번째 세로형 파일럿

첫 개발 대상은 `단일 전기실 + L형 공간 + 기둥 1개 + 발열장비 1개 + 천장 급기 2개 + 벽 배기 1개`로
고정한다.

1. `geometry.v2`와 의미 확인 화면을 만든다.
2. FreeCADCmd가 공기 체적과 named surface를 만든다.
3. UI에서 `surfaceCheck → snappyHexMesh → checkMesh`까지만 먼저 통과시킨다.
4. 같은 형상을 등온 유동으로 계산한다.
5. 장비 표면 열유속과 부력을 추가한다.
6. 비정형 절단면 뷰어와 신뢰도 보고서를 연결한다.

이 파일럿이 모든 게이트를 통과하면 `DXF에서 실제 3D body-fitted CFD까지 이어지는 세로형 기술
MVP`가 성립한다. 제품 목표 달성은 서로 다른 실제 현장 DXF, 벤치마크/GCI와 사용자 UAT를 추가로
통과해야 한다.

## 6. 지금 미루는 범위

- OpenFOAM 최신 버전 전환
- CHT, 다층 벽체 전도, 복사
- 다실·문 개방, 연기/화재
- STEP/IFC 직접 해석과 클라우드 계산

현재 템플릿은 OpenFOAM v1912 사전 형식과 솔버에 맞춰져 있다. 최신 버전은 별도 실행 프로필과 골든
케이스 비교를 통과한 뒤 지원한다. 단일 zone body-fitted 파일럿 검증 전에는 고급 물리를 추가하지 않는다.

## 7. 실행 경계와 산출물 계약

### 프로세스 경계

```text
Windows 일반 Python
  - DXF 파싱, geometry.v2 검증, UI, 작업 오케스트레이션
        ↓ 정규화된 JSON
Windows FreeCADCmd 1.1.1 / OCC 7.8.1
  - 공기 Solid, Boolean, face partition, tessellation
        ↓ BREP + multi-region STL + manifest
WSL Ubuntu-24.04 / OpenFOAM v1912
  - surfaceCheck, snappyHexMesh, solver, sample/VTK
        ↓ 축약 결과 + 로그 + manifest
Windows UI/보고서
```

`ezdxf`/`shapely`는 일반 Python 환경에 두고 FreeCAD embedded Python에 설치하지 않는다. 각
프로세스는 파일 계약으로만 연결해 버전과 실패 위치를 분명하게 한다. FreeCAD 1.1.1/OCC 7.8.1을
초기 검증 프로필로 고정하며 다른 버전은 골든 케이스를 통과하기 전 자동 호환으로 간주하지 않는다.

### 프로젝트 산출물

```text
project.json
inputs/
  source.dxf
geometry/
  geometry.v2.json
  occ_manifest.json
  air_volume.brep
  triSurface/air_domain.stl
  surface_manifest.json
mesh/
  mesh_manifest.json
  quality.json
  logs/
runs/<run_id>/
  run_manifest.json
  logs/
results/
  result_manifest.json
  slices/
  summaries/
```

모든 manifest에는 schema/engine/tool 버전, 입력·출력 해시, 단위·좌표변환, 이전 단계 해시,
semantic ID, 단계 상태와 오류코드를 기록한다. OCC face 순번이나 파일 생성 순서는 영속 ID가 아니다.

## 8. 구현 작업 패키지

| 순서 | 작업 패키지 | 주요 산출물 | 기존 파일 영향 | 종료 조건 |
|---|---|---|---|---|
| P0 완료 | 실행 기준선 정리 | FreeCAD capability, BIM opening 회귀, 고유 job temp | `freecad_builder.py`, `cfd_run.py`, `cfd_studio.py` | FreeCAD/OpenFOAM 진단과 실제 스모크 통과 |
| P1 완료 | 의미 계약 | `geometry.v2.schema.json`, v1→v2 변환기, semantic diff | `dxf_parser.py`, `cfd_studio.py` | 필수 의미 누락 hard fail, 단위 골든 통과 |
| P2 완료 | OCC 공기영역 | `cfd_occ.py`, `cfd_occ_worker.py`, BREP/STL/manifest | BIM builder와 코드 경로 분리 | 사각방·L형방·실제 샘플의 체적·patch·surfaceCheck 통과 |
| P3A 완료 | 기본 body-fitted 메시 | `cfd_mesh.py`, `Allmesh`, quality parser | 독립 WSL 실행·진단 회수·Studio gate 연결 | 사각방·기둥·장비·원형 말단 복합방 solver-grade 품질 통과 |
| P3B 완료 | 세분화·경계층 | 빠른/상세 프리셋, layer/y+ manifest | `cfd_mesh.py`, UI 메시 화면 | 상세 메시·coverage gate와 solver 기반 y+ 산출 통과 |
| P4 구현 완료·G2 검증 중 | 실내공조 물리 | `cfd_physics.py`, steady/transient BC·gate | 기존 `cfd_export.py`는 screening 전용 유지 | 등온 WARN을 URANS로 이어 계산하고 열부력 boundedness·에너지 폐합·3.0 FTT gate 구현; G2 최종값 대기 |
| P5 완료 | 비정형 후처리 | `cfd_post.py`, `result_manifest.json` | `cfd_report.py`와 구조격자 경로 분리 | 좌표 기반 slice, T/U/V 체적가중 통계, flux/에너지 추적 및 해시 연결 통과 |
| P6 구현 완료·UAT 대기 | 마법사 통합 | 3D 의미확인·메시진단·단계 재실행 | `cfd_studio.py` | 원클릭 3.0 FTT 자동 계산·현장 증거 등록·관찰 UAT 화면 구현; 외부 사용자 3명 대기 |
| P7 부분 실증 완료 | 검증·릴리스 | 골든 케이스, GCI, 설치/복구 수용시험 | `tests/`, 문서, 설치 스크립트 | 환경·설치복구 PASS; G2·실제 현장 DXF 3건·UAT 3명 대기 |

각 단계는 이전 단계의 manifest hash가 같으면 재사용하고, 입력이 바뀌면 필요한 하위 단계만
무효화한다. `geometry → mesh → solve → post`를 독립 실행해 오류 원인과 재계산 비용을 줄인다.

## 9. 자동 테스트와 골든 케이스

### 테스트 실행 등급

- `PR-fast` 30초 이내: schema, parser, semantic diff, 로그 파서, VTK 수학 단위 테스트
- `PR-golden` 2분 이내: DXF→`geometry.v2`와 저장 fixture 비교
- `target-runner` 10분 이내: 고정 FreeCAD/OpenFOAM 환경에서 OCC→snappy→소형 solve 실제 실행
- `nightly/release`: Annex 20, 발열 부력방, 3격자 GCI, 전체 세로형 파일럿

mock 기반 워크플로 테스트는 빠른 안전망으로 유지하되 실제 FreeCAD/OpenFOAM 수용 증거를 대체하지
않는다. FCStd/STL의 raw byte를 골든으로 저장하지 않고 ID·역할·좌표·면적·체적·위상·정규화
triangle hash를 허용오차로 비교한다. 기준값 갱신은 자동 덮어쓰기를 금지하고 변경 사유와 검토 승인을
남긴다.

### 최소 골든 세트

| ID | 형상/목적 | 필수 검증 |
|---|---|---|
| G0 | mm/인치 동일 사각방 | 단위·좌표 정규화, 1 m cube STL |
| G1 | L형 단일 zone | 닫힌 경계, 내부점, 단일 유체 region |
| G2 | 기둥+발열 장비 | Boolean 체적, 장비 patch, 열량 합계 |
| G3 | 천장 급기 2+벽 배기 1 | face imprint, patch 면적, CMH·질량 |
| G4 | 열린/자가교차 zone·공간 밖 말단 | 각 단계 hard fail과 쉬운 오류 안내 |
| G5 | Annex 20 body-fitted | 속도장 기준, 질량보존, 3격자 GCI |
| G6 | 세로형 전기실 파일럿 | DXF부터 보고서까지 명령줄 없는 E2E |

## 10. 주요 위험과 대응

| 위험 | 영향 | 대응 |
|---|---|---|
| CAD 의미 오분류 | 잘못된 급배기·발열 조건 | confidence+근거+사용자 확인, 필수 미확인 hard fail |
| OCC Boolean/tessellation 불안정 | 누수·중복면·메시 실패 | shape heal, 공차 기록, semantic face 재식별, 골든 회귀 |
| 큰 전역 CAD 좌표 | 정밀도 저하 | 선택 zone 근처 로컬 원점 이동과 역변환 보존 |
| 고정 임시파일·동시 작업 | 결과 덮어쓰기 | job별 디렉터리/config/log, atomic publish |
| 과도한 snappy 셀 수 | RAM/디스크 고갈 | 사전 셀수 추정, hard limit, 단계별 refinement |
| 구조격자 후처리 재사용 | 잘못된 단면·통계 | engine별 reader 분리, VTK/sample 좌표·연결 사용 |
| 버전 자동 호환 가정 | 재현성 상실 | FreeCAD/OpenFOAM profile 고정, 버전별 골든 재검증 |
| 단일 파일럿 과신 | 현장 적용 오류 | 실제 도면 3건 이상, GCI, 대표 사용자 UAT 추가 |

## 11. 완료 정의와 바로 시작할 순서

완료 상태는 다음처럼 구분한다.

- **기술 MVP:** G6 한 건에서 geometry·surface·mesh·solver·result gate가 모두 통과
- **제한적 베타:** 단위·원점·회전·레이어 방식이 다른 실제 현장 DXF 3건 이상과 벤치마크/GCI 통과
- **제품 목표:** 설치·복구·보고서 추적성 및 대표 기계설비 사용자 UAT까지 통과

바로 시작할 구현 순서는 다음과 같다.

1. [완료] FreeCAD 경로 탐지와 capability manifest를 보강한다.
2. [완료] `build_openings()` 반환계약과 고정 임시파일을 수정하고 실제 opening 스모크를 만든다.
3. [완료] `geometry.v2` schema·v1 변환기·필수 의미 확인 화면을 만든다.
4. [완료] 사각방/L형방을 대상으로 `cfd_occ_worker.py`가 단일 공기 Solid와 manifest를 생성하게 한다.
5. [완료] 기둥·장비 차감과 급배기 face imprint, multi-region STL을 추가한다.
6. [완료] `Allmesh`와 solver-grade 메시 gate를 UI에 연결하고 복합 형상까지 실제 통과시켰다. strict concavity는 별도 진단 경고로 보존한다.
7. [완료] 빠른/상세 메시와 국부 세분화·prism layer·coverage gate를 연결하고 등온 solver에서 y+를 산출한다.
8. [완료·URANS 전환] 실제 terminal 설계풍량 경계조건, `simpleFoam`, 잔차·continuity·벽처리 gate와 Studio 원클릭 실행을 연결했다. 500/500 CMH 파일럿은 벽처리 유효면적 87.66%와 continuity `3.47e-8`을 통과했다. 600회 정상상태 진동은 억지로 PASS시키지 않고 `WARN`으로 보존하며, 마지막 정상장을 `pimpleFoam`/열부력 URANS 초기장으로 넘겨 유동 교환시간·시간창 안정성으로 최종 판정한다.
9. [완료] 정상상태 마지막 time을 보존해 `pimpleFoam`으로 재시작하고 Courant·평균/RMS 유속 변동폭·누적 유동 교환시간·실행비용 gate를 기록한다. quick mesh 재시작과 초기 135,450셀 상세 벤치마크를 통과시켰다.
10. [완료] 이전 60,644셀·prism layer 상세 transient는 등온 성능 예산을 통과했으나 열·부력에서 불안정해 기본 프리셋에서 제외했다. 실패 직전 VTK 진단으로 level 3 원형 말단, 장비 prism, 벽 prism, 장비 refinement 전이 순서로 폭주 위치를 고정했고, 기본 상세 프리셋을 전역 0.35 m·level 2·prism 없음으로 바꿨다. UI 명칭도 `안정 상세 메시`로 수정했으며 등온 실행 버튼은 layer 유무와 무관하게 제공한다.
11. [완료] 장비 kW·대류분율을 OCC 노출면과 mesh patch까지 추적하고, 적용 대류열/제외 복사열, 장비 인접 유체 cellZone 열원, `buoyantBoussinesqPimpleFoam`, 최고온도·Boussinesq 범위·Courant·배기온도 기반 에너지 폐합 gate를 구현했다. v1912 상세 0.05초의 최대 Courant 3.574·압력 FPE를 재현한 뒤, quick 등온 정상장을 `mapFields -mapMethod interpolate`로 상세 메시로 보간하는 감사 가능한 초기화 경로를 추가했다. OpenFOAM v2606과 모든 대류항의 `bounded Gauss upwind` 조합에서 20,377셀·500/500 CMH·800 W·0.05초가 전체 최대 Courant 0.0152, 최종 온도 293.150~293.309 K로 통과했다. 자동 런타임 감지는 v2606을 우선하며 v1912는 legacy 프로필로 남긴다. Studio에는 완료된 상세 등온 결과 뒤에 0.05초 열·부력 안정성 시험 버튼을 공개했다.
12. [완료] 상세 열·부력 latestTime 재시작, 장비 patch 기반 열원 cellZone 재생성, 최대 5초 안전 청크, 누적 물리시간·유동 교환시간 확보율·solver 비용·고정비·남은 예상시간을 `thermal_progress.v1`로 구현했다. 배기 감열은 실제 양(+)의 `phi`와 인접 내부 셀 온도로 계산하고, OpenFOAM 셀 체적 `V`로 실내 축열을 독립 적분해 `누적 투입열 = 실내 축열 + 누적 배기열` 과도 폐합 gate를 적용한다. G2 20,377셀·500/500 CMH·800 W 장시간 수용시험은 59.2236초(유동 교환시간 25.00%)에서 최대 Courant 0.305, 293.150~306.099 K, 과도 폐합 100.0077%로 `PASS`했다. 저장 간격보다 짧은 마지막 0.0636초도 자동 writeInterval 축소 후 회수했으며 셀 체적은 재시작 time에 복사해 반복 비용을 줄였다. `foamToVTK`·`cfd_post.py`·`result_manifest.v1`·자립 HTML 리포트와 Studio 2D/3D 중앙 단면 뷰어까지 연결했다. 0.05초 안정성 시험과 장시간 설계 검토 결과는 UI와 manifest에서 명확히 분리한다.
13. [구현 완료] body-fitted 열·부력 PASS 결과 3개의 실제 공기 체적과 셀 수로 유효 격자폭 `h=(V/N)^(1/3)`을 계산하고, 동일 CAD·유량·열원·부력 조건과 동일 물리시간을 강제하는 `grid_convergence.v1` 계약을 구현했다. 기준온도 대비 최고·p95 온도 상승과 p95 유속에 Celik/Roache GCI를 적용하며, 단조 수렴과 각 지표 GCI 5% 이하를 모두 요구한다. `gci_job.v1` 자동 작업은 geometry.json 한 건에서 3수준 상세 메시, 메시별 등온 초기장, 0.05초 안정성 시험과 최소 0.25 유동 교환시간까지의 열 이어 계산, 최종 GCI와 자립형 보고서를 공유 OpenFOAM 작업 큐에서 순차 실행한다. OCC·메시·등온·열 단계와 실제 산출물을 원자적으로 저장하고 앱 재시작 또는 실패 뒤 PASS 단계부터 재개한다.
14. [검증 완료·보정 필요] G2 실제 자동 3수준 시험 `gci-c7ceb31f21f2`를 0.420/0.350/0.292 m로 끝까지 실행했다. OCC 체적은 32.902 m³, 실제 셀 수는 15,563/20,377/32,919, 유효 세분비는 1.0940/1.1734였고 세 해석 모두 59.2236초·0.25 유동 교환시간에서 PASS했다. 전체 실행은 5,074.7초였고 완료 작업 재실행은 계산 반복 없이 1.405초였다. 최고온도 상승 GCI는 5.8502%로 5% 기준을 조금 초과했고, 온도 상승 p95와 유속 p95는 비단조여서 최종 GCI gate는 FAIL이었다. 기본 간격을 0.350/0.292/0.243 m로 한 수준 이동하고, 새 연구가 geometry·물리 설정·간격이 같은 이전 PASS 결과를 산출물 검증 뒤 자동 재사용하도록 보강했다.
15. [검증 완료·지표 보정 필요] 후속 작업 `gci-7f318b32beb7`에서 이전 0.350/0.292 m 결과를 실제로 재사용하고 0.243 m만 새로 계산했다. 새 mesh는 47,960셀, 세 수준 유효 세분비는 1.1734/1.1336, 새 단계 실행시간은 3,457.328초였으며 세 solver 결과는 모두 PASS했다. 그러나 최고온도 상승은 13.444→16.095→19.424 K로 계속 증가해 GCI 28.9152%였고 p95 온도·유속도 비단조였다. 원시 `T/U/V` 재계산에서 기존 p95가 체적이 아닌 셀 개수 기준이라는 문제를 확인했다. 체적가중 온도평균은 GCI 0.0011%로 안정됐지만 체적가중 유속평균은 17.22%, 체적가중 p95는 여전히 비단조여서 0.25 유동 교환시간의 단일 URANS 시점 자체가 격자 독립성 지표로 부족하다고 판정했다.
16. [완료] 열 이어 계산 결과 회수 시 시작·중간·끝과 최근 이력 최대 28개를 원자적으로 보존하고, `grid_convergence.v2`에서 마지막 0.1 유동 교환시간의 `T/U/V`를 셀 체적가중·시간적분하도록 구현했다. 0.350/0.243/0.169 m 실제 해석은 20,377/47,960/107,991셀, 유효 세분비 1.3302/1.3107, 236.8944초·1.0 유동 교환시간에서 모두 PASS했다. 온도 p95 GCI는 3.8899%였지만 평균온도와 유속 p95가 비단조여 v2 종합 gate는 FAIL이었다.
17. [완료] 비정렬 격자 산포를 단순 반범위나 임의 완화로 통과시키지 않도록 Eça–Hoekstra(2014) 최소제곱근 절차를 `grid_convergence.v3`로 구현했다. 최소 4격자, 실제 세분비 1.25 이상, 적합 표준편차·안전계수·95% 불확실성을 명시적으로 기록한다. G2에는 0.504 m·9,374셀 매우 거친 격자를 추가했고, 작은 급배기구만 최소 국부 해상도를 적용해 패치 면적오차 1.43%/1.31%와 다음 세분비 1.2954를 동시에 통과시켰다. 4격자 1.0 교환시간 v3는 평균온도 5.4844%, 온도 p95 56.9682%, 유속 p95 30.3042%로 FAIL이었다.
18. [완료·격자 세분화 필요] 1.0 교환시간의 마지막 창에서도 평균온도가 격자별 약 0.10~0.12 K 상승하는 과도상태임을 확인해 v3 기본 최소시간을 3.0 유동 교환시간으로 올리고, 각 지표의 마지막 0.1 유동 교환시간 변화가 2% 이하인지 별도 정상성 gate로 추가했다. `gci-aca6a016b2e1`의 9,374/20,377/47,960/107,991셀 네 격자는 모두 710.6832초·3.0 FTT까지 PASS했다. fine 마지막 창은 5개 복구 스냅샷과 끝점 1개를 적분했고 변화율은 평균온도 0.19958%, 온도 p95 0.13447%, 유속 p95 0.05946%였다. 따라서 현재 GCI 실패는 계산시간 부족이 아니라 메시 계열의 공간 불확실성 문제로 판정한다. 장시간 작업은 작업 ID 재개와 교차 프로세스 lock으로 중복 실행을 차단하며, Studio는 직전 solver 비용 기반 예상 진행값을 다음 체크포인트 이내로만 표시한다. 이어 계산 재시작은 입력·`0`·최신 체크포인트만 WSL로 선별 전송하고, 회수는 처음·중간 기준점과 최신 연속 스냅샷 최대 7개를 보존한다. 자동 작업 시작 화면은 geometry 경로 대신 의미 확인 완료 도면 선택 방식이며, 단일 방·급기·배기·발열원·급배기 1% 균형을 실행 전에 강제한다.
19. [부분 실증 완료] G2 v3 수용 결과를 고정한 뒤 실제 현장 DXF 3종(단위·원점·회전·레이어 방식 상이), 설치/복구, 기계설비 담당자 UAT 증거를 순서대로 확보한다. 프로젝트 전용 `.venv` 설치·재설치 경로와 `release_readiness.v1` 감사기를 구현했고, 대시보드에서 각 증거의 PASS/BLOCKED를 확인한다. 2026-07-21 실제 target에서는 클린 Python 설치, 의존성 재설치, Windows 실행기, OpenFOAM v2606, 64셀 환경 수용시험, G2 작업의 401.8944초 체크포인트 중단 후 재개·421.8944초 전진, 기준 파일 해시 보존을 모두 통과해 설치·복구 항목을 PASS로 고정했다. 출시 감사는 설치 증거의 `PASS` 문자열을 그대로 신뢰하지 않고 기준 파일의 현재 SHA-256, 로그 저장 위치, 실행 명령과 성공 표식, 체크포인트 보존·전진을 독립 재검증하며 새 증거에는 로그 SHA-256도 기록한다. 기존 실제 target 증거도 이 강화된 검사로 다시 PASS했다. `install_openfoam2606.bat`는 관리자 권한 자동 요청, WSL/Ubuntu-24.04 설치, 재시작 후 재개, OpenFOAM v2606 설치·업데이트까지 연결했다. WSL이 오류인데 종료코드만 0인 경우도 내부 성공 표식이 없으면 실패시키며, 감사기 역시 성공 표식과 WSL 오류가 함께 있는 로그를 거부한다. `field_dxf_acceptance.v1`은 완료된 열해석 결과에서 원본 DXF→geometry.v2→OCC 표면→v2606 메시→열해석→VTU 결과를 역추적하고 모든 파일과 단계 입력의 SHA-256을 재검증한다. 최소 3.0 유동 교환시간, 완전한 에너지 이력과 최신 결과 시각도 강제하며, 상세 결과 화면의 백그라운드 자동 이어 계산은 체크포인트와 재개 이력을 보존한다. 출시 감사 때도 같은 검사를 다시 수행하므로 샘플, 자체 PASS 선언, 수정되거나 분리된 산출물은 현장 증거로 인정하지 않는다. 세 건의 서명이 달라도 단위·원점·블록 회전·레이어 구성 중 하나라도 전체 묶음에서 변화가 없으면 다양성 부족으로 BLOCKED한다. 출시 준비 화면에는 경로 입력 없이 완료 결과를 검사·등록하는 버튼을 추가했다. `mechanical_user_uat.v1`과 관찰 시험 화면은 서로 다른 관찰자가 6개 필수 작업을 순서대로 기록하게 하고, 서버 시간으로 설정 시간을 산출하며, 사용한 현장 DXF 증거 해시까지 연결한다. 작업 시작·저장·완료·취소는 공용 UAT 잠금으로 직렬화하고 화면의 중복 완료 요청도 차단해 다중 탭 경합으로 최종 증거가 중복 생성되지 않게 했다. 브라우저 저장정보가 없어도 같은 참가자·관찰자·도면을 입력하면 진행 중 초안을 복구한다. 감사기는 편집된 요약값이나 중복 참가자를 제외하고 완료율·중앙값·치명 오류를 다시 계산한다. 현재 실제 현장 DXF 0/3건과 외부 사용자 UAT 0/3명이므로 제품 준비 상태는 계속 BLOCKED다.
20. [구현 완료·현장 실행 대기] `field_pipeline_job.v1` 단일 현장 도면 자동 작업을 추가했다. 확인된 프로젝트 DXF를 이름으로 선택하면 OCC → 안정 상세 메시 → 등온 초기장 → 열·부력 → 최소 3.0 FTT를 GCI와 동일한 단계 gate로 실행한다. 원본 DXF와 geometry 해시를 시작 때 다시 검사하고 `_field_jobs/field-*/field_pipeline_job.json`에 자원 추정, 단계, 시각, 셀 수, FTT, 시도·재개 이력을 원자 저장한다. 대시보드·새 해석 확인 완료 메시지·출시 준비에서 **현장 자동 해석**으로 진입하며, 완료 결과는 곧바로 좌표 기반 뷰어·보고서·현장 증거 등록 후보가 된다. 작업별 잠금 외에 `_system/cfd_solver.lock`을 원자 획득하므로 서로 다른 GCI·현장 작업 및 Studio·CLI 프로세스가 같은 워크스테이션의 OpenFOAM을 겹쳐 실행하지 않는다. 현재 전역 잠금 도입 전에 시작된 G2도 작업별 live lock을 호환 검사해 보호한다. 장시간 청크 사이에는 직전 solver 비용으로 FTT를 추정하되 다음 원자 체크포인트를 상한으로 제한해 진행을 과장하지 않는다. 실제 현장 DXF 3건 투입은 G2 완료 후 수행한다.
21. [진행 중] 공식 MARIN 2014 numerical uncertainty wrapper와 G2 네 지표 값을 직접 교차검증했다. 허용 차수 0.5≤p≤2의 가중 Richardson 회귀를 우선하지 않던 선택 오류와, `phi0/alpha/p` 세 변수를 추정하면서 표준편차 자유도를 `ng-2`로 계산하던 오류를 수정했다. 평균온도 불확실성은 공식값과 일치하는 5.01869%가 되었고 온도 p95 109.98076%, 유속 p95 17.10172%도 공식값과 일치했다. 세 시간 정상성 gate는 모두 PASS지만 공간 불확실성은 FAIL이므로 기준을 낮추지 않는다. 수준별 국부 세분화 설정이 다르면 같은 폭의 메시를 재사용하지 못하도록 안전 조건을 강화하고, 설정까지 같은 기존 0.350/0.243/0.169 m 결과만 재사용해 0.118 m 신규 fine을 추가하는 `gci-18e8320a98c4`를 실행 중이다. 신규 fine은 실제 232,842셀로 생성됐고 600회 등온 초기장과 0.05초 열·부력 안정성 시험을 완료했다. 최대 Courant 수가 2.0 이하인 상태로 3.0 FTT까지 자동 이어 계산하며, 완료 뒤 가장 미세한 네 격자로 v3를 다시 판정한다.
22. [구현 완료] 수십 시간 걸릴 수 있는 로컬 GCI·현장 자동 해석이 Windows 유휴 절전으로 중단되지 않도록 solver 잠금을 보유한 작업 스레드에 시스템 실행 상태 요청을 설정한다. 화면 절전은 그대로 두며, 정상 완료·실패·예외 때 요청을 해제한다. 운영체제가 요청을 지원하지 않거나 호출이 실패해도 CFD 작업 자체는 중단하지 않고 기존 체크포인트 복구를 유지한다.
23. [구현 완료] 고해상도 격자에서 고정 60초 물리시간 청크가 수 시간짜리 무체크포인트 구간이 되는 문제를 보완했다. 첫 열 이어 계산은 0.05초 안정성 시험 비용을 초기·연속 최대 시간간격 비와 안전계수로 환산하고, 첫 청크 뒤부터는 실제 solver 초/물리초를 사용해 기본 30분 벽시계 예산에 맞춰 다음 청크를 줄인다. 예상 완료시간은 실제 continuation 표본 전까지 계속 숨기며, 청크 조절은 저장·복구 단위만 바꾸고 3.0 FTT와 물리·GCI gate는 유지한다.
24. [구현 완료] 5초 solver 하트비트가 작업 `updated_at`을 계속 갱신하면서 체크포인트 사이 예상 진행의 경과시간이 초기화되는 문제를 수정했다. 수준별 `stage_started_at`을 별도로 보존하고 하트비트는 생존·최신 문구만 갱신하므로, 첫 보수 추정과 이후 실측 추정 모두 다음 저장점까지만 단조롭게 전진한다.
25. [구현 완료] Windows 결과 회수 전 관리 프로세스가 종료되면 WSL의 2초 간격 내부 time을 모두 지우고 마지막 Windows 체크포인트로 후퇴하던 복구 공백을 제거했다. `Allrun`·수치설정·열 입력·최신 주요 필드의 SHA-256 지문이 같은 결정적 WSL 작업공간에서 Windows보다 최신 time을 확인하면 해당 공간을 그대로 재개한다. 같은 작업공간의 solver PID가 살아 있으면 지문보다 먼저 중복 실행을 차단한다. 지문이 다르더라도 WSL에 Windows보다 최신 time이 있으면 자동 staging과 삭제를 금지하고 `WSL_REMOTE_CHECKPOINT_CONFLICT`로 안전 중단하며, 최신 time이 없을 때만 검증된 Windows staging 경로를 사용한다.
26. [구현·재실증 완료] `gci-18e8320a98c4`의 실제 중단 복구에서 `thermal_restart_input.json.created_at`이 재개 준비 때마다 달라져 동일 계산의 지문을 무효화하는 문제를 확인했다. 감사 시각만 정규화에서 제외하되 물리·수치 설정과 필드 내용은 계속 해시하며, WSL 상태 조회가 20초 안에 끝나지 않으면 원격 폴더를 없는 것으로 간주하지 않고 `WSL_REMOTE_CHECKPOINT_PROBE_FAILED`로 닫힌 실패 처리한다. 지문 생성 자체가 불가능해도 `WSL_RESTART_FINGERPRINT_UNAVAILABLE`로 자동 staging을 금지한다. 43개 복구·안전 회귀 테스트를 통과했고, 실제 fine 계산에서 원격 2.05초 체크포인트를 삭제·재복사하지 않고 재사용한 뒤 물리시간 전진과 절전 방지를 확인했다.
27. [구현 완료] PowerShell `Start-Process`의 로그 리디렉션 핸들이 호출 셸을 붙잡는 운영 문제를 제거하기 위해 `scripts/start_gci_background.py`를 추가했다. 작업 ID와 manifest를 검증하고 표준출력·오류 로그를 프로젝트 내부에 연결한 뒤 Windows 분리 프로세스로 즉시 반환하며, 실제 `gci-18e8320a98c4` 재개에서 PID 36972를 반환하고 관리 프로세스·WSL solver·하트비트가 독립적으로 유지되는 것을 확인했다.
