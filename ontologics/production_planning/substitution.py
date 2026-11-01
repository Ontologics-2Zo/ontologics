"""Read-only substitution review. Discovery and graph review grant no authority.

No registered Agent tool, approvals store, inventory writes or mixed BOMs.
The optional applicability_reader is a trusted host integration dependency, NOT
an input dict or model tool argument. No production implementation exists in
Task05: real transitions remain blocked. Tests may supply an explicit double.
"""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path

from ..service import configuration_changes, DomainError
from .configuration_lookup import lookup_contract_configurations
from .materials import available_stock
from .planning import build_saved_production_plan

ROOT=Path(__file__).resolve().parents[2]
COMMIT='deb38a377d50eadb04ddc7118a17a5ae58b1ee53'
SOURCE_HASH='c8ee91c74caa752e5ffdd8f5a18faef3dacc2c8339b1e6a7e49f4aa1939952db'
LEFT='config:esp32-poe:L:WROOM'
RIGHT='config:esp32-poe:L1:WROOM'
U2='component:olimex-L-wroom:77be1300069a66c9'
U2_L1='component:olimex-L1-wroom:35335677b228a6d2'


def _issue(code, message):
    return dict(code=code,message=message)


def load_revision_evidence(*, root=ROOT, metadata=None):
    """Validate saved bytes/metadata; never download or replace missing sources.

    Pin is Task05's inspected revision history, not a live upstream reference.
    The retained README distinguishes documentation CC BY-SA and hardware
    Apache-2.0. This revision text is attributed as documentation; no narrower
    per-file license assertion or compatibility assertion is manufactured.
    """
    r=dict(state='unavailable',metadata=None,errors=[])
    try:
        root=Path(root).resolve()
        meta=deepcopy(metadata) if metadata is not None else json.loads((root/'data/production_planning/substitution_evidence_v1.json').read_text())
        r['metadata']=meta;s=meta['source']
        def read(path):
            p=(root/path).resolve()
            if not p.is_relative_to(root/'data'):raise ValueError('Source must remain inside data')
            return p.read_bytes()
        raw=read(s['path'])
        if s['version']!=COMMIT or s['url']!=f'https://raw.githubusercontent.com/OLIMEX/ESP32-POE/{COMMIT}/HARDWARE/Revision-changes.txt':
            raise ValueError('Pinned source commit/URL mismatch')
        if s['sha256']!=SOURCE_HASH or hashlib.sha256(raw).hexdigest()!=SOURCE_HASH:
            raise ValueError('Source bytes hash mismatch')
        if datetime.fromisoformat(s['collected_at']).utcoffset() is None:raise ValueError('Collection offset required')
        if meta['sections']!={'L1':[25,34],'L':[38,47]}:raise ValueError('Revision section mismatch')
        if meta['scope']!=dict(left=LEFT,right=RIGHT,position='U2',component_id=U2,candidate_component_id=U2_L1,related_positions=['R6','R50','R51','U2']):
            raise ValueError('Source applicability scope mismatch')
        expected_license={'data/sources/OLIMEX-README.md':'420a6bcdddc0b7ef5fcb0f3bd6b60e15e184175c49d92e1c2dbc76ed3bdfee6c',
                          'data/sources/OLIMEX-LICENSE.txt':'c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4'}
        if {x['path']:x['sha256'] for x in meta['license_basis']}!=expected_license:raise ValueError('License provenance missing')
        for p,h in expected_license.items():
            if hashlib.sha256(read(p)).hexdigest()!=h:raise ValueError('License basis bytes mismatch')
        if 'CC-BY-SA-3.0' not in s['license']:raise ValueError('Documentation license basis missing')
        nodes=meta['nodes']
        if len(nodes)!=3 or nodes[0]!=s or s['id']!='source:olimex-revisions:task05' or s['type']!='SourceDocument' or s['origin']!='real':
            raise ValueError('Source node mismatch')
        for n,rev,loc in zip(nodes[1:],('L1','L'),('Hardware revision L1; lines 25-34','Hardware revision L; lines 38-47')):
            if (n['id']!='evidence:olimex-revisions:task05:'+rev or n['type']!='Evidence' or n['origin']!='real'
                    or n['source_id']!=s['id'] or n['locator']!=loc):raise ValueError('Evidence locator mismatch')
        r['state']='acquired'
    except (OSError,ValueError,KeyError,TypeError) as exc:
        r['errors'].append(_issue('SOURCE_UNAVAILABLE_OR_MISMATCH',str(exc)))
    return r


