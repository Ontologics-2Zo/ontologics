"""Disposable PG review example, fixed manual interpretation, no human approval."""
from __future__ import annotations

from copy import deepcopy
import json
import os

from examples.materials_contract_intake import ROOT, reference_interpretation
from ontologics.materials.contract_intake import extract_contract_text, read_contract_demand
from ontologics.materials.demand_review import build_demand_review


def main():
    if not os.environ.get('TEST_DATABASE_URL'):
        print('Requires an explicitly prepared disposable TEST_DATABASE_URL; no DB selected automatically')
        return 2
    from tests.db import isolated_database
    from ontologics.materials.host import MaterialsHost as CommonHost
    from ontologics.identity import DevelopmentIdentityProvider, UserIdentity, DevelopmentContext
    from ontologics.materials.persistence import MaterialsService as Service

    with isolated_database() as settings:
        seed = Service(settings, principals={'fixture': {'kind': 'human', 'roles': ['read', 'seed']}})
        try:
            response = seed.bootstrap('fixture', json.loads((ROOT/'data/catalog.json').read_text())['nodes'],
                                      request_key='demand-review-catalog-fixture')
            if not response['ok']:
                print('Dedicated catalog fixture failed')
                return 1
            version = response['data_version']
        finally:
            seed.close()
        host = CommonHost(DevelopmentIdentityProvider([
            UserIdentity('demand-review-reader', frozenset({'read'}))], enabled=True), settings)
        extracted = extract_contract_text(ROOT/'data/production_planning/contract.txt')
        interpreted = reference_interpretation(extracted, data_version=version)

        def read(claims):
            # Each invocation opens a fresh request/connection. Exact version
            # comes from this dedicated fixture, never from model assertions.
            with host.request(DevelopmentContext('demand-review-reader')) as session:
                return read_contract_demand(session, {'data_version': version}, extracted, claims)

        current = read(interpreted)
        view = build_demand_review(current)
        if view['status'] != 'needs_confirmation':
            print(json.dumps(view, ensure_ascii=False, indent=2))
            return 1

        wrong = deepcopy(interpreted)
        wrong['items'][0]['fields']['ordered_quantity'][0]['value'] = 120
        rejected = read(wrong)
        wrong_view = build_demand_review(rejected, previous=current)
        corrected = build_demand_review(read(interpreted), previous=rejected)

        # Deliberately incomplete interpretation of the SAME original contract.
        # L/L1 and WROOM below are catalog candidates, not new source statements.
        model_only = deepcopy(interpreted)
        for field in ('revision', 'variant', 'configuration_id'):
            del model_only['items'][0]['fields'][field]
        model_only['items'][0]['proposed_configuration_id'] = 'config:esp32-poe:L1:WROOM'
        model_view = build_demand_review(read(model_only), previous=current)

        def quantity_history(review):
            revision = review['revision']
            def item(report):
                row = report['items'][0]
                return dict(candidate=row['contract_item_candidate']['ordered_quantity'],
                            interpreted=row['fields']['ordered_quantity'], errors=report['errors'])
            return dict(status=review['status'], confirmation=review['confirmation'],
                        before=item(revision['before']), after=item(revision['after']), changes=revision['changes'])

        # The complete result remains available from build_demand_review. This
        # console projection omits repeated full catalog envelopes, not evidence.
        query = view['query']
        output = dict(
            mode='dedicated_pg_manual_interpretation_review_preparation_not_confirmation',
            assumptions='이행0·배정0·신규 검토100은 원문10/11줄에 명시된 시연 가정; 누락 기본값 아님',
            public_query=dict(data_version=version, lookup_ok=query['lookup_response']['ok'],
                configuration_versions=[r['data_version'] for r in query['configuration_responses']],
                run_ids=[query['lookup_response']['run_id'], *[r['run_id'] for r in query['configuration_responses']]],
                errors=query['errors']),
            current=dict(status=view['status'], data_version=view['data_version'],
                confirmation=view['confirmation'], source=current['report']['source'],
                original=current['report']['original'], items=view['items'],
                errors=view['errors'], unresolved=view['unresolved'], questions=view['questions']),
            rejected_quantity=quantity_history(wrong_view), corrected_quantity=quantity_history(corrected),
            model_only_interpretation_demo=dict(
                note='원문은 L/WROOM 명시. 이 사례만 해석 입력에서 의도적으로 생략; L1 선택은 미확인 제안',
                status=model_view['status'], confirmation=model_view['confirmation'],
                items=model_view['items'], questions=model_view['questions']))
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0 if (wrong_view['status'] == 'invalid' and corrected['status'] == 'needs_confirmation'
                     and not view['confirmation']['verified']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
