---
name: drawing-set-intake
description: 새 현장·새 프로젝트의 도면 세트(DWG/DXF 여러 장)를 처음 받았을 때 반드시 여기서 시작한다. "이 도면 분석해서 전체 생성", "새 현장 도면", "도면 세트", "모델링 해줘", "3D 로 만들어 줘", DWG 경로를 여러 개 붙여 넣었을 때 사용한다 — 작업 폴더(저장소 밖) → DWG→DXF 변환 → sheet inventory(시트 종류 제안) → 범위 합의 → layer_map → parse → IFC build/verify → BOQ → preview URL → 창호(openings)까지 한 흐름으로 이끈다. 설비(MEP) 시트는 건축 파싱에서 갈라 설비 프로필 경로로 보낸다.
---

# 도면 세트 받기

```
작업 폴더 → 변환·인벤토리 → 금지 패턴 파일 → [범위 합의: 멈춤] → (종합평면도면 composite-plan-crop)
        → layer_map → parse_summary → ifc_builder + build_summary → boq_export → serve_project
        → 창호(window-schedule-join)            설비 시트(mep_*)는 4a절 — 건축 층을 정렬한 뒤
```

**전체 흐름이 필요 없는 경우** — 이 스킬은 *새 프로젝트의 도면 세트*를 받을 때의 입구다. 이미 한 층 DXF 한 장만
받았고 레이어도 아는 경우는 4절(layer_map)부터, 기존 모델에 층을 더하는 요청은 add-floor, 파싱 결과가 이상하다는
요청은 verify-model·map-layers 로 간다 — 인벤토리·범위 합의를 강제하지 않는다.

스크립트는 저장소 루트에서 부른다. 아래에서 `$S` = `.claude/skills/drawing-set-intake/scripts`.
Windows 에서는 `PYTHONIOENCODING=utf-8` 을 켜고 경로는 `C:\...` 로 준다(`/c/...` 는 Python 이 못 연다).

## 0. 작업 폴더는 저장소 밖에

저장소 옆에 `<날짜>_<이름>` 폴더를 만들고 **모든 고객 파생 파일을 거기 둔다** — 변환한 DXF, 인벤토리,
프로젝트 layer_map·block_map, 창호일람 xlsx, geometry·IFC·BOQ, `.mep` 프로젝트 폴더.

왜: 이 저장소는 **공개**다. 고객 레이어·블록·xref 이름 한 줄이 곧 현장 식별자다. 저장소 안에 두면
언젠가 `git add` 에 딸려 간다. 스킬·코드에는 `<frame block>`·`<unit xref prefix>` 같은 자리표시만 쓴다.

## 1. 변환 + 시트 인벤토리

```
python $S/sheet_inventory.py <DWG 폴더|파일들> --out-dir <작업 폴더>/inventory --max-mb 200
```

- 이 저장소의 정식 입력은 **DXF** 다(`CLAUDE.md` 범위). DWG 는 저장소의 `dwg_converter.ensure_dxf` 가
  **이미 설치된** 변환기로 `<out-dir>/dxf` 에 바꿀 뿐이다. 변환기가 없다고 나오면 **내려받지 않는다**(외부
  바이너리를 들이지 않는 게 범위다) — 사용자에게 CAD 에서 시트를 DXF 로 저장해 달라고 하고 그 DXF 로 잇는다.
- 시간(실측): 변환은 파일당 1~40 s. ezdxf 로드는 279 MB DXF 96~127 s, 40 MB 4~5 s(이 스킬 시험 두 번에서 잰 값).
  그래서 처음엔 `--max-mb 200` 으로 작은 시트부터 보고, 큰 것은 백그라운드로 **같은 `--out-dir`** 에 따로
  돌린다 — 파일 이름별로 합쳐지므로 앞의 행이 남는다. 다시 돌려도 `<out-dir>/dxf` 에 DWG 보다 새 변환본이
  있으면 재변환하지 않는다.
- `inventory.md` 의 **제안(suggested)** 을 표로 사용자에게 보인다:

