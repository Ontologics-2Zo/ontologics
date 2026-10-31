"""New-only Task09 workflow: inspect facts, host calculation/store, model review.

One registered role, fresh runs. Existing Runtime/provider/tools/confirmation and
storage remain authoritative. Model output NEVER chooses executable host actions.
Pure consistency checks cannot authenticate caller dictionaries; use host entries.
"""
from copy import deepcopy
import json
from pathlib import Path
import re

from ..host import AgentRegistration
from ..identity import IdentityError
from ..tool_specs import function_tools
from .agent import (PLANNING_TOOLS, PLANNING_PROMPT, build_planning_task,
                    evaluate_planning_result, _failure, _snapshot, _json, _data)
from .contract_interpretation import INSTRUCTIONS, interpretation_messages, parse_interpretation, InterpretationError
from .contract_intake import extract_contract_text, read_contract_demand
from .configuration_lookup import lookup_contract_configurations

REGISTRATION=AgentRegistration('production_planning_review',
    '''You are the production planning REVIEW agent. Follow the phase in the HOST
user task. Phase interpretation: extract source claims using the rules below.
Phase planning_review: return the exact eight task fields plus evidence/error as
specified by the review rules below. The wrapper {phase,task} is not the answer.
For conflicting quantities/IDs/units, request DATA INSPECTION by the data owner;
never ask the requester to choose a factual value. Corrections must be recorded
through the existing source/demand workflow before a new run. Missing price or
technical review may remain explicit in a conditional review-ready document.
Interpretation phase rules (ONLY when phase=interpretation):\n<interpretation>'''+INSTRUCTIONS+'</interpretation>\nPlanning review phase rules (ONLY when phase=planning_review):\n<planning_review>'+PLANNING_PROMPT+'</planning_review>\nApply ONLY the section matching the host phase. Inactive section output schemas and tool rules do not apply.',
    PLANNING_TOOLS)
REQUIRED=('contract_item_id','ordered_quantity','fulfilled_quantity','allocated_quantity','planned_input_quantity','unit')


def _issue(code,field,source_value=None,stored_value=None,**details):
    return dict(code=code,field=field,source_value=source_value,stored_value=stored_value,
        owner='data_owner',action='계약 원문·항목·수정 이력·저장 데이터를 점검하고 기존 정정 절차에 반영해 주세요. 사실 값을 임의 선택하지 않습니다.',**details)


def inspect_report(report,public_items):
    """Compare identical contract ITEM scope only; never merge deliveries/totals.

    This is a consistency check over host-fetched data, not authorization or a
    replacement for original source extraction/quantity validators.
    """
    issues=[]
    if report is None:return [_issue('SOURCE_REPORT_MISSING','report')]
    for error in report.get('errors',[]):issues.append(_issue(error['code'],error.get('path','report')))
    if not report.get('items'):issues.append(_issue('SOURCE_ITEMS_MISSING','items'))
    for item in report.get('items',[]):
        for name,field in item['fields'].items():
            if field['state'] in ('conflict','invalid','unsupported') or (name in REQUIRED and field['state']!='supported'):
                issues.append(_issue('SOURCE_FIELD_UNRESOLVED',name,claims=deepcopy(field['claims']),source=deepcopy(report['source'])))
        c=item['contract_item_candidate'];cid=c['id']
        if not c.get('configuration_id'):
            issues.append(_issue('CONFIGURATION_UNRESOLVED','configuration_id',candidates=deepcopy(item['configuration_candidates'])))
        matches=[n for n in public_items if n.get('id')==cid]
        if len(matches)!=1:
            issues.append(_issue('STORED_ITEM_UNRESOLVED','contract_item_id',cid,stored_count=len(matches)));continue
        stored=matches[0]
        for source_key,stored_key in (('ordered_quantity','quantity'),('configuration_id','configuration_id'),('unit','unit')):
            if _json(c.get(source_key))!=_json(stored.get(stored_key)):
                issues.append(_issue('STORED_FACT_CONFLICT',source_key,c.get(source_key),stored.get(stored_key),
                    item_id=cid,source=deepcopy(report['source']),stored_evidence=deepcopy(stored.get('evidence',[]))))
    return issues


