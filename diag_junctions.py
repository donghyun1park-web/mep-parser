"""
diag_junctions.py — 벽 junction/커버리지 측정 + 오버레이 하니스 (읽기전용).

목적(계획 Phase 0): "만들기 전에 측정". 치유 알고리즘 전/후를 같은 잣대로 비교해
추측이 아니라 실측·육안으로 검증한다.

지표:
  - 요소 수(벽/기둥/개구부/슬래브)
  - dangling 끝점 분류: (B)코너 L/X · (C)T접합 · (A)평행근접 · (D)자유단/오접합위험
  - polygonize 닫힌 방 수(1~200 m²)
오버레이 PNG: 벽 중심선(회색) + dangling 끝점을 카테고리색으로(빨강=D 위험).

사용:
  python diag_junctions.py "<plan.dxf>"                       # DXF 파싱해 측정
  python diag_junctions.py geometry.json -o overlay.png       # 파싱된 json
  python diag_junctions.py a.dxf -o before.png                # 치유 전
  (Phase1 구현 후 같은 DXF 재실행 = 치유 후, before/after diff)
"""
import argparse
import math
import os
import sys

DANGLE_MIN = 2.0      # 이보다 작으면 이미 접합으로 간주
DANGLE_MAX = 150.0    # 이보다 크면 무관(자유단)
PARALLEL_DOT = 0.985
ROOM_MIN_M2 = 1.0
ROOM_MAX_M2 = 200.0


def _udir(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n else (0.0, 0.0)


def _foot(p, a, b):
    """점 p → 선분 a-b: (수직거리, 파라미터 t[0..1 클램프 전])."""
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - ax, py - ay), 0.0
    t = ((px - ax) * dx + (py - ay) * dy) / L2
    tc = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + tc * dx), py - (ay + tc * dy)), t


def _wall_centerlines(elements):
    cls = []
    for w in elements.get("wall", []):
        cl = w.get("centerline") or w.get("points")
        if cl and len(cl) >= 2:
            cls.append((tuple(cl[0]), tuple(cl[-1])))
    return cls


def classify_dangling(cls):
    """벽 중심선 목록 → dangling 끝점 분류 카운트 + 끝점별 (좌표, 카테고리) 목록."""
    cats = {"B": 0, "C": 0, "A": 0, "D": 0}
    pts = []  # (x, y, cat)
    for i, (a, b) in enumerate(cls):
        wd = _udir(a, b)
        for ep in (a, b):
            best = (1e18, None)
            for j, (c, d) in enumerate(cls):
                if i == j:
                    continue
                g, t = _foot(ep, c, d)
                if g < best[0]:
                    best = (g, (t, c, d))
            g = best[0]
            if not (DANGLE_MIN < g < DANGLE_MAX):
                continue
            t, c, d = best[1]
            od = _udir(c, d)
            par = abs(wd[0] * od[0] + wd[1] * od[1])
            end_near = min(math.hypot(ep[0] - c[0], ep[1] - c[1]),
                           math.hypot(ep[0] - d[0], ep[1] - d[1])) < DANGLE_MAX
            if par > PARALLEL_DOT:
                cat = "A"
            elif end_near:
                cat = "B"
            elif 0.05 < t < 0.95:
                cat = "C"
            else:
                cat = "D"
            cats[cat] += 1
            pts.append((ep[0], ep[1], cat))
    return cats, pts


def closed_rooms(cls):
    """중심선 noding 후 polygonize → 방 크기(1~200 m²) 폴리곤 수 + 전체 폴리곤 수."""
    try:
        from shapely.geometry import LineString
        from shapely.ops import polygonize, unary_union
    except ImportError:
        return None, None
    if not cls:
        return 0, 0
    geoms = unary_union([LineString([a, b]) for a, b in cls])
    polys = list(polygonize(geoms))
    rooms = sum(1 for p in polys if ROOM_MIN_M2 * 1e6 < p.area < ROOM_MAX_M2 * 1e6)
    return rooms, len(polys)


def load_elements(path, here):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".dxf":
        import dxf_parser as P
        lm = os.path.join(here, "layer_map.csv")
        bm = os.path.join(here, "block_map.csv")
        rules = P.load_layer_map(lm) if os.path.exists(lm) else P.DEFAULT_LAYER_RULES
        brules = P.load_layer_map(bm) if os.path.exists(bm) else P.DEFAULT_BLOCK_RULES
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            data = P.parse(path, rules, brules)
        return data
    import json
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def overlay(cls, pts, out_png, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.lines as ml
    COL = {"B": "#2d6cdf", "C": "#2d8a4e", "A": "#d4a017", "D": "red"}
    fig, ax = plt.subplots(figsize=(18, 12))
    for a, b in cls:
        ax.plot([a[0], b[0]], [a[1], b[1]], color="#ccc", lw=0.5, zorder=1)
    for x, y, cat in pts:
        ax.plot(x, y, "o", color=COL[cat], ms=4 if cat != "D" else 7, zorder=3)
    ax.legend([ml.Line2D([], [], marker="o", color=COL[c], lw=0) for c in COL],
              ["B 코너L/X", "C T접합", "A 평행근접", "D 자유단/위험"], fontsize=11)
    ax.set_aspect("equal")
    ax.set_title(title, fontsize=10)
    plt.tight_layout()
    plt.savefig(out_png, dpi=95)
    return out_png


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="벽 junction/커버리지 측정 + 오버레이")
    ap.add_argument("input", help="plan.dxf 또는 geometry.json")
    ap.add_argument("-o", "--out", default=None, help="오버레이 PNG 경로(기본 <입력>_junctions.png)")
    args = ap.parse_args()

    data = load_elements(args.input, here)
    el = data.get("elements", {})
    cls = _wall_centerlines(el)
    cats, pts = classify_dangling(cls)
    rooms, total_poly = closed_rooms(cls)

    print(f"입력: {args.input}")
    print(f"요소: wall={len(el.get('wall', []))} column={len(el.get('column', []))} "
          f"opening={len(el.get('opening', []))} slab={len(el.get('slab', []))}")
    print(f"벽 중심선: {len(cls)}")
    tot = sum(cats.values())
    print(f"dangling 끝점(2~150mm): {tot}  → "
          f"B코너={cats['B']} C_T접합={cats['C']} A평행={cats['A']} D위험={cats['D']}")
    print(f"치유대상(B+C+A)={cats['B'] + cats['C'] + cats['A']}  보존대상(D)={cats['D']}")
    print(f"polygonize 닫힌 방(1~200m²)={rooms} / 전체폴리곤={total_poly}")

    out = args.out or (os.path.splitext(args.input)[0] + "_junctions.png")
    try:
        overlay(cls, pts, out, f"{os.path.basename(args.input)} — dangling {tot} (D={cats['D']})")
        print(f"오버레이 저장 -> {out}")
    except Exception as e:
        print(f"오버레이 스킵: {e}")


if __name__ == "__main__":
    main()
