"""Host intake over immutable sources, existing request records and drafts.

Receipts are scoped to the requesting identity and the inspected snapshot.
Uploading never approves or changes confirmed knowledge.
"""
import base64
import binascii
import hashlib
import tempfile
from .ingest import build_catalog, parse_olimex_ods
from .model import ROOT, digest, validate_graph
from .service import DomainError, configuration_changes


def source_io(action):
    def guarded():
        try:
            return action()
        except OSError:
            raise DomainError('SOURCE_STORAGE_ERROR', '원본 파일 저장·읽기를 완료하지 못했습니다. 저장소를 확인한 후 같은 요청으로 재시도하세요.') from None
    return guarded


def _receipt_id(service, actor, checksum, version):
    parts = [actor, checksum, version]
    if getattr(service, 'sandbox_client', None) is not None:
        # A new parsing policy cannot replay a pre-isolation receipt as isolated.
        # Source IDs and successful draft request keys remain unchanged.
        parts.append('sandbox-1')
    return 'receipt:' + digest(parts)


def source_intake(service, actor, filename, content_base64):
    if not isinstance(filename, str) or not 1 <= len(filename) <= 255 or not isinstance(content_base64, str):
        raise DomainError('INVALID_INPUT', '파일명과 base64 원본이 필요합니다.')
    try:
        if len(content_base64) > 6_666_668:
            raise ValueError()
        content = base64.b64decode(content_base64, validate=True)
    except (ValueError, binascii.Error):
        raise DomainError('INVALID_INPUT', '원본 파일 인코딩을 확인하세요.') from None
    checksum = hashlib.sha256(content).hexdigest()
    base = service._version()
    receipt_id = _receipt_id(service, actor, checksum, base)

    def act():
        if base != service._version():
            raise DomainError('STALE_RECEIPT', '접수 중 데이터가 변경됐습니다. 원본을 다시 접수하세요.')
        catalog = build_catalog(ROOT)
        source = next((n for n in catalog['nodes'] if n.get('kind') == 'bom' and n['sha256'] == checksum), None)
        if source is None:
            raise DomainError('UNSUPPORTED_SOURCE', '확인된 OLIMEX L/L1 WROOM ODS 원본과 일치하지 않습니다. 임의 구성으로 등록하지 않습니다.')
        sandbox = getattr(service, 'sandbox_client', None)
        parsing = {'mode': 'verified_source_legacy', 'isolated': False}
        if sandbox is not None:
            parsed = sandbox.parse('olimex_ods', content, checksum)
            rows = parsed['result']
            parsing = {'mode': 'docker_sandbox', 'isolated': True, **parsed['sandbox']}
        else:
            # Compatibility only: these bytes already match a reviewed source hash.
            # A configured runner never falls back to this trusted-source path.
            with tempfile.NamedTemporaryFile(suffix='.ods') as uploaded:
                uploaded.write(content)
                uploaded.flush()
                rows = parse_olimex_ods(uploaded.name)
        revision = source['revision']
        config = f'config:esp32-poe:{revision}:WROOM'
        index = {n['id']: n for n in catalog['nodes']}
        if sandbox is not None:
            expected = sorted((n['quantity'], n['raw_value'], index[n['component_id']]['package'],
                               tuple(n['positions']), n['population'], index[n['evidence'][0]]['locator'])
                              for n in catalog['nodes'] if n.get('configuration_id') == config)
            observed = sorted((r['quantity'], r['raw_value'], r['package'], tuple(r['positions']),
                               r['population'], r['locator']) for r in rows)
            if expected != observed:
                raise DomainError('SANDBOX_OUTPUT_INVALID', '격리 파서 결과가 확인된 원본 기준과 일치하지 않습니다.')
        selected = {config} | {n['id'] for n in catalog['nodes'] if n.get('configuration_id') == config}
        pending = list(selected)
        while pending:
            node = index[pending.pop()]
            refs = list(node.get('evidence', [])) + [node[k] for k in ('model_id', 'revision_id', 'component_id', 'source_id') if k in node]
            for ref in refs:
                if ref not in selected:
                    selected.add(ref)
                    pending.append(ref)
        current = {n['id']: n for n in service._nodes(base)}
        operations = [{'op': 'put', 'node': index[nid]} for nid in sorted(selected) if current.get(nid) != index[nid]
                      and not (index[nid]['type'] == 'ProductModel' and nid in current)]
        verification = next(v for v in catalog['verification'] if v['configuration_id'] == config)
        return service._envelope(ok=True, data={
            'receipt_id': receipt_id, 'source': source, 'revision': revision, 'configuration_id': config,
            'rows': rows, 'unknown_count': verification['unknown_population_rows'],
            'operations_count': len(operations), 'operations': operations,
            'base_version': base, 'existing_configuration': config in current,
            'verification': verification, 'received_by': actor, 'parsing': parsing,
            'notice': '실제 OLIMEX 원본 구조화입니다. 실장 미확인·부품 동일성·PDF 전체 대조의 한계를 유지하며 아직 승인·등록되지 않았습니다.',
        }, version=base)
    return service._run(actor, 'source_intake', {'sha256': checksum, 'base_version': base}, source_io(act),
                        permission='draft', request_key=receipt_id)


