"""
mcp_freecad_live.py - FreeCAD Live MCP Bridge Server

AI(Claude/Gemini)가 사용하는 MCP 도구 서버입니다.
내부적으로 FreeCAD 내에 구동된 freecad_live_addon.py (포트 8081)로
HTTP JSON 요청을 보내 실시간으로 문서를 조작합니다.
"""
import sys
import os
import json
import urllib.request
import urllib.error
import traceback

try:
    from mcp.server.fastmcp import FastMCP, Image
except ImportError:
    print("Error: 'mcp' package is required. Run 'pip install mcp'", file=sys.stderr)
    sys.exit(1)

mcp = FastMCP("FreeCAD_Live_Bridge", timeout=600)

RPC_URL = "http://127.0.0.1:8082"
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
    
try:
    import dxf_parser as _P
except ImportError as e:
    print(f"Warning: could not import dxf_parser ({e})")


def _rpc_request(endpoint: str, payload: dict = None) -> dict:
    url = f"{RPC_URL}/{endpoint}"
    try:
        if payload is not None:
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'}, method='POST')
        else:
            req = urllib.request.Request(url, method='GET')
            
        with urllib.request.urlopen(req, timeout=120.0) as response:
            res_body = response.read().decode('utf-8')
            return json.loads(res_body)
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()}


@mcp.tool()
def check_connection() -> str:
    """FreeCAD Live Add-on과 연결되어 있는지 확인합니다."""
    res = _rpc_request("ping")
    if res.get("status") == "ok":
        return "Connected to FreeCAD Live Server successfully."
    return f"Failed to connect: {res.get('error', 'Unknown error')}"


@mcp.tool()
def import_dxf_live(dxf_path: str) -> str:
    """
    2D DXF 도면을 FreeCAD 현재 뷰포트에 실시간으로 Import 합니다.
    """
    if not os.path.exists(dxf_path):
        return f"Error: DXF file not found at {dxf_path}"
    
    res = _rpc_request("import_dxf", {"path": dxf_path})
    if "error" in res:
        return f"Import failed: {res['error']}"
    return res.get("message", "Import successful.")


@mcp.tool()
def make_wall_live(start_x: float, start_y: float, end_x: float, end_y: float, width: float = 200.0, height: float = 2800.0) -> str:
    """
    FreeCAD 현재 화면에 단일 벽체를 즉시 생성합니다. 
    대화하며 모델링할 때 사용합니다.
    """
    pts = [[start_x, start_y, 0], [end_x, end_y, 0]]
    res = _rpc_request("make_wall", {"points": pts, "width": width, "height": height})
    
    if "error" in res:
        return f"Failed to make wall: {res['error']}"
    return f"Wall created successfully. (Label: {res.get('label')})"


@mcp.tool()
def auto_detect_walls(dxf_path: str, use_ai: bool = False) -> str:
    """
    강력한 DXF 파서를 백그라운드에서 실행하여 모든 벽, 기둥, 개구부를 자동으로 찾은 뒤,
    FreeCAD 화면에 한 번에 3D 모델로 생성합니다.
    """
    if not os.path.exists(dxf_path):
        return f"Error: DXF file not found at {dxf_path}"
        
    try:
        # 기존 dxf_parser 파이프라인 활용
        import contextlib
        import io as _io
        
        LAYER_MAP = os.path.join(HERE, "layer_map.csv")
        BLOCK_MAP = os.path.join(HERE, "block_map.csv")
        
        rules = _P.load_layer_map(LAYER_MAP) if os.path.exists(LAYER_MAP) else _P.DEFAULT_LAYER_RULES
        brules = _P.load_layer_map(BLOCK_MAP) if os.path.exists(BLOCK_MAP) else _P.DEFAULT_BLOCK_RULES
        
        with contextlib.redirect_stdout(_io.StringIO()):
            data = _P.parse(dxf_path, rules, brules, use_ai=use_ai)
            
    except Exception as e:
        return f"Failed to parse DXF locally: {e}\n{traceback.format_exc()[-800:]}"
        
    # 추출한 데이터를 RPC 서버로 쏴서 라이브로 빌드하게 함
    res = _rpc_request("build_geometry", {"geometry": data})
    
    if "error" in res:
        return f"Live build failed: {res['error']}"
        
    return f"Geometry live build successful. {res.get('summary', '')}"


