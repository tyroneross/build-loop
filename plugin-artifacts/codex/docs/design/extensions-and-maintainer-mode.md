<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Extensions + Maintainer Mode — per-user build-loop that survives updates

**Status:** approved design, verification-revised (2026-06-12) · **Owner:** Tyrone
**Reviewed by:** 4-persona panel (ergonomics, architect, reliability, skill-systems) → folded in; then a 3-way verification pass — repo architecture-fit audit, Claude Code platform-capability check, Fable plan-critic (verdict: REVISE-narrow) → folded in.

## Problem

Build-loop's Learn phase mines patterns and drafts new skills/agents, but everything user-specific today is either project-local (`.build-loop/skills/experimental/`) or lost on plugin update (the cache is replaced wholesale). Users need:

1. A **retrospective → memory** loop whose outputs persist per-user (exists: Phase 6 Learn; `retrospective-synthesizer` writes the durable human-reference copy to `build-loop-memory/projects/<slug>/retrospectives/`, and machine-readable enforce-candidates to `.build-loop/proposals/enforce-from-retro/` which Phase 6 consumes).
2. A **personal layer** — learned skills/agents + their tuning — that survives any core update, is version-controlled, and is clearly separable from core ("what's original vs what's mine").
3. An **upstream-merge story**: download a new build-loop without losing or breaking the personal layer.
4. A **maintainer mode**: the build-loop author's own instance may recursively improve *any* aspect of build-loop, including core code — while consumers' learning is confined to their personal layer.

## Decision summary

**Core + userland overlay** (the vim/VS-Code pattern), with promotion routing by identity. Consumers learn into an overlay; the maintainer's overlay is the source repo itself. The contribution ladder: overlay → PR upstream → fork (escape hatch, unsupported-but-possible).

## Architecture — three zones

| Zone | Path | Owner | On core update |
|---|---|---|---|
| **Core** | plugin cache (`~/.claude/plugins/cache/...`) | upstream | replaced wholesale |
| **Extensions** | `~/.build-loop-extensions/` | user | untouched by construction |
| **Memory** | memory store root (`install_memory.py`) | user | untouched (existing) |

Memory = knowledge (lessons, decisions, retrospectives). Extensions = capabilities (learned skills/agents + tuning/config). Deliberately separate (clarity over consolidation — user decision).

> Path note: `~/.build-loop-extensions/` (hyphenated, matching `~/.build-loop-memory`). The bare `~/.build-loop/` namespace is **retired** (`scripts/_paths.py:304-321` documents the migration away; `infer_risk_surface.py` treats it as sensitive) — do not resurrect it.

### Extensions dir layout

```
~/.build-loop-extensions/
├── .git/                        # ONE repo covering plugin/ + pending/ (optional private remote)
├── plugin/                      # ← the registered plugin ROOT (= "active"); ONLY this loads
│   ├── .claude-plugin/plugin.json   # generated: name "build-loop-extensions", REQUIRED version field
│   ├── skills/<ext-slug-name>/SKILL.md
│   ├── agents/<ext-slug-name>.md
│   └── config/                  # tuning the artifacts need (model pins, hook fragments)
├── pending/                     # Learn drafts — OUTSIDE the plugin root, structurally cannot load
│   └── skills/<name>/SKILL.md
└── graduated.json               # exclusion registry: artifact IDs absorbed into core
```

**Why pending/ sits outside the plugin root (load-bearing):** Claude Code has **no manifest-level subdir exclusion** — everything under a plugin root auto-discovers and loads (✅ platform-verified). The pending→active gate is therefore enforced *structurally*: `pending/` is simply not inside the plugin. The approve step is `git mv pending/... plugin/...`.

### Loading model (platform-verified)

