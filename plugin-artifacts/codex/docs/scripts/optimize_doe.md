# optimize_doe.py

**Purpose:** Generate Design of Experiments (DOE) matrices and fit OLS effects to measured results, so multi-factor optimization can recover main effects and interactions in a small fixed number of runs instead of a combinatorial explosion.

## What problem does this solve?

Single-variable greedy optimization (the previous build-loop default) works well when there's exactly one knob to turn. With two or more knobs, it falls apart in two ways:

1. **It can't see interactions.** Suppose `BATCH_SIZE` and `WORKERS` jointly determine throughput, but the right value of one depends on the value of the other. A greedy loop that varies one at a time, holding the other fixed, finds a local optimum that the joint optimum dominates. The loop never sees the interaction because it never tested the (high, high) corner.
2. **It costs O(2^k) runs to brute-force.** With 8 candidate factors, a full brute force is 256 runs. Most of that work is wasted because most factor pairs don't interact and most factors don't matter.

Design of Experiments solves both problems simultaneously. A factorial or fractional-factorial matrix tests every factor at every level in a balanced, orthogonal arrangement. Orthogonality means the effect of factor A can be computed independently of factor B, so a small number of runs (8, 12, sometimes 16) gives unbiased estimates of every main effect and many interactions. This is the standard technique in industrial process optimization (Box, Hunter, Hunter 2005) and machine-learning hyperparameter sweeps when full grids are too expensive.

## How it works (algorithm)

### Full factorial (2^k)

A full factorial design tests every combination of `±1` levels across `k` factors. The matrix has `2^k` rows and `k` columns, with each row a unique sign pattern. For `k=3`:

```
    x1 x2 x3
1: -1 -1 -1
2: +1 -1 -1
3: -1 +1 -1
4: +1 +1 -1
5: -1 -1 +1
6: +1 -1 +1
7: -1 +1 +1
8: +1 +1 +1
```

Properties: every column sums to zero (balance), the inner product of any two distinct columns is zero (orthogonality), and every interaction up to `k`-way is estimable. Cost is exponential in `k`, so this is practical only up to `k=3` (8 runs) or `k=4` (16 runs) in build-loop's setting. The implementation just enumerates the 2^k binary numbers and maps `0→-1`, `1→+1`.

### Fractional factorial (2^(k-p))

When `k` is too large for full factorial, a fractional factorial uses a generator string to build a smaller matrix that aliases higher-order interactions onto main-effect columns. The generator `"a b c ab ac"` says: column 4 is the elementwise product of columns 1 and 2 (alias `D = AB`); column 5 is the product of columns 1 and 3 (alias `E = AC`). The result is a Resolution III or IV design — main effects are estimable assuming higher-order interactions are negligible, which is the standard "sparsity-of-effects" assumption in DOE.

Build-loop's implementation parses the generator string, resolves each generator to an elementwise product, and stacks the resulting columns. It supports up to 7 factors in 8 runs (R-III) and is verified column-equivalent to pyDOE3's `fracfact` implementation. The math:

```
Given base columns A, B, C (from a 2^3 full factorial),
  generator "a b c ab ac" yields columns:
    A = column 1
    B = column 2
    C = column 3
    D = A*B (elementwise)
    E = A*C (elementwise)
This is a 2^(5-2) Resolution III design in 8 runs.
```

### Plackett-Burman (12-run screening)

When `k ≥ 8`, both full and fractional factorial become unwieldy. Plackett-Burman designs are screening matrices: they fit `N-1` factors in `N` runs (so 12 runs handles up to 11 factors). They're constructed from a Hadamard matrix's first row applied cyclically. For PB-12:

```
First row: + + - + + + - - - + - 
Each subsequent row is a cyclic shift of the previous row.
The final row is all -1.
```

Properties: orthogonal main-effect columns, but no clean interaction estimates (interactions alias onto multiple main effects in complex ways). This is for **screening** — identifying the "vital few" factors out of many candidates, not for estimating joint effects. Once you've screened down to 2-4 important factors, you'd run a full or fractional factorial on those.

### OLS effects fitting

Given a design matrix `X` (with a column of 1s prepended for the intercept) and a vector `y` of measured responses, the effects are computed by ordinary least squares:

```
β = (X^T X)^(-1) X^T y
```

In Python this is `numpy.linalg.lstsq(X, y)`. When the design is orthogonal (every factorial design is), `X^T X` is diagonal and the OLS estimates collapse to simple half-differences:

```
effect_i = (mean(y where x_i = +1) - mean(y where x_i = -1))
```

Orthogonality is what makes this so cheap and what makes the effects interpretable. Each `β_i` measures the change in `y` per unit change in `x_i`, with no confounding from the other factors.

For `k ≤ 3` (full factorial only), the implementation also fits two-way interactions by appending columns of pairwise elementwise products to `X`. R² is reported when the design is non-saturated (more rows than estimable parameters); for saturated designs (Plackett-Burman, R-III with all main effects), R² is always 1.0 and is omitted from output.

### Why stdlib + numpy is sufficient

The full DOE pipeline (generate, analyze, route by k) requires:
- Boolean enumeration (full factorial): pure Python.
- Elementwise sign products (fractional factorial generators): numpy.
- Cyclic Hadamard construction (Plackett-Burman): hardcoded 12-row first-row, numpy roll.
- OLS fit: `numpy.linalg.lstsq`.

