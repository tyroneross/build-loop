---
title: <one-line imperative summary>
repo: <repo-slug>            # REQUIRED — segmentation key; the repo this item belongs to. Never mix repos.
branch: <branch>             # segmentation key; the branch this item is scoped to (default: main)
created: <YYYY-MM-DD>
validated:                   # YYYY-MM-DD the premise was last RE-CHECKED against the live repo. Empty = never; falls back to `created`. Stamped only by `scripts/premise_revalidation.py validate --note "<evidence>"`, never by hand.
source: <where it came from — issue id / review finding / user request / run id>
classify: SAFE              # SAFE | RISKY | DECISION | PRODUCTION (from scripts/classify_action.py)
effort: M                   # XS | S | M | L | XL
status: open                # open | in-progress | blocked | done
product_impacting: false    # bool — does this affect end-user experience (UI, data, perf, security, accessibility)?
impact:                     # one-line user-facing consequence; empty when product_impacting: false
---

## Problem
<what's broken / missing, with evidence — a code cite or observed failure, not a cited statistic>

## Proposed fix
<smallest mechanism that addresses the root cause; prefer extend/delete over add>

## Acceptance
- <verifiable condition 1>
- <verifiable condition 2>

<!--
Premise freshness (see scripts/premise_revalidation.py): an item is surfaced for execution long
after it was written. By then the bug may be fixed, the file moved, or the precondition false.
`validated` records when the premise was last re-checked against the live repo; it falls back to
`created` when never re-checked, so an item filed today is fresh by construction. Past the window
(default 7 days) the drain gate refuses the item with `stale_needs_revalidation` rather than
executing it. Clear it with:
    python3 scripts/premise_revalidation.py validate --item <path> --note "<what you re-checked>"
`--note` is required: a bare timestamp asserts freshness without evidence, which is the failure
being fixed.

Segmentation rule (see references/memory.md): a backlog item lives ONLY in its owning
repo's scope — the repo's `.build-loop/backlog/` (active) and the durable
`build-loop-memory/projects/<repo>/backlog.md`. `repo` + `branch` are mandatory so a
cross-repo item is never recorded in the wrong tracker. When working repo X on branch B,
read/write only items where repo==X (and branch==B or unscoped).
-->
