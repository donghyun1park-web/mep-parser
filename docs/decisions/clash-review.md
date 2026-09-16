# 간섭 검토 목록 — 위치·부재·조치

간섭을 **어디를 보라**는 목록으로 만든 경위와, 높이 근거·합성 상부 구조·슬리브·속도의 실측.

이 파일은 **왜 그렇게 했는지**의 기록이다. 상시 규약(z 기준면·`opts` 키·실행 순서)은 `CLAUDE.md` 에 있다.

### ★ 간섭은 **위치·부재·조치** 목록으로 — FreeCAD 결과는 이름과 부피뿐이었다
종전 출력은 빌드 로그 `[CLASH] 간섭 14건` 과 build.json 의 `Wall_21 ↔ Duct_14 · 244803.1 mm³` 뿐이었다. 3D
미리보기·검토 목록에는 간섭이 없었고(검토 대기는 건축 분류 확인 97건이 채웠다), 이름에서 원본 부재를 찾으려면
영수증을 뒤져야 했다. 현장 담당자가 "어디를 보라는 것인가" 에 답이 없었다.

- `clash_review.find_clashes(geometry)`: 평면 교차 + 높이 범위(2.5D). 벽·기둥·슬래브·보는 수직 압출, 설비는
  `route_points` 구간마다 높이 범위를 가진 띠라서 수직·경사 구간도 구간별로 맞는다. 한 부재를 두 번 지나면 두 줄.
  폭·높이·경로는 빌더와 같은 함수(`width_of`·`z_range`·`mep_section`·`route_points`·`beam_rings`)만 쓴다.
- 항목: `id`(부재 EID·설비 EID·위치에서 나와 재파싱해도 같다) · `kind`/`action` · `at`(x, y) · `z` · `crossing_mm` ·
  `struct{eid, category, layer, width_mm}` · `mep{eid, category, system, size}` · `level`.
- 조치 구분은 **형상으로만** 가른다(레이어 이름을 추측하지 않는다): 슬래브 → 슬리브·방수 · 기둥/보 → 경로 변경 ·
  벽 두께 < 50mm → 면선 오결합 의심 · 벽 바닥 위 300mm 안에서 끝나는 설비 → 벽 하부 통과(문 하부 경로·벽 선시공
  확인) · 그 밖의 벽 → 관통(슬리브·개구). 문·창 개구부 안을 지나는 것은 빼고 `through_openings` 로 센다.
- ★ **슬리브는 자재로 서되, 판정은 `role` 로 가른다.** 프로필 `equipment` + `outline` + `role: sleeve` 는 다른
  장비와 똑같이 부재로 만들어져 IFC(`IfcDistributionElement`)·물량에 들어간다 — 강관 슬리브는 실제 시공
  자재다. 다만 그 자리를 지나는 관통은 조치가 다르므로(새로 뚫을 것인가, 이미 뚫어 둔 자리인가)
  `clash_review._sleeves` 가 슬리브 평면(여유 `SLEEVE_MARGIN_MM` 20mm)을 모아 `wall_penetration` 을
  **`sleeve_provided`** 로 바꾼다. 실측(단위세대 환기): 도면의 슬리브는 외기 Ø125 자리 2개(125×200 사각형)뿐이고
  벽 관통 6건은 거기서 3.5m 이상 떨어져 있다 — 이 도면에서 `sleeve_provided` 는 **0건이 맞다**(슬리브가 없는
  자리를 '있음' 으로 만들지 않는다).
- ★ **상부 구조는 간섭 계산에만 합성한다**(`_level_slabs`). 평면도에 슬래브·보 몸체가 없어 천장 쪽 간섭은
  **판정 대상 밖**이었다 — '덕트 상단 = 슬래브 밑면' 은 선언일 뿐 아무도 검사하지 않았다. 프로필 `levels` 에
  층간 높이와 슬래브 두께가 **둘 다** 있으면 바닥(상단 = `structural_slab_top_mm`, 아래로 두께)·천장(그 +
  `floor_to_floor_mm`) 두 장을 영역 평면으로 세운다. 선언이 없으면 아무것도 만들지 않는다(추정하지 않는다).
  **모델·IFC·물량에는 넣지 않는다** — 도면에 없는 부재다. 그래서 항목은 `struct.eid` 가 없고
  `struct.synthetic`("바닥"/"천장")·`layer: "(층 높이 선언)"` 를 들며 조치 문구가 다르다(검토 목록·Pascal 탭은
  EID 없는 행을 그대로 처리한다). 보는 합성하지 않는다 — 평면에 정보가 없다.
  ★ 다원본(스택)은 `mep_profile` 이 병합에서 버려지므로 `stack_build` 가 층 항목에 `levels`·`region` 을 남긴다 —
  안 그러면 통합 모델에만 천장이 없어진다(정작 간섭을 보는 모델이 그것이다).
  실측(단위세대): 바닥 −200~0 · 천장 2,600~2,800 두 장이 서고 **항목은 0건** — 환기 덕트가 z 2,475~2,600 으로
  상단이 천장 밑면에 **접촉**하고(접촉은 간섭이 아니다) 난방 코일은 z 70~86 으로 바닥 슬래브 위다. 즉 설치 높이
  선언이 이제 **검사된다** — 밑면을 넘기면 그때 뜬다.
