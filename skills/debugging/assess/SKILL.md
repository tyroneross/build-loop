---
name: build-loop:debugging-assess
description: Parallel domain assessment for complex debugging symptoms — fans out database / frontend / API / performance assessors in parallel and ranks findings. Build-loop's native assessor orchestration, copied from claude-code-debugger's command (no canonical SKILL.md exists upstream).
version: 0.1.0
user-invocable: false
source: claude-code-debugger/commands/assess.md
source_hash: f97d4966e110928acea7678124aad0c421e6fc64ddf0a6f53a7e14580650307a
---

# Parallel Domain Assessment

Run multiple specialized assessor agents in parallel against a vague or multi-domain symptom, then rank findings by confidence. Native to build-loop — content adapted from `claude-code-debugger/commands/assess.md`.

> **Divergence note**: claude-code-debugger ships this as a slash command, not a discrete SKILL.md. The canonical assessor agents (`api-assessor`, `database-assessor`, `frontend-assessor`, `performance-assessor`) are bundled into build-loop under `agents/` and used directly here.

## When to Activate

- Phase 5 Iterate: after 2 consecutive same-root-cause failures in Review-B
- Symptom is vague or unclear ("app broken", "something wrong")
- Multiple domains may be involved ("search is slow and returns wrong results")
- Post-deploy regression with unknown scope
- Complex issue affecting multiple layers

## Hard Caps

- Spawn parallel assessors via `Agent` tool; obey the `~/.claude/CLAUDE.md` §Sub-Agents 4-parallel cap
- Pin model to `sonnet` for each domain assessor — at the orchestrator's Opus 4.7 tier, defaulting to `inherit` would fan out 4× Opus invocations and shred the cost ledger
- Only escalate an individual assessor to Opus if its initial output flags `confidence: low` or `needs_judgment: true`

## Workflow

1. **Detect domains** from symptom keywords:

   | Domain | Trigger Keywords |
   |--------|-----------------|
   | Database | query, schema, migration, prisma, sql, connection, constraint, index |
   | Frontend | react, hook, useEffect, render, component, state, hydration, browser |
   | API | endpoint, route, request, response, auth, 500, 404, cors, middleware |
   | Performance | slow, latency, timeout, memory, leak, cpu, bottleneck, optimization |

2. **Search memory once** — invoke `build-loop:debugging-memory` with the symptom; pass any matching incidents to each assessor as context. Don't make each assessor re-query memory.

3. **Launch assessors in parallel** with `Agent`, all in a single message:
   - `database-assessor` — queries, schema, migrations, connection issues
   - `frontend-assessor` — React, hooks, rendering, state, hydration, SSR
   - `api-assessor` — endpoints, REST/GraphQL, auth, middleware, CORS
   - `performance-assessor` — latency, memory, CPU, bottlenecks

   Pass each: `{ symptom, repro_steps, related_incidents, files_in_scope, model: "sonnet" }`.

4. **Aggregate results** — each assessor returns JSON:
   ```json
   {
     "confidence": 0.0-1.0,
     "probable_causes": ["..."],
     "recommended_actions": ["..."],
     "related_incidents": ["INC_..."]
   }
   ```

5. **Rank** by confidence, then by `len(related_incidents)`. Generate priority sequence of recommended actions.

6. **Present unified diagnosis** — top 3 actions, evidence count, escalation if all assessors return `confidence < 0.4`.

## Result Aggregation Format

```
═══ PARALLEL DOMAIN ASSESSMENT ═══
Symptom: <symptom string>
Assessors fired: database, frontend, api, performance

Top finding (confidence 0.78, database-assessor):
  Probable cause: N+1 query on Article.findMany inside getServerSideProps
  Recommended action: add `include: { author: true }` to eliminate per-row lookup
  Related incidents: INC_DB_20260315_..., INC_DB_20260201_...

Secondary finding (confidence 0.52, performance-assessor):
  Probable cause: missing index on Article.publishedAt
  Recommended action: add migration `CREATE INDEX articles_published_at`

Action sequence: 1) eliminate N+1, 2) add index, 3) re-verify latency
```

## When Assessors Disagree

If two assessors return overlapping `probable_causes` with similar confidence — flag as **multi-causal**. Both fixes may be needed. Don't pick one and discard the other.

If they disagree fundamentally (e.g., database-assessor says query, performance-assessor says rendering) — present both, ask user which path to pursue first, OR escalate to `build-loop:debugging-debug-loop` for causal-tree investigation.

## Output

Write summary to `.build-loop/state.json.debugging.assess[<symptom-hash>]`:
```json
{
  "symptom": "...",
  "assessors_fired": ["database", "frontend", "api", "performance"],
  "top_finding": { "domain": "database", "confidence": 0.78, "cause": "..." },
  "action_sequence": [...],
  "multi_causal": false
}
```

## Sibling Skills

- `build-loop:debugging-memory` — search before assessing (mandatory pre-step)
- `build-loop:debugging-debug-loop` — escalate when assessment is inconclusive
- `build-loop:debugging-store` — store the resolved incident after the recommended action lands

*Source: claude-code-debugger `commands/assess.md`. The canonical implementation is the slash command + the four bundled assessor agents. Drift-checked by `build-loop:sync-skills`.*
