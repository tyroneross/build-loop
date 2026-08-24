<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
# Handoff — build-loop session close, 2026-08-21

Written by the outgoing session after the last commit (`7aa15f9`) landed and before
anything was pushed. Every number below was re-measured; where the incoming brief and
the measurement disagreed, the measurement is recorded and the disagreement called out.

## 1. Orientation

`build-loop` is a **portable multi-phase operating loop for AI coding agents** — assess,
plan, execute, review, iterate, learn — shipped as a plugin that runs identically under
Claude Code, Codex, and any AGENTS.md-aware host. **Public, Apache-2.0.** Python 3
(stdlib-heavy) plus Markdown skills, agents, and commands. No web app, no DB server,
no UI.

- **`scripts/` holds the product AND its tests.** ~4,700 tests sit beside the code they
  guard as `scripts/test_*.py`. `tests/` is a second, separate suite (mostly
  `src/build_loop/architecture/`). Both must run; they do not overlap.
- **`.build-loop/` is gitignored.** It is this repo's own working state. A backlog
  disposition, queue verdict, or decision record written there **dies at clone**.
  Durable records go to `docs/` (tracked) or the private `build-loop-memory` repo.

## 2. Verified state

### Git — 31 unpushed commits on `main`, 0 behind

```
git rev-list --count origin/main..main   ->  31
git rev-list --count main..origin/main   ->  0
```

**The brief said ~21, and the gap matters.** 21 are this session's (`fc08cc4..main`);
**10 were already unpushed when the session started** (`origin/main..fc08cc4`). A push
sends all 31, so whoever pushes owns 10 commits they did not write. Read them first:
`git log --oneline origin/main..fc08cc4`.

Working tree is clean except two untracked paths: `scripts/cli_dispatch_consent.py`
(§6, deliberate) and `tmp/` (pre-existing scratch).

### Tests — NEITHER interpreter runs the whole suite. Repair this first.

The repo has two Python environments whose installed packages are **exactly
complementary**, so each hides a different part of the suite and each returns a
confident, wrong verdict.

| module | bare `python3` | `./.venv/bin/python3` | effect when absent |
|---|---|---|---|
| `pathspec` (**required** dep) | **no** | yes | all 7 `tests/architecture/` modules die at *collection*; pytest aborts the run |
| `psycopg` (`.[db]` extra) | yes | **no** | 11 `scripts/test_db.py` reds, every one an `ImportError` |
| `mlx_embeddings` | yes | **no** | the red-by-design embed test **skips** — the detector goes silent |

Measured at the same sha, minutes apart:

| Suite | bare `python3` | `./.venv/bin/python3` |
|---|---|---|
| `scripts/` | 4 failed, 4680 passed, 83 skipped | 14 failed, 4648 passed, 88 skipped |
| `tests/` | **aborts at collection** | **3 failed, 840 passed, 5 skipped** |

Bare `python3` on `tests/` runs 167 fewer tests and does not say so. The venv turns 11
missing-driver `ImportError`s into what reads as a broken transaction contract, while
silencing the one red the user must see.

**Repair the environment before running anything, then re-baseline:**

```
./.venv/bin/python3 -m pip install -e '.[test,db]'   # psycopg — confirm extra name in pyproject.toml
./.venv/bin/python3 -m pip install mlx_embeddings    # Apple-silicon only
```

The 11 `test_db` reds are environmental — `ImportError: psycopg is not installed`,
raised at the call site by design so mock-based tests still collect. Do not file them
as defects.

### 2.1 Reds — the union is 7, not 1

The brief said exactly one red by design
(`test_embed_backend.py::test_cross_backend_cosine_above_threshold`). True of that test,
false as a description of the suite. Until §2 is repaired no single command produces
this union, so it is assembled from both runs.

