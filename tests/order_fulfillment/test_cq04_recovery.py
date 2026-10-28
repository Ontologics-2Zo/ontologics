"""CQ04 복구 공백: 별도 승인·새 연결·전체 업무 행 불변성.

기존 계산기·Service·Agent·Host와 독립 expected를 그대로 사용한다.
실제 반환 응답은 self.evidence에 보관하며 파일 출력은 실행기가 맡는다.
"""
import copy
from datetime import datetime, timedelta
from fractions import Fraction
import hashlib
import json
import unittest
import uuid

from examples.order_fulfillment import b_delivery_agent as agent
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


def fraction(value):
    return dict(numerator=value.numerator, denominator=value.denominator)


class CQ04RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.database = database_for_test(self)
        self.author = UserIdentity('cq04-author', frozenset({'read', 'draft', 'review'}))
        self.reviewer = UserIdentity('cq04-reviewer', frozenset({'read', 'review'}))
        self.reader = UserIdentity('cq04-reader', frozenset({'read'}))
        users = [self.author, self.reviewer, self.reader]
        self.service = Service(self.database, principals={**PRINCIPALS,
            **{u.actor: u.principal() for u in users}})
        self.addCleanup(self.service.close)
        self.host = CommonHost(DevelopmentIdentityProvider(users, enabled=True), self.database,
            agent_registrations=tuple(r for r in REGISTRATIONS if r.name == 'b_delivery'))
        base = json.loads((ROOT / 'data/catalog.json').read_text())['nodes'] + load_demo_contract()
        seeded = self.service.bootstrap('seed-admin', base, request_key='cq04-recovery-seed')
        self.assertTrue(seeded['ok'], seeded)
        self.before_version = seeded['data_version']

    def error_is(self, response, code):
        self.assertFalse(response['ok'], response)
        self.assertEqual(code, response['errors'][0]['code'], response)

    def draft(self, mixed=False):
        with self.host.request(DevelopmentContext(self.author.subject)) as request:
            response = request.call('prepare_draft', dict(operations=[{'op': 'put', 'node': n}
                for n in load_demo_delivery(mixed=mixed)], request_key='cq04-recovery-draft'))
        self.assertTrue(response['ok'], response)
        self.assertTrue(response['data']['validation']['valid'], response)
        data = response['data']
        stored = self.service.get_draft(self.author.actor, data['draft_id'], data['draft_version'])
        self.assertTrue(stored['ok'], stored)
        return response, stored

    def review(self, did, version, key, user=None):
        with self.host.request(DevelopmentContext((user or self.reviewer).subject)) as request:
            return request.review(did, version, decision='approve',
                note='가상 납품 조건의 정확한 초안에 대한 별도 개발 검토이며 실제 운영 승인이 아님.',
                request_key=key)

    def rows(self):
        return {table: self.service.db.execute('SELECT * FROM ' + table + ' ORDER BY ' + order).fetchall()
            for table, order in [('snapshots', 'version'), ('drafts', 'id,version'),
                ('review_events', 'sequence'), ('evidence_vectors', 'data_version,evidence_id,model'),
                ('requests', 'actor,request_key')]}

    def query(self, version):
        return self.service.delivery_forecast(self.reader.actor, ROUND, AS_OF, version=version)

    def task(self, version, mixed=False):
        return dict(mode='delivery', case='mixed' if mixed else 'normal',
            delivery_round_id=ROUND, as_of=AS_OF, data_version=version,
            question='기한 내 수량·부족량·조건부 전량 도착 예상과 근거를 확인해줘.')

    def run_host(self, task, provider=None, user=None):
        with self.host.request(DevelopmentContext((user or self.reader).subject)) as request:
            return request.run_agent('b_delivery', provider or agent.ProvenanceProvider(
                agent.ScriptedProvider(self.service, task), task),
                [{'role': 'user', 'content': canonical(task)}], config=agent.CONFIG, capture_tool_trace=True)

    def representative_flow(self, mixed):
        suffix = '_mixed' if mixed else ''
        paths = [f'data/order_fulfillment/sources/b_demo_delivery{suffix}_01.md']
        paths += [f'data/order_fulfillment/b_delivery{suffix}_{kind}'
            for kind in ('fixture.json', 'expected.json', 'graph.jsonld')]
        expected = json.loads((ROOT / paths[2]).read_text())
        prepared, stored = self.draft(mixed)
        draft = prepared['data']
        did, dv = draft['draft_id'], draft['draft_version']
        self.assertEqual(self.author.actor, stored['data']['author'])
        premature = self.host.apply_approved(did, dv, request_key='cq04-before-approval')
        self.error_is(premature, 'APPROVAL_REQUIRED')
        unapproved = self.query(self.before_version)
        self.assertTrue(unapproved['ok'], unapproved)
        self.assertIsNone(unapproved['data']['on_time_quantity'])
        self_review = self.review(did, dv, 'cq04-self-review', self.author)
        self.error_is(self_review, 'SELF_APPROVAL_FORBIDDEN')
        reviewed = self.review(did, dv, 'cq04-separate-review')
        self.assertTrue(reviewed['ok'], reviewed)
        self.assertEqual(draft['content_hash'], reviewed['data']['content_hash'])
        self.assertEqual(self.reviewer.actor, reviewed['data']['reviewer'])
        applied = self.host.apply_approved(did, dv, request_key='cq04-exact-apply')
        self.assertTrue(applied['ok'], applied)
        version = applied['data_version']
        self.assertEqual((dv, 2), (applied['data']['draft_version'], version))
        before = self.rows()
        fresh = Service(self.database, principals={self.reader.actor: self.reader.principal()})
        try:
            response = fresh.delivery_forecast(self.reader.actor, ROUND, AS_OF, version=version)
        finally:
            fresh.close()
        self.assertTrue(response['ok'], response)
        keys = ('delivery_round_id', 'snapshot_id', 'as_of', 'configuration_id', 'unit',
            'deadline_status', 'on_time_quantity', 'shortfall_quantity', 'estimated_full_arrival_at',
            'forecast_rule_version')
        comparisons = {k: response['data'][k] == expected[k] for k in keys}
        self.assertTrue(all(comparisons.values()), comparisons)
        task = self.task(version, mixed)
        standalone = ModelRuntime(self.service, self.reader.actor, agent.ProvenanceProvider(
            agent.ScriptedProvider(self.service, task), task), config=agent.CONFIG,
            allowed_tools=agent.TOOLS, capture_tool_trace=True).run([
                {'role': 'system', 'content': agent.PROMPT}, {'role': 'user', 'content': canonical(task)}])
        standalone_checks, standalone_answer = agent.evaluate(
            self.service, task, standalone, version, actor=self.reader.actor)
        self.assertTrue(all(standalone_checks.values()), standalone_checks)
        hosted = self.run_host(task)
        host_checks, host_answer = agent.evaluate(self.service, task, hosted, version, actor=self.reader.actor)
        self.assertTrue(all(host_checks.values()), host_checks)
        for result in (standalone, hosted):
            self.assertEqual(['delivery_forecast', 'lookup_many', 'lookup_many'],
                [t['name'] for t in result['tool_trace']])
            self.assertEqual(response['data']['breakdown'], result['tool_trace'][0]['result']['data']['breakdown'])
            row = self.service.db.execute('SELECT actor,config,output_hash FROM runtime_runs WHERE id=%s',
                (result['run_id'],)).fetchone()
            self.assertEqual(self.reader.actor, row['actor'])
            self.assertEqual(set(agent.TOOLS), set(json.loads(row['config'])['allowed_tools']))
            self.assertEqual(result['output_hash'], row['output_hash'])
        historical = self.query(self.before_version)
        self.assertTrue(historical['ok'], historical)
        self.assertIsNone(historical['data']['on_time_quantity'])
        self.assertEqual(version, self.service.lookup(self.reader.actor)['data_version'])
        after = self.rows()
        self.assertEqual(before, after)
        # 독립 입력 관측으로 비율·완료 초·기한 내 수량을 다시 계산한다.
        nodes = load_demo_delivery(mixed=mixed)
        observations = sorted((n for n in nodes if n['type'] == 'ProductionObservation'),
            key=lambda n: datetime.fromisoformat(n['observed_at']))
        previous, latest = observations
        interval = Fraction((datetime.fromisoformat(latest['observed_at']) -
            datetime.fromisoformat(previous['observed_at'])) // timedelta(microseconds=1), 1000000)
        increments = {k: latest[k] - previous[k] for k in ('produced_quantity', 'good_quantity', 'defective_quantity')}
        rate = Fraction(increments['good_quantity'] * 3600, 1) / interval
        work = next(n for n in nodes if n['type'] == 'ProductionWork')
        condition = next(n for n in nodes if n['type'] == 'DeliveryCondition')
        remaining = work['target_quantity'] - latest['good_quantity']
        exact_seconds = Fraction(remaining * 3600, 1) / rate
        ready_seconds = -(-exact_seconds.numerator // exact_seconds.denominator)
        delay = condition['preparation_seconds'] + condition['transport_seconds']
        start = datetime.fromisoformat(AS_OF)
        full = start + timedelta(seconds=ready_seconds + delay)
        cutoff = datetime.fromisoformat(response['data']['due_at']) - timedelta(seconds=delay)
        horizon = max(0, (cutoff - start) // timedelta(seconds=1))
        production_on_time = min(work['target_quantity'], latest['good_quantity'] +
            (rate * horizon / 3600).__floor__())
        nonproduction_on_time = 500 if mixed else 0
        self.assertEqual((90, 12000 if mixed else 16000), (rate, ready_seconds))
        self.assertEqual(expected['estimated_full_arrival_at'], full.isoformat())
        self.assertEqual(expected['on_time_quantity'], nonproduction_on_time + production_on_time)
        production_row = next(r for r in response['data']['breakdown'] if r['source_id'] == work['id'])
        self.assertEqual(production_on_time, production_row['on_time_quantity'])
        self.assertEqual(fraction(rate), production_row['good_rate_fraction'])
        self.assertIsNone(production_row['actual_completion_at'])
        self.assertEqual('in_progress', work['production_state'])
        self.evidence = dict(execution_id=str(uuid.uuid4()), case=task['case'],
            inputs=[dict(path=str(ROOT / p), sha256=hashlib.sha256((ROOT / p).read_bytes()).hexdigest()) for p in paths],
            query_input=dict(delivery_round_id=ROUND, as_of=AS_OF, data_version=version, condition=condition),
            prepared_response=prepared, draft_read_response=stored, premature_apply_response=premature,
            self_review_response=self_review, review_response=reviewed, apply_response=applied,
            unapproved_service_response=unapproved, service_response=response, historical_service_response=historical,
            standalone_runtime_response=standalone, standalone_parsed_answer=standalone_answer,
            standalone_checks=standalone_checks, host_runtime_response=hosted,
            host_parsed_answer=host_answer, host_checks=host_checks, expected_comparison=comparisons,
            independently_calculated_intermediates=dict(policy='fixture 관측에서 Fraction 독립 계산',
                selected_observations=copy.deepcopy(observations), interval_seconds=fraction(interval),
                increments=increments, good_rate_per_hour=fraction(rate), remaining_quantity=remaining,
                exact_remaining_seconds=fraction(exact_seconds), final_ceiling_seconds=ready_seconds,
                preparation_seconds=condition['preparation_seconds'], transport_seconds=condition['transport_seconds'],
                production_ready_at=(start + timedelta(seconds=ready_seconds)).isoformat(),
                full_arrival_at=full.isoformat(), production_cutoff=cutoff.isoformat(),
                deadline_horizon_seconds=horizon, production_on_time_quantity=production_on_time,
                nonproduction_on_time_quantity=nonproduction_on_time),
            business_rows_before=before, business_rows_after=after,
            business_invariance=dict(equal=before == after, before_sha256=digest(before),
                after_sha256=digest(after), tables={k: len(v) for k, v in before.items()}, confirmed_version=version),
            separate_connection_verified=True, actual_api_calls=0,
            response_policy='이번 실행의 전체 반환 응답. 파싱값·독립 계산은 별도 필드.')

    def test_base_exact_approval_fresh_connection_and_business_invariance(self):
        self.representative_flow(False)

    def test_mixed_exact_approval_fresh_connection_and_business_invariance(self):
        self.representative_flow(True)

    def test_superseded_and_unapproved_revisions_cannot_reuse_old_approval(self):
        prepared, stored = self.draft()
        draft = prepared['data']
        did, dv = draft['draft_id'], draft['draft_version']
        self.assertTrue(self.review(did, dv, 'cq04-review-old')['ok'])
        with self.host.request(DevelopmentContext(self.author.subject)) as request:
            revised = request.call('prepare_draft', dict(operations=copy.deepcopy(stored['data']['operations']),
                request_key='cq04-revised', draft_id=did, expected_version=dv))
        self.assertTrue(revised['ok'], revised)
        newer = revised['data']['draft_version']
        self.assertEqual(dv + 1, newer)
        self.error_is(self.review(did, dv, 'cq04-review-superseded'), 'SUPERSEDED_DRAFT')
        self.error_is(self.host.apply_approved(did, dv, request_key='cq04-apply-old'), 'SUPERSEDED_DRAFT')
        self.error_is(self.host.apply_approved(did, newer, request_key='cq04-apply-unapproved'), 'APPROVAL_REQUIRED')
        self.error_is(self.host.apply_approved(did, 999, request_key='cq04-apply-absent'), 'DRAFT_NOT_FOUND')
        self.assertIsNone(self.query(self.before_version)['data']['on_time_quantity'])
        self.assertEqual(self.before_version, self.service.lookup(self.reader.actor)['data_version'])
        self.assertTrue(self.review(did, newer, 'cq04-review-new')['ok'])
        applied = self.host.apply_approved(did, newer, request_key='cq04-apply-new')
        self.assertTrue(applied['ok'], applied)
        self.assertEqual(newer, applied['data']['draft_version'])
        expected = json.loads((ROOT / 'data/order_fulfillment/b_delivery_expected.json').read_text())
        self.assertEqual(expected['on_time_quantity'], self.query(applied['data_version'])['data']['on_time_quantity'])

    def test_delivery_role_cannot_use_authors_permissions_to_write_or_forge_identity(self):
        self.draft()
        before = self.rows()
        for user in (self.reader, self.author):
            for tool, arguments, code in [('prepare_draft', {}, 'TOOL_NOT_ALLOWED'),
                ('review', {}, 'TOOL_NOT_ALLOWED'), ('apply', {}, 'TOOL_NOT_ALLOWED'),
                ('lookup_many', {'actor': self.reviewer.actor}, 'INVALID_TOOL_ARGUMENTS')]:
                with self.subTest(user=user.actor, tool=tool):
                    def provider(**kwargs):
                        return dict(text='', tool_calls=[dict(name=tool, arguments=arguments)])
                    result = self.run_host(self.task(self.before_version), provider, user)
                    self.assertFalse(result['ok'], result)
                    self.assertEqual(code, result['error']['code'])
                    self.assertEqual([], result['tool_trace'])
        self.assertEqual(before, self.rows())
        self.assertEqual(self.before_version, self.service.lookup(self.reader.actor)['data_version'])


if __name__ == '__main__':
    unittest.main()
