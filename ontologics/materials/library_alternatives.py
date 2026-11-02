"""Human planning decisions over immutable, full-configuration comparisons.

This is an A archive workflow, not a graph approval or production authorization.
No inferred cross-revision inventory mapping, mixed BOM or automatic acceptance.
"""
from uuid import uuid4
from .library_reservations import reservation_token
from ..model import canonical, digest
from ..service import DomainError
from .configuration_lookup import lookup_contract_configurations
from .materials import calculate_material_requirements


class LibraryAlternatives:
    def __init__(self, rfq, context):
        self.rfq=rfq; self.materials=rfq.materials; self.host=rfq.host
        with self.host.request(context) as session:
            session._service.db.execute('''CREATE TABLE IF NOT EXISTS pp_contract_material_choices(
                id TEXT PRIMARY KEY, contract_id TEXT NOT NULL, item_index INTEGER NOT NULL,
                payload JSONB NOT NULL, content_hash TEXT NOT NULL, actor TEXT NOT NULL,
                created_at TEXT NOT NULL)''')

    def latest(self, svc, contract_id, item_index):
        row=svc.db.execute('SELECT * FROM pp_contract_material_choices WHERE contract_id=%s AND item_index=%s ORDER BY created_at DESC,id DESC LIMIT 1',(contract_id,item_index)).fetchone()
        if row and digest(row['payload'])!=row['content_hash']:
            raise DomainError('CHOICE_INTEGRITY','저장된 적용안의 무결성을 확인하지 못했습니다.')
        return row

    def current(self,svc,row,material):
        if not row:return False
        p=row['payload']; r=p['result']
        stock=self.materials.stock.latest(svc,r['configuration_id'])
        return (p['material_review_id']==material['id'] and p['base_hash']==digest(material['result'])
                and r['data_version']==svc._version()
                and material['result']['stock'].get('reservation_token')==reservation_token(svc,(material['contract_id'],material['item_index']))
                and r['stock'].get('snapshot_id')==(stock['id'] if stock else None))

    def assert_base(self,svc,contract_id,item_index,material):
        svc.db.execute('SELECT id FROM pp_contract_originals WHERE id=%s FOR UPDATE',(contract_id,)).fetchone()
        latest=svc.db.execute('SELECT * FROM pp_contract_material_reviews WHERE contract_id=%s AND item_index=%s ORDER BY created_at DESC,id DESC LIMIT 1',(contract_id,item_index)).fetchone()
        link=svc.db.execute('SELECT * FROM pp_contract_configuration_links WHERE contract_id=%s AND item_index=%s ORDER BY created_at DESC,id DESC LIMIT 1',(contract_id,item_index)).fetchone()
        interpretation=svc.db.execute('SELECT id FROM pp_contract_interpretations WHERE contract_id=%s ORDER BY created_at DESC,id DESC LIMIT 1',(contract_id,)).fetchone()
        check=svc.db.execute('SELECT id FROM pp_contract_reading_checks WHERE interpretation_id=%s ORDER BY checked_at DESC,id DESC LIMIT 1',(interpretation['id'],)).fetchone() if interpretation else None
        stock=self.materials.stock.latest(svc,material['result']['configuration_id'])
        if (not latest or latest['id']!=material['id'] or digest(latest['result'])!=digest(material['result'])
            or not link or link['id']!=material['link_id'] or not interpretation or not check
            or link['interpretation_id']!=interpretation['id'] or link['review_id']!=check['id']
            or svc._version()!=material['result']['data_version']
            or material['result']['stock'].get('reservation_token')!=reservation_token(svc,(contract_id,item_index))
            or material['result']['stock'].get('snapshot_id')!=(stock['id'] if stock else None)):
            raise DomainError('STALE_REVISION','계약·구성·재고·소요량이 바뀌었습니다. 다시 계산하세요.')

    def compare(self,context,contract_id,item_index,configuration_id):
        discovered=self.rfq.inspect(context,contract_id,item_index,include_choice=False)
        if not discovered['ok']:return discovered
        base=self.materials.get(context,contract_id,item_index)
        if not base['ok']:return base
        material=base['data']['saved']; original=material['result']
        allowed={original['configuration_id']}|{c['configuration_id'] for c in discovered['data']['review']['candidates']}
        if configuration_id not in allowed:raise DomainError('INVALID_INPUT','근거가 조회된 구성만 비교할 수 있습니다.')
        with self.host.request(context) as session:
            catalog=lookup_contract_configurations(session,data_version=original['data_version'])
            if catalog['configurations'] is None:raise DomainError('CATALOG_QUERY_FAILED','구성 조회에 실패했습니다.')
            detail=next(d for d in catalog['configurations']['candidates'] if d['configuration']['id']==configuration_id)
            svc=session._service
            def read():
                self.assert_base(svc,contract_id,item_index,material)
                stock=self.materials.stock.calculate(self.materials.stock.latest(svc,configuration_id),svc,(contract_id,item_index))
                result=calculate_material_requirements(detail['items'],stock,material['quantity'],configuration_id=configuration_id,
                    data_version=original['data_version'],demand_source=original['demand_source'],bom_complete=True)
                names={n['id']:n['name'] for n in detail['components']}
                for row in result['components']:row['name']=names[row['component_id']]
                result['configuration_name']=detail['configuration']['name']
                payload=dict(material_review_id=material['id'],base_hash=digest(original),result=result)
                # Positions explain full BOM differences; never equate component IDs by name.
                def positions(r):
                    return {p:dict(name=c['name'],component_id=c['component_id'],q=c['q'],D=c['D'],A=c['A'],S=c['S']) for c in r['components'] for b in c['bom_rows'] for p in b['positions']}
                left,right=positions(original),positions(result)
                rows=[dict(position=p,before=left.get(p),after=right.get(p)) for p in sorted(set(left)|set(right))]
                blockers=[]
                if result['errors'] or result['conflicts']:blockers.append('부품·재고 자료 오류를 해결하세요.')
                if configuration_id!=original['configuration_id'] and result['unresolved']:blockers.append('대체 구성의 실장 여부·필요 수량·재고 미확인 항목을 먼저 해결하세요.')
                return svc._envelope(ok=True,data=dict(title=base['data']['title'],original=original,result=result,rows=rows,
                    comparison_hash=digest(payload),payload=payload,blockers=blockers,
                    is_alternative=configuration_id!=original['configuration_id'],choice=self.latest(svc,contract_id,item_index)))
            return svc._run(session.context['actor'],'material_alternative_compare',dict(contract_id=contract_id,item_index=item_index,configuration_id=configuration_id),read)

    def select(self,context,contract_id,item_index,configuration_id,comparison_hash,technical_basis,contract_basis,note,reviewed,request_key):
        if reviewed is not True:raise DomainError('INVALID_INPUT','변경 내용과 미확인 조건을 확인하세요.')
        for value in (technical_basis,contract_basis,note):
            if not isinstance(value,str) or len(value)>2000:raise DomainError('INVALID_INPUT','검토 근거는 2,000자 이내로 입력하세요.')
        if not note.strip():raise DomainError('INVALID_INPUT','적용안을 선택한 이유를 입력하세요.')
        response=self.compare(context,contract_id,item_index,configuration_id)
        if not response['ok']:return response
        data=response['data']
        if data['comparison_hash']!=comparison_hash:raise DomainError('STALE_REVISION','비교 결과가 변경됐습니다. 다시 확인하세요.')
        if data['blockers']:raise DomainError('UNRESOLVED_ALTERNATIVE',' '.join(data['blockers']))
        if data['is_alternative'] and (not technical_basis.strip() or not contract_basis.strip()):
            raise DomainError('APPLICABILITY_REQUIRED','기술 검토 기록과 고객의 개정 변경 허용 근거를 입력하세요.')
        payload=dict(data['payload'],technical_basis=technical_basis.strip(),contract_basis=contract_basis.strip(),note=note.strip(),
            is_alternative=data['is_alternative'],state='human_selected_planning_basis',production_authorized=False)
        material=self.materials.get(context,contract_id,item_index)['data']['saved']
        with self.host.request(context) as session:
            svc=session._service
            def save():
                self.assert_base(svc,contract_id,item_index,material)
                if not self.current(svc,dict(payload=payload),material):raise DomainError('STALE_REVISION','대체안 재고가 변경됐습니다.')
                previous=self.latest(svc,contract_id,item_index)
                if previous and previous['payload']==payload:return svc._envelope(ok=True,data=previous)
                row=dict(id=str(uuid4()),contract_id=contract_id,item_index=item_index,payload=payload,content_hash=digest(payload),actor=session.context['actor'],created_at=svc.now())
                svc.db.execute('INSERT INTO pp_contract_material_choices VALUES(%s,%s,%s,%s,%s,%s,%s)',(row['id'],contract_id,item_index,canonical(payload),row['content_hash'],row['actor'],row['created_at']))
                return svc._envelope(ok=True,data=row)
            return svc._run(session.context['actor'],'material_alternative_select',dict(contract_id=contract_id,item_index=item_index,payload=payload),save,permission='review',request_key=request_key)

    def record_stock(self,context,contract_id,item_index,configuration_id,comparison_hash,quantities,company,location,as_of,source_ref,reviewed,request_key):
        from datetime import datetime
        from .materials import available_stock
        if reviewed is not True:raise DomainError('INVALID_INPUT','가용재고의 범위와 근거를 확인하세요.')
        for value in (company,location,source_ref):
            if not isinstance(value,str) or not value.strip() or len(value)>2000:raise DomainError('INVALID_INPUT','회사·창고·재고 근거를 입력하세요.')
        try:
            if not isinstance(as_of,str) or datetime.fromisoformat(as_of.replace('Z','+00:00')).utcoffset() is None:raise ValueError()
        except ValueError:raise DomainError('INVALID_INPUT','시간대가 포함된 재고 기준 시각을 입력하세요.') from None
        compared=self.compare(context,contract_id,item_index,configuration_id)
        if not compared['ok']:return compared
        data=compared['data']
        if not data['is_alternative']:raise DomainError('INVALID_INPUT','이 화면에서는 대체 구성의 재고만 등록합니다.')
        if data['comparison_hash']!=comparison_hash:raise DomainError('STALE_REVISION','재고나 검토가 변경됐습니다.')
        allowed={c['component_id'] for c in data['result']['components']}
        if not isinstance(quantities,dict) or set(quantities)!=allowed or any(v is not None and (type(v) is not int or v<0 or v>1000000000) for v in quantities.values()):raise DomainError('INVALID_INPUT','각 부품의 가용재고를 0 이상의 정수 또는 미확인으로 입력하세요.')
        coverage={c:['reported:'+c] for c,v in quantities.items() if v is not None}
        scope=dict(company=company.strip(),location=location.strip(),as_of=as_of,unit='piece',component_lots=coverage,coverage_basis=source_ref.strip())
        document=dict(source_ref=source_ref.strip(),stock_scope={k:scope[k] for k in ('company','location','as_of')},stocks=[dict(id='report:'+c,component_id=c,lot_id='reported:'+c,unit='piece',quantity_state='known',availability_basis='reported_available',available_quantity=v) for c,v in quantities.items() if v is not None])
        document['sha256']=digest(document)
        payload=dict(origin='human_reported',label='담당자 입력 가용재고',scope=scope,snapshots=[document])
        checked=available_stock(payload['snapshots'],scope)
        if not checked['valid']:raise DomainError('INVALID_INPUT','재고 입력의 수량·범위를 확인하세요.')
        material=self.materials.get(context,contract_id,item_index)['data']['saved']
        with self.host.request(context) as session:
            svc=session._service
            def save():
                self.assert_base(svc,contract_id,item_index,material)
                if not self.current(svc,dict(payload=data['payload']),material):raise DomainError('STALE_REVISION','재고가 변경됐습니다.')
                previous=self.materials.stock.latest(svc,configuration_id)
                if previous and previous['payload']==payload:return svc._envelope(ok=True,data=dict(id=previous['id']))
                key=str(uuid4())
                svc.db.execute('INSERT INTO pp_company_stock_snapshots VALUES(%s,%s,%s,%s,%s)',(key,configuration_id,canonical(payload),session.context['actor'],svc.now()))
                return svc._envelope(ok=True,data=dict(id=key))
            return svc._run(session.context['actor'],'alternative_stock_report',dict(contract_id=contract_id,item_index=item_index,configuration_id=configuration_id,payload=payload),save,permission='review',request_key=request_key)
