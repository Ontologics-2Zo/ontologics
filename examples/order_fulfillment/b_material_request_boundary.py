"""Read-only registration preflight; no DB, approval, projection or monkeypatch.

The minimal declaration is a capability probe, not the final business schema.
Run: python3 -m examples.order_fulfillment.b_material_request_boundary
Exit 2 means runtime registration is blocked, not successful data registration.
"""
import json
from ontologics import model
from ontologics.tbox import Extension, build_tbox, registered_extensions

REQUEST_TYPE = 'AdditionalMaterialReviewRequest'


def inspect_boundary():
    extension = Extension('B request capability probe', {
        REQUEST_TYPE: (['configuration_id', 'quantity', 'unit'],
                       {'configuration_id': 'Configuration'}),
    })
    turtle = build_tbox((*registered_extensions(), extension))
    # Intentionally not a production request: no invented graph references or evidence.
    # The unknown-type check runs before field/reference validation.
    probe = {'id': 'b:request:capability-probe', 'type': REQUEST_TYPE,
             'origin': 'synthetic', 'evidence': [], 'quantity': 30, 'unit': 'piece'}
    report = model.validate_graph([probe])
    registered = REQUEST_TYPE in model.SCHEMA
    return {
        'status': 'blocked' if not registered else 'requires_full_registration_test',
        'request_type': REQUEST_TYPE,
        'tbox_declaration_generated': f'o:{REQUEST_TYPE} a owl:Class .' in turtle,
        'runtime_registered': registered,
        'validation': report,
        'database_tested': False,
        'neo4j_tested': False,
        'note': 'Declaration generation alone does not register a type. No data was saved.',
    }


def main():
    result = inspect_boundary()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result['status'] == 'blocked' else 0


if __name__ == '__main__':
    raise SystemExit(main())
