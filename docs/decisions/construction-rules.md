# 시공기준 규칙 — 표 한 장, 선언이 없으면 건너뛴다

기계설비·건축 시공법(KCS·KDS·NFTC 등)을 3D 빌드에 반영하는 경위.

이 파일은 **왜 그렇게 했는지**의 기록이다. 상시 규약(z 기준면·`opts` 키·실행 순서)은 `CLAUDE.md` 에 있다.

### ★ 규칙은 표 한 장 · 근거 없는 수치는 표에 못 들어간다
LLM 42개 에이전트로 조사한 규칙 226건 중 원문·미러를 재독해 검증한 결과: confirmed 43 · plausible 2 ·
refuted 9 · unverified 117 · memory_only 11 · 비수치 44. **`verdict=confirmed` 만 `construction_rules.RULES`
에 들어가고, 그것만 적용한다.** 나머지는 `BACKLOG`(표에는 있으되 적용 코드가 없다)에 색인만 남긴다 —
다음 사람이 같은 조사를 다시 하지 않게, 그리고 확인 안 된 수치가 모델에 새지 않게.

미러(`source_kind: mirror` — 원문 kcsc.re.kr PDF 를 못 열어 nogada.kwoody01.com 전사에서 읽은 것)로
읽은 것도 사용자 확인대로 기본 적용한다. `docs/release_checklist.md` 에 "KCSC 원문 재확인" 항목을 둔다.

### ★ 평면도에는 DN·재질·용도가 없다 — 선언이 없으면 건너뛰고 그 사실을 센다
기준은 전부 호칭지름(DN)·재질·용도로 키가 잡히는데, 평면도가 주는 것은 **물리 치수**(외경·폭×높이)뿐이다.
DN100 강관의 실제 외경은 114.3mm(KS D 3507) — 외경으로 DN 을 역산하면 배관마다 다른 스케줄(두께)에서
값이 갈린다. 그래서 하지 않는다. 재질은 카테고리로 추정하지 않는다(`material=` 는 조적벽에서 바로 틀린다
는 기존 규약과 같은 이유). 계통 이름(SA/RA)도 용도가 아니다 — `service=` 를 별도로 선언받는다.

선언 표면은 둘이다: MEP 프로필 행의 `nominal_size`·`material`·`service`(이미 있던 둘에 `service` 추가),
`layer_map.csv` opts 의 `nominal=`·`service=`(신규, `material=` 은 기존 것을 그대로 쓴다 — `construction_rules`
는 `rec['material']`·`(rec['overrides'] or {})['material']` 둘 다 읽는다, 선언 경로가 프로필이냐 layer_map
이냐에 따라 값이 어느 쪽에 실리는지 다르기 때문이다). 없으면 `receipt.declarations_missing`·`skipped[규칙:사유]`
로 세고 조용히 넘어가지 않는다.

### ★ 위반은 V013, 정보·물량은 각자의 표 — `needs_review` 를 신호로 쓰지 않는다
clash-review 가 이미 겪은 교훈과 같다: 서로 다른 성격(위반·정보·물량)을 한 신호로 묶으면 "고칠 것 없음"과
"확인 필요"가 같은 말이 된다. `duct-aspect-ratio`·`hydrant-pipe-diameter-minimums` 는 위반이면 V013(warn).
`drain-slope-by-diameter` 는 위반이 아니라 **정보**다 — `elevation` 이 스칼라 하나라 도면에 그려진 구배
자체를 검증할 수 없고, 필요한 낙차(`route_length × slope`)만 말할 수 있다. 그래서 값에 `slope_basis:
"undeclared"` 를 자기보고하고 V013 을 울리지 않는다. `pipe-support-spacing-horizontal`·`duct-hanger-spacing`·
`drain-cleanout-spacing` 은 BOQ `MEP 지지·청소구` 표로 — 지지·청소구는 위반 여부가 아니라 물량이다.

지지·청소구 개수는 **하한 추정**이다(길이÷간격, 올림). 청소구는 KCS 31 30 25 3.3.1 을 그대로 따라
기점(1) + 간격마다(⌊L/간격⌋) + **45도를 넘는**(45도 자체는 미해당) 방향전환마다 하나씩 더한다 — 방향
전환각은 `route_points` 의 연속 두 구간 방향벡터 사이 각으로 잰다.

