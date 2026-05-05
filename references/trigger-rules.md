# Trigger Rules — orchestrator reference

Loaded by the build-orchestrator at Phase 1 Assess to set boolean flags under `.build-loop/state.json.triggers`. See `skills/build-loop/SKILL.md` §Trigger Conditions for the canonical rule set.

## Triggers

Scan the goal text and the set of files the plan will touch, then set:

- **`structuredWriting`** (pyramid-principle): user-visible copy, README, CHANGELOG, docs, PR description, status update, exec summary, information architecture.
- **`promptAuthoring`** (prompt-builder): product LLM prompts, agent instructions, eval judges, semantic-search query rewriting, RAG prompts.
- **`promptEditingExisting`** (prompt-builder + user confirmation): editing a prompt that already ships in the product.
- **`riskSurfaceChange`** (security-methodology + security-reviewer): the build introduces or modifies any of —
  - a new tool / MCP server / plugin / skill,
  - a new LLM call or shipped prompt,
  - persistent agent memory or vector store,
  - an auth / authz / identity / permission boundary,
  - an external API call,
  - or handling of new user-data classes (PII, financial, health, credentials, regulated).

  Flip is sticky for the whole build. Routes Phase 4 Review-A to dispatch `security-reviewer` after `sonnet-critic`, and arms plan-verify rule 10 (`risk-surface-change-without-threat-model`) at Phase 2.

## Sub-routers (Phase 1 Assess)

Set `uiTarget`, `platform`, `migrationSource` per `skills/build-loop/SKILL.md` §Capability Routing.

## Judgment: prompt-builder vs inline prompt

Use prompt-builder when the prompt is **load-bearing** — its quality directly affects user value and a regression would be visible. Inline-prompt when the call is one-shot orchestrator-to-Claude and the prompt is transient (e.g. extract this list, summarize that diff). When in doubt, default to inline; switch to prompt-builder if the same prompt shape is being reused or hand-authored more than once.
