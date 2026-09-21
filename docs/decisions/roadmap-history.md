# 로드맵과 완료 이력

완료한 단계와 그때의 결정 — 지금 코드의 근거가 아니라 **이력**이다. 현재 동작은 코드와 테스트를 본다.

이 파일은 **왜 그렇게 했는지**의 기록이다. 상시 규약(z 기준면·`opts` 키·실행 순서)은 `CLAUDE.md` 에 있다.

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

## "쉽고 재미있는 루프" (2026-09-18, `docs/plans/2026-09-18-easy-loop.md`)
목표(사용자): 2D 도면 → 3D 자동 모델링(MEP 포함) → 간섭체크 → 물량내역을 **아주 쉽고 재미있게**.
출력 경로 다섯 개(FreeCAD·Blender GLB·Pascal·ifc_builder 직접·미리보기)와 규칙 표가 이미 "틀리지
않는 것"엔 성공했다는 판단 아래, 새 규칙·새 경로를 더 얹는 대신 **① 브라우저 편집기 ② 검토 항목의
버튼화 ③ 선언 표면(프로젝트 기본값) ④ 살아 있는 물량**을 먼저 만들고, 쓰지 않는 경로 셋을 접었다.

- [x] **Phase 0 — 기반**: BOQ 벽 두께가 `GC.width_of` 계약을 따르도록 수정 · 저장 한 번이 파싱
      한 번만 하도록(재파싱 중복 제거) · 편집 저장 경계 강화(elevation 유한성 검증, `overrides` 의
      `null` 은 "지운다", `added` 는 `category` 필수).
- [x] **Phase 1 — 브라우저 편집기**: 인스펙터에 높이 칸(MEP 먼저) · 2D 평면 탭에 배관·덕트·트레이
      선택·분할·결합·그리기 · 3D 클릭 → "평면에서 고치기" 버튼.
- [x] **Phase 2 — 검토 항목을 버튼으로**: 파서가 이미 계산해 둔 제안(얇은 오결합·기둥이 벽처럼
      그려짐·두께 불일치)을 구조로도 내고(`suggestions_apply`), `/layer-rule` 엔드포인트가 프로젝트
      소유 `layer_map.csv` 에 쓴다. 검토 행에 `[적용]` 버튼.
- [x] **Phase 3 — 선언: 프로젝트 기본값**: `source.options.defaults` — 레이어·프로필 선언이 없을
      때만 채우는 재질·DN·용도·보온 등급. `declaration_basis` 로 자기보고, 영수증은 `defaulted` 와
      `missing` 을 따로 센다. GUI 는 건축 도면을 열 때 층고·창호일람·재질을 한 번 묻고, 이미 연
      프로젝트는 '그 밖의 도구 ▾ → 프로젝트 기본값' 으로 다시 연다.
- [x] **Phase 4 — 살아 있는 물량**: `ProjectSession._parse` 가 매 파싱마다 `boq_export.aggregate()`
      를 얹는다 — 저장 한 번이 곧 물량 갱신이다(재요청 없음). 3-에이전트 병렬 리뷰가 다층 조립
      (`stack_build.py`)에서 이 신호가 항상 빠지는 것과, 패널 문구가 없는 열을 가리키는 것을
      찾아 고쳤다.
- [x] **Phase 5 — 동결·정리·납품 메뉴**: `내보내기 ▾` → `납품 ▾`(검토 CSV·물량·창호일람·검증
      빌드 넷만). IFC 직접 내보내기·Blender/GLB 는 메뉴에서 빼되 '그 밖의 도구' 버튼은 남긴다
      (지운 게 아니라 일상 경로가 아니라는 뜻). Pascal 코드는 안 건드리고 [pascal.md](pascal.md)
      에 상태 줄만 — 일상 편집은 평면 탭, Pascal 은 계통·재질 롤·단면 롤·장면 트리가 필요할 때만.
      `docs/modeling_techniques.md` 의 죽은 문구("난방관은 편집 가능한 Blender Curve") 정정 —
      `prepare_payload` 는 원형이든 사각이든 항상 mesh 만 낸다.

### 2026-09-21 — 납품 IFC 를 FreeCAD 에서 ifcopenshell 직접 생성으로

사흘 전 동결한 `ifc_builder.py` 가 납품 경로가 되고 `freecad_builder.py` 가 동결됐다. 동결 때 적은
사유("개구부 불리언과 MEP 솔리드는 FreeCAD 커널이 필요하다")를 실측이 깼다 — 개구부는 IFC 에서
절삭이 아니라 선언이고, FreeCAD 경로도 IFC 에는 같은 void 선언만 실었다. 같은 날 `Arch.makePipe`
(곡면)가 V107 비다양체를 내는 것을 추적해 곡면 API 를 통째로 걷어낸 것이 결정적이었다: 형상은
이미 전부 `geom_contract` 가 계산하고 있었고, FreeCAD 는 그걸 스키마에 옮겨 쓰는 라이터였다.
경위·측정·되돌리는 측정은 [ifc-builder.md](ifc-builder.md).
