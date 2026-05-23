---
description: "Orchestrated development loop: assess → plan → execute → review → iterate. Use --parallel to explicitly authorize Codex subagents/workers."
argument-hint: "[--parallel] [goal description]"
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

{{#if ARGUMENTS}}
Load the `build-loop:build-loop` skill. Goal: `{{ARGUMENTS}}`
{{else}}
Load the `build-loop:build-loop` skill. Ask the user what they're building or changing.
{{/if}}
