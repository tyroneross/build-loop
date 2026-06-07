---
name: handoff
description: "Main Build Loop handoff entrypoint. Use to compose a durable, fixed-template handoff from the current build-loop run state — intent, goal, live checklist, git state, queues, and gotchas — so a fresh session can resume without losing context. Optionally launches a fresh session in the stable checkout."
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Handoff

This is the public Codex entrypoint for Build Loop run handoffs. The canonical
implementation remains internal:

```text
../skills/handoff/SKILL.md
```

Compose the handoff from real `.build-loop/` state (intent, goal, `state.json`
checklist, git status/log, the followup/backlog/ux-queue/issues queues, and
recorded gotchas) into the fixed template. Carry the live checklist across the
boundary verbatim so the next session resumes without re-deriving state. The
optional launch step targets the stable checkout, never a worktree that may be
garbage-collected.
