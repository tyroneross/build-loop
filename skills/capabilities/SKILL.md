---
name: build-loop:capabilities
description: Invoked by Phase 1 Assess to populate `state.json.activeCapabilities[<phase>]` with ≤8 relevant entries via plugin-surface collapse + trigger-aware demotion. Keeps the orchestrator below the empirical tool-selection ceiling. Not user-callable; orchestrator-internal.
user-invocable: false
model: sonnet
tools: ["Read", "Bash"]
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Capability shortlist

Anthropic's Tool Search guidance recommends <=8 candidates per dispatch. The build-loop repo currently exposes ~112 surfaces (20 agents + 34 skills + 14 commands + 5 hooks + ~39 scripts) and no build-loop MCP server. Without filtering, the orchestrator chooses from a haystack on every phase.

This skill is a deterministic, structured matcher: **(phase, intent text) → ≤8 capability entries**, drawn from the registry written by `scripts/build_capability_registry.py`.

## Inputs

| Input | Type | Required | Notes |
|---|---|---|---|
| `phase` | int (1–6) | yes | Phase number from build-loop's 5-phase loop (+1 Learn). |
| `intent` | string | yes | Free-text description of the goal/task for this phase. |
| `kind` | optional list | no | Filter by `agent | skill | command | hook | mcp_tool | script | plugin | tool`. |
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
5. Sort by score descending; on equal score an in-repo entry ranks above an installed one, then `name` ascending.
6. Return the top `SHORTLIST_CAP` entries (`scripts/capability_shortlist.py`) with reason tokens (which intent words and which categories matched).

## Installed plugins and tools (on by default)

The registry only sees this repo. `scripts/capability_shortlist.py` also merges matches from `scripts/plugin_index.py`, a machine-wide index (`~/.build-loop/plugin-index.json`, override with `$BUILD_LOOP_PLUGIN_INDEX`) of the enabled plugins installed for Claude Code and Codex plus the command-line tools declared in `references/external-tools.json`.

- Each installed plugin or tool becomes ONE entry, kind `plugin` or `tool`, category `installed`, carrying an `invoke` field (the skill, agent, command or MCP server to call, or the binary).
- Only plugins callable on the consuming host are offered: the host comes from `scripts/host_capabilities.py` (`--host` overrides), is applied before cross-host de-duplication, and an undetected host gets tools only. Claude plugins disabled in `settings.json` `enabledPlugins`, and project-scope installs outside their project, are excluded.
- They are scored by the same intent rule, get no phase-category bonus, and lose ties to in-repo surfaces. The shortlist cap covers both.
- The plugin this repo is (build-loop itself) is skipped; its surfaces are already in the registry.
- Staleness: the fingerprint covers each host's installed-plugin file and Claude `settings.json`, each plugin's version, install path and every file extraction reads (manifest, SKILL.md, agent/command files, MCP files), the tool declaration file, each tool's `which` result, and `plugin_index.py` itself. Any change rebuilds the index; a malformed cached index is rebuilt too. Each result's surfaces are re-checked on disk before it is returned.
- How agents use the entries: `agents/implementer.md` §"Available capabilities".
- `--no-installed` turns the merge off. Standalone lookup: `python3 scripts/plugin_index.py query <words>`.

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
- Network-call. Runs purely against the local registry and the local installed-plugin index.
- Disambiguate between two capabilities with the same name in different `kind` namespaces — both are surfaced and the orchestrator decides.
