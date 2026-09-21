# Planform 참조 분석 (2026-09-21)

대상: https://planform.keystonehub.io ("Planform — 도면에서 공간으로", LOCAL WORKSPACE v0.1).
방법: 브라우저에서 앱의 모든 다이얼로그·탭·워크벤치 문구를 직접 읽어 기록한 뒤(노트는 세션 스크래치),
네 분야(입력·편집기 / 검토·근거·게이트 / 시각화·수량 / 프레이밍·범위)를 각각 우리 코드와 대조하고,
검증자 한 명이 모든 "참조 가치 있음" 항목을 코드로 다시 확인했다(줄 번호는 2026-09-21 작업 트리 기준).
사용자 목표는 [2026-09-18 계획서](2026-09-18-easy-loop.md) §0 그대로 — **쉽고 재미있게 3D·간섭·물량, 빌드 뒤 수정도 쉽게.**

---

## 0. 한 줄 결론

Planform 은 **건축 외관 검토·프레젠테이션용** 도구다 — 배관·덕트·간섭·시공기준이 없고, 이중 벽선 병합도
"지원하지 않는다"고 스스로 적었다. 우리가 가져올 것은 **기능이 아니라 편집기 손맛과 정직한 문구** 여섯 개
(Redo · Shift 직교 · 팬 · 물량 각주와 "검토용" 배지 · 합계 상시 표시 · 2D 편집+3D 나란히), 전부 합쳐 ~100줄이다.
Planform 이 자랑하는 "근거·확인·해시 게이트"는 우리가 **부재 단위로 더 세밀하게, 기록이 아니라 차단으로** 이미 한다.

---

## 1. Planform 이 무엇인가 (관찰)

- 입력: PDF·PNG·JPG(래스터 → 두 점 축척 보정 → "수평·수직 검은 선분" 벽체 후보 추출) 또는 DXF(단위·중심선
  레이어 선택). **"DXF의 이중 벽선은 자동으로 하나의 중심선으로 합쳐지지 않습니다."**
- 부재: 직선 벽·직사각 개구부·평면 슬래브·지붕·직선 계단·슬래브 개구부·가구 15종·조명·대지·주변 건물. **MEP 없음.**
- 편집기(2D LIVE PLAN): 선택 / 벽 그리기 / 벽 삭제 / 두 점 축척 / 실행 취소·다시 실행 / Shift 직교 / Space+드래그 팬 /
  그리드 1 m / 스케일바 / 공간 이름·면적·치수 라벨. **나란히 보기**(2D 편집 + 3D 동시).
- 3D: 조감·평면·정면 프리셋, 지붕 숨기기, 벽체 단면, 층 분리 슬라이더, 부재별 분해, 와이어프레임, 시간대 조명(태양 고도),
  저장 시점 투어. 하단 상시 지표 4개(슬래브 면적·벽 총길이·벽 개수·문/창).
- 실무 작업실(워크벤치): 실무 기준 프로필(출력 목적·적용 기준·수신 도구·치수 허용차·확인자 — **"기록하며 검증은 수행하지
  않습니다"**), 원본 문서 등록(해시, 개정 시 확인 기록 무효화), **객체별 근거 확인**(값의 근거 = 원도면·실측·제품 자료·가정,
  확인자, 객체 sha256), 검토·도면 탭(기록 모델 sha256 ≠ 현재 모델 → "변경 후 재검토 필요", 검토 완료 전엔 PDF·DXF·IFC4·렌더 비활성).
- 내보내기: GLB · OBJ · Blender .py · 벽체 수량 CSV · 프로젝트 JSON(원본 도면 포함). 각주: **"수량은 개구부를 차감한 벽 중심선
  면적 기준이며, 접합부 중복·마감·할증은 포함하지 않습니다."**
- 태도: 모든 화면에 "…은 아닙니다"(실측·시공 승인 모델 아님, 인증 아님, 측량 결과 아님, 날씨 미반영). 검토 미완료 건수가
  사이드바·탭·하단에 상시 배지. 파일은 브라우저에서 처리, "이 기기에 저장됨".

---

## 2. 채택 권고 (우선순위순)

공통: `frontend/src` 를 고치면 `npm run build` 로 `frontend/built/` 를 갱신한다(커밋 대상). 1~6 이 한 묶음(리빌드 1회).

