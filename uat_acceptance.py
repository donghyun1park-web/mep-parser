"""Build and verify observed mechanical-facility user acceptance sessions."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

import field_acceptance


CONTRACT = "mechanical_user_uat.v1"
GENERATOR = "mep-cfd-studio/mechanical-uat-v1"
TASKS = (
    "launch_application",
    "import_dxf",
    "confirm_geometry",
    "configure_conditions",
    "run_or_open_result",
    "interpret_report",
)
SETUP_TASK = "configure_conditions"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (OSError, ValueError):
        return False


def _parse_time(value):
    text = str(value or "").strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("timezone is required")
    return parsed.astimezone(timezone.utc)


def _atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                                     dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _evidence_record(path, root):
    path = Path(path).resolve()
    try:
        stored = path.relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        stored = str(path)
    return {"path": stored, "sha256": _sha256(path)}


def _resolve_record(record, root):
    path = Path(str((record or {}).get("path") or ""))
    if not path.is_absolute():
        path = Path(root) / path
    return path.resolve()


def compute_session(participant_id, observed_by, started_at, completed_at,
                    tasks, critical_incidents, field_evidence_path,
                    projects_root="cfd_projects"):
    errors = []
    participant_id = str(participant_id or "").strip()
    observed_by = str(observed_by or "").strip()
    if not participant_id:
        errors.append("PARTICIPANT_ID_REQUIRED")
    if not observed_by:
        errors.append("OBSERVER_REQUIRED")
    if participant_id and participant_id.casefold() == observed_by.casefold():
        errors.append("INDEPENDENT_OBSERVER_REQUIRED")
    try:
        start = _parse_time(started_at)
        end = _parse_time(completed_at)
        if end <= start:
            errors.append("SESSION_TIME_ORDER")
    except (TypeError, ValueError):
        start = end = None
        errors.append("SESSION_TIME_INVALID")

    task_rows = list(tasks or [])
    if [str(row.get("id") or "") for row in task_rows] != list(TASKS):
        errors.append("REQUIRED_TASK_ORDER")
    normalized_tasks = []
    for row in task_rows:
        try:
            task_start = _parse_time(row.get("started_at"))
            task_end = _parse_time(row.get("completed_at"))
            assistance = int(row.get("assistance_count") or 0)
            if task_end <= task_start or assistance < 0:
                raise ValueError("invalid task duration or assistance")
            if start and end and (task_start < start or task_end > end):
                raise ValueError("task outside session")
            status = str(row.get("status") or "").upper()
            if status not in ("PASS", "FAIL"):
                raise ValueError("invalid status")
            normalized_tasks.append({
                "id": str(row.get("id") or ""), "status": status,
                "started_at": task_start.isoformat(),
                "completed_at": task_end.isoformat(),
                "assistance_count": assistance,
                "notes": str(row.get("notes") or "").strip(),
            })
        except (TypeError, ValueError):
            errors.append(f"TASK_INVALID:{row.get('id') or 'unknown'}")
    for left, right in zip(normalized_tasks, normalized_tasks[1:]):
        if _parse_time(right["started_at"]) < _parse_time(left["completed_at"]):
            errors.append("TASK_TIME_OVERLAP")
            break

    incidents = []
    for row in critical_incidents or []:
        severity = str(row.get("severity") or "").lower()
        if severity not in ("fatal", "major", "minor"):
            errors.append("INCIDENT_SEVERITY_INVALID")
            continue
        incidents.append({
            "severity": severity,
            "code": str(row.get("code") or "UNSPECIFIED").strip(),
            "notes": str(row.get("notes") or "").strip(),
        })

    root = Path(projects_root).expanduser().resolve()
    field_path = Path(field_evidence_path).expanduser().resolve()
    field_record = {}
    if not field_path.is_file() or not _inside(field_path, root):
        errors.append("FIELD_EVIDENCE_MISSING_OR_OUTSIDE_PROJECT")
    else:
        verified = field_acceptance.validate_evidence(field_path, root)
        if not verified.get("ok"):
            errors.append("FIELD_EVIDENCE_INVALID")
        field_record = _evidence_record(field_path, root)

    task_by_id = {row["id"]: row for row in normalized_tasks}
    first_project_completed = (
        len(normalized_tasks) == len(TASKS)
        and all(task_by_id.get(task, {}).get("status") == "PASS" for task in TASKS)
    )
    setup_minutes = None
    if start and SETUP_TASK in task_by_id:
        setup_minutes = round((
            _parse_time(task_by_id[SETUP_TASK]["completed_at"]) - start
        ).total_seconds() / 60.0, 3)
        if setup_minutes < 0:
            errors.append("SETUP_TIME_INVALID")
    fatal_count = sum(row["severity"] == "fatal" for row in incidents)
    assistance_count = sum(row["assistance_count"] for row in normalized_tasks)
    return {
        "errors": list(dict.fromkeys(errors)),
        "participant_id": participant_id,
        "observed_by": observed_by,
        "started_at": start.isoformat() if start else str(started_at or ""),
        "completed_at": end.isoformat() if end else str(completed_at or ""),
        "tasks": normalized_tasks,
        "critical_incidents": incidents,
        "field_evidence": field_record,
        "first_project_completed": first_project_completed,
        "setup_minutes": setup_minutes,
        "fatal_usability_errors": fatal_count,
        "assistance_count": assistance_count,
    }


def build_uat_session(participant_id, observed_by, started_at, completed_at,
                      tasks, critical_incidents, field_evidence_path,
                      projects_root="cfd_projects", output_path=None):
    root = Path(projects_root).expanduser().resolve()
    computed = compute_session(
        participant_id, observed_by, started_at, completed_at, tasks,
        critical_incidents, field_evidence_path, root,
    )
    session_basis = "|".join((computed["participant_id"], computed["observed_by"],
                              computed["started_at"]))
    session_id = hashlib.sha256(session_basis.encode("utf-8")).hexdigest()[:16]
    manifest = {
        "schema_version": 1, "contract": CONTRACT, "generator": GENERATOR,
        "created_at": _now(), "session_id": session_id,
        "participant_role": "mechanical_facility",
        **computed,
    }
    manifest["status"] = (
        "INVALID" if manifest["errors"] else
        "PASS" if manifest["first_project_completed"]
        and manifest["fatal_usability_errors"] == 0 else "FAIL"
    )
    if output_path is None:
        output_path = root / "_release_evidence" / "uat" / f"session-{session_id}.json"
    _atomic_json(output_path, manifest)
    return {"ok": not manifest["errors"], "manifest": manifest,
            "manifest_path": str(Path(output_path).resolve())}


def validate_evidence(path, projects_root="cfd_projects"):
    try:
        row = _read(path)
        if (row.get("contract") != CONTRACT or row.get("generator") != GENERATOR
                or row.get("participant_role") != "mechanical_facility"
                or row.get("status") not in ("PASS", "FAIL")):
            return {"ok": False, "error": "UAT_CONTRACT_OR_STATUS"}
        root = Path(projects_root).expanduser().resolve()
        field_path = _resolve_record(row.get("field_evidence"), root)
        computed = compute_session(
            row.get("participant_id"), row.get("observed_by"),
            row.get("started_at"), row.get("completed_at"), row.get("tasks"),
            row.get("critical_incidents"), field_path, root,
        )
        keys = (
            "participant_id", "observed_by", "started_at", "completed_at",
            "tasks", "critical_incidents", "field_evidence",
            "first_project_completed", "setup_minutes",
            "fatal_usability_errors", "assistance_count", "errors",
        )
        if computed["errors"] or any(computed[key] != row.get(key) for key in keys):
            return {"ok": False, "error": "UAT_STALE_OR_EDITED"}
        expected_status = (
            "PASS" if computed["first_project_completed"]
            and computed["fatal_usability_errors"] == 0 else "FAIL"
        )
        if row.get("status") != expected_status:
            return {"ok": False, "error": "UAT_STATUS_EDITED"}
        return {"ok": True, "manifest": row, "computed": computed}
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"UAT_READ_ERROR:{exc}"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="JSON containing participant, observer, times, tasks and incidents")
    parser.add_argument("--projects-root", default="cfd_projects")
    parser.add_argument("--field-evidence", required=True)
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    source = _read(args.input)
    result = build_uat_session(
        source.get("participant_id"), source.get("observed_by"),
        source.get("started_at"), source.get("completed_at"),
        source.get("tasks"), source.get("critical_incidents"),
        args.field_evidence, args.projects_root, args.output,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
