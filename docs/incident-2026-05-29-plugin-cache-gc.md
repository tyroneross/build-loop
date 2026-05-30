<!--
SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
SPDX-License-Identifier: Apache-2.0
-->

# Incident — in-use plugin cache version GC'd mid-session (2026-05-29)

## Symptom

Every build-loop hook started failing with `Failed to run: Plugin directory does not
exist: ~/.claude/plugins/cache/rosslabs-ai-toolkit/build-loop/0.13.2`. The session had
loaded build-loop from `0.13.2` at startup; that cache dir was deleted while the session
was still live, orphaning every hook's `${CLAUDE_PLUGIN_ROOT}`.

## Root cause (verified by direct observation)

**Claude Code's plugin system garbage-collects a superseded cache version dir within
minutes of it being superseded — same session, not after the documented grace period.**

- `marketplace-autoupdate.py` (the user's background hook) fetches newer versions into
  fresh `cache/<mkt>/<plugin>/<version>/` dirs and, per its own header, retains old ones
  (its only `rmtree` is rollback of a *failed* fetch). ✅ So it is **not** the deleter.
- build-loop's own `scripts/prune_plugin_cache.py` *can* delete all-but-current, but is
  wired to no hook/cron (manual via `/build-loop:test` or npm). ✅ It did **not** auto-run.
- Direct evidence of CC-side GC: `0.13.4` was created at 22:23 and **gone by 22:40 the
  same session** (~15 min); `0.13.5` was re-fetched at 22:39. `0.13.2` had vanished the
  same way earlier. The catalog advanced 0.13.2 → 0.13.4 → 0.13.5 (autoUpdate:true).
- ⚠️ Official docs ([plugins-reference — "Cache and updates"](https://code.claude.com/docs/en/plugins-reference.md))
  state orphaned version dirs get a **7-day grace** "so concurrent sessions keep running."
  Observed behavior **contradicts** this — removal was same-session within minutes. Either
  the grace is not honored in this CC version, or it is keyed on an orphan-time we didn't
  meet. Treat the 7-day grace as **not reliable** for active-dev plugins.

## Fixes

### 1. Immediate unblock (done, this session)

Recreated the orphaned dir as a symlink to the local dev repo:
`cache/rosslabs-ai-toolkit/build-loop/0.13.2 → ~/dev/git-folder/build-loop`. The session's
`${CLAUDE_PLUGIN_ROOT}` resolves again and serves latest-from-local (0.13.5). ⚠️ This
symlink is itself at risk on the next autoupdate GC cycle — the durable fix is the config
change below. `/reload-plugins` is the clean way to rebind a session to the managed dir.

### 2. Pruner in-use guard (done — this commit)

`scripts/prune_plugin_cache.py` never deletes the version a live session is loaded from.
It reads `CLAUDE_PLUGIN_ROOT` / `CODEX_PLUGIN_ROOT` (unresolved, so a symlinked local-dev
dir keeps its *cache* name) and protects that version by name before any manifest check.
New `--protect VERSION` (repeatable) and `--no-detect-in-use` (escape hatch) flags; report
gains a `protected[]` field. 4 regression tests added incl. the symlinked-in-use shape.
Verified treatment-vs-control on the real cache: with detection on, `0.13.2` is protected;
with `--no-detect-in-use`, it would be pruned (the old behavior). This hardens build-loop's
*own* manual pruner — it does **not** change CC-core GC.

## Recommended user mitigation (CC-core GC — not ours to patch)

The actual recurrence cause is CC-side GC under `autoUpdate:true`. Pick one:

- **`autoUpdate:false`** on `rosslabs-ai-toolkit` (settings.json) — stops mid-session
  version churn; update on demand with `/plugin marketplace update`. Lowest-friction.
- **Directory-source local-dev** for build-loop — `/plugin marketplace add
  ~/dev/git-folder/build-loop`; no cache, no GC, live edits via `/reload-plugins`. Best
  for active development (matches the build-loop-install-topology note).

Also: the autoupdate log shows build-loop registered at **two** marketplace indices
(`[0]` was 0.13.2, `[2]` was 0.13.3) — a likely duplicate-registration contributor worth
de-duping via `/plugin`.
