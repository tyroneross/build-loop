---
name: attribution-standard
description: "Apply the canonical four-layer Apache-2.0 attribution model (NOTICE, per-file SPDX, REUSE.toml, canary markers) to a repo. Triggers on 'stamp attribution', 'add NOTICE', 'license headers', 'attribution layers', a newly public repo, or a repo with .git but no NOTICE / no REUSE.toml / no CONTRIBUTING.md. Build-loop Phase 1 Assess can advise running this when shipped source files lack SPDX headers."
user-invocable: false
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Attribution Standard

Four overlapping mechanisms make stripping attribution either illegal, mechanically tedious, or detectable. Each survives a different removal pattern. The research backing this skill is "Apache-2.0 Attribution & Watermarking" (private research note, 2026-05-22 — substance summarized here).

## The four layers

| Layer | Survives | Legally binding? | Effort |
|---|---|---|---|
| 1. NOTICE file | Wholesale repo lift | **Yes** (Apache 2.0 §4(d)) | one-off |
| 2. Per-file SPDX headers (`SPDX-FileCopyrightText` + `SPDX-License-Identifier`) | Per-file copy-paste | **Yes** (Apache 2.0 §4(c)) | scripted |
| 3. REUSE.toml | Files that cannot carry a comment (JSON, binaries) | Reinforces #2 | scripted |
| 4. Canary markers | Naive copy-paste; detectable via GitHub code search | No | low |

## Canonical strings

**These are build-loop's own values** (this repo's actual copyright holder) — use them verbatim only when stamping attribution onto build-loop itself. **Applying this skill to any OTHER repo requires the target repo's own name/email/years**, not these: derive from `git config user.name` / `user.email` in that repo, an existing LICENSE/NOTICE holder if one is already present, or ask the user. The `attribution_stamp.py` script's `--name`/`--email`/`--years` flags default to the build-loop values below if omitted — **always pass all three explicitly for a non-build-loop repo**; never invoke the script bare against someone else's repo.

