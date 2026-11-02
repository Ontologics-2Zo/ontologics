"""Read-only procurement estimates; caller evidence is never order authority.

All-units prices only, tier bounds [minimum, maximum). Currency quanta are an
explicit caller policy, not inferred legal currency rules. No FX or split orders.
See docs/MATERIALS.md for the complete business input contract.
"""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, Context, InvalidOperation, DivisionByZero, Overflow, localcontext, ROUND_HALF_UP
import re

from .rules import calculation_rule_version


def _integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError('Explicit integer quantity outside allowed range')
    return value


def purchase_quantity(shortage: int, minimum_order: int, order_multiple: int) -> int:
    """S/MOQ >= 0, multiple >= 1; validate even when S=0. No upper bound."""
    _integer(shortage);_integer(minimum_order);_integer(order_multiple,1)
    return 0 if shortage == 0 else ((max(shortage,minimum_order)+order_multiple-1)//order_multiple)*order_multiple


def _text(value):
    if not isinstance(value,str) or not value.strip():raise ValueError('Nonempty exact identifier required')
    return value


def _time(value):
    if not isinstance(value,str) or 'T' not in value:raise ValueError('Offset-aware timestamp required')
    dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    if dt.utcoffset() is None:raise ValueError('Offset-aware timestamp required')
    return dt


def _decimal(value):
    if type(value) not in (str,int,Decimal):raise ValueError('Money accepts decimal string, int or Decimal; never float/bool')
    try:d=Decimal(value)
    except InvalidOperation as exc:raise ValueError('Invalid decimal') from exc
    if not d.is_finite() or d<0:raise ValueError('Finite nonnegative money required')
    if len(d.as_tuple().digits)>24 or not -12<=d.as_tuple().exponent<=12:
        raise ValueError('Money limited to 24 digits and exponent -12..12')
    return d


def _serial(value):
    if isinstance(value,Decimal):return str(value)
    if isinstance(value,dict):return {k:_serial(v) for k,v in value.items()}
    if isinstance(value,list):return [_serial(v) for v in value]
    return deepcopy(value)


def _quote(o, version):
    if not isinstance(o,dict):raise ValueError('Offer object required')
    for k in ('component_id','offer_id','supplier_id'):_text(o.get(k))
    source=o.get('source',{})
    if not isinstance(source,dict) or not isinstance(source.get('sha256'),str) or not re.fullmatch('[0-9a-f]{64}',source['sha256']):
        raise ValueError('Source SHA-256 required')
    for k in ('locator','source_ref'):_text(source.get(k))
    if type(o.get('data_version')) is not int or o['data_version']!=version:raise ValueError('Exact offer review data_version required')
    if o.get('unit')!='piece' or o.get('pricing_method')!='all_units':raise ValueError('Only piece/all_units pricing supported')
    if not isinstance(o.get('currency'),str) or not re.fullmatch('[A-Z]{3}',o['currency']):raise ValueError('Explicit currency code required')
    for k,minimum in [('minimum_order',0),('order_multiple',1),('available_quantity',0)]:
        if o.get(k) is not None:_integer(o[k],minimum)
    _integer(o.get('price_basis_quantity'),1)
    if _time(o.get('valid_from'))>=_time(o.get('valid_until')):raise ValueError('Nonempty validity interval required')
    inclusion=o.get('cost_inclusion',{})
    if not isinstance(inclusion,dict) or any(inclusion.get(k) not in ('included','excluded','unknown') for k in ('shipping','tax')):
        raise ValueError('Explicit shipping/tax inclusion states required')
    tiers=o.get('tiers')
    if not isinstance(tiers,list) or not tiers:raise ValueError('Price tiers required')
    intervals=[]
    for t in tiers:
        if not isinstance(t,dict):raise ValueError('Tier object required')
        lo=_integer(t.get('minimum'));hi=t.get('maximum')
        if hi is not None and _integer(hi,1)<=lo:raise ValueError('Empty tier')
        _decimal(t.get('amount'));intervals.append((lo,hi))
    intervals.sort(key=lambda v:v[0])
    for previous,current in zip(intervals,intervals[1:]):
        if previous[1] is None or previous[1]>current[0]:raise ValueError('Overlapping price tiers')


def estimate_purchase_cost(requirements: list[dict], offers: list[dict], cost_context: dict) -> dict:
    """Estimate one explicit scenario. Invalid requirements/context raise ValueError.

    Every offer is validated (including unused and zero-shortage offers); bad
    offers produce diagnostics and cannot price a line. Missing terms stay None.
    Context requires evaluation time, version, config, scenario, coverage_complete,
    rounding map; costs/selected_offers optional. Output has lossless money strings.
    """
    if not isinstance(cost_context,dict):raise ValueError('Cost context required')
    c=deepcopy(cost_context);at=_time(c.get('evaluated_at'));_integer(c.get('data_version'))
    _text(c.get('configuration_id'))
    if c.get('scenario') not in ('original','virtual_remaining') or type(c.get('coverage_complete')) is not bool:
        raise ValueError('Explicit single scenario and coverage required')
    if 'exchange_rates' in c:raise ValueError('FX is unsupported')
    if not isinstance(requirements,list) or not isinstance(offers,list):raise ValueError('Lists required')
    rounding=c.get('rounding');selected=c.get('selected_offers',{});costs=c.get('costs',{})
    if not all(isinstance(v,dict) for v in (rounding,selected,costs)):raise ValueError('Rounding, selection and costs mappings required')
    quanta={}
    for currency,q in rounding.items():
        if not isinstance(currency,str) or not re.fullmatch('[A-Z]{3}',currency) or q not in ('1','0.1','0.01','0.001','0.0001'):
            raise ValueError('Explicit currency power-of-ten quantum required (1 to .0001)')
        quanta[currency]=Decimal(q)
    if any(not isinstance(k,str) or not isinstance(v,str) or not k or not v for k,v in selected.items()):raise ValueError('Exact offer selection required')
    seen=set()
    for req in requirements:
        if not isinstance(req,dict):raise ValueError('Requirement object required')
        cid=_text(req.get('component_id'))
        if cid in seen or req.get('unit')!='piece':raise ValueError('Distinct exact components in pieces required')
        seen.add(cid)
        if req.get('shortage_quantity') is not None:_integer(req['shortage_quantity'])
    if set(selected)-seen:raise ValueError('Offer selections must refer to requirements')
    r=dict(purpose='procurement_review_only',purchase_authorized=False,context=_serial(c),
        calculation=dict(rule_version=calculation_rule_version('procurement'),precision=80,
            rounding='ROUND_HALF_UP',round_at='each item subtotal and each explicit charge; then sum',
            provenance='caller_provided; no source bytes or supplier authentication'),
        items=[],diagnostics=[],known_item_totals={},additional_costs={},total_by_currency=None,grand_total=None,
        unresolved_items=[],limitations=['Not manufacturing cost or order approval','No FX, split orders or automatic cheapest offer'])
    indexed={};counts={}
    for o in offers:
        oid=o.get('offer_id') if isinstance(o,dict) else None
        if isinstance(oid,str):counts[oid]=counts.get(oid,0)+1
    for number,o in enumerate(offers):
        error=None
        try:
            _quote(o,c['data_version'])
            if counts[o['offer_id']]!=1:raise ValueError('Duplicate offer ID')
            if o['currency'] not in quanta:raise ValueError('Currency rounding policy required')
        except (ValueError,TypeError) as exc:
            error=str(exc);r['diagnostics'].append(dict(code='invalid_offer',index=number,message=error))
        if isinstance(o,dict) and isinstance(o.get('component_id'),str):indexed.setdefault(o['component_id'],[]).append((o,error))
    # Isolate Decimal arithmetic from caller context. No float intermediates.
    with localcontext(Context(prec=80, rounding=ROUND_HALF_UP, Emin=-999999, Emax=999999,
                              traps=[InvalidOperation, DivisionByZero, Overflow])):
        totals={};used={}
        for req in requirements:
            cid=req['component_id'];s=req.get('shortage_quantity')
            item=dict(requirement=_serial(req),component_id=cid,shortage_quantity=s,purchase_quantity=None,
                excess_quantity=None,excess_reasons=[],subtotal=None,currency=None,price=None,offer=None,
                status='unresolved',supply_state='unknown',issues=[])
            candidates=indexed.get(cid,[]);chosen=None
            if cid in selected:
                matches=[v for v in candidates if v[0].get('offer_id')==selected[cid]]
                if len(matches)==1:chosen=matches[0]
                else:item['issues'].append('selected_offer_unavailable')
            elif len(candidates)==1:chosen=candidates[0]
            elif len(candidates)>1:item['issues'].append('offer_selection_required')
            if s is None:item['status']='shortage_unknown';item['issues'].append('shortage_required')
            elif s==0:item.update(status='not_required',purchase_quantity=0,excess_quantity=0)
            elif not candidates:item['status']='quote_required';item['issues'].append('quote_required')
            elif chosen:
                o,error=chosen;item['offer']=_serial(o)
                if error:item['issues'].append('invalid_offer')
                else:
                    item['currency']=o['currency']
                    if o.get('minimum_order') is None or o.get('order_multiple') is None:
                        item['issues'].append('quantity_conditions_required')
                    else:
                        q=purchase_quantity(s,o['minimum_order'],o['order_multiple'])
                        item.update(purchase_quantity=q,excess_quantity=q-s)
                        if o['minimum_order']>s:item['excess_reasons'].append('minimum_order')
                        if q>max(s,o['minimum_order']):item['excess_reasons'].append('order_multiple')
                        supply=o.get('available_quantity')
                        item['supply_state']='unknown' if supply is None else 'insufficient' if supply<q else 'reported_sufficient'
                        if supply is None or supply<q:item['issues'].append('supply_confirmation_required')
                        if not _time(o['valid_from'])<=at<_time(o['valid_until']):item['issues'].append('offer_not_valid_at_evaluation')
                        else:
                            tiers=[t for t in o['tiers'] if t['minimum']<=q and (t['maximum'] is None or q<t['maximum'])]
                            if not tiers:item['issues'].append('price_tier_gap')
                            elif q>10**18 or o['price_basis_quantity']>10**18:
                                item['issues'].append('price_quantity_precision_limit')
                            else:
                                t=tiers[0];money=(Decimal(q)*_decimal(t['amount'])/Decimal(o['price_basis_quantity'])).quantize(quanta[o['currency']])
                                item.update(subtotal=format(money,'f'),status='estimated',price=dict(amount=str(_decimal(t['amount'])),
                                    basis_quantity=o['price_basis_quantity'],unit='piece',tier=_serial(t),method='all_units'))
                                totals[o['currency']]=totals.get(o['currency'],Decimal(0))+money
                                used.setdefault(o['currency'],[]).append(o)
            if s!=0 and item['subtotal'] is None:r['unresolved_items'].append(cid)
            r['items'].append(item)
        r['known_item_totals']={k:format(v,'f') for k,v in totals.items()}
        extra={};cost_complete=True
        # Validate supplied costs even for currencies without a priced line.
        for currency in set(totals)|set(costs):
            specs=costs.get(currency,{})
            if currency not in quanta or not isinstance(specs,dict) or set(specs)-{'shipping','tax','other'}:
                r['diagnostics'].append(dict(code='invalid_cost_context',currency=currency));cost_complete=False;continue
            extra[currency]=Decimal(0);r['additional_costs'][currency]={}
            for kind in ('shipping','tax','other'):
                spec=specs.get(kind,{'state':'unknown'});charge=None;issue=None
                try:
                    if not isinstance(spec,dict) or spec.get('state') not in ('unknown','included','separate'):raise ValueError('Explicit cost state required')
                    state=spec['state']
                    if state!='separate' and 'amount' in spec:raise ValueError('Only separate costs accept amounts')
                    if state=='separate':charge=_decimal(spec.get('amount')).quantize(quanta[currency])
                    if kind in ('shipping','tax') and state!='unknown':
                        required='excluded' if state=='separate' else 'included'
                        if not used.get(currency) or any(o['cost_inclusion'][kind]!=required for o in used[currency]):
                            raise ValueError('Cost inclusion conflicts or is unconfirmed')
                    if kind=='other' and state=='included':raise ValueError('Other costs require explicit amount, including zero')
                    if state=='included':charge=Decimal(0)
                except (ValueError,InvalidOperation) as exc:
                    issue=str(exc);charge=None;r['diagnostics'].append(dict(code='invalid_cost',currency=currency,kind=kind,message=issue))
                r['additional_costs'][currency][kind]=dict(input=_serial(spec),added_amount=None if charge is None else format(charge,'f'),issue=issue)
                if charge is None:cost_complete=False
                else:extra[currency]+=charge
        complete=bool(requirements) and c['coverage_complete'] and not r['unresolved_items'] and not r['diagnostics']
        r['item_costs_complete']=complete
        if complete and cost_complete and totals:
            r['total_by_currency']={k:format(v+extra[k],'f') for k,v in totals.items()}
        r['total_status']='estimated_within_declared_scope' if r['total_by_currency'] is not None else 'unknown'
    return r


def _original_stock(stock):
    return (isinstance(stock,dict) and stock.get('state')!='virtual'
            and stock.get('purpose')!='virtual_material_review_only'
            and 'original_stock' not in stock and 'virtual_deductions' not in stock)


def build_procurement_review(materials_or_plan, offers, cost_context, *, scenario, substitution_review=None):
    """Bind full Task03 material results or a checked Task04 remaining scenario.

    This is consistency checking of caller evidence, not authentication. Task05
    handoff must match the full selected materials, version and positive list.
    Error/conflict inputs are retained but cannot publish purchasable quantities.
    """
    from .planning import build_production_plan
    from .substitution import build_substitution_review
    data=deepcopy(materials_or_plan)
    if scenario not in ('original','virtual_remaining'):raise ValueError('Choose one scenario')
    if scenario=='virtual_remaining':
        if data.get('purpose')!='production_review_only':raise ValueError('Complete Task04 plan required')
        checked=build_production_plan(data['demand'],data['input_materials'],data['initial_quantity'],review_state=data['review_state'])
        for key in ('remaining_materials','components','remaining_quantity','configuration_id','data_version','state','calculation'):
            if data.get(key)!=checked[key]:raise ValueError('Task04 scenario mismatch: '+key)
        m=data['remaining_materials']
    else:
        m=data
        if m.get('purpose')!='material_review_only' or not _original_stock(m.get('stock')):raise ValueError('Original Task03 material input required')
        if not m.get('errors') and not m.get('conflicts'):
            d={k:deepcopy(m[k]) for k in ('configuration_id','data_version','planned_input_quantity','demand_source')}
            d['unit']='piece'
            build_production_plan(d,m,0)  # Existing Task03 derivation/stock guard, no copied arithmetic.
    c=deepcopy(cost_context)
    if c.get('scenario')!=scenario or any(c.get(k)!=m[k] or type(c.get(k))!=type(m[k]) for k in ('configuration_id','data_version')):
        raise ValueError('Exact scenario/configuration/version binding required')
    if m['calculation']['rule_version']!=calculation_rule_version('materials'):raise ValueError('Task03 rule required')
    bad=bool(m['errors'] or m['conflicts'])
    c['coverage_complete']=bool(m['bom_complete'] and m['components'] and not bad and not m['unresolved'])
    req=[dict(component_id=i['component_id'],shortage_quantity=None if bad else i['S'],unit='piece',bom_rows=deepcopy(i['bom_rows'])) for i in m['components']]
    handoff=build_substitution_review(m,[],[],[])['purchase_review']
    if substitution_review is not None:
        s=substitution_review
        if (s.get('purpose')!='substitution_review_only' or s.get('original_materials')!=m
                or s.get('configuration_id')!=m['configuration_id'] or s.get('data_version')!=m['data_version']
                or s.get('shortage_scope')!=('original_material_review' if scenario=='original' else 'virtual_remaining_scenario')
                or s.get('purchase_review')!=handoff):raise ValueError('Task05 handoff mismatch')
    result=estimate_purchase_cost(req,offers,c)
    result.update(materials=deepcopy(m),scenario=scenario,purchase_review=deepcopy(handoff),
        substitution_review=deepcopy(substitution_review),
        upstream_diagnostics={k:deepcopy(m[k]) for k in ('errors','conflicts','unresolved','questions')})
    return result


def _preview(value):
    if isinstance(value,dict):
        return value.get('is_preview') is True or any(_preview(v) for v in value.values())
    if isinstance(value,list):return any(_preview(v) for v in value)
    return False


def build_saved_procurement_review(workflow, host, context, *, offers, cost_context, scenario,
        initial_quantity=None, **material_arguments):
    """Fresh existing Task02/03/04 host reads. No cache, persistence or approvals.

    material_arguments are calculate_saved_demand arguments, including optional
    expected_revision/expected_target_sha256. Public failures remain in envelopes.
    """
    from .materials import calculate_saved_demand
    from .planning import build_saved_production_plan
    if scenario not in ('original','virtual_remaining'):raise ValueError('Choose one scenario')
    snapshots=material_arguments.get('snapshots')
    if not isinstance(snapshots,list) or any(not _original_stock(s) for s in snapshots):raise ValueError('Original stock documents required')
    if scenario=='original':
        if initial_quantity is not None:raise ValueError('Initial production applies only to virtual scenario')
        r=calculate_saved_demand(workflow,host,context,**material_arguments);selected=r['calculation']
    else:
        r=build_saved_production_plan(workflow,host,context,initial_quantity=initial_quantity,**material_arguments);selected=r['plan']
    r['procurement']=None
    if _preview(r):
        r['errors'].append(dict(code='PREVIEW_NOT_PUBLIC',message='Public non-preview data required'));return r
    if selected is not None:r['procurement']=build_procurement_review(selected,offers,cost_context,scenario=scenario)
    return r
