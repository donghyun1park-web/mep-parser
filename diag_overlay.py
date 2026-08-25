"""
diag_overlay.py  —  누락 진단 오버레이 PNG (자기검증 루프의 '눈')

geometry.json 의 QA 결과를 원본 도면 위에 겹쳐 그려, 벽이 어디서 누락됐는지
한 장의 이미지로 보여준다. "vision in the loop" 의 결정론 버전:
  회색   = 원본 DXF 선 (전 레이어 배경)
  파랑   = 빌드된 벽 footprint (paired — 두께 실측)
  주황   = 빌드된 벽 footprint (single_offset — 두께 추정, 검토 대상)
  청록   = closed 폴리곤 벽(솔리드 통과)
  빨강   = 미커버 면선 (벽 생성 실패 = 누락 의심 — qa["uncovered"])

사용:
    python diag_overlay.py geometry.json                # <입력>_diag.png 생성
    python diag_overlay.py geometry.json out.png --no-backdrop
의존: matplotlib(필수), shapely(footprint), ezdxf(배경 — 없으면 배경 생략)
"""
import argparse
import json
import math
import os
import sys


def _collect_backdrop(dxf_path, max_arc_seg=24):
    """DXF 전 레이어의 선형 엔티티 → [(x1,y1,x2,y2), ...] (배경 회색선)."""
    try:
        import ezdxf
    except ImportError:
        return []
    try:
        doc = ezdxf.readfile(dxf_path)
    except Exception:
        return []
    segs = []
    for e in doc.modelspace():
        t = e.dxftype()
        try:
            if t == "LINE":
                s, en = e.dxf.start, e.dxf.end
                segs.append((s.x, s.y, en.x, en.y))
            elif t == "LWPOLYLINE":
                pts = [(p[0], p[1]) for p in e.get_points()]
                if e.closed and pts:
                    pts.append(pts[0])
                segs += [(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
                         for i in range(len(pts) - 1)]
            elif t == "POLYLINE":
                pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                if e.is_closed and pts:
                    pts.append(pts[0])
                segs += [(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1])
                         for i in range(len(pts) - 1)]
            elif t in ("ARC", "CIRCLE"):
                c, r = e.dxf.center, e.dxf.radius
                a0 = math.radians(getattr(e.dxf, "start_angle", 0.0))
                a1 = math.radians(getattr(e.dxf, "end_angle", 360.0))
                if a1 <= a0:
                    a1 += 2 * math.pi
                n = max(4, int((a1 - a0) / (2 * math.pi) * max_arc_seg))
                pp = [(c.x + r * math.cos(a0 + (a1 - a0) * k / n),
                       c.y + r * math.sin(a0 + (a1 - a0) * k / n))
                      for k in range(n + 1)]
                segs += [(pp[i][0], pp[i][1], pp[i + 1][0], pp[i + 1][1])
                         for i in range(len(pp) - 1)]
        except Exception:
            continue
    return segs


def _wall_footprint(rec, default_w):
    """벽 레코드 → (shapely Polygon footprint, style) 또는 None."""
    try:
        from shapely.geometry import LineString, Polygon
    except ImportError:
        return None
    cl = rec.get("centerline") or rec.get("points") or []
    if len(cl) < 2:
        return None
    pts = [(p[0], p[1]) for p in cl]
    pairing = rec.get("pairing", "")
    if rec.get("closed") and len(pts) >= 3:
        try:
            return Polygon(pts), "closed"
        except Exception:
            return None
    w = rec.get("width_detected") or (rec.get("overrides") or {}).get("width") or default_w
    try:
        g = LineString(pts)
        if g.length < 1.0:
            return None
        return g.buffer(float(w) / 2.0, cap_style=2), pairing or "paired"
    except Exception:
        return None


STYLE = {  # (facecolor, alpha, zorder)
    "paired":        ("#2d6cdf", 0.55, 3),
    "closed":        ("#18a9a0", 0.55, 3),
    "single_offset": ("#f59e0b", 0.55, 3),
    "single":        ("#f59e0b", 0.40, 3),
}


