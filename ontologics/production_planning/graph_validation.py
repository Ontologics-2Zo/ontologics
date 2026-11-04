"""Compatibility entry point for validation of the complete common graph.

A/B nodes remain in the same index, so cross-domain references are checked
without dropping either side. Validation alone never approves or stores facts.
"""
from ..model import validate_graph
