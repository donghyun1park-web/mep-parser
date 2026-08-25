"""
grid_detect.py  —  통심선(구조 그리드) 검출 + 기둥 위치 검증

도면에서 가장 신뢰할 수 있는 기준선은 통심선이다. 그리드를 뽑아 교차점과 기둥을
대조하면 (a) 누락된 기둥, (b) 그리드에서 벗어난 기둥을 찾아낼 수 있다.
자기검증 루프(build_qa)가 '벽이 얼마나 회수됐나'를 본다면, 이쪽은 '기둥이 제자리인가'를 본다.

주의: 모든 도면에 통심선이 있는 것은 아니다. 지하주차장 평면처럼 그리드가 없는
도면에서는 axes=0 으로 조용히 반환한다(오탐보다 미검출이 안전).

사용:
    python grid_detect.py plan.dxf                     # 그리드만 검출
    python grid_detect.py plan.dxf -g geometry.json    # 기둥 대조까지
"""
import argparse
import json
import math
import os
import re
import sys

try:
    import ezdxf
except ImportError:
    print("[ERROR] ezdxf 필요: pip install ezdxf", file=sys.stderr)
    sys.exit(1)

# 통심선 레이어 이름 패턴(AIA A-CEN, 국내 관행 통심/심선 등)
GRID_LAYER_RE = re.compile(r"(^|[-_])CEN|GRID|AXIS|통심|심선|기준선", re.I)
ORTHO_TOL = 0.01        # 직교 판정: min(dx,dy)/max(dx,dy) 이 값 미만
COLLINEAR_TOL_MM = 50.0  # 같은 축으로 묶는 수직 오프셋 허용
# 축 채택 기준은 '가장 긴 축 대비 비율'. 도면 전체 범위를 쓰면 한 파일에 여러 시트가
# 담긴 도면(이 프로젝트 관리사무소: bbox 196×346m, 실제 건물 24×60m)에서 임계값이
# 과대해져 전부 탈락한다. 상대 기준은 축척·시트 구성에 영향받지 않는다.
MIN_AXIS_RATIO = 0.30    # 최장 축 총길이의 이 비율 이상이면 그리드 축으로 채택
MIN_AXIS_MM = 10000.0    # 그래도 이보다 짧으면 제외(심볼 중심선 방지)
COLUMN_TOL_MM = 600.0    # 기둥 중심이 교차점에서 이 거리 이내면 '그리드 위'
# 규칙성 판정: 실제 구조 그리드는 스팬이 규칙적(보통 100mm 단위, 1.5m 이상)이다.
# A-CEN 같은 레이어에는 구조 통심선과 심볼 중심선이 섞여 있어, 기하만으로는 완전히
# 분리되지 않는다. 규칙성이 낮으면 '그리드로 단정하지 않음'으로 보고해 오탐을 막는다.
MIN_BAY_MM = 1500.0
REGULAR_CONFIDENCE = 0.6


def _regularity(axes):
    """축 간격 중 '그럴듯한 스팬'(≥1500mm, 100mm 단위) 비율. 축 2개 미만이면 None."""
    if len(axes) < 2:
        return None
    sp = [axes[i + 1] - axes[i] for i in range(len(axes) - 1)]
    ok = sum(1 for s in sp if s >= MIN_BAY_MM and abs(s - round(s / 100.0) * 100) < 20)
    return ok / len(sp)


def _lines_on_grid_layers(msp, layer_re=GRID_LAYER_RE):
    """통심선 레이어의 LINE → [(x1,y1,x2,y2,layer)]."""
    out = []
    for e in msp:
        if e.dxftype() != "LINE":
            continue
        lay = getattr(e.dxf, "layer", "")
        if not layer_re.search(lay):
            continue
        s, d = e.dxf.start, e.dxf.end
        out.append((s.x, s.y, d.x, d.y, lay))
    return out


