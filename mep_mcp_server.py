"""
mep_mcp_server.py  —  MEP Parser 결정론 엔진을 MCP 도구로 노출

설계 원칙(프로젝트 불변):
- 기존 100% 결정론 파이프라인(dxf_parser, freecad_builder)만 도구로 노출.
- AI/LLM 은 FreeCAD 코드를 생성하지 않는다. 대화로 '지휘'만 하고 좌표는 엔진이 계산.
- 수정의 단일 저장소 = project.json; geometry.json은 최신 revision에서 파생한다.

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

mcp = FastMCP("MEP_Parser_Bridge")  # 빌드 타임아웃은 build_freecad 의 subprocess 호출에서 처리
                                    # (FastMCP 생성자는 timeout 인자 미지원 — 버전 호환)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_JSON = os.path.join(HERE, "geometry.json")
LAYER_MAP = os.path.join(HERE, "layer_map.csv")
BLOCK_MAP = os.path.join(HERE, "block_map.csv")

# 엔진을 직접 import(in-process) — subprocess 재실행보다 빠르고 안정적.
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import dxf_parser as _P  # noqa: E402
from project_server import open_source_project, session_from_geometry, verified_artifacts
from project_store import RevisionConflict, atomic_json


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
def parse_dxf(dxf_path: str, json_out_path: str = "", use_ai: bool | None = None,
              use_vision: bool | None = None) -> str:
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
    if dxf_path.lower().endswith(".dwg"):
        try:
            from dwg_converter import ensure_dxf as _ensure_dxf
            dxf_path = _ensure_dxf(dxf_path, log=lambda *_: None)
        except RuntimeError as _de:
            return f"Error: {_de}"
    if not json_out_path:
        json_out_path = os.path.splitext(dxf_path)[0] + ".geometry.json"

    # in-process 호출(subprocess 재실행 없음 → 빠르고 안정적).
    # ★ parse()의 print()가 MCP stdout(JSON-RPC)을 오염시키므로 반드시 리다이렉트.
    import contextlib
    import io as _io
    try:
        session = open_source_project(dxf_path, layer_map=LAYER_MAP if os.path.exists(LAYER_MAP) else None,
                                      block_map=BLOCK_MAP if os.path.exists(BLOCK_MAP) else None,
                                      options=dict(use_ai=bool(use_ai), use_vision=bool(use_vision)))
        manifest = session.store.read()
        sources = manifest['sources']
        options = dict(sources[0].get('options') or {})
        if use_ai is not None:
            options['use_ai'] = use_ai
        if use_vision is not None:
            options['use_vision'] = use_vision
        if sources[0].get('options') != options:
            sources[0]['options'] = options
            session.store.update_sources(sources, manifest['options'], manifest['revision'], manifest['project_id'])
        with contextlib.redirect_stdout(_io.StringIO()):
            state = session.state()
        data = state['geometry']
        atomic_json(json_out_path, data)
    except Exception as e:
        import traceback
        return f"Failed to parse DXF: {e}\n{traceback.format_exc()[-800:]}"

    lines = [f"Project {state['project_id']} revision {state['revision']} (canonical edits saved)", f"Parsed '{dxf_path}' -> '{json_out_path}'", "Elements:"]
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
        data = session_from_geometry(json_path).state()['geometry']
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
                    "category": cat, "eid": item.get("eid"), "index": i, "layer": item.get("layer"),
                    "pairing": item.get("pairing"), "kind": item.get("kind"),
                    "width_detected": item.get("width_detected"),
                    "confidence": item.get("confidence"),
                    "review_reason": item.get("review_reason"),
                    "edit_diagnostics": item.get("edit_diagnostics", []),
                    "overrides": item.get("overrides", {}),
                })
    out = {
        "project": data.get("project"),
        "edits_report": data.get("edits_report", {}),
        "unmapped_suggestions": suggestions,
        "review_elements": review_elements,
        "summary": (f"{len(suggestions)} unmapped layer/block(s), "
                    f"{len(review_elements)} element(s) need review."),
    }
    return json.dumps(out, indent=2, ensure_ascii=False)


def _edit_project(json_path, eid, expected_revision, patch):
    if not eid or expected_revision < 0:
        return "Error: eid and expected_revision are required; legacy index-only mutations are rejected."
    try:
        session = session_from_geometry(json_path)
        manifest = session.store.read()
        result = session.edit(eid, patch, expected_revision, manifest['project_id'])
        atomic_json(json_path, result['geometry'])
        return json.dumps(result, ensure_ascii=False)
    except RevisionConflict as exc:
        return json.dumps({'error':'revision_conflict','current_revision':exc.current_revision})
    except Exception as exc:
        return f"Error updating project: {exc}"


@mcp.tool()
def update_geometry_overrides(category: str = "", index: int = -1, overrides: dict | None = None,
                              json_path: str = DEFAULT_JSON, eid: str = "",
                              expected_revision: int = -1, acknowledge: bool = False) -> str:
    """Persist parameter changes by EID and expected project revision, then reparse.

    index is accepted only for migration compatibility and never selects an element.
    acknowledge explicitly confirms the current review fingerprint; property edits alone do not.
    """
    norm = {"width" if k == "width_detected" else k:v for k,v in (overrides or {}).items()}
    return _edit_project(json_path, eid, expected_revision, dict(overrides=norm, acknowledge=acknowledge))


@mcp.tool()
def change_category(old_category: str = "", index: int = -1, new_category: str = "",
                    json_path: str = DEFAULT_JSON, eid: str = "", expected_revision: int = -1) -> str:
    """Persist an EID category change using revision CAS, then rerun parsing and opening linkage."""
    if new_category not in _P.VALID_CATEGORIES:
        return "Error: unsupported category"
    return _edit_project(json_path, eid, expected_revision, {'category':new_category})


@mcp.tool()
def discard_project_edit(eid: str, expected_revision: int, json_path: str = DEFAULT_JSON) -> str:
    """Explicitly discard one saved edit, retaining the decision in the project manifest."""
    try:
        session = session_from_geometry(json_path)
        result = session.discard(eid, expected_revision, session.store.read()['project_id'])
        atomic_json(json_path, result['geometry'])
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def relink_project_edit(orphan: str, target: str, expected_revision: int,
                        json_path: str = DEFAULT_JSON) -> str:
    """Explicitly associate an orphaned edit with a reviewed current EID; never overwrites target edits."""
    try:
        session = session_from_geometry(json_path)
        result = session.relink(orphan, target, expected_revision, session.store.read()['project_id'])
        atomic_json(json_path, result['geometry'])
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def apply_layer_rule(layer_pattern: str, category: str,
                     width: float = 0, height: float = 0, thickness: float = 0,
                     opts: str = "") -> str:
    """
    Step 3c (권장): layer_map.csv 에 분류 규칙을 추가한다. 원천 수정이라 재파싱 시
    결정론적으로 반영된다(per-item change_category 보다 견고).

    추가 후에는 parse_dxf 를 다시 호출해야 반영된다.

    Args:
        layer_pattern: 레이어명 정규식(예: 'A-PIPE|배관').
        category: wall|column|slab|beam|zone|opening|pipe|duct|tray|equipment|ignore.
                  'ignore' = 도면에는 있으나 모델에 넣지 않을 레이어(세고 버림).
        width/height/thickness: mm (0이면 비움 = 기본값 사용).
        opts: 'key=value;key=value' 레이어별 튜닝. 사용 가능한 키:
              pair_max/pair_min  — 이 레이어의 평행선 페어링 간격(mm).
                                   보/거더 외곽선은 벽보다 넓다(500~2500).
              from=dim           — DIMENSION 을 부재 축선으로 해석(구조도 관행).
              member_re          — from=dim 에서 부재명으로 인정할 정규식.
              schedule=<레이어>   — 부재일람표 레이어. 부재명 → 실제 폭×춤 조인.
              모르는 키는 여기서 거절한다(다음 파싱까지 미루지 않는다).

    주의: 규칙은 **선매칭 우선**이다. 좁은/제외 규칙은 넓은 규칙 위에 있어야 한다.
    (예: '배수판_벽체'는 '벽'을 포함하므로 'WALL|벽|CON' 아래에 두면 영원히 가려진다.)
    추가 후 parse_dxf 를 다시 돌려 warnings 의 '가려진 규칙' 경고를 확인할 것.
    """
    valid = ("wall", "column", "slab", "beam", "zone", "opening",
             "pipe", "duct", "tray", "equipment", "ignore")
    if category not in valid:
        return f"Error: category must be one of {valid}."
    if not os.path.exists(LAYER_MAP):
        return f"Error: layer_map.csv not found at {LAYER_MAP}."
    if opts:
        try:
            _P._parse_opts(opts, LAYER_MAP, 0)
        except Exception as e:
            return f"Error: opts 형식 오류 — {e}"
    w = str(width) if width else ""
    h = str(height) if height else ""
    t = str(thickness) if thickness else ""
    try:
        with open(LAYER_MAP, "a", encoding="utf-8") as f:
            f.write(f"\n{layer_pattern},{category},{w},{h},{t},{opts}")
        return (f"Added rule '{layer_pattern}' -> {category} to layer_map.csv. "
                "Re-run parse_dxf to apply.")
    except Exception as e:
        return f"Error writing layer_map.csv: {e}"


@mcp.tool()
def diagnose_build(json_path: str = DEFAULT_JSON, top_n: int = 10,
                   make_image: bool = False) -> str:
    """
    Step 2b (자기검증): 파싱/빌드 품질을 스스로 진단한다 — "vision in the loop".

    geometry.json 의 qa(면선 커버리지, 미커버=누락 의심 선분)와 미매핑 레이어 제안을
    함께 반환한다. AI 는 이 결과를 보고 apply_layer_rule / update_geometry_overrides
    로 수정 → parse_dxf 재실행 → 다시 diagnose_build 로 확인하는 루프를 돈다.

    Args:
        json_path: geometry.json 경로 (parse_dxf 산출물, qa 포함).
        top_n: 미커버 선분 상위 보고 개수.
        make_image: True 면 진단 오버레이 PNG(diag_overlay)도 생성해 경로 반환.
    """
    if not os.path.exists(json_path):
        return f"Error: JSON not found at {json_path}. Run parse_dxf first."
    try:
        data = session_from_geometry(json_path).state()['geometry']
    except Exception as e:
        return f"Error reading JSON: {e}"
    qa = data.get("qa")
    if not qa:
        return ("No qa data in this geometry.json (parsed by an older version). "
                "Re-run parse_dxf to generate coverage QA.")

    # 미커버 선분을 레이어별로 묶어 원인 진단을 돕는다
    by_layer = {}
    for u in qa.get("uncovered", []):
        d = by_layer.setdefault(u.get("layer", "?"), {"count": 0, "total_mm": 0.0})
        d["count"] += 1
        d["total_mm"] += u.get("length_mm", 0.0)
    out = {
        "face_coverage_pct": qa.get("face_coverage_pct"),
        "face_total_m": qa.get("face_total_m"),
        "uncovered_count": qa.get("uncovered_count"),
        "uncovered_by_layer": by_layer,
        "uncovered_top": qa.get("uncovered", [])[:top_n],
        "wall_pairing": data.get("wall_pairing", {}),
        "needs_review": qa.get("needs_review"),
        "unmapped_suggestions": [
            {"layer": s.get("layer"), "count": s.get("count"),
             "final_guess": s.get("final_guess")}
            for s in data.get("suggestions", []) if not s.get("applied")],
        "hint": ("uncovered_by_layer 에 특정 레이어가 몰려 있으면 그 레이어가 "
                 "미매핑이거나 반대 면선이 다른 레이어일 가능성 → apply_layer_rule 검토. "
                 "고르게 퍼져 있으면 개구부/특수 형상일 가능성이 높음."),
    }
    if make_image:
        try:
            import diag_overlay as _DG
            png, _ = _DG.build_overlay(json_path)
            out["overlay_png"] = png
        except Exception as e:
            out["overlay_png_error"] = str(e)
    return json.dumps(out, indent=2, ensure_ascii=False)


@mcp.tool()
def build_freecad(out_name: str, json_path: str = DEFAULT_JSON) -> str:
    """
    Step 4: geometry.json 을 FreeCAD 3D 모델(.FCStd)과 IFC 로 빌드한다. 마지막 단계.

    최신 프로젝트 revision을 재파싱하고 격리된 FreeCAD 실행기로 빌드한다.
    현재 실행의 검사 영수증, 입력 지문, 실제 파일 해시가 일치할 때만 verified를 반환한다.

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

    try:
        build_input, build_state = session_from_geometry(json_path).export_geometry()
        from freecad_runner import run_build
        result = run_build(fc, build_input, out_name, os.path.join(HERE, 'freecad_builder.py'))
        artifacts = verified_artifacts(out_name + '.build.json', build_state['geometry'], result.run_id)
        return json.dumps({'project_id':build_state['project_id'], 'revision':build_state['revision'],
                           'artifacts':artifacts, 'report':out_name + '.build.json',
                           'status':'verified' if all(a['status']=='verified' for a in artifacts.values()) else 'failed'},
                          ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"Build failed: {exc}"


@mcp.tool()
def get_pascal_snapshot(json_path: str = DEFAULT_JSON) -> str:
    """프로젝트를 Pascal 씬 그래프로 내보낸다 — 편집 화면이 받는 것과 같은 스냅샷.

    반환: project_id · revision · snapshot_sha256 · scene · report(표현 불가 목록 포함).
    적용할 때 revision 과 snapshot_sha256 을 그대로 돌려줘야 한다(낡은 씬 차단).
    """
    try:
        return json.dumps(session_from_geometry(json_path).pascal_snapshot(), ensure_ascii=False)
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def apply_pascal_scene(scene: dict, expected_revision: int, snapshot_sha256: str, op_id: str,
                       dry_run: bool = True, json_path: str = DEFAULT_JSON) -> str:
    """편집한 Pascal 씬을 **변경 명령**으로 저장한다 — 전체 덮어쓰기가 아니다.

    dry_run=True(기본)는 만들 명령과 검증 결과만 돌려준다. 실제로 저장하려면 결과를
    사람에게 보여 준 뒤 dry_run=False 로 다시 부른다. 같은 op_id 는 두 번 적용되지 않는다.
    화면에서 빠진 노드 중 **애초에 못 보낸 부재**는 삭제로 읽지 않는다.
    """
    try:
        session = session_from_geometry(json_path)
        result = session.pascal_apply(scene, expected_revision, session.store.read()['project_id'],
                                      snapshot_sha256, op_id, dry_run)
        state = result.pop('state', None)
        if isinstance(state, dict):
            result['revision'] = state.get('revision')   # 기하 전체는 응답에 싣지 않는다
        return json.dumps(result, ensure_ascii=False)
    except RevisionConflict as exc:
        return json.dumps({'error': str(exc), 'current_revision': exc.current_revision},
                          ensure_ascii=False)
    except Exception as exc:
        return f"Error: {exc}"


if __name__ == "__main__":
    mcp.run()
