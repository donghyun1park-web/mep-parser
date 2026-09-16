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
| 공통 마이터 링 · 원형 관 | `tests/test_round_tube.py` · `tests/test_mep_freecad_contract.py` · `tests/preview_mep.test.mjs` |
| 면 수만 개에서 Arch 후처리가 멈춘다 | **기록만** (FreeCAD 의 동작) |
| **walls.md** | |
| 중복 부재 · EID 까지 같은 벽 | `tests/test_parser.py` · 골든의 `eid_collisions` |
| 벽 병합은 사라지는 속성을 먼저 본다 | `tests/test_merge_layer.py`(19) |
| 두께 우선순위 | `tests/test_contract.py` 의 `width_of` · `tests/test_width_conflict.py` |
| `single_offset` 을 `pair_max` 로 줄이지 말 것 | **기록만** (하지 말라는 기록) |
| EID — `_span_sigs` 는 생성 시점 값 | `tests/test_edit_recovery.py`(16) · 골든 |
| 칸막이 보드선 · 겹친 짝 접기 | `tests/test_thin_pair.py` · `tests/test_wall_opening_quality.py` |
| **openings.md** | |
| 개구부 판정은 세 갈래 · `no_host_reason` 네 사유 | `tests/test_openings.py`(9) · `tests/test_verify.py` 의 V106 |
| 이미 뚫린 자리는 실패가 아니다 | `tests/test_openings.py` 의 `already_void` |
| **drawings-in-practice.md** | |
| 레이어 이름이 부재를 안 알려 준다 · 벽 증거 | `tests/test_layer_evidence.py`(9) — 짝 비율·간격 · 보드선 가드 · 기둥 레이어가 벽일 때 · 진짜 기둥 · 표본 |
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
| **pascal.md** | |
| 다리는 반사가 아니라 회전 | `test_the_bridge_is_a_rotation_not_a_mirror` |
| 단위·축·고저 · 노드 대응 | `tests/test_pascal_bridge.py`(44) |
| 플러그인 단면이 Python 계약과 같다 | `test_plugin_section_rings_match_the_python_contract_under_the_axis_swap` |
| 편집 화면 호스트 · 토큰·루프백 | `tests/test_pascal_host.py`(5) · `tests/test_pascal_runtime.py`(4) · `tests/test_pascal_sync.py`(6) |
| 검토 탭 | `tests/test_pascal_review.py` |
| 저장 상태 표시를 번역에서 뺀다 | **기록만** (브라우저의 동작) |

## 아직 잠기지 않은 것

지금 표에서 `기록만` 이 아닌데 테스트 이름을 못 적은 절은 없다. 다음에 절을 추가하면서 못 적겠으면
여기에 적고 왜 어려운지 한 줄 남긴다 — 비워 두면 다음 사람은 잠긴 줄 안다.
