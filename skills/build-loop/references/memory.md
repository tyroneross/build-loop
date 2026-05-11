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

## Cross-session memory propagation (multi-process / multi-host)

Multiple build-loop sessions can run concurrently. Each global memory write appends one row to `~/.build-loop/memory/INDEX.jsonl` via `scripts/memory_index.py`. Sibling sessions can `tail` this log between phases to see new peer learnings as they land — not just at session start.

**Writer side** — whenever you write/update/delete a file under `~/.build-loop/memory/`, immediately append a row:

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_index.py append \
  --run-id "$RUN_ID" --action write \
  --file "<rel-path>" \
  --source-host claude_code \
  --source-workdir "$PWD"
```

`source-repo` and `source-workdir` are optional but recommended — they let downstream readers in a DIFFERENT repo tag the memory as `[CROSS-REPO — requires scrutiny]` before applying.

**Reader side** — between phases (or at every M2 heartbeat), tail since your last check:

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_index.py tail \
  --since "$LAST_INDEX_CHECK_TS" \
  --exclude-run-id "$RUN_ID" \
  --json
```

For each row:
1. Read the underlying memory file.
2. If the memory's `source_workdir` ≠ this `$PWD` AND `source_repo` ≠ this repo's git remote — tag the surfaced snippet `[CROSS-REPO — requires scrutiny]` in the phase brief.
3. Otherwise — surface as a normal peer signal.

The full cross-repo trust gradient (validation tracking via `cross_repo_validated` + `applied_in_repos[]` frontmatter) lands in PR-β (memory provenance schema). This commit only ships the discovery log.

Concurrency: append uses `fcntl.flock(LOCK_EX)` on a sidecar `.lock` file — multi-writer safe.
