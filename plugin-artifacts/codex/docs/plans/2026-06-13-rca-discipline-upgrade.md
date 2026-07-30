<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
<!-- status: PROPOSED (not executed) | author: Opus | date: 2026-06-13 -->

# Plan — Sharpen build-loop RCA: counterfactual closure, creation/escape split, fix-strength

## Framing (what already exists — do NOT rebuild)

A code-grounded read of build-loop's current RCA shows it already implements most of
the assessment's recommendations, often better-justified:

| Assessment recommendation | Already in build-loop |
|---|---|
| Causal map, not linear 5-whys | `root-cause-investigator` builds a **causal tree**, cites Card 2017 against linear chains |
| Symptom ≠ cause | "Define the Symptom Node" + "Distinguishing Symptoms from Causes" table |
| Evidence + fact/inference/assumption + confidence | `evidence_type` enum + evidence-strength ranking table |
| Reject human-error closure | C-RCA + phase-5: "actor-blame phrases are not terminal causes unless paired with the missing control" |
| Spread check (where else) | C-RCA: "same root cause at other sites → fix all of them" |
| Validation by independent party | C-RCA: root cause + fix + non-regression "verified by an independent subagent before done" |
| Structured output | `root-cause-investigator` emits a JSON envelope (failure_map, causal_tree, prevention_control, system_control_failure) |
| Completeness (explains all symptoms) | "Completeness Check" step |
| Research persistent problems | Research Gate + `root_cause_before_done` "research, don't retry" |

So this is an **extend-three-surfaces** plan, not a new framework. Governing rule
(`AGENTS.md` KISS+DRY): a new mechanism must earn its place against a *named, observed*
failure in THIS repo — not a cited SRE/AHRQ/NASA framework. Each chunk below cites a
real build-loop failure from this week's work.

## The three real gaps (each maps to an observed failure)

**G1 — the stop rule is "actionable system cause," not a counterfactual.** Convergence
(`root-cause-investigator` Step 6) stops at "a concrete fixable control failure with
evidence." It never asks the sharper question: *would the named fix have actually
caught THIS failure?* Observed: the `activation-map-required` rule shipped last run was
"an actionable control" yet was **dormant on 2 of its 4 motivating phrasings** — a
counterfactual stop rule ("would this lever have fired on the real input?") is exactly
what the Fable auditor applied to catch it. The discipline isn't in the prompt.

**G2 — creation and escape are blurred into one chain.** The envelope's `failure_map`
is a single line (symptom → technical → upstream → first controllable failure). It
captures the *escape* half ("why controls didn't catch it") well but not the *creation*
half ("why the defect existed at all") as a distinct axis. Observed: the synthesisDensity
dormant-WARN had two independent causes — it *existed* (gate did `int(dict)→0`) AND it
*escaped* (tests injected the trigger by hand, so the gate's blindness never surfaced).
A single chain nudges toward fixing one; the bug needed both.

**G3 — no fix-strength preference, and unowned-dependency wording risks "ignore."** The
fix taxonomy (dependency-you-don't-own / boundary-you-own / control-didn't-fire) routes
by *type* but not by *strength*. Observed: every fix this week defaulted to "add a
detect-gate" (medium-strength) — none asked "could we eliminate the failure mode or make
the invalid state impossible?" e.g. normalizing synthesisDensity at the *writer* (make
the bad shape impossible) vs. coercing it at the *gate* (detect). A strength ladder
prompts the stronger fix. Separately, the current dependency-routing should never read
as "ignore" — it must be "isolate / validate / monitor / degrade / escalate / accept
residual risk explicitly."

## Explicitly rejected (framework-sprawl — named so they aren't re-proposed)

- **Severity/triage ladder (L1/L2/L3, T0–T3).** REJECTED. There is no observed RCA-
  fatigue failure in build-loop; its model is the opposite — autonomous, *every* issue
  to root cause — and depth already scales by **persistence**, not severity, via the
  Phase-5 stuck-cascade (evidence-gap → memory re-check → parallel-assess @2 fails →
  causal-tree @3 fails). A severity ladder adds a classification step plus a sanctioned
  way to under-investigate. The assessment's #1 recommendation is its least applicable
  to this context.
