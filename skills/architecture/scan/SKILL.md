---
name: build-loop:architecture-scan
description: Use when Phase 1 Assess detects stale architecture state, the user asks for an "architecture scan", or before blast-radius analysis. Refreshes Build Loop's native component and connection data in `.build-loop/architecture/`, with NavGator reserved for escalation-only capabilities.
version: 0.1.0
user-invocable: false
source: NavGator/skills/architecture-scan/SKILL.md
source_hash: 1b4e25d6198373d261502a296dad2ea96abc9e66166115007db940cd48fe7cbb
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Architecture Scan & Status

Scan project architecture, check health, and monitor staleness using Build Loop's native architecture engine (`python -m build_loop.architecture scan`). NavGator remains an optional escalation adapter for capabilities not yet ported into the native engine, such as `llm-map`, `schema`, and `diagram`.

## When to Activate

- Phase 1 Assess: ensure architecture data is fresh (>24h old → re-scan) before sibling skill `build-loop:architecture-impact` consumes it
- User asks about project architecture, stack, or dependencies
- After `npm install`, `pip install`, or similar dependency operations
- User adds/removes dependencies or makes structural changes

## Pre-flight

1. Prefer the repo-local Build Loop venv when present: `<build-loop>/.venv/bin/python -m build_loop.architecture`.
2. Run `scan` before `rules`, `dead`, `impact`, or `trace` when `.build-loop/architecture/index.json` is missing or stale.
3. Use `--mode navgator` only when the requested capability is explicitly NavGator-only or the native command reports that escalation is required.

## Scanning

Use the native scanner to detect components and connections. It maps source imports and the Gator-derived runtime edges that Build Loop now owns natively: path-alias imports, manifest package use, frontend `/api/...` fetches, and conservative service/LLM calls.

**Options:**
- Default: `python -m build_loop.architecture scan --json`
- Incremental marker: add `--incremental` when the caller is refreshing after a small change

After scanning, present a smart-brevity brief:
- **Line 1**: "Scanned [project]. [N] components, [N] connections."
- **Runtime edges**: summarize `connection_counts_by_type` when present
- **What to watch**: low-confidence detections or missing route/package/service targets
- **AI routing**: providers and model count if service/LLM calls detected

## Status

Read `.build-loop/architecture/index.json` and `.build-loop/architecture/manifest.json` to show architecture summary without re-scanning. Returns: component counts, connection counts by type, last scan timestamp, and staleness indicator when available.

If no architecture data exists, recommend running scan first.

## Health Checks

Health information is included in scan output:
- Outdated packages
- Security vulnerabilities
- Orphaned connections (dead-code references)
- Missing imports and unused dependencies

## Decision Tree

| User Intent | Tool | Notes |
|-------------|----------|-------|
| "Scan my project" | native `scan` | Full scan |
| "Refresh after edit" | native `scan --incremental` | Marks scan as incremental |
| "What's my stack?" | read native manifest/index | No re-scan needed |
| "Any unused packages?" | native `scan`, then `dead` | `dead` checks manifest declarations |
| "Is architecture data fresh?" | read native manifest/index | Check timestamp |

## Output Format

Keep output concise. Do NOT dump raw JSON. Summarize into a scannable brief. Write the compact summary into `.build-loop/state.json.architecture.scan` under fields `{component_count, connection_count, connection_counts_by_type, last_scan, staleness, providers}`.

## Sibling Skills

- `build-loop:architecture-impact` — blast radius for a component
- `build-loop:architecture-trace` — data flow trace
- `build-loop:architecture-rules` — violation check
- `build-loop:architecture-dead` — orphan scan
- `build-loop:architecture-review` — full integrity review

*Source: copied verbatim from NavGator and rewritten for build-loop. Drift-checked by `build-loop:sync-skills`.*
