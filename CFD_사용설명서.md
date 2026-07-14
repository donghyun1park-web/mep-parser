# CFD 사용 설명서 (도면 → OpenFOAM)

MEP Parser가 뽑은 `geometry.json`(또는 손으로 쓴 config) 을 **OpenFOAM 실내 열유동 해석 케이스**로
변환·실행·리포트까지 한 번에 처리하는 파이프라인. 전기실 발열·환기 해석을 1호 사례로 검증했고,
지하주차장 환기·사무실 공조 등도 같은 구조(박스 방 + 급/배기 + 발열)로 재사용할 수 있습니다.

---

## 1. 개요

```
[A] cfd_config.json (방/급배기/발열 직접 기술)         ─┐
[B] geometry.json --zone N / --room-bbox (도면 자동추출) ─┼─> cfd_export.py ─> case_xxx/ (OpenFOAM 케이스)
                                                        ┘         │
                                                                  ▼
                                                          cfd_run.py (WSL 실행)
                                                                  │
                                                                  ▼
                                                          cfd_report.py ─> cfd_report_xxx.html (자립 리포트)
```

- **입력 2가지**: 파라메트릭 config를 직접 쓰거나(A), `dxf_parser.py`가 만든 `geometry.json`에서
  방 치수를 **자동 추출**(B, `--from-geometry`)합니다. B가 핵심 시너지 — 수제로 방 치수를 베껴 쓰던
  과정을 없앱니다.
- **CLI 없이 쓰려면 → §12 CFD 스튜디오(GUI)**: `run_cfd.bat` 더블클릭 하나로 대시보드·마법사·
  실행 모니터·결과 뷰어(2D/3D)까지 전부 브라우저에서. 아래 CLI 절들은 자동화/스크립트용.
- **솔버**: `buoyantBoussinesqSimpleFoam` (정상상태·부력·비압축 Boussinesq). 실내 ΔT<30K 범위의
  표준 선택이며, 압축성 솔버(`buoyantSimpleFoam`)의 음의 밀도 발산 문제를 원천 회피합니다.
- **산출물 = 리포트**. 케이스 자체가 아니라 `cfd_report_*.html`이 최종 제품입니다. 수렴 판정, 입력
  가정값(발열량·풍량 — "설계 확정값 아님" 명시), 결과 지표(평균/최고온도·ΔT·유속), 온도·기류 단면
  그림을 담은 자립 HTML(이미지 base64 내장, 브라우저로 바로 열림) — 기계설비/설계자/TAB 담당자에게
  그대로 첨부 가능한 수준을 목표로 합니다.

---

## 2. 요구사항

| 용도 | 요구사항 | 필수? |
|---|---|---|
| 케이스 생성(`cfd_export.py`) | Python 3 (stdlib만 사용) | ✅ 필수 |
| 해석 실행(`cfd_run.py`) | **WSL2 + Ubuntu + OpenFOAM** (apt: `sudo apt-get install openfoam`) | ✅ 필수 |
| 리포트(`cfd_report.py`) | `numpy`, `matplotlib` (프로젝트에 이미 있음) | ✅ 필수 |
| 도면 연동(`--from-geometry`) | `dxf_parser.py`로 만든 `geometry.json` | B 입력 쓸 때 |

WSL에 OpenFOAM이 설치돼 있는지 확인:
```powershell
wsl -e bash -c "test -f /usr/share/openfoam/etc/bashrc && echo OK"
```
`OK`가 안 나오면 WSL 안에서 `sudo apt-get install openfoam` 로 설치하세요. (검증 환경: OpenFOAM **v1912**, openfoam.com apt 배포판)

---

## 3. 빠른 시작 — config로 바로 실행 (파일럿)

`cfd_configs/elec_room_pilot.json` 예제로 3줄이면 리포트까지 나옵니다.

```bash
python cfd_export.py cfd_configs/elec_room_pilot.json -o case_pilot
python cfd_run.py case_pilot
python cfd_report.py case_pilot
```

