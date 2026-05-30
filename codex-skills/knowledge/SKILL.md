---
name: knowledge
description: "Main Build Loop knowledge entrypoint. Use when recording decisions, ADRs, lessons, procedural memory, or repo-local knowledge that should be durable across future runs — AND when reviewing existing memory: surfacing the review queue, detecting decision rot, finding stale procedures, or resolving open conflicts."
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Knowledge

This is the public Codex entrypoint for Build Loop knowledge capture. The
canonical implementation remains internal:

```text
../skills/knowledge/SKILL.md          # capture: decisions, ADRs, lessons, procedures
../skills/knowledge-review/SKILL.md   # review: review queue, decision rot, stale procedures, open conflicts
```

Keep captures factual, scoped to the current repo or decision, and validated
against the files or commands that prove the lesson. Use the review path when
asked to surface what's gone stale or conflicting in existing memory rather than
to record something new.
