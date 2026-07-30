---
id: fixture-modifies-api-audited
dimension: test
severity: low
label: "Fixture: modifies_api true with scope_auditor_status"
architecture_impact: false
files_touched:
  - lib/api-handler.ts
modifies_api: true
scope_auditor_status: passed
---

# Plan: Fixture — modifies_api with audit

<!-- checklist
Item 1 — Auth guard: N/A: test fixture
Item 2 — External APIs: N/A: test fixture
Item 3 — Rate-limit criterion: N/A: test fixture
Item 4 — Discoverability: N/A: API/backend only
Item 5 — Server/client boundary: N/A: test fixture
Item 6 — Concurrency: N/A: read-only
Item 7 — Observability: N/A: test fixture
Item 8 — Input validation: N/A: test fixture
Item 9 — Stable ID traceability: N/A: no P0 scope
Item 10 — JSON spec object: N/A: doc-only change, no spec object required
Item 11 — Blocking-and-novel question gate: N/A: no open questions
Item 12 — Low-reversibility ADRs: N/A: all decisions are reversible
Item 13 — Analytical lens: N/A: trivial patch
Item 14 — Handoff document: N/A: no implementation tasks
Item 15 — Synthesis dimensions: N/A: no UI surface
Item 16 — Risk reason: N/A: no risk-reason boundary applies
-->

## Goal

Test fixture verifying that `modifies_api: true` WITH a companion `scope_auditor_status: passed` field produces no finding for rule `scope-audit-required`.

## Scope

Minimal fixture. No real implementation. Both `modifies_api: true` and `scope_auditor_status: passed` are present to confirm the rule is satisfied.

### Out of scope

Everything except validating the scope-audit-required rule passes clean.