- 1번째 줄: `case_pilot/` 폴더에 OpenFOAM 케이스 생성 (blockMeshDict, 0/, constant/, system/, Allrun)
- 2번째 줄: WSL로 복사 → `blockMesh` → `checkMesh` → 솔버 실행(포그라운드) → 로그·최종 time만 회수
- 3번째 줄: `case_pilot/cfd_report_elec_room.html` 생성 → 브라우저로 열어서 확인

---

## 4. 실전 — 도면(geometry.json)에서 바로 뽑기

DXF → `dxf_parser.py`로 `geometry.json`을 만든 뒤, 그 안의 **zone**(구역 폴리곤) 또는 **좌표 bbox**로
방 하나를 골라 케이스를 생성합니다. 급기/배기 벽은 도면에 없는 정보라 **직접 지정**해야 합니다
(어느 벽에 개구부가 있는지는 자동으로 안내해 줍니다).

### 4-1. zone(A-ZONE 레이어)이 있는 도면
```bash
python cfd_export.py --from-geometry geometry.json --zone 0 --height 3.0 -o case_room0
```

### 4-2. zone이 없는 도면 (좌표로 직접 방 지정)
도면 좌표(mm) 기준 사각형 범위를 `x0,y0,x1,y1` 로 지정합니다.
```bash
python cfd_export.py --from-geometry geometry.json --room-bbox "0,-10000,10000,-2000" --height 3.5 -o case_room1
```

실행하면 이렇게 안내가 나옵니다:
```
[도면추출] room-bbox  방 10.0×8.0×3.5 m
  경계 개구부(급/배기 후보): {'y0': 1, 'xL': 1}  → --supply/--exhaust 로 지정
  장비 6개 감지(v1은 바닥발열로 단순화 — 장애물화는 후속 snappy)
```
`y0`, `xL` 벽에 개구부가 있다는 뜻이므로, 실제로 급기/배기인 쪽을 골라 다시 지정합니다:
```bash
python cfd_export.py --from-geometry geometry.json --room-bbox "0,-10000,10000,-2000" \
  --height 3.5 --supply y0 --exhaust xL -o case_room1
python cfd_run.py case_room1
python cfd_report.py case_room1
```

---

## 5. cfd_export.py 옵션 전체

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `config` | — | config.json 경로 (위치 인자, `--from-geometry`와 양자택일) |
| `-o, --out` | `case_<name>` | 출력 케이스 디렉토리 |
| `--from-geometry G.JSON` | — | 도면 `geometry.json` 에서 치수 자동추출 모드 |
| `--zone N` | — | 방으로 쓸 zone 인덱스 (도면에 A-ZONE closed polyline 있을 때) |
| `--room-bbox "x0,y0,x1,y1"` | — | 방 bbox 직접 지정 (mm, 도면좌표계, zone 없을 때) |
| `--height` | params의 벽 높이 | 층고(m) |
| `--cell` | `0.3` | 격자 셀 크기(m) — 작을수록 정밀·느림 |
| `--supply` | `x0` | 급기 벽 (`x0`\|`xL`\|`y0`\|`yW`) |
| `--exhaust` | `xL` | 배기 벽 (`x0`\|`xL`\|`y0`\|`yW`) |
| `--supply-u` | `0.05` | 급기 유속(m/s). ※약유동은 수렴 나쁨 — §8 참고, 실디퓨저는 보통 0.3~3 |
| `--power-kw` | — | **장비 총발열(kW) — 체적 발열원(권장).** 계산서 총발열 직결 + 에너지 폐합 검증 가능 |
| `--floor-t` | `313.0` | (구식) 발열 바닥 고정온도(K). `--power-kw` 없을 때만 사용 |
| `--endtime` | `400` | 최대 반복 수. 잔차 수렴하면 조기 종료 |
| `--name` | 도면파일명 기반 | 케이스 이름(리포트 제목에 사용) |

