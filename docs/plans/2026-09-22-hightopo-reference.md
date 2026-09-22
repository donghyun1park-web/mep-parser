# HighTopo 참조 분석 (2026-09-22)

대상: https://www.hightopo.com/en-index.html (HT for Web — HTML5/WebGL 2D·3D 시각화 SDK)와 사용자가 든 파이프라인 글
https://hightopo.medium.com/how-to-skillfully-utilize-pipelines-in-3d-development-8016235d462b, 그 밖의 가이드·기사·데모 제목(출처는 §7).
질문(사용자): "HighTopo 에서 우리 프로그램이 배울 것들을 리스트해 봐 — 파이프라인 글의 내용이 시각화에 좋지 않을까."

방법: 원문을 열두 기법 묶음(흐름 · 전환 · 카메라 · 성능 · 드릴다운 · 2D↔3D · 알람 · 라벨 · 링크 · 편집기 · CFD · 범위 밖)으로 나눠
우리 코드와 파일:줄로 대조했다. 후보마다 검증자 둘이 코드로 반박했다 — 하나는 "이미 있거나 이미 접었는가", 하나는 "루프에 기여하는가 ·
이 크기·이 의존성으로 되는가"(조사 3 · 대조 12 · 반박 44 · 종합 1, 읽기 전용). 둘 다 반박에 실패한 것만 §2 에 올렸고, 검증을 못 거친
'계통 추적'은 직접 코드를 읽어 확인했다. 그 뒤 §2 의 열한 항목을 두 묶음으로 구현하고 브라우저에서 확인했다(§6). 줄 번호는 2026-09-22 작업 트리 기준.

사용자 목표는 [2026-09-18 계획서](2026-09-18-easy-loop.md) §0 그대로 — **"열기 → 수 초 안에 3D → 클릭하면 이유 → 버튼 하나로 고침 → 간섭·물량이 바로 바뀜."**
형식은 [Planform 대조](2026-09-21-planform-reference.md)와 같다.

---

## 0. 한 줄 결론

파이프라인 글의 간판 기법 셋(관 표면 UV 흐름 · 관 성장 클리핑 · 관을 따라가는 로밍)은 **흐름 방향 데이터와 텍스처**가 있어야 서는데,
우리 geometry.json 에는 유향이 없다 — 코드가 세 곳에서 그렇게 적었다(`construction_rules.py:10`, `geom_contract.py:936`, `mep_profile.py:766`).
방향을 만드는 유일한 입구는 구배 선언(`ratio`+`high_end` 둘 다)이다. 그대로 옮기면 **없는 방향을 그럴듯하게 그리는 화면**이 된다.

가져온 것은 기법이 아니라 HighTopo 의 습관 — **장면이 스스로 문제를 가리키게 한다** — 이다. 그 습관을 우리 데이터(간섭 위치 · 이음 · 계통 이름)로
옮긴 열한 항목을 두 묶음으로 구현했다. 새 vendor 0 · 새 geometry 키 0 이다. HighTopo 가 자랑하는 '2D/3D 공유 데이터모델 · 알람 · 경량 링크'는
우리가 원본 도면 대조 · 높이 근거 자기보고 · 증거로만 잇기로 이미 더 정직하게 한다(§4).

---

## 1. HighTopo 가 무엇인가 (관찰)

- **제품 성격**: 운영 감시 디지털 트윈 SDK. 미리 만든 3D 장면에 실시간 값을 바인딩한다(데이터센터 PUE·온습도·알람, 스마트빌딩 공조·급배수·엘리베이터).
  임계 초과를 색·깜빡임으로 가리키고, 카메라가 장비로 날아가며(`flyTo`), 관을 따라 시점이 이동한다. 편집기는 3D SCADA 화면을 드래그로 조립하는 로우코드 도구다.
