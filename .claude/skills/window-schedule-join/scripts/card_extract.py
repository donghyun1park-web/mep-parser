"""창호전개도(입면 카드) → 카드 표. 읽기 전용.

카드 = 부호 기호 블록(INSERT, ATTRIB 로 종류·일련번호) + 그 아래 입면 + 세로 치수 사슬 + 'F.L' 글자.
  W·H   일련번호가 100mm 단위 크기다('18x22' = 1800x2200, '7.5x22.4' = 750x2240). 가로 치수 최대값(dimW)과 대조한다.
  창대  ★ F.L 규칙: 'F.L' 글자는 자기 선보다 ~220mm **위**에 있고(사이에 삼각형) 카드 상자보다 ~1.1m 왼쪽에 있기도 하다.
        (실측, 단위세대 창호전개도 245장: 글자-선 220mm 가 F.L 카드 225장 중 175장, 글자 x 는 기호 왼쪽 1.3~2.2m 141장 ·
        오른쪽 4.4~5.2m 84장 — 한 도면에 두 배치가 섞여 있다.)
        → 카드 기호 왼쪽 --fl-left(2.5m)까지 찾고, 글자보다 **100mm 이상 아래**인 사슬 높이 중 가장 가까운 것이 F.L 선이다.
        옆 카드의 글자도 창에 들어오므로 글자-선 사이가 가장 좁은 글자를 고른다.
        창 하단은 사슬에서 (b, b+H) 짝으로 찾는다. 창대 = 창 하단 − F.L.
        F.L 글자가 없으면 사슬 폴백: 창 아래 구간이 정확히 두 개면 [S.L→F.L, F.L→창] 으로 읽는다(sill_basis=chain — 사람이 확인).
  실명(room)·품명(name) 등 카드 칸 글자는 --field 키=DY(기호 기준 세로 오프셋)로 읽는다. 오프셋은 --dump N 으로 한 장 보고
  정한다. 품명 줄(name)은 카드 안의 제품 이름 줄이다 — 시트 제목(도곽 표제)과 다르다.
  세대 형식은 시트 제목(--title-regex, 모든 시트 제목에 맞아야 한다; 그룹 unit/variant)에서. 제목은 도곽 우하단에 있다고 본다.
  w_consistent=false = 일련번호 W 와 카드 가로 치수 최대값이 다르다. 일련번호가 크기이고, 그런 카드는 눈으로 본다.

  정규식은 작은따옴표로(큰따옴표 속 '$' 는 Bash·PowerShell 이 바꾼다).
  python card_extract.py cards.dxf --list-symbols                                  # 부호 기호 블록 찾기
  python card_extract.py cards.dxf --symbol-block '<symbol block>' --dump 0        # dy 재기
  python card_extract.py cards.dxf --symbol-block '<symbol block>' --field room=<dy> --field name=<dy> \\
      --title-regex '\\((?P<unit>\\d+)㎡(?P<u2>\\w*) (?P<variant>\\S+)\\)' --out <work>/decl/cards.json
  python card_extract.py --selftest
"""
import argparse
import collections
import json
import re
import statistics
import sys

import dxfwalk as W

SERIAL = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*[xX×]\s*(\d+(?:\.\d+)?)\s*$")


def _tags(syms, kind_tag, serial_tag):
    """태그를 안 주면 값 모양으로 고른다: 종류 = 영문 1~4자, 일련번호 = 'AxB'."""
    if kind_tag and serial_tag:
        return kind_tag, serial_tag
    score = collections.defaultdict(lambda: [0, 0])
    for s in syms:
        for tag, v in s["attribs"].items():
            score[tag][0] += bool(re.fullmatch(r"[A-Z]{1,4}", v))
            score[tag][1] += bool(SERIAL.match(v))
    kind_tag = kind_tag or max(score, key=lambda t: score[t][0])
    serial_tag = serial_tag or max(score, key=lambda t: score[t][1])
    return kind_tag, serial_tag


def _spacing(syms):
    dx, dy = [], []
    for s in syms:
        r = [o["x"] - s["x"] for o in syms if abs(o["y"] - s["y"]) < 300 and o["x"] > s["x"] + 100]
        b = [s["y"] - o["y"] for o in syms if abs(o["x"] - s["x"]) < 1000 and o["y"] < s["y"] - 500]
        dx += [min(r)] if r else []
        dy += [min(b)] if b else []
    return (statistics.median(dx) if dx else 8000.0), (statistics.median(dy) if dy else 9000.0)


