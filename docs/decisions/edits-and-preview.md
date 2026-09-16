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
- **일괄 수정은 `layer_map` 한 줄 제안**으로 낸다(`bulkHtml`). 개별 EID 41개로
  저장하면 도면이 바뀔 때 전부 고아가 된다. 자동으로 쓰지 않는다 — 붙여 넣을
  줄을 만들어 줄 뿐이다(`map-layers` 스킬: "사람이 CSV 를 쓴다").

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
