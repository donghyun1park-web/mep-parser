# 개선 계획서 — 2026-09-16 솔직 판단 7항목

## 0. 배경과 원칙

2026-09-16 기능 점검의 결론: 이 도구는 "평면도에서 결정론적으로 뽑고 못 한 것을 자기보고한다"는 원칙은
지키지만, **판정의 입력이 아직 사람 손에서 오고 그 입력을 넣는 비용이 크다.** 아래 7항목은 그 판단을
코드 단위로 옮긴 것이다.

| 실측 근거(단위세대 난방·환기, 2026-09-15~16) | 값 |
|---|---|
| 통합 모델 간섭 중 높이 근거가 가정인 줄 | 24 / 24 |
| 환기 연결 후보(한 건씩 확정) | 26 (직선 6 · 엘보 16 · 티 4) |
| 난방 끊긴 끝, 아직 열림 | 10 (분배기 본체를 못 골라 `Ambiguous`) |
| 통합 FreeCAD 빌드 | 421초 (IFC 122.8 · 설비 형상 189 · 간섭 88.4) |
| 2.5D 간섭 목록 | 0.1초 |
| 회귀 골든 | 도면 2장 + 프로젝트 2건 |
| 프런트 테스트 / 파이썬 테스트 | 27 / 635 |
| CLAUDE.md · dxf_parser.py · app.js · mep_gui.py | 1,349 · 3,707 · 1,229 · 1,180 줄 |

**공통 원칙(측정으로 확정된 것)**
- 높이를 추정해 **바꾸지 않는다**. 근거를 표시하고 선언을 유도한다.
- 거리로 잇지 않는다(SA↔RA 오결합 실측). `pair_max` 를 올리지 않는다(덩어리 118개 실측).
- 장비 본체를 코드가 고르지 않는다. **제안까지만**, 적용은 사람이 한다.
- 상부 구조는 간섭 계산에만 합성한다(사용자 결정 2026-09-16). 모델·IFC·물량에 넣지 않는다.
- 항목마다 별도 커밋. 커밋 전 `pytest tests` 전체, 프런트 변경 시 `npm test` + `npm run build`,
  식별자 스캔 0건(공개 저장소 — 고객명·경로·레이어명 금지).
- 파서 쪽을 건드리는 항목(C·E)은 **골든이 먼저 잠겨 있는 상태**에서 시작하고 끝에 diff 로 의도한 변화만
  있는지 확인한 뒤 `--bless` 한다.

## 1. 순서와 규모

중요도 순서는 판단문 그대로(높이 → 설정 비용 → 확정 → 빌드 → 검증 → 지식 → 벽)지만, **개발 순서는
작은 diff 로 현장 가치가 바로 나오는 것을 앞에** 둔다.

| 순서 | 항목 | 규모 | 앞에 두는 이유 |
|---|---|---|---|
| A | 높이 근거를 화면 맨 위로 + 평면 z 0 을 근거로 치지 않기 + 선언 유도 | 1일 | 판정의 신뢰도를 사용자가 첫 화면에서 알아야 한다. 새 판정 없음 |
| B | 이음 후보 일괄 확정 | 1일 | `set_bridges` 가 이미 목록을 받는다. 현장 체감이 즉시 |
| C | 장비 본체 제안 도구 | 2일 | 환기유니트·난방 분배기가 여기 막혀 있다. 폭 측정기와 같은 형태 |
| D | 2.5D 검토를 GUI 기본 단계로 + 검토 목록 CSV | 1일 | 일회성 스크립트로 만들던 `clash-detail.csv` 를 정식 출력으로 |
| E | 간섭 줄에 벽 품질 근거 | 반나절 | 데이터가 레코드에 다 있다. 표시만 |
| F | 검증 확장(프런트 순수 함수화 · 배치 왕복 · 골든 절차) | 1일 | A·B 가 만든 화면 문구를 테스트로 잠근다 |
| G | 지식 이관(CLAUDE.md 분할 · 잠기지 않은 절 테스트) | 1~2일 + 지속 | 동작 변경 없음. 마지막에 |

합계 약 8~9 작업일.

---

## A. 높이 근거 — 화면 맨 위 · 평면 z 0 · 선언 유도

### 문제
- 줄마다 `· 높이 근거: 가정(…)` 이 붙지만 목록 **전체가 가정 위에 서 있다는 사실**은 GUI 로그 한 줄뿐이다.
  검토 목록·Pascal 검토 탭 맨 위에는 없다.
