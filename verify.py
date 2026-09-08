# -*- coding: utf-8 -*-
"""빌드 게이트 — 잘못된 산출물이 조용히 나가는 것을 막는다.

이 모듈이 존재하는 이유(전부 실제로 겪은 사고):
  · 보 135개가 IFC 에서 통째로 빠졌는데 "빌드 완료" 로 출력됨 → V001/V101
  · PIT 벽 상단과 1F 슬래브 하단 사이 1,000mm 공백이 그대로 납품됨 → V004
  · 좌표 -27,250,174mm 인 벽이 isValid()=True 로 통과 → V005
  · 같은 z 에 층이 둘이라 객체가 두 Floor 에 중복 삽입 → V002

FreeCAD 의존 없음(순수 표준 라이브러리 + geom_contract). 그래야 단위테스트가 된다.
freecadcmd 쪽은 build_stats(dict) 를 '생산' 하기만 하면 된다.

  from verify import verify_geometry, verify_build
  rep = verify_geometry(data)          # 빌드 전 — geometry.json 만으로
  rep = verify_build(data, stats, ifc) # 빌드 후 — 산출물 대조
  if rep.failed: ...                   # ERROR 가 하나라도 있으면 True
"""
import json
import math
import os
import re

import geom_contract as GC

# 검사 카탈로그 — id: (기본 severity, 한 줄 설명)
CHECKS = {
    "V106": ("error", "필수 빌드 증거 누락 또는 미생성 요소"),
    "V107": ("error", "현재 실행 IFC 형상/식별자/층/속성 검증 실패"),
    "V001": ("error", "floors[] 에 매칭되지 않는 구조 요소(빌드 시 어느 층에도 안 들어가 IFC 에서 누락)"),
    "V002": ("error", "두 개 이상의 floors[] 에 동시 매칭되는 요소(중복 삽입)"),
    "V003": ("error", "계약 위반 — 알 수 없는 카테고리 버킷, 계약 버전 불일치"),
    "V004": ("error", "층간 연속성 — 연직 지지요소와 상부 바닥 사이 공백"),
    "V005": ("error", "퇴화 형상 — 길이 0, 정점 부족, 좌표 이상치"),
    "V006": ("warn",  "되꺾인 벽 — baseline 이 180° 반전(빌더가 분할하지만 원인은 데이터)"),
    "V007": ("warn",  "미해결 — needs_review, single 페어링 비율, 미매핑 레이어"),
    "V008": ("warn",  "면선 커버리지 부족"),
    "V009": ("error", "미적용 수정, 모호한 식별자 또는 수정 충돌"),
    "V101": ("error", "IFC 대조 — 카테고리별 레코드 수 대비 IFC 엔티티 수"),
    "V102": ("error", "층 소속 — 어느 Floor 에도 안 들어간 객체 / 두 Floor 에 들어간 객체"),
    "V103": ("error", "형상 검증 실패(isValid=False) 객체"),
    "V104": ("error", "모델 bbox 가 입력 bbox 대비 비정상적으로 큼(폭주 솔리드)"),
    "V105": ("warn",  "IFC 에 QA 속성(Pset_MEPParser)이 실리지 않음 — 뷰어에서 "
                      "부재명·EID·needs_review 를 못 본다"),
}

# 'beam' 은 정식 카테고리다(layer_map 의 category=beam). 빠져 있으면 보의
# needs_review·퇴화형상·좌표폭주가 통째로 검사에서 빠진다 — 일람표 미매칭 보
# 236개가 "OK" 로 통과하던 경위.
_STRUCT_CATS = ("wall", "column", "slab", "beam")
_COORD_LIMIT_FACTOR = 10.0     # 입력 bbox 의 몇 배를 넘으면 이상치로 볼지
_FLOOR_TOL = 100.0             # freecad_builder._at_floor 과 동일해야 한다


class Finding(object):
    def __init__(self, cid, severity, message, payload=None):
        self.id, self.severity, self.message = cid, severity, message
        self.payload = payload or {}

    def to_dict(self):
        return {"id": self.id, "severity": self.severity,
                "message": self.message, "payload": self.payload}

    def __repr__(self):
        return f"[{self.severity.upper():5s}] {self.id} {self.message}"


