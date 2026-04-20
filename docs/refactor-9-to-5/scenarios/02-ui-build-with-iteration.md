# Scenario 2: UI build with one iteration cycle

## Setup

- **Project**: Next.js + IBR installed + claude-code-debugger installed (via `availablePlugins`)
- **Goal**: "Add a dashboard card showing total active users with a sparkline of the last 7 days"
- **Scope**: 3 files (`DashboardCard.tsx`, `useActiveUsers.ts`, `dashboard.module.css`), ~120 lines
- **Criteria**:
  1. Tests pass
  2. IBR scan verdict: PASS (no Calm Precision violations)
  3. Lint/type check clean
  4. No mock data in production paths

## Expected failure mode at test time

First Review pass: Validate (sub-step B) sees the IBR scan flag a `gestalt` violation (the card has individual borders on list items). Routes to Iterate.

## What should fire

**First Review:**
- Critic (A) — reviews diff, probably clean
- Validate (B) — tests pass, but IBR scan flags Gestalt violation → FAIL. Memory-first gate queries debugger for similar UI pattern; verdict `NO_MATCH` (first time).
- Route to Iterate

**Iterate (attempt 1):**
- Debugger-bridge Iterate step — no evidence_gap, no prior-failure escalation yet
- Diagnose: "individual borders on list items"
- Fix plan: consolidate into single outer border, add dividers
- Execute fix (targeted)
- Loop back to Review

**Second Review:**
- Critic — skipped (same files, no new scope drift risk)
- Validate (B) — IBR scan PASS this time
- Optimize (C) — skipped (no mechanical metric)
- Fact-Check (D) — no rendered metrics, mock scan clean
- Simplify (E) — trim any over-abstracted helpers
- Report (F) — scorecard written; debugger `store` called with the Gestalt fix; `outcome` N/A (no prior memory to evaluate)

**Learn (6)**: skipped if < 3 prior runs, else scans `runs[]`.

## What should NOT fire

- NavGator bridges (not installed)
- Logging-tracer-bridge repair path (no silent failure)
- More than 1 iteration cycle
