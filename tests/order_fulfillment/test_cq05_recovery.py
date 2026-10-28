"""CQ05 복구: 별도 개발 승인·정확 버전·새 연결·전체 업무 행 보존."""
import hashlib
import json
import unittest

from examples.order_fulfillment import b_response_agent as agent
from examples.order_fulfillment.b_host_agent import REGISTRATIONS
from ontologics.demo import PRINCIPALS
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.model import ROOT, canonical
from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.order_fulfillment.delivery_demo import AS_OF, ROUND, load_demo_delivery
from ontologics.order_fulfillment.host import CommonHost
from ontologics.order_fulfillment.runtime import ModelRuntime
from ontologics.order_fulfillment.service import Service
from tests.db import database_for_test


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class CQ05RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.database = database_for_test(self)
        self.author = UserIdentity('cq05-author', frozenset({'read', 'draft', 'review'}))
        self.reviewer = UserIdentity('cq05-reviewer', frozenset({'read', 'review'}))
        self.reader = UserIdentity('cq05-reader', frozenset({'read'}))
        users = [self.author, self.reviewer, self.reader]
        self.service = Service(self.database, principals={**PRINCIPALS,
            **{u.actor: u.principal() for u in users}})
        self.addCleanup(self.service.close)
        self.host = CommonHost(DevelopmentIdentityProvider(users, enabled=True), self.database,
            agent_registrations=tuple(r for r in REGISTRATIONS if r.name == 'b_response'))
        base = json.loads((ROOT / 'data/catalog.json').read_text())['nodes'] + load_demo_contract()
        seeded = self.service.bootstrap('seed-admin', base, request_key='cq05-recovery-seed')
        self.assertTrue(seeded['ok'], seeded)
        self.before_version = seeded['data_version']

    def rows(self):
        return {table: self.service.db.execute('SELECT * FROM ' + table + ' ORDER BY ' + order).fetchall()
            for table, order in [('snapshots', 'version'), ('drafts', 'id,version'),
                ('review_events', 'sequence'), ('evidence_vectors', 'data_version,evidence_id,model'),
                ('requests', 'actor,request_key')]}

    def representative_flow(self, mixed):
        case = 'mixed' if mixed else 'normal'
        suffix = '_mixed' if mixed else ''
        paths = [f'data/order_fulfillment/sources/b_demo_delivery{suffix}_01.md',
            *[f'data/order_fulfillment/b_delivery{suffix}_{kind}' for kind in
                ('fixture.json', 'expected.json', 'graph.jsonld')],
            'data/order_fulfillment/b_response_expected.json']
        golden = json.loads((ROOT / paths[-1]).read_text())
        expected = golden['cases']['mixed15' if mixed else 'normal']
        nodes = load_demo_delivery(mixed=mixed)
        with self.host.request(DevelopmentContext(self.author.subject)) as request:
            prepared = request.call('prepare_draft', dict(operations=[{'op': 'put', 'node': n}
                for n in nodes], request_key='cq05-recovery-draft'))
        self.assertTrue(prepared['ok'], prepared)
        draft = prepared['data']
        self.assertTrue(draft['validation']['valid'])
        did, dv = draft['draft_id'], draft['draft_version']
        stored = self.service.get_draft(self.author.actor, did, dv)
        self.assertEqual(self.author.actor, stored['data']['author'])
        unapproved = self.service.response_plan(self.reader.actor, ROUND, AS_OF, version=self.before_version)
        self.assertEqual('needs_confirmation', unapproved['data']['response_state'])
        premature = self.host.apply_approved(did, dv, request_key='cq05-before-review')
        self.assertEqual('APPROVAL_REQUIRED', premature['errors'][0]['code'])
        with self.host.request(DevelopmentContext(self.author.subject)) as request:
            self_review = request.review(did, dv, decision='approve',
                note='자기 승인 거부 확인', request_key='cq05-self-review')
        self.assertEqual('SELF_APPROVAL_FORBIDDEN', self_review['errors'][0]['code'])
        with self.host.request(DevelopmentContext(self.reviewer.subject)) as request:
            reviewed = request.review(did, dv, decision='approve',
                note='가상 시험 자료의 별도 개발 검토이며 운영 승인이나 응답 초안 승인이 아님.',
                request_key='cq05-separate-review')
        self.assertTrue(reviewed['ok'], reviewed)
        self.assertEqual((draft['content_hash'], self.reviewer.actor),
            (reviewed['data']['content_hash'], reviewed['data']['reviewer']))
        applied = self.host.apply_approved(did, dv, request_key='cq05-exact-apply')
        self.assertTrue(applied['ok'], applied)
        version = applied['data_version']
        self.assertEqual((dv, 2), (applied['data']['draft_version'], version))
        persisted = self.service.get_draft(self.author.actor, did, dv)
        self.assertEqual(('applied', version, draft['content_hash']),
            tuple(persisted['data'][k] for k in ('status', 'applied_version', 'content_hash')))
        before = self.rows()
        fresh = Service(self.database, principals={self.reader.actor: self.reader.principal()})
        try:
            response = fresh.response_plan(self.reader.actor, ROUND, AS_OF, version=version)
            cq04 = fresh.delivery_forecast(self.reader.actor, ROUND, AS_OF, version=version)
        finally:
            fresh.close()
        self.assertTrue(response['ok'], response)
        data = response['data']
        self.assertEqual({k: v for k, v in cq04['data'].items() if k != 'queried_at'},
            {k: v for k, v in data['forecast'].items() if k != 'queried_at'})
        self.assertEqual(expected['response_state'], data['response_state'])
        comparisons = {section: {k: data[section][k] == v for k, v in expected[section].items()}
            for section in ('forecast', 'dispatch_request')}
        self.assertTrue(all(v for section in comparisons.values() for v in section.values()), comparisons)
        self.assertEqual(golden['selected_evidence'][case], data['evidence'])
        # 독립 fixture의 출발 사건/관측/배정에서 수량을 대조한다.
        allocations = [n for n in nodes if n['type'] == 'Allocation']
        shipments = [n for n in nodes if n['type'] == 'Shipment' and n.get('departed_at')]
        departed = sum(n['quantity'] for n in shipments)
        latest = max((n for n in nodes if n['type'] == 'ProductionObservation'), key=lambda n: n['observed_at'])
        work = next(n for n in nodes if n['type'] == 'ProductionWork')
        future = work['target_quantity'] - latest['good_quantity']
        new = sum(n['quantity'] for n in allocations) - departed
        independent = dict(allocated=sum(n['quantity'] for n in allocations),already_departed=departed,
            new_dispatch=new,currently_available=new-future,future_production=future)
        self.assertEqual((new, new-future, future), tuple(data['dispatch_request'][k]
            for k in ('new_dispatch_quantity', 'currently_available_quantity', 'future_quantity')))
        self.assertTrue(all(i['source_state'] not in ('delivered', 'in_transit')
            for i in data['dispatch_request']['items']))
        task = dict(mode='response', case=case, delivery_round_id=ROUND, as_of=AS_OF,
            data_version=version, question='고객 회신·미출고 요청·확인 사항 검토 초안')
        standalone = ModelRuntime(self.service, self.reader.actor, agent.ProvenanceProvider(
            agent.ScriptedProvider(self.service, task, actor=self.reader.actor), task),
            config=agent.CONFIG, allowed_tools=agent.TOOLS, capture_tool_trace=True).run([
                {'role': 'system', 'content': agent.PROMPT}, {'role': 'user', 'content': canonical(task)}])
        standalone_checks, standalone_answer = agent.evaluate(
            self.service, task, standalone, version, actor=self.reader.actor)
        self.assertTrue(all(standalone_checks.values()), standalone_checks)
        with self.host.request(DevelopmentContext(self.reader.subject)) as request:
            hosted = request.run_agent('b_response', agent.ProvenanceProvider(
                agent.ScriptedProvider(self.service, task, actor=self.reader.actor), task),
                [{'role': 'user', 'content': canonical(task)}], config=agent.CONFIG, capture_tool_trace=True)
            denied = {name: request.agent('b_response').call(name, {})
                for name in ('prepare_draft', 'review', 'apply')}
        self.assertTrue(all(r['errors'][0]['code'] == 'TOOL_NOT_ALLOWED' for r in denied.values()))
        host_checks, host_answer = agent.evaluate(self.service, task, hosted, version, actor=self.reader.actor)
        self.assertTrue(all(host_checks.values()), host_checks)
        for result in (standalone, hosted):
            self.assertEqual(['response_plan', 'lookup_many', 'lookup_many'],
                [t['name'] for t in result['tool_trace']])
            self.assertEqual(data['dispatch_request'], result['tool_trace'][0]['result']['data']['dispatch_request'])
        historical = self.service.response_plan(self.reader.actor, ROUND, AS_OF, version=self.before_version)
        self.assertEqual((self.before_version, 'needs_confirmation'),
            (historical['data_version'], historical['data']['response_state']))
        self.assertEqual(version, self.service.lookup(self.reader.actor)['data_version'])
        after = self.rows()
        self.assertEqual(before, after)
        self.assertTrue(data['draft_only'] and data['review_required'] and not data['execution_performed'])
        self.evidence = dict(execution_id=hosted['run_id'],case=case,
            inputs=[dict(path=str(ROOT/p),sha256=hashlib.sha256((ROOT/p).read_bytes()).hexdigest()) for p in paths],
            query_input=task,condition=data['forecast']['condition'],prepared_response=prepared,
            draft_read_response=stored,persisted_draft_response=persisted,
            unapproved_service_response=unapproved,premature_apply_response=premature,
            self_review_response=self_review,review_response=reviewed,apply_response=applied,
            service_response=response,cq04_service_response=cq04,cq04_preserved=True,
            independent_expected=expected,independent_comparison=comparisons,
            independent_dispatch_calculation=independent,selected_evidence=data['evidence'],
            standalone_runtime_response=standalone,standalone_parsed_answer=standalone_answer,
            standalone_checks=standalone_checks,host_runtime_response=hosted,host_parsed_answer=host_answer,
            host_checks=host_checks,denied_agent_operations=denied,historical_service_response=historical,
            business_rows_before=before,business_rows_after=after,
            business_invariance=dict(equal=True,before_sha256=digest(before),after_sha256=digest(after)),
            actual_api_calls=0)

    def test_normal_exact_approval_fresh_connection_and_business_invariance(self):
        self.representative_flow(False)

    def test_mixed_exact_approval_fresh_connection_and_business_invariance(self):
        self.representative_flow(True)
