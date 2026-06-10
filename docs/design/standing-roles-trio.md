# Standing Roles: Orchestrator / Advisor / Judge

> **Status:** RECONCILED with research (2026-06-10). The deep-research pass
> (run `wf_5a528e5d-b6d`) search/fetch surfaced the canonical literature; its automated
> verification layer was rate-limited and did NOT adjudicate (the "all refuted" summary is
> a harness artifact, not a real refutation). Findings below rest on foundational sources
> corroborated from first-party knowledge; 2026 papers are marked as un-corroborated leads.
> See "Research findings" at the end.
>
> **Branch:** `experiment/advisor-judge-trio` (off `main` @ `3e50f44`). Kept separate
> from `main` deliberately for A/B testing against the current build-loop. This is an
> intentional long-lived experimental branch — NOT subject to collapse-to-main until
> the A/B result is in.

## Why this exists

build-loop's documented model org says **Fable (Frontier tier) plans and verifies; Opus
coordinates; Sonnet executes; Haiku does pattern work.** An assessment on 2026-06-10
(ran on Fable, read-only) confirmed the **planning half is documented-but-dormant**:
no Fable-pinned planner agent exists, both `build-orchestrator` and
`assessment-orchestrator` are `model: opus`, and a subagent cannot change its own model
mid-run — so Phase 1 Assess + Phase 2 Plan synthesis execute **inline on the
orchestrator's model (Opus in agent dispatch; the session model in `/build-loop:run`)**.
Fable touches plans only as a WARN-only reviewer *after* the plan exists.

A ChatGPT research PDF independently reproduced the false "Fable plans" claim by reading
the docs — demonstrating the gap misinforms any reader (human or AI).

This design fixes the gap by **separating the roles that are currently fused in one Opus
context**, and does so on an isolated branch so the change can be measured, not assumed
(the Fable-vs-Opus planning quality delta is currently unmeasured).

## The core principle that assigns models

**Generating is harder than evaluating.** This single rule decides the tier of every role:

- **Advising** is *generative* — "what should change, what's the new approach." Open-ended,
  no fixed answer; deepest reasoning pays here. → **Frontier (Fable).**
- **Judging** is *evaluative* — "does this meet the bar, pass/fail, on the rails." Bounded
  by criteria. → **Thinking (Opus)** for structural calls; **Frontier specialists** summoned
  for verdicts that compound.
- **Coordinating** is *routing* — "who runs next, is the verdict in." → **Thinking (Opus).**

The Advisor is the role that most needs Frontier — and is consulted *least often* (pull-based).
That is the whole cost strategy: **Frontier strength, summoned sparingly.**

## The three standing roles

| Role | Tier (Anthropic map) | What it does | Invocation |
|------|----------------------|--------------|------------|
| **Orchestrator** | Thinking / Opus (Frontier when run in a Frontier session) | Runs the 5 phases, single writer to `.git`, routes work, owns the ledger | Always on |
| **Advisor** | **Frontier / Fable** | Proactive check-ins at checkpoints; formative steering; re-plans on a wrong approach; takes over a task only after cheaper tiers provably fail; its own output is still verified | **Pull** — summoned at triggers, not every step |
| **Judge** | Thinking / Opus **+ summons Frontier specialists** | Gating + validation pass; keeps the run on rails | Push — gates every checkpoint |

Specialists (security-reviewer, fact-checker, independent-auditor, design-contract-specialist,
mock-scanner, …) are **the Judge's bench** — first-tier sub-agents dispatched on demand at
their own tiers, NOT standing roles. Keep the standing set at three.

### Orchestrator
Unchanged from today in role; stays Opus. "Sometimes Fable" is already literally true —
in a Frontier session (`/build-loop:run` while the session model is Fable) the orchestrator
*is* running on Fable. No change needed to make that happen; the ledger just records it.

### Advisor (the genuinely new role)
Today build-loop has only *reactive* escalation (Sonnet→Opus on failure) and *post-hoc*
Review critics. Nothing proactively asks "are we on the right track" mid-flight. The Advisor
is that missing role. It absorbs the remediation ladder from the design thread:

