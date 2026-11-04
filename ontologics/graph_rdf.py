"""Validated RDF projection and fixed SPARQL reads of one confirmed snapshot.

PostgreSQL remains the approval/source-of-truth boundary. This module never
fetches RDF documents, accepts caller SPARQL, or computes temporal decisions.
JSON payloads preserve array order/nulls; RDF classes and relationships select
the returned entities and are checked against those payloads when decoded.
"""
from __future__ import annotations

import json
from functools import lru_cache
from urllib.parse import quote

from pyshacl import validate
from rdflib import BNode, Graph, Literal, Namespace, RDF, URIRef, XSD
from rdflib.collection import Collection
from rdflib.plugins.sparql import prepareQuery

from .domain_schema import C2_QUANTITIES
from .model import (ALLOWED_FIELDS, JSON_FIELDS, LIST_REFERENCES, REAL, SCHEMA, SCHEMA_VERSION, SYNTHETIC,
                    canonical, digest, graph_jsonld, tbox_turtle, validate_graph)

O = Namespace('urn:ontologics:')
P = Namespace('urn:ontologics:projection:')
SH = Namespace('http://www.w3.org/ns/shacl#')
TEMPORAL_TYPES = ('ProductUnit', 'ComponentLot', 'Installation', 'HistoryCoverage')

# RDF datatype/cardinality checks mirror the common validators. They do not
# replace cross-record business rules or infer quantities from missing facts.
INTEGER_MINIMUMS = {
    'ClaimIntake': {'claimed_quantity': 1},
    'ContainmentRequest': {'draft_version': 1},
    'TestRequest': {'draft_version': 1},
    'ReproductionRequirement': {field: 0 for field in C2_QUANTITIES},
    'ContractItem': {'quantity': 1}, 'DeliveryRound': {'quantity': 1},
    'FinishedGoodsStock': {'quantity': 0},
    'ProductionWork': {'target_quantity': 1, 'completed_quantity': 0},
    'ProductionObservation': {'produced_quantity': 0, 'good_quantity': 0, 'defective_quantity': 0},
    'Allocation': {'quantity': 1}, 'Shipment': {'quantity': 1},
    'DeliveryCondition': {'preparation_seconds': 0, 'transport_seconds': 0},
    'ComponentStockSnapshot': {field: 0 for field in (
        'on_hand_quantity', 'reserved_quantity', 'held_quantity', 'available_quantity')},
}
TEXT_LIST_FIELDS = {'BOMItem': {'positions'}, 'ClaimIntake': {'reported_serials'},
                    'ReproductionRequirement': {'replacement_unit_ids'}}


def _check_budget():
    # Reuse the request/child budget; never create a fresh allowance for a
    # projection step. This is a boundary check, not forced SHACL cancellation.
    from .harness import current_budget
    budget = current_budget()
    if budget is not None:
        budget.check()


def entity_uri(node_id):
    """Preserve the original ID as a literal; escape only its RDF IRI spelling."""
    return URIRef('urn:ontologics:entity:' + quote(node_id, safe=':/-._~'))


def _rdf_terms(node, field):
    value = node.get(field)
    if value is None:
        return set()
    if field in SCHEMA[node['type']][1]:
        return {entity_uri(value)}
    if field == 'evidence' or field in LIST_REFERENCES.get(node['type'], {}):
        return {entity_uri(item) for item in value}
    if field in JSON_FIELDS.get(node['type'], set()):
        return {Literal(canonical(value), datatype=RDF.JSON)}
    if isinstance(value, list):
        return {Literal(item) for item in value}
    return {Literal(value)}


def _graph(nodes, version, schema_version, rule_version):
    # Local, generated context only. The JSON-LD parser is never given a URL or
    # caller-controlled @context (which could otherwise perform network I/O).
    _check_budget()
    document = graph_jsonld(nodes)
    for fields in JSON_FIELDS.values():
        for field in fields:
            document['@context'][field] = {'@type': '@json'}
    for row, node in zip(document['@graph'], nodes):
        row['id'] = str(entity_uri(node['id']))
        for field in SCHEMA[node['type']][1]:
            if field in node:
                row[field] = str(entity_uri(node[field]))
        for field in LIST_REFERENCES.get(node['type'], {}):
            row[field] = [str(entity_uri(value)) for value in node[field]]
        row['evidence'] = [str(entity_uri(value)) for value in node['evidence']]
    serialized = canonical(document)
    _check_budget()
    result = Graph().parse(data=serialized, format='json-ld')
    _check_budget()
    result.bind('o', O)
    result.bind('projection', P)
    for ordinal, node in enumerate(nodes):
        subject = entity_uri(node['id'])
        # RDF JSON lexical formatting is explicitly canonicalized for a stable
        # comparison with the existing domain's canonical JSON implementation.
        for field in JSON_FIELDS.get(node['type'], set()):
            if field in node:
                result.set((subject, O[field], Literal(canonical(node[field]), datatype=RDF.JSON)))
        for field, value in (('entity_id', node['id']), ('payload', canonical(node)),
                             ('ordinal', ordinal), ('data_version', version),
                             ('schema_version', schema_version), ('rule_version', rule_version)):
            result.add((subject, P[field], Literal(value)))
    _check_budget()
    return result


