<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Portability verification — 2026-08-18

What was tested, what the tests returned, and which decisions those returns drove.
Written because the raw test files are not what a reader needs; the evidence is.

## The question

build-loop is installed by other people. Does anything in it assume the
maintainer's machine — and if so, does that break a fresh install?

## Test 1 — fresh-install probe (the decisive one)

Ran `append_milestone.py` for real under a synthetic `$HOME`, then looked at
what appeared on disk.

| tree | milestone written to | phantom dirs |
|---|---|---|
| pre-fix (`HEAD`, pristine worktree) | `~/dev/git-folder/build-loop-memory/projects/…` | 5 created |
| post-fix | `~/.build-loop-memory/projects/…` | none |

**Decision driven:** this converted the defect from "docs point at a missing
path" to "the plugin CREATES the maintainer's directory tree on a user's disk
and splits their memory store." That reordered the whole work list — the
Python defaults became critical and the markdown sweep became secondary.

**Instrument validity:** the first version of this probe ran `--help`, which
exits before argparse reaches the write path. It reported "no phantom dirs" on
code that demonstrably creates them. Rebuilt to invoke the real path, then
confirmed it FAILS on pre-fix source before trusting a pass.

## Test 2 — mutation check on the new unit tests

`scripts/test_portable_paths.py` (14 tests) run against `git show HEAD:` copies
of the three pre-fix files:

```
10 failed, 4 passed
```

**Decision driven:** the 4 that pass in both directions are the
override-preservation guards — correct, since those paths were never broken.
A test that passes in both directions proves nothing about the fix; only the
10 that flip do. Kept both, labelled.

## Test 3 — gate precision, measured before arming

`check_portability.py --all --stats`, iteratively:

| stage | hits | what changed |
|---|---|---|
| raw | 54 | — |
| excluding test files | 23 | tests must be able to name the literal they guard |
| after fixing 2 runtime defects + 6 stale docstrings | 16 | — |
| after fixing 7 guarded-but-dead agent sites, 6 exemptions | **0** | — |

**Decision driven:** a gate is not armable at 54 hits — it would be ignored
within a week. Arming was deferred until the tree read zero, and each exemption
carries a written justification in `EXEMPT_PATHS`.

## Test 4 — both-direction gate tests

`scripts/test_check_portability.py` — 18 tests. Seven assert it FIRES (each
defect shape); eight assert it stays SILENT on legitimate lookalikes, including
the `README.md` wording that correctly documents the real resolution order.

**Decision driven:** a guard tested only on clean input certifies nothing. The
silent-cases are what make it safe to leave armed.

## Test 5 — end-to-end block

With the hook wired, appended a literal `Read("~/dev/git-folder/…")` to
`agents/alignment-checker.md` and attempted a commit:

```
BLOCKED: maintainer-machine path found in shipped content.
  agents/alignment-checker.md:139: [private-home-path]
```

**Decision driven:** proved the wiring, not just the script.

## Test 6 — self-audit of the gate

Checked whether the gate's own surface list was complete. It was not: `.agents/`
is tracked and shipped but absent from `SHIPPED_DIRS`, and it was concealing a
`$HOME/Desktop/git-folder/build-loop` fallback.

**Decision driven:** the blind spot was the finding. A `${VAR:-fallback}` shape
looks portable while its fallback is not.

## Test 7 — regression baseline

| when | result |
|---|---|
| before any work | 833 passed, 7 failed |
| after the sweep + gate | 836 passed, 7 failed |

Same 7 failures throughout — all pre-existing, none touched by this work
(capability-registry categories, an expired Groq catalog deadline, a
`results_by_kind` shape drift, two orchestrator doc strings, two git-tag
environment failures).

**Decision driven:** the +3 are new identity-resolution tests. Zero regressions
means the sweep is safe to keep; the 7 remain open and are unrelated.

## What is NOT verified

- **98 private-slug hits remain.** The local `.private-slugs` holds 3 of 5
  guarded slugs, so a sweep would clean what it can see while leaving two
  private names in place and turning CI green — false assurance. Blocked on
  the full list from the `PRIVATE_SLUGS` repo secret. These are cosmetic for
  portability: no agent follows a slug to a path.
- **`docs/_inbox/`** still ships internal validation notes containing
  maintainer paths. `docs/` is deliberately outside the gate's surface (read by
  humans, not followed by agents). Tracked as `bl-port-015`; `scripts/doc_boundary.py`
  already exists to grade it.