**급배기구(openings) 모드 — v2, 디퓨저·취출구 배열 검토용 (권장):**
config 에 `openings` 목록을 주면 "벽 전체" 대신 **실측 크기·위치의 급배기구 N개**가
벽/천장에 패치로 뚫립니다(blockMesh 균일격자 + topoSet/createPatch, snappy 불필요):
```json
"openings": [
  {"role":"supply","type":"4way","wall":"ceiling","cx":3.0,"cy":2.5,"w":0.6,"h":0.6,"cmh":600},
  {"role":"supply","type":"grille","wall":"x0","cx":4.0,"cy":1.8,"w":0.4,"h":0.3,"cmh":400},
  {"role":"exhaust","wall":"xL","cx":2.0,"cy":2.8,"w":0.5,"h":0.5}
]
```
- `wall`: ceiling/floor/x0/xL/y0/yW. `cx,cy`(m)는 그 벽 평면의 2D 좌표 — ceiling/floor=(x,y),
  x0/xL=(y,z), y0/yW=(x,z).
- `type`: `grille`(법선 취출) · `down`(천장 하향) · `4way`(천장 4방향 — 4분면 패치로 수평
  취출+질량 하향, `throat_v`로 목 유속 조정) — REHVA GB10 계열 간이 디퓨저 모델.
- 사각형은 격자 셀에 스냅되고 **유속을 실면적으로 역산해 CMH를 정확히 보존**(리포트에
  실면적·실풍량 표기). 셀 크기가 디퓨저 치수를 나누게(0.6m→셀 0.15/0.2/0.3) 잡으면 좋음.
- `exhaust` 최소 1개 필수(압력출구). 리포트에 **질량수지(배기−급기)** 지표 추가.
- ⚠ 현실 풍량은 열 수렴이 느립니다 — 반복 2000~4000 권장. 4way 등 제트 충돌 유동은
  **진동 정상상태**가 되어 폐합이 ±10% 요동할 수 있음 → 리포트가 최근 스냅샷 3개(600
  iter 간격) 평균으로 판정하고 `±N`으로 진동폭을 표기합니다.

**실형상(V3) — 방 폴리곤 + 장애물(기둥·장비), "모델링된 공간에서 시뮬레이션":**
openings 모드에서 추가로 방의 **실제 형상**과 **장애물**을 지정할 수 있습니다(둘 다 선택):
```json
"room_polygon": [[0,0],[4.9,0],[4.9,4.0],[2.5,4.0],[2.5,7.8],[0,7.8]],
"obstacles": [
  {"kind":"column",    "footprint":[[1.0,1.0],[1.4,1.0],[1.4,1.4],[1.0,1.4]]},
  {"kind":"equipment", "bbox":[0.6,4.6,1.8,5.4], "h":2.2, "kw":2.5}
]
```
- `room_polygon`(m, 방 로컬): L자 등 비직사각 방 — 폴리곤 밖 셀이 고체가 됩니다(계단형, 셀 크기 근사).
- `obstacles`: `footprint`(폴리곤) 또는 `bbox`(x0,y0,x1,y1). `h` 높이(기둥 기본=층고, 장비 기본 2.0m).
  **장비에 `kw`를 주면 그 위치에서 발열** → 랙 옆 국소 핫스팟이 실제로 계산됩니다.
  발열은 `obstacles[].kw`(장비별) **또는** `heat.power_kw`(바닥층 뭉갬) 중 하나만.
- 구현: 고체 셀 = 초고저항 다공(DarcyForchheimer d=1e9, 내부 |U|~1e-4 실측) — 격자는 균일
  유지라 뷰어·리포트·폐합 검증 전부 그대로. 리포트·뷰어에서 고체는 **회색**으로 마스킹되고
  통계(평균/최고온도·체적·ACH)는 **유체 셀만** 집계.
- 도면 연동: 스튜디오 마법사에서 zone 선택 시 **"방 실형상 사용"** 체크 + **[📐 도면 기둥·장비
  불러오기]**로 자동 채움(장비 kW만 입력). 개구부가 폴리곤 밖 벽에 있으면 생성 단계에서 오류로 잡음.
- ⚠ openings 케이스는 잔차 자가종료(residualControl)를 끕니다 — 잔차가 에너지 폐합 전에
  멈추는 것을 실측(770 iter 자가종료, 폐합 50%). 반복 4000~8000 권장, 판정은 폐합 배지.
