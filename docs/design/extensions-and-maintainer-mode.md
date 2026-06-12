<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Extensions + Maintainer Mode — per-user build-loop that survives updates

**Status:** approved design (2026-06-12) · **Owner:** Tyrone · **Reviewed by:** 4-persona panel (new-user/ergonomics, systems architect, reliability, skill-systems) — verdicts folded in.

## Problem

Build-loop's Learn phase mines patterns and drafts new skills/agents, but everything user-specific today is either project-local (`.build-loop/skills/experimental/`) or lost on plugin update (the cache is replaced wholesale). Users need:

1. A **retrospective → memory** loop whose outputs persist per-user (exists: Phase 6 Learn + `retrospective-synthesizer` → build-loop-memory).
2. A **personal layer** — learned skills/agents + their tuning — that survives any core update, is version-controlled, and is clearly separable from core ("what's original vs what's mine").
3. An **upstream-merge story**: download a new build-loop without losing or breaking the personal layer.
4. A **maintainer mode**: the build-loop author's own instance may recursively improve *any* aspect of build-loop, including core code — while consumers' learning is confined to their personal layer.

## Decision summary

**Core + userland overlay** (the vim/VS-Code pattern), with promotion routing by identity. Consumers learn into an overlay; the maintainer's overlay is the source repo itself. The contribution ladder is: overlay → PR upstream → fork (escape hatch, unsupported-but-possible).

## Architecture — three zones

| Zone | Path | Owner | On core update |
|---|---|---|---|
| **Core** | plugin cache (`~/.claude/plugins/cache/...`) | upstream | replaced wholesale |
| **Extensions** | `~/.build-loop/extensions/` | user | untouched by construction |
| **Memory** | memory store root (`install_memory.py`) | user | untouched (existing) |

Memory = knowledge (lessons, decisions, retrospectives). Extensions = capabilities (learned skills/agents + tuning/config). Deliberately separate (clarity over consolidation — user decision).

### Extensions dir layout

```
~/.build-loop/extensions/
├── .claude-plugin/plugin.json   # auto-generated: "build-loop-extensions"
├── .git/                        # user version control (init at setup; optional private remote)
├── pending/                     # Learn drafts land here — NEVER loaded
│   └── skills/<name>/SKILL.md
├── active/                      # approved artifacts — the only loaded surface
│   ├── skills/<ext-slug-name>/SKILL.md
│   ├── agents/<ext-slug-name>.md
│   └── config/                  # tuning the artifacts need (model pins, hook fragments)
└── graduated.json               # exclusion registry: artifact IDs absorbed into core
```

### Loading model

The extensions dir is itself a **Claude Code plugin** ("build-loop-extensions"), registered once at setup. Claude Code natively loads plugins side-by-side → zero merge logic; a core update cannot touch it. Only `active/` is referenced by the generated manifest; `pending/` is invisible to the loader. Codex host: the same dir syncs via the existing `sync_plugin_cache.py` path (same mechanism as core).

**Session-epoch guarantee:** artifacts load at session start only (native Claude Code behavior, stated as a contract). Mid-session core updates or Learn writes cannot tear a running session; new artifacts take effect next session.

## Artifact lifecycle (pending → active → graduated/archived)

1. **Draft.** `self-improvement-architect` writes to `extensions/pending/` (consumer) — project-local `experimental/` remains the per-project scratch tier below it.
2. **Checks (deterministic, pre-approval).** Schema/frontmatter lint · namespace enforcement · trigger-collision scan vs core + active · privacy deny-scan (reuses the memory-seed `deny_patterns`).
3. **Approve.** Explicit user command moves pending → active. Nothing executes without this gate (panel-critical: an autonomous loop must not write artifacts that auto-load).
4. **Track.** Frontmatter telemetry: `activation_count`, `last_activated` (maintained by a lightweight post-run hook).
5. **Retire.** `prune` flags dormant artifacts (no activation in the last 30 runs — configurable) → archive unless pinned. Prevents context-bloat accumulation (panel: "no demotion path is slow poison").
6. **Graduate** (maintainer) / **PR upstream** (consumer) — below.

### Provenance frontmatter (required on every extension artifact)

```yaml
origin: learned | manual
learned_from: <run-id / lesson-id>        # traceability to the retrospective that spawned it
depends_on_core: ">=0.34 <0.36"           # validated against extension-api.json, not bare semver
activation_count: 12
last_activated: 2026-06-12
pinned: false                              # exempt from prune
```

### Naming + collisions

Extension artifacts are namespaced `ext-<user-slug>-<name>` (enforced at the pending→active gate). `collision_policy: core_wins` by default. `build-loop doctor` prints trigger overlaps between active extensions and core skills, plus the instance version (core release tag + extensions HEAD).

## The extension contract: `extension-api.json`

