"""
mep_gui.py  --  [Phase 2.5] GUI shell + edit loop
Double-click (run_gui.bat) -> file select -> scan -> parse -> review -> 3D build.

Design:
- Zero new dependencies (tkinter = Python stdlib). Engine reuses dxf_parser module.
- Review loop: needs_review items shown in list; user edits width/height -> saved to geometry.json.
  Build reads saved geometry.json -> preserves manual edits.
  (Warning: re-parsing overwrites edits -> re-parse button warns before proceeding.)
- FreeCAD build: auto-detect freecadcmd.exe -> subprocess + env vars (MEP_GEOMETRY/MEP_OUT).
- Layer map editor: add/delete/save layer_map.csv rows inside GUI.
  Shows unmapped layers from last parse for quick one-click add.
"""
import contextlib
import csv
import glob
import io
import json
import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import dxf_parser as P

HERE = os.path.dirname(os.path.abspath(__file__))


def resource_path(rel):
    """동봉 리소스(읽기 전용) 경로. PyInstaller onefile 이면 sys._MEIPASS(임시 추출
    dir), 아니면 소스 디렉터리. layer_map/block_map/freecad_builder.py/vendor 용."""
    base = getattr(sys, "_MEIPASS", None) or HERE
    return os.path.join(base, rel)


def user_csv(name):
    """사용자 편집 가능한 CSV 경로. frozen(.exe) 이면 exe 폴더에 영구 사본 보장
    (없으면 번들본 복사) → 편집·저장이 재시작 후에도 유지. 소스 실행이면 그대로."""
    if not getattr(sys, "frozen", False):
        return os.path.join(HERE, name)
    dst = os.path.join(os.path.dirname(sys.executable), name)
    if not os.path.exists(dst):
        try:
            shutil.copyfile(resource_path(name), dst)
        except Exception:
            return resource_path(name)
    return dst


CATEGORIES = ["wall", "column", "slab", "zone", "opening", "pipe", "duct", "tray"]


def find_freecadcmd():
    """Auto-detect freecadcmd.exe. Returns None if not found."""
    cands = [r"C:\Program Files\FreeCAD 1.1\bin\freecadcmd.exe"]
    cands += glob.glob(r"C:\Program Files\FreeCAD*\bin\freecadcmd.exe")
    cands += glob.glob(r"C:\Program Files (x86)\FreeCAD*\bin\freecadcmd.exe")
    for c in cands:
        if os.path.exists(c):
            return c
    return None


# ── Layer Map CSV helpers ─────────────────────────────────────────────────────

def _read_csv_rows(csv_path):
    """Read layer_map.csv -> list of dicts {pattern, category, width, height, thickness}."""
    rows = []
    if not os.path.exists(csv_path):
        return rows
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.DictReader(filter(lambda l: not l.startswith("# "), f)):
            rows.append({
                "pattern": row.get("pattern", "").strip(),
                "category": row.get("category", "").strip(),
                "width": row.get("width", "").strip(),
                "height": row.get("height", "").strip(),
                "thickness": row.get("thickness", "").strip(),
            })
    return rows


def _write_csv_rows(csv_path, rows):
    """Write rows back to layer_map.csv (overwrites, keeps header comment)."""
    header_comment = (
        "# layer_map.csv  --  layer pattern -> category/parameter mapping\n"
        "# pattern: regex (case-insensitive) / "
        "category: wall|column|slab|zone|opening|pipe|duct|tray\n"
        "# width/height/thickness: mm (leave blank to use param defaults)\n"
    )
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        f.write(header_comment)
        writer = csv.DictWriter(f, fieldnames=["pattern", "category", "width", "height", "thickness"])
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


# ── Layer Map Editor Window ───────────────────────────────────────────────────

