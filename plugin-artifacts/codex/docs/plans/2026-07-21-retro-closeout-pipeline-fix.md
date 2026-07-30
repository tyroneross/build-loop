---
title: Fix the retrospective transcript locator and the unreachable wrote_memory closeout status
date: 2026-07-21
run_id: bl-retro-closeout-fix
repo: build-loop
self_recursive: true
synthesis_density: 4
risk_reason: persistence contract
modifies_api: true
stakes: medium
dispatch_tier: opus
---

# Goal

Two verified defects in build-loop's own retrospective/closeout pipeline make the
learning loop structurally silent. Fix both, and prove each fix with a test that
fails against the pre-fix code.

## Verified evidence (re-derived this run, not taken on faith)

**BUG 1 — the locator cannot find a transcript for a build driven from an orchestrator cwd.**

- `scripts/retrospective/locate.py::find_transcript_for_cwd` (L50-70) resolves
  `cwd → cwd_to_slug() → ~/.claude/projects/<slug>/*.jsonl`, newest-wins. No
  `session_id` param, no fallback. CONFIRMED by read.
- The PRODUCTION path is `find_transcript_for_run` (L191-247), called from
  `synthesize.py:273`. It adds temporal-membership + a codex source but derives
  its Claude candidate set from the SAME `cwd_to_slug(cwd)` root (L222-224).
  **The brief named `find_transcript_for_cwd`; the fix must land in BOTH, because
  the run path is `find_transcript_for_run`.**
- Measured on this machine: `~/.claude/projects` holds **72 slug dirs / 251
  depth-2 `.jsonl`**; only **31** slugs hold any transcript.
  `-Users-<user>-dev-git-folder-<repo>/` holds **0** `.jsonl` (only a
  `memory/` subdir); `-Users-<user>/` holds **150**.
- Tonight's session resolves exactly once by filename across all slugs:
  `~/.claude/projects/-Users-<user>/7c4e91a2-3b0d-4f68-9a15-2de8c0f47b31.jsonl`.

**New evidence the brief did not have — a stronger, plumbing-free signal exists.**
Claude Code stamps a per-record `cwd` field in compact JSON. Tonight's transcript
contains `"cwd":"/Users/<user>/dev/git-folder/<repo>"` **2813 times**; a full
attestation call over the 7.8 MB file costs **~5 ms** measured. `locate.py` ALREADY uses exactly
this attestation pattern for codex rollouts (`codex_transcript_cwd`, L106-120), which
are likewise not slug-scoped. So the Claude side can reuse the proven
attest-cwd + verify-time design instead of inventing a new one.

**BUG 2 — `wrote_memory` is structurally unreachable.**

- `closeout/status.py::_latest_retro_summary` (L110-125) requires a summary line
  starting with `durable:` / `- durable:`; `_classify` (L166) returns
  `wrote_memory` only on `retro_enforce >= 1 AND durable`.
- `retrospective/write.py::render_summary` (L95-122) emits exactly five lines —
  headline / takeaways+lessons / issues+enforce / prompts+clusters / `full file:`.
  **None starts with `durable:`.** CONFIRMED by read.
- Root cause of the miss: `closeout/test_status.py::_write_retro_summary` (L85-103)
  **fabricates** a `- durable: ...` fixture line the real writer never produces. The
  reader was tested against a hand-made fixture, never against the writer. The test
  suite masked the contract gap rather than catching it.
- Ordering constraint: `synthesize.run` calls `write_active` (L282) BEFORE
  `promote_durable` (L284), so the summary cannot know the durable path today.

# Deliverables

Every deliverable is owned by exactly one chunk (an earlier draft left 5 and 6 unowned).

