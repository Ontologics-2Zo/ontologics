"""Separate current host facts from a rejected model copy; no response repair.

Trusted application entry, not an Agent tool or a replacement Runtime. Existing
workflow owns calculation, storage, identity and model execution. The raw model
result stays rejected and unchanged. Only a fresh, matching host snapshot is
returned separately. This does not accept arbitrary incomplete source data.
"""
from copy import deepcopy

from .agent import _snapshot, _json
from .agent_workflow import run_review, _current, compact_task


def run_review_preserving_host(host, demands, store, identity, *, calculation_arguments,
                               request_key, provider_factory, config,
                               resume_scopes=None, cancelled=False):
    """Return {status, host_result, model_review}; no ambiguous overall accepted.

    Consumers render host_result.answer as authoritative conditional data. They
    must NOT render rejected model_review.runtime.text as verified explanation.
    Recover only an exact-copy mismatch, not permissions, tool or runtime errors.
    Calculation is never repeated just to recover a model transcription failure.
    """
    args=deepcopy(calculation_arguments)
    scopes=deepcopy(resume_scopes)
    captured={}

    def current(refs):
        demand,issues=_current(host,demands,identity,args,scopes)
        if issues:raise ValueError('SOURCE_OR_STORED_DATA_UNRESOLVED')
        snapshot,confirmation=_snapshot(host,demands,store,identity,
            demand_id=args['demand_id'],data_version=args['data_version'],
            expected_revision=args['expected_revision'],
            expected_target_sha256=args['expected_target_sha256'],
            document_refs=refs,resume_scopes=scopes)
        return compact_task(snapshot,demand),confirmation

    def capture(task,tools):
        # Capture only host-built task; never derive authoritative values from
        # provider text, including fields apparently copied without changes.
        snapshot,confirmation=current(task['documents'])
        if _json(snapshot)!=_json(task):raise ValueError('CONTEXT_CHANGED')
        captured.update(task=deepcopy(task),confirmation=deepcopy(confirmation))
        return provider_factory(task,tools)

    model=run_review(host,demands,store,identity,calculation_arguments=args,
        request_key=request_key,provider_factory=capture,config=config,
        resume_scopes=scopes,cancelled=cancelled)
    result=dict(status='blocked',host_result=dict(available=False,answer={},origin='host_workflow'),
                model_review=model,delivery=dict(recipient='requesting_user',transmitted=False))
    if not captured or not (model.get('accepted') is True or model.get('errors')==['PLANNING_FACT_MISMATCH']):
        return result
    try:
        fresh,confirmation=current(captured['task']['documents'])
        if _json(fresh)!=_json(captured['task']) or _json(confirmation)!=_json(captured['confirmation']):
            raise ValueError('CONTEXT_CHANGED')
    except Exception:
        # Preserve rejected raw model diagnostic, never surface stale host facts.
        result['host_result']['errors']=['HOST_RESULT_REVALIDATION_FAILED']
        return result
    result['host_result'].update(available=True,answer=deepcopy(fresh),
        current_state_revalidated=True,model_authored=False)
    result['status']='review_ready' if model['accepted'] else 'host_result_available_model_rejected'
    return result
