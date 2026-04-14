---
name: model-tiering
description: Use when choosing which model to assign to a subagent, deciding sonnet vs opus in agent frontmatter, evaluating cheaper model sufficiency, or deciding whether to escalate to opus mid-flow.
---

# Model Tiering — Authoritative Reference for Build-Loop

Governs model selection across all build-loop phases. When `model:` appears in an agent frontmatter, or the orchestrator is spawning a subagent, consult this skill first.

## When to use this skill

- Choosing `model:` field in an agent frontmatter
- Orchestrator deciding which subagent model to spawn for a task
- Estimating cost tradeoffs before starting a build
- Deciding whether to escalate mid-flow after failures

## Evidence base (2026 Q1)

| Claim | Source | Certainty |
|-------|--------|-----------|
| Sonnet 4.6: 79.6% SWE-bench Verified | Anthropic announcement + SWE-bench leaderboard | ⚠️ T2, single-source |
| Opus 4.6: 80.8% SWE-bench Verified (1.2pt gap — smallest in Claude history) | Same | ⚠️ T2, single-source |
| Sonnet 4.6 uses 70% fewer tokens than 4.5 on complex file ops with +38% accuracy | Anthropic Sonnet 4.6 announcement | ⚠️ T2, single-source |
| Pricing: Sonnet $3/$15 per MTok input/output | Anthropic pricing page | ⚠️ verify before billing |
| Pricing: Opus $15/$75 per MTok input/output (5x gap) | Anthropic pricing page | ⚠️ verify before billing |

## Default assignments

| Task | Model | effort | Why |
|------|-------|--------|-----|
| Planning (Phase 2–3) | Opus | medium | Ambiguity resolution; wrong plan compounds across all subagents |
| Code execution — bounded chunk, spec clear | Sonnet | medium | Default. High accuracy, 5x cheaper |
| Code execution — ambiguous spec | Opus | medium | Interpretation cost cheaper than rework |
| Code review (final, pre-report) | Opus | medium | Judgment on correctness + tone; catches what Sonnet misses |
| Adversarial critic pass (separate read-only agent) | Sonnet | high | Separation drives quality, not model; Sonnet catches most issues |
| Mock data scanning (Phase 7B) | Haiku | low | Pattern matching only; no judgment needed |
| Fact-checking (Phase 7A) | Sonnet | medium | Trace values to sources; bounded reasoning |
| Debugging — first pass | Sonnet | high | effort=high before escalating; captures most regressions |
| Debugging — after 2 consecutive failures | Opus | high | Escalation trigger; Sonnet has exhausted straightforward paths |
| Novel architecture decision | Opus | medium | Cross-file impact; wrong call is expensive |
| Writing user-facing prose (copy, microcopy, errors) | Opus | medium | Tone, restraint, and nuance matter |

## Escalation triggers (stay on Sonnet UNLESS)

- 2 consecutive failures on the same chunk after a retry at effort=high
- Spec is ambiguous and interpretation will materially change implementation
- A cross-file architectural decision surfaces mid-execution that wasn't in the plan
- Critic flags a "strong-checkpoint" finding that requires judgment, not just a fix
- Novel error pattern not found in `.build-loop/issues/` or debugging memory
- Task produces user-visible prose where tone and restraint are load-bearing

## Techniques that work

- **Self-refine with external verification** (tests, lint, type-check). External oracle is non-negotiable — without it, self-refine is circular.
- **Adversarial critic loop** (writer agent + read-only reviewer agent). Separation is what makes it work. Same model reviewing its own output doesn't catch errors.
- **Best-of-N sampling with self-certainty voting** on HARD chunks only (flagged by plan or first-pass failure). N=3. Cost = ~3x Sonnet, still under 1.5x single-pass Opus.
- **Test-time compute** (effort=medium default, effort=high on retry). Easier problems benefit from revisions; harder problems need parallel sampling — not just more thinking on one path.
- **Plan-then-execute split** (Opus plans once, Sonnet executes many). Established pattern. Amortizes Opus cost across N subagent calls.

## Techniques to avoid

- **Multi-agent debate** (3+ agents argue toward consensus). ⚠️ ICLR 2025 MAD analysis shows majority voting captures most gains; debate adds cost without consistent wins. Use simple voting instead.
- **Self-critique without adversarial separation**. A model editing its own output won't reliably catch its own errors. Use a separate read-only reviewer or rely on external tests.
- **Chain of Density**. Summarization-specific technique; not applicable to code work.
- **Best-of-N by default**. Only on hard chunks. Blanket best-of-N wastes tokens on easy tasks where effort=high is sufficient and cheaper.

## Cost math quick reference

| Configuration | Relative cost |
|---------------|--------------|
| Single-pass Opus | 5x baseline |
| Single-pass Sonnet 4.6 | ~0.3x (70% fewer tokens) |
| Sonnet 4.6, effort=high | ~0.6x |
| Sonnet 4.6, best-of-3 + critic | ~1.2x |
| Sonnet 4.6 best-of-3 + critic vs single-pass Opus | ~4x cheaper |

❓ Best-of-N + critic vs single-pass Opus on SWE-bench has not been directly benchmarked.

## How the build-loop uses this

Orchestrator (Opus) spawns implementer subagent (Sonnet, effort=medium) → external verification gate (tests/lint/types) → Sonnet critic agent (read-only, effort=high) → if strong-checkpoint flagged, escalate to Opus for judgment call. See `agents/build-orchestrator.md §Escalation Triggers`.

Haiku is only used for Phase 7B mock scanning. Never for reasoning tasks.

## Pin vs inherit in agent frontmatter

Not every agent should hard-pin its model. Use this rule:

- **Pin** (`model: opus | sonnet | haiku`) when the task has a clear right tier and cost/quality drift from user's session choice would be a bug. Examples: `sonnet-critic` (the pin IS the point), `mock-scanner` (pattern matching only), `build-orchestrator` (Opus judgment at plan/review boundaries).
- **Inherit** (`model: inherit`) when user intent should flow through. The user's main-session choice is itself a cost/speed preference; respect it. Pair with a "recommended: X" note in this skill rather than forcing via frontmatter. Example: `fact-checker` — recommended Sonnet, but inherit honors whatever tier the user picked upstream.
- **Override mechanism**: users can override any pin by passing `model:` when spawning the agent or by editing the frontmatter. Pins are defaults, not locks.

Forward-compat note: pinned family aliases (`sonnet`, `opus`) auto-track latest versions (e.g., 4.6 → 4.7). `inherit` additionally picks up brand-new tiers (e.g., a future Flash-class model) without frontmatter edits.

## Limitations of this guidance

- ⚠️ Sonnet 4.6 token-efficiency claim is single-source (Anthropic announcement). Treat as directionally correct, not proven.
- ❓ Best-of-N + critic hasn't been tested against single-pass Opus on SWE-bench specifically.
- ⚠️ Escalation triggers are heuristics, not proven thresholds. Revise after observing 5+ real builds and logging outcomes to `.build-loop/memory/`.
