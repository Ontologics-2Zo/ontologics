"""Pure legacy decoder regressions; supported Agent execution is in *_task tests."""
import json
import unittest
from examples.order_fulfillment.b_agent import _answer


class BAgentTests(unittest.TestCase):
    def test_malformed_answer_types_are_rejected_without_database(self):
        for mode in ('intake', 'promise'):
            for text in ('null', '[]', '{}', 'not json', '{"unknowns":null}', '{"evidence":[{}]}'):
                with self.subTest(mode=mode, text=text):
                    self.assertIsNone(_answer(text, mode))

    def test_boolean_versions_are_rejected_without_database(self):
        answer = dict(status='awaiting_review', data_version=1, evidence=['e:contract'],
                      unknowns=[], synthetic_contract=True, summary='가상 미승인 초안',
                      next_action='별도 검토', draft_id='draft:test', draft_version=1,
                      needs_human_review=True)
        self.assertEqual(answer, _answer(json.dumps(answer), 'intake'))
        for field in ('data_version', 'draft_version'):
            with self.subTest(field=field):
                self.assertIsNone(_answer(json.dumps(dict(answer, **{field: True})), 'intake'))

    def test_nested_nonfinite_values_are_rejected_without_database(self):
        answer = dict(status='promise_only', data_version=1, evidence=[], unknowns=[],
                      synthetic_contract=True, summary='가상 계약 약속', next_action='별도 검토',
                      promise={'quantity': 1000})
        self.assertEqual(answer, _answer(json.dumps(answer), 'promise'))
        for value in (float('nan'), float('inf'), -float('inf')):
            with self.subTest(value=value):
                self.assertIsNone(_answer(json.dumps(dict(answer, promise={'quantity': value})), 'promise'))
