---
description: "Scan recent sessions for debugging incidents"
allowed-tools: Bash
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross | SPDX-License-Identifier: Apache-2.0 -->

Mine the Claude Code audit trail for debugging work from the past 7 days:

```bash
npx @tyroneross/claude-code-debugger mine --days 7 --store
```

This scans `.claude/audit/` files for:
- Root cause analysis documents
- Error tracking logs
- Fix reports

Found incidents are automatically stored in debugging memory for future retrieval.

Use this command periodically to capture debugging sessions that weren't manually documented.
