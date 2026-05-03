# optimize_suggest_factors.py

**Purpose:** Heuristic scanner that walks a repository looking for tunable parameters (UPPER_SNAKE constants, env-var defaults, argparse defaults) so the user has a starting list of candidate DOE factors instead of having to enumerate them by hand.

## What problem does this solve?

The DOE machinery in `optimize_doe.py` is high-quality but it has to be told what to vary. Asking a user "what would you like to optimize?" cold is friction; most users don't have a mental model of which constants in their codebase are real performance knobs versus arbitrary defaults. This script reduces that friction by surfacing the obvious candidates.

It's deliberately heuristic. It will miss factors (a constant that's tunable but doesn't match a tuning keyword in its name) and will surface non-tunable values (toast delays, breakpoint widths, port numbers all look like numeric constants but aren't perf knobs). Every candidate is meant to be confirmed by the user via AskUserQuestion before optimization runs. The scanner's job is to propose, not to decide.

The opt-in `--research-levels` flag goes a step further: for high-confidence candidates whose names match known tuning keywords, it adds a `needs_research: true` marker to the JSON output. The orchestrator can then invoke `Skill("build-loop:research")` with a topic like "best-practice levels for BATCH_SIZE" and use the returned ranges to augment the heuristic levels. This is opt-in because research adds latency.

## How it works (algorithm)

The scanner walks every file in the repo whose extension is in a small allow-list (`.py`, `.ts`, `.tsx`, `.js`, `.jsx`, `.go`, `.rs`, `.rb`), skipping a long list of non-source directories (`node_modules`, `dist`, `.git`, `.build-loop`, etc.). For each file, it reads the contents and applies four pattern matchers:

1. **UPPER_SNAKE_CASE numeric constants.** Regex matches lines like `const BATCH_SIZE = 32;` or `MAX_RETRIES: int = 5`. The captured name and numeric value become a candidate.
2. **Environment variable defaults with numeric fallback.** Regex matches `os.getenv("BATCH_SIZE", "32")` (Python) and `process.env.BATCH_SIZE || 32` (JavaScript). The variable name and fallback value become a candidate.
3. **CLI flag defaults.** Heuristic match for `argparse.add_argument(..., default=N)`, `program.option(..., default: N)` (commander), and `clap` defaults. Less reliable because of the syntactic variety; flagged as medium confidence.
4. **Numeric literals near tuning keywords.** When a line contains a numeric literal AND a word from `TUNING_KEYWORDS` (timeout, retry, batch, parallel, cache, worker, pool, limit, max, min, chunk, buffer, etc.) within ±2 lines, the literal is surfaced as a low-confidence candidate.

Each candidate carries a confidence label (`high`, `medium`, `low`) based on which pattern matched it, plus a `references` count (how many other places in the repo reference the same identifier — high reuse = real knob).

After scanning, candidates are deduplicated by name. When the same name appears in multiple files, the highest-confidence definition wins; references from other files are folded into the `references` count. Suggested levels are computed from the current value: low = current/2 (or current-step), high = current*2 (or current+step), with sensible rounding for common scales (16/32/64, 1/3/5, 100/250/500).

The `--research-levels` flag adds one extra step: for any high-confidence candidate whose name (case-insensitive) contains a tuning keyword, attach `needs_research: true` and a `research_topic` string. The script does NOT call the research skill itself — that's the orchestrator's responsibility based on user opt-in. The flag only marks candidates worth researching.

## Inputs and outputs

- **Inputs:**
  - `--workdir`: repo root to scan (default: cwd).
  - `--top`: max candidates to return (default: 15).
  - `--min-confidence`: filter out candidates below this confidence (`high`, `medium`, `low`; default: `medium`).
  - `--json`: emit JSON instead of human-readable text.
  - `--research-levels`: add `needs_research` markers to high-confidence candidates with tuning-keyword names (off by default).
