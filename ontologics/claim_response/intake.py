"""Deterministic claim intake: mail, Excel and CSV claims -> one ClaimIntake draft.

The converter reads a single synthetic customer claim file and prepares candidate
graph nodes (SourceDocument, Evidence, ClaimIntake). It never approves, applies or
links customer serials to internal ProductUnit IDs; people review the draft first.
Values missing from the source stay missing and are reported as issues.
"""
from __future__ import annotations

import csv
import email
import hashlib
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone
from email import policy
from pathlib import Path, PurePosixPath

from ..model import ROOT, digest, instant

MAX_SOURCE_BYTES = 5_000_000
MAX_XML_BYTES = 10_000_000
KST = timezone(timedelta(hours=9))
SHEET_NAME = '클레임'
MAIN_NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG_REL_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'

# Fixed claim form header -> internal field. Excel and CSV share the same form.
COLUMNS = {
    '고객 클레임 번호': 'external_claim_id',
    '고객사': 'customer_name',
    '신고 일시 (KST)': 'reported_at',
    '제조사': 'manufacturer',
    '제품 모델': 'model',
    '설계 개정': 'revision',
    '변형': 'variant',
    '일련번호': 'serial',
    '불량 수량': 'quantity',
    '증상': 'reported_symptom',
    '발생 조건': 'reported_conditions',
    '납품 문서 번호': 'delivery_reference',
    '요청 사항': 'requested_action',
}
# Values that describe the whole claim and must agree on every row.
CLAIM_FIELDS = ('external_claim_id', 'customer_name', 'reported_at', 'manufacturer', 'model', 'revision',
                'variant', 'reported_symptom', 'reported_conditions', 'delivery_reference', 'requested_action')
REQUIRED_FIELDS = ('external_claim_id', 'customer_name', 'reported_at', 'reported_symptom')
OPTIONAL_INTAKE_FIELDS = ('reported_conditions', 'requested_action', 'delivery_reference')
PRODUCT_FIELDS = ('manufacturer', 'model', 'revision', 'variant')
# The customer never supplies these; later C steps find them from separate trace data.
SEPARATE_TRACE_DATA = ['production_line_id', 'stored_unit_ids', 'root_cause', 'test_results']


def _issue(issues, code, message, locator=None):
    entry = {'code': code, 'message': message}
    if locator:
        entry['locator'] = locator
    issues.append(entry)


