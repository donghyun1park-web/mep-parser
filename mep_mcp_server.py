"""
mep_mcp_server.py  —  MEP Parser 결정론 엔진을 MCP 도구로 노출

설계 원칙(프로젝트 불변):
- 기존 100% 결정론 파이프라인(dxf_parser, freecad_builder)만 도구로 노출.
- AI/LLM 은 FreeCAD 코드를 생성하지 않는다. 대화로 '지휘'만 하고 좌표는 엔진이 계산.
- 단일 계약 = geometry.json.

도구 흐름: parse_dxf → get_review_items → (update_overrides / change_category /
           apply_layer_rule) → build_freecad

실행: python mep_mcp_server.py   (stdio MCP 서버 — Claude Desktop/Code, Gemini CLI 호환)
"""
import glob
import json
import os
import subprocess
import sys

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("Error: 'mcp' package is required. Run 'pip install mcp'", file=sys.stderr)
    sys.exit(1)

mcp = FastMCP("MEP_Parser_Bridge")

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_JSON = os.path.join(HERE, "geometry.json")
LAYER_MAP = os.path.join(HERE, "layer_map.csv")
BLOCK_MAP = os.path.join(HERE, "block_map.csv")


def _find_freecadcmd():
    """freecadcmd.exe 자동 탐지(mep_gui 패턴 재사용). 없으면 None."""
    cands = [r"C:\Program Files\FreeCAD 1.1\bin\freecadcmd.exe"]
    cands += glob.glob(r"C:\Program Files\FreeCAD*\bin\freecadcmd.exe")
    cands += glob.glob(r"C:\Program Files (x86)\FreeCAD*\bin\freecadcmd.exe")
    for c in cands:
        if os.path.exists(c):
            return c
    return None


