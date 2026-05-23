<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Recall integration — Phase 1 Assess

`scripts/recall.py` is the retrieval entry point for repo-local episodic
memory. It runs hybrid search (cosine + pg_trgm + full-text) over
`agent_memory.<schema>.semantic_facts` and `episode_events`, returning a
compact text summary suitable for direct injection into Phase 1 Assess
context.

## Why this exists

Phase 1 Assess today reads `INDEX.md`, `MEMORY.md`, recent
`runs[]` and recent feedback files wholesale (~10–20K tokens). For
small projects this is fine; once the corpus crosses ~50 decisions it
dominates Assess context. `recall.py` replaces the wholesale read with
a query-specific top-K plus neighbor expansion (~500–1500 tokens).

The cost is one Ollama embedding call (~30 ms locally) plus one psql
hybrid search (~10–50 ms with HNSW). Both are local; no cloud cost.

## Status: documented but NOT wired (per brief)

This document specifies the integration. **The orchestrator does NOT
call `recall.py` yet.** Wiring Phase 1 Assess is a deliberate follow-up
after pilot data shows the recall quality is high enough on real
queries. The planned wiring is below for reference.

## Planned wiring (do NOT implement until pilot data is in)

In `agents/build-orchestrator.md` Phase 1 Assess, replace the wholesale
"load `~/.build-loop/memory/MEMORY.md` (global) and
`.build-loop/memory/MEMORY.md` (project) if they exist" line with:

```bash
# Build-relevant recall: the goal text is the query.
python3 scripts/recall.py \
  --query "$GOAL_TEXT" \
  --limit 8 \
  --confidence-floor confirmed \
  --neighbor-window 3 \
  > .build-loop/recall.md
```

The orchestrator then loads `.build-loop/recall.md` instead of the full
INDEX/MEMORY tree. The unfiltered files remain available for the user
to inspect; recall.md is the agent-facing summary.

## Trigger conditions

`recall.py` makes sense when:

- The agent is starting a new build/turn that needs prior context.
- The query is well-defined (a specific topic or symptom).

`recall.py` is NOT a substitute when:

- The agent needs to enumerate ALL prior decisions on a topic
  (use `INDEX.md` directly with `--confidence-floor inferred` to widen).
- Auto-capture is in flight and `_candidates.jsonl` hasn't been
  consolidated yet (Phase 4 concern).
- The DB is unreachable (recall returns exit 2; orchestrator should
  fall back to `INDEX.md` read).

## Invariants

1. The DB is the index, not the source of truth. `recall.py` returning
   nothing useful does NOT mean the answer isn't in the corpus —
   re-run `sync_db_from_files.py --rebuild` and try again. If recall
   still misses, the underlying decision is missing from `.episodic/`.
2. The summary is bounded by `--char-budget` (default 8K chars,
   ~1500 tokens). Truncation is explicit ("[truncated to char budget]").
3. Confidence floor is `confirmed` by default. Pass `--confidence-floor
   inferred` to include lower-trust auto-captured decisions (Phase 3).

## Pilot exit criteria

Wire into Phase 1 Assess after:

- 30+ decisions have been migrated/captured into the corpus
- Manual sampling shows top-3 recall quality ≥ 80% on goal-shaped
  queries
- Token budget proves to be ≤ 1500 tokens on realistic queries