| Red | Class | Interpretation |
|---|---|---|
| `scripts/test_embed_backend.py::test_cross_backend_cosine_above_threshold` | **Red by design — do not fix, do not skip** | The only detector for `BUIL-MEMORY-…9vzf60y895rj3vt9` (§3). Green here without the user's decision hides a live correctness bug. **Visible only under bare `python3`**; the venv lacks `mlx_embeddings` and skips it. A skipped detector is a silent one. |
| `scripts/test_codex_plugin_artifact.py::test_checked_in_artifact_is_current` | **Real, and a trap** | The checked-in Codex artifact is missing 9 files added this session. Regenerating it **sweeps in `scripts/cli_dispatch_consent.py`**, the deliberately-uncommitted WIP. Settle §6 first, then regenerate. |
| `scripts/test_prepush_test_gate.py::test_all_default_gates_run_and_pass_on_green_repo`<br>`…::test_shallow_clone_skips_artifact_freshness` | **Cascade** | Both run the real pre-push gate over the real repo and fail with `named-pytest-gates — 1 newly-red test(s) not in the known-red baseline` — they are reporting the codex-artifact red above. Fix that, then re-check these before treating them as separate work. |
| `tests/test_capability_registry.py::test_no_unknown_category`<br>`tests/test_orchestrator_skeleton.py::test_orchestrator_under_line_budget`<br>`tests/test_phase_6_gating_docs.py::test_helper_script_imports_and_exposes_scan` | **Real, reproducible in isolation** | See §2.2. |

### 2.2 The three `tests/` reds

All three postdate 2026-08-19; two are same-day pairs where a fix landed and its guard
did not follow. Each has a known one-line fix.

**`test_no_unknown_category`** — the one uncategorized capability is
`cli_dispatch_consent`, §6's untracked file (the registry scans the filesystem). The gap
is real regardless: `85e2ee8` fixed this class for two other surfaces a day earlier and
`CATEGORY_KEYWORDS` in `scripts/build_capability_registry.py` was not extended for a
third. **Fix once §6 is settled:** add `"consent",` to the `("validation", (...))` tuple
beside `autonomy_gate`. A consent gate decides whether a dispatch may proceed, so
`validation` is the right lane.

**`test_orchestrator_under_line_budget`** — `agents/build-orchestrator.md` is **201
lines against a 200 budget** in committed state, from `da65d3b` (2026-08-19, net +1),
which added a ~90-word `**External-source gate**` bullet under Phase 3 — precisely the
detail the budget exists to push into `references/`. The test has not moved since
2026-06-09 and caught the breach on the first line over. **Fix:** move that bullet's
body to `references/phase-3-execute.md`, leave a pointer. **Do not raise `LINE_BUDGET`**
— that deletes the invariant rather than satisfying it.

**`test_helper_script_imports_and_exposes_scan`** — stale. `scan()` now returns
`{"scannedFiles", "dispositionedSkipped", "patterns"}`; the test asserts dict equality
against the old two-key envelope. `60af201` (2026-08-20) added the key deliberately and
its commit body states the contract is a **superset**; the sole consumer
(`agents/recurring-pattern-detector.md`) splices `patterns[]` and is unaffected.
**Fix the test**, asserting the two contract keys rather than equality.

> `plugin-artifacts/codex/` mirrors both the 201-line orchestrator and the stale
> assertion. `testpaths = ["tests"]` means the mirror is never collected and will never
> go red, but any artifact-sync guard over that tree needs the same two edits.

## 3. Three decisions owed by the user

No agent can resolve these. Each is filed in `.build-loop/backlog/items/`, **which is
gitignored — those three files are the only copy and do not survive a clone.** If you
act on one, write the outcome to `docs/`.

### Decide which embedding model the MLX backend uses — `BUIL-MEMORY-m0hcg9vzf60y895rj3vt9`

Stored vectors are migrating to `bge-m3` while the **default** query path still embeds
in `mxbai` space. Both emit 1024 dims, so every dimension check passes and nothing
detects it. Measured cross-backend cosine on identical text: **-0.0487** (orthogonal).
Similarity between a default-path query and a migrated row is noise. The red test is the
only detector.

