<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->
# Decision-Quality Doctrine

Twelve judgment rules distilled by Fable 5 from live build-loop decision calls
(2026-06-09). They bind at three orchestrator decision points — Phase 2 plan
acceptance, subagent-return handling, review-finding triage. Each rule carries
the live evidence that earned it; the evidence is the recall hook, not decoration.
`context_bootstrap.py` injects this file at Phase 1/2 (phase-gated), so the rules
are present when the orchestrator decides, not buried behind a "load X" instruction.

## The 12 rules

1. **Ground-truth before accepting any suggested fix.** When a reviewer, auditor,
   or subagent proposes a change, verify its PREMISE against real data before
   implementing — read the actual presets/callers/files the fix assumes. Evidence:
   2026-06-09 an auditor proposed a gate→phase validator check; a 30-second read
   of the presets showed gates key by risk category, not phase name — the "fix"
   would have failed all 5 real presets.

2. **Solicited review is not independent validation.** An agent you asked to check
   work is anchored by the ask. Treat its verdict as input; verify the load-bearing
   claims yourself before acting on them.

3. **Spot-check the load-bearing claims of every condensed return.** Before a
   decision rests on a subagent's summary, pick the 2-3 facts it hinges on and
   re-derive them cheaply (wc, grep, du, run the test). Evidence: 2026-06-09 —
   "zero pytest in CI", "SKILL.md 53KB", "548M sources/" were all re-verified in
   one command before the recommendation shipped.

4. **Converging independent evidence ranks priorities.** An item flagged by two
   independent lenses (static repo audit × run-history mining) outranks anything
   flagged once, regardless of severity labels. Build priority lists by
   convergence first, severity second.

5. **Dependency-order the work.** Ship first the item that makes every other item
   verifiable (CI gate before the checks that ride it; capability index before
   tier routing). Ask of each candidate: what does this unlock?

6. **Test any new deterministic check against ALL real shipped data before landing
   it.** A validator that fails real, intentional inputs is rigidity masquerading
   as safety. The skill_chain vocabulary trap (intake/produce vs assess/plan) was
   caught this way; the gate→phase trap nearly wasn't.

7. **Match verification depth to blast radius.** Reversible + local: exit code
   suffices. Load-bearing claim: re-derive it. Irreversible, production, or
   cross-host: independent audit, full stop. Do not spend Opus tokens verifying a
   typo fix or trust an exit code on a schema migration.

8. **Name the falsifier before locking a decision.** State the single observation
   that would prove the decision wrong; if it is cheaply observable NOW, observe
   it before locking (the presets read that reversed f1's suggested fix cost one
   command). If not cheaply observable, record it in the decision record as the
   revisit trigger.

9. **Parallelism follows write-sets, not enthusiasm.** Fan out only across disjoint
   write-sets. Same-repo overlapping-file work queues behind the active writer even
   when it looks independent (rec-4 queued behind the hardening run for exactly
   this reason). Read-only fan-out is always safe.

10. **Research before asserting anything that evolves.** Versions, APIs, pricing,
    host behavior, model IDs. Ladder: memory (cheapest) → local docs/code → live
    docs/web. Never silently fall back to training data; mark unverified claims as
    unverified.

11. **Capture human philosophy verbatim at decision time.** When the user states a
    preference, the decision record carries their words, not a paraphrase — weaker
    models (and future sessions) calibrate from the original. Evidence:
    enforcement-philosophy decision #4 quotes the user directly and successfully
    steered two subsequent dispatches.

12. **Lowest tier that produces a verifiable output.** script → Haiku → Sonnet →
    Opus; escalate on evidence (2 failures or surfaced ambiguity), never
    preemptively; cheaper tier → stronger check. System-optimal means counting the
    whole-system cost: a hook runs on every Bash call in every consumer project; a
    SKILL.md line loads in every session. Weigh diffs by where they execute, not
    how small they read.

## Where the rules bind (orchestrator decision points)

- **Phase 2 plan acceptance** → rules 4, 5, 8, 9, 12 (convergence-rank, dependency-
  order, name the falsifier, write-set parallelism, tier choice).
- **Subagent-return handling** → rules 2, 3, 7 (solicited ≠ independent, spot-check
  load-bearing claims, verification depth = blast radius).
- **Review-finding triage** → rules 1, 6 (ground-truth the premise, test new checks
  against all real data).

Rules 10 and 11 are cross-cutting (every assertion; every captured human preference).