- **Owner / due-date / tracking closure fields.** REJECTED for core RCA. Single-operator
  autonomous loop: "owner" = the loop; tracking = the existing `followup/` + `backlog/`
  queues. The closure that matters (verification + regression guard) is G1.
- **A 12-section markdown RCA schema.** REJECTED — build-loop already has a structured
  JSON envelope; replacing it with a 12-section human schema is output-bloat against the
  concise-output principle. Extend the envelope with 3 fields instead.
- **Separate factual-timeline artifact.** DEFERRED — run records + transcripts already
  carry timeline; a dedicated section earns nothing observed.
- ~~Root-cause LAYER taxonomy~~ → **PROMOTED to W4** (user request 2026-06-13). Demand
  is now evidenced, and there is a concrete consumer: `recurring-pattern-detector`
  already clusters `runs[]` signals across 3+ runs and the run record already carries
  free-text `root_cause` per phase — a structured `root_cause_layer` enum lets it
  surface "the implementer keeps re-introducing State/memory bugs" as a project-shaped
  blind spot, exactly like its existing `security_finding` cross-run pattern. See W4.

## Approach Lenses

- Clean-sheet RCA protocol doc. Rejected — duplicates root-cause-investigator + C-RCA;
  two sources of truth for one discipline.
- Current-constraints (chosen): three additive edits to the existing surfaces
  (C-RCA text, `root-cause-investigator` envelope+convergence, `fix-critique` rubric).
  Envelope additions are additive JSON fields (consumers ignore unknowns).

## Depends-on (reads-from)

- `agents/root-cause-investigator.md` Step-6 convergence + Output-Format JSON envelope — verified
- `skills/build-loop/SKILL.md` + `AGENTS.md` C-RCA / `root_cause_before_done` text — verified
- `agents/fix-critique.md` rubric (the second-subagent verifier C-RCA names) — to read in-chunk
- `skills/build-loop/references/phase-5-iterate.md` "Diagnose root cause" step (the failure-brief chain G2 extends) — verified

## Commits (MECE)