@mcp.tool()
def get_selection() -> str:
    """FreeCAD에서 사용자가 **현재 클릭·선택한 객체**를 구조화해 반환한다.
    자연어 명령("이 벽에 창문", "여기 선들로 벽")의 대상을 파악하는 첫 단계.

    각 객체: role(wall|dxf_lines|other), label, picked_point(클릭한 정확한 3D 지점),
    wall이면 centerline/width/height, dxf_lines면 segment_count.

    권장 흐름:
      1) get_selection 으로 무엇이 선택됐는지 확인
      2) role=="dxf_lines" → make_wall_from_selection (선택한 DXF선을 벽으로)
         role=="wall"      → add_window_to_wall (그 벽에 창문/문)
      3) get_vision_screenshot 으로 결과 확인
    """
    res = _rpc_request("get_selection")
    if "error" in res:
        return f"Selection 읽기 실패: {res['error']}"
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool()
def make_wall_from_selection(height: float = 3000.0, width: float = 200.0, columns: bool = True) -> str:
    """FreeCAD에서 **현재 선택한 DXF 선들**을 결정론 페어링 엔진으로 분석해 Arch 벽(+기둥)으로
    즉시 빌드한다. "여기 벽 세워줘" 명령에 사용. 사용자가 먼저 FreeCAD에서 벽이 될 DXF 선을
    클릭 선택해야 한다(get_selection 으로 확인 가능).

    height: 벽 높이(mm). width: 페어링 검색 기준 두께(실제 두께는 평행선 간격에서 자동 산출).
    columns: 미페어링 선에서 기둥(박스)도 함께 검출할지.
    """
    res = _rpc_request("make_wall_from_selection",
                       {"height": height, "width": width, "columns": columns})
    if "error" in res:
        return f"벽 생성 실패: {res['error']}"
    return res.get("message", "벽 생성 완료.")


@mcp.tool()
def add_window_to_wall(width: float = 1200.0, height: float = 1500.0,
                       sill: float = -1.0, subtype: str = "window",
                       position: float = -1.0, wall_label: str = "") -> str:
    """**선택한 벽**(또는 wall_label)에 창문/문을 단건 삽입한다. "이 벽에 창문 넣어줘" 명령에 사용.
    파라메트릭 Part::Cut 으로 벽을 뚫어 recompute 후에도 유지되며 창틀/문짝 패널(IfcWindow/Door)을 둔다.

    위치 결정 우선순위: ① 사용자가 벽에서 클릭한 지점(picked_point) → 그 위치, ② position
    (0~1 비율 또는 mm, 벽 시작점 기준), ③ 둘 다 없으면 벽 중앙.
    width/height: 창 크기(mm). sill: 창대높이(mm, -1이면 창=900·문=0 기본). subtype: window|door.
    """
    payload = {"width": width, "height": height, "subtype": subtype}
    if sill >= 0:
        payload["sill"] = sill
    if position >= 0:
        payload["position"] = position
    if wall_label:
        payload["wall_label"] = wall_label
    res = _rpc_request("add_window_to_wall", payload)
    if "error" in res:
        return f"창문 삽입 실패: {res['error']}"
    return res.get("message", "창문 삽입 완료.")


@mcp.tool()
def get_vision_screenshot(fit_all: bool = False) -> Image:
    """
    현재 FreeCAD의 3D 뷰포트를 캡처하여 반환합니다. AI가 화면 상태를 보고 판단할 때 필수적입니다.
    fit_all: True면 전체 객체가 보이도록 줌을 맞춥니다.
    """
    res = _rpc_request("screenshot", {"width": 1280, "height": 720, "fit_all": fit_all})
    
    if "error" in res:
        raise RuntimeError(f"Screenshot failed: {res['error']}")
        
    b64_img = res.get("image_base64")
    if not b64_img:
        raise RuntimeError("No image data returned from FreeCAD.")
        
    import base64
    img_data = base64.b64decode(b64_img)
    return Image(data=img_data, format="png")


if __name__ == "__main__":
    mcp.run()
