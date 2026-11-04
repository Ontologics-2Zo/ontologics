"""Resolve existing business references without inventing a plan/contract link."""
import re


def mentioned(identifier,text):
    return bool(re.search(r'(?<![\w:.-])'+re.escape(identifier)+r'(?![\w:.-])',text))


def resolve_context(payload,nodes,text,selection=None):
    index={n['id']:n for n in nodes}
    plan=index.get(payload['baseline_evidence_id'],{})
    if plan.get('type')!='Evidence': raise ValueError('기준 계획 근거를 찾을 수 없습니다.')
    original=plan.get('description','')
    config=index.get(payload['configuration_id'],{})
    candidates=[]
    for item in nodes:
        if item['type']!='ContractItem' or item.get('configuration_id')!=payload['configuration_id'] or item.get('origin')!=payload['origin']: continue
        contract=index.get(item.get('contract_id'),{})
        if contract.get('type')!='Contract': continue
        rounds=[n for n in nodes if n['type']=='DeliveryRound' and n.get('contract_item_id')==item['id']]
        candidates.append(dict(item=item,contract=contract,rounds=rounds))
    def linked(row,body):
        return mentioned(row['item']['id'],body) or mentioned(row['contract']['id'],body) or any(mentioned(r['id'],body) for r in row['rounds'])
    plan_matches=[r for r in candidates if linked(r,original) or plan['id'] in r['item'].get('evidence',[])]
    text_matches=[r for r in candidates if linked(r,text)]
    possible=plan_matches or text_matches
    selected=selection or {}
    if set(selected)-{'contract_item_id','delivery_round_id'}: raise ValueError('허용되지 않은 계약 선택 항목입니다.')
    chosen=None; basis='미확인'
    if selected.get('contract_item_id'):
        chosen=next((r for r in candidates if r['item']['id']==selected['contract_item_id']),None)
        if not chosen: raise ValueError('요청 제품과 같은 등록 계약 품목을 선택하세요.')
        if plan_matches and chosen not in plan_matches: raise ValueError('기준 계획의 계약 참조와 선택한 계약이 다릅니다.')
        if text_matches and chosen not in text_matches: raise ValueError('요청 원문의 계약 참조와 선택한 계약이 다릅니다.')
        basis='사용자 연결 확인'
    elif len(possible)==1:
        chosen=possible[0];basis='기준 계획의 명시적 참조' if plan_matches else '요청 원문의 명시적 참조'
        if plan_matches and text_matches and chosen not in text_matches: raise ValueError('계획과 요청의 계약이 다릅니다.')
    refs={};issues=[];delivery=None
    if chosen:
        refs['contract_item_id']=chosen['item']['id']
        rounds=chosen['rounds']
        if selected.get('delivery_round_id'):
            delivery=next((r for r in rounds if r['id']==selected['delivery_round_id']),None)
            if not delivery: raise ValueError('선택한 납품 차수가 해당 계약 품목에 속하지 않습니다.')
            explicit=[r for r in rounds if mentioned(r['id'],original) or mentioned(r['id'],text)]
            if explicit and delivery not in explicit: raise ValueError('계획/요청의 납품 차수와 선택이 다릅니다.')
        else:
            explicit=[r for r in rounds if mentioned(r['id'],original) or mentioned(r['id'],text)]
            if len(explicit)==1: delivery=explicit[0]
            elif rounds: issues.append('해당 계약 품목의 납품 차수를 선택해 주세요.')
        if delivery: refs['delivery_round_id']=delivery['id']
    else:
        issues.append('기준 계획에서 계약 연결을 확인할 수 없습니다. 어떤 계약 품목의 후속 요청인지 선택해 주세요.' if candidates else '이 제품에 대응하는 계약 품목이 없습니다. 계약 자료 등록이 필요합니다.')
    return dict(ready=bool(chosen) and not issues,references=refs,basis=basis,issues=issues,candidates=candidates,
                configuration=config,plan=dict(id=plan['id'],source_id=plan.get('source_id'),text=original),
                contract=chosen['contract'] if chosen else None,item=chosen['item'] if chosen else None,delivery=delivery,
                scope='납품 차수 후속 요청' if delivery else '계약 품목 후속 요청' if chosen and not chosen['rounds'] else '확인 필요')
