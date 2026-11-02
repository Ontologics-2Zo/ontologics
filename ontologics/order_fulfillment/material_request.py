"""Assemble a reviewed B request for the existing common draft workflow.

The caller supplies stable IDs and structured business values checked against
the source by a user. This adapter does not infer demand from a shortage or
parse free text into confirmed facts. Source/hash and graph checks remain in
the common draft validator; source meaning is part of the human review.
"""
from copy import deepcopy

from ..service import DomainError
from .material_request_schema import REQUEST_TYPE


def request_operations(request, supporting_nodes, approved_nodes, *, base_version,
                       expected_request_revision):
    """Build puts against the approved state read under the common store lock.

    This function performs no I/O and grants no approval. The host supplies
    base_version; callers must not use it outside the locked draft action.
    """
    managed = {'type', 'origin', 'request_revision', 'reference_data_version'}
    if (not isinstance(request, dict) or managed & set(request)
            or not isinstance(request.get('id'), str) or not request['id'].strip()):
        raise DomainError('INVALID_INPUT', 'Provide request business fields and a stable ID; metadata is host-owned')
    if type(expected_request_revision) is not int or expected_request_revision < 0:
        raise DomainError('INVALID_INPUT', 'Expected request revision must be a nonnegative integer')
    supporting_nodes = [] if supporting_nodes is None else supporting_nodes
    if not isinstance(supporting_nodes, list) or len(supporting_nodes) > 1999:
        raise DomainError('INVALID_REQUEST_SUPPORT', 'Provide at most 1999 source/evidence nodes')
    for node in supporting_nodes:
        if (not isinstance(node, dict) or node.get('type') not in ('SourceDocument', 'Evidence')
                or node.get('origin') != 'synthetic'):
            raise DomainError('INVALID_REQUEST_SUPPORT', 'Only synthetic SourceDocument/Evidence support is accepted')

    previous = next((node for node in approved_nodes if node['id'] == request['id']), None)
    if previous is not None and previous['type'] != REQUEST_TYPE:
        raise DomainError('TYPE_CHANGE', 'The request ID already belongs to another node type')
    actual_revision = previous['request_revision'] if previous is not None else 0
    if actual_revision != expected_request_revision:
        raise DomainError('STALE_REQUEST_REVISION', 'Expected revision differs from the approved request')
    node = deepcopy(request)
    node.update(type=REQUEST_TYPE, origin='synthetic',
                request_revision=actual_revision + 1, reference_data_version=base_version)
    return [{'op': 'put', 'node': deepcopy(support)} for support in supporting_nodes] + [
        {'op': 'put', 'node': node}]
