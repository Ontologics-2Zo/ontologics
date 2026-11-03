"""Contract-library adapter for existing BOM arithmetic; no stock fabrication."""
from uuid import uuid4
from ..model import canonical
from .library_reservations import reservation_token
from ..service import DomainError
from .materials import calculate_material_requirements
from .configuration_lookup import lookup_contract_configurations
from .library_stock import LibraryStock

class LibraryMaterials:
    def __init__(self,planning,context):
        self.planning=planning;self.host=planning.host
        self.stock=LibraryStock(self.host,context)
        with self.host.request(context) as session:
            session._service.db.execute('''CREATE TABLE IF NOT EXISTS pp_contract_material_reviews(
                id TEXT PRIMARY KEY,contract_id TEXT NOT NULL,link_id TEXT NOT NULL,
                item_index INTEGER NOT NULL,quantity INTEGER NOT NULL,reason TEXT NOT NULL,
                result JSONB NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL)''')

    def get(self,context,contract_id,item_index):
        response=self.planning.inspect(context,contract_id)
        if not response['ok']:return response
        data=response['data']
        if type(item_index) is not int or not 0<=item_index<len(data['items']):raise DomainError('INVALID_INPUT','주문 품목을 확인하세요.')
        item=data['items'][item_index]
        if not item.get('link_current'):raise DomainError('LINK_REQUIRED','최신 계약 조건에 맞는 제품을 먼저 연결하세요.')
        with self.host.request(context) as session:
            svc=session._service
            def read():
                saved=svc.db.execute('SELECT * FROM pp_contract_material_reviews WHERE contract_id=%s AND item_index=%s ORDER BY created_at DESC,id DESC LIMIT 1',(contract_id,item_index)).fetchone()
                stock=self.stock.latest(svc,item['link']['configuration_id'])
                stock_info=None if stock is None else dict(id=stock['id'],label=stock['payload']['label'],origin=stock['payload']['origin'],scope=stock['payload']['scope'])
                return svc._envelope(ok=True,data={'stock':stock_info,'stock_changed':bool(saved and (saved['result']['stock'].get('snapshot_id')!=(stock['id'] if stock else None) or saved['result']['stock'].get('reservation_token')!=reservation_token(svc,(contract_id,item_index)))),'contract_id':contract_id,'title':data['title'],'item_index':item_index,'link':item['link'],'fields':item['fields'],'saved':saved,'saved_current':bool(saved and saved['link_id']==item['link']['id'])})
            return svc._run(session.context['actor'],'material_review_get',{'contract_id':contract_id,'item_index':item_index},read)

    def calculate(self,context,contract_id,item_index,link_id,quantity,reason,request_key):
        if type(quantity) is not int or not 0<=quantity<=10000000:raise DomainError('INVALID_INPUT','생산 검토 수량은 0~10,000,000 사이의 정수로 입력하세요.')
        if not isinstance(reason,str) or not reason.strip() or len(reason)>2000:raise DomainError('INVALID_INPUT','생산 검토 수량의 산정 근거를 입력하세요.')
        current=self.get(context,contract_id,item_index)
        if not current['ok']:return current
        data=current['data'];link=data['link']
        if link['id']!=link_id:raise DomainError('STALE_REVISION','제품 연결이 바뀌었습니다. 다시 확인하세요.')
        with self.host.request(context) as session:
            query=lookup_contract_configurations(session,data_version=link['data_version'])
            if query['configurations'] is None:raise DomainError('CATALOG_QUERY_FAILED','부품 목록을 조회하지 못했습니다.')
            details=[d for d in query['configurations']['candidates'] if d['configuration']['id']==link['configuration_id']]
            if len(details)!=1:raise DomainError('CONFIGURATION_NOT_FOUND','연결된 제품 구성을 찾을 수 없습니다.')
            detail=details[0]
            svc=session._service
            def read_stock():
                return svc._envelope(ok=True,data=self.stock.calculate(self.stock.latest(svc,link['configuration_id']),svc,(contract_id,item_index)))
            observed=svc._run(session.context['actor'],'material_stock_get',{'configuration_id':link['configuration_id']},read_stock)
            if not observed['ok']:return observed
            stock=observed['data']
            source={'contract_id':contract_id,'link_id':link_id,'review_id':link['review_id'],'quantity_basis':{'value':quantity,'reason':reason,'unit':'boards'},'state':'human_review_input'}
            result=calculate_material_requirements(detail['items'],stock,quantity,configuration_id=link['configuration_id'],data_version=link['data_version'],demand_source=source,bom_complete=True)
            names={n['id']:n['name'] for n in detail['components']}
            for row in result['components']:row['name']=names.get(row['component_id'],row['component_id'])
            result['configuration_name']=detail['configuration'].get('name',link['configuration_id'])
            svc=session._service
            binding={'contract_id':contract_id,'link_id':link_id,'item_index':item_index,'quantity':quantity,'reason':reason,'result':result}
            def save():
                latest_stock=self.stock.latest(svc,link['configuration_id'])
                if stock.get('snapshot_id')!=(latest_stock['id'] if latest_stock else None) or stock.get('reservation_token')!=reservation_token(svc,(contract_id,item_index)):
                    raise DomainError('STALE_REVISION','재고 또는 다른 계획의 예약이 변경됐습니다. 다시 계산하세요.')
                svc.db.execute('SELECT id FROM pp_contract_originals WHERE id=%s FOR UPDATE',(contract_id,)).fetchone()
                previous=svc.db.execute('SELECT * FROM pp_contract_material_reviews WHERE contract_id=%s AND item_index=%s ORDER BY created_at DESC,id DESC LIMIT 1',(contract_id,item_index)).fetchone()
                same=previous and previous['link_id']==link_id and previous['quantity']==quantity and previous['reason']==reason and previous['result']==result
                if not same:
                    svc.db.execute('INSERT INTO pp_contract_material_reviews VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',(str(uuid4()),contract_id,link_id,item_index,quantity,reason,canonical(result),session.context['actor'],svc.now()))
                return svc._envelope(ok=True,data={'result':result})
            return svc._run(session.context['actor'],'material_review_calculate',binding,save,permission='review',request_key=request_key)
