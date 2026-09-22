# 수정 루프와 미리보기

사람이 고친 것을 잃지 않고 되돌리는 구조와, 화면이 판단 근거를 말해야 하는 이유.

이 파일은 **왜 그렇게 했는지**의 기록이다. 상시 규약(z 기준면·`opts` 키·실행 순서)은 `CLAUDE.md` 에 있다.

### ★ 수동 수정 라운드트립 — 주입 위치가 설계의 전부다
`parse(..., edits=<dict>)` 가 사용자 수정을 **개구부 링크 직전**에 주입한다.
그 앞에서 벽 후처리(중복제거·페어링·병합·single 정합·코너스냅·junction 치유)가 전부
끝나므로 **수동 레코드는 애초에 그 흐름에 들어가지 않는다** — 병합·스냅·오프셋되지
않는다. 후처리 함수마다 `source=="manual"` 검사를 넣을 필요가 없는 이유가 그것이다.
개구부 링크보다 앞이어야 하는 이유는 `wall_indices` 가 위치 인덱스라, 삭제·카테고리
이동으로 목록이 밀린 **뒤에** 링크해야 제 벽을 가리키기 때문이다(종전엔 파싱이 끝난
뒤 `main()` 에서 적용해, 벽 하나를 지우면 그 뒤 개구부가 엉뚱한 벽을 뚫었다).

EID 부여는 주입보다 **먼저** 한다(`apply_edits` 가 EID 로 찾으므로). 산정 입력인
`_sigs`·`_span_sigs` 는 레코드 생성 시점에 붙으므로 값은 달라지지 않는다.

| `edits.json` 키 | 뜻 |
|---|---|
| `overrides` | 치수·재질. `geom_contract.width_of` 1순위로 들어간다 |
| `category` | 카테고리 이동(리스트 간 이동) |
| `deleted` | 제거 |
| `added` + `record` | DXF 에 없는 사용자 생성 요소. **이동·분할·결합은 전부 delete+add 로 표현한다** — 새 동사를 만들지 않는다 |
| `review_resolved` + `_review_signature` | 확인한 형상·치수·진단의 지문이 일치할 때만 검토 완료를 유지한다. 서비스의 명시적 `acknowledge` 동작으로 생성하며, 지문 없는 이전 표시는 다시 검토한다. |

`apply_edits` 보고: `applied` · `migrated`(옛 `eid_v1` 에서 이어받음) · `added` ·
`orphaned` · **`ambiguous`**(같은 EID 를 여러 부재가 공유 → **적용하지 않는다**).

### ★ 저장 한 번 = 파싱 한 번
`ProjectSession.save()` 는 제안을 커밋하기 전에 **검증용으로 한 번 파싱**해 `_parse` 가
예외를 던지지 않는지 본다. 종전에는 그 결과를 버리고, 커밋 뒤 `state()` 가 새 revision 을
캐시에서 못 찾아 **또 파싱**했다 — 저장 한 번이 파싱 두 번이었다(실측: 벽 664 골든 도면에서
저장 7.6s). `replace_edits` 가 커밋 직전에 자기 revision 검사를 다시 하므로, 그게 통과하면
검증 때 쓴 `sources`·`edits_by_floor` 와 커밋된 것이 같다는 뜻이다 — 검증 파싱 결과를
그대로 새 상태의 캐시로 쓰고 `geometry.project.revision` 만 커밋된 값으로 고쳐 찍는다
(고치지 않으면 다음 저장의 `expected_revision` 근거가 한 박자 낡는다). 저장 7.6s → 4.9s.
고정: `tests/test_project_server.py::test_save_parses_once_and_the_returned_revision_is_immediately_usable`.

### ★ 편집 값의 `null` 은 "지운다" — 저장 경계는 union 병합만 안다
`overrides` 병합은 Python(`element_id.apply_edits`)·JS(`vendor/edit_geometry.js
materializeElements`) 양쪽 다 **합집합만** 한다 — 한 번 넣은 키를 지울 동사가 없었다.
인스펙터에 "도면값으로 되돌리기"가 필요해지면(예: 편집한 높이를 취소) 값이 아니라
**`null` 을 보내 병합 단계에서 그 키를 지운다**로 정했다 — 새 필드나 새 동사를 만들지
않는다. `project_store.validate_edits` 는 `width`/`height`/`diameter` 류(치수, 0 이하
금지)와 `elevation`/`z_base`(배관 중심·벽 하단 z, **음수 허용** — 지하층)를 갈라 검사하고
둘 다 `null` 은 통과시킨다. 검사 없이 통과하던 시절엔 `elevation:"abc"` 가 그대로
저장돼 다음 `state()` 재파싱마다 `float()` 에서 죽었다.
`added` 편집은 이제 `category` 를 **필수**로 요구한다 — 없으면 서버(`element_id.apply_edits`)
는 `opening`, 클라이언트(`materializeElements`)는 `wall` 로 **말없이** 갈렸다.
고정: `tests/test_project_store.py::test_elevation_accepts_zero_and_negative_but_not_a_string_or_infinity`
· `::test_added_edit_without_category_is_rejected`
· `::test_null_override_deletes_the_key_instead_of_storing_a_stale_none`
· `tests/preview_edit_geometry.test.js`(materializeElements 의 null 병합).

