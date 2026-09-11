# -*- coding: utf-8 -*-
"""Pascal 편집 화면 ↔ MEP-Parser 프로젝트 저장소를 함께 띄운다(3단계 첫 조각).

    python pascal_host/run_host.py <도면.dxf | 프로젝트.mep 폴더> --pascal <Pascal 체크아웃>
        [--sync-overlay] [--build] [--port 3002]

1. 프로젝트 저장소(`ProjectSession`)를 열고 `ProjectServer` 를 127.0.0.1 임의 포트에 띄운다.
2. **고정 커밋**(`PASCAL_COMMIT`)의 Pascal 을 127.0.0.1 에만 띄우고, `MEP_PROJECT_URL`/
   `MEP_PROJECT_TOKEN` 을 그 프로세스 환경에만 준다. 브라우저는 Pascal 의 서버 측
   프록시(`/api/mep/*`)만 부르므로 토큰이 브라우저로 가지 않는다.
3. 편집 화면: http://127.0.0.1:<port>/mep

저장 기준은 **우리 저장소 하나**다. Pascal 은 편집 화면일 뿐이고, 저장은 `/pascal/apply`
(변경 명령 · revision · 스냅샷 해시 · 작업 ID)를 거친다. Pascal 의 씬 DB 에는 쓰지 않는다.

오버레이(`pascal_host/overlay/`)는 **우리 코드**다. 체크아웃에 복사해 빌드한다 — 체크아웃이
고정 커밋이 아니거나 오버레이가 어긋나 있으면 띄우지 않는다(다른 코드가 도는 것을 막는다).
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OVERLAY = HERE / "overlay"
sys.path.insert(0, str(HERE.parent))


def pinned_commit():
    return (HERE / "PASCAL_COMMIT").read_text(encoding="utf-8").strip()


def overlay_files():
    return sorted(p.relative_to(OVERLAY) for p in OVERLAY.rglob("*") if p.is_file())


def overlay_drift(checkout):
    """(없는 파일, 다른 파일). 둘 다 비어야 이 실행기가 가리키는 코드가 돈다."""
    missing, changed = [], []
    for rel in overlay_files():
        dst = Path(checkout) / rel
        if not dst.exists():
            missing.append(str(rel))
        elif dst.read_bytes() != (OVERLAY / rel).read_bytes():
            changed.append(str(rel))
    return missing, changed


def sync_overlay(checkout):
    for rel in overlay_files():
        dst = Path(checkout) / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(OVERLAY / rel, dst)


def checkout_commit(checkout):
    return subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def open_session(target):
    from project_server import ProjectSession, open_source_project
    from project_store import ProjectStore
    target = Path(target)
    if target.is_dir():
        return ProjectSession(ProjectStore(target))
    return open_source_project(str(target))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Pascal 편집 화면 + MEP-Parser 프로젝트 저장소")
    ap.add_argument("target", help="도면 .dxf 또는 기존 프로젝트(.mep) 폴더")
    ap.add_argument("--pascal", required=True, help="고정 커밋의 Pascal 체크아웃(bun install 끝난 것)")
    ap.add_argument("--port", type=int, default=3002)
    ap.add_argument("--sync-overlay", action="store_true", help="오버레이를 체크아웃에 복사")
    ap.add_argument("--build", action="store_true", help="apps/editor 를 next build")
    a = ap.parse_args(argv)

    checkout = Path(a.pascal).resolve()
    app = checkout / "apps" / "editor"
    head, pin = checkout_commit(checkout), pinned_commit()
    if head != pin:
        sys.exit("Pascal 체크아웃이 고정 커밋이 아니다: %s ≠ %s" % (head or "(git 아님)", pin))
    if a.sync_overlay:
        sync_overlay(checkout)
    missing, changed = overlay_drift(checkout)
    if missing or changed:
        sys.exit("오버레이가 체크아웃과 다르다 — --sync-overlay --build 로 맞출 것. "
                 "없음 %s · 다름 %s" % (missing, changed))

    node = shutil.which("node")
    if not node:
        sys.exit("node 가 PATH 에 없다")
    next_bin = subprocess.check_output([node, "-p", "require.resolve('next/dist/bin/next')"],
                                       cwd=app, text=True).strip()
    if a.build:
        subprocess.run([node, next_bin, "build"], cwd=app, check=True)

    session = open_session(a.target)
    server = session.serve()
    env = dict(os.environ, MEP_PROJECT_URL=server.base_url, MEP_PROJECT_TOKEN=server.token)
    # 127.0.0.1 에만 바인딩한다 — 프록시는 토큰을 대신 붙이므로 LAN 에 열리면
    # 인증 없는 쓰기 경로가 된다(오버레이 라우트의 루프백 가드는 두 번째 방어선).
    web = subprocess.Popen([node, next_bin, "start", "-H", "127.0.0.1", "-p", str(a.port)],
                           cwd=app, env=env)
    print("편집 화면: http://127.0.0.1:%d/mep" % a.port, flush=True)
    try:
        web.wait()
    except KeyboardInterrupt:
        pass
    finally:
        web.terminate()
        server.close()


if __name__ == "__main__":
    main()
