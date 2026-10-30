"""CQ01 intake adapter for the public task contract; no new host authority.

The bounded provider enforces calls before dispatch. This validator separately
checks their observed results; the existing DB/oracle evaluator still runs after
the task. A valid result is an unapproved candidate, never a human review.
"""
from __future__ import annotations

import hashlib
import json
import re

from examples.order_fulfillment import b_agent, b_cq01_bounded
from ontologics.contracts import AgentSpec, AgentTask, FieldSpec, PayloadSchema
from ontologics.harness import BudgetLimits, PinnedContext
from ontologics.model import canonical, digest
from ontologics.order_fulfillment.schema import SCHEMA


INPUT = PayloadSchema((FieldSpec('case_id', 'string'), FieldSpec('question', 'string'),
                       FieldSpec('business_input', 'object')))
OUTPUT = PayloadSchema((FieldSpec('data_version', 'integer'), FieldSpec('answer', 'object')))
INTAKE_SPEC = AgentSpec('b_intake', '1.0.0', b_cq01_bounded.INTAKE_PROMPT,
    INPUT, OUTPUT, frozenset(b_agent.B_INTAKE_TOOLS),
    limits=BudgetLimits(max_model_calls=4, max_tool_calls=9, max_repairs=0))

_BUSINESS_FIELDS = frozenset({'task', 'mode', 'source_id', 'source_sha256', 'source_text',
                            'evidence_ids', 'data_version', 'request_key', 'node_fields'})
_GROUPS = ('contract', 'item', 'round')
_KINDS = ('ProductModel', 'ProductRevision', 'Configuration')


def _business(value):
    """Validate the existing fixed-case input without interpreting its prose."""
    if not isinstance(value, dict) or set(value) != _BUSINESS_FIELDS:
        raise ValueError('CQ01 intake requires the exact business input fields')
    value = json.loads(canonical(value))
    if (value['mode'] != 'intake' or type(value['data_version']) is not int
            or value['data_version'] < 1
            or any(not isinstance(value[k], str) or not value[k].strip()
                   for k in ('task', 'source_id', 'source_text', 'source_sha256', 'request_key'))
            or len(value['task']) > 2000 or len(value['source_id']) > 2000
            or len(value['request_key']) > 200
            or re.fullmatch(r'[a-f0-9]{64}', value['source_sha256']) is None
            or hashlib.sha256(value['source_text'].encode()).hexdigest() != value['source_sha256']):
        raise ValueError('CQ01 input has an invalid mode, version, source hash or request key')
    evidence = value['evidence_ids']
    if (not isinstance(evidence, dict) or set(evidence) != set(_GROUPS)
            or any(not isinstance(evidence[k], str) or not evidence[k].strip()
                   or len(evidence[k]) > 2000 for k in _GROUPS)
            or len({value['source_id'], *evidence.values()}) != 4):
        raise ValueError('CQ01 requires one source and three distinct evidence IDs')
    expected_fields = {kind: SCHEMA[kind][0] for kind in ('Contract', 'ContractItem', 'DeliveryRound')}
    if value['node_fields'] != expected_fields:
        raise ValueError('CQ01 node field hints must match the existing domain contract')
    return value


def _conditions(business):
    return {key: business[key] for key in ('source_id', 'source_sha256', 'evidence_ids', 'request_key')}


def _input(task):
    if not isinstance(task, AgentTask):
        raise ValueError('Use the public AgentTask')
    data = INPUT.validate(task.data())
    business = _business(data['business_input'])
    pinned = task.context.to_dict()
    if (data['case_id'] != 'cq01_intake' or not data['question'].strip()
            or len(data['question']) > 2000 or pinned['goal'] != data['question']
            or pinned['data_version'] != business['data_version']
            or canonical(pinned['conditions']) != canonical(_conditions(business))
            or set(pinned['evidence_ids']) != set(business['evidence_ids'].values())
            or pinned['unknowns']):
        raise ValueError('CQ01 input differs from its host-pinned task')
    return data, business


def make_intake_task(business_task, *, question='가상 계약 접수 초안을 준비하고 사람 검토 대기로 남기세요.'):
    """Called by trusted startup code; no actor, reviewer or approval input."""
    business = _business(business_task)
    context = PinnedContext(question, business['data_version'], conditions=_conditions(business),
                            evidence_ids=tuple(business['evidence_ids'].values()))
    task = AgentTask(dict(case_id='cq01_intake', question=question, business_input=business), context)
    _input(task)
    return task


def _observed_reads(business, results):
    version = business['data_version']
    expected = [dict(node_id=nid, version=version)
                for nid in (business['source_id'], *business['evidence_ids'].values())]
    expected += [dict(kind=kind, version=version) for kind in _KINDS]
    if (len(results) != 9 or [r['name'] for r in results] != ['lookup'] * 7 + ['prepare_draft', 'validate_draft']
            or sorted(canonical(r['arguments']) for r in results[:7]) != sorted(map(canonical, expected))
            or any(r['result'].get('ok') is not True for r in results)
            or any(type(r['result'].get('data_version')) is not int
                   or r['result']['data_version'] != version for r in results[:7])):
        return False
    nodes = {}
    for read in results[:7]:
        if 'node_id' not in read['arguments']:
            continue
        data = read['result'].get('data')
        if (not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict)
                or data[0].get('id') != read['arguments']['node_id']):
            return False
        nodes[data[0]['id']] = data[0]
    source = nodes[business['source_id']]
    if (source.get('type') != 'SourceDocument' or source.get('origin') != 'synthetic'
            or source.get('sha256') != business['source_sha256']):
        return False
    return all(nodes[eid].get('type') == 'Evidence' and nodes[eid].get('origin') == 'synthetic'
               and nodes[eid].get('source_id') == business['source_id']
               for eid in business['evidence_ids'].values())


class IntakeAgent:
    def messages(self, task):
        data, business = _input(task)
        return [dict(role='user', content=canonical(dict(question=data['question'], business_input=business)))]

    def decode_result(self, text, task, tool_results):
        # Wrap the original JSON, with no fallback, correction or rewording.
        answer = json.loads(text)
        return dict(data_version=answer['data_version'], answer=answer)

    def validate_result(self, value, task, tool_results):
        try:
            _, business = _input(task)
            answer = b_agent._answer(canonical(value['answer']), 'intake')
            if (answer is None or type(value['data_version']) is not int
                    or value['data_version'] != business['data_version']
                    or answer['data_version'] != business['data_version']
                    or answer['status'] != 'awaiting_review' or answer['unknowns']
                    or set(answer['evidence']) != set(business['evidence_ids'].values())
                    or not _observed_reads(business, tool_results)):
                return False
            prepared, validated = tool_results[-2:]
            arguments, draft = prepared['arguments'], prepared['result']['data']
            checked = validated['result']['data']
            return (set(arguments) == {'operations', 'request_key'}
                and arguments['request_key'] == business['request_key']
                and isinstance(arguments['operations'], list)
                and draft['status'] == checked['status'] == 'awaiting_review'
                and type(draft['base_version']) is int and draft['base_version'] == business['data_version']
                and draft['content_hash'] == digest(arguments['operations'])
                and draft['validation']['valid'] is True and checked['validation']['valid'] is True
                and type(draft['draft_version']) is int
                and type(checked['draft_version']) is int
                and answer['draft_id'] == draft['draft_id'] == checked['draft_id']
                and answer['draft_version'] == draft['draft_version'] == checked['draft_version']
                and canonical(validated['arguments']) == canonical(
                    {'draft_id': draft['draft_id'], 'version': draft['draft_version']}))
        except (KeyError, TypeError, ValueError):
            return False