def shacl_shapes():
    """Generate additional RDF structural constraints from the common SCHEMA.

    Domain identity, source byte integrity, and temporal semantics remain in
    validate_graph/temporal. SHACL does not approve or infer operational facts.
    """
    shapes = Graph()
    shapes.bind('sh', SH)
    shapes.bind('o', O)

    def values(items):
        head = BNode()
        Collection(shapes, head, items)
        return head

    def prop(shape, path, *, minimum=0, maximum=1, datatype=None, target=None):
        p = BNode()
        shapes.add((shape, SH.property, p))
        shapes.add((p, SH.path, path))
        if minimum:
            shapes.add((p, SH.minCount, Literal(minimum)))
        if maximum is not None:
            shapes.add((p, SH.maxCount, Literal(maximum)))
        if datatype:
            shapes.add((p, SH.datatype, datatype))
        if target:
            shapes.add((p, SH.nodeKind, SH.IRI))
            shapes.add((p, SH['class'], target))
        return p

    # Target projection entities even when their rdf:type was removed; a shape
    # targeted only at a known class would otherwise miss an unreferenced node.
    entity_shape = P.EntityShape
    shapes.add((entity_shape, RDF.type, SH.NodeShape))
    shapes.add((entity_shape, SH.targetSubjectsOf, P.entity_id))
    shapes.add((entity_shape, SH.nodeKind, SH.IRI))
    entity_type = prop(entity_shape, RDF.type, minimum=1)
    shapes.add((entity_type, SH['in'], values([O[kind] for kind in SCHEMA])))

    for kind, (required, refs) in SCHEMA.items():
        shape = P[kind + 'Shape']
        shapes.add((shape, RDF.type, SH.NodeShape))
        shapes.add((shape, SH.targetClass, O[kind]))
        shapes.add((shape, SH.nodeKind, SH.IRI))
        shapes.add((shape, SH.closed, Literal(True)))
        prop(shape, RDF.type, minimum=1, target=None)
        for field in ('entity_id', 'payload', 'schema_version', 'rule_version'):
            prop(shape, P[field], minimum=1, datatype=XSD.string)
        for field in ('ordinal', 'data_version'):
            p = prop(shape, P[field], minimum=1, datatype=XSD.integer)
            shapes.add((p, SH.minInclusive, Literal(0)))
        origin = prop(shape, O.origin, minimum=1, datatype=XSD.string)
        allowed_origins = ['real'] if kind in REAL else ['synthetic'] if kind in SYNTHETIC else ['real', 'synthetic']
        shapes.add((origin, SH['in'], values([Literal(x) for x in allowed_origins])))
        prop(shape, O.evidence, minimum=0 if kind in ('SourceDocument', 'Evidence') else 1,
             maximum=None, target=O.Evidence)
        for field in sorted(ALLOWED_FIELDS[kind] - {'id', 'type', 'origin', 'evidence'}):
            minimum = int(field in required and field != 'positions')
            if field in refs:
                prop(shape, O[field], minimum=minimum, target=O[refs[field]])
            elif field in LIST_REFERENCES.get(kind, {}):
                prop(shape, O[field], minimum=minimum, maximum=None,
                     target=O[LIST_REFERENCES[kind][field]])
            elif field in INTEGER_MINIMUMS.get(kind, {}):
                p = prop(shape, O[field], minimum=minimum, datatype=XSD.integer)
                shapes.add((p, SH.minInclusive, Literal(INTEGER_MINIMUMS[kind][field])))
            elif field == 'quantity':
                p = prop(shape, O[field], minimum=minimum)
                choices = []
                for datatype in (XSD.integer, XSD.double):
                    choice = BNode()
                    shapes.add((choice, SH.datatype, datatype))
                    choices.append(choice)
                shapes.add((p, SH['or'], values(choices)))
                shapes.add((p, SH.minExclusive, Literal(0)))
            elif field in ('complete', 'conflict', 'deductions_disjoint'):
                p = prop(shape, O[field], minimum=minimum, datatype=XSD.boolean)
                if field == 'complete':
                    shapes.add((p, SH.hasValue, Literal(True)))
            elif field in JSON_FIELDS.get(kind, set()):
                prop(shape, O[field], minimum=minimum, datatype=RDF.JSON)
            else:
                p = prop(shape, O[field], minimum=minimum,
                         maximum=None if field in TEXT_LIST_FIELDS.get(kind, ()) else 1,
                         datatype=XSD.string)
                shapes.add((p, SH.minLength, Literal(1)))
                if field == 'population':
                    shapes.add((p, SH['in'], values([Literal(x) for x in ('populated', 'not_populated', 'unknown')])))
                if field == 'unit':
                    shapes.add((p, SH.hasValue, Literal('piece')))
                if field == 'movement':
                    shapes.add((p, SH['in'], values([Literal('receipt'), Literal('issue')])))
                if field == 'sha256':
                    shapes.add((p, SH.pattern, Literal('^[a-f0-9]{64}$')))

    # These constraints run in the actual SHACL SPARQL engine. An evidence edge
    # cannot silently cross the boundary between source facts and demo records.
    origin_shape = P.OriginLinksShape
    shapes.add((origin_shape, RDF.type, SH.NodeShape))
    shapes.add((origin_shape, SH.targetSubjectsOf, O.origin))
    check = BNode()
    shapes.add((origin_shape, SH.sparql, check))
    shapes.add((check, SH.message, Literal('Evidence/source origin must match its referring entity')))
    shapes.add((check, SH.select, Literal('''
        SELECT $this WHERE {
          $this <urn:ontologics:origin> ?origin .
          { $this <urn:ontologics:evidence> ?linked }
          UNION { $this <urn:ontologics:source_id> ?linked }
          ?linked <urn:ontologics:origin> ?other .
          FILTER (?origin != ?other)
        }
    ''')))
    return shapes


