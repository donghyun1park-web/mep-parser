"""Start one GCI acceptance worker as a detached Windows background process."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


STUDY_PATTERN = re.compile(r"gci-[0-9a-f]{12}")


def start_worker(root: Path, study: str, stdout_path: Path, stderr_path: Path):
    if os.name != "nt":
        raise RuntimeError("이 실행 도우미는 Windows 전용입니다.")
    if not STUDY_PATTERN.fullmatch(study):
        raise ValueError("GCI 작업 ID 형식이 올바르지 않습니다.")
    repo = Path(__file__).resolve().parents[1]
    root = root.expanduser().resolve()
    manifest = root / "_body_gci" / study / "gci_job.json"
    if not manifest.is_file():
        raise FileNotFoundError(f"GCI 작업을 찾을 수 없습니다: {manifest}")
    stdout_path = stdout_path.expanduser().resolve()
    stderr_path = stderr_path.expanduser().resolve()
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-u",
        str(repo / "run_gci_acceptance.py"),
        "--root",
        str(root),
        "--study",
        study,
    ]
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    environment = dict(os.environ, PYTHONUNBUFFERED="1")
    with (
        open(stdout_path, "a", encoding="utf-8", buffering=1) as stdout,
        open(stderr_path, "a", encoding="utf-8", buffering=1) as stderr,
    ):
        process = subprocess.Popen(
            command,
            cwd=repo,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            env=environment,
            creationflags=flags,
            close_fds=True,
        )
    return {
        "ok": True,
        "pid": process.pid,
        "study": study,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="검증된 GCI 작업을 분리된 Windows 백그라운드에서 재개합니다."
    )
    parser.add_argument("--root", default="cfd_projects")
    parser.add_argument("--study", required=True)
    parser.add_argument("--stdout", required=True)
    parser.add_argument("--stderr", required=True)
    args = parser.parse_args(argv)
    try:
        result = start_worker(
            Path(args.root), args.study, Path(args.stdout), Path(args.stderr)
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
