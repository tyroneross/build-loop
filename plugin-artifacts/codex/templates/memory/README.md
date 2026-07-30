# build-loop-memory — public seed (scaffolding only)

This folder is the **self-contained seed** for a build-loop-memory store. It is the
single place that holds everything build-loop ships for memory setup: the generic
templates, the privacy allowlist (`manifest.json`), and this layout spec.

**What ships vs. what does not.** The memory *store* itself is **private and never
ships** — it holds personal lessons, project decisions, raw artifacts, and operator
paths. Only this **scaffolding** ships in the public plugin: generic `*.template`
files + an allowlisted manifest. `scripts/install_memory.py` *materializes* the store
from this seed at a **separate root** (`$BUILD_LOOP_MEMORY_ROOT` → legacy
`~/dev/git-folder/build-loop-memory` → fresh `~/.build-loop-memory`), seeding only
missing files (idempotent, never overwrites).

## Files in this seed (all of them)

| File | Role |
|------|------|
| `manifest.json` | Privacy allowlist + the complete structure spec. `install_memory.py --validate-seed` rejects any seed file not allowlisted here, and scans for secret/PII deny-patterns. |
| `constitution.md.template` | First-run rule scaffold → `constitution.md` in the store. |
| `MEMORY.md.template` | Global memory-index scaffold → `MEMORY.md` in the store. |
| `charter.md.template` | Project-charter scaffold (North Star + commander's-intent + invariants) → `charter.md`. Placeholder bodies; filled per project at run time. |
| `README.md` | This document. |

Empty directories are **not** shipped (git can't track them, and the manifest is
strict-allowlist); they are **generated** by `install_memory.py` at setup. The full
target layout is below and is declared machine-readably in `manifest.json` →
`generated`.

## Store layout that install_memory.py generates

```
<memory-root>/                     # private, separate root — NEVER the plugin cache
├── constitution.md                # from constitution.md.template
├── MEMORY.md                      # from MEMORY.md.template
├── charter.md                     # from charter.md.template (optional)
├── indexes/                       # rebuildable local indexes
└── projects/
    ├── README.md                  # generated project-lane guide
    └── <slug>/                    # one per project (--ensure-project <slug>)
        ├── raw/                   # raw-source lanes:
        │   ├── documents/  data/  db/  runtime/
        │   └── agent-artifacts/  artifacts/  files/
        ├── apps/        assets/      architecture/  context/
        ├── decisions/   docs/        features/      formats/
        ├── indexes/     lessons/     plugins/       product/
        ├── prompts/     research/    semantic/      skills/
        └── sources/     testing/     tradeoffs/
```

`raw/` lanes (7): documents, data, db, runtime, agent-artifacts, artifacts, files.
Per-project topic dirs (20): apps, assets, architecture, context, decisions, docs,
features, formats, indexes, lessons, plugins, product, prompts, raw, research,
semantic, skills, sources, testing, tradeoffs.

## Setup

```bash
python3 scripts/install_memory.py                      # bootstrap store + seed missing files
python3 scripts/install_memory.py --ensure-project foo # add projects/foo/ scaffold
python3 scripts/install_memory.py --check              # status, no writes
python3 scripts/install_memory.py --validate-seed      # validate THIS seed (allowlist + deny-scan)
```

The store is the durable, private half; this seed is the public, structure-only half.