- **Claude host:** the `plugin/` dir registers once as a **skills-directory plugin** (`~/.claude/skills/` mechanism — loads in place, no cache copy) or via a local marketplace entry. Either is durable; skills-dir is simpler and update-proof by construction.
- **Codex host:** the same `plugin/` dir syncs via the existing `sync_plugin_cache.py` (✅ verified generic: `--source` any dir; cache path derives from manifest `name`+`version`, hence the **required `version` field**).
- **Namespacing:** plugins are natively namespaced (`build-loop-extensions:skill-name`) — loader-level collisions with core are impossible (✅ platform-verified, no shadowing). The residual risk is **trigger/description overlap** (the model choosing the wrong skill at activation time); `doctor` reports overlaps.
- **Hooks compose additively** across plugins (✅ platform-verified) — extension config may contribute hook fragments without conflicting with core's.
- **Session-epoch (observed host behavior, not a contract we control):** plugins load at session start; mid-session core updates or Learn writes don't affect the running session (`/reload-plugins` exists for explicit refresh; SKILL.md content edits are live). Relied upon, not guaranteed by build-loop.

## Artifact lifecycle (pending → active → graduated/archived)

1. **Draft.** Phase 6 Learn routes `self-improvement-architect` output to `~/.build-loop-extensions/pending/` (consumer default; project-local `experimental/` remains the per-project scratch tier below it).
2. **Surface.** Run-end + session-start nudge: "**N pending extension drafts await review** — `build-loop approve --list`." (Fable: without this, the one-day-install user never learns drafts exist and the loop is dead weight.)
3. **Checks (deterministic, pre-approval).** Schema/frontmatter lint · namespace enforcement (`ext-<user-slug>-<name>`) · trigger-overlap scan vs core + active · privacy deny-scan (the `deny_patterns` from `templates/memory/manifest.json`, **extracted into a shared `scripts/privacy.py`** — `validate_public_seed()` is not cleanly importable today).
4. **Approve.** Explicit user command moves pending → `plugin/`. Nothing executes without this gate. **The same gate applies to maintainer promotions** (see Identity — Fable: an autonomous loop writing into `core_repo` without an interactive approve is a new automated path to old power, not "no new power").
5. **Retire.** `doctor` lists active artifacts; manual removal (or `git rm`) retires them. *(Deferred post-P5: activation telemetry + auto-prune — see Deferred.)*
6. **Graduate** (maintainer) / **PR upstream** (consumer) — below.

### Provenance frontmatter (required on every extension artifact)

```yaml
origin: learned | manual
learned_from: <run-id / lesson-id>        # traceability to the retrospective that spawned it
depends_on_core: ">=0.34 <0.36"           # validated against extension-api.json, not bare semver
pinned: false                              # exempt from future prune
```