Options, user's call: **(a)** point MLX at an MLX-packaged `bge-m3` and re-embed —
blocked, no MLX `bge-m3` in the local HF cache; **(b)** drop the MLX default, make
`bge-m3`-via-Ollama the sole path; **(c)** make `embed_backend` refuse to fall back
across embedding spaces and record `embedding_model_version` with every vector.

### Approve un-silencing files the security gate skips — `BUIL-SECURITY-m0hsaw0f7zpg13ggd4gst`

`scripts/security_common.py` tests inert markers with `marker in str(path).lower()`, a
substring match against the whole path. Measured: `is_inert_file` returns `True` for
`/repo/my_archive_service/api/route.ts`. Any live route under a directory whose name
merely *contains* `_archive`, `node_modules`, `phase1-backup`, or `integration_example`
is skipped before any API check runs, and **the gate reports clean**. That is a false
clearance.

The fix is known — match markers against path *parts*, as `is_api_path` already does.
It was filed rather than done because **un-silencing those files surfaces findings that
were previously invisible**, which wants its own review rather than riding along in a
test commit. Current behaviour is pinned by
`test_security_common.py::test_markers_match_as_substrings_of_the_whole_path`, so a
later narrowing is deliberate.

### Decide whether cross-vendor model dispatch gets wired — `BUIL-MODEL-RESOLUTION-m0jahjgzemfd0byhvz2qt`

Measured: `detect_host_providers()` reads `CLAUDECODE`, returns `{'anthropic'}`, and
`resolve()` folds every non-anthropic model into `unavailable` **before** the tier walk.
`modelOverrides.frontier = gpt-5.6-sol` records `skipped: unavailable`. Symmetric — a
Codex session filters out opus/sonnet/haiku/fable the same way. Rally's `--model` only
announces presence; no packet field assigns a model to a peer. So a tier lane always
collapses to the host's own vendor, and CLAUDE.md's "Sonnet and Terra are execution-lane
peers" is not expressible.

The capability exists unwired: `scripts/exec_state.py:60` already accepts `--model`
("skips tier resolution"), and Claude already has a `codex exec` path, but no call site
passes `--model` and the trigger is codex-rescue's stuck-detector rather than tier.
Wiring it changes which model executes work, so it is a routing-policy call.
**Not verified:** no Codex session was observed deploying Sonnet; this is read from code
on the Claude side only. A rally transcript showing otherwise would contradict it and
should be examined first. Full writeup: `docs/2026-08-21-cross-host-model-dispatch.md`
(tracked).

## 4. Open work, and the warning governing all of it

### Re-validate every backlog item against current code before working it

**3 of 3 items opened during this session were mis-stated or already closed.**
`docs/retrospectives/2026-08-21-backlog-revalidation.md` is the tracked record; read it
before touching the backlog. (The brief said "4 of 4"; the tracked retrospective says
3 of 3. Trust the retrospective.)

The failure modes are the point. One was **already fixed** by a later commit that never
wrote back to the item. One described **a mechanism that does not exist** — it claimed
the availability walk pre-empts the override read; measured, overrides outrank the walk,
and "fixing" it would have reordered working code. One **understated its own defect** —
filed as "interleaved rows", actually *lost* rows, from two unlocked
read-modify-writes where the second replaced the first.

Staleness is structural: of the 55 open items, **85% are more than a week old** (31 at
8–30 days, 16 at 31–60). These are prose and **cannot be re-validated mechanically**.
The check is manual and is cheaper than the work it prevents.

### Inventory

`.build-loop/backlog/items/` holds 61 items: **55 open**, 3 resolved, 2 deferred,
1 blocked; 5 are `type: decision`. Largest open areas: coordination, ci, audit,
agent-rally-point (4 each); security, release, docs, architecture (3 each).

### Mechanically-checkable findings — zero currently closeable

