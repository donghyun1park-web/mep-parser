"""
freecad_builder.py  —  Phase 3+5
geometry.json 을 읽어 FreeCAD Arch(BIM) 객체를 생성하고
.FCStd 와 .ifc 로 내보낸다.

★ 반드시 FreeCAD 의 파이썬(freecadcmd)으로 실행한다 (일반 python3 아님):
    freecadcmd freecad_builder.py geometry.json out_model
    -> out_model.FCStd, out_model.ifc 생성

설계:
- 벽은 Arch.makeWall(baseline, width, height) 로 생성 -> 조인트/IFC 매핑이 견고.
- 기둥/슬래브는 닫힌 윤곽 -> 면 -> Arch.makeStructure 로 솔리드화.
- 결정론적. LLM 호출 없음. (LLM 은 실패 케이스 보조용으로만 차후 연결)
"""
import json
import math
import os
import shutil
import sys
import tempfile
import copy

# Windows 한글 출력 크래시 방지
if hasattr(sys.stdout, 'encoding') and sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

import FreeCAD as App
import Part
import Draft
import Arch

# z 기준면 규약은 geom_contract 에만 존재한다. 여기서 재구현하지 말 것.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import geom_contract as GC
import artifact_validation as AV


def vec(p, z=0.0):
    return App.Vector(p[0], p[1], z)


_PSET = "Pset_MEPParser"

# 재질은 **layer_map 의 opts `material=` 에 적힌 것만** 붙는다. 카테고리로 추정하지
# 않는다 — wall→콘크리트는 조적벽에서 바로 틀리고, 물량·내화·열관류 계산이 전부
# 그 위에 얹히므로 조용히 틀린 재질은 형상 오류보다 오래 살아남는다.
_MATERIALS = {}          # (문서명, 재질명) → Arch Material. 부재마다 새로 만들지 않는다
MATERIALS_APPLIED = {}   # 재질명 → 부여된 객체 수(build.json 으로 자기보고)
# 카테고리 → 객체를 못 만든 레코드 목록. **비어 있어야 한다.**
# 벽에서 이 카운터가 없던 동안 72개가 아무 경고 없이 빠진 채 납품될 수 있었다.
UNBUILT = {}
PROVENANCE = {}
BUILT_RECORDS = {}
OPENING_RESULTS = []
OPENING_LEAVES = []


def _material(obj, name):
    key = (obj.Document.Name, name)
    if key not in _MATERIALS:
        _MATERIALS[key] = Arch.makeMaterial(name=name)
    return _MATERIALS[key]


def set_ifc_props(obj, rec):
    """레코드의 QA 정보를 **IFC 로 나가는** 속성으로 심는다.

    `addProperty("App::PropertyString", …)` 는 FreeCAD 문서 안에만 남고 IFC 로는
    나가지 않는다 — 실측: 생성된 .ifc 의 IFCPROPERTYSET 이 0 이었다. 그래서 Bonsai
    (Blender)로 열면 형상만 보이고 부재명·EID·needs_review 가 전부 없었다.
    검수·간섭 검토를 IFC 뷰어에서 하려면 알맹이가 같이 가야 한다.

    FreeCAD 내보내기 규약(importers.exportIFC.getPropertyData):
        obj.IfcProperties = {"이름": "pset;;타입;;값"}
    값이 빈 문자열이면 exporter 가 그 속성을 버리므로, 없는 항목은 아예 안 넣는다.
    """
    sec = rec.get("section") or {}
    vals = [
        # EID 는 Bonsai 에서 찾은 문제를 edits.json 으로 되돌리는 열쇠다.
        ("EID",           "IfcText", rec.get("eid")),
        ("SourceEIDs",    "IfcText", json.dumps(AV.source_eids(rec), ensure_ascii=False)),
        ("RunId",         "IfcText", PROVENANCE.get("run_id")),
        ("InputSHA256",   "IfcText", PROVENANCE.get("input_sha256")),
        ("ProjectId",     "IfcText", str(PROVENANCE.get("project_id") or "")),
        ("Revision",      "IfcText", str(PROVENANCE.get("revision") if PROVENANCE.get("revision") is not None else "")),
        ("Layer",         "IfcLabel",      rec.get("layer")),
        ("Level",         "IfcLabel",      rec.get("level")),
        ("MemberName",    "IfcLabel",      sec.get("name") or rec.get("member_name")),
        ("Section",       "IfcLabel",      sec.get("size")),
        ("Pairing",       "IfcLabel",      rec.get("pairing")),
        ("WidthDetected", "IfcReal",       rec.get("width_detected")),
        # 이 둘이 핵심이다 — 뷰어에서 NeedsReview 로 필터하면 얇은 오결합이 잡힌다.
        ("NeedsReview",   "IfcBoolean",    bool(rec.get("needs_review"))),
        ("ReviewReason",  "IfcLabel",      rec.get("review_reason")
                                           or rec.get("schedule_match")),
    ]
    props = {k: f"{_PSET};;{t};;{v}" for k, t, v in vals if v not in (None, "")}
    try:
        obj.IfcProperties = props
        BUILT_RECORDS[obj.Name] = rec
    except Exception as _pe:                      # IfcProperties 없는 객체(Part::Feature 등)
        print(f"  [warn] IFC 속성 부여 실패({getattr(obj, 'Label', '?')}): {_pe}")

    mat = (rec.get("overrides") or {}).get("material")
    if mat and hasattr(obj, "Material"):
        try:
            obj.Material = _material(obj, str(mat))
            MATERIALS_APPLIED[str(mat)] = MATERIALS_APPLIED.get(str(mat), 0) + 1
        except Exception as _me:
            print(f"  [warn] 재질 '{mat}' 부여 실패({getattr(obj, 'Label', '?')}): {_me}")


def make_wire(points, closed, doc=None, label="_wall_base"):
    """Part.makePolygon → Part::Feature 베이스라인 생성.
    개별 직선 세그먼트(2점) 용. self-intersecting 없음."""
    pts = []
    for p in points:
        v = vec(p)
        if not pts or (pts[-1] - v).Length > 1.0:
            pts.append(v)
    if closed and len(pts) > 1 and (pts[-1] - pts[0]).Length <= 1.0:
        pts.pop()
    if len(pts) < 2:
        return None
    # 세그먼트 길이 최소값 확인 (너무 짧은 세그먼트 → Part.makePolygon 실패)
    total_len = sum((pts[i+1]-pts[i]).Length for i in range(len(pts)-1))
    if total_len < 1.0:
        return None
    try:
        poly_pts = pts + [pts[0]] if closed else pts
        wire_shape = Part.makePolygon(poly_pts)
        d = doc or App.ActiveDocument
        feat = d.addObject("Part::Feature", label)
        feat.Shape = wire_shape
        return feat
    except Exception:
        return None


# ── 벽 체이닝: snap_wall_corners로 정렬된 끝점 기준 연결 ──────────────────────
# 목적: 연속 세그먼트 → 하나의 Draft Wire → Arch.makeWall 1개 → 코너 Miter 자동
# 핵심: snap_wall_corners(50mm tol) 이후 실제 연결된 끝점은 동일 좌표.
#       1mm 판정으로 오연결(T접합 근처) 방지.
_CHAIN_SNAP = 1.0   # 끝점 연결 판정 거리(mm) — snap 후 동일 좌표이므로 1mm 충분

def _chain_wall_segments(walls):
    """열린(non-closed) 벽 레코드들을 끝점 연결로 체이닝.
    Returns: list of (representative_el, chained_centerline_pts, member_indices)
      - representative_el : 체인의 첫 레코드(width/height 참조용)
      - chained_centerline_pts : 이어붙인 다중점 리스트
      - member_indices : 체인에 속한 원본 인덱스 리스트
    분리된 벽(연결 없음)은 단독 체인으로 반환.
    닫힌(pairing="closed") 레코드는 체이닝 대상 제외 — 별도 처리."""
    import math

    def cl(el):
        return el.get("centerline") or el.get("points", [])

    def pt_key(p):
        return (round(p[0] / _CHAIN_SNAP), round(p[1] / _CHAIN_SNAP))

    def compatible(a, b):
        return (GC.width_of(a) == GC.width_of(b) and GC.z_range("wall", a) == GC.z_range("wall", b)
                and a.get("level") == b.get("level"))

    # 열린 벽만 체이닝 대상
    open_idx = [i for i, w in enumerate(walls)
                if not (w.get("closed") or w.get("pairing") == "closed")
                and len(cl(w)) >= 2]

    # 각 끝점 → (wall_idx, 'start'|'end') 매핑
    from collections import defaultdict
    ep_map = defaultdict(list)
    for i in open_idx:
        pts = cl(walls[i])
        ep_map[pt_key(pts[0])].append((i, "start"))
        ep_map[pt_key(pts[-1])].append((i, "end"))

    visited = set()
    chains = []

    for start in open_idx:
        if start in visited:
            continue
        visited.add(start)
        pts0 = cl(walls[start])
        chain_pts = list(pts0)
        chain_ids = [start]

        # 루프 감지용: 체인의 모든 점을 key set으로 관리
        chain_pt_keys = {pt_key(p) for p in chain_pts}

        # 앞쪽(tail) 연장
        while True:
            tail = chain_pts[-1]
            candidates = [x for x in ep_map.get(pt_key(tail), [])
                          if x[0] not in visited and compatible(walls[start], walls[x[0]])]
            if not candidates:
                break
            j, end = candidates[0]
            nxt = cl(walls[j])
            # 추가될 새 점들 중 이미 체인에 있는 점이 있으면 루프 → 중단
            new_pts = nxt[1:] if end == "start" else list(reversed(nxt))[1:]
            if any(pt_key(p) in chain_pt_keys for p in new_pts):
                break
            visited.add(j)
            chain_ids.append(j)
            chain_pts.extend(new_pts)
            chain_pt_keys.update(pt_key(p) for p in new_pts)

        # 뒤쪽(head) 연장
        while True:
            head = chain_pts[0]
            candidates = [x for x in ep_map.get(pt_key(head), [])
                          if x[0] not in visited and compatible(walls[start], walls[x[0]])]
            if not candidates:
                break
            j, end = candidates[0]
            nxt = cl(walls[j])
            new_pts = list(nxt) if end == "end" else list(reversed(nxt))
            new_pts = new_pts[:-1]  # 마지막 점(=head)은 이미 체인에 있음
            if any(pt_key(p) in chain_pt_keys for p in new_pts):
                break
            visited.add(j)
            chain_ids.insert(0, j)
            chain_pts = new_pts + chain_pts
            chain_pt_keys.update(pt_key(p) for p in new_pts)

        chains.append((walls[start], chain_pts, chain_ids))

    return chains


