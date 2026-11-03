"""Read-only material arithmetic. No identity, confirmation or inventory writes.

Stock documents retain stock_scope inheritance and source provenance. The host's
component_lots declares the exhaustive lot set *within the stated evaluation
scope*, with a required coverage_basis. It is an assumption, not proof of stock.
An undeclared/partially observed lot set only supplies an observed subtotal.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json

from .rules import calculation_rule_version


def _time(value):
    if not isinstance(value, str) or 'T' not in value:
        raise ValueError('Offset-aware timestamp required')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.utcoffset() is None:
        raise ValueError('Offset-aware timestamp required')
    return result


def _integer(value, *, positive=False):
    return type(value) is int and value >= (1 if positive else 0)


def _key(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=True)


def _issue(code, refs, message):
    return dict(code=code, refs=deepcopy(refs), message=message)


def available_stock(snapshots: list[dict], scope: dict) -> dict:
    """Select latest observations, never movements, at/before scope.as_of.

    snapshots: documents with source_ref, sha256, stock_scope and stocks.
    Rows inherit company/location/as_of; explicit row values override them.
    scope: company/location/as_of/unit='piece', component_lots={component:[lot]},
    coverage_basis (required when any complete lot set is asserted).
    Invalid rows/conflicts remain diagnostics and block affected components.
    Missing/null quantities are unresolved, not zero; invalid types are errors.
    """
    if not isinstance(scope, dict) or any(not isinstance(scope.get(k), str) or not scope[k] for k in ('company','location','as_of')):
        raise ValueError('Explicit company, location and as_of required')
    cutoff = _time(scope['as_of'])
    if scope.get('unit') != 'piece':
        raise ValueError('Only piece is supported')
    coverage = scope.get('component_lots', {})
    if (not isinstance(coverage, dict) or any(not isinstance(k,str) or not k or not isinstance(v,list)
            or not v or any(not isinstance(l,str) or not l for l in v) or len(set(v)) != len(v)
            for k,v in coverage.items())):
        raise ValueError('component_lots must map exact component IDs to distinct nonempty lot IDs')
    if coverage and (not isinstance(scope.get('coverage_basis'), str) or not scope['coverage_basis'].strip()):
        raise ValueError('Explicit coverage_basis required')
    if not isinstance(snapshots, list):
        raise ValueError('Stock observation documents required')
    result = dict(scope=deepcopy(scope), components={}, selected=[], excluded=[], errors=[], conflicts=[], unresolved=[],
                  limitations=['Last observed inventory only; no guarantee of actual current inventory',
                               'Coverage is host-declared within this evaluation scope, not global inventory'])
    observations, blocked, seen_ids, lot_components = [], set(), {}, {}
    global_error = False
    for di, doc in enumerate(snapshots):
        if (not isinstance(doc,dict) or not isinstance(doc.get('stock_scope'),dict)
                or not isinstance(doc.get('stocks'),list) or not isinstance(doc.get('source_ref'),str)
                or not isinstance(doc.get('sha256'),str) or len(doc['sha256']) != 64):
            result['errors'].append(_issue('INVALID_DOCUMENT',[di],'Observation document and original source hash required'))
            global_error = True
            continue
        for ri, raw in enumerate(doc['stocks']):
            ref = dict(source_ref=doc['source_ref'], sha256=doc['sha256'], locator=f'/stocks/{ri}', document_scope=deepcopy(doc['stock_scope']))
            if not isinstance(raw,dict):
                result['errors'].append(_issue('INVALID_OBSERVATION',[ref],'Object required'));global_error=True;continue
            row = deepcopy(raw)
            for k in ('company','location','as_of'):
                if k not in row: row[k] = doc['stock_scope'].get(k)
            cid = row.get('component_id')
            def error(code, message):
                result['errors'].append(_issue(code,[ref],message))
                if isinstance(cid,str): blocked.add(cid)
            if any(not isinstance(row.get(k),str) or not row[k] for k in ('id','component_id','lot_id','company','location','as_of')):
                error('INVALID_OBSERVATION','Exact ID and scope fields required');global_error=True;continue
            try: when = _time(row['as_of'])
            except ValueError:
                when = None
            # Identity conflicts matter even when the new content has bad units
            # or an invalid timestamp. Preserve raw invalid time for comparison.
            canonical = dict(row)
            if when is not None:
                canonical['as_of'] = when.astimezone(timezone.utc).isoformat()
            content = _key(canonical)
            previous = seen_ids.get(row['id'])
            if previous:
                if previous['content'] == content:
                    result['excluded'].append(dict(observation=row,source=ref,reason='duplicate_observation'));continue
                blocked.update((cid,previous['row']['component_id']))
                result['conflicts'].append(_issue('OBSERVATION_ID_CONFLICT',[previous['source'],ref],'Same observation ID has different content'))
            else: seen_ids[row['id']] = dict(content=content,row=row,source=ref)
            lot_components.setdefault(row['lot_id'],set()).add(cid)
            if when is None:
                error('INVALID_TIME','Offset-aware timestamp required');continue
            if row.get('unit') != scope['unit']:
                error('UNIT_MISMATCH','Stock must use piece');continue
            if any(row[k] != scope[k] for k in ('company','location')):
                result['excluded'].append(dict(observation=row,source=ref,reason='outside_scope'));continue
            for field in ('on_hand_quantity','reserved_quantity','held_quantity','available_quantity'):
                if row.get(field) is not None and not _integer(row[field]):
                    error('INVALID_QUANTITY','All observation quantities must be nonnegative integers')
            if when > cutoff:
                result['excluded'].append(dict(observation=row,source=ref,reason='future_observation'));continue
            observations.append(dict(observation=row,source=ref,when=when))
    for lot, cids in lot_components.items():
        if len(cids)>1:
            blocked.update(cids)
            result['conflicts'].append(_issue('LOT_COMPONENT_CONFLICT',[lot,*sorted(cids)],'One lot cannot identify different components'))
    groups = {}
    for obs in observations:
        row=obs['observation']; groups.setdefault((row['component_id'],row['lot_id']),[]).append(obs)
    selected = {}
    for (cid,lot), entries in sorted(groups.items()):
        by_time={}; conflicting_times=set()
        for obs in entries: by_time.setdefault(obs['when'],[]).append(obs)
        for instant, group in by_time.items():
            # Different IDs at the same instant may corroborate the same counts.
            values={_key({k:v for k,v in o['observation'].items() if k not in ('id','as_of','evidence')}) for o in group}
            if len(values)>1:
                conflicting_times.add(instant)
                blocked.add(cid)
                result['conflicts'].append(_issue('SIMULTANEOUS_CONFLICT',[o['source'] for o in group],'Conflicting observations at the same instant'))
        latest=max(by_time)
        for obs in entries:
            if obs['when']<latest:
                result['excluded'].append(dict(observation=obs['observation'],source=obs['source'],reason='older_observation'))
        chosen=sorted(by_time[latest], key=lambda o:o['observation']['id'])[0]
        for obs in by_time[latest]:
            if obs is not chosen:
                result['excluded'].append(dict(observation=obs['observation'],source=obs['source'],reason='conflicting_observation' if latest in conflicting_times else 'corroborating_observation'))
        row=chosen['observation']; amount=None
        errors=[]; unknown=False
        for k in ('on_hand_quantity','reserved_quantity','held_quantity','available_quantity'):
            if row.get(k) is not None and not _integer(row[k]): errors.append('INVALID_QUANTITY')
        state=row.get('quantity_state')
        if state not in ('known','unknown'): errors.append('INVALID_QUANTITY_STATE')
        basis=row.get('availability_basis')
        if basis=='reported_available':
            required=('available_quantity',)
        elif basis=='on_hand_less_reserved_and_held':
            required=('on_hand_quantity','reserved_quantity','held_quantity')
            if row.get('deductions_disjoint') is not True: errors.append('DEDUCTIONS_NOT_DISJOINT')
        else:
            required=();errors.append('INVALID_AVAILABILITY_BASIS')
        unknown=state=='unknown' or any(row.get(k) is None for k in required)
        if not errors and not unknown:
            amount=row['available_quantity'] if basis=='reported_available' else row['on_hand_quantity']-row['reserved_quantity']-row['held_quantity']
            if amount<0: errors.append('DEDUCTIONS_EXCEED_ON_HAND');amount=None
        for code in set(errors):
            result['errors'].append(_issue(code,[chosen['source']],'Selected observation is invalid'));blocked.add(cid)
        if unknown: result['unresolved'].append(_issue('UNKNOWN_QUANTITY',[chosen['source']],'Missing/null or explicitly unknown count'))
        if cid in blocked:
            amount=None
        selection=dict(observation=row,source=chosen['source'],age_seconds=(cutoff-latest).total_seconds(),
                       available_quantity=amount, state='invalid' if errors or cid in blocked else 'unknown' if unknown else 'known')
        result['selected'].append(selection)
        selected.setdefault(cid,{})[lot]=selection
    cids=set(coverage)|set(selected)|blocked
    for cid in sorted(cids):
        lots=selected.get(cid,{})
        known_amounts=[s['available_quantity'] for s in lots.values() if s['available_quantity'] is not None]
        subtotal=sum(known_amounts) if known_amounts else None
        complete=cid in coverage and set(lots)==set(coverage[cid])
        known=bool(lots) and all(s['state']=='known' for s in lots.values())
        invalid=global_error or cid in blocked
        amount=subtotal if complete and known and not invalid else None
        result['components'][cid]=dict(available_quantity=amount,observed_available_quantity=None if invalid else subtotal,
            coverage_complete=complete,lot_ids=sorted(lots),state='invalid' if invalid else 'known' if amount is not None else 'unknown')
        if amount is None:
            result['unresolved'].append(_issue('STOCK_NOT_FULLY_KNOWN',[cid],'Resolve conflicts, counts and exhaustive lot coverage'))
    result['valid']=not result['errors'] and not result['conflicts']
    return result


def calculate_material_requirements(items: list[dict], stock: dict, planned_input_quantity: int, *,
        configuration_id: str, data_version: int, demand_source: dict, bom_complete: bool = False) -> dict:
    """N is an explicit nonnegative integer (includes zero unlike intake's N>0).

    q/D are None where required BOM totals are unresolved; known_q/known_D
    retain the supported subtotal. A/S require exhaustive declared lot coverage.
    conditional_upper_bound covers only listed evaluated_component_ids; the
    whole BOM limit additionally requires complete BOM and no unresolved rows.
    All results remain review calculations, even when arithmetic is valid.
    """
    if not _integer(planned_input_quantity): raise ValueError('N must be an explicit nonnegative integer')
    if not _integer(data_version) or not isinstance(configuration_id,str) or not configuration_id:
        raise ValueError('Exact configuration and data version required')
    if not isinstance(items,list) or type(bom_complete) is not bool or not isinstance(demand_source,dict):
        raise ValueError('Explicit BOM list, completeness and demand provenance required')
    r=dict(purpose='material_review_only', data_version=data_version,configuration_id=configuration_id,
           planned_input_quantity=planned_input_quantity, demand_source=deepcopy(demand_source),
           calculation=dict(rule_version=calculation_rule_version('materials')), stock=deepcopy(stock),
           components=[],excluded_bom=[],errors=deepcopy(stock['errors']),conflicts=deepcopy(stock['conflicts']),
           unresolved=deepcopy(stock['unresolved']),questions=[],evaluated_component_ids=[],
           conditional_upper_bound=None,whole_bom_material_limit=None,bom_complete=bom_complete,
           limitations=['No production commitment, stock reservation, consumption or substitution permission',
                        'Conditional upper bound applies only to evaluated components and declared lot scope'])
    groups={}; seen={}; global_invalid=False
    for row in deepcopy(items):
        if not isinstance(row,dict) or any(not isinstance(row.get(k),str) or not row[k] for k in ('id','component_id')):
            r['errors'].append(_issue('INVALID_BOM_ROW',[row],'Exact row/component IDs required'));global_invalid=True;continue
        cid=row['component_id']; group=groups.setdefault(cid,dict(rows=[],known_q=0,unknown=False,invalid=False))
        if row['id'] in seen:
            prev=seen[row['id']]
            if _key(prev)==_key(row):
                r['excluded_bom'].append(dict(row=row,reason='duplicate_bom_row'));continue
            r['conflicts'].append(_issue('BOM_ID_CONFLICT',[prev,row],'One BOM ID has different content'))
            group['invalid']=True;groups[prev['component_id']]['invalid']=True;continue
        seen[row['id']]=row; group['rows'].append(row)
        if row.get('configuration_id')!=configuration_id or row.get('unit')!='piece' or not _integer(row.get('quantity'),positive=True):
            r['errors'].append(_issue('INVALID_BOM_ROW',[row],'Exact configuration, piece unit and positive integer quantity required'))
            group['invalid']=True;continue
        population=row.get('population')
        if population=='populated': group['known_q']+=row['quantity']
        elif population=='not_populated': r['excluded_bom'].append(dict(row=row,reason='not_populated'))
        elif population=='unknown':
            group['unknown']=True;r['unresolved'].append(_issue('UNKNOWN_POPULATION',[row],'Required quantity is unresolved'))
        else:
            group['invalid']=True;r['errors'].append(_issue('INVALID_POPULATION',[row],'Use populated/not_populated/unknown'))
    limits=[]
    for cid,g in sorted(groups.items()):
        if not g['known_q'] and not g['unknown'] and not g['invalid']: continue
        q=g['known_q'] if not g['unknown'] and not g['invalid'] and not global_invalid else None
        a=stock['components'].get(cid,{}).get('available_quantity')
        if a is None: r['unresolved'].append(_issue('MISSING_OR_UNKNOWN_STOCK',[cid],'Complete stock required for shortage'))
        d=planned_input_quantity*q if q is not None else None
        shortage=max(0,d-a) if d is not None and a is not None else None
        r['components'].append(dict(component_id=cid,q=q,D=d,A=a,S=shortage,known_q=g['known_q'],
            known_D=planned_input_quantity*g['known_q'],bom_rows=g['rows'],quantity_complete=q is not None))
        if q and a is not None:
            limits.append(a//q);r['evaluated_component_ids'].append(cid)
    if limits: r['conditional_upper_bound']=min(planned_input_quantity,*limits)
    if bom_complete and r['components'] and len(limits)==len(r['components']) and not r['errors'] and not r['conflicts'] and not r['unresolved']:
        r['whole_bom_material_limit']=r['conditional_upper_bound']
    if not bom_complete: r['unresolved'].append(_issue('BOM_COVERAGE_UNCONFIRMED',[],'Input rows are not declared a complete BOM'))
    if not r['components']: r['unresolved'].append(_issue('NO_REQUIRED_COMPONENTS',[],'No calculable required components; limit undefined'))
    r['questions']=[dict(code=i['code'],question=i['message'],refs=deepcopy(i['refs'])) for i in r['unresolved']+r['conflicts']+r['errors']]
    r['valid']=not r['errors'] and not r['conflicts']
    return r


def calculate_saved_demand(workflow, host, context, *, demand_id: str, item_id: str,
        data_version: int, snapshots: list[dict], scope: dict,
        expected_revision: int | None = None, expected_target_sha256: str | None = None) -> dict:
    """Host stage, not an Agent tool: re-read the current saved candidate.

    workflow is the existing PersistentDemandConfirmationWorkflow; host/context
    are trusted application objects. No caller-supplied confirmation dict is
    consumed. Optional revision/hash bind the calculation to a displayed view.
    Public failures are retained, never interpreted as empty inventory/BOM.
    Only registered exact lot/company/component relationships are accepted here;
    use the pure functions for explicitly unregistered development inputs.
    """
    from .configuration_lookup import lookup_contract_configurations
    r=dict(demand_response=None,query=None,calculation=None,errors=[])
    response=workflow.get(context,demand_id)
    r['demand_response']=response
    if not response['ok']: return r
    demand=response['data']
    def error(code,message): r['errors'].append(_issue(code,[demand_id],message))
    if (type(data_version) is not int or response['data_version']!=data_version
            or demand['revision']!=demand['current_revision'] or demand['status']=='superseded'):
        error('DEMAND_VERSION_MISMATCH','Use the exact current candidate and data version');return r
    if ((expected_revision is not None and (type(expected_revision) is not int or expected_revision!=demand['revision']))
            or (expected_target_sha256 is not None and expected_target_sha256!=demand['target_sha256'])):
        error('DEMAND_TARGET_CHANGED','Displayed candidate is no longer current');return r
    matches=[i for i in demand['items'] if i['contract_item_candidate']['id']==item_id]
    if len(matches)!=1:
        error('DEMAND_ITEM_NOT_FOUND','Select one exact candidate item');return r
    item=matches[0]; candidate=item['contract_item_candidate']; n=item['planned_input_quantity']
    if (item['fields']['planned_input_quantity']['state']!='supported' or not _integer(n)
            or candidate['unit']!='piece' or not candidate['configuration_id']):
        error('DEMAND_INPUT_UNRESOLVED','Explicit supported quantity, unit and exact configuration required');return r
    with host.request(context) as session:
        query=lookup_contract_configurations(session,data_version=data_version)
    r['query']=query
    if query['configurations'] is None: return r
    details=[c for c in query['configurations']['candidates'] if c['configuration']['id']==candidate['configuration_id']]
    if len(details)!=1:
        error('CONFIGURATION_NOT_FOUND','Exact configuration is not in the public snapshot');return r
    nodes={n['id']:n for n in query['lookup_response']['data']}
    stocks=available_stock(snapshots,scope)
    for selection in stocks['selected']:
        row=selection['observation']; lot=nodes.get(row['lot_id'])
        if (not lot or lot.get('type')!='ComponentLot' or lot.get('component_id')!=row['component_id']
                or lot.get('company')!=row['company']):
            error('LOT_RELATION_MISMATCH','Observation lot/company/component must match exact public snapshot')
    if r['errors']: return r
    source={k:deepcopy(demand[k]) for k in ('demand_id','revision','current_revision','target_sha256',
                                           'identity_mode','status','source','confirmation','unresolved','questions')}
    source.update(state='confirmed' if demand['confirmation']['verified'] else 'unconfirmed',
                  quantity_basis=deepcopy(item['fields']['planned_input_quantity']),item_id=item_id,
                  data_version=data_version,permissions=deepcopy(item['permissions']))
    r['calculation']=calculate_material_requirements(details[0]['items'],stocks,n,
        configuration_id=candidate['configuration_id'],data_version=data_version,demand_source=source,bom_complete=True)
    return r
