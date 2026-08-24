import importlib.util
from pathlib import Path
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hold_system_awake.py"
SPEC = importlib.util.spec_from_file_location("hold_system_awake", SCRIPT)
hold_system_awake = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(hold_system_awake)


class HoldSystemAwakeTests(unittest.TestCase):
    def test_windows_process_probe_uses_a_read_only_kernel_handle(self):
        kernel32 = mock.MagicMock()
        kernel32.OpenProcess.return_value = 77
        kernel32.GetExitCodeProcess.side_effect = (
            lambda handle, code: setattr(code._obj, "value", 259) or 1
        )
        with mock.patch.object(hold_system_awake.os, "name", "nt"), \
             mock.patch.object(
                 hold_system_awake.ctypes, "WinDLL", return_value=kernel32
             ):
            self.assertTrue(hold_system_awake._process_exists(123))
        kernel32.OpenProcess.assert_called_once_with(0x1000, False, 123)
        kernel32.CloseHandle.assert_called_once_with(77)

    def test_windows_access_denied_still_means_process_is_alive(self):
        kernel32 = mock.MagicMock()
        kernel32.OpenProcess.return_value = 0
        with mock.patch.object(hold_system_awake.os, "name", "nt"), \
             mock.patch.object(
                 hold_system_awake.ctypes, "WinDLL", return_value=kernel32
             ), mock.patch.object(
                 hold_system_awake.ctypes, "get_last_error", return_value=5
             ):
            self.assertTrue(hold_system_awake._process_exists(123))

    def test_guard_tracks_the_requested_process_until_it_exits(self):
        guard = mock.MagicMock()
        guard.__enter__.return_value = True
        with mock.patch.object(
                hold_system_awake, "_process_exists",
                side_effect=[True, True, False]
        ), mock.patch.object(
                hold_system_awake.cfd_power, "keep_system_awake",
                return_value=guard
        ), mock.patch.object(hold_system_awake.time, "sleep") as sleep:
            self.assertEqual(
                hold_system_awake.main(["--pid", "123", "--poll-seconds", "1"]),
                0,
            )
        sleep.assert_called_once_with(1.0)
        guard.__exit__.assert_called_once()

    def test_missing_process_does_not_acquire_the_guard(self):
        with mock.patch.object(
                hold_system_awake, "_process_exists", return_value=False
        ), mock.patch.object(
                hold_system_awake.cfd_power, "keep_system_awake"
        ) as guard:
            self.assertEqual(hold_system_awake.main(["--pid", "123"]), 3)
        guard.assert_not_called()


if __name__ == "__main__":
    unittest.main()