class Report(object):
    def __init__(self, findings=None, policy=None):
        self.findings = list(findings or [])
        self.policy = policy or {}

    @property
    def errors(self):
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warns(self):
        return [f for f in self.findings if f.severity == "warn"]

    @property
    def failed(self):
        return bool(self.errors)

    def to_dict(self):
        return {"status": "failed" if self.failed else "ok",
                "errors": len(self.errors), "warnings": len(self.warns),
                "findings": [f.to_dict() for f in self.findings]}

    def dump(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=1)
        return path

    def text(self, limit=12):
        if not self.findings:
            return "  검사 통과 — 지적사항 없음"
        out = []
        for f in self.findings[:limit]:
            out.append("  " + repr(f))
        if len(self.findings) > limit:
            out.append(f"  … 외 {len(self.findings)-limit}건")
        return "\n".join(out)


def _sev(cid, policy):
    """정책으로 severity 를 조정할 수 있다. 조정은 로그가 아니라 설정 diff 로 남는다."""
    return (policy or {}).get(cid) or CHECKS[cid][0]


def _pts(rec):
    return rec.get("centerline") or rec.get("points") or []


# ══════════════════ 빌드 전 검사 ══════════════════
def verify_geometry(data, policy=None):
    policy = policy or (data.get("verify") or {}).get("severity") or {}
    F = []
    el = data.get("elements") or {}
    params = data.get("params")
    floors = data.get("floors") or []
    edits_report = data.get("edits_report") or {}
    unresolved = {key: edits_report[key] for key in ("orphaned", "ambiguous", "conflicts") if edits_report.get(key)}
    for key in ("edit_conflicts", "conflicts"):
        if data.get(key):
            unresolved[key] = data[key]
    if unresolved:
        F.append(Finding("V009", "error", "Unresolved edits or ambiguous/conflicting identities must be resolved before verified export", unresolved))

    # V003 계약
    for issue in GC.check_contract(data):
        F.append(Finding("V003", _sev("V003", policy), issue))
    if el.get("ignore"):
        F.append(Finding("V003", _sev("V003", policy),
                         f"ignore 버킷에 {len(el['ignore'])}개가 남아 JSON 에 실렸다(드롭돼야 함)"))

    # V001 / V002 — 층 매칭
    if floors:
        fz = [float(f.get("z", 0.0)) for f in floors]
        orphan, dup = [], []
        for cat in _STRUCT_CATS:
            for i, rec in enumerate(el.get(cat) or []):
                z = GC.base_z(cat, rec)
                hits = [k for k, v in enumerate(fz) if abs(z - v) < _FLOOR_TOL]
                if rec.get("level"):
                    hits = [k for k in hits if rec["level"] in (floors[k].get("id"), floors[k].get("label"), floors[k].get("storey"))]
                if not hits:
                    orphan.append({"cat": cat, "index": i, "z_base": z,
                                   "layer": rec.get("layer")})
                elif len(hits) > 1:
                    dup.append({"cat": cat, "index": i, "z_base": z,
                                "floors": [floors[k].get("label") for k in hits]})
        if orphan:
            by = {}
            for o in orphan:
                by.setdefault(round(o["z_base"], 1), 0)
                by[round(o["z_base"], 1)] += 1
            F.append(Finding("V001", _sev("V001", policy),
                             f"층 미매칭 요소 {len(orphan)}개 — 빌드 시 IFC 에서 누락된다",
                             {"count": len(orphan), "by_z_base": by,
                              "floors_z": fz, "sample": orphan[:5]}))
        if dup:
            F.append(Finding("V002", _sev("V002", policy),
                             f"두 층에 동시 매칭 {len(dup)}개 — Floor 중복 삽입",
                             {"count": len(dup), "sample": dup[:5]}))

    # 좌표 범위(이상치 판정 기준).
    # ⚠ 전체 min/max 로 잡으면 이상치가 기준 자체를 키워 스스로를 정상으로 만든다
    #   (27km 짜리 벽 하나가 기준을 27km 로 만들어 검사를 무력화한 실측 사례).
    #   → 1~99 백분위로 강건하게 잡는다.
    xs, ys = [], []
    for cat in _STRUCT_CATS:
        for rec in el.get(cat) or []:
            for p in _pts(rec):
                if math.isfinite(p[0]) and math.isfinite(p[1]):
                    xs.append(p[0]); ys.append(p[1])
    lim = _outlier_limit(xs, ys)

    # V005 퇴화 형상 / V006 되꺾임
    degen, folds = [], []
    for cat in _STRUCT_CATS:
        for i, rec in enumerate(el.get(cat) or []):
            pts = _pts(rec)
            why = None
            if cat == "column" and rec.get("kind") == "circle":
                center = rec.get("center") or []
                if len(center) < 2 or not all(math.isfinite(v) for v in center[:2]) or not math.isfinite(rec.get("radius", 0)) or rec.get("radius", 0) <= 0:
                    why = "잘못된 원형 기둥 중심/반지름"
            elif len(pts) < 2:
                why = "정점 2개 미만"
            elif rec.get("closed") and len(pts) < 3:
                why = "닫힌 폴리곤인데 정점 3개 미만"
            else:
                for p in pts:
                    if not (math.isfinite(p[0]) and math.isfinite(p[1])):
                        why = "좌표가 유한하지 않음"; break
                    if lim and math.hypot(p[0] - lim[0], p[1] - lim[1]) > lim[2]:
                        why = (f"좌표 이상치({p[0]:.0f},{p[1]:.0f}) — 도면 중앙에서 "
                               f"{lim[2]:.0f}mm 를 초과")
                        break
                else:
                    L = sum(math.hypot(b[0]-a[0], b[1]-a[1]) for a, b in zip(pts, pts[1:]))
                    if L < 1.0:
                        why = "총 길이 1mm 미만"
            if why:
                degen.append({"cat": cat, "index": i, "layer": rec.get("layer"), "why": why})
            elif cat == "wall" and len(pts) >= 3 and _has_fold(pts):
                folds.append({"index": i, "layer": rec.get("layer")})
    if degen:
        F.append(Finding("V005", _sev("V005", policy),
                         f"퇴화 형상 {len(degen)}개", {"count": len(degen), "sample": degen[:5]}))
    if folds:
        F.append(Finding("V006", _sev("V006", policy),
                         f"되꺾인 벽 {len(folds)}개 — 빌더가 분할하지만 원본 데이터 중복 의심",
                         {"count": len(folds), "sample": folds[:5]}))

    # V004 층간 연속성
    F += _check_continuity(data, policy)

    # V007 미해결 / V008 커버리지
    nr = sum(1 for c in _STRUCT_CATS for r in (el.get(c) or []) if r.get("needs_review"))
    wp = data.get("wall_pairing") or {}
    single = wp.get("single", 0) + wp.get("single_offset", 0)
    total_wp = single + wp.get("paired", 0)
    bits = []
    if nr:
        bits.append(f"needs_review {nr}개")
    if total_wp and single / total_wp > 0.3:
        bits.append(f"single 페어링 {single}/{total_wp} ({single/total_wp*100:.0f}%)")
    unmapped = [w for w in (data.get("warnings") or []) if "미매핑" in w]
    if unmapped:
        bits.append(f"미매핑 경고 {len(unmapped)}건")
    if bits:
        F.append(Finding("V007", _sev("V007", policy), "미해결: " + ", ".join(bits)))

    qa = data.get("qa") or {}
    cov = qa.get("face_coverage_pct")
    floor_pct = (data.get("verify") or {}).get("coverage_min_pct", 85)
    if cov is not None and cov < floor_pct:
        F.append(Finding("V008", _sev("V008", policy),
                         f"면선 커버리지 {cov:.0f}% < {floor_pct}%", {"coverage": cov}))
    return Report(F, policy)


