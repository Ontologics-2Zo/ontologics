"""Real new-schema common typed path and explicit B contract boundary; no LLM."""
from dataclasses import replace
import unittest
from unittest.mock import Mock
from examples.agents import model_reader
from examples.order_fulfillment.b_host_agent import REGISTRATIONS
from ontologics.contracts import AgentTask
from ontologics.harness import PinnedContext
from ontologics.identity import DevelopmentContext, DevelopmentIdentityProvider, UserIdentity
from ontologics.order_fulfillment.host import AgentRegistration, CommonHost
from ontologics.runtime import ModelConfig
from ontologics.service import Service
from ontologics.ingest import build_catalog
from tests.db import database_for_test


class MainTaskConnectionTests(unittest.TestCase):
    def test_domain_roles_need_review_but_intake_uses_common_tools(self):
        for registration in REGISTRATIONS:
            with self.subTest(role=registration.name):
                def specification():
                    return replace(model_reader.SPEC, name=registration.name,
                                   instructions=registration.instructions,
                                   requested_tools=registration.allowed_tools)
                if registration.name == 'b_intake':
                    # Tool admissibility alone does not implement a typed B adapter.
                    spec = specification()
                    self.assertEqual(registration.allowed_tools, spec.requested_tools)
                    self.assertEqual(spec, AgentRegistration.from_spec(spec).spec)
                else:
                    with self.assertRaises(ValueError):
                        specification()

    def test_common_only_typed_role_works_through_b_host_new_schema(self):
        database = database_for_test(self)
        fixture = Service(database, principals={'seed': {'kind': 'human', 'roles': ['read', 'seed']}})
        try:
            seeded = fixture.bootstrap('seed', build_catalog()['nodes'], request_key='main-only-catalog')
            self.assertTrue(seeded['ok'], seeded)
            self.assertEqual(1, seeded['data_version'])
        finally:
            fixture.close()
        reader = UserIdentity('main-typed-b-reader', frozenset({'read'}))
        denied = UserIdentity('main-typed-b-denied', frozenset())
        identities = DevelopmentIdentityProvider([reader, denied], enabled=True)
        host = CommonHost(identities, database,
                          agent_registrations=(AgentRegistration.from_spec(model_reader.SPEC),))
        task = AgentTask({'question':'같은 확정 버전의 제품을 근거와 함께 조회하세요.'},
                         PinnedContext('고정 개발 조회', 1))
        with host.request(DevelopmentContext(reader.subject)) as request:
            result = request.run_task(model_reader.SPEC.name, model_reader.ModelReader(), task,
                model_reader.ScriptedModelReader(task), config=ModelConfig('scripted',max_attempts=1),
                capture_tool_trace=True)
        self.assertTrue(result['runtime']['ok'], result)
        self.assertEqual('agent-result-1', result['schema_version'])
        self.assertEqual(1,result['payload']['data_version'])
        self.assertTrue(result['payload']['product_ids'])
        self.assertTrue(result['evidence_refs'])
        self.assertIsNone(result['error'])
        provider = Mock()
        with host.request(DevelopmentContext(denied.subject)) as request:
            with self.assertRaises(Exception) as caught:
                request.run_task(model_reader.SPEC.name, model_reader.ModelReader(), task,
                                 provider, config=ModelConfig('scripted', max_attempts=1))
        self.assertEqual('FORBIDDEN', caught.exception.code)
        provider.assert_not_called()
