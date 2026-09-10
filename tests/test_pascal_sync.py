# -*- coding: utf-8 -*-
"""Pascal 편집 화면 ↔ 프로젝트 저장소 — 스냅샷 조회·변경 적용.

저장은 전체 JSON 되돌리기가 아니라 **원본과 대조한 변경 명령**이고, 기존
`ProjectSession.save` 의 revision 검사·검증·잠금을 그대로 탄다. 여기서 고정하는
것은 그 위에 얹은 세 가지다: 스냅샷 지문(낡은 씬 차단), 작업 ID(중복 적용 차단),
dry-run(저장 없이 검증).
"""
import json
import os
import sys
import urllib.error
import urllib.request

import ezdxf
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from project_store import ProjectStore, RevisionConflict  # noqa: E402
import pascal_bridge as PB  # noqa: E402


def _session(tmp_path):
    from project_server import ProjectSession
    dxf = tmp_path / "drawing.dxf"
    doc = ezdxf.new(units=4)
    doc.layers.new("WALL")
    for y in (0, 200):                       # 양면 2선 → 두께 200 벽 하나
        doc.modelspace().add_line((0, y), (5000, y), dxfattribs={"layer": "WALL"})
    doc.saveas(dxf)
    store = ProjectStore(tmp_path / "drawing.mep").create(sources=[{"id": "main", "path": str(dxf)}])
    return ProjectSession(store)


def _walls(scene):
    return [n for n in scene["nodes"].values() if n["type"] == "wall"]


def test_dry_run_validates_then_apply_commits_once(tmp_path):
    """dry-run 은 검증만 하고 revision 을 올리지 않는다. 적용은 한 번 커밋하고,
    같은 작업 ID 로 다시 오면 **다시 쌓지 않는다**(재시도·이중 클릭)."""
    sess = _session(tmp_path)
    snap = sess.pascal_snapshot()
    _walls(snap["scene"])[0]["thickness"] = 0.45          # 두께만 고쳤다
    args = (snap["scene"], snap["revision"], snap["project_id"], snap["snapshot_sha256"], "op-1")

    dry = sess.pascal_apply(*args, dry_run=True)
    assert dry["dry_run"] is True and dry["applied"] is False
    assert list(dry["commands"].values()) == [{"overrides": {"width": 450.0}}]
    assert sess.state()["revision"] == snap["revision"]  # 저장 안 됨

    done = sess.pascal_apply(*args)
    assert done["applied"] is True and done["state"]["revision"] == snap["revision"] + 1
    wall = done["state"]["geometry"]["elements"]["wall"][0]
    assert wall["overrides"]["width"] == 450.0

    again = sess.pascal_apply(*args)                       # 낡은 revision 이지만 중복이다
    assert again["duplicate"] is True
    assert sess.state()["revision"] == snap["revision"] + 1

    from project_server import ProjectSession
    reopened = ProjectSession(ProjectStore(sess.store.folder)).state()   # 종료 → 재열기
    assert reopened["geometry"]["elements"]["wall"][0]["overrides"]["width"] == 450.0
    decisions = sess.store.read()["decisions"]
    assert [d["op_id"] for d in decisions if d.get("action") == "pascal_apply"] == ["op-1"]


def test_stale_revision_and_stale_snapshot_are_refused(tmp_path):
    sess = _session(tmp_path)
    snap = sess.pascal_snapshot()
    _walls(snap["scene"])[0]["thickness"] = 0.45
    with pytest.raises(RevisionConflict):                  # 누가 먼저 저장했다
        sess.pascal_apply(snap["scene"], snap["revision"] + 7, snap["project_id"],
                          snap["snapshot_sha256"], "op-a")
    from project_server import SnapshotConflict
    with pytest.raises(SnapshotConflict):                  # 다른 씬을 보고 고쳤다
        sess.pascal_apply(snap["scene"], snap["revision"], snap["project_id"],
                          "0" * 64, "op-b")
    assert sess.state()["revision"] == snap["revision"]


def test_an_untouched_scene_changes_nothing(tmp_path):
    """아무것도 안 고쳤으면 revision 도 오르지 않는다 — 빈 커밋이 이력을 흐린다."""
    sess = _session(tmp_path)
    snap = sess.pascal_snapshot()
    res = sess.pascal_apply(snap["scene"], snap["revision"], snap["project_id"],
                            snap["snapshot_sha256"], "op-noop")
    assert res["applied"] is False and res["reason"] == "no_changes"
    assert sess.state()["revision"] == snap["revision"]


def test_deleting_a_manual_record_drops_its_edit():
    """사람이 만든 레코드는 그 수정에서만 나온다 — 지우면 수정 자체가 없어진다."""
    existing = {"wm:1": {"added": True, "category": "wall", "record": {"kind": "polyline"}},
                "w:2": {"overrides": {"height": 3000.0}}}
    merged = PB.merge_edits(existing, {"wm:1": {"deleted": True},
                                       "w:2": {"overrides": {"width": 450.0}}})
    assert "wm:1" not in merged
    assert merged["w:2"]["overrides"] == {"height": 3000.0, "width": 450.0}   # 다른 선언은 남는다


def test_http_and_mcp_share_the_same_path(tmp_path):
    """HTTP 가 같은 함수를 부르는지 끝까지 — 스냅샷 → 적용 → 낡은 재적용은 409."""
    sess = _session(tmp_path)
    with sess.serve() as server:
        def call(path, payload=None):
            headers = {"Authorization": "Bearer " + server.token}
            if payload is not None:
                headers["Content-Type"] = "application/json"
            req = urllib.request.Request(server.base_url + path, headers=headers,
                                         data=None if payload is None else json.dumps(payload).encode())
            with urllib.request.urlopen(req) as r:
                return json.load(r)

        snap = call("/pascal/snapshot")
        _walls(snap["scene"])[0]["thickness"] = 0.3
        body = {"scene": snap["scene"], "expected_revision": snap["revision"],
                "project_id": snap["project_id"], "snapshot_sha256": snap["snapshot_sha256"],
                "op_id": "http-1"}
        res = call("/pascal/apply", body)
        assert res["applied"] is True and res["state"]["revision"] == snap["revision"] + 1

        body["op_id"] = "http-2"                            # 새 작업인데 낡은 revision
        with pytest.raises(urllib.error.HTTPError) as err:
            call("/pascal/apply", body)
        assert err.value.code == 409
