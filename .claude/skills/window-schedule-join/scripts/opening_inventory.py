"""평면의 블록 개구부(창·문 INSERT) 전수 — 폭 근거 · 세대 형식 · 가까운 실명. 읽기 전용.

고르는 INSERT: 레이어 꼬리가 --opening-layers 에 맞거나, 블록 이름이 블록 규칙(기본 = 파서의 DEFAULT_BLOCK_RULES,
또는 --block-map)으로 opening 이 되는 것 — 파서가 개구부로 보는 그 집합이다.

폭 근거(우선순위 순, 전부 기록한다):
  label       블록 정의(중첩 포함) 안의 'W:1,800' 글자 — 인쇄 안 되는 층에 둔 폭 표기
  metre       블록 정의 안의 미터 글자 '0.7' → 700 (동적 문 블록. virtual_entities() 는 이 글자를 건너뛴다)
  name        파서와 같은 규칙(dxf_parser._width_from_block_name: 이름 끝 '-1200')
  name_loose  이름 어디든 3~4자리 수('WIN-1200(X)_A' → 1200) — 연도·코드일 수 있다. 검토표가 '낮음'으로 표시
  extent      블록 로컬 X 범위(프레임 바깥까지라 조금 크고, 문은 스윙 호까지 들어가 크게 틀린다)

정규식은 **작은따옴표**로 준다 — 큰따옴표면 Bash 는 '\\$0\\$' 를 '$0$' 로 바꾸고 PowerShell 은 '$0' 을 변수로
읽어, 아무 이름에도 안 맞는다(그러면 이 스크립트가 멈춘다).

  python opening_inventory.py plan.dxf --list-room-blocks                    # 실명 라벨 블록·레이어 찾기
  python opening_inventory.py plan.dxf --unit-regex '^[^$]*-(\\d+[A-Z]?)-[^$]*\\$0\\$' \\
      --room-block '<room label block>' --room-layer '<one text-size layer>$' --out <work>/decl/inv.json
  python opening_inventory.py --selftest
"""
import argparse
import collections
import json
import math
import re
import sys
import time

import dxfwalk as W
import dxf_parser as P

NAME_LOOSE = re.compile(r"(?<!\d)(\d{3,4})(?!\d)")
HANGUL_SHORT = re.compile(r"^[가-힣][가-힣0-9\-()/·. ]{0,11}$")
LABEL_W = re.compile(r"^W\s*[:=]\s*([\d,]+(?:\.\d+)?)", re.I)
METRE_W = re.compile(r"^\d\.\d{1,3}$")
ANON = re.compile(r"^(\*[A-Z]\d+|A\$C[0-9A-Fa-f]+)$")                 # *U<n> · bind 된 익명 블록
DOOR_HINT = re.compile(r"DOOR|문|도어|^D[_\-]", re.I)


def width_evidence(base, texts, ext):
    ev = {}
    for t in texts:
        m = LABEL_W.match(t or "")
        if m:
            ev["label"] = [float(m.group(1).replace(",", "")), t]
            break
    for t in texts:
        if METRE_W.match(t or "") and 0.3 <= float(t) <= 6.0:
            ev["metre"] = [float(t) * 1000.0, t]
            break
    if not ANON.match(base):                                          # 익명 블록 번호는 폭이 아니다
        w = P._width_from_block_name(base)
        if w:
            ev["name"] = [w, base]
        for m in NAME_LOOSE.finditer(base):
            if 300 <= int(m.group(1)) <= 6000:
                ev["name_loose"] = [float(m.group(1)), base]
                break
    if ext[0][1] > ext[0][0]:
        ev["extent"] = [float(ext[0][1] - ext[0][0]), "로컬 X 범위"]
    for k in ("label", "metre", "name", "name_loose", "extent"):
        if k in ev:
            return ev, ev[k][0], k
    return ev, None, None


