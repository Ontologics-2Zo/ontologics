import unittest
from ontologics.materials.contract_policy import classify_policy, resolve_constraint

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
        from ontologics.materials.contract_intake import extract_contract_text, prepare_contract_demand
        from tests.materials.test_contract_intake import TEXT, interpretation, claim
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
        from ontologics.materials.contract_readiness import preview_fields
        row={'result':{'items':[{'fields':{'constraints':field([APPROVAL,SUPPORT])}}]},'checks':[]}
        original=deepcopy(row)
        fields,_=preview_fields(row)
        self.assertEqual('approval_required',fields[0]['fields']['constraints']['policy']['status'])
        self.assertEqual(original,row)
        row['checks']=[{'id':'hold','decisions':{'0:constraints':{'state':'unresolved','value':None,'reason':'별도 확인'}}}]
        fields,_=preview_fields(row)
        self.assertEqual('unknown',fields[0]['fields']['constraints']['policy']['status'])

    def test_model_semantics_for_paraphrase_keep_literal_evidence(self):
        text='구매자와 합의한 대체 부품 명세에 포함된 품목은 그 명세의 적용 범위 안에서 추가 승인 절차 없이 사용할 수 있다.'
        f=field([text],'supported');f['claims'][0]['policy_status']='conditional'
        r=resolve_constraint(f)
        self.assertEqual('conditional',r['policy']['status'])
        self.assertEqual('model_interpretation',r['policy']['method'])
        self.assertEqual(text,r['value']);self.assertFalse(r['policy']['approval_verified'])

    def test_model_cannot_override_explicit_prohibition_or_mixed_conditions(self):
        f=field([DENIED,ALLOWED]);
        for c in f['claims']:c['policy_status']='conditional'
        self.assertEqual('unknown',resolve_constraint(f)['policy']['status'])
        combined=field([DENIED+' '+ALLOWED],'supported')
        combined['claims'][0]['policy_status']='conditional'
        self.assertEqual('unknown',resolve_constraint(combined)['policy']['status'])
        f=field(['사전 승인을 얻어야 한다.','목록에 한하여 사용 가능하다.'])
        for c,status in zip(f['claims'],['approval_required','conditional']):c['policy_status']=status
        self.assertEqual('unknown',resolve_constraint(f)['policy']['status'])

    def test_invalid_semantic_status_is_not_authority(self):
        f=field(['대체할 수 있다.'],'supported');f['claims'][0]['policy_status']='approved'
        self.assertEqual('unknown',resolve_constraint(f)['policy']['status'])

    def test_parser_policy_status_only_on_constraints(self):
        import json,tempfile
        from pathlib import Path
        from ontologics.materials.contract_intake import extract_contract_text
        from ontologics.materials.contract_interpretation import parse_interpretation,InterpretationError
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'source.txt';p.write_text(APPROVAL)
            e=extract_contract_text(p)
            c=dict(value=APPROVAL,policy_status='approval_required',evidence=[dict(segment_id='text:line:1',quote=APPROVAL,occurrence=0)])
            raw={'items':[{'fields':{'constraints':[c]}}]}
            parsed=parse_interpretation(json.dumps(raw),e,data_version=1)
            self.assertEqual('approval_required',parsed['items'][0]['fields']['constraints'][0]['policy_status'])
            c['policy_status']='approved'
            with self.assertRaises(InterpretationError):parse_interpretation(json.dumps(raw),e,data_version=1)
            c['policy_status']='conditional';raw['items'][0]['fields']={'customer':[c]}
            with self.assertRaises(InterpretationError):parse_interpretation(json.dumps(raw),e,data_version=1)

    def test_delivery_and_change_document_clauses_do_not_change_policy(self):
        f=field([ALLOWED,'구매자의 사전 서면 동의를 받은 경우에만 변경 일정을 적용한다.',
                 '수량·가격·납기·제품 구성의 변경은 계약번호와 변경 항목을 명시한 변경 합의서로 관리한다.'])
        for c,h in zip(f['claims'],['conditional','approval_required','unknown']):c['policy_status']=h
        p=resolve_constraint(f)['policy']
        self.assertEqual('conditional',p['status']);self.assertEqual(2,len(p['excluded_clauses']))
        self.assertEqual([ALLOWED],p['conditions'])

    def test_delivery_approval_alone_is_not_substitution_approval(self):
        f=field(['구매자의 사전 서면 동의를 받은 경우에만 변경 일정을 적용한다.'],'supported')
        f['claims'][0]['policy_status']='approval_required'
        r=resolve_constraint(f)
        self.assertEqual('unknown',r['policy']['status']);self.assertEqual('missing',r['state'])

    def test_approval_pending_support_is_not_conditional_permission(self):
        f=field(['대체 자재를 적용하려면 구매자로부터 사전에 서면 승인을 얻어야 한다.',
                 '승인 회신을 받기 전에는 기존에 지정된 부품을 사용한다.'])
        for c,h in zip(f['claims'],['approval_required','conditional']):c['policy_status']=h
        self.assertEqual('approval_required',resolve_constraint(f)['policy']['status'])

    def test_same_date_and_explicit_time_are_complementary(self):
        from ontologics.materials.contract_policy import effective_fields
        f=field(['2026-11-20','2026-11-20T17:00:00+09:00'])
        r=effective_fields({'fields':{'delivery_due':f}})['delivery_due']
        self.assertEqual('supported',r['state']);self.assertEqual('2026-11-20T17:00:00+09:00',r['value'])
        for values in (['2026-11-21','2026-11-20T17:00:00+09:00'],
                       ['2026-11-20T17:00:00+09:00','2026-11-20T18:00:00+09:00']):
            r=effective_fields({'fields':{'delivery_due':field(values)}})['delivery_due']
            self.assertEqual('conflict',r['state'])

    def test_intake_preserves_explicit_deadline_precision(self):
        import tempfile
        from pathlib import Path
        from ontologics.materials.contract_intake import extract_contract_text,prepare_contract_demand
        from tests.materials.test_contract_intake import TEXT,interpretation,claim
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'source.txt';p.write_text(TEXT+'Exact due: 2026-11-10T17:00:00+09:00\n')
            e=extract_contract_text(p);raw=interpretation(e)
            raw['items'][0]['fields']['delivery_due'].append(claim(e,'2026-11-10T17:00:00+09:00'))
            r=prepare_contract_demand(e,raw,{'data_version':1,'candidates':[]},source_only=True)
            f=r['items'][0]['fields']['delivery_due']
            self.assertEqual('supported',f['state']);self.assertEqual('2026-11-10T17:00:00+09:00',f['value'])

class PolicyDatabaseTests(unittest.TestCase):
    def test_four_documents_and_legacy_review_preserve_result_hash(self):
        from tests.db import isolated_database
        from ontologics.materials.auto_review_demo import seed_policy_scenarios
        from ontologics.materials.contract_library_web import LibraryApplication
        from ontologics.materials.library_review import REGISTRATION
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
            from ontologics.materials.contract_policy import effective_fields
            fields=effective_fields(row['result']['items'][0])
            decisions={f'0:{name}':dict(state='accepted' if f['state']=='supported' else 'unresolved',value=f['value'] if f['state']=='supported' else None,reason='') for name,f in fields.items()}
            saved=app.review.confirm(ctx,cid,row['id'],old_hash,[], '', 'policy-confirm',decisions=decisions)
            self.assertTrue(saved['ok'],saved)
            self.assertEqual(old_hash,app.review.latest(ctx,cid)['data']['result_sha256'])
