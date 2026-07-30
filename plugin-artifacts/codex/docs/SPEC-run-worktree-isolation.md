# SPEC — Build-loop run-entry worktree isolation (Phase 1a)

> Lifecycle update (2026-07-11): provisioning remains current. The original
> automatic zombie/orphan deletion design is superseded by the run-aware
> closeout contract in `docs/plans/2026-07-11-run-aware-closeout.md`.

## Context / why
A recurring, costly hazard: multiple build-loop runs (and a live agent fleet) operate on the **same canonical repo checkout**, so commits land on the wrong branch, HEAD gets switched mid-run, and leftover `bl/*` branches + `/tmp/buildloop-*-wt` worktrees accumulate. This consumed a full session of cleanup on `agent-rally-point` (2026-05-31/06-01) and recurred on this very repo. Memory: `feedback_rally_coordinates_files_not_git_worktree` (now a user-confirmed RULE: worktree-per-agent is mandatory).

Root cause: per-run/per-dispatch worktree isolation is **optional** (`isolation="worktree"` is caller-provided), so the default is in-place work on the canonical checkout. `worktree_guard.py` only creates worktrees when explicitly asked; `collapse_run.py` cleanup only fires if closeout is reached (crashed/killed runs leak).

## Goal (durable, structural — not prompt-reliant)
**Every build-loop run operates inside its own git worktree and declares any mutable non-Git data plane. The canonical checkout is never a run's working tree, and two runs cannot silently share a declared writable data resource.** Enforced deterministically (scripts/gates), not by an LLM following a prompt instruction.

## Approach (leading design — refine against real code in Assess/Plan)
1. **Run-entry worktree provisioning (primary).** At run start, deterministically create a run worktree via the existing `scripts/worktree_guard.py:create_guarded_worktree(...)` under `.build-loop/worktrees/run-<runId>` on a `bl/run-<runId>` branch off the run's base. The run's working dir becomes that worktree. **Fail-closed:** if worktree creation fails, abort the run with a clear error — never silently fall back to the canonical checkout. Reuse existing machinery; do not reinvent.
2. **Non-skippable cleanup.** Wrap closeout `collapse_run.py` so it runs in a `try/finally`-equivalent path; a normal finish bundles-then-collapses as today. Record the run's worktree in `state.json` so a crashed run is recoverable.
3. **Run-worktree reporter + explicit finalizer.** `scripts/worktree_reaper/` scans only `.build-loop/worktrees/run-*`, preserves active/unmerged/unattributed/ambiguous candidates, and reports by default. It has no direct Git mutation path. An explicit operator/integrator may pass both `--act` and `--owner-released`; the reaper then delegates an exact, merged candidate and exact canonical path to `collapse_run.py`, which alone creates and verifies the branch-specific bundle, persists the transaction receipt, rechecks branch-to-worktree registration and safety, then uses Git's checked-out-aware safe branch deletion. SessionStart never supplies owner release and is permanently report-only.
4. **Structural backstop (defense-in-depth).** A PreToolUse Bash guard that, when a build-loop run is active (run marker present) AND cwd is the canonical checkout, blocks `git commit` / `git switch` / `git checkout <branch>` with a message pointing to the run worktree. Belt-and-suspenders for any path that escapes #1.
5. **Data-plane manifest (current).** Fresh run provisioning writes `.build-loop/data-manifests/<build_loop_id>.json` and allocates the separate run directory `.build-loop/data/<build_loop_id>/` in the canonical repository. Keeping mutable data outside the linked source worktree preserves normal non-force `git worktree remove` closeout. Adapters declare each mutable surface with `scripts/data_plane.py add` as `per_worktree`, `shared_readonly`, `shared_serialized`, or `external_namespaced`; writable resource keys are collision-checked across active manifests. The manifest never provisions or deletes a database/service by itself. The canonical terminal closeout gate rejects an owned writable surface that is still active, deferred, or errored; zero-surface and explicitly retained/closed surfaces are terminal.

Prefer the attack (1–3, root fix) over defense (4); include 4 only if cheap and non-disruptive.

## Constraints (HARD)
- The dated implementation-worktree constraint from the original Phase 1a run is historical and no longer an active path instruction.
- Do not create nested run worktrees outside `.build-loop/worktrees/`.
- Preserve all existing `/build-loop:run` behavior for the single-run case; isolation must be transparent (the run just happens in a worktree).
- Do not create, copy, migrate, or delete external data resources from the generic lifecycle layer. Repository-specific adapters own those side effects and record their terminal disposition in the manifest.
- Minimal deps; reuse `worktree_guard.py` + `collapse_run.py`. Folder-per-capability for any new code.
- Python tests green (`uv run pytest` or the repo's runner); don't break existing tests.
- Age, missing Rally presence, and absent CWD evidence are never positive deletion authority. Only an explicit owner-release signal can authorize finalization; safety sensors remain vetoes.

## Out of scope (Phase 1b, separate run)
- `rally run` launching each agent into a linked worktree (the rally-cli substrate side). Foundation verified: rally resolves its room via git-common-dir (`agent-rally-point/crates/rally-cli/src/lib.rs:6285`), so linked worktrees share one room. Tracked, not in this run.

## Verification
- Unit tests: run-entry provisioning (success + fail-closed), report-only reaper, explicit owner-release delegation, verified bundle/receipt ordering, and cleanup recovery.
- Smoke: a `/build-loop:run`-equivalent dry path shows the run operating in `.build-loop/worktrees/run-*`, canonical checkout untouched (`git -C <canonical> status` unchanged).
- SessionStart never removes or prunes worktrees, including when legacy `BUILDLOOP_GC_ACT=1` is present.
- A completed, merged run closes only through the strict finalizer and leaves a verified terminal receipt; a simulated crash is reported until explicit owner release.
- Data-plane tests reject escaping per-worktree paths and cross-run writable resource collisions, and reject terminal posts while a declared owned surface remains active.
