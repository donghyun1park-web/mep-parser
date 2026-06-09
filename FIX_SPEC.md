# 벽 검출 수정 지시서 (외곽선→센터선 정합 정밀화)

대상: `dxf_parser.py`. 테스트 도면: `지하3층 건축평면도.dxf`.
원칙: 각 단계는 **독립 적용·독립 검증·독립 롤백** 가능. 좌표 100% 결정론 유지.

---

## 0. 기준선 (수정 전 실측 — 반드시 먼저 기록)

```bash
python - <<'PY'
import sys; sys.stdout.reconfigure(encoding='utf-8')
import dxf_parser as P, collections
r=P.load_layer_map('layer_map.csv'); b=P.load_layer_map('block_map.csv')
d=P.parse(r'C:/AI program/architectural_timelapse_phase16/input/drawings/지하3층 건축평면도.dxf',r,b)
W=d['elements']['wall']
print(collections.Counter(w.get('pairing') for w in W), 'total', len(W))
PY
```

**기준선 수치 (2026-06): `paired=383, single_offset=221, closed=37, total=641`**
- 목표: `single_offset` 을 221 → 80 이하로. (paired 비율 60%→85%+)
- 판정: 각 단계 후 위 스크립트 재실행해 paired↑ / single_offset↓ 확인. **회귀**: paired 가 줄면 그 단계 롤백.

---

## 1단계: 면선 선병합 — 교차벽 갭 브리징 (적응형 gap)

### 문제
`_merge_collinear_segments`(현 412행) 의 `_SEG_MERGE_GAP_MM=50mm`(410행) 가 너무 작다.
한 벽면이 교차벽·기둥마다 끊겨 조각나는데, 그 갭은 **교차벽 두께(100~400mm)** 라 50mm로 못 이음.
→ 조각난 채 페어링 단계로 가서 `overlap<0.3` 으로 탈락 (894개 중 671개가 이 사유).

### 수정
**파일** `dxf_parser.py` / **상수** 410행
```python
# 변경 전
_SEG_MERGE_GAP_MM = 50.0
# 변경 후
_SEG_MERGE_GAP_MM = 50.0          # 1차 보수 병합(제도오차)
_SEG_MERGE_GAP_WALL_MM = 450.0    # 2차 적극 병합(교차벽 갭 브리지, 문 800mm+ 는 보존)
```

**파일** `_merge_collinear_segments`(412행) — gap 판정에 "개구부 추정 분할" 추가.
- 같은 직선 버킷에서 인접 조각 갭이 `_SEG_MERGE_GAP_MM < gap <= _SEG_MERGE_GAP_WALL_MM` 이면 **병합**.
- `gap > _SEG_MERGE_GAP_WALL_MM` 이면 **분할 유지**(개구부일 가능성).
- 호출부 `detect_wall_pairs`(523행) 와 `repair_single_walls` 내 호출(현 _merge 사용처)에서
  `gap_tol=_SEG_MERGE_GAP_WALL_MM` 전달.

### 근거/리스크
- 교차벽 갭(≤450mm)만 잇고 문 개구부(≥800mm)는 분할 → 개구부 보존.
- **리스크**: 폭 450~800mm 좁은 창/개구부를 가로질러 병합 가능 → 4단계에서 opening 위치로 재분할.

### 검증
0단계 스크립트 재실행. **기대: single_offset 221 → 약 140~170.** paired 증가 확인.

### 롤백
`_SEG_MERGE_GAP_WALL_MM` 호출 인자만 제거(상수 정의는 무해).

---

## 2단계 (핵심): 라인-버킷 페어링으로 재설계

### 문제
현 `_find_wall_pairs`(484행) 는 **세그먼트 i↔j 쌍별 그리디**.
- 긴 면 L 맞은편에 조각 F1,F2,F3 → L 은 1개에만 매칭(그리디 1:1) → 나머지 고아.
- `overlap < OVERLAP_RATIO*min_len`(496행) 가 짧은 조각 기준이라 쉽게 탈락.

### 수정 — 신규 함수 `_pair_line_buckets(segs, params)`
1. **라인 버킷화**: 세그먼트를 `(방향 양자화, 수직오프셋 양자화)` 키로 그룹 →
   각 버킷 = "하나의 무한직선 위 면선군". (이미 `_merge_collinear_segments` 의 버킷 로직 재사용)
2. **버킷 간 평행쌍 후보**: 버킷 A,B 가
   - 방향 평행(≤`WALL_ANGLE_TOL_DEG`)
   - 수직거리 `perp ∈ [WALL_PAIR_MIN_MM, WALL_PAIR_MAX_MM]`
   - **사영 구간 겹침이 존재**(임의 양>0; 비율조건 폐기)
