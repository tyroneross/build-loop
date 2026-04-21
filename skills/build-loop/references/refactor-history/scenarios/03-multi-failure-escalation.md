# Scenario 3: Multi-failure stuck iteration with logging-tracer rescue

## Setup

- **Project**: Node.js backend + NavGator installed + claude-code-debugger installed
- **Goal**: "Add rate limiting to `/api/search` endpoint — 100 req/min per IP, Redis-backed"
- **Scope**: 4 files, ~200 lines, touches middleware, Redis client, tests
- **Criteria**:
  1. Integration tests pass
  2. Rate-limit correctness (custom assertion: burst of 101 requests → first 100 succeed, 101st returns 429)
  3. Lint/type clean
  4. NavGator rules pass (no new layer violations)

## Expected failure trajectory

**First Review:**
- Critic (A) clean
- Validate (B): criterion 2 (rate-limit correctness) FAILS with test output: `assertion failed: expected 429, got 500`. No stack trace, no error message — the server returned 500 but the test didn't capture the cause. Memory-first gate synthesizes "500 on 101st request, no stack". `read_logs` MCP returns 0 entries (project is silent — `console.log` only). `evidence_gap: true` flagged.
- Fact-Check (D) and later sub-steps skipped due to B fail
- Route to Iterate

**Iterate attempt 1:**
- Debugger-bridge Iterate sees `evidence_gap: true` from previous attempt
- Invokes logging-tracer-bridge with `{phase: "iterate", action: "repair"}`. Ephemeral mechanism A: wraps new `trace(...)` calls in the Redis client behind `DEBUG_TRACE=1` env gate.
- Re-runs criterion 2 with `DEBUG_TRACE=1 npm test`. Now stderr captures: `Redis connection dropped after 98 ops, reconnect latency > 1s, causes burst to fail at 98 not 100`.
- Now a real root cause. Fix plan: add connection keep-alive + retry wrapper.
- Execute fix.

**Second Review:**
- Validate: criterion 2 now passes. But criterion 3 (lint) fails — the retry wrapper introduced `any` types.
- Route to Iterate.

**Iterate attempt 2:**
- Same criterion? No, different (lint vs rate-limit). No debugger escalation triggered (not 2 same-root-cause).
- Fix types.
- Execute.

**Third Review:**
- Validate all pass.
- Optimize (C): has mechanical metric (test runtime), runs 3-5 iterations. One win: -12% test time after connection pooling tuned.
- Fact-Check (D): NavGator rules check — new `database-isolation` violation? No, Redis already in allowed db layer. Clean.
- Simplify (E): remove an unused retry-count parameter.
- Report (F): scorecard PASS with notes. Debugger `store` called for the Redis burst bug. Logging-tracer instrumentation reverted per "ephemeral by default" (no user approval sought to keep). NavGator `dead` orphan scan: 1 new resolved orphan (the keep-alive wrapper is now wired in).

## What should fire vs NOT

**Fires:**
- Critic, Validate, Fact-Check, Simplify, Report across two final-Review passes
- Logging-tracer repair (evidence_gap trigger)
- NavGator sub-steps (Assess blast-radius + Review-D rules + Report dead scan)
- Debugger gate + store; outcome N/A (no prior KNOWN_FIX applied)
- Optimize (C) — mechanical metric exists
- 2 Iterate attempts

**Does NOT fire:**
- Parallel `/assess` domain assessors (not 2+ same-root-cause failures on one criterion)
- `debug-loop` causal-tree (not 3+ same-criterion failures)
- Learn (skipped unless `runs[] >= 3`)
