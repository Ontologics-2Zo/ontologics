"""Three isolated, synthetic cases for the temporary Task10 screen; explicit --live uses the real API."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import base64
import json
from pathlib import Path
import tempfile

from ontologics.materials.host import MaterialsHost as CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider
from ontologics.model import ROOT, canonical
from ontologics.materials.graph_validation import validate_graph
from ontologics.materials.web_input_store import initialize_inputs, WebInputStore
from ontologics.materials.persistence import MaterialsService as Service
from ontologics.web import USERS
from ontologics.materials.agent_workflow import REGISTRATION
from ontologics.materials.contract_intake import extract_contract_text
from ontologics.materials.demand_confirmation_store import PersistentDemandConfirmationWorkflow
from ontologics.materials.document_store import BusinessDocumentWorkflow
from ontologics.materials.ui import MaterialsApplication, MaterialsServer
from examples.materials_contract_intake import reference_interpretation

FOLDER=ROOT/'data/materials'
SCENARIOS=('standard','missing_field','stored_conflict')


def scripted_interpretation(extracted,version, *, reference=None):
    """Map unchanged reference lines to actual TXT/PDF spans, never general NLP.

    Changed/extra source claims are unresolved. Even a script-like suffix stays
    visible as text but is NOT silently discarded to accept a changed contract.
    """
    if reference is None:
        reference=extract_contract_text(FOLDER/'contract.txt')
    else:
        reference=deepcopy(reference)
        reference['source_bytes']=base64.b64decode(reference.pop('source_bytes_base64'),validate=True)
    baseline=reference_interpretation(reference,data_version=version)
    expected=[s['text'].strip() for s in reference['segments'] if s['text'].strip()]
    actual=[s['text'].strip() for s in extracted['segments'] if s['text'].strip()]
    if expected!=actual:
        return dict(source_sha256=extracted['source']['sha256'],data_version=version,items=[dict(fields={})])
    by_line={s['text'].strip():s for s in extracted['segments'] if s['text'].strip()}
    fields=deepcopy(baseline['items'][0]['fields'])
    for claims in fields.values():
        for claim in claims:
            ev=claim['evidence'][0]
            original=next(s for s in reference['segments'] if s['id']==ev['segment_id'])
            target=by_line[original['text'].strip()]
            # Preserve span within the corresponding original line, including
            # the second zero for allocated_quantity.
            start=ev['start']-len(original['text'])+len(original['text'].lstrip())
            start+=len(target['text'])-len(target['text'].lstrip())
            ev.update(segment_id=target['id'],start=start,end=start+len(ev['quote']))
    return dict(source_sha256=extracted['source']['sha256'],data_version=version,items=[dict(fields=fields)])


def make_application(settings,source_directory, *, direct_fixture=False, live_settings=None, cost_policy=None, graph_settings=None):
    identities=DevelopmentIdentityProvider(USERS,enabled=True)
    request=json.loads((FOLDER/'request.json').read_text())
    for name,ref,expected in [('contract.txt','input:planning:contract','6e2057d140963a9584ea25e514971d81e00728a7dbaeac3b19dd9ba7c519a962'),('production.json','input:planning:production','5973421783a94e122fa5f3b84fb544802cd304e07872506ea875268eee442498')]:
        actual=hashlib.sha256((FOLDER/name).read_bytes()).hexdigest()
        if actual!=expected or actual!=next(x['sha256'] for x in request['external_inputs'] if x['source_ref']==ref):
            raise ValueError('Reference bytes changed')
    scenarios={}
    reference=extract_contract_text(FOLDER/'contract.txt')
    reference['source_bytes_base64']=base64.b64encode(reference.pop('source_bytes')).decode()
    payload=dict(request=request,interpretation_reference=reference,
        defaults=dict(contract_text=(FOLDER/'contract.txt').read_text(),
            stock_text=(FOLDER/'stock.json').read_text(),production_text=(FOLDER/'production.json').read_text(),
            notice='로컬 PostgreSQL 조회 / synthetic 개발 자료 / 실제 모델 미실행',
            storage='postgresql'))
    for scenario in SCENARIOS:
        database=settings[scenario]
        if not database.schema.startswith('test_'):raise ValueError('Disposable test schema required')
        nodes=json.loads((ROOT/'data/catalog.json').read_text())['nodes']+json.loads((FOLDER/'graph_nodes_v1.json').read_text())['nodes']
        if scenario=='stored_conflict':
            next(n for n in nodes if n['id']=='contract-item:planning:001')['quantity']=120
        seed=Service(database,principals={'task10-fixture':{'kind':'human','roles':['seed','read']}})
        try:
            if direct_fixture:
                # Explicit test-only setup, not registration or approval.
                report=validate_graph(nodes)
                if not report['valid']:raise ValueError('Invalid A test fixture')
                with seed.db.transaction():
                    seed.db.execute('LOCK TABLE snapshots IN EXCLUSIVE MODE')
                    if seed._version()!=0 or seed._nodes():
                        raise ValueError('Direct fixture requires an empty test schema')
                    seed.db.execute('INSERT INTO snapshots VALUES(1,%s,%s,%s)',
                        (canonical(nodes),seed.now(),'A direct test fixture; not approved'))
            else:
                result=seed.bootstrap('task10-fixture',nodes,request_key='task10-initial-fixture')
                if not result['ok']:raise ValueError('Empty test schema seed failed')
            initialize_inputs(seed,payload)
        finally:seed.close()
        host=CommonHost(identities,database,agent_registrations=(REGISTRATION,),cost_policy=cost_policy,
                        graph_settings=graph_settings)
        demands=PersistentDemandConfirmationWorkflow(host,identities,database)
        scenarios[scenario]=dict(host=host,demands=demands,store=BusinessDocumentWorkflow(host,demands),
                                inputs=WebInputStore(host))
    from ontologics.openai_provider import OpenAIProvider
    factory=(lambda: OpenAIProvider(live_settings,REGISTRATION.allowed_tools)) if live_settings else None
    return MaterialsApplication(scenarios,DevelopmentContext('dev-alice'),source_directory,scripted_interpretation,
        provider_factory=factory,model_name=live_settings.model if live_settings else None)


def serve(port=0, *, direct_fixture=False, live=False, env_file=None):
    from ontologics.database import environment_values
    from ontologics.openai_provider import OpenAISettings
    from ontologics.graph_views import GraphSettings
    from ontologics.paid_budget import cost_policy_from_environment
    values=environment_values(env_file)
    graph_settings=GraphSettings.from_environment(environ=values)
    provider_settings=OpenAISettings.from_environment(environ=values) if live else None
    policy=cost_policy_from_environment(values) if live else None
    if live and policy is None:raise ValueError("Live mode requires an explicit request cost policy")
    from tests.db import isolated_database
    with ExitStack() as stack:
        settings={s:stack.enter_context(isolated_database()) for s in SCENARIOS}
        folder=Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='task10-uploads-')))
        app=make_application(settings,folder,direct_fixture=direct_fixture,live_settings=provider_settings,cost_policy=policy,graph_settings=graph_settings)
        server=MaterialsServer(app,port)
        print(json.dumps(dict(url=server.origin,mode='synthetic data / live API' if live else 'synthetic data / scripted',shutdown='Ctrl+C cleans schemas and uploads'),ensure_ascii=False),flush=True)
        try:server.serve_forever(poll_interval=.2)
        except KeyboardInterrupt:pass
        finally:server.server_close()


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--direct-test-fixture',action='store_true',
                        help='Insert validated fixtures directly into empty test schemas; no graph approval')
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--live',action='store_true',help='Use real API for interpretation and review; no fallback')
    parser.add_argument('--env-file',type=Path)
    args=parser.parse_args()
    serve(args.port,direct_fixture=args.direct_test_fixture,live=args.live,env_file=args.env_file)
