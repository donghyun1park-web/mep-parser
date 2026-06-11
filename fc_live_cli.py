"""
fc_live_cli.py - Antigravity 제어용 CLI 도구

Antigravity(Gemini)가 `run_command` 도구를 통해 FreeCAD 라이브 서버(8081 포트)를
직접 제어할 수 있게 해주는 커맨드라인 인터페이스입니다.
"""
import sys
import json
import urllib.request
import urllib.error
import argparse
import os

RPC_URL = "http://127.0.0.1:8082"
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

def _rpc_request(endpoint: str, payload: dict = None):
    url = f"{RPC_URL}/{endpoint}"
    try:
        if payload is not None:
            data = json.dumps(payload).encode('utf-8')
            req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'}, method='POST')
        else:
            req = urllib.request.Request(url, method='GET')
            
        with urllib.request.urlopen(req, timeout=600.0) as response:
            res_body = response.read().decode('utf-8')
            return json.loads(res_body)
    except Exception as e:
        return {"error": str(e)}

def do_ping(args):
    print(json.dumps(_rpc_request("ping"), indent=2))

def do_import(args):
    path = os.path.abspath(args.path)
    print(json.dumps(_rpc_request("import_dxf", {"path": path}), indent=2))

def do_make_wall(args):
    pts = [[args.x1, args.y1, 0], [args.x2, args.y2, 0]]
    res = _rpc_request("make_wall", {"points": pts, "width": args.width, "height": args.height})
    print(json.dumps(res, indent=2))

def do_auto_build(args):
    path = os.path.abspath(args.path)
    try:
        import dxf_parser as _P
        import contextlib
        import io as _io
        
        LAYER_MAP = os.path.join(HERE, "layer_map.csv")
        BLOCK_MAP = os.path.join(HERE, "block_map.csv")
        rules = _P.load_layer_map(LAYER_MAP) if os.path.exists(LAYER_MAP) else _P.DEFAULT_LAYER_RULES
        brules = _P.load_layer_map(BLOCK_MAP) if os.path.exists(BLOCK_MAP) else _P.DEFAULT_BLOCK_RULES
        
        print("Parsing DXF locally...", file=sys.stderr)
        with contextlib.redirect_stdout(_io.StringIO()):
            data = _P.parse(path, rules, brules, use_ai=False)
            
        print("Sending to FreeCAD live server...", file=sys.stderr)
        res = _rpc_request("build_geometry", {"geometry": data})
        print(json.dumps(res, indent=2))
    except Exception as e:
        print(json.dumps({"error": str(e)}))

def do_screenshot(args):
    res = _rpc_request("screenshot", {"width": args.width, "height": args.height, "fit_all": args.fit})
    if "error" in res:
        print(json.dumps(res, indent=2))
        return
        
    out_path = os.path.abspath(args.out)
    import base64
    with open(out_path, "wb") as f:
        f.write(base64.b64decode(res["image_base64"]))
    print(json.dumps({"status": "ok", "saved_to": out_path}))

def do_exec(args):
    script_path = os.path.abspath(args.path)
    if not os.path.exists(script_path):
        print(json.dumps({"error": f"File not found: {script_path}"}))
        return
    with open(script_path, "r", encoding="utf-8") as f:
        code = f.read()
    res = _rpc_request("exec_python", {"code": code})
    print(json.dumps(res, indent=2))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Antigravity Live FreeCAD Controller")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    subparsers.add_parser("ping")
    
    p_exec = subparsers.add_parser("exec")
    p_exec.add_argument("path", help="Path to Python script to execute")
    
    p_import = subparsers.add_parser("import")
    p_import.add_argument("path", help="DXF path")
    
    p_wall = subparsers.add_parser("make_wall")
    p_wall.add_argument("x1", type=float)
    p_wall.add_argument("y1", type=float)
    p_wall.add_argument("x2", type=float)
    p_wall.add_argument("y2", type=float)
    p_wall.add_argument("--width", type=float, default=200.0)
    p_wall.add_argument("--height", type=float, default=2800.0)
    
    p_auto = subparsers.add_parser("auto_build")
    p_auto.add_argument("path", help="DXF path")
    
    p_screen = subparsers.add_parser("screenshot")
    p_screen.add_argument("out", help="Output PNG path")
    p_screen.add_argument("--width", type=int, default=1280)
    p_screen.add_argument("--height", type=int, default=720)
    p_screen.add_argument("--fit", action="store_true", help="Fit all before screenshot")
    
    args = parser.parse_args()
    
    if args.command == "ping": do_ping(args)
    elif args.command == "exec": do_exec(args)
    elif args.command == "import": do_import(args)
    elif args.command == "make_wall": do_make_wall(args)
    elif args.command == "auto_build": do_auto_build(args)
    elif args.command == "screenshot": do_screenshot(args)
