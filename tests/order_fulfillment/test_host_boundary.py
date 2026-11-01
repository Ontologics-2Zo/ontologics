"""Offline B composition boundaries; no database, live API or model bypass."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from psycopg.pq import TransactionStatus

from ontologics.database import DatabaseSettings
from ontologics.host import (AgentRegistration as CoreRegistration, CommonHost as CoreHost,
                            RequestSession as CoreSession, ROLE_TOOLS)
from ontologics.identity import (DevelopmentContext, DevelopmentIdentityProvider,
                                IdentityError, UserIdentity)
from ontologics.runtime import ModelConfig, ModelRuntime as CoreRuntime
from ontologics.service import AgentGateway as CoreGateway, DomainError, Service as CoreService
from ontologics.order_fulfillment import host as b_host
from ontologics.order_fulfillment.host import AgentRegistration, CommonHost, RequestSession
from ontologics.order_fulfillment.runtime import ModelRuntime
from ontologics.order_fulfillment.service import AgentGateway


class ServiceDouble:
    """Records host wiring; permission decisions reuse the common implementation."""

    _require = CoreService._require

    def __init__(self, database, *, principals):
        self.database, self.principals = database, principals
        self.closed = False
        self.db = SimpleNamespace(info=SimpleNamespace(transaction_status=TransactionStatus.IDLE))
        self.calls = []

    def close(self):
        self.closed = True

    def _run(self, actor, tool, arguments, action, *, permission='read'):
        self.calls.append((actor, tool, arguments))
        try:
            self._require(actor, permission)
            return action()
        except DomainError as exc:
            return {'ok': False, 'errors': [{'code': exc.code}]}

    def delivery_round(self, actor, delivery_round_id, *, version=None):
        return self._run(actor, 'delivery_round', {'version': version},
                         lambda: {'ok': True, 'data': {'id': delivery_round_id, 'actor': actor}})

    def prepare_draft(self, actor, **arguments):
        return self._run(actor, 'prepare_draft', arguments,
                         lambda: {'ok': True}, permission='draft')


class HostBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.reader = UserIdentity('b-reader', frozenset({'read'}))
        self.identities = DevelopmentIdentityProvider([self.reader], enabled=True)
        self.database = DatabaseSettings('postgresql://unused/offline', 'test_b_host_boundary')
        self.registration = AgentRegistration('b_promise', 'Preserve explicit promise scope.',
                                              ('delivery_round', 'lookup'))
        self.host = CommonHost(self.identities, self.database,
                               agent_registrations=(self.registration,))

    def service_scope(self, host=None):
        created = []
        def factory(*args, **kwargs):
            value = ServiceDouble(*args, **kwargs)
            created.append(value)
            return value
        return created, patch.object(b_host, 'Service', side_effect=factory)

    def test_request_preserves_resolved_identity_and_closes_on_failure(self):
        created, factory = self.service_scope()
        with factory:
            with self.assertRaisesRegex(RuntimeError, 'consumer failure'):
                with self.host.request(DevelopmentContext(self.reader.subject)) as session:
                    self.assertIsInstance(session, RequestSession)
                    self.assertEqual({'subject': self.reader.subject, 'actor': self.reader.actor,
                                      'identity_mode': 'development', 'permissions': ['read']}, session.context)
                    self.identities._users[self.reader.subject] = UserIdentity(self.reader.subject, frozenset())
                    result = session.call('delivery_round', {'delivery_round_id': 'round:1', 'version': 2})
                    self.assertTrue(result['ok'])
                    self.assertEqual(self.reader.actor, result['data']['actor'])
                    self.assertEqual({self.reader.actor: self.reader.principal()}, created[0].principals)
                    raise RuntimeError('consumer failure')
        self.assertTrue(created[0].closed)
        self.assertEqual(1, len(created))

    def test_next_request_refreshes_permissions_without_granting_role_rights(self):
        created, factory = self.service_scope()
        with factory:
            for permissions, expected in (({'read'}, True), (set(), False)):
                self.identities._users[self.reader.subject] = UserIdentity(self.reader.subject, frozenset(permissions))
                with self.host.request(DevelopmentContext(self.reader.subject)) as session:
                    result = session.agent('b_promise').call('delivery_round',
                        {'delivery_round_id': 'round:1', 'version': 1})
                    self.assertEqual(expected, result['ok'])
                    if not expected:
                        self.assertEqual('FORBIDDEN', result['errors'][0]['code'])
        self.assertEqual(2, len(created))
        self.assertTrue(all(service.closed for service in created))

    def test_gateway_keeps_identity_approval_and_builtin_role_boundaries(self):
        _, factory = self.service_scope()
        with factory, self.host.request(DevelopmentContext(self.reader.subject)) as session:
            for tool, arguments, code in (
                ('delivery_round', {'actor': 'user:someone-else'}, 'INVALID_TOOL_ARGUMENTS'),
                ('review', {}, 'TOOL_NOT_ALLOWED'), ('apply', {}, 'TOOL_NOT_ALLOWED')):
                with self.subTest(tool=tool):
                    result = session.agent('b_promise').call(tool, arguments)
                    self.assertEqual(code, result['errors'][0]['code'])
            self.assertEqual(ROLE_TOOLS['product'], session.agent('product').allowed_tools)
            result = session.agent('product').call('delivery_round', {})
            self.assertEqual('TOOL_NOT_ALLOWED', result['errors'][0]['code'])
            result = session.call('prepare_draft', {'request_key': 'no-draft-rights'})
            self.assertEqual('FORBIDDEN', result['errors'][0]['code'])

    def test_unavailable_or_invalid_identity_never_constructs_service(self):
        for resolution, code in ((RuntimeError('private identity backend'), 'IDENTITY_PROVIDER_UNAVAILABLE'),
                                 (None, 'INVALID_IDENTITY'),
                                 (IdentityError('UNKNOWN_IDENTITY', 'Unknown'), 'UNKNOWN_IDENTITY')):
            with self.subTest(code=code):
                with patch.object(self.identities, 'resolve', side_effect=resolution if isinstance(resolution, Exception) else None,
                                  return_value=resolution), patch.object(b_host, 'Service') as service:
                    with self.assertRaises(IdentityError) as caught:
                        with self.host.request(DevelopmentContext(self.reader.subject)):
                            self.fail('Unresolved identity entered a request')
                    self.assertEqual(code, caught.exception.code)
                    service.assert_not_called()

    def test_registration_uses_immutable_tools_and_rejects_authority(self):
        tools = ['delivery_round']
        registration = AgentRegistration('b_isolated', 'Read promises.', tools)
        tools.append('apply')
        self.assertEqual(frozenset({'delivery_round'}), registration.allowed_tools)
        self.assertIsInstance(registration, CoreRegistration)
        for name, instructions, allowed in (
                ('product', 'Read.', ['delivery_round']), ('../role', 'Read.', ['lookup']),
                ('b_bad', '', ['lookup']), ('b_bad', 'Read.', 'lookup'),
                ('b_bad', 'Read.', ['lookup', 'lookup']), ('b_bad', 'Read.', ['review']),
                ('b_bad', 'Read.', ['apply']), ('b_bad', 'Read.', ['bootstrap'])):
            with self.subTest(name=name, tools=allowed), self.assertRaises(ValueError):
                AgentRegistration(name, instructions, allowed)
        with self.assertRaises(ValueError):
            CommonHost(self.identities, self.database,
                       agent_registrations=(registration, registration))

    def test_run_agent_binds_instructions_and_uses_b_runtime(self):
        messages = [{'role': 'user', 'content': 'Read the explicit promise.'}]
        config = ModelConfig(model='scripted-b-boundary')
        provider = lambda **kwargs: {'text': 'done', 'tool_calls': []}
        _, factory = self.service_scope()
        with factory, self.host.request(DevelopmentContext(self.reader.subject)) as session:
            with patch.object(b_host, 'ModelRuntime') as runtime:
                runtime.return_value.run.return_value = {'ok': True}
                self.assertEqual({'ok': True}, session.run_agent('b_promise', provider, messages,
                                                               config=config, capture_tool_trace=True))
                args, kwargs = runtime.call_args
                self.assertEqual((session._service, self.reader.actor, provider), args)
                self.assertEqual(self.registration.allowed_tools, kwargs['allowed_tools'])
                self.assertIs(config, kwargs['config'])
                self.assertIs(True, kwargs['capture_tool_trace'])
                submitted = runtime.return_value.run.call_args.args[0]
                self.assertEqual('system', submitted[0]['role'])
                self.assertIn(self.registration.instructions, submitted[0]['content'])
                self.assertEqual(messages, submitted[1:])
                self.assertEqual(1, len(messages))

    def test_runtime_reuses_common_execution_with_instance_local_gateway(self):
        service = ServiceDouble(self.database, principals={self.reader.actor: self.reader.principal()})
        config = ModelConfig(model='scripted-b-boundary')
        provider = lambda **kwargs: {'text': 'done', 'tool_calls': []}
        runtime = ModelRuntime(service, self.reader.actor, provider, config=config,
                               allowed_tools=('delivery_round',))
        self.assertIs(ModelRuntime.run, CoreRuntime.run)
        self.assertIsInstance(runtime._gateway, AgentGateway)
        self.assertEqual(frozenset({'delivery_round'}), runtime._gateway.allowed_tools)
        calls = {'text': '', 'tool_calls': [{'name': 'delivery_round', 'arguments': {'version': 1}}]}
        self.assertEqual(calls, runtime._model_response(calls))
        self.assertIs(RequestSession.review, CoreSession.review)
        self.assertIs(CommonHost.apply_approved, CoreHost.apply_approved)
        self.assertIs(AgentGateway.call, CoreGateway.call)
        self.assertNotIn('delivery_round', CoreGateway.ALLOWED)
        self.assertNotIn('delivery_round', ROLE_TOOLS['product'])
        with self.assertRaises(ValueError):
            CoreRegistration('b_cannot_change_core', 'Read.', ('delivery_round',))

    def test_runtime_rejects_malformed_scopes_and_open_transaction(self):
        service = ServiceDouble(self.database, principals={self.reader.actor: self.reader.principal()})
        config = ModelConfig(model='scripted-b-boundary')
        provider = lambda **kwargs: {'text': 'done', 'tool_calls': []}
        for allowed in ('delivery_round', [], ['delivery_round', 'delivery_round'],
                        ['apply'], ['review'], [None], [[]], ['']):
            with self.subTest(allowed=allowed), self.assertRaises(ValueError):
                ModelRuntime(service, self.reader.actor, provider, config=config, allowed_tools=allowed)
        service.db.info.transaction_status = TransactionStatus.INTRANS
        with self.assertRaises(ValueError):
            ModelRuntime(service, self.reader.actor, provider, config=config, allowed_tools=['delivery_round'])


if __name__ == '__main__':
    unittest.main()
