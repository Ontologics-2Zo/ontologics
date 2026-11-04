"""Run shipped claim UI logic against deterministic DOM/API boundaries (no database)."""
import shutil
import subprocess
import unittest
from pathlib import Path


class ClaimsUiTests(unittest.TestCase):
    def test_claim_selection_local_demo_and_partial_results(self):
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node.js is required for the claim UI regression test')
        root = Path(__file__).resolve().parents[2]
        result = subprocess.run([node, str(Path(__file__).with_name('claims_ui_test.js'))],
                                cwd=root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
