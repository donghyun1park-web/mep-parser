"""블록 개구부 → 창호일람 조인 **제안**(사람이 확인할 선언). 자동 적용하지 않는다.

입력: opening_inventory.json · cards.json(card_extract) · [doors.json(door_table)]
출력(--out-dir):
  block_map_proposal.csv  세대 형식별 블록 이름 행(구체적인 것 먼저) + 맨 아래 기본 블록 규칙 사본.
                          height 는 **비운다**(block_map 의 height 는 overrides 로 가서 창호일람 높이를 이긴다).
                          mark= 는 후보가 정확히 하나일 때만.
  schedule_rows.json      부호별 W·H·창대·종류. 창대를 모르면 null 을 **명시**한다(키를 빼면 xlsx 가 900 을 채운다).
  review_table.md         한 줄 = 한 블록 형식. 근거·후보·확신. 사람이 이 표로 확인한다.

카드 고르기(같은 세대 형식 안에서, 순서대로 좁힌다 — 하나 남으면 지정, 아니면 미결):
  ① |평면 폭 − 카드 W| ≤ --width-tol  ② 창/문 일치(--either-kinds 는 둘 다 허용: 창 블록으로 그린 미서기문)
  ③ 블록 이름 토큰이 카드 종류·실명·품명에 있으면 그것만('실외기'→실외기실, 'FSD'→FSD)
  ④ 가까운 실명 라벨(--room-max 안, 가까운 두 개)이 카드 실명과 맞는 것 — 인스턴스 전부가 맞아야 한다
공용(세대 형식 없음) 블록: 문 일람표 행 중 폭이 맞는 부호. 여럿이면 평면 개수 = 일람표 행 수인 부호.
  H1(바닥→문 아래) > 0 인 문은 **창으로 선언**한다 — 파서·빌더는 문의 문턱을 무시하고 바닥부터 뚫는다.
같은 부호가 창대만 다르게 두 번 쓰이면(발코니 쪽·침실 쪽) 흔한 쪽이 맨 부호, 나머지는 '부호@실명'
(실명이 없거나 같아 한정이 겹치면 '부호@실명-창대', 그래도 겹치면 미결).
미결 행: 폭 근거가 믿을 만하면(label·metre·name) 폭·종류만 선언한다. 범위(extent)·이름 속 숫자(name_loose)뿐이면
**주석으로만** 낸다 — 문 범위는 스윙 호까지 들어 크게 틀리고, 틀린 폭 선언은 선언이 없는 것보다 나쁘다.

  python propose_block_map.py --inventory <work>/decl/inv.json --cards <work>/decl/cards.json \\
      --doors <work>/decl/doors.json --card-variant '<variant>' --unit-alias 59X=59 --either-kinds PD \\
      --out-dir <work>/decl
  python propose_block_map.py --selftest
"""
import argparse
import collections
import csv
import io
import json
import os
import re

import dxfwalk as W
import dxf_parser as P
from schedule_io import _kind_label_to_subtype

ANON = re.compile(r"^(\*[A-Z]\d+|A\$C[0-9A-Fa-f]+)$")
WEAK_WIDTH = ("extent", "name_loose")                 # 폭이 틀릴 수 있는 근거 — 미결이면 선언하지 않는다


def subtype_of(kind):
    return _kind_label_to_subtype("", kind)          # 저장소 규약: 접두가 종류다(PW·AG→창, 그 밖→문)


def tokens(base):
    if ANON.match(base):
        return []
    out = set(re.findall(r"[A-Za-z]{2,}", base)) - {"WIN", "WINDOW", "DOOR", "DR"}
    for h in re.findall(r"[가-힣]{2,}", base):
        out |= {h, h[:2]}
    return sorted(out)


def rooms_of(s):
    return [W.norm_room(x) for x in re.split(r"[/,]", s or "") if W.norm_room(x)]


def room_rank(inst, card, room_field, room_max):
    rs = rooms_of(card.get(room_field))
    for i, (lab, d) in enumerate(inst["rooms"]):
        n = W.norm_room(lab)
        if d <= room_max and any(r in n or n in r for r in rs):
            return i
    return None


def dedupe(cards, room_field):
    """같은 형식의 같은 카드가 여러 시트에 있다 → (부호, H, 창대, 실명) 하나로."""
    out = collections.OrderedDict()
    for c in cards:
        k = (c["mark"], c["H"], c["sill"], W.norm_room(c.get(room_field)))
        out.setdefault(k, dict(c, sheets=[]))["sheets"].append(c.get("sheet"))
    return list(out.values())


