# -*- coding: utf-8 -*-
"""stack.json → 여러 층 DXF 를 한 geometry.json 으로 조립.

층마다 임시 스크립트(build_mid.py / build_roof.py / build_ph.py)를 새로 쓰던 것을
대체한다. 파싱은 dxf_parser.parse 를 그대로 호출한다 — 여기서 재구현하지 않는다.

stack.json:
{
  "project": "...",
  "levels": [
    {"id": "1F", "z": 6050,  "source": "1층.dxf", "layer_map": "m.csv"},
    {"id": "RF", "z": 14750, "source": "옥상.dxf", "layer_map": "m2.csv",
     "align": "1F", "height": 4000}
  ]
}
  z       : 이 층의 절대 기준 z(mm). floors[] 를 선언에서 직접 만들므로 층 고아가 없다.
  align   : 이 층 좌표를 맞출 기준 층 id(통심선 기준). 생략하면 이동 없음.
  height  : 벽/기둥/존의 높이 기본값. 층마다 다시 불러주지 않기 위한 자리.

사용:
    python stack_build.py stack.json --dry-run     # offset 만 확인하고 멈춘다
    python stack_build.py stack.json -o merged.json
"""
import argparse
import json
import os

import dxf_parser as dp
import geom_contract as GC
from grid_detect import detect_grid

AXIS_TOL_MM = 50.0        # 통심선 일치로 볼 거리
MIN_AXES = 3              # 방향별 최소 증거 축 수
AMBIGUITY = 0.9           # 2위가 1위의 이 비율 이상이면 모호 → 거부
CONTAIN_MARGIN = 0.25     # 하부층 bbox 를 이 비율만큼 키운 범위 안에 들어와야 함


class StackError(ValueError):
    """조립을 진행하면 안 되는 상태 — 좌표 정렬 실패 등."""


# ── offset 해결 ────────────────────────────────────────────────────────────
def _best_shift(ref_axes, mov_axes, tol=AXIS_TOL_MM):
    """mov 를 얼마나 밀면 ref 축과 가장 많이 겹치나. (shift, 1위점수, 2위점수)

    후보는 모든 축 쌍의 차이뿐이다(정답 shift 는 반드시 어떤 쌍을 정확히 맞춘다).
    ponytail: O(n²) 후보 × O(n) 채점. 통심선은 도면당 수십 개라 충분하다.
    """
    if not ref_axes or not mov_axes:
        return None, 0, 0
    scores = {}
    for a in ref_axes:
        for b in mov_axes:
            s = round(a - b, 1)
            if s in scores:
                continue
            scores[s] = sum(1 for m in mov_axes
                            if any(abs(m + s - r) <= tol for r in ref_axes))
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], abs(kv[0])))
    best = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0
    return best[0], best[1], second


def _bbox(data):
    xs, ys = [], []
    for recs in (data.get("elements") or {}).values():
        for r in recs:
            for p in (r.get("points") or []):
                xs.append(p[0])
                ys.append(p[1])
            if r.get("center"):
                xs.append(r["center"][0])
                ys.append(r["center"][1])
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def resolve_offset(ref_dxf, mov_dxf, ref_data, mov_data):
    """두 도면의 통심선으로 (dx, dy) 를 구한다. 가드 3개를 통과해야 반환한다.

    가드가 필요한 이유: 기둥 4개가 정사각으로 배치된 층에서 12,000mm 그리드가
    반복되는 바람에 **틀린 offset 이 4/4 완벽 매칭**으로 통과해 구조물이 Y 로
    43,000mm 어긋난 채 납품된 적이 있다. 점수만으로는 그걸 못 잡는다.
    """
    g1, g2 = detect_grid(ref_dxf), detect_grid(mov_dxf)
    ev = {"ref_axes": [len(g1["x_axes"]), len(g1["y_axes"])],
          "mov_axes": [len(g2["x_axes"]), len(g2["y_axes"])]}

    dx, sx, sx2 = _best_shift(g1["x_axes"], g2["x_axes"])
    dy, sy, sy2 = _best_shift(g1["y_axes"], g2["y_axes"])
    ev.update({"matched": [sx, sy], "runner_up": [sx2, sy2], "offset": [dx, dy]})

    # ① 증거 하한 — 축이 2개뿐이면 점수를 계산할 것도 없이 거부한다.
    if sx < MIN_AXES or sy < MIN_AXES:
        raise StackError(
            f"통심선 증거 부족(x={sx}, y={sy}, 최소 {MIN_AXES}). "
            f"{ev} — align 을 빼고 offset 을 직접 지정하거나 통심선 레이어를 확인할 것")
    # ② 모호성 마진 — 2위가 바짝 붙어 있으면 어느 쪽인지 사람이 정해야 한다.
    if sx2 >= sx * AMBIGUITY or sy2 >= sy * AMBIGUITY:
        raise StackError(
            f"offset 후보가 모호하다(x {sx}vs{sx2}, y {sy}vs{sy2}). {ev} — "
            "그리드가 반복되는 도면이다. offset 을 직접 지정할 것")
    # ③ 포함 검사 — 그리드와 독립이라 그리드가 놓친 것을 잡는다.
    b1, b2 = _bbox(ref_data), _bbox(mov_data)
    if b1 and b2:
        w, h = b1[2] - b1[0], b1[3] - b1[1]
        if not (b1[0] - w * CONTAIN_MARGIN <= b2[0] + dx
                and b2[2] + dx <= b1[2] + w * CONTAIN_MARGIN
                and b1[1] - h * CONTAIN_MARGIN <= b2[1] + dy
                and b2[3] + dy <= b1[3] + h * CONTAIN_MARGIN):
            raise StackError(
                f"offset 적용 후 상부층이 하부층 bbox 밖으로 나간다. {ev} — "
                "윗층이 건물 밖에 있을 수는 없다")
    return dx, dy, ev


