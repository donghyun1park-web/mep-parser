# MEP Parser — DXF → FreeCAD BIM 변환 도구

## 프로젝트 목적
건축·MEP 도면(DXF)에서 기둥/벽/슬래브/개구부를 추출해  
`geometry.json`으로 변환하고, FreeCAD Arch(BIM)로 3D + IFC 모델을 생성한다.

## 범위 (Scope) — 확정
- **입력은 DXF만.** DWG는 지원하지 않는다(사용자가 CAD에서 DXF로 변환 후 입력).
  → ODA File Converter 등 외부 바이너리 의존성 없음.
- **이미지/PDF/스캔본 입력 없음.** 벡터(DXF)만 다룬다 → 기하 추출은 100% 결정론적(ezdxf).
- **AI/LLM은 "코드 생성"이 아니라 "분류 보조"에만.** 빌더는 결정론적 코드가 담당하고,
  LLM은 레이어 명명이 관행을 안 따를 때의 tie-break 등 모호한 판정에만 개입한다.
- 핵심 원칙: **LLM은 FreeCAD 코드가 아니라 `geometry.json` 스키마를 채운다.**

## 파일 구성
| 파일 | 역할 |
|------|------|
| `dxf_parser.py` | DXF → geometry.json 파서 v2 |
| `geom_contract.py` | **기하 계약의 단일 출처.** z 기준면 `z_range()`, 감김 정규화 `ccw()`, 보 축선→footprint `beam_rings()`, MEP 경로 계약 v3(`path3d_segments`·`route_points`·`mep_section`·`rect_parts`). JS 소비자(preview)는 `js_constants()` 로 같은 식을 주입받는다. FreeCAD 의존 없음(단위테스트 가능) |
| `verify.py` | **빌드 게이트.** `verify_geometry`(빌드 전) / `verify_build`(빌드 후). 검사 ID V001~V104. 실패 시 빌더가 마커를 출력하지 않아 산출물이 나가지 않는다 |
| `stack_build.py` | **선언적 다층 조립.** `stack.json`(층별 dxf·z·offset) → 한 geometry.json. 통심선 offset 해결기 + 가드 3개. 파싱은 `dxf_parser.parse` 호출만 한다 |
| `schedule_table.py` | **부재일람표(MEMBER LIST) 복원.** TEXT 격자 → 표 → `{부재명: 단면}`. `layer_map` 의 `opts: schedule=<레이어>` 로 조인 |
| `layer_map.csv` | 레이어명 정규식 → 카테고리·치수 매핑 |
| `block_map.csv` | **블록(INSERT)명 정규식 → 카테고리·치수 매핑** (Phase 2) |
| `freecad_builder.py` | geometry.json → FreeCAD .FCStd + .ifc |
| `ifc_4d.py` | **공정표(CSV) → IFC 공정.** 이미 만들어진 `.ifc` 에 `IfcWorkSchedule`/`IfcTask` 를 얹고 층별로 부재를 연결한다. 4D 재생은 Bonsai 가 하고 **"이 공정이 이 층의 부재들"** 이라는 연결만 우리가 만든다. `ifcopenshell` 필요(선택 의존성 — Bonsai 안에 이미 있다) |
| `element_id.py` | **EID(원본 raw 좌표 기반 식별자) + 수정 사이드카 라운드트립** — 파라미터 변경에 불변, grouping 변경 시에만 EID 변경. `apply_edits()`로 재파싱 후 수정 보존. |
| `preview.py` + `frontend/src/` + `vendor/edit_geometry.js` | **Vite/Three.js 3D·원본 DXF 비교·평면 편집.** GUI 연결 미리보기는 프로젝트에 자동 저장한다. 동봉된 `frontend/built/`로 인터넷·Node 없이 실행하며 독립 HTML에서는 수정 파일을 내보낸다. |
| `source_drawing.py` | 원본 DXF 도형을 읽는 표시 전용 경로. 층별 원본에 조립 오프셋을 적용하며 미지원·제한 생략을 표시한다. IFC 입력 형상을 바꾸지 않는다. |
| `project_store.py` + `project_server.py` | **수정의 단일 저장소.** 프로젝트 revision, 원자적 저장·백업·복구, 층별 로컬 EID, GUI/브라우저/MCP 공통 처리, 인증된 PC 내부 HTTP 연결. |
| `edit_review.py` | 수동 편집 뒤 접합 틈·겹침·개구부 연결 진단. 원본 좌표는 자동 보정하지 않는다. |
| `mep_network.py` | **설비 연결성.** 조각·이어진 무리(`joints` 만)·끊긴 끝과, 끊긴 끝끼리의 이음 **후보**(직선·엘보·티, 같은 계통·높이, 1:1 최근접). 모델은 바꾸지 않는다. `geometry.mep_connectivity` 로 미리보기 검토 목록·평면 탭·Pascal 검토 탭·MCP·GUI 로그가 같이 쓴다 |
| `clash_review.py` | **간섭 검토 목록.** 구조체(벽·기둥·슬래브·보) × 설비 경로의 교차를 2.5D 로 한 곳씩 — 위치·부재 EID·조치 구분. 프로젝트 상태(`geometry.clash_review`)·미리보기 검토 목록·Pascal 검토 탭·MCP 가 같이 쓴다 |
| `artifact_validation.py` + `freecad_runner.py` | 실행별 산출물 영수증과 실제 IFC 재검사. 입력 해시·EID/GlobalId·형상·체적·층·QA 속성을 대조한다. |
| `mep_gui.py` | **현장용 GUI** (Phase 2.5): 파일선택→스캔→파싱→**3D 미리보기(브라우저)**→needs_review 수정→3D빌드 (tkinter, 무의존) |
| `run_gui.bat` | GUI 더블클릭 런처 (CLI 불필요) |
| `run_editor.bat` + `pascal_host/stage_runtime.py` | **편집 화면 런처(npm·bun 없이).** `stage_runtime.py` 가 Pascal standalone 빌드 + node 실행 파일 + `mep-runtime.json`(고정 커밋·오버레이 지문)을 `pascal_runtime/`(gitignore)에 모으고, `run_editor.bat <도면|프로젝트>` 가 `run_host.py --runtime pascal_runtime --open` 을 부른다 |
| `make_sample_dxf.py` | 테스트용 샘플 DXF 생성 (A-WALL/A-COLS/A-SLAB/A-ZONE) |
| `sample_plan.dxf` | 단선 벽 샘플 (회귀용) |
| `sample_walls.dxf` | **양면 2선 벽 샘플** (Phase 1 평행선 검출 검증용) |
| `sample_blocks.dxf` | **블록 참조(기둥/문 INSERT) 샘플** (Phase 2 검증용) |
| `sample_mep.dxf` | **MEP 샘플** (배관/덕트/트레이 중심선 + 장비 블록, Phase 2.7 검증용) |
| `geometry.json` | 파서 출력 예시 |
| `tests/run_all.py` | 의존성 없는 테스트 러너(`python tests/run_all.py`). **빌드 게이트는 `pytest tests` 다** — `build_exe.bat` 이 빌드 전에 돌리고 실패 시 중단한다. `run_all.py` 는 pytest 픽스처(`tmp_path`·`monkeypatch`)를 못 주어 테스트 48개를 못 돌리므로 **미실행이 있으면 스스로 exit 1** 을 낸다 — 반쪽 러너가 게이트 행세를 하지 않게 |
| `tests/golden.json` | **실무 도면 회귀 다이제스트.** 도면은 고객 자료라 커밋하지 않고 경로만 `tests/golden.local.json`(gitignore)에 둔다. 도면이 없으면 `[skip]`. 갱신은 `python tests/test_golden.py --bless`. 항목은 두 종류다 — `dxf`+`layer_map`(레이어맵 파싱) · **`project`(설비 `.mep` 폴더)**: 저장된 프로필·영역·단위 그대로 `ProjectSession` 으로 다시 해석해 설비 요약(이음·커버리지·연결성·간섭·진단 코드)까지 본다. 폴더는 **복사해서** 연다(원본 revision 을 건드리지 않게) |
| `extractors.py` + `mep_macro/` | **FreeCAD 안에서만** 쓰는 라이브 자연어 모델링용 헬퍼(`freecad_live_addon`). 일반 파이프라인은 이걸 거치지 않는다 |
| `FIX_SPEC.md` | 면선 페어링·곡선벽 개선의 이전 설계 기록. 현재 구현 여부는 코드와 회귀 테스트를 확인한다. |
| `pascal_bridge.py` | **geometry.json ↔ Pascal(pascalorg/editor) 씬 그래프.** 벽·기둥·슬래브·zone·개구부와 설비(배관·덕트·트레이는 **전용 플러그인 노드**, 단면 mm). 단위(mm↔m)·축(Pascal 은 Y-up)·고저(레벨 스택) 환산은 전부 `geom_contract` 를 부른다. Pascal 이 표현 못 하거나 스키마 범위를 넘긴 것은 `unconvertible` 로 **세어서 보고**한다 |
| `pascal_host/` | **Pascal 편집 화면 호스트(3단계 첫 조각).** `PASCAL_COMMIT`(고정 커밋) · `overlay/`(체크아웃에 얹는 우리 파일: `/mep` 페이지 · `/api/mep/*` 서버 측 프록시 · `lib/mep-plugin/` 설비 노드 플러그인) · `run_host.py`(저장소 서버와 Pascal 을 함께 띄우는 실행기) |
| `docs/project_workflow.md` | 프로젝트 저장·복구·재연결·출력 검증 사용법과 현재 범위. |

## geometry.json 스키마

### 도면 단위와 기존 프로젝트

- `drawing_units.py`는 헤더/명시 배율/기존 프로젝트 정책을 조사·파서·원본 겹쳐보기에 공통 적용한다. 새 `ProjectStore` source는 `unit_policy: "header"`를 저장한다. 그 필드가 없는 기존 프로젝트의 일반 건축 도면은 종전의 metre=1000/나머지=1 배율을 유지하며, 이를 `legacy_header_policy`로 표시한다. MEP 프로필의 기존 배율 해석은 유지한다.
- `configure_units`는 원본 해시와 revision을 검사하고 단위·영역·수정 이력을 함께 저장한다. mm로 정의한 레이어 규격, 블록 대체 치수와 높이는 원본 배율로 곱하지 않는다. 원호/타원 샘플링 허용오차도 mm로 환산한다. 일반 건축 원호의 기존 `ARC_MAX_SEGS` 상한은 남아 있으므로 매우 큰 반지름에서 보편적인 5mm 상한을 보장하지 않는다.
- 단위 변경은 좌표 기반 EID의 다른 원본 재사용을 일으킬 수 있다. `configure_units`와 `configure_source`는 배율 변경 시 기존 원본 수정을 `c:unit-r<revision>-<old-id>` 같은 예약된 고아 EID로 보존한다. 자동 재연결하지 않는다. 수동 추가 객체는 mm 좌표를 유지하고 검토 승인을 다시 요구한다. 프로젝트를 열기만 해서는 새 헤더 정책으로 변환하지 않는다.

### 프로젝트 원본 분류와 혼합 단면

- MEP profile v1의 선택적 `architecture_layers`는 `pattern/category(wall,column,ignore)/height_mm/width_mm`를 기존 건축 분류 앞에 적용한다. INSERT는 부모 레이어의 명시 규칙을 먼저 적용한다. 직접 도형과 INSERT의 원본 시그니처를 페어링 후 대조해 대표 레이어가 달라져도 검토 표시를 유지한다. 저장은 기존 `configure_source` 경로를 사용한다.
- FreeCAD 개구부는 Arch `Subtractions`에 영구 저장한다. recompute나 절삭 검증이 실패하면 이전 연결과 형상을 복원하고 새 보조 객체를 제거한다. 복원 실패는 출력 중단 오류이며, 전량 절삭 등 원래의 미해결 host 경고를 지우지 않는다.
- MEP layer의 `section_shape: round`는 `diameter_mm`를 사용한다. `source_handles`, `source_refs`(handle와 전체 insert_path), `entity_types`, `block_pattern`은 레이어/색상/선종류와 AND 조건이다. 여러 규칙에 매칭되거나 명시한 출처가 없으면 조용히 첫 규칙을 적용하지 않고 실패한다.
- `equipment/outline`은 source symbol envelope이며 `role: equipment|terminal`과 높이를 가진다. 장비 계약은 하단 기준이므로 경로 중심 높이 계산에서 반높이를 뺀다. 닫힌 원본 하나는 본체 하나로 보존하고, 열린 경계는 INSERT 인스턴스를 넘어서 연결하지 않는다. 중첩 기호·겹친 본체는 수량 검토가 필요하다. 포트나 장비 제품 상세는 생성하지 않는다.
- 선택 영역은 이번 프로필에서 재생성하지 않은 기존 설비에도 적용한다. coverage는 선택한 MEP 원본만 집계하며 건축 전체의 완전성을 평가한 값이 아니다. 고객 도면의 레이어명·핸들·영역·현장 규격은 공개 코드가 아니라 로컬 프로필에 보관한다.

