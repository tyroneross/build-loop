---
name: knowledge
description: Canonical build-loop-memory framework. Use when the user asks to "record a decision", "log an ADR", "write an MADR", "capture this choice", "regenerate the decisions index", "validate knowledge", "migrate feedback to decisions", or "recall <topic>". ALSO the read-only review surface (review mode): "review my decisions", "show review queue", "check decision rot", "list open conflicts", "find stale procedures", or `/knowledge:review`. Active durable writes go to `~/dev/git-folder/build-loop-memory`; legacy `.episodic/` paths are migration/archive inputs only.
user-invocable: true
when_to_use: |
  - User wants to record a substantive choice with rationale
  - User asks to regenerate canonical decision indexes or validate decision files
  - User asks to validate frontmatter or supersession links
  - User asks to migrate `.build-loop/feedback.md` into MADR files
  - User asks to migrate playbooks to `.procedural/`
  - User asks to recall prior decisions on a topic (Phase 2 retrieval)
  - Auto-capture (Phase 3) and consolidation (Phase 4) are NOT yet
    implemented; this skill covers Phase 1 (manual + scripted) and
    Phase 2 (Postgres + pgvector retrieval) only.
namespace: ~/dev/git-folder/build-loop-memory/ (canonical), .build-loop/events.jsonl (repo-local runtime), legacy .episodic/ (migration/archive only)
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Knowledge — Canonical Build-Loop Memory (Phases 1 + 2)

This skill is the entrypoint for the four-memory-types framework. The
full design lives at
`~/dev/research/topics/repo-episodic-memory-framework/repo-episodic-memory-framework.md`
(see §11–§14 for the four-memory-type taxonomy, extraction pipeline,
and Postgres schema). Read it before making structural changes.

## What lives where

```
~/dev/git-folder/build-loop-memory/
├── projects/<project>/decisions/   # canonical MADR decisions + INDEX.md
├── projects/<project>/lessons/     # project-specific lessons
├── lessons/                        # cross-project lessons
├── indexes/                        # generated canonical indexes
└── db/                             # Postgres helper material

<repo>/.build-loop/events.jsonl     # repo-local runtime timeline
<repo>/.episodic/                   # legacy migration/archive input only
```

## Phase 1 surface — file-only operations

| Need | Tool |
|---|---|
| Write a decision (file only) | `python3 scripts/write_decision/__main__.py …` |
| Validate frontmatter + links | `python3 scripts/validate_knowledge.py …` |
| Regenerate INDEX files | `python3 scripts/regenerate_knowledge_index.py …` |
| Migrate `feedback.md` to MADR | `python3 scripts/migrate_feedback_to_decisions.py …` |
| Migrate playbooks to procedural | `python3 scripts/migrate_playbooks_to_procedural.py …` |

## Phase 2 surface — DB-backed retrieval

`write_decision.py` dual-writes (file canonical + best-effort DB row +
embedding via `embed_backend`). DB errors do NOT fail the file write — the DB
is regenerable from files.

| Need | Tool |
|---|---|
| Initialize schema | `psql -d agent_memory -f scripts/init_agent_memory_schema.sql` |
| Recall decisions on a topic | `python3 scripts/recall.py --query "…" --limit 5 …` |
| Rebuild DB from canonical files | `python3 scripts/sync_db_from_files.py --rebuild` |

`recall.py` is the entry point for Phase 1 Assess to load only the most
relevant prior memory rather than reading INDEX.md wholesale. See
`references/recall-integration.md`.

## Authoring a decision (manual)

1. Read `.semantic/TAXONOMY.md` to pick `primary_tag`, secondary
   `tags`, `entity`, and `confidence`.
2. Run `write_decision.py` with the required flags. The script:
   - Allocates the next sequential ID (zero-padded 4-digit).
   - Writes the MADR to
     `~/dev/git-folder/build-loop-memory/projects/<project>/decisions/<canonical-id>.md` using
     `skills/knowledge/templates/madr-minimal.md` as the body
     scaffold (filled from CLI flags).
   - Regenerates the canonical decisions `INDEX.md`.
   - Appends one event to `<repo>/.build-loop/events.jsonl`.
   - Embeds the body via local Ollama and inserts a row into
     `agent_memory.<schema>.semantic_facts` (best-effort; file
     write succeeds even if DB is down).

   File writes are atomic (lock + tempfile + replace).

## Topic identity & overwrite rules

`primary_tag + entity` is the topic-identity key. Two decisions sharing
both fields describe the same topic; the writer enforces the
overwrite ladder defined in `TAXONOMY.md` §3:

- Higher confidence auto-supersedes lower (no flag needed).
- Equal confidence requires `--supersedes <id>` (explicit user direction).
- Lower confidence cannot displace higher.

Superseded decisions move to `_history/<id>-v<N>.md`; INDEX shows only
the current version.

## Validation

`validate_knowledge.py` checks:
- Frontmatter shape (required keys, value types, enum membership)
- `tags` and `primary_tag` against TAXONOMY's vocabulary
- `supersedes` / `superseded_by` links resolve to existing files

`write_decision.py` calls the validator as a pre-write gate; you can
also run it standalone over the whole tree.

## Postgres connection

DB-side scripts read connection from
`~/.config/agent-memory/connection.env` (DATABASE_URL=
postgresql://tyroneross@localhost:5432/agent_memory). Per-project schema:
this repo uses `build_loop_memory`. The schema name is configurable via
the `--schema` flag on each DB-aware script.

## Review mode (read-only)

The read-only review surface — `/knowledge:review` and asks like "review my
decisions", "show review queue", "check decision rot", "list open conflicts",
"find stale procedures" — lists the four sections of decisions/procedures
awaiting human attention (review queue, decision rot, open conflicts, stale
procedures) with a suggested action per item. It NEVER auto-resolves; humans
take the action. Full surface, invocation flags, and the consolidation
cross-reference: **`references/review-mode.md`**. (Namespace note: this surface
reviews the legacy `.episodic/` paths; active durable writes target
build-loop-memory canonical.)

## What's NOT in this skill

- Auto-capture from conversation — Phase 3 (`auto-decision-capture`
  skill, Stop hook with `scan_transcript_for_decisions.py`)
- Memory consolidation — Phase 4 (`consolidate_memory.py`)
- `derived/libraries.json` and `derived/CHANGELOG.md` generators —
  Phase 1.5 / 4

Use this skill only for Phase 1 (manual + scripted) and Phase 2
(retrieval) operations.