### ★ 덕트 지지는 원형에서 물리 지름을 그대로 쓴다 — 별도 DN 선언을 요구하지 않는다
KCS 31 20 20 표 3.2-21(스파이럴 원형덕트)의 "호칭"은 실제로 그 덕트의 지름 자체다(원형 덕트는 애초에
호칭=실치수인 도면 관행) — 사각 덕트·배관과 달리 여기서는 물리 치수가 곧 규칙이 찾는 값이다. 그래서
`nominal_size` 선언을 요구하지 않고 `geom_contract.mep_section` 이 이미 계약대로 요구하는 `diameter_mm`
를 그대로 읽는다. 중복 선언을 만들지 않는다 — "표에 없는 숫자는 코드에 없다"는 이미 선언된 값을 다시
선언하라는 뜻이 아니다. 재질은 여전히 선언해야 한다(표 3.2-20/21 은 **아연도금강판제 한정** — 스테인리스
는 표 3.2-28, PVC 는 표 3.2-36 로 값이 다르고 이번 표에는 옮기지 않았다).

### ★ 슬리브 규격은 그려진 슬리브를 외경+40 과 대조한다 — 슬리브 몸체는 만들지 않는다
KCS 31 20 15 2.2.20 은 "관의 바깥지름보다 40mm 정도 큰 규격"이라고만 한다 — 슬리브를 새로 뚫으라는
조항이 아니라 **도면에 이미 그려진** 슬리브가 그 기준을 채우는지 대조하는 조항이다. 그래서
`clash_review._sleeves()` 가 그려진 슬리브 폴리곤의 최소 변(원은 최소회전사각형으로 재서 지름 근사)을
재고, `sleeve_provided`(맞음)와 `sleeve_undersized`(모자람)를 가른다. 대조 기준(`required_sleeve_mm`)은
배관에만 적용한다 — 2.2.20 은 배관 슬리브 조항이라 덕트 관통은 이 조항 밖이다. 몸체를 만들지 않는 이유는
슬리브가 실제 시공 자재이고(`role: sleeve` 가 이미 그 자리를 표현한다), 규격 부족을 "고쳐서" 보여주면
도면이 실제로 뚫어 둔 규격과 모델이 달라진다 — 여기는 검토 목록이지 시공도가 아니다.

### ★ 기둥·내력벽 관통은 수치가 아니라 구조 확인 요청이다
방화구획·구조 안전상의 관통 허용 한계(RC 보·기둥의 구멍 크기·위치·간격)는 국내 출처를 확보하지 못했다
(`BACKLOG` 의 `rc-beam-hole-limits`). 그래서 `structure_penetration` 조치 문구는 여전히 "경로 변경
검토"일 뿐 수치 기준을 달지 않는다 — 근거 없는 한계를 코드에 박느니 사람이 구조 기술사에게 확인하게
둔다.

### ★ 수직관은 표 3.4-1 대로 '각 층 1개소' — 길이가 아니라 층 수로 센다
수평관과 달리 수직관(입상관)의 지지는 간격(m)이 아니라 **층당 개수**로 정해진다. 층 수는 두 경로로
센다 — ① `floors[]` 선언이 있으면 그 z 목록 중 수직관의 절대 z 범위 안에 드는 개수(최소 1), ② 없으면
MEP 프로필의 `levels.floor_to_floor_mm` 로 추정(⌈수직 연장 ÷ 층고⌉). **둘 다 없으면 `no_storey_height`
로 건너뛴다** — 층 정보 없이 수직관 지지 개수를 추정하지 않는다. 주철관은 직관/이형관 규칙이 다르지만
(KCS 원문에 있음) 재질별 분기를 만들지 않았다 — `_MATERIAL_ALIASES` 에 주철관 별칭이 없으므로 지금은
모든 재질에 '각 층 1개소'만 적용된다(주철관 특례는 미수록, 값을 지어내지 않는다).

