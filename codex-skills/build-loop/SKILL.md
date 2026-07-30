---
name: build-loop
description: "The single Build Loop plugin entrypoint. Use when the user asks to run Build Loop or requests multi-step features, fixes, refactors, migrations, schema/API changes, debugging, optimization, research, repository maintenance, or closeout. Routes plain-language intent automatically without mode selection, including non-Git state isolation through the internal data-plane-worktrees workflow."
user-invocable: true
public-justification: "Codex has no commands surface, so a skill is the only way to expose an entrypoint on that host. This wrapper IS the Codex equivalent of /build-loop:run — it is the single public entrypoint, not one of many. The Claude-side skills stay hidden because Claude has commands/run.md to carry that role. Do not add a Claude-side twin of this file."
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Build Loop

This is the only public Codex entrypoint. Route the user's plain-language goal
through the canonical workflow without asking them to choose a mode or helper.

Load and follow the canonical workflow:

```text
../../skills/build-loop/SKILL.md
```

Use internal helper skills only through that workflow. Read their files
directly from `../../skills/` when the canonical workflow asks for them.
