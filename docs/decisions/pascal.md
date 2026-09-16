# Pascal 다리와 편집 화면 호스트

Pascal 씬 그래프와 주고받는 규약, 편집 화면 호스트의 저장 기준.

이 파일은 **왜 그렇게 했는지**의 기록이다. 상시 규약(z 기준면·`opts` 키·실행 순서)은 `CLAUDE.md` 에 있다.

### ★ Pascal 다리 — 위험은 형상이 아니라 **단위·축·고저**다
`pascal_bridge.py`. [pascalorg/editor](https://github.com/pascalorg/editor) 의 씬 그래프
(`{nodes, rootNodeIds}`)와 부재를 주고받는다. 모형은 우리와 같다(축선/폴리곤 + 치수).
다른 것이 셋이고, **셋 다 틀려도 모델은 열린다** — 1000배 작은 건물도, 90도 누운 덕트도,
한 층 내려앉은 벽도. 그래서 좌표로 잰다.

| | 우리 | Pascal |
|---|---|---|
| 단위 | mm | **m**, 덕트·배관 지름만 **인치** |
| 축 | Z-up, 평면 (x, y) | **Y-up** 오른손·북쪽 **−Z** — 평면은 첫째·**셋째(= −y)**, 둘째가 높이 |
| 고저 | 부재의 `z_base`/`elevation` | **레벨**의 `level`·`baseElevation`·`height` |
| 벽 | 다점 축선 · 닫힌 폴리곤 | `start`→`end` **한 구간** |
| 기둥 | 닫힌 폴리곤 | 중심 + 폭·깊이 + **회전** |

환산은 `geom_contract.mm_to_m`/`m_to_mm`/`mm_to_in`/`in_to_mm` 뿐이다. 다리 안에서
`/1000`·`/25.4` 를 쓰지 말 것 — z 규약을 한 곳에 둔 것과 같은 이유다.

★ **셋째 성분은 −y 다 — y 를 그대로 넣으면 건물이 거울상으로 선다.** Pascal 은 Y-up **오른손**
좌표계이고 북쪽이 **world −Z** 다(`packages/mcp/README.md` "Coordinate conventions" · 평면 패널도
`toSvgY(z) = z` 로 +Z 를 화면 아래로 그린다). (x, y, z) → (x, z, y) 는 행렬식 −1 인 **반사**라 종전
다리로 보낸 모델은 좌우가 뒤집히고 북쪽이 아래로 갔다 — 좌표 왕복·명령 수·검사가 **전부 통과**하는
종류라 방향으로 잰다(`test_the_bridge_is_a_rotation_not_a_mirror`: 반시계 L자 슬래브가 위에서 본
화면 좌표 (X, −Z) 에서도 반시계). 지금은 (x, y, z) → (x, z, −y) **회전**이고 `_to_plan`/`_from_plan`
하나로 옮긴다.
- 기둥 `rotation` 은 Three.js `rotation.y` 라 로컬 +X 가 평면 수치 (cos, −sin) 으로 간다
  (`column/floorplan.ts`: "plots at -rotation") — z = −y 에서는 우리 각도가 **그대로** rotation 이다.
- 개구부 `position[2]` 는 벽 로컬 +Z 오프셋이고, 그 축은 우리 왼쪽 법선의 **반대**라 부호가 뒤집힌다.
- 설비 단면: 회전은 외적을 보존하므로 `section.ts` 가 `geom_contract.section_axes` 식을 **그대로**
  쓴다(반사일 때는 외적 순서를 뒤집어 맞췄었다). 같은 roll 값이 편집 화면과 빌더에서 같은 단면이다.
- 이미 떠 있던 편집 화면이 옛 방향 씬으로 저장하면 전부 '이동' 으로 읽힐 수 있지만, 다리가 바뀌면
  스냅샷 해시가 달라져 409(`SnapshotConflict`)로 막힌다 — 새로고침하면 된다.

**카테고리 대응**

| 우리 | Pascal 노드 | 비고 |
|---|---|---|
| wall | `wall` | 닫힌 **직사각형**은 축선±두께/2 로 무손실. 사다리꼴은 불가 |
| column | `column` | 직사각형 → 중심·폭·깊이·회전. 원형 → `round`+radius |
| slab | `slab` | `elevation` 이 **상단**, 두께는 아래로 — 우리 z 규약과 **같다** |
| zone | `zone` | 폴리곤 + `ceilingHeight`. **고저 필드가 없다**(레벨면에 붙는다) |
| duct · pipe · tray | `mep-parser:duct` · `pipe` · `tray` (**전용 플러그인 노드**) | `path` 는 [x, **높이**, 평면y] m, 단면은 **mm** · 모양 · roll · 계통 · 재질(아래). 기본 `duct-segment`·`pipe-segment`(인치)는 사람이 Pascal 도구로 그렸을 때만 **읽는다** |
| **beam** | **없음** | Pascal 자신의 IFC 임포터도 *"Pascal has no `beam` node type yet"* 이라며 건너뛴다 |
| **equipment** | (부적합) | `hvac-equipment` 는 furnace\|air-handler\|condenser **캐비닛**이고 치수가 0.3~2m 로 묶여 있다 |
| opening | `door` / `window` | **벽의 자식**이고 좌표가 벽 로컬이다(아래) |

- `baseElevation` 은 **절대 고저가 아니라 누적 위에 더하는 오프셋**이다
  (`storey.ts: baseY_i = (직전 baseY + 직전 height) + baseElevation_i`). 절대값으로 착각하면
  2층부터 조용히 내려앉는다. `_levels_from_z`/`_z_of_levels` 가 그 식을 그대로 뒤집는다.
- **레벨은 구조 카테고리(wall·column·slab·zone)의 z 로만 만든다.** 덕트의 `elevation`
  2590mm 는 2.59m 짜리 **층**이 아니라 바닥 위 높이다 — 층으로 세면 건물에 없는 층이
  생기고 그리로 부재가 딸려 간다.
- **좌표는 `metadata` 에 넣지 않는다.** 넣으면 왕복이 통과해도 컨테이너가 통과한 것이지
  변환이 통과한 게 아니다. `metadata.mep` 은 출처만(eid·layer·pairing·검토사유).
- 노드 id 는 eid 의 sha1 앞 16자다. Pascal 은 난수(nanoid)를 쓰지만, 같은 도면을 두 번
  변환하면 같은 씬이어야 diff 가 의미를 갖는다.
- Pascal 에서 고친 치수는 `overrides` 로 돌아온다 — "적어 준 값이 이긴다" 와 같은 자리.

★ **개구부는 벽 로컬 좌표다.** `position` = [벽 시작점부터의 거리 · 바닥 위 **중심**
높이 · 벽 중심면에서의 오프셋]. 문은 `height/2`(바닥), 창은 `sill + height/2` 인데
**같은 식**이라 나누지 않는다. 둘째·셋째 성분을 0 으로 두고 싶어지는데 둘 다 실측값이
있다 — 개구부 중심은 벽 축선 위에 있지 않고(실측 중앙 100mm · 최대 400mm), 실무 도면은
문을 벽 마구리 **밖**에 걸쳐 그린다(실측 29개 중 11개 · 최대 610mm). 버리면 그만큼
조용히 옮겨진다. u 를 구간 안으로 **자르지 않고** 밖이면 `opening_past_wall_end` 로 센다.

★ **subtype 이 없으면 추측하지 않는다.** 실측 지하3층의 개구부 34개는 전부 subtype 이
없다(문인지 창인지 도면이 말해 주지 않는다). Pascal 의 `openingKind:'opening'` 이
정확히 **틀 없는 구멍**이라 그대로 담긴다 — sill 로 문/창을 추정할 필요가 없다.
개구부의 `points`(원본 문짝 기호 도형)는 중심·폭으로 되살릴 수 없어 버리고 센다
(`opening_points_dropped`). 빌더는 center/width/height/sill 만 쓰므로 형상에는 영향이 없다.
호스트가 여럿이면(실무 도면은 같은 문을 레이어마다 그린다) 첫 벽에만 붙이고
`opening_extra_hosts_dropped` 로 센다 — Pascal 의 개구부는 벽 하나의 자식이다.

★ **Pascal 의 기본 MEP 노드는 미국 주택 규격이라 우리 설비가 하나도 안 들어간다.**
`pipe-segment` 는 DWV(배수·통기) 전용이고 지름 1.25~8인치, `duct-segment` 는 높이
3인치 하한이다. 실측: PB 난방관 **15.9mm(0.63인치)** 5경로와 **110×54mm** 환기덕트
41경로가 **전부 0개**가 됐다. 범위에 맞춰 값을 깎으면 다른 크기의 설비가 서므로
그것도 답이 아니다.

→ 모든 설비(배관·덕트·트레이)를 **전용 노드** `mep-parser:<카테고리>` 로 내보낸다. 편집 화면
호스트가 그 종류를 Pascal 플러그인으로 등록해(`overlay/apps/editor/lib/mep-plugin/`, 씬의
`installedPlugins: ["mep-parser:mep"]`) 3D·평면에 그리고 인스펙터에서 고친다. 필드는 `path`
(native 와 같은 좌표: m·Y-up·레벨 로컬) + `shape`(round|rect) · `diameterMm` 또는 `widthMm`·
`heightMm` · `sectionRoll` · `system` · `material` — 단면은 **mm 그대로**다. 플러그인이 없는
Pascal 에서도 씬 스키마가 모르는 타입을 `ForeignNodeEnvelope`(BaseNode + `.loose()`)로 통째로
저장하므로(`apps/editor/lib/graph-schema.ts`) 저장은 무손실이고, 플러그인 전의 foreign 노드
(`section` 에 mm)도 계속 읽는다. **구조 노드에는 이 대안을 두지 않는다** — 거기서는 범위
초과가 데이터 오류지 규격 차이가 아니다.

★ **플러그인 단면은 Python 계약을 옮긴 것이다 — 따로 설계하지 않는다.** `section.ts` 는
`geom_contract.section_axes`·`rect_rings`·`rect_parts` 의 TS 이식이다(다리의 (x,y,z)→(x,z,−y) 는
회전이라 외적이 보존돼 식이 그대로다). `test_plugin_section_rings_match_the_python_contract_under_the_axis_swap` 이
Node 로 돌려 네 경로(조각 1·1·3·1)의 링을 1e-9 m 로 대조한다 — 화면의 덕트와 Blender·
FreeCAD 의 덕트가 같은 단면 방향이어야 화면을 믿을 수 있다.

★ **Pascal 도구로 새로 그린 노드는 수동 레코드로 저장한다.** 종전엔 레코드를 `metadata` 의
출처로만 만들어, 새로 그린 노드는 EID 가 없어 `scene_to_edits` 가 건너뛰었다 — **화면에
그렸는데 저장되지 않았다.** 출처 키(`metadata.mep`)가 **아예 없는** 노드가 새로 그린 것이다
(우리가 내보낸 노드는 출처가 비어도 키가 있다). `base()` 가 `wm:` EID(부모 또는 같은 부모 아래
부재의 층 접두 승계)와 `pairing="manual"` 을 붙이고 원본 출처는 만들어 넣지 않는다. 기본
도구의 원형 덕트(인치)는 우리 원형 덕트(mm)로, 타원 덕트는 사각으로 받고 `oval_duct_as_rect`
로 센다. 인스펙터에서 바꾼 모양·치수·roll·계통·재질은 `overrides` 로 돌아오고, **모양이 바뀌면
새 모양의 치수를 함께** 선언한다(모양만 있는 선언은 쓸 수 없다).

★ **편집 결과는 전체 JSON 되돌리기로 저장하지 않는다.** 그러면 화면에 없는 것이
전부 삭제로 보이고(변환 못 한 사다리꼴 벽·장비까지), 동시 수정·되돌리기가
revision 검사를 지나가지 못한다. `scene_to_edits(geometry, scene)` 가 **원본과
대조해 변경 명령**을 내고 — 어휘는 이미 있는 `edits.json` 것 그대로(`deleted` ·
`overrides` · `added`+`record`) — `project_server.save` 의 revision·검증·잠금을 탄다.

| 화면에서 한 일 | 나오는 명령 |
|---|---|
| 노드를 지웠다 | `{"deleted": true}` — **그 노드가 씬에서 실제로 사라졌을 때만** |
| 옮겼다·모양을 바꿨다 | `deleted` + 새 EID(`wm:`)로 `added` (새 동사를 만들지 않는다) |
| 치수만 고쳤다 | `{"overrides": {...}}` — 최상위에 사는 치수(`diameter` 등)도 포함 |
| 위아래로 옮겼다·층을 옮겼다 | `{"overrides": {"elevation"/"z_base": ...}}` — EID 를 유지한다 |
| 애초에 못 보낸 부재 | **없음** — 되돌리기가 못 만든 것은 삭제가 아니다 |

★ **'움직였는가' 는 왕복이 충실히 나르는 기하만 비교한다.** 열린 벽의 `points` 는
원본 면선 한 줄이라 되돌리기가 축선으로 채우는데, 그걸 비교하던 동안 **손대지 않은
페어링 벽이 전부 '이동' 으로 읽혀** 저장할 때마다 delete+add 가 쏟아질 뻔했다
(실제 파싱 벽 1개로 재현 — 합성 벽은 `points == centerline` 이라 못 잡는다).
`_same_geometry` 는 열린 벽은 축선, 닫힌 형상은 **꼭짓점 집합**(시작점이 회전한다),
MEP 는 평면 점 + `path3d` 를 본다. 고저와 단면은 **해소한 값끼리**(`base_z`·
`mep_dimensions`) 비교해 바뀌면 선언으로 낸다 — `overrides` 만 비교하면 실무 난방 도면처럼
지름이 최상위에만 있는 레코드의 변경과, 덕트를 천장에 붙이려 올린 것이 사라진다.

**저장 경로 — 스냅샷 조회·변경 적용은 한 곳이다.** `ProjectSession.pascal_snapshot()` /
`pascal_apply()` 를 HTTP(`GET /pascal/snapshot` · `POST /pascal/apply`)와 MCP
(`get_pascal_snapshot` · `apply_pascal_scene`)가 같이 부른다. 적용 요청은 네 가지를 싣는다:

| 필드 | 막는 것 |
|---|---|
| `expected_revision` + `project_id` | 다른 창·Codex 가 먼저 저장한 뒤의 낡은 수정(409) |
| `snapshot_sha256` | revision 은 같아도 파서·다리·설정이 바뀌어 **다른 씬을 보고 고친** 수정(409, `SnapshotConflict`) |
| `op_id` | 재시도·이중 클릭의 **중복 적용**. `decisions` 에 기록되고 revision 검사보다 **먼저** 본다 — 그래야 둘째 요청이 409 로 첫 요청의 성공을 가리지 않는다 |
| `dry_run` | 저장 없이 명령·파싱 검증만(MCP 는 기본 `True` — 사람이 보고 적용한다) |

손대지 않은 씬은 **revision 을 올리지 않는다**(`no_changes`) — 빈 커밋이 이력을 흐린다.
사람이 만든 레코드(`added`)를 지우면 그 수정 자체가 없어진다(`merge_edits`).

실측(명령 수): 지하3층·환기평면·실무 난방(PB 15.9mm 5경로) 모두 **손대지 않으면 0 · 하나 고치면 1**,
덕트 100mm 올림 → `overrides.elevation` 1개. 다층 프로젝트의 수동 EID 는 원본의
층 접두를 물려받는다(`1F:wm:…`) — 없으면 `edits_to_local` 이 저장을 거부한다.

★ **수직·경사 구간은 점마다 높이가 다르다.** `elevation` 한 값으로 담으면 입상관이
천장에 눕는다. 다리는 계약 v3 `path3d` 에 높이 변화가 있으면 경로 샘플(`route_points`, 현오차
`PASCAL_CHORD_MM`)을 점마다 싣고, 돌아올 때는 elevation 기준 **상대 높이의 직선 구간**으로
담는다. 평면 경로는 종전 그대로 `points` 를 한 높이에 싣고 그 키를 만들어 붙이지 않는다.
모양 비교도 상대 샘플로 해서, 전체를 올린 것은 `overrides.elevation` 하나로 남는다.

★ **이어진 조각만 한 벽이다.** Pascal 에서 꺾인 벽의 가운데 구간을 지우면 남은
조각이 떨어져 있는데, 그대로 이으면 **도면에 없던 대각선 벽**이 생긴다(실측: 3구간
벽의 가운데를 지우자 `[3000,0]→[6000,3000]`). `WALL_JOIN_TOL_MM` 안에서만 잇고,
갈라져 나온 조각은 저장소 규약대로 **수동 레코드**(`wm:` 접두 · `pairing="manual"`)가
된다 — 원본 EID 를 나눠 가지면 수정 사이드카가 어느 쪽을 가리키는지 알 수 없다.
`wall_split_by_deletion` 으로 자기보고한다.

★ **개구부는 자기가 붙은 구간의 축선으로 되살린다.** 대표 조각만 기억하면 다구간
벽의 **둘째 구간에 붙은 개구부가 호스트를 못 찾고 항상 사라진다**(실측으로 재현).
호스트가 아예 없어졌으면 만들 수 없지만 **조용히 버리지도 않는다** —
`dropped.opening_host_missing` 으로 센다.

★ **벽을 지우면 그 개구부는 삭제가 아니라 연결 해제다.** Pascal 은 벽(구간)을 지울 때 자식인 문·창을
함께 지운다. 그걸 삭제 명령으로 받으면 사람이 지우지 않은 개구부가 사라진다 — `scene_to_edits` 는
**호스트 벽 노드도 함께 사라진** 개구부에는 명령을 내지 않고 `opening_unlinked` 로 센다. 개구부 레코드는
남고, 재파싱의 `link_openings_to_walls` 가 붙일 벽을 다시 찾거나(가운데 구간만 지웠다면 남은 조각)
못 찾으면 `no_host_reason` 을 적어 V106·`unconvertible` 로 검토 목록에 뜬다. 벽이 남아 있는데 개구부만
사라졌으면 그것은 삭제다. (벽이 하나도 없으면 링크가 사유를 안 적고 돌아가던 것을 고쳐
`no_walls_to_check` 가 붙는다.)

★ **왕복 검사는 '되돌아오지 않은 것' 을 건너뛰지 않는다.** 종전엔 `if b is None:
continue` 라 조용히 사라진 부재가 검사를 그냥 통과했다(위 개구부 소실이 정확히
그렇게 숨어 있었다). 지금은 되돌아오지 않으면 `unconvertible` 에 있는지 확인하고,
없으면 실패한다.

★ **치수·고저를 최상위에만 쓰면 낡은 선언에 가려진다.** `mep_dimensions` 와
`base_z` 는 `overrides` 를 **먼저** 본다. Pascal 에서 100→150mm 로 키워도 낡은
`overrides.diameter` 가 남아 있으면 조용히 100 으로 남는다. `set_dim`/`set_base` 가
정식 키와 **별칭 전부**를 함께 갱신한다 — 별칭 표는 `geom_contract.MEP_DIM_ALIASES`
하나뿐이다(여기서 다시 쓰면 키가 늘 때 이쪽이 먼저 틀린다).

★ **`ColumnNode` 의 기본값은 장식용이다.** `style` 기본이 고전 기둥이고
`shaftStartScale` 0.72(목이 잘록), `baseStyle` round-rings, `capitalStyle` simple —
그대로 두면 구조 기둥 자리에 **그리스 신전이 선다.** Pascal 자신의 IFC 임포터도 같은
자리에서 장식을 벗기므로(`style:'plain'`·`baseStyle:'none'`·`shaftSegmentCount:1` …)
우리도 똑같이 벗긴다. 형상이 멀쩡해 보이는 종류의 오류라 검사에는 안 걸린다.

★ **zod 범위를 넘긴 노드는 내보내지 않는다.** Pascal 스키마는 수치에 상·하한이 있고
(zone `ceilingHeight` · 기둥 치수 > 0 등), 넘기면 `.parse()` 가 거부해 그 노드는 **씬에 안
올라온다**. 그냥 내보내면 "변환했다" 고 말해 놓고 Pascal 에서는 사라진다. `_RANGE` 로 미리
걸러 `out_of_pascal_range`(필드·값·한계)로 센다. (설비는 전용 노드라 여기 없다 — 기본 덕트로
보내던 때는 실측 환기평면의 100mm 폭 덕트가 하한 101.6mm 에 걸렸다.)

실측:

| 도면 | 입력 | Pascal 노드 | 표현 불가 | 왕복 최대 좌표 오차 |
|---|---|---|---|---|
| 지하3층 건축평면 | 벽 667 · 기둥 80 · 슬래브 8 · 개구부 34 · 장비 6 | **777** (벽 660 · 기둥 80 · 슬래브 8 · 개구부 29) | 벽 7(사다리꼴) · 개구부 5(붙을 벽 없음) · 장비 6 | **1.5e-11 mm** |
| 아파트 환기평면 | 덕트 45 · 장비 11 · 개구부 12 | **45** | 장비 11 · 개구부 12(벽 없는 도면) | **9.1e-13 mm** |
| MEP 샘플 | 슬래브 1 · 덕트 1 · 배관 2 · 트레이 1 · 장비 2 | **5** (전용 노드 4 — 트레이 포함) | 장비 2 | **0** |

두께·높이·`z_base`·`elevation` 불일치는 세 도면 모두 **0**, 되돌린 파일의
`verify_geometry` 는 원본과 같다.

★ **되찾지 못하는 것 하나**: 열린 벽의 `points` 는 페어링에 쓴 **원본 면선 한 줄**이라
축선에서 되살릴 수 없다(어느 쪽 면인지가 없다). 축선으로 채우고 `points_from_axis` 로
센다(실측 629). 빌드 형상은 축선 기준이라 영향이 없지만, 면선 커버리지 통계는 못 돌린다.

```
python pascal_bridge.py geometry.json -o scene.json          # → Pascal 씬
python pascal_bridge.py scene.json --back -o geometry.json   # ← 되돌리기
```

### ★ Pascal 편집 화면 호스트 — 저장 기준은 우리 저장소 하나다
`pascal_host/`. 고정 커밋(`PASCAL_COMMIT`)의 Pascal 체크아웃에 **우리 오버레이 파일**을
얹어 빌드하고, `run_host.py` 가 프로젝트 저장소 서버와 함께 띄운다.
```
python pascal_host/run_host.py <도면.dxf | 프로젝트.mep> --pascal <체크아웃> [--sync-overlay --build]
# → http://127.0.0.1:3002/mep
```
Pascal 의 `Editor` 는 영속화가 `onLoad`/`onSave` 어댑터 둘뿐이라 **같은 편집기**에
우리 어댑터를 끼운다(`SceneLoader` 와 같은 자리). Pascal 의 씬 DB 에는 쓰지 않는다.

| 부품 | 하는 일 |
|---|---|
| `app/mep/page.tsx` + `components/mep-project-loader.tsx` | 불러오기 `/api/mep/snapshot` · 자동 저장(1초 디바운스) `/api/mep/apply` |
| `app/api/mep/{snapshot,apply}/route.ts` + `lib/mep-project.ts` | **서버 측 프록시.** 저장소 토큰을 붙여 `ProjectServer` 로 넘긴다 |

- **토큰은 브라우저로 가지 않는다.** `MEP_PROJECT_TOKEN` 은 Next 서버 프로세스 환경에만
  있고 클라이언트 파일은 `/api/mep/*` 만 부른다(`NEXT_PUBLIC_` 금지 — 테스트가 고정).
  애초에 브라우저가 직접 부를 수도 없다: 우리 서버는 다른 출처를 **403** 으로 막는다(실측).
- **127.0.0.1 에만 바인딩한다.** 프록시가 토큰을 대신 붙이므로 LAN 에 열리면 인증 없는
  쓰기 경로가 된다. 라우트의 `guardSceneApiRequest`(Pascal 자신의 루프백 가드)는 두 번째 방어선.
- **저장 뒤에는 돌려받은 스냅샷으로 화면을 갈아 끼운다**(`applySceneGraphToEditor`).
  이동한 부재는 새 EID·새 노드 id 를 받으므로, 화면에 남은 옛 씬으로 이어서 저장하면
  방금 만든 복사본을 지우고 옛것을 되살리는 명령이 나온다. 그래서 `pascal_apply` 가
  성공·중복 응답에 새 스냅샷을 싣는다.
  갈아 끼운 씬이 부르는 **메아리 저장은 거르지 않는다** — 편집기가 노드마다 기본값을
  채워 넣어 우리 씬과 서명(`sceneGraphSignature`)이 같아질 수 없다(그걸로 거르던 코드는
  실측에서 한 번도 안 걸려 뺐다). 저장소가 명령 0개로 `no_changes` 를 답해 revision 이
  그대로다. 대신 **revision 이 오른 스냅샷만** 갈아 끼운다 — 같은 revision 을 다시 끼우면
  메아리가 또 메아리를 불러 저장이 끝나지 않는다.
- **사이드바 탭(장면 트리·작도·설정)을 넘겨야 한다.** 안 넘기면 편집기가 플러그인 탭만
  띄워 장면 트리가 없고, 다른 부재 속에 묻힌 벽은 고를 길이 없다(실측으로 겪음).
- **보기 도구줄(`viewerToolbarLeft/Right` — 3D·2D·분할 전환)도 넘겨야 한다.** 안 넘기면
  평면 편집 화면으로 갈 길이 없다 — 설비 경로 점 끌기와 평면 표시가 전부 2D 쪽이다(실측으로 겪음).
- **설비 플러그인은 스냅샷을 불러오기 전에 등록한다**(`ensureMepPlugin()`). 적재가 등록된
  스키마로 노드를 검사하기 때문이다.
- **원본 DXF 는 guide 노드로 깐다**(`/api/mep/source?floor=<층>` → `ProjectServer /pascal/source.svg`).
  `source_drawing.drawing_svg` 가 층 bbox 에 맞춘 **북쪽이 위인** SVG 를 그리고, 스냅샷이 층마다 guide 를
  bbox 중심·`scale = 가로 m / 10`(Pascal guide 는 가로 10 m × scale 평면)으로 그 층의 레벨에 붙인다.
  다리가 y 를 −Z 로 보내므로 뒤집지 않고 벽과 겹친다. ★ 스냅샷 지문은 **guide 를 넣기 전에** 잰다 —
  적용이 원본 선 없이 같은 식으로 다시 재므로, guide 까지 재면 저장할 때마다 409 가 난다. 되돌리기는
  guide 를 건너뛴다(부재가 아니다).
- **검토 탭**(사이드바 '검토', `components/mep-review-tab.tsx`): 선택한 부재의 `metadata.mep` 출처(EID·
  레이어·페어링·검토 사유·계통·재질)와 노드 치수·높이, `/api/mep/review` → `ProjectSession.pascal_review()`
  의 검토 목록(검토 사유 · 붙일 벽이 없는 개구부 · 끊긴 이음)과 편집 화면에 못 보낸 부재. 목록을 누르면
  그 EID 의 노드를 고른다. 조치할 것이 없는 사유(`wall_open_at_this_span`)는 싣지 않는다. 저장으로
  revision 이 오르면 로더가 `mep:revision` 이벤트를 보내 목록을 다시 불러온다.
- **현장 PC 는 동봉 런타임으로 띄운다**(npm·bun·체크아웃 없이). 개발 PC 에서 체크아웃을
  `PASCAL_PORTABLE_BUILD=1 next build` 한 뒤 `python pascal_host/stage_runtime.py --pascal <체크아웃>` —
  Pascal 자신의 CLI 스테이저(`packages/cli/scripts/stage-runtime.ts`)와 같은 순서로 standalone·public·
  `.next/static` 을 모으고 bun 저장소를 평탄화한다. `run_host.py --runtime` 은 런타임의 **고정 커밋·오버레이
  지문**이 지금 저장소와 다르면 띄우지 않고, 동봉 node 로 `server.js` 를 `HOSTNAME=127.0.0.1` 로 띄운다
  (standalone 서버는 HOSTNAME 이 없으면 0.0.0.0 에 붙는다). ★ 경로는 절대 경로로 넘긴다 — 작업 폴더를
  server.js 옆으로 옮기므로 상대 경로 `pascal_runtime` 이 두 번 붙어 MODULE_NOT_FOUND 로 죽었다(실측).
  실측(`sample_mep.dxf`): 스테이징 10초·239MB, 리스너 `127.0.0.1:3002` 하나, health·스냅샷(guide 1)·원본 SVG·
  검토 200, dry-run 적용은 명령 `width_mm 450` 만 내고 revision 그대로, 실제 적용 r0 → r1.
- **체크아웃이 고정 커밋이 아니거나 오버레이가 어긋나면 띄우지 않는다** — 다른 코드가 도는
  편집 화면으로 저장하면 무엇이 저장됐는지 아무도 모른다. Pascal 의 `next.config` 가
  `ignoreBuildErrors: true` 라 빌드는 타입 오류를 삼킨다 — 오버레이는 `tsc` 로 따로 검사한다.

실측(`sample_plan.dxf`, 고정 커밋 `b422fe2`): 편집 화면이 프록시로 **현재 revision** 을
불러온다 · 열린 벽 두께 +50mm → 명령 `overrides.width` 하나 · 돌려받은 스냅샷 r+1 ·
같은 작업 ID 재전송 → `duplicate` · 낡은 기준 → **409 가 프록시를 그대로 통과** ·
재열기 + DXF 재파싱 뒤에도 유지. 오버레이 `tsc` 오류 0.

**실제 화면 편집 왕복(실측)**: 장면 트리에서 외곽 블록 속에 묻힌 열린 벽을 골라 인스펙터에서
두께 0.25 → **0.3** 입력 → 1초 뒤 자동 저장이 `/api/mep/apply` 200(작업 ID 는 브라우저
`crypto.randomUUID()`) → 저장소 **r3**, 수정 `w:58ef7ed7: width 300` → 돌려받은 스냅샷을
갈아 끼운 뒤의 메아리 저장은 `no_changes`(revision 그대로) → 새로고침하면 우리 저장소에서
r3 를 다시 불러오고 → 재열기 + DXF 재파싱 뒤 빌더가 쓸 두께(`width_of`)도 300mm.
메아리 거르기를 빼고 revision 비교로 바꾼 뒤 다시 잰 것: 두께 0.35 → 0.4 → 저장 요청
**2번**(편집 162ms → r5 · 2초 뒤 메아리 63ms `no_changes`), 그 뒤 10초간 추가 요청 0 —
저장 고리가 없다.

**설비 전용 노드 화면 왕복(실측, `sample_mep.dxf`)**: 3D 에 슬래브·덕트·배관 2·트레이가 서고
장면 트리에 `M-DUCT`·`M-PIPE`·`E-TRAY` 로 보인다(콘솔 오류 0). 인스펙터에서 폭 400 → **500mm**
→ r1 `overrides.width_mm` · 계통 `SA` → r2 · 재질 `GI` → r3 · 모양 Rect → **Round** → r4
(`section_shape: round` 와 `diameter: 500` 을 함께 선언) · `.mep` 폴더로 다시 열어도 r4 ·
2D 에서 경로 첫 점을 끌어 2.5m 옮김 → r5, 원본 EID 가 사라지고 새 수동 레코드 `wm:…` 가
원형 Ø500 · SA · GI 를 그대로 갖고 선다.

★ **저장 상태 표시는 브라우저 번역에서 뺀다(`translate="no"`).** Pascal 화면이 영어라
브라우저가 자동 번역을 켜는데, 번역기가 텍스트 노드를 `<font>` 로 갈아 끼우면 React 가
바꾼 revision 이 **화면에 안 나타난다** — 실측: 저장소는 r3 인데 표시는 r2 에 멈춰 있었다
(`<font>` 4개, `<html class="translated…">`). '저장됐나' 를 판단하는 유일한 표시가 조용히
틀리는 종류라, 번역과 무관한 `data-revision` 도 함께 둔다. 고친 뒤 실측: 번역이 켜진 채
(인스펙터 글자가 한국어로 바뀐 상태) 표시가 r4 → r5 로 제대로 바뀌고 그 안의 `<font>` 는 0개.
