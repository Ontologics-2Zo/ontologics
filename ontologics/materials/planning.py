"""Independent, read-only virtual production reviews; never operational authority.

The pure API accepts an explicit demand binding (configuration_id, data_version,
planned_input_quantity, unit, demand_source). Optional target_quantity denotes
order quantity only with target_quantity_basis='ordered_quantity'. No missing
fulfillment/allocation is defaulted or used to derive N. Dict provenance is
caller evidence, never authentication; saved candidates use the host entrypoint.
"""
from copy import deepcopy
import json

from .materials import available_stock, calculate_material_requirements, calculate_saved_demand
from .rules import calculation_rule_version


def _integer(value):
    return type(value) is int and value >= 0


def _same(a, b):
    return json.dumps(a, sort_keys=True, allow_nan=False) == json.dumps(b, sort_keys=True, allow_nan=False)


def _validate_materials(m):
    """Check derivations with Task03, not caller valid/confirmed flags.

    Re-select the supplied selected observations in their original scope to
    validate eligibility and amounts. Original excluded/errors remain in m;
    excluded observations are never allocation candidates. This is consistency
    checking, not certification that a dict contains the original full source.
    """
    if (m.get('purpose') != 'material_review_only' or m['stock'].get('state') == 'virtual'
            or m.get('errors') or m.get('conflicts') or m['stock']['errors'] or m['stock']['conflicts']
            or m['calculation']['rule_version'] != calculation_rule_version('materials')):
        raise ValueError('Original, error-free Task03 material calculation required')
    stock=m['stock']; docs=[]; seen=set()
    for selected in stock['selected']:
        row=selected['observation']; key=row['lot_id']
        if key in seen: raise ValueError('Duplicate selected lot')
        seen.add(key)
        docs.append(dict(source_ref=selected['source']['source_ref'],sha256=selected['source']['sha256'],
                         stock_scope=selected['source']['document_scope'],stocks=[row]))
    checked=available_stock(docs,stock['scope'])
    if checked['errors'] or checked['conflicts'] or not _same(checked['components'],stock['components']):
        raise ValueError('Stock selection or aggregate mismatch')
    selected_by_id={s['observation']['id']:s for s in checked['selected']}
    if len(selected_by_id)!=len(stock['selected']): raise ValueError('Ineligible stock selection')
    for s in stock['selected']:
        other=selected_by_id.get(s['observation']['id'])
        if other is None or any(not _same(s[k],other[k]) for k in ('observation','available_quantity','state','age_seconds')):
            raise ValueError('Inconsistent selected observation')
    items=[row for c in m['components'] for row in c['bom_rows']]
    # Preserve excluded not-populated rows, including entirely excluded groups.
    items += [e['row'] for e in m['excluded_bom']]
    checked=calculate_material_requirements(items,stock,m['planned_input_quantity'],
        configuration_id=m['configuration_id'],data_version=m['data_version'],
        demand_source=m['demand_source'],bom_complete=m['bom_complete'])
    if checked['errors'] or checked['conflicts']:
        raise ValueError('Recomputed BOM has errors or conflicts')
    for key in ('unresolved','questions'):
        canonical=lambda values: sorted(json.dumps(v,sort_keys=True,allow_nan=False) for v in values)
        if canonical(m[key]) != canonical(checked[key]):
            raise ValueError('Material diagnostics mismatch: '+key)
    for key in ('components','conditional_upper_bound','whole_bom_material_limit','evaluated_component_ids'):
        if not _same(m[key],checked[key]): raise ValueError('Material derivation mismatch: '+key)
    return items