| # | 항목 | Planform 근거 | 우리 격차 (파일:줄) | 크기 | 목표 기여 |
|---|---|---|---|---|---|
| 1 | **Redo + 힌트 + undo/redo 테스트** | "실행 취소(⌘Z) / 다시 실행" | `app.js:431-448` UNDO 스택만(pop 만, REDO 없음). HINT2(`:821-838`)에 Ctrl+Z 안내 없음. `tests/` 에 undo 스택 테스트 0건 | ~15줄 + 테스트 1 | 편집을 겁 없이 — "버튼 하나로 고침"의 전제 |
| 2 | **Shift 직교** | "Shift 직교 벽 그리기" | `edit_geometry.js:198-216 snapPoint` 는 끝점·선분 스냅만. 그리기 `app.js:1070-1077`·끌기 `:1094` 에 축 고정 없음 | 순수 함수 ~5줄 + 호출 2곳 + 테스트 1 | 손으로 벽·배관을 그릴 때 직교 없으면 못 쓴다 |
| 3 | **평면 탭 팬(중버튼/Space+드래그)** | "Space + 드래그 이동" | `svg2` 의 viewBox 변경은 fit(`app.js:913-918`)·휠(`:1123-1128`)·탭 전환(`:1174`)뿐. **원본 DXF 창에는 같은 형태가 이미 있다**(`sourceDrag :595, 700-702`) | ~15줄(복제) | 확대하면 이동 불가 — 큰 도면에서 편집이 막힌다 |
| 4 | **물량 각주 + "검토용 모델" 배지** | 각주 "개구부를 차감한 … 접합부 중복·마감·할증은 포함하지 않습니다", 설명문 "실측·시공 승인 모델은 아닙니다" | 벽 면적 `L*h` 개구부 **미차감**(`boq_export.py:78`)인데 어디에도 안 적혀 있다. Excel 2행 meta 는 커버리지·단위만(`:265`). 패널 머리는 층고 출처 한 줄(`review_logic.js:304-308`). "개수(하한)"이 열 이름으로만(`:224`). 화면·Excel·GUI 로그에 "검토용" 문구 0건(grep) | ~8줄 · 3파일 | Excel 은 **남이** 읽는다 — "개구부 차감했겠지"가 물량 오류로 직결 |
| 5 | **물량 합계 상시 표시** | 하단 지표 "슬래브 면적·벽 길이·벽 개수·문/창" | `summarizeBoq`(`review_logic.js:290-300`)가 `tot` 행을 버린다. `aggregate()` 에 벽 합계(`boq_export.py:87-90`)·슬래브 합계(`:126`)·창호 문/창별 행(`:156-168`)이 **이미 있는데** 패널이 안 그린다. `#counts` 는 `opening N`(`app.js:1386-1387`) | ~15줄 · 2파일 | "고치면 물량이 바로 바뀐다"가 한눈에 |
| 6 | **나란히 보기(2D 편집 + 3D)** | "나란히 보기" | `#linkedWorkspace` 는 원본DXF+3D(`shell.html:37-44`), `#view2d` 는 오버레이(`style.css:32`)라 편집 탭이 3D 를 덮는다(`app.js:1166`). `syncEffective→rebuild()` 가 이미 매 편집마다 3D 를 재생성(`:470-474`) | CSS ~10줄 + `setTab` 모드 1 | 끝점을 끌면 옆에서 3D 가 바뀌는 것 — 계획서 §0 "재미"의 정의. 3D 는 계속 보기 전용(edits-and-preview.md '2D 평면 탭') |
| 7 | 원본 변경 배너 + revision 표시 | "개정·해시 변경 시 재검토 상태", "저장 버전" | `project.changed_inputs` 를 state 에 싣지만(`project_server.py:59-60`, `project_store.py:311`) **소비자 0**(GUI·app.js grep 0). revision 은 받되(`app.js:516, 528`) 문구에 없음(`setSaveStatus :476-481`) | ~5줄 | 확인이 왜 만료됐는지 사용자가 안다 |
| 8 | GUI `납품 ▾` 라벨에 `(검토 대기 N)` | 배지 상시 | 첫 화면 넷(`mep_gui.py:322-341`)에 건수 없음. `_parse_done`(`:855-890`)이 needs_review·간섭 total·이음 후보를 **로그에만** 쓴다 | ~10줄 | 납품 누르기 전에 남은 일이 보인다. Python 이라 `buildReviewEntries` 를 못 쓰니 로그 값의 합(근사치) |
| 9 | 샘플 열기 버튼 | "샘플 DXF 다운로드", 업로드 없이 시험 | `sample_plan.dxf` 는 동봉(`mep_parser.spec:22`)이지만 `--selftest`(`mep_gui.py:1441`)만 쓴다. `_do_open`(`:454-485`)은 파일 대화상자부터 | ~8줄(`_do_open` 꼬리를 `_open_path(path)` 로 분리) | 도면 없이 첫 실행이 된다 |
| 10 | **사용설명서 정정**(§4 참조) | "현재 지원 범위/미지원" 문단 | `사용설명서.md:25` "DXF 또는 DWG", `:31`·`:116` "(4) IFC 빌드(FreeCAD 불필요) 권장" — CLAUDE.md(DXF 만·`ifc_builder` 동결)와 반대. 버튼 순서도 옛 GUI | 문서 ~50줄 | 첫 사용자가 동결 경로로 간다 |
| 11 | 확인자·시각 기록 | "근거 확인자", "검토자 이름" | `acknowledge` 는 bool(`project_store.py:74`, `project_server.py:463-471`), `decisions` 에 시각·사람 없음(`:207-209`, `_commit :241-246`). `validate_edits`(`project_store.py:53-83`)는 모르는 키를 거절하지 않으므로 `by`·`at` 는 송신부(브라우저·GUI·MCP)만 고치면 통과 | ~20줄 · 3파일 | 낮음 — 납품 책임 추적용 장부 |
| 12 | `options.verify` 저장 경로 | "허용차·기준 기록" | `verify_geometry` 가 `data['verify']['severity']`·`coverage_min_pct` 를 읽는데(`verify.py:114-124, 256`) **producer 0**(`test_verify.py:213` 만 직접 세팅) | ~25줄(`configure_defaults` 옆) | 낮음 — 필요가 측정되면. purpose·recipient·tolerance 는 넣지 않는다(§3) |
| 13 | 층 분리 슬라이더 | "층 분리" | 3D 는 수평 단면 클리핑만(`app.js:704-729`), 메시 z 는 `z_base` 그대로(`:81`) | ~20줄 | 낮음 — 다층 프로젝트에서만 |
| 14 | 선택 부재 길이 라벨(2D) | 치수 라벨 | `render2` 에 `mk2('text')` 0건(`:928-997`) | ~6줄 | 낮음 |
| 15 | 1 m 그리드 | "그리드 1 m" | 2D 배경색만(`style.css:32`), 3D 만 `GridHelper`(`app.js:59-60`) | ~10줄 | 낮음 — 원본 겹쳐 보기가 기준 역할 |

