# Build Loop Dashboard

## Big idea

The local dashboard shows the current Build Loop phase, major tasks, and agents
that have actually been invoked. The same page retains the autonomy decision
controls for production, reversibility, scope, and major user outcomes.

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

The dashboard is opt-in and token-free. Build Loop does not start it
automatically, `--stop` removes the running surface whenever the user prefers,
and the projection performs no LLM or provider calls.

Use `--foreground` only when an external process manager owns the server
lifecycle. The server refuses non-loopback binding and non-loopback Host/Origin
headers.

## Live run projection

`GET /api/state` includes a `run` object produced by
`scripts/dashboard_projection.py`. The browser refreshes that projection every
two seconds while the page is visible; no server restart is required when the
run advances.

Each of the six standard phases includes its expected output. Free-form progress
comments use the existing bounded working-state channel:

```bash
python3 scripts/working_state_writer.py --workdir "$PWD" \
  --agent "<agent-id>" --run-id "<run-id>" --phase execute \
  --status editing --note "Connecting the live task projection."
```

The note is capped at 800 characters, stored in
`.build-loop/working-state/{current.json,log.jsonl}`, and filtered to the current
run before display.

The projection is read-only and rebuildable. It uses these canonical records:

- `.build-loop/state.json` for the current run, phase, and structured task
  collections;
- `.build-loop/plan.md` to enrich matching bare task IDs and as a major-task
  fallback when structured execution tasks are absent;
- `.build-loop/agent-ledger.jsonl` for recorded agent invocations in the current
  run; and
- `.build-loop/working-state/{current.json,log.jsonl}` for bounded free-form run
  notes and comments; and
- `runs[-1].judge_decisions` in `.build-loop/state.json` for the latest
  completed run when no agent-ledger rows are available.

Missing or malformed optional records produce an honest empty state or warning.
The dashboard does not infer invocation from an available-agent roster and does
not write phase, task, agent, or judge state.

## Persistence contract

- Every selection or note edit appends a `response_saved` event to
  `.build-loop/autonomy-dashboard/responses.jsonl`.
- Reload and process restart reconstruct the latest response per gap from that
  append-only log.
- Each collapsed card names the selected policy and shows saved, queued, or
  applied state;
  JavaScript applies selected styling directly so feedback does not depend on
  CSS `:has()` support.
- **Queue this decision** appends `response_queued` and creates
  `.build-loop/followup/dashboard-<gap>-<timestamp>.md`.
- Queuing a revision moves older executable files for the same gap to
  `.build-loop/autonomy-dashboard/superseded/`; one gap has one live instruction.
- A validated completion appends `response_applied`, moves its instruction out
  of `.build-loop/followup/` into `.build-loop/autonomy-dashboard/applied/`, and
  shows the completion summary and evidence in the dashboard.
- Agents may read saved responses for context. They act only on queued follow-up
  items or a direct user instruction.

## Agent consumption

1. Run `python3 scripts/autonomy_dashboard.py --workdir "$PWD" --print-state`.
2. Treat `responses[*].choice_id` and `note` as owner-authored direction.
3. Treat `queued_path` as the execution instruction and re-check its premise
   against the live repo before changing code.
4. Route the follow-up through normal autonomy, validation, and production gates.
5. After validation succeeds, close the queue item and publish evidence:

   ```bash
   python3 scripts/autonomy_dashboard.py --workdir "$PWD" \
     --complete "<dashboard_gap_id>" \
     --summary "<what changed>" \
     --evidence "commit:<sha>; tests:<result>; audit:<verdict>"
   ```

`Queued` means a current or future Build Loop run can execute the instruction;
the dashboard does not run a hidden worker. `Applied` means Build Loop validated
the result and removed the item from the executable queue.

## Policy ownership

The dashboard explains and records choices. `scripts/autonomy_supervisor.py`
owns execution policy: missing-information routing, task-shape learning, bounded
queue manifests, discovered-issue classification, convergence enforcement, and
live provider/host/cost backpressure. Queue sizing and fan-out are adaptive;
150 is an absolute ceiling, not a target. The third repeated unresolved verdict
requires independent audit and the fifth quarantines the item.