def _is_closed_solid(el):
    """①(solid extrusion) 대상인가. ②는 **정확히 이것의 여집합**이어야 한다.

    두 분기의 조건이 어긋나면 그 사이로 벽이 조용히 사라진다. 실측: 닫힘 표시가
    붙은 2점 레코드 36개가 ①(≥3점 필요)에도 ②(pairing=="closed" 제외)에도
    안 걸려 IFC 에서 통째로 증발했다 — 형상오류 0, 검사 전부 통과."""
    return (el.get("kind") == "polyline"
            and (el.get("closed", False) or el.get("pairing") == "closed")
            and len(el.get("centerline") or el.get("points") or []) >= 3)


def repair_null_walls(objs):
    """Arch 오프셋이 실패해 **형상이 비어버린** 벽을 축선+폭 압출로 되살린다.

    `Arch.makeWall(align="Center")` 은 중심선을 폭의 절반씩 양쪽으로 오프셋하는데,
    긴 다점 체인에서 그 오프셋이 자기교차하면 결과가 null 이 된다(실측: 길이 20m ·
    폭 250mm 벽 하나. 폭 200mm 일 때는 통과했고 실측 두께로 바꾸자 무너졌다).
    **null 은 isValid()==True 라 형상 검사를 통과하고**, IFC 에는 형상 없는
    IfcWall 로 실린다 — Bonsai 에서 IfcWall 398개 중 397개만 형상이 있었다.

    되살리는 규칙은 `geom_contract.beam_footprint` 단독이다(축선 한 구간 + 폭 →
    사각 footprint). 구간별 상자를 fuse 하므로 오프셋 자기교차가 원천적으로 없다.
    Base 와이어의 Edge 에서 좌표를 읽으므로 순서에 의존하지 않는다.

    ★ recompute **이후**, build_openings **이전**에 호출해야 한다. 이후 recompute
      하면 파라메트릭 재계산이 다시 null 로 덮는다(개구부 cut 과 같은 제약)."""
    n = 0
    for obj in objs:
        s = getattr(obj, "Shape", None)
        if s is None or not s.isNull():
            continue
        bs = getattr(getattr(obj, "Base", None), "Shape", None)
        w = getattr(getattr(obj, "Width", None), "Value", 0.0)
        h = getattr(getattr(obj, "Height", None), "Value", 0.0)
        if bs is None or bs.isNull() or w <= 0 or h <= 0:
            continue
        z = obj.Placement.Base.z
        solid = None
        for e in bs.Edges:
            v = e.Vertexes
            if len(v) != 2:
                continue
            ring = GC.beam_footprint([v[0].X, v[0].Y], [v[1].X, v[1].Y], w)
            if not ring:
                continue
            wire = Part.makePolygon(
                [App.Vector(p[0], p[1], z) for p in ring]
                + [App.Vector(ring[0][0], ring[0][1], z)])
            part = Part.Face(wire).extrude(App.Vector(0, 0, h))
            solid = part if solid is None else solid.fuse(part)
        if solid is None:
            continue
        try:
            obj.Shape = solid
            n += 1
            print(f"  [fix] 빈 형상 벽 복구: {obj.Label} "
                  f"(폭 {w:.0f}mm · 축선 {bs.Length:.0f}mm — Arch 오프셋 실패)")
        except Exception as e:
            print(f"  [warn] {getattr(obj, 'Label', '?')} 복구 실패: {e}")
    return n


def build_walls(doc, walls, params):
    objs = []
    src_els = []
    idx_map = {}
    error_group = None

    # ── ① 닫힌 폴리선(pairing="closed"): solid extrusion ────────────────────────
    for i, el in enumerate(walls):
        if not _is_closed_solid(el):
            continue
        baseline = el.get("centerline") or el.get("points", [])
        z_base, _z1 = GC.z_range("wall", el, params)
        height = _z1 - z_base
        dxf_id = el.get("handle") or f"CLOSEDWALL_{i}"
        try:
            pts_3d = [App.Vector(p[0], p[1], z_base) for p in baseline]
            if (pts_3d[-1] - pts_3d[0]).Length > 1.0:
                pts_3d.append(pts_3d[0])
            wire = Part.makePolygon(pts_3d)
            face = Part.Face(wire)
            solid = face.extrude(App.Vector(0, 0, height))
            feat = doc.addObject("Part::Feature", f"ClosedWall_{i}")
            feat.Shape = solid
            struct = Arch.makeStructure(feat)
            # ★ Arch.makeStructure 의 IfcType 기본값은 "Column" 이다. 안 적으면
            # 닫힌 폴리선 벽이 전부 IfcColumn 으로 나간다(실측: sample_plan 의
            # walls=3 이 IFC 에서 IfcWall 2 + IfcColumn 5 였다). 형상은 맞아서
            # 검사도 통과하고, 뷰어에서 기둥 물량만 부풀어 오른다.
            struct.IfcType = "Wall"
            struct.Label = f"ClosedWall_{i}"
            struct.addProperty("App::PropertyString", "DxfId", "Metadata", "")
            struct.DxfId = dxf_id
            set_ifc_props(struct, el)
            objs.append(struct)
            src_els.append(el)
            idx_map[i] = struct
        except Exception as e:
            print(f"[warn] ClosedWall_{i} 생성 실패: {e}")

    # ── ② 열린 폴리선: 체이닝으로 묶어서 생성 (사용자 요청: 잘게 나누지 말고 하나의 벽체로) ───────────────────────────
    # repair_single_walls 로 모든 비-closed 벽이 centerline 기준이 되었으므로 체이닝을 복원합니다.
    open_walls = []
    open_wall_global_indices = []
    for i, el in enumerate(walls):
        if _is_closed_solid(el) or el.get("kind") != "polyline":
            continue                      # ①의 여집합 — 사이에 틈이 없다
        open_walls.append(el)
        open_wall_global_indices.append(i)
        
    chains = _chain_wall_segments(open_walls)

    n_folds = 0
    for c_idx, (base_el, chain_pts, chain_ids) in enumerate(chains):
        if len(chain_pts) < 2:
            continue
        # 체인의 검토 플래그는 **멤버 전체를 OR** 한다. 대표 레코드만 보면 얇은
        # 오결합이 정상 벽과 한 체인에 묶이는 순간 조용히 사라진다
        # (실측: geometry.json 의 thin_pair 55개 중 IFC 에 15개만 남았다).
        # 파서의 merge_collinear_walls 도 같은 규칙으로 needs_review 를 OR 한다.
        chain_el = dict(base_el)
        members = [open_walls[k] for k in chain_ids]
        flagged = [m for m in members if m.get("needs_review")]
        if flagged and not base_el.get("needs_review"):
            chain_el = dict(base_el)
            chain_el["needs_review"] = True
            # 사유에 'chained:' 를 붙인다. 안 붙이면 검토자가 "thin_pair 인데 폭이
            # 200mm?" 로 읽는다 — 폭은 체인 대표 것이고 얇은 건 다른 멤버다.
            _why = flagged[0].get("review_reason") or "needs_review"
            chain_el["review_reason"] = f"chained:{_why}"
        width = GC.width_of(base_el, params, "wall")
        z_base, _z1 = GC.z_range("wall", base_el, params)
        height = _z1 - z_base
        # [실측] 겹치는 동일선상 벽이 체이닝되면 baseline 이 180° 되꺾여(A→B→A방향)
        # Arch.makeWall(align="Center") 의 오프셋이 폭주 → 좌표 1e7 규모의 깨진 솔리드 생성
        # (isValid()=True 라 탐지도 안 됨). 되꺾임 지점에서 잘라 각각 별도 벽으로 만든다.
        subchains = _split_source_members(chain_pts, members)
        created_before = len(objs)
        if len(subchains) > 1:
            n_folds += 1
        for s_idx, (sub_pts, sub_eids) in enumerate(subchains):
            if len(sub_pts) < 2:
                continue
            label = f"Wall_{c_idx}" if len(subchains) == 1 else f"Wall_{c_idx}_{s_idx}"
            try:
                base = make_wire(sub_pts, False, doc=doc, label=f"WallAxis_{label[5:]}")
                if not base:
                    continue
                wall = Arch.makeWall(base, width=width, height=height, align="Center")
                if not wall:
                    continue
                wall.Label = label
                wall.Placement.Base.z = z_base
                wall.addProperty("App::PropertyString", "DxfId", "Metadata", "")
                wall.DxfId = base_el.get("handle") or f"WALL_CHAIN_{c_idx}"
                part_el = dict(chain_el, eid=sub_eids[0], _artifact_source_eids=sub_eids)
                part_el["needs_review"] = any(m.get("needs_review") for m in members if m["eid"] in sub_eids)
                set_ifc_props(wall, part_el)
                objs.append(wall)
                src_els.append(base_el)
                for cid in chain_ids:
                    if open_walls[cid]["eid"] in sub_eids:
                        global_i = open_wall_global_indices[cid]
                        idx_map.setdefault(global_i, []).append(wall)
            except Exception as e:
                print(f"[warn] {label} 체인 생성 실패: {e}")
        if len(objs) - created_before != len(subchains):
            for member in members:
                UNBUILT.setdefault("wall", []).append({"eid": member["eid"],
                    "why": "One or more wall split pieces failed to build"})
    if n_folds:
        print(f"  [fix] 되꺾인 벽 체인 {n_folds}건 분할 (깨진 형상 방지)")

    # ★ 어떤 객체도 만들지 못한 벽 레코드. 체이닝은 여럿→하나라서 수가 줄어드는
    # 것은 정상이지만, idx_map 에 아예 안 들어온 레코드는 IFC 에 존재하지 않는다.
    for i in range(len(walls)):
        if i in idx_map:
            continue
        el = walls[i]
        UNBUILT.setdefault("wall", []).append(
            {"i": i, "eid": el.get("eid"), "layer": el.get("layer"),
             "why": f"pairing={el.get('pairing')} "
                    f"점{len(el.get('centerline') or el.get('points') or [])}개"})
    return objs, idx_map, src_els


