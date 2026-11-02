"""Source interpretation and human reading checks; no graph/production approval."""
import base64
import json
import tempfile
import uuid
from pathlib import Path
from ..model import canonical,digest
from ..runtime import ModelConfig
from ..service import DomainError
from .contract_intake import extract_contract_text,prepare_contract_demand
from .contract_interpretation import REGISTRATION as BASE_REGISTRATION,interpretation_messages,parse_interpretation
from ..host import AgentRegistration
REGISTRATION=AgentRegistration('contract_original_interpretation',BASE_REGISTRATION.instructions+'\nFor this original-document reading phase, keep all dates and units verbatim in their source language.\ncontract_item_id means an explicit LINE ITEM identifier, NEVER the overall contract/document number. If there is no line-item number, leave its claims empty.\nunit is the literal counting unit (e.g. 대), not the counted product noun (e.g. 보드).\nFor constraints quote the complete explicit revision/substitution restriction clause, including its subject, not sentence-ending fragments. Supplementary statements about later consultation are not conflicting alternatives to the main restriction. Do not split complementary policy sentences into competing claims. If the policy is split across lines and cannot fit the evidence schema, leave it unresolved rather than fabricate a merged quote.\n',BASE_REGISTRATION.allowed_tools)

DDL='''CREATE TABLE IF NOT EXISTS pp_contract_interpretations(
 id TEXT PRIMARY KEY, contract_id TEXT NOT NULL REFERENCES pp_contract_originals(id),
 source_sha256 TEXT NOT NULL, status TEXT NOT NULL, result JSONB,
 result_sha256 TEXT, created_at TEXT NOT NULL, completed_at TEXT);
CREATE TABLE IF NOT EXISTS pp_contract_reading_checks(
 id TEXT PRIMARY KEY, interpretation_id TEXT NOT NULL REFERENCES pp_contract_interpretations(id),
 result_sha256 TEXT NOT NULL, actor TEXT NOT NULL, checked_at TEXT NOT NULL,
 scopes JSONB NOT NULL, note TEXT NOT NULL);
ALTER TABLE pp_contract_reading_checks ADD COLUMN IF NOT EXISTS decisions JSONB'''

