# Vector And Graph Tuning

Two shapes the base skill only sketches: a pgvector index, and a knowledge graph
stored in ordinary SQL tables. Every rule below carries the fingerprint you can
see in `db_table_map.py` output and the fix that follows from it. Numbers marked
*(observed)* come from one production instance — PostgreSQL 17.4 on Supabase,
12 GB, counters over a 215-day window — and are illustrative, not thresholds.

## pgvector And HNSW

### 1. `hnsw.ef_search` is the recall knob, and its default is 40

**Fingerprint.** A similarity query returns plausible but incomplete results,
and nobody can say what recall it achieves. No `ef_search` appears anywhere in
the function definition or the session setup.

**Fix.** Raise `ef_search` for the specific query path and measure recall against
an exact `ORDER BY embedding <=> $1` scan on a sample. `ef_search` sets how many
candidates the search keeps per layer: higher is slower and more accurate. It
must be at least the `LIMIT`. Treat it as a per-query setting, not a global one.

### 2. A filtered query under-returns unless the scan can iterate

**Fingerprint.** `LIMIT 20` returns 6 rows. The query pairs a vector distance
with a `WHERE` clause on a normal column. Nothing errors; the result is just
short, and the application treats the short list as "all there is".

Why: the index search runs first and returns `ef_search` candidates, then the
filter deletes most of them. With the default `ef_search` of 40 and a predicate
that keeps 10% of rows, roughly 4 rows survive.

**Fix.** pgvector 0.8.0 added `hnsw.iterative_scan`, which keeps scanning until
enough rows survive the filter. `strict_order` guarantees exact distance order;
`relaxed_order` allows slightly out-of-order results and recovers more of them.
`hnsw.max_scan_tuples` bounds how far the iterative scan will go before it gives
up and returns fewer than `LIMIT` rows. In the observed instance, switching a
filtered chunk search to `relaxed_order` moved it from 6 of 20 rows (recall 0.30)
to 20 of 20 (recall 1.00) for about +2 ms *(observed)*.

Prefer `relaxed_order` when the caller reranks anyway; `strict_order` when the
returned order is the answer.

### 3. `m` and `ef_construction` are build-time trade-offs you pay for forever

**Fingerprint.** An HNSW index built at the defaults (`m=16`,
`ef_construction=64`) on a table whose insert path is the top cost in
`pg_stat_statements`.

**Fix.** `m` sets edges per node: raising it raises recall, index size, build
time, and per-insert maintenance. `ef_construction` raises build quality and
build time only. Because index size is the thing that decides whether the graph
fits cache (rule 4), treat `m` as a size decision, not just a recall decision.
Halving dimensions with `halfvec` or a smaller model is usually a bigger win than
tuning `m`. Rehearse any rebuild against a copy — a rebuild locks writes.

### 4. The graph must fit `shared_buffers` or the page cache

**Fingerprint.** `shared_buffers` 256 MB, HNSW index 2.2 GB, database 12 GB
*(observed)*. Insert `mean_exec_time` around 1,000 ms that barely moves with row
count. `db_table_map.py` reports this as the `vector-insert-above-cache` shape.

**Fix.** Size the graph to the cache or the cache to the graph — there is no
third option. Every insert into a graph that cannot be cached is random I/O
against storage, and no batch size makes disk seeks free. Shrinking the vector
(dimensions, `halfvec`) shrinks the graph; raising `shared_buffers` costs money
on a managed instance but is one setting.

### 5. Set GUCs on the function, never on the session

**Fingerprint.** The repository's SQL file sets `hnsw.iterative_scan` and
`ef_search`; production `proconfig` carries only `enable_seqscan=off` *(observed)*.
The settings were lost somewhere between the file and the database, and nothing
noticed.

**Fix.** `ALTER FUNCTION similarity_search(...) SET hnsw.ef_search = 100` binds
the setting to the call. A session-level `SET` does not survive a transaction-mode
pooler, which hands the next statement a different backend; under PgBouncer in
transaction mode a session `SET` is silently ineffective. Function-scoped
settings are also self-documenting: they show up in `pg_proc.proconfig`, so they
are auditable.

### 6. Detect `proconfig` drift, do not trust the migration

**Fingerprint.** `db_table_map.py` lists a function that uses a vector distance
operator (`<=>`, `<->`, `<#>`, `<+>`) or takes/returns a `vector` type, with no
`hnsw.ef_search` and no `hnsw.iterative_scan` in `proconfig`.

**Fix.** Re-apply the `ALTER FUNCTION` and commit the map that proves it. Compare
the "Function GUCs" section against the repository's SQL on every map run; a
setting that lives only in a migration file is a setting nobody is enforcing.

### 7. Write amplification: the index is maintained on every insert

**Fingerprint.** An HNSW index of 2.2 GB with 33 lifetime scans against 402,158
table inserts; a second at 338 MB with 5 scans *(observed)*. Both are maintained
on every write. `db_table_map.py` calls this `index-maintenance-on-writes`.

**Fix.** An index whose read benefit is 33 scans and whose write cost is 402,158
maintenance operations is a liability with a positive-sounding name. Drop it, or
write down why the cost is accepted. Check `idx_scan` on the *other* HNSW index
over the same data before dropping: two graphs over one column usually means one
is the live read path and the other is a leftover.