def build_production_plan(demand: dict, materials: dict, initial_quantity: int, *, review_state='comparison') -> dict:
    """I is explicit, never clamped. Unknown components remain unallocated.

    quantity_class: full/partial/zero, independent of review/confirmation status.
    A selected review is still a disposable calculation, with no persistence.
    Remaining materials are explicitly virtual and rejected as new base input.
    """
    d,m=deepcopy(demand),deepcopy(materials)
    n=d.get('planned_input_quantity')
    if not _integer(n) or not _integer(initial_quantity) or initial_quantity>n:
        raise ValueError('Explicit integer quantities 0 <= I <= N required')
    if review_state not in ('comparison','selected'): raise ValueError('Explicit review state required')
    if d.get('unit')!='piece' or not _integer(d.get('data_version')):
        raise ValueError('Exact version and piece unit required')
    for key in ('configuration_id','planned_input_quantity','data_version','demand_source'):
        if key not in d or key not in m or not _same(d[key],m[key]): raise ValueError('Demand/material binding mismatch: '+key)
    source=d['demand_source']
    if not isinstance(source,dict) or not source:
        raise ValueError('Explicit demand provenance required')
    if ('data_version' in source and not _same(source['data_version'],d['data_version'])
            or source.get('status')=='superseded'
            or 'revision' in source and not _same(source['revision'],source.get('current_revision'))):
        raise ValueError('Demand provenance version is inconsistent')
    basis=source.get('quantity_basis',{})
    if 'value' in basis and not _same(basis['value'],n):
        raise ValueError('Quantity evidence does not match N')
    if 'target_quantity' in d and (not _integer(d['target_quantity']) or d.get('target_quantity_basis')!='ordered_quantity'):
        raise ValueError('Target must explicitly denote ordered quantity')
    try: items=_validate_materials(m)
    except (KeyError,TypeError) as exc: raise ValueError('Incomplete Task03 result') from exc
    p=m['conditional_upper_bound']
    if p is not None and initial_quantity>p: raise ValueError('I exceeds evaluated material limit')
    remaining=n-initial_quantity
    virtual=dict(state='virtual',scope=deepcopy(m['stock']['scope']),components=deepcopy(m['stock']['components']),
                 selected=[],excluded=[],errors=[],conflicts=[],unresolved=deepcopy(m['stock']['unresolved']),
                 original_stock=deepcopy(m['stock']),virtual_deductions=[],
                 limitations=['Derived scenario balances, not stock observations'],valid=True)
    components=[]
    for c in m['components']:
        cid,q,a=c['component_id'],c['q'],c['A']; used=None;lots=[]
        if q is not None and a is not None:
            used=initial_quantity*q; pending=used
            candidates=sorted((s for s in m['stock']['selected'] if s['observation']['component_id']==cid),
                              key=lambda s:s['observation']['lot_id'])
            for s in candidates:
                if s['state']!='known' or not _integer(s['available_quantity']): raise ValueError('Ineligible lot')
                amount=min(pending,s['available_quantity']);pending-=amount
                lots.append(dict(lot_id=s['observation']['lot_id'],component_id=cid,available_quantity=s['available_quantity'],
                    used_quantity=amount,remaining_available_quantity=s['available_quantity']-amount,
                    original_selection=deepcopy(s),state='virtual'))
            if pending or sum(l['available_quantity'] for l in lots)!=a: raise ValueError('Lot balance mismatch')
            virtual['components'][cid].update(available_quantity=a-used,observed_available_quantity=None,state='virtual')
            virtual['virtual_deductions'].extend(deepcopy(lots))
        else:
            # A known stock with unknown q cannot become a confirmed residual.
            virtual['components'].setdefault(cid,{})['available_quantity']=None
        components.append(dict(deepcopy(c),virtual_used_quantity=used,lots=lots,
                               remaining_available_quantity=a-used if used is not None else None))
    residual=calculate_material_requirements(items,virtual,remaining,configuration_id=m['configuration_id'],
        data_version=m['data_version'],demand_source=m['demand_source'],bom_complete=m['bom_complete'])
    residual['purpose']='virtual_material_review_only'
    by_id={c['component_id']:c for c in residual['components']}
    for c in components:
        r=by_id[c['component_id']]
        c.update(remaining_required_quantity=r['D'],remaining_shortage=r['S'])
    return dict(purpose='production_review_only',state='virtual',review_state=review_state,
        comparison_basis='independent_original_inputs',production_authorized=False,
        confirmation_status='unverified',reported_confirmation=deepcopy(m['demand_source'].get('confirmation')),
        review_status='conditional',
        quantity_class='zero' if initial_quantity==0 else 'full' if initial_quantity==n else 'partial',
        target_quantity=d.get('target_quantity'),target_quantity_basis=d.get('target_quantity_basis'),
        planned_input_quantity=n,initial_quantity=initial_quantity,remaining_quantity=remaining,
        configuration_id=m['configuration_id'],data_version=m['data_version'],unit='piece',
        conditional_upper_bound=p,whole_bom_material_limit=m['whole_bom_material_limit'],
        evaluated_component_ids=deepcopy(m['evaluated_component_ids']),
        calculation=dict(rule_version=calculation_rule_version('production')),
        demand=d,input_materials=m,components=components,remaining_materials=residual,
        unresolved=deepcopy(m['unresolved']),questions=deepcopy(m['questions']),
        limitations=['No production authorization, reservation, issue, consumption or work creation',
                     'Pure dicts cannot authenticate demand confirmation or permissions',
                     'lot_id order only, not FIFO or quality/shelf-life optimization'])


def build_saved_production_plan(workflow, host, context, *, initial_quantity: int,
        review_state='comparison', **material_arguments) -> dict:
    """Fresh Task02 get + Task03 public lookup every call; preserve envelopes."""
    result=calculate_saved_demand(workflow,host,context,**material_arguments)
    result['plan']=None
    m=result['calculation']
    if m is None: return result
    item=next(i for i in result['demand_response']['data']['items']
              if i['contract_item_candidate']['id']==material_arguments['item_id'])
    candidate=item['contract_item_candidate']
    d=dict(configuration_id=m['configuration_id'],data_version=m['data_version'],
           planned_input_quantity=m['planned_input_quantity'],unit=candidate['unit'],demand_source=deepcopy(m['demand_source']),
           contract_item_candidate=deepcopy(candidate),quantity_fields=deepcopy(item['fields']))
    if candidate.get('ordered_quantity') is not None:
        d.update(target_quantity=candidate['ordered_quantity'],target_quantity_basis='ordered_quantity')
    result['plan']=build_production_plan(d,m,initial_quantity,review_state=review_state)
    verified=m['demand_source']['confirmation']['verified']
    result['plan']['confirmation_status']='verified_by_current_host' if verified else 'unverified'
    if verified and not m['unresolved'] and not m['demand_source']['unresolved']:
        result['plan']['review_status']='review_only'
    return result