def _run_cmd(cmd_list, env=None, timeout=900):
    try:
        r = subprocess.run(cmd_list, cwd=HERE, env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
        if r.returncode != 0:
            return False, f"exit {r.returncode}\nSTDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
        return True, r.stdout
    except subprocess.TimeoutExpired:
        return False, f"Command timed out after {timeout}s"
    except Exception as e:
        return False, str(e)


@mcp.tool()
def parse_dxf(dxf_path: str, json_out_path: str = "", use_ai: bool = False,
              use_vision: bool = False) -> str:
    """
    Step 1: DXF 도면을 파싱해 geometry.json 을 생성한다. DXF 작업의 첫 단계.

    프로젝트 튜닝된 layer_map.csv / block_map.csv 를 항상 사용한다(있을 때).
    use_ai: 모호 레이어/블록을 LLM 으로 분류 + 고신뢰 자동적용(ANTHROPIC_API_KEY 필요).
    use_vision: 저신뢰 항목에 Vision 폴백(실험적, API key 필요).
    좌표/형상은 100% 결정론(ezdxf)로 추출 — AI 는 분류만 보조.

    Args:
        dxf_path: 파싱할 .dxf 절대경로.
        json_out_path: 출력 .json 경로. 비우면 DXF 옆에 <name>.geometry.json.
        use_ai: LLM 분류 자동적용 여부.
        use_vision: Vision 폴백 여부.
    """
    if not os.path.exists(dxf_path):
        return f"Error: DXF file not found at {dxf_path}"
    if not json_out_path:
        json_out_path = os.path.splitext(dxf_path)[0] + ".geometry.json"

    cmd = [sys.executable, os.path.join(HERE, "dxf_parser.py"), dxf_path,
           "--out", json_out_path]
    if os.path.exists(LAYER_MAP):
        cmd += ["-m", LAYER_MAP]
    if os.path.exists(BLOCK_MAP):
        cmd += ["-b", BLOCK_MAP]
    if use_ai:
        cmd.append("--llm")          # 실제 CLI 플래그는 --llm
    if use_vision:
        cmd.append("--vision")

    success, output = _run_cmd(cmd)
    if not success:
        return f"Failed to parse DXF:\n{output}"

    try:
        with open(json_out_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return f"Parse succeeded but JSON read failed: {e}\n\nParser output:\n{output}"

    lines = [f"Parsed '{dxf_path}' -> '{json_out_path}'", "Elements:"]
    for cat, items in data.get("elements", {}).items():
        if items:
            lines.append(f"  - {cat}: {len(items)}")
    wp = data.get("wall_pairing", {})
    if wp:
        lines.append(f"Wall pairing: paired={wp.get('paired',0)} "
                     f"single={wp.get('single',0)} single_offset={wp.get('single_offset',0)}")
    sugg = data.get("suggestions", [])
    nrev = sum(1 for items in data.get("elements", {}).values()
               for it in items if it.get("needs_review"))
    lines.append(f"Review: {len(sugg)} unmapped layer/block suggestion(s), "
                 f"{nrev} element(s) flagged needs_review.")
    lines.append("(Use get_review_items to inspect; json_path='%s')" % json_out_path)
    for w in data.get("warnings", [])[:8]:
        lines.append(f"  [warn] {w}")
    return "\n".join(lines)


@mcp.tool()
def get_review_items(json_path: str = DEFAULT_JSON) -> str:
    """
    Step 2: 검토가 필요한 항목을 조회한다. 두 종류를 함께 반환:
      - suggestions: 미매핑 레이어/블록 + 기하/이름/LLM 추측(어느 카테고리로 볼지 판단용).
      - review_elements: needs_review=True 인 개별 요소(치수/방향 확정 필요).
    AI 는 이 정보로 사용자와 상의해 update_geometry_overrides / change_category /
    apply_layer_rule 로 해결한다.
    """
    if not os.path.exists(json_path):
        return f"Error: JSON not found at {json_path}. Run parse_dxf first."
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return f"Error reading JSON: {e}"

    suggestions = []
    for s in data.get("suggestions", []):
        suggestions.append({
            "layer": s.get("layer"), "source": s.get("source"),
            "count": s.get("count"),
            "geom_guess": s.get("geom_guess"), "geom_subtype": s.get("geom_subtype"),
            "name_guess": s.get("name_guess"),
            "llm_guess": s.get("llm_guess"), "llm_confidence": s.get("llm_confidence"),
            "final_guess": s.get("final_guess"), "applied": s.get("applied", False),
        })
    review_elements = []
    for cat, items in data.get("elements", {}).items():
        for i, item in enumerate(items):
            if item.get("needs_review"):
                review_elements.append({
                    "category": cat, "index": i, "layer": item.get("layer"),
                    "pairing": item.get("pairing"), "kind": item.get("kind"),
                    "width_detected": item.get("width_detected"),
                    "confidence": item.get("confidence"),
                })
    out = {
        "unmapped_suggestions": suggestions,
        "review_elements": review_elements,
        "summary": (f"{len(suggestions)} unmapped layer/block(s), "
                    f"{len(review_elements)} element(s) need review."),
    }
    if not suggestions and not review_elements:
        return "No review needed. Proceed to build_freecad."
    return json.dumps(out, indent=2, ensure_ascii=False)


@mcp.tool()
def update_geometry_overrides(category: str, index: int, overrides: dict,
                              json_path: str = DEFAULT_JSON) -> str:
    """
    Step 3: 특정 요소의 파라미터를 덮어쓴다(모호 항목 해결).

    overrides 키는 빌더가 읽는 이름이어야 한다: 'width'(mm) / 'height'(mm) /
    'thickness'(mm). (주의: 'width_detected' 가 아니라 'width' 사용 — 들어오면 자동 변환.)
    예: {"width": 200, "height": 3000}

    Args:
        category: 요소 카테고리 (wall/column/slab/zone/opening/pipe/duct/tray/equipment).
        index: 해당 카테고리 리스트 내 인덱스.
        overrides: 덮어쓸 속성 dict.
        json_path: geometry.json 경로.
    """
    if not os.path.exists(json_path):
        return f"Error: JSON not found at {json_path}."
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("elements", {}).get(category)
        if items is None:
            return f"Error: category '{category}' not found."
        if not (0 <= index < len(items)):
            return f"Error: index {index} out of range (size {len(items)})."
        item = items[index]
        ov = item.setdefault("overrides", {})
        norm = {}
        for k, v in overrides.items():
            # 빌더가 무시하는 width_detected → width 로 정규화
            norm["width" if k == "width_detected" else k] = v
        ov.update(norm)
        item["needs_review"] = False
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return f"Updated {category}[{index}] overrides={norm}."
    except Exception as e:
        return f"Error updating JSON: {e}"


@mcp.tool()
def change_category(old_category: str, index: int, new_category: str,
                    json_path: str = DEFAULT_JSON) -> str:
    """
    Step 3b: 오분류된 요소를 다른 카테고리로 이동한다(예: wall -> duct).

    한계: 이동된 요소는 wall 후처리(pairing/merge/snap)나 MEP 필드 산출을 재실행하지 않는다.
    레이어 전체를 재분류하려면 apply_layer_rule 가 더 정확하다(원천에서 다시 파싱).
    """
    if not os.path.exists(json_path):
        return f"Error: JSON not found at {json_path}."
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        elements = data.setdefault("elements", {})
        items = elements.get(old_category)
        if items is None:
            return f"Error: category '{old_category}' not found."
        if not (0 <= index < len(items)):
            return f"Error: index {index} out of range."
        elements.setdefault(new_category, [])
        item = items.pop(index)
        item["needs_review"] = False
        elements[new_category].append(item)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        warn = ""
        if new_category in ("pipe", "duct", "tray", "equipment"):
            warn = (" [warn] MEP 카테고리는 diameter/elevation/width_mm 등 필드가 필요할 수 "
                    "있음 — update_geometry_overrides 로 보강 권장.")
        return (f"Moved {old_category}[{index}] -> {new_category}"
                f"[{len(elements[new_category])-1}].{warn}")
    except Exception as e:
        return f"Error updating JSON: {e}"


@mcp.tool()
def apply_layer_rule(layer_pattern: str, category: str,
                     width: float = 0, height: float = 0, thickness: float = 0) -> str:
    """
    Step 3c (권장): layer_map.csv 에 분류 규칙을 추가한다. 원천 수정이라 재파싱 시
    결정론적으로 반영된다(per-item change_category 보다 견고).

    추가 후에는 parse_dxf 를 다시 호출해야 반영된다.

    Args:
        layer_pattern: 레이어명 정규식(예: 'A-PIPE|배관').
        category: wall|column|slab|zone|opening|pipe|duct|tray|equipment.
        width/height/thickness: mm (0이면 비움 = 기본값 사용).
    """
    valid = ("wall", "column", "slab", "zone", "opening",
             "pipe", "duct", "tray", "equipment")
    if category not in valid:
        return f"Error: category must be one of {valid}."
    if not os.path.exists(LAYER_MAP):
        return f"Error: layer_map.csv not found at {LAYER_MAP}."
    w = str(width) if width else ""
    h = str(height) if height else ""
    t = str(thickness) if thickness else ""
    try:
        with open(LAYER_MAP, "a", encoding="utf-8") as f:
            f.write(f"\n{layer_pattern},{category},{w},{h},{t}")
        return (f"Added rule '{layer_pattern}' -> {category} to layer_map.csv. "
                "Re-run parse_dxf to apply.")
    except Exception as e:
        return f"Error writing layer_map.csv: {e}"


@mcp.tool()
def build_freecad(out_name: str, json_path: str = DEFAULT_JSON) -> str:
    """
    Step 4: geometry.json 을 FreeCAD 3D 모델(.FCStd)과 IFC 로 빌드한다. 마지막 단계.

    한글/공백 경로 안전: 빌더는 ASCII 임시파일에 저장 후 stdout 마커로 최종경로를 알리고,
    이 도구가 shutil.move 로 옮긴다(파일이 실제로 최종경로에 존재함을 보장).

    Args:
        out_name: 출력 베이스 경로(절대경로 권장). 'C:/.../model' -> model.FCStd / model.ifc.
                  상대경로면 json_path 폴더 기준.
        json_path: geometry.json 경로.
    """
    import shutil
    if not os.path.exists(json_path):
        return f"Error: JSON not found at {json_path}. Run parse_dxf first."
    fc = _find_freecadcmd()
    if not fc:
        return "Error: freecadcmd.exe not found. Install FreeCAD or add to PATH."

    # out_name 절대경로화(상대면 json 폴더 기준)
    if not os.path.isabs(out_name):
        out_name = os.path.join(os.path.dirname(os.path.abspath(json_path)), out_name)

    env = dict(os.environ, MEP_GEOMETRY=json_path, MEP_OUT=out_name,
               PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    success, output = _run_cmd([fc, os.path.join(HERE, "freecad_builder.py")], env=env)

    # 빌더 stdout 마커 파싱 → 임시파일을 최종경로로 이동.
    # FreeCAD 진행바(개행 없는 '(75%')가 마커 앞에 붙을 수 있어 startswith 대신 find 사용.
    moves = {}
    for line in output.splitlines():
        for tag in ("FCSTD_TMP", "FCSTD_DST", "IFC_TMP", "IFC_DST"):
            idx = line.find(tag + ":")
            if idx >= 0:
                moves[tag] = line[idx + len(tag) + 1:].strip()
    moved = []
    try:
        if moves.get("FCSTD_TMP") and moves.get("FCSTD_DST") and os.path.exists(moves["FCSTD_TMP"]):
            os.makedirs(os.path.dirname(moves["FCSTD_DST"]) or ".", exist_ok=True)
            shutil.move(moves["FCSTD_TMP"], moves["FCSTD_DST"])
            moved.append(moves["FCSTD_DST"])
        if moves.get("IFC_TMP") and moves.get("IFC_DST") and os.path.exists(moves["IFC_TMP"]):
            shutil.move(moves["IFC_TMP"], moves["IFC_DST"])
            moved.append(moves["IFC_DST"])
    except Exception as e:
        return f"Build ran but file move failed: {e}\n\nLog:\n{output[-1500:]}"

    fcstd = moves.get("FCSTD_DST", out_name + ".FCStd")
    ok = os.path.exists(fcstd)
    if not success and not ok:
        return f"Failed to build:\n{output[-2000:]}"
    status = "OK" if ok else "FAILED (FCStd missing)"
    return (f"Build {status}.\nSaved: {', '.join(moved) if moved else '(none moved)'}\n\n"
            f"Log tail:\n{output[-1200:]}")


if __name__ == "__main__":
    mcp.run()