| # | File | Chunk |
|---|---|---|
| 1 | `scripts/retrospective/locate.py` — session-id resolution + share-gated cwd-attested cross-slug fallback | 1 |
| 2 | `scripts/retrospective/synthesize.py` — plumb `session_id` (Chunk 1); reorder promote→write (Chunk 2) | 1 + 2 |
| 3 | `scripts/retrospective/__main__.py` — `--session-id` flag | 1 |
| 4 | `scripts/retrospective/write.py` — no-transcript banner (Chunk 1); `durable:` line + `stamp_durable_in_summary` (Chunk 2) | 1 + 2 |
| 5 | `scripts/promotion_queue.py` — stamp the durable line after a drained promotion | 2 |
| 6 | `agents/retrospective-synthesizer.md` — document `--session-id`; correct the stale Step-1 locator description | 3 |
| 7 | `scripts/retrospective/test_locate.py` | 1 |
| 8 | `scripts/retrospective/test_write.py` | 1 + 2 |
| 9 | `scripts/closeout/test_status.py` — end-to-end writer→reader contract test | 2 |
| 10 | `scripts/test_promotion_queue.py` — **added after scope audit (GAP-1)**: `test_drain_free_applies_retro_durable` drains against a repo with no summary tree, so an unguarded stamp would flip it red | 2 |
| 11 | `architecture/model.json`, `architecture/ARCHITECTURE.md`, `docs/build-loop-flow-mockup.html` — **regen-only** (GAP-2), produced by the pre-commit `artifact_guard.py`, never hand-edited | 3 |

# Depends-on (reads-from)

| Contract / data path read by the new code | Status |
|---|---|
| `~/.claude/projects/<slug>/<session-uuid>.jsonl` layout | **verified** — 72 slugs / 251 depth-2 jsonl measured this run |
| Per-record top-level `cwd` field, compact JSON `"cwd":"<abs>"` | **verified** — 2813 hits in tonight's transcript, all 2813 top-level, 0 embedded; total `"cwd":"` = 2894, so 97.2% / 2.8% split |
| Record `timestamp` field (read by `transcript_time_span`) | **verified** — existing, unchanged |
| `temporal_membership.is_member` / `run_window` / `absence_marker` signatures | **verified** — read at `scripts/temporal_membership.py:90,120,160` |
| `state.json.execution.current_session_id` / `started_by_session_id` | **verified present, UNRELIABLE by value** — currently holds the Rally label `bl-model-prompt-profile`, not a UUID. Treated as best-effort only |
| `state.json.runs[]` window fields (`date`/`started_at`) | **verified** — unchanged consumer |
| `.build-loop/retrospectives/<date>/<run-id>.summary.md` line grammar (`durable:` / `- durable:`) | **verified** — `scripts/closeout/status.py:118` is the sole parser |
| `sections.meta.transcript_present` / `transcript_absence_reason` | **verified** — `scripts/retrospective/sections.py:861-872` |
| `promotion_queue._apply_retro` payload (`sections`, `run_id`, `repo`, `workdir`) | **verified** — `scripts/promotion_queue.py:257-269` |

# Approach

## Parallelism decision

`parallel_skipped_reason: shared write-set — Chunk 1 and Chunk 2 both edit
scripts/retrospective/write.py and scripts/retrospective/synthesize.py, so no MECE
file partition exists. Executed sequentially in a single context (Mode B). Chunk 2
additionally DEPENDS on Chunk 1's promote-before-write reordering, so parallel
dispatch would race on both file ownership and logical order.`

`scope_auditor_status: passed_with_gaps_absorbed` — `modifies_api: true`. **Five**
public signatures gain optional keyword params (an earlier draft listed three and
omitted `write_active`, the sole caller of `render_summary`):
`find_transcript_for_run`, `find_transcript_for_cwd`, `synthesize.run`,
`render_summary`, `write_active`. All additions are optional-with-default, so every
existing caller stays source-compatible — scope-auditor confirmed `internal_only: true`
for all five. Its two gaps are absorbed (GAP-1 → deliverable 10; GAP-2 → deliverable
11) and its two implementer constraints (CON-1 `sessions_root()` indirection, CON-2
glob-by-date + never-raise) are written into the Chunk 1 and Chunk 2 bodies.

