import json
import re
import hashlib
import sys
from pathlib import Path


def test_build_vv_baseline_records_identity_and_hash_inventory(tmp_path):
    from vv_baseline import build_vv_baseline, write_vv_baseline

    repo_root = Path(__file__).resolve().parents[1]
    projects_root = tmp_path / "cfd_projects"
    payload = build_vv_baseline(repo_root, projects_root)

    assert re.fullmatch(r"baseline-[0-9TZ-]+-[0-9a-f]{12}", payload["candidate_id"])
    assert payload["git_head"]
    assert isinstance(payload["dirty_paths"], list)
    assert set(payload["dirty_path_hashes"]) == set(payload["dirty_paths"])
    assert len(payload["dependency_snapshot_sha256"]) == 64
    assert payload["python_executable_sha256"] == hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
    assert payload["python_architecture"]
    assert len(payload["installed_distribution_snapshot_sha256"]) == 64
    assert payload["schema_hashes"]
    assert payload["benchmark_hashes"]
    assert payload["test_summary"]["status"] == "NOT_RUN"
    assert isinstance(payload["runtime_skips"], list)

    output = projects_root / "_release_evidence" / "vv" / "candidate" / "vv_baseline.json"
    written = write_vv_baseline(payload, output)
    assert written == output
    assert json.loads(output.read_text(encoding="utf-8"))["candidate_id"] == payload["candidate_id"]
    assert not output.with_suffix(".tmp").exists()


def test_build_vv_baseline_recomputes_junit_summary_and_runtime_skips(tmp_path):
    from vv_baseline import build_vv_baseline

    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuite tests="2" failures="0" errors="0" skipped="1">'
        '<testcase classname="a" name="ok" />'
        '<testcase classname="b" name="env"><skipped message="FreeCAD missing" /></testcase>'
        '</testsuite>',
        encoding="utf-8",
    )
    payload = build_vv_baseline(Path(__file__).resolve().parents[1], tmp_path / "projects", junit)

    assert payload["test_summary"]["status"] == "PASS"
    assert payload["test_summary"]["passed"] == 1
    assert payload["runtime_skips"][0]["test"] == "b.env"


def test_build_vv_baseline_aggregates_pytest_testsuites_root(tmp_path):
    from vv_baseline import build_vv_baseline

    junit = tmp_path / "pytest-junit.xml"
    junit.write_text(
        '<testsuites name="pytest tests">'
        '<testsuite name="unit" tests="2" failures="1" errors="0" skipped="0">'
        '<testcase classname="a" name="ok" />'
        '<testcase classname="a" name="bad"><failure message="boom" /></testcase>'
        '</testsuite>'
        '<testsuite name="env" tests="1" failures="0" errors="0" skipped="1">'
        '<testcase classname="b" name="skip"><skipped message="FreeCAD missing" /></testcase>'
        '</testsuite>'
        '</testsuites>',
        encoding="utf-8",
    )
    payload = build_vv_baseline(Path(__file__).resolve().parents[1], tmp_path / "projects", junit)

    assert payload["test_summary"]["status"] == "FAIL"
    assert payload["test_summary"]["tests"] == 3
    assert payload["test_summary"]["failed"] == 1
    assert payload["test_summary"]["skipped"] == 1
