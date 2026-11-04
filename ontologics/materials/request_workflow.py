"""User-triggered graph request inbox and unissued procurement drafts.

Only A's local review journal is written. Common graph tools and the existing
company stock calculators remain the source of facts and arithmetic.
"""
from collections import defaultdict
from decimal import Decimal
from ..model import ROOT, canonical, digest
import hashlib
from ..service import DomainError
from ..order_fulfillment.material_request_document import KIND, validate_request
from ..order_fulfillment.material_request_registration import read_request
from .materials import calculate_material_requirements
from .library_process_rfq import calculate_automatic


def checked(response):
    if not response['ok']:
        e=response['errors'][0]
        raise DomainError(e['code'],e['message'])
    return response


def select_latest(rows):
    groups=defaultdict(list)
    for row in rows: groups[row['request']['request_id']].append(row)
    result=[]
    for group in groups.values():
        revision=max(r['request']['revision'] for r in group)
        latest=[r for r in group if r['request']['revision']==revision]
        if len(latest)>1:
            for row in latest: row.setdefault('issues',[]).append('같은 요청 개정이 여러 건입니다. 요청 식별을 보완하세요.')
        result.extend(latest)
    return sorted(result,key=lambda r:r['request']['request_id'])


def rfq_document(request,bom,process):
    lines=[];excluded=[]
    for r in bom['components']:
        row=dict(kind='bom',id=r['component_id'],name=r.get('name',r['component_id']),
                 required=r['D'],available=r['A'],shortage=r['S'],unit='piece',basis='등록 BOM × 요청 생산 수량')
        if r['S'] is None: excluded.append(dict(row,reason='BOM 또는 재고 미확인'))
        elif r['S']>0: lines.append(row)
    for r in process:
        row=dict(kind='process',id=r['material_id'],name=r['name'],required=r['required'],
                 available=r['available'],shortage=r['shortage'],unit=r['unit'],basis=r.get('basis'))
        if r['shortage'] is None: excluded.append(dict(row,reason='사용량 기준 미등록 또는 재고 미확인'))
        elif Decimal(r['shortage'])>0: lines.append(row)
    basis={'production_complete':'생산 완료','dispatch':'출고','customer_arrival':'고객 도착'}
    words=['자재 견적요청서 (RFQ) — 미발송 초안',
           f"요청: {request['request_id']} / 개정 {request['revision']}",
           f"제품: {request['configuration_id']} / 추가 생산 검토 {request['quantity']}대",
           f"계약 품목: {request.get('contract_item_id','미연결')}",
           f"납품 차수: {request.get('delivery_round_id','미지정')}",
           f"기존 계획 근거: {request.get('baseline_evidence_id','미연결')}",
           f"요청 기한: {request['need_date']} ({basis[request['need_basis']]})",
           '자재 희망 입고일: 구매팀과 협의 필요 (요청 기한과 다름)',
           '공급사·납품처·MOQ·포장 단위·가격: 구매팀 확인 필요',
           f"조건: {request['conditions_note']}",f"자료 구분: {request['origin']}",'',
           '견적 문의 수량 (확인된 부족량; 발주 수량 아님):']
    words += [f"- {r['name']}: {r['shortage']} {r['unit']} (필요 {r['required']} − 가용 {r['available']}); {r['basis']}" for r in lines]
    words += ['',f"미확인으로 제외: {len(excluded)}종",'공정 수량은 조건부 추정이며 초기 장비 충전량은 미포함입니다.',
              '각 요청은 현재 가용 재고로 개별 검토했습니다. 여러 초안의 수량을 합산해 발주하지 마세요.',
              '재고 예약·사용 차감·발주·외부 발송 없음. 구매 검토 후 조건과 수량을 확정하세요.']
    return dict(status='draft_not_sent' if lines else 'no_confirmed_shortage',lines=lines,excluded=excluded,
                supplier=None,desired_arrival_date=None,sent=False,stock_reserved=False,text='\n'.join(words)+'\n')


def contract_context(contract,item,index):
    """Follow approved contract evidence; show only hash-verified local originals."""
    documents={};evidence=[]
    for eid in dict.fromkeys((contract or {}).get('evidence',[])+(item or {}).get('evidence',[])):
        ev=index.get(eid)
        if not ev or ev.get('type')!='Evidence':continue
        evidence.append(ev)
        source=index.get(ev.get('source_id'))
        if not source or source.get('type')!='SourceDocument' or source['id'] in documents:continue
        doc=dict(source=source,text=None,status='unavailable')
        raw=source.get('path','')
        # Historical approved paths precede the materials rename. Keep the graph
        # metadata immutable; accept the relocated bytes only with the same hash.
        paths=[raw]
        if raw.startswith('data/production_planning/'):
            paths.append('data/materials/'+raw[len('data/production_planning/'):])
        for relative in paths:
            path=(ROOT/relative).resolve()
            if not path.is_relative_to((ROOT/'data').resolve()) or not path.is_file():continue
            if path.stat().st_size>600000:continue
            content=path.read_bytes()
            if hashlib.sha256(content).hexdigest()!=source.get('sha256'):
                doc['status']='hash_mismatch';continue
            try:doc.update(text=content.decode('utf-8'),status='verified')
            except UnicodeDecodeError:doc['status']='binary_original'
            break
        documents[source['id']]=doc
    return dict(contract=contract,item=item,evidence=evidence,documents=list(documents.values()))