- ★ **높이 근거를 판정과 같이 싣는다**(`geom_contract.height_basis` — 이 표를 다시 구현하지 말 것). 평면도에는
  높이가 없어서 간섭·연결 판정은 대부분 선언값이나 레이어 기본값에 기댄다. 그 사실이 결과에 안 실리면
  **가정으로 나온 줄과 도면이 말해 준 줄이 똑같아 보인다**(덕트가 높이 0 으로 깔렸을 때 간섭 2건이 나왔다가
  제 높이로 올리자 0 건이 된 적이 있다 — 형상은 양쪽 다 멀쩡했다). 기준 z 는 `overrides`·`elevation_source`
  (declared/profile) → `declared` · 도면 z → `source` · 없음 → `assumed`, 치수는 `dims_assumed`·
  `dimension_basis` 가 가정이라 했거나 `params`/기본값으로 떨어지면 가정이다. 하나라도 가정이면 그 줄은 가정이다.
  항목에 `basis`·`assumed`(가정한 키)·`struct.z_basis`·`mep.z_basis`, 요약에 `assumed_basis` 와
  `through_openings_assumed`(문턱·높이가 가정인 개구부로 뺀 수). 연결 후보·끊긴 끝도 같은 값을 든다.
  **고치지 않고 말하기만 한다** — 높이를 추정해 채우는 것이 애초에 이 문제를 만들었다.
  실측(통합 모델 24건, 규격 실측 이전 파일): **24건 전부 `assumed`** — 난방 배관 22건이 지름 가정(PB Ø15.9),
  덕트 2건이 단면 가정이고 z 쌍은 전부 (벽=도면 z, 설비=가정)이다. 규격을 외곽선으로 재 선언하면 그만큼 준다.
- ★ **평면도의 z 0 은 근거가 아니다**(`geom_contract.is_plan_zero` → `height_basis` 의 `plan_z`). 평면도는
  설비를 전부 z 0 에 그리므로 0 은 "도면이 높이를 말해 줬다" 가 아니라 **정보가 없다**는 뜻이다. 종전 규약은
  그걸 `source` 로 세서, 바닥에 깔린 덕트가 **선언한 줄과 똑같아 보였다** — 이 표가 막으려던 바로 그 상황이다.
  기준 z 가 0 이면 `assumed` 에 `plan_z` 가 들어간다.
  그 반대쪽 함정도 같다: **z 가 0 이 아니라고 설치 높이인 것은 아니다.** 실측(단위세대 환기) 슬리브 2개의
  원본 z 는 **12,357mm · 24,715mm** 였다 — 한 층짜리 세대에서 12m·24m 는 높이가 아니라 도면 작성 흔적이다.
  둘 다 프로필 진단으로만 말한다(`mep_profile`, 고치지 않는다) — `placement: source` 규칙의 원본이 전부 z 0
  이면 **`PLAN_Z_AS_ELEVATION`**, 선언한 층 높이(`levels.floor_to_floor_mm`) 밖이면 **`SOURCE_Z_OUTSIDE_STOREY`**
  (층 높이 선언이 없으면 잴 기준이 없어 아무 말도 하지 않는다). 설정 화면의 '설치 기준' 이 `source` 면 폼이
  그 자리에서 `slab_soffit`·`center` 를 권한다. 실측: 환기 프로젝트에 둘 다 1건씩 — 디퓨저 11개가 z 0(바닥),
  슬리브 2개가 층 밖이다.
- ★ **목록 전체가 가정 위에 서 있다는 사실은 줄마다의 표시로 안 보인다** — 검토 목록 맨 위 한 줄로 말한다
  (`review_logic.reviewBannerText`, 미리보기 `#reviewBanner` · Pascal 검토 탭 · `pascal_review.summary`).
  가정이 하나도 없으면 아무 말도 하지 않는다(빈 문자열). 연결 요약도 같은 수를 든다
  (`mep_connectivity.summary.assumed_basis` = `{candidates, open_ends}`).
- ★ **간섭은 그 벽 위에서 센 것이다 — 벽을 어떻게 잡았는지 같이 싣는다.** 줄의 `struct` 에 `pairing`·
  `review_reason` 이 붙고, 파서가 **위치나 두께를 확신하지 못하는** 벽이면 `uncertain: true` 와 요약
  `on_uncertain_walls` 가 선다(`UNCERTAIN_PAIRINGS` = `single_offset`·`manual`, 그리고
  `GC.UNTRUSTED_WIDTH_REASONS`). `single_offset` 은 중심선이 선언 폭 오프셋이라 위치도 두께도 추정이다.
  ★ `needs_review` **전체를 쓰지 않는다** — 실무 프로젝트는 건축 레이어 분류 확인으로 벽이 통째로 검토
  대상이라(실측: 통합 모델 24건 전부 `project_architecture_classification`) 그걸로 세면 매번 24/24 가 되어
  아무 말도 안 하는 것과 같다. 좁힌 뒤 실측은 **0건**이다 — 24건 전부 `paired` 벽 위다. 그 도면의 10mm
  칸막이는 `thin_pair` 로 안 걸리고(그리디 페어링이 보드선을 짝지었다) 간섭 목록의 `suspect_thin_wall` 이
  잡는다 — 두 신호가 서로를 덮는다.