- `geom_contract.height_basis` 는 `elevation_source == "source"` 를 **source(도면이 말해 줌)** 로 친다.
  그런데 평면도 DXF 의 z 는 전부 0 이라, `placement: source` 로 둔 규칙은 **덕트를 바닥에 깔아 놓고
  "도면 근거"** 라고 말한다(실측: 덕트 45개 전부 elevation 0 이었을 때 간섭 2건, 제 높이에서 0건).
  지금 표시는 그 줄을 declared 줄과 똑같이 보이게 한다 — 이 항목이 막으려던 바로 그 상황이다.
- 연결 요약(`mep_network.summary`)에는 가정 수가 없다(항목별 `z_basis` 만).

### 있는 것(재사용)
- `clash_review.summary.assumed_basis` · `through_openings_assumed`(clash_review.py:261)
- `mep_network._entry` 의 `z_basis`(mep_network.py:101) · 끊긴 끝 `z_basis`(:302)
- `mep_profile._assign` 의 `elevation_source`(:509) · `source_elevation_mm`(:508) · `placement` 4종(:176)
- `frontend/src/review_logic.js` `assumedHeightText` · `app.js renderReview`(:676)
- `mep_gui._parse_done` 로그(:670)
- `mep_setup_ui` 규칙 폼의 placement 항목

### 만들 것
1. **`geom_contract.height_basis`**: `elevation_source == "source"` 이고 `source_elevation_mm`(없으면
   `elevation`)이 **0** 이면 `datum = "assumed"`, `assumed` 에 `"plan_z"` 를 넣는다. 평면 z 0 은 정보가
   아니라 정보 없음이다. 레거시 파서 경로(`_entity_elevation`)도 같은 함수를 지나므로 같이 잡힌다.
   - 판단 근거를 독스트링에 한 줄: "z 0 을 source 로 치면 바닥에 깔린 덕트가 도면 근거로 나온다".
   - 골든 영향: 난방·환기 프로젝트는 `placement` 가 profile 이라 변화 없어야 한다. 건축 골든은 설비 없음.
     변화가 있으면 그 자체가 발견이다 — diff 를 보고 판단.
2. **`mep_network.analyze` summary**: `assumed_basis: {candidates: n, open_ends: n}`. 항목별 값을 세기만.
3. **`review_logic.reviewBannerText(clashSummary, connectivitySummary)`** → 문자열. 예:
   `간섭 24건 중 가정 높이 24건 · 연결 후보 26건 중 가정 0건 — 설비 설정에서 계통별 설치 높이·규격을
   선언하면 줄어듭니다`. 0건이면 빈 문자열. `app.js renderReview` 가 목록 위 한 줄(`#reviewBanner`)에 넣는다.
   순수 함수라 node:test 로 잠근다(F).
4. **`mep_profile` 진단 `PLAN_Z_AS_ELEVATION`**(warning): 규칙이 `placement: source` 인데 그 규칙의 모든
   원본 z 가 0 이면 한 건. reason: "평면도의 z 0 을 설치 높이로 쓰고 있다 — slab_soffit/center 로 선언할 것".
   `mep_diagnostics.issues` 에 실리므로 MCP `get_mep_diagnostics` 와 골든의 code 별 개수에 자동으로 든다.
5. **`mep_setup_ui`** placement 가 `source` 로 선택되면 폼 아래 한 줄 힌트: "평면 z 를 그대로 씀. 설치
   높이가 아니면 slab_soffit(슬래브 밑면 밀착) 또는 center 를 고르세요". tkinter Label 하나.
6. **`project_server.pascal_review`**: 반환에 `summary: {clash_assumed, gap_assumed}` 추가. 검토 탭
   (`mep-review-tab.tsx`) 목록 위에 같은 배너 문구. 오버레이 `tsc` 0 확인.
7. **`mep_gui._parse_done`** 로그는 이미 있다. 연결 쪽에 `가정 n` 만 덧붙인다.

### 하지 않을 것
- 높이를 채우거나 기본값을 바꾸는 일. `slab_soffit` 자동 전환 없음.
- '가정 근거만 보기' 필터. 배너가 먼저다. 필요해지면 `entry.basis` 로 한 줄이다.

### 테스트
- `tests/test_clash_review.py`: 평면 z 0 덕트(`elevation_source: source`, elevation 0) × 벽 → `basis
  assumed`, `assumed` 에 `plan_z`. 같은 덕트에 `elevation_source: profile` 이면 declared(회귀).
