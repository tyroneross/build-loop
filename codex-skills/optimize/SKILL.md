---
name: optimize
description: "Main Build Loop optimization entrypoint. Use when the user wants to make something faster, cheaper, smaller, simpler, or better against a measurable metric."
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Optimize

This is the public Codex entrypoint for Build Loop optimization. The canonical
implementation remains internal:

```text
../skills/optimize/SKILL.md
```

Define the metric first, identify factors, measure each run, keep only changes
that improve the metric, and revert changes that do not.
