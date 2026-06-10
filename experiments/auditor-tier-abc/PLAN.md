# Experiment: Auditor/Judge tier A/B/C — Sonnet vs Opus vs Fable

**Question:** Does the auditor/judge role need Fable, or is Opus/Sonnet enough — and does the
answer depend on task complexity? If cheap tiers catch simple defects and only Fable catches
subtle ones, route auditor tier *by complexity* (cost-optimal). The ledger + this experiment
turn that hypothesis into data.

**Metric:** seeded-defect detection. Each artifact has a known set of planted defects
(`manifest.json`). Each tier audits the *same* artifact with the *same* prompt. We score
**recall by severity** (did it catch the planted defect?), with extra/false findings adjudicated.

**Conditions (A/B/C):** auditor on **Sonnet** (A) · **Opus** (B) · **Fable** (C). Identical prompt;
only the model varies. The ledger records the resolved model per run (self-certifies the condition).

**Complexity range:**
- **Simple** (`artifacts/simple_cart.py`): 4 local, classic bugs (boundary, empty-guard, op-order,
  mutable default). A competent cheap tier should catch most.
- **Complex** (`artifacts/complex_auth.py`): 5 reasoning-heavy defects (timing attack, privilege
  escalation, TOCTOU, spec/impl drift, fail-open). Need security/concurrency/spec reasoning — where
  the Fable premium should pay off, if it ever does.

## Staged execution with go/no-go/hold gates

> "Pre-plan sections where you stop and determine which was best." Each gate: I score → **Codex
> independently grades the same outputs (cross-vendor, blind to my scoring)** → I reconcile (NOT
> bound to Codex) → record a **go / no-go / hold** decision before proceeding.

- **Stage A — Simple task, 3 tiers.** GATE 1 question: *do the tiers differ at all on simple
  defects?* If Sonnet catches everything → **no-go on premium for simple work** (route simple audits
  cheap). If they differ → note the gap.
- **Stage B — Complex task, 3 tiers.** GATE 2 question: *does Fable catch the critical defects
  Sonnet/Opus miss?* This is the crux — the security/drift defects are where tier should matter.
- **Synthesis.** Recommendation on auditor tiering, likely complexity-conditional. Honest about
  N=1-per-cell → directional, not conclusive; the harness accrues runs toward power.

**go / no-go / hold taxonomy (per gate):**
- **GO** — a clear, consistent signal; proceed / adopt.
- **NO-GO** — a clear negative (e.g., premium adds nothing here); stop / route cheap.
- **HOLD** — inconclusive or my-vs-Codex disagreement; needs more runs before deciding.

## Independence discipline
- Defects are **objective ground truth** (seeded), so grading is measurable, not opinion — this is
  what makes Codex a real independent check, not anchored agreement (the "solicited review ≠
  independent" trap).
- Codex grades **blind to which tier produced which output** and blind to my scores.
- I reconcile and decide; Codex is an opinion, not the verdict.

## Status
Stage A dispatched. Gate 1 pending adjudication + Codex.