def receipt(service, actor, receipt_id):
    if not isinstance(receipt_id, str) or not receipt_id.startswith('receipt:') or len(receipt_id) != 72:
        raise DomainError('INVALID_INPUT', '원본 접수 ID를 확인하세요.')
    row = service.db.execute('SELECT response FROM requests WHERE actor=%s AND request_key=%s',
                             (actor, receipt_id)).fetchone()
    if not row or not row['response'].get('ok'):
        raise DomainError('RECEIPT_OWNER_REQUIRED', '현재 사용자가 접수한 원본만 초안으로 연결할 수 있습니다.')
    return row['response']['data']


def current_receipt(service, actor, receipt_id):
    data = receipt(service, actor, receipt_id)
    if data['base_version'] != service._version():
        raise DomainError('STALE_RECEIPT', '접수 이후 확정 버전이 변경됐습니다. 원본을 다시 접수해 차이를 확인하세요.')
    if (getattr(service, 'sandbox_client', None) is not None and
            (data.get('parsing', {}).get('isolated') is not True or data.get('parsing', {}).get('schema_version') != 'sandbox-1')):
        raise DomainError('SANDBOX_REINTAKE_REQUIRED', '격리 실행 전 접수입니다. 원본을 다시 접수한 뒤 새 후보를 검토하세요.')
    return data


def source_draft(service, actor, receipt_id, request_key, draft_id=None, expected_version=None):
    def act():
        data = current_receipt(service, actor, receipt_id)
        if not data['operations']:
            raise DomainError('NO_CHANGES', '동일 구성이 이미 등록되어 있습니다. 승인된 구성을 조회하세요.')
        return service._save_draft(actor, data['operations'], draft_id, expected_version)
    return service._run(actor, 'source_draft', {'receipt_id': receipt_id, 'draft_id': draft_id,
        'expected_version': expected_version}, act, permission='draft', request_key=request_key)


def source_compare(service, actor, receipt_id, left_id):
    def act():
        data = current_receipt(service, actor, receipt_id)
        if 'configuration_id' not in data:
            raise DomainError('INVALID_INPUT', 'BOM 원본 접수만 개정 비교에 사용할 수 있습니다.')
        nodes = service._candidate(data['operations'], data['base_version'])[0] if data['operations'] else service._nodes(data['base_version'])
        report = validate_graph(nodes)
        if not report['valid']:
            raise DomainError('VALIDATION_FAILED', '원본 후보의 등록 조건이 충족되지 않았습니다.', report)
        return service._envelope(ok=True, data={'left': left_id, 'right': data['configuration_id'],
            'changes': configuration_changes(nodes, left_id, data['configuration_id']),
            'is_preview': True, 'receipt_id': receipt_id, 'draft_id': None, 'draft_version': None,
            'comparison_basis': 'source descriptions by reference designator; not electrical equivalence'},
            version=data['base_version'], warnings=[{'code': 'UNAPPLIED_PREVIEW'}])
    return service._run(actor, 'source_compare', {'receipt_id': receipt_id, 'left_id': left_id}, act)


def workspace(service, actor):
    def act():
        rows = service.db.execute('''SELECT id AS draft_id, version, status, author, reviewer,
            base_version, applied_version AS applied_data_version, created_at
            FROM drafts d WHERE version=(SELECT MAX(version) FROM drafts WHERE id=d.id)
            ORDER BY created_at DESC, id LIMIT 100''').fetchall()
        return service._envelope(ok=True, data={'drafts': list(rows), 'version': service._version(), 'limit': 100})
    return service._run(actor, 'workspace', {}, act)


