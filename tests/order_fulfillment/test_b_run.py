"""Reversible B CLI boundary checks; no database, secrets, or external HTTP."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from examples.order_fulfillment import b_run
from examples.order_fulfillment import b_run_isolation as isolation


class ReusableEntryTests(unittest.TestCase):
    def test_case_inventory_and_default_are_explicit(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(b_run.main(['list']), 0)
        self.assertEqual(output.getvalue().splitlines(), list(b_run.CASES))
        self.assertEqual(len(b_run.CASES), 10)

    def test_live_without_new_authorization_never_prepares_database(self):
        with tempfile.TemporaryDirectory() as raw, patch.object(b_run, 'prepare') as prepare:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(b_run.main(['live', '--output', str(Path(raw) / 'fresh')]), 1)
            prepare.assert_not_called()
            self.assertFalse((Path(raw) / 'fresh').exists())

    def test_free_refuses_paid_authorization_before_opening_it(self):
        with tempfile.TemporaryDirectory() as raw, patch.object(b_run, 'read_json') as read:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(b_run.main(['free', '--output', str(Path(raw) / 'new'),
                                             '--authorization', '/never/read.json']), 1)
            read.assert_not_called()

    def test_case_order_and_duplicates_are_rejected(self):
        with tempfile.TemporaryDirectory() as raw, patch.object(b_run, 'prepare') as prepare:
            for cases in [('cq02_normal', 'cq01_intake'), ('cq02_normal', 'cq02_normal')]:
                with self.subTest(cases=cases), contextlib.redirect_stderr(io.StringIO()):
                    args = ['free', '--output', str(Path(raw) / 'new')]
                    for case in cases:
                        args += ['--case', case]
                    self.assertEqual(b_run.main(args), 1)
            prepare.assert_not_called()

    def test_symlink_and_parent_traversal_are_rejected_before_open(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw); target = directory / 'target.json'
            target.write_text('{"private":"sentinel"}')
            alias = directory / 'alias.json'; alias.symlink_to(target)
            for path in (alias, directory / 'missing' / '..' / 'target.json'):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    b_run.read_json(str(path))

    def test_status_rejects_linked_result_file(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw); run = directory / 'run'; run.mkdir()
            secret = directory / 'private.json'; secret.write_text('{"secret":"never-display"}')
            (run / 'shutdown.json').symlink_to(secret)
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(b_run.main(['status', '--output', str(run)]), 1)
            self.assertNotIn('never-display', output.getvalue())

    def test_json_size_is_bounded(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / 'large.json'; path.write_bytes(b' ' * 256001)
            with self.assertRaises(ValueError):
                b_run.read_json(str(path))

    def test_inherited_database_proxy_and_auth_environment_are_not_forwarded(self):
        with patch.dict('os.environ', {'DATABASE_URL': 'private', 'OPENAI_API_KEY': 'secret',
                                      'HTTPS_PROXY': 'private', 'PGSERVICE': 'private'}):
            result = isolation.clean_environment(Path('/private/tmp/example'))
        for name in ('DATABASE_URL', 'OPENAI_API_KEY', 'HTTPS_PROXY', 'PGSERVICE'):
            self.assertNotIn(name, result)
        self.assertTrue(result['PGPASSFILE'].endswith('/no_passfile'))

    def test_existing_output_and_old_database_nesting_are_refused(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            for path in (directory, directory / 'pgdata_old' / 'new',
                         directory / 'task10_historical' / 'new'):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    isolation.prepare(path, mode='free', cases=['cq02_normal'], pg_bin=directory)

    def test_free_policy_has_no_key_or_external_endpoint_exception(self):
        config = dict(output='/private/tmp/owned', socket='/private/tmp/owned-socket',
                      code_files=[], site_packages=[], mode='free', endpoint_ips=['203.0.113.1'])
        policy = isolation.sandbox_policy(config)
        self.assertIn('(deny network*)', policy)
        self.assertNotIn('203.0.113.1', policy)
        self.assertNotIn(str(isolation.ROOT / '.env.local'), policy)
        self.assertIn('(deny file-read-data (regex #"/[.]env', policy)


if __name__ == '__main__':
    unittest.main()
