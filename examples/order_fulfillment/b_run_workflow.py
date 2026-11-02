"""Retired stored-graph campaign entry point; use b_run tasks.

The former campaign and its scripted approvals are no longer supported.
Existing saved reports remain historical evidence, never replayed here.
"""
from pathlib import Path

from .b_run_transport import Blocked


def run_campaign(*, database_url: str, input_root: Path, output: Path, cases: list[str],
                 offline: bool = True, settings=None, backend=None, review_wait=None,
                 cost_plan=None):
    """Reject before reading inputs, contacting a backend or opening resources."""
    from .b_run_isolation import LEGACY_EXECUTION_MESSAGE
    raise Blocked(LEGACY_EXECUTION_MESSAGE)
