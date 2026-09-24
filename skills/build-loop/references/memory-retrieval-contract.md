<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Memory retrieval contract

**One command answers "what do we already know about X".** Every agent — Claude,
Codex, a subagent, a fresh session — uses the same one, so the same question
returns the same answer.

```bash
python3 scripts/blm.py find --query "<the question, in plain words>" --project <slug>
```

Add `--tier curated` for confirmed decisions only, `--limit N` (default 5), and
`--json` for a machine envelope. Omit `--project` to search every project.

## Read the result

```
✓ Use two judges from different vendors and keep only the consensus rows as gold
    <one-line excerpt>
    <path to the evidence file>
? Recommend setting lock_timeout to ~3 seconds at session start
```

- `✓` **confirmed decision.** Cite it.
- `?` **unreviewed capture.** A tier-3 guess written automatically at session
  end that no human confirmed. Useful as a lead. Verify against code, git, or
  the user before you rely on it, and never present it as settled.

Open the evidence file when you need more than the excerpt. Search returns
~500 tokens; a whole project's decision folder is ~27,000. Search first, open
only what you need.

## Why this exists

Retrieval used to be whatever each agent happened to discover, and the paths
disagreed:

| Path | What it did |
|---|---|
| `rg` over the store (documented in `INDEX.md`) | 16.5s, unranked, exact words only |
| `blm context` | mixed confirmed decisions and unreviewed guesses with no label |
| The SQLite FTS index | fast, but documented nowhere |
| Postgres semantic search | configured on no machine and empty |
| `_review/` quarantine (12,651 records) | excluded from the database entirely |

On 2026-09-16 a Codex review found the `rg` and `blm` paths because it read
`INDEX.md`; a Claude session found the FTS index because it read source. Both
were right, and that is the failure: no contract meant no consistency. The fix
is this document plus one command that always works.

## How it answers

Two legs, fused by reciprocal rank (`scripts/memory_find.py`):

1. **Keyword** over Postgres `search_vector` (title, subject, stored excerpt).
2. **Vector** over `embedding` (1024-dim, local MLX daemon, ~40-130ms).

Neither leg alone was good enough on this store: keyword missed *"how do we
grade the classifier gold set"* (the record says "two judges from different
vendors"); vector missed *"who gets named as a product maker"*. Fusion needs a
record to win on only one axis to surface it, and rewards agreement.

**Fallback is automatic.** With no database configured, `find` uses the local
SQLite FTS index and says so in `backend`. It never returns a silent empty
result: `reasons` always names what was unavailable.

## Trust is a label, never a filter

Quarantined captures are returned and marked. Hiding them made 12,651 records
unreachable rather than untrusted, which is worse: the knowledge existed and
nothing could find it. The row carries `status='quarantined'`, and the reader
sees `?`.

## Configuration (once per machine)

`~/.config/agent-memory/connection.env`:

```
DATABASE_URL=postgresql:///agent_memory
AGENT_MEMORY_SCHEMA=build_loop_memory
```

Without it, `find` still works on the FTS fallback. `find` also probes for the
schema that actually holds rows rather than trusting a configured-but-empty
one — the silent-empty failure this contract exists to end.

## Keeping the index current

```bash
python3 scripts/sync_db_from_files.py --workdir <memory-store-root> --schema build_loop_memory
```

Batched: ~20 records/second (the whole store in about 10 minutes; per-record
embedding was 14x slower). Re-running is idempotent. `--rebuild` truncates
first, needed after a change to how rows are keyed. `--no-review` skips
quarantine captures.
