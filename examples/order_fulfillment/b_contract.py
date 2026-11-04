"""Historical CQ01 promise selection helper and retired storage entry point.

Use b_run tasks for the supported input-only intake and promise agents.
"""

from ontologics.model import ROOT
from ontologics.order_fulfillment.schema_export import graph_jsonld, tbox_turtle
from ontologics.order_fulfillment.schema import LOOKUP_SCHEMA as SCHEMA


def promise_values(data):
    """Select factual fields for comparison with the independently authored answer."""
    contract, item, delivery = data['contract'], data['contract_item'], data['delivery_round']
    return {'contract_id': contract['id'], 'contract_item_id': item['id'],
            'delivery_round_id': delivery['id'], 'company': contract['company'],
            'customer_name': contract['customer_name'],
            'configuration_id': data['configuration']['id'],
            'configuration_name': data['configuration']['name'],
            'contract_quantity': item['quantity'], 'quantity': delivery['quantity'],
            'unit': delivery['unit'], 'due_at': delivery['due_at'],
            'due_basis': delivery['due_basis'], 'destination': delivery['destination'],
            'fulfillment_assessed': data['fulfillment_assessed']}


def run_example(database=None, *, schema=None):
    """Reject the retired stored-graph path before accessing inputs or resources."""
    from .b_run_isolation import LEGACY_EXECUTION_MESSAGE
    raise RuntimeError(LEGACY_EXECUTION_MESSAGE)


def main():
    from .b_run_isolation import LEGACY_EXECUTION_MESSAGE
    raise SystemExit(LEGACY_EXECUTION_MESSAGE)


if __name__ == '__main__':
    main()
