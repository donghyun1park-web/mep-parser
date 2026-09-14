# -*- coding: utf-8 -*-
"""동봉 런타임 만들기 — 현장 PC 가 npm·bun 없이 편집 화면을 띄우게.

    (체크아웃 apps/editor 에서) PASCAL_PORTABLE_BUILD=1 next build
    python pascal_host/stage_runtime.py --pascal <체크아웃> --out pascal_runtime [--node <node.exe>]
    run_editor.bat <도면.dxf | 프로젝트.mep>      ← = run_host.py --runtime pascal_runtime --open

Pascal 자신의 `packages/cli/scripts/stage-runtime.ts` 와 같은 순서다: standalone 복사 → public·.next/static
복사 → bun 저장소(`node_modules/.bun`) 평탄화(링크는 실체로 복사) → sharp 제거 → 네이티브 모듈 거부.
거기에 node 실행 파일과 `mep-runtime.json`(고정 커밋·오버레이 지문)을 더한다 — `run_host.py --runtime` 이
대조해서 다른 코드로 만든 런타임은 띄우지 않는다.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_host as R  # noqa: E402


def stage(checkout, out, node):
    checkout, out, node = Path(checkout).resolve(), Path(out).resolve(), Path(node).resolve()
    app = checkout / "apps" / "editor"
    standalone = app / ".next" / "standalone"
    if not (standalone / "apps" / "editor" / "server.js").is_file():
        raise SystemExit("standalone 빌드가 없다 — apps/editor 에서 PASCAL_PORTABLE_BUILD=1 next build 할 것")
    if R.checkout_commit(checkout) != R.pinned_commit():
        raise SystemExit("체크아웃이 고정 커밋이 아니다")
    missing, changed = R.overlay_drift(checkout)
    if missing or changed:
        raise SystemExit("오버레이가 체크아웃과 다르다 — run_host.py --sync-overlay --build 뒤 다시 빌드할 것")
    if not node.is_file():
        raise SystemExit("node 실행 파일이 없다: %s" % node)

    if out.exists():
        shutil.rmtree(out)
    runtime = out / "runtime"
    shutil.copytree(standalone, runtime, symlinks=False)                 # 링크는 실체로 복사된다
    editor = runtime / "apps" / "editor"
    shutil.copytree(app / "public", editor / "public", dirs_exist_ok=True)
    shutil.copytree(app / ".next" / "static", editor / ".next" / "static", dirs_exist_ok=True)

    modules = runtime / "node_modules"
    store = modules / ".bun" / "node_modules"
    if store.is_dir():
        # bun 은 패키지를 `.bun/node_modules` 에 두고 링크한다 — 평범한 node 해석이 찾도록 위로 올린다.
        for entry in sorted(store.iterdir()):
            for src in (sorted(entry.iterdir()) if entry.name.startswith("@") else [entry]):
                dst = modules / src.relative_to(store)
                if not dst.exists():
                    shutil.copytree(src, dst, symlinks=False)
        shutil.rmtree(modules / ".bun", ignore_errors=True)
    for name in ("sharp", "@img"):                                         # 이미지 최적화는 끈 빌드다
        shutil.rmtree(modules / name, ignore_errors=True)
    native = sorted(p.relative_to(runtime).as_posix() for p in runtime.rglob("*.node"))
    if native:
        raise SystemExit("동봉 런타임에 네이티브 모듈이 있다(다른 PC 에서 안 돈다):\n" + "\n".join(native))

    (out / "node").mkdir(parents=True, exist_ok=True)
    shutil.copy2(node, out / "node" / node.name)
    manifest = {"pascal_commit": R.pinned_commit(), "overlay_sha256": R.overlay_sha256(),
                "entrypoint": "runtime/apps/editor/server.js", "node": "node/" + node.name}
    (out / R.RUNTIME_MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    problems = R.runtime_problems(out)
    if problems:
        raise SystemExit("만든 런타임이 검사를 통과하지 못했다: " + " · ".join(problems))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Pascal 편집 화면 동봉 런타임 만들기")
    ap.add_argument("--pascal", required=True, help="고정 커밋 체크아웃(PASCAL_PORTABLE_BUILD=1 로 빌드한 것)")
    ap.add_argument("--out", default=str(HERE.parent / "pascal_runtime"))
    ap.add_argument("--node", default=shutil.which("node"), help="동봉할 node 실행 파일")
    a = ap.parse_args(argv)
    if not a.node:
        raise SystemExit("node 실행 파일을 찾지 못했다 — --node 로 줄 것")
    print("동봉 런타임: %s" % stage(a.pascal, a.out, a.node))


if __name__ == "__main__":
    main()