| suggested | 뜻 | 다음 |
|---|---|---|
| `composite_plan` | 한 파일에 여러 층 — 같은 도곽 블록이 여러 번(도곽 후보) | 3절 composite-plan-crop |
| `plan` | 한 층 평면 | 4절 layer_map |
| `structural_plan` · `structural_composite_plan` · `structural_sheet` | S- 역할이 엔티티의 절반 넘음(한 층 / 여러 층 / 제목 없음) | 구조 평면. 여러 층이면 먼저 3절. 기둥·보는 map-layers |
| `mep_*`(`mep_plan` 등) | 설비 시트 — 파일 이름·평면도 제목의 과반에 설비 낱말, 또는 M-/P-/FP-/E- 역할이 A- 보다 많음 | **4a절** — 건축 layer_map 으로 파싱하지 않는다 |
| `section` · `elevation` | 단면도·입면도 — 층고·창 높이의 출처 | 층고 단계(나중) |
| `door_schedule` · `window_schedule` | 문·창호 일람표 | window-schedule-join |
| `window_cards` | 창호 전개도(부호·크기·창대 높이 카드) | window-schedule-join |
| `mark_plan` | 창호 안내도(평면 위 부호 지시선) | window-schedule-join |
| `detail` · `site_plan` · `unknown` | 상세도·배치도·판단 못함 | 열어 본다 |

  제안은 **파일 이름 → 제목 문자열의 키워드 + 역할 접두**일 뿐이다(부재 분류가 아니다). 확정은 사람이 한다.
  실측: 창호 시트 4장(문 일람·창호 안내도·창호 전개도 2장)을 파일 이름 없이 제목 문자열만으로 돌려도 같은
  답이 나왔다. 40자 넘는 글자는 일반사항 주석이라 제목에서 뺀다(주석의 '평면도 기준임' 이 제목 행세를 했다).
- `roles` = 레이어 이름의 마지막 `$0$` 뒤(역할). `inventory.json` 에 **전부** 있다(`.md` 에는 없다).
- `frames` = 도곽 후보: 같은 블록 2회 이상, 긴 변 20 m 이상, **용지 모양**(가로세로비 √2 ±2%). 용지 모양을
  안 보면 단면 표시·범례 블록이 도곽 행세를 해 잘라낸 한 층도 `composite_plan` 이 됐다(실측: 잘라낸 기준층의
  78.69×68.52 m 단면 표시 블록 ×2, 창호 전개도의 26.46×3.0 m 띠 블록 ×18). 비율로 걸러도 **후보**다 —
  한 층 평면에 용지 모양 블록이 두 번 있으면 여전히 걸린다. `composite_plan` 은 가장 많이 반복된 도곽 하나로
  판정한다(실측 종합평면도: 도곽 ×25, 126.15×89.1 m).

## 1b. 금지 패턴 파일부터 — 커밋 전 스캔의 재료

인벤토리가 `<out-dir>/forbidden_candidates.txt` 를 쓴다(xref 이름 = 레이어의 마지막 `$0$` 앞 조각들, 이름 있는
블록, 파일 이름의 도면 번호 계열, 익명 블록 일반 패턴 두 줄). 이것을 **작업 폴더의** `forbidden.txt` 로
옮기며 다듬는다 — 일반 이름(`DOOR`·`NORTH` 류)은 지우고, 도면에 없는 것을 더한다:

- 현장명·고객명·발주처·팀 이름 — **국문과 영문(로마자) 표기 둘 다.** 사용자에게 묻는다.
- 작업 폴더 이름과 사용자 폴더 경로 조각.

왜: 커밋 스캔(아래 '고객 자료 위생')은 패턴 파일이 아는 것만 잡는다. 앞 현장의 패턴 파일은 새 현장의 이름을
모른다 — 그대로 쓰면 **0건으로 통과하고 공개 저장소로 샌다.** 이 파일은 저장소에 두지 않는다(목록 자체가 유출).

## 2. 범위를 제안하고 멈춘다

"전체 생성" 을 받아도 전 층을 한 번에 만들지 않는다. 이 셋을 제안하고 **사용자의 yes 를 받는다**:

1. **기준층 하나 먼저.** 첫 실무 세트의 기준층 하나에서 파서·IFC 결함 넷(길이 0 벽·맞닿기만 한 개구부·
   허용치 아래 절삭 등)과 개구부 결함 여러 개가 드러났다. 한 층에서 고치고 나머지 층에 번지는 게 싸다.
2. **층고는 단면도에서, 나중에.** 평면도에는 고저가 없다. 추정 층고로 여러 층을 쌓으면 V004(층 사이
   공백)와 싸우게 된다 — 층을 올리는 건 add-floor 스킬.