class LayerMapEditor:
    """Popup window: view/add/delete layer_map.csv rows + quick-add unmapped layers."""

    def __init__(self, parent, csv_path, unmapped_suggestions=None):
        self.csv_path = csv_path
        self.rows = _read_csv_rows(csv_path)
        self.unmapped = unmapped_suggestions or []  # list of suggestion dicts

        self.win = tk.Toplevel(parent)
        self.win.title(f"Layer Map Editor — {os.path.basename(csv_path)}")
        self.win.geometry("860x540")
        self.win.grab_set()  # modal

        self._build_ui()
        self._refresh_tree()

    def _build_ui(self):
        # ── Top: treeview of current rules ────────────────────
        top = ttk.LabelFrame(self.win, text="Current layer rules (editable)")
        top.pack(fill="both", expand=True, padx=8, pady=6)

        cols = ("pattern", "category", "width", "height", "thickness")
        self.tree = ttk.Treeview(top, columns=cols, show="headings", height=10)
        col_widths = (220, 90, 70, 70, 80)
        for c, w in zip(cols, col_widths):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(top, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True, padx=4, pady=4)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        ttk.Button(top, text="Delete selected row",
                   command=self._delete_row).pack(side="right", padx=4, pady=2)

        # ── Middle: add new row ────────────────────────────────
        mid = ttk.LabelFrame(self.win, text="Add new rule")
        mid.pack(fill="x", padx=8, pady=4)

        fields = [("Pattern (regex)", 28), ("Category", 12), ("Width mm", 8),
                  ("Height mm", 8), ("Thickness mm", 10)]
        self.v_pat = tk.StringVar()
        self.v_cat = tk.StringVar(value="column")
        self.v_wid = tk.StringVar()
        self.v_hei = tk.StringVar()
        self.v_thk = tk.StringVar()
        vars_ = [self.v_pat, self.v_cat, self.v_wid, self.v_hei, self.v_thk]

        for col, ((lbl, w), var) in enumerate(zip(fields, vars_)):
            ttk.Label(mid, text=lbl).grid(row=0, column=col, padx=4, sticky="w")
            if lbl == "Category":
                cb = ttk.Combobox(mid, textvariable=var, values=CATEGORIES, width=w, state="readonly")
                cb.grid(row=1, column=col, padx=4, pady=2)
            else:
                ttk.Entry(mid, textvariable=var, width=w).grid(row=1, column=col, padx=4, pady=2)

        ttk.Button(mid, text="Add row", command=self._add_row).grid(
            row=1, column=len(fields), padx=8, pady=2)

        # ── Bottom: unmapped layers from last parse ────────────
        if self.unmapped:
            bot = ttk.LabelFrame(self.win,
                                 text="Unmapped layers from last parse — click to pre-fill")
            bot.pack(fill="x", padx=8, pady=4)
            canvas = tk.Canvas(bot, height=60)
            hscroll = ttk.Scrollbar(bot, orient="horizontal", command=canvas.xview)
            canvas.configure(xscrollcommand=hscroll.set)
            hscroll.pack(side="bottom", fill="x")
            canvas.pack(fill="x", padx=4)
            inner = ttk.Frame(canvas)
            canvas.create_window((0, 0), window=inner, anchor="nw")
            for s in self.unmapped:
                layer = s.get("layer", "")
                guess = s.get("llm_guess") or s.get("geom_guess") or s.get("name_guess") or "column"
                lbl = f"{layer} [{guess}]"
                ttk.Button(inner, text=lbl,
                           command=lambda l=layer, g=guess: self._prefill(l, g)
                           ).pack(side="left", padx=3, pady=4)
            inner.update_idletasks()
            canvas.configure(scrollregion=canvas.bbox("all"))

        # ── Save button ────────────────────────────────────────
        btn_bar = ttk.Frame(self.win)
        btn_bar.pack(fill="x", padx=8, pady=6)
        ttk.Button(btn_bar, text="Save to CSV", command=self._save).pack(side="right", padx=4)
        ttk.Button(btn_bar, text="Cancel", command=self.win.destroy).pack(side="right", padx=4)
        self.status = ttk.Label(btn_bar, text="")
        self.status.pack(side="left", padx=4)

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(self.rows):
            self.tree.insert("", "end", iid=str(i),
                             values=(r["pattern"], r["category"],
                                     r["width"], r["height"], r["thickness"]))

    def _on_tree_select(self, _evt):
        sel = self.tree.selection()
        if not sel:
            return
        r = self.rows[int(sel[0])]
        self.v_pat.set(r["pattern"])
        self.v_cat.set(r["category"])
        self.v_wid.set(r["width"])
        self.v_hei.set(r["height"])
        self.v_thk.set(r["thickness"])

    def _prefill(self, layer, guess):
        """Pre-fill pattern and category from unmapped layer chip."""
        import re
        escaped = re.escape(layer)
        self.v_pat.set(escaped)
        cat = guess if guess in CATEGORIES else "column"
        self.v_cat.set(cat)
        self.v_wid.set("")
        self.v_hei.set("")
        self.v_thk.set("")
        self.status.config(text=f"Pre-filled: {layer} -> {cat}. Adjust and click [Add row].")

    def _add_row(self):
        pat = self.v_pat.get().strip()
        cat = self.v_cat.get().strip()
        if not pat:
            messagebox.showwarning("Input error", "Pattern cannot be empty.", parent=self.win)
            return
        if cat not in CATEGORIES:
            messagebox.showwarning("Input error",
                                   f"Category must be one of: {', '.join(CATEGORIES)}",
                                   parent=self.win)
            return
        self.rows.append({
            "pattern": pat,
            "category": cat,
            "width": self.v_wid.get().strip(),
            "height": self.v_hei.get().strip(),
            "thickness": self.v_thk.get().strip(),
        })
        self._refresh_tree()
        # clear inputs
        self.v_pat.set("")
        self.v_wid.set("")
        self.v_hei.set("")
        self.v_thk.set("")
        self.status.config(text=f"Added: {pat} -> {cat}")

    def _delete_row(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Info", "Select a row first.", parent=self.win)
            return
        idx = int(sel[0])
        removed = self.rows.pop(idx)
        self._refresh_tree()
        self.status.config(text=f"Deleted: {removed['pattern']}")

    def _save(self):
        try:
            _write_csv_rows(self.csv_path, self.rows)
            self.status.config(text=f"Saved {len(self.rows)} rows -> {os.path.basename(self.csv_path)}")
            messagebox.showinfo("Saved",
                                f"Saved {len(self.rows)} rules to:\n{self.csv_path}",
                                parent=self.win)
            self.win.destroy()
        except Exception as e:
            messagebox.showerror("Save error", str(e), parent=self.win)


# ── Main App ──────────────────────────────────────────────────────────────────

class App:
    def __init__(self, root):
        self.root = root
        root.title("MEP Parser -- DXF to 3D BIM")
        root.geometry("820x660")
        self.data = None          # parsed result dict
        self.geom_path = None     # path to saved geometry.json

        self.v_dxf = tk.StringVar()
        self.v_map = tk.StringVar(value=user_csv("layer_map.csv"))
        self.v_block = tk.StringVar(value=user_csv("block_map.csv"))
        self.v_llm = tk.BooleanVar(value=bool(os.environ.get("ANTHROPIC_API_KEY")))
        self.v_vision = tk.BooleanVar(value=False)  # Vision 폴백(실험적, 기본 OFF)
        self.v_connect = tk.BooleanVar(value=True)  # IFC 표준 벽 접합(코너 마이터)
        self.v_schedule = tk.StringVar()  # 외부 창호일람 Excel 경로(선택)

        self._build_file_row()
        self._build_buttons()
        self._build_review()
        self._build_log()
        self._log(f"FreeCAD: {find_freecadcmd() or 'not found (build disabled)'}")

    # ── UI builders ───────────────────────────────────────────
    def _build_file_row(self):
        f = ttk.LabelFrame(self.root, text="1) File selection")
        f.pack(fill="x", padx=8, pady=6)
        rows = [("DXF drawing", self.v_dxf, self._pick_dxf),
                ("Layer map", self.v_map, lambda: self._pick_csv(self.v_map)),
                ("Block map", self.v_block, lambda: self._pick_csv(self.v_block))]
        for i, (lbl, var, cmd) in enumerate(rows):
            ttk.Label(f, text=lbl, width=12).grid(row=i, column=0, sticky="w", padx=4, pady=2)
            ttk.Entry(f, textvariable=var, width=74).grid(row=i, column=1, padx=4)
            ttk.Button(f, text="Browse", command=cmd).grid(row=i, column=2, padx=2)
            if lbl == "Layer map":
                ttk.Button(f, text="Edit",
                           command=self._open_layer_editor).grid(row=i, column=3, padx=2)

    def _build_buttons(self):
        f = ttk.Frame(self.root)
        f.pack(fill="x", padx=8)
        ttk.Button(f, text="(1) Scan drawing", command=self._do_scan).pack(side="left", padx=4)
        ttk.Button(f, text="(2) Parse -> geometry.json", command=self._do_parse).pack(side="left", padx=4)
        ttk.Button(f, text="(2b) 누락 진단",
                   command=self._do_diag).pack(side="left", padx=4)
        ttk.Button(f, text="(3) 3D 미리보기(브라우저)",
                   command=self._do_preview).pack(side="left", padx=4)
        self.btn_ifc = ttk.Button(f, text="(4) IFC 빌드 (FreeCAD 불필요)",
                                  command=self._do_ifc_build)
        self.btn_ifc.pack(side="left", padx=4)
        self.btn_build = ttk.Button(f, text="(4b) 3D Build (FreeCAD)", command=self._do_build)
        self.btn_build.pack(side="left", padx=4)
        if find_freecadcmd() is None:
            self.btn_build.state(["disabled"])
        ttk.Button(f, text="(5) 물량 Excel",
                   command=self._do_boq).pack(side="left", padx=4)
        ttk.Button(f, text="창호일람 Excel↓",
                   command=self._do_schedule_export).pack(side="left", padx=4)
        ttk.Button(f, text="창호일람 불러오기↑",
                   command=self._do_schedule_pick).pack(side="left", padx=4)
        ttk.Button(f, text="DWG->DXF checklist",
                   command=self._show_checklist).pack(side="right", padx=4)
        ttk.Checkbutton(f, text="벽 접합(코너)",
                        variable=self.v_connect).pack(side="right", padx=2)
        ttk.Checkbutton(f, text="Vision fallback",
                        variable=self.v_vision).pack(side="right", padx=2)
        ttk.Checkbutton(f, text="AI auto-classify",
                        variable=self.v_llm).pack(side="right", padx=2)

    def _build_review(self):
        f = ttk.LabelFrame(self.root,
                           text="(3) Items needing review (needs_review) -- edit and click [Apply]")
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
        ttk.Label(e, text="Width/diameter (mm)").pack(anchor="w")
        self.v_w = tk.StringVar()
        ttk.Entry(e, textvariable=self.v_w, width=12).pack(anchor="w", pady=2)
        ttk.Label(e, text="Height (mm)").pack(anchor="w")
        self.v_h = tk.StringVar()
        ttk.Entry(e, textvariable=self.v_h, width=12).pack(anchor="w", pady=2)
        ttk.Button(e, text="Apply & Save", command=self._apply_review).pack(anchor="w", pady=6)

    def _build_log(self):
        f = ttk.LabelFrame(self.root, text="Log")
        f.pack(fill="both", padx=8, pady=6)
        self.txt = tk.Text(f, height=9, wrap="none")
        self.txt.pack(fill="both", expand=True, padx=4, pady=4)

    # ── Actions ───────────────────────────────────────────────
    def _log(self, msg):
        self.txt.insert("end", str(msg) + "\n")
        self.txt.see("end")

    def _pick_dxf(self):
        p = filedialog.askopenfilename(
            filetypes=[("도면 (DXF/DWG)", "*.dxf *.dwg"),
                       ("DXF", "*.dxf"), ("DWG", "*.dwg"), ("All", "*.*")])
        if p:
            self.v_dxf.set(p)

    def _ensure_dxf(self):
        """선택 파일 검증 + DWG 면 ODA 로 자동 변환(dwg_converter). 실패 시 None."""
        path = self.v_dxf.get().strip()
        if not path or not os.path.exists(path):
            messagebox.showwarning("Check", "도면(DXF/DWG)을 먼저 선택하세요.")
            return None
        if path.lower().endswith(".dwg"):
            try:
                from dwg_converter import ensure_dxf
                path = ensure_dxf(path, log=self._log)
            except RuntimeError as e:
                messagebox.showerror("DWG 변환", str(e))
                return None
        return path

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

    def _open_layer_editor(self):
        """Open the layer map editor popup."""
        csv_path = self.v_map.get().strip()
        if not csv_path:
            csv_path = user_csv("layer_map.csv")
            self.v_map.set(csv_path)
        unmapped = []
        if self.data:
            unmapped = [s for s in self.data.get("suggestions", [])]
        LayerMapEditor(self.root, csv_path, unmapped_suggestions=unmapped)

    def _show_checklist(self):
        win = tk.Toplevel(self.root)
        win.title("DWG -> DXF export checklist")
        win.geometry("640x560")
        txt = tk.Text(win, wrap="word", font=("Consolas", 9))
        sb = ttk.Scrollbar(win, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(fill="both", expand=True, padx=6, pady=6)
        txt.insert("end", P.DWG_DXF_CHECKLIST)
        txt.configure(state="disabled")

    def _do_scan(self):
        dxf = self._ensure_dxf()
        if not dxf:
            return
        self._log("Scanning...")
        self._set_buttons("disabled")

        def run():
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    P.scan(dxf)
                self.root.after(0, lambda: (self._log(buf.getvalue()),
                                            self._set_buttons("!disabled")))
            except Exception as e:
                self.root.after(0, lambda msg=str(e): (
                    self._log(f"[Error] Scan failed: {msg}"),
                    self._set_buttons("!disabled")))
        threading.Thread(target=run, daemon=True).start()

    def _do_parse(self):
        dxf = self._ensure_dxf()
        if not dxf:
            return
        rules, brules = self._rules()
        use_ai = bool(self.v_llm.get())
        use_vision = bool(self.v_vision.get())
        if use_ai:
            self._log("  [AI] 텍스트 분류 + 고신뢰 자동적용 활성")
        if use_vision:
            self._log("  [Vision] 저신뢰 레이어 이미지 분류 폴백 활성")
        self._log("Parsing...")
        self._set_buttons("disabled")

        # 외부 창호일람 Excel(선택) 로드 → 평면도와 함께 먹여 창/문 배치
        ext_sched = None
        sched_path = self.v_schedule.get().strip()
        if sched_path and os.path.exists(sched_path):
            try:
                from schedule_io import load_schedule_xlsx
                ext_sched = load_schedule_xlsx(sched_path)
                self._log(f"  [창호일람] Excel {len(ext_sched)}행 로드: {os.path.basename(sched_path)}")
            except Exception as e:
                self._log(f"  [창호일람] Excel 로드 실패(무시): {e}")

        def run():
            try:
                data = P.parse(dxf, rules, brules,
                               use_ai=use_ai, use_vision=use_vision,
                               ext_schedule=ext_sched)
                self.root.after(0, lambda: self._parse_done(data, dxf))
            except Exception as e:
                msg = str(e)
                self.root.after(0, lambda m=msg: (
                    self._log(f"[Error] Parse failed: {m}"),
                    self._set_buttons("!disabled")))
        threading.Thread(target=run, daemon=True).start()

    def _do_schedule_pick(self):
        """외부 창호일람 Excel 선택 → 다음 파싱에 사용."""
        p = filedialog.askopenfilename(
            title="창호일람 Excel 선택",
            filetypes=[("Excel", "*.xlsx"), ("All", "*.*")])
        if p:
            self.v_schedule.set(p)
            self._log(f"창호일람 Excel 지정: {p} (다음 'Parse'부터 적용)")

    def _do_schedule_export(self):
        """현재 파싱된 창호일람(window_schedule)을 Excel 양식으로 저장.
        파싱 전이면 빈 양식 생성."""
        try:
            from schedule_io import export_schedule_xlsx
        except Exception as e:
            messagebox.showwarning("Excel", f"openpyxl 필요: {e}")
            return
        sched = (self.data or {}).get("window_schedule", []) if self.data else []
        base = self.geom_path or self.v_dxf.get().strip() or os.path.join(HERE, "창호일람")
        default = os.path.splitext(base)[0] + "_창호일람.xlsx"
        p = filedialog.asksaveasfilename(
            title="창호일람 Excel 저장", defaultextension=".xlsx",
            initialfile=os.path.basename(default),
            filetypes=[("Excel", "*.xlsx")])
        if not p:
            return
        try:
            export_schedule_xlsx(sched, p)
            self._log(f"창호일람 Excel 저장({len(sched)}행) -> {p}"
                      + ("  (빈 양식 — 먼저 Parse 하면 추출본이 채워집니다)" if not sched else ""))
            try:
                os.startfile(os.path.dirname(p) or ".")
            except Exception:
                pass
        except Exception as e:
            messagebox.showerror("Excel", f"저장 실패: {e}")

    def _set_buttons(self, state):
        """스캔·파싱 진행 중 버튼 비활성화(GUI 반응 보장)."""
        for w in self.root.winfo_children():
            try:
                for btn in w.winfo_children():
                    if isinstance(btn, ttk.Button):
                        btn.state([state])
            except Exception:
                pass

    def _parse_done(self, data, dxf):
        """parse() 완료 후 메인 스레드에서 UI 업데이트."""
        self.data = data
        self._set_buttons("!disabled")
        self.geom_path = os.path.splitext(dxf)[0] + ".geometry.json"
        self._save()
        el = self.data["elements"]
        wp = self.data.get("wall_pairing", {})
        bk = self.data.get("blocks", {})
        self._log(f"Parse complete -> {self.geom_path}")
        self._log(f"  walls={len(el['wall'])} columns={len(el['column'])} "
                  f"slabs={len(el['slab'])} zones={len(el['zone'])} openings={len(el['opening'])}")
        self._log(f"  wall pairs: paired={wp.get('paired',0)} single={wp.get('single',0)} | "
                  f"blocks {bk.get('inserts',0)} (unmapped {bk.get('unmapped',0)})")
        for w in self.data.get("warnings", []):
            self._log(f"  [warn] {w}")
        sugg = self.data.get("suggestions", [])
        applied = [s for s in sugg if s.get("applied")]
        if applied:
            self._log(f"  [AI 자동적용 {len(applied)}건]:")
            for s in applied:
                self._log(f"    {s.get('source')} '{s['layer']}'x{s.get('applied_count')} "
                          f"-> {s.get('final_guess')}"
                          + (f"/{s.get('final_subtype')}" if s.get('final_subtype') else "")
                          + f" ({s.get('final_confidence')}, {s.get('decided_by')})")
        remain = [s for s in sugg if not s.get("applied")]
        if remain:
            self._log(f"  [{len(remain)} unmapped — [Edit] layer_map]:")
        for s in remain:
            g = f"geom={s['geom_guess']}({s['geom_confidence']})" if s.get("geom_guess") else "geom=?"
            nm = (f"name~{s['name_match']}->{s['name_guess']}({s['name_score']})"
                  if s.get("name_guess") else "name=?")
            llm = (f" [LLM->{s['llm_guess']}({s['llm_confidence']})]"
                   if s.get("llm_guess") else "")
            vis = (f" [Vision->{s['vision_guess']}({s['vision_confidence']})]"
                   if s.get("vision_guess") else "")
            self._log(f"  [suggest] '{s['layer']}'x{s['count']}: {g} {nm}{llm}{vis}")
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
                                             wd if wd is not None else "(default)",
                                             el.get("confidence", "-")))
                    n += 1
        self._log(f"Review items: {n}" + ("" if n else " -- all auto-detected OK"))

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
            messagebox.showinfo("Info", "Select an item from the list first.")
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
            messagebox.showwarning("Check", "Numbers only.")
            return
        el["needs_review"] = False
        self._save()
        self._log(f"[Applied] {cat}[{idx}] overrides={ov} -> saved")
        self._populate_review()

    def _save(self):
        if self.geom_path and self.data:
            with open(self.geom_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)

    def _do_preview(self):
        """FreeCAD 없이 브라우저로 즉석 3D 미리보기(preview.py 재사용).
        파싱 결과(self.data)를 자립 HTML 로 만들고 기본 브라우저로 연다."""
        if not self.data:
            messagebox.showwarning("Check", "먼저 (2) Parse 를 실행하세요.")
            return
        try:
            import webbrowser
            import preview as PV
            html = PV.build_html(self.data)
            base = self.geom_path or os.path.join(HERE, "preview")
            out = os.path.splitext(base)[0] + "_preview.html"
            with open(out, "w", encoding="utf-8") as f:
                f.write(html)
            self._log(f"3D 미리보기 생성 -> {out} (브라우저에서 열림)")
            self._log("  요소 클릭 → 카테고리/치수 수정 → edits.json 다운로드 → "
                      "재파싱 시 --edits 로 적용됨")
            webbrowser.open("file://" + os.path.abspath(out))
        except Exception as e:
            messagebox.showerror("Preview 실패", str(e))

    def _do_diag(self):
        """벽 누락 진단 오버레이 PNG(diag_overlay.py 재사용) 생성 후 열기.
        회색=원본 도면 / 파랑=paired / 주황=single_offset / 빨강=미커버(누락 의심)."""
        if not self.geom_path or not os.path.exists(self.geom_path):
            messagebox.showwarning("Check", "먼저 (2) Parse 를 실행하세요.")
            return
        try:
            import diag_overlay as DG
            out, qa = DG.build_overlay(self.geom_path)
            cov = qa.get("face_coverage_pct", "?")
            self._log(f"누락 진단 -> {out}")
            self._log(f"  면선커버 {cov}% | 미커버 면선 {qa.get('uncovered_count', '?')}개 "
                      f"(빨간 선 = 벽 생성 실패 구간 — 레이어 매핑/치수 검토)")
            for u in (qa.get("uncovered") or [])[:5]:
                self._log(f"    - {u['layer']}  {u['length_mm']:.0f}mm  @({u['p1'][0]:.0f},{u['p1'][1]:.0f})")
            os.startfile(out)
        except ImportError as e:
            messagebox.showerror("진단 불가", f"matplotlib 필요: pip install matplotlib\n({e})")
        except Exception as e:
            messagebox.showerror("진단 실패", str(e))

    def _do_boq(self):
        """물량집계(BOQ) Excel 내보내기 — boq_export.py 재사용.
        벽(두께별 길이·면적·체적)/기둥(단면별)/슬래브/창호/MEP 규격별 집계."""
        if not self.data:
            messagebox.showwarning("Check", "먼저 (2) Parse 를 실행하세요.")
            return
        try:
            import boq_export as BQ
            base = self.geom_path or self.v_dxf.get().strip() or os.path.join(HERE, "물량")
            out = os.path.splitext(base)[0].replace(".geometry", "") + "_물량.xlsx"
            BQ.export_boq_xlsx(self.data, out)
            secs = BQ.aggregate(self.data)
            summary = " | ".join(
                f"{name} {tot[1]}" for name, (_, rows, tot) in secs.items()
                if rows and isinstance(tot[1], int))
            self._log(f"물량집계 Excel -> {out}")
            self._log(f"  {summary}")
            os.startfile(out)
        except ImportError:
            messagebox.showerror("물량 Excel 불가", "openpyxl 필요: pip install openpyxl")
        except PermissionError:
            messagebox.showerror("물량 Excel", "출력 파일이 Excel 에서 열려 있습니다. 닫고 다시 시도하세요.")
        except Exception as e:
            messagebox.showerror("물량 Excel 실패", str(e))

    def _do_ifc_build(self):
        """IfcOpenShell 로 geometry.json → .ifc 빌드 (FreeCAD 불필요).
        저장된 geometry.json(수정 보존)에서 빌드. 백그라운드 스레드로 GUI 반응 유지."""
        if not self.geom_path or not os.path.exists(self.geom_path):
            messagebox.showwarning("Check", "먼저 (2) Parse 를 실행하세요.")
            return
        try:
            import ifc_builder  # noqa: F401
        except ImportError:
            messagebox.showerror("IFC 빌드 불가",
                                 "ifcopenshell 미설치.\n  pip install ifcopenshell numpy")
            return
        out = os.path.splitext(self.geom_path)[0].replace(".geometry", "") + ".ifc"
        self.btn_ifc.state(["disabled"])
        self._log(f"IFC 빌드 시작 (FreeCAD 불필요) → {out}")

        connect = bool(self.v_connect.get())
        if connect:
            self._log("  [표준 접합] 맞닿는 벽 연결 → 코너 마이터 자동 생성")

        def run():
            try:
                import ifc_builder as IB
                stats = IB.build(self.geom_path, out, storey="Level", connect=connect)
                self.root.after(0, lambda: self._ifc_done(out, stats))
            except Exception as e:
                msg = str(e)
                self.root.after(0, lambda m=msg: (
                    self._log(f"[오류] IFC 빌드 실패: {m}"),
                    self.btn_ifc.state(["!disabled"])))
        threading.Thread(target=run, daemon=True).start()

    def _ifc_done(self, out, stats):
        ok = os.path.exists(out)
        if ok:
            sz = os.path.getsize(out)
            self._log(f"✅ IFC 빌드 완료: {out} ({sz//1024}KB)")
            self._log(f"   walls={stats.get('wall',0)} columns={stats.get('column',0)} "
                      f"slabs={stats.get('slab',0)} (skipped {stats.get('skip',0)})")
            self._log("   → Revit/ArchiCAD/BlenderBIM 또는 (3) 3D 미리보기 로 확인")
        else:
            self._log("[오류] IFC 파일 생성 실패")
        self.btn_ifc.state(["!disabled"])

    def _do_build(self):
        if not self.geom_path or not os.path.exists(self.geom_path):
            messagebox.showwarning("Check", "Please parse first.")
            return
        fc = find_freecadcmd()
        if not fc:
            messagebox.showerror("FreeCAD not found", "freecadcmd.exe not found.")
            return
        out = os.path.splitext(self.geom_path)[0].replace(".geometry", "") + "_model"
        env = dict(os.environ, MEP_GEOMETRY=self.geom_path, MEP_OUT=out,
                   PYTHONIOENCODING="utf-8")
        self.btn_build.state(["disabled"])
        self._log(f"Build started... (freecadcmd) -> {out}.FCStd / .ifc")

        builder_py = resource_path("freecad_builder.py")
        build_cwd = os.path.dirname(builder_py)

        def run():
            try:
                r = subprocess.run([fc, builder_py],
                                   cwd=build_cwd, env=env, capture_output=True,
                                   text=True, encoding="utf-8", errors="replace",
                                   timeout=900)
                self.root.after(0, lambda: self._build_done(r, out))
            except Exception as e:
                # Python 3: 람다에서 except 변수 참조 시 소멸 → 명시적 캡처
                _msg = str(e) or repr(type(e))
                self.root.after(0, lambda msg=_msg: (
                    self._log(f"[Error] Build failed: {msg}"),
                    self.btn_build.state(["!disabled"])))
        threading.Thread(target=run, daemon=True).start()

    def _build_done(self, r, out):
        import shutil
        # stdout 파싱: FCSTD_TMP/FCSTD_DST 마커로 임시파일 → 최종경로 이동
        fcstd_tmp = fcstd_dst = ifc_tmp = ifc_dst = None
        for line in (r.stdout or "").splitlines():
            if line.startswith("FCSTD_TMP:"):
                fcstd_tmp = line[len("FCSTD_TMP:"):].strip()
            elif line.startswith("FCSTD_DST:"):
                fcstd_dst = line[len("FCSTD_DST:"):].strip()
            elif line.startswith("IFC_TMP:"):
                ifc_tmp = line[len("IFC_TMP:"):].strip()
            elif line.startswith("IFC_DST:"):
                ifc_dst = line[len("IFC_DST:"):].strip()
            else:
                self._log("  " + line)

        # FCStd 이동
        if fcstd_tmp and fcstd_dst and os.path.exists(fcstd_tmp):
            try:
                os.makedirs(os.path.dirname(fcstd_dst) or ".", exist_ok=True)
                shutil.move(fcstd_tmp, fcstd_dst)
                self._log(f"  [저장] {fcstd_dst}")
            except Exception as _me:
                self._log(f"  [오류] 파일 이동 실패: {_me}")
                self._log(f"  임시경로: {fcstd_tmp}")

        # IFC 이동
        if ifc_tmp and ifc_dst and os.path.exists(ifc_tmp):
            try:
                shutil.move(ifc_tmp, ifc_dst)
                self._log(f"  [저장] {ifc_dst}")
            except Exception as _me:
                self._log(f"  [warn] IFC 이동 실패: {_me}")

        if r.returncode != 0 and r.stderr:
            self._log("[stderr] " + r.stderr.strip()[:500])

        ok = os.path.exists(fcstd_dst or (out + ".FCStd"))
        self._log(f"Build {'complete' if ok else 'FAILED'}: {fcstd_dst or out + '.FCStd'}"
                  + (" ✓ IFC" if ifc_dst and os.path.exists(ifc_dst) else ""))
        self.btn_build.state(["!disabled"])