### 8. Split fixed from marginal cost before you "batch harder"

**Fingerprint.** `rows / calls = 1.00` on a high-call `INSERT`.

**Fix.** Regress `mean_exec_time` against `rows / calls` across the normalized
statement variants `pg_stat_statements` already separates. In the observed
instance the call-weighted fit was 957 ms fixed + 47 ms per row — 95% fixed at
one row per statement *(observed)*. A high marginal term means batch. A high
fixed term means the batch is too small for the per-statement overhead, or the
graph does not fit cache. Choosing without the split optimizes the wrong term.

### 9. Batch sizing follows the split, and is rehearsed

Raise the batch only after the split says the fixed term dominates, and only as
far as the write path's memory and lock-hold time allow. Larger batches hold row
locks longer and raise the cost of a retry. The projection from an observational
fit is a hypothesis; rehearse the new size against a copy before shipping it.

### 10. Partial indexes for low-cardinality filters

**Fingerprint.** Every vector query carries the same narrow predicate —
`WHERE tenant_id = $1`, `WHERE status = 'published'` — and the filter deletes
most candidates after the search.

**Fix.** Build a partial HNSW index per value:
`CREATE INDEX ... ON t USING hnsw (embedding vector_cosine_ops) WHERE status = 'published'`.
The planner searches a graph that contains only matching rows, so `ef_search`
candidates are all usable and iterative scan is unnecessary. This works when the
filter column has few values and they are stable; it does not scale to a
per-user predicate, where the right answer is iterative scan.

## Graph In SQL

A knowledge graph stored in relational tables — entities, mentions, pairs,
evidence, trends, snapshots — is a normal schema with unusual access patterns.
No extension makes these rules unnecessary.

### 11. Adjacency tables need both directions indexed

**Fingerprint.** An edge table with a composite index on `(src_id, dst_id)` and
nothing on `(dst_id, ...)`. Forward traversal is fast; the reverse traversal
sequentially scans, and the map shows `seq_scan` climbing on the edge table.

**Fix.** Index both `(src_id, dst_id)` and `(dst_id, src_id)`, and include the
columns the traversal reads so the lookup stays index-only. Both directions get
used the moment anyone asks "what points at this".

### 12. Bound every recursive CTE by depth and rows

**Fingerprint.** A `WITH RECURSIVE` traversal with no depth column and no
`LIMIT`. It is fine until one hub entity connects to 50,000 others, then it
spills — visible as `temp_files` and `temp_bytes` in the map's spill section.

**Fix.** Carry a `depth` column, stop at an explicit maximum, and cap total rows.
Cycle-guard with a visited array or a `UNION` (not `UNION ALL`) when the data can
contain loops. Return a truncation flag rather than a silently partial answer.

### 13. Materialize hot neighborhoods, with explicit staleness

**Fingerprint.** The same neighborhood is re-traversed on every request, and the
table holding the precomputed answer takes 3.0 million updates against 6,321 rows
*(observed)*.

**Fix.** Materializing a context pack per entity is the right shape; rewriting
the same rows hundreds of times each is not. Store `built_at`, the source
version, and the inputs' versions on the pack, serve it while it is fresh, and
rebuild on a schedule or on an explicit invalidation — not on every write that
touches any input. A cache without a staleness field is a second source of truth.

### 14. Evidence rows are append-only, pruned by policy

**Fingerprint.** An evidence or mention table updated in place, so the record of
why a relationship was asserted changes under the reader's feet.

**Fix.** Append evidence with a timestamp and a source pointer; supersede rather
than mutate. Prune on a written retention policy, not opportunistically. This is
the constitution's governance requirement — lineage and evidence must survive.

### 15. Update-heavy hot rows bloat

**Fingerprint.** 1.1 million updates against 252,720 rows *(observed)*, with
`n_dead_tup` climbing and `last_autovacuum` old. Each update writes a new row
version; when the updated column is indexed, HOT pruning cannot apply and every
index on the table is maintained too.

**Fix.** Check `n_dead_tup` and `last_autovacuum` in the map before proposing
anything. Move high-churn counters (`last_seen_at`, `mention_count`) into a
narrow side table with no indexes on the churning columns, lower `fillfactor` on
the hot table so HOT updates can stay on-page, or tune per-table autovacuum
thresholds. Do not run `VACUUM FULL` on a live large table — it takes an
`ACCESS EXCLUSIVE` lock.

## Sources

- [pgvector README](https://github.com/pgvector/pgvector) — index options, GUC
  names and defaults, iterative scan (0.8.0), filtering guidance.
- [Crunchy Data: hybrid vector search with Postgres and pgvector](https://www.crunchydata.com/blog/hybrid-vector-search)
  — HNSW behaviour under filters and the recall consequence of `ef_search`.
- [dbi-services: pgvector, a guide for DBA — part 2, indexes (March 2026)](https://www.dbi-services.com/blog/pgvector-a-guide-for-dba-part-2-indexes-update-march-2026/)
  — build parameters, index size, and maintenance cost from a DBA view.

Verified against the pgvector docs on 2026-09-05: `hnsw.iterative_scan` accepts
`off`, `strict_order`, `relaxed_order` and shipped in 0.8.0; `hnsw.ef_search`
defaults to 40. Build defaults `m=16` / `ef_construction=64` are the pgvector
README's stated defaults.