### ★ 한 층에 공종 도면 여러 장 — 건축은 한 원본에서만
설비 도면(난방·환기)은 같은 세대의 건축 배경(XREF)을 물고 있다. 도면마다 프로젝트를 만들면 벽이 도면 수만큼
생기고(실측: 실무 단위세대 난방·환기 도면이 각각 벽 98개), 원본마다 층을 만들면 같은 z 에 층이 둘 서서 벽과
설비가 서로 다른 층으로 갈린다 — 간섭 검사를 할 모델이 없다. 종전엔 결과 JSON 을 사람이 병합해야 했다.

- 원본(프로젝트 source = stack level)의 `floor` 가 같으면 **한 층**이다. z 가 다르면 `StackError`.
- `categories` 로 그 원본에서 담을 종류를 정한다. 담지 않은 것은 `stack.levels[].excluded` 로 센다.
  개구부는 같은 원본의 벽과 함께만 담는다(`wall_indices` 가 그 원본 벽을 가리킨다). 첫 원본과 좌표 범위가
  안 겹치면 경고한다(기준점 확인).
- 층 항목은 `{"z","label","id","sources":[…]}` 이고 레코드의 `level` 은 **원본 id 그대로**다(EID 접두·수정이
  원본별). 층 소속은 `geom_contract.floor_has_level` 하나로 빌더·V001/V002·IFC 재검사가 판정한다 — 이름만
  비교하면 같은 층의 원본이 전부 고아가 된다.
- 추가: GUI '같은 층 도면 추가(설비)' · MCP `add_project_source` → `ProjectSession.add_source`. 기준(첫) 원본의
  층·z·offset·매핑을 물려받고 기본 `categories` 는 설비 4종이다. 후보를 먼저 파싱해 실패하면 프로젝트는 그대로다.
  단일 도면 프로젝트는 층 이름 `Level_1` 을 유지하고, 기존 수정은 `main:` 접두로 그대로 붙는다. 원본이 여럿이면
  '설비 도면 설정'·'도면 단위 확인'이 도면을 먼저 고른다.
- 회귀: `tests/test_same_floor_sources.py`, 합성 도면 벽 + 다른 도면 덕트가 한 층에서 FreeCAD/IFC verified·간섭 1
  (`test_same_floor_drawings_share_one_storey_and_clash_natively`).

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

```json
{
  "source": "plan.dxf",
  "units": "mm",
  "scale_applied": 1.0,
  "params": {"wall": {"width": 200, "height": 2800}, "column": {"height": 3000}, "slab": {"thickness": 200}},
  "wall_pairing": {"paired": 5, "single": 1},
  "elements": {
    "wall":    [{"kind": "polyline", "closed": false, "points": [[x,y],[x,y]], "centerline": [[cx,cy],[cx,cy]], "width_detected": 200.0, "confidence": 0.92, "pairing": "paired", "needs_review": false, "overrides": {...}, "zone": 0}],
    "column":  [{"kind": "circle"|"polyline", "center":[x,y], "radius": r}  또는  {"kind":"polyline","closed":true,...}],
    "slab":    [{"kind": "polyline", "closed": true, "points": [...]}],
    "beam":    [{"kind": "polyline", "points": [[x,y],[x,y]], "source": "dimension", "member_name": "RAG11B", "schedule_match": "ok", "section": {"name": "RAG11B", "size": "400x1800x25x25", "notation": "BxD", "b": 400.0, "h": 1800.0}, "overrides": {"width": 400.0, "thickness": 1800.0}}],
    "zone":    [{"kind": "polyline", "closed": true, "points": [...]}],
    "opening": [{"kind": "circle", "center": [...], "radius": r, "width": 900.0, "height": 1200.0, "sill": 900.0, "dims_assumed": ["height","sill"], "wall_indices": [12]}],
    "pipe":    [{"kind": "polyline", "points": [...], "elevation": 2600.0, "diameter": 100.0}],
    "duct":    [{"kind": "polyline", "points": [...], "elevation": 2800.0, "width_mm": 400.0, "height_mm": 300.0}],
    "tray":    [{"kind": "polyline", "points": [...], "elevation": 3000.0, "width_mm": 300.0, "height_mm": 100.0}],
    "equipment":[{"kind": "polyline", "closed": true, "points": [...], "elevation": 0.0}]
  },
  "blocks": {"inserts": 2, "unmapped": 0},
  "mep": {"pipe": 2, "duct": 1, "tray": 1, "equipment": 2},
  "contract": {"version": 2, "z_datum": {"wall": "bottom", "slab": "top", "...": "..."}},
  "floors": [{"z": 0.0, "label": "Level_1"}],
  "ignored": {"배수판_벽체": 128},
  "thin_pairs": {"A-CON": 35},
  "closed_wall_dups": {"A-CON": 36},
  "duplicate_geometry_dropped": {"column": {"A-CON": 13}},
  "width_conflicts": [{"layer": "A-CON", "declared": 200.0, "detected": 450.0, "count": 40}],
  "small_openings_dropped": {"A-DOOR": 244},
  "openings_dims_assumed": {"height": 44, "sill": 44},
  "shadowed_layer_rules": [{"rule": "...", "category": "ignore", "shadowed_by": [...]}],
  "tolerances_effective": {"defaults": {"pair_max": 500.0}, "per_layer": {"^00-보$": {"pair_max": 2500.0}}},
  "member_schedule": {"layers": ["BEAM_SCHEDULE"], "tables": 8, "members": 77, "matched": 225, "unmatched": 0, "names_unmatched": {}},
  "warnings": ["미매핑 레이어: ..."]
}
```

### ★ 중복 부재 — 실무 도면은 같은 것을 두 번 그린다
같은 블록을 같은 자리에 두 번 넣거나(실측 기둥 13개), 골조 레이어와 건축 레이어에
각각 그린다(실측 닫힌 벽 36쌍, `A-CON` ∥ `상부골조`). **형상은 멀쩡해 보여 어떤 검사에도
안 걸리고 물량만 조용히 부푼다.** `drop_duplicate_geometry()` 가 `_geom_key`(좌표·반지름·
`z_base`·`width_detected`)로 **완전히 같은 것만** 버린다 — 0.1mm 라도 다르면 남긴다.
적용 대상은 `column`/`slab`/`beam`/`equipment` + 닫힌 벽. 결과는
`duplicate_geometry_dropped`·`closed_wall_dups` 로 자기보고한다.

**면선 페어링을 거친 열린 벽에는 쓰지 않는다.** 축선이 같은 28쌍 중 12쌍은 두께가 다르다
(450 vs 400mm — 두 레이어가 벽면을 다른 자리에 그렸다). 어느 쪽이 맞는지는 도면을 봐야
알므로 여기서 조용히 고르지 않는다.

**단, EID 까지 같은 벽은 예외다.** EID 가 같다는 것은 원본 엔티티 시그니처와 구간이
같다는 뜻 — 같은 원본에서 두 번 만들어졌다는 증명이라 고를 것이 없다. 그래서 EID 부여
직후(개구부 링크·`apply_edits` 보다 앞) `(eid, _geom_key)` 가 같은 벽을 하나만 남긴다.
실측: 벽 682 → 672, `eid_collisions` 10 → 0, 폭 495mm 열 35 → 25. 나머지 다이제스트는
전부 불변이었다 — 중복만 건드렸다는 증거가 그것이다.

### ★ `App.Vector.normalize()` 는 **제자리에서** 바꾼다 — 덕트가 종잇장이 됐다
`_rect_solid` 가 `App.Rotation(Z, seg.normalize())` 로 회전축을 만든 뒤 **같은 벡터**를
`face.extrude(seg)` 에 넘겼다. `normalize()` 는 3000 → 1.0 으로 원본을 바꾸므로 압출
길이가 **1mm** 가 된다. 실측: 덕트 45개가 단면 150×1, 총 부피 0.001m³ —
**있어야 할 2.132m³ 의 0.05%** 였다.

**형상은 유효하다.** `isValid()` 도 V103(퇴화 형상)도 V107(IFC 재검사)도 전부
통과했고, IFC 에 `IfcDuctSegment` 45개가 제 위치(z 2,390~2,590)에 들어 있었다.
뷰어에서 안 보이는 것만이 유일한 증상이었다 — 사용자가 3D 를 열어 보고 알았다.

그래서 **부피로 잰다**: `build_mep` 이 축선 길이 × 단면적을 `mep_volume.expected_mm3`
로, 실제 솔리드 부피를 `built_mm3` 로 남기고, V106 이 비율 < `MEP_VOLUME_MIN_RATIO`
(0.5)면 error 를 올린다. 코너 마이터 때문에 1.0 은 될 수 없으니 잡으려는 건 오차가
아니라 **자릿수가 다른 퇴화**다. 고친 뒤 실측 비율 1.000.
(`_pipe_solid` 의 같은 `normalize()` 호출은 길이를 별도 인자로 넘겨 무사하다.)

### ★ 계약 v3 — MEP 경로는 `path3d`(해석 구간), 샘플 점은 파생이다
배관·덕트·트레이의 현재 형상은 `path3d.segments` 다 — 직선 · 원호(중심·법선, 법선 둘레 반시계) ·
스플라인(NURBS: 차수·제어점·매듭·가중치). mm·Z-up 그대로이고 **z 는 `elevation`(중심축)에서의
상대값**이다. 그래서 설치 높이 규칙(프로필 placement · `top:` 선언 · `overrides.elevation`)이
여전히 한 손잡이이고, 경로 전체를 위아래로 옮긴 편집도 elevation 선언 하나로 남는다.
DXF 평면도에서 온 경로는 전부 dz = 0 이다.

| `geom_contract` | 쓰는 곳 |
|---|---|
| `path3d_segments` · `route_points` · `route_length` | 모든 빌더·물량·다리. `path3d` 가 없으면 (points, elevation) 의 직선 구간 — **v2 파일은 좌표를 하나도 옮기지 않는다** |
| `mep_section` · `section_shape` | 단면 모양·치수·`section_roll`. 원형 덕트는 `section_shape: "round"` + `diameter` — 지름을 기본값으로 때우지 않는다 |
| `rect_parts` · `rect_sweep_mesh` · `ROUND_SIDES` | 관 형상(아래 절) — 원형 관도 같은 링을 정다각형으로 |

- `mep_paths.extract_curve` 가 원본을 읽는 순간 `path3d` 를 **샘플 점과 같은 방향**으로 낸다.
  타원은 `rational_bspline_from_ellipse` 로 정확한 NURBS, 거울 복사한 원호(돌출 −Z)는 돌출
  벡터가 곧 법선이다. `join_paths` 는 뒤집힌 원본의 구간을 식째로 뒤집어 붙인다. 해석 구간이
  평가한 곡선과 끝이 안 맞으면 샘플 점으로 대체하고 `path3d_basis: "evaluated_points"` 로 말한다.
- EID 는 계속 원본 사양(`source_geometry`)에서만 나온다 — `path3d` 가 생겨도 기존 수정이 고아가 되지 않는다.
- 원본 길이 `source_length_mm` 은 출처로 남고, 편집했거나 사람이 그린 경로의 물량은
  `route_length`(수직·경사 포함)다. 끊긴 `path3d` 는 V010 이 막는다.

### ★ 사각 덕트는 **공통 마이터 링**으로 만든다 — 스윕이 단면을 눕혔다
FreeCAD·Blender 가 `geom_contract.rect_parts` 의 같은 링을 쓴다. 폭은 수평(`section_axes` —
Pascal `rectSectionAxes` 를 다리의 축 맞바꿈까지 반영해 옮긴 것), 꺾인 점은 마이터, 단면
방향은 첫 구간에서 **최소 회전으로 전달**한다(연직 구간에서 world X 로 떨어져 비틀리지 않게).
링의 평면 면을 한 셸로 꿰매므로 상자 fuse 의 공면 이음매(IFC 비다양체 — 실측: 꺾이는 덕트
7개가 V107)가 없고, 부피가 샘플 꺾은선 길이 × 단면적과 **정확히** 같다. Blender 의 평면 직선
경로는 종전 평면 버퍼 압출 그대로다(폭 수평, 같은 형상).

★ **종전 `MakePipeShell` 스윕은 단면을 눕혔다.** 사각 단면을 +Z → 진행방향 최소 회전으로
놓았는데, 그 회전은 방향마다 사각형을 다르게 돌린다. FreeCAD 1.1 실측(400×100 덕트의 높이
방향 크기): **y 방향 100 · x 방향 400 · 대각선 353.6** — x 방향 덕트는 폭·높이가 뒤바뀌고
대각선은 45° 기울었다. 부피는 그대로라 V106(부피)·V107(IFC 재검사)이 못 잡았고, Blender 는
폭이 수평이라 같은 도면이 두 출력에서 다른 덕트였다. 고친 뒤 네 방향 모두 100.

★ 짧은 구간에서 급하게 꺾이면 양 끝 마이터 면이 단면 안에서 교차해 관이 **뒤집힌다**
(실측: 폭 200mm 덕트가 188mm 구간에서 118°). `rect_parts` 가 그 구간만 **직각으로 끊은 독립
토막**으로 만든다 — 그 자리는 실제로 직관이 아니라 피팅이다. FreeCAD 는 토막을 합치고(실측:
유효 솔리드 1개, 부피 비율 0.836 — 모서리 겹침만큼), Blender 는 한 객체의 여러 닫힌 셸로 싣는다.

