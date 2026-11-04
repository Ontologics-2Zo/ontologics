"""C screen proposals preserve original bytes and approved-version document contents."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ontologics.business_drafts import BusinessProposal, preserve_proposal_sources
from ontologics.claim_response import workspace
from ontologics.model import ROOT, validate_graph
from ontologics.service import DomainError
from tests.claim_response import test_workspace as flow


CLAIM_FILE = '1_메일_전수시험_클레임.eml'
RESULT_FILE = '1_테스트팀_결과.csv'


class ClaimWorkspaceIntegrityTests(unittest.TestCase):
    def setUp(self):
        # Compose the existing scenario helper without collecting its test methods twice.
        self.flow = flow.ClaimFileFlowTests()
        self.flow.setUp()
        self.addCleanup(self.flow.doCleanups)

    def source_for(self, record, kind):
        sources = [self.flow.index[self.flow.index[eid]['source_id']]
                   for eid in record['evidence']
                   if self.flow.index[eid].get('source_id') in self.flow.index]
        found = [source for source in sources if source.get('kind') == kind]
        self.assertEqual(1, len(found), kind)
        return found[0]

    def assert_preserved(self, record, kind, original, locator_fragment):
        source = self.source_for(record, kind)
        raw = original.read_bytes()
        self.assertTrue(source['path'].startswith('data/receipts/business-file-'))
        self.assertEqual(raw, (ROOT / source['path']).read_bytes())
        self.assertEqual(hashlib.sha256(raw).hexdigest(), source['sha256'])
        evidence = [self.flow.index[eid] for eid in record['evidence']
                    if self.flow.index[eid].get('source_id') == source['id']]
        self.assertTrue(any(locator_fragment in item['locator'] for item in evidence))

    def test_original_claim_and_result_hash_locator_survive_common_source_preservation(self):
        proposal = workspace.build_records(list(self.flow.index.values()), 'intake_file',
                                           {'file': CLAIM_FILE, 'collected_at': flow.FLOW_AT})
        self.assertIsInstance(proposal, BusinessProposal)
        self.assertTrue(proposal.attachments)
        # A mismatched attachment cannot become a different original under the same source ID.
        changed = BusinessProposal(proposal.records, proposal.sources,
            {key: raw + b'changed' for key, raw in proposal.attachments.items()}, proposal.record_evidence)
        with self.assertRaises(DomainError) as refused:
            preserve_proposal_sources(changed, self.flow.index)
        self.assertEqual('SOURCE_HASH_MISMATCH', refused.exception.code)
        case = self.flow.run_to_test_request(CLAIM_FILE)
        self.assert_preserved(self.flow.one('ClaimIntake'), 'synthetic_claim',
                              workspace.INBOX / CLAIM_FILE, 'text/plain')
        self.flow.step('test_results', {'case_id': case['id'], 'file': RESULT_FILE,
                                       'collected_at': flow.FLOW_AT})
        result = next(n for n in self.flow.index.values() if n['type'] == 'QualityTestResult')
        self.assert_preserved(result, 'synthetic_test_results', workspace.INBOX / RESULT_FILE, 'CSV header row 1')
        proposal = workspace.build_records(list(self.flow.index.values()), 'test_results',
            {'case_id': case['id'], 'file': RESULT_FILE, 'collected_at': flow.FLOW_AT})
        source = self.source_for(result, 'synthetic_test_results')
        with tempfile.TemporaryDirectory(dir=ROOT / 'data/receipts') as folder:
            altered = Path(folder) / 'altered.csv'
            altered.write_bytes(b'changed')
            index = {**self.flow.index, source['id']: {**source, 'path': altered.relative_to(ROOT).as_posix()}}
            with self.assertRaises(DomainError) as refused:
                preserve_proposal_sources(proposal, index)
            self.assertEqual('SOURCE_HASH_MISMATCH', refused.exception.code)

    def test_saved_request_documents_render_approved_targets_without_rebuilding_scope(self):
        case = self.flow.run_to_test_request(CLAIM_FILE)
        request = self.flow.one('TestRequest')
        request['unit_ids'] = request['unit_ids'][:1]
        hold_request = next(n for n in self.flow.index.values()
                            if n['type'] == 'ContainmentRequest' and n['request_kind'] == 'shipment_hold')
        scope = self.flow.index[hold_request['scope_id']]
        scope['unit_ids'] = ['unit:C-S001']
        self.assertTrue(validate_graph(list(self.flow.index.values()))['valid'])
        with patch.object(workspace, '_operations', side_effect=AssertionError('must use approved records')):
            test = self.flow.view('test_request_document', case_id=case['id'])
            containment = self.flow.view('containment_documents', case_id=case['id'])
        self.assertEqual((True, 1), (test['saved'], test['unit_count']))
        self.assertIn('합계 1대.', test['documents'][0]['markdown'])
        shipment = next(d['markdown'] for d in containment['documents'] if '출고 배송 중단' in d['markdown'])
        self.assertIn('C-S001 (1대)', shipment)
        self.assertNotIn('(120대)', shipment)

    def test_reproduction_due_date_reads_verified_original_after_inbox_changes(self):
        case = self.flow.run_to_test_request(CLAIM_FILE)
        self.flow.register_results(case, RESULT_FILE)
        self.flow.step('reproduction', {'case_id': case['id'], 'collected_at': flow.FLOW_AT})
        with tempfile.TemporaryDirectory() as folder, patch.object(workspace, 'INBOX', Path(folder)):
            view = self.flow.view('reproduction_document', case_id=case['id'])
        self.assertEqual('2026-11-13', view['requested_due_date'])
        self.assertIn('날짜: 2026-11-13', view['markdown'])
        # An altered preserved original is rejected; no waiting-file fallback is allowed.
        source = self.source_for(self.flow.one('ClaimIntake'), 'synthetic_claim')
        with tempfile.TemporaryDirectory(dir=ROOT / 'data/receipts') as folder:
            changed = Path(folder) / 'changed.eml'
            changed.write_bytes((ROOT / source['path']).read_bytes() + b'changed')
            self.flow.index[source['id']] = {**source, 'path': changed.relative_to(ROOT).as_posix()}
            with self.assertRaises(DomainError) as refused:
                self.flow.view('reproduction_document', case_id=case['id'])
        self.assertEqual('SOURCE_HASH_MISMATCH', refused.exception.code)

    def test_common_screen_mixed_configuration_flow_produces_two_valid_requirements(self):
        self.flow.index['unit:C-S005']['configuration_id'] = 'config:esp32-poe:L1:WROOM'
        case = self.flow.run_to_test_request(CLAIM_FILE)
        request = self.flow.view('test_request_document', case_id=case['id'])
        self.assertIn('config:esp32-poe:L1:WROOM', request['documents'][0]['markdown'])
        self.flow.register_results(case, RESULT_FILE)
        records = self.flow.step('reproduction', {'case_id': case['id'], 'collected_at': flow.FLOW_AT})
        self.assertEqual({'config:esp32-poe:L:WROOM': 5, 'config:esp32-poe:L1:WROOM': 1},
                         {row['configuration_id']: row['required_good_quantity'] for row in records})
        validation = validate_graph(list(self.flow.index.values()))
        self.assertTrue(validation['valid'], validation['errors'])
        view = self.flow.view('reproduction_document', case_id=case['id'])
        self.assertEqual(2, len(view['requirements']))
        self.assertEqual(2, len(view['documents']))

    def test_result_parts_are_pending_until_every_tested_row_is_registered(self):
        case = self.flow.run_to_test_request(CLAIM_FILE)
        args = {'case_id': case['id'], 'file': RESULT_FILE}
        first = self.flow.step('test_results', {**args, 'collected_at': flow.FLOW_AT})
        self.assertEqual(100, len(first))
        partial = self.flow.view('result_file', **args)
        self.assertEqual((100, 23, False), (partial['stored'], partial['pending_count'], partial['registration_complete']))
        # The second part and original view use approved bytes even after the inbox is gone.
        with tempfile.TemporaryDirectory() as folder, patch.object(workspace, 'INBOX', Path(folder)):
            self.assertEqual(23, self.flow.view('result_file', **args)['pending_count'])
            last = self.flow.step('test_results', {**args, 'collected_at': flow.FLOW_AT})
        self.assertEqual(23, len(last))
        complete = self.flow.view('result_file', **args)
        self.assertEqual((123, 0, True), (complete['stored'], complete['pending_count'], complete['registration_complete']))

    def test_sampled_results_complete_the_file_without_turning_untested_into_results(self):
        case = self.flow.run_to_test_request('2_엑셀_표본시험_클레임.xlsx')
        self.flow.register_results(case, '2_테스트팀_결과.csv')
        result = self.flow.view('result_file', case_id=case['id'], file='2_테스트팀_결과.csv')
        self.assertEqual((34, 0, True), (result['stored'], result['pending_count'], result['registration_complete']))
        self.assertEqual(124, len(result['rows']))
        self.flow.step('reproduction', {'case_id': case['id'], 'collected_at': flow.FLOW_AT})
        requirement = self.flow.one('ReproductionRequirement')
        self.assertEqual(('lower_bound', 90), (requirement['assessment_state'], requirement['unresolved_quantity']))


if __name__ == '__main__':
    unittest.main()
