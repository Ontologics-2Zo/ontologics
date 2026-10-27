"""Pinned CQ06 alternative updates to the unchanged CQ04 baseline.

Loading returns fresh put candidates only; it does not register, approve or
apply a change. Each case starts independently from the CQ04 production-only
approved graph. In particular, alternative11:00 observations must not be stacked.
The helper never reads personal JunhwaDocs files or any model/API configuration.
"""
from __future__ import annotations

import hashlib
import json

from .delivery_demo import AS_OF
from ..model import ROOT

SOURCE_ID = 'source:demo:CQ06:recalculation-v1'
SOURCE_PATH = 'data/order_fulfillment/sources/b_demo_recalculation_01.md'
SOURCE_SHA256 = '4f7cd72e3710525e41a161d04ef44d5faaf485953a5c1f2a09358f249bf726cc'
CASES = ('continued11', 'defects11', 'transport2h', 'combined11', 'zero_rate11')
AFTER_AS_OF = {case: AS_OF if case == 'transport2h' else
               '2026-10-27T11:00:00+09:00' for case in CASES}


def load_demo_recalculation(case='continued11'):
    """Read immutable source-backed updates; no existing source bytes change."""
    if not isinstance(case, str) or case not in CASES:
        raise ValueError('Select one explicit CQ06 independent update case')
    fixture = json.loads((ROOT / 'data/order_fulfillment/b_recalculation_fixture.json').read_text())
    if (fixture.get('source_id') != SOURCE_ID or fixture.get('source_path') != SOURCE_PATH
            or fixture.get('source_sha256') != SOURCE_SHA256
            or hashlib.sha256((ROOT / SOURCE_PATH).read_bytes()).hexdigest() != SOURCE_SHA256):
        raise ValueError('CQ06 source bytes differ; new source/evidence IDs are required')
    selected = fixture.get('cases', {}).get(case, {})
    if selected.get('as_of') != AFTER_AS_OF[case]:
        raise ValueError('CQ06 explicit case observation time differs')
    nodes = selected.get('nodes')
    if not isinstance(nodes, list):
        raise ValueError('CQ06 case candidates must be a node list')
    source = next((node for node in nodes if node.get('id') == SOURCE_ID), None)
    if (source is None or source.get('type') != 'SourceDocument'
            or source.get('path') != SOURCE_PATH or source.get('sha256') != SOURCE_SHA256):
        raise ValueError('CQ06 SourceDocument metadata differs from the pinned source')
    return nodes