- 한계: 계단형 근사(경사벽·곡면은 셀 단위), 장애물 내부 온도는 비물리(마스킹됨), conjugate
  열전달 아님(장비 발열은 체적원 근사).

**발열 입력 두 방식:**
- **`--power-kw` (권장)**: 계산서의 장비 총발열 kW를 그대로 넣습니다. 바닥층에 체적 발열원으로
  주입되고 벽은 단열 처리되어, **주입열 = 배기열**이라는 에너지 폐합을 리포트가 검증합니다(§8).
  계산서와 직접 맞물리는 방식.
- **`--floor-t` (구식)**: 바닥을 고정온도로 두는 단순화. 실제 발열량이 입력이 아니라 결과라
  계산서와 대조가 안 됩니다. 하위호환용으로만 남겨둡니다.

**벽 이름 규약** (박스 방의 6면 — 모든 옵션에서 공통):
```
       ceiling(z=H)
  y0 ┌───────────┐ yW
     │   floor   │        x0 = x가 작은 쪽 벽, xL = x가 큰 쪽 벽
     │  (z=0)    │        y0 = y가 작은 쪽 벽, yW = y가 큰 쪽 벽
     └───────────┘
       x0 ──── xL
```

### config.json 직접 작성 시 (A 입력)
```json
{
  "name": "elec_room",
  "room": {"L": 11.025, "W": 8.95, "H": 5.4},
  "mesh": {"cell": 0.3},
  "g": [0, 0, -9.81],
  "inlet":  {"wall": "x0", "U": [0.05, 0, 0], "T": 293},
  "outlet": {"wall": "xL"},
  "heat":   {"wall": "floor", "floor_T": 313},
  "init": {"T": 300},
  "endTime": 400
}
```
`L/W/H`는 미터, `T`는 켈빈(273.15+°C). `_note`/`_desc` 필드는 리포트에 표시되는 설명이며 없어도 동작합니다.

---

## 6. cfd_run.py — WSL 실행

```bash
python cfd_run.py case_pilot              # 기본
python cfd_run.py case_pilot --keep-mesh  # polyMesh 도 회수(디버깅용, 용량 큼)
python cfd_run.py case_pilot --name foo   # WSL 쪽 실행 폴더명 지정(동시에 여러 케이스 돌릴 때)
```
동작: `~/cfd_runs/<name>` 으로 복사(Windows `/mnt/c` 경유는 느려서 회피) → `blockMesh` →
`checkMesh` → 솔버(포그라운드, 로그 tee) 순으로 실행 → **로그 + 최종 time 디렉토리만** Windows
케이스 폴더로 회수합니다(중간 time·polyMesh는 WSL에 남겨 용량 절약).

---

## 7. cfd_report.py — 결과 리포트

```bash
python cfd_report.py case_pilot                 # case_pilot/cfd_report_<name>.html 생성
python cfd_report.py case_pilot -o report.html   # 출력 경로 지정
python cfd_report.py case_pilot/log.buoyantBoussinesqSimpleFoam  # 로그만 넣으면 잔차 그래프 PNG만
```
`cfd_case_meta.json`(cfd_export.py가 같이 생성)이 있으면 **전체 HTML 리포트**, 없으면 잔차 PNG만
나옵니다. 리포트 구성:
1. **수렴성 판정** — 배지(수렴/부분수렴/발산) + 필드별 잔차 추이 + 그래프
2. **해석 조건(입력 가정)** — 치수 출처(도면 자동추출 여부), 실 치수, 격자, 솔버, 급기·발열 가정값
3. **결과 지표** — 평균/최고/최저 온도, 급기 대비 ΔT, 최대 유속, 반복 수, 연속방정식 오차
4. **온도·기류 단면** — 수평면(작업 높이) + 수직면(성층+기류 화살표) 컨투어 그림