def _envelope_errors(response, version):
    if not isinstance(response,dict) or type(response.get('ok')) is not bool:
        return [_issue('INVALID_PUBLIC_RESPONSE','Expected public envelope')]
    if not response['ok']:
        return deepcopy(response.get('errors') or [_issue('PUBLIC_QUERY_FAILED','Public query failed')])
    if type(response.get('data_version')) is not int or response['data_version']!=version:
        return [_issue('REFERENCE_VERSION_MISMATCH','Use exact public data version')]
    if response.get('is_preview') or isinstance(response.get('data'),dict) and response['data'].get('is_preview'):
        return [_issue('UNAPPLIED_PREVIEW','Preview is not a public snapshot')]
    if response.get('errors') or response.get('conflicts'):
        return deepcopy(response.get('errors',[])+response.get('conflicts',[]))
    return []


def _query_errors(q, version):
    """Validate joins again at the pure entrypoint; no valid flag shortcuts."""
    errors=deepcopy(q.get('errors',[]))
    catalog=q.get('catalog') or {}
    errors+=deepcopy(catalog.get('errors',[]))
    for response in [catalog.get('lookup_response'),*catalog.get('configuration_responses',[]),q.get('usage_response'),q.get('comparison_response')]:
        errors+=_envelope_errors(response,version)
    if errors:return errors
    try:
        if q['data_version']!=version or not catalog['configurations']:raise ValueError('Missing catalog')
        nodes=catalog['lookup_response']['data'];idx={n['id']:n for n in nodes}
        details=catalog['configurations']['candidates']
        if catalog['configurations']['data_version']!=version:raise ValueError('Catalog version mismatch')
        for cid in (q['configuration_id'],q['candidate_configuration_id']):
            d=next(d for d in details if d['configuration']['id']==cid)
            rows=[n for n in nodes if n['type']=='BOMItem' and n['configuration_id']==cid]
            if d['configuration']!=idx[cid] or sorted(d['items'],key=lambda n:n['id'])!=sorted(rows,key=lambda n:n['id']):raise ValueError('Whole BOM mismatch')
            if sorted(d['components'],key=lambda n:n['id'])!=[idx[c] for c in sorted({n['component_id'] for n in rows})]:raise ValueError('Component join mismatch')
        usage=q['usage_response']['data'];comparison=q['comparison_response']['data']
        if usage['component_id']!=q['component_id'] or usage['items']!=[n for n in nodes if n['type']=='BOMItem' and n['component_id']==q['component_id']]:raise ValueError('Exact component usage mismatch')
        if comparison['left']!=q['configuration_id'] or comparison['right']!=q['candidate_configuration_id']:raise ValueError('Configuration comparison mismatch')
        if comparison['changes']!=configuration_changes(nodes,comparison['left'],comparison['right']):raise ValueError('Position differences mismatch')
    except (KeyError,TypeError,ValueError,StopIteration,DomainError):
        errors.append(_issue('QUERY_RELATION_MISMATCH','Exact snapshot/whole BOM/component/position joins required'))
    return errors


def query_substitution(session, *, configuration_id, component_id, candidate_configuration_id, data_version):
    """Existing RequestSession calls only; retains every public envelope."""
    q=dict(configuration_id=configuration_id,component_id=component_id,candidate_configuration_id=candidate_configuration_id,
           data_version=data_version,catalog=lookup_contract_configurations(session,data_version=data_version),
           usage_response=None,comparison_response=None,errors=[])
    for key,tool,args in [('usage_response','component_usage',{'component_id':component_id}),
                         ('comparison_response','compare_configurations',{'left_id':configuration_id,'right_id':candidate_configuration_id})]:
        try:q[key]=session.call(tool,dict(args,version=data_version))
        except Exception:q['errors'].append(_issue('PUBLIC_QUERY_EXCEPTION','Host query raised; consult host audit'))
    q['errors']=_query_errors(q,data_version)
    return q


