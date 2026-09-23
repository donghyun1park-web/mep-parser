"""도면 세트 시트 인벤토리 — 파일마다 '무슨 시트인가' 를 제안한다(확정은 사람).

    python sheet_inventory.py <파일|폴더>... --out-dir <작업폴더>/inventory [--max-mb 200]
    python sheet_inventory.py --selftest

DWG 는 저장소의 dwg_converter.ensure_dxf 로 <out-dir>/dxf 에 변환한다(설치된 변환기만 쓴다).
파일마다: 크기·로드 시간·엔티티 종류·레이어(역할 = 마지막 '$0$' 뒤)·INSERT 이름·반복되는 큰 INSERT
(도곽 후보: 같은 블록 2회 이상, 긴 변 20 m 이상, 가로세로비 √2 ±2%)·제목 문자열(평면도|단면도|...)
→ inventory.json(역할·xref 접두·INSERT 전부) + inventory.md(요약) + forbidden_candidates.txt(커밋 스캔 후보).
같은 --out-dir 로 다시 돌리면 파일 이름별로 합친다(큰 파일만 따로 돌려도 앞의 행이 남는다).
DWG 는 <out-dir>/dxf 에 DWG 보다 새 변환본이 있으면 다시 변환하지 않는다.
제안(suggested)은 파일 이름·제목 문자열의 키워드와 역할 접두일 뿐이다 — 부재 분류가 아니고, 사람이 확인한다.
"""
import argparse
import collections
import json
import os
import re
import sys
import tempfile
import time
import warnings
from pathlib import Path

REPO = Path(os.environ.get("MEP_PARSER_REPO") or Path(__file__).resolve().parents[4])
sys.path.insert(0, str(REPO))

TITLE_RE = re.compile(r"평면도|단면도|입면도|일람|전개도|안내도|상세도|배치도")
# 키워드 → 시트 종류. 앞에 있을수록 우선(창호 안내도에도 '평면도' 글자가 흔하다).
ORDER = [("일람", "schedule"), ("전개도", "window_cards"), ("안내도", "mark_plan"),
         ("단면도", "section"), ("입면도", "elevation"), ("상세도", "detail"),
         ("배치도", "site_plan"), ("평면도", "plan")]
# 설비 시트: 파일 이름, 또는 '평면도' 제목의 과반이 이 낱말을 담으면(배수·기계·전기는 건축 제목에도 흔해 뺐다)
MEP_RE = re.compile(r"설비|위생|급배수|급수|오수|공조|환기|덕트|소방|스프링클러|MEP|HVAC", re.I)
MEP_ROLE = re.compile(r"^(M|P|FP|E)-", re.I)
FRAME_MIN_M = 20.0
# 도곽 = 용지 모양. A 계열 용지는 √2(841/594 = 1.416). 실측(종합평면도): 진짜 도곽 1.416, 같은 파일의
# 주열 치수 블록 1.371(3.1% 차), 단면 표시 블록 1.148 — 2% 로 가른다.
# ponytail: A 계열만 안다. ARCH/ANSI 용지(1.5 · 1.29 · 1.545) 세트가 오면 비율 목록으로 늘린다.
SHEET_RATIO, SHEET_TOL = 2 ** 0.5, 0.02
ANON_BLOCK = re.compile(r"\*|A\$C[0-9a-fA-F]+$")      # 익명 블록 — 변환마다 이름이 바뀐다


def role(layer):
    return layer.rsplit("$0$", 1)[-1]


def _texts(entities):
    for e in entities:
        t = e.dxftype()
        try:
            if t == "TEXT":
                yield e.dxf.text
            elif t == "MTEXT":
                yield e.plain_text()
            elif t == "INSERT":
                for a in e.attribs:
                    yield a.dxf.text
        except Exception:      # 깨진 프록시·인코딩 — 한 개 때문에 시트 전체를 잃지 않는다
            continue


def forbidden_candidates(file_name, layers, inserts):
    """커밋 스캔 후보(grep -E 한 줄씩): xref 이름(마지막 '$0$' 앞 조각들)·이름 있는 블록·도면 번호 계열.
    일반 이름(DOOR 류)은 사람이 지운다 — 현장명·고객명은 도면에 없으니 사람이 더한다."""
    names = {seg for lay in layers for seg in lay.split("$0$")[:-1]}
    for b in inserts:
        if not ANON_BLOCK.match(b):
            names.update(b.split("$0$"))
    # 숫자뿐인 이름(블록 '5136' 류)은 diff 의 아무 숫자에나 걸려 진짜 한 건을 묻는다 — 글자가 든 것만
    out = {re.sub(r"([.\[\](){}*+?|^$\\])", r"\\\1", n.strip()) for n in names
           if len(n.strip()) >= 3 and any(ch.isalpha() for ch in n)}
    m = re.match(r"([A-Za-z]{1,3}-?)(\d{3,})", file_name)
    if m:                                               # 도면 번호 끝 두 자리를 풀어 같은 계열 전부
        out.add(m.group(1) + m.group(2)[:-2] + "[0-9][0-9]")
    return sorted(out)