### ★ 보온 두께는 선언 등급·온도·DN 의 표 조회다 — 계통 이름으로 추정하지 않는다
KCS 31 20 05 의 보온 두께표는 급수(결로 방지)·온수·냉수·냉온수마다 다르고, 온도대별로도 또 나뉜다.
연구에서 원문을 재확인한 조합(등급×온도대×다습 여부)만 `_PIPE_INSULATION` 표에 넣었다 — 예를 들어
온수 91~120°C 는 등급 나만 확인했으므로 가/다/라 를 선언하면 `grade_not_in_table` 로 건너뛴다.
**계산은 `mep_profile._assign` 이 파싱 시점에 한 번만 한다** — `geom_contract.mep_envelope`(Phase 4)와
`construction_rules.insulation()`(BOQ) 둘 다 그 결과(`rec['insulation_mm']`/`['insulation_table']`)를
그대로 읽는다. `geom_contract` 는 `construction_rules` 를 import 할 수 없어서다(반대 방향 의존은 이미
있다 — 순환 참조를 피하려면 계산 지점이 파싱 쪽이어야 한다). 덕트는 크기·노출 여부와 무관하게 등급
하나로 정해진다(표 2.4-1~4) — 제연덕트(`service: smoke_control`)만 전등급 25mm 로 따로 뗀다.

### ★ 보온 외피는 opt-in — 켜면 간섭 수가 바뀐다
슬리브 대조(Phase 2)는 배관 하나하나를 대조하니 결과가 늘 정확하지만, **간섭 목록 전체**의 판정 폭을
보온 두께만큼 넓히는 것은 다른 얘기다 — 기존에 안 걸리던 자리가 새로 걸릴 수 있고, 그 변화가 조용히
일어나면 "왜 어제는 없던 간섭이 오늘 생겼나"를 아무도 설명 못 한다. 그래서 `clash_review._bands` 는
프로필의 `rules.insulation_envelope: true` 를 **명시적으로 켰을 때만** `geom_contract.mep_envelope`
를 쓰고, 기본은 나관 치수(`mep_section`)다. 켜졌을 때는 행마다 `mep.envelope: "insulated"` 를 남기고
`summary.envelope_basis` 로 몇 건이 외피 때문에 넓어졌는지 센다 — 켰다는 사실과 그 영향을 목록 자체가
말한다.

### ★ 구배는 비율과 높은 끝을 둘 다 선언할 때만 — 유향은 평면도에 없다
평면도는 배관이 어느 쪽으로 흐르는지 그리지 않는다(화살표는 주석이지 기하가 아니다). 그래서
`slope: {ratio, high_end}` 를 **둘 다** 선언해야 `geom_contract.sloped_path3d` 가 불린다 — 하나만
선언하면(예: 비율만 알고 방향을 모르면) 적용하지 않는다. `ratio` 는 리터럴("1/100")이거나
`"rule:drain-slope-by-diameter"`(선언한 DN 으로 시공기준 표를 조회, `construction_rules.drain_slope_ratio`)
다. `high_end` 쪽 dz 는 0 — 그 점이 기존 `elevation`(placement 규칙이 이미 계산한 값)의 기준이 되도록
고정한다. path3d 를 얹는 것만으로 계약 v3 의 기존 소비자(`route_points`·`route_length`·`z_range`·
`rect_parts`·`clash_review._bands`·`freecad_builder.build_mep`·`mep_network`)가 다시 구현 없이
그대로 읽는다 — 수직 리저를 위해 이미 만들어 둔 길이었다. **직접 확인한 것**: `pascal_bridge` 는 이미
`path3d_dz_range` 로 평면 여부를 갈라 3D 샘플을 보내고 있어(수직관을 위해 먼저 있던 경로) 구배도 그대로
탄다 — `unconvertible` 로 떨어지지 않는다(실측: 0건). `frontend/src/mep_preview_geometry.js` 는 아직
`path3d`/`route_points` 를 안 읽는다(`js_constants` 에 없다) — 3D 미리보기는 경사·수직 구간을 평균
높이의 평평한 관으로 보여준다. 조용히 다르게 그리는 대신, 그 사실을 `mepRenderWarnings` 에 말로
남긴다(고치지 않는다 — 그 만큼의 작업은 이번 범위 밖이다). `boq_export` 의 MEP 길이 표는 원본(2D)
길이를 그대로 쓴다(1/100 구배가 늘리는 실제 길이는 0.005% 라 물량에 안 보인다) — 다만 어느 쪽을
썼는지는 새 열(`길이기준`)로 숨기지 않는다.

