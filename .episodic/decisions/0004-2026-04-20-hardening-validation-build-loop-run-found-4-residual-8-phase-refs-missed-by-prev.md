---
id: '0004'
slug: hardening-validation-build-loop-run-found-4-residual-8-phase-refs-missed-by-prev
title: 'Hardening-validation build-loop run found 4 residual "8-phase" refs missed by previous 9-phase doc sweep'
type: decision
status: accepted
confidence: confirmed
date: '2026-04-20'
tags: [ui, process]
primary_tag: ui
entity: 'build-loop:hardening-validation-build-loop-run-found-4-residual-8-phase-refs-missed-by-prev'
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
---

# Hardening-validation build-loop run found 4 residual "8-phase" refs missed by previous 9-phase doc sweep

## Context

Migrated from .build-loop/feedback.md (post-hoc lesson, not a forward-looking choice).

## Decision

Add a pre-commit check that greps for "8-phase"|"eight phase" whenever .build-loop/goal.md references phase counts, so canonical phase-count drift surfaces before commit.

## Consequences

Carried forward as a confirmed lesson; consult before re-litigating the same area.
