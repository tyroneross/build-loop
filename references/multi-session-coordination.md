# Multi-Session Coordination (M4 session registry, M5 memory index)

_Linked from `agents/build-orchestrator.md` §Multi-session concurrency._

Multiple build-loop sessions can run concurrently in different terminals and across coding hosts (Claude Code, Codex, Gemini CLI). They MUST coordinate so they don't clobber each other's working trees or commit races. Three scripts own this concern:

- `scripts/session_registry.py` — presence + collision detection (`~/.build-loop/sessions/<run_id>.json`)
- `scripts/memory_writer.py` — canonical writer for memory files (provenance frontmatter + atomic INDEX append in one operation)
- `scripts/memory_index.py` — append-only discovery log at `~/.build-loop/memory/INDEX.jsonl`

## Required orchestrator integration points

This section defines two M-series trigger families that complement M1 (envelope persist) + M2 (heartbeat) + M3 (cost-ledger row):

- **M4 — Session registry presence + collision check.** Fires at Phase 1 start, every M2 trigger point (heartbeat refresh), Phase 3 pre-dispatch (`files_owned` update + recheck), and clean completion (unregister). Telemetry + safety; never blocks a build except on CRITICAL collision in headless mode.
- **M5 — Memory index append + canonical writer.** Fires on every memory write to `~/.build-loop/memory/` (via `memory_writer.py write`) and every read between phases (via `memory_index.py tail --since`). Telemetry + cross-session discovery; never blocks.

### M4 — On Phase 1 Assess start (after `run_id` is generated, BEFORE any planning):

1. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/session_registry.py register --run-id "$RUN_ID" --host claude_code --workdir "$PWD" --pid $$ --phase assess`
2. `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/session_registry.py check --run-id "$RUN_ID" --workdir "$PWD" --phase assess --json` → parse `tier`
3. **Tier handling**:
   - `LOW` (exit 0) — log peer count to terminal: "N other build-loop sessions active (different workdirs)"; continue.
   - `MEDIUM` (exit 1) — log: "Peer session at same workdir, phase=`<peer.phase>`, started=`<peer.started_at>`"; continue.
   - `HIGH` (exit 2) — interactive (Claude Code): `AskUserQuestion`("Peer session at this workdir is in `<execute|iterate>` — proceed / abort / queue?"). Headless (Codex): log + set `high_frequency_mode: true` (heartbeat cadence → every 30s vs 5min default); continue.
   - `CRITICAL` (exit 3) — interactive: hard-stop with message naming overlapping files. Headless: `python3 .../session_registry.py` writes `SAFE-STOP-collision-<peer-run-id>.md` sentinel to `<workdir>/.build-loop/` and the orchestrator exits non-zero.
4. Surface any `<workdir>/.build-loop/SAFE-STOP-collision-*.md` files left by prior aborted sessions BEFORE doing anything else. The user must acknowledge and delete each sentinel before this session proceeds.

### M4 — On every M2 heartbeat trigger point (dispatch_chunk, return_chunk, phase_transition, iterate_attempt):

Refresh the session_registry heartbeat. Append `--phase <current>` whenever the phase changes:

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/session_registry.py heartbeat --run-id "$RUN_ID" --phase "$CURRENT_PHASE"
```

If the M2 helper update fails, the session_registry heartbeat is still best-effort — never block the build.

### M4 — On Phase 3 pre-dispatch (after MECE file ownership is decided):

Update `files_owned` on the presence file so concurrent peers can see exactly which files this session will touch. Re-run `check` immediately afterward to catch new CRITICAL overlaps that materialized while planning:

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/session_registry.py heartbeat --run-id "$RUN_ID" --phase execute --files-owned "$FILES_OWNED_CSV"
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/session_registry.py check --run-id "$RUN_ID" --workdir "$PWD" --phase execute --files-owned "$FILES_OWNED_CSV" --json
```

CRITICAL handling at this point is identical to Phase 1 — interactive surfaces immediately, headless writes the sentinel and exits.

### M4 — On clean completion (Review-G final, AFTER the run-entry is written):

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/session_registry.py unregister --run-id "$RUN_ID"
```

Moves presence to `sessions/dead/` so it doesn't clutter the active scan for future sessions. Failure to unregister is benign — the stale-sweep (5-min default) will absorb it.

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

When `AskUserQuestion` is unavailable, the orchestrator MUST NOT block on collision detection. The deterministic defaults per `feedback_no_permission_asks.md` posture:

- LOW/MEDIUM: log + proceed at normal cadence.
- HIGH: enter `high_frequency_mode` (heartbeat every 30s, recheck collisions before every chunk dispatch); proceed.
- CRITICAL: write SAFE-STOP sentinel + exit non-zero. The first sentinel always wins; the survivor takes the work.

The interactive→headless distinction lives in this prompt, not in the scripts — the scripts return tiers + exit codes deterministically.
