# Synthesis Decision Experiment Guide

## Bottom Line

Use a hybrid execution policy:

- Default to code-tier fan-out for low synthesis-density work.
- Escalate to thinking-tier execution when a plan has more than 5 `synthesis_dimensions` entries, or when the user or plan explicitly requests `tier: thinking`.
- Keep the C3-C5 backstops on every synthesis-bearing implementation: deterministic attestation lint, subjective synthesis critic, and halt-and-ask for architectural novel decisions.
- Add a separate runtime smoke gate for live HTTP/SSE/UI surfaces. The synthesis-decision machinery improves planning and delegation, but it does not replace live end-to-end validation.

The experiment supports the current `> 5` routing rule as the pragmatic default. Sonnet/fan-out is materially faster, but it misses too many synthesis decisions on dense or architectural work. Thinking-tier execution should be reserved for the work shapes where that miss rate is most expensive.

## Evidence Source And Limits

Primary numeric source:

- `<research-root>/topics/synthesis-decision-delegation/experiment-2026-05-07/metrics.md`

Related source notes:

- `CONTINUATION.md`
- `HANDOFF_IMPROVEMENT_PLAN.md`
- `C3-spec.md`
- `C4-spec.md`
- `C5-spec.md`
- `C6-spec.md`

Memory confirms this experiment was ingested as one bundle-level source with direct external `raw_ref` entries, but the accessible numeric evidence I found is the recorded per-commit metrics table, not provider billing logs or full transcript token accounting. So the calculations below are independently verified against the experiment packet's per-commit rows, not against an external billing export.

## Recalculated Metrics

### Per-Commit Inputs

| Commit | Alpha tokens | Beta tokens | Alpha wall-clock | Beta wall-clock | Alpha novel decisions | Beta novel decisions |
|---|---:|---:|---:|---:|---:|---:|
| C1 | 105,524 | 78,365 | 540s | 420s | 4 | 3 |
| C2 | 58,831 | 43,382 | 612s | 180s | 1 | 0 |
| C3 | 80,298 | 65,892 | 260s | 252s | 4-5 | 3 |
| C4 | 50,507 | 37,455 | 127s | 140s | 4 | 3 |
| C5 | 75,691 | 44,735 | 254s | 182s | 5 | 0 |
| C6 | 74,206 | 48,686 | 162s | 134s | 4 | 0 |

### Token Calculation

Alpha total:

```text
105,524 + 58,831 + 80,298 + 50,507 + 75,691 + 74,206 = 445,057
```

Beta total:

```text
78,365 + 43,382 + 65,892 + 37,455 + 44,735 + 48,686 = 318,515
```

Savings:

```text
445,057 - 318,515 = 126,542 tokens saved
126,542 / 445,057 = 28.43% saved
318,515 / 445,057 = 71.57% beta token share
```

Conclusion: the recorded "28% token savings" claim is arithmetically correct when rounded.

### Wall-Clock Calculation

Alpha total:

```text
540 + 612 + 260 + 127 + 254 + 162 = 1,955s = 32.58 min
```

Beta total:

```text
420 + 180 + 252 + 140 + 182 + 134 = 1,308s = 21.80 min
```

Savings:

```text
1,955 - 1,308 = 647s saved
647 / 1,955 = 33.09% saved
1,308 / 1,955 = 66.91% beta wall-clock share
```

Conclusion: the recorded "33% wall-clock savings" claim is arithmetically correct when rounded.

### Novel-Decision Recall Calculation

Alpha novel decisions:

```text
C1 4 + C2 1 + C3 4-5 + C4 4 + C5 5 + C6 4 = 22-23
```

Beta novel decisions:

```text
C1 3 + C2 0 + C3 3 + C4 3 + C5 0 + C6 0 = 9
```

Beta recall:

```text
9 / 22 = 40.91%
9 / 23 = 39.13%
```

Beta miss rate:

```text
13 / 22 = 59.09%
14 / 23 = 60.87%
```

Conclusion: "beta catches about 40%" and "beta misses about 60%" are both supported. The exact value depends on whether C3 is counted as 4 or 5 alpha decisions.

## What The Experiment Actually Shows

### 1. Speed Savings Are Real

Beta/fan-out used fewer tokens and less wall-clock time:

- 28.43% token savings.
- 33.09% wall-clock savings.

That is enough to preserve fan-out as the default for low-density work. Removing the speed lane would discard a meaningful advantage.

### 2. Quality Loss Is Also Real

Beta surfaced only 9 of alpha's 22-23 novel synthesis decisions. The gap is worst on architectural-density commits:

- C5: alpha surfaced 5, beta surfaced 0.
- C6: alpha surfaced 4, beta surfaced 0.

That matters because C5 and C6 are exactly the kinds of changes where silent decisions can damage the orchestrator contract.

