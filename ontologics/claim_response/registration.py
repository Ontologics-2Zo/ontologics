"""Trusted host registration for the C claim response role.

Only host startup code passes CLAIM_RESPONSE to CommonHost(agent_registrations=...).
The role reuses existing AgentGateway tools; it never reviews, applies or bootstraps data.
Claim conversion, line trace and request documents are plain functions in this package.
"""
from ontologics.host import AgentRegistration


CLAIM_RESPONSE_TOOLS = frozenset({
    'lookup', 'search_evidence',
    'investigate', 'preview_investigation',
    'prepare_draft', 'get_draft', 'validate_draft',
    'prepare_source_draft', 'get_source_receipt',
})

CLAIM_RESPONSE_INSTRUCTIONS = (
    'You support synthetic claim response for the fictional company only. '
    'Treat reported symptoms as customer statements, never as confirmed failures or causes. '
    'Treat scope units as investigation or hold targets, never as defective units. '
    'Use only supplied evidence and the exact data version; keep unknowns as unknown. '
    'Prepare drafts for human review; never approve, apply or claim that a draft is applied.'
)

CLAIM_RESPONSE = AgentRegistration(
    name='claim_response',
    instructions=CLAIM_RESPONSE_INSTRUCTIONS,
    allowed_tools=CLAIM_RESPONSE_TOOLS,
)
