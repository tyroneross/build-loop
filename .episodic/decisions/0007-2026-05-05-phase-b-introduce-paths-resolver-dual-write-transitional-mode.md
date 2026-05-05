---
id: '0007'
slug: phase-b-introduce-paths-resolver-dual-write-transitional-mode
title: 'Phase B: introduce _paths resolver + dual-write transitional mode'
type: decision
status: accepted
confidence: explicit
date: '2026-05-05'
tags: [architecture, tooling, process]
primary_tag: architecture
entity: 'build-loop:phase-b-resolver-dual-write'
project: build-loop
tool: manual
model: claude-opus-4-7
task_category: unknown
author: tyroneross
source: manual
related_runs: []
related_decisions: []
supersedes: null
superseded_by: null
bookmark_snapshot_id: null
captured_turn_excerpt: null
last_validated: null
last_accessed: null
files_touched: [scripts/_paths.py, scripts/project_resolver.py, scripts/write_decision.py, scripts/recall.py, scripts/scan_transcript_for_decisions.py, scripts/sync_db_from_files.py, scripts/init_agent_memory_schema.sql, scripts/migrate_schema_to_1024.sql]
closing_commit: null
confidence_source: user_statement
confirmation_count: 0
valid_until: null
causal_parent_id: null
embedding_model_version: mxbai-embed-large-v1
domain: meta
goal: maintainability
---

# Phase B: introduce _paths resolver + dual-write transitional mode

## Context

Phase A copied 6 decisions into ~/dev/git-folder/build-loop-memory/decisions/build-loop/ and created personal_memory schema mirroring build_loop_memory. Phase B adds the rails so cutover (Phase C) is a 5-minute freeze, not a coordinated migration. With no env vars set, behavior is byte-identical to pre-change.

## Decision

Add scripts/_paths.py as the single source of truth for path/schema literals, plus AGENT_MEMORY_DUAL_WRITE=1 transitional mode that mirrors decisions to legacy and new paths/schemas during the cutover window.

## Alternatives considered

Option A (chosen): env-flag transitional dual-write. Option B: hard-cutover at C with no transitional period — risks lost captures from in-flight Stop hook / build-orchestrator.

## Consequences

Build-orchestrator Phase 5 captures during the cutover window land in BOTH legacy and new stores. After Phase D, the dual-write code path can be deleted.