def interpret_source(host,identity,path,*,data_version,provider,config):
    """Model interpretation of an explicitly host-selected source, no save/confirm."""
    if config.max_steps!=1:raise ValueError('Interpretation is one bounded model step')
    extracted=extract_contract_text(Path(path))
    with host.request(identity) as session:
        query=lookup_contract_configurations(session,data_version=data_version)
        if query['configurations'] is None:
            return dict(accepted=False,errors=['PUBLIC_QUERY_FAILED'],runtime=None,next_action='data_check',data_checks=[])
        messages=interpretation_messages(extracted,data_version=data_version,configurations=query['configurations'])
        messages[0]['content']=_json(dict(phase='interpretation',active_rules='Use ONLY interpretation rules. Return items with fields mapping to claim lists; no planning answer fields. Do not call tools. Preserve every explicit conflicting claim.',task=json.loads(messages[0]['content'])))
        runtime=session.run_agent(REGISTRATION.name,provider,messages,config=config,capture_tool_trace=True)
        result=dict(accepted=False,runtime=runtime,interpretation=None,report=None,errors=[],data_checks=[],
            next_action='data_check',human_confirmation=False,source_sha256=extracted['source']['sha256'])
        if not runtime['ok']:
            result['errors']=[runtime['error']['code']];return result
        if runtime['tool_trace']:
            result['errors']=['INTERPRETATION_TOOL_UNEXPECTED'];return result
        try:
            interpreted=parse_interpretation(runtime['text'],extracted,data_version=data_version)
            demand=read_contract_demand(session,{'data_version':data_version},extracted,interpreted)
            public=session.call('lookup',{'kind':'ContractItem','version':data_version})
            issues=inspect_report(demand['report'],_data(public))
            latest=session.call('lookup',{'kind':'ContractItem'})
            _data(latest)
            if latest['data_version']!=data_version:issues.append(_issue('PUBLIC_VERSION_CHANGED','data_version'))
            if extract_contract_text(Path(path))['source']['sha256']!=extracted['source']['sha256']:
                issues.append(_issue('SOURCE_CHANGED','source_sha256'))
            result.update(accepted=not issues,interpretation=interpreted,report=demand['report'],data_checks=issues,
                errors=sorted({i['code'] for i in issues}),next_action='host_prepare_candidate' if not issues else 'data_check')
        except (InterpretationError,ValueError) as exc:
            result['errors']=[getattr(exc,'code','INTERPRETATION_REJECTED')]
        session.record_agent_check(runtime['run_id'],accepted=result['accepted'],code=None if result['accepted'] else 'PLANNING_INTERPRETATION_REJECTED')
        return result


def _current(host,demands,identity,args,resume_scopes):
    demand=_data(demands.get(identity,args['demand_id']))
    if demand['revision']!=args['expected_revision'] or demand['target_sha256']!=args['expected_target_sha256']:
        raise ValueError('DEMAND_TARGET_CHANGED')
    if resume_scopes is not None and (not resume_scopes or any(s not in demand['confirmation']['verified_scopes'] for s in resume_scopes)):
        raise ValueError('CONFIRMATION_REQUIRED')
    with host.request(identity) as session:
        public=session.call('lookup',{'kind':'ContractItem'})
        nodes=_data(public)
        if public['data_version']!=args['data_version']:raise ValueError('PUBLIC_VERSION_CHANGED')
    issues=inspect_report(demand['intake_report'],nodes)
    if args['item_id'] not in {i['contract_item_candidate']['id'] for i in demand['items']}:
        issues.append(_issue('DEMAND_ITEM_MISMATCH','item_id',args['item_id']))
    return demand,issues


