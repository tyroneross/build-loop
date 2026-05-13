# Memory Setup

Build-loop's advisory judges (`commit-auditor`, `promotion-reviewer`, `alignment-checker`) and the Phase 1 Assess memory-load step read from two stores:

| Store | Location | Owner | Versioning |
|---|---|---|---|
| Global memory | `~/.build-loop/memory/` | This user | **Should be in a private git repo** (your lessons live here) |
| Project memory | `<repo>/.build-loop/memory/` | This project's contributors | Project repo (gitignored or committed per project policy) |

The build-loop public repo ships only the **scaffolding** — templates and the setup script. Your actual lessons, constitution rules, and patterns belong in a private repo because they contain references to specific projects, decisions, and operator preferences that aren't appropriate for public distribution.

## Quick start

```bash
# Bootstrap with templates (creates ~/.build-loop/memory/ if missing,
# seeds constitution.md + MEMORY.md from templates/memory/)
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/install_memory.py

# Check status anytime
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/install_memory.py --check
```

This creates:

```
~/.build-loop/memory/
├── constitution.md     # template — replace with your invariants
└── MEMORY.md           # template — index for entries you add
```

## Linking to a private repo

You have a few options for versioning the global memory. The recommended one is **clone a private repo into the memory dir**:

### Option A — Clone a private repo at install time

```bash
# Empty / non-existent ~/.build-loop/memory/ required
python3 scripts/install_memory.py --link-repo git@github.com:<you>/<your-memory-repo>.git
```

The repo should contain `constitution.md` and `MEMORY.md` plus your existing lessons. The install script will clone it directly; no templates are seeded (the repo provides content).

### Option B — Bootstrap with templates, then init a repo

```bash
python3 scripts/install_memory.py
cd ~/.build-loop/memory
git init
git add constitution.md MEMORY.md
git commit -m "init: build-loop memory scaffolding"
git remote add origin git@github.com:<you>/<your-memory-repo>.git
git push -u origin main
```

### Option C — Symlink to an existing repo elsewhere

If you already have a private memory repo (e.g. `~/dev/git-folder/build-loop-memory`):

```bash
# Move templates aside, then symlink
mv ~/.build-loop/memory ~/.build-loop/memory.bak
ln -s ~/dev/git-folder/build-loop-memory ~/.build-loop/memory
# (verify constitution.md + MEMORY.md exist in the linked repo)
```

The orchestrator and scripts don't care whether `~/.build-loop/memory/` is a real directory or a symlink — they `Read()` paths through it normally.

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

There's a separate **canonical decision store** at `~/dev/git-folder/build-loop-memory/decisions/<project>/` (your private repo). That store is project-tagged and written by `scripts/write_decision.py` — it captures the discrete "we decided X" events of any project.

The global `~/.build-loop/memory/` is for **cross-project lessons** — patterns and feedback that apply broadly, not project-specific decisions.

```
~/.build-loop/memory/                      # cross-project lessons (this guide)
~/dev/git-folder/build-loop-memory/        # canonical project-tagged decisions (separate repo)
<repo>/.build-loop/memory/                 # project-local overrides
<repo>/.build-loop/.episodic/decisions/    # legacy local decision store (deprecated)
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

Treat `~/.build-loop/memory/` as containing potentially-sensitive context:

- Operator preferences, project-specific decisions, client names if you write them in
- Constitution rules may reveal architectural patterns from past projects
- Feedback entries often quote real exchanges

The recommendation is a **private git repo** (`Option A` or `B` above), not the public build-loop repo. Build-loop ships only the templates.