- ★ **일상 검토는 2.5D 목록에서 끝난다 — FreeCAD 불리언은 납품 검증용이다.** 같은 자리를 2.5D 는 0.1초에,
  FreeCAD 통합 빌드는 421초에 찾는다(위 실측). 그런데 GUI 흐름은 `(3) 미리보기 → (4) 3D Build` 라 현장이
  매번 빌드를 눌렀고, 현장에 보낸 표는 **그때그때 짠 스크립트**로 만들었다. 이제 정식 출력이다:
  `clash_review.to_rows`·`write_csv`(열 20개) · `mep_network.to_rows`(확정한 이음·후보·계통 충돌·끊긴 끝을
  `row_type` 으로 한 표에) · GUI **'(3b) 검토 목록 CSV'** → `<프로젝트>/review/clash_r<rev>.csv` ·
  `connectivity_r<rev>.csv`(**파일 이름에 revision** — 어느 상태의 목록인지 알아야 그 표를 믿는다) ·
  CLI `python clash_review.py geometry.json [-o x.csv] [--connectivity]`. 인코딩은 물량 CSV 와 같은
  `utf-8-sig` 라 Excel 이 바로 연다. 빌드 버튼은 **'(4) 납품 검증 빌드 (FreeCAD · 느림)'** 이고, 누르면
  지난 `build.json` 의 `stage_seconds` 합과 가장 긴 단계를 먼저 찍는다 — 7분짜리를 모르고 누르지 않게.
  실측(통합 모델): 간섭 CSV **24행**(손으로 짠 표와 같은 건수, 열은 13 → 20) · 연결 CSV 110행(끊긴 끝 84 ·
  후보 26). FreeCAD 빌드 속도 자체는 손대지 않았다 — 남은 큰 구간(IFC 122.8초 · 설비 형상 189초)이 FreeCAD
  내부라 여기서 줄일 수단이 없다.
- 붙는 곳: `ProjectSession._parse` → `geometry.clash_review`(실패하면 `summary.error` — 모델·수정은 막지 않는다) ·
  미리보기 '검토 대기' 맨 앞(분류 '간섭', 누르면 설비 부재 선택) · Pascal 검토 탭(`pascal_review` 의 clash) · MCP
  `get_review_items.clash_review` · GUI 파싱 로그 요약. FreeCAD `check_clashes` 항목에도 `struct_eids`·`mep_eids`·
  `center_mm` 이 붙고 로그에 30건까지 찍힌다.
- ★ FreeCAD `check_clashes` 가 느렸던 이유는 불리언이 아니라 **1차 거르기와 형상 접근**이었다. 둘 다 결과를
  바꾸지 않고 없앴다(실측 통합 빌드: 간섭 단계 **522.4초 → 88.4초**, 검사 쌍 170 · 간섭 14건 동일):
  - `BoundBox.intersected()` 는 교차 **상자**를 돌려주고 늘 참이라 한 쌍도 거르지 못했다 — 불리언은 `intersect()`.
    저장본 재열기로 잰 같은 모델: 2,940쌍 전부 `common()` 161.6초 → 168쌍 68.7초.
  - 형상·경계상자를 **객체당 한 번만** 읽는다. 종전엔 안쪽 루프에서 설비 것을 다시 읽어 Arch 객체의 `Shape`
    접근이 70 × 42 = 2,940번 일어났다(개구부를 뺀 파라메트릭 벽은 접근이 비싸다).
  - `build.json` 의 `clash_pairs_checked`(불리언까지 간 쌍)와 `stage_seconds`(단계별 초)가 이걸 자기보고한다 —
    화면 로그는 빌드가 끝나면 없어서, 이 내역을 알아내려 저장본을 다시 열어 재야 했다.
  - 남은 88.4초는 거의 난방 코일이다. 2.5D 목록을 1차 거르기로 쓰면 더 줄지만 놓침 위험과 맞바꾼다(미적용).
  - 같은 빌드의 단계 합 946.1 → 421.2초, 이제 가장 큰 구간은 **IFC 내보내기**(122.8초)다.
- 실측(실무 단위세대 난방·환기 통합 모델): **24건을 0.1초에** — RA Ø100 벽 관통 6 · 난방 코일 벽 하부 통과 4 ·
  두께 10mm '벽' 오결합 의심 14(난방 12 · SA 2). 같은 모델의 FreeCAD 불리언 간섭 14쌍과 위치가 전부 대응한다
  (FreeCAD 는 체인으로 합친 벽 단위라 쌍이 적다). 그 FreeCAD 통합 빌드는 1,138.6초였다.
