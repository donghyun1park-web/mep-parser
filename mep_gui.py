"""
mep_gui.py  —  [Phase 2.5] 사용성 껍데기 + 수정 루프
CLI 모르는 현장 사용자용 GUI. 더블클릭(run_gui.bat) → 파일 선택 →
스캔 → 파싱 → needs_review 수정 → 3D 빌드까지 한 창에서.

설계:
- 새 의존성 0 (tkinter = 파이썬 표준). 엔진은 dxf_parser 모듈을 그대로 재사용.
- 수정 루프: needs_review 요소를 목록에 띄우고 폭/높이를 고쳐 geometry.json 에 반영·저장.
  빌드는 '저장된 geometry.json'에서 함 → 사람이 고친 값이 그대로 3D 로 감(보존).
  (주의: DXF 재파싱은 수정을 덮어씀 → 재파싱 버튼은 경고 후 진행.)
- FreeCAD 빌드는 freecadcmd.exe 자동 탐지 후 subprocess + 환경변수(MEP_GEOMETRY/MEP_OUT).
"""
import contextlib
import glob
import io
import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import dxf_parser as P

HERE = os.path.dirname(os.path.abspath(__file__))


def find_freecadcmd():
    """freecadcmd.exe 자동 탐지. 없으면 None."""
    cands = [r"C:\Program Files\FreeCAD 1.1\bin\freecadcmd.exe"]
    cands += glob.glob(r"C:\Program Files\FreeCAD*\bin\freecadcmd.exe")
    cands += glob.glob(r"C:\Program Files (x86)\FreeCAD*\bin\freecadcmd.exe")
    for c in cands:
        if os.path.exists(c):
            return c
    return None


