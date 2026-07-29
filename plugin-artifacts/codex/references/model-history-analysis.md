# Model Run History Analysis

Use `scripts/model_run_history.py` to preselect treatments and repeatable tasks
for a controlled model bake-off. The script reads local Codex rollout JSONL and
keeps the model and effort level as separate treatment fields.

## Example

```bash
python3 scripts/model_run_history.py \
  --arm sol-hi=gpt-5.6-sol:high \
  --arm tera-xhi=gpt-5.6-terra:xhigh \
  --output .build-loop/model-history-sol-hi-vs-tera-xhi.json
```

The report contains per-arm completion, latency, token, tool-call, and
verification-signal proxies. It groups repeats of the first non-injected
user-role message in each turn; known plugin, environment, skill, and abort
envelopes are excluded before hashing. Directional workspace/task cohorts are a
separate, weaker signal. Copied turns from forked or compacted rollouts are
deduplicated by turn ID plus model and effort before aggregation. Token medians
include only turns with per-call `last_token_usage`; legacy session-cumulative
totals are omitted because they cannot be assigned to one turn.

## Interpretation Boundary

History cannot establish which arm produces better code. Task mix, prompt,
workspace, tooling, and environment vary across observations. Completion means
the host emitted `task_complete`; verification signals are command-pattern
proxies reported as both a count and a per-arm rate. Treat all comparisons as
directional.

The script never emits prompt text or workspace names. It emits opaque
identifiers and local session references so a selected repeat can be recovered
locally.

## Controlled Follow-up

Move promising exact repeats into `model-bakeoff`:

1. Fix one base SHA, prompt, acceptance rubric, judge, and tool environment.
2. Run `gpt-5.6-sol:high` and `gpt-5.6-terra:xhigh` in isolated worktrees.
3. Collect at least three samples per arm.
4. Re-run the same deterministic verifier against committed outputs.
5. Store results in the existing `abc-comparison/v2` artifact with exact model,
   effort, effort provenance, and mode fields.

Historical analysis selects candidates. The controlled harness supplies quality
evidence.
