"""Compatibility B output helpers over the shared registered schema.

Inputs must already be validated. These helpers neither validate/approve the
graph nor write files, and use the current common ontology/version.
The scoped common+B tbox_turtle() return value is not the shared file. Generate
ontology/tbox.ttl with python -m ontologics.tbox to preserve all reviewed roles.
"""
from ..model import graph_jsonld as _common_graph_jsonld, tbox_turtle as _common_tbox_turtle
from .schema import LIST_REFERENCES, SCHEMA


def jsonld_context():
    """Return only B relationship entries for the combined context."""
    context = {}
    for _, refs in SCHEMA.values():
        for key in refs:
            context[key] = {'@type': '@id'}
    for refs in LIST_REFERENCES.values():
        for key in refs:
            context[key] = {'@type': '@id'}
    return context


def jsonld_node(node):
    """Encode one B node through the shared relation/JSON mapping."""
    if node.get('type') not in SCHEMA:
        raise ValueError('B export requires a B graph type')
    return _common_graph_jsonld([node])['@graph'][0]


def graph_jsonld(nodes):
    """Encode the complete registered graph through the shared exporter."""
    return _common_graph_jsonld(list(nodes))


def tbox_terms():
    """Return B class/property declarations, without a common ontology/version.

    This fragment needs the common o/owl/rdfs prefixes. It does not choose an
    ontology version or register these types with validation/storage services.
    """
    lines = [f'o:{kind} a owl:Class .' for kind in SCHEMA]
    refs = {}
    for _, relations in SCHEMA.values():
        for prop, target in relations.items():
            refs.setdefault(prop, set()).add(target)
    for relations in LIST_REFERENCES.values():
        for prop, target in relations.items():
            refs.setdefault(prop, set()).add(target)
    for prop, targets in refs.items():
        if len(targets) == 1:
            lines.append(f'o:{prop} a owl:ObjectProperty ; rdfs:range o:{next(iter(targets))} .')
    return '\n'.join(lines) + '\n'


def tbox_turtle():
    """Keep common Turtle verbatim, appending only absent B declarations.

    This composes the common exporter's one-line declarations, not arbitrary
    Turtle documents. Conflicting class/property declarations fail closed.
    """
    common = _common_tbox_turtle()
    declarations = {}
    for line in common.splitlines():
        if line.startswith('o:') and ' a owl:' in line:
            declarations.setdefault(line.split(' ', 1)[0], set()).add(line)
    missing = []
    for term in tbox_terms().splitlines():
        subject = term.split(' ', 1)[0]
        existing = declarations.get(subject, set())
        if existing and existing != {term}:
            raise ValueError('Conflicting common and B T-box declaration: ' + subject)
        if not existing:
            missing.append(term)
    if not missing:
        return common
    separator = '' if common.endswith('\n') else '\n'
    return common + separator + '\n'.join(missing) + '\n'
