# RCA enhancement: assessment of two external reviews + build-loop integration plan

Date: 2026-06-14 · Status: assessment + plan (implementation routes through build-loop).

## What was assessed

Two independent external assessments (different models) of a compact RCA prompt, both
recommending the prompt be upgraded toward a fuller incident-learning protocol. Both
cite mature practice (AHRQ, Google SRE, NASA, RCA2). This doc judges them **on fit to a
solo agentic coding harness** and maps them against build-loop's existing debug system —
not on whether they sound authoritative.

## Assessment of the two reviews

**Both are high quality and strongly convergent.** Independently they recommend the same
seven upgrades: (1) proportional triage/severity scaling, (2) evidence artifacts +
timeline, (3) a counterfactual closure test, (4) an action-strength hierarchy (eliminate
> … > docs), (5) replacing "ignore" with degrade/escalate, (6) a broader cause taxonomy
incl. agent failure modes, (7) validation-that-the-fix-worked + regression guard. The
convergence across two models is itself a signal these are real.

**But their key blind spot: they review the prompt in isolation.** build-loop already has
a mature debug system (`debug-loop` skill, `root-cause-investigator` + `fix-critique`
agents). Measured against it, ~80% of both reviews' recommendations are **already
implemented** — so adopting their revised prompts wholesale would duplicate and bloat.
The value is the **small set of genuine gaps**, not the full apparatus.

### Recommendation → already in build-loop? (gap map)

| Recommendation | In build-loop today | Verdict |
|---|---|---|
| Proportional triage | ✅ `debug-loop` Scope Check (skip trivial/KNOWN_FIX; enter on verdict) | have it (binary, sufficient) |
| Anti-blame → system-control terminal cause | ✅ investigator + Phase 2 (no "agent forgot") | have it |
| Evidence discipline + strength levels | ✅ Strong/Moderate/Weak; Phase 4 "every step → evidence" | have it |
| Symptom separated from cause | ✅ Phase 1 symptom node | have it |
| Causal map beyond a ladder | ✅ causal tree + 6 frameworks (Ishikawa, K-T, differential, falsification) | have it (stronger than the reviews') |
| Verify fix worked + regression guard | ✅ Phase 4 Verify, Phase 5 Score (5 evidence-gated criteria) | have it |
| Pressure-test / critique | ✅ `fix-critique` agent (Phase 6) | have it |
| Spread check (same bug elsewhere) | ✅ fix-critique "does the same bug exist elsewhere?" + Score symptom-coverage | have it |
| Durable prevention control | ✅ Report #6, investigator #5 | partial — present but **unranked** (see Gap 3) |
| Transparency / honest report | ✅ Phase 7 ✅/⚠️/❓ | have it |
| Iteration + convergence + escalation | ✅ Iteration rules, 5x hard stop | have it |
| **Counterfactual closure test** | ❌ grep: none | **GAP 1** |
| **Exists-vs-escape (detection) split** | ❌ grep "escape": none; traces creation path only | **GAP 2** |
| **Action-strength ranking of the fix** | ⚠️ lists controls but doesn't rank by durability | **GAP 3** |
| Severity tiers L1/L2/L3 | binary scope check instead | defer (binary already prevents RCA fatigue) |
| Owners / due dates / formal action tracking | n/a — solo agent, no ticketing; incidents stored in `.build-loop/issues/` | reject (org-scale apparatus, wrong fit) |
| 10–12 section mandatory output template | Phase 7 report (13 items, scaled, marked) exists | reject (bloat; conflicts with concise-output rule) |

## The three gaps worth adopting (surgical, no bloat)

**Gap 1 — Counterfactual closure test (highest value).** build-loop's Score proves the
symptom is gone; it does not prove the *prevention control* would have caught this. Add a
Score criterion: *"Had the proposed prevention control existed beforehand, would it have
prevented / detected / contained THIS exact failure before it surfaced? Name the control
and the trace point it would have fired at."* This sharpens the otherwise-implicit link
between root cause and durable fix, and it's the single best idea both reviews surfaced.

**Gap 2 — Exists-vs-escape split.** The investigator traces *why the bad thing existed*
(creation path) but never structurally asks *why no control caught it* (detection/escape
path). These demand different fixes (validation/invariant vs. test/monitor/gate). Add a
required second branch class to `root-cause-investigator` and a line to Phase 2 / Report.
(Note: this is distinct from the existing spread-check, which asks where else the bug
exists, not why it escaped.)

**Gap 3 — Action-strength ranking.** "Durable prevention control" is named but unranked,
so a weak doc/training control can pass as durable. Add a strength ladder and require the
strongest feasible: eliminate the failure mode > make invalid states impossible > add an
automated blocking gate > add earlier detection (test/eval/monitor) > add containment/
rollback > decision-support/checklist > docs-only (last resort). Fits the standing
"always the durable fix" + "attack over defense" rules.

## Integration plan (3 files, all edits route through build-loop)

| # | File | Edit | Size |
|---|---|---|---|
| 1 | `agents/root-cause-investigator.md` | add the exists-vs-escape branch class to the causal-tree process + JSON output (`escape_path`); add the action-strength ladder to the prevention-control field | S |
| 2 | `skills/debug-loop/SKILL.md` | Phase 2: state both creation + escape cause; Phase 5: add Score criterion #6 (counterfactual); Phase 6 `fix-critique` input: include the counterfactual verdict; Phase 7 Report #3/#6: escape path + ranked control | S |
| 3 | `agents/fix-critique.md` | add Check: "counterfactual — would the prevention control have caught this exact failure?" alongside existing spread/regression checks | XS |

Explicitly NOT doing: new severity tiers, owner/due-date fields, a parallel 10-section
output template. build-loop already triages, reports, and stores incidents; adding those
would be the process drag both reviews warned about.

Sequencing: one build-loop run, three chunks (file 1 → 2 → 3), `plan-critic` on the spec,
`fix-critique`/independent-auditor on the diff. Anti-dormancy check: confirm the new Score
criterion actually gates (a fix that fails the counterfactual must not pass Phase 5).

## Meta-note
The disciplined move here was the same RCA lever logic applied to a build decision: don't
adopt an authoritative-looking external recommendation wholesale — check what already
exists, and patch only the real gap. ~80% was already built; the deliverable is 3 small
edits, not a new protocol.