- `tests/test_mep_network.py`: summary `assumed_basis` 두 수.
- `tests/test_mep_profiles.py`: `placement: source` + z 0 원본 → `PLAN_Z_AS_ELEVATION` 1건, `slab_soffit`
  → 0건.
- `tests/preview_review.test.mjs`: `reviewBannerText` 문구 · 0건이면 빈 문자열.
- `pascal_host` 오버레이 테스트(기존 tsc 검사)에 summary 필드 존재.

### 완료 조건
- 단위세대 통합 모델을 열면 배너가 "24건 중 24건" 을 띄운다(실측으로 확인).
- 환기 프로젝트에서 규칙 하나를 `slab_soffit` 로 바꾸면 그 덕트 줄이 declared 로 바뀌는 것을 실측
  (난방 22건은 지름 가정이라 규격 선언까지 있어야 준다 — 그 사실을 배너가 말한다).
- 골든 diff 가 의도한 변화(연결 summary 키 추가 · 진단 code 추가)뿐이면 `--bless`.

### 결과 (완료 — 커밋 `0b5fb0a`)

| 모델 | 배너가 말하는 것 |
|---|---|
| 통합(난방+환기) | 간섭 24건 중 가정 높이 **24건** · 연결 후보 26건 중 가정 **26건** |
| 단위세대 난방 | 간섭 8건 중 가정 8건(전부 벽 하부 통과) · 끊긴 끝 10/10 가정 |
| 단위세대 환기 | 간섭 8건 중 가정 8건(전부 벽 관통) · 후보 26/26 가정 · 끊긴 끝 74/74 가정 |

- 골든 diff 는 **새 진단 두 건뿐**이었다(환기 `PLAN_Z_AS_ELEVATION` 1 · `SOURCE_Z_OUTSIDE_STOREY` 1).
  간섭·연결·커버리지 수치는 하나도 안 움직였다 — 덕트·배관은 프로필 placement 라 종전과 같은 근거다.
- **가정의 원인은 z 가 아니라 단면이었다**: 난방 배관 5개가 `diameter` 가정, 환기 덕트 37개가 단면 가정
  (원형 10 · 사각 27). 폭은 외곽선으로 41/41 맞췄지만 **높이·모양은 평면에 없어** 제품 자료에서 온 값이라
  `dimension_basis: assumed` 가 맞다. 배너를 줄이려면 규격 근거를 사람이 올려야 한다 — 코드가 할 일이 아니다.
- **새로 드러난 사실 둘**(종전에는 아무 데도 안 나왔다):
  - 디퓨저 11개가 `placement: source` 라 **z 0**(바닥)에 서 있다. 천장 디퓨저다.
  - 슬리브 2개의 원본 z 가 **12,357mm · 24,715mm** — 한 층짜리 세대에서 높이가 아니라 도면 작성 흔적이다.
    이건 z 0 이 아니라 계획에 없던 갈래라 `SOURCE_Z_OUTSIDE_STOREY` 를 같은 자리에 하나 더 뒀다.
- 화면 실측(합성 도면, 서버 미리보기): 배너가 목록 위에 뜨고(`#reviewBanner`, hidden 해제) 줄 끝에
  `높이 근거: 가정(height, plan_z)` 이 붙는다. 오버레이 `tsc` 0. `pytest` 628 · `npm test` 28.

---

## B. 이음 후보 일괄 확정

### 문제
후보 26건을 버튼 26번, revision 26번. MCP 도 한 건씩(`confirm_mep_connection`). 난방 끝 10개는 C 가
풀어야 후보가 생긴다.

### 있는 것(재사용)
- `project_store.set_bridges(bridges, …)` — **이미 목록 전체를 받는다**(project_store.py:247).
- `ProjectSession.confirm_bridge`(project_server.py:417): 후보 id 검증 → `kept` 재구성 → `set_bridges`.
- 후보 항목의 `kind` · `size_change` · `systems` · `z_basis`(mep_network.py:93~102). 계통 충돌은 이미
  `conflicts` 로 분리돼 있다.
- `app.js confirmGap(id, confirmed)`(:695) → `POST /bridges` · `review_logic` gap 행의 `confirm` 필드.
- HTTP 라우트 `/bridges`(project_server.py:724).

