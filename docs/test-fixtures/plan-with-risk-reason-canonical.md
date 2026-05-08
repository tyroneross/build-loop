---
id: fixture-risk-reason-canonical
dimension: test
severity: low
label: "Fixture: canonical risk_reason value"
architecture_impact: false
files_touched:
  - lib/auth.ts
risk_reason: security boundary
---

# Plan: Fixture — canonical risk_reason

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
Item 16 — Risk reason: security boundary — auth logic change
-->

## Goal

Test fixture verifying that `risk_reason: security boundary` (a canonical value) passes plan_verify without a BLOCKER for rule `risk-reason-invalid-value`.

## Scope

Minimal fixture. No real implementation.

### Out of scope

Everything except validating the risk_reason field.