```
./.venv/bin/python3 scripts/revalidate_self_review_findings.py --workdir . --json
{"scanned": 369, "resolved": 0, "open": 15, "source_gone": 0,
 "not_checkable": 303, "already_dispositioned": 51, "applied": 0, "items": []}
```

**15 genuinely-open `self_missing_test` findings, 0 stale.** The sweep already ran this
session, which is why `resolved` is 0 and `already_dispositioned` is 51. Expect no free
wins. The 303 `not_checkable` are judgment kinds
(`self_complexity_high_complexity`, `user_correction_cluster`) **deliberately** never
auto-closed.

### What I would do next, in order

1. **Repair the test environment and re-baseline** (§2). Every judgement below depends
   on a suite that reports the truth, and none currently does. Cheap, and a prerequisite.
2. **Read the 10 pre-session commits, then push all 31.** Nothing gates them, and they
   are the only thing at risk from a lost checkout.
3. **Answer §6.** Two reds are downstream of it: the Codex artifact cannot be
   regenerated until it is settled, and the capability registry is red *because* that
   file is untracked.
4. **Take the 3 `tests/` reds** (§2.2) — bounded, each with a known one-line fix, and
   currently noise that hides the next real break.
5. **Write the 15 missing tests**, but only after re-validating that each finding's
   source file still exists in the shape the finding describes.

Do **not** open the 55-item backlog as the default next move. Its expected yield per
hour is the worst on this list, for the reason above.

## 5. Hazards that cost real time this session

- **`.build-loop/` is gitignored, so dispositions written there die at clone.** The
  highest-cost trap in the repo. Backlog items, queue verdicts, and decision records all
  live there. Durable output goes to `docs/` or `build-loop-memory`.
- **`backlog.py --context` breaks on shell metacharacters.** It is a plain argparse
  string with no file input, so a prose block containing `|`, `(`, `!`, or `>` is
  mangled or rejected by the shell before Python sees it. Write the body to a file and
  pass `--context "$(cat body.txt)"`, or create the item and edit its `## Context`
  section directly.
- **A repo config REPLACES a default policy list rather than extending it.**
  `deployment_policy.load_policy` merges onto `DEFAULT_POLICY`, but
  `load_protected_branches` returns the config list outright. Setting
  `deploymentPolicy.protectedBranches: ["release"]` silently drops `main` and `master`
  from protection. Read the loader before writing any `.build-loop/config.json` list.
- **Tests sharing a global lock or a live model report concurrency rather than
  correctness.** A red — or a *skip* — from such a test describes the environment until
  proven otherwise. Live example: `test_embed_backend.py:126` skips with `embed daemon
  active — bypasses in-process fallback path`, so whether that file tests anything
  depends on whether a daemon happens to be running. Re-run in isolation before
  diagnosing. The `test_prepush_test_gate` pair (§2.1) is the same shape.
- **Wrong interpreter, wrong verdict, in both directions** (§2). Neither environment is
  complete and each one's blind spot is invisible from inside it. Check what imports
  before trusting a count.

## 6. Unfinished thread — the CLI-dispatch consent gate

`scripts/cli_dispatch_consent.py` **exists in the working tree, is untracked, and was
deliberately not committed.** 171 lines. It gates shelling out to another vendor's CLI
(`claude -p`, `codex exec`, `agent -p`, `ollama run`) behind a per-user, per-product
consent record with four modes (`once`/`ask`/`auto`/`denied`).

**Treat it as unreviewed WIP.** Two external models reviewed the design; both
independently concluded it is a sound UX policy and **not an enforcement boundary**.
What they found wrong:

- **Nobody has to call it** — the likeliest bypass, per both. It is a library the caller
  is *supposed* to invoke before `subprocess.run`; the agent it constrains is the caller
  and will write its own subprocess line. Both fixes agree: enforce at the execution
  chokepoint (a `PreToolUse` Bash hook matching `claude -p|codex exec|agent -p|ollama
  run`, or one mandatory dispatcher) rather than at the call site. Claude Code supports
  this; the Codex-side equivalent is **unverified**.
