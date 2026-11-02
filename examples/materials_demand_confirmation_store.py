"""Actual isolated PG save/confirm/reconnect/revise. Development identities only."""
from copy import deepcopy
import json
import os

from examples.materials_contract_intake import ROOT, reference_interpretation
from ontologics.materials.contract_intake import extract_contract_text
from ontologics.materials.demand_confirmation import DemandConfirmationWorkflow
from ontologics.materials.demand_confirmation_store import PersistentDemandConfirmationWorkflow


def main():
    if not os.environ.get('TEST_DATABASE_URL'):
        print('Requires an explicitly prepared disposable TEST_DATABASE_URL')
        return 2
    from tests.db import isolated_database
    from ontologics.materials.host import MaterialsHost as CommonHost
    from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
    from ontologics.materials.persistence import MaterialsService as Service

    with isolated_database() as settings:
        service = Service(settings, principals={'fixture': {'kind': 'human', 'roles': ['seed', 'read']}})
        try:
            seeded = service.bootstrap('fixture', json.loads((ROOT/'data/catalog.json').read_text())['nodes'],
                                       request_key='persistent-example-seed')
            if not seeded['ok']:
                return 1
        finally:
            service.close()
        identities = DevelopmentIdentityProvider([
            UserIdentity('persistent-author', frozenset({'read', 'draft'})),
            UserIdentity('persistent-reviewer', frozenset({'read', 'review'})),
            UserIdentity('persistent-reader', frozenset({'read'}))], enabled=True)
        host = CommonHost(identities, settings)
        workflow = PersistentDemandConfirmationWorkflow(host, identities, settings)
        preflight = DemandConfirmationWorkflow(host, identities)
        author, reviewer, reader = [DevelopmentContext('persistent-'+s) for s in ('author', 'reviewer', 'reader')]
        path = ROOT/'data/production_planning/contract.txt'
        interpreted = reference_interpretation(extract_contract_text(path), data_version=seeded['data_version'])
        target = preflight.prepare(author, path, interpreted, data_version=seeded['data_version'])['target']
        def ok(response):
            if not response['ok']:
                raise RuntimeError('Dedicated persistent example failed: '+str([e['code'] for e in response['errors']]))
            return response['data']
        saved = ok(workflow.save(author, target, request_key='persistent-save'))
        scopes = [dict(item_id='contract-item:planning:001', scope=name) for name in (
            'configuration', 'ordered_quantity', 'fulfilled_quantity', 'allocated_quantity',
            'planned_input_quantity', 'delivery_value')]
        event = ok(workflow.confirm(reviewer, saved['demand_id'], 1, expected_target_sha256=saved['target_sha256'],
            scopes=scopes, note='개발 신원의 명시적 확인 사건 시험; 실제 운영 사람 확인 아님', request_key='persistent-confirm'))
        workflow = PersistentDemandConfirmationWorkflow(CommonHost(identities, settings), identities, settings)
        reconnected = ok(workflow.get(reader, saved['demand_id'], revision=1))
        amended = deepcopy(interpreted)
        amended['items'][0]['proposed_configuration_id'] = 'config:esp32-poe:L:WROOM'
        new_target = preflight.prepare(author, path, amended, data_version=seeded['data_version'])['target']
        revised = ok(workflow.save(author, new_target, demand_id=saved['demand_id'], expected_revision=1,
                                  request_key='persistent-revise'))
        old = ok(workflow.get(reader, saved['demand_id'], revision=1))
        current = ok(workflow.get(reader, saved['demand_id'], revision=2))
        print(json.dumps(dict(mode='actual_disposable_pg_persistence_development_identity_not_operational_confirmation',
            saved=saved, recorded=event, reconnected=dict(status=reconnected['status'],
                confirmation=reconnected['confirmation'], source=reconnected['source']),
            revised=revised, old_revision=dict(status=old['status'], confirmation=old['confirmation'], events=old['events']),
            current=dict(status=current['status'], confirmation=current['confirmation'], questions=current['questions'])),
            ensure_ascii=False, indent=2))
        return 0 if (len(reconnected['confirmation']['verified_scopes']) == 6 and
            not reconnected['confirmation']['verified'] and old['status'] == 'superseded' and
            current['status'] == 'needs_confirmation' and len(old['events']) == 2) else 1


if __name__ == '__main__':
    raise SystemExit(main())
