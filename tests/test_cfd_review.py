import contextlib
import hashlib
import json
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest import mock

from jsonschema import Draft202012Validator
import pytest

import cfd_case_health
import cfd_evidence
import cfd_review
from test_cfd_evidence import make_complete_case


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _future_evidence(tmp_path):
    paths = make_complete_case(tmp_path, with_gci=True)
    evidence = cfd_evidence.build_case_evidence(
        paths["case"], projects_root=paths["root"]
    )
    evidence.pop("legacy_case_ref")
    evidence["case_identity"] = {
        "contract": "case_identity.v1",
        "path": evidence["artifact_refs"]["geometry"]["path"],
        "sha256": evidence["artifact_refs"]["geometry"]["sha256"],
    }
    evidence["purpose"] = "design_review_candidate"
    evidence["status"] = "PASS"
    evidence["errors"] = []
    for check in evidence["checks"]:
        check.update(status="PASS", reason_codes=[], evidence_refs=[])
    _write(paths["evidence"], evidence)
    return paths


def _create(paths, *, decision="APPROVED", supersedes=(), target=None, output_dir=None):
    target = target or paths["evidence"]
    return cfd_review.create_review(
        target,
        projects_root=paths["root"],
        expected_target_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
        reviewer_id="reviewer-1",
        decision=decision,
        reason="reviewed current immutable evidence",
        output_dir=output_dir,
        supersedes_review_ids=supersedes,
    )


def test_create_review_binds_canonical_target_hash_and_uuid4_id(tmp_path):
    paths = _future_evidence(tmp_path)

    review = _create(paths)

    assert review["review_id"].startswith("review-")
    assert len(review["review_id"]) == 39
    assert review["review_id"][7:] == review["review_id"][7:].lower()
    int(review["review_id"][7:], 16)
    assert review["target"] == {
        "contract": "case_evidence.v1",
        "path": paths["evidence"].relative_to(paths["root"]).as_posix(),
        "sha256": hashlib.sha256(paths["evidence"].read_bytes()).hexdigest(),
    }
    review_path = paths["evidence"].parent / "_reviews" / f'{review["review_id"]}.case_review.v1.json'
    assert review_path.is_file()
    assert cfd_review.validate_review(review_path, projects_root=paths["root"]) == []
    schema = _read(Path(cfd_review.__file__).with_name("case_review.v1.schema.json"))
    Draft202012Validator(schema).validate(review)


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"reviewer_id": ""}, "reviewer"),
        ({"reason": ""}, "reason"),
        ({"decision": "PENDING"}, "decision"),
        ({"expected_target_sha256": "f" * 64}, "REVIEW_TARGET_CHANGED"),
    ],
)
def test_create_review_rejects_invalid_inputs(tmp_path, change, error):
    paths = _future_evidence(tmp_path)
    kwargs = {
        "projects_root": paths["root"],
        "expected_target_sha256": cfd_review.sha256_file(paths["evidence"]),
        "reviewer_id": "reviewer",
        "decision": "APPROVED",
        "reason": "reason",
    }
    kwargs.update(change)

    with pytest.raises(ValueError, match=error):
        cfd_review.create_review(paths["evidence"], **kwargs)


def test_review_rejects_non_evidence_escape_symlink_and_noncanonical_output(tmp_path):
    paths = _future_evidence(tmp_path / "case")
    other = paths["root"] / "other.json"
    _write(other, {"contract": "not-evidence"})
    with pytest.raises(ValueError, match="case_evidence"):
        cfd_review.create_review(
            other, projects_root=paths["root"],
            expected_target_sha256=cfd_review.sha256_file(other),
            reviewer_id="r", decision="APPROVED", reason="r",
        )
    with pytest.raises(ValueError, match="target"):
        cfd_review.create_review(
            tmp_path / "outside.json", projects_root=paths["root"],
            expected_target_sha256="0" * 64,
            reviewer_id="r", decision="APPROVED", reason="r",
        )
    with pytest.raises(ValueError, match="canonical"):
        _create(paths, output_dir=paths["root"] / "different-reviews")

    outside = tmp_path / "outside-evidence.json"
    outside.write_bytes(paths["evidence"].read_bytes())
    link = paths["evidence"].with_name("linked-evidence.json")
    try:
        os.symlink(outside, link)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    with pytest.raises(ValueError, match="target"):
        cfd_review.create_review(
            link, projects_root=paths["root"],
            expected_target_sha256=cfd_review.sha256_file(outside),
            reviewer_id="r", decision="APPROVED", reason="r",
        )


def test_target_mutation_invalidates_review_without_editing_record(tmp_path):
    paths = _future_evidence(tmp_path)
    review = _create(paths)
    review_path = paths["evidence"].parent / "_reviews" / f'{review["review_id"]}.case_review.v1.json'
    original = review_path.read_bytes()
    paths["evidence"].write_bytes(paths["evidence"].read_bytes() + b" ")

    errors = cfd_review.validate_review(review_path, projects_root=paths["root"])

    assert any(item["code"] == "REVIEW_TARGET_CHANGED" for item in errors)
    assert review_path.read_bytes() == original


def test_post_lock_rehash_closes_target_toctou(tmp_path):
    paths = _future_evidence(tmp_path)

    @contextlib.contextmanager
    def mutate_before_publish(_directory):
        paths["evidence"].write_bytes(paths["evidence"].read_bytes() + b" ")
        yield

    with mock.patch.object(
        cfd_review, "_review_directory_lock", side_effect=mutate_before_publish
    ), pytest.raises(ValueError, match="REVIEW_TARGET_CHANGED"):
        _create(paths)

    reviews = paths["evidence"].parent / "_reviews"
    assert not list(reviews.glob("*.case_review.v1.json"))


