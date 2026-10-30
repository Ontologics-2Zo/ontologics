"""B graph additions extracted from model.py, not yet wired into storage.

These definitions contain only B additions. Importing them does not register
types, change the common graph version, or modify model.SCHEMA.
"""
from copy import deepcopy

from ..model import COMMON_FIELDS, SCHEMA as _COMMON_SCHEMA


# Provenance of the extraction, not a new common or Agent contract version.
EXTRACTED_GRAPH_VERSION = 'graph-0.5'

SCHEMA = {
    'Contract': (['company', 'customer_name'], {}),
    'ContractItem': (['contract_id', 'configuration_id', 'quantity', 'unit'],
                     {'contract_id': 'Contract', 'configuration_id': 'Configuration'}),
    'DeliveryRound': (['contract_item_id', 'quantity', 'unit', 'due_at', 'due_basis', 'destination'],
                      {'contract_item_id': 'ContractItem'}),
    'FulfillmentSnapshot': (['company', 'as_of', 'round_ids', 'confirmations'], {}),
    'FinishedGoodsStock': (['snapshot_id', 'company', 'configuration_id', 'observed_at', 'location', 'quantity', 'unit'],
                           {'snapshot_id': 'FulfillmentSnapshot', 'configuration_id': 'Configuration'}),
    'ProductionWork': (['snapshot_id', 'company', 'configuration_id', 'observed_at', 'target_quantity', 'completed_quantity', 'unit', 'production_state'],
                       {'snapshot_id': 'FulfillmentSnapshot', 'configuration_id': 'Configuration'}),
    'ProductionObservation': (['production_work_id', 'observed_at', 'produced_quantity', 'good_quantity', 'defective_quantity', 'unit'],
                              {'production_work_id': 'ProductionWork'}),
    'Allocation': (['snapshot_id', 'delivery_round_id', 'quantity', 'unit'],
                   {'snapshot_id': 'FulfillmentSnapshot', 'delivery_round_id': 'DeliveryRound',
                    'stock_id': 'FinishedGoodsStock', 'production_work_id': 'ProductionWork'}),
    'Shipment': (['snapshot_id', 'allocation_id', 'quantity', 'unit', 'destination'],
                 {'snapshot_id': 'FulfillmentSnapshot', 'allocation_id': 'Allocation'}),
    'DeliveryCondition': (['snapshot_id', 'delivery_round_id', 'preparation_seconds', 'transport_seconds', 'dispatch_mode'],
                          {'snapshot_id': 'FulfillmentSnapshot', 'delivery_round_id': 'DeliveryRound'}),
}
SYNTHETIC = set(SCHEMA)
OPTIONAL_REFERENCES = {'Allocation': {'stock_id', 'production_work_id'}}
LIST_REFERENCES = {'FulfillmentSnapshot': {'round_ids': 'DeliveryRound'}}
OPTIONAL_FIELDS = {
    'ProductionWork': {'started_at', 'planned_start_at'},
    'Allocation': {'stock_id', 'production_work_id'},
    'Shipment': {'planned_departure_at', 'departed_at', 'expected_arrival_at', 'arrived_at'},
}
# Additions to the common validator's structured-field exceptions only.
STRUCTURED_FIELDS = {
    'round_ids', 'confirmations', 'target_quantity', 'completed_quantity',
    'produced_quantity', 'good_quantity', 'defective_quantity',
    'preparation_seconds', 'transport_seconds',
}
ALLOWED_FIELDS = {kind: COMMON_FIELDS | set(required) | OPTIONAL_FIELDS.get(kind, set())
                  for kind, (required, _) in SCHEMA.items()}


def lookup_schema(common_schema=None):
    """Return detached common+B metadata for B reads, without registering types.

    Matching duplicates support the current B-extended model during separation.
    Conflicts are configuration errors, never permission to override a domain.
    This metadata does not change common graph validation or storage support.
    """
    combined = deepcopy(_COMMON_SCHEMA if common_schema is None else common_schema)
    for kind, definition in SCHEMA.items():
        if kind in combined and combined[kind] != definition:
            raise ValueError('Conflicting common and B lookup schema: ' + kind)
        combined[kind] = deepcopy(definition)
    return combined


# Shared by the B service and tool descriptions for this process's startup.
LOOKUP_SCHEMA = lookup_schema()
