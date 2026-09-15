"""설비 경로 연결성 — 조각 · 이어진 무리 · 끊긴 끝, 그리고 끊긴 끝끼리의 이음 **후보**. 모델은 바꾸지 않는다.

`source_coverage.complete` 는 선택한 원본을 **다 담았다**는 뜻이지 계통이 **이어졌다**는 뜻이 아니다(실측: 실무
단위세대 환기 덕트 37조각 · 이음 0 인데 complete). 여기서 둘을 따로 센다.

- 이어짐은 `joints`(도면이 이어 그린 곳 — `geom_contract.assign_joints`)만 친다.
- 끊긴 끝끼리는 같은 카테고리 · 계통 · 높이 범위이고 **직선**(마주 봄) · **엘보**(크게 꺾여 만남) · **티**(다른 경로의
  옆면에 닿음)로 설명될 때만 후보다. 가까울 뿐인 끝(나란히 선 끝)은 후보가 아니고, 계통만 다르면 `conflicts` 다.
- 한 끝은 한 후보에만 — 가장 가까운 짝부터. 이음으로 확정하는 것은 사람이다(후보를 모델에 쓰지 않는다).

거리만으로 잇는 규칙은 같은 도면에서 300mm 안 28곳을 이었고 그중 SA↔RA 2 · 기하상 불가 3 이었다. 이 규칙은
끝끼리 22곳(직선 6 · 엘보 16) + 티 4 를 후보로 낸다. 탐색 거리는 **단면 폭의 배수**다 — 고정 300mm 는 Ø15.9 난방
코일에선 분배기 앞의 서로 다른 회로 끝 4쌍을 엘보로 이었다(폭 배수로 0, 환기 후보는 그대로 26)."""
import hashlib
import math
from collections import Counter

from shapely.geometry import LineString, Point, Polygon
from shapely.strtree import STRtree

import geom_contract as GC

REVIEW_WIDTHS = 2.5      # 이음 후보를 찾는 틈 = 두 단면 중 큰 폭 × 이 값(엘보·레듀서 크기는 단면을 따라간다)
STRAIGHT_DEG = 10.0      # 마주 보는 두 끝의 방향 차 허용
ELBOW_MIN_DEG = 60.0     # 이보다 크게 꺾여 만나야 엘보
SLACK_MM = 20.0          # 직선 끝의 옆 어긋남 · 엘보 모서리가 끝 뒤로 들어간 거리(중심선을 조금 넘겨 그린 경우) 허용
TERMINAL_MM = 150.0      # 장비·단말 외곽에서 이 안에서 끝나면 단말 끝
KIND_LABELS = {"straight": "직선", "elbow": "엘보", "tee": "티"}


def _find(parent, i):
    while parent[i] != i:
        parent[i] = parent[parent[i]]
        i = parent[i]
    return i


def _union(parent, i, j):
    a, b = _find(parent, i), _find(parent, j)
    if a != b:
        parent[max(a, b)] = min(a, b)


def _outward(pts, port):
    """끝에서 경로 바깥을 향하는 평면 단위벡터. 수직 구간뿐이면 None."""
    seq = pts if port == "start" else pts[::-1]
    for p in seq[1:]:
        dx, dy = seq[0][0] - p[0], seq[0][1] - p[1]
        n = math.hypot(dx, dy)
        if n > 1e-6:
            return dx / n, dy / n
    return None


def _shape(a, b, width, reach):
    """두 끝 사이를 직선·엘보로 설명할 수 있으면 (종류, 엘보 모서리) — 아니면 None."""
    da, db = a["dir"], b["dir"]
    if da is None or db is None:
        return None
    vx, vy = b["xy"][0] - a["xy"][0], b["xy"][1] - a["xy"][1]
    dot, cross = da[0] * db[0] + da[1] * db[1], da[0] * db[1] - da[1] * db[0]
    if (dot <= -math.cos(math.radians(STRAIGHT_DEG)) and da[0] * vx + da[1] * vy > 0
            and abs(da[0] * vy - da[1] * vx) <= max(SLACK_MM, 0.25 * width)):
        return "straight", None
    if abs(cross) >= math.sin(math.radians(ELBOW_MIN_DEG)):
        t = (vx * db[1] - vy * db[0]) / cross          # 모서리까지 — a 에서
        s = (vx * da[1] - vy * da[0]) / cross          # — b 에서
        if -SLACK_MM <= t <= reach and -SLACK_MM <= s <= reach:
            return "elbow", (a["xy"][0] + da[0] * t, a["xy"][1] + da[1] * t)
    return None


