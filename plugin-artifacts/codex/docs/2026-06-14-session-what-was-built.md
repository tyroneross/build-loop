<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
<!-- status: DRAFT · session 2026-06-13/14 · author: Opus -->

# What was built this session, the problems it solved, and why these approaches

## Summary

This session shipped one production release (build-loop **v0.35.0**) and produced a set
of proposals, one live-tested method, and a reusable template. Nearly everything traces
to a single recurring weakness in build-loop, so the work either closes an instance of
that weakness or hardens the system against it.

## The through-line problem

Two linked failure modes kept recurring, and most of the session targets them:

- **Dormant machinery** — a control or feature exists in code, its tests pass, but the
  real trigger never fires on the real input. Four live instances in one week (a WARN
  gated on a data shape the gate misread; a crash-marker that read the wrong phase field;
  repo-level Codex hooks that never fired; run identity reused so new runs were never
  recorded).
- **Silent run-close** — an inline run (the methodology followed on the host loop, with
  no orchestrator dispatch) ends without recording itself, so it is invisible to the
  learning phase and any skipped-judgment gap stays silent until a human asks.

## What was built

### 1. f6 — structural run-close (shipped, v0.35.0)

- **Problem:** inline runs never reached the orchestrator's run-close step, so they were
  invisible to Learn and the Frontier-judgment gap was silent. Observed across three
  consecutive sessions where a human had to prompt the closeout.
- **Built:** a host `Stop` hook that records the run, runs the judgment gate, surfaces a
  one-line WARN when a stakes-gated run skipped the Frontier judgment layer, sweeps
  crash-orphaned runs at the next session start, and releases the run identity once a
  terminal outcome is recorded. Plus two adjacent fixes: the crash-annotator now honors
  both the orchestrator and inline phase conventions, and a fresh run no longer inherits
  the previous run's state.
- **Why this approach:** a Stop hook is the only host event at run-close that carries
  session context. It cannot dispatch agents, so it auto-records and auto-surfaces the
  gap rather than pretending to run the judgment itself — an honest scope limit, stated
  as such. It is fail-open, self-gated on `.build-loop/` presence, and idempotent, and it
  reuses the existing run-record writer and judgment gate rather than adding new logic.

### 2. Agent-spec hardening (shipped, v0.35.0)

- **Problem:** the dormant-machinery class itself — plans propose event-driven components
  whose activation is never verified; hand-off briefs leave the "done" definition implicit.
- **Built:** a plan rule that requires any plan proposing an event-driven component to map
  each one to a real trigger and a verified-live state; a seventh required hand-off-brief
  field (`acceptance-criteria`); and corrections to the role taxonomy (a "core" agent is
  one whose verdict gates a step, not one on a given model tier; the no-sub-sub-agents
  rule is documented as a delegation-depth security cap).
- **Why:** extend the three enforcement surfaces that already own these concerns instead
  of adding new mechanisms, per the governing simplicity rule. Each change cites a named,
  observed failure in this repo — not a cited statistic or external framework.

### 3. RCA discipline upgrade (proposed + live-tested, not yet shipped)

- **Problem:** root-cause analyses that stop at a symptom or rank the wrong cause first,
  which costs extra fix-and-review iterations.
- **Built (as a plan and a test):** three levers — separate the "why it existed" and "why
  controls missed it" questions; refuse to close until a counterfactual shows the fix
  would have caught this exact failure on the real input; prefer eliminating a failure
  mode over merely detecting it — plus a root-cause-layer taxonomy for cross-run pattern
  detection. Then a live within-subject A/B test against two real bugs.
- **Why this approach:** build-loop's RCA is already strong (causal tree, evidence
  typing, anti-blame), so the work extends it against observed failures and explicitly
  rejects framework-sprawl (severity ladders, owner/due-date fields, a 12-section schema).
  And it was tested before building: the levers won the A/B (clearly on the hard bug),
  which is what authorized the build under a decision rule fixed in advance.

### 4. Reusable experiment-results template (built)

- **Problem:** results write-ups that assert unsupported quality claims and omit metric
  direction or significance.
- **Built:** one fill-in-the-blanks template covering A/B, DOE, regression, backtest, and
  ablation, with non-deletable honesty rails — state the sample size, give a direction for
  every metric, force "not computed — why" when statistics do not apply, and earn every
  adjective.
- **Why:** generalize the report structure that worked this session and bake in the
  honesty the work kept needing.

## The method (how, not only what)

- **Extend before add.** Prefer deleting or extending an existing surface over a new
  mechanism; a new rule must earn its place against a named observed failure.
- **Verify the activation path.** The lesson the session kept re-learning: the
  activation-map rule itself shipped dormant twice (it fired on prose *about* activation,
  not only real proposals) and was caught by audit, not by its own tests.
- **Test before build.** The RCA A/B was pre-registered — goal, measures, and decision
  rule committed before any arm ran, with a guard requiring the bug I did not personally
  diagnose to carry the result.
- **Honest labeling.** No overclaiming: "partial blinding," "n=2 directional," "dormant
  pending wiring" are stated plainly where they apply.
- **Adversarial verification.** Every self-modification passed the self-mod test gate and
  an independent auditor before being called done.
- **Memory closeout.** Durable lessons and provenance were routed to build-loop-memory,
  not left only in the run scratchpad.

## Status

- **Shipped + released:** f6 + agent-spec hardening (v0.35.0, npm + GitHub Packages).
- **Proposed, not executed:** issue-capture-gap plan; RCA-discipline-upgrade plan (W1–W4).
- **Tested:** RCA upgrade (treatment won; n=2, directional, partial blinding).
- **Built but dormant:** the experiment-results template — no trigger points to it yet.
- **Recorded:** two lessons + one reference + a milestone in build-loop-memory.

## Honest open items

- The results template is dormant until its activation path is wired (skill promotion
  and/or build-loop report-step pointers, each with a verified-live check).
- The RCA plan is authorized by the test but not yet built.
- This session's documentation commits are stranded on a non-main local branch and are
  not backed up — a branch-hygiene casualty of a checkout shared across sessions.
- The live test was n=2 with partial blinding: directional evidence, not a conclusion.
