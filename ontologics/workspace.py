"""Read-only business overview over one approved graph and the draft ledger.

These counts describe registered records, never actual production, fulfillment,
or defect quantities. The HTTP host supplies the actor; this is not an Agent
tool or a second approval/storage path.
"""
from collections import Counter

from .model import SCHEMA


DRAFT_LIMIT = 100
GROUP_LIMIT = 20
GROUP_KINDS = {
    'planning': ('Contract', 'ContractItem', 'ComponentStockSnapshot',
                 'ProductionWork', 'ProductionObservation'),
    'fulfillment': ('Contract', 'ContractItem', 'DeliveryRound', 'FulfillmentSnapshot', 'FinishedGoodsStock',
                    'Allocation', 'Shipment', 'DeliveryCondition'),
    'claims': ('ProductionLine', 'UnitProductionRecord', 'ClaimIntake',
               'QualityCase', 'QualityScope', 'ContainmentRequest',
               'TestRequest', 'QualityTestResult', 'ReproductionRequirement'),
}
LATEST_DRAFTS = 'version=(SELECT MAX(version) FROM drafts WHERE id=d.id)'


def dashboard(service, actor):
    def read():
        # _run holds the shared store lock while selecting the version, graph,
        # and draft ledger. A failed/unpublished projection fails the overview;
        # no partial success or fallback to a different backend is returned.
        version = service._version()
        nodes = service._read_nodes(version)
        graph_counts = {kind: 0 for kind in SCHEMA}
        graph_counts.update(Counter(node['type'] for node in nodes))
        groups = {}
        for name, declared in GROUP_KINDS.items():
            kinds = [kind for kind in declared if kind in SCHEMA]
            records = sorted((node for node in nodes if node['type'] in kinds),
                             key=lambda node: (node['type'], node['id']))
            groups[name] = {'kinds': kinds, 'total': len(records), 'limit': GROUP_LIMIT,
                            'truncated': len(records) > GROUP_LIMIT,
                            'records': records[:GROUP_LIMIT]}
        draft_counts = {row['status']: row['count'] for row in service.db.execute(
            'SELECT status, COUNT(*) AS count FROM drafts d WHERE ' + LATEST_DRAFTS +
            ' GROUP BY status').fetchall()}
        drafts = service.db.execute('''SELECT id AS draft_id, version, status, author, reviewer,
            base_version, applied_version AS applied_data_version, created_at
            FROM drafts d WHERE ''' + LATEST_DRAFTS +
            ' ORDER BY created_at DESC, id LIMIT %s', (DRAFT_LIMIT,)).fetchall()
        return service._envelope(ok=True, version=version, data={
            'version': version, 'draft_counts': draft_counts,
            'draft_total': sum(draft_counts.values()), 'draft_limit': DRAFT_LIMIT,
            'drafts': list(drafts), 'graph_counts': graph_counts, 'groups': groups})
    return service._run(actor, 'dashboard', {}, read)
