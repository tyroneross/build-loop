# Blind A/B: which tier in which role? + finalized recommendations

> **Status:** experiment design + recommendations of record (2026-06-10). Companion to
> `standing-roles-trio.md`. The trio *mechanism* (advisor agent, dispatch ladder, ledger)
> is built as no-regret infrastructure; this doc sets the *parameters* (which tier in which
> role) empirically rather than by assertion.

---

## Part 1 — Recommendations of record (the complexity-graduated spine)

Settled across the 2026-06-10 design thread + the deep-research pass. These are the design's
position; Part 2 is how we test the one parameter the evidence can't settle (Fable-authors vs
Opus-authors-Fable-checks).

**Principle:** *Checking catches errors cheaply; generating adds insight expensively.* Spend the
Frontier tier where the artifact is small but the decision compounds — plan, check, steer — and
keep it off token-heavy generation (building) except as last resort.

**The spine (user's refinement, adopted — it's the more evidence-backed default):**
Opus orchestrates **and authors the plan**; Fable **independently assesses** and injects; Fable's
*depth* scales with complexity; `.build-loop/` run artifacts + build-loop-memory carry context
across handoffs so nothing is lost.

**Complexity-graduated planning ladder:**
| Stakes | Who authors | Fable's role | Why |
|--------|-------------|--------------|-----|
| Low (bounded fix, ≤1 file) | Orchestrator inline (Opus/session) | none | stakes don't justify cost |
| Medium (multi-file, schema, API) | **Opus** | **assess the draft** + inject fixes | cheap safety check; research-backed (external > self) |
| High (architecture, irreversible, security) | **Opus drafts** | **independently derive from source, then reconcile** | checking can't add a better decomposition; independence catches Opus's blind spots |
| Failure / stuck | — | **steer (re-plan) or take over the chunk** | verifier-signal triggered, last resort |

**Independence is mechanical, not nominal.** "Fable reads Opus's plan" is *anchored* review — it
inherits Opus's framing. Real independence requires giving Fable the **source (goal + requirements
+ memory)**, not just Opus's draft, so at high stakes it *derives in a fresh context* and reconciles.
This is the user's own filed lesson (*solicited peer review is not independent*) + the research
context-separation finding, converging.

**Context substrate (mitigates the #1 risk — multi-agent handoff loss):**
- Within-run handoff (Opus's plan + reasoning → Fable) → `.build-loop/` artifacts (`plan.json`,
  `agent-ledger.jsonl`). Fable reads these *fresh* — same move gives both the handoff AND the
  context-separation that makes review independent.
- Cross-run durable (decisions, lessons, prior architecture calls) → **build-loop-memory**, read at
  Phase 1 Assess. Fable's past assessments + the ledger accrue and inform the next run.

**Execution + verification (unchanged, research-backed):** Sonnet builds → Opus on hard chunks →
Fable takes over only on demonstrated repeated failure. Verification stays a **multi-specialist Fable
panel** (single judge is unreliable — bias + overconfidence), with chain-of-thought, gated on
**objective verifier signals, never self-reported confidence.**

---

## Part 2 — The blind A/B

### What we're actually testing
The evidence firmly backs *Fable checks* and *separate-critic > self-critique*. It does **not**
settle: (a) does an advisor help enough to pay for; (b) must the advisor be Fable or is Opus enough;
(c) does orchestrator tier matter; (d) does judge tier matter. Those are parameters — measure them.

### Conditions (OFAT from a baseline — isolates each swap cheaply)
Full factorial (advisor×orch×judge = 12 cells) is too costly for a first signal. One-factor-at-a-time
from the current-build-loop baseline answers every question the user listed with 5 conditions:

| ID | Orchestrator | Advisor | Judge | Isolates |
|----|--------------|---------|-------|----------|
| **B0** (baseline = current) | Opus | none | Opus | status quo (the "A" arm) |
| **C1** | Opus | **Fable** | Opus | advisor value (vs B0) |
| **C2** | Opus | **Opus** | Opus | does advisor need to be Fable? (vs C1) |
| **C3** | Opus | none | **Fable** | judge-tier effect (vs B0) |
| **C4** | **Fable** | none | Opus | orchestrator-tier effect (vs B0) |

Contrasts: C1−B0 = advisor worth; C1−C2 = Fable-vs-Opus advisor; C3−B0 = judge tier; C4−B0 = orch tier.

### Toggle mechanism (deterministic)
Per-condition `.build-loop/config.json` `modelOverrides` (resolved by `scripts/model_overrides.py
--config <path>`), one config file per cell. The **agent-ledger** records the *resolved model id per
action* so each run self-certifies which condition actually fired — closing the "Fable barely fired
and I couldn't see it" gap that motivated this whole effort.

### Tasks & metrics — two batches
**Batch 1 (objective, first — tests advisor/judge error-catching):** seeded-defect detection (CCR
method). Inject a fixed set of known defects into a repo snapshot; each condition's advisor/judge
reviews; measure **detection F1 on the known set**. Fully objective, blind-gradeable. Directly tests
the core Frontier-tier value (catching compounding errors).

**Batch 2 (ecological, second — tests plan/build quality):** same feature spec per condition →
produce plan + build → measure **tests-passing %, # review findings that survive scrutiny, rework
cycles**. Tests the Fable-authors-vs-Opus-authors-Fable-checks question (the one inference the
research couldn't settle).

### Blinding protocol
Grader (a fresh-context Fable agent and/or the user) sees outputs **stripped of condition labels, in
randomized order**, and scores quality / counts caught-defects without knowing the model-config or
which items are seeded. The ledger holds the true mapping; revealed only after grading. This is the
blind in "blind A/B."

### Repo field (from the registry — 72 repos; first batch picks Python repos with test suites)
Candidates with clean state + real test surfaces, good for seeded-defect injection + pytest:
`build-loop` (77+ tests), `NavGator`, `prompt-builder`, `research-plugin`, `api-registry`,
`agent-builder`. First batch: 3–4 of these. (User may point at a preferred field; the harness is
repo-agnostic.)

### Analysis (honest about power)
Small N first batch = **directional, not conclusive** — report effect direction + magnitude, not
significance claims. The ledger persists, so runs accumulate toward power over time. Pre-registered
decision rule (set before grading to avoid post-hoc rationalization):
- Adopt **Fable advisor** as default only if C1 catches materially more *critical* defects than both
  B0 (no advisor) and C2 (Opus advisor), at acceptable cost.
- Keep advisor **Opus** (or none) if C2 ≈ C1 (Fable advisor adds no edge over Opus advisor).
- Promote **Fable judge** (C3) / **Fable orchestrator** (C4) only on a clear, replicated edge.

### No-regret vs gated-on-results
- **No regret (built regardless — already landing on this branch):** the agent-ledger (instrument),
  `dispatch_tier: frontier` enum, the advisor agent + dispatch ladder *mechanism*, doc honesty.
- **Gated on this A/B (do NOT hard-code until data):** which tier the advisor/judge/orchestrator
  *defaults* to; whether plan-critic *blocks* vs advises; the exact complexity thresholds. These are
  parameters the experiment sets.

### Status
Trio mechanism build: in flight on `experiment/advisor-judge-trio` (ledger + advisor + ladder
committed; verification in progress). A/B harness + Batch 1: next, after the mechanism build's
verification lands.
