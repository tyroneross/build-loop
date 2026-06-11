<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Memory — Global and Project-Scoped (full protocol)

> Loaded from `skills/build-loop/SKILL.md` summary. Contains the full memory system: global vs project stores, routing rules, and read/write policies.

## Memory — Global and Project-Scoped

Build-loop maintains one canonical long-term memory store at `~/dev/git-folder/build-loop-memory/`. Every build reads canonical indexes/folders; writes go to exactly one lane based on scope.

### Recall-optimized memory discipline

Canonical reference: `build-loop-memory/references/2026-06-11-memory-discipline-prompt.md` (`version: 2026-06-11.1`). Apply it to every non-trivial memory-relevant read or write.

Operational contract:

- Recall first for significant repo work, debugging, planning, and any memory write. Read the store-root `INDEX.md` first, then project context such as `projects/<slug>/context/CONTEXT.md` and generated `CURRENT.*`, plus `constitution.md` / `MEMORY.md` where present. Search `indexes/INDEX.jsonl`, scan `chronology.jsonl`, read the matching lane, and verify any remembered file/flag/API/script still exists before relying on it.
- Write only durable facts that aid future recall: decisions + rationale, lessons, reusable references, gotchas, experiment results, product opportunities, and durable operational patterns. Do not write restated code, git-derivable facts, transient status, or handoff-only state.
- Before writing, search `indexes/INDEX.jsonl` for an existing slug/title, update instead of duplicating, then check `indexes/duplicates.jsonl` after indexing. Use title and `description` as the recall hooks.
- Do not hand-write project decisions. Use `scripts/write_decision/__main__.py`; it writes the decision lane and updates that lane's `INDEX.md` / update ledger. Generated master-index reachability is still incomplete for new `projects/<slug>/decisions/` files, so verify decisions through `memory_facade` or the decision lane until the scanner/map split is reconciled.
- Current reference gap: `memory_writer.py` has `research` as a project sublane but not `references`; `reference_capture` writes to `projects/<slug>/research/`, while `build-loop-memory/scripts/rebuild_memory_indexes.py` scans `references/` and not `research/`. For generated-index recall today, write `type: reference` content under `projects/<slug>/lessons/references/`, or update both writer and indexer to agree on `references` or `research`.
- After any memory write, run the relevant host index/check step when mutation is in scope and verify the entry is reachable from the proper recall surface: `INDEX.jsonl` for generated-index lanes, decision lane/index or `memory_facade` for decisions, or the host system's equivalent.

**Cross-project memory**: `build-loop-memory/lessons/` plus the sibling top-level lanes `design/`, `debugging/`, and `product/`

- Applies across every project this user builds.
- Examples: "Deployment to Vercel uses `vercel deploy --prebuilt` when `ENABLE_AUTH=true`"; "Neon is the default Postgres for Next.js 16 projects"; "TestFlight upload uses ASC API key from `~/.appstoreconnect/private_keys/`"; "User prefers zero-dep scripts over package additions".
- Structure: one file per fact/lesson/tool-discovery. Generated recall indexes live in `build-loop-memory/indexes/`.
- Types: `tool`, `deployment`, `library-choice`, `user-preference`, `pattern`.

**Project memory**: `build-loop-memory/projects/<slug>/` (slug derived via `scripts/_paths.derive_slug_from_cwd`)

- Applies only to the current project.
- Examples: "This app's design system lives in `src/styles/tokens.css`, not Tailwind"; "Routes under `/admin/` require `requireAdmin()` guard"; "The `custom_themes` table has a user_id VarChar bug from 2026-04-13 — see migration note".
- Same lane structure as top level: `decisions/`, `lessons/`, `debugging/`, `design/`, `product/`, and related domain folders.
- Types: `design`, `convention`, `gotcha`, `decision`, `contract`.

### Routing rule (always ask this question)

**"Would this apply to a different project?"**

- **Yes** → top-level canonical lane (`build-loop-memory/lessons/`, `design/`, `debugging/`, or `product/`). Deployment tools, library choices, general user preferences, reusable patterns.
- **No** → project canonical lane (`build-loop-memory/projects/<slug>/...`). Design tokens, internal APIs, project-specific gotchas, per-repo conventions.
- **Ambiguous** → ask the user once, then save. Don't guess.