### 상위 6 착수점
1. **Redo** — `app.js:431-448` 의 `UNDO/pushUndo/undo` 를 `review_logic.js` 의 `createHistory()`(push/undo/redo, push 가 redo 를 비움)로 빼고,
   `:445-448` keydown 에 `y` / `shift+z`. HINT2 `select` 문구(`:822`)에 "Ctrl+Z 되돌리기 · Ctrl+Y 다시". 서버 응답이 `_lastSnap` 을 갱신하는
   `:529` 에서도 redo 를 비운다. 테스트: `tests/preview_review.test.mjs`.
2. **Shift 직교** — `vendor/edit_geometry.js` 에 `orthoPoint(from, to)`(|dx|≥|dy| 면 y 고정) → `app.js:1032 snapPoint(...)` 호출 앞에서
   `ev.shiftKey` 면 기준점(그리기 `drawFrom :1071`, 끌기 `cl[1-dragging.i]` 또는 이웃점 `:1093`)으로 투영. 테스트: `preview_edit_geometry.test.js`.
3. **팬** — `svg2` `pointerdown`(`app.js:1062`) 맨 앞에 `ev.button===1 || spaceHeld` → `pan={last:evtWorld(ev)}`; `pointermove :1090`/`pointerup :1105` 에
   `sourceDrag`(`:700-702`)와 같은 `vb.x-=dx; vb.y-=dy; applyVB()`. Space 는 `window keydown/keyup` 플래그.
4. **각주·배지** — `boq_export.py:265` meta 에 " · 벽 면적·체적은 중심선 길이×높이×두께(개구부 미차감), 접합부 중복·마감·할증 미포함, 지지 개수는 하한 · 검토용";
   `review_logic.js` 에 같은 상수 export 해 `renderBoq`(`app.js:509-512`)가 `#boqHeightBasis` 뒤에 붙임; `shell.html:57 <h2>` 에 `<span class="muted">검토용 모델</span>`.
   테스트: `tests/test_boq.py`(Excel meta), `preview_review.test.mjs`.