def _window(s, syms, mdx, mdy):
    right = [o["x"] for o in syms if abs(o["y"] - s["y"]) < 300 and o["x"] > s["x"] + 100]
    below = [o["y"] for o in syms if o["y"] < s["y"] - 500 and abs(o["x"] - s["x"]) < mdx / 2]
    x1 = min(min(right) - 500, s["x"] + 1.5 * mdx) if right else s["x"] + mdx - 500
    y0 = max(max(below) + 300, s["y"] - 1.5 * mdy) if below else s["y"] - mdy
    return s["x"] - 500, x1, y0, s["y"] + 300


def _pick_bottom(levels, H, fl):
    pairs = [b for b in levels if any(abs(t - b - H) <= 2 for t in levels)]
    if fl is not None:
        pairs = [b for b in pairs if b >= fl - 60] or pairs
    if pairs:
        return min(pairs), "pair"
    return (levels[-1] - H, "top-H") if levels else (None, None)


def extract(items, symbol_block, kind_tag=None, serial_tag=None, level_text="F.L", title_regex=None,
            sheet_regex=r"^[A-Z]{1,3}-\d{3,5}$", fields=None, fl_left=2500.0, serial_unit=100.0):
    syms = [i for i in items if i["k"] == "INSERT" and (i["t"] == symbol_block or re.fullmatch(symbol_block, i["t"]))]
    if not syms:
        sys.exit(f"기호 블록 {symbol_block!r} INSERT 가 없다")
    kind_tag, serial_tag = _tags(syms, kind_tag, serial_tag)
    syms.sort(key=lambda s: (-round(s["y"] / 300), s["x"]))
    mdx, mdy = _spacing(syms)
    vdims = [d for d in items if d["k"] == "DIM" and isinstance(d.get("m"), (int, float)) and abs(d["p2"][0] - d["p3"][0]) < 1
             and abs(abs(d["p2"][1] - d["p3"][1]) - d["m"]) < 1]
    hdims = [d for d in items if d["k"] == "DIM" and isinstance(d.get("m"), (int, float)) and abs(d["p2"][1] - d["p3"][1]) < 1
             and abs(abs(d["p2"][0] - d["p3"][0]) - d["m"]) < 1]
    fls = [t for t in items if t["k"] in ("TEXT", "MTEXT") and t["t"] == level_text]
    texts = [t for t in items if t["k"] in ("TEXT", "MTEXT") and t["t"]]
    titles = [t for t in texts if title_regex and re.search(title_regex, t["t"])]
    sheets = [t for t in texts if re.fullmatch(sheet_regex, t["t"])]
    cards = []
    for i, s in enumerate(syms):
        x0, x1, y0, y1 = _window(s, syms, mdx, mdy)
        inside = lambda p: x0 <= p[0] < x1 and y0 <= p[1] < y1
        levels = sorted({round(y) for d in vdims if inside(d["p2"]) for y in (d["p2"][1], d["p3"][1])})
        dimw = max((d["m"] for d in hdims if inside(d["p2"])), default=None)
        kind, serial = s["attribs"].get(kind_tag, ""), s["attribs"].get(serial_tag, "")
        m = SERIAL.match(serial)
        Wd, H = (float(m.group(1)) * serial_unit, float(m.group(2)) * serial_unit) if m else (None, None)
        fl = fl_txt = None
        if levels:
            # 글자는 **자기 선** 바로 위에 있다 → 이 카드 사슬에서 글자와 선 사이가 가장 좁은 글자를 고른다(같으면 가까운 것).
            # 옆 카드의 F.L 글자도 창 안에 들어온다(오른쪽 안쪽 · 왼쪽 2m 두 배치가 한 도면에 섞여 있었다).
            gap = lambda t: min((t["y"] - y for y in levels if y <= t["y"] - 100), default=None)
            cand = [t for t in fls if s["x"] - fl_left <= t["x"] < x1 and gap(t) is not None and gap(t) < 1000]
            if cand:
                fl_txt = min(cand, key=lambda t: (round(gap(t), -1), abs(t["x"] - s["x"])))
                fl = fl_txt["y"] - gap(fl_txt)
        bottom, bottom_basis = _pick_bottom(levels, H, fl) if H else (None, None)
        sill = basis = note = None
        if bottom is not None and fl is not None:
            sill, basis = float(bottom - fl), "F.L"
        elif bottom is not None:
            under = [y for y in levels if y < bottom - 1]
            if len(under) == 2:
                sill, basis = float(bottom - under[1]), "chain"
                note = f"F.L 글자 없음 — 사슬 [S.L→F.L {under[1] - under[0]:.0f} · F.L→창 {bottom - under[1]:.0f}] 으로 읽음"
        near = lambda dy: min((t for t in texts if abs(t["y"] - s["y"] - dy) < 150 and x0 - 500 <= t["x"] < x1),
                              key=lambda t: abs(t["x"] - s["x"]), default=None)
        f = {k: (near(dy) or {}).get("t") for k, dy in (fields or {}).items()}
        title = min((t for t in titles if t["x"] >= s["x"] - 2000 and t["y"] <= s["y"] + 3000),
                    key=lambda t: (t["x"] - s["x"]) + (s["y"] - t["y"]), default=None)
        unit = variant = None
        if title:
            tm = re.search(title_regex, title["t"])
            gd = tm.groupdict()
            unit = "".join(v or "" for k, v in gd.items() if k != "variant") if gd else "".join(g or "" for g in tm.groups())
            variant = gd.get("variant")
        sheet = min(sheets, key=lambda t: abs(t["x"] - title["x"]) + abs(t["y"] - title["y"]))["t"] if title and sheets else None
        cards.append({
            "idx": i, "kind": kind, "serial": serial, "mark": f"{kind} {serial}".strip(), "W": Wd, "H": H,
            "dimW": dimw, "w_consistent": (dimw is None or Wd is None or abs(dimw - Wd) < 1),
            "window_bottom_basis": bottom_basis, "sill": sill, "sill_basis": basis, "note": note,
            "chain": [b - a for a, b in zip(levels, levels[1:])],
            "fl_text_offset": [round(fl_txt["x"] - s["x"]), round(fl_txt["y"] - (fl or fl_txt["y"]))] if fl_txt else None,
            **f, "sheet_title": title["t"] if title else None, "unit": unit, "variant": variant, "sheet": sheet,
            "x": round(s["x"]), "y": round(s["y"]),
        })
    return cards, {"kind_tag": kind_tag, "serial_tag": serial_tag, "card_spacing": [mdx, mdy]}