def _selftest():
    """헤드리스 자가검증(번들 .exe 스모크 테스트용). GUI 창 없이:
    동봉 리소스 해석 → 샘플 DXF 파싱 → 미리보기 HTML 생성까지 확인 후 종료.
    결과/트레이스백을 selftest_result.txt 에도 기록(windowed exe 는 stdout 없음).
    번들 누락(모듈/리소스)이 있으면 비정상 종료(exit 1)로 드러난다."""
    import traceback
    base = (os.path.dirname(sys.executable)
            if getattr(sys, "frozen", False) else HERE)
    logf = os.path.join(base, "selftest_result.txt")
    try:
        import preview as PV
        sample = resource_path("sample_plan.dxf")
        rules = P.load_layer_map(resource_path("layer_map.csv"))
        brules = P.load_layer_map(resource_path("block_map.csv"))
        data = P.parse(sample, rules, brules)
        n = sum(len(v) for v in data["elements"].values())
        html = PV.build_html(data)
        offline = "data:text/javascript;base64" in html
        msg = (f"[selftest] parse OK: elements={n}, "
               f"shapely={'on' if P.HAS_SHAPELY else 'off'}\n"
               f"[selftest] preview OK: html={len(html)} bytes, "
               f"offline_three={offline}\n[selftest] PASS\n")
        rc = 0
    except Exception:
        msg = "[selftest] FAIL\n" + traceback.format_exc()
        rc = 1
    try:
        with open(logf, "w", encoding="utf-8") as f:
            f.write(msg)
    except Exception:
        pass
    print(msg)
    return rc


def _guard_std_streams():
    """PyInstaller windowed(.exe, --noconsole) 모드에서 sys.stdout/stderr 가 None.
    파서 등 코드 곳곳의 print() 가 None 에 쓰다 크래시 → 버퍼로 치환해 무력화."""
    if sys.stdout is None:
        sys.stdout = io.StringIO()
    if sys.stderr is None:
        sys.stderr = io.StringIO()


def main():
    _guard_std_streams()
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