> ⚠️ 리포트 상단에 "풍량·발열·온도는 설계 가정값이며 확정 설계값이 아닙니다" 경고가 항상 표시됩니다.
> 실제 디퓨저 면적·장비별 실발열량을 반영하면 수치가 달라집니다 — 이 리포트는 **경향/방법론 검토용**.

---

## 8. 신뢰성 검증 — "이 결과를 믿을 수 있는가?"

CFD는 잔차가 떨어졌다고 다 믿으면 안 됩니다. 이 파이프라인은 신뢰를 **두 가지 계산 가능한
지표**로 확인합니다.

### 8-1. 에너지 폐합율 (발열 kW 케이스)
`--power-kw` 로 발열을 넣으면, 정상상태 + 단열벽에서는 **주입한 열량이 전부 배기로 나가야**
합니다(에너지 보존). 리포트가 배기 유량가중 엔탈피(ρ·cp·Σφ·ΔT)를 주입열과 비교해 **폐합율%**
를 계산합니다.
- **90~110%면 신뢰** — 물리적으로 수렴했다는 뜻. 배지가 `수렴(에너지폐합 100%)`.
- **크게 벗어나면 미수렴** — 잔차가 떨어져도 에너지가 안 닫힌 것. 배지가 `미수렴`.

> ⚠️ **중요**: 부력지배 약유동(예: 급기유속 0.05 m/s 같은 최소모델)은 **잔차는 떨어지는데 에너지가
> 40%밖에 안 닫히는** 함정이 있습니다(실측 확인됨). 이때는 반복을 늘리거나, 급기유속을 현실값
> (0.3~3 m/s)으로 올리세요. 폐합율이 이 함정을 자동으로 잡아냅니다 — 그래서 발열 케이스의
> 진짜 수렴 게이트입니다.

```bash
python cfd_export.py --from-geometry g.json --room-bbox "x0,y0,x1,y1" \
  --power-kw 10 --supply-u 0.3 -o case && python cfd_run.py case && python cfd_report.py case
# → 리포트 배지: "수렴(에너지폐합 100%)", 배기 온도상승 = 계산서 P/(ρcp·풍량) 과 일치
```

### 8-2. 격자 독립성 (`cfd_gridstudy.py`)
결과가 격자 조밀도가 아니라 물리에서 온 것임을 보이려면, 같은 방을 셀 크기 여러 개로 돌려
지표가 수렴하는지 확인합니다(CFD 보고서 신뢰성의 1번 관문, PBD 심사 요구사항과 같은 논리).
```bash
python cfd_gridstudy.py --from-geometry g.json --room-bbox "x0,y0,x1,y1" \
  --power-kw 10 --cells 0.3,0.2,0.15 -o study
```
출력 예:
```
 cell(m)      cells     T_avg     T_max       dT     폐합%
   0.300      4,160     20.17     21.33     0.32       100
   0.200     14,040     20.16     21.38     0.31       100
   0.150     34,320     20.16     21.42     0.31       100
GCI(T_max_C): 수렴차수 p=0.43, 외삽값=21.688, 최세밀격자 GCI=1.59% (신뢰(≤5%))
```
- **격자간 변화 <2%** → 격자 독립(충분). 이 방은 셀 0.2 m면 이미 격자 독립.
- **GCI%** = 최세밀 격자의 남은 격자 오차 추정(ASME V&V 표준). ≤5%면 신뢰할 만한 수치.

### 8-3. 벤치마크 검증 (`cfd_validate.py`, IEA Annex 20)
공인 벤치마크(슬롯취출 방, LDA 실측)로 파이프라인 오차를 정량화합니다:
```bash
python cfd_export.py cfd_benchmarks/annex20/annex20.json -o case_annex20
python cfd_run.py case_annex20
python cfd_validate.py case_annex20
# → annex20_validation.png (x/H=2 프로파일: CFD vs 실측 오버레이) + RMS/정성 판정
```
실측 데이터는 Nielsen et al.(2010) 논문 그림을 디지타이즈한 것(±0.03·U0 판독 오차,
CSV 헤더에 출처 명시). 정성 3항목(천장 제트 부착·바닥 역류·제트 감쇠) + RMS≤0.10·U0 기준.