def list_symbols(items, top=6):
    """부호 기호 블록 후보: ATTRIB 값이 '18x22' 꼴인 INSERT 이름을 쓰인 수로."""
    c, ex = collections.Counter(), {}
    for i in items:
        if i["k"] == "INSERT" and any(SERIAL.match(v) for v in i["attribs"].values()):
            c[i["t"]] += 1
            ex.setdefault(i["t"], i["attribs"])
    for name, n in c.most_common(top):
        print(f"{n:5d}  --symbol-block '{re.escape(name)}'  예 {ex[name]}")
    return c


def dump(items, symbol_block, n, radius):
    syms = sorted((i for i in items if i["k"] == "INSERT" and (i["t"] == symbol_block or re.fullmatch(symbol_block, i["t"]))),
                  key=lambda s: (-round(s["y"] / 300), s["x"]))
    s = syms[n]
    print("CARD", n, s["attribs"], round(s["x"]), round(s["y"]))
    near = [t for t in items if t is not s and abs(t["x"] - s["x"]) < radius and abs(t["y"] - s["y"]) < radius]
    for t in sorted(near, key=lambda t: (-round(t["y"] / 100), t["x"])):
        extra = f" m={t['m']:.0f} p2y={t['p2'][1] - s['y']:.0f} p3y={t['p3'][1] - s['y']:.0f}" if t["k"] == "DIM" else ""
        print(f"  {t['k']:6s} dx={t['x'] - s['x']:7.0f} dy={t['y'] - s['y']:7.0f} {t['t']!r:.50} [{t['l']}]{extra}")


