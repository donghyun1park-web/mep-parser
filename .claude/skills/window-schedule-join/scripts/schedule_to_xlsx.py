"""schedule_rows.json → 창호일람 xlsx(저장소 schedule_io 양식) → 다시 읽어 같은지 확인.

왜 확인하나: export_schedule_xlsx 는 키가 **빠진** 창대에 기본값(창 900 · 문 0)을 채우고, 종류 칸은 subtype 에서 만든다.
그래서 행마다 subtype 을 채우고 모르는 창대는 null 을 명시해야 한다 — 다시 읽어 subtype·W·H·창대·수량이 같을 때만 성공.

  python schedule_to_xlsx.py <work>/decl/schedule_rows.json <work>/decl/창호일람.xlsx
  python schedule_to_xlsx.py --selftest
"""
import argparse
import json

import dxfwalk  # 저장소 경로 · 저장소 밖 출력 확인
from schedule_io import export_schedule_xlsx, load_schedule_xlsx


def convert(rows, path, title="창호일람표"):
    for r in rows:
        if r.get("subtype") not in ("door", "window") or "sill" not in r:
            raise SystemExit(f"행 {r.get('mark')!r}: subtype(door|window)과 sill 키(모르면 null)가 있어야 한다")
    export_schedule_xlsx(rows, path, title)
    back = {r["mark"]: r for r in load_schedule_xlsx(path)}
    bad = []
    for r in rows:
        b = back.get(r["mark"])
        want = (r["subtype"], float(r["width"]), float(r["height"]), None if r["sill"] is None else float(r["sill"]), int(r.get("count", 1)))
        got = b and (b["subtype"], b["width"], b["height"], b["sill"], b["count"])
        if want != got:
            bad.append((r["mark"], want, got))
    if bad or len(back) != len(rows):
        raise SystemExit(f"왕복 불일치 {bad} (행 {len(rows)} → {len(back)})")
    return len(rows)


def selftest():
    import os, tempfile
    rows = [{"mark": "PW 18x22", "subtype": "window", "width": 1800, "height": 2200, "sill": 240, "count": 2},
            {"mark": "FSD-3", "subtype": "window", "width": 700, "height": 1500, "sill": 600, "count": 1},   # 부호 접두는 문 — 종류 칸이 창을 지킨다
            {"mark": "PW 9x5", "subtype": "window", "width": 900, "height": 500, "sill": None, "count": 1}]  # 모르는 창대는 빈칸 그대로
    with tempfile.TemporaryDirectory() as d:
        assert convert(rows, os.path.join(d, "s.xlsx")) == 3
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("rows", nargs="?"), ap.add_argument("xlsx", nargs="?")
    ap.add_argument("--title", default="창호일람표")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not (a.rows and a.xlsx):
        ap.error("rows.json 과 출력 xlsx 필요")
    out = dxfwalk.out_path(a.xlsx)
    n = convert(json.load(open(a.rows, encoding="utf-8")), out, a.title)
    print(f"{n}행 왕복 일치 ->", out)


if __name__ == "__main__":
    main()
