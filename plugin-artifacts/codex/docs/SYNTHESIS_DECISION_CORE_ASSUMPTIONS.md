# Synthesis Decision Core Assumptions

## Bottom Line

The recommended build-loop policy assumes Sonnet/code-tier execution is valuable for speed, but unsafe as the sole decision-maker for dense or high-consequence synthesis. More Sonnet usage is possible without expected quality loss only when the work is bounded by a thinking-tier plan, deterministic checks, runtime validation, or a measured calibration gate.

## Terminology Assumptions

- "Sonnet" means the code-tier implementer lane: fast, scoped execution once the "what" is already settled.
- "Thinking-tier" means the synthesis lane: planning, ambiguity resolution, cross-file judgment, risk routing, and final high-consequence decisions.
- "Quality gap" means missed synthesis or integration decisions, not merely bad code style.
- "No degradation" means no expected increase in shipped defects or silent decision drift after Review gates run. It does not mean every individual Sonnet draft is as complete as thinking-tier reasoning.

## Evidence Assumptions

- The per-commit experiment table is accurate enough for directional policy, even though it is not provider billing data.
- The observed savings are real: Sonnet/fan-out saved about 28% tokens and 33% wall-clock versus the alpha/thinking baseline.
- The observed recall gap is also real: beta/code-tier surfaced 9 of alpha's 22-23 novel decisions, or about 39-41%.
- The failure mode is mostly decision recognition: code-tier often does not realize a synthesis decision exists.
- Dense architectural commits are the highest-risk zone. C5 and C6 are the strongest signal because beta surfaced 0 novel decisions where alpha surfaced 5 and 4.
- Current sample size is small enough that thresholds should be treated as operational defaults, not universal laws.

## Build-Loop Design Assumptions

- Keep one hybrid policy instead of separate "cheap" and "quality" workflows.
- Use thinking-tier where decisions are dense, ambiguous, architectural, irreversible, security-sensitive, or user-trust-sensitive.
- Use Sonnet/code-tier where the task is bounded, the interface is stable, and the plan has made the synthesis choices explicit.
- Do not rely on implementer honesty alone. Every implementer attestation needs an independent check when the claim can be verified.
- Treat runtime behavior as a separate quality surface. Good planning and attestation do not prove that HTTP, SSE, browser, or UI flows work live.
- Treat Codex and Claude Code as separate host shells over the same method. Claude behavior should remain unchanged while Codex gets additive adapters and packaging checks.

## Can Sonnet Use Increase Without Quality Degradation?

Yes, but only by moving more bounded execution to Sonnet while keeping decision authority and failure detection outside Sonnet.

Good candidates for more Sonnet:

- Mechanical implementation of a thinking-tier plan.
- Tests and fixtures derived from explicit acceptance criteria.
- Static scans, mock-data scans, and bounded code critics.
- Refactors where public interfaces do not change.
- Optimization loops with a mechanical metric.
- Evidence gathering, caller listing, and codebase fact packets for a thinking-tier decision.
- Draft implementations followed by deterministic validation and, for high-risk surfaces, thinking-tier review.

Bad candidates for more Sonnet:

- Choosing architecture boundaries.
- Deciding where a new protocol, persistence contract, or validation layer lives.
- Interpreting vague product intent.
- Security, auth, deployment, billing, data-loss, or user-trust-sensitive changes.
- UI/UX decisions where placement, hierarchy, and copy tone were not locked in the plan.
- Any change with unenumerated synthesis decisions and no reliable runtime/test backstop.

## Safe Ways To Increase Sonnet Share

### 1. Add A Pre-Dispatch Eligibility Gate

Route to Sonnet only when all are true:

- `synthesis_dimensions_count <= 5`.
- No `risk_reason` is present.
- No `modifies_api` scope gap remains.
- The plan has concrete claimed values, not vague values.
- The implementer brief names files owned, files not owned, interface contract, validation, and decision ledger.
- Existing tests or a runtime smoke can verify the changed behavior.

If any condition fails, use thinking-tier for the affected plan or chunk.

### 2. Use Sonnet For Drafting, Not Final Authority

Let Sonnet produce the first implementation or evidence packet, then use deterministic gates and targeted thinking-tier review for high-risk claims.

This increases Sonnet's share of useful work without letting it silently own the synthesis decision.

### 3. Make Runtime Smoke A Sonnet Enabler

More Sonnet is safer when live behavior is checked programmatically. A runtime smoke gate should trigger on:

- request handlers
- SSE or streaming events
- browser event consumers
- embedded HTML or JS
- routing middleware
- server modules

Without that live gate, shifting runtime-protocol work to Sonnet is likely to increase missed defects.

### 4. Split Synthesis From Execution More Aggressively

Have thinking-tier produce:

- decision ledger
- alternatives rejected
- caller-scope audit
- locked interface contract
- validation plan

Then let Sonnet implement within that box.

This is the cleanest way to increase Sonnet usage without asking Sonnet to become better at recognizing hidden decisions.

### 5. Use Two Code-Tier Checks Only For Low-Risk Work

A Sonnet implementer plus independent Sonnet critic can improve low-risk coverage cheaply. It should not replace thinking-tier review for dense or high-consequence synthesis because both code-tier passes can share the same blind spot: failing to notice that a decision exists.

### 6. Create A Calibration Gate Before Expanding Defaults

Before lowering the thinking-tier threshold or moving more commits to Sonnet by default, require a fixture suite:

- 8-12 representative commits.
- Known expected novel decisions.
- Equal briefs for thinking-tier and Sonnet/code-tier.
- Metrics for missed decisions, false-positive blocks, runtime defects, and post-review escapes.

Increase Sonnet defaults only for categories where the suite shows no regression after Review gates.

## What Not To Do

- Do not route all `synthesis_dimensions >= 1` work to thinking-tier by default; it throws away the measured speed lane.
- Do not route all `synthesis_dimensions <= 5` work to Sonnet blindly; low-count work can still be high consequence.
- Do not let Sonnet classify its own safety without deterministic preconditions.
- Do not treat attestation lint as complete coverage; it verifies only dimensions that can be observed in the diff.
- Do not treat Codex worker support as equivalent to Claude `Agent(...)` fan-out. Codex requires explicit user authorization for subagents.

## Decision Rule

Increase Sonnet use where the task is bounded, the synthesis choices are explicit, and independent gates can catch drift. Do not increase Sonnet use where the task requires noticing hidden decisions, resolving architecture, or validating live runtime behavior without a programmatic smoke test.
