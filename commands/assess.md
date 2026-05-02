---
description: "Run parallel domain assessment for complex debugging symptoms"
allowed-tools: Bash, Read, Task
argument-hint: "<symptom>"
---

{{#if ARGUMENTS}}

Analyze "{{ARGUMENTS}}" across multiple domains in parallel.

**1. Detect domains** from symptom keywords (database, frontend, API, performance)

**2. Launch assessors in parallel** using Task tool with these agents:
- `database-assessor` - query, schema, migration issues
- `frontend-assessor` - React, component, render issues
- `api-assessor` - endpoint, auth, middleware issues
- `performance-assessor` - slow, memory, bottleneck issues

Spawn multiple Task calls in a single message for detected domains.

**3. Search memory** for similar past incidents:
```bash
npx @tyroneross/claude-code-debugger debug "{{ARGUMENTS}}"
```

**4. Present results** ranked by confidence, with recommended action sequence.

{{else}}

Provide a symptom: `/assess <description>`

Examples:
- `/assess search is slow and returns wrong results`
- `/assess 500 error on user registration`
- `/assess react component re-renders infinitely`

For simple issues, use `/debugger` instead.

{{/if}}