*(No mutable telemetry fields in frontmatter — a hook rewriting tracked files dirties the user's extensions repo every run and guarantees merge conflicts on multi-machine sync. Telemetry, when added, goes to a gitignored log — Fable.)*

## The extension contract: `extension-api.json`

Core ships a versioned `extension-api.json` declaring the **stable surface** extensions may depend on: hook names + event shapes, context keys, stable script entrypoints (e.g. `model_overrides.py` CLI), skill-frontmatter schema. `depends_on_core` is validated against this file — not bare semver (architect panel: "semver against an undeclared surface is hollow").

**Update flow:** after a core update, a post-update check validates every `plugin/` artifact against the new `extension-api.json` + re-runs the deterministic checks. Breakages **never block the update**; they are filed into the Learn queue as repair work with failure evidence, and the broken artifact is demoted to `pending/` (stops loading until repaired and re-approved). Conflicts become iterate-loop input, not merge hell.

## Identity = promotion routing (not privilege escalation — but gated)

`~/.build-loop-identity.json` (**per-machine; deliberately NOT inside the synced extensions repo** — roles are per-machine; do not "fix" it into the repo):

```json
{ "role": "maintainer", "core_repo": "~/dev/git-folder/build-loop" }
```

- **Absent → consumer.** Learn promotions land in `extensions/pending/`.
- **Maintainer:** promotions target the **source repo** — but land as a **proposal requiring the same interactive approve** as consumer drafts (e.g. a draft commit/branch or a `proposals/` entry in `core_repo`, never an autonomous direct write to core). The source checkout's existing `self_mod_verify.py` gate still applies on top. Rationale (Fable): `identity.json` is plain user-writable JSON read by an autonomous phase; a poisoned retrospective or edited identity file must not be able to redirect autonomous writes into an arbitrary repo. Routing is automated; **landing is human-gated** in both roles.
- **Forks:** a serious fork sets `core_repo` to their fork and inherits the identical workflow.
- **Validation:** `core_repo` is checked (exists, is a build-loop repo) when identity is written and at every promotion. Setup warns when a build-loop source checkout exists on disk but no identity file does (silent-misrouting footgun).

### Graduation (maintainer: "my extensions become core")

When a maintainer extension proves out (promotion-reviewer — `model: fable` — sign-off + the interactive approve), **graduate**: move artifact → core repo, commit, append its ID to `graduated.json`. The loader skips graduated IDs so the next core release (which now contains it) doesn't double-load. A single auditable registry — not per-file tombstones (panel: tombstones die in `git clean` and orphan on core renames; the registry maps old-ID → core-ID on rename).

### PR upstream (consumer rung 2)

At the same decision point, consumers get "propose as PR upstream." `promotion-reviewer` (Fable) applies a **generalizability rubric** first: strip project-specific nouns/paths, verify triggers on a synthetic out-of-domain example, privacy deny-scan (transcript-derived content must not leak). Most learned skills won't clear it — that's correct; overfit personal skills stay personal.

## First-run: `build-loop setup`

One idempotent command: creates the extensions scaffold + git init, generates `plugin/.claude-plugin/plugin.json` (with version), registers the skills-dir plugin, runs `install_memory.py --guided`, offers identity setup, prints the quickstart (where things live, how `approve`/`doctor` work, what the pending-drafts nudge means). Re-runnable; every step no-ops if done. This is the "one-day terminal install with guidance" surface.

## Version control & reproducibility

- **Their version** = core release tag + extensions HEAD (both printed by `doctor`).
- Extensions repo: git from day one; optional `--link-repo` private remote (same pattern as memory).
- **Rollback:** core by reinstalling a prior release; extensions by git; memory append-only. Independently recoverable; the post-update check re-runs after any rollback.

## Failure modes addressed

| Failure | Mitigation |
|---|---|
| Bad learned artifact executes | structural gate: `pending/` is outside the plugin root — cannot load |
| Drafts rot unseen | run-end/session-start pending-drafts nudge |
| Autonomous write to core via edited identity.json | maintainer promotions land as proposals behind the same interactive approve + `self_mod_verify` |
| Update breaks an extension | post-update check → demote to pending + Learn-queue repair work; update never blocked |
| Mid-session torn state | session-start loading (observed host behavior; `/reload-plugins` for explicit refresh) |
| Tombstone drift | `graduated.json` registry (rename-mapped, auditable) |
| Silent promotion misrouting | identity validation + setup warning |
| Trigger shadowing | plugin namespacing (loader) + `doctor` trigger-overlap report (activation) |
| Multi-machine conflicts | no mutable state in tracked files; identity per-machine outside the repo |

## Implementation phases (revised per Fable — P1 is the true MVP)

1. **P1 — Extensions zone + consumer routing + minimal registration:** dir scaffold (`plugin/` + `pending/` + git init), generated versioned manifest, skills-dir registration, **Learn-routing of `self-improvement-architect` drafts to `pending/`** (net-new logic in the promote path — note: `/build-loop:promote-experiment` referenced by phase-6-learn does not exist as a file yet; this is written fresh, not modified), approve command + deterministic checks, `scripts/privacy.py` extraction, pending-drafts nudge. *P1 alone = "learning survives updates" actually true.*
2. **P2 — Identity + maintainer mode:** `identity.json` (per-machine), maintainer proposal-routing with interactive approve, graduation + `graduated.json`, setup warning.
3. **P3 — Contract + update safety:** `extension-api.json` in core, post-update validation + demote-to-pending + Learn-queue repair filing.
4. **P4 — Hygiene surfaces:** `doctor` (version, trigger overlaps, active inventory), generalizability rubric for the PR rung.
5. **P5 — `build-loop setup`** consolidating P1–P4 into the one-command first-run + quickstart docs.

## Deferred (YAGNI — explicit)

- **Activation telemetry + auto-`prune`** (post-P5): real at ~100 artifacts, not the realistic single-digit count; frontmatter mutation poisons the git repo; "did a skill activate" is itself hard. `doctor` + manual removal covers it until volume proves otherwise.
- **Community registry rung** between extensions and core-PR.
