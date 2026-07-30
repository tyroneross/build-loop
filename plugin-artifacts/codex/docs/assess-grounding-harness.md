<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Assess-Grounding Replay Harness (v1)

Measure and improve the **grounding of build-loop's Phase-1 Assess step** empirically, by replaying prior real challenges under different grounding strategies and scoring the resulting assessments against what actually happened.

## The core idea

The assessor *is* the `build-orchestrator` (`model: opus`) running `skills/build-loop/references/phase-1-assess.md` inline — there is no separate assess agent. "Grounding" is varied by **what evidence the protocol injects**, not by editing a prompt. The primary dial is the step-5 architecture-retrieval tier (navgator-full → gator → explore → raw-read), plus step-5b reads-deps and the step-14 citation gate.

Build-loop does **not** snapshot the assessment-as-produced per run, so there is no gold assessment to diff against. But every `state.json` `runs[]` entry records the **objective outcome**: `triggers`, `synthesisDensity`, `filesTouched`, `outcome`. So we grade **assessment → outcome** ("did Assess predict what actually mattered?"), not assessment → gold. Ground truth is the recorded outcome, **never assistant prose** — circular self-grading is the one failure mode that invalidates everything.

A "mock repo" is just a real checkout: `git worktree add <tmp> <sha>` at the run's recorded commit.

## Objective vector (multi-goal)

A good assessment is several competing things at once, so the score is a **vector, not a number** (`scripts/assess_grounding_score.py`):

| Objective | Direction | Source | Meaning |
|---|---|---|---|
| `trigger_recall` | max | deterministic | caught the triggers that actually mattered |
| `trigger_precision` | max | deterministic | did not over-flag triggers (over-flag = wasted Opus cycles) |
| `synthesis_calibration` | max | deterministic | predicted synthesis count + escalation ≈ actual |
| `file_recall` / `file_precision` | max | deterministic | predicted files vs files actually touched (optional) |
| `groundedness` | max | **Fable judge** | fraction of TRUE triggers whose cited file:line evidence holds |
| `cost_tokens` / `latency_ms` | min | measured | what the assess pass cost |

`groundedness` is the only LLM-graded objective (eval-guide.md doctrine: binary PASS/FAIL per trigger, one evaluator per dimension, run on Fable). Everything else is code-based. `null` = the run didn't record that field → **not gradable, never scored as 0** (e.g. a run with empty `synthesisDensity`).

Precision-vs-recall and groundedness-vs-cost are genuine opposing pressures, so there is no single winner — the harness reports the **Pareto frontier** (`pareto_variants`). If you must collapse to one number, scalarize with *explicit, visible* weights; the weight vector is a product decision, not something the eval derives.

Stratify by `goal_type` (audit/refactor/bugfix/feature/migration/debug): the best grounding config almost certainly differs by type, so the output is a **conditional grounding policy** ("migration → high-ground tier; trivial → baseline"), not one global config.

## What you do with the results — the loop

```
OFFLINE replay  ──►  candidate config / failure diagnosis  ──►  ONLINE A/B  ──►  promote  ──►  regression suite
 (filter, cheap)      (5 use-paths below)                       (the VERDICT)               (re-run on protocol change)
```

Offline replay is a **filter** — a fast candidate-finder + failure-diagnoser — **not the verdict**. From it, five use-paths:

1. **Direct config edit** — a Pareto-dominant variant → change the `phase-1-assess.md` step-5 default / make citation mandatory.
2. **Conditional routing policy** — per-`goal_type` winners → a cheap goal-classifier picks the grounding tier as an early Assess step.
3. **Few-shot exemplars** — the highest-scoring assessments become grounding examples injected into the protocol.
4. **Drafted experiment** — hand the winner to `self-improvement-architect`; it writes `.build-loop/experiments/<name>.jsonl` with `baseline/target/sample_size`.
5. **Failure taxonomy** — cluster the low scorers ("misses riskSurfaceChange on auth diffs") into specific protocol fixes.

**Online A/B is the verdict.** Offline replay only rewards *matching the recorded past* and cannot see counterfactual gains (a better assessment might have produced a *different, better* outcome than history shows). So a winning offline candidate goes through the existing `experiments/*.jsonl` + Phase-6 promote/discard loop on real runs before adoption. Once adopted, the challenge set becomes a **regression suite** re-run on every change to the Assess protocol — the same lesson as a CI gate: verify the real gate, not the proxy.

## Guardrails (do not skip)

- **Ground truth = objective recorded outcomes only.** Never grade against assistant prose.
- **Offline = filter, online A/B = verdict.** Label offline results as hypotheses; don't adopt on offline alone.
- **Goodhart.** Optimizing `groundedness` can yield performative citations without better decisions — run `overfitting-reviewer` on winners and tie the headline to *outcome quality*, not citation density.
- **Small-N.** v1 has 3 seeds; treat results as hypotheses, hold out a split, don't overclaim until ~10–20 diverse challenges exist.

## Files

- `scripts/assess_grounding_score.py` — deterministic multi-objective scorer (+ `test_assess_grounding_score.py`, 16 tests).
- `evals/assess-grounding/challenges.jsonl` — challenge spec + 3 real seed challenges (build-loop's own runs).
- `evals/assess-grounding/replay-pilot.workflow.js` — the replay→judge→score Workflow (sandboxed JS; agents hold the real tools).

## How to run

Scorer alone (deterministic; candidates from any source):

```bash
python3 scripts/assess_grounding_score.py \
  --candidates <candidates.jsonl> \
  --challenges evals/assess-grounding/challenges.jsonl
```

Live replay pilot (spawns Opus Assess agents + Fable judges — budget it; subset to stay bounded):

```
Workflow({ scriptPath: "evals/assess-grounding/replay-pilot.workflow.js",
           args: { challengeIds: ["blr-f6-stop-closeout"], reps: 1 } })
```

## v1 scope

Model is fixed = **Opus** (option a); the variant list and scorer are built so **model becomes an added DoE factor** (option b) by giving each variant a `model` field — no rework. v1 ships the offline half (replay + multi-objective scoring + Pareto/per-goal-type scorecard); the online-A/B half reuses the existing `experiments/*.jsonl` + Phase-6 machinery.
