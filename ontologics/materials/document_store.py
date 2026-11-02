"""Host-only, same-PostgreSQL document extension; existing files stay unchanged.

Uses the existing RequestSession Service and its _run transaction, schema lock,
requests replay and runs audit. Private common seams are explicit dependencies,
not a parallel identity/database framework. Never register this object with an
Agent. Immutable document bodies and append-only human events are separate.
"""
from copy import deepcopy
from datetime import datetime
import hashlib
import html
import json
import os
from pathlib import Path
import re
import uuid

from ..identity import IdentityError, UserIdentity
from ..model import canonical, digest
from ..service import DomainError
from .documents import build_saved_business_documents, edit_document_text, INPUT_FIELDS
from .demand_confirmation_store import DemandConfirmationStore

RULE_VERSION = 'document-store-0.1'
DDL = '''
CREATE TABLE IF NOT EXISTS pp_document_versions(
 document_ref TEXT NOT NULL, document_version INTEGER NOT NULL CHECK(document_version>0),
 owner_actor TEXT NOT NULL, kind TEXT NOT NULL,
 data_version INTEGER NOT NULL REFERENCES snapshots(version),
 content JSONB NOT NULL, markdown TEXT NOT NULL, draft JSONB NOT NULL,
 recipe JSONB NOT NULL, demand_events JSONB NOT NULL, record_hash TEXT NOT NULL,
 PRIMARY KEY(document_ref,document_version));
CREATE TABLE IF NOT EXISTS pp_document_events(
 sequence BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 event_id TEXT NOT NULL UNIQUE, document_ref TEXT NOT NULL, document_version INTEGER NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('review','manual_dispatch')),
 event JSONB NOT NULL, event_hash TEXT NOT NULL,
 FOREIGN KEY(document_ref,document_version) REFERENCES pp_document_versions(document_ref,document_version));
'''


