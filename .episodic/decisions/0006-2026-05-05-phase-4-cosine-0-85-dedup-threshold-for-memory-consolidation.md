---
id: '0006'
slug: phase-4-cosine-0-85-dedup-threshold-for-memory-consolidation
title: 'Phase 4: cosine 0.85 dedup threshold for memory consolidation'
type: decision
status: accepted
confidence: explicit
date: '2026-05-05'
tags: [architecture, data, tooling]
primary_tag: data
entity: 'build-loop:phase-4-consolidation'
project: build-loop
tool: claude-code
model: unknown
task_category: unknown
author: tyroneross
source: auto-explicit
related_runs: []
related_decisions: []
supersedes: null
superseded_by: null
bookmark_snapshot_id: null
captured_turn_excerpt: 'build-loop run Phase 4: P4.1 consolidate_memory.py — dedup against existing semantic_facts via cosine >= 0.85'
last_validated: null
last_accessed: null
files_touched: []
closing_commit: null
---

# Phase 4: cosine 0.85 dedup threshold for memory consolidation

## Context

Phase 4 consolidation pass needs deterministic dedup against existing semantic_facts when promoting candidates. The design ref §12 specifies these thresholds drawn from Mem0/Zep production consensus.

## Decision

consolidate_memory.py uses cosine ≥ 0.90 = IGNORE, 0.85-0.90 = MERGE/UPDATE, <0.85 = INSERT for semantic_facts dedup, matching design ref §12 and write_decision.py topic-identity ladder

## Alternatives considered

Single threshold 0.85 (would over-merge phrasing variants); SBERT-style 0.7 (too loose for our 1024-dim mxbai). Picked design-ref values to stay consistent with the inline taxonomy already in auto-decision-capture skill.

## Consequences

consolidate_memory.py and any future semantic-dedup pass uses these constants; documented in TAXONOMY.md as part of Phase 4 wiring.
