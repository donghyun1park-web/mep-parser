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
TERMINAL_MM = 300.0      # 장비·단말 외곽에서 이 안에서 **그쪽을 향해** 끝나면 단말 끝
TERMINAL_FACING = 0.5    # 끝의 진행 방향과 단말 방향의 cos — 옆을 지나가는 경로를 단말로 삼키지 않는다
KIND_LABELS = {"straight": "직선", "elbow": "엘보", "tee": "티"}
ROUTINE_KINDS = ("straight", "elbow")   # 일괄 확정으로 **보여 줄** 종류 — 규칙은 여기 한 줄뿐이다(`_entry`)


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


def _faces(end, shell):
    """끝이 그 장비·단말 **쪽을 향해** 끝나는가 — 옆을 스쳐 지나가는 경로는 단말이 아니다.

    실측(단위세대 환기, 디퓨저 33개): 덕트 끝에서 단말 외곽까지 150mm 안은 0개, 300mm 안이 9개이고
    그중 8개는 끝이 그 단말을 향한다. 나머지 하나(203mm)는 **반대 방향**이라 거리만 보면 삼킨다."""
    if end["dir"] is None:
        return True                                  # 수직으로만 끝난 경로는 방향을 물을 수 없다
    centre = shell.centroid
    vx, vy = centre.x - end["xy"][0], centre.y - end["xy"][1]
    distance = math.hypot(vx, vy)
    if distance < 1e-6:
        return True
    return (end["dir"][0] * vx + end["dir"][1] * vy) / distance >= TERMINAL_FACING


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


def _entry(kind, gap, a, ra, b_xy, rb, b_port, corner, at_mm=None):
    eids = [ra["rec"].get("eid"), rb["rec"].get("eid")]
    key = kind + "|" + "|".join(sorted(["%s:%s" % (eids[0], a["port"]), "%s:%s" % (eids[1], b_port)]))
    pts = [a["xy"]] + ([corner] if corner else []) + [b_xy]
    entry = {"id": "gap:" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12], "kind": kind, "category": ra["cat"],
             "systems": [ra["system"], rb["system"]], "eids": eids, "ports": [a["port"], b_port],
             "points": [[round(float(p[0]), 1), round(float(p[1]), 1)] for p in pts], "gap_mm": round(gap, 1),
             "sizes": [ra["size"], rb["size"]], "size_change": ra["size"] != rb["size"],
             "level": ra["rec"].get("level")}
    # 한 건씩 보지 않아도 되는 후보 — **묶어서 보여 주는 기준이지 자동으로 적용하는 기준이 아니다.**
    # 티는 뺀다: 가지는 계통 위상과 물량(티 피팅 · 줄기의 `at_mm`)을 바꾼다. 규격이 바뀌는 자리도 뺀다 —
    # 레듀서가 서는 곳이라 도면을 봐야 한다.
    entry["routine"] = kind in ROUTINE_KINDS and not entry["size_change"]
    if at_mm is not None:
        entry["at_mm"] = round(float(at_mm), 3)     # 가지(tee)가 줄기의 어디에 붙는가 — 이음 기록에 그대로 쓴다
    # 후보 판정은 높이 범위가 겹치는지도 본다 — 그 높이가 가정이면 후보도 가정 위에 서 있다.
    assumed = sorted(set(ra["basis"]["assumed"] + rb["basis"]["assumed"]))
    entry["z_basis"] = "assumed" if assumed else "declared"
    if assumed:
        entry["assumed"] = assumed
    return entry


