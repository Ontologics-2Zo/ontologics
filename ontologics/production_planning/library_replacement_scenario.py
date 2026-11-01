"""Explicit user-authorized synthetic resistor approval/stock in A tables only."""
from copy import deepcopy
from ..model import canonical,digest
from ..service import DomainError

SCENARIO='resistor-replacement-demo-v1'
TARGET='scenario:resistor:4r7-0402-alternative'

def install(application,contract_id):
    context=application.context;materials=application.materials;replacements=application.rfq.replacements
    response=materials.get(context,contract_id,0)
    if not response['ok']:return response
    data=response['data'];saved=data['saved']
    if not saved or not data['saved_current'] or saved['quantity']!=120:raise DomainError('INVALID_INPUT','저장된 120대 생산 검토에서 실행하는 시나리오입니다.')
    source=next((r for r in saved['result']['components'] if r['name']=='4.7R/R0402' and r['q']==1),None)
    if source is None:raise DomainError('INVALID_INPUT','제품당 1개인 4.7Ω/R0402 원부품을 찾지 못했습니다.')
    with application.host.request(context) as session:
        svc=session._service
        def read():return svc._envelope(ok=True,data=materials.stock.latest(svc,saved['result']['configuration_id']))
        observed=svc._run(session.context['actor'],'replacement_scenario_stock_read',dict(contract_id=contract_id),read)
        if not observed['ok']:return observed
        stock=observed['data']
        if not stock or stock['payload']['origin']!='synthetic':raise DomainError('INVALID_INPUT','기존 회사 재고 시나리오에서만 실행할 수 있습니다.')
        payload=deepcopy(stock['payload'])
        if payload.get('scenario')!=SCENARIO:
            payload['scenario']=SCENARIO;payload['label']='회사 재고·저항 대체 시나리오'
            payload['scope']['as_of']=svc.now()
            for doc in payload['snapshots']:
                doc['stock_scope']['as_of']=payload['scope']['as_of']
                for row in doc['stocks']:
                    if row['component_id']==source['component_id']:
                        row.update(availability_basis='reported_available',available_quantity=80)
                        for k in ('on_hand_quantity','reserved_quantity','held_quantity','deductions_disjoint'):row.pop(k,None)
                doc['sha256']=digest({k:v for k,v in doc.items() if k!='sha256'})
    if stock['payload'].get('scenario')!=SCENARIO:
        stored=materials.stock.store(context,saved['result']['configuration_id'],payload,SCENARIO+':stock:'+contract_id)
        if not stored['ok']:return stored
    calculation=materials.calculate(context,contract_id,0,data['link']['id'],120,saved['reason'],SCENARIO+':materials:'+contract_id)
    if not calculation['ok']:return calculation
    component=dict(id=TARGET,name='DEMO-R4R7-0402 · 4.7Ω 대체 저항',scope='가상 회사 승인품',origin='synthetic',scenario=SCENARIO,
        specs=dict(resistance_ohm=4.7,package='0402',tolerance_percent=1,rated_power_w=0.0625),
        limitation='정격·허용오차 및 해당 회로 승인 조건은 시연용 가정. 실제 원부품 사양 검증이나 OLIMEX 승인 아님.')
    with application.host.request(context) as session:
        svc=session._service
        def write():
            previous=svc.db.execute('SELECT payload FROM pp_replacement_scenario_components WHERE id=%s',(TARGET,)).fetchone()
            if previous and previous['payload']!=component:raise DomainError('SCENARIO_CONFLICT','기존 가상 부품 정의가 달라 보존했습니다.')
            svc.db.execute('INSERT INTO pp_replacement_scenario_components VALUES(%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING',(TARGET,canonical(component),session.context['actor'],svc.now()))
            return svc._envelope(ok=True,data=dict(id=TARGET))
        registered=svc._run(session.context['actor'],'replacement_scenario_component',component,write,permission='review',request_key=SCENARIO+':component')
    if not registered['ok']:return registered
    fresh=materials.get(context,contract_id,0)['data']['saved']
    return replacements.record(context,contract_id,0,fresh['id'],source['component_id'],TARGET,'compatible',
        '가상 승인 DEMO-TECH-001: 4.7Ω, 0402, ±1%, 0.0625W 조건 및 핀/정격/회로 영향, 1:1 부분 혼용 충족을 시연용으로 가정. 실제 제품 승인 아님.',
        '가상 승인 DEMO-CONTRACT-001: 해당 주문의 동등 사양 저항 대체·부분 혼용 허용 가정.',
        30,'가상 재고 DEMO-STOCK-001: 대체 저항 가용30개, 예약·보류 제외 가정.',payload['scope']['as_of'],'가상 회사 자재 창고',True,SCENARIO+':review:'+fresh['id'])
