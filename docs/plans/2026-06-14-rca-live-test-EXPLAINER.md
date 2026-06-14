<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
<!-- Plain-language companion to 2026-06-14-rca-live-test-{preregistration,RESULTS}.md -->

# What the RCA live test was, in plain terms

## Bottom line (read this first)

We ran a fair, blind head-to-head between build-loop's CURRENT root-cause method and
a PROPOSED upgrade, on two real bugs. The upgrade won both. It mattered most on the
hard, ambiguous bug, where it reached the correct cause with half the effort and was
honest about what it could not verify. It added overhead with no correctness gain on
the easy bug. Conclusion: build the upgrade, but keep its full weight for hard bugs.

## The terms, defined before use

- **Root cause analysis (RCA):** figuring out the TRUE underlying reason a bug
  happened, not just the surface symptom.
- **Subject 1 (S1):** a bug in build-loop's own plan checker. A rule meant to fire only
  when a plan PROPOSES new event-driven machinery was firing on a sentence that
  explicitly said "this plan adds NO such machinery."
- **Subject 2 (S2):** a bug in the easy-terminal app. Idle "Agent" rows pile up in the
  sidebar, about one more every time the smoke test runs, and survive app restarts.
- **The three upgrade levers being tested:**
  - **Creation/escape split:** answer two questions, not one — why the bug EXISTED, and
    separately why the safeguards FAILED to catch it.
  - **Counterfactual closure:** don't call a fix done until you can say "if this fix had
    existed, it would have caught THIS exact bug on the REAL input."
  - **Fix-strength ladder:** prefer eliminating the failure mode over merely detecting it.

## The roles — what each was, did, expected vs actual

### Control arm
- **What it was:** build-loop's current `root-cause-investigator` agent, unchanged.
- **What it did:** diagnosed S1 and S2 from source code, blind to the other arm.
- **Expected:** competent root causes (this agent is already strong).
- **Actual:** S1 — found the cause efficiently (4 tool calls). S2 — spent 89 tool calls
  / ~12 minutes and anchored on an INTERMITTENT cause that cannot explain a steady
  one-per-run leak. It did surface a second real defect, but ranked the wrong one first.

### Treatment arm
- **What it was:** the same agent plus the three levers above, injected as instructions
  (a faithful stand-in for the built version — test before shipping).
- **What it did:** diagnosed the same two bugs, blind to the control arm.
- **Expected:** more thorough, possibly slower.
- **Actual:** S1 — same root cause, richer (named creation+escape and a valid
  counterfactual), but 3x slower (13 calls) on an already-easy bug. S2 — fewer calls
  (42), correctly identified the DETERMINISTIC driver, and honestly flagged the one fact
  it could not verify (a daemon written in Rust whose source is not in the repo) instead
  of overclaiming.

### Judge
- **What it was:** an independent reviewer agent, blind to which output came from which
  arm. (Planned as Fable; ran on Opus because Fable was unavailable this session.)
- **What it did:** read the actual code to establish the TRUE cause for each bug, scored
  both outputs on five measures, and picked the stronger.
- **Expected:** neutral adjudication on ground truth, not style.
- **Actual:** picked the treatment on both bugs. Also caught nuance neither side should
  hide: the control found a second real defect the treatment underweighted, and the
  treatment's edge on S2 came largely from honest calibration.

### Pre-registration
- **What it was:** a document committed BEFORE any arm ran, fixing the goal, the
  measures, and the win/lose rule.
- **Why:** so the verdict could not be reverse-engineered to fit the result, and so a
  self-bias guard was locked in (a real win required the bug I did NOT personally
  diagnose, S2, to carry the correctness gain).

## The result against the locked rule

The treatment reached the true root cause at least as well on both bugs (tied on S1, won
on S2), produced a strictly stronger artifact on both, and carried its correctness win on
S2 — the bug I did not diagnose. That satisfied the pre-registered win condition.

## Honest caveats

- Only two bugs. Directional evidence, not proof.
- The judge ran on Opus, not the planned Fable.
- The benefit is concentrated on hard bugs; on the easy bug the levers cost effort for no
  correctness gain.
- The treatment was not strictly better — the control found a second real defect it
  underweighted; the best fix merges both findings.
