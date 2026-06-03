# MEP Parser — MCP 서버 (대화형 모델링)

기존 결정론 엔진(`dxf_parser`, `freecad_builder`)을 **MCP 도구**로 노출해,
Claude·Gemini가 자연어로 DXF→FreeCAD 파이프라인을 지휘하게 한다.

## 원칙
- AI/LLM은 **FreeCAD 코드를 생성하지 않는다.** 대화로 '지휘'만 하고 좌표는 엔진이 계산.
- 기하 추출 100% 결정론(ezdxf). AI는 모호한 분류만 보조.
- 단일 계약 = `geometry.json`.

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
| `update_geometry_overrides(category, index, overrides, json_path?)` | 요소 치수 덮어쓰기. 키: `width`/`height`/`thickness`(mm) |
| `change_category(old, index, new, json_path?)` | 오분류 요소 카테고리 이동(개별) |
| `apply_layer_rule(layer_pattern, category, width?, height?, thickness?)` | **권장**: layer_map.csv에 규칙 추가 후 재파싱(원천 수정, 결정론적) |
| `build_freecad(out_name, json_path?)` | FreeCAD 빌드 → .FCStd/.ifc. 한글경로 안전(ASCII 임시→이동) |

## 대화 예시
```
나: "지하2층 건축평면도.dxf 파싱해줘"
AI: parse_dxf(...) → "walls=744 cols=76 openings=307, 미매핑 12건..."
나: "미매핑 A-PIPE는 배관이야"
AI: apply_layer_rule("A-PIPE", "pipe", width=100) → "추가됨, 재파싱 필요"
    parse_dxf(...) → 갱신
나: "빌드해줘"
AI: build_freecad("model_v1", ...) → "OK, model_v1.FCStd / .ifc 생성"
```

## 흐름
`parse_dxf` → `get_review_items` → (`apply_layer_rule` / `update_geometry_overrides`
/ `change_category`) → `build_freecad`