def dyn_name(doc, name):
    """*U 익명 블록 → 원래 동적 블록 이름(AcDbBlockRepBTag). 없으면 None."""
    try:
        xd = doc.blocks.get(name).block_record.get_xdata("AcDbBlockRepBTag")
        h = [v for c, v in xd if c == 1005][0]
        return doc.entitydb[h].dxf.name
    except Exception:
        return None


def room_labels(doc, msp, block_re, layer_re, exclude_re):
    """실명 라벨(WCS). --room-block 이 있으면 그 블록 안 글자, 없으면 모델공간 글자. --room-layer 로 한 글자 크기만."""
    out = []
    if block_re:
        texts = [t for e in msp.query("INSERT") if re.search(block_re, e.dxf.name) for t in W.block_contents(doc, e)[1]]
    else:
        texts = [t for t in W.collect(msp.query("TEXT MTEXT"), max_depth=0)]
    for t in texts:
        s = (t["t"] or "").replace("\n", "").strip()
        if not s or (layer_re and not re.search(layer_re, t["l"])) or (exclude_re and re.fullmatch(exclude_re, s)):
            continue
        out.append((s, (t["x"], t["y"])))
    return out


def list_room_blocks(path, top=8):
    """실명 라벨 블록 후보: 짧은 한글 글자가 많은 블록 — 레이어별 개수·글자 높이(크기별 사본을 가를 때)."""
    doc = ezdxf_read(path)
    seen, out = set(), []
    for e in doc.modelspace().query("INSERT"):
        if e.dxf.name in seen:
            continue
        seen.add(e.dxf.name)
        ts = [t for t in W.block_contents(doc, e, pts=False)[1] if HANGUL_SHORT.match(t["t"])]
        if len(ts) >= 5:
            by = collections.defaultdict(list)
            for t in ts:
                by[t["l"]].append((round(t["h"] or 0, 1), t["t"]))
            out.append((len(ts), e.dxf.name, by))
    for n, name, by in sorted(out, key=lambda r: -r[0])[:top]:
        print(f"{n:5d}  --room-block '^{re.escape(name)}$'")
        for l, hs in by.items():
            print(f"        --room-layer '{re.escape(l)}$'  글자 {len(hs)} · 높이 {sorted({h for h, _ in hs})[:4]} · 예 {hs[0][1]!r}")
    print(W.drops())
    return out


def inventory(path, opening_layers, block_rules, unit_re, room_block=None, room_layer=None,
              room_exclude=r"PS|AD|EPS|TPS|DS"):
    doc = ezdxf_read(path)
    msp = doc.modelspace()
    labels = room_labels(doc, msp, room_block, room_layer, room_exclude) if (room_block or room_layer) else []
    groups = collections.OrderedDict()
    for e in msp.query("INSERT"):
        name, layer = e.dxf.name, e.dxf.layer
        by_layer = bool(re.search(opening_layers, W.tail(layer), re.I)) if opening_layers else False
        by_block = P.classify(name, block_rules)[0] == "opening"
        if not (by_layer or by_block):
            continue
        ip = e.dxf.insert
        key = (name, round(ip.x), round(ip.y))
        if key in groups:                     # 같은 이름·같은 삽입점 = 쌓인 중복(자르기 산출물). 물리 개구부는 하나
            groups[key]["stacked"] += 1
            continue
        ax = W.local_axes(e)
        pts, texts = W.block_contents(doc, e)
        ext = []
        for ux, uy in ax:
            ts = [(p.x - ip.x) * ux + (p.y - ip.y) * uy for p in pts] or [0.0]
            ext.append([round(min(ts), 1), round(max(ts), 1)])
        mx, my = (ext[0][0] + ext[0][1]) / 2, (ext[1][0] + ext[1][1]) / 2
        centre = [round(ip.x + ax[0][0] * mx + ax[1][0] * my, 1), round(ip.y + ax[0][1] * mx + ax[1][1] * my, 1)]
        base = W.tail(name)
        ev, width, wsrc = width_evidence(base, [t["t"] for t in texts], ext)
        m = re.search(unit_re, name) if unit_re else None
        dyn = dyn_name(doc, name) if name.startswith("*") else None
        near = sorted(((s, round(math.dist(p, centre))) for s, p in labels), key=lambda r: r[1])
        seen, rooms = set(), []
        for s, d in near:                     # 서로 다른 실명 두 개까지
            if s not in seen:
                seen.add(s)
                rooms.append([s, d])
            if len(rooms) == 2:
                break
        groups[key] = {
            "handle": e.dxf.handle, "name": name, "base": base, "unit": m.group(1) if m else None,
            "layer": layer, "role": W.tail(layer), "dyn": dyn,
            "side": "door" if DOOR_HINT.search(W.tail(layer)) or DOOR_HINT.search(dyn or base) else "window",
            "reason": "+".join(k for k, v in (("layer", by_layer), ("block_rule", by_block)) if v),
            "p": [round(ip.x, 1), round(ip.y, 1)], "axes": ax, "rotation": e.dxf.get("rotation", 0),
            "ext": ext, "centre": centre, "width": width, "width_src": wsrc, "width_evidence": ev,
            "rooms": rooms, "stacked": 1,
        }
    return list(groups.values())


