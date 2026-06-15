<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Database Agent Constitution

Use this reference when an agent is assessing or designing database, storage,
query-planning, vector-search, retrieval, migration, or data-integrity work.
It is a lens for better diagnosis and implementation briefs. It is not a new
agent role.

## Core Rule

Prefer the fewest reliable primitives that can carry the most real workload.
New infrastructure, new execution paths, and new caches must earn their place
by reducing total complexity, improving correctness, or serving observed query
shapes.

## General Software Rules

1. **Prefer simple systems over clever integrations.** Avoid adding queues,
   services, databases, or protocols unless they reduce total operational load.
2. **Minimize distinct code paths.** Express new behavior as data or
   configuration through an existing path when that keeps the system clearer.
3. **Make invariants explicit.** Name the invariants the change preserves, then
   defend them with types, checks, tests, or runtime guards.
4. **Design for graceful failure.** Dependencies fail. Prefer detect, degrade,
   retry, and log over crash-only behavior when correctness allows it.
5. **Prefer idempotent, restart-safe writes.** Retries after timeouts, process
   restarts, or partial failure should not corrupt state or duplicate effects.
6. **Put pressure on hot paths.** Optimize the core query/write paths that
   traffic actually uses before adding specialized edge paths.
7. **Treat operations as code quality.** New subsystems need observable errors,
   bounded recovery behavior, and enough logging to debug production failures.

## Database Rules

1. **Use a small primitive set.** Prefer scans, index lookups, range lookups,
   append/log writes, manifest updates, compaction, and vector search primitives
   over bespoke execution paths.
2. **Reuse internal structures for ad-hoc work.** Joins, sorts, grouping, and
   temporary query plans should reuse the same planning/index structures where
   practical.
3. **Treat durability as an invariant.** "No committed data is lost on process
   crash or restart" must be explicitly preserved or explicitly out of scope.
4. **Keep critical writes local and understandable.** Extra infrastructure on
   the write path must handle unavailability, duplicates, and reordering.
5. **Make trade-offs visible.** For schema, index, cache, or storage-layout
   changes, state read cost, write cost, space amplification, operational
   complexity, and failure behavior.
6. **Optimize for observed query shapes.** Use real queries, latency targets,
   and volume before choosing indexes or storage layouts.
7. **Test invariants, not just happy paths.** Cover replay safety, crash or
   rollback behavior, query-result stability after index/schema changes, and
   migration idempotence.

## Vector And Retrieval Addendum

Use this addendum when the work touches embeddings, vector indexes, semantic
retrieval, reranking, object storage, or search-specific storage tiers.

1. **One durable truth, rebuildable accelerators.** Treat blob/object storage,
   SQLite, Postgres, or the chosen canonical store as truth. Treat RAM, SSD,
   HNSW/IVF/PQ indexes, and derived caches as rebuildable unless the design
   states otherwise.
2. **Compile features into segment primitives.** Prefer append or rewrite
   segment, compact or merge, build/update index, search segment plus filter,
   top-k merge, rerank, and versioned manifest update.
3. **Batch object-storage work.** Prefer batched writes, compaction, and
   reindexing over per-document micro-operations when object-store latency or
   request cost matters.
4. **Degrade to slower correctness.** Missing hot caches or partial indexes
   should fall back to simpler scans, fewer segments, or lower-recall modes only
   when the returned confidence/status makes the degradation explicit.
5. **Make the cost model explicit.** State cold/warm latency, storage cost,
   rebuild cost, compaction behavior, and repair path.
6. **Make ingestion replay-safe.** Duplicate ingestion, interrupted uploads, and
   replays must not corrupt manifests or lose indexed data.

## Required Assessment Fields

When a database or retrieval agent uses this reference, include:

- `invariants`: the correctness properties the diagnosis or design protects.
- `reused_primitives`: existing query, index, write, manifest, or retrieval paths
  the proposal reuses.
- `new_dependencies`: any new system, queue, service, cache, or storage tier.
- `tradeoffs`: read cost, write cost, space cost, operational cost, and failure
  behavior.
- `failure_modes`: crashes, partial writes, dependency outages, duplicate
  messages, out-of-order updates, stale caches, and rollback behavior.
- `tests`: invariant-level checks, replay tests, migration tests, query-result
  stability tests, or fuzz/property tests.

## Prompt Blocks

### General Coding Agent

Follow the software constitution: fewer systems, fewer code paths, explicit
invariants, graceful failure, idempotent writes, hot-path optimization, and
operable designs. Before proposing code, list the invariants, dependencies,
reused primitives, and failure modes.

### Database Agent

You are the database agent. Optimize for SQLite-style minimalism and shared code
paths. Prefer reusing scans, indexes, logs, manifests, ranges, and transaction
primitives over creating new execution paths. Preserve crash safety, idempotent
replay, and invariant correctness above cleverness.

### Vector Or Retrieval Agent

You are the vector/retrieval database agent. Prefer one durable source of truth,
rebuildable hot caches, segment-based ingestion/search, batched object-storage
work, explicit cost models, and replay-safe manifests. If an index or cache is
missing, return a slower but honest result with explicit degradation status.

## Build-Loop Usage

- Keep this as a reference document until repeated runs prove a narrower rule
  deserves promotion.
- Use `database-assessor` for diagnosis and evidence. Use `implementer` for the
  fix after the "what" is decided.
- Do not add a separate vector or database implementation agent until the task
  repeats, has a stable input/output envelope, and cannot be expressed as a
  skill/reference plus the existing `implementer` role.
- If this guidance repeatedly prevents or fixes real defects, promote the
  validated pattern through build-loop-memory as a lesson or constitution
  candidate, with source doc, affected runs, and evidence.
- For wholesale memory intake, write a docs/source summary and coverage artifact
  first; promote only the stable, evidence-backed rules.

## Reference Reading

These links were used only to calibrate the vocabulary in this document. The
binding rules above are the local build-loop rules.

- [CMU Database Group: turbopuffer object-storage-native database for search](https://db.cs.cmu.edu/events/pg-vs-world-turbopuffer-simon-eskildsen/)
- [turbopuffer: Simon Eskildsen on scaling Shopify, building turbopuffer, and databases](https://turbopuffer.com/blog/podcast-cafe-cursor)
- [Micah Kepe: SQLite query optimizer deep dive](https://micahkepe.com/blog/sqlite-query-optimizer/)
- [DEV Community: SQLite correctness under hardware failures](https://dev.to/lovestaco/how-sqlite-turns-hardware-chaos-into-correctness-40ba)
- [Amplify Partners: turbopuffer and vector databases](https://www.amplifypartners.com/barrchives/how-turbopuffer-is-building-the-future-of-vector-databases-with-ceo-simon-eskildsen)
- [turbopuffer: object-storage cost and database trade-offs](https://turbopuffer.com/blog/podcast-barrchives-podcast-how-build-10x-cheaper-object-storage)
