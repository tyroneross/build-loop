---
name: agent-rally-point
description: "Use when coordinating build-loop with peer coding agents, checking Rally Point presence/inbox state, posting handoffs or feedback, validating the embedded Rally Point boundary, or changing the future agent-rally-point spin-out surface."
user-invocable: false
---

<!-- SPDX-FileCopyrightText: 2025-2026 Tyrone Ross, Jr <46267523+tyroneross@users.noreply.github.com> | SPDX-License-Identifier: Apache-2.0 -->

# Agent Rally Point

Build-loop ships a native embedded `agent-rally-point` capability while the
standalone repo matures. Treat it as a mini-plugin inside build-loop: keep the
substrate logic, docs, tests, and thin build-loop adapters grouped so spin-out
is mechanical later.

## Native Surface

| Purpose | Path |
|---|---|
| Substrate package | `${CLAUDE_PLUGIN_ROOT}/scripts/rally_point/` |
| Host-neutral CLI | `${CLAUDE_PLUGIN_ROOT}/scripts/agent_rally.py` |
| Slash command | `${CLAUDE_PLUGIN_ROOT}/commands/agent-rally-point.md` |
| Hook adapters | `${CLAUDE_PLUGIN_ROOT}/hooks/session-start-rally-point.sh`, `${CLAUDE_PLUGIN_ROOT}/hooks/pre-edit-rally-point.sh` |
| Build-loop adapters | `${CLAUDE_PLUGIN_ROOT}/scripts/coordination_status.py`, `${CLAUDE_PLUGIN_ROOT}/scripts/coordination_rally.py`, `${CLAUDE_PLUGIN_ROOT}/scripts/coordination_bootstrap.py` |
| Boundary manifest | `${CLAUDE_PLUGIN_ROOT}/scripts/rally_point/plugin_boundary.json` |

## Required Orchestrator Contract

At the Phase 1 preamble, before the first Rally Point write, the orchestrator
must generate or resume the durable run identity:

```python
from scripts.rally_point.build_loop_id import generate_or_resume

execution = generate_or_resume(
    workdir=Path.cwd(),
    tool="<tool-id>",
    session_id="<session-id>",
)
```

`session_id` is the ephemeral host session. `run_id` is caller-chosen legacy
provenance. `build_loop_id` is the durable human-visible run identity and is
surfaced on Rally Point records as top-level `build_loop_id` and
`build_loop_run_label`.

## Operating Rules

- Resolve the channel through `scripts/rally_point/discovery_bridge.py` or
  `scripts/agent_rally.py where`; do not hardcode legacy channel roots.
- Write cross-session events through `scripts/rally_point/post.py::post()`;
  do not append directly to `changes.jsonl`.
- Keep `build_loop_id` separate from `producer_metadata`.
- Keep substrate files independent from build-loop memory/orchestration
  internals. If a build-loop-specific integration is needed, put it in a thin
  adapter named in `plugin_boundary.json`.
- Use stable tool ids: `claude_code`, `codex`, `cursor`, `gemini`, or another
  lowercase id agreed by the adapter.

