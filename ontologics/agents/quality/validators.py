"""Validate Quality answers against common-tool facts, without HTTP dependencies."""
import json
import re

from ...service import DomainError


def quality_observations(results):
    observations = []
    for mode in ('A', 'B'):
        data = results[mode]['data']
        before = {r['unit_id']: r for r in data['before']['results']} if 'before' in data else {}
        after = data['after']['results'] if 'after' in data else data['results']
        for row in after:
            old = before.get(row['unit_id'])
            observations.append({'mode': mode, 'unit_id': row['unit_id'],
                'before': None if old is None else {'status': old['status'], 'evidence': sorted(old['evidence'])},
                'after': {'status': row['status'], 'evidence': sorted(row['evidence'])}})
    return observations


def verified_quality_text(text, results):
    try:
        answer = json.loads(text)
        if not isinstance(answer, dict) or set(answer) not in ({'observations'}, {'observations', 'explanation'}):
            raise ValueError()
        expected = quality_observations(results)
        observed = answer['observations']
        if not isinstance(observed, list) or len(observed) != len(expected):
            raise ValueError()
        for item in observed:
            for phase in ('before', 'after'):
                if item[phase] is not None:
                    item[phase]['evidence'] = sorted(item[phase]['evidence'])
        order = lambda x: (x['mode'], x['unit_id'])
        if sorted(observed, key=order) != sorted(expected, key=order):
            raise ValueError()
        # Free model prose is never displayed: correct structured facts can
        # still contradict a model's accompanying summary.
    except (ValueError, TypeError, KeyError):
        raise DomainError('AI_FACT_MISMATCH', 'AI의 보드별 판정·근거가 공통 도구와 일치하지 않아 설명을 표시하지 않습니다.') from None
    labels = {'included': '장착 이력 확인', 'excluded': '조건상 제외', 'needs_confirmation': '추가 이력 확인 필요'}
    lines = ['A는 기준 시점에 장착되어 있는지, B는 장착 구간과 조사 기간이 겹치는지를 확인합니다. '
             'B는 시작 포함·끝 제외 [start,end)입니다. 제거 시점·전체 이력 근거가 부족하면 추가 확인을 유지합니다. '
             '장착 이력 확인은 결함·고장 확인이 아니며 조건상 제외도 안전 판정이 아닙니다.',
             '아래는 실제 모델의 구조화 답을 공통 도구와 대조한 판정·근거입니다. 설명 문장은 검증된 값으로 서버가 구성합니다.', '']
    for item in expected:
        parts = []
        for phase, label in (('before', '보완 전'), ('after', '보완 후' if item['before'] else '조회 결과')):
            value = item[phase]
            if value is not None:
                parts.append(label + ' ' + labels[value['status']] + ' / 근거: ' + (', '.join(value['evidence']) or '없음'))
        lines.append(item['unit_id'] + ' · ' + item['mode'] + ': ' + ' → '.join(parts))
    return '\n'.join(lines), expected


def validated_quality_proposal(text, question, results):
    try:
        proposal = json.loads(text)
        if not isinstance(proposal, dict) or set(proposal) != {'unit_ids', 'lot_id', 'at', 'start', 'end'}:
            raise ValueError()
        units, lots = results
        unit_ids = {n['id'] for n in units['data']}
        lot_ids = {n['id'] for n in lots['data']}
        if not isinstance(proposal['unit_ids'], list) or len(proposal['unit_ids']) > 50 or len(set(proposal['unit_ids'])) != len(proposal['unit_ids']):
            raise ValueError()
        if not set(proposal['unit_ids']) <= unit_ids or (proposal['lot_id'] is not None and proposal['lot_id'] not in lot_ids):
            raise ValueError()
        def mentioned(token):
            return isinstance(token, str) and bool(token) and bool(re.search(
                r'(?<![A-Za-z0-9_:.-])' + re.escape(token) + r'(?![A-Za-z0-9_:.-])', question, re.I))
        explicit_units = {n['id'] for n in units['data'] if mentioned(n['id']) or mentioned(n.get('serial'))}
        # Valid graph IDs are not enough: they must originate in the question,
        # and an explicit additional board cannot silently disappear.
        if set(proposal['unit_ids']) != explicit_units:
            proposal['unit_ids'] = []
        selected_lot = next((n for n in lots['data'] if n['id'] == proposal['lot_id']), None)
        if selected_lot:
            code = selected_lot.get('lot_code')
            unique_code = sum(n.get('lot_code') == code for n in lots['data']) == 1
            if not (mentioned(selected_lot['id']) or (unique_code and mentioned(code))):
                proposal['lot_id'] = None
        from ...temporal import parse_time
        for key in ('at', 'start', 'end'):
            if proposal[key] is not None:
                # A guessed timestamp must not become a real query; only copy
                # explicit offsets/instants supplied in the user's question.
                if not isinstance(proposal[key], str) or proposal[key] not in question:
                    proposal[key] = None
                else:
                    parse_time(proposal[key])
        if proposal['start'] and proposal['end'] and parse_time(proposal['start']) >= parse_time(proposal['end']):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise DomainError('AI_PLAN_INVALID', 'AI가 제안한 조사 조건을 확인하지 못했습니다. 대상·시각을 직접 입력하세요.') from None
    return proposal