★ 원형 단면(배관·원형 덕트): 단일 평면 직선만 `Arch.makePipe`를 사용한다. 꺾임·원호·스플라인·수직 구간이
있으면 사각 덕트와 **같은 마이터 링**을 원에 내접한 정다각형(`GC.ROUND_SIDES` = 24변)으로 꿰매 평면
면만의 관을 만든다(`GC.rect_parts(..., sides=)`, 부피는 원의 98.9%). `Arch.makePipe` 는 원을 첫 구간의
**현(chord)** 에 수직으로 놓아 곡선으로 시작하는 관을 찌그러뜨리고(실측: R1000 사분원 위 Ø100 덕트 부피
70.7%), 그걸 피하려고 쓰던 해석 와이어 `makePipeShell` 스윕은 **IFC 에서 깨졌다** — FreeCAD 1.1 실측:
수직으로 꺾인 Ø20 입상관이 V107(비다양체 메시), R500 원호 Ø125 덕트는 IFC 단계에서 240초 넘게 멈췄다.
형상 검사(부피·bbox)로는 안 드러나 **빌더 전체(FCStd 재열기 + IFC 재검사)** 로 잰다 — 고친 뒤 원호 덕트
10초·입상관 6초, 혼합 환기 픽스처(110×54 · 204×60 경사 · Ø100 가지 · Ø125 원호 · Ø20 입상, 티 이음 1)가
FCStd·IFC 둘 다 verified(43초). Blender 도 원형 경로를 **같은 정다각형 관 메시**로 싣는다(종전 원형 bevel
곡선은 저장본 대조가 'Saved actual Z bounds differ' 로 막혔다). 관 메시의 `z_bounds_mm` 는 `z_range` 봉투가
아니라 **실제 꼭짓점**에서 잰다 — 입상관 끝 단면은 수평이라 봉투보다 반지름만큼 낮다. 고친 뒤 같은
혼합 환기 픽스처가 Blender 4.2 저장본 재열기까지 verified(EID 5개 전부).

★ **관 면이 수만 개면 FreeCAD 는 형상 만들기가 아니라 Arch 후처리에서 멈춘다.** 실무 난방 도면(PB Ø15.9 5경로 ·
원호 R90~100 · 링 1,867개 × 24변 = 44,698면)이 900초 제한에서 시간 초과했다(MEP 생성 149.8초 · recompute
270.8초 · 임시 IFC 120MB, 미검증). 원인 둘 다 **면을 바꾸지 않고** 없앴다:
- Base 가 있는 Arch 컴포넌트는 recompute 마다(저장본 재열기 검증 포함) `removeSplitter` 와, 수평 투영 면을
  **하나씩 fuse** 하는 면적 계산(`ArchComponent.computeAreas`)을 다시 돈다 — 면 수의 제곱이다. MEP 형상은
  **Base 없이** `Arch.makeComponent()` 에 직접 싣는다(Base 가 없으면 Arch 가 형상을 건드리지 않는다).
- IFC 직렬화(`SERIALIZE` → IfcAdvancedBrep)는 `removeSplitter` 를 또 돌고 같은 형상에 파일이 13배다. 평면 면만인
  MEP 형상(`FACETED_EXPORT`)은 면 그대로 IfcFacetedBrep 로 쓴다 — 곡면이 있는 `Arch.makePipe` 만 직렬화한다.

실측(같은 입력·같은 링): recompute 270.8 → **0초**, 전체 **416.5초에 FCStd·IFC 둘 다 verified**, FCStd 53.5 → 39.2MB,
IFC 120.5 → **9.4MB**. 5경로 부피·bbox 가 `rect_sweep_mesh` 계산값과 같고(부피 차 < 0.001mm³) IFC 재검사 부피 차는
1e-9 수준이다. 남은 최대 구간은 MEP 형상 생성(189초, 여유 메모리 0.6GB 에서 잰 값)이다. 24변·현오차 0.5mm
규약은 그대로다 — 바꾸려면 같은 형상 검증부터 한다.

`mep_volume` 은 **사각·외곽선 duct/tray 만** 센다. 원형 단면은 스윕 몫(오차 0.07~0.1%)이고 장비는
축선이 아니라 footprint 압출이라 '길이 × 단면' 기대식이 안 맞는다. 한쪽만 담으면
셈이 어긋난다(실측: 장비가 built 에만 들어가 MEP 샘플 비율이 3.78 이었다 → 1.0000).

### ★ 설비 도면은 엘보·티에서 중심선이 끊긴다 — `connect_gap=` 으로 잇는다
피팅이 **별도 레이어**에 그려져(실측 `환기-피팅` 1317개) 중심선이 그 자리에서 끊긴다.
실측: 덕트 끝점 86개 중 45개가 다음 덕트까지 ~200mm. 그대로 두면 토막난 덕트가
나오고 연장도 조각으로 잡힌다.

`join_mep_runs` 가 선언한 거리 안에서 잇는다 — **선언했을 때만**. 틈을 메우는 건
없던 기하를 만드는 일이라 파서가 알아서 할 일이 아니다. 결과는 `mep_joined`
(`before`/`after`/`gap_mm`/`bridged_mm`)로 자기보고한다. 실측(`connect_gap=250`):
덕트 45조각 → **15개 계통**, 총연장 67.5 → 70.6m(이어붙인 틈 3.9m).

벽의 `join_connected_lines`(5mm 격자)를 재사용하지 않는 이유는 허용치가 40배 다르기
때문이다 — 그 격자 해시는 200mm 에서 경계 효과로 짝을 놓친다. MEP 는 레코드가 수십
개라 정확한 O(n²) 로 붙여도 싸다.

`ifctype_counts` 는 **MEP 까지** 센다(`pipesegment`·`ductsegment`·`cablecarriersegment`·
`distributionelement`). 종전엔 구조 4종만 세서, IFC 에 `IfcDuctSegment` 45개가 들어
있는데 영수증에는 `{'wall': 88}` 만 찍혔다 — 덕트가 조용히 사라져도 `build.json` 이
말해 주지 않는 상태였다(보가 정확히 그렇게 사라진 적이 있다 — D3b 참조).

빌더는 카테고리별로 **객체를 못 만든 레코드**를 `build.json` 의 `unbuilt` 에 남긴다.
전부 0 이어야 한다 — 이 카운터가 없던 동안 벽 72개가 경고 없이 빠진 채 납품될 수 있었다.

### ★ 이음(joint)은 **도면이 이어 그린 곳**에만 — 가까움으로 잇지 않는다
분기·피팅은 계통을 정하고 물량이 된다. 그런데 끝이 스치는 자리를 이으면 SA 와 RA 가 한 계통이
되고 피팅이 부푼다. 그래서 `geom_contract.assign_joints` 는 **명시적 증거**만 이음으로 친다:

| 도면에서 | 이음 |
|---|---|
| 경로 끝이 다른 경로 끝과 같은 점(3D, 0.001mm) | 끝-끝 — 엘보·레듀서·티·크로스 |
| 경로 끝이 다른 경로의 **안쪽 위**에 놓임 | 가지 — 줄기는 `port: "tap"` + `at_mm`(`route_points` 를 따른 길이) |
| 안쪽끼리 교차 · 틈(0.5mm 라도) · 다른 높이 · 다른 카테고리 | **아니다** |

레코드가 `joints: [{id, port, at_mm?}]` 를 들고, **같은 id 를 든 레코드끼리가 한 이음**이다.
id 는 이음 점 좌표에서 나와 재파싱해도 같고(다층은 `stack_build` 가 층 접두를 한 번만 붙인다),
구성원이 id 를 들고 다니므로 Pascal 에서 옮겨 delete+add 로 EID 가 바뀌어도 끊어지지 않는다
(다리의 `_PROVENANCE`). 편집으로 상대가 지워지거나(`single_member`) 떨어지면(`members_apart`)
**고치지 않고** V012 가 말한다(`joint_problems`) — 파싱 직후에는 생기지 않는다.

- 부르는 곳: 레거시 파서(`connect_gap` 잇기 뒤) · MEP 프로필(`join_paths` 뒤, 프로필의
  `endpoint_tolerance_mm`). `join_paths` 는 가지점에서 잇기를 멈추므로 그 경로들이 이음으로 묶인다.
- 물량 `MEP 이음` 표: 가지 수 3 = 티 · 4 = 크로스 · 2 는 꺾이면 엘보, 곧고 규격이 다르면 레듀서.
  **곧고 같은 규격은 세지 않는다** — 피팅이 아니라 선을 끊어 그린 자리다.
- IFC 는 `Pset_MEPParser.Joints`(JSON). Pascal 기본 도구의 피팅·단말 노드는 아직 저장하지 않고
  `dropped["pascal_node:<종류>"]` 로 센다(조용히 사라지지 않게).
- 실측(`sample_mep.dxf`): 배관 가지 1개가 줄기 5000mm 지점의 **티**로 잡히고 덕트·트레이는 이음 0.
  같은 도면을 두 층에 쌓으면 `1F:j:…`·`2F:j:…` 두 이음으로 갈린다.

### ★ 덕트 폭은 도면에 이미 있다 — 외곽선 간격으로 재서 규칙을 나눈다(P2)
종전엔 규격마다 규칙을 만들고 원본 핸들 번호를 사람이 적었다(실측: 환기 한 장에 규칙 7개 · 핸들 41개).
`mep_profile.measure_outline_widths(dxf, rule)` 가 규칙에 걸리는 중심선 LINE 마다 **같은 레이어**의 나란한(2°)
선 중 중심선 길이의 절반 이상 겹치는 가장 가까운 좌·우 선을 찾아 폭 = 두 간격의 합으로 재고, 3mm 안에서
이어지는 폭끼리 묶는다. 한쪽만(`one_side`) · 좌우 간격 차 > max(3mm, 폭 5%)(`asymmetric`) · 선 없음 · 곡선은
사유와 함께 `unmeasured`. `split_rule_by_outline_widths` 가 폭 묶음마다 원 규칙 + `source_handles`(블록 안이면
`source_refs`) + `width_mm`(원형 규칙이면 `diameter_mm`) 규칙을, 못 잰 원본은 따로 한 규칙을 낸다.
- **평면에는 높이와 원형/사각 구분이 없다** — 폭만 증거로 내고 나머지는 제품 자료로 사람이 채운다(저장 검증이
  빈 치수를 막는다). 자동 적용하지 않는다.
- 붙는 곳: 설비 설정 '선택 규칙을 외곽선 폭별로 나누기'(확인 대화) · `ProjectSession.measure_outline_widths` ·
  MCP `measure_mep_outline_widths`(→ `propose_mep_profile`).
- 실측(단위세대 환기, 빨간 중심선 LINE): SA 110×10 · 125×4 · 204×5, RA 100×6 · 110×12 · 125×4 · 204×4 — 사람이 적은
  규격과 **41/41 일치**, 불일치 0. 사람이 규격 판정 불가로 뺀 RA 짧은 토막 4개도 110 으로 잡힌다(확인 대상).

### ★ 원본 반영 '완전' ≠ 계통이 이어짐 — 연결성은 따로 센다(P1-d 1단계)
`source_coverage.complete` 는 선택한 원본을 다 담았다는 뜻인데 이어졌다는 뜻으로 읽혔다(실측: 실무 단위세대 환기
덕트 37조각 · 이음 0 인데 complete). `mep_network.analyze` 가 따로 센다 — **보고와 표시만, 모델은 그대로**.
- 이어짐은 `joints` 만. 끊긴 끝끼리의 후보는 같은 카테고리·계통·높이 범위이고 **직선**(마주 봄 10° · 옆 어긋남
  ≤ max(20, 폭/4)) · **엘보**(60° 이상 꺾여 모서리가 두 끝 앞) · **티**(끝의 연장이 다른 경로 옆면)일 때만, 가까운
  짝부터 한 끝에 하나. 계통만 다르면 `conflicts`. 장비 외곽 150mm 안의 끝은 `terminal`.
- 탐색 거리는 **큰 단면 폭 × 2.5** 다. 고정 300mm 는 Ø15.9 난방 코일의 분배기 앞 서로 다른 회로 끝 4쌍을 엘보로
  이었다(폭 배수로 0). 환기는 두 규칙 모두 26 이다.
- ★ 사용자 제안이던 '피팅 레이어 폴리곤에 닿으면 잇기 → 거리 안이면 잇기' 는 같은 도면에서 측정하고 접었다:
  피팅 레이어 1,317개는 선·스플라인뿐(닫힌 도형 0)이고 7곳 기호에 몰려 끝점 74개 중 10개만 닿아 1단계가 0건,
  거리 300mm 잇기는 28곳 중 SA↔RA 2 · 기하 불가 3 이었다. 계통 수만 보면 그게 가장 좋아 보인다(37 → 9).
- 끊긴 끝은 **열림만이 아니다**: 장비·단말 외곽에서 `TERMINAL_MM`(300mm) 안에서 **그쪽을 향해** 끝나면
  `terminal`, 슬리브 평면에 닿으면 `sleeve`(모델 밖으로 나가는 끝)다. 방향을 같이 보는 이유는 실측이다 —
  단위세대 환기의 디퓨저 33개 기준으로 끝에서 150mm 안은 **0개**, 300mm 안이 9개인데 그중 8개만 그 단말을
  향하고 나머지 하나(203mm)는 **반대 방향**이라 거리만 보면 옆을 지나는 경로를 단말로 삼킨다.
