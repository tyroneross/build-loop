---
id: BUILDLOOP-COORD-001
schema_version: 1
title: Enforce branch-closeout receipts on direct Rally resolution
status: deferred
priority: P1
type: feature
area: coord
entities: [rally, branch-closeout, integrator]
gated: none
provenance: {source: plan-audit, ref: docs/plans/2026-07-11-run-aware-closeout.md}
evidence: [docs/plans/2026-07-11-run-aware-closeout.md]
supersedes: null
superseded_by: null
created: 2026-07-11
updated: 2026-07-11
review_by: 2026-08-10
owner: unassigned
---

## Context

Build Loop now rejects its canonical `run-closeout` phase post unless the terminal receipt verifies. The current Rally CLI still accepts a direct handoff resolution without checking that receipt, so native Rally resolution remains the narrower bypass.

## Acceptance

- Rally or an owned integration wrapper rejects resolution of a merge handoff when the linked Build Loop branch-closeout receipt is missing, retryable, or nonterminal.
- The accepted path records the receipt URI, run ID, branch, and terminal status on the Rally event.
- Direct CLI bypass behavior and compatibility are tested for Claude, Codex, and terminal-hosted agents.

## Notes

Do this after the run-aware closeout receipt and report-only cleanup path are stable. The preferred boundary is a host-neutral Rally check or wrapper, not another SessionStart hook.