def choose(group, cards, a):
    i0 = group[0]
    either = set(a.either_kinds)
    cs = [c for c in cards if c["W"] and abs(c["W"] - i0["width"]) <= a.width_tol
          and (c["kind"] in either or subtype_of(c["kind"]) == i0["side"])]
    ev = [f"폭 {i0['width']:.0f}±{a.width_tol:.0f}({i0['width_src']})", "창/문"]
    toks = tokens(i0["base"])
    hit = [c for c in cs if any(t in f"{c['kind']} {c.get(a.room_field) or ''} {c.get(a.name_field) or ''}" for t in toks)]
    if len(cs) > 1 and hit and len(hit) < len(cs):
        cs, ev = hit, ev + ["이름 토큰 " + "·".join(t for t in toks if any(t in f"{c['kind']} {c.get(a.room_field)} {c.get(a.name_field)}" for c in hit))]
    if len(cs) > 1:
        ranks = {id(c): [room_rank(i, c, a.room_field, a.room_max) for i in group] for c in cs}
        ok = [c for c in cs if None not in ranks[id(c)]]
        if ok:
            best = min(sum(ranks[id(c)]) for c in ok)
            k = [c for c in ok if sum(ranks[id(c)]) == best]
            if len(k) < len(cs):
                cs, ev = k, ev + ["실명 " + ", ".join(f"{i['rooms'][0][0]} {i['rooms'][0][1]}mm" for i in group if i["rooms"])]
    same = [c for c in cs if subtype_of(c["kind"]) == i0["side"]]
    if len(cs) > 1 and same and len(same) < len(cs):   # --either-kinds 는 허용일 뿐 — 같은 쪽 종류가 있으면 그쪽
        cs, ev = same, ev + ["종류가 블록 쪽(창/문)과 같은 것 우선"]
    return cs, ev


def choose_door(group, rows, all_common, a):
    i0 = group[0]
    marks = collections.defaultdict(list)
    for r in rows:
        if r["W"] and abs(r["W"] - i0["width"]) <= a.width_tol:
            marks[r["MARK"]].append(r)
    ev = [f"폭 {i0['width']:.0f}±{a.width_tol:.0f}({i0['width_src']})"]
    if len(marks) > 1:
        n = len([i for i in all_common if i["width"] and abs(i["width"] - i0["width"]) <= a.width_tol])
        k = [m for m, rs in marks.items() if len(rs) == n]
        if len(k) == 1:
            marks, ev = {k[0]: marks[k[0]]}, ev + [f"개수 일치: 평면 {n} = 일람표 {k[0]} {n}행"]
    return marks, ev


def propose(inv, cards, doors, a):
    alias = dict(x.split("=", 1) for x in a.unit_alias)
    cards = [c for c in cards if c.get("unit") and (not a.card_variant or re.search(a.card_variant, c.get("variant") or ""))]
    by_unit = collections.defaultdict(list)
    for c in cards:
        by_unit[c["unit"]].append(c)
    groups = collections.OrderedDict()
    for i in inv:
        groups.setdefault(i["name"], []).append(i)
    common = [i for i in inv if not i["unit"]]
    out = []
    for name, g in groups.items():
        i0 = g[0]
        rec = {"name": name, "unit": i0["unit"], "base": i0["base"], "role": i0["role"], "count": len(g),
               "stacked": sum(i["stacked"] for i in g), "width": i0["width"], "width_src": i0["width_src"],
               "side": i0["side"], "rooms": [i["rooms"][:1] for i in g], "cands": [], "ev": [], "pick": None, "status": "미결"}
        if not i0["width"] or i0["width"] > a.max_width:
            rec["status"] = f"검토 — 폭 {i0['width']} (> {a.max_width:.0f}): 창 하나가 아닐 수 있다"
        elif i0["unit"]:
            ucards = dedupe(by_unit.get(alias.get(i0["unit"], i0["unit"]), []), a.room_field)
            if not ucards:
                rec["status"] = f"미결 — 형식 {i0['unit']} 카드 없음(--unit-alias?)"
            else:
                cs, rec["ev"] = choose(g, ucards, a)
                rec["cands"] = [{"src": "card", **c} for c in cs]
        elif doors:
            marks, rec["ev"] = choose_door(g, doors, common, a)
            rec["cands"] = [{"src": "doors", "mark": m, "rows": rs} for m, rs in marks.items()]
        if len(rec["cands"]) == 1:
            rec["pick"], rec["status"] = rec["cands"][0], "지정"
        elif not rec["cands"] and rec["status"] == "미결" and i0["unit"]:
            ucs = dedupe(by_unit.get(alias.get(i0["unit"], i0["unit"]), []), a.room_field)
            if i0["side"] == "door":        # 문 폭 근거(범위)는 스윙 호까지 들어 크게 틀린다 — 폭순이 아니라 문 카드 전부
                near, lab = [c for c in ucs if subtype_of(c["kind"]) == "door" or c["kind"] in a.either_kinds], "형식의 문 카드 전부"
            else:
                near, lab = sorted(ucs, key=lambda c: abs((c["W"] or 0) - (i0["width"] or 0)))[:3], "폭 가까운 카드"
            rec["hint"] = lab + ": " + " / ".join(f"{c['mark']}({c.get(a.room_field)}, W {c['W']:.0f})" for c in near if c["W"])
        out.append(rec)
    return out, specs(out, a)


