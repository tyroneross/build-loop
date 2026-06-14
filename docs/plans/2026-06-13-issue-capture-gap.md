<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
<!-- status: PROPOSED (not executed) | author: Opus | date: 2026-06-13 -->

# Plan — Close the issue-capture gap: deterministic, auditable bug/user-mention capture

## Problem (evidenced)

Audit 2026-06-13 (read-only, code-grounded) found build-loop captures corrections,
run outcomes, commit-bypasses, and lessons in append-only timestamped logs — but the
one thing the user asked about, **bugs / "this doesn't work" / things the user
mentions**, falls in a gap:

- **A1 — user bug *reports* have no automatic capture.** `scan_corrections` (the only
  automatic user-turn miner, a wired Stop hook) catches *corrections to the agent*
  (`revert` / `wrong` / `don't` / `stop`) and preferences/tradeoffs — its own design
  doc says "captures decisions only." None of `broken` / `doesn't work` / `isn't
  loading` / `crashes` / `missing` / `errors out` are in its pattern sets
  (`scripts/scan_corrections/detect.py`). A user saying "the login is broken" that the
  agent doesn't act on leaves **zero** structured trace — the transcript is the only
  record, and a transcript is not a queryable issue log.
- **A2 — the bug store is convention, not capture.** `.build-loop/issues/` is written
  only because Phase-3 guidance *says* "if it's too large/risky, log to
  `.build-loop/issues/`" (`references/phase-3-execute.md:33`). No script writes it, no
  template exists (backlog + ux-queue have templates; issues does not), no provenance
  is required. Whether a discovered bug is captured depends on the agent choosing to,
  and an un-logged bug leaves no trace.

Net: capture is best-effort where it should be guaranteed. This is the same
dormant/activation failure class the last run hardened — applied to inputs instead of
machinery.

## Goal

Every user-mentioned bug and every agent-discovered problem lands in ONE auditable
shape (`.build-loop/issues/<id>.md`, provenance-stamped, append-only by unique file),
captured **deterministically** off the already-wired Stop hook — no new trigger, no
reliance on agent discretion for the auditable trace to exist.

## Non-goals

