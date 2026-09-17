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

## 이 문서를 쓰는 규칙
- **여기에는 상시 규약만 둔다** — z 기준면·`opts` 키·CSV 컬럼·실행 순서처럼 코드를 쓸 때마다 필요한 것.
- "왜 그렇게 했는지"(실측·접은 대안·사고 경위)는 `docs/decisions/` 로. 길이가 아니라 **쓰임**으로 가른다.
- ★ **새 절을 쓸 때는 그 절을 고정하는 테스트 이름을 같이 적는다**(`docs/decisions/README.md` 의 표).
  적을 이름이 없으면 `기록만` 이라고 쓰고, 그것이 정당한 경우는 둘뿐이다 — **하지 말라는 기록**(측정하고
  접은 대안)과 **외부 환경의 사실**(FreeCAD·Pascal·브라우저). 둘 다 아닌데 못 적겠으면 아직 쓸 때가 아니다.
- 측정하지 않은 수치를 적지 않는다. 실측은 **어느 도면에서 쟀는지**와 같이 적는다.

## 파일 구성
| 파일 | 역할 |
|------|------|
| `dxf_parser.py` | DXF → geometry.json 파서 v2. ★ **레이어 이름이 부재를 안 알려 줄 때 증거를 낸다**(`layer_evidence` — 평행 짝 비율·간격을 파서가 벽을 정의하는 그 함수(`_find_wall_pairs`)로 잰다). 기둥으로 매핑된 레이어가 벽처럼 그려졌으면 `[분류 의심]` 경고 + 붙여 넣을 layer_map 한 줄 + `column_layers_like_wall`, 레코드 사유는 `column_layer_looks_like_wall`. **재분류는 하지 않는다** |
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
| `mep_gui.py` | **현장용 GUI** (tkinter, 무의존). 첫 화면은 넷이다 — `열기…` · `설비 도면 설정` · `내보내기 ▾` · `그 밖의 도구 ▾`. ★ **열기 한 번이 길을 정한다**: 도면 종류를 먼저 묻고(추측은 `guess_drawing_kind` 가 레이어 **이름만** 보고 채우는 기본 선택일 뿐) **건축이면 파싱, 설비면 설정 창**으로 갈라 브라우저까지 연다. 설비 평면을 그냥 파싱하면 **배경 건축 XREF 가 기둥·벽이 되어 오답이 첫 화면이 된다**(실측: 단위세대 환기 도면 검토 대기 43건이 전부 그것). 일회성·상황별 도구(스캔·단위·누락 진단·같은 층 추가·edits 가져오기·layer_map 편집·AI 체크박스·needs_review 표)는 **접혀 있을 뿐 그대로 있다** |
| `run_gui.bat` | GUI 더블클릭 런처 (CLI 불필요) |
| `run_editor.bat` + `pascal_host/stage_runtime.py` | **편집 화면 런처(npm·bun 없이).** `stage_runtime.py` 가 Pascal standalone 빌드 + node 실행 파일 + `mep-runtime.json`(고정 커밋·오버레이 지문)을 `pascal_runtime/`(gitignore)에 모으고, `run_editor.bat <도면|프로젝트>` 가 `run_host.py --runtime pascal_runtime --open` 을 부른다 |
| `make_sample_dxf.py` | 테스트용 샘플 DXF 생성 (A-WALL/A-COLS/A-SLAB/A-ZONE) |
| `sample_plan.dxf` | 단선 벽 샘플 (회귀용) |
| `sample_walls.dxf` | **양면 2선 벽 샘플** (Phase 1 평행선 검출 검증용) |
| `sample_blocks.dxf` | **블록 참조(기둥/문 INSERT) 샘플** (Phase 2 검증용) |
| `sample_mep.dxf` | **MEP 샘플** (배관/덕트/트레이 중심선 + 장비 블록, Phase 2.7 검증용) |
| `geometry.json` | 파서 출력 예시 |
| `tests/run_all.py` | 의존성 없는 테스트 러너(`python tests/run_all.py`). **빌드 게이트는 `pytest tests` 다** — `build_exe.bat` 이 빌드 전에 돌리고 실패 시 중단한다. `run_all.py` 는 pytest 픽스처(`tmp_path`·`monkeypatch`)를 못 주어 테스트 48개를 못 돌리므로 **미실행이 있으면 스스로 exit 1** 을 낸다 — 반쪽 러너가 게이트 행세를 하지 않게 |
| `tests/golden.json` | **실무 도면 회귀 다이제스트.** 도면은 고객 자료라 커밋하지 않고 경로만 `tests/golden.local.json`(gitignore)에 둔다. 도면이 없으면 `[skip]`. 갱신은 `python tests/test_golden.py --bless`. 항목은 두 종류다 — `dxf`+`layer_map`(레이어맵 파싱) · **`project`(설비 `.mep` 폴더)**: 저장된 프로필·영역·단위 그대로 `ProjectSession` 으로 다시 해석해 설비 요약(이음·커버리지·연결성·간섭·진단 코드)까지 본다. 폴더는 **복사해서** 연다(원본 revision 을 건드리지 않게). 새 도면 등록 절차와 커밋 전 스캔은 `docs/release_checklist.md` |
| `extractors.py` + `mep_macro/` | **FreeCAD 안에서만** 쓰는 라이브 자연어 모델링용 헬퍼(`freecad_live_addon`). 일반 파이프라인은 이걸 거치지 않는다 |
| `FIX_SPEC.md` | 면선 페어링·곡선벽 개선의 이전 설계 기록. 현재 구현 여부는 코드와 회귀 테스트를 확인한다. |
| `pascal_bridge.py` | **geometry.json ↔ Pascal(pascalorg/editor) 씬 그래프.** 벽·기둥·슬래브·zone·개구부와 설비(배관·덕트·트레이는 **전용 플러그인 노드**, 단면 mm). 단위(mm↔m)·축(Pascal 은 Y-up)·고저(레벨 스택) 환산은 전부 `geom_contract` 를 부른다. Pascal 이 표현 못 하거나 스키마 범위를 넘긴 것은 `unconvertible` 로 **세어서 보고**한다 |
| `pascal_host/` | **Pascal 편집 화면 호스트(3단계 첫 조각).** `PASCAL_COMMIT`(고정 커밋) · `overlay/`(체크아웃에 얹는 우리 파일: `/mep` 페이지 · `/api/mep/*` 서버 측 프록시 · `lib/mep-plugin/` 설비 노드 플러그인) · `run_host.py`(저장소 서버와 Pascal 을 함께 띄우는 실행기) |
| `docs/project_workflow.md` | 프로젝트 저장·복구·재연결·출력 검증 사용법과 현재 범위. |
| `docs/decisions/` | **왜 그렇게 했는지의 기록** — 실측·접은 대안·사고 경위. 아래 '결정 기록' 표 참조 |
| `docs/release_checklist.md` | **릴리스 전 수동 점검 5가지**(브라우저·FreeCAD·Pascal 은 CI 에 없다) + 골든 등록 절차. 자동 검사가 덮는 것은 적지 않는다 — 순수 함수는 전부 `npm test` 가 잠갔다 |