## Approach lenses (risk_reason: persistence contract)

**Clean-sheet.** A retrospective would take the transcript path as a required input
from the harness, and the summary would be structured JSON with markdown rendered from
it — no line-grammar contract between writer and reader to drift.

**Current-constraints.** Corrected after plan-critic: the SessionEnd hook path DOES
already receive and pass `transcript_path`
(`scripts/hooks/session_end_retro_sweep.py:189-196` → `--transcript`), so the
clean-sheet answer is already realized there and that path is NOT broken. The gap is
specifically the **agent-dispatch** path (`agents/retrospective-synthesizer.md:45`
passes only `--workdir` and `--run-id`) plus every manual/CLI invocation. Making
`--transcript` mandatory would break those callers and force every dispatch site to
solve transcript resolution itself. So: keep the markdown grammar, make the writer
honor it, and add resolution paths that work with the evidence actually available at
those sites (session id when a caller can supply one; cwd attestation when none can).

**Bridge / backcast.** This change makes the writer the source of the `durable:` line
and adds an end-to-end writer→reader test. That is the precondition for a later move
to a structured summary sidecar: once one test pins writer-and-reader agreement, the
grammar can be swapped behind it without a silent-divergence risk. Not done now — no
observed failure demands it (KISS).

## Chunk 1 — BUG 1: transcript resolution

Three ordered sources in `find_transcript_for_run`, highest evidence first.

**Source 0 (new) — explicit session id.** `find_transcript_by_session_id(session_id)`
globs `<projects>/*/<session_id>.jsonl` across all slugs (exact filename, 251-file
scan, cheap). Falls back to a prefix match on the longest hex-ish token in the id, so
a Rally tool id (`fable-7c4e91a2`) resolves to `7c4e91a2-….jsonl`. The prefix must be
**≥ 8 hex chars AND match exactly one file** — a short token like `bed` or `face`
could uniquely prefix an unrelated session, and uniqueness is not correctness.
Ambiguous or too-short → None, never a guess.

*Design decision — the temporal bypass applies ONLY to an EXPLICIT session id,
corrected after a plan-critic finding.* The first design let the session-id path skip
the time gate and ALSO auto-derived that id from
`state.json.execution.started_by_session_id`. That field is documented at
`scripts/rally_point/build_loop_id.py:27` as *immutable post-generation* — it survives
resumes and later runs in the same repo — so an auto-derived stale id with the time
gate removed would reopen the RCA-2026-07-11 defect class. Corrected rule:

- **Explicitly passed** (`--session-id` / `session_id=`) → caller-ASSERTED identity,
  same evidence class as the existing `synthesize.run(transcript=...)` override which
  already bypasses locate entirely. Time gate skipped; host gate still applies.
- **Auto-derived from `state.json`** → treated as a HINT, and still gated by
  temporal membership like any other candidate.

**Source 1 (unchanged) — cwd-slug candidates**, newest-first, temporal membership.

**Source 1b (new) — cwd-ATTESTED cross-slug fallback.** When source 1 yields nothing,
scan OTHER slugs for transcripts that (a) were modified at/after the run window opened
(cheap mtime prune), (b) attest the workdir as a DOMINANT top-level `cwd` (share gate
below), and (c) pass temporal membership. Modeled on the double gate
`find_codex_transcript_for_run` already applies (attest cwd + verify time), with a
deliberately STRONGER attestation test — see below.

*Why attestation, not "newest across all slugs":* an unattested cross-slug search
could attach a concurrent, unrelated session that merely overlaps in time — a fresh
instance of the "nearest-in-time-but-wrong" defect class `temporal_membership.py`
exists to prevent.

*Attestation is SHARE-BASED, not existential — corrected after a plan-critic finding
that measurement confirmed.* The first design confirmed attestation on the FIRST
matching record. Measured on tonight's transcript, that is unsafe: a single transcript
carries **two** distinct top-level `cwd` values —
`/Users/<user>/dev/git-folder/<repo>` **2813× (97.2%)** and `/Users/<user>` (the home dir)
**81× (2.8%)**. An existential gate would therefore attach this transcript to a
retrospective for `/Users/<user>` (the home dir), whose work it represents 2.8% of — the very
defect class this source is supposed to avoid, reintroduced in a new form.

