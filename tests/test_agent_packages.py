"""Role ownership, host trust and saved-read compatibility; no model/DB calls."""
import ast
from importlib.util import resolve_name
from pathlib import Path
import unittest
from unittest.mock import patch

from ontologics.agents import registry
from ontologics.agents.product.agent import ProductAgent
from ontologics.agents.quality.agent import QualityAgent
from ontologics.agents.knowledge.agent import KnowledgeAgent
from ontologics.agents.supervisor.agent import SupervisorAgent
from ontologics.host import RequestSession, _definition_fingerprint
from ontologics.identity import UserIdentity


class AgentPackageTests(unittest.TestCase):
    def setUp(self):
        self.request = RequestSession(None, UserIdentity('reader', frozenset({'read'})), 'development')

    @staticmethod
    def task(role, flow):
        return registry.builtin_task(role, flow, 'Explain the selected evidence.', 3, [],
            [{'role': 'system', 'content': 'Keep the selected version.'},
             {'role': 'user', 'content': 'Explain the selected evidence.'}])

    def test_fixed_registry_and_legacy_exports_use_actual_role_implementations(self):
        from ontologics import assistant, builtin_agents, web
        from ontologics.agents import service
        from ontologics.agents.knowledge import validators as knowledge
        from ontologics.agents.quality import validators as quality
        from ontologics.agents.product.workflow import prepare_receipt_draft

        cases = [('product', 'draft', ProductAgent, '1.0.0'),
                 ('knowledge', 'semantic', KnowledgeAgent, '1.0.0'),
                 ('quality', 'investigation', QualityAgent, '1.0.1'),
                 ('supervisor', 'route', SupervisorAgent, '1.0.0')]
        for role, flow, expected, version in cases:
            with self.subTest(role=role):
                implementation, task = self.task(role, flow)
                self.assertIs(type(implementation), expected)
                self.assertIs(type(builtin_agents.BuiltinRole(role, flow, [])), expected)
                self.assertIs(self.request._task_spec(role, implementation, task), implementation.spec)
                self.assertEqual(version, implementation.spec.version)
                self.assertTrue(implementation.decode.__module__.startswith('ontologics.agents.' + role + '.'))
                self.assertEqual(registry.ROLES[role].CONTRACTS[flow], builtin_agents.CONTRACTS[(role, flow)])
        self.assertIs(assistant.assistant, service.assistant)
        self.assertIs(assistant.prepare_receipt_draft, prepare_receipt_draft)
        self.assertIs(assistant.validated_quality_proposal, quality.validated_quality_proposal)
        self.assertIs(assistant.verified_knowledge_text, knowledge.verified_knowledge_text)
        self.assertIs(web.verified_quality_text, quality.verified_quality_text)
        self.assertIs(web.quality_observations, quality.quality_observations)

    def test_subclass_or_wrong_role_cannot_claim_builtin_host_authority(self):
        class UnregisteredQuality(QualityAgent):
            pass

        real, task = self.task('quality', 'investigation')
        extension = UnregisteredQuality('investigation', [])
        self.assertFalse(registry.is_builtin(extension))
        with self.assertRaisesRegex(ValueError, 'registered versioned AgentSpec'):
            self.request._task_spec('quality', extension, task)
        with self.assertRaisesRegex(ValueError, 'Builtin role does not match'):
            self.request._task_spec('product', real, task)
        with self.assertRaises(TypeError):
            registry.ROLES['quality'] = UnregisteredQuality

    def test_role_modules_do_not_depend_on_http_or_compatibility_facades(self):
        # Keep transport imports pointing into Agent implementations, never back
        # out to HTTP modules. Inspect all role helpers, not a fixed file count.
        root = Path(registry.__file__).parent
        banned = ('ontologics.web', 'ontologics.assistant', 'ontologics.builtin_agents', 'examples')
        for role in registry.ROLES:
            for path in (root / role).rglob('*.py'):
                package = 'ontologics.agents.' + '.'.join(path.parent.relative_to(root).parts)
                for node in ast.walk(ast.parse(path.read_text())):
                    if isinstance(node, ast.Import):
                        names = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        name = '.' * node.level + (node.module or '')
                        base = resolve_name(name, package) if node.level else name
                        names = [base, *(base + '.' + alias.name for alias in node.names)]
                    else:
                        continue
                    for name in names:
                        with self.subTest(module=str(path.relative_to(root)), dependency=name):
                            self.assertFalse(any(name == target or name.startswith(target + '.') for target in banned))

    def test_read_checkpoint_fingerprint_tracks_local_helpers_without_other_role_churn(self):
        class Provider:
            def public_config(self):
                return {'model': 'scripted-package-contract', 'mode': 'test'}

        quality, quality_task = self.task('quality', 'investigation')
        product, product_task = self.task('product', 'read')
        provider = Provider()

        def fingerprint(implementation, task):
            messages = self.request._task_messages(implementation, task, implementation.messages(task))
            return _definition_fingerprint(implementation.spec, messages, provider, implementation)

        original = {role: fingerprint(implementation, task) for role, implementation, task in (
            ('quality', quality, quality_task), ('product', product, product_task))}
        read_bytes = Path.read_bytes
        root = Path(registry.__file__).parent
        helper = root / 'quality' / 'validators.py'

        def changed_helper(path):
            return read_bytes(path) + (b'\n# changed role validation behavior\n' if path == helper else b'')

        with patch.object(Path, 'read_bytes', autospec=True, side_effect=changed_helper):
            self.assertNotEqual(original['quality'], fingerprint(quality, quality_task))
            self.assertEqual(original['product'], fingerprint(product, product_task))
        # Shared execution changes must invalidate every affected role, while
        # merely reading fingerprints must never modify repository files.
        shared = root / 'execution.py'
        def changed_shared(path):
            return read_bytes(path) + (b'\n# changed shared execution behavior\n' if path == shared else b'')
        with patch.object(Path, 'read_bytes', autospec=True, side_effect=changed_shared):
            self.assertNotEqual(original['quality'], fingerprint(quality, quality_task))
            self.assertNotEqual(original['product'], fingerprint(product, product_task))
        self.assertEqual(original['quality'], fingerprint(quality, quality_task))
        self.assertEqual(original['product'], fingerprint(product, product_task))


if __name__ == '__main__':
    unittest.main()
