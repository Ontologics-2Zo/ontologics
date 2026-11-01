"""Shared artifact regressions; runtime admission/approval remains unchanged."""
import copy
import json
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ontologics import model, tbox
from ontologics.production_planning import schema as a
from ontologics.order_fulfillment import schema as b


class SharedTboxTests(unittest.TestCase):
    def test_combined_declarations_preserve_common_and_each_role_without_registration(self):
        before = copy.deepcopy((model.SCHEMA, model.ALLOWED_FIELDS, model.SCHEMA_VERSION,
                                a.SCHEMA, b.SCHEMA))
        graph = model.ROOT / 'data/initial_graph.jsonld'
        graph_before = graph.read_bytes()
        result = tbox.build_tbox()
        self.assertTrue(result.startswith(model.tbox_turtle()))
        kinds = {line.split()[0][2:] for line in result.splitlines() if ' a owl:Class ' in line}
        self.assertEqual(kinds, set(model.SCHEMA) | set(a.SCHEMA) | set(b.SCHEMA))
        for kind in kinds:
            self.assertEqual(result.count(f'o:{kind} a owl:Class .'), 1)
        for prop, target in (('round_ids', 'DeliveryRound'), ('stock_id', 'FinishedGoodsStock'),
                             ('production_work_id', 'ProductionWork'), ('lot_id', 'ComponentLot')):
            self.assertEqual(result.count(f'o:{prop} a owl:ObjectProperty ; rdfs:range o:{target} .'), 1)
        self.assertEqual(before, (model.SCHEMA, model.ALLOWED_FIELDS, model.SCHEMA_VERSION, a.SCHEMA, b.SCHEMA))
        self.assertEqual(graph_before, graph.read_bytes())
        self.assertEqual(result, (model.ROOT / 'ontology/tbox.ttl').read_text())
        self.assertIn('UNKNOWN_TYPE', [e['code'] for e in model.validate_graph([
            dict(id='new', type='Contract', origin='synthetic', evidence=[])])['errors']])

    def test_same_definitions_deduplicate_but_conflicting_metadata_fails(self):
        a_ext, b_ext = tbox.registered_extensions()
        self.assertEqual(tbox.build_tbox((a_ext, b_ext)), tbox.build_tbox((a_ext, b_ext, a_ext)))
        mutations = [
            replace(b_ext, schema={**b.SCHEMA, 'Contract': (['company'], {})}),
            replace(b_ext, optional_fields={**b.OPTIONAL_FIELDS, 'Contract': {'note'}}),
            replace(b_ext, schema={**b.SCHEMA, 'ContractItem': (
                b.SCHEMA['ContractItem'][0], {'contract_id': 'Configuration', 'configuration_id': 'Configuration'})}),
            replace(b_ext, list_references={**b.LIST_REFERENCES, 'ContractItem': {'contract_id': 'Contract'}}),
            replace(b_ext, optional_references={**b.OPTIONAL_REFERENCES, 'ContractItem': {'contract_id'}}),
        ]
        for changed in mutations:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                tbox.build_tbox((a_ext, changed))

    def test_bad_ranges_names_missing_targets_and_scalar_list_conflicts_are_rejected(self):
        cases = [
            tbox.Extension('bad', {'New': (['lot_id'], {'lot_id': 'Configuration'})}),
            tbox.Extension('bad', {'New': (['ref'], {'ref': 'Missing'})}),
            tbox.Extension('bad', {'Bad Name': ([], {})}),
            tbox.Extension('bad', {'New': (['evidence'], {'evidence': 'Configuration'})}),
            tbox.Extension('bad', {'New': ([], {'ref': 'Configuration'})}),
            tbox.Extension('bad', {'New': (['ProductModel'], {'ProductModel': 'Configuration'})}),
            tbox.Extension('bad', {'evidence': ([], {})}),
            tbox.Extension('bad', {'New': (['lot_id'], {})}, list_references={'New': {'lot_id': 'ComponentLot'}}),
            tbox.Extension('bad', {'New': (['quantity'], {'quantity': 'Configuration'})}),
            tbox.Extension('bad', {'New': (['origin'], {'origin': 'Configuration'})}),
            tbox.Extension('bad', {'New': (['id'], {'id': 'Configuration'})}),
            tbox.Extension('bad', {'New': (['lot_id'], {})}),
        ]
        for extension in cases:
            with self.subTest(extension=extension), self.assertRaises(ValueError):
                tbox.build_tbox((extension,))

    def test_all_file_generators_use_the_same_artifact_and_check_does_not_write(self):
        from ontologics import demo
        self.assertEqual(demo.tbox_turtle(), tbox.build_tbox())
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'shared.ttl'
            for module in ('ontologics.tbox', 'ontologics.production_planning.export_tbox'):
                command = [sys.executable, '-m', module, '--output', str(target)]
                target.write_text('old reviewed content')
                check = subprocess.run(command + ['--check'], capture_output=True, text=True)
                self.assertNotEqual(check.returncode, 0)
                self.assertEqual(target.read_text(), 'old reviewed content')
                write = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(write.returncode, 0, write.stderr)
                self.assertEqual(target.read_text(), tbox.build_tbox())
                check = subprocess.run(command + ['--check'], capture_output=True, text=True)
                self.assertEqual(check.returncode, 0, check.stderr)

    def test_demo_regeneration_preserves_reviewed_id_context_and_does_not_hide_conflicts(self):
        from ontologics.demo import export_artifacts
        graph = model.graph_jsonld([])
        before = copy.deepcopy(graph)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data').mkdir()
            (root / 'ontology').mkdir()
            target = root / 'data/initial_graph.jsonld'
            existing = dict(graph, **{'@context': {**graph['@context'],
                            'contract_id': {'@type': '@id'}, 'contract_item_id': {'@type': '@id'}}})
            target.write_text(json.dumps(existing))
            export_artifacts(graph, root)
            self.assertEqual(json.loads(target.read_text()), existing)
            self.assertEqual((root / 'ontology/tbox.ttl').read_text(), tbox.build_tbox())
            self.assertEqual(graph, before)
            invalid = dict(existing, **{'@context': {**existing['@context'], 'id': 'different'}})
            target.write_text(json.dumps(invalid))
            (root / 'ontology/tbox.ttl').write_text('previous reviewed artifact')
            with self.assertRaisesRegex(ValueError, 'context'):
                export_artifacts(graph, root)
            self.assertEqual(json.loads(target.read_text()), invalid)
            self.assertEqual((root / 'ontology/tbox.ttl').read_text(), 'previous reviewed artifact')


if __name__ == '__main__':
    unittest.main()
