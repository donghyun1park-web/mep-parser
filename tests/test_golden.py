# -*- coding: utf-8 -*-
"""실무 도면 골든 회귀.

합성 샘플(벽 3~4개)만으로는 **벽이 전부 10mm 씩 밀려도 통과한다.** 실제로 버그를
드러내는 건 실무 도면(벽 637개, 부재 225개)인데 그건 고객 자료라 저장소에 못 넣는다.

그래서 **다이제스트만 커밋한다** — 도면은 각자 로컬에 두고 경로만 gitignore 되는
`tests/golden.local.json` 에 적는다. 도면이 없으면 [skip] 을 찍고 넘어간다.

전체 JSON 골든은 float 하나만 바뀌어도 깨져서 두 주면 아무도 안 본다.
여기 담는 건 '한눈에 틀렸다고 알 수 있는 요약' 이다.

설정 (tests/golden.local.json):
    {"지하3층": {"dxf": "C:/.../지하3층 건축평면도.dxf",
                 "layer_map": "layer_map.csv",
                 "block_map": "block_map.csv"}}

항목은 두 종류다:
    {"단위세대_난방": {"dxf": "C:/.../heating.dxf", "layer_map": "layer_map.csv"},
     "단위세대_환기": {"project": "C:/.../unit.mep"}}
`project` 는 저장된 프로필·영역·단위 그대로 `ProjectSession` 으로 다시 해석해 설비 요약까지 본다
(폴더는 복사해서 연다 — 원본 revision 을 건드리지 않게).

갱신(의도한 변화일 때만):
    python tests/test_golden.py --bless

★ **키 이름은 중립적으로** — 현장명·고객명은 공개 저장소에 나가면 안 된다. 등록 절차와 커밋 전
식별자 스캔은 `docs/release_checklist.md` 에 있다.
"""
import contextlib
import hashlib
import io
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

CONFIG = os.path.join(HERE, "golden.local.json")
GOLDEN = os.path.join(HERE, "golden.json")


def _path(p):
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


def digest(data):
    """geometry.json → 비교 가능한 요약. 결정론적이어야 한다(정렬·반올림)."""
    el = data.get("elements", {})
    thick = {}
    for w in el.get("wall", []):
        t = w.get("width_detected")
        if t:
            k = str(int(round(float(t) / 10.0) * 10))
            thick[k] = thick.get(k, 0) + 1
    xs = [p[0] for recs in el.values() for r in recs for p in (r.get("points") or [])]
    ys = [p[1] for recs in el.values() for r in recs for p in (r.get("points") or [])]
    eids = sorted(r["eid"] for recs in el.values() for r in recs if r.get("eid"))
    qa = data.get("qa") or {}
    return {
        "counts": {k: len(v) for k, v in sorted(el.items()) if v},
        "wall_pairing": data.get("wall_pairing") or {},
        "wall_thickness_10mm": dict(sorted(thick.items(), key=lambda kv: int(kv[0]))),
        "thin_pairs": dict(sorted((data.get("thin_pairs") or {}).items())),
        # 가정 치수가 늘어나는 것도 회귀다 — 조용히 더 추정하기 시작하면 잡힌다.
        "openings_dims_assumed": dict(sorted((data.get("openings_dims_assumed") or {}).items())),
        "small_openings_dropped": sum((data.get("small_openings_dropped") or {}).values()),
        # 좌표까지 같아 버린 중복 부재. 늘면 도면이 달라졌거나 판정이 헐거워진 것이다.
        "closed_wall_dups": sum((data.get("closed_wall_dups") or {}).values()),
        "duplicate_geometry_dropped": {
            k: sum(v.values())
            for k, v in sorted((data.get("duplicate_geometry_dropped") or {}).items())},
        # 실측≠선언 두께. 늘면 빌드가 실측을 더 많이 무시하고 있다는 뜻이다.
        "width_conflicts": sum(c["count"] for c in (data.get("width_conflicts") or [])),
        # ★ EID 가 부재를 특정하지 못하면 수동 수정이 엉뚱한 데 걸린다.
        # 남는 중복은 좌표까지 같은 진짜 중복 부재뿐이어야 한다(실측 263 → 10).
        "eid_collisions": {c: len(v) - len({r["eid"] for r in v if r.get("eid")})
                           for c, v in sorted(el.items())
                           if v and len(v) != len({r["eid"] for r in v if r.get("eid")})},
        "needs_review": sum(1 for recs in el.values() for r in recs
                            if r.get("needs_review")),
        "face_coverage_pct": round(qa.get("face_coverage_pct", 0)),
        "floors": [round(f.get("z", 0)) for f in (data.get("floors") or [])],
        "member_schedule": {k: v for k, v in (data.get("member_schedule") or {}).items()
                            if k in ("tables", "members", "matched", "unmatched")},
        "bbox": [round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))] if xs else [],
        # grouping 이 바뀌면 EID 가 바뀐다. 의도한 변화면 --bless, 아니면 회귀다.
        "eid_hash": hashlib.sha1("\n".join(eids).encode()).hexdigest()[:12],
        "eid_count": len(eids),
        **_mep_digest(data),
    }


