import tempfile
import unittest
from pathlib import Path

import cfd_diagnostics


class VtkDiagnosticsTests(unittest.TestCase):
    def test_peak_velocity_includes_cell_id_and_approximate_centre(self):
        vtk = """# vtk DataFile Version 2.0
sample
ASCII
DATASET UNSTRUCTURED_GRID
POINTS 8 float
0 0 0 1 0 0 1 1 0 0 1 0
0 0 1 1 0 1 1 1 1 0 1 1
CELLS 2 10
4 0 1 2 3 4 4 5 6 7
CELL_TYPES 2
10 10
CELL_DATA 2
FIELD FieldData 2
cellID 1 2 int
17 42
U 3 2 float
1 0 0 0 3 4
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.vtk"
            path.write_text(vtk, encoding="ascii")
            result = cfd_diagnostics.analyze_velocity_vtk(path)
        self.assertEqual(result["peak"]["cell_id"], 42)
        self.assertEqual(result["peak"]["vtk_cell_type"], 10)
        self.assertEqual(result["peak"]["speed_m_s"], 5.0)
        self.assertEqual(result["peak"]["approximate_centre_m"], [0.5, 0.5, 1.0])


class RuntimeTimingDiagnosticsTests(unittest.TestCase):
    def test_reads_last_openfoam_execution_and_clock_times(self):
        result = cfd_diagnostics.parse_openfoam_timing(
            "ExecutionTime = 1.2 s  ClockTime = 2 s\n"
            "ExecutionTime = 3.45 s  ClockTime = 5.67 s\n"
        )
        self.assertEqual(
            result,
            {"execution_seconds": 3.45, "clock_seconds": 5.67},
        )

    def test_missing_openfoam_timing_is_not_reported_as_zero(self):
        self.assertEqual(
            cfd_diagnostics.parse_openfoam_timing("solver output without timing\n"),
            {"execution_seconds": None, "clock_seconds": None},
        )

    def test_reads_gnu_time_peak_rss_and_keeps_missing_value_empty(self):
        self.assertEqual(
            cfd_diagnostics.parse_gnu_time_v(
                "Command being timed: ./Allrun\n"
                "Maximum resident set size (kbytes): 123456\n"
            ),
            {"peak_rss_kib": 123456},
        )
        self.assertEqual(
            cfd_diagnostics.parse_gnu_time_v("no memory line\n"),
            {"peak_rss_kib": None},
        )


class MpiRuntimeSmokeDiagnosticsTests(unittest.TestCase):
    def test_all_requested_ranks_with_clean_output_pass(self):
        result = cfd_diagnostics.evaluate_mpi_runtime_smoke(
            [
                {
                    "ranks": 2,
                    "returncode": 0,
                    "timed_out": False,
                    "hostname_line_count": 2,
                    "cleanup": {"status": "CLEAN"},
                },
                {
                    "ranks": 4,
                    "returncode": 0,
                    "timed_out": False,
                    "hostname_line_count": 4,
                    "cleanup": {"status": "CLEAN"},
                },
            ]
        )

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["reason_code"], "")

    def test_timeout_is_rank_spawn_blocked_even_if_cleanup_succeeds(self):
        result = cfd_diagnostics.evaluate_mpi_runtime_smoke(
            [
                {
                    "ranks": 2,
                    "returncode": None,
                    "timed_out": True,
                    "hostname_line_count": 0,
                    "cleanup": {"status": "CLEAN"},
                },
            ],
            required_ranks=(2,),
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason_code"], "MPI_RANK_SPAWN_HANG")

    def test_cleanup_uncertainty_never_becomes_mpi_pass(self):
        result = cfd_diagnostics.evaluate_mpi_runtime_smoke(
            [
                {
                    "ranks": 2,
                    "returncode": 0,
                    "timed_out": False,
                    "hostname_line_count": 2,
                    "cleanup": {"status": "UNVERIFIED"},
                },
            ],
            required_ranks=(2,),
        )

        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["reason_code"], "MPI_CLEANUP_UNVERIFIED")


if __name__ == "__main__":
    unittest.main()
