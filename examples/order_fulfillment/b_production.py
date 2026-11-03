"""Retired stored-graph example; use b_run tasks for supported input-only agents.
"""
def run_example(database=None, *, schema=None):
    """Reject the old storage path before reading inputs or opening resources."""
    from .b_run_isolation import LEGACY_EXECUTION_MESSAGE
    raise RuntimeError(LEGACY_EXECUTION_MESSAGE)


def main():
    from .b_run_isolation import LEGACY_EXECUTION_MESSAGE
    raise SystemExit(LEGACY_EXECUTION_MESSAGE)



if __name__ == '__main__':
    main()