def test_supersession_is_append_only_and_cross_target_is_rejected(tmp_path):
    paths = _future_evidence(tmp_path)
    first = _create(paths)
    first_path = paths["evidence"].parent / "_reviews" / f'{first["review_id"]}.case_review.v1.json'
    original = first_path.read_bytes()
    second = _create(paths, decision="REJECTED", supersedes=[first["review_id"]])

    assert second["supersedes_review_ids"] == [first["review_id"]]
    assert first_path.read_bytes() == original

    copied = paths["evidence"].with_name("case_evidence-copy.v1.json")
    copied.write_bytes(paths["evidence"].read_bytes())
    with pytest.raises(ValueError, match="REVIEW_SUPERSESSION_INVALID"):
        _create(paths, target=copied, supersedes=[second["review_id"]])


def test_fork_is_ambiguous_until_one_review_supersedes_all_current_leaves(tmp_path):
    paths = _future_evidence(tmp_path)
    left = _create(paths, decision="APPROVED")
    right = _create(paths, decision="REJECTED")

    with mock.patch.object(
        cfd_case_health.cfd_evidence, "validate_case_evidence", return_value=[]
    ):
        ambiguous = cfd_case_health.build_case_health(
            paths["evidence"], projects_root=paths["root"]
        )
    assert ambiguous["citation_status"] == "CITATION_BLOCKED"
    assert "REVIEW_HISTORY_AMBIGUOUS" in {
        item["code"] for item in ambiguous["errors"]
    }

    partial = _create(paths, supersedes=[left["review_id"]])
    with mock.patch.object(
        cfd_case_health.cfd_evidence, "validate_case_evidence", return_value=[]
    ):
        still_ambiguous = cfd_case_health.build_case_health(
            paths["evidence"], projects_root=paths["root"]
        )
    assert still_ambiguous["citation_status"] == "CITATION_BLOCKED"

    resolved = _create(
        paths,
        decision="APPROVED",
        supersedes=[right["review_id"], partial["review_id"]],
    )
    with mock.patch.object(
        cfd_case_health.cfd_evidence, "validate_case_evidence", return_value=[]
    ):
        health = cfd_case_health.build_case_health(
            paths["evidence"], projects_root=paths["root"]
        )
    assert health["citation_status"] == "DESIGN_CITABLE"
    assert cfd_case_health.review_summary(
        paths["evidence"], projects_root=paths["root"]
    ) == {"status": "APPROVED", "review_id": resolved["review_id"]}


def test_direct_nonrecursive_discovery_never_selects_nested_review(tmp_path):
    paths = _future_evidence(tmp_path)
    review = _create(paths)
    reviews = paths["evidence"].parent / "_reviews"
    direct = reviews / f'{review["review_id"]}.case_review.v1.json'
    nested = reviews / "nested" / direct.name
    nested.parent.mkdir()
    direct.replace(nested)

    with mock.patch.object(
        cfd_case_health.cfd_evidence, "validate_case_evidence", return_value=[]
    ):
        health = cfd_case_health.build_case_health(
            paths["evidence"], projects_root=paths["root"]
        )

    assert health["citation_status"] == "NOT_EVALUATED"
    assert health["errors"][0]["code"] == "REVIEW_REQUIRED"


def test_schema_invalid_direct_review_is_not_ignored_as_another_target(tmp_path):
    paths = _future_evidence(tmp_path)
    review = _create(paths)
    review_path = paths["evidence"].parent / "_reviews" / f'{review["review_id"]}.case_review.v1.json'
    tampered = _read(review_path)
    tampered["target"]["path"] = str(paths["evidence"].resolve())
    _write(review_path, tampered)

    with mock.patch.object(
        cfd_case_health.cfd_evidence, "validate_case_evidence", return_value=[]
    ):
        health = cfd_case_health.build_case_health(
            paths["evidence"], projects_root=paths["root"]
        )

    assert health["citation_status"] == "CITATION_BLOCKED"
    assert "REVIEW_SCHEMA_INVALID" in {item["code"] for item in health["errors"]}


def test_concurrent_creation_and_uuid_collision_never_overwrite(tmp_path):
    paths = _future_evidence(tmp_path)
    first_hex = "1" * 12 + "4" + "1" * 3 + "8" + "1" * 15
    second_hex = "2" * 12 + "4" + "2" * 3 + "9" + "2" * 15
    ids = iter([first_hex, first_hex, second_hex])

    def next_uuid():
        return SimpleNamespace(hex=next(ids))

    with mock.patch.object(cfd_review.uuid, "uuid4", side_effect=next_uuid):
        with ThreadPoolExecutor(max_workers=2) as pool:
            created = list(pool.map(lambda _: _create(paths), range(2)))

    assert {row["review_id"] for row in created} == {
        f"review-{first_hex}", f"review-{second_hex}"
    }
    reviews = paths["evidence"].parent / "_reviews"
    assert len(list(reviews.glob("*.case_review.v1.json"))) == 2
    assert all(
        cfd_review.validate_review(path, projects_root=paths["root"]) == []
        for path in reviews.glob("*.case_review.v1.json")
    )


def test_publish_fsync_failure_leaves_no_record_or_staging(tmp_path):
    paths = _future_evidence(tmp_path)

    with mock.patch.object(cfd_review.os, "fsync", side_effect=OSError("disk")):
        with pytest.raises(OSError, match="disk"):
            _create(paths)

    reviews = paths["evidence"].parent / "_reviews"
    assert not list(reviews.glob("*.case_review.v1.json"))
    assert not list(reviews.glob(".*.tmp"))