def selftest():
    doc = W.new_doc()
    sym = doc.blocks.new("MARKSYM")
    sym.add_circle((0, 0), 300)
    sym.add_attdef("KND", (0, 0))
    sym.add_attdef("SER", (0, 300))
    msp = doc.modelspace()

    def card(x, y, kind, ser):
        r = msp.add_blockref("MARKSYM", (x, y))
        r.add_auto_attribs({"KND": kind, "SER": ser})

    def vdim(x, ya, yb):
        msp.add_linear_dim(base=(x + 300, 0), p1=(x, ya), p2=(x, yb), angle=90).render()

    card(0, 0, "PW", "18x22")                         # F.L 글자가 선보다 220 위, 카드 상자보다 1.1m 왼쪽
    for ya, yb in ((-4800, -4560), (-4560, -2360)):
        vdim(2400, ya, yb)
    msp.add_linear_dim(base=(0, -1000), p1=(0, -1200), p2=(1800, -1200)).render()
    msp.add_text("F.L").set_placement((-1600, -4580))
    msp.add_text("침실2").set_placement((0, -6700))
    card(6500, 0, "PW", "36x22")                      # F.L 글자 없음 → 사슬 [120 · 240 · 창]
    for ya, yb in ((-4920, -4800), (-4800, -4560), (-4560, -2360)):
        vdim(8900, ya, yb)
    card(0, -9000, "PD", "9x24")                   # 둘째 줄: 여닫이 문(창대 0)
    vdim(2400, -13800, -11400)
    msp.add_text("F.L").set_placement((-1600, -13580))
    msp.add_text("(59㎡B 기본)").set_placement((12000, -20000))
    msp.add_text("Q-101").set_placement((12000, -20500))
    items = W.collect(msp)
    assert list(list_symbols(items)) == ["MARKSYM"]
    cards, meta = extract(items, "MARKSYM", fields={"room": -6700},
                          title_regex=r"\((?P<unit>\d+)㎡(?P<u2>\w*) (?P<variant>\S+)\)")
    by = {c["mark"]: c for c in cards}
    assert meta["kind_tag"] == "KND" and meta["serial_tag"] == "SER", meta
    a = by["PW 18x22"]
    assert (a["W"], a["H"], a["dimW"], a["sill"], a["sill_basis"]) == (1800, 2200, 1800, 240, "F.L"), a
    assert a["room"] == "침실2" and a["unit"] == "59B" and a["variant"] == "기본" and a["sheet"] == "Q-101", a
    b = by["PW 36x22"]
    assert (b["sill"], b["sill_basis"]) == (240, "chain") and "120" in b["note"], b
    assert by["PD 9x24"]["sill"] == 0, by["PD 9x24"]
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dxf", nargs="?")
    ap.add_argument("--symbol-block", help="부호 기호 블록 이름(또는 전체 일치 정규식)")
    ap.add_argument("--kind-tag"), ap.add_argument("--serial-tag")
    ap.add_argument("--level-text", default="F.L")
    ap.add_argument("--title-regex", help="시트 제목 정규식(그룹 unit·variant) — 모든 시트 제목에 맞아야 한다")
    ap.add_argument("--sheet-regex", default=r"^[A-Z]{1,3}-\d{3,5}$", help="도면번호 글자")
    ap.add_argument("--field", action="append", default=[], metavar="NAME=DY", help="기호 기준 DY(mm) 줄의 글자 → 필드")
    ap.add_argument("--fl-left", type=float, default=2500.0)
    ap.add_argument("--serial-unit", type=float, default=100.0, help="일련번호 한 칸의 mm")
    ap.add_argument("--dump", type=int, metavar="N", help="N 번째 카드 주변 글자·치수를 기호 기준으로 찍고 끝낸다")
    ap.add_argument("--dump-radius", type=float, default=9000.0)
    ap.add_argument("--list-symbols", action="store_true", help="부호 기호 블록 후보(ATTRIB 값이 'AxB')를 찍고 끝낸다")
    ap.add_argument("--out", help="출력 json — 저장소 밖(<작업 폴더>/decl/cards.json)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.dxf:
        ap.error("dxf 필요")
    if not (a.list_symbols or a.symbol_block):
        ap.error("--symbol-block 필요(모르면 --list-symbols)")
    if not (a.list_symbols or a.dump is not None or a.out):
        ap.error("--out 필요(저장소 밖)")
    out = W.out_path(a.out) if a.out else None
    items = W.collect(W.ezdxf.readfile(a.dxf).modelspace())
    if a.list_symbols:
        return list_symbols(items)
    if a.dump is not None:
        return dump(items, a.symbol_block, a.dump, a.dump_radius)
    fields = {k: float(v) for k, v in (f.split("=", 1) for f in a.field)}
    cards, meta = extract(items, a.symbol_block, a.kind_tag, a.serial_tag, a.level_text, a.title_regex,
                          a.sheet_regex, fields, a.fl_left, a.serial_unit)
    json.dump({"source": a.dxf, "meta": meta, "cards": cards}, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("cards", len(cards), "sill", collections.Counter(c["sill_basis"] for c in cards),
          "W 불일치(눈으로 볼 카드)", sum(not c["w_consistent"] for c in cards), "형식", len({c["unit"] for c in cards}),
          W.drops(), "->", out)


if __name__ == "__main__":
    main()