5. **합계** — `summarizeBoq`(`review_logic.js:290`)에 `wallTotal:{count:tot[1], lengthM:tot[2], volumeM3:tot[5]}`, `slabAreaM2 = (boq['슬래브']||[])[2]?.[2]`,
   문/창 = 창호 rows kind 별 합 → `boqBodyHtml` 첫 줄. `aggregate()` 는 이미 다 계산한다.
6. **나란히** — `setTab` 에 모드 하나: `svg2` 를 `sourcePane` 자리에, `threeHost` 유지. 편집 저장 경로는 그대로(3D 는 보기 전용).

---

## 3. 참조하되 하지 말 것

| 항목 | 사유 | 근거 |
|---|---|---|
| 층 추가 → 즉시 빈 평면 | 입력은 DXF 만, 층은 도면+z 선언(`stack.json`). 빈 층은 그릴 근거가 없고 새 저장 동사가 된다 | CLAUDE.md 범위, 계획서 §14 "새 저장 동사" |
| 이미지·PDF 벽체 후보 추출, 래스터 두 점 축척 | 벡터만·결정론. DXF 의 '두 길이로 배율 계산'은 이미 있다(`drawing_units_ui.py:45, 84-88`) | CLAUDE.md "이미지/PDF/스캔본 입력 없음" |
| 검토 완료 전 출력 비활성 | 우리 설계는 미확인을 **속성으로 실어 보낸다**(`NeedsReview` Pset, `artifact_validation.py:43-45`). 실측: 검토 대기 43건이 배경 XREF 한 줄이 원인. 게이트는 verify error 만(`freecad_builder.py:1277-1284`), V007 은 warn(`verify.py:40`) — 강한 현장은 severity 정책으로 올린다(`:114`) | edits-and-preview.md '미리보기는 왜' 절, CLAUDE.md mep_gui 행 |
| 개구부 차감 물량(각주는 넣되 차감 자체는 안 함) | 건축 물량산출 관행의 1차 출처가 없다 — 형상을 바꾸는 자리 | construction-rules.md '하지 않은 것' |
| 부재별 분해·시간대 조명·시점 투어·가구·재질 텍스처·렌더 | 목표는 간섭·물량. 렌더는 Bonsai 권고 | 계획서 §0·§14, CLAUDE.md 6.5 |
| 프로젝트 JSON 에 원본 도면 묶기 | 고객 도면 사본이 늘어난다(공개 저장소). 지문(`project_store.py:137-140`)이 이미 변경을 잡는다. `_open_project` 의 경로 누락 안내 문구 1개만 | project_workflow.md, 메모리 `public-repo-client-data` |
| 값의 출처를 4분류 한 필드로 | 축마다 다른 함수(`geom_contract.height_basis`, `pairing`, `dims_assumed`, `elevation_source`, `declaration_basis`) — 뭉치면 높이·두께 출처가 섞인다. '실측·제품 자료'는 범위 밖. 선택: IFC Pset 에 `HeightBasis` ~10줄(지금은 clash CSV 에만) | CLAUDE.md z 기준면 절 |
| 납품 프로필의 purpose·recipient·tolerance | 소비자 없음. Planform 자신도 "허용차는 기록만, 검증은 수행하지 않습니다" | 계획서 §14 "필요가 측정되면" |
| 공간 이름 라벨 | A-ZONE 폴리라인에 이름 근거가 없다 | CLAUDE.md zone 귀속 |

---

## 4. 우리가 이미 더 잘하는 것 (Planform 이 못하거나 안 한다고 적은 것)