def _cluster_axes(entries, tol=COLLINEAR_TOL_MM, min_total=0.0):
    """[(좌표, 길이)] → 근접한 것끼리 축으로 묶고, 축의 '총 길이'로 걸러낸다.

    통심선은 보통 여러 조각으로 끊겨 그려지므로(이 도면 중앙값 5.7m, 건물 60m)
    개별 선 길이로 거르면 전부 탈락한다. 같은 축의 조각 길이를 합산해 판정한다.
    반환 [(축 좌표, 총 길이)]."""
    if not entries:
        return []
    entries = sorted(entries)
    groups, cur = [], [entries[0]]
    for v, ln in entries[1:]:
        if v - cur[-1][0] <= tol:
            cur.append((v, ln))
        else:
            groups.append(cur)
            cur = [(v, ln)]
    groups.append(cur)
    out = []
    for g in groups:
        total = sum(ln for _v, ln in g)
        if total >= min_total:
            # 길이 가중 평균 = 짧은 보조선에 덜 흔들림
            pos = sum(v * ln for v, ln in g) / max(total, 1e-9)
            out.append((pos, total))
    return out


def detect_grid(dxf_path, layer_re=GRID_LAYER_RE):
    """DXF → 그리드 축/교차점. 통심선이 없으면 axes 0 으로 반환.
    반환 dict: x_axes / y_axes / intersections / spans / n_source_lines"""
    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    lines = _lines_on_grid_layers(msp, layer_re)
    if not lines:
        return {"x_axes": [], "y_axes": [], "intersections": [],
                "n_source_lines": 0, "note": "통심선 레이어 없음"}

    # 도면 전체 범위(그리드 축 길이 기준)
    xs = [v for ln in lines for v in (ln[0], ln[2])]
    ys = [v for ln in lines for v in (ln[1], ln[3])]
    ext_x, ext_y = max(xs) - min(xs), max(ys) - min(ys)

    vert, horiz = [], []
    for x1, y1, x2, y2, _lay in lines:
        dx, dy = abs(x2 - x1), abs(y2 - y1)
        if max(dx, dy) == 0:
            continue
        if min(dx, dy) / max(dx, dy) >= ORTHO_TOL:
            continue                      # 사선 = 심볼 중심선 등 → 제외
        if dx < dy:                       # 수직선 → X 축
            vert.append(((x1 + x2) / 2.0, dy))
        else:                             # 수평선 → Y 축
            horiz.append(((y1 + y2) / 2.0, dx))

    def _keep(entries):
        """클러스터링 후 최장 축 대비 비율로 채택."""
        cl = _cluster_axes(entries)
        if not cl:
            return []
        top = max(t for _p, t in cl)
        thr = max(MIN_AXIS_RATIO * top, MIN_AXIS_MM)
        return sorted(p for p, t in cl if t >= thr)

    x_axes = [round(p, 1) for p in _keep(vert)]
    y_axes = [round(p, 1) for p in _keep(horiz)]
    rx, ry = _regularity(x_axes), _regularity(y_axes)
    vals = [r for r in (rx, ry) if r is not None]
    conf = min(vals) if vals else 0.0
    reliable = bool(vals) and conf >= REGULAR_CONFIDENCE
    inters = [[x, y] for x in x_axes for y in y_axes] if reliable else []
    return {
        "x_axes": x_axes,
        "y_axes": y_axes,
        "intersections": inters,
        "regularity": {"x": rx, "y": ry, "confidence": round(conf, 2)},
        "reliable": reliable,
        "n_source_lines": len(lines),
        "spans": {"x": round(ext_x, 1), "y": round(ext_y, 1)},
    }


def _column_centers(geom):
    """geometry.json → 기둥 중심 좌표 목록."""
    out = []
    for c in geom.get("elements", {}).get("column", []):
        if c.get("center"):
            out.append((c["center"][0], c["center"][1]))
        elif c.get("points"):
            p = c["points"]
            out.append((sum(q[0] for q in p) / len(p), sum(q[1] for q in p) / len(p)))
    return out


