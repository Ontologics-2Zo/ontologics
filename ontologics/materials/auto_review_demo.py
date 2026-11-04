"""Explicit local UI test fixtures; deterministic extraction, no model API calls.

Run only against a local development contract library. Existing originals and
human checks are retained. No production, stock, or purchase records are made.
"""
import argparse
import base64
import json
from ..database import DatabaseSettings
from ..host import CommonHost
from ..identity import DevelopmentContext, DevelopmentIdentityProvider
from ..web import USERS
from .contract_library import ContractLibrary
from .library_review import LibraryReview, REGISTRATION


def seed(settings):
    host=CommonHost(DevelopmentIdentityProvider(USERS,enabled=True),settings,agent_registrations=(REGISTRATION,))
    context=DevelopmentContext('dev-alice')
    library=ContractLibrary(host,context)
    results=[]
    for scenario,title in [('normal','01 정상 · 확인 전 총소요량'),('normal-fresh','04 직접 테스트 · 확인 기록 없는 정상 계약'),('missing','02 개정 누락 · 필요한 질문'),('conflict','03 수량 충돌 · 자동 계산 보류')]:
        pairs=[('manufacturer','OLIMEX'),('product_model','ESP32-POE'),('revision','L'),('variant','WROOM'),('ordered_quantity',100),('unit','대')]
        if scenario=='missing':pairs=[p for p in pairs if p[0]!='revision']
        if scenario=='conflict':pairs.append(('ordered_quantity',120))
        lines=['자동 검토 웹 테스트용 가정 자료 — 실제 계약·주문이 아님. 시나리오: '+scenario]
        fields={}
        for name,value in pairs:
            lines.append(f'{name}: {value}')
            fields.setdefault(name,[]).append(dict(value=value,evidence=[dict(segment_id=f'text:line:{len(lines)}',quote=str(value),occurrence=0)]))
        raw=('\n'.join(lines)+'\n').encode()
        saved=library.register(context,title='[자동 검토 테스트] '+title,counterparty='가상 고객 · 실거래 아님',filename='auto-review-'+scenario+'.txt',content_base64=base64.b64encode(raw).decode(),request_key='auto-review-demo-register-1:'+scenario)
        if not saved['ok']:raise RuntimeError('Test original registration failed')
        cid=saved['data']['id']
        def provider(fields=fields,**kwargs):return dict(text=json.dumps({'items':[{'fields':fields}]},ensure_ascii=False),tool_calls=[])
        review=LibraryReview(library,context,lambda provider=provider:provider,'deterministic-ui-fixture')
        latest=review.latest(context,cid)
        if not latest['ok']:raise RuntimeError('Test interpretation lookup failed')
        if latest['data'] is None:
            result=review.interpret(context,cid,'auto-review-demo-interpret-1:'+scenario)
            if not result['ok'] or result['data']['status']!='needs_review':raise RuntimeError('Test interpretation failed')
        results.append(dict(scenario=scenario,contract_id=cid,title=title))
    return results


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--schema',default='a_contract_library')
    parser.add_argument('--local-test-fixtures',action='store_true',required=True)
    parser.add_argument('--customer-scenarios',action='store_true')
    parser.add_argument('--policy-scenarios',action='store_true')
    args=parser.parse_args()
    settings=DatabaseSettings.from_environment(schema=args.schema)
    from urllib.parse import urlsplit
    if urlsplit(settings.url).hostname not in ('localhost','127.0.0.1','::1'):
        raise SystemExit('Only a local development database is supported')
    print(json.dumps(seed_policy_scenarios(settings) if args.policy_scenarios else seed_customer_scenarios(settings) if args.customer_scenarios else seed(settings),ensure_ascii=False,indent=2))