### 만들 것
1. **`mep_network` 후보에 `routine: bool`** — `kind in ("straight", "elbow") and not size_change`. 규칙은 이
   한 줄이 유일한 자리(상수 `ROUTINE_KINDS`). 티는 제외: 계통 위상과 물량(티 피팅)을 바꾼다. summary 에
   `routine: n`.
   - 실측 예상(환기): 직선 6 + 엘보 16 중 규격 바뀜 9 를 뺀 수. 확인 뒤 표에 적는다.
2. **`ProjectSession.confirm_bridges(candidate_ids, expected_revision, project_id, confirmed=True, reason='')`**:
   `confirm_bridge` 본문을 목록으로 일반화하고 `confirm_bridge` 는 `confirm_bridges([id])` 를 부른다.
   - 전부 검증 후 한 번의 `set_bridges` → **revision 1 증가**. 모르는 id 가 하나라도 있으면 전체 거부
     (부분 적용 없음 — 어느 것이 들어갔는지 아무도 모르게 되지 않게).
   - decision 은 한 건: `{"action": "confirm_bridges", "candidate_ids": [...], "revision": r}`.
3. **HTTP `/bridges`**: 본문에 `candidate_ids` 가 있으면 목록으로, `candidate_id` 는 종전대로. 둘 다 있으면 400.
4. **프런트**: gap 행 위에 버튼 `일상 후보 N건 일괄 확정`(N = `routine` 이고 미확정인 후보 수). 클릭 →
   `confirmGap` 과 같은 경로로 `candidate_ids` 전송 → 돌려받은 상태로 다시 그린다. 요청 본문 생성은
   `review_logic.bridgeRequest(ids, confirmed, revision, projectId)` 순수 함수로(F 가 잠근다).
   독립 HTML(서버 없음)에서는 종전 규약대로 버튼을 그리지 않는다.
   - 확정 취소는 종전대로 한 건씩. 일괄 취소는 만들지 않는다(취소는 드물고 실수 비용이 크다).
5. **MCP `confirm_mep_connections(candidate_ids, expected_revision, json_path, reviewed_by_user, reason)`**:
   `reviewed_by_user` 필수 규약 동일. 기존 단건 도구는 유지.
6. **GUI 로그**: `이음 후보 26(일상 N)`.

### 하지 않을 것
- 자동 확정. `routine` 은 **묶어서 보여 주는 기준**이지 적용 기준이 아니다.
- 티 포함. 가지 이음은 `at_mm` 이 물량을 바꾼다.

### 테스트
- `tests/test_mep_network.py`: `routine` 플래그(직선 같은 규격 → True · 규격 바뀜 → False · 티 → False).
- `tests/test_mep_network.py` 프로젝트: 후보 3건 일괄 → r+1 · `bridges` 3 · 재파싱 후 `applied` 3 ·
  모르는 id 섞으면 전체 거부 + revision 불변 · HTTP `candidate_ids` 왕복 · `candidate_id` 단건 회귀.
- `tests/preview_review.test.mjs`: `bridgeRequest` 본문 · 일괄 버튼 수 텍스트.

### 완료 조건
- 환기 프로젝트에서 일상 후보를 한 번에 확정 → revision 1 증가 · `groups` 가 그만큼 준다(실측 수치 기록).
- 화면 왕복 1회 실측(버튼 → r+1 → '확정한 이음' 행). 이 실측은 F 의 체크리스트 항목이 된다.

### 결과 (완료 — 커밋 `f2ab9e2`)

실측(단위세대 환기): 후보 26 중 **일상 17** — 직선 3 · 엘보 14. 남은 9 는 규격 바뀜 5(직선 3 · 엘보 2)와
티 4 이고, **티 4개는 모두 규격이 함께 바뀐다**(가지가 작은 관으로 갈린다). 난방은 후보 0 이라 변화 없다.
계획의 예상('직선 6 + 엘보 16 에서 규격 바뀜 9 를 뺀 수')과 같은 수다.

화면 왕복(합성 덕트 8조각 · 서버 미리보기): 버튼 '일상 이음 후보 3건 일괄 확정' → **r0 → r1**, 결정 기록은
**한 건**(후보 id 3개) · 무리 8 → 5 · 후보 5 → 2(티 2 만 남고 버튼이 사라진다) · 행 3개가 '확정한 이음' 으로
바뀐다. 그중 하나를 '확정 취소' 하면 다시 후보가 되고 버튼이 '1건' 으로 돌아온다 — **단건 경로는 그대로다.**
`pytest` 631 · `npm test` 29.

