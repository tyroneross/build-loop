# Design note — durable memory identity: stable ID + typed project graph

Status: **spec / design capture** (not scheduled for build). 2026-07-10.
Predecessor: the `memoryProjectSlug` pin (merged, build-loop `f039428`) — the band-aid this supersedes.

## Bottom line

Project identity in build-loop-memory is derived from a **mutable** thing (the git folder name),
so a rename silently orphans a project's memory (root cause of the 2026-07-09 P0-4). The merged
**pin** stops the bleeding going forward but is a single value: it cannot model a **rename chain**,
**forward old references**, or express **provenance/dependency** between projects. The durable fix
is the model PersonalLLMWiki already proved over years of notes: **a stable ID that is never
derived, plus typed edges** (`renamed_from`/`alias`, `derived_from`, `depends_on`) on the graph the
store already builds. This note specs that upgrade.

## Problem

`scripts/_paths.py::derive_slug_from_cwd` keys a project by `basename(repo_root)`. Consequences:

- **Rename → orphan.** `ai-assistant` → `rosslabs-ai-assistant` stranded 7 lessons + 2 retros under
  the old slug; `backend_health` reported the decisions store `dir_missing` (P0-4).
- **The pin is a single value.** `memoryProjectSlug` pins the *current* slug. It does NOT:
  1. reconcile writes **scattered across multiple pre-pin slugs** (rename twice before pinning →
     `projects/a` and `projects/b`, pin points at one);
  2. **forward old references** — a cross-project link or index entry naming the old slug no longer
     resolves;
  3. model **provenance** (fork/derived-from) or **dependency** (project B relies on A's contract).
- **The detector is a heuristic.** `backend_health.detect_possible_rename` picks the most name-similar
  sibling — for a multi-hop chain it guesses the closest, not the correct origin.

## What already exists (build on it, don't rebuild)

- **A graph** — `indexes/graph-nodes.jsonl` (~nodes are `memory-entry`) + `indexes/graph-edges.jsonl`
  (~1,450 edges). But project identity is a **string attribute** (`project: "..."`) on each memory
  node; there is **no project node** to attach rename/provenance edges to.
- **A path→project table** — `config/projects.yaml` maps `path → project`. It is **structurally
  unreachable** for real git repos today (dirname derivation short-circuits before the table is
  consulted). The alias mechanism exists and is bypassed.
- **The pin** — `_read_pinned_slug` (config-driven, graceful degradation). This is a *retrofit* of a
  stable ID: "assign a stable slug after the fact."

## Proposed design

### 1. Stable project ID, never derived

Assign a project ID at `build-loop init` (a UUID, or the first slug frozen as the ID), written to
`.build-loop/config.json`. The folder name becomes **display only**; resolution keys on the ID. The
merged pin is the manual version of this — `init` makes it automatic for new projects so no one has
to pin reactively.

### 2. Project as a first-class node

Add a `project` node type to the graph (id, canonical slug, display name, store path, created_at).
Memory entries reference the **project node id**, not a raw string — so identity lives in one place.

### 3. Typed project edges (the payload)

| Edge | Resolves |
|---|---|
| `renamed_from` / `alias` (→ prior slug/id) | a lookup on **any** historical slug walks to the current store — closes rename-chain + old-reference-forwarding in one mechanism |
| `derived_from` / `forked_from` (→ source project) | a forked/derived project can inherit or reference its origin's lessons (provenance) |
| `depends_on` (→ other project) | cross-project dependency (B's decisions depend on A's contract) |

Mirrors the PersonalLLMWiki relationship set (`supersedes`/`superseded_by`/`depends_on`/`extends`/
`related`) and its rule: *IDs are stable; if you must rename, follow the edges — never re-derive.*

### 4. Alias-walking resolver

`project_resolver.resolve_project` gains an alias walk: derive candidate slug → if no store, follow
`renamed_from`/`alias` edges (bounded, cycle-guarded) to the canonical project node → resolve there.
`projects.yaml` becomes the seed of this table instead of dead config. Deterministic, no LLM.

### 5. Migration

One-time doctor pass: (a) mint a stable ID per existing `projects/<slug>/`; (b) backfill
`project` node + entry references; (c) convert any existing `memoryProjectSlug` pin into a
`renamed_from` edge; (d) seed alias edges from `projects.yaml`. Idempotent, gated by
`validate_memory_store --strict` + `self_mod_verify`.

## Phasing

- **Phase 0 — pin (DONE).** Stops silent orphaning forward. `f039428`.
- **Phase 1 — stable ID + `renamed_from`/`alias` edges + alias-walking resolver.** Closes the
  multiple-rename + old-reference-forwarding gaps. This is the high-value slice.
- **Phase 2 — `derived_from` / `depends_on` edges.** Provenance + cross-project dependency. Pays off
  once several related repos share memory (this session already spanned ai-assistant, build-loop, and
  build-loop-memory as linked stores).

## Tradeoffs / risks

- **Migration is the cost.** Minting IDs + backfilling references touches every project lane; must be
  idempotent and gated. This is why Phase 1 is opt-in-per-store, not a flag day.
- **Keep the resolver deterministic.** Alias walking is graph traversal, not inference — bound the
  depth and guard cycles. No LLM in the identity path.
- **Backward compatibility.** Unmigrated stores must keep working via the current dirname/pin path;
  the alias walk is a fallback that only fires when direct resolution misses.
- **Don't over-build.** If cross-repo forks/dependencies never materialize, Phase 1 alone (rename
  robustness) is sufficient; Phase 2 is justified only by real `derived_from`/`depends_on` need.

## Open questions

- ID form: UUID (opaque, rename-proof) vs. frozen-first-slug (human-readable, mostly rename-proof)?
- Does `renamed_from` live in the graph edges, in `projects.yaml`, or both (graph as truth, yaml as
  seed)?
- Should `depends_on` between projects drive *recall* (surface A's lessons when working B), or only
  provenance display? Recall coupling is powerful but risks context bloat — gate it.

## Reference

PersonalLLMWiki model — stable frontmatter `id:`, typed relationships, "IDs are stable; avoid
renaming; if unavoidable, grep every `[[old-id]]` ref first" (`ObsidianVault/brain/rules.md`,
`WORKSPACE.md` §Dependency & Change Model). The problem build-loop is hitting is the one the vault
solved by refusing to derive identity from location.
