import tempfile
from pathlib import Path
import unittest
from unittest import mock

from scripts import start_gci_background


class StartGciBackgroundTests(unittest.TestCase):
    def test_rejects_invalid_study_id_before_starting(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(
            ValueError, "ID 형식"
        ):
            start_gci_background.start_worker(
                Path(tmp), "not-a-study", Path(tmp) / "out.log", Path(tmp) / "err.log"
            )

    def test_starts_detached_worker_with_project_logs(self):
        study = "gci-0123456789ab"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "projects"
            manifest = root / "_body_gci" / study / "gci_job.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("{}\n", encoding="utf-8")
            stdout = Path(tmp) / "logs" / "stdout.log"
            stderr = Path(tmp) / "logs" / "stderr.log"
            process = mock.Mock(pid=4321)
            with mock.patch.object(
                start_gci_background.subprocess, "Popen", return_value=process
            ) as popen:
                result = start_gci_background.start_worker(
                    root, study, stdout, stderr
                )

            self.assertTrue(result["ok"])
            self.assertEqual(result["pid"], 4321)
            self.assertTrue(stdout.is_file())
            self.assertTrue(stderr.is_file())
            kwargs = popen.call_args.kwargs
            expected_flags = (
                start_gci_background.subprocess.CREATE_NEW_PROCESS_GROUP
                | start_gci_background.subprocess.DETACHED_PROCESS
            )
            self.assertEqual(kwargs["creationflags"], expected_flags)
            self.assertTrue(kwargs["close_fds"])
            self.assertEqual(kwargs["stdin"], start_gci_background.subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()
