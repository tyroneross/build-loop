<!--
SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com>
SPDX-License-Identifier: Apache-2.0
-->

# Build-Loop — Lessons Learned: rally-point CLI hardening session

| Tag | Value |
|---|---|
| **Date** | 2026-05-30 |
| **Subject repo** | `agent-rally-point` (`tyroneross/agent-rally-point`, branch `main`) |
| **Applies to** | build-loop Phase 1 Assess + verification discipline; any tool/CLI/plugin that emits structured output |
| **Work effort** | **XL** — full backlog drained to zero (R10, B17, `rally mission`, B18 reframe, 20 code-scan findings, all housekeeping) + JSON-envelope standardization + inject validation/hardening |
| **Span** | ~12 subagent dispatches (10 `coder`/`Explore` + 1 multi-agent `Workflow` scan of 8 agents) · ~15 commits to `main` · 48 → **255** tests · multi-hour interactive session |
| **Tokens (subagents)** | ≈2M output tokens (scan workflow 646K; coders ~75–135K each; explores) |
| **Verification** | Evidence-grounded — live tmux inject e2e, ledger round-trip, `cargo test`, raw-JSON dumps; not inferred |

---

## Dominant through-line

**The session's recurring defect was my own ad-hoc verification, not the code under test.** ~5×
I drew a wrong "it's broken / empty / missing" conclusion from a sloppy `bash | python -c`
one-liner — wrong JSON nesting (×3), missing required args (`enter` without `--tool`), a missing
required flag (`status` needs `--global`), a string-vs-dict field, and `.result.ranked` vs
`.ranked`. Every time, the feature was fine; my probe was wrong. The durable fixes were a uniform
output contract + an in-repo contract test — **not** a cleverer one-liner. Everything below
radiates from that. (Verify-time companion to the standing
[verify-the-negative](../../../build-loop-memory/lessons/0001-2026-05-30-verify-the-negative-before-asserting-it.md)
lesson, which was already the headline of three lesson stores.)

## Lessons

### L1 — A confusing-to-parse output contract is a *product defect*, not a parsing-discipline problem ✅ high-confidence
The `rally --json` envelope had three inconsistent nesting patterns (some results under
`data.<command>`, some flat, `wake-due` under `data.due`) plus two commands emitting non-JSON under
`--json`. No "dump raw first" discipline fixes an output with no rule — every consumer guesses.

**Lesson:** when consumers (incl. future-you) keep mis-parsing a tool's output, suspect the
*contract*, not the parser. Standardize so `data[command]` always holds the result and enforce it
with a **COMMANDS-driven contract test** so a new command physically can't skip it. Pre-1.0 with
~0 external consumers is the cheap moment to do it. Applies to build-loop's own JSON-emitting
scripts and any plugin tool surface.

### L2 — Async deliver-then-ack: a timeout is not a failure of the primary action ✅ high-confidence, live-verified
`rally inject --require-ack` recorded the content fact and delivered the message *before* waiting
for the ack, then on ack-timeout returned `ok:false`/exit-1 — which a caller reads as total failure
→ retry → **duplicate delivery**.

**Lesson:** the durable primary action (record + deliver) reports its own success; the downstream
ack is separate metadata. On timeout return `ok:true, delivered:true, ack:{resolved:false,
timed_out:true}` so the caller sees the message landed and checks `ack.resolved` instead of
re-sending. The failure response must distinguish "not acked" from "not delivered." Generalizes to
any at-least-once messaging / handoff / job-submit surface (including build-loop's coordination posts).

### L3 — A backlog/plan item is a hypothesis, not an instruction — Phase 1 re-validates twice ✅ evidence-based
Most rally-point rows ranked "open" had already shipped that same session (the shipped-but-not-
recognized class). Worse: **B18's written prescription — "hard-reject foreign-repo writes" — would
have violated the never-block charter.** The already-shipped quarantine-and-filter was the correct
approach. Building the backlog literally would have regressed a core principle.

**Lesson:** Phase 1 Assess validates each candidate item twice — (a) is it *still an issue*, against
current code; (b) is the prescribed *fix* still right, against the charter/invariants. Write one
line of evidence for each, or rewrite the approach. Reconcile the backlog to truth before planning.
A backlog is the start of an assessment, not the end of one. → also in
`build-loop-memory/lessons/2026-05-30-backlog-item-is-a-hypothesis.md` + `.build-loop/feedback.md`.

### L4 — Ad-hoc bash verification of a CLI is itself a defect surface ✅ high-confidence (the meta-lesson)
The 5 wrong-negatives all came from hand-written `bash | python -c` probes with wrong nesting,
missing args, or missing flags. The in-repo test suite — which uses correct args and the real field
map — was green the whole time.

**Lesson:** for verifying a CLI/tool, **trust and extend the project's test suite** (it already
encodes correct args + expected shapes); reach for an ad-hoc one-liner only when unavoidable, and
then capture **stdout AND stderr** separately and read `--help`/source for the exact required
args/flags first (CLIs emit errors to stderr + nonzero exit with empty stdout). For build-loop's
Validate/Fact-Check, prefer running the project's own tests over bespoke output parsing.

---

## Cross-references
- Subject-repo lessons: `agent-rally-point/LESSONS.md` #10–#12.
- Cross-project: `build-loop-memory/lessons/{0001-…verify-the-negative, 2026-05-30-backlog-item-is-a-hypothesis}.md`.
- Global agent memory: `feedback_verify_before_asserting_negative_recurrence.md`, `feedback_tool_output_contract_design.md`.
- Runtime feedback log: `.build-loop/feedback.md` (2026-05-30 lines).
- Output contract reference: `agent-rally-point/docs/JSON_ENVELOPE.md`.