### ★ 미리보기는 "왜 이렇게 나왔는지" 를 말한다
`build_html` 페이로드에 `warnings`·`thin_pairs`·`width_conflicts`·`qa`·`edits_report`
가 실린다. 선택 패널의 `whyHtml()` 이 부재별로 검토 사유·실측 대 선언 두께·추정치
(`dims_assumed`)·일람표 미매칭을 띄우고, `REASON` 표가 코드 한 단어를 **조치까지 담은
한 문장**으로 바꾼다. 종전엔 형상만 보냈다 — 사용자가 고칠 대상을 보고 있는 유일한
화면에 판단 근거가 하나도 없었다.

그래서 `needs_review` 를 켜는 자리는 **반드시 `review_reason` 을 같이 남긴다**
(실측: 180개 중 154개가 사유 없음이었고 전부 `single_offset` 이었다).
테스트 `test_review_flags_always_carry_a_reason` 이 이걸 고정한다.

편집은 브라우저에 **자동 저장**된다(`localStorage`, 도면별 키) — 새로고침 한 번에
오후 작업이 날아가지 않게. `Ctrl+Z` 는 편집 스냅샷 스택을 되감고, **한 동작 = 한 단계**다
(무동작은 쌓지 않는다). 수정을 적용하면 `review_resolved` 가 함께 기록되어 이미 고친
부재가 `NeedsReview=True` 로 IFC 까지 나가지 않는다.

### ★ 2D 평면 탭 — 기하 편집은 여기서만
`preview.html` 의 ‘평면 편집’ 탭(SVG). 화면 개발은 `frontend`에서 `npm ci`, `npm run build`로 빌드한다. 배포본에는 빌드 결과를 동봉한다. 3D 는 **보기 전용**이다 — 원근
투사에서는 끝점을 정확히 집을 수 없고, 건축 편집은 본래 평면이 정확하다.
선택 패널(`fillPanel`)은 두 탭이 **공유**한다(뷰마다 인스펙터를 따로 두면 필드가
어긋나기 시작한다).

**이동·분할·결합은 새 동사를 만들지 않는다.** 전부 `delete + add` 다:

| 조작 | edits.json 표현 |
|---|---|
| 이동 | 원본 `deleted` + 옮긴 좌표로 `added` |
| 분할 | 원본 `deleted` + 조각 2개 `added` |
| 결합 | 원본 2개 `deleted` + 하나 `added` |

`apply_edits` 가 이미 그 둘을 안다. 네 번째 동사를 만들면 파서·빌더·미리보기가
전부 그걸 배워야 한다.

- 수동 레코드는 `pairing:"manual"`, EID 접두 `wm:`. 주입이 벽 후처리 뒤라서
  병합·스냅·치유를 **애초에 안 지난다**(위 라운드트립 절 참조).
- 그래서 끝점 스냅은 **브라우저에서 사람이 보면서** 건다(`snapPoint`, 화면 12px).
  파서가 나중에 몰래 옮기는 것보다 낫다.
- 결합 허용치 `JOIN_TOL_MM=600` 은 **실치수**다. 픽셀 기준으로 두면 같은 조작이
  줌에 따라 다르게 동작한다(멀리서 보면 6m 떨어진 벽도 붙었다).
- **일괄 수정은 `layer_map` 한 줄 제안**으로 낸다(`bulkHtml`, 같은 레이어·같은 실측 두께를 공유하는
  임의의 EID 묶음). 개별 EID 41개로 저장하면 도면이 바뀔 때 전부 고아가 된다. 이 경로는 여전히
  **복사만** 한다 — 사람이 CSV 를 붙여 넣는다(`map-layers` 스킬). 아래 절의 `suggestions_apply` 는
  **파서가 이미 조건과 값을 구조로 낸** 세 가지(얇은 오결합·기둥이 벽처럼·두께 불일치)만 원클릭으로
  간다 — 서로 다른 신뢰 수준이다: 구조화된 제안은 파서가 계산을 끝냈고, `bulkHtml` 은 사람이
  판단할 몫(레이어를 쪼갤지 등)이 남아 있다.

### ★ 인스펙터의 높이 칸 — 계약은 이미 있었다, 첫 배포는 MEP 만
`overrides.elevation`/`overrides.z_base` 는 `geom_contract.base_z`가 이미 읽고 있었다
(2D 평면 탭이 벽 끝점을 옮기는 것과 같은 층위 — **선언**이지 새 계약이 아니다). 그래서
추가한 것은 인스펙터 칸 하나(`e_zrow`)와 그 값을 `overrides` 로 보내는 배선뿐이다.
값은 `geom_contract.js_constants()` 가 내보내는 `ELEV_CATS`(`globalThis.MepContract`)로
카테고리를 가른다 — 여기서 다시 구현하지 않는다.

**첫 배포는 배관·덕트·트레이·장비(elevation)만 노출한다.** 벽·기둥의 `z_base` 편집은
미룬다 — `floor_z` 가 없는 레코드는 `base_z` 로 층을 고르므로(`geom_contract.floor_z`),
스택 프로젝트에서 벽 `z_base` 를 바꾸면 그 벽이 다른 층으로 튈 수 있다. 그 경로를 안전하게
만드는 건 별도 작업이다.

칸을 **비우면** `null` 을 보내 편집을 지운다("저장 경계"절의 null=삭제 규약을 여기서
처음 실제로 쓴다). 숫자가 아닌 값은 무시한다(빈 칸도 아니고 숫자도 아니면 손대지 않는다).

