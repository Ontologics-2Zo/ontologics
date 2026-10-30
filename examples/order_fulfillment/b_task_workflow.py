"""Free ten-case B input workflow using unchanged common Task contracts.

This validates wiring, not LLM comprehension, human review, or paid readiness.
The caller owns a fresh database. No environment loading or paid backend exists.
"""
from __future__ import annotations

import json
from pathlib import Path

from examples.order_fulfillment import (
    b_intake_task as intake, b_promise_agent as promise, b_fulfillment_task as fulfillment,
    b_production_task as production, b_delivery_task as delivery,
    b_response_task as response, b_comparison_task as comparison)
from examples.order_fulfillment.b_input_task import json_sha256
from examples.order_fulfillment.b_run_isolation import checked_path, save
from examples.order_fulfillment.b_run_transport import CASES
from ontologics.database import DatabaseSettings
from ontologics.host import AgentRegistration, CommonHost
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.model import ROOT
from ontologics.service import Service


ROLES = (
    (intake, intake.IntakeCandidateAgent, intake.ScriptedIntakeProvider),
    (promise, promise.PromiseAgent, promise.ScriptedPromiseProvider),
    (fulfillment, fulfillment.FulfillmentAgent, fulfillment.ScriptedFulfillmentProvider),
    (production, production.ProductionAgent, production.ScriptedProductionProvider),
    (delivery, delivery.DeliveryAgent, delivery.ScriptedDeliveryProvider),
    (response, response.ResponseAgent, response.ScriptedResponseProvider),
    (comparison, comparison.ComparisonAgent, comparison.ScriptedComparisonProvider))


def case_task(case, *, version=1):
    """Reuse B calculations and task builders; no graph registration or approval."""
    if case not in CASES:
        raise ValueError('Unknown fixed B representative')
    index = CASES.index(case)
    role = ROLES[(0, 1, 2, 3, 4, 4, 5, 5, 6, 6)[index]]
    module = role[0]
    if module is intake:
        task = intake.make_demo_task(data_version=version)
    elif module is response:
        task = response.make_demo_task(mixed=case.endswith('mixed'), version=version)
    elif module is comparison:
        task = comparison.make_demo_task(case=case.removeprefix('cq06_'), version=version)
    else:
        nodes = json.loads((ROOT / 'data/catalog.json').read_text())['nodes'] + promise.load_demo_contract()
        if module is promise:
            task = promise.make_promise_task(nodes, 'delivery-round:demo:B-01:1',
                input_revision='B-01-v1', data_version=version)
        else:
            options = dict(queried_at='2026-10-27T10:10:00+09:00', data_version=version)
            if module is fulfillment:
                task = fulfillment.make_fulfillment_task(nodes + fulfillment.load_demo_fulfillment(),
                    fulfillment.ROUND, fulfillment.AS_OF, input_revision='B-CQ02-v1', **options)
            elif module is production:
                task = production.make_production_task(nodes + production.load_demo_production(),
                    production.WORK, production.ROUND, production.AS_OF, input_revision='B-CQ03-v1', **options)
            else:
                mixed = case.endswith('mixed')
                task = delivery.make_delivery_task(nodes + delivery.load_demo_delivery(mixed=mixed),
                    delivery.ROUND, delivery.AS_OF,
                    input_revision='B-CQ04-mixed-v1' if mixed else 'B-CQ04-normal-v1', **options)
    return role, task


def _execute_case(host, case, task, role):
    module, agent, provider = role
    # Each representative has its own common request budget. This free suite
    # never resets a running request or carries a paid cost policy across runs.
    with host.request(DevelopmentContext('b-input-reader')) as request:
        return request.run_task(module.SPEC.name, agent(), task, provider(task),
                                config=module.CONFIG, capture_tool_trace=True)


def _review_material(case, data):
    if 'review_material' in data:
        material = data['review_material']
        digest = json_sha256(material)
        if digest != data['assessment']['review_material_sha256']:
            raise ValueError('B review material does not match the pinned assessment')
        return dict(kind='deterministic_input_comparison' if case.startswith('cq06_')
                    else 'deterministic_input_draft', sha256=digest, data=material)
    if 'calculation_details' in data:
        return dict(kind='deterministic_calculation_details',
                    sha256=json_sha256(data['calculation_details']), data=data['calculation_details'])
    return None


