# 결정 기록 — 무엇이 테스트로 잠겨 있는가

이 폴더의 파일은 **왜 그렇게 했는지**의 기록이다. 상시 규약(z 기준면·`opts` 키·컬럼·실행 순서)은
`CLAUDE.md` 에 있다.

기록만으로는 부족하다. **잠기지 않은 절은 다음 사람이 다시 깨뜨린다** — 문서는 읽지 않아도 빌드가 되고,
깨진 뒤에야 문서가 맞았다는 것을 안다. 그래서 절마다 그것을 고정하는 테스트를 적는다.

## 규칙

**새 ★ 절을 쓸 때는 이 표에 한 줄을 같이 적는다.** 테스트 이름이거나 `기록만` 이다. 둘 다 못 적겠으면
그 절은 아직 쓸 때가 아니다.

`기록만` 이 정당한 경우는 둘뿐이다:
- **하지 말라는 기록** — 측정해 보고 접은 대안(`pair_max` 상향 · 거리로 잇기). 지금 코드에 없는 것은
  테스트로 고정할 대상이 없다. 다시 하려는 사람을 막는 것이 목적이다.
- **외부 환경의 사실** — FreeCAD·Pascal·브라우저의 동작. 우리 코드가 아니라 그쪽이 바뀐다.

## 절 → 테스트