def build_substitution_review(shortage, comparisons, evidence, constraints):
    """Task03 original or Task04 virtual shortage -> bounded discovery review.

    Constraints are displayed claims, never authenticated permission. Only the
    inspected U2 transition is discovered here; unrelated shortages never inherit
    that candidate. Other-model replacement requires separate application evidence.
    """
    m,queries,docs,claims=deepcopy((shortage,comparisons,evidence,constraints))
    if m.get('purpose') not in ('material_review_only','virtual_material_review_only'):
        raise ValueError('Task03 original or explicit Task04 virtual shortage required')
    r=dict(purpose='substitution_review_only',original_materials=m,comparisons=queries,evidence=docs,constraints=claims,
        shortage_scope='virtual_remaining_scenario' if m['purpose']=='virtual_material_review_only' else 'original_material_review',
        data_version=m['data_version'],configuration_id=m['configuration_id'],candidates=[],diagnostics=[],purchase_review=[],
        states=dict(candidate_discovery='none_in_reviewed_scope',source_acquisition='unavailable',graph_evidence='not_registered',
                    technical_suitability='unverified',contract_permission='unverified',human_review='not_recorded',
                    material_calculation='blocked',production_execution='not_authorized'),
        production_authorized=False,status='검토한 자료 범위에서 사용 가능한 대안 미확보',
        reviewed_scope='Pinned OLIMEX L/L1 WROOM BOMs and revision history only; not a market-wide search',
        required_review=['Target configuration and U2 position; electrical conditions, pinout, package and surrounding circuit',
                         'R6 charge current; R50/R51 population unresolved against complete design and assembly policy',
                         'Contract L restriction, customer agreement and revision_change/substitution permissions',
                         'Fresh candidate demand/version and exact candidate-component stock coverage'],
        alternative_kinds=['same_component_other_lot','different_component_model','existing_configuration_transition'])
    if m.get('errors') or m.get('conflicts'):
        r['diagnostics']+=deepcopy(m.get('errors',[])+m.get('conflicts',[]))
        return r
    positive=[c for c in m['components'] if type(c.get('S')) is int and c['S']>0]
    for c in positive:r['purchase_review'].append(dict(component_id=c['component_id'],bom_rows=deepcopy(c['bom_rows']),shortage_quantity=c['S'],scope=r['shortage_scope'],data_version=m['data_version'],action='original_component_purchase_review_only'))
    usable=[]
    for doc in docs:
        if doc.get('state')!='acquired':r['diagnostics']+=deepcopy(doc.get('errors',[]));continue
        checked=load_revision_evidence(metadata=doc.get('metadata'))
        r['diagnostics']+=checked['errors']
        if checked['state']=='acquired':usable.append(checked)
    if usable:r['states']['source_acquisition']='acquired'
    else:r['diagnostics'].append(_issue('REVISION_EVIDENCE_REQUIRED','Acquire and verify pinned revision history and license basis'))
    for q in queries:
        errors=_query_errors(q,m['data_version']);r['diagnostics']+=errors
        if errors or not usable:continue
        idx={n['id']:n for n in q['catalog']['lookup_response']['data']}
        if all(idx.get(n['id'])==n for n in usable[0]['metadata']['nodes']):
            r['states']['graph_evidence']='registered'
        if (m['configuration_id'],q['configuration_id'],q['candidate_configuration_id'],q['component_id'])!=(LEFT,LEFT,RIGHT,U2):continue
        matched=[c for c in positive if c['component_id']==U2 and any(b['id']=='bom:esp32-poe:L:WROOM:row-57' and b['component_id']==U2 and b['configuration_id']==LEFT for b in c['bom_rows'])]
        if not matched:continue
        target=idx['bom:esp32-poe:L1:WROOM:row-56']
        if target['component_id']!=U2_L1 or target['positions']!=['U2']:continue
        if not r['candidates']:
            r['candidates'].append(dict(kind='existing_configuration_transition',configuration_id=RIGHT,component_id=U2_L1,
                source_component_id=U2,bom_item=deepcopy(target),direct_exchange_allowed=False,
                related_changes=[deepcopy(c) for c in q['comparison_response']['data']['changes'] if c['position'] in ('U2','R6','R50','R51')],
                evidence_ids=[n['id'] for n in usable[0]['metadata']['nodes'][1:]],
                state='candidate_only',recalculation_blockers=['Technical applicability unavailable','Customer agreement/contract permission unverified','Candidate demand and exact L1 stock not established']))
            r['states']['candidate_discovery']='candidate_found'
    return r


