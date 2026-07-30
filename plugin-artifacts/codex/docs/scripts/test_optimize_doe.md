# test_optimize_doe.py

**Purpose:** End-to-end test harness for the DOE pipeline (`optimize_doe.py`) and its handoff into the autoresearch loop (`optimize_loop.py`). Asserts mathematical correctness of design generators, accuracy of OLS effects, CLI round-trip behavior, and equivalence with pyDOE3 (when installed).

## What problem does this solve?

Statistical code is high-stakes because errors are silent. A wrong sign on a fractional-factorial generator alias would silently confound main effects with two-way interactions, and the analyzer's output would *look* correct (effects ranked, R² computed, best run identified) while actually telling the user the wrong story. There's no Python exception, no assertion error in the math itself — just systematically wrong recommendations.

This test harness exists so that mathematical correctness is asserted up front, not discovered later in a misleading optimization result. It uses three complementary verification strategies:

1. **Algebraic invariants** (orthogonality, balance, shape) — properties that any correct design must satisfy. Easy to assert, catches a wide class of bugs.
2. **Round-trip on synthetic data with known coefficients** — generate y from a known linear model, verify OLS recovers the coefficients within tolerance. Catches errors in the analyzer that algebraic invariants miss.
3. **Equivalence to pyDOE3** — when pyDOE3 happens to be installed, our matrices match pyDOE3's. This is a third-party cross-check that's skipped by default (build-loop is stdlib + numpy only) but provides confidence when available.

A fourth integration test class (`HandoffToAutoresearchTests`, added in v0.8.2) exercises the DOE → autoresearch handoff: generate, analyze, then `optimize_loop.py --init --baseline-config` and assert the experiment.json embeds the correct doe_baseline block.

## How it works (algorithm)

### Direct unit tests on the design generators

For each generator (full factorial 2^k, fractional factorial via generator string, Plackett-Burman 12), the test asserts:

- **Shape:** `(2^k, k)` for full factorial, `(2^(k-p), k)` for fractional, `(12, 11)` for PB-12.
- **Element values:** every entry is exactly +1 or -1, no other values.
- **Orthogonality:** `X^T X` is diagonal (off-diagonal entries are zero, within numpy tolerance). Orthogonality is what makes OLS effects independent and uncorrelated, so this is the most important invariant.
- **Balance:** every column sums to zero. Implied by orthogonality but cheap to assert separately.

### Effects accuracy tests

For each design type, the test:
1. Synthesizes a known linear model `y = β0 + β1*x1 + β2*x2 + ... + interaction terms`.
2. Generates the corresponding design matrix.
3. Runs `fit_effects(design, y)`.
4. Asserts the recovered coefficients match the truth within tolerance.

For noiseless data, tolerance is `places=8` (essentially exact). For noisy data (σ=0.1), tolerance is `delta=0.2`. The Plackett-Burman screening test uses a sparse-effects scenario (only 3 of 11 factors active) and asserts the top 3 effects by absolute size are exactly the active factors.

### CLI round-trip test

Constructs a 3-factor scenario, calls the `generate` and `analyze` subcommands as subprocesses (not via Python imports — exercises the actual user-facing CLI), and asserts the top-ranked effect matches the synthetic ground truth.

### pyDOE3 equivalence test

Uses `try: import pyDOE3 except ImportError: skipTest`. When pyDOE3 is present, generates the same designs both ways and asserts equivalence (exact for fractional, row-permutation for full, sign-equivalent column for PB).

### Handoff integration test (v0.8.2)

Three subtests:
1. `analyze` emits `best_factors`. Generate a 2-factor full factorial, synthesize results where the (high, high) corner minimizes y, run analyze with `--direction lower`, assert the output JSON contains `best_factors: {batch_size: 64, retries: 5}`.
2. `optimize_loop.py --init --baseline-config` consumes the effects.json. Init a throwaway git repo, run the full DOE pipeline, then init the autoresearch loop with the baseline-config flag, assert experiment.json embeds `doe_baseline.factors`.
3. Legacy effects.json (without `best_factors`) is rejected with a clear error message and non-zero exit.

