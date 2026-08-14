from pathlib import Path


def _write_authoritative_case(projects_root):
    case = projects_root / "_body_solver" / "recovered-case"
    for relative in (
        "log.checkMesh", "log.solver", "1/T", "1/U", "1/phi", "1/V",
        "mesh_manifest.json", "run_manifest.json", "result_manifest.json",
        "results/recovered.vtu", "reports/recovered.html",
    ):
        path = case / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
    return case


def test_probe_path_verifies_read_create_replace_delete(tmp_path):
    from scripts.io_acceptance import probe_path

    result = probe_path(tmp_path / "result-root")

    assert result["status"] == "PASS"
    assert result["read"] is True
    assert result["create"] is True
    assert result["replace"] is True
    assert result["delete"] is True


def test_run_io_acceptance_requires_and_rehashes_actual_recovered_artifacts(tmp_path):
    from scripts.io_acceptance import REQUIRED_ROOTS, run_io_acceptance

    projects_root = tmp_path / "cfd_projects"
    _write_authoritative_case(projects_root)
    result = run_io_acceptance(projects_root)

    assert result["contract"] == "io_acceptance.v1"
    assert result["status"] == "PASS"
    assert set(result["roots"]) == set(REQUIRED_ROOTS)
    assert all(item["status"] == "PASS" for item in result["probes"])
    assert len(result["artifact_probes"]) == 11
    assert all(len(item["sha256"]) == 64 and item["read"] for item in result["artifact_probes"])


def test_run_io_acceptance_blocks_missing_recovered_artifact(tmp_path):
    from scripts.io_acceptance import run_io_acceptance

    projects_root = tmp_path / "cfd_projects"
    case = _write_authoritative_case(projects_root)
    (case / "1" / "V").unlink()

    result = run_io_acceptance(projects_root)

    assert result["status"] == "BLOCKED"
    assert any(item["error_code"] == "ARTIFACT_MISSING:latest_V" for item in result["artifact_probes"])


def test_probe_path_fails_closed_with_stable_code_when_target_is_a_file(tmp_path):
    from scripts.io_acceptance import probe_path

    target = tmp_path / "not-a-directory"
    target.write_text("user data", encoding="utf-8")

    result = probe_path(target)

    assert result["status"] == "BLOCKED"
    assert result["error_code"] == "IO_TARGET_NOT_DIRECTORY"
