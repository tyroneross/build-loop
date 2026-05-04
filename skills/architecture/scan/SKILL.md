---
name: build-loop:architecture-scan
description: Refresh project architecture data — components, connections, prompts, infrastructure. Build-loop's native scan, copied from NavGator. Use during Phase 1 Assess to ensure architecture data is fresh before blast-radius analysis.
version: 0.1.0
user-invocable: false
source: NavGator/skills/architecture-scan/SKILL.md
source_hash: 1b4e25d6198373d261502a296dad2ea96abc9e66166115007db940cd48fe7cbb
---

# Architecture Scan & Status

Scan project architecture, check health, and monitor staleness using the NavGator MCP tools (`mcp__plugin_navgator__scan`, `mcp__plugin_navgator__status`). This is build-loop's native architecture scan — content adapted from `NavGator/skills/architecture-scan/SKILL.md`. The MCP tools come from the NavGator plugin; if NavGator is not installed the skill no-ops with a one-line note.

## When to Activate

- Phase 1 Assess: ensure architecture data is fresh (>24h old → re-scan) before sibling skill `build-loop:architecture-impact` consumes it
- User asks about project architecture, stack, or dependencies
- After `npm install`, `pip install`, or similar dependency operations
- User adds/removes dependencies or makes structural changes

## Pre-flight

1. If `.navgator/architecture/index.json` does not exist → emit `NavGator: no architecture snapshot found — skipping native scan` and exit. Do not block the build.
2. If `mcp__plugin_navgator__scan` MCP tool is not registered (NavGator plugin uninstalled) → same no-op message, fall through to text-only summary if needed.

## Scanning

Use `mcp__plugin_navgator__scan` to detect components, connections, AI prompts, and infrastructure.

**Options:**
- Default: full scan including code analysis
- `quick: true`: package files only, skip code analysis (faster)

After scanning, present a smart-brevity brief:
- **Line 1**: "Scanned [project]. [N] components, [N] connections."
- **What's new**: added/removed components since last scan
- **What to watch**: outdated packages, vulnerabilities, low-confidence detections
- **AI routing**: providers and model count if AI calls detected

## Status

Use `mcp__plugin_navgator__status` to show architecture summary without re-scanning. Returns: component counts by type/layer, connection counts, AI routing table, last scan timestamp, staleness indicator.

If no architecture data exists, recommend running scan first.

## Health Checks

Health information is included in scan output:
- Outdated packages
- Security vulnerabilities
- Orphaned connections (dead-code references)
- Missing imports and unused dependencies

## Decision Tree

| User Intent | MCP Tool | Notes |
|-------------|----------|-------|
| "Scan my project" | `mcp__plugin_navgator__scan` | Full scan |
| "Quick scan" | `mcp__plugin_navgator__scan` (quick: true) | Packages only |
| "What's my stack?" | `mcp__plugin_navgator__status` | No re-scan needed |
| "Any outdated packages?" | `mcp__plugin_navgator__scan` | Check health results |
| "Is architecture data fresh?" | `mcp__plugin_navgator__status` | Check timestamp |

## Output Format

Keep output concise. Do NOT dump raw MCP output — summarize into a scannable brief. Write the compact summary into `.build-loop/state.json.architecture.scan` under fields `{component_count, connection_count, last_scan, staleness, providers}`.

## Sibling Skills

- `build-loop:architecture-impact` — blast radius for a component
- `build-loop:architecture-trace` — data flow trace
- `build-loop:architecture-rules` — violation check
- `build-loop:architecture-dead` — orphan scan
- `build-loop:architecture-review` — full integrity review

*Source: copied verbatim from NavGator and rewritten for build-loop. Drift-checked by `build-loop:sync-skills`.*