So the gate is: `share = count("cwd":"<workdir>") / count("cwd":"")`, computed by
streaming byte counts (no JSON parse, no whole-file read), then up to 5 `json.loads`
calls on matching lines to confirm the value is genuinely top-level and not embedded
in a tool payload. Candidates rank by **share descending, then mtime descending**.

**Floor = 0.10, calibrated against the whole store — CORRECTED after the independent
audit.** The first draft set `share >= 0.25` from ONE transcript's 97.2/2.8 split and
claimed it sat "well below any legitimate multi-repo split". Measuring the 15 largest
transcripts falsified that: **25 of the 44 repos holding >150 genuine records fall
below 0.25**, including one repo at 0.240 (n=1515) and another at 0.161 (n=453) —
precisely the multi-repo orchestrator sessions this source exists to serve. A floor
tuned on one sample would have converted the original bug into a false NEGATIVE.

The floor is a NOISE threshold, not a selection rule (ranking selects). 0.10 keeps a
~3.6x margin over the measured 0.028 false-positive while admitting those legitimate
splits.

*Why the embedded-payload confirm is still needed:* measured 2813 needle hits and
2813 top-level confirmations, 0 embedded — but that is one file, so the confirm stays
rather than trusting the substring alone.

Bounded and test-isolable: resolve the root through `locate.sessions_root()` (NEVER a
re-derived `Path.home()/...`, because `test_locate_codex.py` isolates only by
monkeypatching that function), glob exactly `*/*.jsonl` (depth-2, never `rglob` —
`~/.claude/projects` also holds nested `subagents/agent-*.jsonl` trees that would break
the measured bound), skip the cwd's own slug, mtime-prune, cap 40 candidates.
Measured on the live store: 251 transcripts → **26** after mtime prune, 0.28 s to scan
all of them, **4** attesting <repo> with tonight's session ranked first.

**Plumbing.** `find_transcript_for_run(..., session_id=None)` →
`synthesize.run(..., session_id=None)` → `--session-id` CLI flag. When not supplied,
`synthesize` auto-derives from `state.json.execution.current_session_id` /
`started_by_session_id` as a time-gated HINT (per the corrected rule above). Verified:
this repo's state currently holds the Rally label `"bl-model-prompt-profile"`, which
matches nothing and falls through.

**Honest activation-path note.** In production the session-id path is currently
DORMANT: `scripts/hooks/session_end_retro_sweep.py:189-196` already resolves the real
transcript from the SessionEnd payload and passes `--transcript`, bypassing locate
entirely; the agent dispatch path (`agents/retrospective-synthesizer.md:45`) supplies
neither id nor path; and `state.json`'s session fields are unreliable by value. So the
only NEW path this change actually activates in production is **Source 1b**. Source 0
is the exact route for a caller that CAN supply an id (a human, or a future dispatch
that plumbs it) and is the reason `--session-id` exists — but this plan must not claim
it is exercised end-to-end. The falsifier tests Source 1b, which is the live path.

**Say-so-loudly.** `render_full_markdown` emits a `> NO TRANSCRIPT` banner directly
under the title when `meta.transcript_present` is false, naming the absence reason and
stating that transcript-derived sections are empty by construction. `render_summary`
prefixes the headline with `NO TRANSCRIPT`. Two small edits, no new mechanism.

## Chunk 2 — BUG 2: reachable, honest `wrote_memory`

`render_summary(sections, *, run_id, durable_path=None)` emits `  durable: <path>`
**only** when a real path is passed. Skipped/failed/queued promotion → no line → the
reader keeps returning None → `wrote_memory` stays unreachable. That preserves
`no_durable_lesson` as the honest answer.

