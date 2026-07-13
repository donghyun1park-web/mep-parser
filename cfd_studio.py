"""
cfd_studio.py — MEP CFD Studio: 대시보드 통합 단일 프로그램

CLI 4개(cfd_export/run/report/gridstudy) 체인을 브라우저 하나로 통합:
  더블클릭(run_cfd.bat) → 브라우저 → 대시보드(전 케이스 집계) → 새 해석 마법사 →
  실행 모니터 → 리포트/결과 뷰어.

설계 원칙(계획서):
- stdlib http.server 만(의존성 0), 127.0.0.1 바인딩, 자립 HTML(외부 CDN 없음).
- 파일이 진실: 프로젝트 루트(기본 cfd_projects/) 직속 폴더 중 cfd_case_meta.json 있는
  것이 케이스. 서버가 죽어도 재스캔으로 복구.
- 엔진 재사용: cfd_export.build_case/cfg_from_geometry · cfd_run.run_case ·
  cfd_report.case_summary/build (판정·지표는 CLI와 동일 코드 = 불일치 없음).

사용:
  python cfd_studio.py                 # cfd_projects/ 루트, 브라우저 자동
  python cfd_studio.py --root <경로> --port 8090 --no-browser
"""
import argparse
import glob
import json
import os
import re
import shutil
import socket
import sys
import threading
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, unquote

import cfd_export
import cfd_report
from cfd_run import run_case, check_openfoam

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "cfd_projects")   # main()에서 확정

_SAFE_NAME = re.compile(r"^[\w가-힣.\- ]+$")


# ── 케이스 스캔 ───────────────────────────────────────────────────────────────

def safe_case_dir(name):
    """케이스 폴더명 검증 + ROOT 밖 접근 차단. 유효하면 절대경로, 아니면 None."""
    if not name or not _SAFE_NAME.match(name) or ".." in name:
        return None
    full = os.path.realpath(os.path.join(ROOT, name))
    if not full.startswith(os.path.realpath(ROOT) + os.sep):
        return None
    return full if os.path.isdir(full) else None


def scan_cases():
    """루트 직속 케이스 폴더 → case_summary 목록(최신순)."""
    cases = []
    if os.path.isdir(ROOT):
        for d in sorted(os.listdir(ROOT)):
            full = os.path.join(ROOT, d)
            if not os.path.isdir(full):
                continue
            if not os.path.exists(os.path.join(full, "cfd_case_meta.json")):
                continue
            try:
                s = cfd_report.case_summary(full)
            except Exception as e:
                s = {"dir": d, "name": d, "badge": f"요약 실패: {e}", "badge_color": "#c0392b",
                     "status": "error", "mtime": 0}
            if s:
                s["gci_pct"] = (s.get("gci") or {}).get("gci_pct")
                cases.append(s)
    cases.sort(key=lambda c: c.get("mtime") or 0, reverse=True)
    return {"root": ROOT, "cases": cases}


# ── 실행 큐 (동시 1개 — WSL 경합·결정론 보장) ────────────────────────────────

RUN = {"active": None, "queue": [], "history": {}, "worker": False}
RUN_LOCK = threading.Lock()
OPENFOAM_OK = None   # main()에서 1회 체크(1초 status 폴링마다 wsl 프로세스를 띄우지 않음)


def _enqueue(name, kind):
    """실행/격자검증 작업 예약. 문제 있으면 오류 문자열, 정상이면 None."""
    if not OPENFOAM_OK:
        return ("WSL OpenFOAM 이 없습니다 — WSL 에서 `sudo apt-get install openfoam` "
                "설치 후 스튜디오를 재시작하세요.")
    if not safe_case_dir(name):
        return "케이스 없음"
    with RUN_LOCK:
        if RUN["active"] and RUN["active"]["name"] == name:
            return "이미 실행 중"
        if any(q["name"] == name for q in RUN["queue"]):
            return "이미 대기열에 있음"
        RUN["queue"].append({"name": name, "kind": kind})
        RUN["history"].pop(name, None)
        if not RUN["worker"]:
            RUN["worker"] = True
            threading.Thread(target=_run_worker, daemon=True).start()
    return None


def enqueue_run(name):
    return _enqueue(name, "run")


def enqueue_grid(name):
    return _enqueue(name, "grid")


def _run_worker():
    """대기열을 하나씩 처리(동시 1개). run=실행+리포트, grid=격자검증 3케이스."""
    while True:
        with RUN_LOCK:
            if not RUN["queue"]:
                RUN["worker"] = False
                return
            job = RUN["queue"].pop(0)
            name, kind = job["name"], job["kind"]
            case_dir = safe_case_dir(name)
            end_t = None
            try:
                with open(os.path.join(case_dir, "cfd_case_meta.json"), encoding="utf-8") as f:
                    end_t = json.load(f).get("config", {}).get("endTime")
            except Exception:
                pass
            RUN["active"] = {"name": name, "step": "준비", "time": 0.0,
                             "endTime": end_t, "lines": []}
        act = RUN["active"]
        try:
            if kind == "grid":
                err = _do_gridstudy(name, case_dir, act)
                ok = err is None
            else:
                ok, err = _do_run(name, case_dir, act)
        except Exception as e:
            ok, err = False, f"{type(e).__name__}: {e}"
        with RUN_LOCK:
            RUN["history"][name] = {"ok": ok, "error": err}
            RUN["active"] = None
        FIELD_CACHE.pop(name, None)   # 결과 갱신 → 뷰어 캐시 무효화


def _do_run(name, case_dir, act):
    def cb(line):
        if line.startswith("Time = "):
            act["step"] = "solver"
            try:
                act["time"] = float(line.split("=", 1)[1])
            except ValueError:
                pass
        elif line.startswith("=== blockMesh"):
            act["step"] = "blockMesh"
        elif line.startswith("=== checkMesh"):
            act["step"] = "checkMesh"
        elif line.startswith("=== solver"):
            act["step"] = "solver"
        act["lines"].append(line)
        del act["lines"][:-15]

    r = run_case(case_dir, progress_cb=cb)
    err = r.get("error")
    if r["ok"]:
        act["step"] = "리포트 생성"
        try:
            cfd_report.generate_report(case_dir)
        except Exception as e:
            err = f"리포트 생성 실패: {e}"
    return (r["ok"] and not err), err


