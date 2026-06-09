---
title: <one-line imperative summary>
repo: <repo-slug>            # REQUIRED — segmentation key; the repo this item belongs to. Never mix repos.
branch: <branch>             # segmentation key; the branch this item is scoped to (default: main)
created: <YYYY-MM-DD>
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
Segmentation rule (see references/memory.md): a backlog item lives ONLY in its owning
repo's scope — the repo's `.build-loop/backlog/` (active) and the durable
`build-loop-memory/projects/<repo>/backlog.md`. `repo` + `branch` are mandatory so a
cross-repo item is never recorded in the wrong tracker. When working repo X on branch B,
read/write only items where repo==X (and branch==B or unscoped).
-->
