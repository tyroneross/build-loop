# SPEC — Build-loop run-entry worktree isolation (Phase 1a)

## Context / why
A recurring, costly hazard: multiple build-loop runs (and a live agent fleet) operate on the **same canonical repo checkout**, so commits land on the wrong branch, HEAD gets switched mid-run, and leftover `bl/*` branches + `/tmp/buildloop-*-wt` worktrees accumulate. This consumed a full session of cleanup on `agent-rally-point` (2026-05-31/06-01) and recurred on this very repo. Memory: `feedback_rally_coordinates_files_not_git_worktree` (now a user-confirmed RULE: worktree-per-agent is mandatory).

Root cause: per-run/per-dispatch worktree isolation is **optional** (`isolation="worktree"` is caller-provided), so the default is in-place work on the canonical checkout. `worktree_guard.py` only creates worktrees when explicitly asked; `collapse_run.py` cleanup only fires if closeout is reached (crashed/killed runs leak).

## Goal (durable, structural — not prompt-reliant)
**Every build-loop run operates inside its own git worktree. The canonical checkout is never a run's working tree.** Enforced deterministically (scripts/hooks), not by an LLM following a prompt instruction.

## Approach (leading design — refine against real code in Assess/Plan)
1. **Run-entry worktree provisioning (primary).** At run start, deterministically create a run worktree via the existing `scripts/worktree_guard.py:create_guarded_worktree(...)` under `.build-loop/worktrees/run-<runId>` on a `bl/run-<runId>` branch off the run's base. The run's working dir becomes that worktree. **Fail-closed:** if worktree creation fails, abort the run with a clear error — never silently fall back to the canonical checkout. Reuse existing machinery; do not reinvent.
2. **Non-skippable cleanup.** Wrap closeout `collapse_run.py` so it runs in a `try/finally`-equivalent path; a normal finish bundles-then-collapses as today. Record the run's worktree in `state.json` so a crashed run is recoverable.
3. **Zombie reaper.** New `scripts/worktree_reaper.py`: scans `.build-loop/worktrees/*`, and for any whose `bl/*` branch is (a) not checked out and (b) not referenced by a recent `state.json` run → bundle to `.build-loop/bundles/` then `git worktree remove`. Idempotent, dry-run flag, age threshold. This removes the leaked-worktree class from crashed runs.
4. **Structural backstop (defense-in-depth).** A PreToolUse Bash guard that, when a build-loop run is active (run marker present) AND cwd is the canonical checkout, blocks `git commit` / `git switch` / `git checkout <branch>` with a message pointing to the run worktree. Belt-and-suspenders for any path that escapes #1.

Prefer the attack (1–3, root fix) over defense (4); include 4 only if cheap and non-disruptive.

## Constraints (HARD)
- **Work ONLY in `/Users/tyroneross/dev/git-folder/build-loop-wt-isolation`** (branch `feat/run-entry-worktree-isolation`). NEVER touch the canonical build-loop checkout at `/Users/tyroneross/dev/git-folder/build-loop` (a Codex agent has uncommitted work on `codex/global-memory-update-ledger` there) or the `.build-loop/worktrees/monitoring-intake` worktree.
- Do not create your own nested worktrees outside `.build-loop/worktrees/`; operate in-place in this worktree.
- Preserve all existing `/build-loop:run` behavior for the single-run case; isolation must be transparent (the run just happens in a worktree).
- Minimal deps; reuse `worktree_guard.py` + `collapse_run.py`. Folder-per-capability for any new code.
- Python tests green (`uv run pytest` or the repo's runner); don't break existing tests.

## Out of scope (Phase 1b, separate run)
- `rally run` launching each agent into a linked worktree (the rally-cli substrate side). Foundation verified: rally resolves its room via git-common-dir (`agent-rally-point/crates/rally-cli/src/lib.rs:6285`), so linked worktrees share one room. Tracked, not in this run.

## Verification
- Unit tests: run-entry provisioning (success + fail-closed), reaper (zombie detection + bundle-before-remove + dry-run), cleanup non-skip.
- Smoke: a `/build-loop:run`-equivalent dry path shows the run operating in `.build-loop/worktrees/run-*`, canonical checkout untouched (`git -C <canonical> status` unchanged).
- No leftover worktrees/branches after a completed run; reaper clears a simulated crashed-run leak.
