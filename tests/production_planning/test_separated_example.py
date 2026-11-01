"""Executable demonstration uses real isolated PG and no external model."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'Disposable PG required')
class SeparatedExampleTests(unittest.TestCase):
    def test_three_scenarios_and_exports(self):
        self.assertIsNotNone(importlib.util.find_spec('examples.production_planning_separated_results'))
        from examples.production_planning_separated_results import run_suite
        with tempfile.TemporaryDirectory() as folder:
            result=run_suite(folder)
            normal,omitted,conflict=result['scenarios']
            self.assertEqual(['review_ready','host_result_available_model_rejected','blocked'],[r['status'] for r in result['scenarios']])
            self.assertEqual([3,3,0],[len(r['exports']) for r in result['scenarios']])
            self.assertTrue(normal['model_accepted']);self.assertFalse(omitted['model_accepted'])
            self.assertEqual(0,result['model_api_attempts'])
            self.assertIn('점검',conflict['message'])
            for r in result['scenarios']:
                self.assertTrue(r['business_state_unchanged'])
                for path in r['exports']:self.assertTrue(Path(path).is_file())
            raw=json.loads((Path(folder)/'missing_field/result.json').read_text())
            self.assertIsNone(raw['review']['host_result']['answer']['facts']['schedule']['remaining']['processing_start'])
            model=json.loads(raw['review']['model_review']['runtime']['text'])
            self.assertNotIn('processing_start',model['facts']['schedule']['remaining'])
            self.assertIn('모델 답변', (Path(folder)/'summary.md').read_text())
