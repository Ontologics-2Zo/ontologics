"""Local persistent archive with real model interpretation and human reading checks."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit
from ..database import DatabaseSettings
from .host import MaterialsHost as CommonHost
from ..identity import DevelopmentContext,DevelopmentIdentityProvider
from ..web import WebApplication,WebServer,Handler,USERS,exact_keys,failure
from .contract_library import ContractLibrary
from .library_review import LibraryReview
from .library_review import REGISTRATION
from .library_product_review import LibraryProductReview
from .customer_library import CustomerLibrary
from .library_materials import LibraryMaterials
from .library_rfq import LibraryRFQ

STATIC=Path(__file__).with_name('library_static')

class LibraryApplication(WebApplication):
    def __init__(self,host,context,provider_factory=None,model=None,request_host=None):
        super().__init__(host,context)
        self.library=ContractLibrary(host,context)
        self.review=LibraryReview(self.library,context,provider_factory,model)
        self.customers=CustomerLibrary(self.review,context)
        self.planning=LibraryProductReview(self.review,context)
        self.materials=LibraryMaterials(self.planning,context)
        self.rfq=LibraryRFQ(self.materials,context)
        from .library_reservations import LibraryReservations
        self.reservations=LibraryReservations(self.rfq)
        from .library_plan_document import LibraryPlanDocument
        self.plan_document=LibraryPlanDocument(self.reservations,context)
        from .library_fulfillment import LibraryFulfillment
        self.fulfillment=LibraryFulfillment(self.plan_document)
        from .library_schedule import LibrarySchedule
        self.schedule=LibrarySchedule(self.plan_document)
        from .library_process_materials import LibraryProcessMaterials
        self.process_materials=LibraryProcessMaterials(host,context)
        from .library_process_rfq import LibraryProcessRFQ
        self.process_rfq=LibraryProcessRFQ(self.process_materials,context)
        from .request_workflow import RequestWorkflow
        self.requests=RequestWorkflow(self.materials,self.process_rfq,context,request_host)
    def dispatch(self,path,body=None):
        if path=='/api/tools/contracts/request-inbox':
            exact_keys(body,())
            return self.requests.inbox(self.context)
        if path=='/api/tools/contracts/request-seen':
            exact_keys(body,('evidence_id',))
            return self.requests.seen(self.context,**body)
        if path=='/api/tools/contracts/request-review':
            exact_keys(body,('evidence_id','data_version','allowance_percent','request_key'))
            return self.requests.review(self.context,**body)

        if path=='/api/tools/contracts/process-requirement-preview':
            exact_keys(body,('configuration_id','quantity','allowance_percent'))
            return self.process_rfq.preview(self.context,**body)
        if path=='/api/tools/contracts/process-requirement-auto':
            exact_keys(body,('configuration_id','quantity','allowance_percent','job_name','basis_hash','reviewed','request_key'))
            return self.process_rfq.automatic_review(self.context,**body)
        if path=='/api/tools/contracts/process-requirement-review':
            exact_keys(body,('configuration_id','quantity','job_name','inputs','reviewed','request_key'))
            return self.process_rfq.review(self.context,**body)
        if path=='/api/tools/contracts/process-requirement-get':
            exact_keys(body,('configuration_id','review_id'))
            return self.process_rfq.inspect(self.context,**body)
        if path=='/api/tools/contracts/process-rfq-create':
            exact_keys(body,('review_id','material_ids','supplier','desired_date','date_basis','destination','note','reviewed','request_key'))
            return self.process_rfq.create(self.context,**body)
        if path=='/api/tools/contracts/product-inventory':
            exact_keys(body,('quantity',))
            return self.materials.inventory(self.context,**body)
        if path=='/api/tools/contracts/process-materials':
            exact_keys(body,('configuration_id','quantity'))
            return self.process_materials.inspect(self.context,**body)
        if path=='/api/tools/contracts/customer':
            exact_keys(body,('contract_id',))
            return self.customers.inspect(self.context,**body)
        if path=='/api/tools/contracts/customer-sync':
            exact_keys(body,('contract_id','request_key'))
            return self.customers.sync(self.context,**body)
        if path=='/api/tools/contracts/customer-select':
            exact_keys(body,('contract_id','customer_id','basis_hash','request_key'))
            return self.customers.select(self.context,**body)
        if path=='/api/tools/contracts/customer-contracts':
            exact_keys(body,('customer_id',))
            return self.customers.contracts(self.context,**body)
        if path=='/api/tools/contracts/planning-preview':
            exact_keys(body,('contract_id',))
            return self.planning.inspect_preview(self.context,**body)
        if path=='/api/tools/contracts/materials-preview':
            exact_keys(body,('contract_id','item_index'))
            return self.materials.preview(self.context,**body)
        if path=='/api/tools/contracts/schedule-propose':
            exact_keys(body,('contract_id','item_index','not_before'))
            return self.schedule.propose(self.context,**body)
        if path=='/api/tools/contracts/schedule-save':
            exact_keys(body,('contract_id','item_index','not_before','basis_hash','inputs_hash','candidate_id','note','reviewed','request_key'))
            return self.schedule.save(self.context,**body)
        if path=='/api/tools/contracts/supply-status':
            exact_keys(body,('contract_id','item_index'))
            return self.fulfillment.inspect(self.context,**body)
        if path=='/api/tools/contracts/warehouse-status':
            exact_keys(body,('purchase_id',))
            return self.fulfillment.warehouse.inspect(self.context,**body)
        if path=='/api/tools/contracts/warehouse-register':
            exact_keys(body,('purchase_id','reference','details','request_key'))
            return self.fulfillment.warehouse.register(self.context,**body)
        if path=='/api/tools/contracts/warehouse-allocate':
            exact_keys(body,('contract_id','item_index','receipt_id','quantity','basis_hash','reviewed','request_key'))
            return self.fulfillment.warehouse.allocate(self.context,**body)
        if path=='/api/tools/contracts/supply-record':
            exact_keys(body,('contract_id','item_index','basis_hash','kind','reference','details','reviewed','request_key'))
            if body['kind']=='receipt':return failure('WAREHOUSE_REQUIRED','입고·검수는 창고 화면에서 등록하고 생산계획에서는 할당하세요.')
            return self.fulfillment.record(self.context,**body)
        if path=='/api/tools/contracts/production-plan':
            exact_keys(body,('contract_id','item_index'))
            return self.plan_document.inspect(self.context,**body)
        if path=='/api/tools/contracts/production-plan-save':
            exact_keys(body,('contract_id','item_index','basis_hash','note','reviewed','request_key'))
            return self.plan_document.save(self.context,**body)
        if path=='/api/tools/contracts/active-reservations':
            exact_keys(body,('contract_id',))
            return self.reservations.list_active(self.context,**body)
        if path=='/api/tools/contracts/reservation-plan':
            exact_keys(body,('contract_id','item_index'))
            return self.reservations.inspect(self.context,**body)
        if path=='/api/tools/contracts/reserve-materials':
            exact_keys(body,('contract_id','item_index','preview_hash','reviewed','request_key'))
            return self.reservations.reserve(self.context,**body)
        if path=='/api/tools/contracts/release-materials':
            exact_keys(body,('contract_id','item_index','reservation_id','request_key'))
            return self.reservations.release(self.context,**body)
        if path=='/api/context':return super().dispatch(path,body)
        if path=='/api/tools/contracts/register':
            body=dict(body)
            body.setdefault('counterparty','')  # Legacy API accepted; never used as buyer evidence.
            exact_keys(body,('title','counterparty','filename','content_base64','request_key'))
            saved=self.library.register(self.context,**body)
            if saved['ok'] and self.review.provider_factory:
                previous=self.review.latest(self.context,saved['data']['id'])
                if previous['ok'] and previous['data'] is None:
                    self.interpret(saved['data']['id'],'auto:'+body['request_key'])
            if saved['ok']:
                link=self.customers.sync(self.context,saved['data']['id'],'buyer-register:'+body['request_key'])
                saved['data']['buyer_sync_ok']=link['ok']
            return saved
        if path=='/api/tools/contracts/search':
            exact_keys(body,('query','offset'))
            r=self.library.search(self.context,**body,customer_contract_ids=self.customers.search_contract_ids(self.context,body['query']))
            if r['ok']:
                for item in r['data']['items']:
                    latest=self.review.latest(self.context,item['id'])
                    if not latest['ok']:return latest
                    row=latest['data']
                    buyer=self.customers.inspect(self.context,item['id'])
                    if not buyer['ok']:return buyer
                    item['counterparty']=buyer['data']['buyer_name'] or ''
                    item['review_state']=('checked' if row.get('checks') else row['status']) if row else 'not_started'
            return r
        if path=='/api/tools/contracts/get':
            exact_keys(body,('contract_id',))
            r=self.library.get(self.context,body['contract_id'])
            if r['ok']:
                review=self.review.latest(self.context,body['contract_id'])
                if not review['ok']:return review
                r['data']['review']=review['data']
                buyer=self.customers.inspect(self.context,body['contract_id'])
                if not buyer['ok']:return buyer
                r['data']['buyer']=buyer['data']
                r['data']['counterparty']=buyer['data']['buyer_name'] or ''
            return r
        if path=='/api/tools/contracts/interpret':
            exact_keys(body,('contract_id','request_key'))
            return self.interpret(body['contract_id'],body['request_key'])
        if path=='/api/tools/contracts/replacements':
            exact_keys(body,('contract_id','item_index'))
            return self.rfq.replacements.inspect(self.context,**body)
        if path=='/api/tools/contracts/replacement-review':
            exact_keys(body,('contract_id','item_index','material_review_id','source_id','target_id','status','technical_basis','contract_basis','available','stock_basis','stock_as_of','location','reviewed','request_key'))
            return self.rfq.replacements.record(self.context,**body)
        if path=='/api/tools/contracts/replacement-preview':
            exact_keys(body,('contract_id','item_index','allocations'))
            return self.rfq.replacements.preview(self.context,**body)
        if path=='/api/tools/contracts/replacement-select':
            exact_keys(body,('contract_id','item_index','allocations','comparison_hash','note','reviewed','request_key'))
            return self.rfq.replacements.select(self.context,**body)
        if path=='/api/tools/contracts/rfq':
            exact_keys(body,('contract_id','item_index'))
            return self.rfq.inspect(self.context,require_component=True,**body)
        if path=='/api/tools/contracts/purchase-request-create':
            exact_keys(body,('contract_id','item_index','material_review_id','component_ids','department','desired_date','date_basis','destination','technical_note','note','reviewed','request_key','choice_id'))
            return self.rfq.create(self.context,supplier='',request_type='purchase_request',require_component=True,**body)
        if path=='/api/tools/contracts/rfq-create':
            exact_keys(body,('contract_id','item_index','material_review_id','component_ids','supplier','desired_date','destination','note','request_key'))
            return self.rfq.create(self.context,**body)
        if path=='/api/tools/contracts/materials':
            exact_keys(body,('contract_id','item_index'))
            return self.materials.get(self.context,**body)
        if path=='/api/tools/contracts/materials-calculate':
            exact_keys(body,('contract_id','item_index','link_id','quantity','reason','request_key'))
            return self.materials.calculate(self.context,**body)
        if path=='/api/tools/contracts/planning':
            exact_keys(body,('contract_id',))
            return self.planning.inspect(self.context,**body)
        if path=='/api/tools/contracts/connect':
            exact_keys(body,('contract_id','interpretation_id','review_id','data_version','item_index','configuration_id','request_key'))
            return self.planning.connect(self.context,**body)
        if path=='/api/tools/contracts/storage':
            exact_keys(body,('contract_id',))
            return self.review.storage(self.context,body['contract_id'])
        if path=='/api/tools/contracts/review':
            exact_keys(body,('contract_id','interpretation_id','result_sha256','decisions','note','request_key'))
            result=self.review.confirm(self.context,scopes=[],**body)
            if result['ok']:result['data']['buyer_sync_ok']=self.customers.sync(self.context,body['contract_id'],'buyer-review:'+body['request_key'])['ok']
            return result
        if path=='/api/tools/contracts/confirm':
            exact_keys(body,('contract_id','interpretation_id','result_sha256','scopes','note','request_key'))
            result=self.review.confirm(self.context,**body)
            if result['ok']:result['data']['buyer_sync_ok']=self.customers.sync(self.context,body['contract_id'],'buyer-confirm:'+body['request_key'])['ok']
            return result
        return failure('ROUTE_NOT_FOUND','지원하지 않는 작업입니다.')

    def interpret(self,contract_id,request_key):
        if not self.model_lock.acquire(blocking=False):return failure('AI_BUSY','다른 계약을 해석 중입니다. 잠시 후 재시도하세요.')
        try:
            result=self.review.interpret(self.context,contract_id,request_key)
            if result['ok']:result['data']['buyer_sync_ok']=self.customers.sync(self.context,contract_id,'buyer-interpret:'+request_key)['ok']
            return result
        finally:self.model_lock.release()

class LibraryHandler(Handler):
    def handle_method(self,method):
        path=urlsplit(self.path)
        files={'/':'index.html','/library.js':'app.js','/library.css':'style.css','/process-rfq.js':'process_rfq.js','/request-inbox.js':'request_inbox.js'}
        if method=='GET' and path.path in files and not path.query:
            from ..service import DomainError
            try:
                self.guard()
                filename=files[path.path]
                mime={'html':'text/html','js':'text/javascript','css':'text/css'}[filename.rsplit('.',1)[1]]
                return self.send_content(200,(STATIC/filename).read_bytes(),mime+'; charset=utf-8')
            except DomainError as exc:return self.respond(failure(exc.code,str(exc)))
        # This focused UI has no role switching or unrelated common routes.
        if path.path!='/api/context' and not path.path.startswith('/api/tools/contracts/'):
            self.request_id=__import__('uuid').uuid4().hex
            return self.respond(failure('ROUTE_NOT_FOUND','지원하지 않는 작업입니다.'))
        return super().handle_method(method)

class LibraryServer(WebServer):
    def __init__(self,app,port):
        super().__init__(app,port)
        self.RequestHandlerClass=LibraryHandler

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=51171)
    parser.add_argument('--schema',default='a_contract_library')
    parser.add_argument('--seed-process-stock',action='store_true',help='사용자 승인 가상 공정 재고를 최초 한 번 등록하고 기존 기록은 보존')
    parser.add_argument('--seed-stock-links',action='store_true',help='L/L1의 동일 표기·패키지를 회사 테스트 재고 품목으로 명시적으로 연결')
    parser.add_argument('--request-schema',help='공유 요청 그래프의 PostgreSQL schema; 생략하면 현재 저장소')
    parser.add_argument('--request-database-env')
    parser.add_argument('--request-graph-env')
    parser.add_argument('--request-graph-namespace')
    args=parser.parse_args()
    from ..database import environment_values
    from ..graph_views import GraphSettings
    from ..openai_provider import OpenAISettings,OpenAIProvider
    from ..paid_budget import cost_policy_from_environment
    values=environment_values()
    provider_settings=OpenAISettings.from_environment(environ=values)
    policy=cost_policy_from_environment(values)
    if policy is None:raise ValueError('Explicit model cost policy required')
    settings=DatabaseSettings.from_environment(schema=args.schema)
    host=CommonHost(DevelopmentIdentityProvider(USERS,enabled=True),settings,agent_registrations=(REGISTRATION,),cost_policy=policy,
                    graph_settings=GraphSettings.from_environment(environ=values))
    request_host=None
    if args.request_schema:
        from dataclasses import replace
        # Explicit source files are isolated from the A workspace's DATABASE_URL.
        source_db=DatabaseSettings.from_environment(schema=args.request_schema,env_file=args.request_database_env,
            environ={} if args.request_database_env else None)
        source_graph=GraphSettings.from_environment(env_file=args.request_graph_env,
            environ={} if args.request_graph_env else None)
        if args.request_graph_namespace:source_graph=replace(source_graph,namespace=args.request_graph_namespace)
        request_host=CommonHost(DevelopmentIdentityProvider(USERS,enabled=True),source_db,graph_settings=source_graph)
    elif args.request_database_env or args.request_graph_env or args.request_graph_namespace:
        parser.error('별도 요청 저장소는 --request-schema를 함께 지정하세요.')
    app=LibraryApplication(host,DevelopmentContext('dev-alice'),lambda:OpenAIProvider(provider_settings,REGISTRATION.allowed_tools),provider_settings.model,request_host=request_host)
    if args.seed_process_stock:
        result=app.process_materials.seed_scenario(app.context,'process-material-stock-demo-v1')
        if not result['ok']:raise ValueError('공정 자재 가상 재고 초기화에 실패했습니다.')
    if args.seed_stock_links:
        from .library_inventory_identity import seed_company_test_links
        print('회사 테스트 재고 품목 연결:',seed_company_test_links(host,app.context),flush=True)
    server=LibraryServer(app,args.port)
    print(json.dumps(dict(url=server.origin,storage='persistent PostgreSQL',model_calls=True)),flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':main()
