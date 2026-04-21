# Scenario 1: Simple bugfix

## Setup

- **Project**: mid-size Next.js app, no NavGator, no claude-code-debugger installed
- **Goal**: "Fix the `TypeError: Cannot read properties of undefined (reading 'email')` in `src/api/users.ts:42`"
- **Scope**: 1-2 files, ~15 lines
- **Criteria**:
  1. Tests pass (`npm test`)
  2. Lint clean (`npm run lint`)
  3. No new type errors (`tsc --noEmit`)

## Expected failure modes at test time

None — single-file fix, plan reveals the bug is an unchecked optional. Implementer fixes on first try.

## What should fire

- Critic (A) — scope drift check on the diff
- Validate (B) — tests + lint + type check
- Fact-Check (D) — no rendered data, mock scan clean
- Report (F) — scorecard written

## What should NOT fire

- Iterate (no failures)
- Memory-first gate (debugger not installed)
- NavGator sub-steps (NavGator not installed)
- Optimize (no mechanical metric)
- Learn (no prior runs)
