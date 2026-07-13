<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
<!-- CONFIG: v1.0 | T2 | plugin(report-writeup) | Analytical | SCORE: 22/25 [A:5|C:4|Cs:5|D:4|Cp:4] -->

# Experiment / data-analysis results — reusable template + guide

One write-up shape for any analysis: A/B, DOE / full-factorial / fractional-factorial,
regression, backtest, ablation, regression-test. Fill the blanks; delete the Method
sub-lines that don't apply; never delete the honesty rails (n, direction legend,
certainty, "not computed — why").

---

## The template (copy below this line)

```markdown
# <test name> — results
<!-- headline: DECISION: <build X / don't / inconclusive> · certainty: <high|medium|low> · n=<N> -->

## Objective
- **Testing:** <the one thing under test>
- **Why:** <the recurring problem or the decision this informs>
- **Payoff if it succeeds (user-visible):** <what changes for the end user / consumer>
- **Decision this unblocks:** <what we do differently on a pass vs a fail>

## What we did
- **Analysis type:** <A/B | DOE full-factorial | fractional-factorial | regression | backtest | ablation | regression-test | other>
- **Method (1 paragraph):** <how it ran, start to finish>
- **Sample size n = <N>** (<unit: bugs / runs / requests / rows / sessions>)  ← never omit
- **Arms / factors** (keep the line that fits your type):
  - A/B: control = <...> · treatment(s) = <...>
  - DOE/factorial: factors × levels = <...> · runs = <...> · design = <full | fractional res IV | ...>
  - Regression: predictors = <...> · response = <...> · model = <OLS | logistic | ...>
  - Backtest: data + window = <...> · in/out-of-sample split = <...>
  - Ablation: component removed = <...> · baseline = <...>
- **Held constant (confounds managed):** <model, environment, inputs, seed, …>
- **Model/thinking identity** (required for model or agent comparisons):
  - `<arm>`: model `<provider/model/version>` · normalized level `<none|minimal|low|medium|high|xhigh|max|ultra|unknown>` · provider value `<exact label|null>` · mode `<single_agent|multi_agent|adaptive|unknown>`
  - Provenance: `<source_document|experiment_config|runtime_log|unknown>` · evidence `<URL + table/footnote OR exact command/config/log>`
  - Evidence kind: `<published_document|local_test>` · observed at `<ISO-8601>`
  - Mixed/unknown effort confound: `<none | explain why ranking is directional>`
- **Measurement:** <who/what scored, against what ground truth, and exactly how>
  - Blinding: <none | labels withheld | fully blinded> — <if you claim blinded, say what made it so; if a tell leaked, say so>

## Hypothesis
- **Predicted:** <outcome> **because** <mechanism / prior reason>
- **Pass means:** <what it implies> → **action:** <...>
- **Fail means:** <what it implies> → **action:** <...>
- **Pre-registered?** <yes + link | no> · decision rule fixed before running? <yes | no>

## Results

**Metric legend — state direction for EVERY metric (this is mandatory):**
| Metric | Measures | Direction | Scale |
|--------|----------|-----------|-------|
| <m1> | <...> | higher better / lower better | <0–2 · % · ms · count · $> |
| <cost> | effort/latency/$ | lower better (only counts when quality ties or wins) | <calls · s · $> |

**Data:**
| <arm / run> | Model ID | Thinking level / mode | Thinking provenance | <m1> | <m2> | <cost> |
|---|---|---|---|---|---|---|
| <...> | <provider/model/version> | <high / single_agent> | <runtime_log: path#line> | | | |

**Statistics — fill what you actually computed; for the rest write "not computed — <why>":**
- **Effect size:** <metric: value (e.g. Δ, Cohen's d, lift %)> | not computed — <why>
- **Statistical significance:** <test, p-value, CI> | **NOT COMPUTABLE — n=<N> too small (directional only)**
- **Correlation:** <vars: r> | not computed — <why>
- **Goodness of fit** (regression only): <R², adj-R², residual check> | n/a
- **Winner / direction:** <which, on which metrics>

## Interpretation
- **Certainty:** ✅ high | ⚠️ medium | ❓ low — <why, tied to n + method strength>
- **What it shows / does NOT show:** <scope of the claim>
- **Threats to validity / confounds:** <weak blinding (how), small n, self-selected subject, model substitution, single grader, …>
- **Unknowns:** <what could not be verified>
- **Honesty check:** every adjective here is earned by the method — no "fair / blind / significant / proven" unless the method above supports it.

## Next steps
- [ ] **Implement winner:** <...>  (or: do not implement — <why>)
- [ ] **Stronger re-test:** <what raises certainty — larger n, cleaner blinding, a real significance test, second grader>
- [ ] **New tests suggested:** <...>
- [ ] **Risks to monitor after rollout:** <...>
```

---

## Usage guide

1. **Pick one Method sub-line** for your analysis type; delete the others. The honesty
   rails — `n`, the direction legend, the statistics block, certainty — stay regardless
   of type.
2. **Direction legend is not optional.** A table of numbers is unreadable without "higher
   or lower better" per metric. Cost metrics (calls, time, $) are lower-better but only
   count as a win when quality ties or improves (Accuracy > Speed > Cost).
3. **When stats don't apply, say so explicitly** — never leave significance/correlation/
   fit blank or imply them. Rules of thumb: significance/correlation need roughly n ≥ 8–10+
   to mean anything; below that write "directional only (n too small)". Regression fit
   (R²) is n/a unless you actually fit a model. DOE reports main effects + interactions,
   not p-values, unless replicated.
4. **Earn every adjective.** Do not write "fair", "blind", "robust", "significant", or
   "proven" unless the Method section shows what made it so. If blinding leaked (e.g. the
   treatment's output format was a tell), record it as a threat to validity — don't claim
   blindness.
5. **Headline = decision + certainty + n.** A decision-maker should get "build it / don't /
   inconclusive", how sure, and on how much data, from the first line.
6. **Pre-register when the result will drive a real decision** — commit Objective +
   Hypothesis + decision rule before running, so the verdict can't be retrofit.
7. **Thinking level is part of the treatment.** Never collapse scores by model name when
   effort or mode differs. Preserve the source's exact label and a normalized level. For
   published rows, cite the page/table/footnote; for our tests, cite the effective runtime
   config or log. If the value is not reported, write `unknown` and name the confound.
   Model bake-offs carry these fields in each arm of the existing `abc-comparison/v2`
   observation artifact. Preserve that raw artifact: Benchmark Lab retention is a pending
   Lab-owned extension, and current ingest acceptance does not prove the fields survived.