한 번은 전체 실행에서 `test_hidden_tk_proposals_only_show_active_source` 가 실패했는데 단독·반복 5회·전체
재실행에서 전부 통과했다. 종전에도 한 번 있었던 tkinter 환경 flake 로 본다 — 재현되면 그때 원인을 판다.

---

## C. 장비 본체 제안 도구

### 문제
`_equipment_outlines`(mep_profile.py:594) 는 한 INSERT 안에서 닫힌 면이 겹치면 `Ambiguous equipment
outline` 으로 저장을 막는다. 맞는 판단이지만 **어느 핸들을 고르라는지 말하지 않는다.** 디퓨저는 사람이
DXF 를 열어 바깥 원 핸들을 찾았고(33 → 11), 환기유니트와 난방 분배기 두 레이어는 아직 못 넣었다. 도면이
바뀌면 핸들도 다시 찾아야 한다.

### 있는 것(재사용)
- `_outlines`(mep_profile.py:525): polygonize · 중첩 링 = 구멍 처리 · 면별 `source_refs`·`footprint_area_mm2`.
- `_equipment_outlines`: INSERT 인스턴스별 그룹 키(`_signature(insert_path)`).
- **`measure_outline_widths`**(mep_profile.py:368) + `split_rule_by_outline_widths` + `ProjectSession.
  measure_outline_widths`(project_server.py:291) + MCP `measure_mep_outline_widths` + 설정 화면
  `_split_by_outline`(mep_setup_ui.py:420) 확인 대화 — **같은 모양의 도구가 이미 하나 있다.** 그 형태를 그대로.
- `source_handles` / `source_refs` / `block_pattern` 필터(규칙 AND 조건).

### 만들 것
1. **`mep_profile.measure_equipment_bodies(dxf_path, row, unit_scale_to_mm=None, *, legacy_units=False,
   region=None)`** — 읽기 전용. 규칙이 고르는 원본을 `measure_outline_widths` 와 같은 선택 경로로 모아
   INSERT 인스턴스별로 `_outlines` 를 돌리고, 인스턴스마다 닫힌 면을 **면적 내림차순**으로 낸다:
   `{instance: insert_path 서명, faces: [{area_mm2, bbox_mm, handles(블록 안 핸들), refs, contains: [작은 면
   번호…]}]}`.
   - **제안 규칙**: 인스턴스 안에서 다른 모든 면을 덮는 면이 정확히 하나면 그 면이 본체 후보. 모든
     인스턴스가 같은 블록 정의(같은 안쪽 핸들 집합)면 `source_handles: [그 핸들]` 하나로 제안. 블록 정의가
     인스턴스마다 다르면 `source_refs`(handle + insert_path) 목록으로 제안.
   - **제안 불가**: 덮는 면이 없거나 둘 이상(겹치되 포함 관계가 아님) → `ambiguous_instances` 에 인스턴스·
     면 목록을 실어 돌려준다. 사람이 고른다.
   - 직접 도형(INSERT 아님)은 인스턴스 하나로 취급.
2. **`split_rule_by_equipment_bodies(row, measurement)`** → 제안이 있으면 원 규칙 + `source_handles`(또는
   `source_refs`) 한 규칙, 없으면 `None` 과 사유. `split_rule_by_outline_widths` 의 거울.
3. **`Ambiguous equipment outline` 메시지**에 인스턴스 서명과 겹친 면의 핸들·면적을 싣는다. 도구를 안 써도
   어디를 보라는지 오류가 말한다.
4. 붙는 곳: `ProjectSession.measure_equipment_bodies(rule, source_id, region_bounds_mm)` · MCP
   `measure_mep_equipment_bodies`(독스트링에 "제안 → `propose_mep_profile` 로 사람이 검토") · 설정 화면
   버튼 `선택 장비 규칙의 본체 고르기`(확인 대화: 인스턴스 수 · 면 목록 · 제안 핸들 · 모호 인스턴스).
5. CLAUDE.md 연결성 절의 "코드가 고를 일이 아니다" 문장 옆에 "제안은 이 도구가 한다" 한 줄.

### 하지 않을 것
- 가장 큰 면을 조용히 고르는 일. 유니트 기호는 바깥에 점검 여유(점선 사각형)를 두는 경우가 있어
  바깥 면이 본체가 아닐 수 있다. 제안에 면적·bbox 를 같이 실어 사람이 판단한다.
- 겹친 면을 합집합으로 채우는 일(중첩 기호 → 물량 부풀림, 실측 33개).

