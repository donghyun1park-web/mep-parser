"""Small, dependency-free diagnostics for OpenFOAM ASCII legacy VTK files."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re


_OPENFOAM_TIMING = re.compile(
    r"ExecutionTime\s*=\s*([0-9.eE+-]+)\s*s\s+ClockTime\s*=\s*([0-9.eE+-]+)\s*s"
)
_GNU_TIME_RSS = re.compile(
    r"^\s*Maximum resident set size \(kbytes\):\s*(\d+)\s*$", re.MULTILINE
)


def evaluate_mpi_runtime_smoke(trials, *, required_ranks=(2, 4)):
    """Classify bounded MPI hostname trials without inferring a safe result.

    A successful ``mpirun --version`` only proves that the launcher is
    installed.  MPI may be used for CFD only after every requested rank count
    starts, produces one hostname line per rank, and its isolated process group
    is verified clean.  ``BLOCKED`` deliberately distinguishes a failed/hung
    smoke from ``NOT_RUN`` so the UI can preserve recovery evidence.
    """
    rows = [dict(row or {}) for row in (trials or [])]
    requested = []
    for value in required_ranks or ():
        try:
            ranks = int(value)
        except (TypeError, ValueError):
            continue
        if ranks > 0 and ranks not in requested:
            requested.append(ranks)
    if not requested:
        return {"status": "NOT_RUN", "reason_code": "MPI_SMOKE_NOT_RUN"}
    if not rows:
        return {"status": "NOT_RUN", "reason_code": "MPI_SMOKE_NOT_RUN"}

    by_ranks = {}
    for row in rows:
        try:
            ranks = int(row.get("ranks"))
        except (TypeError, ValueError):
            continue
        if ranks > 0 and ranks not in by_ranks:
            by_ranks[ranks] = row

    for ranks in requested:
        row = by_ranks.get(ranks)
        if row is None:
            return {"status": "BLOCKED", "reason_code": "MPI_SMOKE_INCOMPLETE"}
        cleanup = dict(row.get("cleanup") or {})
        cleanup_status = str(cleanup.get("status") or "UNVERIFIED").upper()
        timed_out = bool(row.get("timed_out"))
        if timed_out:
            return {"status": "BLOCKED", "reason_code": "MPI_RANK_SPAWN_HANG"}
        if cleanup_status != "CLEAN":
            return {"status": "BLOCKED", "reason_code": "MPI_CLEANUP_UNVERIFIED"}
        if row.get("returncode") != 0:
            return {"status": "BLOCKED", "reason_code": "MPI_RUNTIME_COMMAND_FAILED"}
        try:
            lines = int(row.get("hostname_line_count"))
        except (TypeError, ValueError):
            lines = -1
        if lines != ranks:
            return {"status": "BLOCKED", "reason_code": "MPI_RANK_OUTPUT_MISMATCH"}
    return {"status": "PASS", "reason_code": ""}


def parse_openfoam_timing(text):
    """Extract the final OpenFOAM execution and wall-clock timing pair.

    The solver can print this line repeatedly. The final one is the only
    useful value for a completed run; unavailable timing stays None rather
    than being silently converted to zero.
    """
    matches = list(_OPENFOAM_TIMING.finditer(text or ""))
    if not matches:
        return {"execution_seconds": None, "clock_seconds": None}
    match = matches[-1]
    try:
        return {
            "execution_seconds": float(match.group(1)),
            "clock_seconds": float(match.group(2)),
        }
    except ValueError:
        return {"execution_seconds": None, "clock_seconds": None}


def parse_gnu_time_v(text):
    """Return GNU time -v peak resident set size in KiB, if reported."""
    matches = list(_GNU_TIME_RSS.finditer(text or ""))
    if not matches:
        return {"peak_rss_kib": None}
    try:
        return {"peak_rss_kib": int(matches[-1].group(1))}
    except ValueError:
        return {"peak_rss_kib": None}


def _marker(lines, name):
    prefix = name + " "
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            return index, line.split()
    raise ValueError(f"VTK section not found: {name}")


def _numbers(lines, start, count, cast):
    values = []
    index = start
    while index < len(lines) and len(values) < count:
        values.extend(cast(value) for value in lines[index].split())
        index += 1
    if len(values) != count:
        raise ValueError(f"Expected {count} VTK values, found {len(values)}")
    return values, index


def analyze_velocity_vtk(path, top_count=10):
    """Return peak cell-centred velocity and approximate cell locations."""
    path = Path(path)
    lines = path.read_text(encoding="ascii").splitlines()
    if not lines or "ASCII" not in lines[2].upper():
        raise ValueError("Only ASCII legacy VTK files are supported")

    points_at, points_header = _marker(lines, "POINTS")
    point_count = int(points_header[1])
    point_values, _ = _numbers(lines, points_at + 1, point_count * 3, float)
    points = [point_values[i:i + 3] for i in range(0, len(point_values), 3)]

    cells_at, cells_header = _marker(lines, "CELLS")
    cell_count = int(cells_header[1])
    connectivity_size = int(cells_header[2])
    connectivity, _ = _numbers(lines, cells_at + 1, connectivity_size, int)
    cells = []
    cursor = 0
    for _ in range(cell_count):
        vertex_count = connectivity[cursor]
        cursor += 1
        cells.append(connectivity[cursor:cursor + vertex_count])
        cursor += vertex_count
    if cursor != len(connectivity):
        raise ValueError("Unexpected trailing VTK cell connectivity")

    types_at, types_header = _marker(lines, "CELL_TYPES")
    if int(types_header[1]) != cell_count:
        raise ValueError("CELL_TYPES count does not match CELLS")
    cell_types, _ = _numbers(lines, types_at + 1, cell_count, int)

    field_at, field_header = _marker(lines, "U")
    if int(field_header[1]) != 3 or int(field_header[2]) != cell_count:
        raise ValueError("U must be a three-component cell field")
    velocity_values, _ = _numbers(lines, field_at + 1, cell_count * 3, float)
    velocities = [velocity_values[i:i + 3]
                  for i in range(0, len(velocity_values), 3)]

    try:
        ids_at, ids_header = _marker(lines, "cellID")
        cell_ids, _ = _numbers(lines, ids_at + 1, int(ids_header[2]), int)
    except ValueError:
        cell_ids = list(range(cell_count))

    def row(index):
        vector = velocities[index]
        magnitude = math.sqrt(sum(value * value for value in vector))
        connectivity = cells[index]
        if cell_types[index] == 42:  # VTK_POLYHEDRON face stream
            vertex_ids = []
            cursor = 1
            for _ in range(connectivity[0]):
                face_size = connectivity[cursor]
                cursor += 1
                vertex_ids.extend(connectivity[cursor:cursor + face_size])
                cursor += face_size
            vertex_ids = list(dict.fromkeys(vertex_ids))
        else:
            vertex_ids = connectivity
        vertices = [points[item] for item in vertex_ids]
        centre = [sum(vertex[axis] for vertex in vertices) / len(vertices)
                  for axis in range(3)]
        lower = [min(vertex[axis] for vertex in vertices) for axis in range(3)]
        upper = [max(vertex[axis] for vertex in vertices) for axis in range(3)]
        return {
            "cell_id": cell_ids[index],
            "vtk_cell_index": index,
            "vtk_cell_type": cell_types[index],
            "velocity_m_s": vector,
            "speed_m_s": magnitude,
            "approximate_centre_m": centre,
            "bounds_m": {"minimum": lower, "maximum": upper},
        }

    ranked = sorted(
        range(cell_count),
        key=lambda index: sum(value * value for value in velocities[index]),
        reverse=True,
    )[:max(1, int(top_count))]
    return {
        "schema_version": "cfd.velocity-diagnostic.v1",
        "source": str(path),
        "cell_count": cell_count,
        "peak": row(ranked[0]),
        "top_cells": [row(index) for index in ranked],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vtk", type=Path)
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args(argv)
    print(json.dumps(analyze_velocity_vtk(args.vtk, args.top), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
