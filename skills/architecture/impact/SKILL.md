---
name: build-loop:architecture-impact
description: Blast-radius analysis for a component or file before refactor. Build-loop's native impact analysis, copied from NavGator. Use in Phase 1 Assess for top-risk components and Phase 5 Iterate before any cross-layer fix.
version: 0.1.0
user-invocable: false
source: NavGator/skills/impact-analysis/SKILL.md
source_hash: e1eab41fae52db57fd80c996fec0d2480703e4ac407f5cf8e81edb882511a413
---

# Impact Analysis & Connections

Analyze what's affected by a change and map component connections using the NavGator MCP tools (`mcp__plugin_navgator__impact`, `mcp__plugin_navgator__connections`, `mcp__plugin_navgator__trace`). Native to build-loop — content adapted from `NavGator/skills/impact-analysis/SKILL.md`. If NavGator is not installed, no-op with a one-line note.

## When to Activate

- Phase 1 Assess: after `build-loop:architecture-scan`, run impact on the top 5 highest-risk components and write a compact summary into `.build-loop/state.json.architecture.impact`
- Phase 5 Iterate: BEFORE any escalation, run impact on the affected component to confirm whether the failing fix is cross-layer
- User asks what's affected by changing a component or file
- Before major changes to shared components

## Pre-flight

1. If `.navgator/architecture/index.json` does not exist → no-op with `NavGator: no architecture snapshot found — skipping impact analysis`. Recommend `build-loop:architecture-scan` first.

## Impact Analysis

Use `mcp__plugin_navgator__impact` with the component name to analyze blast radius.

**Input:** Component name (e.g., "express", "prisma", "/api/users")

**Returns:**
- Component's name, type, and layer
- **Incoming connections**: components/files that USE this component (may need changes)
- **Outgoing connections**: components this one depends on
- Severity assessment (critical/high/medium/low based on dependent count)
- Specific file paths and line numbers for each connection

### File-Based Impact

If the user provides a file path instead of a component name:
1. The tool resolves the file to its parent component automatically via file map lookup
2. If no component found, suggest running `build-loop:architecture-scan` to refresh data

## Connection Mapping

Use `mcp__plugin_navgator__connections` to show all connections for a component.

**Input:** Component name (required), direction (optional: "in", "out", or "both")

**Returns:**
- All incoming connections (what connects TO this component)
- All outgoing connections (what this component connects TO)
- File paths and line numbers for each connection

## Dataflow Tracing

For deeper data-flow analysis, defer to sibling skill `build-loop:architecture-trace` — same MCP tool family, focused on input→output pipelines.

## Decision Tree

| User Intent | MCP Tool | Notes |
|-------------|----------|-------|
| "What breaks if I change X?" | `mcp__plugin_navgator__impact` | Full blast radius |
| "Show connections for X" | `mcp__plugin_navgator__connections` | All connections |
| "What depends on X?" | `mcp__plugin_navgator__connections` (direction: "in") | Incoming only |
| "What does X use?" | `mcp__plugin_navgator__connections` (direction: "out") | Outgoing only |
| "Is it safe to modify X?" | `mcp__plugin_navgator__impact` | Check severity |

## After Analysis

Present results clearly:
1. Severity level and summary
2. Direct dependents (most important to review)
3. Transitive dependents (may be affected)
4. Recommendation: which files to review before making changes

Write summary into `.build-loop/state.json.architecture.impact[<component>]` with fields `{severity, direct_count, transitive_count, layers_crossed}`.

## Sibling Skills

- `build-loop:architecture-scan` — refresh data first
- `build-loop:architecture-trace` — pipeline trace
- `build-loop:architecture-rules` — violation check after change
- `build-loop:architecture-review` — full integrity review

*Source: copied verbatim from NavGator and rewritten for build-loop. Drift-checked by `build-loop:sync-skills`.*