- **The gated agent can write the store.** Plain JSON in `$HOME`, and every agent this
  gate restricts runs as the user with file write. `"decided_by": "user"` is
  self-asserted; `flock`/`fsync` protect against corruption, not forgery. The
  env-overridable store path is a second forgery route (`CONSENT_PATH=/tmp/x.json`
  containing `auto`).
- **Persisting `once` in the `mode` slot is a trap.** Any `if product in
  store["consent"]` turns it into a grant, and `once` becomes indistinguishable from
  `ask` next check. Also undefined *once per what* — the motivating Codex session
  dispatched 11 times. Both: persist `ask`, issue the one-shot in memory bound to a
  run/request id.
- **Per-product is the wrong key.** Should be `product × target vendor` —
  `build-loop→ollama` is local and free, `build-loop→codex` ships code off the machine.
  Model and effort belong as *constraints on* the grant; tiers are durable, model names
  are not.
- **Per-user scope is right for spend, wrong for data.** A global `auto` also authorizes
  the NDA repo the user would never want sent to another vendor. Both: add a repo-level
  override that may **only narrow, never grant** — a repo file that could say `auto` is
  self-granting consent in an attacker-controlled clone.
- One reviewer also flagged **unbounded recursion** (one `auto` authorizes `claude -p` →
  build-loop → `codex exec` → `claude -p`; pass a depth env var, refuse above 2),
  **headless runs** (`needs_prompt=True` at 3am has nobody to ask — define fail-closed
  with a pre-grant path), and **exit 1 colliding with Python's uncaught-exception
  code**, so a crash in `check()` reads as "needs asking".

**Decision owed before this is committed:** does the gate defend against *accidental
omission*, or against *an agent actively trying to dispatch*? The first is roughly what
is written. The second needs a broker outside the agent's authority and is a different
piece of software. Committing as-is ships something that looks like enforcement and is
not — and by this repo's own standing rule, a bypassable gate is worse than no gate.

## 7. Session-scoped state that will NOT survive

`loop_state.json` and `loop_check.py` live in
`/private/tmp/claude-501/…/8b255ccd-…/scratchpad/`. **That directory is session-scoped
and is deleted with the session.** So are the two external-model review transcripts and
the review brief §6 summarises — §6 is deliberately written to be the surviving record
of all three.

`loop_state.json` held a `baseline` sha plus this session's 21 commits so `loop_check.py`
could answer "has another session taken over, and is there work left?" without judgment.
**Do not restore it.** Both inputs are re-derivable at any moment:

```
git rev-list --count origin/main..main                            # takeover / unpushed depth
grep -l "^status: open" .build-loop/backlog/items/*.md | wc -l    # remaining work
```

If the next session wants that stop-check, **write it into `scripts/` with a test**,
taking the baseline as a CLI argument rather than from a temp file. A loop-control
script living in `/tmp` is a control that silently stops existing — the
`.build-loop/`-is-gitignored failure in a second form.

## 8. Provenance and authorization

Written by the outgoing session immediately after `7aa15f9`, from live measurement at
that sha. Every count in §2 was produced by the command quoted beside it.

**Three claims in the incoming brief were contradicted by measurement** and corrected
here: unpushed count 21 → **31**; "exactly one red" → **7 product reds** across both
interpreters; "4 of 4 backlog items mis-stated" → the tracked retrospective says
**3 of 3**. The §2.2 diagnoses came from a dedicated investigation pass.

**Not verified:** §2.1's union red-set was assembled from two runs under two
interpreters, never one clean run, because no interpreter can currently produce one.
Treat it as the best available reconstruction; re-baseline after the §2 repair before
quoting it as fact.

**You are authorized to start on §4's ordered list without asking** — ordinary
build-loop work in this repo. Three things are **not** authorized: pushing without first
reading the 10 pre-session commits, committing `scripts/cli_dispatch_consent.py`, and
making `test_cross_backend_cosine_above_threshold` green by any route other than the
user's answer in §3.