def _outlier_limit(xs, ys, factor=_COORD_LIMIT_FACTOR):
    """이상치 판정 반경. (중앙점에서의 거리)의 90퍼센타일 × factor.

    min/max 나 낮은 백분위를 쓰면 이상치가 기준 자체를 키워 스스로를 정상으로
    만든다(27km 벽 하나가 기준을 27km 로 만든 실측 사례). 중앙점 기준 거리는
    이상치 몇 개가 섞여도 90퍼센타일이 흔들리지 않는다.
    반환값은 '중앙점으로부터 허용 거리'."""
    n = len(xs)
    if n == 0:
        return None
    sx, sy = sorted(xs), sorted(ys)
    mx, my = sx[n // 2], sy[n // 2]
    d = sorted(math.hypot(x - mx, y - my) for x, y in zip(xs, ys))
    r90 = d[min(n - 1, int(round(0.9 * (n - 1))))]
    if r90 <= 0:
        r90 = d[-1] or 1.0
    return (mx, my, max(r90 * factor, 1000.0))


def _has_fold(pts, cos_tol=-0.99):
    prev = None
    for a, b in zip(pts, pts[1:]):
        dx, dy = b[0]-a[0], b[1]-a[1]
        L = math.hypot(dx, dy)
        if L < 1e-9:
            continue
        v = (dx/L, dy/L)
        if prev and (prev[0]*v[0] + prev[1]*v[1]) <= cos_tol:
            return True
        prev = v
    return False


def _check_continuity(data, policy):
    """인접 층 사이에 구조체가 전혀 없는 공백이 있는지. PIT-1F 1,000mm 공백 재발 방지."""
    el = data.get("elements") or {}
    params = data.get("params")
    tol = (data.get("verify") or {}).get("level_continuity_tol_mm", 50)
    spans = []
    for cat in _STRUCT_CATS:
        for rec in el.get(cat) or []:
            try:
                z0, z1 = GC.z_range(cat if cat != "slab" else
                                    ("beam" if (rec.get("overrides") or {}).get("ifc_type") == "Beam" else "slab"),
                                    rec, params)
            except GC.ContractError:
                continue
            if z1 > z0:
                spans.append((z0, z1))
    if not spans:
        return []
    spans.sort()
    merged = [list(spans[0])]
    for z0, z1 in spans[1:]:
        if z0 <= merged[-1][1] + tol:
            merged[-1][1] = max(merged[-1][1], z1)
        else:
            merged.append([z0, z1])
    if len(merged) == 1:
        return []
    gaps = [{"from": merged[i][1], "to": merged[i+1][0],
             "gap_mm": merged[i+1][0] - merged[i][1]} for i in range(len(merged)-1)]
    return [Finding("V004", _sev("V004", policy),
                    f"층간 공백 {len(gaps)}곳 — 구조체가 끊긴다: "
                    + ", ".join(f"{g['from']:.0f}~{g['to']:.0f}({g['gap_mm']:.0f}mm)" for g in gaps[:3]),
                    {"gaps": gaps, "covered": merged})]


# ══════════════════ 빌드 후 검사 ══════════════════
_IFC_CAT_MAP = {
    "wall":   ("IFCWALL", "IFCWALLSTANDARDCASE"),
    "column": ("IFCCOLUMN",),
    "slab":   ("IFCSLAB",),
    "beam":   ("IFCBEAM",),
}


def count_ifc_psets(ifc_path, name="Pset_MEPParser"):
    """지정 PropertySet 을 가진 IFC 객체 수. ifcopenshell 없으면 평문으로 센다.

    `addProperty("App::PropertyString", …)` 는 FreeCAD 문서 안에만 남고 IFC 로는
    안 나간다. 실제로 그 상태로 오래 나갔고 아무도 몰랐다 — 형상은 멀쩡하니
    기존 검사가 전부 통과했기 때문이다. 그래서 별도 검사가 필요하다."""
    try:
        with open(ifc_path, "r", encoding="utf-8", errors="ignore") as fh:
            txt = fh.read()
    except Exception:
        return 0
    return len(re.findall(r"IFCPROPERTYSET\('[^']*',#\d+,'" + re.escape(name) + r"'", txt))


def count_ifc_entities(ifc_path):
    """Lightweight catalog count only; strict verification uses inspect_ifc."""
    counts = {}
    try:
        with open(ifc_path, "r", encoding="utf-8", errors="ignore") as fh:
            txt = fh.read()
    except Exception:
        return {}
    for key, names in _IFC_CAT_MAP.items():
        counts[key] = sum(len(re.findall(r"=\s*" + n + r"\(", txt, re.I)) for n in names)
    return counts


def verify_build(data, build_stats, ifc_path=None, policy=None, stage=None):
    """build_stats: freecadcmd 쪽에서 생산하는 순수 dict.
      {intent:{wall,column,slab,beam}, built:{...}, floor_orphans:int,
       floor_dups:int, invalid_shapes:int, bbox:[x0,y0,z0,x1,y1,z1], clashes:[...]}"""
    policy = policy or (data.get("verify") or {}).get("severity") or {}
    F = []
    st = build_stats or {}
    stage = stage or ("post_export" if ifc_path else "pre_export")
    if stage not in ("catalog", "pre_export", "post_export"):
        raise ValueError(f"Unknown validation stage: {stage}")
    required = ("intent", "built", "floor_orphans", "floor_dups", "invalid_shapes", "bbox")
    missing = [key for key in required if key not in st]
    if missing and (stage != "catalog" or not st):
        F.append(Finding("V106", "error", "Missing required build statistics", {"missing": missing}))
    unbuilt = {c: v for c, v in (st.get("unbuilt") or {}).items()
               if (v.get("count", 0) if isinstance(v, dict) else len(v))}
    if unbuilt:
        F.append(Finding("V106", "error", "Input records were not built", unbuilt))
    if stage != "catalog":
        F.extend(verify_geometry(data).findings)
        from artifact_validation import input_hash
        prov = st.get("provenance") or {}
        if not prov.get("run_id") or prov.get("input_sha256") != input_hash(data):
            F.append(Finding("V106", "error", "Missing or stale input/run provenance"))
        if st.get("runtime_errors"):
            F.append(Finding("V106", "error", "Runtime failed", {"errors": st["runtime_errors"]}))
        # 개구부 판정은 세 갈래다. 종전에는 셋을 한 줄로 묶어 전부 error 로 올려,
        # 붙일 벽이 없는 개구부 하나만 있어도 건물 전체가 납품 불가가 됐다.
        for opening in st.get("opening_results") or []:
            hosts = opening.get("requested_hosts") or []
            cut = opening.get("cut_host_eids") or []
            if hosts and not cut:
                # 호스트를 지정해 놓고 하나도 못 뚫었다 = 약속한 구멍이 없다.
                F.append(Finding("V106", "error", "Opening cut nothing", opening))
            elif opening.get("failed_hosts"):
                # 일부만 못 맞혔다. 링크는 후보 목록이라 정상적으로도 생길 수 있으니
                # 납품을 막지는 않되, 조용히 넘기지도 않는다.
                F.append(Finding("V106", "warn", "Opening missed some hosts", opening))
        # 붙일 벽을 아예 못 찾은 개구부. 빌더가 못 한 일이 아니라 링크가 못 찾은
        # 것이므로 납품을 막지 않는다 — 다만 **사유별로 묶어서** 반드시 말한다.
        # 개구부마다 하나씩 올리면(실측 15건) 보고서가 그걸로 덮이고, 전부 한 줄로
        # 묶으면 "고칠 것 없음" 과 "레이어 매핑이 틀렸음" 이 같은 말로 보인다.
        # 사유는 `link_openings_to_walls` 가 레코드에 적어 둔다(거기 주석 참조).
        _why = {o.get("eid"): o.get("no_host_reason")
                for o in (data.get("elements") or {}).get("opening") or []}
        _groups = {}
        for o in st.get("opening_results") or []:
            if not (o.get("requested_hosts") or []):
                _groups.setdefault(_why.get(o.get("eid")) or "unknown", []).append(o.get("eid"))
        for _key, _msg in (
                ("wall_open_at_this_span",
                 "Openings sit where the drawing already left the wall open (nothing to cut)"),
                ("no_wall_on_this_line",
                 "Openings are not on any wall line — check the layer mapping"),
                ("unknown", "Openings found no host wall")):
            if _groups.get(_key):
                F.append(Finding("V106", "warn", _msg,
                                 {"count": len(_groups[_key]), "eids": _groups[_key][:20]}))
    if ifc_path and not os.path.isfile(ifc_path):
        F.append(Finding("V107", "error", "IFC output is missing"))
    if stage == "post_export":
        try:
            if not ifc_path or not os.path.isfile(ifc_path) or os.path.getsize(ifc_path) == 0:
                raise ValueError("IFC output is missing or empty")
            from artifact_validation import inspect_ifc
            errors, mapping = inspect_ifc(data, st, ifc_path)
            st["eid_map"] = mapping
            for error in errors:
                F.append(Finding("V107", "error", error))
        except Exception as exc:
            F.append(Finding("V107", "error", f"IFC verifier failed: {exc}"))

    # V102 층 소속
    orph, dup = int(st.get("floor_orphans", 0)), int(st.get("floor_dups", 0))
    if orph:
        F.append(Finding("V102", _sev("V102", policy),
                         f"어느 Floor 에도 속하지 않은 객체 {orph}개 — IFC 에서 누락된다",
                         {"orphans": orph, "detail": st.get("floor_orphan_detail")}))
    if dup:
        F.append(Finding("V102", _sev("V102", policy),
                         f"두 Floor 에 중복 삽입된 객체 {dup}개", {"dups": dup}))

    # V103 형상
    inv = int(st.get("invalid_shapes", 0))
    if inv:
        F.append(Finding("V103", _sev("V103", policy),
                         f"형상 검증 실패 객체 {inv}개", {"invalid": inv}))

    # V104 bbox 폭주
    bb = st.get("bbox")
    if bb and len(bb) == 6:
        el = data.get("elements") or {}
        xs = [p[0] for c in _STRUCT_CATS for r in (el.get(c) or []) for p in _pts(r)]
        ys = [p[1] for c in _STRUCT_CATS for r in (el.get(c) or []) for p in _pts(r)]
        if xs:
            want = max(max(xs)-min(xs), max(ys)-min(ys))
            got = max(bb[3]-bb[0], bb[4]-bb[1])
            if want > 0 and got > want * 2.0:
                F.append(Finding("V104", _sev("V104", policy),
                                 f"모델 bbox {got:.0f}mm 가 입력 {want:.0f}mm 의 2배 초과 — 폭주 솔리드 의심",
                                 {"model": got, "input": want}))

    # V105 QA 속성 왕복 — 형상만 맞고 속성이 빠지면 뷰어 검수가 통째로 무의미해진다
    if stage == "catalog" and ifc_path and os.path.exists(ifc_path):
        n_built = sum(int(v) for k, v in (st.get("built") or {}).items()
                      if k in ("walls", "columns", "slabs", "beams"))
        n_pset = count_ifc_psets(ifc_path)
        if n_built and not n_pset:
            F.append(Finding("V105", _sev("V105", policy),
                             f"IFC 에 Pset_MEPParser 가 0개 — 객체 {n_built}개를 만들었는데 "
                             "부재명·EID·needs_review 가 하나도 안 실렸다",
                             {"built": n_built, "psets": 0}))

    # V101 IFC 대조
    if stage == "catalog" and ifc_path and os.path.exists(ifc_path):
        got = count_ifc_entities(ifc_path)
        intent = st.get("intent") or {}
        for cat, n_want in intent.items():
            if cat not in _IFC_CAT_MAP or not n_want:
                continue
            n_got = got.get(cat, 0)
            if n_got == 0:
                F.append(Finding("V101", _sev("V101", policy),
                                 f"IFC 에 {cat} 이(가) 0개 — {n_want}개를 만들었는데 전부 누락됐다",
                                 {"category": cat, "intent": n_want, "ifc": 0}))
            elif n_got < n_want * 0.9:
                F.append(Finding("V101", _sev("V101", policy),
                                 f"IFC {cat} {n_got}개 < 생성 {n_want}개 (10% 이상 누락)",
                                 {"category": cat, "intent": n_want, "ifc": n_got}))
    return Report(F, policy)


# ══════════════════ CLI ══════════════════
def main():
    import argparse
    ap = argparse.ArgumentParser(description="geometry.json / 빌드 산출물 검증")
    ap.add_argument("geometry", nargs="?", help="geometry.json 경로")
    ap.add_argument("--build-stats", help="<out>.build.json 경로(빌드 후 검사)")
    ap.add_argument("--ifc", help="빌드된 .ifc 경로")
    ap.add_argument("--dump-checks", action="store_true", help="검사 카탈로그를 JSON 으로 출력")
    ap.add_argument("--json", help="리포트를 이 경로에 저장")
    a = ap.parse_args()

    if a.dump_checks:
        print(json.dumps({k: {"severity": v[0], "desc": v[1]} for k, v in CHECKS.items()},
                         ensure_ascii=False, indent=1))
        return 0
    if not a.geometry:
        ap.error("geometry.json 경로가 필요하다 (--dump-checks 제외)")

    data = json.load(open(a.geometry, encoding="utf-8"))
    rep = verify_geometry(data)
    print("[빌드 전 검사]")
    print(rep.text())

    if a.build_stats or a.ifc:
        st = json.load(open(a.build_stats, encoding="utf-8")) if a.build_stats and os.path.exists(a.build_stats) else {}
        rep2 = verify_build(data, st, a.ifc, stage="post_export")
        print("[빌드 후 검사]")
        print(rep2.text())
        rep.findings += rep2.findings

    print(f"\n결과: {'FAILED' if rep.failed else 'OK'} "
          f"(error {len(rep.errors)} / warn {len(rep.warns)})")
    if a.json:
        rep.dump(a.json)
        print(f"리포트 → {a.json}")
    return 2 if rep.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
