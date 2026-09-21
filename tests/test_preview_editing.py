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
    suggestion = {"code": "thin_pair", "row_layer": "A-CON", "pattern": "^A-CON$",
                  "op": "set_opts", "category": "wall", "opts": {"pair_min": 83}}
    boq = {"벽": [["두께", "개수"], [["T200", 1]], ["합계", 1]]}
    html = preview.build_html({
        "source": "plan.dxf",
        "elements": {"wall": []},
        "project_runtime": runtime,
        "project_edits": {"w:1": {"deleted": True}},
        "suggestions_apply": [suggestion],
        "boq": boq,
        "level_height_overrode": 0,
    })
    match = re.search(r'<script id="mep-data" type="application/json">(.*?)</script>', html, re.S)
    assert match, "generated preview did not contain its runtime data payload"
    payload = json.loads(match.group(1).replace("<\\/", "</"))
    assert payload["project_runtime"] == runtime
    assert payload["project_edits"] == {"w:1": {"deleted": True}}
    # 검토 목록의 [적용] 버튼이 읽는 값이다 — build_html 의 명시적 허용목록에서 빠지면 서버 응답에는
    # 있어도 화면에는 안 뜬다(브라우저로 실제로 겪은 버그).
    assert payload["suggestions_apply"] == [suggestion]
    # Phase 4(물량 요약 패널)의 같은 허용목록 함정 — boq 와, 층고가 "선언"인지 판정하는
    # level_height_declared(키 존재만 보고, 값 0도 선언이다 — 아래서 False 가 아니라 True 를 확인한다).
    assert payload["boq"] == boq
    assert payload["level_height_declared"] is True
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


def test_undeclared_level_height_reaches_the_page_as_false_not_null():
    """`level_height_overrode` 가 아예 없으면(층고를 안 물은 프로젝트) 선언 아님 — null 이 아니라 False 다.
    (`data.get(...)` 로 값을 그대로 옮기면 '선언 0건'과 '미선언'이 둘 다 null 이 되어 구분이 안 된다.)"""
    html = preview.build_html({"source": "plan.dxf", "elements": {"wall": []}})
    match = re.search(r'<script id="mep-data" type="application/json">(.*?)</script>', html, re.S)
    payload = json.loads(match.group(1).replace("<\\/", "</"))
    assert payload["level_height_declared"] is False
    assert payload["boq"] == {}


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
