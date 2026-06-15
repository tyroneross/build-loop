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

## AI-First Database Principle

For AI systems, the database is not memory. The database is the storage and
retrieval substrate. Memory is the logical layer that decides what should be
remembered, forgotten, updated, retrieved, trusted, and injected into context.

An AI-first database stack should answer:

```text
What information should the agent use, why, from where, with what permission,
at what freshness level, and with what evidence?
```

The default stack is:

```text
canonical database
+ raw artifact store
+ searchable indexes
+ explicit memory records
+ cache
+ audit/event log
```

Do not make a vector database the system of record. Store truth in a
transactional database, append-only log, or object store, then build vector,
keyword, graph, cache, and summary layers around it.

## AI-First Capability Model

| Capability | Agent requirement |
|---|---|
| Truth | Canonical records remain stable, durable, and auditable. |
| Meaning | Summaries, metadata, tags, embeddings, and relationships are derived from truth. |
| Retrieval | Exact search, semantic search, filters, ranking, and reranking can be combined. |
| Memory | Scoped facts, preferences, procedures, and episodes can be updated or superseded. |
| State | Runs, steps, tool calls, approvals, retries, and resumable work are represented. |
| Governance | Permissions, lineage, evidence, retention, deletion, and audit are enforceable. |

## Substrate Selection

Use the simplest substrate that satisfies the query, governance, and scale
requirements:

| Need | Prefer | Avoid |
|---|---|---|
| Canonical app data, users, permissions, agent runs | Postgres/Supabase/MySQL | Vector-only truth |
| Local-first or offline agent workspace | SQLite/SwiftData/Room/IndexedDB plus files | Raw files as the only index |
| Raw files, media, PDFs, source evidence | Object store or filesystem plus metadata DB | Filenames as semantic truth |
| Moderate semantic search inside app data | Postgres plus pgvector plus full-text search | Separate vector infra by default |
| Large semantic corpus or high retrieval QPS | Dedicated vector DB with metadata filters | Unsynced side index |
| Exact terms, facets, product/legal/log search | Search engine or Postgres FTS | Pure vector search |
| Durable relationships, lineage, dependencies | Relational edges or graph DB | Graph for unstable extracted entities |
| Fast working state, locks, idempotency, caches | Redis/KV with versioned keys | Cache as durable memory |
| Replay, audit, rebuildable projections | Event log, JSONL, Kafka, or event table | CRUD-only state for agent actions |
| Historical analytics and batch enrichment | Warehouse/lakehouse feeding a serving layer | Warehouse as runtime memory |

For many AI apps, the default recommendation is Postgres/Supabase with pgvector,
full-text search, object storage, row-level permissions, and event/audit tables.
Add a dedicated vector database, search engine, graph database, or warehouse only
when query shape, scale, governance, or economics requires it.

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

## Retrieval And Metadata Rules

1. **Route by query type.** Exact IDs, SKUs, dates, names, and legal clauses
   should use SQL or keyword search first. Conceptual similarity should use
   vector search. Relationship questions should use joins or graph traversal.
   Recent activity should use the event log. Personal preferences should use
   explicit memory records.
2. **Use hybrid retrieval by default in production.** Combine metadata filters,
   keyword search, vector search, and reranking when correctness matters.
3. **Filter before ranking when security or scope matters.** Tenant, user,
   project, status, source, classification, version, and permission filters are
   correctness controls, not ranking hints.
4. **Track embedding and index versions.** Store `embedding_model`,
   `embedding_version`, `index_version`, chunking version, and source version so
   stale retrieval can be explained and rebuilt.
5. **Version cache keys.** Retrieval, embedding, permission, tool-result, and
   prompt-fragment caches need version keys such as `index_version` and
   `permission_version`; otherwise stale or unauthorized context can leak.
6. **Treat summaries as derived.** Summary tables and materialized context views
   are useful, but they must link back to source evidence.

## File And Artifact Rule

File structure rarely dictates one specific database. It creates query and
governance requirements that make some substrates better than others.

Every AI-visible artifact should have a database record with the relevant subset
of:

```text
artifact_id, owner, tenant, project, source, mime_type, checksum, version,
permissions, status, classification, summary, entities, topics,
extracted_text_pointer, embedding_status, retention_policy
```

The raw file remains evidence. The database row supplies ownership, permissions,
freshness, retrieval status, and lineage. The indexes are rebuildable
derivatives.

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
- `substrate_boundary`: which store is canonical truth, which stores are
  derived indexes/caches, and which layer is memory policy.
- `governance`: permission, lineage, retention, deletion, and audit controls.

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
- Near-term incorporation: require database/retrieval assessments to distinguish
  canonical substrate, derived indexes/caches, explicit memory records, and
  audit/event logs.
- Medium-term incorporation: add a deterministic checklist that flags designs
  where vector search is treated as source of truth, cache keys omit permission
  or index versions, or AI-visible files lack metadata records. A first
  conservative slice ships as `scripts/db_substrate_lint.py --workdir
  <target-repo> --json` — an **advisory, WARN-only** lint (wired into no
  blocking gate) covering two grep-able patterns: version-less
  embedding/retrieval rows or cache keys (Retrieval rules #4/#5) and AI-visible
  artifacts lacking a metadata record (File And Artifact Rule). It is seeded
  from two observed atomize-ai failures (evidence sample 1) and cites the rule +
  evidence per finding. The semantic items above (vector-as-truth,
  derived-summary-as-authoritative) stay an assessor-LLM lens — a grep would
  false-positive and rot — until repeated runs prove a narrower rule.
- Later incorporation: only add a dedicated database/vector agent or memory
  guardian after repeated runs show a stable input/output envelope that cannot
  be covered by this reference plus `database-assessor` and `implementer`.

## Reference Reading

These links were used only to calibrate the vocabulary in this document. The
binding rules above are the local build-loop rules.

- [CMU Database Group: turbopuffer object-storage-native database for search](https://db.cs.cmu.edu/events/pg-vs-world-turbopuffer-simon-eskildsen/)
- [turbopuffer: Simon Eskildsen on scaling Shopify, building turbopuffer, and databases](https://turbopuffer.com/blog/podcast-cafe-cursor)
- [Micah Kepe: SQLite query optimizer deep dive](https://micahkepe.com/blog/sqlite-query-optimizer/)
- [DEV Community: SQLite correctness under hardware failures](https://dev.to/lovestaco/how-sqlite-turns-hardware-chaos-into-correctness-40ba)
- [Amplify Partners: turbopuffer and vector databases](https://www.amplifypartners.com/barrchives/how-turbopuffer-is-building-the-future-of-vector-databases-with-ceo-simon-eskildsen)
- [turbopuffer: object-storage cost and database trade-offs](https://turbopuffer.com/blog/podcast-barrchives-podcast-how-build-10x-cheaper-object-storage)
