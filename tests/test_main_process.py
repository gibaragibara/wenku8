import sys
import time
import unittest

import main


class ProcessTimeoutTest(unittest.TestCase):
    def test_process_group_is_stopped_at_hard_timeout(self):
        started = time.monotonic()

        rc = main.run_process_group(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout_seconds=1,
        )

        self.assertEqual(rc, 124)
        self.assertLess(time.monotonic() - started, 5)

    def test_successful_process_status_is_preserved(self):
        rc = main.run_process_group(
            [sys.executable, "-c", "raise SystemExit(7)"],
            timeout_seconds=5,
        )

        self.assertEqual(rc, 7)


if __name__ == "__main__":
    unittest.main()
