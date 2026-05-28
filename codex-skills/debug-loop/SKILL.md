---
name: debug-loop
description: "Main Build Loop debugging entrypoint. Use for root-cause analysis, failing fixes, crashes, exceptions, broken behavior, and validation failures that need an iterative diagnose-fix-verify loop."
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Debug Loop

This is the public Codex entrypoint for Build Loop debugging. The canonical
implementation remains internal:

```text
../skills/debug-loop/SKILL.md
```

Start with diagnosis, use evidence to identify root cause, apply the smallest
targeted fix, and verify with the repo's native tests or runtime checks.
