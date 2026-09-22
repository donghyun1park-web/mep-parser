# 납품 IFC 는 누가 내는가 — FreeCAD 에서 ifcopenshell 직접 생성으로

`CLAUDE.md` 에는 규약만 있다. 여기에는 **왜 뒤집었는지**가 있다. `ifc_builder.py` 나
`freecad_builder.py` 를 고치기 전에 이 파일을 읽는다.

## 뒤집은 것

2026-09-18 에 `ifc_builder.py` 를 **동결**하고 "납품은 FreeCAD 경로 하나" 라고 적었다
([roadmap-history.md](roadmap-history.md), `docs/plans/2026-09-18-easy-loop.md` §14 "ifc_builder
개구부/MEP 추가 안 함"). 2026-09-21 에 그 반대로 바꿨다 — **납품 IFC 는 `ifc_builder.py` 가
내고, `freecad_builder.py` 가 동결**이다. 그때 적은 동결 사유는 "개구부 불리언과 MEP 솔리드는
FreeCAD 커널이 필요하다" 였는데, 아래 실측이 그 전제를 깼다.

## 전제를 깬 실측 셋 (2026-09-21)

**① 개구부는 절삭이 아니라 선언이다.** 벽 3,000×200×2,800 에 900×200×2,100 개구부 하나를
`IfcOpeningElement` + `IfcRelVoidsElement` 로만 선언하고 **재검사가 쓰는 바로 그 함수**
(`ifcopenshell.geom.create_shape`, `USE_WORLD_COORDS=True`)로 다시 읽었다 — 부피 1.302 m³,
다양체, 65 ms. 정확히 빠졌다. FreeCAD 경로도 IFC 에는 절삭 솔리드가 아니라 같은 void 선언을
싣는다(FreeCAD 1.1 `exportIFC.py:788-802`). 즉 **불리언은 IFC 에 나가지도 않았다.**

**② 곡면은 우리 검증기와 애초에 안 맞는다.** 같은 날 V107(비다양체 메시) 회귀를 추적하니
`Arch.makePipe` 였다 — 평면 직선 하나뿐인 Ø20 배관 하나가 재검사에서 나쁜 변 4,978개였다
([mep-geometry.md](mep-geometry.md) '원형 단면'). 곡면 BRep 을 지우고 정다각형 마이터 관으로
바꾸자 IFC 내보내기가 28~52초에서 0.6초가 됐다. **모든 형상은 이미 `geom_contract` 가
계산한다** — FreeCAD 는 그걸 스키마에 옮겨 쓰는 라이터였을 뿐이다.

**③ 일상 루프에 FreeCAD 가 없다.** 파싱→간섭→물량→수정(`project_server._parse`)에
`import FreeCAD` 0건. `.FCStd` 를 읽는 코드는 빌더 자신뿐이다. 고정비는 설치 2,130 MB ·
31,494 파일 · `timeout=900` · 네이티브 테스트 14건이 여유 RAM 2 GiB 미만이면 skip.

## 측정 — 같은 도면, 두 빌더

합성 도면(모든 카테고리 하나씩: 벽 3·기둥 2·슬래브 1·보 1·zone 1·개구부 2·배관 2·덕트 1·
트레이 1·장비 1·이음 1)을 `ifc_builder` 로 빌드하면 **0.5초, 재검사 오류 0건**이다
(`tests/test_ifc_delivery.py`). 2층 스택은 0.8초. 실무 도면은 아래 표.

**실무 도면 5건**(`tests/golden.local.json` 의 골든 — 도면은 커밋하지 않는다). 전부 `verified`,
`skip` 0건. 이 PC(Windows, Python 3.14). 같은 5건을 한 번 더 돌렸을 때 기계 부하로 최대 1.4배까지
느려졌다(난방 46.7초) — 아래는 한가할 때의 수치다:

| 골든 키 | 입력 | 소요 | `.ifc` |
|---|---|---|---|
| `단위세대_건축평면` | 벽 91 · 개구부 16 | 0.8초 | 0.3 MB |
| `단위세대_환기` | 벽 85 · 덕트 37 · 장비 13 · 개구부 13 | 2.1초 | 0.4 MB |
| `struct_beam_schedule` | 보 225 | 2.7초 | 0.6 MB |
| `지하3층_건축평면` | 벽 664 · 기둥 80 · 슬래브 8 · 개구부 22 | 9.8초 | 2.1 MB |
| `단위세대_난방` | 벽 85 · 배관 5 · 개구부 13 | 33.8초 | 5.8 MB |

위 표는 CLI(`connect` 없음)다. GUI 기본값인 벽 접합(`connect=True`)을 켜면 `단위세대_난방` 이
33.8초 → **47.8초**가 된다(같은 PC, `ProjectSession.export_geometry` → `build` → `verified_artifacts`
전 구간, `verified`). 이것도 60초 안이다.

난방이 가장 느린 이유는 부재 수가 아니라 **구간 수**다 — 난방 코일 5본이 각각 135~644 구간이고
원형 단면은 구간마다 24면 링이라 관 하나가 삼각형 3만 개가 된다. 검사 시간의 대부분은 빌드가
아니라 재검사(`create_shape` + 다양체 대조)다. 그래도 되돌리는 측정의 기준선 60초 안이다.

같은 세션의 검증자 측정(벽 638·기둥 72·슬래브 8, 구스키마·개구부 제거): `ifc_builder` 14.0초
verified 대 `freecad_builder` 66.9초 **BUILD_FAILED**(V107, 체인 벽 exporter 재생성 부피 −21%).
1회 측정이므로 실무 수치로 쓰지 않는다.

**아직 안 잰 것(정직):** 같은 골든을 `freecad_builder` 로 빌드한 나란한 수치 — 이 PC 의 여유 RAM
1.34 GB 가 저장소 자신의 FreeCAD 픽스처 가드(2 GiB)보다 낮아 돌리지 않았다. Bonsai 육안 대조도
아직이다. 둘 다 아래 '다시 뒤집는다면' 의 항목이고 `docs/release_checklist.md` 5번이다.

## 지금의 규약

- **납품 IFC = `ifc_builder.build`** (`mep_gui` '납품 ▾ → 납품 검증 빌드 (IFC)'). FreeCAD 설치
  불필요. 형상은 전부 `geom_contract` 에서 온다 — 곡면 없음.
- **`freecad_builder.py` 는 동결.** 지우지 않았다. '그 밖의 도구 → FreeCAD 빌드(동결 · 느림)'
  에 있고 `.FCStd` 가 필요하거나 두 경로를 대조할 때 쓴다. **새 기능은 여기 안 얹는다.**
- **개구부는 선언**(`IfcOpeningElement`)이고, 빼는 부피는 우리가 2.5D 로 따로 계산해 영수증
  (`opening_results[].cuts[].removed_volume_mm3`)과 `expected_products[].volume_mm3` 에 적는다.
  3D 불리언을 쓰지 않는다 — 수직 프리즘은 z 스윕 × 평면 합집합이면 **정확**하다.
- **`wall_indices` 는 그 층 안에서의 위치다.** 층을 이어붙이는 쪽(`stack_build`,
  `ifc_builder.build_multi`)이 이어붙인 만큼 민다. 안 밀면 위층 문이 아래층 벽을 뚫는데
  **형상은 멀쩡하고 검사도 통과한다** — 테스트가 유일한 신호다.
- **이음 id 도 층마다 따로다**(이음 점 좌표에서 나오므로 같은 DXF 면 같다). 안 붙이면 두 층의
  구성원이 한 이음으로 묶여 `members_on_different_storeys` 로 전부 조용히 빠진다.
- **벽타입은 반대로 모델 하나에 하나다.** 층마다 캐시를 새로 주면 같은 `WALL-200` 이 층 수만큼
  생겨 뷰어 타입 목록이 반복된다(실측: 3층 → 6개). `ctx` 에 두어 공유한다.
- **재질은 벽타입 캐시의 키다.** 접합(`connect=True`, GUI 기본값) 벽은 `create_2pt_wall` 이 만드는
  `IfcMaterialLayerSetUsage` 를 달고 있다. 거기에 평범한 `IfcMaterial` 을 덮어쓰면 **벽 Body 가
  아예 안 생겨** 빌드가 `Representation is NULL` 로 죽는다. 그래서 재질은 레이어셋에 쓰고,
  벽타입 캐시 키는 두께가 아니라 **(두께, 재질)** 이다 — 두께만으로 캐시하면 같은 200mm 의
  조적 벽이 앞선 콘크리트 벽의 레이어셋을 물려받고 **형상은 멀쩡해서 아무 검사에도 안 걸린다**.
  선언이 없으면 `IfcMaterialLayer.Material` 을 **비워 둔다**(종전 `_wall_type` 의 "Concrete"
  하드코딩은 조적벽을 콘크리트로 납품했다).
- **MCP `build_freecad` 는 그대로 둔다**(이름이 곧 약속이라 바꾸면 호출자가 깨진다) — 설명문에
  동결이라고 적고 `ifc_builder` 를 가리킨다. MCP 로 납품하는 사람은 없고 대형 빌드는 어차피
  타임아웃이라, 새 MCP 도구는 만들지 않았다.
- `verified_artifacts` 는 **영수증이 선언한 산출물만** 센다. 종전처럼 `fcstd` 를 무조건 세면
  성공한 IFC 빌드가 '검증 안 됨' 으로 보인다.

## 적대적 리뷰가 찾은 것 (2026-09-21)

납품 경로로 바꾼 직후 별도 리뷰어에게 "검사를 통과하는데 틀린 모델" 만 찾게 했다. 7건이 나왔고
전부 재현한 뒤 고쳤다. **공통점은 하나다** — `inspect_ifc` 는 빌더가 약속한 부피·경계와 IFC 가
같은지를 보지, 그 약속이 맞는지는 모른다. 빌더가 틀린 치수로 부피를 셈하면 검사도 같이 틀린다.

| 결함 | 증상 | 고친 자리 |
|---|---|---|
| 층 조립이 실측 두께를 버림 | 250mm 벽이 200mm 로 섬. 실무 도면(`지하3층`)을 두 층으로 쌓으면 빈 메시로 **죽음** | `build_multi` 가 두께를 얼리기 전에 `width_of` 로 읽는다 |
| 층 조립이 `params` 를 버림 | 4m 벽의 일반 개구부가 2.9m 에서 멈춤 | 첫 층 `params` 를 싣는다(`stack_build` 와 같은 규약) |
| 개구부 수정값 무시 | 미리보기에서 2.4m 로 고친 창이 IFC 에서 1.2m 로 뚫림 | `_opening_box` 가 `overrides` 먼저. 미리보기 창대도 같은 순서로 |
| 외곽선 트레이가 덕트 단면 | 100mm 로 선언한 트레이가 300mm 로 섬 | `_footprint_product` 가 자기 카테고리를 받는다 |
| 벽을 통째로 비우는 개구부 | ifcopenshell 이 빈 불리언을 버려 **안 뚫린 벽**이 나가고, 재검사는 엉뚱한 부피 오류를 냄 | 선언하지 않고 `failed_hosts` 에 `cut would consume the whole host` — FreeCAD 경로와 같은 판정 |
| 경계 계산이 try 밖 | 빈 메시 하나로 영수증에 사유 없이 트레이스백 | try 안으로 |
| 모델 경계가 `None` | V104(폭주 솔리드)가 꺼져 있었음 | 제품 경계의 합집합을 `bbox` 로. 골든 5건에서 오경보 0건 |

수정 뒤 `지하3층` 두 층 조립(벽 1,328)이 28.6초에 `verified` 이고, 층마다 뺀 부피가 한 층 빌드와
**정확히** 같다. 층을 넘는 절삭 0건. 새 전량 절삭 가드는 골든 5건에서 한 번도 울리지 않았다.

**고치지 않은 것 둘(의도):**
- **외곽선 덕트·트레이는 `mep_volume` 비율에 넣지 않는다.** 기대·실제 부피를 같은 식(면적×높이)으로
  셈해 비율이 늘 1 이고, 섞으면 축선 부재가 종잇장이 되는 사고를 합계 비율이 가린다(FreeCAD 경로는
  넣는다 — 그쪽의 약점이다). 대신 외곽선 제품의 부피는 V107 이 IFC 에서 제품마다 잰다.
- **V101·V105 는 여전히 `catalog` 단계 전용이다**(두 빌더 모두 운영에서 안 돈다). 같은 목적을
  `inspect_ifc` 가 덮는다 — 빠진 부재는 EID 대조, 빠진 속성은 Pset 필수 검사. 틀렸던 것은
  "V101 이 대조한다" 는 **빌더 주석**이었고 그걸 고쳤다.

## 첫 종합평면도에서 (2026-09-23)

층마다 A1 도곽을 나란히 놓은 xref 묶음 평면도의 기준층 한 장(벽 721 · 개구부 136)을 빌드하자 재검사가 두 가지로 막았다.
둘 다 **2.5D 계산은 맞는데 IFC 불리언 엔진(OCC)의 허용치 아래 조각을 선언**한 것이다.
- **스침**: 커터가 여러 선분 벽의 모서리를 평면 0.35mm² 쯤 긁었다(깊이 0.0002mm). 접촉 판정이 '부피 > 1e-6mm³' 라 절삭으로
  선언됐다. 평면 교차 1mm² 미만(`MIN_CUT_AREA_MM2`)은 닿지 않은 것으로 본다(`_cut_contact`).
- **거의 중복**: 같은 창이 두 레이어에 조금 어긋나 겹쳐 그려지면 두 번째 커터가 **새로** 빼는 양이 418mm³ 뿐이다. 그걸 선언하면
  거의 겹친 두 무효체 경계에서 엔진이 28mm³ 쯤 다르게 잘라 V107 '내보낸 부피 ≠ 계산한 부피' 가 났다(허용치는 가장 작은 절삭 ×
  0.5% = 2mm³ — 빠진 절삭을 잡으려고 일부러 작다). 새로 빼는 양을 평면 면적으로 환산해 1mm² 미만이면 이미 비운 자리(`already_void`)
  로 본다(`_negligible_cut`). 종전 기준(≤ 1e-6mm³)은 완전 중복만 걸렀다.
- 좌표가 원점에서 2km 떨어진 것(도곽 배치 위치)은 원인이 **아니었다** — 원점 기준으로 다시 재도 차이는 0.003mm³ 였다.
- 결과: 그 기준층 `verified`(벽 721 · 절삭 86, 경고만 — 벽이 끊긴 자리의 창 · 검토 대기).

## 잃은 것 (정직하게)

- `.FCStd` 산출물 — 소비자가 없었다. FreeCAD 버튼은 남아 있으므로 필요하면 그대로 낸다.
- `freecad_builder.check_clashes`(FreeCAD 불리언 간섭) — 일상 검토는 이미 `clash_review` 2.5D 다.
- 미확인: Bonsai 렌더링 대조 · PyInstaller onefile 에서 납품 빌드가 실제로 도는지(`--selftest` 가
  `ifc_builder`·`ifcopenshell.geom` 의 import 까지는 보지만 빌드는 안 돌린다). 둘 다
  `docs/release_checklist.md` 5번이다.

## 이 문서를 고정하는 테스트

| 절 | 잠그는 테스트 |
|---|---|
| 모든 카테고리가 IFC 에 나가고 재검사를 통과한다 | `test_every_category_reaches_the_ifc_and_the_artifact_verifier_passes` |
| 스침·거의 중복 절삭은 선언하지 않는다 | `test_a_cutter_that_only_grazes_a_wall_is_not_a_cut` · `test_a_near_duplicate_opening_counts_as_already_void` |
| 개구부는 선언이고 `create_shape` 가 그 부피를 실제로 뺀다 | `test_an_opening_is_a_declared_void_and_the_shape_really_loses_that_volume` |
| 층별 `wall_indices`·이음 id·벽타입 — 위층 문이 아래층 벽을 뚫지 않고, 타입이 층마다 복제되지 않는다 | `test_a_stacked_floor_cuts_its_own_wall_and_not_the_one_below` |
| 이음 몸체 형식은 물량표와 같은 함수에서 온다 | `test_a_confirmed_joint_becomes_a_fitting_body_of_the_kind_the_quantity_table_counts` |
| 원형 단면은 평면 다각형으로만 나간다(곡면 금지) | `test_round_sections_ship_as_flat_faced_polygons_so_the_re_reader_sees_a_manifold` |
| 선언 재질 + 벽 접합(GUI 기본값)이 같이 서도 빌드가 산다 | `test_a_declared_wall_material_survives_the_wall_connection_the_gui_turns_on` |
| 같은 두께 다른 재질이 레이어셋을 공유하지 않는다 | `test_two_walls_of_one_thickness_keep_their_own_declared_materials` |
| 선언 없는 재질은 비어 있다 — 'Concrete' 로 안 채운다 | `test_an_undeclared_wall_material_stays_empty_instead_of_becoming_concrete` |
| 못 만든 레코드는 수가 아니라 **무엇이 왜** 로 영수증에 | `test_a_record_that_could_not_be_built_blocks_the_file_and_the_receipt_names_it` |
| 납품 메뉴는 FreeCAD 없이도 눌린다 | `test_hidden_tk_delivery_menu_builds_ifc_directly_and_freecad_moves_to_the_tools_frame` |
| 영수증이 선언한 산출물만 검증한다 | `test_artifact_claim_requires_current_receipt_and_actual_hash` (`tests/test_project_clients.py`) |
| 거부할 때 지난 영수증을 먼저 무효화한다 | `test_a_refused_build_invalidates_the_old_receipt_before_writing` |
| 층 조립은 층마다 실측 두께·벽 높이를 지킨다 | `test_a_stacked_build_keeps_each_floors_measured_wall_thickness_and_wall_height` |
| 개구부는 수동 편집값으로 뚫린다 | `test_an_edited_opening_is_cut_where_the_preview_draws_it` |
| 벽을 통째로 비우는 개구부는 이름을 적고 막는다 | `test_an_opening_that_would_empty_its_wall_is_refused_by_name` |
| 외곽선 트레이는 트레이 단면으로 선다 | `test_a_footprint_tray_stands_on_the_tray_section_not_the_duct_one` |
| 영수증에 모델 경계가 있어 V104 가 돈다 | `test_the_receipt_carries_the_model_bbox_so_the_runaway_solid_check_runs` |
| 외곽선 덕트는 비율에서 빼되 부피는 V107 이 잰다 | `test_a_footprint_duct_is_left_out_of_the_mep_ratio_but_its_volume_is_still_checked` |

## 다시 뒤집는다면

이 결정을 되돌릴 측정은 하나다 — 골든 단위세대 통합 `.mep` 를 두 빌더로 빌드해 Bonsai 에
나란히 열기. ① 둘 다 verified ② 개구부 뚫림·설비/이음 보임·`NeedsReview` 필터 됨
③ ifcopenshell 경로 60초 안. 하나라도 아니면 FreeCAD 를 납품으로 되돌린다.
