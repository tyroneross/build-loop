<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->
# Backlog re-validation — 2026-08-21

`.build-loop/backlog/` is gitignored, so a disposition written into an item file dies
at clone. This file is the tracked record.

## Three of the items opened this session were already resolved

Each was closed by a later commit that never wrote back to the item, so all three kept
reading as open work.

| Item | Filed | Closed by | Evidence |
|---|---|---|---|
| `BUIL-CI-kz6m8c40ch2at` — pre-push gate red on a green repo | — | `85e2ee8` | Both failing default-gate tests shared one root cause in `tests/`; `scripts/test_prepush_test_gate.py` passes 47 at HEAD |
| `BUIL-MODEL-RESOLUTION-kynysz4f4852m` — resolver ignores `modelOverrides` | 2026-07-28 | `9bcf138` (2026-07-30) | Overrides are read before the in-tier walk and outrank it; see below |
| `phase-6-learn` non-atomic `_candidates.jsonl` write | — | `1d63c71` | Fixed this session; the filed defect UNDERSTATED it — see below |

## The model-resolver ticket's mechanism was wrong

It claimed the in-tier availability walk runs before the override read, so an override
is discarded whenever any tier model is reachable. Measured at HEAD with opus available:

```
modelOverrides.frontier = fable           -> model=fable, source=config    HONORED
modelOverrides.frontier = claude-sonnet-5 -> skipped: below-floor          correct guard
modelOverrides.frontier = gpt-5.6-sol     -> skipped: unavailable          correct guard
```

Overrides outrank the walk. The two non-selections are the documented guards, and both
are recorded in `resolution_path`, so neither is silent:

```json
[{"model": "gpt-5.6-sol", "skipped": "unavailable"}, {"model": "opus", "selected": true}]
```

Regression coverage already exists —
`scripts/test_model_resolver.py::test_frontier_override_to_thinking_model_is_allowed`
probes with `gpt-5.6-terra` rather than `opus` precisely because opus is what the walk
would return anyway, so only an honored override can produce that result.

**Had I fixed the ticket as written, I would have reordered working code.**

## The candidates.jsonl ticket UNDERSTATED its defect

Filed as "interleave and corrupt/duplicate rows". The dedup read and the
read-modify-write were both unlocked, so two concurrent Stop hooks each read the old
file and the second write REPLACED the first — lost rows, not interleaved ones.
Fixed in `1d63c71`.

## Staleness exposure

Age of the 55 items still open:

| age | count | share |
|---|---:|---:|
| 0–7 days | 8 | 15% |
| 8–30 days | 31 | 56% |
| 31–60 days | 16 | 29% |

85% are more than a week old, which is long enough for the referenced code to have
moved. Three of three items opened this session were mis-stated or already closed.

**Re-validate a backlog item against current code before working it.** Reading the
ticket is not verification: one was closed, one described a mechanism that does not
exist, and one described the wrong failure. Unlike `self_missing_test` findings, these
are prose and cannot be re-validated mechanically — the check is manual, and it is
cheaper than the work it prevents.