def _mep_digest(data):
    """설비 프로젝트에서만 나오는 요약. 건축 도면 골든은 이 키들이 없어 그대로다.

    id·좌표는 담지 않는다 — 반올림 한 자리에 흔들리면 두 주면 아무도 안 본다. 세는 것만 담는다."""
    out = {}
    # `mep` 카테고리 수는 넣지 않는다 — `counts` 에 이미 있고, 건축 도면 골든까지 빈 키가 붙어 정보 없이
    # 다시 승인하게 된다(회귀 파일은 '한눈에 틀렸다고 알 수 있는 요약' 이어야 한다).
    joints = data.get("mep_joints") or (data.get("mep_diagnostics") or {}).get("paths", {}).get("joints")
    if joints:
        out["mep_joints"] = {k: joints[k] for k in ("joints", "taps") if k in joints}
    coverage = data.get("source_coverage") or {}
    if coverage:
        out["source_coverage"] = {k: coverage.get(k) for k in ("selected", "represented", "omitted", "complete")}
    net = (data.get("mep_connectivity") or {}).get("summary") or {}
    if net and not net.get("error"):
        out["mep_connectivity"] = {k: net.get(k) for k in
                                   ("runs", "groups", "groups_with_candidates", "open_ends", "candidates",
                                    "by_kind", "conflicts", "by_status")}
    clash = (data.get("clash_review") or {}).get("summary") or {}
    if clash and not clash.get("error"):
        out["clash_review"] = {k: clash.get(k) for k in
                               ("total", "by_kind", "through_openings", "through_openings_assumed", "assumed_basis")}
    issues = (data.get("mep_diagnostics") or {}).get("issues")
    if issues:
        codes = {}
        for issue in issues:
            codes[issue.get("code", "?")] = codes.get(issue.get("code", "?"), 0) + 1
        out["mep_issues"] = dict(sorted(codes.items()))
    return out


def _parse(spec):
    if spec.get("project"):
        return _parse_project(spec)
    import dxf_parser as dp
    dxf = _path(spec["dxf"])
    if not os.path.exists(dxf):
        return None
    rules = dp.load_layer_map(_path(spec["layer_map"])) if spec.get("layer_map") \
        else dp.DEFAULT_LAYER_RULES
    blocks = dp.load_layer_map(_path(spec["block_map"])) if spec.get("block_map") else []
    with contextlib.redirect_stdout(io.StringIO()):
        return dp.parse(dxf, rules, blocks,
                        member_schedule=spec.get("member_schedule"))


