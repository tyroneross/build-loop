---
description: "Deep iterative debugging — causal-tree investigation, fix, verify, critique. Up to 5 iterations."
argument-hint: "<symptom>"
---

{{#if ARGUMENTS}}
Load the `build-loop:debug-loop` skill. Symptom: `{{ARGUMENTS}}`
{{else}}
Load the `build-loop:debug-loop` skill. Ask the user what failure to investigate.

Examples:
- `/build-loop:debug tests pass locally but fail in CI`
- `/build-loop:debug login works once then breaks on refresh`
- `/build-loop:debug API returns wrong data intermittently`

For quick memory lookup, use `/build-loop:debugger` instead.
For multi-domain assessment, use `/build-loop:assess` instead.
{{/if}}
