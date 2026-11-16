# Quality-first workflow learning

## Objective

Produce a materially better user outcome than the same model using native tools
without an added loop, with fewer user corrections and recovery steps. Treat
latency and tokens as separate efficiency measures. Reuse relevant preferences,
context and learned procedures; do not grow a workflow merely because a task
completed successfully.

## Implemented scope

- `scripts/workflow_evidence.py` evaluates optional matched evidence in existing
  run records. It writes no separate state store and makes no model calls.
- `write_run_entry` validates `harness.workflow_evaluation` before persistence.
  An accepted observation must agree with the run's pass outcome.
- Learn consumes `workflow_contract` from the existing experiment's created row
  before legacy score paths. Missing or malformed evidence cannot fall back to
  generic pass/partial/fail scores. A result can request independent review;
  it cannot activate a workflow or authorize memory changes.
- Frozen assignments name both run IDs for every case. Relevant missing records,
  uncontrolled experiments, mismatched context, unknown quality/interventions,
  incomplete candidates and per-case regressions prevent qualification.
- The contract pins distinct workflow revisions, native baseline configuration,
  execution-config and trace hashes, checker/manifest hashes, a positive quality
  threshold and a prespecified sample. Review authenticates cited evidence;
  matching JSON does not authenticate a trace or establish population superiority.
- Completed reviews are recorded in the existing experiment log, keyed by evidence
  and artifact hashes. Unchanged evidence is not reviewed again while the receipt
  remains in the bounded retained log window. Changed artifacts/evidence may be
  reconsidered. Truncation can cause re-review and remains visible.
- `review_trigger` provides conditional existing-behavior compatibility checks in
  the existing review. Numeric confidence/size uncertainty remains visible;
  existing review obligations and nullable boolean semantics remain intact.

Routing reasons, tool choices, preference references and recalled/used item IDs
remain in existing harness metadata. Memory-facade recall and context snapshots
retain their ownership. Memory exposure does not establish usefulness. This change
adds no scheduler, daemon, automatic promotion or new harness dependency.

## Fresh pilot

Four counterbalanced attempts compared direct Sol medium with the same requested
model plus a focused compatibility procedure on two newly authored legacy-state
coding tasks. All attempts used the same native runtime and a 180-second budget.
Nine oracle controls passed their expected verdicts before candidate execution.
The manifest, tasks, graders, references and runner were frozen before calls.

| Task | Direct | Procedure | Frozen correctness |
| --- | ---: | ---: | --- |
| SQLite delivery updates | 72.92 s | 104.99 s | Both passed all 36 checks |
| JSON delivery updates | 79.98 s | 124.57 s | Both passed all 49 checks |
| Total reported tokens | 322,587 | 477,477 | No observed quality gain |

All four runs completed normally. An independent trace audit found no observed
hidden-answer access or prohibited effects. Both direct runs independently
performed substantial compatibility checks; both procedure runs followed the
procedure. Failures and recoveries inside attempts remain in raw traces.

These are two designed tasks with one call per arm, targeting a prior diagnostic
failure. They are exploratory transfer cases, not a representative or blinded
population sample. Served model identity, billing and human effort are unknown.
The treatment combined an instruction with a prior lesson; it does not isolate
memory retrieval or prove a long-term learning effect. No extra calls were made
in pursuit of significance. The procedure earned no mandatory routing promotion.

Retained local evidence: `~/dev/research/runs/loop-quality-20261116/`, including
`fresh-pilot/manifest.json`, `fresh-pilot/results.jsonl`,
`statistical-review.md`, and `implementation/`. Earlier evidence remains unchanged
under `loop-options-20261115/`.

## Validation and next evidence

Behavioral tests exercise the real run writer, public Learn entrypoint, evaluator,
review attestation and repeated-review suppression. Independent architecture and
cross-vendor review challenge the source; the self-modification gate checks the
final owned change set before local integration. Test receipts are retained with
the run, separate from model-performance evidence.

A future learning study must freeze a discovery set, distinct held-out tasks,
repeated assignments, preference/context applicability and intervention-count
rules. Compare cold execution against selective learned recall while keeping
allowed tools/memory opportunities matched. Record selected/used memories,
corrections, external quality, total resources and failures. Expand coverage when
new evidence reveals a gap; retain, revise or withdraw workflows based on those
results. The current pilot does not justify replacing the native executor.