| # | chunk | files (owned) | modifies_api | risk_reason |
|---|---|---|---|---|
| 1 | W1 — counterfactual closure | `agents/root-cause-investigator.md` (Step-6 stop rule + envelope `counterfactual` field), `agents/fix-critique.md` (rubric line: reject a fix that wouldn't have caught THIS failure), `skills/build-loop/SKILL.md` + `AGENTS.md` (C-RCA: one sentence — "not closed until the lever would have prevented/detected/contained this exact failure") | false (additive field + prompt text) | none |
| 2 | W2 — creation/escape split | `agents/root-cause-investigator.md` (envelope `creation_path` + `escape_path` alongside `failure_map`; Step-2 prompts both axes), `skills/build-loop/references/phase-5-iterate.md` (failure-brief gains the two-question split) | false (additive fields) | none |
| 3 | W3 — fix-strength + de-risk wording | `agents/root-cause-investigator.md` (`prevention_control` guidance gains the strength order), `skills/build-loop/SKILL.md` + `AGENTS.md` (C-RCA fix taxonomy: add strength order; replace any "ignore" with isolate/validate/monitor/degrade/escalate/accept-residual-risk), `agents/fix-critique.md` (rubric: prefer-stronger-control check) | false (prompt text) | none |
| 4 | W4 — root-cause layer taxonomy | `agents/root-cause-investigator.md` (envelope `root_cause_layer` enum + classification step), `agents/recurring-pattern-detector.md` (new cross-run cluster signal `root_cause_layer`), `agents/build-orchestrator.md` (Review-G carries `root_cause_layer` into the `runs[]` phase record — additive), `scripts/test_recurring_pattern_detector.py` (or the detector's colocated test — cluster-by-layer case) | false (additive enum field; consumers ignore unknowns) | none |

No `## Activation Map` is required: the plan introduces no event-driven component —
it sharpens prompt discipline on existing agents fired at their existing call sites,
so there is no new trigger to map. (See the recurring rule-brittleness follow-up: the
detector keys activation tokens near creation verbs, so even prose *about* activation
can trip it — a known wrapped-prose limitation, filed previously, not fixed here.)

Scope-auditor: skip — zero public-signature changes (additive JSON envelope fields,
ignored by existing consumers; prompt text).

## W1 detail (the highest-value change)

Convergence stop rule becomes, in `root-cause-investigator` Step 6 and C-RCA:

> A root cause is not closed at "an actionable control." It is closed when the named
> **lever + actuator** would have **prevented, detected, or contained THIS exact
> failure before it reached the surface** — stated as a one-line counterfactual with
> the evidence that it would have fired on the real input (not a hand-constructed one).

Envelope gains `"counterfactual": "if <lever+actuator> had existed, it would have
<prevented|detected|contained> this because <evidence it fires on the real signal>"`.
`fix-critique` rejects (verdict: needs-work) any fix whose counterfactual is absent or
fails on the real reproduction.

## W4 detail — root-cause layer taxonomy

The investigator classifies each confirmed root cause by exactly one layer (the bug's
true origin layer, not the symptom's surface). Twelve layers, tailored to build-loop's
actual failure modes (agent-/code-shaped, not generic SRE):

`input-data` · `requirements-spec` · `prompt-instruction` · `model-reasoning` ·
`tool-api` · `state-memory-cache` · `orchestration-workflow` · `permission-security` ·
`test-eval-gate` · `observability-alerting` · `human-handoff-process` ·
`external-dependency`

Envelope gains `"root_cause_layer": "<one enum>"` per confirmed root cause (multi-root
trees carry one per confirmed branch). Review-G carries it into the `runs[]` phase
record beside the existing free-text `root_cause`. `recurring-pattern-detector` adds a
cross-run signal: a layer recurring across ≥3 runs emits a `root_cause_layer` pattern
(same shape as its existing `security_finding` pattern) — surfacing a project-shaped
blind spot a project-local rule could catch earlier. Worked dogfood: this week's misses
classify `test-eval-gate` (hand-injected fixtures) + `model-reasoning`/`tool-api`
(`int(dict)→0`); three more `test-eval-gate` roots would flag fixtures as the systemic
weak point — the layer field makes that detectable, free-text `root_cause` does not.

## Acceptance

- **W1:** root-cause-investigator envelope carries a non-empty `counterfactual`; its
  Step-6 text names the prevented/detected/contained test; `fix-critique` rubric has a
  line that fails a fix lacking a real-input counterfactual; C-RCA (SKILL.md + AGENTS.md)
  states the closure test in one sentence. Dogfood check: the synthesisDensity dormant
  case, run through the revised stop rule, would NOT have closed (the gate's
  counterfactual fails on the dict shape) — documented in the chunk's notes.
- **W2:** envelope carries distinct `creation_path` + `escape_path`; phase-5 failure-
  brief prompts both. A worked example (synthesisDensity: created = `int(dict)→0`,
  escaped = hand-injected test fixture) shown in the investigator doc.
- **W3:** `prevention_control` guidance lists the strength order (eliminate → impossible-
  state → automated block → detect → contain → decision-support → docs); no "ignore"
  wording survives `grep -rn "ignore it\|wrap / ignore"`; fix-critique prefers the
  stronger control where feasible.
- **W4:** investigator envelope carries a valid `root_cause_layer` enum per confirmed
  root cause; `recurring-pattern-detector` emits a `root_cause_layer` pattern when one
  layer recurs across ≥3 runs (test proves the cluster fires at 3, not at 2); Review-G
  writes the field into `runs[]` (additive — existing run-record validators still pass).
- Self-mod gate pass per commit (prompt-only chunks: tests are the existing agent/skill
  consistency checks + any envelope-schema test); Fable independent-auditor on the full
  diff before Report; plan-verify clean on this plan.

## Effort

S–M (4 chunks). W1–W3 are prompt/doc-only and co-touch `root-cause-investigator.md`;
W4 adds the enum + a real `recurring-pattern-detector` cluster rule + a test. All four
co-touch `root-cause-investigator.md`, so a single Opus implementer doing W1–W4
sequentially beats fan-out (the file overlap would force serialization anyway).
Fable auditor on the full diff. The only non-additive-prose change is W4's detector
cluster rule + its test; everything else is additive fields and prompt text.
