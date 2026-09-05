---
name: build-loop:database-practice
description: "Measure a database before changing it, and prove an object is dead before retiring it. Use when a build creates or alters a table, column, or index; when a query is slow or the user asks why the database is slow; or when someone proposes dropping an empty table. NOT for worktree or data-plane isolation (use data-plane-worktrees). Runs the read-only attribution set, reads the runtime counters, and applies the retirement gate. The binding rules live in references/database-agent-constitution.md; this skill is the procedure that produces the evidence those rules require."
user-invocable: false
---

<!-- SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Database Practice

The rules are in `references/database-agent-constitution.md` — "Object Lifecycle
And Population Contract" and "Cost Attribution And Retirement". This skill is how
you generate the evidence they demand.

Two things go wrong without it. A plan names a slow thing without naming its
share, and optimizes the wrong 5%. An audit calls an empty table dead, and drops
an object something still reads.

## Procedure

1. **Open the counter window.** `pg_postmaster_start_time()` and
   `pg_stat_database.stats_reset`. Every counter below is "since" that timestamp,
   and the window length is part of every claim you make from it. A 200-day
   counter history is a stronger liveness record than the 30-day observation
   period you were about to propose starting.
2. **Attribute the time.** Run section 2 of the query set. Rank your proposal
   against what is actually there. If it is not in the top 25, say so in the plan.
3. **Read the shape, not the name.** Sections 3, 6, 7, 8, 9 catch the five cost
   shapes below. Each has a fingerprint you can see without an execution plan.
4. **Establish liveness before retirement.** Section 4, then the gate.
5. **Attach before/after.** `calls`, `mean_exec_time`, `total_exec_time`,
   `rows/calls`, `temp_blks_written`, and `idx_scan` for every index touched.
   "The query looks faster" and "the build is green" are not evidence.

Run everything inside `BEGIN READ ONLY` with a statement timeout. Never run the
attribution set against production from an unbounded session.

## The five shapes, with their fingerprints

Ordered by observed share of database execution time in a real production
instance (12 GB, PostgreSQL 17, 299 hours of tracked execution across 48M calls),
not by textbook severity.

| Share | Shape | Fingerprint | Fix |
|---:|---|---|---|
| 49.3% | Insert against a vector index larger than cache | 485,596 `INSERT`s at ~1,000 ms each into two pgvector tables; cost is nearly flat in row count | Split fixed from marginal cost first (below), then batch harder or shrink the index |
| 14.8% | Predicate on a TOASTed column | mean in the tens of seconds on a table of only tens of thousands of rows; `pg_total_relation_size` far above heap + indexes | Derived scalar column (`content_length`), indexed, filtered on instead |
| 8.9% | Per-row lookup through jsonb + trigram | high `calls`, mid-hundreds `mean_ms`, no supporting composite index | Composite index on the real filter columns; move the fuzzy match behind an exact one |
| 4.5% | Vector similarity read | ~1 s mean against an HNSW index larger than `shared_buffers` | Size the graph to cache, or raise the cache |
| — | Index maintenance charged to writes | index `idx_scan` near zero while its table takes hundreds of thousands of inserts | Drop it, or accept the write cost explicitly |

### Split fixed from marginal before you "batch harder"

`rows / calls = 1.00` looks like an unbatched loop and usually is. It was not
here: the writer already batched, at four rows per statement, and the table
simply produced one chunk for most articles.

Regress `mean_exec_time` against `rows / calls` across the normalized statement
variants pg_stat_statements already gives you for free. In this instance the
call-weighted fit was **957 ms fixed per statement + 47 ms per row** — 95% fixed
at one row per statement, still 84% fixed at the batch size actually in use. The
fixed part is first-touch random I/O into an HNSW graph that cannot be cached;
the marginal part is the real per-row index maintenance.

The fit is observational — it reads variants the workload happened to produce, so
batch size may correlate with row width and the projection is a hypothesis, not a
result. Rehearse the new batch size against a copy before shipping it.

That decomposition picks the fix. A high marginal cost means batch. A high fixed
cost means the batch is too small for the overhead it is paying, or the index
does not fit in cache — and raising this batch from 4 to 64 moves per-row cost
from 286 ms to 62 ms without touching the index at all. Guessing which, without
the split, optimizes the wrong term.

