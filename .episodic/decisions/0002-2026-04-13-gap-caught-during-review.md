---
id: '0002'
slug: gap-caught-during-review
title: Gap caught during review
type: decision
status: accepted
confidence: confirmed
date: '2026-04-13'
tags: [ui, process]
primary_tag: ui
entity: 'build-loop:gap-caught-during-review'
source: migration
related_runs: []
related_decisions: []
supersedes: null
superseded_by: null
bookmark_snapshot_id: null
captured_turn_excerpt: null
---

# Gap caught during review

## Context

Migrated from .build-loop/feedback.md (post-hoc lesson, not a forward-looking choice).

## Decision

First pass wired critic into orchestrator agent file but not into skills/build-loop/SKILL.md. An orchestrator reading only the skill would have silently skipped critic dispatch. Lesson: when adding a new phase, update BOTH the orchestrator agent AND the skill's phase list AND the process-flow diagram. Grep for the phase names before considering the change complete.

## Consequences

Carried forward as a confirmed lesson; consult before re-litigating the same area.
