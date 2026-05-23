---
description: "Surface review-needing items across episodic memory: review queue, decision rot, open conflicts, stale procedures."
argument-hint: "[--rot-threshold-days N] [--no-db]"
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross | SPDX-License-Identifier: Apache-2.0 -->

Load the `build-loop:knowledge-review` skill.

{{#if ARGUMENTS}}
Run with arguments: `{{ARGUMENTS}}`
{{else}}
Run with defaults (`--rot-threshold-days 90`).
{{/if}}

Invoke `python3 scripts/knowledge_review.py --workdir "$PWD" {{ARGUMENTS}}` and present the markdown output to the user. Read-only; do not auto-resolve any item.
