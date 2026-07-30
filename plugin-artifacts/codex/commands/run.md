---
description: "The one build-loop command. Describe anything in plain language — build, fix, debug, optimize, research, test, root-cause, retrospective, plan, PRD — and it routes automatically. You never pick a mode."
argument-hint: "[--parallel] [goal description]"
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

{{#if ARGUMENTS}}
Load the `build-loop:build-loop` skill. Goal: `{{ARGUMENTS}}`
{{else}}
Load the `build-loop:build-loop` skill. Ask the user what they're building or changing.
{{/if}}