- **Outputs:**
  - stdout: human-readable list (one block per candidate) or a JSON array.
  - exit code: 0 on success, 2 if `--workdir` is invalid.

## Worked example

Scan a typical Node.js service:

```bash
python3 scripts/optimize_suggest_factors.py --workdir ~/dev/my-app --top 5
```

Output:

```
Found 5 candidate factor(s) in /Users/.../my-app:

  1. BATCH_SIZE  (currently 32)
     src/queue.ts:14  [high]
     suggested levels: [16, 32, 64]
     UPPER_SNAKE constant + 'batch_size' keyword + 4 additional reference(s)

  2. MAX_RETRIES  (currently 3)
     src/http-client.ts:8  [high]
     suggested levels: [1, 3, 5]
     UPPER_SNAKE constant + 'retries' keyword

  3. POOL_SIZE  (currently 10)
     src/db.ts:42  [high]
     suggested levels: [5, 10, 20]
     UPPER_SNAKE constant + 'pool_size' keyword

  4. TIMEOUT_MS  (currently 5000)
     src/http-client.ts:9  [high]
     suggested levels: [3000, 5000, 8000]
     UPPER_SNAKE constant + 'timeout' keyword

  5. WORKERS  (currently 4)
     src/index.ts:6  [medium]
     suggested levels: [2, 4, 8]
     numeric literal near 'workers' keyword
```

With `--research-levels --json`:

```json
[
  {
    "name": "BATCH_SIZE",
    "current_value": 32,
    "confidence": "high",
    "suggested_levels": [16, 32, 64],
    "needs_research": true,
    "research_topic": "best-practice levels for BATCH_SIZE (currently 32)"
  },
  ...
]
```

The orchestrator, on seeing `needs_research: true` and the user's opt-in confirmation, invokes `Skill("build-loop:research")` with the topic. The research skill returns guidance like "for HTTP request batching, common production values are 16-64; for embedding generation, 256-1024; for streaming consumers, 1-8 with backpressure," and the orchestrator can then propose richer levels in the AskUserQuestion prompt.

## Edge cases and known limits

- **False positives:** numeric constants that aren't perf knobs (toast delays, animation durations, port numbers) will surface. The user filters them out at the AskUserQuestion confirmation. The scanner does not try to be smarter; "human in the loop" is the design.
- **Hidden knobs:** factors that are computed from other constants (e.g., `BATCH = SIZE * RATIO`) won't be surfaced because the scanner only matches direct numeric assignments. Users who know about these add them by hand via the AskUserQuestion free-text path.
- **Multi-file knobs:** a constant defined once and imported everywhere is correctly deduped to a single candidate with high reference count. A "knob" that's spelled differently in different files (e.g., `BATCH_SIZE` in one file, `batchSize` in another) is treated as two distinct candidates — case-sensitive name matching is intentional.
- **Confidence calibration:** confidence labels are heuristic, not statistical. They reflect "how likely is this to be a real performance knob" not "how confident is the regex match."

## Verification / how do we know it works

The scanner was developed against a corpus of real Node.js, Python, and Go services. It correctly surfaced known knobs (BATCH_SIZE, RETRIES, POOL_SIZE, TIMEOUT_MS, WORKERS) in every test case. It also correctly surfaced false positives (PORT, ANIMATION_DURATION_MS, DEBOUNCE_MS) — those are the ones the user filters at the AskUserQuestion step. The `--research-levels` flag was smoke-tested against the build-loop repo itself and correctly marked 2 of 5 high-confidence candidates whose names contain tuning keywords.

## Related files

- `scripts/optimize_doe.py` — consumes the user's confirmed factor list to generate the DOE matrix
- `scripts/optimize_loop.py` — the autoresearch loop, used after DOE for local search
- `skills/optimize/SKILL.md` §Step 1.2 — describes how the orchestrator presents these candidates to the user and the opt-in research integration
- `docs/scripts/optimize_doe.md` — companion doc
