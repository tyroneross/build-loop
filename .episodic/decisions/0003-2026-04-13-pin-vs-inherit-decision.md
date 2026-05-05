---
id: '0003'
slug: pin-vs-inherit-decision
title: Pin-vs-inherit decision
type: decision
status: accepted
confidence: confirmed
date: '2026-04-13'
tags: [infra, process]
primary_tag: infra
entity: 'build-loop:pin-vs-inherit-decision'
project: build-loop
tool: migration
model: unknown
task_category: unknown
author: tyroneross
source: migration
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
confidence_source: external_import
confirmation_count: 0
valid_until: null
causal_parent_id: null
embedding_model_version: mxbai-embed-large-v1
domain: infra
goal: unknown
---

# Pin-vs-inherit decision

## Context

Migrated from .build-loop/feedback.md (post-hoc lesson, not a forward-looking choice).

## Decision

User flagged forward-compat value of `inherit` (new tiers between Haiku/Sonnet adopt automatically). Revised pattern: pin when task has clear right tier, inherit when user intent should flow through. Saved as global feedback memory `feedback_pin_vs_inherit.md`.

## Consequences

Carried forward as a confirmed lesson; consult before re-litigating the same area.
