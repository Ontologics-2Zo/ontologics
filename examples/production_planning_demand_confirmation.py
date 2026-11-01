"""Real public PG/identity preflight; persistence is explicitly NOT connected."""
import json
import os

from examples.production_planning_contract_intake import ROOT, reference_interpretation
from ontologics.production_planning.contract_intake import extract_contract_text
from ontologics.production_planning.demand_confirmation import DemandConfirmationWorkflow


def main():
    if not os.environ.get('TEST_DATABASE_URL'):
        print('Requires an explicitly prepared disposable TEST_DATABASE_URL')
        return 2
    from tests.db import isolated_database
    from ontologics.production_planning.host import PlanningHost as CommonHost
    from ontologics.identity import DevelopmentIdentityProvider, DevelopmentContext, UserIdentity
    from ontologics.production_planning.persistence import PlanningService as Service, DomainError

    with isolated_database() as settings:
        seed = Service(settings, principals={'fixture': {'kind': 'human', 'roles': ['seed', 'read']}})
        try:
            result = seed.bootstrap('fixture', json.loads((ROOT/'data/catalog.json').read_text())['nodes'],
                                    request_key='confirmation-module-seed')
            if not result['ok']:
                print('Dedicated catalog seed failed')
                return 1
            version = result['data_version']
        finally:
            seed.close()
        identities = DevelopmentIdentityProvider([
            UserIdentity('module-author', frozenset({'read', 'review'})),
            UserIdentity('module-reviewer', frozenset({'read', 'review'})),
            UserIdentity('module-reader', frozenset({'read'}))], enabled=True)
        host = CommonHost(identities, settings)
        workflow = DemandConfirmationWorkflow(host, identities)
        path = ROOT/'data/production_planning/contract.txt'
        interpreted = reference_interpretation(extract_contract_text(path), data_version=version)
        prepared = workflow.prepare(DevelopmentContext('module-author'), path, interpreted, data_version=version)
        target = prepared['target']
        if target is None:
            print('Public demand preparation failed')
            return 1
        results = []
        for subject, expected in [('module-author', 'SELF_APPROVAL_FORBIDDEN'),
                                  ('module-reader', 'FORBIDDEN'),
                                  ('module-reviewer', 'PERSISTENCE_UNAVAILABLE')]:
            try:
                workflow.submit(DevelopmentContext(subject), target, expected_target_sha256=target.sha256,
                    scopes=[{'item_id': 'contract-item:planning:001', 'scope': 'ordered_quantity'}],
                    note='명시적 개발 시험 요청; 실제 업무 확인 아님', request_key='example-'+subject)
                return 1
            except DomainError as error:
                results.append(dict(subject=subject, code=error.code))
                if error.code != expected:
                    print(json.dumps(results, ensure_ascii=False))
                    return 1
        print(json.dumps(dict(
            mode='dedicated_pg_common_identity_preflight_no_persistence_no_confirmation',
            data_version=version, source_sha256=prepared['review']['revision']['after']['source']['sha256'],
            target_sha256=target.sha256, author_subject=target.author_subject, identity_mode=target.identity_mode,
            status=prepared['review']['status'], confirmation=prepared['review']['confirmation'],
            item=prepared['review']['items'][0]['contract_item_candidate'],
            review_scopes=prepared['review']['items'][0]['review_scopes'],
            checks=results, persistence_connected=False), ensure_ascii=False, indent=2))
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
