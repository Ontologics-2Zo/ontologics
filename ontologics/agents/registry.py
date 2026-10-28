"""Fixed service-role registry. Names and authority are trusted host choices.

External AgentSpec registrations still use CommonHost(agent_registrations=...).
Subclassing a builtin does not make it a trusted builtin role.
"""
import hashlib
from pathlib import Path
from types import MappingProxyType
from ..contracts import AgentTask
from ..harness import PinnedContext
from .product.agent import ProductAgent
from .quality.agent import QualityAgent
from .knowledge.agent import KnowledgeAgent
from .supervisor.agent import SupervisorAgent

ROLES = MappingProxyType({'product': ProductAgent, 'quality': QualityAgent,
                          'knowledge': KnowledgeAgent, 'supervisor': SupervisorAgent})

def role_class(role):
    return ROLES[role]

def is_builtin(implementation):
    return type(implementation) in ROLES.values()

def BuiltinRole(role, flow, messages):
    """Legacy constructor spelling; all implementations live in role packages."""
    return role_class(role)(flow, messages)

def builtin_task(role, flow, question, version, calls, messages, *, options=None, conditions=None):
    implementation = BuiltinRole(role, flow, messages)
    task = AgentTask({'question': question, 'calls': calls, 'flow': flow, 'options': options or {}},
                     PinnedContext(implementation.spec.instructions, version, conditions=conditions or {}))
    return implementation, task

def run_screen(role, app, request, body):
    # Local imports avoid an execution/registry cycle. No role implementation here.
    from .product.workflow import run_screen as product
    from .quality.workflow import run_screen as quality
    from .knowledge.workflow import run_screen as knowledge
    return {'product': product, 'quality': quality, 'knowledge': knowledge}[role](app, request, body)

def implementation_digest(implementation):
    """Invalidate read checkpoints if role helpers or shared dispatch change.

    Only repository code for fixed roles is read, never caller-supplied paths.
    Persist a digest, not source. Saved business drafts are not changed.
    """
    if not is_builtin(implementation):
        return None
    root = Path(__file__).parent
    files = sorted((root / implementation.ROLE).glob('*.py'))
    files += [root / name for name in ('base.py', 'execution.py', 'registry.py')]
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()