- **Steer** — on a *planning miss* (the plan itself no longer fits), the Advisor re-plans and
  issues **corrected instructions** (a diff vs the prior plan + the failure evidence), not just
  a "no." Execution resumes at the cheap tier on the new plan.
- **Take over** — on an *execution miss* (plan sound, task genuinely hard), escalate the
  executor Sonnet→Opus→**Advisor executes it directly**, last resort, earned by repeated failure.
- **Check in** — at checkpoints (chunk-close, repeated friction, drift signals) the Advisor can
  course-correct *before* a dead-end. Preventive, not just rescue. This keeps the autonomous
  loop moving (a pure block would stall it, violating the keep-going / no-idle-wait rules).
- **Always verified** — the take-over rung does not self-certify; independent-auditor / fix-critique
  (Frontier) check the Advisor's own output.

A pure gate that only says "no" is a dead-end; the Advisor's contract is **block → re-instruct →
and if it keeps failing, do it itself.**

### Judge
Split judging into two layers so the model assignment is honest:
- **Structural gating** (fields present, build green, scope respected, plan complete) → **Opus**.
- **Substantive verdicts** (is this security-sensitive code safe? is this fact grounded? is this
  decomposition sound?) → **Frontier specialists**, because a wrong "pass" ships a regression.

So the Judge is **an Opus head-judge that dispatches Frontier specialists for the calls that
compound.** Not all-Fable (over-pays for structural checks); not all-Opus (under-powers the
compounding verdicts).

## The escalation / remediation ladder (recorded, every rung)

| Rung | Who | Action | Trigger |
|------|-----|--------|---------|
| 0 | Sonnet (Code) | execute | default |
| 1 | Opus (Thinking) | execute | 2 fails at Sonnet |
| 2a | **Advisor (Fable)** | **re-plan** — new instructions | failure diagnosed as *planning miss* |
| 2b | **Advisor (Fable)** | **take over** — executes it | *execution miss*, still failing after Opus |
| 3 | Frontier critics | verify the takeover | any Frontier-authored output |

**Escalation trigger = objective verifier signal, NOT self-reported confidence** (research:
models/judges are systematically overconfident; intrinsic self-judgment is unreliable). The
ladder advances on: test/build failure, auditor verdict, retry-count, or scope-breach — never
"the model says it's unsure." **Diagnosis of planning-miss vs execution-miss** is itself a
Frontier judgment: made by the Advisor (or a quick Frontier call) reading the failure evidence
+ the diff against the plan, not by the failing executor.

## The Advisor dispatch ladder (how it's enforced without breaking)

Mirrors the existing GAP-1 auditor ladder (proven pattern for `independent-auditor`). Evaluated
only when **stakes-gating** trips (`synthesisDensity > 5`, `triggers.riskSurfaceChange`,
`stakes ≥ medium`, or explicit `dispatch_tier: frontier`):

- **Rung 0 — own context already Frontier** (Fable session): synthesize inline, it's already
  Fable. `advisor_status: inline-frontier`. Zero added cost.
- **Rung 1 — Agent tool present**: dispatch the Fable Advisor. `ran:dispatched-agent`.
- **Rung 2 — no Agent tool, peer reachable** (nested Mode B, Codex/rally up): peer process.
  `ran:peer-host(<host>)`.
- **Rung 3 — none**: synthesize inline on the orchestrator's own model (Opus), labeled honestly.
  `fallback:inline-opus`.

**Non-breaking guarantee:** Rung 3 *is* today's behavior. The floor equals current state; strictly
better whenever a dispatch path is reachable. The handoff cost fires *only* in Rung 1/2 — i.e.,
only when the active context isn't already Frontier — so you pay it exactly where it buys an upgrade
and never where planning is already Frontier.

