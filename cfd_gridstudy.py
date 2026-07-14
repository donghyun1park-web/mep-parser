"""
cfd_gridstudy.py — 격자 독립성 검증 (CFD 신뢰성의 1번 관문)

같은 방을 셀 크기 여러 개(예: 0.3/0.2/0.15 m)로 각각 생성·실행·측정해서, 결과 지표
(평균/최고온도·ΔT)가 격자를 조밀하게 해도 몇 % 이내로 수렴하는지 보인다. 3격자면
GCI(Grid Convergence Index, Roache/Celik ASME V&V 표준)까지 계산.

"CFD를 믿을 수 있나"의 첫 답: 격자를 바꿔도 결과가 안 변하면(격자 독립) 그 수치는 격자가
아니라 물리에서 온 것이다. 소방 성능위주설계(PBD) 심사가 격자 민감도를 요구하는 것과 같은 논리.

사용:
  python cfd_gridstudy.py <config.json> --cells 0.3,0.2,0.15 -o study_pilot
  python cfd_gridstudy.py --from-geometry g.json --room-bbox "x0,y0,x1,y1" --power-kw 10 --cells 0.3,0.2,0.15
"""
import argparse
import json
import math
import os
import subprocess
import sys

import cfd_export
import cfd_report

HERE = os.path.dirname(os.path.abspath(__file__))


def solve_order(f1, f2, f3, r21, r32):
    """겉보기 수렴차수 p (불균등 격자비, Celik 반복). f1=최세밀.
    반환: (p, extrapolated_f1, gci21_pct) 또는 실패 시 None."""
    e21, e32 = f2 - f1, f3 - f2
    if e21 == 0 or e32 == 0:
        return None
    ratio = e32 / e21
    if ratio <= 0:                 # 비단조 → 격자 미독립(진동)
        return ("비단조", None, None)
    s = math.copysign(1.0, ratio)
    p = 2.0
    for _ in range(100):           # 고정점 반복
        q = math.log((r21**p - s) / (r32**p - s))
        p_new = abs(math.log(abs(ratio)) + q) / math.log(r21)
        if abs(p_new - p) < 1e-6:
            p = p_new
            break
        p = p_new
    f_ext = (r21**p * f1 - f2) / (r21**p - 1)
    ea = abs((f1 - f2) / f1) if f1 else 0.0        # 근사 상대오차
    gci21 = 1.25 * ea / (r21**p - 1) * 100.0       # %
    return (p, f_ext, gci21)