class App:
    def __init__(self, root):
        self.root = root
        root.title("MEP Parser — DXF → 3D BIM (현장용)")
        root.geometry("820x640")
        self.data = None          # 파싱 결과 dict
        self.geom_path = None     # 저장된 geometry.json 경로

        self.v_dxf = tk.StringVar()
        self.v_map = tk.StringVar(value=os.path.join(HERE, "layer_map.csv"))
        self.v_block = tk.StringVar(value=os.path.join(HERE, "block_map.csv"))
        # LLM tie-break: API key 있을 때만 기본 활성
        self.v_llm = tk.BooleanVar(value=bool(os.environ.get("ANTHROPIC_API_KEY")))

        self._build_file_row()
        self._build_buttons()
        self._build_review()
        self._build_log()
        self._log(f"FreeCAD: {find_freecadcmd() or '미탐지(빌드 비활성)'}")

    # ── UI 구성 ──────────────────────────────────────────────
    def _build_file_row(self):
        f = ttk.LabelFrame(self.root, text="1) 파일 선택")
        f.pack(fill="x", padx=8, pady=6)
        rows = [("DXF 도면", self.v_dxf, self._pick_dxf, "*.dxf"),
                ("레이어 맵", self.v_map, lambda: self._pick_csv(self.v_map), "*.csv"),
                ("블록 맵", self.v_block, lambda: self._pick_csv(self.v_block), "*.csv")]
        for i, (lbl, var, cmd, _) in enumerate(rows):
            ttk.Label(f, text=lbl, width=10).grid(row=i, column=0, sticky="w", padx=4, pady=2)
            ttk.Entry(f, textvariable=var, width=78).grid(row=i, column=1, padx=4)
            ttk.Button(f, text="찾기", command=cmd).grid(row=i, column=2, padx=4)

    def _build_buttons(self):
        f = ttk.Frame(self.root)
        f.pack(fill="x", padx=8)
        ttk.Button(f, text="① 도면 점검(스캔)", command=self._do_scan).pack(side="left", padx=4)
        ttk.Button(f, text="② 파싱 → geometry.json", command=self._do_parse).pack(side="left", padx=4)
        self.btn_build = ttk.Button(f, text="④ 3D 빌드(FreeCAD)", command=self._do_build)
        self.btn_build.pack(side="left", padx=4)
        if find_freecadcmd() is None:
            self.btn_build.state(["disabled"])
        ttk.Button(f, text="DWG->DXF 체크리스트",
                   command=self._show_checklist).pack(side="right", padx=4)
        ttk.Checkbutton(f, text="LLM 분류 보조",
                        variable=self.v_llm).pack(side="right", padx=2)

    def _build_review(self):
        f = ttk.LabelFrame(self.root, text="③ 검토 필요 항목 (needs_review) — 고치고 [적용] 누르면 저장")
        f.pack(fill="both", expand=True, padx=8, pady=6)
        cols = ("idx", "cat", "pairing", "width", "conf")
        self.tree = ttk.Treeview(f, columns=cols, show="headings", height=7)
        for c, w in zip(cols, (50, 80, 90, 110, 80)):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w, anchor="center")
        self.tree.pack(side="left", fill="both", expand=True, padx=4, pady=4)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        e = ttk.Frame(f)
        e.pack(side="right", fill="y", padx=6)
        ttk.Label(e, text="폭/지름 width(mm)").pack(anchor="w")
        self.v_w = tk.StringVar()
        ttk.Entry(e, textvariable=self.v_w, width=12).pack(anchor="w", pady=2)
        ttk.Label(e, text="높이 height(mm)").pack(anchor="w")
        self.v_h = tk.StringVar()
        ttk.Entry(e, textvariable=self.v_h, width=12).pack(anchor="w", pady=2)
        ttk.Button(e, text="적용·저장", command=self._apply_review).pack(anchor="w", pady=6)

    def _build_log(self):
        f = ttk.LabelFrame(self.root, text="로그")
        f.pack(fill="both", padx=8, pady=6)
        self.txt = tk.Text(f, height=9, wrap="none")
        self.txt.pack(fill="both", expand=True, padx=4, pady=4)

    # ── 동작 ─────────────────────────────────────────────────
    def _log(self, msg):
        self.txt.insert("end", str(msg) + "\n")
        self.txt.see("end")

    def _pick_dxf(self):
        p = filedialog.askopenfilename(filetypes=[("DXF", "*.dxf"), ("All", "*.*")])
        if p:
            self.v_dxf.set(p)

    def _pick_csv(self, var):
        p = filedialog.askopenfilename(filetypes=[("CSV", "*.csv"), ("All", "*.*")])
        if p:
            var.set(p)

    def _rules(self):
        m = self.v_map.get().strip()
        b = self.v_block.get().strip()
        rules = P.load_layer_map(m) if m and os.path.exists(m) else P.DEFAULT_LAYER_RULES
        brules = P.load_layer_map(b) if b and os.path.exists(b) else P.DEFAULT_BLOCK_RULES
        return rules, brules

    def _show_checklist(self):
        """DWG→DXF 체크리스트 팝업 창."""
        win = tk.Toplevel(self.root)
        win.title("DWG → DXF 내보내기 체크리스트")
        win.geometry("640x560")
        txt = tk.Text(win, wrap="word", font=("Consolas", 9))
        sb = ttk.Scrollbar(win, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(fill="both", expand=True, padx=6, pady=6)
        txt.insert("end", P.DWG_DXF_CHECKLIST)
        txt.configure(state="disabled")

    def _do_scan(self):
        dxf = self.v_dxf.get().strip()
        if not dxf or not os.path.exists(dxf):
            messagebox.showwarning("확인", "DXF 도면을 먼저 선택하세요.")
            return
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                P.scan(dxf)
        except Exception as e:
            self._log(f"[오류] 스캔 실패: {e}")
            return
        self._log(buf.getvalue())

    def _do_parse(self):
        dxf = self.v_dxf.get().strip()
        if not dxf or not os.path.exists(dxf):
            messagebox.showwarning("확인", "DXF 도면을 먼저 선택하세요.")
            return
        rules, brules = self._rules()
        try:
            self.data = P.parse(dxf, rules, brules)
        except Exception as e:
            self._log(f"[오류] 파싱 실패: {e}")
            return
        self.geom_path = os.path.splitext(dxf)[0] + ".geometry.json"
        # [6a] LLM tie-break: 파싱 직후, 저장 전에 모호 제안 보강
        if self.v_llm.get() and self.data.get("suggestions"):
            self._log("  [LLM] 모호 레이어 분류 요청 중...")
            try:
                P.llm_tiebreak_suggestions(self.data["suggestions"])
            except Exception as e:
                self._log(f"  [LLM] 실패: {e}")
        self._save()
        el = self.data["elements"]
        wp = self.data.get("wall_pairing", {})
        bk = self.data.get("blocks", {})
        self._log(f"파싱 완료 -> {self.geom_path}")
        self._log(f"  walls={len(el['wall'])} columns={len(el['column'])} "
                  f"slabs={len(el['slab'])} zones={len(el['zone'])} openings={len(el['opening'])}")
        self._log(f"  벽 쌍: paired={wp.get('paired',0)} single={wp.get('single',0)} | "
                  f"블록 {bk.get('inserts',0)}개(미매핑 {bk.get('unmapped',0)})")
        for w in self.data.get("warnings", []):
            self._log(f"  [warn] {w}")
        for s in self.data.get("suggestions", []):
            g = f"기하={s['geom_guess']}({s['geom_confidence']})" if s.get("geom_guess") else "기하=?"
            nm = (f"이름~{s['name_match']}->{s['name_guess']}({s['name_score']})"
                  if s.get("name_guess") else "이름=?")
            llm = (f" [LLM->{s['llm_guess']}({s['llm_confidence']}) {s['llm_reason']}]"
                   if s.get("llm_guess") else "")
            self._log(f"  [제안] '{s['layer']}'x{s['count']}: {g} {nm}{llm}"
                      "  -> layer_map.csv 에 추가 검토")
        self._populate_review()

    def _populate_review(self):
        self.tree.delete(*self.tree.get_children())
        if not self.data:
            return
        n = 0
        for cat, items in self.data["elements"].items():
            for idx, el in enumerate(items):
                if el.get("needs_review"):
                    wd = el.get("width_detected")
                    self.tree.insert("", "end", iid=f"{cat}:{idx}",
                                     values=(idx, cat, el.get("pairing", "-"),
                                             wd if wd is not None else "(기본)",
                                             el.get("confidence", "-")))
                    n += 1
        self._log(f"검토 필요 항목: {n}개" + ("" if n else " — 모두 자동 검출 OK"))

    def _on_select(self, _evt):
        sel = self.tree.selection()
        if not sel:
            return
        cat, idx = sel[0].split(":")
        el = self.data["elements"][cat][int(idx)]
        ov = el.get("overrides", {})
        self.v_w.set(str(ov.get("width", el.get("width_detected") or "")))
        self.v_h.set(str(ov.get("height", "")))

    def _apply_review(self):
        sel = self.tree.selection()
        if not sel or not self.data:
            messagebox.showinfo("안내", "목록에서 항목을 먼저 고르세요.")
            return
        cat, idx = sel[0].split(":")
        el = self.data["elements"][cat][int(idx)]
        ov = el.setdefault("overrides", {})
        try:
            if self.v_w.get().strip():
                ov["width"] = float(self.v_w.get())
            if self.v_h.get().strip():
                ov["height"] = float(self.v_h.get())
        except ValueError:
            messagebox.showwarning("확인", "숫자만 입력하세요.")
            return
        el["needs_review"] = False
        self._save()
        self._log(f"[적용] {cat}[{idx}] overrides={ov} → 저장")
        self._populate_review()

    def _save(self):
        if self.geom_path and self.data:
            with open(self.geom_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)

    def _do_build(self):
        if not self.geom_path or not os.path.exists(self.geom_path):
            messagebox.showwarning("확인", "먼저 파싱하세요.")
            return
        fc = find_freecadcmd()
        if not fc:
            messagebox.showerror("FreeCAD 없음", "freecadcmd.exe 를 찾지 못했습니다.")
            return
        out = os.path.splitext(self.geom_path)[0].replace(".geometry", "") + "_model"
        env = dict(os.environ, MEP_GEOMETRY=self.geom_path, MEP_OUT=out,
                   PYTHONIOENCODING="utf-8")
        self.btn_build.state(["disabled"])
        self._log(f"빌드 시작… (freecadcmd) → {out}.FCStd / .ifc")

        def run():
            try:
                r = subprocess.run([fc, os.path.join(HERE, "freecad_builder.py")],
                                   cwd=HERE, env=env, capture_output=True,
                                   text=True, encoding="utf-8", errors="replace",
                                   timeout=300)
                self.root.after(0, lambda: self._build_done(r, out))
            except Exception as e:
                self.root.after(0, lambda: (self._log(f"[오류] 빌드 실패: {e}"),
                                            self.btn_build.state(["!disabled"])))
        threading.Thread(target=run, daemon=True).start()

    def _build_done(self, r, out):
        for line in (r.stdout or "").splitlines():
            self._log("  " + line)
        if r.returncode != 0 and r.stderr:
            self._log("[stderr] " + r.stderr.strip()[:500])
        ok = os.path.exists(out + ".FCStd")
        self._log(f"빌드 {'완료' if ok else '실패'}: {out}.FCStd"
                  + (" / " + out + ".ifc" if os.path.exists(out + ".ifc") else ""))
        self.btn_build.state(["!disabled"])


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
