import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


CHECK_IDS = (
    "code_baseline",
    "filesystem_io",
    "serial_environment",
    "working_room_e2e",
    "real_dxf_screening",
    "restart_integrity",
    "exact_heat_verification",
    "limited_numerical_spotchecks",
)


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_sources(root, check_ids=CHECK_IDS, *, stale=False):
    evidence_dir = root / "_release_evidence" / "working_validation"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    checks = {}
    stamp = "2000-01-01T00:00:00Z" if stale else datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for check_id in check_ids:
        source = evidence_dir / f"{check_id}.source.json"
        source.write_text(json.dumps({"contract": "recovered_evidence.v1", "created_at": stamp, "artifact": check_id}), encoding="utf-8")
        checks[check_id] = {"path": str(source.relative_to(root)).replace("\\", "/"), "sha256": _sha256(source)}
    manifest = {"contract": "working_validation.sources.v1", "checks": checks}
    (evidence_dir / "sources.json").write_text(json.dumps(manifest), encoding="utf-8")
    return evidence_dir / "sources.json", checks


def test_fake_pass_cannot_promote_working_status(tmp_path):
    from working_validation import evaluate_working_validation

    result = evaluate_working_validation(tmp_path)

    assert result["status"] == "BLOCKED"
    assert result["working_ready_on_target"] is False
    assert result["design_citable"] is False
    assert result["release_ready"] is False


def test_first_six_rehashed_sources_produce_working_status(tmp_path):
    from working_validation import evaluate_working_validation

    _write_sources(tmp_path, CHECK_IDS[:6])

    result = evaluate_working_validation(tmp_path)

    assert result["status"] == "WORKING_SINGLE_PC"
    assert result["working_ready_on_target"] is True
    assert result["limited_numerical_spotchecks_pass_on_target"] is False
    assert [row["id"] for row in result["checks"]] == list(CHECK_IDS)
    assert [row["id"] for row in result["numerical_blockers"]] == list(CHECK_IDS[6:])


def test_all_eight_rehashed_sources_produce_numerical_spotcheck_status(tmp_path):
    from working_validation import evaluate_working_validation

    _write_sources(tmp_path)

    result = evaluate_working_validation(tmp_path)

    assert result["status"] == "NUMERICAL_SPOTCHECK_PASS_SINGLE_PC"
    assert result["working_ready_on_target"] is True
    assert result["limited_numerical_spotchecks_pass_on_target"] is True
    assert result["design_citable"] is False
    assert result["release_ready"] is False


def test_forged_or_stale_or_missing_source_blocks_working_status(tmp_path):
    from working_validation import evaluate_working_validation

    sources, checks = _write_sources(tmp_path, CHECK_IDS[:6])
    source = tmp_path / checks["code_baseline"]["path"]
    source.write_text("forged", encoding="utf-8")
    assert evaluate_working_validation(tmp_path)["status"] == "BLOCKED"

    _write_sources(tmp_path, CHECK_IDS[:6], stale=True)
    assert evaluate_working_validation(tmp_path)["status"] == "BLOCKED"

    sources.unlink()
    assert evaluate_working_validation(tmp_path)["status"] == "BLOCKED"


def test_evidence_requires_exact_check_ids_and_excludes_own_output(tmp_path):
    from working_validation import evaluate_working_validation, write_working_validation

    sources, checks = _write_sources(tmp_path, CHECK_IDS[:6])
    payload = json.loads(sources.read_text(encoding="utf-8"))
    payload["checks"]["unexpected"] = payload["checks"].pop("code_baseline")
    sources.write_text(json.dumps(payload), encoding="utf-8")
    assert evaluate_working_validation(tmp_path)["status"] == "BLOCKED"

    sources, checks = _write_sources(tmp_path, CHECK_IDS[:6])
    output = tmp_path / "_release_evidence" / "working_validation-run-1.json"
    result = write_working_validation(tmp_path, output)
    assert output.is_file()
    assert str(output.resolve()) not in result["evidence_sha256"]
    assert str(sources.resolve()) not in result["evidence_sha256"]


def test_compare_runs_is_canonical_for_time_and_output_but_detects_evidence_changes(tmp_path):
    from working_validation import compare_working_validation_runs, write_working_validation

    _write_sources(tmp_path, CHECK_IDS[:6])
    first = tmp_path / "working_validation-run-one.json"
    second = tmp_path / "working_validation-run-two.json"
    write_working_validation(tmp_path, first)
    write_working_validation(tmp_path, second)

    equal = compare_working_validation_runs(first, second)
    assert equal["equal"] is True
    assert equal["differences"] == []

    second_payload = json.loads(second.read_text(encoding="utf-8"))
    second_payload["evidence_sha256"]["tampered"] = "0" * 64
    second.write_text(json.dumps(second_payload), encoding="utf-8")
    assert compare_working_validation_runs(first, second)["equal"] is False
