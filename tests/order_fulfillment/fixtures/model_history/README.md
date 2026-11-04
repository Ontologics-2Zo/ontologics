# B historical model test fixtures

These files preserve exact source bytes for B regression comparisons. They are
test data, not application modules, validators for production, or a schema
registration mechanism. The `.py.txt` suffix keeps them out of Python imports.

| Revision | Original sources |
| --- | --- |
| `ca7c391a9e813b6ab9e6fddfb48ab639c09a764b` | `ontologics/model.py` (common graph-0.1), `ontologics/service.py`, and `ontologics/host.py` (storage-boundary comparisons) |
| `4539c904ca05f4529f8da099eff5e6be8c160c50` | `ontologics/model.py` and its three `order_fulfillment/*_validation.py` dependencies (historical B graph-0.5) |

The files were copied byte for byte from these revisions. Their pinned SHA-256
values are in `tests/order_fulfillment/model_oracle.py`; every source is checked
before execution in private modules. Existing tests verify rejection of altered
model and dependency sources and isolation from live application modules.

Bundling the sources lets tests run in shallow CI checkouts or source copies
without `.git`. Tests do not fetch history or silently substitute current code.
Missing or altered fixtures fail the regression check. Update the revision,
expected hashes, source files, and comparison expectations together only after
reviewing a deliberate change to the historical comparison baseline.