- **파이프**(파이프라인 글): `ht.Polyline` 이 관이다. 텍스처 + `shape3d.uv.offset` 을 `startAnim` 으로 계속 증가시키면 표면이 흐른다("material flow 방향 표시에 흔히 쓴다").
  `3d.clip.direction`+`3d.clip.percentage` 0→1 로 관이 자라난다. `getLineOffset(polyline, t)` 로 관 위 점·접선을 얻어 카메라나 모델을 관 위로 옮긴다.
- **형상**(shape·modeling 가이드): thickness · 단면 변 수 · resolution · `repeat.uv.length` · dash. 관·엘보는 cylinder/torus 프리미티브 + CSG, 벽·바닥은 압출. 인스턴싱·LOD 언급 없음.
- **2D/3D 연동**(3D 가이드 · 흐름 예제): DataModel 하나에 2D 토폴로지와 3D 가 묶여 선택·조작이 즉시 양쪽에 반영된다. 흐름 플러그인 둘(dashflow·flow)은 **2D 전용**이다.
- **성능**(토폴로지 성능 · 사전로딩 기사): 줌이 임계 이하면 라벨·아이콘을 숨기고, 연선 2048개를 Shape 하나로 병합하고, 진입 전 모델·텍스처를 선로딩한다.
- **배관의 위치에 대한 태도**(데이터센터 글 FAQ): "배관은 대부분 땅속·벽 속이라 실제 위치가 아니라 감시·알람이 관심" — 장비↔장비 관을 알고리즘이 **생성**한다.
  BIM/IFC 원본은 웹에 무거우니 경량 재모델링을 권한다.
- **데모**(제목만 관찰 가능): 'From Static to Dynamic: The Evolution of 3D Piping' · 'Scene Transition Animation' · 'Explosion Effect' · 'Spatial Annotation' ·
  'Auto Routing' · '3D Pathfinding' · 'Wireframe Building' · 'Ventilation System' · 'HVAC Group' · 'Water Supply Network' · 'Heatmap Airflow' ·
  'Topology Rendering Optimization' · '2D and 3D Connection Effect'.
- **우리와의 차이**: 그들은 *만들어진* 모델 위에 *들어오는* 값을 보여 준다(위치는 상관없다). 우리는 *도면에서* 모델을 *유도*해 간섭·물량을 판정한다(위치가 전부다).
  그래서 그들의 "관을 만들어 잇는다"는 우리에게 형상 날조이고, 그들의 "실시간 알람"은 우리에게 파싱 시점의 정적 판정이다. 배울 것은 그 위에 얹힌 **화면의 예절**이다.

---

## 2. 채택 권고 (우선순위순)

공통: `frontend/src` 를 고치면 `npm run build` 로 `frontend/built/` 를 같은 커밋에(`tests/test_preview_bundle.py` 가 해시 잠금). 순수 함수는 `review_logic.js`,
잠금은 `tests/preview_review.test.mjs`. #1~#8 이 첫 묶음, #9·#10 과 3D 간섭 고리가 두 번째 묶음 — **둘 다 2026-09-22 완료**(§6).

