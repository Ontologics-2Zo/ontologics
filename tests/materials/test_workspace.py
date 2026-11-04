"""A common-screen adapter: real PostgreSQL, model double, isolated schemas."""
import base64
import json
import unittest

from ontologics.harness import BudgetLimits, ExecutionBudget
from ontologics.host import CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.model import digest
from ontologics.materials.workspace import MaterialsWorkspace, REGISTRATION
from ontologics.service import DomainError
from ontologics.web import USERS, seed_development
from tests.db import database_for_test


class MaterialsWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.database = database_for_test(self)
        self.assertTrue(seed_development(self.database)['ok'])
        self.alice = DevelopmentContext('dev-alice')
        self.bob = DevelopmentContext('dev-bob')
        self.reader = DevelopmentContext('dev-reader')
        self.host = CommonHost(DevelopmentIdentityProvider(USERS, enabled=True),
                               self.database, agent_registrations=(REGISTRATION,))
        self.model_calls = []

        def provider(**kwargs):
            self.model_calls.append(kwargs['model'])
            fields = {}
            for name, value, line in [('ordered_quantity', 7, 2), ('manufacturer', 'OLIMEX', 3),
                                      ('product_model', 'ESP32-POE', 4), ('revision', 'L', 5),
                                      ('variant', 'WROOM', 6), ('unit', '대', 2)]:
                fields[name] = [{'value': value, 'evidence': [{'segment_id': f'text:line:{line}',
                                  'quote': str(value), 'occurrence': 0}]}]
            return {'text': json.dumps({'items': [{'fields': fields}]}), 'tool_calls': []}

        self.app = MaterialsWorkspace(self.host, self.alice, provider_factory=lambda names: provider,
                                     model='scripted-test')
        self.raw = '가상 통합 시험 계약\n주문 7대\n제조사 OLIMEX\n제품 ESP32-POE\n개정 L\n변형 WROOM\n'.encode()
        self.receipt = dict(title='통합 시험 계약', counterparty='가상 고객', filename='contract.txt',
                            content_base64=base64.b64encode(self.raw).decode(), request_key='receive')

    def dispatch(self, action, body, context=None):
        result = self.app.dispatch(context or self.alice, action, body)
        self.assertTrue(result['ok'], result)
        return result['data']

    def fact_hash(self):
        with self.host.request(self.alice) as session:
            svc = session._service
            return digest({table: svc.db.execute('SELECT * FROM ' + table + ' ORDER BY ' + order).fetchall()
                for table, order in [('snapshots', 'version'), ('drafts', 'id,version'), ('review_events', 'sequence')]})

    def reviewed_contract(self):
        cid = self.dispatch('register', self.receipt)['id']
        self.assertEqual([], self.model_calls, 'Receipt must not start a paid interpretation')
        interpreted = self.dispatch('interpret', dict(contract_id=cid, request_key='interpret'))
        self.assertEqual('needs_review', interpreted['status'], interpreted)
        decisions = {f'0:{name}': dict(state='accepted' if field['state']=='supported' else 'unresolved',
                     value=field['value'] if field['state']=='supported' else None, reason='')
                     for name, field in interpreted['result']['items'][0]['fields'].items()}
        self.review_body = dict(contract_id=cid, interpretation_id=interpreted['id'],
            result_sha256=interpreted['result_sha256'], decisions=decisions, note='가상 원문 대조', request_key='review')
        self.dispatch('review', self.review_body, self.bob)
        return cid

    def test_original_review_configuration_materials_reuse_rules_without_changing_approved_facts(self):
        before = self.fact_hash()
        cid = self.reviewed_contract()
        self.assertEqual(['scripted-test'], self.model_calls)
        self.assertEqual(cid, self.dispatch('search', {'query':'통합 시험', 'offset':0})['items'][0]['id'])
        original = self.dispatch('get', {'contract_id':cid})
        self.assertEqual(self.raw, base64.b64decode(original['content_base64']))
        self.assertEqual('user:dev-bob', original['review']['checks'][0]['actor'])
        planning = self.dispatch('planning', {'contract_id':cid})
        candidate = next(row for row in planning['items'][0]['candidates'] if row['exact'])
        self.dispatch('connect', dict(contract_id=cid, interpretation_id=planning['interpretation_id'],
            review_id=planning['review_id'], data_version=planning['data_version'], item_index=0,
            configuration_id=candidate['configuration_id'], request_key='connect'), self.bob)
        material = self.dispatch('materials', {'contract_id':cid, 'item_index':0})
        args = dict(contract_id=cid,item_index=0,link_id=material['link']['id'],quantity=7,
                    reason='가상 주문 7대의 원본 BOM 소요 검토',request_key='calculate')
        result = self.dispatch('materials-calculate', args, self.bob)['result']
        self.assertEqual(planning['data_version'], result['data_version'])
        self.assertTrue(result['components'])
        self.assertTrue(all(row['A'] is None and row['S'] is None for row in result['components']))
        self.assertTrue(any(row['q'] is None for row in result['components']))
        self.assertTrue(all(row['D']==7*row['q'] for row in result['components'] if row['q'] is not None))
        saved = self.dispatch('materials', {'contract_id':cid,'item_index':0})['saved']
        self.assertEqual('user:dev-bob', saved['actor'])
        self.dispatch('materials-calculate', args, self.bob)
        self.assertEqual(saved['id'], self.dispatch('materials', {'contract_id':cid,'item_index':0})['saved']['id'])
        self.assertEqual(before, self.fact_hash())

    def test_exact_review_permissions_unknown_fields_and_shared_limit_remain_enforced(self):
        denied = self.app.dispatch(self.reader, 'register', self.receipt)
        self.assertEqual('FORBIDDEN', denied['errors'][0]['code'])
        with self.assertRaises(DomainError):
            self.app.dispatch(self.alice, 'register', dict(self.receipt, actor='user:dev-bob'))
        pdf = dict(self.receipt, filename='source.pdf',content_base64=base64.b64encode(b'%PDF-1.4\nexample').decode(),request_key='pdf')
        for rejected in (pdf,dict(pdf,filename='source.txt')):
            with self.assertRaises(DomainError) as error:
                self.app.dispatch(self.alice, 'register', rejected)
            self.assertEqual('A_PDF_SANDBOX_REQUIRED', error.exception.code)
        bad_utf8 = dict(self.receipt,content_base64=base64.b64encode(b'\xff\xfe').decode())
        with self.assertRaises(DomainError) as error:
            self.app.dispatch(self.alice, 'register', bad_utf8)
        self.assertEqual('UNSUPPORTED_CONTRACT_FORMAT', error.exception.code)
        # Previously received A PDFs remain stored, but this new route must
        # never start their existing in-host PDF parser or a paid model call.
        legacy = self.app.library.register(self.alice, **pdf)
        self.assertTrue(legacy['ok'], legacy)
        with self.assertRaises(DomainError) as error:
            self.app.dispatch(self.alice, 'interpret', dict(contract_id=legacy['data']['id'],request_key='interpret-pdf'))
        self.assertEqual('A_PDF_SANDBOX_REQUIRED', error.exception.code)
        self.assertEqual([], self.model_calls)
        cid = self.reviewed_contract()
        denied = self.app.dispatch(self.reader, 'review', dict(self.review_body,request_key='reader-review'))
        self.assertEqual('FORBIDDEN', denied['errors'][0]['code'])
        stale = self.app.dispatch(self.bob, 'review', dict(self.review_body,result_sha256='0'*64,request_key='stale'))
        self.assertEqual('STALE_REVISION', stale['errors'][0]['code'])
        budget = ExecutionBudget(BudgetLimits(max_tool_calls=1))
        with self.host.request(self.alice, budget=budget):
            self.dispatch('search', {'query':'', 'offset':0})
            with self.assertRaises(DomainError) as error:
                self.app.dispatch(self.alice, 'get', {'contract_id':cid})
            self.assertEqual('SHARED_BUDGET_EXCEEDED', error.exception.code)
        self.host._identities = DevelopmentIdentityProvider([UserIdentity('dev-alice',frozenset())], enabled=True)
        denied = self.app.dispatch(self.alice, 'search', {'query':'', 'offset':0})
        self.assertEqual('FORBIDDEN', denied['errors'][0]['code'])

    def seed_material_stock(self, cid='config:esp32-poe:L:WROOM'):
        from ontologics.materials.library_stock import company_stock_scenario
        with self.host.request(self.alice) as session:
            detail = session.call('configuration', {'configuration_id': cid})['data']
            stock = company_stock_scenario(detail, session._service.now())
        self.assertTrue(self.app.materials.stock.store(self.alice, cid, stock, 'stock')['ok'])
        self.assertTrue(self.app.process_materials.seed_scenario(self.alice, 'process')['ok'])

    def test_product_combined_review_uses_one_quantity_and_preserves_unknowns_and_graph(self):
        before = self.fact_hash()
        args = dict(configuration_id='config:esp32-poe:L:WROOM', quantity=200,
                    allowance_percent='10', request_key='combined')
        self.assertIn('combined-review', __import__('ontologics.materials.workspace', fromlist=['FIELDS']).FIELDS)
        no_stock = self.dispatch('combined-review', args)
        self.assertTrue(all(r['A'] is None and r['S'] is None for r in no_stock['payload']['bom']['components']))
        self.assertEqual([], no_stock['payload']['rfq']['lines'])
        self.assertTrue(no_stock['payload']['rfq']['excluded'])
        self.seed_material_stock()
        saved = self.dispatch('combined-review', dict(args, request_key='stocked'))
        p = saved['payload']
        self.assertEqual(200, p['quantity'])
        self.assertEqual(200, p['bom']['planned_input_quantity'])
        self.assertEqual(200, p['process_basis']['quantity'])
        self.assertTrue(p['rfq']['lines'])
        self.assertTrue(any(r['kind']=='process' for r in p['rfq']['lines']))
        self.assertTrue(any(r['kind']=='bom' for r in p['rfq']['lines']))
        self.assertTrue(all(r['shortage'] is not None for r in p['rfq']['lines']))
        self.assertFalse(p['rfq']['sent'])
        self.assertFalse(p['rfq']['stock_reserved'])
        self.assertEqual(saved, self.dispatch('combined-review', dict(args, request_key='stocked')))
        self.assertEqual(before, self.fact_hash())
        denied = self.app.dispatch(self.reader, 'combined-review', dict(args, request_key='reader'))
        self.assertEqual('FORBIDDEN', denied['errors'][0]['code'])
        for quantity in [True, -1, 1.5, 10000001]:
            with self.assertRaises(DomainError):
                self.app.dispatch(self.alice, 'combined-review', dict(args, quantity=quantity, request_key='invalid'))
        inventory = self.dispatch('product-inventory', {'quantity': 30})
        self.assertEqual(30, inventory['quantity'])
        self.assertTrue(inventory['products'])
        preview = self.dispatch('process-requirement-preview', {k:v for k,v in args.items() if k!='request_key'})
        self.assertEqual(p['process'], preview['lines'])
        self.assertEqual([], self.dispatch('request-inbox', {})['requests'])

    def test_contract_combined_review_binds_saved_link_and_rejects_old_link(self):
        self.assertIn('contract-combined-review', __import__('ontologics.materials.workspace', fromlist=['FIELDS']).FIELDS)
        cid = self.reviewed_contract()
        plan = self.dispatch('planning', {'contract_id': cid})
        candidate = next(r for r in plan['items'][0]['candidates'] if r['exact'])
        self.dispatch('connect', dict(contract_id=cid, interpretation_id=plan['interpretation_id'],
            review_id=plan['review_id'], data_version=plan['data_version'], item_index=0,
            configuration_id=candidate['configuration_id'], request_key='connect'))
        material = self.dispatch('materials', {'contract_id':cid,'item_index':0})
        self.seed_material_stock(candidate['configuration_id'])
        args = dict(contract_id=cid,item_index=0,link_id=material['link']['id'],quantity=7,
                    reason='계약 7대 검토',allowance_percent='10',request_key='contract-combined')
        before = self.fact_hash()
        p = self.dispatch('contract-combined-review', args)['payload']
        self.assertEqual(cid, p['contract_id'])
        self.assertEqual(args['link_id'], p['link_id'])
        self.assertEqual(7, p['bom']['planned_input_quantity'])
        self.assertEqual(7, p['process_basis']['quantity'])
        self.assertIn(cid, p['rfq']['text'])
        reopened = self.dispatch('materials', {'contract_id':cid, 'item_index':0})
        self.assertIn('combined_saved', reopened)
        self.assertEqual(p, reopened['combined_saved']['payload'])
        self.assertTrue(reopened['combined_current'])
        self.assertFalse(reopened['combined_stock_changed'])
        self.assertEqual(before, self.fact_hash())
        # A fixture reservation for this same contract remains available to its
        # own plan review, but is deducted from an independent product review.
        from ontologics.model import canonical
        component = next(r for r in p['bom']['components'] if r['A'] is not None and r['A'] >= 3)
        reservation = {'lines': [{'component_id': component['component_id'], 'reserve': 3}]}
        with self.host.request(self.alice) as session:
            session._service.db.execute('''INSERT INTO pp_material_reservations
                VALUES(%s,%s,0,%s,%s,'active',%s,%s,NULL,NULL)''',
                ('fixture-own-reservation', cid, canonical(reservation), digest(reservation),
                 session.context['actor'], session._service.now()))
        own = self.dispatch('contract-combined-review', dict(args, request_key='own-reserved'))['payload']
        other = self.dispatch('combined-review', dict(configuration_id=candidate['configuration_id'],
            quantity=7, allowance_percent='10', request_key='independent'))['payload']
        find = lambda payload: next(r for r in payload['bom']['components'] if r['component_id']==component['component_id'])['A']
        self.assertEqual(component['A'], find(own))
        self.assertEqual(component['A'] - 3, find(other))
        with self.host.request(self.alice) as session:
            self.assertEqual(1, session._service.db.execute('SELECT count(*) AS n FROM pp_material_reservations').fetchone()['n'])
        with self.assertRaises(DomainError) as error:
            self.app.dispatch(self.alice, 'contract-combined-review', dict(args, link_id='old', request_key='stale'))
        self.assertEqual('STALE_REVISION', error.exception.code)
        denied = self.app.dispatch(self.reader, 'contract-combined-review', dict(args, request_key='reader'))
        self.assertEqual('FORBIDDEN', denied['errors'][0]['code'])


if __name__ == '__main__':
    unittest.main()
