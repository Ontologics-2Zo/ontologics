import unittest

from ontologics.production_planning.rules import calculation_rule_version


class PlanningRuleTests(unittest.TestCase):
    def test_explicit_calculation_domains_have_separate_versions(self):
        for domain in ('materials', 'procurement', 'production'):
            self.assertEqual(domain + '-0.1', calculation_rule_version(domain))

    def test_unknown_domains_do_not_fall_back_to_temporal(self):
        for domain in ('temporal', '', 'unknown', None, [], {}):
            with self.subTest(domain=domain), self.assertRaises(ValueError):
                calculation_rule_version(domain)
