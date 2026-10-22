"""Re-run supported CQs on PostgreSQL; deterministic vector probe is not RAG quality."""
import argparse
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import sys

from ontologics import __version__
from ontologics.demo import CONFIG_L, CONFIG_L1, PRINCIPALS, must, run_demo, example_queries
from ontologics.retrieval import EvidenceIndex
from ontologics.service import Service


def verify(schema=None):
    summary, graph = run_demo(schema=schema)
    principals = {**PRINCIPALS, 'indexer': {'kind':'system','roles':['read','index']}}
    service = Service(schema=summary['database_schema'], principals=principals)
    try:
        checks, details = {}, {}
        configs = [must(service.configuration('product-agent',cid,version=3))['data'] for cid in (CONFIG_L,CONFIG_L1)]
        checks['CQ01_product_revision_variant'] = [(d['product']['name'],d['revision']['revision'],d['configuration']['variant']) for d in configs] == [('ESP32-POE','L','WROOM'),('ESP32-POE','L1','WROOM')]
        details['configurations']=[{'id':d['configuration']['id'],'rows':len(d['items']),
            'positions':sum(len(n['positions']) for n in d['items']),
            'unknown_population_rows':sum(n['population']=='unknown' for n in d['items'])} for d in configs]
        checks['CQ02_rows_positions_unknown'] = [(d['rows'],d['positions'],d['unknown_population_rows']) for d in details['configurations']] == [(74,128,11),(74,128,12)]
        checks['CQ03_difference_positions'] = [r['position'] for r in summary['configuration_differences']['changes']] == ['R50','R51','R6','U2']
        checks['CQ04_registration_replay'] = summary['duplicate_registration'][0]['code']=='NO_CHANGES' and any(w['code']=='IDEMPOTENT_REPLAY' for w in summary['replay'])
        expected_queries={
            'A': {'mode':'A','lot_id':'lot:demo:lan8710:L07','unit_ids':['unit:U104','unit:U105','unit:U106','unit:U107'], 'at':'2026-09-20T01:00:00Z'},
            'B': {'mode':'B','lot_id':'lot:demo:lan8710:L07','unit_ids':['unit:U104','unit:U105','unit:U106','unit:U107'], 'start':'2026-08-31T15:00:00Z','end':'2026-09-20T15:00:00Z','period_meaning':'installation_overlap'}}
        expected_before=['needs_confirmation','included','needs_confirmation','excluded']
        details['answers']={}
        for mode,expected_after in [('A',['excluded','included','needs_confirmation','excluded']),('B',['included','included','needs_confirmation','excluded'])]:
            before=summary['before'][mode]['data']; after=summary['after'][mode]['data']
            unit_ids=example_queries()[mode]['unit_ids']
            checks[('CQ05' if mode=='A' else 'CQ06')+'_all_units'] = ({r['unit_id']:r['status'] for r in before['results']}==dict(zip(unit_ids,expected_before)) and
                {r['unit_id']:r['status'] for r in after['results']}==dict(zip(unit_ids,expected_after)))
            details['answers'][mode]={'query':after['query'],'before':before['results'],'after':after['results']}
            cmp=summary['same_condition_comparisons'][mode]['data']
            checks['CQ08_'+mode+'_same_conditions_versions']=cmp['before']['query']==cmp['after']['query']==expected_queries[mode] and (cmp['before_version'],cmp['after_version'])==(2,3)
        checks['CQ07_no_fabricated_evidence'] = all(r['status']=='needs_confirmation' and not r['evidence'] for mode in ('A','B') for r in summary['after'][mode]['data']['results'] if r['unit_id']=='unit:U106')
        usages=[]
        for d in configs:
            component=next(n for n in d['components'] if n['name'].startswith('LAN8710A-EZC'))
            usage=must(service.component_usage('product-agent',component['id'],version=3))['data']
            usages.append(usage)
        details['component_usage']=usages
        checks['CQ10_source_scoped_usage']=usages[0]['component_id']!=usages[1]['component_id'] and all(
            len(u['items'])==1 and u['items'][0]['positions']==['U4'] and u['items'][0]['configuration_id']==cid
            for u,cid in zip(usages,(CONFIG_L,CONFIG_L1)))
        details['verified_evidence']=[]
        evidence_ids={eid for u in usages for n in u['items'] for eid in n['evidence']}
        for mode in ('A','B'):
            u104=next(r for r in summary['after'][mode]['data']['results'] if r['unit_id']=='unit:U104')
            checks['CQ07_'+mode+'_supplement_evidence']=set(u104['evidence']) >= {'evidence:demo:initial','evidence:demo:supplement'}
            evidence_ids.update(u104['evidence'])
        for eid in sorted(evidence_ids):
            evidence=must(service.lookup('product-agent',node_id=eid,version=3))['data'][0]
            source=must(service.lookup('product-agent',node_id=evidence['source_id'],version=3))['data'][0]
            valid=(evidence['type']=='Evidence' and bool(evidence['locator']) and source['type']=='SourceDocument' and
                source['origin']==evidence['origin'] and hashlib.sha256(Path(source['path']).read_bytes()).hexdigest()==source['sha256'])
            details['verified_evidence'].append({'evidence_id':eid,'source_id':source['id'],'origin':evidence['origin'],
                'locator':evidence['locator'],'sha256':source['sha256'],'valid':valid})
        checks['CQ01_evidence_source_integrity']=all(e['valid'] for e in details['verified_evidence'])
        sources=must(service.lookup('product-agent',kind='SourceDocument',version=3))['data']
        details['source_boundaries']={'real_sources':sum(n['origin']=='real' for n in sources),
            'synthetic_sources':sum(n['origin']=='synthetic' for n in sources),
            'remaining':'NA/NS/Opened policy; cross-revision identity; full PDF 148-row visual comparison'}
        # A controlled numerical probe verifies real pgvector SQL only. Never label it semantic retrieval evaluation.
        index=EvidenceIndex(service,'indexer',model='deterministic-probe-v1',dimensions=3)
        ids=['evidence:olimex-L-wroom:row-55','evidence:demo:initial']
        documents=must(index.documents(ids,version=3))['data']['documents']
        entries=[{'evidence_id':d['evidence_id'],'content_hash':d['content_hash'],'embedding':v}
                 for d,v in zip(documents,([1,0,0],[0,1,0]))]
        must(index.index(entries,version=3,request_key='cq-vector-probe'))
        retrieved=must(index.search([1,0,0],version=3,origin='real'))
        checks['pgvector_real_sql_probe'] = retrieved['data']['candidates'][0]['evidence']==[ids[0]] and retrieved['data']['candidate_only']
        checks['vector_does_not_change_graph']=service._version()==3
        details['vector_probe']={'model':index.model,'dimensions':3,'top_evidence':retrieved['data']['candidates'][0]['evidence'],
            'scope':'controlled numerical vectors; no embedding API or semantic RAG quality evaluation'}
        backend=service.db.execute("SELECT current_setting('server_version') AS postgresql, extversion AS pgvector FROM pg_extension WHERE extname='vector'").fetchone()
        return {'created_at':datetime.now(timezone.utc).isoformat(),'contract_version':__version__,'backend':dict(backend),
            'database_schema':service.schema,'execution_mode':'real PostgreSQL + pgvector; scripted human test identities',
            'checks':checks,'passed':all(checks.values()),'CQ09':{'status':'proposed_not_implemented'},
            'CQ10_scope':'exact source-scoped ID only; no automatic cross-revision identity merge',
            'versions':[summary[k] for k in ('seed_version','registered_version','supplemented_version')],**details}
    finally:service.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schema',default=None,help='Use a fresh schema; otherwise a unique schema is created')
    parser.add_argument('--output',default='artifacts/local/cq_postgres.json')
    args=parser.parse_args()
    report=verify(args.schema)
    path=Path(args.output);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'output':str(path),'schema':report['database_schema'],'backend':report['backend'],
        'passed':report['passed'],'checks':report['checks'],'CQ09':report['CQ09']},ensure_ascii=False,indent=2))
    return 0 if report['passed'] else 1

if __name__=='__main__':sys.exit(main())