# ── 이동 / 병합 ────────────────────────────────────────────────────────────
def _shift(rec, cat, dx, dy, z, height=None):
    for p in (rec.get("points") or []):
        p[0] += dx
        p[1] += dy
    for p in (rec.get("centerline") or []):
        p[0] += dx
        p[1] += dy
    if rec.get("center"):
        rec["center"][0] += dx
        rec["center"][1] += dy
    key = "elevation" if cat in GC._ELEV_CATS else "z_base"
    rec[key] = float(rec.get(key, 0.0) or 0.0) + z
    # 층에 height 를 적었으면 그게 이긴다. 층고를 적어줬는데 레이어 기본값이
    # 조용히 이기면 "층마다 같은 설명을 반복" 하던 문제로 되돌아간다.
    # 레이어별 높이를 쓰고 싶으면 층에서 height 를 빼면 된다.
    if height and cat in ("wall", "column", "zone"):
        ov = rec.setdefault("overrides", {})
        prev = ov.get("height")
        ov["height"] = float(height)
        return prev is not None and float(prev) != float(height)
    return False


def build_stack(spec, base_dir=".", dry_run=False):
    """stack.json dict → 병합 geometry.json dict (dry_run 이면 offset 리포트만)."""
    parsed, report = {}, []
    out = {"source": spec.get("project", "stack"), "units": "mm",
           "scale_applied": 1.0, "params": {}, "elements": {}, "warnings": [],
           "contract": GC.contract_block(), "stack": {"levels": []}}

    for lv in spec["levels"]:
        lid = lv["id"]
        src = os.path.join(base_dir, lv["source"])
        rules = (dp.load_layer_map(os.path.join(base_dir, lv["layer_map"]))
                 if lv.get("layer_map") else dp.DEFAULT_LAYER_RULES)
        print(f"\n[{lid}] {os.path.basename(src)}")
        data = dp.parse(src, rules, member_schedule=lv.get("member_schedule"))
        parsed[lid] = (src, data)

        dx, dy = lv.get("offset", [0.0, 0.0])
        ev = {"mode": "manual" if lv.get("offset") else "none"}
        if lv.get("align"):
            ref = parsed.get(lv["align"])
            if ref is None:
                raise StackError(f"[{lid}] align 대상 {lv['align']!r} 이 앞에 없다")
            dx, dy, ev = resolve_offset(ref[0], src, ref[1], data)
            ev["mode"] = "grid"
            print(f"  offset ({dx:.0f}, {dy:.0f})  축매칭 {ev['matched']} "
                  f"(2위 {ev['runner_up']})")

        rep = {"id": lid, "z": lv["z"], "offset": [dx, dy], "evidence": ev,
               "counts": {k: len(v) for k, v in data["elements"].items() if v}}
        report.append(rep)
        out["stack"]["levels"].append(rep)
        if dry_run:
            continue

        if not out["params"]:
            out["params"] = data.get("params", {})
        n_ov = 0
        for cat, recs in data["elements"].items():
            for r in recs:
                n_ov += _shift(r, cat, dx, dy, float(lv["z"]), lv.get("height"))
                r["level"] = lid
                r["eid"] = f"{lid}:{r['eid']}"      # 같은 DXF 를 두 층에 쓰면 충돌한다
                out["elements"].setdefault(cat, []).append(r)
        if n_ov:
            print(f"  층고 {lv['height']}mm 가 레이어 높이를 덮음: {n_ov}개")
            rep["height_overrode"] = n_ov
        out["warnings"] += [f"[{lid}] {w}" for w in data.get("warnings", [])]

    # floors[] 를 선언에서 직접 만든다 → z 클러스터링이 없으니 층 고아가 불가능하다.
    out["floors"] = [{"z": float(lv["z"]), "label": lv.get("label", lv["id"])}
                     for lv in spec["levels"]]
    return (report if dry_run else out)


def main():
    ap = argparse.ArgumentParser(description="stack.json → 다층 geometry.json")
    ap.add_argument("stack")
    ap.add_argument("-o", "--out", default="stack_geometry.json")
    ap.add_argument("--dry-run", action="store_true",
                    help="offset 만 해결하고 멈춘다(조립 전 확인용)")
    a = ap.parse_args()

    with open(a.stack, encoding="utf-8") as f:
        spec = json.load(f)
    res = build_stack(spec, os.path.dirname(os.path.abspath(a.stack)), a.dry_run)

    if a.dry_run:
        print("\n=== offset 리포트 (조립하지 않음) ===")
        for r in res:
            print(f"  {r['id']:6s} z={r['z']:>8.0f}  offset={r['offset']}  {r['evidence']}")
        return
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    tot = {k: len(v) for k, v in res["elements"].items() if v}
    print(f"\n조립 완료 → {a.out}\n  층 {len(res['floors'])}개, {tot}")


if __name__ == "__main__":
    main()
