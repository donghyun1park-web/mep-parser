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
import queue
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


# ── 스레드 안전 메인스레드 위임 (Qt cross-thread singleShot) ──────────────────
# Qt 표준 패턴: QTimer.singleShot(0, owner, func) 는 owner 가 사는 스레드에서
# func 를 실행한다. owner 가 메인 스레드 소유 QObject 이면 항상 메인 스레드 실행.
# 30ms QTimer + queue 폴링은 sleep 중 메인 스레드가 막히면 발화 실패 — 제거.
_main_thread_owner = None   # start_server 에서 QObject 생성 (메인 스레드 소유)


def _banner(msg, err=False):
    """Report view(App.Console) + Python console(print) 양쪽에 출력 → 확실히 보이게."""
    try:
        if err:
            App.Console.PrintError(msg + "\n")
        else:
            App.Console.PrintMessage(msg + "\n")
    except Exception:
        pass
    try:
        print(msg)
    except Exception:
        pass


def run_in_main_thread(func, *args, **kwargs):
    """백그라운드 스레드 → 메인스레드 실행(singleShot) 후 결과 대기."""
    box = {"value": None, "error": None}
    ev = threading.Event()

    def _do():
        try:
            box["value"] = func(*args, **kwargs)
        except Exception as e:
            box["error"] = e
            traceback.print_exc()
        finally:
            ev.set()

    # owner 를 context 로 지정 → owner 가 사는 스레드(메인)에서 _do 실행
    QtCore.QTimer.singleShot(0, _main_thread_owner, _do)

    if not ev.wait(timeout=600.0):
        raise TimeoutError("Main-thread execution timed out.")
    if box["error"] is not None:
        raise box["error"]
    return box["value"]

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
_main_timer = None


def start_server(port=8081):
    """★ 반드시 FreeCAD GUI 메인 스레드(매크로/콘솔)에서 호출할 것.
    매크로 재실행에 안전: 기존 서버/타이머를 App 에 저장해 먼저 정리한다."""
    global server_instance, _main_timer

    # ── 재실행 안전: 같은 FreeCAD 프로세스에 남은 이전 서버/타이머 정리 ──
    prev = getattr(App, "_live_addon_state", None)
    if prev:
        try:
            if prev.get("server"):
                prev["server"].shutdown(); prev["server"].server_close()
                print("[Live Add-on] Previous server stopped (re-run).")
        except Exception:
            pass
        try:
            if prev.get("timer"):
                prev["timer"].stop()
        except Exception:
            pass

    # 메인 스레드 소유 QObject 생성 — singleShot 크로스스레드 dispatch 의 anchor
    global _main_thread_owner, _main_timer
    _main_thread_owner = QtCore.QObject()
    _main_timer = None   # 폴링 타이머 불필요(singleShot 방식으로 변경)
    _banner("[Live Add-on] Main-thread owner created (cross-thread dispatch ready).")

    # 포트 재사용 허용(이전 소켓 잔류 대비)
    HTTPServer.allow_reuse_address = True

    def serve():
        global server_instance
        try:
            server_instance = HTTPServer(('127.0.0.1', port), LiveRPCHandler)
            App._live_addon_state = {"server": server_instance, "timer": _main_timer}
            _banner(f"[Live Add-on] Listening on http://127.0.0.1:{port} ...")
            server_instance.serve_forever()
        except OSError as e:
            _banner(f"[Live Add-on] PORT {port} BUSY ({e}). FreeCAD 를 완전히 "
                    f"종료 후 재시작하세요(이전 서버가 포트 점유 중).", err=True)
            server_instance = None

    t = threading.Thread(target=serve, daemon=True)
    t.start()

def stop_server():
    global server_instance, _main_timer
    if server_instance:
        server_instance.shutdown()
        server_instance.server_close()
        server_instance = None
        print("[Live Add-on] Server stopped.")
    global _main_thread_owner
    _main_thread_owner = None   # QObject 해제

# 매크로/콘솔에서 직접 실행 시 서버 기동(메인 스레드에서 호출되어야 함).
start_server()