### 테스트(`tests/test_equipment_bodies.py`, 합성 DXF)
- 블록 안 동심원 3개 × INSERT 2개 → 인스턴스 2, 제안 `source_handles` = 바깥 원 핸들 1개, 제안 규칙으로
  `apply_mep_profile` 하면 본체 2 · `EQUIPMENT_OVERLAP` 0.
- 블록 안 겹치되 포함 아닌 사각형 2개 → `ambiguous_instances` 1, 제안 `None`.
- INSERT 두 개가 서로 다른 블록 정의 → `source_refs` 제안.
- `Ambiguous equipment outline` 오류 문자열에 핸들이 들어 있다.

### 완료 조건
- 실무 환기유니트 레이어 · 난방 `EQ`/`SS-EQ` 에 도구를 돌려 결과를 표로 남긴다(제안이 나오는지, 모호한지).
- 제안이 나온 것은 로컬 프로필에 적용(revision 증가) → 난방 끝 10개의 단말 분포 · 환기 유니트 본체 수 실측
  → 골든 diff 확인 후 `--bless`. 모호하면 그 사실을 기록하고 사람이 고른 핸들로 적용.
- 이 항목이 끝나면 미결 "난방 분배기 핸들" 은 자동으로 닫힌다.

---

## D. 2.5D 검토를 GUI 기본 단계로 + 검토 목록 CSV

### 문제
GUI 의 흐름은 `(3) 3D 미리보기 → (4) 3D Build` 다. 그런데 간섭 위치·조치는 2.5D 목록이 0.1초에 내고,
FreeCAD 는 같은 결과를 7분 뒤에 이름과 부피로 낸다. 현장에 보낸 `clash-detail.csv` 는 이번에 손으로 짠
스크립트로 만들었다 — "일회성 작업" 그 자체다.

### 있는 것(재사용)
- `geometry.clash_review.items` · `geometry.mep_connectivity`(candidates · conflicts · open_ends · bridges).
- `boq_export.export_boq_csv`(boq_export.py:309, stdlib csv) — CSV 쓰기 관례(utf-8-sig).
- `mep_gui._do_boq`(:970) 버튼 처리 패턴 · `_parse_done` 의 요약 로그.

### 만들 것
1. **`clash_review.to_rows(review)`** → 고정 열: `id, kind, label, action, level, x_mm, y_mm, z0_mm, z1_mm,
   crossing_mm, struct_eid, struct_category, struct_layer, struct_width_mm, mep_eid, mep_system, mep_size,
   basis, assumed`. **`clash_review.write_csv(path, review)`**(utf-8-sig, Excel 이 바로 연다).
2. **`mep_network.to_rows(connectivity)`** → 후보·충돌·확정·끊긴 끝을 한 표로: `row_type(candidate|conflict|
   bridged|open_end), id, kind, status, routine, gap_mm, x_mm, y_mm, eid_a, eid_b, system, size_a, size_b,
   size_change, z_basis, level`. `write_csv` 동일.
3. **GUI 버튼 `(3b) 검토 목록 CSV`**(파싱 직후 활성): `<프로젝트>/review/clash_r<rev>.csv` ·
   `connectivity_r<rev>.csv` 를 쓰고 경로를 로그. revision 이 파일명에 들어가 어느 상태의 목록인지 남는다.
4. **버튼 이름**: `(4) 3D Build (FreeCAD 기본)` → `(4) 납품 검증 빌드 (FreeCAD · 느림)`. 빌드 시작 로그에
   이전 `build.json` 의 `stage_seconds` 합이 있으면 `이전 빌드 421초` 를 찍는다(예상 시간을 사용자가 안다).
5. **CLI**: `python clash_review.py geometry.json -o clash.csv` (`--connectivity` 로 연결 표).
6. CLAUDE.md 실행 순서에 "일상 검토는 (2)→(3b), 납품 검증만 (4)" 한 단락.

### 하지 않을 것
- FreeCAD 빌드 속도 개선. 남은 큰 구간(IFC 122.8초 · 설비 형상 189초)은 FreeCAD 내부라 여기서 줄일 수단이
  없다. `FACETED_EXPORT` 까지가 이번 범위의 끝이다. 한계로 적는다.
- Excel(openpyxl) 출력. CSV 면 충분하고 openpyxl 은 선택 의존성이다.
- MCP 내보내기 도구. MCP 는 JSON 을 그대로 읽는다.

