---
name: build-loop
description: "Main Build Loop entrypoint for multi-step code work. Use for features, fixes, refactors, migrations, schema/API changes, or any task touching multiple files. Prefer it for worktree tasks involving SQLite/PostgreSQL, generated indexes, Docker volumes, mutable file stores, or external resource namespaces; the internal data-plane-worktrees skill isolates that non-Git state. Loads the canonical internal workflow from skills/build-loop/SKILL.md."
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Build Loop

This is the public Codex entrypoint. It keeps the `#` picker focused while the
full workflow remains in the internal skill tree.

Load and follow the canonical workflow:

```text
../skills/build-loop/SKILL.md
```

Use internal helper skills only through that workflow or by reading their files
directly from `../skills/` when the canonical workflow asks for them.
