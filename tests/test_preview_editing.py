"""Browser edit behavior runs against the same pure JavaScript shipped in preview.html."""
import os
import json
import re
import shutil
import subprocess
import tempfile

import preview
import stack_build


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_preview_edit_geometry_javascript():
    node = shutil.which("node")
    assert node, "Node.js is required for preview JavaScript behavior tests"
    test_path = os.path.join(ROOT, "tests", "preview_edit_geometry.test.js")
    result = subprocess.run(
        [node, test_path], cwd=ROOT, text=True, capture_output=True, timeout=20
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_generated_preview_carries_project_state_and_valid_javascript():
    runtime = {
        "base_url": "http://127.0.0.1:54321",
        "token": "secret",
        "project_id": "project-a",
        "revision": 4,
    }
    html = preview.build_html({
        "source": "plan.dxf",
        "elements": {"wall": []},
        "project_runtime": runtime,
        "project_edits": {"w:1": {"deleted": True}},
    })
    match = re.search(r'<script id="mep-data" type="application/json">(.*?)</script>', html, re.S)
    assert match, "generated preview did not contain its runtime data payload"
    payload = json.loads(match.group(1).replace("<\\/", "</"))
    assert payload["project_runtime"] == runtime
    assert payload["project_edits"] == {"w:1": {"deleted": True}}
    assert "/*__EDIT_HELPER__*/" not in html

    module = re.findall(r'<script id="mep-app">(.*?)</script>', html, re.S)[-1]
    fd, path = tempfile.mkstemp(suffix=".mjs")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(module)
        checked = subprocess.run(
            [shutil.which("node"), "--check", path], text=True, encoding='utf-8',
            capture_output=True, timeout=20,
        )
        assert checked.returncode == 0, checked.stdout + checked.stderr
    finally:
        os.unlink(path)


def test_javascript_manual_id_roundtrips_through_stack_floor_namespace():
    script = r"""
const E=require('./vendor/edit_geometry.js');
const rec=E.makeManualWall([[110,220],[310,220]],
  {level:'L2',z_base:3300,elevation:3300,attrs:{zone:'east'}},
  {width:200,height:2800},()=> 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee');
process.stdout.write(JSON.stringify({[rec.eid]:{added:true,category:'wall',record:rec}}));
"""
    result = subprocess.run(
        [shutil.which("node"), "-e", script], cwd=ROOT, text=True,
        encoding="utf-8", capture_output=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    world = json.loads(result.stdout)
    eid = "L2:wm:aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    assert list(world) == [eid]
    assert world[eid]["record"]["level"] == "L2"

    levels = [{"id": "L2", "z": 3300, "offset": [100, 200]}]
    local = stack_build.edits_to_local(world, levels)
    local_eid = "wm:aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    assert list(local["L2"]) == [local_eid]
    assert "level" not in local["L2"][local_eid]["record"]
    assert local["L2"][local_eid]["record"]["centerline"] == [[10, 20], [210, 20]]
    assert stack_build.edits_to_world(local, levels) == world