class LibraryReview:
    def __init__(self,library,context,provider_factory=None,model=None):
        self.library=library;self.host=library.host;self.provider_factory=provider_factory;self.model=model
        with self.host.request(context) as session:session._service.db.execute(DDL)

    def latest(self,context,contract_id):
        with self.host.request(context) as session:
            svc=session._service
            def read():
                row=svc.db.execute('SELECT * FROM pp_contract_interpretations WHERE contract_id=%s ORDER BY created_at DESC,id DESC LIMIT 1',(contract_id,)).fetchone()
                if row:
                    if row['result'] is not None and digest(row['result'])!=row['result_sha256']:
                        raise DomainError('RESULT_INTEGRITY','해석 결과 무결성을 확인할 수 없습니다.')
                    row['checks']=svc.db.execute('SELECT id,actor,checked_at,scopes,note,decisions FROM pp_contract_reading_checks WHERE interpretation_id=%s ORDER BY checked_at,id',(row['id'],)).fetchall()
                return svc._envelope(ok=True,data=row)
            return svc._run(session.context['actor'],'contract_interpretation_get',{'contract_id':contract_id},read)

    def interpret(self,context,contract_id,request_key):
        original=self.library.get(context,contract_id)
        if not original['ok']:return original
        data=original['data']
        if not self.provider_factory:raise DomainError('AI_NOT_CONFIGURED','실제 모델 연결을 설정해 주세요. 원본은 보관되어 있습니다.')
        with self.host.request(context) as session:
            svc=session._service
            def begin():
                active=svc.db.execute("SELECT id FROM pp_contract_interpretations WHERE contract_id=%s AND status='processing' AND created_at::timestamptz > now()-interval '3 minutes'",(contract_id,)).fetchone()
                if active:raise DomainError('AI_BUSY','계약을 해석 중입니다. 잠시 후 다시 확인해 주세요.')
                iid=str(uuid.uuid4())
                svc.db.execute('INSERT INTO pp_contract_interpretations VALUES(%s,%s,%s,%s,NULL,NULL,%s,NULL)',(iid,contract_id,data['sha256'],'processing',svc.now()))
                return svc._envelope(ok=True,data={'id':iid})
            started=svc._run(session.context['actor'],'contract_interpretation_start',{'contract_id':contract_id,'sha256':data['sha256']},begin,permission='draft',request_key=request_key)
        if not started['ok']:return started
        if any(w.get('code')=='IDEMPOTENT_REPLAY' for w in started['warnings']):return self.latest(context,contract_id)
        iid=started['data']['id'];provider=None
        result={'items':[],'errors':[],'model':self.model,'api_calls':0,'run_id':None}
        status='failed'
        try:
            with tempfile.TemporaryDirectory(prefix='contract-read-') as folder:
                path=Path(folder)/('source.pdf' if data['mime']=='application/pdf' else 'source.txt')
                path.write_bytes(base64.b64decode(data['content_base64']))
                extracted=extract_contract_text(path)
                if not extracted['supported']:raise DomainError(extracted['errors'][0]['code'],'원본에서 텍스트를 추출할 수 없습니다.')
                with self.host.request(context) as session:
                    snapshot=session.call('lookup',{})
                    if not snapshot['ok']:raise DomainError('PUBLIC_QUERY_FAILED','기준 버전을 조회할 수 없습니다.')
                    version=snapshot['data_version']
                    provider=self.provider_factory()
                    runtime=session.run_agent(REGISTRATION.name,provider,interpretation_messages(extracted,data_version=version),
                        config=ModelConfig(model=self.model,max_steps=1,max_attempts=1),capture_tool_trace=True)
                    result['run_id']=runtime['run_id']
                    if not runtime['ok']:raise DomainError(runtime['error']['code'],'모델 해석을 완료하지 못했습니다.')
                    if runtime.get('tool_trace'):raise DomainError('INTERPRETATION_TOOL_UNEXPECTED','해석 중 허용하지 않은 도구 요청입니다.')
                    interpreted=parse_interpretation(runtime['text'],extracted,data_version=version)
                    report=prepare_contract_demand(extracted,interpreted,{'data_version':version,'candidates':[]},source_only=True)
                    result.update(items=report['items'],errors=report['errors'],original=report['original'])
                    accepted=report['valid'] and bool(report['items'])
                    if not report['items']:result['errors'].append({'code':'MISSING_ITEMS'})
                    session.record_agent_check(runtime['run_id'],accepted=accepted,code=None if accepted else 'CONTRACT_INTERPRETATION_REJECTED')
                    status='needs_review' if accepted else 'failed'
        except Exception as exc:
            # No raw provider response, source text or credentials in error messages.
            result['errors'].append({'code':getattr(exc,'code','INTERPRETATION_FAILED')})
        result['api_calls']=len(getattr(provider,'calls',[]))
        with self.host.request(context) as session:
            svc=session._service
            def finish():
                svc.db.execute('UPDATE pp_contract_interpretations SET status=%s,result=%s,result_sha256=%s,completed_at=%s WHERE id=%s',
                    (status,canonical(result),digest(result),svc.now(),iid))
                return svc._envelope(ok=True,data={'id':iid})
            saved=svc._run(session.context['actor'],'contract_interpretation_finish',{'id':iid,'result_sha256':digest(result)},finish,permission='draft',request_key='finish:'+iid)
        return self.latest(context,contract_id) if saved['ok'] else saved

    def confirm(self,context,contract_id,interpretation_id,result_sha256,scopes,note,request_key,decisions=None):
        original=self.library.get(context,contract_id)
        if not original['ok']:return original
        if not isinstance(note,str) or len(note)>2000:raise DomainError('INVALID_INPUT','확인 의견은 2000자 이하로 입력하세요.')
        with self.host.request(context) as session:
            svc=session._service
            def check():
                nonlocal scopes
                svc.db.execute('SELECT id FROM pp_contract_originals WHERE id=%s FOR UPDATE',(contract_id,)).fetchone()
                row=svc.db.execute('SELECT * FROM pp_contract_interpretations WHERE contract_id=%s ORDER BY created_at DESC,id DESC LIMIT 1',(contract_id,)).fetchone()
                if not row or row['id']!=interpretation_id or row['result_sha256']!=result_sha256:
                    raise DomainError('STALE_REVISION','해석 결과가 바뀌었습니다. 최신 결과를 다시 확인하세요.')
                if row['status']!='needs_review' or row['source_sha256']!=original['data']['sha256'] or digest(row['result'])!=result_sha256:
                    raise DomainError('INVALID_STATE','현재 결과를 확인 처리할 수 없습니다.')
                allowed=[f'{i}:{name}' for i,item in enumerate(row['result']['items']) for name,f in item['fields'].items() if f['state']=='supported']
                if decisions is not None:
                    fields={f'{i}:{name}':f for i,item in enumerate(row['result']['items']) for name,f in item['fields'].items()}
                    if not isinstance(decisions,dict) or set(decisions)!=set(fields):
                        raise DomainError('INVALID_INPUT','모든 항목의 검토 상태가 필요합니다.')
                    scopes=[]
                    for scope,decision in decisions.items():
                        if not isinstance(decision,dict) or set(decision)!={'state','value','reason'}:
                            raise DomainError('INVALID_INPUT','검토 형식을 확인하세요.')
                        state=decision['state'];value=decision['value'];reason=decision['reason']
                        if state not in ('accepted','corrected','unresolved') or not isinstance(reason,str) or len(reason)>2000:
                            raise DomainError('INVALID_INPUT','검토 상태와 사유를 확인하세요.')
                        if state=='accepted':
                            if scope not in allowed or value!=fields[scope]['value']:
                                raise DomainError('INVALID_INPUT','미확인 값은 자동 확정할 수 없습니다.')
                            scopes.append(scope)
                        elif state=='corrected':
                            if not isinstance(value,str) or not value.strip() or len(value)>2000 or not reason.strip():
                                raise DomainError('INVALID_INPUT','수정 값과 원문 근거 또는 수정 사유를 입력하세요.')
                        elif value is not None:
                            raise DomainError('INVALID_INPUT','미확인 항목에는 확정 값을 저장할 수 없습니다.')
                if decisions is None and (not isinstance(scopes,list) or not scopes or any(not isinstance(x,str) for x in scopes) or len(set(scopes))!=len(scopes) or not set(scopes)<=set(allowed)):
                    raise DomainError('INVALID_INPUT','원문 근거가 검증된 항목만 선택해 확인하세요.')
                latest=svc.db.execute('SELECT * FROM pp_contract_reading_checks WHERE interpretation_id=%s ORDER BY checked_at DESC,id DESC LIMIT 1',(interpretation_id,)).fetchone()
                if latest and latest['actor']==session.context['actor'] and latest['decisions']==decisions and latest['note']==note and set(latest['scopes'])==set(scopes):
                    return svc._envelope(ok=True,data={'confirmed_scopes':scopes,'decisions':decisions,'already_saved':True})
                svc.db.execute('INSERT INTO pp_contract_reading_checks (id,interpretation_id,result_sha256,actor,checked_at,scopes,note,decisions) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',
                    (str(uuid.uuid4()),interpretation_id,result_sha256,session.context['actor'],svc.now(),canonical(scopes),note,canonical(decisions) if decisions is not None else None))
                return svc._envelope(ok=True,data={'confirmed_scopes':scopes,'decisions':decisions})
            return svc._run(session.context['actor'],'contract_reading_confirm',dict(contract_id=contract_id,interpretation_id=interpretation_id,result_sha256=result_sha256,scopes=scopes,note=note,decisions=decisions),check,permission='review',request_key=request_key)

    def storage(self,context,contract_id):
        original=self.library.get(context,contract_id)
        if not original['ok']:return original
        with self.host.request(context) as session:
            svc=session._service
            def read():
                interpretations=svc.db.execute('SELECT id,contract_id,status,source_sha256,result_sha256,created_at,completed_at,result FROM pp_contract_interpretations WHERE contract_id=%s ORDER BY created_at,id',(contract_id,)).fetchall()
                checks=svc.db.execute('SELECT c.* FROM pp_contract_reading_checks c JOIN pp_contract_interpretations i ON i.id=c.interpretation_id WHERE i.contract_id=%s ORDER BY c.checked_at,c.id',(contract_id,)).fetchall()
                source={k:v for k,v in original['data'].items() if k not in ('content_base64','text')}
                return svc._envelope(ok=True,data={'pp_contract_originals':[source],'pp_contract_interpretations':interpretations,'pp_contract_reading_checks':checks})
            return svc._run(session.context['actor'],'contract_storage_read',{'contract_id':contract_id},read)
