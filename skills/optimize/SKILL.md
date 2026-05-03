---
name: optimize
description: Metric-driven optimization with DOE by default. Triggers on "run optimization", "optimize this", "make this faster", "improve my app", "speed up X", "reduce <metric>". Defaults to multi-factor Design of Experiments (full / fractional factorial / Plackett-Burman) when ≥2 factors are involved; falls back to single-variable autoresearch for 1-factor cases. If user doesn't specify factors, scans the codebase, proposes candidates, and asks for confirmation before running anything.
---

# Optimize — DOE-First with Autoresearch Fallback

Combines Design of Experiments (multi-factor, statistically rigorous) with Karpathy-style autoresearch (single-factor, hypothesis-driven). DOE is the default; autoresearch is the single-variable special case. Both keep what improves and revert regressions, but DOE plans the full experiment matrix up front and recovers main effects + interactions; autoresearch is a sequential greedy local search.

## When to Use

After Phase 4 (Execute) when a mechanical metric exists:
- Build time (seconds)
- Line count in changed files (simplification)
- Test coverage (%)
- Bundle size (bytes)
- Response time / latency benchmarks
- Any command that outputs a number

Skip when the metric is subjective or requires human judgment.

## Phase 1: SETUP (Opus) — Three-Branch Routing

Highest-leverage phase. Wrong metric = Goodhart's Law. Wrong factors = wasted runs.

### Step 1.1 — Detect trigger shape

| Branch | Trigger | Action |
|---|---|---|
| **A. Power-user explicit** | User supplied factors via CLI flag, `.build-loop/optimize/factors.json`, or inline ("optimize batch_size, retries, workers for throughput") | Skip suggestion; use the user's factors directly |
| **B. Vague optimization** *(default)* | "run optimization", "make my app faster", "improve performance", "speed up", "reduce <metric>" without naming factors | Run factor-identification scan; propose candidates; **AskUserQuestion to confirm before running** |
| **C. Single-variable explicit** | "simplify this file", "reduce build time", scoped `/build-loop:optimize <known-target>` | Skip DOE; run autoresearch (existing behavior, Phase 2 LOOP unchanged) |

### Step 1.2 — Branch A or B: factor identification

**Branch A** (factors pre-supplied): validate shape `[{name, low, high}, ...]` or `[{name, levels: [...]}, ...]`. Skip to Step 1.3.

**Branch B** (suggest factors): run the codebase scanner, present candidates, ask for confirmation.
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/optimize_suggest_factors.py --workdir "$PWD" --top 12 --json
```
Returns ranked candidates (UPPER_SNAKE constants near tuning keywords, env vars with numeric defaults, etc.). For each, the scanner suggests low/center/high levels.

**Then AskUserQuestion** (multi-select, all candidates pre-checked):
> "Which of these should I optimize?"
> [✓] BATCH_SIZE (currently 32) — try [16, 32, 64]
> [✓] RETRIES (currently 3) — try [1, 3, 5]
> [ ] TIMEOUT_MS (currently 5000) — try [3000, 5000, 8000]
> Free-text: "add my own factor / change levels"
> Decline path: "skip optimization"

Only proceed once the user confirms. Do NOT auto-run optimization on heuristic candidates without explicit user buy-in — false positives are common (toast delays, breakpoints, port numbers all look numeric to the scanner but aren't perf knobs).

**Optional: research-backed levels (opt-in).** If `availablePlugins.research` is true AND the user explicitly asks for "research-backed levels" (or accepts the prompt below), append `--research-levels` to the scanner invocation:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/optimize_suggest_factors.py --workdir "$PWD" --top 12 --json --research-levels
```

The scanner adds `needs_research: true` and a `research_topic` string to high-confidence candidates whose names match known tuning keywords (BATCH_SIZE, TIMEOUT, WORKERS, etc.). For each marked candidate, invoke `Skill("build-loop:research")` with the topic string ("best-practice levels for BATCH_SIZE (currently 32)"). Use the returned ranges to augment — not replace — the scanner's heuristic levels in the AskUserQuestion prompt. Default behavior is heuristic-only because research adds latency (a few minutes per candidate); the opt-in is for cases where the user wants level recommendations grounded in benchmarks rather than evenly-spaced guesses around the current value.

The script never calls research itself — it only flags candidates worth researching. The orchestrator decides whether to invoke the research skill based on the user's explicit opt-in.

