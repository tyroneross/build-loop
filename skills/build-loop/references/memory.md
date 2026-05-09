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
