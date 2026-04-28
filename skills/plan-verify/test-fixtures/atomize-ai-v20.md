# Atomize AI Knowledge-Graph Cleanup — v2.0 (synthetic)

> Synthetic fixture reconstructing the five errors caught in atomize-ai v2.0 review cycles.
> Used to validate `plan_verify.py`. Do not treat as a real plan.

## Goal

Drop dead knowledge-graph tables, simplify backend routes, and remove unused chart libraries.

## Phase 1 — Database

We will **delete** `scripts/optimize_loop.py` because it has 0 callers in the codebase. The orphan scan flagged it during the last NavGator review.

We will also **delete** `scripts/write_run_entry.py` — it is an orphan with zero references.

Total orphans removed: **6 orphans**.

## Phase 2 — Routes

Remove route `/api/optimize` and replace with a 308 redirect to `/api/optimize-loop`. This change touches the optimize-loop module.

We will deprecate the path `/v1/jobs/run` and migrate callers to `/v2/jobs`.

## Phase 3 — Packages

The `recharts` package is unused — remove it from package.json. We never imported it.

The package `react-vega` is in package.json and we'll keep it.

## Phase 4 — Chart migration

Numeric note: removing the **5 orphans** from Phase 1 will cut bundle size by approximately 12 KB.

(Numeric drift: Phase 1 says "6 orphans", this section says "5 orphans".)

## Phase 5 — Cleanup

We will rename three files. Total touched: ~50 files.

The chart system is now stable. ✅ verified
