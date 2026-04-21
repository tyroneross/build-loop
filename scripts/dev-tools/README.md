# dev-tools

Developer-facing scripts for maintaining build-loop itself. These are **not** loaded or invoked by any skill, agent, or command during normal operation.

| Script | Purpose |
|---|---|
| `experiment_metrics.py` | Compute metrics from archived `.build-loop/optimize/experiments/*.json` + `.tsv` for evaluating and improving the optimize-loop prompts. Run manually against a consumer project's `.build-loop/` directory. |

If a script in this directory starts being called from a skill/agent/command, move it back to `scripts/` (top-level) — that's the runtime path.
