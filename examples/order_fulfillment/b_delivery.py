"""Retired stored-graph example; use b_run tasks for supported input-only agents.

The pure representative graph exporter remains available.
"""
from ontologics.order_fulfillment.schema_export import graph_jsonld
from ontologics.order_fulfillment.schema import LOOKUP_SCHEMA as SCHEMA, LIST_REFERENCES


def representative_graph(base, candidates):
    """Export exact typed/evidence dependency closure, never merge scenarios."""
    index = {node['id']: node for node in base + candidates}
    selected = set()

    def visit(node_id):
        if node_id in selected:
            return
        selected.add(node_id)
        node = index[node_id]
        for field in SCHEMA[node['type']][1]:
            if field in node:
                visit(node[field])
        for field in LIST_REFERENCES.get(node['type'], {}):
            for ref in node[field]:
                visit(ref)
        for ref in node['evidence']:
            visit(ref)

    for node in candidates:
        visit(node['id'])
    return graph_jsonld([node for node in index.values() if node['id'] in selected])



def run_example(database=None, *, schema=None, mixed=False):
    """Reject the old storage path before reading inputs or opening resources."""
    from .b_run_isolation import LEGACY_EXECUTION_MESSAGE
    raise RuntimeError(LEGACY_EXECUTION_MESSAGE)


def main():
    from .b_run_isolation import LEGACY_EXECUTION_MESSAGE
    raise SystemExit(LEGACY_EXECUTION_MESSAGE)



if __name__ == '__main__':
    main()