def _clean(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _read_bounded(path):
    path = Path(path)
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError('SOURCE_TOO_LARGE: expected a small claim file')
    return path.read_bytes()


# ---------------------------------------------------------------- CSV (2-1)

def parse_claim_csv(path):
    """Read the fixed claim form from UTF-8 CSV. One row per reported board."""
    raw = _read_bounded(path)
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise ValueError('UNSUPPORTED_ENCODING: claim CSV must be UTF-8') from None
    table = list(csv.reader(io.StringIO(text, newline='')))
    if not table:
        raise ValueError('EMPTY_SOURCE: claim CSV has no header')
    columns = _header(table[0])
    rows = []
    for number, values in enumerate(table[1:], start=2):
        if not any(cell.strip() for cell in values):
            continue
        if len(values) > len(table[0]):
            raise ValueError(f'EXTRA_CELLS: row {number} has more cells than the header')
        record = {field: _clean(values[i]) if i < len(values) else None for field, i in columns.items()}
        record['_locator'] = f'row {number}'
        rows.append(record)
    if not rows:
        raise ValueError('NO_CLAIM_ROWS: claim CSV has no board rows')
    return {'format': 'csv', 'rows': rows,
            'locator': f'UTF-8 CSV header row 1, board rows 2-{rows[-1]["_locator"].split()[-1]}',
            'timezone_label': 'KST'}


def _header(cells):
    names = [_clean(cell) for cell in cells]
    if len(set(n for n in names if n)) != len([n for n in names if n]):
        raise ValueError('DUPLICATE_HEADER: each claim column must appear once')
    columns = {}
    for header, field in COLUMNS.items():
        if header not in names:
            raise ValueError('MISSING_HEADER: ' + header)
        columns[field] = names.index(header)
    return columns


# -------------------------------------------------------------- Excel (2-2)

def _xml(archive, name):
    info = archive.getinfo(name)
    if info.file_size > MAX_XML_BYTES:
        raise ValueError('SOURCE_TOO_LARGE: expanded XML')
    data = archive.read(name)
    if b'<!DOCTYPE' in data or b'<!ENTITY' in data:
        raise ValueError('UNSUPPORTED_XML: external entities are forbidden')
    return ET.fromstring(data)


def _column_index(reference):
    letters = re.match(r'[A-Z]+', reference).group(0)
    index = 0
    for letter in letters:
        index = index * 26 + ord(letter) - 64
    return index - 1


def _excel_serial(value, *, date1904):
    """Excel stores dates as days since 1899-12-30 (or 1904-01-01); the form header says KST."""
    base = datetime(1904, 1, 1) if date1904 else datetime(1899, 12, 30)
    moment = base + timedelta(seconds=round(float(value) * 86400))
    return moment.replace(tzinfo=KST).isoformat()


def parse_claim_xlsx(path):
    """Read the '클레임' sheet of the fixed claim form. Formulas are not accepted."""
    _read_bounded(path)
    m = '{' + MAIN_NS + '}'
    with zipfile.ZipFile(path) as archive:
        workbook = _xml(archive, 'xl/workbook.xml')
        date1904 = any(p.get('date1904') in ('1', 'true') for p in workbook.iter(m + 'workbookPr'))
        sheets = [s for s in workbook.iter(m + 'sheet') if s.get('name') == SHEET_NAME]
        if len(sheets) != 1:
            raise ValueError('MISSING_SHEET: expected one sheet named ' + SHEET_NAME)
        rel_id = sheets[0].get('{' + REL_NS + '}id')
        rels = _xml(archive, 'xl/_rels/workbook.xml.rels')
        target = next((r.get('Target') for r in rels.iter('{' + PKG_REL_NS + '}Relationship')
                       if r.get('Id') == rel_id), None)
        if not target:
            raise ValueError('MISSING_SHEET: workbook relationship not found')
        sheet_path = target.lstrip('/') if target.startswith('/') else str(PurePosixPath('xl') / target)
        shared = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            shared = [''.join(t.text or '' for t in si.iter(m + 't'))
                      for si in _xml(archive, 'xl/sharedStrings.xml').iter(m + 'si')]
        sheet = _xml(archive, sheet_path)
    grid = {}
    for row in sheet.iter(m + 'row'):
        number = int(row.get('r'))
        for cell in row.iter(m + 'c'):
            if cell.find(m + 'f') is not None:
                raise ValueError('UNSUPPORTED_FORMULA: claim cells must contain explicit values')
            kind, ref = cell.get('t', 'n'), cell.get('r')
            if kind == 'inlineStr':
                value = ''.join(t.text or '' for t in cell.iter(m + 't'))
            else:
                v = cell.find(m + 'v')
                value = v.text if v is not None else None
                if kind == 's' and value is not None:
                    value = shared[int(value)]
            grid.setdefault(number, {})[_column_index(ref)] = (kind, value)
    if 1 not in grid:
        raise ValueError('EMPTY_SOURCE: claim sheet has no header')
    width = max(grid[1]) + 1
    columns = _header([grid[1].get(i, (None, None))[1] for i in range(width)])
    rows = []
    for number in sorted(n for n in grid if n > 1):
        cells = grid[number]
        if not any(_clean(v) for _, v in cells.values()):
            continue
        if max(cells) >= width:
            raise ValueError(f'EXTRA_CELLS: row {number} has values outside the header')
        record = {}
        for field, i in columns.items():
            kind, value = cells.get(i, (None, None))
            if field == 'reported_at' and kind == 'n' and _clean(value):
                value = _excel_serial(value, date1904=date1904)
            elif field == 'quantity' and kind == 'n' and _clean(value):
                number_value = float(value)
                value = str(int(number_value)) if number_value.is_integer() else value
            record[field] = _clean(value)
        record['_locator'] = f'{SHEET_NAME}!A{number}:{_letter(width - 1)}{number}'
        rows.append(record)
    if not rows:
        raise ValueError('NO_CLAIM_ROWS: claim sheet has no board rows')
    last = rows[-1]['_locator'].rsplit(':', 1)[-1]
    return {'format': 'xlsx', 'rows': rows, 'locator': f'{SHEET_NAME}!A1:{last}',
            'timezone_label': 'KST'}


def _letter(index):
    text = ''
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        text = chr(65 + rest) + text
    return text


# --------------------------------------------------------------- Mail (2-3)

MAIL_PATTERNS = {
    'external_claim_id': r'클레임 번호(?:는|:)\s*([A-Za-z0-9][A-Za-z0-9-]*[A-Za-z0-9])',
    'customer_name': r'^안녕하세요\.\s*(.+?)입니다\.\s*$',
    'delivery_reference': r'납품 문서\s+([A-Za-z0-9][A-Za-z0-9-]*[A-Za-z0-9])',
    'reported_conditions': r'^발생 조건\s*:\s*(.+?)\s*$',
    'claimed_quantity': r'총\s*(\d+)\s*대',
    'unit_quantity': r'각\s*(\d+)\s*대씩',
}
MAIL_TIME = (r'신고 시각은\s*(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일\s*(오전|오후)\s*(\d{1,2})시'
             r'(?:\s*(\d{1,2})분)?\s*(?:\((KST)\))?')
MAIL_PRODUCT = r'제품은\s*(\S+)\s+(\S+?),\s*설계 개정\s*(\S+?),\s*변형\s*(\S+?)입니다'
MAIL_SERIAL = r'^\s*-\s*([A-Za-z0-9][A-Za-z0-9-]*[A-Za-z0-9])\s*$'
MAIL_REQUEST = r'^(.+?요청)드립니다\.?\s*$'


def _line_of(text, position):
    return text.count('\n', 0, position) + 1


def parse_claim_mail(path):
    """Rule-based reading of the customer's plain-text claim mail.

    Only fixed Korean phrases are recognised. A value whose phrase is absent stays
    missing; the message Date header is the sending time, not the reported time.
    """
    message = email.message_from_bytes(_read_bounded(path), policy=policy.default)
    part = message.get_body(preferencelist=('plain',))
    if part is None:
        raise ValueError('MISSING_TEXT_BODY: claim mail needs a text/plain part')
    text = part.get_content().replace('\r\n', '\n')
    found, lines = {}, {}

    def keep(field, value, position):
        found[field] = _clean(value)
        lines[field] = _line_of(text, position)

    for field, pattern in MAIL_PATTERNS.items():
        match = re.search(pattern, text, re.MULTILINE)
        if match:
            keep(field, match.group(1), match.start())
    match = re.search(MAIL_TIME, text)
    if match:
        year, month, day, half, hour, minute, zone = match.groups()
        hour = int(hour) % 12 + (12 if half == '오후' else 0)
        found['reported_at_parts'] = (int(year), int(month), int(day), hour, int(minute or 0), zone)
        lines['reported_at'] = _line_of(text, match.start())
    match = re.search(MAIL_PRODUCT, text)
    if match:
        for field, value in zip(PRODUCT_FIELDS, match.groups()):
            keep(field, value, match.start())
    serials = [(m.group(1), _line_of(text, m.start())) for m in re.finditer(MAIL_SERIAL, text, re.MULTILINE)]
    marker = text.find('증상은 다음과 같습니다')
    if marker >= 0:
        rest = text[marker:].split('\n')[1:]
        symptom = next((line for line in rest if line.strip()), None)
        if symptom and not symptom.strip().startswith('발생 조건'):
            keep('reported_symptom', symptom, marker + len(text[marker:].split('\n')[0]) + 1)
    requests = list(re.finditer(MAIL_REQUEST, text, re.MULTILINE))
    if requests:
        keep('requested_action', requests[-1].group(1), requests[-1].start())
    used = list(lines.values()) + [line for _, line in serials]
    locator = f'text/plain body lines {min(used)}-{max(used)}' if used else 'text/plain body'
    return {'format': 'eml', 'fields': found, 'serials': serials, 'lines': lines,
            'locator': locator, 'sent_at': message.get('Date')}


# --------------------------------------------------------- Normalize (2-4)

def _timestamp(value, label, issues, locator):
    if value is None:
        _issue(issues, 'MISSING_FIELD', 'reported_at', locator)
        return None
    if isinstance(value, tuple):
        year, month, day, hour, minute, zone = value
        if zone != 'KST':
            _issue(issues, 'TIMEZONE_REQUIRED', 'reported time has no timezone', locator)
            return None
        try:
            return datetime(year, month, day, hour, minute, tzinfo=KST).isoformat()
        except ValueError:
            _issue(issues, 'INVALID_TIME', 'reported time is not a calendar time', locator)
            return None
    try:
        instant(value)
        return value
    except ValueError:
        code = 'TIMEZONE_REQUIRED' if 'T' in value else 'INVALID_TIME'
        _issue(issues, code, 'reported_at needs date, time and UTC offset: ' + value, locator)
        return None


def _quantity(value, issues, locator):
    if value is None:
        _issue(issues, 'MISSING_FIELD', 'quantity', locator)
        return None
    if not re.fullmatch(r'\d+', value) or int(value) <= 0:
        _issue(issues, 'INVALID_QUANTITY', 'quantity must be a positive whole number: ' + value, locator)
        return None
    return int(value)


def _from_rows(parsed, issues):
    rows = parsed['rows']
    claim, conflicts = {}, set()
    for field in CLAIM_FIELDS:
        values = {row[field] for row in rows}
        if len(values) > 1:
            _issue(issues, 'ROW_VALUE_CONFLICT', field + ' differs between rows', parsed['locator'])
            conflicts.add(field)
            claim[field] = None
        else:
            claim[field] = values.pop()
    if 'reported_at' not in conflicts:
        claim['reported_at'] = _timestamp(claim['reported_at'], parsed['timezone_label'], issues, parsed['locator'])
    units = []
    for row in rows:
        if row['serial'] is None:
            _issue(issues, 'MISSING_FIELD', 'serial', row['_locator'])
            continue
        units.append({'serial': row['serial'], 'quantity': _quantity(row['quantity'], issues, row['_locator']),
                      'locator': row['_locator']})
    quantities = [u['quantity'] for u in units]
    claimed = sum(quantities) if units and all(q is not None for q in quantities) else None
    return claim, units, claimed


def _from_mail(parsed, issues):
    fields, lines = parsed['fields'], parsed['lines']
    claim = {field: fields.get(field) for field in CLAIM_FIELDS if field != 'reported_at'}
    where = lambda field: 'text/plain line ' + str(lines[field]) if field in lines else parsed['locator']
    claim['reported_at'] = _timestamp(fields.get('reported_at_parts'), 'KST', issues, where('reported_at'))
    each = fields.get('unit_quantity')
    units = []
    for serial, line in parsed['serials']:
        quantity = _quantity(each, issues, 'text/plain line ' + str(line)) if each is not None else None
        units.append({'serial': serial, 'quantity': quantity, 'locator': 'text/plain line ' + str(line)})
    if each is None and units:
        _issue(issues, 'MISSING_FIELD', 'per-board quantity is not stated', parsed['locator'])
    claimed = None
    if fields.get('claimed_quantity') is not None:
        claimed = _quantity(fields['claimed_quantity'], issues, where('claimed_quantity'))
    elif units and all(u['quantity'] is not None for u in units):
        claimed = sum(u['quantity'] for u in units)
    return claim, units, claimed


def _relative_path(path):
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def normalize_claim(parsed, path, *, collected_at):
    """Build SourceDocument, Evidence and (when complete) ClaimIntake candidates.

    intake_state is always needs_review: only a person confirms a claim.
    Returns {'nodes': [...], 'report': {...}}; a blocked report has no ClaimIntake.
    """
    instant(collected_at)
    issues = []
    if parsed['format'] == 'eml':
        claim, units, claimed = _from_mail(parsed, issues)
    else:
        claim, units, claimed = _from_rows(parsed, issues)
    for field in REQUIRED_FIELDS:
        if field != 'reported_at' and claim.get(field) is None and not any(
                i['message'].startswith(field) for i in issues):
            _issue(issues, 'MISSING_FIELD', field, parsed['locator'])
    serials = [u['serial'] for u in units]
    if not serials:
        _issue(issues, 'MISSING_FIELD', 'reported_serials', parsed['locator'])
    if len(set(serials)) != len(serials):
        _issue(issues, 'DUPLICATE_SERIAL', 'the same serial is reported more than once', parsed['locator'])
    if claimed is None and not any(i['code'] in ('INVALID_QUANTITY', 'MISSING_FIELD') and 'quantity' in i['message']
                                   for i in issues):
        _issue(issues, 'MISSING_FIELD', 'claimed_quantity', parsed['locator'])
    warnings = []
    if claimed is not None and serials and claimed != len(serials):
        _issue(warnings, 'CLAIM_QUANTITY_SERIAL_MISMATCH',
               f'claimed quantity {claimed} differs from {len(serials)} reported serials', parsed['locator'])

    raw = Path(path).read_bytes()
    checksum = hashlib.sha256(raw).hexdigest()
    key = checksum[:16]
    source = {'id': 'source:claim_response:' + key, 'type': 'SourceDocument', 'origin': 'synthetic', 'evidence': [],
              'url': 'urn:ontologics:claim_response:' + Path(path).name, 'path': _relative_path(path),
              'sha256': checksum, 'version': 'claim-demo-1', 'license': 'Project demonstration data',
              'collected_at': collected_at, 'kind': 'synthetic_claim'}
    evidence = {'id': 'evidence:claim_response:' + key, 'type': 'Evidence', 'origin': 'synthetic', 'evidence': [],
                'source_id': source['id'], 'locator': parsed['locator'],
                'description': 'Fictional customer claim statement; reported symptoms are not confirmed failures.'}
    nodes = [source, evidence]

    blocked = bool(issues)
    intake = None
    if not blocked:
        identity = digest([claim['customer_name'], claim['external_claim_id']])[:12]
        intake = {'id': 'claim:' + claim['external_claim_id'] + ':' + identity, 'type': 'ClaimIntake',
                  'origin': 'synthetic', 'evidence': [evidence['id']],
                  'external_claim_id': claim['external_claim_id'], 'customer_name': claim['customer_name'],
                  'reported_at': claim['reported_at'], 'reported_symptom': claim['reported_symptom'],
                  'claimed_quantity': claimed, 'reported_serials': serials, 'intake_state': 'needs_review'}
        intake.update({f: claim[f] for f in OPTIONAL_INTAKE_FIELDS if claim.get(f) is not None})
        nodes.append(intake)

    product = {f: claim.get(f) for f in PRODUCT_FIELDS}
    candidate = None
    if all(product.values()):
        candidate = f"config:{product['model'].lower()}:{product['revision']}:{product['variant']}"
    report = {
        'state': 'blocked' if blocked else 'needs_review',
        'format': parsed['format'],
        'file': Path(path).name,
        'sha256': checksum,
        'locator': parsed['locator'],
        'intake_id': intake['id'] if intake else None,
        'claim': {f: claim.get(f) for f in CLAIM_FIELDS if f not in PRODUCT_FIELDS},
        'claimed_quantity': claimed,
        'unit': 'piece',
        'product_reference': product,
        'configuration_candidate_id': candidate,
        'unit_candidates': [{'serial': u['serial'], 'quantity': u['quantity'], 'unit': 'piece', 'unit_id': None,
                             'match_state': 'needs_lookup', 'locator': u['locator']} for u in units],
        'quality_case_id': None,
        'requires_separate_trace_data': list(SEPARATE_TRACE_DATA),
        'issues': issues,
        'warnings': warnings,
    }
    return {'nodes': nodes, 'report': report}


PARSERS = {'.csv': parse_claim_csv, '.xlsx': parse_claim_xlsx, '.eml': parse_claim_mail}


def convert_claim(path, *, collected_at):
    """Read one claim file by extension and return its candidate nodes and report."""
    parser = PARSERS.get(Path(path).suffix.lower())
    if parser is None:
        raise ValueError('UNSUPPORTED_FORMAT: claim files must be .eml, .xlsx or .csv')
    return normalize_claim(parser(path), path, collected_at=collected_at)
