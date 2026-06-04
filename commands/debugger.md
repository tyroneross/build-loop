---
description: "Search build-loop native debugging memory before debugging"
allowed-tools: Read, Grep
argument-hint: "<symptom>"
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

Search build-loop's native debugging memory for similar past incidents before investigating a new bug.

{{#if ARGUMENTS}}

Run the native memory-first workflow with the provided symptom:

```
Skill("build-loop:debugging-memory-search") with input { symptom: "{{ARGUMENTS}}" }
```

After running the search:
1. If a match is found with >70% confidence, try that solution first
2. Review the root cause and fix approach from past incidents
3. Apply the documented fix, adapting as needed for the current context
4. If the fix works, no need to store again (already in memory)
5. If a different fix is needed, document it as a new incident

{{else}}

No symptom provided. Read recent local incidents from `.build-loop/issues/` if present, then ask the user to describe what they are debugging.

Use standalone Coding Debugger only when the user explicitly asks for cross-project debugger memory.

{{/if}}