`vendor/edit_geometry.js` 의 `applyProperties` 는 어떤 카테고리가 elevation 을 쓰는지
**모른다** — 호출자(`app.js`)가 `values.zKey` 로 알려 준다. 그래서 이 함수는 여전히
브라우저 전역(`globalThis.MepContract`) 없이 Node 로 단위테스트할 수 있다.

`freecad_builder.py` 의 층 배정(`_story_owner`·`_at_floor`·이음 레코드)은 이전에
`rec['elevation']` 을 **원값**으로 읽어, 편집한 elevation 이 층 경계를 넘어도 형상만
움직이고 IFC storey 는 옛 층에 남았다. `GC.floor_z`/override-aware 읽기로 고쳤다.

고정: `tests/preview_edit_geometry.test.js`(applyProperties 의 zKey/z, null=삭제,
NaN=무시, added 레코드도 같은 규칙) · `tests/test_project_server.py::test_pipe_elevation_override_saves_reopens_and_moves_the_z_range`
· `tests/test_clash_review.py::test_an_edited_pipe_elevation_reports_declared_not_source`
· `tests/test_mep_freecad_contract.py::test_an_edited_elevation_that_crosses_a_floor_boundary_moves_the_ifc_storey_too`
(freecadcmd 없으면 skip).

### ★ 평면 탭에 배관·덕트·트레이 — 네 동사는 카테고리를 가리지 않았다
`movePolylinePoint`·`splitPolyline`·`joinPolylines`·`snapPoint`(`vendor/edit_geometry.js`)는
처음부터 카테고리를 안 봤다 — 벽만 편집됐던 건 화면이 `wallsAll()` 로 벽만 걸러서였다. 그래서
평면 탭을 설비로 넓히는 일은 **새 파이프라인이 아니라 배선**이다: `cat2` 상태(툴바 select) +
`editable2()`(`EFFECTIVE_ELEMENTS[cat2]`, 벽은 `opening_infill` 제외·설비는 `footprint`
제외) + `makeManualRecord(category, points, source, defaults)`(`makeManualWall` 은 이제
그 얇은 래퍼). **Python 쪽은 0줄** — `validate_edits`·`apply_edits`·`stack_build._shift`·
V011 의 `pairing=='manual'` 면제가 이미 pipe/duct/tray 를 받고 있었다(고정:
`tests/test_roundtrip.py::test_a_manual_pipe_reaches_route_points_network_and_verify_clean`).

- **수동 설비도 `wm:` 접두를 쓴다** — Pascal 의 기존 수동 레코드 규약과 맞춘다. 카테고리는
  EID 가 아니라 `added` 편집의 `category` 필드가 정한다(0-C 가 이미 필수로 만들었다).
- **치수 키는 카테고리마다 다르다**(`diameter` / `width_mm`+`height_mm`) — `mepPropertyKeys`
  가 인스펙터에서 쓰는 것과 같은 이름이라, 평면에서 그리고 인스펙터에서 고치는 값이 같은 곳에 쌓인다.
- **새로 그린 설비의 치수·높이는 이웃에서 물려받는다** — `manualRec` 이 `src`(끌어온 원본, 없으면
  같은 층·같은 카테고리에서 가장 가까운 레코드)의 `elevation`·`system`·`section_shape` 를 복사하고
  치수를 `gcMepDimensions` 로 채운다. 이웃이 전혀 없으면 그 층 소스의 높이(없으면 0)로 떨어진다 —
  아무 카테고리서나 z 를 물려받던 종전 `activeFloorSource()` 의 함정을 여기서 피한다.
- **스냅은 elevation 을 층 프로브에서 뺀다** — 넣으면 `sameFloor`(edit_geometry.js)가 level/floor
  없을 때 elevation 으로 폴백해, 높이가 다른 배관끼리 서로 못 붙는다.
- **결합은 이음 부기용 필드를 보지 않는다** — `joinPolylines` 의 `OMIT` 에 `joints`·`source_refs`·
  `source_length_mm`·`route_length_mm`·`dimension_status` 를 더했다. 프로필에서 나온 두 조각은
  이 값들만 다르므로, 안 더하면 절대 결합되지 않는다. `elevation`·`system`·치수는 여전히 비교한다.
- **이음은 이동에서 다시 안 잡는다** — `mep-connectivity.md` 의 ★ 절.
- **cat2 가 설비면 벽은 배경(`w-bg`)으로만** — 자리를 잡을 때 보여야 하지만 편집 대상은 아니다.
- **3D → 평면(인스펙터의 "평면에서 고치기" 버튼)**: `setTab(true)` 를 부를 뿐이다 — 탭을
  바꿀 때 3D 에서 고른 부재가 벽이 아니어도(pipe/duct/tray) `cat2` 를 그 카테고리로 맞추고
  층·선택을 넘기는 로직은 이미 있던 탭 전환 캐리오버를 일반화한 것이다(종전엔 `selCat==='wall'`
  만). 새 상태·저장은 없다. 버튼은 평면 탭이 다루는 카테고리(`EDIT_CATS`)일 때만 보인다 —
  기둥·슬래브·개구부 등은 넘길 곳이 없다. `fillPanel`/`setTab`은 DOM 의존이라(이 저장소가 전부터
  그래 왔듯) 단위테스트하지 않고, 브라우저로 직접 확인했다: 2D 탭에서 배관 선택 → 3D 탭 전환
  → 버튼 클릭 → 같은 배관이 선택된 채 2D 탭으로 복귀, 콘솔 에러 없음.