3. **설비(MEP)는 건축 위에 나중에 겹친다.** 종합평면도에서 잘라낸 층은 시트 좌표를 그대로 가진다
   (실측: x ≈ 2,050 m). 설비 도면과 겹치려면 정렬이 먼저다. 설비 시트가 세트에 있어도 4a절 경로로 간다.

## 3. 종합평면도면 → composite-plan-crop

판별: 인벤토리가 `composite_plan`, 도곽 후보가 층 수만큼 X 로 나란히(실측: A1 도곽 1/150 = 126150×89100 mm),
레이어가 `<floor xref>$0$<역할>`.

**레이어로 걸러 층을 뽑지 않는다.** 세대 평면·코어는 도곽 안에 놓인 **블록 INSERT** 라 층 레이어에 없다 —
레이어로 거르면 세대가 통째로 빠진다. composite-plan-crop 스킬로 도곽 하나를 잘라 평평한 DXF 와 푼 삽입의
변환 사이드카(나중에 세대 좌표 부호를 얹을 때 필요)를 만든 뒤 4절로 온다. 한 층짜리 평면이면 건너뛴다.

## 4. 프로젝트 layer_map

```
copy .claude\skills\drawing-set-intake\references\layer_map_template.csv <작업 폴더>\layer_map_project.csv
```

`inventory.json` 의 `roles`(전부) 또는 `python dxf_parser.py <층.dxf> --scan` 의 레이어 목록에 **실제로 있는**
역할 이름으로 고친다 — 개수 상위만 보지 않는다(실측: 잘라낸 기준층 역할 89개 중 `A-WIN` 18위, `A-DOOR` 41위인데
개구부 91개가 이 두 줄에 달려 있다). 증상별 처방·opts 전체는 map-layers 스킬과 `CLAUDE.md`.

- **패턴을 `$` 로 끝낸다**(`A-WALL$`). 종합평면도 레이어는 `<xref>$0$<역할>` 이고 앞쪽 xref 이름은 층·세대
  타입마다 다르다. 꼬리로 맞춰야 한 줄이 모든 층·세대에 걸린다. `$` 가 없으면 `A-WALL` 이 `A-WALL-DRY` 에도
  걸린다.
- **벽 행마다 `pair_min=50`.** 건식벽은 보드 면선과 안쪽 선이 10 mm 로 붙어 그려져 가까운 선끼리 짝지으면
  10 mm 벽이 된다(실측: 기준층 건식벽).
- **개구부는 `subtype=door|window` 를 선언한다.** 종류를 알아야 창대 벽·인방을 채우고 창 높이만큼만 뚫는다.
  모르면 층 전체 높이로 뚫린다. 블록 이름에 `D-900`·`W-1200` 같은 부호가 없는 세트가 흔하다.
- **마지막 줄 `.,ignore`.** 저장소 기본 `layer_map.csv` 의 넓은 규칙(ROOM→zone, A-ELE→wall)은 xref 배경을
  오분류한다. 대가: 이 줄 뒤로는 **미매핑 경고가 하나도 안 나온다** — 패턴에서 빠진 벽·문 레이어가 조용히
  버려진다(카테고리 전체가 0 이 되지 않는 한 티가 안 난다). 그래서 **파싱할 때마다 `ignored_roles`(5절)를
  읽고** 벽·문·창으로 보이는 역할은 규칙으로 올린다. 실측(기준층): 버린 레이어 276개·엔티티 2,978개 속에
  틀에 없는 문 역할(엔티티 57)·벽 역할(8)이 섞여 있었다.
- 이름을 보고 부재를 추측하지 않는다. 값은 선언(layer_map/block_map opts)이나 잰 기하에서만 온다 — 파서는
  재분류하지 않고, 사람이 CSV 를 쓴다. 틀의 `S-CONC$→wall` 도 벽식 구조에서만 맞았다(실측 기준층: 기둥 0) —
  라멘조면 같은 레이어의 기둥 외곽선을 map-layers 대로 먼저 나눈다.

## 4a. 설비(MEP) 시트는 이 길로 오지 않는다

`mep_*` 시트를 건축 layer_map 으로 파싱하지 않는다. 설비 평면에는 배경 건축 xref 가 bind 돼 있고(`<xref>$0$A-WALL`)
틀의 `A-WALL$` 이 바로 그 배경을 벽으로 세운다. 실측(`CLAUDE.md` mep_gui 행): 단위세대 환기 평면을 그냥
파싱했더니 검토 대기 43건이 전부 배경 건축이 된 기둥·벽이었다.

