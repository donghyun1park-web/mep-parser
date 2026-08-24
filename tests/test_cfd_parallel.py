import tempfile
import unittest
from pathlib import Path

import cfd_parallel


def _capabilities(*, smoke="PASS", cpu=8, ready=True):
    return {
        "contract": "runtime_capability.v1",
        "parallel_runtime_ready": ready,
        "mpi": {"execution_smoke": smoke},
        "cpu": {"effective_logical_count": cpu},
    }


class ParallelPlanTests(unittest.TestCase):
    def test_missing_smoke_forces_serial_even_for_large_requested_case(self):
        plan = cfd_parallel.choose_parallel_plan(
            "body_fitted_restart",
            cell_count=100_000,
            requested_ranks=8,
            capabilities=_capabilities(smoke="NOT_RUN"),
        )

        self.assertEqual(plan.mode, "serial")
        self.assertEqual(plan.ranks, 1)
        self.assertIn("mpi_execution_smoke_not_passed", plan.blockers)

    def test_requested_rank_above_effective_cpu_is_blocked_not_oversubscribed(self):
        plan = cfd_parallel.choose_parallel_plan(
            "legacy_steady",
            cell_count=200_000,
            requested_ranks=8,
            capabilities=_capabilities(cpu=4),
        )

        self.assertEqual(plan.mode, "serial")
        self.assertIn("requested_ranks_exceed_effective_cpu", plan.blockers)

    def test_explicit_failed_runtime_proof_cannot_fall_back_to_static_ready_flag(self):
        capabilities = _capabilities(smoke="PASS", ready=False)
        capabilities["parallel_ready"] = True  # legacy command-discovery field
        plan = cfd_parallel.choose_parallel_plan(
            "legacy_steady", cell_count=200_000, requested_ranks=4,
            capabilities=capabilities,
        )

        self.assertEqual(plan.mode, "serial")
        self.assertIn("parallel_runtime_not_ready", plan.blockers)

    def test_verified_large_case_emits_mpi_plan_and_traceable_artifact(self):
        plan = cfd_parallel.choose_parallel_plan(
            "body_fitted_restart",
            cell_count=100_000,
            requested_ranks=4,
            capabilities=_capabilities(),
        )

        self.assertEqual(plan.mode, "mpi")
        self.assertEqual(plan.ranks, 4)
        self.assertEqual(plan.decomposition, "scotch")
        self.assertEqual(plan.fallback_chain, ("scotch", "hierarchical", "simple"))

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "parallel_run.v1.json"
            payload = cfd_parallel.write_parallel_run(
                target, plan, case_kind="body_fitted_restart", input_sha256="a" * 64
            )
            self.assertEqual(payload["contract"], "parallel_run.v1")
            self.assertEqual(payload["plan"]["mode"], "mpi")
            self.assertEqual(payload["input_sha256"], "a" * 64)
            self.assertTrue(target.is_file())
