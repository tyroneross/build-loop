<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Fast repository search

Use `scripts/repo_search.py` to find evidence before reading large parts of a
repository. It makes no LLM calls. A content query runs `rg` over live files,
then Python reads only the matching candidates. A metadata query uses a
rebuildable `.build-loop/search/index.json` in the target repository.

```bash
# Fast first pass; does not build an index.
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/repo_search.py" query \
  --workdir "$PWD" --query "release ledger" --kind content --limit 10 --json

# Narrow by evidence type. First metadata query builds the local index.
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/repo_search.py" query \
  --workdir "$PWD" --query "release ledger" --kind decision --limit 10 --json
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/repo_search.py" query \
  --workdir "$PWD" --query "release ledger" --kind structure --limit 10 --json

# Inspect or force refresh the derived index.
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/repo_search.py" status --workdir "$PWD" --json
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/repo_search.py" index --workdir "$PWD" --json
```

`--kind all` combines live content with change, run, decision, and structure
pointers. `--max-files` caps candidate files inspected after the fast pass
(default 40). `content.complete=false` and `content.reasons[]` identify a
truncated or failed search. Increase `--max-files` or search a narrower term
before concluding there are no matches. A missing `rg` invokes a bounded Python
fallback (2,000 files); its coverage is also explicit. The tool skips `.env*`,
key material, dependencies, generated worktrees, and files over 1 MiB in the
content refinement pass.

## Source ownership and freshness

| Result | Canonical source | Local index role |
|---|---|---|
| Content | Current repository files | No content copy; `rg` selects, Python reads candidates |
| Changes | Git commits, most recent 500 | Subject and commit pointer |
| Runs | `.build-loop/state.json.runs[]` | Goal and run pointer |
| Decisions | Canonical `build-loop-memory/projects/<project>/decisions/` | Title and path; `--kind decision` also searches live decision bodies |
| Structure | Existing `.build-loop/architecture/index.json` and `annotations.json` | Component purpose, path, and annotation pointer |
| Local notes | Selected `.build-loop/*.md` and architecture handoff | Heading and path |

The index is ignored through the repository's local Git exclude file, atomic,
and disposable. No tracked `.gitignore` change is required. The query command rebuilds it
when Git HEAD, indexed file paths, or source fingerprints change. It never
writes canonical decisions or reruns the architecture scanner. Architecture
hits say `freshness: snapshot`; verify the cited file in current source before
using a graph edge or purpose statement as a current fact. When the architecture
index is absent, file paths still provide coarse structure search. Use the
existing architecture scanner when dependency edges matter. Use
`memory_locator.py` for cross-project memory; this local index deliberately
keeps project decisions separate from global memory.

## Code annotations

No annotation or LLM pass is a prerequisite. File paths, Git, run state, and
the native architecture scanner work without comments. Add a concise
`BL:<kind> | <summary> | <keywords>` source comment only where mechanical
scanning cannot explain a non-obvious purpose, invariant, or integration
boundary. The existing architecture scanner indexes these comments in
`.build-loop/architecture/annotations.json`; `repo_search.py` reuses that
output. Do not annotate every function or generate speculative descriptions.
Keep annotations beside the code they explain and update them when behavior
changes. Search results are evidence pointers, not promoted memory facts.