### ★ 프로젝트 기본값은 사람이 적은 선언이다 — 카테고리 추정이 아니다
매 부재마다 `material=`·`nominal=`·`service=` 를 opts 나 프로필 행에 적기는 번거롭다 — 실무 도면은
"이 프로젝트는 전부 콘크리트, 이 배관은 전부 PB"처럼 프로젝트 단위로 한 번만 정해지는 값이 많다.
그래서 `source.options.defaults`(스키마는 CLAUDE.md) 를 프로필 밖에 따로 둔다 — **프로필 안에 두면
`mep_setup_ui` 가 저장 때마다 폼에서 프로필을 재조립하며 지운다**(`mep_setup_ui.py` `_form_profile`).

읽는 순서는 고정이다: **`overrides`(수동 편집) > 레이어/프로필 선언 > 프로젝트 기본값**. 기본값은
선언이 없는 필드만 채우고, 채운 필드마다 `rec['declaration_basis']` 에 `{"material": "project_default"}`
식으로 이름을 남긴다 — 기본값도 **적힌 것**이지 형상 추정이 아니라는 것을 자기보고한다
(`freecad_builder.py` 의 `material=` 주석 "적힌 것만 붙는다" 는 기본값에도 그대로 적용된다).

`construction_rules.py` 의 영수증은 `declarations_missing`(선언 자체가 없다)과
`declarations_defaulted`(기본값이 채웠다)를 **따로** 센다 — 기본값이 채워도 "이 부재는 사람이 직접
선언하지 않았다"는 사실은 여전히 보여야 하기 때문이다(`missing` 의 기존 의미를 기본값이 지우면 실제
선언 공백이 안 보이게 된다).

`_material_of()` 는 `overrides` 를 최상위보다 먼저 읽도록 뒤집었다 — 기본값·프로필은 최상위
`rec['material']` 에만 쓰고 `overrides` 에는 안 쓰므로(수동 편집이 아니라서), 나중에 브라우저에서
재질을 편집하는 기능이 생겨도(아직 없다) `overrides` 가 항상 이기게 미리 정렬해 둔 것이다.

GUI 는 건축 도면을 열 때 층고·창호일람·재질 세 가지를 한 번 묻는다(`mep_gui._ask_arch_defaults`) —
층고는 `source['height']`(기존 `level_height` 경로, 새 프로젝트에서만) 로, 재질은 `defaults.architecture`
로 간다. 이미 연 프로젝트의 재질은 '그 밖의 도구 ▾ → 프로젝트 기본값' 에서 다시 바꾼다
(`mep_gui._do_project_defaults` → `ProjectSession.configure_defaults`, `configure_source` 와 같은
검증→시험 파싱→커밋 규칙). 층고는 생성 시점 전용이라 재열기 폼에는 없다. 설비 프로젝트는
`MepSetupDialog` 에 같은 규칙을 쓰는 '프로젝트 기본값' 소탭이 따로 있다(재질·DN·용도·보온등급, 카테고리
pipe/duct/tray) — 위 '설정 저장' 버튼(`mep_profile`)과 저장 키가 다르므로 저장 버튼도 따로다.

잠금 테스트: `tests/test_project_defaults.py`(전체 — 주입·읽는 순서·`configure_defaults` 왕복·거부),
`tests/test_construction_rules.py::test_receipt_counts_defaulted_declarations_separately_from_missing`·
`::test_material_of_reads_overrides_before_the_top_level_field`,
`tests/test_mep_freecad_contract.py::test_declaration_basis_reaches_the_ifc_pset`,
`tests/test_blender_export.py::test_declaration_basis_reaches_the_saved_model`,
`tests/test_mep_workflow.py::test_hidden_tk_defaults_tab_loads_and_forms_mep_declarations`,
`tests/test_window_marks.py::test_open_defaults_into_threads_height_and_fans_material_out_to_all_four_categories`·
`::test_merge_material_default_sets_all_four_architecture_categories_and_keeps_other_keys`.

