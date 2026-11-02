import copy
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from ontologics.production_planning.library_process_materials import (
    load_reference, scenario, inspect_materials, validate_stock)

NOW='2026-11-02T09:00:00+00:00'
CID='config:esp32-poe:L:WROOM'

class ProcessMaterialRulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.reference=load_reference()
    def inspect(self,stock=None,quantity=100,cid=CID):
        return inspect_materials(self.reference,stock,cid,quantity,NOW)
    def test_unknown_demand_is_never_sufficient(self):
        data=self.inspect(scenario(self.reference,NOW))
        self.assertEqual(5,len(data['materials']))
        paste=next(r for r in data['materials'] if ':np560:' in r['id'])
        self.assertEqual('250',paste['stock']['available'])
        self.assertIsNone(paste['required'])
        self.assertEqual('usage_unknown',paste['status'])
        self.assertFalse(data['production_ready'])
    def test_conditional_flux_has_shortage_range_not_purchase_amount(self):
        data=self.inspect(scenario(self.reference,NOW))
        flux=next(r for r in data['materials'] if r['id'].endswith(':951'))
        self.assertEqual('13',flux['stock']['available'])
        self.assertEqual(['13.369989726','26.739979452'],flux['reference_required'])
        self.assertEqual(['0.369989726','13.739979452'],flux['reference_shortage'])
        self.assertIsNone(flux['required']);self.assertIsNone(flux['purchase_quantity'])
        self.assertEqual('reference_shortage',flux['status'])
    def test_same_material_lots_are_shared_by_configurations(self):
        stock=scenario(self.reference,NOW)
        a=self.inspect(stock);b=self.inspect(stock,cid='config:esp32-poe:L1:WROOM')
        self.assertEqual([r['stock'] for r in a['materials']],[r['stock'] for r in b['materials']])
        for r in a['materials']:self.assertEqual(2,len(r['used_by']))
    def test_missing_stock_is_unknown(self):
        data=self.inspect()
        self.assertTrue(all(r['stock']['available'] is None for r in data['materials']))
        self.assertTrue(all(r['reference_shortage'] is None for r in data['materials']))
    def test_expired_stock_excluded(self):
        stock=scenario(self.reference,NOW)
        stock['lots'][0]['expires_at']='2026-11-01T00:00:00+00:00'
        r=self.inspect(stock)['materials'][0]
        self.assertEqual('0',r['stock']['available'])
        self.assertTrue(r['stock']['lots'][0]['expired'])
    def test_reject_invalid_scope_units_and_deductions(self):
        for change in [dict(on_hand='-1'),dict(reserved='501'),dict(unit='piece'),dict(on_hand='NaN'),dict(held=True)]:
            stock=scenario(self.reference,NOW);stock['lots'][0].update(change)
            with self.assertRaises(ValueError):validate_stock(stock,self.reference)
        stock=scenario(self.reference,NOW);stock['lots'].append(copy.deepcopy(stock['lots'][0]))
        with self.assertRaises(ValueError):validate_stock(stock,self.reference)
    def test_invalid_configuration_and_quantity(self):
        for q in [-1,True,1.5,10000001]:
            with self.assertRaises(ValueError):self.inspect(quantity=q)
        with self.assertRaises(ValueError):self.inspect(cid='other')
    def test_relation_graph_has_no_dangling_edges(self):
        d=self.inspect(scenario(self.reference,NOW));nodes={n['id'] for n in d['graph']['nodes']}
        for e in d['graph']['edges']:
            self.assertIn(e['source'],nodes);self.assertIn(e['target'],nodes)
        predicates={e['relation'] for e in d['graph']['edges']}
        self.assertTrue({'candidate_material','stock_lot','supported_by','source_document'}<=predicates)
    def test_zero_quantity_not_false_approved_ready(self):
        d=self.inspect(scenario(self.reference,NOW),quantity=0)
        flux=next(r for r in d['materials'] if r['id'].endswith(':951'))
        self.assertEqual(['0','0'],flux['reference_required'])
        self.assertFalse(d['production_ready'])