def _do_gridstudy(name, case_dir, act):
    """격자 독립성 검증: 케이스 셀 c 기준 [1.5c, c, c/1.5] 3케이스를 <case>/_grid/ 에
    실행(cfd_gridstudy.run_one 재사용) → GCI 를 케이스 meta['gci'] 에 병합.
    반환: 오류 문자열 또는 None."""
    import cfd_gridstudy
    meta_path = os.path.join(case_dir, "cfd_case_meta.json")
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    cfg = meta.get("config", {})
    c = float(cfg.get("mesh", {}).get("cell", 0.3))
    cells = [round(c * 1.5, 3), c, round(c / 1.5, 3)]   # 성긴 → 세밀
    gdir = os.path.join(case_dir, "_grid")
    os.makedirs(gdir, exist_ok=True)
    results = []
    for i, cell in enumerate(cells):
        act["step"] = f"격자검증 {i + 1}/3 · 셀 {cell} m"
        act["time"] = 0.0
        act["lines"].append(f"[격자 {i + 1}/3] 셀 {cell} m 실행...")
        results.append(cfd_gridstudy.run_one(cfg, cell, os.path.join(gdir, f"c{i}"),
                                             cfg.get("endTime")))
    key = "T_max_C"
    vals = [r["metrics"].get(key) for r in results]
    gci = {"key": key, "cells": cells,
           "values": [round(v, 3) if isinstance(v, float) else v for v in vals],
           "ncells": [r["cells"] for r in results]}
    err = None
    if all(v is not None for v in vals):
        res = cfd_gridstudy.solve_order(vals[2], vals[1], vals[0],
                                        cells[1] / cells[2], cells[0] / cells[1])
        if res and res[0] == "비단조":
            gci["verdict"] = "비단조 — 격자 미독립(더 세밀 필요)"
        elif res:
            p, fext, g21 = res
            gci.update(p=round(p, 2), extrapolated=round(fext, 3), gci_pct=round(g21, 2),
                       verdict=("신뢰(≤5%)" if g21 <= 5 else "격자오차 큼(>5%)"))
        else:
            gci["verdict"] = "격자간 변화 0 — 완전 독립"
            gci["gci_pct"] = 0.0
    else:
        gci["verdict"] = "지표 누락(하위 실행 실패)"
        err = "격자검증: 일부 격자 실행 실패(케이스 _grid/ 로그 확인)"
    meta["gci"] = gci
    with open(meta_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    act["lines"].append(f"격자검증 완료: {gci.get('verdict')}"
                        + (f" GCI={gci.get('gci_pct')}%" if gci.get("gci_pct") is not None else ""))
    return err


def run_status():
    with RUN_LOCK:
        act = None
        if RUN["active"]:
            a = RUN["active"]
            act = {"name": a["name"], "step": a["step"], "time": a["time"],
                   "endTime": a["endTime"], "lines": list(a["lines"])}
        queue = [q["name"] + (" (격자검증)" if q["kind"] == "grid" else "")
                 for q in RUN["queue"]]
        return {"openfoam": bool(OPENFOAM_OK), "active": act,
                "queue": queue, "history": dict(RUN["history"])}


# ── 결과 필드 캐시 + 단면 슬라이스 (2D/3D 뷰어 공용 API) ─────────────────────
# 함수객체가 깨진 환경이므로(SHA1 버그) 최종 time 의 ascii 필드를 직접 파싱해
# (nz,ny,nx) 구조격자로 캐시하고, 요청된 절단면만 JSON 으로 반환한다(수 KB).

FIELD_CACHE = {}
FIELD_LOCK = threading.Lock()


def _load_fields(name):
    """케이스 결과 필드 → 구조격자 배열 캐시(mtime 무효화)."""
    d = safe_case_dir(name)
    if not d:
        return None
    tdir = cfd_report.find_latest_time(d)
    if not tdir or not os.path.exists(os.path.join(tdir, "T")):
        return None
    meta = cfd_report._load_meta(d)
    if not meta:
        return None
    mt = os.path.getmtime(os.path.join(tdir, "T"))
    with FIELD_LOCK:
        c = FIELD_CACHE.get(name)
        if c and c["mtime"] == mt:
            return c
    import numpy as np
    n = meta["mesh"]["cells"]
    T = cfd_report._as_array(cfd_report.read_field(os.path.join(tdir, "T")), n)
    if T is None:
        return None
    Tg, xc, yc, zc = cfd_report._cell_grid(T - 273.15, meta)
    entry = {"meta": meta, "mtime": mt, "T": Tg,
             "xc": xc, "yc": yc, "zc": zc,
             "Tmin": float(Tg.min()), "Tmax": float(Tg.max())}
    U = cfd_report._as_array(cfd_report.read_field(os.path.join(tdir, "U")), n)
    if U is not None and getattr(U, "ndim", 1) == 2:
        nx, ny, nz = meta["mesh"]["nx"], meta["mesh"]["ny"], meta["mesh"]["nz"]
        Ug = U[:nx * ny * nz].reshape(nz, ny, nx, 3)
        entry["Ux"], entry["Uy"], entry["Uz"] = Ug[..., 0], Ug[..., 1], Ug[..., 2]
        Umag = np.linalg.norm(Ug, axis=3)
        entry["Umag"] = Umag
        entry["Umax"] = float(Umag.max())
    with FIELD_LOCK:
        FIELD_CACHE[name] = entry
    return entry


def field_info(name):
    e = _load_fields(name)
    if not e:
        return {"error": "결과 필드 없음 — 아직 실행 전이거나 회수 실패"}
    m = e["meta"]["mesh"]
    room = e["meta"]["config"].get("room", {})
    roles = e["meta"].get("roles", {})
    return {"nx": m["nx"], "ny": m["ny"], "nz": m["nz"], "room": room,
            "Tmin": round(e["Tmin"], 2), "Tmax": round(e["Tmax"], 2),
            "Umax": round(e.get("Umax", 0.0), 3), "hasU": "Umag" in e,
            "inlet": next((k for k, v in roles.items() if v == "inlet"), None),
            "outlet": next((k for k, v in roles.items() if v == "outlet"), None)}


def field_slice(name, field, axis, idx, want_vec):
    """절단면 1장: {hx,hy,w,h,pos,data[[..]],(vx,vy)}. data 는 화면행=hy축."""
    e = _load_fields(name)
    if not e:
        return {"error": "결과 필드 없음"}
    key = "Umag" if field == "U" else "T"
    if key not in e:
        return {"error": f"{field} 필드 없음"}
    g = e[key]
    nz, ny, nx = g.shape
    room = e["meta"]["config"].get("room", {})
    L, W, H = room.get("L", 1), room.get("W", 1), room.get("H", 1)
    axis = axis if axis in ("x", "y", "z") else "z"
    lim = {"z": nz, "y": ny, "x": nx}[axis]
    idx = max(0, min(int(idx), lim - 1))
    has_u = "Ux" in e
    if axis == "z":
        data = g[idx]                       # (ny, nx) — 행=y, 열=x
        pos, hx, hy, w, h = e["zc"][idx], "x", "y", L, W
        vec = (e["Ux"][idx], e["Uy"][idx]) if has_u else None
    elif axis == "y":
        data = g[:, idx, :]                 # (nz, nx) — 행=z, 열=x
        pos, hx, hy, w, h = e["yc"][idx], "x", "z", L, H
        vec = (e["Ux"][:, idx, :], e["Uz"][:, idx, :]) if has_u else None
    else:
        data = g[:, :, idx]                 # (nz, ny) — 행=z, 열=y
        pos, hx, hy, w, h = e["xc"][idx], "y", "z", W, H
        vec = (e["Uy"][:, :, idx], e["Uz"][:, :, idx]) if has_u else None
    out = {"axis": axis, "idx": idx, "pos": round(float(pos), 3),
           "hx": hx, "hy": hy, "w": w, "h": h,
           "data": [[round(float(v), 3) for v in row] for row in data]}
    if want_vec and vec is not None:
        out["vx"] = [[round(float(v), 4) for v in row] for row in vec[0]]
        out["vy"] = [[round(float(v), 4) for v in row] for row in vec[1]]
    return out


# ── 마법사 백엔드: 도면 미리보기 · 케이스 생성 · 삭제 ────────────────────────

def inspect_geometry(path, zone=None, bbox=None):
    """geometry.json 미리보기: zone 목록·전체범위·(zone/bbox 선택 시) 개구부 벽 힌트."""
    path = os.path.abspath(os.path.expanduser(path or ""))
    if not os.path.isfile(path):
        return {"error": f"파일 없음: {path}"}
    try:
        with open(path, encoding="utf-8") as f:
            geom = json.load(f)
    except Exception as e:
        return {"error": f"geometry.json 파싱 실패: {e}"}
    el = geom.get("elements", {})
    zones = []
    for i, z in enumerate(el.get("zone", [])):
        ext = cfd_export._xy_extent([z])
        if ext:
            zones.append({"i": i, "L": round((ext[2] - ext[0]) / 1000, 2),
                          "W": round((ext[3] - ext[1]) / 1000, 2)})
    out = {"zones": zones,
           "openings": len(el.get("opening", [])),
           "equipment": len(el.get("equipment", [])),
           "walls": len(el.get("wall", [])),
           "height_m": round(geom.get("params", {}).get("wall", {}).get("height", 2800.0) / 1000.0, 2)}
    ext_all = cfd_export._xy_extent(el.get("wall", []))
    if ext_all:
        out["wall_extent_mm"] = [round(v, 1) for v in ext_all]
    if zone is not None or bbox is not None:
        try:
            cfg, info = cfd_export.cfg_from_geometry(geom, zone=zone, bbox=bbox,
                                                     height=out["height_m"])
            out["room"] = cfg["room"]
            out["openings_by_wall"] = info["openings_by_wall"]
            out["warnings"] = info["warnings"]
        except SystemExit as e:
            out["error"] = str(e)
    return out


_INFLOW = {"x0": (1, 0, 0), "xL": (-1, 0, 0), "y0": (0, 1, 0), "yW": (0, -1, 0)}


def create_case(p):
    """마법사 폼(JSON) → 케이스 생성. 반환 {ok, dir} 또는 {error}."""
    name = (p.get("name") or "").strip()
    if not name or not _SAFE_NAME.match(name):
        return {"error": "케이스명은 한글/영문/숫자/공백/._- 만 가능"}
    out_dir = os.path.join(ROOT, name)
    if os.path.exists(out_dir):
        return {"error": f"이미 존재하는 케이스: {name} (다른 이름 또는 기존 삭제 후)"}
    try:
        supply = p.get("supply", "x0")
        exhaust = p.get("exhaust", "xL")
        if supply == exhaust:
            return {"error": "급기 벽과 배기 벽이 같습니다"}
        supply_u = float(p.get("supply_u", 0.3))
        supply_T = float(p.get("supply_T_C", 20.0)) + 273.15
        power_kw = p.get("power_kw")
        power_kw = float(power_kw) if power_kw not in (None, "") else None
        cell = float(p.get("cell", 0.3))
        endtime = int(p.get("endtime", 400))
        info = None
        if p.get("mode") == "geometry":
            with open(os.path.expanduser(p.get("geometry") or ""), encoding="utf-8") as f:
                geom = json.load(f)
            zone = p.get("zone")
            zone = int(zone) if zone not in (None, "") else None
            bbox = p.get("bbox") or None
            if isinstance(bbox, str) and bbox.strip():
                bbox = [float(x) for x in bbox.split(",")]
            elif not bbox:
                bbox = None
            height = p.get("height")
            height = float(height) if height not in (None, "") else None
            cfg, info = cfd_export.cfg_from_geometry(
                geom, zone=zone, bbox=bbox, height=height, cell=cell,
                supply=supply, exhaust=exhaust, supply_u=supply_u, supply_T=supply_T,
                power_kw=power_kw, endTime=endtime, name=name)
        else:
            L, W, H = float(p["L"]), float(p["W"]), float(p["H"])
            d = _INFLOW.get(supply, (1, 0, 0))
            cfg = {
                "name": name,
                "_note": "스튜디오 직접 입력 · 급배기·발열=가정값(리포트 명시)",
                "room": {"L": L, "W": W, "H": H},
                "mesh": {"cell": cell},
                "g": [0, 0, -9.81],
                "inlet": {"wall": supply,
                          "U": [supply_u * d[0], supply_u * d[1], supply_u * d[2]],
                          "T": supply_T, "_desc": f"급기(가정) — {supply} 벽"},
                "outlet": {"wall": exhaust, "_desc": f"배기(가정) — {exhaust} 벽"},
                "heat": ({"power_kw": power_kw,
                          "_desc": f"장비 총발열(가정) {power_kw} kW = 바닥층 체적발열원"}
                         if power_kw is not None else
                         {"wall": "floor", "floor_T": 313.0,
                          "_desc": "발열 바닥(가정) = 장비 총발열 단순화"}),
                "init": {"T": 300},
                "endTime": endtime,
            }
        cfd_export.build_case(cfg, out_dir)
        if info:
            meta_path = os.path.join(out_dir, "cfd_case_meta.json")
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            meta["from_geometry"] = {k: v for k, v in info.items() if k != "src_polygon"}
            with open(meta_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        return {"ok": True, "dir": name}
    except SystemExit as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def delete_case(name):
    d = safe_case_dir(name)
    if not d:
        return {"error": "케이스 없음"}
    with RUN_LOCK:
        if RUN["active"] and RUN["active"]["name"] == name:
            return {"error": "실행 중인 케이스 — 완료 후 삭제하세요"}
        if name in RUN["queue"]:
            RUN["queue"].remove(name)
    shutil.rmtree(d)
    return {"ok": True}


# ── HTTP 핸들러 ───────────────────────────────────────────────────────────────

_CTYPES = {".html": "text/html; charset=utf-8", ".png": "image/png",
           ".json": "application/json; charset=utf-8", ".js": "text/javascript; charset=utf-8",
           ".csv": "text/csv; charset=utf-8"}


class StudioHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):   # 콘솔 도배 방지
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False),
                   "application/json; charset=utf-8")

    def do_GET(self):
        try:
            self._route_get()
        except (ConnectionAbortedError, BrokenPipeError):
            pass
        except Exception as e:
            try:
                self._json({"error": str(e)}, 500)
            except Exception:
                pass

    def _route_get(self):
        u = urlparse(self.path)
        path = unquote(u.path)
        if path == "/":
            return self._send(200, PAGE_DASH)
        if path == "/new":
            return self._send(200, PAGE_NEW)
        if path == "/api/cases":
            return self._json(scan_cases())
        if path == "/api/status":
            return self._json(run_status())
        m = re.match(r"^/api/fieldinfo/([^/]+)$", path)
        if m:
            return self._json(field_info(m.group(1)))
        m = re.match(r"^/api/slice/([^/]+)$", path)
        if m:
            from urllib.parse import parse_qs
            q = parse_qs(u.query)
            return self._json(field_slice(
                m.group(1),
                q.get("field", ["T"])[0],
                q.get("axis", ["z"])[0],
                q.get("idx", ["0"])[0],
                q.get("vec", ["0"])[0] == "1"))
        m = re.match(r"^/case/([^/]+)/report$", path)
        if m:
            return self._serve_report(m.group(1))
        m = re.match(r"^/case/([^/]+)/file/([^/]+)$", path)
        if m:
            return self._serve_file(m.group(1), m.group(2))
        m = re.match(r"^/vendor/([\w.\-]+)$", path)
        if m:
            full = os.path.join(HERE, "vendor", m.group(1))
            if os.path.isfile(full):
                with open(full, "rb") as f:
                    return self._send(200, f.read(), "text/javascript; charset=utf-8")
            return self._send(404, "not found")
        self._send(404, "not found")

    def do_POST(self):
        try:
            ln = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(ln).decode("utf-8") if ln else "{}"
            p = json.loads(body or "{}")
            path = unquote(urlparse(self.path).path)
            if path == "/api/inspect":
                zone = p.get("zone")
                zone = int(zone) if zone not in (None, "") else None
                bbox = p.get("bbox") or None
                if isinstance(bbox, str) and bbox.strip():
                    bbox = [float(x) for x in bbox.split(",")]
                elif not bbox:
                    bbox = None
                return self._json(inspect_geometry(p.get("geometry", ""), zone=zone, bbox=bbox))
            if path == "/api/create":
                r = create_case(p)
                if r.get("ok") and p.get("run_now"):
                    r["run_error"] = enqueue_run(r["dir"])
                return self._json(r)
            m = re.match(r"^/api/run/([^/]+)$", path)
            if m:
                err = enqueue_run(m.group(1))
                return self._json({"error": err} if err else {"ok": True})
            m = re.match(r"^/api/grid/([^/]+)$", path)
            if m:
                err = enqueue_grid(m.group(1))
                return self._json({"error": err} if err else {"ok": True})
            m = re.match(r"^/api/delete/([^/]+)$", path)
            if m:
                return self._json(delete_case(m.group(1)))
            self._send(404, "not found")
        except (ConnectionAbortedError, BrokenPipeError):
            pass
        except Exception as e:
            try:
                self._json({"error": str(e)}, 500)
            except Exception:
                pass

    def _serve_report(self, case_name):
        d = safe_case_dir(case_name)
        if not d:
            return self._send(404, "케이스 없음")
        reps = glob.glob(os.path.join(d, "cfd_report_*.html"))
        if not reps:
            return self._send(404, "<meta charset='utf-8'>리포트가 아직 없습니다 — 먼저 실행하세요.")
        with open(max(reps, key=os.path.getmtime), encoding="utf-8") as f:
            self._send(200, f.read())

    def _serve_file(self, case_name, fname):
        d = safe_case_dir(case_name)
        if not d or not _SAFE_NAME.match(fname):
            return self._send(404, "not found")
        full = os.path.realpath(os.path.join(d, fname))
        if not full.startswith(d + os.sep) or not os.path.isfile(full):
            return self._send(404, "not found")
        ext = os.path.splitext(fname)[1].lower()
        with open(full, "rb") as f:
            self._send(200, f.read(), _CTYPES.get(ext, "text/plain; charset=utf-8"))