## 결정 기록 (`docs/decisions/`)

**여기 CLAUDE.md 에는 상시 규약만 둔다** — z 기준면·`opts` 키·컬럼·실행 순서처럼 코드를 쓸 때마다 필요한 것.
"왜 그렇게 했는지"(실측·접은 대안·사고 경위)는 아래 파일에 있다. 관련된 코드를 고치기 전에 그 파일부터 읽는다.

| 파일 | 담은 것 |
|---|---|
| [clash-review.md](docs/decisions/clash-review.md) | 간섭을 **위치·부재·조치** 목록으로 만든 경위 · 높이 근거 · 합성 상부 구조 · 슬리브 · 검사 속도 |
| [mep-connectivity.md](docs/decisions/mep-connectivity.md) | 이음은 도면이 이어 그린 곳에만 · 연결 후보와 확정 · 외곽선 폭 측정 · 장비 본체 제안 |
| [mep-geometry.md](docs/decisions/mep-geometry.md) | 경로 계약 v3 · 공통 마이터 링 · `normalize()` 가 덕트를 종잇장으로 만든 사고 |
| [walls.md](docs/decisions/walls.md) | 면선 페어링 · 두께 우선순위 · `pair_max` 를 올리면 더 나빠지는 실측 · 중복 부재 · EID |
| [openings.md](docs/decisions/openings.md) | 붙일 벽을 못 찾는 네 사유와 정반대인 조치 · 이미 뚫린 자리 |
| [drawings-in-practice.md](docs/decisions/drawings-in-practice.md) | 한 층에 공종 도면 여러 장 · 단위세대 평면 · 환기 평면도 · explode 된 부재의 레이어 |
| [edits-and-preview.md](docs/decisions/edits-and-preview.md) | 수정 라운드트립의 주입 위치 · 2D 평면 탭 · 미리보기가 근거를 말하는 이유 |
| [pascal.md](docs/decisions/pascal.md) | Pascal 다리(단위·축·고저) · 편집 화면 호스트의 저장 기준 |
| [roadmap-history.md](docs/decisions/roadmap-history.md) | 완료한 단계와 그때의 결정 — **이력**이다. 현재 동작은 코드와 테스트를 본다 |

