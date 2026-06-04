"""
freecad_live_addon.py - FreeCAD 내부용 Live RPC 서버

이 스크립트는 FreeCAD 내장 파이썬 콘솔이나 매크로에서 실행해야 합니다.
백그라운드 스레드에서 HTTP 서버를 열어 MCP 브릿지(AI)로부터 오는 명령을 수신하고,
메인 GUI 스레드로 안전하게 위임하여 실시간으로 모델링/스크린샷을 수행합니다.
"""
import sys
import os
import json
import base64
import threading
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler

import FreeCAD as App
import FreeCADGui as Gui
import Arch
import Part

# PySide 호환성 처리 (FreeCAD 0.20+ / 1.0+)
try:
    from PySide6 import QtCore
except ImportError:
    try:
        from PySide2 import QtCore
    except ImportError:
        from PySide import QtCore

# 동일 폴더의 freecad_builder 임포트용
try:
    HERE = os.path.dirname(os.path.abspath(__file__))
except NameError:
    # 파이썬 콘솔에 직접 붙여넣기 할 때를 대비한 하드코딩 폴백
    HERE = r"c:\AI program\3D Modeling\mep-parser"

if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    import freecad_builder
except ImportError as e:
    print(f"Warning: could not import freecad_builder ({e})")


def run_in_main_thread(func, *args, **kwargs):
    """
    백그라운드 스레드에서 호출 시, FreeCADGui.doCommand를 이용해 
    메인 GUI 스레드의 명령 큐에 func 실행을 안전하게 밀어넣고 결과를 동기적으로 반환함.
    """
    import uuid
    import time
    
    result_key = f"task_{uuid.uuid4().hex}"
    
    # 전역 모듈(App) 객체를 활용해 스레드 간 데이터 공유
    if not hasattr(App, '_live_addon_tasks'):
        App._live_addon_tasks = {}
        
    App._live_addon_tasks[result_key] = {
        'func': func,
        'args': args,
        'kwargs': kwargs,
        'done': False,
        'result': None,
        'error': None
    }
    
    cmd = f"""
import FreeCAD as App
import traceback
task = App._live_addon_tasks['{result_key}']
try:
    task['result'] = task['func'](*task['args'], **task['kwargs'])
except Exception as e:
    traceback.print_exc()
    task['error'] = e
finally:
    task['done'] = True
"""
    # GUI 스레드에 명령 실행 예약
    Gui.doCommand(cmd)
    
    # 백그라운드 스레드에서 완료 대기 (최대 10분)
    start_t = time.time()
    while not App._live_addon_tasks[result_key]['done']:
        time.sleep(0.05)
        if time.time() - start_t > 600.0:
            raise TimeoutError("Execution on main thread timed out (doCommand).")
            
    task = App._live_addon_tasks.pop(result_key)
    if task['error']:
        raise task['error']
    return task['result']

# ----------------- Main Thread Functions ----------------- #

def ensure_active_document():
    doc = App.ActiveDocument
    if not doc:
        doc = App.newDocument("LiveAI_Doc")
    return doc

def cmd_import_dxf(payload):
    path = payload.get("path")
    if not path or not os.path.exists(path):
        return {"error": "Invalid path"}
    doc = ensure_active_document()
    import importDXF
    importDXF.insert(path, doc.Name)
    Gui.updateGui()
    return {"status": "ok", "message": f"Imported {path}"}

def cmd_make_wall(payload):
    doc = ensure_active_document()
    pts = payload.get("points", [])
    w = float(payload.get("width", 200.0))
    h = float(payload.get("height", 2800.0))
    if len(pts) < 2:
        return {"error": "Need at least 2 points for a wall"}
        
    # 점들을 Vector로 변환
    vecs = [App.Vector(p[0], p[1], p[2] if len(p)>2 else 0.0) for p in pts]
    
    # 베이스라인 생성
    wire = Part.makePolygon(vecs)
    feat = doc.addObject("Part::Feature", "WallBase")
    feat.Shape = wire
    
    # Arch Wall 생성
    wall = Arch.makeWall(feat, width=w, height=h, align="Center")
    doc.recompute()
    Gui.updateGui()
    return {"status": "ok", "label": wall.Label}

