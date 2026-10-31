"""Explicit A persistence must not change or bypass the common schema boundary."""
import copy
import os
import unittest
from ontologics import model
from ontologics.service import Service
from ontologics.production_planning.persistence import PlanningService
from tests.production_planning.test_ontology_extension import catalog, planning_nodes
from tests.db import database_for_test


@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'Disposable PostgreSQL required')
class PlanningPersistenceTests(unittest.TestCase):
    def test_a_storage_does_not_register_common_types_or_allow_invalid_a_facts(self):
        settings=database_for_test(self)
        principals={'fixture':{'kind':'human','roles':['seed','read']}}
        nodes=catalog()+planning_nodes()
        before=copy.deepcopy(model.SCHEMA)
        common=Service(settings,principals=principals)
        self.addCleanup(common.close)
        self.assertFalse(common.bootstrap('fixture',nodes,request_key='common-reject')['ok'])
        service=PlanningService(settings,principals=principals)
        self.addCleanup(service.close)
        bad=copy.deepcopy(nodes)
        next(n for n in bad if n['type']=='ContractItem')['quantity']=-1
        denied=service.bootstrap('fixture',bad,request_key='invalid-a')
        self.assertFalse(denied['ok'])
        self.assertEqual(0,service.lookup('fixture')['data_version'])
        accepted=service.bootstrap('fixture',nodes,request_key='valid-a')
        self.assertTrue(accepted['ok'],accepted)
        self.assertEqual(nodes,service.lookup('fixture')['data'])
        missing=service.preview_investigation('fixture','missing-draft',1,{})
        self.assertFalse(missing['ok'])
        self.assertEqual('DRAFT_NOT_FOUND',missing['errors'][0]['code'])
        self.assertEqual(before,model.SCHEMA)
        self.assertFalse(model.validate_graph(nodes)['valid'])
        graph=service.export_graph('fixture')['data']
        item=next(n for n in graph['@graph'] if n['type']=='ContractItem')
        self.assertTrue(item['contract_id'].startswith('urn:ontologics:entity:'))
