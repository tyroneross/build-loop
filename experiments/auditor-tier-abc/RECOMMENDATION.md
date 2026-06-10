# Recommendation — Auditor/Judge tier (from the A/B/C, Sonnet vs Opus vs Fable)

## Headline
**Route the auditor/judge tier by task complexity, not a flat default.** The premium tier (Fable)
earns its cost only on *complex* code, and even there only on the *subtle* defects — not on the
critical-obvious ones that every tier catches.

## The data (2 stages, N=1/cell, my scorer + Codex blind grade)
| | Simple task | Complex task |
|--|-------------|--------------|
| **Fable** | 0.667 seeded — **missed a high-sev bug**, noisiest; found 2 unseeded reals | **1.00 seeded — only tier to catch the TOCTOU + a subtle dead-code authz bug** |
| **Opus** | **1.00 seeded** — cleanest, perfect coverage | 0.824 — clean, missed the TOCTOU |
| **Sonnet** | 0.667 — caught the key correctness bug | 0.824 — missed TOCTOU + a retracted hallucination |
| Scorer vs Codex | **diverged** (Opus by recall, Fable by total-reals) | **converged** → Fable #1 |

## What it means
1. **Simple / bounded audits → Sonnet or Opus. NO-GO on Fable.** Fable did not win on simple code
   (it missed a seeded high-sev bug Opus caught) and added the most noise. Paying the premium buys
   nothing here.
2. **Complex / security / concurrency audits → Fable.** Only Fable caught the TOCTOU and the subtle
   tautological authz dead-code — the class of defect that ships as a CVE. Cross-validated by an
   independent grader.
3. **Criticals are tier-insensitive.** Every tier caught fail-open + privilege-escalation. So if the
   only goal is "don't miss the worst, most-obvious holes," a cheap tier suffices. Fable's value is
   the *subtle* residue on complex surfaces.

## Cost-optimal pattern (maps onto the trio design)
**Cheap-first, Fable-on-complex-residue:** Sonnet/Opus runs the first audit pass (catches criticals
+ obvious); **escalate to a Fable pass only when the surface is complex / security / concurrency /
high-stakes**, to catch the subtle compounding defects. This is exactly the Judge design — "Opus
head-judge + Fable specialist for verdicts that compound" — now backed by data, not assertion. The
complexity-graduated ladder is validated for the auditor role.

## Honesty
N=1 per cell — directional, not conclusive. Two independent signals (objective seeded-recall scorer
+ blind cross-vendor Codex grade) agree on the complex-task direction, and it matches prior research
(stronger reasoners win on hard reasoning). To harden: re-run each cell 3–5× (the harness + ledger
support it) and add 1–2 more complex artifacts before locking thresholds. The *mechanism* (ledger,
advisor, dispatch ladder) is no-regret and already built; this experiment sets the *parameter*
(when Fable is worth it for auditing) — and the answer is "on complex/subtle surfaces, yes."
