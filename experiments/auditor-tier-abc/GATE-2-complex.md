# Gate 2 — Complex task (N=1 per tier, directional)

## Scores
| Tier | seeded weighted-recall | crit/high recall | caught C3 (TOCTOU)? | unseeded reals | noise | my rank | Codex (blind) rank |
|------|------------------------|------------------|---------------------|----------------|-------|---------|--------------------|
| **Fable**  | **1.00** (5/5) | **1.00** | **YES (only tier)** | most (incl. subtle dead-code authz) | some | **1** | **1** |
| Opus   | 0.824 (missed C3) | 0.80 | no | int(exp) crash, secret | cleanest | 2 | 2 |
| Sonnet | 0.824 (missed C3) | 0.80 | no | refresh, secret | **retracted bogus `hmac.new` claim** | 3 | 3 |

## My scorer vs Codex — CONVERGE this time
Both rank **Fable #1, Opus #2, Sonnet #3.** (Contrast Stage A, where they diverged.) Convergence
of an objective scorer + an independent cross-vendor grader raises confidence in the direction.

## What actually differentiated the tiers
- **All three caught both CRITICAL defects** (C2 priv-esc, C5 fail-open) and the timing + spec-drift.
  So for severe-and-detectable holes, even Sonnet sufficed.
- **The gap was on the SUBTLE high-severity reasoning defects:** only Fable caught C3 (the TOCTOU
  re-read) and the unseeded tautological dead-authz-check (line 50). These need control-/data-flow
  reasoning, not pattern-spotting.
- Sonnet's signal-to-noise took a hit (a confused "hmac.new does not exist" critical it retracted
  mid-finding).

## Decision: GO on Fable for complex / security / concurrency audits
Fable's edge is real and cross-validated — but specifically on *subtle* defects on *complex* code,
not on the critical-obvious ones (which everyone catches).

## Caveat
N=1 per cell. Stochastic — Sonnet's hallucination and Fable's TOCTOU catch might not recur. The
signal is coherent and cross-graded, and matches prior research (stronger reasoner wins on hard
reasoning), so the DIRECTION is trustworthy; exact magnitudes are not.
