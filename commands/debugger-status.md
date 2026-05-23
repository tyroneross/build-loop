---
description: "Show debugging memory statistics"
allowed-tools: Bash
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

Display the current state of the debugging memory system:

```bash
npx @tyroneross/claude-code-debugger status
```

This shows:
- Number of stored incidents
- Number of extracted patterns
- Storage size
- Recent activity

Use this to understand what debugging knowledge has been accumulated and whether the memory system is working correctly.
