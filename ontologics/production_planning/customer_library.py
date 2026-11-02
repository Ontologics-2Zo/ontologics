"""A-owned Customer -> ContractBuyer relations with evidence and version bindings.

These are local business entities, not additions to the shared graph schema.
Name matching never establishes verified legal identity or human approval.
"""
import re
import unicodedata
from uuid import uuid4
from ..model import canonical, digest
from ..service import DomainError
from .contract_library import bounded
from .contract_readiness import preview_fields

DDL='''CREATE TABLE IF NOT EXISTS pp_customers(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, normalized_name TEXT NOT NULL,
 registration_number TEXT UNIQUE, identity_level TEXT NOT NULL,
 provenance JSONB NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS pp_customers_name_idx ON pp_customers(normalized_name);
CREATE TABLE IF NOT EXISTS pp_contract_buyers(
 sequence BIGSERIAL PRIMARY KEY, id TEXT NOT NULL UNIQUE,
 contract_id TEXT NOT NULL REFERENCES pp_contract_originals(id),
 customer_id TEXT NOT NULL REFERENCES pp_customers(id), role TEXT NOT NULL CHECK(role='buyer'),
 basis_hash TEXT NOT NULL, basis JSONB NOT NULL, method TEXT NOT NULL,
 actor TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS pp_contract_buyers_contract_idx ON pp_contract_buyers(contract_id,sequence DESC)'''


def normalized_name(name):
    return ' '.join(unicodedata.normalize('NFKC',name).split()).casefold()


def buyer_claim(row):
    if not row or row.get('status','needs_review')!='needs_review':
        return dict(name=None,registration_number=None,source='unavailable',reason='해석이 완료되면 구매자를 확인합니다.',basis={})
    items,check=preview_fields(row)
    names=[];numbers=[];evidence=[];held=False;invalid_number=False
    for index,item in enumerate(items):
        f=item['fields'].get('customer',{})
        raw=row['result']['items'][index]['fields'].get('customer',{})
        # A missing line-item buyer may inherit the document buyer; an explicit
        # conflict or human hold must not be overwritten by another item.
        if f.get('state') in ('conflict','invalid','unsupported') or (f.get('state')=='unresolved' and (raw.get('state')!='missing' or f.get('reason'))):held=True
        if f.get('value'):
            names.append(f['value']);evidence.append(dict(item_index=index,source=f['source'],claims=raw.get('claims',[]),reason=f.get('reason','')))
        n=item['fields'].get('customer_registration_number',{})
        raw_number=row['result']['items'][index]['fields'].get('customer_registration_number',{})
        if n.get('state') in ('conflict','invalid','unsupported') or (n.get('state')=='unresolved' and (raw_number.get('state') not in (None,'missing') or n.get('reason'))):invalid_number=True
        if n.get('value'):
            number=str(n['value']).strip()
            if not re.fullmatch(r'(?:[0-9]{10}|[0-9]{3}-[0-9]{2}-[0-9]{5})',number):invalid_number=True
            else:numbers.append(number.replace('-',''))
    unique={normalized_name(n) for n in names}
    valid=len(unique)==1 and not held and not invalid_number and len(set(numbers))<=1
    basis=dict(interpretation_id=row['id'],source_sha256=row['source_sha256'],result_sha256=row['result_sha256'],
               check_id=check['id'] if check else None,rule_version='customer-link-1')
    basis['buyer_fingerprint']=digest(dict(names=sorted(unique),numbers=sorted(set(numbers)),valid=valid,evidence=[{k:v for k,v in e.items() if k!='source'} for e in evidence]))
    return dict(name=names[0] if valid else None,registration_number=numbers[0] if valid and numbers else None,
                source='human_correction' if any(e['source']=='human_correction' for e in evidence) else 'source_validated',
                reason='' if valid else '이 계약의 구매자는 어느 회사인가요? 원문 구매자와 식별 정보를 확인해 주세요.',
                evidence=evidence,basis=basis)


def choose_customer(claim,customers):
    if not claim['name']:return dict(state='needs_buyer',customer_id=None,candidates=[])
    name=normalized_name(claim['name']);number=claim.get('registration_number')
    by_name=[c for c in customers if normalized_name(c['name'])==name]
    if number:
        exact=[c for c in customers if c.get('registration_number')==number]
        if exact:
            c=exact[0]
            return dict(state='matched' if normalized_name(c['name'])==name else 'conflict',customer_id=c['id'] if normalized_name(c['name'])==name else None,candidates=exact,method='registration_number')
        # Do not merge a new tax ID into an existing name-only entity.
        return dict(state='create',customer_id=None,candidates=[],method='registration_number')
    return dict(state='matched' if len(by_name)==1 else 'ambiguous' if by_name else 'create',
                customer_id=by_name[0]['id'] if len(by_name)==1 else None,candidates=by_name,method='exact_name')