def ezdxf_read(path):
    t0 = time.time()
    doc = W.ezdxf.readfile(path)
    print(f"  읽기 {time.time() - t0:.0f}s", file=sys.stderr)
    return doc


def summary(rows):
    return {"unique": len(rows), "inserts": sum(r["stacked"] for r in rows),
            "reason": dict(collections.Counter(r["reason"] for r in rows)),
            "width_src": dict(collections.Counter(r["width_src"] for r in rows)),
            "unit": dict(collections.Counter(r["unit"] for r in rows)),
            "side": dict(collections.Counter(r["side"] for r in rows)),
            "with_room": sum(1 for r in rows if r["rooms"])}


def selftest():
    import tempfile, os
    doc = W.new_doc()
    doc.layers.add("X$0$A-WIN")
    doc.layers.add("ROOMS@M")
    inner = doc.blocks.new("INNER")                         # 폭 라벨이 중첩 블록 안에 있다
    inner.add_line((0, 0), (1800, 0))
    inner.add_text("W:1,800", dxfattribs={"layer": "DEFPOINTS"})
    win = doc.blocks.new("U-59B-V$0$WINBLK")
    win.add_blockref("INNER", (0, 0))
    win.add_line((0, 150), (1800, 150))
    door = doc.blocks.new("*U7")                            # 동적 문: 미터 글자
    door.add_line((0, 0), (700, 0))
    door.add_arc((0, 0), 700, 0, 90)
    door.add_text("0.7")
    rl = doc.blocks.new("ROOMLABELS")
    rl.add_text("침실-2", dxfattribs={"layer": "ROOMS@M"}).set_placement((900, 1500))
    rl.add_text("침실-2", dxfattribs={"layer": "ROOMS@L"}).set_placement((900, 1500))
    rl.add_text("PS", dxfattribs={"layer": "ROOMS@M"}).set_placement((900, 400))
    for i, s_ in enumerate(("거실", "주방", "현관", "욕실")):
        rl.add_text(s_, dxfattribs={"layer": "ROOMS@M"}).set_placement((9000 + 500 * i, 9000))
    msp = doc.modelspace()
    msp.add_blockref("U-59B-V$0$WINBLK", (1000, 0), dxfattribs={"layer": "X$0$A-WIN", "rotation": 90})
    msp.add_blockref("U-59B-V$0$WINBLK", (1000, 0), dxfattribs={"layer": "X$0$A-WIN", "rotation": 90})  # 쌓인 중복
    msp.add_blockref("*U7", (5000, 0), dxfattribs={"layer": "A-DOOR"})
    msp.add_blockref("ROOMLABELS", (0, 0))
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.dxf")
        doc.saveas(p)
        rows = inventory(p, r"A-(WIN|DOOR)$", P.DEFAULT_BLOCK_RULES, r"^[^$]*-(\d+[A-Z]?)-[^$]*\$0\$",
                         room_block="ROOMLABELS", room_layer=r"@M$")
        found = list_room_blocks(p)
        assert [r[1] for r in found] == ["ROOMLABELS"] and set(found[0][2]) == {"ROOMS@M", "ROOMS@L"}, found
    by = {r["base"]: r for r in rows}
    w = by["WINBLK"]
    assert len(rows) == 2 and w["stacked"] == 2, rows
    assert w["unit"] == "59B" and w["width"] == 1800 and w["width_src"] == "label", w
    assert abs(w["centre"][0] - 925) < 1 and abs(w["centre"][1] - 900) < 1, w["centre"]   # 회전 90: 로컬 X = WCS Y
    assert [r[0] for r in w["rooms"]] == ["침실-2", "거실"], w["rooms"]       # PS(더 가깝다) 제외 · @L 사본 제외
    d = by["*U7"]
    assert d["width"] == 700 and d["width_src"] == "metre" and d["side"] == "door", d
    assert width_evidence("W-1200", [], [[0, 1250], [0, 100]])[1:] == (1200, "name")          # 파서 규칙
    assert width_evidence("WIN_2019", [], [[0, 1200], [0, 100]])[1:] == (2019, "name_loose")   # 느슨한 수 — 낮은 확신
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dxf", nargs="?")
    ap.add_argument("--opening-layers", default=r"A-(WIN|DOOR)$", help="레이어 꼬리('$0$' 뒤) 정규식")
    ap.add_argument("--block-map", help="블록 규칙 CSV(없으면 파서 DEFAULT_BLOCK_RULES)")
    ap.add_argument("--unit-regex", help="INSERT 전체 이름에서 세대 형식을 잡는 정규식(그룹 1)")
    ap.add_argument("--room-block", help="실명 라벨 블록 이름 정규식")
    ap.add_argument("--room-layer", help="실명 글자 레이어 정규식 — 글자 크기별 사본 중 하나만")
    ap.add_argument("--room-exclude", default=r"PS|AD|EPS|TPS|DS", help="실명이 아닌 라벨(전체 일치)")
    ap.add_argument("--out", help="출력 json — 저장소 밖(<작업 폴더>/decl/inv.json)")
    ap.add_argument("--list-room-blocks", action="store_true", help="실명 라벨 블록·레이어 후보를 찍고 끝낸다")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.dxf:
        ap.error("dxf 필요")
    if a.list_room_blocks:
        return list_room_blocks(a.dxf)
    if not a.out:
        ap.error("--out 필요(저장소 밖)")
    out = W.out_path(a.out)
    rules = P.load_layer_map(a.block_map) if a.block_map else P.DEFAULT_BLOCK_RULES
    rows = inventory(a.dxf, a.opening_layers, rules, a.unit_regex, a.room_block, a.room_layer, a.room_exclude)
    s = summary(rows)
    print(json.dumps(s, ensure_ascii=False))
    print(W.drops())
    # 셸이 인자를 망가뜨려도(큰따옴표 속 $) 조용히 넘어가지 않는다 — 망가진 인벤토리는 세대 블록을 전부 공용으로 만든다
    if a.unit_regex:
        n = sum(1 for r in rows if r["unit"])
        print(f"--unit-regex {a.unit_regex!r}: {n}/{len(rows)} 개 이름에 맞음")
        if not n:
            sys.exit("--unit-regex 가 아무 이름에도 안 맞는다(작은따옴표로 줬나?). 이름 예: "
                     + "; ".join(r["name"] for r in rows[:5]))
    if (a.room_block or a.room_layer) and not s["with_room"]:
        sys.exit("실명 라벨이 하나도 안 잡혔다 — --list-room-blocks 로 블록·레이어를 확인한다")
    json.dump({"source": a.dxf, "args": vars(a), "summary": s, "inserts": rows},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("->", out)


if __name__ == "__main__":
    main()