- 설비 프로필 경로로 간다 — GUI `설비 도면 설정`, 또는 MCP `propose_mep_profile` → 사람이 확인 →
  `apply_mep_profile_proposal`. 프로필은 고객 레이어 이름을 담으므로 작업 폴더에 둔다.
- **순서: 건축 층을 먼저 만들고 정렬한 뒤.** 잘라낸 층은 시트 좌표(2절 3)라 정렬 없이 겹치면 어긋난다.
- 제안이 `mep_*` 인데 건축 시트로 보이면(설비 낱말이 든 건축 제목 등) 사람이 판단한다 — 제안일 뿐이다.

## 5. 파싱 + 지표

```
python $S/parse_summary.py <층.dxf> --layer-map <lm.csv> [--block-map <bm.csv>] [--schedule <창호일람.xlsx>] --out <작업 폴더>/<이름>
```

`<이름>.geometry.json` + `<이름>.log`(파서 출력 전부)를 쓰고 지표를 찍는다. 시간(이번 측정): 115 MB 기준층 68 s.
**고칠 때마다 같은 지표를 다시 찍고 전후 숫자를 사용자에게 보인다.** 개구부 결함은 "더 이상해 보인다" 로
처음 드러났다 — 숫자 없이 눈으로만 보면 고친 것이 나빠진 것으로 보인다.

| 지표 | 볼 것 |
|---|---|
| `elements` | 있어야 할 카테고리가 0 → 패턴 오타(map-layers) |
| `infill_walls` | 창대 벽·인방(`source=opening_infill`). 창이 있는데 0 → subtype 미선언 |
| `subtype` | `None` 이 남으면 그 개구부는 층 전체 높이로 뚫린다 |
| `openings_dims_assumed` | 높이·창대가 **추정**인 개수. 0 이 아니면 창호일람이 필요 → window-schedule-join |
| `width_source` | `default` = 폭을 못 찾아 900 으로 때움(`dims_assumed` 에 width 로도 센다) |
| `dims_source` | `schedule` = 일람과 조인됨 |
| `no_host_reason` | `wall_open_at_this_span` = 제도자가 문 자리에서 벽을 끊어 그렸다(조치 없음) · `no_wall_on_this_line` = 벽 축선에 안 걸림(layer_map 질문). 전체 표는 `docs/decisions/openings.md` |
| `opening_fragments_dropped` · `opening_infill` | 기호 조각으로 버린 수 · 채운 벽·건너뛴 사유 |
| `shadowed_layer_rules` · `warnings` | 가려진 규칙(선매칭 함정) · 경고 앞 5줄은 바로 찍힌다 |
| `ignored_roles` | `.,ignore` 가 삼킨 역할 **전부**(개수 순). 벽·문·창 역할이 보이면 layer_map 에 올린다(4절). 상위만 보면 놓친다 — 기준층의 벽 역할은 77개 중 43위였다 |

## 6. IFC 빌드 + 검증

```
python ifc_builder.py <작업 폴더>/<이름>.geometry.json <작업 폴더>/<이름>.ifc     # → <이름>.build.json
python $S/build_summary.py <작업 폴더>/<이름>
```

- `status: verified` 가 아니면 verify-model 스킬로 간다. **"빌드 완료" 로그를 믿지 말고 영수증(`build.json`)을
  읽는다** — 검증에 실패하면 `.ifc` 가 아예 안 나가는 게 정상이다.
- `panels_without_host_cut` 은 개구부 패널을 세웠지만 뚫은 벽이 없는 것. `uncut_by_no_host_reason` 으로
  나눠 5절 표대로 읽는다. `failed_hosts` 는 절삭 실패 사유, `verify`·`verify_ifc` 는 검사 ID 별 개수.
- 기준(실측, 이 스킬 시험에서 재현): 기준층 `verified`, 창호일람 조인 뒤 V106 2.

## 7. 물량

```
python boq_export.py <작업 폴더>/<이름>.geometry.json <작업 폴더>/<이름>_BOQ.xlsx
```

## 8. 미리보기 URL

```
python $S/serve_project.py <층.dxf> --folder <작업 폴더>/<이름>.mep --layer-map <lm.csv> [--block-map ...] [--schedule ...]
```

백그라운드로 돌리고 찍힌 `URL` 을 사용자에게 준다. 계속 떠 있다(`--hours`, 기본 2).

