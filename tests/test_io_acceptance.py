import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from jsonschema import validate


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _link(root, path, contract=None):
    value = {"path": str(path.relative_to(root)).replace("\\", "/"), "sha256": _sha256(path)}
    if contract:
        value["contract"] = contract
    return value


def _write_authoritative_case(projects_root, case_id="recovered-case"):
    case = projects_root / "_body_solver" / case_id
    for relative in (
        "log.checkMesh", "log.actual-solver", "1/T", "1/U", "1/phi", "1/V",
        "results/recovered.vtu", "reports/recovered.html",
    ):
        path = case / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
    mesh = _write_json(case / "mesh_manifest.json", {"contract": "mesh_manifest.v1", "status": "PASS"})
    run = _write_json(case / "run_manifest.json", {"contract": "run_manifest.v1", "status": "PASS", "requested_ranks": 1})
    result = _write_json(case / "result_manifest.json", {
        "contract": "result_manifest.v1",
        "source": _link(projects_root, case / "results/recovered.vtu"),
        "html": _link(projects_root, case / "reports/recovered.html"),
        "mesh_manifest_sha256": _sha256(mesh),
        "run_manifest_sha256": _sha256(run),
    })
    return case


def _write_inventory(projects_root, cases):
    rows = []
    for case in cases:
        rows.append({
            "case_id": case.name,
            "case_path": str(case.relative_to(projects_root)).replace("\\", "/"),
            "mesh_manifest": _link(projects_root, case / "mesh_manifest.json", "mesh_manifest.v1"),
            "run_manifest": _link(projects_root, case / "run_manifest.json", "run_manifest.v1"),
            "result_manifest": _link(projects_root, case / "result_manifest.json", "result_manifest.v1"),
            "check_mesh_log": _link(projects_root, case / "log.checkMesh"),
            "solver_log": _link(projects_root, case / "log.actual-solver"),
            "latest_time": "1",
            "fields": {field: _link(projects_root, case / "1" / field) for field in ("T", "U", "phi", "V")},
            "vtu": _link(projects_root, case / "results/recovered.vtu"),
            "html": _link(projects_root, case / "reports/recovered.html"),
        })
    path = projects_root / "_working_validation" / "evidence" / "authoritative_case_inventory.v1.json"
    return _write_json(path, {"contract": "io_authoritative_case_inventory.v1", "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "cases": rows})


def test_probe_path_verifies_read_create_replace_delete(tmp_path):
    from scripts.io_acceptance import probe_path

    result = probe_path(tmp_path / "result-root")

    assert result["status"] == "PASS"
    assert result["read"] is True
    assert result["create"] is True
    assert result["replace"] is True
    assert result["delete"] is True


def test_run_io_acceptance_follows_authoritative_inventory_and_manifest_links(tmp_path):
    from scripts.io_acceptance import REQUIRED_ROOTS, run_io_acceptance

    projects_root = tmp_path / "cfd_projects"
    case = _write_authoritative_case(projects_root)
    _write_inventory(projects_root, [case])
    # A stale glob candidate must not be mistaken for the selected solver log or result.
    (case / "log.aaa-stale").write_text("stale", encoding="utf-8")
    (case / "results" / "aaa-stale.vtu").write_text("stale", encoding="utf-8")

    result = run_io_acceptance(projects_root)

    assert result["contract"] == "io_acceptance.v1"
    assert result["status"] == "PASS"
    assert set(result["roots"]) == set(REQUIRED_ROOTS)
    assert all(item["status"] == "PASS" for item in result["probes"])
    assert len(result["artifact_probes"]) == 11
    assert all(len(item["sha256"]) == 64 and item["read"] for item in result["artifact_probes"])
    assert {item["path"] for item in result["artifact_probes"]} >= {str(case / "log.actual-solver"), str(case / "results/recovered.vtu")}
    assert str(case / "log.aaa-stale") not in {item["path"] for item in result["artifact_probes"]}

    schema = json.loads((Path(__file__).resolve().parents[1] / "io_acceptance.v1.schema.json").read_text(encoding="utf-8"))
    validate(result, schema)


def test_run_io_acceptance_requires_inventory_for_every_authoritative_case(tmp_path):
    from scripts.io_acceptance import run_io_acceptance

    projects_root = tmp_path / "cfd_projects"
    first = _write_authoritative_case(projects_root, "first")
    second = _write_authoritative_case(projects_root, "second")
    _write_inventory(projects_root, [first])

    result = run_io_acceptance(projects_root)

    assert result["status"] == "BLOCKED"
    assert any(item["error_code"] == "AUTHORITATIVE_CASE_INVENTORY_INCOMPLETE" for item in result["artifact_probes"])
    assert second.is_dir()


def test_run_io_acceptance_blocks_manifest_linkage_or_selected_artifact_mismatch(tmp_path):
    from scripts.io_acceptance import run_io_acceptance

    projects_root = tmp_path / "cfd_projects"
    case = _write_authoritative_case(projects_root)
    _write_inventory(projects_root, [case])
    (case / "1" / "V").unlink()

    result = run_io_acceptance(projects_root)

    assert result["status"] == "BLOCKED"
    assert any(item["error_code"] == "ARTIFACT_REHASH_MISMATCH:latest_V" for item in result["artifact_probes"])


def test_probe_path_fails_closed_with_stable_code_when_target_is_a_file(tmp_path):
    from scripts.io_acceptance import probe_path

    target = tmp_path / "not-a-directory"
    target.write_text("user data", encoding="utf-8")

    result = probe_path(target)

    assert result["status"] == "BLOCKED"
    assert result["error_code"] == "IO_TARGET_NOT_DIRECTORY"
