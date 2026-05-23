---
name: attribution-standard
description: "Apply the canonical four-layer Apache-2.0 attribution model (NOTICE, per-file SPDX, REUSE.toml, canary markers) to a repo. Triggers on 'stamp attribution', 'add NOTICE', 'license headers', 'attribution layers', a newly public repo, or a repo with .git but no NOTICE / no REUSE.toml / no CONTRIBUTING.md. Build-loop Phase 1 Assess can advise running this when shipped source files lack SPDX headers."
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Attribution Standard

Four overlapping mechanisms make stripping attribution either illegal, mechanically tedious, or detectable. Each survives a different removal pattern. The research backing this skill is at `~/dev/research/apache-2.0-attribution-watermarking-2026-05-22.md`.

## The four layers

| Layer | Survives | Legally binding? | Effort |
|---|---|---|---|
| 1. NOTICE file | Wholesale repo lift | **Yes** (Apache 2.0 §4(d)) | one-off |
| 2. Per-file SPDX headers (`SPDX-FileCopyrightText` + `SPDX-License-Identifier`) | Per-file copy-paste | **Yes** (Apache 2.0 §4(c)) | scripted |
| 3. REUSE.toml | Files that cannot carry a comment (JSON, binaries) | Reinforces #2 | scripted |
| 4. Canary markers | Naive copy-paste; detectable via GitHub code search | No | low |

## Canonical strings

Apply identically wherever they appear. Memorise these — do not paraphrase.

| Field | Value |
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
  --name "Tyrone Ross, Jr" \
  --email "46267523+tyroneross@users.noreply.github.com" \
  --years 2025-2026 \
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

## When NOT to use this skill

- Repos that aren't Apache 2.0 (the SPDX line hardcodes it; for other SPDX IDs, modify the script or invoke per-language manually).
- Repos that don't ship source (pure design assets, datasets, etc.).
- Forks of someone else's project — Apache 2.0 §4(c) requires you to PRESERVE the upstream copyright, not replace it. The script's `--restamp` flag is dangerous here.
