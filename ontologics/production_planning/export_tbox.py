"""Explicit common + A T-box export; no database, API or runtime registration.

Run from the repository: python3 -m ontologics.production_planning.export_tbox
The output is rebuilt from definitions, never patched from an existing TTL.
As in the common exporter, scalar fields and Python rules are not OWL axioms.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .. import model
from . import schema


def extension_lines(common, extension):
    """Render new declarations, rejecting overlapping classes or range conflicts."""
    overlap = set(common) & set(extension)
    if overlap:
        raise ValueError('Duplicate ontology types: ' + ', '.join(sorted(overlap)))
    combined = {**common, **extension}
    ranges = {'evidence': 'Evidence'}  # Implicit common relationship.
    common_properties = {'evidence'}
    for kind, (required, relations) in combined.items():
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', kind):
            raise ValueError('Unsupported Turtle type name: ' + kind)
        for prop, target in relations.items():
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', prop):
                raise ValueError('Unsupported Turtle property name: ' + prop)
            if prop not in required:
                raise ValueError('Relationship missing from required fields: ' + prop)
            if target not in combined:
                raise ValueError('Unknown relationship target: ' + target)
            if prop in ranges and ranges[prop] != target:
                raise ValueError('Conflicting relationship ranges: ' + prop)
            ranges[prop] = target
            if kind in common:
                common_properties.add(prop)
    lines = [f'o:{kind} a owl:Class .' for kind in extension]
    for prop, target in ranges.items():
        if prop not in common_properties:
            lines.append(f'o:{prop} a owl:ObjectProperty ; rdfs:range o:{target} .')
    return lines


def build_tbox():
    """Preserve the current common export and add each A declaration once."""
    additions = extension_lines(model.SCHEMA, schema.SCHEMA)
    # Keep both contributing versions; do not replace the common version marker.
    version = json.dumps(schema.SCHEMA_VERSION, ensure_ascii=False)
    return model.tbox_turtle() + '\n# Production planning extension\n' + '\n'.join([
        f'o:Ontology owl:versionInfo {version} .', *additions,
    ]) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=model.ROOT / 'ontology' / 'tbox.ttl',
                        help='Output file (default: repository ontology/tbox.ttl)')
    args = parser.parse_args(argv)
    # Resolve schema conflicts before opening the output file.
    content = build_tbox()
    args.output.write_text(content, encoding='utf-8')
    print(f'T-box written: {args.output}')


if __name__ == '__main__':
    main()