- 실측(환기): 후보 26(직선 6 · 엘보 16 · 티 4) · 규격 바뀜 9 · 충돌 0 · 무리 37 → 모두 확정 시 11 · 끊긴 끝 74 중
  열림 26 · 3ms. 슬리브 레이어를 `role: sleeve` 로 선언하면 끝 2개가 열림에서 `sleeve` 로 바뀐다.
  난방: 후보 0, 끝 10 — 분배기를 `role: terminal` 로 선언해야 단말로 갈린다.
- ★ 장비 외곽은 **닫힌 면 하나**여야 한다. 실측: 환기유니트 레이어를 `equipment` 로 선언하면 한 INSERT 안에서
  닫힌 면이 여러 개 겹쳐 나와 `Ambiguous equipment outline` 으로 저장이 막힌다 — 도면 기호가 여러 겹이라는
  뜻이므로 원본 필터(`source_handles`·`block_pattern`)로 본체 하나를 지정해야 한다. 코드가 고를 일이 아니다.
- ★ **기호가 여러 겹이면 물량이 그만큼 부푼다.** 실측(단위세대 환기): 디퓨저 블록 `apt-diff` 한 개 안에 동심원이
  셋이라(지름 158·101·97) 단말 본체가 11곳 × 3 = **33개**로 서고 `EQUIPMENT_OVERLAP` 33건이 떴다. 블록 **안쪽**
  핸들은 인스턴스가 달라도 같으므로 `source_handles: ["<바깥 원 핸들>"]` 하나로 위치마다 한 겹만 남는다
  (본체 33 → **11**, 겹침 경고 0, 단말 판정은 그대로). 도면이 바뀌면 이 핸들도 다시 확인해야 한다.
- 실무 적용(단위세대 환기, revision 4): 슬리브 2 · 단말 11 → 끊긴 끝 26 → **17**(단말 7 · 슬리브 2).
  커버리지는 60/64 로 `complete: False` 인데 **그게 맞다** — 못 담은 4개는 슬리브 사각형 안의 **대각선 X 표시**
  (235.8mm 선 4개)라 면이 되지 않는다. 난방은 분배기 후보 레이어(`EQ`·`SS-EQ`)가 둘 다 겹친 외곽이라
  `Ambiguous` 로 막혀 **적용하지 않았다** — 본체 핸들을 도면에서 골라야 한다.
- 붙는 곳: `ProjectSession._parse`(실패하면 `summary.error`) · 미리보기 검토 대기(간섭 다음 '연결 후보'·'계통 충돌')
  · 평면 탭(빨간 점 = 끊긴 끝, 주황 점선 = 후보, 빨간 점선 = 충돌) · Pascal 검토 탭(`mep_gap`) · MCP
  `get_review_items`·`get_mep_diagnostics` · GUI 로그('원본 반영 N/N' 과 따로).
- **2단계 — 사람이 확정한 후보만 이음이 된다.** 확정은 **프로젝트 파일의 `bridges`**(후보 id)에 저장한다.
  수정 사이드카에 두지 않는 이유는 열쇠가 부재 EID 하나인데 후보는 **두 부재의 짝**이고, 상대 EID 가 값 안에
  들어가면 `edits_to_local` 이 키만 옮겨 다층에서 층 접두가 어긋나기 때문이다(월드 EID 로 남는다).
  - 재파싱마다 `mep_network.apply_bridges` 가 **그때의 후보 목록과 대조**해 적용한다. 도면이 바뀌어 후보가
    사라지면 적용하지 않고 `mep_connectivity.bridges.orphaned`(사유 `candidate_gone`)로 말한다 — 후보 id 는
    두 EID·포트·종류에서 나오므로, 없던 이음이 조용히 남지 않는다.
  - 기록되는 것은 `joints` 참조뿐이다(**형상·좌표 불변**): `{id, port, basis: "bridged", gap_mm}` · 가지는 `at_mm`.
    `joint_problems` 는 선언한 틈까지는 `members_apart` 로 보고하지 않는다 — 떨어져 있는 것이 정상인 이음이다.
  - 부르는 곳: 미리보기 검토 목록의 **'이음 확정'·'확정 취소' 버튼**(`POST /bridges` → 프로젝트 토큰) ·
    `ProjectSession.confirm_bridge`(취소도 같은 함수) · MCP `confirm_mep_connection`(`reviewed_by_user` 필요).
  - 확정한 이음은 다음 파싱부터 **후보에서 빠지므로**(이미 이어진 끝이다) `bridges.applied` 가 후보 내용을 그대로
    실어 보내고, 검토 목록에 '확정한 이음' 행으로 남는다 — 취소할 자리가 화면에 있어야 한다.
  - 서버 없이 연 독립 HTML 미리보기에는 저장할 곳이 없어 버튼을 그리지 않는다(수정 사이드카와 같은 규약).
  - ★ **일상 후보는 한 번에 확정한다** — 26건을 버튼 26번·revision 26번으로 밟게 하지 않는다. `routine` 은
    **직선·엘보이면서 규격이 안 바뀌는** 후보다(`mep_network.ROUTINE_KINDS` — 이 규칙은 `_entry` 한 줄뿐).
    티는 뺀다(가지가 계통 위상과 티 피팅·줄기의 `at_mm` 을 바꾼다) · 규격이 바뀌는 자리도 뺀다(레듀서가
    서는 곳이라 도면을 봐야 한다). **묶어서 보여 주는 기준이지 자동으로 적용하는 기준이 아니다** —
    누르는 것은 사람이다. 요약에 `routine`, 검토 목록 위에 버튼 하나(`review_logic.routineCandidateIds` ·
    `bridgeRequest`), `ProjectSession.confirm_bridges` · `POST /bridges` 의 `candidate_ids` · MCP
    `confirm_mep_connections`. **전부 아니면 아무것도** 다 — 모르는 후보가 하나라도 섞이면 통째로 거부한다
    (부분 적용은 무엇이 들어갔는지 아무도 모르게 만든다). `candidate_id` 와 `candidate_ids` 를 함께 보내면 400.
    실측(단위세대 환기): 후보 26 중 **일상 17**(직선 3 · 엘보 14) — 나머지 9 는 규격 바뀜 5 와 티 4 이고
    티는 4개 모두 규격이 함께 바뀐다. 난방은 후보 0.
    실측(화면 왕복, 합성 덕트 8조각): 버튼 '일상 이음 후보 3건 일괄 확정' → r0 → **r1**(결정 기록 **한 건**에
    후보 id 3개) · 무리 8 → 5 · 후보 5 → 2(티 2 만 남고 버튼이 사라진다) · 행 3개가 '확정한 이음' 으로 ·
    그중 하나를 '확정 취소' 하면 다시 후보가 되고 버튼이 '1건' 으로 돌아온다(단건 경로는 그대로다).
  - **실제 화면 왕복(실측, 합성 덕트 2조각 + 직각 가지)**: 검토 목록에 '연결 후보' 2행 → '이음 확정' 클릭 →
    저장소 r0 → **r1**(`bridges` 에 후보 id · 결정 `confirm_bridge`), 행이 '확정한 이음'(+ '확정 취소')으로 바뀌고
    저장 표시 '서버에 저장됨' → '확정 취소' → **r2**(`bridges` 빈 목록 · 결정 `unconfirm_bridge`), 행이 다시
    '연결 후보'. 형상·좌표는 양쪽 모두 그대로다.

### ★ 벽 병합은 '합치면 한쪽이 사라지는 속성' 을 먼저 본다
`merge_collinear_walls` 는 합친 뒤 **한쪽 레코드의 값을 그대로 쓴다.** 그래서
`_merge_compat_key`(= `z_base` 를 `FLOOR_TOL_MM` 으로 양자화한 값 + `overrides` **전체**)가
버킷 키에 들어간다. 다르면 애초에 비교되지 않으므로 쌍별 검사보다 싸고, 연쇄 사슬도
안 끊긴다. 합성 케이스로 재현한 것: 위층 벽이 아래층에 흡수돼 소멸 · 4000mm 파라펫이
2800mm 로 · 조적이 콘크리트로. **셋 다 형상은 멀쩡하고 검사도 통과한다.**
`overrides` 를 항목별로 열거하지 않는 이유는, 항목이 늘 때마다 여기를 고쳐야 하고
안 고치면 그게 다음 버그이기 때문이다.

### ★ 두께의 우선순위 — 선언 → **믿을 수 있는** 실측 → 기본값
`geom_contract.width_of` 하나가 정한다. 여기 표를 코드에 다시 구현하지 말 것.

| 순위 | 출처 | 비고 |
|---|---|---|
| 1 | `overrides.width` (layer_map 선언) | 적어 준 값이 이긴다 — stack 레벨 `height` 와 같은 규약 |
| 2 | `width_detected` (면선 페어링 실측) | **단, `review_reason` 이 `UNTRUSTED_WIDTH_REASONS` 면 건너뛴다** |
| 3 | `params[cat].width` | |

`layer_map` 의 `width` 는 **레이어에 한 번 적는 기본값**이라 한 레이어에 여러 두께가
섞이면 못 적는다. 실측(지하3층 건축평면): `WALL|벽|CON` 이 200mm 를 선언했지만 도면에는
200·250·300·400·450mm 가 섞여 있어 **벽 280개가 실측을 무시당하고 있었다.**
→ 그 행의 `width` 를 **비웠다**. 이제 실측이 쓰인다(200×372 · 250×123 · 450×41 · 400×33 …).

선언을 비우면 **`thin_pair` 가 드러난다** — 종전엔 선언 200mm 이 50mm 오결합을 우연히
가려 주고 있었다. 드러난 뒤에는 **`pair_min` 으로 오결합 자체를 막는다**: 그 행에
`pair_min=67`(A-WALL 중앙값 200 의 1/3)을 주자 50mm 벽 26 → 3, 그중 16개가 제 두께
100mm 로 붙었다(thin_pair 26 → 7 · width_conflicts 4 → 1 · needs_review 180 → 161 ·
벽 672 → 667). 줄어든 5개는 잃은 게 아니라 **합쳐진 것**이다 — 한 벽을 50mm 오결합
둘로 잡던 것이 100mm 하나가 됐고, 면선 커버리지는 98.5 → 98.4 에 미커버 면선 0 이다.
한 행이 여러 레이어를 덮으면 **min 을 쓴다**(A-CON 은 83 이 제안값이지만 결과가 같고,
높은 쪽을 쓰면 A-WALL 의 70mm 벽 하나가 말없이 사라진다). 그래서 `width_of` 가 파서가 못 믿겠다고 표시한 실측을 건너뛰고 3순위로
떨어뜨린다. 어긋나는 나머지는 `width_conflicts` 에 `(레이어, 선언, 실측, 개수)` 로 남는다
(280 → 4). 신뢰 판정은 `paired` 이고 `thin_pair` 가 아닌 것 — `single_offset` 은 중심선
자체가 추정이라 두께도 추정이다.

### ★ `single_offset` 을 `pair_max` 로 줄이려 하지 말 것 — 측정해 봤고 더 나빠진다
`needs_review` 를 보면 대부분이 `single_offset` 이라(실측 161 중 154) `pair_max` 를
올려 짝을 찾아 주고 싶어진다. **하지 마라.** 실측(지하3층):

| `pair_max` | single_offset | needs_review | >500mm 벽 | 그중 **덩어리**(길이<2×두께) |
|---|---|---|---|---|
| 500(기본) | 154 | 161 | 0 | — |
| 700 | 61 | 68 | 73 | **70 (96%)** |
| 1000 | 19 | 25 | 121 | **118 (98%)** |

숫자는 전부 좋아 보인다 — 면선 커버리지도 98.4 → 99.0 으로 오른다. 그런데 새로 생긴
>500mm 벽의 98% 가 **두께보다 짧다**(길이 중앙 400mm, 대조군 1440mm). 두께 800mm ·
길이 400mm 는 벽이 아니라 기둥 옆이나 코너에서 남의 면선을 빨아들인 덩어리다.
진짜 벽 후보는 **3개(12.7m)뿐**이고 그 3개는 700 과 1000 에서 같다 — 즉 500 위로
올려서 얻는 것은 실질적으로 없고, 정직한 경고 135개를 조용히 틀린 덩어리 118개와
맞바꾸는 것이다. `needs_review` 가 161 → 25 로 떨어지니 **개선처럼 보이는 게 더 나쁘다.**

`single_offset` 은 허용치 문제가 아니다. 이것들은 **짧은 토막**이다(길이 중앙 600mm,
paired 는 1470mm · 총연장 148.7m 대 1116.7m). 벽 마구리·문선 리턴처럼 맞은편 면선이
짧아 투영 겹침이 안 나오는 자리다. 파서는 이미 정직하게 처리한다 — 표시하고,
중심선을 선언 폭으로 오프셋해 추정하고, `review_reason=single_offset` 을 남긴다.

`preview.py` 는 `js_constants()` 가 주입하는 **`gcWidthOf`** 로 같은 값을 쓴다
(종전엔 두 곳에서 서로 다른 순서로 재구현해, `thin_pair` 벽이 **화면 50mm / 빌드 200mm**
였다). 규칙을 JS 로 다시 쓰지 말 것.

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

