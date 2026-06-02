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
| `layer_map.csv` | 레이어명 정규식 → 카테고리·치수 매핑 |
| `block_map.csv` | **블록(INSERT)명 정규식 → 카테고리·치수 매핑** (Phase 2) |
| `freecad_builder.py` | geometry.json → FreeCAD .FCStd + .ifc |
| `mep_gui.py` | **현장용 GUI** (Phase 2.5): 파일선택→스캔→파싱→needs_review 수정→3D빌드 (tkinter, 무의존) |
| `run_gui.bat` | GUI 더블클릭 런처 (CLI 불필요) |
| `make_sample_dxf.py` | 테스트용 샘플 DXF 생성 (A-WALL/A-COLS/A-SLAB/A-ZONE) |
| `sample_plan.dxf` | 단선 벽 샘플 (회귀용) |
| `sample_walls.dxf` | **양면 2선 벽 샘플** (Phase 1 평행선 검출 검증용) |
| `sample_blocks.dxf` | **블록 참조(기둥/문 INSERT) 샘플** (Phase 2 검증용) |
| `sample_mep.dxf` | **MEP 샘플** (배관/덕트/트레이 중심선 + 장비 블록, Phase 2.7 검증용) |
| `geometry.json` | 파서 출력 예시 |

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
    "zone":    [{"kind": "polyline", "closed": true, "points": [...]}],
    "opening": [{"kind": "circle", "center": [...], "radius": r}],
    "pipe":    [{"kind": "polyline", "points": [...], "elevation": 2600.0, "diameter": 100.0}],
    "duct":    [{"kind": "polyline", "points": [...], "elevation": 2800.0, "width_mm": 400.0, "height_mm": 300.0}],
    "tray":    [{"kind": "polyline", "points": [...], "elevation": 3000.0, "width_mm": 300.0, "height_mm": 100.0}],
    "equipment":[{"kind": "polyline", "closed": true, "points": [...], "elevation": 0.0}]
  },
  "blocks": {"inserts": 2, "unmapped": 0},
  "mep": {"pipe": 2, "duct": 1, "tray": 1, "equipment": 2},
  "warnings": ["미매핑 레이어: ..."]
}
```

## layer_map.csv 컬럼
`pattern,category,width,height,thickness`
- `pattern`: 정규식 (re.search, 대소문자 무시)
- `category`: `wall` | `column` | `slab` | `zone` | `opening`
- `width/height/thickness`: mm, 빈칸이면 params 기본값 사용

## block_map.csv 컬럼 (Phase 2)
layer_map.csv와 **동일 형식**이나 `pattern`이 **블록(INSERT)명**에 매칭.
- INSERT는 `block_map` 우선 분류 → 미매핑이면 INSERT 레이어로 `layer_map` 폴백 → 그래도 없으면 `미매핑 블록` 경고.
- 분류된 INSERT는 `virtual_entities()`로 블록 내부 형상을 **실좌표 explode** 후 변환.
  - `column`: explode된 closed polyline/circle만 채택. 없으면 `width`(정사각 한 변)로 위치+회전 박스 마커.
  - `opening`: explode된 circle 우선. 없으면 `width`(지름)로 원 마커.
  - 그 외 카테고리: explode 레코드 그대로.
- `-b/--blockmap` 인자로 지정. 생략 시 기본 블록 규칙(COL/기둥/PILLAR→column, DOOR/문·WIND/창→opening).

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
      freecad_builder: `build_walls/columns/slabs`에 `Placement.Base.z=z_base` 적용.
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
