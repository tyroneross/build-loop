---
id: '0005'
slug: a-use-postgres-pgvector-for-repo-memory-acceptance-test
title: 'A: Use Postgres+pgvector for repo memory (acceptance test)'
type: decision
status: accepted
confidence: explicit
date: '2026-05-05'
tags: [architecture, tooling]
primary_tag: architecture
entity: 'build-loop:acceptance-A'
project: build-loop
tool: manual
model: unknown
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
files_touched: []
closing_commit: null
---

# A: Use Postgres+pgvector for repo memory (acceptance test)

## Decision

Postgres + pgvector chosen over SQLite for hybrid retrieval and concurrent writers.
