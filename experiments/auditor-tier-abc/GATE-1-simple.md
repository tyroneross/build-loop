# Gate 1 — Simple task (N=1 per tier, directional)

## Scores
| Tier | seeded weighted-recall | crit/high recall | unseeded real bugs found | extra/noise | my rank | Codex (blind) rank |
|------|------------------------|------------------|--------------------------|-------------|---------|--------------------|
| Opus   | **1.00** (S1,S2,S3,S4) | 1.00 | 0 | 1 | 1 | 2 |
| Fable  | 0.667 (missed S3)      | 0.50 | **2** (coupon-unused, avg-by-qty) | 5 | 2 (tie) | **1** |
| Sonnet | 0.667 (missed S1)      | 0.50 | 0 | 1 | 2 (tie) | 3 |

## My scorer vs Codex — the divergence IS the finding
- Seeded-recall (my metric) ranks **Opus #1** (perfect seeded coverage, cleanest signal).
- Total-real-defect recall (Codex's lens) ranks **Fable #1** (found 2 real bugs I didn't plant).
- Methodology lesson for Stage B: seeded-recall undercredits a deeper auditor that finds *unseeded*
  real defects. Credit unseeded reals explicitly going forward.

## Reconciliation (not bound to Codex)
The actionable conclusion converges across both metrics: **Fable did not cleanly outperform the
cheaper tiers on simple, bounded code** — it missed a seeded high-severity logic bug (tax-order)
that BOTH Opus and Sonnet caught, and added the most noise. There is no case here for paying the
Fable premium on simple audits. Opus was the cleanest; Sonnet caught the most important correctness
bug. Tier did not dominate.

## Decision: NO-GO on Fable for simple tasks
Route simple/bounded audits to Opus (or Sonnet). Reserve the Fable premium for where it might pay —
the complex, reasoning-heavy defects in Stage B. Proceeding to Stage B.

## Caveat
N=1 per cell; model outputs are stochastic. This is directional, not conclusive. The crux is the
Stage-B contrast: does Fable catch the critical security defects (priv-esc, fail-open, TOCTOU) that
cheaper tiers miss? That is the question the whole tiering rule hinges on.
