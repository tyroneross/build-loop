---
name: debugging-memory
description: Search Build Loop's native debugging memory before investigating a bug and store verified fixes afterward. Use first for crashes, errors, regressions, and broken behavior. Not for implementing the fix itself; use debug-loop.
version: 2.0.0
user-invocable: false
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Debugging Memory

Build Loop embeds the Coding Debugger core natively. Search and store use the same project-local structured root: `.claude/memory/`. No standalone debugger package, plugin, or MCP server is required.

## Op routing

| `op` | Purpose | Reference |
|---|---|---|
| `search` | Return a verdict and compact incident matches before investigation. | `references/search.md` |
| `store` | Persist a verified incident to the same store searched above. | `references/store.md` |
| `assess` | Run parallel domain assessment for a multi-domain symptom. | `references/assess.md` |

Omitting `op` means `search`.

## Required workflow

1. Announce: “Checking debugging memory for similar issues...”
2. Run the native search command from `references/search.md` with the exact symptom.
3. Report the verdict and match count.
4. Route the live diagnosis and fix to `build-loop:debug-loop` unless a `KNOWN_FIX` passes every direct-apply check.
5. After verification, run the native store command from `references/store.md`.
6. Search again to prove the newly stored incident is discoverable.

## Verdict handling

- `KNOWN_FIX`: direct-apply only after file, version, and second-signal checks all pass.
- `LIKELY_MATCH`: treat as a grounded hypothesis; verify in the current code.
- `WEAK_SIGNAL`: do not anchor; investigate fresh.
- `NO_MATCH`: investigate fresh and store the verified resolution.

## Storage boundary

- `.claude/memory/incidents/*.json`: durable structured debugger history.
- `.claude/memory/patterns/*.json`: extracted reusable patterns.
- `.build-loop/issues/`: unresolved or executable work only; do not store resolved debugger history there.

## Quality gate

Every stored incident needs an exact symptom, causal root cause, implemented fix, verification evidence, relevant tags, and changed files. Never label an untested diagnosis `verified`.

## Sibling skills

- `build-loop:debug-loop`: investigate, fix, and verify the active bug.
- `build-loop:root-cause-analysis`: post-fix systemic analysis.
- `build-loop:debugging-memory` `{op:"assess"}`: parallel domain assessment.
