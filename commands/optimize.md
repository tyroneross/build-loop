---
name: optimize
description: "Run autonomous metric-driven optimization. Detects available targets or accepts a specific one."
argument-hint: "[target]"
---

Load the `build-loop:optimize` skill.

{{#if ARGUMENTS}}
Target: `{{ARGUMENTS}}`

Run all three phases:
1. **Setup** — configure the optimization for the specified target
2. **Loop** — iterate: hypothesize → edit → measure → keep or revert
3. **Review** — check for overfitting, summarize results

If the target matches a known profile (`simplify`, `optimize-build`, `optimize-tests`, `optimize-bundle`, `optimize-perf`, `semantic-search-latency`), use the pre-configured settings from `profiles.md`.

For custom targets, run Phase 1 (Setup) interactively.
For latency-sensitive targets, prefer repeated sampling with warmups and a stable aggregate such as `median` or `p95` instead of a single timing sample.
{{else}}
No target specified.

1. Run `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/optimize_loop.py --detect --workdir "$PWD"` to discover available targets
2. Check for existing `.build-loop/optimize/experiment.json` — offer to resume if found
3. Present available targets and let the user choose
4. The `simplify` target is always available (reduces line count in changed files)
5. For response-time work, initialize a custom target with `--metric-samples`, `--metric-warmups`, and `--metric-aggregate` so the loop compares stable latency numbers instead of one noisy run
{{/if}}
