<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
<!-- status: PRE-REGISTERED 2026-06-14 (committed BEFORE any arm ran) | author: Opus -->

# Live test pre-registration — RCA discipline upgrade (W1–W3)

Committed before any agent runs, so the verdict cannot be retrofit. This tests
the **proposed** RCA levers by prompt-injection (treatment = baseline agent +
the W1–W3 levers appended), not by shipping them — test-before-build, reversible.

## Goal

Decide whether the RCA discipline upgrade reaches the **same-or-better root-cause
outcome** on real bugs, and whether it does so **without a disqualifying speed
cost**. A clean win authorizes building the RCA plan; a neutral/negative result
revises or shelves it.

## Hypothesis

H1: treatment reaches ≥ control's root-cause correctness (no regression).
H2: treatment produces a measurably stronger artifact on ≥1 of {creation/escape
completeness, fix strength, valid counterfactual} with no regression on the other
subject.
H0 (null): no correctness or quality delta attributable to the levers.

## Design — within-subject, blind A/B

- **Subjects (2 real, currently-reproducing bugs):**
  - **S1 build-loop:** `scripts/plan_verify.py` `activation-map-required` fires on
    prose that *discusses* activation (wrapped entry lines, "adds no hook" notes),
    not only on genuine event-driven proposals. Reproduces live.
  - **S2 easy-terminal:** idle agent panes accumulate (+1 per smoke run, observed
    11→15) and persist across relaunches with stale roster rows.
- **Arms (same model both — Sonnet — to isolate the PROMPT delta):**
  - **Control:** `build-loop:root-cause-investigator`, symptom packet only.
  - **Treatment:** same agent + appended W1–W3 levers (verbatim below).
- **Blinding:** both arms get the identical symptom packet; both are barred from
  reading `.build-loop/{issues,backlog,retrospectives}` (where prior diagnoses
  live) — equal constraint. A **Fable judge** scores both outputs unlabeled
  ("RCA-A"/"RCA-B"), re-deriving ground truth from source code, blind to which is
  treatment until after scoring.
- **Out of test scope (pre-declared):** W4 root-cause-layer — it is a *cross-run*
  clustering signal, structurally incapable of changing a *single-task* outcome,
  so testing it here would be invalid. Excluded by design, not by omission.

## Treatment levers (the exact appended text both we and a built plan would use)

> ADDITIONAL RCA DISCIPLINE (apply strictly, in addition to your normal method):
> 1. CREATION vs ESCAPE — answer two distinct questions: (a) why did the defect
>    EXIST at all; (b) why did it ESCAPE the controls that should have caught it.
> 2. COUNTERFACTUAL CLOSURE — do not close until you state a one-line
>    counterfactual: "if <lever+actuator> had existed, it would have
>    prevented|detected|contained THIS exact failure" — with evidence it fires on
>    the REAL input shape, not a hand-constructed one.
> 3. FIX STRENGTH — choose the strongest feasible fix and name its rung:
>    eliminate failure mode > make the invalid state impossible > automated
>    blocking control > earlier detection > containment/rollback > decision
>    support > docs. For an unowned dependency, never "ignore" — isolate /
>    validate / monitor / degrade / escalate / accept-residual-risk-explicitly.

## Measurements (operationalized; judge scores each arm)

| ID | Measure | Scale |
|----|---------|-------|
| M1 | Reached the true root cause | 0 missed / 1 partial / 2 full |
| M2 | Creation + Escape both identified | 0 neither / 1 one / 2 both |
| M3 | Fix-strength rung of the proposed fix | 1 (docs) … 7 (eliminate) |
| M4 | Counterfactual stated AND would fire on the real input | 0 / 1 / 2 |
| M5 | Stopped at system level (not surface/symptom) | 0 surface / 1 system |
| SPD | Speed proxy | `tool_uses` + `duration_ms` from each arm's envelope |

Quality score Q = M1 + M2 + (M3 normalized 0–2) + M4 + M5, per subject per arm.

## Success criteria (pre-registered decision rule)

- **WIN → build the RCA plan** iff, across both subjects: treatment M1 ≥ control
  M1 on BOTH (no correctness regression) AND treatment Q strictly > control Q on
  ≥1 subject AND treatment Q ≥ control Q on the other AND treatment SPD is not
  >50% higher than control on tool_uses *without* a correctness gain.
- **NEUTRAL → revise** iff no correctness regression but no clear Q gain.
- **FAIL → shelve/rethink** iff treatment regresses M1 on either subject.
- Tie-break philosophy (standing org): Accuracy > Speed > Cost. A
  slower-but-more-correct treatment still WINS; a faster-but-shallower one does not.

## Confounds + mitigations

- *Self-bias* (I proposed the levers): the judge is an independent Fable agent
  re-deriving ground truth from code; I do not pre-assert the answer to it.
- *Difficulty confound*: within-subject (same bug, both arms) controls for it.
- *Model confound*: both arms pinned to Sonnet.
- *Contamination*: both arms barred from the diagnosis dirs; S1's diagnosis lives
  in `.build-loop/backlog/` (barred), S2 has no committed diagnosis.
- *Order/anchoring on the judge*: outputs presented unlabeled; A/B mapping hidden.
- *n=2*: small — this is a directional decision aid, not a powered study. Stated plainly.

## Pre-committed interpretation honesty

If treatment wins only on S1 (the bug I personally diagnosed) and not S2,
discount it as possible self-bias and treat as NEUTRAL. A real win needs S2
(the bug I did not diagnose) to carry quality gain.
