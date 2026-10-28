"""Product result validation against the host's exact saved draft version."""
from ...service import DomainError


def decode_draft(task, tool_results):
    writes = [row['result']['data'] for row in tool_results if row['name'] == 'prepare_source_draft']
    validations = [row for row in tool_results if row['name'] == 'validate_draft']
    if not writes or not validations:
        raise DomainError('AI_DRAFT_INCOMPLETE', 'Exact draft save and validation are required')
    draft, checked = writes[-1], validations[-1]
    if checked['arguments'] != {'draft_id': draft['draft_id'], 'version': draft['draft_version']}:
        raise DomainError('AI_CONDITION_MISMATCH', 'Validation must use the saved draft version')
    return {'data_version': task.context.data_version, 'draft_id': draft['draft_id'],
            'draft_version': draft['draft_version'],
            'validation_valid': checked['result']['data']['validation']['valid'], 'action': 'draft_prepared',
            'source_receipt_id': next(call['arguments']['receipt_id'] for call in task.data()['calls']
                                      if call['name'] == 'get_source_receipt'),
            'provenance_scope': 'unapproved_candidate_or_draft'}
