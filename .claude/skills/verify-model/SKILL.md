---
name: verify-model
description: 모델이 이상할 때. "모델 이상해", "안 맞아", "떠 있어", "겹친다", "빠졌다", "IFC에 없어", 납품 전 점검, 빌드가 BUILD_FAILED/IFC_FAILED 로 끝났을 때 사용한다.
---

# 모델 검증

```
python verify.py <geometry.json>                       # 빌드 전
python verify.py <geometry.json> --build-stats <out>.build.json --ifc <out>.ifc
python verify.py --dump-checks                         # 검사 목록
```

빌드가 `BUILD_FAILED:<경로>` / `IFC_FAILED:<경로>` 로 끝났으면 그 경로가 리포트다.
**산출물이 없는 게 정상이다** — 게이트가 마커를 withhold 해서 GUI·MCP 가 파일을 못 옮긴 것이다.

## 검사ID → 진단 → 조치

| ID | 증상 | 진짜 원인 | 조치 |
|---|---|---|---|
| V001 / V102 | IFC 에 부재가 통째로 없다 (`IfcBeam: 0`) | 레코드 z 가 `floors[]` 의 어떤 z 와도 ±100mm 안에 안 맞아 `Arch.makeFloor` 밖에 남음 → `export([building])` 에서 조용히 빠짐 | **레코드가 아니라 `stack.json` 의 레벨 `z` 를 고친다.** 리포트의 `floor_orphan_detail` 이 어긋난 z 를 알려준다 |
| V002 | 부재가 두 번 보인다 | 두 레벨의 z 가 100mm 안에 붙어 있음 | 레벨 z 를 벌리거나 하나를 합친다 |
| V003 | — | 알 수 없는 카테고리 버킷 / 계약 버전 불일치 | 오래된 geometry.json 이면 재파싱. 새 카테고리면 `geom_contract.Z_DATUM` 에 등록 |
| V004 | 층이 서로 떨어져 있다 | 아래층 연직 지지요소 상단과 위층 바닥 사이 공백 | 아래층 `height` 를 올린다. 층고는 **안목이 아니라 층고**다(슬래브 두께 포함) |
| V005 | 좌표가 수 km | 겹친 동일선상 벽이 A→B→A 로 체이닝 → `align="Center"` 오프셋 발산. `isValid()` 는 True 라 안 걸린다 | 빌더가 `_split_folded_chain` 으로 분할하지만 원인은 데이터다. 해당 레이어의 중복선을 확인 |
| V006 | — | 벽 baseline 이 180° 반전 | V005 와 같은 뿌리 |
| V007 | — | needs_review / single 페어링 과다 / 미매핑 레이어 | needs_review 가 보에 몰려 있으면 **일람표 미매칭**이다 → `map-layers` 의 `schedule=` |
| V008 | 벽이 듬성듬성 | 면선 커버리지 부족 = 페어링 실패 | `opts: pair_max` 를 그 레이어에 맞게 (보/거더는 500~2500) |
| V101 | IFC 수 ≠ 만든 수 | 카테고리/IfcType 불일치. ★ 얇고 긴 폴리곤에 `IfcType="Slab"` 이면 exporter 가 **에러 없이** 누락시킨다(30/30 재현) | 보는 `category=beam` 으로 보내 `build_beams` 를 타게 한다 |
| V103 | — | `isValid()=False` | 해당 footprint 의 자기교차·중복점 확인 |
| V104 | 모델이 도면보다 훨씬 크다 | 폭주 솔리드 | V005 와 같은 뿌리 |

## 창·문이 이상할 때 (검사는 통과하는데 보기 이상함)

`verified` 여도 창·문은 틀릴 수 있다 — 검사는 빌더의 약속과 IFC 를 대조할 뿐 치수가 맞는지는 모른다. 파싱 결과에서 먼저 센다
(`drawing-set-intake` 의 `parse_summary.py` 가 한 번에 출력한다):

| 증상 | 먼저 볼 값 | 원인 → 조치 |
|---|---|---|
| 창 반쪽짜리 창대 벽·인방, 옆은 바닥부터 뚫림 | `width_source=default` · `dims_assumed` 의 `width` | 폭을 아무도 선언 안 한 블록(900 가정). block_map 에 폭 선언 → `window-schedule-join` |
| 창대 높이·창 높이가 전부 같다 | `openings_dims_assumed` · `dims_source` | 평면도에는 높이가 없다 — 창호일람 조인(`window-schedule-join`) |
| 창 판(IfcWindow)이 겹겹이·허공에 | `no_host_reason` 분포 · 선 개구부 수 | 선 여러 줄 창은 파서가 합친다. 남는 `no_wall_on_this_line` 은 벽 레이어 누락이거나 기호가 벽 밖에 그려진 것 |
| 문이 통째로 없다 | 블록 수 vs 개구부 수 · `small_openings_dropped` | 블록 안 작은 원(철물)을 개구부로 삼던 결함(수정됨) — 재파싱. 그래도 없으면 블록 규칙 확인 |
| 문턱이 떠 있어야 하는 문(점검구형) | 일람의 H1 | 도구가 문의 창대를 무시한다 — 형상이 맞게 창으로 선언하고 검토표에 적는다 |

## 원칙

- **검사를 통과시키려고 severity 를 낮추지 말 것.** 사용자가 명시적으로 요청하면
  `geometry.json` 의 `verify.severity` 에 적어 diff 에 남긴다. 로그로 얼버무리지 않는다.
- `MEP_ALLOW_ERRORS=1` 은 탈출구지 해결이 아니다. 쓰면 산출물에
  `verify_status="failed_override"` 가 찍혀 그 빌드가 스스로를 식별한다.
- "빌드 완료" 를 신뢰하지 말고 `<out>.build.json` 의 `intent` vs `ifctype_counts` 를 본다.
- 육안 확인은 `python preview.py <geometry.json>` (FreeCAD 불필요).
