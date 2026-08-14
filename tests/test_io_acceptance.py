from pathlib import Path


def test_probe_path_verifies_read_create_replace_delete(tmp_path):
    from scripts.io_acceptance import probe_path

    result = probe_path(tmp_path / "result-root")

    assert result["status"] == "PASS"
    assert result["read"] is True
    assert result["create"] is True
    assert result["replace"] is True
    assert result["delete"] is True


def test_run_io_acceptance_creates_and_checks_required_roots(tmp_path):
    from scripts.io_acceptance import REQUIRED_ROOTS, run_io_acceptance

    result = run_io_acceptance(tmp_path / "cfd_projects")

    assert result["contract"] == "io_acceptance.v1"
    assert result["status"] == "PASS"
    assert set(result["roots"]) == set(REQUIRED_ROOTS)
    assert all(item["status"] == "PASS" for item in result["probes"])


def test_probe_path_fails_closed_with_stable_code_when_target_is_a_file(tmp_path):
    from scripts.io_acceptance import probe_path

    target = tmp_path / "not-a-directory"
    target.write_text("user data", encoding="utf-8")

    result = probe_path(target)

    assert result["status"] == "BLOCKED"
    assert result["error_code"] == "IO_TARGET_NOT_DIRECTORY"
