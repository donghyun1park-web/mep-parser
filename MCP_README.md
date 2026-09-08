# MEP Parser — MCP 서버 (대화형 모델링)

기존 결정론 엔진(`dxf_parser`, `freecad_builder`)을 **MCP 도구**로 노출해,
Claude·Gemini가 자연어로 DXF→FreeCAD 파이프라인을 지휘하게 한다.

## 원칙
- AI/LLM은 **FreeCAD 코드를 생성하지 않는다.** 대화로 '지휘'만 하고 좌표는 엔진이 계산.
- 기하 추출 100% 결정론(ezdxf). AI는 모호한 분류만 보조.
- 기하 계약 = `geometry.json`. 수정의 원본 저장소는 도면 옆 `.mep/project.json`이며 GUI·브라우저와 공유한다.

## 설치
```bash
pip install mcp ezdxf shapely
# (선택) AI 분류: pip install anthropic, 환경변수 ANTHROPIC_API_KEY
# (선택) Vision: pip install matplotlib pillow
```

## Claude Desktop 연동
`%APPDATA%\Claude\claude_desktop_config.json` 에 `claude_desktop_config.json` 내용 병합
후 Claude Desktop 재시작. (경로는 환경에 맞게 수정)

## 도구
| 도구 | 역할 |
|------|------|
| `parse_dxf(dxf_path, json_out_path?, use_ai?, use_vision?)` | DXF 파싱 → geometry.json. **layer_map.csv/block_map.csv 자동 적용.** 요소·벽쌍·검토 요약 반환 |
| `get_review_items(json_path?)` | 미매핑 레이어/블록 제안(geom/name/llm 추측) + needs_review 요소 목록 |
| `update_geometry_overrides(eid=..., expected_revision=..., overrides=..., json_path=...)` | EID로 치수를 저장한다. 키: `width`/`height`/`thickness`(mm). `acknowledge=True`만 명시적 검토 완료로 처리 |
| `change_category(eid=..., expected_revision=..., new_category=..., json_path=...)` | EID 기반 분류 변경 후 재파싱. 위치 인덱스만 사용하는 옛 요청은 저장을 거부 |
| `apply_layer_rule(layer_pattern, category, width?, height?, thickness?)` | **권장**: layer_map.csv에 규칙 추가 후 재파싱(원천 수정, 결정론적) |
| `build_freecad(out_name, json_path?)` | 최신 프로젝트 revision 사본으로 빌드. 실제 파일과 현재 실행 검사 영수증이 일치한 산출물만 `verified`로 반환 |

`get_review_items`가 반환하는 프로젝트 revision과 대상 EID를 수정 요청에 사용한다. 다른 창의 저장이나 원본 도면 변경으로 revision이 달라지면 충돌이므로 최신 목록을 조회하고 다시 검토한다. 임의로 최신 revision만 끼워 넣어 오래된 수정을 덮어쓰지 않는다. 이전의 프로젝트 정보가 없는 JSON은 `parse_dxf`로 프로젝트를 만든 뒤 사용한다.

저장·복구·고아 재연결·진단용 산출물 구분은 [프로젝트 사용법](docs/project_workflow.md)을 참고한다.

## 대화 예시
```
나: "지하2층 건축평면도.dxf 파싱해줘"
AI: parse_dxf(...) → "walls=744 cols=76 openings=307, 미매핑 12건..."
나: "미매핑 A-PIPE는 배관이야"
AI: apply_layer_rule("A-PIPE", "pipe", width=100) → "추가됨, 재파싱 필요"
    parse_dxf(...) → 갱신
나: "빌드해줘"
AI: build_freecad("model_v1", ...) → 산출물별 verified/failed와 검사 보고서 경로
```

## 흐름
`parse_dxf` → `get_review_items` → (`apply_layer_rule` / `update_geometry_overrides`
/ `change_category`) → `build_freecad`
