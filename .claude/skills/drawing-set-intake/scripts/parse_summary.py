"""파싱 + 지표 요약. 파싱할 때마다 같은 지표를 찍어 전후를 숫자로 비교한다.

    python parse_summary.py <도면.dxf> --layer-map <lm.csv> [--block-map <bm.csv>] [--schedule <창호일람.xlsx>] --out <접두>
    python parse_summary.py --selftest

<접두>.geometry.json 과 <접두>.log(파서 출력 전부)를 쓰고 지표를 stdout 에 찍는다.
빌드는 이어서: python ifc_builder.py <접두>.geometry.json <접두>.ifc  →  build_summary.py <접두>
"""
import argparse
import collections
import contextlib
import io
import json
import os
import sys
import tempfile
import warnings
from pathlib import Path

REPO = Path(os.environ.get("MEP_PARSER_REPO") or Path(__file__).resolve().parents[4])
sys.path.insert(0, str(REPO))
TEMPLATE = Path(__file__).resolve().parents[1] / "references" / "layer_map_template.csv"


def metrics(g):
    el = g["elements"]
    ops = el.get("opening", [])
    C = collections.Counter
    ignored = C()
    for lay, n in (g.get("ignored") or {}).items():     # '.,ignore' 가 삼킨 것 — 역할별로(마지막 '$0$' 뒤)
        ignored[lay.rsplit("$0$", 1)[-1]] += n
    return {
        "elements": {k: len(v) for k, v in el.items() if v},
        "infill_walls": sum(1 for w in el.get("wall", []) if w.get("source") == "opening_infill"),
        "opening_kind": dict(C(o["kind"] for o in ops)),
        "subtype": dict(C(o.get("subtype") for o in ops)),
        "openings_dims_assumed": g.get("openings_dims_assumed"),
        "width_source": dict(C(o.get("width_source") for o in ops if o["kind"] == "circle")),
        "dims_source": dict(C(o.get("dims_source") for o in ops)),
        "no_host_reason": dict(C(o.get("no_host_reason") for o in ops)),
        "opening_fragments_dropped": g.get("opening_fragments_dropped"),
        "opening_infill": g.get("opening_infill"),
        "shadowed_layer_rules": len(g.get("shadowed_layer_rules") or []),
        "warnings": len(g.get("warnings") or []),
        # 마지막 줄 '.,ignore' 뒤로는 미매핑 경고가 없다 — 벽·문·창 역할이 여기 있으면 layer_map 에 올린다.
        # 전부 찍는다: 상위만 자르면 작은 벽 역할이 빠진다(실측 기준층: 벽 역할이 77개 중 43위)
        "ignored_roles": f"{len(ignored)} roles / {sum(ignored.values())} entities: {ignored.most_common()}",
    }


def run(dxf, layer_map, out, block_map=None, schedule=None):
    import dxf_parser as P
    bm = P.load_layer_map(block_map) if block_map else P.DEFAULT_BLOCK_RULES
    kw = {}
    if schedule:
        from schedule_io import load_schedule
        kw["ext_schedule"] = load_schedule(schedule)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        g = P.parse(dxf, P.load_layer_map(layer_map), bm, **kw)
    Path(out + ".log").write_text(buf.getvalue(), encoding="utf-8", errors="replace")
    Path(out + ".geometry.json").write_text(json.dumps(g, ensure_ascii=False), encoding="utf-8", errors="replace")
    m = metrics(g)
    for k, v in m.items():
        print(f"{k}: {v}")
    for w in (g.get("warnings") or [])[:5]:
        print("  [경고]", w)
    return m


def selftest():
    import ezdxf
    with tempfile.TemporaryDirectory() as d:
        doc = ezdxf.new(units=4)
        msp = doc.modelspace()
        for y in (0, 200):                                  # 양면 2선 벽, xref bind 레이어 이름
            msp.add_line((0, y), (6000, y), dxfattribs={"layer": "UNIT$0$A-WALL"})
        msp.add_line((0, 5000), (6000, 5000), dxfattribs={"layer": "UNIT$0$A-FURN"})   # 버려질 배경
        dxf = os.path.join(d, "plan.dxf")
        doc.saveas(dxf)
        m = run(dxf, str(TEMPLATE), os.path.join(d, "t"))
        assert m["elements"] == {"wall": 1}, m                # 틀의 '$' 앵커가 역할 꼬리로 맞는다
        g = json.loads(Path(d, "t.geometry.json").read_text(encoding="utf-8"))
        assert abs(g["elements"]["wall"][0]["width_detected"] - 200) < 1, g["elements"]["wall"][0]
        assert g["ignored"].get("UNIT$0$A-FURN") == 1, g["ignored"]
        assert "('A-FURN', 1)" in m["ignored_roles"], m["ignored_roles"]
        assert Path(d, "t.log").exists()
    print("selftest OK")


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dxf", nargs="?")
    ap.add_argument("--layer-map")
    ap.add_argument("--block-map")
    ap.add_argument("--schedule", help="창호일람 .xlsx (schedule_io.load_schedule)")
    ap.add_argument("--out", help="출력 접두(작업 폴더 안)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not (a.dxf and a.layer_map and a.out):
        ap.error("dxf, --layer-map, --out 이 필요하다")
    run(a.dxf, a.layer_map, a.out, a.block_map, a.schedule)


if __name__ == "__main__":
    main()
