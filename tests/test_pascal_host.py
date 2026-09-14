# -*- coding: utf-8 -*-
"""Pascal 편집 화면 실행기(`pascal_host/run_host.py`)와 오버레이.

실행기는 **고정 커밋 + 우리 오버레이**가 아니면 띄우지 않는다. 다른 코드가 도는
편집 화면으로 저장하면 무엇이 저장됐는지 아무도 모른다.
"""
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import unittest

import geom_contract as GC

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "run_host", os.path.join(ROOT, "pascal_host", "run_host.py"))
run_host = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_host)


def test_the_pascal_commit_is_pinned_by_full_sha():
    assert re.fullmatch(r"[0-9a-f]{40}", run_host.pinned_commit())


def test_overlay_drift_is_detected_until_synced(tmp_path):
    files = [str(p) for p in run_host.overlay_files()]
    assert files, "오버레이가 비었다"
    assert run_host.overlay_drift(tmp_path) == (files, [])
    run_host.sync_overlay(tmp_path)
    assert run_host.overlay_drift(tmp_path) == ([], [])
    (tmp_path / files[0]).write_text("다른 코드", encoding="utf-8")
    assert run_host.overlay_drift(tmp_path) == ([], [files[0]])


def test_the_project_token_never_reaches_the_browser():
    """토큰은 Next 서버 프로세스 환경에만 있어야 한다. 클라이언트 번들에 들어가는
    파일('use client')이 읽거나 `NEXT_PUBLIC_` 로 노출하면 브라우저가 저장소 쓰기
    권한을 갖게 된다."""
    for rel in run_host.overlay_files():
        text = (run_host.OVERLAY / rel).read_text(encoding="utf-8")
        assert "NEXT_PUBLIC_MEP" not in text, rel
        if text.lstrip().startswith("'use client'"):
            assert "MEP_PROJECT_TOKEN" not in text and "MEP_PROJECT_URL" not in text, rel


def test_the_save_status_is_kept_out_of_browser_translation():
    """Pascal 화면이 영어라 브라우저가 자동 번역을 켠다. 번역기가 텍스트 노드를
    `<font>` 로 갈아 끼우면 React 가 바꾼 revision 이 **화면에 안 나타난다** —
    실측: 저장소는 r3 인데 표시는 r2 에 멈춰 있었다. 저장 상태는 사람이 '저장됐나'를
    판단하는 유일한 표시라 번역 대상에서 뺀다(`translate="no"`)."""
    text = (run_host.OVERLAY / "apps" / "editor" / "components" /
            "mep-project-loader.tsx").read_text(encoding="utf-8")
    status = text[text.index('data-testid="mep-status"') - 600: text.index('data-testid="mep-status"') + 200]
    assert 'translate="no"' in status
    assert "data-revision" in status          # 번역과 무관하게 읽을 수 있는 revision


def test_plugin_section_rings_match_the_python_contract_under_the_axis_swap(tmp_path):
    """Pascal 화면의 사각 덕트와 FreeCAD·Blender 의 사각 덕트가 **같은 링**이어야 한다.

    플러그인은 규약을 TS 로 옮긴 사본(`section.ts`)을 쓴다. 다리가 (x,y,z) → (x,z,y) 로 축을
    맞바꾸므로 파이썬 링을 맞바꿔 m 로 바꾼 것과 좌표가 같아야 한다 — 경사·roll·급꺾임
    토막·닫힌 고리까지. 한쪽만 고치면 화면과 납품 모델의 단면 방향이 조용히 갈린다."""
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("Node.js unavailable")
    t = math.radians(118)
    p2 = [1000 + 188 * math.cos(t), 188 * math.sin(t), 2400]
    routes = [
        ([[0, 0, 2400], [2000, 0, 2400], [3000, 1000, 2700]], 204.0, 60.0, 0.0),
        ([[0, 0, 1000], [3000, 0, 1000], [3000, 0, 2500]], 400.0, 100.0, 0.7),
        ([[0, 0, 2400], [1000, 0, 2400], p2,
          [p2[0] + 1000 * math.cos(2 * t), p2[1] + 1000 * math.sin(2 * t), 2400]], 200.0, 100.0, 0.0),
        ([[0, 0, 0], [1000, 0, 0], [1000, 1000, 0], [0, 1000, 0], [0, 0, 0]], 100.0, 60.0, 0.0),
    ]
    swap = lambda p: [p[0] / 1000.0, p[2] / 1000.0, p[1] / 1000.0]     # 우리 mm Z-up → Pascal m Y-up
    expected = [[[[swap(c) for c in ring] for ring in part] for part in GC.rect_parts(pts, w, h, roll)]
                for pts, w, h, roll in routes]
    inputs = [[[swap(p) for p in pts], w / 1000.0, h / 1000.0, roll] for pts, w, h, roll in routes]
    section = (run_host.OVERLAY / "apps" / "editor" / "lib" / "mep-plugin" / "section.ts").as_uri()
    harness = tmp_path / "parity.mjs"
    harness.write_text(
        "import { rectParts } from %s;\n" % json.dumps(section)
        + "const inputs = %s;\n" % json.dumps(inputs)
        + "console.log(JSON.stringify(inputs.map(([p, w, h, r]) => rectParts(p, w, h, r))));\n",
        encoding="utf-8")
    run = subprocess.run([node, str(harness)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    got = json.loads(run.stdout.strip().splitlines()[-1])
    assert [len(parts) for parts in got] == [len(parts) for parts in expected] == [1, 1, 3, 1]
    for parts_g, parts_e in zip(got, expected):
        for rings_g, rings_e in zip(parts_g, parts_e):
            assert len(rings_g) == len(rings_e)
            for ring_g, ring_e in zip(rings_g, rings_e):
                assert all(math.dist(a, b) < 1e-9 for a, b in zip(ring_g, ring_e))
