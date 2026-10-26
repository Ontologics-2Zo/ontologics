"""Development users -> S2 review/revision -> S1 supplement. No login or LLM call."""
import argparse
import json
import uuid

from ontologics.database import DatabaseSettings
from ontologics.demo import CONFIG_L1, example_queries, fixture_nodes, must
from ontologics.host import CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.ingest import build_catalog
from ontologics.service import Service


def run_example(database):
    users = [UserIdentity('dev-alice', frozenset({'read', 'draft', 'review'})),
             UserIdentity('dev-bob', frozenset({'read', 'review'}))]
    identities = DevelopmentIdentityProvider(users, enabled=True)
    host = CommonHost(identities, database)
    alice, bob = DevelopmentContext('dev-alice'), DevelopmentContext('dev-bob')
    catalog = build_catalog()['nodes']
    registration = [n for n in catalog if n['id'] == CONFIG_L1 or n.get('configuration_id') == CONFIG_L1]
    excluded = {n['id'] for n in registration}
    fictional, supplement = fixture_nodes()
    seed = Service(database, principals={'example-seed': {'kind': 'human', 'roles': ['seed']}})
    try:
        must(seed.bootstrap('example-seed', [n for n in catalog if n['id'] not in excluded] + fictional,
                            request_key='development-fixture'))
    finally:
        seed.close()

    def expected_error(result, code):
        if result['ok'] or result['errors'][0]['code'] != code:
            raise RuntimeError('Expected workflow guard: ' + code)
        return code

    with host.request(alice) as request:
        context = request.context
        operations = [{'op': 'put', 'node': n} for n in registration]
        draft = must(request.agent('product').call('prepare_draft',
                     {'operations': operations, 'request_key': 'register-L1'}))['data']
        did, version = draft['draft_id'], draft['draft_version']
        saved = must(request.call('get_draft', {'draft_id': did, 'version': version}))['data']
        self_approval = expected_error(request.review(did, version, decision='approve',
            note='개발용 자기 승인 차단 확인', request_key='self-review'), 'SELF_APPROVAL_FORBIDDEN')
    unapproved = expected_error(host.apply_approved(did, version, request_key='apply-L1'), 'APPROVAL_REQUIRED')
    with host.request(bob) as reviewer:
        must(reviewer.review(did, version, decision='request_changes',
            note='개발 예제: 원본 행과 미확인 표시를 다시 확인', request_key='changes-L1'))
    with host.request(alice) as request:
        revised = must(request.agent('product').call('prepare_draft', {'operations': operations,
            'draft_id': did, 'expected_version': version, 'request_key': 'revise-L1'}))['data']
    stale_version = expected_error(host.apply_approved(did, version, request_key='old-L1'), 'SUPERSEDED_DRAFT')
    with host.request(bob) as reviewer:
        must(reviewer.review(did, revised['draft_version'], decision='approve',
            note='개발 사용자 대역: 정확한 초안 버전 검토', request_key='approve-L1'))
    registered = must(host.apply_approved(did, revised['draft_version'], request_key='apply-L1'))
    with host.request(alice) as request:
        configuration = must(request.call('configuration', {'configuration_id': CONFIG_L1}))
        proposal = must(request.agent('product').call('prepare_draft',
                        {'operations': supplement, 'request_key': 'supplement-U104'}))['data']
    sid, sv = proposal['draft_id'], proposal['draft_version']
    with host.request(bob) as reviewer:
        must(reviewer.review(sid, sv, decision='approve', note='개발 가상 기록의 제거·완전 이력 근거 검토',
                             request_key='approve-supplement'))
    supplemented = must(host.apply_approved(sid, sv, request_key='apply-supplement'))
    replay = must(host.apply_approved(sid, sv, request_key='apply-supplement'))
    comparisons = {}
    with host.request(alice) as request:
        for mode, query in example_queries().items():
            comparisons[mode] = must(request.agent('quality').call('compare_investigation', {
                'query': query, 'before_version': registered['data_version'],
                'after_version': supplemented['data_version']}))
    return {'mode': 'development identities; no login, no live model call', 'request_context': context,
        'database_schema': database.schema, 'draft_author': saved['author'],
        'reviewer': users[1].actor, 'self_approval': self_approval, 'unapproved_apply': unapproved,
        'old_version_apply': stale_version, 'approved_draft_version': revised['draft_version'],
        'registered_bom_rows': len(configuration['data']['items']),
        'registered_version': registered['data_version'], 'supplemented_version': supplemented['data_version'],
        'replay': [w['code'] for w in replay['warnings']],
        'comparisons': {mode: {'query': r['data']['query'], 'before_version': r['data']['before_version'],
            'after_version': r['data']['after_version'], 'evidence': r['evidence'],
            'before': {n['unit_id']: n['status'] for n in r['data']['before']['results']},
            'after': {n['unit_id']: n['status'] for n in r['data']['after']['results']}}
            for mode, r in comparisons.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', help='Local KEY=value settings file; never commit credentials')
    args = parser.parse_args()
    database = DatabaseSettings.from_environment(env_file=args.env_file, schema='host_demo_' + uuid.uuid4().hex)
    print(json.dumps(run_example(database), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