class CustomerLibrary:
    def __init__(self,review,context):
        self.review=review;self.host=review.host
        with self.host.request(context) as session:session._service.db.execute(DDL)

    def inspect(self,context,contract_id):
        return self._resolve(context,contract_id)

    def sync(self,context,contract_id,request_key):
        return self._resolve(context,contract_id,write=True,request_key=request_key)

    def select(self,context,contract_id,customer_id,basis_hash,request_key):
        return self._resolve(context,contract_id,write=True,selected=bounded(customer_id,100),expected_basis=bounded(basis_hash,64),request_key=request_key)

    def _resolve(self,context,contract_id,*,write=False,selected=None,expected_basis=None,request_key=None):
        contract_id=bounded(contract_id,100)
        original=self.review.library.get(context,contract_id)
        if not original['ok']:return original
        result=self.review.latest(context,contract_id)
        if not result['ok']:return result
        row=result['data'];claim=buyer_claim(row);basis_hash=digest(claim['basis'])
        if row and row['source_sha256']!=original['data']['sha256']:
            raise DomainError('SOURCE_HASH_MISMATCH','구매자 해석의 원본이 변경되었습니다.')
        with self.host.request(context) as session:
            svc=session._service
            def run():
                if write:
                    svc.db.execute('SELECT pg_advisory_xact_lock(76321001)')
                    svc.db.execute('SELECT id FROM pp_contract_originals WHERE id=%s FOR UPDATE',(contract_id,))
                latest=svc.db.execute('SELECT * FROM pp_contract_interpretations WHERE contract_id=%s ORDER BY created_at DESC,id DESC LIMIT 1',(contract_id,)).fetchone()
                if latest:
                    latest['checks']=svc.db.execute('SELECT * FROM pp_contract_reading_checks WHERE interpretation_id=%s ORDER BY checked_at,id',(latest['id'],)).fetchall()
                    if latest['result'] is not None and digest(latest['result'])!=latest['result_sha256']:
                        raise DomainError('RESULT_INTEGRITY','구매자 해석의 무결성을 확인할 수 없습니다.')
                if digest(buyer_claim(latest)['basis'])!=basis_hash or (expected_basis and expected_basis!=basis_hash):
                    raise DomainError('STALE_REVISION','구매자 해석 또는 확인 내용이 바뀌었습니다. 다시 확인해 주세요.')
                customers=svc.db.execute('SELECT * FROM pp_customers ORDER BY created_at,id').fetchall()
                decision=choose_customer(claim,customers)
                existing=svc.db.execute('SELECT * FROM pp_contract_buyers WHERE contract_id=%s ORDER BY sequence DESC LIMIT 1',(contract_id,)).fetchone()
                same_buyer=bool(existing and existing['method']=='human_selection' and claim['name'] and
                    existing['basis'].get('source_sha256')==claim['basis'].get('source_sha256') and
                    existing['basis'].get('buyer_fingerprint')==claim['basis'].get('buyer_fingerprint'))
                valid=bool(existing and claim['name'] and (existing['basis_hash']==basis_hash or same_buyer))
                chosen=next((c for c in customers if c['id']==existing['customer_id']),None) if valid else None
                # A prior automatic name match becomes ambiguous when another same-name
                # customer is introduced. A deliberate choice remains valid for this basis.
                if valid and existing['method']!='human_selection' and decision['state'] in ('ambiguous','conflict'):
                    valid=False;chosen=None
                if write and valid and same_buyer and existing['basis_hash']!=basis_hash:
                    svc.db.execute('INSERT INTO pp_contract_buyers(id,contract_id,customer_id,role,basis_hash,basis,method,actor,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                        (str(uuid4()),contract_id,chosen['id'],'buyer',basis_hash,canonical(dict(claim['basis'],evidence=claim['evidence'],inherited_selection=existing['id'])),'human_selection',existing['actor'],svc.now()))
                    existing=svc.db.execute('SELECT * FROM pp_contract_buyers WHERE contract_id=%s ORDER BY sequence DESC LIMIT 1',(contract_id,)).fetchone()
                if selected:
                    if decision['state']!='ambiguous' or selected not in {c['id'] for c in decision['candidates']}:
                        raise DomainError('CUSTOMER_SELECTION_INVALID','현재 동명 고객 후보 중에서 선택해 주세요.')
                    chosen=next(c for c in customers if c['id']==selected);valid=False
                if write and not valid and claim['name']:
                    if decision['state']=='create':
                        chosen=dict(id='customer-'+str(uuid4()),name=claim['name'],normalized_name=normalized_name(claim['name']),
                                    registration_number=claim['registration_number'],identity_level='registration_stated' if claim['registration_number'] else 'name_only',
                                    provenance=dict(contract_id=contract_id,basis=claim['basis'],evidence=claim['evidence']),created_at=svc.now())
                        svc.db.execute('INSERT INTO pp_customers VALUES(%s,%s,%s,%s,%s,%s,%s)',
                            (chosen['id'],chosen['name'],chosen['normalized_name'],chosen['registration_number'],chosen['identity_level'],canonical(chosen['provenance']),chosen['created_at']))
                    elif decision['state']=='matched':chosen=next(c for c in customers if c['id']==decision['customer_id'])
                    if chosen:
                        method='human_selection' if selected else decision['method']
                        svc.db.execute('INSERT INTO pp_contract_buyers(id,contract_id,customer_id,role,basis_hash,basis,method,actor,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                            (str(uuid4()),contract_id,chosen['id'],'buyer',basis_hash,canonical(dict(claim['basis'],evidence=claim['evidence'])),method,session.context['actor'],svc.now()))
                        existing=svc.db.execute('SELECT * FROM pp_contract_buyers WHERE contract_id=%s ORDER BY sequence DESC LIMIT 1',(contract_id,)).fetchone();valid=True
                state='linked' if valid else decision['state']
                question=claim['reason'] if not claim['name'] else '같은 이름의 고객이 여러 곳입니다. 이 계약의 구매자를 선택해 주세요.' if state=='ambiguous' else '사업자번호와 회사명이 기존 고객 정보와 다릅니다. 계약의 구매자 정보를 확인해 주세요.' if state=='conflict' else ''
                return svc._envelope(ok=True,data=dict(state=state,buyer_name=claim['name'],customer=chosen if valid else None,
                    candidates=decision['candidates'] if state=='ambiguous' else [],question=question,basis_hash=basis_hash,
                    provenance=claim,link=existing if valid else None,previous_link_invalidated=bool(existing and not valid),
                    identity_verified=False))
            args=dict(contract_id=contract_id,basis_hash=basis_hash,selected=selected)
            return svc._run(session.context['actor'],'contract_customer_select' if selected else 'contract_customer_sync' if write else 'contract_customer_get',args,run,
                            **(dict(permission='review' if selected else 'draft',request_key=request_key) if write else {}))

    def contracts(self,context,customer_id):
        customer_id=bounded(customer_id,100)
        # Candidate relations are checked against current interpretation/checks below.
        with self.host.request(context) as session:
            svc=session._service
            def read():
                rows=svc.db.execute('SELECT DISTINCT contract_id FROM pp_contract_buyers WHERE customer_id=%s',(customer_id,)).fetchall()
                return svc._envelope(ok=True,data=rows)
            result=svc._run(session.context['actor'],'customer_contract_candidates',dict(customer_id=customer_id),read)
        if not result['ok']:return result
        contracts=[]
        for row in result['data']:
            linked=self.inspect(context,row['contract_id'])
            if not linked['ok']:return linked
            if linked['data']['customer'] and linked['data']['customer']['id']==customer_id:
                original=self.review.library.get(context,row['contract_id'])
                if not original['ok']:return original
                contracts.append(dict(id=row['contract_id'],title=original['data']['title']))
        return dict(result,data=contracts)

    def search_contract_ids(self,context,query):
        query=bounded(query,200,False)
        if not query:return []
        pattern='%'+query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        with self.host.request(context) as session:
            svc=session._service
            def read():
                return svc._envelope(ok=True,data=svc.db.execute('SELECT id FROM pp_customers WHERE name ILIKE %s',(pattern,)).fetchall())
            result=svc._run(session.context['actor'],'customer_search',dict(query=query),read)
        if not result['ok']:raise DomainError('CUSTOMER_QUERY_FAILED','고객 조회에 실패했습니다.')
        ids=set()
        for customer in result['data']:
            linked=self.contracts(context,customer['id'])
            if not linked['ok']:raise DomainError('CUSTOMER_QUERY_FAILED','고객 계약 조회에 실패했습니다.')
            ids.update(c['id'] for c in linked['data'])
        return sorted(ids)