def review_same_component_lots(snapshots, scope, *, component_id):
    """Stock acquisition review, not electrical substitution or extra allocation.

    Calls Task03 once. Report retains all exclusions/conflicts/coverage. This does
    not add its amount to an existing calculation and does not certify graph lots.
    """
    stock=available_stock(snapshots,scope)
    return dict(kind='same_component_other_lot',component_id=component_id,stock=stock,
                available_quantity=stock['components'].get(component_id,{}).get('available_quantity'),
                production_authorized=False,graph_lot_verification='requires_saved_demand_host_path')


def recalculate_configuration_review(workflow, host, context, review, *, candidate_configuration_id,
        demand_id, item_id, data_version, snapshots, scope, initial_quantity,
        expected_revision, expected_target_sha256, applicability_reader=None):
    """Trusted host seam; never interpret a caller's approved/confirmed dict.

    read_current(context, binding) must obtain current technical, contractual and
    human review records from an existing trusted authority with current rights.
    Task02 cannot provide these: default blocks. Test doubles exercise the seam,
    not real approvals. Records must bind exact demand/version/N/I/stock scope;
    accepted records need state='accepted' and a nonempty evidence_refs list.
    No new persistence/signature/approval scheme is implemented here.
    """
    r=dict(recalculation=None,demand_response=None,applicability_response=None,blockers=[],production_authorized=False)
    def block(code,message):r['blockers'].append(_issue(code,message))
    if applicability_reader is None or not callable(getattr(applicability_reader,'read_current',None)):
        block('APPLICABILITY_READER_UNAVAILABLE','Existing Task02 confirms fields only; no technical/contract applicability reader')
    if type(data_version) is not int or type(expected_revision) is not int or expected_revision<1 or not isinstance(expected_target_sha256,str) or len(expected_target_sha256)!=64:
        block('EXACT_TARGET_REQUIRED','Exact version/revision/target hash required')
    if not isinstance(snapshots,list) or any(not isinstance(s,dict) or s.get('state')=='virtual' or s.get('purpose')=='virtual_material_review_only' or 'virtual_deductions' in s or 'original_stock' in s for s in snapshots):
        block('ORIGINAL_STOCK_REQUIRED','Use original observation documents, never virtual residuals')
    original=review.get('original_materials',{})
    original_stock=original.get('stock',{})
    if (review.get('shortage_scope')=='virtual_remaining_scenario'
            or original.get('purpose')!='material_review_only'
            or original_stock.get('state')=='virtual'
            or 'virtual_deductions' in original_stock or 'original_stock' in original_stock):
        block('ORIGINAL_SCENARIO_REQUIRED','Select an independent full original N scenario, not virtual remaining consumption')
    # Rebuild discovery instead of trusting mutated candidate/valid flags.
    try:
        with host.request(context) as session:
            fresh=query_substitution(session,configuration_id=review['configuration_id'],component_id=U2,
                                     candidate_configuration_id=candidate_configuration_id,data_version=data_version)
    except Exception:
        block('PUBLIC_QUERY_FAILED','Fresh exact host query failed');return r
    r['query']=fresh
    checked=build_substitution_review(review['original_materials'],[fresh],review['evidence'],review['constraints'])
    r['blockers']+=deepcopy(checked['diagnostics'])
    if checked['data_version']!=data_version or not any(c['configuration_id']==candidate_configuration_id for c in checked['candidates']):
        block('CANDIDATE_VERSION_MISMATCH','Supported candidate at exact data version required')
    if checked['states']['graph_evidence']!='registered':block('EVIDENCE_NOT_REGISTERED','Exact source/evidence nodes must be registered')
    try:response=workflow.get(context,demand_id)
    except Exception:
        block('DEMAND_QUERY_FAILED','Fresh host demand lookup failed');return r
    r['demand_response']=response;r['blockers']+=_envelope_errors(response,data_version)
    if not response.get('ok'):return r
    try:
        d=response['data'];item=next(i for i in d['items'] if i['contract_item_candidate']['id']==item_id)
        if d['revision']!=expected_revision or d['current_revision']!=expected_revision or d['target_sha256']!=expected_target_sha256 or d['status']=='superseded':block('DEMAND_TARGET_CHANGED','Reload exact current candidate')
        if item['contract_item_candidate']['configuration_id']!=candidate_configuration_id:block('CANDIDATE_DEMAND_REQUIRED','Original L demand cannot be relabelled L1')
        if not d['confirmation']['verified']:block('DEMAND_CONFIRMATION_PENDING','Current host confirmation required')
        if item['permissions'].get('revision_change') is not True or item['permissions'].get('substitution') is not True:block('CONTRACT_PERMISSION_UNRESOLVED','Customer agreement and permissions must be resolved by host')
        n=item['planned_input_quantity']
        if n!=checked['original_materials']['planned_input_quantity']:block('QUANTITY_BASIS_MISMATCH','Independent comparison retains original explicit N')
        if type(initial_quantity) is not int or type(n) is not int or not 0<=initial_quantity<=n:block('INVALID_INITIAL_QUANTITY','Explicit 0 <= I <= N required')
    except (KeyError,TypeError,StopIteration):block('INVALID_DEMAND_RESPONSE','Complete current saved candidate required')
    if r['blockers']:return r
    binding=dict(demand_id=demand_id,item_id=item_id,data_version=data_version,revision=expected_revision,target_sha256=expected_target_sha256,
                 configuration_id=candidate_configuration_id,source_configuration_id=checked['configuration_id'],planned_input_quantity=n,initial_quantity=initial_quantity,
                 stock_scope=deepcopy(scope),source_sha256=SOURCE_HASH)
    try:approval=applicability_reader.read_current(context,deepcopy(binding))
    except Exception:
        block('APPLICABILITY_QUERY_FAILED','Cannot refresh current applicability/permissions');return r
    r['applicability_response']=deepcopy(approval)
    if not isinstance(approval,dict) or approval.get('binding')!=binding:
        block('APPLICABILITY_BINDING_MISMATCH','Exact host review binding required');return r
    for name in ('technical','contract','human'):
        record=approval.get(name,{})
        if record.get('state')!='accepted' or not isinstance(record.get('evidence_refs'),list) or not record['evidence_refs'] or any(not isinstance(x,str) or not x for x in record['evidence_refs']):
            block('APPLICABILITY_PENDING_OR_REJECTED',name+' requires current accepted evidence')
    if r['blockers']:return r
    result=build_saved_production_plan(workflow,host,context,initial_quantity=initial_quantity,
        demand_id=demand_id,item_id=item_id,data_version=data_version,snapshots=deepcopy(snapshots),scope=deepcopy(scope),
        expected_revision=expected_revision,expected_target_sha256=expected_target_sha256)
    r['recalculation']=result
    if result['plan'] is None:block('RECALCULATION_BLOCKED','Inspect retained Task02/03 envelopes and errors')
    elif (result['plan']['configuration_id']!=candidate_configuration_id or not result['calculation']['bom_complete']
          or result['plan']['confirmation_status']!='verified_by_current_host'
          or result['calculation']['planned_input_quantity']!=n
          or result['calculation']['demand_source']['permissions']!=item['permissions']):
        r['recalculation']=None;block('DEMAND_CHANGED_DURING_RECALCULATION','Fresh demand no longer matches applicability')
    return r
