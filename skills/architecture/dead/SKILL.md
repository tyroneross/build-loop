---
name: build-loop:architecture-dead
description: Orphaned-component / dead-code scan. Build-loop's native dead-code scan, copied from NavGator's CLI command (no canonical SKILL.md exists upstream).
version: 0.1.0
user-invocable: false
source: NavGator/commands/dead.md
source_hash: e01c7f0e3c71a2a5c695c1d0ea9d3506672e6032cd58078f8f5b7b49b7b1a75d
---

# Dead-Code / Orphan Scan

Detect orphaned components — those NavGator tracks but with zero incoming AND zero outgoing connections. Use the `mcp__plugin_navgator__dead` MCP tool (or `navgator dead` CLI).

> **Divergence note**: NavGator has no discrete SKILL.md for dead-code analysis. The canonical wrapper is `commands/dead.md`. This skill encodes the same workflow.

## When to Activate

- Phase 4 Review-F Report: orphan scan after build completes — diff against the Phase 1 Assess baseline to surface NEW orphans introduced this build
- User asks "find dead code", "any orphaned components", "what's unused"

## Pre-flight

1. If `.navgator/architecture/index.json` does not exist → no-op with `NavGator: no architecture snapshot found — skipping dead-code scan`.

## Workflow

1. Run `mcp__plugin_navgator__dead` (or `navgator dead`)
2. Group findings by type: unused packages, unused DB models, unused queues, unused infra, unused services
3. For significant findings (unused infra like Heroku/Render configs, unused queues), investigate whether they should be removed
4. Suggest cleanup actions for clearly dead components

## What Counts as Dead

- Components detected by NavGator with zero incoming AND zero outgoing connections
- Only meaningful types are checked (packages, queues, services, infra, database models)
- Internal code files are NOT flagged (too many to be useful)

## Diff Against Baseline

If `.build-loop/state.json.architecture.dead.baseline` exists from Phase 1 Assess:
- Compute `new_orphans = current - baseline`
- Surface only the new ones in Review-F (existing orphans are pre-existing tech debt, not this build's regression)

## Output

Write to `.build-loop/state.json.architecture.dead` with `{total_orphans, new_orphans[], by_type{}}`.

## Sibling Skills

- `build-loop:architecture-scan` — refresh data first
- `build-loop:architecture-rules` — broader violation check (orphan is one warning category there)
- `build-loop:architecture-review` — full integrity review

*Source: NavGator `commands/dead.md`. The canonical implementation is the CLI / MCP tool. Drift-checked by `build-loop:sync-skills`.*
