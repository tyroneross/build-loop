# Scenario 6: Recurring bug, debugger absent (exercises `fallbacks.md#bug-memory`)

## Setup

- **Project**: Node.js backend, no claude-code-debugger, no NavGator, no IBR
- **Prior state**: `.build-loop/issues/2026-03-18-redis-reconnect.md` exists from a prior build, recording a Redis connection reset bug with fix notes
- **Goal**: "Add a batch-process job that pushes to Redis in a loop"
- **Criteria**: standard tests + lint + type

## First failure (during Review-B Validate)

Integration test fails: `Error: Connection is closed` when the batch hits 50 items. Same error class as the prior recorded bug, different call site.

## Pre-fallback behavior

**Review-B memory-first gate**:
- `availablePlugins.claudeCodeDebugger` is false
- debugger-bridge Pre-flight: "Debugger memory: not installed. Using inline debug fallback." → skip with generic message
- Verdict: none — falls through to standard Iterate with no memory context
- Orchestrator begins from scratch: reproduce, isolate, hypothesize
- Eventually rediscovers the same Redis-disconnect root cause. Cost: 2-3 Iterate attempts, ~6-10 min wall clock.

## Post-fallback behavior

**Review-B memory-first gate**:
- debugger-bridge Pre-flight: runs `fallbacks.md#bug-memory`
- Extracts tokens from symptom: `Error`, `Connection`, `closed`, `batch`, `Redis`
- Greps `.build-loop/issues/`, `feedback.md`, `.bookmark/` for each token
- `.build-loop/issues/2026-03-18-redis-reconnect.md` matches 4 tokens (`Error`, `Connection`, `closed`, `Redis`)
- Verdict: `LOCAL_HIT_PARTIAL` (≥2 tokens co-occur in the same file)
- Orchestrator reads the prior issue file: includes a recorded fix (add keep-alive config, retry wrapper)
- Iterate plan: adapt the prior fix to this call site. No direct-apply (the new call path is different), but informed starting point.
- Iterate attempt 1 succeeds on first try.

## Concrete delta

| Aspect | Pre-fallback | Post-fallback |
|---|---|---|
| Memory lookup | Disabled | Enabled via local file grep |
| Verdict granularity | None | 4 states mirroring upstream shape |
| Iterate attempts to resolve | 2-3 | 1 |
| Cross-session learning | None even within this project | Yes, per-project (no cross-project) |
| Prior fix notes surfaced | No — rediscovered from scratch | Yes — read and adapted |
| Install debugger? | Strong recommend for cross-project | Still recommend for classifier + cross-project memory |

**Net**: fallback cuts recurring-bug resolution time roughly in half on projects with any prior `.build-loop/issues/` history. The upstream debugger adds cross-project memory and a classifier; the fallback has neither but captures most of the per-project value.

## Where the fallback gives up

When the project has no prior `.build-loop/issues/` files, there's nothing to grep. Fallback returns `LOCAL_NO_MATCH` and orchestrator proceeds normally. This is correct behavior — no false-positive reuse of unrelated prior issues.