**Research nuance — dispatch is a quality lever, not only cost.** Context-separation evidence
(⚠️ 2026 cross-context-review result, un-corroborated lead, directionally consistent with the
corroborated self-correction literature) indicates review/synthesis in a *fresh* context beats
same-session work. So Rungs 1/2 (fresh-context dispatch) are plausibly *higher quality* than
Rung 0/3 inline, not merely more expensive — inline is a genuine quality compromise, taken only
when dispatch is unreachable. For pure *verification* (the Judge's bench) prefer dispatch whenever
reachable.

## Enablers (small wiring changes)

1. **`dispatch_tier` enum** gains `frontier` (today: `script|haiku|sonnet|opus`). Touch
   `skills/spec-writing/SKILL.md` + the `_DISPATCH_TIER_RE` regex in `scripts/plan_verify.py`.
   Resolver already maps `frontier → fable` in `scripts/model_overrides.py`.
2. **`plan-critic` becomes gating** (not WARN-only) on the same stakes triggers — cheapest way
   to put a Frontier verdict on the plan path; already wired, just change the gate.
3. **Doc reconciliation** — the five sites that claim "Fable plans Phase 1/2" rewritten to describe
   the ladder. (`CLAUDE.md`, `docs/releases/v0.31.0.md`, `references/model-tier-mapping.md`,
   `skills/model-tiering/SKILL.md`, `agents/build-orchestrator.md`.)

## The agent-activity ledger (the instrument)

One append-only ledger every dispatch writes to — replaces today's scattered `state.json.escalations`
/ `judge-decisions.json` / `*_status` fields with a single joinable trail.

- **Where:** `.build-loop/agent-ledger.jsonl` (append-only JSONL — crash-safe, concurrency-safe,
  matches the "progress in JSON not markdown" rule).
- **Who writes:** the orchestrator, single writer (sub-agents return envelopes; orchestrator appends).
  Nested Mode B writes its slice; parent merges (same parent-owes pattern).
- **One line per agent-action:**
  ```
  ts · run_id · phase · chunk_id ·
  agent · tier · model(resolved id) ·
  action(author|execute|re-plan|take-over|verify|gate) ·
  rung(0–3) · status(pass|fail|blocked|partial|variance) ·
  trigger("2 fails@opus" | "riskSurfaceChange" | "planning-miss") ·
  refs(input plan / output commit) · note(failure evidence, why retry justified)
  ```
- **Unlocks:** answers "which model designed this plan / executed each chunk / where did the Advisor
  step in / how often did the fallback fire" at a glance; `*_status` fields become ledger rows (one
  source of truth); **closes the unmeasured quality-delta** — compare outcomes by rung/model to find
  whether Frontier planning actually pays; preserves failure evidence by contract.

## Provider-agnosticism

Everything is **roles × tiers**, never model names. Advisor = Frontier; Orchestrator/Judge = Thinking;
bench = Code/Pattern. The router (`model_overrides.py`) resolves tier→model per host: Frontier→Fable
(Claude) / GPT-5.x (OpenAI) / top-model-elsewhere; Pattern→Haiku or GPT-Nano. The design doesn't change
when the provider does — only the resolution table does.

## What's different from before (outcome view)

| Question | Before — who does what | After — who does what |
|----------|------------------------|------------------------|
| Who designs the plan? | Orchestrator designs it as a side-job of coordinating | A dedicated Advisor designs it and hands it back; orchestrator requests + executes |
| What model designs it? | Opus (or session model); never guaranteed Fable | Fable on high-stakes plans (guaranteed by ladder) or honestly labeled fallback |
| Can a plan request a Fable step? | No — `frontier` unwritable in `dispatch_tier` | Yes — `dispatch_tier: frontier`, routed to Fable |
| Frontier approval before code? | Reads but can't block (plan-critic WARN-only) | Can block on high-stakes plans, before any implementer runs |
| On repeated failure? | Ladder dead-ends at Opus; run stalls/ships partial | Advisor re-instructs (planning miss) or takes over (execution miss) |
| Who did what, recorded? | Scattered, unjoinable | One append-only ledger, every action, with model + rung + outcome |

## Decisions (settled, research-grounded)