1. **이중 벽선 자동 병합** — Planform 명시 미지원. `dxf_parser.py:1296 _find_wall_pairs`, `:1354 detect_wall_pairs`, `:2770 layer_evidence`(레이어 이름이 틀려도 평행 짝 비율로 증거).
2. **MEP·간섭·시공기준·피팅** — Planform 은 건축만. `clash_review.py:214 find_clashes`(슬리브 규격 `:264`), `mep_network.py:150 analyze`, `construction_rules.py:101 RULES · :634 review · :643 supports · :649 insulation`, `geom_contract.py:858 joint_fittings`.
3. **부재별 검토 시그니처** — Planform 은 모델 전체 sha 하나(벽 하나 옮기면 54개 확인 전부 재검토). 우리는 `element_id.py:253 review_signature` → `:299 finalize_reviews` 가 바뀐 부재만 `review_ack_stale`.
4. **출력물 재검사 + 차단** — Planform "IFC 는 참조 모델", "CAD 규격 검증은 수행하지 않습니다". 우리는 `verify.py:43-47 V101~V107`, preflight 실패 시 산출물 없음(`freecad_builder.py:1277-1284`), `artifact_validation.py:13-21` run_id·input_sha256 → Pset, 바이트 해시 대조.
5. **클릭하면 이유** — Planform 은 사람이 출처를 수기로 고르는 기록. 우리 `app.js:370-407 whyHtml` 은 실측≠선언 두께·추정치·일람표 미매칭·끝점 간격 **조치문**을 파서가 자동으로 낸다; `reviewBannerText`(`review_logic.js:72-83`)가 "간섭 N건 중 가정 높이 M건".
6. **원본 대조** — `app.js:636 updateSourceSelection`(원본 DXF↔부재 교차 하이라이트), 편집 탭 원본 겹쳐 보기.
7. **저장 즉시 재계산 + 충돌 보호** — 간섭·연결·BOQ 가 저장마다(`project_server.py:59-66`), revision CAS(`project_store.py:241`).
8. **다층 자동 정합** — Planform 층 추가는 빈 평면. `stack_build.py:79 resolve_offset` 이 통심선으로 offset 을 풀고 모호하면 멈춘다(`:99-102`).
9. **물량의 정직성** — 길이기준 열(`boq_export.py:189-201`), 높이 섞이면 대표값 안 씀(`:83`), 지지(하한)·이음·보온 섹션. Planform 은 벽 길이·면적·개구부 수 CSV 한 장.

---

## 5. 덤으로 드러난 것 (Planform 과 무관하게 고쳐야 할 것)

- **`사용설명서.md` 가 현재 제품과 다르다** — "DXF 또는 DWG"(`:25`, `:67`), "(4) IFC 빌드(FreeCAD 불필요) 권장"(`:31`, `:116`), 옛 버튼 순서. CLAUDE.md 는 DXF 만·`ifc_builder` 동결·첫 화면 넷. 첫 사용자가 동결 경로로 간다. → 위 표 #10.
- **DWG 범위가 문서 둘에서 서로 다르다** — CLAUDE.md "DWG 는 지원하지 않는다 … 외부 바이너리 의존성 없음" 인데 `mep_gui._ensure_dxf` 는 `dwg_converter.ensure_dxf` 를 부르고(설치돼 있으면 변환) 사용설명서는 ODA 설치를 안내한다. 어느 쪽이 맞는지 정하고 다른 쪽을 고친다(코드를 지우든 CLAUDE.md 를 "선택 변환" 으로 고치든).
- `dxf_parser.py:4129` 체크리스트 문구 "치수선도 무시" 는 `from=dim`·`schedule=` 뒤로 거짓이 됐다.

---

## 6. 제안 순서

1. ~~**한 묶음(리빌드 1회)**: #1 Redo → #2 Shift 직교 → #3 팬 → #5 합계 → #4 각주·배지 → #6 나란히 보기.~~
   **2026-09-21 완료.** 경위·잠근 테스트는 [edits-and-preview.md](../decisions/edits-and-preview.md) '편집기 손맛 여섯'.
   브라우저 QA 가 단위 테스트를 통과한 결함 둘을 잡았다 — 저장 응답이 redo 를 먹어 '다시 실행'이 안 되던 것,
   SVG(치환 요소) 폭이 viewBox 종횡비로 정해져 나란히 보기가 오른쪽 절반을 덮던 것.
2. **GUI·문서 묶음**: #8 납품 ▾ 건수 → #9 샘플 열기 → #10 사용설명서 정정(+ DWG 범위 결정) → #7 배너·revision.
3. 나머지(#11~#15)는 필요가 보이면.

---

## 7. 근거 파일
- Planform 관찰 노트: 세션 스크래치 `planform_notes.md`(앱 UI 문구 전사 — 저장소에는 넣지 않았다, 외부 제품 문구라).
- 우리 코드: `frontend/src/app.js`, `frontend/src/review_logic.js`, `frontend/src/shell.html`, `frontend/src/style.css`, `vendor/edit_geometry.js`,
  `boq_export.py`, `mep_gui.py`, `project_store.py`, `project_server.py`, `verify.py`, `element_id.py`, `artifact_validation.py`, `drawing_units_ui.py`,
  `사용설명서.md`, `docs/decisions/*.md`, `docs/plans/2026-09-18-easy-loop.md`.