고정(전부): `tests/preview_edit_geometry.test.js`(makeManualRecord 의 카테고리별 치수·EID·
elevation/system 복사, OMIT 확장으로 이음 부기 필드가 달라도 결합, elevation 이 다르면 여전히
막힘) · `tests/test_roundtrip.py`(파서 이후 파이프라인 확인) ·
`tests/test_mep_workflow.py::test_saving_an_added_pipe_and_moving_a_jointed_one_updates_connectivity_live`
· 수동 QA: `sample_mep.dxf` 미리보기에서 배관 선택→높이 편집→적용, 분할, 재결합까지 브라우저로
실행해 콘솔 에러 없음과 수정 목록 라벨을 확인했다(`➕ 배관 Nmm · ØD`, 종전엔 카테고리와 무관하게
`창호 0×0` 이었다).

### ★ 검토 항목을 버튼으로 — 브라우저는 **명시적 클릭에만, 프로젝트 소유 CSV 에만** 쓴다
파서가 이미 대상 행과 값을 계산해 놓고 경고 문장으로만 버리던 세 가지(얇은 오결합·기둥이 벽처럼
그려짐·두께 불일치)를 `result["suggestions_apply"]`(구조화 리스트, `dxf_parser.py`)로도 낸다.
경고 **문장은 그대로 둔다** — 기존 프롬프트(`mep_gui._prompt_after_parse`)와 테스트가 그 위에 있다.

| `code` | `op` | 값 |
|---|---|---|
| `thin_pair` | `set_opts` | `opts.pair_min = round(median/3)` — 레이어 중앙값의 1/3 |
| `column_layer_looks_like_wall` | `insert_row` | `category:'wall', height:2800`(`suggested_row` 와 같은 값) |
| `width_conflict` | `set_width` | `width = detected`(실측값으로 선언을 바꾼다) |

모두 `row_layer`(제안이 고칠 레이어명 — thin_pair 는 블록 리다이렉트를 거친 값)·`pattern`
(`layer_rule_pattern(row_layer)`)·`evidence`(median_mm/spacing_mm/declared_mm 등, 사람이
검산할 수 있게)를 들고 있다.

**`ProjectSession.apply_layer_suggestion`**(`project_server.py`, POST `/layer-rule`)이 CSV 에
쓴다. 대상 행은 **정규식으로 찾는다**(`layer_map_io.find_matching_row` — `dxf_parser.classify`
와 같은 선매칭·대소문자 무시 판정) — 저장된 패턴 문자열이 `layer_rule_pattern` 표준형과 달라도
(사람이 손으로 쓴 정규식 등) 찾는다. 새 행(`insert_row`)은 헤더 바로 아래에 선다(선매칭 우선).
LayerMapError(오타난 opts)나 적용 뒤 파싱 실패는 **파일을 원래대로 되돌린다.**

- **소스가 프로젝트 로컬 `layer_map` 이 없으면 400** — "동봉 CSV 를 프로젝트에 복사하세요"만
  말하고 대신 써 주지 않는다. **저장소 기본 CSV 는 절대 안 건드린다**(MCP `apply_layer_rule` 이
  써 온 그 파일과 다른 파일이어야 한다).