3. **그리디(거리 오름차순)로 버킷 1:1 확정** — 각 버킷은 최대 1 파트너.
4. **벽 생성**: 두 버킷의 **겹치는 사영 구간**들을 따라 centerline 세그먼트 생성
   (겹침 구간이 여러 개면 구간마다 벽 1개 → 개구부에서 자연 분할).
   - 두께 = perp, align=Center 용 centerline = 두 버킷 중점선의 겹침구간.
5. **미매칭 버킷의 잔여 세그먼트** → 기존 `single` 경로로.

### 적용 위치
`detect_wall_pairs`(510행) 의 `pairs, matched = _find_wall_pairs(segs)`(527행) 호출을
`_pair_line_buckets` 로 교체. 출력 레코드 스키마(centerline/width_detected/pairing="paired")는 동일 유지.

### 근거
- "면선=조각합"으로 보므로 단편화에 무관. overlap 비율조건 제거로 671개 탈락 해소.
- 겹침구간 분할로 개구부가 자동으로 벽 사이 빈칸이 됨(3단계 부담↓).

### 검증
0단계 스크립트. **기대: single_offset ≤ 80, paired ≥ 480.**
추가 측정(오결합 점검): paired 두께 `<60mm` 개수 — 51 → 10 이하 기대.
```bash
python -c "import dxf_parser as P,collections; r=P.load_layer_map('layer_map.csv'); b=P.load_layer_map('block_map.csv'); d=P.parse(r'...지하3층...dxf',r,b); W=[w for w in d['elements']['wall'] if w.get('pairing')=='paired']; print('thin<60mm:', sum(1 for w in W if (w.get('width_detected') or 0)<60))"
```

### 롤백
`detect_wall_pairs` 의 호출을 `_find_wall_pairs` 로 되돌림(기존 함수 보존).

### 리스크
- 가장 큰 변경. **반드시 샘플 4종 회귀**(sample_plan/walls/blocks/mep) 불변 확인 후 진행.
- 겹침구간 다중 분할 로직 버그 시 벽 과다생성 → total wall 수 모니터.

---

## 3단계: collinear 과병합 → 개구부 보존

### 문제
`merge_collinear_walls`(751행) 의 `COLLINEAR_GAP_TOL_MM=500mm`(82행) 가
2단계 후 남은 centerline 들을 500mm 갭까지 이어 **좁은 개구부를 덮음**.

### 수정
- 2단계가 겹침구간 분할을 하므로 이 후처리 병합은 **갭 축소**: `COLLINEAR_GAP_TOL_MM 500 → 120`
  (제도오차+코너 정렬용만, 개구부는 안 이음).
- 단, paired 두께가 **같을 때만** 병합(이미 키에 width 포함됨 — 확인).

### 검증
opening 개수 불변 + 벽이 문 위치에서 끊겨있는지 1구역 시각 렌더(matplotlib 오버레이).

### 롤백
상수 원복.

---

## 4단계: 곡선벽(ARC) · 열린 LWPOLYLINE

### 문제
- ARC 7개(반경 5.7~13m 곡선벽) → `arc_to_points` 폴리근사 후 직선 평행판정 실패 → 누락.
- 열린 LWPOLYLINE 71개 중 일부 면선이 버킷에 안 잡힘.

### 수정 (분리 처리, 낮은 우선순위)
- ARC 면선: 곡선 전용 페어링(동심호 2개 → 평균반경 centerline + 두께=반경차). 신규 `_pair_arc_faces`.
- 열린 LWPOLYLINE: 다중점이면 edge 분해 후 2단계 버킷에 합류(이미 `_wall_segments` 가 분해).
  → 2단계가 처리하므로 추가 작업은 ARC 만.

### 검증
곡선벽 구역 렌더에서 곡선 벽 생성 확인.

---

## 5단계: FreeCAD 빌드 회귀

```bash
# geometry.json 재생성 후
freecadcmd 로 빌드 (MEP_GEOMETRY/MEP_OUT 환경변수) → self-intersecting=0, invalid=0 확인
```
**기대**: 벽 위치가 외곽선 *사이*, 개구부에서 끊김, 곡선벽 생성, FCStd/IFC 정상.

---

## 실행 순서 (권장)
1. **0단계 기준선 기록** (필수 선행)
2. **1단계** → 검증 → 커밋
3. **2단계** → 샘플 회귀 → 실무 검증 → 커밋  ← 핵심, 여기서 대부분 해결
4. **3단계** → 검증 → 커밋
5. (선택) **4단계** ARC
6. **5단계** 빌드 최종 검증

각 단계 커밋 메시지에 **before→after 수치** 기록. 수치 악화 시 즉시 롤백.
