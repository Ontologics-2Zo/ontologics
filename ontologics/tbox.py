"""Shared T-box artifact generation; never registers runtime graph types.

The registry below is explicit. Adding a package does not grant it storage,
validation, approval, or RDF/Neo4j query support.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import re

from . import model


@dataclass(frozen=True)
class Extension:
    name: str
    schema: dict
    optional_fields: dict = field(default_factory=dict)
    optional_references: dict = field(default_factory=dict)
    list_references: dict = field(default_factory=dict)
    version: str | None = None


def registered_extensions():
    """Reviewed declaration sources, not a plugin or service schema registry."""
    from .production_planning import schema as a
    from .order_fulfillment import schema as b
    return (
        Extension('A production planning', a.SCHEMA, a.OPTIONAL_FIELDS,
                  version=a.SCHEMA_VERSION),
        # B's EXTRACTED_GRAPH_VERSION records extraction provenance only.
        Extension('B order fulfillment', b.SCHEMA, b.OPTIONAL_FIELDS,
                  b.OPTIONAL_REFERENCES, b.LIST_REFERENCES),
    )


def _name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', value):
        raise ValueError('Unsupported Turtle name: ' + str(value))


def _definitions(extension):
    result = {}
    for metadata in (extension.optional_fields, extension.optional_references,
                     extension.list_references):
        if set(metadata) - set(extension.schema):
            raise ValueError('Relationship/field metadata has an unknown class')
    for kind, (required, refs) in extension.schema.items():
        _name(kind)
        optional = frozenset(extension.optional_fields.get(kind, ()))
        optional_refs = frozenset(extension.optional_references.get(kind, ()))
        lists = extension.list_references.get(kind, {})
        required_set = frozenset(required)
        if len(required) != len(required_set) or required_set & optional:
            raise ValueError('Conflicting required/optional fields: ' + kind)
        if set(refs) & set(lists):
            raise ValueError('Scalar/list relationship conflict: ' + kind)
        if not optional_refs <= set(refs) or not optional_refs <= optional:
            raise ValueError('Invalid optional relationship: ' + kind)
        if set(refs) - optional_refs - required_set or set(lists) - required_set - optional:
            raise ValueError('Relationship missing from required/optional fields: ' + kind)
        if (set(refs) & optional) != optional_refs:
            raise ValueError('Optional relationship metadata missing: ' + kind)
        for name in required_set | optional | set(refs) | set(lists):
            _name(name)
        for target in [*refs.values(), *lists.values()]:
            _name(target)
        result[kind] = (required_set, dict(refs), optional, optional_refs, dict(lists))
    return result


def _combined(common, extensions):
    definitions = _definitions(common)
    for extension in extensions:
        for kind, definition in _definitions(extension).items():
            if kind in definitions and definitions[kind] != definition:
                raise ValueError('Conflicting schema definition: ' + kind)
            definitions[kind] = definition
    ranges = {'evidence': 'Evidence'}
    cardinalities = {'evidence': 'list'}
    literal_fields = {'id', 'type', 'origin'}
    for required, refs, optional, _, lists in definitions.values():
        literal_fields.update((required | optional) - set(refs) - set(lists))
        for cardinality, relations in (('scalar', refs), ('list', lists)):
            for prop, target in relations.items():
                if target not in definitions:
                    raise ValueError('Unknown relationship target: ' + target)
                if prop in definitions or prop == 'Ontology':
                    raise ValueError('Class/property name collision: ' + prop)
                if prop in ranges and ranges[prop] != target:
                    raise ValueError('Conflicting relationship ranges: ' + prop)
                if prop in cardinalities and cardinalities[prop] != cardinality:
                    raise ValueError('Scalar/list relationship conflict: ' + prop)
                ranges[prop], cardinalities[prop] = target, cardinality
    if literal_fields & set(ranges):
        raise ValueError('Literal/relationship field conflict: ' + ', '.join(sorted(literal_fields & set(ranges))))
    if 'evidence' in definitions or 'Ontology' in definitions:
        raise ValueError('Reserved ontology name used as a class')
    return definitions, ranges


def extension_lines(common_schema, extension_schema):
    """Compatibility helper for A's required scalar-reference declarations."""
    if set(common_schema) & set(extension_schema):
        raise ValueError('Duplicate ontology types')
    common = (Extension('common', common_schema, model.OPTIONAL_FIELDS,
                        model.OPTIONAL_REFERENCES, model.LIST_REFERENCES)
              if common_schema is model.SCHEMA else Extension('common', common_schema))
    _, ranges = _combined(common, (Extension('extension', extension_schema),))
    _, common_ranges = _combined(common, ())
    return ([f'o:{kind} a owl:Class .' for kind in extension_schema]
            + [f'o:{prop} a owl:ObjectProperty ; rdfs:range o:{target} .'
               for prop, target in ranges.items() if prop not in common_ranges])


def build_tbox(extensions=None):
    """Keep common text/version and compose reviewed extension declarations once.

    Matching structural metadata permits shared A/B names; it is not proof that
    their business validators or persistence paths are interchangeable.
    """
    extensions = tuple(registered_extensions() if extensions is None else extensions)
    common = Extension('common', model.SCHEMA, model.OPTIONAL_FIELDS,
                       model.OPTIONAL_REFERENCES, model.LIST_REFERENCES)
    definitions, ranges = _combined(common, extensions)
    text = model.tbox_turtle()
    existing = {}
    for line in text.splitlines():
        if line.startswith('o:') and ' a owl:' in line:
            existing.setdefault(line.split(' ', 1)[0], set()).add(line)
    terms = [f'o:{kind} a owl:Class .' for kind in definitions]
    terms += [f'o:{prop} a owl:ObjectProperty ; rdfs:range o:{target} .'
              for prop, target in ranges.items()]
    missing = []
    for term in terms:
        prior = existing.get(term.split(' ', 1)[0], set())
        if prior and prior != {term}:
            raise ValueError('Conflicting common T-box declaration: ' + term)
        if not prior:
            missing.append(term)
    versions = {model.SCHEMA_VERSION}
    for extension in extensions:
        if extension.version and extension.version not in versions:
            missing.append(f'o:Ontology owl:versionInfo {json.dumps(extension.version)} .')
            versions.add(extension.version)
    if not missing:
        return text
    return text + ('' if text.endswith('\n') else '\n') + '\n# Shared A/B declarations (not runtime registration)\n' + '\n'.join(missing) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=model.ROOT / 'ontology/tbox.ttl')
    parser.add_argument('--check', action='store_true', help='Compare without writing')
    args = parser.parse_args(argv)
    content = build_tbox()  # Resolve every definition conflict before any write.
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding='utf-8') != content:
            parser.exit(1, 'T-box differs; run python -m ontologics.tbox to regenerate.\n')
        print('T-box matches common + A + B definitions.')
    else:
        args.output.write_text(content, encoding='utf-8')
        print(f'T-box written: {args.output}')


if __name__ == '__main__':
    main()