def validate_rdf(graph):
    """Run SHACL against an in-memory graph without imports or inference."""
    if not isinstance(graph, Graph):
        raise TypeError('An in-memory RDF graph is required')
    _check_budget()
    shapes = shacl_shapes()
    ontology = Graph().parse(data=tbox_turtle(), format='turtle')
    _check_budget()
    conforms, report, _ = validate(
        graph, shacl_graph=shapes, ont_graph=ontology,
        inference='none', advanced=False, js=False, do_owl_imports=False,
        allow_infos=False, allow_warnings=False, inplace=False)
    _check_budget()
    errors = []
    if isinstance(report, Graph):
        for result in report.subjects(RDF.type, SH.ValidationResult):
            errors.append({'code': 'SHACL_VIOLATION',
                           'node': str(report.value(result, SH.focusNode) or ''),
                           'path': str(report.value(result, SH.resultPath) or ''),
                           'message': str(report.value(result, SH.resultMessage) or '')})
    if not conforms and not errors:
        errors.append({'code': 'SHACL_FAILURE', 'message': str(report)})
    result = {'valid': bool(conforms), 'errors': sorted(errors, key=canonical), 'warnings': []}
    _check_budget()
    return result


@lru_cache(maxsize=32)
def _prepared(body):
    return prepareQuery('''
        SELECT DISTINCT ?s ?payload ?ordinal WHERE {
    ''' + body + '''
          ?s projection:payload ?payload ; projection:ordinal ?ordinal ;
             projection:data_version ?wantedVersion ;
             projection:schema_version ?wantedSchema ;
             projection:rule_version ?wantedRule .
        } ORDER BY ?ordinal
    ''', initNs={'o': O, 'projection': P})


