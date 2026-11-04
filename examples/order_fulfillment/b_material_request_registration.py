"""Real PostgreSQL workflow with development people and fictional business requests.

Uses existing common approval and graph sync; no model calls or approval bypass.
Each run owns a new schema. Source artifacts remain under ignored data/receipts.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from uuid import uuid4

from ontologics.database import DatabaseSettings
from ontologics.graph_views import GraphSettings
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.model import ROOT
from ontologics.order_fulfillment.host import CommonHost
from ontologics.order_fulfillment.material_request_registration import prepare_request, read_request
from ontologics.service import Service

BASELINE = 'evidence:demo:plan:HB-20261102-01'
ACTORS = [UserIdentity('order-fulfillment-kim',frozenset({'read','draft','review'})),
          UserIdentity('order-fulfillment-lee',frozenset({'read','review'})),
          UserIdentity('materials-reader',frozenset({'read'}))]


def fixture_nodes():
    nodes=json.loads((ROOT/'data/catalog.json').read_text())['nodes']
    path='data/order_fulfillment/sources/material_requests_demo/baseline_plan.md'
    source=dict(id='source:demo:plan:HB-20261102-01',type='SourceDocument',origin='synthetic',evidence=[],
        url='urn:demo:plan:HB-20261102-01',path=path,sha256=hashlib.sha256((ROOT/path).read_bytes()).hexdigest(),
        version='1',license='Synthetic internal example',collected_at='2026-11-03T09:00:00+09:00',kind='baseline_plan')
    return nodes+[source,dict(id=BASELINE,type='Evidence',origin='synthetic',evidence=[],
        source_id=source['id'],locator='계획서 전체',description=(ROOT/path).read_text())]


def request_cases():
    return json.loads((ROOT/'data/order_fulfillment/sources/material_requests_demo/requests.json').read_text())


def require(result):
    if not result['ok']: raise RuntimeError(result['errors'])
    return result


def run(database, graph_settings):
    provider=DevelopmentIdentityProvider(ACTORS,enabled=True)
    pg=CommonHost(provider,database,graph_settings=GraphSettings())
    graph=CommonHost(provider,database,graph_settings=graph_settings)
    seed=Service(database,principals={'fixture':{'kind':'human','roles':['seed','read']}})
    try:
        require(seed.bootstrap('fixture',fixture_nodes(),request_key='development-baseline'))
    finally:
        seed.close()
    reports=[]
    for i,payload in enumerate(request_cases(),1):
        with pg.request(DevelopmentContext(ACTORS[0].subject)) as session:
            created=prepare_request(session,payload,'material-request-'+str(i))
            repeated=prepare_request(session,payload,'material-request-'+str(i))
            assert created['draft']['data']['draft_id']==repeated['draft']['data']['draft_id']
            draft=created['draft']['data']
            checked=require(session.call('validate_draft',{'draft_id':draft['draft_id'],'version':draft['draft_version']}))
            assert checked['data']['validation']['valid']
        with pg.request(DevelopmentContext(ACTORS[1].subject)) as session:
            require(session.review(draft['draft_id'],draft['draft_version'],decision='approve',
                note='개발 신원으로 원문·추가량·수정 관계를 검토. 실제 업무 승인 아님.',request_key='review-'+str(i)))
        applied=require(graph.apply_approved(draft['draft_id'],draft['draft_version'],request_key='apply-'+str(i)))
        version=applied['data_version']
        with pg.request(DevelopmentContext(ACTORS[2].subject)) as session:
            read_pg=read_request(session,created['evidence_id'],version=version)
        with graph.request(DevelopmentContext(ACTORS[2].subject)) as session:
            read_graph=read_request(session,created['evidence_id'],version=version)
        assert read_pg==read_graph
        assert read_pg['record']['request']==payload
        reports.append(dict(request_id=payload['request_id'],revision=payload['revision'],quantity=payload['quantity'],
            source_id=created['source_id'],evidence_id=created['evidence_id'],source_path=created['source_path'],
            draft_id=draft['draft_id'],data_version=version,readback_equal=True))
    return dict(ok=True,schema=database.schema,backend=graph_settings.backend,
        development_identities=True,synthetic_operations=True,
        a_received=False,results=reports)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file')
    parser.add_argument('--graph-env-file')
    parser.add_argument('--neo4j',action='store_true')
    args=parser.parse_args()
    database=DatabaseSettings.from_environment(schema='request_demo_'+uuid4().hex,env_file=args.env_file)
    settings=GraphSettings.from_environment(env_file=args.graph_env_file or args.env_file,backend='neo4j') if args.neo4j else GraphSettings()
    print(json.dumps(run(database,settings),ensure_ascii=False,indent=2))


if __name__=='__main__': main()
