"""Read fixed reference inputs and validate them; no DB, LLM or inventory writes.

Run from repository root: python3 -m examples.production_planning_inputs
This is a development host example, not a contract parser or an Agent tool.
"""
import json
from pathlib import Path

from ontologics.production_planning import validate_planning_context

ROOT = Path(__file__).resolve().parents[1]
INPUTS = ROOT / 'data/production_planning'


def load_reference_inputs():
    """Load the fixed request and original bytes; no model-chosen file paths."""
    request = json.loads((INPUTS / 'request.json').read_text(encoding='utf-8'))
    names = {'input:planning:contract': 'contract.txt', 'input:planning:stock': 'stock.json',
             'input:planning:production': 'production.json'}
    contents = {ref: (INPUTS / name).read_bytes() for ref, name in names.items()}
    stock = json.loads(contents['input:planning:stock'])
    production = json.loads(contents['input:planning:production'])
    request.update(stock_scope=stock['stock_scope'], stocks=stock['stocks'],
                   production_conditions=production['production_conditions'])
    return request, contents


def main():
    context, contents = load_reference_inputs()
    # Version 1 is a local fixture label, not a claimed approved PG version.
    reference = {'data_version': context['data_version'],
        'nodes': json.loads((ROOT / 'data/catalog.json').read_text(encoding='utf-8'))['nodes']}
    report = validate_planning_context(context, reference=reference, external_contents=contents)
    print(json.dumps({'reference_mode': 'local_catalog_fixture_not_approved_snapshot',
                      'report': report}, ensure_ascii=False, indent=2))
    return 0 if report['valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