class RequestWorkflow:
    def __init__(self,materials,process_rfq,context,source_host=None):
        self.materials=materials;self.process_rfq=process_rfq;self.host=materials.host
        self.source_host=source_host or self.host
        with self.host.request(context) as s:
            s._service.db.execute('''CREATE TABLE IF NOT EXISTS pp_request_reviews(
                id TEXT PRIMARY KEY, source_store TEXT NOT NULL, evidence_id TEXT NOT NULL,
                basis_hash TEXT NOT NULL, payload JSONB NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL)''')
            s._service.db.execute('''CREATE TABLE IF NOT EXISTS pp_request_seen(
                source_store TEXT NOT NULL,evidence_id TEXT NOT NULL,actor TEXT NOT NULL,seen_at TEXT NOT NULL,
                PRIMARY KEY(source_store,evidence_id,actor))''')

    def discover(self,session):
        snap=checked(session.call('lookup',{}));nodes=snap['data'];version=snap['data_version']
        idx={n['id']:n for n in nodes};info=session._service.db.info
        store=digest([info.host,info.port,info.dbname,session._service.schema])
        sources={n['id'] for n in nodes if n.get('type')=='SourceDocument' and n.get('kind')==KIND}
        rows=[];invalid=[]
        for n in nodes:
            if n.get('type')!='Evidence' or n.get('source_id') not in sources:continue
            row=None
            try:
                data=read_request(session,n['id'],version=version);record=data['record'];p=record['request']
                row=dict(evidence_id=n['id'],source_id=n['source_id'],request=p,issues=[],record=record,
                         source_hash=data['source']['sha256'])
                if record['provenance']['store_id']!=store:
                    row['issues'].append('요청의 원본 저장소와 조회 저장소가 다릅니다. 이관 근거 확인 필요.')
                # Validate the original registration references, not against its own later publication.
                original=checked(session.call('lookup',{'version':record['provenance']['reference_data_version']}))
                validate_request(p,original['data'])
                item=idx.get(p.get('contract_item_id'));contract=idx.get(item.get('contract_id')) if item else None
                if not item or item.get('type')!='ContractItem' or not contract or contract.get('type')!='Contract':
                    row['issues'].append('기존 계약 품목 연결이 없습니다. order_fulfillment에서 요청을 보완하세요.')
                elif item.get('configuration_id')!=p['configuration_id'] or any(x.get('origin')!=p['origin'] for x in (item,contract)):
                    row['issues'].append('계약·제품 또는 실제/가상 구분이 요청과 다릅니다.')
                row['contract']=contract;row['contract_item']=item
                row['contract_context']=contract_context(contract,item,idx)
                for ref in ('configuration_id','baseline_evidence_id','contract_item_id','delivery_round_id'):
                    if p.get(ref) and idx.get(p[ref])!={x['id']:x for x in original['data']}.get(p[ref]):
                        row['issues'].append(ref+'의 내용이 등록 당시와 달라 재확인이 필요합니다.')
                if p['conditions_state']=='changed':row['issues'].append('변경된 구성·대체품·공정 조건을 먼저 검토하세요.')
                if p['quantity']>10000000:row['issues'].append('지원 생산 수량 범위를 초과했습니다.')
                rows.append(row)
            except (ValueError,KeyError,TypeError) as exc:
                message='요청 형식·근거·참조 검증 실패: '+str(exc)
                if row is not None:
                    row['issues'].append(message);rows.append(row)
                else:invalid.append(dict(evidence_id=n['id'],message=message))
        return dict(source_store=store,data_version=version,requests=select_latest(rows),invalid=invalid,
                    contracts=[n for n in nodes if n.get('type')=='Contract'])

    def inbox(self,context):
        with self.source_host.request(context) as source:
            data=self.discover(source)
        with self.host.request(context) as s:
            svc=s._service
            def read():
                for row in data['requests']:
                    seen=svc.db.execute('SELECT seen_at FROM pp_request_seen WHERE source_store=%s AND evidence_id=%s AND actor=%s',
                        (data['source_store'],row['evidence_id'],s.context['actor'])).fetchone()
                    saved=svc.db.execute('SELECT * FROM pp_request_reviews WHERE source_store=%s AND evidence_id=%s ORDER BY created_at DESC,id DESC LIMIT 1',
                        (data['source_store'],row['evidence_id'])).fetchone()
                    row.update(seen_at=seen['seen_at'] if seen else None,last_review=saved,
                               status='needs_info' if row['issues'] else 'reviewed' if saved else 'seen' if seen else 'new')
                    row.pop('record',None)
                return svc._envelope(ok=True,data=data)
            return svc._run(s.context['actor'],'materials_request_inbox',{},read)

    def seen(self,context,evidence_id):
        with self.source_host.request(context) as source:data=self.discover(source)
        if evidence_id not in {r['evidence_id'] for r in data['requests']}:
            raise DomainError('STALE_REQUEST','최신 요청 목록을 다시 확인하세요.')
        with self.host.request(context) as s:
            svc=s._service
            def save():
                svc.db.execute('INSERT INTO pp_request_seen VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                    (data['source_store'],evidence_id,s.context['actor'],svc.now()))
                return svc._envelope(ok=True,data=dict(seen=True,reviewed=False))
            return svc._run(s.context['actor'],'materials_request_seen',dict(evidence_id=evidence_id),save)

    def review(self,context,evidence_id,data_version,allowance_percent,request_key):
        # A owns the request budget; source reads inherit it without replacing policy.
        with self.host.request(context) as s:
            with self.source_host.request(context) as source:
                data=self.discover(source)
                if data_version!=data['data_version']:
                    raise DomainError('STALE_REQUEST','그래프가 변경되었습니다. 새 요청을 다시 확인하세요.')
                matches=[r for r in data['requests'] if r['evidence_id']==evidence_id]
                if len(matches)!=1:raise DomainError('STALE_REQUEST','최신 개정 요청을 선택하세요.')
                if data['invalid']:raise DomainError('REQUEST_INTEGRITY','식별할 수 없는 요청 기록이 있습니다. 최신 개정 여부를 확인한 뒤 검토하세요.')
                row=matches[0];p=row['request']
                if row['issues']:raise DomainError('REQUEST_NEEDS_INFO',' / '.join(row['issues']))
                detail=checked(source.call('configuration',dict(configuration_id=p['configuration_id'],version=data_version)))['data']
            svc=s._service
            def save():
                stock=self.materials.stock.calculate(self.materials.stock.latest(svc,p['configuration_id']),svc)
                bom=calculate_material_requirements(detail['items'],stock,p['quantity'],configuration_id=p['configuration_id'],
                    data_version=data_version,demand_source=dict(request=p,evidence_id=evidence_id,source_hash=row['source_hash']),bom_complete=True)
                names={n['id']:n['name'] for n in detail['components']}
                for r in bom['components']:r['name']=names.get(r['component_id'],r['component_id'])
                process,basis=self.process_rfq.current(svc,p['configuration_id'],p['quantity'])
                company=row['contract'].get('company')
                scopes=[stock.get('scope'),process.get('stock_scope')]
                if not company or any(scope and scope.get('company')!=company for scope in scopes):
                    raise DomainError('STOCK_COMPANY_MISMATCH','계약 회사와 재고 소유 회사가 다릅니다. 회사 재고 연결을 확인하세요.')
                try:lines=calculate_automatic(process,allowance_percent)
                except ValueError as exc:raise DomainError('INVALID_INPUT',str(exc)) from None
                if p['origin']=='real' and (stock.get('origin')=='synthetic' or (process.get('stock_scope') or {}).get('origin')=='synthetic'):
                    raise DomainError('ORIGIN_MISMATCH','실제 요청에 가상 재고를 적용할 수 없습니다.')
                fingerprint=digest(dict(source=row,version=data_version,bom=bom,process=lines,process_basis=basis,allowance=allowance_percent))
                doc=rfq_document(p,bom,lines)
                doc['text']+=f"기존 계약: {row['contract']['id']} / 고객: {row['contract'].get('customer_name','')} / 기존 계약 품목 수량: {row['contract_item']['quantity']}대 (이번 추가 검토량과 별도)\n"
                doc['text']+=f"요청 근거: {evidence_id}\n원문 SHA-256: {row['source_hash']}\n그래프 버전: {data_version}\n재고·계산 기준: {fingerprint}\n"
                for label,scope in zip(('BOM','공정'),scopes):
                    if scope:doc['text']+=f"{label} 재고 범위: {scope['company']} / {scope['location']} / {scope['as_of']}\n"
                payload=dict(request=p,evidence_id=evidence_id,source_hash=row['source_hash'],source_store=data['source_store'],
                    data_version=data_version,contract=row['contract'],contract_context=row['contract_context'],stock_scopes=dict(bom=stock.get('scope'),process=process.get('stock_scope')),bom=bom,process=lines,allowance_percent=allowance_percent,
                    rfq=doc,basis_hash=fingerprint,evaluated_at=svc.now(),human_confirmed=False,stock_reserved=False)
                id=digest([data['source_store'],evidence_id,fingerprint])
                svc.db.execute('INSERT INTO pp_request_reviews VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                    (id,data['source_store'],evidence_id,fingerprint,canonical(payload),s.context['actor'],svc.now()))
                saved=svc.db.execute('SELECT * FROM pp_request_reviews WHERE id=%s',(id,)).fetchone()
                return svc._envelope(ok=True,data=saved)
            return svc._run(s.context['actor'],'materials_request_rfq_draft',dict(evidence_id=evidence_id,data_version=data_version,
                allowance_percent=allowance_percent,source_store=data['source_store']),save,permission='review',request_key=request_key)
