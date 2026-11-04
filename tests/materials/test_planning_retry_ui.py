"""Exercise the shipped planning buttons and common request-key storage in Node."""
import shutil
import subprocess
import unittest
from pathlib import Path


class PlanningRetryUiTests(unittest.TestCase):
    def test_review_buttons_reuse_pending_keys_until_success(self):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node.js is required for the planning UI regression test')
        root = Path(__file__).resolve().parents[2]
        result = subprocess.run([node, str(Path(__file__).with_name('planning_retry_ui_test.js'))],
                                cwd=root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
