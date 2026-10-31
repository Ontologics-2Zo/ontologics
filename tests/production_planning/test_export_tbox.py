import copy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from ontologics import model
from ontologics.production_planning import schema
from ontologics.production_planning.export_tbox import build_tbox, extension_lines, main


class ExportTboxTests(unittest.TestCase):
    def test_common_output_preserved_and_all_classes_and_relations_present_once(self):
        before = copy.deepcopy(model.SCHEMA)
        output = build_tbox()
        self.assertTrue(output.startswith(model.tbox_turtle()))
        combined = {**model.SCHEMA, **schema.SCHEMA}
        for kind in combined:
            self.assertEqual(output.count(f'o:{kind} a owl:Class .'), 1)
        for _, refs in combined.values():
            for prop, target in refs.items():
                self.assertEqual(output.count(
                    f'o:{prop} a owl:ObjectProperty ; rdfs:range o:{target} .'), 1)
        self.assertEqual(model.SCHEMA, before)
        self.assertEqual(build_tbox(), output)

    def test_collisions_and_missing_targets_fail(self):
        for extension in (
            {'Configuration': ([], {})},
            {'New': (['lot_id'], {'lot_id': 'Configuration'})},
            {'New': (['missing_id'], {'missing_id': 'Missing'})},
            {'Bad Name': ([], {})},
            {'New': (['evidence'], {'evidence': 'Configuration'})},
            {'New': ([], {'new_id': 'Configuration'})},
            {'New': (['bad name'], {'bad name': 'Configuration'})},
        ):
            with self.subTest(extension=extension), self.assertRaises(ValueError):
                extension_lines(model.SCHEMA, extension)

    def test_regeneration_reflects_added_and_removed_definitions(self):
        first = {'New': (['lot_id'], {'lot_id': 'ComponentLot'})}
        self.assertEqual(extension_lines(model.SCHEMA, first), ['o:New a owl:Class .'])
        self.assertEqual(extension_lines(model.SCHEMA, {}), [])

    def test_cli_overwrites_stale_output_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'tbox.ttl'
            target.write_text('stale content')
            command = [sys.executable, '-m', 'ontologics.production_planning.export_tbox',
                       '--output', str(target)]
            for _ in range(2):
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(target.read_text(), build_tbox())

    def test_conflict_does_not_overwrite_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'tbox.ttl'
            target.write_text('existing reviewed output')
            with patch('ontologics.production_planning.export_tbox.extension_lines',
                       side_effect=ValueError('Conflicting relationship ranges')):
                with self.assertRaises(ValueError):
                    main(['--output', str(target)])
            self.assertEqual(target.read_text(), 'existing reviewed output')

    def test_export_has_no_graph_data_or_runtime_registration_side_effects(self):
        graph = model.ROOT / 'data' / 'initial_graph.jsonld'
        before = graph.read_bytes()
        allowed = copy.deepcopy(model.ALLOWED_FIELDS)
        build_tbox()
        self.assertEqual(graph.read_bytes(), before)
        self.assertEqual(model.ALLOWED_FIELDS, allowed)
        self.assertTrue(set(schema.SCHEMA).isdisjoint(model.SCHEMA))

    def test_checked_in_tbox_matches_current_definitions(self):
        self.assertEqual((model.ROOT / 'ontology' / 'tbox.ttl').read_text(), build_tbox())
