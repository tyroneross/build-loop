---
description: "The one build-loop command. Describe anything in plain language — build, fix, debug, optimize, research, test, root-cause, retrospective, plan, PRD — and it routes automatically. You never pick a mode."
argument-hint: "[--parallel] [--no-regrets on|off] [goal description]"
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

{{#if ARGUMENTS}}
Goal: `{{ARGUMENTS}}`

Scope check first, before loading anything else. If the goal is a clearly specified single-file edit, a config change, or a fix under ~20 lines, and it is not a debug, research, optimize, root-cause, retrospective, plan, PRD, or resume request: do not load the skill. Make the change directly, verify it the cheapest way available, and report what changed and how you verified it.

Otherwise, load the `build-loop:build-loop` skill and follow it for this goal.
{{else}}
Load the `build-loop:build-loop` skill. Ask the user what they're building or changing.
{{/if}}
