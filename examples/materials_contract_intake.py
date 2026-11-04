"""Fixed reference interpretation + real public catalog; no LLM or approval.

Requires an explicitly prepared TEST_DATABASE_URL, used only through tests.db's
new disposable schema. The fixed text path and interpretation are trusted host
fixture code, never a model-selected path or a general contract interpreter.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from ontologics.materials.contract_intake import extract_contract_text, read_contract_demand

ROOT = Path(__file__).resolve().parents[1]
_CONTRACT_SHA = '6e2057d140963a9584ea25e514971d81e00728a7dbaeac3b19dd9ba7c519a962'


def reference_interpretation(extracted: dict, *, data_version: int) -> dict:
    """Hand-authored claims for these exact reference bytes, not NLP output.

    Location checks and literal matching demonstrate the next business layer;
    they never claim a model understood the contract. 0/0/100 are explicitly
    written demonstration assumptions, not defaults for absent fields.
    """
    if (extracted['source']['sha256'] != _CONTRACT_SHA or
            hashlib.sha256(extracted['source_bytes']).hexdigest() != _CONTRACT_SHA):
        raise ValueError('Fixed interpretation is only valid for the unchanged reference contract')
    fields = {}
    mapping = [
        ('contract_item_id',3,'contract-item:planning:001',None),
        ('company',4,'Planning Reference Company',None),('customer',5,'Reference Customer (가정)',None),
        ('manufacturer',6,'OLIMEX',None),('product_model',6,'ESP32-POE',None),
        ('revision',7,'L',None),('variant',7,'WROOM',None),
        ('configuration_id',8,'config:esp32-poe:L:WROOM',None),
        ('ordered_quantity',9,'100',100),('unit',9,'piece',None),
        ('fulfilled_quantity',10,'0',0),('allocated_quantity',10,'0',0),
        ('planned_input_quantity',11,'100',100),('delivery_due',12,'2026-11-10',None),
        ('constraints',13,'명시 없음, 사람 확인 필요',None)]
    for key, line, quote, value in mapping:
        segment = next(s for s in extracted['segments'] if s['line'] == line)
        start = segment['text'].rindex(quote) if key == 'allocated_quantity' else segment['text'].index(quote)
        fields[key] = [dict(value=quote if value is None else value, evidence=[dict(
            segment_id=segment['id'], start=start, end=start+len(quote), quote=quote)])]
    return dict(source_sha256=_CONTRACT_SHA, data_version=data_version, items=[dict(fields=fields)])


def main():
    if not os.environ.get('TEST_DATABASE_URL'):
        print('Requires an explicitly prepared disposable TEST_DATABASE_URL; no DB selected automatically')
        return 2
    from tests.db import isolated_database
    from ontologics.materials.host import MaterialsHost as CommonHost
    from ontologics.identity import DevelopmentIdentityProvider, UserIdentity, DevelopmentContext
    from ontologics.materials.persistence import MaterialsService as Service
    with isolated_database() as settings:
        seed = Service(settings, principals={'fixture': {'kind': 'human', 'roles': ['read', 'seed']}})
        try:
            response = seed.bootstrap('fixture', json.loads((ROOT/'data/catalog.json').read_text())['nodes'],
                                      request_key='task02-catalog-fixture')
            if not response['ok']: raise RuntimeError('Dedicated catalog seed failed')
            version = response['data_version']
        finally:
            seed.close()
        host = CommonHost(DevelopmentIdentityProvider([
            UserIdentity('task02-example-reader', frozenset({'read'}))], enabled=True), settings)
        extracted = extract_contract_text(ROOT/'data/production_planning/contract.txt')
        interpreted = reference_interpretation(extracted, data_version=version)
        with host.request(DevelopmentContext('task02-example-reader')) as session:
            result = read_contract_demand(session, {'data_version': version}, extracted, interpreted)
        query, report = result['query'], result['report']
        print(json.dumps(dict(mode='dedicated_pg_catalog_seed_and_manual_interpretation_not_operational_approval',
            public_query=dict(data_version=version,
                lookup_ok=query['lookup_response']['ok'] if query['lookup_response'] else None,
                configuration_count=len(query['configuration_responses']), errors=query['errors']),
            report=report), ensure_ascii=False, indent=2))
        return 0 if report is not None and report['valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