def suggest(name, titles, hits, frames, s_share, mep_share=0.0):
    """(종류, 근거). 파일 이름 키워드가 먼저(도면 목록은 설계자가 붙였다), 없으면 제목 문자열 최다 키워드."""
    kind, why, src = None, None, ""
    for kw, k in ORDER:
        if kw in name:
            kind, why, src = k, f"파일명 '{kw}'", name
            break
    if kind is None and hits:
        best = max(hits.values())
        kw = next(kw for kw, _ in ORDER if hits.get(kw) == best)
        kind, why, src = dict(ORDER)[kw], f"제목 '{kw}' {best}건", " ".join(t for t in titles if kw in t)
    if kind == "schedule":           # 창호 일람은 문도 담는다 — '창' 이 먼저
        kind = "window_schedule" if "창" in src else "door_schedule" if "문" in src else kind
    if kind in (None, "plan") and frames and frames[0]["count"] >= 2:     # 가장 많이 반복된 도곽 하나
        kind, why = "composite_plan", (why or "") + f" + 도곽 후보 {frames[0]['block']}×{frames[0]['count']}"
    plan_t = [t for t in titles if "평면도" in t]
    mep_t = [t for t in plan_t if MEP_RE.search(t)]
    hit = MEP_RE.search(name)
    mep_why = (f"파일명 '{hit.group()}'" if hit else
               f"설비 평면도 제목 {len(mep_t)}/{len(plan_t)}" if 2 * len(mep_t) > len(plan_t) > 0 else
               "M-/P-/FP-/E- 역할이 A- 보다 많음" if mep_share > 0.5 else None)
    if mep_why:                      # 설비 시트는 건축 layer_map 으로 파싱하지 않는다(SKILL.md 4a)
        return f"mep_{kind or 'sheet'}", (why + " + " if why else "") + mep_why
    if s_share > 0.5 and kind in (None, "plan", "composite_plan"):
        kind, why = f"structural_{kind or 'sheet'}", (why or "") + f" + S- 역할 {s_share:.0%}"
    return kind or "unknown", why or "근거 없음 — 열어서 본다"


def inspect_dxf(path):
    import ezdxf
    from ezdxf import bbox
    from drawing_units import scale_to_mm
    t0 = time.time()
    try:
        doc = ezdxf.readfile(path)
    except Exception:
        from ezdxf import recover
        doc, _ = recover.readfile(path)
    load_s = round(time.time() - t0, 1)
    msp = doc.modelspace()
    scale = scale_to_mm(doc)
    types, layers, inserts, first_ins = (collections.Counter(), collections.Counter(),
                                         collections.Counter(), {})
    for e in msp:
        types[e.dxftype()] += 1
        layers[e.dxf.layer] += 1
        if e.dxftype() == "INSERT":
            inserts[e.dxf.name] += 1
            first_ins.setdefault(e.dxf.name, e)
    roles = collections.Counter()
    for lay, n in layers.items():
        roles[role(lay)] += n
    total = sum(types.values())
    s_share = sum(n for r, n in roles.items() if r.upper().startswith("S-")) / total if total else 0.0
    mep_n = sum(n for r, n in roles.items() if MEP_ROLE.match(r))
    a_n = sum(n for r, n in roles.items() if r.upper().startswith("A-"))
    mep_share = mep_n / (mep_n + a_n) if mep_n else 0.0

    frames = []
    for name, n in inserts.items():
        if n < 2:
            continue
        try:
            ext = bbox.extents(doc.blocks[name], fast=True)
        except Exception:      # cp949 MULTILEADER 프록시 등 — 그 블록만 건너뛴다
            continue
        if not ext.has_data:
            continue
        ins = first_ins[name]
        k = (scale or 1.0) / 1000.0
        w = ext.size.x * abs(ins.dxf.xscale) * k
        h = ext.size.y * abs(ins.dxf.yscale) * k
        if max(w, h) >= FRAME_MIN_M and min(w, h) > 0 and \
                abs(max(w, h) / min(w, h) / SHEET_RATIO - 1) <= SHEET_TOL:
            frames.append({"block": name, "count": n, "size_m": [round(w, 2), round(h, 2)]})
    frames.sort(key=lambda f: -f["count"])

    # 제목: 모형 공간 TEXT/MTEXT/ATTRIB + 모형 공간에 놓인 블록 정의 안의 글자(블록당 한 번, 한 단계)
    strings = list(_texts(msp))
    for name in inserts:
        try:
            strings += list(_texts(doc.blocks[name]))
        except Exception:
            continue
    # 40자 넘는 글자는 제목이 아니라 일반사항 주석이다('... 단위세대 평면도 기준임' 같은 문장)
    titles = sorted({s.strip() for s in strings if s and len(s.strip()) <= 40 and TITLE_RE.search(s)})
    hits = collections.Counter(m for s in titles for m in TITLE_RE.findall(s))
    kind, why = suggest(Path(path).stem, titles, hits, frames, s_share, mep_share)
    return {
        "load_s": load_s, "dxfversion": doc.dxfversion, "insunits": doc.header.get("$INSUNITS", 0),
        "scale_to_mm": scale, "entities": total, "types": types.most_common(10),
        "layers": [[lay, role(lay), n] for lay, n in layers.most_common(15)],
        # 역할은 전부 — layer_map 은 개수 순위가 낮은 A-DOOR·A-WIN 에 달려 있다(실측: 기준층 역할 89개 중 41위·18위)
        "roles": roles.most_common(), "inserts": inserts.most_common(15),
        "frames": frames[:5], "title_hits": dict(hits), "titles": titles[:12],
        "s_share": round(s_share, 2), "mep_share": round(mep_share, 2), "suggested": kind, "reason": why,
        "forbidden": forbidden_candidates(Path(path).name, layers, inserts),
    }