| # | 항목 | HighTopo 근거 | 우리 격차(구현 전, 파일:줄) | 목표 기여 |
|---|---|---|---|---|
| 1 | **간섭 행 클릭 → 부딪힌 구조부재도 같이 켠다** | 알람 장비 하이라이트 · `select.color` — 간섭은 두 부재의 사건 | `buildReviewEntries` 가 `struct` 를 만들지만 `reviewRowHtml` 은 싣지 않았다(소비처 0). 클릭은 MEP 만 `selectEid` | '클릭하면 이유'의 **무엇과** — 평행 이중선 벽에서 어느 벽인지 |
| 2 | **계통 추적 — 고른 배관과 이음으로 이어진 무리를 3D 에서 같이 밝힌다** | 계통 격리 · 로밍 — 우리 판은 경로를 만들지 않고 도면이 이어 그린 것만 되짚는다 | 레코드가 `joints:[{id,port}]` 를 든다(`geom_contract.py:709-714`, 확정 이음은 `mep_network.apply_bridges`). Python 은 union-find 로 무리를 세지만 **개수만** 낸다. 3D 선택은 같은 EID 만 켰다 | 흐름 애니메이션이 답하려던 질문("이 관이 어디까지 이어졌나")에 **정직하게** 답한다 |
| 3 | **입력칸에서 Delete·Ctrl+Z 가 부재를 지우지 않게** | 로우코드 편집기의 단축키 — 키가 지금 어디에 붙어 있는지가 손맛의 전제 | 두 keydown 핸들러가 window 전역이고 `ev.target` 을 안 봤다. 평면에서 벽을 고른 채 인스펙터 폭 칸에서 Delete → 벽 삭제 → 저장까지 갔다 | 폭을 고치려다 벽이 사라지는 '조용히 틀린 모델' 방지 |
| 4 | **평면 탭 ↔ 3D 선택을 한 줄기로** | DataModel 하나에 2D/3D 뷰 · '2D and 3D Connection Effect' | 평면 클릭·분할·결합·끌기 끝은 `sel2` 만 바꾸고 3D 강조 0, 3D 클릭은 `sel2` 를 안 건드렸다. 나란히 보기(2026-09-21)에서 두 화면이 **다른 부재**를 밝혔다 | 나란히 보기가 만든 후속 결함 |
| 5 | **계통·용도를 인스펙터 한 줄로** | 'Ventilation System' · 'HVAC Group' — 계통이 장면의 단위 | 인스펙터에 system/service 0건 — 배관을 눌러도 어느 계통인지 몰랐다. 없으면 '계통 없음' 을 정직하게 | 계통 **색**(§3)보다 먼저 — 이름을 적힌 그대로, 추론 없이 |
| 6 | **간섭 지점을 평면 위 고리로** | 'Spatial Annotation' — 문제를 목록이 아니라 장면 안에서 | `clash.at` 이 어느 화면에서도 형상이 된 적 없다. 평면은 연결만 그렸다 | 고치는 화면(평면)에서 간섭 자리가 보인다 |
| 7 | **저장 뒤 "무엇이 바뀌었나" 한 줄** — 간섭·연결 후보 수의 전/후 | 로우코드 '임계 초과 시 빨간불' — 값이 방금 바뀌었다를 화면이 스스로 알린다 | 저장 응답이 목록을 통째로 바꾸고 이전 값은 버렸다. 저장 표시는 '서버에 저장됨' 뿐 | 루프의 마지막 박자 "간섭·물량이 바로 바뀜"에 소리를 준다 |
| 8 | **검토 행으로 갈 때 보고 있던 각도 유지** | `flyTo(node,{direction})` — 어느 방향에서 볼지를 호출자가 준다 | `frameBox(box, direction)` 에 인자가 있는데 `selectEid` 가 안 넘겨 행마다 아이소로 되돌아갔다 | 평면(Top)으로 보다가 행을 넘겨도 평면 그대로 |
| 9 | **끊긴 끝(open_ends)을 검토 행으로** | '이어졌다/안 이어졌다'가 감시 대상 | `buildReviewEntries` 에 open_ends 없음 — 평면의 점과 배너 숫자뿐이라 3D 로 갈 길이 없었다. 데이터는 완비(`mep_network.py:308-312`) | 끊긴 끝을 누르면 3D 가 **그 끝**으로 간다 |
| 10 | **범례 클릭으로 카테고리 숨김**(벽 치우고 설비 보기) | 'Wireframe Building' · 드릴다운 | `applyEditVisuals` 의 visible 은 삭제 편집만, 범례에 click 0. `selectEid`·`fit` 이 visible 을 읽어 가드가 필요하다 | 벽에 가린 설비를 보는 가장 싼 길(반투명 X-ray 대신) |
| 11 | **3D 간섭 고리** — 검토 목록에 보이는 간섭만 | 'Spatial Annotation' | 간섭 좌표가 3D 에는 형상이 된 적 없다(#6 은 평면만) | 벽을 숨겨도 어디가 부딪혔는지 보인다 |

### 구현한 모양(#1~#8)

- **#1** `reviewRowHtml` 이 `data-struct` 를 싣는다. 클릭은 `selectEid` 가 true 를 돌려준 **뒤** 상대를 청록 빛(emissive)으로 덧칠한다 — 선택이 먼저 전부 지우기 때문이다.
  두 부재를 합친 상자로 카메라를 잡지 않는다(슬래브와 합치면 건물 크기로 빠진다). 합성 슬래브(부재 아님, EID 없음)는 빈칸이라 저절로 무동작.
- **#2** `joinedEids(elements, eid)` — 경로 카테고리의 `joints[].id` 를 공유하는 것을 BFS 로 모은다. 연결 **후보**는 joints 가 아니라 건너지 않는다.
  무리는 emissive 가 아니라 **색을 바꾼다**(분홍) — emissive 는 더하기라 하늘색 배관 위에서 안 보였고, 연두는 트레이 색과 겹쳤다(브라우저에서 둘 다 겪었다).
  인스펙터에 '이 부재 포함 N개' 한 줄. 편집 뒤 3D 를 다시 세워도 같은 함수(`highlight3D`)로 복원된다.
- **#3** `isTypingTarget(target)` — 글자를 받는 칸(텍스트·숫자 입력 · textarea · contentEditable)에서만 단축키가 양보한다. 체크박스·슬라이더·버튼·select 는 글자를 안 받으므로 단축키가 산다.
- **#4** 평면에서 고르면 `markIn3D` 가 3D 를 켜고, 3D 에서 고르면 `follow2D` 가 **평면이 화면에 있을 때만** 같은 부재를 고른다(평면 편집 대상이 아닌 기둥·슬래브면 평면 선택을 비운다).
  `selectEid` 는 재사용하지 않는다 — 단면을 끄고 원본 창을 옮겨 편집 중 화면이 튄다.
- **#5** `systemLineText(rec)` — `overrides` 먼저(mep_network 와 같은 순서), `계통 / 용도` 를 적힌 그대로. 없으면 "계통 없음 — 설비 설정에서 계통을 선언하면 표시됩니다".
- **#6** `clashMarkerSpecs(items)` → 평면에 빨간 **고리**(끊긴 끝의 빨간 **점**과 구분), 가정 높이로 나온 간섭은 옅은 점선. 범례에 "이 파싱 결과의 간섭 지점(실시간 감시 아님)".
- **#7** `reviewCounts` · `changeSummaryText` — "간섭 4 → 3 (−1)", 변화가 없으면 "간섭 4 — 변화 없음", 둘 다 0 이면 아무 말도 하지 않는다(설비 없는 건축 도면에서 저장마다 소음이 되지 않게).
  간섭 계산이 실패하면 서버는 `total:0`+`error` 를 싣는다 — 그걸 0 으로 읽으면 엔진이 죽은 것을 감소로 자랑하므로 "비교 불가" 라고 말한다.
  기준값은 **저장 사이클이 시작될 때**(사용자 동작) 뜬다. 편집 저장은 응답 뒤 남은 수정을 한 번 더 보내므로, 그때는 기준을 비우지 않는다.
- **#8** `selectEid` 의 `frameBox` 에 지금의 시선 방향(`cam.position − controls.target`, 복사본)을 넘긴다. '맞춤'·'평면(Top)' 은 그대로 초기화한다.

### 구현한 모양(#9~#11, 두 번째 묶음)

- **#9** status 가 `open` 인 끝만 행이 된다 — 후보·티(이미 후보 행이 있다)·단말·슬리브(끝날 곳을 찾았다)는 문제가 아니다. 실측: 골든 설비 프로젝트 둘의
  '열림' 끝이 10개와 17개(검토 대상 73·118 옆) — 목록을 묻지 않는다. 자리는 연결 후보 다음, 부재별 검토 앞. 행이 끝의 좌표(`data-at`)를 들고 가서
  클릭하면 부재 전체가 아니라 **그 점 둘레 4m** 로 다가가고 빨간 점을 찍는다(`focusPoint`). 다음 선택에서 점은 지워진다.
- **#10** `legendHtml` — 범례 한 줄이 곧 스위치다(`aria-pressed`, 숨긴 줄은 줄 긋고 옅게, 접혀도 머리에 '숨김 N'). 3D 에서만 숨긴다 — 모델·물량·간섭 계산은 그대로,
  저장하지 않는다. 가드 셋: 클릭 광선은 원래 보이는 것만 집는다 · '맞춤'·'평면(Top)' 은 보이는 것에 맞춘다 · 검토 행이 가리킨 부재가 숨긴 종류면 **그 종류를 다시 보인다**
  (안 그러면 클릭이 조용히 죽는다). 고른 부재의 종류를 숨기면 선택을 푼다.
- **#11** `shownClashItems` — 3D 고리는 검토 목록이 **지금 보여 주는** 간섭만 그린다(층·종류 필터를 바꾸면 따라간다). `THREE.Points` 두 개(가정 높이는 옅게),
  깊이 검사 없이 벽 너머로도 보이고, `meshes` 밖이라 클릭·맞춤·단면 범위에 섞이지 않는다. 단면(위쪽 숨기기)은 고리에도 건다.

---

## 3. 참조하되 하지 말 것

| 항목 | 사유 | 근거 |
|---|---|---|
| **관 표면 UV 흐름 · 흐름 입자 · 대시 흐름 · 화살표 텍스처**(파이프라인 글의 핵심) | **유향 데이터가 없다.** 폴리라인 정점 순서는 그린 순서일 뿐이고 조각을 이을 때 방향을 보장하지 않는다. `service=supply_air` 에서 방향을 끌어내면 '계통→용도→물리 방향' 2단 추정이다. 텍스처도 시간축도 없다(단색 Lambert). **대신** #2 계통 추적 + #5 계통 한 줄. 구배를 **선언한** 경로에만 `path3d` 의 z 차이로 평면 내리막 화살표 — 선언 도면이 생기면(~25줄). 사용자 결정(2026-09-22): 흐름 애니메이션은 하지 않는다 | `construction_rules.py:10`, 계획서 §14 "계통 이름→용도 추정 금지" |
| 관 로밍 · 카메라 트윈(flyTo 애니메이션) · 장면 전환 | '시점 투어'는 접힌 항목이다. 미리보기는 경사·수직 구간을 평균 높이로 평평하게 그리므로 관을 따라 날면 **가짜 경로**를 설득력 있게 보여 준다. 트윈은 정보 0비트이고 첫 화면을 늦춘다 | Planform 대조 §3, `app.js` 의 `pathIsSloped` 경고 |
| 관 성장 · 등장 순차 · 분해도 · 확산 링·회전·부유 · 깜빡임 | '열기 → 수 초 안에 3D'를 **늘리기만** 한다. 깜빡임은 "지금 이 순간 임계 초과"라는 뜻인데 우리 판정은 파싱 시점 고정 — 화면이 없는 것(라이브)을 주장한다 | 계획서 §0·§14 |
| 3D 안 텍스트 라벨 · 호버 툴팁 · CSS2DRenderer · 줌 임계 라벨 숨김 | 3D 에 글자가 0개라 숨길 대상이 없다. 상시 패널이 클릭 1회에 EID·규격·근거를 채운다(빠져 있던 것은 계통뿐 → #5). vendor 에 CSS2DRenderer 가 없다. '높이 근거' 를 툴팁에 쓰려면 `height_basis` 를 JS 로 다시 써야 한다("이 표를 다시 구현하지 말 것") | CLAUDE.md z 기준면 절, Planform #14 '낮음' |
| 계통별 색(3D·2D) | 실측: 골든 설비 프로젝트 둘의 선언 계통 수가 1개와 2개 — 색으로 가를 만큼 많지 않다. 평면의 인라인 stroke 는 선택·검토 표시 규칙(`.sel/.rev/.man/.del`)을 이겨 그것을 죽인다. 계통이 여럿인 도면이 생기면 계통 격리 select 한 줄 | #5 로 대체 |
| 장비↔장비 배관 자동 생성 · Auto Routing · 거리로 잇기 | HighTopo 는 위치가 상관없어 관을 만들어도 되지만 우리는 위치가 곧 간섭·물량이다. 같은 도면에서 재어 보고 접었다 | [mep-connectivity.md](../decisions/mep-connectivity.md) '피팅 폴리곤·거리로 잇기' |
| 실시간 센서·임계값 알람·온습도 히트맵·CFD 기류 3D | 입력은 DXF 하나다. CFD 는 WSL·분 단위이고 보고서는 PNG 와 요약 숫자만 낸다 — 납품 뒤 도구 | CLAUDE.md 범위, 계획서 §0 "분 단위는 납품 버튼 뒤로" |
| 구조체 X-ray(반투명) | 가림 해제는 이미 둘(와이어 · 수평 단면)이다. 반투명 벽은 클릭 광선에 투명도 필터가 없어 **클릭을 계속 가로챈다** — '클릭하면 이유'를 깬다. 같은 목적은 #10 숨김이 더 싸다 | `app.js` 클릭 핸들러 |
| 3D 층 필터 · 층 분리 슬라이더 | 층 선택기가 이미 셋(평면 · 원본 · 검토 목록)이다. Planform #13 '낮음' 유지 | Planform 대조 §2 |
| 성능(줌 부분 갱신 · SVG 경로 병합 · InstancedMesh · 재질 공유 · 사전로딩 진행률) | 측정이 없다. 병목은 서버 파싱이다(계획서 §1.2). 재질 공유는 부재별 강조를 깬다. 진행률은 인라인 단일 HTML 이라 분모가 없다 | 계획서 §14 "필요가 측정되면" |
| BIM/IFC 원본을 브라우저로 · Gaussian Splatting · 모바일 O&M | HighTopo 가 권하는 '경량 재모델링' 자리를 geometry.json 이 이미 차지했다. 스플랫은 사진 입력이라 범위 밖 | CLAUDE.md 범위 |

---

## 4. 우리가 이미 더 잘하는 것

1. **실경로·실단면이 정본** — HighTopo 는 관을 생성하지만 우리는 `route_points`/`mep_section` 이 정본이고 위치가 곧 판정이다. 간섭 행이 위치·z·상대 부재·조치문을 든다(`clash_review.py:277-301`).
2. **높이 근거 자기보고** — 간섭·후보·끊긴 끝마다 `basis`/`z_basis`, 목록 맨 위 한 줄(`reviewBannerText`). HighTopo 의 알람은 그 값이 가정인지 말하지 않는다.
3. **연결은 증거로만, 확정은 사람이** — joint id 공유만 연결, 후보는 모델을 안 바꾸고 `/bridges` 로 확정, 일상 후보는 버튼 하나. Auto Routing 의 반대편이다.
4. **편집 → 저장마다 간섭·연결·물량 재계산** — HighTopo 는 편집기와 감시가 다른 제품이다.
5. **원본 도면 대조** — 3D 클릭이 원본 DXF 창의 같은 부재를 밝히고 원본 클릭이 3D 를 고른다. '2D/3D 공유 모델'을 **원본 도면** 수준에서 한다(빠져 있던 평면 탭 한 쌍은 #4).
6. **클릭하면 이유** — `whyHtml` 이 실측≠선언 두께·추정치·일람표 미매칭·끝점 간격을 조치문으로 낸다.
7. **오프라인 단일 HTML · 새 vendor 0** — HighTopo 의 사전로딩·Service Worker 문제가 생기지 않는다.
8. **경량 재모델링** — HighTopo FAQ 가 권하는 그것이 geometry.json 자체다(입력 DXF 만, 결정론).

---

## 5. 덤으로 드러난 것

- **(고침) 저장할 때마다 물량 패널 머리가 '층고: 미선언' 으로 뒤집혔다.** 첫 화면은 `preview.py` 가 `level_height_declared` 로 옮겨 주지만 저장 응답의 geometry 는
  파서 출력 그대로라 `level_height_overrode` 만 있다 — 앞엣것만 읽었다. 두 모양을 `levelHeightDeclared(geometry)` 한 곳에서 읽는다. 이번 묶음의 브라우저 QA 에서 찾았다.
- `clash_review.py:298` 은 계통을 `rec.system` 먼저 읽고, `mep_network.py:172` 는 `overrides.system` 먼저 읽는다 — 편집한 계통이 간섭 행과 연결 요약에서 다르게 보일 수 있다.
  지금 계통을 편집하는 화면이 없어 드러나지 않는다. 계통 편집을 붙일 때 같이 맞춘다.
- `render2` 가 캐시된 원본 배경을 매번 떼었다가 다시 붙인다 — 주석의 의도와 반대다. 느리다는 관찰이 생기면 ~6줄.
- `mep_network` 의 `summary.by_system` 은 만들고 프론트 소비처가 0 이다(`entry.struct` 는 #1 이 살렸다).
- `reviewRowHtml` 이 `entry.key` 를 DOM 에 싣지 않는다 — 3D 마커→행 스크롤을 하려면 `data-key` 가 먼저다.
- 3D 에서 끌어 돌린 뒤 놓은 자리에 부재가 있으면 그 부재가 선택된다(브라우저 click 은 끌기 뒤에도 뜬다) — 기존 동작, 이번엔 손대지 않았다.

---

## 6. 진행

1. ~~**첫 묶음(리빌드 1회)**: #1 상대 강조 → #2 계통 추적 → #3 키 가드 → #4 선택 한 줄기 → #5 계통 한 줄 → #6 간섭 고리 → #7 변화 한 줄 → #8 시선 유지.~~
   **2026-09-22 완료.** 경위·잠근 테스트는 [edits-and-preview.md](../decisions/edits-and-preview.md) '장면이 문제를 가리킨다'.
   브라우저 QA(`sample_mep.dxf` 에 벽 평행선 두 쌍을 더한 합성 도면, 층고 3000 선언, 배관 레이어에 `service=` 선언)에서 확인한 것:
   간섭 행 → 배관 + 벽 청록 · 이어진 배관 분홍 · 평면(Top) 유지 · 평면 클릭 ↔ 3D 강조 양방향 · 폭 칸 Delete 로 안 지워짐(칸 밖 Delete 는 지움) ·
   "간섭 4 → 3 (−1)" / "3 → 4 (+1)" / "간섭 4 — 변화 없음" · 평면에 간섭 고리 4개 · 분할 · 콘솔 에러 0.
   QA 가 단위 테스트를 통과한 결함 셋을 잡았다 — 무리 강조가 하늘색 배관 위에서 안 보이던 것(emissive → 색), 연두가 트레이 색과 겹치던 것(→ 분홍), 위 §5 의 층고 표시.
2. ~~**두 번째 묶음**: #9 끊긴 끝 행 → #10 범례 숨김 → #11 3D 간섭 고리.~~ **2026-09-22 완료**(사용자 요청으로 첫 묶음 직후).
   같은 합성 도면에서 확인: 끊긴 끝 7행 · 행 클릭 → 그 끝으로 카메라 + 빨간 점 · 벽·슬래브 숨김 → '맞춤' 이 설비에 맞음 · 배관을 숨긴 채 배관 간섭 행 클릭 → 배관이 다시 보임 ·
   '끊긴 끝' 필터 → 3D 고리 사라짐 · 저장 뒤에도 숨김 유지 · 단면 켜고 끄기 · 콘솔 에러 0. 인스펙터의 긴 값 옆에서 이름이 두 줄로 꺾이던 것을 고쳤다.
   덤: '수정 적용' 이 손대지 않은 높이 칸까지 `overrides.elevation` 으로 저장해 가정 높이가 '선언' 으로 바뀐다 — 첫 묶음 이전부터 있던 동작, 별도 작업으로 분리했다.
3. **필요가 보이면**: 간섭 행도 **지점**으로 카메라(#9 의 `data-at`·`focusPoint` 를 그대로 쓰면 된다 — 지금은 배관 전체를 잡는다) · 계통 격리 select(계통이 여럿인 도면이 생기면) ·
   '이전 시점' 버튼(OrbitControls `saveState/reset`) · 구배 선언 경로의 평면 내리막 화살표(선언 도면이 생기면).
4. Planform 잔여(GUI·문서 묶음)는 이 문서와 무관하게 그대로.

---

## 7. 근거

- HighTopo:
  파이프라인 글(https://hightopo.medium.com/how-to-skillfully-utilize-pipelines-in-3d-development-8016235d462b, 같은 내용 https://dev.to/hightopo/create-innovative-effects-with-pipelines-in-3d-development-3ic8) ·
  Shape 가이드(https://hightopo.com/guide/guide-en/core/shape/ht-shape-guide.html) · 3D 가이드(https://www.hightopo.com/guide/guide-en/core/3d/ht-3d-guide.html) ·
  Dashflow(https://www.hightopo.com/guide/guide-en/plugin/dashflow/ht-dashflow-guide.html) · Flow(https://www.hightopo.com/guide/guide-en/plugin/flow/ht-flow-guide.html) ·
  Modeling(https://www.hightopo.com/guide/guide-en/plugin/modeling/ht-modeling-guide.html) ·
  데이터센터(https://hightopo.medium.com/3d-visualization-empower-data-center-management-e202c7e6f6db) · 스마트빌딩(https://dev.to/hightopo/digital-twins-in-smart-buildings-231f) ·
  스마트시티(https://hightopo.medium.com/building-3d-smart-city-based-on-html5-webgl-f680d187cef1) · 로우코드(https://hightopo.medium.com/hightopo-low-code-3d-visualization-development-platform-cbc84d6b44f4) ·
  토폴로지 성능(https://segmentfault.com/a/1190000048096180) · 사전로딩(https://hightopo.medium.com/optimizing-user-experience-through-3d-resource-preloading-9940e730e049) ·
  프레임 애니메이션(https://hightopo.medium.com/frame-animation-3d-dynamic-rendering-design-and-implementation-7ca4094dd195) ·
  flyTo 예제(https://www.mo4tech.com/build-production-control-system-based-on-3d-rendering-engine-of-html5-canvas.html) · 3D 흐름 예제(https://www.programmersought.com/article/71454407694/) ·
  데모 목록(https://www.hightopo.com/demos/en-index.html — 제목만).
- 우리 코드: `frontend/src/{app.js,review_logic.js,shell.html,style.css}`, `vendor/edit_geometry.js`, `clash_review.py`, `mep_network.py`, `geom_contract.py`, `mep_profile.py`,
  `construction_rules.py`, `preview.py`, `project_server.py`, `tests/preview_review.test.mjs`, `docs/decisions/{README,edits-and-preview,clash-review,mep-connectivity}.md`,
  `docs/plans/2026-09-18-easy-loop.md`, `docs/plans/2026-09-21-planform-reference.md`.
