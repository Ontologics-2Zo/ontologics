"""Compatibility exports for the former flat AI-panel module.

New callers use agents.service; role implementations live in agents/<role>/.
"""
from .agents.service import assistant
from .agents.execution import ScreenProvider, PROMPT, run_reads, needs_input, attach_delegation
from .agents.knowledge.validators import verified_knowledge_text
from .agents.knowledge.workflow import knowledge_search
from .agents.product.workflow import ReceiptDraftProvider, prepare_receipt_draft
from .agents.quality.validators import validated_quality_proposal
from .agents.quality.workflow import propose_quality_conditions
