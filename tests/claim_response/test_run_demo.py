"""The C example runs end to end on a fresh schema and stops at human review."""
from pathlib import Path
import tempfile
import unittest

from examples.claim_response.run_demo import CLAIMS, run_demo
from tests.db import database_for_test


class RunDemoTests(unittest.TestCase):
    def run_once(self, claim):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        return run_demo(database_for_test(self), claim_path=claim, request_dir=folder.name), Path(folder.name)

    def test_csv_claim_runs_from_file_to_unsent_request_drafts(self):
        summary, folder = self.run_once(CLAIMS / 'claim_table.csv')
        self.assertEqual({'seed': 1, 'claim_registered': 2, 'claim_confirmed': 3, 'after_request_drafts': 3},
                         summary['versions'])
        self.assertEqual(['unit:C-D00%d' % n for n in range(1, 6)], summary['links']['matched'])
        self.assertEqual([], summary['links']['open'])
        self.assertEqual('production-line:demo:C-01', summary['production_line_id'])
        self.assertEqual(('ready', 120, 'C-S001 ~ C-S120'),
                         (summary['hold_scope']['state'], summary['hold_scope']['count'], summary['hold_scope']['units']))
        self.assertEqual(['c_operations_record'], summary['hold_scope']['basis'])
        self.assertEqual((75, 'C-D006 ~ C-D080'), (summary['candidate_scope']['count'], summary['candidate_scope']['units']))
        self.assertEqual(['production_stop', 'shipment_hold'], sorted(r['kind'] for r in summary['requests']))
        self.assertEqual(('awaiting_review', '미검토', '미송부', '미확인'),
                         tuple(summary['request_draft'][k] for k in ('status', 'review_label', 'sending', 'on_site')))
        files = sorted(folder.glob('*.md'))
        self.assertEqual(2, len(files))
        self.assertEqual(sorted(str(f) for f in files), sorted(str(Path(d)) for d in summary['documents']))
        text = next(f for f in files if f.name.endswith('shipment_hold.md')).read_text(encoding='utf-8')
        self.assertIn('C-S001 ~ C-S120 (120대)', text)
        self.assertIn('| 송부 상태 | 미송부 |', text)

    def test_mail_and_excel_claims_reach_the_same_result(self):
        csv, _ = self.run_once(CLAIMS / 'claim_table.csv')
        for name in ('claim_mail.eml', 'claim_sheet.xlsx'):
            with self.subTest(file=name):
                other, _ = self.run_once(CLAIMS / name)
                for key in ('links', 'production_line_id', 'quality_case_id', 'hold_scope', 'candidate_scope'):
                    self.assertEqual(csv[key], other[key], key)


if __name__ == '__main__':
    unittest.main()
