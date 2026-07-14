---
name: build-loop:data-plane-worktrees
description: "Use when a Build Loop run or Git worktree touches mutable non-Git state: SQLite or file-backed databases, PostgreSQL databases/schemas and migrations, generated search/vector indexes, Docker volumes or service projects, mutable file trees, caches that cannot be rebuilt safely, or external cloud/account namespaces. Inventories each surface, selects per-worktree, shared-readonly, shared-serialized, or external-namespaced isolation, registers it in the run data manifest, validates collisions before writes, and requires a terminal disposition at closeout."
user-invocable: false
---

<!-- SPDX-FileCopyrightText: 2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Data-Plane Worktrees

Treat a Git worktree as source-plane isolation only. Inventory and isolate every
mutable resource the run can read or write before implementation starts.

## Classify every surface

| Isolation | Use for | Required evidence |
|---|---|---|
| `per_worktree` | SQLite copies, generated indexes, mutable file stores, disposable local state | `path` under the allocated run data root |
| `shared_readonly` | Canonical raw inputs, fixtures, large immutable corpora | `writable: false` |
| `shared_serialized` | A central writer that cannot be cloned, such as a canonical index updater | Stable `writer` key naming the serialization authority |
| `external_namespaced` | PostgreSQL databases/schemas, Compose projects, buckets, queues, cloud accounts | Stable `namespace` unique to the run |

Prefer `per_worktree`. Use shared mutation only when cloning or namespacing is
not practical and one explicit writer serializes all changes.

## Workflow

1. Read the run identity and baseline manifest from
   `.build-loop/state.json.execution.{build_loop_id,data_manifest_path,data_root}`.
   Fresh isolated runs create these fields automatically.
2. Inventory all non-Git state read or written by code, tests, migrations,
   services, hooks, and generated artifacts. Treat an omitted surface as an
   unresolved isolation risk.
3. Add each surface through the validator before any adapter provisions or
   mutates it:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/data_plane.py" add \
     --workdir "$PWD" \
     --manifest "$DATA_MANIFEST" \
     --surface-json '<surface-json>'
   ```

4. Run `validate` before the first write. A collision, escaping path, invalid
   shared writer, or malformed peer manifest fails closed.
5. Let the repository-specific adapter perform the actual copy, migration,
   service provisioning, or namespace creation. The generic lifecycle never
   guesses credentials or destroys external resources.
6. After adapter cleanup or an explicit retain decision, record each writable
   surface as `closed`, `retained`, or `not_owned`:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/data_plane.py" close \
     --manifest "$DATA_MANIFEST" \
     --surface-id '<surface-id>' \
     --status closed
   ```

7. Run `terminal` before branch closeout. The canonical closeout gate repeats
   this check and blocks active, deferred, or errored owned surfaces.

## Surface shape

```json
{
  "id": "search-index",
  "kind": "generated-index",
  "authority": "derived",
  "isolation": "per_worktree",
  "writable": true,
  "resource_key": "index:search:<run-id>",
  "path": "search-index",
  "status": "active"
}
```

Use a stable `resource_key` for the underlying resource, not a display label.
Two active manifests may share a key only when both use `shared_serialized` and
declare the same non-empty `writer`.

## Database and file rules

- SQLite: snapshot into the run data root; never open the canonical file for
  writes from two worktrees.
- PostgreSQL: prefer database-per-run for migrations. Schema-per-run is
  acceptable only when extensions, roles, and database-level DDL are out of
  scope.
- Generated indexes: build per run; merge source changes first, then rebuild the
  canonical index once through its declared writer.
- Docker/Compose: derive a unique project and volume namespace from the run id.
- Sensitive or large raw files: mount or reference read-only; keep copied
  derivatives in the run data root.

The run data root lives at canonical `.build-loop/data/<run-id>/`, outside the
linked source worktree. Putting ignored data inside a linked worktree makes
normal non-force `git worktree remove` fail.

## Verification

Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/data_plane.py" validate \
  --workdir "$PWD" --manifest "$DATA_MANIFEST" --run-id "$BUILD_LOOP_ID"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/data_plane.py" terminal \
  --workdir "$PWD" --manifest "$DATA_MANIFEST" --run-id "$BUILD_LOOP_ID"
```

Validation cost scales with active manifest and surface count; it does not copy
databases or rebuild indexes. Adapter-specific provisioning cost remains
explicit in the plan and performance evidence.

For the lifecycle contract and closeout integration, read
`docs/SPEC-run-worktree-isolation.md` and `scripts/data_plane.py`.
