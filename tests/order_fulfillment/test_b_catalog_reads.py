"""B accepts main's lookup filters while checking exact pinned node contents."""
from copy import deepcopy
import unittest

from examples.order_fulfillment.b_input_task import catalog_evidence


class CatalogReadTests(unittest.TestCase):
    def setUp(self):
        self.node = dict(id='revision:sample:L', type='ProductRevision', revision='L', evidence=['evidence:sample:L'])
        self.other = dict(id='revision:sample:L1', type='ProductRevision', revision='L1', evidence=['evidence:sample:L1'])

    def read(self, arguments, **changes):
        result = dict(ok=True, data_version=7, data=[self.node], evidence=['evidence:sample:L'])
        result.update(changes)
        return dict(name='lookup', arguments=arguments, result=result)

    def test_main_lookup_selectors_accept_combined_kind_node_id_and_broader_reads(self):
        for filters in (dict(node_id=self.node['id']), dict(kind=self.node['type']),
                        dict(kind=self.node['type'], node_id=self.node['id']), {}):
            with self.subTest(filters=filters):
                read = self.read(dict(filters, version=7), data=[self.node, self.other])
                self.assertEqual(['evidence:sample:L'], catalog_evidence([self.node], 7, [read]))

    def test_common_owns_arguments_while_B_checks_observed_pinned_contents(self):
        args = dict(kind='ProductRevision', node_id=self.node['id'], version=7)
        for change in (dict(kind='ProductModel'), dict(node_id='unread'), dict(version=8),
                       dict(version=True), dict(extra='not-a-main-filter')):
            with self.subTest(arguments=change):
                self.assertEqual(['evidence:sample:L'], catalog_evidence(
                    [self.node], 7, [self.read(dict(args, **change))]))
        bad_node = deepcopy(self.node)
        bad_node['revision'] = 'changed'
        for changes in (dict(ok=False), dict(data_version=8), dict(data=[self.other]),
                        dict(data=[bad_node]), dict(data=[self.node, bad_node])):
            with self.subTest(result=changes):
                self.assertIsNone(catalog_evidence([self.node], 7, [self.read(args, **changes)]))

    def test_evidence_document_lookup_does_not_substitute_for_product_read(self):
        evidence_node = dict(id='evidence:sample:L', type='Evidence', evidence=[],
                             source_id='source:sample', locator='revision L')
        evidence_read = self.read(dict(kind='Evidence', node_id=evidence_node['id'], version=7),
                                  data=[evidence_node])
        self.assertIsNone(catalog_evidence([self.node], 7, [evidence_read]))
        product_read = self.read(dict(kind=self.node['type'], node_id=self.node['id'], version=7))
        self.assertEqual(['evidence:sample:L'], catalog_evidence(
            [self.node], 7, [evidence_read, product_read]))
