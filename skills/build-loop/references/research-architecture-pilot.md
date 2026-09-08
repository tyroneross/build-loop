<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Evaluate agent architecture on matched tasks

Use this optional procedure when the user requests an architecture or handoff
pilot. It reuses the existing benchmark, context snapshots and coordination
mechanisms. It does not create a team, change routing defaults, or authorize
model calls, new credentials, production mutations or additional repositories.

## Classify the bottleneck

Treat these shapes as hypotheses to test, not automatic agent-count rules.

| task_shape | Bottleneck | Baseline and possible experiment | Acceptance evidence |
|---|---|---|---|
| fact_lookup | Precise retrieval | One agent; improve source selection first | Correct answer and supporting source |
| direct_comparison | Consistent fields across entities | One agent vs bounded independent entity research | Same field definitions and per-field evidence |
| breadth_survey | Coverage | One agent vs bounded non-overlapping exploration | Frozen expected topics, source quality, duplicates |
| deep_synthesis | Dependent evidence | One owner retains the reasoning chain; test retrieval/context improvements | Every material conclusion supported by cited evidence |
| contested_topic | Conflicting evidence | Independent evidence check before experimenting with debate | Unresolved disagreements, source quality, judge independence |
| wide_collection | Many grounded cells | Compare whole-brief ownership with schema-bound partitions | Cell-level evidence, missing values, shared denominator |

For coding, use descriptive shapes such as `coupled_change`,
`independent_implementation`, or `interrupted_takeover`. New labels are allowed.
One agent should own a coupled change end-to-end as the initial baseline.
For parallel candidates, inspect both read/write dependencies and shared runtime
state: separate files or services alone do not establish independence. Reuse
Rally claims and data-plane worktrees when applicable. Keep one integration owner.
Reuse skills for procedures and domain references for facts; session identity
alone does not preserve knowledge.

## Change one dimension at a time

- **Context experiment:** freeze an interrupted repository and original brief.
  Compare the existing handoff with an evidence-linked structured handoff.
  Keep execution topology fixed. Record decisions retained, rediscovery,
  interventions and acceptance. Treat predecessor interpretations as claims
  to verify. Retain exact validation commands and source pointers.
- **Execution experiment:** keep the brief, context, tools, inputs, acceptance
  rubric and review policy fixed. Compare one implementer with an owner plus
  one bounded worker on eligible work. Count all worker, review and integration
  usage. A sequential reader/reviewer is a distinct treatment from parallel
  writers. Do not compare a well-scaffolded solo agent to an unscaffolded team.

Start with a small balanced selection of tasks, repeat and counterbalance order.
An initial 8–12 task pilot is exploratory; it is not a power calculation or
statistical proof. Fix acceptance tests before running either arm; where a judge
is needed, hide variant identity and randomize presentation order. Choose task-
specific checks for grounding, coverage, duplicate work and schema consistency.
Keep failures, timeouts and partial runs in the record. Define a common defect
observation window. Stop or narrow trials on safety failures; record the cause.

## Record evidence using the existing JSONL format

Keep receipts under `.build-loop/evals/`; keep full prompts, controls manifests,
source snapshots, outputs and test evidence beside them. Do not publish private
corpora, traces or the user's uploaded documents with the plugin.

Required existing fields: `task_id`, `variant`, `model`, `snapshot`, `passed`.
Pilot mode additionally requires:

- `trial_id`: matched repetition identifier, unique within each variant/task.
- `experiment_axis`: `context` or `execution`.
- `task_shape`: one consistent label per matched task.
- `controls_id`: immutable manifest identifier or digest covering input corpus,
  original task, model/version/effort, host/tool versions, total-system budget,
  acceptance rubric, review policy and observation window. Exclude only the
  declared treatment. Preserve the manifest; an equal label is not proof that
  real controls matched. Use `snapshot` for the exact frozen input state.
- `evidence_kind`: `live` for actual agent trials or `calibration` for synthetic
  harness checks. Never mark a deterministic fixture as a live agent result.

Optional observations: `duration_seconds` (complete task wall time including
review/integration), `user_interventions`, `lost_decisions`, `rework`,
`grounded_claims`/`checked_claims`, and `covered_items`/`expected_items`.
Grounding measures checked claims, not all unexamined claims. Fix the coverage
reference set before trials; a zero denominator is unknown. Omit unobserved
metrics. Record `escaped_defects` only after the agreed observation window.

Use `measured_total_tokens` for normalized total-system provider usage, or the
existing input/output/cache buckets when their accounting is non-overlapping.
Never add overlapping provider cache buckets twice. Include every worker and
reviewer; missing usage stays unknown. Token totals are not dollar prices.

```bash
python3 scripts/token_efficiency_benchmark.py \
  --results .build-loop/evals/architecture-results.jsonl \
  --baseline single-owner --candidate bounded-worker --pilot --json
```

The comparator rejects malformed receipts, excludes pairs with mismatched
declared controls, counts unmatched runs, and separates evidence kind, experiment
axis and task shape. Each metric reports its observed-pair count; incomplete
coverage produces a null change. Grounding/coverage are pooled ratios within a
stratum; duration and count metrics are totals. Report individual regressions
even if aggregate acceptance improves. Do not pool across task shapes to pick
one universal topology. The report is descriptive and never auto-promotes an
architecture. A frozen controls manifest still needs independent inspection.

## Decide the next experiment

Report accepted work, defects, interventions, lost decisions, rework, wall time,
measured usage, missing observations and confounds separately. A cheaper failed
answer is not an improvement. Calibration establishes harness behavior only.
Live results justify a bounded follow-up trial when quality holds and a useful
cost or latency signal recurs; they do not establish a universal specialist or
agent-count rule. Permanent policy changes need separately reviewed evidence.
