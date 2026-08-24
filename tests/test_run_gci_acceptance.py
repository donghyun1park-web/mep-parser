import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run_gci_acceptance


class RunGciAcceptanceCliTests(unittest.TestCase):
    def test_study_option_resumes_without_creating_a_new_study(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved = {"study": "gci-0123456789ab", "status": "FAIL"}
            completed = {"ok": True, "manifest": {"gate_status": "PASS"}}
            with (
                mock.patch.object(
                    run_gci_acceptance.cfd_gci_job,
                    "load_study",
                    return_value=saved,
                ) as load,
                mock.patch.object(
                    run_gci_acceptance.cfd_gci_job,
                    "create_study",
                ) as create,
                mock.patch.object(
                    run_gci_acceptance.cfd_gci_job,
                    "run_study",
                    return_value=completed,
                ) as run,
                mock.patch.object(
                    run_gci_acceptance,
                    "_refresh_release_readiness",
                    return_value={"ok": True, "manifest": {"status": "PASS"}},
                ) as refresh,
                mock.patch.object(run_gci_acceptance, "_emit") as emit,
            ):
                code = run_gci_acceptance.main(
                    ["--root", str(root), "--study", "gci-0123456789ab"]
                )

            self.assertEqual(code, 0)
            load.assert_called_once_with(root.resolve(), "gci-0123456789ab")
            create.assert_not_called()
            run.assert_called_once()
            self.assertEqual(run.call_args.args[:2], (root.resolve(), "gci-0123456789ab"))
            refresh.assert_called_once_with(root.resolve())
            self.assertEqual(
                emit.call_args_list[-1].args[0]["event"],
                "release_readiness_updated",
            )

    def test_unknown_study_fails_without_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.object(
                    run_gci_acceptance.cfd_gci_job,
                    "load_study",
                    return_value=None,
                ),
                mock.patch.object(
                    run_gci_acceptance.cfd_gci_job,
                    "run_study",
                ) as run,
                mock.patch.object(run_gci_acceptance, "_emit"),
            ):
                code = run_gci_acceptance.main(
                    ["--root", tmp, "--study", "gci-ffffffffffff"]
                )

            self.assertEqual(code, 2)
            run.assert_not_called()

    def test_release_audit_failure_does_not_hide_completed_study(self):
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.object(
                    run_gci_acceptance.cfd_gci_job,
                    "load_study",
                    return_value={"study": "gci-0123456789ab"},
                ),
                mock.patch.object(
                    run_gci_acceptance.cfd_gci_job,
                    "run_study",
                    return_value={"ok": True},
                ),
                mock.patch.object(
                    run_gci_acceptance,
                    "_refresh_release_readiness",
                    side_effect=OSError("audit unavailable"),
                ),
                mock.patch.object(run_gci_acceptance, "_emit") as emit,
            ):
                code = run_gci_acceptance.main(
                    ["--root", tmp, "--study", "gci-0123456789ab"]
                )

            self.assertEqual(code, 0)
            self.assertEqual(
                emit.call_args_list[-1].args[0]["event"],
                "release_readiness_failed",
            )


if __name__ == "__main__":
    unittest.main()
