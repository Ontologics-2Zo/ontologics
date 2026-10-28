"""CQ06 recovery: independent approvals, reopened reads and whole business rows."""
import hashlib
import json
import unittest

from examples.order_fulfillment import b_comparison_agent as agent
from examples.order_fulfillment.b_host_agent import REGISTRATIONS
from ontologics.demo import PRINCIPALS
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.model import ROOT, canonical
from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.order_fulfillment.delivery_demo import AS_OF, ROUND, WORK, load_demo_delivery
from ontologics.order_fulfillment.host import CommonHost
from ontologics.order_fulfillment.recalculation_demo import AFTER_AS_OF, CASES, load_demo_recalculation
from ontologics.order_fulfillment.runtime import ModelRuntime
from ontologics.order_fulfillment.service import AgentGateway, Service
from tests.db import isolated_database


def without_query_time(value):
    """Compare independent calls' facts while retaining both original responses."""
    if isinstance(value, dict):
        return {key: without_query_time(item) for key, item in value.items() if key != 'queried_at'}
    if isinstance(value, list):
        return [without_query_time(item) for item in value]
    return value


def business_rows(service):
    return {table: service.db.execute('SELECT * FROM ' + table + ' ORDER BY ' + order).fetchall()
        for table, order in [('snapshots', 'version'), ('drafts', 'id,version'),
            ('review_events', 'sequence'), ('evidence_vectors', 'data_version,evidence_id,model'),
            ('requests', 'actor,request_key')]}


def fixture_comparison_inputs(index):
    """Independently select this single-work fixture's compared input closure.

    The immutable database graph retains old Evidence nodes. Only evidence
    referenced by the current promise, plan and observations is a compared
    input; leaving that scope is not deletion from the approved graph.
    """
    delivery = index[ROUND]
    item = index[delivery['contract_item_id']]
    configuration = index[item['configuration_id']]
    revision = index[configuration['revision_id']]
    business_ids = {ROUND, item['id'], item['contract_id'], configuration['id'],
        revision['id'], revision['model_id'], WORK, index[WORK]['snapshot_id']}
    sid = index[WORK]['snapshot_id']
    target_allocations = {n['id'] for n in index.values() if n['type'] == 'Allocation'
        and n['delivery_round_id'] == ROUND and n['snapshot_id'] == sid}
    for node in index.values():
        if ((node['type'] in ('Allocation', 'ProductionObservation') and node.get('production_work_id') == WORK)
                or (node['type'] == 'DeliveryCondition' and node['delivery_round_id'] == ROUND
                    and node['snapshot_id'] == sid)
                or (node['type'] == 'Shipment' and node['allocation_id'] in target_allocations)):
            business_ids.add(node['id'])
    evidence_ids = {eid for nid in business_ids for eid in index[nid]['evidence']}
    source_ids = {index[eid]['source_id'] for eid in evidence_ids}
    return {nid: index[nid] for nid in business_ids | evidence_ids | source_ids}


def changed_fields(before, after):
    result = []
    for nid in sorted(before.keys() | after.keys()):
        old, new = before.get(nid, {}), after.get(nid, {})
        for field in sorted(old.keys() | new.keys()):
            if (field in old) != (field in new) or old.get(field) != new.get(field):
                result.append(dict(node_id=nid, field=field, before=old.get(field), after=new.get(field),
                    before_present=field in old, after_present=field in new,
                    unit=new.get('unit') if new else old.get('unit'),
                    evidence_by_side=dict(before=old.get('evidence', []), after=new.get('evidence', []))))
    return result


