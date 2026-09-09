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
| `geom_contract.py` | **기하 계약의 단일 출처.** z 기준면 `z_range()`, 감김 정규화 `ccw()`, 보 축선→footprint `beam_rings()`. JS 소비자(preview)는 `js_constants()` 로 같은 식을 주입받는다. FreeCAD 의존 없음(단위테스트 가능) |
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
| `artifact_validation.py` + `freecad_runner.py` | 실행별 산출물 영수증과 실제 IFC 재검사. 입력 해시·EID/GlobalId·형상·체적·층·QA 속성을 대조한다. |
| `mep_gui.py` | **현장용 GUI** (Phase 2.5): 파일선택→스캔→파싱→**3D 미리보기(브라우저)**→needs_review 수정→3D빌드 (tkinter, 무의존) |
| `run_gui.bat` | GUI 더블클릭 런처 (CLI 불필요) |
| `make_sample_dxf.py` | 테스트용 샘플 DXF 생성 (A-WALL/A-COLS/A-SLAB/A-ZONE) |
| `sample_plan.dxf` | 단선 벽 샘플 (회귀용) |
| `sample_walls.dxf` | **양면 2선 벽 샘플** (Phase 1 평행선 검출 검증용) |
| `sample_blocks.dxf` | **블록 참조(기둥/문 INSERT) 샘플** (Phase 2 검증용) |
| `sample_mep.dxf` | **MEP 샘플** (배관/덕트/트레이 중심선 + 장비 블록, Phase 2.7 검증용) |
| `geometry.json` | 파서 출력 예시 |
| `tests/run_all.py` | 의존성 없는 테스트 러너(`python tests/run_all.py`). **빌드 게이트는 `pytest tests` 다** — `build_exe.bat` 이 빌드 전에 돌리고 실패 시 중단한다. `run_all.py` 는 pytest 픽스처(`tmp_path`·`monkeypatch`)를 못 주어 테스트 48개를 못 돌리므로 **미실행이 있으면 스스로 exit 1** 을 낸다 — 반쪽 러너가 게이트 행세를 하지 않게 |
| `tests/golden.json` | **실무 도면 회귀 다이제스트.** 도면은 고객 자료라 커밋하지 않고 경로만 `tests/golden.local.json`(gitignore)에 둔다. 도면이 없으면 `[skip]`. 갱신은 `python tests/test_golden.py --bless` |
| `extractors.py` + `mep_macro/` | **FreeCAD 안에서만** 쓰는 라이브 자연어 모델링용 헬퍼(`freecad_live_addon`). 일반 파이프라인은 이걸 거치지 않는다 |
| `FIX_SPEC.md` | 면선 페어링·곡선벽 개선의 이전 설계 기록. 현재 구현 여부는 코드와 회귀 테스트를 확인한다. |
| `docs/project_workflow.md` | 프로젝트 저장·복구·재연결·출력 검증 사용법과 현재 범위. |

## geometry.json 스키마
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

`ifctype_counts` 는 **MEP 까지** 센다(`pipesegment`·`ductsegment`·`cablecarriersegment`·
`distributionelement`). 종전엔 구조 4종만 세서, IFC 에 `IfcDuctSegment` 45개가 들어
있는데 영수증에는 `{'wall': 88}` 만 찍혔다 — 덕트가 조용히 사라져도 `build.json` 이
말해 주지 않는 상태였다(보가 정확히 그렇게 사라진 적이 있다 — D3b 참조).

빌더는 카테고리별로 **객체를 못 만든 레코드**를 `build.json` 의 `unbuilt` 에 남긴다.
전부 0 이어야 한다 — 이 카운터가 없던 동안 벽 72개가 경고 없이 빠진 채 납품될 수 있었다.

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

### ★ 아파트 단위세대 평면 — 레이어 이름이 부재를 안 알려 준다
실측(74타입): 벽식 구조라 **기둥이 없는데 `A-COL` 이 200mm 내력벽**이다. 기본 규칙의
`COL|기둥` 이 그걸 column 으로 보내 벽이 20개(23.7m·전부 90mm)만 남았다. 평행선 간격을
재면 바로 갈린다 — `A-COL` 200mm×20 · `Parti` 100·110mm · `A-WALL` 90mm×21 ·
`A-FIN` 200·210mm(마감선이라 벽으로 넣으면 이중계상). 셋을 wall 로, `A-FIN` 을 ignore
로 두면 벽 104개 123.8m, 면선커버 100%, 검토필요 3.

**문·창은 블록이고 이름이 폭을 담는다**(`D-900`·`PD-750`·`FSD-1100`·`W-1200`·`W-3600`).
`block_map` 에 없으면 레이어로 폴백해 **문짝 선 조각이 개구부가 된다**(실측: 폭 150·
180mm 짜리 8개, 그중 2개는 겹쳐서 V106 error). 이름으로 매핑하면 750·900·1000·1100·
1200·1800·2300·3600mm 16개가 나오고 전부 벽에 붙는다. ★ 선매칭 우선이라 `WD-900` 을
`D-900` 보다 **위에** 둘 것 — 'D-900' 은 'WD-900' 안에도 있다.

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
배관은 `Arch.makePipe` 가 축선을 스윕한다 — 다점 폴리라인 한 객체, 코너는 마이터.
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
