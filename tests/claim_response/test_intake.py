"""Mail, Excel and CSV claims convert to the same reviewable ClaimIntake draft."""
import email
from email import policy
from email.message import EmailMessage
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
import zipfile

from ontologics.claim_response.intake import convert_claim
from ontologics.model import ALLOWED_FIELDS, ROOT, validate_graph

CLAIMS = ROOT / 'data/claim_response/claims'
FILES = {'eml': CLAIMS / 'claim_mail.eml', 'xlsx': CLAIMS / 'claim_sheet.xlsx', 'csv': CLAIMS / 'claim_table.csv'}
RECEIVED = '2026-10-26T10:30:00+09:00'


def intake_of(result):
    return next((n for n in result['nodes'] if n['type'] == 'ClaimIntake'), None)


def codes(result):
    return {issue['code'] for issue in result['report']['issues']}


class ClaimIntakeConversionTests(unittest.TestCase):
    def setUp(self):
        self.results = {name: convert_claim(path, collected_at=RECEIVED) for name, path in FILES.items()}
        self.expected = json.loads((CLAIMS / 'expected_intake.json').read_text(encoding='utf-8'))

    def test_three_formats_give_the_same_claim(self):
        nodes = {name: dict(intake_of(r)) for name, r in self.results.items()}
        for node in nodes.values():
            node.pop('evidence')
        self.assertEqual(nodes['eml'], nodes['xlsx'])
        self.assertEqual(nodes['eml'], nodes['csv'])
        reports = {name: r['report'] for name, r in self.results.items()}
        for key in ('claim', 'claimed_quantity', 'product_reference', 'configuration_candidate_id'):
            self.assertEqual(reports['eml'][key], reports['xlsx'][key], key)
            self.assertEqual(reports['eml'][key], reports['csv'][key], key)

    def test_matches_expected_example(self):
        e = self.expected
        for name, result in self.results.items():
            with self.subTest(file=name):
                node, report = intake_of(result), result['report']
                for field in ('external_claim_id', 'customer_name', 'reported_at', 'reported_symptom',
                              'reported_conditions', 'requested_action', 'delivery_reference',
                              'claimed_quantity', 'intake_state'):
                    self.assertEqual(e[field], node[field], field)
                self.assertEqual([u['serial'] for u in e['unit_candidates']], node['reported_serials'])
                self.assertEqual(e['product_reference'], report['product_reference'])
                self.assertEqual(e['configuration_candidate_id'], report['configuration_candidate_id'])
                self.assertEqual(e['requires_separate_trace_data'], report['requires_separate_trace_data'])
                self.assertEqual(e['quality_case_id'], report['quality_case_id'])
                candidates = [{k: u[k] for k in ('serial', 'quantity', 'unit', 'unit_id', 'match_state')}
                              for u in report['unit_candidates']]
                self.assertEqual(e['unit_candidates'], candidates)
                self.assertIn(report['sha256'], {s['sha256'] for s in e['source_files']})

    def test_each_file_is_a_valid_evidence_backed_graph_draft(self):
        for name, result in self.results.items():
            with self.subTest(file=name):
                outcome = validate_graph(result['nodes'])
                self.assertEqual([], outcome['errors'])
                self.assertEqual([], outcome['warnings'])
                self.assertEqual('needs_review', result['report']['state'])
                source = result['nodes'][0]
                self.assertEqual(hashlib.sha256((ROOT / source['path']).read_bytes()).hexdigest(), source['sha256'])
                self.assertTrue(source['path'].startswith('data/claim_response/claims/'))

    def test_never_confirms_links_or_invents_trace_facts(self):
        for name, result in self.results.items():
            with self.subTest(file=name):
                node = intake_of(result)
                self.assertEqual('needs_review', node['intake_state'])
                self.assertLessEqual(set(node), ALLOWED_FIELDS['ClaimIntake'])
                self.assertEqual(5, node['claimed_quantity'])  # alternatives, never 15 or three claims
                self.assertTrue(all(u['unit_id'] is None and u['match_state'] == 'needs_lookup'
                                    for u in result['report']['unit_candidates']))
                self.assertEqual({'origin': 'synthetic'}, {'origin': n['origin'] for n in result['nodes']})

    def test_locators_point_to_rows_cells_and_mail_lines(self):
        self.assertEqual('클레임!A1:M6', self.results['xlsx']['report']['locator'])
        self.assertEqual('클레임!A2:M2', self.results['xlsx']['report']['unit_candidates'][0]['locator'])
        self.assertEqual('row 2', self.results['csv']['report']['unit_candidates'][0]['locator'])
        self.assertTrue(self.results['eml']['report']['locator'].startswith('text/plain body lines '))


class BrokenClaimTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.csv_text = FILES['csv'].read_bytes().decode('utf-8-sig')

    def write(self, name, data):
        path = self.folder / name
        path.write_bytes(data if isinstance(data, bytes) else data.encode('utf-8-sig'))
        return path

    def csv_with(self, old, new, count=0):
        self.assertIn(old, self.csv_text)
        return self.write('claim.csv', self.csv_text.replace(old, new, count) if count else self.csv_text.replace(old, new))

    def mail_with(self, old, new, *, date=None):
        source = email.message_from_bytes(FILES['eml'].read_bytes(), policy=policy.default)
        body = source.get_body(preferencelist=('plain',)).get_content()
        self.assertIn(old, body)
        message = EmailMessage()
        for header in ('From', 'To', 'Subject'):
            message[header] = source[header]
        message['Date'] = date or source['Date']
        message.set_content(body.replace(old, new))
        return self.write('claim.eml', message.as_bytes())

    def convert(self, path):
        return convert_claim(path, collected_at=RECEIVED)

    def assertBlocked(self, result, code):
        self.assertEqual('blocked', result['report']['state'])
        self.assertIn(code, codes(result))
        self.assertIsNone(intake_of(result))

    def test_structural_errors_fail_closed(self):
        for old, new, code in (('"고객사"', '"고객 이름"', 'MISSING_HEADER'),
                               ('"증상"', '"고객사"', 'DUPLICATE_HEADER')):
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, code):
                self.convert(self.csv_with(old, new))
        header = self.csv_text.splitlines()[0]
        with self.assertRaisesRegex(ValueError, 'NO_CLAIM_ROWS'):
            self.convert(self.write('claim.csv', header + '\r\n'))
        with self.assertRaisesRegex(ValueError, 'UNSUPPORTED_FORMAT'):
            self.convert(self.write('claim.txt', self.csv_text))
        with self.assertRaisesRegex(ValueError, 'MISSING_TEXT_BODY'):
            self.convert(self.write('claim.eml', b'Subject: empty\r\nContent-Type: application/pdf\r\n\r\nx'))

    def test_times_need_an_explicit_timezone(self):
        self.assertBlocked(self.convert(self.csv_with('10:00:00+09:00', '10:00:00')), 'TIMEZONE_REQUIRED')
        self.assertBlocked(self.convert(self.csv_with('2026-10-26T10:00:00+09:00', '2026-10-26')), 'INVALID_TIME')
        self.assertBlocked(self.convert(self.mail_with('오전 10시(KST)', '오전 10시')), 'TIMEZONE_REQUIRED')

    def test_mail_send_time_is_not_the_reported_time(self):
        result = self.convert(self.mail_with('안녕하세요.', '안녕하세요.', date='Tue, 27 Oct 2026 18:30:00 +0900'))
        self.assertEqual('2026-10-26T10:00:00+09:00', intake_of(result)['reported_at'])
        afternoon = self.convert(self.mail_with('오전 10시', '오후 3시 20분'))
        self.assertEqual('2026-10-26T15:20:00+09:00', intake_of(afternoon)['reported_at'])

    def test_missing_mail_phrases_stay_missing(self):
        result = self.convert(self.mail_with('고객 클레임 번호는 CLM-DEMO-C-20261026-001이고, ', ''))
        self.assertBlocked(result, 'MISSING_FIELD')
        self.assertIsNone(result['report']['claim']['external_claim_id'])
        optional = self.convert(self.mail_with('발생 조건: 실내 설치 후 최초 기동. 동일 조건의 다른 보드는 정상\n', ''))
        self.assertEqual('needs_review', optional['report']['state'])
        self.assertNotIn('reported_conditions', intake_of(optional))

    def test_bad_quantities_and_serials_are_reported(self):
        self.assertBlocked(self.convert(self.csv_with('"C-D003","1"', '"C-D003","0"')), 'INVALID_QUANTITY')
        self.assertBlocked(self.convert(self.csv_with('"C-D003","1"', '"C-D003","1.5"')), 'INVALID_QUANTITY')
        self.assertBlocked(self.convert(self.csv_with('"C-D004"', '""')), 'MISSING_FIELD')
        self.assertBlocked(self.convert(self.csv_with('"C-D004"', '"C-D001"')), 'DUPLICATE_SERIAL')

    def test_rows_must_agree_on_claim_values(self):
        result = self.convert(self.csv_with('"가정 고객사 C-01"', '"가정 고객사 C-02"', 1))
        self.assertBlocked(result, 'ROW_VALUE_CONFLICT')

    def test_quantity_serial_mismatch_is_a_warning_for_people(self):
        result = self.convert(self.csv_with('"C-D005","1"', '"C-D005","2"'))
        self.assertEqual('needs_review', result['report']['state'])
        self.assertEqual(6, intake_of(result)['claimed_quantity'])
        self.assertEqual(['CLAIM_QUANTITY_SERIAL_MISMATCH'], [w['code'] for w in result['report']['warnings']])

    def test_excel_needs_the_claim_sheet_and_no_formulas(self):
        def rewrite(name, change):
            buffer = io.BytesIO()
            with zipfile.ZipFile(FILES['xlsx']) as source, zipfile.ZipFile(buffer, 'w') as target:
                for item in source.infolist():
                    data = source.read(item.filename)
                    target.writestr(item, change(data.decode('utf-8')).encode('utf-8') if item.filename == name else data)
            return self.write('claim.xlsx', buffer.getvalue())
        renamed = rewrite('xl/workbook.xml', lambda xml: xml.replace('name="클레임"', 'name="Sheet1"'))
        with self.assertRaisesRegex(ValueError, 'MISSING_SHEET'):
            self.convert(renamed)
        formula = rewrite('xl/worksheets/sheet1.xml',
                          lambda xml: re.sub(r'(<x:c r="I2"[^>]*>)', r'\1<x:f>1</x:f>', xml, count=1))
        with self.assertRaisesRegex(ValueError, 'UNSUPPORTED_FORMULA'):
            self.convert(formula)


if __name__ == '__main__':
    unittest.main()
