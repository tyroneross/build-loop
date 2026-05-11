# Memory — Global and Project-Scoped (full protocol)

> Loaded from `skills/build-loop/SKILL.md` summary. Contains the full memory system: global vs project stores, routing rules, and read/write policies.

## Memory — Global and Project-Scoped

Build-loop maintains two memory stores. Every build reads both; writes go to exactly one based on scope.

**Global memory**: `~/.build-loop/memory/`

- Applies across every project this user builds.
- Examples: "Deployment to Vercel uses `vercel deploy --prebuilt` when `ENABLE_AUTH=true`"; "Neon is the default Postgres for Next.js 16 projects"; "TestFlight upload uses ASC API key from `~/.appstoreconnect/private_keys/`"; "User prefers zero-dep scripts over package additions".
- Structure: one file per fact/lesson/tool-discovery. Index in `~/.build-loop/memory/MEMORY.md` (line-per-entry: `- [Title](file.md) — hook`).
- Types: `tool`, `deployment`, `library-choice`, `user-preference`, `pattern`.

**Project memory**: `<project>/.build-loop/memory/`

- Applies only to the current project.
- Examples: "This app's design system lives in `src/styles/tokens.css`, not Tailwind"; "Routes under `/admin/` require `requireAdmin()` guard"; "The `custom_themes` table has a user_id VarChar bug from 2026-04-13 — see migration note".
- Same structure as global; index in `.build-loop/memory/MEMORY.md`.
- Types: `design`, `convention`, `gotcha`, `decision`, `contract`.

### Routing rule (always ask this question)

**"Would this apply to a different project?"**

- **Yes** → global (`~/.build-loop/memory/`). Deployment tools, library choices, general user preferences, reusable patterns.
- **No** → project (`.build-loop/memory/`). Design tokens, internal APIs, project-specific gotchas, per-repo conventions.
- **Ambiguous** → ask the user once, then save. Don't guess.

### When to write memory

- User states a preference or convention: save immediately.
- A build surfaces a new tool/library/deployment pattern worth reusing: save after Review-F.
- A project-specific gotcha or decision emerges: save during Review-F Report.
- Do NOT save: ephemeral task details, things already derivable from code or git log, state that changes per build.

### When to read memory

- Always during Phase 1 ASSESS.
- Before deploying: check global deployment memory.
- Before UI work: check project design memory.
- Before adopting a new library: check global library-choice memory.

## Cross-session memory propagation + provenance schema (multi-process / multi-host)

Multiple build-loop sessions can run concurrently. Two scripts own the cross-session model end-to-end:

- `scripts/memory_writer.py` — canonical WRITER. Adds provenance frontmatter and appends to the index in one atomic operation.
- `scripts/memory_index.py` — append-only discovery log at `~/.build-loop/memory/INDEX.jsonl`.

### Provenance frontmatter (every memory file)

```yaml
---
name: <slug>
description: <one-line summary>
type: tool | deployment | library-choice | user-preference | pattern | feedback | reference | design | convention | gotcha | decision | contract
source_repo: "<git remote url or null>"
source_workdir: "<abs path>"
source_run_id: "run_<UTC>_<hash>"
source_host: "claude_code | codex | gemini | other"
cross_repo_validated: false          # flips to true once a DIFFERENT repo applies it
applied_in_repos: []                  # appended entries: {repo, workdir, run_id, applied_at}
created_at: "ISO8601 UTC"
last_updated_at: "ISO8601 UTC"
---
```

### Writer side — use memory_writer.py, never write memory files directly

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_writer.py write \
  --file "<rel-path>" \
  --name "<slug>" \
  --description "<one-line>" \
  --type feedback \
  --run-id "$RUN_ID" \
  --workdir "$PWD" \
  --host claude_code \
  --body-file /tmp/memory-body.md
```

The writer auto-detects `source_repo` from the workdir's git remote, appends a row to `INDEX.jsonl`, and (on update) preserves `created_at` + `applied_in_repos` so cross-repo validation history survives edits.

### Reader side — surface peer writes via INDEX.jsonl

Between phases (or at every M2 heartbeat), tail since your last check:

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_index.py tail \
  --since "$LAST_INDEX_CHECK_TS" \
  --exclude-run-id "$RUN_ID" \
  --json
```

For each row:
1. Read the underlying memory file.
2. If `source_workdir` ≠ this `$PWD` AND `source_repo` ≠ this repo's git remote — tag `[CROSS-REPO — requires scrutiny]` in the phase brief.
3. Surface to the user with the memory's `description` field as the hook.

### Trust gradient — mark-applied flow

When a memory written elsewhere is successfully applied in the current repo, record it:

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_writer.py mark-applied \
  --file "<rel-path>" \
  --applying-repo "$THIS_REPO_REMOTE" \
  --applying-workdir "$PWD" \
  --applying-run-id "$RUN_ID"
```

Appends to `applied_in_repos[]` (deduped by `(repo, workdir)`) and flips `cross_repo_validated` to `true` once at least one applying repo differs from the source. Memories with `cross_repo_validated: true` AND `len(applied_in_repos) >= 2` have earned higher trust — independently verified to hold across distinct repos. Surface that distinction in Phase 1 Assess briefs as `[VALIDATED — applied in N repos]`.

### Migration — existing memory files

`memory_writer.py migrate` is an idempotent backfill that adds provenance frontmatter to existing memory files. Safe to re-run; skips any file that already has all required provenance keys.

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_writer.py migrate \
  --run-id "$RUN_ID" \
  --workdir "$PWD" \
  --host claude_code \
  --dry-run    # inspect first; remove the flag to apply
```

Run once after this version of build-loop is installed; the migration completes immediately for memory dirs of ordinary size (the user's global memory at ~80 files migrates in well under a second).

### Concurrency

- `memory_writer.py write` — atomic tmpfile + os.replace; the memory file IS the lock.
- `memory_index.py append` — `fcntl.flock(LOCK_EX)` on `INDEX.jsonl.lock`; multi-writer safe across hosts.