def history_intake(service, actor, filename, content_base64):
    """User-submitted synthetic operating evidence; no inferred facts or real logs."""
    import json
    import os
    from .model import ALLOWED_FIELDS, SYNTHETIC, instant
    if not isinstance(filename, str) or not 1 <= len(filename) <= 255 or not isinstance(content_base64, str):
        raise DomainError('INVALID_INPUT', '파일명과 base64 JSON 원본이 필요합니다.')
    service._require(actor, 'draft')  # Do not spend controller resources for a denied user.
    parsing = {'mode': 'legacy_json', 'isolated': False}
    try:
        if len(content_base64) > 266_668:
            raise ValueError()
        content = base64.b64decode(content_base64, validate=True)
        if len(content) > 200_000:
            raise ValueError()
        sandbox = getattr(service, 'sandbox_client', None)
        if sandbox is not None:
            parsed = sandbox.parse('history_json', content, hashlib.sha256(content).hexdigest())
            raw = parsed['result']
            parsing = {'mode': 'docker_sandbox', 'isolated': True, **parsed['sandbox']}
        else:
            raw = json.loads(content, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if (not isinstance(raw, dict) or set(raw) != {'origin', 'authored_at', 'notice', 'records'} or
                raw['origin'] != 'synthetic' or not isinstance(raw['notice'], str) or not raw['notice'].strip() or
                not isinstance(raw['records'], list) or not 1 <= len(raw['records']) <= 100):
            raise ValueError()
        instant(raw['authored_at'])
        for record in raw['records']:
            if (not isinstance(record, dict) or record.get('type') not in SYNTHETIC or
                    not isinstance(record.get('id'), str) or not record['id'].strip() or
                    set(record) - (ALLOWED_FIELDS[record['type']] - {'origin', 'evidence'})):
                raise ValueError()
    except (ValueError, TypeError, KeyError, binascii.Error):
        raise DomainError('INVALID_HISTORY_SOURCE', '시연용 가상 운영 기록 JSON 형식·시간·허용 필드를 확인하세요.') from None
    checksum, base = hashlib.sha256(content).hexdigest(), service._version()
    receipt_id = _receipt_id(service, actor, checksum, base)

    def act():
        if base != service._version():
            raise DomainError('STALE_RECEIPT', '접수 중 데이터가 변경됐습니다. 다시 접수하세요.')
        directory = (ROOT / 'data/receipts').resolve()
        if not directory.is_relative_to((ROOT / 'data').resolve()):
            raise DomainError('SOURCE_UNAVAILABLE', '원본 보존 경로를 확인하세요.')
        directory.mkdir(exist_ok=True)
        path = directory / (checksum + '.json')
        # Content-addressed immutable file; create atomically, never overwrite.
        with tempfile.NamedTemporaryFile(dir=directory) as tmp:
            tmp.write(content)
            tmp.flush()
            os.fsync(tmp.fileno())
            try:
                os.link(tmp.name, path)
            except FileExistsError:
                pass
        if hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
            raise DomainError('SOURCE_HASH_MISMATCH', '보존 원본의 해시가 일치하지 않습니다.')
        source_id = 'source:submitted:' + checksum
        current = {n['id']: n for n in service._nodes(base)}
        source = {'id': source_id, 'type': 'SourceDocument', 'origin': 'synthetic', 'evidence': [],
            'url': 'urn:ontologics:submitted:' + checksum, 'path': 'data/receipts/' + path.name,
            'sha256': checksum, 'version': checksum, 'license': 'User-submitted project demonstration data',
            'collected_at': current.get(source_id, {}).get('collected_at', service.now()), 'kind': 'synthetic_operations'}
        operations = [{'op': 'put', 'node': source}]
        for i, record in enumerate(raw['records']):
            eid = 'evidence:submitted:' + checksum + ':' + str(i)
            evidence = {'id': eid, 'type': 'Evidence', 'origin': 'synthetic', 'evidence': [],
                'source_id': source_id, 'locator': '/records/' + str(i),
                'description': 'User-submitted synthetic operating record; not OLIMEX operations. ' + raw['notice'][:1000]}
            node = {**record, 'origin': 'synthetic',
                    'evidence': sorted(set(current.get(record['id'], {}).get('evidence', []) + [eid]))}
            operations.extend([{'op': 'put', 'node': evidence}, {'op': 'put', 'node': node}])
        candidate, _ = service._candidate(operations, base)
        report = validate_graph(candidate)
        return service._envelope(ok=True, data={'receipt_id': receipt_id, 'base_version': base,
            'source': source, 'records': raw['records'], 'operations': operations, 'operations_count': len(operations),
            'received_by': actor, 'validation': report, 'parsing': parsing,
            'notice': '시연용 가상 운영 기록 접수입니다. 형식 검증은 사실 확인·승인이 아닙니다. 작성자와 다른 검토자가 원본과 변경 내용을 확인해야 합니다.'}, version=base)
    return service._run(actor, 'history_intake', {'sha256': checksum, 'base_version': base}, source_io(act),
                        permission='draft', request_key=receipt_id)
