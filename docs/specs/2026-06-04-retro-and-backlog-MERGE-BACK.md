<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
# Merge-back ledger — retro + backlog work (2026-06-04)

Written **before** any branch/subagent launch (anti-sprawl rule). One canonical folder, one
branch, collapsed to `main` as the single final decision. Update the status table as refs are
created and merged. The machine-readable equivalent is `state.json.createdRefs[]`.

## Parent principle
- **Canonical checkout:** `~/dev/git-folder/build-loop` (local). Working dir is local; on `main`,
  synced to `origin/main` at start (e363e3e, 0 ahead / 0 behind).
- **Single integration target:** `main`. Everything collapses here. No `codex/*`, no `integration`,
  no per-approach long-lived branches after the run.

## Plan
1. Commit spec + this ledger to `main` (local) — done before the build run starts.
2. `/build-loop:run` provisions ONE run-worktree under `.build-loop/worktrees/run-<id>`
   (`bl/` branch prefix) — mandatory isolation, never operates on the canonical checkout.
3. Subagents (implementer, etc.) edit files **inside that one worktree** — no sibling worktrees,
   no per-subagent branches.
4. Phase D Closeout runs `collapse_run.py`: bundle for reversibility → merge `bl/...` onto `main`
   → delete merged branch + remove its worktree folder → write final status to `createdRefs[]`.
5. Verify: after collapse, `git worktree list` shows only the canonical checkout; `git branch`
   shows no leftover `bl/*`; `main` contains the work.
6. **No deploy.** Commit stays local. Marketplace/plugin push happens at a user-chosen restart
   boundary (deploying the in-use plugin GCs its cache version and kills live agents).

## Ref status (update as it happens)

| Ref / worktree | Purpose | Merge target | Status | Closed |
|---|---|---|---|---|
| `bl/run-827367` (run-worktree at `.build-loop/worktrees/run-827367`) | implement retro + backlog features | `main` | closed — merged into main; collapsed via scripts/collapse_run.py; bundle at `.build-loop/bundles/collapse-run_20260505T222953Z_5e7592fc-20260604T215740Z.bundle` | 2026-06-04T21:57:40Z |

## Stop conditions / escalation
- A `PRODUCTION` or `DECISION`-classified change surfaces (not auto-executed) → report, await user.
- Any unmerged non-hold branch at closeout → surfaced, not silently left.
- Deploy is explicitly **out of scope** for this session.
