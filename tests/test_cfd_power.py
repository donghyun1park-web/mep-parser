import unittest
from unittest import mock

import cfd_power


class PowerGuardTests(unittest.TestCase):
    def test_keep_awake_sets_and_clears_the_thread_request(self):
        with mock.patch.object(
                cfd_power, "_set_system_required", side_effect=[True, True]
        ) as setter:
            with cfd_power.keep_system_awake() as acquired:
                self.assertTrue(acquired)
            self.assertEqual(setter.call_args_list, [mock.call(True), mock.call(False)])

    def test_failed_power_hint_does_not_attempt_a_clear(self):
        with mock.patch.object(
                cfd_power, "_set_system_required", return_value=False
        ) as setter:
            with cfd_power.keep_system_awake() as acquired:
                self.assertFalse(acquired)
            setter.assert_called_once_with(True)

    def test_context_clears_the_request_after_an_exception(self):
        with mock.patch.object(
                cfd_power, "_set_system_required", side_effect=[True, True]
        ) as setter:
            with self.assertRaisesRegex(RuntimeError, "solver stopped"):
                with cfd_power.keep_system_awake():
                    raise RuntimeError("solver stopped")
            self.assertEqual(setter.call_args_list, [mock.call(True), mock.call(False)])


if __name__ == "__main__":
    unittest.main()