def cmd_build_geometry(payload):
    """freecad_builder.py를 라이브 문서에 직접 적용"""
    if "freecad_builder" not in sys.modules:
        return {"error": "freecad_builder module not available"}
        
    data = payload.get("geometry", {})
    if not data:
        return {"error": "No geometry data provided"}
        
    doc = ensure_active_document()
    params = data.get("params", {})
    el = data.get("elements", {})
    
    try:
        # 1. 벽체
        walls, wall_idx_map, wall_src = freecad_builder.build_walls(doc, el.get("wall", []), params)
        # 2. 기둥, 슬래브
        cols, col_src = freecad_builder.build_columns(doc, el.get("column", []), params)
        slabs, slab_src = freecad_builder.build_slabs(doc, el.get("slab", []), params)
        # 3. 개구부(문/창문)
        n_voids, n_leaf = freecad_builder.build_openings(doc, el.get("opening", []), wall_idx_map, params)
        
        doc.recompute()
        Gui.updateGui()
        
        return {
            "status": "ok", 
            "summary": f"Built {len(walls)} walls, {len(cols)} columns, {len(slabs)} slabs, {n_voids} voids."
        }
    except Exception as e:
        traceback.print_exc()
        return {"error": str(e)}

def cmd_get_screenshot(payload):
    doc = App.ActiveDocument
    if not doc:
        return {"error": "No active document to screenshot"}
    
    view = Gui.ActiveDocument.ActiveView
    if not view:
        return {"error": "No active view to screenshot"}
        
    # 화면 갱신 보장
    Gui.updateGui()
    
    # 임시 파일로 저장 후 base64 인코딩
    tmp_path = os.path.join(HERE, "_live_screenshot_tmp.png")
    
    width = payload.get("width", 1024)
    height = payload.get("height", 768)
    
    # 뷰포트를 전체 객체에 맞춤 (FitAll)
    if payload.get("fit_all", False):
        view.fitAll()
        
    view.saveImage(tmp_path, width, height, "Transparent")
    
    if not os.path.exists(tmp_path):
        return {"error": "Screenshot failed"}
        
    with open(tmp_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode('utf-8')
        
    os.remove(tmp_path)
    return {"status": "ok", "image_base64": img_b64}


# ----------------- HTTP Server ----------------- #

class LiveRPCHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # FreeCAD 콘솔에 너무 많은 로그가 찍히는 것을 방지
        pass
        
    def do_GET(self):
        if self.path == '/ping':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok"}).encode('utf-8'))
            
        elif self.path == '/screenshot':
            # GET 방식 스크린샷 
            try:
                res = run_in_main_thread(cmd_get_screenshot, {"fit_all": False})
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(res).encode('utf-8'))
            except Exception as e:
                self.send_error(500, str(e))
        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length) if content_length > 0 else b""
        
        try:
            payload = json.loads(post_data.decode('utf-8')) if post_data else {}
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON payload")
            return

        res = None
        try:
            if self.path == '/import_dxf':
                res = run_in_main_thread(cmd_import_dxf, payload)
            elif self.path == '/make_wall':
                res = run_in_main_thread(cmd_make_wall, payload)
            elif self.path == '/build_geometry':
                res = run_in_main_thread(cmd_build_geometry, payload)
            elif self.path == '/screenshot':
                res = run_in_main_thread(cmd_get_screenshot, payload)
            else:
                self.send_error(404, "Not Found")
                return
                
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(res).encode('utf-8'))
        except Exception as e:
            traceback.print_exc()
            self.send_error(500, str(e))

server_instance = None

def start_server(port=8081):
    global server_instance
    if server_instance is not None:
        print(f"[Live Add-on] Server is already running.")
        return
        
    def serve():
        global server_instance
        try:
            server_instance = HTTPServer(('127.0.0.1', port), LiveRPCHandler)
            print(f"[Live Add-on] Listening on http://127.0.0.1:{port} ...")
            server_instance.serve_forever()
        except OSError as e:
            print(f"[Live Add-on] Could not start server: {e}")
            server_instance = None
            
    t = threading.Thread(target=serve, daemon=True)
    t.start()

def stop_server():
    global server_instance
    if server_instance:
        server_instance.shutdown()
        server_instance.server_close()
        server_instance = None
        print("[Live Add-on] Server stopped.")

if __name__ == "__main__":
    start_server()
