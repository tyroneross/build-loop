# DoE result: does prompting STRUCTURE substitute for model TIER? (hard coding task)

**Design:** 2³ full factorial — tier(Sonnet/Fable) × plan_first(off/on) × self_verify(off/on).
**Task:** implement an arithmetic expression evaluator (tokenizer+parser, eval/ast forbidden).
**Response:** correctness = hidden 20-case suite (15 value + 5 must-raise). **N=1/cell.**

## Scores
| | base | +self_verify | +plan_first | +plan_first+self_verify |
|--|------|--------------|-------------|--------------------------|
| **Fable**  | 20 | 20 | 20 | 20 |
| **Sonnet** | 20 | 20 | **19** | **17** |

Fable: perfect in every cell. Sonnet: perfect UNLESS told to plan-first — then it over-elaborated
the grammar/regex and shipped precedence (`-2**2`) and tokenizer (`**` split into `*`,`*`) bugs.
Self-verify did NOT rescue it (one cell claimed to "trace cases" but never ran its code).

## DoE OLS effects (coded ±1, higher=better; R²=0.94)
- tier: **+0.50** (Fable)
- plan_first: **−0.50** (hurts)
- tier × plan_first: **+0.50** (the harm is cheap-tier-only — Fable is immune)
- plan_first × self_verify: −0.25 · self_verify: −0.25

## What this says (and how it differs from prior OFAT thinking)
1. **Prompting structure did NOT substitute for tier here — plan-first actively BACKFIRED**, and only
   for the cheap tier (the +0.50 interaction exactly cancels plan_first's harm for Fable). I had been
   leaning toward "cheap + structure ≈ expensive"; the DoE says the opposite on this task.
2. **Tier is the robust lever**: Fable was correct regardless of prompt structure. The cost question
   "can structure let me use the cheap model" gets a NO here.
3. **The DoE earned its keep**: it surfaced a tier×structure INTERACTION as large as the main effects.
   OFAT (vary one factor) would have mis-attributed plan_first's harm as a flat negative, missing that
   it's conditional on tier.

## Caveats (loud)
**N=1/cell, 8 runs, saturated model → R²=0.94 but ZERO residual df, no significance test.** The whole
signal rests on 2 Sonnet bugs that could be stochastic. "plan-first hurts" may be a real over-
elaboration effect in weaker models OR noise. Single task, single domain. This is a **method
demonstration with a directional result**, not a significant finding. Harden: ≥3–5 replicates/cell,
2–3 more tasks, add Opus as a 3rd tier level, measure tokens for the cost axis.
