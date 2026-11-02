"""Run common-validated B candidates without DB, model calls or persistence.

python3 -m examples.order_fulfillment.b_material_request
The request type is statically registered; common validation must succeed.
"""
import json

from ontologics.model import ROOT, validate_graph
from ontologics.order_fulfillment.material_request_validation import (
    validate_request_candidate, validate_request_transition,
)


def load_examples():
    """Load independent JSON copies; input versions are demonstration values.

    reference_data_version=0 does not claim any real database was queried.
    A trusted host must replace it with its actually checked snapshot version.
    """
    fixture = ROOT / 'examples/order_fulfillment/fixtures/material_requests.json'
    return json.loads(fixture.read_text())


def run_example():
    demo = load_examples()
    catalog = json.loads((ROOT / 'data/catalog.json').read_text())['nodes']
    facts = catalog + demo['supporting_nodes']
    requests = demo['requests']
    states = {
        'new_30': [requests['new_30']],
        'revised_40': [requests['revised_40']],
        'independent_10': [requests['revised_40'], requests['independent_10']],
    }
    candidates = {name: validate_request_candidate(facts + nodes) for name, nodes in states.items()}
    transitions = {
        'new_30': validate_request_transition(None, requests['new_30'], expected_request_revision=0),
        'revised_40': validate_request_transition(requests['new_30'], requests['revised_40'], expected_request_revision=1),
        'independent_10': validate_request_transition(None, requests['independent_10'], expected_request_revision=0),
    }
    shared = validate_graph(facts + states['new_30'])
    return {
        'scope': 'synthetic_b_candidate_only',
        'candidates': candidates, 'transitions': transitions,
        'latest_additional_quantities': {
            requests['revised_40']['id']: requests['revised_40']['quantity'],
            requests['independent_10']['id']: requests['independent_10']['quantity'],
        },
        'shared_validation': shared,
        'stored': False, 'approved': False, 'neo4j_verified': False,
    }


def main():
    result = run_example()
    # Preserve full reports for programmatic callers; keep terminal output small.
    def summary(report):
        return {'valid': report['valid'], 'errors': report['errors'],
                'warning_count': len(report.get('warnings', []))}
    output = {key: value for key, value in result.items()
              if key not in ('candidates', 'transitions', 'shared_validation')}
    output.update(candidates={key: summary(value) for key, value in result['candidates'].items()},
                  transitions={key: summary(value) for key, value in result['transitions'].items()},
                  shared_validation=summary(result['shared_validation']))
    print(json.dumps(output, ensure_ascii=False, indent=2))
    local_ok = all(row['valid'] for row in (*result['candidates'].values(), *result['transitions'].values()))
    return 0 if local_ok and result['shared_validation']['valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
