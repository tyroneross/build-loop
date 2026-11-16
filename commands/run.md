---
description: "The one build-loop command. Describe anything in plain language — build, fix, debug, optimize, research, test, root-cause, retrospective, plan, PRD — and it routes automatically. You never pick a mode."
argument-hint: "[--parallel] [--no-regrets on|off] [goal description]"
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

{{#if ARGUMENTS}}
Goal: `{{ARGUMENTS}}`

Scope check first: read `${CLAUDE_PLUGIN_ROOT}/skills/build-loop/references/workflow-selection.md`. Preserve intent, resume obligations, and required repository checks; select direct work, a fixed chain, or adaptive orchestration. File count alone does not require the full loop.

If the selector cannot be read, load the canonical skill and use adaptive orchestration without retrying the selector. Report the missing selector; do not improvise a lighter path.

Follow the selected path. Load the `build-loop:build-loop` skill only for adaptive orchestration, specialist intent routing, resume, or a requirement that needs the full protocol.
{{else}}
Load the `build-loop:build-loop` skill. Ask the user what they're building or changing.
{{/if}}