### ★ 하지 않은 것 — 측정하고 접은 게 아니라 아직 근거가 없어서
- **건축 물량산출 관행**(벽 상단=보 하단, 기둥은 슬래브까지 등)은 조달청·표준품셈 1차 출처를 못 찾았다.
  형상을 바꾸는 자리라 근거 없이 손대지 않는다. 적용하려면 `geom_contract.py` 만 거치고 소비자 6곳
  (resolver·`stack_build._shift`·`pascal_bridge`·`boq`·V004·`height_basis`)을 확인해야 한다.
- **방화구획·방화댐퍼**: 2017 개정으로 "철판 1.5mm" 같은 규정 수치가 성능기준(비차열 1시간·KS F 2822)
  으로 대체됐다 — 옛 수치는 `BACKLOG` 에 `refuted` 로만 남긴다. `zone` 에 방화구획을 선언할 표면이 없어
  적용 자체가 설계부터 다시 필요하다.
- **덕트 엘보 곡률비·리듀서·티 치수**: confirmed 다(KDS 31 25 30 등). 그런데 [mep-geometry.md](mep-geometry.md)
  '이음 몸체' 가 이미 "제품 치수표를 쓰지 않는다"고 정했다 — 강관·PB관·덕트마다 표가 다르고 직관을
  자르지 않아 곡률 엘보 자체를 표현할 수 없기 때문이다. 재론하지 않는다.
- **간섭 허용치(조달청 10mm 류)**: unverified. 구조×설비 0mm 교차가 현재 동작이고 바꿀 근거가 없다.

경위 원본(조사 42 에이전트·규칙 226건·출처 재독 43건)은 이 계획 세션의 워크플로 결과에 있다 — 저장소에는
검증된 표(`construction_rules.py`)만 남긴다.

### 실측 — 실무 설비 프로젝트 두 건(골든)
`단위세대_난방`(PB 온수배관 5개, 전부 `material='PB'` 선언): `pipe-support-spacing-horizontal` 전부
`material_not_recognized` 로 건너뛴다 — **맞는 동작**이다. KCS 31 20 15 표 3.4-1 은 강관·동관·
스테인리스강관·경질염화비닐관·연관·주철관만 신고, PB(난방 코일)는 그 표에 없다. 추정하지 않는다.

`단위세대_환기`(덕트 37개): `duct-aspect-ratio` 27개 평가·위반 0(최대 종횡비 3.4, 1:4 안쪽) — 실제
도면이 규정을 지키고 있어 오탐이 없다. `duct-hanger-spacing` 은 37개 **전부** `material_not_galvanized`
로 건너뛴다 — 이 프로젝트의 MEP 프로필이 `material` 을 하나도 선언하지 않았기 때문이다(도면상 실제
재질과 무관하게, 선언이 없으면 우리는 모른다). 지지 개수를 보려면 프로필에 `material: "아연도금강판"`
을 선언해야 한다 — 이 규칙이 **선언 격차를 드러내는 데 성공했다**는 뜻으로 읽는다.

**프로젝트 기본값 적용 후(같은 도면, 복사본에 `configure_defaults({"mep": {"duct": {"material":
"아연도금강판"}}})` 실측):** `declarations_defaulted.material` 0 → 37, `skipped` 의
`duct-hanger-spacing:material_not_galvanized` 항목이 통째로 사라지고 `duct-hanger-spacing` 이 37건
**적용**(KCS 31 20 20 표 3.2-20/21 인용)으로 넘어간다. `defaults_effective.applied == {"material": 37}`.
`단위세대_난방`(PB 선언 프로젝트)에 같은 기본값을 얹으면 `defaults_effective.applied == {}` — 5개 배관이
이미 레코드에 `material='PB'` 를 갖고 있어 기본값이 낄 자리가 없다(읽는 순서 그대로: 선언이 기본값을 이긴다).
