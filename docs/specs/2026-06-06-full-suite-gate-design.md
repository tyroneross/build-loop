<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
# Design — Part B collection fix + full-suite-collection run gate

**Date:** 2026-06-06
**Repo:** build-loop (from v0.30.0; HEAD `ca878e4` includes the merged Part A db fix)
**Status:** approved; implementation routes through build-loop

## Why

The merged peer commit `8750d2a` (Part A) fixed one collection-breaking import (lazy psycopg) and
documented the root systemic issue: **build-loop's run gate scoped to changed-area tests and
merged work while the full suite was red at collection.** Ground truth confirms a *second*
collection error still breaks `pytest scripts/ tests/ --collect-only` on `main` today:

```
tests/test_run_entry_execution_state.py:19
  ImportError: cannot import name 'EXECUTION_SCHEMA_VERSION' from 'write_run_entry'
```

Two parts: fix the broken symbol (Part B), and close the systemic gap so this class can't recur.

## Part B — restore the missing export (durable, not a test-patch)

`scripts/write_run_entry/execstate.py` holds the execution-state schema version as a bare
`n = 1` (used as `"schema_version": n`). The test imports `EXECUTION_SCHEMA_VERSION` from the
`write_run_entry` package, which is never defined or exported.

Fix (durable + readable over compact):
- Rename `n` → `EXECUTION_SCHEMA_VERSION` in `execstate.py` (a schema-version constant deserves a
  real name; update its `"schema_version": EXECUTION_SCHEMA_VERSION` use site).
- Export it from `scripts/write_run_entry/__init__.py` (alongside `update_execution_state`,
  `compute_run_id`).
- Confirm `tests/test_run_entry_execution_state.py` imports resolve; do NOT paper over the bad name
  by editing only the test.

Acceptance: `pytest scripts/ tests/ --collect-only` returns **0 collection errors** (db/live tests
may still skip via their markers).

## Part C — full-suite-collection run gate (systemic, "every issue is a systems issue")

Add a cheap, durable gate so build-loop cannot close/merge a run while the full suite is red at
collection. Collection (not execution) is the right bar: it's fast, needs no `.[db]`/live services,
and a collection error means whole modules are silently untested (exactly how Parts A+B hid).

Design:
- A gate step (Review-B Validate, or a dedicated pre-close check) runs
  `pytest scripts/ tests/ --collect-only -q` under `env -u PYTHONPATH`.
- **0 collection errors → pass.** Any collection error → the run does NOT report success; it routes
  to Iterate (fix the import) — consistent with build-loop's "fix and continue" doctrine.
- This is collection-only; it does NOT require the full suite to *execute* green (db/live tests
  legitimately skip without extras). Executing changed-area tests stays as today; this gate adds
  the missing "is anything failing to even load?" guarantee.
- Keep it KISS: one check, one command, reuses existing pytest config + markers. No new framework.

Acceptance: a deliberately-broken import in any test module causes the gate to fail (regression
test or documented manual probe); a clean tree passes.

## Files (verify in Phase 1)

- `scripts/write_run_entry/execstate.py` + `scripts/write_run_entry/__init__.py` +
  `tests/test_run_entry_execution_state.py` (Part B).
- The run gate location — `agents/build-orchestrator.md` Review-B / Validate, and/or
  `skills/build-loop/references/phase-4-review.md`; plus any validate script under `scripts/`
  (locate the changed-area test-selection logic referenced by the run-gate). (Part C.)
- Tests under `tests/` / colocated `test_*.py`.
- Version bump 0.30.0 → 0.30.1 (patch — fix + gate), local only.

## Branch hygiene / merge-back

ONE run-worktree → collapse to `main` via `collapse_run.py`; remove any leftover
`.claude/worktrees/` at close; `createdRefs[]` + ledger updated before fan-out. Commit to LOCAL
main only — no push, no deploy without explicit user OK.

## Risks / rollback

- A collection gate that is too strict could block on a legitimately-optional module — mitigated:
  collection errors are real import breakages, not optional-skip cases (those use markers and
  collect fine). Rollback: revert the gate commit; Part B stands alone.