def _text(value, limit=10000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError('Bounded nonempty text required')
    return value


def _version(value):
    if type(value) is not int or value < 1: raise DomainError('INVALID_VERSION', 'Exact positive document version required')
    return value


def _lookup(identities, subject):
    try: user=identities.lookup(subject)
    except IdentityError: return None
    except Exception: raise DomainError('IDENTITY_PROVIDER_UNAVAILABLE','Cannot verify current identity') from None
    if not isinstance(user,UserIdentity) or user.subject!=subject:
        raise DomainError('INVALID_IDENTITY','Exact current person required')
    return user


def _identity_dependency(identities, subject):
    user=_lookup(identities,subject)
    return dict(subject=subject,principal=None if user is None else user.principal())


def _fenced(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    fence = '`' * max(3, 1 + max((len(x) for x in re.findall(r'`+', raw)), default=0))
    return fence + 'json\n' + raw + '\n' + fence


def _cell(value):
    if value is None: return '미정'
    return ''.join('&#'+str(ord(c))+';' if c in '\\`*_{}[]()#+-.!|~' else html.escape(c, quote=True)
                   for c in str(value)).replace('\r', '').replace('\n', '<br>')


def render_document_version(content):
    """Deterministic immutable Markdown. Current review/dispatch are separate events."""
    p = content['payload']; b = p['binding']; q = p['quantities']; s = p['schedule']; scope = p['scope']
    lines = ['# '+_cell(content['title']), '',
        '문서 ID: '+content['document_ref'], '문서 버전: '+str(content['document_version']),
        '내용 SHA-256: '+content['content_sha256'], '',
        '**같은 PostgreSQL에 저장된 불변 문서 본문입니다. 실행 아님.**',
        '생성 당시 미검토·미송부입니다. 이후 검토·외부 송부 사실은 이 정확 버전에 연결된 별도 사건으로 조회합니다.',
        '문서 검토는 기술 적합성·고객 합의·수요 확인·생산/구매 승인이 아닙니다. 자동 전송은 하지 않습니다.', '',
        '구성: '+_cell(b['configuration_id'])+' / 공개 버전: '+str(b['data_version'])+' / 시나리오: '+_cell(b['scenario']), '',
        '| 주문(대) | 신규 N(대) | 선행 I(대) | 잔여 R(대) |', '| --- | --- | --- | --- |',
        '| '+' | '.join(_cell(q[k]) for k in ('target_quantity','planned_input_quantity','initial_quantity','remaining_quantity'))+' |', '',
        f"전체 BOM {scope['bom_rows']}행 · 실장 미확인 {scope['unknown_population_rows']}행 · 재고 미확인 {scope['unknown_stock_components']}부품.",
        '전체 자재 한계: '+_cell(scope['whole_bom_material_limit'])+' / 범위 내 조건부 상한: '+_cell(scope['conditional_upper_bound']), '',
        '**별도 초기 전체 자재 준비 가정** 아래 시간입니다. 위 미확인 범위가 해소된 것이 아닙니다.', '',
        '| 구간 | 수량(대) | 작업 시작 | 가공 시작 | 완료 |', '| --- | --- | --- | --- | --- |']
    for key,label in (('initial','선행 별도 가정'),('remaining','잔여 조건부 예상')):
        lines.append('| '+label+' | '+' | '.join(_cell(s[key][k]) for k in ('quantity','work_start','processing_start','completion'))+' |')
    lines += ['', '전체 자재 준비: '+_cell(p['material_ready']['ready_at']),
        '고객 납기 원문: '+_cell(p['demand'].get('contract_item_candidate',{}).get('delivery_due')),
        '고객 도착 준수: 미정. 날짜에 시각·배송지·출하일을 임의로 추가하지 않습니다.', '',
        '## 원 자재와 가상 잔여', '원래와 잔여는 독립 시나리오이며 합산하지 않습니다. 부품 단위 piece.', '',
        '| 정확 부품 ID / 행·위치·원문 | q | 원 D | 원 A | 원 S | 가상 사용 | 잔여 A | 잔여 D | 잔여 S |',
        '| --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for row in p['components']:
        label=row['component_id']+'\n'+'\n'.join(r['id']+' '+','.join(r['positions'])+' '+r['raw_value'] for r in row['bom_rows'])
        lines.append('| '+_cell(label)+' | '+' | '.join(_cell(row[k]) for k in
            ('q','D','A','S','virtual_used_quantity','remaining_available_quantity','remaining_required_quantity','remaining_shortage'))+' |')
    proc=p['procurement']
    lines += ['', '## 대체·구매 검토', _cell(p['substitution']['status']), _cell(p['substitution']['reviewed_scope']), '',
        '정확 원 부품의 양의 부족 목록입니다. 목록 밖 unknown을0으로 해석하지 않습니다. 발주서가 아닙니다.', '',
        '| 부품 | 부족 | 구매량 | 통화 | 소계 |', '| --- | --- | --- | --- | --- |']
    positive={r['component_id'] for r in proc['purchase_review']}
    for row in proc['items']:
        if row['component_id'] in positive:
            lines.append('| '+' | '.join(_cell(row[k]) for k in ('component_id','shortage_quantity','purchase_quantity','currency','subtotal'))+' |')
    lines += ['', '통화별 알려진 부분 소계: '+_cell(json.dumps(proc['known_item_totals'],ensure_ascii=False)),
        '전체 금액: '+_cell(None if proc['total_by_currency'] is None else json.dumps(proc['total_by_currency'],ensure_ascii=False)), '',
        '## 자유 의견 — 미검증 문구, 계산·근거·권한 변경 아님', _fenced(content['editorial']), '',
        '## 정확 저장 내용·근거·진단',
        'real BOM은 입력 근거, synthetic 운영 입력과 별도 가정은 계산 근거입니다. null=미정, false=권한 없음.',
        _fenced(content), '']
    return '\n'.join(lines)


class _DocumentStore:
    """Small domain actions inside an existing host session; not a public tool."""
    def __init__(self, session, identities):
        self.service=session._service
        self.bound=session.context
        self.identities=identities
        self.actor=self.bound['actor']

    def _user(self, subject):
        return _lookup(self.identities,subject)

    def _refresh(self):
        user=self._user(self.bound['subject'])
        if (user is None or user.actor!=self.actor or self.bound['identity_mode']!=self.identities.mode
                or self.service.principals.get(self.actor,{}).get('kind')!='human'):
            raise DomainError('FORBIDDEN','Current host-bound human required')
        permissions=user.permissions & frozenset(self.bound['permissions'])
        self.service.principals[self.actor]=UserIdentity(user.subject,permissions).principal()
        return user

    def run(self, tool, args, action, *, permission='read', request_key=None):
        # Refresh BEFORE _run can replay an earlier successful mutation.
        try:
            self._refresh()
            self.service._require(self.actor,'read')
        except DomainError as exc:
            def fail(): raise exc
            return self.service._run(self.actor,tool,args,fail)
        def guarded():
            self._refresh()
            self.service._require(self.actor,'read')
            self.service._require(self.actor,permission)
            return action()
        return self.service._run(self.actor,tool,args,guarded,permission=permission,request_key=request_key)

    def _row(self, ref, version):
        _text(ref,200);_version(version)
        if self.service.db.execute("SELECT to_regclass('pp_document_versions') AS name").fetchone()['name'] is None:
            raise DomainError('DOCUMENT_NOT_FOUND','No document stored')
        row=self.service.db.execute('SELECT * FROM pp_document_versions WHERE document_ref=%s AND document_version=%s',(ref,version)).fetchone()
        if row is None: raise DomainError('DOCUMENT_NOT_FOUND','Exact document version not found')
        row=dict(row)
        permissions=set(self.service.principals[self.actor]['roles'])
        if row['owner_actor']!=self.actor and 'review' not in permissions:
            raise DomainError('DOCUMENT_FORBIDDEN','Only owner or current reviewer may access this document')
        if digest({k:v for k,v in row.items() if k!='record_hash'})!=row['record_hash']:
            raise DomainError('DOCUMENT_INTEGRITY','Stored version record mismatch')
        c=row['content']
        if (digest({k:v for k,v in c.items() if k!='content_sha256'})!=c['content_sha256']
                or render_document_version(c)!=row['markdown']):
            raise DomainError('DOCUMENT_INTEGRITY','Stored content/body mismatch')
        row['latest_version']=self.service.db.execute('SELECT MAX(document_version) AS v FROM pp_document_versions WHERE document_ref=%s',(ref,)).fetchone()['v']
        return row

    def _latest(self,row):
        if row['document_version']!=row['latest_version']:
            raise DomainError('DOCUMENT_VERSION_CONFLICT','Reload current document version')

    def _owner(self,row):
        if row['owner_actor']!=self.actor:
            raise DomainError('DOCUMENT_FORBIDDEN','Only document owner may edit or record external dispatch')

    def _binding(self, draft, events):
        b=draft['payload']['binding']; source=b['demand_source']
        if self.service._version()!=b['data_version']:
            raise DomainError('DOCUMENT_DATA_VERSION_CHANGED','Public snapshot changed; prepare a new document')
        row=self.service.db.execute('SELECT * FROM demand_review_revisions WHERE demand_id=%s ORDER BY revision DESC LIMIT 1',
            (source['demand_id'],)).fetchone()
        if (row is None or row['revision']!=source['revision'] or row['target_sha256']!=source['target_sha256']
                or row['data_version']!=b['data_version'] or row['identity_mode']!=self.bound['identity_mode']):
            raise DomainError('DEMAND_TARGET_CHANGED','Current demand revision/hash/version changed')
        DemandConfirmationStore._integrity(row)  # existing source-bytes/target validator
        if hashlib.sha256(bytes(row['source_bytes'])).hexdigest()!=source['source']['sha256']:
            raise DomainError('DEMAND_TARGET_CHANGED','Demand source hash changed')
        current=[dict(r) for r in self.service.db.execute('SELECT id,content_hash FROM demand_review_events WHERE demand_id=%s AND revision=%s ORDER BY sequence',
            (source['demand_id'],source['revision']))]
        if current!=events['records']: raise DomainError('DEMAND_TARGET_CHANGED','Demand confirmation events changed; recalculate')
        for dependency in events['identity_dependencies']:
            if _identity_dependency(self.identities,dependency['subject'])!=dependency:
                raise DomainError('DEMAND_TARGET_CHANGED','Demand confirmation identity/permissions changed; recalculate')

    def _events(self,row):
        events=[]
        for raw in self.service.db.execute('SELECT event,event_hash FROM pp_document_events WHERE document_ref=%s AND document_version=%s ORDER BY sequence',
                (row['document_ref'],row['document_version'])):
            e=raw['event']
            if digest(e)!=raw['event_hash'] or e['content_sha256']!=row['content']['content_sha256']:
                raise DomainError('DOCUMENT_INTEGRITY','Stored event mismatch')
            events.append(deepcopy(e))
        return events

    def view(self,row):
        binding_current=True;binding_issue=None
        try: self._binding(row['draft'],row['demand_events'])
        except DomainError as exc: binding_current=False;binding_issue=exc.code
        events=self._events(row); reviews=[e for e in events if e['kind']=='review']
        valid_review=None
        for e in events:
            valid=False
            if e['kind']=='review':
                user=self._user(e['subject'])
                valid=bool(user and {'read','review'}<=user.permissions and user.actor==e['actor']
                    and user.actor!=row['owner_actor'] and e['identity_mode']==self.identities.mode
                    and row['document_version']==row['latest_version'] and binding_current)
                if reviews[-1]['event_id']==e['event_id'] and valid and e['payload']['decision']=='approve':valid_review=e['event_id']
            e['currently_valid']=valid
        state=dict(review='unreviewed',dispatch='recorded' if any(e['kind']=='manual_dispatch' for e in events) else 'unsent',
            purchasing_received='unknown',ordered='unknown',received='unknown')
        if reviews:
            state['review']='reviewed' if valid_review else 'changes_requested' if reviews[-1]['payload']['decision']=='request_changes' and reviews[-1]['currently_valid'] else 'review_invalidated'
        return dict(deepcopy(row['content']),markdown=row['markdown'],latest_version=row['latest_version'],state=state,
            events=events,binding_current=binding_current,binding_issue=binding_issue,valid_review_event_id=valid_review)

    def _insert_version(self,ref,version,draft,recipe,events):
        # Public draft validator checks locked payload/body/hash and editable fields.
        edit_document_text(draft,{})
        payload=deepcopy(draft['payload']);payload['persistence']='same_postgresql_immutable_document_version'
        content=dict(document_ref=ref,document_version=version,kind=draft['kind'],title=draft['title'],payload=payload,
            editorial=deepcopy(draft['editorial']),owner_actor=self.actor,identity_mode=self.bound['identity_mode'],
            created_at=self.service.now(),source_draft_sha256=draft['content_sha256'],storage_rule_version=RULE_VERSION)
        content['content_sha256']=digest(content)
        row=dict(document_ref=ref,document_version=version,owner_actor=self.actor,kind=draft['kind'],
            data_version=payload['binding']['data_version'],content=content,markdown=render_document_version(content),
            draft=draft,recipe=recipe,demand_events=events)
        checksum=digest(row)
        self.service.db.execute('''INSERT INTO pp_document_versions
            (document_ref,document_version,owner_actor,kind,data_version,content,markdown,draft,recipe,demand_events,record_hash)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
            (ref,version,self.actor,draft['kind'],row['data_version'],canonical(content),row['markdown'],canonical(draft),canonical(recipe),canonical(events),checksum))
        return dict(document_ref=ref,document_version=version,kind=draft['kind'],content_sha256=content['content_sha256'])

    def _append_event(self,row,kind,payload):
        event=dict(event_id=str(uuid.uuid4()),document_ref=row['document_ref'],document_version=row['document_version'],
            content_sha256=row['content']['content_sha256'],kind=kind,actor=self.actor,subject=self.bound['subject'],
            identity_mode=self.bound['identity_mode'],created_at=self.service.now(),payload=deepcopy(payload))
        self.service.db.execute('''INSERT INTO pp_document_events(event_id,document_ref,document_version,kind,event,event_hash)
            VALUES(%s,%s,%s,%s,%s,%s)''',(event['event_id'],row['document_ref'],row['document_version'],kind,canonical(event),digest(event)))
        return event

    def envelope(self,data,version=None):
        return self.service._envelope(ok=True,data=data,version=version)


def _dispatch_details(details,now,created_at):
    try:
        if not isinstance(details,dict) or set(details)!={'recipient','sent_at','method','source'}:raise ValueError()
        _text(details['recipient'],500);_text(details['method'],500)
        stamp=details['sent_at']
        if not isinstance(stamp,str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)',stamp):raise ValueError()
        when=datetime.fromisoformat(stamp.replace('Z','+00:00'))
        if when.utcoffset() is None or not datetime.fromisoformat(created_at)<=when<=datetime.fromisoformat(now):raise ValueError()
        source=details['source']
        if not isinstance(source,dict) or set(source)!={'source_ref','sha256','locator'}:raise ValueError()
        _text(source['source_ref'],500);_text(source['locator'],2000)
        if not isinstance(source['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',source['sha256']):raise ValueError()
    except (ValueError,TypeError,KeyError):
        raise DomainError('INVALID_DISPATCH','Recipient, past offset timestamp, method and source reference/hash/locator required; sender is host-bound') from None
    return deepcopy(details)


class BusinessDocumentWorkflow:
    """Trusted host composition. Uses its existing connection and identity provider.

    Exact-key replays acknowledge the original operation; callers must get the
    version again for current review validity. New keys are always revalidated.
    No browser/API/Agent registration, database fallback or actual transmission.
    """
    def __init__(self,host,demand_workflow):
        if host._database!=demand_workflow.host._database or host._identities is not demand_workflow.identities:
            raise ValueError('Same database and trusted identity provider required')
        self.host=host;self.demands=demand_workflow;self.identities=demand_workflow.identities

    def _perform(self,context,tool,args,action,*,permission='read',request_key=None):
        with self.host.request(context) as session:
            store=_DocumentStore(session,self.identities)
            return store.run(tool,args,lambda:action(store),permission=permission,request_key=request_key)

    def _calculate(self,context,recipe):
        try:
            args=recipe['calculation_arguments']
            if any(args.get(k) is None for k in ('demand_id','item_id','data_version','expected_revision','expected_target_sha256')):
                raise ValueError('Explicit current demand ID/item/version/revision/hash required')
            r=build_saved_business_documents(self.demands,self.host,context,scenario=recipe['scenario'],**deepcopy(args))
            if r['documents'] is None:
                raise DomainError('DEMAND_TARGET_CHANGED','Current source/demand/public lookup did not produce documents')
            demand=r['demand_response']['data']
            # Capture identity dependencies, then let the EXISTING workflow
            # recompute confirmation effectiveness. No duplicate confirmation rules.
            dependencies=[_identity_dependency(self.identities,subject) for subject in sorted({
                e['payload']['reviewer_subject'] for e in demand['events'] if e['kind']=='confirm'})]
            fresh=self.demands.get(context,args['demand_id'])
            if not fresh['ok'] or canonical(fresh['data'])!=canonical(demand):
                raise DomainError('DEMAND_TARGET_CHANGED','Demand/confirmation changed during preparation')
            events=dict(records=[{k:e[k] for k in ('id','content_hash')} for e in demand['events']],
                identity_dependencies=dependencies)
            return r['documents'],events,None
        except DomainError as exc:return None,None,exc
        except (ValueError,TypeError,KeyError):
            return None,None,DomainError('DOCUMENT_RECALCULATION_FAILED','Calculation input failed existing Task02–08 validation')

    def create_documents(self,context,*,calculation_arguments,request_key,scenario='virtual_remaining'):
        recipe=dict(calculation_arguments=deepcopy(calculation_arguments),scenario=scenario)
        drafts,events,failure=self._calculate(context,recipe)
        def act(store):
            if failure:raise failure
            store.service.db.execute(DDL)
            for d in drafts:store._binding(d,events)
            docs=[store._insert_version('document:'+str(uuid.uuid4()),1,d,recipe,events) for d in drafts]
            return store.envelope(dict(documents=docs),drafts[0]['payload']['binding']['data_version'])
        return self._perform(context,'pp_create_documents',recipe,act,permission='draft',request_key=request_key)

    def get_document(self,context,document_ref,document_version):
        def act(store):
            row=store._row(document_ref,document_version)
            return store.envelope(store.view(row),row['data_version'])
        return self._perform(context,'pp_get_document',dict(ref=document_ref,version=document_version),
            act)

    def revise_document(self,context,document_ref,*,expected_version,edits,request_key):
        request=dict(ref=document_ref,expected_version=expected_version,edits=deepcopy(edits))
        loaded=self._perform(context,'pp_load_document_for_revision',dict(ref=document_ref,version=expected_version),
            lambda s:s.envelope(s._row(document_ref,expected_version)))
        failure=None;new=None;recipe=None;events=None
        if not loaded['ok']:
            failure=DomainError(loaded['errors'][0]['code'],loaded['errors'][0]['message'])
        else:
            old=loaded['data'];recipe=deepcopy(old['recipe']);new=deepcopy(old['draft']);events=old['demand_events']
            try:
                if not isinstance(edits,dict) or not edits or set(edits)-{'text','inputs'}:raise ValueError('Use text/inputs edits')
                text=edits.get('text',{});inputs=edits.get('inputs',{})
                if not isinstance(inputs,dict) or set(inputs)-INPUT_FIELDS:raise ValueError('Unsupported structured field')
                new=edit_document_text(new,text)
                changed=any(canonical(recipe['calculation_arguments'].get(k))!=canonical(v) for k,v in inputs.items())
                if changed:
                    recipe['calculation_arguments'].update(deepcopy(inputs))
                    drafts,events,failure=self._calculate(context,recipe)
                    if not failure:
                        draft=next(d for d in drafts if d['kind']==old['kind'])
                        new=edit_document_text(draft,new['editorial'])
                elif new==old['draft']:failure=DomainError('DOCUMENT_NO_CHANGES','Reuse the same immutable version')
            except (ValueError,KeyError,TypeError):failure=DomainError('INVALID_INPUT','Only supported text/calculation inputs may change')
        def act(store):
            row=store._row(document_ref,expected_version);store._owner(row);store._latest(row)
            store._binding(row['draft'],row['demand_events'])
            if failure:raise failure
            store._binding(new,events)
            if canonical(new['payload']['binding'])!=canonical(row['draft']['payload']['binding']):
                raise DomainError('DEMAND_TARGET_CHANGED','Editing cannot replace the original demand binding')
            result=store._insert_version(document_ref,expected_version+1,new,recipe,events)
            return store.envelope(result,row['data_version'])
        return self._perform(context,'pp_revise_document',request,act,permission='draft',request_key=request_key)

    def review_document(self,context,document_ref,document_version,*,expected_sha256,decision,note,request_key):
        args=dict(ref=document_ref,version=document_version,expected_sha256=expected_sha256,decision=decision,note=note)
        def act(store):
            row=store._row(document_ref,document_version);store._latest(row);store._binding(row['draft'],row['demand_events'])
            if row['owner_actor']==store.actor:raise DomainError('SELF_REVIEW_FORBIDDEN','Document author and reviewer must differ')
            if row['content']['content_sha256']!=expected_sha256:raise DomainError('DOCUMENT_CONTENT_CHANGED','Review exact displayed content')
            if decision not in ('approve','request_changes'):raise DomainError('INVALID_DECISION','Choose approve or request_changes')
            _text(note)
            e=store._append_event(row,'review',dict(decision=decision,note=note,scope='document_content_only'))
            return store.envelope(dict(recorded=True,event_id=e['event_id'],document_ref=document_ref,document_version=document_version,
                content_sha256=expected_sha256,scope='document_content_only'),row['data_version'])
        return self._perform(context,'pp_review_document',args,act,permission='review',request_key=request_key)

    def record_manual_dispatch(self,context,document_ref,document_version,*,expected_sha256,details,request_key):
        args=dict(ref=document_ref,version=document_version,expected_sha256=expected_sha256,details=deepcopy(details))
        def act(store):
            row=store._row(document_ref,document_version);store._owner(row);store._latest(row);store._binding(row['draft'],row['demand_events'])
            if row['content']['content_sha256']!=expected_sha256:raise DomainError('DOCUMENT_CONTENT_CHANGED','Record dispatch of the exact reviewed content')
            view=store.view(row)
            if view['valid_review_event_id'] is None:raise DomainError('DOCUMENT_REVIEW_REQUIRED','Current independent human content review required')
            checked=_dispatch_details(details,store.service.now(),row['content']['created_at'])
            e=store._append_event(row,'manual_dispatch',dict(details=checked,sender_actor=store.actor,
                review_event_id=view['valid_review_event_id'],transmission_performed=False))
            return store.envelope(dict(recorded=True,event_id=e['event_id'],document_ref=document_ref,document_version=document_version,
                sender_actor=store.actor,transmission_performed=False),row['data_version'])
        return self._perform(context,'pp_record_manual_dispatch',args,act,permission='draft',request_key=request_key)

    def export_document(self,context,document_ref,document_version,*,expected_sha256,output_directory=None,filename=None):
        args=dict(ref=document_ref,version=document_version,expected_sha256=expected_sha256)
        def act(store):
            row=store._row(document_ref,document_version)
            if row['content']['content_sha256']!=expected_sha256:raise DomainError('DOCUMENT_CONTENT_CHANGED','Export exact displayed version')
            return store.envelope(dict(document_ref=document_ref,document_version=document_version,
                content_sha256=expected_sha256,markdown=row['markdown'],encoding='utf-8'),row['data_version'])
        result=self._perform(context,'pp_export_document',args,act)
        if not result['ok'] or output_directory is None and filename is None:return result
        try:
            if not isinstance(filename,str) or not re.fullmatch(r'[^/\\\x00-\x1f]+\.md',filename) or filename in ('.md','..md'):
                raise ValueError()
            root=Path(output_directory).resolve(strict=True)
            fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
            try:
                out=os.open(filename,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
                with os.fdopen(out,'w',encoding='utf-8',newline='\n') as stream:stream.write(result['data']['markdown'])
            finally:os.close(fd)
            result['data']['path']=str(root/filename)
        except (ValueError,TypeError,OSError):
            result.update(ok=False,data=None,errors=[dict(code='INVALID_EXPORT_PATH',message='Existing trusted directory and new .md leaf required; no overwrite')])
        return result
