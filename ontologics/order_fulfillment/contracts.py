"""Read one fixed-format synthetic contract into unapproved graph candidates.

This host-side importer never approves, persists or reads private documents.
It supports a single contract item and delivery round, not arbitrary contracts.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy

from ..model import ROOT, instant

DEMO_SOURCE_PATH = 'data/order_fulfillment/sources/b_demo_contract_01.md'
DEMO_SOURCE_SHA256 = 'bbe0b4a2ad05bcb14e0eb477f90d9ae5f25c67a706a906e606b8e8023eda67ae'
DEMO_EVIDENCE_IDS = {
    'contract': 'evidence:demo:B-01:contract',
    'item': 'evidence:demo:B-01:item',
    'round': 'evidence:demo:B-01:round',
}

_REQUIRED = (
    '계약 번호', '우리 회사 식별', '고객사', '제품 모델', '설계 개정', '구성',
    '계약 항목 번호', '계약 수량', '수량 단위', '납품 회차', '회차 약속 수량',
    '납품 기한', '기한 기준', '납품 장소',
)
_OPTIONAL = {'계약 유효기간'}


def delivery_promise(nodes, delivery_round_id):
    """Project a promise from validated, unregistered B inputs, without storage.

    This assesses neither fulfillment nor an approved change to the promise.
    Product/configuration identity and source integrity use common validation.
    """
    from .schema_validation import validate_input_graph

    if not isinstance(delivery_round_id, str) or not delivery_round_id.strip():
        raise ValueError('An explicit nonempty delivery round ID is required')
    report = validate_input_graph(nodes)
    if not report['valid']:
        raise ValueError(report)
    index = {node['id']: node for node in nodes}
    delivery = index.get(delivery_round_id)
    if delivery is None or delivery['type'] != 'DeliveryRound':
        raise KeyError('Unknown delivery round')
    item = index[delivery['contract_item_id']]
    config = index[item['configuration_id']]
    revision = index[config['revision_id']]
    return deepcopy(dict(contract=index[item['contract_id']], contract_item=item,
        delivery_round=delivery, configuration=config, revision=revision,
        product=index[revision['model_id']], fulfillment_assessed=False,
        input_state='unregistered'))


def extract_contract(text, configurations, *, evidence_ids):
    """Return ready/needs_confirmation/invalid with candidates and source issues.

    ``configurations`` is a confirmed graph snapshot (including product/revision
    nodes); matching requires exact model, revision and variant. Evidence IDs
    refer to host-prepared source locators and are checked by graph validation
    before any registration. Source text is data, never execution instructions.
    """
    issues, states, rows = [], [], {}

    def issue(field, code, message, state='needs_confirmation', lines=()):
        locator = '계약 표 / ' + field
        if lines:
            locator += '; lines ' + ','.join(map(str, lines))
        issues.append({'field': field, 'code': code, 'message': message, 'locator': locator})
        states.append(state)

    def result(nodes=None):
        state = 'invalid' if 'invalid' in states else 'needs_confirmation' if states else 'ready'
        return {'status': state, 'nodes': nodes if state == 'ready' and nodes else [], 'issues': issues}

    if not isinstance(text, str) or len(text.encode('utf-8')) > 100_000:
        issue('문서', 'INVALID_SOURCE', 'Provide UTF-8 Markdown text of at most 100 KB', 'invalid')
        return result()
    if not isinstance(configurations, list) or not all(isinstance(n, dict) for n in configurations):
        issue('구성', 'INVALID_CONFIGURATION_INPUT', 'Provide confirmed graph nodes', 'invalid')
        return result()
    if (not isinstance(evidence_ids, dict) or set(evidence_ids) != {'contract', 'item', 'round'}
            or not all(isinstance(v, str) and v.strip() for v in evidence_ids.values())
            or len(set(evidence_ids.values())) != 3):
        issue('근거', 'INVALID_EVIDENCE_IDS', 'Provide three distinct nonempty source Evidence IDs', 'invalid')
        return result()

    section, sections, header, separator = False, 0, False, False
    for line_number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if line.startswith('#'):
            section = line == '## 계약 표'
            if section:
                sections += 1
            continue
        if not section or not line:
            continue
        if not line.startswith('|') or not line.endswith('|'):
            issue('문서', 'UNSUPPORTED_FORMAT', 'Expected a two-column contract table', 'invalid', [line_number])
            continue
        cells = [cell.strip() for cell in line.split('|')[1:-1]]
        if cells == ['항목', '원문 값'] and not header:
            header = True
            continue
        if len(cells) == 2 and all(re.fullmatch(r':?-{3,}:?', cell) for cell in cells) and header and not separator:
            separator = True
            continue
        if len(cells) != 2 or not header or not separator:
            issue('문서', 'UNSUPPORTED_FORMAT', 'Expected one fixed table header and two columns', 'invalid', [line_number])
            continue
        field, value = cells
        if field not in _REQUIRED and field not in _OPTIONAL:
            issue(field, 'UNSUPPORTED_FIELD', 'Unknown contract field requires explicit mapping', lines=[line_number])
            continue
        rows.setdefault(field, []).append((value, line_number))

    if sections != 1 or not header or not separator:
        issue('문서', 'UNSUPPORTED_FORMAT', 'Expected exactly one 계약 표 section and table', 'invalid')
    values = {}
    for field in (*_REQUIRED, *_OPTIONAL):
        entries = rows.get(field, [])
        if len(entries) > 1:
            code = 'CONFLICTING_FIELD' if len({v for v, _ in entries}) > 1 else 'DUPLICATE_FIELD'
            issue(field, code, 'Review all source rows; no row is silently preferred', lines=[n for _, n in entries])
        if not entries or not entries[0][0]:
            if field in _REQUIRED:
                issue(field, 'MISSING_FIELD', 'Required source information needs supplementation',
                      lines=[n for _, n in entries])
        else:
            values[field] = entries[0][0]

    quantities = {}
    for field in ('계약 수량', '회차 약속 수량'):
        if field in values:
            raw = values[field]
            if len(raw) > 100 or not re.fullmatch(r'[0-9]+', raw) or int(raw) <= 0:
                issue(field, 'INVALID_QUANTITY', 'A positive integer piece count is required', 'invalid',
                      [n for _, n in rows[field]])
            else:
                quantities[field] = int(raw)
    if len(quantities) == 2 and quantities['회차 약속 수량'] != quantities['계약 수량']:
        excessive = quantities['회차 약속 수량'] > quantities['계약 수량']
        issue('회차 약속 수량', 'ROUND_EXCEEDS_ITEM' if excessive else 'PARTIAL_ROUND_UNSUPPORTED',
              'The first single-round example must cover the whole item', 'invalid' if excessive else 'needs_confirmation')
    if '수량 단위' in values and values['수량 단위'] != 'piece':
        issue('수량 단위', 'UNSUPPORTED_UNIT', 'Only piece counts are supported; do not infer box contents', 'invalid')
    if '기한 기준' in values and values['기한 기준'] != '고객 도착':
        issue('기한 기준', 'UNSUPPORTED_DUE_BASIS', 'Only explicit customer arrival is supported; no departure conversion')
    if '납품 기한' in values:
        try:
            instant(values['납품 기한'])
        except (ValueError, TypeError):
            issue('납품 기한', 'INCOMPLETE_TIME', 'Confirm an explicit time and UTC offset; no default time is inserted')
    for field in ('계약 항목 번호', '납품 회차'):
        if field in values and values[field] != '1':
            issue(field, 'UNSUPPORTED_SCOPE', 'The first format supports item 1 and round 1 only')
    number = values.get('계약 번호', '')
    if number and not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', number):
        issue('계약 번호', 'INVALID_CONTRACT_NUMBER', 'Use an explicit ASCII demo contract number', 'invalid')

    config = None
    if all(field in values for field in ('제품 모델', '설계 개정', '구성')):
        index = {n['id']: n for n in configurations if isinstance(n.get('id'), str)}
        matches = []
        for candidate in configurations:
            if (candidate.get('type') != 'Configuration' or candidate.get('origin') != 'real'
                    or not isinstance(candidate.get('id'), str) or not candidate['id'].strip()):
                continue
            revision_id = candidate.get('revision_id')
            revision = index.get(revision_id, {}) if isinstance(revision_id, str) else {}
            model_id = revision.get('model_id')
            model = index.get(model_id, {}) if isinstance(model_id, str) else {}
            if (revision.get('type') == 'ProductRevision' and model.get('type') == 'ProductModel'
                    and model.get('name') == values['제품 모델']
                    and revision.get('revision') == values['설계 개정']
                    and candidate.get('variant') == values['구성']):
                matches.append(candidate)
        if len(matches) != 1:
            issue('구성', 'CONFIGURATION_NOT_UNIQUE', 'Exactly one existing model/revision/variant must match')
        else:
            config = matches[0]
    if states:
        return result()

    contract_id, item_id = f'contract:demo:{number}', f'contract-item:demo:{number}:1'
    return result([
        {'id': contract_id, 'type': 'Contract', 'origin': 'synthetic', 'evidence': [evidence_ids['contract']],
         'company': values['우리 회사 식별'], 'customer_name': values['고객사']},
        {'id': item_id, 'type': 'ContractItem', 'origin': 'synthetic', 'evidence': [evidence_ids['item']],
         'contract_id': contract_id, 'configuration_id': config['id'],
         'quantity': quantities['계약 수량'], 'unit': 'piece'},
        {'id': f'delivery-round:demo:{number}:1', 'type': 'DeliveryRound', 'origin': 'synthetic',
         'evidence': [evidence_ids['round']], 'contract_item_id': item_id,
         'quantity': quantities['회차 약속 수량'], 'unit': 'piece',
         'due_at': values['납품 기한'], 'due_basis': 'arrival', 'destination': values['납품 장소']},
    ])


def load_demo_contract():
    """Build seven synthetic candidates from the immutable shared example.

    Callers must use the common validation/draft/review/apply path or explicitly
    label an empty-database bootstrap as a development seed.
    """
    source_bytes = (ROOT / DEMO_SOURCE_PATH).read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != DEMO_SOURCE_SHA256:
        raise ValueError('SOURCE_HASH_MISMATCH: new contract source bytes require a new source/evidence version')
    metadata = json.loads((ROOT / 'data/order_fulfillment/b_contract_fixture.json').read_text())
    source = metadata['source']
    if source['path'] != DEMO_SOURCE_PATH or source['sha256'] != DEMO_SOURCE_SHA256:
        raise ValueError('SOURCE_METADATA_MISMATCH')
    catalog = json.loads((ROOT / 'data/catalog.json').read_text())['nodes']
    extracted = extract_contract(source_bytes.decode('utf-8'), catalog, evidence_ids=DEMO_EVIDENCE_IDS)
    if extracted['status'] != 'ready':
        raise ValueError('INVALID_DEMO_CONTRACT: ' + json.dumps(extracted['issues'], ensure_ascii=False))
    return [source, *metadata['evidence'], *extracted['nodes']]
