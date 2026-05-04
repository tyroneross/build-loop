---
name: build-loop:architecture-rules
description: Architecture violation check — orphans, layer violations, circular dependencies, hotspots. Build-loop's native rules engine, copied from NavGator's test command (no canonical SKILL.md exists upstream).
version: 0.1.0
user-invocable: false
source: NavGator/commands/test.md
source_hash: 4a967c08ff3d7cddd408e7caefcd07b09e8931bcaccf9ea70c0f5aa94349e0d1
---

# Architecture Rules / Violation Check

Run NavGator's rules engine to detect architectural violations using the `mcp__plugin_navgator__rules` MCP tool. Classifies findings as blocking (circular dependency, layer violation, database isolation breach, frontend-direct-DB at error level) vs warning (hotspot, high-fan-out, orphan).

> **Divergence note**: NavGator has no discrete SKILL.md for rules. The canonical wrapper is `commands/test.md`, which orchestrates `navgator rules` + `navgator dead` + pipeline traces. This skill extracts the rules-only workflow.

## When to Activate

- Phase 4 Review-D Fact-Check: when code changed in this build, run rules to detect new violations
- Phase 1 Assess: optionally run as a baseline so Review-D can diff
- User asks "any architecture violations", "is this safe to merge", "what did I break"

## Pre-flight

1. If `.navgator/architecture/index.json` does not exist → no-op with `NavGator: no architecture snapshot found — skipping rules check`. Recommend `build-loop:architecture-scan`.
2. Check `index.json` `generated_at` timestamp. If >24 hours old, warn: "Architecture data is N hours old — consider running `build-loop:architecture-scan` first for accurate results."

## Rules Workflow

1. Run `mcp__plugin_navgator__rules` (or `navgator rules --json`)
2. Classify each finding:

| Severity | Categories |
|----------|-----------|
| **Blocking** | `circular-dependency`, `layer-violation`, `database-isolation`, `frontend-direct-db` (error level) |
| **Warning** | `hotspot`, `high-fan-out`, `orphan` |

3. Diff against the Phase 1 baseline if present in `.build-loop/state.json.architecture.rules.baseline`
4. Flag recurrences against `.navgator/lessons/lessons.json` (lessons with matching `signature`)
5. Write the result to `.build-loop/state.json.architecture.rules` with `{blocking_count, warning_count, new_violations[], recurrences[]}`

## Decision

- **Blocking findings** → route back to Phase 5 Iterate with the violation as a fresh criterion
- **Warning findings** → log to `.build-loop/issues/` and surface in Review-F Report
- **No new violations** → continue Review pipeline; report "No new architectural violations introduced"

## Report Format

```
ARCHITECTURE RULES
==================
Blocking: N (N new this build)
Warnings: N (N new this build)
Recurrences against lessons: N

[BLOCKING] Layer violation: ComponentA (frontend) → ComponentB (database)
  File: src/pages/users.tsx:45
  Recurrence: matches lesson 'frontend-direct-db' (last seen 2026-03-15)
```

If no findings: report "No connection-integrity issues found" — do not omit the section.

## Sibling Skills

- `build-loop:architecture-scan` — refresh data first
- `build-loop:architecture-impact` — pre-flight blast-radius for any blocking finding's affected component
- `build-loop:architecture-dead` — orphan-only scan (warnings flagged here too)
- `build-loop:architecture-review` — runs rules as part of a 5-phase integrity review

*Source: NavGator `commands/test.md` (the orchestrator that calls `navgator rules`). The canonical rules engine is the MCP tool. Drift-checked by `build-loop:sync-skills`.*
