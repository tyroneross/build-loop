<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Do / Branch / Surface Policy

_Linked from `agents/build-orchestrator.md` §"Keep going until done"._

Build-loop's autonomy model. Every orchestrator action runs through `scripts/classify_action.py`, which returns one of four MECE labels (**SAFE / RISKY / DECISION / PRODUCTION**). The orchestrator's response is mechanical from that point.

## Classification table

| Label | What it is | Long-mode response | Normal-mode response |
|---|---|---|---|
| **SAFE** | Read-only command, non-broad-blast file, default | Execute on current branch | Execute on current branch |
| **RISKY** | Broad blast (migrations/workflows/Dockerfile/manifests), irreversible non-production action (force-push to feature branch, npm publish in isolation, DROP TABLE on unmerged work) | Mechanism A: isolate to worktree-branch, push branch, log `riskyBranches[]`, continue main | Same as long-mode |
| **DECISION** | Implementer envelope has `novel_decisions[]` with valid `recommended_default` + per-option trade-offs | Mechanism B: auto-pick `recommended_default`, log `autonomousDefaults[]`, continue | Surface trade-off table to operator, wait for pick |
| **PRODUCTION** | Irreversible + production target (production deploy, force-push to main, drop production DB) | Escalate to operator | Escalate to operator |

## Reasons to surface (operator interrupts)

The **only** valid reasons to surface and pause:

1. `classify_action.py` returned **PRODUCTION**.
2. `classify_action.py` returned **DECISION** AND (a) mode is normal, OR (b) `decision_state == "low_confidence"`.
3. Missing credential or secret the user must provide (environment configuration; not a build decision).
4. Externally-blocked work (paid API down, hardware unavailable, third-party CI red).
5. Explicit hand-off point the plan named.
6. Build has run too long to keep going wrong (8h wall-clock without a successful Review pass, or 5 consecutive Iterate failures on the same criterion).

Reasons (3)–(6) override mode — they always surface regardless of long-mode.

## Mechanism A — RISKY → branch isolation

When `classify_action.py` returns `RISKY`:

1. Create (or reuse) a worktree at `.claude/worktrees/risky-<chunk_id>-<short-hash>` using the existing isolation infra (see `CLAUDE.md` §"Concurrent dispatch isolation"). Pass `isolation: "worktree"` to the Agent dispatch.
2. Dispatch the implementer with the worktree as its working directory.
3. On successful envelope return: orchestrator commits in the worktree, pushes the branch (feature-branch push is `auto` per `deployment_policy.py`), and writes a `riskyBranches[]` entry via `scripts/log_decision.py --kind risky_branch`.
4. The main worktree's `HEAD` is unaffected; orchestrator continues to the next chunk on main.
5. The Phase 4 Report surfaces `## Risky work (branched for your review)` as the **first** section, listing each `riskyBranches[N]` entry with branch link, summary, trade-offs, and the `matched_rule` that triggered isolation. The operator merges or discards each branch at their pace.

**Why branches, not prompts.** A migration touch, a Dockerfile change, or an irreversible non-production action is recoverable — you can delete the branch. Asking the operator on each one stalls the loop; isolating them to a branch preserves auditability without blocking forward progress.

## Mechanism B — DECISION → mode-aware auto-pick

When `classify_action.py` returns `DECISION` with `decision_state == "pickable"`:

**Long-mode** (`state.execution.budget.mode in {long, custom, overnight}`):
- For each `novel_decisions[i]`, take `recommended_default` as `chosen`.
- Append to `state.json.runs[].autonomousDefaults[]` via `scripts/log_decision.py --kind autonomous_default`.
- Re-dispatch the implementer with `resolved_decisions:` appended.
- Full protocol in `references/halt-and-ask-protocol.md` §"Mode-aware routing".

**Normal-mode:**
- Surface the trade-off table to the operator via `AskUserQuestion`.
- The prompt MUST be the trade-off table itself (per-option `user_impact`/`performance`/`speed`/`cost`), not raw option text.
- Operator's pick is logged with `escalated: true`.

When `decision_state == "low_confidence"`: surface the trade-off table even in long-mode. The implementer is signaling it cannot pick well; that's a real ask, not a procedural pause.

## What's NOT a reason to surface

Under the do/branch/surface policy, the orchestrator does NOT prompt for:

- Multiple-choice "A vs B vs C" branches when the implementer has a `recommended_default` with `confidence: high` or `med` (long-mode auto-picks; normal-mode surfaces the trade-off table without asking "should I?").
- "Should I commit and push?" — pushes to feature branches are `auto` (see `deployment_policy.py` preview routing).
- "Continue?" after a phase boundary — phases are authorized scope once the plan is accepted.
- "Continue or hold?" on remaining work that is authorized, isolated to the agent's own lane/worktree, and determinate (e.g. the rest of a defined multi-step prune/refactor/migration item list). Run the list to completion, then report once. Output volume and turn length are never reasons to pause.
- A posted **coordination handoff** to a peer (Codex, another session). Handoffs are *fire-and-continue*: the orchestrator keeps executing its own owned lane in parallel. Only a verifier **verdict that gates the next dependent step** is a wait — never the act of handing off, and never a handoff on a parallel-safe lane.
- "Should I retry this iterate failure?" — iterate loops follow the existing N=5 cap.
- Read-only inspection commands like `sed`, `cat`, `vercel logs`, `git status` — `classify_action._is_read_only()` short-circuits these to SAFE.

## Telemetry

Every classification decision logs a single line per action:
- `[do] <classification> reason="<short>" cmd="<truncated>"`
- `[branch] risky_branch=<name> matched_rule="<rule>" files=<count>`
- `[auto-pick] decision=<id> chose=<chosen> confidence=<level> mode=<long|normal>`
- `[escalate] decision=<id> reason=<low_confidence|normal_mode|production|stuck>`

The Phase 4 Report aggregates these into a "Decisions made autonomously" section near the top.
