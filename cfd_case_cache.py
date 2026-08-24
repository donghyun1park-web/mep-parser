"""Fast, safe cache for expensive CFD case summaries.

The cache deliberately fingerprints the selected solver artifacts by path,
size and nanosecond mtime instead of hashing large OpenFOAM fields.  It is an
input-state fingerprint, not a cryptographic provenance hash.  A per-case
in-process lock prevents concurrent HTTP requests from recalculating the same
summary in parallel.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import tempfile
import threading


CACHE_SCHEMA = "case-summary-cache.v1"
CACHE_FILENAME = "cfd_case_summary.cache.v1.json"
# Bump this whenever summary semantics or its source-selection logic changes.
# File fingerprints alone cannot detect a newer version of the Python producer.
SUMMARY_PRODUCER_REVISION = "result-trust-v4-opening-preflight"
_NUMERIC_TIME = re.compile(r"\d+(\.\d+)?$")
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def _file_stat(case_dir: Path, path) -> dict | None:
    if not path:
        return None
    target = Path(path)
    try:
        stat = target.stat()
        relative = target.resolve().relative_to(case_dir.resolve()).as_posix()
    except (OSError, ValueError):
        return None
    if not target.is_file():
        return None
    return {"path": relative, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _time_dirs(case_dir: Path) -> list[Path]:
    times = []
    try:
        children = list(case_dir.iterdir())
    except OSError:
        return []
    for child in children:
        if child.is_dir() and _NUMERIC_TIME.fullmatch(child.name):
            value = float(child.name)
            if value > 0:
                times.append((value, child))
    return [path for _, path in sorted(times)]


def summary_fingerprint(case_dir, *, log_path=None, report_path=None) -> dict:
    """Return the fast input-state fingerprint for one case summary."""
    case = Path(case_dir).resolve()
    times = _time_dirs(case)
    latest = times[-1] if times else None
    recent = times[-3:]
    return {
        "schema": CACHE_SCHEMA,
        "producer_revision": SUMMARY_PRODUCER_REVISION,
        "meta": _file_stat(case, case / "cfd_case_meta.json"),
        "solver_log": _file_stat(case, log_path),
        "time_names": [path.name for path in times],
        "latest_fields": {
            "T": _file_stat(case, latest / "T") if latest else None,
            "U": _file_stat(case, latest / "U") if latest else None,
        },
        "recent_energy_fields": [
            {
                "time": path.name,
                "T": _file_stat(case, path / "T"),
                "phi": _file_stat(case, path / "phi"),
            }
            for path in recent
        ],
        "opening_boundary_verification": _file_stat(
            case, case / "opening_boundary_verification.v1.json"
        ),
        "report": _file_stat(case, report_path),
    }


@contextmanager
def case_lock(case_dir):
    """Serialize summary cache work for one case within this process."""
    key = str(Path(case_dir).resolve())
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.Lock())
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def load(case_dir, fingerprint):
    """Return a cached summary only when the full fingerprint matches."""
    path = Path(case_dir) / CACHE_FILENAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if payload.get("schema") != CACHE_SCHEMA:
        return None
    if payload.get("fingerprint") != fingerprint:
        return None
    summary = payload.get("summary")
    return summary if isinstance(summary, dict) else None


def publish(case_dir, fingerprint, summary):
    """Atomically publish a validated summary cache entry."""
    case = Path(case_dir)
    payload = {
        "schema": CACHE_SCHEMA,
        "fingerprint": fingerprint,
        "summary": summary,
    }
    fd, temp_path = tempfile.mkstemp(
        prefix=f".{CACHE_FILENAME}.", suffix=".tmp", dir=case, text=True
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.write("\n")
        os.replace(temp_path, case / CACHE_FILENAME)
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
