---
name: map-layers
description: 도면 레이어가 안 잡힐 때. "이 레이어 뭐야", "안 잡혀", "미매핑", "파싱했는데 요소가 없어", "보가 벽으로 나온다", "치수가 틀리다" 같은 요청에 사용한다.
---

# 레이어 매핑

```
python dxf_parser.py <도면.dxf> --scan                       # 인벤토리
python dxf_parser.py <도면.dxf> -m layer_map.csv -o g.json   # 파싱
python schedule_table.py <도면.dxf> --layer BEAM_SCHEDULE    # 일람표만 확인
```

파싱 결과의 `suggestions`(기하 투표 + 이름 유사도)를 참고하되 **자동 적용하지 않는다.**
사람이 CSV 를 쓴다. 컬럼·opts 전체 표는 `CLAUDE.md`.

## ★ 선매칭 우선 (first-match-wins)

좁은/제외 패턴을 넓은 패턴 **위에** 둔다. `배수판_벽체` 는 `벽` 을 포함하므로
`WALL|벽|CON` 아래에 두면 영원히 가려진다 — 실제로 배수판이 200mm 벽으로 납품된 적이 있다.

수정 후 반드시 `result["shadowed_layer_rules"]` 와 `가려진 규칙` 경고를 확인한다.
그게 이 함정의 탐지기다.

`#` 주석의 함정: 패턴을 주석 처리해도 alternation 은 **첫 가지만** 죽는다.
`#WALL|벽|CON` 은 여전히 `벽`·`CON` 에 매칭된다. 줄 전체를 지우거나 옮긴다.

## 증상 → 규칙

| 증상 | 규칙 |
|---|---|
| 요소가 0개 | 패턴 오타. `--scan` 의 레이어명을 그대로 복사 |
| 벽이 두 줄로 나온다 | 양면 2선 도면이다. 그대로 두면 페어링이 처리한다 |
| 벽이 `single` 로 떨어진다 | 간격이 `pair_max`(기본 500mm)를 넘음 → `opts: pair_max=…` |
| 보/거더가 안 잡힌다 | 외곽선 간격이 500~2500mm. `opts: pair_max=1800` |
| **`[얇은 오결합]` 경고** | 파서가 레이어 중앙값 대비로 자동 감지해 `needs_review` 로 올린다. 경고문에 적힌 `pair_min` 값을 그대로 layer_map 에 넣으면 된다 |
| 벽 두께가 말이 안 되게 얇다(50mm 콘크리트 벽) | 대개 **같은 벽을 건축(A-WALL)과 구조(A-CON)가 각자 그려 50mm 어긋난 것**이다. 파서가 같은 레이어 쌍을 먼저 선점하게 돼 있어 자동으로 풀린다. 그래도 남으면 `opts: pair_min=80` |
| 보가 선 몇 개로만 있다 | 축선이 DIMENSION 이다 → `opts: from=dim`. 끝점은 `defpoint2`→`defpoint3` |
| `from=dim` 인데 부재가 너무 많다 | 상세도 기호까지 잡힌 것. `opts: member_re=^R[AS]` 로 좁힌다 |
| 보 치수가 다 똑같다 | 일람표를 안 붙였다 → `opts: schedule=<일람표레이어>` + 그 레이어는 `ignore` |
| 일람표 조인 0건 | 레이어명 오타. `schedule_table.py --layer` 로 표가 잡히는지 먼저 본다 |
| 보가 납작하다 | 표기 관례가 뒤집혔다. `section.notation` 확인 (`H 800x300`=춤×폭, `350x1100`=폭×춤) |
| 도면엔 있는데 모델에 넣기 싫다 | `category=ignore`. 세고 버린다(`result["ignored"]`) |
| **개구부가 수백 개** | 문 하나가 문짝선·스윙호·철물로 여러 엔티티다. 파서가 50mm 미만 조각을 버리고 `small_openings_dropped` 로 센다. 진짜 문은 대개 블록 INSERT 안에 있다 |
| 참조/xref 배경 (`$0$`, `(하부층)`) | `ignore`. `$0$` 는 xref bind 흔적이라 사실상 100% 신뢰 |

## 하지 말 것

- **모듈 전역 상수를 런타임에 덮어쓰기(몽키패치).** `opts` 가 그것 때문에 생겼고,
  적용값은 `tolerances_effective` 에 기록되어 산출물이 자기 튜닝을 스스로 말한다.
- 카테고리 오타를 방치 — 로드 시점에 `LayerMapError` 로 죽는다. 조용히 새 버킷이 생기지 않는다.
- 모르는 `opts` 키 — 오류다. 오타난 허용치를 조용히 무시하지 않는다.
- **헤더 없는 컬럼** — CSV 첫 줄이 `pattern,category,width,height,thickness,opts` 인지
  확인할 것. 5컬럼 헤더에 opts 를 적으면 예전엔 조용히 버려졌다(지금은 로드 시 오류).
- **매칭 실패를 기본값으로 때우기.** 일람표 미매칭은 `needs_review` 로 올린다.
  치수가 조용히 틀린 부재가 이 프로젝트에서 가장 비쌌던 실패다.
