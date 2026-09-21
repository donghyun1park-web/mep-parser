"""
boq_export.py  —  geometry.json → 물량집계(BOQ) Excel

파싱된 기하에서 견적용 물량을 결정론적으로 집계한다:
  벽    : 두께별 — 개수 · 총길이(m) · 면적(㎡, 길이×높이) · 체적(㎥)
  기둥  : 단면별(원형 D촉/사각 W×D) — 개수 · 높이 · 콘크리트 체적
  슬래브: 개수 · 면적(㎡, 신발끈 공식) · 체적(㎥)
  창호  : door/window 규격별 개수
  MEP   : pipe/duct/tray 규격별 총길이(m)

Excel 양식은 schedule_io.export_schedule_xlsx 스타일(헤더 채움/테두리/열폭)을 따른다.

사용:
    python boq_export.py geometry.json            # <입력>_물량.xlsx
    python boq_export.py geometry.json out.xlsx
"""
import argparse
import json
import math
import os
import sys

import construction_rules as CR
import geom_contract as GC
from geom_contract import poly_area as _poly_area
from geom_contract import mep_dimensions


def _polyline_len(pts):
    return sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
               for i in range(len(pts) - 1))


def _bbox_wd(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return max(xs) - min(xs), max(ys) - min(ys)


def _r10(v):
    """10mm 반올림(제도 오차 흡수한 규격 그룹핑)."""
    return int(round(float(v) / 10.0) * 10)


def aggregate(data):
    """geometry.json dict → 섹션별 집계 rows. 반환 {섹션명: (헤더, rows, 합계행)}."""
    els = data.get("elements", {})
    params = data.get("params", {})
    ph = float(params.get("wall", {}).get("height", 2800.0))
    pcol_h = float(params.get("column", {}).get("height", 3000.0))
    pslab_t = float(params.get("slab", {}).get("thickness", 200.0))
    out = {}

    # ── 벽: 두께별 ───────────────────────────────────────────
    wg = {}
    for w in els.get("wall", []):
        h = GC.height_of(w, params, "wall")      # 계약과 같은 순서(overrides > 레코드 > params) — 창 위아래 벽은 짧다
        if w.get("closed") and len(w.get("points", [])) >= 3:
            pts = w["points"]
            L = _polyline_len(pts + [pts[0]])
            vol = _poly_area(pts) * h            # 폐합벽: 단면적×높이
            key = "폐합(솔리드)"
            t = None
        else:
            cl = w.get("centerline") or w.get("points") or []
            if len(cl) < 2:
                continue
            L = _polyline_len(cl)
            t = GC.width_of(w, params, "wall")   # 계약과 같은 순서 — thin_pair 실측을 믿지 않는다(geom_contract.width_of)
            vol = L * h * t
            key = f"T{_r10(t)}"
            if w.get("source") == "opening_infill":
                # 창 아래 벽·인방은 같은 평면 선에 아래·위로 겹쳐 선다 — 벽 행에 섞으면 길이가 평면 길이의 두 배로 센다
                key += " 창·문 위아래"
        g = wg.setdefault(key, {"count": 0, "len": 0.0, "area": 0.0, "vol": 0.0, "hs": set()})
        g["count"] += 1
        g["len"] += L
        g["area"] += L * h
        g["vol"] += vol
        g["hs"].add(round(h))
    rows = [[k, g["count"], round(g["len"] / 1000, 1),
             round(next(iter(g["hs"])) / 1000, 2) if len(g["hs"]) == 1 else "",   # 높이가 섞이면 한 값을 대표로 쓰지 않는다
             round(g["area"] / 1e6, 1), round(g["vol"] / 1e9, 2)]
            for k, g in sorted(wg.items())]
    # 길이 합계는 평면 길이다 — 창 아래 벽·인방은 같은 선을 두 번 세므로 뺀다(면적·체적에는 들어간다)
    tot = ["합계", sum(r[1] for r in rows), round(sum(r[2] for r in rows if not str(r[0]).endswith("창·문 위아래")), 1), "",
           round(sum(r[4] for r in rows), 1), round(sum(r[5] for r in rows), 2)]
    out["벽"] = (["두께", "개수", "길이(m)", "높이(m)", "면적(㎡)", "체적(㎥)"], rows, tot)

    # ── 기둥: 단면별 ─────────────────────────────────────────
    cg = {}
    for c in els.get("column", []):
        ov = c.get("overrides") or {}
        h = float(ov.get("height", pcol_h))
        if c.get("kind") == "circle":
            r = float(c.get("radius", 200.0))
            key = f"D{_r10(2 * r)}"
            area = math.pi * r * r
        elif c.get("points"):
            wd = _bbox_wd(c["points"])
            key = f"{_r10(wd[0])}x{_r10(wd[1])}"
            area = _poly_area(c["points"]) or wd[0] * wd[1]
        else:
            continue
        g = cg.setdefault(key, {"count": 0, "vol": 0.0, "h": h})
        g["count"] += 1
        g["vol"] += area * h
    rows = [[k, g["count"], round(g["h"] / 1000, 2), round(g["vol"] / 1e9, 2)]
            for k, g in sorted(cg.items())]
    tot = ["합계", sum(r[1] for r in rows), "", round(sum(r[3] for r in rows), 2)]
    out["기둥"] = (["단면(mm)", "개수", "높이(m)", "체적(㎥)"], rows, tot)

    # ── 슬래브 ──────────────────────────────────────────────
    rows = []
    ta = tv = 0.0
    for i, s in enumerate(els.get("slab", [])):
        if not s.get("points") or len(s["points"]) < 3:
            continue
        ov = s.get("overrides") or {}
        t = float(ov.get("thickness", pslab_t))
        a = _poly_area(s["points"])
        rows.append([f"Slab_{i}", s.get("layer", ""), round(a / 1e6, 1),
                     _r10(t), round(a * t / 1e9, 2)])
        ta += a
        tv += a * t
    tot = ["합계", "", round(ta / 1e6, 1), "", round(tv / 1e9, 2)]
    out["슬래브"] = (["번호", "레이어", "면적(㎡)", "두께(mm)", "체적(㎥)"], rows, tot)

    # ── 보/거더: 부재명·단면별 ────────────────────────────────
    # elements["beam"] 이 물량에서 통째로 빠져 있었다(빌더도 안 읽던 시절의 잔재).
    # 부재명이 있으면 일람표 단면이 붙어 있으므로 그대로 집계 단위로 쓴다.
    bg = {}
    for b in els.get("beam", []):
        pts = b.get("centerline") or b.get("points") or []
        if len(pts) < 2:
            continue
        sec = b.get("section") or {}
        ov = b.get("overrides") or {}
        bw = float(ov.get("width") or sec.get("b") or 0.0)
        bh = float(ov.get("thickness") or sec.get("h") or 0.0)
        name = sec.get("name") or b.get("member_name") or "-"
        size = sec.get("size") or (f"{_r10(bw)}x{_r10(bh)}" if bw and bh else "-")
        key = (name, size)
        L = _polyline_len(pts)
        prev = bg.get(key) or [0, 0.0, 0.0]
        bg[key] = [prev[0] + 1, prev[1] + L, prev[2] + L * bw * bh]
    rows = [[k[0], k[1], n, round(L / 1000.0, 1), round(v / 1e9, 2)]
            for k, (n, L, v) in sorted(bg.items())]
    if rows:
        tot = ["합계", "", sum(r[2] for r in rows),
               round(sum(r[3] for r in rows), 1), round(sum(r[4] for r in rows), 2)]
        # 철골 보의 정산 단위는 연장(m)·중량(ton)이지 체적이 아니다. 체적은 b×h
        # 외곽이라 형강 실체적보다 훨씬 크므로 열 이름에 '외곽' 을 명시한다.
        out["보"] = (["부재명", "단면(mm)", "개수", "연장(m)", "외곽체적(㎥)"], rows, tot)

    # ── 창호: 규격별 ─────────────────────────────────────────
    og = {}
    for o in els.get("opening", []):
        sub = o.get("subtype") or "opening"
        w = o.get("width")
        h = o.get("height")
        size = (f"{_r10(w)}x{_r10(h)}" if w and h else
                f"D{_r10(2 * o['radius'])}" if o.get("radius") else "-")
        key = ({"door": "문", "window": "창"}.get(sub, "개구부"), size)
        og[key] = og.get(key, 0) + 1
    rows = [[k[0], k[1], n] for k, n in sorted(og.items())]
    tot = ["합계", "", sum(r[2] for r in rows)]
    out["창호"] = (["구분", "규격(mm)", "개수"], rows, tot)

    # ── MEP: 규격별 길이 ─────────────────────────────────────
    mg, footprints = {}, []
    for cat, label in (("pipe", "배관"), ("duct", "덕트"), ("tray", "트레이")):
        for m in els.get(cat, []):
            pts = ((m.get("points") or []) if m.get("geometry_mode") == "footprint" else m.get("centerline") or m.get("points") or [])
            if len(pts) < 2:
                continue
            dims = mep_dimensions(cat, m, params)
            if m.get("geometry_mode") == "footprint":
                area = _poly_area(pts) - sum(_poly_area(h) for h in m.get("holes") or [])
                footprints.append([label, m.get("eid") or m.get("layer", ""), dims["height_mm"], round(area / 1e6, 6)])
                continue
            key = (label, _size_label(cat, dims))
            g = mg.setdefault(key, {"count": 0, "len": 0.0, "basis": set()})
            g["count"] += 1
            length = m.get("source_length_mm")
            # 원본 길이는 손대지 않은 원본 경로(2D)의 길이다. 편집했거나 사람이 그린 경로는
            # 현재 모델 경로의 길이(수직·경사 구간 포함, 계약 v3)를 센다. **배수 구배(opt-in)** 는
            # 원본 길이를 그대로 쓴다 — 1/100 구배가 늘리는 실제 길이는 0.005% 라 물량에 안 보이지만,
            # 어느 쪽을 썼는지는 숨기지 않는다(길이기준 열).
            basis = "원본(2D)"
            if length is None or m.get("geometry_modified"):
                from geom_contract import route_length
                length = route_length(m)
                basis = "모델(수정·경사 포함)"
            g["len"] += float(length)
            g["basis"].add(basis)
    rows = [[k[0], k[1], g["count"], round(g["len"] / 1000, 3),
            next(iter(g["basis"])) if len(g["basis"]) == 1 else "혼합"]
            for k, g in sorted(mg.items())]
    tot = ["합계", "", sum(r[2] for r in rows), round(sum(r[3] for r in rows), 3), ""]
    out["MEP"] = (["구분", "규격(mm)", "개수", "길이(m)", "길이기준"], rows, tot)
    out["MEP 외곽"] = (["구분", "원본 식별자", "높이(mm)", "평면 면적(㎡)"], footprints,
                       ["합계", "", "", round(sum(r[3] for r in footprints), 6)])

    # ── MEP 이음: 형식·규격별 개수 — 도면이 실제로 이어 그린 곳(`joints`)만 센다 ──
    #   형식 판정은 IFC 피팅 몸체와 같은 함수다(`GC.joint_fittings`) — 물량표와 모델의 피팅 수가 갈라지지 않게.
    #   같은 규격이 곧게 이어진 곳은 피팅이 아니라 선을 끊어 그린 자리라 세지 않는다. 끊긴 이음은 V012 가 말한다.
    kinds = {"elbow": "엘보", "reducer": "레듀서", "tee": "티", "cross": "크로스"}
    fg = {}
    for fitting in GC.joint_fittings(els, params)["fittings"]:
        sizes = {_size_label(c, mep_dimensions(c, rec, params)) for c, rec, _p, _a in fitting["members"]}
        kind = kinds.get(fitting["kind"]) or "분기" + fitting["kind"][len("branch"):]
        cat = fitting["category"]
        key = ({"pipe": "배관", "duct": "덕트", "tray": "트레이"}.get(cat, cat), kind, "/".join(sorted(sizes)))
        fg[key] = fg.get(key, 0) + 1
    rows = [[k[0], k[1], k[2], count] for k, count in sorted(fg.items())]
    out["MEP 이음"] = (["구분", "형식", "규격(mm)", "개수"], rows, ["합계", "", "", sum(r[3] for r in rows)])

    # ── 설비 지지·청소구 — 시공기준(construction_rules.py). 개수는 하한 추정이다 ──
    support_rows = CR.supports(data)
    if support_rows:
        tot = ["합계", "", "", "", round(sum(r[4] for r in support_rows), 3), "",
               sum(r[6] for r in support_rows), ""]
        out["MEP 지지·청소구"] = (["구분", "용도", "재질", "규격(DN)", "길이(m)", "간격(m)", "개수(하한)", "근거"],
                                 support_rows, tot)

    # ── 설비 보온 — 프로필의 보온 선언(등급·다습·유온)이 있는 배관·덕트만. 두께는 최소 기준이다 ──
    insulation_rows = CR.insulation(data)
    if insulation_rows:
        tot = ["합계", "", "", "", round(sum(r[4] for r in insulation_rows), 3), ""]
        out["MEP 보온"] = (["구분", "용도", "규격", "두께(mm)", "길이(m)", "근거"], insulation_rows, tot)
    return out


def _size_label(cat, dims):
    """규격 표기 — 배관은 외경, 원형 덕트는 Ø지름, 사각은 폭x높이. 길이 표와 이음 표가 같이 쓴다."""
    if "diameter" in dims:                   # 배관 · 원형 덕트(v3)
        return f"{dims['diameter']:g}" if cat == "pipe" else f"Ø{dims['diameter']:g}"
    return f"{dims['width_mm']:g}x{dims['height_mm']:g}"


def export_boq_xlsx(data, path, title=None):
    """geometry.json dict → 물량집계 Excel. schedule_io 스타일 재사용. 반환 경로."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    src = os.path.basename(data.get("source", "") or "geometry.json")
    title = title or f"물량집계 — {src}"
    secs = aggregate(data)

    wb = Workbook()
    ws = wb.active
    ws.title = "물량집계"
    hdr_fill = PatternFill("solid", fgColor="2D6CDF")
    sec_fill = PatternFill("solid", fgColor="EDF2FB")
    hdr_font = Font(bold=True, color="FFFFFF")
    thin = Side(style="thin", color="B0B7C3")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    ws.merge_cells("A1:F1")
    t = ws.cell(row=1, column=1, value=title)
    t.font = Font(size=14, bold=True)
    t.alignment = Alignment(horizontal="center")
    qa = data.get("qa") or {}
    # ★ 무엇을 세고 무엇을 안 세는지 — 이 줄이 없으면 받는 사람이 "개구부는 당연히 뺐겠지" 로 읽는다.
    #   화면 물량 패널도 같은 문장을 쓴다(`review_logic.js` 의 `BOQ_SCOPE_NOTE`).
    meta = (f"파싱 커버리지 {qa.get('face_coverage_pct', '—')}%  ·  단위: m/㎡/㎥"
            "  ·  벽 면적·체적은 중심선 길이×높이×두께(개구부 미차감)"
            "  ·  접합부 중복·마감·할증 미포함  ·  지지 개수는 하한  ·  검토용")
    ws.merge_cells("A2:F2")
    m = ws.cell(row=2, column=1, value=meta)
    m.font = Font(italic=True, color="7A8290")
    m.alignment = Alignment(horizontal="center")

    r = 4
    for name, (headers, rows, tot) in secs.items():
        if not rows:
            continue
        sc = ws.cell(row=r, column=1, value=f"■ {name}")
        sc.font = Font(bold=True, size=12)
        sc.fill = sec_fill
        r += 1
        for c, h in enumerate(headers, start=1):
            cell = ws.cell(row=r, column=c, value=h)
            cell.fill = hdr_fill
            cell.font = hdr_font
            cell.alignment = Alignment(horizontal="center")
            cell.border = border
        r += 1
        for row in rows + [tot]:
            bold = row is tot
            for c, v in enumerate(row, start=1):
                cell = ws.cell(row=r, column=c, value=v)
                cell.border = border
                cell.alignment = Alignment(
                    horizontal="left" if c == 1 else "right")
                if bold:
                    cell.font = Font(bold=True)
            r += 1
        r += 1  # 섹션 사이 빈 행

    for col, w in zip("ABCDEF", [16, 10, 12, 10, 12, 12]):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A4"
    wb.save(path)
    return path


# ── 단가 연결용 CSV (ifc5d Bill of Quantities 형식) ──────────────────
# ifc5d 는 CSV → IfcCostSchedule 임포트를 지원한다(csv2ifc). 여기서는 집계 결과를
# 그 형식으로 내보내 단가(Rate)만 채우면 금액이 계산되는 내역서 뼈대를 만든다.
# Query 는 IfcOpenShell 셀렉터 — 해당 코스트 항목에 묶일 IFC 요소를 지정한다.
BOQ_CSV_HEADERS = ["Index", "Identification", "Name", "Unit", "Quantity",
                   "Property", "Query", "Rate"]


def export_boq_csv(data, path):
    """집계 → ifc5d BoQ CSV(단가 열 비움). 반환 (경로, 행 수)."""
    import csv
    secs = aggregate(data)
    rows = []
    idx = 0

    def add(ident, name, unit, qty, prop, query):
        nonlocal idx
        idx += 1
        rows.append([idx, ident, name, unit, qty, prop, query, ""])

    for key, cnt, ln, h, area, vol in secs["벽"][1]:
        q = f'IfcWall, type="WALL-{key[1:]}"' if key.startswith("T") else "IfcWall"
        add(f"W-{key}", f"벽 {key}", "m3", vol, "GrossVolume", q)
    for key, cnt, h, vol in secs["기둥"][1]:
        add(f"C-{key}", f"기둥 {key}", "m3", vol, "GrossVolume", "IfcColumn")
    for no, layer, area, thk, vol in secs["슬래브"][1]:
        add(f"S-{no}", f"슬래브 {no} ({layer})", "m3", vol, "GrossVolume", "IfcSlab")
    for kind, size, cnt in secs["창호"][1]:
        add(f"O-{size}", f"{kind} {size}", "EA", cnt, "COUNT",
            "IfcDoor" if kind == "문" else "IfcWindow")
    for kind, size, cnt, ln in secs["MEP"][1]:
        add(f"M-{kind}-{size}", f"{kind} {size}", "m", ln, "Length", "IfcFlowSegment")

    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(BOQ_CSV_HEADERS)
        w.writerows(rows)
    return path, len(rows)


def main():
    for _s in (sys.stdout, sys.stderr):   # cp949 콘솔 한글 깨짐 방지
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="geometry.json → 물량집계 Excel")
    ap.add_argument("geometry", help="geometry.json 경로")
    ap.add_argument("out", nargs="?", default=None,
                    help="출력 .xlsx (기본 <입력>_물량.xlsx)")
    ap.add_argument("--csv", action="store_true",
                    help="단가 연결용 BoQ CSV(ifc5d 형식)도 함께 출력")
    args = ap.parse_args()
    with open(args.geometry, encoding="utf-8") as f:
        data = json.load(f)
    out = args.out or os.path.splitext(args.geometry)[0] + "_물량.xlsx"
    try:
        export_boq_xlsx(data, out)
    except ImportError:
        print("[ERROR] openpyxl 필요: pip install openpyxl", file=sys.stderr)
        sys.exit(1)
    secs = aggregate(data)
    parts = []
    for name, (_, rows, tot) in secs.items():
        if rows:
            parts.append(f"{name} {tot[1] if isinstance(tot[1], int) else len(rows)}")
    print(f"[OK] 물량집계 -> {out}  ({', '.join(parts)})")
    if args.csv:
        cpath, n = export_boq_csv(data, os.path.splitext(out)[0] + "_boq.csv")
        print(f"[OK] 단가연결 CSV -> {cpath}  ({n}행 — Rate 열에 단가 입력)")


if __name__ == "__main__":
    main()