def collect(paths):
    out = []
    for p in map(Path, paths):
        if p.is_dir():
            out += sorted(q for q in p.iterdir() if q.suffix.lower() in (".dwg", ".dxf"))
        else:
            out.append(p)
    # 같은 이름의 DWG·DXF 가 둘 다 있으면 DXF 하나만(이미 변환한 것)
    stems = {q.stem for q in out if q.suffix.lower() == ".dxf"}
    return [q for q in out if not (q.suffix.lower() == ".dwg" and q.stem in stems)]


def run(paths, out_dir, max_mb=0.0):
    from dwg_converter import ensure_dxf
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    inv = out_dir / "inventory.json"
    merged = {r["file"]: r for r in json.loads(inv.read_text(encoding="utf-8"))} if inv.exists() else {}
    for p in collect(paths):
        rec = {"file": p.name}
        try:
            dxf = str(p)
            if p.suffix.lower() == ".dwg":
                # ensure_dxf 는 out_dir 를 주면 캐시를 안 본다 — 다시 돌릴 때 전부 재변환(장당 1~40 s)을 막는다
                cached = out_dir / "dxf" / (p.stem + ".dxf")
                fresh = cached.exists() and cached.stat().st_mtime >= p.stat().st_mtime
                dxf = str(cached) if fresh else ensure_dxf(str(p), out_dir=str(out_dir / "dxf"))
            rec["dxf"] = dxf
            rec["mb"] = round(os.path.getsize(dxf) / 1e6, 1)
            if max_mb and rec["mb"] > max_mb:
                rec["skipped"] = f"{rec['mb']} MB > --max-mb {max_mb}"
            else:
                print(f"[..] {p.name} ({rec['mb']} MB)", flush=True)
                rec.update(inspect_dxf(dxf))
        except Exception as ex:
            rec["error"] = f"{type(ex).__name__}: {ex}"
        print(f"[{'OK' if 'suggested' in rec else '--'}] {p.name}: "
              f"{rec.get('suggested') or rec.get('skipped') or rec.get('error')}", flush=True)
        merged[rec["file"]] = rec      # 앞선 실행의 다른 파일 행은 남긴다(큰 파일만 따로 돌린 경우)
    rows = list(merged.values())
    # 깨진 cp949 문자열은 서로게이트로 온다 — 쓰기에서 죽지 않게 replace
    inv.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8", errors="replace")
    # 저장소 밖 작업 폴더에만 쓴다. 익명 블록 이름은 변환마다 바뀌므로 일반 패턴 두 줄로 덮는다.
    cand = {c for r in rows for c in r.get("forbidden", [])} | {r"A\$C[0-9a-f]{5,}", r"\*U[0-9]{3,}"}
    (out_dir / "forbidden_candidates.txt").write_text("\n".join(sorted(cand)) + "\n",
                                                      encoding="utf-8", errors="replace")
    md = ["| 파일 | MB | 로드 s | 엔티티 | 제안 | 근거 | 도곽 후보 | 제목 예 |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        fr = "; ".join(f"{f['block']}×{f['count']} ({f['size_m'][0]}×{f['size_m'][1]} m)" for f in r.get("frames", [])[:2])
        md.append("| {} | {} | {} | {} | {} | {} | {} | {} |".format(
            r["file"], r.get("mb", ""), r.get("load_s", ""), r.get("entities", ""),
            r.get("suggested") or r.get("skipped") or r.get("error", ""), r.get("reason", ""), fr,
            " / ".join(r.get("titles", [])[:3]).replace("|", "/").replace("\n", " ")))
    (out_dir / "inventory.md").write_text("\n".join(md) + "\n", encoding="utf-8", errors="replace")
    return rows


def selftest():
    import ezdxf
    with tempfile.TemporaryDirectory() as d:
        doc = ezdxf.new(units=4)                         # mm — ezdxf 기본값은 m(6)이라 명시한다
        frame = doc.blocks.new("FRAME")
        frame.add_lwpolyline([(0, 0), (841, 0), (841, 594), (0, 594)], close=True)
        msp = doc.modelspace()
        for i in range(2):                               # 1/150 도곽 두 장을 X 로 나란히
            msp.add_blockref("FRAME", (i * 130000, 0), dxfattribs={"xscale": 150, "yscale": 150})
            msp.add_text(f"{i + 1}층 평면도", dxfattribs={"insert": (i * 130000 + 1000, 1000)})
        msp.add_line((0, 0), (5000, 0), dxfattribs={"layer": "UNIT$0$A-WALL"})
        doc.blocks.new("5136")                           # 숫자뿐인 블록 이름은 스캔 후보에서 뺀다
        msp.add_blockref("5136", (0, 0))
        doc.saveas(os.path.join(d, "set_A.dxf"))
        doc2 = ezdxf.new(units=4)
        doc2.modelspace().add_text("문 일람표")
        doc2.saveas(os.path.join(d, "sheet_B.dxf"))
        # 잘라낸 한 층: 크지만 용지 모양이 아닌 블록(단면 표시 80×70 m 류)이 두 번 → 도곽이 아니다
        doc3 = ezdxf.new(units=4)
        doc3.blocks.new("MARK").add_lwpolyline([(0, 0), (80000, 0), (80000, 70000)])
        for i in range(2):
            doc3.modelspace().add_blockref("MARK", (i * 1000, 0))
        doc3.modelspace().add_text("3층 평면도")
        doc3.saveas(os.path.join(d, "floor_C.dxf"))
        doc4 = ezdxf.new(units=4)                        # 설비 평면: 배경 A- 가 많아도 파일 이름이 가른다
        doc4.modelspace().add_line((0, 0), (1, 0), dxfattribs={"layer": "BG$0$A-WALL"})
        doc4.saveas(os.path.join(d, "M-101 급배수 평면도.dxf"))
        inv = os.path.join(d, "inv")
        os.makedirs(os.path.join(inv, "dxf"))
        Path(d, "sheet_D.dwg").write_bytes(b"not a dwg")  # 이미 변환한 DXF 가 더 새로우면 변환기를 안 부른다
        doc2.saveas(os.path.join(inv, "dxf", "sheet_D.dxf"))
        rows = {r["file"]: r for r in run([d], inv)}
        a, b = rows["set_A.dxf"], rows["sheet_B.dxf"]
        assert a["frames"][0]["block"] == "FRAME" and a["frames"][0]["count"] == 2, a["frames"]
        assert a["frames"][0]["size_m"] == [126.15, 89.1], a["frames"]
        assert dict(a["roles"])["A-WALL"] == 1, a["roles"]
        assert a["suggested"] == "composite_plan", a
        assert b["suggested"] == "door_schedule", b
        assert rows["floor_C.dxf"]["suggested"] == "plan" and not rows["floor_C.dxf"]["frames"], rows["floor_C.dxf"]
        assert rows["M-101 급배수 평면도.dxf"]["suggested"] == "mep_plan", rows["M-101 급배수 평면도.dxf"]
        assert rows["sheet_D.dwg"].get("suggested") == "door_schedule", rows["sheet_D.dwg"]
        cand = Path(inv, "forbidden_candidates.txt").read_text(encoding="utf-8").split("\n")
        assert "UNIT" in cand and "M-1[0-9][0-9]" in cand and "5136" not in cand and "" not in cand[:-1], cand
        # 큰 파일만 같은 --out-dir 로 다시 돌려도 앞의 행이 남는다
        again = {r["file"] for r in run([os.path.join(d, "sheet_B.dxf")], inv)}
        assert {"set_A.dxf", "floor_C.dxf", "sheet_B.dxf"} <= again, again
        assert (Path(inv) / "inventory.md").exists()
    print("selftest OK")


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="*", help="DWG/DXF 파일 또는 폴더")
    ap.add_argument("--out-dir", help="출력 폴더(작업 폴더 안 — 저장소 밖)")
    ap.add_argument("--max-mb", type=float, default=0.0, help="이보다 큰 DXF 는 건너뛴다(0 = 제한 없음)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.paths or not a.out_dir:
        ap.error("paths 와 --out-dir 가 필요하다")
    run(a.paths, a.out_dir, a.max_mb)
    print(f"-> {Path(a.out_dir) / 'inventory.md'}")


if __name__ == "__main__":
    main()
