"""Check relocated B imports and source paths without a DB or model API."""
import ast
import hashlib
import importlib
import json
import unittest

from ontologics.model import ROOT


class RelocatedPathTests(unittest.TestCase):
    def test_feature_and_example_modules_import_from_subpackages(self):
        for folder in ('ontologics', 'examples'):
            for path in sorted((ROOT / folder / 'order_fulfillment').glob('*.py')):
                if path.name == '__init__.py':
                    continue
                module = '.'.join(path.relative_to(ROOT).with_suffix('').parts)
                with self.subTest(module=module):
                    self.assertIsNotNone(importlib.import_module(module))

    def test_contract_loader_reads_relocated_source_and_golden_answer(self):
        from ontologics.order_fulfillment.contracts import ROOT as contract_root, load_demo_contract
        self.assertEqual(ROOT, contract_root)
        nodes = load_demo_contract()
        source = next(n for n in nodes if n['type'] == 'SourceDocument')
        expected = json.loads((ROOT / 'data/order_fulfillment/b_contract_expected.json').read_text())
        self.assertEqual(expected['source_id'], source['id'])
        self.assertEqual(expected['source_sha256'], source['sha256'])
        self.assertEqual('data/order_fulfillment/sources/b_demo_contract_01.md', source['path'])
        item = next(n for n in nodes if n['type'] == 'ContractItem')
        self.assertEqual(expected['contract_quantity'], item['quantity'])
        self.assertEqual(expected['configuration_id'], item['configuration_id'])

    def test_fixture_and_graph_source_paths_resolve_with_unchanged_hashes(self):
        sources = set()

        def check(path, expected_hash):
            absolute = (ROOT / path).resolve()
            self.assertTrue(absolute.is_relative_to(ROOT / 'data'), path)
            self.assertTrue(absolute.is_file(), path)
            self.assertEqual(expected_hash, hashlib.sha256(absolute.read_bytes()).hexdigest(), path)
            if absolute.is_relative_to(ROOT / 'data/order_fulfillment/sources'):
                sources.add(path)

        def visit(value):
            if isinstance(value, dict):
                if isinstance(value.get('path'), str) and 'sha256' in value:
                    check(value['path'], value['sha256'])
                if 'source_path' in value and 'source_sha256' in value:
                    check(value['source_path'], value['source_sha256'])
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        for path in sorted((ROOT / 'data/order_fulfillment').glob('*.json*')):
            with self.subTest(file=path.name):
                visit(json.loads(path.read_text()))
        self.assertEqual(6, len(sources))

    def test_b_python_files_do_not_use_old_data_literals(self):
        for folder in ('ontologics', 'examples', 'tests'):
            for path in (ROOT / folder / 'order_fulfillment').glob('*.py'):
                for node in ast.walk(ast.parse(path.read_text())):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str):
                        value = node.value
                        old_data = value.startswith('data/' + 'b_') and value.endswith(('.json', '.jsonld'))
                        old_source = value.startswith('data/sources/' + 'b_demo_') and value.endswith('.md')
                        with self.subTest(file=path.name, line=node.lineno):
                            self.assertFalse(old_data or old_source, value)

    def test_test_subpackage_is_discoverable(self):
        package = ROOT / 'tests/order_fulfillment'
        self.assertTrue((package / '__init__.py').is_file())
        loader = unittest.TestLoader()
        discovered = loader.discover(str(package), top_level_dir=str(ROOT))
        self.assertFalse(loader.errors, loader.errors)
        self.assertGreater(discovered.countTestCases(), 0)

        def ids(suite):
            for case in suite:
                if isinstance(case, unittest.TestSuite):
                    yield from ids(case)
                else:
                    yield case.id()

        names = list(ids(discovered))
        self.assertTrue(any(name.startswith('tests.order_fulfillment.test_contracts.') for name in names))
        self.assertTrue(any(name.startswith(__name__ + '.') for name in names))


if __name__ == '__main__':
    unittest.main()