| Field | Value (build-loop's own) |
|---|---|
| Copyright holder | `Tyrone Ross, Jr` |
| SPDX email tail | `<46267523+tyroneross@users.noreply.github.com>` |
| Year range | `2025-2026` |
| Full SPDX header value | `2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>` |
| NOTICE AI mention | "Portions of this software were developed with the assistance of Anthropic's Claude (via Claude Code) and OpenAI's Codex (via Codex CLI); AI-pair-programming contributions are attributed via Co-Authored-By trailers in the git history." |
| Claude co-author trailer | `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` (substitute actual model + tier) |
| Codex co-author trailer | `Co-Authored-By: OpenAI Codex <noreply@openai.com>` |

## Language → comment-style table

The `attribution_stamp.py` script applies the right comment syntax automatically based on extension.

| Extension | Style | Header form |
|---|---|---|
| `.py`, `.sh`, `.bash`, `.zsh`, `.rb`, `.toml`, `.yml`, `.yaml` | `hash` | `# SPDX-FileCopyrightText: ...` (after shebang if present) |
| `.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, `.cjs`, `.css`, `.scss`, `.go`, `.rs`, `.swift`, `.java`, `.kt` | `slash` | `// SPDX-FileCopyrightText: ...` |
| `.md`, `.mdx`, `.html` | `html_comment` | `<!-- SPDX-FileCopyrightText: ... \| SPDX-License-Identifier: Apache-2.0 -->` (after YAML frontmatter if present) |
| `.json` and binary assets | n/a | covered by `REUSE.toml` |

## Target paths

Default shipped-source roots: `src scripts hooks skills agents commands references`. Override via `--paths`.

Default excluded path components: `node_modules dist build .git .venv venv archive tests/fixtures docs/test-fixtures __pycache__ .pytest_cache .mypy_cache .ruff_cache`. Extend via `--excludes`.

For Python-heavy repos with no `src/` directory (like agent-rally-point), pass the actual package path: `--paths agent_rally_point` or whatever directory holds the .py files.

## How to apply (the script)

```bash
python scripts/attribution_stamp.py \
  --repo <path-to-repo> \
  --name "<target repo's copyright holder — NOT build-loop's, unless stamping build-loop itself>" \
  --email "<target repo owner's email/SPDX tail>" \
  --years <target repo's applicable year range> \
  --canary-files <path1> <path2> \
  [--paths <override-paths...>] \
  [--restamp]
```

The script is **idempotent**:

- Re-running with the same args is a no-op when canonical strings already match.
- `--restamp` REPLACES existing SPDX header lines so a string change (e.g. adding `, Jr` or an email tail) can be rolled across the tree.
- NOTICE is always rewritten from the canonical template (the canonical strings are the source of truth, not what's on disk).
- LICENSE appendix and README "License & Attribution" section are added when missing; CONTRIBUTING.md is written unless already canonical.

## Canary files — pick two

Pick two central, stable files — one near the package entry point and one in the user-facing documentation. The canary marker is invisible to users but indexable by GitHub code search. Stable choices:

- A package's `__init__.py` or main module
- The orchestrator/coordinator skill or main markdown file
- A central agent definition

Avoid: test files, generated code, vendored libraries, files that frequently change shape.

## Build-loop Phase 1 advisory wiring

Build-loop's Phase 1 Assess fires an advisory (routes to the run report; **never** asks the user mid-run, per `feedback_advisory_checks_are_automated`) when a public repo (has a GitHub origin) is missing any of:

- `NOTICE`
- `REUSE.toml`
- `CONTRIBUTING.md`
- SPDX headers on at least 80% of shipped source files

The advisory line is exactly: `Repo is missing standard attribution layers — run \`python scripts/attribution_stamp.py --repo <path>\``. When the build scope is ≥ S and the advisory fires, Phase 2 Plan queues an automatic chunk to run the stamper. Hard-blocking is out of scope — this is advisory, not a gate.

## Verification

After stamping, verify:

```bash
# Canonical name present in shipped source
grep -rln 'Tyrone Ross, Jr' src scripts skills agents commands references | wc -l

# No bare 'Tyrone Ross' (without ', Jr') in stamped files
grep -rln 'SPDX-FileCopyrightText:.*Tyrone Ross$' src scripts skills agents commands references | wc -l   # expect 0

# No SPDX line without the email tail
grep -rL 'noreply.github.com' \
  $(grep -rl 'SPDX-FileCopyrightText: 2025-2026 Tyrone Ross' src scripts skills agents commands references) \
  | wc -l   # expect 0

# REUSE compliance
uvx reuse lint
```

NOTICE must mention both Claude (via Claude Code) and OpenAI Codex (via Codex CLI).

## Discovery layer (credit links) — `scripts/attribution_audit.py`

The four layers above make removing attribution illegal or detectable. The discovery layer makes the owner FINDABLE from the project: people and search/AI engines follow a credit to the owner's site. Research basis: `research/topics/rosslabs/rosslabs.attribution-standard.credit-links.md` (2026-09-23).

**Owner identity lives in a profile, never in code:** `<memory store root>/attribution-profile.json` (override `BUILDLOOP_ATTRIBUTION_PROFILE`) with `github_owners`, `brand_name`, `brand_url`, `copyright_holder`, optional `topic`, `years`, `readme_credit`. No profile → the audit does nothing.

| Item | Applies when | Disposition |
|---|---|---|
| `license` | public repo | decision — license choice is the owner's (code: Apache-2.0) |
| `notice` | public + Apache-2.0 | auto — stamper's NOTICE template + brand link |
| `readme-credit` | public repo | auto — footer line `Built by [Brand](url)` |
| `citation-cff` | public repo | auto — GitHub "Cite this repository" |
| `package-json` | public, non-`private` package | auto — fills missing `homepage`, `author`, `funding`; never overwrites |
| `plugin-json` | public Claude Code plugin | auto — fills missing `homepage`, `author.url` |
| `github-metadata` | public repo | decision — changes public GitHub settings (`gh repo edit --homepage --add-topic`) |
| `web-app-footer` | any web app, public or private (the site is public) | planned — UI change a build-loop run makes and reviews |
| `data-license` | public repo with tracked data files | decision — CC BY 4.0 for data, never for code |

Skipped entirely: forks, archived repos, third-party origins, linked worktrees, repos with no GitHub origin. Unknown visibility (no `gh`) is reported `unknown`, never guessed.

**Rules the items encode:** require credit, never a followed link (Google's link-spam policy names ToS-required links, keyword-rich widget links and links spread across many sites' footers); brand-name anchors only; README links on GitHub are `nofollow`, so repo credits buy discovery and clicks, not ranking.

**When it runs:**
- **Every push from a Claude session:** `hooks/post-push-closeout.sh` runs `attribution_audit.py file` in the background for the pushed repo. It files one backlog item per applicable gap (`area: attribution`, provenance `attribution:<item>`), skips items already tracked or dropped, and marks items `done` once the gap is gone. `auto`/`planned` items land in the `planned` bucket for the next run's pickup; `decision` items land in the `decision` bucket gated `product-decision`.
- **Build-loop runs:** Phase 1 reads open `attribution` items like any backlog work; the fix for `auto` items is `attribution_audit.py apply --repo <path>` (writes files, never commits).
- **Fleet sweep:** `attribution_audit.py sweep --root ~/dev/git-folder --file`.
- Kill switch: `BUILDLOOP_ATTRIBUTION_AUDIT=0`.

## When NOT to use this skill

- Repos that aren't Apache 2.0 (the SPDX line hardcodes it; for other SPDX IDs, modify the script or invoke per-language manually).
- Repos that don't ship source (pure design assets, datasets, etc.).
- Forks of someone else's project — Apache 2.0 §4(c) requires you to PRESERVE the upstream copyright, not replace it. The script's `--restamp` flag is dangerous here.
