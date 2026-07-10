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
    from PySide6.QtCore import QObject, Slot, Qt, QMetaObject
except ImportError:
    try:
        from PySide2 import QtCore
        from PySide2.QtCore import QObject, Slot, Qt, QMetaObject
    except ImportError:
        from PySide import QtCore
        from PySide.QtCore import QObject, Slot, Qt, QMetaObject

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




# ── 메인스레드 위임: 파일 기반 IPC (ai_listener.py 패턴, 작동 보장) ─────────
# ev.wait() + Qt 크로스스레드 방식 모두 FreeCAD Python 3.11 에서 자동 발화 실패.
# 해결: HTTP 핸들러(백그라운드)가 작업을 파일로 쓰고 결과 파일을 폴링(0.1s sleep).
# 메인 스레드의 50ms QTimer 가 작업 파일을 읽어 실행, 결과 파일 작성.
# HTTP 핸들러 폴링은 백그라운드 스레드이므로 메인 스레드/이벤트루프 미차단.
import uuid as _uuid

_TASK_DIR = HERE   # 작업/결과 파일 저장 위치


def _banner(msg, err=False):
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


def _drain_file_tasks():
    """50ms QTimer 콜백 — 작업 파일을 읽어 실행하고 결과 파일 기록."""
    import glob
    for cmd_path in glob.glob(os.path.join(_TASK_DIR, "_live_cmd_*.json")):
        try:
            with open(cmd_path, encoding="utf-8") as f:
                task = json.load(f)
            os.remove(cmd_path)
        except Exception:
            continue
        res_path = os.path.join(_TASK_DIR, f"_live_res_{task['tid']}.json")
        fn_name = task.get("fn")
        payload = task.get("payload", {})
        fn = globals().get(fn_name)
        if fn is None:
            out = {"error": f"Unknown function: {fn_name}"}
        else:
            try:
                out = fn(payload)
            except Exception as e:
                traceback.print_exc()
                out = {"error": str(e)}
        try:
            with open(res_path, "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False)
        except Exception as e:
            _banner(f"[Live Add-on] Result write error: {e}", err=True)


def run_in_main_thread(fn_name, payload, timeout=60):
    """HTTP 핸들러 → 파일로 작업 쓰기 → 결과 파일 폴링."""
    tid = _uuid.uuid4().hex[:8]
    cmd_path = os.path.join(_TASK_DIR, f"_live_cmd_{tid}.json")
    res_path = os.path.join(_TASK_DIR, f"_live_res_{tid}.json")
    with open(cmd_path, "w", encoding="utf-8") as f:
        json.dump({"tid": tid, "fn": fn_name, "payload": payload}, f)
    import time as _time
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        if os.path.exists(res_path):
            with open(res_path, encoding="utf-8") as f:
                result = json.load(f)
            os.remove(res_path)
            return result
        _time.sleep(0.05)
    try:
        os.remove(cmd_path)
    except Exception:
        pass
    raise TimeoutError(f"Task {tid} timed out after {timeout}s")


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

def cmd_exec_python(payload):
    code = payload.get("code", "")
    if not code:
        return {"error": "No code provided"}
    try:
        # Execute the code in the global namespace of this module
        exec(code, globals(), globals())
        return {"status": "ok", "message": "Code executed successfully"}
    except Exception as e:
        import traceback
        err = traceback.format_exc()
        return {"error": err}


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

def _wall_baseline(wall_obj):
    """Arch Wall 객체 → (centerline [[x,y],[x,y]], width, height). 못 읽으면 (None,..)."""
    cl = None
    base = getattr(wall_obj, "Base", None)
    if base is not None and getattr(base, "Shape", None):
        vs = base.Shape.Vertexes
        if len(vs) >= 2:
            cl = [[round(vs[0].Point.x, 1), round(vs[0].Point.y, 1)],
                  [round(vs[-1].Point.x, 1), round(vs[-1].Point.y, 1)]]
    def _q(v, dflt):
        try:
            return float(v)
        except Exception:
            return dflt
    return cl, _q(getattr(wall_obj, "Width", 200.0), 200.0), _q(getattr(wall_obj, "Height", 2800.0), 2800.0)


_BUILT_PREFIXES = ("Wall_", "Col_", "Center_", "BaseSolid_", "WallAxis_", "ColBase", "WinCutter_", "WallCut_")


def _is_wall_obj(o):
    tid = getattr(o, "TypeId", "") or ""
    ifc = getattr(o, "IfcType", "") or ""
    lbl = getattr(o, "Label", "") or ""
    return ("Wall" in tid) or (ifc == "Wall") or lbl.startswith("Wall") or "WallCut" in lbl


def cmd_get_selection(payload):
    """현재 FreeCAD 선택을 구조화 반환 — '이 벽'/'여기 선들'을 AI가 파악하는 핵심.
    각 객체: {label,name,typeId,ifcType,role,picked_point, (wall이면)centerline/width/height,
              (dxf_lines면)segment_count}. role: wall|dxf_lines|other."""
    selex = Gui.Selection.getSelectionEx()
    if not selex:
        return {"status": "ok", "count": 0, "objects": [],
                "hint": "선택이 없습니다. FreeCAD에서 대상(DXF 선 또는 벽)을 클릭 선택하세요."}
    try:
        from mep_macro.freecad_utils import extract_segments_from_objects
    except Exception:
        extract_segments_from_objects = None
    out = []
    for s in selex:
        o = s.Object
        picked = None
        try:
            if s.PickedPoints:
                p = s.PickedPoints[0]
                picked = [round(p.x, 1), round(p.y, 1), round(p.z, 1)]
        except Exception:
            pass
        entry = {"label": o.Label, "name": o.Name, "typeId": getattr(o, "TypeId", ""),
                 "ifcType": getattr(o, "IfcType", "") or "", "picked_point": picked}
        if _is_wall_obj(o):
            cl, w, h = _wall_baseline(o)
            entry.update({"role": "wall", "centerline": cl, "width": w, "height": h})
        else:
            segs = extract_segments_from_objects([o]) if extract_segments_from_objects else []
            if segs:
                entry["role"] = "dxf_lines"
                entry["segment_count"] = len(segs)
            else:
                entry["role"] = "other"
        out.append(entry)
    return {"status": "ok", "count": len(out), "objects": out}


def cmd_make_wall_from_selection(payload):
    """선택한 DXF 선 → 벽 페어링 → Arch 벽(+기둥). build_walls_from_segments 공유 헬퍼 사용."""
    doc = ensure_active_document()
    sel = Gui.Selection.getSelection()
    if not sel:
        return {"error": "선택된 객체가 없습니다. FreeCAD에서 DXF 선을 클릭 선택하세요."}
    from mep_macro.freecad_utils import extract_segments_from_objects, build_walls_from_segments
    src = [o for o in sel if not any((o.Name or "").startswith(p) for p in _BUILT_PREFIXES)]
    segs = extract_segments_from_objects(src)
    if not segs:
        return {"error": "선택에서 선분을 찾지 못했습니다(이미 빌드된 객체만 선택됨?)."}
    props = {"label": payload.get("label", "Sel"),
             "height": float(payload.get("height", 3000)),
             "default_thickness": float(payload.get("width", 200))}
    res = build_walls_from_segments(doc, segs, props,
                                    build_columns=bool(payload.get("columns", True)))
    for o in src:
        if hasattr(o, "ViewObject") and o.ViewObject:
            o.ViewObject.Visibility = False
    doc.recompute()
    Gui.updateGui()
    return {"status": "ok", "walls": res["walls"], "columns": res["columns"],
            "skipped": res["skipped"],
            "message": f"벽 {res['walls']}개 + 기둥 {res['columns']}개 생성(선분 {len(segs)}개에서)."}


def cmd_add_window_to_wall(payload):
    """선택한(또는 wall_label) 벽에 창문/문 단건 삽입. 클릭 지점(picked_point) 투영 위치 또는
    position(0~1 비율 또는 mm) 또는 중앙. freecad_builder.cut_window_into_wall(Part::Cut, 영속) 사용."""
    import math
    import freecad_builder
    doc = ensure_active_document()

    wall_obj = None
    picked = None
    wl = payload.get("wall_label")
    if wl:
        for o in doc.Objects:
            if o.Label == wl:
                wall_obj = o
                break
    if wall_obj is None:
        for s in Gui.Selection.getSelectionEx():
            if _is_wall_obj(s.Object):
                wall_obj = s.Object
                try:
                    if s.PickedPoints:
                        picked = s.PickedPoints[0]
                except Exception:
                    pass
                break
    if wall_obj is None:
        return {"error": "대상 벽을 찾지 못했습니다. 벽을 클릭 선택하거나 wall_label 을 지정하세요."}

    cl, ww, wh = _wall_baseline(wall_obj)
    if cl is None:
        return {"error": "벽 중심선을 읽지 못했습니다(파라메트릭 Arch Wall 이 아님?)."}
    a, b = cl[0], cl[1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy) or 1.0
    ux, uy = dx / L, dy / L

    # 창 중심 위치 t (벽 시작점 기준 거리)
    if picked is not None:
        t = (picked.x - a[0]) * ux + (picked.y - a[1]) * uy
    elif payload.get("position") is not None:
        pos = float(payload["position"])
        t = pos * L if 0.0 <= pos <= 1.0 else pos
    else:
        t = L / 2.0
    t = max(0.0, min(L, t))
    cx, cy = a[0] + t * ux, a[1] + t * uy

    subtype = payload.get("subtype", "window")
    op = {"center": [cx, cy],
          "width": float(payload.get("width", 1200)),
          "height": float(payload.get("height", 1500)),
          "sill": float(payload.get("sill", 0.0 if subtype == "door" else 900.0)),
          "subtype": subtype, "host_dir": [ux, uy], "host_width": ww}
    params = {"wall": {"width": ww, "height": wh}}

    doc.recompute()  # Arch Wall Shape 실현 보장
    import uuid as _uuid
    tag = _uuid.uuid4().hex[:6]
    out = freecad_builder.cut_window_into_wall(doc, wall_obj, op, params, tag=tag)
    doc.recompute()
    Gui.updateGui()
    if not out.get("void"):
        return {"error": "창문 void 생성 실패(위치/크기를 확인하세요)."}
    return {"status": "ok", "wall": wall_obj.Label, "subtype": subtype,
            "center": [round(cx, 1), round(cy, 1)],
            "size": [op["width"], op["height"]], "sill": op["sill"],
            "leaf": out.get("leaf"), "cut_obj": out.get("cut_obj"),
            "message": f"{wall_obj.Label} 에 {subtype} {op['width']:.0f}x{op['height']:.0f} 삽입 @({cx:.0f},{cy:.0f})."}


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
            try:
                res = run_in_main_thread("cmd_get_screenshot", {"fit_all": False})
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(res).encode('utf-8'))
            except Exception as e:
                self.send_error(500, str(e))

        elif self.path == '/get_selection':
            try:
                res = run_in_main_thread("cmd_get_selection", {})
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(res, ensure_ascii=False).encode('utf-8'))
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
                res = run_in_main_thread("cmd_import_dxf", payload)
            elif self.path == '/make_wall':
                res = run_in_main_thread("cmd_make_wall", payload)
            elif self.path == '/build_geometry':
                res = run_in_main_thread("cmd_build_geometry", payload)
            elif self.path == '/screenshot':
                res = run_in_main_thread("cmd_get_screenshot", payload)
            elif self.path == '/exec_python':
                res = run_in_main_thread("cmd_exec_python", payload, timeout=300)
            elif self.path == '/get_selection':
                res = run_in_main_thread("cmd_get_selection", payload)
            elif self.path == '/make_wall_from_selection':
                res = run_in_main_thread("cmd_make_wall_from_selection", payload, timeout=180)
            elif self.path == '/add_window_to_wall':
                res = run_in_main_thread("cmd_add_window_to_wall", payload, timeout=120)
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


def start_server(port=8082):
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

    # 파일 기반 IPC 드레인 타이머 (ai_listener.py 동일 패턴, 작동 보장)
    global _dispatcher, _main_timer
    _dispatcher = None
    _main_timer = QtCore.QTimer()
    _main_timer.timeout.connect(_drain_file_tasks)
    _main_timer.start(50)
    App._live_drain_timer = _main_timer   # App 저장 → GC 없음
    _banner("[Live Add-on] File-IPC drain timer started (50ms).")

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
    global _dispatcher, _main_timer
    try:
        if _main_timer:
            _main_timer.stop()
            _main_timer = None
        for attr in ("_live_drain_timer", "_live_dispatcher"):
            try:
                delattr(App, attr)
            except Exception:
                pass
    except Exception:
        pass
    _dispatcher = None

# 매크로/콘솔에서 직접 실행 시 서버 기동(메인 스레드에서 호출되어야 함).
start_server()