Two numbers from the same instance make the index row concrete: a 2.31 GB HNSW
index recorded **31 lifetime index scans across 214 days** while being maintained
on 265,000 inserts, and a second HNSW index of 345 MB recorded **5**. Both were
being paid for on every write. Meanwhile `shared_buffers` was 256 MB against a
12 GB database — the graph could never be cached, so every insert was random I/O.

Spill in the same instance: 336,253 temp files and 2,053 GB written, against a
`work_mem` of 3.4 MB, with one CTE writing 41.8 GB across 91 calls.

## Liveness: reading the counters

| Signal | Reads as |
|---|---|
| `idx_scan > 0` | An application issued a filtered query. Audit scripts issue `count(*)`/`count(col)`, which are sequential scans, so an index scan is not audit noise |
| `seq_scan` well above the median across peer tables | Real sequential reads on top of the audit/monitor floor |
| `n_tup_ins > 0` with `n_live_tup = 0` | Written and purged — a working queue or retention job, not a dead table |
| `n_tup_ins = 0` across the whole window | Never written since the counters started |
| Query-text match in `pg_stat_statements` | A lower bound only — blind to dynamic SQL, views, routines, and evicted entries |

`n_live_tup` is a stale planner estimate and must never decide emptiness. In the
instance above it read seven populated tables as empty, one of them holding 148
rows.

Static source matching is the weaker method and errs both ways: it counted
documentation and generated-client references as evidence of life for two tables
that runtime showed had zero access in 214 days, and it flagged two others for
retirement that runtime showed were being read.

## Track tables — the map, and the diff against last time

The attribution set answers "where does the time go today". The table map
answers "what changed since the last time we looked", which is the question a
schema change or a hot query needs. `scripts/db_table_map.py` collects every
counter this skill reads, in one read-only transaction, and writes a JSON map
plus a Markdown report with a **Shapes** section that names which of the five
cost shapes each finding matches.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/database-practice/scripts/db_table_map.py" \
  --dsn "$DATABASE_URL" --statement-timeout 25 \
  --out-json docs/10-reports/database-audits/$(date +%F)-database-map.json \
  --out-md   docs/10-reports/database-audits/$(date +%F)-database-map.md