*Budget decision (the ≤5 non-blank-line contract).* Adding a 6th line would break the
documented budget, so the two count lines merge into one:
`takeaways: N · lessons: M · issues: P · enforce-candidates: Q`. Result: **4 lines
without durable, 5 with** — the budget holds in both cases and the no-durable summary
gets shorter. Chosen over raising the cap (weakens a contract) and over appending the
path to the `full file:` line (the reader requires a line that STARTS with `durable:`).

*Ordering.* `synthesize.run` calls `promote_durable` BEFORE `write_active` and passes
the result through. `promote_durable` renders from `sections` independently of
`write_active`, so the swap is behavior-neutral for every other field.

*Queued-then-drained promotions.* `promotion_queue._apply_retro` (L257-269) performs a
genuine promotion later, when the store frees. Add
`write.stamp_durable_in_summary(workdir, run_id, durable_path)` — replace-or-insert a
single line in the EXISTING summary file rather than a full re-render. Without this
the queued path stays permanently unreportable, which is the same class of silent gap
as BUG 2 itself.

Two constraints from the scope audit, both load-bearing:

- **It must locate the summary by globbing `.build-loop/retrospectives/*/<run-id>.summary.md`,
  never by `_today_iso()`.** A queued promotion drains on a LATER day than the write;
  `scripts/stop_closeout.py:440-442` documents this exact hazard and already globs
  `*/` for it. A today-only lookup would silently no-op on precisely the cross-day
  case the queue exists to serve.
- **It must never raise, returning a status dict like every other `write.py` public
  function.** `promotion_queue.drain` (L330-342) converts any exception into a
  `failed` record, so a raise on a repo with no summary tree would flip a passing
  drain to a failure — see GAP-1.

*Ownership of the reorder.* The `promote_durable`-before-`write_active` reordering in
`synthesize.run` belongs to **Chunk 2** (it exists to feed `durable_path` into the
summary). Chunk 1 does not touch call order.

*Accepted residual risk.* On a BUSY store, `promote_durable` now enqueues before
`write_active` creates the summary. A concurrent `drain` firing inside that window
would stamp a summary that does not yet exist. The fail-soft no-op above makes that a
silent skip rather than an error, and the next closeout re-drains. Accepted: the
window is sub-second and the failure mode is "no durable line", i.e. the honest
pre-fix behavior, never a wrong line.

## Chunk 3 — documentation and generated artifacts

- `agents/retrospective-synthesizer.md` — document `--session-id`, correct the Step-1
  description that currently claims the CLI "locates the most-recently-modified
  `~/.claude/projects/<cwd-slug>/*.jsonl`" (that text describes the DEAD
  `find_transcript_for_cwd`, not the production `find_transcript_for_run`).
- `architecture/model.json`, `architecture/ARCHITECTURE.md`,
  `docs/build-loop-flow-mockup.html` — **regen-only outputs**, never hand-edited.
  `hooks/git/pre-commit` runs `artifact_guard.py --staged`, which watches `agents/`
  and `scripts/` and regenerates + `git add`s these on drift. `model.json` embeds
  `__main__.py`'s docstring verbatim, which gains `--session-id`. Let the hook do it;
  never `--no-verify`.

# Activation Map

What actually calls each new capability in production. Written explicitly because the
scope audit found the session-id path has no live producer today — shipping it without
saying so would be a dormant-feature claim.

- **Source 1b (share-gated cwd-attested cross-slug scan)** — trigger: `synthesize.run` -> `find_transcript_for_run` (`scripts/retrospective/synthesize.py:273`), reached by the agent dispatch (`agents/retrospective-synthesizer.md:45`), by `python3 -m retrospective`, and by `session_end_retro_sweep.py` when no `--transcript` resolves — verified-live: yes
  - This is the path that fixes tonight's failure; the falsifier probes it directly against the real `~/.claude/projects` store.
