"""Two reviewed synthetic mail sources bound to unchanged operational examples.

The registry is a trusted demo fixture, not a general natural-language parser.
Reference tables and reviewed anchors never enter the model's task payload.
"""
from __future__ import annotations

import json
from copy import deepcopy
import os
from tempfile import NamedTemporaryFile

from . import b_terminal_input as terminal
from ontologics.model import ROOT
from ontologics.harness import canonical
from .b_input_task import json_sha256


MAX_TRANSPORT_MINUTES = 7 * 24 * 60


REGISTRY = ROOT / 'data/order_fulfillment/examples/mail_demo_cases.json'
INPUTS = ('demo_normal_800_mail.md', 'demo_risk_800_mail.md')
load_input = terminal.load_input


def resolve(value):
    if not isinstance(value, dict) or set(value) != {'path', 'sha256', 'text'}:
        raise ValueError('Use an immutable reviewed mail input')
    if load_input(ROOT / value['path']) != value:
        raise ValueError('The mail input changed')
    cases = json.loads(REGISTRY.read_text())['cases']
    matches = [row for row in cases if row['mail_path'] == value['path']
               and row['mail_sha256'] == value['sha256']]
    if len(matches) != 1:
        raise ValueError('Select one of the two reviewed mail examples')
    record = matches[0]
    baseline = terminal.load_input(ROOT / record['baseline_path'])
    if baseline['sha256'] != record['baseline_sha256']:
        raise ValueError('The separately reviewed operational example changed')
    if any(not isinstance(anchor, str) or not anchor or anchor not in value['text']
           for anchor in record['anchors'].values()):
        raise ValueError('The reviewed source anchors no longer match the mail')
    return baseline, record['anchors']


def reference_task(value, *, version=1):
    baseline, _ = resolve(value)
    return terminal.case_task('cq01_intake', baseline, version=version)[1]


def case_task(case, value, *, version=1):
    baseline, _ = resolve(value)
    if case == 'cq01_intake':
        from . import b_mail_intake_task as mail
        return (mail, mail.MailIntakeAgent, mail.ScriptedMailProvider), mail.make_task(value, version=version)
    # These are independent scenario calculations, not approved intake results.
    return terminal.case_task(case, baseline, version=version)


def operations_source(value):
    baseline, _ = resolve(value)
    return dict(path=baseline['path'], sha256=baseline['sha256'],
                relationship='separate_reviewed_operational_example')


def _transport_input(value, minutes):
    """Bind one explicit browser assumption to immutable, validated B inputs."""
    if type(minutes) is not int or not 0 <= minutes <= MAX_TRANSPORT_MINUTES:
        raise ValueError('운송 시간은 0분부터 168시간까지 정수 분으로 입력하세요.')
    baseline, _ = resolve(value)
    data = terminal.validate_input(baseline)
    _, _, _, before, round_id, _ = terminal._nodes(baseline, data)
    after = deepcopy(before)
    assumption = dict(kind='user_transport_assumption', baseline_sha256=baseline['sha256'],
                      transport_minutes=minutes, transport_seconds=minutes * 60)
    checksum = json_sha256(assumption)
    source_id, evidence_id = 'source:transport:' + checksum, 'evidence:transport:' + checksum
    # The unchanged common validator requires actual, hash-checked source bytes.
    # Preserve this assumption separately; never rewrite the reviewed examples.
    folder = terminal.checked_path(ROOT / 'data/receipts/order_fulfillment/transport_assumptions')
    folder.mkdir(parents=True, exist_ok=True)
    path = terminal.checked_path(folder / (checksum + '.json'))
    content = canonical(assumption).encode()
    with NamedTemporaryFile(dir=folder, delete=False) as stream:
        temporary = stream.name
        stream.write(content)
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            pass
        if terminal.checked_path(path).read_bytes() != content:
            raise ValueError('The recorded transport assumption changed')
    finally:
        os.unlink(temporary)
    after.extend([
        dict(id=source_id, type='SourceDocument', origin='synthetic', evidence=[],
             url='urn:ontologics:transport-assumption:' + checksum,
             path=str(path.relative_to(ROOT)), sha256=checksum,
             version=checksum, license='Synthetic browser assumption; not a transport event.',
             collected_at=data['queried_at']),
        dict(id=evidence_id, type='Evidence', origin='synthetic', evidence=[],
             source_id=source_id, locator='transport_seconds',
             description='User-entered comparison assumption: ' + str(minutes) + ' minutes')])
    condition = next(node for node in after if node['type'] == 'DeliveryCondition')
    condition.update(transport_seconds=minutes * 60, evidence=[evidence_id])
    return baseline, data, before, after, round_id, assumption, checksum


def transport_comparison(value, minutes, *, version=1):
    """Compare one explicit browser assumption without replacing the baseline."""
    baseline, data, before, after, round_id, assumption, checksum = _transport_input(value, minutes)
    module = terminal.workflow.ROLES[terminal.CASES.index('cq06_transport2h')][0]
    task = module.make_comparison_task(before, after, round_id, data['as_of'], data['as_of'],
        before_revision='terminal-' + baseline['sha256'], after_revision='transport-' + checksum,
        queried_at=data['queried_at'], data_version=version)
    return task, assumption


def transport_response(value, minutes, *, version=1):
    """Use the same changed input and existing response Task as the comparison."""
    _, data, _, after, round_id, assumption, checksum = _transport_input(value, minutes)
    role = terminal.workflow.ROLES[terminal.CASES.index('cq05_mixed')]
    task = role[0].make_response_task(after, round_id, data['as_of'],
        queried_at=data['queried_at'], input_revision='transport-' + checksum, data_version=version)
    return role, task, assumption
