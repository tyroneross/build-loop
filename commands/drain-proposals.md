---
description: "Review the cross-repo proposal backlog interactively. Scans every registered repo's .build-loop/proposals/ (incl. enforce-from-retro/ + self-review) plus ~/.assistant/proposals/, then walks each new item to apply / reject / defer. Never auto-applies."
argument-hint: "[--all] [--scan-only]"
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

You are walking the proposal-drain digest with the user. The drain script is
`scripts/drain_proposals.py` (cross-repo aggregator; state persists so decided
items never re-surface). NEVER apply, edit, or delete a proposal without the
user's explicit decision on that specific item.

## Procedure

1. Refresh the digest:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/drain_proposals.py" scan --json
   ```
   Report the headline: `N new / T total` and the per-repo breakdown.

2. If `--scan-only` was passed (`{{ARGUMENTS}}`), stop here — print the digest
   path and the `new` count, do not walk items.

3. Otherwise walk the `new` items (or all items if `--all` was passed), in the
   order returned (new first, oldest first). For EACH item present the one-line,
   repo, id, and age, then ask the user for a decision using AskUserQuestion with
   options: **Apply**, **Reject**, **Defer**, **Skip** (leave as new), **Stop**.
   - Batch related items from the same repo into one question when they share a
     theme, but record each item's decision separately.
   - **Apply** means: open the proposal, do the work it specifies (or dispatch a
     build-loop run for it), THEN record `set --status apply`. Applying is real
     work, not just a state flip — do not mark applied unless the change landed.
   - **Reject** / **Defer**: record immediately with an optional `--note`.

4. Record every decision:
   ```
   python3 "${CLAUDE_PLUGIN_ROOT:-.}/scripts/drain_proposals.py" set --key <KEY> --status apply|reject|defer --note "<why>"
   ```

5. When the user says Stop or the list is exhausted, re-run `scan` and report the
   remaining `new` count so the user sees progress.

## Non-negotiable
- No auto-apply. A proposal is only `applied` after its change actually lands.
- Decisions are per-item and come from the user, never inferred.