### 8-4. 추가로 권장(수동)
- **계산서 대조**: 배기 온도상승이 계산서 ΔT=Q/(ρcp·풍량)와 맞는지(폐합율이 100%면 자동 성립).
- **실측 대조**: TAB 풍량·온도 실측치와 CFD 예측을 비교(가장 강한 신뢰 근거).

---

## 9. 알려진 한계 (v1)

- **방 = 경계 bbox 박스로 근사**합니다. 비직사각 방(L자형 등)은 실제 벽 폴리곤이 아니라 바운딩
  박스로 계산되어 여유공간이 생길 수 있습니다. 폴리곤 압출은 후속(Phase 4 대상).
- **장비는 장애물이 아니라 바닥층 체적 발열로 단순화**합니다. 총발열량(kW)은 정확히 반영되지만
  (에너지 폐합 검증됨), 개별 랙 위치의 국소 핫스팟은 못 잡습니다(snappyHexMesh 장애물화는 후속).
- **급기/배기는 도면에 없는 정보**라 `--supply`/`--exhaust`로 사람이 지정해야 합니다. 경계 개구부
  위치는 자동으로 안내하지만, 급기인지 배기인지는 판단하지 않습니다.
- **급기 = 벽면 전체(최소모델)**: 실디퓨저의 작은 취출면적이 아니라 벽 한 면 전체를 급기로 봅니다.
  풍량·유속이 실제와 다르고, 약유동은 수렴이 나쁩니다(§8-1). 실디퓨저 패치는 후속.
- **함수객체(functionObjects) 미사용**: 이 WSL apt OpenFOAM(v1912) 빌드는 `functions{}` 블록이
  SHA1 IOstream 버그로 전부 실패합니다. 대신 `cfd_report.py`가 최종 time의 ascii 필드를 직접 읽어
  단면·통계·에너지폐합을 계산합니다 — 사용자 입장에서는 차이가 없지만, controlDict에 함수객체를
  직접 추가하면 솔버가 즉시 죽으니 추가하지 마세요.

---

## 10. 문제 해결

| 증상 | 원인/조치 |
|---|---|
| `WSL 에 OpenFOAM 환경이 없습니다` | WSL에 `sudo apt-get install openfoam` 필요. §2 확인 명령으로 재확인. |
| `zone N 없음` | `geometry.json`에 A-ZONE closed polyline이 그 인덱스만큼 없음. `--zone` 대신 `--room-bbox` 사용. |
| `치수 추출 실패` | 도면에 wall/zone 좌표가 비어있음(파싱 자체가 안 됐을 가능성) — `dxf_parser.py --scan`으로 먼저 인벤토리 확인. |
| 리포트에 단면 그림이 안 나옴 | 최종 time 디렉토리가 회수되지 않음 — `cfd_run.py` 실행이 중간에 실패했을 가능성. `case_xxx/log.checkMesh`, `log.buoyantBoussinesqSimpleFoam` 확인. |
| 잔차가 안 떨어지고 발산 배지가 뜸 | `--endtime`을 늘리거나(정상상태라 보통 400~600이면 충분), `--supply-u`가 과도하게 크지 않은지 확인. |
| **배지가 `미수렴(에너지폐합 40%)`** | 부력지배 약유동. `--supply-u`를 0.3~3 m/s로 올리거나 `--endtime`을 크게. 잔차만 보지 말 것(§8-1). |
| `FOAM FATAL IO ERROR ... "sha1"` | controlDict에 함수객체를 직접 추가한 경우 발생. §9 참고 — 함수객체 쓰지 말고 `cfd_report.py`에 맡기세요. |

---

## 11. 재현 한 줄 요약

```bash
# A) config 직접
python cfd_export.py <config.json> -o <case> && python cfd_run.py <case> && python cfd_report.py <case>

# B) 도면에서(발열 kW + 에너지폐합 검증)
python cfd_export.py --from-geometry <geometry.json> --room-bbox "x0,y0,x1,y1" \
  --power-kw 10 --supply-u 0.3 -o <case> && python cfd_run.py <case> && python cfd_report.py <case>

# C) 격자 독립성 검증
python cfd_gridstudy.py --from-geometry <geometry.json> --room-bbox "x0,y0,x1,y1" \
  --power-kw 10 --cells 0.3,0.2,0.15 -o study
```

