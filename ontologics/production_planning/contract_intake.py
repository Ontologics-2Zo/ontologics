"""Host-only contract extraction and evidence-linked demand proposals.

No file-reading tool is registered. Paths, source bytes, catalog bundles and
sessions are supplied by trusted host code. Literal evidence checks do not
interpret language, authenticate people, register facts or calculate demand.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re

from ..model import instant

_QUANTITIES = {'ordered_quantity', 'fulfilled_quantity', 'allocated_quantity', 'planned_input_quantity'}
_FIELDS = ('contract_item_id', 'company', 'customer', 'manufacturer', 'product_model',
           'revision', 'variant', 'configuration_id', 'ordered_quantity', 'unit',
           'fulfilled_quantity', 'allocated_quantity', 'planned_input_quantity',
           'delivery_due', 'constraints')
_OPTIONAL = {'company', 'customer', 'manufacturer', 'configuration_id', 'constraints'}


def _hash(value):
    # Invalid Python inputs (including NaN/inf claims) still need a diagnostic
    # binding. Quantity validation rejects them; this hash grants no authority.
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=True).encode()).hexdigest()


def _segments(pages, pdf):
    segments, texts, offset = [], [], 0
    for page_no, text in enumerate(pages, 1):
        if texts:
            texts.append('\f'); offset += 1
        texts.append(text)
        paragraph, in_paragraph = 0, False
        for line_no, raw in enumerate(text.splitlines(keepends=True), 1):
            line = raw.rstrip('\r\n')
            if line.strip():
                if not in_paragraph: paragraph += 1
                in_paragraph = True
            else:
                in_paragraph = False
            sid = f'pdf:page:{page_no}:line:{line_no}' if pdf else f'text:line:{line_no}'
            segments.append(dict(id=sid, text=line, start=offset, end=offset+len(line),
                                 line=line_no, paragraph=paragraph if in_paragraph else None,
                                 page=page_no if pdf else None))
            offset += len(raw)
    return ''.join(texts), segments


def _extract_bytes(raw, kind):
    if kind == 'text':
        try: pages = [raw.decode('utf-8')]
        except UnicodeDecodeError: return None, 'UNSUPPORTED_ENCODING'
    elif kind == 'pdf':
        try:
            from pypdf import PdfReader
        except ImportError:
            return None, 'PDF_EXTRACTOR_UNAVAILABLE'
        try:
            reader = PdfReader(BytesIO(raw), strict=True)
            if reader.is_encrypted: return None, 'ENCRYPTED_PDF_UNSUPPORTED'
            pages = [page.extract_text() or '' for page in reader.pages]
        except Exception:
            return None, 'PDF_EXTRACTION_FAILED'
        # Empty pages may contain scanned content. Do not silently drop them.
        if not pages or any(not p.strip() for p in pages):
            return None, 'NO_TEXT_OR_OCR_REQUIRED'
    else:
        return None, 'UNSUPPORTED_FORMAT'
    if not any(p.strip() for p in pages): return None, 'EMPTY_TEXT'
    text, segments = _segments(pages, kind == 'pdf')
    return dict(text=text, segments=segments, page_count=len(pages) if kind == 'pdf' else None), None


def extract_contract_text(path: Path) -> dict:
    """Read only a host-allowlisted UTF-8 text or text PDF; preserve raw bytes.

    Offsets are Unicode character indices, end-exclusive. Evidence offsets are
    relative to the named line segment. PDF offsets refer to extracted text,
    never byte offsets in the PDF. No natural-language field extraction occurs.
    File access errors propagate to the trusted host; they are not empty text.
    """
    path = Path(path)
    raw = path.read_bytes()
    kind = {'.txt': 'text', '.pdf': 'pdf'}.get(path.suffix.lower(), 'unsupported')
    data, error = _extract_bytes(raw, kind)
    result = dict(supported=error is None, status='unsupported' if error else 'extracted',
                  source=dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest(),
                              byte_length=len(raw), format=kind), source_bytes=raw,
                  text='', segments=[], page_count=None, errors=[])
    if error: result['errors'].append(dict(code=error, message='No supported text extraction; no OCR performed'))
    else: result.update(data)
    return result


def _configuration_valid(detail):
    """Check the public response's ID joins, without replacing graph validation."""
    if not isinstance(detail, dict): return False
    c, r, p = (detail.get(k) for k in ('configuration', 'revision', 'product'))
    if not all(isinstance(n, dict) and isinstance(n.get('id'), str) for n in (c, r, p)): return False
    if (c.get('type') != 'Configuration' or r.get('type') != 'ProductRevision'
            or p.get('type') != 'ProductModel' or c.get('revision_id') != r['id']
            or r.get('model_id') != p['id']): return False
    if not all(isinstance(n.get(k), str) and n[k].strip() for n, k in
               ((c, 'variant'), (r, 'revision'), (p, 'name'), (p, 'manufacturer'))): return False
    items, components = detail.get('items'), detail.get('components')
    if not isinstance(items, list) or not isinstance(components, list): return False
    if any(not isinstance(n, dict) or n.get('type') != 'ComponentModel' or not isinstance(n.get('id'), str)
           for n in components): return False
    ids = {n['id'] for n in components}
    if len(ids) != len(components): return False
    return all(isinstance(n, dict) and isinstance(n.get('id'), str) and n.get('type') == 'BOMItem' and n.get('configuration_id') == c['id']
               and n.get('component_id') in ids for n in items)