def seed_customer_scenarios(settings):
    """Explicit fixtures for automatic customer links and ambiguous identities."""
    from .contract_library_web import LibraryApplication
    host=CommonHost(DevelopmentIdentityProvider(USERS,enabled=True),settings,agent_registrations=(REGISTRATION,))
    context=DevelopmentContext('dev-alice');app=LibraryApplication(host,context)
    scenarios=[('05 구매자 자동 연결','자동연결 고객',None),('06 같은 고객 재주문','자동연결 고객',None),
               ('07 동명 고객 A','동명 고객','123-45-67890'),('08 동명 고객 B','동명 고객','987-65-43210'),
               ('09 동명 고객 선택','동명 고객',None),('10 구매자 누락',None,None)]
    results=[]
    for title,buyer,number in scenarios:
        pairs=[('customer',buyer),('customer_registration_number',number),('company','Planning Reference Company'),
               ('manufacturer','OLIMEX'),('product_model','ESP32-POE'),('revision','L'),('variant','WROOM'),('ordered_quantity',150),('unit','대')]
        lines=['고객 연결 기능 검증용 가상 자료: '+title];fields={}
        for name,value in pairs:
            if value is None:continue
            lines.append(f'{name}: {value}')
            fields[name]=[dict(value=value,evidence=[dict(segment_id=f'text:line:{len(lines)}',quote=str(value),occurrence=0)])]
        def provider(fields=fields,**kwargs):return dict(text=json.dumps({'items':[{'fields':fields}]},ensure_ascii=False),tool_calls=[])
        app.review.provider_factory=lambda provider=provider:provider;app.review.model='deterministic-customer-fixture'
        response=app.dispatch('/api/tools/contracts/register',dict(title='[고객 연결 테스트] '+title,filename='customer-'+title[:2]+'.txt',content_base64=base64.b64encode(('\n'.join(lines)+'\n').encode()).decode(),request_key='customer-demo-1:'+title[:2]))
        if not response['ok']:raise RuntimeError('Customer test registration failed')
        results.append(dict(title=title,id=response['data']['id']))
    return results

def seed_policy_scenarios(settings):
    """Local UI fixtures using realistic documents, without paid model calls."""
    from pathlib import Path
    from .contract_library_web import LibraryApplication
    host=CommonHost(DevelopmentIdentityProvider(USERS,enabled=True),settings,agent_registrations=(REGISTRATION,))
    context=DevelopmentContext('dev-alice');app=LibraryApplication(host,context);results=[]
    labels={'customer':'구매자','customer_registration_number':'구매자 사업자등록번호',
            'company':'공급자','manufacturer':'제조사','product_model':'제품명','revision':'설계 개정',
            'variant':'제품 변형','ordered_quantity':'주문 수량','unit':'수량 단위','delivery_due':'고객 도착 납기',
            'contract_item_id':'계약 항목 번호'}
    for path in sorted(Path(__file__).with_name('example_contracts').glob('*.txt')):
        text=path.read_text();fields={};in_policy=False
        for number,line in enumerate(text.splitlines(),1):
            for key,label in labels.items():
                if line.startswith(label+': ') and key not in fields:
                    value=line[len(label)+2:]
                    fields[key]=[dict(value=int(value) if key=='ordered_quantity' else value,
                        evidence=[dict(segment_id=f'text:line:{number}',quote=value,occurrence=0)])]
            if line.startswith('제5조 '):in_policy=True;continue
            if line.startswith('제6조 '):in_policy=False
            if in_policy and ('대체 부품 사용:' in line or '변경안 제출만으로' in line or
                              '부품번호 외 대체' in line or '대체품 목록에 등재' in line):
                fields.setdefault('constraints',[]).append(dict(value=line,evidence=[dict(
                    segment_id=f'text:line:{number}',quote=line,occurrence=0)]))
        def provider(fields=fields,**kwargs):return dict(text=json.dumps({'items':[{'fields':fields}]},ensure_ascii=False),tool_calls=[])
        app.review.provider_factory=lambda provider=provider:provider;app.review.model='deterministic-policy-fixture'
        result=app.dispatch('/api/tools/contracts/register',dict(title='[대체 조건 테스트] '+path.stem,
            filename=path.name,content_base64=base64.b64encode(text.encode()).decode(),request_key='policy-demo-1:'+path.name))
        if not result['ok']:raise RuntimeError('Policy fixture registration failed')
        row=app.review.latest(context,result['data']['id'])['data']
        results.append(dict(id=result['data']['id'],policy=row['constraint_fields'][0]['policy']['status']))
    return results

if __name__=='__main__':main()