def check_columns(grid, geom, tol=COLUMN_TOL_MM):
    """그리드 교차점 ↔ 기둥 대조. 반환 dict:
    on_grid / off_grid(그리드에서 벗어난 기둥) / empty_nodes(기둥 없는 교차점)."""
    inters = grid.get("intersections") or []
    cols = _column_centers(geom)
    if not inters or not cols:
        return {"columns": len(cols), "intersections": len(inters),
                "on_grid": 0, "off_grid": [], "empty_nodes": [],
                "note": "그리드 또는 기둥 없음 — 대조 생략"}
    used = set()
    on_grid = 0
    off = []
    for cx, cy in cols:
        best, bi = None, None
        for i, (gx, gy) in enumerate(inters):
            d = math.hypot(cx - gx, cy - gy)
            if best is None or d < best:
                best, bi = d, i
        if best is not None and best <= tol:
            on_grid += 1
            used.add(bi)
        else:
            off.append({"center": [round(cx, 0), round(cy, 0)],
                        "nearest_node_mm": round(best or -1, 0)})
    empty = [inters[i] for i in range(len(inters)) if i not in used]
    off.sort(key=lambda o: -o["nearest_node_mm"])
    return {
        "columns": len(cols), "intersections": len(inters),
        "on_grid": on_grid,
        "on_grid_pct": round(100.0 * on_grid / len(cols), 1),
        "off_grid": off[:20],
        "off_grid_count": len(off),
        "empty_nodes": empty[:20],
        "empty_node_count": len(empty),
    }


def main():
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="통심선(구조 그리드) 검출 + 기둥 검증")
    ap.add_argument("dxf", help="DXF 도면 경로")
    ap.add_argument("-g", "--geometry", default=None,
                    help="geometry.json — 기둥 위치 대조")
    ap.add_argument("-o", "--out", default=None, help="결과 JSON 저장 경로")
    args = ap.parse_args()

    grid = detect_grid(args.dxf)
    nx, ny = len(grid["x_axes"]), len(grid["y_axes"])
    if not nx and not ny:
        print(f"[그리드] 통심선 없음 (레이어 후보선 {grid['n_source_lines']}개) "
              "— 이 도면은 그리드 기준 검증 불가")
    elif not grid.get("reliable"):
        conf = grid.get("regularity", {}).get("confidence", 0)
        print(f"[그리드] 축 후보 X {nx} · Y {ny} 검출됐으나 간격이 불규칙 "
              f"(규칙성 {conf:.0%} < {REGULAR_CONFIDENCE:.0%}) — 구조 그리드로 단정하지 않음.")
        print("  통심선 레이어에 심볼 중심선이 섞인 도면으로 보임. "
              "전용 레이어가 있으면 GRID_LAYER_RE 를 좁혀 재시도하세요.")
    else:
        print(f"[그리드] X축 {nx}개 · Y축 {ny}개 · 교차점 {len(grid['intersections'])}개 "
              f"(원본선 {grid['n_source_lines']}개, 규칙성 "
              f"{grid['regularity']['confidence']:.0%})")
        if nx > 1:
            sp = [round(grid["x_axes"][i + 1] - grid["x_axes"][i]) for i in range(nx - 1)]
            print(f"  X 간격(mm): {sp[:10]}{' …' if len(sp) > 10 else ''}")
        if ny > 1:
            sp = [round(grid["y_axes"][i + 1] - grid["y_axes"][i]) for i in range(ny - 1)]
            print(f"  Y 간격(mm): {sp[:10]}{' …' if len(sp) > 10 else ''}")

    result = {"grid": grid}
    if args.geometry and os.path.exists(args.geometry):
        with open(args.geometry, encoding="utf-8") as f:
            geom = json.load(f)
        chk = check_columns(grid, geom)
        result["column_check"] = chk
        if chk.get("note"):
            print(f"[기둥] {chk['note']} (기둥 {chk['columns']}개)")
        else:
            print(f"[기둥] {chk['columns']}개 중 그리드 위 {chk['on_grid']}개 "
                  f"({chk['on_grid_pct']}%) · 이탈 {chk['off_grid_count']}개 · "
                  f"빈 교차점 {chk['empty_node_count']}개")
            for o in chk["off_grid"][:5]:
                print(f"    이탈: ({o['center'][0]:.0f},{o['center'][1]:.0f}) "
                      f"최근접 노드 {o['nearest_node_mm']:.0f}mm")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[OK] 결과 저장 -> {args.out}")


if __name__ == "__main__":
    main()
