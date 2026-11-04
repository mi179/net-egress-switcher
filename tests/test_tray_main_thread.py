import threading
import unittest
from unittest import mock

from egress_switcher.tray import assert_main_thread, invoke_appkit_run, start_tray, tray_supported


class TrayMainThreadTests(unittest.TestCase):
    def test_macos_does_not_start_a_tray_thread(self):
        with mock.patch("egress_switcher.tray.sys.platform", "darwin"), mock.patch(
            "egress_switcher.tray.threading.Thread"
        ) as thread:
            self.assertFalse(tray_supported())
            self.assertIsNone(start_tray(object()))
        thread.assert_not_called()

    def test_appkit_run_is_refused_off_the_main_thread(self):
        errors: list[str] = []
        ran = []

        def worker():
            try:
                invoke_appkit_run(lambda: ran.append("ran"))
            except RuntimeError as exc:
                errors.append(str(exc))

        thread = threading.Thread(target=worker, name="off-main-appkit")
        thread.start()
        thread.join()
        self.assertEqual(ran, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("NSApplication.run", errors[0])
        self.assertIn("主线程", errors[0])

    def test_appkit_run_is_allowed_on_the_main_thread(self):
        calls = []
        invoke_appkit_run(lambda: calls.append(threading.current_thread() is threading.main_thread()))
        self.assertEqual(calls, [True])
        assert_main_thread("NSApplication.run")

    def test_background_thread_cannot_pass_the_guard(self):
        seen = []

        def worker():
            try:
                assert_main_thread("NSApplication.run")
            except RuntimeError as exc:
                seen.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertEqual(len(seen), 1)
