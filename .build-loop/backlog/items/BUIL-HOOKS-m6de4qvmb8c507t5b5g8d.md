---
id: BUIL-HOOKS-m6de4qvmb8c507t5b5g8d
schema_version: 1
title: Remove bootstrap progress assumption from deadline test
status: open
priority: P2
type: fix
area: hooks
bucket: planned
workstream: hooks
related_to: []
decision_options: []
decision_impacts: []
entities: []
gated: none
provenance:
  source: retrospective
  ref: scripts/test_bounded_hook.py:277
impact: "The suite can reject correct deadline behavior under scheduler or interpreter startup load, obscuring hook regressions."
observed: 2026-11-01
evidence: [scripts/test_bounded_hook.py:261, hooks/session-start-memory.sh:23, hooks/_bounded_hook.sh:26]
supersedes: null
superseded_by: null
created: 2026-11-01
updated: 2026-11-01
review_by: 2026-12-01
owner: unassigned
---

## What happened
During Task A verification, the hook suite passed 239 tests and failed test_memory_bootstrap_retries_partial_store_without_overwriting because the memory directory did not exist after a 500 ms timeout. The test then passed alone. A fixture with 700 ms interpreter startup reproduced return code 0, empty JSON fallback and no created destination in 533 ms.

## When
2026-11-01

## Impact
The suite can reject correct deadline behavior under scheduler or interpreter startup load, obscuring hook regressions.

## Recommendation
Precreate a partial store and personal sentinel for retry-preservation coverage. Test interruption separately with a readiness handshake. Preserve production deadlines.

## Why
The whole-invocation deadline includes supervisor, shell and Python startup before installation reaches mkdir; there is no minimum-progress guarantee. The assertion and memory helper predate the reviewed range (2954297b1). Safely repairing the test needs a separate interruption fixture; increasing a delay would conceal the assumption.
