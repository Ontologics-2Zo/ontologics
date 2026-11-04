"""Register the authored Hanbit scenario through existing common host workflows.

This is a development-only host helper, never an Agent tool or production
approval route. Callers provide a configured shared host and two distinct existing
development people. No database settings, identities or graph facts are seeded
here; an existing real product catalog is a prerequisite.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..identity import DevelopmentContext, DevelopmentIdentityProvider
from ..model import ROOT, digest, instant
from ..host import CommonHost
from ..order_fulfillment.material_request_document import (
    build_nodes as request_nodes, decode_record, validate_request,
)
from ..order_fulfillment.material_request_registration import prepare_request, read_request
from ..service import DomainError
from .rfq_handoff_document import write_once

BUNDLE = Path(__file__).with_name('data')/'hanbit_facilities_20261104'
STORAGE_ROOT = ROOT/'data/receipts/materials/business_cases'


def _ok(result):
    if not result['ok']:
        error = result['errors'][0]
        raise DomainError(error['code'], error.get('message', '공통 등록 작업이 실패했습니다.'))
    return result


def _conflict(message):
    raise DomainError('BUSINESS_CASE_CONFLICT', message)


def _development(host, author_subject, reviewer_subject):
    if (not isinstance(host, CommonHost)
            or not isinstance(host._identities, DevelopmentIdentityProvider)
            or host._identities.mode != 'development'
            or author_subject == reviewer_subject):
        raise DomainError('DEVELOPMENT_REVIEW_REQUIRED', '서로 다른 두 개발 검토 신원과 구성된 공통 Host가 필요합니다.')
    author = host._identities.resolve(DevelopmentContext(author_subject))
    reviewer = host._identities.resolve(DevelopmentContext(reviewer_subject))
    if not {'read', 'draft'} <= author.permissions or not {'read', 'review'} <= reviewer.permissions:
        raise DomainError('FORBIDDEN', '작성자의 읽기·초안 권한과 검토자의 읽기·검토 권한이 필요합니다.')


def _case_data():
    """Verify every bundled asset before producing any graph operations."""
    manifest = json.loads((BUNDLE/'manifest.json').read_text(encoding='utf-8'))
    if (manifest['format'] != 'materials-business-dataset-1'
            or manifest['origin'] != 'synthetic'
            or manifest['internal_metadata']['original_contract']['source_kind'] != 'existing_authored_contract'):
        _conflict('업무 자료의 내부 출처와 구분을 확인하세요.')
    files = {}
    for entry in manifest['files']:
        name = entry['path']
        if (not isinstance(name, str) or Path(name).name != name or name in files
                or entry['origin'] != 'synthetic' or (BUNDLE/name).is_symlink()):
            _conflict('업무 자료 파일 목록이 올바르지 않습니다.')
        raw = (BUNDLE/name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry['sha256']:
            _conflict('업무 자료 원문 해시가 다릅니다: '+name)
        files[name] = dict(entry, raw=raw)
    request = json.loads(files['additional_request.json']['raw'])
    metadata = manifest['internal_metadata']['graph_registration']
    instant(metadata['collected_at'])
    if (metadata['baseline_quantity'] != 180 or request['quantity'] != 60
            or request['need_date'] != '2026-11-27' or request['need_basis'] != 'production_complete'
            or request['origin'] != 'synthetic' or request['relation'] != 'additional'
            or request['configuration_id'] != manifest['configuration_id']
            or request['baseline_evidence_id'] != manifest['baseline_evidence_id']
            or request['contract_item_id'] != manifest['contract_item_id']
            or request['request_id'] != manifest['request_id']
            or files['contract.txt']['sha256'] != manifest['internal_metadata']['original_contract']['sha256']):
        _conflict('기준 계약 180대와 추가 요청 60대의 구성·수량·일정을 확인하세요.')
    root = STORAGE_ROOT.resolve()
    allowed = (ROOT/'data/receipts/materials').resolve()
    if not root.is_relative_to(allowed) or any(p.is_symlink() for p in [STORAGE_ROOT, *STORAGE_ROOT.parents] if p.is_relative_to(ROOT/'data')):
        _conflict('원문 보존 위치는 materials의 내부 접수 경로여야 합니다.')
    nodes, originals = [], []
    for name, kind, locator in [('contract.txt', 'supply_contract', '계약서 본문 및 별첨 1 전체'),
                                ('plan.txt', 'baseline_plan', '기준계획서 전체; 공급계약서 제2·3·4·7조 및 별첨 1')]:
        entry = files[name]
        path = root/(entry['sha256']+'.txt')
        originals.append((path, entry['raw']))
        nodes.extend([
            dict(id=entry['source_id'], type='SourceDocument', origin='synthetic', evidence=[],
                 kind=kind, url='urn:ontologics:'+entry['source_id'], path=str(path.relative_to(ROOT)),
                 sha256=entry['sha256'], version='1', license=metadata['license'],
                 collected_at=metadata['collected_at']),
            dict(id=entry['evidence_id'], type='Evidence', origin='synthetic', evidence=[],
                 source_id=entry['source_id'], locator=locator, description=entry['raw'].decode('utf-8')),
        ])
    evidence = files['contract.txt']['evidence_id']
    nodes.extend([
        dict(id=manifest['contract_id'], type='Contract', origin='synthetic', evidence=[evidence],
             company=manifest['supplier'], customer_name=manifest['customer']),
        dict(id=manifest['contract_item_id'], type='ContractItem', origin='synthetic', evidence=[evidence],
             contract_id=manifest['contract_id'], configuration_id=manifest['configuration_id'],
             quantity=metadata['baseline_quantity'], unit='piece'),
    ])
    # Keep the separately authored request bytes as well as B's canonical record.
    original = files['additional_request.txt']
    originals.append((root/(original['sha256']+'.txt'), original['raw']))
    return manifest, request, nodes, originals


def _existing_request(session, snapshot, payload, author_subject):
    matches = []
    for node in snapshot['data']:
        if node.get('type') != 'Evidence':
            continue
        try:
            record = decode_record(node.get('description', ''))
        except (ValueError, TypeError, KeyError):
            continue
        if record['request']['request_id'] == payload['request_id']:
            matches.append(node)
    if not matches:
        return None
    if len(matches) != 1:
        _conflict('같은 요청 번호에 다른 개정 또는 중복 기록이 존재합니다.')
    found = read_request(session, matches[0]['id'], version=snapshot['data_version'])
    record = found['record']
    info = session._service.db.info
    store_id = digest([info.host, info.port, info.dbname, session._service.schema])
    if (record['request'] != payload or record['provenance']['actor'] != 'user:'+author_subject
            or record['provenance']['store_id'] != store_id):
        _conflict('기존 요청의 내용·등록 신원·저장소가 등록 대상과 다릅니다.')
    try:
        expected = request_nodes(ROOT/found['source']['path'], record)
    except (OSError, ValueError, TypeError, KeyError):
        _conflict('기존 요청의 보존 원문이 일치하지 않습니다.')
    if expected != [found['source'], found['evidence']]:
        _conflict('기존 요청의 식별자 또는 원문 근거가 일치하지 않습니다.')
    pinned = _ok(session.call('lookup', {'version': record['provenance']['reference_data_version']}))
    prior, current = ({n['id']: n for n in rows} for rows in (pinned['data'], snapshot['data']))
    for key in ('configuration_id', 'contract_item_id', 'baseline_evidence_id'):
        if prior.get(payload[key]) != current.get(payload[key]):
            _conflict('기존 요청의 등록 당시 기준과 현재 기준이 다릅니다: '+key)
    return found


def _validate(session, draft, base_version):
    # prepare_draft captures its base atomically. A race after our initial read
    # must not turn an additive import into an overwrite of a newly added node.
    if draft['base_version'] != base_version:
        raise DomainError('STALE_BASE', '검토를 시작한 뒤 기준 데이터 버전이 변경됐습니다.')
    if any(change['before'] is not None for change in draft['changes']):
        _conflict('추가 등록 중 기존 기록과 충돌했습니다. 기존 내용을 덮어쓰지 않습니다.')
    result = _ok(session.call('validate_draft', {'draft_id': draft['draft_id'], 'version': draft['draft_version']}))
    if not result['data']['validation']['valid']:
        raise DomainError('VALIDATION_FAILED', '공통 초안 검증에 실패했습니다.', result['data']['validation'])


def _review_apply(host, projection, reviewer_subject, draft, key):
    with host.request(DevelopmentContext(reviewer_subject)) as session:
        if session.context['identity_mode'] != 'development':
            raise DomainError('DEVELOPMENT_REVIEW_REQUIRED', '개발 검토 신원을 확인하세요.')
        _ok(session.review(draft['draft_id'], draft['draft_version'], decision='approve',
                           note='사용자가 요청한 내부 synthetic 업무 자료의 원문·수량·추가 수요 관계를 별도 개발 신원으로 검토. 실제 업무 승인이나 생산 지시가 아님.',
                           request_key=key+':review'))
    return _ok(projection.apply_approved(draft['draft_id'], draft['draft_version'], request_key=key+':apply'))


def register_business_case(host, *, author_subject, reviewer_subject, projection_host=None, expected_version=None):
    """Add the 180-board baseline and separate 60-board review request.

    ``host`` is an already-configured CommonHost for PostgreSQL reads/writes;
    the B CommonHost subclass is also supported.
    An optional ``projection_host`` uses the same database/schema and publishes
    each approved version to its configured graph backend. No live work is done
    until this function is explicitly called. Exact replays add no graph version;
    existing content conflicts are refused before new drafts are prepared.
    """
    projection = projection_host or host
    for current in (host, projection):
        _development(current, author_subject, reviewer_subject)
    if host._database != projection._database:
        _conflict('등록 Host와 그래프 Host는 같은 데이터베이스·스키마를 사용해야 합니다.')
    if expected_version is not None and (type(expected_version) is not int or expected_version < 0):
        raise ValueError('기준 데이터 버전은 0 이상의 정수여야 합니다.')
    manifest, payload, nodes, originals = _case_data()
    author_context = DevelopmentContext(author_subject)
    key = 'materials-business:'+manifest['dataset_id']
    with host.request(author_context) as session:
        before = _ok(session.call('lookup', {}))
        index = {node['id']: node for node in before['data']}
        missing = []
        for node in nodes:
            existing = index.get(node['id'])
            if existing is not None and existing != node:
                _conflict('기존 업무 기록을 덮어쓸 수 없습니다: '+node['id'])
            if existing is None:
                missing.append(node)
        found = _existing_request(session, before, payload, author_subject)
        if found and missing:
            _conflict('기존 요청의 계약·기준 계획 기록이 누락되었습니다.')
        if not found:
            if expected_version is not None and before['data_version'] != expected_version:
                raise DomainError('STALE_BASE', '요청한 기준 데이터 버전과 현재 버전이 다릅니다.')
            validate_request(payload, before['data']+missing)
    for path, raw in originals:
        if write_once(path, raw) != raw:
            _conflict('보존 원문 파일이 기존 내용과 다릅니다: '+path.name)
    baseline_version = before['data_version']
    drafts = []
    if missing:
        baseline_key = key+':baseline:'+digest(missing)[:16]
        with host.request(author_context) as session:
            response = _ok(session.call('prepare_draft', {
                'operations': [{'op': 'put', 'node': node} for node in missing], 'request_key': baseline_key}))
            draft = response['data']
            _validate(session, draft, before['data_version'])
        applied = _review_apply(host, projection, reviewer_subject, draft, baseline_key)
        baseline_version = applied['data_version']
        drafts.append(draft['draft_id'])
    if not found:
        with host.request(author_context) as session:
            created = prepare_request(session, payload, key+':request')
            draft = created['draft']['data']
            _validate(session, draft, baseline_version)
        applied = _review_apply(host, projection, reviewer_subject, draft, key+':request')
        version = applied['data_version']
        evidence_id = created['evidence_id']
        drafts.append(draft['draft_id'])
    else:
        version = before['data_version']
        evidence_id = found['evidence']['id']
        baseline_version = found['record']['provenance']['reference_data_version']
    with host.request(author_context) as session:
        after = _ok(session.call('lookup', {'version': version}))
        result = read_request(session, evidence_id, version=version)
        original_after = _ok(session.call('lookup', {'version': before['data_version']}))
    if projection is not host:
        with projection.request(author_context) as session:
            projected = _ok(session.call('lookup', {'version': version}))
            projected_request = read_request(session, evidence_id, version=version)
        if projected['data'] != after['data'] or projected_request != result:
            _conflict('PostgreSQL과 그래프에서 조회한 동일 버전 기록이 다릅니다.')
    prior = {node['id']: node for node in before['data']}
    current = {node['id']: node for node in after['data']}
    allowed_ids = {node['id'] for node in nodes} | {result['source']['id'], evidence_id}
    if (any(current.get(nid) != node for nid, node in prior.items())
            or set(current)-set(prior) != allowed_ids-set(prior)
            or original_after['data'] != before['data'] or result['record']['request'] != payload):
        _conflict('기존 기록 보존 또는 최종 등록 결과가 일치하지 않습니다.')
    return dict(ok=True, request_id=payload['request_id'], quantity=payload['quantity'],
                need_date=payload['need_date'], need_basis=payload['need_basis'],
                contract_id=manifest['contract_id'], contract_item_id=manifest['contract_item_id'],
                baseline_evidence_id=payload['baseline_evidence_id'], configuration_id=payload['configuration_id'],
                source_id=result['source']['id'], evidence_id=evidence_id, source_path=result['source']['path'],
                before_version=before['data_version'], baseline_version=baseline_version, after_version=version,
                before_nodes=len(prior), after_nodes=len(current), added_node_ids=sorted(set(current)-set(prior)),
                draft_ids=drafts, already_registered=found is not None,
                existing_nodes_unchanged=True, projection_readback_equal=True,
                development_review=True, origin='synthetic', production_authorized=False,
                stock_reserved=False, a_received=False)