Core ships a versioned `extension-api.json` declaring the **stable surface** extensions may depend on: hook names + event shapes, context keys, stable script entrypoints (e.g. `model_overrides.py` CLI), skill-frontmatter schema. `depends_on_core` is validated against this file — not bare semver (panel: "semver against an undeclared surface is hollow").

**Update flow:** after a core update, a post-update check validates every `active/` artifact against the new `extension-api.json` + runs the deterministic checks. Breakages **never block the update**; they are filed into the Learn queue as repair work with failure evidence, and the broken artifact is demoted to `pending/` (it stops loading until repaired and re-approved). Conflicts become iterate-loop input, not merge hell.

## Identity = promotion routing (not privilege)

`~/.build-loop/identity.json`:

```json
{ "role": "maintainer", "core_repo": "~/dev/git-folder/build-loop" }
```

- **Absent → consumer.** Learn promotions land in `extensions/pending/`.
- **Maintainer:** promotions may target the **source repo** directly — the maintainer's "overlay" is core itself. The source checkout already self-modifies under `self_mod_verify.py`; this design adds no new power, it routes promotions. Recursive self-improvement of any aspect (including core code) is preserved exactly where it exists today: the source checkout.
- **Forks:** a serious fork sets `core_repo` to their fork and inherits the identical workflow. Identity is not a privilege boundary — consumers without the source simply have nowhere else to write; the cache is regenerated every update.
- **Validation:** `core_repo` is checked (exists, is a build-loop repo) when identity is written and at every promotion (panel: silent misrouting footgun). Setup warns when a build-loop source checkout exists on disk but no identity file does.

### Graduation (maintainer: "my extensions become core")

When a maintainer extension proves out (telemetry + promotion-reviewer sign-off), **graduate**: move artifact → core repo, commit, append its ID to `extensions/graduated.json`. The loader skips graduated IDs, so the next core release (which now contains it) doesn't double-load. A single auditable JSON registry — not per-file tombstones (panel: tombstones die in `git clean` and orphan on core renames; the registry maps old-ID → core-ID on rename).

### PR upstream (consumer rung 2)

At the same decision point, consumers get "propose as PR upstream." `promotion-reviewer` (Fable) applies a **generalizability rubric** first: strip project-specific nouns/paths, verify triggers on a synthetic out-of-domain example, privacy deny-scan (transcript-derived content must not leak). Most learned skills won't clear it — that's correct; overfit personal skills stay personal.

**Deferred (YAGNI):** a community-registry rung between extensions and core-PR. Noted for future; not designed now.

## First-run: `build-loop setup`

One idempotent command (panel: the first-run story must exist): creates the extensions dir scaffold + git init, generates the extensions plugin manifest, registers the plugin, runs `install_memory.py --guided` (memory store), offers identity setup, prints what it did. Re-runnable; every step is a no-op if already done. This is also the "one-day terminal install with guidance" surface — `setup` ends by printing the quickstart (where things live, how to approve a pending skill, how `doctor`/`prune` work).

## Version control & reproducibility

- **Their version** = core release tag + extensions HEAD (both printed by `doctor`).
- Extensions repo: git from day one; optional `--link-repo` private remote (same pattern as memory).
- **Rollback:** core rolls back by reinstalling a prior release; extensions roll back by git; memory is append-only. The three zones are independently recoverable; the post-update check re-runs after any rollback to re-validate.

## Failure modes addressed (from the reliability review)

| Failure | Mitigation |
|---|---|
| Bad learned artifact executes | `pending/` gate: nothing loads without explicit approval |
| Update breaks an extension | post-update check → demote to pending + Learn-queue repair work; update never blocked |
| Mid-session torn state | session-epoch: load at session start only |
| Tombstone drift | `graduated.json` registry (rename-mapped, auditable) |
| Silent promotion misrouting | identity validation + setup warning |
| Slow accumulation/bloat | activation telemetry + `prune` |
| Trigger shadowing | namespace prefix + `core_wins` + `doctor` overlap report |

## Implementation phases (sketch — full plan via writing-plans)

1. **P1 — Extensions zone:** dir scaffold, generated plugin manifest, `pending/active` lifecycle + approve command, namespace enforcement, deny-scan reuse.
2. **P2 — Identity + routing:** `identity.json`, promotion routing in Phase 6 Learn, graduation + `graduated.json`, maintainer warning in setup.
3. **P3 — Contract + update safety:** `extension-api.json` in core, post-update validation + demote-to-pending + Learn-queue repair filing.
4. **P4 — Lifecycle hygiene:** activation telemetry hook, `prune`, `doctor`, generalizability rubric for the PR rung.
5. **P5 — `build-loop setup`** (consolidates P1–P4 surfaces into the one-command first-run) + docs/quickstart.

Each phase lands independently; P1 alone already delivers "learning survives updates."
