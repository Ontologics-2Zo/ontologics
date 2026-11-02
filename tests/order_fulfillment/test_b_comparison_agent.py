"""Pure legacy comparison format/history guards; execution is in input task tests."""
import re
import unittest
from examples.order_fulfillment import b_comparison_agent as cq
from ontologics.model import canonical
from ontologics.order_fulfillment.openai_provider import ProviderError


class ComparisonAgentTests(unittest.TestCase):
    def test_local_patterns_reject_over_budget_and_multiline_strings_at_each_boundary(self):
        limits={'change_summary':60,'customer_reply':80,'dispatch_summary':45,'confirmations':35}
        self.assertEqual(220,sum(limits.values()))
        for key,limit in limits.items():
            with self.subTest(key=key):
                pattern=cq.TEXT_PATTERNS[key]
                self.assertIsNotNone(re.fullmatch(pattern,'가'*limit))
                for wrong in ('','가'*(limit+1),'가\n나','가\n'):
                    self.assertIsNone(re.fullmatch(pattern,wrong))

    def test_failed_or_out_of_order_history_is_rejected_before_provider(self):
        task={'delivery_round_id':'r','before_version':1,'after_version':2,
              'before_as_of':'2026-10-27T10:00:00+09:00','after_as_of':'2026-10-27T11:00:00+09:00'}
        called=[]
        guard=cq.ProvenanceProvider(lambda **kw:called.append(kw),task)
        for history in [
            [{'role':'tool','name':'compare_response_plan','content':canonical({'ok':False,'data_version':2})}],
            [{'role':'tool','name':'lookup_many','content':'{}'}]]:
            with self.assertRaises(ProviderError):guard(model=cq.CONFIG.model,messages=history)
        self.assertEqual([],called)
