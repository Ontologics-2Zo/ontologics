"""Compatibility entry point for the shared common + A + B T-box artifact.

The A schema/validator remain local to materials. This command does
not register A or B types in common storage, approvals, SHACL or Neo4j.
"""
from ..tbox import build_tbox, extension_lines, main

__all__ = ['build_tbox', 'extension_lines', 'main']

if __name__ == '__main__':
    main()
