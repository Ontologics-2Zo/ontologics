import unittest
from ontologics.production_planning.contract_policy import classify_policy, resolve_constraint

APPROVAL = '구매자의 사전 서면 승인 없이는 허용하지 않는다.'
SUPPORT = '변경안 제출만으로 변경이 승인된 것으로 보지 않으며, 구매자의 서면 승인 전까지 본 계약의 제품 구성을 유지한다.'
DENIED = '지정된 제조사 및 부품번호 외 대체 부품 사용을 금지한다.'
ALLOWED = '합의된 대체품 목록에 등재된 부품은 별도 승인 없이 사용할 수 있다.'

def field(texts, state='conflict'):
    return dict(state=state, value=texts[0] if state=='supported' else None,
                claims=[dict(value=t,evidence=[dict(quote=t,segment_id=f'text:line:{i}',start=0,end=len(t))]) for i,t in enumerate(texts)])

class PolicyTests(unittest.TestCase):
    def test_four_states(self):
        for texts,expected in [([APPROVAL],'approval_required'),([DENIED],'prohibited'),([ALLOWED],'conditional'),([],'unknown')]:
            self.assertEqual(expected,classify_policy(texts)['status'])
    def test_complementary_clauses_are_not_conflicts(self):
        result=resolve_constraint(field([APPROVAL,SUPPORT]))
        self.assertEqual('supported',result['state'])
        self.assertEqual('approval_required',result['policy']['status'])
        self.assertIn(SUPPORT,result['value'])
        self.assertEqual(2,len(result['claims']))
    def test_real_conflict_and_mixed_scopes_remain_unknown(self):
        for texts in ([DENIED,ALLOWED], [APPROVAL,ALLOWED], ['저항은 대체 가능하지만 안전 부품은 대체를 금지한다.']):
            self.assertEqual('unknown',classify_policy(texts)['status'])
    def test_invalid_or_unsupported_evidence_never_resolves(self):
        for state in ('invalid','unsupported','missing'):
            r=resolve_constraint(field([APPROVAL,SUPPORT],state))
            self.assertEqual(state,r['state']);self.assertEqual('unknown',r['policy']['status'])
    def test_unknown_language_does_not_grant_permission(self):
        for text in ('별도 협의','승인 여부 미확정','구매자 승인 불필요 여부는 추후 결정한다.','대체 불가 여부를 확인한다.'):
            self.assertEqual('unknown',classify_policy([text])['status'])
    def test_single_unknown_clause_keeps_source_but_not_permission(self):
        r=resolve_constraint(field(['별도 협의'],'supported'))
        self.assertEqual('supported',r['state']);self.assertEqual('unknown',r['policy']['status'])

    def test_intake_validates_then_combines_without_false_conflict(self):
        import tempfile
        from pathlib import Path
        from ontologics.production_planning.contract_intake import extract_contract_text, prepare_contract_demand
        from tests.production_planning.test_contract_intake import TEXT, interpretation, claim
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'contract.txt';p.write_text(TEXT+APPROVAL+'\n'+SUPPORT+'\n')
            e=extract_contract_text(p);raw=interpretation(e)
            raw['items'][0]['fields']['constraints']=[claim(e,APPROVAL),claim(e,SUPPORT)]
            r=prepare_contract_demand(e,raw,{'data_version':1,'candidates':[]},source_only=True)
            f=r['items'][0]['fields']['constraints']
            self.assertEqual('supported',f['state']);self.assertEqual('approval_required',f['policy']['status'])
            self.assertFalse(any(x.get('code')=='CONFLICTING_VALUES' for x in r['errors']))
            raw['items'][0]['fields']['constraints'][1]['evidence'][0]['start']+=1
            r=prepare_contract_demand(e,raw,{'data_version':1,'candidates':[]},source_only=True)
            self.assertEqual('invalid',r['items'][0]['fields']['constraints']['state'])

    def test_preview_resolves_legacy_without_mutating_original_or_overriding_hold(self):
        from copy import deepcopy
        from ontologics.production_planning.contract_readiness import preview_fields
        row={'result':{'items':[{'fields':{'constraints':field([APPROVAL,SUPPORT])}}]},'checks':[]}
        original=deepcopy(row)
        fields,_=preview_fields(row)
        self.assertEqual('approval_required',fields[0]['fields']['constraints']['policy']['status'])
        self.assertEqual(original,row)
        row['checks']=[{'id':'hold','decisions':{'0:constraints':{'state':'unresolved','value':None,'reason':'별도 확인'}}}]
        fields,_=preview_fields(row)
        self.assertEqual('unknown',fields[0]['fields']['constraints']['policy']['status'])

class PolicyDatabaseTests(unittest.TestCase):
    def test_four_documents_and_legacy_review_preserve_result_hash(self):
        from tests.db import isolated_database
        from ontologics.production_planning.auto_review_demo import seed_policy_scenarios
        from ontologics.production_planning.contract_library_web import LibraryApplication
        from ontologics.production_planning.library_review import REGISTRATION
        from ontologics.host import CommonHost
        from ontologics.identity import DevelopmentContext,DevelopmentIdentityProvider
        from ontologics.web import USERS
        from ontologics.model import digest,canonical
        with isolated_database() as settings:
            results=seed_policy_scenarios(settings)
            self.assertEqual({'approval_required','prohibited','conditional','unknown'}, {r['policy'] for r in results})
            ctx=DevelopmentContext('dev-alice')
            app=LibraryApplication(CommonHost(DevelopmentIdentityProvider(USERS,enabled=True),settings,agent_registrations=(REGISTRATION,)),ctx)
            cid=next(r['id'] for r in results if r['policy']=='approval_required')
            row=app.review.latest(ctx,cid)['data'];old=row['result']
            # Reproduce the prior validator's stored result, using the same real source claims.
            old['items'][0]['fields']['constraints'].update(state='conflict',value=None)
            old['items'][0]['fields']['constraints'].pop('policy',None)
            old_hash=digest(old)
            with app.host.request(ctx) as session:
                session._service.db.execute('UPDATE pp_contract_interpretations SET result=%s,result_sha256=%s WHERE id=%s',(canonical(old),old_hash,row['id']))
            row=app.review.latest(ctx,cid)['data']
            self.assertEqual(old_hash,digest(row['result']))
            self.assertEqual('conflict',row['result']['items'][0]['fields']['constraints']['state'])
            self.assertEqual('approval_required',row['constraint_fields'][0]['policy']['status'])
            from ontologics.production_planning.contract_policy import effective_fields
            fields=effective_fields(row['result']['items'][0])
            decisions={f'0:{name}':dict(state='accepted' if f['state']=='supported' else 'unresolved',value=f['value'] if f['state']=='supported' else None,reason='') for name,f in fields.items()}
            saved=app.review.confirm(ctx,cid,row['id'],old_hash,[], '', 'policy-confirm',decisions=decisions)
            self.assertTrue(saved['ok'],saved)
            self.assertEqual(old_hash,app.review.latest(ctx,cid)['data']['result_sha256'])