### 3. The Failure Mode Is Decision Recognition, Not Just Wrong Decisions

The most important pattern is not "beta makes bad calls." It is "beta often does not recognize that a call exists."

That is why C5 is necessary but insufficient. Halt-and-ask only works when the implementer notices a missing synthesis decision. Dense architectural work still needs thinking-tier routing.

### 4. Scaffolding Helps, But It Does Not Close The Tier Gap

The alpha2 follow-up suggests scaffolding improves thinking-tier output and auditability:

- Alpha2 surfaced 4 novel decisions on one S-sized task.
- Alpha2-2 surfaced 7 novel decisions on another S-M task.
- The packet estimates alpha2 at 4-7 novels per S-M task versus beta at about 1-2.

This strengthens the recommendation to use good scaffolding for both tiers. It does not justify relying on code-tier fan-out for architectural synthesis.

## Current Build-Loop Alignment

The latest `build-loop` appears aligned with the experiment in these areas:

- `synthesis_dimensions` planning contract.
- `plan_verify.py` vague-value lint.
- Shared parser via `iter_synthesis_dimension_entries()` and `count_synthesis_dimensions()`.
- `route_decision.py` as a deterministic routing helper.
- `attestation_lint.py` for deterministic diff-vs-claim checks.
- `synthesis-critic` for subjective UI synthesis dimensions.
- `status: blocked` / `novel_decisions` halt-and-ask backstop.
- `> 5` density threshold as the practical speed/depth compromise.

One documentation caveat: older experiment text says `synthesis_dimensions >= 1` should route to thinking-tier. The current `> 5` rule is more operationally balanced and should be treated as the superseding policy unless you intentionally choose a quality-first mode.

## Recommendations

### Recommendation 1: Keep The Current `> 5` Density Rule

Why:

- It preserves beta's measured speed advantage for low-density work.
- It escalates before the failure mode becomes most dangerous.
- It matches the C5/C6 evidence that architectural-density work is where beta's recall collapses.

Implementation rule:

```text
if explicit thinking override:
  route to thinking-tier
elif synthesis_dimensions_count > 5:
  route to thinking-tier
else:
  route to code-tier fan-out with C3-C5 backstops
```

### Recommendation 2: Add A Quality-First Override

Why:

Some work has low dimension count but high consequence. Examples: security boundaries, persistence contracts, runtime protocol semantics, deployment/release behavior, and user-visible trust claims.

Add or document a first-class override:

```yaml
tier: thinking
reason: "quality-critical despite low synthesis dimension count"
```

This avoids overloading the numeric threshold with risk semantics.

### Recommendation 3: Strengthen Implementer Briefs With Decision Ownership

Why:

The handoff review found that decision-ownership gaps were left for implementers to decide. That is the same recursive failure the experiment is meant to prevent.

Every synthesis-heavy brief should include:

- Decision name.
- Owner.
- Locked value.
- Alternatives rejected.
- What to do if the implementer finds a new decision.

### Recommendation 4: Add Runtime Smoke Validation For Live HTTP/SSE/UI Surfaces

Why:

The memory store records a separate validation failure: many passing tests still missed live server/UI behavior. That is not solved by better synthesis routing.

Trigger the gate when changed files include request handlers, SSE/event taxonomies, embedded HTML/JS, browser UI handlers, or server modules.

Minimum viable check:

```text
start local server
curl -sN --max-time 5 <live endpoint>
extract emitted event types or key response markers
compare against UI/event handlers in the diff
fail Phase 4 Validate if emitted events are unhandled
```

### Recommendation 5: Re-Run One Real UI Placement Test

Why:

The experiment notes say C1-C6 were not validated against a real UI commit. That remains the most important methodology gap.

Good candidate task:

- A concrete UI placement decision.
- A copy-tone or empty-state requirement.
- A route/component where incorrect placement is visible.
- One alpha2/thinking run and one beta/fan-out run under equal scaffolding.

Pass condition:

- The implementation places the element correctly.
- C3/C4 catch any attestation drift.
- C5 catches any unenumerated architectural decision.
- A screenshot or headless UI check confirms the user-visible result.

## Suggested Next Changes To Build-Loop

1. Add a `runtimeSurface` trigger in Phase 1.
2. Add a Phase 4.B live-smoke gate when `runtimeSurface == true`.
3. Add a `tier: thinking` override example to the plan templates.
4. Add a small `scripts/test_route_decision.py` wrapper if `route_decision.py --self-test` is not enough for CI visibility.
5. Update experiment docs or README wording to say the current superseding routing policy is `> 5`, not `>= 1`.

## Decision Rule

Use code-tier fan-out when speed matters and synthesis density is low. Use thinking-tier when decisions are dense, architectural, irreversible, security-sensitive, or user-trust-sensitive. Always validate live runtime behavior separately from plan/delegation quality.
