"""Order fulfillment tools layered on the unchanged common service boundary.

Only B reads and their public tool allowlist live here. Database transactions,
identity checks, immutable drafts, review/apply and auditing remain inherited.
"""
from ..service import AgentGateway as CommonAgentGateway, DomainError, Service as CommonService
from .schema import LOOKUP_SCHEMA

B_TOOLS = frozenset({'lookup_many', 'delivery_round', 'fulfillment_status',
                     'production_completion', 'delivery_forecast', 'response_plan',
                     'compare_response_plan'})


class Service(CommonService):
    """Add B queries while retaining the common permission/audit transaction."""

    def lookup_many(self, actor, node_ids, kind, *, version=None):
        """Read one complete, bounded ID batch from one explicit confirmed version."""
        def act():
            if type(version) is not int or version < 0:
                raise DomainError('INVALID_VERSION', 'Batch reads require an explicit nonnegative data version')
            if (not isinstance(node_ids, list) or not 1 <= len(node_ids) <= 16
                    or any(not isinstance(nid, str) or not nid.strip() for nid in node_ids)
                    or len(set(node_ids)) != len(node_ids) or not isinstance(kind, str) or kind not in LOOKUP_SCHEMA):
                raise DomainError('INVALID_INPUT', 'Provide 1..16 unique nonempty node IDs and one known kind')
            index = {n['id']: n for n in self._nodes(version)}
            if any(nid not in index or index[nid]['type'] != kind for nid in node_ids):
                raise DomainError('NOT_FOUND', 'Every requested ID must exist with the requested kind in this version')
            return self._envelope(ok=True, data=[index[nid] for nid in node_ids], version=version)
        return self._run(actor, 'lookup_many', {'node_ids': node_ids, 'kind': kind, 'version': version}, act)


    def delivery_round(self, actor, delivery_round_id, *, version=None):
        """Read a contract promise in one explicit snapshot; no fulfillment forecast."""
        def act():
            if isinstance(version, bool) or not isinstance(version, int) or version < 0:
                raise DomainError('INVALID_VERSION', 'Delivery queries require an explicit nonnegative data version')
            if not isinstance(delivery_round_id, str) or not delivery_round_id.strip():
                raise DomainError('INVALID_INPUT', 'delivery_round_id must be nonempty text')
            idx = {n['id']: n for n in self._nodes(version)}
            round_node = idx.get(delivery_round_id)
            if not round_node or round_node['type'] != 'DeliveryRound':
                raise DomainError('NOT_FOUND', 'Unknown delivery round in the requested data version')
            item = idx[round_node['contract_item_id']]
            config = idx[item['configuration_id']]
            revision = idx[config['revision_id']]
            return self._envelope(ok=True, data={'contract': idx[item['contract_id']],
                'contract_item': item, 'delivery_round': round_node, 'configuration': config,
                'revision': revision, 'product': idx[revision['model_id']],
                'fulfillment_assessed': False}, version=version,
                warnings=[{'code': 'FULFILLMENT_NOT_ASSESSED',
                           'detail': 'Contract promise only; stock, production and transport are not assessed'}])
        return self._run(actor, 'delivery_round', {'delivery_round_id': delivery_round_id, 'version': version}, act)


    def fulfillment_status(self, actor, delivery_round_id, as_of, *, version=None):
        """Read an exact source-backed snapshot; never infer future feasibility."""
        from .fulfillment import fulfillment_status
        from ..model import instant
        def act():
            if isinstance(version, bool) or not isinstance(version, int) or version < 0:
                raise DomainError('INVALID_VERSION', 'Fulfillment queries require an explicit nonnegative data version')
            if not isinstance(delivery_round_id, str) or not delivery_round_id.strip():
                raise DomainError('INVALID_INPUT', 'delivery_round_id must be nonempty text')
            try:
                instant(as_of)
            except (ValueError, TypeError) as exc:
                raise DomainError('INVALID_TIME', str(exc)) from exc
            nodes = self._nodes(version)
            if not any(n['id'] == delivery_round_id and n['type'] == 'DeliveryRound' for n in nodes):
                raise DomainError('NOT_FOUND', 'Unknown delivery round in the requested data version')
            try:
                data = fulfillment_status(nodes, delivery_round_id, as_of, queried_at=self.now())
            except ValueError as exc:
                raise DomainError('VALIDATION_FAILED', 'Invalid fulfillment graph', exc.args[0]) from exc
            return self._envelope(ok=True, data=data, version=version,
                warnings=data['warnings'] + [{'code': 'FULFILLMENT_NOT_ASSESSED',
                    'detail': 'Snapshot only; production completion and future arrival are not forecast'}])
        return self._run(actor, 'fulfillment_status',
            {'delivery_round_id': delivery_round_id, 'as_of': as_of, 'version': version}, act)


    def delivery_forecast(self, actor, delivery_round_id, as_of, *, version=None):
        """Read conditional customer-arrival quantities in one confirmed version."""
        from .delivery_forecast import delivery_forecast
        from ..model import instant
        def act():
            if isinstance(version, bool) or not isinstance(version, int) or version < 0:
                raise DomainError('INVALID_VERSION', 'Delivery forecasts require an explicit nonnegative data version')
            if not isinstance(delivery_round_id, str) or not delivery_round_id.strip():
                raise DomainError('INVALID_INPUT', 'delivery_round_id must be nonempty text')
            try:
                instant(as_of)
            except (ValueError, TypeError) as exc:
                raise DomainError('INVALID_TIME', str(exc)) from exc
            nodes = self._nodes(version)
            if not any(n['id'] == delivery_round_id and n['type'] == 'DeliveryRound' for n in nodes):
                raise DomainError('NOT_FOUND', 'Unknown delivery round in the requested data version')
            try:
                data = delivery_forecast(nodes, delivery_round_id, as_of, queried_at=self.now())
            except ValueError as exc:
                raise DomainError('VALIDATION_FAILED', 'Invalid delivery graph or forecast input', exc.args[0]) from exc
            return self._envelope(ok=True, data=data, version=version, warnings=data['warnings'])
        return self._run(actor, 'delivery_forecast',
            {'delivery_round_id': delivery_round_id, 'as_of': as_of, 'version': version}, act)


    def response_plan(self, actor, delivery_round_id, as_of, *, version=None):
        """Read derived response drafts from one confirmed snapshot; never register them."""
        from .response_plan import response_plan
        from ..model import instant
        def act():
            if isinstance(version, bool) or not isinstance(version, int) or version < 0:
                raise DomainError('INVALID_VERSION', 'Response plans require an explicit nonnegative data version')
            if not isinstance(delivery_round_id, str) or not delivery_round_id.strip():
                raise DomainError('INVALID_INPUT', 'delivery_round_id must be nonempty text')
            try:
                instant(as_of)
            except (ValueError, TypeError) as exc:
                raise DomainError('INVALID_TIME', str(exc)) from exc
            nodes = self._nodes(version)
            if not any(n['id'] == delivery_round_id and n['type'] == 'DeliveryRound' for n in nodes):
                raise DomainError('NOT_FOUND', 'Unknown delivery round in the requested data version')
            try:
                data = response_plan(nodes, delivery_round_id, as_of, queried_at=self.now())
            except ValueError as exc:
                raise DomainError('VALIDATION_FAILED', 'Invalid response plan graph or forecast input', exc.args[0]) from exc
            return self._envelope(ok=True, data=data, version=version, warnings=data['forecast']['warnings'])
        return self._run(actor, 'response_plan',
            {'delivery_round_id': delivery_round_id, 'as_of': as_of, 'version': version}, act)


    def compare_response_plan(self, actor, delivery_round_id, before_as_of, after_as_of, *,
                              before_version=None, after_version=None):
        """Compare explicit approved inputs under one read/audit transaction.

        Both projections use the current common rules. This does not restore
        historical model answers or approve, persist or execute a new plan.
        """
        from .response_comparison import ComparisonError, compare_response_plan
        from ..model import instant
        def act():
            for version in (before_version, after_version):
                if isinstance(version, bool) or not isinstance(version, int) or version < 0:
                    raise DomainError('INVALID_VERSION', 'Comparison requires two explicit nonnegative data versions')
            if not isinstance(delivery_round_id, str) or not delivery_round_id.strip():
                raise DomainError('INVALID_INPUT', 'delivery_round_id must be nonempty text')
            try:
                instant(before_as_of)
                instant(after_as_of)
            except (ValueError, TypeError) as exc:
                raise DomainError('INVALID_TIME', str(exc)) from exc
            before, after = self._nodes(before_version), self._nodes(after_version)
            for nodes in (before, after):
                if not any(n['id'] == delivery_round_id and n['type'] == 'DeliveryRound' for n in nodes):
                    raise DomainError('NOT_FOUND', 'Unknown delivery round in one comparison version')
            try:
                data = compare_response_plan(before, after, delivery_round_id, before_as_of, after_as_of,
                    before_version=before_version, after_version=after_version, queried_at=self.now())
            except ComparisonError as exc:
                raise DomainError(exc.code, str(exc)) from exc
            except ValueError as exc:
                raise DomainError('VALIDATION_FAILED', 'Invalid comparison graph or forecast input', exc.args[0]) from exc
            warnings = (data['before']['response_plan']['forecast']['warnings'] +
                        data['after']['response_plan']['forecast']['warnings'])
            return self._envelope(ok=True, data=data, version=after_version, warnings=warnings)
        return self._run(actor, 'compare_response_plan', dict(delivery_round_id=delivery_round_id,
            before_version=before_version, before_as_of=before_as_of,
            after_version=after_version, after_as_of=after_as_of), act)


    def production_completion(self, actor, production_work_id, delivery_round_id, as_of, *, version=None):
        """Conditional good-unit completion from one explicit confirmed version."""
        from .production import production_completion
        from ..model import instant
        def act():
            if isinstance(version, bool) or not isinstance(version, int) or version < 0:
                raise DomainError('INVALID_VERSION', 'Production queries require an explicit nonnegative data version')
            for value in (production_work_id, delivery_round_id):
                if not isinstance(value, str) or not value.strip():
                    raise DomainError('INVALID_INPUT', 'Work and delivery round IDs must be nonempty text')
            try:
                instant(as_of)
            except (ValueError, TypeError) as exc:
                raise DomainError('INVALID_TIME', str(exc)) from exc
            nodes = self._nodes(version)
            index = {n['id']: n for n in nodes}
            for nid, kind in ((production_work_id, 'ProductionWork'), (delivery_round_id, 'DeliveryRound')):
                if index.get(nid, {}).get('type') != kind:
                    raise DomainError('NOT_FOUND', 'Unknown work or round in the requested data version')
            try:
                data = production_completion(nodes, production_work_id, delivery_round_id, as_of,
                                             queried_at=self.now())
            except ValueError as exc:
                raise DomainError('VALIDATION_FAILED', 'Invalid production graph or forecast input', exc.args[0]) from exc
            return self._envelope(ok=True, data=data, version=version, warnings=data['warnings'])
        return self._run(actor, 'production_completion', dict(production_work_id=production_work_id,
            delivery_round_id=delivery_round_id, as_of=as_of, version=version), act)



class AgentGateway(CommonAgentGateway):
    """Expose B tools without changing the common gateway or granting approval."""
    ALLOWED = CommonAgentGateway.ALLOWED | B_TOOLS
