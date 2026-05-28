# Memory Setup

Build-loop's advisory judges and the Phase 1 Assess memory-load step read from
one consolidated tree under `~/dev/git-folder/build-loop-memory/` by default:

| Tier | Location | Owner | Versioning |
|---|---|---|---|
| Global | `~/dev/git-folder/build-loop-memory/` plus top-level lanes such as `lessons/` | This user | **Should be in a private git repo** (your lessons live here) |
| Project | `~/dev/git-folder/build-loop-memory/projects/<slug>/` (slug derived via `scripts/_paths.derive_slug_from_cwd` — basename of the git repo root, lowercased + normalized; `workers/` sub-component becomes `<slug>/workers`) | This user | Same private repo as global |

> **History** — until PR 3 of the memory-consolidation series (merged 2026-05-13), the legacy per-repo location was also read by `memory_facade._resolve_memory_dirs` as a transitional shim. As of PR 3, only the consolidated tree is read; any pre-migration content still at the legacy path is invisible. Operators with such content should run `scripts/migrate_project_memory.py --apply` (idempotent), then `scripts/cleanup_legacy_memory_stubs.py --apply` to remove the now-inert `.MOVED.md` stubs.

The build-loop public repo ships only the **scaffolding** — templates and the setup script. Your actual lessons, constitution rules, and patterns belong in a private repo because they contain references to specific projects, decisions, and operator preferences that aren't appropriate for public distribution.

## Quick start

```bash
# Bootstrap with templates (creates ~/dev/git-folder/build-loop-memory/ if missing,
# seeds constitution.md + MEMORY.md from templates/memory/)
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/install_memory.py

# Check status anytime
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/install_memory.py --check
```

This creates:

```
~/dev/git-folder/build-loop-memory/
├── constitution.md     # template — replace with your invariants
└── MEMORY.md           # template — index for entries you add
```

## Linking to a private repo

You have a few options for versioning the global memory. The recommended one is **clone a private repo into the memory dir**:

### Option A — Clone a private repo at install time

```bash
# Empty / non-existent build-loop-memory directory required
python3 scripts/install_memory.py --link-repo git@github.com:<you>/<your-memory-repo>.git
```

The repo should contain `constitution.md` and `MEMORY.md` plus your existing lessons. The install script will clone it directly; no templates are seeded (the repo provides content).

### Option B — Bootstrap with templates, then init a repo

```bash
python3 scripts/install_memory.py
cd ~/dev/git-folder/build-loop-memory
git init
git add constitution.md MEMORY.md
git commit -m "init: build-loop memory scaffolding"
git remote add origin git@github.com:<you>/<your-memory-repo>.git
git push -u origin main
```

### Option C — Override the canonical root

If your private memory repo lives somewhere else, set
`BUILD_LOOP_MEMORY_STORE_ROOT` or pass `--dest <path>` to the setup script. The
scripts resolve the root through `scripts/_paths.py`.

## What goes in the global store

Add entries over time as you accumulate lessons:

| Type | Filename pattern | When to add |
|---|---|---|
| `feedback` | `feedback_<slug>.md` | After a user correction OR a validated success worth remembering |
| `pattern` | `pattern_<slug>.md` | Recurring solution worth naming |
| `reference` | `reference_<slug>.md` | Durable facts about external systems / topologies |
| `decision` | `decision_<slug>.md` | One-shot architectural decisions |
| `user` | `user_<slug>.md` | User-profile facts that shape collaboration style |

Each file uses YAML frontmatter:

```markdown
---
name: <kebab-case-slug>
description: <one-sentence summary for relevance scoring>
metadata:
  type: feedback | pattern | reference | decision | user
---

# Title

Body content. Link related entries with [[other-slug]].

For feedback / pattern entries, structure as:
- The rule or fact
- **Why:** the rationale
- **How to apply:** when/where the rule fires
```

`MEMORY.md` is the index. Each entry there is one line:

```markdown
- [Title](filename.md) — one-line hook for relevance matching
```

Keep `MEMORY.md` under ~200 lines; entries past that get truncated when loaded into orchestrator context.

## What about decisions?

The **canonical decision store** is
`~/dev/git-folder/build-loop-memory/projects/<project>/decisions/`. It is
project-tagged and written by `scripts/write_decision.py` — it captures the
discrete "we decided X" events of any project.

The top-level `lessons/` lane is for **cross-project lessons** — patterns and
feedback that apply broadly, not project-specific decisions.

```
~/dev/git-folder/build-loop-memory/                  # canonical root
~/dev/git-folder/build-loop-memory/lessons/          # cross-project lessons
~/dev/git-folder/build-loop-memory/projects/<slug>/  # project-local memory
~/dev/git-folder/build-loop-memory/projects/_archive/<slug>/  # retired projects, still queryable
<repo>/.build-loop/memory/                           # legacy project location — no longer read (PR 3 removed the shim); migrate via scripts/migrate_project_memory.py
<repo>/.episodic/decisions/                          # legacy local decision store (migration/archive)
```

## Constitution rules

`constitution.md` is the highest-priority store — loaded at every Phase 1 Assess and cited by advisory judges. Keep it concise:

- **Stable rule IDs** (`C-SEC/no_secret_in_repo`, `C-AUTH/owner_only_writes`) — never renumber
- **One sentence per rule** — detail goes in linked memory entries
- **Under 200 lines total** — judges re-read this on every Phase 3 checkpoint

Edit the template that the install script seeded — the prefilled rules are reasonable defaults but your invariants may differ.

## Verifying setup

```bash
# Check status
python3 scripts/install_memory.py --check

# Test memory recall (will exit 0 even if backends are down)
python3 scripts/memory_facade.py recall --query "test" --limit 3

# Backend health probe
python3 scripts/backend_health.py
```

If any of these fail, see `references/memory-systems.md` for the full backend topology and graceful-degradation contract.

## Privacy

Treat `~/dev/git-folder/build-loop-memory/` as containing potentially-sensitive context:

- Operator preferences, project-specific decisions, client names if you write them in
- Constitution rules may reveal architectural patterns from past projects
- Feedback entries often quote real exchanges

The recommendation is a **private git repo** (`Option A` or `B` above), not the public build-loop repo. Build-loop ships only the templates.