def compact_task(task,demand):
    """Host projection, never model arithmetic. Full provenance stays in documents."""
    f=task['facts'];q=f['quantities'];scope=f['scope'];sched=f['schedule'];proc=f['procurement']
    follow=[dict(owner='production_planner',action='기존 수요 확인 절차에서 미확인 범위를 검토',pending_scopes=demand['confirmation']['pending_scopes']),
        dict(owner='technical_reviewer',action='실장 미확인 및 대체 적합성 검토; 적용 승인 아님'),
        dict(owner='contract_owner',action='개정 변경·대체 허용 및 고객 도착 조건 확인'),
        dict(owner='inventory_owner',action='전체 BOM 재고와 실제 사용 가능 상태 확인'),
        dict(owner='purchasing_contact',action='원 부품 견적·MOQ·주문 배수·납기 확인; 발주 아님')]
    facts=dict(quantities=q,scope={k:scope[k] for k in ('bom_rows','unknown_population_rows','unknown_stock_components','whole_bom_material_limit')},
        components=[{k:c[k] for k in ('component_id','q','A','D','S','virtual_used_quantity','remaining_available_quantity','remaining_required_quantity','remaining_shortage')} for c in f['components']],
        procurement=dict(request_kind='견적·납기 확인 요청',items=[{k:i.get(k) for k in ('component_id','shortage_quantity','purchase_quantity','subtotal')} for i in proc['purchase_review']],total_by_currency=proc['total_by_currency'],known_item_totals=proc['known_item_totals']),
        schedule=dict(initial=sched['initial'],remaining=sched['remaining'],initial_assumption=sched.get('initial_conditions'),customer_arrival_compliance=sched['customer_arrival_compliance']),
        authority=f['authority'],follow_up=follow,confirmation_verified=demand['confirmation']['verified'])
    return build_planning_task(dict(target=task['target'],source=dict(sha256=demand['source']['sha256']),
        candidates=[],questions=[],facts=facts,documents=task['documents'],status='review_ready',conditional=True))


def run_review(host,demands,store,identity,*,calculation_arguments,request_key,provider_factory,config,
               resume_scopes=None,cancelled=False):
    """Fixed host workflow, not a second model-command executor.

    Calculation/store happen only after current source/data checks. A model may
    select allowed READ tools then explain exact host output. No human events.
    """
    args=deepcopy(calculation_arguments);runtime=None
    task=dict(target={'data_version':args.get('data_version')},source={},candidates=[],questions=[],facts={},documents=[],status='failed',conditional=True)
    try:
        if cancelled:return _failure(task,'CANCELLED')
        demand,issues=_current(host,demands,identity,args,resume_scopes)
        if issues:
            task.update(status='awaiting_confirmation',questions=issues,source={'sha256':demand['source']['sha256']},
                candidates=[i['configuration_candidates'] for i in demand['items']])
            return dict(accepted=False,answer=task,runtime=None,errors=[i['code'] for i in issues],
                next_action='data_check',facts_origin='host_workflow',delivery={'recipient':'requesting_user','transmitted':False})
        created=_data(store.create_documents(identity,calculation_arguments=args,request_key=request_key))
        refs=created['documents']
        task['documents']=[dict(document_ref=d['document_ref'],document_version=d['document_version'],content_sha256=d['content_sha256'],binding_current=False,state={}) for d in refs]
        snap_args=dict(demand_id=args['demand_id'],data_version=args['data_version'],expected_revision=args['expected_revision'],
            expected_target_sha256=args['expected_target_sha256'],document_refs=refs,resume_scopes=resume_scopes)
        snapshot,confirmation=_snapshot(host,demands,store,identity,**snap_args)
        task=compact_task(snapshot,demand)
        provider=provider_factory(deepcopy(task),function_tools(sorted(PLANNING_TOOLS)))
        with host.request(identity) as session:
            runtime=session.run_agent(REGISTRATION.name,provider,[{'role':'user','content':_json(dict(phase='planning_review',active_rules='Use ONLY planning_review rules. You MUST first call configuration or lookup for target.configuration_id at target.data_version, then cite successful tool_run_id and Evidence IDs. Copy the eight task fields exactly. Interpretation no-tool rules are INACTIVE.',task=task))}],config=config,capture_tool_trace=True)
        result=evaluate_planning_result(task,runtime,{})
        if not result['accepted']:return result
        fresh,issues=_current(host,demands,identity,args,resume_scopes)
        snap,confirmed=_snapshot(host,demands,store,identity,**snap_args)
        if issues or _json(compact_task(snap,fresh))!=_json(task) or _json(confirmation)!=_json(confirmed):
            return _failure(task,'CONTEXT_CHANGED',runtime)
        result.update(next_action='human_document_review',host_workflow=['inspect_source_and_saved_contract','create_documents_existing_Task02_to_08','get_document_current'],
            delivery={'recipient':'requesting_user','format':'returned_result_and_document_files','transmitted':False})
        return result
    except IdentityError as exc:return _failure(task,exc.code,runtime)
    except (ValueError,KeyError,TypeError) as exc:
        code=str(exc) if re.fullmatch('[A-Z][A-Z_]{1,80}',str(exc)) else 'HOST_CONTEXT_INVALID'
        return _failure(task,code,runtime)
    except Exception:return _failure(task,'HOST_WORKFLOW_ERROR',runtime)