# next time, diff against the previous JSON:
python3 .../db_table_map.py --dsn "$DATABASE_URL" --prev docs/10-reports/database-audits/<prev>.json ...
```

The script strips the URI parameters psql rejects (`pgbouncer`, `connection_limit`,
`pool_timeout`), refuses any DDL or DML, and wraps everything in `BEGIN READ ONLY`
with a `SET LOCAL statement_timeout`. Commit both files under the project's
`docs/**/database-audits/` directory; re-run with `--prev` whenever a migration
lands or a query enters the top 25. A liveness verdict that flips, an index whose
`idx_scan` stays flat while its table's inserts grow, or a size delta over 10%
is the diff's job to surface.

Verified 2026-09-05 against a 12 GB PostgreSQL 17.4 instance through a
transaction-mode pooler: 128 tables, 529 indexes, 23 shape findings in one pass.

## Vector and graph shapes

pgvector indexes and knowledge graphs stored in plain SQL have their own
fingerprints — a filtered similarity query that silently under-returns, an HNSW
graph larger than `shared_buffers` maintained on every insert, a function whose
`hnsw.*` settings never reached production, an adjacency table without a covering
index in both directions, a materialized-neighbourhood cache with a hot-row update
profile. `references/vector-and-graph-tuning.md` carries each fingerprint, the
fix, and the pgvector settings (`ef_search` default 40; `iterative_scan`
`relaxed_order` / `strict_order` from 0.8.0; `m` / `ef_construction`; function-scoped
`ALTER FUNCTION ... SET` because a session `SET` is lost under a transaction-mode
pooler).

## Fingerprint → first action

| Fingerprint (from the map or the query set) | First action |
|---|---|
| `INSERT` with `rows/calls` = 1.00 into a table with an HNSW/GIN index | Split fixed from marginal cost (regress `mean_exec_time` on `rows/calls`), then batch or shrink the index |
| Index `idx_scan` near zero while its table takes ≥ 10⁵ inserts | Retirement gate: rename → observe → drop, or record why it is kept |
| HNSW index larger than `shared_buffers` | Size the graph to cache (fewer dimensions, halfvec, partial index) or raise the cache; never assume the graph is resident |
| Filtered vector query returns fewer rows than `LIMIT` | `ALTER FUNCTION ... SET hnsw.iterative_scan = relaxed_order` (function-scoped), then measure recall against an exact scan |
| Vector function's `proconfig` lacks the `hnsw.*` settings the repo's SQL declares | Drift: restore with a migration and add a check that reads `pg_proc.proconfig` |
| `pg_total_relation_size` ≫ heap + indexes (TOAST) on a filtered table | Derived scalar column beside the wide one; filter and sort on the scalar |
| High `n_tup_upd` on a small table (hot rows) | Check HOT-update ratio and fillfactor; move the churn to an append-only side table |
| `temp_blks_written` on a CTE or sort | Raise `work_mem` for that role or query only, after rehearsing against a copy |
| `n_live_tup` = 0 but `idx_scan` > 0 | Not empty. `n_live_tup` is an estimate; run the exact `count(*)` from section 5 |

## Sonnet-tier runbook

Each step names the command, the output you must have in hand, and the stop
condition. Do not skip to a fix; the evidence is the deliverable.

1. **Window.** Run section 1 of the query set (or the map's "Counter window"). Have: `pg_postmaster_start_time`, `stats_reset`, window length in days. Stop if the window is under 14 days — say so in the plan; the counters cannot support a retirement claim yet.
2. **Map.** Run `db_table_map.py` with `--out-json` and `--out-md` (and `--prev` when a previous map exists). Have: the Markdown report committed under `docs/**/database-audits/`. Stop if the script reports `pg_stat_statements` missing — attribution (step 3) is then a lower bound and the plan must say so.
3. **Attribute.** Read the map's "Top statements by total_exec_time". Have: the share of DB time your proposal touches, quoted as a percentage. Stop if it is not in the top 25 — record the proposal as low-leverage and do not spend a migration on it without a stated reason.
4. **Shape.** Read the map's "Shapes" section and the fingerprint table above. Have: one named shape per finding you intend to act on, with its evidence line. Stop if a finding matches no shape — escalate to a stronger tier with the evidence.
5. **Liveness before retirement.** For any drop or rename, run the retirement gate below. Have: every checkbox with its evidence. Stop at the first unchecked line.
6. **Before/after.** For every index or statement you change, record `calls`, `mean_exec_time`, `total_exec_time`, `rows/calls`, `temp_blks_written`, and `idx_scan` from the map before and after, and re-run the map with `--prev`. "The query looks faster" is not evidence.

**Escalate to a stronger tier when:** the proposal is DDL on a table over 1 GB; any HNSW or GIN rebuild; any change to a function's `proconfig` or to `shared_buffers` / `work_mem`; attribution is ambiguous between two shapes; or the retirement gate depends on an owner who has not answered. State what was measured so far in the hand-off.

## Retirement gate

Every line holds, or the object stays:

- [ ] A named owner confirms the feature state and its replacement.
- [ ] Repository search covers raw SQL, ORM model names, mapped names, generated
      clients, scripts, tests, and documentation.
- [ ] Database dependencies cover foreign keys, views, materialized views,
      routines, triggers, policies, and publications.
- [ ] External workers, cron jobs, queues, dashboards, and integrations checked.
- [ ] Runtime counters show zero reads and zero writes across a stated window,
      and the window's start date is stated with it.
- [ ] Retention, compliance, backup, and restore requirements resolved.
- [ ] The change is a staged rename → deny → observe → drop.
- [ ] Tests pass against the staged change.

Stage one is always a rename with the old name left as a view, or a revoke. A
drop that has not survived a rename has not been tested.

## Query set

`references/diagnostic-queries.sql` — counter window, time attribution,
single-row-insert fingerprint, per-table liveness, exact emptiness, index cost vs
benefit, duplicate indexes, temp spill, TOAST ratio, and the column population
check. Verified green against PostgreSQL 17.4 on Supabase.
`references/vector-and-graph-tuning.md` — pgvector/HNSW and graph-in-SQL
fingerprints and fixes. `scripts/db_table_map.py` — the table map and diff.