def prepare_contract_demand(extracted: dict, interpreted: dict, configurations: dict, *, previous: dict | None = None, source_only: bool = False) -> dict:
    """Validate literal interpretation claims and return conditional candidates.

    interpreted = {source_sha256, data_version, items: [{fields: {name:
      [{value, evidence: [{segment_id, start, end, quote}]}]},
      proposed_configuration_id?: str}], confirmation_ref?: any, ...}.
    source_only validates literal source claims without planning date/unit conversion
    or configuration selection; its items contain fields only, never planning candidates.
    Every field carries one or more claims; no missing value becomes zero.
    configurations is a host bundle {data_version, candidates: public details}.
    A list alone cannot establish the snapshot version. previous is display
    history only and cannot authorize confirmation. All inputs are copied.
    """
    errors, unresolved, questions, items = [], [], [], []

    def issue(path, code, message, *, error=False):
        row = dict(path=path, code=code, message=message)
        (errors if error else unresolved).append(row)
        if not error: questions.append(dict(path=path, code=code, question=message))

    source = extracted.get('source', {}) if isinstance(extracted, dict) else {}
    raw = extracted.get('source_bytes') if isinstance(extracted, dict) else None
    if not isinstance(source, dict): source = {}
    extracted_ok = isinstance(raw, bytes) and extracted.get('supported') is True
    if extracted_ok:
        if hashlib.sha256(raw).hexdigest() != source.get('sha256') or len(raw) != source.get('byte_length'):
            issue('extracted.source', 'SOURCE_HASH_MISMATCH', 'Original bytes and extraction hash must agree', error=True)
            extracted_ok = False
        else:
            rebuilt, error = _extract_bytes(raw, source.get('format'))
            if error or any(extracted.get(k) != v for k, v in (rebuilt or {}).items()):
                issue('extracted', 'EXTRACTION_MISMATCH', 'Text and locations must match extraction of original bytes', error=True)
                extracted_ok = False
    else:
        issue('extracted', 'UNSUPPORTED_EXTRACTION', 'A supported extraction with original bytes is required', error=True)
    if not isinstance(interpreted, dict):
        issue('interpreted', 'INVALID_INTERPRETATION', 'Expected an interpretation object', error=True)
        interpreted = {}
    version = interpreted.get('data_version')
    if type(version) is not int or version < 0:
        issue('data_version', 'INVALID_VERSION', 'Host input needs an exact nonnegative data version', error=True)
    if interpreted.get('source_sha256') != source.get('sha256'):
        issue('source_sha256', 'SOURCE_HASH_MISMATCH', 'Interpretation must name these original bytes', error=True)
        extracted_ok = False
    segments = {s['id']: s for s in extracted['segments']} if extracted_ok else {}
    details = []
    if not isinstance(configurations, dict) or not isinstance(configurations.get('candidates'), list):
        issue('configurations', 'INVALID_CONFIGURATIONS', 'Host catalog bundle requires candidates and data_version', error=True)
    elif type(configurations.get('data_version')) is not int or configurations['data_version'] != version:
        issue('configurations.data_version', 'REFERENCE_VERSION_MISMATCH', 'Catalog and input data versions must agree', error=True)
    else:
        seen = set()
        for detail in configurations['candidates']:
            if not _configuration_valid(detail) or detail['configuration']['id'] in seen:
                issue('configurations', 'CONFIGURATION_RELATION_MISMATCH', 'Configuration/revision/model/BOM relations must agree', error=True)
                continue
            seen.add(detail['configuration']['id']); details.append(deepcopy(detail))
        if len(details) != len(configurations['candidates']): details = []

    raw_items = interpreted.get('items')
    if not isinstance(raw_items, list) or not raw_items:
        issue('items', 'MISSING_ITEMS', 'Provide contract item interpretation candidates')
        raw_items = []
    for index, raw_item in enumerate(raw_items):
        path = f'items[{index}]'
        if not isinstance(raw_item, dict) or not isinstance(raw_item.get('fields'), dict):
            issue(path, 'INVALID_ITEM', 'Item fields must be an object', error=True)
            raw_item = {'fields': {}}
        fields = raw_item['fields']
        for key in fields.keys() - set(_FIELDS):
            issue(path+'.'+str(key), 'UNKNOWN_FIELD', 'Field is outside the interpretation contract', error=True)
        evaluated = {}
        for name in _FIELDS:
            fp = path+'.'+name
            claims = fields.get(name, [])
            state, supported = 'missing', []
            if not isinstance(claims, list):
                issue(fp, 'INVALID_CLAIMS', 'Field requires a list of value/evidence claims', error=True)
                claims = []; state = 'invalid'
            if not claims and name not in _OPTIONAL:
                issue(fp, 'MISSING_VALUE', 'Which value belongs to this contract item? No default has been applied')
            for ci, c in enumerate(claims):
                cp = fp+f'.claims[{ci}]'
                if not isinstance(c, dict):
                    issue(cp, 'INVALID_CLAIM', 'Claim must carry value and evidence', error=True); state = 'invalid'; continue
                value = c.get('value')
                if name in _QUANTITIES:
                    minimum = 0 if name in ('fulfilled_quantity', 'allocated_quantity') else 1
                    good = type(value) is int and value >= minimum
                    if not good:
                        issue(cp, 'INVALID_QUANTITY', 'Use an explicit integer count; bool/float/null are not counts', error=True)
                else:
                    good = isinstance(value, str) and bool(value.strip())
                    if not good: issue(cp, 'INVALID_TEXT', 'Use a nonempty literal text value', error=True)
                if not good: state = 'invalid'; continue
                if not source_only and name == 'unit' and value != 'piece':
                    issue(cp, 'INVALID_UNIT', 'Only literal piece is supported; translate units in a separate reviewed step', error=True)
                    state = 'invalid'; continue
                if not source_only and name == 'delivery_due':
                    try:
                        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value): date.fromisoformat(value)
                        else: instant(value)
                    except (TypeError, ValueError, OverflowError):
                        issue(cp, 'INVALID_TIME', 'Use a calendar date or an offset-aware datetime without filling missing precision', error=True)
                        state = 'invalid'; continue
                evidence = c.get('evidence')
                if not isinstance(evidence, list) or not evidence:
                    issue(cp, 'EVIDENCE_MISSING', 'Where in the original text is this value stated?')
                    state = 'unsupported'; continue
                grounded = True
                for ev in evidence:
                    s = segments.get(ev.get('segment_id')) if isinstance(ev, dict) and isinstance(ev.get('segment_id'), str) else None
                    start, end = (ev.get('start'), ev.get('end')) if isinstance(ev, dict) else (None, None)
                    quote = ev.get('quote') if isinstance(ev, dict) else None
                    if (s is None or type(start) is not int or type(end) is not int or not 0 <= start < end <= len(s['text'])
                            or not isinstance(quote, str) or s['text'][start:end] != quote):
                        issue(cp, 'EVIDENCE_LOCATION_MISMATCH', 'Evidence must name an existing segment and exact character span', error=True)
                        grounded = False; continue
                    literal = str(value)
                    left = s['text'][start-1:start] if start else ''
                    right = s['text'][end:end+1]
                    # Never accept L as the prefix of L1, or 100 inside 1000.
                    token_cut = ((literal[0].isascii() and literal[0].isalnum() and left.isascii() and left.isalnum()) or
                                 (literal[-1].isascii() and literal[-1].isalnum() and right.isascii() and right.isalnum()))
                    numeric_cut = name in _QUANTITIES and (
                        left in ('+', '-') or
                        (left in ('.', ',') and start > 1 and s['text'][start-2].isdigit()) or
                        (right in ('.', ',') and end+1 < len(s['text']) and s['text'][end+1].isdigit()))
                    if quote != literal or token_cut or numeric_cut:
                        issue(cp, 'EVIDENCE_VALUE_MISMATCH', 'Quoted literal must support the complete value, not a different value or token prefix', error=True)
                        grounded = False
                if grounded: supported.append(value)
                else: state = 'invalid'
            unique = {json.dumps(v, ensure_ascii=False) for v in supported}
            if len(unique) > 1:
                state = 'conflict'; issue(fp, 'CONFLICTING_VALUES', 'Which of the conflicting source values applies?')
            elif len(unique) == 1 and len(supported) == len(claims): state = 'supported'
            evaluated[name] = dict(state=state, value=supported[0] if state == 'supported' else None, claims=deepcopy(claims))

        if source_only:
            items.append(dict(fields=evaluated))
            continue

        def value(name): return evaluated[name]['value']

        matches = []
        for detail in details:
            c, r, p = (detail[k] for k in ('configuration', 'revision', 'product'))
            expected = dict(product_model=p['name'], manufacturer=p['manufacturer'], revision=r['revision'],
                            variant=c['variant'], configuration_id=c['id'])
            if all(value(k) is None or value(k) == v for k, v in expected.items()):
                matches.append(dict(configuration_id=c['id'], product_model=p['name'], manufacturer=p['manufacturer'],
                                    revision=r['revision'], variant=c['variant'], evidence=deepcopy(c.get('evidence', []))))
        selected = None
        proposed = raw_item.get('proposed_configuration_id')
        if proposed is not None:
            if not isinstance(proposed, str) or proposed not in {c['configuration_id'] for c in matches}:
                issue(path+'.proposed_configuration_id', 'CONFIGURATION_SELECTION_MISMATCH', 'Proposed selection must exist and agree with supported source fields', error=True)
            else: selected = proposed
        elif len(matches) == 1 and (value('configuration_id') is not None or
                                   all(value(k) is not None for k in ('product_model', 'revision', 'variant'))):
            selected = matches[0]['configuration_id']
        if not matches: issue(path+'.configuration_id', 'CONFIGURATION_NOT_FOUND', 'No existing configuration matches; check model/revision/variant and version')
        elif selected is None: issue(path+'.configuration_id', 'CONFIGURATION_UNCONFIRMED', 'Which existing configuration is intended? Model name alone does not specify revision or variant')
        if any(evaluated[k]['state'] in ('invalid', 'unsupported', 'conflict') for k in
               ('product_model', 'manufacturer', 'revision', 'variant', 'configuration_id')):
            selected = None
        constraint_state = 'requires_review' if fields.get('constraints') else 'unspecified'
        issue(path+'.constraints', 'CONSTRAINTS_UNCONFIRMED', 'Confirm revision/substitution restrictions; missing text grants no permission')
        if value('delivery_due') and len(value('delivery_due')) == 10:
            issue(path+'.delivery_due', 'DUE_TIME_UNCONFIRMED', 'Date is preserved; exact arrival time, offset and destination remain unconfirmed')
        contract = dict(id=value('contract_item_id'), configuration_id=selected, ordered_quantity=value('ordered_quantity'),
                        fulfilled_quantity=value('fulfilled_quantity'), allocated_quantity=value('allocated_quantity'),
                        unit=value('unit'), delivery_due=value('delivery_due'),
                        delivery_due_precision=('date' if len(value('delivery_due')) == 10 else 'datetime') if value('delivery_due') else None,
                        constraints_state=constraint_state)
        items.append(dict(fields=evaluated, configuration_candidates=matches, contract_item_candidate=contract,
                          common_item_candidate=dict(configuration_id=selected, quantity=value('ordered_quantity'), unit=value('unit')),
                          planned_input_quantity=value('planned_input_quantity'),
                          proposed_configuration_id=deepcopy(proposed), permissions=dict(revision_change=None, substitution=None)))
    issue('confirmation', 'HUMAN_CONFIRMATION_UNAVAILABLE', 'A trusted persisted demand-confirmation lookup is required; graph review is a different action')
    binding = dict(source_sha256=source.get('sha256'), data_version=version, items=deepcopy(items))
    binding_hash = _hash(binding)
    old_binding = previous.get('binding') if isinstance(previous, dict) else None
    changed = old_binding is not None and old_binding != binding
    return dict(valid=not errors, status='invalid' if errors else 'needs_confirmation',
                source=deepcopy(source), original=dict(text=extracted.get('text', '') if isinstance(extracted, dict) else '',
                    segments=deepcopy(extracted.get('segments', [])) if isinstance(extracted, dict) else []),
                interpretation=deepcopy(interpreted), items=items,
                errors=errors, unresolved=unresolved, questions=questions,
                binding=binding, binding_sha256=binding_hash,
                revision=dict(binding_changed=changed, before=deepcopy(old_binding), after=deepcopy(binding)),
                confirmation=dict(verified=False, state='unavailable', invalidated=changed,
                    supplied_claims={k:deepcopy(interpreted[k]) for k in ('confirmed','confirmation_ref','confirmation') if k in interpreted}))


def read_contract_demand(session, context: dict, extracted: dict, interpreted: dict, *, previous: dict | None = None) -> dict:
    """Compose extraction claims with context.data_version's public catalog.

    context is host-owned. Query failures retain envelopes and report=None;
    they never masquerade as ordinary needs_confirmation candidates.
    """
    from .configuration_lookup import lookup_contract_configurations
    version = context.get('data_version') if isinstance(context, dict) else None
    if not isinstance(interpreted, dict) or type(version) is not int or type(interpreted.get('data_version')) is not int or interpreted['data_version'] != version:
        return dict(query=dict(lookup_response=None, configuration_responses=[], configurations=None,
            errors=[dict(path='data_version', code='REFERENCE_VERSION_MISMATCH', message='Interpretation and host context must name the same exact version')]), report=None)
    query = lookup_contract_configurations(session, data_version=version)
    report = None if query['configurations'] is None else prepare_contract_demand(
        extracted, interpreted, query['configurations'], previous=previous)
    return dict(query=query, report=report)
