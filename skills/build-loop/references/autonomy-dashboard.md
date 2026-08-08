# Autonomy Decision Dashboard

## Big idea

Build Loop should finish related work by default and interrupt the owner only
when a decision changes production, reversibility, authorized scope, or a major
user outcome.

## Start

```bash
python3 scripts/autonomy_dashboard.py --workdir "$PWD" --port 8765
```

The command starts a detached local server that survives the launching terminal
or agent session. Open `http://127.0.0.1:8765`. Check or stop it with:

```bash
python3 scripts/autonomy_dashboard.py --workdir "$PWD" --status
python3 scripts/autonomy_dashboard.py --workdir "$PWD" --stop
```

Use `--foreground` only when an external process manager owns the server
lifecycle. The server refuses non-loopback binding and non-loopback Host/Origin
headers.

## Persistence contract

- Every selection or note edit appends a `response_saved` event to
  `.build-loop/autonomy-dashboard/responses.jsonl`.
- Reload and process restart reconstruct the latest response per gap from that
  append-only log.
- **Queue this decision** appends `response_queued` and creates
  `.build-loop/followup/dashboard-<gap>-<timestamp>.md`.
- Agents may read saved responses for context. They act only on queued follow-up
  items or a direct user instruction.

## Agent consumption

1. Run `python3 scripts/autonomy_dashboard.py --workdir "$PWD" --print-state`.
2. Treat `responses[*].choice_id` and `note` as owner-authored direction.
3. Treat `queued_path` as the execution instruction and re-check its premise
   against the live repo before changing code.
4. Route the follow-up through normal autonomy, validation, and production gates.

## Policy ownership

The dashboard explains and records choices. `scripts/autonomy_supervisor.py`
owns execution policy: missing-information routing, task-shape learning, bounded
queue manifests, discovered-issue classification, convergence enforcement, and
live provider/host/cost backpressure.