---

## 12. CFD 스튜디오 — GUI 통합 프로그램 (권장)

위 CLI 전부를 **브라우저 하나**로 쓰는 단일 프로그램. `run_cfd.bat` 더블클릭(또는
`python cfd_studio.py`) → 브라우저가 자동으로 열립니다. CLI 지식 불필요.

```
python cfd_studio.py                    # 기본: cfd_projects/ 폴더가 프로젝트 루트
python cfd_studio.py --root <경로>      # 다른 프로젝트 폴더
```

### 화면 구성
- **대시보드(홈)** — 모든 케이스를 표로 집계: 방 치수·발열·급기·평균/최고온도·**폐합%**·
  **GCI%**·수렴 배지. 열 클릭 정렬, 폐합 불량(90~110% 밖) 행은 붉게 강조.
  요약 카드(케이스/수렴/경고/미실행) + 실행 중이면 상단에 진행 스트립(단계·Time 진행바·로그).
- **＋ 새 해석 (마법사)** — 3단계 한 페이지:
  ① 방 정보: 치수 직접 입력 또는 `geometry.json` 불러오기(zone 목록·경계 개구부 자동 안내,
  급/배기 후보 벽에 ★ 표시) ② 해석 조건: 발열 kW(계산서값) + **급배기 방식 선택** —
  벽 전체(간이) 또는 **급배기구 지정(디퓨저/그릴 편집기)**: 행마다 역할/타입(4way·down·grille)/
  벽/위치/크기/CMH 입력, [📐 도면 디퓨저 불러오기]로 도면 장비블록 위치 자동 채움(CMH만 입력)
  ③ 확인: **예상값 박스**(ΣCMH·ACH·격자 셀수·계산서 손계산 ΔT — 실행 후 CFD 폐합 검증과 짝)
  → [생성] 또는 [생성＋즉시 실행].
- **실행 모니터** — 동시 1건 큐(WSL 경합 방지). blockMesh→checkMesh→solver 단계와
  Time 진행바가 1초마다 갱신, 완료 시 리포트 자동 생성.
- **케이스 행 동작** — [리포트](새 탭) · [결과](뷰어) · [실행/재실행] · [격자](격자 독립성
  검증: 셀 1.5c/c/c÷1.5 3케이스 배치 → GCI 배지가 표에 추가) · [삭제].
- **결과 뷰어** — SimScale식 인터랙티브:
  - **2D 단면**: 축(수평Z/수직X/수직Y) 선택 + 위치 슬라이더로 임의 단면 즉시 보기,
    마우스 올리면 그 지점 좌표·온도/유속 값, 기류 화살표 겹치기, 컬러바(케이스 고정 색범위).
  - **3D 컷플레인**: 방 와이어프레임 + 절단면 3장(x/y/z 슬라이더로 이동), 드래그 회전·휠 줌,
    파랑 면=급기·빨강 면=배기. 색·값은 2D와 동일 데이터.
- **비교** — 체크박스로 2~4개 선택 → [선택 비교]: 지표를 나란히(최고온도 최저 케이스 ★).
  같은 방의 풍량/발열 대안 검토용.

### 알아둘 것
- 케이스 = 프로젝트 루트의 폴더 하나(파일이 진실). 스튜디오를 꺼도 데이터는 그대로,
  재시작하면 재스캔됩니다. CLI로 만든 케이스 폴더를 루트에 복사해도 그대로 인식.
- WSL OpenFOAM이 없으면: 케이스 생성·조회는 되고, 실행 버튼에 설치 안내가 뜹니다.
- 실행 중 스튜디오를 끄면 WSL 쪽 계산은 계속되지만 결과 회수가 안 됨 → 재실행 하세요.
- 3D 뷰어는 `vendor/three.module.js`(로컬 내장) 사용 — 인터넷 불필요.