def run_one(cfg, cell, out_dir, endtime):
    """셀 크기 하나: 케이스 생성 → WSL 실행 → 지표 추출."""
    import copy
    c = copy.deepcopy(cfg)
    c.setdefault("mesh", {})["cell"] = cell
    if endtime:
        c["endTime"] = endtime
    cfd_export.build_case(c, out_dir)
    meta = json.load(open(os.path.join(out_dir, "cfd_case_meta.json"), encoding="utf-8"))
    r = subprocess.run([sys.executable, os.path.join(HERE, "cfd_run.py"), out_dir],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    m = None
    try:
        m = cfd_report.field_metrics(out_dir, meta)
    except Exception as e:
        print(f"  지표 추출 실패({cell}m): {e}", file=sys.stderr)
    cells = meta["mesh"]["cells"]
    return {"cell": cell, "cells": cells, "metrics": m or {}}


def main():
    ap = argparse.ArgumentParser(description="격자 독립성 검증 (여러 셀 크기 배치 실행)")
    ap.add_argument("config", nargs="?", help="cfd config.json")
    ap.add_argument("--from-geometry", metavar="G.JSON")
    ap.add_argument("--zone", type=int)
    ap.add_argument("--room-bbox")
    ap.add_argument("--height", type=float)
    ap.add_argument("--supply", default="x0")
    ap.add_argument("--exhaust", default="xL")
    ap.add_argument("--supply-u", type=float, default=0.3)
    ap.add_argument("--power-kw", type=float)
    ap.add_argument("--cells", default="0.3,0.2,0.15", help="셀 크기 목록(m), 조밀→ 마지막")
    ap.add_argument("--endtime", type=int, default=600)
    ap.add_argument("-o", "--out", default="gridstudy")
    ap.add_argument("--key", default="T_max_C", help="GCI 대상 지표(T_max_C|T_avg_C|dT_rise)")
    args = ap.parse_args()

    # cfg 확보(config 직접 or 도면추출)
    if args.from_geometry:
        geom = json.load(open(args.from_geometry, encoding="utf-8"))
        bbox = [float(x) for x in args.room_bbox.split(",")] if args.room_bbox else None
        cfg, _ = cfd_export.cfg_from_geometry(
            geom, zone=args.zone, bbox=bbox, height=args.height,
            supply=args.supply, exhaust=args.exhaust, supply_u=args.supply_u,
            power_kw=args.power_kw, endTime=args.endtime)
    elif args.config:
        cfg = json.load(open(args.config, encoding="utf-8"))
    else:
        ap.error("config 또는 --from-geometry 필요")

    cells = [float(x) for x in args.cells.split(",")]
    cells = sorted(cells, reverse=True)   # 성긴→세밀
    print(f"격자 독립성 검증: 셀 {cells} m, 지표={args.key}")
    results = []
    for i, c in enumerate(cells):
        print(f"[{i+1}/{len(cells)}] 셀 {c} m 실행...")
        results.append(run_one(cfg, c, f"{args.out}_c{i}", args.endtime))

    # 표 출력
    print("\n{:>8} {:>10} {:>9} {:>9} {:>8} {:>9}".format(
        "cell(m)", "cells", "T_avg", "T_max", "dT", "폐합%"))
    for r in results:
        m = r["metrics"]
        print("{:>8.3f} {:>10,} {:>9} {:>9} {:>8} {:>9}".format(
            r["cell"], r["cells"],
            _f(m.get("T_avg_C")), _f(m.get("T_max_C")),
            _f(m.get("dT_rise")), _f(m.get("closure_pct"), 0)))

    # 격자간 % 변화(마지막=최세밀 기준)
    key = args.key
    vals = [r["metrics"].get(key) for r in results]
    if all(v is not None for v in vals) and len(vals) >= 2:
        print(f"\n{key} 격자간 변화:")
        for i in range(len(vals) - 1):
            d = abs(vals[i + 1] - vals[i])
            base = abs(vals[i + 1]) or 1
            print(f"  {cells[i]}→{cells[i+1]} m: {d:.3f} ({100*d/base:.1f}%)")
        verdict = "격자 독립(충분)" if abs(vals[-1] - vals[-2]) / (abs(vals[-1]) or 1) < 0.02 else "격자 미독립 — 더 조밀히"
        print(f"  최세밀 2격자 변화 판정: {verdict}")

    # GCI (3격자 이상)
    gci = None
    if len(vals) >= 3 and all(v is not None for v in vals[:3]):
        # vals[0]=성긴(f3) ... vals[-1]=세밀(f1)
        f1, f2, f3 = vals[-1], vals[-2], vals[-3]
        r21, r32 = cells[-2] / cells[-1], cells[-3] / cells[-2]
        res = solve_order(f1, f2, f3, r21, r32)
        if res and res[0] == "비단조":
            print(f"\nGCI: 비단조 수렴 — 격자 미독립(더 세밀·품질 개선 필요)")
        elif res:
            p, fext, g21 = res
            print(f"\nGCI({key}): 수렴차수 p={p:.2f}, 외삽값={fext:.3f}, "
                  f"최세밀격자 GCI={g21:.2f}% "
                  f"({'신뢰(≤5%)' if g21 <= 5 else '격자오차 큼(>5%)'})")
            gci = {"p": p, "extrapolated": fext, "gci_pct": g21, "key": key}

    out = {"cells": cells, "key": key,
           "results": [{"cell": r["cell"], "cells": r["cells"], "metrics": r["metrics"]} for r in results],
           "gci": gci}
    jpath = f"{args.out}_gridstudy.json"
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n결과 저장 -> {jpath}")


def _f(v, nd=2):
    return f"{v:.{nd}f}" if isinstance(v, (int, float)) else "-"


if __name__ == "__main__":
    main()
