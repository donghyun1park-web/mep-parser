# -*- coding: utf-8 -*-
"""동봉 런타임 — 현장 PC 가 npm 없이 띄우는 편집 화면이 **지금 저장소의** 편집 화면인가.

다른 오버레이로 만든 런타임을 띄우면 무엇이 저장되는지 아무도 모른다(체크아웃 모드의 고정 커밋·오버레이
대조와 같은 이유). standalone 서버는 HOSTNAME 이 없으면 모든 인터페이스에 붙는다 — 127.0.0.1 을 준다.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pascal_host"))
import run_host as R  # noqa: E402


def _runtime(tmp_path, **override):
    (tmp_path / "runtime" / "apps" / "editor").mkdir(parents=True)
    (tmp_path / "runtime" / "apps" / "editor" / "server.js").write_text("// server", encoding="utf-8")
    (tmp_path / "node").mkdir()
    (tmp_path / "node" / "node.exe").write_bytes(b"MZ")
    manifest = {"pascal_commit": R.pinned_commit(), "overlay_sha256": R.overlay_sha256(),
                "entrypoint": "runtime/apps/editor/server.js", "node": "node/node.exe"}
    manifest.update(override)
    (tmp_path / R.RUNTIME_MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path


def test_a_runtime_built_from_this_repository_starts_on_loopback_only(tmp_path):
    runtime = _runtime(tmp_path)
    assert R.runtime_problems(runtime) == []
    cmd, cwd, env = R.runtime_command(runtime, 3002)
    assert cmd == [str(runtime / "node" / "node.exe"), str(runtime / "runtime" / "apps" / "editor" / "server.js")]
    assert cwd == runtime / "runtime" / "apps" / "editor"
    assert env == {"HOSTNAME": "127.0.0.1", "PORT": "3002"}


def test_a_relative_runtime_path_still_launches_the_right_files(tmp_path, monkeypatch):
    """`run_editor.bat` 는 `--runtime pascal_runtime`(상대 경로)을 준다. 서버는 server.js 옆에서 돌므로
    상대 경로를 그대로 넘기면 node 가 엉뚱한 곳을 찾는다(실측 MODULE_NOT_FOUND)."""
    _runtime(tmp_path / "pascal_runtime")
    monkeypatch.chdir(tmp_path)
    cmd, cwd, _env = R.runtime_command("pascal_runtime", 3002)
    assert all(os.path.isabs(p) and os.path.isfile(p) for p in cmd) and os.path.isabs(cwd)


def test_a_stale_or_foreign_runtime_is_refused(tmp_path):
    assert R.runtime_problems(tmp_path) and "mep-runtime.json" in R.runtime_problems(tmp_path)[0]
    stale = _runtime(tmp_path / "a", overlay_sha256="0" * 64)
    assert any("오버레이" in p for p in R.runtime_problems(stale))
    foreign = _runtime(tmp_path / "b", pascal_commit="deadbeef")
    assert any("고정 커밋" in p for p in R.runtime_problems(foreign))
    broken = _runtime(tmp_path / "c", node="node/missing.exe")
    assert any("node" in p for p in R.runtime_problems(broken))


def test_overlay_fingerprint_changes_with_any_overlay_file(tmp_path, monkeypatch):
    overlay = tmp_path / "overlay"
    (overlay / "apps").mkdir(parents=True)
    (overlay / "apps" / "a.ts").write_text("export const a = 1\n", encoding="utf-8")
    monkeypatch.setattr(R, "OVERLAY", overlay)
    first = R.overlay_sha256()
    (overlay / "apps" / "a.ts").write_text("export const a = 2\n", encoding="utf-8")
    assert R.overlay_sha256() != first
