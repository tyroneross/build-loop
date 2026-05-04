---
name: build-loop:debugging-memory-search
description: Memory-first lookup before debugging — search past incidents and patterns via the debugger MCP search tool. Build-loop's native debugging memory, copied from claude-code-debugger. Distinct from the legacy in-tree `build-loop:debugging-memory` skill (kept for backward compat); this is the source-tracked native version.
version: 0.1.0
user-invocable: false
source: claude-code-debugger/skills/debugging-memory/SKILL.md
source_hash: 484cd20dfe7fc6f345e508738a54fc6ba9750dca1efa9dbe26c6d57e5ba8f46e
---

# Debugging Memory Workflow (Native, Sourced)

Memory-first debugging. Core principle: **never solve the same bug twice**. Native to build-loop — content adapted from `claude-code-debugger/skills/debugging-memory/SKILL.md`. Uses the bundled debugger MCP server (`mcp__plugin_build-loop-debugger__*`).

> **Naming note**: this skill is `build-loop:debugging-memory-search` to avoid colliding with the legacy in-tree `build-loop:debugging-memory` skill (which the orchestrator continues to call as the memory-first gate's primary entry point). Both have equivalent content; this one carries `source` + `source_hash` provenance and is drift-checked by `build-loop:sync-skills`. New code should prefer the legacy name until the orchestrator is migrated; sibling skills in `skills/debugging/` reference the legacy name where the gate's exact runtime semantics are needed.

## When to Activate

- Phase 1 Assess: pull recent project incident context for orientation (`list` MCP)
- Phase 4 Review-B Validate: on every criterion failure with an error-like signal — read logs first, synthesize symptom, search memory
- Phase 5 Iterate: at the start of every Iterate attempt, re-search with the new symptom (failure may have shifted shape after a fix)

## Memory-First Approach

Before investigating any bug, always check the debugging memory using the debugger `search` MCP tool with the symptom description.

The search returns a **verdict** with matching incidents and patterns.

**Verdict-based decision tree:**

1. **KNOWN_FIX**: Apply the documented fix directly only when the strict direct-apply gate (below) passes; otherwise adapt the prior incident as a hypothesis and route to the standard fix flow
2. **LIKELY_MATCH**: Review the past incident, use it as a starting point — never direct-apply
3. **WEAK_SIGNAL**: Consider loosely related incidents, but investigate fresh
4. **NO_MATCH**: Proceed with standard debugging via `build-loop:debugging-debug-loop`, then document the solution after

### Direct-apply gate (KNOWN_FIX only)

All three must pass — otherwise downgrade to adapted-plan routing and record `direct_apply_blocked_by`:

1. **file_match**: at least one file in the prior incident's `files_changed` exists in current repo at same relative path
2. **version_match**: dependency versions in the prior incident match current within minor (semver). If the prior incident's `tags` include a version, compare it to current `package.json`/`requirements.txt`/etc.
3. **second_signal**: at least one secondary signal — same error class, same callsite line range, or same component layer

Skip direct-apply for any pattern with category `react-hooks`, `performance`, or anything where the fix is "increase value X" — those are context-sensitive.

## Progressive Depth Retrieval

1. **Initial search**: `search` MCP — returns verdict + compact matches
2. **Drill down**: `detail` MCP with the ID for full incident/pattern data
3. **Outcome tracking**: `outcome` MCP to record whether the fix worked

## Visibility

When this skill activates, always announce it to the user:

1. **Before searching**: "Checking debugging memory for similar issues..."
2. **After search**: "Found X matching incident(s) from past debugging sessions" or "No matching incidents — starting fresh investigation"

## Deep Investigation Mode

For non-trivial issues, escalate to the `build-loop:debugging-debug-loop` skill. Trigger is the **verdict category**, not a numeric confidence score:

- **`KNOWN_FIX`** → apply directly, skip the loop
- **`LIKELY_MATCH`** → enter debug loop (past incidents need verification against current context)
- **`WEAK_SIGNAL`** → enter debug loop (loosely related, fresh investigation needed)
- **`NO_MATCH`** → enter debug loop (no prior knowledge)

Also enter the debug loop when:
- Initial diagnosis feels superficial
- Previous fix didn't hold
- User explicitly asks for root-cause analysis
- Multiple symptoms suggest a shared cause

## Basic Steps (simple, clear-cut issues)

1. **Reproduce** — exact steps, environmental factors, minimal repro
2. **Isolate** — binary search recent changes, disable components, check logs
3. **Diagnose** — trace execution, examine state, identify offending code
4. **Fix** — minimal, targeted, no side effects
5. **Verify** — original repro, related tests, regression check

## Incident Documentation

After fixing a bug, store via `build-loop:debugging-store` skill (uses the `store` MCP tool). Required fields: `symptom`, `root_cause`, `fix`. Optional: `category`, `tags`, `files_changed`, `file`.

## Quality Indicators

The memory system scores incidents on:
- Root cause analysis depth (30%)
- Fix documentation completeness (30%)
- Verification status (20%)
- Tags and metadata (20%)

Target 75%+ quality score for effective future retrieval.

## Tagging Strategy

- Technology: `react`, `typescript`, `api`, `database`
- Category: `logic`, `config`, `dependency`, `performance`
- Symptom type: `crash`, `render`, `timeout`, `validation`

## Pattern Recognition

The memory system extracts patterns when 3+ similar incidents exist. Patterns have higher reliability than individual incidents. When a pattern matches, trust the solution template (90%+ confidence), apply the recommended approach, note caveats.

## MCP Tools Quick Reference

| Tool | Purpose |
|------|---------|
| `search` | Search memory for similar bugs (returns verdict) |
| `store` | Store a new debugging incident |
| `detail` | Get full incident or pattern details |
| `status` | Show memory statistics |
| `list` | List recent incidents |
| `patterns` | List known fix patterns |
| `outcome` | Record whether a fix worked |

(In build-loop these are exposed under `mcp__plugin_build-loop-debugger__*`.)

## Review-F Outcome Feedback

Closes the memory-first gate's feedback loop. Both required:

- For each newly resolved Review-B/Iterate failure: invoke `store` MCP with `{symptom, root_cause, fix, tags: ["build-loop", project, layer], files}`
- For each Review-B memory gate where a prior `KNOWN_FIX` or `LIKELY_MATCH` was applied: invoke `outcome` MCP with `{incident_id, result: "worked"|"failed"|"modified", notes}` — this trains the verdict classifier

Skipping `outcome` means the verdict classifier never improves.

## Subagent Integration

When debugging involves subagents:

1. **Pre-query memory once** with the `search` MCP before spawning agents
2. **Distribute context** — each agent gets relevant subset
3. **Aggregate findings** — collect insights from all agents
4. **Store unified incident** — single `store` call to document combined diagnosis

Subagents do not inherit Skill or MCP access — pre-load context into their prompt.

## Sibling Skills

- `build-loop:debugging-store` — write incident after fix
- `build-loop:debugging-assess` — parallel domain assessment for multi-domain symptoms
- `build-loop:debugging-debug-loop` — iterative root-cause analysis with causal-tree investigation

*Source: copied verbatim from claude-code-debugger and rewritten for build-loop. Drift-checked by `build-loop:sync-skills`.*
