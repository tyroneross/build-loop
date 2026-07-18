<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Post-push scope-gated retrospective auto-trigger

Single source of truth for the wired, non-blocking, scope-gated retrospective that fires after a push. It **reuses** the three existing retro tiers — it does not add a retro engine.

## Why (verified)

A Fable delta-comparison ruled the 3-stage recursive retrospective **notably better** than a single-pass prose retro: its independent Stage-3 judge caught a false recommendation (a "build X" that was already built) before the user acted, and it found the unifying root cause and converted lessons into actuators. The load-bearing feature is the **independent verification judge**. But the full pipeline is worth its ~3-5× Fable cost only for substantial work. So this is a **scope-gated** trigger — the scoping IS the cost control, not an always-on Fable trigger.

## The three reused tiers (no engine rebuilt)

| Tier | Runs | Cost | Reuses |
|---|---|---|---|
| **trivial** | zero-LLM deterministic sweep | ~free | `python3 -m retrospective` (same call as `session_end_retro_sweep.py`) |
| **medium** | deterministic sweep + independent Stage-3 judge | ~1 Fable agent | `skills/recursive-retrospective/references/03-judge.md` |
| **substantial** | deterministic sweep + full 3-stage pipeline | ~3-5× Fable | `Skill("build-loop:recursive-retrospective")` |

## Trigger mechanism (why this, honestly)

Git has **no native client-side post-push hook**. Two options were considered; option (a) was chosen because the git pre-push hook is the ONLY surface that fires for EVERY push regardless of initiator (ad-hoc `git push`, Codex, launchd, a crashed automation) — option (b), hooking build-loop's own closeout flow, would only cover build-loop runs and miss exactly the gap `session_end_retro_sweep` exists to close.

- **Primary path (all pushes):** `hooks/git/pre-push` → `post_push_retro.arm.arm_and_spawn` writes a UNIQUE baton (`armed-<ts>-<pid>-<sha>.json`, concurrency-safe) recording the pushed ref-range, then spawns a **detached** (`start_new_session`, stdio→DEVNULL) background job `python3 -m post_push_retro run --armed <baton>`. It does NO retro work synchronously, so the push is never blocked or slowed. Fires only on the clean success path (a held/blocked push does not spawn a retro). Fail-open — a broken trigger never breaks a push.
- **In-run path (build-loop Phase 4G):** the orchestrator is already an LLM context, so it calls `run --llm-available` and dispatches the Fable judge/pipeline INLINE (see `references/phase-4-review.md` §"Post-push retrospective dispatch").
- **Durable fallback path:** `hooks/session-start-closeout.sh` runs `post_push_retro drain` — re-runs a STALE baton (machine slept / crashed before the detached job finished) and escalates a stale unclaimed Fable upgrade to the fallback.

**LLM boundary (honest):** a git-hook background job has no LLM, so for medium/substantial it captures the deterministic baseline and ARMS the Fable upgrade (`upgrade.json`) for the next LLM context (Phase 4G or a session-start drain). This is the same deterministic-first / queue-the-LLM pattern `session_end_retro_sweep` uses. The upgrade carries its own ref-range and a staleness escalation, so "medium → run the judge" can never silently become "never".

## Coverage (multi-branch + worktree, checkpointed, per-repo)

Covers all work since the prior retrospective across every advanced branch AND worktree — not just the current push's delta. Anchored **per-repo** at `git rev-parse --git-common-dir` (shared by all worktrees), so N worktrees of one repo share ONE checkpoint and count as ONE repo (using per-worktree `--show-toplevel` here would spuriously escalate routine 2-worktree work to substantial). State lives under `<git-common-dir>/build-loop-retro/`: `checkpoint.json` (atomic, read-modify-write UNION — no double-cover, no miss), batons, `upgrade.json`, and `failed/` witnesses. The checkpoint advances only after a successful retro.

> **v1 scope:** coverage drives tier selection, the checkpoint, and the fallback ref-range. The retro NARRATIVE is produced by the reused synthesizer as-is; making the synthesizer multi-branch-delta-aware is a documented follow-up (that would be rebuilding the engine — out of scope).

## Classifier → tier (thresholds are config, defaults cite Fable guidance)

`substantial` if any: `repos_touched ≥ 2` · `commit_count ≥ 10` · a risk-surface change (auth/privacy/egress/security-hook/schema) · a P0 recommendation · a ≥2-instance failure cluster. Else `trivial` if a single commit OR docs/config-only with no recommendations. Else `medium` if the deterministic retro emitted actionable recommendations. Else `trivial` (cheapest default). Retro-FIRST: the deterministic retro runs before classification so its output feeds the medium-tier signal.

## Fallback — never silently skip

If the retro cannot run for ANY reason (Fable/budget/crash/timeout/lock), the work is filed, not lost:
- build-loop's OWN repo → `scripts/backlog.py new` (local backlog).
- any OTHER repo → `scripts/file_to_operations_center.py` (Operations-Center queue).
Each entry carries repo + ref-range + tier + why + focus actions. Because the primary caller is a detached DEVNULL process, a CLI failure ALSO writes a durable local witness under `<git-common-dir>/build-loop-retro/failed/`, surfaced by the session-start drain. The falsifier this guards: a push producing neither a retro nor a fallback entry.

## Config (`.build-loop/config.json` → `retrospective`; default ENABLED)

```json
{
  "retrospective": {
    "autoAfterPush": true,
    "optOut": false,
    "thresholds": {
      "substantial_commits": 10,
      "substantial_repos": 2,
      "first_run_cap": 50,
      "upgrade_stale_hours": 24,
      "risk_surface_globs": ["*auth*", "*secret*", "hooks/**", "*.sql", "..."],
      "docs_config_globs": ["*.md", "*.json", "docs/**", "..."]
    },
    "budget": { "maxTokens": null }
  }
}
```

The GLOBAL default lives in `scripts/post_push_retro/config.py` (`.build-loop/config.json` is gitignored, so there is no tracked default file). **Default posture: enabled** (the user asked to make it standard). Cost stays bounded because the classifier only ever spends Fable on medium/substantial; trivial pushes run the free sweep. To make it opt-in globally, set `"autoAfterPush": false`; per-repo kill switch is `"optOut": true`; the budget guard (`budget.maxTokens`) skips the Fable upgrade → fallback when exceeded (deterministic capture is retained).

## Activation

The trigger is wired but dormant until the git hook is installed: `python3 scripts/install_git_hooks.py --install`. `hooks/session-start-git-hooks.sh` re-installs hooks at session start. A repo whose hook was never installed skips (an inherent limit of any git-hook trigger) — documented, not silent-by-design.

## Files

`scripts/post_push_retro/{config,coverage,classifier,router,fallback,arm,__main__}.py` (+ colocated `test_*.py`); `hooks/git/pre-push` (arm); `hooks/session-start-closeout.sh` (drain); Phase 4G dispatch in `references/phase-4-review.md`.