### ★ EID — 부재를 특정하는 열쇠
`element_eid(prefix, _sigs + _span_sigs)`.
- `_sigs` = 이 부재를 만든 **원본 엔티티** 시그니처 → 파라미터 변경에 불변.
- `_span_sigs` = 그 레코드가 원본 면선에서 차지한 **구간**, **생성 시점에** 부착.
  없으면 긴 면선 한 쌍에서 잘린 N개 세그먼트가 전부 같은 EID 를 받는다
  (실측: 벽 682개 중 357개, 52%). **반드시 생성 시점 값이어야 한다** — `points` 는
  나중에 `_merge_two_segments`·`snap_wall_corners`·`heal_wall_junctions` 가
  centerline 값으로 덮어쓰고, `single_offset` 의 centerline 은 `±선언폭/2` 오프셋이라
  선언 폭을 따라 움직인다(실측: 폭 200→400 에서 219개 이동).
- `closed`(폴리곤 1:1)·`axis`(DIMENSION 부재)는 잘리지 않으므로 구간을 안 붙인다 —
  불필요한 EID 변경은 그 자체가 고아를 만든다.
- 실측: 벽 EID 중복 263 → 10 → **0**(마지막 10개는 좌표까지 같은 진짜 중복 벽이라
  위 '중복 부재' 절의 규칙으로 버린다). 골든의 `eid_collisions` 가 감시한다.

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

### ★ z 기준면(datum) — 규약의 유일한 출처는 `geom_contract.py`
**이 표를 코드에 다시 구현하지 말 것.** 소비자는 `geom_contract.z_range(cat, rec, params)` 만 호출한다.
규약이 4곳에 흩어져 있다가 preview 가 슬래브를 '하단' 으로 읽어 보/슬래브가 한 두께 떠 보였고,
그 증상을 원인 미상으로 두고 `z_base` 에 두께를 더하는 보정을 **데이터에** 넣어 다른 소비자까지 오염시켰다.

| 카테고리 | 키 | 기준면 | 뜻 |
|---|---|---|---|
| wall / column / zone / opening / equipment | `z_base` | **하단** | 위로 `height` |
| slab / **beam** | `z_base` | **상단** | 아래로 `thickness`(보는 춤). 구조도면이 주는 값이 FL(상단)이기 때문 |
| pipe / duct / tray | `elevation` | **중심축** | 위아래로 절반씩. MEP 는 키 이름부터 다르다 — 개명하면 `cfd_export`·`boq_export` 가 깨진다 |

실무 규약: **콘크리트 보 상단 = 슬래브 상단**, 춤은 슬래브 두께를 포함한다.
(철골 거더는 반대로 **상단 = 데크 하단**.)

폴리곤 감김도 같은 계약이다: `Arch.makeStructure` 는 **면 법선 방향으로** 압출하므로 CW 폴리곤은
−Z 로 밀린다. 닫힌 폴리곤은 반드시 `geom_contract.ccw()` 를 통과시킬 것.

## layer_map.csv 컬럼
`pattern,category,width,height,thickness,opts`
- `pattern`: 정규식 (re.search, 대소문자 무시). **선매칭 우선** — 좁은/제외 패턴을 넓은 패턴 위에 둘 것.
  (`배수판_벽체` 는 `벽` 을 포함하므로 `WALL|벽|CON` 아래에 두면 영원히 가려진다. 파서가
  `result["shadowed_layer_rules"]` 로 이런 규칙을 잡아낸다.)
- `category`: `wall` | `column` | `slab` | `beam` | `zone` | `opening` | `pipe` | `duct` | `tray` |
  `equipment` | `ignore`. **오타는 로드 시점에 `LayerMapError`** — 조용히 새 버킷이 생기지 않는다.
  `ignore` 는 정식 카테고리다: 세고 버린다(`result["ignored"]`).
- `width/height/thickness`: mm, 빈칸이면 params 기본값 사용
- `opts`: `키=값;키=값` — 컬럼을 늘리는 대신 여기로 확장한다. **모르는 키는 오류**(오타난
  허용치를 조용히 무시하지 않는다). 실제 적용값은 `result["tolerances_effective"]` 에 기록되어
  산출물이 자기 튜닝을 스스로 말한다 — 몽키패치 방지 장치.

| opts 키 | 뜻 |
|---|---|
| `pair_max` / `pair_min` | 이 레이어의 평행선 페어링 간격(mm). 보/거더 외곽선은 벽보다 훨씬 넓다(500~2500). 교차 레이어 쌍은 **두 값의 min** |
| `from=dim` | 이 레이어의 **형상 출처가 DIMENSION**이라는 뜻. 실무 구조도면은 부재를 치수선으로 긋고 텍스트를 부재명으로 덮어쓴다. 끝점은 `defpoint2`(13)→**`defpoint3`(14)** — `defpoint`(10)는 치수선이 그려질 위치일 뿐 측정점이 아니다. ★ 같은 레이어의 **비-DIMENSION 엔티티는 치수 장식**(보조선 LINE·화살표 INSERT)이라 제외하고 `dimension_decoration_skipped` 로 센다. 실측: 부재 225개 옆에 장식 694개 — 안 거르면 길이 500mm 화살촉이 보로 세워진다 |
| `member_re` | `from=dim` 에서 부재명으로 인정할 정규식. 일반 가드로는 부재명(`RAB1D`)과 철근상세 기호(`Lt`,`ta`)를 구분할 수 없다 |
| `schedule=<레이어>` | 부재일람표 레이어. `member_name` → 실제 폭×춤 조인 |
| `centerline=<판정>` | 이 레이어에서 **중심선만** 부재로 쓴다. `color:1`(ACI 색 번호) 또는 `linetype:CENTER`. 나머지는 외곽선이라 세고 버린다(`outline_skipped`). 값 형식이 틀리면 **로드 시점에 `LayerMapError`** — 오타난 판정 기준을 조용히 안 먹으면 외곽선이 그대로 부재가 되는데 그건 형상이 멀쩡해 보여 검사에 안 걸린다 |
| `elevation=<판정>` | **평면도에는 고저가 없다.** MEP 부재의 z 를 선언한다 — `top:<mm>` 은 부재 **상단**을 그 높이에, `center:<mm>` 은 중심축을 그 높이에. 실측 z 를 **이긴다**(두께의 `overrides.width` 와 같은 규약). 적용되면 `elevation_source="declared"` 로 자기보고. 값 형식이 틀리면 로드 시점에 `LayerMapError` |
| `material=<이름>` | `IfcMaterial` 이름. **적힌 것만 붙는다** — `wall`→콘크리트 같은 카테고리 추정은 조적벽에서 바로 틀리고, 물량·내화·열관류가 전부 그 위에 얹혀 형상 오류보다 오래 산다. 재질은 `overrides` 를 타고 나간다(벽의 병합·체이닝이 보존하는 필드가 그것). 이름당 `IfcMaterial` 1개 + `IfcRelAssociatesMaterial` 1개, 부여 수는 `build.json` 의 `materials` 에 자기보고 |

```
# 보/거더: 외곽선은 넓게 페어링, 축선은 DIMENSION, 단면은 일람표에서
^(AU|STEEL)_(BEAM|GIRDER)$,beam,,,,pair_max=1800;from=dim;schedule=BEAM_SCHEDULE
^BEAM_SCHEDULE$,ignore,,,,
```

### 부재일람표 조인 (`schedule=`)
도면은 부재의 **위치**만, 일람표는 **단면**만 준다(`RAG11B` → `400x1800x25x25`).
`schedule_table.py` 가 TEXT 격자를 표로 복원해 이어붙인다.
- 표기 관례가 표마다 다르다: `H 800x300x14/26` = **춤×폭**, `350x1100x8x8` = **폭×춤**.
  뒤집으면 납작한 보가 나오므로 결과에 `notation` 을 남긴다. 폭>춤이면 경고.
- 한 레이어에 표가 여러 개다(실측 `BEAM_SCHEDULE`: TEXT 291개 = 표 8개). 제목 행 기준으로 분리한다.
- TEXT 는 `align_point` 가 진짜 앵커다(중앙정렬). `insert` 로 열을 묶으면 문자열 길이에 따라 섞인다.
- **매칭 실패는 기본값으로 때우지 않는다** — `needs_review` + 경고. 일람표가 `from=dim` 의
  2차 오용 가드로도 작동한다(상세도 기호는 일람표에 없으므로 전부 미매칭으로 드러난다).

## block_map.csv 컬럼 (Phase 2)
layer_map.csv와 **동일 형식**이나 `pattern`이 **블록(INSERT)명**에 매칭.
- INSERT는 `block_map` 우선 분류 → 미매핑이면 INSERT 레이어로 `layer_map` 폴백 → 그래도 없으면 `미매핑 블록` 경고.
- 분류된 INSERT는 `virtual_entities()`로 블록 내부 형상을 **실좌표 explode** 후 변환.
  - `column`: explode된 closed polyline/circle만 채택. 없으면 `width`(정사각 한 변)로 위치+회전 박스 마커.
  - `opening`: explode된 circle 우선. 없으면 `width`(지름)로 원 마커.
  - 그 외 카테고리: explode 레코드 그대로.
- `-b/--blockmap` 인자로 지정. 생략 시 기본 블록 규칙(COL/기둥/PILLAR→column, DOOR/문·WIND/창→opening).

### ★ `ignore` 는 **두 분기 모두**에서 버려야 한다
`ignore` 는 세고 버리는 정식 카테고리인데(`result["ignored"]`), 그 검사가 오랫동안
**비-INSERT 분기에만** 있었다. 블록은 그대로 `elements["ignore"]` 버킷에 실려 나갔다 —
실측(아파트 단위세대 건축평면): 가구·위생기구·실외기가 전부 블록이라 **912개**가
JSON 에 실렸고 V003 이 빌드를 막았다. 같은 규약을 두 분기가 나눠 가지면 한쪽만
고쳐진다(주석에 "종전엔 버킷이 생겼다" 는 수정 기록이 남아 있는데, 그 수정이 INSERT
분기에는 안 갔다). `test_ignore_layer_never_becomes_an_elements_bucket` 이 둘 다 고정한다.

### 기둥 LINE 경계 복원과 POLYLINE 가져오기

같은 기둥 레이어의 모든 점을 convex hull/AABB로 감싸면 떨어진 부재 사이와 오목한
코너가 고체로 채워진다. `recover_column_outlines()`는 레이어·높이·설정별로 원본 선이
실제로 닫은 면만 복원하며, 새 선을 만들거나 틈을 스냅하지 않는다. 복원 면은
`column_outline_inferred` 검토 대상이다. 닫히지 않거나 구멍을 가진 경계는 원본 선과
출처를 `column_boundary_unresolved`로 유지하고 V005가 출력 전에 차단한다. 단순히
기둥 레이어에 있다는 이유로 내력벽 분류를 확정하지 않는다. 기존 닫힌 기둥은 유지한다.
내부 연결선으로 쪼개진 면도 합집합으로 채우지 않는다. 공유 경계를 가진 후보는 검토로
남긴다. 정확히 겹친 선은 면 복원 때만 중복 제거하며 출처는 모두 보존한다. 복원 기둥의
EID 구간 식별에는 원본 Z 평면을 포함하여 같은 XY의 위·아래 부재 수정이 충돌하지 않게 한다.

classic 2D POLYLINE에는 `points_in_wcs()`가 없다. `mep_paths.extract_curve()`는
`points()`의 XY, 부모 `elevation.z`, `ocs().to_wcs()`로 원본 시작점을 복원한 뒤
가상 LINE/ARC의 방향을 맞춘다. 반복 직선 꼭짓점은 길이 0인 경로 구간을 만들지 않으며
원본 사양에는 남긴다. 일치 끝점의 bulge는 해석 불가로 진단한다. 경사진 원본 경로는
기존 계약대로 평탄화하지 않고 검토 진단을 남긴다.

회귀: `tests/test_import_boundaries.py`는 분리된 두 기둥, 오목한 경계, 열린/중첩 경계,
높이 분리, bulge 부호·뒤집힌 OCS·고도·반복 정점과 인벤토리 격리를 합성 도면으로 검사한다.

### ★ 아파트 단위세대 평면 — 레이어 이름이 부재를 안 알려 준다
실측(74타입): 벽식 구조라 **기둥이 없는데 `A-COL` 이 200mm 내력벽**이다. 기본 규칙의
`COL|기둥` 이 그걸 column 으로 보내 벽이 20개(23.7m·전부 90mm)만 남았다. 평행선 간격을
재면 바로 갈린다 — `A-COL` 200mm×20 · `Parti` 100·110mm · `A-WALL` 90mm×21 ·
`A-FIN` 200·210mm(마감선이라 벽으로 넣으면 이중계상). 셋을 wall 로, `A-FIN` 을 ignore
로 두면 벽 104개 123.8m, 면선커버 100%, 검토필요 3.

**문·창은 블록이고 이름이 폭을 담는다**(`D-900`·`PD-750`·`FSD-1100`·`W-1200`·`W-3600`).
`block_map` 에 없으면 레이어로 폴백해 **문짝 선 조각이 개구부가 된다**(실측: 폭 150·
180mm 짜리 8개, 그중 2개는 겹쳐서 V106 error). 이름으로 매핑하면 750·900·1000·1100·
1200·1800·2300·3600mm 16개가 나오고 전부 벽에 붙는다.
- 동봉 `block_map.csv` 가 `(^|\$)(W|AG)-\d\d\d\d?$` · `(^|\$)(P|FS|WD|S|F)?D-\d\d\d\d?$` 로 받는다(바인드된
  외부참조는 이름 앞에 `XREF…$0$` 가 붙는다). 행을 직접 쓸 때는 선매칭 우선이라 `WD-` 를 `D-` 보다 위에 둘 것.
  행에 폭이 없으면 `insert_to_records` 가 **이름 끝 '-숫자'(300~6000)** 를 폭으로 쓰고 `width_source: "block_name"`
  을 남긴다 — 종전엔 전부 900 이었다(실측: 750·1000·1100 문 8개).