def apply_bridges(geometry, bridges, candidates):
    """사람이 확정한 이음 후보를 `joints` 로 기록한다 → {"applied", "orphaned"}. **형상은 그대로다.**

    확정은 후보 id 로 저장한다(프로젝트 파일의 `bridges`). id 는 두 부재의 EID·포트·종류에서 나오므로 도면이
    바뀌어 그 후보가 사라지면 **적용하지 않고** `orphaned` 로 말한다 — 없던 이음이 조용히 남지 않게. 기록한
    이음 참조는 `basis: "bridged"` 와 선언한 틈(`gap_mm`)을 들고 다니고, `geom_contract.joint_problems` 는 그
    틈까지는 '구성원이 떨어졌다' 로 보고하지 않는다."""
    by_eid = {}
    for cat in GC.ROUTE_CATS:
        for rec in (geometry.get("elements") or {}).get(cat) or []:
            if rec.get("eid"):
                by_eid.setdefault(str(rec["eid"]), []).append(rec)
    live = {str(c["id"]): c for c in candidates or []}
    applied, orphaned = [], []
    for bridge in bridges or []:
        bid = str((bridge or {}).get("id") or "")
        current = live.get(bid)
        if current is None:
            orphaned.append({"id": bid, "reason": "candidate_gone"})
            continue
        members = [by_eid.get(str(eid)) or [] for eid in current["eids"]]
        if any(len(m) != 1 for m in members):
            orphaned.append({"id": bid, "reason": "record_missing_or_ambiguous"})
            continue
        jid = "j:" + bid.split(":", 1)[-1]
        for rec, port in zip([m[0] for m in members], current["ports"]):
            ref = {"id": jid, "port": port, "basis": "bridged", "gap_mm": current["gap_mm"]}
            if port == "tap":
                ref["at_mm"] = float(current.get("at_mm") or 0.0)
            rec.setdefault("joints", []).append(ref)
        # 확정한 자리는 다음 재파싱부터 **후보 목록에서 사라진다**(이미 이어진 끝이므로). 그래서 화면이 확정한
        # 이음을 보여 주고 취소까지 하려면 후보 내용을 여기에 실어 보내야 한다 — id 만으로는 위치를 못 찾는다.
        applied.append({k: current[k] for k in ("id", "kind", "category", "systems", "eids", "ports",
                                                "points", "gap_mm", "level") if k in current})
    return {"applied": applied, "orphaned": orphaned}


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
                         "basis": GC.height_basis(cat, rec, params),
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
                    best = (gap, j, (p.x, p.y), along)
        if best:
            gap, j, xy, along = best
            used.add(k)
            a["status"] = "tee"
            candidates.append(_entry("tee", gap, a, ra, xy, runs[j], "tap", None, at_mm=along))
            links.append((a["run"], j))

    # 장비·단말에서 끝나는 것과 **슬리브를 지나 모델 밖으로 나가는 것**은 다르다 — 둘 다 '열림' 이 아니다.
    from clash_review import SLEEVE_MARGIN_MM
    shells, sleeves = [], []
    for rec in elements.get("equipment") or []:
        try:
            poly = (Point(rec["center"][:2]).buffer(float(rec["radius"])) if rec.get("kind") == "circle"
                    else Polygon([p[:2] for p in rec.get("points") or []]).buffer(0))
        except Exception:
            continue
        if poly.is_empty:
            continue
        role = rec.get("role") or (rec.get("overrides") or {}).get("role")
        (sleeves if role == "sleeve" else shells).append(poly)
    for e in ends:
        if e["status"] != "open":
            continue
        pt = Point(e["xy"])
        if any(s.distance(pt) <= SLEEVE_MARGIN_MM for s in sleeves):
            e["status"] = "sleeve"
        elif any(s.distance(pt) <= TERMINAL_MM and _faces(e, s) for s in shells):
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
               "routine": sum(1 for c in candidates if c["routine"]),
               "by_status": dict(Counter(e["status"] for e in ends)), "skipped_routes": skipped,
               # 평면도에는 높이가 없다 — 가정 높이로 나온 판정이 몇 건인지 요약이 말한다(고치지 않고 말한다).
               "assumed_basis": {"candidates": sum(1 for c in candidates if c["z_basis"] == "assumed"),
                                 "open_ends": sum(1 for e in ends if runs[e["run"]]["basis"]["z"] == "assumed")},
               "by_system": [dict(row, groups=len(row["groups"]), groups_with_candidates=len(row["groups_with_candidates"]))
                             for _key, row in sorted(by_system.items())]}
    open_ends = [{"eid": runs[e["run"]]["rec"].get("eid"), "category": runs[e["run"]]["cat"],
                  "system": runs[e["run"]]["system"], "port": e["port"],
                  "at": [round(e["xy"][0], 1), round(e["xy"][1], 1)], "z": round(e["z"], 1),
                  "z_basis": runs[e["run"]]["basis"]["z"],
                  "status": e["status"], "level": runs[e["run"]]["rec"].get("level")} for e in ends]
    return {"summary": summary, "candidates": candidates, "conflicts": conflicts, "open_ends": open_ends,
            "review_widths": REVIEW_WIDTHS,
            "method": ("connected = shared joint ids only; candidates between open ends of the same category, system "
                       "and height band: straight (facing within 10 deg), elbow (turn >= 60 deg) or tee (end ray meets "
                       "another route), nearest first, one per end, within 2.5 x the larger section width; model unchanged")}
