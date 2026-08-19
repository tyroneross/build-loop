---
name: build-loop:data-plane-worktrees
description: "Decide how a build/worktree safely isolates mutable non-Git state (databases, caches, volumes, indexes) so parallel work doesn't collide. Use when the user asks \"is it safe to run this in parallel\" or \"will this migration collide with the other branch\", or a run touches a database/cache/volume. Covers SQLite and PostgreSQL databases, generated search/vector indexes, Docker volumes, mutable file trees, and external cloud/account namespaces."
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

## Migration rehearsal (isolated — production never touched)

Run on **Fable** (DB actions pin Frontier tier — see `skills/model-tiering`). Rehearse migration-first deploys against a throwaway **local** DB before any production migration. Hard guard: assert the target URL contains `@127.0.0.1:` (or your local host) AND a `rehearsal` marker before every create/apply/drift/teardown; never read `DATABASE_URL`/`DIRECT_URL` when they point at prod.

1. Create ephemeral DB (`atomize_rehearsal_<run>`), materialize the **base (origin/main) schema** so ALTER targets and FK parents exist.
2. Apply each migration in order (`psql -v ON_ERROR_STOP=1 -f`); re-apply to prove **idempotency** (exit 0 both times).
3. Drift-check the migrated DB vs the branch datamodel — exit 0 = no missing columns/tables (no runtime `P2022`).
4. Functional-test the new CHECK/FK constraints (bad value rejected, good value accepted).
5. Drop the ephemeral DB **and any cluster-global roles/extensions you created** (roles are not per-DB — verify 0 remain).

**Prisma 7 CLI (verified 2026-07-22 — several v6 flags were removed):**

```bash
export PRISMA_MIGRATE_URL="postgresql://<user>@127.0.0.1:5432/atomize_rehearsal_<run>"  # highest precedence in prisma.config.ts
# base schema DDL from a datamodel (--from-url REMOVED; --to-schema-datamodel REMOVED → use --to-schema):
git show origin/main:prisma/schema.prisma > /tmp/base.prisma
npx prisma migrate diff --from-empty --to-schema /tmp/base.prisma --script -o /tmp/base.sql
psql -v ON_ERROR_STOP=1 -d atomize_rehearsal_<run> -f /tmp/base.sql
# drift: migrated live DB vs branch datamodel (-o REQUIRED — env-injection notices pollute stdout):
npx prisma migrate diff --from-config-datasource prisma.config.ts --to-schema prisma/schema.prisma --exit-code -o /tmp/drift.txt
```

Supabase-CLI migrations (`supabase/migrations/*.sql`) are plain SQL applied by `supabase migration up`/`db push` — apply them with `psql` in the rehearsal, NOT `prisma migrate deploy` (Prisma's `migrations.path` tracks `prisma/migrations` only). Additive `add column if not exists` (nullable) + `NOT VALID`→`VALIDATE CONSTRAINT` is the safe pattern; `VALIDATE` full-scans the table under SHARE UPDATE EXCLUSIVE (writes continue) — schedule off-peak when large.

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