def specs(rows, a):
    """지정 행 → 부호별 일람 행. 창대만 다른 같은 부호는 흔한 쪽이 맨 부호."""
    for r in rows:
        p = r["pick"]
        if not p:
            continue
        if p["src"] == "card":
            sub = subtype_of(p["kind"])
            sill = p["sill"]
            note = [p["note"]] if p.get("note") else []
            if p.get("w_consistent") is False:
                note += [f"★ 카드 가로 치수 {p['dimW']:.0f} ≠ 일련번호 W {p['W']:.0f} — 일련번호가 크기다. 카드를 눈으로 본다"]
            if sub == "door" and sill is not None and abs(sill) <= 50:
                note += [f"문 — 바닥부터(카드 {sill:.0f})"] if sill else []
                sill = 0.0
            r["spec"] = {"mark": p["mark"], "subtype": sub, "width": p["W"], "height": p["H"], "sill": sill,
                         "room": W.norm_room((rooms_of(p.get(a.room_field)) or [""])[0]) or r["unit"],
                         "src": f"카드 {'/'.join(s for s in p['sheets'] if s)} {p.get('sheet_title') or ''} {p.get(a.room_field) or ''} · "
                                f"{p.get(a.name_field) or ''}".strip(), "note": note}
        else:
            r0 = p["rows"][0]
            h1 = r0.get("H1")
            raised = isinstance(h1, (int, float)) and h1 > 0
            r["spec"] = {"mark": p["mark"], "subtype": "window" if raised else subtype_of(p["mark"]), "width": r0["W"],
                         "height": r0["H"], "sill": float(h1) if raised else 0.0, "room": r["unit"] or "공용",
                         "src": f"문 일람표 {r0['FLOOR']} NO {', '.join(x['NO'] for x in p['rows'])}",
                         "note": [f"H1={h1:.0f} — 바닥에서 떠 있는 문. 도구가 문의 문턱을 무시하므로 창으로 선언(IFC 에서 IfcWindow)"]
                         if raised else [f"★ 범례 H1 값이 여럿 {h1} — 확인" if isinstance(h1, list) else "문 — 바닥부터(H1 없음)"]}
    by = collections.defaultdict(list)
    for r in rows:
        if r.get("spec"):
            by[r["spec"]["mark"]].append(r)
    for m, rs in by.items():
        vals = collections.Counter((r["spec"]["height"], r["spec"]["sill"]) for r in rs)
        main = vals.most_common(1)[0][0]
        seen = {}
        for r in rs:
            s = r["spec"]
            key = (s["height"], s["sill"])
            f = m if key == main else f"{m}@{s['room']}"
            if seen.setdefault(f, key) != key:           # 실명이 없거나 같아 한정이 겹친다 → 창대까지
                f = f"{f}-{'?' if s['sill'] is None else format(s['sill'], '.0f')}"
                if seen.setdefault(f, key) != key:
                    r["status"], r["spec"] = "미결 — 부호 한정이 겹친다(실명·창대가 같고 높이만 다르다)", None
                    continue
            s["final"] = f
    sched = collections.OrderedDict()
    for r in rows:
        s = r.get("spec")
        if not s:
            continue
        row = sched.setdefault(s["final"], {"mark": s["final"], "subtype": s["subtype"], "width": s["width"],
                                            "height": s["height"], "sill": s["sill"], "count": 0, "remarks": []})
        row["count"] += r["count"]
        row["remarks"].append(f"{s['src']} · 평면 {r['unit'] or '공용'} {r['base']}×{r['count']}")
        row["remarks"] += [n for n in s["note"] if n not in row["remarks"]]
    out = []
    for row in sched.values():
        if row["sill"] is None:
            row["remarks"].append("창대 근거 없음 → 파서가 가정하고 dims_assumed 로 보고한다")
        out.append(dict(row, remarks="; ".join(row["remarks"])))
    return out