### 테스트
- `tests/test_clash_review.py`: 합성 geometry → `to_rows` 열 순서·값, `write_csv` 왕복(csv 모듈로 다시
  읽어 첫 줄 = 열 이름, utf-8-sig BOM).
- `tests/test_mep_network.py`: `to_rows` 에 네 row_type 이 모두 나온다(확정 1 · 후보 1 · 충돌 1 · 끝 1 픽스처).
- CLI 스모크(`subprocess`, sample_mep geometry).

### 완료 조건
- 단위세대 통합 프로젝트에서 (3b) 를 눌러 나온 CSV 가 손으로 만든 `clash-detail.csv` 와 같은 24행
  (열은 더 많다). 이후 로컬 스크립트는 지운다.

---

## E. 간섭 줄에 벽 품질 근거

### 문제
간섭 줄의 벽은 `single_offset`(축선 자체가 추정) · `thin_pair` · `manual` 일 수 있는데 줄에는 두께만 있다.
50mm 미만만 `suspect_thin_wall` 로 가른다. "벽이 간섭 품질의 바닥" 이라는 사실이 결과에 안 실린다.

### 있는 것(재사용)
- 벽 레코드의 `pairing` · `review_reason` · `needs_review` · `width_detected` · `confidence`.
- `clash_review._prisms` 가 `rec` 를 들고 있다(clash_review.py:68).
- A 의 배너.

### 만들 것
1. 항목 `struct` 에 `pairing` · `review_reason` · `needs_review`(있을 때만). summary 에
   `on_reviewed_walls: n`(struct 가 `needs_review` 인 줄 수).
2. `review_logic` 간섭 행 문구 끝에 ` · 벽 검토 필요(single_offset)` — `assumedHeightText` 와 같은 자리.
3. A 의 배너 문구에 ` · 검토 필요 벽 위 n건`.
4. D 의 CSV 열에 `struct_pairing, struct_review_reason` 추가.

### 하지 않을 것
- 페어링·허용치 변경. 측정으로 접은 것을 다시 열지 않는다.
- 새 kind. 조치 구분은 형상으로만 가른다는 규칙 유지.

### 테스트
- `tests/test_clash_review.py`: `pairing: single_offset, review_reason: single_offset, needs_review: true`
  벽 × 배관 → 줄에 세 필드 · summary 1. 검토 없는 벽은 키 자체가 없다.
- `preview_review.test.mjs`: 문구.

### 완료 조건
- 통합 모델 24건 중 검토 필요 벽 위의 수를 실측해 REPORT 에 적는다.

---

## F. 검증 확장

### 문제
프런트 테스트 27건은 `review_logic` 순수 함수만 본다. 확정 버튼·Pascal 편집은 손으로 한 번씩 봤다. 새
고객 도면이 오면 골든 등록이 절차로 적혀 있지 않다.

### 만들 것
1. **프런트 순수 함수화**: `app.js` 의 `reviewRowHtml`(:688) · A 의 배너 · B 의 일괄 버튼 HTML · B 의
   `bridgeRequest` 를 `review_logic.js` 로 옮겨 문자열 in/out 으로 만든다. DOM 은 `app.js` 가 `innerHTML`
   로 붙이기만. node:test 로 행 HTML 에 `data-gap`·`data-confirmed` 속성이 있는지, 서버 없는 모드에서
   버튼이 없는지 잠근다. DOM 라이브러리를 넣지 않는다.
2. **HTTP 왕복**: B 의 배치 테스트(파이썬)가 `/bridges` 를 실제 서버로 친다 — 기존 단건 왕복 테스트 옆.
3. **수동 체크리스트**: `docs/upgrade_verification.md` 에 릴리스 전 5항목 — 검토 목록 확정/취소 1회 ·
   일괄 확정 1회 · Pascal 인스펙터 치수 1회(r+1 확인) · GUI (3b) CSV · 납품 빌드 1회(stage_seconds 기록).
   자동화하지 않는 이유(브라우저 구동은 CI 에 없다)를 한 줄로.
4. **골든 절차**: `tests/test_golden.py` 모듈 독스트링과 CLAUDE.md 골든 행에 "새 도면 등록: `golden.local.json`
   에 중립 키로 경로 추가 → `--bless` → 스캔 → 커밋". 설정 화면에서 프로젝트를 저장할 때 로그에
   `골든 등록: python tests/test_golden.py --bless (tests/golden.local.json 에 경로 추가 후)` 한 줄을 찍어
   잊지 않게 한다.

