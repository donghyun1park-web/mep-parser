"""문 일람표(층별 문 목록) 도면 → 행. 읽기 전용.

표는 선이 아니라 글자 칸이다(MTEXT/TEXT). 같은 y 줄에 표 덩어리 여러 개가 옆으로 서 있다(실측: 10개).
  1. 'FLOOR' 머리 칸 하나 = 표 덩어리 하나. 덩어리 오른쪽 끝 = 같은 줄 다음 'FLOOR' 칸.
  2. 머리는 2단일 수 있다(TEXT 'ARCHI' 위 / 'MARK' 아래, MTEXT 'FLOOR' 한 칸) → 같은 x 의 글자를 위→아래로 이어 열 이름.
  3. 칸 글자 크기(머리 'FLOOR' 크기 ±20%)가 아니거나 어느 열 가운데에서도 먼 글자는 버린다
     (실측: 도곽 격자 기호 'A'(글자 크기 600, 칸 837)가 부호 칸에 'FSD-3 A' 로 붙었다).
  4. 데이터 칸은 부착점이 왼쪽인 MTEXT 가 섞여 있다 — 삽입점이 아니라 **칸 가운데**(부착점+폭)로 열을 고른다.
     (삽입점으로 고르면 FROM 열 글자가 옆 MARK 열로 간다.)
  5. 문 범례표(부호 · W x H · W1 · H1 …)에서 부호별 H1(바닥에서 문 아래까지 — 점검구형 문)을 붙인다.

  python door_table.py doors.dxf --floor-filter '<floor label regex>' --out <work>/decl/doors.json
  python door_table.py --selftest
"""
import argparse
import collections
import json
import re
import statistics

import dxfwalk as W

# 열 이름 → 표준 키. 순서가 중요하다('H/W SET NO.' 는 NO 보다 먼저 HW 로).
CANON = [("HW", r"H/W|HARDWARE"), ("FLOOR", r"^FLOOR$|^층$"), ("FROM", r"FROM"), ("TO", r"\bTO\b"),
         ("MARK", r"MARK|TYPE|부\s*호"), ("W", r"WIDTH|^W$|폭"), ("H", r"HEIGHT|^H$|높이"), ("HAND", r"HAND"),
         ("QTY", r"Q'?TY|수량"), ("REMARK", r"REMARK|비\s*고"), ("NO", r"^NO\.?$|번호")]


def canon(name):
    for key, pat in CANON:
        if re.search(pat, name, re.I):
            return key
    return name


