# Multi-Session Coordination (App Pulse presence, M5 memory index)

_Linked from `agents/build-orchestrator.md` §Multi-session concurrency._

Multiple build-loop sessions can run concurrently in different terminals and across coding hosts (Claude Code, Codex, Gemini CLI). They MUST coordinate so they don't clobber each other's working trees or commit races. The mechanisms that own this concern:

- **App Pulse presence** — `scripts/app_pulse/presence.py` + `scripts/app_pulse/channel_paths.py`: the single concurrent-presence source of truth. One file per live session at `~/.build-loop/apps/<slug>/sessions/<session-id>.json`. (The legacy `scripts/session_registry.py` / `~/.build-loop/sessions/<run_id>.json` mechanism was documented-dead and was **removed 2026-05-18** — see `KNOWN-ISSUES.md` §M4.)
- `scripts/memory_writer.py` — canonical writer for memory files (provenance frontmatter + atomic INDEX append in one operation)
- `scripts/memory_index.py` — append-only discovery log at `~/.build-loop/memory/INDEX.jsonl`

## Required orchestrator integration points

This section defines the App Pulse presence integration plus the M5 trigger family. They complement M1 (envelope persist) + M2 (heartbeat) + M3 (cost-ledger row):

- **App Pulse presence — concurrent-session awareness.** Fires at the Phase 1 preamble (write presence) and each phase-start (read active peers + checkpoint). Awareness only (D4): peer file-overlap is a WARNING, never a block. Checkpoint-poll, no daemon (D3).
- **M5 — Memory index append + canonical writer.** Fires on every memory write to `~/.build-loop/memory/` (via `memory_writer.py write`) and every read between phases (via `memory_index.py tail --since`). Telemetry + cross-session discovery; never blocks.

### App Pulse presence — slug resolution (D1, worktree-aware)

`scripts/app_pulse/channel_paths.app_slug(cwd=<repo>)` resolves the channel slug from `git rev-parse --git-common-dir` → canonical-repo basename, so the **main checkout and every `git worktree` of the same repo share one channel** — precisely the concurrent scenario this targets (agent dispatches run under `isolation: "worktree"`). Falls back to memory's `derive_slug_from_cwd` only outside a git repo. Use this resolver; never reimplement slug derivation.

### App Pulse presence — On Phase 1 Assess preamble (after `run_id` is generated, BEFORE any planning):

