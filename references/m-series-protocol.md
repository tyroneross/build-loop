<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# M-Series Protocol (M1 envelope persist, M2 heartbeat, M3 cost-ledger)

_Linked from `agents/build-orchestrator.md` §Phase 3 Execute._

M1 and M2 protect resume correctness after orchestrator stream termination. M3 produces an external measurement record for dispatch-pattern analysis. All three writes happen at the same orchestrator step on each subagent dispatch; one helper call each, ≤20ms.

## M1 — Persist subagent envelopes immediately on receipt (crash-recovery)

After each implementer subagent returns, BEFORE making any further routing decision, atomic-write its envelope to `.build-loop/subagent-results/<run-id>/<chunk-id>.attempt-<n>.json` via `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/write_subagent_result.py --workdir "$PWD" --run-id "<run-id>" --envelope -` (envelope JSON via stdin). The `<run-id>` is `state.json.execution.run_id`. The `<n>` is the implementer's attempt count for this chunk in this build (1 for first try, 2 for retries). Failure of this write is a hard error — re-attempt once, then surface to the user; never silently drop the envelope. This step exists so that if the orchestrator's Claude subagent stream terminates mid-Execute (529, OOM, kill -9), the resumed orchestrator can read these files and skip work that already shipped. See `docs/plans/crash-recovery-state-json.md` §M1 for rationale.

## M2 — Heartbeat the chunk pointer to state.json on every dispatch + return (crash-recovery)

The orchestrator owns six trigger points that update `state.json.execution` via `python3 -c "from sys import path; path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts'); from write_run_entry import update_execution_state; from pathlib import Path; update_execution_state(Path('.build-loop/state.json'), '<action>', ...)"` or by importing the helper from a thin orchestrator-side wrapper. The trigger points and their actions:

1. **`run_id` provenance + run start** — at the END of Phase 1 Assess: generate `run_id` as `run_<UTC-timestamp>_<8-char-hash>` where the hash is `sha256(timestamp + intent_md_sha + working_branch)[:8]`; persist it as the FIRST execution-block write via `update_execution_state(state_path, 'start', run_id=..., queued_chunks=[...], file_ownership={...})` populated from the Phase 2 plan output. This must happen BEFORE any chunk dispatch.
2. **Before dispatching each implementer** (Phase 3 Execute): `update_execution_state(state_path, 'dispatch_chunk', chunk_id=<id>)` — moves chunk_id from `queued_chunks` → `in_flight_chunks`.
3. **After receiving each implementer return** (Phase 3 Execute, immediately AFTER the M1 envelope write above): `update_execution_state(state_path, 'return_chunk', chunk_id=<id>, status=<one-of-9-statuses>)` — moves chunk_id from `in_flight_chunks` → `completed_chunks` with status; refreshes `last_heartbeat_at`.
4. **On phase transition** (Execute→Review, Review→Iterate, Iterate→Review, Review→Report): `update_execution_state(state_path, 'phase_transition', phase=<one-of-execute|review|iterate|report>)`.
5. **On Iterate attempt start** (Phase 5 Iterate, BEFORE the cascade fires): `update_execution_state(state_path, 'iterate_attempt')` — increments the counter; this preserves the 5x iteration cap across resume.
6. **On clean completion** (Phase 4 Review-G success): `update_execution_state(state_path, 'complete')` — sets `phase: "report"`. This is the "no resume needed" sentinel; `--resume` refuses to run against a state where `phase == "report"`.

Failure of any heartbeat write is logged but never blocks the build — the in-memory state remains authoritative for the live build, and the worst case is that resume picks up at the last-good heartbeat. See `docs/plans/crash-recovery-state-json.md` §M2 for rationale.

### M2 liveness beat — phase/commit boundary heartbeat + rally presence (bl-orchestrator-heartbeat-rally-presence)

The six trigger points above are chunk-centric (dispatch/return). A long run that sits between chunks, or an inline/background run that never fans out, can go a long time with `last_heartbeat_at` stale and NO rally presence — so a watcher can only reconstruct status from git + CI (the user-flagged defect). On **long or autonomous runs**, the orchestrator additionally beats at **every phase boundary AND every commit** via one fail-open call:

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/orchestrator_heartbeat.py \
  --workdir "$PWD" --phase "<assess|plan|execute|review|iterate|report>" \
  --label "<boundary one-liner>" [--files a.py,b.py] --json
```

This is a THIN wrapper over two existing fail-open mechanisms — it refreshes `state.execution.last_heartbeat_at` (via the `heartbeat` action) AND writes a `presence.write_presence` beat to the resolved rally channel — so any watcher (`coordination_status.py`, `rally room`) reads live status. NO new coordination surface. It never wedges the run (exit 0 always); a missing execution block or unresolvable channel is a clean skip. Call it right after each `phase_transition` write and right after each commit lands.

### M2 sidecar — working-state writes (NEW 2026-05-13, plan §15.2)

At the same M2 trigger points 2 + 3 + 4 + 6, also write `.build-loop/working-state/current.json` + append `.build-loop/working-state/log.jsonl` via:

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/working_state_writer.py \
  --workdir "$PWD" --agent "orchestrator" \
  --run-id "$RUN_ID" --chunk-id "<chunk_id_or_empty>" \
  --status "<dispatching | awaiting_return | phase_transition | completed>" \
  --current-task-summary "<phase/chunk one-liner>"
```