class CQ06RecoveryTests(unittest.TestCase):
    def test_five_independent_approved_pairs_reopen_and_preserve_all_business_rows(self):
        self.evidence = []
        golden = json.loads((ROOT / 'data/order_fulfillment/b_recalculation_expected.json').read_text())
        for case in CASES:
            with self.subTest(case=case), isolated_database() as database:
                self.evidence.append(self.independent_case(database, case, golden))
        self.assertEqual(list(CASES), [row['case'] for row in self.evidence])

    def independent_case(self, database, case, golden):
        author = UserIdentity('cq06-author', frozenset({'read', 'draft', 'review'}))
        reviewer = UserIdentity('cq06-reviewer', frozenset({'read', 'review'}))
        reader = UserIdentity('cq06-reader', frozenset({'read'}))
        users = [author, reviewer, reader]
        principals = {**PRINCIPALS, **{user.actor: user.principal() for user in users}}
        host = CommonHost(DevelopmentIdentityProvider(users, enabled=True), database,
            agent_registrations=tuple(role for role in REGISTRATIONS if role.name == 'b_comparison'))
        service = Service(database, principals=principals)
        approvals = []
        try:
            seed = service.bootstrap('seed-admin', json.loads((ROOT / 'data/catalog.json').read_text())['nodes']
                + load_demo_contract(), request_key='cq06-recovery-seed')
            self.assertTrue(seed['ok'], seed)
            self.assertEqual(1, seed['data_version'])
            for label, nodes, expected_version in [('baseline', load_demo_delivery(), 2),
                    ('alternative', load_demo_recalculation(case), 3)]:
                with host.request(DevelopmentContext(author.subject)) as request:
                    prepared = request.call('prepare_draft', dict(operations=[{'op': 'put', 'node': n}
                        for n in nodes], request_key=label + '-draft'))
                self.assertTrue(prepared['ok'], prepared)
                draft = prepared['data']
                self.assertTrue(draft['validation']['valid'], draft)
                did, dv = draft['draft_id'], draft['draft_version']
                stored = service.get_draft(author.actor, did, dv)
                self.assertEqual(author.actor, stored['data']['author'])
                premature = host.apply_approved(did, dv, request_key=label + '-before-review')
                self.assertEqual('APPROVAL_REQUIRED', premature['errors'][0]['code'])
                with host.request(DevelopmentContext(author.subject)) as request:
                    self_review = request.review(did, dv, decision='approve', note='자기 승인 거부 확인',
                        request_key=label + '-self-review')
                self.assertEqual('SELF_APPROVAL_FORBIDDEN', self_review['errors'][0]['code'])
                with host.request(DevelopmentContext(reviewer.subject)) as request:
                    reviewed = request.review(did, dv, decision='approve',
                        note='별도 개발 검토자가 가상 입력을 검토함. 운영 승인이나 고객 발송 승인이 아님.',
                        request_key=label + '-review')
                self.assertTrue(reviewed['ok'], reviewed)
                self.assertNotEqual(author.actor, reviewer.actor)
                self.assertEqual((draft['content_hash'], reviewer.actor),
                    (reviewed['data']['content_hash'], reviewed['data']['reviewer']))
                applied = host.apply_approved(did, dv, request_key=label + '-exact-apply')
                self.assertTrue(applied['ok'], applied)
                self.assertEqual((dv, expected_version), (applied['data']['draft_version'], applied['data_version']))
                persisted = service.get_draft(author.actor, did, dv)
                self.assertEqual(('applied', expected_version, draft['content_hash']), tuple(
                    persisted['data'][key] for key in ('status', 'applied_version', 'content_hash')))
                approvals.append(dict(label=label, prepared=prepared, stored=stored, premature_apply=premature,
                    self_review=self_review, review=reviewed, apply=applied, persisted=persisted))
        finally:
            service.close()
        # The authoring connection is closed before all retained forecast queries.
        task = dict(mode='comparison', case=case, delivery_round_id=ROUND,
            before_version=2, before_as_of=AS_OF, after_version=3, after_as_of=AFTER_AS_OF[case],
            question='두 승인 버전의 수량·시각·입력 변경과 검토 초안 비교')
        original = {}
        audit = Service(database, principals=principals)
        try:
            rows_before = business_rows(audit)
            version_before = audit.lookup(reader.actor)['data_version']
            for side, version, at in [('before', 2, AS_OF), ('after', 3, AFTER_AS_OF[case])]:
                fresh = Service(database, principals={reader.actor: reader.principal()})
                try:
                    original[side] = dict(
                        production=fresh.production_completion(reader.actor, WORK, ROUND, at, version=version),
                        delivery=fresh.delivery_forecast(reader.actor, ROUND, at, version=version),
                        response=fresh.response_plan(reader.actor, ROUND, at, version=version),
                        graph=fresh.lookup(reader.actor, version=version))
                finally:
                    fresh.close()
                self.assertTrue(all(result['ok'] for result in original[side].values()), original[side])
            comparison = audit.compare_response_plan(reader.actor, **agent.query_arguments(task))
            self.assertTrue(comparison['ok'], comparison)
            data = comparison['data']
            self.assertEqual((2, 3), tuple(data[side]['data_version'] for side in ('before', 'after')))
            for side, expected in [('before', golden['before']), ('after', golden['cases'][case])]:
                raw = original[side]
                self.assertEqual(without_query_time(raw['production']['data']), without_query_time(data[side]['production']))
                self.assertEqual(without_query_time(raw['response']['data']), without_query_time(data[side]['response_plan']))
                self.assertEqual(without_query_time(raw['delivery']['data']),
                    without_query_time(raw['response']['data']['forecast']))
                for field, value in expected['production'].items():
                    self.assertEqual(value, raw['production']['data'][field], (case, side, field))
                for section in ('forecast', 'dispatch_request'):
                    for field, value in expected[section].items():
                        self.assertEqual(value, raw['response']['data'][section][field], (case, side, section, field))
            for section in ('quantity_deltas', 'time_deltas'):
                self.assertEqual(golden['cases'][case][section], data[section], (case, section))
            provenance = agent.resolve_provenance(audit, comparison, actor=reader.actor)
            graph_indexes = {side: {node['id']: node for node in original[side]['graph']['data']}
                for side in ('before', 'after')}
            changed = changed_fields(graph_indexes['before'], graph_indexes['after'])
            compared_inputs = {side: fixture_comparison_inputs(index) for side, index in graph_indexes.items()}
            expected_input_changes = changed_fields(compared_inputs['before'], compared_inputs['after'])
            self.assertEqual(expected_input_changes, data['changed_inputs'])
            # The approved graph keeps formerly referenced immutable evidence.
            prior_evidence = {nid: node for nid, node in graph_indexes['before'].items()
                if node['type'] in ('Evidence', 'SourceDocument')}
            self.assertTrue(all(graph_indexes['after'][nid] == node for nid, node in prior_evidence.items()))
            for side in ('before', 'after'):
                self.assertEqual(set(data['integrity_evidence_by_side'][side]),
                    {row['evidence']['id'] for row in provenance[side]})
                self.assertTrue(all(row['data_version'] == task[side + '_version'] for row in provenance[side]))
            provider = agent.ScriptedProvider(audit, task, actor=reader.actor)
            guard = agent.ProvenanceProvider(provider, task)
            standalone = ModelRuntime(audit, reader.actor, guard, config=agent.CONFIG,
                allowed_tools=agent.TOOLS, capture_tool_trace=True).run([
                    {'role': 'system', 'content': agent.PROMPT}, {'role': 'user', 'content': canonical(task)}])
            checks, parsed = agent.evaluate(audit, task, standalone, 3, actor=reader.actor)
            self.assertTrue(all(checks.values()), checks)
            self.assertEqual(standalone['text'], guard.final_submission['text'])
            with host.request(DevelopmentContext(reader.subject)) as request:
                host_guard = agent.ProvenanceProvider(agent.ScriptedProvider(audit, task, actor=reader.actor), task)
                hosted = request.run_agent('b_comparison', host_guard,
                    [{'role': 'user', 'content': canonical(task)}], config=agent.CONFIG, capture_tool_trace=True)
                denied = {name: request.agent('b_comparison').call(name, {})
                    for name in ('prepare_draft', 'review', 'apply', 'bootstrap')}
            self.assertTrue(all(r['errors'][0]['code'] == 'TOOL_NOT_ALLOWED' for r in denied.values()))
            host_checks, host_parsed = agent.evaluate(audit, task, hosted, 3, actor=reader.actor)
            self.assertTrue(all(host_checks.values()), host_checks)
            self.assertEqual(hosted['text'], host_guard.final_submission['text'])
            errors = {}
            for label, changes, code in [('reversed_version', dict(before_version=3, after_version=2), 'INVALID_VERSION'),
                    ('reversed_time', dict(after_as_of='2026-10-27T09:00:00+09:00'), 'INVALID_TIME'),
                    ('missing_version', dict(after_version=99), 'VERSION_NOT_FOUND')]:
                errors[label] = audit.compare_response_plan(reader.actor, **{**agent.query_arguments(task), **changes})
                self.assertEqual(code, errors[label]['errors'][0]['code'])
            readonly = AgentGateway(audit, reader.actor, allowed_tools=agent.TOOLS)
            standalone_denied = {name: readonly.call(name, {}) for name in ('prepare_draft', 'review', 'apply', 'bootstrap')}
            self.assertTrue(all(r['errors'][0]['code'] == 'TOOL_NOT_ALLOWED' for r in standalone_denied.values()))
            rows_after = business_rows(audit)
            version_after = audit.lookup(reader.actor)['data_version']
            self.assertEqual(rows_before, rows_after)
            self.assertEqual((3, 3), (version_before, version_after))
            return dict(case=case, schema=database.schema, seed=seed, approvals=approvals,
                authoring_connection_closed_before_queries=True, query_input=task,
                full_original_responses=original, full_comparison_response=comparison,
                independent_expected=dict(before=golden['before'], after=golden['cases'][case]),
                queried_at_comparison_limitation='Only dynamic queried_at is omitted for fact equality; raw responses are retained.',
                actual_full_graph_changes=changed, compared_input_nodes_by_side=compared_inputs,
                independent_expected_input_changes=expected_input_changes,
                comparison_scope_limitation='Presence in changed_inputs means selected input relevance, not approved graph existence.',
                old_immutable_evidence_preserved=True, provenance_by_version=provenance,
                standalone_runtime_response=standalone, standalone_provider_submission=guard.final_submission,
                standalone_parsed_answer=parsed, standalone_checks=checks,
                host_runtime_response=hosted, host_provider_submission=host_guard.final_submission,
                host_parsed_answer=host_parsed, host_checks=host_checks,
                compact_projection=agent.compact_response(comparison),
                python_explanation=agent.build_explanation(data), invalid_responses=errors,
                denied_agent_operations=standalone_denied, denied_host_operations=denied,
                business_rows_before=rows_before, business_rows_after=rows_after,
                business_invariance=dict(equal=True, before_sha256=hashlib.sha256(canonical(rows_before).encode()).hexdigest(),
                    after_sha256=hashlib.sha256(canonical(rows_after).encode()).hexdigest()),
                data_versions_before_after=[version_before, version_after], actual_api_calls=0)
        finally:
            audit.close()