- Reconciling "was this bug actually fixed this run?" automatically — left to the
  existing Phase-1 hypothesis re-validation (`feedback.md` 2026-05-30: "a backlog/plan
  item is a hypothesis"). Captured entries default `status: unverified`.
- A persistent cross-run `issues/INDEX.md` — `context_bootstrap` already surfaces
  counts + top items; a durable index is deferred to a backlog watch item, not built.
- NLP/LLM bug classification in the hook — the Stop hook stays a zero-dep regex miner
  (fail-open, budgeted). LLM judgment, if ever wanted, belongs in a later refinement
  pass, not the capture hook.
- Capturing bug reports as confirmed *open* bugs — they are `unverified` candidates
  until Phase 1 or a human confirms. Precision over recall on `status`, not on capture.

## Approach Lenses

- **Clean-sheet:** a new `issue_capture.py` miner + its own hook. Rejected — duplicates
  `scan_corrections`' transcript-mining, budget, fail-open, dedup, and frontmatter
  infrastructure; adds a second Stop-hook entry. KISS/DRY violation, new activation
  surface to verify.
- **Current-constraints (chosen):** extend `scan_corrections` with a fourth signal
  class and a write-routing branch (bug_report → `issues/`, everything else →
  `pending-lessons/` as today). Rides the existing wired Stop hook. One new static
  asset (`templates/issue.md`) shared by both the deterministic writer and the
  agent-discretion path so ALL issues — auto or manual — share one auditable shape.

## Depends-on (reads-from)

- `scripts/scan_corrections/detect.py` `Candidate` dataclass (kind/signal_type/quote/
  context) + `detect_candidates()` over user turns — verified
- `scripts/scan_corrections/__main__.py` `_write_candidate` / `_emit_frontmatter` /
  budget + dedup + fail-open contract — verified
- `scripts/context_bootstrap.py` `QUEUE_NAMES` includes `"issues"`; `queue_context()`
  surfaces counts + top items at Phase 1 — verified (line 58, 245)
- `.build-loop/issues/` is in the Phase-5 drain set + bootstrap surface — verified
- `templates/backlog-item.md` frontmatter shape (the provenance/segmentation model the
  issue template mirrors) — verified

## Activation Map

(dogfoods the `activation-map-required` rule shipped last run — both new-capture
entries ride ALREADY-WIRED triggers, so verified-live is cheap. Entries are
one-per-line per the rule's documented format; detail follows below each.)

- bug-report capture — trigger: existing `scan_corrections` Stop-hook entry in `hooks/hooks.json` (Stop array, fires every turn-end; no new wiring) — verified-live: pending
- issues/ surfacing — trigger: existing `context_bootstrap.py` `queue_context()` at Phase 1 Assess (already enumerates `"issues"`) — verified-live: pending
- agent-discretion issue logging — trigger: Phase-3 implementer guidance (existing call site, `references/phase-3-execute.md:33`) — verified-live: yes

Verification detail (each `pending` maps to a task in chunk D): bug-report capture →
unit test on a seeded bug-phrasing transcript + a headless `claude -p "<trivial>"
--plugin-dir <checkout>` harness probe in a throwaway repo asserting an `issues/`
entry is written by the real pipeline; issues/ surfacing → assert a captured entry
appears in the bootstrap packet counts + top items. The `yes` entry already fires
today — this plan only changes the SHAPE it writes, to the shared template.

Rule-brittleness finding (dogfood, recorded under Known design tensions below):
the activation-map rule keys the trigger-key and verified-live-key on the SAME
physical line, so wrapped prose trips it — filed as a follow-up, not fixed here.

## Commits (MECE)

| # | chunk | files (owned) | modifies_api | risk_reason |
|---|---|---|---|---|
| 1 | A — issue template + shared shape | `templates/issue.md` (new), `skills/build-loop/references/phase-3-execute.md` (point the agent-discretion path at the template), `skills/build-loop/references/memory.md` (note issues/ provenance shape) | false (new asset + doc) | none |
| 2 | B — bug_report signal class | `scripts/scan_corrections/detect.py` (add `BUG_REPORT_PATTERNS` + `kind="bug_report"`), `scripts/scan_corrections/test_detect.py` | false (additive pattern set; existing kinds unchanged) | none |
| 3 | C — write routing + capture | `scripts/scan_corrections/__main__.py` (route `kind=="bug_report"` → `issues/` via `templates/issue.md` shape with `status: unverified` + `captured_at`/`source`/`run_id`/verbatim quote; all else unchanged), `scripts/scan_corrections/test_cli.py` | false (new branch; default path byte-identical) | none |
| 4 | D — surfacing + harness proof | `scripts/test_context_bootstrap.py` (assert a captured issue surfaces), one headless-harness integration probe (`hooks/test_closeout.sh`-style or a new `scripts/test_issue_capture_e2e.py`) | false | none |

Scope-auditor: skip — zero public-signature changes (additive pattern set + a write-routing branch whose only caller is the Stop hook, updated in-chunk).

## Known design tensions (decided here)

1. **False positives** (regex can't tell a real unaddressed bug from one just fixed, or
   a hypothetical "if X breaks"). Decision: capture anyway with `status: unverified`;
   Phase 1 re-validates every issue against current code (existing hypothesis lesson);
   SessionStart surfaces them for one-glance dismissal. The gap is "no auditable
   trace" — a status-tagged entry IS the trace; suppressing capture to avoid noise
   re-opens the gap. Precision lives on `status`, not on whether to record.
2. **Direct-to-issues vs candidate lane.** Decision: write straight to `issues/` (not a
   `pending-issues/` promotion lane). The deliverable is an immediate auditable trace;
   a promotion step delays exactly that. `issues/` is repo-local + short-lived +
   Phase-5-drained, so noise is bounded.
3. **Pattern recall.** Bug phrasings overlap feature requests ("X should do Y"). Keep
   patterns tight (`broken` / `doesn't|don't work` / `not (loading|showing|working)` /
   `crashes?` / `errors? out` / `regress(ed|ion)` / `is\s+(?:still\s+)?broken`); accept
   misses. Per last run's lesson: **fixtures must be the exact prose of real bug
   reports** — mine `~/.claude/projects/**/*.jsonl` for actual user bug turns and use
   them as test fixtures, not hand-written phrasings.

## Acceptance

- **A (template):** `templates/issue.md` exists with provenance frontmatter (title,
  repo, branch, created, source ∈ {transcript-bug-report, agent-discovery,
  user-request}, captured_at, run_id, status ∈ {unverified, open, fixed, wontfix,
  duplicate}, classify, user_impact, verbatim_quote). Phase-3 doc references it.
- **B (detection):** a user turn "the login button is broken" → one `bug_report`
  candidate; "always use uv" → still a `preference` (no regression in the 3 existing
  classes); a question "why is X broken?" → no capture (question-skip holds). Fixtures
  drawn from real transcripts.
- **C (capture):** a `bug_report` candidate writes `.build-loop/issues/<ts>-<hash>.md`
  in the template shape with `status: unverified` + verbatim quote; corrections/
  preferences/tradeoffs still write `pending-lessons/` byte-for-byte as today;
  fail-open + budget + dedup contract intact (existing tests green).
- **D (activation, verified-live):** the headless-harness probe shows the REAL Stop
  hook on the dev checkout writing an `issues/` entry from a seeded bug transcript, and
  `context_bootstrap` surfaces it — converting both Activation Map `pending` → `yes`.
- Self-mod gate pass per commit; Fable independent-auditor on the full diff before
  Report; plan-verify clean on this plan (incl. the activation-map rule it dogfoods).

## Effort

S–M (4 small chunks, all additive, riding existing infrastructure). Suggested tiering:
Fable plan (this doc) → Opus/Sonnet implementers per chunk → Fable auditor. Parallel-
safe: A‖B, then C depends on A+B, then D depends on C.
