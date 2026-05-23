---
name: knowledge-review
description: Repo-local episodic memory review surface. Use when the user asks to "review my decisions", "show review queue", "check decision rot", "list open conflicts", "find stale procedures", or runs `/knowledge:review`. Read-only — never auto-resolves.
when_to_use: |
  - User runs `/knowledge:review` or asks to surface review-needing items
  - User wants to see decisions older than the staleness threshold
  - User wants to see `_review/` queue items awaiting promotion
  - User wants to see open `fact_conflicts` rows
  - User wants to see procedures whose `depends_on` symbols are missing from the codebase
namespace: .episodic/decisions/_review/, .episodic/decisions/, .procedural/, agent_memory.<schema>.fact_conflicts
companion_scripts:
  - scripts/knowledge_review.py — aggregates all four sections into a markdown report
  - scripts/detect_decision_rot.py — drives the rot section
  - scripts/procedural_governance.py — drives the stale-procedures section (validate-symbols mode)
  - scripts/consolidate_memory.py — referenced as the next-step action when surface items accumulate
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross | SPDX-License-Identifier: Apache-2.0 -->

# knowledge-review — surface review-needing items

`/knowledge:review` is the read-only review surface for the four-memory-types
framework (Working / Episodic / Semantic / Procedural). It does NOT modify
any data; it lists what humans need to decide on.

The full design lives at
`~/dev/research/topics/repo-episodic-memory-framework/repo-episodic-memory-framework.md`
(see §11–§14).

## What it surfaces

```
┌────────────────────────────────────────────────────────────────┐
│ knowledge_review.py — four sections                            │
├────────────────────────────────────────────────────────────────┤
│ 1. Review queue        — `.episodic/decisions/_review/`        │
│                          Tier-3 captures awaiting promotion    │
│ 2. Decision rot        — decisions older than threshold        │
│                          (default 90 days)                     │
│ 3. Open conflicts      — fact_conflicts rows resolved=FALSE    │
│                          (skipped when DB unavailable)         │
│ 4. Stale procedures    — depends_on symbols missing from code  │
└────────────────────────────────────────────────────────────────┘
```

Each item carries a suggested action. The user takes the action via
existing scripts:

| Section | Action | Script |
|---|---|---|
| Review queue | promote | `mv .episodic/decisions/_review/<file> .episodic/decisions/` then `python3 scripts/sync_db_from_files.py` |
| Review queue | dismiss | `rm .episodic/decisions/_review/<file>` |
| Decision rot | mark-validated | edit frontmatter to add `last_validated: YYYY-MM-DD` |
| Decision rot | supersede | `python3 scripts/supersede_decision.py --old-id <id> ...` |
| Decision rot | revoke | `python3 scripts/revoke_decision.py --id <id> --reason ...` |
| Open conflicts | resolve | `UPDATE` one row in `semantic_facts` to `status='superseded'`; set `fact_conflicts.resolved=TRUE` |
| Stale procedures | re-verify | edit `depends_on[].last_verified` after confirming symbol still works |
| Stale procedures | revoke | move `.procedural/<name>/` to `.procedural/_archive/<name>/` |

## How to invoke

Slash command:

```
/knowledge:review [--rot-threshold-days N] [--no-db]
```

Direct script:

```bash
python3 scripts/knowledge_review.py \
  --workdir "$PWD" \
  --rot-threshold-days 90 \
  --symbol-paths scripts,src,app
```

Useful flags:

- `--rot-threshold-days <N>` — change the staleness threshold (default 90)
- `--symbol-paths <csv>` — codebase paths to grep for `depends_on` symbols
- `--no-db` — skip the conflicts section (faster; useful when DB is down)
- `--schema <name>` — Postgres schema to query (default `build_loop_memory`)

## Cross-reference: consolidation

When the review queue grows large, run consolidation to merge
mature candidates into `semantic_facts`:

```bash
# Inspect what would happen
python3 scripts/consolidate_memory.py --workdir "$PWD" --dry-run

# Apply
python3 scripts/consolidate_memory.py --workdir "$PWD"
```

Consolidation reads `.semantic/_candidates.jsonl` (typically populated by
the auto-capture batch sweep), dedupes against existing facts, and
records the action in `.semantic/_candidates_history.jsonl`.

## Read-only contract

This skill never:
- promotes items from `_review/` (user does the `mv`)
- mutates `last_validated` (user edits the file)
- resolves conflicts (user updates the rows)
- modifies procedure frontmatter (user edits or runs `--rewrite` on validate-symbols)

The reason: review/promotion decisions need human judgment about whether
a captured inference matches the user's intent. Auto-promotion would
poison the trusted set.