def build_overlay(geom_path, out_png=None, dxf_path=None, backdrop=True,
                  max_px=4000):
    """geometry.json → 진단 오버레이 PNG. 반환 (out_png, qa dict)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection

    with open(geom_path, encoding="utf-8") as f:
        data = json.load(f)
    qa = data.get("qa") or {}
    walls = data.get("elements", {}).get("wall", [])
    pw = float(data.get("params", {}).get("wall", {}).get("width", 200.0))
    dxf = dxf_path or data.get("source", "")
    if out_png is None:
        out_png = os.path.splitext(geom_path)[0] + "_diag.png"

    fig, ax = plt.subplots(figsize=(16, 12))
    # 1) 배경: 원본 DXF 회색선
    if backdrop and dxf and os.path.exists(dxf):
        segs = _collect_backdrop(dxf)
        if segs:
            ax.add_collection(LineCollection(
                [[(x1, y1), (x2, y2)] for x1, y1, x2, y2 in segs],
                colors="#b8b8b8", linewidths=0.3, zorder=1))
    # 2) 빌드된 벽 footprint
    n_style = {}
    for rec in walls:
        fp = _wall_footprint(rec, pw)
        if not fp:
            continue
        poly, style = fp
        color, alpha, z = STYLE.get(style, STYLE["paired"])
        polys = getattr(poly, "geoms", [poly])
        for p in polys:
            try:
                xs, ys = p.exterior.xy
                ax.fill(xs, ys, facecolor=color, alpha=alpha,
                        edgecolor="none", zorder=z)
            except Exception:
                continue
        n_style[style] = n_style.get(style, 0) + 1
    # 3) 미커버 면선 (누락 의심) — 빨강 굵은 선
    unc = qa.get("uncovered", [])
    if unc:
        ax.add_collection(LineCollection(
            [[(u["p1"][0], u["p1"][1]), (u["p2"][0], u["p2"][1])] for u in unc],
            colors="#e11d48", linewidths=2.2, zorder=5))

    ax.set_aspect("equal")
    ax.autoscale()
    ax.set_axis_off()
    cov = qa.get("face_coverage_pct")
    title = "벽 누락 진단"
    if cov is not None:
        title += f"  |  면선커버 {cov:.0f}%  |  미커버 {qa.get('uncovered_count', 0)}개"
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    legend = [Patch(facecolor="#2d6cdf", alpha=0.55, label=f"paired ({n_style.get('paired', 0)})"),
              Patch(facecolor="#18a9a0", alpha=0.55, label=f"closed ({n_style.get('closed', 0)})"),
              Patch(facecolor="#f59e0b", alpha=0.55,
                    label=f"single_offset ({n_style.get('single_offset', 0)})"),
              Line2D([0], [0], color="#e11d48", lw=2.2,
                     label=f"미커버 면선 ({len(unc)})")]
    ax.legend(handles=legend, loc="lower right", fontsize=9, framealpha=0.9)
    try:
        ax.set_title(title, fontsize=12, fontfamily="Malgun Gothic")
        for t in ax.get_legend().get_texts():
            t.set_fontfamily("Malgun Gothic")
    except Exception:
        pass  # 한글 폰트 없으면 기본 폰트(글자 깨짐만, 기능 무관)

    dpi = min(300, max(100, int(max_px / 16)))
    fig.savefig(out_png, dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_png, qa


def main():
    for _s in (sys.stdout, sys.stderr):   # cp949 콘솔 한글 깨짐 방지
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="벽 누락 진단 오버레이 PNG")
    ap.add_argument("geometry", help="geometry.json 경로 (qa 포함)")
    ap.add_argument("out", nargs="?", default=None, help="출력 PNG (기본 <입력>_diag.png)")
    ap.add_argument("--dxf", default=None, help="배경 DXF (기본 geometry.json 의 source)")
    ap.add_argument("--no-backdrop", action="store_true", help="DXF 배경 생략")
    args = ap.parse_args()
    out, qa = build_overlay(args.geometry, args.out, dxf_path=args.dxf,
                            backdrop=not args.no_backdrop)
    cov = qa.get("face_coverage_pct", "?")
    print(f"[OK] 진단 이미지 -> {out}  (면선커버 {cov}%, "
          f"미커버 {qa.get('uncovered_count', '?')}개)")


if __name__ == "__main__":
    main()