### Step 1.3 — Design selection (Branches A + B with k≥2)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/optimize_doe.py detect <k>
```
Auto-routes by factor count: `k=1` → fall back to autoresearch (Branch C), `2 ≤ k ≤ 3` → 2^k full factorial (4–8 runs), `4 ≤ k ≤ 7` → 2^(k-p) fractional R-III/IV (8 runs), `k ≥ 8` → Plackett-Burman 12-run screening (handles up to 11).

Generate the matrix:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/optimize_doe.py generate \
  --factors "$(cat .build-loop/optimize/factors.json)" \
  --design auto --seed "$RANDOM" \
  > .build-loop/optimize/doe.json
```

### Step 1.4 — Branch C: autoresearch setup

Single-factor — keep the existing setup. Define:
1. `target` — what to optimize (name)
2. `scope` — which files can change (glob or list)
3. `metric_cmd` — shell command → number
4. `guard_cmd` — shell command that must exit 0
5. `budget` — max total iterations (default 5 for post-build, 20 for standalone)
6. `direction` — `"lower"` or `"higher"`
7. `metric_samples` — measured benchmark runs per iteration (default 1)
8. `metric_warmups` — warmup runs discarded before measuring (default 0)
9. `metric_aggregate` — how to combine samples (`last`, `mean`, `median`, `p95`, etc.)

Auto-detection: run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/optimize_loop.py --detect --workdir "$PWD"` to discover available single-variable targets.

Initialize:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/optimize_loop.py   --init --workdir "$PWD"   --target "<name>" --scope "<glob>"   --metric-cmd "<cmd>" --guard-cmd "<cmd>"   --budget <N> --direction "<lower|higher>"   --metric-samples <count> --metric-warmups <count>   --metric-aggregate "<last|min|max|mean|median|p95>"
```

For latency work such as semantic search, do not optimize on one timer reading. Use a representative query set, run multiple measured samples, discard at least one warmup when cold starts matter, and aggregate with `median` or `p95`.

## Phase 2: LOOP (Sonnet)

### Branch A/B (DOE) — run the matrix

For each row in `.build-loop/optimize/doe.json` (in randomized `run_order`):
1. Apply the factor values from `runs[i]._factors` to the codebase / config / env
2. Run `metric_cmd` (with `metric_samples` and `metric_warmups` from setup)
3. Run `guard_cmd` (must exit 0)
4. Append to `.build-loop/optimize/results.jsonl`: `{"run_id": i, "value": <number>, "guard_ok": true}`
5. Revert factor changes (each run is from the same baseline; DOE doesn't accumulate)

After all runs complete, fit effects:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/optimize_doe.py analyze \
  --design .build-loop/optimize/doe.json \
  --results .build-loop/optimize/results.jsonl \
  --direction "<lower|higher>" \
  > .build-loop/optimize/effects.json
```

Output: ranked main effects + interactions, `r2` if non-saturated, best run id with the winning factor levels. The output also includes a `best_factors` block mapping factor names to their concrete values at the best run. Apply the winning combination as a single commit.

**Optional handoff to autoresearch.** For local search around the DOE-identified optimum, initialize an autoresearch experiment using the effects.json as the starting baseline:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/optimize_loop.py \
  --init --workdir "$PWD" \
  --target "<name>" \
  --metric-cmd "<cmd>" --guard-cmd "<cmd>" \
  --direction "<lower|higher>" \
  --baseline-config .build-loop/optimize/effects.json
```

`--baseline-config` reads the DOE best-run factor levels and records them in `experiment.json.doe_baseline.factors`. The autoresearch agent reads that block before its first iteration, applies those values as the starting point, then iterates from there. Without `--baseline-config`, the loop starts from the current working tree as before.

### Branch C (autoresearch) — single-variable greedy

Dispatch the `optimize-runner` agent. It executes:

```
1. Read .build-loop/optimize/experiment.json + results.tsv + git log
2. Hypothesize: ONE atomic change based on what worked/failed before
3. Edit: only files matching scope
4. Commit: git commit -m "optimize: <description>"
5. Measure: run metric_cmd with the configured sampling settings
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

See `profiles.md`. The `simplify` profile is always available. For latency-sensitive work, start with `semantic-search-latency` or `optimize-perf` plus explicit sampling settings.
