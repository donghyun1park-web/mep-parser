"""두 도면을 통심선(그리드 축) **이름**으로 맞춰 평행이동량을 낸다. 읽기 전용.

창호 안내도(공용부 부호가 지시선으로 붙은 평면)는 층 평면과 좌표가 다르다. 축 이름(X1..X10, Y1..Y7)이 두 도면에
다 있으면 이름끼리 짝지어 dx, dy 를 잰다(실측: 축 17개, 잔차 0). 회전·축척은 다루지 않는다 — 잔차가 크면 그 탓이다.
같은 축망이 한 도면에 두 벌이면(잘라낸 층에 옆 도곽 것이 딸려 옴) 동률이라 값을 내지 않는다 — --region 으로 한 벌만.
축 방향은 이름 접두(X·Y)별로 스스로 정한다: 같은 접두 축들이 x 로 퍼져 있으면 세로 축선(→ x 가 좌표).

  python axis_align.py plan.dxf guide.dxf --region-b x0,y0,x1,y1       # B 의 한 층 패널만
  python axis_align.py --selftest
"""
import argparse
import collections
import re
import statistics

import dxfwalk as W


def axes(items, name_re, tag=None, region=None):
    pos = collections.defaultdict(list)
    for t in items:
        if t["k"] not in ("TEXT", "MTEXT", "ATTRIB") or not re.fullmatch(name_re, t["t"] or ""):
            continue
        if tag and t.get("tag") != tag:
            continue
        if region and not (region[0] <= t["x"] <= region[2] and region[1] <= t["y"] <= region[3]):
            continue
        pos[t["t"]].append((t["x"], t["y"]))
    return pos


def coord_kind(pos):
    """접두별: 'x'(세로 축선, x 가 좌표) 또는 'y'."""
    kinds = {}
    groups = collections.defaultdict(list)
    for n, ps in pos.items():
        groups[re.match(r"\D*", n).group(0)].append((statistics.median(p[0] for p in ps), statistics.median(p[1] for p in ps)))
    for g, cs in groups.items():
        sx = max(c[0] for c in cs) - min(c[0] for c in cs)
        sy = max(c[1] for c in cs) - min(c[1] for c in cs)
        kinds[g] = "x" if sx >= sy else "y"
    return kinds


def _clusters(vals, tol=50.0):
    out = []
    for v in sorted(vals):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [statistics.median(c) for c in out]


def align(pa, pb, tol=50.0):
    """축 종류(x·y)별로, 이름이 같은 축의 좌표 차 중 **가장 많은 이름이 지지하는** 값. 한 도면에 같은 축망이 두 벌 있으면
    (실측: 잘라낸 층 도면에 옆 도곽의 축망이 한 벌 더 — 도곽 폭만큼 떨어져) 후보가 동률이 되어 None 을 돌려준다."""
    kinds = coord_kind(pa)
    pairs = {"x": {}, "y": {}}
    multi = {}
    for n in sorted(set(pa) & set(pb)):
        k = kinds.get(re.match(r"\D*", n).group(0), "x")
        i = 0 if k == "x" else 1
        ca, cb = _clusters([p[i] for p in pa[n]], tol), _clusters([p[i] for p in pb[n]], tol)
        if len(ca) > 1 or len(cb) > 1:
            multi[n] = (ca, cb)
        pairs[k][n] = [b - a for a in ca for b in cb]
    out, resid, cands = {}, {}, {}
    for k, per in pairs.items():
        vals = _clusters([d for ds in per.values() for d in ds], tol)
        support = sorted(((sum(any(abs(d - c) <= tol for d in ds) for ds in per.values()), c) for c in vals), key=lambda t: -t[0])
        cands[k] = [(round(c, 1), s) for s, c in support[:4]]
        best = support[0] if support else None
        tie = best and any(s == best[0] and abs(c - best[1]) > tol for s, c in support[1:])
        out[k] = None if (not best or tie) else best[1]
        if out[k] is not None:
            for n, ds in per.items():
                resid[n] = round(min(ds, key=lambda d: abs(d - out[k])) - out[k], 1)
    return out["x"], out["y"], resid, multi, cands


def selftest():
    a, b = W.new_doc(), W.new_doc()
    bub = b.blocks.new("BUBBLE")
    bub.add_circle((0, 0), 400)
    bub.add_attdef("AX", (0, 0))
    for i, x in enumerate((0, 6000, 12000), 1):
        for y in (20000, -2000):                              # 축선 양 끝 버블
            a.modelspace().add_text(f"X{i}").set_placement((x, y))
            b.modelspace().add_blockref("BUBBLE", (x + 100000, y - 5000 + 3000)).add_auto_attribs({"AX": f"X{i}"})
    for j, y in enumerate((0, 8000), 1):
        a.modelspace().add_text(f"Y{j}").set_placement((-2000, y))
        b.modelspace().add_blockref("BUBBLE", (-2000 + 100000 - 700, y - 5000)).add_auto_attribs({"AX": f"Y{j}"})
    pb = axes(W.collect(b.modelspace()), r"[XY]\d{1,2}")
    dx, dy, res, multi, _ = align(axes(W.collect(a.modelspace()), r"[XY]\d{1,2}"), pb)
    assert (dx, dy) == (100000, -5000) and set(res.values()) == {0} and not multi, (dx, dy, res)
    for i, x in enumerate((0, 6000, 12000), 1):               # 옆 도곽의 같은 축망 한 벌(50m 옆)
        a.modelspace().add_text(f"X{i}").set_placement((x + 50000, 20000))
    dx, dy, res, multi, cands = align(axes(W.collect(a.modelspace()), r"[XY]\d{1,2}"), pb)
    assert dx is None and dy == -5000 and "X1" in multi, (dx, cands)    # 동률 — 추측하지 않는다
    dx, dy, *_ = align(axes(W.collect(a.modelspace()), r"[XY]\d{1,2}", region=(-5000, -5000, 30000, 30000)), pb)
    assert dx == 100000, dx
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a", nargs="?"), ap.add_argument("b", nargs="?")
    ap.add_argument("--axis-regex", default=r"[XY]\d{1,2}", help="축 이름(전체 일치)")
    ap.add_argument("--tag", help="축 이름이 ATTRIB 이면 그 태그만")
    ap.add_argument("--region-a"), ap.add_argument("--region-b", help="x0,y0,x1,y1 — 여러 층 패널이 한 도면에 있을 때")
    ap.add_argument("--max-depth", type=int, default=8)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not (a.a and a.b):
        ap.error("두 dxf 필요")
    reg = lambda s: [float(v) for v in s.split(",")] if s else None
    pa = axes(W.collect(W.ezdxf.readfile(a.a).modelspace(), a.max_depth), a.axis_regex, a.tag, reg(a.region_a))
    pb = axes(W.collect(W.ezdxf.readfile(a.b).modelspace(), a.max_depth), a.axis_regex, a.tag, reg(a.region_b))
    dx, dy, res, multi, cands = align(pa, pb)
    print(f"축 A {len(pa)} · B {len(pb)} · 공통 {len(set(pa) & set(pb))}  →  B = A + ({dx}, {dy})")
    print("잔차(mm):", res, "|", W.drops())
    if multi:
        print("★ 같은 이름의 축 좌표가 여러 개(축망이 두 벌?) — --region-a/--region-b 로 한 벌만:",
              {n: [[round(v) for v in c] for c in ab] for n, ab in list(multi.items())[:4]})
    if dx is None or dy is None:
        print("후보(값, 지지하는 축 수):", cands)


if __name__ == "__main__":
    main()
