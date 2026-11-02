"""Temporary A-only HTTP adapter. All authentication/CSRF and business work is reused.

POSTs deliberately use the existing Handler /api/tools/ dispatch seam. These
host actions are NOT registered AgentGateway tools. Live mode uses the existing provider through an explicit factory; no fallback.
"""
from copy import deepcopy
import base64
import binascii
import hashlib
import json
from pathlib import Path
import uuid
from urllib.parse import urlsplit

from ..model import canonical, digest
from ..service import DomainError
from ..web import WebApplication, WebServer, Handler, exact_keys, failure
from ..runtime import ModelConfig
from .contract_intake import extract_contract_text
from .contracts import validate_planning_context
from .demand_confirmation import DemandConfirmationWorkflow
from .agent_workflow import interpret_source, inspect_report
from .agent_result_separation import run_review_preserving_host
from examples.materials_agent import FixtureProvider

PREFIX='/api/tools/planning/'
SCOPES=[dict(item_id='contract-item:planning:001',scope='ordered_quantity')]
STATIC=Path(__file__).with_name('ui_static')
BOUND=('scenario','case_id','data_version','revision','target_sha256')


def checked(response):
    if not response['ok']:
        error=response['errors'][0]
        raise DomainError(error['code'],error['message'])
    return response['data']


