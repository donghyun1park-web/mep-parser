"""
mep_gui.py  --  [Phase 2.5] GUI shell + edit loop
Double-click (run_gui.bat) -> file select -> scan -> parse -> review -> 3D build.

Design:
- Zero new dependencies (tkinter = Python stdlib). Engine reuses dxf_parser module.
- Review changes persist by EID in a revisioned project beside the source drawing.
  Preview, GUI and MCP share canonical edits; builds reparse the latest saved revision.
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
from project_server import ProjectSession, open_source_project, verified_artifacts
from project_store import ProjectStore, ProjectCorrupt, RevisionConflict, atomic_json

HERE = os.path.dirname(os.path.abspath(__file__))


def resource_path(rel):
    """동봉 리소스(읽기 전용) 경로. PyInstaller onefile 이면 sys._MEIPASS(임시 추출
    dir), 아니면 소스 디렉터리. layer_map/block_map/freecad_builder.py/vendor 용."""
    base = getattr(sys, "_MEIPASS", None) or HERE
    return os.path.join(base, rel)


def insert_layer_rule_first(csv_path, row):
    """layer_map 헤더 바로 아래에 규칙 한 줄을 넣는다. 규칙은 **선매칭 우선**이라 끝에 붙이면
    'COL|기둥' 같은 넓은 규칙에 가려져 아무 일도 안 난다 — 파서 경고가 'COL 행 위에' 라고 하는 이유."""
    with open(csv_path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("pattern,"):
            lines.insert(i + 1, row)
            break
    else:
        raise ValueError(f"layer_map 헤더(pattern,…)가 없습니다: {csv_path}")
    with open(csv_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


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


# dxf_parser.VALID_CATEGORIES 와 같아야 한다. 빠진 카테고리는 편집기에서
# 표현조차 안 되고, 저장 시 그 규칙이 사라진다.
CATEGORIES = ["wall", "column", "slab", "beam", "zone", "opening",
              "pipe", "duct", "tray", "equipment", "ignore"]


def guess_drawing_kind(dxf_path):
    """레이어 **이름만** 보고 건축/설비를 짚는다 — 물어보는 대화의 기본 선택을 채울 뿐이다.

    ★ 판정이 아니라 추측이다. 엔티티를 순회하지 않고 레이어 테이블만 읽는다(즉시).
      규칙은 둘뿐이고 둘 다 `DEFAULT_LAYER_RULES` 를 그대로 쓴다(이름 규칙을 새로 만들지 않는다):
      ① 설비 이름(PIPE·DUCT·TRAY…)이 하나라도 있으면 설비다.
      ② 벽·기둥 이름이 **하나도 없으면** 해석할 건축이 없다 — 설비로 짚는다. 실측(단위세대 환기):
         배경 건축이 XREF 라 벽 레이어 이름이 없고, 그냥 해석하면 배경이 기둥 43개로 잡힌다.
      읽지 못하면 종전 기본 경로인 건축으로 둔다.
    """
    try:
        import ezdxf
        names = [layer.dxf.name for layer in ezdxf.readfile(dxf_path).layers]
    except Exception:
        return "건축"
    kinds = {P.classify(name, P.DEFAULT_LAYER_RULES)[0] for name in names}
    if kinds & {"pipe", "duct", "tray", "equipment"}:
        return "설비"
    return "건축" if kinds & {"wall", "column"} else "설비"


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

CSV_FIELDS = ["pattern", "category", "width", "height", "thickness", "opts"]


def _read_csv_rows(csv_path):
    """layer_map.csv → [{...CSV_FIELDS, "_before": [원문 주석줄]}]

    ★ `opts` 와 주석을 **반드시 왕복**시킨다. 종전 구현은 5컬럼만 읽고 5컬럼만 써서,
    편집기에서 '저장' 을 누르면 `pair_max`·`pair_min`·`from=dim`·`member_re`·
    `schedule=`·`material=` 과 주석이 통째로 사라졌다 — 사용자가 알 방법이 없었다.
    (파서가 이번에 넣은 `row.get(None) → LayerMapError` 가드도 이건 못 잡는다.
     쓰기가 자기 일관적이라 5컬럼 파일로 조용히 로드되기 때문이다.)

    주석은 **바로 뒤 규칙에 붙여** 두었다가 저장 시 그 앞에 다시 쓴다 — 주석은
    보통 다음 규칙을 설명하므로 그게 의미를 지킨다.
    """
    rows, pending, seen_header = [], [], False
    if not os.path.exists(csv_path):
        return rows
    with open(csv_path, encoding="utf-8") as f:
        for ln in f.read().splitlines():
            t = ln.strip()
            if not t or t.startswith("#"):
                pending.append(ln)
                continue
            vals = next(csv.reader([ln]))
            if not seen_header:
                seen_header = True          # 헤더 줄
                continue
            r = {k: (vals[i].strip() if i < len(vals) else "")
                 for i, k in enumerate(CSV_FIELDS)}
            r["_before"], pending = pending, []
            rows.append(r)
    if pending and rows:                    # 파일 끝 주석
        rows[-1]["_after"] = pending
    return rows


def _write_csv_rows(csv_path, rows):
    """rows → layer_map.csv. `opts`·주석을 읽은 그대로 되돌려 놓는다."""
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_FIELDS)
        for r in rows:
            for c in r.get("_before") or []:
                print(c, file=f)
            w.writerow([r.get(k, "") for k in CSV_FIELDS])
            for c in r.get("_after") or []:
                print(c, file=f)


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

        cols = tuple(CSV_FIELDS)
        self.tree = ttk.Treeview(top, columns=cols, show="headings", height=10)
        col_widths = (200, 85, 60, 60, 70, 200)
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

        fields = [("Pattern (regex)", 24), ("Category", 12), ("Width mm", 7),
                  ("Height mm", 7), ("Thickness mm", 9), ("opts (k=v;k=v)", 24)]
        self.v_pat = tk.StringVar()
        self.v_cat = tk.StringVar(value="column")
        self.v_wid = tk.StringVar()
        self.v_hei = tk.StringVar()
        self.v_thk = tk.StringVar()
        self.v_opt = tk.StringVar()
        vars_ = [self.v_pat, self.v_cat, self.v_wid, self.v_hei, self.v_thk, self.v_opt]

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
                             values=tuple(r.get(k, "") for k in CSV_FIELDS))

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
        self.v_opt.set("")
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
            "opts": self.v_opt.get().strip(),
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
        root.geometry("820x520")
        self.data = None          # parsed result dict
        self.geom_path = None     # path to saved geometry.json
        self.project_session = None
        self.project_server = None
        # 열기 한 번으로 브라우저까지 간다 — 파싱이 끝나면 이 표시를 보고 미리보기를 연다.
        self._open_after_parse = False
        root.protocol("WM_DELETE_WINDOW", self._close)

        self.v_dxf = tk.StringVar()
        self.v_map = tk.StringVar(value=user_csv("layer_map.csv"))
        self.v_block = tk.StringVar(value=user_csv("block_map.csv"))
        self.v_llm = tk.BooleanVar(value=bool(os.environ.get("ANTHROPIC_API_KEY")))
        self.v_vision = tk.BooleanVar(value=False)  # Vision 폴백(실험적, 기본 OFF)
        self.v_connect = tk.BooleanVar(value=True)  # IFC 표준 벽 접합(코너 마이터)
        self.v_schedule = tk.StringVar()  # 외부 창호일람 Excel 경로(선택)
        self.v_edits = tk.StringVar()     # preview 에서 받은 edits.json 경로(선택)

        # 첫 화면은 넷뿐이다. 나머지는 '그 밖의 도구' 안에 접혀 있다(지우지 않았다).
        self._build_main_bar()
        self._build_tools()
        self._build_log()
        self._log(f"FreeCAD: {find_freecadcmd() or 'not found (build disabled)'}")

    # ── UI builders ───────────────────────────────────────────
    def _build_main_bar(self):
        f = ttk.Frame(self.root)
        f.pack(fill="x", padx=8, pady=8)
        ttk.Button(f, text="열기…", command=self._do_open).pack(side="left", padx=4)
        ttk.Button(f, text="설비 도면 설정", command=self._do_mep_setup).pack(side="left", padx=4)
        self.btn_export = ttk.Menubutton(f, text="내보내기 ▾")
        menu = tk.Menu(self.btn_export, tearoff=0)
        for label, command in [("검토 목록 CSV", self._do_review_csv),
                               ("물량 Excel", self._do_boq),
                               ("창호일람 Excel 내보내기", self._do_schedule_export),
                               ("IFC 직접 내보내기", self._do_ifc_build),
                               ("납품 검증 빌드 (FreeCAD · 느림)", self._do_build),
                               ("Blender / GLB", self._do_blender_build)]:
            menu.add_command(label=label, command=command)
        if find_freecadcmd() is None:
            menu.entryconfigure("납품 검증 빌드 (FreeCAD · 느림)", state="disabled")
        self.btn_export["menu"] = menu
        self.btn_export.pack(side="left", padx=4)
        self.v_tools = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="그 밖의 도구 ▾", variable=self.v_tools,
                        command=self._toggle_tools).pack(side="left", padx=4)

    def _build_tools(self):
        """일회성·상황별 도구. 만들어 두고 접어 둔다 — 기능은 하나도 줄이지 않았다."""
        self.tools = ttk.LabelFrame(self.root, text="그 밖의 도구")
        self._build_file_row(self.tools)
        self._build_buttons(self.tools)
        self._build_review(self.tools)

    def _toggle_tools(self):
        if self.v_tools.get():
            self.tools.pack(fill="both", expand=True, padx=8, pady=6, before=self.log_frame)
        else:
            self.tools.pack_forget()

    def _build_file_row(self, parent=None):
        f = ttk.LabelFrame(parent or self.root, text="1) File selection")
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

    def _build_buttons(self, parent=None):
        f = ttk.Frame(parent or self.root)
        f.pack(fill="x", padx=8)
        ttk.Button(f, text="프로젝트 열기", command=self._open_project).pack(side="left", padx=4)
        ttk.Button(f, text="(1) Scan drawing", command=self._do_scan).pack(side="left", padx=4)
        ttk.Button(f, text="도면 단위 확인", command=lambda: self._do_mep_setup(units_only=True)).pack(side="left", padx=4)
        ttk.Button(f, text="설비 도면 설정 · Codex 제안", command=self._do_mep_setup).pack(side="left", padx=4)
        ttk.Button(f, text="같은 층 도면 추가(설비)", command=self._do_add_source).pack(side="left", padx=4)
        ttk.Button(f, text="(2) 다시 해석", command=self._do_parse).pack(side="left", padx=4)
        ttk.Button(f, text="(2b) 누락 진단",
                   command=self._do_diag).pack(side="left", padx=4)
        ttk.Button(f, text="(3) 3D 미리보기(브라우저)",
                   command=self._do_preview).pack(side="left", padx=4)
        ttk.Button(f, text="(3b) 검토 목록 CSV",
                   command=self._do_review_csv).pack(side="left", padx=4)
        ttk.Button(f, text="edits.json 불러오기",
                   command=self._pick_edits).pack(side="left", padx=4)
        self.btn_ifc = ttk.Button(f, text="선택: IFC 직접 내보내기",
                                  command=self._do_ifc_build)
        # FreeCAD is the primary build action; direct IFC remains optional.
        # 일상 검토는 (2)→(3b) 에서 끝난다. FreeCAD 불리언은 같은 자리를 훨씬 느리게 다시 찾으므로
        # 납품 검증용이라고 이름으로 말한다(실측 통합 빌드 421초 대 2.5D 목록 0.1초).
        self.btn_build = ttk.Button(f, text="(4) 납품 검증 빌드 (FreeCAD · 느림)", command=self._do_build)
        self.btn_build.pack(side="left", padx=4)
        ttk.Button(f, text="Blender / GLB 내보내기", command=self._do_blender_build).pack(side="left", padx=4)
        self.btn_ifc.pack(side="left", padx=4)
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
        # Keep the primary build and project actions reachable at the default window size.
        controls = f.winfo_children()
        for control in controls:
            control.pack_forget()
        for i, control in enumerate(controls):
            control.grid(row=i // 5, column=i % 5, sticky="w", padx=4, pady=3)

    def _build_review(self, parent=None):
        f = ttk.LabelFrame(parent or self.root,
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
        self.v_ack = tk.BooleanVar(value=False)
        ttk.Checkbutton(e, text="현재 검토 사유 확인 완료", variable=self.v_ack).pack(anchor="w")
        self.v_h = tk.StringVar()
        ttk.Entry(e, textvariable=self.v_h, width=12).pack(anchor="w", pady=2)
        ttk.Button(e, text="Apply & Save", command=self._apply_review).pack(anchor="w", pady=6)

    def _build_log(self):
        f = self.log_frame = ttk.LabelFrame(self.root, text="Log")
        f.pack(fill="both", expand=True, padx=8, pady=6)
        self.txt = tk.Text(f, height=9, wrap="none")
        self.txt.pack(fill="both", expand=True, padx=4, pady=4)

    # ── Actions ───────────────────────────────────────────────
    def _log(self, msg):
        self.txt.insert("end", str(msg) + "\n")
        self.txt.see("end")

    def _do_open(self):
        """열기 한 번이 길을 정한다 — 건축이면 해석, 설비면 설정, 저장된 프로젝트면 그대로.

        ★ 설비 도면을 그냥 해석하면 **오답이 첫 화면이 된다**: 설비 평면은 배경 건축을 XREF 로
          물고 있어 기본 규칙으로 파싱하면 그 배경이 기둥·벽이 된다(실측: 단위세대 환기 도면에서
          검토 대기 43건이 전부 배경 기둥). 그래서 종류를 **먼저 한 번 묻는다.**
        """
        path = filedialog.askopenfilename(
            title="도면 또는 저장된 프로젝트 열기",
            filetypes=[("도면 · 저장된 프로젝트", "*.dxf *.dwg project.json"),
                       ("도면 (DXF/DWG)", "*.dxf *.dwg"),
                       ("저장된 프로젝트", "project.json"), ("All", "*.*")])
        if not path:
            return
        if os.path.basename(path).lower() == "project.json":
            self._open_after_parse = True
            self._open_project(os.path.dirname(path))
            return
        self.v_dxf.set(path)
        dxf = self._ensure_dxf()          # DWG 면 여기서 DXF 로 바뀐다
        if not dxf:
            return
        kind = self._ask_drawing_kind(guess_drawing_kind(dxf))
        if kind is None:
            return
        self._open_after_parse = True
        # 설비는 파싱하지 않고 설정부터 — 저장하면 그 콜백이 `_parse_done` 을 부른다.
        self._do_mep_setup() if kind == "설비" else self._do_parse()

    def _ask_drawing_kind(self, guess):
        """도면 종류를 묻는다. 추측은 기본 선택만 채우고 **고르는 것은 사람이다.**"""
        win = tk.Toplevel(self.root)
        win.title("도면 종류")
        win.transient(self.root)
        win.resizable(False, False)
        ttk.Label(win, text="이 도면은 무엇입니까?", font=("", 10, "bold")).pack(anchor="w", padx=14, pady=(12, 2))
        ttk.Label(win, justify="left", wraplength=430,
                  text="설비 평면은 배경 건축 도면을 함께 물고 있습니다. 그냥 해석하면 그 배경이 "
                       "기둥·벽으로 잡혀 검토 대기만 수십 건 쌓입니다. 그래서 설비를 고르면 "
                       "어느 레이어가 덕트·배관인지 먼저 정합니다.").pack(anchor="w", padx=14)
        choice = tk.StringVar(value=guess)
        for value, text in (("건축", "건축 평면 — 벽·기둥·슬래브를 바로 해석합니다"),
                            ("설비", "설비 평면 — 난방·환기. 설비 도면 설정을 먼저 엽니다")):
            ttk.Radiobutton(win, text=text, value=value, variable=choice).pack(anchor="w", padx=20, pady=3)
        ttk.Label(win, text=f"레이어 이름으로 '{guess}' 을 짚었습니다. 추측이니 맞는 쪽을 고르세요.",
                  foreground="#555555").pack(anchor="w", padx=14, pady=(2, 4))
        picked = {}
        row = ttk.Frame(win)
        row.pack(fill="x", padx=12, pady=(0, 12))

        def confirm():
            picked["kind"] = choice.get()
            win.destroy()
        ttk.Button(row, text="확인", command=confirm).pack(side="right", padx=4)
        ttk.Button(row, text="취소", command=win.destroy).pack(side="right")
        win.bind("<Return>", lambda _event: confirm())
        win.bind("<Escape>", lambda _event: win.destroy())
        win.grab_set()
        self.root.wait_window(win)
        return picked.get("kind")

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

    def _open_project(self, folder=None):
        folder = folder or filedialog.askdirectory(title="저장된 .mep 프로젝트 폴더 선택")
        if not folder:
            return
        try:
            store = ProjectStore(folder)
            try:
                manifest = store.read()
            except ProjectCorrupt as exc:
                if not messagebox.askyesno("프로젝트 복구", str(exc) + "\n이전 정상 저장본으로 복구할까요?"):
                    return
                manifest = store.recover_backup()
            if self.project_server:
                self.project_server.close()
                self.project_server = None
            self.project_session = ProjectSession(store)
            source = manifest['sources'][0]
            self.v_dxf.set(source['path'])
            self.v_map.set(source.get('layer_map') or '')
            self.v_block.set(source.get('block_map') or '')
            self.v_schedule.set(source.get('schedule') or '')
            self.v_llm.set(bool(source.get('options', {}).get('use_ai')))
            self.v_vision.set(bool(source.get('options', {}).get('use_vision')))
            self.geom_path = str(store.folder / 'geometry.json')
            self._set_buttons('disabled')
            def run():
                try:
                    state = self.project_session.state()
                    self.root.after(0, lambda: self._parse_done(state['geometry'], source['path']))
                except Exception as exc:
                    self.root.after(0, lambda msg=str(exc): (self._log(msg), self._set_buttons('!disabled')))
            threading.Thread(target=run, daemon=True).start()
        except Exception as exc:
            messagebox.showerror("프로젝트 열기 실패", str(exc))

    def _pick_edits(self):
        p = filedialog.askopenfilename(title="edits.json 가져오기", filetypes=[("JSON", "*.json")])
        if not p:
            return
        try:
            if not self.project_session:
                raise ValueError("먼저 도면을 파싱하거나 프로젝트를 여세요.")
            state = self.project_session.store.read()
            self.project_session.import_legacy(p, state['revision'], state['project_id'])
            self._save()
            self._populate_review()
            self._log("수정 가져오기 완료. 원본 사본을 프로젝트 imports 폴더에 보존했습니다.")
        except Exception as exc:
            messagebox.showerror("가져오기 실패", str(exc))

    def _do_parse(self):
        dxf = self._ensure_dxf()
        if not dxf:
            return
        options = dict(use_ai=bool(self.v_llm.get()), use_vision=bool(self.v_vision.get()))
        try:
            same = self.project_session and self.project_session.store.read()['sources'][0]['path'] == os.path.abspath(dxf)
            if not same:
                try:
                    self.project_session = open_source_project(dxf, layer_map=self.v_map.get().strip() or None,
                        block_map=self.v_block.get().strip() or None, options=options,
                        schedule=self.v_schedule.get().strip() or None)
                except ProjectCorrupt as exc:
                    folder = os.path.splitext(dxf)[0] + '.mep'
                    if not messagebox.askyesno("프로젝트 복구", str(exc) + "\n이전 정상 저장본으로 복구할까요?"):
                        return
                    store = ProjectStore(folder)
                    store.recover_backup()
                    self.project_session = ProjectSession(store)
                except OSError:
                    folder = filedialog.askdirectory(title="도면 옆에 저장할 수 없습니다. 프로젝트 저장 폴더 선택")
                    if not folder:
                        return
                    self.project_session = open_source_project(dxf, folder=folder,
                        layer_map=self.v_map.get().strip() or None, block_map=self.v_block.get().strip() or None,
                        options=options, schedule=self.v_schedule.get().strip() or None)
            manifest = self.project_session.store.read()
            sources = manifest['sources']
            source = sources[0]
            if same:
                saved_options = dict(source.get('options') or {})
                saved_options.update(options)
                source.update(layer_map=self.v_map.get().strip() or None, block_map=self.v_block.get().strip() or None,
                              options=saved_options, schedule=self.v_schedule.get().strip() or None)
            else:
                self.v_map.set(source.get('layer_map') or '')
                self.v_block.set(source.get('block_map') or '')
                self.v_schedule.set(source.get('schedule') or '')
                self.v_llm.set(bool(source.get('options', {}).get('use_ai')))
                self.v_vision.set(bool(source.get('options', {}).get('use_vision')))
            if sources != self.project_session.store.read()['sources']:
                self.project_session.store.update_sources(sources, manifest['options'], manifest['revision'], manifest['project_id'])
            if self.project_server:
                self.project_server.close()
                self.project_server = None
            self.geom_path = str(self.project_session.store.folder / 'geometry.json')
        except Exception as exc:
            messagebox.showerror("프로젝트 열기 실패", str(exc))
            return
        self._log("저장된 최신 수정을 적용해 다시 파싱합니다...")
        self._set_buttons("disabled")
        def run():
            try:
                state = self.project_session.state()
                self.root.after(0, lambda: self._parse_done(state['geometry'], dxf))
            except Exception as exc:
                self.root.after(0, lambda msg=str(exc): (self._log(msg), self._set_buttons("!disabled")))
        threading.Thread(target=run, daemon=True).start()
        return True

    def _do_schedule_pick(self):
        """외부 창호일람 Excel 선택 → 다음 파싱에 사용."""
        p = filedialog.askopenfilename(
            title="창호일람 선택 (Excel 양식 또는 창호일람표 DXF)",
            filetypes=[("창호일람", "*.xlsx *.dxf"), ("Excel", "*.xlsx"), ("DXF", "*.dxf"), ("All", "*.*")])
        if p:
            self.v_schedule.set(p)
            self._log(f"창호일람 지정: {p} (다음 'Parse'부터 적용)")
        return p

    def _do_schedule_export(self):
        """현재 파싱된 창호일람(window_schedule)을 Excel 양식으로 저장.
        파싱 전이면 빈 양식 생성."""
        try:
            from schedule_io import export_schedule_xlsx, schedule_with_marks
        except Exception as e:
            messagebox.showwarning("Excel", f"openpyxl 필요: {e}")
            return
        # 평면의 블록 부호(W-1200 · D-900)는 폭·수량이 채워진 채 나간다 — 사용자는 높이·창대높이만 적는다.
        sched = schedule_with_marks((self.data or {}).get("window_schedule", []),
                                    (self.data or {}).get("window_marks", [])) if self.data else []
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
        """스캔·파싱 진행 중 버튼 비활성화(GUI 반응 보장).

        ★ 접힌 '그 밖의 도구' 안의 버튼은 한 겹 더 깊다 — 재귀로 내려가지 않으면 진행 중에도
          눌린다(프레임 구조를 바꿀 때 조용히 깨지는 자리다)."""
        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, (ttk.Button, ttk.Menubutton)):
                    child.state([state])
                else:
                    walk(child)
        for w in self.root.winfo_children():
            try:
                walk(w)
            except Exception:
                pass

    def _parse_done(self, data, dxf):
        """parse() 완료 후 메인 스레드에서 UI 업데이트."""
        self.data = data
        self._set_buttons("!disabled")
        self.geom_path = str(self.project_session.store.folder / "geometry.json")
        atomic_json(self.geom_path, self.data)
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
        clash = (self.data.get("clash_review") or {}).get("summary") or {}
        if clash.get("total") or clash.get("error"):
            from clash_review import LABELS
            kinds = " · ".join(f"{LABELS.get(k, k)} {n}" for k, n in (clash.get("by_kind") or {}).items())
            self._log(f"  [간섭 후보] {clash.get('total', 0)}건 ({kinds}) — 위치는 3D 미리보기 '검토 대기' 맨 앞"
                      + (f" · 가정 높이에 기댄 것 {clash['assumed_basis']}건" if clash.get("assumed_basis") else "")
                      + (f" · 개구부 통과 {clash['through_openings']}건(가정 문턱·높이 {clash.get('through_openings_assumed', 0)})"
                         if clash.get("through_openings") else "")
                      + (f" · 계산 실패: {clash['error']}" if clash.get("error") else ""))
        net = (self.data.get("mep_connectivity") or {}).get("summary") or {}
        if net.get("runs") or net.get("error"):
            # '완전' 은 원본 반영에만 붙인다 — 선택한 원본을 다 담아도 계통은 끊겨 있을 수 있다.
            cov = self.data.get("source_coverage") or {}
            self._log(f"  [설비 연결] 조각 {net.get('runs', 0)} · 이어진 무리 {net.get('groups', 0)} · "
                      f"끊긴 끝 {net.get('open_ends', 0)} · 이음 후보 {net.get('candidates', 0)}"
                      f"(일상 {net.get('routine', 0)} · 모두 확정하면 {net.get('groups_with_candidates', 0)}무리)"
                      + (f" · 가정 높이에 기댄 후보 {(net.get('assumed_basis') or {})['candidates']}건"
                         if (net.get("assumed_basis") or {}).get("candidates") else "")
                      + (f" · 계통 충돌 {net['conflicts']}" if net.get("conflicts") else "")
                      + (f" · 원본 반영 {cov.get('represented')}/{cov.get('selected')}" if cov else "")
                      + (f" · 계산 실패: {net['error']}" if net.get("error") else ""))
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
        # 증거가 있는 제안만 한 줄씩 — 열린 선이라는 것만으로 wall(0.45) 를 20줄 찍던 것은 소음이었다.
        loud = [s for s in remain if s.get("geom_guess") or s.get("name_guess") or s.get("llm_guess") or s.get("vision_guess")]
        quiet = [s for s in remain if s not in loud]
        if remain:
            self._log(f"  [{len(remain)} unmapped — [Edit] layer_map]:")
        for s in loud:
            g = f"geom={s['geom_guess']}({s['geom_confidence']})" if s.get("geom_guess") else "geom=?"
            nm = (f"name~{s['name_match']}->{s['name_guess']}({s['name_score']})"
                  if s.get("name_guess") else "name=?")
            llm = (f" [LLM->{s['llm_guess']}({s['llm_confidence']})]"
                   if s.get("llm_guess") else "")
            vis = (f" [Vision->{s['vision_guess']}({s['vision_confidence']})]"
                   if s.get("vision_guess") else "")
            why = f" — {s['geom_reason']}" if s.get("evidence") and s.get("geom_reason") else ""
            self._log(f"  [suggest] '{s['layer']}'x{s['count']}: {g} {nm}{llm}{vis}{why}")
        if quiet:
            self._log(f"  [벽 아님·형상 없음 {len(quiet)}개] " + " · ".join(s['layer'].split('$')[-1] for s in quiet[:12])
                      + (" · …" if len(quiet) > 12 else ""))
        qa = self.data.get("qa") or {}
        if qa.get("wall_like_unmapped_m"):
            self._log(f"  [QA] 벽으로 보이는 미매핑 선 {qa['wall_like_unmapped_m']}m "
                      f"({', '.join(l.split('$')[-1] for l in qa.get('wall_like_unmapped_layers', []))}) — "
                      f"면선커버 {qa.get('face_coverage_pct', '?')}% 는 벽으로 매핑된 레이어만 센 값이다")
        self._populate_review()
        # 오답이 첫 화면이 되기 전에 묻는다 — 외벽이 기둥 레이어에 있거나 창호 높이가 추정치면 여기서 멈춘다.
        if not self._is_mep_project() and self._prompt_after_parse(dxf):
            return   # 다시 파싱 중 — _parse_done 이 한 번 더 온다
        # '열기…' 로 시작했으면 여기서 브라우저까지 간다 — 버튼을 세 번 누르게 하지 않는다.
        if self._open_after_parse:
            self._open_after_parse = False
            self._do_preview()

    def _is_mep_project(self):
        """설비 프로젝트(프로필이 있는 원본)에는 건축 질문을 하지 않는다 — 배경 XREF 의 A-COL·창호는 그 도면의 부재가 아니다."""
        try:
            return any((s.get('options') or {}).get('mep_profile')
                       for s in self.project_session.store.read()['sources'])
        except Exception:
            return False

    def _prompt_after_parse(self, dxf):
        """파싱 결과가 사용자 결정을 기다리는 두 경우. True 면 재파싱을 시작했다(호출부는 멈춘다).
        1) 기둥 레이어가 벽처럼 그려짐(`column_layers_like_wall`) — 아파트 A-COL 은 200mm 내력벽·외벽이다.
           파서는 재분류하지 않는다(CLAUDE.md). 여기서 사용자가 layer_map 한 줄을 승인한다.
        2) 창호 높이·창대높이가 추정치(`openings_dims_assumed`) — 평면도에 없는 값이라 일람을 받는다.
        같은 도면·같은 질문은 한 세션에 한 번만 묻는다."""
        asked = self.__dict__.setdefault("_prompted", set())
        for ev in self.data.get("column_layers_like_wall") or []:
            key = ("wall_layer", dxf, ev.get("layer"))
            if key in asked or not ev.get("suggested_row"):
                continue
            asked.add(key)
            short = str(ev.get("layer", "")).split("$")[-1]
            csv_path = self.v_map.get().strip() or user_csv("layer_map.csv")   # 어느 파일에 넣는지 대화창에 보인다
            if messagebox.askyesno(
                    "외벽·내력벽이 기둥 레이어에 있습니다",
                    f"'{short}' 레이어는 기둥(column)으로 매핑됐지만 선 {ev.get('lines')}개 중 "
                    f"{ev.get('paired')}개가 {ev.get('spacing_mm')}mm 간격의 평행 짝입니다 — 벽처럼 그린 레이어입니다.\n"
                    f"아파트 단위세대 도면의 A-COL 은 보통 외벽·세대간 내력벽입니다.\n\n"
                    f"{csv_path} 에 다음 한 줄을 넣고 벽으로 다시 해석할까요?\n  {ev['suggested_row']}"):
                try:
                    insert_layer_rule_first(csv_path, ev["suggested_row"])
                except Exception as exc:
                    messagebox.showerror("layer_map 수정 실패", str(exc))
                    return False
                self.v_map.set(csv_path)
                self._log(f"{csv_path} 에 추가: {ev['suggested_row']}  → 다시 해석")
                return bool(self._do_parse())
        assumed = self.data.get("openings_dims_assumed") or {}
        marks = self.data.get("window_marks") or []
        key = ("window_dims", dxf)
        if assumed and marks and key not in asked:
            asked.add(key)
            choice = self._ask_window_dims(marks, max(assumed.values()))
            if choice == "export":
                self._do_schedule_export()
                self._log("양식의 높이·창대높이를 채운 뒤 '창호일람 불러오기↑' 로 다시 해석하세요.")
            elif choice == "load":
                if self._do_schedule_pick():
                    return bool(self._do_parse())
                asked.discard(key)   # 파일 선택을 취소했으면 다음 파싱에 다시 묻는다
        return False

    def _ask_window_dims(self, marks, n_assumed):
        """창호 부호 목록을 보이고 '양식 내보내기 / 일람 불러오기 / 추정치로 계속' 중 하나를 받는다."""
        win = tk.Toplevel(self.root)
        win.title("창호 치수가 필요합니다")
        win.transient(self.root)
        win.grab_set()
        ttk.Label(win, justify="left", text=(
            f"창·문 {n_assumed}개의 높이와 창대높이(슬래브 위 설치 높이)를 평면도에서 읽을 수 없어 추정치를 썼습니다\n"
            "(창 높이 1200·창대 900, 문 높이 2100·문턱 0). 아래 부호별로 값을 주면 그대로 모델에 들어갑니다.")
        ).pack(anchor="w", padx=12, pady=(12, 6))
        box = tk.Text(win, height=min(12, len(marks) + 1), width=56, font=("Consolas", 10))
        box.pack(padx=12)
        kind = {"window": "창", "door": "문"}
        for m in marks:
            box.insert("end", f"{m['mark']:<14}{kind.get(m.get('subtype'), '?'):<4}폭 {m['width']:>5.0f}   ×{m['count']}\n")
        box.config(state="disabled")
        choice = {"v": "continue"}

        def pick(v):
            choice["v"] = v
            win.destroy()
        f = ttk.Frame(win)
        f.pack(pady=10)
        ttk.Button(f, text="일람 양식 내보내기(부호·폭 채워짐)…", command=lambda: pick("export")).pack(side="left", padx=4)
        ttk.Button(f, text="창호일람 불러오기(Excel/DXF)…", command=lambda: pick("load")).pack(side="left", padx=4)
        ttk.Button(f, text="추정치로 계속", command=lambda: pick("continue")).pack(side="left", padx=4)
        win.protocol("WM_DELETE_WINDOW", lambda: pick("continue"))
        win.wait_window()
        return choice["v"]

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
        self.v_ack.set(False)
        if cat in ('pipe', 'duct', 'tray'):
            from geom_contract import mep_dimensions
            try:
                dims = mep_dimensions(cat, el, self.data.get('params'))
                self.v_w.set(str(dims.get('diameter', dims.get('width_mm', ''))))
                self.v_h.set(str(dims.get('height_mm', '')))
            except ValueError:
                self.v_w.set('')
                self.v_h.set('')
        else:
            self.v_w.set(str(ov.get("width", el.get("width_detected") or "")))
            self.v_h.set(str(ov.get("height", "")))

    def _apply_review(self):
        sel = self.tree.selection()
        if not sel or not self.data:
            messagebox.showinfo("Info", "Select an item from the list first.")
            return
        cat, idx = sel[0].split(":")
        el = self.data['elements'][cat][int(idx)]
        try:
            from mep_setup_ui import mep_property_overrides
            overrides = mep_property_overrides(cat, self.v_w.get(), self.v_h.get())
            meta = self.data['project']
            state = self.project_session.edit(el['eid'], dict(overrides=overrides, acknowledge=bool(self.v_ack.get())),
                                              meta['revision'], meta['project_id'])
            self.data = state['geometry']
            atomic_json(self.geom_path, self.data)
            self.v_ack.set(False)
            self._populate_review()
            self._log(f"저장 완료: revision {state['revision']}")
        except RevisionConflict as exc:
            self._save()
            self._populate_review()
            messagebox.showwarning("다른 창에서 변경됨", str(exc))
        except Exception as exc:
            messagebox.showerror("저장 실패", str(exc))

    def _save(self):
        if self.project_session:
            self.data = self.project_session.state()['geometry']
            if self.geom_path:
                atomic_json(self.geom_path, self.data)

    def _close(self):
        if self.project_server:
            self.project_server.close()
        self.root.destroy()

    def _do_add_source(self):
        """같은 층에 공종 도면(난방·환기 등)을 겹친다 — 건축은 기준 도면에서만, 이 도면에서는 설비만 담는다."""
        if not self.project_session:
            messagebox.showinfo('프로젝트 필요', '먼저 기준(건축) 도면을 Parse 하거나 프로젝트를 여세요.')
            return
        path = filedialog.askopenfilename(title='같은 층에 겹칠 설비 도면(DXF)', filetypes=[('DXF', '*.dxf')])
        if not path:
            return
        session = self.project_session
        self._set_buttons('disabled')
        self._log(f'같은 층 도면 추가: {os.path.basename(path)} — 이 도면에서는 설비 부재만 담습니다…')
        def run():
            try:
                manifest = session.store.read()
                state = session.add_source(path, manifest['revision'], manifest['project_id'])
                base = manifest['sources'][0]['path']
                self.root.after(0, lambda: (self._log(f"추가 완료(revision {state['revision']}). 이 도면의 설비 레이어는 "
                                                      "'설비 도면 설정'에서 도면을 골라 저장하세요."),
                                            self._parse_done(state['geometry'], base)))
            except Exception as exc:
                self.root.after(0, lambda msg=str(exc): (self._log(msg), self._set_buttons('!disabled'),
                                                         messagebox.showerror('도면 추가 실패', msg)))
        threading.Thread(target=run, daemon=True).start()

    def _do_source_setup(self, session, units_only=False):
        """원본이 여럿인 프로젝트 — 도면을 골라 그 원본의 단위·설비 설정을 연다."""
        sources = session.store.read()['sources']
        source_id = self._pick_source(sources)
        if source_id is None:
            return
        source = next(s for s in sources if s['id'] == source_id)
        self._set_buttons('disabled')
        self._log(f"{os.path.basename(source['path'])}: 원본의 영역·레이어·단위를 분석하고 있습니다…")
        def scanned(inventory):
            from mep_setup_ui import MepSetupDialog
            from drawing_units_ui import UnitSetupDialog
            self._set_buttons('!disabled')
            def saved(state):
                if self.project_session is session:
                    self._parse_done(state['geometry'], sources[0]['path'])
            (UnitSetupDialog if units_only else MepSetupDialog)(self.root, session, inventory, saved, source_id=source_id)
        def run():
            try:
                inventory = session.source_units(source_id)['inventory']
                self.root.after(0, lambda: scanned(inventory))
            except Exception as exc:
                self.root.after(0, lambda msg=str(exc): (self._log(msg), self._set_buttons('!disabled'),
                                                         messagebox.showerror('설비 분석 실패', msg)))
        threading.Thread(target=run, daemon=True).start()

    def _pick_source(self, sources):
        """원본이 여럿이면(같은 층 공종 도면) 설정할 도면을 고른다. 취소하면 None."""
        win = tk.Toplevel(self.root)
        win.title('설정할 도면 선택')
        win.transient(self.root)
        box = tk.Listbox(win, width=90, height=min(8, len(sources)))
        for source in sources:
            kinds = ', '.join(source.get('categories') or ['전체'])
            box.insert('end', f"{os.path.basename(source['path'])}  ({kinds})")
        box.selection_set(0)
        box.pack(padx=10, pady=8)
        chosen = {}
        def ok():
            picked = box.curselection()
            chosen['id'] = sources[picked[0]]['id'] if picked else None
            win.destroy()
        ttk.Button(win, text='선택', command=ok).pack(pady=6)
        win.grab_set()
        self.root.wait_window(win)
        return chosen.get('id')

    def _do_mep_setup(self, units_only=False):
        if self.project_session and len(self.project_session.store.read()['sources']) > 1:
            return self._do_source_setup(self.project_session, units_only)
        dxf = self._ensure_dxf()
        if not dxf:
            return
        try:
            same = self.project_session and self.project_session.store.read()['sources'][0]['path'] == os.path.abspath(dxf)
            if not same:
                try:
                    self.project_session = open_source_project(dxf, layer_map=self.v_map.get().strip() or None,
                        block_map=self.v_block.get().strip() or None, options={'use_ai': False, 'use_vision': False})
                except OSError:
                    folder = filedialog.askdirectory(title='설비 프로젝트 저장 폴더 선택')
                    if not folder:
                        return
                    self.project_session = open_source_project(dxf, folder=folder,
                        layer_map=self.v_map.get().strip() or None, block_map=self.v_block.get().strip() or None,
                        options={'use_ai': False, 'use_vision': False})
            if self.project_server:
                self.project_server.close()
                self.project_server = None
        except Exception as exc:
            messagebox.showerror('프로젝트 열기 실패', str(exc))
            return
        self._set_buttons('disabled')
        self._log('설비 원본의 영역·레이어·단위를 분석하고 있습니다…')
        selected_session = self.project_session
        def scanned(inventory):
            from mep_setup_ui import MepSetupDialog
            from drawing_units_ui import UnitSetupDialog
            self._set_buttons('!disabled')
            def saved(state):
                if self.project_session is selected_session:
                    self._parse_done(state['geometry'], dxf)
                else:
                    self._log(f"별도 설비 프로젝트 revision {state['revision']} 저장 완료: {dxf}")
            dialog = UnitSetupDialog if units_only else MepSetupDialog
            dialog(self.root, selected_session, inventory, saved)
        def run():
            try:
                source_id = selected_session.store.read()['sources'][0]['id']
                inventory = selected_session.source_units(source_id)['inventory']
                self.root.after(0, lambda: scanned(inventory))
            except Exception as exc:
                self.root.after(0, lambda msg=str(exc): (self._log(msg), self._set_buttons('!disabled'), messagebox.showerror('설비 분석 실패', msg)))
        threading.Thread(target=run, daemon=True).start()

    def _do_blender_build(self):
        if not self.project_session:
            messagebox.showinfo('프로젝트 필요', '먼저 도면을 파싱하거나 설비 도면 설정을 저장하세요.')
            return
        folder = filedialog.askdirectory(title='Blender / GLB 결과 저장 폴더', initialdir=str(self.project_session.store.folder))
        if not folder:
            return
        selected_session = self.project_session
        self._set_buttons('disabled')
        self._log('최신 저장 revision으로 Blender / GLB를 생성하고 실제 저장 파일을 검증합니다…')
        def done(receipt):
            self._set_buttons('!disabled')
            self._log('Blender 내보내기: ' + receipt.get('status', 'failed'))
            if receipt.get('status') == 'verified':
                from mep_setup_ui import blender_review_status
                review_summary, detail = blender_review_status(receipt)
                self._log(review_summary)
                for message in detail:
                    self._log('  [검토] ' + message)
                for kind, item in receipt.get('artifacts', {}).items():
                    self._log(f"  {kind}: {item.get('path', '')}")
                messagebox.showinfo('Blender / GLB 생성 완료', review_summary + '\n' + '\n'.join(detail[:8]) + '\n\n' + '\n'.join(item.get('path', '') for item in receipt['artifacts'].values()))
            else:
                messagebox.showwarning('내보내기 미완료', '\n'.join(map(str, receipt.get('errors', []) + receipt.get('diagnostics', []))) or str(receipt.get('status')))
        def run():
            try:
                from blender_runner import build_blender
                build_input, state = selected_session.export_geometry()
                from pathlib import Path
                import secrets
                out_dir = Path(folder) / f"blender-r{state['revision']}-{secrets.token_hex(4)}"
                receipt = build_blender(build_input, str(out_dir))
                self.root.after(0, lambda: done(receipt))
            except Exception as exc:
                self.root.after(0, lambda msg=str(exc): done({'status': 'failed', 'errors': [msg]}))
        threading.Thread(target=run, daemon=True).start()

    def _do_preview(self):
        if not self.project_session:
            messagebox.showwarning("Check", "먼저 (2) Parse 를 실행하세요.")
            return
        try:
            import webbrowser
            if not self.project_server:
                self.project_server = self.project_session.serve()
            webbrowser.open(self.project_server.url)
            self._log("미리보기 연결됨: 저장하면 프로젝트 revision이 갱신됩니다.")
        except Exception as exc:
            messagebox.showerror("Preview 실패", str(exc))

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

    def _do_review_csv(self):
        """간섭·연결 검토 목록을 CSV 로. 일상 검토는 여기서 끝난다 — FreeCAD 빌드는 납품 검증용이다.

        파일 이름에 revision 을 넣는다: 어느 상태의 목록인지 나중에 알 수 있어야 현장에 보낸 표를 믿는다."""
        if not self.data:
            messagebox.showwarning("Check", "먼저 (2) Parse 를 실행하세요.")
            return
        try:
            import clash_review as CR
            import mep_network as NET
            folder = os.path.join(str(self.project_session.store.folder), "review")
            os.makedirs(folder, exist_ok=True)
            revision = (self.data.get("project") or {}).get("revision", 0)
            clash_path, clash_rows = CR.write_csv(
                os.path.join(folder, f"clash_r{revision}.csv"),
                CR.to_rows(self.data.get("clash_review")), CR.CSV_COLUMNS)
            net_path, net_rows = CR.write_csv(
                os.path.join(folder, f"connectivity_r{revision}.csv"),
                NET.to_rows(self.data.get("mep_connectivity")), NET.CSV_COLUMNS)
            self._log(f"검토 목록 CSV -> {clash_path} ({clash_rows}행) · {net_path} ({net_rows}행)")
            summary = (self.data.get("clash_review") or {}).get("summary") or {}
            if summary.get("assumed_basis"):
                self._log(f"  간섭 {summary.get('total', 0)}건 중 가정 높이 {summary['assumed_basis']}건 — "
                          "설비 설정에서 설치 높이·규격을 선언하면 줄어듭니다")
            os.startfile(folder)
        except PermissionError:
            messagebox.showerror("검토 목록 CSV", "출력 파일이 Excel 에서 열려 있습니다. 닫고 다시 시도하세요.")
        except Exception as e:
            messagebox.showerror("검토 목록 CSV 실패", str(e))

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
            self._save()
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
                build_input, build_state = self.project_session.export_geometry()
                stats = IB.build(build_input, out, storey="Level", connect=connect)
                receipt_path = out + '.client-receipt.json'
                atomic_json(receipt_path, stats)
                artifacts = verified_artifacts(receipt_path, build_state['geometry'], stats.get('provenance', {}).get('run_id'))
                self.root.after(0, lambda: self._ifc_done(out, stats, artifacts.get('ifc')))
            except Exception as e:
                msg = str(e)
                self.root.after(0, lambda m=msg: (
                    self._log(f"[오류] IFC 빌드 실패: {m}"),
                    self.btn_ifc.state(["!disabled"])))
        threading.Thread(target=run, daemon=True).start()

    def _ifc_done(self, out, stats, receipt=None):
        receipt = receipt or {'status':'failed','error':'Current artifact receipt missing'}
        self._log(f"IFC: {receipt['status']} — {receipt.get('path') or receipt.get('error', '')}")
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
        self.btn_build.state(["disabled"])
        self._log(f"Build started... (freecadcmd) -> {out}.FCStd / .ifc")
        # 얼마나 걸릴지는 지난 빌드가 이미 말해 뒀다(`stage_seconds`) — 7분짜리를 모르고 누르지 않게.
        try:
            with open(out + ".build.json", encoding="utf-8") as handle:
                stages = (json.load(handle).get("stage_seconds") or {})
            if stages:
                top = max(stages.items(), key=lambda kv: kv[1])
                self._log(f"  이전 빌드 {sum(stages.values()):.0f}초 (가장 긴 단계 {top[0]} {top[1]:.0f}초) — "
                          "일상 검토는 (3b) 검토 목록 CSV 로 충분합니다")
        except Exception:
            pass

        builder_py = resource_path("freecad_builder.py")

        def run():
            try:
                build_input, build_state = self.project_session.export_geometry()
                from freecad_runner import run_build
                r = run_build(fc, build_input, out, builder_py, timeout=900)
                r.stdout = (r.stdout or b'').decode('utf-8', errors='replace') if isinstance(r.stdout, bytes) else r.stdout
                r.stderr = (r.stderr or b'').decode('utf-8', errors='replace') if isinstance(r.stderr, bytes) else r.stderr
                artifacts = verified_artifacts(out + '.build.json', build_state['geometry'], r.run_id)
                self.root.after(0, lambda: self._build_done(r, out, artifacts))
            except Exception as e:
                # Python 3: 람다에서 except 변수 참조 시 소멸 → 명시적 캡처
                _msg = str(e) or repr(type(e))
                self.root.after(0, lambda msg=_msg: (
                    self._log(f"[Error] Build failed: {msg}"),
                    self.btn_build.state(["!disabled"])))
        threading.Thread(target=run, daemon=True).start()

    def _build_done(self, r, out, artifacts=None):
        self.btn_build.state(["!disabled"])
        artifacts = artifacts or {}
        for kind in ('fcstd', 'ifc'):
            receipt = artifacts.get(kind) or {'status':'failed','error':'Current artifact receipt missing'}
            status = receipt.get('status')
            self._log(f"{kind.upper()}: {status} — {receipt.get('path') or receipt.get('error', '')}")
        if any((artifacts.get(k) or {}).get('status') != 'verified' for k in ('fcstd','ifc')):
            self._log("요청한 산출물 중 검증되지 않은 항목이 있습니다. 검사 보고서를 확인하세요.")
            self._log((r.stdout or '')[-1500:])
        self._log(f"검사 보고서: {out}.build.json")


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
        offline = '<script id="mep-app">' in html and '<script src=' not in html
        if not offline:
            raise RuntimeError('오프라인 미리보기 번들이 누락되었습니다')
        # Exercise the shipped MEP path/profile/export preparation modules, without running native CAD.
        import tempfile
        import hashlib
        import math
        import ezdxf
        import geom_contract as GC
        import blender_builder as BB
        from mep_profile import inspect_mep_source
        with tempfile.TemporaryDirectory(prefix='mep-selftest-', dir=base) as folder:
            from pathlib import Path
            drawing = Path(folder) / 'heating.dxf'
            doc = ezdxf.new(units=4)
            doc.layers.new('SELFTEST_HEAT')
            doc.modelspace().add_line((0, 0), (1000, 0), dxfattribs={'layer': 'SELFTEST_HEAT'})
            doc.modelspace().add_arc((1000, 100), 100, 270, 360, dxfattribs={'layer': 'SELFTEST_HEAT'})
            doc.saveas(drawing)
            inventory = inspect_mep_source(str(drawing))
            source_hash = hashlib.sha256(drawing.read_bytes()).hexdigest()
            if inventory['source_sha256'] != source_hash:
                raise RuntimeError('MEP inventory source hash mismatch')
            profile = {'version': 1, 'source_sha256': source_hash,
                'layers': [{'pattern': '^SELFTEST_HEAT$', 'category': 'pipe', 'system': 'heating',
                            'diameter_mm': 15.9, 'nominal_size': '15A', 'material': 'PB',
                            'dimension_basis': 'assumed', 'placement': 'foam_top'}],
                'levels': {'structural_slab_top_mm': 0},
                'floor_layers': [{'role': 'impact_insulation', 'thickness_mm': 30},
                                {'role': 'foamed_concrete', 'thickness_mm': 40},
                                {'role': 'screed', 'thickness_mm': 40}]}
            heat = P.parse(str(drawing), [], [], mep_profile=profile)
            paths = heat['elements']['pipe']
            if len(paths) != 1 or len(paths[0].get('source_refs', [])) != 2:
                raise RuntimeError('MEP LINE/ARC connectivity or provenance failed')
            pipe = paths[0]
            if GC.mep_dimensions('pipe', pipe)['diameter'] != 15.9 or any(abs(a-b) > 1e-6 for a, b in zip(GC.z_range('pipe', pipe), (70., 85.9))):
                raise RuntimeError('MEP OD or foam-top elevation failed')
            if abs(pipe['source_length_mm'] - (1000 + math.pi * 50)) > 1e-6:
                raise RuntimeError('MEP analytic curve length failed')
            payload = BB.prepare_payload(heat)
            if not any(item.get('kind') == 'curve' for item in payload['objects']):
                raise RuntimeError('Blender payload has no source pipe curve')
        msg = (f"[selftest] parse OK: elements={n}, "
               f"shapely={'on' if P.HAS_SHAPELY else 'off'}\n"
               f"[selftest] preview OK: html={len(html)} bytes, "
               f"offline_three={offline}\n"
               f"[selftest] MEP profile/LINE+ARC/source_refs OK: paths={len(paths)}\n"
               f"[selftest] PB OD/foam_top OK: 15.9 mm, z=70..85.9 mm\n"
               f"[selftest] Blender payload preparation OK: objects={len(payload['objects'])}\n[selftest] PASS\n")
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