There's no need for `scipy.optimize` or `pyDOE3`. The math is closed-form. We verified empirically against pyDOE3 1.6.2 that all five canonical designs (2^3 full, 2^4 full, 2^(5-2), 2^(7-4), PB-12) produce equivalent matrices: exact match for fractional, row-permutation match for full (since enumeration order differs), sign-equivalent column match for Plackett-Burman.

## Inputs and outputs

The script has three subcommands:

### `generate`
- **Inputs:**
  - `--factors`: JSON list of `[{"name":"x", "low":1, "high":5}, ...]` or `[{"name":"x", "levels":[a, b]}, ...]`. Either a literal JSON string or a path to a JSON file.
  - `--design`: `auto` (route by k), `full`, `fractional`, or `pb`.
  - `--seed`: integer for randomizing run order.
- **Outputs:** stdout JSON with `design`, `factors`, `matrix` (raw ±1 array), `run_order` (randomized indices), and `runs` (each run's concrete factor values mapped from ±1 levels).

### `analyze`
- **Inputs:**
  - `--design`: path to the JSON file from `generate`.
  - `--results`: path to a JSONL file with one `{"run_id": int, "value": float}` per line.
  - `--direction`: `lower` (minimize) or `higher` (maximize).
- **Outputs:** stdout JSON with `summary` (design type, n_runs, n_factors, r²), `ranked_effects` (sorted by absolute effect size), `best_run` (index of the best measured run), `best_value`, `direction`, and `best_factors` (concrete factor values at the best run, when the design file has them).

### `detect`
- **Inputs:** an integer `factor-count`.
- **Outputs:** stdout JSON `{factor_count, design}` indicating which design type would auto-route for that k. No matrix is generated.

## Worked example

Optimize three factors (batch_size, retries, workers) for a metric where lower is better:

```bash
# Step 1: define factors
cat > factors.json <<EOF
[
  {"name": "batch_size", "low": 16, "high": 64},
  {"name": "retries",    "low": 1,  "high": 5},
  {"name": "workers",    "low": 2,  "high": 8}
]
EOF

# Step 2: generate matrix
python3 scripts/optimize_doe.py generate --factors factors.json --design auto --seed 1 \
  > design.json
# design.json contains 8 runs (full factorial for k=3), randomized run_order

# Step 3: for each run in run_order, apply runs[i]._factors to the codebase,
#         measure the metric, and append to results.jsonl:
echo '{"run_id": 0, "value": 12.4}' >> results.jsonl
echo '{"run_id": 1, "value":  8.1}' >> results.jsonl
# ... etc, all 8 runs

# Step 4: analyze
python3 scripts/optimize_doe.py analyze --design design.json --results results.jsonl --direction lower
```

Output (truncated):

```json
{
  "summary": {"design_type": "full", "n_runs": 8, "n_factors": 3, "r2": 1.0, "intercept": 9.6},
  "ranked_effects": [
    {"term": "batch_size", "effect": -3.2, "kind": "main"},
    {"term": "workers",    "effect": -1.8, "kind": "main"},
    {"term": "retries",    "effect":  0.4, "kind": "main"},
    {"term": "batch_size:workers", "effect": -0.9, "kind": "interaction"}
  ],
  "best_run": 7,
  "best_value": 5.2,
  "direction": "lower",
  "best_factors": {"batch_size": 64, "retries": 5, "workers": 8}
}
```

Reading: batch_size dominates; the (high, high, high) corner is best; there's a small interaction between batch_size and workers.

## Edge cases and known limits

- **Saturated designs:** when `n_estimable_params == n_runs`, R² is always 1.0 and is mathematically uninformative. The implementation suppresses interactions in saturated cases (k ≥ 4 in fractional, all PB) so the reported main effects aren't aliased with interactions you'd want to see separately.
- **Continuous factors at non-binary levels:** the design is two-level; if you want to test three levels of a factor, you need a Box-Behnken or central-composite design, which build-loop doesn't ship. The escape hatch: run two separate two-level studies straddling the suspected optimum.
- **Run order not enforced:** `run_order` is a suggestion. If the user's loop applies runs in matrix order, the analysis is unaffected. Randomization protects against time-dependent confounders (the metric drifts over the course of the experiment).
- **Categorical factors:** supported via the `levels: [a, b]` form, but only for two-level categoricals. A four-way categorical needs to be encoded as two binary factors.

## Verification / how do we know it works

`scripts/test_optimize_doe.py` exercises:
- Design generators produce correctly-shaped matrices with perfect orthogonality (Gram matrix is diagonal).
- OLS recovers known ground-truth coefficients within numerical tolerance for noiseless data and within 0.2 for noise σ=0.1.
- The auto-routing picks the correct design type for each k.
- The full CLI pipeline (generate → analyze) produces sensible ranked findings on synthetic data.
- When `pyDOE3` is installed, our matrices are equivalent to pyDOE3's for all five canonical designs (verified 2026-05-03 against pyDOE3 1.6.2).
- The DOE→autoresearch handoff: `analyze` emits `best_factors`; `optimize_loop.py --init --baseline-config` consumes it.

## Related files

- `skills/optimize/SKILL.md` — describes when to invoke each subcommand
- `scripts/optimize_loop.py` — the autoresearch loop that can take a DOE handoff via `--baseline-config`
- `scripts/optimize_suggest_factors.py` — proposes factor candidates if the user hasn't supplied them
- `scripts/test_optimize_doe.py` — tests for this script
- `docs/scripts/optimize_suggest_factors.md` — companion doc
- `docs/scripts/test_optimize_doe.md` — test-script companion doc