- ★ CSV 패턴에 **쉼표를 쓰지 말 것.** `\d{3,4}` 의 쉼표가 열을 갈라 로드가 `LayerMapError`(카테고리 `4}$`)로 죽었다.
- 블록 문이 선 자리의 문짝·문틀 선도 레이어 규칙(`DOOR|문`)으로 개구부가 된다. `drop_opening_fragments` 가
  **자기 폭의 두 배 이상인 개구부 폭 안의 선 조각(원 제외)** 과 좌표까지 같은 중복(`_geom_key`)을 버리고
  `opening_fragments_dropped` 로 센다. 원은 남긴다 — 50~200mm 는 설비 슬리브와 크기가 겹친다.
  실측: 단위세대 문짝 조각 8개 → 0(개구부 16 → 13, 붙일 벽 없음 2 → 0) · 지하3층 1912mm 양개문 틀 안의
  문짝선(868·874mm, 점 2개) 8개(개구부 34 → 26 — void 는 틀이 이미 뚫는다).

### ★ 칸막이 보드선 — 같은 레이어 벽 안에 겹쳐 선 안쪽 짝
칸막이는 벽면 두 줄 안쪽 10~20mm 에 보드·마감선을 한 줄씩 더 긋는다(실측 선 간격 10mm 66곳 · 20mm 36곳 ·
100mm 30곳). 전역 `WALL_PAIR_MIN_MM`(1mm, 밀착 철골용)로 짝지으면 그 선끼리 **폭 10mm '벽'** 이 되고(실측:
칸막이 47개 중 40개), 간섭 목록이 그걸 '두께 < 50mm 오결합 의심' 14건으로 채웠다.
- 프로필 `architecture_layers` 의 벽 행은 `pair_min_mm` 기본 **50**(설정 화면 '벽면 짝 최소 간격') → `_opts.pair_min`.
  layer_map `opts` 의 `pair_min` 과 같은 자리다.
- 그래도 0/10/90/100 네 줄은 80·90·90 짝이 **같은 자리에 겹쳐 선다.** `collapse_nested_wall_pairs` 가 EID 중복
  제거 직후(개구부 링크·수정 주입 앞) **같은 레이어 · 같은 층 · 두께 차 ≤ 50mm · 평면 90% 이상 포함**인 열린 짝
  벽을 버리고 가장 두꺼운 것을 남긴다(`nested_pairs_collapsed`). 다른 레이어끼리(A-CON 450 ∥ 상부골조 400)와
  두께가 크게 다른 겹침(두 칸막이 바깥선끼리 짝지은 440mm 덩어리)은 고르지 않는다.
- 실측(단위세대 난방·환기): 칸막이 47 → 17, 10mm 벽 40 → 2(남은 2개는 `thin_pair` 검토) · 통합 간섭 목록
  24 → 16(오결합 의심 14 → 0, 대신 100·110mm 칸막이 하부 통과 4 · 관통 2). 지하3층 골든: 벽 667 → 662 — 5개 모두
  **같은 원본 선에서 나온** 같은 레이어 벽 안에 100% 들어 있었다(4개는 폭·길이까지 같고 1개는 200 이 250 안).
- 한계: 그리디 페어링이 바깥면(100) 대신 보드선-면선 짝(90)을 고를 수 있다 — 한 벽이 되는 것까지만 보장한다.
  두 칸막이 바깥선끼리의 440·445mm 덩어리는 남는다.

### ★ explode 된 부재의 `layer` 는 **규칙을 정한 레이어가 아니다**
블록이 미매핑이면 **INSERT 가 놓인 레이어**로 폴백해 분류하는데(위 규칙),
`virtual_entities()` 로 펼친 레코드의 `layer` 는 **블록 안쪽 엔티티**의 레이어다.
그래서 카테고리·`overrides`·`opts` 는 A 레이어가 정했는데 이름표는 B 로 나간다.

실측(지하3층): 모델스페이스에 `101/102/103동 상부골조B2_0106` INSERT 3개가
**`A-CON` 레이어**에 놓여 있다. block_map 에 걸리는 이름이 아니라 A-CON 으로
폴백해 `wall` 이 되고, 펼친 벽 230개가 `layer="상부골조"` 를 단다. `상부골조` 는
**layer_map 어느 행에도 안 걸리는 이름**이다(모델스페이스 직속은 LINE 1개뿐 —
그 1개가 '미매핑 레이어: 상부골조(1)' 이다).

→ 그래서 `thin_pairs`·`계단 의심`·`width_conflicts` 는 `상부골조` 를 말하지만,
그 이름으로 layer_map 에 행을 써 봐야 **아무 일도 안 난다.** 고칠 자리는 `A-CON`
행이다. 얇은 오결합 경고가 이제 그걸 직접 말한다(`_rule_layer`).

- 이 사실은 **레코드가 아니라 레이어의 성질**이라 explode 시점에 한 번만 모은다.
  레코드마다 들고 다니면 면선 페어링이 새 레코드를 만들며 흘린다(실측 230 → 36:
  닫힌 폴리곤만 살아남는다) — 그러면 레코드 생성 지점 5곳을 전부 고쳐야 한다.
- 넘겨주는 건 그 레이어가 **자기 규칙을 가질 수 없을 때뿐**이다. A-CON 처럼 제 행이
  있는 레이어는 블록에서 나온 벽이 섞여 있어도 제 행으로 고쳐야 한다(안 그러면
  A-CON 이 '#CHK_U_250212 행을 고치라' 고 한다 — 그 레이어에도 블록이 하나 있다).

## 실행 순서

### 1. 의존성
```
pip install ezdxf shapely
```

### 2. 샘플 생성 (또는 실제 DXF 사용)
```
python make_sample_dxf.py
```

### 3. 도면 인벤토리 확인 (빌드 전 점검)
```
python dxf_parser.py sample_plan.dxf --scan
```

### 4. 파싱
```
python dxf_parser.py sample_plan.dxf -m layer_map.csv -b block_map.csv -o geometry.json

# 부재일람표를 CSV 대신 CLI 로 지정(= opts 의 schedule= 과 동일 효과, 반복 가능)
python dxf_parser.py plan.dxf -m layer_map.csv --member-schedule BEAM_SCHEDULE -o geometry.json

# 일람표만 따로 확인 (표 구조·단면 파싱 점검용)
python schedule_table.py plan.dxf --layer BEAM_SCHEDULE
```

### 5. FreeCAD BIM 빌드 (freecadcmd 필요)
freecadcmd 는 argv 의 `.json`·파일명을 '열 문서'로 오인하므로 **환경변수로 전달**한다.
```
# Windows (PowerShell)
$env:MEP_GEOMETRY="geometry.json"; $env:MEP_OUT="out_model"; & "C:\Program Files\FreeCAD 1.1\bin\freecadcmd.exe" freecad_builder.py

# Linux/macOS
MEP_GEOMETRY=geometry.json MEP_OUT=out_model freecadcmd freecad_builder.py
# → out_model.FCStd, out_model.ifc
```

### 6. 3D 미리보기 (FreeCAD 불필요, 무설치·오프라인)
```
python preview.py geometry.json            # 파싱된 json
python preview.py sample_plan.dxf -m layer_map.csv -b block_map.csv  # DXF 즉시
# → <입력>_preview.html (three.js, 카테고리·신뢰도 색 + 클릭 수정)
```
브라우저에서 요소 클릭 → 카테고리/치수 수정 → `edits.json` 다운로드 →
재파싱 시 EID로 재적용(수정 보존):
```
python dxf_parser.py plan.dxf -m layer_map.csv -o geometry.json --edits edits.json
```

