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
