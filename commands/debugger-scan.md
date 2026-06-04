---
description: "Capture recent debugging incidents into build-loop native memory"
allowed-tools: Read, Grep, Bash
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

Review recent build-loop run notes, reports, and validation failures for debugging work from the past 7 days. Store confirmed incidents as Markdown files under `.build-loop/issues/`.

Prioritize:
- Root cause analysis documents
- Error tracking logs
- Fix reports

Do not call external debugger packages by default. Use standalone Coding Debugger only when the user explicitly asks for cross-project memory.
