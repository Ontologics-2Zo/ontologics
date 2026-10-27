"""CQ06 approved synthetic input update -> before/after comparison; no LLM.

Each independent case uses a fresh PostgreSQL schema by default. Scripted
human review is a development check and does not establish operational approval.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import uuid

import psycopg
from psycopg import sql

from ontologics import __version__
from ontologics.order_fulfillment.contracts import load_demo_contract
from ontologics.database import DatabaseSettings
from ontologics.order_fulfillment.delivery_demo import AS_OF, ROUND, load_demo_delivery
from ontologics.demo import PRINCIPALS, must
from ontologics.ingest import build_catalog
from ontologics.model import ROOT
from ontologics.order_fulfillment.recalculation_demo import AFTER_AS_OF, CASES, load_demo_recalculation
from ontologics.order_fulfillment.service import AgentGateway, Service
from ontologics.order_fulfillment.tool_specs import B_COMPARISON_TOOLS


def run_example(database=None, *, schema=None, case='continued11'):
    candidates = load_demo_recalculation(case)
    golden = json.loads((ROOT / 'data/order_fulfillment/b_recalculation_expected.json').read_text())
    expected = golden['cases'][case]
    generated = schema is None and not isinstance(database, DatabaseSettings)
    selected_schema = schema or ('b_cq06_' + uuid.uuid4().hex if generated else None)
    settings = (database if isinstance(database, DatabaseSettings) else
                DatabaseSettings(database, selected_schema) if database is not None else
                DatabaseSettings.from_environment(schema=selected_schema))
    principals = copy.deepcopy(PRINCIPALS)
    principals['comparison-reader'] = {'kind': 'human', 'roles': ['read']}
    service = Service(settings, principals=principals)
    reader = AgentGateway(service, 'comparison-reader', allowed_tools=B_COMPARISON_TOOLS)
    try:
        base = build_catalog()['nodes'] + load_demo_contract() + load_demo_delivery()
        before = must(service.bootstrap('seed-admin', base, request_key='cq06-seed'))['data_version']
        old_graph = must(service.lookup('comparison-reader', version=before))['data']
        draft = must(service.prepare_draft('product-agent', [{'op':'put','node':n} for n in candidates],
                                           request_key='cq06-updated-inputs'))['data']
        did, dv = draft['draft_id'], draft['draft_version']
        validation = must(service.validate_draft('product-agent', did, dv))
        if not validation['data']['validation']['valid']:
            raise ValueError('CQ06 synthetic input bundle failed validation')
        args = dict(delivery_round_id=ROUND, before_version=before, after_version=before,
                    before_as_of=AS_OF, after_as_of=AS_OF)
        pending = must(reader.call('compare_response_plan', args))
        denied = service.apply('workflow', did, dv, request_key='cq06-before-review')
        must(service.review('reviewer', did, dv, decision='approve',
            note='Scripted development reviewer checked synthetic CQ06 updates; not operational approval.',
            request_key='cq06-reviewed'))
        after = must(service.apply('workflow', did, dv, request_key='cq06-applied'))['data_version']
        args.update(after_version=after, after_as_of=AFTER_AS_OF[case])
        new_graph = must(service.lookup('comparison-reader', version=after))['data']
        def counts():
            return {table:service.db.execute('SELECT COUNT(*) AS n FROM '+table).fetchone()['n']
                    for table in ('snapshots','drafts','review_events')}
        count_before = counts()
        query = must(reader.call('compare_response_plan', args))
        count_after = counts()
        data = query['data']
        new = data['after']
        checked = all(new['response_plan'][section][key] == value
                      for section in ('forecast','dispatch_request')
                      for key,value in expected[section].items())
        checked = checked and all(new['production'][key] == value for key,value in expected['production'].items())
        deltas_match = all(data[section][key] == value for section in ('quantity_deltas','time_deltas')
                           for key,value in expected[section].items())
        details = {}
        for side, version in (('before',before),('after',after)):
            details[side] = []
            for eid in data['evidence_by_side'][side]:
                evidence = must(service.lookup('comparison-reader', node_id=eid, version=version))['data'][0]
                source = must(service.lookup('comparison-reader', node_id=evidence['source_id'], version=version))['data'][0]
                details[side].append(dict(evidence=evidence,source=source))
        blocked_tools = [reader.call(tool,{}) for tool in ('prepare_draft','review','apply')]
        old_now = must(service.lookup('comparison-reader', version=before))['data']
        new_now = must(service.lookup('comparison-reader', version=after))['data']
        old_observations = {n['id']:n for n in old_graph if n['type']=='ProductionObservation'}
        new_index = {n['id']:n for n in new_graph}
        checks = {
            'independent_after_expected':checked,
            'independent_delta_expected':deltas_match,
            'explicit_approved_pair':(data['before']['data_version'],data['after']['data_version'],query['data_version'])==(before,after,after),
            'pending_not_selected':pending['data']['quantity_deltas']['on_time_quantity']==0,
            'approval_required':not denied['ok'] and denied['errors'][0]['code']=='APPROVAL_REQUIRED',
            'old_approved_graph_preserved':old_graph==old_now,
            'new_approved_graph_unchanged':new_graph==new_now,
            'read_creates_no_business_records':count_before==count_after,
            'old_observations_preserved':all(new_index[nid]==n for nid,n in old_observations.items()),
            'selected_evidence_exact_union':data['evidence']==sorted(set(data['evidence_by_side']['before'])|set(data['evidence_by_side']['after'])),
            'evidence_resolvable_per_version':all(row['evidence']['locator'] for rows in details.values() for row in rows),
            'source_hashes_unchanged':all(hashlib.sha256((ROOT/row['source']['path']).read_bytes()).hexdigest()==row['source']['sha256']
                                          for rows in details.values() for row in rows),
            'full_previous_response_preserved':data['before']['response_plan']['forecast']['on_time_quantity']==960,
            'no_execution_or_approval':data['draft_only'] and data['review_required'] and not data['execution_performed'],
            'gateway_has_no_write_authority':all(not r['ok'] and r['errors'][0]['code']=='TOOL_NOT_ALLOWED' for r in blocked_tools),
        }
        if not all(checks.values()):
            raise ValueError('CQ06 example failed: '+json.dumps(checks))
        return dict(contract_version=__version__,case=case,checks=checks,versions=[before,after],
            query=query,evidence_details=details,generated_schema_removed_on_exit=generated,
            mode='deterministic development example; scripted reviewer, no LLM',
            limitations=['No CQ06 Agent or actual LLM validation','No operational approval, sending or dispatch execution'])
    finally:
        service.close()
        if generated:
            with psycopg.connect(settings.url,autocommit=True) as connection:
                connection.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(settings.schema)))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',choices=CASES,default='continued11')
    parser.add_argument('--schema',help='Caller-owned schema; default newly generated disposable schema')
    parser.add_argument('--output',default='artifacts/local/b_cq06_task08/example.json')
    args=parser.parse_args()
    output=Path(args.output)
    if output.exists():
        raise ValueError('Use a new output path to preserve previous execution evidence')
    report=run_example(schema=args.schema,case=args.case)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x') as stream:
        stream.write(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(case=args.case,checks=report['checks'],versions=report['versions'],output=str(output)),ensure_ascii=False))


if __name__=='__main__':
    main()
