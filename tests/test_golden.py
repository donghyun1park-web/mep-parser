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

갱신(의도한 변화일 때만):
    python tests/test_golden.py --bless
"""
import contextlib
import hashlib
import io
import json
import os
import sys

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
    }


def _parse(spec):
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
        print(f"  [skip] {os.path.basename(CONFIG)} 없음 — 실무 도면 골든 회귀를 건너뜀")
        return
    golden = _load(GOLDEN, {})
    problems, ran = [], 0
    for name, spec in sorted(cfg.items()):
        data = _parse(spec)
        if data is None:
            print(f"  [skip] '{name}' 도면 파일 없음: {spec['dxf']}")
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
