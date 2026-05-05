# scripts/_attic/

Scripts moved here are NOT runtime — they are kept for historical traceability.

If a tool here is needed again, prefer to:

1. Read it for context.
2. Re-implement in current style (idioms, helpers, dependencies may have moved).
3. Don't restore in place.

## Contents

- `migrate_schema_v2.py` / `test_migrate_schema_v2.py` — one-shot v1→v2 metadata migration. Run completed; the `.sql` initializer at `scripts/init_agent_memory_schema.sql` carries the v2 fields natively.
- `migrate_schema_v3.py` / `test_migrate_schema_v3.py` — one-shot v2→v3 metadata migration. Same status as v2.
- `swift-platform-parity.py` — Swift cross-platform parity scanner from an unrelated coding-work review plan. Never wired into build-loop; no callers in agents/, skills/, commands/, scripts/, or src/.

## Restore

```
git mv scripts/_attic/<file> scripts/<file>
```
