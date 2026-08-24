"""Immutable, hash-bound human review records for Case Evidence."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
from typing import Any, Sequence
import uuid

from jsonschema import Draft202012Validator


CONTRACT = "case_review.v1"
REVIEW_ID_PATTERN = re.compile(r"^review-[0-9a-f]{32}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _schema(filename: str) -> Draft202012Validator:
    path = Path(__file__).resolve().with_name(filename)
    return Draft202012Validator(json.loads(path.read_text(encoding="utf-8")))


def _read_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        return None


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _contained(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _no_reparse_chain(path: Path, root: Path) -> bool:
    try:
        relative = path.absolute().relative_to(root.absolute())
    except ValueError:
        return False
    current = root
    if _is_reparse(current):
        return False
    for part in relative.parts:
        current = current / part
        if current.exists() and _is_reparse(current):
            return False
    return True


def _safe_existing(path: Path, root: Path, *, directory: bool = False) -> Path | None:
    try:
        lexical = path.absolute()
        if not _no_reparse_chain(lexical, root):
            return None
        resolved = lexical.resolve(strict=True)
        if not _contained(resolved, root):
            return None
        if directory and not resolved.is_dir():
            return None
        if not directory and not resolved.is_file():
            return None
        return resolved
    except (OSError, RuntimeError, ValueError):
        return None


def _projects_root(projects_root: Path) -> Path:
    raw = Path(projects_root).expanduser()
    try:
        root = raw.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("projects_root must be a real directory") from exc
    if not root.is_dir() or _is_reparse(raw):
        raise ValueError("projects_root must be a real directory")
    return root


def resolve_evidence_target(
    target_path: Path, *, projects_root: Path
) -> tuple[Path, Path, dict, str, str]:
    """Resolve one schema-valid Case Evidence target beneath the project root."""
    root = _projects_root(projects_root)
    raw = Path(target_path).expanduser()
    if not raw.is_absolute():
        raw = root / raw
    target = _safe_existing(raw, root)
    if target is None:
        raise ValueError("review target must be a safe project file")
    payload = _read_json(target)
    validator = _schema("case_evidence.v1.schema.json")
    if payload is None or list(validator.iter_errors(payload)):
        raise ValueError("review target must satisfy case_evidence.v1")
    relative = target.relative_to(root).as_posix()
    return root, target, payload, sha256_file(target), relative


def _valid_review_id(review_id: Any) -> bool:
    if not isinstance(review_id, str) or not REVIEW_ID_PATTERN.fullmatch(review_id):
        return False
    try:
        parsed = uuid.UUID(hex=review_id[7:])
    except (ValueError, AttributeError):
        return False
    return parsed.version == 4 and parsed.variant == uuid.RFC_4122


def _error(errors: list[dict[str, str]], code: str, detail: str) -> None:
    row = {"code": code, "detail": detail}
    if row not in errors:
        errors.append(row)


def _review_validation(
    review_path: Path, root: Path
) -> tuple[dict | None, list[dict[str, str]]]:
    errors: list[dict[str, str]] = []
    safe = _safe_existing(Path(review_path), root)
    if safe is None:
        return None, [{"code": "REVIEW_RECORD_INVALID", "detail": "review path is unsafe"}]
    record = _read_json(safe)
    if record is None or list(_schema("case_review.v1.schema.json").iter_errors(record)):
        _error(errors, "REVIEW_SCHEMA_INVALID", "review record schema is invalid")
    if not isinstance(record, dict):
        return record, errors
    review_id = record.get("review_id")
    if not _valid_review_id(review_id):
        _error(errors, "REVIEW_ID_INVALID", "review_id is not a UUIDv4 lowercase hex ID")
    if safe.name != f"{review_id}.case_review.v1.json":
        _error(errors, "REVIEW_ID_INVALID", "review filename does not match review_id")
    target = record.get("target") if isinstance(record.get("target"), dict) else {}
    raw_target = target.get("path")
    if not isinstance(raw_target, str) or "\\" in raw_target:
        target_path = None
    else:
        candidate = root / raw_target
        target_path = _safe_existing(candidate, root)
    if target_path is None:
        _error(errors, "REVIEW_TARGET_CHANGED", "review target is unavailable or unsafe")
    else:
        current_target = _read_json(target_path)
        if (
            current_target is None
            or list(_schema("case_evidence.v1.schema.json").iter_errors(current_target))
        ):
            _error(
                errors,
                "REVIEW_TARGET_SCHEMA_INVALID",
                "current review target does not satisfy case_evidence.v1",
            )
        try:
            current_hash = sha256_file(target_path)
        except OSError:
            current_hash = None
        if target.get("contract") != "case_evidence.v1" or current_hash != target.get("sha256"):
            _error(errors, "REVIEW_TARGET_CHANGED", "review target bytes changed")
    return record, errors


def validate_review(review_path: Path, *, projects_root: Path) -> list[dict]:
    """Validate a closed review record and rehash its current target."""
    root = _projects_root(projects_root)
    _, errors = _review_validation(Path(review_path), root)
    return errors


def _canonical_review_directory(
    target: Path, root: Path, output_dir: Path | None
) -> Path:
    canonical = target.parent / "_reviews"
    if output_dir is not None:
        raw = Path(output_dir).expanduser()
        if not raw.is_absolute():
            raw = root / raw
        try:
            if raw.absolute().resolve() != canonical.absolute().resolve():
                raise ValueError
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError("output_dir must be the canonical evidence _reviews directory") from exc
    canonical.mkdir(parents=True, exist_ok=True)
    safe = _safe_existing(canonical, root, directory=True)
    if safe is None:
        raise ValueError("output_dir must be the canonical evidence _reviews directory")
    return safe


@contextmanager
def _review_directory_lock(directory: Path):
    """Serialize cooperating publishers across threads and processes."""
    key = str(directory.resolve()).casefold()
    with _THREAD_LOCKS_GUARD:
        thread_lock = _THREAD_LOCKS.setdefault(key, threading.RLock())
    with thread_lock:
        lock_path = directory / ".case_review.lock"
        with lock_path.open("a+b") as stream:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _direct_review_paths(directory: Path) -> list[Path]:
    try:
        return sorted(
            (
                path for path in directory.iterdir()
                if path.name.endswith(".case_review.v1.json")
            ),
            key=lambda path: path.name,
        )
    except OSError:
        return []


def _history(
    directory: Path, root: Path, target_relative: str, target_sha256: str
) -> dict:
    records: dict[str, dict] = {}
    invalid: list[dict[str, str]] = []
    for path in _direct_review_paths(directory):
        record, errors = _review_validation(path, root)
        target = record.get("target") if isinstance(record, dict) and isinstance(record.get("target"), dict) else {}
        exact_target = (
            target.get("path") == target_relative
            and target.get("sha256") == target_sha256
        )
        structural_errors = [
            error for error in errors
            if error.get("code") != "REVIEW_TARGET_CHANGED"
        ]
        if record is None:
            invalid.extend(errors or [{"code": "REVIEW_RECORD_INVALID", "detail": "unreadable review"}])
        elif structural_errors:
            invalid.extend(structural_errors)
        elif exact_target and errors:
            invalid.extend(errors)
        elif exact_target:
            review_id = record["review_id"]
            if review_id in records:
                _error(invalid, "REVIEW_ID_INVALID", "duplicate review_id")
            else:
                records[review_id] = record
    superseded: set[str] = set()
    for record in records.values():
        for prior in record.get("supersedes_review_ids") or []:
            if prior not in records or prior == record["review_id"]:
                _error(invalid, "REVIEW_SUPERSESSION_INVALID", "review history has an invalid edge")
            else:
                superseded.add(prior)
    leaves = [record for review_id, record in records.items() if review_id not in superseded]
    if records and not leaves:
        _error(invalid, "REVIEW_SUPERSESSION_INVALID", "review history has no current leaf")
    return {"records": records, "leaves": leaves, "errors": invalid}


def current_review_state(evidence_path: Path, *, projects_root: Path) -> dict:
    """Derive current review state without choosing a newest or first record."""
    root, target, _, digest, relative = resolve_evidence_target(
        evidence_path, projects_root=projects_root
    )
    directory = target.parent / "_reviews"
    if not directory.is_dir():
        return {"status": "MISSING", "review_id": None, "errors": []}
    safe_directory = _safe_existing(directory, root, directory=True)
    if safe_directory is None:
        return {
            "status": "INVALID",
            "review_id": None,
            "errors": [{"code": "REVIEW_RECORD_INVALID", "detail": "review directory is unsafe"}],
        }
    history = _history(safe_directory, root, relative, digest)
    if history["errors"]:
        return {"status": "INVALID", "review_id": None, "errors": history["errors"]}
    leaves = history["leaves"]
    if len(leaves) > 1:
        return {
            "status": "AMBIGUOUS",
            "review_id": None,
            "errors": [{
                "code": "REVIEW_HISTORY_AMBIGUOUS",
                "detail": "multiple unsuperseded current review leaves",
            }],
        }
    if not leaves:
        return {"status": "MISSING", "review_id": None, "errors": []}
    leaf = leaves[0]
    return {
        "status": leaf["decision"],
        "review_id": leaf["review_id"],
        "errors": [],
    }


def _fsync_directory(directory: Path) -> None:
    flags = getattr(os, "O_RDONLY", 0)
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        descriptor = os.open(directory, flags)
    except (OSError, TypeError):
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _publish_review(directory: Path, record: dict) -> Path:
    final = directory / f'{record["review_id"]}.case_review.v1.json'
    if final.exists():
        raise FileExistsError(final)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{final.name}.", suffix=".tmp", dir=directory
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, final)
        temporary.unlink()
        _fsync_directory(directory)
        return final
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def create_review(
    target_path: Path,
    *,
    projects_root: Path,
    expected_target_sha256: str,
    reviewer_id: str,
    decision: str,
    reason: str,
    output_dir: Path | None = None,
    supersedes_review_ids: Sequence[str] = (),
) -> dict:
    """Append one closed review after optimistic and post-lock target checks."""
    if decision not in {"APPROVED", "REJECTED"}:
        raise ValueError("decision must be APPROVED or REJECTED")
    if not isinstance(reviewer_id, str) or not reviewer_id.strip():
        raise ValueError("reviewer_id is required")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason is required")
    if not isinstance(expected_target_sha256, str) or not SHA256_PATTERN.fullmatch(expected_target_sha256):
        raise ValueError("expected_target_sha256 must be lowercase SHA-256")
    supersedes = list(supersedes_review_ids or ())
    if len(supersedes) != len(set(supersedes)) or not all(
        isinstance(item, str) and _valid_review_id(item) for item in supersedes
    ):
        raise ValueError("REVIEW_SUPERSESSION_INVALID")

    root, target, _, initial_hash, relative = resolve_evidence_target(
        target_path, projects_root=projects_root
    )
    if initial_hash != expected_target_sha256:
        raise ValueError("REVIEW_TARGET_CHANGED")
    directory = _canonical_review_directory(target, root, output_dir)

    with _review_directory_lock(directory):
        try:
            locked_root, locked_target, _, locked_hash, locked_relative = resolve_evidence_target(
                target, projects_root=root
            )
        except ValueError as exc:
            raise ValueError("REVIEW_TARGET_CHANGED") from exc
        if (
            locked_root != root
            or locked_target != target
            or locked_relative != relative
            or locked_hash != expected_target_sha256
        ):
            raise ValueError("REVIEW_TARGET_CHANGED")
        history = _history(directory, root, relative, locked_hash)
        if history["errors"]:
            raise ValueError("REVIEW_HISTORY_INVALID")
        leaf_ids = {record["review_id"] for record in history["leaves"]}
        if not set(supersedes).issubset(leaf_ids):
            raise ValueError("REVIEW_SUPERSESSION_INVALID")

        for _ in range(64):
            review_id = "review-" + uuid.uuid4().hex
            if not _valid_review_id(review_id):
                continue
            final = directory / f"{review_id}.case_review.v1.json"
            if final.exists():
                continue
            record = {
                "contract": CONTRACT,
                "schema_version": 1,
                "created_at": _now(),
                "review_id": review_id,
                "reviewer": reviewer_id.strip(),
                "decision": decision,
                "status": decision,
                "reason": reason.strip(),
                "target": {
                    "contract": "case_evidence.v1",
                    "path": relative,
                    "sha256": locked_hash,
                },
                "errors": [],
            }
            if supersedes:
                record["supersedes_review_ids"] = supersedes
            if list(_schema("case_review.v1.schema.json").iter_errors(record)):
                raise RuntimeError("CASE_REVIEW_SCHEMA_MISMATCH")
            try:
                final_root, final_target, _, final_hash, final_relative = resolve_evidence_target(
                    target, projects_root=root
                )
            except ValueError as exc:
                raise ValueError("REVIEW_TARGET_CHANGED") from exc
            if (
                final_root != root
                or final_target != target
                or final_relative != relative
                or final_hash != expected_target_sha256
            ):
                raise ValueError("REVIEW_TARGET_CHANGED")
            try:
                _publish_review(directory, record)
            except FileExistsError:
                continue
            return record
    raise RuntimeError("REVIEW_ID_COLLISION")
