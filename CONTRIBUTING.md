<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Contributing to build-loop

Thanks for your interest. A few load-bearing conventions before you open a PR.

## License & Attribution

build-loop is licensed under the **Apache License, Version 2.0**. By contributing, you agree your contribution is licensed under the same terms.

Downstream redistribution rules (from the license itself):

- Apache 2.0 **§4(c)** — you must retain, in the source form of any derivative work, all copyright, patent, trademark, and attribution notices from the source form of the work. Translation: don't strip the per-file `SPDX-FileCopyrightText` and `SPDX-License-Identifier` headers when you fork or vendor source files.
- Apache 2.0 **§4(d)** — if the work includes a `NOTICE` file, derivative works you distribute must include a readable copy of the attribution notices it contains. Translation: when you redistribute build-loop, the `NOTICE` file at the repo root must travel with it (in a `NOTICE` file, in your docs, or rendered by the product). The contents of `NOTICE` are informational — they cannot add license terms — but the obligation to preserve them is binding.

Per-file headers in this repo follow REUSE 3.3 (https://reuse.software/spec-3.3/). Files that cannot carry an inline comment (`.json`, binary assets) are annotated via `REUSE.toml` at the repo root. Validate locally with `uvx reuse lint`.

## AI co-author attribution

A significant portion of this codebase was written collaboratively with AI coding assistants — Anthropic's Claude (via Claude Code) and OpenAI's Codex (via Codex CLI). The convention this repo follows: **every commit produced with meaningful AI assistance ends with a Git `Co-Authored-By:` trailer naming the model**.

For Claude Code sessions, the trailer is:

```
Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

Substitute the actual model + tier you used (e.g., `Claude Sonnet 5`, `Claude Haiku 4.5`).

For Codex CLI sessions, the trailer is:

```
Co-Authored-By: OpenAI Codex <noreply@openai.com>
```

GitHub renders the avatar of any recognized email on the commit page, so the AI contribution is visible at the commit level. This is a community convention, not a legal requirement of Apache 2.0. If you're authoring without AI assistance, omit the trailer; don't pad commits with it.

## Signed commits

Signed commits (`git commit -S` for GPG, or SSH-signed via `git config gpg.format ssh`) are **recommended** and surfaced as `Verified` badges by GitHub. They strengthen the evidentiary chain in case of an authorship dispute. They are not enforced.

## Commit message style

Conventional Commits (https://www.conventionalcommits.org/) — `type(scope): subject`. Common types in this repo: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `perf`.
