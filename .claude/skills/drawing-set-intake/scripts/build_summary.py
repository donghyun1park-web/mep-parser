"""IFC 빌드 영수증 요약. '빌드 완료' 로그가 아니라 <접두>.build.json 을 읽는다.

    python ifc_builder.py <접두>.geometry.json <접두>.ifc      # → <접두>.build.json
    python build_summary.py <접두>
    python build_summary.py --selftest
"""
import argparse
import collections
import json
import sys
import tempfile
from pathlib import Path


def summary(prefix):
    b = json.loads(Path(prefix + ".build.json").read_text(encoding="utf-8"))
    g = json.loads(Path(prefix + ".geometry.json").read_text(encoding="utf-8"))
    ops = {o.get("eid"): o for o in g["elements"].get("opening", [])}
    res = b.get("opening_results") or []
    leaf = [r for r in res if r.get("leaf_built")]
    uncut = [r for r in leaf if not r.get("cut_host_eids")]
    C = collections.Counter
    ids = lambda key: dict(C(f.get("id") for f in (b.get(key) or {}).get("findings", [])))
    return {
        "status": b.get("status"),
        "ifc": (b.get("artifacts") or {}).get("ifc", {}).get("status"),
        "built": b.get("built"),
        "openings": len(res), "panels": len(leaf),
        "panels_without_host_cut": len(uncut),
        "uncut_by_no_host_reason": dict(C(ops.get(r["eid"], {}).get("no_host_reason") for r in uncut)),
        "failed_hosts": dict(C(f.get("error") for r in res for f in r.get("failed_hosts", []))),
        "verify": ids("verify"), "verify_ifc": ids("verify_ifc"),
    }


def selftest():
    with tempfile.TemporaryDirectory() as d:
        p = str(Path(d, "t"))
        Path(p + ".geometry.json").write_text(json.dumps({"elements": {"opening": [
            {"eid": "o:1"}, {"eid": "o:2", "no_host_reason": "no_wall_near"}]}}), encoding="utf-8")
        Path(p + ".build.json").write_text(json.dumps({
            "status": "verified", "artifacts": {"ifc": {"status": "verified"}},
            "opening_results": [{"eid": "o:1", "leaf_built": True, "cut_host_eids": ["w:1"]},
                                {"eid": "o:2", "leaf_built": True, "cut_host_eids": [],
                                 "failed_hosts": [{"error": "empty_cut"}]}],
            "verify": {"findings": [{"id": "V106"}, {"id": "V106"}]}}), encoding="utf-8")
        s = summary(p)
        assert s["panels"] == 2 and s["panels_without_host_cut"] == 1, s
        assert s["uncut_by_no_host_reason"] == {"no_wall_near": 1}, s
        assert s["failed_hosts"] == {"empty_cut": 1} and s["verify"] == {"V106": 2}, s
    print("selftest OK")


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("prefix", nargs="?", help="<접두>.build.json 과 <접두>.geometry.json 의 접두")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.prefix:
        ap.error("prefix 가 필요하다")
    for k, v in summary(a.prefix).items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
