<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Systemic RCA Fixtures

This directory holds JSON reports for `scripts/systemic_rca_eval.py`.

Minimum report fields:

- `plain_language_failure`
- `why_it_happened`
- `failure_map`
- `system_control_failure`
- `failure_classification`
- `technical_details.evidence`
- `pruned_causes` or `pruned_branches`
- `tradeoffs`
- `impact`
- `prevention_control`

Use `sample-systemic-report.json` as the shape reference. `golden-corpus.json` contains 10 positive examples across common build-loop failure classes. `negative/shallow-actor-blame.json` is the regression fixture that proves actor-blame, jargon-first explanations, and shallow failure maps do not pass.

```bash
python3 scripts/systemic_rca_eval.py docs/test-fixtures/systemic-rca/*.json --score-only
```

The DOE factors and generated 8-run fractional-factorial design live under `doe/`:

```bash
python3 scripts/optimize_doe.py generate \
  --factors docs/test-fixtures/systemic-rca/doe/systemic-rca-factors.json \
  --design auto \
  --seed 20260524
```

Build run packets from the tracked DOE matrix:

```bash
python3 scripts/systemic_rca_doe.py build-packets \
  --design docs/test-fixtures/systemic-rca/doe/systemic-rca-doe.json \
  --corpus docs/test-fixtures/systemic-rca/golden-corpus.json \
  --outdir .build-loop/optimize/systemic-rca-packets
```

After each variant writes RCA outputs as `run-00.json`, `run-01.json`, and so on, turn them into `optimize_doe.py` results:

```bash
python3 scripts/systemic_rca_doe.py score-results \
  --results-dir .build-loop/optimize/systemic-rca-results \
  --design docs/test-fixtures/systemic-rca/doe/systemic-rca-doe.json \
  --jsonl > .build-loop/optimize/systemic-rca-results.jsonl

python3 scripts/optimize_doe.py analyze \
  --design docs/test-fixtures/systemic-rca/doe/systemic-rca-doe.json \
  --results .build-loop/optimize/systemic-rca-results.jsonl \
  --direction higher
```

Known failure classes:

- `ambiguous-contract`
- `cache-drift`
- `context-packet-gap`
- `dependency-provenance-gap`
- `environment-misread`
- `evidence-gap`
- `missing-test-trigger`
- `multi-session-coordination-gap`
- `observability-gap`
- `runtime-smoke-gap`
- `scope-audit-gap`
- `test-fixture-gap`
- `ui-contract-gap`
- `warning-baseline-gap`