## Inputs and outputs

- **Inputs:** none. The test resolves paths to `optimize_doe.py` and `optimize_loop.py` via its own location.
- **Outputs:**
  - stdout: standard unittest output. 18 tests total in v0.8.2 (15 pre-handoff, 3 handoff).
  - exit code: 0 on full pass, non-zero on first hard failure. pyDOE3 test skips silently when the library isn't installed.

## Worked example

```bash
python3 scripts/test_optimize_doe.py
```

Output (v0.8.2):

```
test_generate_then_analyze (...) ... ok
test_fracfact_orthogonal (...) ... ok
test_fracfact_shape (...) ... ok
test_full_factorial_orthogonal (...) ... ok
test_full_factorial_shape (...) ... ok
test_pb_12_orthogonal (...) ... ok
test_pb_12_shape (...) ... ok
test_fractional_with_noise (...) ... ok
test_full_factorial_no_noise (...) ... ok
test_pb_screening_identifies_vital_few (...) ... ok
test_analyze_emits_best_factors (...) ... ok
test_optimize_loop_init_consumes_baseline_config (...) ... ok
test_optimize_loop_init_rejects_missing_best_factors (...) ... ok
test_levels_array (...) ... ok
test_low_high_mapping (...) ... ok
test_equivalence_when_pydoe3_present (...) ... skipped 'pyDOE3 not installed (expected — build-loop is stdlib+numpy)'
test_build_design_dispatch (...) ... ok
test_select_design (...) ... ok

----------------------------------------------------------------------
Ran 18 tests in 1.131s

OK (skipped=1)
```

When a generator string is wrong (regression scenario): the orthogonality test fails immediately with a numpy assertion showing the off-diagonal entries that are non-zero. The user sees which columns are aliased incorrectly.

## Edge cases and known limits

- **Tolerance choice for noise tests:** σ=0.1 with `delta=0.2` was chosen so the test is reliable across numpy random seeds and platforms. Tighter tolerance produces flaky tests; looser tolerance lets bigger errors through. The current setting catches sign errors and significant magnitude errors, which is what matters.
- **PB-12 screening test:** uses a deterministic random seed (rng=11) chosen so the synthetic noise doesn't accidentally rank a true-zero factor in the top 3. With a different seed the test could be flaky; the seed is fixed for reproducibility.
- **pyDOE3 equivalence is opt-in:** because pyDOE3 isn't a build-loop dependency. Developers who care can `pip install pyDOE3` to activate the cross-check. CI for build-loop does NOT install it.
- **Handoff test creates a real git repo:** `optimize_loop.py --init` needs a baseline commit to record. The test uses `tempfile.TemporaryDirectory` and cleans up.

## Verification / how do we know it works

The test harness was developed before the DOE math itself was fully written; bugs in early `optimize_doe.py` revisions surfaced as orthogonality failures and noise-recovery failures. Each was fixed before merge. The pyDOE3 equivalence claim (documented in `KNOWN-ISSUES.md`) was first established by running this test with pyDOE3 installed; reproducible on any machine that does `pip install pyDOE3` and re-runs the test.

The handoff integration test was added at the same time as `optimize_loop.py`'s `--baseline-config` flag (v0.8.2). It caught one regression during development: an off-by-one in the `best_run` index lookup that was silently using the wrong row of the runs array.

## Related files

- `scripts/optimize_doe.py` — primary subject under test
- `scripts/optimize_loop.py` — secondary subject (handoff test)
- `docs/scripts/optimize_doe.md` — companion doc explaining the math
- `docs/scripts/optimize_suggest_factors.md` — companion doc for the candidate scanner
- `KNOWN-ISSUES.md` — empirical pyDOE3 equivalence verification record
- `skills/optimize/SKILL.md` — describes the DOE → autoresearch handoff