class RdfSnapshot:
    """One validated, version-pinned RDF projection with no mutation API."""

    def __init__(self, nodes, *, version, schema_version, rule_version):
        _check_budget()
        if isinstance(version, bool) or not isinstance(version, int) or version < 0:
            raise ValueError('Data version must be a nonnegative integer')
        if schema_version != SCHEMA_VERSION or not isinstance(rule_version, str) or not rule_version.strip():
            raise ValueError('Unsupported schema or missing rule version')
        report = validate_graph(nodes)
        _check_budget()
        if not report['valid']:
            raise ValueError('Invalid domain graph: ' + canonical(report['errors']))
        copied = json.loads(canonical(nodes))
        self.version, self.schema_version, self.rule_version = version, schema_version, rule_version
        self.content_hash = digest(copied)
        self.node_count = len(copied)
        _check_budget()
        self._graph = _graph(copied, version, schema_version, rule_version)
        self.validation = validate_rdf(self._graph)
        if not self.validation['valid']:
            raise ValueError('RDF validation failed: ' + canonical(self.validation['errors']))

    def rdf_graph(self):
        """Return an independent graph for export/inspection, not a write handle."""
        _check_budget()
        copied = Graph()
        for triple in self._graph:
            copied.add(triple)
        _check_budget()
        return copied

    def _decode(self, row):
        node = json.loads(str(row.payload))
        subject = row.s
        if (subject != entity_uri(node['id']) or
                set(self._graph.objects(subject, RDF.type)) != {O[node['type']]} or
                set(self._graph.objects(subject, P.entity_id)) != {Literal(node['id'])}):
            raise ValueError('RDF entity identity disagrees with its payload')
        for field in ALLOWED_FIELDS[node['type']] - {'id', 'type'}:
            if set(self._graph.objects(subject, O[field])) != _rdf_terms(node, field):
                raise ValueError('RDF field disagrees with its payload: ' + field)
        return node

    def _select(self, body, **bindings):
        _check_budget()
        bindings.update(wantedVersion=Literal(self.version), wantedSchema=Literal(self.schema_version),
                        wantedRule=Literal(self.rule_version))
        prepared = _prepared(body)
        _check_budget()
        rows = self._graph.query(prepared, initBindings=bindings)
        _check_budget()
        result = [self._decode(row) for row in rows]
        _check_budget()
        return result

    def nodes(self):
        nodes = self._select('?s projection:entity_id ?entityId .')
        if len(nodes) != self.node_count or digest(nodes) != self.content_hash:
            raise ValueError('RDF snapshot is incomplete or has mixed content/versions')
        _check_budget()
        return nodes

    def lookup(self, node_id=None, kind=None):
        _check_budget()
        body, bindings = '?s projection:entity_id ?entityId .', {}
        if node_id is not None:
            body += ' FILTER (?entityId = ?wantedId) '
            bindings['wantedId'] = Literal(node_id)
        if kind is not None:
            if kind not in SCHEMA:
                return []
            body += ' ?s a ?wantedType . '
            bindings['wantedType'] = O[kind]
        return self._select(body, **bindings)

    def configuration(self, configuration_id):
        nodes = self._select('''
            { ?s a o:Configuration ; projection:entity_id ?wantedId }
            UNION { ?config a o:Configuration ; projection:entity_id ?wantedId ; o:revision_id ?s }
            UNION { ?config a o:Configuration ; projection:entity_id ?wantedId ; o:revision_id/o:model_id ?s }
            UNION { ?config a o:Configuration ; projection:entity_id ?wantedId .
                    ?s a o:BOMItem ; o:configuration_id ?config }
            UNION { ?config a o:Configuration ; projection:entity_id ?wantedId .
                    ?item a o:BOMItem ; o:configuration_id ?config ; o:component_id ?s }
        ''', wantedId=Literal(configuration_id))
        index = {node['id']: node for node in nodes}
        config = index.get(configuration_id)
        if config is None or config['type'] != 'Configuration':
            raise ValueError('Unknown configuration')
        items = [n for n in nodes if n['type'] == 'BOMItem']
        revision = index[config['revision_id']]
        return {'configuration': config, 'revision': revision, 'product': index[revision['model_id']],
                'items': items, 'components': [index[cid] for cid in sorted({n['component_id'] for n in items})]}

    def component_usage(self, component_id):
        nodes = self._select('''
            { ?s a o:ComponentModel ; projection:entity_id ?wantedId }
            UNION { ?component a o:ComponentModel ; projection:entity_id ?wantedId .
                    ?s a o:BOMItem ; o:component_id ?component }
        ''', wantedId=Literal(component_id))
        if not any(n['id'] == component_id and n['type'] == 'ComponentModel' for n in nodes):
            raise ValueError('Unknown source-scoped component')
        return {'component_id': component_id, 'items': [n for n in nodes if n['type'] == 'BOMItem'],
                'scope': 'exact component identity; unverified cross-revision equivalence is not merged'}

    def temporal_nodes(self):
        # Retrieve all relevant types, including other lots and coverage. Filtering
        # to just one lot here could suppress conflicts needed by temporal.py.
        return self._select('''
            VALUES ?kind { o:ProductUnit o:ComponentLot o:Installation o:HistoryCoverage }
            ?s a ?kind .
        ''')
