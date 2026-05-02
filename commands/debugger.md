---
description: "Search past bugs for similar issues before debugging"
allowed-tools: Bash, Read
argument-hint: "<symptom>"
---

Search debugging memory for similar past incidents before investigating a new bug.

{{#if ARGUMENTS}}

Run the debugger search with the provided symptom:

```bash
npx @tyroneross/claude-code-debugger debug "{{ARGUMENTS}}"
```

After running the search:
1. If a match is found with >70% confidence, try that solution first
2. Review the root cause and fix approach from past incidents
3. Apply the documented fix, adapting as needed for the current context
4. If the fix works, no need to store again (already in memory)
5. If a different fix is needed, document it as a new incident

{{else}}

No symptom provided. Show recent issues from memory:

```bash
npx @tyroneross/claude-code-debugger status
```

Ask the user to describe what they're debugging, or suggest picking from the recent issues shown above.

{{/if}}