### Artifact lanes & segmentation (issues / backlog / lessons)

Three work/knowledge artifact types, each with a clear WRITE → READ → TRACK lane. **Every lane is repo-segmented; the segmentation is mechanical, not discretionary** — so work on repo X never reads or writes repo Y's items.

| Artifact | Write (where) | Read / Track | Lifetime |
|---|---|---|---|
| **issues** | `<repo>/.build-loop/issues/<id>.md` — current-run bugs | Phase 5 Iterate drains them; repo-local so inherently scoped | short-lived (resolve → delete) |
| **backlog** | durable: `build-loop-memory/projects/<slug>/backlog.md` (slug folder = repo scope); active: `<repo>/.build-loop/backlog/<id>.md` | read before planning self-work; Phase 5 drains active items | long-lived |
| **backlog-archive** | `build-loop-memory/projects/<slug>/backlog-archive.md` | closed/moved/superseded items land here **with rationale + ref** — never deleted silently | durable |
| **lessons** | `projects/<slug>/lessons/` (project) OR top-level `lessons/` (cross-project, stored `_unscoped`) — via `memory_writer.py` | `context_bootstrap` recall scopes to `(slug OR _unscoped)` — never other projects | durable |

**Segmentation contract (binding):**
- The **slug folder** (`projects/<slug>/`) is the repo key; the **`repo` + `branch` frontmatter** on each issue/backlog item is the explicit scope tag (template: `templates/backlog-item.md`). Both must agree.
- When working repo X on branch B, **read and write only** items where `repo == X` (and `branch == B` or unscoped). A cross-repo item discovered mid-work is recorded in **its** repo's scope, **never** the current repo's tracker.
- **No shared/freeform cross-repo trackers.** (The retired `OPEN-ITEMS.md` was exactly this anti-pattern — one file that accreted rows from unrelated app repos into build-loop's scope. Replaced by the slug-segmented `projects/<slug>/backlog.md`.)
- Reads are already enforced: `context_bootstrap` queue reads are repo-local `.build-loop/`, and lessons recall passes the resolved `project` so the query scopes to `(project OR _unscoped)` — `project=None` (all-projects) is never used for current-work context.

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
- `scripts/memory_index.py` — append-only discovery log in the selected canonical lane.
- `scripts/memory_update_ledger.py` — global append-only audit/freshness log for the whole configured memory root.

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

### Writer side — use the canonical writer for normal memory writes

**Top-level (cross-project) write** — `--scope top-level` routes to `build-loop-memory/lessons/` (or a sibling lane when `--file <lane>/x.md` is used):

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_writer.py \
  --scope top-level \
  write \
  --file "<rel-path>" \
  --name "<slug>" \
  --description "<one-line>" \
  --type feedback \
  --run-id "$RUN_ID" \
  --workdir "$PWD" \
  --host claude_code \
  --body-file /tmp/memory-body.md
```

**Project-scoped write** — `--scope project --project <slug>` routes to `build-loop-memory/projects/<slug>/lessons/` (or a sublane when `--file <sublane>/x.md` is used):

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_writer.py \
  --scope project --project "$PROJECT_SLUG" \
  write \
  --file "<rel-path>" \
  --name "<slug>" \
  --description "<one-line>" \
  --type gotcha \
  --run-id "$RUN_ID" \
  --workdir "$PWD" \
  --host claude_code \
  --body-file /tmp/memory-body.md
```

The writer auto-detects `source_repo` from the workdir's git remote, appends a row to the lane-local `INDEX.jsonl`, appends a row to the global update ledger at `indexes/updates.jsonl`, and (on update) preserves `created_at` + `applied_in_repos` so cross-repo validation history survives edits. Direct writes are repair/fallback work only: use them only when no canonical writer exists or the current task is explicitly a memory-system repair, then run the host index/check step and verify reachability.

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

### Store side — global update ledger

Every canonical memory mutation should also append one JSONL row to:

```
<memory-root>/indexes/updates.jsonl
```

This is the store-wide ledger. It is not a replacement for lane-local `INDEX.jsonl`; the two logs have different jobs:

| Log | Scope | Primary job |
|---|---|---|
| `<lane>/INDEX.jsonl` | One memory lane | Peer discovery inside that lane |
| `indexes/updates.jsonl` | Whole memory root | Audit trail, freshness baseline, repair inventory |

Row schema:

```json
{
  "ts": "2026-06-01T12:00:00Z",
  "schema_version": 1,
  "event_id": "<sha256-prefix>",
  "project": "build-loop",
  "lane": "decisions",
  "action": "write",
  "path": "projects/build-loop/decisions/0001-example.md",
  "writer": "write_decision.py",
  "run_id": "run_...",
  "source_repo": "<git remote or omitted>",
  "source_workdir": "<absolute workdir or omitted>",
  "source_commit": "<repo HEAD represented by this memory update>",
  "source_host": "codex",
  "memory_id": "0001",
  "summary": "Short human hook",
  "sha256": "<content hash when available>",
  "metadata": {}
}
```

`memory_writer.py`, `write_decision.py`, and `append_milestone.py` emit this ledger row automatically. Direct writes to memory files should be treated as legacy or repair work because they bypass provenance, discovery, and freshness.

CLI:

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/memory_update_ledger.py tail \
  --project "$PROJECT_SLUG" \
  --limit 20 \
  --json
```

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
- `memory_update_ledger.py append` — `fcntl.flock(LOCK_EX)` on `updates.jsonl.lock`; append-only and multi-writer safe.

## Append-only milestones (anti-rewrite-drift)

### The problem this solves

"Current state" files that are rewritten in place rot: the writer overwrites without fully reading, summaries drift from reality, and no one can tell which run produced a given snapshot. The fix is append-only by construction — a log that can only grow forward.

### What gets appended and when

Every build-loop run appends a single milestone record at **Review-G** via `scripts/append_milestone.py`. Each record captures what shipped and the repo HEAD sha at write time.

JSONL contract (frozen — sibling staleness-check reads this):

```
<memory-root>/projects/<slug>/milestones.jsonl
```

Each line:

```json
{"ts": "2026-05-30T12:00:00Z", "commit": "<sha>", "repo": "<dir-name>", "summary": "<what shipped>", "run_id": "<id|null>"}
```

### How to append

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/append_milestone.py \
  --workdir "$PWD" \
  --summary "feat: shipped auth + dashboard" \
  --run-id "$RUN_ID" \
  --json
```

`--commit` and `--project` override the defaults (git HEAD and slug derived from `--workdir`). `--memory-root` overrides the default `~/dev/git-folder/build-loop-memory`.

Output: `{"appended": true, "path": "...", "line": "..."}` on success; `{"appended": false, "reason": "..."}` on fail-soft (non-git workdir, unwritable root). Exit 0 in both cases.

Idempotency: if the last line already has the same `commit` AND `summary`, the call is a no-op. Safe to re-run on retry.

### The core principle: pointer not duplicate

The milestone log is the **durable, never-rewritten** record of project progress. Other memory files (`lessons/`, `decisions/`, etc.) remain the authoritative content store. The milestone is a pointer — "at this commit, this run shipped this" — not a duplicated copy of their content.

> The rewrite-in-place pattern is what rots. Append-only logs + pointers resist drift by construction: you can always `tail` to see the latest state, `grep` to find when something shipped, and the sibling staleness-check can compare the latest milestone commit against the current HEAD to detect stale memory instantly.

Decisions use the existing `decisions/` lane (also append-only files, one file per decision). The milestone log adds the run-level "what shipped" layer that `decisions/` doesn't track.

### Staleness detection

`memory_staleness_check.py` now prefers the latest `source_commit` in `indexes/updates.jsonl` for the current project, then falls back to the latest milestone's `commit` field. It compares that baseline against `git rev-parse HEAD` in the project workdir. If HEAD has moved past the last memory update by the configured commit threshold, the project's memory is potentially stale and Phase 1 Assess should flag it.

Impact:

- A decision, lesson, migration, mark-applied, or milestone can refresh the memory baseline when it records `source_commit`.
- Older memory stores without `indexes/updates.jsonl` keep working because milestone fallback is unchanged.
- A stale warning means "no durable memory update has been recorded for this project at or near HEAD"; it does not prove every individual memory file is stale.

### Concurrency

`fcntl.flock(LOCK_EX)` on `milestones.jsonl.lock` — same pattern as `memory_index.py`. Multi-writer safe.
