# Three concurrency patterns worth keeping from the archived post-push-retro branch

**Written:** 2026-08-23 · **Source:** `archive/2026-08-23/worktree-agent-afd7b86f855514588`
**Status:** patterns only — the branch's code is NOT coming back

`worktree-agent-afd7b86f855514588` (2026-07-18, 7 commits) built a
`scripts/post_push_retro/` package. Main went a different way: `session_end_retro_sweep.py`
plus `agents/retrospective-synthesizer.md`, still being maintained on 2026-08-22.
Merging the branch would have left build-loop with two retro pipelines, so it was
archived — 184 commits behind, with no file it adds present on main.

The tier classifier, the risk-surface globs, and the post-push trigger are all
genuinely superseded. **Three concurrency defenses are not**, because they answer
questions main's design still faces. Each was written in response to a named
independent-auditor finding, so each has a real failure behind it rather than a
principle.

Recover the code with:

```sh
git show archive/2026-08-23/worktree-agent-afd7b86f855514588:scripts/post_push_retro/<file>
```

---

## 1. On a concurrent upgrade, MERGE the records — do not overwrite

`coverage.py`, `arm_upgrade()` · auditor finding f1 · commit `5082333`

Two pushes land close together and both decide the retro tier should be upgraded.
The second writer overwrites the first, and **the first push's commit range is gone
permanently** — no error, no retry, and the range is never retro'd.

The fix is a read-modify-write union rather than a replace: union the commit ranges,
take the **max** tier, and preserve the **earliest** timestamp. Every input survives
regardless of write order.

The general rule this encodes: when two writers can independently decide "this needs
more work," last-write-wins silently discards the other's evidence. Union is the
correct merge for an accumulating record; overwrite is correct only when the later
value strictly supersedes the earlier one — which is precisely what "concurrent" rules out.

## 2. Guard a checkpoint advance with an ancestry check

`coverage.py`, `_is_ancestor()` + `update_checkpoint_from_coverage()` · finding f4 · `5082333`

A checkpoint that records "last sha we retro'd up to" can be moved **backwards** by a
concurrent peer holding an older tip. Everything between the two shas then gets
re-processed, or skipped, depending on which direction the regression runs.

```python
def _is_ancestor(repo, candidate, existing) -> bool:
    """True when `candidate` is an ancestor of (older than) `existing` —
    so advancing to `candidate` would REGRESS."""
    ...
    return subprocess.call(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", candidate, existing],
        stdout=DEVNULL, stderr=DEVNULL, timeout=15) == 0
```

Call it before every checkpoint write and refuse the update when it returns True.
Note the failure posture: any exception — timeout, git missing, OS error — returns
`False`, so an unanswerable ancestry question never blocks a legitimate advance.
The check is a regression guard, not an authorization gate, and it is tuned accordingly.

## 3. A silent-failure path needs a durable local witness

`fallback.py` · finding f2 · commit `175085d`

The failure this exists for: automation launched detached, with output to `DEVNULL`.
When it fails there is no exit code anyone reads, no log anyone opens, and no
evidence the work was ever attempted. The job looks healthy because nothing
contradicts it.

The pattern writes a failure witness to `<git-common-dir>/build-loop-retro/failed/`
and drains it at session start, so the next session surfaces what the last one lost.
Two properties make it work: the witness lives in the **git common dir**, so it
survives worktree churn; and the drain is on a path that definitely runs, rather than
on the failing path itself.

This one is live in build-loop today, in a different form and for a different
mechanism — the same session that archived this branch found `check_runtime_memory_tracking`
and the `pre_bash_dispatch.sh` gates failing open under Codex with no witness at all
(`docs/2026-08-22-codex-hook-root-resolution.md`). Worth reading the two together
before designing the next background job.

---

## Not carried forward

Tier classification (trivial / medium / substantial), the post-push trigger, and the
risk-surface globs. The globs already exist on main as `infer_risk_surface.py`; the
other two belong to the post-push design main did not take.
