"""Read-only, conditional elapsed-time estimates; never execution or authority.

Schedule evidence is caller-provided, not supplier-authenticated. Full Task03/04
inputs are checked by Task06. See MATERIALS section 18 for the contract.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import re

from .planning import build_production_plan, build_saved_production_plan
from .procurement import build_procurement_review

RULE_VERSION = 'scheduling-0.1'


def _integer(v):
    if type(v) is not int or not 0 <= v <= 10**18:
        raise ValueError('Integer quantity 0..10**18 required')
    return v


def _number(v):
    if type(v) not in (str, int, Decimal):
        raise ValueError('Decimal string, integer or Decimal required; not bool/float')
    try:
        d = Decimal(v)
    except InvalidOperation as exc:
        raise ValueError('Invalid decimal') from exc
    if (not d.is_finite() or d < 0 or len(d.as_tuple().digits) > 24
            or not -12 <= d.as_tuple().exponent <= 12):
        raise ValueError('Finite nonnegative decimal, <=24 digits, exponent -12..12 required')
    return Fraction(d)  # exact rational, independent of caller Decimal context


def _time(v):
    if not isinstance(v, str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', v):
        raise ValueError('ISO timestamp with seconds and explicit offset required')
    # datetime accepts offsets such as +01:60 by normalization; disallow that.
    if v[-1] != 'Z' and (int(v[-5:-3]) > 23 or int(v[-2:]) > 59):
        raise ValueError('Invalid offset')
    return datetime.fromisoformat(v.replace('Z', '+00:00'))


def _text(v):
    if not isinstance(v, str) or not v.strip():
        raise ValueError('Exact nonempty identifier required')
    return v


def _source(v):
    if not isinstance(v, dict) or not re.fullmatch('[0-9a-f]{64}', str(v.get('sha256', ''))):
        raise ValueError('Source SHA-256 required')
    for key in ('source_ref', 'locator'):
        _text(v.get(key))


def _event(e, evaluated_at):
    if not isinstance(e, dict) or e.get('state') not in ('actual', 'assumed', 'promised'):
        raise ValueError('Explicit actual/assumed/promised milestone required')
    _source(e.get('source'))
    at = _time(e.get('at'))
    if e['state'] == 'actual' and at > evaluated_at:
        raise ValueError('Future event cannot be actual')
    return at


def _calendar(v):
    for key in ('calendar', 'calendar_policy'):
        if key in v and v[key] != 'continuous_elapsed_24h':
            raise ValueError('Unsupported calendar policy')
    if any(key in v for key in ('shifts', 'shift_calendar', 'holidays')):
        raise ValueError('Shift/holiday calendars unsupported')


def _duration(v):
    if not isinstance(v, dict) or v.get('unit') not in ('seconds', 'minutes', 'hours', 'days'):
        raise ValueError('Elapsed seconds/minutes/hours/days required; business calendars unsupported')
    _calendar(v)
    return _number(v.get('value')) * {'seconds': 1, 'minutes': 60, 'hours': 3600, 'days': 86400}[v['unit']]


def _add(at, seconds):
    # Ceiling each stage to seconds; cannot understate duration through rounding.
    n = -(-seconds.numerator // seconds.denominator)
    return at + timedelta(seconds=n)


def _preview(value):
    if isinstance(value, dict):
        return value.get('is_preview') is True or any(_preview(v) for v in value.values())
    return isinstance(value, list) and any(_preview(v) for v in value)


def _serial(v):
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, dict):
        return {k: _serial(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_serial(x) for x in v]
    return deepcopy(v)


def _schedule_at(s, component, purchase, m, c):
    _calendar(s)
    for key in ('configuration_id', 'data_version'):
        if type(s.get(key)) is not type(m[key]) or s.get(key) != m[key]:
            raise ValueError('Schedule binding mismatch: ' + key)
    if (s.get('scenario') != c['scenario'] or s.get('demand_source') != m['demand_source']
            or _integer(s.get('board_quantity')) != m['planned_input_quantity'] or s.get('unit') != 'piece'):
        raise ValueError('Exact scenario/demand/board quantity/piece binding required')
    if not _time(s.get('valid_from')) <= _time(c['evaluated_at']) < _time(s.get('valid_until')):
        raise ValueError('Schedule evidence not valid at evaluation')
    _source(s.get('source'))
    if s.get('offer_id') is not None:
        offer = purchase.get('offer')
        if not offer or s['offer_id'] != offer['offer_id'] or s.get('supplier_id') != offer['supplier_id']:
            raise ValueError('Exact selected offer and supplier required')
        _source(offer.get('source'))
        if (type(offer.get('data_version')) is not int or offer['data_version'] != m['data_version']
                or offer.get('unit') != 'piece' or offer.get('component_id') != component['component_id']):
            raise ValueError('Linked offer version/unit/component mismatch')
        _text(offer.get('supplier_id'))
        if not _time(offer['valid_from']) <= _time(c['evaluated_at']) < _time(offer['valid_until']):
            raise ValueError('Linked offer expired')
    if component['D'] is None or component['S'] is None:
        raise ValueError('Required quantity or net shortage unknown')
    q = _integer(s.get('quantity'))
    coverage = s.get('coverage')
    minimum = max(component['S'], purchase['purchase_quantity'] or 0)
    if coverage == 'full_requirement':
        minimum = max(component['D'], minimum)
    elif coverage != 'shortage':
        raise ValueError('Explicit full_requirement or shortage coverage required')
    if q < minimum:
        raise ValueError('Supply quantity insufficient for requirement/purchase quantity')
    at_eval = _time(c['evaluated_at'])
    required = s.get('required_milestones')
    if not isinstance(required, list) or any(not isinstance(k, str) for k in required):
        raise ValueError('Explicit required_milestones list required')
    gates = [_event(c['milestones'].get(k), at_eval) for k in required]
    if s.get('kind') in ('usable_at', 'arrival_at'):
        at = _event(s, at_eval)
        if s['kind'] == 'arrival_at':
            at = _add(at, _duration(s.get('inspection')))
        # A promised arrival before a prerequisite is inconsistent, not delayed
        # silently: obtain a new promise that incorporates the prerequisite.
        if gates and max(gates) > at:
            raise ValueError('Usable/arrival promise precedes required prerequisite')
    elif s.get('kind') == 'lead_time':
        if s.get('state') not in ('assumed', 'promised'):
            raise ValueError('Lead time is an estimate, not actual receipt')
        start = _event(c['milestones'].get(s.get('start_milestone')), at_eval)
        if gates and max(gates) > start:
            raise ValueError('Lead-time start precedes required prerequisite')
        if type(s.get('transport_included')) is not bool:
            raise ValueError('Explicit transport inclusion required')
        at = _add(start, _duration(s.get('lead_time')))
        if not s['transport_included']:
            at = _add(at, _duration(s.get('transport')))
        at = _add(at, _duration(s.get('inspection')))
    else:
        raise ValueError('Only usable_at, arrival_at or lead_time is supported; observation/supply/dispatch is not usable')
    if coverage == 'shortage' and component['D'] > component['S']:
        at = max(at, _event(s.get('existing_usable'), at_eval))
    return at


def estimate_material_ready(materials_or_plan, schedules, context, *, scenario,
                            offers=None, cost_context=None):
    """Return per-component usable estimates and whole/partial readiness.

    Original uses full Task03; virtual_remaining uses a full checked Task04 plan.
    Invalid context raises ValueError. Ineligible/missing schedule records produce
    per-item issues and None, retaining evidence. No price is required for an
    independent schedule record. No empty or incomplete scope becomes ready.
    """
    c = deepcopy(context)
    _calendar(c)
    _time(c.get('evaluated_at'))
    if c.get('scenario') != scenario:
        raise ValueError('Exact scenario required')
    if not isinstance(schedules, list) or not isinstance(c.get('selected_schedules'), dict) or not isinstance(c.get('milestones'), dict):
        raise ValueError('Schedules list, selection and milestone maps required')
    price_context = deepcopy(cost_context) if cost_context is not None else dict(c, rounding={}, costs={})
    for key in ('scenario', 'configuration_id', 'data_version', 'evaluated_at'):
        if type(price_context.get(key)) is not type(c.get(key)) or price_context.get(key) != c.get(key):
            raise ValueError('Price and schedule review context mismatch')
    procurement = build_procurement_review(materials_or_plan, offers or [], price_context, scenario=scenario)
    m = procurement['materials']
    component_ids = {x['component_id'] for x in m['components']}
    if set(c['selected_schedules']) - component_ids:
        raise ValueError('Selection must reference exact required components')
    result = dict(purpose='material_readiness_review_only', status='unknown', ready_at=None, known_latest_at=None,
        calculation={'rule_version': RULE_VERSION}, scenario=scenario, context=_serial(c), procurement=procurement,
        items=[], diagnostics=[], purchase_authorized=False, alternative_ranking=None,
        required_information=['Original-component quotation, delivery basis, review, send and commitment evidence',
            'Alternative technical review, customer agreement, configuration check and fresh candidate demand/stock'],
        provenance='caller_evidence_only; not authenticated receipt or approval',
        inputs=_serial(dict(materials_or_plan=materials_or_plan,schedules=schedules,context=context,
                           scenario=scenario,offers=offers,cost_context=cost_context)))
    counts = {}
    for s in schedules:
        if isinstance(s, dict) and isinstance(s.get('schedule_id'), str):
            counts[s['schedule_id']] = counts.get(s['schedule_id'], 0) + 1
        if not isinstance(s, dict) or s.get('component_id') not in component_ids:
            result['diagnostics'].append('unmatched_schedule_component')
    known = []
    for component, purchase in zip(m['components'], procurement['items']):
        cid = component['component_id']
        item = dict(component_id=cid, required_quantity=component['D'], shortage_quantity=component['S'],
                    unit='piece', ready_at=None, status='unknown', issues=[], evidence=None)
        candidates = [s for s in schedules if isinstance(s, dict) and s.get('component_id') == cid]
        selected = c['selected_schedules'].get(cid)
        if selected is not None:
            candidates = [s for s in candidates if s.get('schedule_id') == selected]
        if len(candidates) != 1:
            item['issues'].append('schedule_evidence_or_exact_selection_required')
        else:
            s = candidates[0]; item['evidence'] = _serial(s)
            try:
                _text(s.get('schedule_id'))
                if counts[s['schedule_id']] != 1:
                    raise ValueError('Conflicting duplicate schedule ID')
                if m['errors'] or m['conflicts']:
                    raise ValueError('Upstream material errors/conflicts')
                at = _schedule_at(s, component, purchase, m, c)
                item.update(ready_at=at.isoformat(), status='estimated')
                known.append(at)
            except (ValueError, TypeError, KeyError) as exc:
                item['issues'].append(str(exc))
            except OverflowError:
                item['issues'].append('time_range_exceeded')
        result['items'].append(item)
    if known:
        result['known_latest_at'] = max(known).isoformat()
    complete = (bool(result['items']) and len(known) == len(result['items']) and m['bom_complete']
                and not any(m[k] for k in ('errors','conflicts','unresolved')) and not result['diagnostics'])
    if complete:
        result.update(ready_at=result['known_latest_at'], status='estimated')
    else:
        result['diagnostics'].append('whole_material_readiness_unresolved')
    return result


def _checked_plan(p):
    if p.get('purpose') != 'production_review_only' or p.get('state') != 'virtual':
        raise ValueError('Complete virtual Task04 plan required')
    checked = build_production_plan(p['demand'], p['input_materials'], p['initial_quantity'], review_state=p['review_state'])
    for key in ('remaining_materials','components','remaining_quantity','configuration_id','data_version','calculation','planned_input_quantity'):
        if p.get(key) != checked[key]:
            raise ValueError('Task04 scenario mismatch: ' + key)
    for key in ('initial_quantity','remaining_quantity','planned_input_quantity'):
        _integer(p[key])


def _job(quantity, resource_ref):
    return dict(quantity=quantity, unit='board', resource_ref=resource_ref, status='no_work' if quantity == 0 else 'unknown',
                work_start=None, processing_start=None, completion=None, issues=[])


def _calculate_job(job, starts, setup, rate, zone):
    try:
        start = max(starts).astimezone(zone)
        processing = _add(start, setup * 60)
        end = _add(processing, Fraction(job['quantity']) * 3600 / rate)
        job.update(work_start=start.isoformat(), processing_start=processing.isoformat(), completion=end.isoformat(),
                   status='conditional_estimate')
    except (OverflowError, ValueError):
        job['issues'].append('time_range_exceeded')


def estimate_production_schedule(plan, resource, material_ready, *, initial_conditions=None,
                                 remaining_setup_minutes=None):
    """One resource; initial readiness is an explicit separate hypothetical case.

    material_ready must be the unchanged virtual_remaining result for this plan.
    The initial hypothetical case never changes actual whole-BOM readiness.
    Remaining re-setup is explicit; missing is unknown. Zero jobs have no events.
    """
    _checked_plan(plan)
    rr = deepcopy(resource)
    ref = _text(rr.get('resource_ref')); available = _time(rr.get('available_at'))
    rate = _number(rr.get('boards_per_hour')); setup = _number(rr.get('setup_minutes'))
    if not rate:
        raise ValueError('Positive throughput required')
    reset = None if remaining_setup_minutes is None else _number(remaining_setup_minutes)
    for key in ('loss_quantity','defect_quantity','rework_quantity'):
        _integer(rr.get(key))
    ready = deepcopy(material_ready)
    if ready.get('purpose') != 'material_readiness_review_only' or ready.get('scenario') != 'virtual_remaining':
        raise ValueError('Remaining material readiness required')
    inputs = ready.get('inputs', {})
    if inputs.get('materials_or_plan') != plan or estimate_material_ready(**inputs) != ready:
        raise ValueError('Readiness scenario or derivation mismatch')
    result = dict(purpose='schedule_review_only', calculation=dict(rule_version=RULE_VERSION,
        calendar='continuous_elapsed_24h', precision='ceil_each_duration_to_second', numeric='exact_rational'),
        plan=deepcopy(plan), resource=_serial(rr), material_ready=ready, initial_conditions=_serial(initial_conditions),
        remaining_setup_minutes=_serial(remaining_setup_minutes), production_authorized=False,purchase_authorized=False,
        resource_reserved=False, whole_bom_material_limit=plan['whole_bom_material_limit'],
        customer_arrival_compliance=None, customer_arrival_required=['packing','dispatch','transport','destination','arrival time'],
        assumptions=['Constant rate; no interruption; zero loss, defect and rework',
            'Initial all-required-material readiness is a separate explicit hypothesis, not actual stock readiness'],
        initial=_job(plan['initial_quantity'],ref),remaining=_job(plan['remaining_quantity'],ref))
    calendar_unsupported = False
    try:
        _calendar(rr)
    except ValueError:
        calendar_unsupported = True
    unsupported = (calendar_unsupported or rr.get('interruption_policy') != 'none_assumed' or 'resources' in rr
                   or rr.get('calendar', 'continuous_elapsed_24h') != 'continuous_elapsed_24h'
                   or 'remaining_resource_ref' in rr
                   or any(rr[k] != 0 for k in ('loss_quantity','defect_quantity','rework_quantity')))
    if unsupported:
        for job in (result['initial'],result['remaining']):
            if job['quantity']: job['issues'].append('unsupported_resource_policy_recalculation_required')
        return result
    first, last = result['initial'], result['remaining']
    if first['quantity']:
        try:
            ic = initial_conditions
            if (not isinstance(ic,dict) or ic.get('state') != 'assumed'
                    or ic.get('scope') != 'all_required_materials_for_initial_quantity'
                    or _integer(ic.get('quantity')) != first['quantity']):
                raise ValueError('Explicit initial all-material readiness assumption required')
            _source(ic.get('source'))
            if not isinstance(ic.get('prerequisites'),list):
                raise ValueError('Explicit initial prerequisites list required')
            gates = [_event(e,_time(ready['context']['evaluated_at'])) for e in ic['prerequisites']]
            _calculate_job(first,[available,_time(ic.get('ready_at')),*gates],setup,rate,available.tzinfo)
        except (ValueError,TypeError) as exc:
            first['issues'].append(str(exc))
    if last['quantity']:
        if first['quantity'] and first['completion'] is None:
            last['issues'].append('initial_completion_unknown_same_resource')
        if ready['ready_at'] is None:
            last['issues'].append('remaining_material_ready_unknown')
        if reset is None:
            last['issues'].append('remaining_setup_policy_required')
        if not last['issues']:
            starts = [available,_time(ready['ready_at'])]
            if first['completion'] is not None: starts.append(_time(first['completion']))
            _calculate_job(last,starts,reset,rate,available.tzinfo)
    return result


def build_saved_schedule_review(workflow, host, context, *, initial_quantity, resource,
                                schedules, schedule_context, offers=None, cost_context=None,
                                initial_conditions=None, remaining_setup_minutes=None, **material_arguments):
    """Fresh Task02 get -> Task03 -> Task04 -> Task06 -> Task07; no persistence.

    Preserve public envelopes, preview/failure and current permissions. Caller
    flags never establish authorization. Only the existing host authenticates.
    """
    snapshots = material_arguments.get('snapshots')
    if not isinstance(snapshots,list) or any(not isinstance(s,dict) or s.get('state') == 'virtual'
            or s.get('purpose') == 'virtual_material_review_only' or 'original_stock' in s or 'virtual_deductions' in s for s in snapshots):
        raise ValueError('Original stock documents required')
    result = build_saved_production_plan(workflow,host,context,initial_quantity=initial_quantity,**material_arguments)
    result.update(material_ready=None,schedule=None)
    if _preview(result):
        result['errors'].append(dict(code='PREVIEW_NOT_PUBLIC',message='Non-preview public data required'))
        return result
    if result['plan'] is None:
        return result
    ready = estimate_material_ready(result['plan'],schedules,schedule_context,scenario='virtual_remaining',
                                    offers=offers,cost_context=cost_context)
    result['material_ready'] = ready
    result['schedule'] = estimate_production_schedule(result['plan'],resource,ready,
        initial_conditions=initial_conditions,remaining_setup_minutes=remaining_setup_minutes)
    return result