def object_json(text):
    if not isinstance(text,str) or len(text)>150000: raise ValueError('Bounded JSON text required')
    value=json.loads(text,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if not isinstance(value,dict):raise ValueError('JSON object required')
    return value


class MaterialsApplication(WebApplication):
    def __init__(self,scenarios,context,source_directory,interpretation_builder, *, provider_factory=None, model_name=None):
        super().__init__(scenarios['standard']['host'],context)
        self.scenarios=scenarios
        self.source_directory=Path(source_directory)
        self.interpretation_builder=interpretation_builder
        self.provider_factory=provider_factory
        self.model_name=model_name
        self.cases={}
        self.intakes={}

    def envelope(self,scenario,data):
        with scenario['host'].request(self.context) as session:
            r=session.call('lookup',{'kind':'ContractItem'})
        checked(r); r['data']=data
        # Strip temporary upload paths from screen metadata. Downloaded immutable
        # documents retain their exact stored bytes and source provenance.
        if 'markdown' not in data:
            r=json.loads(canonical(r).replace(str(self.source_directory),'[temporary-upload]'))
        return r

    def dispatch(self,path,body=None):
        if path=='/api/context':return super().dispatch(path,body)
        if not path.startswith(PREFIX):return failure('ROUTE_NOT_FOUND','이 시험 화면은 자재 관리 전용입니다.')
        if not self.model_lock.acquire(blocking=False):return failure('AI_BUSY','처리 중입니다. 완료 후 같은 요청으로 재시도하세요.')
        try:
            return self.planning(path[len(PREFIX):],body)
        finally:self.model_lock.release()

    def scenario(self,body):
        if not isinstance(body,dict) or not isinstance(body.get('scenario'),str) or body['scenario'] not in self.scenarios:
            raise DomainError('INVALID_INPUT','서버에 준비된 세 시험 사례만 선택할 수 있습니다.')
        s=self.scenarios[body['scenario']]
        with s['host'].request(self.context) as session:
            r=session.call('lookup',{'kind':'ContractItem'}); checked(r)
            bound=session.context
        inputs=checked(s['inputs'].get(self.context))
        return dict(s,**inputs),r,bound

    def current(self,body):
        s,public,bound=self.scenario(body)
        case=self.cases.get(body['case_id']) if isinstance(body['case_id'],str) else None
        if case is None or case['scenario']!=body['scenario']:raise DomainError('NOT_FOUND','접수 내역을 다시 불러오세요.')
        if case['cancelled']:raise DomainError('CANCELLED','취소한 시험입니다. 새 접수로 다시 시작하세요.')
        saved=case['saved']
        if (type(body['data_version']) is not int or type(body['revision']) is not int or
            body['data_version']!=public['data_version'] or body['data_version']!=case['version'] or
            body['revision']!=saved['revision'] or body['target_sha256']!=saved['target_sha256']):
            raise DomainError('DEMAND_TARGET_CHANGED','버전·revision·hash가 변경됐습니다. 새 접수 후 확인하세요.')
        demand=checked(s['demands'].get(self.context,saved['demand_id']))
        if demand['current_revision']!=saved['revision'] or demand['target_sha256']!=saved['target_sha256']:
            raise DomainError('STALE_REVISION','수요가 정정됐습니다. 현재 revision을 확인하세요.')
        issues=inspect_report(demand['intake_report'],public['data'])
        if issues:raise DomainError('SOURCE_OR_STORED_DATA_UNRESOLVED','데이터 담당자에게 원문·항목·개정 이력·저장 데이터 점검을 요청하세요.')
        return s,case,demand,bound

    def planning(self,op,body):
        if op=='defaults':
            exact_keys(body,('scenario',)); s,r,_=self.scenario(body)
            return self.envelope(s,dict(s['defaults'],data_version=r['data_version'],
                model_mode='live' if self.provider_factory else 'scripted',model_name=self.model_name))
        if op=='intake':return self.intake(body)
        extras={'confirm':('request_key','note'),'run':('request_key','mode'),'cancel':('request_key',),
                'refresh':(), 'document':('document_ref','document_version','content_sha256'),
                'download':('document_ref','document_version','content_sha256')}
        if op not in extras:raise DomainError('ROUTE_NOT_FOUND','지원하지 않는 화면 작업입니다.')
        exact_keys(body,BOUND+extras[op])
        if op in ('run','confirm','cancel'):
            if not isinstance(body['request_key'],str) or not 1<=len(body['request_key'])<=160:
                raise DomainError('INVALID_REQUEST_KEY','요청 식별자가 필요합니다.')
        s,case,demand,bound=self.current(body)
        if op=='refresh':return self.envelope(s,self.view(case,demand))
        if op=='confirm':
            checked(s['demands'].confirm(self.context,case['saved']['demand_id'],body['revision'],
                expected_target_sha256=body['target_sha256'],scopes=SCOPES,note=body['note'],
                request_key=body['request_key']))
            fresh=checked(s['demands'].get(self.context,case['saved']['demand_id']))
            return self.envelope(s,self.view(case,fresh))
        if op in ('cancel','run') and bound['subject']!=case['owner']:
            raise DomainError('DRAFT_OWNER_REQUIRED','접수 작성자인 Alice로 전환하세요.')
        if op=='cancel':
            case['cancelled']=True
            return self.envelope(s,dict(status='cancelled',message='이 임시 화면의 재개를 중단했습니다. 기존 확인 사건의 삭제·철회는 아닙니다.'))
        if op=='run':
            if body['mode'] not in ('direct','agent'):raise DomainError('INVALID_INPUT','직접 호스트 또는 대역 Agent를 선택하세요.')
            if any(scope not in demand['confirmation']['verified_scopes'] for scope in SCOPES):
                raise DomainError('CONFIRMATION_REQUIRED','Bob이 현재 주문 수량 범위를 확인해야 합니다. 전체 계약 승인과 다릅니다.')
            args=deepcopy(case['arguments'])
            providers=[]
            if body['mode']=='agent':
                def factory(task,tools):
                    if self.provider_factory:
                        provider=self.provider_factory(); providers.append(provider)
                        return provider
                    provider=FixtureProvider(task,tools)
                    def respond(**kw):
                        out=provider(**kw)
                        if body['scenario']=='missing_field' and not out['tool_calls']:
                            value=json.loads(out['text']);value['facts']['schedule']['remaining'].pop('processing_start')
                            out['text']=json.dumps(value)
                        return out
                    return respond
                r=run_review_preserving_host(s['host'],s['demands'],s['store'],self.context,
                    calculation_arguments=args,request_key=body['request_key'],provider_factory=factory,
                    config=ModelConfig(model=self.model_name or 'task10-scripted-only',max_steps=4,max_attempts=1),resume_scopes=SCOPES)
                model=r['model_review']; runtime=model.get('runtime') or {}
                # Raw/rejected model text and answer never cross the UI boundary.
                r['model_review']={k:model.get(k) for k in ('accepted','errors')}
                r['model_review'].update(status='accepted' if model['accepted'] else 'rejected',
                    run_id=runtime.get('run_id'),
                    tool_calls=[dict(name=t['name'],run_id=t['result'].get('run_id')) for t in runtime.get('tool_trace',[])])
            else:
                created=checked(s['store'].create_documents(self.context,calculation_arguments=args,request_key=body['request_key']))
                views=[checked(s['store'].get_document(self.context,d['document_ref'],d['document_version'])) for d in created['documents']]
                if any(not v['binding_current'] or v['document_version']!=v['latest_version'] for v in views):
                    raise DomainError('DOCUMENT_STALE','문서가 변경됐습니다. 현재 문서를 다시 확인하세요.')
                facts=deepcopy(views[0]['payload'])
                r=dict(status='host_result_ready',host_result=dict(available=True,origin='host_workflow',
                    answer=dict(facts=facts,documents=created['documents'])),
                    model_review=dict(accepted=None,status='not_run',errors=[]))
            # Recheck confirmation/version even on idempotent storage replays.
            self.current(body)
            if not r['host_result']['available']:
                code=(r['model_review'].get('errors') or ['HOST_WORKFLOW_ERROR'])[0]
                raise DomainError(code,'현재 결과를 제공할 수 없습니다. 확인·입력·권한·버전을 점검하세요.')
            case['documents']=deepcopy(r['host_result']['answer']['documents'])
            r.update(mode=body['mode'],model_api_calls=sum(len(p.calls) for p in providers),
                model_name=self.model_name,model_mode='live' if self.provider_factory else 'scripted',
                notice='호스트 계산 / 생산·구매·예약 승인 아님')
            return self.envelope(s,r)
        ref={k:body[k] for k in ('document_ref','document_version','content_sha256')}
        if not any(all(d[k]==ref[k] for k in ref) for d in case.get('documents',[])):
            raise DomainError('DOCUMENT_CONTENT_CHANGED','화면에서 받은 정확한 문서·버전·hash가 필요합니다.')
        view=checked(s['store'].get_document(self.context,ref['document_ref'],ref['document_version']))
        if not view['binding_current'] or view['latest_version']!=ref['document_version'] or view['content_sha256']!=ref['content_sha256']:
            raise DomainError('DOCUMENT_STALE','현재 binding 또는 최신 버전이 바뀌어 제공을 중단했습니다.')
        if op=='document':return self.envelope(s,view)
        result=s['store'].export_document(self.context,ref['document_ref'],ref['document_version'],expected_sha256=ref['content_sha256'])
        checked(result)
        # Exact immutable Markdown, no HTML renderer or server output path.
        return result

    def view(self,case,demand):
        return dict(scenario=case['scenario'],case_id=case['id'],data_version=case['version'],
            revision=demand['revision'],target_sha256=demand['target_sha256'],status=demand['status'],
            original=demand['original'],source=demand['source'],items=demand['items'],confirmation=demand['confirmation'],
            confirmation_scope=SCOPES,stock_scope=case['stock_scope'],data_checks=[],execution=case.get('execution'),
            message='Bob으로 전환해 주문 수량만 확인한 뒤 Alice로 돌아와 실행하세요. 나머지 미확인은 유지됩니다.')

    def intake(self,body):
        exact_keys(body,('scenario','filename','content_base64','stock_text','production_text','initial_quantity','data_version','request_key'))
        s,public,bound=self.scenario(body)
        if 'draft' not in bound['permissions']:raise DomainError('FORBIDDEN','접수는 작성 권한이 필요합니다.')
        if type(body['data_version']) is not int or body['data_version']!=public['data_version']:
            raise DomainError('PUBLIC_VERSION_CHANGED','데이터 버전이 달라졌습니다. 예제를 다시 불러오세요.')
        if not isinstance(body['request_key'],str) or not 1<=len(body['request_key'])<=160:raise ValueError('Request key required')
        key=(bound['subject'],body['scenario'],body['request_key'])
        signature=digest(body)
        if key in self.intakes:
            old_signature,data=self.intakes[key]
            if old_signature!=signature:raise DomainError('IDEMPOTENCY_CONFLICT','같은 요청 키로 내용을 바꿀 수 없습니다.')
            return self.envelope(s,data)
        if len(self.cases)>=100:raise DomainError('INPUT_TOO_LARGE','시험 접수 한도입니다. 서버를 재시작하세요.')
        filename=body['filename']
        if not isinstance(filename,str) or '/' in filename or '\\' in filename or Path(filename).suffix.lower() not in ('.txt','.pdf'):
            raise DomainError('INVALID_INPUT','파일 경로가 아닌 .txt 또는 텍스트 PDF 파일을 올리세요.')
        try:raw=base64.b64decode(body['content_base64'],validate=True)
        except (ValueError,TypeError,binascii.Error):raise DomainError('INVALID_INPUT','파일 인코딩을 확인하세요.') from None
        if not 0<len(raw)<=300000:raise DomainError('INPUT_TOO_LARGE','파일은 300 KB 이하입니다.')
        sha=hashlib.sha256(raw).hexdigest(); path=self.source_directory/(sha+Path(filename).suffix.lower())
        if not path.exists():
            with path.open('xb') as f:f.write(raw)
        if path.read_bytes()!=raw:raise DomainError('SOURCE_HASH_MISMATCH','접수 원문 해시 불일치')
        extracted=extract_contract_text(path)
        if not extracted['supported']:
            raise DomainError(extracted['errors'][0]['code'],'텍스트 추출 실패: UTF-8 텍스트 또는 텍스트 PDF가 필요합니다. OCR은 지원하지 않습니다.')
        if self.provider_factory:
            provider=self.provider_factory()
        else:
            manual=self.interpretation_builder(extracted,body['data_version'],reference=s['interpretation_reference'])
            provider=lambda **kw:dict(text=json.dumps(manual),tool_calls=[])
        interpreted=interpret_source(s['host'],self.context,path,data_version=body['data_version'],
            provider=provider,config=ModelConfig(model=self.model_name or 'task10-scripted-interpretation',max_steps=1,max_attempts=1))
        execution=dict(mode='live' if self.provider_factory else 'scripted',model=self.model_name,
            model_api_calls=len(getattr(provider,'calls',[])),run_id=(interpreted.get('runtime') or {}).get('run_id'))
        interpretation=interpreted.get('interpretation')
        if not interpreted['accepted']:
            data=dict(status='blocked',scenario=body['scenario'],case_id=None,execution=execution,
                original=dict(text=extracted['text'],segments=extracted['segments']),
                report=interpreted.get('report'),data_checks=interpreted['data_checks'],errors=interpreted['errors'],
                message='데이터 담당자에게 원문·항목·개정 이력·저장 데이터를 점검해 달라고 요청하세요. 계산·문서 생성 전에 중단했습니다.')
            self.intakes[key]=(signature,data)
            return self.envelope(s,data)
        stock=object_json(body['stock_text']);production=object_json(body['production_text'])
        exact_keys(stock,('origin','purpose','stock_scope','coverage','stocks'))
        exact_keys(production,('purpose','production_conditions'))
        if not isinstance(production['production_conditions'],dict):raise DomainError('INVALID_INPUT','production_conditions는 JSON 객체여야 합니다.')
        if stock['origin']!='synthetic' or production['production_conditions'].get('origin')!='synthetic':raise ValueError('Synthetic test inputs only')
        if type(body['initial_quantity']) is not int or not 0<=body['initial_quantity']<=100:raise ValueError('Initial quantity must be 0..100')
        source_bytes={'input:planning:contract':raw,'input:planning:stock':body['stock_text'].encode(),'input:planning:production':body['production_text'].encode()}
        context=deepcopy(s['request']);context.update(stock_scope=stock['stock_scope'],stocks=stock['stocks'],production_conditions=production['production_conditions'],data_version=body['data_version'],external_inputs=[dict(source_ref=k,sha256=hashlib.sha256(v).hexdigest()) for k,v in source_bytes.items()])
        with s['host'].request(self.context) as session:
            graph=session.call('lookup',{'version':body['data_version']})
        validation=validate_planning_context(context,reference=dict(data_version=body['data_version'],nodes=checked(graph)),external_contents=source_bytes)
        if not validation['valid']:raise DomainError('INVALID_PLANNING_INPUT','기존 입력 검증 실패: '+','.join(e['code'] for e in validation['errors']))
        target=DemandConfirmationWorkflow(s['host'],s['host']._identities).prepare(self.context,path,interpretation,data_version=body['data_version'])['target']
        saved=checked(s['demands'].save(self.context,target,request_key='ui:'+body['request_key']))
        stock.update(source_ref='input:planning:stock',sha256=hashlib.sha256(source_bytes['input:planning:stock']).hexdigest())
        scope=dict(stock['stock_scope'],unit='piece',component_lots={r['component_id']:[r['lot_id']] for r in stock['stocks']},coverage_basis='Explicit three-component synthetic test only; not whole BOM availability')
        resource=deepcopy(production['production_conditions'])
        resource['source']=dict(source_ref='input:planning:production',sha256=hashlib.sha256(source_bytes['input:planning:production']).hexdigest(),locator='uploaded production JSON / production_conditions',origin='synthetic')
        assumption=dict(state='assumed',scope='all_required_materials_for_initial_quantity',quantity=body['initial_quantity'],ready_at=resource['available_at'],prerequisites=[],source=dict(source_ref='task10:initial-readiness-hypothesis',sha256=digest({'initial_quantity':body['initial_quantity'],'resource':resource}),locator='Task10 explicit initial all-material hypothesis'))
        args=dict(demand_id=saved['demand_id'],item_id='contract-item:planning:001',data_version=body['data_version'],expected_revision=saved['revision'],expected_target_sha256=saved['target_sha256'],snapshots=[stock],scope=scope,initial_quantity=body['initial_quantity'],resource=resource,schedules=[],schedule_context=dict(configuration_id='config:esp32-poe:L:WROOM',data_version=body['data_version'],scenario='virtual_remaining',evaluated_at='2026-10-28T09:00:00+09:00',selected_schedules={},milestones={}),initial_conditions=assumption)
        case=dict(id=str(uuid.uuid4()),owner=bound['subject'],scenario=body['scenario'],saved=saved,version=body['data_version'],arguments=args,stock_scope=stock['stock_scope'],cancelled=False,documents=[],execution=execution)
        self.cases[case['id']]=case
        data=self.view(case,checked(s['demands'].get(self.context,saved['demand_id'])))
        self.intakes[key]=(signature,data)
        return self.envelope(s,data)


class MaterialsHandler(Handler):
    def handle_method(self,method):
        static={'/':('index.html','text/html; charset=utf-8'),'/task10/app.js':('app.js','text/javascript; charset=utf-8'),'/task10/style.css':('style.css','text/css; charset=utf-8')}
        url=urlsplit(self.path)
        if method=='GET' and url.path in static and not url.query:
            try:
                self.guard()
                name,mime=static[url.path]
                return self.send_content(200,(STATIC/name).read_bytes(),mime)
            except DomainError as exc:return self.respond(failure(exc.code,str(exc)))
        return super().handle_method(method)


class MaterialsServer(WebServer):
    def __init__(self,app,port=0):
        super().__init__(app,port)
        self.RequestHandlerClass=MaterialsHandler
