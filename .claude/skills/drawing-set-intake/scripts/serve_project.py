"""도면을 프로젝트로 열고 브라우저 미리보기 URL 을 띄운다(계속 떠 있다 — 백그라운드로 돌린다).

    python serve_project.py <도면.dxf> --folder <작업폴더>/<이름>.mep --layer-map <lm.csv>
                            [--block-map <bm.csv>] [--schedule <창호일람.xlsx>] [--hours 2]

기존 프로젝트 폴더는 **저장된** 원본 설정을 쓴다 — 새 block_map·일람이면 새 폴더를 준다.
떠 있는 서버는 옛 파서 모듈을 들고 있다 — 파서를 고쳤으면 서버를 내리고 다시 띄운다.
"""
import argparse
import os
import sys
import time
from pathlib import Path

REPO = Path(os.environ.get("MEP_PARSER_REPO") or Path(__file__).resolve().parents[4])
sys.path.insert(0, str(REPO))


def _same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dxf")
    ap.add_argument("--folder", required=True, help="<이름>.mep 프로젝트 폴더(작업 폴더 안)")
    ap.add_argument("--layer-map", required=True)
    ap.add_argument("--block-map")
    ap.add_argument("--schedule")
    ap.add_argument("--hours", type=float, default=2.0)
    a = ap.parse_args()
    from project_server import open_source_project
    from project_store import ProjectStore

    if Path(a.folder, "project.json").exists():     # 조용히 옛 설정으로 뜨는 것을 막는다
        src = ProjectStore(a.folder).read()["sources"][0]
        # 인자를 빼도 저장된 값이 쓰인다 — 뺀 것도 다른 설정으로 본다
        stale = [k for k, v in (("layer_map", a.layer_map), ("block_map", a.block_map), ("schedule", a.schedule))
                 if bool(v) != bool(src.get(k)) or (v and not _same(src[k], v))]
        if stale:
            sys.exit(f"[!] {a.folder} 는 다른 {', '.join(stale)} 로 만든 프로젝트다 — 새 --folder 를 준다")

    sess = open_source_project(a.dxf, folder=a.folder, layer_map=a.layer_map,
                               block_map=a.block_map, schedule=a.schedule)
    g = sess.state()["geometry"]
    print("elements", {k: len(v) for k, v in g["elements"].items() if v}, flush=True)
    print("openings_dims_assumed", g.get("openings_dims_assumed"), flush=True)
    with sess.serve() as server:
        print("URL", server.url, flush=True)
        time.sleep(a.hours * 3600)


if __name__ == "__main__":
    main()
