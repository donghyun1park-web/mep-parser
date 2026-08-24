"""Safe, traceable OpenFOAM parallel-execution policy.

This module does not launch MPI.  It decides whether a case may use MPI from
recorded runtime evidence, and writes the immutable planning artifact that a
runner can later update.  Missing evidence must choose serial execution.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile


PARALLEL_RUN_CONTRACT = "parallel_run.v1"
MPI_FALLBACK_CHAIN = ("scotch", "hierarchical", "simple")


@dataclass(frozen=True)
class ParallelExecutionPlan:
    """A conservative execution decision shared by all solver paths."""

    mode: str
    ranks: int
    decomposition: str | None
    fallback_chain: tuple[str, ...]
    requested_ranks: int
    effective_cpu_count: int | None
    cell_count: int
    min_cells: int
    blockers: tuple[str, ...]


def _positive_int(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _capability_values(capabilities):
    """Read either runtime_capability.v1 or a raw diagnose_openfoam result."""
    payload = dict(capabilities or {})
    mpi = dict(payload.get("mpi") or {})
    cpu = dict(payload.get("cpu") or {})
    # A runtime contract may explicitly deny parallel use even though the
    # legacy discovery flag is true (commands found + CPU count).  Do not let
    # `or` turn that denial back into permission.
    if "parallel_runtime_ready" in payload:
        parallel_ready = bool(payload.get("parallel_runtime_ready"))
    else:
        parallel_ready = bool(payload.get("parallel_ready"))
    return {
        "parallel_ready": parallel_ready,
        "execution_smoke": str(
            mpi.get("execution_smoke") or payload.get("mpi_execution_smoke")
            or "NOT_RUN"
        ).upper(),
        "effective_cpu_count": _positive_int(
            cpu.get("effective_logical_count")
            if cpu else payload.get("effective_cpu_count")
        ),
    }


def choose_parallel_plan(case_kind, cell_count, capabilities, *, requested_ranks=1,
                         min_cells=80_000):
    """Return an MPI plan only when live runtime evidence proves it is safe.

    A serial choice is not an error: it carries blocking reasons so the GUI and
    report can explain why a requested MPI run was not enabled.
    """
    requested = _positive_int(requested_ranks) or 1
    cells = max(0, _positive_int(cell_count) or 0)
    threshold = max(1, _positive_int(min_cells) or 80_000)
    evidence = _capability_values(capabilities)
    cpu = evidence["effective_cpu_count"]
    blockers = []

    if requested <= 1:
        blockers.append("parallel_not_requested")
    if cells < threshold:
        blockers.append("below_parallel_cell_threshold")
    if evidence["execution_smoke"] != "PASS":
        blockers.append("mpi_execution_smoke_not_passed")
    if not evidence["parallel_ready"]:
        blockers.append("parallel_runtime_not_ready")
    if cpu is None or cpu < 2:
        blockers.append("effective_cpu_count_unavailable")
    elif requested > cpu:
        blockers.append("requested_ranks_exceed_effective_cpu")

    if blockers:
        return ParallelExecutionPlan(
            mode="serial",
            ranks=1,
            decomposition=None,
            fallback_chain=("serial",),
            requested_ranks=requested,
            effective_cpu_count=cpu,
            cell_count=cells,
            min_cells=threshold,
            blockers=tuple(blockers),
        )
    return ParallelExecutionPlan(
        mode="mpi",
        ranks=requested,
        decomposition=MPI_FALLBACK_CHAIN[0],
        fallback_chain=MPI_FALLBACK_CHAIN,
        requested_ranks=requested,
        effective_cpu_count=cpu,
        cell_count=cells,
        min_cells=threshold,
        blockers=(),
    )


def write_parallel_run(path, plan, *, case_kind, input_sha256=""):
    """Atomically persist a planned ``parallel_run.v1`` artifact."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "contract": PARALLEL_RUN_CONTRACT,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "case_kind": str(case_kind or ""),
        "phase": "planned",
        "input_sha256": str(input_sha256 or ""),
        "plan": asdict(plan),
        "execution": {
            "status": "NOT_RUN",
            "mode": plan.mode,
            "ranks": plan.ranks,
            "decomposition": plan.decomposition,
            "fallback_reason": "",
        },
    }
    fd, temporary = tempfile.mkstemp(
        prefix=".parallel_run.", suffix=".tmp", dir=str(target.parent), text=True
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return payload
