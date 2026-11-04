"""Explicit, isolated Hanbit business workspace; never run during normal reads.

Business prose is ordinary operational language. Synthetic origin and assumptions
remain in the bundle. No production approval, reservation or mail is fabricated.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import sys

from ..database import DatabaseSettings
from ..graph_views import GraphSettings
from ..host import CommonHost
from ..identity import DevelopmentContext, DevelopmentIdentityProvider
from ..model import ROOT, digest
from ..service import Service
from ..web import USERS

BUNDLE = Path(__file__).with_name('data') / 'hanbit_facilities_20261104'
CONFIGURATIONS = ('config:esp32-poe:L:WROOM', 'config:esp32-poe:L1:WROOM')


def checked(response):
    if not response['ok']:
        raise ValueError(str(response['errors']))
    return response['data']


def read_bundle():
    manifest = json.loads((BUNDLE / 'manifest.json').read_text())
    for entry in manifest['files']:
        path = (BUNDLE / entry['path']).resolve()
        if not path.is_relative_to(BUNDLE.resolve()):
            raise ValueError('업무 자료 경로를 확인하세요.')
        if hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('업무 자료 해시가 다릅니다: ' + entry['path'])
    return manifest


def mail_environment():
    read_bundle()
    return {
        'MATERIALS_MAIL_TRANSPORT': 'local_capture',
        'MATERIALS_MAIL_FROM': 'materials@ontologics.test',
        'MATERIALS_MAIL_FROM_NAME': '온톨로직스 자재관리팀',
        'MATERIALS_MAIL_CONTACTS': (BUNDLE / 'buyer_contacts.json').read_text(),
    }


def _check_stock(app, context, bom, process):
    """Do not reset a workspace once its stock observations or ledger changed."""
    from .library_reservations import active_rows
    from .library_fulfillment import received_totals
    from .library_warehouse import held_totals
    with app.host.request(context) as session:
        svc = session._service
        if active_rows(svc) or any(received_totals(svc).values()) or any(held_totals(svc).values()):
            raise ValueError('기존 예약·입고 재고가 있어 초기 자료를 다시 넣을 수 없습니다.')
        rows = svc.db.execute('SELECT configuration_id,payload FROM pp_company_stock_snapshots').fetchall()
        if any(row['configuration_id'] not in CONFIGURATIONS or row['payload'] != bom for row in rows):
            raise ValueError('변경된 부품 재고가 있습니다. 현재 기록을 보존했습니다.')
        rows = svc.db.execute('SELECT payload FROM pp_process_material_stock').fetchall()
        if any(row['payload'] != process for row in rows):
            raise ValueError('변경된 공정 재고가 있습니다. 현재 기록을 보존했습니다.')


def install(database, graph_settings, *, schema_prefix='materials_hanbit_'):
    """Run from a trusted development CLI, on a dedicated store only."""
    if not database.schema.startswith(schema_prefix):
        raise ValueError('materials_hanbit_로 시작하는 전용 개발 스키마가 필요합니다.')
    manifest = read_bundle()
    bom = json.loads((BUNDLE / 'bom_stock.json').read_text())
    process = json.loads((BUNDLE / 'process_stock.json').read_text())
    from .materials import available_stock
    from .library_process_materials import load_reference, validate_stock
    result = available_stock(bom['snapshots'], bom['scope'])
    if not result['valid'] or result['unresolved']:
        raise ValueError('부품 재고 자료를 확인하세요.')
    validate_stock(process, load_reference())
    if bom['scope']['company'] != process['company'] or process['company'] != manifest['supplier']:
        raise ValueError('계약·부품·공정 재고의 회사가 다릅니다.')
    catalog = json.loads((ROOT / 'data/catalog.json').read_text())['nodes']
    seed = Service(database, principals={'materials-business-seed': {'kind': 'human', 'roles': ['seed']}})
    try:
        checked(seed.bootstrap('materials-business-seed', catalog, request_key='hanbit-catalog-v1'))
    finally:
        seed.close()
    identities = DevelopmentIdentityProvider(USERS, enabled=True)
    pg = CommonHost(identities, database)
    graph = CommonHost(identities, database, graph_settings=graph_settings)
    context = DevelopmentContext('dev-alice')
    from .workspace import MaterialsWorkspace
    app = MaterialsWorkspace(pg, context, request_host=pg)
    _check_stock(app, context, bom, process)
    from .business_case_graph import register_business_case
    registered = register_business_case(pg, author_subject='dev-alice', reviewer_subject='dev-bob', projection_host=graph)
    from .library_inventory_identity import seed_company_test_links
    link_count = seed_company_test_links(pg, context)
    for cid in CONFIGURATIONS:
        checked(app.materials.stock.store(context, cid, bom, 'hanbit-stock:' + digest(cid)))
    checked(app.process_materials.store(context, process, 'hanbit-process-stock-v1'))
    original = checked(app.library.register(context,
        title='한빛시설관리 통합운영센터 증설용 제어보드 공급계약서',
        counterparty=manifest['customer'], filename='HFM-2026-1104-01_제어보드_공급계약서.txt',
        content_base64=base64.b64encode((BUNDLE / 'contract.txt').read_bytes()).decode(),
        request_key='hanbit-contract-original-v1'))
    with graph.request(context) as session:
        snapshot = session.call('lookup', {})
        checked(snapshot)
    inbox = checked(MaterialsWorkspace(graph, context, request_host=graph).requests.inbox(context))
    request = next(row for row in inbox['requests'] if row['request']['request_id'] == manifest['request_id'])
    if request['issues']:
        raise ValueError(str(request['issues']))
    return dict(ok=True, schema=database.schema, data_version=snapshot['data_version'],
                original_id=original['id'], title=original['title'], contract_quantity=180,
                request_id=manifest['request_id'], request_evidence_id=request['evidence_id'],
                additional_quantity=60, shared_component_pairs=link_count,
                stock_components=len(result['components']), process_materials=len(process['lots']),
                graph=registered)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('install', 'serve'))
    parser.add_argument('--development', action='store_true', required=True)
    parser.add_argument('--schema', required=True)
    parser.add_argument('--env-file')
    parser.add_argument('--graph-env-file')
    parser.add_argument('--graph-backend', choices=('postgres', 'neo4j'), default='postgres')
    parser.add_argument('--port', type=int, default=8063)
    args = parser.parse_args()
    if not args.schema.startswith('materials_hanbit_'):
        parser.error('Use a dedicated materials_hanbit_ schema; existing workspaces are preserved.')
    if args.command == 'install':
        database = DatabaseSettings.from_environment(schema=args.schema, env_file=args.env_file)
        settings = GraphSettings.from_environment(env_file=args.graph_env_file or args.env_file, backend=args.graph_backend)
        print(json.dumps(install(database, settings), ensure_ascii=False, indent=2))
        return 0
    # This profile deliberately keeps requests and A calculations in one store.
    if any(key.startswith('MAT_REQUEST_') and value for key, value in os.environ.items()):
        parser.error('Clear MAT_REQUEST_* for this self-contained business workspace.')
    os.environ.update(mail_environment())
    forwarded = ['serve', '--development', '--schema', args.schema, '--user', 'alice',
                 '--port', str(args.port), '--graph-backend', args.graph_backend]
    for key in ('env_file', 'graph_env_file'):
        if getattr(args, key):
            forwarded.extend(['--' + key.replace('_', '-'), getattr(args, key)])
    sys.argv = [sys.argv[0], *forwarded]
    from ..web import main as web_main
    return web_main()


if __name__ == '__main__':
    raise SystemExit(main())
