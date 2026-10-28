"""CQ03 복구 공백: 정확 초안 승인, 읽기 불변성, 추가 입력·권한 경계.

원래 Service/Host/계산기와 독립 정답을 사용한다. 파일 출력은 하지 않으며,
실행기가 성공 시 ``evidence``의 실제 반환 응답을 저장할 수 있다.
"""
import copy
from datetime import datetime
from fractions import Fraction
import hashlib
import json
import unittest
import uuid

from examples.order_fulfillment import b_production_agent as agent
from examples.order_fulfillment.b_host_agent import REGISTRATIONS
from ontologics.demo import PRINCIPALS
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.model import ROOT, canonical, validate_graph
from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.order_fulfillment.host import CommonHost
from ontologics.order_fulfillment.production import (
    AS_OF, ROUND, WORK, SOURCE_PATH, REQUIRED_CONFIRMATIONS,
    calculate_completion, load_demo_production, production_completion,
)
from ontologics.order_fulfillment.runtime import ModelRuntime
from ontologics.order_fulfillment.service import Service
from tests.db import database_for_test


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class CQ03RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.database = database_for_test(self)
        self.author = UserIdentity('cq03-author', frozenset({'read', 'draft', 'review'}))
        self.reviewer = UserIdentity('cq03-reviewer', frozenset({'read', 'review'}))
        self.reader = UserIdentity('cq03-reader', frozenset({'read'}))
        users = [self.author, self.reviewer, self.reader]
        self.service = Service(self.database, principals={**PRINCIPALS,
            **{u.actor: u.principal() for u in users}})
        self.addCleanup(self.service.close)
        self.host = CommonHost(DevelopmentIdentityProvider(users, enabled=True), self.database,
            agent_registrations=tuple(r for r in REGISTRATIONS if r.name == 'b_production'))
        self.base = json.loads((ROOT / 'data/catalog.json').read_text())['nodes'] + load_demo_contract()
        seeded = self.service.bootstrap('seed-admin', self.base, request_key='cq03-recovery-seed')
        self.assertTrue(seeded['ok'], seeded)
        self.before_version = seeded['data_version']
        self.expected = json.loads((ROOT / 'data/order_fulfillment/b_production_expected.json').read_text())

    def error_is(self, response, code):
        self.assertFalse(response['ok'], response)
        self.assertEqual(code, response['errors'][0]['code'], response)

    def draft(self):
        with self.host.request(DevelopmentContext(self.author.subject)) as request:
            response = request.call('prepare_draft', dict(operations=[{'op': 'put', 'node': n}
                for n in load_demo_production()], request_key='cq03-recovery-draft'))
        self.assertTrue(response['ok'], response)
        self.assertTrue(response['data']['validation']['valid'], response)
        self.stored_draft_response = self.service.get_draft(self.author.actor,
            response['data']['draft_id'], response['data']['draft_version'])
        self.assertTrue(self.stored_draft_response['ok'], self.stored_draft_response)
        return response

    def review(self, did, version, key, user=None):
        with self.host.request(DevelopmentContext((user or self.reviewer).subject)) as request:
            return request.review(did, version, decision='approve',
                note='가상 생산 자료의 정확한 초안에 대한 개발 검토이며 실제 운영 승인이 아님.',
                request_key=key)

    def rows(self):
        return {table: self.service.db.execute('SELECT * FROM ' + table + ' ORDER BY ' + order).fetchall()
            for table, order in [('snapshots', 'version'), ('drafts', 'id,version'),
                ('review_events', 'sequence'), ('evidence_vectors', 'data_version,evidence_id,model')]}

    def query(self, version):
        return self.service.production_completion(self.reader.actor, WORK, ROUND, AS_OF, version=version)

    def task(self, version):
        return dict(mode='production', production_work_id=WORK, delivery_round_id=ROUND,
            as_of=AS_OF, data_version=version, question='이 작업의 조건부 생산 완료 예상과 근거를 확인해줘.')

    def run_host(self, task, provider=None, user=None):
        with self.host.request(DevelopmentContext((user or self.reader).subject)) as request:
            return request.run_agent('b_production', provider or agent.ProvenanceProvider(
                agent.ScriptedProvider(self.service, task), task),
                [{'role': 'user', 'content': canonical(task)}], config=agent.CONFIG, capture_tool_trace=True)

    def test_operational_draft_exact_review_apply_and_reads_preserve_all_business_rows(self):
        execution_id = str(uuid.uuid4())
        prepared = self.draft()
        draft = prepared['data']
        did, dv = draft['draft_id'], draft['draft_version']
        self.assertEqual(self.author.actor, self.stored_draft_response['data']['author'])
        premature = self.host.apply_approved(did, dv, request_key='cq03-before-approval')
        self.error_is(premature, 'APPROVAL_REQUIRED')
        unapproved = self.query(self.before_version)
        self.error_is(unapproved, 'NOT_FOUND')
        self_review = self.review(did, dv, 'cq03-self-review', self.author)
        self.error_is(self_review, 'SELF_APPROVAL_FORBIDDEN')
        reviewed = self.review(did, dv, 'cq03-separate-review')
        self.assertTrue(reviewed['ok'], reviewed)
        self.assertEqual(draft['content_hash'], reviewed['data']['content_hash'])
        self.assertEqual(self.reviewer.actor, reviewed['data']['reviewer'])
        applied = self.host.apply_approved(did, dv, request_key='cq03-exact-apply')
        self.assertTrue(applied['ok'], applied)
        version = applied['data_version']
        self.assertEqual((dv, 2), (applied['data']['draft_version'], version))
        before = self.rows()
        audit_before = self.service.db.execute('SELECT COUNT(*) AS n FROM runs').fetchone()['n']
        fresh = Service(self.database, principals={self.reader.actor: self.reader.principal()})
        try:
            response = fresh.production_completion(self.reader.actor, WORK, ROUND, AS_OF, version=version)
        finally:
            fresh.close()
        self.assertTrue(response['ok'], response)
        keys = ('production_work_id', 'delivery_round_id', 'snapshot_id', 'as_of', 'status',
            'configuration_id', 'unit', 'target_quantity', 'latest_good_quantity',
            'selected_observation_ids', 'good_rate_per_hour', 'produced_rate_per_hour',
            'recent_defect_ratio', 'remaining_quantity', 'remaining_seconds',
            'estimated_completion_at', 'fulfillment_assessed')
        comparisons = {k: response['data'][k] == self.expected[k] for k in keys}
        self.assertTrue(all(comparisons.values()), comparisons)
        task = self.task(version)
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
            actual = next(t for t in result['tool_trace'] if t['name'] == 'production_completion')
            self.assertEqual(response['data']['selected_observation_ids'], actual['result']['data']['selected_observation_ids'])
            self.assertEqual(response['data']['remaining_seconds'], actual['result']['data']['remaining_seconds'])
            row = self.service.db.execute('SELECT actor,config,output_hash FROM runtime_runs WHERE id=%s',
                (result['run_id'],)).fetchone()
            self.assertEqual(self.reader.actor, row['actor'])
            self.assertEqual({'lookup', 'production_completion'}, set(json.loads(row['config'])['allowed_tools']))
            self.assertEqual(result['output_hash'], row['output_hash'])
        historical = self.query(self.before_version)
        self.error_is(historical, 'NOT_FOUND')
        self.assertEqual(version, self.service.lookup(self.reader.actor)['data_version'])
        after = self.rows()
        self.assertEqual(before, after)
        audit_after = self.service.db.execute('SELECT COUNT(*) AS n FROM runs').fetchone()['n']
        self.assertGreater(audit_after, audit_before)
        data = response['data']
        previous, latest = data['observations']
        elapsed_delta = datetime.fromisoformat(latest['observed_at']) - datetime.fromisoformat(previous['observed_at'])
        elapsed = Fraction(elapsed_delta.days * 86400000000 + elapsed_delta.seconds * 1000000 + elapsed_delta.microseconds, 1000000)
        increments = {k: latest[k] - previous[k] for k in ('produced_quantity', 'good_quantity', 'defective_quantity')}
        good_rate = Fraction(increments['good_quantity'] * 3600, 1) / elapsed
        produced_rate = Fraction(increments['produced_quantity'] * 3600, 1) / elapsed
        defect_ratio = Fraction(increments['defective_quantity'], increments['produced_quantity'])
        remaining = data['target_quantity'] - latest['good_quantity']
        exact_seconds = Fraction(remaining * 3600, 1) / good_rate
        ceiling = -(-exact_seconds.numerator // exact_seconds.denominator)
        self.assertEqual((3600, 90, 100, Fraction(1, 10), 400, 16000),
            (elapsed, good_rate, produced_rate, defect_ratio, remaining, ceiling))
        self.assertEqual(ceiling, data['remaining_seconds'])
        self.assertIsNone(data['actual_completion_at'])
        self.assertEqual('in_progress', data['work']['production_state'])
        def fraction(value):
            return dict(numerator=value.numerator, denominator=value.denominator)
        inputs = [SOURCE_PATH, 'data/order_fulfillment/b_production_fixture.json',
            'data/order_fulfillment/b_production_expected.json', 'data/order_fulfillment/b_production_graph.jsonld']
        self.evidence = dict(execution_id=execution_id,
            inputs=[dict(path=str(ROOT / p), sha256=hashlib.sha256((ROOT / p).read_bytes()).hexdigest()) for p in inputs],
            query_input=dict(production_work_id=WORK, delivery_round_id=ROUND, as_of=AS_OF, data_version=version),
            prepared_response=prepared, draft_read_response=copy.deepcopy(self.stored_draft_response),
            premature_apply_response=premature, self_review_response=self_review,
            review_response=reviewed, apply_response=applied, unapproved_service_response=unapproved,
            service_response=response, historical_service_response=historical,
            standalone_runtime_response=standalone, standalone_parsed_answer=standalone_answer,
            standalone_checks=standalone_checks, host_runtime_response=hosted,
            host_parsed_answer=host_answer, host_checks=host_checks, expected_comparison=comparisons,
            independently_calculated_intermediates=dict(policy='Service 응답과 별도로 선택 관측에서 Fraction 계산',
                selected_observations=copy.deepcopy(data['observations']), interval_seconds=fraction(elapsed),
                increments=increments, good_rate_per_hour=fraction(good_rate),
                produced_rate_per_hour=fraction(produced_rate), recent_defect_ratio=fraction(defect_ratio),
                remaining_quantity=remaining, exact_remaining_seconds=fraction(exact_seconds),
                final_ceiling_seconds=ceiling),
            business_rows_before=before, business_rows_after=after,
            business_invariance=dict(equal=before == after, before_sha256=digest(before),
                after_sha256=digest(after), tables={k: len(v) for k, v in before.items()}, confirmed_version=version),
            audit_runs=dict(before=audit_before, after=audit_after), separate_connection_verified=True,
            actual_api_calls=0, response_policy='이번 실행이 반환한 전체 응답. 파싱값과 독립 계산값은 별도 필드.')

    def test_superseded_draft_cannot_replace_an_unapproved_revision(self):
        draft = self.draft()['data']
        did, dv = draft['draft_id'], draft['draft_version']
        self.assertTrue(self.review(did, dv, 'cq03-review-old')['ok'])
        with self.host.request(DevelopmentContext(self.author.subject)) as request:
            revised = request.call('prepare_draft', dict(operations=copy.deepcopy(self.stored_draft_response['data']['operations']),
                request_key='cq03-revised', draft_id=did, expected_version=dv))
        self.assertTrue(revised['ok'], revised)
        next_version = revised['data']['draft_version']
        self.assertEqual(dv + 1, next_version)
        self.error_is(self.review(did, dv, 'cq03-review-superseded'), 'SUPERSEDED_DRAFT')
        self.error_is(self.host.apply_approved(did, dv, request_key='cq03-apply-old'), 'SUPERSEDED_DRAFT')
        self.error_is(self.host.apply_approved(did, next_version, request_key='cq03-apply-unapproved'), 'APPROVAL_REQUIRED')
        self.error_is(self.host.apply_approved(did, 999, request_key='cq03-apply-absent'), 'DRAFT_NOT_FOUND')
        self.error_is(self.query(self.before_version), 'NOT_FOUND')
        self.assertEqual(self.before_version, self.service.lookup(self.reader.actor)['data_version'])
        self.assertTrue(self.review(did, next_version, 'cq03-review-new')['ok'])
        applied = self.host.apply_approved(did, next_version, request_key='cq03-apply-new')
        self.assertTrue(applied['ok'], applied)
        self.assertEqual(next_version, applied['data']['draft_version'])
        self.assertEqual(self.expected['estimated_completion_at'], self.query(applied['data_version'])['data']['estimated_completion_at'])

    def test_registered_reader_cannot_mutate_or_forge_a_reviewer(self):
        self.draft()
        before = self.rows()
        for user in (self.reader, self.author):
            for tool, arguments, code in [('prepare_draft', {}, 'TOOL_NOT_ALLOWED'),
                ('review', {}, 'TOOL_NOT_ALLOWED'), ('apply', {}, 'TOOL_NOT_ALLOWED'),
                ('lookup', {'actor': self.reviewer.actor}, 'INVALID_TOOL_ARGUMENTS')]:
                with self.subTest(user=user.actor, tool=tool):
                    def provider(**kwargs):
                        return dict(text='', tool_calls=[dict(name=tool, arguments=arguments)])
                    result = self.run_host(self.task(self.before_version), provider, user)
                    self.assertFalse(result['ok'], result)
                    self.assertEqual(code, result['error']['code'])
                    self.assertEqual([], result['tool_trace'])
        self.assertEqual(before, self.rows())
        self.assertEqual(self.before_version, self.service.lookup(self.reader.actor)['data_version'])

    def test_invalid_target_types_and_false_coverage_never_yield_a_forecast(self):
        nodes = self.base + load_demo_production()
        observations = sorted((n for n in nodes if n['type'] == 'ProductionObservation'), key=lambda n: n['observed_at'])
        for target in (True, False, 1000.0, '1000', -1, 0, None, [], {}):
            with self.subTest(target=target), self.assertRaises(ValueError):
                calculate_completion(observations[0], observations[1], target)
        for flag in REQUIRED_CONFIRMATIONS:
            with self.subTest(flag=flag):
                changed = copy.deepcopy(nodes)
                snapshot = next(n for n in changed if n['type'] == 'FulfillmentSnapshot')
                snapshot['confirmations'][flag] = False
                self.assertTrue(validate_graph(changed)['valid'])
                result = production_completion(changed, WORK, ROUND, AS_OF, queried_at='2026-10-27T10:10:00+09:00')
                self.assertEqual('needs_confirmation', result['status'])
                self.assertIsNone(result['estimated_completion_at'])
                self.assertIsNone(result['remaining_seconds'])
                self.assertTrue(any(u['field'] == flag and u['code'] == 'COVERAGE_NOT_CONFIRMED' for u in result['unknowns']))

    def test_guard_rejects_wrong_work_round_time_and_review_apply_before_execution(self):
        draft = self.draft()['data']
        self.assertTrue(self.review(draft['draft_id'], draft['draft_version'], 'cq03-guard-review')['ok'])
        applied = self.host.apply_approved(draft['draft_id'], draft['draft_version'], request_key='cq03-guard-apply')
        self.assertTrue(applied['ok'], applied)
        version = applied['data_version']
        task = self.task(version)
        args = dict(production_work_id=WORK, delivery_round_id=ROUND, as_of=AS_OF, version=version)
        calls = [dict(name='production_completion', arguments={**args, field: value})
            for field, value in [('production_work_id', 'work:other'), ('delivery_round_id', 'round:other'),
                ('as_of', '2026-10-27T11:00:00+09:00')]]
        calls += [dict(name=tool, arguments={}) for tool in ('review', 'apply')]
        before = self.rows()
        for call in calls:
            with self.subTest(call=call):
                def provider(**kwargs):
                    return dict(text='', tool_calls=[call])
                result = self.run_host(task, agent.ProvenanceProvider(provider, task))
                self.assertFalse(result['ok'], result)
                self.assertEqual('CQ03_PROVENANCE_STEP', result['error']['code'])
                self.assertEqual([], result['tool_trace'])
        self.assertEqual(before, self.rows())
        self.assertEqual(version, self.service.lookup(self.reader.actor)['data_version'])


if __name__ == '__main__':
    unittest.main()