def _split_source_members(chain_pts, members):
    """Preserve ordered segment ownership through reversals, including one source
    split across several solids. Geometry overlap does not imply shared identity."""
    owners = []
    for member in members:
        pts = member.get("centerline") or member.get("points") or []
        owners.extend([member["eid"]] * sum(math.dist(a[:2], b[:2]) > 1e-6 for a, b in zip(pts, pts[1:])))
    out, offset = [], 0
    for pts in _split_folded_chain(chain_pts):
        count = len(pts) - 1
        eids = sorted(set(owners[offset:offset+count]))
        if not eids:
            raise ValueError("Wall split lost source segment ownership")
        out.append((pts, eids))
        offset += count
    if offset != len(owners):
        raise ValueError("Wall chain segment/source counts differ")
    return out


def _split_folded_chain(pts, cos_tol=-0.99):
    """벽 baseline 이 180° 되꺾이는 지점에서 분할.

    겹치는 동일선상 벽 세그먼트가 체이닝되면 A→B→A 형태가 되어
    Arch.makeWall 의 중심선 오프셋이 발산한다. 연속 방향벡터의
    코사인이 cos_tol 이하(≈172° 이상 꺾임)면 그 지점에서 자른다.
    """
    import math as _m

    def _dir(a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = _m.hypot(dx, dy)
        return (dx / L, dy / L) if L > 1e-9 else None

    out, cur = [], [pts[0]]
    prev = None
    for a, b in zip(pts, pts[1:]):
        v = _dir(a, b)
        if v is None:
            continue          # 길이 0 세그먼트는 건너뜀
        if prev is not None and (prev[0] * v[0] + prev[1] * v[1]) <= cos_tol:
            out.append(cur)   # 되꺾임 → 여기서 끊음
            cur = [a]
        cur.append(b)
        prev = v
    out.append(cur)
    return [c for c in out if len(c) >= 2]


def _opening_axes(op):
    """opening 의 host 벽 방향(u)·법선(n) 단위벡터. host_dir 없으면 X축 가정."""
    hd = op.get("host_dir")
    if hd and (abs(hd[0]) + abs(hd[1])) > 1e-6:
        import math as _m
        ln = _m.hypot(hd[0], hd[1])
        ux, uy = hd[0] / ln, hd[1] / ln
    else:
        ux, uy = 1.0, 0.0
    return (ux, uy), (-uy, ux)  # (along-wall, normal)


def _opening_solids(op, params):
    """opening 스펙 → (cutter_shape, leaf_shape|None, subtype). 좌표·배향 계산을 한 곳에 모아
    build_openings(다건)와 cut_window_into_wall(라이브 단건)이 공유. doc 미접근(순수 Part)."""
    d = params.get("wall", {})
    wall_h = float(d.get("height", 2800.0))
    margin = 100.0
    c = op.get("center") or [0, 0]
    cx, cy = float(c[0]), float(c[1])
    subtype = op.get("subtype")
    width = float(op.get("width") or (float(op.get("radius", 450.0)) * 2))
    depth = float(op.get("host_width") or d.get("width", 200.0)) + margin
    z_base = float(op.get("z_base", 0.0))
    if subtype == "window":
        sill = float(op.get("sill", 900.0)) + z_base
        oh = float(op.get("height", 1200.0))
    elif subtype == "door":
        sill = z_base
        oh = float(op.get("height", 2100.0))
    else:
        sill = z_base - margin
        oh = wall_h + margin * 2
    (ux, uy), (nx, ny) = _opening_axes(op)
    hw, hd_ = width / 2.0, depth / 2.0
    corners = [
        App.Vector(cx - ux * hw - nx * hd_, cy - uy * hw - ny * hd_, sill),
        App.Vector(cx + ux * hw - nx * hd_, cy + uy * hw - ny * hd_, sill),
        App.Vector(cx + ux * hw + nx * hd_, cy + uy * hw + ny * hd_, sill),
        App.Vector(cx - ux * hw + nx * hd_, cy - uy * hw + ny * hd_, sill),
    ]
    cutter = Part.Face(Part.makePolygon(corners + [corners[0]])).extrude(App.Vector(0, 0, oh))

    leaf = None
    if subtype in ("door", "window"):
        lh = 40.0 / 2.0  # 판 두께/2
        lc = [
            App.Vector(cx - ux * hw - nx * lh, cy - uy * hw - ny * lh, sill),
            App.Vector(cx + ux * hw - nx * lh, cy + uy * hw - ny * lh, sill),
            App.Vector(cx + ux * hw + nx * lh, cy + uy * hw + ny * lh, sill),
            App.Vector(cx - ux * hw + nx * lh, cy - uy * hw + ny * lh, sill),
        ]
        leaf = Part.Face(Part.makePolygon(lc + [lc[0]])).extrude(App.Vector(0, 0, oh))
    return cutter, leaf, subtype


def _add_leaf_feature(doc, leaf_shape, subtype, tag, return_object=False):
    """창틀/문짝 Part::Feature 추가 + IfcType 태깅. 라벨 반환."""
    feat = doc.addObject("Part::Feature",
                         f"{'Door' if subtype == 'door' else 'Window'}_{tag}")
    feat.Shape = leaf_shape
    try:
        arch_obj = Arch.makeEquipment(feat) if hasattr(Arch, "makeEquipment") else feat
    except Exception:
        arch_obj = feat
    target = arch_obj if arch_obj is not None else feat
    try:
        target.Label = f"{'Door' if subtype == 'door' else 'Window'}_{tag}"
        if hasattr(target, "IfcType"):
            target.IfcType = "Door" if subtype == "door" else "Window"
    except Exception:
        pass
    return target if return_object else target.Label


def cut_window_into_wall(doc, wall_obj, op, params, tag="live", add_leaf=True):
    """라이브 단건 삽입: 한 벽 객체에 opening void + (옵션)창틀/문짝 패널.
    freecad_live_addon.cmd_add_window_to_wall 이 호출. _opening_solids 로 build_openings 와 기하 공유.
    ★ 라이브 영속성: Arch Wall 의 .Shape 직접 덮어쓰기는 다음 recompute 에 사라지므로,
      파라메트릭 Part::Cut(Base=벽, Tool=cutter) 로 만들어 recompute 후에도 유지되게 한다.
    returns {"void": bool, "leaf": label|None, "cut_obj": label|None}."""
    cutter_shape, leaf, subtype = _opening_solids(op, params)
    out = {"void": False, "leaf": None, "cut_obj": None}
    try:
        cutter_feat = doc.addObject("Part::Feature", f"WinCutter_{tag}")
        cutter_feat.Shape = cutter_shape
        if hasattr(cutter_feat, "ViewObject") and cutter_feat.ViewObject:
            cutter_feat.ViewObject.Visibility = False
        cut = doc.addObject("Part::Cut", f"WallCut_{tag}")
        cut.Base = wall_obj
        cut.Tool = cutter_feat
        out["void"] = True
        out["cut_obj"] = cut.Label
    except Exception as e:
        print(f"[warn] cut_window_into_wall void(Part::Cut) 실패: {e}")
    if add_leaf and leaf is not None:
        try:
            out["leaf"] = _add_leaf_feature(doc, leaf, subtype, tag)
        except Exception as e:
            print(f"[warn] cut_window_into_wall leaf 실패: {e}")
    return out


def build_openings(doc, openings, wall_idx_map, params):
    """[Phase D] 문/창 3D. 사각형 void 로 벽 cut + 문짝/창틀 솔리드(IfcDoor/Window).
    - subtype='door'  : 바닥~height 개구, 얇은 문짝 판.
    - subtype='window': sill~sill+height 개구, 창틀+유리 판.
    - subtype 없음     : 사각 void 만(기존 동작 보강). radius 없을 때 원통 폴백.
    recompute 이후·saveAs 이전 호출(비파라메트릭 cut). returns (n_void, n_leaf).
    좌표·솔리드 계산은 _opening_solids 로 cut_window_into_wall(라이브 단건)과 공유."""
    n_void = n_leaf = 0
    applied = {}          # obj.Name â ì§ê¸ê¹ì§ ê·¸ ë²½ì ì ì©ë ì»¤í°ë¤ì í©
    for oi, op in enumerate(openings):
        result = {"eid": op["eid"], "requested_hosts": op.get("wall_indices", []),
                  "cut_host_eids": [], "failed_hosts": [], "cuts": [],
                  "already_void": [], "leaf_built": False}
        OPENING_RESULTS.append(result)
        try:
            cutter, leaf, subtype = _opening_solids(op, params)
        except Exception as exc:
            result["failed_hosts"].append(str(exc))
            continue
        seen = set()
        for wi in op.get("wall_indices", []):
            objects = wall_idx_map.get(wi, [])
            if not isinstance(objects, list):
                objects = [objects]
            if not objects:
                result["failed_hosts"].append({"wall_index": wi, "error": "host not built"})
            changed = False
            gaps = []
            for obj in objects:
                if obj.Name in seen:
                    changed = True
                    continue
                try:
                    before = obj.Shape.Volume
                    overlap = obj.Shape.common(cutter).Volume
                    if overlap <= 1e-6:
                        # ì¬ë£ê° ìë¤ë ê²ì ë ê°ì§ë¤: ì ë¿ìê±°ë, **ì´ë¯¸ ëë ¸ê±°ë.**
                        # ì¤ë¬´ ëë©´ì ê°ì ë¬¸ì ë ì´ì´ë§ë¤ ê·¸ë ¤ ê°êµ¬ë¶ê° ê²¹ì¹ë¤(ì¤ì¸¡: ë²½
                        # íëì í­ 868Â·874Â·874mm ê°êµ¬ë¶ê° 44mm ê°ê²©ì¼ë¡ 3ê°). ë¨¼ì  ì¨ ì»¤í°ê°
                        # ê·¸ ìë¦¬ë¥¼ ì´ë¯¸ ë¹ì ì¼ë©´ void ë **ì¡´ì¬íë¤** â ì¤í¨ë¡ ì¬ë¦¬ë©´
                        # V106 ì´ ë©ì¤¦í ê±´ë¬¼ì ë©í ë¶ê°ë¡ ë§ë ë¤.
                        prev = applied.get(obj.Name)
                        if prev is not None and prev.common(cutter).Volume > 1e-6:
                            result["already_void"].append(
                                {"host_name": obj.Label,
                                 "host_eids": AV.source_eids(BUILT_RECORDS[obj.Name])})
                            result["cut_host_eids"].extend(
                                AV.source_eids(BUILT_RECORDS[obj.Name]))
                            seen.add(obj.Name)
                            changed = True
                            continue
                        hb, cb = obj.Shape.BoundBox, cutter.BoundBox
                        # ì ì ë¿ìëì§ë¥¼ ì«ìë¡ ë¨ê¸´ë¤. gap ì´ ììì¸ ì¶ì´ ë¨ì´ì§ ì¶ì´ê³ ,
                        # ì ë¤ ììì¸ë° dist>0 ì´ë©´ bbox ë§ ê²ì¹ë ê¸°ì¸ì´ì§ ë²½ì´ë¤.
                        gaps.append({"host": obj.Label,
                            "dist_mm": round(obj.Shape.distToShape(cutter)[0], 2),
                            "gap_mm": [
                            round(max(cb.XMin - hb.XMax, hb.XMin - cb.XMax), 1),
                            round(max(cb.YMin - hb.YMax, hb.YMin - cb.YMax), 1),
                            round(max(cb.ZMin - hb.ZMax, hb.ZMin - cb.ZMax), 1)]})
                        continue
                    cut = obj.Shape.cut(cutter)
                    if cut.isNull() or not cut.isValid() or before - cut.Volume <= 1e-6:
                        raise ValueError("cut invalid or did not remove host volume")
                    # Arch exporters regenerate wall solids from the baseline. A
                    # Shape assignment alone loses the opening during IFC export.
                    # Persist the cutter in Arch's subtraction graph instead.
                    void = doc.addObject("Part::Feature", f"OpeningVoid_{oi}_{wi}")
                    void.Shape = cutter
                    obj.Subtractions = list(obj.Subtractions) + [void]
                    doc.recompute()
                    void.Visibility = False
                    cut = obj.Shape
                    if cut.isNull() or not cut.isValid() or before - cut.Volume <= 1e-6:
                        raise ValueError("Arch subtraction failed to remove host volume")
                    applied[obj.Name] = (cutter if obj.Name not in applied
                                         else applied[obj.Name].fuse(cutter))
                    seen.add(obj.Name)
                    changed = True
                    n_void += 1
                    host_eids = AV.source_eids(BUILT_RECORDS[obj.Name])
                    result["cut_host_eids"].extend(host_eids)
                    result["cuts"].append({"host_name": obj.Label, "host_eids": host_eids,
                        "before_volume_mm3": before, "after_volume_mm3": cut.Volume,
                        "removed_volume_mm3": before-cut.Volume})
                except Exception as exc:
                    result["failed_hosts"].append({"wall_index": wi, "error": str(exc)})
            if objects and not changed:
                # 왜 안 닿았는지를 축별 간격으로 남긴다 — 양수인 축이 떨어진 축이고,
                # 셋 다 음수면 bbox 는 겁치는데 솔리드가 안 닿은 것이다(벽 배향/두께).
                result["failed_hosts"].append({"wall_index": wi,
                    "error": "cutter did not intersect host", "gaps": gaps})
        result["cut_host_eids"] = sorted(set(result["cut_host_eids"]))
        if leaf is not None:
            try:
                leaf_obj = _add_leaf_feature(doc, leaf, subtype, oi, return_object=True)
                # Changing an Equipment's IfcType installs expressions before its
                # Shape exists; FreeCAD 1.1 retains -inf IFC dimensions. Bind the
                # known opening dimensions explicitly, in the native mm units.
                for key, value in (("OverallHeight", float(op.get("height") or (2100 if subtype == "door" else 1200))),
                                   ("OverallWidth", float(op.get("width") or float(op.get("radius", 450))*2))):
                    if hasattr(leaf_obj, key):
                        leaf_obj.setExpression(key, None)
                        setattr(leaf_obj, key, value)
                set_ifc_props(leaf_obj, op)
                OPENING_LEAVES.append((leaf_obj, op))
                result["leaf_built"] = True
                n_leaf += 1
            except Exception as exc:
                result["failed_hosts"].append({"error": "leaf: " + str(exc)})
    return n_void, n_leaf


def build_columns(doc, columns, params):
    objs = []
    src_els = []
    for i, el in enumerate(columns):
        z0, z1 = GC.z_range("column", el, params)
        height = z1 - z0
        if el["kind"] == "circle":
            base = Draft.makeCircle(el["radius"], placement=App.Placement(
                vec(el["center"]), App.Rotation()))
        elif el["kind"] == "polyline" and el.get("closed"):
            base = make_wire(GC.ccw(el["points"]), True)
        else:
            # 조용히 넘기지 않는다 — 벽에서 이렇게 72개가 사라졌다.
            UNBUILT.setdefault("column", []).append(
                {"i": i, "eid": el.get("eid"), "layer": el.get("layer"),
                 "why": f"kind={el.get('kind')} closed={el.get('closed')}"})
            continue
        base.Label = f"ColBase_{i}"
        col = Arch.makeStructure(base, height=height)
        col.IfcType = "Column"
        col.Label = f"Column_{i}"
        col.Normal = App.Vector(0, 0, 1)  # 감김 무관하게 +Z 압출(슬래브는 GC.ccw 로 동일 효과)
        col.Placement.Base.z = z0
        
        # [라운드트립 기반] DXF Handle 주입
        col.addProperty("App::PropertyString", "DxfId", "Metadata", "Original DXF Handle")
        col.DxfId = el.get("handle") or f"COLUMN_{i}"
        set_ifc_props(col, el)
        
        objs.append(col)
        src_els.append(el)
    return objs, src_els


def build_slabs(doc, slabs, params):
    objs = []
    src_els = []
    for i, el in enumerate(slabs):
        if el["kind"] != "polyline" or not el.get("closed"):
            UNBUILT.setdefault("slab", []).append(
                {"i": i, "eid": el.get("eid"), "layer": el.get("layer"),
                 "why": f"kind={el.get('kind')} closed={el.get('closed')}"})
            continue
        cat = "beam" if (el.get("overrides", {}).get("ifc_type") == "Beam") else "slab"
        z0, z1 = GC.z_range(cat, el, params)
        thk = z1 - z0
        # [실측·확정] Arch.makeStructure 는 닫힌 와이어를 면으로 만들어 '면 법선' 방향으로 압출한다.
        # 법선은 폴리곤 감김 방향(winding)이 결정 → CW 면 -Z 로 압출되어 결과가 두께만큼 더 내려간다
        # (356개 중 CW 352개가 전부 z_base-thk 로 밀림, CCW 4개만 정상 — 상관계수 1.0 으로 확인).
        # 입력 감김에 좌우되지 않도록 항상 CCW(법선 +Z)로 정규화한다.
        base = make_wire(GC.ccw(el["points"]), True)
        base.Label = f"SlabBase_{i}"
        # 슬래브는 바닥(-thk) 방향으로 두께. 여기선 +Z 로 두고 배치만 내림.
        slab = Arch.makeStructure(base, height=thk)
        # [실측] 가늘고 긴(보 형태) 폴리곤에 IfcType="Slab" 강제 시 FreeCAD IFC exporter가
        # 조용히(에러 없이) 해당 오브젝트를 통째로 누락시킴(실측: 30/30 사례 재현·확정).
        # overrides.ifc_type 로 명시적 지정 가능(기본값은 기존 그대로 "Slab" — 하위호환).
        slab.IfcType = el.get("overrides", {}).get("ifc_type", "Slab")
        slab.Label = f"Slab_{i}"
        slab.Placement.Base.z = z0  # 기준면 해석은 geom_contract 가 단독 담당
        
        # [라운드트립 기반] DXF Handle 주입
        slab.addProperty("App::PropertyString", "DxfId", "Metadata", "Original DXF Handle")
        slab.DxfId = el.get("handle") or f"SLAB_{i}"
        set_ifc_props(slab, el)
        
        objs.append(slab)
        src_els.append(el)
    return objs, src_els


def build_beams(doc, beams, params):
    """[D3b] elements["beam"] → Arch Structure(IfcType="Beam").

    종전에 elements["beam"] 을 읽는 코드가 **아예 없었다** — layer_map 에
    category=beam 을 쓰면 레코드가 빌드 단계에서 조용히 사라졌다. 그래서 보를
    `slab + overrides.ifc_type=Beam` 으로 우회해 왔는데, 그건 build_slabs:537 의
    경고("얇고 긴 폴리곤에 IfcType=Slab 이면 IFC exporter 가 조용히 누락")가
    가리키는 지뢰 바로 옆이다. 여기서 정식 경로를 만든다.

    두 형태를 모두 받는다:
      · 열린 폴리라인(축선) — `from=dim` + 일람표 조인 산출물. 구간마다 폭 b 의
        사각 footprint 를 만들어 춤 h 만큼 압출한다. 축+단면이지 가짜 닫힌
        폴리곤이 아니므로 감김 문제가 원천적으로 없다.
      · 닫힌 폴리라인(외곽선) — footprint 를 그대로 쓴다(슬래브와 같은 경로).

    z 규약은 slab 과 같은 'top': z_base 가 보 **상단**이고 아래로 춤만큼 내려간다
    (실무 규약: 콘크리트 보 상단 = 슬래브 상단, 춤은 슬래브 두께를 포함).
    """
    objs, src_els = [], []
    n_nosec = 0
    for i, el in enumerate(beams):
        created_before = len(objs)
        if el.get("kind") != "polyline":
            continue
        z0, z1 = GC.z_range("beam", el, params)
        depth = z1 - z0
        if depth <= 0:
            print(f"  [warn] Beam_{i} 춤이 0 이하({depth}) — 건너뜀")
            continue
        # 일람표 미매칭 등으로 단면을 모르는 보는 기본단면으로 세우되 **세어서 보고**한다.
        # 안 세우면 부재가 통째로 사라지고, 조용히 세우면 틀린 치수가 납품된다.
        if el.get("needs_review") or el.get("schedule_match") in ("unmatched", "size_unparsed"):
            n_nosec += 1

        # 축선→footprint 규칙은 geom_contract 가 단독 소유(preview 도 같은 식을 주입받는다)
        rings = GC.beam_rings(el, params)
        for j, ring in enumerate(rings):
            base = make_wire(GC.ccw(ring), True)
            if base is None:
                continue
            base.Label = f"BeamBase_{i}_{j}"
            try:
                bm = Arch.makeStructure(base, height=depth)
            except Exception as _be:
                print(f"  [warn] Beam_{i}_{j} 생성 실패: {_be}")
                continue
            bm.IfcType = "Beam"
            bm.Normal = App.Vector(0, 0, 1)   # 감김 무관하게 +Z 압출
            bm.Placement.Base.z = z0
            nm = el.get("member_name")
            bm.Label = f"Beam_{i}_{j}" + (f"_{nm}" if nm else "")
            bm.addProperty("App::PropertyString", "DxfId", "Metadata", "Original DXF Handle")
            bm.DxfId = el.get("handle") or f"BEAM_{i}_{j}"
            set_ifc_props(bm, el)
            if nm:
                bm.addProperty("App::PropertyString", "MemberName", "Metadata", "부재명(일람표)")
                bm.MemberName = str(nm)
            objs.append(bm)
            src_els.append(el)
        if len(objs) - created_before != len(rings) or not rings:
            UNBUILT.setdefault("beam", []).append({"eid": el.get("eid"),
                "why": "One or more beam segments failed to build"})
    if n_nosec:
        print(f"  [!] 단면 미상 보 {n_nosec}개 — 기본단면으로 세움(일람표 매칭 필요)")
    return objs, src_els, n_nosec


def build_spaces(doc, zones, params):
    """zone 닫힌 폴리라인 → Arch.makeSpace 방 객체. IFC Space 태깅."""
    objs = []
    src_els = []
    for i, el in enumerate(zones):
        if el["kind"] != "polyline" or not el.get("closed"):
            continue
        pts = el["points"]
        if len(pts) < 3:
            continue
        try:
            # zone 은 벽 높이를 기본값으로 쓴다(방 높이). 규약은 geom_contract 단독.
            z_b, _z1 = GC.z_range("zone", el, params)
            room_h = _z1 - z_b
            if room_h <= 0:
                room_h = GC.height_of(el, params, "wall")
            pts_3d = [App.Vector(float(p[0]), float(p[1]), z_b) for p in pts]
            pts_3d.append(pts_3d[0])  # 닫기
            wire = Part.makePolygon(pts_3d)
            face = Part.Face(wire)
            solid = face.extrude(App.Vector(0, 0, room_h))
            feat = doc.addObject("Part::Feature", f"SpaceShape_{i}")
            feat.Shape = solid
            feat.Label = f"SpaceShape_{i}"
            space = Arch.makeSpace([feat])
            space.Label = f"Space_{i}"
            space.IfcType = "Space"
            set_ifc_props(space, el)
            objs.append(space)
            src_els.append(el)
        except Exception as e:
            print(f"[warn] Space_{i} 생성 실패: {e}")
    return objs, src_els


# ── [Phase 5a] MEP 3D 솔리드 ────────────────────────────────
def _pipe_solid(pts, radius, elev):
    """다점 중심선 → 원기둥 세그먼트 fuse. 모두 z=elev."""
    shapes = []
    for k in range(len(pts) - 1):
        p1 = App.Vector(float(pts[k][0]),   float(pts[k][1]),   elev)
        p2 = App.Vector(float(pts[k+1][0]), float(pts[k+1][1]), elev)
        seg = p2 - p1
        ln = seg.Length
        if ln < 1.0:
            continue
        cyl = Part.makeCylinder(radius, ln, p1, seg.normalize())
        shapes.append(cyl)
    if not shapes:
        return None
    result = shapes[0]
    for s in shapes[1:]:
        result = result.fuse(s)
    return result


def _rect_solid(pts, width, height, elev):
    """다점 중심선 → 사각단면(width×height) 세그먼트 fuse. duct/tray 용."""
    w2, h2 = width / 2.0, height / 2.0
    # 단면: Z축 방향 정렬 기준 사각형(XY 평면), 이후 각 세그먼트 방향으로 회전
    rect_pts = [App.Vector(-w2, -h2, 0), App.Vector(w2, -h2, 0),
                App.Vector(w2,  h2, 0), App.Vector(-w2,  h2, 0),
                App.Vector(-w2, -h2, 0)]
    rect_wire = Part.makePolygon(rect_pts)
    rect_face = Part.Face(rect_wire)
    shapes = []
    for k in range(len(pts) - 1):
        p1 = App.Vector(float(pts[k][0]),   float(pts[k][1]),   elev)
        p2 = App.Vector(float(pts[k+1][0]), float(pts[k+1][1]), elev)
        seg = p2 - p1
        ln = seg.Length
        if ln < 1.0:
            continue
        # Z→seg 방향 회전
        try:
            rot = App.Rotation(App.Vector(0, 0, 1), seg.normalize())
        except Exception:
            rot = App.Rotation()
        mat = App.Placement(p1, rot).Matrix
        face_rot = rect_face.transformed(mat)
        solid = face_rot.extrude(seg)
        shapes.append(solid)
    if not shapes:
        return None
    result = shapes[0]
    for s in shapes[1:]:
        result = result.fuse(s)
    return result


def _equip_solid(pts, elev, default_h=1000.0):
    """장비: 닫힌 폴리라인 footprint → extrude 솔리드."""
    pts_3d = [App.Vector(float(p[0]), float(p[1]), elev) for p in pts]
    pts_3d.append(pts_3d[0])
    wire = Part.makePolygon(pts_3d)
    face = Part.Face(wire)
    return face.extrude(App.Vector(0, 0, default_h))


# MEP 카테고리 → FreeCAD IfcType 문자열(ArchIFC.IfcTypes 의 표기 그대로).
# 이게 없으면 exporter 가 전부 IfcBuildingElementProxy 로 내보낸다 — 실측:
# 지하3층 IFC 의 MEP 6개가 전부 Proxy 였고, 뷰어는 그게 배관인지 알 수 없었다.
_MEP_IFC_TYPE = {
    "pipe":      "Pipe Segment",            # → IfcPipeSegment
    "duct":      "Duct Segment",            # → IfcDuctSegment
    "tray":      "Cable Carrier Segment",   # → IfcCableCarrierSegment
    # 도면의 블록만 보고 펌프인지 냉동기인지 알 수 없다. 일반 배급요소가 정직하다.
    "equipment": "Distribution Element",    # → IfcDistributionElement
}


def build_mep(doc, mep_elements):
    """MEP 중심선 → Arch 컴포넌트. **IFC MEP 타입을 달아서** 내보낸다.

    종전에는 Part::Feature 로만 만들어 IFC 에서 전부 IfcBuildingElementProxy 가 됐다.
    형상은 맞지만 뷰어(Bonsai/Navisworks)가 배관인지 덕트인지 모르니 시스템 필터도,
    카테고리별 물량도, 의미 있는 간섭 리포트도 안 나온다. MEP 가 이 프로젝트의
    차별점인데 정작 IFC 에서 정체불명이었다.

    배관은 `Arch.makePipe` 가 축선을 따라 원형 단면을 스윕한다 — 다점 폴리라인을
    한 객체로 처리하고 코너도 마이터로 잇는다(실측: 직선합 대비 체적 오차 0.07%).
    직접 원통을 fuse 하던 것보다 형상도 낫고 IfcType 도 공짜다.
    덕트/트레이/장비는 Arch 전용 생성자가 없으므로 기존 솔리드를 Arch 컴포넌트로
    감싸고 IfcType 만 지정한다.
    """
    objs, src = [], []
    for cat in ("pipe", "duct", "tray", "equipment"):
        for i, el in enumerate(mep_elements.get(cat, [])):
            elev = float(el.get("elevation", 0.0))
            pts = el.get("centerline") or el.get("points") or []
            if len(pts) < 2:
                continue
            label = f"{cat.capitalize()}_{i}"
            try:
                obj = None
                if cat == "pipe":
                    ax = make_wire([[p[0], p[1]] for p in pts], False, doc=doc,
                                   label=f"PipeAxis_{i}")
                    if ax is None:
                        continue
                    ax.Placement.Base.z = elev
                    obj = Arch.makePipe(ax, diameter=float(el.get("diameter") or 100.0))
                else:
                    if cat in ("duct", "tray"):
                        w = float(el.get("width_mm") or 400.0)
                        h = float(el.get("height_mm")
                                  or (300.0 if cat == "duct" else 100.0))
                        shape = _rect_solid(pts, w, h, elev)
                    else:                                   # equipment
                        if not el.get("closed") or len(pts) < 3:
                            continue
                        shape = _equip_solid(pts, elev)
                    if shape is None or not shape.isValid():
                        print(f"[warn] MEP {label} 형상 오류")
                        continue
                    feat = doc.addObject("Part::Feature", f"MepShape_{cat}_{i}")
                    feat.Shape = shape
                    obj = Arch.makeComponent(feat)
                if obj is None:
                    continue
                obj.IfcType = _MEP_IFC_TYPE[cat]
                obj.Label = label
                set_ifc_props(obj, el)
                objs.append(obj)
                src.append(el)
            except Exception as e:
                print(f"[warn] MEP {label}: {e}")
    return objs


# ── [Phase 5b] 구조 vs MEP 간섭(clash) 검사 ─────────────────
def check_clashes(struct_objs, mep_objs, vol_tol=1.0):
    """구조·MEP 솔리드 페어별 clash 목록 반환.
    AABB Broad-phase 먼저 → 겹침 후보만 shape.common() 수행 → O(S×M) 최악에서
    실제로는 O(겹침 후보)로 대폭 축소."""
    clashes = []
    if not mep_objs:
        return clashes

    # Broad-phase: BoundBox 교차 여부 확인 (cheap)
    def _bb(obj):
        try:
            s = getattr(obj, "Shape", None)
            if s is None or not s.isValid():
                return None
            bb = s.BoundBox
            # isValid() 없는 FreeCAD 버전 대비 hasattr 확인
            if hasattr(bb, "isValid") and not bb.isValid():
                return None
            if bb.XLength <= 0 and bb.YLength <= 0 and bb.ZLength <= 0:
                return None  # 퇴화 bounding box
            return bb
        except Exception:
            return None

    for so in struct_objs:
        sbb = _bb(so)
        if sbb is None:
            continue
        for mo in mep_objs:
            mbb = _bb(mo)
            if mbb is None:
                continue
            # AABB 겹침 확인 (Intersect)
            try:
                if not sbb.intersected(mbb):
                    continue
            except Exception:
                continue
            try:
                s_shape = so.Shape
                m_shape = mo.Shape
                common = s_shape.common(m_shape)
                if common.Volume > vol_tol:
                    clashes.append({"struct": so.Label, "mep": mo.Label,
                                    "volume_mm3": round(common.Volume, 1)})
            except Exception:
                pass
    return clashes


def main():
    import traceback
    import io
    class Tee:
        def __init__(self, target):
            self.target, self.buffer = target, io.StringIO()
        def write(self, value):
            self.buffer.write(value)
            return self.target.write(value)
        def flush(self):
            self.target.flush()
    oldout, olderr = sys.stdout, sys.stderr
    out, err = Tee(oldout), Tee(olderr)
    sys.stdout, sys.stderr = out, err
    code, failure = 0, None
    try:
        _main_impl()
    except BaseException as exc:
        code = int(exc.code or 0) if isinstance(exc, SystemExit) else 1
        if code:
            failure = str(exc)
            traceback.print_exc()
    finally:
        sys.stdout, sys.stderr = oldout, olderr
        base = os.environ.get("MEP_OUT") or (sys.argv[2] if len(sys.argv) > 2 else "out_model")
        path = os.path.abspath(base + ".build.json")
        stats = {}
        try:
            with open(path, encoding="utf-8") as stream:
                previous = json.load(stream)
            if previous.get("provenance", {}).get("run_id") == PROVENANCE.get("run_id") and PROVENANCE:
                stats = previous
        except (OSError, ValueError):
            pass
        stats.setdefault("provenance", dict(PROVENANCE))
        stats.setdefault("artifacts", {k: {"status": "failed", "path": None} for k in ("fcstd", "ifc")})
        stats["runtime"] = {"exit_code": code, "stdout": out.buffer.getvalue(), "stderr": err.buffer.getvalue()}
        if failure:
            stats.setdefault("runtime_errors", []).append(failure)
            stats["status"] = "failed"
        _write_json(path, stats)
        if code:
            print(f"BUILD_FAILED:{path}", flush=True)
    if code:
        raise SystemExit(code)


def _main_impl():
    geom_path = os.environ.get("MEP_GEOMETRY") or (sys.argv[1] if len(sys.argv) > 1 else None)
    out_base  = os.environ.get("MEP_OUT")      or (sys.argv[2] if len(sys.argv) > 2 else "out_model")
    if not geom_path:
        print("usage: MEP_GEOMETRY=geometry.json MEP_OUT=out freecadcmd freecad_builder.py")
        sys.exit(1)

    print(f"[1/8] JSON 로드: {geom_path}")
    with open(geom_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    global PROVENANCE
    PROVENANCE = AV.provenance(data)
    if os.environ.get("MEP_BUILD_RUN_ID"):
        PROVENANCE["run_id"] = os.environ["MEP_BUILD_RUN_ID"]
    PROVENANCE.update({"builder_path": os.path.abspath(__file__), "builder_sha256": AV.file_hash(__file__)})
    original_data = copy.deepcopy(data)
    UNBUILT.clear()
    BUILT_RECORDS.clear()
    OPENING_RESULTS.clear()
    OPENING_LEAVES.clear()
    import verify as V
    preflight = V.verify_geometry(data)
    allow_errors = os.environ.get("MEP_ALLOW_ERRORS", "").strip().lower() not in ("", "0", "false")
    if preflight.failed and not allow_errors:
        _write_json(os.path.abspath(out_base + ".build.json"), {
            "schema_version": 2, "provenance": PROVENANCE, "preflight": preflight.to_dict(),
            "verify": preflight.to_dict(), "status": "failed", "artifacts": {
                k: {"status": "failed", "path": None, "provenance": PROVENANCE} for k in ("fcstd", "ifc")}})
        raise ValueError("Geometry preflight failed: " + preflight.text())
    AV.prepare_records(data)
    params = data.get("params", {})
    el = data["elements"]
    print(f"  walls={len(el.get('wall',[]))} cols={len(el.get('column',[]))}"
          f" slabs={len(el.get('slab',[]))} openings={len(el.get('opening',[]))}")

    import time as _time

    print("[2/8] 문서 생성")
    doc = App.newDocument("BIM")
    # Part::Feature.Shape = wire_shape 는 shape 직접 할당 → 개별 recompute 가 빠름.
    # setSkipRecompute 는 오히려 배치 recompute 비용 증가 → 사용 안 함.

    _t0 = _time.time()
    print("[3/8] 벽체 빌드")
    walls, wall_idx_map, wall_src = build_walls(doc, el.get("wall", []), params)
    print(f"  → {len(walls)}개 벽체 ({_time.time()-_t0:.1f}s)")

    print("[4/8] 기둥/슬래브/보/공간/MEP 빌드")
    _t1 = _time.time()
    cols,   col_src   = build_columns(doc, el.get("column", []), params)
    slabs,  slab_src  = build_slabs(doc, el.get("slab", []), params)
    beams, beam_src, n_nosec = build_beams(doc, el.get("beam", []), params)
    spaces, space_src = build_spaces(doc, el.get("zone", []), params)
    mep_objs          = build_mep(doc, el)
    print(f"  → cols={len(cols)} slabs={len(slabs)} beams={len(beams)}"
          f" spaces={len(spaces)} mep={len(mep_objs)} ({_time.time()-_t1:.1f}s)")
    for _c, _v in sorted(UNBUILT.items()):
        _n = {}
        for _e in _v:
            _n[_e["why"]] = _n.get(_e["why"], 0) + 1
        print(f"  [!] {_c} 레코드 {len(_v)}개가 객체를 못 만들었다 — IFC 에 없다: {_n}")
        print(f"      EID: {[e['eid'] for e in _v[:6]]}")

    print("[5/8] recompute")
    _t2 = _time.time()
    try:
        doc.recompute()
        print(f"  → {len(doc.Objects)}개 객체 ({_time.time()-_t2:.1f}s)")
    except Exception as _re:
        print(f"  [warn] recompute 오류: {_re}")

    print("[6/8] 층 컨테이너 생성")
    _FLOOR_TOL  = 100.0
    floors_info = data.get("floors") or [{"z": 0.0, "label": "Level_1"}]
    _fz_list = [float(f.get("z", 0.0)) for f in floors_info]

    # ★ 어느 Floor 에도 못 들어간 객체는 Arch.makeBuilding 트리 밖에 남아
    #   export([building]) 에서 조용히 빠진다(보 135개가 IfcBeam:0 이 된 원인).
    #   두 Floor 에 동시 매칭되면 중복 삽입된다. 둘 다 세어서 게이트로 넘긴다.
    _hits = {}          # id(obj) -> 매칭된 층 인덱스 목록
    _meta = {}          # id(obj) -> (라벨, z_base)

    def _at_floor(obj_list, src_list, fz, fi):
        out = []
        for obj, el_r in zip(obj_list, src_list):
            zb = float(el_r.get("z_base", el_r.get("elevation", 0.0)) or 0.0)
            _meta.setdefault(id(obj), (getattr(obj, "Label", "?"), zb))
            _hits.setdefault(id(obj), [])
            floor_match = not el_r.get("level") or el_r["level"] in (
                floors_info[fi].get("id"), floors_info[fi].get("label"), floors_info[fi].get("storey"))
            if abs(zb - fz) < _FLOOR_TOL and floor_match:
                _hits[id(obj)].append(fi)
                out.append(obj)
        return out

    def _in_story(obj_list, src_list, fi):
        """MEP 전용 배정. 배관 elevation(예: 2600)이 층 z 와 '일치' 할 리 없다 —
        층 안에서 도는 설비이므로 elevation 을 포함하는 층(가장 큰 z <= elev)에 넣는다.
        정확 매칭을 요구하면 MEP 는 영원히 고아가 되어 IFC 에서 빠진다."""
        out = []
        for obj, el_r in zip(obj_list, src_list):
            elev = float(el_r.get("elevation", 0.0) or 0.0)
            _meta.setdefault(id(obj), (getattr(obj, "Label", "?"), elev))
            _hits.setdefault(id(obj), [])
            below = [k for k, z in enumerate(_fz_list) if z <= elev + _FLOOR_TOL]
            if el_r.get("level"):
                named = [k for k, floor in enumerate(floors_info) if el_r["level"] in
                         (floor.get("id"), floor.get("label"), floor.get("storey"))]
                owner = named[0] if len(named) == 1 else None
            else:
                owner = max(below, key=lambda k: _fz_list[k]) if below else \
                    min(range(len(_fz_list)), key=lambda k: abs(_fz_list[k] - elev))
            if owner == fi:
                _hits[id(obj)].append(fi)
                out.append(obj)
        return out

    # MEP 도 그룹핑 대상에 넣는다 — 지금까지 src_els 가 없어 구조적으로 제외돼
    # IFC 에서 항상 누락됐다. elevation 을 z_base 자리에 넣어 동일하게 다룬다.
    mep_src = [BUILT_RECORDS[o.Name] for o in mep_objs]

    floor_containers = []
    for fi, finfo in enumerate(floors_info):
        fz    = float(finfo.get("z", 0.0))
        flbl  = finfo.get("label", f"Level_{fi+1}")
        fw  = _at_floor(walls,   wall_src,  fz, fi)
        fc  = _at_floor(cols,    col_src,   fz, fi)
        fs  = _at_floor(slabs,   slab_src,  fz, fi)
        fb  = _at_floor(beams,   beam_src,  fz, fi)
        fsp = _at_floor(spaces,  space_src, fz, fi)
        fm  = _in_story(mep_objs, mep_src, fi)
        try:
            fl = Arch.makeFloor(fw + fc + fs + fb + fsp + fm)
            fl.Label = flbl
            fl.Placement.Base.z = fz
            floor_containers.append(fl)
            print(f"  {flbl}: walls={len(fw)} cols={len(fc)} slabs={len(fs)}"
                  + (f" beams={len(fb)}" if fb else "")
                  + (f" mep={len(fm)}" if fm else ""))
        except Exception as _fe:
            print(f"  [warn] makeFloor 실패({flbl}): {_fe}")

    _orphans = [_meta[k] for k, v in _hits.items() if not v]
    _dups    = [_meta[k] for k, v in _hits.items() if len(v) > 1]
    if _orphans:
        print(f"  [!] 어느 층에도 속하지 않은 객체 {len(_orphans)}개 — IFC 에서 누락된다")
        print(f"      floors z = {[round(z) for z in _fz_list]}")
        for lbl, zb in _orphans[:5]:
            print(f"      {lbl}  z_base={zb:.0f}")
    if _dups:
        print(f"  [!] 두 층에 중복 삽입된 객체 {len(_dups)}개")
    try:
        building = Arch.makeBuilding(floor_containers)
        building.Label = "Building"
        doc.recompute()
    except Exception as _be:
        print(f"  [warn] makeBuilding/recompute: {_be}")

    # recompute 직후·개구부 cut 직전. 빈 형상은 cut 도 못 하므로 순서가 중요하다.
    _n_fixed = repair_null_walls(walls)

    print("[7/8] 문/창 3D (사각형 void + 문짝/창틀) + clash 검사")
    n_voids, n_leaf = build_openings(doc, el.get("opening", []), wall_idx_map, params)
    if OPENING_LEAVES:
        leaf_objs = [obj for obj, rec in OPENING_LEAVES]
        leaf_src = [dict(rec, elevation=GC.base_z("opening", rec)) for obj, rec in OPENING_LEAVES]
        for fi, floor in enumerate(floor_containers):
            floor.Group = list(floor.Group) + _in_story(leaf_objs, leaf_src, fi)
        doc.recompute()
        _orphans = [_meta[k] for k, v in _hits.items() if not v]
        _dups = [_meta[k] for k, v in _hits.items() if len(v) > 1]
    print(f"  개구부 void={n_voids}개, 문짝/창틀={n_leaf}개")
    struct_objs = walls + cols + slabs + beams
    clashes = check_clashes(struct_objs, mep_objs)
    if clashes:
        print(f"  [CLASH] 간섭 {len(clashes)}건")
    else:
        print("  [CLASH] 간섭 없음")

    fcstd = f"{out_base}.FCStd"
    ifc   = f"{out_base}.ifc"

    # ── 보조 형상 숨김 ────────────────────────────────────────────────────────
    # Arch 객체의 Base(벽 축선·슬래브 윤곽)는 GUI 워크벤치에서 자동으로 숨겨지지만
    # freecadcmd(headless)에는 ViewObject 가 없어 Visibility=true 로 저장된다.
    # → GUI 로 열면 Z=0 평면에 축선 수백 개가 함께 보임. App 레벨 Visibility 로 숨긴다.
    _n_hidden = 0
    for _o in doc.Objects:
        _lbl = getattr(_o, "Label", "")
        if _lbl.startswith(("WallAxis", "SlabBase", "ColBase", "BeamBase",
                            "PipeAxis", "MepShape",
                            "SpaceShape", "_wall_")):
            try:
                _o.Visibility = False
                _n_hidden += 1
            except Exception:
                pass
    if _n_hidden:
        print(f"  보조 형상 {_n_hidden}개 숨김(축선/베이스)")

    # ── 게이트 준비: 형상검증은 저장 '전' 에 한다 ────────────────────────────
    # 종전에는 saveAs/IFC export 이후에 세고 경고만 했다 — 깨진 형상이 이미
    # 디스크에 쓰인 뒤였고 아무도 그 경고에 반응하지 않았다.
    try:
        _bad = [o for o in doc.Objects if _shape_ok(o) is False or
                (o.Name in BUILT_RECORDS and _shape_ok(o) is not True)]
        n_err = len(_bad)
        # 개수만으로는 못 쫓는다 — 어느 객체가 왜 깨졌는지 이름과 함께 말한다.
        for _o in _bad[:10]:
            _s = getattr(_o, "Shape", None)
            _bl = getattr(getattr(_o, "Base", None), "Shape", None)
            print(f"  [!] 형상 실패 {getattr(_o, 'Label', '?')} "
                  f"(IfcType={getattr(_o, 'IfcType', '?')}, "
                  f"null={None if _s is None else _s.isNull()}, "
                  f"Width={getattr(getattr(_o, 'Width', None), 'Value', None)}, "
                  f"base_len={None if _bl is None else round(_bl.Length, 1)})")
    except Exception as _ee:
        print(f"  [warn] 형상 검사 실패: {_ee}")
        raise RuntimeError("Shape verification failed") from _ee

    _bbox = None
    try:
        import FreeCAD as _A
        _bb = None
        for _o in doc.Objects:
            _s = getattr(_o, "Shape", None)
            if _s is None or _s.isNull():
                continue
            b = _s.BoundBox
            if b.XLength > 1e9:
                continue
            _bb = b if _bb is None else _bb.united(b)
        if _bb is not None:
            _bbox = [_bb.XMin, _bb.YMin, _bb.ZMin, _bb.XMax, _bb.YMax, _bb.ZMax]
    except Exception:
        pass

    # intent 는 원본 카테고리 수가 아니라 **실제 부여된 IfcType** 으로 센다.
    # 닫힌 폴리선 벽은 Arch.makeStructure 라 IfcWall 이 아니고, 보는 slab 버킷에
    # 있지만 IfcType=Beam 이다. 원본 수로 세면 정상 빌드가 불일치로 걸린다.
    # ★ MEP 도 센다. 종전엔 구조 4종만 세서, IFC 에 IfcDuctSegment 45개가 들어 있는데
    #   영수증에는 {'wall': 88} 만 찍혔다 — 덕트가 조용히 사라져도 build.json 이
    #   말해 주지 않는 상태였다(보가 그렇게 사라진 적이 있다: D3b 참조).
    _by_ifctype = {}
    for _o in (walls + cols + slabs + beams + mep_objs):
        _t = str(getattr(_o, "IfcType", "") or "").strip().lower().replace(" ", "")
        if _t:
            _by_ifctype[_t] = _by_ifctype.get(_t, 0) + 1
    built_eids = {eid for rec in BUILT_RECORDS.values() for eid in AV.source_eids(rec)}
    for cat, i, rec in AV.records(data):
        if cat == "opening":
            continue
        if rec["eid"] not in built_eids and not any(r.get("eid") == rec["eid"] for r in UNBUILT.get(cat, [])):
            UNBUILT.setdefault(cat, []).append({"i": i, "eid": rec["eid"], "why": "No product built"})
    build_stats = {
        "schema_version": 2, "provenance": dict(PROVENANCE), "preflight": preflight.to_dict(),
        "expected_products": [{"name": doc.getObject(name).Label, "source_eids": AV.source_eids(rec),
                               "volume_mm3": doc.getObject(name).Shape.Volume,
                               "bbox_mm": _world_bounds(doc.getObject(name)),
                               "qa": AV.qa_values(rec, PROVENANCE)}
                              for name, rec in BUILT_RECORDS.items()],
        "opening_results": list(OPENING_RESULTS),
        "intent": {"wall": _by_ifctype.get("wall", 0),
                   "column": _by_ifctype.get("column", 0),
                   "slab": _by_ifctype.get("slab", 0),
                   "beam": _by_ifctype.get("beam", 0)},
        "ifctype_counts": _by_ifctype,
        "built": {"walls": len(walls), "columns": len(cols), "slabs": len(slabs),
                  "beams": len(beams), "spaces": len(spaces), "mep": len(mep_objs),
                  "floors": len(floor_containers)},
        "beams_without_section": n_nosec,
        "materials": dict(MATERIALS_APPLIED),
        "null_walls_repaired": _n_fixed,
        # 카테고리별 '객체를 못 만든 레코드'. 전부 0 이어야 정상이다.
        "unbuilt": {c: {"count": len(v), "detail": v[:10]}
                    for c, v in sorted(UNBUILT.items())},
        "floor_orphans": len(_orphans), "floor_dups": len(_dups),
        "floor_orphan_detail": [{"label": l, "z_base": z} for l, z in _orphans[:20]],
        "invalid_shapes": n_err,
        "bbox": _bbox,
        "openings_void": n_voids, "opening_leaves": n_leaf,
        "clashes": [{"struct": c.get("struct"), "mep": c.get("mep"),
                     "volume_mm3": c.get("volume_mm3")} for c in (clashes or [])],
    }

    _stats_path = os.path.abspath(out_base + ".build.json")
    build_stats["artifacts"] = {key: {"status": "failed", "path": None, "provenance": dict(PROVENANCE)}
                                for key in ("fcstd", "ifc")}
    _rep = V.verify_build(original_data, build_stats, stage="pre_export")
    build_stats["verify"] = _rep.to_dict()
    if _rep.failed and not allow_errors:
        _write_json(_stats_path, build_stats)
        raise ValueError("Pre-export verification failed: " + _rep.text())
    diagnostic = _rep.failed
    # Writable unique directory, never the read-only installation/source directory.
    os.makedirs(os.path.dirname(os.path.abspath(out_base)), exist_ok=True)
    temp_parent = os.path.dirname(os.path.abspath(out_base))
    with tempfile.TemporaryDirectory(prefix="mep_build_", dir=temp_parent if temp_parent.isascii() else None) as tmpdir:
        tmp_fc = os.path.join(tmpdir, "model.FCStd")
        tmp_ifc = os.path.join(tmpdir, "model.ifc")
        try:
            doc.saveAs(tmp_fc)
            if not os.path.isfile(tmp_fc) or os.path.getsize(tmp_fc) == 0:
                raise ValueError("FCStd output missing or empty")
            # FCStd is a zip: verify the persisted document, not just saveAs success.
            import zipfile
            with zipfile.ZipFile(tmp_fc) as archive:
                if archive.testzip() or "Document.xml" not in archive.namelist():
                    raise ValueError("Invalid FCStd archive")
            validation_copy = os.path.join(tmpdir, "reopen.FCStd")
            shutil.copy2(tmp_fc, validation_copy)
            build_stats["fcstd_validation"] = _validate_saved_document(validation_copy, doc, build_stats["expected_products"])
            dst = os.path.abspath(out_base + (".diagnostic" if diagnostic else "") + ".FCStd")
            os.replace(tmp_fc, dst)
            status = "diagnostic_nonverified" if diagnostic else "verified"
            build_stats["artifacts"]["fcstd"] = AV.artifact_receipt(dst, PROVENANCE, status)
            if not diagnostic:
                print(f"FCSTD_DST:{dst}", flush=True)
        except Exception as exc:
            build_stats["artifacts"]["fcstd"]["error"] = str(exc)
        try:
            exporter = None
            for module_name in ("importers.exportIFC", "exportIFC", "importIFC"):
                try:
                    module = __import__(module_name, fromlist=["export"])
                    if hasattr(module, "export"):
                        exporter = module
                        break
                except ImportError:
                    continue
            if exporter is None:
                raise ImportError("IFC exporter is unavailable")
            # FreeCAD's fallback default is 1 metre after scaling; a 100 mm
            # pipe became a four-sided prism (36% volume loss). Set a local
            # 0.01 mm chord tolerance without changing the user's preferences.
            original_representation = getattr(exporter, "getRepresentation", None)
            original_attributes = getattr(exporter, "exportIfcAttributes", None)
            if original_attributes:
                def dimension_attributes(obj, kwargs, scale=0.001):
                    attributes = original_attributes(obj, kwargs, scale)
                    # 1.1's general attribute exporter only scales Elevation;
                    # door/window positive length attributes also need IFC units.
                    for key in ("OverallHeight", "OverallWidth"):
                        if key in attributes and hasattr(obj, key):
                            attributes[key] = float(getattr(obj, key).Value) * scale
                    return attributes
                exporter.exportIfcAttributes = dimension_attributes
            if original_representation:
                def precise_representation(*args, **kwargs):
                    kwargs["tessellation"] = 0.00001
                    if kwargs.get("preferences"):
                        kwargs["preferences"] = dict(kwargs["preferences"], SERIALIZE=True)
                    return original_representation(*args, **kwargs)
                exporter.getRepresentation = precise_representation
            try:
                exporter.export([building], tmp_ifc)
            finally:
                if original_representation:
                    exporter.getRepresentation = original_representation
                if original_attributes:
                    exporter.exportIfcAttributes = original_attributes
            report = V.verify_build(original_data, build_stats, tmp_ifc, stage="post_export")
            build_stats["verify_ifc"] = report.to_dict()
            if report.failed and not allow_errors:
                raise ValueError("IFC verification failed: " + report.text())
            diagnostic_ifc = diagnostic or report.failed
            dst = os.path.abspath(out_base + (".diagnostic" if diagnostic_ifc else "") + ".ifc")
            os.replace(tmp_ifc, dst)
            status = "diagnostic_nonverified" if diagnostic_ifc else "verified"
            build_stats["artifacts"]["ifc"] = AV.artifact_receipt(dst, PROVENANCE, status)
            if not diagnostic_ifc:
                print(f"IFC_DST:{dst}", flush=True)
        except Exception as exc:
            build_stats["artifacts"]["ifc"]["error"] = str(exc)
            import traceback
            traceback.print_exc()
            print(f"IFC_FAILED:{_stats_path}: {exc}", flush=True)
    verified = all(a["status"] == "verified" for a in build_stats["artifacts"].values())
    build_stats["status"] = "verified" if verified else "failed"
    _write_json(_stats_path, build_stats)
    print(f"BUILD_{'VERIFIED' if verified else 'FAILED'}:{_stats_path}", flush=True)
    if not verified:
        raise SystemExit(2)


def _write_json(path, obj):
    try:
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"  [warn] build.json 저장 실패: {e}")


def _world_bounds(obj):
    shape = obj.Shape.copy()
    shape.Placement = obj.getGlobalPlacement()
    box = shape.BoundBox
    return [box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax]


def _validate_saved_document(path, original_doc, expected_products):
    """Open a different pathname to avoid FreeCAD returning the already-open doc.
    Recompute checks that persisted Arch cuts survive reopening rather than merely
    checking the saved shape cache or ZIP structure."""
    reopened = None
    try:
        reopened = App.openDocument(path)
        if reopened is original_doc or reopened.Name == original_doc.Name:
            raise ValueError("FCStd validation reused the active document")
        reopened.recompute()
        by_label = {obj.Label: obj for obj in reopened.Objects}
        for expected in expected_products:
            obj = by_label.get(expected["name"])
            if obj is None or _shape_ok(obj) is not True:
                raise ValueError(f"FCStd missing or invalid persisted shape: {expected['name']}")
            if abs(obj.Shape.Volume-expected["volume_mm3"]) > max(1.0, expected["volume_mm3"]*1e-8):
                raise ValueError(f"FCStd persisted volume/cut changed: {expected['name']}")
            if any(abs(a-b) > 1 for a, b in zip(_world_bounds(obj), expected["bbox_mm"])):
                raise ValueError(f"FCStd persisted world bounds changed: {expected['name']}")
            props = getattr(obj, "IfcProperties", {})
            for key in ("EID", "SourceEIDs", "RunId", "InputSHA256"):
                if str(props.get(key, "")).split(";;", 2)[-1] != str(expected["qa"][key]):
                    raise ValueError(f"FCStd persisted QA property changed: {expected['name']}/{key}")
        return {"reopened": True, "recomputed": True, "shapes": len(expected_products),
                "volume_absolute_tolerance_mm3": 1.0, "volume_relative_tolerance": 1e-8,
                "bounds_tolerance_mm": 1.0}
    finally:
        if reopened is not None and reopened.Name != original_doc.Name:
            App.closeDocument(reopened.Name)
        App.setActiveDocument(original_doc.Name)


def _shape_ok(o):
    """Shape 유효성 검사. 예외 발생 시 None 반환.

    ★ **null shape 은 isValid()==True 다**(빈 것은 공허하게 유효하다). 그래서
    형상이 아예 없는 객체가 검사를 통과해 '벽 398개' 중 하나로 IFC 에 실렸다.
    실측: 두께를 실측값으로 바꾸자 Arch.makeWall(align="Center") 오프셋이 벽
    하나를 null 로 만들었고, 그 벽은 개구부 cut 에서 'Null input shape' 로만
    존재를 드러냈다(그 경고가 없었다면 아무도 몰랐다)."""
    try:
        s = getattr(o, "Shape", None)
        if s is None:
            return None
        if s.isNull():
            return False
        return s.isValid()
    except Exception:
        return None


# main() 실행 조건:
#   - python freecad_builder.py 직접 실행 (__name__=="__main__")
#   - freecadcmd freecad_builder.py (이때 __name__=="freecad_builder", MEP_GEOMETRY 환경변수 설정됨)
# 라이브 애드온이 `import freecad_builder` 할 때(MEP_GEOMETRY 없음)는 main() 실행 안 됨.
if __name__ == "__main__" or os.environ.get("MEP_GEOMETRY"):
    main()