import os
@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'), 'Disposable PostgreSQL schema required')
class ProcessMaterialDatabaseTests(unittest.TestCase):
    def setUp(self):
        from tests.db import database_for_test
        from ontologics.host import CommonHost
        from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider
        from ontologics.web import USERS
        from ontologics.production_planning.contract_library_web import LibraryApplication
        self.settings=database_for_test(self)
        self.context=DevelopmentContext('dev-alice')
        self.host=CommonHost(DevelopmentIdentityProvider(USERS,enabled=True),self.settings)
        self.app=LibraryApplication(self.host,self.context)
    def get(self,cid=CID):
        return self.app.dispatch('/api/tools/contracts/process-materials',dict(configuration_id=cid,quantity=100))
    def test_explicit_seed_persistence_and_shared_stock_api(self):
        r=self.get();self.assertTrue(r['ok'],r);self.assertIsNone(r['data']['stock_scope'])
        seed=self.app.process_materials.seed_scenario(self.context,'seed-process');self.assertTrue(seed['ok'],seed)
        first=self.get();self.assertEqual('synthetic',first['data']['stock_scope']['origin'])
        repeat=self.app.process_materials.seed_scenario(self.context,'seed-process-again')
        self.assertTrue(repeat['data']['preserved'])
        self.assertEqual(first['data']['stock_snapshot_id'],self.get('config:esp32-poe:L1:WROOM')['data']['stock_snapshot_id'])
        with self.host.request(self.context) as s:
            self.assertEqual(1,s._service.db.execute('SELECT count(*) AS n FROM pp_process_material_stock').fetchone()['n'])
            self.assertGreater(s._service.db.execute("SELECT count(*) AS n FROM runs WHERE tool='process_material_stock_get'").fetchone()['n'],0)
    def test_denied_seed_and_corrupted_stock(self):
        from ontologics.identity import DevelopmentContext,DevelopmentIdentityProvider,UserIdentity
        from ontologics.host import CommonHost
        from ontologics.production_planning.library_process_materials import LibraryProcessMaterials
        reader=DevelopmentContext('reader')
        host=CommonHost(DevelopmentIdentityProvider([UserIdentity('reader',frozenset({'read'}))],enabled=True),self.settings)
        service=LibraryProcessMaterials(host,reader)
        self.assertFalse(service.seed_scenario(reader,'forbidden-seed')['ok'])
        self.app.process_materials.seed_scenario(self.context,'seed')
        with self.host.request(self.context) as s:
            s._service.db.execute("UPDATE pp_process_material_stock SET content_hash='bad'")
        result=self.get();self.assertFalse(result['ok']);self.assertEqual('PROCESS_STOCK_INTEGRITY',result['errors'][0]['code'])
    def test_http_csrf_and_reference_validation(self):
        import json,threading
        from urllib.request import build_opener,HTTPCookieProcessor,Request
        from urllib.error import HTTPError
        from http.cookiejar import CookieJar
        from ontologics.production_planning.contract_library_web import LibraryServer
        server=LibraryServer(self.app,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def close():server.shutdown();server.server_close();thread.join()
        self.addCleanup(close)
        client=build_opener(HTTPCookieProcessor(CookieJar()))
        token=json.load(client.open(server.origin+'/api/context'))['data']['csrf_token']
        def req(csrf):return Request(server.origin+'/api/tools/contracts/process-materials',data=json.dumps(dict(configuration_id=CID,quantity=100)).encode(),headers={'Content-Type':'application/json','Origin':server.origin,'X-CSRF-Token':csrf})
        with self.assertRaises(HTTPError):client.open(req('bad'))
        result=json.load(client.open(req(token)));self.assertTrue(result['ok'],result)
        self.assertEqual(5,len(result['data']['materials']))
