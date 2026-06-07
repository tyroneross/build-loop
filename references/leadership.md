<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Leadership — operating doctrine for initiative + decision-escalation ladder

Loaded on demand from `agents/build-orchestrator.md` §"Keep going until done" and `references/keep-going-policy.md`. Consult whenever a turn involves ambiguity, an unforced choice, or a "should I ask?" impulse.

## Decide-at-70% rule

When confidence in the right next step is ≥70%, decide and execute; surface the reasoning and the chosen option in the readback. Below 70%, take one of the cheap escalation steps below before deciding. The threshold is calibrated, not literal — if the cost of being wrong is contained (reversible, low-blast-radius, recoverable in <1 chunk), decide earlier; if the cost is unbounded or production-impacting, climb the ladder regardless of confidence.

## Decision-escalation ladder (cheapest → costliest)

Try each step in order. Stop at the first that resolves the uncertainty. Never skip down to "ask the human" while a cheaper step exists.

1. **Self-research** — read the relevant code, docs, and recent commits. Most uncertainty dissolves on contact with the actual state of the system.
2. **Memory** — `scripts/memory_facade.py recall(query=...)` against `build-loop-memory/projects/<slug>/` and the durable lanes. Past decisions, feedback, and lessons frequently answer the question directly.
3. **Peers** — when a peer session is active in the Rally channel, post a coordination question via `scripts/rally_point/post.py` and continue parallel work while awaiting reply (do not idle-wait). Use the same channel discovery as the rest of multi-session coordination.
4. **Relevant persona panel** — for cross-cutting or judgment-heavy questions, dispatch a small parallel panel of `subagent_type` calls with the persona/role framed in the brief (e.g. security reviewer + UX reviewer + scope auditor on the same artifact). The orchestrator reconciles their envelopes.
5. **Human** — only for irreversible production-impacting actions (gate #1), irreversible destructive deletes (gate #2), or `user_impact: major` decisions (gate #3) per `references/keep-going-policy.md`. Anything cheaper is the orchestrator's decision.

## Pursue parallel work before idling

When blocked waiting on a peer, CI, deployment verification, or external signal, default to non-conflicting parallel work — pre-stage downstream chunks, verify state, capture loose ends, draft closeout artifacts. Idle-wait is a workflow violation. The only constraint: do not touch files actively claimed by a peer session per Rally Point presence.

## Token-posture gauge

Before dispatching expensive subagent fan-out or deep analysis, gauge remaining token budget via `scripts/budget_check.py` (or the most-recent `state.json.runs[-1].budget_summary`). If approaching `finalize_and_stop` territory, prefer cheaper / inline paths and queue deferred work into `.build-loop/followup/` rather than burn the remaining budget on speculative work. The end-of-run report carries the budget readback regardless.

## Why this lives in references/

The orchestrator body keeps a single line pointing here. Operating doctrine is text the orchestrator consults at runtime, not a procedure that fires every phase — progressive disclosure keeps the agent body focused on routing.