### 하지 않을 것
- Playwright · jsdom 도입. 순수 함수 분리로 얻는 것이 더 많고 의존성이 0 이다.
- 빌드 시간 상한 테스트. `stage_seconds` 는 자기보고이고 기계에 따라 흔들린다.

### 완료 조건
- `npm test` 가 행 HTML · 배너 · 요청 본문을 본다(건수 27 → 35 안팎).
- 체크리스트를 이 계획의 A·B 완료 시점에 한 번 실제로 돈다.

---

## G. 지식 이관

### 문제
CLAUDE.md 1,349줄이 "왜 그렇게 했는지" 를 들고 있다. 테스트로 잠긴 절은 괜찮지만 잠기지 않은 절은
다음 사람이 다시 깨뜨린다. `dxf_parser.py` 3,707줄 · `mep_gui.py` 1,180줄 · `app.js` 1,229줄.

### 만들 것
1. **CLAUDE.md 분할**(동작 변경 0): 규칙 · 금지 사항 · 파일 표 · 실행 순서만 남기고(목표 500줄 아래), "★
   …" 실측 서사는 `docs/decisions/<주제>.md` 로 옮기고 CLAUDE.md 에 한 줄 포인터. 내용은 지우지 않는다
   (git 에 남는다). 한 커밋.
2. **잠기지 않은 절 목록**: `★` 절마다 대응 테스트 이름을 표로(`docs/decisions/README.md`). 없는 것 중
   싼 것부터 테스트를 붙인다. 후보(확인 필요): 블록 이름 폭 300~6000 경계 · `top:` 선언이 계통별 중심을 다르게
   놓는 것 · `ignore` 두 분기 · `single_offset` 을 `pair_max` 로 못 줄인다는 실측(이건 테스트가 아니라
   기록으로 둔다).
3. **큰 파일 분할은 하지 않는다.** 동작 변경이 없는 리팩터링은 이 계획 어디에도 이득이 없다. 어떤 항목이
   그 영역을 고칠 때만(예: B 가 `confirm_bridge` 를 만지면서) 그 함수를 옮길지 판단한다.

### 완료 조건
- CLAUDE.md 줄 수 · 잠기지 않은 절 수를 표로 남긴다. 이후 절을 새로 쓸 때 "테스트 이름 또는 '기록만'" 을
  같이 적는 규칙을 CLAUDE.md 머리에 한 줄.

---

## 2. 항목별 커밋 이름(예정)

| 순서 | 커밋 |
|---|---|
| A | `feat(review): 높이 근거를 목록 맨 위에 — 평면 z 0 은 근거가 아니다` |
| B | `feat(mep): 일상 이음 후보를 한 번에 확정한다 — revision 하나, 전부 아니면 아무것도` |
| C | `feat(profile): 장비 본체를 제안한다 — 고르는 것은 사람` |
| D | `feat(gui): 2.5D 검토 목록을 CSV 로 — 일상 검토는 (3b), 납품 검증만 (4)` |
| E | `feat(clash): 간섭 줄에 벽 페어링 근거` |
| F | `test(review): 행 HTML·배너·요청 본문을 순수 함수로 잠근다` |
| G | `docs: CLAUDE.md 를 규칙과 결정 기록으로 나눈다` |

## 3. 미결 사항(사용자 답 필요)

- 로컬 `models/combined_r2.*` · `combined_r3.*`(각 58MB) 삭제 여부. 계획과 무관하게 답이 있어야 지운다.
- 난방 분배기 본체 핸들: **C 가 끝나면 도구가 제안한다.** 그때까지 보류.

## 4. 이 계획에 넣지 않은 것과 이유

| 제안될 법한 것 | 넣지 않은 이유 |
|---|---|
| 거리로 잇기 · 높이 추정 · `pair_max` 상향 | 셋 다 측정에서 숫자는 좋아지고 결과는 틀렸다 |
| 상부 슬래브·보를 모델에 | 사용자 결정(간섭 계산에만). 보는 평면에 정보가 없다 |
| FreeCAD 빌드 속도 | 남은 구간이 FreeCAD 내부(IFC 직렬화·Arch 후처리). 2.5D 가 일상 검토를 맡는다 |
| Playwright 등 브라우저 자동화 | CI 에 브라우저가 없다. 순수 함수 분리 + 수동 체크리스트 |
| 큰 파일 분할 | 동작 변경 없는 리팩터링. 고치는 김에만 |
| 단면도·입면 입력 | 입력 범위(DXF 평면)를 바꾸는 일이라 별도 결정 |
