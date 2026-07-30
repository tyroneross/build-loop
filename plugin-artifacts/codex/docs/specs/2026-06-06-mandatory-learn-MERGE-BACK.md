<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
# Merge-back ledger — mandatory Learn (2026-06-06)

Written before any branch/subagent launch (anti-sprawl). One canonical folder, one branch,
collapsed to `main` as the single final decision. Machine-readable equivalent: `state.json.createdRefs[]`.

## Parent principle
- **Canonical checkout:** `~/dev/git-folder/build-loop` (local). On `main`, synced to `origin/main`
  at `903d8d3` (v0.29.3), 0 ahead.
- **Single integration target:** `main`. No per-approach / long-lived branches after the run.

## Plan
1. Commit spec + this ledger to `main` (local) — done before the build run starts.
2. `/build-loop` provisions ONE run-worktree (`bl/` prefix), never operates on canonical checkout.
3. Subagents edit inside that one worktree — no sibling worktrees, no per-subagent branches.
4. Phase D `collapse_run.py`: bundle → merge `bl/...` onto `main` → delete merged branch + remove
   worktree folder → write final status to `createdRefs[]`. Harness-isolation worktrees under
   `.claude/worktrees/` must also be removed at close (observed lingering on prior runs — clean it).
5. Verify: `git worktree list` shows only canonical; `git branch` shows no `bl/*` / `worktree-agent*`.
6. **No deploy / no push** without explicit user OK. Local main only. Surface deploy as final step.

## Ref status (update as it happens)

| Ref / worktree | Purpose | Merge target | Status | Closed |
|---|---|---|---|---|
| `worktree-agent-ae2bdd5987272f4da` (harness-isolated run branch) | mandatory Learn + retro→Learn wiring (single commit `b64a879`) | `main` | **merged via fast-forward** | 2026-06-06 |
| `.claude/worktrees/agent-ae2bdd5987272f4da/` (harness worktree) | container for the harness-isolated run | (removed at closeout) | **removed** | 2026-06-06 |
| `.claude/worktrees/agent-acf639e1f965f5b18/` (leftover from prior run) | stale harness worktree, locked, pointed at HEAD `903d8d3` | (removed per user request) | **removed** | 2026-06-06 |
| `worktree-agent-acf639e1f965f5b18` (leftover branch from prior run) | branch for the leftover worktree | (deleted) | **deleted** | 2026-06-06 |

## Stop conditions / escalation
- PRODUCTION/DECISION-classified change → report, await user.
- Any unmerged non-hold or `worktree-agent*` branch at closeout → surfaced + cleaned, not left.
- Deploy/push is OUT OF SCOPE for the run; it is a separate user decision.

## Closeout summary

- Single commit `b64a879` (`feat(learn): mandatory Phase 6 + retro->Learn wiring (v0.30.0)`) landed on the harness-isolated branch.
- Fast-forward merge to `main`. No merge commit needed (single commit, linear history).
- Both `.claude/worktrees/` entries removed (this run's + the stale leftover) per user request.
- Final `git worktree list` should show only the canonical checkout.
- **NO push to `origin/main`** (per brief). User decides marketplace deploy as a separate restart-boundary step.