## Commands

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent_rally.py status --workdir "$PWD" --session-id "$SESSION_ID" --json
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent_rally.py heartbeat --workdir "$PWD" --session-id "$SESSION_ID" --task-ref "$TASK_REF" --progress "still working" --json
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent_rally.py ack-inbox --workdir "$PWD" --session-id "$SESSION_ID" --tool "$TOOL_ID" --json
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent_rally.py boundary --repo "${CLAUDE_PLUGIN_ROOT}" --check --json
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/rally_point/boundary.py --repo "${CLAUDE_PLUGIN_ROOT}" --check --json
```

Use `/agent-rally-point status` for the same sensor from Claude Code.
Status/watch envelopes include `inbox_latest_messages`, a compact doorbell
preview for the newest unread direct/broadcast inbox records. Counts are
session-ack aware; read `inbox/<tool>.jsonl` or `inbox/all.jsonl` before acting
on a full message, then run `ack-inbox` after handling it. Ack cursors live
under `inbox/.acks/` and never rewrite the append-only inbox payloads.

## Task Heartbeat — still on the claimed task

Presence is process liveness: it tells peers this session can still write to
the channel. Task heartbeat is work liveness: it tells peers whether the
session is still on the claimed task, what changed since the last check-in,
what evidence exists, and when the next check-in is due.

For long-running tasks, write a heartbeat at task start and then at least every
10 minutes:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent_rally.py heartbeat \
  --workdir "$PWD" \
  --session-id "$SESSION_ID" \
  --tool "$TOOL_ID" \
  --model "$MODEL" \
  --run-id "$RUN_ID" \
  --task-ref "$TASK_REF" \
  --status running \
  --progress "short update since last check-in" \
  --evidence "files/tests/commit-or-handoff-ids" \
  --json
```

Then pass the same `--task-ref` to `status` or `watch`. Health can be
`current`, `stale_check_in`, `wrong_task`, `missing`, `drift_risk`, `blocked`,
or `needs_attention`. `blocked` and `needs_attention` make status report
`blocked`; stale, missing, wrong-task, and drift-risk states report `warn`.

## Roster — who is running, where, doing what, with how many subagents

`roster` answers the cross-channel "who's live right now" question in one
command. It walks **every** `<apps_root>/*/sessions/*.json` (all repos at
once), keeps sessions whose `last_seen` is within the stale window, and
builds a parent/child tree from each record's `parent` link plus the
self-reported `spawned` fan-out.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent_rally.py roster              # live tree, all channels
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent_rally.py roster --app ptyd   # one channel
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent_rally.py roster --json       # structured (array of agent objects w/ children + spawned)
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent_rally.py roster --stale-secs 300 --all   # widen window, keep stale
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent_rally.py roster --watch 5    # re-render every 5s
```

Each row: `session_id · app · host:cwd · tool/model · task · last-seen(age) ·
subagents (Σtotal by-type + live nested count)`. Children that posted their
own presence nest under the parent; subagents that did not post presence are
reflected by the parent's `spawned` totals.

### Convention — populate the roster on every presence write

So the roster is useful, agents **must** enrich their `presence` calls. These
fields are additive and backward-compatible (existing callers keep working):

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/agent_rally.py presence \
  --session-id "$SESSION_ID" --tool claude_code --model opus \
  --cwd "$PWD" \
  --task "what this agent is actually doing right now" \
  --parent "$SPAWNING_SESSION_ID" \
  --spawned "coder:2,workflow:21,independent-auditor:1"
```

- `--cwd` / `--pid` / `--host` — where it runs (default cwd / this pid / this host).
- `--task` — fuller free text (falls back to `--phase` for display).
- `--parent <session_id>` — links a subagent to its spawning agent; omit for top-level.
- `--spawned <type:count,…>` — the fan-out an agent self-reports.
- Every `presence` call rewrites `last_seen`; re-post periodically so the
  agent stays in the live window (default 120s). Presence is not task
  heartbeat; use `agent_rally.py heartbeat` for long-running task check-ins.

Top-level orchestrators should post `--spawned` reflecting their dispatched
subagents; each dispatched subagent that can post should set `--parent` to the
orchestrator's `session_id`.

## Validation

```bash
uv run pytest scripts/test_build_loop_id.py scripts/rally_point/test_boundary.py scripts/rally_point/test_orchestrator_contract.py scripts/test_agent_rally_roster.py scripts/test_agent_rally_status.py
python3 scripts/agent_rally.py boundary --repo "$PWD" --check --json
```

## Spin-Out Rule

When extracting to the standalone plugin, copy the namespaced substrate,
skill, command, hooks, tests, and docs named in `plugin_boundary.json`.
Build-loop should then keep only the thin adapters that call the standalone
package.
