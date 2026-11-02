"""Reviewed A/B/C graph metadata, independent of role imports and execution.

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


# C claim response, 1st phase (PR #6). Kept in its own mapping so the reviewed
# A/B set above (SCHEMA, A_TYPES, B_TYPES) is unchanged; model.py composes both.
# All C records are synthetic demonstration operations, never OLIMEX facts.
C_SCHEMA = {
    'ProductionLine': (['company', 'line_code', 'name'], {}),
    'UnitProductionRecord': (['unit_id', 'production_line_id', 'produced_at'],
                             {'unit_id': 'ProductUnit', 'production_line_id': 'ProductionLine',
                              # Unsupported link: declared for the shared range only; any value is rejected (C rule).
                              'production_work_id': 'ProductionWork'}),
    'ClaimIntake': (['external_claim_id', 'customer_name', 'reported_at', 'reported_symptom', 'claimed_quantity',
                     'reported_serials', 'intake_state'], {}),
    'QualityCase': (['intake_id', 'customer_name', 'reported_at', 'reported_symptom', 'unit_ids'],
                    {'intake_id': 'ClaimIntake'}),
    'QualityScope': (['case_id', 'production_line_id', 'as_of', 'unit_ids', 'scope_state'],
                     {'case_id': 'QualityCase', 'production_line_id': 'ProductionLine'}),
    'ContainmentRequest': (['case_id', 'scope_id', 'request_kind', 'prepared_at', 'document_ref'],
                           {'case_id': 'QualityCase', 'scope_id': 'QualityScope', 'production_line_id': 'ProductionLine'}),
}
C_TYPES = tuple(C_SCHEMA)
OPTIONAL_REFERENCES.update({'UnitProductionRecord': {'production_work_id'},
                            'ContainmentRequest': {'production_line_id'}})
LIST_REFERENCES.update({'QualityCase': {'unit_ids': 'ProductUnit'}, 'QualityScope': {'unit_ids': 'ProductUnit'}})
OPTIONAL_FIELDS.update({
    'UnitProductionRecord': {'production_work_id'},
    'ClaimIntake': {'reported_conditions', 'requested_action', 'delivery_reference'},
    'QualityScope': {'start', 'end'},
    'ContainmentRequest': {'production_line_id', 'draft_id', 'draft_version'},
})
# claimed_quantity and draft_version are positive integers, reported_serials is a
# unique text list; their exact checks are C rules (claim_response/schema.py).
STRUCTURED_FIELDS.update({
    'ClaimIntake': {'claimed_quantity', 'reported_serials'},
    'QualityCase': {'unit_ids'},
    'QualityScope': {'unit_ids'},
    'ContainmentRequest': {'draft_version'},
})

def subset(mapping, kinds):
    """Return detached metadata without changing insertion order or ownership."""
    return deepcopy({kind: value for kind, value in mapping.items() if kind in kinds})
