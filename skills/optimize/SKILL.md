---
name: optimize
description: Autonomous metric-driven optimization loop. Measures a number, makes atomic changes, keeps improvements, reverts regressions. Use after implementation for build time, code simplification, test coverage, bundle size, or any mechanical metric.
---

# Optimize — Autoresearch-Pattern Optimization

Karpathy's autoresearch adapted for post-implementation optimization: define a mechanical metric, constrain the scope, iterate autonomously. Keeps what improves, reverts what doesn't.

## When to Use

After Phase 4 (Execute) when a mechanical metric exists:
- Build time (seconds)
- Line count in changed files (simplification)
- Test coverage (%)
- Bundle size (bytes)
- Any command that outputs a number

Skip when the metric is subjective or requires human judgment.

## Phase 1: SETUP (Opus)

Highest-leverage phase. Wrong metric = Goodhart's Law.

Define:
1. `target` — what to optimize (name)
2. `scope` — which files can change (glob or list)
3. `metric_cmd` — shell command → number
4. `guard_cmd` — shell command that must exit 0
5. `budget` — max total iterations (default 5 for post-build, 20 for standalone)
6. `direction` — `"lower"` or `"higher"`

Auto-detection: run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/optimize_loop.py --detect --workdir "$PWD"` to discover available targets.

The `simplify` target is always available: metric = total lines in scope files, direction = lower, guard = build passes.

Initialize:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/optimize_loop.py \
  --init --workdir "$PWD" \
  --target "<name>" --scope "<glob>" \
  --metric-cmd "<cmd>" --guard-cmd "<cmd>" \
  --budget <N> --direction "<lower|higher>"
```

## Phase 2: LOOP (Sonnet)

Dispatch the `optimize-runner` agent. It executes:

```
1. Read .build-loop/optimize/experiment.json + results.tsv + git log
2. Hypothesize: ONE atomic change based on what worked/failed before
3. Edit: only files matching scope
4. Commit: git commit -m "optimize: <description>"
5. Measure: run metric_cmd
6. Guard: run guard_cmd
7. Decide: improved over best_value AND guard passes → KEEP (update best)
           worse OR guard fails → git revert HEAD
8. Log: append to results.tsv with hypothesis text
9. Convergence check:
   - 5 consecutive discards → plateau, stop
   - metric trending worse over 3 kept iterations → regressing, stop
   - budget exhausted → stop
10. If not converged → step 1
```

## Phase 3: REVIEW (Opus + Sonnet)

1. Dispatch `overfitting-reviewer` (Sonnet, read-only): check for removed safety features, fragile shortcuts, test-gaming
2. Generate summary: iterations, kept/reverted, improvement %, top changes
3. Archive: `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/optimize_loop.py --archive --workdir "$PWD"`

## Model Tiering

| Component | Model | Why |
|-----------|-------|-----|
| Setup | Opus | Wrong metric = Goodhart |
| Hypothesis generation | Sonnet (pinned) | High volume, 5x cheaper |
| Metric/guard execution | Bash | No LLM |
| Keep/revert | Deterministic | Numeric comparison |
| Overfitting review | Sonnet (read-only) | Pattern matching |
| Final report | Opus | Judgment |

## Integration with Build-Loop

Phase 4.7 (AUTO-OPTIMIZE): after Phase 4 Execute completes and commits, check for optimization targets. Run sequentially (not parallel with Phase 4).

Standalone: `/build-loop:optimize [target]`

## State Files

```text
.build-loop/optimize/
├── experiment.json    # Active config
├── results.tsv        # Iteration log with hypotheses
└── experiments/       # Archived pairs (.json + .tsv)
```

## Built-in Profiles

See `profiles.md`. The `simplify` profile is always available.
