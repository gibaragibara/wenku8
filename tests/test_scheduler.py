import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class SchedulerTimeoutTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("timeout"), "GNU timeout is required")
    def test_timeout_is_reported_as_cycle_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_python = Path(tmp) / "python"
            fake_python.write_text("#!/bin/sh\nsleep 30\n", encoding="ascii")
            fake_python.chmod(0o755)
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{tmp}{os.pathsep}{env['PATH']}",
                    "ENABLE_ONEDRIVE_UPLOAD": "false",
                    "RUN_ONCE": "true",
                    "SCRAPE_TIMEOUT_SECONDS": "1",
                }
            )

            result = subprocess.run(
                ["sh", str(REPO_ROOT / "run_scheduler.sh")],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )

        self.assertEqual(result.returncode, 0)
        self.assertIn("timed out after 1s (rc=124)", result.stdout)
        self.assertIn("Cycle failed", result.stdout)
        self.assertNotIn("Cycle succeeded", result.stdout)


if __name__ == "__main__":
    unittest.main()
