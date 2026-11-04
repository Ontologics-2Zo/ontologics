"""Reviewed contract requirements and version-bound public catalog matching."""
from ..model import digest,canonical
from ..service import DomainError
from .configuration_lookup import lookup_contract_configurations
from .contract_readiness import preview_fields, evaluate_contract_readiness

IDENTITY=('manufacturer','product_model','revision','variant')

def reviewed_items(row):
    checks=[c for c in row.get('checks',[]) if c.get('decisions')]
    check=checks[-1] if checks else None
    items=[]
    for i,item in enumerate(row['result']['items']):
        fields={}
        for name,f in item['fields'].items():
            decision=check['decisions'].get(f'{i}:{name}') if check else None
            state=decision['state'] if decision else 'unresolved'
            fields[name]={'value':decision['value'] if decision and state in ('accepted','corrected') else None,
                          'state':state,'source':'human_correction' if state=='corrected' else 'reviewed_extraction' if state=='accepted' else 'unconfirmed',
                          'original_value':f.get('value'),'reason':decision.get('reason','') if decision else ''}
        items.append({'fields':fields})
    return items,check

class LibraryProductReview:
    def __init__(self,review,context):
        self.review=review;self.host=review.host
        with self.host.request(context) as session:
            session._service.db.execute('''CREATE TABLE IF NOT EXISTS pp_contract_configuration_links(
                id TEXT PRIMARY KEY,contract_id TEXT NOT NULL,interpretation_id TEXT NOT NULL,
                review_id TEXT NOT NULL,item_index INTEGER NOT NULL,configuration_id TEXT NOT NULL,
                data_version INTEGER NOT NULL,actor TEXT NOT NULL,created_at TEXT NOT NULL)''')

    def inspect_preview(self,context,contract_id):
        return self.inspect(context,contract_id,_preview=True)

    def inspect(self,context,contract_id,*,_preview=False):
        original=self.review.library.get(context,contract_id)
        if not original['ok']:return original
        latest=self.review.latest(context,contract_id)
        if not latest['ok']:return latest
        row=latest['data']
        with self.host.request(context) as session:
            svc=session._service
            snapshot=session.call('lookup',{})
            if not snapshot['ok']:return snapshot
            version=snapshot['data_version']
            query=lookup_contract_configurations(session,data_version=version)
            if query['configurations'] is None:raise DomainError('CATALOG_QUERY_FAILED','제품 조회가 실패했습니다. 데이터 없음과는 다른 오류입니다.')
            catalog=query['configurations']['candidates']
            nodes={n['id']:n for n in query['lookup_response']['data']}
            def read():
                base={'contract_id':contract_id,'title':original['data']['title'],'items':[],'status':'review_required'}
                if not row or row['status']!='needs_review':
                    if _preview:base['status']=row['status'] if row else 'not_started'
                    return svc._envelope(ok=True,data=base)
                if row['source_sha256']!=original['data']['sha256']:raise DomainError('SOURCE_HASH_MISMATCH','원본이 변경되었습니다.')
                items,check=(preview_fields(row) if _preview else reviewed_items(row))
                base.update(items=items,interpretation_id=row['id'],review_id=check['id'] if check else None)
                if not check and not _preview:return svc._envelope(ok=True,data=base)
                if _preview:base.update(result_sha256=row['result_sha256'],source_sha256=row['source_sha256'])
                base.update(status='catalog_empty' if not catalog else 'ready',data_version=version,catalog_count=len(catalog))
                for i,item in enumerate(items):
                    fields=item['fields'];item['missing']=[k for k in IDENTITY if not fields.get(k,{}).get('value')]
                    item['candidates']=[]
                    for detail in catalog:
                        c,r,p=(detail[k] for k in ('configuration','revision','product'))
                        values=dict(manufacturer=p['manufacturer'],product_model=p['name'],revision=r['revision'],variant=c['variant'])
                        comparison={k:{'contract':fields.get(k,{}).get('value'),'catalog':v,'matches':fields.get(k,{}).get('value')==v} for k,v in values.items()}
                        exact=all(v['matches'] for v in comparison.values())
                        if fields.get('configuration_id',{}).get('value') and fields['configuration_id']['value']!=c['id']:exact=False
                        item['candidates'].append({'configuration_id':c['id'],'name':c.get('name',c['id']),'comparison':comparison,'exact':exact,'evidence':c.get('evidence',[]),'bom_count':len(detail['items']),'identity_notes':c.get('identity_notes',''),'sources':[{'description':nodes[e].get('description',''),'locator':nodes[e].get('locator',''),'source':nodes.get(nodes[e].get('source_id'),{}).get('path','')} for e in c.get('evidence',[]) if e in nodes]})
                    links=svc.db.execute('SELECT * FROM pp_contract_configuration_links WHERE contract_id=%s AND item_index=%s ORDER BY created_at DESC LIMIT 1',(contract_id,i)).fetchone()
                    item['link']=links
                    item['link_current']=bool(links and check and links['review_id']==check['id'] and links['interpretation_id']==row['id'] and links['data_version']==version)
                    if _preview:
                        basis=dict(source_sha256=row['source_sha256'],interpretation_id=row['id'],result_sha256=row['result_sha256'],check_id=check['id'] if check else None,data_version=version)
                        item['readiness']=evaluate_contract_readiness(fields,item['candidates'],basis=basis)
                        item['readiness']['basis']['configuration_id']=item['readiness']['configuration_id']
                        item['basis_hash']=digest(item['readiness']['basis'])
                return svc._envelope(ok=True,data=base)
            return svc._run(session.context['actor'],'contract_planning_inspect',{'contract_id':contract_id},read)

    def connect(self,context,contract_id,interpretation_id,review_id,data_version,item_index,configuration_id,request_key):
        current=self.inspect(context,contract_id)
        if not current['ok']:return current
        data=current['data']
        if any(data.get(k)!=v for k,v in [('interpretation_id',interpretation_id),('review_id',review_id),('data_version',data_version)]):raise DomainError('STALE_REVISION','계약 검토 또는 제품 데이터가 바뀌었습니다. 다시 조회하세요.')
        if type(item_index) is not int or not 0<=item_index<len(data['items']):raise DomainError('INVALID_INPUT','계약 항목을 확인하세요.')
        item=data['items'][item_index]
        if not any(c['configuration_id']==configuration_id and c['exact'] for c in item['candidates']):raise DomainError('CONFIGURATION_MISMATCH','확인된 계약 조건과 모두 일치하는 구성만 연결할 수 있습니다.')
        binding=dict(contract_id=contract_id,interpretation_id=interpretation_id,review_id=review_id,data_version=data_version,item_index=item_index,configuration_id=configuration_id)
        with self.host.request(context) as session:
            svc=session._service
            def save():
                # Immutable binding: later review/catalog changes make this link stale on read.
                svc.db.execute('INSERT INTO pp_contract_configuration_links VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING',
                    (digest(binding),contract_id,interpretation_id,review_id,item_index,configuration_id,data_version,session.context['actor'],svc.now()))
                return svc._envelope(ok=True,data=binding)
            return svc._run(session.context['actor'],'contract_configuration_connect',binding,save,permission='review',request_key=request_key)