def _parse_project(spec):
    """설비 프로젝트(`.mep` 폴더) 회귀 — 저장된 프로필·영역·단위 그대로 다시 해석한다.

    레이어맵 파싱만으로는 설비 해석(프로필 선택·단면·고저·이음·연결·간섭)이 하나도 안 돌아, 오늘 바꾼
    것들이 회귀로 안 잡혔다. ★ 폴더를 **복사해서** 연다 — `refresh_inputs` 가 원본 지문을 갱신하며
    project.json 을 건드리면, 회귀를 돌릴 때마다 사람이 쓰는 프로젝트의 revision 이 흔들린다."""
    import shutil
    import tempfile
    folder = _path(spec["project"])
    if not os.path.isdir(folder):
        return None
    from project_server import ProjectSession
    from project_store import ProjectStore
    with tempfile.TemporaryDirectory(prefix="golden_mep_") as tmp:
        copy = os.path.join(tmp, os.path.basename(folder.rstrip("/\\")) or "project.mep")
        shutil.copytree(folder, copy)
        manifest = json.loads(open(os.path.join(copy, "project.json"), encoding="utf-8").read())
        if any(not os.path.exists(source["path"]) for source in manifest["sources"]):
            return None                       # 원본 도면이 이 PC 에 없다
        with contextlib.redirect_stdout(io.StringIO()):
            return ProjectSession(ProjectStore(copy)).state()["geometry"]


def _load(p, default):
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _diff(want, got, path=""):
    """어디가 달라졌는지 사람이 읽을 수 있게. '해시 불일치' 만 던지면 아무도 안 고친다."""
    out = []
    for k in sorted(set(want) | set(got)):
        a, b = want.get(k), got.get(k)
        if isinstance(a, dict) and isinstance(b, dict):
            out += _diff(a, b, f"{path}{k}.")
        elif a != b:
            out.append(f"    {path}{k}: 골든={a!r}  현재={b!r}")
    return out


def test_real_drawings_match_the_golden_digest():
    cfg = _load(CONFIG, {})
    if not cfg:
        raise unittest.SkipTest(f'{os.path.basename(CONFIG)} 없음 — 실무 도면 회귀 미실행')
    golden = _load(GOLDEN, {})
    problems, ran, missing = [], 0, []
    for name, spec in sorted(cfg.items()):
        data = _parse(spec)
        if data is None:
            print(f"  [skip] '{name}' 도면 파일 없음: {spec['dxf']}")
            missing.append(name)
            continue
        ran += 1
        got = digest(data)
        if name not in golden:
            problems.append(f"  '{name}' 골든이 없다 — 확인 후 --bless 로 등록할 것")
            continue
        d = _diff(golden[name], got)
        if d:
            problems.append(f"  '{name}' 다이제스트 불일치:\n" + "\n".join(d))
    if problems:
        raise AssertionError(
            "\n" + "\n".join(problems)
            + "\n  의도한 변화면: python tests/test_golden.py --bless")
    if ran:
        print(f"  [golden] 실무 도면 {ran}건 일치")
    if missing:
        raise unittest.SkipTest(f'{ran}건 일치, {len(missing)}건 미실행: {missing}')


def _bless():
    cfg = _load(CONFIG, {})
    if not cfg:
        print(f"{CONFIG} 가 없습니다. 형식은 이 파일 상단 독스트링 참고.")
        return 1
    golden = _load(GOLDEN, {})
    for name, spec in sorted(cfg.items()):
        data = _parse(spec)
        if data is None:
            print(f"  [skip] {name}: {spec['dxf']} 없음")
            continue
        old, new = golden.get(name), digest(data)
        golden[name] = new
        if old is None:
            print(f"  [신규] {name}")
        else:
            d = _diff(old, new)
            print(f"  [갱신] {name}" + ("\n" + "\n".join(d) if d else "  (변화 없음)"))
    with open(GOLDEN, "w", encoding="utf-8") as f:
        json.dump(golden, f, ensure_ascii=False, indent=2, sort_keys=True)
    print(f"\n저장 → {GOLDEN}")
    return 0


if __name__ == "__main__":
    if "--bless" in sys.argv:
        raise SystemExit(_bless())
    test_real_drawings_match_the_golden_digest()
    print("OK")
