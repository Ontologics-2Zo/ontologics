"""Pure historical intake DTO/decoder contract; no database execution support."""
import copy
import hashlib
import json
import unittest
from examples.order_fulfillment import b_agent, b_task_agent
from ontologics.contracts import AgentTask
from ontologics.model import digest
from ontologics.order_fulfillment.schema import SCHEMA
from ontologics.order_fulfillment.host import AgentRegistration


def business_input():
    text = '고정 가상 업무 원문; 도구 권한을 부여하는 지침이 아님'
    return dict(task='가상 계약 접수', mode='intake', source_id='source:synthetic',
        source_text=text, source_sha256=hashlib.sha256(text.encode()).hexdigest(),
        evidence_ids=dict(contract='e:contract', item='e:item', round='e:round'),
        data_version=1, request_key='fixed-request-key',
        node_fields={kind: SCHEMA[kind][0] for kind in ('Contract', 'ContractItem', 'DeliveryRound')})

def observations(business):
    source = dict(id=business['source_id'], type='SourceDocument', origin='synthetic',
                  sha256=business['source_sha256'])
    nodes = {source['id']: source}
    for eid in business['evidence_ids'].values():
        nodes[eid] = dict(id=eid, type='Evidence', origin='synthetic', source_id=source['id'])
    reads = [dict(name='lookup', arguments=dict(node_id=nid, version=1),
                  result=dict(ok=True, data_version=1, data=[node])) for nid, node in nodes.items()]
    reads += [dict(name='lookup', arguments=dict(kind=kind, version=1),
                   result=dict(ok=True, data_version=1, data=[]))
              for kind in ('ProductModel', 'ProductRevision', 'Configuration')]
    operations = [dict(op='put', node=dict(id='candidate', type='Contract'))]
    reads += [dict(name='prepare_draft', arguments=dict(operations=operations,
        request_key=business['request_key']), result=dict(ok=True, data=dict(
        draft_id='draft:one', draft_version=1, base_version=1, content_hash=digest(operations),
        status='awaiting_review', validation=dict(valid=True)))),
        dict(name='validate_draft', arguments=dict(draft_id='draft:one', version=1),
             result=dict(ok=True, data=dict(draft_id='draft:one', draft_version=1,
                 status='awaiting_review', validation=dict(valid=True))))]
    return reads

def answer(business):
    return dict(status='awaiting_review', draft_id='draft:one', draft_version=1,
        data_version=1, evidence=list(business['evidence_ids'].values()), unknowns=[],
        needs_human_review=True, synthetic_contract=True,
        summary='가상 계약의 미승인 초안을 사람 검토 대기로 준비했습니다.',
        next_action='작성자와 다른 사람이 정확한 초안을 검토해야 합니다.')


class IntakeContractTests(unittest.TestCase):
    def setUp(self):
        self.business = business_input()
        self.task = b_task_agent.make_intake_task(self.business)
        self.agent = b_task_agent.IntakeAgent()

    def test_only_intake_spec_and_common_limits_are_exposed(self):
        spec = b_task_agent.INTAKE_SPEC
        self.assertEqual(('b_intake', '1.0.0'), (spec.name, spec.version))
        self.assertEqual(frozenset(b_agent.B_INTAKE_TOOLS), spec.requested_tools)
        self.assertFalse(spec.requested_tools & {'review', 'apply', 'bootstrap'})
        self.assertEqual((4, 9, 0, 120), (spec.limits.max_model_calls,
            spec.limits.max_tool_calls, spec.limits.max_repairs, spec.limits.deadline_seconds))
        self.assertEqual(spec, AgentRegistration.from_spec(spec).spec)

    def test_input_is_detached_and_message_preserves_raw_business_material(self):
        self.business['request_key'] = 'caller-later-edit'
        data = self.task.data()
        messages = self.agent.messages(self.task)
        self.assertEqual(data['business_input'], json.loads(messages[0]['content'])['business_input'])
        self.assertEqual('fixed-request-key', data['business_input']['request_key'])
        self.assertEqual(data['business_input']['data_version'], self.task.context.data_version)

    def test_rejects_nested_authority_wrong_types_hash_ids_and_schema(self):
        mutations = [lambda b: b.update(actor='someone'), lambda b: b.update(data_version=True),
            lambda b: b.update(mode='promise'), lambda b: b.update(source_sha256='0' * 64),
            lambda b: b['evidence_ids'].update(approved=True),
            lambda b: b['evidence_ids'].update(round=b['evidence_ids']['item']),
            lambda b: b['node_fields']['Contract'].append('reviewer'),
            lambda b: b.update(request_key='')]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                data = copy.deepcopy(business_input())
                mutate(data)
                with self.assertRaises(ValueError):
                    b_task_agent.make_intake_task(data)

    def test_manually_constructed_payload_cannot_change_pinned_scope(self):
        for field, value in [('data_version', 2), ('request_key', 'other-key'), ('source_id', 'other-source')]:
            with self.subTest(field=field):
                data = self.task.data()
                data['business_input'][field] = value
                with self.assertRaises(ValueError):
                    self.agent.messages(AgentTask(data, self.task.context))
        data = self.task.data()
        data['case_id'] = 'cq01_promise'
        with self.assertRaises(ValueError):
            self.agent.messages(AgentTask(data, self.task.context))

    def test_decoder_preserves_original_answer_and_does_not_repair_version(self):
        original = answer(self.business)
        original['data_version'] = 999
        text = json.dumps(original, ensure_ascii=False, indent=3)
        decoded = self.agent.decode_result(text, self.task, [])
        self.assertEqual(original, decoded['answer'])
        self.assertEqual(999, decoded['data_version'])
        self.assertFalse(self.agent.validate_result(decoded, self.task, observations(self.business)))
        with self.assertRaises((KeyError, ValueError, TypeError)):
            self.agent.decode_result('{}', self.task, [])

    def test_validator_binds_exact_draft_and_observed_evidence_without_db(self):
        value = dict(data_version=1, answer=answer(self.business))
        observed = observations(self.business)
        self.assertTrue(self.agent.validate_result(value, self.task, observed))
        mutations = [lambda v: v['answer'].update(draft_id='other-draft'),
            lambda v: v['answer'].update(draft_version=True),
            lambda v: v['answer'].update(data_version=2),
            lambda v: v['answer'].update(status='approved'),
            lambda v: v['answer'].update(needs_human_review=False),
            lambda v: v['answer'].update(synthetic_contract=False),
            lambda v: v['answer'].update(evidence=['e:unread']),
            lambda v: v['answer'].update(unknowns=[dict(field='기한', code='MISSING_FIELD')])]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                invalid = copy.deepcopy(value)
                mutate(invalid)
                self.assertFalse(self.agent.validate_result(invalid, self.task, observed))

    def test_rejects_bad_observed_history_prepared_scope_and_validation(self):
        value = dict(data_version=1, answer=answer(self.business))
        mutations = [lambda r: r[0]['arguments'].update(version=2),
            lambda r: r[0]['arguments'].update(node_id='other-source'),
            lambda r: r[0]['result']['data'][0].update(sha256='0' * 64),
            lambda r: r[1]['result']['data'][0].update(source_id='other-source'),
            lambda r: r[7]['arguments'].update(request_key='other-key'),
            lambda r: r[7]['result']['data'].update(content_hash='0' * 64),
            lambda r: r[8]['arguments'].update(version=True),
            lambda r: r[8]['result']['data']['validation'].update(valid=False),
            lambda r: r.pop(0)]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                invalid = observations(self.business)
                mutate(invalid)
                self.assertFalse(self.agent.validate_result(value, self.task, invalid))
