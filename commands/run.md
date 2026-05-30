---
description: "The single entry for any coding task — build, fix, refactor, optimize, research, or test. Auto-routes to the right mode; you don't pick."
argument-hint: "[--parallel] [goal description]"
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

{{#if ARGUMENTS}}
Load the `build-loop:build-loop` skill. Goal: `{{ARGUMENTS}}`
{{else}}
Load the `build-loop:build-loop` skill. Ask the user what they're building or changing.
{{/if}}
