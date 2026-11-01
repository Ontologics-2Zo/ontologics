"""Post-merge host wiring regression; no database or external model calls."""
import unittest
from unittest.mock import patch

from ontologics.contracts import AgentSpec, FieldSpec, PayloadSchema
from ontologics.identity import DevelopmentContext
from ontologics.harness import BudgetLimits, ExecutionBudget, PinnedContext, current_budget
from ontologics.paid_budget import CostPolicy, ModelPrice
from ontologics.service import DomainError
from ontologics.order_fulfillment import host as b_host
from ontologics.order_fulfillment.runtime import ModelRuntime
from ontologics.runtime import ModelConfig
from tests.order_fulfillment import test_host_boundary as baseline


class MainHostCompatibilityTests(unittest.TestCase):
    setUp = baseline.HostBoundaryTests.setUp
    service_scope = baseline.HostBoundaryTests.service_scope
    def test_request_forwards_sandbox_identity_lookup_and_shared_cost_policy(self):
        sandbox = object()
        policy = CostPolicy('1', (ModelPrice('fixed-test', '1', '1', 400000),))
        host = b_host.CommonHost(self.identities, self.database,
            agent_registrations=(self.registration,), sandbox_client=sandbox, cost_policy=policy)
        created, factory = self.service_scope()
        with factory, host.request(DevelopmentContext(self.reader.subject)) as session:
            self.assertIs(sandbox, session._service.sandbox_client)
            self.assertEqual(self.identities.lookup, session._identity_lookup)
            self.assertIs(policy, session._budget.cost_policy)
            self.assertIs(session._budget, current_budget())
        self.assertTrue(created[0].closed)
        self.assertIsNone(current_budget())

    def test_direct_and_role_calls_consume_one_shared_budget(self):
        for call_role in (False, True):
            with self.subTest(role=call_role):
                budget = ExecutionBudget(BudgetLimits(max_tool_calls=1))
                _, factory = self.service_scope()
                with factory, self.host.request(DevelopmentContext(self.reader.subject), budget=budget) as session:
                    gateway = session.agent('b_promise') if call_role else session
                    self.assertTrue(gateway.call('delivery_round', {'delivery_round_id': 'round:1', 'version': 1})['ok'])
                    with self.assertRaises(DomainError) as caught:
                        gateway.call('delivery_round', {'delivery_round_id': 'round:1', 'version': 1})
                    self.assertEqual('SHARED_BUDGET_EXCEEDED', caught.exception.code)
                    self.assertIs(budget, session._budget)

    def test_nested_same_identity_cannot_replace_request_budget(self):
        _, factory = self.service_scope()
        with factory, self.host.request(DevelopmentContext(self.reader.subject)) as outer:
            with self.host.request(DevelopmentContext(self.reader.subject)) as inner:
                self.assertIs(outer._budget, inner._budget)
            with self.assertRaises(DomainError) as caught:
                with self.host.request(DevelopmentContext(self.reader.subject), budget=ExecutionBudget()):
                    self.fail('nested request replaced shared limits')
            self.assertEqual('EXECUTION_CONTEXT_MISMATCH', caught.exception.code)

    def test_run_agent_preserves_child_budget_context_validator_and_observer(self):
        _, factory = self.service_scope()
        pinned = PinnedContext('Read the approved snapshot.', 1)
        validator, observer = lambda text: text, lambda *args: None
        with factory, self.host.request(DevelopmentContext(self.reader.subject)) as session:
            budget = session._budget.child(BudgetLimits(max_model_calls=4))
            with patch.object(b_host, 'ModelRuntime') as runtime:
                session.run_agent('b_promise', lambda **kwargs: None, [],
                    config=ModelConfig(model='fixed-test'), budget=budget,
                    pinned_context=pinned, final_validator=validator, tool_observer=observer,
                    task_tools={'delivery_round'})
                kwargs = runtime.call_args.kwargs
                self.assertIs(budget, kwargs['budget'])
                self.assertIs(pinned, kwargs['pinned_context'])
                self.assertIs(validator, kwargs['final_validator'])
                self.assertIs(observer, kwargs['tool_observer'])
                self.assertEqual(frozenset({'delivery_round'}), kwargs['allowed_tools'])

    def test_run_agent_refuses_unrelated_budget_or_unregistered_task_tools(self):
        _, factory = self.service_scope()
        with factory, self.host.request(DevelopmentContext(self.reader.subject)) as session:
            for kwargs, code in (({'budget': ExecutionBudget()}, 'EXECUTION_CONTEXT_MISMATCH'),
                                 ({'task_tools': {'prepare_draft'}}, 'TOOL_NOT_ALLOWED')):
                with self.subTest(code=code), patch.object(b_host, 'ModelRuntime') as runtime:
                    with self.assertRaises(DomainError) as caught:
                        session.run_agent('b_promise', lambda **kwargs: None, [],
                                          config=ModelConfig(model='fixed-test'), **kwargs)
                    self.assertEqual(code, caught.exception.code)
                    runtime.assert_not_called()

    def test_runtime_preserves_common_execution_options(self):
        service = baseline.ServiceDouble(self.database, principals={self.reader.actor: self.reader.principal()})
        budget = ExecutionBudget()
        pinned = PinnedContext('Read.', 1)
        validator, observer = lambda text: text, lambda *args: None
        runtime = ModelRuntime(service, self.reader.actor, lambda **kwargs: None,
            config=ModelConfig(model='fixed-test'), allowed_tools={'delivery_round'},
            budget=budget, pinned_context=pinned, final_validator=validator, tool_observer=observer)
        self.assertIs(budget, runtime._budget)
        self.assertIs(pinned, runtime._pinned_context)
        self.assertIs(validator, runtime._final_validator)
        self.assertIs(observer, runtime._tool_observer)

    def spec(self, name='b_read_typed', tools=('lookup',)):
        schema = PayloadSchema((FieldSpec('data_version', 'integer'),))
        return AgentSpec(name, '1.0.0', 'Read existing approved facts.', schema, schema, tools)

    def test_common_only_typed_registration_preserves_validated_spec(self):
        spec = self.spec()
        registration = b_host.AgentRegistration.from_spec(spec)
        self.assertIs(spec, registration.spec)
        self.assertEqual(spec.requested_tools, registration.allowed_tools)

    def test_typed_registration_rejects_mismatched_spec(self):
        spec = self.spec()
        for name, instructions, tools in ((spec.name, 'Different instructions.', spec.requested_tools),
                                          ('b_wrong_name', spec.instructions, spec.requested_tools),
                                          (spec.name, spec.instructions, {'delivery_round'})):
            with self.subTest(name=name, tools=tools), self.assertRaises(ValueError):
                b_host.AgentRegistration(name, instructions, tools, spec)

    def test_b_domain_tools_stay_outside_public_agentspec_contract(self):
        with self.assertRaises(ValueError):
            self.spec(tools=('delivery_round',))

