import unittest
from ontologics.materials.customer_library import buyer_claim, choose_customer

class CustomerRuleTests(unittest.TestCase):
    def row(self,name='Acme'):
        return {'id':'i','source_sha256':'s','result_sha256':'r','checks':[],
                'result':{'items':[{'fields':{'customer':{'state':'supported','value':name,'claims':[]}}}]}}
    def test_buyer_is_source_grounded_not_original_counterparty(self):
        claim=buyer_claim(self.row());self.assertEqual('Acme',claim['name']);self.assertEqual('source_validated',claim['source'])
    def test_missing_conflicting_or_human_held_buyer_needs_question(self):
        row=self.row();row['result']['items'][0]['fields']['customer']['state']='conflict'
        self.assertIsNone(buyer_claim(row)['name'])
        row=self.row();row['checks']=[{'id':'c','decisions':{'0:customer':{'state':'unresolved','value':None,'reason':''}}}]
        self.assertIsNone(buyer_claim(row)['name'])
    def test_unique_name_and_ambiguous_names(self):
        claim=buyer_claim(self.row())
        self.assertEqual('create',choose_customer(claim,[])['state'])
        a={'id':'a','name':'Acme','registration_number':None}
        self.assertEqual('a',choose_customer(claim,[a])['customer_id'])
        self.assertEqual('ambiguous',choose_customer(claim,[a,dict(a,id='b')])['state'])
    def test_registration_does_not_override_conflicting_name(self):
        claim=dict(buyer_claim(self.row()),registration_number='1234567890')
        a={'id':'a','name':'Other','registration_number':'1234567890'}
        self.assertEqual('conflict',choose_customer(claim,[a])['state'])

import os
import base64
import json
from ontologics.host import CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider
from ontologics.web import USERS
from tests.db import database_for_test

@unittest.skipUnless(os.environ.get('TEST_DATABASE_URL'),'Disposable PostgreSQL required')
class CustomerDatabaseTests(unittest.TestCase):
    def setUp(self):
        from ontologics.materials.library_review import REGISTRATION
        from ontologics.materials.contract_library_web import LibraryApplication
        self.settings=database_for_test(self)
        self.context=DevelopmentContext('dev-alice')
        host=CommonHost(DevelopmentIdentityProvider(USERS,enabled=True),self.settings,agent_registrations=(REGISTRATION,))
        self.app=LibraryApplication(host,self.context)
        self.serial=0
    def contract(self,name='Customer A',number=None):
        self.serial+=1;lines=[f'Contract {self.serial}'];fields={}
        for k,v in [('customer',name),('customer_registration_number',number),('ordered_quantity',100)]:
            if v is None:continue
            lines.append(f'{k}: {v}');fields[k]=[dict(value=v,evidence=[dict(segment_id=f'text:line:{len(lines)}',quote=str(v),occurrence=0)])]
        def provider(**kwargs):return dict(text=json.dumps({'items':[{'fields':fields}]}),tool_calls=[])
        self.app.review.provider_factory=lambda:provider;self.app.review.model='test'
        r=self.app.dispatch('/api/tools/contracts/register',dict(title=f'Order {self.serial}',filename=f'order-{self.serial}.txt',content_base64=base64.b64encode('\n'.join(lines).encode()).decode(),request_key=f'create-{self.serial}'))
        self.assertTrue(r['ok'],r)
        return r['data']['id']
    def inspect(self,cid):
        r=self.app.customers.inspect(self.context,cid);self.assertTrue(r['ok'],r);return r['data']
    def test_auto_create_reuse_history_and_search_without_counterparty(self):
        a=self.contract();b=self.contract()
        first=self.inspect(a);second=self.inspect(b)
        self.assertEqual('linked',first['state']);self.assertEqual(first['customer']['id'],second['customer']['id'])
        history=self.app.customers.contracts(self.context,first['customer']['id'])['data']
        self.assertEqual({a,b},{x['id'] for x in history})
        search=self.app.dispatch('/api/tools/contracts/search',dict(query='Customer A',offset=0))
        self.assertEqual({a,b},{x['id'] for x in search['data']['items']})
        self.assertTrue(self.app.customers.sync(self.context,a,'repeat')['ok'])
        with self.app.host.request(self.context) as session:
            self.assertEqual(1,session._service.db.execute('SELECT count(*) AS n FROM pp_customers').fetchone()['n'])
            self.assertEqual(2,session._service.db.execute('SELECT count(*) AS n FROM pp_contract_buyers').fetchone()['n'])
            self.assertEqual(0,session._service.db.execute('SELECT count(*) AS n FROM pp_contract_reading_checks').fetchone()['n'])
    def test_same_name_different_registration_requires_choice_for_name_only(self):
        a=self.contract();b=self.contract(number='123-45-67890')
        one=self.inspect(a);self.assertEqual('ambiguous',one['state']);self.assertTrue(one['previous_link_invalidated'])
        target=self.inspect(b)['customer']['id']
        selected=self.app.customers.select(self.context,a,target,one['basis_hash'],'select')
        self.assertTrue(selected['ok'],selected);self.assertEqual('human_selection',self.inspect(a)['link']['method'])
        row=self.app.review.latest(self.context,a)['data']
        decisions={f'0:{k}':dict(state='accepted' if f['state']=='supported' else 'unresolved',value=f['value'] if f['state']=='supported' else None,reason='') for k,f in row['result']['items'][0]['fields'].items()}
        decisions['0:ordered_quantity']=dict(state='corrected',value='120',reason='수량만 수정')
        reviewed=self.app.dispatch('/api/tools/contracts/review',dict(contract_id=a,interpretation_id=row['id'],result_sha256=row['result_sha256'],decisions=decisions,note='',request_key='quantity-only'))
        self.assertTrue(reviewed['ok'],reviewed)
        self.assertEqual(target,self.inspect(a)['customer']['id'])
        stale=self.app.customers.select(self.context,a,target,one['basis_hash'],'stale-choice')
        self.assertFalse(stale['ok']);self.assertEqual('STALE_REVISION',stale['errors'][0]['code'])
        conflicting=self.contract('Other Name','123-45-67890')
        self.assertEqual('conflict',self.inspect(conflicting)['state'])
    def test_missing_buyer_and_human_correction_rebind_without_false_history(self):
        missing=self.contract(None);self.assertEqual('needs_buyer',self.inspect(missing)['state'])
        cid=self.contract();old=self.inspect(cid)['customer']['id']
        row=self.app.review.latest(self.context,cid)['data']
        decisions={f'0:{k}':dict(state='accepted' if f['state']=='supported' else 'unresolved',value=f['value'] if f['state']=='supported' else None,reason='') for k,f in row['result']['items'][0]['fields'].items()}
        decisions['0:customer']=dict(state='corrected',value='Customer B',reason='원문 구매자 수정')
        changed=self.app.dispatch('/api/tools/contracts/review',dict(contract_id=cid,interpretation_id=row['id'],result_sha256=row['result_sha256'],decisions=decisions,note='',request_key='buyer-change'))
        self.assertTrue(changed['ok'],changed);self.assertTrue(changed['data']['buyer_sync_ok'])
        current=self.inspect(cid);self.assertEqual('Customer B',current['customer']['name']);self.assertNotEqual(old,current['customer']['id'])
        self.assertEqual([],self.app.customers.contracts(self.context,old)['data'])
    def test_read_permission_cannot_create_customer(self):
        cid=self.contract()
        result=self.app.customers.sync(DevelopmentContext('dev-reader'),cid,'forbidden')
        self.assertFalse(result['ok']);self.assertEqual('FORBIDDEN',result['errors'][0]['code'])