| 절 | 고정하는 것 |
|---|---|
| **clash-review.md** | |
| 간섭은 위치·부재·조치 목록으로 | `tests/test_clash_review.py` 전체(14) — 위치·조치 구분·재파싱 동일 id |
| 슬리브는 자재로 서되 판정은 `role` 로 | `test_a_penetration_at_a_drawn_sleeve_is_told_apart_from_one_without` |
| 상부 구조는 간섭 계산에만 합성 | `test_ceiling_and_floor_slabs_come_from_the_level_declaration_only_when_it_exists` |
| 다원본 스택에 `levels`·`region` 을 남긴다 | `tests/test_stack.py` · `tests/test_same_floor_sources.py` |
| 높이 근거를 판정과 같이 싣는다 | `test_each_row_says_whether_the_heights_it_used_were_declared_or_assumed` |
| 평면도의 z 0 은 근거가 아니다 | `test_a_plan_z_of_zero_is_not_evidence_of_an_installation_height` · `tests/test_mep_profiles.py` 의 `PLAN_Z_AS_ELEVATION`·`SOURCE_Z_OUTSIDE_STOREY` 두 건 |
| 목록 전체가 가정 위에 서 있다는 한 줄 | `tests/preview_review.test.mjs` — 배너 문구 · 0건이면 빈 문자열 |
| 간섭은 그 벽 위에서 센 것이다 | `test_a_clash_row_says_how_the_parser_found_the_wall_it_stands_on` |
| 일상 검토는 2.5D 에서 끝난다(CSV) | `test_the_list_becomes_one_table_excel_can_open` · `test_the_command_line_writes_both_tables` |
| `check_clashes` 가 느렸던 이유 | `tests/test_builder.py` 의 `clash_pairs_checked`·`stage_seconds` (네이티브) |
| **mep-connectivity.md** | |
| 이음은 도면이 이어 그린 곳에만 | `tests/test_mep_joints.py`(7) |
| 평면 탭에서 옮긴 설비는 이음을 다시 안 잡는다(현재 동작) | `tests/test_mep_workflow.py` 의 `test_saving_an_added_pipe_and_moving_a_jointed_one_updates_connectivity_live` |
| `connect_gap=` 으로만 잇는다 | `tests/test_route_extraction.py` · `tests/test_mep_paths.py` |
| 외곽선 폭으로 규칙을 나눈다 | `tests/test_outline_widths.py`(3) |
| 연결성은 따로 센다 · 후보·확정 | `tests/test_mep_network.py`(13) — 후보 종류 · 단말·슬리브 · 확정 왕복 · 일괄 확정 |
| 본체 후보는 도구가 제안한다 | `tests/test_equipment_bodies.py`(6) — 제안 · 모호 · 이미 한 몸 · 높이로 갈린 묶음 |
| 묶는 방식은 파이프라인과 같아야 한다 | `test_sources_the_pipeline_keeps_apart_by_height_are_kept_apart_here_too` |
| 일상 후보는 한 번에 확정한다 | `test_only_straight_and_elbow_candidates_of_the_same_size_are_offered_as_routine` · `test_confirming_many_candidates_takes_one_revision_and_is_all_or_nothing` |
| 피팅 폴리곤·거리로 잇기를 접었다 | **기록만** (하지 말라는 기록) |
| **mep-geometry.md** | |
| `normalize()` 가 덕트를 종잇장으로 | `tests/test_builder.py` 의 `mep_volume` · V106 비율 |
| 계약 v3 `path3d` | `tests/test_route_contract.py`(18) · `tests/test_mep_contract.py` |
| 공통 마이터 링 · 원형 관 — 곡면(`Arch.makePipe`)은 평면 직선도 V107 이라 아예 안 쓴다 | `tests/test_round_tube.py` · `tests/test_mep_freecad_contract.py`(`test_pipe_uses_reviewed_outer_diameter_and_axis` · `test_a_single_straight_round_pipe_builds_a_manifold_ifc_shape`, 네이티브) · `tests/preview_mep.test.mjs` |
| 면 수만 개에서 Arch 후처리가 멈춘다 | **기록만** (FreeCAD 의 동작) |
| 이음 몸체 — 형식은 물량표와 같은 함수 · 크기는 틈과 단면 · 끊긴 이음은 사유 | `test_bodies_fill_the_corner_reach_the_confirmed_gap_and_match_the_quantity_table` · `test_a_broken_joint_gets_no_body_and_says_why` |
| 이음 몸체가 IFC 피팅으로 나가 재검사를 통과한다 | `test_fittings_reach_the_ifc_as_fitting_classes_and_pass_the_reverification` (네이티브) |
| 토막을 합치지 않는다 · 치수표를 쓰지 않는다 | **기록만** (FreeCAD 내보내기의 동작 · 하지 말라는 기록) |
| **walls.md** | |
| 중복 부재 · EID 까지 같은 벽 | `tests/test_parser.py` · 골든의 `eid_collisions` |
| 벽 병합은 사라지는 속성을 먼저 본다 | `tests/test_merge_layer.py`(19) |
| 두께 우선순위 | `tests/test_contract.py` 의 `width_of` · `tests/test_width_conflict.py` · `tests/test_boq.py`(BOQ 도 같은 순서) |
| `single_offset` 을 `pair_max` 로 줄이지 말 것 | **기록만** (하지 말라는 기록) |
| EID — `_span_sigs` 는 생성 시점 값 | `tests/test_edit_recovery.py`(16) · 골든 |
| 칸막이 보드선 · 겹친 짝 접기 | `tests/test_thin_pair.py` · `tests/test_wall_opening_quality.py` |
| 길이 0 벽 — 치유가 두 끝을 한 점으로 · 미세 선분 | `tests/test_merge_layer.py` 의 `test_healing_never_pulls_both_ends_of_a_wall_onto_one_point` · `test_healing_does_not_flip_a_wall_whose_ends_cross_over` · `test_a_micro_edge_never_becomes_a_zero_length_wall` |
| **openings.md** | |
| 개구부 판정은 세 갈래 · `no_host_reason` 네 사유 | `tests/test_openings.py`(9) · `tests/test_verify.py` 의 V106 |
| 이미 뚫린 자리는 실패가 아니다 | `tests/test_openings.py` 의 `already_void` |
| 맞닿기만 한 개구부는 호스트가 아니다(축·수직 최소 1mm 겹침) | `test_an_opening_that_only_touches_a_wall_end_is_in_the_gap_not_on_the_wall` · `test_an_opening_whose_cutter_only_touches_the_wall_face_is_not_a_host` |
| 블록 이름이 부호다 · 문은 창 기본값을 받지 않는다 | `test_block_openings_carry_their_mark_and_doors_do_not_get_window_defaults` |
| 높이·창대는 일람에서 부호로 · 빈칸은 빈칸 | `test_schedule_rows_fill_height_and_sill_by_mark_and_the_rest_stay_assumed` · `test_blank_schedule_cells_do_not_masquerade_as_user_values` · `test_door_window_prefixes_are_doors_like_every_other_mark_table` · `test_openings_made_from_the_schedule_are_not_listed_as_plan_marks` |
| 끊김에 이미 창이 있으면 두 번 뚫지 않는다 | `test_a_gap_that_already_holds_a_block_opening_is_not_opened_twice` |
| 창 위아래 벽을 채운다 · 벽이 이어지면 채우지 않는다 · 일람 치수 · 수정 보존 | `tests/test_opening_infill.py` 의 `test_window_gets_a_sill_wall_and_a_lintel_and_a_door_gets_a_lintel` · `test_a_continuous_wall_is_cut_by_the_builder_and_needs_no_infill` · `test_schedule_dimensions_drive_the_infill_and_clear_the_assumption` · `test_edits_on_infill_walls_survive_a_reparse_and_are_not_orphans` · `test_quantities_use_the_infill_height_not_the_storey_height` |
| 개구부 종류는 선언으로도 온다(`opts subtype=door\|window`) — 부호 없는 블록·선으로 그린 창 | `test_declared_subtype_gives_unmarked_openings_their_sill_wall_and_lintel` · `test_opening_subtype_is_declared_door_or_window_only` |
| 층 판정은 `floor_z` — 떠 있는 벽이 층을 만들지 않는다 | `test_raised_walls_do_not_make_floors_and_pass_the_floor_gate` · `test_stacked_levels_move_the_floor_anchor_and_prefix_the_opening_link` · `test_pascal_keeps_one_level_reports_raised_walls_and_an_untouched_scene_saves_nothing` · `test_a_pipe_just_above_a_lintel_bottom_is_a_wall_penetration_not_under_a_wall` |
| 블록 창호의 중심 · 스윙 문의 축 · 문설주 조각 | `test_block_openings_sit_at_the_gap_center_not_at_the_insertion_end` · `test_a_swing_door_drawn_with_its_leaf_along_local_x_is_centred_on_the_wall` · `test_door_jamb_pieces_touching_the_door_edge_are_still_fragments` |
| 기준 벽 · 다른 개구부 · 재연결 · 다층 IFC | `test_closed_polygon_walls_are_never_the_reference_so_no_infill_lands_on_a_wall_face` · `test_a_window_overlapping_a_door_does_not_get_a_sill_wall_inside_the_door` · `test_relink_keeps_infill_edits_on_infill_walls_and_wall_edits_on_walls` · `test_multi_storey_ifc_moves_the_floor_anchor_with_each_storey` |
| GUI 가 묻는다 — 벽 같은 기둥 레이어 · 추정 창호 치수 | `test_the_gui_asks_about_a_wall_like_column_layer_once_and_reparses_on_yes` · `test_the_gui_asks_for_window_dims_when_they_are_assumed_and_reparses_after_a_schedule_is_picked` · `test_layer_rule_inserted_first_wins_over_the_broad_column_rule` · `test_cancelling_the_schedule_file_dialog_keeps_the_question_for_next_time` |
| **drawings-in-practice.md** | |
| 레이어 이름이 부재를 안 알려 준다 · 벽 증거 | `tests/test_layer_evidence.py`(10) — 짝 비율·간격 · 보드선 가드 · 기둥 레이어가 벽일 때 · 진짜 기둥 · 표본 |
| 동봉 규칙의 A-COL 은 벽(정확히 그 이름만) · 증거 경로는 S-COL 로 | `test_apartment_convention_rows_do_not_shadow_the_wall_rules_below_them` · `test_a_column_layer_drawn_as_walls_says_so_and_offers_the_layer_map_row` · `test_a_real_column_layer_is_left_alone` |
| 기하 투표만으로는 자동 적용하지 않는다 | `test_a_geometry_only_guess_is_never_auto_applied` |
| 한 층에 공종 도면 여러 장 | `tests/test_same_floor_sources.py`(5) |
| `ignore` 는 두 분기 모두에서 | `test_ignore_layer_never_becomes_an_elements_bucket` |
| 블록 이름이 폭을 담는다 · 문짝 조각 | `tests/test_wall_opening_quality.py`(5) · `tests/test_layer_map.py` |
| explode 된 부재의 `layer` | `tests/test_source_classification.py`(14) |
| 환기 평면도는 중심선 선언으로 | `tests/test_layer_map.py` 의 `centerline=` · `elevation=` |
| **edits-and-preview.md** | |
| 수정 라운드트립의 주입 위치 | `tests/test_edit_recovery.py` · `tests/test_roundtrip.py`(9) |
| 미리보기가 근거를 말한다 | `test_review_flags_always_carry_a_reason` · `tests/preview_review.test.mjs`(32) |
| 2D 평면 탭 · delete+add 어휘 | `tests/test_preview_editing.py` · `tests/preview_coordinates.test.mjs` |
| 고아 재연결은 제안까지만 | `tests/test_edit_recovery.py` 의 `suggest_relink` |
| 저장 한 번 = 파싱 한 번 | `tests/test_project_server.py` 의 `test_save_parses_once_and_the_returned_revision_is_immediately_usable` |
| 편집 값의 `null` 은 지운다 · 저장 경계는 유한수·category 를 검사한다 | `tests/test_project_store.py` 의 `test_elevation_accepts_zero_and_negative_but_not_a_string_or_infinity` · `test_added_edit_without_category_is_rejected` · `test_null_override_deletes_the_key_instead_of_storing_a_stale_none` · `tests/preview_edit_geometry.test.js` |
| '수정 적용' 은 손댄 칸만 저장 · 벽 폭 칸은 세워진 두께 | `tests/preview_review.test.mjs` 의 `'inspector apply saves only the fields the user touched…'` |
| 인스펙터 높이 칸(첫 배포는 MEP 만) | `tests/preview_edit_geometry.test.js` · `tests/test_project_server.py` 의 `test_pipe_elevation_override_saves_reopens_and_moves_the_z_range` · `tests/test_clash_review.py` 의 `test_an_edited_pipe_elevation_reports_declared_not_source` · `tests/test_mep_freecad_contract.py` 의 `test_an_edited_elevation_that_crosses_a_floor_boundary_moves_the_ifc_storey_too` |
| 평면 탭에 배관·덕트·트레이(선택·끌기·분할·결합·그리기) | `tests/preview_edit_geometry.test.js` · `tests/test_roundtrip.py` 의 `test_a_manual_pipe_reaches_route_points_network_and_verify_clean` · `tests/test_mep_workflow.py` 의 `test_saving_an_added_pipe_and_moving_a_jointed_one_updates_connectivity_live` |
| 검토 항목을 버튼으로(`suggestions_apply` · `/layer-rule`) | `tests/test_thin_pair.py`·`tests/test_layer_evidence.py`·`tests/test_width_conflict.py`(구조화 제안 3종) · `tests/test_layer_map_io.py`(5) · `tests/test_project_server.py`(적용·멱등·낡은 revision·롤백·소스 없음·프로필 거절 6종) · `tests/test_project_clients.py` 의 `test_mcp_apply_layer_rule_inserts_above_existing_rules_not_at_the_end` |
| 편집기 손맛 여섯(Planform 대조) — 다시 실행 · Shift 직교 · 팬 · 합계 · 각주/배지 · 나란히 보기 | `tests/preview_review.test.mjs`(`createHistory` 2건 · 합계 1건) · `tests/preview_edit_geometry.test.js`(`orthoPoint`) · `tests/test_boq.py::test_excel_says_what_it_does_not_count` |
| 저장 응답은 기준선만 옮긴다(redo 를 먹지 않는다) · `#view2d` 는 SVG 라 폭을 직접 준다 | 위 `createHistory` 저장 응답 테스트 · **기록만**(브라우저의 치환 요소 동작) |
| 물량 요약 패널 — 저장이 곧 갱신, 재파싱 재호출 없음 · 높이 출처는 값이 아니라 키 존재로(다층 조립도 같은 신호) | `tests/test_boq.py::test_state_carries_boq_and_an_edit_updates_it_on_save` · `tests/test_preview_editing.py`(boq·level_height_declared 허용목록 2건) · `tests/preview_review.test.mjs`(4건 + 저장 응답 모양 `'the storey-height basis survives a save response…'`) · `tests/test_stack.py`(다층 선언 신호 3건) |
| 장면이 문제를 가리킨다(HighTopo 대조) — 간섭 상대 강조 · 이음 무리 · 키 가드 · 계통 한 줄 · 간섭 고리 · 저장 뒤 한 줄 · 끊긴 끝 행 · 범례 숨김 · 3D 간섭 고리 | `tests/preview_review.test.mjs` 의 `joinedEids`·`systemLineText`·`isTypingTarget`·`clashMarkerSpecs`·`changeSummaryText`·끊긴 끝 행·`shownClashItems`·`legendHtml` 8건 + `data-struct` 두 줄 |
| 흐름 방향 애니메이션을 하지 않는다(평면도에 유향이 없다) · 선택 한 줄기·시선 유지·강조 색 | **기록만**(하지 말라는 기록) · 나머지는 브라우저의 동작 — `release_checklist.md` 8번 |
| **pascal.md** | |
| 다리는 반사가 아니라 회전 | `test_the_bridge_is_a_rotation_not_a_mirror` |
| 단위·축·고저 · 노드 대응 | `tests/test_pascal_bridge.py`(44) |
| 플러그인 단면이 Python 계약과 같다 | `test_plugin_section_rings_match_the_python_contract_under_the_axis_swap` |
| 편집 화면 호스트 · 토큰·루프백 | `tests/test_pascal_host.py`(5) · `tests/test_pascal_runtime.py`(4) · `tests/test_pascal_sync.py`(6) |
| 검토 탭 | `tests/test_pascal_review.py` |
| 저장 상태 표시를 번역에서 뺀다 | **기록만** (브라우저의 동작) |
| 2026-09-18 동결 — 일상 편집은 평면 탭, Pascal 인스펙터 전용 기능(계통·재질 롤·단면 롤·장면 트리) | **기록만** (Pascal 자신의 동작 · 코드 변경 없음) |
| **ifc-builder.md** | |
| 납품 IFC 는 `ifc_builder`(ifcopenshell 직접) — 모든 카테고리가 나가고 재검사를 통과한다 | `tests/test_ifc_delivery.py` 의 `test_every_category_reaches_the_ifc_and_the_artifact_verifier_passes` |
| 개구부는 절삭이 아니라 선언 — `create_shape` 가 그 부피를 실제로 뺀다 | `test_an_opening_is_a_declared_void_and_the_shape_really_loses_that_volume` |
| `wall_indices`·이음 id·벽타입은 층을 이어붙일 때의 함정 — 위층 문이 아래층 벽을 뚫지 않고 타입이 층마다 복제되지 않는다 | `test_a_stacked_floor_cuts_its_own_wall_and_not_the_one_below` |
| 이음 몸체 형식은 물량표와 같은 함수 · 이음 id 는 층마다 따로 | `test_a_confirmed_joint_becomes_a_fitting_body_of_the_kind_the_quantity_table_counts` |
| 원형 단면은 평면 다각형으로만 — 곡면 솔리드는 나가지 않는다 | `test_round_sections_ship_as_flat_faced_polygons_so_the_re_reader_sees_a_manifold` |
| 재질은 벽타입 캐시의 키 — 접합 벽의 레이어셋을 덮어쓰지 않고, 선언 없으면 비워 둔다 | `test_a_declared_wall_material_survives_the_wall_connection_the_gui_turns_on` · `test_two_walls_of_one_thickness_keep_their_own_declared_materials` · `test_an_undeclared_wall_material_stays_empty_instead_of_becoming_concrete` |
| 납품 메뉴는 FreeCAD 없이 눌린다 · FreeCAD 는 '그 밖의 도구' | `tests/test_mep_workflow.py` 의 `test_hidden_tk_delivery_menu_builds_ifc_directly_and_freecad_moves_to_the_tools_frame` |
| 못 만든 레코드는 수가 아니라 무엇이 왜 로 영수증에 | `tests/test_ifc_delivery.py` 의 `test_a_record_that_could_not_be_built_blocks_the_file_and_the_receipt_names_it` |
| 영수증이 선언한 산출물만 검증한다 | `tests/test_project_clients.py` 의 `test_artifact_claim_requires_current_receipt_and_actual_hash` |
| 거부할 때 지난 영수증을 먼저 무효화한다 | `tests/test_artifact_validation.py` 의 `test_a_refused_build_invalidates_the_old_receipt_before_writing` |
| 적대적 리뷰 7건 — 검사가 빌더의 약속과 IFC 를 대조할 뿐 약속이 맞는지는 모른다 | `tests/test_ifc_delivery.py` 의 `test_a_stacked_build_keeps_each_floors_measured_wall_thickness_and_wall_height` · `test_an_edited_opening_is_cut_where_the_preview_draws_it` · `test_an_opening_that_would_empty_its_wall_is_refused_by_name` · `test_a_footprint_tray_stands_on_the_tray_section_not_the_duct_one` · `test_the_receipt_carries_the_model_bbox_so_the_runaway_solid_check_runs` |
| 외곽선 MEP 는 `mep_volume` 비율에서 뺀다(합계가 종잇장 사고를 가린다) | `test_a_footprint_duct_is_left_out_of_the_mep_ratio_but_its_volume_is_still_checked` |
| 스침·거의 중복 절삭은 선언하지 않는다(엔진 허용치 아래 조각) | `tests/test_ifc_delivery.py` 의 `test_a_cutter_that_only_grazes_a_wall_is_not_a_cut` · `test_a_near_duplicate_opening_counts_as_already_void` |
| `freecad_builder.py` 동결 — `.FCStd`·`check_clashes` 는 남기되 새 기능은 안 얹는다 | **기록만**(하지 말라는 기록) |
| **construction-rules.md** | |
| 규칙은 표 한 장 · confirmed 만 적용 | `test_every_rule_row_carries_standard_clause_url_verdict_and_retrieved_date` |
| 선언이 없으면 건너뛰고 영수증에 센다 | `test_only_confirmed_rules_apply_and_the_receipt_lists_every_skipped_reason` |
| DN 은 선언에서만 — 외경으로 역산하지 않는다 | `test_hanger_counts_use_declared_material_and_nominal_size_and_never_the_outside_diameter` |
| 위반은 V013 · 정보는 필요 낙차만 말하고 형상은 두지 않는다 | `tests/test_verify.py` 의 `test_V013_fires_on_a_flat_duct`·`test_V013_silent_on_a_compliant_duct` · `test_a_drain_run_reports_the_fall_it_needs_and_says_the_slope_is_undeclared` |
| 위반 판정(종횡비·소화전 최소 관경) | `test_a_flat_duct_over_one_to_four_is_a_violation_and_a_square_duct_is_silent` · `test_a_hydrant_branch_under_dn40_is_a_violation_and_dn40_is_not` |
| 지지·청소구는 하한 추정 · 45도를 넘는 꺾임만 청소구 | `test_cleanouts_count_the_head_the_length_bands_and_turns_over_45_degrees` · `tests/test_route_outputs.py` 의 `test_the_support_sheet_says_it_is_a_lower_bound_and_names_the_clause` |
| 원형 덕트 지지는 물리 지름을 그대로 쓴다(중복 선언 요구하지 않는다) | **기록만**(설계 선택 — 재도전 대상 아님) |
| 슬리브 규격은 그려진 슬리브를 외경+40 과 대조한다 — 몸체는 만들지 않는다 | `test_a_drawn_sleeve_smaller_than_pipe_od_plus_40_is_its_own_row_and_names_the_clause` |
| 기둥·내력벽 관통은 수치가 아니라 구조 확인 요청이다 | **기록만**(국내 출처 없음 — `BACKLOG` 의 `rc-beam-hole-limits`) |
| 수직관은 표 3.4-1 대로 '각 층 1개소' — 층 정보 없으면 건너뛴다 | `test_risers_take_one_support_per_storey_only_when_storeys_are_declared` |
| 스프링클러 행거는 선언한 배관 등급(가지/주행)을 따른다 | `test_sprinkler_hangers_follow_the_declared_pipe_class` |
| 보온 두께는 선언 등급·온도·DN 의 표 조회 — 계통 이름으로 추정하지 않는다 | `test_insulation_thickness_comes_from_declared_grade_temperature_and_dn_only` · `test_a_humid_location_takes_the_humid_table` · `tests/test_mep_profiles.py` 의 `test_insulation_declares_grade_and_temperature_and_the_parser_stamps_the_looked_up_thickness` · `tests/test_route_outputs.py` 의 `test_the_insulation_sheet_reads_the_thickness_mep_profile_already_looked_up` |
| 보온 외피는 opt-in — 켜면 간섭 수가 바뀐다 | `test_an_insulated_pipe_widens_the_clash_band_only_when_the_profile_opts_in` |
| 구배는 비율·높은 끝을 둘 다 선언할 때만 | `tests/test_route_contract.py` 의 `test_a_declared_slope_lowers_the_low_end_by_length_over_ratio_and_says_so` · `tests/test_clash_review.py` 의 `test_a_sloped_drain_meets_the_slab_where_the_slope_puts_it` |
| Pascal 은 이미 path3d 를 타고 구배도 그대로 간다 | **기록만**(기존 동작 확인 — 새 코드 없음) |
| 3D 미리보기는 경사·수직 구간을 평평하게 보여준다 — 조용히 다르지 않고 말한다 | **기록만**(`mepRenderWarnings` — node:test 는 `review_logic.js` 만 잠근다. 프리뷰 렌더 자체는 수동 점검 대상) |
| 건축 물량산출 관행·방화구획·피팅 치수표는 하지 않는다 | **기록만** (근거 없음 · 별도 설계 필요 · 기존 기록과 충돌) |
| 프로젝트 기본값은 선언 — 읽는 순서는 override > 선언 > 기본값 · 영수증은 defaulted/missing 을 따로 센다 | `tests/test_project_defaults.py`(17) · `test_receipt_counts_defaulted_declarations_separately_from_missing` · `test_material_of_reads_overrides_before_the_top_level_field` · `test_declaration_basis_reaches_the_ifc_pset` · `test_declaration_basis_reaches_the_saved_model` |
| 열 때 세 질문(층고·창호일람·재질) · 재열기는 '프로젝트 기본값' 버튼으로 | `tests/test_window_marks.py` 의 `test_open_defaults_into_threads_height_and_fans_material_out_to_all_four_categories` · `test_merge_material_default_sets_all_four_architecture_categories_and_keeps_other_keys` |
| 설비 프로젝트의 프로젝트 기본값은 `MepSetupDialog` 소탭 · 저장 키가 달라 저장 버튼도 따로다 | `tests/test_mep_workflow.py::test_hidden_tk_defaults_tab_loads_and_forms_mep_declarations` |

## 아직 잠기지 않은 것

지금 표에서 `기록만` 이 아닌데 테스트 이름을 못 적은 절은 없다. 다음에 절을 추가하면서 못 적겠으면
여기에 적고 왜 어려운지 한 줄 남긴다 — 비워 두면 다음 사람은 잠긴 줄 안다.
