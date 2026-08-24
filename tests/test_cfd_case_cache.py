import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
from concurrent.futures import ThreadPoolExecutor

import cfd_case_cache
import cfd_report


LOG = """Time = 1
smoothSolver: Solving for Ux, Initial residual = 1e-4, Final residual = 1e-8, No Iterations 2
smoothSolver: Solving for Uy, Initial residual = 1e-4, Final residual = 1e-8, No Iterations 2
smoothSolver: Solving for Uz, Initial residual = 1e-4, Final residual = 1e-8, No Iterations 2
GAMG: Solving for p_rgh, Initial residual = 1e-4, Final residual = 1e-8, No Iterations 2
smoothSolver: Solving for T, Initial residual = 1e-4, Final residual = 1e-8, No Iterations 2
time step continuity errors : sum local = 1e-8, global = 1e-8, cumulative = 1e-7
End
"""


class CaseSummaryCacheTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[1]
        self.tmp = tempfile.TemporaryDirectory(prefix=".test-case-cache-", dir=self.repo)
        self.case = Path(self.tmp.name)
        (self.case / "cfd_case_meta.json").write_text(json.dumps({
            "config": {
                "name": "cache case",
                "room": {"L": 2, "W": 2, "H": 2},
                "inlet": {"U": [0.3, 0, 0]},
            },
            "mesh": {"cells": 8},
            "heat": {"mode": "none"},
        }), encoding="utf-8")
        self.log = self.case / "log.buoyantBoussinesqSimpleFoam"
        self.log.write_text(LOG, encoding="ascii")

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _metrics(temperature=24.0):
        return {
            "T_avg_C": temperature,
            "T_max_C": temperature + 1.0,
            "dT_rise": temperature - 20.0,
            "closure_pct": None,
        }

    def test_cache_hit_skips_log_and_field_parsers(self):
        with mock.patch.object(cfd_report, "field_metrics", return_value=self._metrics()):
            first = cfd_report.case_summary(self.case)

        with mock.patch.object(cfd_report, "parse_log", side_effect=AssertionError("log parser called")), \
             mock.patch.object(cfd_report, "field_metrics", side_effect=AssertionError("field parser called")):
            second = cfd_report.case_summary(self.case)

        self.assertEqual(second, first)
        self.assertTrue((self.case / "cfd_case_summary.cache.v1.json").is_file())

    def test_log_change_invalidates_cache_and_recomputes(self):
        with mock.patch.object(cfd_report, "field_metrics", return_value=self._metrics(24.0)):
            cfd_report.case_summary(self.case)

        self.log.write_text(LOG + "# changed\n", encoding="ascii")
        original = cfd_report.parse_log
        with mock.patch.object(cfd_report, "parse_log", wraps=original) as parse_mock, \
             mock.patch.object(cfd_report, "field_metrics", return_value=self._metrics(27.0)):
            summary = cfd_report.case_summary(self.case)

        self.assertEqual(summary["T_avg_C"], 27.0)
        self.assertEqual(parse_mock.call_count, 1)

    def test_volume_heat_metadata_requires_energy_balance_metrics(self):
        meta_path = self.case / "cfd_case_meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["heat"] = {"mode": "volume", "power_w": 1000.0}
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        parsed = {
            "crashed": False,
            "continuity_global": [(1000, 1e-7)],
            "n_iters": 1000,
            "residuals": {
                field: [1e-4]
                for field in ("Ux", "Uy", "Uz", "p_rgh", "T", "k", "epsilon")
            },
        }
        metrics = self._metrics(25.0)
        metrics["U_max"] = 0.4

        with mock.patch.object(cfd_report, "parse_log", return_value=parsed), \
             mock.patch.object(cfd_report, "field_metrics", return_value=metrics):
            summary = cfd_report.case_summary(self.case)

        self.assertEqual(summary["citation_status"], "NOT_EVALUATED")
        self.assertFalse(summary["citable"])
        self.assertIn("field_metrics", summary["blockers"])

    def test_unreadable_solver_log_returns_structured_hold(self):
        with mock.patch.object(
            cfd_report,
            "_read_solver_log",
            side_effect=PermissionError("access denied"),
        ):
            summary = cfd_report.case_summary(self.case)

        self.assertEqual(summary["status"], "unreadable")
        self.assertEqual(summary["result_status"], "NOT_EVALUATED")
        self.assertFalse(summary["citable"])
        self.assertIn("solver_log_unreadable", summary["blockers"])
        self.assertFalse((self.case / "cfd_case_summary.cache.v1.json").exists())

    def test_changed_meta_invalidates_cache(self):
        with mock.patch.object(cfd_report, "field_metrics", return_value=self._metrics(24.0)):
            cfd_report.case_summary(self.case)

        meta = json.loads((self.case / "cfd_case_meta.json").read_text(encoding="utf-8"))
        meta["heat"] = {"mode": "uniform", "total_kw": 2.0}
        (self.case / "cfd_case_meta.json").write_text(json.dumps(meta), encoding="utf-8")
        original = cfd_report.parse_log
        with mock.patch.object(cfd_report, "parse_log", wraps=original) as parse_mock, \
             mock.patch.object(cfd_report, "field_metrics", return_value=self._metrics(26.0)):
            summary = cfd_report.case_summary(self.case)

        self.assertEqual(summary["T_avg_C"], 26.0)
        self.assertEqual(parse_mock.call_count, 1)

    def test_opening_verification_artifact_invalidates_cache(self):
        with mock.patch.object(cfd_report, "field_metrics", return_value=self._metrics(24.0)):
            cfd_report.case_summary(self.case)

        verification = self.case / "opening_boundary_verification.v1.json"
        verification.write_text(json.dumps({
            "contract": "opening_boundary_verification.v1", "status": "PARTIAL",
        }), encoding="utf-8")
        original = cfd_report.parse_log
        with mock.patch.object(cfd_report, "parse_log", wraps=original) as parse_mock, \
             mock.patch.object(cfd_report, "field_metrics", return_value=self._metrics(26.5)):
            summary = cfd_report.case_summary(self.case)

        self.assertEqual(summary["T_avg_C"], 26.5)
        self.assertEqual(parse_mock.call_count, 1)
        self.assertEqual(summary["opening_verification_status"], "PARTIAL")

    def test_changed_latest_or_energy_field_invalidates_cache(self):
        time_dir = self.case / "1"
        time_dir.mkdir()
        for name in ("T", "U", "phi"):
            (time_dir / name).write_text("initial", encoding="ascii")
        with mock.patch.object(cfd_report, "field_metrics", return_value=self._metrics(24.0)):
            cfd_report.case_summary(self.case)

        for index, field_name in enumerate(("T", "U", "phi"), start=1):
            with self.subTest(field=field_name):
                (time_dir / field_name).write_text(f"changed-{field_name}", encoding="ascii")
                original = cfd_report.parse_log
                with mock.patch.object(cfd_report, "parse_log", wraps=original) as parse_mock, \
                     mock.patch.object(cfd_report, "field_metrics",
                                       return_value=self._metrics(27.0 + index)):
                    summary = cfd_report.case_summary(self.case)

                self.assertEqual(summary["T_avg_C"], 27.0 + index)
                self.assertEqual(parse_mock.call_count, 1)

    def test_changed_numeric_time_directories_invalidate_cache(self):
        time_dir = self.case / "1"
        time_dir.mkdir()
        for name in ("T", "U", "phi"):
            (time_dir / name).write_text("initial", encoding="ascii")
        with mock.patch.object(cfd_report, "field_metrics", return_value=self._metrics(24.0)):
            cfd_report.case_summary(self.case)

        next_time = self.case / "2"
        next_time.mkdir()
        for name in ("T", "U", "phi"):
            (next_time / name).write_text("next", encoding="ascii")
        original = cfd_report.parse_log
        with mock.patch.object(cfd_report, "parse_log", wraps=original) as parse_mock, \
             mock.patch.object(cfd_report, "field_metrics", return_value=self._metrics(29.0)):
            summary = cfd_report.case_summary(self.case)

        self.assertEqual(summary["T_avg_C"], 29.0)
        self.assertEqual(parse_mock.call_count, 1)

    def test_producer_revision_invalidates_cache(self):
        with mock.patch.object(cfd_report, "field_metrics", return_value=self._metrics(24.0)):
            cfd_report.case_summary(self.case)

        original = cfd_report.parse_log
        with mock.patch.object(cfd_case_cache, "SUMMARY_PRODUCER_REVISION", "test-new-revision"), \
             mock.patch.object(cfd_report, "parse_log", wraps=original) as parse_mock, \
             mock.patch.object(cfd_report, "field_metrics", return_value=self._metrics(30.0)):
            summary = cfd_report.case_summary(self.case)

        self.assertEqual(summary["T_avg_C"], 30.0)
        self.assertEqual(parse_mock.call_count, 1)
        payload = json.loads((self.case / cfd_case_cache.CACHE_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(payload["fingerprint"]["producer_revision"], "test-new-revision")

    def test_cache_hit_is_rechecked_when_source_changes_during_load(self):
        with mock.patch.object(cfd_report, "field_metrics", return_value=self._metrics(24.0)):
            cfd_report.case_summary(self.case)

        original_load = cfd_case_cache.load
        mutated = False

        def load_then_mutate(*args, **kwargs):
            nonlocal mutated
            cached = original_load(*args, **kwargs)
            if not mutated:
                mutated = True
                self.log.write_text(LOG + "# changed during cache read\n", encoding="ascii")
            return cached

        original = cfd_report.parse_log
        with mock.patch.object(cfd_case_cache, "load", side_effect=load_then_mutate), \
             mock.patch.object(cfd_report, "parse_log", wraps=original) as parse_mock, \
             mock.patch.object(cfd_report, "field_metrics", return_value=self._metrics(31.0)):
            summary = cfd_report.case_summary(self.case)

        self.assertEqual(summary["T_avg_C"], 31.0)
        self.assertEqual(parse_mock.call_count, 1)

    def test_concurrent_requests_share_one_uncached_summary(self):
        started = threading.Event()
        release = threading.Event()

        def blocking_metrics(*_args, **_kwargs):
            started.set()
            self.assertTrue(release.wait(timeout=5))
            return self._metrics(32.0)

        with mock.patch.object(cfd_report, "field_metrics", side_effect=blocking_metrics) as metrics_mock:
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(cfd_report.case_summary, self.case)
                self.assertTrue(started.wait(timeout=5))
                second = executor.submit(cfd_report.case_summary, self.case)
                release.set()
                first_summary = first.result(timeout=10)
                second_summary = second.result(timeout=10)

        self.assertEqual(first_summary["T_avg_C"], 32.0)
        self.assertEqual(second_summary, first_summary)
        self.assertEqual(metrics_mock.call_count, 1)

    def test_corrupt_cache_is_ignored_and_rebuilt(self):
        cache = self.case / "cfd_case_summary.cache.v1.json"
        cache.write_text("not json", encoding="ascii")

        with mock.patch.object(cfd_report, "field_metrics", return_value=self._metrics(25.0)):
            summary = cfd_report.case_summary(self.case)

        self.assertEqual(summary["T_avg_C"], 25.0)
        payload = json.loads(cache.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], "case-summary-cache.v1")


if __name__ == "__main__":
    unittest.main()