# ── 대시보드 페이지 (자립 HTML/JS, 리포트와 같은 시각 언어) ──────────────────

PAGE_DASH = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MEP CFD Studio</title>
<style>
 :root{--accent:#2c5f8a;--line:#e2e2e2;--muted:#666}
 *{box-sizing:border-box}
 body{font-family:'Malgun Gothic','Segoe UI',sans-serif;margin:0;background:#f0f2f5;color:#1a1a1a}
 .wrap{max-width:1280px;margin:18px auto;padding:0 16px}
 .hdr{display:flex;align-items:center;gap:14px;background:#fff;padding:14px 20px;border-radius:10px;box-shadow:0 1px 6px rgba(0,0,0,.07)}
 .hdr h1{font-size:19px;margin:0;color:var(--accent);white-space:nowrap}
 .hdr .root{color:var(--muted);font-size:12px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .btn{background:var(--accent);color:#fff;border:none;border-radius:8px;padding:9px 16px;font-size:14px;cursor:pointer;text-decoration:none;display:inline-block;white-space:nowrap}
 .btn.sec{background:#fff;color:var(--accent);border:1px solid var(--accent)}
 .cards{display:flex;gap:12px;margin:14px 0;flex-wrap:wrap}
 .card{flex:1;min-width:140px;background:#fff;border-radius:10px;padding:13px 18px;box-shadow:0 1px 6px rgba(0,0,0,.07)}
 .card .n{font-size:26px;font-weight:700}
 .card .l{color:var(--muted);font-size:12.5px}
 .tblwrap{background:#fff;border-radius:10px;box-shadow:0 1px 6px rgba(0,0,0,.07);overflow-x:auto}
 table{width:100%;border-collapse:collapse;font-size:13.5px;min-width:1080px}
 th,td{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
 th{background:#fafafa;cursor:pointer;user-select:none;font-weight:600}
 th:hover{color:var(--accent)}
 th .arr{font-size:10px;color:var(--accent)}
 td.num,th.num{text-align:right}
 .badge{display:inline-block;color:#fff;border-radius:12px;padding:2px 10px;font-size:12px;font-weight:600}
 tr.rowwarn td{background:#fdecea}
 .empty{background:#fff;border-radius:10px;padding:56px 20px;text-align:center;color:var(--muted);box-shadow:0 1px 6px rgba(0,0,0,.07)}
 .empty .steps{font-size:15px;margin:14px 0 22px}
 a.rep{color:var(--accent);font-weight:600;text-decoration:none}
 a.rep:hover{text-decoration:underline}
 a.del{color:#c0392b}
 .foot{color:var(--muted);font-size:11.5px;margin:14px 2px}
 .strip{background:#eaf2f8;border:1px solid #aed6f1;border-radius:10px;padding:10px 16px;margin:0 0 12px;font-size:13.5px}
 .strip.err{background:#fdecea;border-color:#f5b7b1;color:#922b21}
 .strip .bar{background:#d6eaf8;border-radius:6px;height:9px;margin:7px 0;overflow:hidden}
 .strip .fill{background:var(--accent);height:100%;transition:width .5s}
 .strip pre{background:#1e2a33;color:#d5e8f5;border-radius:6px;padding:8px 10px;font-size:11.5px;max-height:220px;overflow:auto;white-space:pre-wrap}
 .strip summary{cursor:pointer;color:var(--accent);font-size:12.5px}
 .ov{position:fixed;inset:0;background:rgba(20,30,40,.5);display:none;align-items:center;justify-content:center;z-index:60}
 .ovbox{background:#fff;border-radius:12px;padding:16px 20px;max-width:94vw;max-height:92vh;overflow:auto;box-shadow:0 6px 30px rgba(0,0,0,.25)}
 .ovhdr{display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap}
 .ovhdr b{color:var(--accent);font-size:15px}
 .ovhdr select,.ovhdr input[type=range]{font-size:13px}
 .ovhdr .x{margin-left:auto;background:none;border:none;font-size:18px;cursor:pointer;color:#666}
 #vwcv{border:1px solid var(--line);border-radius:6px;cursor:crosshair}
 #vwread{font-size:13px;color:#333;margin-top:8px;min-height:18px}
 .cmpbar{position:fixed;bottom:18px;left:50%;transform:translateX(-50%);background:var(--accent);color:#fff;
  border-radius:24px;padding:10px 22px;box-shadow:0 4px 16px rgba(0,0,0,.3);display:none;z-index:55;font-size:14px}
 .cmpbar button{background:#fff;color:var(--accent);border:none;border-radius:14px;padding:5px 14px;margin-left:12px;cursor:pointer;font-weight:600}
 #cmptbl{border-collapse:collapse;font-size:13.5px;min-width:480px}
 #cmptbl th,#cmptbl td{padding:8px 14px;border-bottom:1px solid var(--line);text-align:right}
 #cmptbl th:first-child,#cmptbl td:first-child{text-align:left;background:#fafafa;font-weight:600}
 .best{color:#1e8449;font-weight:700}
</style></head><body><div class="wrap">
 <div class="hdr">
  <h1>MEP CFD Studio</h1>
  <div class="root" id="root">…</div>
  <button class="btn sec" onclick="load()">새로고침</button>
  <a class="btn" href="/new">＋ 새 해석</a>
 </div>
 <div class="cards" id="cards"></div>
 <div id="strip"></div>
 <div id="main"></div>
 <div class="foot">도면→OpenFOAM CFD 파이프라인 · 지표: 평균/최고 온도, 급기 대비 ΔT,
  에너지 폐합율(주입열=배기열, 90~110% 정상), GCI(격자 오차, ≤5% 신뢰)</div>
</div>

<div id="vwov" class="ov" onclick="if(event.target===this)vwClose()">
 <div class="ovbox">
  <div class="ovhdr">
   <b id="vwtitle"></b>
   <select id="vwmode" onchange="vwMode()"><option value="2d">2D 단면</option><option value="3d">3D 컷플레인</option></select>
   <select id="vwfield" onchange="fieldCh()"><option value="T">온도</option><option value="U">유속</option></select>
   <select id="vwaxis" onchange="vwAxis()"><option value="z">수평면(Z)</option><option value="y">수직면(Y)</option><option value="x">수직면(X)</option></select>
   <input type="range" id="vwidx" style="width:170px" oninput="vwFetch()">
   <span id="vwpos" style="font-size:13px;color:#444;min-width:88px"></span>
   <label style="font-size:13px" id="vwveclb"><input type="checkbox" id="vwvec" onchange="vwFetch()"> 기류 화살표</label>
   <button class="x" onclick="vwClose()">✕</button>
  </div>
  <div id="vw2d" style="display:flex;gap:12px;align-items:flex-start">
   <canvas id="vwcv" width="640" height="430" onmousemove="vwHover(event)" onmouseleave="vwread.textContent=''"></canvas>
   <canvas id="vwcb" width="52" height="430"></canvas>
  </div>
  <div id="vw3d" style="display:none">
   <div style="display:flex;gap:12px;align-items:flex-start">
    <canvas id="cv3d" width="640" height="430" style="border:1px solid var(--line);border-radius:6px"></canvas>
    <canvas id="vwcb3" width="52" height="430"></canvas>
   </div>
   <div style="font-size:13px;margin-top:8px">
    절단면 X <input type="range" id="s3x" style="width:140px" oninput="upd3&&upd3('x',this.value)">
    Y <input type="range" id="s3y" style="width:140px" oninput="upd3&&upd3('y',this.value)">
    Z(높이) <input type="range" id="s3z" style="width:140px" oninput="upd3&&upd3('z',this.value)">
    <span style="color:#888">· 드래그=회전 · 휠=줌 · 파랑면=급기 · 빨강면=배기</span>
   </div>
  </div>
  <div id="vwread"></div>
 </div>
</div>

<div id="cmpov" class="ov" onclick="if(event.target===this)cmpov.style.display='none'">
 <div class="ovbox"><div class="ovhdr"><b>케이스 비교</b><button class="x" onclick="cmpov.style.display='none'">✕</button></div>
  <div id="cmpbody"></div></div>
</div>
<div id="cmpbar" class="cmpbar"><span id="cmpn"></span><button onclick="openCompare()">선택 비교 →</button></div>

<script>
let CASES=[], KEY='mtime', ASC=false;
const SEL=new Set();
const COLS=[
 {k:'_sel',t:''},
 {k:'name',t:'케이스명'},
 {k:'room',t:'방 L×W×H (m)'},
 {k:'cells',t:'셀',num:1},
 {k:'heat_label',t:'발열'},
 {k:'supply_u',t:'급기 m/s',num:1,dec:2},
 {k:'T_avg_C',t:'평균T ℃',num:1,dec:1},
 {k:'T_max_C',t:'최고T ℃',num:1,dec:1},
 {k:'dT_rise',t:'ΔT K',num:1,dec:1},
 {k:'closure_pct',t:'폐합 %',num:1,dec:0},
 {k:'gci_pct',t:'GCI %',num:1,dec:1},
 {k:'badge',t:'상태'},
 {k:'mtime',t:'날짜',num:1},
 {k:'_act',t:'동작'}
];
function fmt(c,v){
 if(v===null||v===undefined||v==='')return '—';
 if(c.k==='mtime'){const d=new Date(v*1000);return (d.getMonth()+1)+'/'+d.getDate()+' '+String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');}
 if(c.k==='cells')return v.toLocaleString();
 if(c.dec!==undefined&&typeof v==='number')return v.toFixed(c.dec);
 return v;
}
function cards(){
 const n=CASES.length;
 const ok=CASES.filter(c=>(c.badge||'').startsWith('수렴')).length;
 const warn=CASES.filter(c=>/^미수렴|^발산/.test(c.badge||'')).length;
 const idle=CASES.filter(c=>c.status==='created').length;
 document.getElementById('cards').innerHTML=
  card(n,'케이스','#2c5f8a')+card(ok,'수렴 🟢','#1e8449')+card(warn,'경고 🔴','#c0392b')+card(idle,'미실행 ⚪','#7f8c8d');
}
function card(n,l,col){return `<div class="card"><div class="n" style="color:${col}">${n}</div><div class="l">${l}</div></div>`}
function sortBy(k){ if(KEY===k)ASC=!ASC; else {KEY=k;ASC=(k==='name'||k==='room');} render(); }
function render(){
 cards();
 const main=document.getElementById('main');
 if(!CASES.length){
  main.innerHTML=`<div class="empty"><h3>아직 케이스가 없습니다</h3>
   <div class="steps">① ＋ 새 해석 (방·발열·급기 입력) → ② 실행 → ③ 리포트·결과 확인</div>
   <a class="btn" href="/new">＋ 새 해석 시작</a></div>`;
  return;
 }
 const arr=[...CASES].sort((a,b)=>{
  let x=a[KEY],y=b[KEY];
  if(x==null&&y==null)return 0; if(x==null)return 1; if(y==null)return -1;
  if(typeof x==='string')x=x.toLowerCase(),y=(y+'').toLowerCase();
  return (x<y?-1:x>y?1:0)*(ASC?1:-1);
 });
 let h='<div class="tblwrap"><table><thead><tr>';
 for(const c of COLS){
  if(c.k==='_sel'){h+='<th></th>';continue;}
  const arrow=(KEY===c.k)?`<span class="arr">${ASC?'▲':'▼'}</span>`:'';
  h+=`<th class="${c.num?'num':''}" onclick="sortBy('${c.k}')">${c.t}${arrow}</th>`;
 }
 h+='</tr></thead><tbody>';
 for(const r of arr){
  const warn=(r.closure_pct!=null&&(r.closure_pct<90||r.closure_pct>110));
  h+=`<tr class="${warn?'rowwarn':''}">`;
  for(const c of COLS){
   if(c.k==='_sel'){
    h+=`<td><input type="checkbox" ${SEL.has(r.dir)?'checked':''} onchange="selCh('${encodeURIComponent(r.dir)}',this.checked)"></td>`;continue;
   }
   if(c.k==='badge'){h+=`<td><span class="badge" style="background:${r.badge_color||'#7f8c8d'}">${r.badge||''}</span></td>`;continue;}
   if(c.k==='_act'){
    const d=encodeURIComponent(r.dir);
    let a=[];
    if(r.report)a.push(`<a class="rep" target="_blank" href="/case/${d}/report">리포트</a>`);
    if(r.status!=='created')a.push(`<a class="rep" href="#" onclick="openViewer('${d}');return false">결과</a>`);
    a.push(`<a class="rep" href="#" onclick="runCase('${d}');return false">${r.status==='created'?'실행':'재실행'}</a>`);
    if(r.status!=='created')a.push(`<a class="rep" href="#" title="격자 독립성 검증(3격자 배치 실행 → GCI)" onclick="gridCase('${d}');return false">격자</a>`);
    a.push(`<a class="rep del" href="#" onclick="delCase('${d}',this);return false">삭제</a>`);
    h+=`<td>${a.join(' · ')}</td>`;continue;
   }
   h+=`<td class="${c.num?'num':''}">${fmt(c,r[c.k])}</td>`;
  }
  h+='</tr>';
 }
 h+='</tbody></table></div>';
 main.innerHTML=h;
 cmpBar();
}
function selCh(d,on){
 const name=decodeURIComponent(d);
 if(on)SEL.add(name); else SEL.delete(name);
 cmpBar();
}
function cmpBar(){
 const bar=document.getElementById('cmpbar');
 const n=[...SEL].filter(s=>CASES.some(c=>c.dir===s)).length;
 bar.style.display=n>=2?'':'none';
 document.getElementById('cmpn').textContent=n+'개 선택됨';
}
const CMPROWS=[
 ['room','방 (m)',v=>v],['cells','셀',v=>v?v.toLocaleString():'—'],
 ['heat_label','발열',v=>v],['supply_u','급기 m/s',v=>v],
 ['T_avg_C','평균T ℃',v=>v!=null?v.toFixed(1):'—'],
 ['T_max_C','최고T ℃',v=>v!=null?v.toFixed(1):'—','min'],
 ['dT_rise','ΔT K',v=>v!=null?v.toFixed(1):'—'],
 ['outlet_dT','배기 ΔT K',v=>v!=null?v.toFixed(2):'—'],
 ['closure_pct','폐합 %',v=>v!=null?v.toFixed(0)+'%':'—'],
 ['gci_pct','GCI %',v=>v!=null?v.toFixed(1)+'%':'—'],
 ['badge','상태',v=>v||'—'],
];
function openCompare(){
 const sel=CASES.filter(c=>SEL.has(c.dir)).slice(0,4);
 if(sel.length<2){alert('2개 이상 선택하세요');return}
 let h='<table id="cmptbl"><tr><th></th>'+sel.map(c=>`<th>${c.dir}</th>`).join('')+'</tr>';
 for(const [k,label,f,best] of CMPROWS){
  let bi=-1;
  if(best==='min'){
   let bv=Infinity;
   sel.forEach((c,i)=>{if(c[k]!=null&&c[k]<bv){bv=c[k];bi=i;}});
  }
  h+=`<tr><th>${label}</th>`+sel.map((c,i)=>`<td class="${i===bi?'best':''}">${f(c[k])}${i===bi?' ★':''}</td>`).join('')+'</tr>';
 }
 h+='<tr><th>리포트</th>'+sel.map(c=>`<td>${c.report?`<a class="rep" target="_blank" href="/case/${encodeURIComponent(c.dir)}/report">열기</a>`:'—'}</td>`).join('')+'</tr></table>';
 document.getElementById('cmpbody').innerHTML=h;
 document.getElementById('cmpov').style.display='flex';
}
// ── 결과 뷰어 (2D 단면: 슬라이더·호버·기류 화살표 / 3D 컷플레인은 모듈에서) ──
var VW=null, SL=null;   // var = window 프로퍼티(3D 모듈 스크립트가 접근)
const CSTOPS=[[0,[48,18,59]],[0.25,[40,187,236]],[0.5,[164,252,60]],[0.75,[251,126,33]],[1,[122,4,3]]];
function cmap(t){
 t=Math.max(0,Math.min(1,t));
 for(let i=1;i<CSTOPS.length;i++){
  if(t<=CSTOPS[i][0]){
   const [t0,c0]=CSTOPS[i-1],[t1,c1]=CSTOPS[i],f=(t-t0)/(t1-t0);
   return `rgb(${c0.map((v,k)=>Math.round(v+(c1[k]-v)*f)).join(',')})`;
  }
 }
 return 'rgb(122,4,3)';
}
async function openViewer(d){
 const name=decodeURIComponent(d);
 const r=await fetch('/api/fieldinfo/'+d);const j=await r.json();
 if(j.error){alert(j.error);return}
 VW={case:d,name,info:j,axis:'z'};
 document.getElementById('vwtitle').textContent=name+' — 결과 뷰어';
 document.getElementById('vwfield').value='T';
 document.getElementById('vwaxis').value='z';
 document.getElementById('vwvec').checked=false;
 document.getElementById('vwmode').value='2d';
 vwMode();
 vwAxis();
 document.getElementById('vwov').style.display='flex';
}
function vwClose(){document.getElementById('vwov').style.display='none';VW=null;}
function vwMode(){
 const m=document.getElementById('vwmode').value;
 document.getElementById('vw2d').style.display=m==='2d'?'flex':'none';
 document.getElementById('vw3d').style.display=m==='3d'?'':'none';
 for(const id of ['vwaxis','vwidx','vwpos','vwveclb'])
  document.getElementById(id).style.display=m==='3d'?'none':'';
 if(m==='3d'&&VW){
  for(const [id,n] of [['s3x',VW.info.nx],['s3y',VW.info.ny],['s3z',VW.info.nz]]){
   const s=document.getElementById(id);s.min=0;s.max=n-1;s.value=Math.round(n/2);
  }
  if(window.init3D)init3D(); else document.getElementById('vwread').textContent='3D 모듈 로딩 중… 잠시 후 다시';
 }
}
function fieldCh(){
 vwFetch();
 if(document.getElementById('vwmode').value==='3d'&&window.init3D)init3D();
}
function vwAxis(){
 if(!VW)return;
 VW.axis=document.getElementById('vwaxis').value;
 const n={z:VW.info.nz,y:VW.info.ny,x:VW.info.nx}[VW.axis];
 const s=document.getElementById('vwidx');
 s.min=0;s.max=n-1;s.value=Math.round(n/2);
 vwFetch();
}
async function vwFetch(){
 if(!VW)return;
 const f=document.getElementById('vwfield').value;
 const vec=document.getElementById('vwvec').checked?1:0;
 const idx=document.getElementById('vwidx').value;
 const r=await fetch(`/api/slice/${VW.case}?field=${f}&axis=${VW.axis}&idx=${idx}&vec=${vec}`);
 SL=await r.json();
 if(SL.error){document.getElementById('vwread').textContent='⚠ '+SL.error;return}
 const axisName={z:'높이 z',y:'y',x:'x'}[VW.axis];
 document.getElementById('vwpos').textContent=`${axisName} = ${SL.pos} m`;
 vwDraw();
}
function vwRange(){
 const f=document.getElementById('vwfield').value;
 return f==='T'?[VW.info.Tmin,VW.info.Tmax]:[0,VW.info.Umax];
}
function vwDraw(){
 const cv=document.getElementById('vwcv'),ctx=cv.getContext('2d');
 const d=SL.data,ny=d.length,nx=d[0].length;
 const scale=Math.min(660/SL.w,430/SL.h);
 cv.width=Math.max(220,Math.round(SL.w*scale));
 cv.height=Math.max(160,Math.round(SL.h*scale));
 const cw=cv.width/nx,chh=cv.height/ny;
 const [mn,mx]=vwRange(),rg=(mx-mn)||1;
 for(let j=0;j<ny;j++)for(let i=0;i<nx;i++){
  ctx.fillStyle=cmap((d[j][i]-mn)/rg);
  ctx.fillRect(i*cw,cv.height-(j+1)*chh,cw+0.7,chh+0.7);
 }
 if(SL.vx&&document.getElementById('vwvec').checked){
  ctx.strokeStyle='rgba(255,255,255,.85)';ctx.fillStyle='rgba(255,255,255,.85)';ctx.lineWidth=1.1;
  const st=Math.max(1,Math.round(nx/20)),um=VW.info.Umax||1,len=Math.min(cw,chh)*2.1;
  for(let j=0;j<ny;j+=st)for(let i=0;i<nx;i+=st){
   const vx=SL.vx[j][i]/um*len,vy=SL.vy[j][i]/um*len;
   if(Math.abs(vx)<0.5&&Math.abs(vy)<0.5)continue;
   const x0=(i+0.5)*cw,y0=cv.height-(j+0.5)*chh;
   const x1=x0+vx,y1=y0-vy;
   ctx.beginPath();ctx.moveTo(x0,y0);ctx.lineTo(x1,y1);ctx.stroke();
   const a=Math.atan2(y1-y0,x1-x0);
   ctx.beginPath();ctx.moveTo(x1,y1);
   ctx.lineTo(x1-4*Math.cos(a-0.45),y1-4*Math.sin(a-0.45));
   ctx.lineTo(x1-4*Math.cos(a+0.45),y1-4*Math.sin(a+0.45));
   ctx.fill();
  }
 }
 // 컬러바
 const cb=document.getElementById('vwcb'),c2=cb.getContext('2d');
 cb.height=cv.height;
 c2.clearRect(0,0,cb.width,cb.height);
 for(let y=0;y<cb.height;y++){
  c2.fillStyle=cmap(1-y/cb.height);
  c2.fillRect(0,y,20,1);
 }
 c2.fillStyle='#333';c2.font='11px sans-serif';
 const unit=document.getElementById('vwfield').value==='T'?'℃':'m/s';
 c2.fillText(mx.toFixed(1),23,10);
 c2.fillText(((mn+mx)/2).toFixed(1),23,cb.height/2+4);
 c2.fillText(mn.toFixed(1),23,cb.height-2);
 c2.fillText(unit,23,cb.height/2+18);
}
function vwHover(ev){
 if(!SL||!SL.data)return;
 const cv=document.getElementById('vwcv'),rect=cv.getBoundingClientRect();
 const d=SL.data,ny=d.length,nx=d[0].length;
 const i=Math.min(nx-1,Math.max(0,Math.floor((ev.clientX-rect.left)/rect.width*nx)));
 const j=Math.min(ny-1,Math.max(0,ny-1-Math.floor((ev.clientY-rect.top)/rect.height*ny)));
 const px=((i+0.5)*SL.w/nx).toFixed(2),py=((j+0.5)*SL.h/ny).toFixed(2);
 const unit=document.getElementById('vwfield').value==='T'?'℃':'m/s';
 document.getElementById('vwread').textContent=`${SL.hx}=${px} m, ${SL.hy}=${py} m  →  ${d[j][i]} ${unit}`;
}
async function gridCase(d){
 const name=decodeURIComponent(d);
 if(!confirm(name+' 격자 독립성 검증을 실행할까요? (셀 크기 3종을 배치 실행 — 수 분 소요, GCI 배지가 표에 추가됩니다)'))return;
 const r=await fetch('/api/grid/'+d,{method:'POST'});
 const j=await r.json();
 if(j.error)alert(j.error);
}
async function runCase(d){
 const r=await fetch('/api/run/'+d,{method:'POST'});
 const j=await r.json();
 if(j.error)alert(j.error);
 pollNow=true;
}
async function delCase(d){
 const name=decodeURIComponent(d);
 if(!confirm(name+' 케이스 폴더를 삭제할까요? (되돌릴 수 없음)'))return;
 const r=await fetch('/api/delete/'+d,{method:'POST'});
 const j=await r.json();
 if(j.error)alert(j.error); else load();
}
let WAS_ACTIVE=false, pollNow=false;
async function pollStatus(){
 try{
  const r=await fetch('/api/status');const s=await r.json();
  const el=document.getElementById('strip');
  let h='';
  if(s.active){
   WAS_ACTIVE=true;
   const a=s.active;
   const pct=(a.endTime&&a.time)?Math.min(100,Math.round(a.time/a.endTime*100)):0;
   h+=`<div class="strip">▶ 실행중: <b>${a.name}</b> · ${a.step}`+
      (a.time?` · Time ${a.time}/${a.endTime||'?'} (${pct}%)`:'')+
      (s.queue.length?` · 대기 ${s.queue.length}건`:'')+
      `<div class="bar"><div class="fill" style="width:${pct}%"></div></div>`+
      `<details><summary>진행 로그</summary><pre>${(a.lines||[]).join('\\n')}</pre></details></div>`;
  } else {
   if(s.queue.length)h+=`<div class="strip">⏳ 대기열 ${s.queue.length}건…</div>`;
   if(WAS_ACTIVE){WAS_ACTIVE=false;load();}
   for(const [k,v] of Object.entries(s.history||{})){
    if(v&&v.error)h+=`<div class="strip err">⚠ ${k}: ${v.error}</div>`;
   }
  }
  if(!s.openfoam)h+=`<div class="strip err">⚠ WSL OpenFOAM 미설치 — 케이스 생성은 가능하나 실행 불가.
   WSL에서 <code>sudo apt-get install openfoam</code> 후 스튜디오 재시작.</div>`;
  el.innerHTML=h;
 }catch(e){}
 setTimeout(pollStatus,1000);
}
async function load(){
 const r=await fetch('/api/cases');const j=await r.json();
 CASES=j.cases||[];
 document.getElementById('root').textContent='프로젝트: '+j.root;
 render();
}
load();
pollStatus();
</script>
<script type="importmap">{"imports":{"three":"/vendor/three.module.js"}}</script>
<script type="module">
// ── 3D 컷플레인 뷰어 (three.js — preview.py 와 같은 벤더 파일, 오프라인) ──
// 좌표 매핑: CFD(x,y,z; z=높이) → three(X,Z,Y; Y=up). 텍스처는 2D 뷰어와 같은
// /api/slice + cmap 을 그대로 사용 → 값·색이 2D 와 정의상 일치.
import * as THREE from 'three';
import { OrbitControls } from '/vendor/OrbitControls.js';
let R=null;
function mat(c,op){return new THREE.MeshBasicMaterial({color:c,transparent:true,opacity:op,side:THREE.DoubleSide});}
function sliceCanvas(sl){
 const d=sl.data,ny=d.length,nx=d[0].length;
 const c=document.createElement('canvas');c.width=Math.max(64,nx*6);c.height=Math.max(64,ny*6);
 const ctx=c.getContext('2d');
 const [mn,mx]=vwRange(),rg=(mx-mn)||1;
 const cw=c.width/nx,ch=c.height/ny;
 for(let j=0;j<ny;j++)for(let i=0;i<nx;i++){
  ctx.fillStyle=cmap((d[j][i]-mn)/rg);
  ctx.fillRect(i*cw,c.height-(j+1)*ch,cw+0.7,ch+0.7);
 }
 return c;
}
async function setPlane(axis,idx){
 try{
  const f=document.getElementById('vwfield').value;
  const r=await fetch(`/api/slice/${VW.case}?field=${f}&axis=${axis}&idx=${idx}`);
  const sl=await r.json(); if(sl.error){document.getElementById('vwread').textContent='⚠ '+sl.error;return;}
  const {L,W,H}=VW.info.room;
  let mesh=R.planes[axis];
  if(!mesh){
   let g;
   if(axis==='z'){g=new THREE.PlaneGeometry(L,W);}      // 수평면
   else if(axis==='y'){g=new THREE.PlaneGeometry(L,H);} // x–z 면
   else{g=new THREE.PlaneGeometry(W,H);}                // y–z 면
   mesh=new THREE.Mesh(g,new THREE.MeshBasicMaterial({side:THREE.DoubleSide}));
   if(axis==='z')mesh.rotation.x=Math.PI/2;    // 국소+y → 세계+Z(CFD y)
   if(axis==='x')mesh.rotation.y=-Math.PI/2;   // 국소+x → 세계+Z(CFD y)
   R.planes[axis]=mesh;R.scene.add(mesh);
  }
  if(mesh.material.map)mesh.material.map.dispose();
  mesh.material.map=new THREE.CanvasTexture(sliceCanvas(sl));
  mesh.material.needsUpdate=true;
  if(axis==='z')mesh.position.set(L/2,sl.pos,W/2);
  else if(axis==='y')mesh.position.set(L/2,H/2,sl.pos);
  else mesh.position.set(sl.pos,H/2,W/2);
  R.pos[axis]=sl.pos;
  R.renderer.render(R.scene,R.camera);   // RAF 와 무관하게 즉시 1프레임(확실성)
 }catch(e){
  document.getElementById('vwread').textContent='⚠ 3D: '+e.message;
 }
}
window.upd3=function(axis,idx){setPlane(axis,idx);};
window._dbg3=function(){return R?{raf:!!R.raf,children:R.scene.children.length,
 planes:Object.keys(R.planes),pos:R.pos,zY:R.planes.z?R.planes.z.position.y:null}:null;};
window._rot3=function(rad){ // 검증용: OrbitControls 와 동일한 궤도 회전 경로
 if(!R)return null;
 const t=R.controls.target,p=R.camera.position;
 const dx=p.x-t.x,dz=p.z-t.z,r0=Math.hypot(dx,dz),a=Math.atan2(dz,dx)+rad;
 p.set(t.x+r0*Math.cos(a),p.y,t.z+r0*Math.sin(a));
 R.camera.lookAt(t);R.renderer.render(R.scene,R.camera);
 return p.toArray().map(v=>+v.toFixed(2));
};
window.init3D=async function(){
 if(!VW)return;
 const cv=document.getElementById('cv3d');
 if(!R){
  const renderer=new THREE.WebGLRenderer({canvas:cv,antialias:true,preserveDrawingBuffer:true});
  renderer.setSize(640,430,false);
  R={renderer,scene:new THREE.Scene(),camera:new THREE.PerspectiveCamera(45,640/430,0.01,2000),
     controls:null,planes:{},pos:{},raf:null};
  R.scene.background=new THREE.Color(0xf4f6f8);
  R.controls=new OrbitControls(R.camera,cv);
 }
 R.scene.clear(); R.planes={};
 const {L,W,H}=VW.info.room;
 const edges=new THREE.LineSegments(
  new THREE.EdgesGeometry(new THREE.BoxGeometry(L,H,W)),
  new THREE.LineBasicMaterial({color:0x2c5f8a}));
 edges.position.set(L/2,H/2,W/2);
 R.scene.add(edges);
 const face=(wall,color)=>{
  let p;
  if(wall==='x0'||wall==='xL'){p=new THREE.Mesh(new THREE.PlaneGeometry(W,H),mat(color,0.16));
   p.rotation.y=-Math.PI/2;p.position.set(wall==='x0'?0:L,H/2,W/2);}
  else if(wall==='y0'||wall==='yW'){p=new THREE.Mesh(new THREE.PlaneGeometry(L,H),mat(color,0.16));
   p.position.set(L/2,H/2,wall==='y0'?0:W);}
  else return;
  R.scene.add(p);
 };
 if(VW.info.inlet)face(VW.info.inlet,0x2980b9);
 if(VW.info.outlet)face(VW.info.outlet,0xc0392b);
 R.camera.position.set(L*1.45,H*1.7,W*1.55);
 R.controls.target.set(L/2,H/2,W/2);R.controls.update();
 for(const [ax,id] of [['x','s3x'],['y','s3y'],['z','s3z']])
  await setPlane(ax,document.getElementById(id).value);
 // 컬러바(2D와 동일 색범위)
 const cb=document.getElementById('vwcb3'),c2=cb.getContext('2d');
 const [mn,mx]=vwRange();
 c2.clearRect(0,0,cb.width,cb.height);
 for(let y=0;y<cb.height;y++){c2.fillStyle=cmap(1-y/cb.height);c2.fillRect(0,y,20,1);}
 c2.fillStyle='#333';c2.font='11px sans-serif';
 const unit=document.getElementById('vwfield').value==='T'?'℃':'m/s';
 c2.fillText(mx.toFixed(1),23,10);c2.fillText(mn.toFixed(1),23,cb.height-2);
 c2.fillText(unit,23,cb.height/2+4);
 R.renderer.render(R.scene,R.camera);
 if(!R.raf)anim();
};
function anim(){
 if(!R)return;
 const vis=document.getElementById('vw3d').style.display!=='none'
        && document.getElementById('vwov').style.display!=='none';
 if(!vis){R.raf=null;return;}
 R.raf=requestAnimationFrame(anim);
 R.controls.update();
 R.renderer.render(R.scene,R.camera);
}
</script></body></html>"""


# ── 새 해석 마법사 페이지 ────────────────────────────────────────────────────

PAGE_NEW = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>새 해석 — MEP CFD Studio</title>
<style>
 :root{--accent:#2c5f8a;--line:#e2e2e2;--muted:#666}
 *{box-sizing:border-box}
 body{font-family:'Malgun Gothic','Segoe UI',sans-serif;margin:0;background:#f0f2f5;color:#1a1a1a}
 .wrap{max-width:860px;margin:18px auto;padding:0 16px}
 .hdr{display:flex;align-items:center;gap:14px;background:#fff;padding:14px 20px;border-radius:10px;box-shadow:0 1px 6px rgba(0,0,0,.07)}
 .hdr h1{font-size:19px;margin:0;color:var(--accent)}
 .btn{background:var(--accent);color:#fff;border:none;border-radius:8px;padding:9px 16px;font-size:14px;cursor:pointer;text-decoration:none;display:inline-block}
 .btn.sec{background:#fff;color:var(--accent);border:1px solid var(--accent)}
 .panel{background:#fff;border-radius:10px;box-shadow:0 1px 6px rgba(0,0,0,.07);padding:16px 22px;margin:14px 0}
 h2{font-size:15px;color:var(--accent);border-bottom:2px solid var(--accent);padding-bottom:6px;margin:2px 0 14px}
 label{margin-right:18px}
 input[type=number],input[type=text]{border:1px solid #ccc;border-radius:6px;padding:6px 8px;font-size:14px;width:90px}
 input.wide{width:430px} input.mid{width:220px}
 select{border:1px solid #ccc;border-radius:6px;padding:6px;font-size:14px}
 .row{margin:9px 0;line-height:2.1}
 .hint{background:#eaf2f8;border-radius:8px;padding:8px 12px;font-size:12.5px;color:#1a5276;margin:8px 0}
 .warn{color:#b9770e;font-size:12.5px;font-weight:600}
 .prevbox{background:#fbf9f3;border:1px solid #e0d9c2;border-radius:8px;padding:12px 16px;font-size:13.5px;line-height:1.9;margin-bottom:12px}
 .err{color:#c0392b;font-weight:600;margin-top:10px}
 .req{color:#c0392b}
</style></head><body><div class="wrap">
 <div class="hdr"><h1>＋ 새 해석</h1><div style="flex:1"></div><a class="btn sec" href="/">← 대시보드</a></div>

 <div class="panel"><h2>STEP 1 · 방 정보</h2>
  <div class="row">
   <label><input type="radio" name="mode" value="manual" checked onchange="modeCh()"> 치수 직접 입력</label>
   <label><input type="radio" name="mode" value="geometry" onchange="modeCh()"> 도면(geometry.json)에서 자동 추출</label>
  </div>
  <div id="sec_manual">
   <div class="row">L <input id="L" type="number" step="0.1" value="11.0" oninput="preview()"> ×
    W <input id="W" type="number" step="0.1" value="9.0" oninput="preview()"> ×
    H <input id="H" type="number" step="0.1" value="5.4" oninput="preview()"> m</div>
  </div>
  <div id="sec_geom" style="display:none">
   <div class="row">경로 <input id="gpath" type="text" class="wide" placeholder="C:\\...\\geometry.json">
    <button class="btn sec" onclick="inspect()">불러오기</button></div>
   <div id="ginfo" class="hint" style="display:none"></div>
   <div class="row">zone <select id="zone" onchange="selCh()"><option value="">(bbox 직접)</option></select>
    &nbsp;bbox(mm) <input id="bbox" type="text" class="mid" placeholder="x0,y0,x1,y1" onchange="selCh()">
    &nbsp;층고 <input id="height" type="number" step="0.1" value="3.0" oninput="preview()"> m</div>
   <div id="ohint" class="hint" style="display:none"></div>
  </div>
 </div>

 <div class="panel"><h2>STEP 2 · 해석 조건</h2>
  <div class="row">발열(계산서 총발열) <input id="kw" type="number" step="0.5" placeholder="예: 10" oninput="preview()"> kW
   <span class="req">★권장</span> <span style="color:var(--muted);font-size:12px">— 비우면 구식 바닥 40°C 고정온도 모드</span></div>
  <div class="row">급기 벽 <select id="supply" onchange="preview()"></select>
   &nbsp;배기 벽 <select id="exhaust" onchange="preview()"></select>
   <span style="color:var(--muted);font-size:12px">(x0=서, xL=동, y0=남, yW=북 벽 — 도면 좌표 기준)</span></div>
  <div class="row">급기 유속 <input id="su" type="number" step="0.05" value="0.3" oninput="preview()"> m/s
   <span id="suwarn" class="warn"></span></div>
  <div class="row">급기 온도 <input id="st" type="number" step="1" value="20"> °C
   &nbsp;· 격자 셀 <input id="cell" type="number" step="0.05" value="0.3"> m
   &nbsp;· 최대 반복 <input id="iters" type="number" step="50" value="400"></div>
 </div>

 <div class="panel"><h2>STEP 3 · 확인</h2>
  <div id="preview" class="prevbox">방 정보를 입력하세요.</div>
  <div class="row">케이스명 <input id="name" type="text" class="mid" placeholder="전기실_B1_10kW"></div>
  <button class="btn" onclick="create(false)">생성</button>
  <button class="btn" onclick="create(true)">생성＋즉시 실행</button>
  <span id="msg" class="err"></span>
 </div>
</div>
<script>
let GDIMS=null, OHINT={};
const WALLS=['x0','xL','y0','yW'];
function v(id){return document.getElementById(id).value}
function el(id){return document.getElementById(id)}
function mode(){return document.querySelector('input[name=mode]:checked').value}
function modeCh(){
 el('sec_manual').style.display=mode()==='manual'?'':'none';
 el('sec_geom').style.display=mode()==='geometry'?'':'none';
 preview();
}
function wallOpts(){
 for(const id of ['supply','exhaust']){
  const cur=v(id)|| (id==='supply'?'x0':'xL');
  el(id).innerHTML=WALLS.map(w=>{
   const star=OHINT[w]?` ★개구부${OHINT[w]}`:'';
   return `<option value="${w}" ${w===cur?'selected':''}>${w}${star}</option>`;
  }).join('');
 }
}
async function inspect(){
 const r=await fetch('/api/inspect',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({geometry:v('gpath')})});
 const j=await r.json();
 if(j.error){el('ginfo').style.display='';el('ginfo').textContent='⚠ '+j.error;return}
 el('ginfo').style.display='';
 el('ginfo').innerHTML=`벽 ${j.walls} · 개구부 ${j.openings} · 장비 ${j.equipment} · zone ${j.zones.length}개`+
  (j.wall_extent_mm?` · 도면범위 x ${j.wall_extent_mm[0]}~${j.wall_extent_mm[2]}, y ${j.wall_extent_mm[1]}~${j.wall_extent_mm[3]} mm`:'');
 el('zone').innerHTML='<option value="">(bbox 직접)</option>'+
  j.zones.map(z=>`<option value="${z.i}">zone[${z.i}] ${z.L}×${z.W} m</option>`).join('');
 if(j.height_m)el('height').value=j.height_m;
 if(j.zones.length){el('zone').value=j.zones[0].i;selCh();}
}
async function selCh(){
 const body={geometry:v('gpath')};
 if(v('zone')!=='')body.zone=v('zone'); else if(v('bbox').trim())body.bbox=v('bbox');
 else {GDIMS=null;preview();return}
 const r=await fetch('/api/inspect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 const j=await r.json();
 if(j.error){el('ohint').style.display='';el('ohint').textContent='⚠ '+j.error;GDIMS=null;preview();return}
 GDIMS=j.room||null; OHINT=j.openings_by_wall||{};
 let t='';
 if(Object.keys(OHINT).length)t+='💡 경계 개구부(급/배기 후보): '+Object.entries(OHINT).map(([w,n])=>`${w}벽 ${n}개`).join(' · ');
 if(j.warnings&&j.warnings.length)t+=(t?'<br>':'')+j.warnings.map(w=>'⚠ '+w).join('<br>');
 el('ohint').style.display=t?'':'none'; el('ohint').innerHTML=t;
 wallOpts(); preview();
}
function dims(){
 if(mode()==='manual')return {L:+v('L'),W:+v('W'),H:+v('H')};
 if(!GDIMS)return null;
 return {L:GDIMS.L,W:GDIMS.W,H:+v('height')||GDIMS.H};
}
function preview(){
 const u=+v('su');
 el('suwarn').textContent=(u&&u<0.1)?'⚠ 약유동: 에너지폐합이 안 닫혀 미수렴 위험 — 0.3 이상 권장':'';
 const d=dims(), pv=el('preview');
 if(!d||!d.L||!d.W||!d.H){pv.innerHTML='방 정보를 입력하세요 (도면 모드는 불러오기 → zone/bbox 선택).';return}
 const sup=v('supply')||'x0';
 const A=(sup==='x0'||sup==='xL')?d.W*d.H:d.L*d.H;
 const Q=u*A, cmh=Q*3600, vol=d.L*d.W*d.H, ach=cmh/vol;
 const kw=parseFloat(v('kw'));
 let t=`방 ${d.L}×${d.W}×${d.H} m — 체적 ${vol.toFixed(0)} m³<br>`+
  `풍량 = ${u} m/s × ${A.toFixed(1)} m² = <b>${cmh.toLocaleString(undefined,{maximumFractionDigits:0})} CMH</b> · ACH ${ach.toFixed(1)}`;
 if(kw&&Q>0)t+=`<br>예상 배기 ΔT = Q/(ρc·V̇) = ${kw}kW/(1206×${Q.toFixed(2)}) = <b>${(kw*1000/(1206*Q)).toFixed(2)} K</b>
  <span style="color:#666;font-size:12px">— 실행 후 CFD 배기 ΔT·에너지폐합이 이 손계산과 맞아야 정상</span>`;
 else t+=`<br><span class="warn">발열 kW 미입력 — 계산서 대조(에너지폐합 검증)가 불가한 구식 모드로 생성됩니다.</span>`;
 pv.innerHTML=t;
}
async function create(runNow){
 el('msg').textContent='';
 const p={mode:mode(),name:v('name'),power_kw:v('kw'),supply:v('supply'),exhaust:v('exhaust'),
  supply_u:v('su'),supply_T_C:v('st'),cell:v('cell'),endtime:v('iters'),run_now:runNow};
 if(mode()==='manual'){p.L=v('L');p.W=v('W');p.H=v('H');}
 else{p.geometry=v('gpath');p.zone=v('zone');p.bbox=v('bbox');p.height=v('height');}
 if(!p.name){el('msg').textContent='케이스명을 입력하세요';return}
 const r=await fetch('/api/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});
 const j=await r.json();
 if(j.error){el('msg').textContent=j.error;return}
 if(j.run_error)alert('생성됨. 실행 실패: '+j.run_error);
 location.href='/';
}
wallOpts(); el('exhaust').value='xL'; preview();
</script></body></html>"""


# ── 기동 ─────────────────────────────────────────────────────────────────────

def find_port(prefer):
    """지정 포트가 사용 중이면 +1 씩 20개까지 시도, 0이면 OS 임의."""
    if prefer == 0:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p
    for p in range(prefer, prefer + 20):
        try:
            s = socket.socket()
            s.bind(("127.0.0.1", p))
            s.close()
            return p
        except OSError:
            continue
    raise SystemExit(f"빈 포트를 못 찾음({prefer}~{prefer+19})")


def main():
    global ROOT, OPENFOAM_OK
    ap = argparse.ArgumentParser(description="MEP CFD Studio — 대시보드 통합 프로그램")
    ap.add_argument("--root", default=os.path.join(HERE, "cfd_projects"),
                    help="프로젝트 루트(케이스 폴더 모음, 기본 cfd_projects/)")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    ROOT = os.path.abspath(args.root)
    os.makedirs(ROOT, exist_ok=True)
    OPENFOAM_OK = check_openfoam()
    port = find_port(args.port)
    url = f"http://127.0.0.1:{port}"
    httpd = ThreadingHTTPServer(("127.0.0.1", port), StudioHandler)
    print(f"MEP CFD Studio: {url}")
    print(f"  프로젝트 루트: {ROOT}")
    print(f"  OpenFOAM(WSL): {'OK' if OPENFOAM_OK else '없음 — 실행 기능 비활성(생성·조회만)'}")
    print("  종료: Ctrl+C")
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료.")


if __name__ == "__main__":
    main()