- **`durable:` summary line** — trigger: `synthesize.run` -> `write_active(durable_path=...)`, read by `closeout.status._latest_retro_summary` (`scripts/closeout/status.py:118`) on every `python3 -m closeout` — verified-live: yes
  - Fires on every retrospective and every closeout; test (g) asserts the end-to-end writer->reader path.
- **`stamp_durable_in_summary`** — trigger: `promotion_queue._apply_retro` (`scripts/promotion_queue.py:257`) during `closeout.status._drain_promotions` — verified-live: yes
  - Fires whenever a peer-held store frees and a queued retro drains; tests (l) and (m) cover the no-summary and cross-day cases.
- **No-transcript banner** — trigger: `render_full_markdown` on every retro write, unconditional when `meta.transcript_present` is false — verified-live: yes
  - Covered by test (j).
- **Source 0 (explicit `--session-id`)** — trigger: `__main__.py` CLI flag and `synthesize.run(session_id=...)` — verified-live: pending
  - DORMANT BY DESIGN, stated openly. No current producer supplies a bare session UUID: the hook path passes `--transcript` instead (bypassing locate entirely), the agent path passes neither, and `state.json`'s session fields hold Rally labels. It ships as the exact route for a caller that HAS an id, and is auto-derived as a time-gated hint when `state.json` happens to hold a real one. Not claimed as exercised end-to-end.

# Verification

Two honestly-distinct classes. Only class A proves a defect is fixed; class B guards
against weakening. The earlier draft labeled the whole table "each test must fail
pre-fix", which its own rows contradicted — corrected here.

**Class A — true mutation tests (exercise PRE-FIX PRODUCTION behavior and fail on it):**

| # | Test | Pre-fix result |
|---|---|---|
| b | transcript for a workdir whose slug dir is empty resolves via cwd attestation from another slug | **fails** — `find_transcript_for_run` returns `(None, marker)`; verified live for <repo> |
| g | end-to-end `write_active` → `closeout.run` yields `wrote_memory` on a genuine promotion | **fails** — measured live: returns `queued_pending_lesson`, `retro_durable_path=None` |
| j | no-transcript banner appears in `render_full_markdown` | **fails** — no banner exists |
| k | a transcript attesting the workdir in only 2.8% of records is REJECTED | **fails** — pre-fix there is no cross-slug path at all, and the naive existential design would accept it |

**Class B — new-symbol and no-weakening guards (do not fail pre-fix; stated as such):**

| # | Test | Purpose |
|---|---|---|
| a | resolution by explicit session id from a different cwd | new symbol; pre-fix raises `TypeError` |
| c | prefix match shorter than 8 hex chars, or non-unique, returns None | bounds the new fallback |
| d | cross-slug candidate that does not attest the cwd at all is rejected | bounds Source 1b |
| e | `render_summary` emits `durable:` when a path is passed | new param; pre-fix `TypeError` |
| f | `render_summary` emits NO `durable:` when promotion was skipped | **keeps `no_durable_lesson` honest** |
| h | end-to-end with promotion skipped yields `queued_pending_lesson`, never `wrote_memory` | no-weakening |
| i | summary stays ≤5 non-blank lines with the durable line present | budget contract |
| l | drain against a repo with NO summary file still drains cleanly | GAP-1 regression guard |
| m | `stamp_durable_in_summary` finds a summary written on a DIFFERENT day | cross-day queue case |

Test (g) is load-bearing: it replaces the fabricated fixture with REAL writer output,
so the writer/reader contract cannot silently diverge again. Test (k) is the one that
would have caught the design defect plan-critic found. The fabricated
`_write_retro_summary` fixture stays for reader-unit tests, but the new end-to-end
test asserts writer-and-reader actually agree.

**Baseline to beat (measured this run, pre-change):** `python3 -m pytest scripts/ -q`
→ **3330 passed, 15 failed, 30 skipped** in 321 s. All 15 failures are PRE-EXISTING
and unrelated (6 × `test_self_mod_verify` timeouts, 3 × `test_plugin_manifest`
versions, 2 × `test_prepush_test_gate`, 2 × `test_embed_backend`, 1 ×
`test_command_surface_policy`, 1 × `test_sync_agent_model_defaults`). Acceptance: no
NEW failure, and `scripts/retrospective/` + `scripts/closeout/` + `test_promotion_queue.py`
fully green (baseline there: **102 passed**).