1. **v1 scope** = the foundational, A/B-able core: the **ledger** (instrument first), the **Advisor
   agent (Fable) + dispatch ladder** for Phase 2 plan synthesis, the **`dispatch_tier: frontier`**
   enum, **plan-critic gating** on stakes triggers, and **doc reconciliation**. The full
   **take-over-execution rung (2b)** and proactive checkpoint check-ins are **v2** — land after the
   A/B confirms the core pays. (Keeps the first build's blast radius bounded; respects the
   multi-agent-overhead risk by not decomposing more than needed until measured.)
2. **Diagnosis (planning-miss vs execution-miss)** = the Advisor's call (Frontier), reading failure
   evidence + diff-vs-plan. Never the failing executor's self-report.
3. **Escalation trigger** = objective verifier signal (test/build failure, auditor verdict,
   retry-count, scope-breach). NOT self-reported confidence (overconfidence is documented).
4. **Judge stays a multi-specialist panel** for compounding verdicts (security / factuality / plan
   soundness), with chain-of-thought, not a single judge — single-judge bias + adversarial-injection
   vulnerability is documented. Raw judge confidence is not a gate.
5. **Ledger first** — it's the instrument that makes the A/B measurable.

## Risks (from the contrarian evidence — surfaced, not buried)

- **Multi-agent handoff failure** is the real risk (Cognition "Don't Build Multi-Agents"; MAST
  failure taxonomy, arXiv 2503.13657). Systems fail at handoffs via context loss / conflicting
  decisions; a single full-context agent often beats a fragmented one. **Mitigations (in design):**
  stakes-gating (most runs stay single-context), file-based artifact handoffs (plan doc + ledger,
  not lossy summaries), inline fallback as the single-context default, and the ledger making every
  handoff observable. The design sits on the **orchestrator-workers** side of the line Anthropic
  endorses for multi-file coding — but handoff discipline is make-or-break. **The A/B test is the
  resolution**: confirm net benefit vs the current single-context build, don't assume it.
- **The quality delta of Frontier planning remains the open empirical question** — the ledger is how
  the A/B answers it (compare outcomes by rung/model).

## Research findings (sources)

Search/fetch surfaced 23 sources (deep-research `wf_5a528e5d-b6d`); automated verification was
rate-limited (did not adjudicate). Confidence marked per source class.

**Corroborated from first-party knowledge (✅ foundational):**
- Self-correction needs *external* feedback; intrinsic self-correction *decreases* reasoning
  accuracy — Huang et al., "LLMs Cannot Self-Correct Reasoning Yet" (ICLR 2024, OpenReview
  PAFEQQtDf8); Kamoi et al., MIT TACL "When Can LLMs Actually Correct Their Own Mistakes."
- Tool/external-grounded correction works — Gou et al., CRITIC (arXiv 2305.11738).
- LLM-as-judge: ~80% human agreement but position/verbosity/self-enhancement bias — Zheng et al.,
  MT-Bench (arXiv 2306.05685).
- Cascades cut cost at equal accuracy — FrugalGPT (arXiv 2305.05176); RouteLLM (LMSYS 2024).
- Orchestrator-workers endorsed for dynamic multi-file coding — Anthropic, "Building Effective
  Agents"; "Multi-Agent Research System."
- Multi-agent failure taxonomy — "Why Do Multi-Agent LLM Systems Fail?" (MAST, arXiv 2503.13657);
  Cognition, "Don't Build Multi-Agents."

**Un-corroborated leads (⚠️ 2025–2026, specific stats unverified by me):**
- Self-critique −1.8…−5.1% vs cross-critique +30–40%; stronger reasoner = better critic (o1-mini
  88.9% vs Qwen2.5-72B 61.8% F1) — arXiv 2501.14492.
- Cross-context (fresh-session) review > same-session review, widest on Critical errors —
  arXiv 2603.12123.
- Judge overconfidence; calibrated confidence enables tiered routing — arXiv 2512.22245.
- Judge biases survey + multi-judge aggregation — arXiv 2411.16594; MT-Bench judge agreement
  58.8–65.2%, CoT best debias — arXiv 2604.23178.

> Re-running the verification layer (off-peak, to dodge the rate limit) would upgrade the ⚠️ leads;
> not blocking for the build since the core decisions rest on the ✅ corroborated set.
