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

  **Constitution auto-infer (NEW 2026-05-12, plan §12.7 P4)** — beyond the enumeration above, `riskSurfaceChange` is auto-set to `true` whenever the goal text, plan body, or planned `filesTouched` overlap any rule in `~/.build-loop/memory/constitution.md`. Run the deterministic detector once after constitution load:

  ```
  python3 ${CLAUDE_PLUGIN_ROOT}/scripts/infer_risk_surface.py --workdir "$PWD" --json
  ```

  Detector reads `state.json.constitution.loadedRuleIds[]` + `state.json.goal` + `.build-loop/plan/plan.md` (if present) + planned files, returns `{risk_surface_change: bool, matched_rules: [...], evidence: [...]}`. Merge `risk_surface_change: true` from the detector into the existing triggers logic — never downgrade a manual `true` to `false`. The detector closes the §11.4 Sim G gap where auth-touching diffs shipped without security-reviewer firing because the manual flag was missed.

  Flip is sticky for the whole build. Routes Phase 4 Review-A to dispatch `security-reviewer` in parallel with `commit-auditor` at `scope: "build"` (replaces retired `sonnet-critic` per plan §15.1), and arms plan-verify rule 10 (`risk-surface-change-without-threat-model`) at Phase 2.

## Sub-routers (Phase 1 Assess)

Set `uiTarget`, `platform`, `migrationSource` per `skills/build-loop/SKILL.md` §Capability Routing.

## Judgment: prompt-builder vs inline prompt

Use prompt-builder when the prompt is **load-bearing** — its quality directly affects user value and a regression would be visible. Inline-prompt when the call is one-shot orchestrator-to-Claude and the prompt is transient (e.g. extract this list, summarize that diff). When in doubt, default to inline; switch to prompt-builder if the same prompt shape is being reused or hand-authored more than once.