def write(rows, sched, a):
    os.makedirs(a.out_dir, exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    buf.write("pattern,category,width,height,thickness,opts\n")
    buf.write("# ★ 제안이다 — review_table.md 를 사람이 확인하기 전에는 쓰지 않는다. 행마다 mark 는 사람이 확인할 선언이다.\n"
              "# height 는 비워 둔다(block_map height → overrides → 창호일람 높이를 이긴다). 높이·창대는 창호일람(mark 조인)에서.\n"
              "# 기본 블록 규칙을 **대체**하므로 맨 아래에 그대로 옮겨 두었다 — 다른 블록의 분류가 바뀌지 않게.\n")
    for unit in sorted({r["unit"] or "" for r in rows}, key=lambda u: (u == "", u)):
        buf.write(f"# ── {'세대 ' + unit if unit else '공용(세대 형식 없음)'}\n")
        for r in [r for r in rows if (r["unit"] or "") == unit]:
            spec = r.get("spec")
            why = ("지정 " + spec["final"]) if spec else (r["status"] + (" — 후보 " + " / ".join(c["mark"] for c in r["cands"]) if r["cands"] else ""))
            buf.write(f"# {r['base']} ×{r['count']} ({r['role']}; 폭 {r['width_src']}) → {why}"
                      + (" ★ *U 이름은 DWG→DXF 변환마다 바뀐다 — 다시 변환하면 이 행을 새로 만든다" if r["name"].startswith("*") else "") + "\n")
            line = ["^" + re.escape(r["name"]) + "$", "opening"]
            if r["status"].startswith("검토"):             # 설명은 윗줄에 — 주석을 풀면 그대로 CSV 행이 되게
                buf.write("# ↓ 제외하려면 사람이 아래 줄의 '# ' 를 지운다\n# " + ",".join(line[:1] + ["ignore", "", "", "", ""]) + "\n")
                continue
            sub = spec["subtype"] if spec else r["side"]
            opts = f"subtype={sub}" + (f";mark={spec['final']}" if spec else "")
            row = line + [f"{(spec['width'] if spec else r['width']):.0f}", "", "", opts]
            if not spec and r["width_src"] in WEAK_WIDTH:
                buf.write(f"# ↓ 미결: 폭 근거가 {r['width_src']} 뿐 — 사람이 폭·부호를 정한 뒤 아래 줄의 '# ' 를 지운다\n"
                          "# " + ",".join(row) + "\n")
                continue
            w.writerow(row)
    buf.write("# ── 기본 블록 규칙 — 위 행에 안 걸린 블록은 오늘과 같은 분류를 받는다\n")
    if a.default_block_rules:
        with open(a.default_block_rules, encoding="utf-8") as f:
            buf.writelines(l if l.endswith("\n") else l + "\n" for l in list(f)[1:])
    else:
        for pat, cat, _ in P.DEFAULT_BLOCK_RULES:
            w.writerow([pat, cat, "", "", "", ""])
    csv_path = os.path.join(a.out_dir, "block_map_proposal.csv")
    open(csv_path, "w", encoding="utf-8", newline="").write(buf.getvalue())
    P.load_layer_map(csv_path)                        # 파서가 읽는지 바로 확인(모르는 opts·정규식 오류는 여기서 죽는다)
    json.dump(sched, open(os.path.join(a.out_dir, "schedule_rows.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    md = ["# 블록 개구부 ↔ 창호일람 조인 **제안** — 검토표", "",
          "모든 부호(mark)는 사람이 확인할 **선언**이다. 확신이 '낮음'·'미결'인 행은 원본(카드·평면)을 열어 보고 정한다.", "",
          f"블록 형식 {len(rows)} · 지정 {sum(1 for r in rows if r.get('spec'))} · 미결 {sum(1 for r in rows if r['status'].startswith('미결'))}"
          f" · 검토 {sum(1 for r in rows if r['status'].startswith('검토'))}", "",
          "| 형식 | 블록 | 레이어 | 개수(쌓임) | 폭(근거) | 가까운 실명 | 제안 mark | 종류 | H | 창대 | 확신 | 근거 / 후보 |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    fmt = lambda v: "—" if v is None else f"{v:.0f}"
    for r in rows:
        s = r.get("spec")
        conf = ("낮음(폭=범위)" if r["width_src"] == "extent" else "낮음(폭=이름 속 숫자)" if r["width_src"] == "name_loose"
                else "—" if not s else "높음" if len(r["ev"]) <= 2 else "중간")
        near = "; ".join(f"{x[0][0]} {x[0][1]}" for x in r["rooms"] if x) or "—"
        tail = "; ".join(r["ev"])
        if s:
            tail += f"; {s['src']}" + ("; " + "; ".join(s["note"]) if s["note"] else "")
        elif r["cands"]:
            tail += "; 후보: " + " / ".join(f"{c['mark']}({c.get(a.room_field) or c.get('src')}, 창대 {fmt(c.get('sill'))})" for c in r["cands"])
        if r.get("hint"):
            tail += f"; {r['hint']}"
        md.append(f"| {r['unit'] or '공용'} | `{r['base']}` | {r['role']} | {r['count']}({r['stacked']}) | {fmt(r['width'])}({r['width_src']}) | {near} | "
                  f"{s['final'] if s else '**' + r['status'].split(' ')[0] + '**'} | {s['subtype'] if s else r['side']} | "
                  f"{fmt(s['height']) if s else ''} | {fmt(s['sill']) if s else ''} | {conf} | {tail} |")
    open(os.path.join(a.out_dir, "review_table.md"), "w", encoding="utf-8").write("\n".join(md) + "\n")
    return csv_path


def args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inventory"), ap.add_argument("--cards"), ap.add_argument("--doors")
    ap.add_argument("--default-block-rules", help="맨 아래에 옮길 기존 block_map CSV(없으면 파서 기본 규칙)")
    ap.add_argument("--card-variant", help="카드 시트 variant 정규식(평면과 같은 평면형 시트만)")
    ap.add_argument("--unit-alias", action="append", default=[], metavar="PLAN=CARD", help="평면 형식 → 카드 형식 이름")
    ap.add_argument("--either-kinds", type=lambda s: [x for x in s.split(",") if x], default=[], help="창/문 어느 블록이든 되는 종류")
    ap.add_argument("--width-tol", type=float, default=50.0)
    ap.add_argument("--max-width", type=float, default=6000.0)
    ap.add_argument("--room-max", type=float, default=4000.0)
    ap.add_argument("--room-field", default="room")
    ap.add_argument("--name-field", default="name", help="카드 품명 줄 필드(card_extract --field name=<dy>)")
    ap.add_argument("--out-dir", help="출력 폴더 — 저장소 밖(<작업 폴더>/decl)")
    ap.add_argument("--selftest", action="store_true")
    return ap.parse_args(argv)


def selftest():
    import tempfile
    inst = lambda name, unit, w, side, rooms, src="label": {"name": name, "base": W.tail(name), "unit": unit, "role": "A-WIN", "width": w,
                                                            "width_src": src, "side": side, "rooms": rooms, "stacked": 1}
    inv = [inst("U-59B$0$A$C0001", "59B", 1800, "window", [["확장발코니", 600], ["침실-2", 2000]]),
           inst("U-59B$0$A$C0001", "59B", 1800, "window", [["확장발코니", 600], ["침실-3", 2000]]),
           inst("U-59B$0$A$C0002", "59B", 1800, "window", [["발코니1", 700]]),
           inst("U-59B$0$WIN-GRILL(실외기실용)", "59B", 900, "window", [["실외기실", 800]], "name_loose"),
           inst("U-59B$0$DR-X", "59B", 1395, "door", [["현관", 900]], "extent"),
           inst("*U12", None, 700, "door", [], "metre"), inst("*U13", None, 700, "door", [], "metre"),
           inst("*U14", None, 1100, "door", [], "metre"), inst("X$0$BIG", None, 10500, "window", [], "extent")]
    card = lambda mark, W_, H, sill, room: {"mark": mark, "kind": mark.split()[0], "W": W_, "H": H, "sill": sill, "room": room,
                                            "name": "", "unit": "59B", "variant": "V", "sheet": "Q-1", "sheet_title": "(59B)"}
    cards = [card("PW 18x22", 1800, 2200, 240, "침실2/침실3"), card("PW 18x22", 1800, 2200, 320, "발코니1"),
             card("AG 9x22", 900, 2200, 320, "실외기실"), card("PW 9x12.5", 900, 1250, 1270, "다용도실"),
             card("FSD 9x21", 900, 2100, 0, "현관"), card("FSD 7.5x21", 750, 2100, 0, "대피실")]
    doors = [{"FLOOR": "ODD", "NO": "1", "MARK": "FSD-3", "W": 700, "H": 1500, "H1": 600.0},
             {"FLOOR": "ODD", "NO": "2", "MARK": "FSD-3", "W": 700, "H": 1500, "H1": 600.0},
             {"FLOOR": "ODD", "NO": "3", "MARK": "FSD-1", "W": 1100, "H": 2100, "H1": None},
             {"FLOOR": "ODD", "NO": "4", "MARK": "AD-3", "W": 1100, "H": 2100, "H1": None},
             {"FLOOR": "ODD", "NO": "5", "MARK": "AD-3", "W": 1100, "H": 2100, "H1": None}]
    with tempfile.TemporaryDirectory() as d:
        a = args(["--out-dir", d])
        rows, sched = propose(inv, cards, doors, a)
        write(rows, sched, a)
        by = {r["base"]: r for r in rows}
        assert by["A$C0001"]["spec"]["final"] == "PW 18x22" and by["A$C0002"]["spec"]["final"] == "PW 18x22@발코니1", rows
        assert by["WIN-GRILL(실외기실용)"]["spec"]["final"] == "AG 9x22"                # 이름 토큰 '실외'
        dx = by["DR-X"]                                                                 # 미결 · 폭 = 범위(스윙 호)
        assert dx["status"] == "미결" and "FSD 7.5x21" in dx["hint"] and "FSD 9x21" in dx["hint"], dx
        assert by["*U12"]["spec"]["subtype"] == "window" and by["*U12"]["spec"]["sill"] == 600  # 떠 있는 문 → 창
        assert by["*U14"]["spec"]["final"] == "FSD-1"                                    # 개수 일치 1 = FSD-1 1행
        assert by["BIG"]["status"].startswith("검토")
        s = {r["mark"]: r for r in sched}
        assert s["PW 18x22"]["count"] == 2 and s["PW 18x22@발코니1"]["sill"] == 320 and s["FSD-3"]["count"] == 2
        text = open(os.path.join(d, "block_map_proposal.csv"), encoding="utf-8").read()
        assert ",,,subtype=window;mark=PW 18x22\n" in text and "\nCOL|기둥|PILLAR,column" in text
        assert all(re.search(r",opening,\d+,,,", l) for l in text.splitlines() if ",opening," in l and not l.startswith("#")
                   and "|" not in l)                                                     # height 는 늘 빈칸
        assert "\n# ^U\\-59B\\$0\\$DR\\-X$,opening,1395" in text, text                    # 약한 폭 미결은 주석으로만
        assert "낮음(폭=범위)" in open(os.path.join(d, "review_table.md"), encoding="utf-8").read()
    # 한정 충돌: 실명 없는 두 카드(320·1000)가 같은 '@형식' 으로 겹친다 → 창대까지 붙인다
    mk = lambda sill, room: {"unit": "59B", "base": "B", "count": 1, "status": "지정",
                             "pick": {"src": "card", "sheets": ["Q-1"], **card("PW 18x22", 1800, 2200, sill, room)}}
    rows2 = [mk(240, "침실1"), mk(240, "침실2"), mk(320, ""), mk(1000, "")]
    specs(rows2, a)
    assert sorted(r["spec"]["final"] for r in rows2) == ["PW 18x22", "PW 18x22", "PW 18x22@59B", "PW 18x22@59B-1000"], rows2
    print("selftest ok")


def main():
    a = args()
    if a.selftest:
        return selftest()
    if not (a.inventory and a.cards and a.out_dir):
        raise SystemExit("--inventory · --cards · --out-dir(저장소 밖) 필요")
    a.out_dir = W.out_path(a.out_dir)
    inv = json.load(open(a.inventory, encoding="utf-8"))["inserts"]
    cards = json.load(open(a.cards, encoding="utf-8"))["cards"]
    doors = json.load(open(a.doors, encoding="utf-8"))["rows"] if a.doors else []
    rows, sched = propose(inv, cards, doors, a)
    path = write(rows, sched, a)
    st = collections.Counter(r["status"].split(" ")[0] for r in rows)
    print("블록 형식", len(rows), dict(st), "부호", len(sched), "->", path)


if __name__ == "__main__":
    main()
