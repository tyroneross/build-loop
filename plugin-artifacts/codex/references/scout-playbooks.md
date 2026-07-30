<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
# Architecture-scout playbook detail

Verbose command + template boilerplate extracted from `agents/architecture-scout.md`
to keep the agent definition under its concision budget. The scout keeps the
operative step sequence inline; load this file for the exact command string and
the handoff template when running the `baseline` task.

## `baseline` step 4 — persist the baseline as a decision

Run once per baseline (idempotent topic-identity supersession by primary_tag+entity
in `write_decision.py`):

```bash
SCAN_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
COMPONENTS=$(jq '.component_count // .components_count // 0' .build-loop/architecture/index.json)
CONNECTIONS=$(jq '.connection_count // .connections_count // 0' .build-loop/architecture/index.json)
VIOLATIONS=$(jq '.violations | length' .episodic/architecture/known_violations.json 2>/dev/null || echo 0)

python3 "${CLAUDE_PLUGIN_ROOT:-$PWD}/scripts/write_decision/__main__.py" \
  --workdir "$PWD" \
  --title "Architecture baseline scan: ${COMPONENTS} components, ${CONNECTIONS} connections" \
  --decision "Baseline captured at ${SCAN_TS}; ACP path .build-loop/architecture/acp.json recorded for downstream phase use." \
  --context "Top hotspots and recent violations summarized in the scout's envelope; full ACP at .build-loop/architecture/acp.json." \
  --consequences "Cross-session recall available via scripts/recall.py and scripts/memory_facade.py; Phase 1 in next session uses this as warm start." \
  --tags "architecture,proposed:baseline,proposed:scout,proposed:arch-baseline" \
  --primary-tag "architecture" \
  --entity "baseline-scan" \
  --confidence "confirmed" \
  --confidence-source "tool_extraction" \
  --status "accepted" \
  --source "auto-confirmed" \
  --domain "meta" \
  --goal "maintainability" \
  --task-category "research" \
  --no-db
```

Use `--no-db` because Phase 1 must not block on Postgres availability; the
`consolidate_memory.py` Stop-hook step will sync the file row into `semantic_facts`
later. Record the resulting decision id (stdout) in
`findings[].side_effects: "wrote_decision_<id>"`. If `write_decision.py` is missing
or returns non-zero, log `"write_decision_failed"` and proceed — the scan still happened.

## `baseline` step 7 — portable handoff artifact

Write `.build-loop/architecture/handoff.md` unconditionally on every `baseline` run
(overwrite the previous version). Self-contained markdown — no external state required
to interpret it. The `task: handoff` variant produces the same artifact without
re-running the full ACP refresh (reads existing `acp.json` + `baseline.json` caches).

Required sections (use these exact headings):

```markdown
# Architecture Handoff
_Generated: <ISO timestamp> | Components: N | Connections: M_

## Component Map
| Name | Path | Role |
|------|------|------|
| ... | ... | one-line role |

## Key Connections / Data Flows
<!-- Each row: source → target : flow description -->

## Runtime Topology
<!-- Deployment units, process boundaries, external services. -->

## LLM Use-Cases
<!-- Each LLM call site: component, model_class, purpose. -->

## Porting Notes
<!-- What a fresh session or port to another version needs to know:
     pinned deps, non-obvious config, env vars, build order constraints,
     known violations still open. Keep to facts, not opinions. -->
```

Keep the file ≤ 400 lines. Truncate the Component Map table to the 20 highest
blast-radius components when the project exceeds that count; note
`(truncated — full list in acp.json)` below the table.

**Fresh / resumed session behavior**: when the orchestrator's Phase 1 detects
`handoff.md` exists AND its mtime is within the last 24 hours (or within the
`architecture.staleness_threshold_hours` config value when set), it reads
`handoff.md` and skips dispatching a full baseline scout. The session still
dispatches `chunk-impact` scouts as needed. When `handoff.md` is absent, stale, or
the orchestrator passes `force_baseline: true`, run the full baseline and overwrite.