Implementers write their own per-step working-state during the chunk per `agents/implementer.md` §"Working-state writes" — orchestrator writes are bookend events around them. Failure here is fire-and-forget; never blocks. Files are gitignored; do not commit working-state to the repo.

### M2 context snapshot sidecar - resume handoff (NEW 2026-05-28)

At the same M2 trigger points 2 + 3 + 4 + 6, also write a non-blocking context snapshot:

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/context_snapshot.py \
  --workdir "$PWD" \
  --trigger "<agent_dispatch | agent_return | phase_transition>" \
  --phase "<execute | review | iterate | report>" \
  --agent "orchestrator" \
  --run-id "$RUN_ID" \
  --chunk-id "<chunk_id_or_empty>" \
  --status "<dispatching | awaiting_return | phase_transition | completed>" \
  --message "<phase/chunk one-liner>" \
  --file "<owned-or-returned-file>" \
  --json
```

The helper writes `.build-loop/context/current.md`, a JSON snapshot under
`.build-loop/context/snapshots/`, and appends dispatch/return rows to
`agent-briefs.jsonl` or `agent-returns.jsonl`. Use `--if-changed` for interval
or heartbeat calls. Failure is fire-and-forget like working-state; never block
the build because the snapshot is a resume/handoff aid, not the source of truth.

## M3 — Cost-ledger row per subagent dispatch (telemetry, not crash-recovery)

Complements M1 (envelope persist) and M2 (heartbeat). The orchestrator emits one ledger row at dispatch time and one at return time per subagent invocation. Both rows carry the same `task_id` so wall-clock and status can be correlated post-hoc by Round 4 dispatch-pattern analysis (and any later cost study).

Procedure per dispatch:

1. **Generate `TASK_ID`** before the `Agent(...)` call: `TASK_ID="$(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/dispatch_identity.py --plain)"`. Record `started_at` (ISO 8601 UTC).
2. **Prepend `[TASK_ID: <id>]` to the implementer brief** as the first line of the prompt body. The implementer echoes it in `task_id` per `references/implementer-envelope-schema.md`.
3. **Write the dispatch row** (status=`dispatched`):
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/write_cost_ledger_row.py \
     --agent implementer \
     --task-id "$TASK_ID" \
     --model "<resolved-tier-model>" \
     --status dispatched \
     --dispatch-mode "<fan-out|inline|self-recursive>" \
     --started-at "<iso8601>" \
     --run-id "$RUN_ID" \
     --chunk-id "<chunk_id>"
   ```
4. **Dispatch** the subagent.
5. **After return** (at the same point as M1 envelope-persist + M2 `return_chunk` heartbeat), write the return row:
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/write_cost_ledger_row.py \
     --agent implementer \
     --task-id "$TASK_ID" \
     --model "<resolved-tier-model>" \
     --status "<envelope.status>" \
     --dispatch-mode "<same-as-dispatch>" \
     --files-changed-count <N> \
     --wall-clock-seconds <envelope.wall_clock_seconds> \
     --tokens-estimate <envelope.tokens_estimate || omit> \
     --tokens-source envelope \
     --started-at "<dispatch-iso8601>" \
     --completed-at "<return-iso8601>" \
     --run-id "$RUN_ID" \
     --chunk-id "<chunk_id>"
   ```

**Scope**: M3 applies to every `Agent(subagent_type="build-loop:<x>")` call the orchestrator makes — implementer (Phase 3), scope-auditor (Phase 2/3), commit-auditor (Phase 3 step 7 chunk scope + Phase 4-A build scope; replaces retired sonnet-critic), synthesis-critic (Phase 3 step 6), fact-checker (Phase 4-D), architecture-scout (Phase 1 + chunk-impact), optimize-runner (Phase 4-C), overfitting-reviewer (Phase 4-C). Set `--agent` to the subagent's frontmatter name; set `--dispatch-mode` from the active dispatch context.

**Failure mode**: helper exit-0 is success; exit-1 is a validation error (fix the args and retry once); exit-2 is a filesystem error (log once, do NOT block the build — telemetry is best-effort). Never let a ledger-write failure halt a Phase 3 commit.

**Why this is independent of M1/M2**: M1 and M2 protect resume correctness. M3 produces an external measurement record so dispatch-pattern claims like "Mode A burns 4× tokens" can be evidenced rather than estimated. The three writes are sequential at the same orchestrator step; one helper call each, ≤20ms.
