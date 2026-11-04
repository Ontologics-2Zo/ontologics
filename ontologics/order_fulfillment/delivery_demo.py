"""Pinned synthetic B-CQ04 candidates; separate production-only and mixed scopes.

These shared files are newly authored development facts/assumptions. Loading is
read-only and does not register, approve or apply them. The code has no dependency
on personal JunhwaDocs notes or on the CQ02/CQ03 operational examples.
"""
from __future__ import annotations

import hashlib
import json

from ..model import ROOT

AS_OF = '2026-10-27T10:00:00+09:00'
ROUND = 'delivery-round:demo:B-01:1'
SNAPSHOT = 'snapshot:demo:CQ04:base:10'
WORK = 'work:demo:CQ04:base'
CONDITION = 'condition:demo:CQ04:base'
MIXED_SNAPSHOT = 'snapshot:demo:CQ04:mixed:10'
MIXED_WORK = 'work:demo:CQ04:mixed:P'
MIXED_CONDITION = 'condition:demo:CQ04:mixed'
SOURCE_ID = 'source:demo:CQ04:delivery-v1'
SOURCE_PATH = 'data/order_fulfillment/sources/b_demo_delivery_01.md'
SOURCE_SHA256 = '510c474c1a68ab1cc34d3b323d3a1760f8ccda977fd127df0700c2b66a3e2d74'
MIXED_SOURCE_ID = 'source:demo:CQ04:delivery-mixed-v1'
MIXED_SOURCE_PATH = 'data/order_fulfillment/sources/b_demo_delivery_mixed_01.md'
MIXED_SOURCE_SHA256 = '392789dd5993d2c6b242d91f85aa8e06978c694f4c76bdfb3a847c7a5cb09544'


def load_demo_delivery(*, mixed=False):
    """Read independently pinned candidates for exactly one synthetic scenario."""
    if not isinstance(mixed, bool):
        raise ValueError('mixed must explicitly be boolean')
    path = MIXED_SOURCE_PATH if mixed else SOURCE_PATH
    digest = MIXED_SOURCE_SHA256 if mixed else SOURCE_SHA256
    source_id = MIXED_SOURCE_ID if mixed else SOURCE_ID
    fixture_path = 'data/order_fulfillment/b_delivery_mixed_fixture.json' if mixed else 'data/order_fulfillment/b_delivery_fixture.json'
    fixture = json.loads((ROOT / fixture_path).read_text())
    if (fixture['source_path'] != path or fixture['source_sha256'] != digest
            or fixture['source_id'] != source_id
            or hashlib.sha256((ROOT / path).read_bytes()).hexdigest() != digest):
        raise ValueError('B-CQ04 source bytes differ; new source/evidence IDs are required')
    source = next(n for n in fixture['nodes'] if n['id'] == source_id)
    if source['path'] != path or source['sha256'] != digest:
        raise ValueError('B-CQ04 SourceDocument metadata differs from the pinned source')
    return fixture['nodes']
