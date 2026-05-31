---
title: {title}
created: {created}
source: {source}
classify: {classify}
effort: {effort}
status: {status}
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# {title}

## Problem

{problem}

## Proposed approach

{proposed_approach}

## Why deferred

{why_deferred}

---

**Field reference:**

- `classify`: SAFE (no side-effects) | RISKY (touches infra/schema/auth) | DECISION (needs user confirmation) | PRODUCTION (live-traffic impact)
- `effort`: XS (<1h) | S (1–4h) | M (half-day) | L (1–2 days) | XL (>2 days). No dollar costs — t-shirt sizing only.
- `status`: open | in-progress | done
- `source`: free-form — who/what surfaced this item (e.g. "user steering 2026-05-30", "Phase 6 Learn run-abc", "self-review")

Backlog items are **longer-lived** than `issues/` (current-run bugs). They survive across runs and are only drained when status becomes `done` or the item is explicitly removed.