def num(s):
    s = (s or "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def rows_of(cells, floor_header="FLOOR"):
    anchors = [c for c in cells if c["t"].upper() == floor_header.upper()]
    out = []
    for ti, a in enumerate(sorted(anchors, key=lambda c: (-c["y"], c["x"]))):
        h = a["h"] or 300.0
        row = sorted(b["x"] for b in anchors if abs(b["y"] - a["y"]) < h)
        steps = [q - p for p, q in zip(row, row[1:])]
        nxt = [x for x in row if x > a["x"] + h]
        x1 = (min(nxt) if nxt else a["x"] + (statistics.median(steps) if steps else 1e9)) - h
        x0 = a["x"] - h
        lower = [b["y"] for b in anchors if b["y"] < a["y"] - h and x0 <= b["x"] < x1]
        y0 = max(lower) + 1.5 * h if lower else -1e18
        head = sorted((c for c in cells if abs(c["y"] - a["y"]) <= 1.2 * h and x0 <= c["x"] < x1), key=lambda c: c["x"])
        cols = []                                          # [x, [글자(위→아래)]]
        for c in head:
            if cols and abs(c["x"] - cols[-1][0]) < h:
                cols[-1][1].append(c)
            else:
                cols.append([c["x"], [c]])
        cols = [(x, canon(" ".join(t["t"] for t in sorted(g, key=lambda t: -t["y"])))) for x, g in cols]
        # 칸 글자와 크기가 다른 글자(도곽 표제·개정 표시 'A')는 표가 아니다 — 안 거르면 부호 칸에 'FSD-3 A' 로 붙는다
        body = [c for c in cells if y0 < c["y"] < a["y"] - 1.2 * h and x0 <= c["x"] < x1
                and (not c["h"] or abs(c["h"] - h) < 0.2 * h)]
        gap = statistics.median([q[0] - p[0] for p, q in zip(cols, cols[1:])] or [4 * h])
        lines = collections.defaultdict(dict)
        for c in body:
            x, key = min(cols, key=lambda k: abs(k[0] - c["x"]))
            if abs(x - c["x"]) > max(4 * h, 0.6 * gap):      # 어느 열에도 안 붙는 글자(도곽 격자 기호 등)
                continue
            y = round(c["y"] / (0.6 * h))
            lines[y][key] = (lines[y].get(key, "") + " " + c["t"]).strip()
        for y in sorted(lines, reverse=True):
            r = lines[y]
            if r.get("FLOOR") and r.get("MARK"):
                out.append({**r, "W": num(r.get("W")), "H": num(r.get("H")), "table": ti})
    return out


def legend(cells, keys=("H1", "W1"), mark_header=r"^부\s*호$|^MARK$"):
    """문 범례표: 부호 열과 같은 줄의 H1·W1 칸. {부호: {H1: 값}}. 같은 부호가 두 범례에서 다르면 목록으로 남는다."""
    out = collections.defaultdict(dict)
    for k in (c for c in cells if c["t"] in keys):
        h = k["h"] or 300.0
        hdr = [m for m in cells if re.search(mark_header, m["t"]) and 0 < k["x"] - m["x"] < 15000 and -h < m["y"] - k["y"] < 1500]
        if not hdr:
            continue
        m = min(hdr, key=lambda m: k["x"] - m["x"])
        for mk in (c for c in cells if abs(c["x"] - m["x"]) < h and c["y"] < k["y"] - h / 2 and k["y"] - c["y"] < 30000):
            v = [c for c in cells if abs(c["x"] - k["x"]) < h and abs(c["y"] - mk["y"]) < h / 2]
            if v:
                prev = out[mk["t"]].get(k["t"])
                val = v[0]["t"]
                out[mk["t"]][k["t"]] = val if prev in (None, val) else sorted({*([prev] if isinstance(prev, str) else prev), val})
    return dict(out)


def extract(items, layer=None, floor_filter=None, floor_header="FLOOR"):
    cells = [i for i in items if i["k"] in ("TEXT", "MTEXT") and i["t"] and (not layer or re.search(layer, i["l"]))]
    rows = rows_of(cells, floor_header)
    leg = legend([i for i in items if i["k"] in ("TEXT", "MTEXT") and i["t"]])
    for r in rows:
        for k, v in (leg.get(r["MARK"]) or {}).items():
            r[k] = num(v) if isinstance(v, str) else v
    if floor_filter:
        rows = [r for r in rows if re.search(floor_filter, r["FLOOR"])]
    return rows, leg


def selftest():
    from ezdxf.enums import TextEntityAlignment
    doc = W.new_doc()
    msp = doc.modelspace()
    mt = lambda t, x, y, w=4000, ap=5: msp.add_mtext(t, dxfattribs={"insert": (x, y), "char_height": 800, "width": w,
                                                                    "attachment_point": ap})
    tx = lambda t, x, y: msp.add_text(t, height=800).set_placement((x, y), align=TextEntityAlignment.MIDDLE_CENTER)
    for bx in (0, 50000):                                  # 옆으로 선 표 덩어리 둘
        mt("FLOOR", bx, 0), mt("NO", bx + 5000, 0), mt("DOOR FROM", bx + 15000, 0, 10000), mt("Q'TY", bx + 30000, 0)
        tx("ARCHI", bx + 8000, 500), tx("MARK", bx + 8000, -500)
        tx("DOOR", bx + 22000, 500), tx("WIDTH", bx + 22000, -500), tx("DOOR", bx + 26000, 500), tx("HEIGHT", bx + 26000, -500)
    for y, fl, no, mk, w, hh in ((-2500, "B1F", "101", "FSD-3", "700", "1500"), (-4500, "5F~ODD", "501", "FSD-1", "1,100", "2100")):
        mt(fl, 50000, y), mt(no, 55000, y), tx(mk, 58000, y), mt("복도", 60000, y, 10000, ap=4)   # 왼쪽 부착 칸
        mt(w, 72000, y), mt(hh, 76000, y), mt("9", 80000, y)
    tx("부  호", 100000, 10000), tx("H1", 106600, 9600), tx("FSD-3", 100000, 8400), tx("600", 106600, 8400)
    tx("FSD-1", 100000, 5200), tx("-", 106600, 5200)
    rows, leg = extract(W.collect(msp))
    by = {r["NO"]: r for r in rows}
    assert set(by) == {"101", "501"}, rows
    a = by["101"]
    assert (a["FLOOR"], a["MARK"], a["FROM"], a["W"], a["H"], a["QTY"], a["H1"]) == ("B1F", "FSD-3", "복도", 700, 1500, "9", 600), a
    assert by["501"]["W"] == 1100 and by["501"]["H1"] is None, by["501"]
    assert len(extract(W.collect(msp), floor_filter="ODD")[0]) == 1
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dxf", nargs="?")
    ap.add_argument("--layer", help="표 글자 레이어 정규식(없으면 전부)")
    ap.add_argument("--floor-filter", help="FLOOR 열 정규식(예: 대상 층 표기)")
    ap.add_argument("--floor-header", default="FLOOR")
    ap.add_argument("--out", help="출력 json — 저장소 밖(<작업 폴더>/decl/doors.json)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not (a.dxf and a.out):
        ap.error("dxf 와 --out(저장소 밖) 필요")
    out = W.out_path(a.out)
    rows, leg = extract(W.collect(W.ezdxf.readfile(a.dxf).modelspace()), a.layer, a.floor_filter, a.floor_header)
    json.dump({"source": a.dxf, "legend": leg, "rows": rows}, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    c = collections.Counter((r["MARK"], r["W"], r["H"], r.get("H1")) for r in rows)
    print("rows", len(rows), "| 부호·W·H·H1 ×개수:", "; ".join(f"{k[0]} {k[1]:.0f}x{k[2]:.0f} H1 {k[3]} ×{v}" if k[1] and k[2] else f"{k} ×{v}"
                                                         for k, v in sorted(c.items(), key=str)), W.drops(), "->", out)


if __name__ == "__main__":
    main()