Full-suite gate: `python3 -m pytest scripts/ -q` plus the mandatory
`scripts/self_mod_verify.py --scope auto --auto-revert` before any commit.

# Risks

- **Cross-slug scan cost and memory.** Bounded by the mtime prune (251 -> 26 on a real
  run window) + a 40-candidate cap. Per-file cost is measured, not asserted: ~5 ms on a
  7.8 MB transcript and **151 ms on the 299 MB worst case in the store**. The count
  streams in 1 MiB chunks, so peak RSS is O(chunk): measured **6.3 MB** on that 299 MB
  file, down from a 299 MB spike when it used `read_bytes()`. An earlier draft of this
  risk cited "0.8 ms", which measured `bytes.count` on already-resident bytes and
  excluded the file read — corrected after the independent audit.
- **False attachment via cross-slug search.** Mitigated by requiring literal cwd
  attestation AND temporal membership (the codex path's proven double gate).
- **Summary-shape change breaks a consumer.** Grepped: `closeout/status.py` is the
  only parser of `*.summary.md`. Its two accepted forms (`durable:` / `- durable:`)
  are both satisfied.
- **Reordering promote before write.** `promote_durable` reads only `sections`; no
  dependency on `write_active` output. Existing tests cover both independently.

# Non-goals

- Not changing `_classify`'s routing rule or the closeout status taxonomy.
- Not adding a transcript-presence gate to closeout status (a zero-evidence retro
  produces zero enforce-candidates, so it already cannot reach `wrote_memory`).
- No push. Local commits only.

# Alternatives considered

1. **Session-id path only (the brief's literal direction).** Rejected as insufficient
   alone: nothing in the pipeline reliably holds a bare session UUID today —
   `state.json.execution.started_by_session_id` currently holds the Rally label
   `bl-model-prompt-profile`. A session-id-only fix would leave tonight's exact
   failure unfixed whenever the caller can't supply an id. Kept as Source 0 because
   when an id IS available it is exact; paired with attestation for when it isn't.
2. **Cross-slug newest-wins, no attestation.** Rejected — reintroduces the
   nearest-in-time-but-wrong defect `temporal_membership.py` was written to kill.
3. **Raise the summary budget to 6 lines.** Rejected — weakens a documented contract
   to avoid one merge.
4. **Re-render the whole summary on drain.** Rejected — a targeted line replace is
   strictly smaller and cannot disturb anything else already in the file. (An earlier
   draft justified this by "clobbers the synthesizer agent's Step-2 enrichment";
   plan-critic correctly noted Step 2 scopes enrichment to the ACTIVE file's 11
   sections and never mentions the summary. The rejection stands on minimality, not on
   that claim.)
5. **Plumb `--transcript` from the dispatch site the way `session_end_retro_sweep.py`
   already does.** This is the least-invasive in-repo-proven option and was missing
   from the first draft. Rejected as a COMPLETE fix, adopted as a partial: the hook
   path already does it and stays unchanged, but the agent-dispatch path has no
   transcript to plumb — the dispatching orchestrator does not know its own
   transcript path, which is exactly why tonight's retro ran blind. Source 0 is the
   generalization of this option (accept an id when the caller has one); Source 1b
   covers the case where no caller can supply either.
6. **Existential cwd attestation (first matching record wins).** Rejected on
   measurement — 2.8% minority attestation would attach a 97%-other-repo transcript.
   Replaced by the share gate.

# Falsifier

If, after the change, `python3 -m retrospective --workdir /Users/<user>/dev/git-folder/<repo> --json`
still reports `transcript_present: false` for a run whose window covers tonight, the
fix has failed regardless of unit-test color. That end-to-end check is the acceptance
probe.
