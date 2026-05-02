---
description: "Orchestrated development loop: assess → plan → execute → review → iterate. Use --parallel to explicitly authorize Codex subagents/workers."
argument-hint: "[--parallel] [goal description]"
---

{{#if ARGUMENTS}}
Load the `build-loop:build-loop` skill. Goal: `{{ARGUMENTS}}`
{{else}}
Load the `build-loop:build-loop` skill. Ask the user what they're building or changing.
{{/if}}
