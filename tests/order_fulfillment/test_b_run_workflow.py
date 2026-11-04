"""The retired stored-graph campaign cannot start through its Python API."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from examples.order_fulfillment import b_run_workflow as workflow


class WorkflowTests(unittest.TestCase):
    def test_stored_campaign_rejects_before_input_database_or_output_access(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for offline in (True, False):
                with self.subTest(offline=offline), patch('psycopg.connect') as connect, \
                        patch('ontologics.database.environment_values') as environment, \
                        patch.object(Path, 'mkdir') as mkdir, patch.object(Path, 'open') as opened:
                    with self.assertRaisesRegex(workflow.Blocked, 'B_LEGACY_EXECUTION_RETIRED'):
                        workflow.run_campaign(database_url='do-not-open', input_root=root / 'missing',
                            output=root / 'new', cases=['cq01_intake'], offline=offline)
                    connect.assert_not_called(); environment.assert_not_called()
                    mkdir.assert_not_called(); opened.assert_not_called()
            self.assertFalse((root / 'new').exists())