### 6.5 Blender 로 보기 (렌더·4D 공정·간섭 검토)
**새로 만들지 않는다.** [Bonsai](https://bonsaibim.org/)(구 BlenderBIM)가 IFC 네이티브로
4D 시공 시퀀스(`IfcTask`)·clash detection·QTO 를 전부 한다. 우리는 IFC4 를 이미 내보낸다.
```
Blender > Preferences > Get Extensions > "Bonsai" 설치 → File > Import > IFC
```
MEP 는 `IfcPipeSegment`/`IfcDuctSegment`/`IfcCableCarrierSegment`/`IfcDistributionElement`
로 나가므로 뷰어에서 계통별 필터·물량이 된다(종전엔 전부 `IfcBuildingElementProxy` 였다).
배관의 단일 평면 직선은 `Arch.makePipe`, 다점 경로는 공통 정다각형 마이터 관으로 생성한다.
빌더가 `Pset_MEPParser` 로 QA 속성을 함께 내보내므로 뷰어에서 부재를 클릭하면
`EID` · `Layer` · `MemberName` · `Section` · `Pairing` · `WidthDetected` ·
`NeedsReview` · `ReviewReason` 이 보인다. **`NeedsReview=True` 로 필터하면 얇은
오결합이 그대로 잡힌다.** `EID` 는 `edits.json` 으로 되돌리는 열쇠다.
(주: `App::PropertyString` 는 FreeCAD 문서에만 남고 IFC 로 안 나간다 —
반드시 `set_ifc_props()` 를 거칠 것. V105 가 이걸 검사한다.)

**4D 공정**은 Bonsai 가 재생만 할 뿐 "이 공정이 이 층의 부재들" 이라는 연결은 모른다.
그것만 `ifc_4d.py` 가 얹는다(부재 594개를 손으로 고르지 않게):
```
python ifc_4d.py out_model.ifc schedule.csv -o out_4d.ifc
# schedule.csv: task,level,start,finish  ← level 은 IfcBuildingStorey.Name(우리 stack.json 의 label)
# → Bonsai > Sequence > Visualise Work Schedule Date Range
```
`level` 을 비우면 건물 전체. **IFC 에 없는 층 이름은 `[!]` 로 실제 층 목록과 함께 보고한다**
— 오타 한 번에 연결 0건인 채로 성공처럼 끝나는 것을 막는 유일한 신호다.

### 7. 단일 .exe 빌드 (현장 PC = Python 불필요)
개발 PC(Python 3.11 권장)에서 1회 빌드 → 현장 PC 더블클릭 실행.
```
build_exe.bat            # = py -3.11 -m pip install ezdxf shapely pyinstaller pytest
                         #   + py -3.11 -m pytest tests -q        ← 게이트
                         #   + py -3.11 -m PyInstaller mep_parser.spec --noconfirm
# → dist\MEP-Parser.exe (onefile, 윈도우 GUI, ~34MB)
dist\MEP-Parser.exe --selftest   # 헤드리스 스모크(번들 건전성, exit 0=정상)
```
★ **선택 의존성이 없는 PC 에서 테스트는 `skip` 이어야 한다 — 에러면 게이트가 통째로
막힌다.** 실측: 깨끗한 Python 3.11(빌드용)에서 `ifcopenshell` 16건 + `mcp` 1건이
에러로 터져 .exe 가 안 만들어졌다. 두 모듈 다 **없으면 `sys.exit(1)`** 하므로
가드는 `except (ImportError, SystemExit)` 이어야 한다(`ImportError` 만 잡으면 안 걸린다).
규약은 `tests/test_ifc4d.py` 와 같다: `unittest.SkipTest` 를 올린다.
그래서 빌드 PC 의 게이트는 269 통과 / 21 미실행이다 — IFC 내보내기·검증 계열은
**안 돈다.** 거기까지 게이트로 덮고 싶으면 위 pip 줄에 `ifcopenshell` 을 더한다.
- `mep_parser.spec`: layer/block csv·freecad_builder.py·sample·vendor 동봉,
  ezdxf/shapely collect_all, matplotlib/anthropic/vision 제외(graceful 폴백).
- 런타임 리소스는 `resource_path()`(`sys._MEIPASS`)로, 편집 csv는 `user_csv()`로
  exe 폴더에 영구 사본 보장. windowed(.exe)는 `sys.stdout=None` → None-safe 가드 필수.

### ★ 환기·설비 평면도 — 덕트는 **외곽선 2줄 + 중심선 1줄**로 그려진다
건축평면도와 도면 문법이 다르다. 우리 MEP 추출은 **중심선**을 전제하는데
(`annotate_mep` 가 축선에 치수를 붙인다), 설비 도면은 덕트를 외곽선으로 그리고
중심선을 따로 얹는다. 그대로 먹이면 한 덕트가 3중으로 계상되고 끝막이 선까지
덕트가 된다 — 실측(아파트 단위세대 환기평면): **덕트 179개 207.6m → 실제 45개 67.5m.**

**도면이 이미 중심선을 표시하고 있다.** 실측: SA·RA 179줄 중 중심선 45줄이
`color=1`(빨강), 그중 6줄은 `linetype=CENTER` 도 함께(색이 상위집합). 그래서
`opts` 의 `centerline=color:1` 로 **선언**해서 쓴다 — 추정하지 않는다.

★ **면선 페어링으로 대신할 수 없다.** 세 줄이 나란하면 중심선이 외곽선과
**절반 간격**으로 먼저 짝지어져, 폭이 절반인 중복 덕트가 나온다(실측: SA·RA 를
wall 로 돌렸더니 106개가 폭 50·102mm — 실제는 100·204mm). 측정하고 접었다.

**ELLIPSE 는 플렉시블 덕트다.** `entity_to_record` 가 LINE·LWPOLYLINE·POLYLINE·
CIRCLE·ARC·SPLINE 만 알아서 216개가 `unhandled` 경고만 남기고 빠졌다.
`flattening(ELLIPSE_SAG)` 은 제어점이 아니라 **곡선 위의 점**을 주고 부분 타원도
따라간다(SPLINE 의 `control_points` 근사와 다르다).

**덕트의 고저는 `top:` 으로 선언한다 — 중심축이 아니라 상단이다.**
현장 규칙: *환기덕트는 천장 슬래브에 딱 붙고, 그 아래로 스프링클러 배관·욕실 배기덕트가
지난다.* 계통마다 덕트 높이가 다르므로(실측 SA 150 · RA 200) **상단**을 맞춰야 그 규칙이
지켜진다 — 중심축을 계통마다 계산해 적어 주는 것은 같은 규칙을 두 번 쓰는 것이다.
`elevation=top:2690` 하나면 SA 는 중심 2615, RA 는 2590 이 되고 상단은 둘 다 2690 이다.

평면도의 z 를 그냥 쓰면 **덕트가 바닥에 깔린다**(실측: 덕트 45개 전부 elevation=0).
그 상태의 `check_clashes` 결과는 허상이다 — 실측에서 간섭 2건이 나왔다가 덕트를
제 높이로 올리자 0 건이 됐다.

**★ 이 도면으로 벽을 만들지 말 것.** 환기평면도에는 벽 레이어가 없다. 배경 도면이
`BACK` 한 레이어에 벽·가구·창호기호·해치를 전부 담고 있어, wall 로 매핑하면
**4313개가 나오는데 길이 중앙이 1mm 이고 500mm 넘는 건 336개뿐**이다. 벽이
필요하면 같은 세대의 건축평면도를 `stack.json` 에 함께 넣는다.

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

## zone 귀속 방식
zone은 별도 파일 없이 **DXF의 `A-ZONE` 레이어**(closed LWPOLYLINE)를 직접 사용.  
shapely point-in-polygon으로 각 요소의 중심이 어느 구역인지 자동 판정 → `"zone": 0` (인덱스).

## 관련 기존 프로젝트
- `c:\AI program\MEP ems\core\` — 배관 물량산출·BOQ 자동화
  - `dxf_inventory.py` — 레이어 인벤토리 (--scan 패턴 원형)
  - `dxf_pipe_extractor.py` — 배관 선분 추출
  - `classification_engine.py` — fuzzy/confidence 분류

## 다음 개선 과제 (우선순위순)
실무 도면에서 "선 → BIM 객체" 간극을 메우는 것이 핵심. 비전·RAG보다 아래가 우선.
- [x] **[1] 평행선 쌍 → 벽 중심선+두께 검출** — 실무 도면은 벽을 양면 2선으로 그림. **(완료)**
      `dxf_parser.detect_wall_pairs()`: 세그먼트 분해 → 평행/수직거리/투영겹침 판정 → 그리디 매칭.
      paired는 `centerline`+`width_detected`+`confidence`, single은 중심선 벽 + `needs_review=true`.
- [x] **[2] BLOCK(INSERT) 처리 + `block_map.csv`** — 기둥·문·창은 보통 블록 참조로 삽입됨. **(완료)**
      `insert_to_records()`: 블록명 분류 → `virtual_entities()` 실좌표 explode → 형상 채택, 없으면 박스/원 마커 폴백.
      `sample_blocks.dxf`로 검증: 기둥 4 explode 정확, 문 2 마커 폴백, FreeCAD 빌드 columns=4 통과.
### 로드맵 재정렬 (2026-05-29 사용자 회의 결과)
4자 회의(건설시니어 A·프로그래머 B·현장소장 C·MEP담당 D)에서 방향 검증.
**결론: 기술 방향(결정론 엔진 + `geometry.json` 단일 계약)은 맞다. 우선순위가 틀렸다.**
실사용자(A·C·D)가 CLI를 못 써 엔진이 좋아도 0명이 쓴다 → 사용성이 1순위.
MEP는 "추출은 곧, 3D 빌드는 나중"으로 분할(D 합의). 스키마 안 엎고 카테고리 자리만 선점.

- [x] **[2.5] 사용성 껍데기 + 수정 루프** — **완료(1차분, 2026-05-29)**. `mep_gui.py`+`run_gui.bat`:
      더블클릭 실행, 파일선택→①스캔→②파싱→③needs_review 목록 수정(폭/높이 override·저장)→④3D빌드.
      tkinter(무의존). freecadcmd 자동탐지+subprocess. 빌드는 '저장된 geometry.json'에서 → 수정 보존.
      한계: DXF 재파싱은 수정 덮어씀(요소 매칭 미구현). 정식 라운드트립은 후속.
- [x] **[2.7] MEP 추출 트랙(데이터만)** — **완료(2026-05-29)**. `geometry.json`에 `pipe/duct/tray/equipment`
      카테고리 신설(스키마 보존, 기존 빌더 무영향). 중심선 추출은 기존 `entity_to_record` 재활용,
      `_entity_elevation()`로 Z 고저 보존 + `annotate_mep()`로 pipe→diameter, duct/tray→width_mm·height_mm,
      장비는 블록 explode. `sample_mep.dxf` 검증(pipe2/duct1/tray1/equip2, 고저·치수 정확). **3D 빌드는 [5].**
      (주: `dxf_pipe_extractor.py`는 core.* 강결합이라 import 대신 동등 로직 자체 사용.)
- [x] **[3] confidence 기반 기하 분류기 + 미매핑 fuzzy 제안** — **완료(2026-05-29)**.
      `classify_geometry()`: 레이어명 무시, 기하만으로 추정(소형 정사각 닫힘폴리→column 0.85,
      대형 닫힘폴리→slab, 중형→zone, 열린선→wall 모호, 소형원→column/개구부 모호).
      `fuzzy_layer_suggestion()`: difflib로 레이어명 vs 규칙 토큰 유사도(`pattern_engine.build_fuzzy_suggestions` 차용).
      `build_suggestions()`: 미매핑 레이어별 기하 투표 + 이름 fuzzy → `result["suggestions"]`.
      **자동 매핑 안 함, 제안만**(A가 검토해 CSV 작성). CLI·GUI 로그에 "[제안]" 출력.
- [x] **[4.0] 벽 토폴로지 정비 (collinear 재병합)** — **완료(2026-05-29)**. `merge_collinear_walls()`:
      같은 직선(각도 2°·수직오프셋 10mm tol) 위 끝-끝 갭<50mm 세그먼트를 한 벽으로 연쇄 병합.
      쪼개진 LINE이 BIM 객체로 분절되는 것 방지([4] 개구부 boolean의 선결 조건). 설계:
      세그먼트를 '직선 키'(방향+원점수직오프셋+두께+pairing 양자화)로 버킷팅 → 버킷 내 1D 사영 정렬
      후 단일 패스 연쇄 = **O(N log N), 결정론 유지**(키·좌표 정렬). overrides 보존(빌더 치수 손실 방지),
      confidence=min, needs_review=OR. 헬퍼 `_get_normalized_direction`/`_point_to_line_distance`/
      `_check_collinear_connectable`/`_merge_two_segments`. 상수 `COLLINEAR_ANGLE_TOL_DEG=2.0`/
      `COLLINEAR_DIST_TOL_MM=10.0`/`COLLINEAR_GAP_TOL_MM=50.0`. `result["wall_merge"]={before,after}`.
      **범위 주의: 같은 직선 연쇄만. 직각 코너 틈(사각방=벽4개)은 안 메움 → 코너 스냅/miter는 [4.1] 별도.**
      검증: 3샘플 회귀(쪼갬 없어 카운트 불변=무해), 합성 단위테스트 5→4(갭50 병합·overrides 보존·코너/평행 미병합).
      성능 강화(보류): 실무 N↑ 시 결정론적 spatial-hash broad-phase(셀=WALL_PAIR_MAX_MM,
      후보 `sorted()`로 결정론 보장) — 현재 버킷 단일패스로 충분, 필요 시 도입.
- [x] **[4.1] 코너 스냅** — **완료(2026-05-29)**. `snap_wall_corners(wall_records, snap_tol=25mm)`:
      끝점 목록 x-정렬 슬라이딩 윈도우 → euclidean dist < snap_tol 쌍 union-find → 클러스터 centroid 치환.
      centerline·points 양쪽 동기화. deepcopy 로 원본 불변. O(N log N), 결정론(정렬+작은인덱스-root UF).
      `result["wall_merge"]["snapped_corners"]` = 스냅된 벽 수. 상수 `CORNER_SNAP_TOL_MM=25.0`.
      검증: 3샘플 회귀 무해(snapped=0), T자 단위테스트 gap5→centroid 2502.5 정확·무관점 불변·overrides 보존.
- [x] **[4a] 개구부 void 뚫기** — **완료(2026-05-29)**. dxf_parser: `_pt_to_seg_dist` + `link_openings_to_walls()`:
      opening 중심→벽 중심선 수직거리 < r + 벽두께/2 + 10mm → `opening["wall_indices"]=[i,...]` 태깅.
      freecad_builder: `build_walls` → `(objs, idx_map)` 반환; `apply_opening_voids(idx_map, openings, params)`:
      `Part.makeCylinder(r, h+margin)` → `wall_obj.Shape.cut(cutter)` → `wall_obj.Shape = cut` 덮어쓰기.
      ★ `doc.recompute()` 1회(Arch shape 확정) 직후 void 적용 → 이후 recompute 금지(파라메트릭 덮어씀).
      `빌드 완료: ... openings_void=N` 출력. v1 원통 커터(rectangular 개구부는 v2 예정).
      검증: unit test 링크 OK, sample_plan openings=[0],[4] 자동링크, 3샘플 회귀 무해.
- [x] **[4b] 다층 Z 오프셋** — **완료(2026-05-29)**. dxf_parser: structural 요소에 `z_base` 추가
      (`_entity_elevation` 재활용), `detect_wall_pairs`/`merge_collinear_walls`/`snap_wall_corners`에
      z_base 전파. parse() 내 floors 감지: z_base 값 100mm tol 양자화 → `result["floors"]=[{z,label}]`.
      freecad_builder: `build_walls/columns/slabs`가 `geom_contract.z_range()` 로 (z0,z1)을 받아
      `Placement.Base.z=z0` 적용. ★ 슬래브/보는 `z_base`가 **상단**이라 z0 = z_base − thickness다
      (종전 이 줄이 "z_base 적용"으로만 적혀 있어 preview 가 하단으로 오해한 것이 D6 사고).
      main()에서 `floors_info`로 루프 → `Arch.makeFloor` per level + `fl.Placement.Base.z=fz`.
      단층 폴백: floors 없으면 Level_1(z=0). 검증: 3샘플 z_base=0.0·floors=Level_1 OK, 다층 합성 2층 감지 OK.
- [x] **[4c] zone → Arch.makeSpace** — **완료(2026-05-29)**. `build_spaces()`: zone 닫힌폴리 →
      `Part.makePolygon` → `Part.Face` → `face.extrude(h)` → `Arch.makeSpace([feat])`, IFC Type=Space.
      floor 컨테이너에 포함(4b 다층 그룹핑 적용).
- [ ] **[4] (원래 메모)** → 4a ✅ 4b ✅ 4c ✅ 완료.
- [x] **[5] MEP 3D 빌드 + 간섭 검토** — **완료(2026-05-29)**. freecad_builder.py:
      `_pipe_solid`: 다점 중심선 → `Part.makeCylinder` 세그먼트 fuse(z=elevation).
      `_rect_solid`: 사각단면(width×height) → `Part.Face` rotation(`App.Rotation(Z→seg)`)·extrude 세그먼트 fuse.
      `_equip_solid`: 닫힌 폴리 footprint → `Part.Face` extrude(1000mm).
      `build_mep(doc, el)`: pipe/duct/tray/equipment → `Part::Feature` 객체. `result["mep"]` 카운트 활용.
      `check_clashes(struct_objs, mep_objs)`: `shape.common()` 볼륨 > 1mm³ → clash 목록. O(S×M).
      main()에서 recompute 이후: opening void → clash 검사 → saveAs 순서.
      출력: `[CLASH] 간섭 N건: Wall_i ↔ Pipe_j  V mm³` 또는 `[CLASH] 간섭 없음`.
      검증: sample_mep 파싱 pipe2/duct1/tray1/equip2 + 필드(elev/diam/width_mm/height_mm) 확인.
- [x] **[6b] DWG→DXF 체크리스트** — **완료(2026-05-29)**. `DWG_DXF_CHECKLIST` 상수(8개 항목):
      저장형식/단위설정/레이어/엔티티/블록/좌표계/저장전점검/변환검증. CLI `--checklist` 플래그
      (dxf 인수 없이 실행 가능, `nargs='?'`). GUI `mep_gui.py` 오른쪽 버튼 → Toplevel 스크롤 팝업.
- [x] **[6a] LLM tie-break** — **완료(2026-05-29)**. `llm_tiebreak_suggestions(suggestions, api_key)`:
      트리거: `geom_confidence < 0.7 AND name_score < 0.6` 항목만 API 호출(고신뢰도 스킵).
      모델: claude-haiku-4-5. 시스템 프롬프트: category 값만 제안, FreeCAD 코드 생성 절대 금지.
      응답: `{"category":..., "reason":..., "confidence":...}` JSON. suggestion에 `llm_guess/reason/confidence` 추가.
      **자동매핑 없음** - 사용자가 검토 후 layer_map.csv 에 직접 추가.
      Graceful fallback: `anthropic` SDK 미설치 → ImportError 무시. API key 없음 → 조용히 스킵.
      CLI `--llm` 플래그. GUI "LLM 분류 보조" 체크박스(ANTHROPIC_API_KEY 있으면 기본 활성).
      출력: `[제안] 'X'x5: ... [LLM->wall(0.85) 벽으로 추정됨]`.

## AI 기반 자동 요소 인식/생성 (2026-06-02)
방향(사용자 결정): **하이브리드(텍스트 우선+Vision 폴백) + 고신뢰(>0.8) 자동적용 + 문/창 3D**.
불변 제약: 기하 100% 결정론(ezdxf), AI는 category/subtype/치수만(코드생성 금지), `<dxf>.ai_cache.json` 재현성 캐시.
- [x] **Phase A** — 문/창 기하 휴리스틱(결정론). `entity_to_record`가 ARC→`from_arc`/`arc_radius` 보존.
      `classify_geometry()` **4-튜플 반환**(cat,conf,reason,subtype): ARC 스윙(r 300~1500)→`opening/door`,
      얇은 닫힘박스(긴변 600~3000·짧은변≤400)→`opening/window`. `build_suggestions(kind=layer|block)` subtype 투표.
      `parse()`가 미매핑 블록 explode 기하 수집(`unmapped_block_recs/entities`).
- [x] **Phase B** — 텍스트 AI + 자동적용. `_llm_one`(기하통계 feature, subtype 반환),
      `llm_tiebreak_suggestions`(레이어+블록, `cache`). `best_classification()`=name>vision>llm>geom 종합.
      `apply_ai_classifications(threshold=0.8)`: conf>임계 → 미매핑 레코드를 `elements[cat]` 자동 합류(+subtype),
      이하는 `needs_review`. **AI는 wall 후처리 前 실행** → 자동적용 wall/opening도 pairing/merge/snap/link 거침.
      `parse(use_ai, use_vision, api_key, ai_threshold)`. CLI `--vision`, `--ai-threshold`.
- [x] **Phase C** — `vision_classify.py`(자립, 옵션). `render_dxf_to_png()`=ezdxf matplotlib 백엔드 렌더 +
      `ax.transData` 기반 DXF→픽셀 변환(aspect 자동조정 대응). `vision_fallback()`: 저신뢰 레이어 crop →
      Claude Vision 분류(category/subtype만, 좌표/코드 생성 금지). 캐시 공유. graceful(의존/키 없으면 스킵).
- [x] **Phase D** — `build_openings()`: 사각형 void(host_dir 배향, sill~sill+height)로 벽 cut +
      subtype 시 문짝/창틀 솔리드(`IfcType` Door/Window). `apply_opening_voids`(원통) 대체.
      `link_openings_to_walls`가 opening 스키마(subtype/center/radius/width/height/sill/host_dir) 항상 설정.
      GUI: "AI auto-classify"+"Vision fallback" 체크박스, 자동적용 로그.
- 검증: 샘플4종 회귀불변, 실무도면 walls=921/cols=76/openings=307(스키마 완비),
      FreeCAD 빌드 OK(void=272, FCStd 3.37MB+IFC 587KB). AI/Vision 라이브 테스트는 ANTHROPIC_API_KEY 필요.

## 개선 작업 (2026-09, 실무 다층 프로젝트 회고 기반)
"잘못된 결과물이 그냥 나온다 / 같은 설명을 반복한다 / 결과를 믿기 어렵다 / 일회성 작업이 많다"
— 이 4가지가 **구조적으로** 재발하지 않게 만드는 것이 목표. 상세 계획은 plan 파일 참조.

- [x] **Phase 1 — 규약 중앙화**: `geom_contract.py` 신설. z 기준면·감김 정규화가 존재하는 유일한 장소.
      `preview.py` 는 import 가 불가능하므로 `js_constants()` 로 **주입**받는다(재구현 금지).
### ★ 개구부 판정은 **세 갈래**다 — 한 줄로 묶으면 건물이 통째로 막힌다
`opening_results` 하나를 읽는 자리가 세 곳(`verify.V106` · `artifact_validation` 의
호스트 검사와 영수증 검사)인데, 셋 다 "호스트가 없거나 실패하면 error" 로 묶여 있었다.
그래서 **붙일 벽을 못 찾은 개구부 15개** 때문에 실무 도면 전체가 납품 불가였다.

| 상태 | 뜻 | 판정 |
|---|---|---|
| `requested_hosts` 가 빔 | 링크가 붙일 벽을 못 찾았다 | **warn** — 아래 사유별로 묶어서. 빌더가 못 한 일이 아니다 |
| 호스트를 지정했는데 `cut_host_eids` 가 빔 | 약속한 구멍이 없다 | **error** |
| 일부만 뚫림(`failed_hosts` 있음) | 링크는 후보 목록이라 정상적으로도 생긴다 | **warn** |
| 입력 개구부가 `opening_results` 에 아예 없음 | 빌더가 말없이 버렸다 | **error** |

**"붙일 벽이 없다" 도 두 가지고 조치가 정반대다.** `link_openings_to_walls` 가
레코드에 `no_host_reason` 을 적고 V106 이 그걸로 나눠 보고한다 — 한 줄로 묶으면
"고칠 것 없음" 과 "레이어 매핑이 틀렸음" 이 같은 말로 보인다.

| `no_host_reason` | 뜻 | 조치 |
|---|---|---|
| `wall_open_at_this_span` | 벽 축선 위인데 벽 구간 **밖**이고 벽 끝이 개구부 가장자리에서 한 벽두께 안 → 제도자가 문 자리에서 **벽을 끊어 그렸다.** `no_host_gap_mm` 이 그 틈 | **없음.** 뚫을 재료가 없는 게 맞다 |
| `no_wall_at_this_level` | **z 가 안 겹쳐 벽 루프가 아예 안 돌았다.** `no_host_z_mm` = 개구부의 z 구간. 평면에서 아무리 가까워도 검사 자체가 안 된다 | 도면의 Z 를 볼 것 |
| `no_wall_on_this_line` | 어느 벽 축선에도 안 걸린다. `no_host_dist_mm` = 가장 가까운 벽까지 **실거리**(점-선분) | layer_map 질문 |
| `no_walls_to_check` | 잴 벽이 하나도 없었다 | 벽 매핑부터 |

★ z 갈래가 없던 동안 그 경우가 `no_wall_on_this_line` 으로 보고되고 **최근접 거리에는
센티널 `1e+18` 이 그대로 실려 나갔다**(실측: 아파트 환기평면의 환기슬리브 12개가
`z_base=12358mm`, 벽은 0~2400). 평면 위치 문제라고 말하니 사람이 평면만 들여다본다.
센티널을 값인 척 내보내지 않는 것도 같은 규칙이다 — 못 잰 것은 키를 아예 안 쓴다.

실측(지하3층): 15개 = `wall_open_at_this_span` 5(A-DOOR 문간 2곳, 틈 50~113mm ·
벽 틈 자체는 1520·2121mm) + `no_wall_on_this_line` 10(`OPEN` 레이어의 공백부 X
표시선 = 대각선 5쌍, 최근접 벽 735~2495mm · 점 2개짜리 선이라 뚫을 형상이 없다).

★ 축선 판정에 `perp` 만 쓰면 **벽의 연장선**까지 축선으로 친다 — 62m 떨어진 벽이
'문선이 끊긴 벽' 으로 둔갑했다(실측 6건). 그래서 `over <= r + ww` 를 함께 본다.
최근접 거리도 수직거리가 아니라 점-선분 실거리다(같은 이유로 25mm 로 찍혔다).

**커터가 아무것도 못 깎았다고 실패가 아니다 — 이미 뚫려 있을 수 있다.**
실무 도면은 같은 문을 레이어마다 그려 개구부가 겹친다(실측: 벽 하나에 폭 868·874·
874mm 개구부가 44mm 간격으로 3개). 먼저 온 커터가 그 자리를 이미 비웠으면 void 는
**존재한다**. `build_openings` 가 벽별로 적용한 커터의 합(`applied`)을 들고 있다가,
겹치면 `already_void` 로 기록하고 그 호스트를 뚫린 것으로 센다(실측: 6건).
못 닿은 경우는 `gaps` 에 축별 간격과 `dist_mm` 을 남긴다 — 양수인 축이 떨어진 축이고,
셋 다 음수인데 `dist_mm`>0 이면 bbox 만 겹치는 기울어진 벽이다.

- [x] **Phase 4(일부) — 빌드 게이트**: `verify.py` + 마커 withhold 방식.
      검사 실패 시 빌더가 `FCSTD_DST`/`IFC_DST` 마커를 **출력하지 않는다** → GUI·MCP 가 파일을
      옮기지 못한다. 소비자 코드 변경 없이 fail-closed. 탈출구 `MEP_ALLOW_ERRORS=1` 은
      `verify_status="failed_override"` 를 산출물에 찍는다.
- [x] **D1 — layer_map `opts` 컬럼**: 레이어별 페어링 허용치 등. 몽키패치 제거,
      `tolerances_effective` 로 자기기술.
- [x] **D2 — `from=dim`**: DIMENSION 을 부재 축선으로(옵트인). 후처리 3종(join/pair/merge) 우회.
- [x] **D3 — `schedule=`**: 부재일람표 조인. `schedule_table.py`.
      실측 검증: 표 8개·부재 77개 → DIMENSION 부재 225개 전부 조인, 미매칭 0.
- [x] **D3b — `beam` 정식 빌드**: `build_beams` — 축선을 따라 b×h footprint 를 압출, `IfcType="Beam"`,
      부재명을 `MemberName` 프로퍼티와 라벨에 심는다. 종전엔 `elements["beam"]` 을 읽는 코드가
      **아예 없어서** `category=beam` 레코드가 빌드 단계에서 조용히 사라졌다(그래서 보를
      `slab + overrides.ifc_type=Beam` 으로 우회했고, 그건 얇고 긴 폴리곤을 `IfcType="Slab"` 로
      태그하면 IFC exporter 가 조용히 누락시키는 지뢰 옆이었다). 레거시 경로는 하위호환으로 유지.
      축선→footprint 규칙은 `geom_contract.beam_rings()` 단독 — preview 는 `gcBeamRings` 로 주입받는다.
      소비자 3종 동기화: `freecad_builder`(빌드) · `preview`(렌더) · `boq_export`(부재명별 연장·물량).
      실측: 부재 225개 → `IfcBeam: 225`, 형상오류 0, `RAG11B`=400×1800·`SB0`=100×200(춤×폭 표기 정정 반영).
- [x] **Phase 3 — `stack_build.py` + `stack.json`**: 선언적 층 조립. 층마다 임시 스크립트를
      새로 쓰던 것을 대체한다. `floors[]` 를 레벨 선언에서 직접 만들므로 **층 고아가 불가능**하다.
      EID 에 층 id 접두(`1F:w:722069bb`) — 같은 DXF 를 두 층에 쓰면 조용히 충돌했다.
      offset 해결기(`grid_detect` 재사용, 통심선 축 기준)에 가드 3개:
      ① 증거 하한 방향별 ≥3축 — 기둥 4개짜리 층은 점수를 보기 전에 거부(43,000mm 사고를 잡았을 검사)
      ② 모호성 마진 — 2위가 1위의 0.9배 이상이면 거부(상부층 축이 그리드 부분열이면 여러 shift 가 동점)
      ③ 포함 검사 — offset 적용 후 상부층이 하부층 bbox 안. 그리드와 독립이라 그리드가 놓친 것을 잡는다
      `--dry-run` 은 offset 만 해결하고 멈춘다. 결과·증거는 산출물의 `stack.levels` 에 남는다.
      레벨의 `height` 는 레이어 높이를 **이긴다**(적어줬는데 조용히 지면 원래 문제로 되돌아감) —
      덮은 개수를 보고한다. 미구현: 참조/xref 레이어 자동판별(D16) — `ignore` 규칙으로 수동 처리.
- [x] **Phase 5 — 스킬** `.claude/skills/` 에 `add-floor` / `verify-model` / `map-layers`.
      SKILL.md 세 장뿐 — **파이썬을 넣지 않는다**(넣고 싶어지면 모듈이 빠졌다는 신호다).
      `add-floor` 는 `--dry-run` offset 을 보여주고 **멈추는 것**이 절차의 핵심이고,
      `verify-model` 은 검사ID→진단→조치 표가 본체다. 상시 사실(z 규약·opts)은 여기 CLAUDE.md,
      절차와 중단지점은 스킬 — 나누는 기준이 그거다.
