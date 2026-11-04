"""Component-level 1:1 planning allocations; original configuration/BOM immutable."""
from copy import deepcopy
from datetime import datetime
from uuid import uuid4
from ..model import canonical,digest
from ..service import DomainError
from .library_alternatives import LibraryAlternatives
from .substitution import U2,U2_L1,load_revision_evidence


def replacement_result(original,reports,allocations):
    """Allocate only confirmed shortages. Shared target stock is counted once."""
    result=deepcopy(original); indexed={c['component_id']:c for c in result['components']}
    used=set();targets={}; applied=[]
    for a in allocations:
        if not isinstance(a,dict) or set(a)!={'review_id','quantity'}:raise DomainError('INVALID_INPUT','대체 검토와 수량을 확인하세요.')
        if not isinstance(a['review_id'],str):raise DomainError('INVALID_INPUT','검토 ID를 확인하세요.')
        report=reports.get(a['review_id']);n=a['quantity']
        if not report or type(n) is not int or n<=0:raise DomainError('INVALID_INPUT','저장된 검토와 양의 정수 수량이 필요합니다.')
        p=report['payload'];source=p['source_id'];target=p['target_id'];row=indexed.get(source)
        if source in used or not row or type(row['S']) is not int or n>row['S']:raise DomainError('INVALID_INPUT','대체 수량은 해당 부품의 부족량 이내여야 합니다.')
        if p['status']!='compatible' or p['known_blocker']:raise DomainError('INCOMPATIBLE','직접 대체 조건이 확인되지 않은 부품입니다.')
        used.add(source)
        identity={k:p[k] for k in ('available','stock_basis','stock_as_of','location')}
        if target in targets and targets[target]['identity']!=identity:raise DomainError('STOCK_CONFLICT','같은 대체품의 재고 보고가 다릅니다. 동일 기준으로 다시 확인하세요.')
        bucket=targets.setdefault(target,dict(quantity=0,identity=identity,name=p['target_name']))
        bucket['quantity']+=n
        row['substituted_quantity']=n
        row['original_D']=row['D'];row['D']-=n;row['S']=max(0,row['D']-row['A'])
        applied.append(dict(review_id=report['id'],source_id=source,target_id=target,source_name=row['name'],target_name=p['target_name'],quantity=n,technical_basis=p['technical_basis'],contract_basis=p['contract_basis']))
    if used & set(targets):raise DomainError('INVALID_INPUT','대체 경로의 순환·연쇄 사용은 지원하지 않습니다.')
    for target,b in targets.items():
        if target in indexed:
            row=indexed[target]
            if row['D'] is None or row['A'] is None or row['A']!=b['identity']['available']:raise DomainError('STOCK_CONFLICT','기존 BOM 부품과 대체품의 재고·소요량 기준이 일치해야 합니다.')
            row['original_D']=row['D'];row['D']+=b['quantity'];row['S']=max(0,row['D']-row['A'])
        else:
            row=dict(component_id=target,name=b['name'],q=None,D=b['quantity'],A=b['identity']['available'],S=max(0,b['quantity']-b['identity']['available']),bom_rows=[],quantity_complete=True,role='replacement')
            result['components'].append(row)
        row['replacement_demand']=b['quantity']
    result.update(purpose='component_replacement_planning_only',replacements=applied,
        conditional_upper_bound=None,whole_bom_material_limit=None,production_authorized=False)
    return result


