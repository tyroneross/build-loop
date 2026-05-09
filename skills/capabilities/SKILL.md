---
name: build-loop:capabilities
description: Invoked by Phase 1 Assess to populate `state.json.activeCapabilities[<phase>]` with ≤8 relevant entries via plugin-surface collapse + trigger-aware demotion. Keeps the orchestrator below the empirical tool-selection ceiling. Not user-callable; orchestrator-internal.
model: sonnet
tools: ["Read", "Bash"]
---

# Capability shortlist

Anthropic's Tool Search guidance recommends ≤8 candidates per dispatch. The build-loop repo currently exposes ~113 surfaces (20 agents + 34 skills + 14 commands + 5 hooks + 1 MCP server + ~39 scripts). Without filtering, the orchestrator chooses from a haystack on every phase.

This skill is a deterministic, structured matcher: **(phase, intent text) → ≤8 capability entries**, drawn from the registry written by `scripts/build_capability_registry.py`.

## Inputs

| Input | Type | Required | Notes |
|---|---|---|---|
| `phase` | int (1–6) | yes | Phase number from build-loop's 5-phase loop (+1 Learn). |
| `intent` | string | yes | Free-text description of the goal/task for this phase. |
| `kind` | optional list | no | Filter by `agent | skill | command | hook | mcp_tool | script`. |
| `workdir` | optional path | no | Repo root containing `.build-loop/capability-registry.json`. Defaults to `$PWD`. |

## Phase → category routing

Phases bias the shortlist toward categories the loop typically uses there:

| Phase | Primary categories | Secondary |
|---|---|---|
| 1 Assess | architecture, planning, memory, observability | meta |
| 2 Plan | planning, architecture, validation | meta |
| 3 Execute | execution, debugging, ux-ui, deployment | testing |
| 4 Review | validation, debugging, ux-ui, optimization | testing |
| 5 Iterate | debugging, execution, validation | architecture |
| 6 Learn | meta, memory, optimization | validation |

When `phase` is outside 1–6, fall back to scoring purely on intent keyword matches.

## Procedure

1. Read `<workdir>/.build-loop/capability-registry.json`. If it doesn't exist, run:
   ```bash
   python3 <workdir>/scripts/build_capability_registry.py --workdir "<workdir>"
   ```
   Then re-read.
2. Lowercase the `intent` string. Tokenize on whitespace and punctuation.
3. For each registry entry, compute a relevance score:
   - **+5** for every intent token that appears in `name`, `description`, or `triggers[]`.
   - **+3** if the entry's `category` is in the phase's primary list.
   - **+1** if the entry's `category` is in the phase's secondary list.
   - **+1** if the entry's `tier` is `sonnet` or `opus` (preferred over `n/a` for substantive work).
4. Apply optional `kind` filter.
5. Sort by score descending, then by `name` ascending for stable tie-breaks.
6. Return the top 8 with reason tokens (which intent words and which categories matched).

## Output shape

```json
{
  "phase": 1,
  "intent": "<echoed>",
  "shortlist_size": 8,
  "registry_total": 113,
  "results": [
    {
      "name": "<capability name>",
      "kind": "agent|skill|command|hook|mcp_tool|script",
      "category": "<routing label>",
      "score": 12,
      "reasons": ["matched_intent_token: scan", "matched_category: architecture"],
      "source_path": "agents/architecture-scout.md",
      "description": "<truncated to 240 chars>"
    }
  ]
}
```

`shortlist_size` is always ≤ 8 (the registry's own size cap matches Anthropic's Tool Search guidance). When fewer than 8 entries score above 0, return all that scored. When zero match, return the top 4 by phase-category match alone — the orchestrator should never receive an empty shortlist if the registry is non-empty.

## Caching

Write the result to `<workdir>/.build-loop/state.json.activeCapabilities[]` with `{phase, intent, shortlist: [...], generated_at}`. The orchestrator's Phase 1 Assess step reads this directly without re-running the skill if `intent` and `phase` haven't changed.

## What this skill does NOT do

- Execute any of the capabilities it surfaces. It returns a shortlist; the orchestrator dispatches.
- Mutate any source file. Read-only on the repo; write-only to `state.json` (single field).
- Network-call. Runs purely against the local registry.
- Disambiguate between two capabilities with the same name in different `kind` namespaces — both are surfaced and the orchestrator decides.