def run_campaign(*, database, output, cases):
    """Run a canonical subset once with scripted providers and catalog-only seed.

    The owner retains its new database even if report preservation fails. This
    function closes connections but never deletes a schema or prior run output.
    """
    if not isinstance(database, DatabaseSettings):
        raise ValueError('Explicit fresh development DatabaseSettings required')
    if (not isinstance(cases, list) or not cases or any(c not in CASES for c in cases)
            or cases != [c for c in CASES if c in cases]):
        raise ValueError('Use a unique canonical ordered subset of the ten cases')
    output = checked_path(Path(output))
    output.mkdir(mode=0o700)  # Atomic duplicate-run rejection, never resume.
    reports = []
    active = None
    audit = None
    total = 0
    save(output / 'run.json', dict(execution_mode='scripted', workflow='input_tasks',
        cases=cases, database_schema=database.schema, actual_api_attempts=0,
        human_review='not_performed', live_authorization_eligible=False))
    try:
        audit = Service(database, principals={'seed': {'kind': 'human', 'roles': ['read', 'seed']}})
        catalog = json.loads((ROOT / 'data/catalog.json').read_text())['nodes']
        if audit._version() != 0:
            raise ValueError('A fresh empty catalog-only database is required')
        seeded = audit.bootstrap('seed', catalog,
                                 request_key='b-input-catalog-only')
        if not seeded['ok']:
            raise ValueError('A fresh empty catalog-only database is required')
        version = seeded['data_version']
        identities = DevelopmentIdentityProvider([
            UserIdentity('b-input-reader', frozenset({'read'}))], enabled=True)
        host = CommonHost(identities, database,
            agent_registrations=tuple(AgentRegistration.from_spec(r[0].SPEC) for r in ROLES))
        for case in cases:
            active = case
            if total + 4 > 40:
                raise ValueError('The free suite cannot exceed forty model calls')
            role, task = case_task(case, version=version)
            data = task.data()
            directory = output / case
            directory.mkdir()
            save(directory / 'input.json', data)
            material = _review_material(case, data)
            if material is not None:
                save(directory / 'review_material.json', material)
            result = _execute_case(host, case, task, role)
            save(directory / 'result.json', dict(execution_mode='scripted', actual_api_attempts=0,
                answer_producer='scripted_provider',
                human_review='not_performed', result=result))
            calls = result['request_execution_budget']['attempted_calls']
            total += calls['model']
            if (not result['runtime']['ok'] or result['error'] is not None or result['payload'] is None
                    or not 1 <= calls['model'] <= 4 or calls['repair'] != 0 or total > 40):
                raise ValueError('Representative execution or bounded result validation failed')
            if case == 'cq01_intake' and result['payload']['extraction']['status'] != 'ready':
                raise ValueError('Intake needs source correction; subsequent representatives stopped')
            reports.append(dict(case=case, agent_name=result['agent_name'],
                model_calls=calls['model'], result_path=case + '/result.json',
                review_material_path=case + '/review_material.json' if material is not None else None))
        if (audit._version() != version or audit._nodes() != catalog
                or audit.db.execute('SELECT COUNT(*) AS n FROM drafts').fetchone()['n']
                or audit.db.execute('SELECT COUNT(*) AS n FROM review_events').fetchone()['n']):
            raise ValueError('Input-only validation must not alter business storage')
        summary = dict(passed=True, execution_mode='scripted', workflow='input_tasks',
            cases=cases, completed_representatives=len(reports), representatives=reports,
            model_calls=total, actual_api_attempts=0, data_version=version,
            b_business_stored=False, human_review='not_performed',
            live_quality_verified=False, live_authorization_eligible=False)
        save(output / 'summary.json', summary)
        return summary
    except BaseException as error:
        save(output / 'stopped.json', dict(stopped=True, case=active, error_type=type(error).__name__,
            completed_cases=[r['case'] for r in reports], model_calls=total,
            actual_api_attempts=0, automatic_paid_rerun=False, database_schema_retained=database.schema))
        raise
    finally:
        if audit is not None:
            audit.close()