1. `presence.write_presence(channel_dir, session_id=..., tool="claude_code", model=..., run_id="$RUN_ID", app_slug=<from channel_paths.app_slug>, phase="assess", files_in_flight=[])`. Codex / Gemini / other hosts substitute their `tool` value. Fire-and-forget: never raises, never blocks.
2. `peers = presence.read_active_presence(channel_dir, exclude_session=<this session_id>)` (this also reaps stale presence whose `heartbeat_ts` is older than the channel's `heartbeat_minutes`, default 15).
3. **Peer handling** (awareness, never a hard block — D4):
   - No peers — continue silently.
   - Peers, no file overlap — log one line per peer: tool, run_id, phase.
   - Peers WITH `files_in_flight` overlapping this session's planned files — surface a `soft-claim` **WARNING** naming the peer + overlapping files + the peer's phase, then proceed with awareness. Interactive hosts MAY additionally `AskUserQuestion` to coordinate; headless hosts log + proceed (per `feedback_no_permission_asks.md`). There is no SAFE-STOP sentinel and no non-zero exit — App Pulse is awareness, not a lock.

### App Pulse presence — On each phase-start and when files-owned changes:

Refresh presence so concurrent peers see the current phase and the files this session will touch:

```
presence.write_presence(channel_dir, session_id=..., tool="claude_code", model=...,
                         run_id="$RUN_ID", app_slug=<slug>, phase="<current>",
                         files_in_flight=<MECE files for this phase>)
```

Then `checkpoint.checkpoint_read(channel_dir, session_id=..., my_files=[...])`; when its envelope reports peers / `dep-change` (→ reinstall) / `arch-scan-complete` (→ re-baseline scout cache) / file-overlap (→ `soft-claim` WARNING), surface the compact reaction block. The `presence.write_presence` call preserves the per-session read cursor across refreshes. All writes are fire-and-forget; the only locked write is the `revision` bump (short-timeout, skip-on-timeout). None can block or fail a host action.

### App Pulse presence — On clean completion:

No explicit unregister is needed. The last presence write stands; `presence.reap_stale` (run opportunistically at every peer read) removes it once `heartbeat_ts` exceeds the stale window. A forgotten session is therefore self-healing — no `dead/` directory, no cleanup step.

### M5 — Between phases, scan for new sibling learnings:

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_index.py tail --since "$LAST_INDEX_CHECK_TS" --exclude-run-id "$RUN_ID" --json
```

Read any new rows. If a row's `file` matches a memory category relevant to the current build (e.g. `feedback_buildloop_*` during a build-loop work session), Read the underlying memory file and surface its `description` field in the next phase brief. Tag based on the file's provenance frontmatter:

- `[CROSS-REPO — requires scrutiny]` when `source_workdir` ≠ this workdir AND `source_repo` ≠ this repo's git remote.
- `[VALIDATED — applied in N repos]` when `cross_repo_validated: true` AND `len(applied_in_repos) >= 2`.
- Otherwise — surface as a normal peer signal.

When a cross-repo memory is successfully applied in the current build, record it so the trust gradient updates:

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_writer.py mark-applied \
  --file "<rel-path>" \
  --applying-repo "$THIS_REPO_REMOTE" \
  --applying-workdir "$PWD" \
  --applying-run-id "$RUN_ID"
```

### M5 — On every memory write under `~/.build-loop/memory/` (Phase 4 Review-F, Phase 6 Learn, or any save-memory action): ALWAYS use `memory_writer.py write`. Never write memory files directly.

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_writer.py write \
  --file "<rel-path>" \
  --name "<slug>" \
  --description "<one-line>" \
  --type feedback \
  --run-id "$RUN_ID" \
  --workdir "$PWD" \
  --host claude_code \
  --body-file <tmp-body-path>
```

The writer adds provenance frontmatter (source_repo auto-detected, source_workdir, source_run_id, source_host, cross_repo_validated=false, applied_in_repos=[], created_at, last_updated_at) and atomically appends the INDEX row. On update, preserves `created_at` + `applied_in_repos` so cross-repo validation history survives edits. Sibling sessions see the write on their next tail.

**One-time migration**: on the first build after this version is installed, run `memory_writer.py migrate --dry-run` to preview, then re-run without `--dry-run` to backfill provenance frontmatter onto existing memory files. Idempotent; safe to re-run.

## Headless host (Codex, cron) deterministic defaults

App Pulse presence is awareness-only (D4), so headless hosts never block on it. Per the `feedback_no_permission_asks.md` posture:

- No peers / peers without file overlap: log + proceed at normal cadence.
- Peers WITH overlapping `files_in_flight`: log the `soft-claim` WARNING (peer, files, phase) and proceed. There is no hard-stop, sentinel, or non-zero exit — coordination is the human's call after the fact, not a gate.

The interactive→headless distinction lives in this prompt, not in the scripts: `read_active_presence` returns the same peer list regardless of host; only the surfacing differs (interactive MAY additionally `AskUserQuestion`).

## R5 — Pre/post canonical snapshot around isolated dispatch

Every `Agent(subagent_type=..., isolation: "worktree", ...)` dispatch MUST be wrapped in a `git status --porcelain` snapshot of the canonical working tree. After the dispatch returns, re-snapshot. Non-empty diff with no canonical edits in between = isolation contract broken; surface as an error in the run report.

```bash
PRE=$(git -C "$CANONICAL_WORKDIR" status --porcelain)
# ... Agent(isolation: "worktree", ...) dispatch here ...
POST=$(git -C "$CANONICAL_WORKDIR" status --porcelain)
[ "$PRE" = "$POST" ] || { echo "❌ ISOLATION_BREACH: canonical changed during dispatch"; echo "PRE:"; echo "$PRE"; echo "POST:"; echo "$POST"; }
```

Detects, does not fix. The breach class (unproven mechanism — possible causes: interrupted earlier dispatch leaking edits, Codex sub-subagent running outside the worktree, IDE auto-fix, background hooks) is case-by-case. Naming the breach is the contract; the operator decides recovery. Cost is two `git status` calls per dispatch — small enough to make automatic.