- **설비 프로필(`architecture_layers`) 소스는 아직 지원하지 않는다** — 정직하게 거절한다("설비
  도면 설정에서 직접 고치세요"). 반쯤 되는 구현보다 낫다.
- **CSV 파일 변경은 `ProjectStore.refresh_inputs`(지문 비교)가 이미 revision 을 올린다** — 새
  커밋 동사를 안 만든다. `decisions=` 인자를 추가해 같은 커밋에 결정도 같이 싣는다. 변경이 없으면
  (멱등 재적용) 결정도 안 남는다 — 아무 일도 안 났는데 로그만 쌓지 않는다.
- **`insert_layer_rule_first`·`_read_csv_rows`·`_write_csv_rows`는 `layer_map_io.py`(신규,
  tkinter 없음)로 옮겼다** — `mep_gui.py` 는 거기서 다시 가져온다(기존 테스트가 `mep_gui.X` 로
  참조해도 그대로 동작). MCP `apply_layer_rule` 도 이제 헤더 아래에 넣는다(종전엔 끝에 붙여
  넓은 규칙에 가려질 수 있었다) — 단, MCP 는 여전히 **저장소 공용** CSV 를 쓴다; 프로젝트
  로컬 CSV 를 쓰는 것은 `/layer-rule` 뿐이다.
- **검토 화면의 `[적용]` 버튼**(`review_logic.js`·`app.js`)은 이 엔드포인트를 부른다 — `confirmGap`
  과 같은 모양(POST → `mergeServerPresentation` → 목록 다시 그림). 검토 행은 `suggestions_apply`
  에서 직접 만든다 — `thin_pair`·`column_layer_looks_like_wall` 처럼 이미 `element` 행이 있는
  것과도 **따로** 뜬다(부재별이 아니라 레이어별 제안이라서).
- **`preview.build_html` 은 넘길 geometry 키를 명시적으로 고른다** — `suggestions_apply` 를 그
  허용목록에 추가하는 걸 빠뜨렸더니 `/state` 응답과 서버 로그에는 있는데 **화면에는 안 떴다**
  (브라우저로 직접 켜서 겪은 버그 — 이 파일을 고치는 다음 사람은 새 geometry 키를 여기 목록에도
  넣는다). 고정: `tests/test_preview_editing.py::test_generated_preview_carries_project_state_and_valid_javascript`.

고정: `tests/test_thin_pair.py::test_a_structured_suggestion_carries_the_same_row_and_value_as_the_sentence`
· `tests/test_layer_evidence.py`(column_layer_looks_like_wall 의 구조 필드) ·
`tests/test_width_conflict.py::test_a_structured_suggestion_offers_the_measured_width` ·
`tests/test_layer_map_io.py`(5 — 정규식 매칭·병합·멱등) ·
`tests/test_project_server.py`(6 — 적용·멱등 재적용·낡은 revision·오타 롤백·CSV 없는 소스·설비
프로필 거절) · `tests/test_project_clients.py::test_mcp_apply_layer_rule_inserts_above_existing_rules_not_at_the_end`
· `tests/preview_review.test.mjs`(제안 행·apply 버튼·이스케이프·REASON 완비 3건) ·
수동 QA: 실제 `ProjectSession.serve()` 로 브라우저에서 thin_pair 제안 `[적용]` 클릭 → CSV 가
헤더 아래에 갱신되고 검토 항목이 0 으로 줄어드는 것을 확인했다(이 과정에서 위 `preview.build_html`
버그를 발견·수정).

### ★ 물량 요약 패널 — 저장이 곧 갱신이다, 다시 파싱을 부르지 않는다
Phase 4. 종전엔 물량(`boq_export.aggregate()`)이 CLI `main()` 과 GUI '물량 Excel' 버튼에만 있었다
— 벽 두께를 하나 고쳐도 물량이 바뀌었는지 보려면 Excel 을 다시 뽑아야 했다. `ProjectSession._parse`
가 매 파싱마다 `data['boq'] = boq_export.aggregate(data)` 를 얹는다(이웃 블록들과 같은
try/except — 실패해도 모델·수정은 막지 않고 `{'error': ...}` 만 남긴다). **새 계산 경로가 아니다**
— `aggregate()` 는 그대로고, `_parse` 가 그 출력을 `state()`/저장 응답에 얹을 뿐이다. 그래서
"저장 한 번 = 파싱 한 번"(위 절)이 이미 보장하는 대로, **저장 응답의 `geometry.boq` 를 받으면
패널이 그 자리에서 갱신된다** — 별도 재요청이 없다.

실측(골든 설비 프로젝트 복사본, `aggregate()` 가 MEP 이음·지지·청소구 섹션까지 내는 상태):
단위세대_난방 `aggregate()` 7.8ms + `json.dumps()` 0.1ms(1.4KB) · 단위세대_환기(지지·청소구 섹션
포함) `aggregate()` 1.8ms + `json.dumps()` 0.2ms(5.9KB) — 계획의 "< 0.1s" 조건을 여유 있게 만족.

**표시 정직성**: 벽·기둥 표의 개별 높이는 이미 계약대로 정직하다(`GC.height_of` — overrides >
레코드 > params, 섞이면 그 그룹의 높이 칸을 비운다, `boq_export.py`). 표에 없는 건 **그 값이
어디서 왔는지**뿐이라, 숫자를 다시 말하지 않고 출처만 패널 머리 한 줄로 말한다: `'level_height_overrode'
in data` 의 **키 존재**(값이 아니라)가 신호다 — 열 때 층고를 선언하면 이 키가 항상 남는다(개별
override 와 우연히 같아 실제로 덮은 게 0 건이어도 `0` 이 남지, 키 자체가 없어지지 않는다). `preview.py`
는 그래서 `level_height_declared`(불리언, `data.get('level_height_overrode')` 가 아니라 `in`
검사)로 낸다 — `.get(..., None)` 을 그대로 보냈다면 "선언했지만 0 건 덮음"과 "아예 미선언"이 둘 다
JSON `null` 이 되어 구분이 안 됐다.

**`stack_build.py` 도 같은 신호를 올린다.** 처음엔 "다층 프로젝트는 '열 때 세 질문' 흐름이 없으니
당장은 기본값으로만 보여도 과장이 아니다"라고 적었는데, 다중 에이전트 리뷰(워크플로)가 그 전제
자체가 틀렸다고 잡았다: 실무 흐름은 **단일 원본으로 열어 층고를 선언한 뒤(§7-D) '같은 층 도면
추가'(`mep_gui._do_add_source`, 이미 배포됨)로 두 번째 원본을 얹는 것**이다 — 그 순간
`ProjectStore.is_plain_source` 가 거짓이 되어 `_parse` 가 `build_stack` 경로로 영구히 넘어간다.
층고 선언은 그대로 살아 벽·기둥에 실제로 적용되는데(`stack_build.py:245-247` 가 그 값을 그대로
`level_height` 로 넘긴다), `build_stack()` 의 병합 `out` 딕셔너리는 그 사실(`level_height_overrode`
키)을 **어디에도 올리지 않았다** — 패널이 "선언했는데 왜 미선언이라고 하지" 하는 거짓을 말할 수
있었다(형상은 항상 맞았다 — 이 신호는 패널 문구 하나만의 문제였다). 고쳤다: 층 루프 안에서 `'level_height_overrode'
in data` 일 때만(값이 아니라 키 존재) `out['level_height_overrode']` 를 그 층의 덮은 개수만큼
누적한다 — 한 층도 선언 안 했으면 `out` 에 이 키가 아예 없다(단일 원본과 같은 계약).

**Excel 은 여전히 '납품 ▾'** — 패널은 요약(벽 두께별 높이·길이·체적, MEP 구분·규격별 길이+길이기준,
창호 규격별 개수)만 보여주고 서식 있는 물량표는 종전 그대로 Excel 로 뽑는다. 순수 변환
(`summarizeBoq`·`boqBodyHtml`·`boqHeightBasisText`)은 `review_logic.js` 에 있다 — 다른 순수
함수들과 같은 자리, DOM 은 `app.js` 의 `renderBoq()` 하나가 붙인다.

같은 리뷰가 문구 버그도 하나 더 잡았다: `boqHeightBasisText(true)` 가 "행마다 높이 열 참고"라고
안내하는데, 처음 버전은 벽 행에서 `r[3]`(높이(m))을 아예 빼고 길이·체적만 보여줘서 참고하라는
열이 화면에 없었다(Excel 에만 있었다). 벽 요약에 `heightM` 을 더해 `h2.8m · 45.2m · 25.3㎥`
식으로 낸다 — 높이가 섞인 그룹(`aggregate()` 가 `''` 로 비워 둔 그 값 그대로)은 `h?` 로, 숫자를
꾸며내지 않는다.

고정: `tests/test_boq.py::test_state_carries_boq_and_an_edit_updates_it_on_save`(json.dumps
성공 · 벽 두께 편집 뒤 `state()['geometry']['boq']` 갱신) · `tests/test_preview_editing.py`
의 `test_generated_preview_carries_project_state_and_valid_javascript`(boq·level_height_declared
가 build_html 허용목록에 있다) · `::test_undeclared_level_height_reaches_the_page_as_false_not_null`
· `tests/preview_review.test.mjs`(summarizeBoq·boqHeightBasisText·boqBodyHtml 4건, 높이 열 렌더링
포함) · `tests/test_stack.py`(다층 조립의 선언 신호 3건: 선언·미선언·두 원본 중 하나만 선언). 수동 QA:
`sample_mep.dxf`(층고 3000 선언)를 실제 `ProjectSession.serve()` 로 열어 덕트 폭을 400→500 으로
고쳐 저장 → 콘솔 에러 없이 패널이 "덕트 400x300"→"덕트 500x300" 으로 그 자리에서 바뀌는 것을 확인.
그 뒤 3-에이전트 병렬 리뷰 + 1개 검증 에이전트로 다시 검사해 위 두 결함(다층 신호 누락·문구가
없는 열을 가리킴)을 찾아 고쳤다 — 처음 QA 가 못 잡은 것은 둘 다 단일 원본·짧은 세션이라 안
드러나는 경로였다.

### ★ 편집기 손맛 여섯 — 외부 제품(Planform) 대조에서 가져온 것
2026-09-21, 참조 제품 [Planform](https://planform.keystonehub.io) 을 브라우저에서 훑고 우리 코드와 대조했다
(경위·채택/기각 전체 목록은 [plans/2026-09-21-planform-reference.md](../plans/2026-09-21-planform-reference.md)).
가져온 것은 **기능이 아니라 손맛과 정직한 문구** 여섯이다 — 그쪽엔 MEP·간섭·이중 벽선 병합이 아예 없다.

- **다시 실행(Ctrl+Y · Ctrl+Shift+Z)** — 되돌리기만 있고 되돌린 것을 되살릴 길이 없었다. 스택 로직은
  `review_logic.createHistory`(순수 함수)로 빼서 node 테스트가 잠근다. ★ **저장 응답은 기준선만 옮긴다
  (`rebase`) — 되돌릴 미래를 지우지 않는다.** 처음엔 `reset` 으로 redo 를 비웠는데, 되돌리기가 저장을 부르고
  그 저장이 redo 를 먹어 **'다시 실행'이 영원히 안 됐다**(브라우저에서 실제로 겪었다 — 단위 테스트는 통과했다).
  미래를 버리는 것은 새 동작(`push`)뿐이다.
- **Shift 직교** — `MepEdit.orthoPoint(from,to)`. 이동량이 큰 축만 남긴다. ★ **스냅보다 먼저** 건다 —
  직교로 민 뒤 그 자리 근처 끝점에 붙어야, 직교가 스냅을 이기고 어긋난 좌표가 남지 않는다. 끌기의 기준점은
  **끌지 않는 이웃 끝점**이다(그래야 그 구간이 수평·수직으로 선다).
- **화면 이동(Space·중버튼 드래그)** — 확대하면 편집할 자리로 갈 방법이 없었다. 원본 DXF 창의 `sourceDrag` 와
  같은 방식이다(viewBox 를 옮기고 매 프레임 새 좌표계에서 다시 잰다).
- **물량 합계 상시 표시** — `aggregate()` 가 이미 낸 **합계행을 그대로** 쓴다(화면이 다시 더하면 두 수가
  갈라진다). 없는 것은 0 으로 적지 않고 줄에서 뺀다 — 벽이 없는 설비 도면에 "벽 0m" 을 적으면 0 을 실측처럼 읽는다.
- **물량 각주 + '검토용 모델' 배지** — 벽 면적은 중심선 길이×높이라 **개구부를 빼지 않는데** 그 사실이 어디에도
  없었다. 같은 문장이 화면(`BOQ_SCOPE_NOTE`)과 Excel 2행에 같이 간다. 개구부 **차감 자체는 하지 않는다** —
  건축 물량산출 관행의 1차 출처가 없다([construction-rules.md](construction-rules.md) '하지 않은 것').
- **평면 + 3D 나란히** — 끝점을 끌면 옆에서 3D 가 바뀐다. 편집 경로는 그대로다(3D 는 계속 보기 전용) —
  `syncEffective→rebuild()` 가 이미 매 편집마다 3D 를 다시 만든다. ★ **`#view2d` 는 SVG(치환 요소)다** —
  `width:auto` 로 두면 left/right 가 아니라 **viewBox 종횡비**로 폭이 정해져 오른쪽 절반을 덮는다
  (실측: 높이 720 × 1.266 = 911.8px). 폭을 직접 준다. 이것도 브라우저에서만 드러났다.

고정: `tests/preview_review.test.mjs`(`createHistory` 2건 — 분기 버림·저장 응답이 redo 를 안 먹는다,
합계 1건) · `tests/preview_edit_geometry.test.js`(`orthoPoint` — 축 선택·동률·기준점 없음·원본 불변) ·
`tests/test_boq.py::test_excel_says_what_it_does_not_count`. 수동 QA: `sample_mep.dxf` 를 실제
`ProjectSession.serve()` 로 열어 Shift 직교 그리기(두 끝점 y 가 같은 값)·되돌리기→다시 실행·Space 팬·
나란히 보기 폭(491/490) 을 확인했고, 그 과정에서 위 두 ★(redo 소멸·SVG 폭)를 찾아 고쳤다.

### ★ 장면이 문제를 가리킨다 — 외부 제품(HighTopo)에서 가져온 것과 안 가져온 것
2026-09-22, 사용자가 [HighTopo](https://www.hightopo.com/en-index.html) 의 파이프라인 글(관 표면 UV 흐름 · 관 성장 · 관 로밍)이
시각화에 좋지 않겠느냐고 물었다. 대조 전체와 기각 목록은 [plans/2026-09-22-hightopo-reference.md](../plans/2026-09-22-hightopo-reference.md).

**흐름 애니메이션은 가져오지 않았다.** 평면도에는 유향이 없다(`construction_rules.py:10`, `geom_contract.py:936`) — 폴리라인 정점 순서는 그린
순서일 뿐이고, `service` 에서 방향을 끌어내면 계통→용도→방향 2단 추정이다. 흐르는 관은 없는 방향을 그럴듯하게 그린다. 그 글이 답하려던
질문("이 관이 어디까지 이어졌나")에는 **이음으로 이어진 무리**로 답한다. 사용자도 같은 결정을 했다(2026-09-22).

가져온 것은 기법이 아니라 습관 — 문제를 목록에서만이 아니라 **장면 안에서** 가리킨다:

- **간섭은 두 부재의 사건이다** — 간섭 행이 상대 구조부재(`data-struct`)를 싣고, 클릭은 배관과 벽을 **같이** 켠다(벽은 청록 빛).
  `selectEid` 가 true 를 돌려준 **뒤** 덧칠한다 — `select()` 가 먼저 전부 지운다. 두 부재를 합친 상자로 카메라를 잡지 않는다(슬래브와 합치면 건물 크기로 빠진다).
- **이음으로 이어진 무리** — `joinedEids` 는 `joints[].id` 공유만 따른다(`mep_network` 의 union-find 와 같은 정의, 확정한 이음도 joints 다). 후보는 건너지 않는다.
  ★ 무리는 emissive 가 아니라 **색을 바꾼다**(분홍). emissive 는 더하기라 하늘색 배관 위에서 보이지 않았고, 연두는 트레이 카테고리 색과 겹쳤다 — 둘 다 브라우저에서만 드러났다.
  강조는 `highlight3D` 한 곳이 칠하고 지운다(`rebuild` 도 이걸로 복원) — 원래 색은 `baseColorOf`(편집한 카테고리 먼저)로 돌아간다.
- **선택은 한 줄기** — 평면에서 고르면 3D 도(`markIn3D`), 나란히 보기에서 3D 를 고르면 평면도(`follow2D`, 평면이 화면에 있을 때만). `selectEid` 는 재사용하지 않는다 —
  단면을 끄고 원본 창을 옮겨 편집 중 화면이 튄다.
- **단축키는 글자를 치는 칸에서 양보한다**(`isTypingTarget`) — 평면에서 벽을 고른 채 인스펙터 폭 칸에서 Delete 를 누르면 벽이 지워지고 저장까지 갔다.
  체크박스·슬라이더·버튼·select 는 글자를 안 받으므로 단축키가 그대로 산다.
- **계통·용도는 적힌 그대로 한 줄** — 없으면 "계통 없음" 이라고 말한다. 색으로 가르지 않는다(골든 설비 프로젝트의 선언 계통이 1개·2개뿐이고, 인라인 색은 선택 표시를 이긴다).
- **간섭 지점은 평면 위 빨간 고리** — 끊긴 끝의 빨간 점과 모양으로 구분, 가정 높이는 옅은 점선. 이 파싱 결과의 판정이지 실시간 감시가 아니라고 범례가 말한다.
- **저장 뒤 한 줄** — "서버에 저장됨 · 간섭 4 → 3 (−1)". ★ 기준값은 **저장 사이클이 시작될 때**(사용자 동작) 뜬다 — 편집 저장은 응답 뒤 남은 수정을 한 번 더 보내므로
  (`onAck` 의 unchanged=false) 그때 기준을 비우면 두 번째 응답이 첫 문장을 덮는다. 간섭 계산이 실패한 응답(`total:0`+`error`)은 0 으로 읽지 않고 "비교 불가". 둘 다 0 이면 말하지 않는다.
- **검토 행으로 갈 때 보고 있던 각도 유지** — `frameBox` 에 지금 시선 방향의 복사본을 넘긴다(`frameBox` 가 인자를 normalize 로 바꾼다).
- **끊긴 끝도 검토 행** — 이어질 곳(후보)도 끝날 곳(장비·단말·슬리브)도 못 찾은 끝(`status:'open'`)만. 행이 점을 들고 가서(`data-at`) 클릭하면
  부재 전체가 아니라 그 점 둘레로 다가가고 빨간 점을 찍는다. 골든 설비 프로젝트에서 10개·17개라 목록을 묻지 않는다.
- **범례가 숨김 스위치** — 3D 에서만, 저장하지 않는다. ★ 검토 행이 숨긴 종류의 부재를 가리키면 **그 종류를 다시 보인다** — `selectEid` 는 보이는 메시만 찾으므로
  안 그러면 클릭이 조용히 죽는다. '맞춤' 은 보이는 것에 맞춘다. 반투명 X-ray 는 하지 않는다 — 클릭 광선이 반투명 벽을 계속 집는다.
- **3D 간섭 고리는 목록이 보여 주는 것만** — 층·종류 필터를 바꾸면 고리도 따라간다(목록과 장면이 같은 말). `meshes` 밖의 `THREE.Points` 라 클릭·맞춤·단면 범위에 안 섞인다.

같은 브라우저 QA 가 이 묶음과 무관한 결함 하나를 잡았다: 저장 응답의 geometry 에는 `level_height_declared` 가 없고(그건 `preview.py` 가 첫 화면에만 만든다)
`level_height_overrode` 만 있어, **저장할 때마다 물량 패널 머리가 '층고: 미선언' 으로 뒤집혔다.** 두 모양을 `levelHeightDeclared` 한 곳에서 읽는다(키 존재가 신호 — 위 물량 절과 같은 규약).

고정: `tests/preview_review.test.mjs` 의 `'joinedEids follows shared joint ids and never crosses a candidate-only gap'` ·
`'systemLineText prefers the override the editor saved and says so when nothing was declared'` ·
`'shortcut keys are ignored while typing in a text field but not on buttons, checkboxes or sliders'` ·
`'clash markers keep the point and the assumed basis, and survive a synthetic slab without a struct eid'` ·
`'change summary names what moved, says when nothing moved, and never turns a failed clash run into a drop'` ·
`'the storey-height basis survives a save response, which carries the parser key and not the preview key'` ·
`'open ends become review rows so a broken end is clickable, and only the unexplained ones'` ·
`'the 3D clash rings follow the review filters: only clashes the list is showing'` ·
`'the legend is a hide switch per category that says what is hidden and escapes names'` ·
`'review rows carry the hooks the click handler needs, and no save buttons without a server'`(`data-struct` 두 줄). 선택 한 줄기·시선 유지·색은 DOM/WebGL 이라
[release_checklist.md](../release_checklist.md) 8번 수동 점검. 수동 QA: `sample_mep.dxf` 에 벽 평행선 두 쌍을 더한 합성 도면을 `ProjectSession.serve()` 로 열어 위 항목을 전부 눌러 봤다(콘솔 에러 0).

### ★ 수정 루프의 남은 한계 — 알고 남겨 둔 것
전부 "지금도 납품은 되지만, 다음에 이 코드를 여는 사람이 알아야 하는" 것들이다.

**① 고아 edit 의 재연결은 **제안까지만** 한다.**
`grouping` 이 바뀌면 EID 가 바뀌고 그 수정은 고아가 된다. `element_id.suggest_relink()`
가 같은 층·같은 카테고리 안에서 거리·방향·겹침으로 순위를 매겨 상위 5개를 낸다
(`edits_report.relink_suggestions`, 앵커는 preview 가 남긴 `_at`). 호출처는
`dxf_parser`·`project_server`·`stack_build` 셋. 위치를 못 찾으면
`missing_source_location`, 모호하면 `no_unambiguous_candidate` 로 사유를 남긴다.
**자동 재연결은 하지 않는다** — 잘못 붙으면 조용히 틀린 모델이 된다. 붙이는 것은 사람 몫.

**② 체인 멤버의 EID 는 `SourceEIDs` 로 전부 나간다.**
`freecad_builder` 가 같은 직선의 벽 N개를 한 `Arch.makeWall` 로 묶을 때
`part_el["_artifact_source_eids"] = sub_eids` 로 멤버 전체를 싣고, `set_ifc_props` 가
`Pset_MEPParser.SourceEIDs`(JSON 배열)로 내보낸다. 실측(지하3층): 속성 584개 중
82개가 멤버 2개 이상이고 최대 17개다. 그래서 Bonsai 에서 벽을 클릭해 찾은 문제를
`edits.json` 으로 되돌릴 열쇠가 멤버마다 있다. `needs_review` 도 같은 이유로 멤버
전체를 OR 한다.

**③ 수동 벽 끝점은 브라우저 스냅이 전부다.**
위 2D 평면 탭 절 참조. 스냅을 놓치면 그 벽 끝에서 개구부 void 가 빗나갈 수 있다.