class LibraryReplacements(LibraryAlternatives):
    def __init__(self,rfq,context):
        super().__init__(rfq,context)
        with self.host.request(context) as session:
            session._service.db.execute('''CREATE TABLE IF NOT EXISTS pp_replacement_scenario_components(
                id TEXT PRIMARY KEY,payload JSONB NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL)''')
            session._service.db.execute('''CREATE TABLE IF NOT EXISTS pp_component_replacement_reviews(
                id TEXT PRIMARY KEY,contract_id TEXT NOT NULL,item_index INTEGER NOT NULL,source_id TEXT NOT NULL,
                payload JSONB NOT NULL,content_hash TEXT NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL)''')

    def reviews(self,svc,contract_id,item_index):
        rows=svc.db.execute('SELECT DISTINCT ON (source_id) * FROM pp_component_replacement_reviews WHERE contract_id=%s AND item_index=%s ORDER BY source_id,created_at DESC,id DESC',(contract_id,item_index)).fetchall()
        for r in rows:
            if digest(r['payload'])!=r['content_hash']:raise DomainError('REVIEW_INTEGRITY','대체 검토 기록의 무결성을 확인하지 못했습니다.')
        return rows

    def current(self,svc,row,material):
        if not row or row['payload'].get('mode')!='component_replacement':return False
        if not super().current(svc,row,material):return False
        latest={r['id']:r for r in self.reviews(svc,row.get('contract_id',row['payload']['contract_id']),row.get('item_index',row['payload']['item_index']))}
        return all(a['review_id'] in latest for a in row['payload']['allocations'])

    def inspect(self,context,contract_id,item_index):
        base=self.materials.get(context,contract_id,item_index)
        if not base['ok']:return base
        data=base['data'];material=data['saved']
        if not material or not data['saved_current'] or data['stock_changed']:raise DomainError('MATERIAL_REVIEW_REQUIRED','최신 재고로 원안 소요량을 계산하세요.')
        with self.host.request(context) as session:
            catalog=session.call('lookup',dict(version=material['result']['data_version']))
            if not catalog['ok']:return catalog
            names={n['id']:n.get('name','') for n in catalog['data']}
            usage={}
            for n in catalog['data']:
                if n.get('type')=='BOMItem':usage.setdefault(n['component_id'],set()).add(names.get(n['configuration_id'],''))
            components=[dict(id=n['id'],name=n['name'],scope=' / '.join(sorted(usage.get(n['id'],{'등록 부품'})))) for n in catalog['data'] if n.get('type')=='ComponentModel']
            svc=session._service
            def read():
                self.assert_base(svc,contract_id,item_index,material)
                rows=self.reviews(svc,contract_id,item_index)
                rows=[r for r in rows if r['payload']['material_review_id']==material['id'] and r['payload']['base_hash']==digest(material['result'])]
                extras=[r['payload'] for r in svc.db.execute('SELECT payload FROM pp_replacement_scenario_components').fetchall()]
                choice=self.latest(svc,contract_id,item_index)
                return svc._envelope(ok=True,data=dict(title=data['title'],material=material,components=components+extras,reviews=rows,choice=choice,choice_current=self.current(svc,choice,material),impact=self.impact()))
            return svc._run(session.context['actor'],'component_replacement_inspect',dict(contract_id=contract_id,item_index=item_index),read)

    @staticmethod
    def impact():
        evidence=load_revision_evidence()
        return dict(verified=evidence['state']=='acquired',source_id=U2,target_id=U2_L1,
            path='data/sources/OLIMEX-Revision-changes-task05.txt',lines='33–34',
            text='U2의 TPS2376D→TPS2375D 교체와 함께 R50·R51을 실장하지 않았다는 기록입니다. 기존 주변 부품을 유지한 직접 교환의 호환 근거는 아닙니다. R6의 455mA→100mA 변경은 별도 항목입니다.')

    def record(self,context,contract_id,item_index,material_review_id,source_id,target_id,status,technical_basis,contract_basis,available,stock_basis,stock_as_of,location,reviewed,request_key):
        if not isinstance(source_id,str) or not isinstance(target_id,str):raise DomainError('INVALID_INPUT','부품을 선택하세요.')
        if reviewed is not True or status not in ('compatible','unconfirmed','rejected'):raise DomainError('INVALID_INPUT','검토 상태와 확인란을 확인하세요.')
        for v in (technical_basis,contract_basis,stock_basis,location):
            if not isinstance(v,str) or len(v)>2000:raise DomainError('INVALID_INPUT','검토 근거는 2,000자 이내로 입력하세요.')
        if available is not None and (type(available) is not int or not 0<=available<=1000000000):raise DomainError('INVALID_INPUT','가용재고는 0 이상 정수 또는 미확인입니다.')
        if available is not None:
            try:
                if not isinstance(stock_as_of,str) or datetime.fromisoformat(stock_as_of.replace('Z','+00:00')).utcoffset() is None:raise ValueError()
            except ValueError:raise DomainError('INVALID_INPUT','재고 기준 시각을 확인하세요.') from None
            if not stock_basis.strip() or not location.strip():raise DomainError('INVALID_INPUT','재고 근거와 확인 범위를 입력하세요.')
        if status=='compatible' and (not technical_basis.strip() or not contract_basis.strip() or available is None):raise DomainError('APPLICABILITY_REQUIRED','기술·계약 및 부분 혼용 허용 근거와 가용재고가 필요합니다.')
        response=self.inspect(context,contract_id,item_index)
        if not response['ok']:return response
        data=response['data'];material=data['material'];original=material['result']
        if material['id']!=material_review_id:raise DomainError('STALE_REVISION','소요량이 변경됐습니다.')
        ids={n['id']:n['name'] for n in data['components']}
        sources={r['component_id'] for r in original['components'] if type(r['S']) is int and r['S']>0}
        if source_id not in sources or target_id not in ids or source_id==target_id:raise DomainError('INVALID_INPUT','부족 원부품과 다른 등록 부품을 선택하세요.')
        blocked=(source_id,target_id)==(U2,U2_L1)
        if blocked and status=='compatible':raise DomainError('DESIGN_CHANGE_REQUIRED','보관된 기록에는 R50·R51 변경이 동반됩니다. 이 경로에서 단순 직접 대체로 확정할 수 없습니다.')
        payload=dict(material_review_id=material['id'],base_hash=digest(original),source_id=source_id,target_id=target_id,target_name=ids[target_id],status=status,technical_basis=technical_basis.strip(),contract_basis=contract_basis.strip(),available=available,stock_basis=stock_basis.strip(),stock_as_of=stock_as_of,location=location.strip(),known_blocker=blocked,provenance='synthetic_scenario' if any(c['id']==target_id and c.get('origin')=='synthetic' for c in data['components']) else 'human_review_record',ratio='1:1',production_authorized=False)
        with self.host.request(context) as session:
            svc=session._service
            def save():
                self.assert_base(svc,contract_id,item_index,material)
                previous=next((r for r in self.reviews(svc,contract_id,item_index) if r['source_id']==source_id),None)
                if previous and previous['payload']==payload:return svc._envelope(ok=True,data=previous)
                from .library_fulfillment import received_totals
                if target_id in received_totals(svc):raise DomainError('RECONCILIATION_REQUIRED','입고가 반영된 대체품의 기준 재고·검토는 대사 없이 교체할 수 없습니다.')
                row=dict(id=str(uuid4()),contract_id=contract_id,item_index=item_index,source_id=source_id,payload=payload,content_hash=digest(payload),actor=session.context['actor'],created_at=svc.now())
                svc.db.execute('INSERT INTO pp_component_replacement_reviews VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(row['id'],contract_id,item_index,source_id,canonical(payload),row['content_hash'],row['actor'],row['created_at']))
                return svc._envelope(ok=True,data=row)
            return svc._run(session.context['actor'],'component_replacement_review',dict(contract_id=contract_id,item_index=item_index,payload=payload),save,permission='review',request_key=request_key)

    def preview(self,context,contract_id,item_index,allocations):
        if not isinstance(allocations,list):raise DomainError('INVALID_INPUT','대체 수량 목록이 필요합니다.')
        response=self.inspect(context,contract_id,item_index)
        if not response['ok']:return response
        data=response['data'];material=data['material'];original=material['result']
        if original['errors'] or original['conflicts']:raise DomainError('INVALID_MATERIAL_REVIEW','원안 소요량 오류를 먼저 해결하세요.')
        reports={r['id']:deepcopy(r) for r in data['reviews']}
        from .library_reservations import reserved_totals
        with self.host.request(context) as session:
            svc=session._service
            def read_reserved():
                self.assert_base(svc,contract_id,item_index,material)
                from .library_fulfillment import received_totals
                return svc._envelope(ok=True,data=dict(reserved=reserved_totals(svc,(contract_id,item_index)),received=received_totals(svc)))
            observed=svc._run(session.context['actor'],'replacement_reserved_stock',dict(contract_id=contract_id,item_index=item_index),read_reserved)
            if not observed['ok']:return observed
        for report in reports.values():
            p=report['payload']
            if p['available'] is not None:
                p['original_report_available']=p['available']
                p['gross_available']=p['available']+observed['data']['received'].get(p['target_id'],0)
                p['available']=max(0,p['gross_available']-observed['data']['reserved'].get(p['target_id'],0))
        result=replacement_result(original,reports,allocations)
        payload=dict(mode='component_replacement',contract_id=contract_id,item_index=item_index,material_review_id=material['id'],base_hash=digest(original),result=result,allocations=sorted(allocations,key=lambda a:a['review_id']),reports=[reports[a['review_id']] for a in sorted(allocations,key=lambda a:a['review_id'])])
        return dict(response,data=dict(payload=payload,comparison_hash=digest(payload),original=original,result=result))

    def select(self,context,contract_id,item_index,allocations,comparison_hash,note,reviewed,request_key):
        if reviewed is not True or not isinstance(note,str) or len(note)>2000:raise DomainError('INVALID_INPUT','전체 확인과 선택 메모 형식을 확인하세요.')
        response=self.preview(context,contract_id,item_index,allocations)
        if not response['ok']:return response
        data=response['data']
        if data['comparison_hash']!=comparison_hash:raise DomainError('STALE_REVISION','대체 검토나 재고가 바뀌었습니다. 다시 계산하세요.')
        payload=dict(data['payload'],note=note.strip() or ('대체 수량 적용' if allocations else '대체 없이 기존 계산 유지'),technical_basis='; '.join(r['payload']['technical_basis'] for r in data['payload']['reports']),contract_basis='; '.join(r['payload']['contract_basis'] for r in data['payload']['reports']),production_authorized=False)
        material=self.materials.get(context,contract_id,item_index)['data']['saved']
        with self.host.request(context) as session:
            svc=session._service
            def save():
                self.assert_base(svc,contract_id,item_index,material)
                if not self.current(svc,dict(payload=payload),material):raise DomainError('STALE_REVISION','대체 검토가 바뀌었습니다.')
                previous=self.latest(svc,contract_id,item_index)
                if previous and previous['payload']==payload:return svc._envelope(ok=True,data=previous)
                row=dict(id=str(uuid4()),contract_id=contract_id,item_index=item_index,payload=payload,content_hash=digest(payload),actor=session.context['actor'],created_at=svc.now())
                svc.db.execute('INSERT INTO pp_contract_material_choices VALUES(%s,%s,%s,%s,%s,%s,%s)',(row['id'],contract_id,item_index,canonical(payload),row['content_hash'],row['actor'],row['created_at']))
                return svc._envelope(ok=True,data=row)
            return svc._run(session.context['actor'],'component_replacement_select',dict(contract_id=contract_id,item_index=item_index,payload=payload),save,permission='review',request_key=request_key)