def _entry(kind, gap, a, ra, b_xy, rb, b_port, corner):
    eids = [ra["rec"].get("eid"), rb["rec"].get("eid")]
    key = kind + "|" + "|".join(sorted(["%s:%s" % (eids[0], a["port"]), "%s:%s" % (eids[1], b_port)]))
    pts = [a["xy"]] + ([corner] if corner else []) + [b_xy]
    return {"id": "gap:" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12], "kind": kind, "category": ra["cat"],
            "systems": [ra["system"], rb["system"]], "eids": eids, "ports": [a["port"], b_port],
            "points": [[round(float(p[0]), 1), round(float(p[1]), 1)] for p in pts], "gap_mm": round(gap, 1),
            "sizes": [ra["size"], rb["size"]], "size_change": ra["size"] != rb["size"],
            "level": ra["rec"].get("level")}


def analyze(geometry):
    """geometry dict → {"summary", "candidates", "conflicts", "open_ends", "method"}. 입력을 바꾸지 않는다."""
    params = geometry.get("params")
    elements = geometry.get("elements") or {}
    runs, skipped = [], 0
    for cat in GC.ROUTE_CATS:
        for rec in elements.get(cat) or []:
            if rec.get("geometry_mode") == "footprint":
                continue                        # 외곽선으로 만든 설비는 축선 끝이 없다(이음도 안 매긴다)
            try:
                pts = GC.route_points(cat, rec)
                sec = GC.mep_section(cat, rec, params)
                w, h = (sec["diameter"],) * 2 if sec.get("diameter") else (sec["width_mm"], sec["height_mm"])
                size = "Ø%g" % w if sec.get("diameter") else "%g×%g" % (w, h)
            except Exception:
                skipped += 1
                continue
            if len(pts) < 2:
                skipped += 1
                continue
            runs.append({"cat": cat, "rec": rec, "pts": pts, "w": float(w), "h": float(h), "size": size,
                         "system": (rec.get("overrides") or {}).get("system", rec.get("system"))})

    parent, members = list(range(len(runs))), {}
    for i, run in enumerate(runs):
        for ref in run["rec"].get("joints") or []:
            members.setdefault(str(ref.get("id")), []).append(i)
    for idx in members.values():
        for j in idx[1:]:
            _union(parent, idx[0], j)

    ends = []
    for i, run in enumerate(runs):
        if run["rec"].get("closed") or math.dist(run["pts"][0], run["pts"][-1]) < 1e-6:
            continue
        joined = {ref.get("port") for ref in run["rec"].get("joints") or []}
        for port, p in (("start", run["pts"][0]), ("end", run["pts"][-1])):
            if port not in joined:
                ends.append({"run": i, "port": port, "xy": (float(p[0]), float(p[1])), "z": float(p[2]),
                             "dir": _outward(run["pts"], port), "status": "open"})

    def reach(*idx):
        return REVIEW_WIDTHS * max(runs[i]["w"] for i in idx)

    # 끝 × 끝 — 격자로 이웃 칸만 본다(칸 = 가장 큰 탐색 거리)
    cell = max([1.0] + [REVIEW_WIDTHS * run["w"] for run in runs])
    grid = {}
    for k, e in enumerate(ends):
        grid.setdefault((math.floor(e["xy"][0] / cell), math.floor(e["xy"][1] / cell)), []).append(k)
    same, other = [], []
    for k, a in enumerate(ends):
        gx, gy = math.floor(a["xy"][0] / cell), math.floor(a["xy"][1] / cell)
        for m in sorted(n for dx in (-1, 0, 1) for dy in (-1, 0, 1) for n in grid.get((gx + dx, gy + dy), ()) if n > k):
            b = ends[m]
            ra, rb = runs[a["run"]], runs[b["run"]]
            if a["run"] == b["run"] or ra["cat"] != rb["cat"]:
                continue
            gap, limit = math.dist(a["xy"], b["xy"]), reach(a["run"], b["run"])
            if gap > limit or abs(a["z"] - b["z"]) > (ra["h"] + rb["h"]) / 2:
                continue
            shape = _shape(a, b, max(ra["w"], rb["w"]), limit)
            if shape:
                (same if ra["system"] == rb["system"] else other).append((gap, k, m) + shape)

    def eid_port(k):
        return str(runs[ends[k]["run"]]["rec"].get("eid")), ends[k]["port"]

    used, links, candidates, conflicts = set(), [], [], []
    for pool, out, status in ((same, candidates, "candidate"), (other, conflicts, "conflict")):
        for gap, k, m, kind, corner in sorted(pool, key=lambda p: (p[0], eid_port(p[1]), eid_port(p[2]))):
            if k in used or m in used:
                continue
            used.update((k, m))
            a, b = ends[k], ends[m]
            a["status"] = b["status"] = status
            out.append(_entry(kind, gap, a, runs[a["run"]], b["xy"], runs[b["run"]], b["port"], corner))
            if status == "candidate":
                links.append((a["run"], b["run"]))

    # 남은 끝 → 다른 경로의 옆면(티). 줄기의 끝 근처에 닿는 것은 위 직선·엘보 몫이다.
    lines = [LineString([p[:2] for p in run["pts"]]) for run in runs]
    tree = STRtree(lines) if lines else None
    for k, a in enumerate(ends):
        if k in used or a["dir"] is None or tree is None:
            continue
        ra, limit = runs[a["run"]], reach(a["run"])
        ray = LineString([a["xy"], (a["xy"][0] + a["dir"][0] * limit, a["xy"][1] + a["dir"][1] * limit)])
        best = None
        for j in sorted(int(n) for n in tree.query(ray)):
            rb, line = runs[j], lines[j]
            if j == a["run"] or rb["cat"] != ra["cat"] or rb["system"] != ra["system"]:
                continue
            zs = [p[2] for p in rb["pts"]]
            if a["z"] + ra["h"] / 2 < min(zs) - rb["h"] / 2 or a["z"] - ra["h"] / 2 > max(zs) + rb["h"] / 2:
                continue
            hit = ray.intersection(line)
            for p in getattr(hit, "geoms", [hit]):
                if p.is_empty or p.geom_type != "Point":
                    continue                    # 나란히 겹친 구간은 가지가 아니다
                along, gap = line.project(p), math.dist(a["xy"], (p.x, p.y))
                if gap > 1e-6 and rb["w"] / 2 < along < line.length - rb["w"] / 2 and (best is None or gap < best[0]):
                    best = (gap, j, (p.x, p.y))
        if best:
            gap, j, xy = best
            used.add(k)
            a["status"] = "tee"
            candidates.append(_entry("tee", gap, a, ra, xy, runs[j], "tap", None))
            links.append((a["run"], j))

    shells = []
    for rec in elements.get("equipment") or []:
        try:
            poly = (Point(rec["center"][:2]).buffer(float(rec["radius"])) if rec.get("kind") == "circle"
                    else Polygon([p[:2] for p in rec.get("points") or []]).buffer(0))
        except Exception:
            continue
        if not poly.is_empty:
            shells.append(poly)
    shell_tree = STRtree(shells) if shells else None
    for e in ends:
        if e["status"] == "open" and shell_tree is not None:
            pt = Point(e["xy"])
            if any(shells[int(n)].distance(pt) <= TERMINAL_MM for n in shell_tree.query(pt.buffer(TERMINAL_MM))):
                e["status"] = "terminal"

    joined = list(parent)
    for i, j in links:
        _union(joined, i, j)
    by_system = {}
    for i, run in enumerate(runs):
        row = by_system.setdefault((run["cat"], str(run["system"])), {
            "category": run["cat"], "system": run["system"], "runs": 0, "groups": set(),
            "groups_with_candidates": set(), "open_ends": 0, "candidates": 0})
        row["runs"] += 1
        row["groups"].add(_find(parent, i))
        row["groups_with_candidates"].add(_find(joined, i))
    for e in ends:
        by_system[(runs[e["run"]]["cat"], str(runs[e["run"]]["system"]))]["open_ends"] += 1
    for c in candidates:
        by_system[(c["category"], str(c["systems"][0]))]["candidates"] += 1
    summary = {"runs": len(runs), "groups": len({_find(parent, i) for i in range(len(runs))}),
               "groups_with_candidates": len({_find(joined, i) for i in range(len(runs))}),
               "open_ends": len(ends), "candidates": len(candidates),
               "by_kind": dict(Counter(c["kind"] for c in candidates)), "conflicts": len(conflicts),
               "by_status": dict(Counter(e["status"] for e in ends)), "skipped_routes": skipped,
               "by_system": [dict(row, groups=len(row["groups"]), groups_with_candidates=len(row["groups_with_candidates"]))
                             for _key, row in sorted(by_system.items())]}
    open_ends = [{"eid": runs[e["run"]]["rec"].get("eid"), "category": runs[e["run"]]["cat"],
                  "system": runs[e["run"]]["system"], "port": e["port"],
                  "at": [round(e["xy"][0], 1), round(e["xy"][1], 1)], "z": round(e["z"], 1),
                  "status": e["status"], "level": runs[e["run"]]["rec"].get("level")} for e in ends]
    return {"summary": summary, "candidates": candidates, "conflicts": conflicts, "open_ends": open_ends,
            "review_widths": REVIEW_WIDTHS,
            "method": ("connected = shared joint ids only; candidates between open ends of the same category, system "
                       "and height band: straight (facing within 10 deg), elbow (turn >= 60 deg) or tee (end ray meets "
                       "another route), nearest first, one per end, within 2.5 x the larger section width; model unchanged")}
