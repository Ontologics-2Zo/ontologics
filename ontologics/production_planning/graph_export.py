"""JSON-LD export of the explicit common + A definitions, without mutation."""
from .. import model
from . import schema

def graph_jsonld(nodes):
    definitions = {**model.SCHEMA, **schema.SCHEMA}
    context = {'@vocab': 'urn:ontologics:', 'id': '@id', 'type': '@type', 'evidence': {'@type': '@id'}}
    for _, refs in definitions.values():
        for key in refs: context[key] = {'@type': '@id'}
    # IDs use stable URNs rather than accidental JSON-LD prefixes.
    graph = []
    for n in nodes:
        row = dict(n, id='urn:ontologics:entity:' + n['id'])
        for key in definitions[n['type']][1]: row[key] = 'urn:ontologics:entity:' + n[key]
        row['evidence'] = ['urn:ontologics:entity:' + e for e in n.get('evidence', [])]
        graph.append(row)
    return {'@context': context, '@graph': graph}
