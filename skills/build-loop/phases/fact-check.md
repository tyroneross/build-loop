# Phase 7: Fact Check & Mock Scan — Detailed Guidance

Loaded on demand when entering Phase 7.

## Gate A: Fact Checker

| Check | What to Do |
|-------|-----------|
| **Rendered data** | Any %, $, score, count, assessment in UI — trace to data source. If a number appears on screen, where does it come from? |
| **Claims in code/comments** | Assertions about performance, accuracy, coverage — verified or marked ⚠️ UNVERIFIED |
| **Plan/spec claims** | What was "achieved" — cross-check against actual eval results from Phase 5 |
| **Extreme language** | Flag "always", "never", "100%", "guaranteed", "impossible", "all", "none" in code, UI copy, error messages, docs. Replace with accurate qualified language unless genuinely absolute |
| **Assessment integrity** | App displays quality scores, risk levels, health indicators? Verify scoring logic exists and produces the displayed value. No hardcoded "95%" without backing logic |
| **Source traceability** | Every rendered metric: data source → transformation → display. Missing link = flag it |

## Gate B: Mock Data Scanner

Scan production code paths for:
- Hardcoded fake data in display paths (names, emails, addresses, phone numbers, prices)
- Placeholder text (lorem ipsum, "TODO", "FIXME") in rendered output
- Fake metrics: hardcoded percentages, scores, or counts not derived from real computation
- Mock API responses left in production code (not test files)
- Fake semantic search, recommendation, chart, metric, summary, or comparison responses where users expect real data to make decisions
- `faker` library or `Math.random()` generating user-facing data
- Seed/fixture data rendering outside dev/test environments
- Commented-out real implementations replaced by stubs

**Scope**: Production code paths only. Test files, fixtures, and dev-only code are excluded.

## Resolution

- Blocking issues (fake data rendered to users or supporting user decisions, unverifiable claims) → route back to Phase 5 (Iterate)
- Warnings (TODO in comments, minor language issues) → include in Review-F report
