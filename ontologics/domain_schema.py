"""Reviewed A/B graph metadata, independent of role imports and execution.

The shared model composes these additive definitions with its original twelve
types. Role schema modules re-export detached subsets for import compatibility;
neither importing a role nor mutating a subset registers a new shared type.
"""
from copy import deepcopy

SCHEMA = {
    'Contract': (['company', 'customer_name'], {}),
    'ContractItem': (['contract_id', 'configuration_id', 'quantity', 'unit'],
                     {'contract_id': 'Contract', 'configuration_id': 'Configuration'}),
    'DeliveryRound': (['contract_item_id', 'quantity', 'unit', 'due_at', 'due_basis', 'destination'],
                      {'contract_item_id': 'ContractItem'}),
    'ComponentStockSnapshot': (['lot_id', 'location', 'as_of', 'unit', 'availability_basis', 'quantity_state'],
                               {'lot_id': 'ComponentLot'}),
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
A_TYPES = frozenset({'Contract', 'ContractItem', 'DeliveryRound', 'ComponentStockSnapshot'})
B_TYPES = frozenset(set(SCHEMA) - {'ComponentStockSnapshot'})
OPTIONAL_REFERENCES = {'Allocation': {'stock_id', 'production_work_id'}}
LIST_REFERENCES = {'FulfillmentSnapshot': {'round_ids': 'DeliveryRound'}}
OPTIONAL_FIELDS = {
    'ComponentStockSnapshot': {'available_quantity', 'deductions_disjoint', 'held_quantity', 'on_hand_quantity', 'reserved_quantity'},
    'ProductionWork': {'started_at', 'planned_start_at'},
    'Allocation': {'stock_id', 'production_work_id'},
    'Shipment': {'planned_departure_at', 'departed_at', 'expected_arrival_at', 'arrived_at'},
}
JSON_FIELDS = {'FulfillmentSnapshot': {'confirmations'}}
# Non-text fields receive their exact numeric/boolean/list/object validation in
# the existing domain rules. Null stock counts remain unknown, never zero.
STRUCTURED_FIELDS = {
    'ComponentStockSnapshot': set(OPTIONAL_FIELDS['ComponentStockSnapshot']),
    'FulfillmentSnapshot': {'round_ids', 'confirmations'},
    'ProductionWork': {'target_quantity', 'completed_quantity'},
    'ProductionObservation': {'produced_quantity', 'good_quantity', 'defective_quantity'},
    'DeliveryCondition': {'preparation_seconds', 'transport_seconds'},
}


def subset(mapping, kinds):
    """Return detached metadata without changing insertion order or ownership."""
    return deepcopy({kind: value for kind, value in mapping.items() if kind in kinds})
