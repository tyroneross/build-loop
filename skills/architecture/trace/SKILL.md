---
name: build-loop:architecture-trace
description: Trace data flow through the architecture — follow a component's connections from input to output. Build-loop's native trace, copied from NavGator's CLI command (no canonical SKILL.md exists upstream).
version: 0.1.0
user-invocable: false
source: NavGator/commands/trace.md
source_hash: d1a62f22ebcf3f19e2f9d52c6df565a4ba5ed53a676b5e02e43e670273f6d9f8
---

# Architecture Data-Flow Trace

Follow data flow through the architecture using the NavGator `mcp__plugin_navgator__trace` MCP tool. Trace pipelines forward (cron → route → service → DB → queue → LLM) or backward (consumer → producer).

> **Divergence note**: NavGator does not ship a discrete SKILL.md for trace — only a slash command (`commands/trace.md`) that wraps the MCP tool. This skill encodes the same workflow as a build-loop-native skill.

## When to Activate

- Phase 1 Assess: trace pipelines for cron jobs and queue producers when prompts or LLM calls are in scope
- Phase 5 Iterate: trace forward from a failing endpoint or backward from a downstream symptom to pinpoint the broken link
- User asks "how does X data flow", "what feeds X", "what does X feed"
- Component is a cron job, API route, database model, or queue

## Pre-flight

1. If `.navgator/architecture/index.json` does not exist → no-op with `NavGator: no architecture snapshot found — skipping trace`. Recommend `build-loop:architecture-scan`.

## Trace Workflow

1. Run `mcp__plugin_navgator__trace` with the component name
2. If the component is a cron job or API route, trace forward to show the full pipeline
3. If it's a database model or queue, trace both directions to show producers AND consumers
4. Present the trace as a readable pipeline:

```
/api/cron/refresh-rss [Vercel cron]
  → route.ts [backend]
  → rss-ingestion-service [service]
  → Article [database]
  → search-enhancement-queue [queue]
  → OpenAI [LLM provider]
```

5. Flag any anomalies in the trace (dead ends, duplicate consumers, missing connections)
6. If trace returns 0 paths, suggest the component might be orphaned (run `build-loop:architecture-dead`) or data may need refreshing (`build-loop:architecture-scan`)

## Tool Options

- `direction: "forward" | "backward" | "both"` — one-way or bidirectional trace
- `production: true` — filter out test/script connections
- `max_paths: N` — cap path enumeration on highly-connected components

## Output

Write trace summary into `.build-loop/state.json.architecture.trace[<component>]` with `{paths_count, layer_chain, anomalies[]}`.

## Sibling Skills

- `build-loop:architecture-scan` — refresh data first
- `build-loop:architecture-impact` — blast-radius alongside trace
- `build-loop:architecture-dead` — confirm orphan if trace returns 0 paths
- `build-loop:architecture-review` — full integrity review

*Source: NavGator CLI command (`commands/trace.md`). The canonical implementation is the MCP tool, not a skill file. Drift-checked by `build-loop:sync-skills`.*