- **새 block_map·일람이면 새 `--folder`.** 기존 프로젝트 폴더는 **저장된** 원본 설정을 쓴다 — 인자를 바꿔도
  옛 설정으로 뜬다. 스크립트는 저장된 layer_map·block_map·일람과 인자가 다르면 — **인자를 뺀 경우도** — 멈춘다.
- **파서를 고쳤으면 서버를 내리고 다시 띄운다.** 떠 있는 서버는 옛 파서 모듈을 들고 있어 새 코드가 안 보인다.

## 9. 창호 → window-schedule-join

subtype 을 선언하면 개구부가 **더 이상해 보일** 수 있다. 원래 있던 결함이 보이게 된 것이다(실측: 기준층):
블록 창 67개가 전부 폭 900 을 삽입점(= 창의 한쪽 끝)에 두어 반쪽 창대·인방 토막이 39곳, 선 레코드 69개가
실제로는 개구부 14개, 문 철물 원을 개구부로 잡아 버려 현관문 6개가 사라짐. 전부 파서에서 고쳐졌다 —
다시 보이면 파서 회귀이므로 증상과 지표를 들고 보고한다.

평면에 창호 부호가 없으면(흔하다) window-schedule-join 스킬로 간다 — 블록 종류를 부호로 선언하고
(block_map `opts: mark=`) 창호 전개도·문 일람·안내도에서 일람 xlsx 를 만든다. 이 흐름에서 지킬 것 셋:

- **높이는 일람에, block_map 에는 넣지 않는다.** block_map 의 height 는 `overrides` 가 되어 일람을 이긴다.
- **제안 뒤 독립 검증을 한 번 더 돌린다** — 원본에서 행마다 다시 유도한다. 첫 제안에서 8건이 틀렸다
  (들린 문 창대 누락·미서기문 종류·방마다 다른 창대·9건 미결 등).
- 결과(실측: 기준층): 개구부 136 → 91, 블록 개구부 72개 전부 일람 조인(가정 0), 선으로 그린 19개만 가정,
  창대 벽·인방 137개 전폭, IFC verified, V106 11 → 2.

## 고객 자료 위생 + 커밋 전 스캔

- 스킬·코드·테스트·**커밋 메시지** 어디에도 현장명·고객명, 고객 레이어/블록/xref 이름, 도면 번호, 사용자
  폴더 경로를 쓰지 않는다. 일반화한 실측("종합평면도 기준층에서 …")은 괜찮다.
- 금지 패턴 목록은 **로컬에만** 둔다 — 1b절의 `<작업 폴더>/forbidden.txt`(`docs/release_checklist.md` '골든
  등록' 3번: 목록을 저장소에 적는 것 자체가 유출). 스테이징 diff **와** 커밋 메시지 둘 다, **커밋하기 전에**
  검사한다. 메시지는 파일로 써서 검사한 뒤 그 파일로 커밋한다. 파이프를 `head` 로 자르지 않는다:
  ```
  git diff --cached | grep -inE -f <작업 폴더>/forbidden.txt          # 0건이어야 한다
  grep -inE -f <작업 폴더>/forbidden.txt <작업 폴더>/msg.txt           # 0건이어야 한다
  git commit -F <작업 폴더>/msg.txt
  git log --format=%B @{u}..HEAD | grep -inE -f <작업 폴더>/forbidden.txt   # 푸시 전 한 번 더
  ```
  diff 만 보고 메시지를 안 봐서 현장명이 로컬 커밋에 들어간 적이 있다 — 커밋 뒤의 로그 검사로는 늦다.
- `git add -A` 금지 — 파일을 지정한다(같은 트리에 다른 세션의 미커밋 작업이 있다). 푸시는 사용자 승인 뒤에만.

## 하지 말 것

- 전 층을 한 번에 — 2절에서 멈추지 않고 진행.
- 변환기·뷰어 내려받기, 저장소 안에 작업 파일 두기.
- 설비 시트를 건축 layer_map 으로 파싱하기(4a절).
- 레이어 이름으로 부재를 추측하거나 파서 결과를 사후 재분류. LLM 은 형상을 그리지 않는다 — `geometry.json`
  은 파서가, 값은 선언과 잰 기하가 채운다.
- 일람 미매칭·추정 치수를 기본값으로 때우고 말하지 않기 — `dims_assumed` 개수를 그대로 보고한다.
- 층마다 임시 파이썬 스크립트 작성. 이 폴더 스크립트와 저장소 CLI 로 안 되면 그건 보고할 결함이다.