★ **새 절을 쓸 때는 "이 절을 고정하는 테스트 이름" 또는 "기록만" 을 같이 적는다.** 잠기지 않은 절은
다음 사람이 다시 깨뜨린다 — 무엇이 잠겨 있는지는 [decisions/README.md](docs/decisions/README.md) 의 표.

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

### 출력 예시

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

**층 판정은 `geom_contract.floor_z(cat, rec)` 로 한다 — `z_base` 가 아니다.** 창 아래 벽·인방(`source="opening_infill"`)은
층 바닥보다 떠서 시작하고(`z_base` 2100) `floor_z` 에 옆 벽의 층을 든다. 층 매칭(V001·빌더 `_at_floor`·IFC 층 재검사·
Pascal 레벨·층 조립 이동)에 `z_base` 를 쓰면 가짜 층이 생기거나 빌드가 막힌다. 형상은 여전히 `z_range` 로만 읽는다.
고정: `tests/test_opening_infill.py` 의 `test_raised_walls_do_not_make_floors_and_pass_the_floor_gate` ·
`test_stacked_levels_move_the_floor_anchor_and_prefix_the_opening_link` ·
`test_pascal_keeps_one_level_reports_raised_walls_and_an_untouched_scene_saves_nothing`

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

### 6.4 검토 목록 CSV (일상 검토 — FreeCAD 불필요)
간섭·연결 목록을 표로 낸다. 같은 자리를 FreeCAD 불리언보다 수천 배 빠르게 찾으므로 **일상 검토는 여기까지**이고,
`(4) 납품 검증 빌드` 는 납품 전에 한 번 돌린다.
```
python clash_review.py geometry.json --connectivity
# → geometry_clash.csv (위치·부재·조치·높이 근거) · geometry_connectivity.csv (이음 후보·끊긴 끝)
```
GUI 는 '(3b) 검토 목록 CSV' 가 `<프로젝트>/review/clash_r<rev>.csv` 로 쓴다.

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

## zone 귀속 방식
zone은 별도 파일 없이 **DXF의 `A-ZONE` 레이어**(closed LWPOLYLINE)를 직접 사용.  
shapely point-in-polygon으로 각 요소의 중심이 어느 구역인지 자동 판정 → `"zone": 0` (인덱스).

## 관련 기존 프로젝트
- `c:\AI program\MEP ems\core\` — 배관 물량산출·BOQ 자동화
  - `dxf_inventory.py` — 레이어 인벤토리 (--scan 패턴 원형)
  - `dxf_pipe_extractor.py` — 배관 선분 추출
  - `classification_engine.py` — fuzzy/confidence 분류
