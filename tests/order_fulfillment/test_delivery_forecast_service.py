"""CQ04 confirmed-version read tool in disposable PostgreSQL schemas; no LLM calls."""
import copy
import hashlib
import json
import unittest

from ontologics import __version__
from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.order_fulfillment.delivery_demo import load_demo_delivery
from ontologics.demo import PRINCIPALS
from ontologics.order_fulfillment.host import AgentRegistration, CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.model import ROOT, SCHEMA_VERSION
from ontologics.order_fulfillment.service import AgentGateway, Service
from ontologics.order_fulfillment.tool_specs import (B_FULFILLMENT_TOOLS, B_INTAKE_TOOLS, B_PROMISE_TOOLS,
    B_PRODUCTION_TOOLS, KNOWLEDGE_TOOLS, PRODUCT_TOOLS, QUALITY_TOOLS, SUPERVISOR_TOOLS,
    function_tools)
from tests.db import database_for_test

AS_OF='2026-10-27T10:00:00+09:00'
ROUND='delivery-round:demo:B-01:1'


class DeliveryForecastServiceTests(unittest.TestCase):
    def setUp(self):
        self.database=database_for_test(self)
        principals=copy.deepcopy(PRINCIPALS)
        principals['delivery-reader']={'kind':'human','roles':['read']}
        self.service=Service(self.database,principals=principals)
        self.addCleanup(self.service.close)
        nodes=json.loads((ROOT/'data/catalog.json').read_text())['nodes']+load_demo_contract()
        seeded=self.service.bootstrap('seed-admin',nodes,request_key='delivery-base')
        self.assertTrue(seeded['ok'],seeded)
        self.before=seeded['data_version']
        self.gateway=AgentGateway(self.service,'delivery-reader',allowed_tools={'delivery_forecast','lookup'})
        self.candidates=load_demo_delivery()

    def error_is(self,result,code):
        self.assertFalse(result['ok'],result)
        self.assertEqual(code,result['errors'][0]['code'],result)

    def arguments(self,version,**changes):
        result=dict(delivery_round_id=ROUND,as_of=AS_OF,version=version)
        result.update(changes)
        return result

    def query(self,version,**changes):
        return self.gateway.call('delivery_forecast',self.arguments(version,**changes))

    def draft(self):
        result=self.service.prepare_draft('product-agent',[{'op':'put','node':n} for n in self.candidates],
                                          request_key='delivery-draft')
        self.assertTrue(result['ok'],result)
        return result['data']

    def approve_apply(self,draft):
        did,dv=draft['draft_id'],draft['draft_version']
        reviewed=self.service.review('reviewer',did,dv,decision='approve',
            note='Test human checked synthetic delivery conditions and allocations',request_key='delivery-review')
        self.assertTrue(reviewed['ok'],reviewed)
        result=self.service.apply('workflow',did,dv,request_key='delivery-apply')
        self.assertTrue(result['ok'],result)
        return result['data_version']

    def register(self,mixed=False):
        if mixed:self.candidates=load_demo_delivery(mixed=True)
        draft=self.draft()
        self.assertTrue(draft['validation']['valid'],draft)
        return self.approve_apply(draft)

    def test_D21_unapproved_conditions_are_invisible_then_exact_approved_version_matches_golden(self):
        before=self.query(self.before)
        # The round exists in the baseline, but no snapshot/conditions do: it cannot be a forecast.
        self.assertTrue(before['ok'],before)
        self.assertEqual('needs_confirmation',before['data']['deadline_status'])
        draft=self.draft()
        self.assertEqual('awaiting_review',draft['status'])
        self.error_is(self.service.apply('workflow',draft['draft_id'],draft['draft_version'],
                                        request_key='delivery-too-early'),'APPROVAL_REQUIRED')
        pending=self.query(self.before)
        self.assertTrue(pending['ok'],pending)
        self.assertIsNone(pending['data']['on_time_quantity'])
        version=self.approve_apply(draft)
        graph_before=self.service.lookup('delivery-reader',version=version)['data']
        result=self.query(version)
        self.assertTrue(result['ok'],result)
        golden=json.loads((ROOT/'data/order_fulfillment/b_delivery_expected.json').read_text())
        for key in ('deadline_status','on_time_quantity','shortfall_quantity','estimated_full_arrival_at'):
            self.assertEqual(golden[key],result['data'][key],key)
        self.assertEqual((version,SCHEMA_VERSION,__version__,'temporal-0.1'),
                         (result['data_version'],result['schema_version'],result['contract_version'],result['rule_version']))
        self.assertEqual('delivery-0.1',result['data']['forecast_rule_version'])
        self.assertEqual(graph_before,self.service.lookup('delivery-reader',version=version)['data'])
        self.assertEqual(version,self.service.lookup('delivery-reader')['data_version'])
        self.assertIsNone(self.query(self.before)['data']['on_time_quantity'])

    def test_D21_explicit_version_round_and_offset_required(self):
        version=self.register()
        for value in (None,True,False,-1,'2',2.0,[],{}):
            with self.subTest(version=value):self.error_is(self.query(value),'INVALID_VERSION')
        self.error_is(self.query(version+100),'VERSION_NOT_FOUND')
        for rid in ('missing:round','contract:demo:B-01'):
            with self.subTest(rid=rid):self.error_is(self.query(version,delivery_round_id=rid),'NOT_FOUND')
        for rid in ('',' ',None,[],{}):
            with self.subTest(rid=rid):self.error_is(self.query(version,delivery_round_id=rid),'INVALID_INPUT')
        for stamp in ('2026-10-27','2026-10-27T10:00:00',None,[],{}):
            with self.subTest(as_of=stamp):self.error_is(self.query(version,as_of=stamp),'INVALID_TIME')
        for field in ('delivery_round_id','as_of'):
            args=self.arguments(version);args.pop(field)
            self.error_is(self.gateway.call('delivery_forecast',args),'INVALID_TOOL_ARGUMENTS')
        args=self.arguments(version);args.pop('version')
        self.error_is(self.gateway.call('delivery_forecast',args),'INVALID_VERSION')

    def test_D19_D20_equivalent_utc_and_other_time_do_not_retime_facts(self):
        version=self.register()
        result=self.query(version,as_of='2026-10-27T01:00:00Z')
        self.assertTrue(result['ok'],result)
        self.assertEqual(960,result['data']['on_time_quantity'])
        unknown=self.query(version,as_of='2026-10-27T11:00:00+09:00')
        self.assertTrue(unknown['ok'],unknown)
        self.assertEqual('needs_confirmation',unknown['data']['deadline_status'])
        self.assertIsNone(unknown['data']['on_time_quantity'])
        self.assertEqual(version,self.service.lookup('delivery-reader')['data_version'])

    def test_D25_mixed_fixture_is_approved_separately_and_matches_independent_golden(self):
        version=self.register(mixed=True)
        result=self.query(version)
        self.assertTrue(result['ok'],result)
        golden=json.loads((ROOT/'data/order_fulfillment/b_delivery_mixed_expected.json').read_text())
        for key in ('deadline_status','on_time_quantity','shortfall_quantity','estimated_full_arrival_at'):
            self.assertEqual(golden[key],result['data'][key],key)
        self.assertEqual(4,len(result['data']['breakdown']))
        self.assertEqual(1000,result['data']['on_time_quantity'])

    def test_D16_invalid_condition_cannot_be_approved_or_applied(self):
        condition=next(n for n in self.candidates if n['type']=='DeliveryCondition')
        condition['transport_seconds']=True
        draft=self.draft()
        self.assertFalse(draft['validation']['valid'])
        self.assertEqual('needs_changes',draft['status'])
        self.error_is(self.service.review('reviewer',draft['draft_id'],draft['draft_version'],decision='approve',
            note='Reject malformed condition',request_key='delivery-bad-review'),'VALIDATION_FAILED')
        self.error_is(self.service.apply('workflow',draft['draft_id'],draft['draft_version'],
                                        request_key='delivery-bad-apply'),'APPROVAL_REQUIRED')
        self.assertEqual(self.before,self.service.lookup('delivery-reader')['data_version'])

    def test_D29_incomplete_coverage_is_valid_data_but_unknown_result(self):
        snapshot=next(n for n in self.candidates if n['type']=='FulfillmentSnapshot')
        snapshot['confirmations'].pop('material_disjoint')
        version=self.register()
        result=self.query(version)
        self.assertTrue(result['ok'],result)
        self.assertEqual('needs_confirmation',result['data']['deadline_status'])
        self.assertIsNone(result['data']['shortfall_quantity'])

    def test_D34_D35_returned_provenance_resolves_and_read_never_rewrites_sources(self):
        version=self.register()
        first=self.query(version)
        self.assertTrue(first['ok'],first)
        for eid in first['evidence']:
            result=self.service.lookup('delivery-reader',node_id=eid,version=version)
            self.assertTrue(result['ok'],result)
            evidence=result['data'][0]
            self.assertEqual('Evidence',evidence['type'])
            self.assertTrue(evidence['locator'])
            source=self.service.lookup('delivery-reader',node_id=evidence['source_id'],version=version)
            self.assertTrue(source['ok'],source)
            document=source['data'][0]
            self.assertEqual(document['sha256'],hashlib.sha256((ROOT/document['path']).read_bytes()).hexdigest())
        second=self.query(version)
        self.assertEqual(first['data']['on_time_quantity'],second['data']['on_time_quantity'])
        self.assertEqual(version,self.service.lookup('delivery-reader')['data_version'])
        document=copy.deepcopy(next(n for n in self.candidates if n['type']=='SourceDocument'))
        document['license']+=' overwrite'
        self.error_is(self.service.prepare_draft('product-agent',[{'op':'put','node':document}],
            request_key='delivery-source-overwrite'),'IMMUTABLE_SOURCE')

    def test_D21_read_identity_cannot_gain_write_or_actor_override_and_is_audited(self):
        version=self.register()
        args=self.arguments(version)
        self.error_is(AgentGateway(self.service,'unknown').call('delivery_forecast',args),'FORBIDDEN')
        for override in ({'actor':'reviewer'},{'principals':{}}):
            self.error_is(self.gateway.call('delivery_forecast',dict(args,**override)),'INVALID_TOOL_ARGUMENTS')
        for tool in ('prepare_draft','review','apply','bootstrap'):
            self.error_is(self.gateway.call(tool,{}),'TOOL_NOT_ALLOWED')
        self.error_is(self.service.prepare_draft('delivery-reader',[{'op':'put','node':self.candidates[-1]}],
                                                request_key='delivery-reader-write'),'FORBIDDEN')
        self.assertTrue(self.query(version)['ok'])
        history=self.service.run_history('seed-admin')['data']
        self.assertTrue(any(r['tool']=='delivery_forecast' and r['actor']=='delivery-reader'
                            and r['status']=='succeeded' for r in history))
        self.assertTrue(any(r['tool']=='delivery_forecast' and r['status']=='failed' for r in history))

    def test_D21_tool_schema_and_existing_roles_keep_separate_authority(self):
        spec=function_tools(('delivery_forecast',))[0]
        self.assertEqual({'delivery_round_id','as_of','version'},set(spec['parameters']['required']))
        self.assertFalse(spec['parameters']['additionalProperties'])
        for roles in (PRODUCT_TOOLS,QUALITY_TOOLS,B_INTAKE_TOOLS,B_PROMISE_TOOLS,
                      B_FULFILLMENT_TOOLS,B_PRODUCTION_TOOLS,KNOWLEDGE_TOOLS,SUPERVISOR_TOOLS):
            self.assertNotIn('delivery_forecast',roles)

    def test_D21_common_host_extension_uses_request_users_read_authority(self):
        version=self.register()
        reader=UserIdentity('cq04-test-reader',frozenset({'read'}))
        denied=UserIdentity('cq04-test-denied',frozenset())
        identity=DevelopmentIdentityProvider([reader,denied],enabled=True)
        registration=AgentRegistration('cq04_test_read','Test-only CQ04 boundary.',frozenset({'delivery_forecast'}))
        host=CommonHost(identity,self.database,agent_registrations=(registration,))
        with host.request(DevelopmentContext(reader.subject)) as session:
            result=session.agent('cq04_test_read').call('delivery_forecast',self.arguments(version))
            self.assertTrue(result['ok'],result)
            self.assertEqual(960,result['data']['on_time_quantity'])
            self.error_is(session.agent('product').call('delivery_forecast',self.arguments(version)),'TOOL_NOT_ALLOWED')
            self.error_is(session.agent('cq04_test_read').call('prepare_draft',{}),'TOOL_NOT_ALLOWED')
        with host.request(DevelopmentContext(denied.subject)) as session:
            self.error_is(session.agent('cq04_test_read').call('delivery_forecast',self.arguments(version)),'FORBIDDEN')
        self.assertEqual(version,self.service.lookup('delivery-reader')['data_version'])


if __name__=='__main__':
    unittest.main()
