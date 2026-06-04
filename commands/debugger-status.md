---
description: "Show build-loop native debugging memory statistics"
allowed-tools: Bash, Read
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

Display the current state of build-loop's native debugging memory:

```bash
find .build-loop/issues -type f -name '*.md' 2>/dev/null | wc -l
```

This shows:
- Number of stored incidents
- Number of extracted patterns
- Storage size
- Recent activity

Use this to understand what debugging knowledge has been accumulated and whether the memory system is working correctly.
