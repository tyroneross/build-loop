<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Advisor dispatch ladder (Phase 2 plan synthesis at Frontier)

The Advisor (Fable) is the Frontier standing role that **authors and re-plans** the Phase 2 plan synthesis. Generating a plan is harder than evaluating one, so the deepest reasoning pays here — but the Advisor is **pull-based**: summoned only when stakes-gating trips, not every run. This ladder enforces that the plan is authored at Frontier *when it matters*, without breaking the runs where it doesn't.

It deliberately **mirrors the GAP-1 independent-auditor ladder** (`phase-4-review.md` §"Sub-step A"). Same shape, same honesty discipline, an analogous `advisor_status` field. If you know that ladder, you know this one.

## When the ladder is evaluated (stakes-gating)

The orchestrator walks this ladder at the Phase 2 plan-synthesis step **only when** any of these objective triggers fire:

- `state.json.synthesisDensity > 5` (synthesis-dense plan), or
- `triggers.riskSurfaceChange` (security/persistence/runtime/deploy/trust boundary), or
- `stakes >= medium` (from the commander's-intent posture / charter), or
- an explicit `dispatch_tier: frontier` on any work item in the draft plan.

If none trips, the ladder is skipped entirely — the orchestrator synthesizes the plan inline as today (most runs stay single-context; this respects the multi-agent-handoff risk by not decomposing more than needed). **Triggers are objective signals, never self-reported confidence** (models are systematically overconfident).

## The four rungs (record `advisor_status` honestly)

Dispatching the Advisor via `Agent(subagent_type="build-loop:advisor")` requires the Agent tool. A *nested* orchestrator (dispatched as a subagent — Mode B — or running per-commit mode) does **not** have the Agent tool, because the harness blocks sub-subagents. Walk the ladder and record `advisor_status`:

1. **Rung 0 — own context already Frontier** (the orchestrator is itself running on Fable, e.g. `/build-loop:run` while the session model is Fable): **synthesize the plan inline** — it is *already* Frontier, no handoff needed. → `advisor_status: inline-frontier`. Zero added cost.

2. **Rung 1 — Agent tool present** (top-level / Mode A, own context not already Frontier): **dispatch the Fable Advisor** (`Agent(subagent_type="build-loop:advisor")`) to author/re-plan, then verify via plan-critic + scope-auditor. → `advisor_status: ran:dispatched-agent`.

3. **Rung 2 — no Agent tool, peer host reachable** (nested Mode B, but a peer host — rally channel / `codex exec` — can execute; reachable because the orchestrator retains Bash even when nested): **run the Advisor as a peer process** over the same channel the cross-vendor reviewer uses. Reconcile the peer's plan + envelope into the plan artifact. → `advisor_status: ran:peer-host(<host>)`. Prefer this over the fallback whenever a peer host can execute (fresh-context synthesis is plausibly *higher quality*, not merely cheaper — see "Quality, not only cost" below).

4. **Rung 3 — none reachable** (no Agent tool, no peer host): **synthesize inline on the orchestrator's own model (Opus)**, labeled honestly. → `advisor_status: fallback:inline-opus`.

## Non-breaking guarantee

**Rung 3 IS today's behavior** — the orchestrator synthesizing the plan inline on Opus. The floor of this ladder equals the current state; it is strictly better whenever a dispatch path (Rung 1/2) or an already-Frontier context (Rung 0) is reachable. The handoff cost fires *only* in Rung 1/2 — i.e., only when the active context isn't already Frontier — so you pay it exactly where it buys an upgrade and never where planning is already Frontier. Worst case equals current; there is no regression path.

## Quality, not only cost

Dispatch is a **quality lever**, not just a cost knob. Context-separation evidence (⚠️ 2026 cross-context-review lead, directionally consistent with the corroborated self-correction literature: correction needs *external* feedback; intrinsic self-correction degrades accuracy) indicates synthesis in a *fresh* context beats same-session work. So Rungs 1/2 (fresh-context dispatch) are plausibly *higher quality* than Rung 0/3 inline — inline is a genuine quality compromise, taken only when dispatch is unreachable. The Advisor is a **separate agent**, never the executor self-reflecting.

## Ledger row per Advisor action (the instrument)

The orchestrator (single writer) appends one row to `.build-loop/agent-ledger.jsonl` per Advisor action via `scripts/agent_ledger.py`:

```
action: author | re-plan
agent: advisor · tier: frontier · model: <resolved id (fable / gpt-5.x / …)>
rung: 0|1|2|3 · status: pass|fail|partial · trigger: <synthesisDensity>5 | riskSurfaceChange | stakes>=medium | dispatch_tier:frontier>
refs: {output: docs/plans/<slug>.md, input: <prior plan / failure evidence>}
note: <on re-plan: failure evidence + why a retry is justified>
```

This closes the unmeasured quality-delta: the A/B test reads the ledger to compare plan outcomes by `rung`/`model` and find whether Frontier planning actually pays.

## Re-plan mode (the remediation contract, v1)

When the orchestrator diagnoses a **planning miss** during Iterate (the plan itself no longer fits, on an objective signal — test/build failure, auditor verdict, retry-count, scope breach), it re-enters this ladder with `action: re-plan`. The Advisor reads the **failure evidence + the diff vs the current plan**, diagnoses planning-miss vs execution-miss (*its* Frontier call, never the failing executor's self-report), and on a planning miss issues **corrected instructions** — a diff against the prior plan + the evidence — so execution resumes at the cheap tier on a sound plan.

**v1 scope:** author + re-plan only. The **take-over-execution rung** (the Advisor executing a chunk directly) and **proactive mid-run checkpoint check-ins** are **v2** — land after the A/B confirms the core pays.

## Always verified — never self-certifying

The Advisor's authored/re-planned plan is checked by the existing Frontier critics before any implementer runs: `plan_verify.py` (deterministic) → `plan-critic` (reasoning; **blocking on the same stakes triggers**, advisory otherwise) → `scope-auditor` at the Plan→Execute boundary. Frontier-authored output does not get a pass on review. Keep the verification panel multi-specialist — do not collapse it to a single judge.
